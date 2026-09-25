"""认证服务 —— 登录、刷新、登出、改密。

事务边界在此层（docs/03 §6.3）：每个写操作在结束时显式 `commit`。
**事务内不做文件 IO / 哈希 / 网络**（README「SQLite 风险说明」第 3 条）——
密码哈希与 JWT 签发都是 CPU 操作，很快；真正慢的 IO（解压、下载）不在 M1。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    AccountDisabledError,
    AccountLockedError,
    InvalidCredentialsError,
    TokenExpiredError,
    TokenRevokedError,
    UnauthenticatedError,
    ValidationError,
)
from app.core.permissions import permissions_for_roles
from app.core.security import (
    create_access_token,
    dummy_verify,
    generate_refresh_token,
    hash_password,
    normalize_username,
    validate_password_strength,
    verify_password,
)
from app.core.timeutil import ensure_utc, utcnow
from app.models.enums import UserStatus
from app.models.user import User
from app.repositories import auth_sessions as sessions_repo
from app.repositories import users as users_repo
from app.schemas.auth import UserMe
from app.services import settings_service

logger = logging.getLogger(__name__)


@dataclass
class AuthResult:
    """登录 / 刷新的产物。"""

    access_token: str
    expires_in: int
    user: User
    refresh_token: str
    refresh_expires_at: datetime


def build_user_me(user: User) -> UserMe:
    """ORM 用户 → 响应对象。登录、refresh、`/auth/me` 共用，保证三处形状一致。"""
    roles = user.role_codes
    return UserMe(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        roles=roles,
        permissions=sorted(permissions_for_roles(roles)),
        must_change_password=bool(user.must_change_password),
        auth_source=user.auth_source,
        status=user.status,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


async def _issue(
    session: AsyncSession,
    user: User,
    *,
    user_agent: str | None,
    ip: str | None,
    policy: settings_service.SecurityPolicy,
) -> AuthResult:
    now = utcnow()
    access_token, expires_in = create_access_token(
        user_id=user.id,
        username=user.username,
        roles=user.role_codes,
        must_change_password=bool(user.must_change_password),
        expires_delta=timedelta(minutes=policy.access_token_minutes),
    )
    refresh_token = generate_refresh_token()
    refresh_expires_at = now + timedelta(days=policy.refresh_token_days)
    await sessions_repo.create(
        session,
        user_id=user.id,
        refresh_token=refresh_token,
        expires_at=refresh_expires_at,
        user_agent=user_agent,
        ip=ip,
        now=now,
    )
    return AuthResult(
        access_token=access_token,
        expires_in=expires_in,
        user=user,
        refresh_token=refresh_token,
        refresh_expires_at=refresh_expires_at,
    )


async def login(
    session: AsyncSession,
    *,
    username: str,
    password: str,
    user_agent: str | None = None,
    ip: str | None = None,
) -> AuthResult:
    """登录。

    顺序（docs/03 §3.2 的失败响应表）：
      1. 归一化用户名 → 查用户；查不到与密码错误返回**同一个** INVALID_CREDENTIALS
      2. 账号禁用 → 403 ACCOUNT_DISABLED
      3. 锁定期内 → 423 ACCOUNT_LOCKED（**即使密码正确也拒绝**，FR-AUTH-06）
      4. 验密失败 → 失败计数 +1，达阈值设 locked_until
      5. 成功 → 清失败计数 → 签 token → 写 auth_sessions
    """
    policy = await settings_service.get_security_policy(session)
    now = utcnow()
    normalized = normalize_username(username)

    user = await users_repo.get_by_username(session, normalized)
    if user is None:
        # 等时验密，抹平「用户不存在」与「密码错误」的响应时间差，
        # 否则可以用响应时间枚举用户名。
        dummy_verify()
        raise InvalidCredentialsError()

    # ---- 账号状态 ----
    if user.status != UserStatus.ACTIVE.value:
        raise AccountDisabledError()

    # ---- 锁定检查 ----
    locked_until = ensure_utc(user.locked_until)
    if locked_until is not None and locked_until > now:
        raise AccountLockedError(retry_after_seconds=int((locked_until - now).total_seconds()))

    # 锁定已过期 → 清空，让用户拿到全新的一组尝试机会
    if locked_until is not None and locked_until <= now:
        user.locked_until = None
        user.failed_login_count = 0

    # ---- 验密 ----
    if not verify_password(password, user.password_hash):
        await users_repo.record_login_failure(
            session,
            user,
            now=now,
            max_failures=policy.login_max_failures,
            lockout_minutes=policy.lockout_minutes,
        )
        await session.commit()
        # 第 N 次失败本身仍返回凭据错误；下一次请求（第 N+1 次）才会命中
        # 上面的锁定分支返回 423 —— 与契约 §8 验收第 12 条一致。
        raise InvalidCredentialsError()

    # ---- 成功 ----
    await users_repo.record_login_success(session, user, now=now, ip=ip)
    result = await _issue(session, user, user_agent=user_agent, ip=ip, policy=policy)
    await session.commit()
    logger.info("登录成功 user_id=%s username=%s", user.id, user.username)
    return result


async def refresh(
    session: AsyncSession,
    *,
    refresh_token: str | None,
    ip: str | None = None,
) -> AuthResult:
    """用 refresh Cookie 换新 access token。

    **必须同时返回 `user` 对象**（契约 §3.3）—— 前端靠它恢复用户态，
    避免额外再调 `/auth/me`。

    refresh token 本身**不轮换**（契约未要求；轮换会让并发刷新互相踩踏）。
    """
    if not refresh_token:
        raise UnauthenticatedError("缺少 refresh token")

    row = await sessions_repo.get_by_token(session, refresh_token)
    if row is None:
        # Cookie 来自别的环境或已被清理，视同未登录
        raise UnauthenticatedError("refresh token 无效")

    now = utcnow()
    if row.revoked_at is not None:
        raise TokenRevokedError("登录已失效，请重新登录")

    expires_at = ensure_utc(row.expires_at)
    if expires_at is None or expires_at <= now:
        raise TokenExpiredError("登录已过期，请重新登录")

    user = row.user
    if user is None:  # pragma: no cover - 外键保证不会发生
        raise UnauthenticatedError()

    # FR-IAM-05：禁用用户后其所有会话立即失效
    if user.status != UserStatus.ACTIVE.value:
        await sessions_repo.revoke(session, row, now=now)
        await session.commit()
        raise AccountDisabledError()

    policy = await settings_service.get_security_policy(session)
    access_token, expires_in = create_access_token(
        user_id=user.id,
        username=user.username,
        roles=user.role_codes,
        must_change_password=bool(user.must_change_password),
        expires_delta=timedelta(minutes=policy.access_token_minutes),
    )
    await sessions_repo.touch(session, row, now=now)
    await session.commit()
    return AuthResult(
        access_token=access_token,
        expires_in=expires_in,
        user=user,
        refresh_token=refresh_token,
        refresh_expires_at=expires_at,
    )


async def logout(session: AsyncSession, *, refresh_token: str | None) -> None:
    """登出：吊销该 session 行（FR-AUTH-07）。

    幂等 —— 没有 Cookie 或 Cookie 已失效也返回成功，前端不必区分。
    """
    if not refresh_token:
        return
    row = await sessions_repo.get_by_token(session, refresh_token)
    if row is None:
        return
    if row.revoked_at is None:
        await sessions_repo.revoke(session, row, now=utcnow())
        await session.commit()


async def change_password(
    session: AsyncSession,
    *,
    user: User,
    old_password: str,
    new_password: str,
    current_session_id: int | None = None,
) -> None:
    """修改自己的密码（FR-AUTH-10）。

    - 校验旧密码。**失败返回 400 而不是 401**：401 会触发前端的
      「静默 refresh 后重放」拦截器，导致改密页反复重试，语义也不对
      （用户已认证，只是输入的原密码不对）。
    - 强度校验（长度 ≥ 10、四类字符至少 3 类、不得与用户名相同，FR-AUTH-04）
    - 更新哈希、清 `must_change_password`
    - **吊销该用户全部其他 session**（本机当前会话保留，前端按契约 §3.2 ⑥
      自行清空内存并跳回 /login）
    """
    if not verify_password(old_password, user.password_hash):
        raise ValidationError(
            message="原密码不正确",
            fields=[{"field": "old_password", "message": "原密码不正确"}],
        )

    if old_password == new_password:
        raise ValidationError(
            message="新密码不能与原密码相同",
            fields=[{"field": "new_password", "message": "新密码不能与原密码相同"}],
        )

    validate_password_strength(new_password, user.username)

    now = utcnow()
    user.password_hash = hash_password(new_password)
    user.password_changed_at = now
    user.must_change_password = False
    user.updated_at = now
    await session.flush()

    revoked = await sessions_repo.revoke_all_for_user(
        session, user.id, now=now, except_session_id=current_session_id
    )
    await session.commit()
    logger.info("改密成功 user_id=%s 已吊销其他会话数=%s", user.id, revoked)


async def revoke_all_sessions(session: AsyncSession, user_id: int) -> int:
    """强制下线（FR-AUTH-12，M3 管理台调用；M1 由改密路径间接使用）。"""
    revoked = await sessions_repo.revoke_all_for_user(session, user_id, now=utcnow())
    await session.commit()
    return revoked


def client_ip(request_headers_ip: str | None) -> str | None:
    return request_headers_ip


__all__ = [
    "AuthResult",
    "build_user_me",
    "change_password",
    "client_ip",
    "login",
    "logout",
    "refresh",
    "revoke_all_sessions",
]
