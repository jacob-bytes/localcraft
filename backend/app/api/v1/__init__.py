"""`/api/v1` 路由聚合。

M1 的 12 个接口 + M2 新增的 38 个 = **50**（契约 §6 + §6.1）。
M3 的 `/admin/users`、`/admin/groups`、`/admin/categories`、`/admin/tags`、
`/admin/tokens`、`/admin/import|export`、`/admin/stats` 等**不在此挂载**，
守卫测试会断言它们的缺席。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth, directory, images, me, system, taxonomy, tools
from app.api.v1.admin import admin_router

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(auth.router)
api_router.include_router(directory.router)
api_router.include_router(taxonomy.router)
api_router.include_router(tools.router)
api_router.include_router(images.router)
api_router.include_router(me.router)
api_router.include_router(admin_router)

__all__ = ["api_router"]
