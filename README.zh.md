# dsh-pet-desktop-linux

[English](README.md) | **中文**

为 Linux 上的 DSH（DeepSeek Harness）提供一个**桌面宠物**：一个透明的、
无边框、始终置顶的 GTK3 小窗口，浮在桌面上，并根据你的 DSH 会话状态切换动作 ——
空闲、工作中、等待你处理、出错、已完成待查看。

**平台：仅 Linux（X11 / XWayland）。** 不支持 Windows 与 macOS，也不支持
关闭了 XWayland 的纯 Wayland 原生会话。详见下方[环境要求](#环境要求)。

> **正版说明**：本插件只以 `dsh-pet-desktop-linux`（无 scope）之名、由 `Wlaeg`
> 账号发布。同名包若不是来自该账号，均非本插件。

---

## 这不是一个纯聚合包

本插件**自带一个完整可用的桌面程序**，不是只把别人的依赖清单转发一遍的空壳：

| 组成 | 文件 | 职责 |
|---|---|---|
| 桌面程序 | `desktop/pet.py` | 宠物窗口本体：GTK3 透明窗口、精灵图动画、右键菜单、工作状态对话框 |
| Host 半身 | `lib/index.js` | 探测系统依赖、启动/停止 Python 程序、提供 HTTP 路由、读写配置文件 |
| 浏览器半身 | `client.js` | DSH 设置页里的「桌面宠物」面板，附带包内 `locale/{en,zh}.json` 清单文案 |

唯一的依赖 `@michengai/dsh-codex-pet` **只用来读取宠物精灵图**。本插件刻意不把
那些素材拷进自己的仓库：商店规则要求聚合包的依赖必须解析到原作者发布的包，因此本插件
直接依赖上游 npm 包，并在运行时解析素材路径
（`require.resolve('@michengai/dsh-codex-pet/package.json')`）。见
[素材与许可](#素材与许可)。

上游插件把宠物画在 **DSH 网页里**；本插件画的是一个**独立的、始终置顶的桌面窗口**，
页面切到后台、最小化甚至关闭时它照样在动。两者是不同的东西，不是重复条目。

---

## 环境要求

| 项目 | 要求 |
|---|---|
| 平台 | **Linux，X11 或 XWayland。** 已在 Manjaro GNOME（Wayland 会话，宠物走 XWayland）下验证 |
| DSH | 任意带 Cordis 插件系统、提供 `webServer` 与 `settings.section` 插槽的 DSH 构建 |
| Node | **>= 22**（DSH 自身的要求） |
| 桌面会话 | 需要可用的 GTK3 会话。GNOME/Wayland 下宠物是 XWayland 客户端 —— **关掉 XWayland 的纯 Wayland 会跑不起来** |
| npm 依赖 | `@michengai/dsh-codex-pet`（仅用于定位素材） |

### 系统依赖

设置页会探测其中的五项 —— Python 3、PyGObject、GTK3、GdkPixbuf 与 `xdotool` ——
并在缺项时给出对应发行版的安装命令。剩下两项（WebP loader 与 `zstd`）**不在探测范围内**，
如果宠物起来了但表现不对，用下面的命令自查。

| 组件 | Arch / Manjaro | Debian / Ubuntu | Fedora |
|---|---|---|---|
| Python 3 | `python` | `python3` | `python3` |
| Python 3 + PyGObject（`import gi`） | `python-gobject` | `python3-gi` | `python3-gobject` |
| GTK3 typelib | `gtk3` | `gir1.2-gtk-3.0` | `gtk3` |
| GdkPixbuf typelib | `gdk-pixbuf2` | `gir1.2-gdkpixbuf-2.0` | `gdk-pixbuf2` |
| GdkPixbuf 的 WebP 解码 | *（`gdk-pixbuf2` 自带）* | `webp-pixbuf-loader` | `webp-pixbuf-loader` |
| `zstd` 命令 *（仅 Python < 3.14 需要）* | `zstd` | `zstd` | `zstd` |
| xdotool | `xdotool` | `xdotool` | `xdotool` |

```sh
# Arch / Manjaro
sudo pacman -S python python-gobject gtk3 gdk-pixbuf2 zstd xdotool

# Debian / Ubuntu
sudo apt install python3 python3-gi gir1.2-gtk-3.0 gir1.2-gdkpixbuf-2.0 webp-pixbuf-loader zstd xdotool

# Fedora
sudo dnf install python3 python3-gobject gtk3 gdk-pixbuf2 webp-pixbuf-loader zstd xdotool
```

会话事件文件是 zstd 压缩的。Python 3.14 及以上用标准库（`compression.zstd`）解压；
更老的解释器会退回调用 `zstd` 命令行，所以 **`python3` 低于 3.14 时必须装 `zstd`**。
两者都没有时宠物照常显示，但状态会一直停在 `idle`。

精灵图是 WebP，GdkPixbuf 必须能解码它。按发行版里 GdkPixbuf 的版本不同，可能需要额外装
`webp-pixbuf-loader`（Debian/Ubuntu、Fedora）。用下面这条命令自查，打印 `False` 就说明
缺 loader，装上面的包即可：

```sh
python3 -c "import gi; gi.require_version('GdkPixbuf','2.0'); \
from gi.repository import GdkPixbuf; \
print('webp' in [f.get_name() for f in GdkPixbuf.Pixbuf.get_formats()])"
```

插件的依赖探测覆盖 Python 3、PyGObject、GTK3、GdkPixbuf 与 `xdotool` 五项，但**自己不会
真的解码一张 WebP**，也不检测 `zstd`；缺 loader 时窗口照样出现、只是精灵图加载失败，
这在设置面板的日志末尾能看到。

`xdotool` 用来写 GTK3 自己设不了的两个 EWMH 窗口提示：`_NET_WM_STATE_ABOVE`（置顶）
与 `_NET_WM_STATE_STICKY`（全工作区显示）。没有它不仅宠物照样能显示，但既不会压在其他
窗口之上，也不会跟着你切换工作区。

解码走 `GdkPixbuf.PixbufLoader`，帧以 `Gtk.Image` 的 pixbuf 贴出，因此**不需要
Pillow**；绘制也不经过任何 `cairo.Context`，**同样不需要 pycairo**。

---

## 安装

本插件是一个 DSH Cordis **bundle**，通过 DSH 的插件管理器
（`plugin_manager`，`action: install_bundle`）安装，`target` 指向：

- **Git 仓库** —— `https://github.com/Wlaeg/dsh-pet-desktop-linux`
- 或**本地目录** —— 本目录的绝对路径

等价地，在 DSH 的 shell 里：

```sh
# 发布到 npm 之后
dsh plugin --profile web add dsh-pet-desktop-linux

# 直接从仓库安装
dsh plugin --profile web add github:Wlaeg/dsh-pet-desktop-linux

# 从本地目录安装
dsh plugin --profile web add /绝对路径/dsh-pet-desktop-linux
```

bundle 在 `package.json` 里声明自己的 patch：

```json
{
  "dsh": {
    "bundle": { "patch": "./cordis.patch.yml" },
    "client": {
      "platform": "web",
      "immediately": true,
      "inject": ["@deepseek-ai/dsh-client-locale", "@deepseek-ai/dsh-client-ui-renderer"]
    }
  },
  "icon": "./assets/icon.png"
}
```

（`dsh.client.inject` 是**浏览器模块加载顺序**声明：DSH 会先加载列表里这些包的浏览器
半身，再加载本插件。它不是 Cordis 服务注入。浏览器半身真正需要的服务由它自己声明：
`return { inject: ['slots', 'locale'], apply }`。）

而这个 patch 就是一条 `insert`，用来挂载 Host 半身：

```yaml
- insert:
    - id: dsh-pet-desktop-linux
      name: 'dsh-pet-desktop-linux'
      config: {}
```

浏览器半身由 `dsh.client` 声明，并绑定在 `name` 为裸包名的那一行上 —— 子路径行不会
携带半身。

装好后刷新一次 DSH 网页，浏览器半身才会注册设置面板。除此之外不需要别的步骤：**DSH 一
启动，它就会自己把宠物窗口拉起来**（`autostart` 默认为 `true`，见「配置」一节）。你点停止
或卸载 bundle 时宠物进程就退出。

> 覆盖已安装的旧副本（例如改过源码之后）需要**重启 DSH 宿主进程** —— 运行中的进程仍然
> 用启动时加载的那一代 JS 模块。只刷新页面不够。

---

## 功能

**窗口**

- 无边框、透明背景的 GTK3 窗口 —— 只有宠物本体可见，窗口底色完全透明
- 始终置顶（`_NET_WM_STATE_ABOVE`），压过普通窗口
- 跨工作区常驻（`_NET_WM_STATE_STICKY`，经 `xdotool`）—— GTK3 没有对应 API，而且
  GTK 会在窗口 map 之后重写这些提示，所以插件会重试并自愈
- 可拖动；位置以 0–1 归一化坐标持久化
- 尺寸可调（64–224 px），右键菜单与设置面板都能改
- **不实现点击穿透**：窗口保留自己的输入区域，点宠物上的点击落在宠物上，不会穿透到
  它背后的窗口

**动画**

- 从上游精灵图里裁出 192×208 的单元格，再用 `InterpType.NEAREST` 缩放，保持像素边缘锐利
- 每种工作状态对应一行动画，逐帧有各自的时长（idle、向左/向右跑、挥手、跳跃、failed、
  waiting、running、review）
- 鼠标靠近时宠物会朝光标方向看

**五种工作状态** —— 从你的 DSH 会话事件文件推断（`$DSH_HOME/sessions/*/*/session.v4.jsonl.zstd`），
不访问 DSH API（`/api/sessions` 这类端点一律 401）：

| 状态 | 含义 |
|---|---|
| `idle` | 没有会话在忙 |
| `running` | 有会话正在跑一轮 |
| `waiting` | 有会话卡在等你 —— 挂着未返回的 `ask_user_question` 或 `exit_plan_mode` 调用 |
| `failed` | 某会话最近一轮以错误（或 `max-tokens`）结束 |
| `review` | 某一轮已结束，但还没被查看过 |

多个会话同时存在时按优先级取：`waiting` 最高，其次 `running`，再 `failed`，最后
`review`。（桌面版刻意把 `running` 排在 `failed` 之上，这样一个长任务不会因为更早的一次
失败而一直显示错误。）

**右键菜单**

- 只读状态行，显示当前状态（有细节时一并显示）
- **查看工作状态详情…** —— 列出这个状态背后的会话
- **切换宠物** —— 从上游 `assets/codex/` 目录下扫描到的宠物里选
- **放大一点 / 缩小一点** —— 每次 16 px，限制在 64–224
- **隐藏桌面宠物** —— 隐藏窗口，进程继续存活
- **打开 DSH 界面**
- **退出桌面宠物**

**设置面板** —— DSH 设置页里的「桌面宠物」分区：

- 状态行：运行中/已停止、PID、当前宠物 id
- **启动** / **停止** / **重启** 按钮（重启 = 停止 → 等退出 → 启动）
- 宠物下拉框，选项来自 `GET /api/state`
- 尺寸滑块（64–224）与「在桌面上隐藏宠物」开关
- **「随 DSH 自动启动」开关** —— 关掉后 DSH 启动时不会自动拉起宠物，只能手动点「启动」
- 依赖缺失提示：仅在确有缺失时显示，红框列出缺失项与对应的安装命令
- 可折叠的日志视图，显示 `GET /api/state` 返回的最近若干行日志，带刷新按钮，
  启动失败时可以直接在界面上定位

面板是中英双语的（经客户端 locale 注册）。它**不提供**位置拖拽、开机自启（指操作系统级
自启，不是 DSH 内的 `autostart`），也**不支持**同时运行多只宠物。

**Host HTTP 路由**（前缀 `/dsh-pet-desktop`），设置面板在用，本机脚本也可以调：

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/dsh-pet-desktop/api/state` | `{running, pid, startedAt, lastExit, port, config, configPath, petScript, pets, assets, dependencies, log[]}` |
| POST | `/dsh-pet-desktop/api/start` | 启动桌面宠物 |
| POST | `/dsh-pet-desktop/api/stop` | 优雅停止 |
| GET/POST | `/dsh-pet-desktop/api/config` | 读写配置 |
| GET | `/dsh-pet-desktop/api/deps` | 依赖探测结果与安装提示 |

写操作需要请求头 `x-dsh-pet-desktop: 1` 与 `content-type: application/json`。来自本机与
私有网段之外的请求一律 `403` —— 这些路由能启动和停止真实进程。

配置文件不存在时，先用内存里的默认值（`selected: "codex"`、`size: 104`、
`hidden: false`、`position: {x: 0.94, y: 0.70}`、`autostart: true`）；文件本身在**第一次
写入**时（也就是你改了一次设置）才创建。配置写入是原子且串行的（临时文件 + rename），读方
不会看到写了一半的文件。

> `autostart` 由 Host 半身在 DSH 启动时读取。DSH 拉起宠物进程后会**先等 1.5 秒**再
> spawn，避开启动高峰；这一步失败不会影响 DSH 本身，失败原因会出现在设置面板的日志里
> （最常见的就是缺 GTK3/GdkPixbuf/`xdotool`）。

---

## 配置

配置文件与**上游网页版插件的配置文件互相独立**，两边不会互相拉扯：

```
~/.dsh/codex-pet-desktop/config.json
```

```json
{
  "selected": "codex",
  "size": 104,
  "hidden": false,
  "position": { "x": 0.94, "y": 0.70 },
  "autostart": true
}
```

| 字段 | 类型 | 含义 |
|---|---|---|
| `selected` | string | 宠物 id —— 上游 `assets/codex/` 下的任意目录名 |
| `size` | number | 宠物宽度像素，限制在 64–224 |
| `hidden` | boolean | 启动时是否隐藏桌面窗口 |
| `position` | object \| null | 窗口的归一化位置（`0`–`1`）；`null` 表示放右下角 |
| `autostart` | boolean | DSH 启动时是否自动拉起宠物窗口（默认 `true`） |

设置面板与右键菜单能改的东西都会写回这份文件，所以手改文件和在界面上改是等价的。
`autostart` 只有设置面板能改 —— 不过宠物程序本身也**认得并且原样保留**这个字段，
所以你拖动窗口（会触发位置回写）不会把它抹掉。

---

## 素材与许可

- 本插件**自己的代码**是 MIT（见 [LICENSE](LICENSE)）。
- **宠物图集不随本插件分发。** 运行时本插件解析上游 npm 包 `@michengai/dsh-codex-pet`，
  从其 `<root>/assets/codex/<pet>/spritesheet.webp` 读取。本仓库与本包内都不含任何精灵图。
- 上游包的 `NOTICE` 写明：`assets/codex` 下的宠物图集**来源于 OpenAI Codex**，
  不在该项目 Apache License 2.0 的授权范围内 —— 相关权利归原权利人所有。
- `assets/icon.png`（插件管理器「插件」页卡片上用的图标，`package.json` 的 `icon` 字段指向它）是从同一份图集里裁出的单帧，因此同属第三方
  素材，不是本插件的原创作品。
- 本插件是社区项目，与 OpenAI、DeepSeek 均无隶属关系，不是其官方产品。

完整声明见 [NOTICE](NOTICE)。

---

## 排障

| 现象 | 原因与处理 |
|---|---|
| 设置面板提示缺依赖 | 按上面的表格装上对应包，再点面板里的重新检测 |
| 宠物不置顶，或切换工作区后不见了 | 缺 `xdotool`，或窗口管理器不认 EWMH 提示 |
| 宠物窗口完全不出现 | 当前是关掉 XWayland 的纯 Wayland 会话，而宠物是 X11 客户端。可在设置面板看日志末尾 |
| 宠物一直显示 `idle` | 它读的是 DSH 会话文件；确认 DSH 确实在为你在看的那个工作区写会话 |
| 宠物显示的是别的工作区的活动 | 它监听 `$DSH_HOME` 下的会话；用右键菜单的**查看工作状态详情…**看它究竟抓到了哪些会话 |

---

## 卸载

用 DSH 的插件管理器（`plugin_manager`，`action: remove_bundle`）卸载。插件卸载时 Host
半身会停掉 Python 进程。配置文件 `~/.dsh/codex-pet-desktop/config.json` 会保留，想彻底
清干净就自己删掉。

---

## 许可

[MIT](LICENSE) © 2026 Wlaeg —— 仅覆盖本插件的原创代码。第三方素材由
[NOTICE](NOTICE) 说明，不在 MIT 授权范围内。
