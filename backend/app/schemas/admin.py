"""M3 管理面 Schema：用户 / 角色 / 用户组 / API Token / 统计 / 导入导出。

docs/03 §2.5 是 M3 的接口边界；响应形状按 contracts §15.6 的新规则
以 `backend/openapi.json` 为权威来源（docs/03 负责语义与错误码）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.enums import (
    ApiScope,
    ImageKind,
    RoleCode,
    ToolStatus,
    ToolType,
    ToolVisibility,
    UserStatus,
)
from app.schemas.common import OptionalUTCDateTime


# ===========================================================================
# 用户与角色
# ===========================================================================
class RoleOut(BaseModel):
    id: int
    code: RoleCode
    name: str
    description: str | None = None
    is_builtin: bool = True
    #: 该角色的权限点（由代码里的 frozenset 计算，不是从库里读 —— 防止改库提权）
    permissions: list[str] = Field(default_factory=list)
    user_count: int = 0


class AdminUserItem(BaseModel):
    id: int
    username: str
    display_name: str
    email: str | None = None
    roles: list[str] = Field(default_factory=list)
    status: UserStatus
    auth_source: str
    must_change_password: bool = False
    failed_login_count: int = 0
    locked_until: OptionalUTCDateTime = None
    last_login_at: OptionalUTCDateTime = None
    last_login_ip: str | None = None
    created_at: OptionalUTCDateTime = None
    updated_at: OptionalUTCDateTime = None
    #: 用量（列表页也要展示，避免前端逐行再查一次）
    tool_count: int = 0
    used_bytes: int = 0
    #: 签发中的 API Token 数（禁用用户时要提示会连带失效）
    active_token_count: int = 0


class AdminUserListResponse(BaseModel):
    items: list[AdminUserItem]
    total: int
    page: int
    page_size: int
    pages: int


class AdminUserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    email: str | None = Field(default=None, max_length=255)
    password: str | None = Field(
        default=None,
        max_length=256,
        description="留空则服务端生成随机密码，并在响应的 generated_password 里回显一次",
    )
    roles: list[RoleCode] = Field(default_factory=lambda: [RoleCode.USER])
    must_change_password: bool = True

    @field_validator("username")
    @classmethod
    def _username_charset(cls, v: str) -> str:
        import re

        v = v.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", v):
            raise ValueError("用户名只能包含字母、数字、下划线、点、连字符")
        return v


class AdminUserCreateResponse(BaseModel):
    user: AdminUserItem
    #: **明文密码只出现这一次**（与 API Token 同理）。请求里留空时才有值。
    generated_password: str | None = None


class AdminUserUpdateRequest(BaseModel):
    """PATCH —— 只更新传入的字段。`username` **不可改**（FR-IAM-01）。"""

    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    email: str | None = Field(default=None, max_length=255)
    status: UserStatus | None = None


class AdminRoleReplaceRequest(BaseModel):
    """`PUT /admin/users/{id}/roles` —— 全量替换。"""

    roles: list[RoleCode] = Field(min_length=1, max_length=4)


class ResetPasswordRequest(BaseModel):
    password: str | None = Field(default=None, max_length=256)


class ResetPasswordResponse(BaseModel):
    user_id: int
    must_change_password: bool = True
    revoked_sessions: int = 0
    generated_password: str | None = None


# ===========================================================================
# 用户组
# ===========================================================================
class GroupOut(BaseModel):
    id: int
    name: str
    description: str | None = None
    is_active: bool = True
    member_count: int = 0
    #: 被多少个工具的 ACL 引用（删除前要提示，FR-GRP-04）
    acl_reference_count: int = 0
    created_at: OptionalUTCDateTime = None
    updated_at: OptionalUTCDateTime = None


class GroupCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=500)
    is_active: bool = True


class GroupUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None


class GroupMemberOut(BaseModel):
    user_id: int
    username: str
    display_name: str
    email: str | None = None
    status: UserStatus
    added_at: OptionalUTCDateTime = None
    added_by_name: str | None = None


class GroupMemberAddRequest(BaseModel):
    """支持批量（FR-GRP-02）。"""

    user_ids: list[int] = Field(min_length=1, max_length=200)


class GroupMemberAddResponse(BaseModel):
    added: int
    already_members: int
    not_found: list[int] = Field(default_factory=list)


class GroupInUseDetail(BaseModel):
    """`409 GROUP_IN_USE` 的 details —— 必须列出引用它的工具。"""

    group_id: int
    group_name: str
    tool_count: int
    tools: list[dict] = Field(default_factory=list)


# ===========================================================================
# API Token
# ===========================================================================
class ApiTokenCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    scopes: list[ApiScope] = Field(min_length=1)
    #: 省略 → 默认 90 天（docs/05 §13.8）；显式传 null → 永不过期
    expires_at: OptionalUTCDateTime = None
    #: 备注（可选，便于批量管理）
    note: str | None = Field(default=None, max_length=255)

    @field_validator("scopes")
    @classmethod
    def _dedupe(cls, v: list[ApiScope]) -> list[ApiScope]:
        seen: list[ApiScope] = []
        for item in v:
            if item not in seen:
                seen.append(item)
        return seen


class ApiTokenOut(BaseModel):
    id: int
    name: str
    note: str | None = None
    token_prefix: str
    scopes: list[str] = Field(default_factory=list)
    created_by_id: int | None = None
    created_by_name: str | None = None
    expires_at: OptionalUTCDateTime = None
    last_used_at: OptionalUTCDateTime = None
    last_used_ip: str | None = None
    revoked_at: OptionalUTCDateTime = None
    created_at: OptionalUTCDateTime = None
    #: 是否仍然可用（未吊销、未过期）
    is_active: bool = True


class ApiTokenCreateResponse(ApiTokenOut):
    """**明文 Token 只在此响应中出现一次**（FR-ADMIN-10）。"""

    token: str
    warning: str = "请立即复制保存。此 Token 不会再次显示。"


# ===========================================================================
# 分类 / 标签（管理侧）
# ===========================================================================
class AdminCategoryOut(BaseModel):
    id: int
    slug: str
    name: str
    description: str | None = None
    icon: str | None = None
    sort_order: int = 0
    is_active: bool = True
    tool_count: int = 0
    created_at: OptionalUTCDateTime = None
    updated_at: OptionalUTCDateTime = None


class AdminCategoryCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    slug: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=500)
    icon: str | None = Field(default=None, max_length=64)
    sort_order: int = 0
    is_active: bool = True


class AdminCategoryUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    slug: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=500)
    icon: str | None = Field(default=None, max_length=64)
    sort_order: int | None = None
    is_active: bool | None = None


class CategoryOrderItem(BaseModel):
    id: int
    sort_order: int


class CategoryOrderRequest(BaseModel):
    """`PUT /admin/categories/order` —— 批量提交新顺序。"""

    items: list[CategoryOrderItem] = Field(min_length=1, max_length=200)


class AdminTagOut(BaseModel):
    id: int
    name: str
    display_name: str
    usage_count: int = 0
    created_at: OptionalUTCDateTime = None


class TagRenameRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=64)


class TagMergeRequest(BaseModel):
    """把 `source_ids` 合并进 `target_id`。"""

    source_ids: list[int] = Field(min_length=1, max_length=100)
    target_id: int


class TagMergeResponse(BaseModel):
    target_id: int
    target_name: str
    merged_tags: int = 0
    #: 实际转移的引用条数（去掉与目标标签重复的关联）
    moved_references: int = 0
    deduplicated_references: int = 0
    deleted_tags: list[str] = Field(default_factory=list)


class TagCleanupResponse(BaseModel):
    deleted: int = 0
    tags: list[str] = Field(default_factory=list)


# ===========================================================================
# 全站工具 / 回收站
# ===========================================================================
class AdminToolItem(BaseModel):
    id: int
    slug: str
    name: str
    summary: str
    tool_type: ToolType
    visibility: ToolVisibility
    status: ToolStatus
    category: dict | None = None
    tags: list[str] = Field(default_factory=list)
    cover_url: str | None = None
    owner: dict | None = None
    current_version: str | None = None
    download_count: int = 0
    view_count: int = 0
    version_seq: int = 0
    reject_reason: str | None = None
    offline_reason: str | None = None
    deleted_at: OptionalUTCDateTime = None
    published_at: OptionalUTCDateTime = None
    created_at: OptionalUTCDateTime = None
    updated_at: OptionalUTCDateTime = None


class AdminToolListResponse(BaseModel):
    items: list[AdminToolItem]
    total: int
    page: int
    page_size: int
    pages: int


class AdminToolCreateRequest(BaseModel):
    """`POST /admin/tools` —— 管理侧代创建，**允许指定 owner_id**（docs/03 §5.2）。"""

    name: str = Field(min_length=1, max_length=128)
    summary: str = Field(min_length=1, max_length=500)
    description_md: str = Field(default="", max_length=100_000)
    tool_type: ToolType
    owner_id: int
    category_id: int | None = None
    tags: list[str] = Field(default_factory=list, max_length=8)
    visibility: ToolVisibility = ToolVisibility.PUBLIC
    webapp_url: str | None = Field(default=None, max_length=1024)
    #: 直接发布（跳过审批）—— 批量导入场景用
    publish: bool = False

    @field_validator("webapp_url")
    @classmethod
    def _check_url(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("URL 必须以 http:// 或 https:// 开头")
        return v

    @model_validator(mode="after")
    def _webapp_requires_url(self) -> AdminToolCreateRequest:
        # 与 `/me/tools` 的 ToolCreateRequest 同一条规则（FR-TOOL-05）。
        # 少了这一层，webapp 不填 URL 会一路走到 INSERT，撞
        # `ck_tools_webapp_url` 检查约束，向调用方暴露成 500（M3 自查时发现）。
        if self.tool_type == ToolType.WEBAPP and not self.webapp_url:
            raise ValueError("webapp 类型必须填写 URL")
        return self


class TransferOwnerRequest(BaseModel):
    new_owner_id: int
    reason: str | None = Field(default=None, max_length=2000)


class TransferOwnerResponse(BaseModel):
    tool_id: int
    previous_owner: dict | None = None
    new_owner: dict | None = None
    #: 转移后的配额占用（重算过的值）
    previous_owner_used_bytes: int = 0
    new_owner_used_bytes: int = 0
    approval_record_id: int | None = None


class PurgeToolResponse(BaseModel):
    tool_id: int
    purged_files: int = 0
    purged_versions: int = 0


# ===========================================================================
# 统计
# ===========================================================================
class StatusCount(BaseModel):
    status: str
    count: int


class AdminOverviewResponse(BaseModel):
    """`GET /admin/overview` —— **纯数字，不做图表**（FR-ADMIN-14 / 第 10 章）。"""

    user_count: int = 0
    active_user_count: int = 0
    disabled_user_count: int = 0
    tool_count: int = 0
    tools_by_status: list[StatusCount] = Field(default_factory=list)
    pending_count: int = 0
    category_count: int = 0
    tag_count: int = 0
    group_count: int = 0
    active_token_count: int = 0
    download_count: int = 0
    view_count: int = 0
    used_bytes: int = 0
    quota_bytes: int = 0
    used_percent: float = 0.0
    #: 存储水位是否超过 `quota.warn_threshold_pct`（契约 §18.6）。
    #: 判断口径在 `settings_service.storage_warning()` 里只写一份，
    #: `scripts/disk-alert.sh` 复用同一语义。
    storage_warning: bool = False
    #: 当前生效的告警阈值（百分比），便于前端显示「85%」而不是硬编码
    storage_warning_threshold_pct: int = 85
    downloads_last_7_days: int = 0
    recycle_bin_count: int = 0


class ToolRankItem(BaseModel):
    tool_id: int
    slug: str
    name: str
    tool_type: ToolType
    status: ToolStatus
    owner_name: str | None = None
    download_count: int = 0
    view_count: int = 0


class ToolRankResponse(BaseModel):
    items: list[ToolRankItem]
    total: int = 0


class StorageOwnerItem(BaseModel):
    owner_id: int
    username: str
    display_name: str
    tool_count: int = 0
    version_count: int = 0
    used_bytes: int = 0
    quota_bytes: int = 0
    used_percent: float = 0.0


class StorageStatsResponse(BaseModel):
    total_used_bytes: int = 0
    total_quota_bytes: int = 0
    used_percent: float = 0.0
    file_count: int = 0
    orphan_file_count: int = 0
    items: list[StorageOwnerItem] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20


# ===========================================================================
# 批量导入导出
# ===========================================================================
class ImportErrorItem(BaseModel):
    """`errors[]` —— **必须精确到行与字段**（FR-API-09 / 易错点 15）。"""

    row: int
    field: str | None = None
    value: Any = None
    message: str


class GeneratedPassword(BaseModel):
    username: str
    password: str


class ImportResultResponse(BaseModel):
    dry_run: bool = False
    on_conflict: str = "skip"
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    #: **明文密码的唯二出现处**（另一个是单个创建用户）。绝不写日志。
    generated_passwords: list[GeneratedPassword] = Field(default_factory=list)
    errors: list[ImportErrorItem] = Field(default_factory=list)


class ToolImportItem(BaseModel):
    slug: str | None = Field(default=None, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    summary: str = Field(default="", max_length=500)
    description_md: str = Field(default="", max_length=100_000)
    tool_type: ToolType
    owner_username: str | None = None
    category_slug: str | None = None
    tags: list[str] = Field(default_factory=list)
    visibility: ToolVisibility = ToolVisibility.PUBLIC
    webapp_url: str | None = None
    publish: bool = True


class ToolImportRequest(BaseModel):
    items: list[ToolImportItem] = Field(min_length=1, max_length=500)
    dry_run: bool = False
    on_conflict: str = "skip"


__all__ = [
    "AdminCategoryCreateRequest",
    "AdminCategoryOut",
    "AdminCategoryUpdateRequest",
    "AdminOverviewResponse",
    "AdminRoleReplaceRequest",
    "AdminTagOut",
    "AdminToolCreateRequest",
    "AdminToolItem",
    "AdminToolListResponse",
    "AdminUserCreateRequest",
    "AdminUserCreateResponse",
    "AdminUserItem",
    "AdminUserListResponse",
    "AdminUserUpdateRequest",
    "ApiTokenCreateRequest",
    "ApiTokenCreateResponse",
    "ApiTokenOut",
    "CategoryOrderItem",
    "CategoryOrderRequest",
    "GeneratedPassword",
    "GroupCreateRequest",
    "GroupInUseDetail",
    "GroupMemberAddRequest",
    "GroupMemberAddResponse",
    "GroupMemberOut",
    "GroupOut",
    "GroupUpdateRequest",
    "ImageKind",
    "ImportErrorItem",
    "ImportResultResponse",
    "PurgeToolResponse",
    "ResetPasswordRequest",
    "ResetPasswordResponse",
    "RoleOut",
    "StatusCount",
    "StorageOwnerItem",
    "StorageStatsResponse",
    "TagCleanupResponse",
    "TagMergeRequest",
    "TagMergeResponse",
    "TagRenameRequest",
    "ToolImportItem",
    "ToolImportRequest",
    "ToolRankItem",
    "ToolRankResponse",
    "TransferOwnerRequest",
    "TransferOwnerResponse",
]

# ===========================================================================
# J-8（contracts/CONTRACT.md §19.7）：补齐此前无字段定义的响应模型
#
# 这 9 个端点原先返回裸 `dict`，在 openapi 里只能是 `additionalProperties: true`。
# 而 §15.6 宣布「openapi 是响应形状的唯一权威」—— 没有字段定义时这句话对它们
# 名不副实：前端只能从 types.ts 反推形状，check:api-types 也无从校验。
#
# 全部按既有惯例（列表 = items/total/page/page_size/pages）与
# docs/03 §2.5 的语义定义，不改变任何响应内容，只补类型。
# ===========================================================================


class GroupListResponse(BaseModel):
    items: list[GroupOut]
    total: int
    page: int
    page_size: int
    pages: int


class GroupMemberListResponse(BaseModel):
    items: list[GroupMemberOut]
    total: int
    page: int
    page_size: int
    pages: int


class GroupDeleteResponse(BaseModel):
    """删除用户组的响应（FR-GRP-04）。

    `affected_tools` 列出被清理掉 ACL 条目的工具 id；带 `slug` 的详细影响面
    在 `409 GROUP_IN_USE` 的 `details.tools` 里（J-7）。
    """

    status: str
    cleaned_acl_entries: int
    affected_tools: list[int] = Field(default_factory=list)


class OkResponse(BaseModel):
    """只有 `{"status": "ok"}` 的通用响应。"""

    status: str


class CategoryDeleteResponse(BaseModel):
    """删除分类 —— **软删除**（FR-TAX-02）。"""

    status: str
    soft_deleted: bool


class AdminTagListResponse(BaseModel):
    items: list[AdminTagOut]
    total: int
    page: int
    page_size: int
    pages: int


class ApiTokenListResponse(BaseModel):
    items: list[ApiTokenOut]
    total: int
    page: int
    page_size: int
    pages: int


class RevokeSessionsResponse(BaseModel):
    """强制下线（FR-AUTH-12）。

    J-3 之后本接口**同时吊销该用户的 API Token** —— 字段名保持不变以免破坏
    前端契约，语义在以下 docstring 里说明。
    """

    status: str
    revoked_sessions: int

