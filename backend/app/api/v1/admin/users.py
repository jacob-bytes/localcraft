"""用户与角色管理路由（docs/03 §2.5 的 8 个接口）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.admin._guards import roles_guard, users_guard
from app.core.deps import Principal
from app.core.pagination import PageParams
from app.db.session import get_db
from app.schemas.admin import (
    AdminRoleReplaceRequest,
    AdminUserCreateRequest,
    AdminUserCreateResponse,
    AdminUserItem,
    AdminUserListResponse,
    AdminUserUpdateRequest,
    ResetPasswordRequest,
    ResetPasswordResponse,
    RoleOut,
)
from app.services import admin_user_service

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=AdminUserListResponse, summary="用户列表")
async def list_users(
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(users_guard)],
    params: Annotated[PageParams, Depends()],
    q: Annotated[str | None, Query(description="按用户名/显示名模糊搜索")] = None,
    role: Annotated[list[str] | None, Query(description="角色筛选，可重复")] = None,
    status: Annotated[str | None, Query()] = None,
) -> AdminUserListResponse:
    """FR-IAM-04：模糊搜索 + 角色筛选 + 状态筛选 + 分页。"""
    del principal
    items, total = await admin_user_service.list_users(
        session,
        q=q,
        roles=list(role) if role else None,
        status=status,
        limit=params.limit,
        offset=params.offset,
    )
    return AdminUserListResponse(
        items=items,
        total=total,
        page=params.page,
        page_size=params.page_size,
        pages=(total + params.page_size - 1) // params.page_size if params.page_size else 0,
    )


@router.post(
    "/users", response_model=AdminUserCreateResponse, status_code=201, summary="创建用户"
)
async def create_user(
    body: AdminUserCreateRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(users_guard)],
) -> AdminUserCreateResponse:
    """FR-IAM-01。密码留空时生成随机密码并**只回显一次**。

    注意：`generated_password` 绝不出现在日志里。
    """
    created = await admin_user_service.create_user(
        session, payload=body, actor_id=principal.user_id
    )
    return AdminUserCreateResponse(
        user=await admin_user_service.get_user_item(session, created.user.id),
        generated_password=created.generated_password,
    )


@router.get("/users/{user_id}", response_model=AdminUserItem, summary="用户详情")
async def get_user(
    user_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(users_guard)],
) -> AdminUserItem:
    del principal
    return await admin_user_service.get_user_item(session, user_id)


@router.patch("/users/{user_id}", response_model=AdminUserItem, summary="编辑用户")
async def update_user(
    user_id: int,
    body: AdminUserUpdateRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(users_guard)],
) -> AdminUserItem:
    """FR-IAM-02。禁用会连带吊销该用户的全部 refresh token 与 API Token。"""
    return await admin_user_service.update_user(
        session, user_id=user_id, payload=body, actor_id=principal.user_id
    )


@router.post(
    "/users/{user_id}/reset-password",
    response_model=ResetPasswordResponse,
    summary="重置密码",
)
async def reset_password(
    user_id: int,
    body: ResetPasswordRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(users_guard)],
) -> ResetPasswordResponse:
    """FR-IAM-03：重置后强制改密，并吊销该用户全部会话。"""
    user, generated, revoked = await admin_user_service.reset_password(
        session, user_id=user_id, password=body.password, actor_id=principal.user_id
    )
    return ResetPasswordResponse(
        user_id=user.id,
        must_change_password=True,
        revoked_sessions=revoked,
        generated_password=generated,
    )


@router.put("/users/{user_id}/roles", response_model=AdminUserItem, summary="全量替换角色")
async def replace_roles(
    user_id: int,
    body: AdminRoleReplaceRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(users_guard)],
) -> AdminUserItem:
    """FR-IAM-02。降级最后一个超管 → 409 LAST_SUPERADMIN；降级自己 → 被拒。"""
    return await admin_user_service.replace_roles(
        session,
        user_id=user_id,
        roles=[r.value for r in body.roles],
        actor_id=principal.user_id,
    )


@router.post(
    "/users/{user_id}/revoke-sessions", summary="强制下线（吊销全部会话）"
)
async def revoke_sessions(
    user_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(users_guard)],
) -> dict:
    """FR-AUTH-12。"""
    revoked = await admin_user_service.revoke_sessions(
        session, user_id=user_id, actor_id=principal.user_id
    )
    return {"status": "ok", "revoked_sessions": revoked}


@router.get("/roles", response_model=list[RoleOut], summary="角色列表")
async def list_roles(
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(roles_guard)],
) -> list[RoleOut]:
    del principal
    return await admin_user_service.list_roles(session)


__all__ = ["router"]
