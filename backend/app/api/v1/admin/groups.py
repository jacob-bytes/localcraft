"""用户组管理路由（docs/03 §2.5 的 7 个接口 / FR-GRP-01~06）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.admin._guards import groups_guard
from app.core.deps import Principal
from app.core.pagination import PageParams
from app.db.session import get_db
from app.schemas.admin import (
    GroupCreateRequest,
    GroupDeleteResponse,
    GroupListResponse,
    GroupMemberAddRequest,
    GroupMemberAddResponse,
    GroupMemberListResponse,
    GroupOut,
    GroupUpdateRequest,
    OkResponse,
)
from app.services import group_service

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/groups", response_model=GroupListResponse, summary="用户组列表")
async def list_groups(
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(groups_guard)],
    params: Annotated[PageParams, Depends()],
    q: Annotated[str | None, Query()] = None,
) -> dict:
    del principal
    items, total = await group_service.list_groups(
        session, q=q, limit=params.limit, offset=params.offset
    )
    return {
        "items": [i.model_dump(mode="json") for i in items],
        "total": total,
        "page": params.page,
        "page_size": params.page_size,
        "pages": (total + params.page_size - 1) // params.page_size if params.page_size else 0,
    }


@router.post("/groups", response_model=GroupOut, status_code=201, summary="创建用户组")
async def create_group(
    body: GroupCreateRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(groups_guard)],
) -> GroupOut:
    return await group_service.create_group(
        session, payload=body, actor_id=principal.user_id
    )


@router.patch("/groups/{group_id}", response_model=GroupOut, summary="编辑用户组")
async def update_group(
    group_id: int,
    body: GroupUpdateRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(groups_guard)],
) -> GroupOut:
    del principal
    return await group_service.update_group(session, group_id=group_id, payload=body)


@router.delete("/groups/{group_id}", response_model=GroupDeleteResponse, summary="删除用户组")
async def delete_group(
    group_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(groups_guard)],
    force: Annotated[
        bool, Query(description="已确认影响面，连带清理引用它的 ACL 条目")
    ] = False,
) -> dict:
    """FR-GRP-04：被 ACL 引用时返回 **409 GROUP_IN_USE** 并列出引用它的工具。

    管理员看到影响面后带 `?force=true` 再调一次，才会真正删除并清理悬空 ACL。
    """
    del principal
    if force:
        return await group_service.force_delete_group(session, group_id=group_id)
    return await group_service.delete_group(session, group_id=group_id)


@router.get(
    "/groups/{group_id}/members",
    response_model=GroupMemberListResponse,
    summary="成员列表",
)
async def list_members(
    group_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(groups_guard)],
    params: Annotated[PageParams, Depends()],
    q: Annotated[str | None, Query()] = None,
) -> dict:
    del principal
    items, total = await group_service.list_members(
        session, group_id=group_id, q=q, limit=params.limit, offset=params.offset
    )
    return {
        "items": [i.model_dump(mode="json") for i in items],
        "total": total,
        "page": params.page,
        "page_size": params.page_size,
        "pages": (total + params.page_size - 1) // params.page_size if params.page_size else 0,
    }


@router.post(
    "/groups/{group_id}/members",
    response_model=GroupMemberAddResponse,
    summary="批量添加成员",
)
async def add_members(
    group_id: int,
    body: GroupMemberAddRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(groups_guard)],
) -> GroupMemberAddResponse:
    """一次事务写入全部成员（不逐条提交 —— 易错点 2）。"""
    return await group_service.add_members(
        session, group_id=group_id, user_ids=body.user_ids, actor_id=principal.user_id
    )


@router.delete(
    "/groups/{group_id}/members/{user_id}", response_model=OkResponse, summary="移除成员"
)
async def remove_member(
    group_id: int,
    user_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(groups_guard)],
) -> dict:
    """移除后可见性立即生效（FR-GRP-06：每次请求实时判定，不做 token 内嵌缓存）。"""
    del principal
    await group_service.remove_member(session, group_id=group_id, user_id=user_id)
    return {"status": "ok"}


__all__ = ["router"]
