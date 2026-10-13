#!/usr/bin/env python3
"""pixel_diff.py —— 透明性 unmap 差分判据的逐像素比较工具。

用途
----
判定 GTK 窗口是否**真透明**：截两张图，一张窗口在（with），一张窗口被
`xdotool windowunmap` 摘掉（without），然后比较窗口矩形内的像素。

判据（本项目最可靠的一条，见 DESIGN.md 第 3 节第 3 条）：
  - 真透明：窗口矩形内**非宠物像素**（四角最典型）`with == without`；
    只有宠物精灵图实际覆盖的像素不同 → 差异率远低于 100%。
  - 不透明（如 GTK4 顶层窗口恒有主题底色 rgb(59,59,59)）：
    整窗 100% 与 without 不同 → 差异率接近 100%。

用法
----
  python3 pixel_diff.py <with.png> <without.png> <x> <y> <w> <h>
  python3 pixel_diff.py --corner with.png without.png x y w h   # 额外单独报告四角

只依赖 PyGObject / GdkPixbuf（本机已有），不依赖 Pillow。
"""

import sys

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

CORNER = 12  # 四角取多大的方形区域做「非宠物区域」采样


def load(path):
    return GdkPixbuf.Pixbuf.new_from_file(path)


def region_diff(a, b, x, y, w, h, step=1):
    """返回 (diff_pixels, total_pixels, max_channel_delta)。"""
    ca, cb = a.get_pixels(), b.get_pixels()
    rs, nc = a.get_rowstride(), a.get_n_channels()
    diff = 0
    total = 0
    max_delta = 0
    for py in range(y, y + h, step):
        base = py * rs
        for px in range(x, x + w, step):
            o = base + px * nc
            total += 1
            d = max(
                abs(ca[o] - cb[o]),
                abs(ca[o + 1] - cb[o + 1]),
                abs(ca[o + 2] - cb[o + 2]),
            )
            # alpha 也要比：真透明的窗口在 alpha 上同样应当一致
            if nc > 3:
                d = max(d, abs(ca[o + 3] - cb[o + 3]))
            if d > 0:
                diff += 1
                if d > max_delta:
                    max_delta = d
                if max_delta >= 255:
                    pass
    return diff, total, max_delta


def main():
    argv = sys.argv[1:]
    show_corners = False
    if argv and argv[0] == "--corner":
        show_corners = True
        argv = argv[1:]
    if len(argv) != 6:
        print(__doc__)
        return 2

    with_png, without_png = argv[0], argv[1]
    x, y, w, h = (int(v) for v in argv[2:6])

    a, b = load(with_png), load(without_png)
    if (a.get_width(), a.get_height()) != (b.get_width(), b.get_height()):
        print(f"FAIL 两张截图尺寸不同：{a.get_width()}x{a.get_height()} vs {b.get_width()}x{b.get_height()}")
        return 1

    print(f"# with   = {with_png} ({a.get_width()}x{a.get_height()})")
    print(f"# without= {without_png} ({b.get_width()}x{b.get_height()})")
    print(f"# 窗口矩形 = ({x},{y}) {w}x{h}")

    diff, total, max_delta = region_diff(a, b, x, y, w, h)
    pct = 100.0 * diff / total if total else 0.0
    print(f"整窗差异: {diff}/{total} = {pct:.2f}%  最大通道差={max_delta}")

    corners_ok = True
    if show_corners or True:
        c = CORNER
        spots = {
            "左上": (x, y),
            "右上": (x + w - c, y),
            "左下": (x, y + h - c),
            "右下": (x + w - c, y + h - c),
        }
        for label, (cx, cy) in spots.items():
            d, t, md = region_diff(a, b, cx, cy, c, c)
            ok = d == 0
            corners_ok = corners_ok and ok
            print(f"  角落[{label}] ({cx},{cy}) {c}x{c}: 差异={d}/{t} 最大通道差={md} {'OK(透明)' if ok else 'DIFF(可疑)'}")

    verdict = "PASS(透明)" if (pct < 90.0 and corners_ok) else "FAIL(疑似不透明/非宠物区域有差异)"
    print(f"判据: 整窗差异<90% 且 四角完全一致 → {verdict}")
    return 0 if verdict.startswith("PASS") else 1


if __name__ == "__main__":
    sys.exit(main())
