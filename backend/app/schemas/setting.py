"""系统设置读写 Schema（docs/03 §3.13）。

`options` / `min` / `max` 由服务端下发，前端据此渲染控件，
**避免前端硬编码设置项元信息**（docs/03 §3.13 的明确要求）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import SettingValueType
from app.schemas.common import OptionalUTCDateTime


class SettingUpdatedBy(BaseModel):
    id: int
    display_name: str


class SettingItem(BaseModel):
    key: str
    value: Any
    value_type: SettingValueType
    is_public: bool
    description: str | None = None
    #: 枚举型设置的合法取值（下拉框用）
    options: list[str] | None = None
    #: 数值型设置的上下界（数字输入用）
    minimum: int | None = Field(default=None, serialization_alias="min")
    maximum: int | None = Field(default=None, serialization_alias="max")
    updated_at: OptionalUTCDateTime = None
    updated_by: SettingUpdatedBy | None = None


class SettingUpdateItem(BaseModel):
    key: str = Field(max_length=64)
    value: Any


class SettingUpdateRequest(BaseModel):
    """批量**部分**更新（docs/03 §3.13）。"""

    items: list[SettingUpdateItem] = Field(min_length=1, max_length=100)


class SettingWarning(BaseModel):
    code: str
    message: str
    pending_count: int | None = None


class SettingListResponse(BaseModel):
    items: list[SettingItem]
    #: 切换 `approval.mode` 时附带的提示（FR-APPR-02）
    warnings: list[SettingWarning] = Field(default_factory=list)
