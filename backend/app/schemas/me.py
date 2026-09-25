"""个人中心与图片 Schema。

docs/03 只给了路径没给形状，这里按 docs/02 的字段与 docs/04 的页面需求定义，
已在 checkpoint 报告的「待裁决」里列出，供监控方与前端对齐。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.enums import ImageKind
from app.schemas.common import OptionalUTCDateTime


class ProfileOut(BaseModel):
    """`GET /me/profile` —— 资料 + 用量（docs/03 §2.4）。"""

    id: int
    username: str
    display_name: str
    email: str | None = None
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    status: str
    auth_source: str
    must_change_password: bool
    last_login_at: OptionalUTCDateTime = None
    created_at: OptionalUTCDateTime = None
    usage: UsageOut | None = None


class ProfileUpdateRequest(BaseModel):
    """`PATCH /me/profile` —— 只能改展示名与邮箱（FR-AUTH-11）。"""

    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    email: str | None = Field(default=None, max_length=255)


class UsageOut(BaseModel):
    """用量与配额（FR-FILE-12 / `GET /me/stats`）。"""

    tool_count: int = 0
    published_tool_count: int = 0
    draft_tool_count: int = 0
    pending_tool_count: int = 0
    version_count: int = 0
    download_count: int = 0
    #: 已用存储字节（该用户上传的版本文件之和）
    used_bytes: int = 0
    #: 用户配额（字节），0 表示不限
    quota_bytes: int = 0
    #: 平台配额（字节），0 表示不限
    total_quota_bytes: int = 0
    platform_used_bytes: int = 0
    #: 已用百分比（0~100），前端进度条用
    used_percent: float = 0.0


class DownloadLogOut(BaseModel):
    """`GET /me/downloads` 的历史条目。"""

    id: int
    tool_id: int
    tool_slug: str | None = None
    tool_name: str | None = None
    version_id: int | None = None
    version: str | None = None
    file_name: str | None = None
    file_size: int | None = None
    via_api_token_id: int | None = None
    created_at: OptionalUTCDateTime = None


# ---------------------------------------------------------------------------
# 图片
# ---------------------------------------------------------------------------
class ImageOut(BaseModel):
    """docs/03 §3.4 的 `images[]` 元素。"""

    id: int
    kind: ImageKind
    url: str
    thumb_url: str
    file_name: str | None = None
    mime_type: str | None = None
    file_size: int | None = None
    width: int | None = None
    height: int | None = None
    sort_order: int = 0
    alt_text: str | None = None
    sha256: str | None = None
    created_at: OptionalUTCDateTime = None


class ImagePatchRequest(BaseModel):
    """`PATCH /me/tools/{id}/images/{image_id}`。"""

    sort_order: int | None = None
    alt_text: str | None = Field(default=None, max_length=255)
    #: 设为封面（同一工具至多 1 张，服务端会把原封面降级为截图）
    set_as_cover: bool | None = None


class ImageListResponse(BaseModel):
    items: list[ImageOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 工具统计（`GET /tools/{slug}/stats`）
# ---------------------------------------------------------------------------
class DailyStatPoint(BaseModel):
    stat_date: str
    views: int
    downloads: int


class ToolStatsResponse(BaseModel):
    tool_id: int
    download_count: int
    view_count: int
    #: 最近 N 天的每日汇总（来自 tool_stats_daily）
    daily: list[DailyStatPoint] = Field(default_factory=list)


ProfileOut.model_rebuild()
