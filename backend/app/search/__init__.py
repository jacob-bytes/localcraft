"""检索后端工厂。

业务代码只通过 `get_search_backend()` 拿接口，不直接依赖 FTS5
（docs/02 §3.20 的隔离要求）。
"""

from __future__ import annotations

from functools import lru_cache

from app.core.config import settings
from app.search.base import SearchBackend, SearchDocument, tokenize, tokenize_query
from app.search.postgres_fts import PostgresFtsBackend
from app.search.sqlite_fts import LikeSearchBackend, SqliteFts5Backend


@lru_cache
def get_search_backend() -> SearchBackend:
    """SQLite 走 FTS5，PostgreSQL 走 `tsvector`，其他方言回退到 LIKE。

    后端实例内部会在索引缺失/报错时**自动降级**（`available=False`），
    所以「迁移没跑到 0003 / 0009」这种情况不会让服务起不来。

    M15（契约 §31）：PG 分支原来是 LIKE 回退，现在接上
    `PostgresFtsBackend`（`tools.search_vector` 列 + GIN 索引，迁移 0009）。
    列不存在时它同样降级为「处理不了」，由仓储走 LIKE —— 与 SQLite 对称。
    其余方言（例如 MySQL）仍然是 LIKE 回退。
    """
    if settings.is_sqlite:
        return SqliteFts5Backend()
    if settings.database_url.startswith("postgresql"):
        return PostgresFtsBackend()
    return LikeSearchBackend()


__all__ = [
    "LikeSearchBackend",
    "PostgresFtsBackend",
    "SearchBackend",
    "SearchDocument",
    "SqliteFts5Backend",
    "get_search_backend",
    "tokenize",
    "tokenize_query",
]
