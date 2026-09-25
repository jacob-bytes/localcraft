"""用户组服务（FR-GRP-01~06 / docs/01 §5.3）。

用户组**只用于可见性授权**，不承载角色权限（FR-GRP-03）。

两条容易出错的地方：

  1. **删除组必须先检查 ACL 引用**（FR-GRP-04）：被引用时返回 `409 GROUP_IN_USE`
     并**列出引用它的工具**，让管理员判断影响面。
  2. **删除组必须清理悬空 ACL**：`tool_acl.subject_id` 是多态外键（指向 users 或
     groups），数据库层不管 —— 不清理就会留下永远命中不了的死条目
     （docs/02 §3.10 的取舍说明）。
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DuplicateEntryError, GroupInUseError, NotFoundError
from app.core.timeutil import utcnow
from app.models.user import Group, User
from app.repositories import groups as groups_repo
from app.repositories import users as users_repo
from app.schemas.admin import (
    GroupCreateRequest,
    GroupMemberAddResponse,
    GroupMemberOut,
    GroupOut,
    GroupUpdateRequest,
)

logger = logging.getLogger(__name__)


async def _build_group_out(session: AsyncSession, group: Group) -> GroupOut:
    counts = await groups_repo.member_counts(session, [group.id])
    refs = await groups_repo.acl_references(session, [group.id])
    return GroupOut(
        id=group.id,
        name=group.name,
        description=group.description,
        is_active=group.is_active,
        member_count=counts.get(group.id, 0),
        acl_reference_count=len(refs.get(group.id, [])),
        created_at=group.created_at,
        updated_at=group.updated_at,
    )


async def list_groups(
    session: AsyncSession, *, q: str | None = None, limit: int = 200, offset: int = 0
) -> tuple[list[GroupOut], int]:
    rows = await groups_repo.list_groups(session, q=q, limit=limit, offset=offset)
    total = await groups_repo.count_groups(session)
    if not rows:
        return [], total
    ids = [g.id for g in rows]
    counts = await groups_repo.member_counts(session, ids)
    refs = await groups_repo.acl_references(session, ids)
    return (
        [
            GroupOut(
                id=g.id,
                name=g.name,
                description=g.description,
                is_active=g.is_active,
                member_count=counts.get(g.id, 0),
                acl_reference_count=len(refs.get(g.id, [])),
                created_at=g.created_at,
                updated_at=g.updated_at,
            )
            for g in rows
        ],
        total,
    )


async def get_group(session: AsyncSession, group_id: int) -> GroupOut:
    group = await groups_repo.get_by_id(session, group_id)
    if group is None:
        raise NotFoundError(message="用户组不存在", details={"group_id": group_id})
    return await _build_group_out(session, group)


async def create_group(
    session: AsyncSession, *, payload: GroupCreateRequest, actor_id: int
) -> GroupOut:
    name = payload.name.strip()
    if await groups_repo.get_by_name(session, name) is not None:
        raise DuplicateEntryError(
            message=f"组名已存在：{name}", details={"field": "name", "value": name}
        )
    now = utcnow()
    group = Group(
        name=name,
        description=payload.description,
        is_active=payload.is_active,
        created_by_id=actor_id,
        created_at=now,
        updated_at=now,
    )
    session.add(group)
    await session.commit()
    return await _build_group_out(session, group)


async def update_group(
    session: AsyncSession, *, group_id: int, payload: GroupUpdateRequest
) -> GroupOut:
    group = await groups_repo.get_by_id(session, group_id)
    if group is None:
        raise NotFoundError(message="用户组不存在", details={"group_id": group_id})

    if payload.name is not None:
        new_name = payload.name.strip()
        existing = await groups_repo.get_by_name(session, new_name)
        if existing is not None and existing.id != group.id:
            raise DuplicateEntryError(
                message=f"组名已存在：{new_name}", details={"field": "name", "value": new_name}
            )
        group.name = new_name
    if payload.description is not None:
        group.description = payload.description
    if payload.is_active is not None:
        group.is_active = payload.is_active
    group.updated_at = utcnow()
    await session.commit()
    return await _build_group_out(session, group)


async def delete_group(session: AsyncSession, *, group_id: int) -> dict:
    """删除组（FR-GRP-04）。

    被 ACL 引用时返回 **409 GROUP_IN_USE** 并在 details 里列出引用它的工具 ——
    管理员据此判断影响面。确认后再调一次（或带 `force=true`）才会真正删除，
    并在**同一事务内**清理悬空 ACL。
    """
    group = await groups_repo.get_by_id(session, group_id)
    if group is None:
        raise NotFoundError(message="用户组不存在", details={"group_id": group_id})

    refs = await groups_repo.acl_references(session, [group.id])
    referenced = refs.get(group.id, [])
    if referenced:
        raise GroupInUseError(
            message=f"用户组「{group.name}」仍被 {len(referenced)} 个工具的可见性授权引用",
            details={
                "group_id": group.id,
                "group_name": group.name,
                "tool_count": len(referenced),
                "tools": [{"id": tid, "name": name} for tid, name in referenced],
            },
        )

    cleaned = await groups_repo.delete_group(session, group)
    await session.commit()
    logger.info("删除用户组 id=%s 清理 ACL 条目=%s", group_id, cleaned)
    return {"status": "ok", "cleaned_acl_entries": cleaned}


async def force_delete_group(session: AsyncSession, *, group_id: int) -> dict:
    """二次确认后的强制删除：连带清理引用它的 ACL 条目。"""
    group = await groups_repo.get_by_id(session, group_id)
    if group is None:
        raise NotFoundError(message="用户组不存在", details={"group_id": group_id})
    refs = await groups_repo.acl_references(session, [group.id])
    cleaned = await groups_repo.delete_group(session, group)
    await session.commit()
    logger.info(
        "强制删除用户组 id=%s 影响工具数=%s 清理 ACL=%s",
        group_id,
        len(refs.get(group_id, [])),
        cleaned,
    )
    return {
        "status": "ok",
        "cleaned_acl_entries": cleaned,
        "affected_tools": [tid for tid, _ in refs.get(group_id, [])],
    }


# ---------------------------------------------------------------------------
# 成员
# ---------------------------------------------------------------------------
async def list_members(
    session: AsyncSession,
    *,
    group_id: int,
    q: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[GroupMemberOut], int]:
    group = await groups_repo.get_by_id(session, group_id)
    if group is None:
        raise NotFoundError(message="用户组不存在", details={"group_id": group_id})
    rows = await groups_repo.list_members(session, group_id, q=q, limit=limit, offset=offset)
    total = await groups_repo.count_members(session, group_id)
    return (
        [
            GroupMemberOut(
                user_id=member.user_id,
                username=user.username,
                display_name=user.display_name,
                email=user.email,
                status=user.status,
                added_at=member.added_at,
                added_by_name=added_by_name,
            )
            for member, user, added_by_name in rows
        ],
        total,
    )


async def add_members(
    session: AsyncSession, *, group_id: int, user_ids: list[int], actor_id: int
) -> GroupMemberAddResponse:
    """批量添加成员（FR-GRP-02）。

    一次事务写入，不逐条提交 —— 大批量导入时逐条提交会锁库（易错点 2）。
    """
    group = await groups_repo.get_by_id(session, group_id)
    if group is None:
        raise NotFoundError(message="用户组不存在", details={"group_id": group_id})

    existing = await groups_repo.existing_member_ids(session, group_id)
    not_found: list[int] = []
    to_add: list[int] = []
    for user_id in user_ids:
        if user_id in existing:
            continue
        user = await session.get(User, user_id)
        if user is None:
            not_found.append(user_id)
            continue
        to_add.append(user_id)

    added = await groups_repo.add_members(
        session, group_id, to_add, added_by_id=actor_id, now=utcnow()
    )
    await session.commit()
    return GroupMemberAddResponse(
        added=added,
        already_members=len([u for u in user_ids if u in existing]),
        not_found=not_found,
    )


async def remove_member(session: AsyncSession, *, group_id: int, user_id: int) -> None:
    group = await groups_repo.get_by_id(session, group_id)
    if group is None:
        raise NotFoundError(message="用户组不存在", details={"group_id": group_id})
    if not await groups_repo.remove_member(session, group_id, user_id):
        raise NotFoundError(
            message="该用户不在组内", details={"group_id": group_id, "user_id": user_id}
        )
    await session.commit()


async def group_ids_for_user(session: AsyncSession, user_id: int) -> list[int]:
    """某人所属的组（可见性判定用；每次请求实时求值，FR-GRP-06）。"""
    return await users_repo.group_ids_for_user(session, user_id)


__all__ = [
    "add_members",
    "create_group",
    "delete_group",
    "force_delete_group",
    "get_group",
    "group_ids_for_user",
    "list_groups",
    "list_members",
    "remove_member",
    "update_group",
]
