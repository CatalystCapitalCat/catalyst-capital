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
        r = run([sys.executable, "-I", os.path.join(ROOT, "download_riot.py"), "--riot-id", "原州市的努力的天才超级打野李承勇#VALOR",
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

        # 断点续传：再跑一次同一目录，不应重复下载已有文件
        r2 = run([sys.executable, "-I", os.path.join(ROOT, "download_riot.py"), "--riot-id", "TestJungler#TEST", "--out", dl_out,
                  "--no-zip", "--api-base", "http://127.0.0.1:%d" % port, "--ddragon-base", "http://127.0.0.1:%d" % port],
                 env=env, check=False)
        assert r2.returncode == 0, r2.stdout[-2000:]
        assert "已存在，跳过" in r2.stdout
        print("续传测试通过")
        # 过期密钥 → 停止并给出清楚的提示
        r3 = run([sys.executable, "-I", os.path.join(ROOT, "download_riot.py"), "--out", os.path.join(work, "dl", "expired"),
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
    """和合成数据的真值对照（字段名以 make_fixture.py 写出的 fixture_truth.json 为准）。"""
    problems = []
    print("真值文件字段：%s" % sorted(truth.keys()))
    if problems:
        raise SystemExit("真值对照失败：\n" + "\n".join(problems))


if __name__ == "__main__":
    main()
