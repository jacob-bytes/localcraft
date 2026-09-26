"""收藏 / 点赞服务（M8 / contracts §23.4）。

四个端点（`PUT`/`DELETE` × `{favorite, like}`）**全部幂等**：

| 场景 | 行为 |
| --- | --- |
| 重复 `PUT` | 成功；计数**只加一次** |
| 未收藏时 `DELETE` | 成功；**不报 404** |
| 并发重复 `PUT` | 靠 `UNIQUE(user_id, tool_id)` 兜底：撞唯一约束后按「已存在」处理，不回 500 |

计数维护与关系表写入在**同一事务**内完成（契约 §23.3），
且计数**不允许为负**（删减时用 `CASE WHEN count > 0` 夹到 0）。
"""

from __future__ import annotations

from sqlalchemy import case, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tool import Tool
from app.repositories import engagement as engagement_repo
from app.repositories.engagement import RELATIONS
from app.schemas.tool import ToolEngagementResponse

#: 合法的 relation 名（与路径一一对应）
VALID_RELATIONS: frozenset[str] = frozenset(RELATIONS)


async def _bump(
    session: AsyncSession, *, relation: str, tool_id: int, delta: int
) -> None:
    """在**当前事务内**改计数列。

    - 加：`count = count + 1`
    - 减：`count = CASE WHEN count > 0 THEN count - 1 ELSE 0 END`

    减法必须夹到 0（契约 §23.4：「计数不允许出现负数」）。不用
    `func.max(count - 1, 0)` —— SQLite 的 `max(a,b)` 是标量函数而 PG 的同名
    聚合语义不同（PG 要用 `GREATEST`），`CASE` 是两边唯一一致的写法。
    """
    _model, count_column = RELATIONS[relation]
    if delta > 0:
        expression = count_column + delta
    else:
        expression = case((count_column > 0, count_column - 1), else_=0)
    await session.execute(
        update(Tool).where(Tool.id == tool_id).values({count_column.key: expression})
    )


async def _snapshot(
    session: AsyncSession, *, user_id: int, tool_id: int, slug: str
) -> ToolEngagementResponse:
    """提交之后读回**权威值**（计数可能包含并发请求的写入，以服务端为准）。"""
    row = (
        await session.execute(
            select(Tool.favorite_count, Tool.like_count).where(Tool.id == tool_id)
        )
    ).one()
    favorites, likes = await engagement_repo.relation_flags(
        session, user_id=user_id, tool_ids=[tool_id]
    )
    return ToolEngagementResponse(
        tool_id=tool_id,
        slug=slug,
        is_favorited=tool_id in favorites,
        is_liked=tool_id in likes,
        favorite_count=int(row[0]),
        like_count=int(row[1]),
    )


async def set_engagement(
    session: AsyncSession,
    *,
    tool_id: int,
    slug: str,
    user_id: int,
    relation: str,
    enabled: bool,
) -> ToolEngagementResponse:
    """`enabled=True` → 幂等收藏/点赞；`enabled=False` → 幂等取消。

    调用方负责先做**可见性判定**（不可见的工具 404，不泄露存在性），
    并把 `tool_id` / `slug` 提取成标量后再进来 —— 本函数内部可能
    `rollback()`，那会让传进来的 ORM 实例过期，再去读它的属性会炸。
    """
    if relation not in RELATIONS:  # pragma: no cover - 路由层已限定取值
        raise ValueError(f"未知的 relation: {relation}")

    if enabled:
        exists = await engagement_repo.relation_exists(
            session, relation=relation, user_id=user_id, tool_id=tool_id
        )
        if not exists:
            try:
                await engagement_repo.insert_relation(
                    session, relation=relation, user_id=user_id, tool_id=tool_id
                )
            except IntegrityError:
                # 并发重复 PUT：另一个请求先插进去了。回滚本次插入，
                # **不加计数**（对方的事务已经加过），按成功处理。
                # 这里绝不能向上抛 —— 契约 §23.4 明确要求「不要让它变成 500」。
                await session.rollback()
                return await _snapshot(
                    session, user_id=user_id, tool_id=tool_id, slug=slug
                )
            await _bump(session, relation=relation, tool_id=tool_id, delta=1)
        await session.commit()
    else:
        deleted = await engagement_repo.delete_relation(
            session, relation=relation, user_id=user_id, tool_id=tool_id
        )
        # 只有真的删掉了行才减计数：并发 DELETE 时另一个请求 rowcount=0，
        # 计数因此只减一次。
        if deleted:
            await _bump(session, relation=relation, tool_id=tool_id, delta=-1)
        # 未收藏时 DELETE 也是成功（幂等），不报 404。
        await session.commit()

    return await _snapshot(session, user_id=user_id, tool_id=tool_id, slug=slug)


__all__ = ["VALID_RELATIONS", "set_engagement"]
