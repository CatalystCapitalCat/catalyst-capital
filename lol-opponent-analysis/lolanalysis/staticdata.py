"""Data Dragon 英雄/装备数据：中文名、"成装"判定。

Data Dragon 的 item.json 没有"传说装备"字段，所以"成装"用规则判定（规则写进报告的数据来源页）：
  召唤师峡谷可用（maps["11"]）、商店可买（gold.purchasable 且 inStore 不为 false）、
  总价 ≥ COMPLETED_MIN_GOLD、不是鞋/消耗品/饰品、没有 requiredChampion / requiredAlly（奥恩升级装）。
"""

COMPLETED_MIN_GOLD = 2000
EXCLUDED_TAGS = {"Boots", "Consumable", "Trinket", "Jungle", "Lane", "GoldPer"}
COMPLETED_RULE = ("成装 = 召唤师峡谷可用、商店可直接购买、总价 ≥ %d 金币、"
                  "不是鞋/消耗品/饰品/打野或对线起始装/辅助任务装、不是奥恩升级装" % COMPLETED_MIN_GOLD)


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
            for loc, target in (("zh_CN", self.champ_zh),):
                data = dataset.ddragon_json(self.latest, "champion_%s.json" % loc) or {}
                for cid, c in (data.get("data") or {}).items():
                    target[cid] = c.get("name", cid)
                    try:
                        self.champ_by_key[int(c.get("key"))] = cid
                    except (TypeError, ValueError):
                        pass

    def champ_name(self, champion_name):
        """中文名（找不到就用英文 id）。"""
        return self.champ_zh.get(champion_name, champion_name)

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
            completed = (
                bool(maps.get("11", False))
                and bool(gold.get("purchasable", False))
                and it.get("inStore", True) is not False
                and (gold.get("total") or 0) >= COMPLETED_MIN_GOLD
                and not (tags & EXCLUDED_TAGS)
                and not it.get("requiredChampion")
                and not it.get("requiredAlly")
                and not it.get("hideFromAll", False)
                and not it.get("consumed", False)
            )
            out[int(iid)] = {
                "name_zh": it.get("name", iid),
                "name_en": (en_data.get(iid) or {}).get("name", it.get("name", iid)),
                "gold_total": gold.get("total") or 0,
                "completed": completed,
            }
        self._items[version] = out
        return out

    def item(self, patch, item_id):
        ver = self.item_version_for_patch(patch)
        info = self.items(ver).get(int(item_id)) if ver else None
        if info is None and self.latest and ver != self.latest:
            info = self.items(self.latest).get(int(item_id))
        return info
