"""API Token 服务（FR-API-01~05 / docs/02 §3.15 / docs/03 §3.12 / docs/05 §13.8）。

关键设计点：

  1. **明文只出现一次**：库里只有 SHA256 与可读前缀（FR-API-02）。
  2. **存 SHA256 而不是 Argon2**：Token 是 256 bit 高熵随机串，
     不存在被暴力破解的可能，不需要慢哈希；而鉴权是**每个请求**都要做的事，
     慢哈希会直接把吞吐打死。「高熵凭证用快哈希、低熵密码用慢哈希」是标准做法
     （docs/02 §3.15 已论证）。
  3. **Scope 必须是创建者权限的子集**（docs/03 §3.12）：防止管理员误签发
     超出自己权限的 Token。
  4. **`last_used_at` / `last_used_ip` 走内存聚合**，不逐请求 UPDATE ——
     这是 M1 就定下的硬约束（README「SQLite 风险说明」第 4 条）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError
from app.core.permissions import permissions_for_roles
from app.core.security import generate_api_token
from app.core.timeutil import ensure_utc, utcnow
from app.models.enums import ApiScope, RoleCode
from app.models.user import ApiToken, User
from app.repositories import api_tokens as tokens_repo
from app.schemas.admin import (
    ApiTokenCreateRequest,
    ApiTokenCreateResponse,
    ApiTokenOut,
)

logger = logging.getLogger(__name__)

#: Token 默认有效期（docs/05 §13.8：默认 90 天）
DEFAULT_TTL_DAYS = 90


async def _build_out(session: AsyncSession, row: ApiToken, *, now: datetime) -> ApiTokenOut:
    creator = await session.get(User, row.created_by_id) if row.created_by_id else None
    expires = ensure_utc(row.expires_at)
    return ApiTokenOut(
        id=row.id,
        name=row.name,
        token_prefix=row.token_prefix,
        scopes=list(row.scopes or []),
        created_by_id=row.created_by_id,
        created_by_name=creator.display_name if creator is not None else None,
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
        last_used_ip=row.last_used_ip,
        revoked_at=row.revoked_at,
        created_at=row.created_at,
        is_active=row.revoked_at is None and (expires is None or expires > now),
    )


async def list_tokens(
    session: AsyncSession, *, include_revoked: bool = True, limit: int = 200, offset: int = 0
) -> tuple[list[ApiTokenOut], int]:
    rows = await tokens_repo.list_all(
        session, include_revoked=include_revoked, limit=limit, offset=offset
    )
    total = await tokens_repo.count_all(session, include_revoked=include_revoked)
    now = utcnow()
    return [await _build_out(session, row, now=now) for row in rows], total


async def create_token(
    session: AsyncSession,
    *,
    payload: ApiTokenCreateRequest,
    creator: User,
    request_ip: str | None = None,
) -> ApiTokenCreateResponse:
    """签发 Token。`payload.expires_at` 未传 → 默认 90 天；显式传 null → 永不过期。"""
    del request_ip
    requested = [s.value for s in payload.scopes]
    creator_permissions = permissions_for_roles(creator.role_codes)

    # ---- Scope 必须是创建者权限的子集（docs/03 §3.12）----
    # `admin:all` 只在创建者确实是超管时才允许 —— 它是「全部权限」的简写，
    # 不能成为绕过创建者角色的后门。
    exceeds: list[str] = []
    for scope in requested:
        if scope == ApiScope.ADMIN_ALL.value:
            if RoleCode.SUPERADMIN.value not in creator.role_codes:
                exceeds.append(scope)
            continue
        if scope not in creator_permissions:
            exceeds.append(scope)
    if exceeds:
        raise ForbiddenError(
            f"不能签发超出自己权限的 Scope：{', '.join(sorted(exceeds))}",
            details={
                "exceeds": sorted(exceeds),
                "creator_permissions": sorted(creator_permissions),
            },
        )

    # `model_fields_set` 区分「未传」与「显式传 null」
    now = utcnow()
    if "expires_at" not in payload.model_fields_set:
        expires_at = now + timedelta(days=DEFAULT_TTL_DAYS)
    else:
        expires_at = ensure_utc(payload.expires_at)

    plaintext = generate_api_token()
    row = await tokens_repo.create(
        session,
        name=payload.name.strip(),
        plaintext=plaintext,
        scopes=requested,
        created_by_id=creator.id,
        expires_at=expires_at,
        now=now,
    )
    await session.commit()

    base = await _build_out(session, row, now=now)
    # 明文只在此处出现一次；绝不写日志（FR-API-02）
    logger.info(
        "签发 API Token id=%s prefix=%s scopes=%s by=%s",
        row.id,
        row.token_prefix,
        requested,
        creator.username,
    )
    return ApiTokenCreateResponse(**base.model_dump(), token=plaintext)


async def revoke_token(
    session: AsyncSession, *, token_id: int, actor_id: int
) -> ApiTokenOut:
    """吊销（FR-API-04：吊销后立即失效）。"""
    row = await tokens_repo.get_by_id(session, token_id)
    if row is None:
        raise NotFoundError(message="Token 不存在", details={"token_id": token_id})
    if row.revoked_at is None:
        await tokens_repo.revoke(session, row, revoked_by_id=actor_id, now=utcnow())
        await session.commit()
    return await _build_out(session, row, now=utcnow())


async def delete_token(session: AsyncSession, *, token_id: int) -> None:
    """物理删除 Token 记录（仅用于清理历史条目；日常用吊销）。"""
    row = await tokens_repo.get_by_id(session, token_id)
    if row is None:
        raise NotFoundError(message="Token 不存在", details={"token_id": token_id})
    await tokens_repo.delete_row(session, row)
    await session.commit()


def available_scopes(creator: User) -> list[str]:
    """当前创建者可签发的 Scope 列表（前端下拉框用）。"""
    permissions = permissions_for_roles(creator.role_codes)
    scopes = [s.value for s in ApiScope if s.value != ApiScope.ADMIN_ALL.value]
    allowed = [s for s in scopes if s in permissions]
    if RoleCode.SUPERADMIN.value in creator.role_codes:
        allowed.append(ApiScope.ADMIN_ALL.value)
    return sorted(allowed)


__all__ = [
    "DEFAULT_TTL_DAYS",
    "available_scopes",
    "create_token",
    "delete_token",
    "list_tokens",
    "revoke_token",
]
