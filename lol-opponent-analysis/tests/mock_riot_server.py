#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SYNTHETIC TEST MOCK of the Riot Games API and Data Dragon -- test-only.

Serves a fixture dataset produced by tests/make_fixture.py (or the real
downloader's layout) over plain HTTP on 127.0.0.1 so the downloader can be run
with  --api-base http://127.0.0.1:N --ddragon-base http://127.0.0.1:N .
Routing is on the request PATH only; the optional X-Host header (real host
name) is only logged.

Python 3 standard library only.

Usage:
  python3 mock_riot_server.py --data DIR --port N
      [--inject-429 N] [--inject-503-match ID]... [--inject-404-timeline ID]...
      [--inject-429-no-retry-after] [--alias "Name#Tag"]...

Riot endpoints (require header X-Riot-Token; missing -> 401, "EXPIRED" -> 403):
  /riot/account/v1/accounts/by-riot-id/{gameName}/{tagLine}
  /riot/account/v1/accounts/by-puuid/{puuid}
  /riot/account/v1/region/by-game/lol/by-puuid/{puuid}
  /lol/league/v4/entries/by-puuid/{puuid}
  /lol/match/v5/matches/by-puuid/{puuid}/ids?queue=&type=&start=&count=&startTime=&endTime=
  /lol/match/v5/matches/{matchId}
  /lol/match/v5/matches/{matchId}/timeline
Data Dragon endpoints (no auth):
  /api/versions.json
  /cdn/{ver}/data/{locale}/champion.json | item.json | championFull.json
  /cdn/{ver}/img/map/map11.png

Fault injection:
  --inject-429 N                first N authorised Riot requests -> 429
                                (Retry-After: 1, X-Rate-Limit-Type: application)
  --inject-503-match ID         GET /lol/match/v5/matches/ID -> 503 every time
  --inject-404-timeline ID      GET /lol/match/v5/matches/ID/timeline -> 404
  --inject-429-no-retry-after   one service-level 429 (X-Rate-Limit-Type: service,
                                no Retry-After) on the first match-detail
                                request that is not already answered by
                                --inject-429.
One line per request (method, path, status) is logged to stderr. The
X-Riot-Token value is never logged.
"""

import argparse
import hashlib
import json
import os
import signal
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_ALIASES = ["原州市的努力的天才超级打野李承勇#VALOR"]
QUEUE_RANKED_SOLO = 420
TIERS = ["IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND", "MASTER"]


def riot_error(code, message):
    return {"status": {"message": message, "status_code": code}}


class Fixture(object):
    """Read-only index over a dataset directory."""

    def __init__(self, data_dir, aliases):
        self.dir = os.path.abspath(data_dir)
        j = lambda rel: json.load(open(os.path.join(self.dir, rel), encoding="utf-8"))
        self.account = j("account.json")
        self.region = j("account_region.json")
        self.target_entries = j("league_entries.json")
        self.target_ids = j("match_ids.json")
        rp = os.path.join(self.dir, "lane_opponent_ranks.json")
        self.ranks = j("lane_opponent_ranks.json") if os.path.isfile(rp) else {}
        self.meta = j(os.path.join("ddragon", "meta.json"))
        self.target = self.account["puuid"]
        self.accounts = {}
        self._add_account(self.account["gameName"], self.account["tagLine"], self.target)
        for a in aliases:
            name, _, tag = a.rpartition("#")
            if name and tag:
                self._add_account(name, tag, self.target)
        # index matches -> participants / start time
        self.match_start = {}
        self.by_puuid = {}
        self.names = {}
        mdir = os.path.join(self.dir, "matches")
        for fn in sorted(os.listdir(mdir)) if os.path.isdir(mdir) else []:
            if not fn.endswith(".json"):
                continue
            m = json.load(open(os.path.join(mdir, fn), encoding="utf-8"))
            mid = m["metadata"]["matchId"]
            info = m["info"]
            self.match_start[mid] = info.get("gameStartTimestamp", info.get("gameCreation", 0))
            for p in info["participants"]:
                self.by_puuid.setdefault(p["puuid"], set()).add(mid)
                if p["puuid"] != self.target and p.get("riotIdGameName"):
                    self.names[p["puuid"]] = (p["riotIdGameName"], p.get("riotIdTagline", ""))
        for puuid, (n, t) in self.names.items():
            self._add_account(n, t, puuid)

    def _add_account(self, name, tag, puuid):
        self.accounts[(name.casefold(), tag.casefold())] = {"puuid": puuid, "gameName": name, "tagLine": tag}

    def known(self, puuid):
        return puuid == self.target or puuid in self.by_puuid

    def account_by_puuid(self, puuid):
        if puuid == self.target:
            return dict(self.account)
        if puuid in self.names:
            n, t = self.names[puuid]
            return {"puuid": puuid, "gameName": n, "tagLine": t}
        return None

    def match_ids(self, puuid):
        if puuid == self.target:
            return list(self.target_ids)
        ids = self.by_puuid.get(puuid, set())
        return sorted(ids, key=lambda m: self.match_start.get(m, 0), reverse=True)

    def league(self, puuid):
        if puuid == self.target:
            return self.target_entries
        if puuid in self.ranks:
            return self.ranks[puuid]["entries"]
        if puuid not in self.by_puuid:
            return []
        h = hashlib.sha256(("mock-rank:" + puuid).encode()).digest()
        if h[0] < 20:
            return []           # some players are unranked
        tier = TIERS[3 + h[1] % 5]
        rank = "I" if tier == "MASTER" else ["IV", "III", "II", "I"][h[2] % 4]
        lid = hashlib.md5(("mock-league:" + puuid).encode()).hexdigest()
        return [{"freshBlood": False, "hotStreak": bool(h[3] % 2), "inactive": False,
                 "leagueId": "%s-%s-%s-%s-%s" % (lid[:8], lid[8:12], lid[12:16], lid[16:20], lid[20:]),
                 "leaguePoints": h[4] % 100, "losses": 20 + h[5] % 150, "puuid": puuid,
                 "queueType": "RANKED_SOLO_5x5", "rank": rank, "tier": tier, "veteran": False,
                 "wins": 20 + h[6] % 150}]

    def file(self, *parts):
        p = os.path.normpath(os.path.join(self.dir, *parts))
        if not p.startswith(self.dir + os.sep) or not os.path.isfile(p):
            return None
        with open(p, "rb") as f:
            return f.read()


class State(object):
    def __init__(self, args):
        self.lock = threading.Lock()
        self.app429_left = max(0, args.inject_429)
        self.service429_pending = bool(args.inject_429_no_retry_after)
        self.fail503 = set(args.inject_503_match or [])
        self.fail404tl = set(args.inject_404_timeline or [])
        self.app_count = 0


def make_handler(fx, state):
    class Handler(BaseHTTPRequestHandler):
        server_version = "SyntheticRiotMock/0.1"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):   # silence default logging (it would include nothing secret,
            pass                             # but we want exactly one line per request)

        def do_GET(self):
            self._handle(head=False)

        def do_HEAD(self):
            self._handle(head=True)

        # ------------------------------------------------------------
        def _send(self, status, body, ctype="application/json;charset=utf-8", headers=None, head=False):
            if not isinstance(body, (bytes, bytearray)):
                body = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if not head:
                self.wfile.write(body)
            self._status = status

        def _handle(self, head):
            self._status = 0
            raw_path, _, query = self.path.partition("?")
            try:
                self._route(raw_path, query, head)
            except Exception as e:   # never crash the server on a bad request
                self._send(500, riot_error(500, "Internal server error (mock): %s" % type(e).__name__), head=head)
            host = self.headers.get("X-Host")
            sys.stderr.write("%s %s %s%s -> %d%s\n" % (
                time.strftime("%H:%M:%S"), self.command, raw_path, ("?" + query) if query else "",
                self._status, (" [X-Host: %s]" % host) if host else ""))
            sys.stderr.flush()

        def _route(self, raw_path, query, head):
            segs = [urllib.parse.unquote(s, encoding="utf-8", errors="strict") for s in raw_path.split("/")[1:]]
            qs = urllib.parse.parse_qs(query, keep_blank_values=True)
            if segs and segs[0] in ("riot", "lol"):
                return self._riot(segs, qs, head)
            return self._ddragon(segs, head)

        # ------------------------------------------------------------ Riot
        def _riot(self, segs, qs, head):
            token = self.headers.get("X-Riot-Token")
            if not token:
                return self._send(401, riot_error(401, "Unauthorized"), head=head)
            if token == "EXPIRED":
                return self._send(403, riot_error(403, "Forbidden"), head=head)
            is_detail = (len(segs) == 5 and segs[:4] == ["lol", "match", "v5", "matches"] and segs[4] != "by-puuid")
            with state.lock:
                state.app_count += 1
                n = state.app_count
                if state.app429_left > 0:
                    state.app429_left -= 1
                    inject = "app"
                elif state.service429_pending and is_detail:
                    state.service429_pending = False
                    inject = "service"
                else:
                    inject = None
            rl = {"X-App-Rate-Limit": "20:1,100:120", "X-App-Rate-Limit-Count": "%d:1,%d:120" % (min(n, 20), min(n, 100)),
                  "X-Method-Rate-Limit": "2000:60", "X-Method-Rate-Limit-Count": "1:60"}
            if inject == "app":
                return self._send(429, riot_error(429, "Rate limit exceeded"), head=head,
                                  headers=dict(rl, **{"Retry-After": "1", "X-Rate-Limit-Type": "application"}))
            if inject == "service":
                return self._send(429, riot_error(429, "Rate limit exceeded"), head=head,
                                  headers={"X-Rate-Limit-Type": "service"})
            nf = lambda msg="Data not found": self._send(404, riot_error(404, msg), headers=rl, head=head)
            ok = lambda body, ctype="application/json;charset=utf-8": self._send(200, body, ctype, rl, head)
            # account-v1
            if segs[:5] == ["riot", "account", "v1", "accounts", "by-riot-id"] and len(segs) == 7:
                acc = fx.accounts.get((segs[5].casefold(), segs[6].casefold()))
                return ok(acc) if acc else nf()
            if segs[:5] == ["riot", "account", "v1", "accounts", "by-puuid"] and len(segs) == 6:
                acc = fx.account_by_puuid(segs[5])
                return ok(acc) if acc else nf()
            if segs[:4] == ["riot", "account", "v1", "region"] and len(segs) == 8 and segs[4] == "by-game" \
                    and segs[6] == "by-puuid":
                if segs[5] != "lol" or not fx.known(segs[7]):
                    return nf()
                return ok({"puuid": segs[7], "game": "lol", "region": fx.region.get("region", "euw1")})
            # league-v4
            if segs[:5] == ["lol", "league", "v4", "entries", "by-puuid"] and len(segs) == 6:
                return ok(fx.league(segs[5]))
            # match-v5
            if segs[:5] == ["lol", "match", "v5", "matches", "by-puuid"] and len(segs) == 7 and segs[6] == "ids":
                try:
                    start = int(qs.get("start", ["0"])[0] or 0)
                    count = int(qs.get("count", ["20"])[0] or 20)
                except ValueError:
                    return self._send(400, riot_error(400, "Bad request - invalid start/count"), headers=rl, head=head)
                if start < 0 or count < 0 or count > 100:
                    return self._send(400, riot_error(400, "Bad request - count must be between 0 and 100"),
                                      headers=rl, head=head)
                ids = fx.match_ids(segs[5])
                queue = qs.get("queue", [""])[0]
                mtype = qs.get("type", [""])[0]
                if (queue and queue != str(QUEUE_RANKED_SOLO)) or (mtype and mtype != "ranked"):
                    ids = []
                st, et = qs.get("startTime", [""])[0], qs.get("endTime", [""])[0]
                if st or et:
                    def in_range(m):
                        ts = fx.match_start.get(m)
                        if ts is None:
                            return True
                        return (not st or ts >= int(st) * 1000) and (not et or ts <= int(et) * 1000)
                    ids = [m for m in ids if in_range(m)]
                return ok(ids[start:start + count])
            if segs[:4] == ["lol", "match", "v5", "matches"] and len(segs) in (5, 6):
                mid = segs[4]
                if len(segs) == 5:
                    if mid in state.fail503:
                        return self._send(503, riot_error(503, "Service Unavailable"), headers=rl, head=head)
                    body = fx.file("matches", mid + ".json") if "/" not in mid else None
                    return ok(body) if body is not None else nf("Data not found - match file not found")
                if segs[5] == "timeline":
                    if mid in state.fail404tl:
                        return nf("Data not found - timeline not found")
                    body = fx.file("timelines", mid + ".json") if "/" not in mid else None
                    return ok(body) if body is not None else nf("Data not found - timeline not found")
            return self._send(404, riot_error(404, "Resource not found"), headers=rl, head=head)

        # ------------------------------------------------------------ Data Dragon
        def _ddragon(self, segs, head):
            if segs == ["api", "versions.json"]:
                vers = fx.meta.get("versions_json_head") or fx.meta.get("versions") or [fx.meta.get("latest_version")]
                return self._send(200, vers, head=head)
            if len(segs) == 5 and segs[0] == "cdn" and segs[2] == "data" and \
                    segs[4] in ("champion.json", "item.json", "championFull.json"):
                ver, loc, kind = segs[1], segs[3], segs[4].split(".")[0]
                body = fx.file("ddragon", ver, "%s_%s.json" % (kind, loc)) if "/" not in ver + loc else None
                if body is not None:
                    return self._send(200, body, head=head)
            if len(segs) == 5 and segs[0] == "cdn" and segs[2:] == ["img", "map", "map11.png"]:
                body = fx.file("ddragon", segs[1], "map11.png") if "/" not in segs[1] else None
                if body is not None:
                    return self._send(200, body, "image/png", head=head)
            return self._send(404, b"Not Found", "text/plain", head=head)

    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(description="SYNTHETIC test-only mock of Riot API + Data Dragon.")
    ap.add_argument("--data", required=True, help="fixture dataset directory")
    ap.add_argument("--port", type=int, required=True, help="TCP port (0 = pick a free one)")
    ap.add_argument("--bind", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    ap.add_argument("--inject-429", type=int, default=0, metavar="N")
    ap.add_argument("--inject-503-match", action="append", metavar="ID")
    ap.add_argument("--inject-404-timeline", action="append", metavar="ID")
    ap.add_argument("--inject-429-no-retry-after", action="store_true")
    ap.add_argument("--alias", action="append", metavar="NAME#TAG",
                    help="extra Riot ID mapped to the target puuid (default: %s)" % DEFAULT_ALIASES)
    args = ap.parse_args(argv)
    fx = Fixture(args.data, args.alias if args.alias else DEFAULT_ALIASES)
    state = State(args)
    httpd = ThreadingHTTPServer((args.bind, args.port), make_handler(fx, state))
    httpd.daemon_threads = True
    port = httpd.server_address[1]
    sys.stderr.write("mock_riot_server (SYNTHETIC) serving %s on http://%s:%d\n" % (fx.dir, args.bind, port))
    sys.stderr.flush()

    def stop(*_):
        threading.Thread(target=httpd.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    try:
        httpd.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
