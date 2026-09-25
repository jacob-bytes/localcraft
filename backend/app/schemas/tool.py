"""门户工具列表 Schema（docs/03 §3.3）。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.models.enums import ToolType, ToolVisibility
from app.schemas.auth import UserBrief
from app.schemas.common import OptionalUTCDateTime
from app.schemas.taxonomy import CategoryBrief, CategoryFacet, TypeFacet


class ToolListItem(BaseModel):
    """门户卡片。字段与 docs/03 §3.3 的响应示例逐字段对应。

    `has_pending_version` 仅当请求者是 owner 或 approver/superadmin 时为 `true`，
    对其他用户恒为 `false`（docs/03 §3.3「`has_pending_version` 的可见性」，SRS Q3）。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str
    summary: str
    tool_type: ToolType
    visibility: ToolVisibility
    category: CategoryBrief | None = None
    tags: list[str] = []
    cover_url: str | None = None
    owner: UserBrief
    current_version: str | None = None
    file_size: int | None = None
    download_count: int
    view_count: int
    has_pending_version: bool = False
    can_download: bool = True
    published_at: OptionalUTCDateTime = None
    updated_at: OptionalUTCDateTime = None


class ToolFacets(BaseModel):
    """分类与类型的计数**已按当前用户可见性过滤**（FR-TAX-07）。

    **只在 `page == 1` 时返回**，翻页时整个 `facets` 键省略（docs/03 §3.3）。
    """

    categories: list[CategoryFacet] = []
    types: list[TypeFacet] = []


class ToolListResponse(BaseModel):
    items: list[ToolListItem]
    total: int
    page: int
    page_size: int
    pages: int
    facets: ToolFacets | None = None


#: `sort` 白名单 → 实际 ORDER BY 表达式（在 repository 里映射，绝不拼接用户输入）
SORT_WHITELIST: frozenset[str] = frozenset({"hot", "new", "name", "-updated_at"})
