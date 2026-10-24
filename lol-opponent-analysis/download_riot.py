#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
英雄联盟对手数据下载脚本（只用 Python 标准库，Python 3.8+）

做的事：
  1. account-v1：按 游戏名#Tag 查 PUUID（并尝试查他当前所在服务器）
  2. league-v4：查他当前的单双排段位
  3. match-v5：下载最近 N 场单双排（queue 420）的对局详情 + 时间线
  4. league-v4：（可关）查每场对面同位置玩家的当前段位，用来判断对局是不是同段位
  5. Data Dragon：当前版本（以及对局所在版本）的英雄、装备数据，当前版本的完整英雄技能数据
     （championFull.json：技能冷却/消耗/距离）和召唤师峡谷小地图 map11.png
  6. 每个原始 JSON 按比赛 ID 单独存文件，manifest.json 记下每个文件的下载时间、接口、大小、sha256，
     最后整个目录打包成 zip

密钥：只从环境变量 RIOT_API_KEY 读取，不写进文件，不打印。
429：按返回的 Retry-After 暂停后重试；某一场失败就跳过并记进 manifest.json 的 failures。

用法（默认参数已经填好要查的对手）：
  python download_riot.py
  python download_riot.py --riot-id "名字#TAG" --platform euw1 --count 20
"""

import argparse
import datetime as dt
import hashlib
import http.client
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import deque

TOOL_VERSION = "1.0"
DEFAULT_RIOT_ID = "原州市的努力的天才超级打野李承勇#VALOR"
DEFAULT_PLATFORM = "auto"  # auto = 用 account-v1 查他的服务器，查不到就用 euw1（欧服西部）
FALLBACK_PLATFORM = "euw1"
RANKED_SOLO_QUEUE = 420

# 平台（服务器）→ match-v5 / account-v1 用的大区路由
PLATFORM_TO_REGION = {
    "euw1": "europe", "eun1": "europe", "tr1": "europe", "ru": "europe", "me1": "europe",
    "na1": "americas", "br1": "americas", "la1": "americas", "la2": "americas",
    "kr": "asia", "jp1": "asia",
    "oc1": "sea", "sg2": "sea", "tw2": "sea", "vn2": "sea", "ph2": "sea", "th2": "sea",
}

# account-v1 只在 americas / asia / europe 提供（东南亚服用 asia）
ACCOUNT_REGION = {"americas": "americas", "asia": "asia", "europe": "europe", "sea": "asia"}

DDRAGON_HOST = "ddragon.leagueoflegends.com"


class FatalAPIError(Exception):
    """密钥无效/过期等，继续请求也没意义的错误。"""


class HTTPFailure(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def utc_now():
    return dt.datetime.now(dt.timezone.utc)


def iso(ts):
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


class Logger:
    """同时打印到屏幕和 download_log.txt。不会写入密钥。"""

    def __init__(self, path):
        self.path = path
        self.fh = open(path, "a", encoding="utf-8")

    def __call__(self, msg):
        line = "[%s] %s" % (utc_now().strftime("%H:%M:%S"), msg)
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            print(line.encode("ascii", "replace").decode("ascii"), flush=True)
        self.fh.write(line + "\n")
        self.fh.flush()

    def close(self):
        self.fh.close()


class RateLimiter:
    """客户端主动限速：多个滑动窗口，例如 20 次/1 秒 + 100 次/120 秒（开发者密钥的默认限制）。"""

    def __init__(self, spec):
        self.windows = []
        for part in spec.split(","):
            n, sec = part.split(":")
            self.windows.append((int(n), float(sec), deque()))

    def wait(self):
        while True:
            now = time.monotonic()
            sleep_for = 0.0
            for limit, sec, q in self.windows:
                while q and now - q[0] >= sec:
                    q.popleft()
                if len(q) >= limit:
                    sleep_for = max(sleep_for, sec - (now - q[0]) + 0.05)
            if sleep_for <= 0:
                for _, _, q in self.windows:
                    q.append(now)
                return
            time.sleep(sleep_for)


class Client:
    def __init__(self, api_key, log, rate_spec, api_base=None, ddragon_base=None, timeout=30):
        self.api_key = api_key
        self.log = log
        self.limiter = RateLimiter(rate_spec)
        self.api_base = api_base.rstrip("/") if api_base else None
        self.ddragon_base = ddragon_base.rstrip("/") if ddragon_base else None
        self.timeout = timeout
        self.ctx = ssl.create_default_context()
        self.request_count = 0

    def _url(self, host, path, query=None, ddragon=False):
        base = self.ddragon_base if ddragon else self.api_base
        url = (base if base else "https://" + host) + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        return url

    def _open(self, url, host, riot):
        headers = {"User-Agent": "lol-opponent-analysis/%s (personal use)" % TOOL_VERSION,
                   "Accept": "application/json", "X-Host": host}
        if riot:
            headers["X-Riot-Token"] = self.api_key
        req = urllib.request.Request(url, headers=headers)
        return urllib.request.urlopen(req, timeout=self.timeout,
                                      context=self.ctx if url.startswith("https") else None)

    def get(self, host, path, query=None, riot=True, raw=False, label=""):
        """GET 一个地址。429 按 Retry-After 等待后重试；5xx/网络错误最多重试 4 次（2、4、8、16 秒）。
        返回 bytes（raw=True）或解析后的 JSON。404 等其它错误抛 HTTPFailure。"""
        url = self._url(host, path, query, ddragon=not riot)
        net_retries = 0
        rate_retries = 0
        while True:
            if riot:
                self.limiter.wait()
            self.request_count += 1
            try:
                with self._open(url, host, riot) as resp:
                    body = resp.read()
                if raw:
                    return body
                try:
                    return json.loads(body.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    # 例如被网络代理/登录页替换成了 HTML：按网络错误重试，最后作为这一项的失败记录
                    if net_retries < 4:
                        net_retries += 1
                        self.log("  返回的不是 JSON，%d 秒后重试（第 %d/4 次）%s" % (2 ** net_retries, net_retries,
                                                                           "（%s）" % label if label else ""))
                        time.sleep(2 ** net_retries)
                        continue
                    raise HTTPFailure(0, "返回的内容不是合法 JSON")
            except urllib.error.HTTPError as e:
                status = e.code
                hdrs = e.headers or {}
                try:
                    detail = e.read()[:300].decode("utf-8", "replace")
                except Exception:
                    detail = ""
                if status == 429:
                    rate_retries += 1
                    if rate_retries > 20:
                        raise HTTPFailure(429, "连续 429 超过 20 次，放弃这个请求")
                    ra = hdrs.get("Retry-After")
                    kind = hdrs.get("X-Rate-Limit-Type", "未说明")
                    try:
                        wait = float(ra) if ra is not None else None
                    except ValueError:
                        wait = None
                    if wait is None:
                        # 服务端限流（service）有时不带 Retry-After，用指数退避
                        wait = min(60.0, 2.0 ** min(rate_retries, 6))
                    self.log("  429 请求太频繁（类型：%s），按要求等待 %.0f 秒后继续%s" %
                             (kind, wait, "（%s）" % label if label else ""))
                    time.sleep(wait + 0.5)
                    continue
                if riot and status in (401, 403):
                    raise FatalAPIError(
                        "HTTP %d：密钥无效、已过期或没有权限。开发者密钥每 24 小时过期，"
                        "请到 https://developer.riotgames.com 重新生成后再运行。" % status)
                if status >= 500 and net_retries < 4:
                    net_retries += 1
                    wait = 2 ** net_retries
                    self.log("  HTTP %d 服务器错误，%d 秒后重试（第 %d/4 次）%s" %
                             (status, wait, net_retries, "（%s）" % label if label else ""))
                    time.sleep(wait)
                    continue
                raise HTTPFailure(status, "HTTP %d %s" % (status, detail.strip()[:200]))
            except (urllib.error.URLError, socket.timeout, OSError, http.client.HTTPException) as e:
                reason = getattr(e, "reason", e)
                if isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(reason):
                    raise FatalAPIError(
                        "SSL 证书校验失败：%s。macOS 上请先运行 /Applications/Python 3.x/Install Certificates.command。" % reason)
                if net_retries < 4:
                    net_retries += 1
                    wait = 2 ** net_retries
                    self.log("  网络错误（%s），%d 秒后重试（第 %d/4 次）%s" %
                             (reason, wait, net_retries, "（%s）" % label if label else ""))
                    time.sleep(wait)
                    continue
                raise HTTPFailure(0, "网络错误：%s" % reason)


class Store:
    """把每个文件写到输出目录，并在 manifest 里记下载时间、接口、大小、sha256。"""

    def __init__(self, root, manifest):
        self.root = root
        self.manifest = manifest

    def write(self, rel, data, endpoint):
        path = os.path.join(self.root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not isinstance(data, (bytes, bytearray)):
            data = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        tmp = path + ".part"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
        now = iso(utc_now())
        self.manifest["files"][rel] = {
            "downloaded_at_utc": now,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "endpoint": endpoint,
        }
        return now

    def existing(self, rel):
        """断点续传：文件已存在且是合法 JSON、manifest 里也有记录，就不重复下载。"""
        path = os.path.join(self.root, *rel.split("/"))
        if not os.path.exists(path) or rel not in self.manifest["files"]:
            return None
        try:
            with open(path, "rb") as fh:
                json.loads(fh.read().decode("utf-8"))
            return self.manifest["files"][rel]["downloaded_at_utc"]
        except Exception:
            return None

    def save_manifest(self):
        path = os.path.join(self.root, "manifest.json")
        tmp = path + ".part"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.manifest, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="下载一位英雄联盟玩家最近的单双排对局数据（Riot API + Data Dragon）")
    p.add_argument("--riot-id", default=DEFAULT_RIOT_ID, help="游戏名#Tag（默认：%(default)s）")
    p.add_argument("--platform", default=DEFAULT_PLATFORM,
                   help="服务器：auto（自动查，查不到用 euw1）、euw1、eun1、kr ……（默认 auto）")
    p.add_argument("--count", type=int, default=20,
                   help="下载最近多少场有效单双排（默认 20，最多 95；重开局不算，会自动多取几场补足）")
    p.add_argument("--out", default=None, help="输出目录（默认 ./lol_data_<时间>）")
    p.add_argument("--no-opponent-ranks", action="store_true",
                   help="不查每场对面同位置玩家的当前段位（默认会查，约多 20 次请求）")
    p.add_argument("--no-zip", action="store_true", help="只下载，不打包 zip")
    p.add_argument("--rate", default="20:1,100:120",
                   help="客户端限速，次数:秒，逗号分隔（默认开发者密钥：20:1,100:120）")
    # 下面两个只在本地测试时使用（指向模拟服务器），正常运行不要填
    p.add_argument("--api-base", default=None, help=argparse.SUPPRESS)
    p.add_argument("--ddragon-base", default=None, help=argparse.SUPPRESS)
    return p.parse_args(argv)


def split_riot_id(riot_id):
    if "#" not in riot_id:
        raise SystemExit("游戏名格式应为 名字#Tag，例如 %s" % DEFAULT_RIOT_ID)
    name, tag = riot_id.rsplit("#", 1)
    name, tag = name.strip(), tag.strip()
    if not name or not tag:
        raise SystemExit("游戏名或 Tag 为空：%r" % riot_id)
    return name, tag


def q(s):
    return urllib.parse.quote(s, safe="")


def patch_of(game_version):
    """'16.20.712.1234' -> '16.20'"""
    m = re.match(r"^(\d+)\.(\d+)", game_version or "")
    return "%s.%s" % (m.group(1), m.group(2)) if m else None


def clean_key(raw):
    """去掉首尾空格和引号（cmd 的 set 会把引号也存进去）；只接受可打印 ASCII、无空格的密钥。不回显密钥。"""
    k = (raw or "").strip().strip('"').strip("'").strip()
    if not k or not re.fullmatch(r"[\x21-\x7e]+", k):
        return None
    return k


def is_remake(match):
    info = (match or {}).get("info") or {}
    dur = info.get("gameDuration") or 0
    return any(p.get("gameEndedInEarlySurrender") for p in info.get("participants") or []) or (0 < dur < 300)


def main(argv=None):
    args = parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(errors="replace")
        except Exception:
            pass

    raw_key = os.environ.get("RIOT_API_KEY", "")
    if not raw_key.strip():
        print("没有找到环境变量 RIOT_API_KEY。\n"
              "  Windows PowerShell： $env:RIOT_API_KEY=\"你的密钥\"\n"
              "  Windows cmd：        set RIOT_API_KEY=你的密钥\n"
              "  macOS / Linux：      export RIOT_API_KEY=你的密钥\n"
              "密钥在 https://developer.riotgames.com 登录后获取（开发者密钥 24 小时过期）。")
        return 2
    api_key = clean_key(raw_key)
    if not api_key:
        print("环境变量 RIOT_API_KEY 的格式不对（含空格、换行或非英文字符）。请重新复制 RGAPI- 开头的密钥再设置一次。")
        return 2

    game_name, tag_line = split_riot_id(args.riot_id)
    count = max(1, min(95, args.count))
    started = utc_now()
    out = os.path.abspath(args.out or "lol_data_%s" % started.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(out, exist_ok=True)

    manifest_path = os.path.join(out, "manifest.json")
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        manifest.setdefault("runs", []).append({"started_at_utc": iso(started)})
        manifest["failures"] = []
    else:
        manifest = {
            "tool": "download_riot.py",
            "tool_version": TOOL_VERSION,
            "riot_id": "%s#%s" % (game_name, tag_line),
            "game_name": game_name,
            "tag_line": tag_line,
            "queue": RANKED_SOLO_QUEUE,
            "count_requested": count,
            "started_at_utc": iso(started),
            "local_timezone": str(dt.datetime.now().astimezone().tzinfo),
            "matches": {},
            "failures": [],
            "files": {},
            "notes": [
                "API 密钥只从环境变量读取，没有写入任何文件。",
                "所有时间为 UTC。段位是下载当时的段位，不是对局当时的段位。",
                "重开局（提前投降/不足 5 分钟）不计入 %d 场，会多取几场补足；重开局不下载时间线。" % count,
            ],
        }
    store = Store(out, manifest)
    log = Logger(os.path.join(out, "download_log.txt"))
    client = Client(api_key, log, args.rate, args.api_base, args.ddragon_base)

    def fail(what, match_id, err):
        manifest["failures"].append({"what": what, "match_id": match_id, "error": str(err),
                                     "at_utc": iso(utc_now())})
        log("  ✗ %s %s 失败：%s（已跳过并记录）" % (what, match_id or "", err))

    def optional(what, fn):
        """可选步骤：任何错误（包括 401/403）都只记录，不中止。"""
        try:
            return fn()
        except (HTTPFailure, FatalAPIError) as e:
            fail(what, None, e)
        except Exception as e:  # 响应格式异常等
            fail(what, None, "%s: %s" % (type(e).__name__, e))
        return None

    fatal = None
    interrupted = False
    patches = set()
    processed = []
    ranks = {}
    try:
        # ---------- 1. account-v1 ----------
        platform = args.platform.lower()
        acc_region = ACCOUNT_REGION.get(PLATFORM_TO_REGION.get(platform, "europe"), "europe") if platform != "auto" else "europe"
        log("查询账号 %s#%s（account-v1，%s）" % (game_name, tag_line, acc_region))
        try:
            account = client.get("%s.api.riotgames.com" % acc_region,
                                 "/riot/account/v1/accounts/by-riot-id/%s/%s" % (q(game_name), q(tag_line)),
                                 label="account")
        except HTTPFailure as e:
            if e.status == 404:
                log("找不到这个账号：请检查游戏名和 Tag（区分空格，Tag 不带 #）。")
                return 3
            raise
        puuid = account["puuid"]
        store.write("account.json", account, "account-v1 /riot/account/v1/accounts/by-riot-id/{gameName}/{tagLine}")
        manifest["puuid"] = puuid
        log("  找到账号：%s#%s" % (account.get("gameName", game_name), account.get("tagLine", tag_line)))

        if platform == "auto":
            platform_source = "默认值（欧服西部 euw1）"
            platform = FALLBACK_PLATFORM
            reg = optional("region", lambda: client.get(
                "%s.api.riotgames.com" % acc_region,
                "/riot/account/v1/region/by-game/lol/by-puuid/%s" % q(puuid), label="region"))
            if isinstance(reg, dict):
                store.write("account_region.json", reg, "account-v1 /riot/account/v1/region/by-game/lol/by-puuid/{puuid}")
                if str(reg.get("region", "")).lower() in PLATFORM_TO_REGION:
                    platform = reg["region"].lower()
                    platform_source = "account-v1 region/by-game/lol 接口"
            else:
                log("  查不到所在服务器，按欧服西部 euw1 处理")
        else:
            platform_source = "命令行参数 --platform"
        region = PLATFORM_TO_REGION.get(platform, "europe")
        manifest.update({"platform": platform, "region": region, "platform_source": platform_source})
        log("  服务器：%s（来源：%s），对局路由：%s" % (platform, platform_source, region))
        store.save_manifest()

        # ---------- 2. league-v4（可选） ----------
        log("查询当前段位（league-v4）")
        entries = optional("league", lambda: client.get("%s.api.riotgames.com" % platform,
                                                        "/lol/league/v4/entries/by-puuid/%s" % q(puuid), label="league"))
        if isinstance(entries, list):
            store.write("league_entries.json", entries, "league-v4 /lol/league/v4/entries/by-puuid/{puuid}")
            solo = [x for x in entries if x.get("queueType") == "RANKED_SOLO_5x5"]
            if solo:
                s = solo[0]
                log("  单双排：%s %s %s 胜点，%s 胜 %s 负" % (s.get("tier"), s.get("rank"), s.get("leaguePoints"),
                                                     s.get("wins"), s.get("losses")))
            else:
                log("  本赛季没有单双排段位记录（可能未定级）")

        # ---------- 3. match-v5 ids（多取几场，用来补重开局） ----------
        fetch_n = min(100, count + 5)
        log("获取最近的单双排比赛 ID（match-v5，取 %d 个，用前 %d 场有效对局）" % (fetch_n, count))
        ids = client.get("%s.api.riotgames.com" % region,
                         "/lol/match/v5/matches/by-puuid/%s/ids" % q(puuid),
                         query={"queue": RANKED_SOLO_QUEUE, "start": 0, "count": fetch_n}, label="match ids")
        if not isinstance(ids, list):
            raise HTTPFailure(0, "比赛 ID 列表格式不对")
        store.write("match_ids.json", ids,
                    "match-v5 /lol/match/v5/matches/by-puuid/{puuid}/ids?queue=420&start=0&count=%d" % fetch_n)
        log("  共 %d 个" % len(ids))
        store.save_manifest()

        # ---------- 4. 每场对局 + 时间线 ----------
        lane_opponents = {}
        valid = 0
        for mid in ids:
            if valid >= count:
                break
            processed.append(mid)
            manifest["match_ids"] = processed
            try:
                match = _download_one(client, store, manifest, log, fail, region, out, mid, len(processed))
            except FatalAPIError:
                raise
            except Exception as e:  # 单场的任何意外都只跳过这一场
                fail("match", mid, "%s: %s" % (type(e).__name__, e))
                match = None
            rec = manifest["matches"].get(mid, {})
            if match is None:
                valid += 1  # 下载失败的也占一个名额（"最近 N 场"），不往后补
                continue
            if rec.get("remake"):
                continue  # 重开局不占名额
            valid += 1
            info = match.get("info", {})
            p = patch_of(info.get("gameVersion"))
            if p:
                patches.add(p)
            me = next((x for x in info.get("participants", []) if x.get("puuid") == puuid), None)
            if me and me.get("teamPosition"):
                opp = next((x for x in info["participants"] if x.get("teamId") != me.get("teamId")
                            and x.get("teamPosition") == me.get("teamPosition")), None)
                if opp and opp.get("puuid"):
                    lane_opponents[opp["puuid"]] = mid
            store.save_manifest()

        # ---------- 5. 对面同位置玩家的当前段位（可选） ----------
        if not args.no_opponent_ranks and lane_opponents:
            log("查询每场对面同位置玩家的当前段位（league-v4，%d 人）" % len(lane_opponents))
            for opp_puuid, mid in lane_opponents.items():
                ent = optional("lane_opponent_rank", lambda: client.get(
                    "%s.api.riotgames.com" % platform, "/lol/league/v4/entries/by-puuid/%s" % q(opp_puuid),
                    label="rank " + mid))
                if isinstance(ent, list):
                    ranks[opp_puuid] = {"fetched_at_utc": iso(utc_now()), "match_id": mid, "entries": ent}

    except (FatalAPIError, HTTPFailure) as e:
        fatal = str(e)
        log("✗ 停止：%s" % fatal)
        manifest["failures"].append({"what": "fatal", "match_id": None, "error": fatal, "at_utc": iso(utc_now())})
    except KeyboardInterrupt:
        interrupted = True
        log("✗ 已中断。已下载的文件保留，用 --out 指向同一目录重新运行可以继续。")
    finally:
        if ranks:
            store.write("lane_opponent_ranks.json", ranks, "league-v4 /lol/league/v4/entries/by-puuid/{puuid}（对面同位置玩家）")
        store.save_manifest()

    # ---------- 6. Data Dragon（不需要密钥） ----------
    if not interrupted:
        try:
            download_ddragon(client, store, manifest, log, sorted(patches))
        except Exception as e:  # Data Dragon 失败不影响对局数据
            fail("ddragon", None, e)

    finished = utc_now()
    manifest["finished_at_utc"] = iso(finished)
    cur = [manifest["matches"].get(m, {}) for m in processed]
    ok_m = sum(1 for r in cur if r.get("match") == "ok")
    ok_t = sum(1 for r in cur if r.get("timeline") == "ok")
    remakes = sum(1 for r in cur if r.get("remake"))
    stale = [m for m in manifest["matches"] if m not in processed]
    manifest["summary"] = {"matches_processed": len(processed), "matches_ok": ok_m, "timelines_ok": ok_t,
                           "remakes": remakes, "failures": len(manifest["failures"]),
                           "requests": client.request_count, "stale_entries_from_earlier_runs": stale}
    store.save_manifest()
    log("完成：对局详情 %d 场（其中重开局 %d 场），时间线 %d 场，失败 %d 项，用时 %.0f 秒" %
        (ok_m, remakes, ok_t, len(manifest["failures"]), (finished - started).total_seconds()))
    log.close()

    if not args.no_zip and not interrupted:
        zpath = out.rstrip("/\\") + ".zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(out):
                for f in sorted(files):
                    if f.endswith(".part"):
                        continue
                    full = os.path.join(root, f)
                    zf.write(full, os.path.relpath(full, os.path.dirname(out)))
        print("已打包：%s（%.1f MB）" % (zpath, os.path.getsize(zpath) / 1e6))
        print("把这个 zip 文件发回来即可。")
    return 1 if (fatal or interrupted) else 0


def _download_one(client, store, manifest, log, fail, region, out, mid, n):
    """下载一场的详情和时间线；失败只记录。返回对局详情（失败返回 None）。"""
    rec = manifest["matches"].setdefault(mid, {})
    rel_m, rel_t = "matches/%s.json" % mid, "timelines/%s.json" % mid
    log("[%d] %s" % (n, mid))
    match = None
    cached = store.existing(rel_m)
    if cached:
        rec.update({"match": "ok", "match_downloaded_at_utc": cached})
        with open(os.path.join(out, "matches", mid + ".json"), encoding="utf-8") as fh:
            match = json.load(fh)
        log("  对局详情已存在，跳过")
    else:
        try:
            match = client.get("%s.api.riotgames.com" % region, "/lol/match/v5/matches/%s" % q(mid), label=mid)
            if not isinstance(match, dict) or "info" not in match:
                raise HTTPFailure(0, "对局详情格式不对（没有 info）")
            rec["match"] = "ok"
            rec.pop("match_error", None)
            rec["match_downloaded_at_utc"] = store.write(rel_m, match, "match-v5 /lol/match/v5/matches/{matchId}")
        except HTTPFailure as e:
            rec["match"] = "failed"
            rec["match_error"] = str(e)
            fail("match", mid, e)
            return None
    rec["remake"] = bool(is_remake(match))
    if rec["remake"]:
        rec["timeline"] = "skipped"
        log("  重开局：不计入场数，不下载时间线")
        return match
    cached = store.existing(rel_t)
    if cached:
        rec.update({"timeline": "ok", "timeline_downloaded_at_utc": cached})
        log("  时间线已存在，跳过")
    else:
        try:
            tl = client.get("%s.api.riotgames.com" % region, "/lol/match/v5/matches/%s/timeline" % q(mid),
                            label=mid + " timeline")
            if not isinstance(tl, dict) or "info" not in tl:
                raise HTTPFailure(0, "时间线格式不对（没有 info）")
            rec["timeline"] = "ok"
            rec.pop("timeline_error", None)
            rec["timeline_downloaded_at_utc"] = store.write(rel_t, tl, "match-v5 /lol/match/v5/matches/{matchId}/timeline")
        except HTTPFailure as e:
            rec["timeline"] = "failed"
            rec["timeline_error"] = str(e)
            fail("timeline", mid, e)
    return match


def download_ddragon(client, store, manifest, log, match_patches):
    log("下载 Data Dragon 静态数据（英雄、装备、小地图）")
    versions = client.get(DDRAGON_HOST, "/api/versions.json", riot=False, label="versions")
    latest = versions[0]
    wanted = [latest]
    match_versions = {}
    for p in match_patches:  # 对局所在版本的装备数据也下一份，判断“成装”更准
        v = next((x for x in versions if x.startswith(p + ".")), None)
        if v:
            match_versions[p] = v
            if v not in wanted:
                wanted.append(v)
    for ver in wanted:
        files = [("champion_zh_CN.json", "/cdn/%s/data/zh_CN/champion.json" % ver),
                 ("champion_en_US.json", "/cdn/%s/data/en_US/champion.json" % ver),
                 ("item_zh_CN.json", "/cdn/%s/data/zh_CN/item.json" % ver),
                 ("item_en_US.json", "/cdn/%s/data/en_US/item.json" % ver)]
        if ver == latest:
            files.append(("map11.png", "/cdn/%s/img/map/map11.png" % ver))
            # 完整英雄数据（技能名、冷却、消耗、距离），分析时从这里读技能数值
            files.append(("championFull_zh_CN.json", "/cdn/%s/data/zh_CN/championFull.json" % ver))
            files.append(("championFull_en_US.json", "/cdn/%s/data/en_US/championFull.json" % ver))
        for name, path in files:
            rel = "ddragon/%s/%s" % (ver, name)
            if rel in manifest["files"] and os.path.exists(os.path.join(store.root, *rel.split("/"))):
                continue
            try:
                body = client.get(DDRAGON_HOST, path, riot=False, raw=True, label=name)
                store.write(rel, body, "Data Dragon %s" % path)
            except HTTPFailure as e:
                manifest["failures"].append({"what": "ddragon", "match_id": None, "error": "%s: %s" % (path, e),
                                             "at_utc": iso(utc_now())})
                log("  ✗ %s 下载失败：%s" % (path, e))
    meta = {"latest_version": latest, "versions": wanted, "match_patch_versions": match_versions,
            "versions_json_head": versions[:10]}
    store.write("ddragon/meta.json", meta, "Data Dragon /api/versions.json")
    manifest["ddragon"] = {"latest_version": latest, "versions_downloaded": wanted,
                           "map_image": "ddragon/%s/map11.png" % latest}
    log("  Data Dragon 当前版本 %s；对局版本对应：%s" % (latest, match_versions or "无"))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已中断。已下载的文件保留在输出目录，重新运行并用 --out 指向同一目录可以继续。")
        sys.exit(130)
