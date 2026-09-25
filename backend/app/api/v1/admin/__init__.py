"""管理侧路由聚合。

M2：审批与治理（12 个接口）
M3：用户/角色、用户组、分类标签、全站工具与回收站、API Token、统计、导入导出（42 个）

M4 不再新增接口（验收清单里的 M4 是「打磨与交付」）。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.admin import (
    approvals,
    groups,
    import_export,
    stats,
    taxonomy,
    tokens,
    tools,
    users,
)

admin_router = APIRouter()
admin_router.include_router(approvals.router)
admin_router.include_router(users.router)
admin_router.include_router(groups.router)
admin_router.include_router(taxonomy.router)
admin_router.include_router(tools.router)
admin_router.include_router(tokens.router)
admin_router.include_router(stats.router)
admin_router.include_router(import_export.router)

__all__ = ["admin_router"]
