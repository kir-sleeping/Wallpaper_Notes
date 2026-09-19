# Wallpaper_Notes 🗒️

> 把便签直接贴在桌面上。不需要打开任何软件就能看到，退到桌面就能写。

让桌面不只是壁纸——更是一个低摩擦的灵感看板。

高度自定义——美学由自己定义

> **English?** This README is in Chinese because the app is Chinese-first. An English version may come later. The code comments, SKILL.md, and config files are bilingual or English-friendly.

---

## ✨ 功能

**🖼️ 桌面原生显示** — 窗口吸附在桌面层（HWND_BOTTOM），不挡应用

**📑 多便签 + 标签切换** — 一个标签 = 一个 `.md` 文件，直观的标签栏管理

**🖱️ 极低编辑摩擦** — 双击进入编辑模式，双击保存退出；Ctrl+Alt+A 一键唤出（快捷键可在设置中修改）**Esc**在编辑模式时点击Esc，界面自动退回到桌面

**📝 Markdown 渲染** — 实时预览渲染效果：标题（无需空行分隔）、有序/无序/任务列表（可嵌套）、引用、围栏代码块、表格、分隔线、链接（可点击）、删除线、图片（支持 `{width=300}` / `{width=50%}` 尺寸与多图并排）、行内样式全支持

**🎨 高度可定制** — 窗口样式、字体、颜色、透明度、圆角、边框……所有视觉效果均可通过托盘设置的对话框实时调整，所见即所得

**🤖 AI 友好** — 外部程序可直接读写 `notes/` 下的 `.md` 文件；附赠 AI Agent 配套技能（SKILL.md），让 AI 帮你管理便签

**🔤 自定义字体** — 内置字体管理，支持通过 UI 导入任意 `.ttf`/`.otf` 字体，持久保存，重启不丢失

**💎 视觉效果** — 玻璃模糊效果、磨砂质感效果、文字发光、滚动条美化

**🪶 资源占用低** — 后台静默运行，占用极少

---

## 📸 截图

![](screenshots/wallpaper-overview3.png)
![](screenshots/wallpaper-overview.png)
![](screenshots/wallpaper-overview2.png)
*桌面全貌——便签直接贴在壁纸上，不挡应用窗口，融入桌面环境*

---

![](screenshots/wallpaper-overview4.png)
![](screenshots/wallpaper-overview5.png)
*图片支持——桌面随意装饰*

---

![](screenshots/settings-styles.png)
*样式配置界面——丰富且高度自定义化的样式选项，所见即所得*

---

## 🚀 快速开始

### 直接下载

> *暂未打包，计划使用 PyInstaller 打包为单文件 exe*

### 源码运行

```bash
# 1. 克隆仓库
git clone https://github.com/kmom-ux/Wallpaper_Notes.git
cd Wallpaper_Notes

# 2. 安装依赖
pip install -r requirements.txt

# 3. 运行
python main.py
```

### 打包 exe

```bash
pip install pyinstaller
pyinstaller main.py --onefile --noconsole --name Wallpaper_Notes --icon=note_app_icon_final.ico
```

---

## 🎮 使用指南

### 基本操作

| 操作 | 方式 |
|------|------|
| 新建便签 | 点击标签行右端的 `+` |
| 编辑便签 | 双击便签内容区 / 按 `Enter` / 托盘菜单「进入编辑」 |
| 保存并退出编辑 | 再次双击 / 连点第三下 / 按 `Esc`（均自动保存） |
| 切换便签 | 点击便签名；或在标签行上滚动滚轮（上滚上一张、下滚下一张；切到被裁掉的便签时标签行会自动滑过去显示） |
| 删除 / 重命名便签 | 右键便签名 → 删除 / 重命名 |
| 移动便签到分组 | 右键便签名 → 移动到… |
| 新建文件夹 | 右键标签行右端 `+` / 右键标签行空白处 → 新建文件夹 |
| 分组（文件夹） | 右键分组名 → 新建便签 / 新建子文件夹 / 重命名 / 删除；悬停分组名 0.3 秒自动展开，子便签不套底色、由实心胶囊标识 |
| 横向滚动标签行 | `Shift` + 在标签行上滚动滚轮 |
| 唤出窗口 | 默认快捷键 `Ctrl+Alt+A`（可在设置中修改） |
| 设置 | 右键托盘图标 → 设置 |

### 开机自启

打开设置对话框，勾选「开机自启」即可。应用通过 Windows 注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 实现。

---

## 🎨 自定义配置

所有预览样式均可在**设置对话框**中实时调整并预览，无需手动编辑 JSON。

### theme.json 参考

如需直接编辑或批量配置：

| 配置节 | 说明 | 关键字段 |
|--------|------|---------|
| `window` | 窗口外观 | `border_radius` |
| `tab_bar` | 标签栏样式 | `background_color`, `text_color`, `active_text_color`, `active_indicator_color`, `font_family`, `font_size`, `radius_top`, `padding_h`, `padding_v` |
| `tab_strip` | 分组抽屉配色 | `folder_pill_color`, `folder_pill_text_color`, `folder_bg_open`(子项底色), `folder_bg`(子项悬停色), `separator_color`(右缘分隔线), `folder_bar_color`, `plus_color`, `fade_width` |
| `content` | 内容区显示 | `font_family`, `font_size`, `text_color`, `heading_color`, `heading_font_family`, `pane_border_*`, `enable_glass`, `glass_opacity`, `enable_glow`, `enable_frost`, `frost_intensity`, `frost_grain`, `link_color` |
| `editor` | 编辑模式 | `font_family`, `font_size`, `background_color`, `text_color`, `caret_color`, `padding_h`, `padding_v` |
| `scrollbar` | 滚动条 | `width`, `handle_color`, `handle_hover_color`, `track_color` |

### config.json 参考

| 配置项 | 说明 |
|--------|------|
| `hotkey.modifiers` | 组合键（Ctrl, Alt, Shift, Win） |
| `hotkey.key` | 触发按键 |
| `behavior.autostart` | 开机自启（推荐通过设置对话框修改） |
| `window.open_drawers` | 启动时展开的分组抽屉（自动记录，一般无需手动改） |
| `behavior.notes_dir` | 便签目录名（默认 `notes`） |

---

## 🔤 字体管理

支持通过**设置对话框 → 字体管理**添加自定义字体：

1. 点击「添加字体...」按钮
2. 选择 `.ttf` 或 `.otf` 字体文件
3. 字体自动复制到 `fonts/` 目录，注册到应用
4. 所有字体下拉菜单立刻出现新字体，可直接选用
5. 重启应用后自动加载，字体不丢失

---

## 🤖 AI 集成

### AI Agent 辅助技能

本项目附赠一个 **SKILL.md** 文件，专为 AI Agent（如 OpenClaw、Claude Code 等）设计。

将 SKILL.md 配置为 AI 的 companion skill 后，你只需自然语言指令即可：

```
帮我整理桌面便签，把灵感和待办分开
→ AI 读取 notes/ 目录 → 分析内容 → 自动分类整理
```

```
在桌面新增一句今日诗句
→ AI 写入 notes/金句.md → 窗口自动刷新
```

详情见 [`SKILL.md`](./SKILL.md)。

### 手动集成

```bash
# AI 写入文件，应用自动刷新显示
echo "今天是全新的一天，充满无限可能。" > notes/金句.md
```

支持任何能写入 `.md` 文件的程序或脚本。

---

## 📁 项目结构

```
Wallpaper_Notes/
├── main.py              # 入口
├── app.py               # 应用初始化、设置对话框路由
├── window.py            # 窗口管理、标签栏、编辑/预览切换
├── config.py            # 配置持久化（config.json / theme.json）
├── settings.py          # 设置对话框（UI 实时预览）
├── renderer.py          # Markdown → HTML 渲染
├── notes_manager.py     # 便签文件监控（watchdog）
├── hotkey.py            # 全局快捷键（RegisterHotKey）
├── tray.py              # 系统托盘菜单
├── ui_components.py     # 标签行组件（NoteStrip / FolderDrawer / NoteChip）+ QSS 生成
├── models.py            # 数据模型（NoteInfo / FolderNode）
├── theme.json           # 样式默认配置
├── config.json          # 功能默认配置
├── SKILL.md             # AI Agent 配套技能
├── notes/               # 便签数据（子文件夹 = 分组）
│   ├── 便签.md
│   └── 分组/
│       └── 分组内便签.md
├── fonts/               # 自定义字体（UI 导入后自动放置）
├── design-prototypes/   # 界面设计签收稿
├── project-decisions/   # 设计决策记录
├── 开发者指南/           # PRD / 架构设计 / 开发规范等
├── screenshots/         # README 截图
├── requirements.txt
└── .gitignore
```

---

## 🛠 开发

### 环境要求

- Python 3.10+
- Windows 10/11（依赖 Win32 API）
- PySide6

### 安装依赖

```bash
pip install -r requirements.txt
```
### 注意

该项目有一些bug，虽然不影响主要功能，但托盘设置中的部分参数无法生效


### 项目设计文档

- [`开发者指南/PRD.md`](./开发者指南/PRD.md) — 产品需求文档
- [`开发者指南/架构设计.md`](./开发者指南/架构设计.md) — 系统架构说明
- [`开发者指南/开发规范.md`](./开发者指南/开发规范.md) — 开发标准与约定
- [`开发者指南/项目功能映射表.md`](./开发者指南/项目功能映射表.md) — 功能→文件映射
- [`开发者指南/前端样式文档.md`](./开发者指南/前端样式文档.md) — 前端样式参考

---

## 📄 许可证

MIT License — 详见 [LICENSE](./LICENSE) 文件。
