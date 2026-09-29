"""门户工具列表 Schema（docs/03 §3.3）。

M2 追加了详情、创建/编辑请求与「我的工具」列表项，放在本文件末尾。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import ToolStatus, ToolType, ToolVisibility
from app.schemas.acl import AclEntryOut, ToolPermissions
from app.schemas.auth import UserBrief
from app.schemas.common import OptionalUTCDateTime
from app.schemas.me import ImageOut
from app.schemas.taxonomy import CategoryBrief, CategoryFacet, TypeFacet
from app.schemas.version import SkillTreeSummary, VersionDetail, VersionUploader


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
    #: ---- M8（contracts §23.5）----
    #: 两个计数照常对匿名返回；两个 bool 对匿名**恒为 false**。
    favorite_count: int = 0
    like_count: int = 0
    is_favorited: bool = False
    is_liked: bool = False
    published_at: OptionalUTCDateTime = None
    updated_at: OptionalUTCDateTime = None
    #: ---- M14（contracts §29.3）----
    #: 在线工具探活的**派生**字段：仅当 `tool_type == 'webapp'` 且
    #: `webapp_health_status in ('fail','timeout')` 时为 `true`。
    #: **从未检测过（NULL）不算不健康** —— 否则刚打开探活开关时会满屏告警，
    #: 而那是「还没测」而不是「坏了」。列表只给这个布尔、不给两个原始字段：
    #: 列表页 24 条不需要背两个完整字段，卡片只需要知道要不要打标记。
    webapp_unhealthy: bool = False


class ToolFacets(BaseModel):
    """分类与类型的计数**已按当前用户可见性过滤**（FR-TAX-07）。

    **只在 `page == 1` 时有值**；**键始终存在**，翻页时是 `null`（不是省略键，
    与 docs/09 §6.3 第 20 条同源）。
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


# ===========================================================================
# M2：详情 / 创建 / 编辑
# ===========================================================================
class CurrentVersionDetail(VersionDetail):
    """详情页的 `current_version`（docs/03 §3.4 的字段集）。"""

    uploaded_by: VersionUploader | None = None


class SkillDetailInfo(BaseModel):
    """`tool_type == "skill"` 时详情页的 `skill` 字段。

    **不返回完整 `readme_md` 与文件树** —— 它们可能很大，
    由 `GET /versions/{version}/skill-preview` 按需拉取（docs/03 §3.4）。
    """

    manifest: dict | None = None
    readme_md: str | None = None
    file_tree_summary: SkillTreeSummary | None = None
    parse_error: str | None = None


class PromptDetailInfo(BaseModel):
    content: str
    char_count: int


class ToolDetail(BaseModel):
    """`GET /api/v1/tools/{slug}`（docs/03 §3.4）。

    `permissions` 是服务端算好的布尔值 —— 前端不重复实现权限逻辑。
    """

    id: int
    slug: str
    name: str
    summary: str
    description_md: str
    description_html: str
    tool_type: ToolType
    visibility: ToolVisibility
    status: ToolStatus
    category: CategoryBrief | None = None
    tags: list[str] = Field(default_factory=list)
    images: list[ImageOut] = Field(default_factory=list)
    webapp_url: str | None = None
    #: ---- M14（contracts §29.3）----
    #: 详情页给**原始值**（列表页只给派生布尔）：
    #: `webapp_health_status` 是 `'ok' | 'fail' | 'timeout' | null`，
    #: `null` 表示**从未检测过**（与 `'fail'` 是两回事）；
    #: `webapp_checked_at` 是最近一次检测时间，同样可以是 `null`。
    #: 时间字段用项目统一的 `OptionalUTCDateTime`（序列化成带 `Z` 的 UTC 串）——
    #: 与 `published_at` / `updated_at` 同口径，避免 PG 的会话时区把偏移暴露到线上。
    webapp_health_status: str | None = None
    webapp_checked_at: OptionalUTCDateTime = None
    current_version: CurrentVersionDetail | None = None
    pending_version: CurrentVersionBrief | None = None
    version_count: int = 0
    history_version_count: int = 0
    download_count: int = 0
    view_count: int = 0
    #: ---- M8（contracts §23.5）----
    favorite_count: int = 0
    like_count: int = 0
    is_favorited: bool = False
    is_liked: bool = False
    #: M8 追加（contracts §23.5 的 4 个字段之外的**新增只读字段**）：
    #: B11 让作者能写入 `estimated_saving_minutes`，若详情不回读它就变成
    #: 只写不读的盲字段。按任务书「新增字段允许、改/删字段不允许」的规则补上，
    #: `NULL` 表示作者未填写。**不在列表项里返回**（列表保持精简）。
    estimated_saving_minutes: int | None = None
    owner: UserBrief
    published_at: OptionalUTCDateTime = None
    last_version_at: OptionalUTCDateTime = None
    created_at: OptionalUTCDateTime = None
    updated_at: OptionalUTCDateTime = None
    reject_reason: str | None = None
    offline_reason: str | None = None
    deleted_at: OptionalUTCDateTime = None
    #: 兼容 docs/03 §3.4 顶层的两个便捷布尔
    can_download: bool = False
    can_edit: bool = False
    permissions: ToolPermissions = Field(default_factory=ToolPermissions)
    acl: list[AclEntryOut] | None = None
    #: 按类型附加
    skill: SkillDetailInfo | None = None
    prompt: PromptDetailInfo | None = None


class CurrentVersionBrief(BaseModel):
    id: int
    version: str


class ToolCreateRequest(BaseModel):
    """`POST /me/tools`（docs/03 §3.6 的校验规则）。"""

    name: str = Field(min_length=1, max_length=128)
    summary: str = Field(min_length=1, max_length=500)
    description_md: str = Field(default="", max_length=100_000)
    tool_type: ToolType
    category_id: int | None = None
    tags: list[str] = Field(default_factory=list, max_length=8)
    visibility: ToolVisibility = ToolVisibility.PUBLIC
    webapp_url: str | None = Field(default=None, max_length=1024)
    webapp_health_url: str | None = Field(default=None, max_length=1024)
    #: M8（contracts §23.3 ③ / §23.5）：作者自述的**单次使用**预计节省分钟数。
    #: 取值范围 1~1440，超出由 Pydantic 拦成 400 `VALIDATION_ERROR`。
    #: `NULL` 表示作者未填写，**不是一个可以当成 0 的值** —— 因此**不设默认 0**。
    estimated_saving_minutes: int | None = Field(default=None, ge=1, le=1440)

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, v: list[str]) -> list[str]:
        cleaned = [t.strip() for t in v if t and t.strip()]
        if len(cleaned) > 8:
            raise ValueError("标签不能超过 8 个")
        for tag in cleaned:
            if len(tag) > 64:
                raise ValueError(f"标签过长: {tag[:20]}…")
        return cleaned

    @field_validator("webapp_url", "webapp_health_url")
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
    def _webapp_requires_url(self) -> ToolCreateRequest:
        # FR-TOOL-05：webapp 必须填内网 URL
        if self.tool_type == ToolType.WEBAPP and not self.webapp_url:
            raise ValueError("webapp 类型必须填写 URL")
        return self


class ToolUpdateRequest(BaseModel):
    """`PATCH /me/tools/{id}` —— 元信息变更，**不触发重新审批**（FR-TOOL-13）。

    所有字段可选，只更新传入的（局部更新语义）。
    """

    name: str | None = Field(default=None, min_length=1, max_length=128)
    summary: str | None = Field(default=None, min_length=1, max_length=500)
    description_md: str | None = Field(default=None, max_length=100_000)
    category_id: int | None = None
    tags: list[str] | None = Field(default=None, max_length=8)
    visibility: ToolVisibility | None = None
    webapp_url: str | None = Field(default=None, max_length=1024)
    webapp_health_url: str | None = Field(default=None, max_length=1024)
    #: M8：与 `ToolCreateRequest` 同一条范围约束（1~1440）。
    #: 局部更新语义：**省略该字段 = 不改**；显式传 `null` = 清空（回到「未填写」）。
    #: 两者必须可区分，否则作者无法撤销自己填过的估算值 —— 调用方用
    #: `model_fields_set` 判断是否显式传入。
    estimated_saving_minutes: int | None = Field(default=None, ge=1, le=1440)

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        cleaned = [t.strip() for t in v if t and t.strip()]
        if len(cleaned) > 8:
            raise ValueError("标签不能超过 8 个")
        for tag in cleaned:
            if len(tag) > 64:
                raise ValueError(f"标签过长: {tag[:20]}…")
        return cleaned

    @field_validator("webapp_url", "webapp_health_url")
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


class ToolEngagementResponse(BaseModel):
    """收藏 / 点赞 4 个端点的**统一**响应（M8，contracts §23.4）。

    契约只规定这 4 个端点「幂等」，响应形状由后端定。这里刻意返回
    **操作后的完整状态**（两个计数 + 两个当前请求者布尔），而不是一个空壳
    `{"status": "ok"}`：

    - 前端点一次按钮就能拿到最新计数，**不需要再拉一次详情**；
    - 幂等语义在响应里是自解释的 —— 重复 `PUT` 返回同样的
      `is_favorited=true` 与同一个 `favorite_count`，前端不需要靠猜。
    """

    tool_id: int
    slug: str
    #: 操作**之后**的值（PUT 恒 true，DELETE 恒 false）—— 便于前端对齐本地状态。
    is_favorited: bool = False
    is_liked: bool = False
    #: 操作之后的计数。并发下这两个数可能包含他人的写入，因此以服务端为准。
    favorite_count: int = 0
    like_count: int = 0


class MyToolListItem(BaseModel):
    """`GET /me/tools` 的元素 —— 比门户卡片多状态与理由（含草稿/驳回/下架）。"""

    id: int
    slug: str
    name: str
    summary: str
    tool_type: ToolType
    visibility: ToolVisibility
    status: ToolStatus
    category: CategoryBrief | None = None
    tags: list[str] = Field(default_factory=list)
    cover_url: str | None = None
    current_version: str | None = None
    pending_version: str | None = None
    file_size: int | None = None
    download_count: int = 0
    view_count: int = 0
    version_seq: int = 0
    reject_reason: str | None = None
    offline_reason: str | None = None
    published_at: OptionalUTCDateTime = None
    created_at: OptionalUTCDateTime = None
    updated_at: OptionalUTCDateTime = None
    #: 状态说明（前端「我的工具」列表直接显示，避免自己拼文案）
    status_label: str = ""


class MyToolListResponse(BaseModel):
    items: list[MyToolListItem]
    total: int
    page: int
    page_size: int
    pages: int


class SubmitResponse(BaseModel):
    tool_id: int
    status: ToolStatus
    version_seq: int
    auto_approved: bool = False
    auto_approved_rule: str | None = None
    approval_record_id: int | None = None


class WithdrawResponse(BaseModel):
    tool_id: int
    status: ToolStatus
    approval_record_id: int | None = None


class DownloadTicketResponse(BaseModel):
    """docs/03 §3.15。"""

    url: str
    expires_at: OptionalUTCDateTime = None
    file_name: str | None = None
    file_size: int | None = None
    file_sha256: str | None = None


ToolDetail.model_rebuild()

