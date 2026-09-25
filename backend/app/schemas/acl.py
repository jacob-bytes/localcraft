"""可见性授权（ACL）Schema（docs/03 §3.11）。"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.models.enums import AclSubjectType, ToolVisibility


class AclEntryIn(BaseModel):
    subject_type: AclSubjectType
    subject_id: int
    can_download: bool = True


class AclReplaceRequest(BaseModel):
    """`PUT /me/tools/{id}/acl` —— **全量替换**语义（FR-ACL-03）。"""

    visibility: ToolVisibility
    entries: list[AclEntryIn] = Field(default_factory=list)

    @field_validator("entries")
    @classmethod
    def _max_entries(cls, v: list[AclEntryIn]) -> list[AclEntryIn]:
        # 百人规模 + 少量用户组，100 条足够；防止有人提交超大列表打满内存
        if len(v) > 100:
            raise ValueError("授权条目不能超过 100 条")
        return v


class AclEntryOut(BaseModel):
    """回显用的授权条目（带主体名称，避免前端再查一次）。"""

    id: int | None = None
    subject_type: AclSubjectType
    subject_id: int
    subject_name: str | None = None
    can_download: bool = True


class AclResponse(BaseModel):
    """docs/03 §3.11：「返回工具详情的 permissions 与 acl 部分」。"""

    tool_id: int
    visibility: ToolVisibility
    entries: list[AclEntryOut] = Field(default_factory=list)
    permissions: dict[str, bool] = Field(default_factory=dict)
    effective: bool = Field(
        default=True,
        description="ACL 是否正在生效（visibility==restricted 时才有意义）",
    )


# ---------------------------------------------------------------------------
# 工具详情里的 permissions 对象（docs/03 §3.4）
# ---------------------------------------------------------------------------
class ToolPermissions(BaseModel):
    """服务端算好的权限布尔值。

    docs/03 §3.4 明确说这是刻意设计：「前端不需要自己实现一遍权限逻辑，
    直接读服务端给的布尔值」。服务端仍是唯一权威。
    """

    can_edit: bool = False
    can_download: bool = False
    can_manage_versions: bool = False
    can_view_acl: bool = False
    can_delete: bool = False
    can_submit: bool = False
    can_approve: bool = False
