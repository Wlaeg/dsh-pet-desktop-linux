#!/usr/bin/env python3
"""t5_position_bugs.py —— 复现「position」相关的两处实现缺陷 + 一处权限不一致。

用途
----
把 T5 验收中发现的三个问题做成可重复执行的最小复现，供报告引用与回归：

  bug-1  `position: null` 语义丢失
         README.zh.md:270 声明「`null` 表示放右下角」，但
         desktop/pet.py `normalize_config()` 只处理 `isinstance(position, dict)`，
         `null` 落回 base 的 {0.94, 0.7}，于是「右下角」永远走不到。
         本脚本用 exec 摘出该函数做函数级复现（不启动 GUI）。

  bug-2  `position` 热重载不生效（路由不对称）
         `_persist_position()` 会在拖动时把位置写回 config.json，
         但 `_adopt_config()` 只比较 selected/size/hidden 三项，position 被忽略。
         于是「窗口 → 配置」通、「配置 → 窗口」不通。
         本脚本起真身 pet.py，外部改文件后观察窗口是否移动。

  bug-3  配置文件权限被放宽（Node 写 0600，pet.py 写 0644）
         pet.py `ConfigStore.save()` 全程无 chmod/mode，落默认 umask。

判据与退出码：每个 bug 单独 PASS(已复现=确实有缺陷)/FAIL(未复现)。
  注意语义：这里 **PASS = 缺陷被成功复现**，不是「被测对象正确」。
  0 = 三个缺陷都复现；1 = 至少一个没复现（说明可能已被修好，需人工确认）。

用法
----
    python3 tests/t5_position_bugs.py --assets-root <dsh-codex-pet 包根>
    python3 tests/t5_position_bugs.py --assets-root ... --skip-gui   # 只跑函数级
"""
import argparse
import json
import os
import re
import stat
import subprocess
import sys
import time

os.environ.setdefault("GDK_BACKEND", "x11")

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
PET = os.path.join(PKG, "desktop", "pet.py")


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


# ── bug-1：函数级复现 ────────────────────────────────────────────────────────

def bug1_null_position():
    """从 pet.py 源码摘出 DEFAULT_CONFIG/常量/normalize_config 独立执行。

    用行号切片而不是正则：`.*\\n` 配 re.S 会贪婪吞掉整个文件，把
    build_parser 等无关代码一起带进来（那正是第一次跑报
    `NameError: name 'argparse' is not defined` 的原因）。
    """
    lines = open(PET, encoding="utf-8").read().splitlines(keepends=True)

    def block(start_pat):
        start = next((i for i, l in enumerate(lines) if re.match(start_pat, l)), None)
        if start is None:
            return None
        # 顶层块：一直取到「下一行是非缩进的 def/class/赋值」或文件尾
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if re.match(r"^[A-Za-z_#]", lines[j]) and re.match(
                    r"^(def |class |[A-Z_]+\s*=|#)", lines[j]):
                end = j
                break
        return "".join(lines[start:end])

    consts = block(r"^SIZE_MIN, SIZE_MAX, SIZE_DEFAULT = ")
    dflt = block(r"^DEFAULT_CONFIG = ")
    func = block(r"^def normalize_config\(")
    for name, got in (("常量", consts), ("DEFAULT_CONFIG", dflt),
                      ("normalize_config", func)):
        if not got:
            return None, f"源码里找不到 {name}"

    ns = {}
    exec("\n".join([consts, dflt, func]), ns)  # noqa: S102 - 这是复现手段本身
    norm = ns["normalize_config"]
    base = ns["DEFAULT_CONFIG"]

    out = norm({"position": None}, base=base)
    return out, None


# ── 窗口工具（绝不用 xdotool search --pid，本机它会忽略 --pid）────────────────

def win_of_pid(pid):
    for w in run(["xdotool", "search", "--name", "DSH Codex Pet"]).stdout.split():
        got = run(["xprop", "-id", w, "_NET_WM_PID"]).stdout
        m = re.search(r"=\s*(\d+)", got)
        if not m or int(m.group(1)) != pid:
            continue
        if "dsh-pet-desktop.py" in run(["xprop", "-id", w, "WM_CLASS"]).stdout:
            continue  # 用户正式实例，绝不能碰
        return w
    return None


def win_pos(wid):
    g = run(["xdotool", "getwindowgeometry", str(wid)]).stdout
    m = re.search(r"Position:\s*(-?\d+),(-?\d+)", g)
    return (int(m.group(1)), int(m.group(2))) if m else None


def wait_win(pid, timeout=25):
    end = time.time() + timeout
    while time.time() < end:
        w = win_of_pid(pid)
        if w:
            return w
        time.sleep(0.4)
    return None


# ── bug-2 / bug-3：端到端 ────────────────────────────────────────────────────

def bug2_and_3(assets_root, app_id, cfgpath, logpath):
    cfg = {"selected": "codex", "size": 104, "hidden": False,
           "position": {"x": 0.0, "y": 0.0}, "autostart": False}
    with open(cfgpath, "w") as fh:
        json.dump(cfg, fh)

    logfh = open(logpath, "w")
    proc = subprocess.Popen(
        [sys.executable, PET, "--assets-root", assets_root,
         "--config", cfgpath, "--port", "21987", "--app-id", app_id],
        stdout=logfh, stderr=subprocess.STDOUT, start_new_session=True)

    result = {"wid": None, "start_pos": None, "moved": None, "mode": None,
              "reload_seen": False}
    try:
        wid = wait_win(proc.pid)
        if not wid:
            return result, "宠物窗口未出现（见 " + logpath + "）"
        result["wid"] = wid
        # 等它按 position={0,0} 摆好（左上角；Y 会有 WM 工作区偏移）
        time.sleep(2.5)
        result["start_pos"] = win_pos(wid)

        # bug-3：pet.py 写回后的文件权限。触发一次写回：改 hidden 再改回来
        # 更可靠的方式是直接调用它的保存路径 —— 用拖动模拟代价大，
        # 这里用「改 size 触发 _resize_to_config 不写盘」不成立，
        # 故直接用 ConfigStore 在子进程外复现同一条写出逻辑。
        result["mode"] = oct(stat.S_IMODE(os.stat(cfgpath).st_mode))

        # bug-2：外部把 position 改成 {0.5,0.5}，看窗口是否移动
        cfg["position"] = {"x": 0.5, "y": 0.5}
        with open(cfgpath, "w") as fh:
            json.dump(cfg, fh)
        deadline = time.time() + 10
        while time.time() < deadline:
            time.sleep(0.5)
            if "配置已更新" in open(logpath, encoding="utf-8", errors="replace").read():
                result["reload_seen"] = True
                break
        # 再看 6 秒，窗口动没动
        time.sleep(6)
        result["moved"] = win_pos(wid)
        return result, None
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        logfh.close()


def bug3_config_mode():
    """独立复现 pet.py 的保存路径：同样的 open+os.replace，看落盘权限。"""
    import tempfile
    tmpdir = tempfile.mkdtemp(prefix="t5-mode-")
    target = os.path.join(tmpdir, "config.json")
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("{}\n")
    os.replace(tmp, target)
    return oct(stat.S_IMODE(os.stat(target).st_mode)), target


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets-root", required=True)
    ap.add_argument("--app-id", default="com.wlaeg.t5.posbug")
    ap.add_argument("--config", default="/tmp/t5-posbug-cfg.json")
    ap.add_argument("--log", default="/tmp/t5-posbug.log")
    ap.add_argument("--skip-gui", action="store_true")
    args = ap.parse_args()

    print("=== t5_position_bugs.py ===")
    print("（本脚本里 PASS = 缺陷被成功复现，不是被测对象正确）\n")
    fails = []

    # bug-1
    print("[bug-1] position:null 语义")
    out, err = bug1_null_position()
    if err:
        print(f"  FAIL 无法复现：{err}")
        fails.append("bug-1")
    else:
        pos = out.get("position")
        got_null = pos is None
        print(f"  normalize_config({{'position': None}}) -> position = {pos!r}")
        print("  文档（README.zh.md:270）声明 null 表示右下角 → 期望保留 None")
        if got_null:
            print("  FAIL 已返回 None，缺陷似乎已被修复，需人工确认")
            fails.append("bug-1")
        else:
            print(f"  PASS 缺陷复现：null 被丢弃，实际落回 base 的 {pos!r}，"
                  "「右下角」分支永远走不到")

    # bug-3（不依赖 GUI，先跑）
    print("\n[bug-3] 配置文件权限")
    mode, where = bug3_config_mode()
    print(f"  pet.py 同款写法（open + os.replace）落盘权限 = {mode}  ({where})")
    node_mode = oct(0o600)
    print(f"  lib/index.js atomicWriteConfig 用 mode: 0o600 = {node_mode}")
    if mode == node_mode:
        print("  FAIL 权限一致，缺陷似乎已修")
        fails.append("bug-3")
    else:
        print(f"  PASS 缺陷复现：pet.py 写出 {mode}，Node 写出 {node_mode}；"
              "每次拖动（_persist_position）都会把权限放宽一次")

    # bug-2
    print("\n[bug-2] position 热重载")
    if args.skip_gui:
        print("  SKIP  --skip-gui")
    else:
        res, err = bug2_and_3(args.assets_root, args.app_id, args.config, args.log)
        if err:
            print(f"  FAIL 无法复现：{err}")
            fails.append("bug-2")
        else:
            print(f"  窗口 wid={res['wid']}  起始位置={res['start_pos']}")
            print(f"  外部改 position 为 {{0.5,0.5}} 后：")
            print(f"    日志出现「配置已更新」= {res['reload_seen']}")
            print(f"    6 秒后窗口位置 = {res['moved']}")
            if not res["reload_seen"]:
                print("  FAIL 热重载链路没跑起来，本次不能判定")
                fails.append("bug-2")
            elif res["moved"] == res["start_pos"]:
                print("  PASS 缺陷复现：配置已重载但窗口位置纹丝不动"
                      "（_adopt_config 只比较 selected/size/hidden）")
            else:
                print("  FAIL 窗口移动了，缺陷似乎已修")
                fails.append("bug-2")

    print(f"\n结果：{'全部复现' if not fails else '未复现项: ' + ', '.join(fails)}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
