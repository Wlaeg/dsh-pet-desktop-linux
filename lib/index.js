/**
 * dsh-pet-desktop-linux —— Host 半身（Node 侧）。
 *
 * 这个插件把本机跑通的「GTK3 透明置顶桌面宠物」搬进 DSH 插件体系。Node 侧
 * 做不了透明置顶窗口（Electron 太重、node-gtk 编译困难），所以窗口交给包内
 * 自带的 Python 程序 `desktop/pet.py`；Host 半身负责四件事：
 *
 *   1. **依赖探测**：python3 / PyGObject / GTK3 / GdkPixbuf / xdotool 缺一不可，
 *      缺了就给出 Arch / Debian / Fedora 的安装命令，而不是让用户去看 Python 堆栈；
 *   2. **素材定位**：宠物图集属于上游 `@michengai/dsh-codex-pet`（Apache-2.0，
 *      但图集本身来源于 OpenAI Codex、不在其授权范围内），因此本插件 **只依赖、
 *      不复制**，运行时用 require.resolve 找到上游包根，把 `--assets-root` 传给
 *      Python 端 —— 由 Node 解析路径是关键：npm/pnpm 的 hoisting 布局下 Python
 *      自己无从得知包在哪；
 *   3. **进程管理**：spawn / SIGTERM→SIGKILL / 环形日志缓冲，并在插件卸载或
 *      DSH 退出时确保子进程不变成孤儿；
 *   4. **HTTP 路由**：`/dsh-pet-desktop/api/*`，供浏览器半身的设置面板读写状态、
 *      启停宠物、改配置。
 *
 * 【为什么 webServer 必须用 ctx.inject 等】
 * apply() 执行时 webServer 服务可能还没挂载（取决于 bundle patch 的加载顺序），
 * 直接 ctx.get('webServer') 拿到 undefined 会让路由永远注册不上。
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { spawn, execFile } from 'node:child_process'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

export const name = 'dsh-pet-desktop-linux'

/** 声明服务依赖：webServer 就绪后才 apply（与 ctx.inject 双保险）。 */
export const inject = ['webServer']

const NS = name
/** 路由前缀（DESIGN.md 冻结：/dsh-pet-desktop/api/*）。 */
const BASE = '/dsh-pet-desktop'
/** 素材来源：上游宠物插件（npm dependency，绝不把 webp 拷进本仓库）。 */
const UPSTREAM_PACKAGE = '@michengai/dsh-codex-pet'
/** 受信任写入的请求头（与上游宠物插件的 x-dsh-pet 同一套思路）。 */
const TRUST_HEADER = 'x-dsh-pet-desktop'
const TRUST_VALUE = '1'
/** stdout/stderr 环形缓冲只保留最近 200 行。 */
const LOG_LIMIT = 200
/** 请求体上限：配置很小，超过就是有人在乱来。 */
const BODY_LIMIT = 64 * 1024
/** 依赖探测结果的缓存时长（探测要起 4 个 python 子进程，别每个请求都跑）。 */
const PROBE_CACHE_MS = 30_000
/** stop() 的宽限期：SIGTERM 之后多久升级为 SIGKILL。 */
const STOP_GRACE_MS = 4_000
/**
 * pet.py 的 Gtk.Application 单实例 id 前缀。实际用的是 `<前缀>.p<DSH端口>`，
 * 这样每个 profile 的宠物实例互不撞名（撞名会导致新进程静默退出，见 resolveAppId）。
 */
const APP_ID_BASE = 'com.wlaeg.dsh-pet-desktop-linux'
/** DSH 启动后延迟多久自动拉起宠物（避开启动高峰，也等 X 连接就绪）。 */
const AUTO_START_DELAY_MS = 1_500

/** lib/index.js → 包根。 */
const packageRoot = fileURLToPath(new URL('../', import.meta.url))
/** ESM 里没有 require，用它解析上游依赖（从本插件位置向上找 node_modules）。 */
const requireFromHere = createRequire(import.meta.url)

/** 包内 Python 程序（T1 交付物）。 */
function petScriptPath() {
  return path.join(packageRoot, 'desktop', 'pet.py')
}

// ── 配置 ──────────────────────────────────────────────────────────────────────

/** DSH 主目录。 */
function dshHome() {
  return process.env.DSH_HOME || path.join(os.homedir(), '.dsh')
}

/** 配置文件路径（与上游网页版 ~/.dsh/codex-pet/config.json 刻意分开）。 */
function configPath() {
  return path.join(dshHome(), 'codex-pet-desktop', 'config.json')
}

/** 默认配置（DESIGN.md 冻结）。 */
const DEFAULT_CONFIG = {
  selected: 'codex',
  size: 104,
  hidden: false,
  position: { x: 0.94, y: 0.7 },
  /**
   * DSH 启动后自动拉起桌面宠物。
   *
   * 默认 true 是「装完即用」的关键：用户装完插件重启 DSH，宠物窗口自己就来了，
   * 不需要先去设置页点一次「启动」。缺依赖 / 缺素材时只在日志里留一条提示，
   * 不阻塞、不报错（见 apply() 里的 autoStart 调用）。
   */
  autostart: true,
}

/** 上游内置宠物的显示名（仅用于设置页更友好；未知目录名会退回 id 本身）。 */
const PET_NAMES = {
  codex: 'Codex',
  dewey: 'Dewey',
  fireball: 'Fireball',
  hoots: 'Hoots',
  rocky: 'Rocky',
  seedy: 'Seedy',
  stacky: 'Stacky',
  bsod: 'BSOD',
  'null-signal': 'Null Signal',
}

const SIZE_MIN = 64
const SIZE_MAX = 224

/** 只接受已知字段，越界/类型不对就退回默认值。 */
export function sanitizeConfig(raw, previous = DEFAULT_CONFIG) {
  const src = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {}
  const base = previous ?? DEFAULT_CONFIG

  let selected = base.selected
  if (typeof src.selected === 'string' && /^[\w-]+$/.test(src.selected)) selected = src.selected

  let size = base.size
  if (src.size !== undefined) {
    const value = Number(src.size)
    if (Number.isFinite(value)) size = Math.round(Math.min(SIZE_MAX, Math.max(SIZE_MIN, value)))
  }

  let hidden = base.hidden
  if (typeof src.hidden === 'boolean') hidden = src.hidden

  let autostart = base.autostart
  if (typeof src.autostart === 'boolean') autostart = src.autostart

  let position = base.position
  if (src.position === null) {
    position = null
  } else if (src.position && typeof src.position === 'object') {
    const x = Number(src.position.x)
    const y = Number(src.position.y)
    if (Number.isFinite(x) && Number.isFinite(y)) {
      position = { x: Math.min(1, Math.max(0, x)), y: Math.min(1, Math.max(0, y)) }
    }
  }

  return { selected, size, hidden, position, autostart }
}

/** 读配置；文件不存在或坏掉都退回默认值（配置永远不该让插件挂掉）。 */
export function readConfig() {
  try {
    const raw = JSON.parse(fs.readFileSync(configPath(), 'utf8'))
    return sanitizeConfig(raw, DEFAULT_CONFIG)
  } catch {
    return { ...DEFAULT_CONFIG, position: { ...DEFAULT_CONFIG.position } }
  }
}

/**
 * 写序列：配置写入串行化，避免两个请求同时 rename 造成互相覆盖。
 * 每次写入都是「临时文件 + rename」的原子替换，读侧永远看不到半截 JSON。
 */
let writeQueue = Promise.resolve()

function atomicWriteConfig(config) {
  const file = configPath()
  fs.mkdirSync(path.dirname(file), { recursive: true })
  const tmp = `${file}.${process.pid}.${Date.now()}.tmp`
  try {
    fs.writeFileSync(tmp, `${JSON.stringify(config, null, 2)}\n`, { mode: 0o600 })
    fs.renameSync(tmp, file)
  } finally {
    try {
      if (fs.existsSync(tmp)) fs.unlinkSync(tmp)
    } catch { /* 临时文件已随 rename 消失，或清理失败都无所谓 */ }
  }
}

/** 原子 + 串行地写配置，返回写完后真正落盘的规范值。 */
export function writeConfig(config) {
  const operation = writeQueue.then(() => {
    atomicWriteConfig(config)
    return config
  })
  // 队列本身不能因为一次失败而断掉。
  writeQueue = operation.then(
    () => undefined,
    () => undefined,
  )
  return operation
}

// ── 素材定位（上游包，只依赖不复制）─────────────────────────────────────────

/**
 * 依次尝试多种解析起点，覆盖三种真实布局：
 *   1. 本插件自己所在的 node_modules（npm/pnpm 正常安装）；
 *   2. profile 的 node_modules（hoisting 后上游与插件平级）；
 *   3. 直接拼路径（require.resolve 被 exports 字段挡住时的兜底）。
 */
export function resolveAssets() {
  const tried = []
  const manifestPaths = []

  const push = value => {
    if (typeof value === 'string' && value !== '' && !manifestPaths.includes(value)) manifestPaths.push(value)
  }

  try {
    push(requireFromHere.resolve(`${UPSTREAM_PACKAGE}/package.json`))
  } catch (error) {
    tried.push(`require.resolve: ${error?.code ?? error?.message ?? error}`)
  }

  const profileDirs = []
  if (process.env.DSH_PROFILE_DIR) profileDirs.push(process.env.DSH_PROFILE_DIR)
  const profileName = process.env.DSH_PROFILE || 'web'
  profileDirs.push(path.join(dshHome(), 'profiles', profileName))
  profileDirs.push(path.join(dshHome(), 'profiles', 'web'))

  for (const dir of profileDirs) {
    try {
      push(createRequire(path.join(dir, 'package.json')).resolve(`${UPSTREAM_PACKAGE}/package.json`))
    } catch (error) {
      tried.push(`${dir}: ${error?.code ?? error?.message ?? error}`)
    }
    push(path.join(dir, 'node_modules', UPSTREAM_PACKAGE, 'package.json'))
    push(path.join(dir, 'node_modules', ...UPSTREAM_PACKAGE.split('/'), 'package.json'))
  }

  const notInstalled = {
    ok: false,
    root: null,
    assetsDir: null,
    pets: [],
    detail: `未找到上游素材包 ${UPSTREAM_PACKAGE}`,
    hint: '需要同时安装上游宠物插件（本插件依赖它的图集，按合规要求不做拷贝）：\n'
      + `  dsh plugin add ${UPSTREAM_PACKAGE}\n`
      + '或在本插件目录执行：npm install',
    tried,
  }

  for (const manifest of manifestPaths) {
    let root
    try {
      if (!fs.existsSync(manifest)) { tried.push(`不存在：${manifest}`); continue }
      root = path.dirname(manifest)
      const assetsDir = path.join(root, 'assets', 'codex')
      const pets = listPets(assetsDir)
      if (pets.length === 0) {
        tried.push(`无图集：${assetsDir}`)
        continue
      }
      return {
        ok: true,
        root,
        assetsDir,
        pets,
        detail: `已定位上游素材：${root}（${pets.length} 只宠物）`,
        hint: '',
        tried,
      }
    } catch (error) {
      tried.push(`${manifest}: ${error?.message ?? error}`)
    }
  }

  return notInstalled
}

/** 扫描 `<assets>/codex/` 目录：只有带 spritesheet.webp 的才算可用宠物。 */
export function listPets(assetsDir) {
  if (typeof assetsDir !== 'string' || assetsDir === '') return []
  let entries
  try {
    entries = fs.readdirSync(assetsDir, { withFileTypes: true })
  } catch {
    return []
  }
  const pets = []
  for (const entry of entries) {
    if (!entry.isDirectory() || !/^[\w-]+$/.test(entry.name)) continue
    const sprite = path.join(assetsDir, entry.name, 'spritesheet.webp')
    if (!fs.existsSync(sprite)) continue
    pets.push({
      id: entry.name,
      name: PET_NAMES[entry.name] ?? entry.name,
      sprite,
      source: 'builtin',
    })
  }
  return pets.sort((a, b) => a.id.localeCompare(b.id))
}

// ── 依赖探测 ──────────────────────────────────────────────────────────────────

const HINT_PYGOBJECT = [
  'Arch:   sudo pacman -S python-gobject',
  'Debian: sudo apt install python3-gi python3-gi-cairo',
  'Fedora: sudo dnf install python3-gobject',
].join('\n')

const HINT_GTK3 = [
  'Arch:   sudo pacman -S gtk3',
  'Debian: sudo apt install gir1.2-gtk-3.0',
  'Fedora: sudo dnf install gtk3',
].join('\n')

const HINT_PIXBUF = [
  'Arch:   sudo pacman -S gdk-pixbuf2',
  'Debian: sudo apt install gir1.2-gdkpixbuf-2.0',
  'Fedora: sudo dnf install gdk-pixbuf2',
].join('\n')

const HINT_XDOTOOL = [
  'Arch:   sudo pacman -S xdotool',
  'Debian: sudo apt install xdotool',
  'Fedora: sudo dnf install xdotool',
].join('\n')

const HINT_PYTHON = [
  'Arch:   sudo pacman -S python',
  'Debian: sudo apt install python3',
  'Fedora: sudo dnf install python3',
].join('\n')

/** 已解析过的可执行文件；undefined = 没找过，null = 本机没有。 */
const whichCache = new Map()

/** 在 PATH 里找可执行文件（不 shell 展开）。 */
function which(command) {
  if (whichCache.has(command)) return whichCache.get(command)
  let found = null
  for (const dir of String(process.env.PATH || '').split(path.delimiter)) {
    if (dir === '') continue
    const candidate = path.join(dir, command)
    try {
      fs.accessSync(candidate, fs.constants.X_OK)
      if (fs.statSync(candidate).isFile()) { found = candidate; break }
    } catch { /* 下一个目录 */ }
  }
  whichCache.set(command, found)
  return found
}

/** 跑一个 python 片段，返回 {ok, output}。 */
function runPython(python, code, timeout = 8_000) {
  return new Promise(resolve => {
    execFile(python, ['-c', code], { timeout, windowsHide: true }, (error, stdout, stderr) => {
      const out = String(stdout ?? '').trim()
      const err = String(stderr ?? '').trim()
      if (error) {
        resolve({ ok: false, output: err.split('\n').filter(Boolean).slice(-1)[0] || String(error.message ?? error) })
        return
      }
      resolve({ ok: true, output: out })
    })
  })
}

let probeCache = null

/**
 * 探测本机依赖。返回：
 *   { ok, checkedAt, items: [{ id, label, ok, detail, hint }] }
 * 每项的 hint 都是可直接粘贴的安装命令（Arch / Debian / Fedora 三行）。
 */
export async function probeDependencies(options = {}) {
  const force = options.force === true
  const now = Date.now()
  if (!force && probeCache && now - probeCache.checkedAt < PROBE_CACHE_MS) return probeCache

  const items = []
  const python = which('python3')

  // 1) python3
  if (python === null) {
    items.push({ id: 'python3', label: 'Python 3', ok: false, detail: 'PATH 中找不到 python3', hint: HINT_PYTHON })
  } else {
    let detail = python
    try {
      const { execFileSync } = await import('node:child_process')
      detail = `${execFileSync(python, ['--version'], { encoding: 'utf8' }).trim()} · ${python}`
    } catch { /* 拿不到版本不影响判断 */ }
    items.push({ id: 'python3', label: 'Python 3', ok: true, detail, hint: '' })
  }

  if (python === null) {
    // python 都没有，后面的 pygobject 探测没有意义，直接标记为失败。
    items.push({ id: 'pygobject', label: 'PyGObject (import gi)', ok: false, detail: '需要先安装 python3', hint: HINT_PYGOBJECT })
    items.push({ id: 'gtk3', label: 'GTK3 typelib', ok: false, detail: '需要先安装 python3 / PyGObject', hint: HINT_GTK3 })
    items.push({ id: 'gdkpixbuf', label: 'GdkPixbuf typelib', ok: false, detail: '需要先安装 python3 / PyGObject', hint: HINT_PIXBUF })
  } else {
    // 2) PyGObject
    const gi = await runPython(python, 'import gi; print(getattr(gi, "__version__", "ok"))')
    items.push({
      id: 'pygobject',
      label: 'PyGObject (import gi)',
      ok: gi.ok,
      detail: gi.ok ? `gi ${gi.output}` : gi.output,
      hint: gi.ok ? '' : HINT_PYGOBJECT,
    })

    // 3) GTK3 typelib
    const gtk = gi.ok
      ? await runPython(python, "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk; print(Gtk.get_major_version(), Gtk.get_minor_version())")
      : { ok: false, output: 'PyGObject 不可用' }
    items.push({
      id: 'gtk3',
      label: 'GTK3 typelib',
      ok: gtk.ok,
      detail: gtk.ok ? `Gtk ${gtk.output}` : gtk.output,
      hint: gtk.ok ? '' : HINT_GTK3,
    })

    // 4) GdkPixbuf typelib
    const pixbuf = gi.ok
      ? await runPython(python, "import gi; gi.require_version('GdkPixbuf','2.0'); from gi.repository import GdkPixbuf; print('GdkPixbuf ok')")
      : { ok: false, output: 'PyGObject 不可用' }
    items.push({
      id: 'gdkpixbuf',
      label: 'GdkPixbuf typelib',
      ok: pixbuf.ok,
      detail: pixbuf.ok ? pixbuf.output : pixbuf.output,
      hint: pixbuf.ok ? '' : HINT_PIXBUF,
    })
  }

  // 5) xdotool（置顶 / 跨工作区 STICKY 必需）
  const xdotool = which('xdotool')
  items.push({
    id: 'xdotool',
    label: 'xdotool',
    ok: xdotool !== null,
    detail: xdotool ?? 'PATH 中找不到 xdotool',
    hint: xdotool === null ? HINT_XDOTOOL : '',
  })

  const result = {
    ok: items.every(item => item.ok),
    checkedAt: now,
    items,
  }
  probeCache = result
  return result
}

// ── 环形日志缓冲 ──────────────────────────────────────────────────────────────

class RingLog {
  constructor(limit) {
    this.limit = limit
    this.lines = []
  }

  push(text, stream = 'out') {
    const stamp = new Date().toISOString().slice(11, 19)
    for (const line of String(text).split(/\r?\n/)) {
      if (line.trim() === '') continue
      this.lines.push(`[${stamp}]${stream === 'err' ? '[err]' : ''} ${line}`)
    }
    if (this.lines.length > this.limit) this.lines.splice(0, this.lines.length - this.limit)
  }

  note(message) {
    this.push(message, 'sys')
  }

  snapshot() {
    return [...this.lines]
  }
}

// ── HTTP 辅助 ─────────────────────────────────────────────────────────────────

/**
 * 只服务本机与私有网段。DSH 的 webServer 常绑 0.0.0.0，这些路由能启停真实
 * 桌面进程，不该对公网开口。
 */
function allowed(req) {
  const address = req.socket?.remoteAddress ?? ''
  const bare = address.startsWith('::ffff:') ? address.slice(7) : address
  if (bare === '' || bare === '::1' || bare === '127.0.0.1' || bare === 'localhost') return true
  const v4 = /^(\d+)\.(\d+)\.(\d+)\.(\d+)$/.exec(bare)
  if (v4) {
    const a = Number(v4[1])
    const b = Number(v4[2])
    if (a === 10 || a === 127) return true
    if (a === 192 && b === 168) return true
    if (a === 172 && b >= 16 && b <= 31) return true
    return false
  }
  const head = bare.toLowerCase()
  return head.startsWith('fc') || head.startsWith('fd') || head.startsWith('fe8')
}

/**
 * 受信任写入校验：自定义头 + JSON content-type，再挡掉跨站与跨源。
 * 本机脚本（curl / 客户端半身）能轻松满足，浏览器里的第三方页面不能。
 */
export function trustedWrite(req) {
  const headers = req?.headers ?? {}
  if (headers[TRUST_HEADER] !== TRUST_VALUE) return false
  const contentType = headers['content-type']
  if (typeof contentType !== 'string' || !contentType.toLowerCase().startsWith('application/json')) return false
  if (headers['sec-fetch-site'] === 'cross-site') return false
  const origin = headers.origin
  if (typeof origin === 'string' && origin !== '') {
    try {
      if (new URL(origin).host !== headers.host) return false
    } catch {
      return false
    }
  }
  return true
}

function readJson(req) {
  return new Promise(resolve => {
    let text = ''
    req.on('data', chunk => {
      text += chunk
      if (text.length > BODY_LIMIT) {
        text = ''
        req.destroy()
      }
    })
    req.on('end', () => {
      try {
        const value = text.trim() === '' ? {} : JSON.parse(text)
        resolve(value && typeof value === 'object' && !Array.isArray(value) ? value : null)
      } catch {
        resolve(null)
      }
    })
    req.on('error', () => resolve(null))
  })
}

// ── 插件主体 ──────────────────────────────────────────────────────────────────

export function apply(ctx) {
  const logger = ctx?.logger ?? console
  const log = new RingLog(LOG_LIMIT)

  log.note(`[${NS}] 插件已加载；包根 ${packageRoot}`)

  /** 当前子进程状态。child 为 null 表示没在跑。 */
  let child = null
  let startedAt = 0
  let lastExit = null
  let stopping = null
  /** 由 ctx.inject 回调回填，start 时用来取 DSH 端口。 */
  let scopedCtx = null

  function running() {
    return child !== null
  }

  /** 子进程是否还活着（EPERM 也算活着，只是不属于当前用户）。 */
  function isAlive(pid) {
    if (!Number.isInteger(pid) || pid <= 0) return false
    try {
      process.kill(pid, 0)
      return true
    } catch (error) {
      return error?.code === 'EPERM'
    }
  }

  /** 向进程组发信号（detached 子进程自成一组），失败回退到单进程。 */
  function signalProcess(pid, signal) {
    try {
      process.kill(-pid, signal)
      return true
    } catch { /* 组不存在，回退 */ }
    try {
      process.kill(pid, signal)
      return true
    } catch {
      return false
    }
  }

  /** DSH 端口：宠物进程要拿它去连上游网页版宠物的 API。 */
  function resolvePort() {
    const port = scopedCtx?.webServer?.port
    if (Number.isInteger(port) && port > 0) return port
    for (const key of ['DSH_WEB_PORT', 'DSH_PORT', 'PORT']) {
      const value = Number(process.env[key])
      if (Number.isInteger(value) && value > 0) return value
    }
    return 0
  }

  /**
   * 传给 pet.py 的 Gtk.Application 单实例 id。
   *
   * Gtk.Application 用 D-Bus 名做单实例仲裁，撞名的第二个进程会把激活请求转发给
   * 已有实例后**静默退出**（do_activate 不执行、退出码 0），现象是「子进程启动后
   * 立即退出」却毫无日志线索。所以不能所有 profile 共用固定 id：按 DSH 端口派生，
   * 每个 profile 天然隔离（web=19387、其它 profile 各自不同）。
   *
   * 用户若在别的终端手动跑同 id 的实例，pet.py 侧的 app_id_owner() 预检会给出中文
   * 报错而不是静默退出。
   */
  function resolveAppId(port) {
    return `${APP_ID_BASE}.p${Number.isInteger(port) && port > 0 ? port : 0}`
  }

  function snapshot() {
    const config = readConfig()
    const assets = resolveAssets()
    return {
      running: running(),
      pid: child?.pid ?? null,
      startedAt: running() ? startedAt : null,
      lastExit,
      port: resolvePort(),
      config,
      configPath: configPath(),
      petScript: petScriptPath(),
      pets: assets.pets,
      assets,
      log: log.snapshot(),
    }
  }

  /**
   * 拉起桌面宠物。pet.py 不存在（Python 半身尚未交付）时给出明确错误，
   * 而不是让 spawn 抛一个 ENOENT 到用户脸上。
   */
  async function start() {
    if (running()) {
      await stop('restart')
    }
    if (running()) {
      return { ok: false, status: 409, error: '上一个进程还没退干净，请稍后重试' }
    }

    const petPy = petScriptPath()
    if (!fs.existsSync(petPy)) {
      const message = `找不到 Python 桌面程序：${petPy}`
      log.note(`[${NS}] 启动失败：${message}`)
      return { ok: false, status: 503, error: message, hint: '本插件的 desktop/pet.py 缺失，请重新安装插件包' }
    }

    const probe = await probeDependencies()
    if (!probe.ok) {
      const missing = probe.items.filter(item => !item.ok).map(item => item.id)
      log.note(`[${NS}] 启动失败：缺少依赖 ${missing.join(', ')}`)
      return { ok: false, status: 503, error: `缺少依赖：${missing.join(', ')}`, dependencies: probe }
    }

    const assets = resolveAssets()
    if (!assets.ok) {
      log.note(`[${NS}] 启动失败：${assets.detail}`)
      return { ok: false, status: 503, error: assets.detail, hint: assets.hint, assets }
    }

    const python = which('python3') ?? 'python3'
    const cfgPath = configPath()
    const port = resolvePort()
    const args = [
      petPy,
      '--assets-root', assets.root,
      '--config', cfgPath,
      '--port', String(port),
      '--app-id', resolveAppId(port),
      // 让 pet.py 盯着本进程：SIGKILL / 崩溃 / OOM 时上游的 dispose 与 exit
      // 钩子都不会跑，只有子进程自己发现父进程没了才能避免留下孤儿窗口。
      '--parent-pid', String(process.pid),
    ]

    log.note(`[${NS}] 启动：${python} ${args.join(' ')}`)

    let spawned
    try {
      spawned = spawn(python, args, {
        cwd: packageRoot,
        detached: true,
        stdio: ['ignore', 'pipe', 'pipe'],
        windowsHide: true,
        env: {
          ...process.env,
          // Python 的 stdout 默认块缓冲，不加这个日志会几十秒才出现一行。
          PYTHONUNBUFFERED: '1',
        },
      })
    } catch (error) {
      log.note(`[${NS}] spawn 失败：${error?.message ?? error}`)
      return { ok: false, status: 500, error: `启动失败：${error?.message ?? error}` }
    }

    child = spawned
    startedAt = Date.now()
    lastExit = null

    spawned.stdout?.on('data', chunk => log.push(chunk, 'out'))
    spawned.stderr?.on('data', chunk => log.push(chunk, 'err'))
    spawned.on('error', error => {
      log.note(`[${NS}] 子进程错误：${error?.message ?? error}`)
      if (child === spawned) child = null
      lastExit = { at: Date.now(), code: null, signal: null, error: String(error?.message ?? error) }
    })
    spawned.on('exit', (code, signal) => {
      log.note(`[${NS}] 子进程退出：code=${code} signal=${signal}`)
      if (child === spawned) child = null
      lastExit = { at: Date.now(), code, signal }
    })
    // 不拖住 Host 退出：生命周期由 dispose 钩子负责。
    spawned.unref()

    // 给 800ms 观察窗口：能立刻发现「python 起手就崩」这类问题并如实上报。
    await new Promise(resolve => setTimeout(resolve, 800))

    if (child === spawned && isAlive(spawned.pid)) {
      return { ok: true, status: 200, pid: spawned.pid, log: log.snapshot().slice(-20) }
    }

    const tail = log.snapshot().slice(-20).join('\n')
    if (child === spawned) child = null
    return { ok: false, status: 500, error: '子进程启动后立即退出', log: tail }
  }

  /** SIGTERM → 宽限期 → SIGKILL。重复调用共享同一个 promise。 */
  function stop(reason = 'manual') {
    if (stopping) return stopping
    const target = child
    if (target === null) return Promise.resolve({ ok: true, status: 200, stopped: false, reason: 'not-running' })

    log.note(`[${NS}] 停止子进程 pid=${target.pid}（${reason}）`)
    // 注意：promise 的 executor 是同步跑的，可能当场就 finish()，
    // 所以是否要把这次操作记进 stopping 必须靠 done 标记判断。
    let done = null
    // 用 let 而非 const：executor 可能在同步阶段就 finish()，那一刻
    // `pending` 自己还处在 TDZ，finish 里若引用它判断会直接抛
    // ReferenceError。所以 finish 不碰 pending，清理交给下面的 then。
    let pending = null
    pending = new Promise(resolve => {
      let settled = false
      let timer = null
      // 是否已经升级到 SIGKILL。必须单独记一个标志：发出 SIGKILL 后子进程的
      // 'exit' 事件通常比 300ms 兜底更早到达，若只靠 finish(true) 传参，
      // exit 先调 finish(false) 会把 killed 报成 false，客户端就分不清
      // "它自己退的" 还是 "我们强杀的"。
      let escalated = false
      const finish = killed => {
        if (settled) return
        settled = true
        if (timer !== null) clearTimeout(timer)
        if (child === target) child = null
        done = { ok: true, status: 200, stopped: true, killed: killed || escalated, pid: target.pid }
        resolve(done)
      }
      // 这个宽限定时器**故意不 unref**：子进程本身是 unref 的，若再把这个
      // 唯一的"我等 4 秒"承诺也 unref 掉，那么调用方裸 await stop() 时事件
      // 循环会认为无事可做而直接退出，promise 永不 settle。
      // 它最多存活 STOP_GRACE_MS + 300ms，不会长期拖住进程退出。
      timer = setTimeout(() => {
        escalated = true
        log.note(`[${NS}] SIGTERM 超时，升级为 SIGKILL`)
        signalProcess(target.pid, 'SIGKILL')
        // 给内核一点回收时间，避免立刻重启时撞上残留的 X 资源。
        setTimeout(() => finish(true), 300)
      }, STOP_GRACE_MS)
      target.once('exit', () => finish(false))
      signalProcess(target.pid, 'SIGTERM')
      // 极端情况：pid 已经不存在、exit 事件也不会再来。
      if (!isAlive(target.pid)) finish(false)
    })
    if (done === null) {
      stopping = pending
      pending.then(() => { if (stopping === pending) stopping = null })
    }
    return pending
  }

  /** 同步强杀：只能用在 process 'exit' 这种不允许 await 的地方。 */
  function killSync() {
    const target = child
    if (target === null) return
    signalProcess(target.pid, 'SIGKILL')
    child = null
  }

  // ── 路由处理 ────────────────────────────────────────────────────────────────

  function createHandler() {
    return async function handler(req, res) {
      const url = new URL(req.url ?? '/', 'http://localhost')
      // 前缀路由由 webServer 匹配 /dsh-pet-desktop，handler 拿到的是完整 pathname，
      // 这里再剥掉前缀与 /api 段，得到 /state、/config 这类短路由。
      let route = url.pathname
      if (route === BASE) route = '/'
      else if (route.startsWith(`${BASE}/`)) route = route.slice(BASE.length)
      if (route === '/api') route = '/'
      else if (route.startsWith('/api/')) route = route.slice('/api'.length)
      route = route.replace(/\/+$/, '') || '/'
      const method = String(req.method ?? 'GET').toUpperCase()

      const send = (status, payload) => {
        if (res.headersSent || res.writableEnded) return
        res.writeHead(status, {
          'content-type': 'application/json; charset=utf-8',
          'cache-control': 'no-store',
          'x-content-type-options': 'nosniff',
        })
        res.end(JSON.stringify(payload))
      }

      if (!allowed(req)) return send(403, { error: '仅允许本机或私有网段访问' })

      try {
        if (method === 'GET' && route === '/') {
          return send(200, { name: NS, routes: ['/api/state', '/api/start', '/api/stop', '/api/config', '/api/deps'] })
        }

        if (method === 'GET' && route === '/state') {
          // 任务契约要求 state 自带 dependencies，客户端刷新一次就能画全界面。
          // snapshot() 保持同步（start/stop 的返回体要用），这里单独 await 探测；
          // probeDependencies 有 60s 缓存，轮询不会变成反复起子进程。
          const dependencies = await probeDependencies({ force: url.searchParams.get('refresh') === '1' })
          return send(200, { ...snapshot(), dependencies })
        }

        if (method === 'GET' && route === '/deps') {
          const force = url.searchParams.get('refresh') === '1'
          const dependencies = await probeDependencies({ force })
          const assets = resolveAssets()
          const petPy = petScriptPath()
          return send(200, {
            ok: dependencies.ok && assets.ok && fs.existsSync(petPy),
            dependencies,
            assets,
            petScript: {
              ok: fs.existsSync(petPy),
              path: petPy,
              detail: fs.existsSync(petPy) ? 'desktop/pet.py 已就位' : '缺少 desktop/pet.py',
              hint: fs.existsSync(petPy) ? '' : '插件包不完整，请重新安装 dsh-pet-desktop-linux',
            },
          })
        }

        if (method === 'POST' && route === '/start') {
          if (!trustedWrite(req)) return send(403, { error: '请求来源校验失败' })
          const result = await start()
          // 成功与失败都带 state，客户端只需一套解析路径。
          if (!result.ok) return send(result.status ?? 500, { ...result, state: snapshot() })
          return send(200, { ...result, state: snapshot() })
        }

        if (method === 'POST' && route === '/stop') {
          if (!trustedWrite(req)) return send(403, { error: '请求来源校验失败' })
          const result = await stop('api')
          return send(200, { ...result, state: snapshot() })
        }

        if (route === '/config') {
          if (method === 'GET') {
            const assets = resolveAssets()
            return send(200, { config: readConfig(), configPath: configPath(), pets: assets.pets, assets })
          }
          if (method === 'POST') {
            if (!trustedWrite(req)) return send(403, { error: '请求来源校验失败' })
            const body = await readJson(req)
            if (body === null) return send(400, { error: '请求体必须是 JSON 对象' })
            const current = readConfig()
            const next = sanitizeConfig({ ...current, ...body, position: body.position === undefined ? current.position : body.position }, current)
            const assets = resolveAssets()
            if (assets.pets.length > 0 && !assets.pets.some(pet => pet.id === next.selected)) {
              return send(400, { error: `宠物 ${next.selected} 不在可用列表中`, pets: assets.pets.map(pet => pet.id) })
            }
            await writeConfig(next)
            log.note(`[${NS}] 配置已更新：${JSON.stringify(next)}`)
            // 与 start/stop 对齐：任何成功写操作都返回完整 state，客户端一套逻辑搞定。
            return send(200, { ok: true, config: next, configPath: configPath(), pets: assets.pets, state: snapshot() })
          }
          return send(405, { error: `方法不支持：${method}` })
        }

        return send(404, { error: `未知路由 ${route}` })
      } catch (error) {
        logger.warn?.(`[${NS}] 处理 ${method} ${route} 失败：${error?.message ?? error}`)
        try { send(500, { error: String(error?.message ?? error) }) } catch { /* 响应已发出 */ }
      }
    }
  }

  // webServer 可能在 apply 时还没就绪，用 ctx.inject 等它。
  if (typeof ctx?.inject === 'function') {
    ctx.inject(['webServer'], scoped => {
      scopedCtx = scoped
      const server = scoped.webServer
      if (!server || typeof server.register !== 'function') {
        logger.warn?.(`[${NS}] webServer 不可用，HTTP 路由未注册`)
        return
      }
      const dispose = server.register({ kind: 'prefix', path: BASE, handler: createHandler() })
      log.note(`[${NS}] 已注册路由前缀 ${BASE}`)
      if (typeof scoped.effect === 'function') {
        scoped.effect(() => dispose, `${NS}: HTTP 路由`)
      }
      maybeAutoStart()
    })
  } else {
    logger.warn?.(`[${NS}] ctx.inject 不可用，HTTP 路由未注册`)
    maybeAutoStart()
  }

  /**
   * 装完即用：DSH 启动后自动把宠物窗口拉起来。
   *
   * 刻意**不 await**、也刻意吞掉所有失败：这是后台便利行为，不该拖着 DSH 启动，
   * 更不该因为用户机器上没装 GTK3 就让插件加载失败。失败细节留在环形日志里，
   * 用户在设置页一眼能看到缺什么依赖。
   *
   * 已存在同 app-id 的实例（例如插件被热重载、或用户之前手动跑过）时，
   * pet.py 会自己识别单实例并转发激活请求后退出，不会重复开窗。
   */
  function maybeAutoStart() {
    const current = readConfig()
    if (!current.autostart) {
      log.note(`[${NS}] 自动启动已关闭（autostart=false），跳过`)
      return
    }
    if (running()) return
    // 微延迟：让 DSH 自身的启动流程先跑完，别和它抢 CPU / 抢 X 连接。
    const timer = setTimeout(() => {
      start().then(result => {
        if (!result?.ok) {
          log.note(`[${NS}] 自动启动未成功：${result?.error ?? '未知原因'}`)
        } else {
          log.note(`[${NS}] 已自动启动桌面宠物（pid ${result.pid}）`)
        }
      }).catch(error => {
        log.note(`[${NS}] 自动启动异常：${error?.message ?? error}`)
      })
    }, AUTO_START_DELAY_MS)
    // 别让这个定时器拖住进程退出。
    if (typeof timer?.unref === 'function') timer.unref()
  }

  // ── 生命周期：绝不留下孤儿进程 ──────────────────────────────────────────────
  //
  // 三条兜底路径都指向同一个 killSync()，因为它必须幂等且同步：
  //   1. ctx.effect 的清理函数（cordis 首选的资源归还方式）；
  //   2. 'dispose' 事件（老版本/其他 fiber 形态）；
  //   3. process 'exit'（DSH 进程直接退出时最后一道保险）。
  //
  // 注意这三条都拦不住 SIGKILL：kill -9 / OOM killer 下 'exit' 不触发，
  // 而子进程是 detached 的，也不会随父进程组一起收信号。真正兜住这种情况的
  // 是 spawn 时传的 `--parent-pid` —— pet.py 自己每 2 秒查一次父进程还在不在，
  // 发现被 init 收养就退出。所以这里不再额外挂 SIGTERM/SIGINT 处理器
  // （那会干扰 DSH 自身的退出流程），孤儿问题在子进程侧解决，更可靠。
  const cleanup = () => { killSync() }
  try {
    // 两条钩子都挂上，互为保险：cordis 的 fiber 走 effect 归还资源，
    // 但不同版本/形态的 fiber 可能只派发 'dispose' 事件。killSync 幂等，
    // 两者都触发也不会重复杀。
    if (typeof ctx?.effect === 'function') ctx.effect(() => cleanup, `${NS}: 停止桌面宠物`)
    if (typeof ctx?.on === 'function') ctx.on('dispose', cleanup)
  } catch (error) {
    logger.warn?.(`[${NS}] 注册清理钩子失败：${error?.message ?? error}`)
  }
  process.once('exit', cleanup)

  logger.info?.(`[${NS}] 已启用；配置：${configPath()}`)

  // 便于测试与调试：把内部句柄暴露在 ctx 上（cordis 不会因此报错）。
  return { start, stop, snapshot, ring: log }
}

// 供测试与客户端半身参考（不参与插件生命周期）。
export { BASE as routePrefix, RingLog, packageRoot }
