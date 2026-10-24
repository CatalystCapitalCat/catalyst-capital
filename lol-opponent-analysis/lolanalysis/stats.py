"""第二遍：只读 CSV，算出所有汇总表，存成 summary.json，并打印几十行的汇总。

约定：
  - 每张表带 src（接口 + 场次范围）、n（样本场数）、small（n < 10 → "样本太少"）。
  - 由位置/坐标推出来的结论都带 inferred=True，并在 basis 里写依据。
  - 颜色（rating）：green 比基准好、yellow 差不多、red 差、grey 无对比基准。
    基准 = 同一批对局里"对面同位置玩家"的平均值（同一场对局，MMR 接近），不是官方的段位平均。
"""

import csv
import datetime as dt
import json
import math
import os
from collections import Counter, defaultdict

from . import geometry as G

SMALL_N = 10
REL_BAND = 0.10        # 相对基准 ±10% 以内算"差不多"
CS_PER_CAMP = 4.0      # 估算"刷了几组"用的假设：每组野怪约 4 个补刀（见数据来源页）
DIFF_BANDS = {         # 对位差值的"差不多"区间（绝对值）
    "gold_diff_10": 300, "gold_diff_15": 500, "xp_diff_10": 200, "xp_diff_15": 300,
    "cs_diff_10": 5, "cs_diff_15": 8,
}


# ---------------------------------------------------------------- 读 CSV
STR_COLS = {
    "match_id", "exclude_reason", "game_start_utc", "game_end_utc", "game_version", "patch", "champ", "champ_zh",
    "side", "position", "position_zh", "skill_order_1_3", "opp_puuid", "opp_name", "opp_champ", "opp_champ_zh",
    "first_item_name", "puuid", "riot_id", "zone", "assist_pids", "killer_champ", "victim_champ", "monster_type",
    "sub_type", "building_type", "tower_type", "lane_type", "who", "name_zh",
    "version", "slot", "spell_name", "cooldown", "cost", "cost_type", "range", "description", "stats", "plaintext",
}


def _num(v, col=None):
    if col in STR_COLS:
        return v or ""
    if v is None or v == "":
        return None
    try:
        f = float(v)
        return int(f) if f.is_integer() and "." not in v else f
    except ValueError:
        return v


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return [{k: _num(v, k) for k, v in row.items()} for row in csv.DictReader(fh)]


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2.0


def pct(a, b):
    return a / b if b else None


def mmss(minutes):
    if minutes is None:
        return "—"
    s = int(round(minutes * 60))
    return "%d:%02d" % (s // 60, s % 60)


def rate(his, base, higher_better=True, band=REL_BAND, absolute=False, n_base=None):
    """返回 green/yellow/red/grey。absolute=True 时 band 是绝对值（用于对位差值，基准为 0）。"""
    if his is None or (base is None and not absolute) or (n_base is not None and n_base < 3):
        return "grey"
    if absolute:
        d = his - (base or 0)
        if abs(d) <= band:
            return "yellow"
        better = d > 0 if higher_better else d < 0
        return "green" if better else "red"
    if base == 0:
        return "grey"
    d = (his - base) / abs(base)
    if abs(d) <= band:
        return "yellow"
    better = d > 0 if higher_better else d < 0
    return "green" if better else "red"


# ---------------------------------------------------------------- 主函数
def compute(out_dir, ds_meta, log=print):
    C = os.path.join(out_dir, "csv")
    games_all = read_csv(os.path.join(C, "games.csv"))
    parts = read_csv(os.path.join(C, "participants.csv"))
    frames = read_csv(os.path.join(C, "frames.csv"))
    kills = read_csv(os.path.join(C, "kills.csv"))
    objs = read_csv(os.path.join(C, "objectives.csv"))
    items = read_csv(os.path.join(C, "items.csv"))
    with open(os.path.join(out_dir, "parse_log.json"), encoding="utf-8") as fh:
        plog = json.load(fh)

    games = [g for g in games_all if g["included"] == 1]
    games.sort(key=lambda g: g["game_start_utc"] or "")
    gid = {g["match_id"]: g for g in games}
    tl_games = [g for g in games if g["has_timeline"] == 1]

    S = {"meta": _meta(ds_meta, games_all, games, tl_games, plog)}
    src = Src(S["meta"])
    S["rank"] = ds_meta.get("rank")
    S["games_list"] = [_game_brief(g) for g in sorted(games, key=lambda g: g["game_start_utc"], reverse=True)]

    pos_count = Counter(g["position"] for g in games)
    S["positions"] = [{"position": p, "games": n, "wins": sum(g["win"] for g in games if g["position"] == p),
                       "share": n / len(games)} for p, n in pos_count.most_common()]
    main_pos = pos_count.most_common(1)[0][0] if games else ""
    S["main_position"] = main_pos
    main_games = [g for g in games if g["position"] == main_pos]

    S["overall"] = _overall(games, main_games, src)
    S["baseline"] = _baseline(main_games, parts, src)
    S["champ_pool"] = _champ_pool(games, src)
    S["duration"] = _duration(games, src)
    S["tilt"] = _tilt(games, src)
    S["duo"] = _duo(games, parts, src)
    S["lane"] = _lane(games, tl_games, kills, src)
    jg = [g for g in tl_games if g["position"] == "JUNGLE"]
    S["jungle"] = _jungle(jg, frames, kills, objs, src) if jg else None
    S["deaths"] = _deaths(tl_games, kills, src)
    S["weakness"] = _weakness(games, tl_games, kills, frames, parts, src)
    S["ban"] = _ban(S["champ_pool"], src)
    S["lobby_ranks"] = _lobby_ranks(ds_meta, main_games, src)
    S["ref"] = _ref(C, S["champ_pool"], ds_meta.get("my_pool") or [])
    S["checks"] = {"challenges_mismatch": plog.get("challenges_mismatch", []),
                   "geometry_selftest_failures": [list(map(str, b)) for b in G.self_test()]}
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(S, fh, ensure_ascii=False, indent=1)
    print_summary(S, log)
    return S


class Src:
    """生成"来源"说明：接口 + 场次范围 + 下载日期 + 版本。"""

    def __init__(self, meta):
        self.meta = meta

    def __call__(self, games, what="match-v5 对局详情"):
        ids = sorted(g["match_id"] for g in games)
        patches = sorted(set(g["patch"] for g in games if g.get("patch")))
        if not ids:
            rng = "无"
        elif len(ids) <= 3:
            rng = "、".join(ids)
        else:
            rng = "%s … %s 共 %d 场" % (ids[0], ids[-1], len(ids))
        return "%s；%s；下载于 %s；版本 %s" % (what, rng, self.meta.get("downloaded_date", "?"),
                                          "/".join(patches) or "?")


def _meta(ds_meta, games_all, games, tl_games, plog):
    excluded = [{"match_id": g["match_id"], "reason": g["exclude_reason"]} for g in games_all if g["included"] != 1]
    for mid, s in plog["matches"].items():
        if not s.get("included") and mid not in {e["match_id"] for e in excluded}:
            excluded.append({"match_id": mid, "reason": s.get("reason")})
    dates = sorted(g["game_start_utc"] for g in games if g["game_start_utc"])
    m = dict(ds_meta)
    m.pop("rank", None)
    m.pop("lane_opp_ranks", None)
    m.update({
        "n_ids": len(plog["matches"]), "n_included": len(games), "n_timeline": len(tl_games),
        "excluded": excluded, "first_game_utc": dates[0] if dates else None,
        "last_game_utc": dates[-1] if dates else None,
        "patches": sorted(set(g["patch"] for g in games if g["patch"])),
        "item_versions": sorted(set(v for v in plog.get("item_versions", {}).values() if v)),
    })
    return m


def _game_brief(g):
    return {k: g.get(k) for k in ("match_id", "game_start_utc", "patch", "champ", "champ_zh", "position", "side", "win",
                                  "duration_min", "kills", "deaths", "assists", "opp_champ_zh", "gold_diff_15",
                                  "first_item_name", "first_item_time_s", "has_timeline")}


def _agg(gs):
    n = len(gs)
    if not n:
        return None
    K = sum(g["kills"] for g in gs)
    D = sum(g["deaths"] for g in gs)
    A = sum(g["assists"] for g in gs)
    mins = sum(g["duration_min"] for g in gs)
    return {
        "games": n, "wins": sum(g["win"] for g in gs), "wr": sum(g["win"] for g in gs) / n,
        "avg_k": K / n, "avg_d": D / n, "avg_a": A / n, "kda": (K + A) / max(1, D),
        "cs_per_min": sum(g["cs_total"] for g in gs) / mins if mins else None,
        "dmg_share": mean(g["dmg_share"] for g in gs), "kp": mean(g["kp"] for g in gs),
        "vision": mean(g["vision"] for g in gs), "vision_per_min": mean(g["vision_per_min"] for g in gs),
        "duration_min": mean(g["duration_min"] for g in gs), "small": n < SMALL_N,
    }


def _opp_agg(gs):
    gs = [g for g in gs if g.get("opp_pid") is not None]
    n = len(gs)
    if not n:
        return None
    K = sum(g["opp_kills"] for g in gs)
    D = sum(g["opp_deaths"] for g in gs)
    A = sum(g["opp_assists"] for g in gs)
    return {"games": n, "kda": (K + A) / max(1, D), "avg_d": D / n,
            "cs_per_min": (sum(g["opp_cs_per_min"] * g["duration_min"] for g in gs) / sum(g["duration_min"] for g in gs)
                           if sum(g["duration_min"] for g in gs) else None),
            "dmg_share": mean(g["opp_dmg_share"] for g in gs),
            "kp": mean(g["opp_kp"] for g in gs), "vision_per_min": mean(g["opp_vision_per_min"] for g in gs)}


def _overall(games, main_games, src):
    return {"all": _agg(games), "main": _agg(main_games), "src": src(games)}


METRICS = [  # (key, 中文, 越大越好?)
    ("kda", "KDA", True), ("avg_d", "场均死亡", False), ("cs_per_min", "每分钟补刀", True),
    ("dmg_share", "伤害占比", True), ("kp", "参团率", True), ("vision_per_min", "每分钟视野得分", True),
]


def _baseline(main_games, parts, src):
    his, opp = _agg(main_games), _opp_agg(main_games)
    rows = []
    if his and opp:
        for key, zh, hb in METRICS:
            rows.append({"metric": key, "label": zh, "his": his[key], "base": opp[key], "n_base": opp["games"],
                         "rating": rate(his[key], opp[key], hb, n_base=opp["games"]), "higher_better": hb})
    # 首件成装时间（越早越好）、10/15 分钟对位差（基准为 0）
    fi = _first_item_pair(main_games)
    if fi["n_pair"]:
        rows.append({"metric": "first_item_min", "label": "首件成装时间（分钟，中位数，只比两人都做出成装的场次）",
                     "his": fi["his_median"], "base": fi["opp_median"], "n_base": fi["n_pair"],
                     "rating": rate(fi["his_median"], fi["opp_median"], False, n_base=fi["n_pair"]),
                     "higher_better": False, "note": fi["note"]})
    for key, zh in (("gold_diff_10", "10 分钟经济差"), ("gold_diff_15", "15 分钟经济差"), ("xp_diff_10", "10 分钟经验差"),
                    ("cs_diff_10", "10 分钟补刀差"), ("cs_diff_15", "15 分钟补刀差")):
        xs = [g[key] for g in main_games if g.get(key) is not None]
        if xs:
            rows.append({"metric": key, "label": zh + "（对位）", "his": mean(xs), "base": 0, "n_base": len(xs),
                         "rating": rate(mean(xs), 0, True, band=DIFF_BANDS[key], absolute=True, n_base=len(xs)),
                         "higher_better": True, "abs_band": DIFF_BANDS[key]})
    return {"rows": rows, "n": len(main_games), "small": len(main_games) < SMALL_N,
            "rule": "基准 = 同一批对局中对面同位置玩家的平均值；相对差在 ±%d%% 以内为黄，"
                    "更好为绿，更差为红；对位差值按绝对区间（如 10 分钟经济差 ±%d 金）判断；样本 < 3 为灰"
                    % (int(REL_BAND * 100), DIFF_BANDS["gold_diff_10"]),
            "src": src(main_games)}


def _first_item_pair(gs):
    """首件成装：只比两人都做出成装的场次（中位数，分钟）；另外报告各自没做出成装的场数。"""
    pair = [g for g in gs if g.get("first_item_time_s") is not None and g.get("opp_first_item_time_s") is not None]
    his_none = sum(1 for g in gs if g.get("has_timeline") == 1 and g.get("first_item_time_s") is None)
    opp_none = sum(1 for g in gs if g.get("has_timeline") == 1 and g.get("opp_pid") is not None
                   and g.get("opp_first_item_time_s") is None)
    return {"n_pair": len(pair), "his_median": median(g["first_item_time_s"] / 60.0 for g in pair),
            "opp_median": median(g["opp_first_item_time_s"] / 60.0 for g in pair),
            "note": "两人都做出成装的 %d 场；他没做出成装 %d 场，对位没做出 %d 场" % (len(pair), his_none, opp_none)}


def _champ_pool(games, src):
    by = defaultdict(list)
    for g in games:
        by[g["champ"]].append(g)
    rows = []
    for champ, gs in by.items():
        a = _agg(gs)
        o = _opp_agg(gs)
        a.update({"champ": champ, "champ_zh": gs[0]["champ_zh"], "positions": dict(Counter(g["position"] for g in gs)),
                  "match_ids": [g["match_id"] for g in gs], "losses": a["games"] - a["wins"],
                  "net_wins": 2 * a["wins"] - a["games"], "smoothed_wr": (a["wins"] + 5) / (a["games"] + 10.0)})
        a["ratings"] = {}
        for key, _, hb in METRICS:
            a["ratings"][key] = rate(a[key], o[key] if o else None, hb, n_base=o["games"] if o else 0)
        a["opp_base"] = o
        rows.append(a)
    rows.sort(key=lambda r: (-r["games"], -r["wr"]))
    return {"rows": rows, "src": src(games), "n": len(games)}


def _duration(games, src):
    buckets = [("20 分钟以内", 0, 20), ("20–30 分钟", 20, 30), ("30 分钟以上", 30, 999)]
    rows = []
    for name, lo, hi in buckets:
        gs = [g for g in games if lo <= g["duration_s"] / 60.0 < hi]
        rows.append({"bucket": name, "games": len(gs), "wins": sum(g["win"] for g in gs),
                     "wr": pct(sum(g["win"] for g in gs), len(gs)), "small": len(gs) < SMALL_N,
                     "match_ids": [g["match_id"] for g in gs]})
    wins = [g["duration_min"] for g in games if g["win"]]
    losses = [g["duration_min"] for g in games if not g["win"]]
    early = [g for g in games if g["duration_s"] < 1800]
    late = [g for g in games if g["duration_s"] >= 1800]
    wr_e, wr_l = pct(sum(g["win"] for g in early), len(early)), pct(sum(g["win"] for g in late), len(late))
    verdict = "看不出明显倾向"
    if wr_e is not None and wr_l is not None:
        if wr_e - wr_l >= 0.15:
            verdict = "偏前期：30 分钟内结束的对局胜率明显更高"
        elif wr_l - wr_e >= 0.15:
            verdict = "偏后期：拖到 30 分钟以上的对局胜率明显更高"
    return {"rows": rows, "median_win_min": median(wins), "median_loss_min": median(losses),
            "wr_under30": wr_e, "n_under30": len(early), "wr_over30": wr_l, "n_over30": len(late),
            "verdict": verdict, "small": min(len(early), len(late)) < SMALL_N,
            "rule": "按 gameDuration 分段；<30 与 ≥30 分钟胜率相差 ≥15 个百分点才下结论",
            "src": src(games)}


def _parse_iso(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ") if s else None


def _tilt(games, src):
    gs = sorted(games, key=lambda g: g["game_start_utc"])
    pairs = []
    for a, b in zip(gs, gs[1:]):
        end_a = _parse_iso(a["game_end_utc"]) or (_parse_iso(a["game_start_utc"]) + dt.timedelta(minutes=a["duration_min"]))
        gap = (_parse_iso(b["game_start_utc"]) - end_a).total_seconds() / 60.0
        pairs.append({"prev": a["match_id"], "next": b["match_id"], "prev_win": a["win"], "next_win": b["win"],
                      "next_deaths": b["deaths"], "gap_min": gap})

    def block(ps, label):
        out = {}
        for key, cond in (("after_loss", 0), ("after_win", 1)):
            sel = [p for p in ps if p["prev_win"] == cond]
            out[key] = {"n": len(sel), "wr": pct(sum(p["next_win"] for p in sel), len(sel)),
                        "avg_deaths": mean(p["next_deaths"] for p in sel), "small": len(sel) < SMALL_N}
        out["label"] = label
        return out

    allp = block(pairs, "所有相邻两场")
    sess = block([p for p in pairs if p["gap_min"] <= 60], "间隔 ≤ 60 分钟（同一次连续排位）")
    # 连败后的表现
    streak = []
    for i in range(2, len(gs)):
        if gs[i - 1]["win"] == 0 and gs[i - 2]["win"] == 0:
            streak.append(gs[i])
    al, aw = allp["after_loss"], allp["after_win"]
    verdict = "看不出上头迹象"
    if al["wr"] is not None and aw["wr"] is not None:
        worse_wr = aw["wr"] - al["wr"] >= 0.15
        more_d = (al["avg_deaths"] or 0) - (aw["avg_deaths"] or 0) >= 1.0
        if worse_wr and more_d:
            verdict = "有上头迹象：输了之后下一场胜率更低、死亡更多"
        elif worse_wr or more_d:
            verdict = "有一点上头迹象（只有胜率或死亡一项变差）"
    return {"all": allp, "session": sess, "pairs": pairs,
            "after_2_losses": {"n": len(streak), "wr": pct(sum(g["win"] for g in streak), len(streak)),
                               "avg_deaths": mean(g["deaths"] for g in streak)},
            "overall_avg_deaths": mean(g["deaths"] for g in gs), "verdict": verdict,
            "small": min(al["n"], aw["n"]) < SMALL_N,
            "rule": "按开局时间排序，看每场的下一场（只含数据里的单双排，中间打的其它模式看不到）；"
                    "输后胜率比赢后低 ≥15 个百分点且场均死亡多 ≥1 次，判为容易上头",
            "src": src(games)}


def _duo(games, parts, src):
    inc = {g["match_id"]: g for g in games}
    mates = defaultdict(list)
    for p in parts:
        if p["match_id"] in inc and p["same_team"] == 1 and p["is_him"] == 0:
            mates[p["puuid"]].append(p)
    rows = []
    for puuid, ps in mates.items():
        if len(ps) >= 3:
            mids = [p["match_id"] for p in ps]
            wins = sum(inc[m]["win"] for m in mids)
            latest = max(ps, key=lambda p: inc[p["match_id"]]["game_start_utc"])
            others = [g for g in games if g["match_id"] not in mids]
            rows.append({"riot_id": latest["riot_id"], "puuid": puuid, "games": len(ps), "wins": wins,
                         "wr": wins / len(ps), "positions": dict(Counter(p["position"] for p in ps)),
                         "champs": dict(Counter(p["champ_zh"] for p in ps)), "match_ids": mids,
                         "wr_without": pct(sum(g["win"] for g in others), len(others)),
                         "n_without": len(others), "small": len(ps) < SMALL_N})
    rows.sort(key=lambda r: -r["games"])
    return {"rows": rows, "rule": "他的队友中，在纳入统计的对局里出现 ≥3 次的玩家", "src": src(games)}


def _lane(games, tl_games, kills, src):
    out = {"by_position": {}, "src": src(tl_games, "match-v5 时间线（每分钟一帧）")}
    for pos in sorted(set(g["position"] for g in tl_games)):
        gs = [g for g in tl_games if g["position"] == pos]
        row = {"n": len(gs), "small": len(gs) < SMALL_N}
        for key in ("gold_diff_10", "xp_diff_10", "cs_diff_10", "gold_diff_15", "xp_diff_15", "cs_diff_15",
                    "team_gold_diff_10", "team_gold_diff_15"):
            xs = [g[key] for g in gs if g.get(key) is not None]
            row[key] = {"mean": mean(xs), "median": median(xs), "n": len(xs),
                        "ahead_share": pct(sum(1 for x in xs if x > 0), len(xs)),
                        "rating": rate(mean(xs), 0, True, band=DIFF_BANDS.get(key, 1000), absolute=True,
                                       n_base=len(xs)) if xs else "grey"}
        fp = _first_item_pair(gs)
        row["first_item"] = {"median": fp["his_median"], "opp_median": fp["opp_median"], "n": fp["n_pair"],
                             "note": fp["note"],
                             "rating": rate(fp["his_median"], fp["opp_median"], False, n_base=fp["n_pair"]),
                             "items": dict(Counter(g["first_item_name"] for g in gs if g.get("first_item_name")))}
        out["by_position"][pos] = row
    # 前 10 分钟的击杀/死亡/助攻
    tl_ids = {g["match_id"]: g for g in tl_games}
    ev = []
    for k in kills:
        if k["match_id"] in tl_ids and k["him_role"] and k["t_min"] < 10:
            g = tl_ids[k["match_id"]]
            zone_rel = G.rel_zone(k["zone"], g["team_id"]) if k["zone"] else "未知"
            ev.append({"match_id": k["match_id"], "champ_zh": g["champ_zh"], "position": g["position"],
                       "t": mmss(k["t_min"]), "t_min": k["t_min"],
                       "type": {"K": "击杀", "A": "助攻", "V": "死亡"}[k["him_role"]],
                       "zone": zone_rel, "n_attackers": k["n_attackers"], "executed": k["executed"],
                       "ganked": int(k["him_role"] == "V" and (k["n_attackers"] or 0) >= 2),
                       "killer": k["killer_champ"], "victim": k["victim_champ"], "x": k["x"], "y": k["y"]})
    ev.sort(key=lambda e: (e["match_id"], e["t_min"]))
    deaths = [e for e in ev if e["type"] == "死亡"]
    out["early_events"] = ev
    out["early_summary"] = {
        "games": len(tl_games),
        "kills": sum(1 for e in ev if e["type"] == "击杀"), "assists": sum(1 for e in ev if e["type"] == "助攻"),
        "deaths": len(deaths), "ganked_deaths": sum(e["ganked"] for e in deaths),
        "solo_deaths": sum(1 for e in deaths if e["n_attackers"] == 1),
        "executed": sum(1 for e in deaths if e["executed"]),
        "deaths_per_game": pct(len(deaths), len(tl_games)),
        "death_zones": Counter(e["zone"] for e in deaths).most_common(),
        "rule": "被抓 = 击杀他的敌方英雄（击杀者 + 助攻）≥ 2 人；处决 = 被防御塔/小兵/野怪击杀（killerId=0）",
    }
    return out


# ---------------------------------------------------------------- 打野
def _jungle(jg, frames, kills, objs, src):
    fr_by = defaultdict(dict)
    opp_fr = defaultdict(dict)
    for f in frames:
        if f["is_him"] == 1:
            fr_by[f["match_id"]][f["minute"]] = f
        elif f["is_opp"] == 1:
            opp_fr[f["match_id"]][f["minute"]] = f
    k_by = defaultdict(list)
    for k in kills:
        k_by[k["match_id"]].append(k)
    o_by = defaultdict(list)
    for o in objs:
        o_by[o["match_id"]].append(o)

    openings, ganks_all, invade_rows = [], [], []
    for g in jg:
        mid, team = g["match_id"], g["team_id"]
        fr = fr_by.get(mid, {})
        openings.append(_opening(g, fr))
        eps = _gank_episodes(g, fr, k_by.get(mid, []))
        ganks_all.append({"match_id": mid, "champ_zh": g["champ_zh"], "side": g["side"], "win": g["win"],
                          "episodes": eps})
        invade_rows.append(_invade(g, fr, opp_fr.get(mid, {}), k_by.get(mid, [])))

    # 开野方式统计
    pat = Counter(o["pattern"] for o in openings)
    start = Counter(o["start_label"] for o in openings)
    n = len(openings)
    patterns = [{"pattern": p, "games": c, "share": c / n,
                 "wins": sum(o["win"] for o in openings if o["pattern"] == p),
                 "match_ids": [o["match_id"] for o in openings if o["pattern"] == p]} for p, c in pat.most_common()]
    starts = [{"start": s, "games": c, "share": c / n,
               "by_side": dict(Counter(o["side"] for o in openings if o["start_label"] == s)),
               "median_first_gank_min": median(_first_gank_min(ganks_all, [o["match_id"] for o in openings
                                                                         if o["start_label"] == s])),
               "first_gank_lanes": dict(Counter(_first_gank_lane(ganks_all, o["match_id"]) for o in openings
                                                if o["start_label"] == s))} for s, c in start.most_common()]
    # 第一次抓人
    first = []
    for gk in ganks_all:
        eps = [e for e in gk["episodes"] if e["lane"] in ("TOP", "MID", "BOT")]
        e = eps[0] if eps else None
        first.append({"match_id": gk["match_id"], "champ_zh": gk["champ_zh"], "side": gk["side"],
                      "t": e["t_label"] if e else "—", "t_min": e["t_min"] if e else None,
                      "lane": G.ZONE_NAMES.get(e["lane"], e["lane"]) if e else "前 14 分钟没去过线上",
                      "lane_code": e["lane"] if e else None, "result": e["result"] if e else "—",
                      "basis": e["basis"] if e else "", "inferred": e["inferred"] if e else True})
    fg_lane = Counter(f["lane"] for f in first)
    fg_ok = [f for f in first if f["t_min"] is not None]
    # 前 10 分钟各路去的次数
    pre10 = Counter()
    pre10_success = Counter()
    pre10_direct = Counter()
    for gk in ganks_all:
        for e in gk["episodes"]:
            if e["t_min"] < 10 and e["lane"] in ("TOP", "MID", "BOT"):
                pre10[e["lane"]] += 1
                if e["result"] == "抓到了（有击杀/助攻）":
                    pre10_success[e["lane"]] += 1
                if not e["inferred"]:
                    pre10_direct[e["lane"]] += 1
    lanes = []
    for L in ("TOP", "MID", "BOT"):
        top_visits = []
        for gk in ganks_all:
            ts = [e["t_min"] for e in gk["episodes"] if e["lane"] == L and e["t_min"] < 10]
            top_visits.append(min(ts) if ts else None)
        lanes.append({"lane": L, "lane_zh": G.ZONE_NAMES[L], "visits_pre10": pre10[L],
                      "per_game": pre10[L] / n, "success": pre10_success[L], "direct_events": pre10_direct[L],
                      "games_visited": sum(1 for t in top_visits if t is not None),
                      "first_visit_median": median(top_visits), "first_visit_min": min([t for t in top_visits if t is not None], default=None),
                      "first_visit_times": [mmss(t) if t is not None else "—" for t in top_visits]})

    objectives = _objectives(jg, o_by, src)
    half = _half_pref(jg, fr_by, first, objectives)
    return {
        "n": n, "small": n < SMALL_N, "src": src(jg, "match-v5 时间线（每分钟一帧位置 + 事件）"),
        "openings": openings, "patterns": patterns, "starts": starts,
        "first_gank": first, "first_gank_lanes": fg_lane.most_common(),
        "first_gank_median": median(f["t_min"] for f in fg_ok),
        "first_gank_success": pct(sum(1 for f in fg_ok if f["result"] == "抓到了（有击杀/助攻）"), len(fg_ok)),
        "lanes_pre10": lanes, "ganks": ganks_all, "invade": _invade_summary(invade_rows),
        "objectives": objectives, "half_preference": half,
        "cs_per_camp_assumption": CS_PER_CAMP,
        "basis": ("开野：第 1 分钟（1:00）位置所在半区 = 开局方向（1:00 在基地/河道时改用 2:00）；"
                  "最近营地距离 < %.0f 码时标注营地名；第 3 分钟位置 = 去向；补刀按每组约 %.0f 个估算组数。"
                  "抓人：时间线击杀事件（他是击杀/助攻/阵亡者）在线上或河道口 = 直接数据；"
                  "第 2–14 分钟整分钟位置在线上或河道口 = 推测（每分钟只记一次位置）。"
                  % (G.CAMP_RADIUS * 1.6 * (G.X_MAX - G.X_MIN), CS_PER_CAMP)),
    }


def _opening(g, fr):
    team = g["team_id"]

    def info(m):
        f = fr.get(m)
        if not f or f["x"] is None:
            return None
        rz = G.rel_zone(f["zone"], team)
        camp = G.nearest_camp(f["x"], f["y"])
        camp_lbl = ""
        if camp:
            ct, cname, _ = camp
            camp_lbl = ("己方" if ct == team else ("对方" if ct else "")) + cname
        return {"minute": m, "x": f["x"], "y": f["y"], "zone": f["zone"], "zone_rel": rz, "camp": camp_lbl,
                "cs_jungle": f["cs_jungle"], "cs_lane": f["cs_lane"]}

    p = {m: info(m) for m in (1, 2, 3, 4)}
    basis = []
    start_half, start_min = None, None
    for m in (1, 2):
        q = p.get(m)
        if q and q["zone"].startswith("JG_"):
            start_half = q["zone"]
            start_min = m
            break
    if start_half:
        _, side, half = start_half.split("_")
        own = (side == "BLUE") == (team == 100)
        buff = G.jungle_half_buff(team if own else (300 - team), half)
        if own:
            start_label = "%s开（%s侧）" % ("上半区" if half == "TOP" else "下半区", buff)
        else:
            start_label = "一级入侵对方%s（%s侧）" % ("上半区" if half == "TOP" else "下半区", buff)
        basis.append("%d:00 在%s%s" % (start_min, p[start_min]["zone_rel"],
                                       "（%s 附近）" % p[start_min]["camp"] if p[start_min]["camp"] else ""))
    else:
        start_label = "无法判断"
        basis.append("1:00、2:00 都不在野区（%s / %s）" % (p[1]["zone_rel"] if p.get(1) else "无数据",
                                                     p[2]["zone_rel"] if p.get(2) else "无数据"))
    q3 = p.get(3)
    if q3:
        z = q3["zone"]
        if z in ("TOP", "MID", "BOT") or (G.gank_lane(q3["x"], q3["y"]) is not None):
            where3 = "去%s附近" % G.ZONE_NAMES[G.gank_lane(q3["x"], q3["y"]) or z]
        elif z.startswith("JG_"):
            rz = q3["zone_rel"]
            if start_half and z == start_half:
                where3 = "还在开局半区"
            elif rz.startswith("己方"):
                where3 = "到了另一半野区"
            else:
                where3 = "在对方野区"
        elif z.startswith("BASE_"):
            where3 = "回城"
        elif z in ("RIVER_TOP", "RIVER_BOT", "BARON_PIT", "DRAGON_PIT"):
            where3 = "在%s" % G.ZONE_NAMES[z]
        else:
            where3 = "其它"
        basis.append("3:00 在%s%s" % (q3["zone_rel"], "（%s 附近）" % q3["camp"] if q3["camp"] else ""))
    else:
        where3 = "无 3:00 数据"
    cs = {m: (p[m]["cs_jungle"] if p.get(m) else None) for m in (2, 3, 4)}
    camps3 = round(cs[3] / CS_PER_CAMP, 1) if cs.get(3) is not None else None
    basis.append("打野补刀 2:00/3:00/4:00 = %s/%s/%s" % (cs[2], cs[3], cs[4]))
    return {"match_id": g["match_id"], "champ_zh": g["champ_zh"], "side": g["side"], "win": g["win"],
            "start_label": start_label, "where_3": where3, "pattern": "%s → 3:00 %s" % (start_label, where3),
            "points": [p[m] for m in (1, 2, 3, 4) if p.get(m)], "cs": cs, "camps_by_3_est": camps3,
            "basis": "；".join(basis), "inferred": True}


def _gank_episodes(g, fr, ks):
    """他每次去线上的经历。
    直接数据：击杀事件（他是击杀/助攻/阵亡者）发生在线上或河道口；同一路 60 秒内的多个事件算一次。
    推测：第 2–13 分钟整分钟位置在线上/河道口。同一路连续几分钟算一次"去线上"，
    这段时间前后 1 分钟内有同一路的击杀事件就并到那次事件里，没有才记为"没有击杀"。"""
    team = g["team_id"]
    evs = []
    for k in ks:
        if not k["him_role"] or k["t_min"] >= 14 or k["x"] is None:
            continue
        lane = G.gank_lane(k["x"], k["y"])
        if lane is None:
            continue
        if k["him_role"] in ("K", "A") and k["victim_team"] != team:
            result = "抓到了（有击杀/助攻）"
        elif k["him_role"] == "V":
            result = "他阵亡"
        else:
            continue
        evs.append({"t_min": k["t_min"], "t_label": mmss(k["t_min"]), "lane": lane, "result": result,
                    "inferred": False, "basis": "击杀事件 %s，坐标(%d,%d)，%s" % (mmss(k["t_min"]), k["x"], k["y"],
                                                                         G.ZONE_NAMES.get(k["zone"], k["zone"]))})
    evs.sort(key=lambda e: e["t_min"])
    merged = []
    for e in evs:
        last = merged[-1] if merged else None
        if last and last["lane"] == e["lane"] and e["t_min"] - last["_end"] <= 1.0:
            last["_end"] = e["t_min"]
            if e["result"].startswith("抓到"):
                last["result"] = e["result"]  # 只要有一次击杀/助攻就算抓到了
            continue
        e["_end"] = e["t_min"]
        merged.append(e)
    # 整分钟位置：同一路连续的分钟合成一段
    runs = []
    for m in range(2, 14):
        f = fr.get(m)
        lane = G.gank_lane(f["x"], f["y"]) if f and f["x"] is not None else None
        if lane and runs and runs[-1]["lane"] == lane and runs[-1]["m_b"] == m - 1:
            runs[-1]["m_b"] = m
        elif lane:
            runs.append({"lane": lane, "m_a": m, "m_b": m, "f": f})
    for r in runs:
        near = [e for e in merged if e["lane"] == r["lane"] and r["m_a"] - 1.0 <= e["t_min"] <= r["m_b"] + 1.0]
        if near:
            continue
        f = r["f"]
        span = ("%d:00" % r["m_a"]) if r["m_a"] == r["m_b"] else ("%d:00–%d:00" % (r["m_a"], r["m_b"]))
        merged.append({"t_min": r["m_a"] - 0.5, "t_label": "约 %d:30–%d:00" % (r["m_a"] - 1, r["m_a"]), "lane": r["lane"],
                       "result": "没有击杀（没抓成或只是路过/拿河蟹）", "inferred": True,
                       "basis": "%s 的整分钟位置在%s，例如 %d:00 位置(%d,%d)" % (
                           span, G.ZONE_NAMES[r["lane"]], r["m_a"], f["x"], f["y"])})
    for e in merged:
        e.pop("_end", None)
    merged.sort(key=lambda e: e["t_min"])
    return merged


def _first_gank_min(ganks_all, mids):
    out = []
    for gk in ganks_all:
        if gk["match_id"] in mids:
            ts = [e["t_min"] for e in gk["episodes"] if e["lane"] in ("TOP", "MID", "BOT")]
            out.append(min(ts) if ts else None)
    return out


def _first_gank_lane(ganks_all, mid):
    for gk in ganks_all:
        if gk["match_id"] == mid:
            eps = [e for e in gk["episodes"] if e["lane"] in ("TOP", "MID", "BOT")]
            return G.ZONE_NAMES[eps[0]["lane"]] if eps else "无"
    return "无"


def _invade(g, fr, ofr, ks):
    team = g["team_id"]
    mins = [m for m in range(2, 11) if fr.get(m) and fr[m]["zone"].startswith("JG_")
            and G.rel_zone(fr[m]["zone"], team).startswith("对方")]
    opp_mins = [m for m in range(2, 11) if ofr.get(m) and ofr[m]["zone"].startswith("JG_")
                and G.rel_zone(ofr[m]["zone"], 300 - team).startswith("对方")]
    ev = [k for k in ks if k["him_role"] and k["t_min"] < 10 and k["zone"].startswith("JG_")
          and G.rel_zone(k["zone"], team).startswith("对方")]
    return {"match_id": g["match_id"], "minutes_in_enemy_jg_pre10": len(mins), "first_minute": mins[0] if mins else None,
            "opp_minutes_in_his_jg_pre10": len(opp_mins), "fights_in_enemy_jg_pre10": len(ev),
            "enemy_jg_cs_game": g.get("enemy_jg_cs"), "ch_enemy_jg_monsters": g.get("ch_enemy_jg_monsters"),
            "ch_initial_crab": g.get("ch_initial_crab"), "ch_scuttle": g.get("ch_scuttle")}


def _invade_summary(rows):
    n = len(rows)
    return {"rows": rows, "n": n,
            "games_with_invade_pre10": sum(1 for r in rows if r["minutes_in_enemy_jg_pre10"] > 0),
            "avg_minutes_pre10": mean(r["minutes_in_enemy_jg_pre10"] for r in rows),
            "opp_avg_minutes_pre10": mean(r["opp_minutes_in_his_jg_pre10"] for r in rows),
            "avg_enemy_jg_cs_game": mean(r["enemy_jg_cs_game"] for r in rows),
            "avg_scuttle": mean(r["ch_scuttle"] for r in rows),
            "basis": "第 2–10 分钟的整分钟位置在对方野区的次数（推测，每分钟一个点）；"
                     "全场吃对方野怪数来自对局详情 totalEnemyJungleMinionsKilled（直接数据）"}


OBJ_TYPES = [("DRAGON", "小龙（元素龙）"), ("HORDE", "虚空巢虫"), ("RIFTHERALD", "峡谷先锋"),
             ("BARON_NASHOR", "纳什男爵"), ("ATAKHAN", "阿塔坎"), ("ELDER_DRAGON", "远古龙")]


def _objectives(jg, o_by, src):
    rows = []
    for t, zh in OBJ_TYPES:
        evs = []
        firsts = []
        for g in jg:
            es = [o for o in o_by.get(g["match_id"], []) if _otype(o) == t]
            evs.extend(es)
            mine = [o for o in es if o["his_team_took"] == 1]
            if mine:
                firsts.append(min(o["t_min"] for o in mine))
        if not evs:
            continue
        took = [o for o in evs if o["his_team_took"] == 1]
        rows.append({"type": t, "label": zh, "total": len(evs), "his_team": len(took),
                     "his_team_share": pct(len(took), len(evs)),
                     "him_involved_of_team": pct(sum(o["him_involved"] for o in took), len(took)),
                     "first_take_median_min": median(firsts), "games_with_take": len(firsts),
                     "first_spawn_take_median_min": median(min(o["t_min"] for o in o_by.get(g["match_id"], [])
                                                                if _otype(o) == t)
                                                            for g in jg if any(_otype(o) == t for o in o_by.get(g["match_id"], [])))})
    # 每场谁先拿到第一个小龙/第一组巢虫
    first_obj = Counter()
    for g in jg:
        es = sorted([o for o in o_by.get(g["match_id"], []) if o["his_team_took"] == 1
                     and _otype(o) in ("DRAGON", "HORDE", "RIFTHERALD")], key=lambda o: o["t_min"])
        if es:
            first_obj[_otype(es[0])] += 1
        else:
            first_obj["无"] += 1
    return {"rows": rows, "first_objective_of_his_team": first_obj.most_common(),
            "src": src(jg, "match-v5 时间线 ELITE_MONSTER_KILL 事件")}


def _otype(o):
    if o["monster_type"] == "DRAGON" and o["sub_type"] == "ELDER_DRAGON":
        return "ELDER_DRAGON"
    return o["monster_type"]


def _half_pref(jg, fr_by, first, objectives):
    top = bot = 0
    for g in jg:
        for m, f in fr_by.get(g["match_id"], {}).items():
            if 3 <= m <= 14 and f["x"] is not None and not f["zone"].startswith("BASE_"):
                if G.half_of(f["x"], f["y"]) == "TOP":
                    top += 1
                else:
                    bot += 1
    fg = Counter(f["lane_code"] for f in first if f["lane_code"])
    fo = dict(tuple(x) for x in objectives["first_objective_of_his_team"])
    signals = {"frames_top_share": pct(top, top + bot), "frames_n": top + bot,
               "first_gank_top": fg.get("TOP", 0), "first_gank_bot": fg.get("BOT", 0), "first_gank_mid": fg.get("MID", 0),
               "first_obj_grubs_or_herald": fo.get("HORDE", 0) + fo.get("RIFTHERALD", 0), "first_obj_dragon": fo.get("DRAGON", 0)}

    def vote(a, b):  # 场次差 ≥2 才投票
        return 1 if a - b >= 2 else (-1 if b - a >= 2 else 0)

    score = 0
    if signals["frames_top_share"] is not None:
        score += 1 if signals["frames_top_share"] >= 0.60 else (-1 if signals["frames_top_share"] <= 0.40 else 0)
    score += vote(signals["first_gank_top"], signals["first_gank_bot"])
    score += vote(signals["first_obj_grubs_or_herald"], signals["first_obj_dragon"])
    verdict = "偏上半区（上路/巢虫）" if score >= 2 else ("偏下半区（下路/小龙）" if score <= -2 else "没有明显偏向")
    return {"signals": signals, "score": score, "verdict": verdict, "inferred": True, "small": len(jg) < SMALL_N,
            "basis": "三个信号各记 +1（上半）/−1（下半）/0：3–14 分钟整分钟位置在上半区的比例（≥60% / ≤40%）；"
                     "第一次抓人去上路和去下路的场次（相差 ≥2 场才算）；他所在队伍拿的第一个资源是巢虫/先锋还是小龙"
                     "（相差 ≥2 场才算；注意小龙 5:00 刷新、巢虫 8:00 刷新，小龙天然更早）。合计 ≥2 判上半，≤−2 判下半"}


# ---------------------------------------------------------------- 死亡分布
def _deaths(tl_games, kills, src):
    inc = {g["match_id"]: g for g in tl_games}
    rows = []
    for k in kills:
        if k["match_id"] in inc and k["him_role"] == "V" and k["x"] is not None:
            g = inc[k["match_id"]]
            rows.append({"match_id": k["match_id"], "t_min": k["t_min"], "t": mmss(k["t_min"]), "x": k["x"], "y": k["y"],
                         "zone": G.rel_zone(k["zone"], g["team_id"]), "n_attackers": k["n_attackers"],
                         "executed": k["executed"], "killer": k["killer_champ"], "champ_zh": g["champ_zh"],
                         "side": g["side"], "win": g["win"]})
    early = [r for r in rows if r["t_min"] < 10]
    bins = [("0:00–1:59", 0, 2), ("2:00–3:59", 2, 4), ("4:00–5:59", 4, 6), ("6:00–7:59", 6, 8), ("8:00–9:59", 8, 10)]
    tb = [{"bin": b, "deaths": sum(1 for r in early if lo <= r["t_min"] < hi)} for b, lo, hi in bins]
    zc = Counter(r["zone"] for r in early).most_common()
    allz = Counter(r["zone"] for r in rows).most_common()
    return {"rows": rows, "n_games": len(tl_games), "total": len(rows), "early_total": len(early),
            "early_zone_counts": zc, "all_zone_counts": allz, "early_time_bins": tb,
            "early_top_zone": zc[0] if zc else None,
            "early_top_bin": _top_bin(tb) if early else None,
            "src": src(tl_games, "match-v5 时间线 CHAMPION_KILL 事件（他是受害者）")}


def _top_bin(tb):
    top = max(b["deaths"] for b in tb)
    winners = [b["bin"] for b in tb if b["deaths"] == top]
    return {"bin": "、".join(winners) + ("（并列）" if len(winners) > 1 else ""), "deaths": top}


# ---------------------------------------------------------------- 弱点
def _weakness(games, tl_games, kills, frames, parts, src):
    tl_ids = {g["match_id"] for g in tl_games}
    early = defaultdict(lambda: {"deaths": 0, "ganked": 0})
    for k in kills:
        if k["match_id"] in tl_ids and k["him_role"] == "V" and k["t_min"] < 10:
            early[k["match_id"]]["deaths"] += 1
            early[k["match_id"]]["ganked"] += int((k["n_attackers"] or 0) >= 2)
    W = [g for g in games if g["win"]]
    L = [g for g in games if not g["win"]]

    def m(gs, f, tl_only=False):
        gs = [g for g in gs if g["match_id"] in tl_ids] if tl_only else gs
        return mean(f(g) for g in gs)

    feats = [
        ("场均死亡", lambda g: g["deaths"], False),
        ("前 10 分钟死亡", lambda g: early[g["match_id"]]["deaths"], True),
        ("前 10 分钟被抓死", lambda g: early[g["match_id"]]["ganked"], True),
        ("10 分钟对位经济差", lambda g: g.get("gold_diff_10"), True),
        ("15 分钟对位经济差", lambda g: g.get("gold_diff_15"), True),
        ("15 分钟全队经济差", lambda g: g.get("team_gold_diff_15"), True),
        ("参团率", lambda g: g["kp"], False),
        ("伤害占比", lambda g: g["dmg_share"], False),
        ("每分钟视野得分", lambda g: g["vision_per_min"], False),
        ("首件成装（分钟）", lambda g: g["first_item_time_s"] / 60.0 if g.get("first_item_time_s") else None, True),
        ("己方拿小龙数", lambda g: g.get("team_dragons"), False),
        ("己方拿巢虫数", lambda g: g.get("team_grubs"), False),
        ("对局时长（分钟）", lambda g: g["duration_min"], False),
    ]
    table = [{"feature": name, "wins": m(W, f, tl), "losses": m(L, f, tl)} for name, f, tl in feats]
    # 输的类型
    types = []
    for g in L:
        e = early[g["match_id"]]
        tg15 = g.get("team_gold_diff_15")
        reasons = []
        if g["match_id"] in tl_ids and e["ganked"] >= 2:
            reasons.append("被抓崩（前 10 分钟被多人击杀 ≥2 次）")
        if tg15 is not None and tg15 <= -2000:
            reasons.append("前期崩（15 分钟全队落后 ≥2000 金）")
        if tg15 is not None and tg15 >= 0 and g["duration_min"] >= 30:
            reasons.append("前期领先/均势、后期被拖死（15 分钟不落后、30 分钟后输）")
        if not reasons:
            reasons.append("其它/数据不足" if tg15 is None else "中期逐渐落后")
        types.append({"match_id": g["match_id"], "champ_zh": g["champ_zh"], "opp_champ_zh": g.get("opp_champ_zh"),
                      "duration_min": g["duration_min"], "team_gold_diff_15": tg15, "early_deaths": e["deaths"],
                      "early_ganked": e["ganked"], "types": reasons})
    type_count = Counter(r for t in types for r in t["types"]).most_common()
    # 对面同位置英雄、敌方英雄在他输的局里出现的次数
    opp_rec = defaultdict(lambda: [0, 0])
    for g in games:
        if g.get("opp_champ_zh"):
            opp_rec[g["opp_champ_zh"]][0 if g["win"] else 1] += 1
    inc = {g["match_id"]: g for g in games}
    enemy_rec = defaultdict(lambda: [0, 0])
    for p in parts:
        if p["match_id"] in inc and p["same_team"] == 0:
            enemy_rec[p["champ_zh"]][0 if inc[p["match_id"]]["win"] else 1] += 1
    opp_rows = sorted([{"champ_zh": c, "his_wins": w, "his_losses": l} for c, (w, l) in opp_rec.items()],
                      key=lambda r: (-r["his_losses"], r["his_wins"]))
    enemy_rows = sorted([{"champ_zh": c, "his_wins": w, "his_losses": l} for c, (w, l) in enemy_rec.items()
                         if l >= 2], key=lambda r: (-(r["his_losses"] - r["his_wins"]), -r["his_losses"]))[:10]
    return {"table": table, "n_wins": len(W), "n_losses": len(L), "loss_types": types, "loss_type_count": type_count,
            "opp_champs": opp_rows, "enemy_champs_in_losses": enemy_rows,
            "small": len(L) < SMALL_N,
            "rule": "输的类型：被抓崩 = 前 10 分钟被 ≥2 名敌人击杀 ≥2 次；前期崩 = 15 分钟全队经济落后 ≥2000；"
                    "后期被拖死 = 15 分钟不落后但 30 分钟后输。一场可以有多个类型。",
            "src": src(games)}


def _ban(pool, src):
    rows = []
    for r in pool["rows"]:
        rows.append({"champ_zh": r["champ_zh"], "champ": r["champ"], "games": r["games"], "wins": r["wins"],
                     "wr": r["wr"], "net_wins": r["net_wins"], "smoothed_wr": r["smoothed_wr"],
                     "calc": "胜率 = %d/%d = %.0f%%；净胜场 = 胜 %d − 负 %d = %+d；平滑胜率 = (%d+5)/(%d+10) = %.0f%%"
                             % (r["wins"], r["games"], 100 * r["wr"], r["wins"], r["losses"], r["net_wins"],
                                r["wins"], r["games"], 100 * r["smoothed_wr"])})
    cand = [r for r in rows if r["games"] >= 3]
    cand.sort(key=lambda r: (-r["net_wins"], -r["games"], -r["smoothed_wr"]))
    picks = [r for r in cand if r["smoothed_wr"] >= 0.5][:2]
    fallback = None
    if not picks and cand:
        best = max(cand, key=lambda r: r["games"])
        picks = [best]
        fallback = "没有“≥3 场且平滑胜率 ≥50%”的英雄，改按场次最多推荐（他在这个英雄上胜率并不高，禁它只是限制他的熟练英雄）"
    return {"rows": rows, "recommend": picks, "fallback": fallback, "small": sum(r["games"] for r in rows) < SMALL_N,
            "rule": "只考虑他玩过 ≥3 场的英雄；按净胜场（胜 − 负）从高到低排，平手看场次，再看平滑胜率"
                    "（(胜+5)/(场+10)，相当于先假设 10 场 50% 胜率，避免 2 场 2 胜这种小样本排到最前）；"
                    "取平滑胜率 ≥50% 的前 1–2 个",
            "src": pool["src"]}


def _ref(C, pool, my_pool, top_n=4):
    """Data Dragon 当前版本的技能数据：他最常用的几个英雄 + 我的英雄池。"""
    path = os.path.join(C, "champion_spells.csv")
    if not os.path.exists(path):
        return None
    rows = read_csv(path)
    by = defaultdict(list)
    for r in rows:
        by[r["champ"]].append(r)
    his = [c["champ"] for c in pool["rows"][:top_n]]
    out = {"version": rows[0]["version"] if rows else None, "his": [], "mine": []}
    for key, champs in (("his", his), ("mine", my_pool)):
        for c in champs:
            if c in by:
                rs = by[c]
                out[key].append({"champ": c, "name_zh": rs[0]["name_zh"],
                                 "spells": [{k: r[k] for k in ("slot", "spell_name", "cooldown", "cost", "cost_type", "range")}
                                            for r in rs]})
            else:
                out[key].append({"champ": c, "name_zh": c, "spells": [], "missing": True})
    out["src"] = "Data Dragon %s championFull.json（zh_CN）" % (out["version"] or "?")
    return out


def _lobby_ranks(ds_meta, main_games, src):
    ranks = ds_meta.get("lane_opp_ranks") or {}
    if not ranks:
        return None
    c = Counter()
    for g in main_games:
        r = ranks.get(g.get("opp_puuid") or "")
        if not r:
            continue
        solo = [e for e in r.get("entries") or [] if e.get("queueType") == "RANKED_SOLO_5x5"]
        c[(solo[0]["tier"] + " " + solo[0]["rank"]) if solo else "无单双排段位"] += 1
    return {"counts": c.most_common(), "n": sum(c.values()),
            "src": "league-v4 /lol/league/v4/entries/by-puuid（对面同位置玩家，下载时的当前段位，不是对局时的段位）"}


# ---------------------------------------------------------------- 打印
def _p(x, d=0):
    return "—" if x is None else ("%.*f%%" % (d, 100 * x))


def _f(x, d=1):
    return "—" if x is None else ("%.*f" % (d, x))


def print_summary(S, log=print):
    m = S["meta"]
    log("=" * 60)
    log("%s | 纳入 %d/%d 场（有时间线 %d）| 版本 %s | 下载 %s" % (
        m.get("riot_id"), m["n_included"], m["n_ids"], m["n_timeline"], "/".join(m["patches"]), m.get("downloaded_date")))
    r = S.get("rank")
    if r:
        log("段位：%s %s %s LP，本赛季 %s 胜 %s 负" % (r.get("tier"), r.get("rank"), r.get("leaguePoints"), r.get("wins"), r.get("losses")))
    log("位置：" + "，".join("%s %d 场" % (p["position"], p["games"]) for p in S["positions"]))
    log("-- 英雄池（场次/胜率/KDA/分均补刀/伤害占比/参团/视野分）")
    for c in S["champ_pool"]["rows"][:8]:
        log("  %-8s %2d 场 %4s  KDA %s  %s  %s  %s  %s" % (c["champ_zh"], c["games"], _p(c["wr"]), _f(c["kda"], 2),
                                                        _f(c["cs_per_min"]), _p(c["dmg_share"]), _p(c["kp"]), _f(c["vision"], 0)))
    log("-- 时长分段：" + "；".join("%s %d 场 胜率 %s" % (b["bucket"], b["games"], _p(b["wr"])) for b in S["duration"]["rows"])
        + " → " + S["duration"]["verdict"])
    t = S["tilt"]["all"]
    log("-- 输后下一场：%d 场 胜率 %s 场均死 %s；赢后下一场：%d 场 胜率 %s 场均死 %s → %s" % (
        t["after_loss"]["n"], _p(t["after_loss"]["wr"]), _f(t["after_loss"]["avg_deaths"]),
        t["after_win"]["n"], _p(t["after_win"]["wr"]), _f(t["after_win"]["avg_deaths"]), S["tilt"]["verdict"]))
    log("-- 双排：" + ("；".join("%s %d 场 胜率 %s" % (d["riot_id"], d["games"], _p(d["wr"])) for d in S["duo"]["rows"]) or "无（没有出现 ≥3 次的队友）"))
    for pos, row in S["lane"]["by_position"].items():
        log("-- %s 对位差（n=%d）：10 分 经济 %s 经验 %s 补刀 %s；15 分 经济 %s；首件成装 %s 分" % (
            pos, row["n"], _f(row["gold_diff_10"]["mean"], 0), _f(row["xp_diff_10"]["mean"], 0),
            _f(row["cs_diff_10"]["mean"]), _f(row["gold_diff_15"]["mean"], 0), _f(row["first_item"]["median"])))
    es = S["lane"]["early_summary"]
    log("-- 前 10 分钟：击杀 %d 助攻 %d 死亡 %d（被抓 %d，单杀 %d）" % (es["kills"], es["assists"], es["deaths"],
                                                          es["ganked_deaths"], es["solo_deaths"]))
    J = S.get("jungle")
    if J:
        log("-- 开野（%d 场，推测）：" % J["n"] + "；".join("%s %d 场" % (s["start"], s["games"]) for s in J["starts"]))
        for p in J["patterns"][:4]:
            log("     %s：%d 场（%s）" % (p["pattern"], p["games"], _p(p["share"])))
        log("-- 第一次抓人：中位 %s，去向 %s，成功率 %s" % (mmss(J["first_gank_median"]),
                                                  "，".join("%s %d" % x for x in J["first_gank_lanes"]),
                                                  _p(J["first_gank_success"])))
        log("-- 前 10 分钟去各路：" + "；".join("%s %d 次（%d 场去过，最早中位 %s）" % (
            l["lane_zh"], l["visits_pre10"], l["games_visited"], mmss(l["first_visit_median"])) for l in J["lanes_pre10"]))
        inv = J["invade"]
        log("-- 反野：%d/%d 场前 10 分钟进过对面野区；全场场均吃对面野怪 %s" % (
            inv["games_with_invade_pre10"], inv["n"], _f(inv["avg_enemy_jg_cs_game"])))
        for o in J["objectives"]["rows"]:
            log("     %s：他方拿 %d/%d（%s），他参与 %s，首次拿中位 %s" % (o["label"], o["his_team"], o["total"],
                                                         _p(o["his_team_share"]), _p(o["him_involved_of_team"]),
                                                         mmss(o["first_take_median_min"])))
        log("-- 半区偏好（推测）：%s" % J["half_preference"]["verdict"])
    D = S["deaths"]
    log("-- 死亡：共 %d 次，前 10 分钟 %d 次；最多的区域 %s；时间段 %s" % (
        D["total"], D["early_total"], D["early_top_zone"], D["early_top_bin"]))
    Wk = S["weakness"]
    log("-- 输的类型（%d 负）：" % Wk["n_losses"] + "；".join("%s %d" % x for x in Wk["loss_type_count"]))
    log("-- 建议禁用：" + "；".join("%s（%s）" % (b["champ_zh"], b["calc"]) for b in S["ban"]["recommend"]))
    if S["checks"]["challenges_mismatch"]:
        log("!! 自算值与官方 challenges 不一致 %d 处（见 summary.json checks）" % len(S["checks"]["challenges_mismatch"]))
    if S["checks"]["geometry_selftest_failures"]:
        log("!! 区域划分自检失败：%s" % S["checks"]["geometry_selftest_failures"])
    log("=" * 60)
