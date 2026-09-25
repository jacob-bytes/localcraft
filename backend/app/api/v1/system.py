"""系统与元信息路由（`/api/v1/meta`）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.meta import MetaResponse
from app.services import settings_service

router = APIRouter(tags=["system"])


@router.get("/meta", response_model=MetaResponse, summary="公开配置")
async def get_meta(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> MetaResponse:
    """无需鉴权。

    **只返回 `is_public = true` 的设置项**（FR-CFG-03）—— 配额、审批开关、
    白名单等私有项绝不通过这个匿名接口外泄。
    """
    return await settings_service.get_meta(session)
