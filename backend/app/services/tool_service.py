"""门户工具列表服务：把仓储查出的 ORM 对象装配成响应形状。"""

from __future__ import annotations

import math

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import RoleCode
from app.models.tool import Tool
from app.repositories import tools as tools_repo
from app.repositories.tools import PortalFilters, VisibilityContext
from app.schemas.auth import UserBrief
from app.schemas.taxonomy import CategoryBrief
from app.schemas.tool import ToolFacets, ToolListItem, ToolListResponse

#: 能看到「有新版本待审」角标的角色（docs/03 §3.3，SRS 待确认 Q3）
PENDING_VISIBLE_ROLES: frozenset[str] = frozenset(
    {RoleCode.APPROVER.value, RoleCode.SUPERADMIN.value}
)


def cover_url_for(tool: Tool) -> str | None:
    """封面图走 `/api/v1/images/{id}` 接口而不是静态路径 ——
    接口内做可见性校验，避免用裸 ID 枚举（docs/02 §1.4）。"""
    if tool.cover_image_id is None:
        return None
    return f"/api/v1/images/{tool.cover_image_id}?variant=thumb"


def build_list_item(
    tool: Tool,
    *,
    viewer_user_id: int | None,
    viewer_roles: frozenset[str] | set[str],
    can_download: bool,
) -> ToolListItem:
    """ORM → 卡片。字段逐条对应 docs/03 §3.3 的响应示例。"""
    sees_pending = viewer_user_id == tool.owner_id or bool(
        set(viewer_roles) & PENDING_VISIBLE_ROLES
    )
    current = tool.current_version
    return ToolListItem(
        id=tool.id,
        slug=tool.slug,
        name=tool.name,
        summary=tool.summary,
        tool_type=tool.tool_type,
        visibility=tool.visibility,
        category=(
            CategoryBrief(
                id=tool.category.id,
                slug=tool.category.slug,
                name=tool.category.name,
                icon=tool.category.icon,
            )
            if tool.category is not None
            else None
        ),
        # 展示用 display_name（保留原始大小写）；服务端对 `?tag=` 入参做同样的
        # 归一化，所以前端拿这个值回填 URL 依然能正确筛选（FR-TAX-03）。
        tags=[t.display_name for t in tool.tags],
        cover_url=cover_url_for(tool),
        owner=UserBrief(
            id=tool.owner.id,
            username=tool.owner.username,
            display_name=tool.owner.display_name,
        ),
        current_version=current.version if current is not None else None,
        file_size=current.file_size if current is not None else None,
        download_count=tool.download_count,
        view_count=tool.view_count,
        # 兜底：owner/approver 之外恒为 false（避免通过该字段探测待审状态）
        has_pending_version=bool(tool.pending_version_id is not None and sees_pending),
        can_download=can_download,
        published_at=tool.published_at,
        updated_at=tool.updated_at,
    )


async def list_portal_tools(
    session: AsyncSession,
    *,
    visibility: VisibilityContext,
    filters: PortalFilters,
    sort: str,
    page: int,
    page_size: int,
    viewer_roles: frozenset[str] | set[str],
    can_download: bool,
) -> ToolListResponse:
    """门户列表 + facets。

    **`facets` 只在 `page == 1` 时返回**，翻页时整个键省略（docs/03 §3.3），
    避免每次翻页都重复算一遍聚合。
    """
    total = await tools_repo.count_portal_tools(session, visibility=visibility, filters=filters)
    rows = await tools_repo.list_portal_tools(
        session,
        visibility=visibility,
        filters=filters,
        sort=sort,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    items = [
        build_list_item(
            tool,
            viewer_user_id=visibility.user_id,
            viewer_roles=viewer_roles,
            can_download=can_download,
        )
        for tool in rows
    ]

    facets: ToolFacets | None = None
    if page == 1:
        facets = ToolFacets(
            categories=await tools_repo.build_category_facets(session, visibility=visibility),
            types=await tools_repo.build_type_facets(session, visibility=visibility),
        )

    pages = math.ceil(total / page_size) if page_size else 0
    return ToolListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
        facets=facets,
    )


__all__ = [
    "PENDING_VISIBLE_ROLES",
    "build_list_item",
    "cover_url_for",
    "list_portal_tools",
]
