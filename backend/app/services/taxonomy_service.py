"""分类与标签服务。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import categories as categories_repo
from app.repositories import tags as tags_repo
from app.repositories import tools as tools_repo
from app.repositories.tools import VisibilityContext
from app.schemas.taxonomy import CategoryOut, TagOut


async def list_categories(
    session: AsyncSession, *, visibility: VisibilityContext
) -> list[CategoryOut]:
    """`GET /api/v1/categories`（FR-TAX-07）。

    每个分类带上**当前用户可见的**工具数，与 `GET /tools` 的
    `facets.categories[].count` 共用同一段聚合 SQL，两处数字必然一致。
    """
    counts = await tools_repo.category_tool_counts(session, visibility=visibility)
    rows = await categories_repo.list_active(session)
    return [
        CategoryOut(
            id=c.id,
            slug=c.slug,
            name=c.name,
            description=c.description,
            icon=c.icon,
            sort_order=c.sort_order,
            is_active=c.is_active,
            tool_count=counts.get(c.id, 0),
        )
        for c in rows
    ]


async def list_tags(
    session: AsyncSession, *, q: str | None = None, limit: int = tags_repo.DEFAULT_TAG_LIMIT
) -> list[TagOut]:
    """`GET /api/v1/tags`（支持 `?q=` 前缀搜索，用于输入联想）。"""
    rows = await tags_repo.list_tags(session, q=q, limit=limit)
    return [
        TagOut(
            id=t.id,
            name=t.name,
            display_name=t.display_name,
            usage_count=t.usage_count,
        )
        for t in rows
    ]
