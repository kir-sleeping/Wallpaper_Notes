"""设置对话框 — 全局快捷键 + 样式配置。

职责：
1. 提供修改全局快捷键的 UI
2. 提供修改 theme.json 中所有样式参数的 UI
3. 通过 get_result() 返回新的快捷键设置和完整的样式字典

边界：
- 纯 UI，无业务逻辑
- 不直接访问 config / hotkey / theme 的持久化模块
- 样式字段未在 UI 中展示的部分，从 theme 快照原样保留
"""

from __future__ import annotations

import copy
import re
import shutil
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QDoubleSpinBox,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

# ── 常用字体列表 ──────────────────────────────────────────────

_FONT_OPTIONS = [
    "Microsoft YaHei",
    "Microsoft YaHei UI",
    "Segoe UI",
    "PingFang SC",
    "Source Han Sans SC",
    "Source Han Serif SC",
    "Noto Sans SC",
    "Noto Serif SC",
    "SimSun",
    "SimHei",
    "KaiTi",
    "FangSong",
    "STKaiti",
    "STFangSong",
    "YouYuan",
    "LiSu",
    "DengXian",
    "Cascadia Code",
    "Consolas",
    "monospace",
    "sans-serif",
]

_KEY_OPTIONS = [
    *[chr(i) for i in range(ord("A"), ord("Z") + 1)],
    *[str(i) for i in range(10)],
    *[f"F{i}" for i in range(1, 13)],
    "Space",
    "Tab",
    "Escape",
    "Enter",
    "Backspace",
    "Delete",
]


# ═══════════════════════════════════════════════════════════════
# CSS 颜色解析（QColor 对 rgba(r,g,b,a) 中的 float alpha 解析有误）
# ═══════════════════════════════════════════════════════════════

_RGBA_RE = re.compile(r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)", re.IGNORECASE)
_RGB_RE = re.compile(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", re.IGNORECASE)


def _css_to_qcolor(css: str) -> QColor:
    """安全解析 CSS 颜色字符串，正确处理 rgba 中的 float alpha。"""
    css = css.strip()
    m = _RGBA_RE.match(css)
    if m:
        r, g, b, a = int(m.group(1)), int(m.group(2)), int(m.group(3)), float(m.group(4))
        return QColor(r, g, b, int(round(a * 255)))
    m = _RGB_RE.match(css)
    if m:
        r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return QColor(r, g, b)
    # 回退：transparent、#hex、命名颜色等
    return QColor(css)


# ═══════════════════════════════════════════════════════════════
# ColorButton — 可点击打开颜色选择器的色块按钮
# ═══════════════════════════════════════════════════════════════

class ColorButton(QPushButton):
    """可点击打开颜色选择器的色块按钮。"""

    def __init__(
        self, initial: str, title: str = "选择颜色", parent=None
    ) -> None:
        super().__init__(parent)
        self._title = title
        self._color = QColor()
        self.setFixedSize(28, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("点击选择颜色")
        self.set_color(initial)
        self.clicked.connect(self._pick_color)

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(
            self._color,
            self,
            self._title,
            QColorDialog.ShowAlphaChannel,
        )
        if color.isValid():
            self.set_color_from_qcolor(color)

    def set_color(self, css_color: str) -> None:
        """通过 CSS 颜色字符串设置颜色。"""
        self._color = _css_to_qcolor(css_color)
        self._update_swatch()

    def set_color_from_qcolor(self, color: QColor) -> None:
        self._color = QColor(color)
        self._update_swatch()

    def _update_swatch(self) -> None:
        self.setStyleSheet(
            f"background-color: {self._color.name()}; "
            f"border: 1px solid #888; "
            f"border-radius: 3px;"
        )

    def get_qcolor(self) -> QColor:
        return QColor(self._color)

    def get_css_color(self) -> str:
        """返回 CSS 颜色字符串。
        
        - 有透明度 (>254)：rgba(r,g,b,a) 格式
        - 不透明：#RRGGBB 格式
        """
        c = self._color
        if c.alpha() < 255:
            return (
                f"rgba({c.red()},{c.green()},{c.blue()},{c.alphaF():.2f})"
            )
        return c.name()


# ═══════════════════════════════════════════════════════════════
# ColorAlphaWidget — 颜色选取 + 透明度滑块组合
# ═══════════════════════════════════════════════════════════════

class ColorAlphaWidget(QWidget):
    """颜色选取按钮 + 透明度百分比滑块。

    with_alpha=False 时隐藏滑块（纯色字段使用）。
    """

    def __init__(
        self,
        initial: str,
        title: str = "选择颜色",
        with_alpha: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._with_alpha = with_alpha
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._btn = ColorButton(initial, title, self)
        layout.addWidget(self._btn)

        self._alpha_label = QLabel("", self)
        self._alpha_label.setFixedWidth(30)

        self._alpha_slider = QSlider(Qt.Horizontal, self)
        self._alpha_slider.setRange(0, 100)
        self._alpha_slider.setFixedWidth(80)
        self._alpha_slider.setToolTip("透明度 (0=不透明, 100=全透)")

        qc = _css_to_qcolor(initial)
        # 透明度 = 1 - alpha（0=不透明, 100=全透明）
        trans_val = int(round((1.0 - qc.alphaF()) * 100))
        self._alpha_slider.setValue(trans_val)
        self._alpha_label.setText(f"{trans_val}%")

        if not with_alpha:
            self._alpha_slider.setVisible(False)
            self._alpha_label.setVisible(False)

        layout.addWidget(self._alpha_slider)
        layout.addWidget(self._alpha_label)

        self._alpha_slider.valueChanged.connect(self._on_alpha_changed)

    def _on_alpha_changed(self, value: int) -> None:
        self._alpha_label.setText(f"{value}%")
        qc = self._btn.get_qcolor()
        # 透明度 0=不透明, 100=全透明 → alpha = 1 - 透明度
        qc.setAlphaF(1.0 - value / 100.0)
        self._btn.set_color_from_qcolor(qc)

    def get_css_color(self) -> str:
        return self._btn.get_css_color()

    def set_color(self, css_color: str) -> None:
        self._btn.set_color(css_color)
        if self._with_alpha:
            qc = _css_to_qcolor(css_color)
            trans_val = int(round((1.0 - qc.alphaF()) * 100))
            self._alpha_slider.setValue(trans_val)
            self._alpha_label.setText(f"{trans_val}%")


# ═══════════════════════════════════════════════════════════════
# SettingsDialog
# ═══════════════════════════════════════════════════════════════

class SettingsDialog(QDialog):
    """设置对话框 — 快捷键 + 样式配置。"""

    applied = Signal(object)  # 点击「应用」时发出，携带当前 theme dict

    def __init__(
        self,
        current_modifiers: list[str],
        current_key: str,
        theme: dict[str, Any],
        autostart: bool = False,
        fonts_dir: str | Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置 - Wallpaper_Notes")
        self.setMinimumWidth(420)
        self.setMinimumHeight(480)
        self.resize(420, 560)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowContextHelpButtonHint
        )

        self._theme = theme
        self._autostart = autostart
        self._fonts_dir = fonts_dir
        self._custom_font_families: set[str] = set()
        self._font_combos: list[QComboBox] = []

        # 加载自定义字体
        if fonts_dir:
            self._load_custom_fonts()

        # ── 滚动区域 ──
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        content = QWidget()
        self._form = QVBoxLayout(content)
        self._form.setSpacing(10)

        self._form.addWidget(self._build_hotkey_group(
            current_modifiers, current_key
        ))
        self._form.addWidget(self._build_window_group())
        self._form.addWidget(self._build_tab_bar_group())
        self._form.addWidget(self._build_tab_strip_group())
        self._form.addWidget(self._build_content_group())
        self._form.addWidget(self._build_font_mgmt_group())
        self._form.addWidget(self._build_editor_group())
        self._form.addWidget(self._build_scrollbar_group())
        self._form.addStretch()

        scroll.setWidget(content)

        # ── 对话框主布局 ──
        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Apply
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        # Apply 单独连接，不经过 clicked 通用信号（避免 role 判断出错）
        apply_btn = buttons.button(QDialogButtonBox.StandardButton.Apply)
        if apply_btn is not None:
            apply_btn.clicked.connect(self._on_apply)
        main_layout.addWidget(buttons)

    # ═════════════════════════════════════════════════════════
    # UI 构建辅助
    # ═════════════════════════════════════════════════════════

    def _add_color_row(
        self,
        parent,
        label: str,
        css_color: str,
        with_alpha: bool = True,
        title: str = "",
    ) -> ColorAlphaWidget:
        """颜色选取行。"""
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        row.addStretch()
        w = ColorAlphaWidget(css_color, title or label, with_alpha)
        row.addWidget(w)
        parent.addLayout(row)
        return w

    def _add_spin_row(
        self,
        parent,
        label: str,
        value: int,
        min_v: int = 0,
        max_v: int = 100,
        suffix: str = "px",
    ) -> QSpinBox:
        """数值行。"""
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        row.addStretch()
        sb = QSpinBox()
        sb.setRange(min_v, max_v)
        sb.setValue(value)
        sb.setSuffix(suffix)
        row.addWidget(sb)
        parent.addLayout(row)
        return sb

    def _add_double_spin_row(
        self,
        parent,
        label: str,
        value: float,
        min_v: float = 0.0,
        max_v: float = 1.0,
        step: float = 0.01,
    ) -> QDoubleSpinBox:
        """浮点数数值行。"""
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        row.addStretch()
        sb = QDoubleSpinBox()
        sb.setRange(min_v, max_v)
        sb.setSingleStep(step)
        sb.setValue(value)
        row.addWidget(sb)
        parent.addLayout(row)
        return sb

    def _add_font_row(
        self, parent, label: str, current_font: str
    ) -> QComboBox:
        """字体选单行（可编辑，支持自定义输入+自定义字体）。"""
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        row.addStretch()
        cb = QComboBox()
        cb.setEditable(True)
        cb.addItems(_FONT_OPTIONS)
        # 添加已注册的自定义字体
        for family in sorted(self._custom_font_families):
            if cb.findText(family) < 0:
                cb.addItem(family)
        idx = cb.findText(current_font)
        if idx >= 0:
            cb.setCurrentIndex(idx)
        else:
            cb.setEditText(current_font)
        cb.setMinimumWidth(150)
        self._font_combos.append(cb)
        row.addWidget(cb)
        parent.addLayout(row)
        return cb

    # ═════════════════════════════════════════════════════════
    # 各组构建
    # ═════════════════════════════════════════════════════════

    def _build_hotkey_group(
        self, modifiers: list[str], key: str
    ) -> QGroupBox:
        g = QGroupBox("全局快捷键")
        gl = QVBoxLayout(g)
        gl.addWidget(QLabel("唤出便签窗口："))

        mod_row = QHBoxLayout()
        mod_row.addWidget(QLabel("组合键："))
        self._cb_ctrl = QCheckBox("Ctrl")
        self._cb_shift = QCheckBox("Shift")
        self._cb_alt = QCheckBox("Alt")
        self._cb_win = QCheckBox("Win")

        ml = [m.lower() for m in modifiers]
        self._cb_ctrl.setChecked("ctrl" in ml)
        self._cb_shift.setChecked("shift" in ml)
        self._cb_alt.setChecked("alt" in ml)
        self._cb_win.setChecked("win" in ml)
        mod_row.addWidget(self._cb_ctrl)
        mod_row.addWidget(self._cb_shift)
        mod_row.addWidget(self._cb_alt)
        mod_row.addWidget(self._cb_win)
        mod_row.addStretch()
        gl.addLayout(mod_row)

        key_row = QHBoxLayout()
        key_row.addWidget(QLabel("按键："))
        self._key_combo = QComboBox()
        self._key_combo.addItems(_KEY_OPTIONS)
        idx = self._key_combo.findText(key.capitalize())
        if idx >= 0:
            self._key_combo.setCurrentIndex(idx)
        key_row.addWidget(self._key_combo)
        key_row.addStretch()
        gl.addLayout(key_row)

        # ── 开机自启 ──
        gl.addSpacing(6)
        self._cb_autostart = QCheckBox("开机自启")
        self._cb_autostart.setChecked(self._autostart)
        gl.addWidget(self._cb_autostart)
        return g

    def _build_window_group(self) -> QGroupBox:
        w = self._theme.get("window", {})
        g = QGroupBox("窗口样式")
        gl = QVBoxLayout(g)
        self._win_radius = self._add_spin_row(
            gl, "圆角：", w.get("border_radius", 12), 0, 50
        )
        return g

    def _build_tab_bar_group(self) -> QGroupBox:
        tb = self._theme.get("tab_bar", {})
        g = QGroupBox("标签栏")
        gl = QVBoxLayout(g)
        self._tab_bg = self._add_color_row(
            gl,
            "背景色：",
            tb.get("background_color", "rgba(245,245,245,0.9)"),
        )
        self._tab_color = self._add_color_row(
            gl,
            "文字色：",
            tb.get("text_color", "#666666"),
            with_alpha=False,
        )
        self._tab_active = self._add_color_row(
            gl,
            "选中色：",
            tb.get("active_text_color", "#333333"),
            with_alpha=False,
        )
        self._tab_indicator = self._add_color_row(
            gl,
            "指示色：",
            tb.get("active_indicator_color", "#4A90D9"),
            with_alpha=False,
        )
        self._tab_font = self._add_font_row(
            gl, "字体：", tb.get("font_family", "Microsoft YaHei")
        )
        self._tab_size = self._add_spin_row(
            gl, "字号：", tb.get("font_size", 14), 8, 40, suffix="pt"
        )
        self._tab_radius_top = self._add_spin_row(
            gl, "上圆角：", tb.get("radius_top", 0), 0, 30, suffix="px"
        )
        return g

    def _build_tab_strip_group(self) -> QGroupBox:
        """分组抽屉（文件夹胶囊）配色。"""
        ts = self._theme.get("tab_strip", {})
        tb = self._theme.get("tab_bar", {})   # 文件夹色的默认值取自标签栏
        g = QGroupBox("分组抽屉")
        gl = QVBoxLayout(g)
        self._folder_pill = self._add_color_row(
            gl,
            "胶囊颜色：",
            ts.get("folder_pill_color", tb.get("active_indicator_color", "#4A90D9")),
            with_alpha=False,
        )
        self._folder_pill_text = self._add_color_row(
            gl,
            "胶囊文字色：",
            ts.get("folder_pill_text_color", "#ffffff"),
            with_alpha=False,
        )
        self._folder_sep = self._add_color_row(
            gl, "右缘分隔线：", ts.get("separator_color", "rgba(0,0,0,0.16)"),
        )
        self._folder_bar = self._add_color_row(
            gl,
            "文件夹条颜色：",
            ts.get(
                "folder_bar_color",
                tb.get("active_indicator_color", "#4A90D9"),
            ),
        )
        self._plus_color = self._add_color_row(
            gl, "新建(+)色：", ts.get("plus_color", "rgba(0,0,0,0.30)"),
        )
        self._fade_width = self._add_spin_row(
            gl, "右缘渐隐：", ts.get("fade_width", 28), 0, 80, suffix="px"
        )
        return g

    def _build_content_group(self) -> QGroupBox:
        c = self._theme.get("content", {})
        g = QGroupBox("内容区")
        gl = QVBoxLayout(g)
        self._content_bg = self._add_color_row(
            gl,
            "背景色：",
            c.get("background_color", "transparent"),
        )
        self._content_color = self._add_color_row(
            gl,
            "文字色：",
            c.get("text_color", "#333333"),
            with_alpha=False,
        )
        self._heading_color = self._add_color_row(
            gl,
            "标题色：",
            c.get("heading_color", "#aaddff"),
            with_alpha=False,
        )
        self._heading_font = self._add_font_row(
            gl, "标题字体：", c.get("heading_font_family", self._get_content_font(c))
        )
        self._content_font = self._add_font_row(
            gl, "字体：", c.get("font_family", "Microsoft YaHei")
        )
        self._content_size = self._add_spin_row(
            gl, "字号：", c.get("font_size", 16), 8, 40, suffix="pt"
        )
        # ── 内容框边框 ──
        sep = QLabel("内容框边框")
        sep.setStyleSheet(
            "font-weight: bold; color: #888; margin-top: 8px;"
        )
        gl.addWidget(sep)
        self._pane_bc = self._add_color_row(
            gl,
            "边框色：",
            c.get("pane_border_color", "rgba(200,200,200,0.5)"),
        )
        self._pane_bw = self._add_spin_row(
            gl, "边框宽：", c.get("pane_border_width", 1), 0, 10
        )
        self._pane_br = self._add_spin_row(
            gl, "下圆角：", c.get("pane_border_radius", 4), 0, 50
        )

        # ── 内容效果 ──
        sep2 = QLabel("视觉效果")
        sep2.setStyleSheet(
            "font-weight: bold; color: #888; margin-top: 8px;"
        )
        gl.addWidget(sep2)
        self._enable_glass = QCheckBox("玻璃效果")
        self._enable_glass.setChecked(c.get("enable_glass", False))
        gl.addWidget(self._enable_glass)
        self._glass_opacity = self._add_double_spin_row(
            gl,
            "玻璃透明度：",
            c.get("glass_opacity", 0.10),
            0.00,
            0.30,
            0.01,
        )
        self._enable_frost = QCheckBox("磨砂质感")
        self._enable_frost.setChecked(c.get("enable_frost", False))
        gl.addWidget(self._enable_frost)
        self._frost_intensity = self._add_double_spin_row(
            gl,
            "磨砂强度：",
            c.get("frost_intensity", 0.20),
            0.00,
            0.50,
            0.01,
        )
        # 颗粒大小行
        row_grain = QHBoxLayout()
        row_grain.addWidget(QLabel("颗粒大小："))
        self._frost_grain = QComboBox()
        self._frost_grain.addItems(["小", "中", "大"])
        self._frost_grain.setCurrentIndex(c.get("frost_grain", 1) - 1)
        row_grain.addWidget(self._frost_grain)
        row_grain.addStretch()
        gl.addLayout(row_grain)
        self._enable_glow = QCheckBox("文字发光")
        self._enable_glow.setChecked(c.get("enable_glow", False))
        gl.addWidget(self._enable_glow)
        return g

    def _build_editor_group(self) -> QGroupBox:
        e = self._theme.get("editor", {})
        g = QGroupBox("编辑器")
        gl = QVBoxLayout(g)
        self._editor_bg = self._add_color_row(
            gl,
            "背景色：",
            e.get("background_color", "#FAFAFA"),
            with_alpha=False,
        )
        self._editor_color = self._add_color_row(
            gl,
            "文字色：",
            e.get("text_color", "#333333"),
            with_alpha=False,
        )
        self._editor_caret = self._add_color_row(
            gl,
            "光标色：",
            e.get("caret_color", "#4A90D9"),
            with_alpha=False,
        )
        self._editor_font = self._add_font_row(
            gl,
            "字体：",
            e.get("font_family", "Cascadia Code, Consolas, monospace"),
        )
        self._editor_size = self._add_spin_row(
            gl, "字号：", e.get("font_size", 15), 8, 40, suffix="pt"
        )
        return g

    def _build_scrollbar_group(self) -> QGroupBox:
        sb = self._theme.get("scrollbar", {})
        g = QGroupBox("滚动条")
        gl = QVBoxLayout(g)
        self._scroll_width = self._add_spin_row(
            gl, "宽度：", sb.get("width", 6), 2, 20
        )
        self._scroll_handle = self._add_color_row(
            gl,
            "把手色：",
            sb.get("handle_color", "rgba(0,0,0,0.2)"),
        )
        self._scroll_hover = self._add_color_row(
            gl,
            "悬停色：",
            sb.get("handle_hover_color", "rgba(0,0,0,0.4)"),
        )
        self._scroll_track = self._add_color_row(
            gl,
            "轨道色：",
            sb.get("track_color", "transparent"),
        )
        return g

    # ═════════════════════════════════════════════════════════
    # 字体管理
    # ═════════════════════════════════════════════════════════

    def _get_content_font(self, content: dict[str, Any]) -> str:
        """返回 content 节的默认字体，用作标题字体的 fallback。"""
        return str(content.get("font_family", "Microsoft YaHei"))

    def _load_custom_fonts(self) -> None:
        """扫描 fonts/ 目录，注册所有 .ttf/.otf 字体到 QFontDatabase。"""
        fonts_dir = Path(self._fonts_dir)
        if not fonts_dir.is_dir():
            return
        for ext in ("*.ttf", "*.otf"):
            for font_file in fonts_dir.glob(ext):
                families = QFontDatabase.addApplicationFont(str(font_file))
                if families != -1:
                    for family in families:
                        self._custom_font_families.add(family)

    def _refresh_font_combos(self) -> None:
        """将新添加的自定义字体同步到所有字号选单。"""
        for cb in self._font_combos:
            for family in sorted(self._custom_font_families):
                if cb.findText(family) < 0:
                    cb.addItem(family)

    def _build_font_mgmt_group(self) -> QGroupBox:
        """字体管理分组：显示已添加字体 + 添加按钮。"""
        g = QGroupBox("字体管理")
        gl = QVBoxLayout(g)

        # 已添加字体列表
        if self._fonts_dir:
            fonts_dir = Path(self._fonts_dir)
            if fonts_dir.is_dir():
                custom_files = sorted(
                    f.name for f in fonts_dir.glob("*.ttf")
                ) + sorted(f.name for f in fonts_dir.glob("*.otf"))
                if custom_files:
                    gl.addWidget(QLabel("已添加："))
                    for fname in custom_files:
                        lbl = QLabel(f"  • {fname}")
                        lbl.setStyleSheet("color: #888; font-size: 12px;")
                        gl.addWidget(lbl)
                else:
                    gl.addWidget(QLabel("尚未添加自定义字体"))
        else:
            gl.addWidget(QLabel("字体目录不可用"))

        gl.addSpacing(6)
        add_btn = QPushButton("添加字体...")
        add_btn.clicked.connect(self._on_add_font)
        gl.addWidget(add_btn)

        gl.addWidget(QLabel("支持 .ttf / .otf 格式，添加后自动注册到应用"))
        return g

    def _on_add_font(self) -> None:
        """打开文件对话框选择字体文件，复制到 fonts/ 并注册。"""
        if not self._fonts_dir:
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择字体文件", "", "字体文件 (*.ttf *.otf)"
        )
        if not file_path:
            return

        fonts_dir = Path(self._fonts_dir)
        fonts_dir.mkdir(parents=True, exist_ok=True)

        src = Path(file_path)
        dst = fonts_dir / src.name

        # 若文件不在 fonts/ 中则复制
        if not dst.exists() or dst.resolve() != src.resolve():
            shutil.copy2(str(src), str(dst))

        # 注册到 QFontDatabase
        families = QFontDatabase.addApplicationFont(str(dst))
        if families == -1 or not families:
            if dst.exists():
                dst.unlink()  # 注册失败时删除复制的文件
            QMessageBox.warning(
                self, "字体错误",
                f"无法加载字体文件“{src.name}”。\n请确认文件格式正确。"
            )
            return

        for family in families:
            self._custom_font_families.add(family)

        # 刷新所有字体下拉列表
        self._refresh_font_combos()

    # ═════════════════════════════════════════════════════════
    # 按钮事件
    # ═════════════════════════════════════════════════════════

    def _on_apply(self) -> None:
        """处理 Apply 按钮（保存+预览，不关闭对话框）。"""
        _, _, theme, _ = self.get_result()
        self.applied.emit(theme)

    # ═════════════════════════════════════════════════════════
    # 读取结果
    # ═════════════════════════════════════════════════════════

    def get_result(self) -> tuple[list[str], str, dict[str, Any], bool]:
        """返回 (modifiers, key, theme_dict, autostart)。"""
        # ── 快捷键 ──
        modifiers: list[str] = []
        if self._cb_ctrl.isChecked():
            modifiers.append("ctrl")
        if self._cb_shift.isChecked():
            modifiers.append("shift")
        if self._cb_alt.isChecked():
            modifiers.append("alt")
        if self._cb_win.isChecked():
            modifiers.append("win")
        key = self._key_combo.currentText().lower()

        # ── 开机自启 ──
        autostart = self._cb_autostart.isChecked()

        # ── 主题：深拷贝原结构 → 覆盖 UI 控制的字段 ──
        theme = copy.deepcopy(self._theme)

        theme.setdefault("window", {})
        theme["window"]["border_radius"] = self._win_radius.value()

        theme.setdefault("tab_bar", {})
        theme["tab_bar"]["background_color"] = self._tab_bg.get_css_color()
        theme["tab_bar"]["text_color"] = self._tab_color.get_css_color()
        theme["tab_bar"]["active_text_color"] = (
            self._tab_active.get_css_color()
        )
        theme["tab_bar"]["active_indicator_color"] = (
            self._tab_indicator.get_css_color()
        )
        theme["tab_bar"]["font_family"] = self._tab_font.currentText()
        theme["tab_bar"]["font_size"] = self._tab_size.value()
        theme["tab_bar"]["radius_top"] = self._tab_radius_top.value()

        theme.setdefault("tab_strip", {})
        theme["tab_strip"]["folder_pill_color"] = self._folder_pill.get_css_color()
        theme["tab_strip"]["folder_pill_text_color"] = (
            self._folder_pill_text.get_css_color()
        )
        theme["tab_strip"]["separator_color"] = self._folder_sep.get_css_color()
        theme["tab_strip"]["folder_bar_color"] = self._folder_bar.get_css_color()
        # 文字色已并入胶囊（胶囊文字色）、子项区已无淡底，清理历史键
        for legacy in ("folder_text_color", "folder_active_text_color",
                       "folder_bg", "folder_bg_open", "folder_bg_hover",
                       "folder_bg_open_hover"):
            theme["tab_strip"].pop(legacy, None)
        theme["tab_strip"]["plus_color"] = self._plus_color.get_css_color()
        theme["tab_strip"]["fade_width"] = self._fade_width.value()

        theme.setdefault("content", {})
        theme["content"]["background_color"] = (
            self._content_bg.get_css_color()
        )
        theme["content"]["text_color"] = self._content_color.get_css_color()
        theme["content"]["heading_color"] = self._heading_color.get_css_color()
        theme["content"]["heading_font_family"] = self._heading_font.currentText()
        theme["content"]["font_family"] = self._content_font.currentText()
        theme["content"]["font_size"] = self._content_size.value()
        theme["content"]["pane_border_color"] = (
            self._pane_bc.get_css_color()
        )
        theme["content"]["pane_border_width"] = self._pane_bw.value()
        theme["content"]["pane_border_radius"] = self._pane_br.value()
        theme["content"]["enable_glass"] = self._enable_glass.isChecked()
        theme["content"]["glass_opacity"] = self._glass_opacity.value()
        theme["content"]["enable_frost"] = self._enable_frost.isChecked()
        theme["content"]["frost_intensity"] = self._frost_intensity.value()
        theme["content"]["frost_grain"] = self._frost_grain.currentIndex() + 1
        theme["content"]["enable_glow"] = self._enable_glow.isChecked()

        theme.setdefault("editor", {})
        theme["editor"]["background_color"] = (
            self._editor_bg.get_css_color()
        )
        theme["editor"]["text_color"] = self._editor_color.get_css_color()
        theme["editor"]["caret_color"] = (
            self._editor_caret.get_css_color()
        )
        theme["editor"]["font_family"] = self._editor_font.currentText()
        theme["editor"]["font_size"] = self._editor_size.value()

        theme.setdefault("scrollbar", {})
        theme["scrollbar"]["width"] = self._scroll_width.value()
        theme["scrollbar"]["handle_color"] = (
            self._scroll_handle.get_css_color()
        )
        theme["scrollbar"]["handle_hover_color"] = (
            self._scroll_hover.get_css_color()
        )
        theme["scrollbar"]["track_color"] = (
            self._scroll_track.get_css_color()
        )

        return modifiers, key, theme, autostart
