"""个人中心路由（契约 §6.1 的 19 个接口）。

写接口用 `require_roles_and_scope` 组合守卫：
  - 角色：viewer 不能创建/上传（docs/01 §3.2）
  - Scope：API Token 必须有 `tools:write`（docs/03 §1.4）
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Principal, require_roles_and_scope
from app.core.errors import NotFoundError, StateConflictError, ValidationError
from app.core.pagination import Page, PageParams
from app.core.permissions import permissions_for_roles
from app.core.timeutil import utcnow
from app.db.session import get_db
from app.models.enums import ApiScope, ImageKind, RoleCode, ToolStatus, ToolType, VersionStatus
from app.models.tool import Tool, ToolImage, ToolVersion
from app.repositories import downloads as downloads_repo
from app.repositories import tool_images as images_repo
from app.repositories import tool_versions as versions_repo
from app.repositories import tools as tools_repo
from app.schemas.acl import AclReplaceRequest, AclResponse
from app.schemas.me import (
    DownloadLogOut,
    ImageOut,
    ImagePatchRequest,
    ProfileOut,
    ProfileUpdateRequest,
    UsageOut,
)
from app.schemas.tool import (
    MyToolListItem,
    MyToolListResponse,
    SubmitResponse,
    ToolCreateRequest,
    ToolDetail,
    ToolUpdateRequest,
    WithdrawResponse,
)
from app.schemas.version import (
    VersionPatchRequest,
    VersionSummary,
    VersionUploader,
    VersionUploadResponse,
)
from app.services import (
    acl_service,
    approval_service,
    image_service,
    skill_service,
    version_service,
)
from app.services.tool_service import (
    STATUS_LABELS,
    allocate_slug,
    build_detail,
    build_image_out,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/me", tags=["me"])

#: 读守卫：任意已登录用户（含 viewer）
read_guard = require_roles_and_scope(
    RoleCode.VIEWER.value,
    RoleCode.USER.value,
    RoleCode.APPROVER.value,
    RoleCode.SUPERADMIN.value,
    scope=ApiScope.TOOLS_READ.value,
)
#: 写守卫：viewer 不在列表里（docs/01 §3.2）
write_guard = require_roles_and_scope(
    RoleCode.USER.value,
    RoleCode.APPROVER.value,
    RoleCode.SUPERADMIN.value,
    scope=ApiScope.TOOLS_WRITE.value,
)

#: 分块大小（1 MiB）—— 与 storage 层一致
CHUNK_SIZE = 1024 * 1024


# ===========================================================================
# 个人资料与统计
# ===========================================================================
@router.get("/profile", response_model=ProfileOut, summary="个人资料与用量")
async def get_profile(
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(read_guard)],
) -> ProfileOut:
    user = principal.user
    return ProfileOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        roles=user.role_codes,
        permissions=sorted(permissions_for_roles(user.role_codes)),
        status=user.status,
        auth_source=user.auth_source,
        must_change_password=bool(user.must_change_password),
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        usage=await _build_usage(session, user.id),
    )


@router.patch("/profile", response_model=ProfileOut, summary="修改显示名与邮箱")
async def update_profile(
    body: ProfileUpdateRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(read_guard)],
) -> ProfileOut:
    """FR-AUTH-11：只允许改展示名与邮箱（username 创建后不可改）。"""
    user = principal.user
    if body.display_name is not None:
        user.display_name = body.display_name.strip()
    if body.email is not None:
        user.email = body.email.strip() or None
    user.updated_at = utcnow()
    await session.commit()
    return await get_profile(session, principal)


@router.get("/stats", response_model=UsageOut, summary="我的统计与配额")
async def get_my_stats(
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(read_guard)],
) -> UsageOut:
    return await _build_usage(session, principal.user_id)


async def _build_usage(session: AsyncSession, user_id: int) -> UsageOut:
    from app.services import settings_service

    status_counts = await tools_repo.owner_status_counts(session, user_id)
    used_bytes = await versions_repo.sum_file_size_for_user(session, user_id)
    platform_used = await versions_repo.sum_file_size_all(session)
    limits = await settings_service.get_upload_limits(session)

    version_count = len(await versions_repo.list_all_for_owner(session, user_id))
    download_count = sum(
        t.download_count
        for t in await tools_repo.list_owned_tools(
            session, owner_id=user_id, limit=1000, offset=0
        )
    )
    quota = limits.per_user_quota_bytes
    return UsageOut(
        tool_count=sum(status_counts.values()),
        published_tool_count=status_counts.get(ToolStatus.APPROVED.value, 0)
        + status_counts.get(ToolStatus.PENDING_UPDATE.value, 0),
        draft_tool_count=status_counts.get(ToolStatus.DRAFT.value, 0),
        pending_tool_count=status_counts.get(ToolStatus.PENDING.value, 0)
        + status_counts.get(ToolStatus.PENDING_UPDATE.value, 0),
        version_count=version_count,
        download_count=download_count,
        used_bytes=used_bytes,
        quota_bytes=quota,
        total_quota_bytes=limits.total_quota_bytes,
        platform_used_bytes=platform_used,
        used_percent=round(used_bytes / quota * 100, 2) if quota > 0 else 0.0,
    )


# ===========================================================================
# 我的工具
# ===========================================================================
@router.get("/tools", response_model=MyToolListResponse, summary="我的工具列表")
async def list_my_tools(
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(read_guard)],
    params: Annotated[PageParams, Depends()],
    status: Annotated[list[str] | None, Query()] = None,
    tool_type: Annotated[list[str] | None, Query(alias="type")] = None,
    category: Annotated[list[str] | None, Query()] = None,
    q: Annotated[str | None, Query()] = None,
    sort: Annotated[str | None, Query()] = None,
) -> MyToolListResponse:
    """含草稿/待审/驳回/下架，**排除软删除**。"""
    from app.repositories.tools import SORT_EXPRESSIONS

    effective_sort = sort or "-updated_at"
    if effective_sort not in SORT_EXPRESSIONS:
        from app.core.errors import InvalidSortError

        raise InvalidSortError(
            message=f"不支持的排序方式: {effective_sort}",
            details={"allowed": sorted(SORT_EXPRESSIONS)},
        )

    rows = await tools_repo.list_owned_tools(
        session,
        owner_id=principal.user_id,
        statuses=list(status) if status else None,
        q=(q or "").strip() or None,
        tool_types=list(tool_type) if tool_type else None,
        category_slugs=sorted({c for c in (category or []) if c}) or None,
        sort=effective_sort,
        limit=params.limit,
        offset=params.offset,
    )
    total = await tools_repo.count_owned_tools(
        session,
        owner_id=principal.user_id,
        statuses=list(status) if status else None,
        q=(q or "").strip() or None,
        tool_types=list(tool_type) if tool_type else None,
        category_slugs=sorted({c for c in (category or []) if c}) or None,
    )
    items = [_build_my_item(tool) for tool in rows]
    return MyToolListResponse(
        items=items,
        total=total,
        page=params.page,
        page_size=params.page_size,
        pages=(total + params.page_size - 1) // params.page_size if params.page_size else 0,
    )


def _build_my_item(tool) -> MyToolListItem:
    from app.schemas.taxonomy import CategoryBrief

    return MyToolListItem(
        id=tool.id,
        slug=tool.slug,
        name=tool.name,
        summary=tool.summary,
        tool_type=tool.tool_type,
        visibility=tool.visibility,
        status=tool.status,
        category=(
            CategoryBrief(
                id=tool.category.id,
                slug=tool.category.slug,
                name=tool.category.name,
                icon=tool.category.icon,
            )
            if tool.category is not None
            else None
        ),
        tags=[t.display_name for t in tool.tags],
        # 列表只给缩略图（FR-FILE-10：避免列表页加载原图）
        cover_url=(
            f"/api/v1/images/{tool.cover_image_id}?variant=thumb"
            if tool.cover_image_id
            else None
        ),
        current_version=tool.current_version.version if tool.current_version else None,
        pending_version=None,
        file_size=tool.current_version.file_size if tool.current_version else None,
        download_count=tool.download_count,
        view_count=tool.view_count,
        version_seq=tool.version_seq,
        reject_reason=tool.reject_reason,
        offline_reason=tool.offline_reason,
        published_at=tool.published_at,
        created_at=tool.created_at,
        updated_at=tool.updated_at,
        status_label=STATUS_LABELS.get(tool.status, tool.status),
    )


@router.post("/tools", response_model=ToolDetail, status_code=201, summary="创建工具草稿")
async def create_tool(
    body: ToolCreateRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(write_guard)],
) -> ToolDetail:
    """创建后是 `draft`，不进入审批队列；需要显式 submit。"""
    await _ensure_category(session, body.category_id)
    await _ensure_tag_limit(session, body.tags)

    now = utcnow()
    slug = await allocate_slug(session, body.name)
    tool = Tool(
        slug=slug,
        name=body.name.strip(),
        summary=body.summary.strip(),
        description_md=body.description_md,
        tool_type=body.tool_type.value,
        visibility=body.visibility.value,
        status=ToolStatus.DRAFT.value,
        owner_id=principal.user_id,
        category_id=body.category_id,
        webapp_url=body.webapp_url,
        webapp_health_url=body.webapp_health_url,
        version_seq=1,
        created_at=now,
        updated_at=now,
    )
    session.add(tool)
    await session.flush()
    await _apply_tags(session, tool, body.tags, created_by_id=principal.user_id)
    await _sync_index(session, tool)
    await session.commit()

    return await _reload_detail(session, tool.id, principal)


@router.get("/tools/{tool_id}", response_model=ToolDetail, summary="我的工具详情")
async def get_my_tool(
    tool_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(read_guard)],
) -> ToolDetail:
    """含草稿、被驳回、已下架的详情。

    非 owner 但具备审批权限的人也能看（FR-APPR-06：审批人要能直接预览）。
    其他人一律 404。
    """
    tool = await tools_repo.get_by_id(session, tool_id)
    if tool is None or tool.deleted_at is not None:
        raise NotFoundError(message="资源不存在")
    is_owner = tool.owner_id == principal.user_id
    is_approver = bool(
        principal.roles & {RoleCode.APPROVER.value, RoleCode.SUPERADMIN.value}
    )
    if not (is_owner or is_approver):
        raise NotFoundError(message="资源不存在")
    return await _reload_detail(session, tool.id, principal)


@router.patch("/tools/{tool_id}", response_model=ToolDetail, summary="编辑工具元信息")
async def update_my_tool(
    tool_id: int,
    body: ToolUpdateRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(write_guard)],
) -> ToolDetail:
    """FR-TOOL-11：`pending` 状态下不可编辑 → 409 `TOOL_NOT_EDITABLE`。

    FR-TOOL-13：元信息变更**不触发重新审批**（只有版本内容变更才触发）。
    """
    tool = await _own_tool(session, tool_id, principal, for_write=True)
    if tool.status == ToolStatus.PENDING.value:
        from app.core.errors import ToolNotEditableError

        raise ToolNotEditableError(
            message="待审状态下不可编辑，请先撤回提交",
            details={"status": tool.status},
        )

    if body.category_id is not None:
        await _ensure_category(session, body.category_id)
        tool.category_id = body.category_id
    if body.name is not None:
        tool.name = body.name.strip()
    if body.summary is not None:
        tool.summary = body.summary.strip()
    if body.description_md is not None:
        tool.description_md = body.description_md
    if body.visibility is not None:
        await _apply_visibility(session, tool, body.visibility.value)
    if body.webapp_url is not None:
        tool.webapp_url = body.webapp_url
    if body.webapp_health_url is not None:
        tool.webapp_health_url = body.webapp_health_url
    if body.tags is not None:
        await _ensure_tag_limit(session, body.tags)
        await _apply_tags(session, tool, body.tags, created_by_id=principal.user_id)

    tool.updated_at = utcnow()
    await session.flush()
    # 元信息变更也要刷搜索索引（名称/简介/详情/标签都在索引里）
    await _sync_index(session, tool)
    await session.commit()
    return await _reload_detail(session, tool.id, principal)


@router.delete("/tools/{tool_id}", summary="软删除工具")
async def delete_my_tool(
    tool_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(write_guard)],
) -> dict[str, str]:
    """**软删除**（FR-TOOL-10）：只置 `deleted_at`，存储文件延迟清理。

    软删除后立刻从所有列表消失（门户查询都带 `deleted_at IS NULL`）。
    """
    tool = await _own_tool(session, tool_id, principal, for_write=True)
    now = utcnow()
    tool.deleted_at = now
    tool.deleted_by_id = principal.user_id
    tool.updated_at = now
    # 从搜索索引里摘掉，否则搜索还能命中已删除的工具
    from app.search import get_search_backend

    await get_search_backend().delete(session, tool.id)
    await session.commit()
    return {"status": "ok"}


# ===========================================================================
# 提交 / 撤回
# ===========================================================================
@router.post("/tools/{tool_id}/submit", response_model=SubmitResponse, summary="提交审批")
async def submit_my_tool(
    tool_id: int,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(write_guard)],
) -> SubmitResponse:
    tool = await _own_tool(session, tool_id, principal, for_write=True)
    outcome = await approval_service.submit_tool(
        session,
        tool=tool,
        actor_id=principal.user_id,
        actor_label=principal.display_name,
        request_id=request.state.request_id,
    )
    return SubmitResponse(
        tool_id=tool.id,
        status=outcome.status,
        version_seq=outcome.version_seq,
        auto_approved=outcome.auto_approved,
        auto_approved_rule=outcome.auto_approved_rule,
        approval_record_id=outcome.approval_record_id,
    )


@router.post("/tools/{tool_id}/withdraw", response_model=WithdrawResponse, summary="撤回提交")
async def withdraw_my_tool(
    tool_id: int,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(write_guard)],
) -> WithdrawResponse:
    tool = await _own_tool(session, tool_id, principal, for_write=True)
    outcome = await approval_service.withdraw_tool(
        session,
        tool=tool,
        actor_id=principal.user_id,
        actor_label=principal.display_name,
        request_id=request.state.request_id,
    )
    return WithdrawResponse(**outcome)


# ===========================================================================
# ACL
# ===========================================================================
@router.put("/tools/{tool_id}/acl", response_model=AclResponse, summary="全量替换授权列表")
async def replace_acl(
    tool_id: int,
    body: AclReplaceRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(write_guard)],
) -> AclResponse:
    tool = await _own_tool(session, tool_id, principal, for_write=True)
    return await acl_service.replace_acl(
        session, tool=tool, payload=body, actor_id=principal.user_id
    )


# ===========================================================================
# 版本
# ===========================================================================
@router.get(
    "/tools/{tool_id}/versions",
    response_model=list[VersionSummary],
    summary="我的工具版本列表（含待审与已驳回）",
)
async def list_my_versions(
    tool_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(read_guard)],
) -> list[VersionSummary]:
    tool = await _own_tool(session, tool_id, principal, for_write=False)
    rows = await versions_repo.list_for_tool(session, tool.id, order_desc=True)
    return [_version_summary(row) for row in rows]


@router.post(
    "/tools/{tool_id}/versions",
    response_model=VersionUploadResponse,
    status_code=201,
    summary="上传新版本（multipart，12 步顺序）",
)
async def upload_version(
    tool_id: int,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(write_guard)],
    version: Annotated[str, Form(min_length=1, max_length=64)],
    changelog_md: Annotated[str, Form()] = "",
    auto_submit: Annotated[bool, Form()] = False,
    prompt_content: Annotated[str | None, Form()] = None,
    webapp_url: Annotated[str | None, Form()] = None,
    file: Annotated[UploadFile | None, File()] = None,
) -> VersionUploadResponse:
    """见 `version_service.create_version` 的 12 步顺序说明。"""
    tool = await _own_tool(session, tool_id, principal, for_write=True)

    # 第 1 步：工具状态（pending 时不允许再上传，先撤回）
    if tool.status == ToolStatus.PENDING.value:
        raise StateConflictError(
            message="待审状态下不能上传新版本，请先撤回提交",
            details={"status": tool.status},
        )

    declared_size = getattr(file, "size", None) if file is not None else None
    file_name = file.filename if file is not None else None

    # 第 3~6 步：一切能在落盘前发现的错误都在这里报掉
    await version_service.precheck_upload(
        session,
        tool=tool,
        version=version.strip(),
        declared_size=declared_size,
        file_name=file_name,
        owner_id=principal.user_id,
    )

    # 第 7~8 步在事务之外完成（流式落盘 + 哈希 + skill 解析）
    outcome = await version_service.create_version(
        session,
        tool=tool,
        version=version.strip(),
        changelog_md=changelog_md,
        uploader_id=principal.user_id,
        uploader_label=principal.display_name,
        stream=_stream_upload(file) if file is not None else None,
        file_name=file_name,
        prompt_content=prompt_content,
        webapp_url=webapp_url,
        auto_submit=auto_submit,
        request_id=request.state.request_id,
        skill_limits=skill_service.SkillLimits.from_settings(settings_unzip_limits()),
    )

    record = outcome.version
    skill_info = None
    if tool.tool_type == ToolType.SKILL:
        skill_info = _skill_info(record)
    return VersionUploadResponse(
        id=record.id,
        tool_id=record.tool_id,
        version=record.version,
        status=record.status,
        file_name=record.file_name,
        file_size=record.file_size,
        file_sha256=record.file_sha256,
        prompt_content=record.prompt_content,
        skill=skill_info,
        tool_status=outcome.tool.status,
        created_at=record.created_at,
    )


@router.patch(
    "/tools/{tool_id}/versions/{version}",
    response_model=VersionSummary,
    summary="修改版本变更说明",
)
async def patch_version(
    tool_id: int,
    version: str,
    body: VersionPatchRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(write_guard)],
) -> VersionSummary:
    """FR-VER-11：**只有未过审的版本**可以改变更说明；
    已过审的版本内容不可改，只能发新版本。"""
    tool = await _own_tool(session, tool_id, principal, for_write=True)
    row = await versions_repo.get_by_version(session, tool.id, version)
    if row is None:
        raise NotFoundError(message="版本不存在", details={"version": version})
    if row.status not in (VersionStatus.PENDING.value, VersionStatus.REJECTED.value):
        raise StateConflictError(
            message="已通过审批的版本不可修改，请发布新版本",
            details={"version": row.version, "status": row.status},
        )
    row.changelog_md = body.changelog_md
    row.updated_at = utcnow()
    await session.commit()
    return _version_summary(row)


@router.delete("/tools/{tool_id}/versions/{version}", summary="删除未过审的版本")
async def delete_version(
    tool_id: int,
    version: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(write_guard)],
) -> dict[str, str]:
    """FR-VER-09/10：当前版本不可删；只有 `pending` / `rejected` 可删。"""
    tool = await _own_tool(session, tool_id, principal, for_write=True)
    row = await versions_repo.get_by_version(session, tool.id, version)
    if row is None:
        raise NotFoundError(message="版本不存在", details={"version": version})
    if row.id == tool.current_version_id or row.is_current:
        raise StateConflictError(
            message="当前版本不可删除，要停用请走「下架」流程",
            details={"version": row.version},
        )
    if row.status not in (VersionStatus.PENDING.value, VersionStatus.REJECTED.value):
        raise StateConflictError(
            message="只有未通过审批的版本可以删除",
            details={"version": row.version, "status": row.status},
        )

    storage_path = row.storage_path
    if tool.pending_version_id == row.id:
        tool.pending_version_id = None
        if tool.status == ToolStatus.PENDING.value:
            tool.status = ToolStatus.DRAFT.value
        elif tool.status == ToolStatus.PENDING_UPDATE.value:
            tool.status = ToolStatus.APPROVED.value
        tool.updated_at = utcnow()
    await session.delete(row)
    await session.commit()

    # 文件删除放到事务提交之后（失败只记警告，由孤儿清理兜底）
    if storage_path:
        from app.storage import get_storage

        await get_storage().delete(storage_path)
    return {"status": "ok"}


# ===========================================================================
# 图片
# ===========================================================================
@router.post(
    "/tools/{tool_id}/images",
    response_model=ImageOut,
    status_code=201,
    summary="上传封面或截图",
)
async def upload_image(
    tool_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(write_guard)],
    file: Annotated[UploadFile, File()],
    kind: Annotated[str, Form()] = ImageKind.SCREENSHOT.value,
    alt_text: Annotated[str | None, Form()] = None,
) -> ImageOut:
    """FR-FILE-09：png/jpg/jpeg/webp/gif，单张 ≤ 5 MB，封面 1 张、截图 ≤ 8 张。

    **必须校验真实格式**（Pillow 嗅探）而不是扩展名 —— 否则改名的可执行文件
    就能进存储。同时生成 480px 缩略图（FR-FILE-10）。
    """
    from app.services import settings_service

    if kind not in (ImageKind.COVER.value, ImageKind.SCREENSHOT.value):
        raise ValidationError(
            message="kind 只能是 cover 或 screenshot",
            fields=[{"field": "kind", "message": "只能是 cover 或 screenshot"}],
        )
    tool = await _own_tool(session, tool_id, principal, for_write=True)
    limits = await settings_service.get_upload_limits(session)

    data = await file.read()
    info = image_service.inspect_image(data, declared_name=file.filename or "")

    if kind == ImageKind.COVER.value:
        existing_cover = await images_repo.get_cover(session, tool.id)
        if existing_cover is not None:
            # 换封面：把旧封面降级为截图而不是删掉，避免用户误操作丢图
            existing_cover.kind = ImageKind.SCREENSHOT.value
            await session.flush()
    else:
        shot_count = await images_repo.count_by_kind(
            session, tool.id, ImageKind.SCREENSHOT
        )
        if shot_count >= limits.max_screenshots:
            raise ValidationError(
                message=f"截图不能超过 {limits.max_screenshots} 张",
                details={"limit": limits.max_screenshots, "current": shot_count},
            )

    # 缩略图生成是 CPU 密集的同步代码，扔到线程里跑
    import asyncio

    thumb_bytes = await asyncio.to_thread(image_service.make_thumbnail, data)

    from app.storage import get_storage

    storage = get_storage()
    import uuid as _uuid

    stem = _uuid.uuid4().hex
    original = await storage.write_bytes_atomic(
        data, directory=f"images/{tool.id}", file_name=f"{stem}.{info.ext}"
    )
    thumb = await storage.write_bytes_atomic(
        thumb_bytes,
        directory=f"images/{tool.id}",
        file_name=f"{stem}.thumb.png",
    )

    now = utcnow()
    image = ToolImage(
        tool_id=tool.id,
        kind=kind,
        storage_path=original.storage_path,
        thumb_path=thumb.storage_path,
        file_name=file.filename or f"image.{info.ext}",
        mime_type=info.mime_type,
        file_size=info.size,
        width=info.width,
        height=info.height,
        sha256=original.sha256,
        sort_order=await images_repo.next_sort_order(session, tool.id),
        alt_text=alt_text,
        uploaded_by_id=principal.user_id,
        created_at=now,
    )
    session.add(image)
    await session.flush()
    if kind == ImageKind.COVER.value:
        tool.cover_image_id = image.id
    tool.updated_at = now
    await session.commit()
    return build_image_out(image)


@router.patch(
    "/tools/{tool_id}/images/{image_id}", response_model=ImageOut, summary="更新图片信息"
)
async def patch_image(
    tool_id: int,
    image_id: int,
    body: ImagePatchRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(write_guard)],
) -> ImageOut:
    tool = await _own_tool(session, tool_id, principal, for_write=True)
    image = await _get_tool_image(session, tool.id, image_id)
    if body.sort_order is not None:
        image.sort_order = body.sort_order
    if body.alt_text is not None:
        image.alt_text = body.alt_text
    if body.set_as_cover:
        previous = await images_repo.get_cover(session, tool.id)
        if previous is not None and previous.id != image.id:
            previous.kind = ImageKind.SCREENSHOT.value
        image.kind = ImageKind.COVER.value
        tool.cover_image_id = image.id
    tool.updated_at = utcnow()
    await session.commit()
    return build_image_out(image)


@router.delete("/tools/{tool_id}/images/{image_id}", summary="删除图片")
async def delete_image(
    tool_id: int,
    image_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(write_guard)],
) -> dict[str, str]:
    tool = await _own_tool(session, tool_id, principal, for_write=True)
    image = await _get_tool_image(session, tool.id, image_id)
    paths = [p for p in (image.storage_path, image.thumb_path) if p]
    if tool.cover_image_id == image.id:
        tool.cover_image_id = None
    tool.updated_at = utcnow()
    await images_repo.delete(session, image)
    await session.commit()

    from app.storage import get_storage

    storage = get_storage()
    for path in paths:
        await storage.delete(path)
    return {"status": "ok"}


async def _get_tool_image(session: AsyncSession, tool_id: int, image_id: int) -> ToolImage:
    image = await images_repo.get_by_id(session, image_id)
    # 必须校验归属：否则可以拿 A 工具的路径删 B 工具的图
    if image is None or image.tool_id != tool_id:
        raise NotFoundError(message="资源不存在")
    return image


# ===========================================================================
# 下载历史
# ===========================================================================
@router.get("/downloads", summary="我的下载历史")
async def my_downloads(
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(read_guard)],
    params: Annotated[PageParams, Depends()],
) -> Page[DownloadLogOut]:
    rows = await downloads_repo.list_logs_for_user(
        session, principal.user_id, limit=params.limit, offset=params.offset
    )
    total = await downloads_repo.count_logs_for_user(session, principal.user_id)
    items = [
        DownloadLogOut(
            id=log.id,
            tool_id=log.tool_id,
            tool_slug=slug,
            tool_name=name,
            version_id=log.version_id,
            version=version,
            file_name=None,
            file_size=None,
            via_api_token_id=log.via_api_token_id,
            created_at=log.created_at,
        )
        for log, slug, name, version in rows
    ]
    return Page.build(items, total, params)


# ===========================================================================
# 内部辅助
# ===========================================================================
async def _own_tool(
    session: AsyncSession, tool_id: int, principal: Principal, *, for_write: bool
) -> Tool:
    """取自己的工具。非 owner 一律 404（不泄露存在性）。

    `for_write=True` 时非 owner 也是 404 —— 即使对方有审批权限，
    也不能通过 `/me/*` 改别人的工具（审批走 `/admin/*`）。
    """
    tool = await tools_repo.get_by_id(session, tool_id)
    if tool is None or tool.deleted_at is not None or tool.owner_id != principal.user_id:
        raise NotFoundError(message="资源不存在")
    del for_write
    return tool


async def _reload_detail(
    session: AsyncSession, tool_id: int, principal: Principal
) -> ToolDetail:
    tool = await tools_repo.get_by_id(session, tool_id)
    if tool is None:
        raise NotFoundError(message="资源不存在")
    return await build_detail(
        session,
        tool=tool,
        user_id=principal.user_id,
        roles=principal.roles,
        permissions=principal.permissions,
        visibility_ok=True,
        include_acl=True,
    )


async def _ensure_category(session: AsyncSession, category_id: int | None) -> None:
    if category_id is None:
        return
    from app.repositories import categories as categories_repo

    category = await categories_repo.get_by_id(session, category_id)
    if category is None or not category.is_active:
        raise ValidationError(
            message="分类不存在或已停用",
            fields=[{"field": "category_id", "message": "分类不存在或已停用"}],
        )


async def _ensure_tag_limit(session: AsyncSession, tags: list[str]) -> None:
    from app.services import settings_service

    limits = await settings_service.get_upload_limits(session)
    if len(tags) > limits.max_tags:
        raise ValidationError(
            message=f"标签不能超过 {limits.max_tags} 个",
            fields=[{"field": "tags", "message": f"最多 {limits.max_tags} 个标签"}],
        )


async def _apply_tags(
    session: AsyncSession, tool, tags: list[str], *, created_by_id: int | None
) -> None:
    """全量替换工具标签（标签是随元信息一起提交的）。"""
    from sqlalchemy import delete as sa_delete

    from app.models.tool import ToolTag
    from app.repositories import tags as tags_repo

    await session.execute(sa_delete(ToolTag).where(ToolTag.tool_id == tool.id))
    for name in tags:
        tag = await tags_repo.get_or_create(session, name, created_by_id=created_by_id)
        session.add(ToolTag(tool_id=tool.id, tag_id=tag.id))
        tag.usage_count = (tag.usage_count or 0) + 1
    await session.flush()
    # 重新加载关系，保证后续 build_detail 读到的标签是新的
    await session.refresh(tool, ["tags"])


async def _apply_visibility(session: AsyncSession, tool, visibility: str) -> None:
    """改可见性。

    切到 `restricted` 时必须已有 ACL 条目（否则没人看得到）——
    但注意 **ACL 条目在切到 public/private 时保留不删**（docs/03 §3.11）。
    """
    from app.core.errors import AclRequiredError
    from app.models.enums import ToolVisibility
    from app.repositories import tool_acl as acl_repo

    if visibility == ToolVisibility.RESTRICTED.value:
        existing = await acl_repo.count_for_tool(session, tool.id)
        if existing == 0:
            raise AclRequiredError(
                message="改为 restricted 前请先配置至少一条授权",
                details={"tool_id": tool.id},
            )
    tool.visibility = visibility


async def _sync_index(session: AsyncSession, tool) -> None:
    from app.search import SearchDocument, get_search_backend

    await session.refresh(tool, ["tags"])
    await get_search_backend().upsert(
        session,
        tool.id,
        SearchDocument(
            name=tool.name,
            summary=tool.summary,
            description=tool.description_md,
            tags=[t.display_name for t in tool.tags],
        ),
    )


def _version_summary(row: ToolVersion) -> VersionSummary:
    return VersionSummary(
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
            VersionUploader(id=row.uploader.id, display_name=row.uploader.display_name)
            if row.uploader is not None
            else None
        ),
        approved_at=row.approved_at,
        reject_reason=row.reject_reason,
        purged_at=row.purged_at,
        created_at=row.created_at,
        can_download=row.status
        in (VersionStatus.APPROVED.value, VersionStatus.SUPERSEDED.value)
        and bool(row.storage_path),
    )


def _skill_info(record: ToolVersion):
    from app.schemas.version import SkillTreeSummary, SkillVersionInfo

    tree = record.skill_file_tree or []
    summary = (
        SkillTreeSummary(
            file_count=sum(1 for item in tree if not item.get("is_dir")),
            total_size=sum(int(item.get("size") or 0) for item in tree),
            max_depth=max(
                (len(str(item.get("path", "")).split("/")) for item in tree), default=0
            ),
        )
        if tree
        else None
    )
    return SkillVersionInfo(
        manifest=record.skill_manifest,
        file_tree_summary=summary,
        parse_error=record.skill_parse_error,
    )


def settings_unzip_limits() -> dict[str, int]:
    from app.core.config import settings as env_settings

    return env_settings.unzip_limits


async def _stream_upload(upload: UploadFile) -> AsyncIterator[bytes]:
    """把 `UploadFile` 变成异步分块迭代器（1 MiB/块）。

    这样 `storage.stage()` 可以边收边写边算哈希，**不会把整个文件读进内存**。
    """
    while True:
        chunk = await upload.read(CHUNK_SIZE)
        if not chunk:
            break
        yield chunk


__all__ = ["router"]
