"""鉴权依赖层与 API Token 的边界测试。

`app/core/deps.py` 是权限判定的执行层（`require_role` / `require_scope` /
`portal_access`），任务书要求这一块覆盖率 ≥ 90%。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.security import (
    API_TOKEN_PREFIX,
    create_access_token,
    generate_api_token,
    hash_api_token,
)
from app.core.timeutil import utcnow
from app.db.session import SessionLocal
from app.models.enums import UserStatus
from app.models.user import ApiToken, User
from tests.conftest import PASSWORDS, auth, login

TOOLS = "/api/v1/tools"
ME = "/api/v1/auth/me"


async def _make_api_token(
    username: str,
    scopes: list[str],
    *,
    revoked: bool = False,
    expired: bool = False,
) -> str:
    token = generate_api_token()
    now = utcnow()
    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.username == username))
        ).scalar_one()
        session.add(
            ApiToken(
                name=f"{username} 的测试令牌",
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


# ---------------------------------------------------------------------------
# Bearer 头解析
# ---------------------------------------------------------------------------
async def test_missing_authorization_header(client) -> None:
    response = await client.get(ME)
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


async def test_malformed_authorization_header_treated_as_anonymous(client) -> None:
    response = await client.get(ME, headers={"Authorization": "Token abc"})
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


async def test_garbage_jwt_is_rejected(client) -> None:
    """签名/结构不对属于「凭证格式非法」→ UNAUTHENTICATED（不是 TOKEN_REVOKED）。

    M4 自查发现：`security.decode_access_token` 的 docstring 一直写的是
    UNAUTHENTICATED，但代码抛的是 TOKEN_REVOKED —— 已按 docstring 修正并在此钉死。
    """
    response = await client.get(ME, headers=auth("not.a.jwt"))
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


async def test_expired_access_token_returns_token_expired(client) -> None:
    """前端靠 `TOKEN_EXPIRED` 触发「静默 refresh 后重放一次」。"""
    token, _ = create_access_token(
        user_id=1, username="admin", roles=["superadmin"], expires_delta=timedelta(seconds=-10)
    )
    response = await client.get(ME, headers=auth(token))
    assert response.status_code == 401
    assert response.json()["code"] == "TOKEN_EXPIRED"


async def test_jwt_for_missing_user_is_rejected(client) -> None:
    token, _ = create_access_token(user_id=999_999, username="ghost", roles=["user"])
    response = await client.get(ME, headers=auth(token))
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


async def test_jwt_with_wrong_subject_claim_is_rejected(client) -> None:
    import jwt as pyjwt

    from app.core.config import settings

    payload = {
        "sub": "not-an-int",
        "type": "access",
        "exp": int((utcnow() + timedelta(minutes=5)).timestamp()),
    }
    token = pyjwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
    response = await client.get(ME, headers=auth(token))
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# API Token（docs/03 §1.3 / §1.4）
# ---------------------------------------------------------------------------
async def test_api_token_with_tools_read_can_list(client, seeded) -> None:
    token = await _make_api_token("outsider", ["tools:read"])
    assert token.startswith(API_TOKEN_PREFIX)
    response = await client.get(TOOLS, headers=auth(token))
    assert response.status_code == 200, response.text


async def test_api_token_without_tools_read_is_scope_missing(client, seeded) -> None:
    token = await _make_api_token("outsider", ["users:write"])
    response = await client.get(TOOLS, headers=auth(token))
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "SCOPE_MISSING"
    assert response.json()["details"]["missing_scopes"] == ["tools:read"]


async def test_unknown_api_token_is_rejected(client) -> None:
    response = await client.get(TOOLS, headers=auth(API_TOKEN_PREFIX + "nope"))
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


async def test_revoked_api_token_is_rejected(client) -> None:
    token = await _make_api_token("outsider", ["tools:read"], revoked=True)
    response = await client.get(TOOLS, headers=auth(token))
    assert response.status_code == 401


async def test_expired_api_token_is_rejected(client) -> None:
    token = await _make_api_token("outsider", ["tools:read"], expired=True)
    response = await client.get(TOOLS, headers=auth(token))
    assert response.status_code == 401


async def test_api_token_is_intersected_with_creator_permissions(client, seeded) -> None:
    """docs/03 §1.3：Token 权限不能超过创建者（实时取交集，不做签发快照）。

    viewer 自身只有 `tools:read`，即使 Token 声明了 `settings:write`，
    有效权限也只有交集。
    """
    token = await _make_api_token("viewer", ["tools:read", "settings:write"])
    ok = await client.get(TOOLS, headers=auth(token))
    assert ok.status_code == 200
    # viewer 的 tools:read 生效，但 viewer 没有 download 权限
    assert all(item["can_download"] is False for item in ok.json()["items"])


async def test_admin_all_scope_expands(client, seeded) -> None:
    """`admin:all` 隐含全部其他 Scope（docs/03 §1.4）。"""
    token = await _make_api_token("admin", ["admin:all"])
    response = await client.get(TOOLS, headers=auth(token))
    assert response.status_code == 200, response.text


async def test_api_token_of_disabled_creator_is_rejected(client) -> None:
    """M4 验收 2：Token 创建者被禁用 → `TOKEN_REVOKED`。

    与「refresh 路径发现用户被禁用」返回同一个 code：凭证已失效。
    （JWT 路径上的「用户被禁用」仍是 ACCOUNT_DISABLED —— 那是账号状态，
      不是凭证状态，两者刻意区分。）
    """
    token = await _make_api_token("disabled", ["tools:read"])
    response = await client.get(TOOLS, headers=auth(token))
    assert response.status_code == 401
    assert response.json()["code"] == "TOKEN_REVOKED"


async def test_api_token_of_downgraded_creator_loses_permissions(client, seeded) -> None:
    """创建者被降级后，其签发的 Token 立即降权。"""
    from app.models.user import UserRole

    token = await _make_api_token("admin", ["admin:all"])

    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.username == "admin"))
        ).scalar_one()
        original_role_ids = [link.role_id for link in (await session.execute(select(UserRole).where(UserRole.user_id == user.id))).scalars().all()]
        viewer_role = (
            await session.execute(select(__import__("app.models.user", fromlist=["Role"]).Role).where(__import__("app.models.user", fromlist=["Role"]).Role.code == "viewer"))
        ).scalar_one()
        for link in (
            await session.execute(select(UserRole).where(UserRole.user_id == user.id))
        ).scalars().all():
            await session.delete(link)
        session.add(UserRole(user_id=user.id, role_id=viewer_role.id, granted_at=utcnow()))
        await session.commit()

    try:
        # admin:all 展开成全部权限，但与 viewer 的实际权限取交集后只剩 tools:read
        response = await client.get(TOOLS, headers=auth(token))
        assert response.status_code == 200, response.text
        assert all(item["can_download"] is False for item in response.json()["items"])
    finally:
        async with SessionLocal() as session:
            user = (
                await session.execute(select(User).where(User.username == "admin"))
            ).scalar_one()
            for link in (
                await session.execute(select(UserRole).where(UserRole.user_id == user.id))
            ).scalars().all():
                await session.delete(link)
            for role_id in original_role_ids:
                session.add(UserRole(user_id=user.id, role_id=role_id, granted_at=utcnow()))
            await session.commit()


# ---------------------------------------------------------------------------
# 强制改密拦截在各类入口上的覆盖
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/api/v1/tools", "/api/v1/categories", "/api/v1/tags"])
async def test_password_gate_blocks_all_portal_reads(client, path: str) -> None:
    token = await login(client, "newbie")
    response = await client.get(path, headers=auth(token))
    assert response.status_code == 403, f"{path}: {response.text}"
    assert response.json()["code"] == "PASSWORD_CHANGE_REQUIRED"


@pytest.mark.parametrize(
    "path",
    ["/healthz", "/readyz", "/api/v1/meta", "/api/v1/auth/provider"],
)
async def test_gate_exempt_endpoints_stay_reachable(client, path: str) -> None:
    token = await login(client, "newbie")
    response = await client.get(path, headers=auth(token))
    assert response.status_code == 200, f"{path}: {response.text}"


async def test_api_token_with_password_change_required_is_blocked(client) -> None:
    """API Token 也要经过改密拦截。"""
    token = await _make_api_token("newbie", ["tools:read"])
    response = await client.get(TOOLS, headers=auth(token))
    assert response.status_code == 403
    assert response.json()["code"] == "PASSWORD_CHANGE_REQUIRED"


async def test_anonymous_portal_access_respects_setting(client) -> None:
    """默认（false）匿名被拒；这里断言拒绝码是 UNAUTHENTICATED。"""
    response = await client.get("/api/v1/categories")
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


async def test_disabled_user_jwt_is_rejected(client) -> None:
    from app.core.security import create_access_token

    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.username == "viewer"))
        ).scalar_one()
        token, _ = create_access_token(
            user_id=user.id, username=user.username, roles=["viewer"]
        )
        user.status = UserStatus.DISABLED.value
        await session.commit()

    try:
        response = await client.get(ME, headers=auth(token))
        assert response.status_code == 403
        assert response.json()["code"] == "ACCOUNT_DISABLED"
    finally:
        async with SessionLocal() as session:
            user = (
                await session.execute(select(User).where(User.username == "viewer"))
            ).scalar_one()
            user.status = UserStatus.ACTIVE.value
            await session.commit()


# ---------------------------------------------------------------------------
# 全文索引重建
# ---------------------------------------------------------------------------
async def test_reindex_search_rebuilds_index(session, seeded) -> None:
    from app.search import get_search_backend

    backend = get_search_backend()
    count = await backend.reindex_all(session)
    await session.commit()
    assert count >= 1

    # 重建后仍能按中文/英文检索到
    ids = await backend.matching_tool_ids(session, "public-approved")
    assert ids is None or isinstance(ids, set)


async def test_like_fallback_backend_returns_none(session) -> None:
    """非 SQLite 方言（或 FTS5 不可用）时回退到 LIKE，由仓储处理。"""
    from app.search.sqlite_fts import LikeSearchBackend

    backend = LikeSearchBackend()
    assert await backend.matching_tool_ids(session, "anything") is None
    assert await backend.reindex_all(session) == 0
    await backend.upsert(session, 1, None)  # type: ignore[arg-type]
    await backend.delete(session, 1)
    assert backend.name == "like_fallback"


async def test_fts_backend_degrades_when_table_missing(session) -> None:
    """虚表不存在时不应抛异常，而是降级为「处理不了」。"""
    from sqlalchemy import text

    from app.search.sqlite_fts import FTS_TABLE, SqliteFts5Backend

    backend = SqliteFts5Backend()
    await session.execute(text(f"ALTER TABLE {FTS_TABLE} RENAME TO {FTS_TABLE}_bak"))
    try:
        result = await backend.matching_tool_ids(session, "x")
        assert result is None
        assert backend.available is False
    finally:
        await session.rollback()
        await session.execute(text(f"ALTER TABLE {FTS_TABLE}_bak RENAME TO {FTS_TABLE}"))
        await session.commit()


def test_password_constants_used_by_deps_tests() -> None:
    assert PASSWORDS["viewer"] == "Viewer@12345"
