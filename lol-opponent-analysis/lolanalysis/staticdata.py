"""Data Dragon 英雄/装备数据：中文名、"成装"判定、技能数据。

Data Dragon 的 item.json 没有"传说装备"字段，"成装"按规则判定（规则写进报告的数据来源页）：
  4 位数 id、召唤师峡谷可用（maps["11"]）、总价 ≥ 2000、不是消耗品/饰品，并且 into 里的
  4 位数峡谷装备都不可直接购买（这样魔宗/大天使这类"只能自动升级"的装备仍算成装）。
  另外手动加入梅贾的窃魂卷（3041，1500 金，第三方站点归为传说装备）。
  这个规则在 16.21.1 上与游戏客户端的 epicness=5（传说）对照过：116 件里只有 1 件是已下架的旧神话装。
英雄按数字 championId 对应 Data Dragon 的 key（不用 championName：FiddleSticks/Fiddlesticks 大小写不一致）。
"""

import html
import re

COMPLETED_MIN_GOLD = 2000
EXCLUDED_TAGS = {"Consumable", "Trinket"}
EXTRA_COMPLETED = {3041}  # 梅贾的窃魂卷
COMPLETED_RULE = ("成装 = 4 位数 id、召唤师峡谷可用、总价 ≥ %d 金币、不是消耗品/饰品，且它能升级成的 4 位数峡谷装备"
                  "都不可直接购买（魔宗、大天使等仍算成装）；另加梅贾的窃魂卷。规则在 Data Dragon 16.21.1 上与客户端"
                  "传说装备标记对照过（116 件中 1 件误判，为已下架旧装备）" % COMPLETED_MIN_GOLD)
# 我的英雄池的常用叫法（Data Dragon 里没有外号）
NICKNAMES = {"Renekton": "鳄鱼", "Ambessa": "狼母", "Rumble": "兰博", "Jax": "武器"}


class StaticData:
    def __init__(self, dataset):
        self.ds = dataset
        meta = dataset.ddragon_meta or {}
        self.latest = meta.get("latest_version")
        self.match_patch_versions = meta.get("match_patch_versions", {})
        self.champ_zh = {}   # championName(id 形式, 如 LeeSin) -> 中文名
        self.champ_by_key = {}  # 数字 championId -> id
        self._items = {}     # version -> {item_id: info}
        if self.latest:
            data = dataset.ddragon_json(self.latest, "champion_zh_CN.json") or {}
            for cid, c in (data.get("data") or {}).items():
                # zh_CN 里 name 是称号（如"武器大师"），title 是人名（如"贾克斯"）
                self.champ_zh[cid] = c.get("name", cid)
                try:
                    self.champ_by_key[int(c.get("key"))] = cid
                except (TypeError, ValueError):
                    pass

    def champ_id(self, participant):
        """参与者 → Data Dragon 英雄 id（优先用数字 championId）。"""
        cid = self.champ_by_key.get(participant.get("championId"))
        return cid or participant.get("championName", "")

    def champ_name(self, champ_id):
        """中文名（找不到就用英文 id）。"""
        return self.champ_zh.get(champ_id, champ_id)

    def item_version_for_patch(self, patch):
        return self.match_patch_versions.get(patch) or self.latest

    def items(self, version):
        if version in self._items:
            return self._items[version]
        out = {}
        zh = self.ds.ddragon_json(version, "item_zh_CN.json") or {}
        en = self.ds.ddragon_json(version, "item_en_US.json") or {}
        en_data = en.get("data") or {}
        for iid, it in (zh.get("data") or en_data).items():
            gold = it.get("gold") or {}
            tags = set(it.get("tags") or [])
            maps = it.get("maps") or {}
            into_sr = [x for x in (it.get("into") or []) if len(str(x)) == 4]
            completed = (
                len(str(iid)) == 4
                and bool(maps.get("11", False))
                and (gold.get("total") or 0) >= COMPLETED_MIN_GOLD
                and not (tags & EXCLUDED_TAGS)
                and all(not ((zh.get("data") or en_data).get(str(x), {}).get("gold") or {}).get("purchasable", False)
                        for x in into_sr)
            ) or int(iid) in EXTRA_COMPLETED
            out[int(iid)] = {
                "name_zh": it.get("name", iid),
                "name_en": (en_data.get(iid) or {}).get("name", it.get("name", iid)),
                "gold_total": gold.get("total") or 0,
                "completed": completed,
                "stats": it.get("stats") or {},
                "plaintext": clean_text(it.get("plaintext", "")),
                "stats_text": stats_text(it.get("description", "")),
                "version": version,
            }
        self._items[version] = out
        return out

    def item(self, patch, item_id):
        ver = self.item_version_for_patch(patch)
        info = self.items(ver).get(int(item_id)) if ver else None
        if info is None and self.latest and ver != self.latest:
            info = self.items(self.latest).get(int(item_id))
        return info

    # ------------------------------------------------------------ 技能（championFull.json）
    def _full(self):
        if not hasattr(self, "_full_cache"):
            self._full_cache = (self.ds.ddragon_json(self.latest, "championFull_zh_CN.json") or {}).get("data") or {}
        return self._full_cache

    def has_spell_data(self):
        return bool(self._full())

    def spells(self, champ_id):
        """当前版本的技能数据：名字、冷却、消耗、距离（来自 Data Dragon championFull.json，zh_CN）。"""
        c = self._full().get(champ_id)
        if not c:
            return None
        st = c.get("stats") or {}
        partype = c.get("partype", "")
        out = {"champ": champ_id, "name": c.get("name", champ_id), "title": c.get("title", ""),
               "version": self.latest,
               "passive": {"name": (c.get("passive") or {}).get("name", ""),
                           "desc": clean_text((c.get("passive") or {}).get("description", ""))},
               "spells": [],
               "stats": {k: st.get(k) for k in ("hp", "hpperlevel", "armor", "armorperlevel", "attackdamage",
                                                 "attackdamageperlevel", "attackrange", "movespeed")}}
        for key, sp in zip("QWER", c.get("spells") or []):
            out["spells"].append({
                "key": key, "name": sp.get("name", ""), "maxrank": sp.get("maxrank"),
                "cooldown": sp.get("cooldownBurn") or "/".join(_fmt(x) for x in sp.get("cooldown") or []),
                "cost": sp.get("costBurn", ""),
                "cost_type": clean_text((sp.get("costType", "") or "").replace("{{ abilityresourcename }}", partype)),
                "range": sp.get("rangeBurn", ""), "desc": clean_text(sp.get("description", "")),
            })
        return out


def _fmt(x):
    return ("%g" % x) if isinstance(x, (int, float)) else str(x)


def clean_text(s):
    """去掉 Data Dragon 文本里的 HTML 标签。"""
    s = re.sub(r"<br\s*/?>", " ", s or "")
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def stats_text(description):
    """装备描述里 <stats>…</stats> 那一段（属性），去掉标签。"""
    m = re.search(r"<stats>(.*?)</stats>", description or "", re.S)
    if not m:
        return ""
    parts = [clean_text(x) for x in re.split(r"<br\s*/?>", m.group(1))]
    return "；".join(p for p in parts if p)
