#!/bin/bash
# t5_autostart_e2e.sh —— 「装完即用」端到端验收（全新安装路径 + autostart 开/关）
#
# 用途
# ----
# 验证本插件最核心的用户旅程：**从 tarball 全新装进一个干净 profile，启动 DSH，
# 不做任何手动点击，宠物窗口自己出现**（autostart=true 默认），以及反过来
# autostart=false 时确实不自动拉起。
#
# 这条用例专门用**隔离 DSH_HOME**，绝不碰用户的 ~/.dsh：
#   DSH_HOME=$ISO_HOME  → 配置落在 $ISO_HOME/codex-pet-desktop/config.json
#   端口默认 22987（避开 lead 的 21987 与正式实例的 19387）
#
# 前置
# ----
#   $TARBALL 必须是 `npm pack` 出来的包；脚本自己建 profile 并解包安装。
#
# 用法
# ----
#   tests/t5_autostart_e2e.sh [tarball] [port]
#   tests/t5_autostart_e2e.sh                        # 默认 /tmp/dsh-pet-desktop-linux-1.0.0.tgz 22987
#
# 退出码
# ----
#   0 = 两个用例都通过；1 = 有失败。结果逐条打印 PASS/FAIL 并写入 $OUT_LOG。
set -u

TARBALL="${1:-/tmp/dsh-pet-desktop-linux-1.0.0.tgz}"
PORT="${2:-22987}"
DSH_DIR=/home/wlaeg/.npm/_npx/1e7f6d9597241db0
ISO_HOME=/tmp/t5-autostart-home        # 每次重建，保证「全新安装」
PROFILE=fresh
OUT_LOG=/tmp/t5-autostart-e2e.log

# 只锚定到本脚本自己建的路径，绝不匹配用户的 ~/.local/bin/dsh-pet-desktop.py。
# 注意：awk 里用 [.] 而不是 \. —— awk 不认 \. 转义，会刷 "转义序列 \. 被当作单纯的 ." 警告。
PET_PATTERN="t5-autostart-home/profiles/$PROFILE/node_modules/dsh-pet-desktop-linux/desktop/pet[.]py"

pass=0; fail=0
say()  { printf '%s\n' "$*" | tee -a "$OUT_LOG"; }
ok()   { pass=$((pass+1)); say "  PASS  $*"; }
bad()  { fail=$((fail+1)); say "  FAIL  $*"; }
check(){ if [ "$2" = "$3" ]; then ok "$1 ($2)"; else bad "$1：期望 $3，实际 $2"; fi; }

# 按绝对路径取 pid，绝不写裸 'pet.py'（会自匹配执行它的 shell）
pet_pids() {
  ps -eo pid,cmd | awk -v p="$PET_PATTERN" '$0 ~ p && !/awk/ {print $1}'
}
dsh_pids() {
  # awk 不吃 \. 转义（会刷警告），写 [.] 才对
  ps -eo pid,cmd | awk -v p="bin[.]js --profile $PROFILE" '$0 ~ p && !/awk/ {print $1}'
}
# 按 pid 精确取窗口。
# 绝不能写 `xdotool search --pid <pid> --name "DSH Codex Pet"`：实测本机
# xdotool 4.20260303.1 同时给这两个条件时会**忽略 --pid**（传不存在的 pid 也
# 返回全部同名窗口），会误抓到用户正在运行的正式宠物并操纵它。
# 也不能用 `| tail -1`：那同样可能选中正式实例（它是真实存在的同名窗口）。
# 正确做法：先按名字枚举，再用 _NET_WM_PID 自己比对。
win_of_pid() {
  local want="$1" w pid
  for w in $(xdotool search --name "DSH Codex Pet" 2>/dev/null); do
    pid=$(xprop -id "$w" _NET_WM_PID 2>/dev/null | awk '{print $NF}')
    if [ "$pid" = "$want" ]; then printf '%s' "$w"; return 0; fi
  done
  return 1
}
kill_all() {
  for p in $(pet_pids) $(dsh_pids); do kill "$p" 2>/dev/null; done
  sleep 2
  for p in $(pet_pids) $(dsh_pids); do kill -9 "$p" 2>/dev/null; done
  sleep 1
}

# 启动隔离 DSH 并等它就绪。**不要写成 S=$(start_dsh ...)**：
# bash 命令替换要等所有继承了那条 stdout 管道的进程退出，而 setsid 出去的长命 node
# 会让替换永不返回（本轮实测 rc=124 挂死，白白浪费 200 秒超时）。
# 因此结果通过全局变量 DSH_WAIT_SEC 回传。
DSH_WAIT_SEC=""
start_dsh() {           # $1=日志文件
  local log="$1"
  rm -f "$log"
  ( cd "$DSH_DIR" && DSH_HOME="$ISO_HOME" setsid nohup node node_modules/@deepseek-ai/dsh/lib/bin.js \
      --profile "$PROFILE" --no-open --port "$PORT" >"$log" 2>&1 </dev/null & )
  DSH_WAIT_SEC=""
  for i in $(seq 1 40); do
    sleep 1
    grep -q 'token=' "$log" 2>/dev/null && { DSH_WAIT_SEC="$i"; return 0; }
  done
  return 1
}
stop_dsh() { for p in $(dsh_pids); do kill "$p" 2>/dev/null; done; sleep 2; }

: > "$OUT_LOG"
say "=== t5_autostart_e2e.sh  起始 $(date '+%F %T') ==="
say "tarball = $TARBALL ($(stat -c%s "$TARBALL" 2>/dev/null) 字节)"
say "port=$PORT  ISO_HOME=$ISO_HOME"
say ""

[ -f "$TARBALL" ] || { say "找不到 tarball：$TARBALL"; exit 1; }
kill_all
rm -rf "$ISO_HOME"

# ── 建一个真正干净的 profile：只用 tarball + 上游宠物包 ────────────────────────
say "--- 准备干净 profile ---"
mkdir -p "$ISO_HOME/profiles/$PROFILE/node_modules"
tar xzf "$TARBALL" -C "$ISO_HOME/profiles/$PROFILE/node_modules" --strip-components=0
# tarball 解出来是 package/ 前缀，展平
if [ -d "$ISO_HOME/profiles/$PROFILE/node_modules/package" ]; then
  rm -rf "$ISO_HOME/profiles/$PROFILE/node_modules/dsh-pet-desktop-linux"
  mv "$ISO_HOME/profiles/$PROFILE/node_modules/package" \
     "$ISO_HOME/profiles/$PROFILE/node_modules/dsh-pet-desktop-linux"
fi
# 上游宠物素材：本机已装在别的 profile 里，直接复制（离线可跑）
UPSTREAM=""
for c in /home/wlaeg/.dsh/profiles/pettest/node_modules/@michengai/dsh-codex-pet \
         /tmp/t5-iso-home/profiles/newtest/node_modules/@michengai/dsh-codex-pet; do
  [ -d "$c/assets/codex" ] && { UPSTREAM="$c"; break; }
done
if [ -z "$UPSTREAM" ]; then say "找不到上游宠物素材包，无法继续"; exit 1; fi
mkdir -p "$ISO_HOME/profiles/$PROFILE/node_modules/@michengai"
rm -rf "$ISO_HOME/profiles/$PROFILE/node_modules/@michengai/dsh-codex-pet"
cp -r "$UPSTREAM" "$ISO_HOME/profiles/$PROFILE/node_modules/@michengai/dsh-codex-pet"
say "上游素材来源 = $UPSTREAM"
say "安装的 pet.py md5 = $(md5sum "$ISO_HOME/profiles/$PROFILE/node_modules/dsh-pet-desktop-linux/desktop/pet.py" | cut -c1-12)"

cat > "$ISO_HOME/profiles/$PROFILE/package.json" <<JSON
{
  "name": "t5-autostart-$PROFILE",
  "private": true,
  "dsh": { "profile": { "bundles": ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app", "dsh-pet-desktop-linux"] } },
  "dependencies": { "dsh-pet-desktop-linux": "file:$TARBALL" }
}
JSON
say "配置文件存在? $([ -e "$ISO_HOME/codex-pet-desktop/config.json" ] && echo 是 || echo 否（全新安装，符合预期）)"
say ""

# ══ 用例 1：全新安装 + 默认 autostart=true → 不点任何按钮窗口自己出现 ══════════
say "=== 用例 1：全新安装默认自动启动 ==="
if start_dsh /tmp/t5-autostart-1.log; then ok "DSH 就绪耗时 ${DSH_WAIT_SEC}s"; else bad "DSH 未在 40s 内就绪"; fi
sleep 6                    # AUTO_START_DELAY_MS=1500，再留出 pet.py 起 GTK 的时间
N=$(pet_pids | wc -l)
check "宠物进程数（无人工点击）" "$N" "1"
if [ "$N" -ge 1 ]; then
  P=$(pet_pids | head -1)
  say "  宠物 pid=$P；命令行："
  ps -p "$P" -o args= | sed 's/^/      /' | tee -a "$OUT_LOG"
  W=$(win_of_pid "$P")
  if [ -n "$W" ]; then
    say "  窗口 wid=$W  $(xwininfo -id "$W" 2>/dev/null | awk '/Absolute upper-left X/{x=$NF}/Absolute upper-left Y/{y=$NF}/Width/{w=$NF}/Height/{h=$NF}/Map State/{m=$NF}END{print w"x"h" @ ("x","y") "m}')"
  else
    say "  窗口：（未找到属于 pid=$P 的窗口）"
  fi
  say "  日志中的自动启动行："
  curl -s "http://127.0.0.1:$PORT/dsh-pet-desktop/api/state" 2>/dev/null \
    | python3 -c "import json,sys;d=json.load(sys.stdin);print('\n'.join('      '+l for l in d.get('log',[]) if '自动' in l or '启动' in l))" 2>/dev/null \
    | tee -a "$OUT_LOG"
fi
stop_dsh
sleep 1
check "关闭 DSH 后宠物无孤儿残留" "$(pet_pids | wc -l)" "0"
say ""

# ══ 用例 2：autostart=false → 重启 DSH 后窗口不出现 ═══════════════════════════
say "=== 用例 2：autostart=false 不自动拉起 ==="
mkdir -p "$ISO_HOME/codex-pet-desktop"
cat > "$ISO_HOME/codex-pet-desktop/config.json" <<'JSON'
{
  "selected": "codex",
  "size": 104,
  "hidden": false,
  "position": { "x": 0.94, "y": 0.7 },
  "autostart": false
}
JSON
say "  预置配置 autostart=false（$(stat -c%s "$ISO_HOME/codex-pet-desktop/config.json") 字节）"
kill_all
if start_dsh /tmp/t5-autostart-2.log; then ok "DSH 就绪耗时 ${DSH_WAIT_SEC}s"; else bad "DSH 未在 40s 内就绪"; fi
sleep 7
check "宠物进程数（应为 0）" "$(pet_pids | wc -l)" "0"
say "  DSH 日志中的跳过提示："
grep -a "自动启动已关闭\|autostart" /tmp/t5-autostart-2.log 2>/dev/null | tail -3 | sed 's/^/      /' | tee -a "$OUT_LOG"
say "  API 日志中的跳过提示："
curl -s "http://127.0.0.1:$PORT/dsh-pet-desktop/api/state" 2>/dev/null \
  | python3 -c "import json,sys;d=json.load(sys.stdin);print('\n'.join('      '+l for l in d.get('log',[]) if 'autostart' in l or '自动' in l))" 2>/dev/null | tee -a "$OUT_LOG"
say "  会话窗口表中是否出现测试专属标题窗口："
xdotool search --name "DSH Codex Pet" 2>/dev/null | while read -r w; do
  pid=$(xprop -id "$w" _NET_WM_PID 2>/dev/null | awk '{print $NF}')
  cls=$(xprop -id "$w" WM_CLASS 2>/dev/null | cut -d= -f2)
  say "      wid=$w pid=$pid class=$cls"
done | tee -a "$OUT_LOG"
say ""

# ── 收尾 ──────────────────────────────────────────────────────────────────────
kill_all
say "=== 清理完成：宠物残留 $(pet_pids | wc -l) 个，DSH 残留 $(dsh_pids | wc -l) 个 ==="
say ""
say "结果：PASS=$pass  FAIL=$fail"
[ "$fail" -eq 0 ] || exit 1
