#!/usr/bin/env python3
"""win_fingerprint.py —— 窗口内容指纹 / 区域比较（不依赖 Pillow）。

用途
----
配合 `shot` 截图，给「宠物是否真的变了」「窗口尺寸是否真的变了」提供
可复核的数值判据，而不是只靠人眼。

子命令
------
  geo <wid>                     # 打印 xwininfo 的几何（x y w h depth map state）
  fp  <png> <x> <y> <w> <h>     # 打印该区域的内容指纹（像素和 + 非透明像素数）
  cmp <a.png> <b.png> <x> <y> <w> <h>
                                # 比较两图同区域，打印差异像素数与差异率
  find <png> <x0> <y0> <w0> <h0> <ref.png> <rx> <ry> <rw> <rh>
                                # 在搜索区域内滑动参考块找最佳匹配（用于确认
                                # 宠物真的换了 —— 换了以后旧块就找不到了）

只依赖 PyGObject / GdkPixbuf。
"""

import subprocess
import sys

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402


def load(path):
    return GdkPixbuf.Pixbuf.new_from_file(path)


def region_stats(pb, x, y, w, h):
    ca, rs, nc = pb.get_pixels(), pb.get_rowstride(), pb.get_n_channels()
    total = 0
    nonzero = 0
    checksum = 0
    for py in range(y, y + h):
        base = py * rs
        for px in range(x, x + w):
            o = base + px * nc
            r, g, b = ca[o], ca[o + 1], ca[o + 2]
            a = ca[o + 3] if nc > 3 else 255
            total += 1
            if r or g or b:
                nonzero += 1
            checksum = (checksum * 31 + r * 7 + g * 13 + b * 17 + a * 19) & 0xFFFFFFFF
    return {"total": total, "nonzero": nonzero, "sum": checksum}


def region_diff(a, b, x, y, w, h):
    ca, cb = a.get_pixels(), b.get_pixels()
    rs, nc = a.get_rowstride(), a.get_n_channels()
    diff = total = 0
    for py in range(y, y + h):
        base = py * rs
        for px in range(x, x + w):
            o = base + px * nc
            total += 1
            if (ca[o], ca[o + 1], ca[o + 2]) != (cb[o], cb[o + 1], cb[o + 2]):
                diff += 1
    return diff, total


def cmd_geo(wid):
    out = subprocess.run(["xwininfo", "-id", str(wid)], capture_output=True, text=True).stdout
    fields = {}
    for line in out.splitlines():
        line = line.strip()
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        if key in ("Absolute upper-left X", "Absolute upper-left Y", "Width", "Height", "Depth", "Map State"):
            fields[key] = val.strip()
    print(
        f"wid={wid} x={fields.get('Absolute upper-left X')} y={fields.get('Absolute upper-left Y')} "
        f"w={fields.get('Width')} h={fields.get('Height')} depth={fields.get('Depth')} "
        f"map={fields.get('Map State')}"
    )
    return 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    kind = sys.argv[1]

    if kind == "geo":
        return cmd_geo(sys.argv[2])

    if kind == "fp":
        pb = load(sys.argv[2])
        x, y, w, h = (int(v) for v in sys.argv[3:7])
        st = region_stats(pb, x, y, w, h)
        print(f"区域({x},{y}) {w}x{h} 非背景像素={st['nonzero']}/{st['total']} 指纹={st['sum']:08x}")
        return 0

    if kind == "cmp":
        a, b = load(sys.argv[2]), load(sys.argv[3])
        x, y, w, h = (int(v) for v in sys.argv[4:8])
        d, t = region_diff(a, b, x, y, w, h)
        print(f"区域({x},{y}) {w}x{h} 差异={d}/{t} = {100.0*d/t:.1f}%" if t else "空区域")
        return 0

    if kind == "find":
        src = load(sys.argv[2])
        x0, y0, w0, h0 = (int(v) for v in sys.argv[3:7])
        ref = load(sys.argv[7])
        rx, ry, rw, rh = (int(v) for v in sys.argv[8:12])
        ref_block = ref.new_subpixbuf(rx, ry, rw, rh)
        best = None
        for oy in range(y0, y0 + h0 - rh + 1, 2):
            for ox in range(x0, x0 + w0 - rw + 1, 2):
                blk = src.new_subpixbuf(ox, oy, rw, rh)
                ca, cb = blk.get_pixels(), ref_block.get_pixels()
                rs, nc = blk.get_rowstride(), blk.get_n_channels()
                d = 0
                for py in range(rh):
                    base = py * rs
                    for px in range(rw):
                        o = base + px * nc
                        if (ca[o], ca[o + 1], ca[o + 2]) != (cb[o], cb[o + 1], cb[o + 2]):
                            d += 1
                if best is None or d < best[0]:
                    best = (d, ox, oy)
        print(f"最佳匹配：差异={best[0]} 位置=({best[1]},{best[2]}) 参考块 {rw}x{rh}")
        return 0

    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
