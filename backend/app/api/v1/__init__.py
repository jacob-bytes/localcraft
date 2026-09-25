"""`/api/v1` 路由聚合。

**M1 只挂契约 §6 冻结的 12 个接口**，其余（工具详情、写接口、管理台）
属于 M2/M3，此处不预埋。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth, system, taxonomy, tools

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(auth.router)
api_router.include_router(taxonomy.router)
api_router.include_router(tools.router)

__all__ = ["api_router"]
