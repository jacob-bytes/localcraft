"""分类与标签管理路由（docs/03 §2.5 的 9 个接口 / FR-TAX-01~07）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.admin._guards import taxonomy_guard
from app.core.deps import Principal
from app.db.session import get_db
from app.schemas.admin import (
    AdminCategoryCreateRequest,
    AdminCategoryOut,
    AdminCategoryUpdateRequest,
    AdminTagListResponse,
    AdminTagOut,
    CategoryDeleteResponse,
    CategoryOrderRequest,
    TagCleanupResponse,
    TagMergeRequest,
    TagMergeResponse,
    TagRenameRequest,
)
from app.services import admin_taxonomy_service

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# 分类
# ---------------------------------------------------------------------------
@router.get(
    "/categories", response_model=list[AdminCategoryOut], summary="分类列表（含禁用）"
)
async def admin_list_categories(
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(taxonomy_guard)],
) -> list[AdminCategoryOut]:
    """管理侧列表**含 `is_active=false` 的分类**，与门户的 `/categories` 不同。"""
    del principal
    return await admin_taxonomy_service.list_categories(session)


@router.post(
    "/categories",
    response_model=AdminCategoryOut,
    status_code=201,
    summary="创建分类",
)
async def create_category(
    body: AdminCategoryCreateRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(taxonomy_guard)],
) -> AdminCategoryOut:
    del principal
    return await admin_taxonomy_service.create_category(session, payload=body)


@router.patch(
    "/categories/{category_id}", response_model=AdminCategoryOut, summary="编辑分类"
)
async def update_category(
    category_id: int,
    body: AdminCategoryUpdateRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(taxonomy_guard)],
) -> AdminCategoryOut:
    del principal
    return await admin_taxonomy_service.update_category(
        session, category_id=category_id, payload=body
    )


@router.delete(
    "/categories/{category_id}",
    response_model=CategoryDeleteResponse,
    summary="删除分类（软删除）",
)
async def delete_category(
    category_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(taxonomy_guard)],
) -> dict:
    """FR-TAX-02：有工具引用 → 409 CATEGORY_IN_USE + 引用数；否则软删除。

    **禁止物理删除** —— `tools.category_id` 有外键，硬删会让工具悬空。
    """
    del principal
    return await admin_taxonomy_service.delete_category(session, category_id=category_id)


@router.put(
    "/categories/order",
    response_model=list[AdminCategoryOut],
    summary="批量调整分类排序",
)
async def reorder_categories(
    body: CategoryOrderRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(taxonomy_guard)],
) -> list[AdminCategoryOut]:
    """一次事务写完（拖拽排序会一次提交整个列表）。"""
    del principal
    return await admin_taxonomy_service.reorder_categories(session, items=body.items)


# ---------------------------------------------------------------------------
# 标签
# ---------------------------------------------------------------------------
@router.get("/tags", response_model=AdminTagListResponse, summary="标签列表")
async def admin_list_tags(
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(taxonomy_guard)],
    q: Annotated[str | None, Query(description="前缀搜索")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    del principal
    items, total = await admin_taxonomy_service.list_tags(
        session, q=q, limit=page_size, offset=(page - 1) * page_size
    )
    return {
        "items": [i.model_dump(mode="json") for i in items],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if page_size else 0,
    }


@router.patch("/tags/{tag_id}", response_model=AdminTagOut, summary="重命名标签")
async def rename_tag(
    tag_id: int,
    body: TagRenameRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(taxonomy_guard)],
) -> AdminTagOut:
    del principal
    return await admin_taxonomy_service.rename_tag(
        session, tag_id=tag_id, display_name=body.display_name
    )


@router.post("/tags/merge", response_model=TagMergeResponse, summary="合并标签")
async def merge_tags(
    body: TagMergeRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(taxonomy_guard)],
) -> TagMergeResponse:
    """FR-TAX-05：引用关系转移到目标标签，返回**转移数量**。"""
    del principal
    return await admin_taxonomy_service.merge_tags(session, payload=body)


@router.post("/tags/cleanup", response_model=TagCleanupResponse, summary="清理零引用标签")
async def cleanup_tags(
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(taxonomy_guard)],
) -> TagCleanupResponse:
    """FR-TAX-06。先重算 `usage_count` 再删，双重确认避免误删。"""
    del principal
    return await admin_taxonomy_service.cleanup_tags(session)


__all__ = ["router"]
