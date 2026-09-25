"""认证链路测试：登录 / 锁定 / 禁用 / 刷新 / 登出 / 改密。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.timeutil import utcnow
from app.db.session import SessionLocal
from app.models.enums import UserStatus
from app.models.user import User
from tests.conftest import PASSWORDS, auth, login

LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/auth/me"
CHANGE = "/api/v1/auth/change-password"


async def _reset_login_state(username: str) -> None:
    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.username == username))
        ).scalar_one()
        user.failed_login_count = 0
        user.locked_until = None
        await session.commit()


async def _force_expire_lock(username: str) -> None:
    """把锁定时间挪到过去，用于验证「锁定过期后计数清零」。"""
    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.username == username))
        ).scalar_one()
        user.locked_until = utcnow() - timedelta(seconds=1)
        await session.commit()


async def _revoke_all(username: str) -> None:
    from app.repositories import auth_sessions as sessions_repo

    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.username == username))
        ).scalar_one()
        await sessions_repo.revoke_all_for_user(session, user.id, now=utcnow())
        await session.commit()


# ---------------------------------------------------------------------------
# 登录成功 + Cookie 下发
# ---------------------------------------------------------------------------
async def test_login_success_sets_refresh_cookie(client) -> None:
    response = await client.post(
        LOGIN, json={"username": "admin", "password": PASSWORDS["admin"]}
    )
    assert response.status_code == 200, response.text
    body = response.json()

    # 契约 §3.2 ① 的响应形状
    assert set(body) == {"access_token", "token_type", "expires_in", "user"}
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 30 * 60
    assert body["user"]["username"] == "admin"
    assert body["user"]["roles"] == ["superadmin"]
    assert body["user"]["must_change_password"] is False

    # Cookie：HttpOnly + SameSite=Lax + Path=/api/v1/auth + Max-Age≈7 天
    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith("refresh_token=")
    assert "HttpOnly" in set_cookie
    assert "Path=/api/v1/auth" in set_cookie
    assert "SameSite=lax" in set_cookie.lower().replace("samesite=lax", "SameSite=lax")
    assert "Max-Age=604800" in set_cookie or "Max-Age=604799" in set_cookie
    # dev 环境（COOKIE_SECURE=false）**不应**带 Secure
    assert "Secure" not in set_cookie

    # 客户端确实把 Cookie 存下来了
    assert client.cookies.get("refresh_token")


async def test_login_username_is_case_insensitive(client) -> None:
    response = await client.post(
        LOGIN, json={"username": "  ADMIN  ", "password": PASSWORDS["admin"]}
    )
    assert response.status_code == 200, response.text
    assert response.json()["user"]["username"] == "admin"


# ---------------------------------------------------------------------------
# 用户名不存在 / 密码错误 → 完全相同的错误码与文案
# ---------------------------------------------------------------------------
async def test_unknown_username_and_wrong_password_are_indistinguishable(client) -> None:
    unknown = await client.post(
        LOGIN, json={"username": "no-such-user", "password": "Whatever@12345"}
    )
    wrong = await client.post(
        LOGIN, json={"username": "admin", "password": "WrongPass@12345"}
    )

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["code"] == wrong.json()["code"] == "INVALID_CREDENTIALS"
    assert unknown.json()["message"] == wrong.json()["message"] == "用户名或密码错误"


# ---------------------------------------------------------------------------
# 锁定
# ---------------------------------------------------------------------------
async def test_lockout_after_threshold_blocks_correct_password(client) -> None:
    await _reset_login_state("lockme")

    for attempt in range(1, 6):
        response = await client.post(
            LOGIN, json={"username": "lockme", "password": "WrongPass@12345"}
        )
        # 契约 §8 第 12 条：前 5 次仍是凭据错误
        assert response.status_code == 401, f"第 {attempt} 次: {response.text}"
        assert response.json()["code"] == "INVALID_CREDENTIALS"

    # 第 6 次即使密码正确也拒绝
    response = await client.post(
        LOGIN, json={"username": "lockme", "password": PASSWORDS["lockme"]}
    )
    assert response.status_code == 423, response.text
    body = response.json()
    assert body["code"] == "ACCOUNT_LOCKED"
    assert isinstance(body["details"]["retry_after_seconds"], int)
    assert 0 < body["details"]["retry_after_seconds"] <= 15 * 60

    await _reset_login_state("lockme")


async def test_lock_expiry_clears_failure_counter(client) -> None:
    await _reset_login_state("lockme")
    for _ in range(5):
        await client.post(LOGIN, json={"username": "lockme", "password": "WrongPass@12345"})

    await _force_expire_lock("lockme")

    response = await client.post(
        LOGIN, json={"username": "lockme", "password": PASSWORDS["lockme"]}
    )
    assert response.status_code == 200, response.text

    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.username == "lockme"))
        ).scalar_one()
        assert user.failed_login_count == 0
        assert user.locked_until is None


async def test_successful_login_resets_failure_counter(client) -> None:
    await _reset_login_state("lockme")
    for _ in range(3):
        await client.post(LOGIN, json={"username": "lockme", "password": "WrongPass@12345"})

    response = await client.post(
        LOGIN, json={"username": "lockme", "password": PASSWORDS["lockme"]}
    )
    assert response.status_code == 200, response.text

    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.username == "lockme"))
        ).scalar_one()
        assert user.failed_login_count == 0


# ---------------------------------------------------------------------------
# 禁用账号
# ---------------------------------------------------------------------------
async def test_disabled_account_rejected(client) -> None:
    response = await client.post(
        LOGIN, json={"username": "disabled", "password": PASSWORDS["disabled"]}
    )
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "ACCOUNT_DISABLED"


async def test_disabled_account_refresh_is_rejected(client) -> None:
    """FR-IAM-05：禁用后其所有会话立即失效。"""
    await _revoke_all("disabled")
    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.username == "disabled"))
        ).scalar_one()
        user.status = UserStatus.ACTIVE.value
        await session.commit()

    token = await login(client, "disabled")
    assert client.cookies.get("refresh_token")

    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.username == "disabled"))
        ).scalar_one()
        user.status = UserStatus.DISABLED.value
        await session.commit()

    # 已签发的 access token 立即失效
    response = await client.get(ME, headers=auth(token))
    assert response.status_code == 403
    assert response.json()["code"] == "ACCOUNT_DISABLED"

    # refresh 也失效
    response = await client.post(REFRESH)
    assert response.status_code == 403

    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.username == "disabled"))
        ).scalar_one()
        user.status = UserStatus.DISABLED.value
        await session.commit()


# ---------------------------------------------------------------------------
# refresh
# ---------------------------------------------------------------------------
async def test_refresh_returns_user_object(client) -> None:
    """契约 §3.3：refresh 必须返回 user，前端靠它恢复用户态。"""
    await login(client, "admin")

    response = await client.post(REFRESH)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"access_token", "token_type", "expires_in", "user"}
    assert body["user"]["username"] == "admin"
    assert body["user"]["roles"] == ["superadmin"]
    assert body["user"]["must_change_password"] is False


async def test_refresh_without_cookie_is_401(client) -> None:
    response = await client.post(REFRESH)
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


async def test_refresh_with_garbage_cookie_is_401(client) -> None:
    client.cookies.set("refresh_token", "rt_not-a-real-token")
    response = await client.post(REFRESH)
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# 登出
# ---------------------------------------------------------------------------
async def test_logout_revokes_refresh_token(client) -> None:
    await login(client, "admin")
    assert (await client.post(REFRESH)).status_code == 200

    response = await client.post(LOGOUT)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    # 清 Cookie
    assert "Max-Age=0" in response.headers.get("set-cookie", "")

    # 即使有人留着旧 token 也不能再刷新
    client.cookies.set("refresh_token", "rt_anything")
    assert (await client.post(REFRESH)).status_code == 401


async def test_logout_is_idempotent_without_cookie(client) -> None:
    response = await client.post(LOGOUT)
    assert response.status_code == 200


async def test_logout_revokes_the_exact_session(client) -> None:
    await login(client, "admin")
    cookie = client.cookies.get("refresh_token")
    assert cookie

    await client.post(LOGOUT)

    # 把旧 cookie 手工塞回去，服务端应已吊销该行
    client.cookies.set("refresh_token", cookie)
    response = await client.post(REFRESH)
    assert response.status_code == 401
    assert response.json()["code"] == "TOKEN_REVOKED"


# ---------------------------------------------------------------------------
# /auth/me
# ---------------------------------------------------------------------------
async def test_me_requires_authentication(client) -> None:
    response = await client.get(ME)
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


async def test_me_returns_roles_and_permissions(client) -> None:
    token = await login(client, "viewer")
    response = await client.get(ME, headers=auth(token))
    assert response.status_code == 200
    body = response.json()
    assert body["roles"] == ["viewer"]
    # viewer 不能下载（docs/01 §3.2）
    assert "download" not in body["permissions"]
    assert "tools:read" in body["permissions"]


# ---------------------------------------------------------------------------
# 改密
# ---------------------------------------------------------------------------
async def test_change_password_revokes_other_sessions(client) -> None:
    await _revoke_all("lockme")

    # 会话 A（设备 1）
    other = type(client)(
        transport=client._transport, base_url="http://testserver"
    )
    async with other:
        await login(other, "lockme")
        # 会话 B（设备 2，即当前 client）
        token_b = await login(client, "lockme")

        assert (await other.post(REFRESH)).status_code == 200

        response = await client.post(
            CHANGE,
            headers=auth(token_b),
            json={"old_password": PASSWORDS["lockme"], "new_password": "Rotated@54321"},
        )
        assert response.status_code == 200, response.text

        # 其他会话被吊销
        assert (await other.post(REFRESH)).status_code == 401

    # 新密码可登录，旧密码不行
    assert (
        await client.post(
            LOGIN, json={"username": "lockme", "password": "Rotated@54321"}
        )
    ).status_code == 200
    assert (
        await client.post(
            LOGIN, json={"username": "lockme", "password": PASSWORDS["lockme"]}
        )
    ).status_code == 401

    # 还原，保证测试可重复执行
    async with SessionLocal() as session:
        from app.core.security import hash_password

        user = (
            await session.execute(select(User).where(User.username == "lockme"))
        ).scalar_one()
        user.password_hash = hash_password(PASSWORDS["lockme"])
        user.must_change_password = False
        await session.commit()
    await _revoke_all("lockme")


async def test_change_password_rejects_wrong_old_password(client) -> None:
    token = await login(client, "admin")
    response = await client.post(
        CHANGE,
        headers=auth(token),
        json={"old_password": "NotMyPassword@1", "new_password": "Brand@New12345"},
    )
    # 400 而不是 401：401 会触发前端「静默 refresh 并重放」的拦截器
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"]["fields"][0]["field"] == "old_password"


@pytest.mark.parametrize(
    "weak",
    ["short1A!", "alllowercase", "1234567890", "admin"],
)
async def test_change_password_enforces_strength(client, weak: str) -> None:
    token = await login(client, "admin")
    response = await client.post(
        CHANGE,
        headers=auth(token),
        json={"old_password": PASSWORDS["admin"], "new_password": weak},
    )
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["details"]["fields"]


async def test_change_password_then_gate_lifts(client) -> None:
    """newbie 改密成功后必须能访问业务接口（契约 §8 第 11 条）。"""
    await _revoke_all("newbie")
    token = await login(client, "newbie")

    # 改密前被拦截
    blocked = await client.get("/api/v1/tools", headers=auth(token))
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "PASSWORD_CHANGE_REQUIRED"

    response = await client.post(
        CHANGE,
        headers=auth(token),
        json={"old_password": PASSWORDS["newbie"], "new_password": "Fresh@54321"},
    )
    assert response.status_code == 200, response.text

    # 同一个 access token 立刻可用 —— 拦截读的是数据库而不是 JWT 副本
    allowed = await client.get("/api/v1/tools", headers=auth(token))
    assert allowed.status_code == 200, allowed.text

    # 还原
    async with SessionLocal() as session:
        from app.core.security import hash_password

        user = (
            await session.execute(select(User).where(User.username == "newbie"))
        ).scalar_one()
        user.password_hash = hash_password(PASSWORDS["newbie"])
        user.must_change_password = True
        await session.commit()
    await _revoke_all("newbie")
