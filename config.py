"""配置持久化层。

职责：
1. 读取 config.json + theme.json，合并为统一的配置对象
2. 提供类型安全的配置访问方法
3. 支持运行时写入（窗口位置记忆、开机自启状态更改等）

边界：
- 不感知 UI 存在
- 不做 JSON 字段校验（假设格式正确）
- autostart 注册表写入在此处理（MVP 简化）
"""

from __future__ import annotations

import json
import sys
import winreg
from pathlib import Path
from typing import Any


class Config:
    """配置加载与持久化。"""

    def __init__(self, config_path: str | Path, theme_path: str | Path) -> None:
        self._config_path = Path(config_path).resolve()
        self._theme_path = Path(theme_path).resolve()
        self._config: dict[str, Any] = {}
        self._theme: dict[str, Any] = {}

    # ── 加载与保存 ─────────────────────────────────────────────

    def load(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """读取 config.json + theme.json，返回 (config_dict, theme_dict)。"""
        self._config = self._read_json(self._config_path)
        self._theme = self._read_json(self._theme_path)
        return self._config, self._theme

    def save_config(self, config: dict[str, Any]) -> None:
        """持久化 config.json。"""
        self._config = config
        self._write_json(self._config_path, config)

    def save_theme(self, theme: dict[str, Any]) -> None:
        """持久化 theme.json。"""
        self._theme = theme
        self._write_json(self._theme_path, theme)

    # ── 窗口几何 ────────────────────────────────────────────────

    def get_window_geometry(self) -> tuple[int | None, int | None, int, int]:
        """返回 (x, y, width, height)，x/y 为 None 表示居中。"""
        win = self._config.get("window", {})
        x: int | None = win.get("x")
        y: int | None = win.get("y")
        w: int = win.get("width", 400)
        h: int = win.get("height", 500)
        return x, y, w, h

    def set_window_geometry(self, x: int | None, y: int | None, w: int, h: int) -> None:
        self._config.setdefault("window", {})
        self._config["window"]["x"] = x
        self._config["window"]["y"] = y
        self._config["window"]["width"] = w
        self._config["window"]["height"] = h
        self.save_config(self._config)

    # ── 快捷键 ──────────────────────────────────────────────────

    def get_hotkey(self) -> tuple[list[str], str]:
        """返回 (modifiers_list, key)，如 (['ctrl', 'alt'], 'a')。"""
        hk = self._config.get("hotkey", {})
        modifiers: list[str] = hk.get("modifiers", ["ctrl", "alt"])
        key: str = hk.get("key", "a")
        return modifiers, key

    # ── 分组抽屉展开状态 ────────────────────────────────────────

    def get_open_drawers(self) -> list[str]:
        """返回启动时应展开的分组抽屉（相对路径列表）。"""
        win = self._config.get("window", {})
        raw = win.get("open_drawers", [])
        return [str(k) for k in raw] if isinstance(raw, list) else []

    def set_open_drawers(self, keys: list[str]) -> None:
        self._config.setdefault("window", {})
        self._config["window"]["open_drawers"] = list(keys)
        self.save_config(self._config)

    # ── 开机自启 ────────────────────────────────────────────────

    def is_autostart_enabled(self) -> bool:
        behavior = self._config.get("behavior", {})
        return bool(behavior.get("autostart", False))

    def set_autostart(self, enabled: bool) -> None:
        """同时更新注册表 HKCU\\...\\Run。"""
        self._config.setdefault("behavior", {})
        self._config["behavior"]["autostart"] = enabled

        reg_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = "Wallpaper_Notes"

        try:
            if enabled:
                entry = self._build_autostart_entry()
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_key, 0, winreg.KEY_SET_VALUE)
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, entry)
                winreg.CloseKey(key)
            else:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_key, 0, winreg.KEY_SET_VALUE)
                try:
                    winreg.DeleteValue(key, app_name)
                except FileNotFoundError:
                    pass  # 注册表中不存在时忽略
                winreg.CloseKey(key)
        except PermissionError:
            pass  # MVP：权限不足时静默失败

        self.save_config(self._config)

    # ── 内部工具 ────────────────────────────────────────────────

    def _read_json(self, path: str | Path) -> dict[str, Any]:
        """读取并解析 JSON 文件，文件不存在时返回空 dict。"""
        path = Path(path)
        if not path.exists():
            return {}
        try:
            raw = path.read_text(encoding="utf-8")
            return dict(json.loads(raw))
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_json(self, path: str | Path, data: dict[str, Any]) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _build_autostart_entry(self) -> str:
        """构造注册表自启动命令。

        开发模式：python.exe main.py
        打包模式：exe 自身路径
        """
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}"'
        script = Path(__file__).resolve().parent / "main.py"
        return f'"{sys.executable}" "{script}"'
