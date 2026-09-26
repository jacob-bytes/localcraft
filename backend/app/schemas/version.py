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
    #: 文件树是否因超过存储上限（2000 条）被截断。持久化字段，不再事后推断。
    file_tree_truncated: bool = False


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


class DuplicateVersionMatch(BaseModel):
    """上传去重命中信息（M8 / contracts §23.2）。

    **检测在服务端做**，因为浏览器端算 SHA-256 需要 `crypto.subtle`，
    而它只在安全上下文（HTTPS / localhost）可用；本项目按 D39 用
    `http://<ip>:<port>` 直连，该场景下 `crypto.subtle` 为 `undefined`。
    契约 §23.2 因此裁定：**不新增端点、不做客户端预检**，
    由上传响应回带命中信息，前端据此展示**非阻塞**提示。

    命中依据是 `tool_versions.file_sha256`（已有索引 `ix_tool_versions_sha256`，
    不新增哈希计算）。命中**不阻断上传**。
    """

    #: 命中版本所属的**其他工具**（同一工具的新版本不算重复，那是正常迭代）。
    tool_id: int
    slug: str
    name: str
    #: 命中版本本身的信息，便于前端提示「与 xx 的 v1.2.0 内容相同」。
    version_id: int
    version: str
    file_sha256: str
    #: 该版本是否为对应工具的当前版本 —— 前端可据此优先给出「可直接用」的引导。
    is_current: bool = False
    uploaded_at: OptionalUTCDateTime = None


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
    #: M8 去重命中（contracts §23.2）。`null` = 未命中或本次没有文件
    #: （webapp / prompt 类型没有 `file_sha256`，无从比对）。
    #: **不阻断上传**，只是非阻塞提示。
    duplicate_of: DuplicateVersionMatch | None = None


class VersionPatchRequest(BaseModel):
    """PATCH `/me/tools/{tool_id}/versions/{version}` —— 只能改变更说明（FR-VER-11）。"""

    changelog_md: str = Field(max_length=100_000)


class SkillPreviewResponse(BaseModel):
    """docs/03 §3.5 的响应。"""

    version: str
    manifest: dict | None = None
    readme_md: str | None = None
    file_tree: list[dict] = Field(default_factory=list)
    #: 是否被截断。**直接读持久化列**（contracts §15.3），不做任何推断。
    file_tree_truncated: bool = False
    total_size: int = 0
