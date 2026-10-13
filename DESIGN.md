# dsh-pet-desktop-linux 架构冻结（Lead 制定，所有队员遵守）

## 目标

把本机已跑通的桌面宠物（`/home/wlaeg/.local/bin/dsh-pet-desktop.py`，1539 行）打包成
可上架 DSH 插件商店的 npm 插件。验收标准（用户原话 m02451）：

> 打包这些插件，上架dsh插件商店，要做到别人安装我这一个插件就可以直接使用桌面宠物

## 包名与命名（已决策，不得更改）

- npm 包名：**`dsh-pet-desktop-linux`**（无 scope，与商店里已有的
  `dsh-pet-desktop`（owner qiqibabyy，纯 GitHub 条目）区分开）
- 插件 id / cordis 条目名：`dsh-pet-desktop-linux`
- 仓库名：`dsh-pet-desktop-linux`（owner `Wlaeg`）
- 工作目录：`/home/wlaeg/文档/deepseek-harness/default-workspace/dsh-pet-desktop-linux/`

**为什么不叫 `dsh-pet-desktop`：** 商店已有同名条目（不同 owner），且那些是 Windows
原生（Node/Electron 路线）。本插件是 **Linux/X11 专属**（GTK3 + XWayland），命名带
`-linux` 既是事实描述也避免撞名。

## 技术路线（已验证，不要重新论证）

### 1. 桌面窗口 = 插件包内自带的 Python 程序

Node 侧没有能做出「GTK3 透明置顶窗口」的现成方案（Electron 太重、node-gtk 编译困难）。
本机已用 Python + PyGObject 完整跑通，直接复用。

### 2. 素材来源：依赖上游插件，不复制素材（关键合规决策）

- 商店审查第 8 条明确：**依赖必须解析到原作者的 npm 包/仓库，不许把别人作品拷贝到自己账号下**。
- 宠物图集（9 只 webp）来自 `@michengai/dsh-codex-pet@0.1.17`（Apache-2.0），
  且其 `NOTICE` 写明：**图集本身来源于 OpenAI Codex，不在该项目的 Apache-2.0 授权范围内**。
- 因此本插件 **把 `@michengai/dsh-codex-pet` 作为 npm dependency**，
  运行时用 `require.resolve('@michengai/dsh-codex-pet/package.json')` 定位包根，
  再从 `<root>/assets/codex/<pet>/spritesheet.webp` 读图集。**绝不把 webp 拷进本仓库。**
- 实测（m02650）：`require.resolve` 能正确定位，`assets/codex/` 下 9 只宠物齐全，
  `codex/spritesheet.webp` 为 1309110 字节。

### 3. Python 依赖削减到只剩 PyGObject + GTK3（已实测）

原程序依赖 `Pillow`（解 webp）与 `pycairo`（绘制）。**两者都已证实可以去掉**：
- 解码：`GdkPixbuf.PixbufLoader` 直接加载 webp 字节（`loader.write(raw); loader.close()`），
  实测得到 `1536x2288 alpha=True`，角落像素 RGBA=(0,0,0,0)。
- 裁帧：`pixbuf.new_subpixbuf(col*192, row*208, 192, 208)`
- 缩放：`pixbuf.scale_simple(w, h, GdkPixbuf.InterpType.NEAREST)`（保像素风锐利）
- 显示：`Gtk.Image.set_from_pixbuf(scaled)` —— Gtk.Image 自身按 pixbuf 的 alpha 合成，
  无需手工清屏、无需 cairo 上下文。

> ⚠️ **本文件早期版本（及 m02668 的实测）写的 `Gdk.cairo_set_source_pixbuf` 绘制路线
> 已被证伪**：`Gtk.DrawingArea` 的 `draw` 信号要靠 `gi` 的 cairo 转换器把
> `cairo.Context` 传给 Python 回调，而该转换器**内部仍会 `import cairo`**（pycairo）。
> 缺 pycairo 时 `cr` 为 `None`，抛
> `TypeError: Couldn't find foreign struct converter for 'cairo.Context'`。
> 也就是说 **DrawingArea 路线隐含依赖 pycairo，`Gtk.Image` 路线才是真正的零 pycairo**。
> 同理 **不要用 `from gi.repository import cairo`** —— 该命名空间不提供
> `ImageSurface` / `OPERATOR_SOURCE` / `FORMAT_ARGB32`（实测 `AttributeError`）。
>
> 现实现见 `desktop/pet.py` 模块 docstring 第 8-18 行与 `SpriteSheet` 类（约 366 行起）。

**最终 Python 依赖**：`python3` + `python-gobject`(gi) + `gtk3` typelib + `GdkPixbuf` typelib
+ 系统命令 `xdotool`（置顶/跨工作区）。
（另：Python < 3.14 时读会话事件文件需要系统 `zstd` 命令——
`compression.zstd` 是 3.14 才进标准库的。）

### 4. 安装形态与上架要求

- 商店收录**不强制 npm 发布**：先例 `dsh-pet-desktop`（qiqibabyy）的 catalog 条目
  `npm: null`、`version: null`，仅凭 GitHub 仓库被收录。
- 但仓库必须满足（contributing.md 第 54-77 行）：
  1. `package.json` 声明 **`dsh.bundle`**（只有 `dsh.client` 不算，这是最常见被拒原因）
  2. 仓库根有 `cordis.patch.yml`，内含 `insert:` 一条
  3. 有真实可用代码（非占位/纯 README）
  4. 仓库创建满 1 天（自动 CI 检查）
- 提交 = 向 `awesome-dsh-plugin/awesome-dsh-plugin` 提 PR，新增
  `data/plugins/<owner>__<repo>.yml`（即 `Wlaeg__dsh-pet-desktop-linux.yml`）：
  ```yaml
  url: https://github.com/Wlaeg/dsh-pet-desktop-linux
  name: Wlaeg/dsh-pet-desktop-linux
  category: fun
  description:
    en: ...
    zh: ...
  ```

### 5. npm 认证现状（阻塞项，Lead 负责）

`npm whoami` → `ENEEDAUTH`。**npm 未登录**。`gh` 已登录 `Wlaeg`（token 有效）。
→ 结论：本任务**走 GitHub 仓库路线**（不依赖 npm 发布），与竞品做法一致。
若后续要发 npm，需用户提供 token（届时再问，不阻塞当前工作）。

## 包结构（冻结）

```
dsh-pet-desktop-linux/
├── package.json          # dsh.bundle + dsh.client + dependency 上游宠物插件
├── cordis.patch.yml      # insert 一行挂载 host 半身
├── lib/index.js          # Host 半身：依赖探测、spawn 管理、HTTP 路由、配置读写
├── client.js             # 浏览器半身：设置页「桌面宠物」面板 + locale
├── desktop/pet.py        # 桌面窗口程序（从现有 1539 行程序改造）
├── locale/{zh,en}.json   # 文案
├── assets/icon.png       # 插件图标（settings 页用；精灵图取自上游，需注意标注）
├── README.md / README.zh.md
├── LICENSE               # MIT（本插件原创代码）
└── NOTICE                # 说明素材来自上游/OpenAI Codex，非本插件授权范围
```

## 接口契约（Host ↔ Python ↔ Client，冻结）

### Host 半身提供的 HTTP 路由（前缀 `/dsh-pet-desktop`）

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/dsh-pet-desktop/api/state` | `{running, pid, config, dependencies, log[]}` |
| POST | `/dsh-pet-desktop/api/start` | 拉起桌面宠物 |
| POST | `/dsh-pet-desktop/api/stop` | 优雅停止 |
| GET/POST | `/dsh-pet-desktop/api/config` | 读写配置 |
| GET | `/dsh-pet-desktop/api/deps` | 依赖探测结果 + 缺失项的安装提示 |

受信任写入校验与上游宠物插件一致：请求头 `x-dsh-pet-desktop: 1`
+ `content-type: application/json`。本机脚本可直接满足。

### Python 端启动参数（冻结）

```
python3 desktop/pet.py --assets-root <上游包根> --config <配置文件路径> --port <DSH端口> [--wait]
```

- `--assets-root`：由 Host 端用 `require.resolve` 解析后传入，Python 端**不自己找**
  （跨包管理器/hoisting 布局时 Python 无从得知）。
- `--config`：配置 JSON 路径（默认 `~/.dsh/codex-pet-desktop/config.json`），
  与上游网页版 `~/.dsh/codex-pet/config.json` **分开**，避免互相拉扯。
- `--wait`：被动等待模式（DSH 未就绪时不退出，每 5 秒重试）。

### 配置文件（冻结）

`~/.dsh/codex-pet-desktop/config.json`：
```json
{
  "selected": "codex",
  "size": 104,
  "hidden": false,
  "position": {"x": 0.94, "y": 0.70}
}
```
- `selected`：宠物 id，取自 `<assets-root>/assets/codex/` 下的目录名
- `size`：64–224（与上游一致的约束）
- `hidden`：桌面窗口是否隐藏
- `position`：归一化坐标 0–1

## 写入范围划分（避免冲突）

| 任务 | 独占写入范围 |
|---|---|
| T1 Python 桌面端 | `desktop/` |
| T2 Host 半身 | `lib/` |
| T3 客户端半身 | `client.js`、`locale/` |
| T4 打包元数据 | `package.json`、`cordis.patch.yml`、`README*.md`、`LICENSE`、`NOTICE`、`assets/` |
| T5 端到端验证 | `tests/`（只读其他目录） |

Lead 负责 `DESIGN.md`、最终集成、以及对外发布步骤。

## 已知坑（务必避开，全部是本机实测踩出来的）

1. **GTK4 顶层窗口无法透明**（渲染器无关的硬限制）→ 必须 GTK3。
2. **`GDK_BACKEND=x11` 必须写在 `import gi` 之前**，否则 GDK 早已连上 Wayland display，环境变量失效。
3. **gtk4-layer-shell 在 mutter 上永远不可用**（GNOME 不实现 wlr-layer-shell）→ 不要走这条路。
4. **`set_keep_above(True)` 在 mutter 下确实产生 `_NET_WM_STATE_ABOVE`**，但
   **STICKY（跨工作区）GTK3 没有 API**，必须 `xdotool windowstate --add STICKY <xid>`。
5. **`xdotool windowstate` 一次只能加一个属性，属性名须大写**：
   `--add ABOVE` ✓，`-- add,above` 静默失效。
6. **EWMH 写入有启动时机竞争**：GTK 在 map 后还会重写 `_NET_WM_STATE`，
   需在 300/1000/2500ms 重试 + 自愈。
7. **`compression.zstd` 流式读截断文件会抛异常但已读明文完好** → 必须 try 包裹保留部分内容
   （会话文件正在被写入，硬失败会让状态闪断）。
8. **`pkill -f 'dsh-pet-desktop\.py'` 会自匹配执行它的 shell 并自杀** → 必须用 `^` 锚定，
   或 `pgrep -af ... | grep -v 'bash -c'`。
9. **绝对不要用 `read` 目标为 `PPID`**（bash 只读变量，会导致循环永真刷屏）。
10. **截图必须用 `~/.local/bin/shot`**（走 xdg-desktop-portal，已预授权）；
    `gnome-screenshot`/`grim`/`xwd`/`import` 在本机全部失败。
11. **坐标换算注意 scale**：显示器布局会变。当前 `DP-2 2560x1440 scale=1.000 pos=(0,0) [primary]`，
   而此时 X root 与截图尺寸一致（**S=1.0**）。改布局后需重新标定：
   先 `xwininfo -root` 取 X 尺寸、`shot --list` 取逻辑尺寸，再用色块自动定位反推。

## 团队流程

建任务 → 认领 → 做 → 完成。Lead 负责最终集成与验收。
每个队员必须先读本文件。
