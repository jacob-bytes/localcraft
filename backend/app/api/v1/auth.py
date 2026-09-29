"""认证路由：`/api/v1/auth/*`（契约 §6 的 6 个接口）。

**本组路由不做强制改密拦截** —— 用户必须能在 `must_change_password = true`
的状态下访问 `/auth/me` 与 `/auth/change-password`，否则永远出不来。
`/auth/*` 是改密拦截的豁免前缀之一。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings as env_settings
from app.core.cookies import clear_refresh_cookie, set_refresh_cookie
from app.core.deps import ClientInfo, get_client_info, get_current_user
from app.core.rate_limit import rate_limit_login
from app.core.timeutil import utcnow
from app.db.session import get_db
from app.models.enums import AuthSource
from app.models.user import User
from app.repositories import auth_sessions as sessions_repo
from app.schemas.auth import (
    AuthProviderResponse,
    ChangePasswordRequest,
    LoginField,
    LoginRequest,
    TokenResponse,
    UserMe,
)
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_response(result: auth_service.AuthResult, response: Response) -> TokenResponse:
    """下发 Cookie + 构造响应。登录与 refresh 共用，保证两者形状一致。"""
    max_age = int((result.refresh_expires_at - utcnow()).total_seconds())
    set_refresh_cookie(response, result.refresh_token, max_age_seconds=max_age)
    return TokenResponse(
        access_token=result.access_token,
        token_type="bearer",
        expires_in=result.expires_in,
        user=auth_service.build_user_me(result.user),
    )


async def _current_session_id(request: Request, session: AsyncSession) -> int | None:
    """从 refresh Cookie 反查当前 session 行 —— 改密时要把「其他」会话吊销掉。"""
    cookie = request.cookies.get(env_settings.refresh_cookie_name)
    if not cookie:
        return None
    row = await sessions_repo.get_by_token(session, cookie)
    return row.id if row is not None else None


@router.get(
    "/provider",
    response_model=AuthProviderResponse,
    summary="当前认证方式与登录表单字段",
)
async def auth_provider() -> AuthProviderResponse:
    """FR-AUTH-09：本地账号体系下返回用户名 + 密码两个字段。

    将来接 OIDC 时只改这里，前端按 `login_fields` 渲染，业务代码不动
    （FR-AUTH-08 的可插拔认证）。
    """
    return AuthProviderResponse(
        provider=AuthSource.LOCAL,
        display_name="本地账号",
        login_fields=[
            LoginField(name="username", label="用户名", type="text"),
            LoginField(name="password", label="密码", type="password"),
        ],
        password_change_supported=True,
        refresh_supported=True,
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="登录",
    # M14（契约 §29.4）：登录档配额（默认每 IP 每分钟 10 次，比全局总配额严得多）。
    # 这是**暴力破解**的第一道应用层闸门；账号锁定（`security.login_max_failures`）
    # 仍然按账号计数，两者互补：限流挡「广撒网」，锁定挡「死磕一个账号」。
    dependencies=[Depends(rate_limit_login)],
)
async def login(
    body: LoginRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
    client: Annotated[ClientInfo, Depends(get_client_info)],
) -> TokenResponse:
    """登录成功 → 返回 access token + `user`，并 `Set-Cookie` 下发 refresh token。

    失败：
      - 用户名或密码错误 → 401 `INVALID_CREDENTIALS`（**同一个码、同一句文案**）
      - 账号禁用 → 403 `ACCOUNT_DISABLED`
      - 账号锁定 → 423 `ACCOUNT_LOCKED`（`details.retry_after_seconds`）
    """
    result = await auth_service.login(
        session,
        username=body.username,
        password=body.password,
        user_agent=client.user_agent,
        ip=client.ip,
    )
    return _token_response(result, response)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="用 refresh cookie 换新 access token",
)
async def refresh(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    client: Annotated[ClientInfo, Depends(get_client_info)],
) -> TokenResponse:
    """契约 §3.3：**必须同时返回 `user` 对象**，前端靠它恢复用户态，
    避免额外再调 `/auth/me`。

    refresh token **不轮换**（契约未要求；轮换会让并发刷新互相踩踏）。
    失败一律 401，前端据此视为未登录。
    """
    cookie = request.cookies.get(env_settings.refresh_cookie_name)
    result = await auth_service.refresh(session, refresh_token=cookie, ip=client.ip)
    return TokenResponse(
        access_token=result.access_token,
        token_type="bearer",
        expires_in=result.expires_in,
        user=auth_service.build_user_me(result.user),
    )


@router.post("/logout", summary="登出并吊销 refresh token")
async def logout(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, str]:
    """吊销该 session 行并清 Cookie（FR-AUTH-07）。

    幂等，且**不要求 Bearer 头** —— access token 过期后用户仍应能可靠登出。
    """
    cookie = request.cookies.get(env_settings.refresh_cookie_name)
    await auth_service.logout(session, refresh_token=cookie)
    clear_refresh_cookie(response)
    return {"status": "ok"}


@router.get("/me", response_model=UserMe, summary="当前用户信息")
async def me(
    user: Annotated[User, Depends(get_current_user)],
) -> UserMe:
    """含 `roles`、`permissions`、`must_change_password`。"""
    return auth_service.build_user_me(user)


@router.post("/change-password", summary="修改自己的密码")
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    response: Response,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, str]:
    """校验旧密码 → 强度校验 → 更新哈希 → 清 `must_change_password`
    → 吊销该用户**全部其他 session**（FR-AUTH-10 / 契约 §3.2 ⑥）。

    同时清掉当前 refresh Cookie：契约 §3.2 ⑥ 要求「改密成功后强制用新密码
    重新登录」，而登录页挂载时会尝试 refresh。若 Cookie 还在，refresh 会成功
    并把用户静默弹回门户，与契约 §8 验收第 11 条冲突。
    """
    current_session_id = await _current_session_id(request, session)
    await auth_service.change_password(
        session,
        user=user,
        old_password=body.old_password,
        new_password=body.new_password,
        current_session_id=current_session_id,
    )
    clear_refresh_cookie(response)
    return {"status": "ok"}


__all__ = ["router"]
