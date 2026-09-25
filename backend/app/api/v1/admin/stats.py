"""统计与概览路由（docs/03 §2.5 的 3 个接口）。

**纯数字，不做图表**（FR-ADMIN-14 / docs/01 第 10 章第 2 条明确不做可视化大屏）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.admin._guards import admin_all_guard, approver_guard
from app.core.deps import Principal
from app.db.session import get_db
from app.schemas.admin import (
    AdminOverviewResponse,
    StorageStatsResponse,
    ToolRankResponse,
)
from app.services import stats_service

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get(
    "/overview", response_model=AdminOverviewResponse, summary="管理概览数字"
)
async def overview(
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(approver_guard)],
) -> AdminOverviewResponse:
    """待审数、工具数（按状态分布）、用户数、存储用量、近 7 日下载量。"""
    del principal
    return await stats_service.overview(session)


@router.get(
    "/stats/tools", response_model=ToolRankResponse, summary="工具排行（下载量 Top N）"
)
async def tool_ranking(
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(approver_guard)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    include_deleted: Annotated[bool, Query()] = False,
) -> ToolRankResponse:
    del principal
    return await stats_service.tool_ranking(
        session, limit=limit, include_deleted=include_deleted
    )


@router.get(
    "/stats/storage", response_model=StorageStatsResponse, summary="存储占用明细"
)
async def storage_stats(
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(admin_all_guard)],
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> StorageStatsResponse:
    del principal
    return await stats_service.storage_stats(session, limit=limit, offset=offset)


__all__ = ["router"]
