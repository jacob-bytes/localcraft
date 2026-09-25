"""标签仓储。"""

from __future__ import annotations

import unicodedata

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.taxonomy import Tag

#: 联想框默认返回条数
DEFAULT_TAG_LIMIT = 50


def normalize_tag_name(name: str) -> str:
    """标签名归一化：去首尾空格、NFKC 全角转半角、转小写（FR-TAX-03）。

    目的是避免 `Python` 与 `python` 并存。
    """
    return unicodedata.normalize("NFKC", name.strip()).lower()


async def list_tags(
    session: AsyncSession, *, q: str | None = None, limit: int = DEFAULT_TAG_LIMIT
) -> list[Tag]:
    """标签列表，`q` 为**前缀**搜索（docs/03 §2.3：用于输入联想）。

    排序按 `usage_count` 降序，其次按名称 —— 常用标签排在前面。
    """
    stmt = select(Tag).order_by(Tag.usage_count.desc(), Tag.name.asc()).limit(limit)
    if q:
        stmt = stmt.where(Tag.name.startswith(normalize_tag_name(q)))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_by_name(session: AsyncSession, name: str) -> Tag | None:
    result = await session.execute(select(Tag).where(Tag.name == normalize_tag_name(name)))
    return result.scalar_one_or_none()


async def get_or_create(
    session: AsyncSession, name: str, *, created_by_id: int | None = None
) -> Tag:
    """按归一化名取标签，不存在则创建（`display_name` 保留原始写法）。"""
    normalized = normalize_tag_name(name)
    existing = await get_by_name(session, normalized)
    if existing is not None:
        return existing
    tag = Tag(
        name=normalized,
        display_name=name.strip(),
        usage_count=0,
        created_by_id=created_by_id,
    )
    session.add(tag)
    await session.flush()
    return tag


async def recompute_usage_counts(session: AsyncSession) -> int:
    """全量重算 `usage_count`（docs/02 §3.7 推荐「定时重算」而非增删时维护）。"""
    from app.models.tool import ToolTag

    subquery = (
        select(ToolTag.tag_id, func.count(ToolTag.tool_id).label("cnt"))
        .group_by(ToolTag.tag_id)
        .subquery()
    )
    result = await session.execute(
        select(Tag, subquery.c.cnt).outerjoin(subquery, subquery.c.tag_id == Tag.id)
    )
    updated = 0
    for tag, cnt in result.all():
        tag.usage_count = int(cnt or 0)
        updated += 1
    await session.flush()
    return updated
