"""分页参数与泛型分页包装（契约 §4.2 / docs/03 §1.5）。

放在 `core/` 而不是 `schemas/`，因为它是**跨接口的横切约定**，
docs/03 §6.1 的目录组织也是这样划分的。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Annotated, Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel

#: 默认每页条数。门户首页前端固定传 24（契约 §4.2），后端默认 20。
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 200

T = TypeVar("T")


class PageParams:
    """分页查询参数依赖。

    - `page` ≥ 1
    - `page_size` 1 ~ 200

    超出范围的取值由 FastAPI 的 Query 校验拦成 400 `VALIDATION_ERROR`
    （`details.fields` 指向具体参数）。

    **超过最大页数返回空 `items` 而不是 404**（docs/03 §1.5），
    所以这里不校验「页码上限」—— 页码再大也只是 offset 越过结果集。
    """

    def __init__(
        self,
        page: Annotated[int, Query(ge=1, description="页码，从 1 开始")] = 1,
        page_size: Annotated[
            int, Query(ge=1, le=MAX_PAGE_SIZE, description=f"每页条数，1~{MAX_PAGE_SIZE}")
        ] = DEFAULT_PAGE_SIZE,
    ) -> None:
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"PageParams(page={self.page}, page_size={self.page_size})"


class Page(BaseModel, Generic[T]):
    """分页响应包装（契约 §4.2）。

    `pages` 由服务端算好返回，避免前端重复算。
    """

    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int

    @classmethod
    def build(cls, items: Sequence[T], total: int, params: PageParams) -> Page[T]:
        pages = math.ceil(total / params.page_size) if params.page_size else 0
        return cls(
            items=list(items),
            total=total,
            page=params.page,
            page_size=params.page_size,
            pages=pages,
        )
