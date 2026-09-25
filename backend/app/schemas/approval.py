"""审批相关 Schema（docs/03 §3.8 ~ §3.10 + 白名单/历史）。"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.models.enums import ApprovalAction, ToolType, ToolVisibility
from app.schemas.common import OptionalUTCDateTime
from app.schemas.taxonomy import CategoryBrief


class PendingVersionBrief(BaseModel):
    id: int
    version: str
    changelog_md: str = ""
    file_name: str | None = None
    file_size: int | None = None
    file_sha256: str | None = None


class CurrentVersionBrief(BaseModel):
    id: int
    version: str


class ApprovalQueueItem(BaseModel):
    """docs/03 §3.8 的队列元素。"""

    tool_id: int
    tool_slug: str
    tool_name: str
    tool_type: ToolType
    summary: str
    visibility: ToolVisibility
    category: CategoryBrief | None = None
    tags: list[str] = Field(default_factory=list)
    #: `new_tool` = 首次提交；`new_version` = 已发布工具的新版本
    submission_type: str
    status: str
    pending_version: PendingVersionBrief | None = None
    current_version: CurrentVersionBrief | None = None
    owner: dict | None = None
    submitted_at: OptionalUTCDateTime = None
    #: 服务端算好的等待小时数（前端据此高亮积压超过 24 小时的条目）
    waiting_hours: float = 0.0


class ApproveRequest(BaseModel):
    version_id: int | None = None
    note: str | None = Field(default=None, max_length=2000)
    #: 乐观锁：传 `tools.version_seq`，不匹配返回 409 ALREADY_PROCESSED
    expected_version_seq: int | None = None
    #: 批量批准时前端可能带上的幂等标记（当前未使用，保留字段位置）
    force: bool = False


class RejectRequest(BaseModel):
    """docs/03 §3.10：`reason` 必填，长度 5~2000（FR-APPR-08 ≥5 字）。"""

    reason: str = Field(min_length=5, max_length=2000)
    version_id: int | None = None

    @field_validator("reason")
    @classmethod
    def _strip_and_check(cls, v: str) -> str:
        stripped = v.strip()
        if len(stripped) < 5:
            raise ValueError("驳回理由不能少于 5 个字")
        return stripped


class OfflineRequest(BaseModel):
    """下架（FR-APPR-10：理由必填）。"""

    reason: str = Field(min_length=5, max_length=2000)

    @field_validator("reason")
    @classmethod
    def _strip(cls, v: str) -> str:
        stripped = v.strip()
        if len(stripped) < 5:
            raise ValueError("下架理由不能少于 5 个字")
        return stripped


class RelistRequest(BaseModel):
    """重新上架（FR-APPR-11：理由选填）。"""

    reason: str | None = Field(default=None, max_length=2000)


class BatchApproveRequest(BaseModel):
    """批量批准（FR-APPR-09）。不提供批量驳回 —— 驳回需逐条理由。"""

    tool_ids: list[int] = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=2000)


class BatchApproveItemResult(BaseModel):
    tool_id: int
    ok: bool
    status: str | None = None
    error_code: str | None = None
    message: str | None = None


class BatchApproveResponse(BaseModel):
    succeeded: int
    failed: int
    results: list[BatchApproveItemResult] = Field(default_factory=list)


class SupersededVersionBrief(BaseModel):
    id: int
    version: str


class ApproveResponse(BaseModel):
    """docs/03 §3.9 的响应。"""

    tool_id: int
    status: str
    version_seq: int
    current_version: CurrentVersionBrief | None = None
    superseded_version: SupersededVersionBrief | None = None
    #: 因超出 history_limit 被归档的版本，前端据此提示「版本 x.y.z 已归档」
    purged_versions: list[SupersededVersionBrief] = Field(default_factory=list)
    approval_record_id: int | None = None


class RejectResponse(BaseModel):
    """docs/03 §3.10 的响应。

    注意 `status` 通常是 `approved` —— 驳回「新版本」时工具本身仍是已发布状态。
    """

    tool_id: int
    status: str
    pending_version: PendingVersionBrief | None = None
    rejected_version: SupersededVersionBrief | None = None
    approval_record_id: int | None = None


class OfflineResponse(BaseModel):
    tool_id: int
    status: str
    approval_record_id: int | None = None


class RelistResponse(OfflineResponse):
    pass


# ---------------------------------------------------------------------------
# 审批历史（FR-APPR-12）
# ---------------------------------------------------------------------------
class ApprovalRecordOut(BaseModel):
    id: int
    tool_id: int
    tool_name: str | None = None
    tool_slug: str | None = None
    version_id: int | None = None
    version: str | None = None
    action: ApprovalAction
    from_status: str | None = None
    to_status: str
    version_from_status: str | None = None
    version_to_status: str | None = None
    actor_id: int | None = None
    #: 展示名**快照** —— 用户改名后历史仍准确（docs/02 §3.13）
    actor_label: str
    is_automatic: bool = False
    auto_rule: str | None = None
    reason: str | None = None
    note: str | None = None
    created_at: OptionalUTCDateTime = None


# ---------------------------------------------------------------------------
# 免审白名单（FR-APPR-03/04）
# ---------------------------------------------------------------------------
class WhitelistEntryIn(BaseModel):
    user_id: int
    reason: str | None = Field(default=None, max_length=255)
    expires_at: OptionalUTCDateTime = None


class WhitelistEntryOut(BaseModel):
    user_id: int
    username: str | None = None
    display_name: str | None = None
    reason: str | None = None
    added_by_id: int | None = None
    added_by_name: str | None = None
    expires_at: OptionalUTCDateTime = None
    created_at: OptionalUTCDateTime = None
    is_effective: bool = True
