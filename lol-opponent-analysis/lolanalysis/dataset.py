"""读取 download_riot.py 打出来的 zip（或已解压的目录）。

只按需逐个读取原始 JSON，不把内容打印出来。"""

import json
import os
import zipfile


class DatasetError(Exception):
    pass


def safe_extract(zip_path, dest):
    """解压到一个新的空目录；拒绝绝对路径和 .. 路径。"""
    os.makedirs(dest, exist_ok=True)
    if os.listdir(dest):
        raise DatasetError("解压目录不是空的：%s" % dest)
    dest_abs = os.path.abspath(dest)
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/"):
                raise DatasetError("zip 里有不安全的路径：%r" % info.filename)
            target = os.path.abspath(os.path.join(dest_abs, *name.split("/")))
            if not target.startswith(dest_abs + os.sep) and target != dest_abs:
                raise DatasetError("zip 里有不安全的路径：%r" % info.filename)
        zf.extractall(dest_abs)
    return dest_abs


def find_root(path):
    """找到含 manifest.json 的目录（zip 里可能多包了一层文件夹）。"""
    if os.path.exists(os.path.join(path, "manifest.json")):
        return path
    for root, dirs, files in os.walk(path):
        if "manifest.json" in files:
            return root
    raise DatasetError("没找到 manifest.json：%s" % path)


def _load(path):
    with open(path, "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


class Dataset:
    def __init__(self, root):
        self.root = root
        self.manifest = _load(self._p("manifest.json"))
        self.account = _load(self._p("account.json")) if self.exists("account.json") else {}
        self.puuid = self.account.get("puuid") or self.manifest.get("puuid")
        if not self.puuid:
            raise DatasetError("数据里没有 PUUID（account.json 缺失）")
        self.league = _load(self._p("league_entries.json")) if self.exists("league_entries.json") else None
        self.match_ids = (_load(self._p("match_ids.json")) if self.exists("match_ids.json")
                          else self.manifest.get("match_ids", []))
        self.ddragon_meta = _load(self._p("ddragon/meta.json")) if self.exists("ddragon/meta.json") else {}
        self.lane_opp_ranks = (_load(self._p("lane_opponent_ranks.json"))
                               if self.exists("lane_opponent_ranks.json") else {})

    def _p(self, rel):
        return os.path.join(self.root, *rel.split("/"))

    def exists(self, rel):
        return os.path.exists(self._p(rel))

    def file_meta(self, rel):
        return self.manifest.get("files", {}).get(rel, {})

    def match(self, mid):
        rel = "matches/%s.json" % mid
        return _load(self._p(rel)) if self.exists(rel) else None

    def timeline(self, mid):
        rel = "timelines/%s.json" % mid
        return _load(self._p(rel)) if self.exists(rel) else None

    def ddragon_file(self, version, name):
        rel = "ddragon/%s/%s" % (version, name)
        return self._p(rel) if self.exists(rel) else None

    def ddragon_json(self, version, name):
        p = self.ddragon_file(version, name)
        return _load(p) if p else None

    def map_image(self):
        rel = (self.manifest.get("ddragon") or {}).get("map_image")
        if rel and self.exists(rel):
            return self._p(rel)
        ddir = self._p("ddragon")
        if os.path.isdir(ddir):
            for v in sorted(os.listdir(ddir), reverse=True):
                p = os.path.join(ddir, v, "map11.png")
                if os.path.exists(p):
                    return p
        return None


def open_dataset(path, work_dir):
    """path 可以是 zip 或目录。zip 会解压到 work_dir/raw（必须是新目录）。"""
    if os.path.isfile(path) and zipfile.is_zipfile(path):
        dest = os.path.join(work_dir, "raw")
        if os.path.isdir(dest) and os.listdir(dest):
            root = find_root(dest)
        else:
            root = find_root(safe_extract(path, dest))
    elif os.path.isdir(path):
        root = find_root(path)
    else:
        raise DatasetError("既不是 zip 也不是目录：%s" % path)
    return Dataset(root)
