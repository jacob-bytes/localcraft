"""分类仓储。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.taxonomy import Category


async def list_active(session: AsyncSession) -> list[Category]:
    """启用中的分类，按 `sort_order` 升序（docs/02 §3.6）。

    `GET /api/v1/categories` 只返回 `is_active = true` 的项（契约 §6）。
    """
    result = await session.execute(
        select(Category)
        .where(Category.is_active.is_(True))
        .order_by(Category.sort_order.asc(), Category.id.asc())
    )
    return list(result.scalars().all())


async def get_by_id(session: AsyncSession, category_id: int) -> Category | None:
    return await session.get(Category, category_id)


async def get_by_slug(session: AsyncSession, slug: str) -> Category | None:
    result = await session.execute(select(Category).where(Category.slug == slug))
    return result.scalar_one_or_none()


async def existing_slugs(session: AsyncSession, slugs: list[str]) -> set[str]:
    """校验请求里给的分类 slug 是否存在，用于把非法筛选值收敛掉。"""
    if not slugs:
        return set()
    result = await session.execute(select(Category.slug).where(Category.slug.in_(slugs)))
    return {str(row[0]) for row in result.all()}
