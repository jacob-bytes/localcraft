"""`SearchBackend` 抽象。

docs/02 §3.20：全文检索必须被抽象隔离，**不在业务代码里直接写 FTS5 语法**。
SQLite 用 FTS5，将来 PG 用 `pg_trgm`/`tsvector`，业务层只认这个接口。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# 中文分词 —— docs/02 §3.20 的「推荐做法」
# ---------------------------------------------------------------------------
# FTS5 的 unicode61 分词器把连续中文当成**一个** token，导致「工具」搜不到
# 「内网工具平台」。推荐做法是：写入索引时把中文按**单字**加空格切分、
# 英文数字保持整词；查询时对关键词做同样的处理。
_CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x3400, 0x4DBF),  # CJK 扩展 A
    (0x4E00, 0x9FFF),  # CJK 基本区
    (0xF900, 0xFAFF),  # CJK 兼容表意
    (0x3040, 0x30FF),  # 日文假名
    (0xAC00, 0xD7AF),  # 韩文
)


def is_cjk(ch: str) -> bool:
    code = ord(ch)
    return any(lo <= code <= hi for lo, hi in _CJK_RANGES)


def tokenize(text: str) -> str:
    """把文本转成 FTS5 友好的空格分隔 token 串。

    中文按单字切分（`内网工具` → `内 网 工 具`），英文与数字保持整词，
    其余字符作为分隔符丢弃。
    """
    out: list[str] = []
    buf: list[str] = []
    for ch in text:
        if is_cjk(ch):
            if buf:
                out.append("".join(buf))
                buf.clear()
            out.append(ch)
        elif ch.isalnum() or ch in "._-+#":
            buf.append(ch)
        else:
            if buf:
                out.append("".join(buf))
                buf.clear()
    if buf:
        out.append("".join(buf))
    return " ".join(out)


def tokenize_query(text: str) -> list[str]:
    """查询关键词 → token 列表（与写入侧同一套规则）。"""
    return [t for t in tokenize(text).split(" ") if t]


@dataclass
class SearchDocument:
    """待索引的一篇文档。"""

    name: str = ""
    summary: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)

    def to_index_text(self) -> str:
        return "\n".join([self.name, self.summary, self.description, " ".join(self.tags)])


@runtime_checkable
class SearchBackend(Protocol):
    """全文检索后端接口。"""

    @property
    def name(self) -> str: ...

    async def upsert(self, session: AsyncSession, tool_id: int, doc: SearchDocument) -> None:
        """写入/更新一篇文档。必须由调用方的同一个事务包裹，保证索引与数据一致。"""
        ...

    async def delete(self, session: AsyncSession, tool_id: int) -> None: ...

    async def matching_tool_ids(self, session: AsyncSession, query: str) -> set[int] | None:
        """返回命中的 tool_id 集合。

        `None` 表示「本后端无法处理该查询」，由调用方回退到 LIKE 匹配。
        """
        ...

    async def reindex_all(self, session: AsyncSession) -> int: ...
