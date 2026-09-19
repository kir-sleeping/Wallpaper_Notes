"""Markdown 渲染管道。

职责：
1. 接收 Markdown 文本，返回 HTML 供 QTextBrowser 显示
2. 结合 theme.json 生成 CSS 样式表
3. 作为渲染管道的隔离层——未来替换渲染库只需改此文件

策略：
  mistune（CommonMark + GFM 插件）负责 Markdown → HTML，本模块负责
  mistune 不知道的三件事：
  a) 面向 Qt 富文本引擎输出——QTextBrowser 只认 HTML/CSS 子集，
     <del>→<s>、任务列表 checkbox→☐/☑（<input> 不被支持）
  b) hard_wrap=True：单换行渲染为 <br>（CommonMark 默认合并为空格，
     但便签场景"写一行显示一行"是既定行为，旧便签依赖它）
  c) 图片相对路径基于便签文件所在目录解析为 file:/// 绝对 URL
     （QTextBrowser 无 baseUrl 时无法解析相对资源）；盘符路径、中文路径、
     反斜线路径都会先解码再统一转成 file:///（见 to_qt_url）；并默认给图片加
     style="max-width:100%"——Qt 不会自动缩放超宽图片，而该 CSS 在 Qt 中
     是「只缩不放」语义，且随窗口缩放自动重排。单图可用 `{width=N}`
     （像素）覆盖。

  escape=True：原生 HTML 被转义显示（沿用旧渲染器的安全策略）。

边界：
- 不做文件 IO（base_dir 由调用方传入）
- 不感知 UI 层
"""

from __future__ import annotations

import re
from html import escape as _esc_attr
from html import unescape as _html_unescape
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import mistune

# ── CSS 模板 ──────────────────────────────────────────────────

# QTextBrowser 支持的 CSS 子集（Qt Rich Text）
_CONTENT_CSS_TEMPLATE = """
body {{
    font-family: "{font_family}", "Segoe UI Emoji", sans-serif;
    font-size: {font_size}px;
    color: {text_color};
    line-height: {line_height};
    padding: {padding_v}px {padding_h}px;
    margin: 0;
}}
h1 {{ font-family: "{heading_font_family}", "Segoe UI Emoji", sans-serif; font-size: {h1_size}px; color: {heading_color}; margin: 8px 0 4px 0; }}
h2 {{ font-family: "{heading_font_family}", "Segoe UI Emoji", sans-serif; font-size: {h2_size}px; color: {heading_color}; margin: 6px 0 3px 0; }}
h3 {{ font-family: "{heading_font_family}", "Segoe UI Emoji", sans-serif; font-size: {h3_size}px; color: {heading_color}; margin: 4px 0 2px 0; }}
h4 {{ font-family: "{heading_font_family}", "Segoe UI Emoji", sans-serif; font-size: {h4_size}px; color: {heading_color}; margin: 4px 0 2px 0; }}
h5 {{ font-family: "{heading_font_family}", "Segoe UI Emoji", sans-serif; font-size: {h5_size}px; color: {heading_color}; margin: 3px 0 1px 0; }}
h6 {{ font-family: "{heading_font_family}", "Segoe UI Emoji", sans-serif; font-size: {h6_size}px; color: {heading_color}; margin: 3px 0 1px 0; }}
p  {{ margin: 4px 0; }}
ul, ol {{ margin: 4px 0; padding-left: 20px; }}
blockquote {{
    border-left: 3px solid {quote_border};
    padding: 4px 12px;
    margin: 6px 0;
    color: {quote_color};
}}
code {{
    font-family: "{mono_family}";
    background: {code_bg};
    padding: 1px 4px;
    border-radius: 3px;
}}
pre {{
    font-family: "{mono_family}";
    background: {code_bg};
    padding: 8px 12px;
    border-radius: 4px;
    margin: 6px 0;
}}
pre code {{
    background: transparent;
    padding: 0;
    border-radius: 0;
}}
table {{ margin: 6px 0; }}
th {{ background-color: {code_bg}; padding: 3px 8px; }}
td {{ padding: 3px 8px; }}
th, td {{ border: 1px solid {quote_border}; }}
hr {{ border: none; border-top: 1px solid {quote_border}; margin: 8px 0; }}
a  {{ color: {link_color}; }}
"""

# ── 图片路径 → Qt 可加载 URL ──────────────────────────────────

# 盘符路径：D:/x 或 D:\x（注意 http:// 等不会误匹配——首字符后紧跟 ':' 才是）
_WIN_DRIVE_RE = re.compile(r"^[a-zA-Z]:[\\/]")
# 真 URL 的 scheme（http、file、data …）
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")


def _as_file_uri(path: Path) -> str:
    """Path → file:/// URI；失败时退回原字符串。"""
    try:
        return path.resolve().as_uri()
    except (OSError, ValueError):
        return str(path)


def to_qt_url(url: str, base_dir: str | None) -> str:
    """把 Markdown 里的图片路径转成 QTextBrowser 能加载的 URL。

    三个必须处理的坑：
    1. mistune 会给手写路径做百分号编码（中文 `图片/照片.png` 变成
       `%E5%9B%BE%E7%89%87/...`，反斜线 `\\` 变成 `%5C`），所以当成本地
       路径前必须先解码，否则拼出来的文件名在磁盘上不存在。
    2. 盘符路径（`D:/…`、`D:\\…`、以及编码后的 `D:%5C…`）必须转成
       file:/// 形式。裸写盘符时 Qt 只能靠「原样字符串恰好是存在的文件」
       碰巧打开（纯 ASCII 正斜线路径因此看起来能用），含中文或反斜线就必然失败。
    3. 反斜线路径要先解码再判断，否则冒号后紧跟的是 `%` 而非分隔符，
       盘符判定会漏掉。
    """
    if _SCHEME_RE.match(url):
        decoded = unquote(url)
        if _WIN_DRIVE_RE.match(decoded):     # D:\ / D:/ / D:%5C… → 本地盘符路径
            return _as_file_uri(Path(decoded))
        return url                            # http(s)/file:// 等真 URL：保持原样
    if not base_dir:                          # 无基准目录：保持原样
        return url
    return _as_file_uri(Path(base_dir) / unquote(url))   # 相对路径 → 便签所在目录


# ── Qt 不支持的标签 → 等效替换 ────────────────────────────────

# mistune task_lists 插件输出：… disabled/>（未勾选）或 … disabled checked/>（已勾选）
_TASK_CHECKBOX_RE = re.compile(r'<input[^>]*type="checkbox"[^>]*/>')

_FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")

# 图片目的地（含尖括号包裹形式）：![alt](路径) / ![alt](<带 空格 的路径>)
_IMG_DEST_RE = re.compile(r"(!\[[^\]]*\]\(\s*<?)([^)>\n]*)(>?\s*\))")


def _is_delimiter_row(line: str) -> bool:
    """GFM 表格分隔行：| --- | :--: | 形态，至少一个单元格。"""
    s = line.strip()
    if "|" not in s:
        return False
    s = s.strip("|")
    cells = s.split("|")
    if not cells:
        return False
    return all(re.fullmatch(r"\s*:?-+:?\s*", c) for c in cells)


def _ensure_table_breaks(text: str) -> str:
    """解析前的预处理（围栏代码块内一律跳过不做改动）。

    1. 表格前自动补空行 —— GFM 中表格无法打断引用/列表的惰性延续
       （「> 引用行」下一行写表格会被吸进引用段落）。检测「表头行 +
       分隔行」组合，若上一行非空则补一个空行。
    2. 图片路径里的反斜线换成正斜线 —— Markdown 把 `\\` 当转义符，
       `D:\\储存\\_下划线.png` 里的 `\\_` 会让反斜线被吃掉，图片随即失效。
       Windows 同样接受 `/`，解析前替换即可规避免（只动图片目的地，
       不动正文；代码块内跳过）。
    """
    lines = text.split("\n")
    out: list[str] = []
    in_fence = False
    fence_marker = ""
    for i, line in enumerate(lines):
        if in_fence:
            out.append(line)
            if line.strip().startswith(fence_marker):
                in_fence = False
            continue
        m = _FENCE_RE.match(line)
        if m:
            in_fence = True
            fence_marker = m.group(1)
            out.append(line)
            continue
        line = _IMG_DEST_RE.sub(
            lambda mm: mm.group(1) + mm.group(2).replace("\\", "/") + mm.group(3),
            line,
        )
        if (
            "|" in line
            and i + 1 < len(lines)
            and _is_delimiter_row(lines[i + 1])
            and out
            and out[-1].strip()
        ):
            out.append("")
        out.append(line)
    return "\n".join(out)


def _postprocess_for_qt(html: str) -> str:
    """把 Qt 富文本引擎不认识的标签替换为等效物。"""

    def _checkbox(m: re.Match) -> str:
        return "☑ " if "checked" in m.group(0) else "☐ "

    html = _TASK_CHECKBOX_RE.sub(_checkbox, html)
    # Qt 不支持 <del>，<s> 是其删除线标签
    html = html.replace("<del>", "<s>").replace("</del>", "</s>")
    return html


# ── 图片宽度覆盖 ──────────────────────────────────────────────

# mistune 不认 `{width=N}` 后缀，会把它当普通文本留在 <img> 之后，此处合并回标签。
# 像素值 → width 属性；百分比 → 按当前可用宽度换算成固定像素的 width 属性
# （先定尺寸，之后窗口变化不再等比缩放），再由渲染器的 max-width:100% 兜底：
# 便签窄到放不下时自动压回，不溢出。
# 注意不能用 width="N%" 属性本身——Qt 会把布局高度算成 0（图片压住后续内容）。
_IMG_WIDTH_SUFFIX_RE = re.compile(r"(<img\b[^>]*>)\s*\{width=([0-9]{1,3}%|[0-9]{1,5})\}")
_IMG_WIDTH_ATTR_RE = re.compile(r'\s*width="[^"]*"')
_IMG_STYLE_ATTR_RE = re.compile(r'\s*style="[^"]*"')
_IMG_SRC_RE = re.compile(r'src="([^"]*)"')


def _apply_image_widths(
    html: str,
    content_width: int | None = None,
    image_size=None,
) -> str:
    """把图片后的 `{width=N}` / `{width=N%}` 后缀合并进 <img> 标签。

    像素：width="N"（精确尺寸），渲染器的 max-width:100% 保留作容器兜底。
    百分比：按 content_width 换算成**固定像素**——先定尺寸，此后窗口缩放不再
    等比变化；便签窄到放不下时由 max-width:100% 压回。若提供 image_size
    （原图尺寸探测函数），宽度还会卡在原图宽度以内（不放大）。
    拿不到 content_width（控件尚未布局）时退回 style="max-width:N%" 占位，
    待重渲染时再定成固定尺寸。
    非法值（{width=0%}、{width=101%}、{width=abc}）不生效，原样留作文字。
    """

    def _strip_tail(tag: str) -> str:
        tag = tag[:-1].rstrip()          # 去掉结尾 '>'
        return tag[:-1].rstrip() if tag.endswith("/") else tag

    def _natural_width(tag: str) -> int | None:
        """探测原图宽度；失败返回 None（此时不做不放大限制）。"""
        if image_size is None:
            return None
        m = _IMG_SRC_RE.search(tag)
        if not m:
            return None
        try:
            size = image_size(_html_unescape(m.group(1)))
        except Exception:
            return None
        if size and len(size) == 2 and size[0] > 0:
            return int(size[0])
        return None

    def _merge(m: re.Match) -> str:
        value = m.group(2)
        tag = m.group(1)
        if value.endswith("%"):
            pct = int(value[:-1])
            if not 1 <= pct <= 100:
                return m.group(0)        # 越界：原样保留为文字
            if not (content_width and content_width > 0):
                tag = _IMG_STYLE_ATTR_RE.sub("", tag)
                return _strip_tail(tag) + f' style="max-width:{pct}%" />'
            tag = _IMG_WIDTH_ATTR_RE.sub("", tag)
            px = max(1, round(content_width * pct / 100))
            natural = _natural_width(tag)
            if natural is not None:
                px = min(px, natural)    # 不放大
            # style="max-width:100%" 保留：容器更窄时压回，不溢出
            return _strip_tail(tag) + f' width="{px}" />'
        tag = _IMG_WIDTH_ATTR_RE.sub("", tag)   # 防御：清掉已有 width
        return _strip_tail(tag) + f' width="{value}" />'

    return _IMG_WIDTH_SUFFIX_RE.sub(_merge, html)


# ── mistune 渲染器定制 ────────────────────────────────────────

class _QtHtmlRenderer(mistune.HTMLRenderer):
    """面向 Qt 富文本引擎的渲染器。

    仅覆写 image()：
    1. 路径统一交给 to_qt_url() 转成 Qt 能加载的 file:/// URL
       （处理 mistune 的百分号编码与盘符路径，见该函数）。
    2. 每张图带 style="max-width:100%"——Qt 不会自动缩放超宽图片。实测
       该 CSS 在 Qt 中是「只缩不放」语义（200px 图在 300/500/800px 便签里
       均保持 200px），且随窗口缩放自动重排，无需重新渲染 HTML。
       注意：不能用 width="100%"/"50%" 这类百分比——Qt 对百分比宽度会算出
       0 布局高度，图片会压住后续内容（实测确认）。
    3. 单图可用 `{width=N}`（像素）或 `{width=N%}`（占便签宽的比例上限）
       覆盖默认值，见 _apply_image_widths。百分比必须走 max-width：
       width="N%" 属性会让 Qt 的布局高度塌陷为 0（实测确认）。
    """

    def __init__(self, base_dir: str | None = None) -> None:
        super().__init__(escape=True)
        self._base_dir = base_dir

    def image(self, text: str, url: str, title: str | None = None) -> str:
        url = to_qt_url(url, self._base_dir)
        alt = re.sub(r"<[^>]+>", "", text)  # strip 行内标记，取纯文本做 alt
        s = '<img src="' + _esc_attr(url, quote=True) + '"'
        s += ' alt="' + _esc_attr(alt, quote=True) + '"'
        if title:
            s += ' title="' + _esc_attr(title, quote=True) + '"'
        # max-width 让超宽图片缩到容器内（只缩不放），且不破坏布局高度
        return s + ' style="max-width:100%" />'


def _create_markdown(base_dir: str | None):
    """构建本模块专用的 mistune 实例。

    hard_wrap=True：单换行 → <br>（保持旧渲染器行为）。
    escape=True 由 _QtHtmlRenderer 构造参数提供：原生 HTML 转义显示。
    """
    return mistune.create_markdown(
        hard_wrap=True,
        renderer=_QtHtmlRenderer(base_dir),
        plugins=["strikethrough", "table", "url", "task_lists"],
    )


# ── 公开接口 ──────────────────────────────────────────────────

def render(
    md_text: str,
    theme: dict[str, Any],
    base_dir: str | Path | None = None,
    content_width: int | None = None,
    image_size=None,
) -> str:
    """将 Markdown 文本转换为完整 HTML（含内联 CSS），供 QTextBrowser 显示。

    base_dir：便签文件所在目录，用于解析 ![图片](相对路径)。
    不传时相对路径图片无法显示（QTextBrowser 缺 baseUrl）。
    content_width：正文可用文字宽度（像素），用于把 `{width=N%}` 定成固定像素尺寸。
    image_size：可选的原图尺寸探测函数 (url) -> (w, h) | None，用于避免把图放大。
    """
    css = build_css(theme)
    md = _create_markdown(str(base_dir) if base_dir else None)
    body = _postprocess_for_qt(
        _apply_image_widths(md(_ensure_table_breaks(md_text)), content_width, image_size)
    )
    return f"""<html><head><meta charset="utf-8"><style>{css}</style></head><body>{body}</body></html>"""


def build_css(theme: dict[str, Any]) -> str:
    """根据 theme 生成 QTextBrowser 用的 CSS 样式表。

    读取路径：
      theme.content → 正文样式
      theme.editor  → monospace 字体系列（代码块用）
    """
    content = theme.get("content", {})
    editor = theme.get("editor", {})

    font_family = str(content.get("font_family", "Microsoft YaHei"))
    font_size = int(content.get("font_size", 16))
    text_color = str(content.get("text_color", "#333333"))
    heading_color = str(content.get("heading_color", "#aaddff"))
    heading_font_family = str(content.get("heading_font_family", font_family))
    line_height = float(content.get("line_height", 1.6))
    padding_h = int(content.get("padding_h", 16))
    padding_v = int(content.get("padding_v", 16))

    mono_family = str(editor.get("font_family", "Cascadia Code, Consolas, monospace"))

    bg_raw = str(content.get("background_color", "transparent"))
    # 从 "transparent" 或 "rgba(...)" 中提取 alpha 用于引文
    quote_color = text_color
    quote_border = _derive_color(text_color, 0.2)

    css = _CONTENT_CSS_TEMPLATE.format(
        font_family=font_family,
        font_size=font_size,
        text_color=text_color,
        heading_color=heading_color,
        heading_font_family=heading_font_family,
        line_height=line_height,
        padding_h=padding_h,
        padding_v=padding_v,
        h1_size=int(font_size * 1.5),
        h2_size=int(font_size * 1.25),
        h3_size=int(font_size * 1.1),
        h4_size=int(font_size * 1.0),
        h5_size=int(font_size * 0.95),
        h6_size=int(font_size * 0.9),
        mono_family=mono_family,
        quote_color=quote_color,
        quote_border=quote_border,
        code_bg="rgba(0,0,0,0.05)",
        link_color=str(content.get("link_color", "#4A90D9")),
    )
    return css


# ── 内部 ──────────────────────────────────────────────────────


def _derive_color(base: str, alpha: float) -> str:
    """从十六进制颜色值生成 rgba 变体（引用边框、表格边框用）。"""
    base = base.lstrip("#")
    if len(base) != 6:
        return f"rgba(0,0,0,{alpha})"
    try:
        r = int(base[0:2], 16)
        g = int(base[2:4], 16)
        b = int(base[4:6], 16)
        return f"rgba({r},{g},{b},{alpha})"
    except ValueError:
        return f"rgba(0,0,0,{alpha})"
