#!/usr/bin/env python3
"""t5_host_api.py —— 宿主端（lib/index.js）API 全套验收，跑在**真实 DSH 实例**上。

用途
----
对 `/dsh-pet-desktop` 路由做端到端验收，不用任何 mock：
真实启动一个隔离 DSH，用 urllib 打真 HTTP 请求，用 xdotool/xwininfo
核对进程与窗口状态。覆盖：

  1. GET /            路由清单
  2. GET /api/state   字段结构（running/pid/config/pets/dependencies/log…）
  3. GET /api/deps    五项依赖探测
  4. 安全            无 trust 头 / 错误 content-type / cross-site 一律 403
  5. POST /api/start  拉起宠物，且窗口 pid 属于该子进程
  6. POST /api/config 改 size+selected 真的生效（窗口几何变化）
  7. POST /api/config 改 hidden 真的 map/unmap
  8. POST /api/stop   子进程退出，lastExit 记录 signal
  9. 孤儿检查          杀掉 DSH 后宠物不应残留

隔离
----
DSH_HOME 用临时目录，profile 用调用方指定的（默认 newtest，已装本插件），
端口默认 22993，绝不碰用户 ~/.dsh 与正式 DSH(19387)/宠物(1149)。

用法
----
    tests/t5_host_api.py --dsh-home /tmp/t5-iso-home --profile newtest
    tests/t5_host_api.py --port 22993
退出码 0 = 全部通过。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

DSH_DIR = "/home/wlaeg/.npm/_npx/1e7f6d9597241db0"
BIN = os.path.join(DSH_DIR, "node_modules", "@deepseek-ai", "dsh", "lib", "bin.js")
BASE = "/dsh-pet-desktop"

pass_n = 0
fail_n = 0


def ok(m):
    global pass_n
    pass_n += 1
    print(f"  PASS  {m}")


def bad(m):
    global fail_n
    fail_n += 1
    print(f"  FAIL  {m}")


def req(port, path, method="GET", body=None, headers=None, timeout=8):
    """返回 (status, parsed_json_or_text, raw_headers)。"""
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        status = e.code
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw


def trusted(port, path, body, method="POST"):
    return req(port, path, method, body,
               {"x-dsh-pet-desktop": "1", "content-type": "application/json"})


def xdo(*args):
    return subprocess.run(["xdotool", *args], capture_output=True, text=True).stdout.split()


def win_of_pid(pid):
    """在 'DSH Codex Pet' 标题的窗口里找属于 pid 的那个。"""
    for w in xdo("search", "--name", "DSH Codex Pet"):
        out = subprocess.run(["xprop", "-id", w, "_NET_WM_PID"],
                             capture_output=True, text=True).stdout
        if out.strip().split()[-1] == str(pid):
            return w
    return None


def win_geo(wid):
    out = subprocess.run(["xwininfo", "-id", str(wid)], capture_output=True, text=True).stdout
    g = {}
    for k, pat in (("x", r"Absolute upper-left X:\s+(-?\d+)"),
                   ("y", r"Absolute upper-left Y:\s+(-?\d+)"),
                   ("w", r"Width:\s+(\d+)"), ("h", r"Height:\s+(\d+)"),
                   ("map", r"Map State:\s+(\S+)")):
        m = re.search(pat, out)
        g[k] = m.group(1) if m else None
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsh-home", default="/tmp/t5-iso-home")
    ap.add_argument("--profile", default="newtest")
    ap.add_argument("--port", type=int, default=22993)
    ap.add_argument("--pet-pattern", default="t5-iso-home/profiles/newtest")
    args = ap.parse_args()
    port = args.port

    print("=== t5_host_api.py ===")
    print(f"DSH_HOME={args.dsh_home}  profile={args.profile}  port={port}")

    def pets():
        return [l.split()[0] for l in subprocess.run(["ps", "-eo", "pid,cmd"],
                                                     capture_output=True, text=True).stdout.splitlines()
                if args.pet_pattern in l and "pet.py" in l and "awk" not in l]

    for p in pets():
        subprocess.run(["kill", p], check=False)
    time.sleep(1.5)

    log = "/tmp/t5-hostapi.log"
    if os.path.exists(log):
        os.unlink(log)
    env = dict(os.environ, DSH_HOME=args.dsh_home)
    with open(log, "w") as lf:
        dsh = subprocess.Popen(["node", BIN, "--profile", args.profile,
                                "--no-open", "--port", str(port)],
                               cwd=DSH_DIR, env=env, stdout=lf, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, start_new_session=True)
    ready = None
    t0 = time.time()
    for _ in range(40):
        time.sleep(1)
        if os.path.exists(log) and "token=" in open(log, errors="ignore").read():
            ready = time.time() - t0
            break
    if ready is None:
        bad("DSH 未在 40s 内就绪")
        dsh.kill()
        return 1
    ok(f"隔离 DSH 就绪，耗时 {ready:.1f}s")

    try:
        # ── 1 路由清单 ──────────────────────────────────────────────────────
        st, body = req(port, f"{BASE}/")
        if st == 200 and isinstance(body, dict) and "routes" in body:
            ok(f"GET {BASE}/ -> {st} {json.dumps(body, ensure_ascii=False)}")
        else:
            bad(f"路由清单异常：{st} {body}")

        # ── 2 state 结构 ────────────────────────────────────────────────────
        st, s = req(port, f"{BASE}/api/state")
        need = {"running", "pid", "config", "dependencies", "log", "pets"}
        missing = need - set(s) if isinstance(s, dict) else need
        if st == 200 and not missing:
            ok(f"state 含全部必需字段（另有 {sorted(set(s) - need)}）")
        else:
            bad(f"state 缺字段 {missing}（status={st}）")
        print(f"        running={s.get('running')} pid={s.get('pid')} pets={len(s.get('pets', []))}"
              f" config={json.dumps(s.get('config'), ensure_ascii=False)}")
        # autostart 有 1.5s 延迟 + 进程拉起时间，给足 12 秒再判定（实测约 4s 起来）
        auto = None
        for _ in range(12):
            time.sleep(1)
            _, sc = req(port, f"{BASE}/api/state")
            if sc.get("running") and sc.get("pid"):
                auto = sc
                break
        if auto:
            ok(f"autostart 已把宠物拉起：pid={auto['pid']}（未手动点任何按钮）")
            s = auto
        else:
            bad("autostart 12 秒内未自动拉起宠物")

        # ── 3 deps ──────────────────────────────────────────────────────────
        st, d = req(port, f"{BASE}/api/deps")
        dep = d.get("dependencies", {}) if isinstance(d, dict) else {}
        ids = {i["id"]: i for i in dep.get("items", [])}
        want = ["python3", "pygobject", "gtk3", "gdkpixbuf", "xdotool"]
        if all(i in ids for i in want):
            ok("deps 含五项：" + " / ".join(f"{i}={ids[i]['detail']}" for i in want))
        else:
            bad(f"deps 项不全：{sorted(ids)}")

        # ── 4 安全校验（负例必须 403） ───────────────────────────────────────
        neg = [
            ("无 trust 头", {"content-type": "application/json"}),
            ("content-type=text/plain", {"x-dsh-pet-desktop": "1", "content-type": "text/plain"}),
            ("trust 头值=0", {"x-dsh-pet-desktop": "0", "content-type": "application/json"}),
            ("cross-site", {"x-dsh-pet-desktop": "1", "content-type": "application/json",
                            "sec-fetch-site": "cross-site"}),
        ]
        for name, h in neg:
            st, b = req(port, f"{BASE}/api/config", "POST", {"size": 100}, h)
            if st == 403:
                ok(f"负例 {name} -> 403 {b.get('error') if isinstance(b, dict) else b}")
            else:
                bad(f"负例 {name} 未拦：{st} {b}")
        for path in ("start", "stop"):
            st, _ = req(port, f"{BASE}/api/{path}", "POST", {})
            if st == 403:
                ok(f"POST /api/{path} 无头 -> 403")
            else:
                bad(f"POST /api/{path} 无头 -> {st}（应 403）")

        # ── 5 start + 窗口 pid 核对 ──────────────────────────────────────────
        st, r = trusted(port, f"{BASE}/api/start", {})
        pid = r.get("pid") if isinstance(r, dict) else None
        if st == 200 and pid:
            ok(f"POST /api/start -> {st} pid={pid}（若已在跑则返回现进程）")
        else:
            bad(f"start 返回异常：{st} {r}")
        time.sleep(1.5)
        wid = win_of_pid(pid) if pid else None
        if wid:
            g = win_geo(wid)
            ok(f"窗口归属核对：wid={wid} 的 _NET_WM_PID=={pid}，"
               f"几何 {g['w']}x{g['h']} @ ({g['x']},{g['y']}) {g['map']}")
        else:
            bad(f"找不到 pid={pid} 的窗口")

        # ── 6 config: size + selected 真的生效 ──────────────────────────────
        # 必须先取当前 size，写一个**不同的**值，否则「没变化」无法区分是 bug 还是同值。
        _, s_cur = req(port, f"{BASE}/api/state")
        cur_size = s_cur.get("config", {}).get("size", 160)
        new_size = 96 if cur_size != 96 else 176
        cur_sel = s_cur.get("config", {}).get("selected", "fireball")
        new_sel = "codex" if cur_sel != "codex" else "rocky"
        before = win_geo(wid) if wid else None
        st, cfg = trusted(port, f"{BASE}/api/config", {"size": new_size, "selected": new_sel})
        if st == 200 and cfg.get("config", {}).get("size") == new_size:
            ok(f"POST /api/config size={cur_size}->{new_size} selected={cur_sel}->{new_sel}"
               f" -> 回读 {cfg['config']['size']}/{cfg['config']['selected']}")
        else:
            bad(f"config 写入异常：{st} {cfg}")
        if before is None:
            bad("拿不到初始窗口几何，尺寸变化无法判定")
        else:
            changed = False
            for _ in range(6):
                time.sleep(1)
                g2 = win_geo(wid)
                if g2["w"] and g2["w"] != before["w"]:
                    changed = True
                    ok(f"窗口几何随配置变化：{before['w']}x{before['h']} -> {g2['w']}x{g2['h']}"
                       f"（{g2['w']}/{before['w']} 缩放，符合 size 比例）")
                    break
            if not changed:
                bad(f"4 秒内窗口尺寸没变（仍 {before['w']}x{before['h']}）")

        # ── 7 hidden 真实 map/unmap ─────────────────────────────────────────
        if wid is None:
            bad("没有窗口 id，hidden 项无法验证")
        else:
            trusted(port, f"{BASE}/api/config", {"hidden": True})
            mapped = None
            for _ in range(8):
                time.sleep(1)
                if win_geo(wid)["map"] == "IsUnMapped":
                    mapped = "unmap"
                    break
            if mapped:
                ok("hidden=true -> Map State: IsUnMapped")
            else:
                bad(f"hidden=true 后未 unmap：{win_geo(wid)['map']}")
            trusted(port, f"{BASE}/api/config", {"hidden": False})
            back = None
            for _ in range(8):
                time.sleep(1)
                if win_geo(wid)["map"] == "IsViewable":
                    back = True
                    break
            if back:
                ok("hidden=false -> Map State: IsViewable（可恢复）")
            else:
                bad(f"hidden=false 后未恢复：{win_geo(wid)['map']}")

        # ── 8 stop -> lastExit.signal ───────────────────────────────────────
        st, r = trusted(port, f"{BASE}/api/stop", {})
        if st == 200 and r.get("stopped"):
            ok(f"POST /api/stop -> {st} stopped={r['stopped']} killed={r.get('killed')}")
        else:
            bad(f"stop 异常：{st} {r}")
        gone = False
        for _ in range(10):
            time.sleep(0.5)
            if not pets():
                gone = True
                break
        if gone:
            ok("子进程确实退出，无残留")
        else:
            bad(f"stop 后仍有残留：{pets()}")
        _, s2 = req(port, f"{BASE}/api/state")
        le = s2.get("lastExit") or {}
        if le.get("signal") or le.get("code") is not None:
            ok(f"lastExit 记录了退出原因：{json.dumps(le, ensure_ascii=False)}")
        else:
            bad(f"lastExit 未记录：{le!r}")

        # ── 9 孤儿检查：杀掉 DSH，宠物不应残留 ──────────────────────────────
        trusted(port, f"{BASE}/api/start", {})
        time.sleep(2)
        alive = pets()
        if alive:
            ok(f"重新拉起宠物 pid={alive}")
        else:
            bad("重新 start 失败，孤儿检查无法进行")
        subprocess.run(["kill", str(dsh.pid)], check=False)
        time.sleep(3)
        left = pets()
        if not left:
            ok("DSH 被 SIGTERM 后宠物子进程无孤儿残留")
        else:
            bad(f"DSH 退出后仍有宠物孤儿：{left}")
            for p in left:
                subprocess.run(["kill", "-9", p], check=False)
        if dsh.poll() is None:
            dsh.kill()
    finally:
        if dsh.poll() is None:
            dsh.kill()
        time.sleep(1)
        for p in pets():
            subprocess.run(["kill", "-9", p], check=False)

    print()
    print(f"结果：PASS={pass_n}  FAIL={fail_n}")
    return 1 if fail_n else 0


if __name__ == "__main__":
    sys.exit(main())
