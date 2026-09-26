"""收藏 / 点赞关系仓储（M8 / contracts §23.3）。

只负责关系表的读写；**计数列的维护**在 `app/services/engagement_service.py`，
两者必须在同一事务内（契约 §23.3）。
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import delete, literal, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.timeutil import utcnow
from app.models.engagement import ToolFavorite, ToolLike
from app.models.tool import Tool

#: relation 名 → (关系模型, 计数列)。两个方向的形状完全对称（契约 §23.4）。
RELATIONS: dict[str, tuple[type[ToolFavorite] | type[ToolLike], InstrumentedAttribute[int]]] = {
    "favorite": (ToolFavorite, Tool.favorite_count),
    "like": (ToolLike, Tool.like_count),
}


async def relation_flags(
    session: AsyncSession, *, user_id: int | None, tool_ids: Iterable[int]
) -> tuple[set[int], set[int]]:
    """当前用户在**给定工具集合**上的 `(已收藏 id 集合, 已点赞 id 集合)`。

    实现约束（contracts §23.5）：**不得给列表查询加 JOIN**。这里用
    **一条 `UNION ALL`** 取回两组关系 —— 比两次独立查询少一次往返，
    也仍然只有一条语句。

    `tool_ids` 必须由调用方限定为**当前页/当前详情**的工具 id：
    分页列表之外的工具不应出现在结果里（任务书 B8），
    否则会退化成「把用户全部收藏都捞出来再在 Python 里筛」。

    匿名请求者（`user_id is None`）直接返回空集，**不查库**。
    """
    ids = list(tool_ids)
    if user_id is None or not ids:
        return set(), set()

    favorite_rows = select(
        ToolFavorite.tool_id.label("tool_id"), literal("favorite").label("kind")
    ).where(ToolFavorite.user_id == user_id, ToolFavorite.tool_id.in_(ids))
    like_rows = select(
        ToolLike.tool_id.label("tool_id"), literal("like").label("kind")
    ).where(ToolLike.user_id == user_id, ToolLike.tool_id.in_(ids))

    rows = (await session.execute(favorite_rows.union_all(like_rows))).all()
    favorites = {int(row[0]) for row in rows if row[1] == "favorite"}
    likes = {int(row[0]) for row in rows if row[1] == "like"}
    return favorites, likes


async def relation_exists(
    session: AsyncSession, *, relation: str, user_id: int, tool_id: int
) -> bool:
    model, _column = RELATIONS[relation]
    result = await session.execute(
        select(model.id).where(model.user_id == user_id, model.tool_id == tool_id)
    )
    return result.first() is not None


async def insert_relation(
    session: AsyncSession, *, relation: str, user_id: int, tool_id: int
) -> None:
    """插入关系行并 flush。

    **可能抛 `IntegrityError`**（`UNIQUE(user_id, tool_id)`）—— 并发重复 `PUT`
    时后到的那个会撞约束。调用方（服务层）负责捕获并按「已收藏」处理，
    绝不把它变成 500（契约 §23.4）。
    """
    model, _column = RELATIONS[relation]
    session.add(model(user_id=user_id, tool_id=tool_id, created_at=utcnow()))
    await session.flush()


async def delete_relation(
    session: AsyncSession, *, relation: str, user_id: int, tool_id: int
) -> int:
    """删除关系行，返回**实际删掉的行数**。

    用返回行数而不是「先查再删」：两个并发 `DELETE` 都查到行时，
    只有一个能删掉（rowcount=1），另一个 rowcount=0 —— 计数因此只减一次。
    """
    model, _column = RELATIONS[relation]
    result = await session.execute(
        delete(model).where(model.user_id == user_id, model.tool_id == tool_id)
    )
    return int(result.rowcount or 0)


__all__ = [
    "RELATIONS",
    "delete_relation",
    "insert_relation",
    "relation_exists",
    "relation_flags",
]
