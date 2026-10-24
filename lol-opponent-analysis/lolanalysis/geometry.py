"""召唤师峡谷坐标 → 区域划分 / 野怪营地 / 小地图像素。

坐标系：时间线 position 的 x 向右、y 向上；蓝方基地在左下（x、y 都小），红方在右上。
先归一化到 u,v ∈ [0,1]（左下 0,0），区域划分都在归一化坐标里做。
区域划分是近似几何（不是官方多边形），analyze.py calibrate 会把点画到小地图上并统计
"上单的对线期位置有多少落在上路"等来核对；报告里写明这是推测分区。
"""

import math

# Riot 早期静态数据给出的召唤师峡谷（mapId 11）坐标范围，也用来把坐标映射到 map11.png
X_MIN, Y_MIN = -120.0, -120.0
X_MAX, Y_MAX = 14870.0, 14980.0
GEOMETRY_SOURCE = ("小地图坐标映射：x ∈ [-120, 14870]、y ∈ [-120, 14980]（与 Data Dragon map11.png 配套的常用范围；"
                   "游戏文件给出的 0–14850 与之相差不超过约 6 像素）；map11.png 是旧版示意图，不显示 2024 年后的地形改动；"
                   "防御塔坐标来自时间线 BUILDING_KILL 事件，营地坐标来自游戏数据刷新点；区域边界为本工具的近似划分")


def norm(x, y):
    return (x - X_MIN) / (X_MAX - X_MIN), (y - Y_MIN) / (Y_MAX - Y_MIN)


def to_px(x, y, w=512, h=512):
    u, v = norm(x, y)
    return u * w, (1.0 - v) * h


def _seg_dist(p, a, b):
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


def _poly_dist(p, pts):
    return min(_seg_dist(p, pts[i], pts[i + 1]) for i in range(len(pts) - 1))


# 车道中心线（归一化坐标）。依据：各路一塔/二塔/高地塔的近似坐标都落在这些折线 ±0.03 内。
LANES = {
    "TOP": [(0.080, 0.300), (0.080, 0.860), (0.140, 0.920), (0.700, 0.920)],
    "MID": [(0.270, 0.270), (0.730, 0.730)],
    "BOT": [(0.300, 0.080), (0.860, 0.080), (0.920, 0.140), (0.920, 0.700)],
}
LANE_HALF_WIDTH = {"TOP": 0.055, "MID": 0.050, "BOT": 0.055}
RIVER_HALF_WIDTH = 0.085          # |u+v-1| ≤ 此值 → 河道
GANK_RIVER_LANE_DIST = 0.13       # 河道里离某条线中心线这么近 → 算作去这条线（河道口）

# 野怪营地（游戏坐标，取自游戏数据的刷新点；不同来源相差最多约 240 码）。team = 营地属于哪一方的野区。
CAMPS = [
    # 蓝方（100）野区：上半 = 蓝buff/蛤蟆/三狼，下半 = 红buff/F6/石头人
    (100, "蓝buff", 3872, 7901), (100, "蛤蟆", 2091, 8428), (100, "三狼", 3781, 6444),
    (100, "F6", 6707, 5521), (100, "红buff", 7862, 4111), (100, "石头人", 8324, 2755),
    # 红方（200）野区：上半 = 红buff/F6/石头人，下半 = 蓝buff/蛤蟆/三狼
    (200, "蓝buff", 10932, 6991), (200, "蛤蟆", 12704, 6444), (200, "三狼", 11008, 8387),
    (200, "F6", 7987, 9471), (200, "红buff", 7017, 10776), (200, "石头人", 6317, 12147),
    # 河蟹（不属于任何一方）
    (0, "上河蟹", 4400, 9600), (0, "下河蟹", 10500, 5170),
]
CAMP_RADIUS = 0.045               # 归一化距离，约 670 码
PIT_CENTERS = {"BARON_PIT": (5007, 10471), "DRAGON_PIT": (9866, 4414)}
# 坑的半径（归一化）：大龙坑约 970 码（2026 年巢虫/先锋击杀点的均值都在内），小龙坑约 820 码（不含旁边的下河蟹）
PIT_RADIUS = {"BARON_PIT": 0.065, "DRAGON_PIT": 0.055}

# 区域代码（与队伍无关）
ZONE_NAMES = {
    "BASE_BLUE": "蓝方基地", "BASE_RED": "红方基地",
    "TOP": "上路", "MID": "中路", "BOT": "下路",
    "RIVER_TOP": "上半河道", "RIVER_BOT": "下半河道",
    "BARON_PIT": "大龙/先锋/虚空巢虫坑", "DRAGON_PIT": "小龙坑",
    "JG_BLUE_TOP": "蓝方上半野区", "JG_BLUE_BOT": "蓝方下半野区",
    "JG_RED_TOP": "红方上半野区", "JG_RED_BOT": "红方下半野区",
}


def _nearest_camp_norm(u, v):
    best = None
    for team, name, x, y in CAMPS:
        cu, cv = norm(x, y)
        d = math.hypot(u - cu, v - cv)
        if best is None or d < best[0]:
            best = (d, team, name)
    return best


def zone(x, y):
    """返回与队伍无关的区域代码。"""
    u, v = norm(x, y)
    if u <= 0.30 and v <= 0.30 and u + v <= 0.52:
        return "BASE_BLUE"
    if u >= 0.70 and v >= 0.70 and u + v >= 1.48:
        return "BASE_RED"
    for code, (px, py) in PIT_CENTERS.items():
        pu, pv = norm(px, py)
        if math.hypot(u - pu, v - pv) <= PIT_RADIUS[code]:
            return code
    camp = _nearest_camp_norm(u, v)
    in_camp = camp[0] <= CAMP_RADIUS and camp[1] != 0
    if not in_camp:
        for lane, pts in LANES.items():
            if _poly_dist((u, v), pts) <= LANE_HALF_WIDTH[lane]:
                return lane
    if abs(u + v - 1.0) <= RIVER_HALF_WIDTH and not in_camp:
        return "RIVER_TOP" if v > u else "RIVER_BOT"
    side = "BLUE" if u + v < 1.0 else "RED"
    half = "TOP" if v > u else "BOT"
    return "JG_%s_%s" % (side, half)


def nearest_camp(x, y, max_dist=CAMP_RADIUS * 1.6):
    """最近的营地 (team, 名字, 距离)；太远返回 None。"""
    u, v = norm(x, y)
    d, team, name = _nearest_camp_norm(u, v)
    return (team, name, d) if d <= max_dist else None


def rel_zone(code, team_id):
    """把区域代码换成相对某一方的说法（己方/对方）。"""
    own = "BLUE" if team_id == 100 else "RED"
    if code.startswith("BASE_"):
        return "己方基地" if code.endswith(own) else "对方基地"
    if code.startswith("JG_"):
        _, side, half = code.split("_")
        who = "己方" if side == own else "对方"
        return "%s%s野区" % (who, "上半" if half == "TOP" else "下半")
    return ZONE_NAMES.get(code, code)


def jungle_half_buff(team_id, half):
    """某一方的上/下半野区里是哪个 buff：蓝方上半=蓝buff、下半=红buff；红方上半=红buff、下半=蓝buff。"""
    if team_id == 100:
        return "蓝buff" if half == "TOP" else "红buff"
    return "红buff" if half == "TOP" else "蓝buff"


def gank_lane(x, y):
    """位置对应"去哪条线"：在线上→该线；河道口靠近某条线→该线；其它（野区/河道中央）→ None。"""
    code = zone(x, y)
    if code in ("TOP", "MID", "BOT"):
        return code
    if code in ("RIVER_TOP", "RIVER_BOT"):
        u, v = norm(x, y)
        best = min(LANES, key=lambda L: _poly_dist((u, v), LANES[L]))
        if _poly_dist((u, v), LANES[best]) <= GANK_RIVER_LANE_DIST:
            return best
    return None


def half_of(x, y):
    """地图上半/下半（以河道对角线 u=v 为界的另一条对角线：v>u 为上半）。"""
    u, v = norm(x, y)
    return "TOP" if v > u else "BOT"


def self_test():
    """用已知地标检查区域划分（营地、防御塔、泉水）。返回失败列表。"""
    checks = [
        ((554, 581), "BASE_BLUE"), ((14302, 14387), "BASE_RED"),
        ((981, 10441), "TOP"), ((4318, 13875), "TOP"), ((7943, 13411), "TOP"),
        ((5846, 6396), "MID"), ((8955, 8510), "MID"),
        ((10504, 1029), "BOT"), ((13866, 4505), "BOT"), ((6919, 1483), "BOT"),
        ((5007, 10471), "BARON_PIT"), ((9866, 4414), "DRAGON_PIT"),
        ((4400, 9600), "RIVER_TOP"), ((10500, 5200), "RIVER_BOT"),
    ]
    for team, name, x, y in CAMPS:
        if team == 100:
            half = "TOP" if name in ("蓝buff", "蛤蟆", "三狼") else "BOT"
            checks.append(((x, y), "JG_BLUE_" + half))
        elif team == 200:
            half = "BOT" if name in ("蓝buff", "蛤蟆", "三狼") else "TOP"
            checks.append(((x, y), "JG_RED_" + half))
    bad = []
    for (x, y), want in checks:
        got = zone(x, y)
        if got != want:
            bad.append(((x, y), want, got))
    return bad
