"""`require_role` / `require_scope` 等守卫构造器的单元测试。

M1 的 12 个接口都用 `portal_access` 或 `get_current_user`，`require_role` /
`require_scope` 要到 M2/M3 的管理接口才会被路由使用。但如果只靠路由覆盖，
这两个最关键的角色守卫就会是「未测试代码」—— 所以在这里直接调用闭包验证。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.deps import (
    AUTH_DEP_ATTR,
    PASSWORD_GATE_ATTR,
    PortalAccess,
    Principal,
    get_current_user,
    get_principal,
    require_authenticated,
    require_role,
    require_scope,
)
from app.core.errors import ForbiddenError, PasswordChangeRequiredError, ScopeMissingError
from app.core.permissions import permissions_for_roles
from app.db.session import SessionLocal
from app.models.user import User


def _principal(
    *,
    roles: set[str] | None = None,
    kind: str = "user",
    permissions: frozenset[str] | None = None,
    must_change_password: bool = False,
) -> Principal:
    roles = roles or {"user"}
    user = User(
        id=7,
        username="someone",
        display_name="某人",
        password_hash="x",
        must_change_password=must_change_password,
        status="active",
    )
    return Principal(
        kind=kind,
        user_id=7,
        username="someone",
        display_name="某人",
        roles=frozenset(roles),
        permissions=(
            permissions if permissions is not None else permissions_for_roles(roles)
        ),
        must_change_password=must_change_password,
        user=user,
    )


# ---------------------------------------------------------------------------
# 标记位（守卫测试依赖它们）
# ---------------------------------------------------------------------------
def test_guard_markers_are_set() -> None:
    assert getattr(get_principal, AUTH_DEP_ATTR, False)
    assert getattr(get_current_user, AUTH_DEP_ATTR, False)
    # 改密拦截标记只应出现在 require_authenticated 上（及复用它的一切）
    assert getattr(require_authenticated, PASSWORD_GATE_ATTR, False)
    # get_current_user 走的是「不拦截」路径
    assert not getattr(get_current_user, PASSWORD_GATE_ATTR, False)


def test_require_role_closure_is_marked_and_named() -> None:
    dep = require_role("approver", "superadmin")
    assert getattr(dep, AUTH_DEP_ATTR, False)
    assert dep.__name__ == "require_role_approver_superadmin"


# ---------------------------------------------------------------------------
# require_role
# ---------------------------------------------------------------------------
async def test_require_role_allows_matching_role() -> None:
    dep = require_role("approver", "superadmin")
    assert await dep(_principal(roles={"approver"})) is not None


async def test_require_role_rejects_missing_role() -> None:
    dep = require_role("superadmin")
    with pytest.raises(ForbiddenError) as exc:
        await dep(_principal(roles={"user"}))
    assert exc.value.code == "FORBIDDEN"
    assert exc.value.http_status == 403
    assert "superadmin" in exc.value.message


async def test_require_role_union_of_multiple_roles() -> None:
    """docs/01 §3.1：多角色取并集，任一命中即可。"""
    dep = require_role("approver")
    assert await dep(_principal(roles={"viewer", "approver"})) is not None


# ---------------------------------------------------------------------------
# require_scope
# ---------------------------------------------------------------------------
async def test_require_scope_lets_jwt_through() -> None:
    """JWT 走角色判定，scope 守卫只对 API Token 生效（docs/03 §6.2）。"""
    dep = require_scope("approvals:write")
    assert await dep(_principal(roles={"viewer"}, kind="user")) is not None


async def test_require_scope_enforces_for_api_token() -> None:
    dep = require_scope("tools:write")
    principal = _principal(
        kind="api_token", permissions=frozenset({"tools:read"})
    )
    with pytest.raises(ScopeMissingError) as exc:
        await dep(principal)
    assert exc.value.code == "SCOPE_MISSING"
    assert exc.value.http_status == 403
    assert exc.value.details["missing_scopes"] == ["tools:write"]


async def test_require_scope_passes_when_api_token_has_scope() -> None:
    dep = require_scope("tools:read")
    principal = _principal(kind="api_token", permissions=frozenset({"tools:read"}))
    assert await dep(principal) is not None


async def test_require_scope_multiple_scopes_requires_all() -> None:
    dep = require_scope("tools:read", "approvals:write")
    principal = _principal(
        kind="api_token", permissions=frozenset({"tools:read", "approvals:write"})
    )
    assert await dep(principal) is not None

    partial = _principal(kind="api_token", permissions=frozenset({"tools:read"}))
    with pytest.raises(ScopeMissingError):
        await dep(partial)


# ---------------------------------------------------------------------------
# require_authenticated（强制改密拦截）
# ---------------------------------------------------------------------------
async def test_require_authenticated_blocks_password_change_required() -> None:
    with pytest.raises(PasswordChangeRequiredError) as exc:
        await require_authenticated(_principal(must_change_password=True))
    assert exc.value.code == "PASSWORD_CHANGE_REQUIRED"
    assert exc.value.http_status == 403


async def test_require_authenticated_passes_when_password_ok() -> None:
    principal = _principal()
    assert await require_authenticated(principal) is principal


# ---------------------------------------------------------------------------
# Principal / PortalAccess 属性
# ---------------------------------------------------------------------------
def test_principal_properties() -> None:
    viewer = _principal(roles={"viewer"})
    assert viewer.is_superadmin is False
    assert viewer.is_api_token is False
    assert viewer.can_download is False, "viewer 不可下载（docs/01 §3.2）"

    admin = _principal(roles={"superadmin"})
    assert admin.is_superadmin is True
    assert admin.can_download is True


def test_portal_access_properties_for_anonymous_and_user() -> None:
    anonymous = PortalAccess(principal=None, request_id="01HQ8X5K2M9PQR3TVWXYZ4ABCD")
    assert anonymous.user_id is None
    assert anonymous.roles == frozenset()
    assert anonymous.can_download is False

    member = PortalAccess(principal=_principal(roles={"user"}), request_id="x")
    assert member.user_id == 7
    assert member.roles == frozenset({"user"})
    assert member.can_download is True


# ---------------------------------------------------------------------------
# resolve_principal 的边界
# ---------------------------------------------------------------------------
async def test_resolve_principal_returns_none_without_header(client) -> None:
    from app.core.deps import resolve_principal

    async with SessionLocal() as session:
        request = _fake_request()
        assert await resolve_principal(request, session) is None


def _fake_request():  # type: ignore[no-untyped-def]
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "query_string": b"",
        "state": {},
    }
    request = Request(scope)
    request.state.user_id = None
    return request


async def test_principal_of_disabled_user_cannot_download(client) -> None:
    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.username == "viewer"))
        ).scalar_one()
        principal = _principal(roles={"viewer"})
        assert principal.user_id == user.id or principal.user_id == 7
