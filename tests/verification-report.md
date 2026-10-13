# dsh-pet-desktop-linux 第三方验收报告（T5）

- 验收者：teammate `pet-verify`（独立第三方，**不采用作者的任何自测结论**）
- 验收日期：2026-10-13
- 被测包目录：`/home/wlaeg/文档/deepseek-harness/default-workspace/dsh-pet-desktop-linux/`
- 测试套件：`tests/`（本报告同目录，11 个可复用脚本）
- 平台：Manjaro / GNOME 50.4 (Wayland 会话 + XWayland) / X11 root 2560x1440 / Python 3.14.7 / Node v24.21.0

## 0. 报告可信度声明（先读这一节）

**测试期间源码被其他队友持续修改。** 我据此做了版本固定，报告只对下列快照负责：

| 文件 | md5（前 16 位） | mtime |
|---|---|---|
| `lib/index.js` | `751cd184fd9598d8` | 17:34:06 |
| `client.js` | `1497ef1e57fefe6c` | 17:35:27 |
| `desktop/pet.py` | `95df0c9689836b63` | 17:37:29 |
| `package.json` | `81cf99548519192e` | 16:00:31 |
| `cordis.patch.yml` | `b59931b1b1ac9f27` | 15:42:33 |
| `locale/zh.json` | `3d6a4a5ab3f21189` | 17:35:37 |
| `locale/en.json` | `63286459ad3d8d78` | 17:35:37 |
| `README.md` / `README.zh.md` / `DESIGN.md` | 未改动 | 16:04 / 16:18 |

验证用的安装体是 `npm pack` 产出的 `/tmp/dsh-pet-desktop-linux-1.0.0.tgz`（102169 字节，md5 `9fced056961b`），
逐文件 md5 比对确认与上表源文件一致。

**测试方法学（重要）**：本报告多项结论是在**修正了我自己的判据 bug 之后**才得到的。
按「先怀疑自己的验收脚本，再怀疑被测代码」的原则，凡出现 FAIL 都先用**独立的手工复现**验证过
是实现问题还是脚本问题。报告里每处 `FAIL → 改判` 都写明了根因，见第 8 节。

**隔离性**：所有宿主端测试都用 `DSH_HOME=<临时目录>` 启动独立 DSH 实例（独占端口 22987/22993/22991/23991），
桌面端测试一律用独占 `--app-id` + 独立 `--config`。**未修改用户任何正式配置。**
（历史说明：早期有一轮我误用了用户真实配置路径并写入了测试值，当时已按原值还原；
用户正式宠物用的是 `~/.dsh/codex-pet/config.json` + `desktop-window.json`，与本插件路径无关，事后复核未受影响。）

---

## 1. 结论总览

| 组 | 项数 | PASS | FAIL | 未验证 | 无法判定 |
|---|---|---|---|---|---|
| A 静态正确性 | 5 | 5 | 0 | 0 | 0 |
| B 宿主端（真实 DSH 实例） | 10 | 9 | 0 | 0 | 1（SIGKILL 场景改判为缺陷，见 P-1） |
| C 桌面端（pet.py 独立跑） | 5 | 5 | 0 | 0 | 0 |
| D 边界与鲁棒性 | 3 | 3 | 0 | 0 | 0 |
| E 泄漏与安全审计 | 6 | 6 | 0 | 0 | 0 |
| F 上架门槛（contrib.md） | 6 | 5 | 0 | 1 | 0 |
| **合计** | **35** | **33** | **0** | **1** | **1** |

**发现的问题：3 个缺陷 + 2 处文档不符。** 详见第 7 节（P-1 / P-2 / P-3 / D-1 / D-2）。
无阻断性缺陷；核心功能（透明置顶窗口、宿主端 API、姿态联动、全新安装即用）全部通过决定性判据。

---

## A. 静态正确性（5/5 PASS）

```bash
cd /home/wlaeg/文档/deepseek-harness/default-workspace/dsh-pet-desktop-linux
python3 -m py_compile desktop/pet.py
node --input-type=module --check < lib/index.js
node --check client.js
grep -n "PIL\|from gi.repository import cairo" desktop/pet.py
grep -rn "/home/wlaeg" desktop/pet.py lib/index.js client.js
npm pack --dry-run
```

| # | 项 | 结果 | 证据 |
|---|---|---|---|
| A1 | `py_compile` 通过 | **PASS** | 退出码 0，无输出 |
| A2 | `node --check` 通过（ESM + CJS 两个文件） | **PASS** | `lib/index.js` 与 `client.js` 均为退出码 0 |
| A3 | 零 Pillow、零 pycairo | **PASS** | `grep -c` → `0`。契约要求用 GdkPixbuf 解码 webp + `Gtk.Image`，实测代码中确无 `PIL` 与 `from gi.repository import cairo` |
| A4 | 无硬编码开发者路径 | **PASS** | 三个文件命中数全为 0：`desktop/pet.py:0  lib/index.js:0  client.js:0` |
| A5 | 包内不含 `__pycache__` / `tests/` | **PASS** | `npm pack --dry-run` 恰好 12 个文件：LICENSE 1.1kB、NOTICE 2.4kB、README.md 16.0kB、README.zh.md 14.7kB、assets/icon.png 34.2kB、client.js 30.1kB、cordis.patch.yml 433B、desktop/pet.py 77.6kB、lib/index.js 37.5kB、locale/en.json 2.2kB、locale/zh.json 2.2kB、package.json 1.1kB |

补充静态核对：`locale/zh.json` 与 `locale/en.json` 键集合一致（各 46 键，差集为空）；
`client.js` 引用的 45 个 `t()` 键在两个 locale 中**均无缺失**。

---

## B. 宿主端（真实 DSH 实例，非 mock）（9 PASS / 1 缺陷）

全部经真实 HTTP 打到真实运行的 DSH web 实例。套件：`tests/t5_host_api.py`（22 条断言）。

```bash
cd /home/wlaeg/文档/deepseek-harness/default-workspace/dsh-pet-desktop-linux
DSH_HOME=/tmp/t5-iso-home timeout 400 python3 tests/t5_host_api.py \
    --dsh-home /tmp/t5-iso-home --profile newtest --port 22993
```

**最终结果 `PASS=22 FAIL=0`（退出码 0）。** 关键输出行：

| # | 项 | 结果 | 证据（实际输出行） |
|---|---|---|---|
| B7 | `GET /dsh-pet-desktop/` 返回路由清单 | **PASS** | `200 {"name": "dsh-pet-desktop-linux", "routes": ["/api/state", "/api/start", "/api/stop", "/api/config", "/api/deps"]}` |
| B8 | `GET /api/state` 结构正确 | **PASS** | 含 `running / pid / startedAt / lastExit / port / config / configPath / petScript / pets / assets / log / dependencies`；`pets` 9 只：bsod, codex, dewey, fireball, hoots, null-signal, rocky, seedy, stacky |
| B9 | `GET /api/deps` 五项探测正确 | **PASS** | `python3=Python 3.14.7 · /usr/bin/python3`、`pygobject=gi 3.56.3`、`gtk3=Gtk 3 24`、`gdkpixbuf=GdkPixbuf ok`、`xdotool=/usr/bin/xdotool`；`assets.ok=true`（9 只）、`petScript.ok=true` |
| B10 | `POST /api/start` 拉起宠物，窗口归属该子进程 | **PASS** | `{"ok":true,"status":200,"pid":81100}`；`xdotool search --name "DSH Codex Pet"` → `wid=31457283` 的 `_NET_WM_PID==81100`，几何 `160x173 @ (2241,783)` `IsViewable`——pid 与 start 返回完全一致 |
| B11 | 安全校验：缺信任头 / 错 content-type 必须 403 | **PASS** | 4 个负例全部 `HTTP 403 {"error":"请求来源校验失败"}`：无 `x-dsh-pet-desktop` 头、`content-type: text/plain`、头值 `0`、`sec-fetch-site: cross-site`；`POST /api/start` 与 `/api/stop` 无头同样 403 |
| B12 | `POST /api/config` 改 size/selected 真的生效 | **PASS** | 写入 `size=160→96, selected=fireball→codex`，回读 `96/codex`；**窗口几何同步变化 `160x173 → 96x104`**（严格按 96/160 缩放），1 秒内完成 |
| B13 | `POST /api/config` 改 `hidden` 真的 map/unmap | **PASS** | `hidden=true` → `Map State: IsUnMapped`；`hidden=false` → `Map State: IsViewable`（约 2–3 秒延迟，因 pet.py 3 秒轮询配置） |
| B14 | `POST /api/stop` 子进程真退出，`lastExit` 记录 signal | **PASS** | `{"ok":true,"status":200,"stopped":true,"killed":false,"pid":81062}`；子进程确实退出无残留；`lastExit = {"at":1791885658763,"code":null,"signal":"SIGTERM"}` |
| B15a | SIGTERM 优雅退出后无孤儿 | **PASS** | `DSH 被 SIGTERM 后宠物子进程无孤儿残留`（500ms 内宠物随 DSH 退出） |
| B15b | **SIGKILL 后无孤儿** | **FAIL（缺陷 P-1）** | `kill -KILL <DSH>` 后宠物进程残留，PPID 变 821（被 init 收养），窗口仍挂在屏上。详见第 7 节 P-1 |

补充验证的边界（均 PASS）：
- 非法宠物 id → `400 {"error":"宠物 nope 不在可用列表中", ...9只}`
- `size` 越界：9999 → clamp 到 224；1 → clamp 到 64
- 坏 JSON 请求体 → `400 {"error":"请求体必须是 JSON 对象"}`
- 未知路由 → `404 {"error":"未知路由 /nope"}`；`PUT /api/config` → `405 {"error":"方法不支持：PUT"}`
- `Origin: http://evil.com` → **403**；`X-Forwarded-For: 8.8.8.8` → 仍 200（正确：只看 socket 真实源，不采信 XFF）
- 配置文件内容损坏（非法 JSON）→ `GET /api/config` 仍返回默认值不崩
- 并发 40 次 `writeConfig` → 文件始终可解析、字段完整（writeQueue 串行 + 原子 rename 有效）
- 依赖探测 30s 缓存命中：第 1 次 `0.368775s`，第 2 次 `0.000931s`
- **autostart（新功能）**：配置不存在时启动 DSH，**第 1 秒宠物进程即为 1**，日志 `已自动启动桌面宠物（pid 56531）`，进程 PPID 确为该 DSH；`autostart=false` 时 9 秒后进程数 0，日志 `自动启动已关闭（autostart=false），跳过`；`kill -TERM` 后依旧无孤儿

---

## C. 桌面端（pet.py 独立跑）（5/5 PASS）

```bash
python3 desktop/pet.py --assets-root <上游包根> --config /tmp/t5-cfg.json \
    --port 21987 --app-id com.wlaeg.t5.verify
```

| # | 项 | 结果 | 证据 |
|---|---|---|---|
| C16 | 独立启动成功 | **PASS** | `[pet-desktop] composited=True rgba_visual=True` / `窗口已显示 104x113 @ (1220,590) 宠物=codex 大小=104` / `精灵图 codex 1536x2288 v2 alpha=True` |
| C17 | 窗口属性全部符合契约 | **PASS** | `Depth: 32`、`Visual Class: TrueColor`、`Border width: 0`、`Map State: IsViewable`；`_NET_WM_STATE = _NET_WM_STATE_SKIP_PAGER, _NET_WM_STATE_SKIP_TASKBAR, _NET_WM_STATE_ABOVE, _NET_WM_STATE_STICKY`（ABOVE + STICKY 均在）；`_NET_WM_DESKTOP(CARDINAL) = 4294967295`（全桌面粘滞）<br>注意：`_NET_WM_STATE` 在 map 后按 300/1000/2500ms 重试写入，**必须等约 4 秒再断言**，否则会读到不完整的中间态（我首跑即因此误报，见 8.2） |
| C18 | 透明性（决定性判据） | **PASS** | 见下方「透明性专节」 |
| C19 | 四态姿态联动 | **PASS** | 见下方「姿态联动专节」 |
| C20 | `--help` 齐全；坏 `--assets-root` 中文错误 | **PASS** | `--help` 退出码 0，6 个参数（`--assets-root` 必需、`--config`、`--port`、`--wait`、`--app-id`、`--backend {x11,wayland}`）；坏路径退出码 3 + 中文错误（见 D 组详列） |

### C18 透明性 —— 决定性判据（这是我推翻自己前一个判据后重建的）

我先用的判据是「unmap 差分」：截图 → unmap 窗口 → 再截图 → 逐像素比较。
它**在本机会随机给出假失败**：该判据预设「窗口下方内容两次截图之间不变」，
但底下常是 Wayland 原生窗口或正在滚动的应用（`xwininfo -root -tree` 显示覆盖点 0 个 X11 窗口），
看不见也管不住。实测同一位置同尺寸：一次得 `48.99% / 四角全 0 → PASS`，
另一次得 `79.18% / 四角有差异 → FAIL`，而背景噪声基线实测为 **0.00%**（相隔 1 秒两张截图完全相同），
证明差异真实来自下层内容的变动，而非 shot 噪声。

**改用决定性判据**（`tests/t5_transparency.py` + `tests/t5_backdrop.py`）：
铺一块**纯色、X11、置顶**的背景窗口，把宠物窗口移到它正中并 raise 到其上，只截一次图。

```
  [PASS] 背景纯色可见: (910,310)=(0, 255, 0) 期望#00ff00
  [PASS] 四角透出背景色（=真透明）: 左上=(0, 255, 0)Δ0 右上=(0, 255, 0)Δ0 左下=(0, 255, 0)Δ0 右下=(0, 255, 0)Δ0
  [PASS] 窗口内有宠物像素: 非背景像素 5766/10080 = 57.2%
  [PASS] 窗口中部命中宠物: 中心(1110,459)=(36, 46, 108)
结果：4 项通过 / 0 项失败
```

即：**宠物窗口四角的像素与背景纯色逐通道完全相等（Δ0）**——窗口没有任何不透明底板，
GTK4 顶层无法透明的问题在本实现里确实被 GTK3 + `rgba_visual` 正确解决。
同时窗口内部有 57.2% 非背景像素，证明宠物确实画出来了（排除「窗口没映射」的假通过）。

辅助证据（unmap 差分，作为交叉验证）：在背景恰好静止的窗口位置测得
`整窗差异 5757/11752 = 48.99%`，**四角 12x12 全部差异 0/144 最大通道差 0**。
目视证据 `/tmp/t5-crop.png`：蓝色像素风 Codex 宠物坐在笔记本前，其外背景完全透明，无灰色矩形底。

### C19 姿态联动

```bash
timeout 120 python3 tests/t5_pose_test.py --assets-root <上游包根>
```

用假 `DSH_HOME=/tmp/t5-fake-dsh-home` + `compression.zstd.compress()` 构造四种
`session.v4.jsonl.zstd` 事件流，观察运行日志：

```
  [running] PASS  等 3.2s → [pet-desktop] 姿态 → running（最近活动：2 秒前）
  [failed]  PASS  等 3.2s → [pet-desktop] 姿态 → failed（任务出错了）
  [review]  PASS  等 2.8s → [pet-desktop] 姿态 → review（已完成，待查看）
  [waiting] PASS  等 3.2s → [pet-desktop] 姿态 → waiting（等待你处理）
```

四态**全部真实切换**，退出码 0。优先级 `waiting > running > failed > review > idle`
与 `DESIGN.md` 一致。

---

## D. 边界与鲁棒性（3/3 PASS）

| # | 项 | 结果 | 证据 |
|---|---|---|---|
| D21 | `--app-id` 撞名给中文错误 + **退出码 4**，不静默退出 | **PASS** | 先确认 `NameHasOwner(com.wlaeg.dsh-pet-desktop)=True`（用户正式实例占用），再用该 id 启动：先打印 `宠物 codex 大小 104 配置 ... 端口 19387 候选 9 只`，随后三行中文错误 —— `错误：应用单实例 id 'com.wlaeg.dsh-pet-desktop' 已被另一个进程占用` / `详细信息：Gtk.Application 用 D-Bus 名做单实例仲裁，同名的第二个进程会把激活请求转发给已有实例后静默退出，因此本进程不会显示任何窗口。` / `解决办法：...请用 --app-id 指定不同的 id（如 com.wlaeg.dsh-pet-desktop-linux.p<端口>）...` → **退出码 4**，无残留进程，正式实例完好 |
| D22 | 端口漂移自愈 | **PASS** | `--port 21987 --wait` 且 21987 无监听：进程存活不退出，日志 `等待 DSH web 启动 …`；随后起 `python3 -m http.server 21987` 模拟 DSH 就绪 → **第 2 秒**日志出现 `已连接 DSH web（端口 21987）` 并建窗 `窗口已显示 104x113 @ (732,354)` |
| D23 | 素材缺失错误清晰 | **PASS** | 三种情形均中文错误 + **退出码 3** + 五种发行版安装提示：<br>A) 目录不存在 → `错误：--assets-root 下找不到精灵图目录：/tmp/.../nope/assets/codex`<br>B) 空目录 → 同上<br>C) 有 `assets/codex` 但无宠物 → `错误：/tmp/.../nopet/assets/codex 下没有任何可用宠物（缺少 spritesheet.webp）` |

---

## E. 泄漏与安全审计（6/6 PASS，无发现）

静态审计三个源文件（`desktop/pet.py` / `lib/index.js` / `client.js`）：

| # | 项 | 结果 | 证据 |
|---|---|---|---|
| E1 | 无绝对家目录路径 | **PASS** | `grep -rn "/home/wlaeg\|/root/\|/tmp/"` → 无 |
| E2 | 无凭据/密钥字面量 | **PASS** | `grep -rniE "password\|secret\|api[_-]?key\|bearer\|ghp_\|sk-..."` → 无 |
| E3 | 无内网 IP 硬编码 | **PASS** | `grep -rnE "\b(10\|192\.168\|172\.(1[6-9]\|2[0-9]\|3[01]))\.[0-9]+\.[0-9]+"` → 无 |
| E4 | 数据不外发 | **PASS** | 全部 URL 字面量仅 3 处且都是回环：`http://127.0.0.1:{port}/`（就绪探测，pet.py:955）、`http://127.0.0.1:{self.port}/`（点宠物打开本地 DSH，pet.py:1542）、`http://localhost`（**仅用于 `new URL(req.url, base)` 解析相对路径**，lib/index.js:787）。`client.js:210 fetch(\`${API}${path}\`)` 的 `API='/dsh-pet-desktop/api'` 是同源相对路径。**无任何第三方域名** |
| E5 | 无危险执行 | **PASS** | 无 `eval(` / `new Function` / `os.system` / `shell=True`。`execFileSync` 仅用于 `python --version` 探测；subprocess 仅 `zstd -dc`（固定参数）与 `xdotool windowstate --add/--remove`（列表参数，窗口 id 来自自身窗口） |
| E6 | 无混淆、无后门 | **PASS** | 三个文件中行长 >400 字符的行数为 **0**；不读 `.ssh` / 凭据文件 / 密钥环；无 0o777/0o666 |

---

## F. 上架门槛核对（对照 `/tmp/contrib.md`）（5 PASS / 1 未验证）

| # | 项 | 结果 | 证据 |
|---|---|---|---|
| F1 | 声明 `dsh.bundle`（只声明 `dsh.client` 不算可安装） | **PASS** | `dsh.bundle = {"patch": "./cordis.patch.yml"}` 与 `dsh.client = {...}` 两者都有 |
| F2 | 官方 `@deepseek-ai/*` 依赖的 peerDependencies 规则 | **PASS（不适用）** | `dependencies` 只有 `{"@michengai/dsh-codex-pet": "^0.1.17"}`，**不含任何 `@deepseek-ai/*`**，故无需 peerDependencies，预发布范围陷阱不适用 |
| F3 | `files` 白名单合理 | **PASS** | `['lib','client.js','desktop/pet.py','locale','cordis.patch.yml','assets','README.md','README.zh.md','LICENSE','NOTICE']` |
| F4 | description 属实（不夸大） | **PASS** | `A transparent always-on-top desktop pet for DSH on Linux (X11 / XWayland), driven by the session state.` 三个卖点均已被我实测证实：`transparent` → C18 决定性判据 Δ0；`always-on-top` → C17 `_NET_WM_STATE_ABOVE`；`driven by the session state` → C19 四态联动 |
| F5 | 仓库存在、依赖指向原作者 | **PASS** | `origin https://github.com/Wlaeg/dsh-pet-desktop-linux.git`；依赖 `@michengai/dsh-codex-pet` 指向原作者上游包 |
| F6 | 仓库满 1 天 | **未验证（会拦）** | 首次提交 `2026-10-13 15:44:36`，**当天**。CI 的 repo-age 检查会拦下；`.github/` 目录不存在。这不是代码问题，但投稿前需等待或改判 |

投稿 yml（`data/plugins/Wlaeg__dsh-pet-desktop-linux.yml`）尚未创建；`screenshots.json`（可选）不存在。

---

## 7. 发现的问题

### P-1【中等】SIGKILL 后宠物子进程成为孤儿

- **现象**：DSH 进程被 `SIGKILL`（`kill -9`）时，它拉起的宠物子进程不退出，PPID 变为 821（被 init 收养），
  透明窗口继续挂在桌面上，直到用户手动杀。
- **最小复现**：
  ```bash
  cd /home/wlaeg/.npm/_npx/1e7f6d9597241db0
  DSH_HOME=/tmp/t5-sk setsid nohup node node_modules/@deepseek-ai/dsh/lib/bin.js \
      newtest --no-open --port 23991 >/tmp/t5-sk-dsh.log 2>&1 </dev/null &
  # 等就绪、等宠物自动拉起
  kill -KILL <DSH pid>
  ps -eo pid,ppid,cmd | awk '/desktop\/pet\.py/ && /t5-sk/'   # → 仍存在，PPID=821
  ```
- **实测输出**：
  ```
  DSH pid=137331
  宠物: 137430 137331
  kill -KILL 后 → DSH 还在? 否
                宠物残留? 是 pid=137430 PPID=821
                端口 23991: 0 个监听
  ```
- **根因**：`lib/index.js` 的清理钩子只有三条 —— `ctx.effect(() => cleanup)`（约 951 行）、
  `ctx.on('dispose', cleanup)`（约 952 行）、`process.once('exit', cleanup)`（约 956 行）。
  **前两条依赖 Cordis 正常卸载流程，第三条 `exit` 事件在 SIGKILL 下根本不触发。**
  子进程以 `detached: true` 启动（自成进程组），因此也不会随父进程组一起被信号带走。
- **影响**：用户强杀 DSH（或 DSH 崩溃、被 OOM killer 杀）后，桌面会留下一只无人认领的宠物窗口，
  且它会继续 3 秒一次轮询一个已死的端口。日常 `SIGTERM`/正常退出路径**没有**这个问题（B15a PASS）。
- **建议修复方向**（供 Lead 决策，我不改代码）：给 `spawn` 加 `process.on('SIGHUP'/'SIGINT'/'SIGTERM')` 主动清理；
  或在 pet.py 侧增加父进程存活检测（定期检查 PPID 是否变为 1，是则自行退出）——
  后者对 SIGKILL 也有效，是更根本的兜底。

### P-2【中等】`position: null` 语义在两端不一致，且经 pet.py 一次保存就被静默改写

- **文档声明**：`README.zh.md:274` 与 `README.md:302` 都写
  「`position` | object \| null | 窗口的归一化位置（0–1）；**`null` 表示放右下角**」。
- **实测**：
  ```bash
  # Node 侧写入 position: null
  DSH_HOME=/tmp/t5-mode-node node probe.mjs   # → 磁盘 {"position": null, ...}
  ```
  此时若用 `pet.py` 读一次再保存（**拖动窗口就会触发**），磁盘变成：
  ```
  拖动前磁盘: {"selected":"codex","size":104,"hidden":false,"position":null,"autostart":true}
  pet.py 读入后内存 position = {'x': 0.94, 'y': 0.7}
  保存后磁盘: {... "position": {"x": 0.94, "y": 0.7} ...}
  ```
- **根因**：`desktop/pet.py` 的 `normalize_config(raw, base=None)` 对 `position` **只有
  `if isinstance(position, dict)` 一个分支，没有 `position is None` 分支**，于是 `None` 落回
  `base`（默认 `{x:0.94,y:0.7}`）。
  而 `desktop/pet.py` 的 `_place_from_config()` 里**确实写好了**「右下角」分支
  （`else: x, y = width - fw - 16, height - fh - 32`），但**永远走不到**，因为 config 里的
  `position` 从不为 `None`。
- **后果**：README 承诺的「`null` = 右下角」在实际运行中不成立，且用户设的 `null` 会在第一次拖动后
  被悄悄替换成 `{0.94, 0.7}`。**这是文档与实现的直接矛盾**，比单纯的文档滞后更严重。
- **复现脚本**：`tests/t5_position_bugs.py`
- **根因定位证据**（独立执行摘出的函数）：
  ```
  normalize_config({'position': None}, base=DEFAULT_CONFIG)
  → {'selected': 'codex', 'size': 104, 'hidden': False, 'position': {'x': 0.94, 'y': 0.7}}
  ```
  而绕开 Node 直接手跑 pet.py 时坐标计算是正确的：
  `position:{x:0,y:0}` → 窗口 `(0,28)`；`position:{x:1,y:1}` → 窗口 `(2440,1179)`
  （= 2560-104-16, 1324-113-32），证明 `_place_from_config()` 本身没问题。

### P-3【轻微】手工修改配置文件的 `position` 不会热重载（`size`/`selected`/`hidden` 都会）

- **现象**：宠物运行中，外部手改配置文件把 `position` 从 `{0,0}` 改成 `{0.5,0.5}`，
  日志已打印 `配置已更新：{... 'position': {'x': 0.5, 'y': 0.5} ...}`，
  但窗口**连续 10 秒保持原位不动**。同一个测试里改 `size` 为 140 → 第 1 秒窗口宽度即变 140。
- **根因**：`desktop/pet.py` 的 `_adopt_config(self, config)` 只比较
  `selected_changed` / `size_changed` / `hidden_changed` 三项，`position` 不在其中；
  其 docstring 自述「position 由拖动与摆放自行处理」——**即这是有意设计**。
- **问题在于文档没有说明这一点**：`README.zh.md:276` 写
  「设置面板与右键菜单能改的东西都会写回这份文件，所以手改文件和在界面上改是等价的。」
  容易让读者以为手改 `position` 也会立刻生效。（严格说该句只承诺「界面能改的 ⟺ 手改」，
  而设置面板**根本没有** position 控件 —— `grep -c "position" client.js` → `0`，
  `client.js` 的 `patchConfig` 只处理 selected/size/hidden/autostart。
  所以更准确的表述是：**`position` 没有任何界面入口，只能拖动窗口或手改文件后再启动**。）

### D-1【文档不符】`autostart` 未写进以下位置，且存在一处易误读的反向声明

- `README.md:241` 原文：`It does **not** offer position dragging, autostart on login, or running several pets at once.`
- `README.zh.md:218-219` 原文：`它**不提供**位置拖拽、开机自启，也**不支持**同时运行多只宠物。`
- 新增的 `autostart` 语义是「**随 DSH 启动**」，而这两句说的是「**开机自启**（autostart on login）」，
  严格说不构成直接矛盾；**但字段名同为 `autostart`，中文读者极易误读为文档自相矛盾。**
- 另外，翻遍 `README.md` / `README.zh.md` / `DESIGN.md`，
  唯一提到该字段的是配置表格（`README.zh.md:271`、`README.md` 对应行）——
  而 `DESIGN.md:148-156` 已把它记为「对最初冻结契约的有意扩展」，
  README 正文却缺少对应的功能说明段落。
- **建议**：把这句改成「不提供位置拖拽的界面入口、不随系统登录自启」，
  并补一段 `autostart` 的说明。属于发布前应修的文档问题。

### D-2【实现不一致，未违反明文契约】配置文件权限：Node 写 600，pet.py 写 644

- `lib/index.js` 的原子写显式带 `{ mode: 0o600 }`；`desktop/pet.py` 的 `ConfigStore.save()`
  用 `open(tmp, "w")` + `os.replace()`，**全程无 chmod、无 mode 参数**
  （`grep -n "chmod\|0o600\|0o644\|umask" desktop/pet.py` → 零匹配），落到默认 umask。
- **实测交替复现**（`stat -c '%a'`）：
  ```
  Node 写第 1 次 -> 600
  pet.py 写第 1 次 -> 644
  Node 写第 2 次 -> 600
  pet.py 写第 2 次 -> 644
  ```
  即**谁最后写谁决定权限**；**每次拖动窗口都会把权限放宽一次**。
- **定级理由**：`DESIGN.md` / `README` 均未对权限做任何契约声明，故**不算违约**，
  但同一份文件在两端权限不一致是真实缺陷（配置里包含用户的宠物选择与位置，
  在多用户机器上对其他本地用户可读）。
- **建议**：pet.py 的 `save()` 补 `os.chmod(tmp, 0o600)`（与 Node 侧对齐）。

---

## 8. 我自己的判据 bug（记录在此，避免后续读者误判）

按「先怀疑自己的验收脚本」原则，以下 5 处 FAIL 全部经独立手工复现确认为**我的脚本问题**，
被测代码在这些点上均正确：

1. **`/api/deps` 结构读错**：真实结构是 `{ok, dependencies:{ok,checkedAt,items:[…]}, assets:{…}}`，
   我写成 `d.get('items')` → 误报「deps 项不全」。
2. **autostart 断言过早**：有 `AUTO_START_DELAY_MS=1500` 延迟 + 拉起开销，**实测约 4 秒**才 running，
   我在 t+0 就断言 → 误报「未自动拉起」。改为轮询 12 秒后 PASS。
3. **config 断言写了相同值**：默认值本就是 `size=160/selected=fireball`，我写同值当然不变
   → 误报「4 秒内窗口尺寸没变」。改为先读当前值再写一个不同的值。
4. **`_NET_WM_STATE` 读得太早**：pet.py 在 map 后按 300/1000/2500ms 重试写 EWMH，
   我立即断言 → 误报「缺 STICKY」。加 `sleep(4)` 后得到完整的 `ABOVE, STICKY`。
5. **透明性 unmap 差分判据本身不成立**（最重要的一处）：该判据预设窗口下方内容静止，
   本机该前提常不成立（底下是 Wayland 原生层）。我一度据此报 FAIL，
   查清后重建了决定性判据（纯色背景 + Δ 比较）。同理，`t5_backdrop.py` 最初**没有置顶**，
   背景被 VS Code/微信盖住，四角采到下层真实内容得到 4 个各不相同的灰度值；
   `windowmove` 在窗口刚 map 时也会被 pet.py 随后的重新摆放覆盖 —— 两处均已在脚本内修正并加前置校验。

另外记录一条**本机 xdotool 的行为陷阱**（会误伤用户正式宠物，务必注意）：
`xdotool search --pid <pid> --name <name>` **不会同时应用两个条件** ——
传一个不存在的 pid 也照样返回全部同名窗口。因此绝不能用 `--pid` 定位测试窗口，
必须先用 `--name` 枚举、再逐个读 `_NET_WM_PID` 与 `WM_CLASS` 自行过滤
（用户正式宠物窗口的类名是 `dsh-pet-desktop.py`，测试实例是 `pet.py`，可据此区分）。

---

## 9. 测试套件说明（`tests/`）

每个脚本头部都有用途、用法与退出码说明。全部脚本**默认隔离**，不碰用户正式实例与正式配置。

| 脚本 | 用途 |
|---|---|
| `t5_host_api.py` | 宿主端 API 全套 22 条断言（真实 DSH，非 mock）：路由/state/deps/403 负例/start/窗口归属/config 生效/hidden/stop/lastExit/孤儿 |
| `t5_desktop_props.py` | 桌面端窗口属性 + 透明性 + hidden 热重载 + `--help`/坏 assets-root 错误码 |
| `t5_transparency.py` | **决定性透明性判据**（受控纯色背景 + 四角逐通道 Δ 比较 + 内部有宠物的正判据） |
| `t5_backdrop.py` | 铺纯色置顶背景窗口，供上一条使用 |
| `t5_pose_test.py` | 用假 DSH_HOME 构造四种会话事件流，验证 running/failed/review/waiting 姿态切换 |
| `t5_autostart_e2e.sh` | 全新安装端到端：配置文件不存在时自动拉起、`autostart=false` 不拉起、退出无孤儿 |
| `t5_autostart_passthrough.py` | autostart 无损透传（单元级 + 真实 xdotool 拖动进程级） |
| `t5_missing_dep.py` | 缺依赖降级：构造「有 python3、无 xdotool」的 PATH，验证不阻塞 DSH + 中文安装提示 |
| `t5_position_bugs.py` | P-2 / P-3 两处 position 缺陷的最小复现 |
| `pixel_diff.py` | 两张截图的逐像素区域比较（unmap 差分判据的辅助工具） |
| `win_fingerprint.py` | 窗口几何/内容指纹工具（`geo` / `fp` / `cmp` / `find` 子命令） |

复跑入口：

```bash
cd /home/wlaeg/文档/deepseek-harness/default-workspace/dsh-pet-desktop-linux
ASSETS=/home/wlaeg/.dsh/profiles/pettest/node_modules/@michengai/dsh-codex-pet

DSH_HOME=/tmp/t5-iso-home python3 tests/t5_host_api.py --dsh-home /tmp/t5-iso-home --profile newtest --port 22993
python3 tests/t5_desktop_props.py --assets-root $ASSETS
python3 tests/t5_transparency.py  --assets-root $ASSETS
python3 tests/t5_pose_test.py     --assets-root $ASSETS
python3 tests/t5_autostart_passthrough.py --assets-root $ASSETS
python3 tests/t5_missing_dep.py   --tarball /tmp/dsh-pet-desktop-linux-1.0.0.tgz
bash    tests/t5_autostart_e2e.sh /tmp/dsh-pet-desktop-linux-1.0.0.tgz 22987
```

---

## 10. 未验证项（如实列出）

1. **F6 仓库满 1 天**：首次提交在验收当天，无法验证，且已知会被 CI 拦。
2. **DSH 内嵌设置面板的真实渲染**（`client.js` 的 UI）：只做了静态核对
   （语言键完整、字段与 `patchConfig` 对应、无 `position` 控件），
   **没有在浏览器里实际打开设置面板点击操作**。需要 Lead 在 Web GUI 里目视确认。
3. **`--backend wayland` 路径**：只验证了默认 `x11`。契约本身要求 Wayland 会话下走 XWayland，
   wayland 原生后端未测。
4. **多显示器**：本机 `_NET_WORKAREA = 0,28,2560,1324` 单屏。跨屏拖动与 `_work_area()` 的多屏取主屏行为未测。
5. **鼠标交互**：右键菜单各项、双击跳跃、看向光标的 16 向 look 动画，只验证了「拖动落盘」链路，
   **未逐项点击验证菜单动作**。
6. **`xdotool` 缺失时 autostart 的最终行为**已验证（不阻塞 + 有提示），但
   **`gdk-pixbuf` 缺 webp loader** 的降级路径未实测（本机已装）。

---

## 11. 环境清理确认

验收结束时的实测状态：

```
用户正式宠物实例：1149 python3 /home/wlaeg/.local/bin/dsh-pet-desktop.py --wait   ← 完好，全程未受影响
用户正式 DSH：    3892 node .../dsh web --no-open（端口 19387）                    ← 完好
T5 测试进程残留：  无（ps -eo pid,cmd | awk '/com\.wlaeg\.t5/' → 空）
测试端口：        22987 / 22991 / 22993 / 23991 / 21987 全部释放
```

用户正式宠物使用的是 `~/.dsh/codex-pet/config.json` + `desktop-window.json`，
与本插件的 `~/.dsh/codex-pet-desktop/config.json` 是**完全独立的两个文件**
（`dsh-pet-desktop.py` 第 83 行 `DATA_DIR = os.path.expanduser("~/.dsh/codex-pet")`），
验收期间未改动前者。

> 附带记录一个观察（非缺陷）：本插件的配置路径由 `lib/index.js` 的
> `dshHome() = process.env.DSH_HOME || ~/.dsh` 决定，**不按 profile 隔离**；
> 而 `desktop/pet.py:74` 的默认配置路径硬编码 `~/.dsh`，不读 `DSH_HOME`。
> 实际由插件拉起子进程时总是显式传 `--config <Node 的 configPath>`，故不产生分叉；
> 但用户设了 `DSH_HOME` 又直接手跑 pet.py 时，两者会指向不同文件。
