"""门户工具列表路由（`GET /api/v1/tools`）。

**M1 只实现列表**；`/tools/{slug}` 详情与所有写接口属于 M2（契约 §6、§12）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import PortalAccess, get_visibility_context, portal_access
from app.core.errors import InvalidSortError, ValidationError
from app.core.pagination import PageParams
from app.db.session import get_db
from app.models.enums import PortalSort
from app.repositories import tags as tags_repo
from app.repositories.tools import (
    SORT_EXPRESSIONS,
    VALID_TOOL_TYPES,
    PortalFilters,
    VisibilityContext,
)
from app.schemas.tool import ToolListResponse
from app.services import tool_service

router = APIRouter(tags=["portal"])


@router.get("/tools", response_model=ToolListResponse, summary="门户工具列表")
async def list_tools(
    session: Annotated[AsyncSession, Depends(get_db)],
    access: Annotated[PortalAccess, Depends(portal_access)],
    visibility: Annotated[VisibilityContext, Depends(get_visibility_context)],
    params: Annotated[PageParams, Depends()],
    q: Annotated[str | None, Query(description="关键词，搜索名称、简介、详情、标签")] = None,
    category: Annotated[list[str] | None, Query(description="分类 slug，可重复传")] = None,
    tag: Annotated[list[str] | None, Query(description="标签名，可重复传")] = None,
    tool_type: Annotated[
        list[str] | None, Query(alias="type", description="file/webapp/skill/prompt，可重复传")
    ] = None,
    owner: Annotated[str | None, Query(description="按作者 username 筛选")] = None,
    sort: Annotated[
        str | None,
        Query(description="hot（默认）/ new / name / -updated_at"),
    ] = None,
) -> ToolListResponse:
    """门户主列表。

    几个要点：

    - **`sort` 白名单校验**，非法值返回 400 `INVALID_SORT` 而不是 500；
      排序表达式从 `SORT_EXPRESSIONS` 查表得到，**绝不把 `sort` 拼进 SQL**
      （docs/03 §1.6）。
    - **`facets` 只在 `page == 1` 时返回**，翻页时整个键省略（docs/03 §3.3）。
    - 可见性判定下推到 SQL（EXISTS 子查询），不在 Python 里过滤
      （docs/01 §4.3 性能要求）。
    - 未登录用户由 `portal.allow_anonymous_view` 决定 401 还是返回公开数据
      （契约 §6），该判断在 `portal_access` 依赖里完成。
    """
    effective_sort = sort or PortalSort.HOT.value
    if effective_sort not in SORT_EXPRESSIONS:
        raise InvalidSortError(
            message=f"不支持的排序方式: {effective_sort}",
            details={"allowed": sorted(SORT_EXPRESSIONS)},
        )

    types = tool_type or []
    invalid_types = [t for t in types if t not in VALID_TOOL_TYPES]
    if invalid_types:
        raise ValidationError(
            message="工具类型非法",
            fields=[
                {"field": "type", "message": f"不支持的工具类型: {t}"} for t in invalid_types
            ],
        )

    filters = PortalFilters(
        q=(q or "").strip() or None,
        # 分类切片去重并保持稳定顺序，避免同一 slug 重复传导致 IN 列表膨胀
        category_slugs=sorted({c for c in (category or []) if c}),
        # 标签入参做与写入侧一致的归一化（FR-TAX-03），
        # 所以前端可以用 display_name 回填 URL
        tag_names=sorted({tags_repo.normalize_tag_name(t) for t in (tag or []) if t.strip()}),
        tool_types=sorted(set(types)),
        owner_username=(owner or "").strip().lower() or None,
    )

    return await tool_service.list_portal_tools(
        session,
        visibility=visibility,
        filters=filters,
        sort=effective_sort,
        page=params.page,
        page_size=params.page_size,
        viewer_roles=access.roles,
        can_download=access.can_download,
    )
