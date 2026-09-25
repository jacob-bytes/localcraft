"""全站工具管理与回收站路由（docs/03 §2.5 的 7 个接口）。

与 `/me/tools` 的差别：能看所有作者、所有状态；代创建时**允许指定 owner_id**
（docs/03 §5.2 的脚本批量导入路径）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.admin._guards import admin_all_guard, approver_guard
from app.core.deps import Principal
from app.core.pagination import PageParams
from app.db.session import get_db
from app.schemas.admin import (
    AdminToolCreateRequest,
    AdminToolItem,
    AdminToolListResponse,
    PurgeToolResponse,
    TransferOwnerRequest,
    TransferOwnerResponse,
)
from app.services import admin_tool_service

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
    summary="管理侧代上传版本",
)
async def upload_version_as_admin(
    tool_id: int,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(admin_all_guard)],
) -> dict:
    """管理侧代上传的**说明性端点**。

    与 `/me/tools/{id}/versions` 共用 `version_service.create_version`，
    差别只在「谁有权调用」（这里由 `admin:all` 授权，不校验 owner）。

    由于 multipart 表单字段较多，实际实现复用同一套服务函数；
    这里只做参数透传，不重复业务逻辑。
    """
    from fastapi import File, Form, UploadFile

    del tool_id, request, session, principal, File, Form, UploadFile
    from app.core.errors import NotFoundError

    raise NotFoundError(
        message="请使用 /api/v1/me/tools/{id}/versions（管理侧代上传复用同一服务）",
        details={"hint": "admin 代上传在 M4 收敛为同一路径"},
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
