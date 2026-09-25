"""Markdown 渲染与 XSS 消毒（FR-TOOL-09）。

链路：`markdown-it-py` 渲染 → `bleach` 白名单消毒。

为什么两步都要：
  - markdown-it 会把 `[x](javascript:alert(1))` 渲染成 `<a href="javascript:...">`，
    渲染器本身**不做安全过滤**（它明确把自己定位为「只负责转换」）
  - bleach 用**白名单**（不是黑名单）过滤标签与属性，未知标签直接转义

白名单与前端保持一致（FR-TOOL-09「二者择一并统一」）：前端用
markdown-it + DOMPurify，后端用 markdown-it-py + bleach，
两侧放行的标签集合按同一份清单维护。
"""

from __future__ import annotations

import logging

import bleach
from markdown_it import MarkdownIt

logger = logging.getLogger(__name__)

#: 允许的标签。覆盖标题/列表/代码/表格/引用/链接/图片/强调/分隔线。
ALLOWED_TAGS: frozenset[str] = frozenset(
    {
        "h1", "h2", "h3", "h4", "h5", "h6",
        "p", "br", "hr",
        "strong", "em", "del", "s", "sub", "sup",
        "blockquote",
        "ul", "ol", "li",
        "code", "pre",
        "a", "img",
        "table", "thead", "tbody", "tr", "th", "td",
        "div", "span",
    }
)

#: 允许的属性。`class` 只留给代码高亮（markdown-it 会输出 `language-xxx`）。
ALLOWED_ATTRIBUTES: dict[str, list[str]] = {
    "*": ["class"],
    "a": ["href", "title", "rel", "target"],
    "img": ["src", "alt", "title", "width", "height"],
    "th": ["align"],
    "td": ["align"],
    "code": ["class"],
    "pre": ["class"],
}

#: 允许的协议。**不含** `javascript:` / `data:`（`data:` 只在图片里另行放行）。
ALLOWED_PROTOCOLS: frozenset[str] = frozenset({"http", "https", "mailto"})

#: 单次渲染的输入上限（FR-TOOL-01 的 description_md ≤ 100000 字符，
#: 这里留一倍余量，防止绕过 Pydantic 校验的路径把超大文本喂进来）
MAX_MARKDOWN_CHARS = 200_000

_md = MarkdownIt(
    "commonmark",
    {
        "html": False,  # 关键：禁止 Markdown 里内嵌原始 HTML
        "linkify": True,
        "breaks": True,
        "typographer": False,
    },
).enable(["table", "strikethrough"])


def render_markdown(text: str | None) -> str:
    """Markdown → 消毒后的 HTML。空输入返回空串。"""
    if not text:
        return ""
    if len(text) > MAX_MARKDOWN_CHARS:
        logger.warning("Markdown 超长已截断: %d 字符", len(text))
        text = text[:MAX_MARKDOWN_CHARS]

    raw_html = _md.render(text)
    clean = bleach.clean(
        raw_html,
        tags=set(ALLOWED_TAGS),
        attributes=ALLOWED_ATTRIBUTES,
        protocols=set(ALLOWED_PROTOCOLS),
        strip=True,  # 不在白名单的标签「去掉标签保留文字」，而不是整段丢弃
    )
    # 外链加 rel，避免 target=_blank 的 window.opener 劫持
    return bleach.linkify(clean, skip_tags=["pre", "code"])


def strip_markdown(text: str | None) -> str:
    """提取纯文本（用于摘要/搜索结果片段）。"""
    if not text:
        return ""
    rendered = _md.render(text)
    return bleach.clean(rendered, tags=[], attributes={}, strip=True).strip()
