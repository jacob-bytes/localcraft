"""依赖注入：`get_principal` / `require_role` / `require_scope` / `get_current_user`。

设计要点（docs/03 §6.2 + 契约 §11 硬约束第 6 条）：

1. 每个业务路由**必须显式声明鉴权依赖**，否则视为漏加权限，守卫测试会失败。
2. `require_role` / `require_scope` 内部都依赖 `require_authenticated`，
   而 `require_authenticated` 负责**强制改密拦截** —— 这样「用了角色/Scope
   守卫」的路由天然被拦截，不存在「忘了加改密判断」的可能。
3. 只有 `/auth/*`、`/meta`、`/healthz`、`/readyz` 走不设防的入口，
   它们用 `get_current_user`（不触发改密拦截）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    AccountDisabledError,
    ForbiddenError,
    PasswordChangeRequiredError,
    ScopeMissingError,
    UnauthenticatedError,
)
from app.core.permissions import ALL_PERMISSIONS, PERM_DOWNLOAD, permissions_for_roles
from app.core.security import API_TOKEN_PREFIX, decode_access_token, hash_api_token
from app.core.timeutil import ensure_utc, utcnow
from app.db.session import get_db
from app.models.enums import ApiScope, UserStatus
from app.models.user import ApiToken, User
from app.services import settings_service

#: 标记：本依赖链包含鉴权。守卫测试遍历 `app.routes` 时用它判断
#: 「这个路由到底有没有挂权限」。挂在函数对象上，闭包也能被检查到。
AUTH_DEP_ATTR = "__selftool_requires_auth__"

#: 标记：本依赖链包含**强制改密拦截**（FR-AUTH-03）。
#: 守卫测试用它断言「除豁免前缀外，每个路由都被改密拦截覆盖」。
#: 该标记只应出现在 `require_authenticated`（及复用它的一切）与
#: `portal_access`（它在依赖内部自行做了同一判断）上。
PASSWORD_GATE_ATTR = "__selftool_password_gate__"

BEARER_PREFIX = "bearer "


@dataclass(frozen=True)
class Principal:
    """统一身份。JWT 与 API Token 都归一成这个形状（docs/03 §6.2）。"""

    kind: str  # "user" | "api_token"
    user_id: int
    username: str
    display_name: str
    roles: frozenset[str]
    permissions: frozenset[str]
    must_change_password: bool
    user: User = field(repr=False)
    api_token_id: int | None = None

    @property
    def is_api_token(self) -> bool:
        return self.kind == "api_token"

    @property
    def can_download(self) -> bool:
        """docs/01 §3.2：viewer 只能浏览 public 工具，**不能下载**。"""
        return PERM_DOWNLOAD in self.permissions

    @property
    def is_superadmin(self) -> bool:
        return "superadmin" in self.roles


def _mark_auth(fn: Any) -> Any:
    setattr(fn, AUTH_DEP_ATTR, True)
    return fn


def _mark_password_gate(fn: Any) -> Any:
    """标记「本依赖会执行强制改密拦截」。"""
    setattr(fn, AUTH_DEP_ATTR, True)
    setattr(fn, PASSWORD_GATE_ATTR, True)
    return fn


def bearer_token(request: Request) -> str | None:
    """从 `Authorization: Bearer <token>` 取出凭证。"""
    header = request.headers.get("Authorization")
    if not header:
        return None
    value = header.strip()
    if value.lower().startswith(BEARER_PREFIX):
        token = value[len(BEARER_PREFIX) :].strip()
        return token or None
    # 格式不合法（缺少 Bearer 前缀）视同未携带
    return None


async def _principal_from_api_token(session: AsyncSession, token: str) -> Principal:
    """API Token 鉴权（docs/02 §3.15 / docs/03 §1.3）。

    - 先按 `token_hash` 点查（唯一索引命中）
    - 再校验未吊销、未过期
    - **实时**读取创建者当前角色并取交集 —— 创建者被禁用或降级后，
      其签发的 Token 立即失效或降权，不做签发时快照。
    """
    result = await session.execute(
        select(ApiToken).where(ApiToken.token_hash == hash_api_token(token))
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise UnauthenticatedError("API Token 无效")
    if row.revoked_at is not None:
        raise UnauthenticatedError("API Token 已被吊销")
    expires_at = ensure_utc(row.expires_at)
    if expires_at is not None and expires_at <= utcnow():
        raise UnauthenticatedError("API Token 已过期")

    creator: User | None = row.creator
    if creator is None:  # pragma: no cover - 外键保证不会发生
        raise UnauthenticatedError("API Token 的创建者不存在")
    if creator.status != UserStatus.ACTIVE.value:
        raise AccountDisabledError()

    creator_perms = permissions_for_roles(creator.role_codes)
    token_scopes = set(row.scopes or [])
    # admin:all 隐含全部其他 Scope（docs/03 §1.4）
    if ApiScope.ADMIN_ALL.value in token_scopes:
        token_scopes = set(ALL_PERMISSIONS)
    effective = frozenset(token_scopes & creator_perms)

    return Principal(
        kind="api_token",
        user_id=creator.id,
        username=creator.username,
        display_name=creator.display_name,
        roles=frozenset(creator.role_codes),
        permissions=effective,
        must_change_password=bool(creator.must_change_password),
        user=creator,
        api_token_id=row.id,
    )


async def resolve_principal(request: Request, session: AsyncSession) -> Principal | None:
    """解析凭证，无凭证时返回 `None`。

    **只有「完全没有 Authorization 头」才算匿名**；带了但无效的凭证一律抛错，
    否则前端的「401 → 静默 refresh → 重放」拦截器不会被触发，
    用户会在会话过期后静默降级成匿名身份。
    """
    token = bearer_token(request)
    if not token:
        return None

    if token.startswith(API_TOKEN_PREFIX):
        principal = await _principal_from_api_token(session, token)
        request.state.user_id = principal.user_id
        return principal

    payload = decode_access_token(token)
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise UnauthenticatedError("凭证内容不合法") from exc

    user = await session.get(User, user_id)
    if user is None:
        raise UnauthenticatedError("用户不存在")
    if user.status != UserStatus.ACTIVE.value:
        raise AccountDisabledError()

    roles = frozenset(user.role_codes)
    principal = Principal(
        kind="user",
        user_id=user.id,
        username=user.username,
        display_name=user.display_name,
        roles=roles,
        permissions=permissions_for_roles(roles),
        # 从库里读，而不是信 JWT 里的副本 —— 改密成功后拦截立即解除，
        # 不需要等 access token 过期。
        must_change_password=bool(user.must_change_password),
        user=user,
    )
    # 供访问日志记录 user_id（中间件在响应阶段读取）
    request.state.user_id = principal.user_id
    return principal


async def get_principal(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> Principal:
    """必须已认证。未携带凭证 → 401 `UNAUTHENTICATED`。"""
    principal = await resolve_principal(request, session)
    if principal is None:
        raise UnauthenticatedError()
    return principal


_mark_auth(get_principal)


@_mark_password_gate
async def require_authenticated(
    principal: Annotated[Principal, Depends(get_principal)],
) -> Principal:
    """强制改密拦截（FR-AUTH-03）。

    `must_change_password = true` 时，除 `/auth/*`、`/meta`、`/healthz`、`/readyz`
    外一律返回 `403 PASSWORD_CHANGE_REQUIRED`。

    这个依赖被 `require_role` / `require_scope` / 门户入口依赖复用，
    所以「新接口忘了加改密判断」在结构上就不可能发生。
    """
    if principal.must_change_password:
        raise PasswordChangeRequiredError()
    return principal


async def get_current_user(
    principal: Annotated[Principal, Depends(get_principal)],
) -> User:
    """当前用户 ORM 对象。**不做改密拦截** —— `/auth/me` 与
    `/auth/change-password` 必须在 `must_change_password = true` 时仍可访问。
    """
    return principal.user


_mark_auth(get_current_user)


def require_role(*roles: str) -> Any:
    """角色守卫（docs/01 §3.2 权限矩阵）。

    内部依赖 `require_authenticated`，因此自动带强制改密拦截。
    """

    async def _dep(
        principal: Annotated[Principal, Depends(require_authenticated)],
    ) -> Principal:
        if not (principal.roles & set(roles)):
            raise ForbiddenError(f"需要以下角色之一：{', '.join(sorted(roles))}")
        return principal

    _dep.__name__ = f"require_role_{'_'.join(sorted(roles))}"
    return _mark_auth(_dep)


def require_scope(*scopes: str) -> Any:
    """Scope 守卫（docs/03 §1.4 / §6.2）。

    - API Token：要求 Token 的有效 Scope 覆盖所需集合
    - JWT：角色已在 `require_role` 里判定过，这里放行
      （「任意已登录用户」的接口只需 `require_scope("tools:read")`）
    """

    async def _dep(
        principal: Annotated[Principal, Depends(require_authenticated)],
    ) -> Principal:
        if principal.is_api_token:
            missing = set(scopes) - principal.permissions
            if missing:
                raise ScopeMissingError(sorted(missing))
        return principal

    _dep.__name__ = f"require_scope_{'_'.join(sorted(scopes))}"
    return _mark_auth(_dep)


@dataclass(frozen=True)
class PortalAccess:
    """门户读取入口的身份上下文。

    `principal` 为 `None` 表示匿名浏览（只有在
    `portal.allow_anonymous_view = true` 时才可能走到这里）。
    """

    principal: Principal | None
    request_id: str

    @property
    def user_id(self) -> int | None:
        return self.principal.user_id if self.principal else None

    @property
    def roles(self) -> frozenset[str]:
        return self.principal.roles if self.principal else frozenset()

    @property
    def can_download(self) -> bool:
        return bool(self.principal and self.principal.can_download)


async def portal_access(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> PortalAccess:
    """门户读取接口的统一入口依赖（`/tools`、`/categories`、`/tags`）。

    规则：
      - 带了有效凭证 → 正常身份；但 `must_change_password = true` 时
        返回 403 `PASSWORD_CHANGE_REQUIRED`
      - 完全匿名 → 由 `portal.allow_anonymous_view` 决定放行还是 401
        （契约 §6 对 `/tools` 的明确要求）
      - API Token 额外要求 `tools:read`
    """
    principal = await resolve_principal(request, session)

    if principal is not None:
        if principal.must_change_password:
            raise PasswordChangeRequiredError()
        if principal.is_api_token and ApiScope.TOOLS_READ.value not in principal.permissions:
            raise ScopeMissingError([ApiScope.TOOLS_READ.value])
    else:
        allowed = await settings_service.is_anonymous_view_allowed(session)
        if not allowed:
            raise UnauthenticatedError()

    return PortalAccess(principal=principal, request_id=request.state.request_id)


_mark_password_gate(portal_access)


async def get_visibility_context(
    session: Annotated[AsyncSession, Depends(get_db)],
    access: Annotated[PortalAccess, Depends(portal_access)],
) -> Any:
    """把 `PortalAccess` 编译成 SQL 可见性上下文（含所属用户组）。"""
    from app.repositories.tools import VisibilityContext

    principal = access.principal
    policy = await settings_service.get_security_policy(session)
    return await VisibilityContext.build(
        session,
        user_id=principal.user_id if principal else None,
        roles=principal.roles if principal else (),
        allow_admin_view_private=policy.allow_admin_view_private,
    )


@dataclass
class ClientInfo:
    ip: str | None
    user_agent: str | None


def get_client_info(request: Request) -> ClientInfo:
    """客户端 IP 与 UA。

    生产环境 uvicorn 以 `--proxy-headers --forwarded-allow-ips=127.0.0.1` 启动，
    因此 `request.client.host` 已是 nginx 传来的真实客户端 IP。
    """
    client = request.client
    return ClientInfo(
        ip=client.host if client else None,
        user_agent=request.headers.get("User-Agent"),
    )
