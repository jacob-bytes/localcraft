"""用户仓储。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import normalize_username
from app.models.enums import UserStatus
from app.models.user import Role, User, UserRole


async def get_by_id(session: AsyncSession, user_id: int) -> User | None:
    result = await session.execute(
        select(User).options(selectinload(User.roles)).where(User.id == user_id)
    )
    return result.scalar_one_or_none()


async def get_by_username(session: AsyncSession, username: str) -> User | None:
    """按**归一化**用户名查询（契约 §3.3：用户名大小写不敏感）。"""
    normalized = normalize_username(username)
    result = await session.execute(
        select(User).options(selectinload(User.roles)).where(User.username == normalized)
    )
    return result.scalar_one_or_none()


async def list_users(session: AsyncSession, *, limit: int = 500, offset: int = 0) -> list[User]:
    result = await session.execute(
        select(User).options(selectinload(User.roles)).order_by(User.id).limit(limit).offset(offset)
    )
    return list(result.scalars().all())


async def count_users(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(User))
    return int(result.scalar_one())


async def count_active_superadmins(session: AsyncSession) -> int:
    """启用状态的超管数量 —— 用于 `LAST_SUPERADMIN` 校验（FR-IAM-07）。"""
    result = await session.execute(
        select(func.count(func.distinct(User.id)))
        .select_from(User)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(Role.code == "superadmin", User.status == UserStatus.ACTIVE.value)
    )
    return int(result.scalar_one())


async def get_role_by_code(session: AsyncSession, code: str) -> Role | None:
    result = await session.execute(select(Role).where(Role.code == code))
    return result.scalar_one_or_none()


async def group_ids_for_user(session: AsyncSession, user_id: int) -> list[int]:
    """用户所属的用户组 id 列表。

    docs/02 §4 Q2 优化建议 1：一个用户通常只属于个位数个组，先在应用层查出来
    内联成 `IN (...)`，比在可见性查询里做关联子查询快。
    """
    from app.models.user import GroupMember  # 局部导入避免循环

    result = await session.execute(
        select(GroupMember.group_id).where(GroupMember.user_id == user_id)
    )
    return [int(row[0]) for row in result.all()]


async def reset_login_failures(session: AsyncSession, user: User, *, now: datetime) -> None:
    user.failed_login_count = 0
    user.locked_until = None
    user.updated_at = now


async def record_login_failure(
    session: AsyncSession, user: User, *, now: datetime, max_failures: int, lockout_minutes: int
) -> None:
    """失败计数 +1；达阈值则设置 `locked_until`（FR-AUTH-06）。"""
    from datetime import timedelta

    user.failed_login_count = (user.failed_login_count or 0) + 1
    if user.failed_login_count >= max_failures:
        user.locked_until = now + timedelta(minutes=lockout_minutes)
    user.updated_at = now


async def record_login_success(
    session: AsyncSession, user: User, *, now: datetime, ip: str | None
) -> None:
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    user.last_login_ip = ip
    user.updated_at = now
