"""审批记录与免审白名单仓储。

`approval_records` 是**只插入、不更新、不删除**的流水表（docs/02 §3.13），
所以这里只有 `insert` 与查询，没有 update/delete。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.approval import ApprovalRecord
from app.models.enums import ApprovalAction, ToolStatus
from app.models.tool import Tool
from app.models.user import ApprovalWhitelist, GroupMember, User


async def insert_record(
    session: AsyncSession,
    *,
    tool_id: int,
    version_id: int | None,
    action: ApprovalAction,
    from_status: str | None,
    to_status: str,
    version_from_status: str | None = None,
    version_to_status: str | None = None,
    actor_id: int | None,
    actor_label: str,
    is_automatic: bool = False,
    auto_rule: str | None = None,
    reason: str | None = None,
    note: str | None = None,
    request_id: str | None = None,
    now: datetime,
) -> ApprovalRecord:
    """写一条审批流水。

    `actor_label` 存的是**当时的显示名快照** —— 用户之后改名，
    历史记录仍要还原当时的称呼（docs/02 §3.13）。
    """
    record = ApprovalRecord(
        tool_id=tool_id,
        version_id=version_id,
        action=action.value,
        from_status=from_status,
        to_status=to_status,
        version_from_status=version_from_status,
        version_to_status=version_to_status,
        actor_id=actor_id,
        actor_label=actor_label,
        is_automatic=is_automatic,
        auto_rule=auto_rule,
        reason=reason,
        note=note,
        request_id=request_id,
        created_at=now,
    )
    session.add(record)
    await session.flush()
    return record


# ---------------------------------------------------------------------------
# 审批队列
# ---------------------------------------------------------------------------
def _queue_filters(
    *,
    status: str | None,
    tool_type: str | None,
    owner_username: str | None,
) -> list:
    clauses = [Tool.deleted_at.is_(None)]
    if status == ToolStatus.PENDING.value:
        clauses.append(Tool.status == ToolStatus.PENDING.value)
    elif status == ToolStatus.PENDING_UPDATE.value:
        clauses.append(Tool.status == ToolStatus.PENDING_UPDATE.value)
    elif status == "pending_all":
        clauses.append(
            Tool.status.in_([ToolStatus.PENDING.value, ToolStatus.PENDING_UPDATE.value])
        )
    elif status:
        clauses.append(Tool.status == status)
    else:
        # 默认只看待处理
        clauses.append(
            Tool.status.in_([ToolStatus.PENDING.value, ToolStatus.PENDING_UPDATE.value])
        )
    if tool_type:
        clauses.append(Tool.tool_type == tool_type)
    if owner_username:
        clauses.append(
            Tool.owner_id.in_(select(User.id).where(User.username == owner_username))
        )
    return clauses


async def list_queue(
    session: AsyncSession,
    *,
    status: str | None = None,
    tool_type: str | None = None,
    owner_username: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[Tool]:
    """审批队列：**按提交时间正序**（FR-APPR-05，先提交先处理）。

    「提交时间」用 `updated_at` —— 每次提交/重提都会刷新它。
    """
    result = await session.execute(
        select(Tool)
        .where(*_queue_filters(status=status, tool_type=tool_type, owner_username=owner_username))
        .order_by(Tool.updated_at.asc(), Tool.id.asc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().unique().all())


async def count_queue(
    session: AsyncSession,
    *,
    status: str | None = None,
    tool_type: str | None = None,
    owner_username: str | None = None,
) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(Tool)
        .where(*_queue_filters(status=status, tool_type=tool_type, owner_username=owner_username))
    )
    return int(result.scalar_one())


async def count_pending(session: AsyncSession) -> int:
    """待审总数 —— 切换 `auto_approve_all` 时的 warnings 用它（FR-APPR-02）。"""
    result = await session.execute(
        select(func.count())
        .select_from(Tool)
        .where(
            Tool.deleted_at.is_(None),
            Tool.status.in_([ToolStatus.PENDING.value, ToolStatus.PENDING_UPDATE.value]),
        )
    )
    return int(result.scalar_one())


async def list_history(
    session: AsyncSession,
    *,
    tool_id: int | None = None,
    actor_id: int | None = None,
    action: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[ApprovalRecord]:
    result = await session.execute(
        select(ApprovalRecord)
        .where(
            *_history_filters(
                tool_id=tool_id,
                actor_id=actor_id,
                action=action,
                date_from=date_from,
                date_to=date_to,
            )
        )
        .order_by(ApprovalRecord.created_at.desc(), ApprovalRecord.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def count_history(
    session: AsyncSession,
    *,
    tool_id: int | None = None,
    actor_id: int | None = None,
    action: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(ApprovalRecord)
        .where(
            *_history_filters(
                tool_id=tool_id,
                actor_id=actor_id,
                action=action,
                date_from=date_from,
                date_to=date_to,
            )
        )
    )
    return int(result.scalar_one())


def _history_filters(
    *,
    tool_id: int | None,
    actor_id: int | None,
    action: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
) -> list:
    clauses = []
    if tool_id is not None:
        clauses.append(ApprovalRecord.tool_id == tool_id)
    if actor_id is not None:
        clauses.append(ApprovalRecord.actor_id == actor_id)
    if action:
        clauses.append(ApprovalRecord.action == action)
    if date_from is not None:
        clauses.append(ApprovalRecord.created_at >= date_from)
    if date_to is not None:
        clauses.append(ApprovalRecord.created_at <= date_to)
    return clauses


async def latest_submission_times(
    session: AsyncSession, tool_ids: Sequence[int]
) -> dict[int, datetime]:
    """批量取每个工具最近一次 `submit`/`resubmit` 的时间。

    一次查询搞定，避免审批队列里对每条做一次 N+1 查询。
    """
    if not tool_ids:
        return {}
    result = await session.execute(
        select(ApprovalRecord.tool_id, func.max(ApprovalRecord.created_at))
        .where(
            ApprovalRecord.tool_id.in_(list(tool_ids)),
            ApprovalRecord.action.in_(
                [ApprovalAction.SUBMIT.value, ApprovalAction.RESUBMIT.value]
            ),
        )
        .group_by(ApprovalRecord.tool_id)
    )
    return {int(row[0]): row[1] for row in result.all()}


# ---------------------------------------------------------------------------
# 免审白名单
# ---------------------------------------------------------------------------
async def get_whitelist_entry(session: AsyncSession, user_id: int) -> ApprovalWhitelist | None:
    return await session.get(ApprovalWhitelist, user_id)


async def list_whitelist(
    session: AsyncSession,
) -> list[tuple[ApprovalWhitelist, User, str | None]]:
    """左连用户与添加人，一次查完，避免 N+1。

    第三个元素是**添加人的显示名**（不是 User 对象）——
    用别名表时 `row.adder.display_name` 取不到值，必须显式选出列。
    """
    adder = User.__table__.alias("adder")
    result = await session.execute(
        select(ApprovalWhitelist, User, adder.c.display_name)
        .join(User, User.id == ApprovalWhitelist.user_id)
        .outerjoin(adder, adder.c.id == ApprovalWhitelist.added_by_id)
        .order_by(ApprovalWhitelist.created_at.desc())
    )
    return [(row[0], row[1], row[2]) for row in result.all()]


async def add_whitelist_entry(
    session: AsyncSession,
    *,
    user_id: int,
    reason: str | None,
    added_by_id: int | None,
    expires_at: datetime | None,
    now: datetime,
) -> ApprovalWhitelist:
    """新增或覆盖（一个用户最多一条 —— 主键就是 user_id）。"""
    existing = await session.get(ApprovalWhitelist, user_id)
    if existing is not None:
        existing.reason = reason
        existing.added_by_id = added_by_id
        existing.expires_at = expires_at
        await session.flush()
        return existing
    row = ApprovalWhitelist(
        user_id=user_id,
        reason=reason,
        added_by_id=added_by_id,
        expires_at=expires_at,
        created_at=now,
    )
    session.add(row)
    await session.flush()
    return row


async def remove_whitelist_entry(session: AsyncSession, user_id: int) -> bool:
    row = await session.get(ApprovalWhitelist, user_id)
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    return True


async def is_whitelisted(session: AsyncSession, user_id: int, *, now: datetime) -> bool:
    """是否在有效期内命中白名单。"""
    row = await session.get(ApprovalWhitelist, user_id)
    if row is None:
        return False
    if row.expires_at is None:
        return True
    expires = row.expires_at
    if expires.tzinfo is None:
        from datetime import UTC

        expires = expires.replace(tzinfo=UTC)
    return expires > now


async def group_ids_for_user(session: AsyncSession, user_id: int) -> list[int]:
    result = await session.execute(
        select(GroupMember.group_id).where(GroupMember.user_id == user_id)
    )
    return [int(row[0]) for row in result.all()]


async def exists_active_user(session: AsyncSession, user_id: int) -> bool:
    result = await session.execute(
        select(func.count())
        .select_from(User)
        .where(User.id == user_id, User.status == "active")
    )
    return int(result.scalar_one()) > 0


__all__ = [
    "add_whitelist_entry",
    "count_history",
    "count_pending",
    "count_queue",
    "exists_active_user",
    "get_whitelist_entry",
    "group_ids_for_user",
    "insert_record",
    "is_whitelisted",
    "latest_submission_times",
    "list_history",
    "list_queue",
    "list_whitelist",
    "remove_whitelist_entry",
]
