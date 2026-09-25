"""通用 Schema：时间类型与错误信封。

分页参数与包装在 `app/core/pagination.py`（docs/03 §6.1 的目录组织），
此处不再重复定义。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

from app.core.timeutil import to_iso_z

# ---------------------------------------------------------------------------
# 时间序列化 —— 契约 §5：ISO 8601 UTC 带 `Z`
# ---------------------------------------------------------------------------
# 显式用 PlainSerializer 而不是依赖 pydantic 默认行为：SQLite 读回来的
# datetime 是 naive 的，默认序列化会输出不带 Z 的字符串，前端会按本地时区解析。
UTCDateTime = Annotated[
    datetime,
    PlainSerializer(lambda v: to_iso_z(v), return_type=str, when_used="json"),
]
OptionalUTCDateTime = Annotated[
    datetime | None,
    PlainSerializer(
        lambda v: to_iso_z(v) if v is not None else None,
        return_type=str | None,
        when_used="json",
    ),
]


class ORMModel(BaseModel):
    """可从 ORM 对象构造的响应模型基类。"""

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# 错误信封
# ---------------------------------------------------------------------------
class FieldError(BaseModel):
    """`details.fields[]` 的单条（docs/03 §4 的标准格式）。"""

    field: str
    message: str
    value_length: int | None = None


class ErrorResponse(BaseModel):
    """所有非 2xx 统一形状（契约 §4.3）。这是文档模型，实际由异常处理器构造。"""

    code: str
    message: str
    details: dict[str, Any] | None = None
    request_id: str = Field(description="与响应头 X-Request-Id 一致")
