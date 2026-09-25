"""API Token 管理路由（docs/03 §2.5 的 4 个接口 / FR-API-01~05）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.admin._guards import settings_guard
from app.core.deps import Principal
from app.core.pagination import PageParams
from app.db.session import get_db
from app.schemas.admin import (
    ApiTokenCreateRequest,
    ApiTokenCreateResponse,
    ApiTokenOut,
)
from app.services import token_service

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/tokens", summary="API Token 列表")
async def list_tokens(
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(settings_guard)],
    params: Annotated[PageParams, Depends()],
    include_revoked: Annotated[bool, Query()] = True,
) -> dict:
    """**永远不返回明文** —— 只有前缀（FR-ADMIN-10）。"""
    del principal
    items, total = await token_service.list_tokens(
        session,
        include_revoked=include_revoked,
        limit=params.limit,
        offset=params.offset,
    )
    return {
        "items": [i.model_dump(mode="json") for i in items],
        "total": total,
        "page": params.page,
        "page_size": params.page_size,
        "pages": (total + params.page_size - 1) // params.page_size if params.page_size else 0,
    }


@router.post(
    "/tokens",
    response_model=ApiTokenCreateResponse,
    status_code=201,
    summary="签发 API Token",
)
async def create_token(
    body: ApiTokenCreateRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(settings_guard)],
) -> ApiTokenCreateResponse:
    """明文只在此响应中出现一次（FR-ADMIN-10）。

    Scope 必须是创建者权限的子集，否则 403（docs/03 §3.12）。
    """
    return await token_service.create_token(
        session, payload=body, creator=principal.user
    )


@router.post("/tokens/{token_id}/revoke", response_model=ApiTokenOut, summary="吊销 Token")
async def revoke_token(
    token_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(settings_guard)],
) -> ApiTokenOut:
    """FR-API-04：吊销后立即失效。"""
    return await token_service.revoke_token(
        session, token_id=token_id, actor_id=principal.user_id
    )


@router.delete("/tokens/{token_id}", summary="删除 Token 记录")
async def delete_token(
    token_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(settings_guard)],
) -> dict:
    """物理删除记录（保留审计痕迹请用吊销）。"""
    del principal
    await token_service.delete_token(session, token_id=token_id)
    return {"status": "ok"}


__all__ = ["router"]
