#!/usr/bin/env python3
"""t5_desktop_props.py —— 桌面端（pet.py 独立进程）窗口属性与透明性验收。

用途
----
不经过 DSH，直接以独占 --app-id 起一份 pet.py，用 X11 工具核对窗口应满足的
EWMH 契约，并用 **unmap 差分** 这个不依赖人眼的判据验证真透明：

  1. 启动独立实例（独占 app-id，绝不碰用户正式宠物）
  2. xprop/xwininfo 核对：Depth=32、_NET_WM_STATE 含 ABOVE+STICKY、
     _NET_WM_DESKTOP=4294967295、Map State=IsViewable、Border width=0
  3. 透明性：shot(with) → windowunmap → shot(without) → windowmap，
     逐像素比较。判据：整窗差异 < 90%（否则整窗都被不透明底色覆盖）
     且四角 12x12 完全一致（非宠物区域 = 背景）。
  4. hidden 切换、--help、坏 --assets-root 的错误与退出码

为什么用 unmap 差分而不是看截图：GTK 窗口的真透明在 PNG 里是 alpha=0，
但截图产物的 alpha 会被合成，肉眼与像素值都不直观。unmap 后那块区域露出
的是真实桌面背景，两者在**非宠物区域**必须逐像素相同 —— 这才等价于
「窗口没有不透明底板」。实测若 GTK4 顶层无法透明，整窗差异会接近 100%。

用法
----
    tests/t5_desktop_props.py --assets-root <上游包根>
退出码 0 = 全部通过。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PET = os.path.join(os.path.dirname(HERE), "desktop", "pet.py")
SHOT = os.path.expanduser("~/.local/bin/shot")

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


def run(*a, **kw):
    return subprocess.run(a, capture_output=True, text=True, **kw)


def xprop(wid, name):
    return run("xprop", "-id", str(wid), name).stdout.strip()


def xwininfo(wid):
    out = run("xwininfo", "-id", str(wid)).stdout
    g = {}
    for k, pat in (("x", r"Absolute upper-left X:\s+(-?\d+)"),
                   ("y", r"Absolute upper-left Y:\s+(-?\d+)"),
                   ("w", r"Width:\s+(\d+)"), ("h", r"Height:\s+(\d+)"),
                   ("depth", r"Depth:\s+(\d+)"), ("map", r"Map State:\s+(\S+)"),
                   ("border", r"Border width:\s+(\d+)")):
        m = re.search(pat, out)
        g[k] = m.group(1) if m else None
    return g


def find_win(pid):
    for w in run("xdotool", "search", "--name", "DSH Codex Pet").stdout.split():
        if xprop(w, "_NET_WM_PID").split()[-1:] == [str(pid)]:
            return w
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets-root", required=True)
    ap.add_argument("--app-id", default="com.wlaeg.t5.props")
    ap.add_argument("--port", type=int, default=21999)
    ap.add_argument("--config", default="/tmp/t5-props-cfg.json")
    args = ap.parse_args()

    print("=== t5_desktop_props.py ===")
    cfg = {"selected": "codex", "size": 104, "hidden": False,
           "position": {"x": 0.5, "y": 0.5}, "autostart": False}
    with open(args.config, "w") as f:
        json.dump(cfg, f)

    # pet.py 的日志写到文件而不是 PIPE：PIPE 不排空会填满缓冲区把子进程卡住。
    logpath = "/tmp/t5-desktop-props.log"
    logfh = open(logpath, "w")
    proc = subprocess.Popen(
        ["python3", PET, "--assets-root", args.assets_root, "--config", args.config,
         "--port", str(args.port), "--app-id", args.app_id],
        stdout=logfh, stderr=subprocess.STDOUT, start_new_session=True)

    wid = None
    for _ in range(20):
        time.sleep(0.5)
        if proc.poll() is not None:
            logfh.close()
            bad(f"pet.py 提前退出：{open(logpath).read()[:400]}")
            return 1
        wid = find_win(proc.pid)
        if wid:
            break
    if not wid:
        bad("15 秒内没找到窗口")
        proc.kill()
        logfh.close()
        return 1
    ok(f"独立实例已起：pid={proc.pid} wid={wid}（日志 {logpath}）")

    try:
        # ── 2 窗口属性 ──────────────────────────────────────────────────────
        # pet.py 在 map 之后按 300/1000/2500ms 重试写 EWMH 状态；过早读取会看到
        # 缺 STICKY / _NET_WM_DESKTOP=0。等 4 秒再断言（实测 4s 已稳定）。
        time.sleep(4)
        g = xwininfo(wid)
        if g["depth"] == "32":
            ok(f"Depth: 32（含 alpha 通道）")
        else:
            bad(f"Depth={g['depth']}，应为 32")
        state = xprop(wid, "_NET_WM_STATE")
        missing = [s for s in ("ABOVE", "STICKY") if s not in state]
        if not missing:
            ok(f"_NET_WM_STATE 含 ABOVE+STICKY：{state.split('=', 1)[1].strip()}")
        else:
            bad(f"_NET_WM_STATE 缺 {missing}：{state}")
        desk = xprop(wid, "_NET_WM_DESKTOP")
        if desk.endswith("4294967295"):
            ok("_NET_WM_DESKTOP = 4294967295（全桌面粘滞）")
        else:
            bad(f"_NET_WM_DESKTOP 异常：{desk}")
        if g["map"] == "IsViewable":
            ok("Map State: IsViewable")
        else:
            bad(f"Map State={g['map']}")
        if g["border"] == "0":
            ok("Border width: 0")
        else:
            bad(f"Border width={g['border']}")
        print(f"        几何 {g['w']}x{g['h']} @ ({g['x']},{g['y']})")

        # ── 3 透明性 unmap 差分 ─────────────────────────────────────────────
        if not os.path.exists(SHOT):
            bad(f"找不到截图工具 {SHOT}")
        else:
            def snap(path):
                r = run("timeout", "60", SHOT, "-f", path)
                if not os.path.exists(path):
                    bad(f"shot 未产出 {path}：rc={r.returncode} "
                        f"out={r.stdout.strip()[:100]!r} err={r.stderr.strip()[:150]!r}")
                    return False
                return True

            if snap("/tmp/t5p-warm.png"):
                ok("截图预热完成（portal 已热）")
            have = snap("/tmp/t5p-with.png")
            run("xdotool", "windowunmap", wid)
            time.sleep(1.5)
            have = snap("/tmp/t5p-without.png") and have
            run("xdotool", "windowmap", wid)
            time.sleep(1.0)
            g2 = xwininfo(wid)
            if (g2["x"], g2["y"], g2["w"], g2["h"]) != (g["x"], g["y"], g["w"], g["h"]):
                bad(f"unmap/map 后几何漂移：{g2}")
            else:
                ok("unmap/map 后几何未变（差分前提成立）")
            if not have:
                bad("截图缺失，透明性无法判定")
            else:
                r = run("python3", os.path.join(HERE, "pixel_diff.py"),
                        "/tmp/t5p-with.png", "/tmp/t5p-without.png",
                        g["x"], g["y"], g["w"], g["h"])
                print("\n".join("        " + l for l in r.stdout.strip().splitlines()))
                if "PASS(透明)" in r.stdout:
                    ok("透明性判据通过（非宠物区域与背景逐像素一致）")
                else:
                    bad("透明性判据未通过（见上方差异率）")

        # ── 4 hidden 真实生效 ──────────────────────────────────────────────
        cfg["hidden"] = True
        with open(args.config, "w") as f:
            json.dump(cfg, f)
        hid = None
        for _ in range(10):
            time.sleep(1)
            if xwininfo(wid)["map"] == "IsUnMapped":
                hid = True
                break
        if hid:
            ok("配置 hidden=true -> IsUnMapped（热重载生效）")
        else:
            bad(f"hidden=true 未生效：{xwininfo(wid)['map']}")

        # ── 5 --help / 坏 assets-root ──────────────────────────────────────
        h = run("python3", PET, "--help")
        flags = [f for f in ("--assets-root", "--config", "--port", "--wait",
                             "--app-id", "--backend") if f in h.stdout]
        if h.returncode == 0 and len(flags) == 6:
            ok(f"--help 退出码 0，6 个参数齐全")
        else:
            bad(f"--help rc={h.returncode} 参数={flags}")
        e = run("python3", PET, "--assets-root", "/nonexistent-t5",
                "--config", args.config, "--port", str(args.port),
                "--app-id", "com.wlaeg.t5.err")
        if e.returncode == 3 and "找不到精灵图目录" in e.stdout + e.stderr:
            first = (e.stdout + e.stderr).strip().splitlines()[0]
            ok(f"坏 --assets-root -> 退出码 3，中文错误：{first}")
        else:
            bad(f"坏 --assets-root rc={e.returncode} 输出={(e.stdout+e.stderr)[:200]}")
    finally:
        proc.kill()
        proc.wait()
        time.sleep(0.5)
        left = [l.split()[0] for l in run("ps", "-eo", "pid,cmd").stdout.splitlines()
                if args.app_id in l and "pet.py" in l]
        for p in left:
            run("kill", "-9", p)
        print(f"        清理：残留={left or '无'}")

    print()
    print(f"结果：PASS={pass_n}  FAIL={fail_n}")
    return 1 if fail_n else 0


if __name__ == "__main__":
    sys.exit(main())
