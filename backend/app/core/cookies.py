"""refresh token Cookie 的读写。

契约 §2 的配合方式：dev 走 Vite proxy、prod 由后端托管 SPA，浏览器视角**同源**，
因此 `SameSite=Lax` 即可，**不需要** `SameSite=None`，也**不需要** `Secure`（dev 是 http）。
用 `COOKIE_SECURE` 环境变量控制。
"""

from __future__ import annotations

from fastapi import Response

from app.core.config import settings


def set_refresh_cookie(response: Response, refresh_token: str, max_age_seconds: int) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=refresh_token,
        max_age=max_age_seconds,
        path=settings.refresh_cookie_path,
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def clear_refresh_cookie(response: Response) -> None:
    """清 Cookie。属性必须与下发时一致，否则浏览器不会覆盖。"""
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=settings.refresh_cookie_path,
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
