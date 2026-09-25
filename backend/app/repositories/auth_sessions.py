"""登录会话仓储（refresh token 的服务端可吊销状态）。"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_refresh_token
from app.models.user import AuthSession


async def create(
    session: AsyncSession,
    *,
    user_id: int,
    refresh_token: str,
    expires_at: datetime,
    user_agent: str | None,
    ip: str | None,
    now: datetime,
) -> AuthSession:
    row = AuthSession(
        user_id=user_id,
        refresh_token_hash=hash_refresh_token(refresh_token),
        user_agent=(user_agent or None) and user_agent[:255],
        ip=ip,
        expires_at=expires_at,
        created_at=now,
    )
    session.add(row)
    await session.flush()
    return row


async def get_by_token(session: AsyncSession, refresh_token: str) -> AuthSession | None:
    result = await session.execute(
        select(AuthSession).where(
            AuthSession.refresh_token_hash == hash_refresh_token(refresh_token)
        )
    )
    return result.scalar_one_or_none()


async def revoke(session: AsyncSession, row: AuthSession, *, now: datetime) -> None:
    row.revoked_at = now
    await session.flush()


async def revoke_all_for_user(
    session: AsyncSession, user_id: int, *, now: datetime, except_session_id: int | None = None
) -> int:
    """吊销某用户的全部（或除某条外的）会话。

    改密后调用，保证其他设备上的会话立即失效（FR-AUTH-07、FR-IAM-05）。
    """
    stmt = (
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    if except_session_id is not None:
        stmt = stmt.where(AuthSession.id != except_session_id)
    result = await session.execute(stmt)
    return int(result.rowcount or 0)


def is_usable(row: AuthSession, *, now: datetime) -> bool:
    if row.revoked_at is not None:
        return False
    expires = row.expires_at
    if expires.tzinfo is None:
        from datetime import UTC

        expires = expires.replace(tzinfo=UTC)
    return expires > now


async def touch(session: AsyncSession, row: AuthSession, *, now: datetime) -> None:
    """记录 refresh 的使用时间。

    注意：这是**每次刷新一次**的写操作，不是每请求一次 —— 与 docs/02 §3.15
    对 `api_tokens.last_used_at` 要求的「内存聚合批量落库」不同，
    refresh 本来就是低频动作（access token 30 分钟一换），逐次写是可接受的。
    """
    row.last_used_at = now
    await session.flush()


async def purge_expired(session: AsyncSession, *, now: datetime, older_than_days: int = 30) -> int:
    """清理过期超 30 天的会话行（docs/02 §5，M1 仅提供函数，由定时任务调用）。"""
    cutoff = now - timedelta(days=older_than_days)
    result = await session.execute(
        select(AuthSession).where(AuthSession.expires_at < cutoff)
    )
    rows = list(result.scalars().all())
    for row in rows:
        await session.delete(row)
    return len(rows)
