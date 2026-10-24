#!/usr/bin/env python3
"""端到端测试（只用合成数据）：
  1. make_fixture.py 生成合成数据
  2. mock_riot_server.py 模拟 Riot API + Data Dragon（注入 429、503、404）
  3. download_riot.py 指向模拟服务器下载 → 检查失败记录、zip、密钥没有泄漏
  4. analyze.py all 跑完整分析 → 检查输出，并和 fixture_truth.json 对照

用法：python3 -I tests/run_tests.py --work 临时目录
"""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FAKE_KEY = "RGAPI-test-0000-SECRET-should-never-appear"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def run(cmd, env=None, check=True):
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8")
    if check and r.returncode != 0:
        print("命令失败：", " ".join(cmd))
        print(r.stdout[-3000:])
        print(r.stderr[-3000:])
        raise SystemExit(1)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    args = ap.parse_args()
    work = os.path.abspath(args.work)
    if os.path.exists(work):
        shutil.rmtree(work)
    os.makedirs(work)
    fx_dir = os.path.join(work, "fixture", "data")
    fx_zip = os.path.join(work, "fixture", "fixture.zip")
    run([sys.executable, "-I", os.path.join(HERE, "make_fixture.py"), "--out", fx_dir, "--zip", fx_zip])
    with open(os.path.join(fx_dir, "match_ids.json"), encoding="utf-8") as fh:
        ids = json.load(fh)
    with open(os.path.join(fx_dir, "manifest.json"), encoding="utf-8") as fh:
        fx_manifest = json.load(fh)
    ok_ids = [m for m in ids if fx_manifest["matches"].get(m, {}).get("match") == "ok"
              and fx_manifest["matches"][m].get("timeline") == "ok"]
    m503, t404 = ok_ids[1], ok_ids[2]

    port = free_port()
    log = open(os.path.join(work, "mock.log"), "w")
    srv = subprocess.Popen([sys.executable, "-I", os.path.join(HERE, "mock_riot_server.py"), "--data", fx_dir, "--port", str(port),
                            "--inject-429", "2", "--inject-503-match", m503, "--inject-404-timeline", t404,
                            "--inject-429-no-retry-after"], stdout=log, stderr=log)
    try:
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        env = dict(os.environ, RIOT_API_KEY=FAKE_KEY)
        dl_out = os.path.join(work, "dl", "lol_data_test")
        t0 = time.time()
        r = run([sys.executable, "-I", os.path.join(ROOT, "download_riot.py"), "--riot-id", "TestJungler#TEST",
                 "--out", dl_out, "--api-base", "http://127.0.0.1:%d" % port, "--ddragon-base", "http://127.0.0.1:%d" % port],
                env=env, check=False)
        print("下载脚本退出码 %d，用时 %.0f 秒" % (r.returncode, time.time() - t0))
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
        assert FAKE_KEY not in r.stdout and FAKE_KEY not in r.stderr, "密钥出现在输出里"
        with open(os.path.join(dl_out, "manifest.json"), encoding="utf-8") as fh:
            man = json.load(fh)
        fails = {(f["what"], f["match_id"]) for f in man["failures"]}
        assert ("match", m503) in fails, "503 的对局没有记进 failures：%s" % fails
        assert ("timeline", t404) in fails, "404 的时间线没有记进 failures：%s" % fails
        assert man["matches"][t404]["match"] == "ok"
        assert "429" in r.stdout, "没有看到 429 等待的日志"
        zpath = dl_out + ".zip"
        assert os.path.exists(zpath)
        with zipfile.ZipFile(zpath) as zf:
            for n in zf.namelist():
                assert FAKE_KEY.encode() not in zf.read(n), "密钥出现在 %s" % n
            names = zf.namelist()
        assert any(n.endswith("map11.png") for n in names)
        assert sum(1 for n in names if "/matches/" in n) == len(man["match_ids"]) - sum(
            1 for v in man["matches"].values() if v.get("match") != "ok")
        for rel, meta in man["files"].items():
            assert meta.get("downloaded_at_utc") and meta.get("sha256"), rel
        print("下载测试通过：%d 个文件，失败 %d 项（含注入的 503/404）" % (len(names), len(man["failures"])))

        r0 = run([sys.executable, "-I", os.path.join(ROOT, "download_riot.py"), "--riot-id", "测试用中文名字打野#CN1",
                  "--count", "1", "--no-zip", "--no-opponent-ranks", "--out", os.path.join(work, "dl", "cn_name"),
                  "--api-base", "http://127.0.0.1:%d" % port, "--ddragon-base", "http://127.0.0.1:%d" % port], env=env, check=False)
        assert r0.returncode == 0 and "找到账号" in r0.stdout, r0.stdout[-1500:]
        print("中文游戏名（百分号编码）测试通过")
        # 断点续传：再跑一次同一目录，不应重复下载已有文件
        r2 = run([sys.executable, "-I", os.path.join(ROOT, "download_riot.py"), "--riot-id", "TestJungler#TEST", "--out", dl_out,
                  "--no-zip", "--api-base", "http://127.0.0.1:%d" % port, "--ddragon-base", "http://127.0.0.1:%d" % port],
                 env=env, check=False)
        assert r2.returncode == 0, r2.stdout[-2000:]
        assert "已存在，跳过" in r2.stdout
        print("续传测试通过")
        # 过期密钥 → 停止并给出清楚的提示
        r3 = run([sys.executable, "-I", os.path.join(ROOT, "download_riot.py"), "--riot-id", "TestJungler#TEST",
                  "--out", os.path.join(work, "dl", "expired"),
                  "--no-zip", "--api-base", "http://127.0.0.1:%d" % port, "--ddragon-base", "http://127.0.0.1:%d" % port],
                 env=dict(os.environ, RIOT_API_KEY="EXPIRED"), check=False)
        assert r3.returncode != 0 and "密钥" in r3.stdout, r3.stdout[-1500:]
        print("过期密钥测试通过")
    finally:
        srv.terminate()
        srv.wait(5)
        log.close()

    out = os.path.join(work, "out")
    r = run([sys.executable, "-I", os.path.join(ROOT, "analyze.py"), "all", zpath, "--out", out])
    lines = r.stdout.strip().splitlines()
    print("analyze 输出 %d 行" % len(lines))
    print("\n".join(lines[-60:]))
    for f in ("summary.json", "dashboard.html", "dashboard_fragment.html", "calibration.json", "csv/games.csv", "csv/frames.csv"):
        assert os.path.exists(os.path.join(out, f)), f
    with open(os.path.join(out, "summary.json"), encoding="utf-8") as fh:
        S = json.load(fh)
    truth_path = os.path.join(os.path.dirname(fx_dir), "fixture_truth.json")
    if os.path.exists(truth_path):
        with open(truth_path, encoding="utf-8") as fh:
            truth = json.load(fh)
        check_truth(S, truth, out, excluded_ids={m503})
    print("全部测试通过")


def check_truth(S, truth, out, excluded_ids):
    """和合成数据的真值逐项对照。只对比实际纳入分析的对局（测试里额外注入了 503/404）。"""
    import csv
    problems, notes = [], []

    def rows(name):
        with open(os.path.join(out, "csv", name), encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))

    games = {g["match_id"]: g for g in rows("games.csv") if g["included"] == "1"}
    T = truth["matches"]
    # 1. 胜负、英雄、位置、红蓝方
    for mid, g in games.items():
        t = T.get(mid)
        if not t:
            problems.append("%s 不在真值里" % mid)
            continue
        if int(g["win"]) != int(bool(t["win"])) or g["champ"] != t["champion"] or g["position"] != t["team_position"]:
            problems.append("%s 基本信息不一致：%s/%s/%s vs %s/%s/%s" % (mid, g["win"], g["champ"], g["position"],
                                                                 t["win"], t["champion"], t["team_position"]))
    # 2. 英雄池（打野）
    for c in S["champ_pool"]["rows"]:
        ids = [m for m in c["match_ids"] if T.get(m, {}).get("team_position") == "JUNGLE"]
        tw = sum(1 for m in ids if T[m]["win"])
        if c["wins"] != sum(1 for m in c["match_ids"] if T[m]["win"]):
            problems.append("英雄池 %s 胜场不一致" % c["champ"])
    # 3. 双排
    duo = truth["duo_partner"]
    exp = [m for m in duo["match_ids"] if m in games]
    got = [d for d in S["duo"]["rows"] if d["puuid"] == duo["puuid"]]
    if len(exp) >= 3 and (not got or got[0]["games"] != len(exp)):
        problems.append("双排场次不一致：期望 %d，得到 %s" % (len(exp), got and got[0]["games"]))
    # 4. 前 10 分钟死亡（总数 / 被抓）
    tl_ids = [m for m, g in games.items() if g["has_timeline"] == "1"]
    exp_d = sum(len(T[m]["deaths_pre10"]) for m in tl_ids)
    exp_g = sum(1 for m in tl_ids for d in T[m]["deaths_pre10"] if d["enemies_involved"] >= 2)
    es = S["lane"]["early_summary"]
    if es["deaths"] != exp_d or es["ganked_deaths"] != exp_g:
        problems.append("前 10 分钟死亡不一致：%d/%d vs 真值 %d/%d" % (es["deaths"], es["ganked_deaths"], exp_d, exp_g))
    # 5. 首件成装时间（含撤销的那场）
    for mid in tl_ids:
        fl = T[mid].get("first_completed_legendary")
        got = games[mid]["first_item_time_s"]
        if fl and (not got or abs(float(got) - fl["ts_ms"] / 1000.0) > 1):
            problems.append("%s 首件成装时间：%s vs 真值 %.0f" % (mid, got, fl["ts_ms"] / 1000.0))
    undo = [u for u in truth.get("item_undo", []) if u.get("match_id") in tl_ids] if isinstance(truth.get("item_undo"), list) else []
    notes.append("撤销购买的对局：%d 场纳入对照" % len(undo))
    # 6. 开野方向（上半/下半/入侵）
    J = S.get("jungle") or {}
    agree = total = 0
    for o in J.get("openings", []):
        t = T[o["match_id"]]["opening"]
        want = "入侵" if t.get("invade") else ("下半" if t["start_half"] == "bottom" else "上半")
        total += 1
        if want in o["start_label"]:
            agree += 1
        else:
            notes.append("开野不一致 %s：真值 %s（%s），推测 %s；依据 %s" % (o["match_id"], t["pattern"], want, o["start_label"], o["basis"]))
    notes.append("开野方向一致 %d/%d" % (agree, total))
    if total and agree / total < 0.8:
        problems.append("开野方向一致率太低：%d/%d" % (agree, total))
    # 7. 抓到人的抓人（真值 success=True）能不能在同一路 ±60 秒找到
    hit = miss = 0
    for gk in J.get("ganks", []):
        eps = gk["episodes"]
        for t in T[gk["match_id"]]["ganks_pre10"]:
            if not t["success"]:
                continue
            lane = {"MIDDLE": "MID", "BOTTOM": "BOT"}.get(t["lane"], t["lane"])
            ok = any(e["lane"] == lane and e["result"].startswith("抓到") and abs(e["t_min"] - t["minute"]) <= 1.0 for e in eps)
            hit += ok
            miss += not ok
    notes.append("成功的抓人找回 %d/%d" % (hit, hit + miss))
    if hit + miss and hit / (hit + miss) < 0.8:
        problems.append("成功抓人找回率太低：%d/%d" % (hit, hit + miss))
    # 8. 死亡热点
    top = (S["deaths"].get("early_top_zone") or [None])[0]
    if top != "对方上半野区":
        problems.append("前 10 分钟死亡热点应为 对方上半野区，得到 %s" % top)
    for n in notes:
        print("  " + n)
    if problems:
        raise SystemExit("真值对照失败：\n" + "\n".join(problems))
    print("真值对照通过")


if __name__ == "__main__":
    main()
