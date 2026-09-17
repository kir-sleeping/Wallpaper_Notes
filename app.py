"""应用控制器 — 模块接线与生命周期管理。

职责：
1. 按顺序创建所有模块（Config → Window → NotesManager → Hotkey → Tray）
2. 连接所有信号（NotesManager ↔ Window、Hotkey → Window、Tray → App）
3. 实现回调函数，让 Window 通过回调触发 NotesManager 操作
4. 管理应用启动和退出
"""

from __future__ import annotations

import ctypes
from pathlib import Path
from typing import Any

from PySide6.QtGui import QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication

from config import Config
from hotkey import HotkeyManager
from notes_manager import NotesManager
from tray import TrayManager
from window import WallpaperWindow


class App:
    """主应用控制器。"""

    def __init__(
        self,
        notes_dir: str | Path,
        config_path: str | Path,
        theme_path: str | Path,
    ) -> None:
        # ── Windows 任务栏标识（分离 python.exe 图标和名称）──
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "WallpaperNotes.App"
        )

        # ── 配置 ──
        self._config = Config(config_path, theme_path)
        _, self._theme = self._config.load()

        # ── 同步开机自启（确保注册表与 config 一致）──
        self._config.set_autostart(self._config.is_autostart_enabled())

        # ── 加载自定义字体 ──
        self._app_root = Path(config_path).resolve().parent
        self._fonts_dir = self._app_root / "fonts"
        self._load_custom_fonts()

        # ── 回调表 ──
        self._callbacks: dict[str, Any] = {
            "on_save_content": self._on_save_content,
            "on_create_note": self._on_create_note,
            "on_delete_note": self._on_delete_note,
            "on_rename_note": self._on_rename_note,
            "on_move_note": self._on_move_note,
            "on_create_folder": self._on_create_folder,
            "on_rename_folder": self._on_rename_folder,
            "on_delete_folder": self._on_delete_folder,
            "get_tree": lambda: self._notes.scan_tree(),
        }

        # ── 窗口（最先创建，后续模块需要其 HWND）──
        self._window = WallpaperWindow(self._callbacks, self._config, self._theme)

        # ── 便签数据管理器 ──
        self._notes = NotesManager(notes_dir)
        self._notes.note_changed.connect(self._window.refresh_current_tab)
        self._notes.note_added.connect(self._window.add_tab)
        self._notes.note_deleted.connect(self._window.remove_tab)
        self._notes.note_moved.connect(self._window.on_note_moved)
        self._notes.folder_added.connect(self._window.on_folder_added)
        self._notes.folder_renamed.connect(self._window.on_folder_renamed)
        self._notes.folder_deleted.connect(self._window.on_folder_deleted)

        # ── 全局热键 ──
        modifiers, key = self._config.get_hotkey()
        self._hotkey = HotkeyManager(modifiers, key)
        # 热键 ID 传给窗口，让窗口在 nativeEvent 中直接处理 WM_HOTKEY
        # （不再经过信号连接，确保窗口过程内持有前台权限）
        self._window.set_hotkey_id(self._hotkey.hotkey_id)

        # ── 应用图标 ──
        app_icon = QIcon(r"D:\app1111\Wallpaper_Notes\note_app_icon_final.ico")
        self._window.setWindowIcon(app_icon)

        # ── 托盘 ──
        self._tray = TrayManager(self._window, app_icon)
        self._tray.exit_requested.connect(self._quit)
        self._tray.settings_requested.connect(self._on_settings_requested)
        self._tray.edit_requested.connect(self._window.bring_to_front_and_edit)

        # ── 确保托盘后退出（关闭窗口不退出）──
        QApplication.setQuitOnLastWindowClosed(False)

        # ── 加载初始便签 + 启动文件监听 ──
        self._load_initial_notes()
        self._notes.start_watching()
        # 热键注册放到 run() 中 window.show() 之后执行
        # self._hotkey.register()  # 已移到 run()
        self._tray.show()

    # ── 生命周期 ────────────────────────────────────────────────

    def run(self) -> None:
        self._window.show()
        # 注册全局热键在窗口 HWND 上，确保 WM_HOTKEY 经窗口过程派发
        # （此时线程拥有前台权限，SetForegroundWindow 理应成功）
        self._hotkey.register(target_hwnd=int(self._window.winId()))
        QApplication.instance().exec()

    def _quit(self) -> None:
        self._notes.stop_watching()
        self._hotkey.unregister()
        self._tray.hide()
        QApplication.instance().quit()

    def _load_custom_fonts(self) -> None:
        """加载 fonts/ 目录中的 .ttf/.otf 字体到应用字体数据库。"""
        if not self._fonts_dir.is_dir():
            return
        for ext in ("*.ttf", "*.otf"):
            for font_file in self._fonts_dir.glob(ext):
                QFontDatabase.addApplicationFont(str(font_file))

    # ── 回调（Window → NotesManager）────────────────────────────

    def _on_save_content(self, filepath: str, content: str) -> None:
        self._notes.save_content(filepath, content)

    def _on_create_note(self, name: str | None = None, group: str = "") -> None:
        if not name:
            name = self._unique_filename("新便签", group)
        self._notes.create(name, group)

    def _on_delete_note(self, filepath: str) -> None:
        self._notes.delete(filepath)

    def _on_rename_note(self, old_path: str, new_name: str) -> None:
        self._notes.rename(old_path, new_name)

    def _on_move_note(self, filepath: str, target_group: str) -> None:
        self._notes.move(filepath, target_group)

    def _on_create_folder(self, rel_path: str) -> None:
        self._notes.create_folder(rel_path)

    def _on_rename_folder(self, rel_path: str, new_name: str) -> None:
        self._notes.rename_folder(rel_path, new_name)

    def _on_delete_folder(self, rel_path: str) -> None:
        self._notes.delete_folder(rel_path)

    # ── 设置界面 ───────────────────────────────────────────────

    def _on_settings_requested(self) -> None:
        from settings import SettingsDialog

        modifiers, key = self._config.get_hotkey()
        _, theme = self._config.load()  # 重新加载 theme.json
        dialog = SettingsDialog(
            current_modifiers=modifiers,
            current_key=key,
            theme=theme,
            autostart=self._config.is_autostart_enabled(),
            fonts_dir=self._fonts_dir,
            parent=self._window,
        )
        # 连接「应用」按钮（保存+预览，不关闭对话框）
        dialog.applied.connect(self._apply_theme)

        if dialog.exec() != SettingsDialog.DialogCode.Accepted:
            return

        new_mods, new_key, new_theme, new_autostart = dialog.get_result()

        # 保存快捷键
        config_dict, _ = self._config.load()
        config_dict.setdefault("hotkey", {})
        config_dict["hotkey"]["modifiers"] = new_mods
        config_dict["hotkey"]["key"] = new_key
        self._config.save_config(config_dict)
        self._hotkey.rebind(new_mods, new_key, target_hwnd=int(self._window.winId()))

        # 保存并应用新主题
        self._apply_theme(new_theme)

        # 保存开机自启（写入注册表）
        self._config.set_autostart(new_autostart)

    def _apply_theme(self, theme: dict) -> None:
        """保存主题到文件并在窗口中刷新。"""
        self._config.save_theme(theme)
        self._theme = theme
        self._window.apply_theme(theme)

    # ── 辅助 ────────────────────────────────────────────────────

    def _load_initial_notes(self) -> None:
        for note in self._notes.scan_all():
            self._window.add_tab(note.filepath)
        # 恢复分组抽屉展开状态，并按分组树重建标签行
        self._window.set_open_drawers(self._config.get_open_drawers())
        self._window.rebuild_strip()

    def _unique_filename(self, base: str, group: str = "") -> str:
        """在同分组内生成不重名的便签名（跨分组允许同名）。"""
        tree = self._notes.scan_tree()
        target = self._find_folder_node(tree, group) if group else tree
        existing = {n.filename for n in target.notes} if target else set()
        if base not in existing:
            return base
        i = 2
        while f"{base}_{i}" in existing:
            i += 1
        return f"{base}_{i}"

    @staticmethod
    def _find_folder_node(tree, rel_path: str):
        if tree is None:
            return None
        if tree.rel_path == rel_path:
            return tree
        for sub in tree.folders:
            found = App._find_folder_node(sub, rel_path)
            if found is not None:
                return found
        return None
