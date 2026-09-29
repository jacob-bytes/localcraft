"""全站工具管理与回收站路由（docs/03 §2.5 的 7 个接口）。

与 `/me/tools` 的差别：能看所有作者、所有状态；代创建时**允许指定 owner_id**
（docs/03 §5.2 的脚本批量导入路径）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.admin._guards import admin_all_guard, approver_guard
from app.core.deps import Principal
from app.core.errors import NotFoundError, StateConflictError
from app.core.pagination import PageParams
from app.core.rate_limit import rate_limit_upload
from app.db.session import get_db
from app.models.enums import ToolStatus
from app.repositories import tools as tools_repo
from app.schemas.admin import (
    AdminToolCreateRequest,
    AdminToolItem,
    AdminToolListResponse,
    PurgeToolResponse,
    TransferOwnerRequest,
    TransferOwnerResponse,
)
from app.schemas.version import VersionUploadResponse
from app.services import admin_tool_service, skill_service, version_service
from app.services.upload_stream import settings_unzip_limits, stream_upload

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/tools", response_model=AdminToolListResponse, summary="全站工具列表")
async def list_all_tools(
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(approver_guard)],
    params: Annotated[PageParams, Depends()],
    q: Annotated[str | None, Query()] = None,
    status: Annotated[list[str] | None, Query()] = None,
    tool_type: Annotated[list[str] | None, Query(alias="type")] = None,
    category: Annotated[list[str] | None, Query()] = None,
    visibility: Annotated[list[str] | None, Query()] = None,
    owner: Annotated[str | None, Query(description="按作者 username")] = None,
    include_deleted: Annotated[bool, Query()] = False,
    date_from: Annotated[datetime | None, Query()] = None,
    date_to: Annotated[datetime | None, Query()] = None,
) -> AdminToolListResponse:
    """FR-ADMIN-12：含所有状态、所有作者；支持状态/类型/分类/作者/可见性/时间范围筛选。"""
    del principal
    return await admin_tool_service.list_all_tools(
        session,
        q=q,
        statuses=list(status) if status else None,
        tool_types=list(tool_type) if tool_type else None,
        category_slugs=sorted({c for c in (category or []) if c}) or None,
        visibilities=list(visibility) if visibility else None,
        owner_username=owner,
        include_deleted=include_deleted,
        date_from=date_from,
        date_to=date_to,
        limit=params.limit,
        offset=params.offset,
    )


@router.post(
    "/tools", response_model=AdminToolItem, status_code=201, summary="管理侧代创建工具"
)
async def create_tool_as_admin(
    body: AdminToolCreateRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(admin_all_guard)],
) -> AdminToolItem:
    """docs/03 §5.2：脚本批量导入用，**允许指定 owner_id**、可绕过 owner 校验。"""
    return await admin_tool_service.create_tool_as_admin(
        session, payload=body, actor_id=principal.user_id
    )


@router.post(
    "/tools/{tool_id}/versions",
    response_model=VersionUploadResponse,
    status_code=201,
    summary="管理侧代上传版本",
    # M14（契约 §29.4）：上传档配额（与 /me 的上传共用同一档）。
    dependencies=[Depends(rate_limit_upload)],
)
async def upload_version_as_admin(
    tool_id: int,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(admin_all_guard)],
    version: Annotated[str, Form(min_length=1, max_length=64)],
    changelog_md: Annotated[str, Form()] = "",
    auto_submit: Annotated[bool, Form()] = False,
    prompt_content: Annotated[str | None, Form()] = None,
    webapp_url: Annotated[str | None, Form()] = None,
    file: Annotated[UploadFile | None, File()] = None,
) -> VersionUploadResponse:
    """管理侧代上传版本（`docs/03` §5.2 的脚本化接口面）。

    与 `/me/tools/{id}/versions` 的差别**只有一处**：这里由 `admin:all` 授权、
    不校验 owner。其余校验顺序与落盘全部走同一个 `version_service.create_version`，
    不复制业务逻辑（契约 §18.4，也见「易错点清单」第 6 条）。

    契约 §18.4 的背景：这个路由原先恒定返回 404，却仍在冻结的 92 个操作里、
    并被 `docs/03` §5.2 的示例脚本引用 —— 「看起来存在但必然报错」比没有更糟。
    """
    # 管理侧不校验 owner，只确认工具存在且未删除
    tool = await tools_repo.get_by_id(session, tool_id)
    if tool is None or tool.deleted_at is not None:
        raise NotFoundError(message="工具不存在", details={"tool_id": tool_id})
    if tool.status == ToolStatus.PENDING.value:
        raise StateConflictError(
            message="待审状态下不能上传新版本，请先撤回提交",
            details={"status": tool.status},
        )

    declared_size = getattr(file, "size", None) if file is not None else None
    file_name = file.filename if file is not None else None

    await version_service.precheck_upload(
        session,
        tool=tool,
        version=version.strip(),
        declared_size=declared_size,
        file_name=file_name,
        # 代上传：配额记在**工具负责人**头上，与 /me 路径口径一致
        owner_id=tool.owner_id,
    )

    outcome = await version_service.create_version(
        session,
        tool=tool,
        version=version.strip(),
        changelog_md=changelog_md,
        uploader_id=principal.user_id,
        uploader_label=principal.display_name,
        stream=stream_upload(file) if file is not None else None,
        file_name=file_name,
        prompt_content=prompt_content,
        webapp_url=webapp_url,
        auto_submit=auto_submit,
        request_id=request.state.request_id,
        skill_limits=skill_service.SkillLimits.from_settings(settings_unzip_limits()),
    )

    record = outcome.version
    return VersionUploadResponse(
        id=record.id,
        tool_id=record.tool_id,
        version=record.version,
        status=record.status,
        file_name=record.file_name,
        file_size=record.file_size,
        file_sha256=record.file_sha256,
        prompt_content=record.prompt_content,
        # 管理侧代上传不返回 skill 预览详情（与 /me 路径的差异仅在响应丰度，
        # 不影响落盘结果；需要预览可再调 /me/tools/{id}/versions/{v}/skill-preview）
        skill=None,
        tool_status=outcome.tool.status,
        created_at=record.created_at,
        # M8：去重命中与 /me 路径同一份数据（contracts §23.2）。
        duplicate_of=outcome.duplicate_of,
    )


@router.post(
    "/tools/{tool_id}/transfer",
    response_model=TransferOwnerResponse,
    summary="转移负责人",
)
async def transfer_owner(
    tool_id: int,
    body: TransferOwnerRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(admin_all_guard)],
) -> TransferOwnerResponse:
    """FR-IAM-11：重算双方配额 + 写 `transfer_owner` 审批留痕。"""
    return await admin_tool_service.transfer_owner(
        session,
        tool_id=tool_id,
        payload=body,
        actor_id=principal.user_id,
        actor_label=principal.display_name,
    )


@router.post(
    "/tools/{tool_id}/restore", response_model=AdminToolItem, summary="从回收站还原"
)
async def restore_tool(
    tool_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(admin_all_guard)],
) -> AdminToolItem:
    del principal
    return await admin_tool_service.restore_tool(session, tool_id=tool_id)


@router.delete(
    "/tools/{tool_id}/purge", response_model=PurgeToolResponse, summary="彻底清除工具"
)
async def purge_tool(
    tool_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(admin_all_guard)],
) -> PurgeToolResponse:
    """**先删文件再删行** —— 顺序反了会留下无人引用的孤儿文件。"""
    del principal
    return await admin_tool_service.purge_tool(session, tool_id=tool_id)


@router.get(
    "/recycle-bin", response_model=AdminToolListResponse, summary="回收站列表"
)
async def list_recycle_bin(
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(admin_all_guard)],
    params: Annotated[PageParams, Depends()],
) -> AdminToolListResponse:
    del principal
    return await admin_tool_service.list_recycle_bin(
        session, limit=params.limit, offset=params.offset
    )


__all__ = ["router"]
