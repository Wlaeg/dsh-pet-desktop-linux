#!/usr/bin/env python3
"""t5_backdrop.py —— 起一块**受控纯色背景**窗口，用于宠物透明性的决定性判据。

用途
----
unmap 差分判据有一个前提：窗口下方的内容在两次截图之间不能变。
实测本机这个前提经常不成立（底下是 Wayland 原生窗口或正在滚动的应用，
看不见也管不住），于是四角出现差异，会把「透明」误判成「不透明」。

本脚本提供正确做法：先铺一块**纯色、X11、位置固定**的背景窗口，再让宠物
窗口叠在它上面。此时判据变成决定性的：

  * 宠物窗口四角像素 == 背景纯色   → 窗口真透明（无底板）
  * 宠物窗口四角像素 != 背景纯色   → 窗口有不透明底板

纯色背景自己是静态的，不受任何滚动/动画影响。

用法
----
    # 终端 A：铺背景并保持运行（Ctrl-C 关闭）
    tests/t5_backdrop.py --x 1500 --y 200 --w 400 --h 300 --color "#00ff00"

    # 终端 B：把宠物窗口移到该区域后截图比较
    xdotool windowmove <pet-wid> 1600 260
"""
import argparse
import os
import sys

# 必须在 import gi 之前设置：Wayland 原生窗口 xdotool/xwininfo 看不见，
# 也无法作为「宠物底下那层」被稳定截图比对。
os.environ.setdefault("GDK_BACKEND", "x11")

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402


def parse_color(spec):
    """'#rrggbb' -> (r,g,b) 0-65535。"""
    spec = spec.lstrip("#")
    r, g, b = (int(spec[i:i + 2], 16) * 257 for i in (0, 2, 4))
    return r, g, b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--x", type=int, default=1500)
    ap.add_argument("--y", type=int, default=200)
    ap.add_argument("--w", type=int, default=400)
    ap.add_argument("--h", type=int, default=300)
    ap.add_argument("--color", default="#00ff00")
    args = ap.parse_args()

    r, g, b = parse_color(args.color)

    win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    win.set_decorated(False)
    win.set_resizable(False)
    win.set_default_size(args.w, args.h)
    win.move(args.x, args.y)
    # 必须置顶：否则会被 VS Code / 微信等普通窗口盖住，宠物四角采到的
    # 就不是背景纯色而是下层真实内容，判据会假失败（T5 实测踩过：背景只
    # 露出顶部 55px，四角采样全落在被遮挡区域，得到四个各不相同的灰度）。
    win.set_keep_above(True)
    # 纯色：用 CSS 画背景，避免依赖绘图回调
    css = Gtk.CssProvider()
    css.load_from_data(
        f"window {{ background-color: rgb({r // 257},{g // 257},{b // 257}); }}".encode())
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    win.connect("destroy", Gtk.main_quit)
    win.show_all()

    def report():
        gdk = win.get_window()
        if gdk is not None:
            x, y = win.get_position()
            print(f"BACKDROP xid={gdk.get_xid()} geom={args.w}x{args.h}+{x}+{y} "
                  f"color={args.color}", flush=True)
        return False

    # 注意：**不要**定期 present()。背景与宠物都是 _NET_WM_STATE_ABOVE，
    # 同类窗口之间按「最后 raise 的在上」排序；周期性 raise 会把背景顶到
    # 宠物之上，反而挡住宠物。正确层次由调用方保证：背景先 keep_above，
    # 宠物再 windowraise（见 t5_transparency.py）。
    GLib.timeout_add(300, report)
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
