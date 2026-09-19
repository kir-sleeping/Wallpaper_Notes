"""UI 组件工厂与主题引擎。

职责：
1. 根据 theme.json 生成 QSS 样式表字符串
2. 提供预配置的 UI 组件实例（已应用主题的组件）
3. 标签行组件群：NoteStrip（便签 chip + 文件夹抽屉）——
   「超出 QTabWidget 能力」的改造点，分组功能在此落地

边界：
- 组件不触碰文件系统，数据（FolderNode 树）由 window.py 注入
- 右键菜单 / 结构变更回调由 window.py 通过信号承接
"""

from __future__ import annotations

import math
import re
import time
from typing import Any

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QObject,
    QPoint,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
)
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsEffect,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QWidget,
)

# ── QSS 生成 ──────────────────────────────────────────────────

_TAB_QSS = """
QTabBar {{
    background-color: {bg};
    border-top-left-radius: {radius_top}px;
    border-top-right-radius: {radius_top}px;
}}
QTabWidget::pane {{
    background-color: transparent;
}}
QTabBar::tab {{
    background: transparent;
    color: {text_color};
    font-family: "{font_family}";
    font-size: {font_size}px;
    padding: {pv}px {ph}px;
    border: none;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{
    color: {active_color};
    border-bottom: 2px solid {indicator};
}}
QTabBar::tab:hover {{
    color: {active_color};
}}
QTabBar QPushButton {{
    background: transparent;
    border: none;
    color: {text_color};
}}
QTabBar QPushButton:hover {{
    color: {active_color};
}}
"""

_CONTENT_QSS = """
QTextBrowser {{
    background-color: {bg};
    color: {text_color};
    font-family: "{font_family}";
    font-size: {font_size}px;
    border: none;
    border-bottom-left-radius: {pane_br}px;
    border-bottom-right-radius: {pane_br}px;
    padding: {pv}px {ph}px;
}}
"""

_EDITOR_QSS = """
QPlainTextEdit {{
    background-color: {bg};
    color: {text_color};
    font-family: "{font_family}";
    font-size: {font_size}px;
    border: none;
    selection-background-color: {selection};  /* padding+border-radius 已移除——由容器 margin + radius 提供 */
}}
"""

_SCROLLBAR_QSS = """
QScrollBar:vertical {{
    background: {track};
    width: {width}px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {handle};
    border-radius: {width}px;
    min-height: 20px;
}}
QScrollBar::handle:vertical:hover {{
    background: {hover};
}}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {{
    background: none;
}}
QScrollBar:horizontal {{
    height: {width}px;
    background: {track};
}}
QScrollBar::handle:horizontal {{
    background: {handle};
    border-radius: {width}px;
    min-width: 20px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {hover};
}}
QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {{
    width: 0;
}}
"""


def generate_tab_bar_qss(theme: dict[str, Any]) -> str:
    """标签栏样式。"""
    tb = theme.get("tab_bar", {})
    return _TAB_QSS.format(
        bg=_s(tb, "background_color", "rgba(245,245,245,0.9)"),
        radius_top=_i(tb, "radius_top", 0),
        text_color=_s(tb, "text_color", "#666666"),
        font_family=_s(tb, "font_family", "Microsoft YaHei"),
        font_size=_i(tb, "font_size", 14),
        ph=_i(tb, "padding_h", 12),
        pv=_i(tb, "padding_v", 8),
        active_color=_s(tb, "active_text_color", "#333333"),
        indicator=_s(tb, "active_indicator_color", "#4A90D9"),
    )


def generate_content_qss(theme: dict[str, Any], pane_br: int | None = None) -> str:
    """内容区显示模式样式。"""
    c = theme.get("content", {})
    if pane_br is None:
        pane_br = c.get("pane_border_radius", 4)
    return _CONTENT_QSS.format(
        bg=_s(c, "background_color", "transparent"),
        text_color=_s(c, "text_color", "#333333"),
        font_family=_s(c, "font_family", "Microsoft YaHei"),
        font_size=_i(c, "font_size", 16),
        ph=_i(c, "padding_h", 16),
        pv=_i(c, "padding_v", 16),
        pane_br=pane_br,
    )


def generate_editor_qss(theme: dict[str, Any], pane_br: int | None = None) -> str:
    """编辑模式样式。"""
    e = theme.get("editor", {})
    if pane_br is None:
        pane_br = theme.get("content", {}).get("pane_border_radius", 4)
    sel = _s(e, "caret_color", "#4A90D9")
    return _EDITOR_QSS.format(
        bg=_s(e, "background_color", "#FAFAFA"),
        text_color=_s(e, "text_color", "#333333"),
        font_family=_s(e, "font_family", "Cascadia Code, Consolas, monospace"),
        font_size=_i(e, "font_size", 15),
        selection=sel,
    )  # ph/pv 不再出现在 QSS 模板——由容器布局 margins 提供


def generate_scrollbar_qss(theme: dict[str, Any]) -> str:
    """滚动条样式。"""
    sb = theme.get("scrollbar", {})
    return _SCROLLBAR_QSS.format(
        width=_i(sb, "width", 6),
        track=_s(sb, "track_color", "transparent"),
        handle=_s(sb, "handle_color", "rgba(0,0,0,0.2)"),
        hover=_s(sb, "handle_hover_color", "rgba(0,0,0,0.4)"),
    )


# ── 合并样式表 ────────────────────────────────────────────────

def build_global_qss(theme: dict[str, Any]) -> str:
    """一次性生成窗口级和容器级 QSS（子控件样式由局部覆盖）。"""
    return (
        generate_tab_bar_qss(theme)
        + generate_scrollbar_qss(theme)
    )


# ── 组件工厂 ──────────────────────────────────────────────────

def create_tab_bar(parent, theme: dict[str, Any]) -> QTabWidget:
    """创建已应用 theme 样式的 QTabWidget。

    - 标签可关闭（右键菜单由 window.py 处理）
    - 标签不可拖拽重排（MVP）
    - '+' 按钮由 window.py 在右侧追加
    """
    tab = QTabWidget(parent)
    tab.setDocumentMode(True)  # 更扁平的外观
    tab.setMovable(False)
    tab.setTabsClosable(False)  # 删除通过右键菜单，不用关闭按钮
    return tab


# ── 内部工具 ──────────────────────────────────────────────────

def _i(d: dict[str, Any], key: str, default: int) -> int:
    return int(d.get(key, default))


# ═══════════════════════════════════════════════════════════════
# 标签行：便签 chip + 文件夹抽屉（分组功能前端）
# ═══════════════════════════════════════════════════════════════
#
# 视觉规范（与 design-prototypes/group-tabs.html 签收稿一致）：
# - 无图标：文件夹不用 📁/▾，靠「容器隐喻」表达——淡底胶囊包住子项，
#   左侧圆角小（柜体）、右侧圆角大（拉出前沿），嵌套 = 胶囊套胶囊
# - 底色不透明度递增：闭合 → 悬浮 → 展开（嵌套半透明自然叠加变深）
# - 抽屉内首项是文件夹名 chip，点击收起（即返回上级）
# - 行尾低对比度文本 `+` 新建便签（右键新建文件夹）


def _s(d: dict[str, Any], key: str, default: str) -> str:
    return str(d.get(key, default))


def _i(d: dict[str, Any], key: str, default: int) -> int:
    return int(d.get(key, default))


def generate_strip_qss(theme: dict[str, Any]) -> str:
    """生成标签行（NoteStrip 及全部子组件）的 QSS。

    基础样式沿用 theme.tab_bar（背景/文字/激活色/指示条），
    抽屉专属样式来自 theme.tab_strip。
    """
    tb = theme.get("tab_bar", {})
    ts = theme.get("tab_strip", {})

    bg = _s(tb, "background_color", "rgba(245,245,245,0.9)")
    radius_top = _i(tb, "radius_top", 0)
    text_color = _s(tb, "text_color", "#666666")
    active_color = _s(tb, "active_text_color", "#333333")
    indicator = _s(tb, "active_indicator_color", "#4A90D9")

    # 文件夹实心胶囊：底色由 paintEvent 绘制（见 FolderDrawer），QSS 只管文字色
    pill_text = _qcolor(ts.get("folder_pill_text_color", "#ffffff"))
    sep_color = _s(ts, "separator_color", "rgba(0,0,0,0.16)")
    plus_color = _s(ts, "plus_color", "rgba(0,0,0,0.30)")
    plus_hover = _s(ts, "plus_hover_color", "#000000")

    return f"""
QWidget#noteStrip {{
    background-color: {bg};
    border-top-left-radius: {radius_top}px;
    border-top-right-radius: {radius_top}px;
}}
QPushButton#noteChip {{
    background: transparent;
    color: {text_color};
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0;              /* 直角：圆角会让底部指示线两端上翘 */
    padding: 5px 9px;
}}
QPushButton#noteChip:hover {{
    color: {active_color};
}}
QPushButton#noteChip[active="true"] {{
    color: {active_color};
}}
QPushButton#folderNameChip {{
    /* 胶囊底色由 FolderDrawer.paintEvent 绘制（QSS border-radius 在此环境
       被忽略，只能 QPainter 画）；这里只管文字颜色与内边距 */
    background: transparent;
    color: {pill_text.name(QColor.NameFormat.HexArgb)};
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 999px;
    padding: 0px 8px;
}}
QWidget#folderSep {{
    background: {sep_color};
}}
QWidget#stripSep {{
    background: {sep_color};
}}
QPushButton#stripPlus {{
    background: transparent;
    color: {plus_color};
    border: none;
    border-radius: 4px;
    padding: 5px 11px;
}}
QPushButton#stripPlus:hover {{
    color: {plus_hover};
}}
"""


def _repolish(widget: QWidget) -> None:
    """动态属性变更后强制重算 QSS。"""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


def _lerp_color(a: QColor, b: QColor, t: float) -> QColor:
    """两色线性插值（含 alpha），用于指示条的灰→指示色过渡。"""
    t = max(0.0, min(1.0, t))
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
        round(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


def _luminance(c: QColor) -> float:
    """感知亮度（0~255），用于胶囊文字的对比色判断。"""
    return 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()


def _pill_hover_color(c: QColor) -> QColor:
    """胶囊悬停色：亮色微暗、深色微亮（各 8%），保持同色系。"""
    t = 0.08 if _luminance(c) > 128 else -0.08
    white, black = QColor(255, 255, 255), QColor(0, 0, 0)
    return _lerp_color(white if t < 0 else black, c, 1.0 - abs(t))


_CSS_COLOR_RE = re.compile(
    r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)", re.I
)


def _qcolor(css: str) -> QColor:
    """CSS 颜色 → QColor（QColor 自己不认识 rgba(r,g,b,a) 写法）。"""
    s = str(css).strip()
    m = _CSS_COLOR_RE.match(s)
    if m:
        alpha = float(m.group(4)) if m.group(4) is not None else 1.0
        return QColor(
            int(m.group(1)), int(m.group(2)), int(m.group(3)),
            round(alpha * 255),
        )
    return QColor(s)


class NoteChip(QPushButton):
    """便签 chip——裸文字 + 底部指示条。

    指示条：未选中是居中的短灰条，选中后缓动变长并转为指示色。
    长度与颜色都在 paintEvent 里画（QSS 做不了动画），QSS 只管文字与背景；
    底部仍留 2px 透明边框占位，条正好画在这段里，几何与改版前一致。
    """

    _BAR_H = 2                  # 指示条高度
    _BAR_IDLE_RATIO = 0.34      # 未选中时长度占 chip 宽的比例
    _BAR_ANIM_MS = 180          # 变长/收短动画时长

    def __init__(
        self,
        filepath: str,
        name: str,
        idle_color: QColor,
        active_color: QColor,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(name, parent)
        self.setObjectName("noteChip")
        self.filepath = filepath
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # 固定宽度：chip 永不压缩，超出标签行时被裁掉（溢出隔断，同 QTabBar 行为）
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self._idle_color = QColor(idle_color)
        self._active_color = QColor(active_color)
        self._progress = 0.0        # 0 = 短灰条，1 = 满宽指示色条
        self._first_sync = True     # 首次同步状态直接到位，不做动画
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(self._BAR_ANIM_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_bar_anim)

    def set_bar_colors(self, idle_color: QColor, active_color: QColor) -> None:
        """主题换色后同步（不动进度）。"""
        self._idle_color = QColor(idle_color)
        self._active_color = QColor(active_color)
        self.update()

    def set_active(self, active: bool) -> None:
        self.setProperty("active", active)
        _repolish(self)
        target = 1.0 if active else 0.0
        if self._first_sync or abs(target - self._progress) < 0.001:
            self._first_sync = False
            self._anim.stop()
            self._progress = target
            self.update()
            return
        self._anim.stop()
        self._anim.setStartValue(self._progress)
        self._anim.setEndValue(target)
        self._anim.start()

    def _on_bar_anim(self, value) -> None:
        self._progress = float(value)
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        idle_len = max(10.0, w * self._BAR_IDLE_RATIO)
        length = idle_len + (w - idle_len) * self._progress
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_lerp_color(self._idle_color, self._active_color, self._progress))
        radius = self._BAR_H / 2
        painter.drawRoundedRect(
            QRectF((w - length) / 2, h - self._BAR_H, length, self._BAR_H),
            radius, radius,
        )


class _ClipBox(QWidget):
    """抽屉的可视容器：对外宽度 = clip_width，内部内容保持自然宽度并被裁剪。

    「拉出」动画驱动 clip_width 由 0 长到内容宽：内容始终按自身尺寸绘制，
    逐段露出（裁剪），而不是被布局压扁——QBoxLayout 在空间不足时会压到
    minimumSizeHint 以下，因此这里不把内容交给布局约束，而是手动定位。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self._clip_width = 0
        self._inner = QWidget(self)
        self._inner_lay = QHBoxLayout(self._inner)
        self._inner_lay.setContentsMargins(0, 0, 0, 0)
        self._inner_lay.setSpacing(3)

    # 内容层：子项（chip / 嵌套抽屉）加到这里
    @property
    def inner_layout(self) -> QHBoxLayout:
        return self._inner_lay

    @property
    def inner_widget(self) -> QWidget:
        """内容层控件本体（供事件过滤器挂载）。"""
        return self._inner

    def content_width(self) -> int:
        return max(self._inner.sizeHint().width(), 0)

    def clip_width(self) -> int:
        return self._clip_width

    def set_clip(self, width: int) -> None:
        width = max(0, int(width))
        if width == self._clip_width:
            return
        self._clip_width = width
        # sizeHint 变化需要逐级向上通知：host → 抽屉 → 标签行 → 滚动层
        self.updateGeometry()
        parent = self.parentWidget()
        if parent is not None:
            parent.updateGeometry()

    def sizeHint(self) -> QSize:  # type: ignore[override]
        return QSize(self._clip_width, self._inner.sizeHint().height())

    def minimumSizeHint(self) -> QSize:  # type: ignore[override]
        return QSize(0, self._inner.minimumSizeHint().height())

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        # 内容层恒为自然宽度：超出可视宽度的部分由父控件裁剪
        self._inner.setGeometry(
            0, 0, max(self._inner.sizeHint().width(), 1), self.height()
        )


class FolderDrawer(QWidget):
    """文件夹抽屉——自包含容器：[名称 chip | 分隔线 | 子项区]。

    子项区可递归嵌套 FolderDrawer。展开 = 可视宽度 0→内容宽
    的 160ms 动画（内容逐段露出，宽度生长即“拉出”）。父名永远是首个元素，
    点击名称 = 收起（即返回上级）。
    """

    open_requested = Signal(str)   # rel_path — 悬浮 300ms 或点击后请求展开
    close_requested = Signal(str)  # rel_path — 悬浮打开的抽屉，鼠标离开后请求收起

    _HOVER_MS = 300
    _ANIM_MS = 160
    _LEAVE_MS = 260  # 鼠标离开后的宽限期（路过不算离开）

    # 顶部指示条
    _BAR_H = 2                 # 条高
    _PILL_RADIUS = 5           # 胶囊圆角半径（适度小圆角，非全圆）
    _BAR_EASE_TAU_MS = 70      # 缓动时间常数（越大越慢）
    _BAR_TICK_MS = 16          # 缓动帧间隔

    def __init__(
        self, rel_path: str, name: str, strip: "NoteStrip",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("folderDrawer")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.rel_path = rel_path
        self._strip = strip
        self._open = False
        self._anim: QVariantAnimation | None = None
        # 打开来源：悬浮「瞥一眼」打开的抽屉，鼠标离开后自动收起；
        # 点击打开 / 启动恢复的抽屉保持展开（手风琴负责互斥）
        self._hover_open_pending = False
        self._hover_opened = False

        # 顶部指示条：静止时与胶囊平齐，选中子便签后延伸到它。
        # 缓动作用在「右端位置」上（每帧按当前布局重算目标），因此同组内换
        # 子便签、抽屉拉开过程中条都能平滑跟随，而不是跳过去。
        self._bar_color = QColor("#4A90D9")
        self._bar_end = -1.0        # 当前右端（抽屉坐标）；<0 表示尚未初始化
        self._active_chip: QWidget | None = None
        # 胶囊底色（paintEvent 绘制；QSS border-radius 在此环境被忽略）
        self._pill_color = QColor("#4A90D9")
        self._pill_hover = QColor("#4A90D9")
        self._pill_hovered = False
        self._bar_timer = QTimer(self)
        self._bar_timer.setInterval(self._BAR_TICK_MS)
        self._bar_timer.timeout.connect(self._bar_tick)
        self._bar_last_ms = 0.0

        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.timeout.connect(self._on_hover_timeout)

        self._leave_timer = QTimer(self)
        self._leave_timer.setSingleShot(True)
        self._leave_timer.timeout.connect(self._on_leave_timeout)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 1, 3, 1)
        lay.setSpacing(0)  # 间距随开合切换，见 set_open
        self._lay = lay

        # 固定宽度：抽屉宽度恒为内容宽，不随标签行挤压收缩
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self.name_btn = QPushButton(name)
        self.name_btn.setObjectName("folderNameChip")
        self.name_btn.installEventFilter(self)   # 悬停变色用
        self.name_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.name_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.name_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        lay.addWidget(self.name_btn)

        self._host = _ClipBox(self)
        lay.addWidget(self._host)

        # 右缘分隔线：收起时紧跟胶囊，展开时随子项被推到最右侧
        self._sep = QWidget()
        self._sep.setObjectName("folderSep")
        self._sep.setFixedSize(1, 14)
        lay.addWidget(self._sep)

    # ── 子项构建（由 NoteStrip.rebuild 填充）──

    def add_child_widget(self, w: QWidget) -> None:
        self._host.inner_layout.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)

    # ── 顶部指示条 ──

    def set_bar_color(self, color: QColor) -> None:
        self._bar_color = QColor(color)
        self.update()

    def set_pill_colors(self, color: QColor, hover: QColor) -> None:
        self._pill_color = QColor(color)
        self._pill_hover = QColor(hover)
        self.update()

    def set_active_chip(self, chip: QWidget | None) -> None:
        """告知「选中的子便签是否在本抽屉内」，据此延伸或收回顶部条。"""
        self._active_chip = chip
        self._update_bar_target()

    def _bar_top(self) -> float:
        """条的垂直位置：胶囊顶边再下移 1px（条完全落在胶囊的顶边带内，
        与胶囊同色融合为一体；延伸段看起来像胶囊顶边线继续长出去）。"""
        return float(self.name_btn.y() - self._BAR_H + 2)

    def _bar_target_end(self) -> float:
        """按当前布局算出条右端该在哪。

        静止（未选中子便签）时与胶囊完全平齐——条的左右边缘就是胶囊的
        左右边缘；选中子便签后延伸到该便签的右缘。条任何时候都不消失。
        """
        name_end = float(self.name_btn.geometry().right() + 1 - self._PILL_RADIUS)
        end = name_end
        if self._active_chip is not None and self._open:
            chip = self._active_chip
            end = float(chip.mapTo(self, QPoint(0, 0)).x() + chip.width() + 1)
        return max(end, name_end)

    def _update_bar_target(self) -> None:
        """状态变化（选中/展开收起）后启动缓动。"""
        if self._bar_end < 0.0:                 # 首次：直接就位，不做动画
            self._bar_end = self._bar_target_end()
            self.update()
            return
        self._bar_last_ms = time.monotonic() * 1000.0
        if not self._bar_timer.isActive():
            self._bar_timer.start()

    def _bar_tick(self) -> None:
        now = time.monotonic() * 1000.0
        dt = max(1.0, now - self._bar_last_ms)
        self._bar_last_ms = now
        target_end = self._bar_target_end()
        # 指数缓出：每帧走掉剩余距离的一定比例（与帧率无关），越接近越慢
        k = 1.0 - math.exp(-dt / self._BAR_EASE_TAU_MS)
        self._bar_end += (target_end - self._bar_end) * k
        if abs(target_end - self._bar_end) < 0.4:
            self._bar_end = target_end
            self._bar_timer.stop()
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        # 胶囊底：QPainter 画（QSS border-radius 被忽略），悬停时换悬停色
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._pill_hover if self._pill_hovered else self._pill_color)
        g = self.name_btn.geometry()
        painter.drawRoundedRect(QRectF(g), self._PILL_RADIUS, self._PILL_RADIUS)
        painter.end()
        super().paintEvent(event)          # 顶条在其上、子控件文字在其上
        if self._bar_color.alpha() == 0:
            return
        # 左端内缩一个圆角半径：不悬在胶囊的圆角之外
        x0 = float(self.name_btn.x() + self._PILL_RADIUS)
        end = self._bar_target_end() if self._bar_end < 0.0 else self._bar_end
        if end <= x0 + 2:
            return
        color = QColor(self._bar_color)          # 恒定全色：未选中也不消失
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        radius = self._BAR_H / 2
        painter.drawRoundedRect(
            QRectF(x0, self._bar_top(), end - x0, self._BAR_H), radius, radius
        )

    # ── 状态 ──

    def is_open(self) -> bool:
        return self._open

    def set_open(self, open_: bool, animate: bool = True, by_hover: bool = False) -> None:
        if self._open == open_:
            return
        self._open = open_
        if open_:
            self._hover_opened = by_hover
        else:
            self._hover_opened = False
            self._leave_timer.stop()
        self.setProperty("open", open_)
        _repolish(self)
        self._lay.setSpacing(4)   # 胶囊 | 子便签 | 分隔线 之间恒定小间距

        if self._anim is not None:
            self._anim.stop()
            self._anim = None

        if open_:
            self._host.setVisible(True)
            target = self._host.content_width()
            if animate and target > 0:
                self._animate_clip(self._host.clip_width(), target)
            else:
                self._host.set_clip(target)
        else:
            if animate:
                self._animate_clip(
                    self._host.clip_width(), 0, on_finished=self._host.hide)
            else:
                self._host.set_clip(0)
                self._host.setVisible(False)
        self.updateGeometry()
        self._update_bar_target()   # 展开/收起后顶部条的状态可能变化

    def _animate_clip(self, start: int, end: int, on_finished=None) -> None:
        """可视宽度动画——宽度生长即「拉出」，收拢即「推回」（内容裁剪不压缩）。"""
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(self._ANIM_MS)
        self._anim.setStartValue(int(start))
        self._anim.setEndValue(int(end))
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(
            lambda v: self._host.set_clip(int(v)))
        if on_finished is not None:
            self._anim.finished.connect(on_finished)
        self._anim.finished.connect(self._on_anim_done)
        self._anim.start()

    def _on_anim_done(self) -> None:
        """动画结束 → 重算本层与祖先层的宽度。

        嵌套抽屉展开时，父层的目标宽度是在动画开始那一刻算的，那时子抽屉
        的宽度还是 0，父层胶囊就会偏窄、把子项裁掉。这里在动画结束后补算
        （子层先到位，父层才量得准）。
        """
        self._strip.refresh_drawer_widths()

    def is_animating(self) -> bool:
        """抽屉的拉出/收拢动画是否正在进行。"""
        return (
            self._anim is not None
            and self._anim.state() == QAbstractAnimation.State.Running
        )

    def pending_growth(self) -> int:
        """拉出动画结束后本层还会再宽多少像素（动画进行中才非零）。

        用于预测「动画终点」的位置：先把目标滑到位，让视口跟着拉出一起平移，
        而不是等动画结束再跳一下。
        """
        if not self._open:
            return 0
        return max(0, self._host.content_width() - self._host.clip_width())

    def anim_remaining_ms(self) -> int:
        """当前拉出/收拢动画的剩余时长（毫秒）；无动画返回 0。

        自动滑动按这个时长走，就能和拉出同时结束。
        """
        if not self.is_animating():
            return 0
        return max(0, self._anim.duration() - self._anim.currentTime())

    def filter_wheel_with(self, watcher: QObject) -> None:
        """把抽屉内部也纳入滚轮处理范围。

        _ClipBox / 内容层是裸 QWidget，不处理滚轮也不向上传播够了，
        指针落在抽屉的空白处时滚轮会变成盲区（父层收不到）。
        """
        self._host.installEventFilter(watcher)
        self._host.inner_widget.installEventFilter(watcher)

    def refresh_width(self) -> None:
        """内容宽度变化后同步（嵌套抽屉开合导致父层内容变宽时调用）。"""
        if self._open:
            self._host.set_clip(self._host.content_width())
        self.updateGeometry()

    # ── 悬浮拉出 ──

    def eventFilter(self, obj: QObject, event) -> bool:  # type: ignore[override]
        from PySide6.QtCore import QEvent
        if obj is self.name_btn and event.type() in (
            QEvent.Type.Enter, QEvent.Type.HoverEnter,
            QEvent.Type.Leave, QEvent.Type.HoverLeave,
        ):
            hovered = event.type() in (QEvent.Type.Enter, QEvent.Type.HoverEnter)
            if self._pill_hovered != hovered:
                self._pill_hovered = hovered
                self.update()
            return False
        return super().eventFilter(obj, event)

    def enterEvent(self, event) -> None:  # type: ignore[override]
        self._leave_timer.stop()  # 回来了：撤销待收起
        if not self._open and not self._strip.drag_active():
            self._hover_timer.start(self._HOVER_MS)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._hover_timer.stop()
        # 悬浮瞥一眼打开的抽屉：鼠标离开后收起（宽限期内回来则取消）
        if self._hover_opened and not self._strip.drag_active():
            self._leave_timer.start(self._LEAVE_MS)
        super().leaveEvent(event)

    def _on_hover_timeout(self) -> None:
        if not self._open and not self._strip.drag_active():
            self._hover_open_pending = True
            self.open_requested.emit(self.rel_path)

    def _on_leave_timeout(self) -> None:
        if self._hover_opened and not self._strip.drag_active():
            self.close_requested.emit(self.rel_path)

    def clear_hover_flag(self) -> None:
        """用户在抽屉内发生了交互（点击便签）→ 抽屉转为常驻展开。"""
        self._hover_opened = False
        self._leave_timer.stop()


class _RightFadeEffect(QGraphicsEffect):
    """右侧渐隐：把控件（含子控件）渲染进临时图像，再按水平渐变把右端 alpha 压到 0。

    用于标签行右缘——被裁掉的便签不是硬切一刀，而是向右淡出到隔断处。
    原理：DestinationIn 合成一张「右端透明」的渐变遮罩，遮罩外的内容原样保留。
    """

    def __init__(self, fade_width: int = 28, parent=None) -> None:
        super().__init__(parent)
        self._fade_width = max(0, fade_width)

    def set_fade_width(self, width: int) -> None:
        width = max(0, int(width))
        if width != self._fade_width:
            self._fade_width = width
            self.update()

    def draw(self, painter: QPainter) -> None:  # type: ignore[override]
        rect = self.boundingRect()
        size = rect.size().toSize()
        fade = min(self._fade_width, size.width())
        if size.width() <= 0 or size.height() <= 0 or fade <= 0:
            self.drawSource(painter)
            return

        # 1. 把源内容画到临时图像
        src = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
        src.fill(Qt.GlobalColor.transparent)
        src_painter = QPainter(src)
        src_painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.drawSource(src_painter)
        src_painter.end()

        # 2. 渐变遮罩：fade 段之前不透明，fade 段内 alpha 线性降到 0
        mask = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
        mask.fill(Qt.GlobalColor.transparent)
        mask_painter = QPainter(mask)
        solid_w = size.width() - fade
        if solid_w > 0:
            mask_painter.fillRect(QRect(0, 0, solid_w, size.height()),
                                  QColor(0, 0, 0, 255))
        grad = QLinearGradient(solid_w, 0, size.width(), 0)
        grad.setColorAt(0.0, QColor(0, 0, 0, 255))
        grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        mask_painter.fillRect(
            QRect(solid_w, 0, fade, size.height()), grad)
        mask_painter.end()

        # 3. DestinationIn：按遮罩 alpha 裁剪源内容
        comp = QPainter(src)
        comp.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_DestinationIn)
        comp.drawImage(0, 0, mask)
        comp.end()

        painter.drawImage(rect.topLeft(), src)


class NoteStrip(QWidget):
    """标签行——根目录直系便签 chip + 文件夹抽屉的单行容器。

    - 溢出隔断：chip 保持自身宽度，放不下的被右边界裁掉（不压缩）；
      滚轮在标签行上 = 切换便签（上滚上一张、下滚下一张），
      Shift+滚轮 = 横向滚动，可滚到被裁掉的部分
    - 手风琴：同级同时只展开一个抽屉，关闭父级时收起后代
    - 按住行内任意 chip 拖动 = 移动窗口（距离阈值区分点击）
    - 拖动期间抑制悬浮拉出
    - 不触碰文件系统：结构数据（FolderNode）由 window.py 注入

    结构：NoteStrip → QScrollArea（无边框/隐藏滚动条）→ row（chip 行）
    内容层比可视区宽时溢出不压缩，靠滚动区裁剪——纯 QHBoxLayout 在空间不足
    时会把子项压到 minimumSizeHint 以下（实测复现）。
    """

    note_activated = Signal(str)        # filepath
    drawers_changed = Signal(list)      # 展开的 rel_path 列表（含祖先链）
    note_context = Signal(str, QPoint)  # filepath, 全局坐标
    folder_context = Signal(str, QPoint)
    plus_clicked = Signal()
    plus_context = Signal(QPoint)
    strip_context = Signal(QPoint)
    drag_started = Signal()
    drag_moved = Signal(QPoint)         # 相对按压点的位移
    drag_finished = Signal()

    def __init__(self, theme: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("noteStrip")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_strip_context)

        self._theme = theme
        self._open: set[str] = set()
        self._chips: dict[str, NoteChip] = {}       # filepath → chip
        self._drawers: dict[str, FolderDrawer] = {} # rel_path → drawer

        # 拖窗口状态
        self._press_global: QPoint | None = None
        self._drag_engaged = False
        self._dragging = False

        # 滚轮切便签状态：当前激活便签 + 增量累积（触控板小步累积成一格）
        self._active_fp: str = ""
        self._wheel_accum = 0

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 2, 0)  # 右侧 2px 与 chip 行内边距一致
        outer.setSpacing(0)

        # 滚动层：把 chip 行与「窗口可视宽度」解耦，溢出不压缩
        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("stripScroller")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea#stripScroller { background: transparent; border: none; }")
        vp = self._scroll.viewport()
        vp.setObjectName("stripViewport")
        vp.setAutoFillBackground(False)
        # 注意：这里必须带 objectName 选择器——裸声明等价于 `* {...}`，
        # 会把 background 规则泄漏给全部后代，覆盖掉文件夹抽屉的胶囊底色
        vp.setStyleSheet("QWidget#stripViewport { background: transparent; }")
        vp.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        vp.customContextMenuRequested.connect(self._on_strip_context)
        vp.installEventFilter(self)
        # 标签行本体也过滤：落在行内空白/边距上的滚轮同样是切换便签，不能漏
        self.installEventFilter(self)

        # chip 行（内容层，宽度由内容决定）
        self._row = QWidget()
        self._row.setObjectName("stripRow")
        self._row.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._row.customContextMenuRequested.connect(self._on_strip_context)
        self._row.installEventFilter(self)
        self._lay = QHBoxLayout(self._row)
        self._lay.setContentsMargins(8, 0, 2, 0)
        self._lay.setSpacing(4)
        self._scroll.setWidget(self._row)
        # 注意：QScrollArea.setWidget() 内部会强制 widget->setAutoFillBackground(True)，
        # 该控件随即用面板色（#F3F3F3 不透明）铺满整行——会盖掉标签行的圆角背景
        # 与文件夹胶囊色调。故必须在其之后关闭，并用限定作用域的透明规则兜底。
        self._row.setAutoFillBackground(False)
        self._row.setStyleSheet("QWidget#stripRow { background: transparent; }")

        # 右缘渐隐：被裁掉的便签向右淡出到隔断处，而不是硬切一刀
        self._fade = _RightFadeEffect(28, self)
        self._scroll.setGraphicsEffect(self._fade)
        # 平滑滚动状态
        self._scroll_anim: QVariantAnimation | None = None
        self._scroll_target = 0

        outer.addWidget(self._scroll, 1)

        # 新建入口：常驻标签行最右侧（在滚动区之外，永不被裁掉也不随滚动移动），
        # 与可滚动的便签区之间用一条竖线隔断
        self._strip_sep = QWidget()
        self._strip_sep.setObjectName("stripSep")
        self._strip_sep.setFixedSize(1, 14)
        outer.addWidget(self._strip_sep, 0, Qt.AlignmentFlag.AlignVCenter)

        self._plus = QPushButton("+")
        self._plus.setObjectName("stripPlus")
        self._plus.setCursor(Qt.CursorShape.PointingHandCursor)
        self._plus.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._plus.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._plus.setToolTip("新建便签（右键：新建文件夹）")
        self._plus.clicked.connect(self.plus_clicked.emit)
        self._plus.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._plus.customContextMenuRequested.connect(
            lambda pos: self.plus_context.emit(self._plus.mapToGlobal(pos))
        )
        self._plus.installEventFilter(self)
        outer.addWidget(self._plus, 0, Qt.AlignmentFlag.AlignVCenter)

        # 滚动范围/位置变化 → 更新右缘渐隐
        hsb = self._scroll.horizontalScrollBar()
        hsb.valueChanged.connect(self._update_fade)
        hsb.rangeChanged.connect(self._update_fade)

        self.apply_theme(theme)

    # ── 主题 ──

    def apply_theme(self, theme: dict[str, Any]) -> None:
        self._theme = theme
        self.setStyleSheet(generate_strip_qss(theme))
        tb = theme.get("tab_bar", {})
        font_px = _i(tb, "font_size", 14)
        line = round(font_px * 1.35)
        # 标签行高度需同时容纳：文字 + 上下内边距，以及文件夹胶囊
        # （胶囊比 chip 高 2px：自身 1px 上下边距）。若只按文字算，胶囊偏高的
        # 主题（小 padding_v、大字号）会把行容器撑高，导致所有 chip 一起下移。
        height = max(
            30,
            _i(tb, "padding_v", 8) * 2 + line + 2,
            line + 14,
        )
        self.setFixedHeight(height)
        # 行容器高度钉死：抽屉再高也不能撑大行容器（否则全部 chip 居中下移）
        self._row.setMaximumHeight(height)
        ts = theme.get("tab_strip", {})
        self._fade.set_fade_width(_i(ts, "fade_width", 28))
        self._update_fade()
        self._apply_fonts()
        self._apply_bar_colors()

    def _bar_colors(self) -> tuple[QColor, QColor, QColor, QColor, QColor]:
        """指示条配色：(便签未选中, 便签选中, 文件夹条, 胶囊色, 胶囊悬停色)。

        便签选中＝主题的指示色；未选中＝文字色淡化成的灰条（可用
        tab_strip.bar_idle_color 单独指定）；文件夹条与胶囊默认同指示色
        （分别可用 folder_bar_color / folder_pill_color 单独指定）。
        """
        tb = self._theme.get("tab_bar", {})
        ts = self._theme.get("tab_strip", {})
        raw_idle = ts.get("bar_idle_color")
        if raw_idle:
            idle = _qcolor(raw_idle)
        else:
            idle = _qcolor(_s(tb, "text_color", "#666666"))
            idle.setAlphaF(0.40)
        active = _qcolor(_s(tb, "active_indicator_color", "#4A90D9"))
        folder_bar = _qcolor(ts.get("folder_bar_color", _s(tb, "active_indicator_color", "#4A90D9")))
        pill = _qcolor(ts.get("folder_pill_color", _s(tb, "active_indicator_color", "#4A90D9")))
        pill_hover = _pill_hover_color(pill)
        return idle, active, folder_bar, pill, pill_hover

    def _apply_bar_colors(self) -> None:
        idle, active, folder_bar, pill, pill_hover = self._bar_colors()
        for chip in self._chips.values():
            chip.set_bar_colors(idle, active)
        for drawer in self._drawers.values():
            drawer.set_bar_color(folder_bar)
            drawer.set_pill_colors(pill, pill_hover)

    def _apply_fonts(self) -> None:
        tb = self._theme.get("tab_bar", {})
        family = _s(tb, "font_family", "Segoe UI")
        size = _i(tb, "font_size", 14)
        for chip in self._chips.values():
            chip.setFont(QFont(family, size))
        for drawer in self._drawers.values():
            drawer.name_btn.setFont(QFont(family, size))
            # 胶囊高度 = 文字高 + 3px（只比文字高 3px）；QSS 竖直 padding 为 0
            fm = drawer.name_btn.fontMetrics()
            drawer.name_btn.setFixedHeight(fm.height() + 3)
        if hasattr(self, "_plus") and self._plus is not None:
            self._plus.setFont(QFont(family, size + 1))

    # ── 结构重建 ──

    def rebuild(self, root: Any) -> None:
        """按 FolderNode 树整行重建（GUI 是文件系统投影）。"""
        # 清空旧组件（只清 widget，末尾 stretch 每次重建时重加）
        # setParent(None) 立即脱离控件树——deleteLater 是延迟删除，
        # 旧控件会带着上一次的几何继续绘制，造成重影/残留
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._chips.clear()
        self._drawers.clear()

        self._build_level(root, self._lay)
        self._open = {k for k in self._open if k in self._drawers}
        self._lay.addStretch(1)  # chip 少时靠左排列；`+` 常驻滚动区之外的右侧

        self._apply_fonts()
        self._apply_bar_colors()
        self._apply_open_states(animate=False)

    def _build_level(self, node: Any, lay: QHBoxLayout) -> None:
        for info in node.notes:
            idle, active, _folder_bar, _pill, _pill_hover = self._bar_colors()
            chip = NoteChip(info.filepath, info.filename, idle, active)
            chip.setFont(QFont(self._strip_font_family(), self._strip_font_size()))
            chip.clicked.connect(
                lambda checked=False, fp=info.filepath: self._on_chip_clicked(fp)
            )
            chip.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            chip.customContextMenuRequested.connect(
                lambda pos, fp=info.filepath, c=chip:
                    self.note_context.emit(fp, c.mapToGlobal(pos))
            )
            chip.installEventFilter(self)
            self._chips[info.filepath] = chip
            lay.addWidget(chip, 0, Qt.AlignmentFlag.AlignVCenter)

        for sub in node.folders:
            drawer = self._make_drawer(sub)
            lay.addWidget(drawer, 0, Qt.AlignmentFlag.AlignVCenter)

    def _make_drawer(self, node: Any) -> FolderDrawer:
        drawer = FolderDrawer(node.rel_path, node.name, self)
        _i, _a, _f, pill, pill_hover = self._bar_colors()
        drawer.set_pill_colors(pill, pill_hover)
        drawer.set_bar_color(_f)
        drawer.name_btn.clicked.connect(
            lambda checked=False, rel=node.rel_path: self.toggle_drawer(rel)
        )
        drawer.name_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        drawer.name_btn.customContextMenuRequested.connect(
            lambda pos, rel=node.rel_path, d=drawer:
                self.folder_context.emit(rel, d.name_btn.mapToGlobal(pos))
        )
        drawer.open_requested.connect(
            lambda rel: self.open_drawer(rel, by_hover=True))
        drawer.close_requested.connect(self.close_drawer)
        drawer.installEventFilter(self)
        drawer.name_btn.installEventFilter(self)
        drawer.filter_wheel_with(self)          # 抽屉内部不留滚轮盲区

        for info in node.notes:
            idle, active, _folder_bar, _pill, _pill_hover = self._bar_colors()
            chip = NoteChip(info.filepath, info.filename, idle, active)
            chip.clicked.connect(
                lambda checked=False, fp=info.filepath: self._on_chip_clicked(fp)
            )
            chip.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            chip.customContextMenuRequested.connect(
                lambda pos, fp=info.filepath, c=chip:
                    self.note_context.emit(fp, c.mapToGlobal(pos))
            )
            chip.installEventFilter(self)
            self._chips[info.filepath] = chip
            drawer.add_child_widget(chip)

        for sub in node.folders:
            drawer.add_child_widget(self._make_drawer(sub))

        self._drawers[node.rel_path] = drawer
        return drawer

    def _strip_font_family(self) -> str:
        return _s(self._theme.get("tab_bar", {}), "font_family", "Segoe UI")

    def _strip_font_size(self) -> int:
        return _i(self._theme.get("tab_bar", {}), "font_size", 14)

    # ── 激活与展开状态 ──

    def set_active(self, filepath: str) -> None:
        prev_fp = self._active_fp
        self._active_fp = filepath
        chip_now = self._chips.get(filepath)
        for fp, chip in self._chips.items():
            chip.set_active(fp == filepath)
        # 文件夹顶部条：只延伸给「包含选中便签」的抽屉（含嵌套的祖先）
        for drawer in self._drawers.values():
            drawer.set_active_chip(
                chip_now if chip_now is not None and drawer.isAncestorOf(chip_now)
                else None
            )
        self._collapse_drawers_left(prev_fp, filepath)
        self._reveal_active(filepath)

    def _collapse_drawers_left(self, prev_fp: str, now_fp: str) -> None:
        """选择从某个展开的分组里移出去后，把这个分组收起。

        判据是「离开」而不是「不在里面」：只有上一张还在该分组、新选的
        已经在外面时才收起。这样启动恢复的展开状态、以及「打开分组浏览、
        同时选中根目录便签」的用法都不会被误收。
        """
        if not prev_fp or prev_fp == now_fp:
            return
        prev_chip = self._chips.get(prev_fp)
        now_chip = self._chips.get(now_fp)
        if prev_chip is None or now_chip is None:
            return
        for rel, drawer in list(self._drawers.items()):
            if rel not in self._open:
                continue
            if not drawer.isAncestorOf(prev_chip):
                continue                  # 上一张也不在这个分组里 → 与本次无关
            if drawer.isAncestorOf(now_chip):
                continue                  # 新选中的仍在组内（如切到同组另一张）
            self.close_drawer(rel)

    # ── 自动滑动：让激活的便签保持可见 ──

    _REVEAL_PAD = 12          # 便签与视口边缘的留白
    _REVEAL_RETRY_MS = 120    # 布局尚未算出滚动范围时的重试间隔
    _REVEAL_VERIFY_MS = 80    # 抽屉动画结束后复核预测位置的余量

    def _reveal_active(self, filepath: str) -> None:
        """切换便签后，若它被边缘裁掉则自动把标签行滑过去。

        抽屉正在拉出时**立即**开始滑动，时长对齐抽屉动画的剩余时间——
        视口跟着拉出一起平移，而不是等动画结束再跳一下。目标位置按
        「动画终点」预测（加上祖先抽屉尚未展开的宽度）。
        """
        chip = self._chips.get(filepath)
        if chip is None:
            return
        drawer = self._innermost_drawer_of(chip)
        shift = self._pending_shift_for(drawer.rel_path) if drawer else 0
        sliding = self._max_anim_remaining_ms()

        def _run(retry: bool = False) -> None:
            if self._active_fp != filepath:
                return  # 期间又切走了，交给最新一次处理
            if not self._scroll_chip_into_view(filepath, shift, sliding) and not retry:
                # 切换紧跟 rebuild 时布局可能还没算出滚动范围，稍后重试一次
                QTimer.singleShot(self._REVEAL_RETRY_MS, lambda: _run(retry=True))
                return
            if sliding and not retry:
                # 抽屉动画结束后复核一次：嵌套补算可能让预测有偏差
                QTimer.singleShot(
                    sliding + self._REVEAL_VERIFY_MS,
                    lambda: self._verify_chip(filepath),
                )

        QTimer.singleShot(0, _run)

    def _verify_chip(self, filepath: str) -> None:
        """复核：目标仍未露出则补正一次（已可见时 _scroll_chip_into_view 不动）。"""
        if self._active_fp == filepath:
            self._scroll_chip_into_view(filepath, 0, 120)

    def _innermost_drawer_of(self, chip) -> "FolderDrawer | None":
        """包含该 chip 的最内层抽屉（不在任何抽屉里则返回 None）。"""
        found = None
        for rel, d in self._drawers.items():
            if d.isAncestorOf(chip):
                if found is None or rel.count("/") > found[0].count("/"):
                    found = (rel, d)
        return found[1] if found else None

    def _pending_shift_for(self, rel_path: str) -> int:
        """祖先抽屉仍在拉出时，该层左缘还会右移多少像素。

        动画终点位置 = 当前位置 + 这个偏移，用它预测目标，滑动才能提前开始
        并与拉出同时结束。
        """
        parts = rel_path.split("/")
        total = 0
        for i in range(1, len(parts)):          # 只算祖先，不含自身
            rel = "/".join(parts[:i])
            d = self._drawers.get(rel)
            if d is not None and d.is_open():
                total += d.pending_growth()
        return total

    def _max_anim_remaining_ms(self) -> int:
        """当前所有抽屉动画中最长的剩余时长（无动画则 0）。"""
        return max((d.anim_remaining_ms() for d in self._drawers.values()), default=0)

    def _scroll_chip_into_view(
        self, filepath: str, shift: int = 0, duration: int | None = None
    ) -> bool:
        """把指定便签 chip 滚入可视区。

        shift：预测的额外位移（抽屉动画终点位置用）。
        duration：指定滑动时长（与抽屉动画对齐）；None 用默认时长。
        返回 False 表示「当前布局还判断不了」（滚动范围未算出），调用方可重试。
        """
        chip = self._chips.get(filepath)
        if chip is None:
            return True
        vp = self._scroll.viewport()
        if vp.width() <= 0:
            return True
        hsb = self._scroll.horizontalScrollBar()
        if hsb.maximum() <= 0:
            # 内容比视口宽却还没有滚动范围 → 布局未就绪
            return self._row.sizeHint().width() <= vp.width()
        pos = chip.mapTo(vp, QPoint(0, 0))
        pad = self._REVEAL_PAD
        if pos.x() < pad:                                   # 被左缘裁掉
            target = hsb.value() + pos.x() + shift - pad
        elif pos.x() + shift + chip.width() > vp.width() - pad:   # 被右缘裁掉
            target = hsb.value() + (pos.x() + shift + chip.width() - vp.width() + pad)
        else:
            return True
        self._smooth_scroll_to(target, duration)
        return True

    # ── 滚轮切便签 ──

    # 一个滚轮「格」的累积阈值：鼠标滚轮一格是 120（角度单位）。
    # 触控板给的是像素增量且事件密集，按 1.2 折算（约 100px 行程切一张），
    # 避免手指一滑就飞过好几张便签。
    _WHEEL_STEP = 120
    _PIXEL_TO_ANGLE = 1.2

    def _wheel_steps(self, event) -> int:
        """把一次滚轮事件折算成切换步数（正数 = 下一张）。"""
        delta = event.angleDelta().y()
        if delta:
            self._wheel_accum += delta
        else:
            self._wheel_accum += event.pixelDelta().y() * self._PIXEL_TO_ANGLE
        if not self._wheel_accum:
            return 0
        steps = 0
        while abs(self._wheel_accum) >= self._WHEEL_STEP:
            steps += -1 if self._wheel_accum > 0 else 1   # 上滚 = 上一张
            self._wheel_accum -= (
                self._WHEEL_STEP if self._wheel_accum > 0 else -self._WHEEL_STEP
            )
        return steps

    def _cycle_note(self, steps: int) -> None:
        """按标签行的显示顺序切换到上/下一张便签。

        顺序取 _chips 的插入序——与 _build_level 的构建顺序一致，
        即标签行的视觉顺序（根目录便签在前，各分组便签随后）。
        分组内的便签被切到时，window 侧会展开其祖先抽屉。

        到头即停、**不绕回**：快速滚动时绕回会让人一下从末尾跳到开头，
        看起来像切错了。
        """
        order = list(self._chips)
        if not order:
            return
        if self._active_fp in order:
            cur = order.index(self._active_fp)
            i = max(0, min(len(order) - 1, cur + steps))
            if i == cur:
                return          # 已在首/尾，继续滚不再动作
        else:
            i = 0 if steps > 0 else len(order) - 1
        # 切走后的收起（含悬停展开的抽屉）统一由 set_active → _collapse_drawers_left 处理
        self.note_activated.emit(order[i])

    def _on_chip_clicked(self, filepath: str) -> None:
        """用户点选便签 = 明确交互 → 被瞥一眼打开的抽屉转为常驻展开。"""
        self._clear_hover_flags()
        self.note_activated.emit(filepath)

    # ── 横向滚动（平滑）与右缘渐隐 ──

    def _smooth_scroll_to(self, target: int, duration: int | None = None) -> None:
        """缓动到目标滚动位置——直接 setValue 是一格一跳，观感生硬。

        duration 为 None 时按距离算（短距离 90ms 起、长距离封顶 180ms）；
        传入时用指定时长——抽屉拉出时按动画剩余时长走，两者同时结束，
        视口看起来就是「跟着拉出一起平移」。
        """
        hsb = self._scroll.horizontalScrollBar()
        target = max(0, min(hsb.maximum(), int(target)))
        self._scroll_target = target
        if self._scroll_anim is not None:
            self._scroll_anim.stop()
        start = hsb.value()
        if start == target:
            return
        span = abs(target - start)
        anim = QVariantAnimation(self)
        anim.setDuration(
            max(60, int(duration)) if duration is not None
            else max(90, min(180, int(span * 0.7)))
        )
        anim.setStartValue(start)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.valueChanged.connect(lambda v: hsb.setValue(int(v)))
        anim.finished.connect(self._update_fade)
        self._scroll_anim = anim
        anim.start()

    def _update_fade(self) -> None:
        """右侧渐隐仅在有内容被裁掉时生效（滚到最右则恢复实边）。"""
        hsb = self._scroll.horizontalScrollBar()
        self._fade.setEnabled(
            hsb.maximum() > 0 and hsb.value() < hsb.maximum())

    def set_open_drawers(self, keys: list[str]) -> None:
        """设置展开集合（含祖先链归一化，不发射信号）。"""
        expanded: set[str] = set()
        for k in keys:
            if k in self._drawers:
                parts = k.split("/")
                for i in range(1, len(parts) + 1):
                    expanded.add("/".join(parts[:i]))
        self._open = expanded
        self._apply_open_states(animate=False)
        self._refresh_open_widths()

    def open_drawers(self) -> list[str]:
        return sorted(self._open)

    def toggle_drawer(self, rel_path: str) -> None:
        opening = rel_path not in self._open
        if not opening:
            self._prune(rel_path)
        else:
            self._open_with_ancestors(rel_path)
        self._apply_open_states()
        self._refresh_open_widths()
        self.drawers_changed.emit(self.open_drawers())
        if opening:
            self._align_drawer_left(rel_path)

    def close_drawer(self, rel_path: str) -> None:
        """收起指定抽屉（悬浮瞥一眼打开后的自动收起走这里）。"""
        if rel_path not in self._open:
            return
        self._prune(rel_path)
        self._apply_open_states()
        self._refresh_open_widths()
        self.drawers_changed.emit(self.open_drawers())

    def open_drawer(self, rel_path: str, by_hover: bool = False) -> None:
        """手风琴展开：收起同级兄弟，确保祖先链展开。

        by_hover=True 表示由悬浮触发（鼠标离开后自动收起）；
        点击 / 恢复状态打开时保持展开。
        """
        parts = rel_path.split("/")
        parent_parts = parts[:-1]
        for key in list(self._open):
            kp = key.split("/")
            if kp[:-1] == parent_parts and key != rel_path:
                self._prune(key)
        self._open_with_ancestors(rel_path)
        self._apply_open_states(by_hover_rel=rel_path if by_hover else None)
        self._refresh_open_widths()
        self.drawers_changed.emit(self.open_drawers())
        self._align_drawer_left(rel_path)

    # ── 抽屉展开后的贴左 ──

    def _align_drawer_left(self, rel_path: str) -> None:
        """抽屉展开后若其内容被右缘裁掉，把标签行滑到该抽屉贴左。

        悬停「瞥一眼」时最需要：抽屉向右展开，右边缘放不下时子便签会被
        裁掉，滑到贴左能看到最多的子项。内容本来就放得下则不动。

        滑动与抽屉拉出同步：立即开始、时长取动画剩余时间，
        视口跟着拉出一起左移，而不是等动画结束再跳一下。
        """
        drawer = self._drawers.get(rel_path)
        if drawer is None:
            return
        shift = self._pending_shift_for(rel_path)
        sliding = self._max_anim_remaining_ms()

        def _run(retry: bool = False) -> None:
            if rel_path not in self._open:
                return  # 期间又收起了
            if not self._scroll_drawer_left(rel_path, shift, sliding) and not retry:
                QTimer.singleShot(self._REVEAL_RETRY_MS, lambda: _run(retry=True))
                return
            if sliding and not retry:
                # 动画结束后复核一次：嵌套补算可能让预测有偏差
                QTimer.singleShot(
                    sliding + self._REVEAL_VERIFY_MS,
                    lambda: self._verify_drawer(rel_path),
                )

        QTimer.singleShot(0, _run)

    def _verify_drawer(self, rel_path: str) -> None:
        """复核：抽屉仍未整体可见则补正一次（已可见时不动）。"""
        if rel_path in self._open:
            self._scroll_drawer_left(rel_path, 0, 140)

    def _scroll_drawer_left(
        self, rel_path: str, shift: int = 0, duration: int | None = None
    ) -> bool:
        """把抽屉滑到（或保持在）视口左端；返回 False 表示布局未就绪。

        对齐目标取「最外层打开祖先」：整条链放得下时这样连父级名字一起露出来，
        否则（链比视口还宽）才对被悬停的那一层，让它的子项尽量多露出。
        shift / duration：预测位移与指定时长（见 _reveal_active 注释）。
        """
        drawer = self._drawers.get(rel_path)
        if drawer is None:
            return True
        vp = self._scroll.viewport()
        if vp.width() <= 0:
            return True
        hsb = self._scroll.horizontalScrollBar()
        if hsb.maximum() <= 0:
            # 内容比视口宽却还没有滚动范围 → 布局未就绪
            return self._row.sizeHint().width() <= vp.width()
        inset = self._lay.contentsMargins().left()
        outer_rel, outer = self._outermost_open_ancestor(rel_path)
        if outer is not None and outer.width() + shift <= vp.width() - inset:
            drawer = outer
        pos = drawer.mapTo(vp, QPoint(0, 0))
        if pos.x() >= 0 and pos.x() + shift + drawer.width() <= vp.width():
            return True                       # 整体可见：不动
        # 被任一缘裁掉 → 滑到贴左（宽度超过视口时这样露出的子项最多）
        self._smooth_scroll_to(hsb.value() + pos.x() + shift - inset, duration)
        return True

    def _outermost_open_ancestor(self, rel_path: str):
        """rel_path 自身及其祖先中，最外层的已展开抽屉。"""
        parts = rel_path.split("/")
        found = None
        for i in range(1, len(parts) + 1):
            rel = "/".join(parts[:i])
            d = self._drawers.get(rel)
            if d is not None and d.is_open():
                found = d
        return rel_path, found

    def reveal_note(self, filepath: str, group: str) -> None:
        """激活便签在其分组内时，静默展开祖先抽屉（不收兄弟）。"""
        if group and group not in ("", "."):
            parts = group.split("/")
            for i in range(1, len(parts) + 1):
                self._open.add("/".join(parts[:i]))
            self._apply_open_states()
            self._refresh_open_widths()
            self.drawers_changed.emit(self.open_drawers())

    def _open_with_ancestors(self, rel_path: str) -> None:
        parts = rel_path.split("/")
        for i in range(1, len(parts) + 1):
            self._open.add("/".join(parts[:i]))

    def _prune(self, rel_path: str) -> None:
        """收起抽屉及其全部后代。"""
        self._open.discard(rel_path)
        prefix = rel_path + "/"
        for key in list(self._open):
            if key.startswith(prefix):
                self._open.discard(key)

    def _apply_open_states(
        self, animate: bool = True, by_hover_rel: str | None = None
    ) -> None:
        for rel, drawer in self._drawers.items():
            drawer.set_open(
                rel in self._open,
                animate=animate,
                by_hover=(rel == by_hover_rel),
            )

    def _clear_hover_flags(self) -> None:
        """用户在抽屉内点击便签 → 该抽屉转为常驻展开（不再随鼠标离开收起）。"""
        for drawer in self._drawers.values():
            drawer.clear_hover_flag()

    def _refresh_open_widths(self) -> None:
        """嵌套抽屉开合后，自内向外同步各层可视宽度。"""
        for rel in sorted(self._drawers, key=lambda k: -k.count("/")):
            self._drawers[rel].refresh_width()

    def refresh_drawer_widths(self) -> None:
        """供抽屉动画结束时回调——重算各层宽度并同步右缘渐隐。"""
        self._refresh_open_widths()
        self._update_fade()

    # ── 拖窗口手势（按住任意 chip 拖动 = 移动窗口）──

    def drag_active(self) -> bool:
        return self._dragging or self._press_global is not None

    def eventFilter(self, obj: QObject, event) -> bool:  # type: ignore[override]
        from PySide6.QtCore import QEvent

        etype = event.type()

        # ── 滚轮 ──
        # 默认：切换便签（上滚 = 上一张，下滚 = 下一张，首尾循环）
        # Shift+滚轮：横向滚动标签行（够到被右缘裁掉的便签）
        if etype == QEvent.Type.Wheel:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                hsb = self._scroll.horizontalScrollBar()
                if hsb.maximum() <= 0:
                    return False  # 无溢出：不拦截
                px = event.pixelDelta().y() or event.pixelDelta().x()
                delta = px if px else (event.angleDelta().y() or event.angleDelta().x())
                base = self._scroll_target if self._scroll_anim is not None else hsb.value()
                # 滚轮向下 → 内容左移（向右滚动），与常见横向滚动方向一致
                self._smooth_scroll_to(base - delta)
                return True
            steps = self._wheel_steps(event)
            if steps:
                self._cycle_note(steps)
            return True  # 吞掉——不让便签内容跟着滚（含累积未满一格时）

        if etype == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._drag_engaged = False
            return False  # 不拦截——chip 正常处理按压

        if etype == QEvent.Type.MouseMove and self._press_global is not None:
            if event.buttons() & Qt.MouseButton.LeftButton:
                gp = event.globalPosition().toPoint()
                if not self._drag_engaged:
                    dist = (gp - self._press_global).manhattanLength()
                    if dist >= QApplication.startDragDistance():
                        self._drag_engaged = True
                        self._dragging = True
                        self.setCursor(Qt.CursorShape.ClosedHandCursor)
                        self.drag_started.emit()
                if self._drag_engaged:
                    self.drag_moved.emit(gp - self._press_global)
                    return True
            return False

        if etype == QEvent.Type.MouseButtonRelease:
            engaged = self._drag_engaged
            self._press_global = None
            self._drag_engaged = False
            self._dragging = False
            if engaged:
                self.setCursor(Qt.CursorShape.ArrowCursor)
                self.drag_finished.emit()
                return True  # 吞掉释放——防止触发 chip 点击
            return False

        return False

    def _on_strip_context(self, pos: QPoint) -> None:
        # 只在空白处（非子组件）响应
        child = self.childAt(pos)
        if child is None or child is self:
            self.strip_context.emit(self.mapToGlobal(pos))
