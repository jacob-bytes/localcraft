"""API Token 仓储（docs/02 §3.15）。

**明文不落库、不打日志**（FR-API-02）：库里只有 SHA256 与可读前缀。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import api_token_prefix, hash_api_token
from app.models.user import ApiToken, User


async def create(
    session: AsyncSession,
    *,
    name: str,
    plaintext: str,
    scopes: list[str],
    created_by_id: int,
    expires_at: datetime | None,
    now: datetime,
) -> ApiToken:
    row = ApiToken(
        name=name,
        token_prefix=api_token_prefix(plaintext),
        token_hash=hash_api_token(plaintext),
        scopes=list(scopes),
        created_by_id=created_by_id,
        expires_at=expires_at,
        created_at=now,
    )
    session.add(row)
    await session.flush()
    return row


async def get_by_id(session: AsyncSession, token_id: int) -> ApiToken | None:
    return await session.get(ApiToken, token_id)


async def list_all(
    session: AsyncSession, *, include_revoked: bool = True, limit: int = 200, offset: int = 0
) -> list[ApiToken]:
    stmt = select(ApiToken).order_by(ApiToken.created_at.desc(), ApiToken.id.desc())
    if not include_revoked:
        stmt = stmt.where(ApiToken.revoked_at.is_(None))
    result = await session.execute(stmt.limit(limit).offset(offset))
    return list(result.scalars().unique().all())


async def count_all(session: AsyncSession, *, include_revoked: bool = True) -> int:
    stmt = select(func.count()).select_from(ApiToken)
    if not include_revoked:
        stmt = stmt.where(ApiToken.revoked_at.is_(None))
    return int((await session.execute(stmt)).scalar_one())


async def count_active_for_user(session: AsyncSession, user_id: int, *, now: datetime) -> int:
    """某用户仍在有效期内的 Token 数（禁用用户前提示用）。"""
    result = await session.execute(
        select(func.count())
        .select_from(ApiToken)
        .where(
            ApiToken.created_by_id == user_id,
            ApiToken.revoked_at.is_(None),
            (ApiToken.expires_at.is_(None)) | (ApiToken.expires_at > now),
        )
    )
    return int(result.scalar_one())


async def count_active(session: AsyncSession, *, now: datetime) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(ApiToken)
        .where(
            ApiToken.revoked_at.is_(None),
            (ApiToken.expires_at.is_(None)) | (ApiToken.expires_at > now),
        )
    )
    return int(result.scalar_one())


async def revoke(
    session: AsyncSession, row: ApiToken, *, revoked_by_id: int | None, now: datetime
) -> None:
    row.revoked_at = now
    row.revoked_by_id = revoked_by_id
    await session.flush()


async def revoke_all_for_user(
    session: AsyncSession, user_id: int, *, revoked_by_id: int | None, now: datetime
) -> int:
    """一键吊销某用户的全部 Token（docs/05 §13.8 的轮换要求）。

    禁用用户时也要调用它 —— 否则 Token 会继续可用（FR-IAM-05）。
    """
    result = await session.execute(
        select(ApiToken).where(
            ApiToken.created_by_id == user_id, ApiToken.revoked_at.is_(None)
        )
    )
    rows = list(result.scalars().all())
    for row in rows:
        row.revoked_at = now
        row.revoked_by_id = revoked_by_id
    await session.flush()
    return len(rows)


async def delete_row(session: AsyncSession, row: ApiToken) -> None:
    """删除 Token 记录（仅超管，用于清理历史条目）。

    注意与「吊销」的区别：吊销保留审计痕迹，删除是物理移除。
    """
    await session.delete(row)
    await session.flush()


__all__ = [
    "User",
    "count_active",
    "count_active_for_user",
    "count_all",
    "create",
    "delete_row",
    "get_by_id",
    "list_all",
    "revoke",
    "revoke_all_for_user",
]
