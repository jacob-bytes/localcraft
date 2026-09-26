"""SQLite FTS5 检索实现（外部内容模式）。"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.search.base import SearchBackend, SearchDocument, tokenize, tokenize_query

logger = logging.getLogger(__name__)

#: FTS5 虚表名（docs/02 §3.20）
FTS_TABLE = "tool_search_index"


class SqliteFts5Backend(SearchBackend):
    """FTS5 后端。

    **普通（非 contentless）FTS5 表**：需要 `DELETE` / `UPDATE` 能力，
    而 `content=''` 的 contentless 表不支持它们（见迁移 0003 的说明）。
    代价是 name/summary/description/tags 多存一份，按 docs/02 §7 的估算
    约 1.5 MB/年，相对 50 MB/年的数据库总量可以忽略。

    rowid 直接用 `tool_id`，所以 upsert 是「先 DELETE 再 INSERT」——
    这比 FTS5 的 UPSERT 语义更明确，且 `DELETE` 是按 rowid 走索引删除。
    """

    def __init__(self, *, available: bool = True) -> None:
        self._available = available

    @property
    def name(self) -> str:
        return "sqlite_fts5"

    @property
    def available(self) -> bool:
        return self._available

    @property
    def supports_reindex(self) -> bool:
        """FTS5 虚表可写时为 True；已降级（表缺失/报错）时为 False。

        降级态返回 False 是有意的：这时 `reindex_all()` 只会立刻返回 0，
        让 CLI 报「成功重建 0 条」同样是误导，应当明确报错。
        """
        return self._available

    def _mark_unavailable(self, exc: Exception) -> None:
        if self._available:
            logger.warning("FTS5 索引不可用，回退到 LIKE 匹配: %s", exc)
        self._available = False

    async def upsert(self, session: AsyncSession, tool_id: int, doc: SearchDocument) -> None:
        if not self._available:
            return
        try:
            await session.execute(
                text(f"DELETE FROM {FTS_TABLE} WHERE rowid = :rid"),
                {"rid": tool_id},
            )
            await session.execute(
                text(
                    f"INSERT INTO {FTS_TABLE}(rowid, name, summary, description, tags) "
                    "VALUES (:rid, :name, :summary, :description, :tags)"
                ),
                {
                    "rid": tool_id,
                    "name": tokenize(doc.name),
                    "summary": tokenize(doc.summary),
                    "description": tokenize(doc.description),
                    "tags": tokenize(" ".join(doc.tags)),
                },
            )
        except Exception as exc:  # pragma: no cover - 环境相关
            self._mark_unavailable(exc)

    async def delete(self, session: AsyncSession, tool_id: int) -> None:
        if not self._available:
            return
        try:
            await session.execute(
                text(f"DELETE FROM {FTS_TABLE} WHERE rowid = :rid"),
                {"rid": tool_id},
            )
        except Exception as exc:  # pragma: no cover - 环境相关
            self._mark_unavailable(exc)

    async def matching_tool_ids(self, session: AsyncSession, query: str) -> set[int] | None:
        tokens = tokenize_query(query)
        if not tokens:
            return None
        if not self._available:
            return None
        # 每个 token 用双引号包成短语，避免用户输入里的 FTS5 元字符
        # （`*`、`"`、`OR`、`NEAR` 等）被当成语法 —— 这也是「不把用户输入
        # 拼进查询语法」在同一条原则上的一致性做法。
        match_expr = " AND ".join('"' + t.replace('"', '""') + '"' for t in tokens)
        try:
            result = await session.execute(
                text(f"SELECT rowid FROM {FTS_TABLE} WHERE {FTS_TABLE} MATCH :q"),
                {"q": match_expr},
            )
            return {int(row[0]) for row in result.all()}
        except Exception as exc:
            self._mark_unavailable(exc)
            return None

    async def reindex_all(self, session: AsyncSession) -> int:
        """从 `tools` 表全量重建索引（`python -m app.cli reindex-search`）。"""
        if not self._available:
            return 0
        await session.execute(text(f"DELETE FROM {FTS_TABLE}"))
        result = await session.execute(
            text(
                "SELECT t.id, t.name, t.summary, t.description_md, "
                "COALESCE(GROUP_CONCAT(tg.display_name, ' '), '') AS tag_names "
                "FROM tools t "
                "LEFT JOIN tool_tags tt ON tt.tool_id = t.id "
                "LEFT JOIN tags tg ON tg.id = tt.tag_id "
                "WHERE t.deleted_at IS NULL "
                "GROUP BY t.id"
            )
        )
        count = 0
        for row in result.all():
            await self.upsert(
                session,
                int(row[0]),
                SearchDocument(
                    name=row[1] or "",
                    summary=row[2] or "",
                    description=row[3] or "",
                    tags=[row[4]] if row[4] else [],
                ),
            )
            count += 1
        return count


class LikeSearchBackend(SearchBackend):
    """非 SQLite（或 FTS5 不可用）时的回退实现。

    只负责告诉调用方「我处理不了」，真正的 LIKE 匹配写在 tools 仓储里
    （仓储是唯一允许写 SQL 的地方）。
    """

    @property
    def name(self) -> str:
        return "like_fallback"

    @property
    def supports_reindex(self) -> bool:
        """LIKE 回退**没有索引可重建**：检索语句直接查 `tools` 表，
        没有会漂移的旁路索引，也就没有「重建」这件事。
        """
        return False

    async def upsert(self, session: AsyncSession, tool_id: int, doc: SearchDocument) -> None:
        return None

    async def delete(self, session: AsyncSession, tool_id: int) -> None:
        return None

    async def matching_tool_ids(self, session: AsyncSession, query: str) -> set[int] | None:
        return None

    async def reindex_all(self, session: AsyncSession) -> int:
        return 0
