"""第三遍：summary.json（+ strategy.json）→ 单文件 HTML 仪表板。

HTML 里只有汇总后的数据；小地图图片以 data URI 内嵌。所有来自数据的文字都经过转义。
输出两个文件：
  dashboard.html           完整网页，双击即可打开
  dashboard_fragment.html  同样内容但不带 <html>/<head>/<body> 外壳（发布为 Artifact 时用）
"""

import base64
import html
import json
import os

from . import geometry as G

def E(v):
    """转义任何值（数字、列表、None 也可以）。"""
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        v = "、".join(str(x) for x in v)
    return html.escape(str(v))


def safe_url(u):
    from urllib.parse import urlparse
    try:
        return u if urlparse(str(u)).scheme in ("http", "https") else None
    except ValueError:
        return None

RATING = {"green": ("good", "▲", "好于基准"), "yellow": ("warn", "●", "持平"),
          "red": ("bad", "▼", "差于基准"), "grey": ("none", "○", "无对比基准")}
POS_ZH = {"TOP": "上路", "JUNGLE": "打野", "MIDDLE": "中路", "BOTTOM": "下路", "UTILITY": "辅助", "": "未知"}


# ------------------------------------------------------------------ 小工具
def pct(x, d=0):
    return "—" if x is None else "%.*f%%" % (d, 100 * x)


def num(x, d=1, sign=False):
    if x is None:
        return "—"
    s = "%+.*f" % (d, x) if sign else "%.*f" % (d, x)
    return s.replace("-", "−")


def mmss(minutes):
    if minutes is None:
        return "—"
    s = int(round(minutes * 60))
    return "%d:%02d" % (s // 60, s % 60)


def chip(rating, text=None):
    cls, mark, label = RATING.get(rating or "grey", RATING["grey"])
    return '<span class="chip %s" title="%s"><i aria-hidden="true">%s</i>%s</span>' % (
        cls, label, mark, E(text if text is not None else label))


def cell(value_html, rating):
    cls = RATING.get(rating or "grey", RATING["grey"])[0]
    mark = RATING.get(rating or "grey", RATING["grey"])[1]
    return '<td class="num r-%s" title="%s">%s <i class="mk" aria-hidden="true">%s</i></td>' % (
        cls, RATING[rating or "grey"][2], value_html, mark)


def small_badge(flag):
    return ' <span class="badge">样本太少</span>' if flag else ""


def inferred_badge():
    return ' <span class="badge inf">推测</span>'


def src_note(text):
    return '<p class="src">来源：%s</p>' % E(text or "未核实")


def table(headers, rows, cls=""):
    th = "".join("<th>%s</th>" % h for h in headers)
    body = "".join("<tr>%s</tr>" % "".join(r) if isinstance(r, (list, tuple)) else r for r in rows)
    return '<div class="tbl %s"><table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>' % (cls, th, body)


def td(x, cls=""):
    return '<td class="%s">%s</td>' % (cls, x) if cls else "<td>%s</td>" % x


# ------------------------------------------------------------------ 小地图
class MiniMap:
    def __init__(self, png_path):
        self.href = None
        if png_path and os.path.exists(png_path):
            with open(png_path, "rb") as fh:
                self.href = "data:image/png;base64," + base64.b64encode(fh.read()).decode("ascii")

    def defs(self):
        """整页只内嵌一次小地图图片，各张地图用 <use> 引用。"""
        if not self.href:
            return ""
        return ('<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>'
                '<image id="map11" href="%s" x="0" y="0" width="512" height="512" preserveAspectRatio="none"/>'
                '</defs></svg>' % self.href)

    def svg(self, layers, title, legend, zones=False):
        """layers: 已生成的 SVG 片段列表（坐标为 512×512 像素）。"""
        bg = '<use href="#map11"/>' if self.href else self._schematic()
        z = self._zones() if zones else ""
        leg = "".join('<span class="lg"><svg width="14" height="14" aria-hidden="true">%s</svg>%s</span>' % (sym, E(lbl))
                      for sym, lbl in legend)
        return ('<figure class="map"><svg viewBox="0 0 512 512" role="img" aria-label="%s">%s%s%s</svg>'
                '<figcaption><b>%s</b><span class="legend">%s</span></figcaption></figure>' % (
                    E(title), bg, z, "".join(layers), E(title), leg))

    @staticmethod
    def _schematic():
        # 没有 map11.png 时的示意底图（只画车道和河道）
        parts = ['<rect width="512" height="512" fill="var(--map-bg)"/>']
        for lane in G.LANES.values():
            pts = " ".join("%.1f,%.1f" % (u * 512, (1 - v) * 512) for u, v in lane)
            parts.append('<polyline points="%s" fill="none" stroke="var(--map-lane)" stroke-width="22"/>' % pts)
        parts.append('<line x1="0" y1="0" x2="512" y2="512" stroke="var(--map-river)" stroke-width="40" opacity=".5"/>')
        return "".join(parts)

    @staticmethod
    def _zones():
        """把 geometry.zone() 实际使用的边界画出来：基地多边形、河道带、上下半分界、车道带、坑、营地。"""
        def P(u, v):
            return "%.1f,%.1f" % (u * 512, (1 - v) * 512)
        out = []
        # 基地：u ≤ 0.30、v ≤ 0.30、u+v ≤ 0.52（红方对称）
        out.append('<polygon points="%s" class="zb"/>' % " ".join(P(u, v) for u, v in
                   [(0, 0), (0.30, 0), (0.30, 0.22), (0.22, 0.30), (0, 0.30)]))
        out.append('<polygon points="%s" class="zb"/>' % " ".join(P(1 - u, 1 - v) for u, v in
                   [(0, 0), (0.30, 0), (0.30, 0.22), (0.22, 0.30), (0, 0.30)]))
        r = G.RIVER_HALF_WIDTH
        for off in (-r, r):
            out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" class="zl"/>' % (
                0, (1 - (1 + off)) * 512, (1 + off) * 512, 512))
        out.append('<line x1="0" y1="512" x2="512" y2="0" class="zl dim"/>')
        for name, lane in G.LANES.items():
            pts = " ".join(P(u, v) for u, v in lane)
            out.append('<polyline points="%s" class="zlane" stroke-width="%.1f"/>' % (pts, 2 * G.LANE_HALF_WIDTH[name] * 512))
        for code, (x, y) in G.PIT_CENTERS.items():
            px, py = G.to_px(x, y)
            out.append('<circle cx="%.1f" cy="%.1f" r="%.1f" class="zp"><title>%s</title></circle>' % (
                px, py, G.PIT_RADIUS[code] * 512, E(G.ZONE_NAMES[code])))
        for team, name, x, y in G.CAMPS:
            px, py = G.to_px(x, y)
            out.append('<circle cx="%.1f" cy="%.1f" r="%.1f" class="zc"><title>%s%s</title></circle>' % (
                px, py, G.CAMP_RADIUS * 512, {100: "蓝方", 200: "红方", 0: ""}[team], E(name)))
        return "".join(out)


def dot(x, y, cls, title, r=5.5, shape="circle"):
    px, py = G.to_px(x, y)
    if shape == "x":
        d = r * 0.9
        return ('<g class="pt %s"><path d="M%.1f,%.1f l%.1f,%.1f m0,%.1f l%.1f,%.1f" /><title>%s</title></g>' % (
            cls, px - d, py - d, 2 * d, 2 * d, -2 * d, -2 * d, 2 * d, E(title)))
    if shape == "tri":
        return ('<polygon class="pt %s" points="%.1f,%.1f %.1f,%.1f %.1f,%.1f"><title>%s</title></polygon>' % (
            cls, px, py - r * 1.2, px - r * 1.1, py + r * 0.8, px + r * 1.1, py + r * 0.8, E(title)))
    return '<circle class="pt %s" cx="%.1f" cy="%.1f" r="%.1f"><title>%s</title></circle>' % (cls, px, py, r, E(title))


def legend_sym(cls, shape="circle"):
    if shape == "tri":
        return '<polygon class="pt %s" points="7,1.5 1.5,12 12.5,12"/>' % cls
    if shape == "x":
        return '<g class="pt %s"><path d="M2,2 l10,10 m0,-10 l-10,10"/></g>' % cls
    return '<circle class="pt %s" cx="7" cy="7" r="5"/>' % cls


# ------------------------------------------------------------------ 主函数
def render(summary, strategy, map_png, out_dir, calib=None, notes=None):
    S = summary
    st = strategy or {}
    mm = MiniMap(map_png)
    tabs = [
        ("overview", "概览", _overview(S, st)),
        ("pool", "英雄池", _pool(S)),
        ("jungle" if S.get("jungle") else "lane", "打野" if S.get("jungle") else "对线", _lane_jungle(S, mm)),
        ("deaths", "死亡分布", _deaths(S, mm)),
        ("plan", "应对方案", _plan(S, st) + _ref_section(S, notes)),
        ("sources", "数据来源", _sources(S, st, mm, calib, notes)),
    ]
    m = S["meta"]
    r = S.get("rank") or {}
    rank_txt = ("%s %s · %s LP · 本赛季 %s 胜 %s 负" % (r.get("tier", ""), r.get("rank", ""), r.get("leaguePoints", "—"),
                                                 r.get("wins", "—"), r.get("losses", "—"))) if r else "单双排段位：无记录"
    head = (
        '<header class="top"><div class="wrap">'
        '<p class="eyebrow">对手分析 · 召唤师峡谷单双排</p>'
        '<h1>%s</h1>'
        '<p class="sub">%s · 主要位置 <b>%s</b> · 纳入 %d / %d 场（有时间线 %d 场）· 版本 %s · 数据下载于 %s%s</p>'
        '</div></header>' % (
            E(m.get("riot_id", "?")), E(rank_txt), E(POS_ZH.get(S.get("main_position", ""), S.get("main_position", ""))),
            m["n_included"], m["n_ids"], m["n_timeline"], E("/".join(m.get("patches", [])) or "?"),
            E(m.get("downloaded_date", "?")), small_badge(m["n_included"] < 10)))
    if m.get("synthetic"):
        head = '<div class="synthetic">测试用合成数据，不是真实对局，不能用来做判断</div>' + head
    nav = '<nav class="tabs" role="tablist"><div class="wrap">%s</div></nav>' % "".join(
        '<button role="tab" id="tab-%s" aria-controls="p-%s" aria-selected="%s">%s</button>' % (
            k, k, "true" if i == 0 else "false", E(t)) for i, (k, t, _) in enumerate(tabs))
    panels = "".join('<section class="panel wrap" role="tabpanel" id="p-%s" aria-labelledby="tab-%s"%s>%s</section>' % (
        k, k, "" if i == 0 else " hidden", body) for i, (k, _, body) in enumerate(tabs))
    head_part = (
        '<title>%s 对手档案</title>\n<meta name="claude:viewport-fit-sides" content="cover">\n'
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600&'
        'family=JetBrains+Mono:wght@400&family=Noto+Sans+SC:wght@400;500;700&display=swap">\n'
        '<style>%s</style>\n' % (E(m.get("game_name") or "对手"), CSS))
    body_part = '%s%s%s<main>%s</main>\n<script>%s</script>\n' % (mm.defs(), head, nav, panels, JS)
    fragment = head_part + body_part
    full = ('<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            '%s</head><body>%s</body></html>\n' % (head_part, body_part))
    p_full = os.path.join(out_dir, "dashboard.html")
    p_frag = os.path.join(out_dir, "dashboard_fragment.html")
    with open(p_full, "w", encoding="utf-8") as fh:
        fh.write(full)
    with open(p_frag, "w", encoding="utf-8") as fh:
        fh.write(fragment)
    return p_full, p_frag


# ------------------------------------------------------------------ 页签：概览
def _overview(S, st):
    out = []
    concl = st.get("conclusion")
    if concl:
        out.append('<div class="lead">%s</div>' % "".join("<p>%s</p>" % E(s) for s in concl))
    else:
        out.append('<div class="lead pending"><p>结论待写：跑完分析后，根据下面的数据写 3–5 句结论（strategy.json 的 conclusion）。</p></div>')
    b = S["baseline"]
    rows = []
    for x in b["rows"]:
        fmt = (lambda v: pct(v)) if x["metric"] in ("dmg_share", "kp") else (
            (lambda v: num(v, 0, sign=True)) if x["metric"].startswith(("gold", "xp")) else (
                (lambda v: num(v, 1, sign=True)) if x["metric"].startswith("cs_diff") else (lambda v: num(v, 2))))
        rows.append([td(E(x["label"])), cell(fmt(x["his"]), x["rating"]),
                     td(fmt(x["base"]) if x["base"] is not None else "—", "num"), td(str(x["n_base"]), "num")])
    out.append('<h2>关键指标 vs 同场对位%s</h2>' % small_badge(b["small"]))
    out.append('<p class="note">%s</p>' % E(b["rule"]))
    out.append(table(["指标", "他", "基准（对面同位置平均）", "基准样本"], rows))
    out.append(src_note(b["src"]))

    pos = S["positions"]
    out.append("<h2>位置分布</h2>")
    out.append(table(["位置", "场次", "胜场", "占比"], [[td(E(POS_ZH.get(p["position"], p["position"]))), td(p["games"], "num"),
                                                 td(p["wins"], "num"), td(pct(p["share"]), "num")] for p in pos]))

    d = S["duration"]
    out.append("<h2>按时长的胜率%s</h2>" % small_badge(d["small"]))
    out.append(table(["时长", "场次", "胜场", "胜率"], [[td(E(x["bucket"])), td(x["games"], "num"), td(x["wins"], "num"),
                                                 td(pct(x["wr"]) + (small_badge(True) if x["small"] else ""), "num")]
                                                for x in d["rows"]]))
    out.append('<p class="verdict">%s。赢的对局时长中位 %s 分钟，输的 %s 分钟。<span class="note">规则：%s</span></p>' % (
        E(d["verdict"]), num(d["median_win_min"]), num(d["median_loss_min"]), E(d["rule"])))
    out.append(src_note(d["src"]))

    t = S["tilt"]
    rows = []
    for blk in (t["all"], t["session"]):
        for key, zh in (("after_loss", "输了之后"), ("after_win", "赢了之后")):
            x = blk[key]
            rows.append([td(E(blk["label"])), td(zh), td(x["n"], "num"),
                         td(pct(x["wr"]) + small_badge(x["small"]), "num"), td(num(x["avg_deaths"]), "num")])
    a2 = t["after_2_losses"]
    rows.append([td("连输两场之后"), td("—"), td(a2["n"], "num"), td(pct(a2["wr"]) + small_badge(a2["n"] < 10), "num"),
                 td(num(a2["avg_deaths"]), "num")])
    out.append("<h2>会不会上头%s</h2>" % small_badge(t["small"]))
    out.append(table(["范围", "前一场", "下一场数", "下一场胜率", "下一场场均死亡"], rows))
    out.append('<p class="verdict">%s。全部对局场均死亡 %s。<span class="note">规则：%s</span></p>' % (
        E(t["verdict"]), num(t["overall_avg_deaths"]), E(t["rule"])))
    out.append(src_note(t["src"]))

    du = S["duo"]
    out.append("<h2>常一起排的队友</h2>")
    if du["rows"]:
        out.append(table(["玩家", "一起场次", "一起胜率", "他不带此人时胜率", "位置", "常用英雄"],
                         [[td(E(x["riot_id"])), td(x["games"], "num"), td(pct(x["wr"]) + small_badge(x["small"]), "num"),
                           td("%s（%d 场）" % (pct(x["wr_without"]), x["n_without"]), "num"),
                           td(E("、".join("%s %d" % (POS_ZH.get(k, k), v) for k, v in x["positions"].items()))),
                           td(E("、".join("%s %d" % kv for kv in x["champs"].items())))] for x in du["rows"]]))
    else:
        out.append("<p>没有在 3 场以上同队出现的队友（数据显示他基本单排）。</p>")
    out.append('<p class="note">%s</p>' % E(du["rule"]) + src_note(du["src"]))

    out.append("<h2>逐场记录</h2>")
    out.append(table(["日期（UTC）", "比赛 ID", "英雄", "位置", "阵营", "结果", "时长", "K/D/A", "对位", "15 分经济差"],
                     [[td(E((g["game_start_utc"] or "")[:16].replace("T", " ")), "mono"), td(E(g["match_id"]), "mono"),
                       td(E(g["champ_zh"])), td(E(POS_ZH.get(g["position"], g["position"]))), td(E(g["side"])),
                       td('<span class="wl %s">%s</span>' % ("w" if g["win"] else "l", "胜" if g["win"] else "负")),
                       td(num(g["duration_min"], 0) + " 分", "num"),
                       td("%d/%d/%d" % (g["kills"], g["deaths"], g["assists"]), "num"), td(E(g.get("opp_champ_zh") or "—")),
                       td(num(g.get("gold_diff_15"), 0, sign=True), "num")] for g in S["games_list"]], "dense"))
    return "".join(out)


# ------------------------------------------------------------------ 页签：英雄池
def _pool(S):
    P = S["champ_pool"]
    rows = []
    for c in P["rows"]:
        rt = c["ratings"]
        rows.append([td("<b>%s</b>%s" % (E(c["champ_zh"]), small_badge(c["small"]))), td(c["games"], "num"),
                     td("%d−%d" % (c["wins"], c["losses"]), "num"), td(pct(c["wr"]), "num"),
                     cell(num(c["kda"], 2), rt["kda"]),
                     td("%.1f / %.1f / %.1f" % (c["avg_k"], c["avg_d"], c["avg_a"]), "num"),
                     cell(num(c["cs_per_min"]), rt["cs_per_min"]), cell(pct(c["dmg_share"]), rt["dmg_share"]),
                     cell(pct(c["kp"]), rt["kp"]), cell(num(c["vision"], 0), rt["vision_per_min"])])
    out = ["<h2>英雄池（%d 场）</h2>" % P["n"],
           '<p class="note">颜色：和这些对局里对面同位置玩家的平均值比（±10%% 内为持平）；场次少于 10 的英雄标“样本太少”。'
           'KDA =（击杀+助攻）/ 死亡（死亡为 0 按 1 算），按该英雄所有场次合计；每分钟补刀 =（小兵+野怪）/ 时长；'
           '伤害占比 = 对英雄伤害 / 全队对英雄伤害；参团率 =（击杀+助攻）/ 全队击杀；视野得分为场均（颜色按每分钟视野得分比较）。</p>',
           table(["英雄", "场次", "胜−负", "胜率", "KDA", "场均 K/D/A", "分均补刀", "伤害占比", "参团率", "视野得分"], rows),
           src_note(P["src"])]
    B = S["ban"]
    out.append("<h2>禁用排序</h2>")
    out.append('<p class="note">%s</p>' % E(B["rule"]))
    out.append(table(["英雄", "场次", "胜率", "净胜场", "平滑胜率", "计算过程"],
                     [[td(E(r["champ_zh"])), td(r["games"], "num"), td(pct(r["wr"]), "num"), td(num(r["net_wins"], 0, sign=True), "num"),
                       td(pct(r["smoothed_wr"]), "num"), td(E(r["calc"]), "calc")] for r in B["rows"]]))
    rec = B["recommend"]
    out.append('<p class="verdict">按这个规则建议禁：%s%s%s</p>' % (
        "、".join("<b>%s</b>" % E(r["champ_zh"]) for r in rec) if rec else "没有玩过 ≥3 场的英雄，按场次禁意义不大",
        small_badge(B.get("small")), ('<span class="note">%s</span>' % E(B["fallback"])) if B.get("fallback") else ""))
    out.append(src_note(B["src"]))
    return "".join(out)


# ------------------------------------------------------------------ 页签：对线 / 打野
def _lane_jungle(S, mm):
    out = []
    L = S["lane"]
    rows = []
    for pos, r in L["by_position"].items():
        rows.append([td(E(POS_ZH.get(pos, pos))), td(r["n"], "num") if not r["small"] else td("%d%s" % (r["n"], small_badge(True)), "num")]
                    + [cell(num(r[k]["mean"], 0 if "gold" in k or "xp" in k else 1, sign=True), r[k]["rating"])
                       for k in ("gold_diff_10", "xp_diff_10", "cs_diff_10", "gold_diff_15", "xp_diff_15", "cs_diff_15")]
                    + [td(pct(r["gold_diff_15"]["ahead_share"]), "num"),
                       cell(num(r["first_item"]["median"]) + " 分", r["first_item"]["rating"])])
    out.append("<h2>和对位的差距（10 / 15 分钟）%s</h2>" % small_badge(any(r["small"] for r in L["by_position"].values())))
    out.append('<p class="note">差值 = 他 − 对面同位置玩家，取第 10、15 分钟那一帧的累计金币（totalGold）、经验、补刀（小兵+野怪）。'
               '首件成装：第一件“成装”购买的时间（已扣掉撤销的购买），颜色与对位的首件成装时间比。</p>')
    out.append(table(["位置", "场次", "10 分经济差", "10 分经验差", "10 分补刀差", "15 分经济差", "15 分经验差", "15 分补刀差",
                      "15 分领先局占比", "首件成装（中位）"], rows))
    fi_items = []
    for pos, r in L["by_position"].items():
        if r["first_item"]["items"]:
            fi_items.append("%s：%s" % (POS_ZH.get(pos, pos), "、".join("%s %d 次" % kv for kv in r["first_item"]["items"].items())))
    if fi_items:
        out.append('<p class="note">首件成装：%s</p>' % E("；".join(fi_items)))
    for pos, r in L["by_position"].items():
        out.append('<p class="note">%s 首件成装对比：%s；对位中位数 %s 分钟。</p>' % (
            E(POS_ZH.get(pos, pos)), E(r["first_item"]["note"]), num(r["first_item"]["opp_median"])))
    out.append(src_note(L["src"]))

    es = L["early_summary"]
    out.append("<h2>前 10 分钟的击杀和死亡%s</h2>" % small_badge(es["games"] < 10))
    out.append('<p class="verdict">%d 场里：击杀 %d、助攻 %d、死亡 %d（其中被抓 %d、单挑被杀 %d、被塔/兵处决 %d），场均死亡 %s。'
               '<span class="note">%s</span></p>' % (
                   es["games"], es["kills"], es["assists"], es["deaths"], es["ganked_deaths"], es["solo_deaths"],
                   es["executed"], num(es["deaths_per_game"], 2), E(es["rule"])))
    ev = L["early_events"]
    out.append(table(["比赛 ID", "英雄", "时间", "类型", "地点", "敌方参与人数", "击杀者 → 被杀者"],
                     [[td(E(e["match_id"]), "mono"), td(E(e["champ_zh"])), td(e["t"], "num"),
                       td('<span class="ev %s">%s</span>%s' % ({"死亡": "d", "击杀": "k", "助攻": "a"}[e["type"]], e["type"],
                                                               " · 被抓" if e["ganked"] else "")),
                       td(E(e["zone"])), td(e["n_attackers"], "num"),
                       td(E("%s → %s" % (e["killer"] or "防御塔/小兵", e["victim"])))] for e in ev], "dense scroll"))
    out.append(src_note(L["src"]))

    J = S.get("jungle")
    if J:
        out.append(_jungle_section(J, mm))
    return "".join(out)


ROUTE_CLS = ["s1", "s2", "s3"]


def _jungle_section(J, mm):
    out = ['<h2>打野：开野方向%s%s</h2>' % (inferred_badge(), small_badge(J["small"]))]
    out.append('<p class="note">依据：%s</p>' % E(J["basis"]))
    out.append(table(["开局方向", "场次", "占比", "阵营·起手 buff", "第一次去线上（中位）", "第一次去的线"],
                     [[td(E(s["start"])), td(s["games"], "num"), td(pct(s["share"]), "num"),
                       td(E("、".join("%s %d" % kv for kv in s["by_side"].items()))), td(mmss(s["median_first_gank_min"]), "num"),
                       td(E("、".join("%s %d" % kv for kv in s["first_gank_lanes"].items())))] for s in J["starts"]]))
    out.append(table(["开野方式（开局 → 3:00 位置）", "场次", "占比", "胜场"],
                     [[td(E(p["pattern"])), td(p["games"], "num"), td(pct(p["share"]), "num"), td(p["wins"], "num")]
                      for p in J["patterns"]]))
    # 地图：每场 1:00→2:00→3:00→4:00 的位置，按开局方向着色（最多 3 类，其余合并为“其它”）
    starts = [s["start"] for s in J["starts"]]
    cls_of = {s: ROUTE_CLS[i] if i < 3 else "s0" for i, s in enumerate(starts)}
    layers = []
    for o in J["openings"]:
        pts = o["points"]
        if len(pts) >= 2:
            d = " ".join("%.1f,%.1f" % G.to_px(p["x"], p["y"]) for p in pts)
            layers.append('<polyline points="%s" class="route %s"/>' % (d, cls_of.get(o["start_label"], "s0")))
        for p in pts:
            layers.append(dot(p["x"], p["y"], cls_of.get(o["start_label"], "s0"),
                              "%s %s %d:00 %s %s" % (o["match_id"], o["champ_zh"], p["minute"], p["zone_rel"], p["camp"]),
                              r=4.5 if p["minute"] > 1 else 6))
    legend = [(legend_sym(cls_of[s]), s) for s in starts[:3]]
    if len(starts) > 3:
        legend.append((legend_sym("s0"), "其它"))
    out.append('<div class="maps">')
    out.append(mm.svg(layers, "开野路线：每场 1:00 → 4:00 的整分钟位置（大点 = 1:00）", legend))
    # 抓人地图
    glayers = []
    for gk in J["ganks"]:
        for e in gk["episodes"]:
            if e["t_min"] >= 10:
                continue
            # 推测的位置没有精确坐标，用依据里的坐标
            xy = _xy_from_basis(e["basis"])
            if not xy:
                continue
            if e["result"].startswith("抓到"):
                glayers.append(dot(xy[0], xy[1], "ok", "%s %s %s %s" % (gk["match_id"], e["t_label"], G.ZONE_NAMES[e["lane"]], e["result"])))
            elif e["result"] == "他阵亡":
                glayers.append(dot(xy[0], xy[1], "dead", "%s %s %s 他阵亡" % (gk["match_id"], e["t_label"], G.ZONE_NAMES[e["lane"]]), shape="x"))
            else:
                glayers.append(dot(xy[0], xy[1], "maybe", "%s %s %s 没有击杀（推测）" % (gk["match_id"], e["t_label"], G.ZONE_NAMES[e["lane"]]), shape="tri"))
    out.append(mm.svg(glayers, "前 10 分钟去线上的位置", [(legend_sym("ok"), "抓到了（击杀/助攻）"),
                                                    (legend_sym("dead", "x"), "他阵亡"),
                                                    (legend_sym("maybe", "tri"), "整分钟位置在线上、没有击杀（推测）")]))
    out.append("</div>")

    out.append("<h2>第一次抓人%s%s</h2>" % (inferred_badge(), small_badge(J["small"])))
    out.append('<p class="verdict">中位时间 %s；去向 %s；有击杀/助攻的比例 %s。</p>' % (
        mmss(J["first_gank_median"]), E("，".join("%s %d 场" % tuple(x) for x in J["first_gank_lanes"])), pct(J["first_gank_success"])))
    out.append(table(["比赛 ID", "英雄", "阵营", "时间", "哪条线", "结果", "依据"],
                     [[td(E(f["match_id"]), "mono"), td(E(f["champ_zh"])), td(E(f["side"])), td(E(f["t"]), "num"),
                       td(E(f["lane"])), td(E(f["result"]) + (inferred_badge() if f["inferred"] else "")), td(E(f["basis"]), "calc")]
                      for f in J["first_gank"]], "dense"))
    out.append("<h2>前 10 分钟去各条线%s%s</h2>" % (inferred_badge(), small_badge(J["small"])))
    out.append(table(["线", "次数", "场均", "去过的场次", "其中有击杀/助攻", "其中有击杀事件为证", "第一次去（中位 / 最早）"],
                     [[td(E(l["lane_zh"])), td(l["visits_pre10"], "num"), td(num(l["per_game"], 2), "num"),
                       td("%d / %d" % (l["games_visited"], J["n"]), "num"), td(l["success"], "num"), td(l["direct_events"], "num"),
                       td("%s / %s" % (mmss(l["first_visit_median"]), mmss(l["first_visit_min"])), "num")] for l in J["lanes_pre10"]]))
    inv = J["invade"]
    out.append("<h2>进对面野区%s%s</h2>" % (inferred_badge(), small_badge(J["small"])))
    out.append('<p class="verdict">%d / %d 场在前 10 分钟有整分钟位置落在对面野区；场均 %s 个整分钟点（对面打野进他野区：场均 %s）。'
               '全场场均吃对面野怪 %s（对局详情 totalEnemyJungleMinionsKilled）；场均河蟹 %s。<span class="note">%s</span></p>' % (
                   inv["games_with_invade_pre10"], inv["n"], num(inv["avg_minutes_pre10"]), num(inv["opp_avg_minutes_pre10"]),
                   num(inv["avg_enemy_jg_cs_game"]), num(inv["avg_scuttle"]), E(inv["basis"])))
    O = J["objectives"]
    out.append("<h2>野区资源%s</h2>" % small_badge(J["small"]))
    out.append(table(["资源", "他方拿 / 总数", "他方占比", "他方拿时他参与", "他方首次拿（中位）", "全场首次被拿（中位）", "他方拿到过的场次"],
                     [[td(E(o["label"])), td("%d / %d" % (o["his_team"], o["total"]), "num"), td(pct(o["his_team_share"]), "num"),
                       td(pct(o["him_involved_of_team"]), "num"), td(mmss(o["first_take_median_min"]), "num"),
                       td(mmss(o["first_spawn_take_median_min"]), "num"), td(o["games_with_take"], "num")] for o in O["rows"]]))
    fo = "，".join("%s %d 场" % ({"DRAGON": "小龙", "HORDE": "虚空巢虫", "RIFTHERALD": "峡谷先锋"}.get(k, k), v)
                  for k, v in O["first_objective_of_his_team"])
    hp = J["half_preference"]
    sg = hp["signals"]
    out.append('<p class="verdict">他方拿的第一个资源：%s。半区偏好%s：<b>%s</b>（3–14 分钟整分钟位置在上半区 %s，共 %d 个点；'
               '第一次抓人 上路 %d / 中路 %d / 下路 %d）。<span class="note">%s</span></p>' % (
                   E(fo), inferred_badge() + small_badge(hp.get("small")), E(hp["verdict"]), pct(sg["frames_top_share"]),
                   sg["frames_n"], sg["first_gank_top"],
                   sg["first_gank_mid"], sg["first_gank_bot"], E(hp["basis"])))
    out.append(src_note(O["src"]))
    out.append("<h2>逐场开野记录%s</h2>" % inferred_badge())
    out.append(table(["比赛 ID", "英雄", "阵营·起手 buff", "结果", "开野方式", "3:00 时约清了几组", "依据"],
                     [[td(E(o["match_id"]), "mono"), td(E(o["champ_zh"])), td(E("%s·%s" % (o["side"], o.get("start_buff") or "?"))),
                       td('<span class="wl %s">%s</span>' % ("w" if o["win"] else "l", "胜" if o["win"] else "负")),
                       td(E(o["pattern"])), td(num(o["camps_by_3_est"]), "num"), td(E(o["basis"]), "calc")] for o in J["openings"]],
                     "dense"))
    out.append(src_note(J["src"]))
    return "".join(out)


def _xy_from_basis(basis):
    import re
    m = re.search(r"\((-?\d+),(-?\d+)\)", basis or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


# ------------------------------------------------------------------ 页签：死亡分布
def _deaths(S, mm):
    D = S["deaths"]
    out = ["<h2>他的所有死亡位置（%d 场，共 %d 次）%s</h2>" % (D["n_games"], D["total"], small_badge(D["n_games"] < 10))]
    layers = []
    for r in sorted(D["rows"], key=lambda r: -r["t_min"]):
        cls = "t1" if r["t_min"] < 10 else ("t2" if r["t_min"] < 20 else "t3")
        layers.append(dot(r["x"], r["y"], cls, "%s %s %s %s，敌方 %d 人%s" % (
            r["match_id"], r["t"], r["champ_zh"], r["zone"], r["n_attackers"], "（处决）" if r["executed"] else ""),
            r=6 if r["t_min"] < 10 else 4.5))
    out.append('<div class="maps">')
    out.append(mm.svg(layers, "死亡位置（颜色 = 时间段）", [(legend_sym("t1"), "前 10 分钟"), (legend_sym("t2"), "10–20 分钟"),
                                                    (legend_sym("t3"), "20 分钟以后")]))
    side = ['<div class="side">']
    side.append("<h3>前 10 分钟：死在哪里</h3>")
    side.append(table(["地点", "次数"], [[td(E(z)), td(c, "num")] for z, c in D["early_zone_counts"]]) if D["early_zone_counts"]
                else "<p>前 10 分钟没有死亡。</p>")
    side.append("<h3>前 10 分钟：什么时候死</h3>")
    side.append(table(["时间段", "次数"], [[td(b["bin"]), td(b["deaths"], "num")] for b in D["early_time_bins"]]))
    if D["early_top_zone"]:
        side.append('<p class="verdict">前 10 分钟死得最多：<b>%s</b>（%d 次）；最集中的时间段：<b>%s</b>（%d 次）。</p>' % (
            E(D["early_top_zone"][0]), D["early_top_zone"][1], E(D["early_top_bin"]["bin"]), D["early_top_bin"]["deaths"]))
    side.append("<h3>全场：死在哪里</h3>")
    side.append(table(["地点", "次数"], [[td(E(z)), td(c, "num")] for z, c in D["all_zone_counts"]]))
    side.append("</div>")
    out.append("".join(side))
    out.append("</div>")
    out.append("<h3>逐次记录</h3>")
    out.append(table(["比赛 ID", "英雄", "时间", "地点", "敌方参与人数", "击杀者", "这场结果"],
                     [[td(E(r["match_id"]), "mono"), td(E(r["champ_zh"])), td(r["t"], "num"), td(E(r["zone"])),
                       td(r["n_attackers"], "num"), td(E(r["killer"] or "防御塔/小兵")),
                       td("胜" if r["win"] else "负")] for r in sorted(D["rows"], key=lambda r: (r["match_id"], r["t_min"]))],
                     "dense scroll"))
    out.append('<p class="note">地点为按坐标的近似分区（见数据来源页的核对图）。</p>' + src_note(D["src"]))

    W = S["weakness"]
    out.append("<h2>他输的对局有什么共同点%s</h2>" % small_badge(W["small"]))
    out.append(table(["指标", "赢的 %d 场" % W["n_wins"], "输的 %d 场" % W["n_losses"]],
                     [[td(E(r["feature"])), td(_fmt_feat(r["feature"], r["wins"]), "num"), td(_fmt_feat(r["feature"], r["losses"]), "num")]
                      for r in W["table"]]))
    out.append('<p class="verdict">输的类型：%s。<span class="note">%s</span></p>' % (
        E("；".join("%s %d 场" % tuple(x) for x in W["loss_type_count"]) or "无"), E(W["rule"])))
    out.append(table(["比赛 ID", "他的英雄", "对位英雄", "时长", "15 分全队经济差", "前 10 分钟死亡（被抓）", "类型"],
                     [[td(E(t["match_id"]), "mono"), td(E(t["champ_zh"])), td(E(t["opp_champ_zh"] or "—")),
                       td(num(t["duration_min"], 0) + " 分", "num"), td(num(t["team_gold_diff_15"], 0, sign=True), "num"),
                       td("%d（%d）" % (t["early_deaths"], t["early_ganked"]), "num"), td(E("；".join(t["types"])))]
                      for t in W["loss_types"]], "dense"))
    out.append("<h3>对面同位置英雄的战绩（他的胜−负）</h3>")
    out.append(table(["对位英雄", "他赢", "他输"], [[td(E(r["champ_zh"])), td(r["his_wins"], "num"), td(r["his_losses"], "num")]
                                               for r in W["opp_champs"]], "dense"))
    if W["enemy_champs_in_losses"]:
        out.append("<h3>在他输的对局里出现 ≥2 次的敌方英雄</h3>")
        out.append(table(["敌方英雄", "他赢", "他输"], [[td(E(r["champ_zh"])), td(r["his_wins"], "num"), td(r["his_losses"], "num")]
                                                   for r in W["enemy_champs_in_losses"]], "dense"))
    out.append(src_note(W["src"]))
    return "".join(out)


def _fmt_feat(name, v):
    if v is None:
        return "—"
    if name in ("参团率", "伤害占比"):
        return pct(v)
    if "经济差" in name:
        return num(v, 0, sign=True)
    return num(v, 2 if v < 10 else 1)


# ------------------------------------------------------------------ 页签：应对方案
def _plan(S, st):
    out = []
    if not st:
        out.append('<div class="lead pending"><p>应对方案待写：看完前几页的数据后写进 strategy.json，再重新生成本页。</p></div>')
    sc = st.get("scenarios") or []
    if st.get("ban"):
        out.append("<h2>禁用</h2><p>%s</p>" % E(st["ban"]))
    if sc:
        out.append("<h2>按他最可能拿的英雄</h2>")
        out.append(table(["他拿", "我们禁什么", "我选什么", "对线怎么打", "他的强势期", "我的获胜条件"],
                         [[td("<b>%s</b>" % E(x.get("his_champ", ""))), td(E(x.get("we_ban", ""))), td(E(x.get("i_pick", ""))),
                           td(E(x.get("lane_plan", ""))), td(E(x.get("his_power", ""))), td(E(x.get("win_condition", "")))]
                          for x in sc], "plan"))
    op = st.get("opening_table") or []
    if op:
        out.append("<h2>按他的开野方向%s</h2>" % inferred_badge())
        out.append(table(["他的开野方式", "出现场次", "几分几秒在哪里插眼", "哪条线什么时候小心", "我们打野可以在哪里反蹲 / 反野"],
                         [[td("<b>%s</b>" % E(x.get("opening", ""))), td(E(x.get("games", "")), "num"), td(E(x.get("wards", ""))),
                           td(E(x.get("careful", ""))), td(E(x.get("counter_jungle", "")))] for x in op], "plan"))
    for key, title in (("lane_plan", "对线细节"), ("pick", "选人"), ("rhythm", "节奏"), ("avoid", "需要避免的事")):
        v = st.get(key)
        if not v:
            continue
        out.append("<h2>%s</h2>" % title)
        if isinstance(v, dict):
            out.append("<dl class='kv'>%s</dl>" % "".join("<dt>%s</dt><dd>%s</dd>" % (E(k), E(x)) for k, x in v.items()))
        elif isinstance(v, list):
            out.append("<ul>%s</ul>" % "".join("<li>%s</li>" % E(x) for x in v))
        else:
            out.append("<p>%s</p>" % E(v))
    ms = st.get("matchup_sources") or []
    if ms:
        out.append("<h3>对位数据的来源</h3>")
        out.append(table(["内容", "版本", "段位范围", "样本量", "说明"],
                         [[td(E(x.get("what", ""))), td(E(x.get("patch", "未核实"))), td(E(x.get("tier", "未核实"))),
                           td(E(str(x.get("sample", "未核实")))), td(E(x.get("note", "")))] for x in ms]))
    if st.get("fact_vs_inference"):
        out.append("<h3>哪些是数据、哪些是推测</h3><ul>%s</ul>" % "".join("<li>%s</li>" % E(x) for x in st["fact_vs_inference"]))
    return "".join(out)


def _ref_section(S, notes):
    """英雄技能（Data Dragon 当前版本）+ 最近两个版本的改动（官方公告/Wiki，带链接）。"""
    out = []
    ref = S.get("ref")
    if ref and (ref["his"] or ref["mine"]):
        out.append("<h2>英雄技能冷却（Data Dragon %s）</h2>" % E(ref["version"] or "?"))
        out.append('<p class="note">冷却为各等级的秒数（Data Dragon 原始数据，未计入技能急速）；消耗、距离同样来自 Data Dragon。</p>')
        for title, key in (("他常用的英雄", "his"), ("我的英雄池", "mine")):
            rows = []
            for c in ref[key]:
                if c.get("missing"):
                    rows.append([td("<b>%s</b>" % E(c["name_zh"])), td("Data Dragon 里没有找到", ""), td(""), td(""), td("")])
                    continue
                for i, sp in enumerate(c["spells"]):
                    rows.append([td("<b>%s</b>" % E(c["name_zh"]) if i == 0 else ""), td(E(sp["slot"])), td(E(sp["spell_name"])),
                                 td(E(sp["cooldown"] or "—"), "num"), td(E(" ".join(x for x in (sp["cost"], sp["cost_type"]) if x) or "—"))])
            if rows:
                out.append("<h3>%s</h3>" % title)
                out.append(table(["英雄", "技能", "名称", "冷却（秒）", "消耗"], rows, "dense"))
        out.append(src_note(ref["src"]))
    if ref and ref.get("first_items"):
        out.append("<h2>他常出的首件成装（Data Dragon %s）</h2>" % E(ref["first_items"][0]["version"]))
        out.append(table(["装备", "作为首件成装的次数", "总价", "属性"],
                         [[td("<b>%s</b>" % E(i["name_zh"])), td(i["times"], "num"), td(i["gold_total"], "num"),
                           td(E(i["stats_text"] or "—"))] for i in ref["first_items"]]))
        out.append(src_note(ref.get("items_src")))
    if notes:
        champs = set()
        if ref:
            champs = {c["champ"] for c in ref["his"] + ref["mine"]} | {c["name_zh"] for c in ref["his"] + ref["mine"]}
        rows = []
        for n in notes.get("items", []):
            subj = n.get("subject", "")
            if n.get("category") != "champion" or subj in champs or n.get("champ_id") in champs:
                rows.append([td(E(n.get("patch", ""))), td(E(subj)), td(E(n.get("direction_zh", n.get("direction", "")))),
                             td(E(n.get("change", ""))),
                             td(('<a href="%s" rel="noopener" target="_blank">%s</a>' % (E(safe_url(n.get("source_url"))), E(n.get("source_name", "来源"))))
                                if safe_url(n.get("source_url")) else E(n.get("source_url", "")))])
        out.append("<h2>最近两个版本的相关改动</h2>")
        out.append('<p class="note">%s</p>' % E(notes.get("note", "只列出他常用的英雄、我的英雄池和野区/资源相关的改动。")))
        out.append(table(["版本", "对象", "方向", "改动", "来源"], rows, "dense") if rows else "<p>这两个版本里没有相关改动。</p>")
    return "".join(out)


# ------------------------------------------------------------------ 页签：数据来源
def _sources(S, st, mm, calib, notes=None):
    m = S["meta"]
    out = ["<h2>下载记录</h2>"]
    kv = [("对手", m.get("riot_id")), ("PUUID", m.get("puuid")), ("服务器", "%s（%s）" % (m.get("platform"), m.get("platform_source"))),
          ("下载开始（UTC）", m.get("started_at_utc")), ("下载结束（UTC）", m.get("finished_at_utc")),
          ("对局时间范围（UTC）", "%s — %s" % (m.get("first_game_utc"), m.get("last_game_utc"))),
          ("对局版本", "、".join(m.get("patches", []))), ("Data Dragon 当前版本", m.get("ddragon_latest")),
          ("判断成装用的装备数据版本", "、".join(m.get("item_versions", []))),
          ("请求的比赛 ID", "%d 场（queue 420 单双排）" % m["n_ids"]), ("纳入统计", "%d 场（其中有时间线 %d 场）" % (m["n_included"], m["n_timeline"]))]
    out.append("<dl class='kv'>%s</dl>" % "".join("<dt>%s</dt><dd class='mono'>%s</dd>" % (E(k), E(str(v))) for k, v in kv))
    out.append("<h3>用到的接口</h3><ul>%s</ul>" % "".join("<li>%s</li>" % E(x) for x in (
        "account-v1 /riot/account/v1/accounts/by-riot-id/{gameName}/{tagLine} → PUUID",
        "league-v4 /lol/league/v4/entries/by-puuid/{puuid} → 当前段位（下载时的段位）",
        "match-v5 /lol/match/v5/matches/by-puuid/{puuid}/ids?queue=420 → 比赛 ID",
        "match-v5 /lol/match/v5/matches/{matchId} → 对局详情（每场一个文件 matches/<ID>.json）",
        "match-v5 /lol/match/v5/matches/{matchId}/timeline → 时间线（timelines/<ID>.json，每分钟一帧位置/经济 + 事件）",
        "Data Dragon champion.json / item.json / map11.png → 中文名、成装判定、小地图")))
    if m.get("excluded"):
        out.append("<h3>没纳入统计的对局</h3>")
        out.append(table(["比赛 ID", "原因"], [[td(E(x["match_id"]), "mono"), td(E(x["reason"] or ""))] for x in m["excluded"]]))
    if m.get("failures"):
        out.append("<h3>下载失败记录</h3>")
        out.append(table(["内容", "比赛 ID", "错误", "时间"], [[td(E(f.get("what", ""))), td(E(f.get("match_id") or "—"), "mono"),
                                                         td(E(f.get("error", ""))), td(E(f.get("at_utc", "")), "mono")]
                                                        for f in m["failures"]]))
    lr = S.get("lobby_ranks")
    if lr:
        out.append("<h3>对面同位置玩家的段位（检验“同段位”）</h3>")
        out.append(table(["段位", "人数"], [[td(E(k)), td(v, "num")] for k, v in lr["counts"]]))
        out.append(src_note(lr["src"]))
    out.append("<h2>口径和规则</h2><ul>%s</ul>" % "".join("<li>%s</li>" % E(x) for x in (
        S["baseline"]["rule"],
        "颜色：绿 ▲ = 比基准好，黄 ● = 差不多，红 ▼ = 差，灰 ○ = 无对比基准。颜色旁边都有符号，不只靠颜色区分。",
        "样本少于 10 场的结论都标了“样本太少”。",
        "时间线位置每分钟只记一次（frameInterval = 60000 ms），所有由位置推出来的路线、抓人、反野都标了“推测”并写了依据。",
        "被抓 = 击杀他的敌方英雄（击杀者 + 助攻）≥ 2 人。",
        m.get("item_rule", ""),
        "估算清了几组野怪时按每组约 %.0f 个补刀算（英雄联盟 Wiki「Creep score」页面：整组野怪约 4 个补刀；石头人的分项加起来对不上，"
        "只用于粗略估计）。" % (S.get("jungle") or {}).get("cs_per_camp_assumption", 4),
        G.GEOMETRY_SOURCE,
    ) if x))
    if notes:
        out.append("<h3>版本公告和外部资料</h3>")
        out.append("<p>%s</p>" % E(notes.get("_说明", "")))
        out.append("<ul>%s</ul>" % "".join(
            '<li>%s：%s %s</li>' % (E(p.get("patch")), E(p.get("release")),
                                    ('<a href="%s" rel="noopener" target="_blank">官方公告</a>' % E(safe_url(p.get("url"))))
                                    if safe_url(p.get("url")) else "") for p in notes.get("patches", [])))
    out.append('<p class="note">OP.GG：这个分析环境无法访问 OP.GG（网络策略拦截），对位胜率只采用你提供的 OP.GG 截图，'
               '并在应对方案页写明版本、段位范围和样本量；没有截图的地方不写胜率。</p>')
    chk = S.get("checks") or {}
    incon = list((st or {}).get("inconsistencies") or [])
    for c in chk.get("challenges_mismatch") or []:
        incon.append("%s：自算 %s = %s，官方 challenges = %s" % (c["match_id"], c["field"], c["ours"], c["challenges"]))
    if m.get("ddragon_latest") and m.get("patches") and not any((m.get("ddragon_latest") or "").startswith(p + ".") for p in m["patches"]):
        incon.append("Data Dragon 当前版本 %s 与对局版本 %s 不同：成装判定优先用对局版本的装备数据（若已下载）"
                     % (m.get("ddragon_latest"), "、".join(m["patches"])))
    out.append("<h2>不同来源之间不一致的地方</h2>")
    out.append("<ul>%s</ul>" % "".join("<li>%s</li>" % E(x) for x in incon) if incon else "<p>没有发现不一致。</p>")
    ext = (st or {}).get("external_sources") or []
    if ext:
        out.append("<h3>你提供的截图 / 其它来源</h3>")
        out.append(table(["来源", "内容", "与下载数据的对比"], [[td(E(x.get("what", ""))), td(E(x.get("notes", ""))),
                                                        td(E("；".join(x.get("conflicts", [])) or "一致"))] for x in ext]))
    if calib:
        out.append(_calib_section(calib, mm))
    return "".join(out)


def _calib_section(calib, mm):
    out = ["<h2>区域划分核对</h2>",
           '<p class="note">把几场的整分钟位置画在小地图上，叠加本工具的区域边界（虚线：河道带和上下半分界；浅色带：车道；圆圈：野怪营地）。'
           '下表是“对线期（2–12 分钟）各位置玩家落在自己那条线的比例”，比例高说明车道划分合理。</p>']
    layers = []
    for p in calib["points"]:
        layers.append(dot(p["x"], p["y"], {"TOP": "s1", "JUNGLE": "s2", "MIDDLE": "s3"}.get(p["position"], "s0"),
                          "%s %s %d:00 → %s" % (p["match_id"], POS_ZH.get(p["position"], ""), p["minute"], G.ZONE_NAMES.get(p["zone"], p["zone"])),
                          r=3))
    out.append('<div class="maps">')
    out.append(mm.svg(layers, "核对图：%d 场的整分钟位置" % calib["n_games"],
                      [(legend_sym("s1"), "上单"), (legend_sym("s2"), "打野"), (legend_sym("s3"), "中单"), (legend_sym("s0"), "下路/辅助")],
                      zones=True))
    out.append('<div class="side">')
    out.append(table(["位置", "对线期位置点", "落在自己那条线", "落在野区", "落在河道"],
                     [[td(E(POS_ZH.get(r["position"], r["position"]))), td(r["n"], "num"), td(pct(r["in_lane"]), "num"),
                       td(pct(r["in_jungle"]), "num"), td(pct(r["in_river"]), "num")] for r in calib["lane_check"]]))
    out.append('<p class="note">打野 1:00–3:00 位置在野区的比例：%s（%d 个点）。地标自检失败：%s。</p>' % (
        pct(calib["jungler_in_jungle"]), calib["jungler_n"], E(str(calib["selftest"] or "无"))))
    out.append("</div></div>")
    return "".join(out)


# ------------------------------------------------------------------ 样式与脚本
CSS = r"""
/* Layout: 顶部档案头 + 横向页签；每个页签是一列阅读流，小地图与侧栏在宽屏并排、窄屏堆叠 */
:root{
  --bg:#f3f5f7; --surface:#ffffff; --ink:#152033; --ink-2:#4b5566; --muted:#7b8494; --line:#dce1e8;
  --accent:#1d6a73; --accent-soft:#e2f0f1;
  --good:#0ca30c; --warn:#fab219; --bad:#d03b3b;
  --good-bg:rgba(12,163,12,.13); --warn-bg:rgba(250,178,25,.22); --bad-bg:rgba(208,59,59,.14); --none-bg:rgba(123,132,148,.12);
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s0:#8a8f98;
  --t1:#d03b3b; --t2:#fab219; --t3:#cfd6df;
  --map-bg:#26323a; --map-lane:#56636b; --map-river:#3b6f87; --ring:#ffffff;
  --font-display:"Barlow Condensed","Noto Sans SC","PingFang SC","Microsoft YaHei",sans-serif;
  --font-body:"Noto Sans SC","PingFang SC","Microsoft YaHei","Hiragino Sans GB",system-ui,sans-serif;
  --font-mono:"JetBrains Mono",ui-monospace,"SFMono-Regular",Consolas,monospace;
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  --bg:#0f1318; --surface:#161c24; --ink:#e7ebf0; --ink-2:#b5bdc9; --muted:#8a93a3; --line:#2a333f;
  --accent:#52b5bf; --accent-soft:#17323a; --s1:#3987e5; --s2:#d95926; --s3:#199e70;
  --t3:#6f7a88; color-scheme:dark}}
:root[data-theme="dark"]{
  --bg:#0f1318; --surface:#161c24; --ink:#e7ebf0; --ink-2:#b5bdc9; --muted:#8a93a3; --line:#2a333f;
  --accent:#52b5bf; --accent-soft:#17323a; --s1:#3987e5; --s2:#d95926; --s3:#199e70;
  --t3:#6f7a88; color-scheme:dark}
*{box-sizing:border-box}
[hidden]{display:none!important}
body{background:var(--bg);color:var(--ink);font-family:var(--font-body);font-size:15px;line-height:1.65;margin:0}
.wrap{max-width:1180px;margin:0 auto;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
.synthetic{background:var(--bad);color:#fff;text-align:center;padding-block:6px;font-weight:700}
header.top{background:var(--surface);border-bottom:1px solid var(--line)}
header.top .wrap{padding-block:22px 16px}
.eyebrow{margin:0;font-family:var(--font-display);text-transform:uppercase;letter-spacing:.08em;color:var(--accent);font-weight:600}
h1{font-family:var(--font-display);font-weight:600;font-size:clamp(26px,4vw,40px);line-height:1.15;margin:4px 0 6px;text-wrap:balance;overflow-wrap:anywhere}
.sub{margin:0;color:var(--ink-2)}
nav.tabs{position:sticky;top:env(safe-area-inset-top,0px);z-index:5;background:var(--surface);border-bottom:1px solid var(--line)}
nav.tabs .wrap{display:flex;gap:4px;overflow-x:auto;scrollbar-width:none}
nav.tabs button{font:600 16px/1 var(--font-display);letter-spacing:.04em;background:none;border:0;border-bottom:3px solid transparent;
  color:var(--ink-2);padding:14px 12px 11px;cursor:pointer;white-space:nowrap}
nav.tabs button[aria-selected="true"]{color:var(--ink);border-bottom-color:var(--accent)}
nav.tabs button:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
body::before{content:"";position:fixed;inset:0 0 auto;height:env(safe-area-inset-top,0px);z-index:10;background:var(--surface)}
main{padding-block:8px 48px}
.panel{display:flex;flex-direction:column;gap:6px}
h2{font-family:var(--font-display);font-weight:600;font-size:23px;margin:26px 0 4px;text-wrap:balance}
h3{font-size:16px;margin:18px 0 4px}
p{margin:4px 0;max-width:75ch}
.note,.src{color:var(--muted);font-size:13px}
.src{margin-top:2px}
.verdict{background:var(--accent-soft);padding:10px 14px;border-radius:6px;max-width:none}
.verdict .note{display:block;margin-top:4px}
.lead{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:14px 18px;margin-top:18px;font-size:16px}
.lead.pending{border-style:dashed;color:var(--ink-2)}
.badge{display:inline-block;font-size:11px;line-height:1;padding:4px 6px;border-radius:4px;background:var(--warn-bg);color:var(--ink);
  font-weight:500;vertical-align:middle;white-space:nowrap}
.badge.inf{background:var(--none-bg)}
.tbl{overflow-x:auto;background:var(--surface);border:1px solid var(--line);border-radius:6px}
.tbl.scroll{max-height:440px;overflow-y:auto}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:7px 10px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}
th{font-weight:500;color:var(--ink-2);background:var(--surface);position:sticky;top:0;white-space:nowrap}
tbody tr:last-child td{border-bottom:0}
.dense td{padding:5px 10px}
td.num{font-variant-numeric:tabular-nums;white-space:nowrap}
td.mono,dd.mono{font-family:var(--font-mono);font-size:12.5px;overflow-wrap:anywhere}
td.calc{color:var(--ink-2);font-size:13px;min-width:16em}
.plan td{min-width:11em}
td.r-good{background:var(--good-bg)} td.r-warn{background:var(--warn-bg)} td.r-bad{background:var(--bad-bg)} td.r-none{background:var(--none-bg)}
td .mk{font-style:normal;font-size:11px;color:var(--ink-2)}
.chip{display:inline-flex;gap:4px;align-items:center;padding:2px 8px;border-radius:999px;font-size:12px;background:var(--none-bg)}
.chip.good{background:var(--good-bg)} .chip.warn{background:var(--warn-bg)} .chip.bad{background:var(--bad-bg)}
.chip i{font-style:normal}
.wl{display:inline-block;min-width:1.6em;text-align:center;border-radius:4px;font-weight:700;font-size:13px}
.wl.w{background:var(--good-bg)} .wl.l{background:var(--bad-bg)}
.ev{font-weight:500} .ev.d{color:var(--bad)} .ev.k{color:var(--good)}
.maps{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr));gap:18px;align-items:start;margin-top:8px}
.maps>*{min-width:0}
figure.map{margin:0;background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:10px}
figure.map>svg{display:block;width:100%;max-width:560px;height:auto;aspect-ratio:1/1;border-radius:4px;background:var(--map-bg)}
.lg svg{display:inline-block;width:14px;height:14px;flex:none}
figcaption{display:flex;flex-direction:column;gap:4px;margin-top:8px;font-size:13px}
.legend{display:flex;flex-wrap:wrap;gap:6px 14px;color:var(--ink-2)}
.lg{display:inline-flex;align-items:center;gap:5px}
.pt{stroke:var(--ring);stroke-width:1.6}
circle.pt,polygon.pt{fill-opacity:.95}
.pt.s1{fill:var(--s1)} .pt.s2{fill:var(--s2)} .pt.s3{fill:var(--s3)} .pt.s0{fill:var(--s0)}
.pt.t1{fill:var(--t1)} .pt.t2{fill:var(--t2)} .pt.t3{fill:var(--t3)}
.pt.ok{fill:var(--good)} .pt.maybe{fill:var(--warn)}
g.pt.dead path{stroke:var(--bad);stroke-width:3.2;fill:none}
g.pt.dead{stroke:none}
.route{fill:none;stroke-width:1.4;opacity:.55;stroke-linejoin:round}
.route.s1{stroke:var(--s1)} .route.s2{stroke:var(--s2)} .route.s3{stroke:var(--s3)} .route.s0{stroke:var(--s0)}
.zl{fill:none;stroke:#fff;stroke-width:1.4;stroke-dasharray:6 5;opacity:.8}
.zl.dim{opacity:.45}
.zlane{fill:none;stroke:#fff;opacity:.12;stroke-linecap:round;stroke-linejoin:round}
.zc{fill:none;stroke:#ffe08a;stroke-width:1.2;opacity:.85}
.zb{fill:none;stroke:#fff;stroke-width:1.6;opacity:.8}
.zp{fill:none;stroke:#b8f0ff;stroke-width:1.4;stroke-dasharray:3 3;opacity:.9}
.side{display:flex;flex-direction:column;gap:6px;min-width:0}
dl.kv{display:grid;grid-template-columns:max-content 1fr;gap:6px 16px;margin:6px 0;background:var(--surface);border:1px solid var(--line);
  border-radius:6px;padding:12px 14px}
dl.kv dt{color:var(--ink-2)} dl.kv dd{margin:0;min-width:0;overflow-wrap:anywhere}
ul{margin:4px 0;padding-left:1.3em;max-width:85ch} li{margin:3px 0}
@media (max-width:560px){dl.kv{grid-template-columns:1fr} dl.kv dd{margin-bottom:6px} td.calc{min-width:12em}}
@media (prefers-reduced-motion:reduce){*{scroll-behavior:auto}}
"""

JS = r"""
(function(){
  var tabs=[].slice.call(document.querySelectorAll('nav.tabs button'));
  function show(key, push){
    var hit=false;
    tabs.forEach(function(b){var on=b.id==='tab-'+key; if(on)hit=true;
      b.setAttribute('aria-selected',on?'true':'false');
      document.getElementById(b.getAttribute('aria-controls')).hidden=!on;});
    if(!hit&&tabs[0]){show(tabs[0].id.slice(4));return;}
    if(push){try{history.replaceState(null,'','#'+key);}catch(e){}}
  }
  tabs.forEach(function(b){b.addEventListener('click',function(){show(b.id.slice(4),true);});});
  document.querySelector('nav.tabs').addEventListener('keydown',function(e){
    var i=tabs.indexOf(document.activeElement); if(i<0)return;
    if(e.key==='ArrowRight'||e.key==='ArrowLeft'){var j=(i+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
      tabs[j].focus(); show(tabs[j].id.slice(4),true); e.preventDefault();}
  });
  var h=(location.hash||'').replace('#','');
  if(/^[a-z]+$/.test(h))show(h,false);
})();
"""
