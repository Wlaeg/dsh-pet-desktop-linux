# SPEC 1.1 增补 —— 气泡必须列出任务名与状态，点击即跳转

用户在看过第一版气泡后提出明确要求（原话）：

> 一定要做到详细写出任务名字和状态，点击直接跳转页面

第一版的做法**不满足**这个要求，要改。下面是新的硬性要求。

## R1. 默认就要显示任务名与状态（最重要）

第一版折叠态长这样：`〔工作中〕 3`（多会话时只剩一个数字），用户看不到是哪个任务在跑。
**这是要修掉的核心问题。**

新要求：

- **气泡默认（未展开）就逐条列出每个正在工作的会话**，每条**必须同时包含**：
  - 姿态（中文，取 `POSE_LABELS`：工作中 / 等待你处理 / 任务出错了 / 已完成，待查看）
  - **会话标题原文**（`watcher.sessions[i]["title"]`；为空才用「(未命名会话)」）
  - 时长（`human_age(age)`）
- 一行一个任务，形如：`● 工作中 · 给桌面宠物加目光跟随与气泡 · 3 分钟前`
- **会话标题不要用 `set_max_width_chars(42)` 这种固定宽度硬截断** —— 用户要的是"详细写出"。
  改为：`label.set_max_width_chars(80)` 或更宽，并让气泡宽度自适应内容
  （宽度上限放到 560px；只有真的超宽才 `set_ellipsize(Pango.EllipsizeMode.END)`）。
- **`MAX_SESSIONS` 从 5 提到 8**：常态下要让用户一眼看全，而不是靠点数。
- 数量显示规则：**只有超出上限被截掉时才追加一行** `…还有 N 个会话`。

## R2. 展开 / 收起仍然保留，但语义变了

- 既然默认就列任务，展开态只用来显示**更多细节**：waiting 会话的问题与选项
  （每个问题一行 `？ 问题文本`，最多 2 个问题；选项各一行 `· 标签 — 说明`）。
- 点气泡**空白处**切换展开/收起（沿用第一版逻辑与 `self.expanded` 记忆）。
- 用户手动收起过就不要再自动弹开（第一版逻辑正确，保留）。
- `pose == "waiting"` 时首次出现仍**强制展开一次**并加 `pet-bubble-alert`（黄框）。

## R3. 点击任务 → 必须真的跳转

- 点某个任务那一行 → 立刻触发跳转，**并给一个视觉反馈**（那一行底色短暂变化，
  或气泡边框闪一下，用 CSS class 切换即可），让用户知道点到了。
- 跳转链路**两层都要做**，第二层是第一层失败时的兜底：
  1. `POST /dsh-pet-desktop/api/focus`，body `{"sessionId": "<id>"}`
     （第一版已实现，Host 端返回 `{ok:true}` 并留单槽位，client.js 每秒取走并
     `ctx.uiWorkspace.openSession(id)`）。
  2. **D-Bus 兜底**：如果上面的 POST 失败（连不上 Host），调
     ```bash
     gdbus call --session --dest dsh.web --object-path /dsh/web \
       --method org.freedesktop.Application.Activate "{}"
     ```
     把 DSH 窗口带到前台（**这条已实测可用**，见下）。用 `subprocess.run`，超时 3 秒，
     失败静默。
- **两层都不要弹任何错误对话框**。

## R4. 已实测的事实（直接用）

- `gdbus call --session --dest dsh.web --object-path /dsh/web --method org.freedesktop.Application.Activate "{}"`
  → 返回 `()`，DSH 桌面壳被激活。DSH 桌面壳是 Gtk.Application，D-Bus 名就是 `dsh.web`。
- `ctx.uiWorkspace.openSession(sessionId)` 存在（`UiWorkspaceService`，内部
  `replaceMain(target, signal, "reveal")`）。client.js 必须声明 `inject: ['uiWorkspace']`。
- Host 端写接口约定：请求头 `x-dsh-pet-desktop: 1` + `content-type: application/json`。

## R5. 不要改的东西

第一版已验证通过的部分**不要回退**：

- 全局鼠标跟随（`_track_pointer` / `LOOK_INTERVAL_MS = 120`）保持原样。
- `_on_leave` 返回 `False`（不做任何事）保持原样。
- 拖动 / 双击跳跃 / 右键菜单逻辑不动。
- `_watch_parent` 的「先 quit 后 print」顺序不动。
- 气泡的透明配方（`window.pet-bubble` CSS class + RGBA visual）不动。
- `_render()` 里的签名去重不要去掉（`age // 10` 粒度），否则每 3 秒整个列表重建会闪。

## 验收标准（交付前逐条自查，并在报告里给结论）

1. 造 3 个会话（1 running / 1 failed / 1 review），**不展开**气泡，
   气泡上必须能同时看到**三个任务的标题**与各自状态。
2. 标题较长（例如「给桌面宠物加目光跟随与像素风会话气泡」）时**不出现省略号**，
   或只在超过 560px 宽度上限时才省略。
3. 点某一行 → `POST /api/focus` 被发出（用日志或抓包确认），
   并且失败时会走 D-Bus 兜底（可临时把 port 改成死端口验证兜底分支被走到）。
4. waiting 场景：气泡黄框 + 自动展开 + 显示问题文本。
5. `python3 -m py_compile desktop/pet.py`、两个 node `--check` 全过。

## 验证提醒

- 运行中的正式插件**不会**自动加载你的源码改动（源码在
  `~/文档/deepseek-harness/default-workspace/dsh-pet-desktop-linux/`，
  安装副本在 `~/.dsh/profiles/web/node_modules/dsh-pet-desktop-linux/`）。
  自查请用**隔离实例**：独立 `--config /tmp/...`、独立 `--app-id`、独立 `DSH_HOME`。
- **绝对不要**碰用户正在跑的正式实例（app-id `com.wlaeg.dsh-pet-desktop-linux.p19387`）。
- 测完杀掉自己起的实例。
