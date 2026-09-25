"""门户分类与标签路由。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import PortalAccess, get_visibility_context, portal_access
from app.db.session import get_db
from app.repositories.tools import VisibilityContext
from app.schemas.taxonomy import CategoryOut, TagOut
from app.services import taxonomy_service

router = APIRouter(tags=["portal"])


@router.get(
    "/categories",
    response_model=list[CategoryOut],
    summary="分类列表（含当前用户可见工具数）",
)
async def list_categories(
    session: Annotated[AsyncSession, Depends(get_db)],
    # M5：`get_visibility_context` 不再传递依赖 `portal_access`（图片签名路径需要匿名），
    # 所以「匿名能不能看门户」这条判定必须在每个门户路由上**显式**声明。
    access: Annotated[PortalAccess, Depends(portal_access)],
    visibility: Annotated[VisibilityContext, Depends(get_visibility_context)],
) -> list[CategoryOut]:
    """FR-TAX-07：每个分类带上**当前用户可见的**工具数量。

    `access` 只用于强制匿名可见性策略（`portal.allow_anonymous_view`），
    路由体不需要它 —— 但**必须声明**，否则守卫测试会认为这条路由没有鉴权。

    计数与 `GET /tools` 的 `facets.categories[].count` 共用同一段聚合 SQL，
    保证左侧导航的计数与实际能看到的卡片数一致。

    只返回 `is_active = true` 的分类（契约 §6）。
    """
    return await taxonomy_service.list_categories(session, visibility=visibility)


@router.get(
    "/tags",
    response_model=list[TagOut],
    summary="标签列表（支持 ?q= 前缀搜索）",
)
async def list_tags(
    session: Annotated[AsyncSession, Depends(get_db)],
    access: Annotated[PortalAccess, Depends(portal_access)],
    q: Annotated[str | None, Query(description="前缀搜索关键词，用于输入联想")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[TagOut]:
    """标签列表按 `usage_count` 降序，方便联想框把常用标签排在前面。

    `q` 是**前缀**匹配（docs/03 §2.3），不是模糊包含。
    """
    del access  # 仅用于触发鉴权/匿名判定
    return await taxonomy_service.list_tags(session, q=q, limit=limit)
