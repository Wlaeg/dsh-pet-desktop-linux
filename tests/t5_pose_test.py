#!/usr/bin/env python3
"""t5_pose_test.py —— 实测 pet.py 的四态姿态联动（running/failed/review/waiting）。

用途
----
pet.py 是独立进程，拿不到 DSH 客户端运行时里的会话状态，只能读
`<DSH_HOME>/sessions/<workspace>/<id>/session.v4.jsonl.zstd`。本脚本用**假的
DSH_HOME** 造出四种事件流，观察 pet.py 日志里是否真的打出
`[pet-desktop] 姿态 → <pose>`，从而证明姿态联动不是纸面功能。

为什么用假 DSH_HOME：绝不能往用户真实会话目录里写垃圾事件；本脚本把
DSH_HOME 指向一个 /tmp 下的空目录，pet.py 读不到任何真实会话。

用法
----
  python3 t5_pose_test.py --assets-root <上游包根> \
      [--pet-py <pet.py 路径>] [--app-id com.wlaeg.t5.pose] [--keep]

退出码：0 = 四态全部按预期切换；非 0 = 有姿态未出现（脚本会打印实际日志）。
"""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time

try:
    from compression import zstd  # Python 3.14+
except ImportError:  # 老解释器
    zstd = None

DEFAULT_PET = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "desktop", "pet.py"
)

WORKSPACE = "t5ws"
SESSION_ID = "s1"


def write_session(home, lines, touch=True):
    """把事件行写成 session.v4.jsonl.zstd（整帧压缩）。"""
    d = os.path.join(home, "sessions", WORKSPACE, SESSION_ID)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "session.v4.jsonl.zstd")
    blob = ("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n").encode()
    raw = zstd.compress(blob)
    with open(path, "wb") as fh:
        fh.write(raw)
    if touch:
        os.utime(path, None)
    return path


def meta():
    return {"type": "session", "version": 4, "id": SESSION_ID, "cwd": "/tmp", "origin": "dsh"}


def title(text="T5 pose test"):
    return {"type": "session/title", "data": {"title": text}}


def turn_start(t=None):
    return {"type": "turn/start", "time": t or int(time.time() * 1000)}


def turn_end(kind, t=None):
    return {"type": "turn/end", "data": {"reason": {"kind": kind}}, "time": t or int(time.time() * 1000)}


def ask_call(call_id="call_t5_1"):
    args = json.dumps(
        {"questions": [{"header": "T5", "question": "继续吗？", "options": [{"label": "是"}]}]},
        ensure_ascii=False,
    )
    return {"type": "tool/call", "data": {"name": "ask_user_question", "callId": call_id, "arguments": args}}


CASES = [
    ("running", [meta(), title(), turn_start()]),
    ("failed", [meta(), title(), turn_start(), turn_end("error")]),
    ("review", [meta(), title(), turn_start(), turn_end("completed")]),
    ("waiting", [meta(), title(), turn_start(), ask_call()]),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets-root", required=True)
    ap.add_argument("--pet-py", default=DEFAULT_PET)
    ap.add_argument("--app-id", default="com.wlaeg.t5.pose")
    ap.add_argument("--home", default="/tmp/t5-fake-dsh-home")
    ap.add_argument("--wait", type=float, default=9.0, help="每种姿态最多等多少秒")
    ap.add_argument("--keep", action="store_true", help="结束后不删临时文件")
    args = ap.parse_args()

    if zstd is None:
        print("FAIL 本机 Python 没有 compression.zstd，无法造事件（脚本自身限制）")
        return 2

    home = os.path.abspath(args.home)
    shutil.rmtree(home, ignore_errors=True)
    os.makedirs(os.path.join(home, "sessions"), exist_ok=True)
    cfg = os.path.join(home, "cfg.json")
    with open(cfg, "w") as fh:
        json.dump({"selected": "codex", "size": 96, "hidden": False,
                   "position": {"x": 0.5, "y": 0.5}}, fh)

    log_path = os.path.join(home, "pet.log")
    env = dict(os.environ, DSH_HOME=home)
    with open(log_path, "wb") as logfh:
        proc = subprocess.Popen(
            [sys.executable, args.pet_py, "--assets-root", args.assets_root,
             "--config", cfg, "--port", "21987", "--app-id", args.app_id],
            stdout=logfh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            env=env, start_new_session=True,
        )
    print(f"# 已启动 pet.py pid={proc.pid}（DSH_HOME={home}，独占 app-id={args.app_id}）")

    results = {}
    try:
        # 等窗口起来
        deadline = time.time() + 20
        while time.time() < deadline:
            if os.path.exists(log_path) and "窗口已显示" in open(log_path, encoding="utf-8", errors="replace").read():
                break
            if proc.poll() is not None:
                print("FAIL pet.py 提前退出：")
                print(open(log_path, encoding="utf-8", errors="replace").read())
                return 1
            time.sleep(0.4)
        else:
            print("FAIL 20 秒内窗口没起来")
            print(open(log_path, encoding="utf-8", errors="replace").read())
            return 1

        for pose, lines in CASES:
            # 每种姿态先写一份"过期"的空会话（让上一态自然消失），再写本态
            before = open(log_path, encoding="utf-8", errors="replace").read()
            write_session(home, lines)
            t0 = time.time()
            seen = False
            while time.time() - t0 < args.wait:
                time.sleep(0.4)
                now = open(log_path, encoding="utf-8", errors="replace").read()
                fresh = now[len(before):]
                if f"姿态 → {pose}" in fresh:
                    line = [x for x in fresh.splitlines() if f"姿态 → {pose}" in x][-1]
                    print(f"  [{pose}] PASS  等 {time.time()-t0:.1f}s → {line}")
                    seen = True
                    break
            if not seen:
                now = open(log_path, encoding="utf-8", errors="replace").read()
                extra = "\n".join(now.splitlines()[-8:])
                print(f"  [{pose}] FAIL  {args.wait}s 内没有 '姿态 → {pose}'；日志尾部：\n{extra}")
            results[pose] = seen
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            proc.terminate()
        try:
            proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        print(f"# pet.py 退出码={proc.returncode}")
        if not args.keep:
            shutil.rmtree(home, ignore_errors=True)

    print("=== 结果 ===")
    for pose, ok in results.items():
        print(f"  {pose:8s} {'PASS' if ok else 'FAIL'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
