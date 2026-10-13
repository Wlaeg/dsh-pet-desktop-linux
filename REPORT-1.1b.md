# SPEC 1.1b 实施与验证报告

本轮在第一版未提交改动之上实施增补规格。没有提交 git，没有更新安装副本，没有启动、重启、请求或终止正式端口 19387 / app-id 含 p19387 的实例。

## 本轮修改文件

- `/home/wlaeg/文档/deepseek-harness/default-workspace/dsh-pet-desktop-linux/desktop/pet.py`
- `/home/wlaeg/文档/deepseek-harness/default-workspace/dsh-pet-desktop-linux/REPORT-1.1b.md`

`lib/index.js`、`client.js` 的第一版未提交改动保留，本轮没有再编辑它们。没有修改 `shop/`、`assets/`、`locale/`、`README*`、`package.json` 或既有 `tests/`；没有安装新依赖。

## 逐条规格映射

以下行号对应本轮交付源码。为缩短表格，`P` 指绝对路径 `/home/wlaeg/文档/deepseek-harness/default-workspace/dsh-pet-desktop-linux/desktop/pet.py`，`H` 指同目录 `lib/index.js`，`C` 指同目录 `client.js`。

| 规格条目 | 文件:行号 | 实施与结论 |
| --- | --- | --- |
| R1 默认逐条列出任务 | P:1875-1881 | 展开与收起均构建任务列表，删除折叠态的数量摘要。 |
| R1 中文状态、原文标题、时长 | P:1816-1826 | 同一横行三个字段，状态取 `POSE_LABELS`，标题直接取 `item["title"]`，为空才用 `(未命名会话)`，时长取 `human_age`。 |
| R1 不再固定字符数截断 | P:1790-1796、P:1820-1826 | 移除气泡内所有 `set_max_width_chars(42)`；不再用任何固定字符数截断，依据实际可用像素宽度省略标题。状态和时长设置为 `NONE`，不会被长标题挤成省略号。 |
| R1 自适应、上限 560px | P:1724、P:1755-1758、P:1889-1890、P:1903 | 在 GTK 的 preferred-width 尺寸协商与 resize 中限制上限，避免不可缩放窗口按超长文本的自然宽度膨胀。短标题保留完整内容。 |
| R1 最多 8 个、仅截掉时显示数量 | P:1723、P:1875-1881 | 前 8 个会话均列出，只有剩余数量大于零才显示 `…还有 N 个会话`。 |
| R2 展开只补 waiting 细节 | P:1828-1839 | 收起仍有标题、状态和时长；展开时额外显示最多两个问题及其全部选项，说明为空时只显示选项标签。 |
| R2 空白切换、记忆保留 | P:1734-1736、P:1760-1766 | 保留原切换逻辑与 `expanded`；上下各留 4px 空白区域，点击任务行不切换展开状态。 |
| R2 waiting 首次强制展开一次 | P:1861-1868 | 保留第一版 waiting 会话 id 提醒逻辑；同一 waiting 会话手动收起后，下一轮不会再次强制展开。 |
| R2 waiting 黄框 | P:1089-1092、P:1883-1887 | 保留 `pet-bubble-alert` CSS 与切换逻辑；真实 PetApp 测得边框为 2px、`rgb(255,204,102)`。 |
| R3 点击视觉反馈 | P:1103-1105、P:1768-1783、P:1806-1813 | 点击行加 `pet-bubble-clicked`，350ms 后移除；重复点击重新计时。EventBox 使用可见窗口以呈现行底色。截图像素变化验证通过。 |
| R3 主跳转 POST | P:1662-1679、P:1782 | 保留后台线程 POST，body 为 `{"sessionId": id}`，两个请求头和 3 秒超时保留；成功时不调用兜底。 |
| R3 D-Bus 兜底 | P:1680-1694 | POST 异常后用 `subprocess.run` 调规定的完整 gdbus 命令，超时 3 秒；stdout/stderr 丢弃，缺命令或超时也静默。 |
| R4 Host 写接口与单槽 | H:555、H:846-863 | 第一版实现未修改；实测请求头校验、POST `{ok:true}`、后写覆盖前写、GET 消费后清空。 |
| R4 client inject 与定位 | C:626-644、C:656 | 第一版实现未修改；实测声明 `uiWorkspace`、每秒取 focus 并调用 `openSession(id)`、异常后继续、卸载清理定时器。 |
| R5 全局跟随/leave | P:88、P:1271-1298、P:1497-1498、P:2169 | 第一版逻辑原样保留；真实 PetApp 轻量调用跟随函数产生 look，leave 返回 False 且不清空。 |
| R5 拖动/双击/右键 | P:1449、P:1461、P:1479、P:1500 | AST 比较 `_on_button_press`、`_on_button_release`、`_on_motion`、`_popup_menu` 与 git HEAD 完全相同。未做完整物理鼠标回归。 |
| R5 父进程退出顺序 | P:2131-2156 | AST 与 git HEAD 完全相同，仍是先 quit 后受保护的 print。 |
| R5 透明配方 | P:1034-1088、P:1738-1750 | 原宠物透明设置与气泡 RGBA visual、app-paintable、`pet-bubble` CSS 原样保留；没有新增 cairo 路径。 |
| R5 签名去重 | P:1846-1859 | 保留 pose/id/pose/age//10 与去重返回；补入标题和问题，使同一时间桶内这些内容的变动也能更新。相同签名下控件对象不重建，已测。 |

## 实际验证

测试辅助文件与证据全部放在独立目录 `/tmp/dsh-pet-1.1b-htFqza/`，未放入仓库既有 `tests/`。

所有 GTK 测试使用以下环境及独立 D-Bus：

```bash
DSH_HOME=/tmp/dsh-pet-1.1b-htFqza \
NO_AT_BRIDGE=1 GIO_USE_VFS=local PYTHONWARNINGS=ignore::DeprecationWarning \
dbus-run-session -- python3 /tmp/dsh-pet-1.1b-htFqza/check_bubble.py
```

测试模块明确解析 `--config /tmp/dsh-pet-1.1b-htFqza/config.json`、`--app-id com.wlaeg.dsh-pet-test.b11`、`--port 22994`；完整 PetApp 使用独立的 `/tmp/dsh-pet-1.1b-htFqza/app-config.json`。Host 验证只绑定随机回环端口且配置 `autostart=false`，不会自动拉起宠物。D-Bus 兜底只访问私有总线中测试注册的 `dsh.web`，不会激活正式桌面壳。

| 命令 | 实际结果 |
| --- | --- |
| `python3 -m py_compile desktop/pet.py` | 退出码 0。 |
| `node --input-type=module --check < lib/index.js` | 退出码 0。 |
| `node --input-type=module --check < client.js` | 退出码 0。 |
| `git diff --check` | 退出码 0，无空白错误。 |
| 上述环境运行 `check_bubble.py` | 6 项测试通过：默认三个任务、超限提示、超长标题/时长、waiting 细节与手动收起、点击反馈消退、idle 隐藏与签名去重。默认样例实测 442×77px；超长标题实测不超过 560px，只有标题省略，时长完整。点击反馈产生 38376 个变化字节。 |
| 同环境、`dbus-run-session -- node /tmp/dsh-pet-1.1b-htFqza/check_focus.mjs` | 退出码 0；真实导入 Host 模块注册 HTTP handler，任务行 GTK 事件发出 POST，GET 取走 id 后为空；死端口触发真实 gdbus，并收到私有 `Activate({})`。模拟 gdbus 缺失、超时均静默结束。真实 client.js 在 Node VM 中每秒消费 focus 并调用测试 `uiWorkspace.openSession`；抛异常后下一项仍能打开，卸载清理定时器。 |
| 同环境运行 `check_app.py` | 退出码 0；用标准库 zstd 压缩 session.v4.jsonl.zstd，真实 watcher 读三个 running/failed/review 会话，真实 PetApp 未展开时显示三个标题、状态和时长，427×77px；加入 waiting 文件后自动展开，显示问题与选项，黄框 CSS 测量通过。全局跟随与 leave 轻量检查通过。 |
| Python AST 比较当前源码与 `git show HEAD:desktop/pet.py` | 拖动、释放、motion、右键菜单、父进程监视这五个方法完全一致。 |

已人工查看气泡截图。证据路径：

- `/tmp/dsh-pet-1.1b-htFqza/collapsed.png`
- `/tmp/dsh-pet-1.1b-htFqza/waiting.png`
- `/tmp/dsh-pet-1.1b-htFqza/clicked.png`
- `/tmp/dsh-pet-1.1b-htFqza/app-waiting.png`
- `/tmp/dsh-pet-1.1b-htFqza/app-validation.log`
- `/tmp/dsh-pet-1.1b-htFqza/focus-validation.log`
- `/tmp/dsh-pet-1.1b-htFqza/focus-http-log.json`

## 失败记录与验证边界

1. 修改前回归测试复现默认只有数量摘要、没有 waiting 选项、没有点击反馈。最初宽度实现仍被 GTK 的自然宽度撑大，测试测到 2560px；随后改为在 preferred-width 协商中限制上限，最终超长标题测试通过。另一轮测试暴露字体改变后时长被挤压，改为状态、时长不省略且仅标题可压缩，最终通过。
2. 物理点击自动化没有通过：`xdotool mousemove --window <测试XID> ... click 1` 执行成功，但 Gdk 读到的指针没有到达目标坐标；`xdotool click --window <测试XID> 1` 也未触发气泡事件。处于 GNOME/XWayland 环境，但没有进一步认定具体根因或修改系统。最终测试覆盖 GTK 的实际信号连接、行反馈像素和 HTTP 副作用，**不等于真实物理鼠标端到端验收**。空白区的物理点击、拖动/双击/右键的物理操作仍需真实桌面验收。
3. 第一次 waiting zstd 假数据把 `callId` 与 session id 写成相同字符串，触发既有解析器的字节计数规则，误判已处理。已改成独立 callId 并重跑通过；未修改生产解析器。
4. 独立总线初次运行触发 accessibility/GVfs、portal/RealtimeKit 等环境告警；后续轻量 GTK 测试关闭 accessibility 并使用本地 VFS。完整 PetApp 仍有私有 portal 的服务告警，测试断言及退出码通过；未修改系统服务配置。截图主要验证内容与行反馈，waiting 黄框通过 CSS 计算值验证，未做外层窗口边框截图像素断言。
5. 没有连接正式浏览器/DSH 桌面壳去观察页面切换；client 验证使用实际 client.js 加测试 workspace 服务。真实 DSH 页面 reveal 和正式窗口置前效果未重新验收，D-Bus 命令按增补规格提供的已验证事实实现。
6. 不引入依赖；本仓库没有 node_modules，完整 PetApp 只读已有上游图集。未运行 Qodo 外部审查，PATH 与 `/home/wlaeg/.qodo/bin/qodo` 都没有可执行文件；不安装或切换其他模型。

## 验收结论

增补验收 1、2、4、5 的自动化检查通过；验收 3 的 GTK 任务行事件、真实 POST、失败时真实 gdbus 与 client 消费通过，物理鼠标和正式页面切换的边界如上。本轮全部测试脚本、临时 HTTP 服务与私有 D-Bus 测试进程运行后已退出，未保留测试宠物进程；临时证据文件保留用于复核。

交付前另做了一次只读代码复核，使用同一允许模型，未发现可证实且可归因于本轮新增的缺陷。复核者没有重跑运行测试，其审查不能扩大上述验证覆盖。测试会话均已结束，进程检查未发现本轮 `check_app.py`、`check_bubble.py`、`check_focus.py`、`check_focus.mjs` 残留，已关闭复核 agent。
