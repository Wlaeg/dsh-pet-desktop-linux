#!/usr/bin/env python3
"""t5_autostart_passthrough.py —— autostart 字段「无损透传」回归测试。

用途
----
守护一个真实的数据一致性 bug（Lead 已修，本脚本做回归）：

  pet.py 的 normalize_config() 是**白名单**收敛——只认识的字段留下，其余丢弃。
  而拖动窗口结束时会触发 _persist_position()，它调用 ConfigStore.save()，
  save() 又是「读全量 → 合并 patch → 写回全量」。
  于是只要 autostart 不在白名单里，用户设的 autostart=false 就会被
  **一次纯位置保存悄悄抹回 true**，界面上的开关下次启动就失效。

本脚本走**两条独立路径**验证，任一条挂掉都算 FAIL：
  路径 A（单元级）：直接 exec pet.py 的 normalize_config / ConfigStore，
                    save({"position":...}) 后检查盘上 autostart 是否还在。
  路径 B（进程级）：真起一个 pet.py，用 xdotool 模拟一次真实拖动
                   （按住左键 → 移动 → 松开，位移 >4px 才触发持久化），
                   然后读盘上的 JSON。这条才是端到端证据。

用法
----
    tests/t5_autostart_passthrough.py --assets-root <上游宠物包>
    tests/t5_autostart_passthrough.py --assets-root <...> --skip-drag   # 只跑路径 A

退出码 0 = 两条路径都通过；1 = 有失败。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
PET_PY = os.path.join(PKG, "desktop", "pet.py")

APP_ID = "com.wlaeg.t5.autostart-pt"          # 独占 app-id，绝不与正式实例撞名
CFG = "/tmp/t5-autostart-pt.json"
LOG = "/tmp/t5-autostart-pt.log"

pass_n = 0
fail_n = 0


def ok(msg):
    global pass_n
    pass_n += 1
    print(f"  PASS  {msg}")


def bad(msg):
    global fail_n
    fail_n += 1
    print(f"  FAIL  {msg}")


# ── 路径 A：直接从源码里抠出 normalize_config / ConfigStore 跑 ────────────────
def load_pet_module():
    """exec pet.py 的常量 + normalize_config + ConfigStore 段，不触发 GUI。"""
    src = open(PET_PY, encoding="utf-8").read()
    cs = src.index("class ConfigStore")
    ce = src.index("\nclass ", cs + 10)           # ConfigStore 之后的第一个 class
    nc = src.index("def normalize_config(")
    consts = re.search(r"SIZE_MIN, SIZE_MAX, SIZE_DEFAULT = .*\n", src).group(0)
    dflt = re.search(r"DEFAULT_CONFIG\s*=\s*\{.*?\n\}", src, re.S).group(0)
    ns = {"os": os, "json": json, "sys": sys}
    exec(consts + "\n" + dflt + "\n" + src[nc:cs] + src[cs:ce], ns)
    return ns


def path_a():
    print("--- 路径 A：单元级（normalize_config / ConfigStore）---")
    ns = load_pet_module()
    nc, Store, D = ns["normalize_config"], ns["ConfigStore"], ns["DEFAULT_CONFIG"]

    ck = "/tmp/t5-autostart-pt-unit.json"
    if os.path.exists(ck):
        os.unlink(ck)
    with open(ck, "w", encoding="utf-8") as fh:
        json.dump({"selected": "codex", "size": 104, "hidden": False,
                   "position": {"x": 0.0, "y": 0.0}, "autostart": False}, fh)

    st = Store(ck)
    if st.data.get("autostart") is False:
        ok("初始读入 autostart=false 被保留（未被白名单吞掉）")
    else:
        bad(f"初始读入 autostart 丢失，实际 {st.data.get('autostart')!r}")

    # 关键动作：模拟拖动结束时的位置持久化（只 patch position）
    st.save({"position": {"x": 0.5, "y": 0.5}})
    on_disk = json.load(open(ck, encoding="utf-8"))
    if on_disk.get("autostart") is False:
        ok("save({'position'}) 后盘上 autostart 仍为 false（未被抹掉）")
    else:
        bad(f"save({'position'}) 把 autostart 改成了 {on_disk.get('autostart')!r} —— 数据被抹")

    if on_disk.get("position") == {"x": 0.5, "y": 0.5}:
        ok("同一次 save 里 position 正常写盘 {x:0.5,y:0.5}")
    else:
        bad(f"position 没写对：{on_disk.get('position')}")

    # 老配置（完全没有该字段）应得默认 true，而不是 None
    legacy = {"selected": "codex", "size": 104, "hidden": False,
              "position": {"x": 1.0, "y": 1.0}}
    r = nc(legacy, base=D)
    if r.get("autostart") is True:
        ok("老配置（无 autostart 键）回填默认 true")
    else:
        bad(f"老配置回填错误：autostart={r.get('autostart')!r}")

    # 非布尔值不该被当成合法输入透传
    for weird in ("no", 0, 1, None, [], {}):
        r = nc({"autostart": weird}, base=D)
        if isinstance(r.get("autostart"), bool):
            ok(f"非布尔输入 {weird!r:>6} 被规范化成布尔 {r['autostart']}")
        else:
            bad(f"非布尔输入 {weird!r} 泄漏进了配置：{r.get('autostart')!r}")
    os.path.exists(ck) and os.unlink(ck)


# ── 路径 B：真起进程 + xdotool 真拖动 ────────────────────────────────────────
def win_geometry(wid):
    out = subprocess.run(["xwininfo", "-id", str(wid)], capture_output=True, text=True).stdout
    g = {}
    for key, pat in (("x", r"Absolute upper-left X:\s+(-?\d+)"),
                     ("y", r"Absolute upper-left Y:\s+(-?\d+)"),
                     ("w", r"Width:\s+(\d+)"),
                     ("h", r"Height:\s+(\d+)")):
        m = re.search(pat, out)
        g[key] = int(m.group(1)) if m else None
    return g


def path_b(assets_root):
    print("--- 路径 B：进程级（真实 xdotool 拖动触发 _persist_position）---")
    if os.path.exists(CFG):
        os.unlink(CFG)
    with open(CFG, "w", encoding="utf-8") as fh:
        json.dump({"selected": "codex", "size": 104, "hidden": False,
                   "position": {"x": 0.2, "y": 0.3}, "autostart": False}, fh)

    proc = subprocess.Popen(
        [sys.executable, PET_PY, "--assets-root", assets_root, "--config", CFG,
         "--port", "21987", "--app-id", APP_ID],
        stdout=open(LOG, "w"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        start_new_session=True)
    try:
        wid = None
        for _ in range(40):
            time.sleep(0.5)
            r = subprocess.run(["xdotool", "search", "--name", "DSH Codex Pet"],
                               capture_output=True, text=True)
            for w in reversed(r.stdout.split()):
                pid = subprocess.run(["xprop", "-id", w, "_NET_WM_PID"],
                                     capture_output=True, text=True).stdout.strip().split()[-1]
                if pid == str(proc.pid):
                    wid = w
                    break
            if wid:
                break
        if not wid:
            bad("未找到本次测试实例的窗口，无法做拖动")
            return
        ok(f"测试实例窗口 wid={wid} pid={proc.pid}")

        # 窗口刚 map 时可能还没准备好接收合成指针事件：实测首次跑会出现
        # 「mousedown+mousemove 全都到不了窗口」的间歇失败，隔几秒重跑就好了。
        # 因此先等窗口稳定，再带重试地拖，最多试 3 轮。
        time.sleep(1.5)
        cx0, cy0 = 0, 0
        before = None
        for attempt in range(1, 4):
            g = win_geometry(wid)
            before = (g["x"], g["y"])
            cx, cy = g["x"] + g["w"] // 2, g["y"] + g["h"] // 2
            # 位移必须 >4px，否则 _on_button_release 当成点击而不落盘
            subprocess.run(["xdotool", "mousemove", str(cx), str(cy)], check=False)
            time.sleep(0.3)
            subprocess.run(["xdotool", "mousedown", "1"], check=False)
            time.sleep(0.2)
            for step in range(1, 6):                   # 分步移动，模拟真实拖动
                subprocess.run(["xdotool", "mousemove",
                                str(cx - 12 * step), str(cy - 6 * step)], check=False)
                time.sleep(0.1)
            time.sleep(0.2)
            subprocess.run(["xdotool", "mouseup", "1"], check=False)
            time.sleep(1.5)                            # 等 save() 落盘
            g2 = win_geometry(wid)
            if (g2["x"], g2["y"]) != before:
                break
            if attempt < 3:
                print(f"        第 {attempt} 轮拖动未生效（窗口可能尚未就绪），重试…")
                time.sleep(2.0)

        g2 = win_geometry(wid)
        after = (g2["x"], g2["y"])
        print(f"        拖动前位置 {before}  尺寸 {g['w']}x{g['h']}")
        print(f"        拖动后位置 {after}")
        if after != before:
            ok(f"窗口确实被拖动了 {before} -> {after}（说明走了 _persist_position 分支）")
        else:
            bad("窗口位置没变，拖动未生效（后面的持久化断言不算数）")

        disk = json.load(open(CFG, encoding="utf-8"))
        if disk.get("autostart") is False:
            ok("真实拖动落盘后 autostart 仍为 false ★核心回归点★")
        else:
            bad(f"真实拖动后 autostart 变成 {disk.get('autostart')!r} —— 用户设置被抹掉")

        if disk.get("position") != {"x": 0.2, "y": 0.3}:
            ok(f"position 已按拖动更新并写盘：{disk.get('position')}")
        else:
            bad("position 没有被写回（拖动持久化没生效）")

        print("        盘上完整配置：")
        print("          " + json.dumps(disk, ensure_ascii=False))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        time.sleep(0.5)


def main():
    ap = argparse.ArgumentParser(description="autostart 字段无损透传回归测试")
    ap.add_argument("--assets-root", required=True, help="上游宠物包根目录")
    ap.add_argument("--skip-drag", action="store_true", help="只跑单元级路径 A")
    args = ap.parse_args()

    print("=== t5_autostart_passthrough.py ===")
    print(f"pet.py = {PET_PY}")
    path_a()
    if not args.skip_drag:
        path_b(args.assets_root)
    print()
    print(f"结果：PASS={pass_n}  FAIL={fail_n}")
    return 1 if fail_n else 0


if __name__ == "__main__":
    sys.exit(main())
