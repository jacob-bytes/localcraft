"""分类与标签 Schema。"""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import ORMModel


class CategoryOut(ORMModel):
    """`GET /api/v1/categories` 列表项。

    `tool_count` 是**当前用户可见的**工具数量（FR-TAX-07）——
    与门户 `facets.categories[].count` 同源同算法，保证左侧导航的计数
    和实际能看到的卡片数一致。
    """

    id: int
    slug: str
    name: str
    description: str | None = None
    icon: str | None = None
    sort_order: int
    is_active: bool
    tool_count: int = 0


class TagOut(ORMModel):
    """`GET /api/v1/tags` 列表项。

    `name` 是归一化后的键（小写、全角转半角），`display_name` 是首次创建时的
    原始写法（FR-TAX-03）。前端联想框展示 `display_name`，提交筛选时用任一个
    都可以 —— 服务端对 `?tag=` 入参会做同样的归一化。
    """

    id: int
    name: str
    display_name: str
    usage_count: int


class CategoryBrief(BaseModel):
    """工具卡片里的分类（docs/03 §3.3 `category`）。"""

    id: int
    slug: str
    name: str
    icon: str | None = None


class CategoryFacet(BaseModel):
    """`facets.categories[]`（docs/03 §3.3）。"""

    slug: str
    name: str
    count: int


class TypeFacet(BaseModel):
    """`facets.types[]`（docs/03 §3.3）。"""

    value: str
    count: int
