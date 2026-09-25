"""M4 打磨项：凭证失效语义统一 + 冻结接口面。

对应 `contracts/CONTRACT.md` §16.4 / §16.5 的裁定：

  - §16.4：`TOKEN_REVOKED` 必须修正 —— API Token 的「已被吊销」与
    「创建者被禁用」两条分支原先返回 `UNAUTHENTICATED` / `ACCOUNT_DISABLED`，
    与 refresh 路径的 `TOKEN_REVOKED` 不一致。M4 起统一。
  - §16.5：接口面永久冻结于 92，守卫测试改为「总数恰为 92 + 与 docs/03 §2.5 逐条一致」。

错误码的划分规则（本文件即规则的可执行说明）：

  | code | 含义 |
  | --- | --- |
  | `UNAUTHENTICATED` | 没带凭证 / 凭证格式非法 / 查无此凭证 |
  | `TOKEN_REVOKED`   | 凭证曾经有效，但已失效（被吊销、创建者被禁用/消失） |
  | `TOKEN_EXPIRED`   | 凭证过期 |
  | `ACCOUNT_DISABLED`| **账号**状态（JWT 路径），不是凭证状态 |
"""

from __future__ import annotations

from datetime import timedelta

from app.core.security import create_access_token, generate_api_token, hash_api_token
from app.core.timeutil import utcnow
from app.db.session import SessionLocal
from app.models.user import ApiToken, User
from tests.conftest import auth, login
from tests.m4_helpers import (
    documented_admin_endpoints,
    parse_docs_03_section_2_5,
)

TOOLS = "/api/v1/tools"
ME = "/api/v1/auth/me"


async def _issue_token(
    username: str,
    scopes: list[str],
    *,
    revoked: bool = False,
    expired: bool = False,
) -> str:
    """直接写库造一个 API Token，便于精确构造「已吊销 / 已过期」。"""
    from sqlalchemy import select

    token = generate_api_token()
    now = utcnow()
    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.username == username))
        ).scalar_one()
        session.add(
            ApiToken(
                name=f"{username} 的 M4 令牌",
                token_prefix=token[:8],
                token_hash=hash_api_token(token),
                scopes=scopes,
                created_by_id=user.id,
                expires_at=(now - timedelta(hours=1)) if expired else None,
                revoked_at=now if revoked else None,
                created_at=now,
            )
        )
        await session.commit()
    return token


# ===========================================================================
# M4 验收 1/2/3：三条凭证失效语义
# ===========================================================================
async def test_m4_acceptance_1_revoked_api_token_returns_token_revoked(client, seeded) -> None:
    """验收 1：吊销 API Token 后调用 → 401 `TOKEN_REVOKED`（不再是 UNAUTHENTICATED）。"""
    token = await _issue_token("admin", ["tools:read"], revoked=True)
    response = await client.get(TOOLS, headers=auth(token))
    assert response.status_code == 401, response.text
    assert response.json()["code"] == "TOKEN_REVOKED", response.text


async def test_m4_acceptance_2_disabled_creator_returns_token_revoked(client, seeded) -> None:
    """验收 2：Token 创建者被禁用后调用 → 401 `TOKEN_REVOKED`。"""
    from sqlalchemy import select

    created = await client.post(
        "/api/v1/admin/users",
        json={
            "username": "m4disabled",
            "display_name": "M4 被禁用者",
            "password": "M4Disabled@123",
            "roles": ["user"],
            "must_change_password": False,
        },
        headers=auth(await login(client, "admin")),
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["user"]["id"]

    token = await _issue_token("m4disabled", ["tools:read"])
    assert (await client.get(TOOLS, headers=auth(token))).status_code == 200

    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one()
        user.status = "disabled"
        await session.commit()

    response = await client.get(TOOLS, headers=auth(token))
    assert response.status_code == 401, response.text
    assert response.json()["code"] == "TOKEN_REVOKED", response.text


async def test_m4_acceptance_3_missing_credential_stays_unauthenticated(client) -> None:
    """验收 3：不带凭证仍然是 `UNAUTHENTICATED`（不要误改）。"""
    response = await client.get(ME)
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"

    # 空 Bearer 与「只有前缀没内容」也归到「没带凭证」
    for header in ("Bearer ", "Bearer    ", ""):
        headers = {"Authorization": header} if header else {}
        got = await client.get(ME, headers=headers)
        assert got.status_code == 401, (header, got.text)
        assert got.json()["code"] == "UNAUTHENTICATED", (header, got.text)


# ===========================================================================
# 自查：凭证失效的每一个分支都要落在正确的 code 上
# ===========================================================================
async def test_expired_api_token_returns_token_expired(client, seeded) -> None:
    """过期 ≠ 未认证 ≠ 被吊销。前端要能区分「静默刷新」与「跳登录」。"""
    token = await _issue_token("admin", ["tools:read"], expired=True)
    response = await client.get(TOOLS, headers=auth(token))
    assert response.status_code == 401, response.text
    assert response.json()["code"] == "TOKEN_EXPIRED", response.text


async def test_unknown_api_token_returns_unauthenticated(client, seeded) -> None:
    """查无此凭证 → UNAUTHENTICATED（注意：不是 TOKEN_REVOKED）。"""
    response = await client.get(TOOLS, headers=auth(f"st_{'A' * 43}"))
    assert response.status_code == 401, response.text
    assert response.json()["code"] == "UNAUTHENTICATED", response.text


async def test_garbage_jwt_is_unauthenticated_not_revoked(client) -> None:
    """签名/结构非法 → UNAUTHENTICATED。

    M4 自查发现 `decode_access_token` 的 docstring 写的是 UNAUTHENTICATED，
    代码却抛 TOKEN_REVOKED —— 已修正，这里钉死。
    """
    for garbage in ("not.a.jwt", "aaa.bbb.ccc", "x"):
        response = await client.get(ME, headers=auth(garbage))
        assert response.status_code == 401, (garbage, response.text)
        assert response.json()["code"] == "UNAUTHENTICATED", (garbage, response.text)


async def test_refresh_token_used_as_access_token_is_unauthenticated(client, seeded) -> None:
    """类型不对的 JWT 属于「凭证格式非法」，不是「已吊销」。"""
    token, _ = create_access_token(user_id=1, username="admin", roles=["superadmin"])
    # 手工把 type 改成 refresh，模拟「拿 refresh token 当 access token 用」
    import jwt as pyjwt

    from app.core.config import settings

    payload = pyjwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    payload["type"] = "refresh"
    forged = pyjwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)

    response = await client.get(ME, headers=auth(forged))
    assert response.status_code == 401, response.text
    assert response.json()["code"] == "UNAUTHENTICATED", response.text


async def test_disabled_user_jwt_still_reports_account_disabled(client, seeded) -> None:
    """**刻意保留**：JWT 路径上的「账号被禁用」是账号状态，不是凭证状态。

    与 API Token 路径的 TOKEN_REVOKED 分开是设计选择：
    前端对前者展示「账号已禁用，请联系管理员」，对后者提示「登录已失效」。
    """
    from sqlalchemy import select

    from app.models.enums import UserStatus

    token, _ = create_access_token(user_id=1, username="admin", roles=["superadmin"])
    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.username == "admin"))
        ).scalar_one()
        original = user.status
        user.status = UserStatus.DISABLED.value
        await session.commit()
    try:
        response = await client.get(ME, headers=auth(token))
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "ACCOUNT_DISABLED", response.text
    finally:
        async with SessionLocal() as session:
            user = (
                await session.execute(select(User).where(User.username == "admin"))
            ).scalar_one()
            user.status = original
            await session.commit()


async def test_credential_error_code_matrix_is_exhaustive(seeded) -> None:
    """把「哪些 code 属于凭证失效」写死，防止以后又冒出第三个说法。"""
    from app.core.errors import (
        AccountDisabledError,
        TokenExpiredError,
        TokenRevokedError,
        UnauthenticatedError,
    )

    assert UnauthenticatedError().code == "UNAUTHENTICATED"
    assert TokenRevokedError().code == "TOKEN_REVOKED"
    assert TokenExpiredError().code == "TOKEN_EXPIRED"
    assert AccountDisabledError().code == "ACCOUNT_DISABLED"
    # 三个 401 语义必须各占一个 code，不能合并
    assert len({UnauthenticatedError().code, TokenRevokedError().code, TokenExpiredError().code}) == 3
    for exc in (UnauthenticatedError, TokenRevokedError, TokenExpiredError):
        assert exc().http_status == 401


# ===========================================================================
# 冻结接口面：与 docs/03 §2.5 逐条一致
# ===========================================================================
def test_docs_03_section_2_5_is_parseable() -> None:
    parsed = parse_docs_03_section_2_5()
    assert len(parsed) >= 30, f"§2.5 只解析出 {len(parsed)} 条，解析逻辑可能失效：{parsed[:5]}"
    assert all(p.startswith("/api/v1/admin") for _, p in parsed), (
        "§2.5 是管理后台总表，路径都应以 /api/v1/admin 开头"
    )
    assert len(documented_admin_endpoints()) == len(parsed)
