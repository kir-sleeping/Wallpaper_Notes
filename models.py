from dataclasses import dataclass, field


@dataclass
class NoteInfo:
    """单个便签的数据表示。

    filepath:  .md 文件的完整路径
    filename:  显示用名称（不含 .md 后缀）
    content:   文件内容（原始 Markdown 文本）
    last_modified:  文件最后修改时间戳（time.time() 格式）
    group:     相对 notes/ 的父目录路径（posix 风格，如 '工作/2026'），
               '' 表示根目录。文件夹即分组：group 由文件位置推导，
               不是独立字段。
    """
    filepath: str
    filename: str
    content: str
    last_modified: float
    group: str = ""


@dataclass
class FolderNode:
    """分组树节点——notes/ 下一个子文件夹的内存投影。

    name:      文件夹名（即分组显示名）
    rel_path:  相对 notes/ 的路径（posix 风格），根为 ''
    notes:     该文件夹直系的便签（不含子文件夹内的）
    folders:   子分组（递归嵌套，层级不限）
    """
    name: str
    rel_path: str
    notes: list[NoteInfo] = field(default_factory=list)
    folders: list["FolderNode"] = field(default_factory=list)
