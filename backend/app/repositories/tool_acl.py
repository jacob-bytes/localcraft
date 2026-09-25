"""可见性授权（ACL）仓储（docs/02 §3.10）。

`tool_acl.subject_id` 是**多态外键**（指向 users.id 或 groups.id，不做数据库级 FK），
所以主体存在性校验必须在应用层做。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AclSubjectType
from app.models.tool import ToolAcl


async def list_for_tool(session: AsyncSession, tool_id: int) -> list[ToolAcl]:
    result = await session.execute(
        select(ToolAcl)
        .where(ToolAcl.tool_id == tool_id)
        .order_by(ToolAcl.subject_type.asc(), ToolAcl.subject_id.asc())
    )
    return list(result.scalars().all())


async def replace_all(
    session: AsyncSession,
    tool_id: int,
    *,
    entries: Sequence[tuple[str, int, bool]],
    created_by_id: int | None,
    now: datetime,
) -> list[ToolAcl]:
    """全量替换（FR-ACL-03）。

    先删后插。注意：**只有在显式调用本函数时才会删** ——
    工具切到 public/private 时调用方不会调用它，ACL 条目因此被保留
    （docs/03 §3.11 明确要求「保留但不生效」）。
    """
    await session.execute(sa_delete(ToolAcl).where(ToolAcl.tool_id == tool_id))
    created: list[ToolAcl] = []
    for subject_type, subject_id, can_download in entries:
        row = ToolAcl(
            tool_id=tool_id,
            subject_type=subject_type,
            subject_id=subject_id,
            can_download=can_download,
            created_by_id=created_by_id,
            created_at=now,
        )
        session.add(row)
        created.append(row)
    await session.flush()
    return created


async def subject_ids(
    session: AsyncSession, session_type: AclSubjectType
) -> set[int]:
    result = await session.execute(
        select(ToolAcl.subject_id).where(ToolAcl.subject_type == session_type.value)
    )
    return {int(row[0]) for row in result.all()}


async def count_for_tool(session: AsyncSession, tool_id: int) -> int:
    from sqlalchemy import func

    result = await session.execute(
        select(func.count()).select_from(ToolAcl).where(ToolAcl.tool_id == tool_id)
    )
    return int(result.scalar_one())
