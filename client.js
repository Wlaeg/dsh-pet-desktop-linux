/**
 * dsh-pet-desktop-linux —— 浏览器半身。
 *
 * 与 Host 半身（lib/index.js）的分工：
 *   Host 半身负责依赖探测、把 `desktop/pet.py` 拉起来/停掉、并暴露
 *   `/dsh-pet-desktop/api/*` 路由；本半身只做一件事——在 DSH 设置页里
 *   提供一个「桌面宠物」控制台，把 Host 的状态画出来、把用户的意图发回去。
 *   所有进程操作都在 Host 侧完成，浏览器不碰任何系统命令。
 *
 * 模块形态与 dsh-notify-linux/client.js 一致：手写 ModuleLoader 包，无构建步骤，
 * 除 shell 已提供的 `react` 外无依赖。颜色全部走主题变量，跟随明暗切换。
 *
 * 接口形状以 T2 的 lib/index.js 实测响应为准：`/api/state` 返回
 * `{running,pid,config,pets,dependencies,log[]}`；`/api/deps` 额外带
 * `assets` 与 `petScript` 的明细；三个写操作（start/stop/config）都返回完整 `state`。
 */
window.__ModuleLoader__.load({
  id: 'dsh-pet-desktop-linux',
  factory: require => {
    const React = require('react')
    const { createElement: h, useState, useEffect, useRef, useCallback } = React

    const NS = 'dsh-pet-desktop-linux'
    const API = '/dsh-pet-desktop/api'

    // 与 Host 约定的写请求校验头：两个头同时满足才放行
    // （x-dsh-pet-desktop 的值必须精确等于 '1'，头名大小写不敏感）。
    const WRITE_HEADERS = {
      'content-type': 'application/json',
      'x-dsh-pet-desktop': '1',
    }

    const SIZE_MIN = 64
    const SIZE_MAX = 224
    const POLL_MS = 4000
    const SIZE_DEBOUNCE_MS = 300
    const LOG_LINES = 200

    // ── 文案 ──────────────────────────────────────────────────────────────────
    // 注册进 DSH 的 locale 服务；zh / en 键集合必须完全一致。
    // 例外只有两类，都不是「文案」：安装命令原文（hint），以及 Host 回传的
    // 诊断文本（detail），两者都按原样显示、不做翻译。
    const DICT = {
      zh: {
        'meta.title': '桌面宠物',
        'meta.description': '在桌面上养一只 Codex 宠物：可启动/停止，设置种类与大小，并查看运行状态与日志。',
        nav: '桌面宠物',
        title: '桌面宠物',
        intro: '在桌面上显示一只 Codex 风格宠物。窗口透明、置顶并跨工作区，只在 Linux/X11（XWayland）下可用。开关与种类在这里调整，进程由 DSH 主进程托管。',
        loading: '正在读取状态…',
        loadFailed: '无法连接插件后端，面板暂不可用。',
        retry: '重试',
        actionFailed: '操作失败：{msg}',
        refresh: '刷新',

        status: '运行状态',
        statusRunning: '运行中',
        statusStopped: '已停止',
        pid: '进程号',
        pidNone: '—',
        currentPet: '当前宠物',
        petUnknown: '未选择',

        controls: '控制',
        controlsHint: '启动与停止由 DSH 主进程执行，不需要管理员权限。',
        btnStart: '启动',
        btnStop: '停止',
        btnRestart: '重启',
        busyStarting: '正在启动…',
        busyStopping: '正在停止…',
        busyRestarting: '正在重启…',

        pet: '宠物种类',
        petHint: '宠物图集由上游插件 @michengai/dsh-codex-pet 提供。',
        petNone: '（没有可用宠物）',

        size: '大小',
        sizeHint: '桌面窗口的像素宽度。',
        sizeUnit: 'px',

        hidden: '在桌面上隐藏',
        hiddenHint: '隐藏后进程继续运行，只是窗口不显示。',

        deps: '运行依赖',
        depsOk: '依赖齐全，可以启动。',
        depsMissing: '缺少 {n} 项，桌面宠物暂时无法启动。',
        depsMissingHint: '按下面的命令安装后点「刷新」重新检测。命令需要你手动执行。',
        depsUnknown: '未获取到依赖信息。',
        depAssets: '宠物素材包',
        depScript: '桌面程序 pet.py',

        logs: '运行日志',
        logsHint: '显示最近 {n} 行。',
        logsEmpty: '暂无日志。',
        showLogs: '展开日志',
        hideLogs: '收起日志',
      },
      en: {
        'meta.title': 'Desktop Pet',
        'meta.description': 'Keep a Codex pet on your desktop: start and stop it, pick a species and size, and watch its status and logs.',
        nav: 'Desktop Pet',
        title: 'Desktop Pet',
        intro: 'Show a Codex-style pet on your desktop. The window is transparent, always on top and sticky across workspaces, and it only works on Linux/X11 (XWayland). Tune it here; the DSH host process owns the child process.',
        loading: 'Reading status…',
        loadFailed: 'Cannot reach the plugin backend; the panel is unavailable.',
        retry: 'Retry',
        actionFailed: 'Action failed: {msg}',
        refresh: 'Refresh',

        status: 'Status',
        statusRunning: 'Running',
        statusStopped: 'Stopped',
        pid: 'PID',
        pidNone: '—',
        currentPet: 'Current pet',
        petUnknown: 'none selected',

        controls: 'Controls',
        controlsHint: 'The DSH host process starts and stops the pet; no administrator rights are needed.',
        btnStart: 'Start',
        btnStop: 'Stop',
        btnRestart: 'Restart',
        busyStarting: 'Starting…',
        busyStopping: 'Stopping…',
        busyRestarting: 'Restarting…',

        pet: 'Species',
        petHint: 'The spritesheet comes from the upstream @michengai/dsh-codex-pet package.',
        petNone: '(no pets available)',

        size: 'Size',
        sizeHint: 'Width of the desktop window in pixels.',
        sizeUnit: 'px',

        hidden: 'Hide on the desktop',
        hiddenHint: 'The process keeps running; only the window is hidden.',

        deps: 'Runtime dependencies',
        depsOk: 'All dependencies present; ready to start.',
        depsMissing: '{n} item(s) missing, so the desktop pet cannot start yet.',
        depsMissingHint: 'Install them with the commands below, then press Refresh. Run the commands yourself.',
        depsUnknown: 'No dependency information available.',
        depAssets: 'Pet asset package',
        depScript: 'Desktop program pet.py',

        logs: 'Runtime log',
        logsHint: 'Last {n} lines.',
        logsEmpty: 'No log output yet.',
        showLogs: 'Show log',
        hideLogs: 'Hide log',
      },
    }

    const CSS = `
.dpd_page{display:flex;flex-direction:column;gap:22px;color:var(--dsw-alias-label-primary);font-size:13px;line-height:1.55}
.dpd_h2{font-size:15px;font-weight:600;margin:0}
.dpd_p{margin:0;color:var(--dsw-alias-label-secondary)}
.dpd_group{display:flex;flex-direction:column;border:1px solid var(--dsw-alias-border-l1);border-radius:10px;background:var(--dsw-alias-bg-layer-2);padding:4px 14px}
.dpd_head{padding:12px 0 4px;display:flex;flex-direction:column;gap:2px}
.dpd_headtitle{font-weight:600;font-size:13px}
.dpd_headhint{color:var(--dsw-alias-label-tertiary);font-size:12px}
.dpd_row{display:flex;align-items:center;justify-content:space-between;gap:18px;padding:11px 0;border-top:1px solid var(--dsw-alias-border-l1)}
.dpd_row:first-of-type{border-top:none}
.dpd_rowtext{display:flex;flex-direction:column;gap:1px;min-width:0}
.dpd_rowlabel{font-weight:500}
.dpd_rowhint{color:var(--dsw-alias-label-tertiary);font-size:12px}
.dpd_rowctl{flex:none;display:flex;align-items:center;gap:8px}
.dpd_select{background:var(--dsw-alias-bg-layer-1);color:var(--dsw-alias-label-primary);border:1px solid var(--dsw-alias-border-l2);border-radius:7px;padding:5px 8px;font:inherit;font-size:12px;min-width:150px}
.dpd_btn{background:var(--dsw-alias-bg-layer-1);color:var(--dsw-alias-label-primary);border:1px solid var(--dsw-alias-border-l2);border-radius:7px;padding:5px 12px;font:inherit;font-size:12px;cursor:pointer}
.dpd_btn:hover:not(:disabled){background:var(--dsw-alias-bg-layer-3)}
.dpd_btn:disabled{opacity:.5;cursor:default}
.dpd_btn.primary{border-color:var(--dsw-alias-state-business-primary);color:var(--dsw-alias-state-business-primary)}
.dpd_btns{display:flex;flex-wrap:wrap;gap:8px;padding:12px 0}
.dpd_slider{display:flex;flex-direction:column;gap:2px;align-items:flex-end}
.dpd_range{width:180px;accent-color:var(--dsw-alias-state-business-primary)}
.dpd_scale{display:flex;justify-content:space-between;width:180px;color:var(--dsw-alias-label-tertiary);font-size:11px}
.dpd_check{width:16px;height:16px;accent-color:var(--dsw-alias-state-business-primary);cursor:pointer}
.dpd_state{display:inline-flex;align-items:center;gap:7px;font-weight:500}
.dpd_dot{width:8px;height:8px;border-radius:50%;background:var(--dsw-alias-label-tertiary);flex:none}
.dpd_dot.on{background:var(--dsw-alias-state-success-primary)}
.dpd_dot.off{background:var(--dsw-alias-state-error-primary)}
.dpd_mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;color:var(--dsw-alias-label-secondary)}
.dpd_err{display:flex;align-items:center;gap:10px;color:var(--dsw-alias-state-error-primary)}
.dpd_warn{border:1px solid var(--dsw-alias-state-error-primary);border-radius:10px;background:var(--dsw-alias-bg-layer-2);padding:12px 14px;display:flex;flex-direction:column;gap:8px}
.dpd_warntitle{font-weight:600;color:var(--dsw-alias-state-error-primary)}
.dpd_warnhint{color:var(--dsw-alias-label-secondary);font-size:12px}
.dpd_dep{display:flex;flex-direction:column;gap:3px;padding:7px 0;border-top:1px solid var(--dsw-alias-border-l1)}
.dpd_dep:first-of-type{border-top:none}
.dpd_depname{font-weight:500}
.dpd_depdetail{color:var(--dsw-alias-label-secondary);font-size:12px}
.dpd_cmd{display:block;margin-top:3px;padding:6px 9px;border-radius:7px;background:var(--dsw-alias-bg-layer-1);border:1px solid var(--dsw-alias-border-l1);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;color:var(--dsw-alias-label-primary);white-space:pre-wrap;word-break:break-all;user-select:all}
.dpd_logbox{margin:0 0 12px;padding:10px 12px;border-radius:8px;background:var(--dsw-alias-bg-layer-1);border:1px solid var(--dsw-alias-border-l1);max-height:320px;overflow:auto;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;line-height:1.5;color:var(--dsw-alias-label-secondary);white-space:pre-wrap;word-break:break-all}
.dpd_ok{color:var(--dsw-alias-state-success-primary)}
`

    // ── 同源 HTTP ─────────────────────────────────────────────────────────────
    // 与 dsh-notify 同一套写法：AbortController 超时 + redirect:'error' +
    // 非 2xx 抛错（把 Host 的 error 文本原样带出来）。写请求带两个校验头。

    async function api(path, options) {
      const { timeout = 15000, method = 'GET', body, ...rest } = options ?? {}
      const ctrl = new AbortController()
      const timer = setTimeout(() => ctrl.abort(), timeout)
      // 非 GET 一律按写请求处理：**即使没有请求体，也必须带齐那两个校验头**。
      // Host 的 trustedWrite() 同时检查 `x-dsh-pet-desktop` 与 `content-type`，
      // 而 /start、/stop 本来就没有 body；曾经把 headers 挂在 body 分支上，
      // 结果这两个按钮发出去的请求缺头，被 Host 判 403 —— 按钮点了没反应。
      const write = method !== 'GET' && method !== 'HEAD'
      try {
        const response = await fetch(`${API}${path}`, {
          ...rest,
          method,
          redirect: 'error',
          signal: ctrl.signal,
          ...(write ? { headers: WRITE_HEADERS } : {}),
          ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        })
        const text = await response.text()
        let payload
        try { payload = text === '' ? {} : JSON.parse(text) } catch { payload = { error: text.slice(0, 200) } }
        if (!response.ok) throw new Error(payload?.error ?? `HTTP ${response.status}`)
        return payload
      } finally {
        clearTimeout(timer)
      }
    }

    // ── 状态解析 ──────────────────────────────────────────────────────────────
    // 形状以 T2 的 lib/index.js 为准。这里逐字段取值而不是直接 `payload.running`，
    // 只为了让「Host 尚未就绪 / 字段缺失」退化成合理默认值，而不是整块面板白屏。

    function str(value, fallback) {
      return typeof value === 'string' && value !== '' ? value : (fallback ?? '')
    }

    function normalizeState(payload) {
      const root = payload ?? {}
      const config = root.config ?? {}
      const petsRaw = Array.isArray(root.pets) ? root.pets : []
      const pets = petsRaw
        .map(entry => (typeof entry === 'string'
          ? { id: entry, name: entry }
          : { id: str(entry?.id), name: str(entry?.name) || str(entry?.id) }))
        .filter(entry => entry.id !== '')

      const sizeRaw = Number(config.size)
      const size = Number.isFinite(sizeRaw)
        ? Math.min(SIZE_MAX, Math.max(SIZE_MIN, Math.round(sizeRaw)))
        : SIZE_MIN
      const logRaw = root.log

      return {
        running: root.running === true,
        pid: typeof root.pid === 'number' ? root.pid : null,
        pets,
        selected: str(config.selected),
        size,
        hidden: config.hidden === true,
        autostart: config.autostart !== false,
        log: Array.isArray(logRaw) ? logRaw.map(line => String(line)) : [],
        assets: root.assets ?? null,
        dependencies: root.dependencies ?? null,
      }
    }

    /**
     * `/api/deps` 的探测结果。三个 ok 是分开的，不能混：
     * `dependencies.ok` 是 5 项运行时依赖，`assets.ok` 是上游图集定位，
     * `petScript.ok` 是 `desktop/pet.py` 是否存在。顶层 `ok` 是三者的与。
     */
    function normalizeDeps(payload) {
      if (payload === null || payload === undefined || typeof payload !== 'object') return null
      const dependencies = payload.dependencies ?? null
      const items = Array.isArray(dependencies?.items) ? dependencies.items : null
      const assets = payload.assets ?? null
      const petScript = payload.petScript ?? null
      // 三个来源全都拿不到时必须返回 null（→「未获取到依赖信息」）。
      // 否则 collectMissing 会拿到空列表，把「探测失败」谎报成「依赖齐全」。
      if (items === null && assets === null && petScript === null) return null
      return { ok: payload.ok === true, items, assets, petScript }
    }

    /** `/api/state` 内嵌的依赖信息，形状与 `/api/deps` 的 `dependencies` 相同。 */
    function depsFromState(state) {
      const dependencies = state?.dependencies
      if (dependencies === null || dependencies === undefined) return null
      return {
        ok: dependencies.ok === true,
        items: Array.isArray(dependencies.items) ? dependencies.items : null,
        assets: state.assets ?? null,
        // state 里的 petScript 是路径字符串，没有 ok 标志；只有 /api/deps 才给。
        petScript: null,
      }
    }

    /**
     * 汇总「真正卡住启动」的项，顺序与 Host 启动前的检查一致：
     * 运行时依赖 → 素材包 → pet.py。返回 `null` 表示依赖信息还没拿到，
     * 此时界面说「未获取到依赖信息」，而不是谎报齐全。
     */
    function collectMissing(deps, t) {
      if (deps === null) return null
      const items = []
      for (const entry of deps.items ?? []) {
        if (entry?.ok === false) {
          items.push({
            key: `dep:${str(entry.id, str(entry.label, 'dependency'))}`,
            name: str(entry.label) || str(entry.id),
            detail: str(entry.detail),
            hint: str(entry.hint),
          })
        }
      }
      const assets = deps.assets
      if (assets !== null && typeof assets === 'object' && assets.ok === false) {
        items.push({ key: 'assets', name: t('depAssets'), detail: str(assets.detail), hint: str(assets.hint) })
      }
      const petScript = deps.petScript
      if (petScript !== null && typeof petScript === 'object' && petScript.ok === false) {
        items.push({ key: 'petScript', name: t('depScript'), detail: str(petScript.detail), hint: str(petScript.hint) })
      }
      return items
    }

    // ── 设置页 ────────────────────────────────────────────────────────────────

    function Row({ label, hint, children }) {
      return h('div', { className: 'dpd_row' },
        h('div', { className: 'dpd_rowtext' },
          h('div', { className: 'dpd_rowlabel' }, label),
          hint ? h('div', { className: 'dpd_rowhint' }, hint) : null),
        h('div', { className: 'dpd_rowctl' }, children))
    }

    function Group({ title, hint, children }) {
      return h('div', { className: 'dpd_group' },
        h('div', { className: 'dpd_head' },
          h('div', { className: 'dpd_headtitle' }, title),
          hint ? h('div', { className: 'dpd_headhint' }, hint) : null),
        children)
    }

    function Panel(props) {
      const { t } = props
      const [state, setState] = useState(undefined)
      const [deps, setDeps] = useState(null)
      const [failure, setFailure] = useState('')
      const [busy, setBusy] = useState('')
      const [logOpen, setLogOpen] = useState(false)
      const alive = useRef(true)
      const sizeTimer = useRef(null)

      useEffect(() => () => {
        alive.current = false
        clearTimeout(sizeTimer.current)
      }, [])

      // 三个写操作都返回完整 state，统一从这里覆盖本地状态，不用回读。
      const absorb = useCallback(payload => {
        const next = payload?.state
        if (next === undefined || next === null || typeof next !== 'object') return undefined
        const normalized = normalizeState(next)
        if (alive.current) setState(normalized)
        return normalized
      }, [])

      const readState = useCallback(async () => {
        const payload = await api('/state')
        if (!alive.current) return undefined
        const next = normalizeState(payload)
        setState(next)
        // 依赖信息优先用 /api/deps 的完整版本；它还没到时先用 state 内嵌的兜底。
        setDeps(previous => previous ?? depsFromState(next))
        return next
      }, [])

      const readDeps = useCallback(async (force) => {
        try {
          const payload = await api(force === true ? '/deps?refresh=1' : '/deps')
          if (!alive.current) return
          const next = normalizeDeps(payload)
          if (next !== null) setDeps(next)
        } catch { /* 依赖接口不可用时保留 state 内嵌的结果 */ }
      }, [])

      const load = useCallback((force) => {
        setFailure('')
        readState()
          .then(() => { if (alive.current) void readDeps(force === true) })
          .catch(error => { if (alive.current) setFailure(String(error?.message ?? error)) })
      }, [readState, readDeps])

      useEffect(() => { load(false) }, [load])

      // 面板开着时轮询，让「运行中 / PID」跟着 child 的真实状态走
      // （进程可能被外部 kill 掉，Host 每次都用 process.kill 真实探测）。
      useEffect(() => {
        const timer = setInterval(() => {
          readState().catch(() => { /* 静默重试 */ })
        }, POLL_MS)
        return () => clearInterval(timer)
      }, [readState])

      const run = useCallback(async (label, work) => {
        setBusy(label)
        setFailure('')
        try {
          absorb(await work())
        } catch (error) {
          if (alive.current) setFailure(String(error?.message ?? error))
        } finally {
          if (alive.current) {
            setBusy('')
            readState().catch(() => { /* 下一轮轮询会纠正 */ })
          }
        }
      }, [absorb, readState])

      const start = useCallback(() => run('start', () => api('/start', { method: 'POST' })), [run])
      const stop = useCallback(() => run('stop', () => api('/stop', { method: 'POST' })), [run])

      // Host 没有 /api/restart（DESIGN.md 未冻结该路由）。stop 是 await 的，
      // 返回时子进程已经收尸完毕，所以直接接着 start 即可，不需要轮询。
      const restart = useCallback(() => run('restart', async () => {
        await api('/stop', { method: 'POST' })
        return api('/start', { method: 'POST' })
      }), [run])

      const patchConfig = useCallback(async (partial) => {
        setFailure('')
        try {
          absorb(await api('/config', { method: 'POST', body: partial }))
        } catch (error) {
          if (alive.current) {
            setFailure(String(error?.message ?? error))
            void readState().catch(() => { /* 用服务端的值把乐观更新纠正回来 */ })
          }
        }
      }, [absorb, readState])

      // 滑块拖动时 onChange 连续触发：先本地跟随，再 debounce 提交，
      // 避免每移动一格都打一次 /api/config。
      const onSizeInput = useCallback(value => {
        setState(previous => (previous === undefined ? previous : { ...previous, size: value }))
        clearTimeout(sizeTimer.current)
        sizeTimer.current = setTimeout(() => { void patchConfig({ size: value }) }, SIZE_DEBOUNCE_MS)
      }, [patchConfig])

      const onHideToggle = useCallback(checked => {
        setState(previous => (previous === undefined ? previous : { ...previous, hidden: checked }))
        void patchConfig({ hidden: checked })
      }, [patchConfig])

      const onAutostartToggle = useCallback(checked => {
        setState(previous => (previous === undefined ? previous : { ...previous, autostart: checked }))
        void patchConfig({ autostart: checked })
      }, [patchConfig])

      const onPetChange = useCallback(value => {
        setState(previous => (previous === undefined ? previous : { ...previous, selected: value }))
        void patchConfig({ selected: value })
      }, [patchConfig])

      if (state === undefined) {
        return h('div', { className: 'dpd_page' },
          failure === ''
            ? h('p', { className: 'dpd_p' }, t('loading'))
            : h('div', { className: 'dpd_err' },
                h('span', null, t('loadFailed')),
                h('button', { className: 'dpd_btn', onClick: () => load(false) }, t('retry'))))
      }

      const locked = busy !== ''

      const statusRow = h(Row, { key: 'st', label: t('status') },
        h('span', { className: 'dpd_state' },
          h('span', { className: `dpd_dot${state.running ? ' on' : ' off'}` }),
          state.running ? t('statusRunning') : t('statusStopped')))

      const pidRow = h(Row, { key: 'pid', label: t('pid') },
        h('span', { className: 'dpd_mono' }, state.pid === null ? t('pidNone') : String(state.pid)))

      const petRow = h(Row, { key: 'pet', label: t('currentPet') },
        h('span', { className: 'dpd_mono' }, state.selected === '' ? t('petUnknown') : state.selected))

      const controlButtons = [
        h('button', {
          key: 'start',
          className: 'dpd_btn primary',
          disabled: locked || state.running,
          onClick: start,
        }, busy === 'start' ? t('busyStarting') : t('btnStart')),
        h('button', {
          key: 'stop',
          className: 'dpd_btn',
          disabled: locked || !state.running,
          onClick: stop,
        }, busy === 'stop' ? t('busyStopping') : t('btnStop')),
        h('button', {
          key: 'restart',
          className: 'dpd_btn',
          disabled: locked,
          onClick: restart,
        }, busy === 'restart' ? t('busyRestarting') : t('btnRestart')),
      ]

      // 当前值不在列表里（比如 Host 还没扫到图集）时补一个占位项，
      // 否则 <select> 会静默跳到第一个选项、把真值显示错。
      const options = state.pets.slice()
      if (state.selected !== '' && !options.some(entry => entry.id === state.selected)) {
        options.unshift({ id: state.selected, name: state.selected })
      }
      const petOptions = options.length === 0
        ? [h('option', { key: '__none', value: '' }, t('petNone'))]
        : options.map(entry => h('option', { key: entry.id, value: entry.id }, entry.name))

      const petSelectRow = h(Row, { key: 'petsel', label: t('pet'), hint: t('petHint') },
        h('select', {
          className: 'dpd_select',
          value: state.selected,
          disabled: locked || options.length === 0,
          onChange: event => onPetChange(event.target.value),
        }, petOptions))

      const sizeRow = h(Row, { key: 'size', label: t('size'), hint: t('sizeHint') },
        h('div', { className: 'dpd_slider' },
          h('div', { className: 'dpd_mono' }, `${state.size} ${t('sizeUnit')}`),
          h('input', {
            type: 'range',
            className: 'dpd_range',
            min: SIZE_MIN,
            max: SIZE_MAX,
            step: 4,
            value: state.size,
            disabled: locked,
            onChange: event => onSizeInput(Number(event.target.value)),
          }),
          h('div', { className: 'dpd_scale' },
            h('span', null, String(SIZE_MIN)),
            h('span', null, String(SIZE_MAX)))))

      const hiddenRow = h(Row, { key: 'hidden', label: t('hidden'), hint: t('hiddenHint') },
        h('input', {
          type: 'checkbox',
          className: 'dpd_check',
          checked: state.hidden,
          disabled: locked,
          onChange: event => onHideToggle(event.target.checked),
        }))

      const autostartRow = h(Row, { key: 'autostart', label: t('autostart'), hint: t('autostartHint') },
        h('input', {
          type: 'checkbox',
          className: 'dpd_check',
          checked: state.autostart,
          disabled: locked,
          onChange: event => onAutostartToggle(event.target.checked),
        }))

      const missing = collectMissing(deps, t)

      // 依赖提示：缺东西才用红框；齐全给一句绿字；读不到就明说读不到。
      let depsBlock
      if (missing === null) {
        depsBlock = h('p', { className: 'dpd_p' }, t('depsUnknown'))
      } else if (missing.length === 0) {
        depsBlock = h('p', { className: 'dpd_p dpd_ok' }, t('depsOk'))
      } else {
        depsBlock = h('div', { className: 'dpd_warn' },
          h('div', { className: 'dpd_warntitle' }, t('depsMissing').replace('{n}', String(missing.length))),
          h('div', { className: 'dpd_warnhint' }, t('depsMissingHint')),
          missing.map(item => h('div', { key: item.key, className: 'dpd_dep' },
            h('div', { className: 'dpd_depname' }, item.name),
            item.detail === '' ? null : h('div', { className: 'dpd_depdetail' }, item.detail),
            item.hint === ''
              ? null
              : item.hint.split('\n')
                .filter(line => line.trim() !== '')
                .map((line, index) => h('code', { key: index, className: 'dpd_cmd' }, line)))))
      }

      const logTail = state.log.slice(-LOG_LINES)
      const logGroup = h(Group, { title: t('logs'), hint: t('logsHint').replace('{n}', String(LOG_LINES)) },
        h('div', { className: 'dpd_btns' },
          h('button', { className: 'dpd_btn', onClick: () => setLogOpen(open => !open) },
            logOpen ? t('hideLogs') : t('showLogs')),
          h('button', { className: 'dpd_btn', disabled: busy !== '', onClick: () => load(true) }, t('refresh'))),
        logOpen
          ? h('pre', { className: 'dpd_logbox' }, logTail.length === 0 ? t('logsEmpty') : logTail.join('\n'))
          : null)

      const failureBar = failure === ''
        ? null
        : h('div', { className: 'dpd_err' },
            // 用函数式 replace：错误文本里可能出现 `$&` 等替换模式，字符串形式会被它解析。
            t('actionFailed').replace('{msg}', () => failure))

      return h('div', { className: 'dpd_page' },
        h('div', null,
          h('h2', { className: 'dpd_h2' }, t('title')),
          h('p', { className: 'dpd_p' }, t('intro'))),
        failureBar,
        h(Group, { title: t('status'), hint: '' }, [statusRow, pidRow, petRow]),
        h(Group, { title: t('controls'), hint: t('controlsHint') },
          h('div', { className: 'dpd_btns' }, controlButtons)),
        h(Group, { title: t('pet'), hint: t('petHint') }, [petSelectRow, sizeRow, hiddenRow, autostartRow]),
        h(Group, { title: t('deps'), hint: '' }, depsBlock),
        logGroup)
    }

    // ── apply ─────────────────────────────────────────────────────────────────

    function apply(ctx) {
      const t = ctx.locale.bind(NS)
      ctx.effect(() => ctx.locale.register(NS, { zh: DICT.zh, en: DICT.en }), 'dsh-pet-desktop-linux: dictionaries')

      ctx.effect(() => {
        const style = document.createElement('style')
        style.setAttribute('data-plugin', NS)
        style.textContent = CSS
        document.head.appendChild(style)
        return () => { try { style.remove() } catch { /* 已移除 */ } }
      }, 'dsh-pet-desktop-linux: styles')

      ctx.effect(() => {
        const poll = async () => {
          try {
            const response = await fetch(`${API}/focus`)
            if (!response.ok) return
            const body = await response.json()
            if (typeof body?.sessionId !== 'string' || body.sessionId === '') return
            try {
              await ctx.uiWorkspace.openSession(body.sessionId)
            } catch {
              // DSH 界面切换失败时静默丢弃，本轮轮询仍需继续。
            }
          } catch {
            // Host 未启动、路由暂不可用或响应不是 JSON 时静默忽略。
          }
        }
        void poll()
        const timer = setInterval(() => { void poll() }, 1000)
        return () => clearInterval(timer)
      }, 'dsh-pet-desktop-linux: focus polling')

      // order 13：上游 @michengai/dsh-codex-pet 占了 12，本插件紧跟其后。
      ctx.slots.inject('settings.section', () => ctx.slots.register({
        name: 'settings.section',
        id: NS,
        order: 13,
        label: () => t('nav'),
        locale: NS,
      }, props => h(Panel, { ...props, t })))
    }

    return { inject: ['slots', 'locale', 'uiWorkspace'], apply }
  },
})
