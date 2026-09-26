"""通用 Schema：时间类型与错误信封。

分页参数与包装在 `app/core/pagination.py`（docs/03 §6.1 的目录组织），
此处不再重复定义。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, PlainSerializer

from app.core.timeutil import ensure_utc, to_iso_z

# ---------------------------------------------------------------------------
# 时间序列化 —— 契约 §5：ISO 8601 UTC 带 `Z`
# ---------------------------------------------------------------------------
# 显式用 PlainSerializer 而不是依赖 pydantic 默认行为：SQLite 读回来的
# datetime 是 naive 的，默认序列化会输出不带 Z 的字符串，前端会按本地时区解析。
#
# ★ M10 补上 `AfterValidator(ensure_utc)`：**校验期就把值规整成 UTC**。
# 为什么需要：`PlainSerializer` 只统一了 **JSON 文本**（`to_iso_z` 内部会
# astimezone(utc)），但 Python 侧的对象仍然原样保留来源偏移。实测
# `ApiTokenOut.revoked_at`：
#
#   - 刚写入、走内存对象返回 → `tzinfo=timezone.utc`（`utcnow()` 的产物）
#   - 从库里回读返回          → `tzinfo=ZoneInfo('Asia/Shanghai')`
#     （PostgreSQL 按**会话时区**返回 aware datetime）
#
# 同一个瞬时、同一张表、同一个字段，两种偏移。瞬时值没错，但
# 「同一接口对同一实体给出不同表示」会让任何按字符串/相等性比较的消费者出错
# （M10 之前就有一条测试因此误判，见 docs/09 §16.8）。
# 加上这一层后，任何进出 schema 的时间都保证是 **UTC-aware**：
# naive 的被标记为 UTC（与 `to_iso_z` 一贯的口径一致），aware 的被换算到 UTC。
UTCDateTime = Annotated[
    datetime,
    AfterValidator(ensure_utc),
    PlainSerializer(lambda v: to_iso_z(v), return_type=str, when_used="json"),
]
OptionalUTCDateTime = Annotated[
    datetime | None,
    AfterValidator(ensure_utc),
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
