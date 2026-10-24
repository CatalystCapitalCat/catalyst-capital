"""第一遍：原始 JSON → CSV（只解析一次，后面统计都读 CSV）。

输出（out/csv/）：
  games.csv         每场一行：他的英雄/位置/红蓝方/胜负/时长、对位、团队资源、10/15 分钟差值、首件成装……
  participants.csv  每场 10 人的基础数据（找双排、算同场对位平均用）
  frames.csv        每分钟一帧、每人一行：坐标、区域、经济、经验、补刀
  kills.csv         所有击杀事件：时间、坐标、区域、击杀者/助攻/受害者、参与人数
  objectives.csv    小龙/巢虫/先锋/男爵等击杀事件
  buildings.csv     防御塔/水晶被摧毁事件
  items.csv         他和对位的购买记录（已扣掉撤销的购买）
  parse_log.json    每场的处理结果和被排除的原因
"""

import csv
import datetime as dt
import json
import os
import re

from . import geometry as G

SKILL_KEYS = {1: "Q", 2: "W", 3: "E", 4: "R"}
POSITION_ZH = {"TOP": "上路", "JUNGLE": "打野", "MIDDLE": "中路", "BOTTOM": "下路", "UTILITY": "辅助", "": "未知"}


def patch_of(game_version):
    m = re.match(r"^(\d+)\.(\d+)", game_version or "")
    return "%s.%s" % (m.group(1), m.group(2)) if m else ""


def ms_to_iso(ms):
    if not ms:
        return ""
    return dt.datetime.fromtimestamp(ms / 1000.0, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _r(x, n=3):
    return round(x, n) if isinstance(x, float) else x


class CSVOut:
    def __init__(self, path, fields):
        self.fh = open(path, "w", encoding="utf-8-sig", newline="")
        self.w = csv.DictWriter(self.fh, fieldnames=fields, extrasaction="raise")
        self.w.writeheader()
        self.n = 0

    def row(self, d):
        self.w.writerow({k: _r(d.get(k, "")) for k in self.w.fieldnames})
        self.n += 1

    def close(self):
        self.fh.close()


GAME_FIELDS = [
    "match_id", "included", "exclude_reason", "game_start_utc", "game_end_utc", "game_version", "patch",
    "duration_s", "duration_min", "remake", "surrender", "has_timeline",
    "pid", "champ", "champ_zh", "team_id", "side", "position", "position_zh", "win",
    "kills", "deaths", "assists", "kda", "cs_lane", "cs_jungle", "cs_total", "cs_per_min", "gold",
    "dmg", "team_dmg", "dmg_share", "team_kills", "kp", "vision", "vision_per_min", "wards_placed",
    "wards_killed", "control_wards", "champ_level", "ally_jg_cs", "enemy_jg_cs", "first_blood",
    "summoner1", "summoner2", "skill_order_1_3",
    "ch_present", "ch_kp", "ch_dmg_pct", "ch_initial_buff", "ch_initial_crab", "ch_scuttle", "ch_jg_cs_pre10",
    "ch_enemy_jg_monsters", "ch_takedowns_first_x", "ch_kills_on_laners_early_jg", "ch_solo_kills",
    "ch_lane_minions_pre10", "ch_dragon_td", "ch_baron_td", "ch_herald_td", "ch_void_monster_kill",
    "ch_epic_steals",
    "opp_pid", "opp_puuid", "opp_name", "opp_champ", "opp_champ_zh", "opp_kills", "opp_deaths", "opp_assists",
    "opp_cs_per_min", "opp_dmg_share", "opp_kp", "opp_vision", "opp_vision_per_min", "opp_gold",
    "team_dragons", "team_grubs", "team_heralds", "team_barons", "team_towers", "team_atakhan",
    "enemy_dragons", "enemy_grubs", "enemy_heralds", "enemy_barons", "enemy_towers", "enemy_atakhan",
    "gold_diff_10", "xp_diff_10", "cs_diff_10", "gold_diff_15", "xp_diff_15", "cs_diff_15",
    "team_gold_diff_10", "team_gold_diff_15", "team_gold_diff_20",
    "first_item_id", "first_item_name", "first_item_time_s", "opp_first_item_time_s",
]
PART_FIELDS = [
    "match_id", "pid", "puuid", "riot_id", "team_id", "position", "champ", "champ_zh", "win",
    "kills", "deaths", "assists", "cs_total", "cs_per_min", "gold", "dmg", "dmg_share", "kp", "vision",
    "vision_per_min", "is_him", "same_team", "is_lane_opp",
    "gold_10", "xp_10", "cs_10", "gold_15", "xp_15", "cs_15", "first_item_time_s",
]
FRAME_FIELDS = ["match_id", "minute", "ts_ms", "pid", "team_id", "is_him", "is_opp", "x", "y", "zone",
                "total_gold", "xp", "level", "cs_lane", "cs_jungle"]
KILL_FIELDS = ["match_id", "ts_ms", "t_min", "killer_pid", "victim_pid", "assist_pids", "killer_team",
               "victim_team", "killer_champ", "victim_champ", "x", "y", "zone", "n_attackers", "executed",
               "him_role", "opp_role", "bounty", "shutdown"]
OBJ_FIELDS = ["match_id", "ts_ms", "t_min", "monster_type", "sub_type", "killer_pid", "killer_team",
              "his_team_took", "him_involved", "opp_involved", "x", "y"]
BLD_FIELDS = ["match_id", "ts_ms", "t_min", "building_type", "tower_type", "lane_type", "owner_team",
              "killer_pid", "his_team_destroyed", "him_involved"]
ITEM_FIELDS = ["match_id", "pid", "who", "ts_ms", "t_min", "item_id", "name_zh", "gold_total", "completed"]


def _team_totals(parts, team_id, key):
    return sum((p.get(key) or 0) for p in parts if p.get("teamId") == team_id)


def _obj(team, key):
    return ((team or {}).get("objectives") or {}).get(key, {}).get("kills", "")


def frame_at(frames, minute):
    """第 minute 分钟的帧（时间戳离 minute*60s 不超过 30 秒），没有返回 None。"""
    if minute < len(frames):
        f = frames[minute]
        if abs(f.get("timestamp", 0) - minute * 60000) <= 30000:
            return f
    for f in frames:
        if abs(f.get("timestamp", 0) - minute * 60000) <= 30000:
            return f
    return None


def resolve_purchases(events):
    """按参与者整理购买记录，扣掉被撤销（ITEM_UNDO）的购买。返回 {pid: [(ts, itemId), ...]}"""
    buys = {}
    for e in events:
        t = e.get("type")
        pid = e.get("participantId")
        if not pid:  # 辅助的世界地图集在 0 秒以 participantId 0 记录，跳过
            continue
        if t == "ITEM_PURCHASED":
            buys.setdefault(pid, []).append([e.get("timestamp", 0), e.get("itemId")])
        elif t == "ITEM_UNDO":
            before, after = e.get("beforeId") or 0, e.get("afterId") or 0
            if before and not after:  # 撤销一次购买
                lst = buys.get(pid, [])
                for i in range(len(lst) - 1, -1, -1):
                    if lst[i][1] == before and lst[i][0] <= e.get("timestamp", 0):
                        del lst[i]
                        break
    return {pid: [tuple(x) for x in lst] for pid, lst in buys.items()}


def parse_dataset(ds, static, out_dir, log=print):
    csv_dir = os.path.join(out_dir, "csv")
    os.makedirs(csv_dir, exist_ok=True)
    W = {
        "games": CSVOut(os.path.join(csv_dir, "games.csv"), GAME_FIELDS),
        "parts": CSVOut(os.path.join(csv_dir, "participants.csv"), PART_FIELDS),
        "frames": CSVOut(os.path.join(csv_dir, "frames.csv"), FRAME_FIELDS),
        "kills": CSVOut(os.path.join(csv_dir, "kills.csv"), KILL_FIELDS),
        "objs": CSVOut(os.path.join(csv_dir, "objectives.csv"), OBJ_FIELDS),
        "blds": CSVOut(os.path.join(csv_dir, "buildings.csv"), BLD_FIELDS),
        "items": CSVOut(os.path.join(csv_dir, "items.csv"), ITEM_FIELDS),
    }
    plog = {"puuid": ds.puuid, "matches": {}, "item_versions": {}, "challenges_mismatch": []}
    try:
        for mid in ds.match_ids:
            status = _parse_one(ds, static, mid, W, plog)
            plog["matches"][mid] = status
    finally:
        for w in W.values():
            w.close()
    plog["row_counts"] = {k: w.n for k, w in W.items()}
    with open(os.path.join(out_dir, "parse_log.json"), "w", encoding="utf-8") as fh:
        json.dump(plog, fh, ensure_ascii=False, indent=2)
    inc = sum(1 for s in plog["matches"].values() if s.get("included"))
    log("解析完成：%d 场 ID，纳入统计 %d 场；CSV 行数 %s" % (len(ds.match_ids), inc, plog["row_counts"]))
    for mid, s in plog["matches"].items():
        if not s.get("included"):
            log("  排除 %s：%s" % (mid, s.get("reason")))
    return plog


def _parse_one(ds, static, mid, W, plog):
    match = ds.match(mid)
    if match is None:
        rec = (ds.manifest.get("matches") or {}).get(mid, {})
        return {"included": False, "reason": "对局详情没下载成功（%s）" % (rec.get("error") or "文件不存在")}
    info = match.get("info") or {}
    parts = info.get("participants") or []
    me = next((p for p in parts if p.get("puuid") == ds.puuid), None)
    if me is None:
        return {"included": False, "reason": "对局里找不到他的 PUUID"}
    if info.get("queueId") != 420:
        return {"included": False, "reason": "不是单双排（queueId=%s）" % info.get("queueId")}

    dur = info.get("gameDuration") or 0
    if dur > 100000:  # 11.20 以前的旧数据是毫秒
        dur = dur / 1000.0
    remake = any(p.get("gameEndedInEarlySurrender") for p in parts) or dur < 300
    patch = patch_of(info.get("gameVersion"))
    my_team = me.get("teamId")
    pos = me.get("teamPosition") or me.get("individualPosition") or ""
    if pos == "Invalid":
        pos = ""
    opp = None
    if me.get("teamPosition"):
        opp = next((p for p in parts if p.get("teamId") != my_team and p.get("teamPosition") == me["teamPosition"]), None)

    teams = {t.get("teamId"): t for t in info.get("teams") or []}
    team_kills = {tid: _team_totals(parts, tid, "kills") for tid in (100, 200)}
    team_dmg = {tid: _team_totals(parts, tid, "totalDamageDealtToChampions") for tid in (100, 200)}
    mins = dur / 60.0 if dur else 0

    def basic(p):
        tid = p.get("teamId")
        cs = (p.get("totalMinionsKilled") or 0) + (p.get("neutralMinionsKilled") or 0)
        k, d, a = p.get("kills") or 0, p.get("deaths") or 0, p.get("assists") or 0
        return {
            "kills": k, "deaths": d, "assists": a, "kda": (k + a) / max(1, d),
            "cs_total": cs, "cs_per_min": cs / mins if mins else 0,
            "gold": p.get("goldEarned") or 0, "dmg": p.get("totalDamageDealtToChampions") or 0,
            "dmg_share": (p.get("totalDamageDealtToChampions") or 0) / team_dmg[tid] if team_dmg.get(tid) else 0,
            "kp": (k + a) / team_kills[tid] if team_kills.get(tid) else 0,
            "vision": p.get("visionScore") or 0,
            "vision_per_min": (p.get("visionScore") or 0) / mins if mins else 0,
        }

    g = {
        "match_id": mid, "game_version": info.get("gameVersion", ""), "patch": patch,
        "game_start_utc": ms_to_iso(info.get("gameStartTimestamp") or info.get("gameCreation")),
        "game_end_utc": ms_to_iso(info.get("gameEndTimestamp")),
        "duration_s": int(dur), "duration_min": mins, "remake": int(remake),
        "surrender": int(any(p.get("gameEndedInSurrender") for p in parts)),
        "pid": me.get("participantId"), "champ": static.champ_id(me),
        "champ_zh": static.champ_name(static.champ_id(me)), "team_id": my_team,
        "side": "蓝方" if my_team == 100 else "红方", "position": pos, "position_zh": POSITION_ZH.get(pos, pos),
        "win": int(bool(me.get("win"))), "cs_lane": me.get("totalMinionsKilled") or 0,
        "cs_jungle": me.get("neutralMinionsKilled") or 0, "team_dmg": team_dmg.get(my_team),
        "team_kills": team_kills.get(my_team), "wards_placed": me.get("wardsPlaced") or 0,
        "wards_killed": me.get("wardsKilled") or 0, "control_wards": me.get("visionWardsBoughtInGame") or 0,
        "champ_level": me.get("champLevel") or 0, "ally_jg_cs": me.get("totalAllyJungleMinionsKilled") or 0,
        "enemy_jg_cs": me.get("totalEnemyJungleMinionsKilled") or 0,
        "first_blood": int(bool(me.get("firstBloodKill") or me.get("firstBloodAssist"))),
        "summoner1": me.get("summoner1Id"), "summoner2": me.get("summoner2Id"),
    }
    g.update(basic(me))
    ch = me.get("challenges")
    g["ch_present"] = int(isinstance(ch, dict))
    if isinstance(ch, dict):
        for col, key in (("ch_kp", "killParticipation"), ("ch_dmg_pct", "teamDamagePercentage"),
                         ("ch_initial_buff", "initialBuffCount"), ("ch_initial_crab", "initialCrabCount"),
                         ("ch_scuttle", "scuttleCrabKills"), ("ch_jg_cs_pre10", "jungleCsBefore10Minutes"),
                         ("ch_enemy_jg_monsters", "enemyJungleMonsterKills"),
                         ("ch_takedowns_first_x", "takedownsFirstXMinutes"),
                         ("ch_kills_on_laners_early_jg", "killsOnLanersEarlyJungleAsJungler"),
                         ("ch_solo_kills", "soloKills"), ("ch_lane_minions_pre10", "laneMinionsFirst10Minutes"),
                         ("ch_dragon_td", "dragonTakedowns"), ("ch_baron_td", "baronTakedowns"),
                         ("ch_herald_td", "riftHeraldTakedowns"), ("ch_void_monster_kill", "voidMonsterKill"),
                         ("ch_epic_steals", "epicMonsterSteals")):
            if key in ch:
                g[col] = ch[key]
        # 与官方 challenges 交叉核对：参团率、伤害占比
        for mine, key in (("kp", "killParticipation"), ("dmg_share", "teamDamagePercentage")):
            if key in ch and abs((ch[key] or 0) - g[mine]) > 0.02:
                plog["challenges_mismatch"].append({"match_id": mid, "field": mine, "ours": round(g[mine], 3),
                                                     "challenges": round(ch[key] or 0, 3)})
    if opp:
        ob = basic(opp)
        g.update({
            "opp_pid": opp.get("participantId"), "opp_puuid": opp.get("puuid"),
            "opp_name": "%s#%s" % (opp.get("riotIdGameName") or opp.get("summonerName") or "?", opp.get("riotIdTagline") or ""),
            "opp_champ": static.champ_id(opp), "opp_champ_zh": static.champ_name(static.champ_id(opp)),
            "opp_kills": ob["kills"], "opp_deaths": ob["deaths"], "opp_assists": ob["assists"],
            "opp_cs_per_min": ob["cs_per_min"], "opp_dmg_share": ob["dmg_share"], "opp_kp": ob["kp"],
            "opp_vision": ob["vision"], "opp_vision_per_min": ob["vision_per_min"], "opp_gold": ob["gold"],
        })
    enemy = 200 if my_team == 100 else 100
    for pre, tid in (("team", my_team), ("enemy", enemy)):
        t = teams.get(tid)
        g[pre + "_dragons"] = _obj(t, "dragon")
        g[pre + "_grubs"] = _obj(t, "horde")
        g[pre + "_heralds"] = _obj(t, "riftHerald")
        g[pre + "_barons"] = _obj(t, "baron")
        g[pre + "_towers"] = _obj(t, "tower")
        g[pre + "_atakhan"] = _obj(t, "atakhan")

    tl = ds.timeline(mid)
    g["has_timeline"] = int(tl is not None)
    reason = None
    if remake:
        reason = "重开局（提前投降/时长 < 5 分钟）"
    elif not pos:
        reason = "没有位置信息"
    g["included"] = int(reason is None)
    g["exclude_reason"] = reason or ""

    pid_team = {p.get("participantId"): p.get("teamId") for p in parts}
    pid_champ = {p.get("participantId"): static.champ_name(static.champ_id(p)) for p in parts}
    first_item = {}
    snap = {}
    if tl is not None and not remake:
        tinfo = tl.get("info") or {}
        # 时间线的 participantId 以 timeline.info.participants 的 puuid 对应关系为准
        tl_map = {x.get("puuid"): x.get("participantId") for x in tinfo.get("participants") or []}
        if tl_map:
            remap = {tl_map.get(p.get("puuid")): p for p in parts}
            pid_team = {pid: p.get("teamId") for pid, p in remap.items() if pid}
            pid_champ = {pid: static.champ_name(static.champ_id(p)) for pid, p in remap.items() if pid}
            my_pid = tl_map.get(ds.puuid, g["pid"])
            opp_pid = tl_map.get(opp.get("puuid")) if opp else None
        else:
            my_pid, opp_pid = g["pid"], g.get("opp_pid")
        frames = tinfo.get("frames") or []
        events = [e for f in frames for e in (f.get("events") or [])]
        _frames_rows(mid, frames, pid_team, my_pid, opp_pid, W["frames"])
        for minute in (10, 15, 20):
            f = frame_at(frames, minute)
            if f:
                snap[minute] = {int(k): v for k, v in (f.get("participantFrames") or {}).items()}
        for minute in (10, 15):
            s = snap.get(minute)
            if s and opp_pid and my_pid in s and opp_pid in s:
                a, b = s[my_pid], s[opp_pid]
                g["gold_diff_%d" % minute] = (a.get("totalGold") or 0) - (b.get("totalGold") or 0)
                g["xp_diff_%d" % minute] = (a.get("xp") or 0) - (b.get("xp") or 0)
                g["cs_diff_%d" % minute] = ((a.get("minionsKilled") or 0) + (a.get("jungleMinionsKilled") or 0)
                                            - (b.get("minionsKilled") or 0) - (b.get("jungleMinionsKilled") or 0))
        for minute in (10, 15, 20):
            s = snap.get(minute)
            if s:
                tg = {100: 0, 200: 0}
                for pid, pf in s.items():
                    if pid_team.get(pid) in tg:
                        tg[pid_team[pid]] += pf.get("totalGold") or 0
                g["team_gold_diff_%d" % minute] = tg[my_team] - tg[enemy]
        # 加点顺序（前 3 级）
        # 15.17 起时间线有重复的 SKILL_LEVEL_UP 事件：按 (参与者, 技能, 时间) 去重
        seen, skills = set(), []
        for e in events:
            if (e.get("type") == "SKILL_LEVEL_UP" and e.get("participantId") == my_pid
                    and e.get("levelUpType", "NORMAL") == "NORMAL"):
                k = (e.get("skillSlot"), e.get("timestamp"))
                if k not in seen:
                    seen.add(k)
                    skills.append(SKILL_KEYS.get(e.get("skillSlot"), "?"))
        g["skill_order_1_3"] = "-".join(skills[:3])
        # 购买（扣掉撤销）→ 首件成装
        buys = resolve_purchases(events)
        item_ver = static.item_version_for_patch(patch)
        plog["item_versions"][mid] = item_ver
        for pid, lst in buys.items():
            for ts, iid in lst:
                it = static.item(patch, iid) or {}
                if it.get("completed") and pid not in first_item:
                    first_item[pid] = (ts, iid, it)
                if pid in (my_pid, opp_pid):
                    W["items"].row({"match_id": mid, "pid": pid, "who": "him" if pid == my_pid else "opp",
                                    "ts_ms": ts, "t_min": ts / 60000.0, "item_id": iid,
                                    "name_zh": it.get("name_zh", ""), "gold_total": it.get("gold_total", ""),
                                    "completed": int(bool(it.get("completed")))})
        if my_pid in first_item:
            ts, iid, it = first_item[my_pid]
            g.update({"first_item_id": iid, "first_item_name": it.get("name_zh", ""), "first_item_time_s": ts / 1000.0})
        if opp_pid in first_item:
            g["opp_first_item_time_s"] = first_item[opp_pid][0] / 1000.0
        _events_rows(mid, events, pid_team, pid_champ, my_pid, opp_pid, my_team, W)
    else:
        my_pid, opp_pid = g["pid"], g.get("opp_pid")

    W["games"].row(g)
    # participants.csv
    for p in parts:
        b = basic(p)
        pid = p.get("participantId")
        row = {"match_id": mid, "pid": pid, "puuid": p.get("puuid"),
               "riot_id": "%s#%s" % (p.get("riotIdGameName") or p.get("summonerName") or "?", p.get("riotIdTagline") or ""),
               "team_id": p.get("teamId"), "position": p.get("teamPosition") or "", "champ": static.champ_id(p),
               "champ_zh": static.champ_name(static.champ_id(p)), "win": int(bool(p.get("win"))),
               "is_him": int(p.get("puuid") == ds.puuid), "same_team": int(p.get("teamId") == my_team),
               "is_lane_opp": int(bool(opp) and p.get("puuid") == opp.get("puuid"))}
        row.update(b)
        if tl is not None and not remake:
            tpid = pid
            if tl_map:
                tpid = tl_map.get(p.get("puuid"), pid)
            for minute in (10, 15):
                s = snap.get(minute) or {}
                if tpid in s:
                    pf = s[tpid]
                    row["gold_%d" % minute] = pf.get("totalGold") or 0
                    row["xp_%d" % minute] = pf.get("xp") or 0
                    row["cs_%d" % minute] = (pf.get("minionsKilled") or 0) + (pf.get("jungleMinionsKilled") or 0)
            if tpid in first_item:
                row["first_item_time_s"] = first_item[tpid][0] / 1000.0
        W["parts"].row(row)

    status = {"included": bool(g["included"]), "reason": reason or "", "has_timeline": bool(tl is not None),
              "champ": g["champ"], "opp_champ": g.get("opp_champ") or "", "position": pos, "win": g["win"],
              "patch": patch}
    if not g["included"]:
        return status
    if tl is None:
        status["note"] = "时间线缺失：只用对局详情，时间线相关统计不含这一场"
    return status


def _frames_rows(mid, frames, pid_team, my_pid, opp_pid, out):
    for idx, f in enumerate(frames):
        ts = f.get("timestamp", 0)
        for k, pf in (f.get("participantFrames") or {}).items():
            pid = int(k)
            pos = pf.get("position") or {}
            x, y = pos.get("x"), pos.get("y")
            out.row({"match_id": mid, "minute": idx, "ts_ms": ts, "pid": pid, "team_id": pid_team.get(pid, ""),
                     "is_him": int(pid == my_pid), "is_opp": int(pid == opp_pid), "x": x, "y": y,
                     "zone": G.zone(x, y) if x is not None and y is not None else "",
                     "total_gold": pf.get("totalGold"), "xp": pf.get("xp"), "level": pf.get("level"),
                     "cs_lane": pf.get("minionsKilled"), "cs_jungle": pf.get("jungleMinionsKilled")})


def _role(pid, killer, victim, assists):
    if pid is None:
        return ""
    if pid == victim:
        return "V"
    if pid == killer:
        return "K"
    if pid in assists:
        return "A"
    return ""


def _events_rows(mid, events, pid_team, pid_champ, my_pid, opp_pid, my_team, W):
    for e in events:
        t = e.get("type")
        ts = e.get("timestamp", 0)
        pos = e.get("position") or {}
        x, y = pos.get("x"), pos.get("y")
        if t == "CHAMPION_KILL":
            killer, victim = e.get("killerId") or 0, e.get("victimId") or 0
            assists = [a for a in (e.get("assistingParticipantIds") or []) if a]
            attackers = set(a for a in assists if 1 <= a <= 10)
            if 1 <= killer <= 10:
                attackers.add(killer)
            W["kills"].row({
                "match_id": mid, "ts_ms": ts, "t_min": ts / 60000.0, "killer_pid": killer, "victim_pid": victim,
                "assist_pids": ";".join(str(a) for a in assists), "killer_team": pid_team.get(killer, ""),
                "victim_team": pid_team.get(victim, ""), "killer_champ": pid_champ.get(killer, ""),
                "victim_champ": pid_champ.get(victim, ""), "x": x, "y": y,
                "zone": G.zone(x, y) if x is not None and y is not None else "",
                "n_attackers": len(attackers), "executed": int(killer == 0),
                "him_role": _role(my_pid, killer, victim, assists), "opp_role": _role(opp_pid, killer, victim, assists),
                "bounty": e.get("bounty", ""), "shutdown": e.get("shutdownBounty", ""),
            })
        elif t == "ELITE_MONSTER_KILL":
            killer = e.get("killerId") or 0
            assists = e.get("assistingParticipantIds") or []
            kteam = e.get("killerTeamId") or pid_team.get(killer, "")
            if kteam not in (100, 200):  # 先锋到时间消失也会记一条 killerTeamId=300 的事件，不算任何一方
                continue
            W["objs"].row({
                "match_id": mid, "ts_ms": ts, "t_min": ts / 60000.0, "monster_type": e.get("monsterType", ""),
                "sub_type": e.get("monsterSubType", ""), "killer_pid": killer, "killer_team": kteam,
                "his_team_took": int(kteam == my_team), "him_involved": int(my_pid == killer or my_pid in assists),
                "opp_involved": int(opp_pid is not None and (opp_pid == killer or opp_pid in assists)), "x": x, "y": y,
            })
        elif t == "BUILDING_KILL":
            killer = e.get("killerId") or 0
            assists = e.get("assistingParticipantIds") or []
            owner = e.get("teamId", "")
            W["blds"].row({
                "match_id": mid, "ts_ms": ts, "t_min": ts / 60000.0, "building_type": e.get("buildingType", ""),
                "tower_type": e.get("towerType", ""), "lane_type": e.get("laneType", ""), "owner_team": owner,
                "killer_pid": killer, "his_team_destroyed": int(owner != "" and owner != my_team),
                "him_involved": int(my_pid == killer or my_pid in assists),
            })


def write_refdata(static, plog, my_pool, csv_dir, log=print):
    """参考数据（只来自 Data Dragon 当前版本）：
    champion_spells.csv  他用过的英雄、对位英雄、我的英雄池：被动和 QWER 的名字、冷却、消耗、距离
    items_ref.csv        他和对位买过的成装：价格、属性
    """
    champs = []
    for s in plog["matches"].values():
        if s.get("included"):
            for c in (s.get("champ"), s.get("opp_champ")):
                if c and c not in champs:
                    champs.append(c)
    for c in my_pool:
        if c not in champs:
            champs.append(c)
    n = 0
    missing = []
    with open(os.path.join(csv_dir, "champion_spells.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["champ", "name_zh", "version", "slot", "spell_name", "cooldown", "cost", "cost_type", "range",
                    "maxrank", "description"])
        for c in champs:
            sp = static.spells(c)
            if not sp:
                missing.append(c)
                continue
            w.writerow([c, sp["name"], sp["version"], "P", sp["passive"]["name"], "", "", "", "", "", sp["passive"]["desc"]])
            for x in sp["spells"]:
                w.writerow([c, sp["name"], sp["version"], x["key"], x["name"], x["cooldown"], x["cost"], x["cost_type"],
                            x["range"], x["maxrank"], x["desc"]])
                n += 1
    if missing:
        log("  championFull 里找不到：%s（Data Dragon 完整英雄数据缺失或英雄名不匹配）" % "、".join(missing))
    item_ids = set()
    items_path = os.path.join(csv_dir, "items.csv")
    if os.path.exists(items_path):
        with open(items_path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                if row.get("completed") == "1":
                    item_ids.add(int(row["item_id"]))
    with open(os.path.join(csv_dir, "items_ref.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["item_id", "name_zh", "version", "gold_total", "stats", "plaintext"])
        for iid in sorted(item_ids):
            it = static.items(static.latest).get(iid) if static.latest else None
            if it:
                w.writerow([iid, it["name_zh"], it["version"], it["gold_total"],
                            json.dumps(it["stats"], ensure_ascii=False, sort_keys=True), it["plaintext"]])
    log("参考数据：%d 个英雄技能（%s），%d 件成装" % (len(champs) - len(missing), static.latest, len(item_ids)))
    return {"champs": champs, "missing": missing}
