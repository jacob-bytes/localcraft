"""工具图片仓储（docs/02 §3.12）。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ImageKind
from app.models.tool import ToolImage


async def get_by_id(session: AsyncSession, image_id: int) -> ToolImage | None:
    return await session.get(ToolImage, image_id)


async def list_for_tool(session: AsyncSession, tool_id: int) -> list[ToolImage]:
    result = await session.execute(
        select(ToolImage)
        .where(ToolImage.tool_id == tool_id)
        .order_by(ToolImage.kind.asc(), ToolImage.sort_order.asc(), ToolImage.id.asc())
    )
    return list(result.scalars().all())


async def get_cover(session: AsyncSession, tool_id: int) -> ToolImage | None:
    result = await session.execute(
        select(ToolImage).where(
            ToolImage.tool_id == tool_id, ToolImage.kind == ImageKind.COVER.value
        )
    )
    return result.scalars().first()


async def count_by_kind(session: AsyncSession, tool_id: int, kind: ImageKind) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(ToolImage)
        .where(ToolImage.tool_id == tool_id, ToolImage.kind == kind.value)
    )
    return int(result.scalar_one())


async def next_sort_order(session: AsyncSession, tool_id: int) -> int:
    result = await session.execute(
        select(func.coalesce(func.max(ToolImage.sort_order), -1)).where(
            ToolImage.tool_id == tool_id
        )
    )
    return int(result.scalar_one()) + 1


async def delete(session: AsyncSession, image: ToolImage) -> None:
    await session.delete(image)
    await session.flush()
