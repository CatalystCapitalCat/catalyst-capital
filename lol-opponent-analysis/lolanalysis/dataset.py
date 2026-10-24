"""读取 download_riot.py 打出来的 zip（或已解压的目录）。

只按需逐个读取原始 JSON，不把内容打印出来。"""

import hashlib
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
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise DatasetError("zip 里有符号链接：%r" % info.filename)
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
        # manifest["match_ids"] 是实际处理的那一段（重开局会多取几场补足）；旧版只有 match_ids.json
        self.match_ids = (self.manifest.get("match_ids")
                          or (_load(self._p("match_ids.json")) if self.exists("match_ids.json") else []))
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


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def open_dataset(path, work_dir):
    """path 可以是 zip 或目录。zip 解压到 work_dir/raw_<zip 的 sha256 前 12 位>：
    同一个 zip 重复分析会复用解压结果，换了 zip（比如续传后重新打包）一定会重新解压，不会用到旧数据。"""
    if os.path.isfile(path) and zipfile.is_zipfile(path):
        digest = _sha256(path)
        dest = os.path.join(work_dir, "raw_%s" % digest[:12])
        marker = os.path.join(dest, ".source.json")
        if os.path.exists(marker):
            root = find_root(dest)
        else:
            if os.path.isdir(dest) and os.listdir(dest):
                raise DatasetError("解压目录里有不完整的旧内容，请删除后重试：%s" % dest)
            safe_extract(path, dest)
            with open(marker, "w", encoding="utf-8") as fh:
                json.dump({"zip": os.path.abspath(path), "sha256": digest}, fh)
            root = find_root(dest)
        print("数据：%s（sha256 %s…）→ %s" % (os.path.basename(path), digest[:12], root))
    elif os.path.isdir(path):
        root = find_root(path)
    else:
        raise DatasetError("既不是 zip 也不是目录：%s" % path)
    return Dataset(root)
