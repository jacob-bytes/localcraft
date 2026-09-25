"""下载服务：票据签发/校验、可见性校验、Content-Disposition。

两条鉴权路径（docs/03 §1.10 + §3.15）：

  1. `Authorization: Bearer <access_token>` —— 普通前端请求
  2. `?ticket=<HMAC>` —— 一次性短时效票据，用于 `<a href>` 原生下载

票据是**唯一可以不带 Authorization 头**的凭证，所以下载路由自己负责鉴权
（不挂常规鉴权依赖）。票据的校验在 `app.core.security`，
这里只负责「拿到版本 → 校验可见性/状态 → 组装响应头」。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.security import (
    DOWNLOAD_TICKET_TTL_SECONDS,
    TicketError,
    TicketExpiredError,
    create_download_ticket,
    verify_download_ticket,
)
from app.core.timeutil import ensure_utc, utcnow
from app.models.enums import ToolStatus, ToolVisibility, VersionStatus
from app.models.tool import Tool, ToolVersion
from app.repositories import tool_acl as tool_acl_repo
from app.repositories import tool_versions as versions_repo
from app.repositories.tools import VisibilityContext
from app.storage import get_storage

logger = logging.getLogger(__name__)

#: 可下载的工具状态（docs/01 §4.1 的「可下载」列）
DOWNLOADABLE_TOOL_STATUSES: frozenset[str] = frozenset(
    {ToolStatus.APPROVED.value, ToolStatus.PENDING_UPDATE.value}
)

#: 可下载的版本状态。`purged` **不可下载**（FR-VER-08：已归档）；
#: `pending` / `rejected` 也不可（还没过审）。
DOWNLOADABLE_VERSION_STATUSES: frozenset[str] = frozenset(
    {VersionStatus.APPROVED.value, VersionStatus.SUPERSEDED.value}
)


@dataclass(frozen=True)
class DownloadTarget:
    tool: Tool
    version: ToolVersion
    absolute_path: str  # 相对 DATA_DIR 的 storage_path
    file_name: str
    file_size: int


def content_disposition(file_name: str) -> str:
    """RFC 5987 / RFC 6266 的 `Content-Disposition`，支持中文文件名。

    同时给出 `filename=` 的 ASCII 回退与 `filename*=UTF-8''` 的百分号编码版本 ——
    老浏览器只认前者，现代浏览器优先后者。直接塞原始中文会让头部非法。
    """
    safe_ascii = "".join(
        ch if (ch.isascii() and ch not in '"\\') else "_" for ch in file_name
    ) or "download"
    encoded = quote(file_name, safe="")
    return f"attachment; filename=\"{safe_ascii}\"; filename*=UTF-8''{encoded}"


def guess_media_type(version: ToolVersion) -> str:
    """给出下载的 Content-Type。

    刻意**不**沿用上传时的 `mime_type`：那是客户端声明的，可能撒谎。
    统一用 `application/octet-stream` 让浏览器走下载而不是尝试内联渲染
    （否则一个 `.html` 包会被浏览器当页面打开，形成存储型 XSS 面）。
    """
    return "application/octet-stream"


async def resolve_target(
    session: AsyncSession,
    *,
    tool: Tool,
    version_id: int | None,
    allow_purged_hint: bool = False,
) -> DownloadTarget:
    """把 `(tool, version_id?)` 解析成一个可下载目标。

    任一条件不满足都抛 **404**（不是 403）—— FR-FILE-08：403 会泄露资源存在性。
    """
    if tool.status not in DOWNLOADABLE_TOOL_STATUSES:
        raise NotFoundError(
            message="资源不存在", details={"reason": "tool_not_downloadable"}
        )

    version: ToolVersion | None
    if version_id is None:
        version = (
            await versions_repo.get_by_id(session, tool.current_version_id)
            if tool.current_version_id
            else None
        )
    else:
        version = await versions_repo.get_by_id(session, version_id)
        # 版本必须属于这个工具 —— 否则可以用 A 工具的 slug 下载 B 工具的版本
        if version is not None and version.tool_id != tool.id:
            version = None

    if version is None:
        raise NotFoundError(message="资源不存在", details={"reason": "version_not_found"})

    if version.status not in DOWNLOADABLE_VERSION_STATUSES:
        hint = "该版本已归档，不可下载" if version.status == VersionStatus.PURGED.value else None
        raise NotFoundError(
            message="资源不存在",
            details={
                "reason": "version_not_downloadable",
                "version_status": version.status,
                **({"hint": hint} if (hint and allow_purged_hint) else {}),
            },
        )

    if not version.storage_path:
        # webapp / prompt 类型没有文件
        raise NotFoundError(message="资源不存在", details={"reason": "no_file"})

    absolute = get_storage().absolute(version.storage_path)
    if not absolute.is_file():
        logger.warning(
            "数据库有版本但磁盘文件缺失 tool=%s version=%s path=%s",
            tool.slug,
            version.version,
            version.storage_path,
        )
        raise NotFoundError(message="资源不存在", details={"reason": "file_missing"})

    return DownloadTarget(
        tool=tool,
        version=version,
        absolute_path=str(absolute),
        file_name=version.file_name or absolute.name,
        file_size=int(version.file_size or absolute.stat().st_size),
    )


async def acl_allows_download(
    session: AsyncSession,
    *,
    tool: Tool,
    visibility: VisibilityContext,
) -> bool:
    """J-2（contracts/CONTRACT.md §20.4）：ACL 条目的 `can_download` 是否放行。

    `tool_acl.can_download` 此前**前后端都存、docs/03 §3.11 描述为生效字段、
    但没有任何读取点** —— 「取消允许下载」静默无效。这里把它接上。

    判定规则（按 docs/01 §4.3 的可见性算法延伸）：

    - owner 与 superadmin 不受限（他们本就能绕过 ACL）
    - 非 `restricted` 可见性不涉及 ACL，一律放行
    - `restricted` 下：取**全部命中的条目**（直接授权 + 所属组授权），
      **任一命中条目 `can_download=true` 即放行**；命中但全部为 false 才拒绝
    - 一条都没命中（理论上不该发生，因为调用方已先过了可见性检查）→ 放行，
      避免因为判定顺序问题把正常用户挡在门外
    """
    if tool.owner_id == visibility.user_id or visibility.is_superadmin:
        return True
    if tool.visibility != ToolVisibility.RESTRICTED.value:
        return True

    entries = await tool_acl_repo.list_for_tool(session, tool.id)
    wanted_groups = set(visibility.group_ids)
    matched = [
        e
        for e in entries
        if (e.subject_type == "user" and e.subject_id == visibility.user_id)
        or (e.subject_type == "group" and e.subject_id in wanted_groups)
    ]
    if not matched:
        return True
    return any(e.can_download for e in matched)


def issue_ticket(*, tool: Tool, version: ToolVersion, user_id: int) -> tuple[str, str, object]:
    """签发下载票据，返回 `(token, url, expires_at)`。"""
    token, expires_at = create_download_ticket(
        tool_id=tool.id,
        version_id=version.id,
        user_id=user_id,
        ttl_seconds=DOWNLOAD_TICKET_TTL_SECONDS,
    )
    url = (
        f"/api/v1/tools/{tool.slug}/download"
        f"?version_id={version.id}&ticket={quote(token, safe='')}"
    )
    return token, url, expires_at


def decode_ticket(token: str, *, tool: Tool) -> dict:
    """校验票据。失败抛 404（不暴露「票据过期」与「资源不存在」的区别）。

    票据里绑定了 `user_id`，所以票据泄露给别人也用不了；
    同时校验 `tool_id` 与请求路径一致，防止拿 A 工具的票据去下 B 工具。
    """
    try:
        payload = verify_download_ticket(token)
    except (TicketError, TicketExpiredError) as exc:
        raise NotFoundError(
            message="资源不存在", details={"reason": "invalid_ticket", "detail": str(exc)}
        ) from exc

    if int(payload.get("tool_id", -1)) != tool.id:
        raise NotFoundError(message="资源不存在", details={"reason": "ticket_tool_mismatch"})
    return payload


async def check_ticket_user_active(session: AsyncSession, payload: dict) -> None:
    """票据绑定的用户必须仍然可用（被禁用后票据立即失效）。"""
    from app.models.user import User

    user = await session.get(User, int(payload["user_id"]))
    if user is None or user.status != "active":
        raise NotFoundError(message="资源不存在", details={"reason": "ticket_user_inactive"})


def ticket_expired(expires_at) -> bool:
    moment = ensure_utc(expires_at)
    return moment is not None and moment <= utcnow()


__all__ = [
    "DOWNLOADABLE_TOOL_STATUSES",
    "DOWNLOADABLE_VERSION_STATUSES",
    "DownloadTarget",
    "check_ticket_user_active",
    "content_disposition",
    "decode_ticket",
    "guess_media_type",
    "issue_ticket",
    "resolve_target",
]
