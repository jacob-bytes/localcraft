"""门户工具列表路由（`GET /api/v1/tools`）。

**M1 只实现列表**；`/tools/{slug}` 详情与所有写接口属于 M2（契约 §6、§12）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    DownloadAccess,
    PortalAccess,
    download_access,
    get_client_info,
    get_visibility_context,
    portal_access,
)
from app.core.errors import (
    DownloadNotAllowedError,
    InvalidSortError,
    NotFoundError,
    ValidationError,
)
from app.core.pagination import PageParams
from app.db.session import get_db
from app.models.enums import PortalSort, RoleCode, ToolType, VersionStatus
from app.repositories import downloads as downloads_repo
from app.repositories import tags as tags_repo
from app.repositories import tool_versions as versions_repo
from app.repositories import tools as tools_repo
from app.repositories.tools import (
    SORT_EXPRESSIONS,
    VALID_TOOL_TYPES,
    PortalFilters,
    VisibilityContext,
)
from app.schemas.me import DailyStatPoint, ToolStatsResponse
from app.schemas.tool import DownloadTicketResponse, ToolDetail, ToolListResponse
from app.schemas.version import SkillPreviewResponse, VersionSummary, VersionUploader
from app.services import download_service, tool_service
from app.services.counter_service import get_counter_service, viewer_key_for
from app.services.download_service import acl_allows_download

router = APIRouter(tags=["portal"])


@router.get("/tools", response_model=ToolListResponse, summary="门户工具列表")
async def list_tools(
    session: Annotated[AsyncSession, Depends(get_db)],
    access: Annotated[PortalAccess, Depends(portal_access)],
    visibility: Annotated[VisibilityContext, Depends(get_visibility_context)],
    params: Annotated[PageParams, Depends()],
    q: Annotated[str | None, Query(description="关键词，搜索名称、简介、详情、标签")] = None,
    category: Annotated[list[str] | None, Query(description="分类 slug，可重复传")] = None,
    tag: Annotated[list[str] | None, Query(description="标签名，可重复传")] = None,
    tool_type: Annotated[
        list[str] | None, Query(alias="type", description="file/webapp/skill/prompt，可重复传")
    ] = None,
    owner: Annotated[str | None, Query(description="按作者 username 筛选")] = None,
    sort: Annotated[
        str | None,
        Query(description="hot（默认）/ new / name / -updated_at"),
    ] = None,
) -> ToolListResponse:
    """门户主列表。

    几个要点：

    - **`sort` 白名单校验**，非法值返回 400 `INVALID_SORT` 而不是 500；
      排序表达式从 `SORT_EXPRESSIONS` 查表得到，**绝不把 `sort` 拼进 SQL**
      （docs/03 §1.6）。
    - **`facets` 只在 `page == 1` 时返回**，翻页时整个键省略（docs/03 §3.3）。
    - 可见性判定下推到 SQL（EXISTS 子查询），不在 Python 里过滤
      （docs/01 §4.3 性能要求）。
    - 未登录用户由 `portal.allow_anonymous_view` 决定 401 还是返回公开数据
      （契约 §6），该判断在 `portal_access` 依赖里完成。
    """
    effective_sort = sort or PortalSort.HOT.value
    if effective_sort not in SORT_EXPRESSIONS:
        raise InvalidSortError(
            message=f"不支持的排序方式: {effective_sort}",
            details={"allowed": sorted(SORT_EXPRESSIONS)},
        )

    types = tool_type or []
    invalid_types = [t for t in types if t not in VALID_TOOL_TYPES]
    if invalid_types:
        raise ValidationError(
            message="工具类型非法",
            fields=[
                {"field": "type", "message": f"不支持的工具类型: {t}"} for t in invalid_types
            ],
        )

    filters = PortalFilters(
        q=(q or "").strip() or None,
        # 分类切片去重并保持稳定顺序，避免同一 slug 重复传导致 IN 列表膨胀
        category_slugs=sorted({c for c in (category or []) if c}),
        # 标签入参做与写入侧一致的归一化（FR-TAX-03），
        # 所以前端可以用 display_name 回填 URL
        tag_names=sorted({tags_repo.normalize_tag_name(t) for t in (tag or []) if t.strip()}),
        tool_types=sorted(set(types)),
        owner_username=(owner or "").strip().lower() or None,
    )

    return await tool_service.list_portal_tools(
        session,
        visibility=visibility,
        filters=filters,
        sort=effective_sort,
        page=params.page,
        page_size=params.page_size,
        viewer_roles=access.roles,
        can_download=access.can_download,
    )


# ===========================================================================
# M2：详情 / 版本列表 / skill 预览 / 下载票据 / 下载 / 统计
# ===========================================================================
@router.get("/tools/{slug}", response_model=ToolDetail, summary="工具详情")
async def get_tool_detail(
    slug: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    access: Annotated[PortalAccess, Depends(portal_access)],
    visibility: Annotated[VisibilityContext, Depends(get_visibility_context)],
    request: Request = None,  # type: ignore[assignment]
) -> ToolDetail:
    """无权访问返回 **404**（不是 403）—— FR-FILE-08，避免探测资源存在性。

    浏览量在这里累加（内存计数器，不写库）。
    """
    tool = await tools_repo.get_visible_by_slug(session, slug, visibility=visibility)
    if tool is None:
        raise NotFoundError(message="资源不存在")

    # 浏览计数：内存累加 + 去重（同一用户 1 小时内只计一次）
    counters = get_counter_service()
    client = get_client_info(request) if request is not None else None
    counters.record_view(
        tool.id,
        viewer_key=viewer_key_for(
            access.user_id, client.ip if client else None
        ),
        dedup_minutes=await _view_dedup_minutes(session),
    )

    return await tool_service.build_detail(
        session,
        tool=tool,
        user_id=access.user_id,
        roles=access.roles,
        permissions=access.permissions,
        visibility_ok=True,
        include_acl=True,
    )


async def _view_dedup_minutes(session: AsyncSession) -> int:
    from app.repositories import system_settings as settings_repo

    return await settings_repo.get_effective_int(session, "stats.view_dedup_minutes", 60)


@router.get(
    "/tools/{slug}/versions",
    response_model=list[VersionSummary],
    summary="版本列表（含待审、已驳回、已归档）",
)
async def list_tool_versions(
    slug: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    access: Annotated[PortalAccess, Depends(portal_access)],
    visibility: Annotated[VisibilityContext, Depends(get_visibility_context)],
) -> list[VersionSummary]:
    """对**有可见性权限的用户**开放（FR-VER-06）。

    `purged` 版本仍然出现在列表里但 `can_download=false` —— 界面显示「已归档」，
    而不是把它藏起来（那样用户会以为版本丢了）。
    """
    tool = await tools_repo.get_visible_by_slug(session, slug, visibility=visibility)
    if tool is None:
        raise NotFoundError(message="资源不存在")

    rows = await versions_repo.list_for_tool(session, tool.id, order_desc=True)
    is_owner = access.user_id == tool.owner_id
    is_approver = bool(
        set(access.roles) & {RoleCode.APPROVER.value, RoleCode.SUPERADMIN.value}
    )
    can_download_any = access.can_download
    result: list[VersionSummary] = []
    for row in rows:
        # 待审/已驳回的版本只对 owner 与审批人可见，避免提前泄露未发布内容
        if row.status in (
            VersionStatus.PENDING.value,
            VersionStatus.REJECTED.value,
        ) and not (is_owner or is_approver):
            # 待审/已驳回只对 owner 与审批人可见
            continue
        result.append(
            VersionSummary(
                id=row.id,
                tool_id=row.tool_id,
                version=row.version,
                changelog_md=row.changelog_md,
                status=row.status,
                is_current=row.is_current,
                file_name=row.file_name,
                file_size=row.file_size,
                file_sha256_short=(row.file_sha256 or "")[:16] or None,
                file_ext=row.file_ext,
                mime_type=row.mime_type,
                uploaded_by=(
                    VersionUploader(
                        id=row.uploader.id, display_name=row.uploader.display_name
                    )
                    if row.uploader is not None
                    else None
                ),
                approved_at=row.approved_at,
                reject_reason=row.reject_reason,
                purged_at=row.purged_at,
                created_at=row.created_at,
                can_download=bool(
                    can_download_any
                    and row.status
                    in (VersionStatus.APPROVED.value, VersionStatus.SUPERSEDED.value)
                    and row.storage_path
                ),
            )
        )
    return result


@router.get(
    "/tools/{slug}/versions/{version}/skill-preview",
    response_model=SkillPreviewResponse,
    summary="Skill 包预览（manifest / readme / 文件树）",
)
async def skill_preview(
    slug: str,
    version: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    access: Annotated[PortalAccess, Depends(portal_access)],
    visibility: Annotated[VisibilityContext, Depends(get_visibility_context)],
) -> SkillPreviewResponse:
    tool = await tools_repo.get_visible_by_slug(session, slug, visibility=visibility)
    if tool is None:
        raise NotFoundError(message="资源不存在")
    if tool.tool_type != ToolType.SKILL:
        raise NotFoundError(message="资源不存在", details={"reason": "not_a_skill"})

    row = await versions_repo.get_by_version(session, tool.id, version)
    if row is None:
        raise NotFoundError(message="资源不存在", details={"version": version})
    is_owner = access.user_id == tool.owner_id
    is_approver = bool(
        set(access.roles) & {RoleCode.APPROVER.value, RoleCode.SUPERADMIN.value}
    )
    if row.status in (VersionStatus.PENDING.value, VersionStatus.REJECTED.value) and not (
        is_owner or is_approver
    ):
        raise NotFoundError(message="资源不存在")

    tree = row.skill_file_tree or []
    total_size = sum(int(item.get("size") or 0) for item in tree)
    return SkillPreviewResponse(
        version=row.version,
        manifest=row.skill_manifest,
        readme_md=row.skill_readme_md,
        file_tree=tree,
        # 直接读持久化列（contracts §15.3）。M2 用 `file_count < len(tree)` 推断，
        # 因为「文件数」与「含目录的条目数」口径不一致，在所有小包上误报 true。
        file_tree_truncated=bool(row.skill_tree_truncated),
        total_size=total_size,
    )


@router.post(
    "/tools/{slug}/download-ticket",
    response_model=DownloadTicketResponse,
    summary="签发下载票据",
)
async def create_download_ticket_endpoint(
    slug: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    access: Annotated[PortalAccess, Depends(portal_access)],
    visibility: Annotated[VisibilityContext, Depends(get_visibility_context)],
    version_id: Annotated[int | None, Query()] = None,
) -> DownloadTicketResponse:
    """签发 60 秒有效的 HMAC 票据（docs/03 §3.15）。

    票据绑定 `user_id`：泄露给别人也用不了。允许在有效期内多次使用，
    因为浏览器会为 Range 请求或重试发多次请求。
    """
    if access.principal is None:
        # 匿名不允许签发票据 —— 否则等于把下载权交给了任何人
        from app.core.errors import UnauthenticatedError

        raise UnauthenticatedError()
    if not access.can_download:
        raise NotFoundError(message="资源不存在", details={"reason": "no_download_permission"})

    tool = await tools_repo.get_visible_by_slug(session, slug, visibility=visibility)
    if tool is None:
        raise NotFoundError(message="资源不存在")

    # J-2：ACL 条目级的下载权限（可见但不可下载）。放在可见性判定之后 ——
    # 「角色不够」用 404 隐藏资源存在性（FR-FILE-08），
    # 「ACL 明确拒绝」用 403 说明原因（用户能看到详情页，返回 404 只会让他困惑）。
    if not await acl_allows_download(session, tool=tool, visibility=visibility):
        raise DownloadNotAllowedError()

    target = await download_service.resolve_target(
        session, tool=tool, version_id=version_id
    )
    _, url, expires_at = download_service.issue_ticket(
        tool=tool, version=target.version, user_id=access.user_id or 0
    )
    return DownloadTicketResponse(
        url=url,
        expires_at=expires_at,
        file_name=target.file_name,
        file_size=target.file_size,
        file_sha256=target.version.file_sha256,
    )


@router.get(
    "/tools/{slug}/download",
    summary="下载（支持 Range 断点续传）",
    response_class=FileResponse,
)
async def download_tool(
    slug: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    dl: Annotated[DownloadAccess, Depends(download_access)],
    version_id: Annotated[
        int | None, Query(description="指定版本；不传则下载当前版本")
    ] = None,
) -> FileResponse:
    """走 ASGI `FileResponse`（sendfile 路径）。

    鉴权两条路径：
      - `?ticket=` 有效票据 → 不需要 Authorization 头
      - 否则常规凭证；匿名再看 `portal.allow_anonymous_view`

    `FileResponse` 自带 Range 支持（返回 206 + `Content-Range`），
    所以断点续传不用自己实现。
    """
    tool = await tools_repo.get_by_slug(session, slug)
    if tool is None:
        raise NotFoundError(message="资源不存在")

    # 票据路径用票据里绑定的版本；否则用查询参数（不传则当前版本）。
    # 这里必须显式区分：票据里的 version_id 是签发时锁定的，
    # 不允许被查询参数覆盖，否则「票据绑定的版本」就形同虚设。
    if dl.is_ticket:
        version_id = dl.ticket_version_id
    if dl.is_ticket:
        # 票据里的 tool_id 必须与路径一致，否则可以拿 A 的票据下 B
        if int(dl.ticket["tool_id"]) != tool.id:
            raise NotFoundError(message="资源不存在", details={"reason": "ticket_tool_mismatch"})
        # 票据签发后可见性可能已经变了 —— 仍然按票据用户的当前权限复核
        visibility = await VisibilityContext.build(
            session,
            user_id=dl.ticket_user_id,
            roles=await _roles_for_user(session, dl.ticket_user_id),
            allow_admin_view_private=await _allow_admin_view_private(session),
        )
        if await tools_repo.get_visible_by_id(session, tool.id, visibility=visibility) is None:
            raise NotFoundError(message="资源不存在")
        if not await _has_download_permission(session, dl.ticket_user_id):
            raise NotFoundError(message="资源不存在", details={"reason": "no_download_permission"})
        # J-2：ACL 条目级判定。用**票据用户**的 visibility 复核 ——
        # 票据签发后可见性/授权可能已变，与上面的可见性复核同理。
        if not await acl_allows_download(session, tool=tool, visibility=visibility):
            raise DownloadNotAllowedError()
    else:
        visibility = await VisibilityContext.build(
            session,
            user_id=dl.user_id,
            roles=dl.roles,
            allow_admin_view_private=await _allow_admin_view_private(session),
        )
        if await tools_repo.get_visible_by_id(session, tool.id, visibility=visibility) is None:
            raise NotFoundError(message="资源不存在")
        # FR-ACL-05：viewer 即使在 public 工具上也不能下载。
        # 返回 404 而不是 403 —— 与其它无权场景保持一致（FR-FILE-08）。
        if not dl.can_download:
            raise NotFoundError(
                message="资源不存在", details={"reason": "no_download_permission"}
            )
        # J-2：ACL 条目级判定（可见但不可下载）
        if not await acl_allows_download(session, tool=tool, visibility=visibility):
            raise DownloadNotAllowedError()

    target = await download_service.resolve_target(
        session, tool=tool, version_id=version_id
    )

    # 计数与明细入内存队列（不写库，由后台任务批量落库）
    get_counter_service().record_download(
        tool_id=tool.id,
        version_id=target.version.id,
        user_id=dl.user_id,
        ip=None,
        user_agent=None,
        via_api_token_id=dl.principal.api_token_id if dl.principal else None,
    )

    headers = {
        "Content-Disposition": download_service.content_disposition(target.file_name),
        "X-Content-Type-Options": "nosniff",
        # 支持 Range 的显式声明，便于前端与下载管理器识别
        "Accept-Ranges": "bytes",
        "Content-SHA256": target.version.file_sha256 or "",
        # 下载内容可能随版本变化，禁止共享缓存
        "Cache-Control": "private, no-store",
    }
    return FileResponse(
        target.absolute_path,
        media_type=download_service.guess_media_type(target.version),
        headers=headers,
    )


async def _roles_for_user(session: AsyncSession, user_id: int | None) -> frozenset[str]:
    if user_id is None:
        return frozenset()
    from app.models.user import User as _User

    user = await session.get(_User, user_id)
    return frozenset(user.role_codes) if user is not None else frozenset()


async def _has_download_permission(session: AsyncSession, user_id: int | None) -> bool:
    if user_id is None:
        return False
    from app.core.permissions import PERM_DOWNLOAD
    from app.models.user import User as _User

    user = await session.get(_User, user_id)
    if user is None:
        return False
    from app.core.permissions import permissions_for_roles

    return PERM_DOWNLOAD in permissions_for_roles(user.role_codes)


async def _allow_admin_view_private(session: AsyncSession) -> bool:
    from app.repositories import system_settings as settings_repo

    return await settings_repo.get_effective_bool(
        session, "portal.allow_admin_view_private", True
    )


@router.get("/tools/{slug}/stats", response_model=ToolStatsResponse, summary="工具统计")
async def tool_stats(
    slug: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    access: Annotated[PortalAccess, Depends(portal_access)],
    visibility: Annotated[VisibilityContext, Depends(get_visibility_context)],
) -> ToolStatsResponse:
    tool = await tools_repo.get_visible_by_slug(session, slug, visibility=visibility)
    if tool is None:
        raise NotFoundError(message="资源不存在")
    del access
    daily = await downloads_repo.list_daily_for_tool(session, tool.id, days=30)
    return ToolStatsResponse(
        tool_id=tool.id,
        download_count=tool.download_count,
        view_count=tool.view_count,
        daily=[
            DailyStatPoint(
                stat_date=row.stat_date.isoformat(), views=row.views, downloads=row.downloads
            )
            for row in reversed(daily)
        ],
    )
