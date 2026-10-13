# 项目指令（给 Codex）

## 这是什么
DSH 桌面宠物插件（Linux/GNOME）。Python GTK3 宿主进程画宠物，另有 DSH 插件两半
（`lib/index.js` 宿主端路由、`client.js` 浏览器端注入）。

## 铁律
- **不要动用户的正式运行实例**：app-id 含 `p19387`、端口 19387。测试一律用隔离
  `--config` / `--app-id` / `DSH_HOME`。
- **不要引入新依赖。**
- 改完必须过：`python3 -m py_compile desktop/pet.py`、
  `node --input-type=module --check < lib/index.js`、
  `node --input-type=module --check < client.js`。

## 代码约定
- 中文注释与中文 UI 文案；`pet.py` 是单文件，保持风格一致。
- GTK3 透明窗口配方不要改（RGBA visual + `OPERATOR_SOURCE` 清屏 + `OPERATOR_OVER` 绘制）。
- 气泡/面板之间靠 `self.parent.<field>` 直连，保持现有风格。
