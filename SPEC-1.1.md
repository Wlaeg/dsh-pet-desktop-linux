# SPEC 1.1 —— 目光跟随与像素风会话气泡

给 `dsh-pet-desktop-linux` 加两个能力。实现者只改本仓库，不改 DSH 本体。

## 背景（读代码时的事实）

- 桌面宠物是 `desktop/pet.py`（GTK3 + XWayland，约 1950 行）。它是**独立进程**，
  由 `lib/index.js`（Host 半身，Node）在插件加载时 spawn，命令行参数里带
  `--assets-root / --config / --port / --app-id / --parent-pid`。
- 精灵图集 8 列 × 11 行。`look_cell(dx, dy)`（pet.py:529）把方向向量换算成
  **row 9/10 的 16 个朝向帧**；`self.look` 是 `(row, col)`。
- 现在只有指针**进入宠物窗口**时才转视角（`_on_motion`，pet.py:1418），
  离开就 `self.look = None`（`_on_leave`，pet.py:1436）。
- 会话状态来自 `SessionWatcher`（pet.py:807），它读
  `~/.dsh/sessions/<workspace>/<session>/session.v4.jsonl.zstd`。
  `watcher.pose` / `watcher.detail` / `watcher.sessions` 已经是现成的：
  `sessions` 是 dict 列表，每项含 `id / title / cwd / pose / age / questions`。
- 窗口用 `Gtk.Image` 渲染（无 cairo 依赖），窗口自己接事件。
- 应用级 CSS provider 把 `window` / `.background` 清成透明（pet.py:1057 一带）。
  **任何要看得见的新窗口必须自己带一个 class 把底色加回来**，照
  `window.pet-status` 的写法（pet.py 里已有先例）。

## 硬约束（违反即返工）

1. **不要改 `self.look` 的语义**（仍是 `(row, col)` 或 `None`）。
2. **不要动拖动逻辑**：`_on_button_press` / `_on_button_release` 的拖动与双击跳跃必须原样工作。
3. **不要引入新依赖**。允许用 `Gdk` / `GdkPixbuf` / `Pango` / `Gio` / `GLib`。
   新窗口用 `Gtk.Window`（不要 `Gtk.Dialog`，气泡不需要模态）。
4. **不要用 cairo 库**（`import cairo` / `from gi.repository import cairo` 都不可用）。
5. **`GDK_BACKEND=x11` 必须在 `import gi` 之前设好**（已有代码这么做了，别动顺序）。
6. **不要碰 `_watch_parent` 的退出顺序**（先 `quit()` 后 print，见 pet.py:1816 的注释）。
7. 所有面向用户的文本用中文，注释用中文。

## 功能 A：目光跟随鼠标

**行为**：不论鼠标在屏幕哪里，宠物都转头看向它。鼠标移出宠物窗口不再重置视角。

**做法**（已实测可行）：

```python
seat = Gdk.Display.get_default().get_default_seat()
ok, x, y = seat.get_pointer().get_position()   # 返回屏幕坐标（X root）
```

在 `PetWindow` 里加一个 `GLib.timeout_add(LOOK_INTERVAL_MS, self._track_pointer)`
定时器（建议 `LOOK_INTERVAL_MS = 120`），回调里：

1. 取全局指针位置。
2. `fw, fh = self.frame_size`；算窗口中心：用 `self.get_position()` 拿窗口原点，
   或直接 `self.pos_x / self.pos_y`（这两个已经是窗口左上角坐标）。
   中心 = `(pos_x + fw / 2, pos_y + fh / 2)`。
3. `cell = look_cell(px - cx, py - cy)`。
4. **守卫**（与现有 `_on_motion` 保持一致）：`self.base_pose != "idle" or self.action is not None`
   或 `self.dragging` 时，把 `self.look` 置 `None` 并返回；否则赋值并在变化时 `self._render_frame()`。
5. 只在 `cell != self.look` 时重绘（避免无谓刷新）。

**保留 `_on_leave`**：它现在会把 look 清空，但下一个轮询周期会立刻设回来 ——
为了让「鼠标停在窗口边缘」不闪烁，把 `_on_leave` 改成**不做任何事**（直接 `return False`），
或者在 `_track_pointer` 里判断窗口是否已隐藏（`self.get_visible()`）时清 look。

**停止条件**：窗口 `hide()` 时不要再计算（不影响正确性，但省 CPU）。
定时器跟随窗口生命周期即可，不需要显式移除。

## 功能 B：像素风会话气泡

### B1. 外观

一个**独立的小窗口**，贴在宠物旁边，显示当前会话状态。

- 像素风：方角、2px 硬边框、无圆角、无渐变、等宽字体
  （`"DejaVu Sans Mono", "Noto Sans Mono CJK SC", monospace`）。
- 配色建议（深色底 + 高对比描边）：
  底 `#1c1c20`、边框 `#3a3a44`、正文 `#e8e8ea`。
  姿态色沿用 `StatusDialog` 已有的那套：
  waiting `#ffcc66` / running `#6cc0ff` / failed `#ff7b72` / review `#7ee787`。
- **透明窗口**：`set_decorated(False)` / `set_app_paintable(True)` /
  `set_skip_taskbar_hint(True)` / `set_skip_pager_hint(True)` / `set_keep_above(True)` /
  `set_type_hint(Gdk.WindowTypeHint.UTILITY)` / RGBA visual。
  **不要 `set_accept_focus(False)`** —— 气泡要能接收点击。
- 自带 CSS class `pet-bubble`，把底色加回来（照 `pet-status` 的写法）。

### B2. 折叠 / 展开

- **折叠态**（默认）：**一行**。左边一个姿态指示色块，右边一行短文案。
  例：`工作中 · 2`、`等待你处理`、`已完成 · 1`。
  多个会话时显示**优先级最高**的那个姿态 + 会话总数。
- **展开态**：逐条列出 `watcher.sessions`（最多 5 条），每条一行：
  `〔姿态〕标题 · 时长`，标题过长用 `set_ellipsize(Pango.EllipsizeMode.END)` 截断。
  waiting 的条目**额外列出问题**（`questions` 里头的 `question` 文本，最多 2 条）。
- **点击折叠态 → 展开；点击展开态的空白区 → 折叠。**
  两个状态之间用窗口 `resize()` 适配内容高度。
- 鼠标悬停时边框变亮（可选，用 `enter-notify-event` / `leave-notify-event` 加 class）。

### B3. 何时出现

- `watcher.pose == "idle"` → 气泡**隐藏**。
- 否则 → 显示。折叠态与展开态**分别记住**用户的最后选择：
  用 `PetBubble.expanded` 一个布尔字段，用户手动点开/收起时改它；
  **不要**因为 pose 变化就把用户手动收起的气泡又弹开。
- `pose == "waiting"` 时：**强制展开一次**（这是「出现选择则弹出提醒」），
  并在气泡上加一个 `pet-bubble-alert` class（边框换成 `#ffcc66`，可加
  `animation`/闪烁效果——纯 CSS，不要引 Pango 之外的东西）。
  已经展开且已提醒过（同一个 waiting 会话 id 提醒过一次）就不要反复强制展开。

### B4. 位置

- 贴在宠物窗口旁边：气泡底边对齐宠物底边，气泡左边 = 宠物右边 + 8px；
  **屏幕右侧放不下时翻到宠物左边**（气泡右边 = 宠物左边 - 8px）。
  用 `Gdk.Screen.get_monitor_workarea()` 或 `Gdk.Display.get_monitor_workarea()`
  拿工作区判断边界。
- 宠物被拖动时气泡跟着走 —— 在 `_move()` 里同步气泡位置，或在轮询里对齐。

### B5. 数据刷新

- 复用 `PetWindow._poll`（每 3 秒）的节奏更新气泡内容即可，
  不要在气泡里另起定时器读文件。
- 加**签名去重**（照 `StatusDialog._render` 的做法，pet.py:1660 一带）：
  pose + 会话 (id, pose, age//10) 组成的 tuple，没变就不重建内容。

## 功能 C：点击会话 → 定位

用户点展开态里的某个会话条目时，要能把 DSH 界面切到那个会话。

**实现链路**（三段，都要做）：

1. **pet.py**：点击条目时 `POST http://127.0.0.1:<port>/dsh-pet-desktop/api/focus`
   body `{"sessionId": "<id>"}`，请求头带 `x-dsh-pet-desktop: 1`
   （Host 半身对写操作已有这个校验，照 `/api/config` 的写法）。
   用 `urllib.request`，超时 3 秒，失败不要弹任何东西（静默）。
2. **lib/index.js**：新增 `POST /api/focus`（走已注册的 `/dsh-pet-desktop` 前缀）：
   校验头 + `content-type: application/json`；把 `sessionId` 存进一个**单槽位**
   `pendingFocus`（新的覆盖旧的），返回 `{ok: true}`。
   再新增 `GET /api/focus`：返回 `{sessionId: <string|null>}` **并清空**
   （取走即消费）。这两个都是本机回环调用，不需要更多鉴权。
3. **client.js**：`inject` 加 `"uiWorkspace"`；在 `apply(ctx)` 里起一个
   `setInterval(..., 1000)` 轮询 `GET /dsh-pet-desktop/api/focus`；
   拿到非 null 的 `sessionId` 就调 `ctx.uiWorkspace.openSession(sessionId)`。
   **清理**：用 `ctx.effect(() => { const t = setInterval(...); return () => clearInterval(t) }, '...')`
   保证卸载时停掉。
   轮询失败（网络错误 / 404）要静默忽略，不要刷控制台。

   `uiWorkspace.openSession(target)` 的实参就是一个 session id 字符串，
   它内部走 `replaceMain(target, signal, "reveal")`。

**兜底**：如果 `openSession` 抛异常，`catch` 掉不要中断轮询。

## 交付要求

- `python3 -m py_compile desktop/pet.py` 通过；
  `node --input-type=module --check < lib/index.js` 与 `... < client.js` 通过。
- 改动只落在 `desktop/pet.py`、`lib/index.js`、`client.js` 三个文件
  （外加必要时 `locale/*.json`；若动了 locale，中英两份都要有对应 key）。
- 不要改 `package.json` 的 `version`（由维护者发版时统一改）。
- 不要动 `tests/` 里的既有脚本，也不要动 `README*.md`（文档由规格作者更新）。
- 不要提交 git（由规格作者验收后统一提交）。

## 自查清单（实现者交付前逐条核对）

1. 鼠标移到屏幕任意位置，宠物都转向它（不只是窗口内）。
2. 拖动宠物、双击跳跃、右键菜单全部照旧可用。
3. idle 时看不到气泡；一有会话就出现折叠气泡。
4. 点折叠气泡能展开，再点能收起；用户收起后不会被下一轮轮询弹开。
5. waiting 出现时气泡自动展开并变黄框。
6. 点会话条目后，DSH 浏览器界面切到该会话（这条需要真实 DSH 才能验，
   实现者至少确认 `POST /api/focus` 返回 `{ok:true}`、
   `GET /api/focus` 能取走并清空）。
7. 气泡没有不透明灰底（真透明），且不抢焦点。
8. `_watch_parent` 的退出行为没被破坏。
