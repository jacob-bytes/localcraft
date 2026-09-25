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
    NotFoundError,
    PasswordChangeRequiredError,
    ScopeMissingError,
    TokenExpiredError,
    TokenRevokedError,
    UnauthenticatedError,
)
from app.core.permissions import ALL_PERMISSIONS, PERM_DOWNLOAD, permissions_for_roles
from app.core.security import (
    API_TOKEN_PREFIX,
    decode_access_token,
    hash_api_token,
    verify_image_signature,
)
from app.core.timeutil import ensure_utc, utcnow
from app.db.session import get_db
from app.models.enums import ApiScope, UserStatus
from app.models.user import ApiToken, User
from app.services import settings_service

#: 标记：本依赖链包含鉴权。守卫测试遍历 `app.routes` 时用它判断
#: 「这个路由到底有没有挂权限」。挂在函数对象上，闭包也能被检查到。
AUTH_DEP_ATTR = "__localcraft_requires_auth__"

#: 标记：本依赖链包含**强制改密拦截**（FR-AUTH-03）。
#: 守卫测试用它断言「除豁免前缀外，每个路由都被改密拦截覆盖」。
#: 该标记只应出现在 `require_authenticated`（及复用它的一切）与
#: `portal_access`（它在依赖内部自行做了同一判断）上。
PASSWORD_GATE_ATTR = "__localcraft_password_gate__"

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


async def _principal_from_api_token(
    session: AsyncSession, token: str, *, request: Request | None = None
) -> Principal:
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
    # 错误码划分（契约 §16.4 的裁定）：
    #   UNAUTHENTICATED —— 只用于「没带凭证 / 凭证格式非法 / 查无此凭证」
    #   TOKEN_REVOKED   —— 凭证曾经有效但已失效（被吊销、创建者被禁用）
    #   TOKEN_EXPIRED   —— 凭证过期
    # 同一个语义（凭证失效）必须在 refresh 路径与 API Token 路径上返回同一个 code，
    # 否则前端的 ErrorCode 分支会漏掉一条。
    if row is None:
        raise UnauthenticatedError("API Token 无效")
    if row.revoked_at is not None:
        raise TokenRevokedError("API Token 已被吊销")
    expires_at = ensure_utc(row.expires_at)
    if expires_at is not None and expires_at <= utcnow():
        raise TokenExpiredError("API Token 已过期")

    creator: User | None = row.creator
    if creator is None:  # pragma: no cover - 外键保证不会发生
        raise TokenRevokedError("API Token 的创建者不存在")
    if creator.status != UserStatus.ACTIVE.value:
        # 创建者被禁用 → 其签发的全部 Token 一并失效（FR-IAM-05）。
        # 消息保留说明性文字，但 code 与「Token 被吊销」保持一致。
        raise TokenRevokedError("API Token 的创建者已被禁用")

    # FR-API-05：记录 last_used_at / last_used_ip。
    # 走内存聚合批量落库（禁止逐请求 UPDATE —— SQLite 单写者，会打死写锁）。
    from app.services.counter_service import get_counter_service

    client_ip = None
    if request is not None and request.client is not None:
        client_ip = request.client.host
    get_counter_service().record_token_use(row.id, ip=client_ip)

    creator_perms = permissions_for_roles(creator.role_codes)
    token_scopes = set(row.scopes or [])
    # admin:all 隐含全部其他 Scope（docs/03 §1.4）
    if ApiScope.ADMIN_ALL.value in token_scopes:
        token_scopes = set(ALL_PERMISSIONS)
    effective = set(token_scopes & creator_perms)

    # `download` 是本项目内部的能力点，不对外暴露成 Scope。
    # docs/03 §1.4 的映射表写的是「下载工具 → 角色 user 及以上 + Scope tools:read」，
    # 所以只要 Token 有 tools:read、且**创建者**的角色本身就允许下载，
    # 就应当具备下载能力。否则 API Token 永远下不了东西（它的 scopes 里
    # 不可能出现 "download" 这个内部能力点）。
    if (
        ApiScope.TOOLS_READ.value in effective
        and PERM_DOWNLOAD in creator_perms
    ):
        effective.add(PERM_DOWNLOAD)

    effective = frozenset(effective)

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
        principal = await _principal_from_api_token(session, token, request=request)
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
    def permissions(self) -> frozenset[str]:
        """匿名访客没有任何权限点（`can_download` 等布尔值因此全为 False）。"""
        return self.principal.permissions if self.principal else frozenset()

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


@_mark_auth
async def image_access(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> PortalAccess:
    """图片接口的鉴权依赖：**能力签名** 或 **Authorization 头**，二选一（契约 §14.3）。

    为什么必须做成依赖而不是写在路由体里：守卫测试要求每个非公开路由都挂
    带 `AUTH_DEP_ATTR` 的依赖。把这个判定放在这里，守卫测试才看得见它。

    规则：
      - `?sig=` 有效 → 放行。此时 `principal` 可能为 None（匿名拿签名 URL 取图），
        路由体仍会照常做父工具可见性校验 —— **签名不是绕过可见性的后门**。
      - 签名无效/缺失 → 回退到 `resolve_principal`；没有有效凭证就 404，
        与「图片不存在」同码，避免用状态码差异探测资源。
    """
    image_id_raw = request.path_params.get("image_id")
    variant = request.query_params.get("variant") or "full"
    sig = request.query_params.get("sig")

    signed_ok = False
    if image_id_raw is not None and sig:
        try:
            signed_ok = verify_image_signature(int(image_id_raw), variant, sig)
        except (TypeError, ValueError):
            signed_ok = False

    principal = await resolve_principal(request, session)

    if not signed_ok and principal is None:
        # 两条路径都不成立 —— 与「资源不存在」同码
        raise NotFoundError(message="资源不存在")

    if principal is not None:
        if principal.must_change_password:
            raise PasswordChangeRequiredError()
        if (
            principal.is_api_token
            and ApiScope.TOOLS_READ.value not in principal.permissions
        ):
            raise ScopeMissingError([ApiScope.TOOLS_READ.value])

    return PortalAccess(principal=principal, request_id=request.state.request_id)


_mark_password_gate(image_access)


_mark_password_gate(portal_access)


async def get_visibility_context(
    session: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
) -> Any:
    """把当前请求的身份编译成 SQL 可见性上下文（含所属用户组）。

    **刻意不依赖 `portal_access`**：图片路由走「能力签名」时是匿名的
    （`<img>` 标签带不上 Authorization 头），而 `portal_access` 在
    `portal.allow_anonymous_view=false` 时会直接 401 —— 那会让所有
    签名图片全部加载失败。这里的职责只是「把已知身份翻译成可见性谓词」，
    至于「能不能访问」由各路由自己的鉴权依赖（`portal_access` /
    `image_access` / `download_access`）负责。
    """
    from app.repositories.tools import VisibilityContext

    principal = await resolve_principal(request, session)
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


# ---------------------------------------------------------------------------
# 下载入口：票据 **或** 常规凭证
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DownloadAccess:
    """下载接口的身份上下文。

    两条路径（docs/03 §1.10）：
      - `ticket` 有值：HMAC 票据就是凭证，**不需要** Authorization 头
      - 否则走常规鉴权；完全匿名时由 `portal.allow_anonymous_view` 决定

    票据里的 `tool_id` 校验放到处理器里做 —— 依赖阶段还不知道要访问哪个工具。
    """

    principal: Principal | None
    ticket: dict[str, Any] | None
    request_id: str

    @property
    def is_ticket(self) -> bool:
        return self.ticket is not None

    @property
    def ticket_user_id(self) -> int | None:
        return int(self.ticket["user_id"]) if self.ticket else None

    @property
    def ticket_version_id(self) -> int | None:
        return int(self.ticket["version_id"]) if self.ticket else None

    @property
    def user_id(self) -> int | None:
        if self.ticket is not None:
            return self.ticket_user_id
        return self.principal.user_id if self.principal else None

    @property
    def roles(self) -> frozenset[str]:
        return self.principal.roles if self.principal else frozenset()

    @property
    def can_download(self) -> bool:
        """票据用户按「普通已登录用户」处理；匿名则不可下载。"""
        if self.ticket is not None:
            return True
        return bool(self.principal and self.principal.can_download)


async def download_access(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> DownloadAccess:
    """下载专用入口依赖。

    **票据优先**：带 `?ticket=` 时不再解析 Authorization —— 票据已经把
    `user_id` 绑进去了，混用两种凭证只会让权限来源变得难以推理。
    """
    from app.services import download_service

    ticket_token = request.query_params.get("ticket")
    if ticket_token:
        # 只校验签名与有效期；tool_id 的比对在处理器里（那里才知道工具）
        try:
            from app.core.security import verify_download_ticket

            payload = verify_download_ticket(ticket_token)
        except Exception as exc:
            from app.core.errors import NotFoundError

            # 统一 404：不区分「签名错」与「已过期」，避免探测
            raise NotFoundError(
                message="资源不存在", details={"reason": "invalid_ticket"}
            ) from exc
        await download_service.check_ticket_user_active(session, payload)
        return DownloadAccess(principal=None, ticket=payload, request_id=request.state.request_id)

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

    return DownloadAccess(
        principal=principal, ticket=None, request_id=request.state.request_id
    )


# 本依赖内部已经做了改密拦截（principal.must_change_password → 403），
# 所以标成 password gate，让守卫测试的「拦截覆盖面」断言成立。
_mark_password_gate(download_access)


def require_roles_and_scope(*roles: str, scope: str) -> Any:
    """同时校验**角色**与**API Token Scope**。

    为什么需要组合守卫：
      - 只查角色 → API Token 会绕过 `scopes` 限制（它的 `roles` 是创建者的角色）
      - 只查 Scope → JWT 请求不受限（`require_scope` 对 JWT 直接放行）

    `/me/*` 的写接口两个都要：docs/01 §3.2 要求 viewer 不能上传，
    docs/03 §1.4 又要求 API Token 具备 `tools:write`。
    """

    async def _dep(
        principal: Annotated[Principal, Depends(require_authenticated)],
    ) -> Principal:
        if not (principal.roles & set(roles)):
            raise ForbiddenError(f"需要以下角色之一：{', '.join(sorted(roles))}")
        if principal.is_api_token:
            missing = {scope} - principal.permissions
            if missing:
                raise ScopeMissingError(sorted(missing))
        return principal

    _dep.__name__ = f"require_roles_{'_'.join(sorted(roles))}_scope_{scope}"
    return _mark_auth(_dep)
