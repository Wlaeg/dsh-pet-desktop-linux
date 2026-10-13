#!/usr/bin/env python3
"""t5_missing_dep.py —— 缺依赖时 autostart 是否阻塞 / 是否降级可用。

用途
----
需求原文要求「装完即用」，但插件**不能**因为用户机器缺一个外部命令
（典型：xdotool）就让 DSH 起不来。本脚本验证三件事：

  1. 把 xdotool 从 PATH 里遮掉后，依赖探测如实报缺（不谎报 ok）。
  2. 此时 autostart 仍会尝试、失败只在环形日志留一行中文提示，**不抛出**。
  3. DSH 进程本身正常就绪（HTTP 路由可用、/api/state 可读），没有被拖住。

做法
----
在临时目录放一个**只屏蔽 xdotool** 的 PATH 前缀：不伪造任何别的命令，
其余依赖（python3 / gi / gtk3 / gdkpixbuf）照常从真实 PATH 解析。
用隔离 DSH_HOME + 独占端口，绝不碰用户 ~/.dsh。

用法
----
    tests/t5_missing_dep.py [--port 22991] [--tarball <tgz>]
退出码 0 = 全部符合预期。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

DSH_DIR = "/home/wlaeg/.npm/_npx/1e7f6d9597241db0"
BIN = os.path.join(DSH_DIR, "node_modules", "@deepseek-ai", "dsh", "lib", "bin.js")
ISO_HOME = "/tmp/t5-nodep-home"
PROFILE = "nodep"
SHIELD = "/tmp/t5-path-shield"          # 里面放「谁都不许用 xdotool」的桩

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


def get(port, path, timeout=5):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
        return json.load(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=22991)
    ap.add_argument("--tarball", default="/tmp/dsh-pet-desktop-linux-1.0.0.tgz")
    args = ap.parse_args()
    port = args.port

    print("=== t5_missing_dep.py ===")
    for p in subprocess.run(["ps", "-eo", "pid,cmd"], capture_output=True, text=True
                            ).stdout.splitlines():
        if f"--profile {PROFILE}" in p and "awk" not in p:
            subprocess.run(["kill", p.split()[0]], check=False)
    time.sleep(2)
    subprocess.run(["rm", "-rf", ISO_HOME], check=False)

    # ── 建 profile（直接摊开 tarball，免 npm 网络） ──────────────────────────
    nm = os.path.join(ISO_HOME, "profiles", PROFILE, "node_modules")
    os.makedirs(nm, exist_ok=True)
    subprocess.run(["tar", "xzf", args.tarball, "-C", nm], check=True)
    os.rename(os.path.join(nm, "package"), os.path.join(nm, "dsh-pet-desktop-linux"))
    up = "/home/wlaeg/.dsh/profiles/pettest/node_modules/@michengai/dsh-codex-pet"
    os.makedirs(os.path.join(nm, "@michengai"), exist_ok=True)
    shutil.copytree(up, os.path.join(nm, "@michengai", "dsh-codex-pet"))
    with open(os.path.join(ISO_HOME, "profiles", PROFILE, "package.json"), "w") as fh:
        json.dump({"name": "t5-nodep", "private": True,
                   "dsh": {"profile": {"bundles": ["@deepseek-ai/dsh-base",
                                                   "@deepseek-ai/dsh-web-app",
                                                   "dsh-pet-desktop-linux"]}},
                   "dependencies": {"dsh-pet-desktop-linux": f"file:{args.tarball}"}}, fh)

    # ── 造一个「只缺 xdotool」的精确 PATH ────────────────────────────────────
    # 第一版做法是从真实 PATH 里剔掉所有含 xdotool 的目录 —— 结果把 /usr/bin 一起
    # 剔了，python3 也没了，探测报「五项全缺」，证据不精确（虽然结论仍成立）。
    # 正确做法：新建一个**只放 python3 软链**的目录，PATH 只用它。
    #   python3 找得到 → python3 探测 ok
    #   gi / gtk3 / gdkpixbuf 由 `python3 -c` 子进程探测，不看 PATH → ok
    #   xdotool 无处可寻 → 唯独它缺
    shutil.rmtree(SHIELD, ignore_errors=True)
    os.makedirs(SHIELD)
    py = shutil.which("python3")
    if not py:
        bad("本机找不到 python3，无法构造缺依赖环境")
        return 1
    os.symlink(py, os.path.join(SHIELD, "python3"))
    no_xdo_path = SHIELD
    found = shutil.which("xdotool", path=no_xdo_path)
    print(f"  精确 PATH = {SHIELD}（只含 python3 -> {py}）")
    print(f"  其中找 python3 -> {shutil.which('python3', path=no_xdo_path)}")
    print(f"  其中找 xdotool -> {found}")
    if found is None:
        ok("已构造出「python3 有、xdotool 无」的精确 PATH")
    else:
        bad(f"没能屏蔽 xdotool，仍在 {found}")

    # ── 起 DSH（带裁剪 PATH），计时确认没被 autostart 拖住 ───────────────────
    log = "/tmp/t5-nodep.log"
    if os.path.exists(log):
        os.unlink(log)
    env = dict(os.environ, DSH_HOME=ISO_HOME, PATH=no_xdo_path)
    # PATH 只剩 SHIELD 后 `node` 不可解析，必须用绝对路径启动 DSH 自己
    node = shutil.which("node") or "/usr/bin/node"
    print(f"  用绝对路径启动 DSH：{node}")
    t0 = time.time()
    with open(log, "w") as lf:
        proc = subprocess.Popen([node, BIN, "--profile", PROFILE, "--no-open",
                                 "--port", str(port)],
                                cwd=DSH_DIR, env=env, stdout=lf, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
    ready = None
    for _ in range(40):
        time.sleep(1)
        if os.path.exists(log) and "token=" in open(log, errors="ignore").read():
            ready = time.time() - t0
            break
    if ready:
        ok(f"DSH 在缺 xdotool 的环境下仍就绪，耗时 {ready:.1f}s（未被 autostart 阻塞）")
    else:
        bad("DSH 未在 40s 内就绪 —— autostart 可能阻塞了启动")

    try:
        st = get(port, "/dsh-pet-desktop/api/state")
        ok("/api/state 可读，插件路由正常注册")
        deps = st.get("dependencies", {})
        xd = next((i for i in deps.get("items", []) if i["id"] == "xdotool"), None)
        if xd and xd["ok"] is False:
            ok(f"依赖探测如实报缺：xdotool ok=false detail={xd['detail']!r}")
        else:
            bad(f"依赖探测没报缺 xdotool：{xd}")
        if deps.get("ok") is False:
            ok("dependencies.ok=false（整体不可启动）")
        else:
            bad(f"dependencies.ok 应为 false，实际 {deps.get('ok')}")
        if xd and xd.get("hint"):
            ok(f"给出了安装提示：{xd['hint'].splitlines()[0]}")
        else:
            bad("缺少 xdotool 却没给安装提示")

        time.sleep(6)
        n = len([l for l in subprocess.run(["ps", "-eo", "pid,cmd"], capture_output=True,
                                           text=True).stdout.splitlines()
                 if f"t5-nodep-home/profiles/{PROFILE}" in l and "pet.py" in l
                 and "awk" not in l])
        if n == 0:
            ok("缺依赖时没有半死不活的宠物进程残留")
        else:
            bad(f"缺依赖却起了 {n} 个宠物进程")

        # ★ 必须重新读一次：maybeAutoStart 延迟 1.5s + probeDependencies 要跑子进程，
        #   6 秒前的快照里还没有这两行日志（第一版脚本就栽在这个时序上，误报 FAIL）。
        st = get(port, "/dsh-pet-desktop/api/state")
        logs = "\n".join(st.get("log", []))
        if "自动启动未成功" in logs or "缺少依赖" in logs:
            ok("环形日志里有可读的中文失败提示")
            for l in st.get("log", []):
                if "自动启动" in l or "缺少依赖" in l:
                    print(f"        {l}")
        else:
            bad("日志里没有自动启动失败的说明")
            print("        实际日志尾部：" + repr(st.get("log", [])[-4:]))
    finally:
        subprocess.run(["kill", str(proc.pid)], check=False)
        time.sleep(2)
        subprocess.run(["kill", "-9", str(proc.pid)], check=False)
        subprocess.run(["rm", "-rf", ISO_HOME], check=False)

    print()
    print(f"结果：PASS={pass_n}  FAIL={fail_n}")
    return 1 if fail_n else 0


if __name__ == "__main__":
    sys.exit(main())
