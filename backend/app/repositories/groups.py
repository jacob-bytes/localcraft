"""用户组仓储（docs/02 §3.4 §3.5）。

用户组**只用于可见性授权**，不承载角色权限（FR-GRP-03）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tool import Tool, ToolAcl
from app.models.user import Group, GroupMember, User


async def get_by_id(session: AsyncSession, group_id: int) -> Group | None:
    return await session.get(Group, group_id)


async def get_by_name(session: AsyncSession, name: str) -> Group | None:
    result = await session.execute(select(Group).where(Group.name == name.strip()))
    return result.scalar_one_or_none()


async def list_groups(
    session: AsyncSession, *, q: str | None = None, limit: int = 200, offset: int = 0
) -> list[Group]:
    stmt = select(Group).order_by(Group.name.asc()).limit(limit).offset(offset)
    if q:
        stmt = stmt.where(Group.name.ilike(f"%{q}%"))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_groups(session: AsyncSession) -> int:
    return int(
        (await session.execute(select(func.count()).select_from(Group))).scalar_one()
    )


async def member_counts(session: AsyncSession, group_ids: list[int]) -> dict[int, int]:
    """批量取成员数，避免列表页 N+1。"""
    if not group_ids:
        return {}
    result = await session.execute(
        select(GroupMember.group_id, func.count(GroupMember.user_id))
        .where(GroupMember.group_id.in_(group_ids))
        .group_by(GroupMember.group_id)
    )
    return {int(r[0]): int(r[1]) for r in result.all()}


async def acl_references(
    session: AsyncSession, group_ids: list[int]
) -> dict[int, list[tuple[int, str]]]:
    """批量取「哪些工具的 ACL 引用了这些组」。

    FR-GRP-04：删除组前必须能列出引用它的工具。
    """
    if not group_ids:
        return {}
    result = await session.execute(
        select(ToolAcl.subject_id, Tool.id, Tool.name, Tool.slug)
        .join(Tool, Tool.id == ToolAcl.tool_id)
        .where(
            ToolAcl.subject_type == "group",
            ToolAcl.subject_id.in_(group_ids),
        )
        .order_by(Tool.id.asc())
    )
    out: dict[int, list[tuple[int, str]]] = {}
    for group_id, tool_id, name, _slug in result.all():
        out.setdefault(int(group_id), []).append((int(tool_id), str(name)))
    return out


async def delete_group(session: AsyncSession, group: Group) -> int:
    """删除组，并在**同一事务内**清理它在 `tool_acl` 里的悬空条目。

    `tool_acl.subject_id` 是多态外键（指向 users 或 groups），**数据库不管**，
    所以应用层必须自己清理 —— 否则会留下指向已删组的 ACL，
    可见性判定会永远命中不了却又占着索引（docs/02 §3.10 的取舍说明）。
    """
    result = await session.execute(
        sa_delete(ToolAcl).where(
            ToolAcl.subject_type == "group", ToolAcl.subject_id == group.id
        )
    )
    cleaned = int(result.rowcount or 0)
    await session.delete(group)
    await session.flush()
    return cleaned


# ---------------------------------------------------------------------------
# 成员
# ---------------------------------------------------------------------------
async def list_members(
    session: AsyncSession, group_id: int, *, q: str | None = None, limit: int = 200, offset: int = 0
) -> list[tuple[GroupMember, User, str | None]]:
    adder = User.__table__.alias("adder")
    stmt = (
        select(GroupMember, User, adder.c.display_name)
        .join(User, User.id == GroupMember.user_id)
        .outerjoin(adder, adder.c.id == GroupMember.added_by_id)
        .where(GroupMember.group_id == group_id)
        .order_by(GroupMember.added_at.desc(), User.username.asc())
        .limit(limit)
        .offset(offset)
    )
    if q:
        stmt = stmt.where(User.username.ilike(f"%{q}%") | User.display_name.ilike(f"%{q}%"))
    result = await session.execute(stmt)
    return [(row[0], row[1], row[2]) for row in result.all()]


async def count_members(session: AsyncSession, group_id: int) -> int:
    result = await session.execute(
        select(func.count()).select_from(GroupMember).where(GroupMember.group_id == group_id)
    )
    return int(result.scalar_one())


async def existing_member_ids(session: AsyncSession, group_id: int) -> set[int]:
    result = await session.execute(
        select(GroupMember.user_id).where(GroupMember.group_id == group_id)
    )
    return {int(r[0]) for r in result.all()}


async def add_members(
    session: AsyncSession,
    group_id: int,
    user_ids: list[int],
    *,
    added_by_id: int | None,
    now: datetime,
) -> int:
    """批量添加（一次事务，不逐条提交 —— 易错点 2）。"""
    existing = await existing_member_ids(session, group_id)
    added = 0
    for user_id in user_ids:
        if user_id in existing:
            continue
        session.add(
            GroupMember(
                group_id=group_id, user_id=user_id, added_by_id=added_by_id, added_at=now
            )
        )
        added += 1
    await session.flush()
    return added


async def remove_member(session: AsyncSession, group_id: int, user_id: int) -> bool:
    row = await session.get(GroupMember, {"group_id": group_id, "user_id": user_id})
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    return True


__all__ = [
    "acl_references",
    "add_members",
    "count_groups",
    "count_members",
    "delete_group",
    "existing_member_ids",
    "get_by_id",
    "get_by_name",
    "list_groups",
    "list_members",
    "member_counts",
    "remove_member",
]
