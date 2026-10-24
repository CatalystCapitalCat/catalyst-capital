#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对手分析：download_riot.py 打出来的 zip → CSV → 汇总 → HTML 仪表板（只用标准库）。

  python analyze.py all  数据.zip --out 输出目录 [--strategy strategy.json]
  python analyze.py parse 数据.zip --out 输出目录      # 只解析一次原始 JSON → CSV
  python analyze.py stats --out 输出目录               # 只读 CSV，打印汇总、写 summary.json
  python analyze.py calibrate --out 输出目录           # 区域划分核对（写 calibration.json）
  python analyze.py report --out 输出目录 [--strategy strategy.json]

原始 JSON 只在 parse 读一次，不打印内容；之后都读 CSV。
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lolanalysis import geometry as G  # noqa: E402
from lolanalysis import parse as P  # noqa: E402
from lolanalysis import report as R  # noqa: E402
from lolanalysis import stats as ST  # noqa: E402
from lolanalysis.dataset import open_dataset  # noqa: E402
from lolanalysis.staticdata import COMPLETED_RULE, StaticData  # noqa: E402

# 我的英雄池：武器大师、荒漠屠夫（鳄鱼）、机械公敌（兰博）、铁血狼母（安蓓萨）
MY_POOL = "Jax,Renekton,Rumble,Ambessa"
# 最近两个版本的官方公告/Wiki 摘录（带版本号和链接）
DEFAULT_PATCH_NOTES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "patch_notes_26.20-26.21.json")


def ds_meta(ds):
    m = ds.manifest
    solo = None
    if isinstance(ds.league, list):
        solo = next((e for e in ds.league if e.get("queueType") == "RANKED_SOLO_5x5"), None)
    fin = m.get("finished_at_utc") or m.get("started_at_utc") or ""
    return {
        "riot_id": "%s#%s" % (ds.account.get("gameName") or m.get("game_name"), ds.account.get("tagLine") or m.get("tag_line")),
        "game_name": ds.account.get("gameName") or m.get("game_name"), "puuid": ds.puuid,
        "platform": m.get("platform"), "platform_source": m.get("platform_source"),
        "started_at_utc": m.get("started_at_utc"), "finished_at_utc": m.get("finished_at_utc"),
        "downloaded_date": fin[:10] or "?", "ddragon_latest": (ds.ddragon_meta or {}).get("latest_version"),
        "failures": m.get("failures", []), "synthetic": bool(m.get("synthetic")),
        "rank": solo, "lane_opp_ranks": ds.lane_opp_ranks, "item_rule": COMPLETED_RULE,
        "league_downloaded_at_utc": ds.file_meta("league_entries.json").get("downloaded_at_utc"),
    }


def cmd_parse(args):
    os.makedirs(args.out, exist_ok=True)
    ds = open_dataset(args.data, args.out)
    static = StaticData(ds)
    plog = P.parse_dataset(ds, static, args.out)
    P.write_refdata(static, plog, [c.strip() for c in args.my_pool.split(",") if c.strip()],
                    os.path.join(args.out, "csv"))
    meta = ds_meta(ds)
    meta["my_pool"] = [c.strip() for c in args.my_pool.split(",") if c.strip()]
    with open(os.path.join(args.out, "ds_meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)
    mp = ds.map_image()
    with open(os.path.join(args.out, "map_path.txt"), "w", encoding="utf-8") as fh:
        fh.write(mp or "")
    bad = G.self_test()
    if bad:
        print("!! 区域划分地标自检失败：%s" % bad)


def _load_meta(out):
    with open(os.path.join(out, "ds_meta.json"), encoding="utf-8") as fh:
        return json.load(fh)


def cmd_stats(args):
    ST.compute(args.out, _load_meta(args.out))


def cmd_calibrate(args, quiet=False):
    frames = ST.read_csv(os.path.join(args.out, "csv", "frames.csv"))
    parts = ST.read_csv(os.path.join(args.out, "csv", "participants.csv"))
    games = [g for g in ST.read_csv(os.path.join(args.out, "csv", "games.csv")) if g["included"] == 1 and g["has_timeline"] == 1]
    pos = {(p["match_id"], p["pid"]): p["position"] for p in parts}
    inc = {g["match_id"] for g in games}
    want = {"TOP": "TOP", "MIDDLE": "MID", "BOTTOM": "BOT", "UTILITY": "BOT"}
    tally = {}
    jg = [0, 0]
    for f in frames:
        if f["match_id"] not in inc or not f["zone"]:
            continue
        p = pos.get((f["match_id"], f["pid"]), "")
        if p in want and 2 <= f["minute"] <= 12:
            t = tally.setdefault(p, [0, 0, 0, 0])
            t[0] += 1
            t[1] += f["zone"] == want[p]
            t[2] += f["zone"].startswith("JG_")
            t[3] += f["zone"].startswith("RIVER") or f["zone"].endswith("_PIT")
        if p == "JUNGLE" and 1 <= f["minute"] <= 3:
            jg[0] += 1
            jg[1] += f["zone"].startswith("JG_")
    sample = sorted(inc)[: args.games]
    pts = [{"match_id": f["match_id"], "minute": f["minute"], "x": f["x"], "y": f["y"], "zone": f["zone"],
            "position": pos.get((f["match_id"], f["pid"]), "")}
           for f in frames if f["match_id"] in sample and 1 <= f["minute"] <= 14 and f["x"] is not None]
    calib = {"n_games": len(sample), "games": sample, "points": pts,
             "lane_check": [{"position": p, "n": t[0], "in_lane": t[1] / t[0], "in_jungle": t[2] / t[0], "in_river": t[3] / t[0]}
                            for p, t in sorted(tally.items()) if t[0]],
             "jungler_in_jungle": jg[1] / jg[0] if jg[0] else None, "jungler_n": jg[0], "selftest": G.self_test()}
    with open(os.path.join(args.out, "calibration.json"), "w", encoding="utf-8") as fh:
        json.dump(calib, fh, ensure_ascii=False)
    if not quiet:
        print("区域核对（对线期 2–12 分钟位置落在自己那条线的比例）：")
        for r in calib["lane_check"]:
            print("  %-8s %4d 点  线上 %3.0f%%  野区 %3.0f%%  河道 %3.0f%%" % (
                r["position"], r["n"], 100 * r["in_lane"], 100 * r["in_jungle"], 100 * r["in_river"]))
        if calib["jungler_in_jungle"] is not None:
            print("  打野 1–3 分钟在野区：%.0f%%（%d 点）" % (100 * calib["jungler_in_jungle"], jg[0]))
        print("  地标自检失败：%s" % (calib["selftest"] or "无"))
    return calib


def cmd_report(args):
    with open(os.path.join(args.out, "summary.json"), encoding="utf-8") as fh:
        S = json.load(fh)
    strategy = None
    if args.strategy:
        with open(args.strategy, encoding="utf-8") as fh:
            strategy = json.load(fh)
    cpath = os.path.join(args.out, "calibration.json")
    calib = None
    if os.path.exists(cpath):
        with open(cpath, encoding="utf-8") as fh:
            calib = json.load(fh)
    with open(os.path.join(args.out, "map_path.txt"), encoding="utf-8") as fh:
        mp = fh.read().strip() or None
    notes = None
    if args.patch_notes:
        with open(args.patch_notes, encoding="utf-8") as fh:
            notes = json.load(fh)
    full, frag = R.render(S, strategy, mp, args.out, calib, notes)
    print("仪表板：%s（%.0f KB）" % (full, os.path.getsize(full) / 1024))


def main(argv=None):
    ap = argparse.ArgumentParser(description="英雄联盟对手分析")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("all", "parse"):
        p = sub.add_parser(name)
        p.add_argument("data", help="download_riot.py 打出来的 zip 或解压后的目录")
        p.add_argument("--my-pool", default=MY_POOL,
                       help="我的英雄池（Data Dragon 英雄 id，逗号分隔；默认：%(default)s）")
        p.add_argument("--out", required=True)
        p.add_argument("--strategy", default=None)
        p.add_argument("--patch-notes", default=DEFAULT_PATCH_NOTES if os.path.exists(DEFAULT_PATCH_NOTES) else None, help="版本公告摘录（JSON，见 patch_notes.example.json）")
        p.add_argument("--games", type=int, default=3)
    for name in ("stats", "calibrate", "report"):
        p = sub.add_parser(name)
        p.add_argument("--out", required=True)
        p.add_argument("--strategy", default=None)
        p.add_argument("--patch-notes", default=DEFAULT_PATCH_NOTES if os.path.exists(DEFAULT_PATCH_NOTES) else None, help="版本公告摘录（JSON，见 patch_notes.example.json）")
        p.add_argument("--games", type=int, default=3, help="核对图画几场（calibrate）")
    args = ap.parse_args(argv)
    if args.cmd == "parse":
        cmd_parse(args)
    elif args.cmd == "stats":
        cmd_stats(args)
    elif args.cmd == "calibrate":
        cmd_calibrate(args)
    elif args.cmd == "report":
        cmd_report(args)
    elif args.cmd == "all":
        cmd_parse(args)
        cmd_calibrate(args)
        cmd_stats(args)
        cmd_report(args)


if __name__ == "__main__":
    main()
