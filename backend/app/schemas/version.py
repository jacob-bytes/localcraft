"""版本相关 Schema（docs/02 §3.11 / docs/03 §3.5 §3.7）。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import VersionStatus
from app.schemas.common import OptionalUTCDateTime, ORMModel


class VersionUploader(BaseModel):
    """版本上传者（docs/03 §3.4 的 `current_version.uploaded_by`）。

    只有 `id` 与 `display_name` —— 不是完整的 UserBrief，按 docs 示例来。
    """

    id: int
    display_name: str


class SkillTreeSummary(BaseModel):
    file_count: int
    total_size: int
    max_depth: int


class SkillVersionInfo(BaseModel):
    """版本上的 skill 附加信息（docs/03 §3.7 的 `skill` 字段）。"""

    manifest: dict | None = None
    file_tree_summary: SkillTreeSummary | None = None
    parse_error: str | None = None


class VersionSummary(ORMModel):
    """版本列表项 / `me/tools/{id}/versions` 的元素。

    `file_sha256` 在列表里给**前 16 位**（FR-VER-06），完整值在详情与下载接口里给。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    tool_id: int
    version: str
    changelog_md: str
    status: VersionStatus
    is_current: bool
    file_name: str | None = None
    file_size: int | None = None
    file_sha256_short: str | None = None
    file_ext: str | None = None
    mime_type: str | None = None
    uploaded_by: VersionUploader | None = None
    approved_at: OptionalUTCDateTime = None
    reject_reason: str | None = None
    purged_at: OptionalUTCDateTime = None
    created_at: OptionalUTCDateTime = None
    #: 是否可下载（purged / rejected / pending 不可下载）
    can_download: bool = False


class VersionDetail(VersionSummary):
    """版本详情 —— 附带完整 SHA256 与 skill 信息。"""

    file_sha256: str | None = None
    skill: SkillVersionInfo | None = None
    prompt_content: str | None = None


class VersionUploadResponse(BaseModel):
    """docs/03 §3.7 的 201 响应。"""

    id: int
    tool_id: int
    version: str
    status: VersionStatus
    file_name: str | None = None
    file_size: int | None = None
    file_sha256: str | None = None
    prompt_content: str | None = None
    skill: SkillVersionInfo | None = None
    tool_status: str
    created_at: OptionalUTCDateTime = None


class VersionPatchRequest(BaseModel):
    """PATCH `/me/tools/{tool_id}/versions/{version}` —— 只能改变更说明（FR-VER-11）。"""

    changelog_md: str = Field(max_length=100_000)


class SkillPreviewResponse(BaseModel):
    """docs/03 §3.5 的响应。"""

    version: str
    manifest: dict | None = None
    readme_md: str | None = None
    file_tree: list[dict] = Field(default_factory=list)
    file_tree_truncated: bool = False
    total_size: int = 0
