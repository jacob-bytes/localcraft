"""管理侧路由（M2 只包含审批与治理，M3 才加用户/组/分类/标签/Token）。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.admin import approvals

admin_router = APIRouter()
admin_router.include_router(approvals.router)

__all__ = ["admin_router"]
