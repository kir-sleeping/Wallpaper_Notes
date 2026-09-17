"""文件系统到便签列表的映射器。

职责：
1. 维护 notes/ 目录的实时镜像——与 .md 文件（含子文件夹）保持最终一致
2. 提供 CRUD 方法，直接操作文件系统（便签 + 文件夹即分组）
3. 启动 watchdog 递归监听目录变化，变化时发射信号
4. 对上游屏蔽文件路径、监听器等实现细节

分组模型：
- notes/ 下的子文件夹 = 分组，可递归嵌套，层级不限
- 分组不是独立数据——group 由文件相对位置推导（models.NoteInfo.group）
- scan_tree() 返回整棵分组树，供 UI 一次性重建标签行

边界：
- 不关心文件内容格式（仅读写，不做解析）
- 不关心上游如何使用 NoteInfo / FolderNode
- 只操作 notes/ 目录（所有写操作带路径安全检查）
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from watchdog.events import (
    DirCreatedEvent,
    DirDeletedEvent,
    DirMovedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)
from watchdog.observers.polling import PollingObserver as Observer

from models import FolderNode, NoteInfo

# ── 常量 ──────────────────────────────────────────────────────

_DEBOUNCE_MS = 300  # 同一文件连续事件的时间间隔阈值


def _rel_of(notes_dir: Path, path: str | Path) -> str:
    """绝对路径 → 相对 notes/ 的 posix 风格路径；根目录或不在 notes/ 下返回 ''。"""
    try:
        rel = Path(path).resolve().relative_to(notes_dir).as_posix()
    except ValueError:
        return ""
    return "" if rel == "." else rel


class _NotesEventHandler(FileSystemEventHandler):
    """watchdog 事件 → NotesManager 信号的中转器。

    在独立线程中执行，通过 Qt 信号与主线程通信。
    内置简单去重：同一 (事件类型, 路径) 300ms 内的重复事件只处理一次。
    """

    def __init__(self, manager: NotesManager) -> None:
        super().__init__()
        self._manager = manager

    def on_modified(self, event: FileSystemEventHandler) -> None:
        if isinstance(event, FileModifiedEvent):
            self._manager._on_watchdog_event("modified", event.src_path)

    def on_created(self, event: FileSystemEventHandler) -> None:
        if isinstance(event, FileCreatedEvent):
            self._manager._on_watchdog_event("created", event.src_path)
        elif isinstance(event, DirCreatedEvent):
            self._manager._on_watchdog_event("folder_created", event.src_path)

    def on_deleted(self, event: FileSystemEventHandler) -> None:
        if isinstance(event, FileDeletedEvent):
            self._manager._on_watchdog_event("deleted", event.src_path)
        elif isinstance(event, DirDeletedEvent):
            self._manager._on_watchdog_event("folder_deleted", event.src_path)

    def on_moved(self, event: FileSystemEventHandler) -> None:
        if isinstance(event, FileMovedEvent):
            self._manager._on_watchdog_event_moved(event.src_path, event.dest_path)
        elif isinstance(event, DirMovedEvent):
            self._manager._on_watchdog_event("folder_deleted", event.src_path)
            self._manager._on_watchdog_event("folder_created", event.dest_path)


# ═══════════════════════════════════════════════════════════════
# NotesManager
# ═══════════════════════════════════════════════════════════════

class NotesManager(QObject):
    """便签数据管理器，维护 notes/ 目录（含分组子文件夹）的实时映射。"""

    # ── 信号 ────────────────────────────────────────────────────

    note_changed = Signal(str)          # filepath — 文件内容被修改
    note_added = Signal(str)            # filepath — 新文件被创建
    note_deleted = Signal(str)          # filepath — 文件被删除
    note_moved = Signal(str, str)       # old_path, new_path — 便签被移动
    folder_added = Signal(str)          # rel_path — 分组被创建
    folder_renamed = Signal(str, str)   # old_rel, new_rel — 分组被重命名
    folder_deleted = Signal(str)        # rel_path — 分组被删除

    # ── 生命周期 ────────────────────────────────────────────────

    def __init__(self, notes_dir: str | Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._notes_dir = Path(notes_dir).resolve()
        self._observer: Observer | None = None
        # 防抖：{(event_type, path): last_timestamp}
        self._debounce: dict[tuple[str, str], float] = {}
        # 确保目录存在
        self._notes_dir.mkdir(parents=True, exist_ok=True)

    # ── 全量扫描 ────────────────────────────────────────────────

    def scan_all(self) -> list[NoteInfo]:
        """递归扫描 notes/ 下所有 .md 文件（含分组子文件夹），按文件名排序。"""
        md_files = sorted(self._notes_dir.rglob("*.md"), key=lambda p: p.name.lower())
        return [self._build_note_info(p) for p in md_files]

    def scan_tree(self) -> FolderNode:
        """扫描整棵分组树，返回根 FolderNode。

        根节点 rel_path=''，其 notes 为根目录散装便签，
        folders 为一级分组（递归嵌套）。节点按名称排序（不区分大小写）。
        """
        return self._build_folder_node(self._notes_dir, "")

    def _build_folder_node(self, dir_path: Path, rel_path: str) -> FolderNode:
        notes = sorted(dir_path.glob("*.md"), key=lambda p: p.name.lower())
        node = FolderNode(
            name=dir_path.name,
            rel_path=rel_path,
            notes=[self._build_note_info(p) for p in notes],
            folders=[],
        )
        for sub in sorted(
            (p for p in dir_path.iterdir() if p.is_dir()),
            key=lambda p: p.name.lower(),
        ):
            sub_rel = f"{rel_path}/{sub.name}" if rel_path else sub.name
            node.folders.append(self._build_folder_node(sub, sub_rel))
        return node

    # ── 便签 CRUD ───────────────────────────────────────────────

    def create(self, filename: str, group: str = "") -> NoteInfo:
        """在指定分组下创建新的空 .md 文件，返回 NoteInfo。

        filename 不含路径，不含 .md 后缀；group 为分组相对路径，'' = 根目录。
        """
        target_dir = self._resolve_group_dir(group)
        name = filename if filename.endswith(".md") else f"{filename}.md"
        filepath = (target_dir / name).resolve()
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text("", encoding="utf-8")
        info = self._build_note_info(filepath)
        # 由于是自己创建的文件，watchdog 会检测到。
        # 但我们用防抖来避免重复通知。
        self.note_added.emit(str(filepath))
        return info

    def delete(self, filepath: str | Path) -> None:
        """删除指定的 .md 文件。"""
        path = Path(filepath).resolve()
        if not self._is_inside_notes(path):
            return
        if path.exists():
            path.unlink()
            self.note_deleted.emit(str(path))

    def rename(self, old_path: str | Path, new_name: str) -> str:
        """重命名 .md 文件（保持所在分组不变），返回新路径。

        new_name 不含路径，不含 .md 后缀，如 '新便签'。
        """
        old = Path(old_path).resolve()
        nname = new_name if new_name.endswith(".md") else f"{new_name}.md"
        new = (old.parent / nname).resolve()
        old.rename(new)
        # 通知上游：旧标签消失，新标签出现
        self.note_deleted.emit(str(old))
        self.note_added.emit(str(new))
        return str(new)

    def move(self, filepath: str | Path, target_group: str) -> str:
        """移动便签到目标分组，返回新路径。

        target_group 为分组相对路径，'' = 根目录。
        目标存在同名文件时自动追加 _2、_3 … 后缀。
        """
        src = Path(filepath).resolve()
        if not self._is_inside_notes(src) or not src.exists():
            return str(src)

        target_dir = self._resolve_group_dir(target_group)
        if src.parent == target_dir:
            return str(src)  # 已在目标分组，无操作

        dest = target_dir / src.name
        if dest.exists():
            stem, suffix = src.stem, src.suffix
            i = 2
            while (target_dir / f"{stem}_{i}{suffix}").exists():
                i += 1
            dest = target_dir / f"{stem}_{i}{suffix}"

        shutil.move(str(src), str(dest))
        self.note_deleted.emit(str(src))
        self.note_added.emit(str(dest))
        self.note_moved.emit(str(src), str(dest))
        return str(dest)

    # ── 分组 CRUD ───────────────────────────────────────────────

    def create_folder(self, rel_path: str) -> Path:
        """创建分组文件夹（支持 '工作/项目' 形式自动建父级）。"""
        target = self._resolve_group_dir(rel_path)
        target.mkdir(parents=True, exist_ok=True)
        self.folder_added.emit(_rel_of(self._notes_dir, target))
        return target

    def rename_folder(self, rel_path: str, new_name: str) -> str:
        """重命名分组，返回新 rel_path。子分组与便签随之整体移动。"""
        old_dir = self._resolve_group_dir(rel_path)
        if old_dir == self._notes_dir or not old_dir.exists():
            return rel_path
        new_dir = old_dir.parent / new_name
        if new_dir.exists():
            return rel_path  # 目标已存在，放弃（MVP 不做合并）
        old_dir.rename(new_dir)
        old_rel = _rel_of(self._notes_dir, old_dir)
        new_rel = _rel_of(self._notes_dir, new_dir)
        self.folder_renamed.emit(old_rel, new_rel)
        return new_rel

    def delete_folder(self, rel_path: str) -> None:
        """删除分组及其内全部内容（递归删除）。"""
        target = self._resolve_group_dir(rel_path)
        if target == self._notes_dir or not target.exists():
            return
        shutil.rmtree(target)
        self.folder_deleted.emit(_rel_of(self._notes_dir, target))

    # ── 读写内容 ────────────────────────────────────────────────

    def save_content(self, filepath: str | Path, content: str) -> None:
        """写入文件内容。"""
        Path(filepath).write_text(content, encoding="utf-8")

    def read_content(self, filepath: str | Path) -> str:
        """读取文件内容。"""
        try:
            return Path(filepath).read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    # ── 文件监听 ────────────────────────────────────────────────

    def start_watching(self) -> None:
        """启动 watchdog 递归轮询监听（覆盖分组子文件夹）。"""
        if self._observer is not None:
            return
        handler = _NotesEventHandler(self)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._notes_dir), recursive=True)
        self._observer.start()

    def stop_watching(self) -> None:
        """停止 watchdog 监听。"""
        if self._observer is None:
            return
        self._observer.stop()
        self._observer.join(timeout=3)
        self._observer = None

    # ── 内部 ────────────────────────────────────────────────────

    def _is_inside_notes(self, path: Path) -> bool:
        """路径安全检查：必须位于 notes/ 目录内。"""
        return str(path).startswith(str(self._notes_dir))

    def _resolve_group_dir(self, group: str) -> Path:
        """分组相对路径 → notes/ 内的绝对目录。拒绝越界路径。"""
        rel = (group or "").strip("/").replace("\\", "/")
        target = (self._notes_dir / rel).resolve() if rel else self._notes_dir
        if not self._is_inside_notes(target):
            raise ValueError(f"分组路径越界：{group!r}")
        return target

    def _build_note_info(self, filepath: Path) -> NoteInfo:
        """根据文件路径构造 NoteInfo（group 由相对位置推导）。"""
        resolved = filepath.resolve()
        return NoteInfo(
            filepath=str(resolved),
            filename=filepath.stem,  # 不含 .md 后缀
            content=self.read_content(resolved),
            last_modified=resolved.stat().st_mtime if resolved.exists() else 0.0,
            group=_rel_of(self._notes_dir, resolved.parent),
        )

    def _on_watchdog_event(self, event_type: str, src_path: str) -> None:
        """接收 watchdog 事件，防抖后发射对应信号。

        在 watchdog 线程中调用；Signal.emit() 线程安全。
        """
        path_obj = Path(src_path)

        # 文件夹事件
        if event_type.startswith("folder_"):
            rel = _rel_of(self._notes_dir, path_obj)
            if not rel or rel == ".":
                return  # notes/ 根目录本身的变动不通知
            key = (event_type, rel)
            if self._debounced(key):
                return
            if event_type == "folder_created":
                self.folder_added.emit(rel)
            elif event_type == "folder_deleted":
                self.folder_deleted.emit(rel)
            return

        # 文件事件——仅处理 .md
        if path_obj.suffix.lower() != ".md":
            return

        key = (event_type, str(path_obj.resolve()))
        if self._debounced(key):
            return

        # 发射信号
        fp = str(path_obj.resolve())
        if event_type == "modified":
            self.note_changed.emit(fp)
        elif event_type == "created":
            self.note_added.emit(fp)
        elif event_type == "deleted":
            self.note_deleted.emit(fp)

    def _on_watchdog_event_moved(self, src_path: str, dest_path: str) -> None:
        """外部移动 .md 文件 → 拆为 deleted + added。"""
        src, dest = Path(src_path), Path(dest_path)
        if src.suffix.lower() != ".md" or dest.suffix.lower() != ".md":
            return
        self.note_deleted.emit(str(src.resolve()))
        self.note_added.emit(str(dest.resolve()))

    def _debounced(self, key: tuple[str, str]) -> bool:
        """300ms 内同一 (事件, 路径) 只处理一次。返回 True 表示已拦截。"""
        now = time.time()
        last = self._debounce.get(key, 0)
        if now - last < (_DEBOUNCE_MS / 1000.0):
            return True
        self._debounce[key] = now
        return False
