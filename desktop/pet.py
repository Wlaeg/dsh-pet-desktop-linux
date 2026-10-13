#!/usr/bin/env python3
"""DSH Codex Pet 桌面版 —— 让宠物脱离 DSH 窗口，悬浮在 GNOME 桌面上。

这是随插件 dsh-pet-desktop-linux 分发的可移植版本。与 ~/.local/bin 下那份
「个人用」程序的三点关键差异：

1. **不依赖 Pillow / pycairo**。原程序用 Pillow 解 webp、用 cairo 画像素；
   本版本全部改走 GdkPixbuf + Gtk.Image：
       - 解码 webp：GdkPixbuf.PixbufLoader（loader.write(raw); loader.close()）
       - 裁帧：pixbuf.new_subpixbuf(col*192, row*208, 192, 208)
       - 缩放：pixbuf.scale_simple(w, h, GdkPixbuf.InterpType.NEAREST)
       - 显示：Gtk.Image.set_from_pixbuf（Gtk.Image 自身按 pixbuf 的 alpha 绘制）
   ⚠️ 为什么不用 Gtk.DrawingArea + cr：GTK 的 draw 回调要拿到非 None 的 cr，
   需要 Python 侧能加载 pycairo（gi 的 cairo 外部结构转换器 _gi_cairo.so
   内部就会去导入 cairo 模块）。缺 pycairo 时 gi.repository.Gtk 仍能导入，
   但 draw 回调里 cr 是 None，报
   "TypeError: Couldn't find foreign struct converter for 'cairo.Context'"。
   即 DrawingArea 路线隐含依赖 pycairo；Gtk.Image 路线才是真正的零 pycairo。

2. **图集从本地读，不走 HTTP**。由调用方（插件的 Node 半身）用
   require.resolve 定位上游宠物包，把包根经 --assets-root 传进来；精灵图在
   <assets-root>/assets/codex/<pet_id>/spritesheet.webp。更快、离线可用、
   不依赖 DSH web 是否在跑。

3. **参数化 + 配置自管**。没有硬编码的本机路径，也不再依赖
   ~/.local/bin/dsh-web-launch.sh（别人机器上不存在）。配置写在 --config
   指定的 JSON 里。

保留的全部能力（与本机验证过的原程序一致）：GTK3 透明无边框置顶窗口、
EWMH STICKY 跨工作区自愈重试、五态姿态（idle/running/waiting/failed/review）、
v2 图集的 16 向 look（看向光标）、空闲时随机卖萌、拖动/双击跳跃、
右键菜单、「工作状态」对话框。

限制：任务通知气泡与工具审批交互依赖 DSH 客户端运行时，独立窗口拿不到，
本桌面版只做陪伴动画与状态反映；通知与审批请回 DSH 界面看。

用法：
    pet.py --assets-root <上游宠物包根> [--config <配置路径>] [--port <端口>]
           [--wait] [--app-id <应用ID>] [--backend x11|wayland]
"""

import argparse
import glob
import io
import json
import math
import os
import random
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

try:
    import compression.zstd as _zstd  # Python 3.14+ 标准库
except ImportError:  # 老解释器退回 zstd CLI（见 read_session_bytes）
    _zstd = None


# =====================================================================
# 常量
# =====================================================================

# Gtk.Application 的单实例 id。刻意带 -linux 后缀与「个人用」的
# ~/.local/bin/dsh-pet-desktop.py（app-id com.wlaeg.dsh-pet-desktop）区分开：
# 两者一旦撞 id，后启动的那个会在 app.run() 内部把参数转发给先启动的实例后
# **静默退出**（连 do_activate 都不调用，退出码还是 0），现象就是插件报
# 「子进程启动后立即退出」却没有任何日志线索。
APP_ID_DEFAULT = "com.wlaeg.dsh-pet-desktop-linux"
DEFAULT_PORT = 19387
DEFAULT_CONFIG_PATH = "~/.dsh/codex-pet-desktop/config.json"

# 精灵图单元尺寸（宽 192、高 208，192:208 即 client.js 的 size*208/192 比例）
CELL_W, CELL_H = 192, 208

SIZE_MIN, SIZE_MAX, SIZE_DEFAULT = 64, 224, 104

# 上游 @michengai/dsh-codex-pet 的内置宠物表（lib/index.js 的 BUILTINS）。
# 显示名与顺序都照抄上游，保证与 DSH 页面里看到的对得上。
PET_NAMES = {
    "codex": "Codex",
    "dewey": "Dewey",
    "fireball": "Fireball",
    "hoots": "Hoots",
    "rocky": "Rocky",
    "seedy": "Seedy",
    "stacky": "Stacky",
    "bsod": "BSOD",
    "null-signal": "Null Signal",
}
PET_ORDER = list(PET_NAMES)

DEFAULT_CONFIG = {
    "selected": "codex",
    "size": SIZE_DEFAULT,
    "hidden": False,
    "position": {"x": 0.94, "y": 0.70},
}

# 与插件 client.js 的 ANIMATIONS 表逐帧一致
ANIMATIONS = {
    "idle": {"row": 0, "durations": [280, 110, 110, 140, 140, 320]},
    "running-right": {"row": 1, "durations": [120, 120, 120, 120, 120, 120, 120, 220]},
    "running-left": {"row": 2, "durations": [120, 120, 120, 120, 120, 120, 120, 220]},
    "waving": {"row": 3, "durations": [140, 140, 140, 280]},
    "jumping": {"row": 4, "durations": [140, 140, 140, 140, 280]},
    "failed": {"row": 5, "durations": [140, 140, 140, 140, 140, 140, 140, 240]},
    "waiting": {"row": 6, "durations": [150, 150, 150, 150, 150, 260]},
    "running": {"row": 7, "durations": [120, 120, 120, 120, 120, 220]},
    "review": {"row": 8, "durations": [150, 150, 150, 150, 150, 280]},
}

# 姿态 → 人话（右键菜单与「工作状态」对话框共用）
POSE_LABELS = {
    "idle": "空闲",
    "running": "工作中",
    "waiting": "等待你处理",
    "failed": "任务出错了",
    "review": "已完成，待查看",
}


# =====================================================================
# 依赖自检
# =====================================================================

INSTALL_HINTS = """\
  Arch / Manjaro  : sudo pacman -S python-gobject gtk3 gdk-pixbuf2 webp-pixbuf-loader xdotool
  Debian / Ubuntu : sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-gdkpixbuf-2.0 webp-pixbuf-loader xdotool
                    （Ubuntu 22.04 的 webp-pixbuf-loader 在第三方 PPA；Debian bookworm 起在官方仓库）
  Fedora / RHEL   : sudo dnf install python3-gobject gtk3 gdk-pixbuf2 webp-pixbuf-loader xdotool
  openSUSE        : sudo zypper install python3-gobject gtk3 gdk-pixbuf-loader-webp xdotool
                    （webp-pixbuf-loader 需先添加 Packman 源）
"""


def fatal_dependency(what, detail="", code=3):
    """缺依赖：用中文说清楚缺什么、怎么装，并以非 0 退出码结束。"""
    print(f"[pet-desktop] 错误：{what}", file=sys.stderr)
    if detail:
        print(f"[pet-desktop] 详细信息：{detail}", file=sys.stderr)
    print("[pet-desktop] 请按你的发行版安装缺失的依赖：", file=sys.stderr)
    print(INSTALL_HINTS, file=sys.stderr, end="")
    sys.exit(code)


# ---------------------------------------------------------------------
# 参数解析与后端引导（必须早于任何 gi.repository 导入）
#
# 为什么是 GTK3 + X11，而不是 GTK4 / Wayland layer-shell（三条路都实测过）：
#
#  1. wlr-layer-shell 在 GNOME/mutter 上根本不存在（官方 issue #1141 至今未实现，
#     社区口径是"supported on every desktop compositor other than Mutter"），
#     gtk4-layer-shell 在 GNOME 上报 "your Wayland compositor doesn't support
#     Layer Shell"，此路不通。
#  2. GTK4 已移除 set_keep_above / set_position，只能借 xdotool 写 EWMH
#     （ABOVE/STICKY/SKIP_TASKBAR/SKIP_PAGER，且 windowstate 一次只吃一个 --add）。
#  3. 真正的拦路虎是 **GTK4 顶层窗口无法透明**：窗口区域恒定自绘主题底色
#     rgb(59,59,59)。CSS !important、Gtk.Widget.set_opacity、cairo 清屏、
#     改 X11 background_pixel 全部无效；换 cairo 渲染器（GSK_RENDERER=cairo）
#     结果一致，说明与渲染器无关。判据是"unmap 窗口前后截图逐像素求差"：
#     GTK4 窗口内 100% 与 unmap 后不同（= 窗口确实自绘了不透明底色）。
#  4. GTK3 用同一份 unmap 判据实测通过：窗口内非宠物像素 100% 与 unmap 后
#     一致（真透明），只有宠物精灵图本身覆盖像素有差异。关键配方三件套：
#     set_app_paintable(True) + set_visual(screen.get_rgba_visual()) +
#     CSS "background-color: transparent"。
#
# 因此本程序固定在 XWayland 上跑 GTK3（GDK_BACKEND=x11 必须在 GDK 建立
# display 之前设好，否则拿到的是 wayland display）。GNOME Wayland 会话下
# XWayland 始终可用；set_keep_above/set_skip_taskbar_hint 由 mutter 尊重。
# ---------------------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(
        prog="pet.py",
        description="DSH 桌面宠物（GTK3 透明置顶窗口）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--assets-root",
        required=True,
        help="上游宠物插件包根目录（由 Node 侧 require.resolve 解析后传入）；"
        "精灵图位于 <assets-root>/assets/codex/<pet_id>/spritesheet.webp",
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help=f"配置文件路径（默认 {DEFAULT_CONFIG_PATH}）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"DSH web 端口（默认取环境变量 DSH_WEB_PORT，再默认 {DEFAULT_PORT}）；"
        "仅用于「打开 DSH 界面」",
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="被动等待模式：DSH web 未就绪时不退出，每 5 秒重试，就绪后再显示宠物",
    )
    parser.add_argument(
        "--app-id",
        default=APP_ID_DEFAULT,
        help=f"Gtk.Application 单实例 id（默认 {APP_ID_DEFAULT}）；"
        "同一 id 重复运行只会唤起已有窗口，测试时请用不同 id 隔离",
    )
    parser.add_argument(
        "--backend",
        choices=("x11", "wayland"),
        default=None,
        help="GDK 后端，默认 x11（Wayland 下无法做透明置顶窗口，仅供排查用）",
    )
    return parser


def _fatal_usage(message, code=2):
    print(f"[pet-desktop] 错误：{message}", file=sys.stderr)
    sys.exit(code)


# 本文件是脚本而非库，模块级解析裸参数是刻意的：GDK_BACKEND 必须早于
# import gi 设置，而参数又会决定用哪个后端。--help 也因此能在没装 gi 的
# 机器上正常工作。
_ARGS = build_parser().parse_args(sys.argv[1:])

_BACKEND = _ARGS.backend or os.environ.get("PET_BACKEND") or "x11"
if _BACKEND != "wayland":
    os.environ["GDK_BACKEND"] = "x11"


try:
    import gi

    gi.require_version("Gdk", "3.0")
    gi.require_version("Gtk", "3.0")
    gi.require_version("GdkPixbuf", "2.0")
    gi.require_version("Pango", "1.0")

    from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango
except Exception as _gi_error:  # noqa: BLE001 - 缺依赖要给人话，不是堆栈
    fatal_dependency(
        "无法导入 PyGObject(gi) 或 GTK3/GdkPixbuf 的 typelib",
        detail=f"{type(_gi_error).__name__}: {_gi_error}",
    )


def check_runtime_dependencies():
    """检查那些 import 成功也未必可用的运行时依赖（webp 解码器、xdotool）。"""
    missing = []
    try:
        formats = {item.get_name().lower() for item in GdkPixbuf.Pixbuf.get_formats()}
    except Exception:  # noqa: BLE001
        formats = set()
    if "webp" not in formats:
        missing.append(
            "GdkPixbuf 缺少 WebP 解码器（webp-pixbuf-loader）——精灵图是 webp 格式"
        )
    if shutil.which("xdotool") is None:
        missing.append("未找到 xdotool 命令（GTK3 没有跨工作区 STICKY 的 API，必须借它写 EWMH）")
    if missing:
        fatal_dependency("缺少运行时依赖：" + "；".join(missing))
    return sorted(formats)


def app_id_owner(app_id):
    """会话总线上这个 Gtk.Application 单实例名是否已被别的进程占用。

    Gtk.Application 用 D-Bus 名做单实例仲裁：同名的第二个进程不会报错，而是在
    run() 里把命令行转发给已有实例后**静默退出**（do_activate 根本不会被调用，
    退出码 0）。对插件来说这表现为「子进程启动后立即退出」且日志没有任何线索，
    所以启动前主动探一次，把真实原因说清楚。
    """
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        result = bus.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "NameHasOwner",
            GLib.Variant("(s)", (app_id,)),
            None,
            Gio.DBusCallFlags.NONE,
            3000,
            None,
        )
        return bool(result.unpack()[0])
    except Exception:  # noqa: BLE001 - 探测失败不该拦住启动
        return False


# =====================================================================
# 配置文件（--config 指定的 JSON，本程序自管）
# =====================================================================


def normalize_config(raw, base=None):
    """把任意输入收敛成一份合法配置；非法字段退回 base/默认值。"""
    data = dict(base or DEFAULT_CONFIG)
    if not isinstance(raw, dict):
        return data

    selected = raw.get("selected")
    if isinstance(selected, str) and selected.strip():
        data["selected"] = selected.strip()

    size = raw.get("size")
    if isinstance(size, (int, float)) and not isinstance(size, bool):
        try:
            data["size"] = max(SIZE_MIN, min(SIZE_MAX, int(round(float(size)))))
        except (TypeError, ValueError):
            pass

    hidden = raw.get("hidden")
    if isinstance(hidden, bool):
        data["hidden"] = hidden

    position = raw.get("position")
    if isinstance(position, dict):
        try:
            x = float(position.get("x"))
            y = float(position.get("y"))
        except (TypeError, ValueError):
            pass
        else:
            data["position"] = {
                "x": max(0.0, min(1.0, x)),
                "y": max(0.0, min(1.0, y)),
            }
    return data


class ConfigStore:
    """读写 --config 指定的 JSON，并在文件被外部改动时热重载。

    插件 Node 半身通过自己的 API 读写同一份文件；本程序每轮轮询比对
    文件内容，变了就热应用（换宠物/改大小/显示隐藏/挪位置），两边不打架。

    写入走「临时文件 + os.replace」原子替换，避免 Node 端读到半个文件。
    """

    def __init__(self, path):
        self.path = os.path.abspath(os.path.expanduser(path))
        self.data = dict(DEFAULT_CONFIG)
        self.load()

    def load(self):
        """读盘；返回是否与内存中的配置不同。"""
        try:
            with open(self.path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError):
            return False
        fresh = normalize_config(raw, base=self.data)
        if fresh == self.data:
            return False
        self.data = fresh
        return True

    def ensure_exists(self):
        """文件不存在时落一份默认配置，便于 Node 半身直接读。

        只在**不存在**时创建：已存在的文件连内容带 mtime 都保持原样，
        避免每次启动都改写用户的位置/大小（Node 侧也会读它做状态展示）。
        返回值是「是否新建了文件」（save() 的返回值表示内容有无变化，不同）。
        """
        if os.path.exists(self.path):
            return False
        self.save()
        return os.path.exists(self.path)

    def save(self, patch=None):
        """合并 patch 并原子落盘；返回 True 表示配置确实变了。"""
        merged = dict(self.data)
        if patch:
            merged.update(patch)
        fresh = normalize_config(merged, base=self.data)
        changed = fresh != self.data
        self.data = fresh
        payload = json.dumps(fresh, ensure_ascii=False, indent=2) + "\n"
        tmp = self.path + ".tmp"
        try:
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        except OSError as error:
            print(f"[pet-desktop] 配置写入失败 {self.path}: {error}", file=sys.stderr)
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return changed


# =====================================================================
# 精灵图（GdkPixbuf 解码 + 裁帧 + 缩放；零 Pillow、零 pycairo）
# =====================================================================


class SpriteSheet:
    """一张 spritesheet.webp，按需裁出并缩放单帧。

    帧尺寸只有 (行, 列, 目标宽) 三个变量，用缓存把它们固定下来 —— 播放动画
    时每帧只是 Gtk.Image.set_from_pixbuf(缓存对象)，不重复解码/缩放。
    """

    def __init__(self, path):
        with open(path, "rb") as handle:
            raw = handle.read()
        loader = GdkPixbuf.PixbufLoader()
        loader.write(raw)
        loader.close()
        pixbuf = loader.get_pixbuf()
        if pixbuf is None or pixbuf.get_width() < CELL_W or pixbuf.get_height() < CELL_H:
            raise ValueError(f"精灵图无法解码或尺寸异常: {path}")
        self.path = path
        self.sheet = pixbuf
        # v2 图集高 11 行（前 9 行姿态 + 后 2 行 16 向 look），v1 只有 9 行姿态
        self.version = 2 if pixbuf.get_height() == CELL_H * 11 else 1
        self._cache = {}

    @property
    def size(self):
        return self.sheet.get_width(), self.sheet.get_height()

    def clear_cache(self):
        self._cache.clear()

    def frame(self, row, col, width):
        """裁出 (row, col) 单元并缩放到 width 宽（高按 208/192 等比），失败返回 None。"""
        width = max(1, int(width))
        key = (row, col, width)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        x, y = col * CELL_W, row * CELL_H
        if x < 0 or y < 0:
            return None
        if x + CELL_W > self.sheet.get_width() or y + CELL_H > self.sheet.get_height():
            return None
        height = max(1, round(width * CELL_H / CELL_W))
        cell = self.sheet.new_subpixbuf(x, y, CELL_W, CELL_H)
        scaled = cell.scale_simple(width, height, GdkPixbuf.InterpType.NEAREST)
        if scaled is None:
            return None
        self._cache[key] = scaled
        return scaled


def scan_pets(sprites_dir):
    """扫描 <assets-root>/assets/codex/ 下的宠物目录，按上游 BUILTINS 排序。"""
    pets = []
    try:
        entries = list(os.scandir(sprites_dir))
    except OSError:
        return pets
    for entry in entries:
        try:
            if not entry.is_dir():
                continue
        except OSError:
            continue
        sheet = os.path.join(entry.path, "spritesheet.webp")
        if not os.path.isfile(sheet):
            continue
        pets.append(
            {
                "id": entry.name,
                "name": PET_NAMES.get(entry.name, entry.name),
                "path": sheet,
            }
        )
    order = {pet_id: index for index, pet_id in enumerate(PET_ORDER)}
    pets.sort(key=lambda item: (order.get(item["id"], len(order)), item["id"]))
    return pets


def look_cell(dx, dy):
    """光标相对宠物中心的方向 → v2 图集的 (row, col)；距离太近返回 None。"""
    if math.hypot(dx, dy) < 28:
        return None
    index = round((math.atan2(dx, -dy) + math.tau) % math.tau / (math.pi / 8)) % 16
    return (9 + index // 8, index % 8)


# =====================================================================
# 干活状态的数据源（见 SessionWatcher）
#
# DSH 把每个会话的事件流 append 到 <DSH_HOME>/sessions/<workspace>/<id>/
# session.v4.jsonl.zstd（zstd 压缩的 JSONL）。这是独立进程唯一能拿到的
# "会话正在干什么" 的实时信号。
# =====================================================================

DSH_HOME = os.environ.get("DSH_HOME") or os.path.expanduser("~/.dsh")
SESSIONS_ROOT = os.path.join(DSH_HOME, "sessions")
SESSION_GLOB = os.path.join(SESSIONS_ROOT, "*", "*", "session.v4.jsonl.zstd")

# 每轮最多精确解析这么多个 mtime 最新的会话文件。活跃会话必然排在前面；
# 更老的文件要么 open turn 已超期（僵尸），要么 failed/review 早过期，
# 剪掉它们语义无损，却把每轮代价从 O(全部会话) 降到 O(活跃会话)。
SESSION_SCAN_LIMIT = 12
# open turn 且文件在这么久内被写过 == 那个会话真的还在干活。超过则视为
# 僵尸轮次（DSH 崩溃/被杀的残留 turn/start，永远等不到配对的 turn/end）。
SESSION_RUNNING_FRESH = 300.0
# 文件超过这么久没动，之后不可能再有任何姿态可显示（剪枝用）
SESSION_STALE_AFTER = 1800.0
# 出错 / 完成待看两种"结果姿态"各保留多久（照插件语义：要让用户看见）
FAILED_TTL = 300.0
REVIEW_TTL = 180.0
# 挂着提问/审批时文件不再被写入（会话阻塞在等你），所以不能复用按 mtime 算的
# SESSION_RUNNING_FRESH，否则等 5 分钟姿态就自己消失了。用户一旦回答，事件流里
# 就会补上 tool/result，waiting 立刻自然消失，这个上限只兜底"永远没人回答"。
WAITING_TTL = 1800.0
# 后台扫描间隔（在独立线程里跑，避免阻塞 GTK 主循环）
SESSION_POLL_SECONDS = 2.0

# turn/end 的 data.reason.kind → 该轮结束后的姿态。
# 实测 DSH 会写出这几种：completed / error / interrupted / aborted / max-tokens。
# interrupted（会话被打断）与 aborted（用户主动停止）是中性结束，不报错也不庆祝。
FAILED_END_KINDS = frozenset({"error", "max-tokens"})

# 字节级预筛：一个会话文件里 95% 以上的行是 tool/call、assistant/message
# 这类我们完全不关心的大块事件（实测 2151 行里只有 9 行是 turn 事件）。
# 先按子串跳过，只对极少数候选行做 decode + json.loads —— 每文件只解码
# 十余行，而不是整个 8.7MB 明文。zstd 解压那部分无法避免。
_TURN_MARK = b'"turn/'
_TOOL_MARK = b'"tool/'
# 会话标题（session/title 事件，DSH 会给每个会话起名，通常只有 1~2 条）。
# 状态对话框里要显示"哪个会话在干活"，光有 uuid 没法看。
_TITLE_MARK = b'"session/title"'

# 「等待用户处理」的工具：调用后会阻塞，直到用户回答/批准才写下 tool/result。
# 这是 waiting（"等待你处理"）在落盘数据里唯一可靠的信号 —— 插件用的是客户端
# 内存里的 pending 快照（client.js:1982 `requests.getSnapshot()`），独立进程
# 拿不到；但「有调用、没结果」在事件流里是同义的。
WAITING_TOOLS = ("ask_user_question", "exit_plan_mode")
_WAITING_MARKS = tuple(name.encode() for name in WAITING_TOOLS)


def human_age(seconds):
    """把秒数写成人话（对话框与菜单里的新鲜度）。"""
    if seconds < 60:
        return f"{seconds:.0f} 秒前"
    if seconds < 3600:
        return f"{seconds / 60:.0f} 分钟前"
    if seconds < 86400:
        return f"{seconds / 3600:.1f} 小时前"
    return f"{seconds / 86400:.1f} 天前"


def read_session_bytes(path):
    """解压一个会话文件，返回其中的**原始字节**（未解码）。

    文件是 append-only 且正在被写入，读到末尾常常遇到**不完整的 zstd 帧**：
    此时解码器抛 EOFError/ZstdError，但之前解出的明文是完好的 —— 保留它，
    丢掉尾巴即可（下一次扫描会拿到完整内容）。这条容错是必须的，实测
    每次读取正在写的会话文件都会在末尾中断。
    """
    with open(path, "rb") as handle:
        raw = handle.read()
    if not raw:
        return b""
    if _zstd is not None:
        chunks = []
        try:
            with _zstd.open(io.BytesIO(raw), "rb") as stream:
                while True:
                    chunk = stream.read(1 << 20)
                    if not chunk:
                        break
                    chunks.append(chunk)
        except Exception:  # noqa: BLE001 - 截断帧：保留已解出的部分
            pass
        return b"".join(chunks)
    # 兜底：解释器没有 compression.zstd（Python < 3.14）时用 CLI
    result = subprocess.run(
        ["zstd", "-dc", "--no-check", path],
        capture_output=True,
        timeout=10,
    )
    return result.stdout


def _session_meta(line):
    """解析会话首行元数据，失败返回 {}。

    首行是整个文件的元数据（`{"type":"session","version":4,"id":…,"cwd":…,
    "origin":…}`），id / cwd / origin 都从这里取。
    """
    try:
        meta = json.loads(line.decode("utf-8", "replace"))
    except ValueError:
        return {}
    return meta if isinstance(meta, dict) else {}


def _is_subagent(meta):
    """元数据是否属于 subagent 会话。

    不能只做子串匹配（`<cwd>` 里也可能出现 "subagent"），所以老实解析这一行
    JSON，取 origin 字段；parentSession 非空是旧版本的等价信号。
    """
    if meta.get("origin") == "subagent":
        return True
    return bool(meta.get("parentSession"))


def _parse_questions(arguments):
    """把 ask_user_question 的 arguments 解析成待展示的问题列表。

    只要问题、选项标签与说明 —— 状态对话框是只读的（回答请回 DSH 界面）。
    解析不出来时返回空列表，不让展示层出错。
    """
    if not arguments:
        return []
    try:
        payload = json.loads(arguments)
    except ValueError:
        return []
    raw = payload.get("questions") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return []
    questions = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        options = []
        for option in item.get("options") or []:
            if isinstance(option, dict) and option.get("label"):
                options.append(
                    {
                        "label": str(option["label"]),
                        "description": str(option.get("description") or ""),
                    }
                )
            elif isinstance(option, str):
                options.append({"label": option, "description": ""})
        questions.append(
            {
                "header": str(item.get("header") or ""),
                "question": str(item.get("question") or ""),
                "options": options,
            }
        )
    return questions


def parse_session(path):
    """统计一个会话文件，返回 dict 或 None。

    姿态判定用：
      opens        = turn/start 数 - turn/end 数（>0 表示有轮次没结束）
      end_kind     = 最近一次 turn/end 的 reason.kind（completed/error/…）
      end_ms       = 最近一次 turn/end 的毫秒时间戳
      is_subagent  = 首行元数据的 origin 是否为 subagent
      waiting      = 有 ask_user_question/exit_plan_mode 调用尚未拿到结果
    展示用（「工作状态」对话框）：
      id / title / cwd / questions

    插件 client.js 判定姿态时明确把 subagent 会话排除在外（`origin !==
    "subagent"` 过滤）：子任务的忙碌已经体现在主会话的 open turn 上，若单独
    参与判定，子任务一结束就会误报"已完成待查看"。

    waiting 的判定见 WAITING_TOOLS 的说明：以「同名工具调用出现两次以上」
    （call 一次 + result 一次）作为已处理的证据，只出现一次即为悬挂。
    callId 是全局唯一的短串，全文字节计数比逐行配对快得多。
    """
    blob = read_session_bytes(path)
    if not blob:
        return None
    opens = 0
    end_kind = None
    end_ms = 0
    saw_any = False
    is_subagent = False
    session_id = ""
    cwd = ""
    title = ""
    pending = {}  # callId 字节 → arguments 字符串（只记等待类工具）
    first = True
    for line in blob.splitlines():
        if first:
            first = False
            meta = _session_meta(line)
            is_subagent = _is_subagent(meta)
            session_id = str(meta.get("id") or "")
            cwd = str(meta.get("cwd") or "")
        if _TURN_MARK in line:
            try:
                event = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            if kind == "turn/start":
                opens += 1
                saw_any = True
            elif kind == "turn/end":
                opens -= 1
                reason = (event.get("data") or {}).get("reason") or {}
                end_kind = reason.get("kind")
                stamp = event.get("time")
                if isinstance(stamp, int):
                    end_ms = stamp
                saw_any = True
            continue
        # 会话标题（DSH 起的名字，通常 1~2 条：先 fallback 再 provider 覆盖）
        if _TITLE_MARK in line:
            try:
                event = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                continue
            if not isinstance(event, dict) or event.get("type") != "session/title":
                continue
            data = event.get("data") or {}
            if isinstance(data.get("title"), str) and data["title"].strip():
                title = data["title"].strip()
            continue
        # 等待类工具：先做子串预筛（这类调用极稀有，全库几百个文件里只有
        # 26 次），命中才解析 JSON 确认，避免为 7000 行 tool/call 白付解析成本
        if _TOOL_MARK in line and any(mark in line for mark in _WAITING_MARKS):
            try:
                event = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                continue
            if not isinstance(event, dict) or event.get("type") != "tool/call":
                continue
            data = event.get("data") or {}
            if data.get("name") in WAITING_TOOLS and data.get("callId"):
                call_id = str(data["callId"]).encode()
                pending[call_id] = str(data.get("arguments") or "")
    if not saw_any:
        return None
    waiting = False
    questions = []
    for call_id, arguments in pending.items():
        # 出现 1 次 = 只有调用没有结果 = 正挂着等用户回答。
        # 用带引号的字节串计数，否则 call_ab12 会误匹配 call_ab1234。
        if blob.count(b'"' + call_id + b'"') < 2:
            waiting = True
            questions.extend(_parse_questions(arguments))
    return {
        "opens": max(0, opens),
        "end_kind": end_kind,
        "end_ms": end_ms,
        "is_subagent": is_subagent,
        "waiting": waiting,
        "id": session_id,
        "cwd": cwd,
        "title": title,
        "questions": questions,
    }


class SessionWatcher:
    """读 DSH 会话事件文件，判定「此刻有没有会话在干活」。

    为什么不用插件那套：网页版的状态来自 DSH 客户端运行时（浏览器内存里的
    session store），宿主进程不暴露，HTTP API 也没有这个端点（实测
    /api/sessions 等一律 401）。独立进程只能自己从会话事件文件推断。

    数据源 <DSH_HOME>/sessions/<workspace>/<id>/session.v4.jsonl.zstd，用到：
      turn/start             —— 一轮开始
      turn/end  data.reason  —— 一轮结束，kind = completed/error/interrupted/…
    判定（优先级从上到下）：
      open_turns > 0 且文件新鲜          → running（正在干活）
      最近 turn/end 是 error 且够新      → failed
      最近 turn/end 是 completed 且够新  → review
      否则                               → idle

    优先级与插件 selectActivity 不同：插件是 failed 优先（网页里还有通知气泡
    兜底），这里 running 优先 —— 独立窗口的首要职责是如实反映"此刻在不在
    干活"，用历史错误盖住当前工作会误导人。

    扫描跑在后台 daemon 线程里（zstd 解压 + JSON 解析不适合放进 GTK 主循环），
    结果写进 self.pose，主线程按字符串读（GIL 下赋值是原子的）。
    """

    def __init__(self):
        self.pose = "idle"
        self.detail = ""  # 人话说明，进日志/右键菜单
        self.sessions = []  # 非 idle 的会话明细，供「工作状态」对话框
        self._cache = {}  # path -> (mtime, size, parsed)
        self._stop = False
        self._thread = None

    # ---------- 对外 ----------

    def start(self):
        self._thread = threading.Thread(
            target=self._loop, name="pet-sessions", daemon=True
        )
        self._thread.start()

    def stop(self):
        self._stop = True

    def refresh(self):
        """扫一遍（同步），更新 self.pose / self.detail / self.sessions。"""
        pose, detail, items = self._decide()
        self.pose = pose
        self.detail = detail
        self.sessions = items

    # ---------- 内部 ----------

    def _loop(self):
        while not self._stop:
            try:
                self.refresh()
            except Exception as error:  # noqa: BLE001 - 后台线程绝不能死
                self.pose = "idle"
                self.detail = f"会话扫描异常: {error}"
                self.sessions = []
            for _ in range(int(SESSION_POLL_SECONDS * 10)):
                if self._stop:
                    return
                time.sleep(0.1)

    def _decide(self):
        try:
            paths = glob.glob(SESSION_GLOB)
        except OSError:
            return ("idle", "", [])
        if not paths:
            return ("idle", "", [])
        try:
            paths.sort(key=os.path.getmtime, reverse=True)
        except OSError:
            return ("idle", "", [])
        now = time.time()
        running = failed = review = waiting = False
        running_age = None
        items = []  # 非 idle 的会话，供「工作状态」对话框展示
        for path in paths[:SESSION_SCAN_LIMIT]:
            try:
                stat = os.stat(path)
            except OSError:
                continue
            age = now - stat.st_mtime
            if age < 0:
                # 文件 mtime 略超前于本进程的 time.time()（NTP 微调或文件系统时间
                # 精度）会出现 -0.4s 这种值，格式化后是「-0s 前有活动」，很难看。
                age = 0.0
            if age > SESSION_STALE_AFTER and age > WAITING_TTL:
                # 太旧：既不可能还在跑，也不可能还挂着等你（两个上限取大者）。
                # 这里不能 break —— 排序是按 mtime 的，但一个 30 分钟前开始
                # 等你回答的会话，文件时间同样很旧，却必须继续显示 waiting。
                continue
            parsed = self._cached(path, stat)
            if parsed is None:
                continue
            if parsed["is_subagent"]:
                # 与插件一致：只认主会话。子会话的忙碌已经反映在主会话的
                # open turn 上，单独统计只会让子任务一结束就报"已完成待查看"。
                continue
            ended_ago = now - parsed["end_ms"] / 1000.0 if parsed["end_ms"] else age
            # 单个会话的姿态（与下面的整体优先级同一套判据）
            if parsed["waiting"] and age <= WAITING_TTL:
                pose = "waiting"
                waiting = True
            elif parsed["opens"] > 0 and age <= SESSION_RUNNING_FRESH:
                pose = "running"
                running = True
                if running_age is None or age < running_age:
                    running_age = age
            elif (
                parsed["end_kind"] in FAILED_END_KINDS
                and ended_ago <= FAILED_TTL
            ):
                pose = "failed"
                failed = True
            elif parsed["end_kind"] == "completed" and ended_ago <= REVIEW_TTL:
                pose = "review"
                review = True
            else:
                continue  # idle 的会话不进对话框
            items.append(
                {
                    "id": parsed["id"] or os.path.basename(os.path.dirname(path)),
                    "title": parsed["title"],
                    "cwd": parsed["cwd"],
                    "pose": pose,
                    "age": age,
                    "questions": parsed["questions"],
                }
            )
        # 对话框里按姿态优先级排（waiting 最急），同级按新鲜度
        order = {"waiting": 0, "running": 1, "failed": 2, "review": 3}
        items.sort(key=lambda item: (order.get(item["pose"], 9), item["age"]))
        # waiting 最高：挂着提问/审批时，会话仍然 open turn（所以也是 running），
        # 但"卡在等你"必须盖过"正在干活" —— 用户此刻要动手才能推进。
        if waiting:
            return ("waiting", "等待你处理", items)
        if running:
            # detail 是「姿态名之外的附加信息」，不要重复姿态名本身 ——
            # 否则菜单会显示"状态：工作中（正在工作（1s 前有活动））"。
            return ("running", f"最近活动：{human_age(running_age)}", items)
        if failed:
            return ("failed", "任务出错了", items)
        if review:
            return ("review", "已完成，待查看", items)
        return ("idle", "", items)

    def _cached(self, path, stat):
        key = (stat.st_mtime, stat.st_size)
        hit = self._cache.get(path)
        if hit is not None and hit[0] == key:
            return hit[1]
        try:
            parsed = parse_session(path)
        except (OSError, subprocess.SubprocessError):
            return None
        self._cache[path] = (key, parsed)
        return parsed


# =====================================================================
# DSH web 端口（仅用于「打开 DSH 界面」，启动不依赖它）
# =====================================================================


def resolve_port(explicit=None):
    """端口优先级：--port > 环境变量 DSH_WEB_PORT > 19387。"""
    if explicit:
        return int(explicit)
    env = os.environ.get("DSH_WEB_PORT", "")
    if env.isdigit():
        return int(env)
    return DEFAULT_PORT


def port_ready(port, timeout=2):
    """DSH web 是否可达（任意 HTTP 响应都算：4xx 也说明服务器在监听）。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True
    except (OSError, ValueError):
        return False


# =====================================================================
# 宠物窗口
# =====================================================================


class PetWindow(Gtk.ApplicationWindow):
    """悬浮在桌面上的宠物窗口：无边框、真透明、始终置顶、跨工作区。"""

    def __init__(self, app, store, pets, port):
        super().__init__(application=app, title="DSH Codex Pet")
        self.store = store
        self.pets = pets
        self.port = port
        self.config = dict(store.data)
        self.sheet = None

        self.base_pose = "idle"
        self.action = None  # 一次性动作（waving/jumping…），播完回到 base_pose
        self.frame = 0
        self.look = None
        self.timer_id = None
        self.dragging = False
        self.drag_origin = (0, 0)
        self.press_root = (0.0, 0.0)
        self.last_release = 0.0
        self.pos_x = 0
        self.pos_y = 0
        self.menu = None
        self._heal_rounds = 10  # 启动后若干轮轮询里自愈 EWMH（见 _ensure_x11_state）
        self._config_mtime = self._stat_config_mtime()
        self.status_dialog = None  # 「工作状态」对话框（同一时刻最多一个）
        self.start_hidden = bool(self.config.get("hidden", False))

        # 干活状态：网页版靠浏览器内存判定，独立进程改读会话事件文件（见 SessionWatcher）
        self.watcher = SessionWatcher()

        self.set_decorated(False)
        self.set_resizable(False)
        self.set_app_paintable(True)
        # 不进任务栏、不进 alt-tab、不抢焦点
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.set_keep_above(True)  # 实测 mutter 下确实产生 _NET_WM_STATE_ABOVE

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)
        else:
            print("[pet-desktop] 警告：屏幕无 RGBA visual，窗口将不透明", file=sys.stderr)
        print(
            f"[pet-desktop] composited={screen.is_composited()} "
            f"rgba_visual={visual is not None}",
            flush=True,
        )

        # 透明配方第 3 件：CSS 把窗口与内容层的背景全部清掉。
        # GTK3 里窗口底色的默认声明来自主题，这里用应用级 provider 覆盖；
        # 实测（最小复现）window/.background 两个选择器即可，配合
        # set_app_paintable + rgba visual 达成真透明。
        css = Gtk.CssProvider()
        css.load_from_data(
            b"window, .background, eventbox {"
            b"  background-color: rgba(0, 0, 0, 0);"
            b"  background-image: none;"
            b"  box-shadow: none;"
            b"  border: none;"
            b"}"
            # 上面的规则是应用级（add_provider_for_screen），会连「工作状态」
            # 对话框一起清透明 —— 对话框是要看的，必须显式把底色加回来。
            # Gtk.Dialog 的 CSS 节点是 window > dialog，class 挂在 dialog 上，
            # 所以两个名字都要写；特异性上再加一层 .background 保险。
            b"window.pet-status, window.pet-status.background,"
            b"dialog.pet-status, dialog.pet-status.background {"
            b"  background-color: rgba(28, 28, 32, 0.97);"
            b"  border-radius: 12px;"
            b"}"
            b"window.pet-status label, dialog.pet-status label {"
            b"  color: #e8e8ea;"
            b"}"
            b".pet-status-title { font-size: 15px; font-weight: bold; }"
            b".pet-status-dim { color: #a0a2a8; }"
            b".pet-status-pose { font-weight: bold; }"
            b".pet-status-waiting { color: #ffcc66; }"
            b".pet-status-running { color: #6cc0ff; }"
            b".pet-status-failed { color: #ff7b72; }"
            b".pet-status-review { color: #7ee787; }"
            b".pet-status-question { color: #ffffff; font-weight: bold; }"
            b".pet-status-option { color: #c9ccd3; }"
        )
        Gtk.StyleContext.add_provider_for_screen(
            screen, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # 事件直接挂在**窗口**上，不要塞进 Gtk.EventBox：
        #   - Gtk.Image 是 no-window 控件，本身不接事件；
        #   - 而 Gtk.EventBox 若 set_visible_window(False)（为了透明必须关掉它
        #     自己的窗口背景），它就**根本没有 GdkWindow**，实测按钮事件命中
        #     0 次 —— 右键菜单/拖动/鼠标跟随会全部失灵。这是本项目踩过最深的
        #     一个坑：窗口透明和事件接收在这里是互相冲突的，解是让顶层窗口
        #     自己接事件（EventBox 保持 visible_window=True 则会出现不透明
        #     灰底，破坏透明）。实测顶层窗口 add_events 后命中正常。
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.connect("button-press-event", self._on_button_press)
        self.connect("button-release-event", self._on_button_release)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("leave-notify-event", self._on_leave)

        self.image = Gtk.Image()
        self.add(self.image)

        fw, fh = self.frame_size
        self.image.set_size_request(fw, fh)
        self.resize(fw, fh)
        # 先在「还没映射」的时候把目标位置算好并 move 下去，这样窗口第一次
        # 出现在屏幕上就已经在正确位置；否则会先在默认的 (0,0) 闪一下再跳
        # 过去（实测 _on_mapped 日志里打印的坐标是 (0,0) 就是这个原因）。
        self._place_from_config()

        # ⚠️ connect("map", …) 必须在 show_all() **之前**：show_all() 会立刻触发
        # map，之后再连就永远收不到该信号（实测命中 0 次）。原始的
        # ~/.local/bin 版本正是写在 show_all() 之后，因此它的 _on_mapped 从未
        # 执行过 —— 「窗口已显示」日志一直缺失，300/1000/2500ms 的早期 EWMH
        # 重试也没挂上，只靠 _poll 的 10 轮自愈兜住（实测最终状态仍正确，
        # 但自愈窗口更窄）。这里把它挪到前面。
        self.connect("map", self._on_mapped)

        self.show_all()
        # 注意：这里不能省掉 show_all()（Gtk.Image/EventBox 等子控件要显示），
        # 但也正因为调了它，窗口在 _spawn_window 判断 start_hidden 之前就已经
        # 贴上屏幕了 —— 实测 hidden=true 仍会显示，根因就是这一行。所以紧接着
        # 用 hide() 收起，而不是留到 _spawn_window。用 hide() 而非「不调
        # show_all()」的理由：GtkApplication 只在窗口被 destroyed 时退出，
        # hide 掉的窗口仍算存在，进程能存活等待用户从菜单里重新唤出。
        if self.start_hidden:
            self.hide()

    # ---------- 窗口状态 ----------

    def _on_mapped(self, _window):
        GLib.idle_add(self._apply_position)
        # EWMH 状态必须等 mutter 真正接管窗口之后才写得进去：实测 map 信号里
        # 同步调用会打空；而启动阶段 GTK 自己还会再写一次 _NET_WM_STATE
        # （present() 让它挂上 DEMANDS_ATTENTION），把这时候加进去的 STICKY
        # 抹掉、_NET_WM_DESKTOP 归 0（ABOVE 因 GTK 的 set_keep_above 得以保留）。
        # 因此除了几个时间点重试，还在 _poll 里做幂等自愈（见 _ensure_x11_state）。
        for delay in (300, 1000, 2500):
            GLib.timeout_add(delay, self._apply_x11_state)
        fw, fh = self.frame_size
        print(
            f"[pet-desktop] 窗口已显示 {fw}x{fh}"
            f" @ ({self.pos_x},{self.pos_y}) 宠物={self.config.get('selected')}"
            f" 大小={self.config.get('size')}",
            flush=True,
        )

    def _apply_position(self):
        self._move(self.pos_x, self.pos_y)
        return GLib.SOURCE_REMOVE

    def _apply_x11_state(self):
        """用 EWMH 让窗口悬浮：置顶（ABOVE）+ 全工作区（STICKY）。

        置顶其实 GTK3 的 set_keep_above(True) 就够了（实测在 mutter/XWayland
        下会正确产生 _NET_WM_STATE_ABOVE，这里再写一次是幂等冗余）；
        但 STICKY（跨工作区跟随）GTK3 没有对应 API，必须自己写 EWMH。

        两个坑：xdotool windowstate 一次只接受一个 --add，属性名须大写，
        写成 "add,above" 会被静默忽略；且必须等 WM 接管窗口后再写
        （见 _on_mapped 的延迟调用）。
        """
        try:
            xid = self.get_window().get_xid()
        except (AttributeError, TypeError):
            xid = None
        if not xid:
            return GLib.SOURCE_REMOVE
        for state in ("ABOVE", "STICKY"):
            subprocess.run(
                ["xdotool", "windowstate", "--add", state, str(xid)],
                check=False,
                capture_output=True,
                timeout=2,
            )
        # present() 时窗口拿不到焦点，GTK 会挂 DEMANDS_ATTENTION（任务栏闪烁）；
        # 宠物窗口不需要提示用户，显式清掉。
        subprocess.run(
            ["xdotool", "windowstate", "--remove", "DEMANDS_ATTENTION", str(xid)],
            check=False,
            capture_output=True,
            timeout=2,
        )
        return GLib.SOURCE_REMOVE

    def _ensure_x11_state(self):
        """幂等自愈：STICKY 若被 GTK 启动阶段的重写抹掉就补回来。

        这里刻意**无条件重写**（不再先读 xprop 判断），因为 GTK 到底有没有
        抹掉它并不可靠地能观察到，而 windowstate --add 本身是幂等的。
        这样也免掉了 xprop 依赖。
        """
        try:
            xid = self.get_window().get_xid()
        except (AttributeError, TypeError):
            return
        if not xid:
            return
        self._apply_x11_state()

    # ---------- 几何 ----------

    @property
    def frame_size(self):
        size = int(self.config.get("size", SIZE_DEFAULT))
        return size, round(size * CELL_H / CELL_W)

    def _work_area(self):
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        geometry = monitor.get_workarea()
        return geometry.width, geometry.height

    def _clamp(self, x, y):
        width, height = self._work_area()
        fw, fh = self.frame_size
        return (
            max(0, min(x, width - fw - 16)),
            max(0, min(y, height - fh - 32)),
        )

    def _move(self, x, y):
        self.pos_x, self.pos_y = self._clamp(x, y)
        # 无条件 move：对**尚未映射**的窗口，GTK 只是记下几何提示，等窗口
        # 真正 map 时直接出现在该位置 —— 这正是构造期消除 (0,0) 闪烁所需的
        # 行为（move 本身不会把窗口显示出来）。
        self.move(self.pos_x, self.pos_y)

    def _place_from_config(self):
        """按配置里的归一化 position（无则右下角）摆放。"""
        position = self.config.get("position")
        width, height = self._work_area()
        fw, fh = self.frame_size
        if isinstance(position, dict):
            x = position.get("x", 1) * max(1, width - fw - 16)
            y = position.get("y", 1) * max(1, height - fh - 32)
        else:
            x, y = width - fw - 16, height - fh - 32
        self._move(round(x), round(y))

    def _persist_position(self):
        width, height = self._work_area()
        fw, fh = self.frame_size
        self.store.save(
            {
                "position": {
                    "x": self.pos_x / max(1, width - fw - 16),
                    "y": self.pos_y / max(1, height - fh - 32),
                }
            }
        )
        self._config_mtime = self._stat_config_mtime()

    def _resize_to_config(self):
        fw, fh = self.frame_size
        self.image.set_size_request(fw, fh)
        self.resize(fw, fh)
        if self.sheet is not None:
            self.sheet.clear_cache()  # 尺寸变了，旧帧缓存全部作废
        self._render_frame()
        self._move(self.pos_x, self.pos_y)

    # ---------- 精灵图 ----------

    def load_sprite(self, pet_id):
        """解码 <assets-root>/assets/codex/<pet_id>/spritesheet.webp；失败保持旧图。"""
        entry = next((p for p in self.pets if p["id"] == pet_id), None)
        if entry is None:
            print(f"[pet-desktop] 未知宠物 id: {pet_id}", file=sys.stderr)
            return False
        try:
            sheet = SpriteSheet(entry["path"])
        except Exception as error:  # noqa: BLE001 - 加载失败不应让窗口退出
            print(f"[pet-desktop] 精灵图加载失败 {pet_id}: {error}", file=sys.stderr)
            return False
        self.sheet = sheet
        self.frame = 0
        self.look = None
        width, height = sheet.size
        print(
            f"[pet-desktop] 精灵图 {pet_id} {width}x{height} "
            f"v{sheet.version} alpha={sheet.sheet.get_has_alpha()}",
            flush=True,
        )
        self._render_frame()
        return True

    # ---------- 动画 ----------

    def _current_animation(self):
        return ANIMATIONS[self.action or self.base_pose]

    def _switch_pose(self, pose):
        """干活状态变化时切换基础姿态（idle/running/failed/review）。

        不同姿态的帧数与每帧时长都不同，所以换姿态必须重挂定时器，不能只
        改 base_pose —— 否则会按上一个动画的时长表去索引新动画的帧。
        非 idle 姿态还会打断正在播的一次性动作（卖萌不该盖住"正在干活"）。
        """
        if pose == self.base_pose:
            return
        self.base_pose = pose
        if pose != "idle":
            self.action = None
        self.look = None
        self.frame = 0
        self._render_frame()
        if self.timer_id:
            GLib.source_remove(self.timer_id)
            self.timer_id = None
        animation = self._current_animation()
        self.timer_id = GLib.timeout_add(animation["durations"][0], self._tick)
        detail = f"（{self.watcher.detail}）" if self.watcher.detail else ""
        print(f"[pet-desktop] 姿态 → {pose}{detail}", flush=True)

    def _tick(self):
        animation = self._current_animation()
        self.frame = (self.frame + 1) % len(animation["durations"])
        if self.action is not None and self.frame == 0:
            self.action = None  # 一次性动作播完一圈，回到基础姿态
            animation = self._current_animation()
        self._render_frame()
        self.timer_id = GLib.timeout_add(
            animation["durations"][self.frame], self._tick
        )
        return GLib.SOURCE_REMOVE

    def perform(self, pose):
        if pose not in ANIMATIONS:
            return
        self.look = None
        self.action = pose
        self.frame = 0
        animation = ANIMATIONS[pose]
        self._render_frame()
        if self.timer_id:
            GLib.source_remove(self.timer_id)
        self.timer_id = GLib.timeout_add(animation["durations"][0], self._tick)

    def _random_act(self):
        if (
            self.base_pose == "idle"
            and self.action is None
            and not self.dragging
            and self.get_visible()
        ):
            self.perform("waving" if random.random() < 0.7 else "jumping")
        return GLib.SOURCE_CONTINUE

    # ---------- 渲染 ----------

    def _current_cell(self):
        animation = self._current_animation()
        if self.sheet is not None and self.sheet.version == 2:
            if self.look is not None and self.action is None:
                return self.look  # (row, col)
        return animation["row"], self.frame

    def _render_frame(self):
        """把当前帧交给 Gtk.Image。

        没有 cairo 清屏这一步：Gtk.Image 每次都用整块新 pixbuf 替换旧内容，
        不像 DrawingArea 那样在旧像素上叠加，所以不存在拖影问题。
        """
        if self.sheet is None:
            return
        row, col = self._current_cell()
        fw, _fh = self.frame_size
        pixbuf = self.sheet.frame(row, col, fw)
        if pixbuf is None:
            return
        self.image.set_from_pixbuf(pixbuf)

    # ---------- 交互 ----------

    def _on_button_press(self, _widget, event):
        if event.button == 3:
            self._popup_menu(event)
            return True
        if event.button != 1:
            return False
        self.dragging = True
        self.drag_origin = (self.pos_x, self.pos_y)
        self.press_root = (event.x_root, event.y_root)
        self.look = None
        return True

    def _on_button_release(self, _widget, event):
        if event.button != 1 or not self.dragging:
            return False
        self.dragging = False
        offset_x = event.x_root - self.press_root[0]
        offset_y = event.y_root - self.press_root[1]
        if math.hypot(offset_x, offset_y) < 4:
            # 位移过小视为点击：300ms 内两次点击 = 双击跳跃（模拟插件行为）
            now = GLib.get_monotonic_time() / 1000
            if now - self.last_release < 300:
                self.perform("jumping")
                self.last_release = 0.0
            else:
                self.last_release = now
        else:
            self._persist_position()
        return True

    def _on_motion(self, _widget, event):
        if self.dragging and event.state & Gdk.ModifierType.BUTTON1_MASK:
            self._move(
                round(self.drag_origin[0] + event.x_root - self.press_root[0]),
                round(self.drag_origin[1] + event.y_root - self.press_root[1]),
            )
            return False
        if self.sheet is None or self.sheet.version != 2:
            return False
        if self.base_pose != "idle" or self.action is not None:
            return False
        fw, fh = self.frame_size
        cell = look_cell(event.x - fw / 2, event.y - fh / 2)
        if cell != self.look:
            self.look = cell
            self._render_frame()
        return False

    def _on_leave(self, _widget, _event):
        if self.look is not None:
            self.look = None
            self._render_frame()
        return False

    def _popup_menu(self, event):
        # 每次弹出都重建：菜单里有随状态翻转的标签与只读状态行，
        # 缓存一份会显示过期文案。
        self.menu = None
        self._build_menu()
        self.menu.popup_at_pointer(event)

    def _build_menu(self):
        menu = Gtk.Menu()

        # 只读状态行：让用户随时确认宠物此刻在反映什么（不可点击）
        pose = self.watcher.pose
        label = POSE_LABELS.get(pose, pose)
        detail = self.watcher.detail
        status = Gtk.MenuItem(label=f"状态：{label}" + (f"（{detail}）" if detail else ""))
        status.set_sensitive(False)
        menu.append(status)

        # 与上面那行重复，但这条可点开详情（待处理的问题与选项在里面）
        count = len(self.watcher.sessions or [])
        item = Gtk.MenuItem(
            label=f"查看工作状态详情…（{count} 个会话）" if count else "查看工作状态详情…"
        )
        item.connect("activate", lambda *_: self._open_status_dialog())
        menu.append(item)

        menu.append(Gtk.SeparatorMenuItem())

        pets_item = Gtk.MenuItem(label="切换宠物")
        pets_menu = Gtk.Menu()
        current = self.config.get("selected")
        for pet in self.pets:
            pet_id = str(pet.get("id", ""))
            entry = Gtk.CheckMenuItem(label=pet.get("name", pet_id))
            entry.set_active(pet_id == current)
            entry.connect("activate", self._on_pick_pet, pet_id)
            pets_menu.append(entry)
        pets_item.set_submenu(pets_menu)
        menu.append(pets_item)

        for label, callback in (
            ("放大一点", lambda *_: self._resize_by(16)),
            ("缩小一点", lambda *_: self._resize_by(-16)),
        ):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", callback)
            menu.append(item)

        menu.append(Gtk.SeparatorMenuItem())
        for label, callback in (
            ("隐藏桌面宠物", lambda *_: self._set_desktop_visible(False)),
            ("打开 DSH 界面", lambda *_: self._open_dsh()),
            ("退出桌面宠物", lambda *_: self.get_application().quit()),
        ):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", callback)
            menu.append(item)

        menu.show_all()
        self.menu = menu

    def _on_pick_pet(self, _item, pet_id):
        if pet_id == self.config.get("selected"):
            return
        self._apply_config({"selected": pet_id})

    def _set_desktop_visible(self, visible):
        """桌面宠物自己的显隐开关，持久化在 --config 指定的 JSON 里。

        隐藏后从应用菜单（或再跑一次本程序）启动即可唤回 —— 菜单随窗口一起
        隐藏了，所以「再运行一次」是主要的恢复入口（Gtk.Application 单实例
        会把已有窗口唤回来）。
        """
        self.store.save({"hidden": not visible})
        self.config = dict(self.store.data)
        self._config_mtime = self._stat_config_mtime()
        if visible:
            self._move(self.pos_x, self.pos_y)
            self.present()
        else:
            self.hide()
        return False

    def _resize_by(self, delta):
        size = max(SIZE_MIN, min(SIZE_MAX, int(self.config.get("size", SIZE_DEFAULT)) + delta))
        self._apply_config({"size": size})

    def restore_visible(self):
        """重复启动时把宠物唤回来。"""
        if self.start_hidden or self.config.get("hidden"):
            self._set_desktop_visible(True)

    def _apply_config(self, patch):
        """写回配置文件，并立刻本地生效（不用等下一轮轮询）。"""
        self.store.save(patch)
        self._config_mtime = self._stat_config_mtime()
        self._adopt_config(self.store.data)

    def _adopt_config(self, config):
        """同步配置：换宠物 / 改大小 / 改显隐（position 由拖动与摆放自行处理）。"""
        selected_changed = config.get("selected") != self.config.get("selected")
        size_changed = config.get("size") != self.config.get("size")
        hidden_changed = bool(config.get("hidden")) != bool(self.config.get("hidden"))
        self.config = dict(config)
        if selected_changed:
            self.load_sprite(self.config.get("selected"))
        if size_changed:
            self._resize_to_config()
        if hidden_changed:
            self.start_hidden = bool(config.get("hidden"))
            if self.start_hidden:
                self.hide()
            else:
                self._move(self.pos_x, self.pos_y)
                self.present()

    def _stat_config_mtime(self):
        try:
            return os.stat(self.store.path).st_mtime_ns
        except OSError:
            return None

    def _reload_config_if_changed(self):
        """配置文件被外部（插件的 Node 半身）改动时热应用。"""
        mtime = self._stat_config_mtime()
        if mtime is None or mtime == self._config_mtime:
            return
        self._config_mtime = mtime
        if self.store.load():
            print(f"[pet-desktop] 配置已更新：{self.store.data}", flush=True)
            self._adopt_config(self.store.data)

    def _open_dsh(self):
        Gio.AppInfo.launch_default_for_uri(f"http://127.0.0.1:{self.port}/", None)

    def _open_status_dialog(self):
        """打开「工作状态」对话框（同一个实例只留一个，重复点就置顶）。"""
        if self.status_dialog is not None:
            self.status_dialog.present()
            return
        self.status_dialog = StatusDialog(self, self.watcher)
        self.status_dialog.connect("destroy", self._on_status_dialog_closed)

    def _on_status_dialog_closed(self, _widget):
        self.status_dialog = None

    # ---------- 轮询 ----------

    def _poll(self):
        # 干活状态与 DSH web 是否可达无关（数据来自本地会话文件），先处理，
        # 免得 DSH 重启期间宠物卡在旧姿态。
        self._switch_pose(self.watcher.pose)
        # 启动后前 10 轮（约 30 秒）顺带自愈 EWMH 状态，之后 GTK 不再重写窗口属性
        if self._heal_rounds > 0:
            self._heal_rounds -= 1
            self._ensure_x11_state()
        # 配置热重载（Node 半身改配置后，这里跟上）
        self._reload_config_if_changed()
        return GLib.SOURCE_CONTINUE


class StatusDialog(Gtk.Dialog):
    """「工作状态」对话框：列出此刻在干活的 DSH 会话。

    只读展示。挂在提问上的会话会把问题与选项一并列出，但**不能在这里回答**
    —— 答案要写回 DSH 的会话事件流，独立进程没有那条通路。所以对话框只负责
    告诉用户「谁卡住了、在问什么」，动手指回 DSH 界面。
    """

    REFRESH_SECONDS = 2

    def __init__(self, parent, watcher):
        super().__init__(title="工作状态", transient_for=parent)
        self.watcher = watcher
        self.set_default_size(520, 480)
        self.set_skip_taskbar_hint(False)
        self.set_border_width(0)
        # 应用级 CSS 把所有 window 清成了透明，对话框是要看的，靠 pet-status
        # 这个 class 把底色加回来（规则写在 PetWindow 的 CSS provider 里）。
        self.get_style_context().add_class("pet-status")
        self.add_button("关闭", Gtk.ResponseType.CLOSE)
        self.connect("response", lambda *_: self.destroy())

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_size_request(480, 380)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for edge in ("top", "bottom", "start", "end"):
            getattr(self.body, f"set_margin_{edge}")(14)
        scroller.add(self.body)

        area = self.get_content_area()
        area.add(scroller)
        self._signature = None
        self._render()
        self._timer = GLib.timeout_add_seconds(self.REFRESH_SECONDS, self._tick)
        self.connect("destroy", self._on_destroy)
        self.show_all()

    # ---------- 刷新 ----------

    def _tick(self):
        try:
            self._render()
        except Exception as error:  # noqa: BLE001 - 刷新失败不该关掉对话框
            print(f"[pet-desktop] 状态对话框刷新失败: {error}", file=sys.stderr)
        return GLib.SOURCE_CONTINUE

    def _on_destroy(self, _widget):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = None

    def _render(self):
        pose = self.watcher.pose
        detail = self.watcher.detail
        items = list(self.watcher.sessions or [])
        # 内容没变就不重建控件（避免每 2 秒闪一下）。age 按 10 秒粒度量化，
        # 否则秒数每轮都在变，会导致整个列表每 2 秒重建一次。
        signature = (pose, detail, tuple(
            (item.get("id"), item.get("pose"), int(item.get("age", 0) // 10),
             len(item.get("questions") or []))
            for item in items
        ))
        if signature == self._signature:
            return
        self._signature = signature

        for child in self.body.get_children():
            self.body.remove(child)

        head = Gtk.Label()
        head.set_markup(f"<b>状态：{GLib.markup_escape_text(POSE_LABELS.get(pose, pose))}</b>")
        head.set_xalign(0)
        head.get_style_context().add_class("pet-status-title")
        self.body.add(head)

        # detail 与姿态名相同时不必重复（waiting 的 detail 就是"等待你处理"）
        if detail and detail != POSE_LABELS.get(pose, pose):
            sub = Gtk.Label(label=detail)
            sub.set_xalign(0)
            sub.set_line_wrap(True)
            sub.get_style_context().add_class("pet-status-dim")
            self.body.add(sub)

        if not items:
            empty = Gtk.Label(label="当前没有正在工作的会话。")
            empty.set_xalign(0)
            empty.get_style_context().add_class("pet-status-dim")
            self.body.add(Gtk.Separator())
            self.body.add(empty)
            self.body.show_all()
            return

        self.body.add(Gtk.Separator())
        for item in items:
            self.body.add(self._session_block(item))
        self.body.show_all()

    def _session_block(self, item):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        pose = item.get("pose", "idle")
        label = POSE_LABELS.get(pose, pose)

        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label()
        title.set_markup(
            f"<b>{GLib.markup_escape_text(item.get('title') or '(未命名会话)')}</b>"
        )
        title.set_xalign(0)
        title.set_line_wrap(True)
        title.set_max_width_chars(34)
        title_row.pack_start(title, True, True, 0)
        badge = Gtk.Label()
        badge.set_markup(f"<b>{GLib.markup_escape_text(label)}</b>")
        badge.set_xalign(1)
        badge.get_style_context().add_class(f"pet-status-{pose}")
        title_row.pack_end(badge, False, False, 0)
        box.add(title_row)

        meta = " · ".join(
            part
            for part in (
                item.get("cwd") or "",
                human_age(item.get("age", 0)),
            )
            if part
        )
        meta_label = Gtk.Label(label=meta)
        meta_label.set_xalign(0)
        meta_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        meta_label.set_max_width_chars(48)
        meta_label.get_style_context().add_class("pet-status-dim")
        box.add(meta_label)

        for question in item.get("questions") or []:
            box.add(self._question_block(question))
        return box

    def _question_block(self, question):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for edge in ("top", "start"):
            getattr(box, f"set_margin_{edge}")(6 if edge == "top" else 12)

        text = question.get("question") or question.get("header") or "(问题)"
        label = Gtk.Label(label=f"？ {text}")
        label.set_xalign(0)
        label.set_line_wrap(True)
        label.get_style_context().add_class("pet-status-question")
        box.add(label)

        for option in question.get("options") or []:
            description = option.get("description") or ""
            line = f"· {option.get('label', '')}"
            if description:
                line += f" — {description}"
            option_label = Gtk.Label(label=line)
            option_label.set_xalign(0)
            option_label.set_line_wrap(True)
            option_label.get_style_context().add_class("pet-status-option")
            box.add(option_label)

        hint = Gtk.Label(label="（请回 DSH 界面回答）")
        hint.set_xalign(0)
        hint.get_style_context().add_class("pet-status-dim")
        box.add(hint)
        return box


class PetApp(Gtk.Application):
    def __init__(self, store, pets, port, hold=False):
        super().__init__(application_id=_ARGS.app_id, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.store = store
        self.pets = pets
        self.port = port
        self.hold_mode = hold
        self.window = None
        self._holding = False

    def do_activate(self):
        if self.window is not None:
            # 重复启动 = 把宠物唤回来（也是隐藏后唯一的恢复入口，因为菜单
            # 随窗口一起隐藏了）
            self.window.restore_visible()
            return
        if self.hold_mode:
            # --wait 被动等待模式：DSH 还没起来，先占住应用实例（Gtk.Application
            # 在没有窗口时会直接退出），每 5 秒探一次，就绪后再建窗口。
            if self._holding:
                return  # 第二次 activate（重复启动）不要重复挂轮询
            self._holding = True
            self.hold()
            print("[pet-desktop] 等待 DSH web 启动 …", flush=True)
            GLib.timeout_add_seconds(5, self._try_connect)
            return
        self._spawn_window()

    def _try_connect(self):
        if self.window is not None:
            return GLib.SOURCE_REMOVE
        if not port_ready(self.port):
            return GLib.SOURCE_CONTINUE
        print(f"[pet-desktop] 已连接 DSH web（端口 {self.port}）", flush=True)
        self._spawn_window()
        return GLib.SOURCE_REMOVE

    def _on_shutdown(self, _app):
        if self.window is not None:
            self.window.watcher.stop()

    def _spawn_window(self):
        self.window = PetWindow(self, self.store, self.pets, self.port)
        self.window._place_from_config()
        # 必须先拉精灵图，否则 sheet 为 None，渲染直接返回、窗口空白
        self.window.load_sprite(self.window.config["selected"])
        # 显隐由配置文件的 hidden 决定
        if not self.window.start_hidden:
            self.window.present()
        # 动画节拍 + 随机小动作 + 配置/状态轮询
        animation = self.window._current_animation()
        self.window.timer_id = GLib.timeout_add(
            animation["durations"][0], self.window._tick
        )
        GLib.timeout_add(18_000, self.window._random_act)
        GLib.timeout_add_seconds(3, self.window._poll)
        # 会话扫描线程（干活状态）。第一次扫描在线程里立刻执行，约 1 秒后
        # _poll 就能读到 running；不放进 GTK 主循环是因为解压+JSON 解析
        # 会阻塞绘制（实测单次 0.8s）。
        self.window.watcher.start()
        self.connect("shutdown", self._on_shutdown)
        if self._holding:
            self._holding = False
            self.release()


# =====================================================================
# 入口
# =====================================================================


def main():
    check_runtime_dependencies()

    assets_root = os.path.abspath(os.path.expanduser(_ARGS.assets_root))
    sprites_dir = os.path.join(assets_root, "assets", "codex")
    if not os.path.isdir(sprites_dir):
        fatal_dependency(
            f"--assets-root 下找不到精灵图目录：{sprites_dir}",
            detail="该目录应由上游宠物插件包提供（assets/codex/<pet_id>/spritesheet.webp）；"
            "插件 Node 半身会用 require.resolve('@michengai/dsh-codex-pet/package.json') 定位后传入",
        )

    pets = scan_pets(sprites_dir)
    if not pets:
        fatal_dependency(
            f"{sprites_dir} 下没有任何可用宠物（缺少 spritesheet.webp）",
            detail="请确认已安装上游依赖 @michengai/dsh-codex-pet",
        )

    store = ConfigStore(_ARGS.config)
    store.ensure_exists()
    ids = {pet["id"] for pet in pets}
    if store.data.get("selected") not in ids:
        fallback = pets[0]["id"]
        print(
            f"[pet-desktop] 配置里的宠物 {store.data.get('selected')!r} 不存在，回退到 {fallback}",
            file=sys.stderr,
        )
        store.data["selected"] = fallback
        store.save()

    port = resolve_port(_ARGS.port)
    print(
        f"[pet-desktop] 宠物 {store.data['selected']} 大小 {store.data['size']} "
        f"配置 {store.path} 端口 {port} 候选 {len(pets)} 只",
        flush=True,
    )

    hold = False
    if _ARGS.wait:
        if port_ready(port):
            print(f"[pet-desktop] 已连接 DSH web（端口 {port}）", flush=True)
        else:
            hold = True

    # 单实例名被占用时，Gtk.Application 会让本进程静默退出（退出码 0、无任何
    # 日志），所以在这里把真实原因说清楚，而不是让调用方看见「启动后立即退出」。
    if app_id_owner(_ARGS.app_id):
        print(
            f"[pet-desktop] 错误：应用单实例 id {_ARGS.app_id!r} 已被另一个进程占用",
            file=sys.stderr,
        )
        print(
            "[pet-desktop] 详细信息：Gtk.Application 用 D-Bus 名做单实例仲裁，同名的"
            "第二个进程会把激活请求转发给已有实例后静默退出，因此本进程不会显示任何窗口。",
            file=sys.stderr,
        )
        print(
            "[pet-desktop] 解决办法：如果是另一个 DSH profile 的宠物实例，请用 --app-id "
            "指定不同的 id（如 com.wlaeg.dsh-pet-desktop-linux.p<端口>）；"
            "如果只想唤回已有窗口，那已有实例已经在响应了。",
            file=sys.stderr,
        )
        return 4

    app = PetApp(store, pets, port, hold=hold)
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
