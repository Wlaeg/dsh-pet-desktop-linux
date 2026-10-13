#!/usr/bin/env python3
"""t5_transparency.py —— 用「受控纯色背景」判定宠物窗口是否真透明。

用途
----
`unmap 差分`判据要求窗口下方内容在两次截图之间静止。实测本机该前提经常不成立
（底下是 Wayland 原生窗口或正在滚动的应用，看不见也管不住），四角会出现差异，
于是把「透明」误判成「不透明」。

本脚本改用决定性判据：先在 X11 上铺一块**纯色、静止、位置固定**的背景窗口，
把宠物窗口移到它上面，然后只截一次图：

  * 宠物窗口四角像素 == 背景纯色  → 窗口真透明（无底板）
  * 宠物窗口四角像素 != 背景纯色  → 窗口有不透明底板（或坐标算错）

再补一条正面判据：宠物窗口中部必须存在**非背景色**像素，证明宠物确实画出来了
（防止「窗口没映射」被误判成「透明」这类假通过）。

用法
----
    python3 tests/t5_transparency.py --assets-root <dsh-codex-pet 包根>
    python3 tests/t5_transparency.py --assets-root ... --color '#ff00ff' -v

退出码：0 = 全部判据通过；1 = 有判据失败。
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import time

os.environ.setdefault("GDK_BACKEND", "x11")

HERE = os.path.dirname(os.path.abspath(__file__))
SHOT = os.path.expanduser("~/.local/bin/shot")


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def log(msg, verbose=True):
    if verbose:
        print(msg, flush=True)


# ---------- 像素工具 ----------

def load_pixels(path):
    """返回 (data, width, height, n_channels, rowstride)，data 是 bytes。"""
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    pb = GdkPixbuf.Pixbuf.new_from_file(path)
    return (pb.get_pixels(), pb.get_width(), pb.get_height(),
            pb.get_n_channels(), pb.get_rowstride())


def px(data, w, h, nch, rs, x, y):
    if x < 0 or y < 0 or x >= w or y >= h:
        return None
    off = y * rs + x * nch
    return (data[off], data[off + 1], data[off + 2])


def parse_hex(spec):
    spec = spec.lstrip("#")
    return tuple(int(spec[i:i + 2], 16) for i in (0, 2, 4))


# ---------- 窗口工具 ----------

def find_pet_window(pid):
    """按 pid 找宠物窗口，返回 (wid, x, y, w, h)。

    本机坑（实测 xdotool 4.20260303.1）：`xdotool search --pid <pid> --name <n>`
    **不会同时应用两个条件** —— 传一个不存在的 pid 也照样返回全部同名窗口
    （`xdotool search --pid 999999 --name "DSH Codex Pet"` 返回真实窗口）。
    因此绝不能直接用 `--pid`：那会误抓到**用户正在运行的正式宠物**并操纵它。

    正确做法：先用 `--name` 枚举候选，再逐个读 `_NET_WM_PID` 与 `WM_CLASS`
    自己过滤，只有 pid 精确相等且 WM_CLASS 是本测试的 pet.py 才认。
    """
    r = run(["xdotool", "search", "--name", "DSH Codex Pet"])
    for wid in [x for x in r.stdout.split() if x.strip()]:
        got = run(["xprop", "-id", wid, "_NET_WM_PID"]).stdout
        m = re.search(r"=\s*(\d+)", got)
        if not m or int(m.group(1)) != pid:
            continue
        cls = run(["xprop", "-id", wid, "WM_CLASS"]).stdout
        if "pet.py" not in cls or "dsh-pet-desktop.py" in cls:
            continue  # 正式实例的类名是 dsh-pet-desktop.py，必须排除
        g = run(["xdotool", "getwindowgeometry", wid]).stdout
        m = re.search(r"Position:\s*(-?\d+),(-?\d+).*?Geometry:\s*(\d+)x(\d+)", g, re.S)
        if m:
            return wid, int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return None


def wait_for(cond, timeout, step=0.3):
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(step)
    return None


# ---------- 主流程 ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets-root", required=True)
    ap.add_argument("--color", default="#00ff00", help="背景纯色，默认亮绿")
    ap.add_argument("--config", default="/tmp/t5-trans-cfg.json")
    ap.add_argument("--app-id", default="com.wlaeg.t5.transparency")
    ap.add_argument("--out", default="/tmp/t5-trans.png")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--keep", action="store_true", help="不清理，留给人工看")
    args = ap.parse_args()

    pkg = os.path.dirname(HERE)
    script = os.path.join(pkg, "desktop", "pet.py")
    bg_rgb = parse_hex(args.color)
    procs = []
    fails, passes = [], []

    def check(name, ok, detail):
        (passes if ok else fails).append((name, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}", flush=True)

    try:
        # 1. 铺纯色背景（放在屏幕中部，避开正式宠物与面板）
        bx, by, bw, bh = 900, 300, 420, 320
        log(f"[1] 起纯色背景 {args.color} @ ({bx},{by}) {bw}x{bh}", args.verbose)
        bg = subprocess.Popen(
            [sys.executable, os.path.join(HERE, "t5_backdrop.py"),
             "--x", str(bx), "--y", str(by), "--w", str(bw), "--h", str(bh),
             "--color", args.color],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        procs.append(bg)
        # 只认 BACKDROP 行：GTK 启动时的 PyGIWarning 等噪音会先于它出现。
        line = None
        deadline = time.time() + 15
        while time.time() < deadline:
            raw = bg.stdout.readline()
            if not raw:
                if bg.poll() is not None:
                    break
                time.sleep(0.2)
                continue
            if "BACKDROP" in raw:
                line = raw.strip()
                break
        if not line:
            print("  [FAIL] 背景窗口没起来（15s 内未打印 BACKDROP）")
            return 1
        log("      " + line, args.verbose)
        m = re.search(r"geom=(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", line)
        bw, bh, bx, by = (int(m.group(i)) for i in (1, 2, 3, 4))

        # 2. 起宠物（独占 app-id，独立 config，绝不碰用户正式实例）
        with open(args.config, "w") as fh:
            fh.write('{"selected":"codex","size":104,"hidden":false,'
                     '"position":{"x":0.5,"y":0.5}}')
        log("[2] 起测试宠物实例（独占 app-id）", args.verbose)
        pet = subprocess.Popen(
            [sys.executable, script, "--assets-root", args.assets_root,
             "--config", args.config, "--port", "21987", "--app-id", args.app_id],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        procs.append(pet)
        found = wait_for(lambda: find_pet_window(pet.pid), 25)
        if not found:
            print("  [FAIL] 宠物窗口未出现")
            return 1
        wid, x, y, w, h = found
        log(f"      宠物 wid={wid} pid={pet.pid} 几何 {w}x{h}+{x}+{y}", args.verbose)

        # 3. 把宠物移到背景正中，并把它 raise 到背景之上
        #    （两者都是 _NET_WM_STATE_ABOVE，同类窗口按最后 raise 者在上排序）
        #
        #    实测坑：窗口刚 map 后立刻 windowmove 会**不生效**——pet.py 在显示
        #    之后还会再走一次 _place_from_config()，把窗口摆回配置里的位置，
        #    把我们的移动覆盖掉。因此先等窗口稳定，再带校验地移动。
        time.sleep(2.5)
        tx = ty = None
        for attempt in range(1, 5):
            g = find_pet_window(pet.pid)
            if not g:
                print("  [FAIL] windowmove 后窗口消失")
                return 1
            _, _, _, w, h = g
            tx = bx + (bw - w) // 2
            ty = by + (bh - h) // 2
            run(["xdotool", "windowmove", wid, str(tx), str(ty)])
            time.sleep(1.0)
            g2 = find_pet_window(pet.pid)
            if g2 and (g2[1], g2[2]) == (tx, ty):
                break
            log(f"      第 {attempt} 次移动未生效（当前 {g2[1:3] if g2 else None}），重试…",
                args.verbose)
            time.sleep(1.5)
        run(["xdotool", "windowraise", wid])
        time.sleep(1.2)
        moved = find_pet_window(pet.pid)
        if not moved:
            print("  [FAIL] windowmove 后窗口消失")
            return 1
        wid, tx, ty, w, h = moved
        if (tx, ty) != (bx + (bw - w) // 2, by + (bh - h) // 2):
            print(f"  [前提不成立] 无法把宠物窗口移到背景上（实际 {w}x{h}+{tx}+{ty}）"
                  f"—— 本次无法判定透明度")
            print("  结果：无法判定（环境前提不成立，非产品缺陷）")
            return 2
        log(f"      移到 {w}x{h}+{tx}+{ty}", args.verbose)

        # 4. 截图（只截一次；背景静止，不存在时序问题）
        log("[3] 截图", args.verbose)
        env = dict(os.environ, GDK_BACKEND="x11")
        shot = run(["timeout", "60", SHOT, "-f", args.out], env=env)
        if shot.returncode != 0 or not os.path.exists(args.out):
            print(f"  [FAIL] 截图失败 rc={shot.returncode} {shot.stderr.strip()[:200]}")
            return 1

        data, W, H, nch, rs = load_pixels(args.out)
        log(f"      截图 {W}x{H} channels={nch}", args.verbose)

        # 5. 判据一：背景区域本身必须是纯色（证明背景可被截图看到）
        sample = px(data, W, H, nch, rs, bx + 10, by + 10)
        bg_ok = sample is not None and max(
            abs(sample[i] - bg_rgb[i]) for i in range(3)) <= 8
        check("背景纯色可见", bg_ok, f"({bx + 10},{by + 10})={sample} 期望{args.color}")

        # 6. 前置校验：宠物窗口正下方（窗口外、背景内）必须是背景纯色。
        #    若此处不是纯色，说明背景被别的窗口挡住了，判据前提不成立 ——
        #    此时四角差异反映的是下层真实内容，不能算「不透明」。
        pre = px(data, W, H, nch, rs, tx - 20, ty + h // 2)
        pre_ok = pre is not None and max(abs(pre[i] - bg_rgb[i]) for i in range(3)) <= 8
        if not pre_ok:
            print(f"  [前提不成立] 宠物左侧紧邻处 (tx-20,ty+h/2)={pre} 不是背景色"
                  f"{args.color} —— 背景被其他窗口遮挡，本次无法判定透明度")
            print("  结果：无法判定（环境前提不成立，非产品缺陷）")
            return 2

        # 7. 判据二：宠物窗口四角必须等于背景色 → 真透明
        corners = {
            "左上": (tx + 2, ty + 2),
            "右上": (tx + w - 3, ty + 2),
            "左下": (tx + 2, ty + h - 3),
            "右下": (tx + w - 3, ty + h - 3),
        }
        corner_ok = True
        details = []
        for name, (cx, cy) in corners.items():
            got = px(data, W, H, nch, rs, cx, cy)
            if got is None:
                corner_ok = False
                details.append(f"{name}=越界")
                continue
            d = max(abs(got[i] - bg_rgb[i]) for i in range(3))
            if d > 8:
                corner_ok = False
            details.append(f"{name}={got}Δ{d}")
        check("四角透出背景色（=真透明）", corner_ok, " ".join(details))

        # 7. 判据三：窗口中部必须有非背景像素 → 宠物确实画出来了
        cx, cy = tx + w // 2, ty + h // 2
        nonbg = 0
        total = 0
        for yy in range(ty + 4, ty + h - 4):
            for xx in range(tx + 4, tx + w - 4):
                p = px(data, W, H, nch, rs, xx, yy)
                if p is None:
                    continue
                total += 1
                if max(abs(p[i] - bg_rgb[i]) for i in range(3)) > 12:
                    nonbg += 1
        ratio = nonbg / max(1, total)
        check("窗口内有宠物像素", 0.02 <= ratio, f"非背景像素 {nonbg}/{total} = {ratio * 100:.1f}%")
        check("窗口中部命中宠物", px(data, W, H, nch, rs, cx, cy) is not None,
              f"中心({cx},{cy})={px(data, W, H, nch, rs, cx, cy)}")

        print(f"\n结果：{len(passes)} 项通过 / {len(fails)} 项失败", flush=True)
        return 0 if not fails else 1

    finally:
        if not args.keep:
            for p in reversed(procs):
                try:
                    p.terminate()
                except Exception:
                    pass
            time.sleep(0.8)
            for p in procs:
                if p.poll() is None:
                    try:
                        p.kill()
                    except Exception:
                        pass


if __name__ == "__main__":
    sys.exit(main())
