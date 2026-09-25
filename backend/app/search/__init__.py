"""检索后端工厂。

业务代码只通过 `get_search_backend()` 拿接口，不直接依赖 FTS5
（docs/02 §3.20 的隔离要求）。
"""

from __future__ import annotations

from functools import lru_cache

from app.core.config import settings
from app.search.base import SearchBackend, SearchDocument, tokenize, tokenize_query
from app.search.sqlite_fts import LikeSearchBackend, SqliteFts5Backend


@lru_cache
def get_search_backend() -> SearchBackend:
    """SQLite 走 FTS5，其他方言暂时回退到 LIKE。

    后端实例内部会在 FTS5 虚表缺失/报错时**自动降级**（`available=False`），
    所以「迁移没跑到 0003」这种情况不会让服务起不来。
    """
    if settings.is_sqlite:
        return SqliteFts5Backend()
    return LikeSearchBackend()


__all__ = [
    "LikeSearchBackend",
    "SearchBackend",
    "SearchDocument",
    "SqliteFts5Backend",
    "get_search_backend",
    "tokenize",
    "tokenize_query",
]
