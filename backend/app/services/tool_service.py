"""工具服务：门户列表装配 + M2 的创建/编辑/删除/详情。

本模块是「工具」这个聚合根的服务层，事务边界也在这里（docs/03 §6.3）。
"""

from __future__ import annotations

import math
import re
import secrets
import unicodedata

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import PERM_DOWNLOAD
from app.models.enums import AclSubjectType, RoleCode, ToolStatus, ToolType, VersionStatus
from app.models.tool import Tool, ToolImage, ToolVersion
from app.models.user import Group, User
from app.repositories import tool_acl as tool_acl_repo
from app.repositories import tool_images as tool_images_repo
from app.repositories import tool_versions as tool_versions_repo
from app.repositories import tools as tools_repo
from app.repositories.tools import PortalFilters, VisibilityContext
from app.schemas.acl import AclEntryOut, ToolPermissions
from app.schemas.auth import UserBrief
from app.schemas.me import ImageOut
from app.schemas.taxonomy import CategoryBrief
from app.schemas.tool import (
    CurrentVersionBrief,
    CurrentVersionDetail,
    PromptDetailInfo,
    SkillDetailInfo,
    ToolDetail,
    ToolFacets,
    ToolListItem,
    ToolListResponse,
)
from app.schemas.version import SkillTreeSummary, SkillVersionInfo, VersionUploader
from app.services.markdown_service import render_markdown

#: 能看到「有新版本待审」角标的角色（docs/03 §3.3，SRS 待确认 Q3）
PENDING_VISIBLE_ROLES: frozenset[str] = frozenset(
    {RoleCode.APPROVER.value, RoleCode.SUPERADMIN.value}
)


def cover_url_for(tool: Tool) -> str | None:
    """封面图走 `/api/v1/images/{id}` 接口而不是静态路径 ——
    接口内做可见性校验，避免用裸 ID 枚举（docs/02 §1.4）。"""
    if tool.cover_image_id is None:
        return None
    return f"/api/v1/images/{tool.cover_image_id}?variant=thumb"


def build_list_item(
    tool: Tool,
    *,
    viewer_user_id: int | None,
    viewer_roles: frozenset[str] | set[str],
    can_download: bool,
) -> ToolListItem:
    """ORM → 卡片。字段逐条对应 docs/03 §3.3 的响应示例。"""
    sees_pending = viewer_user_id == tool.owner_id or bool(
        set(viewer_roles) & PENDING_VISIBLE_ROLES
    )
    current = tool.current_version
    return ToolListItem(
        id=tool.id,
        slug=tool.slug,
        name=tool.name,
        summary=tool.summary,
        tool_type=tool.tool_type,
        visibility=tool.visibility,
        category=(
            CategoryBrief(
                id=tool.category.id,
                slug=tool.category.slug,
                name=tool.category.name,
                icon=tool.category.icon,
            )
            if tool.category is not None
            else None
        ),
        # 展示用 display_name（保留原始大小写）；服务端对 `?tag=` 入参做同样的
        # 归一化，所以前端拿这个值回填 URL 依然能正确筛选（FR-TAX-03）。
        tags=[t.display_name for t in tool.tags],
        cover_url=cover_url_for(tool),
        owner=UserBrief(
            id=tool.owner.id,
            username=tool.owner.username,
            display_name=tool.owner.display_name,
        ),
        current_version=current.version if current is not None else None,
        file_size=current.file_size if current is not None else None,
        download_count=tool.download_count,
        view_count=tool.view_count,
        # 兜底：owner/approver 之外恒为 false（避免通过该字段探测待审状态）
        has_pending_version=bool(tool.pending_version_id is not None and sees_pending),
        can_download=can_download,
        published_at=tool.published_at,
        updated_at=tool.updated_at,
    )


async def list_portal_tools(
    session: AsyncSession,
    *,
    visibility: VisibilityContext,
    filters: PortalFilters,
    sort: str,
    page: int,
    page_size: int,
    viewer_roles: frozenset[str] | set[str],
    can_download: bool,
) -> ToolListResponse:
    """门户列表 + facets。

    **`facets` 只在 `page == 1` 时返回**，翻页时整个键省略（docs/03 §3.3），
    避免每次翻页都重复算一遍聚合。
    """
    total = await tools_repo.count_portal_tools(session, visibility=visibility, filters=filters)
    rows = await tools_repo.list_portal_tools(
        session,
        visibility=visibility,
        filters=filters,
        sort=sort,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    items = [
        build_list_item(
            tool,
            viewer_user_id=visibility.user_id,
            viewer_roles=viewer_roles,
            can_download=can_download,
        )
        for tool in rows
    ]

    facets: ToolFacets | None = None
    if page == 1:
        facets = ToolFacets(
            categories=await tools_repo.build_category_facets(session, visibility=visibility),
            types=await tools_repo.build_type_facets(session, visibility=visibility),
        )

    pages = math.ceil(total / page_size) if page_size else 0
    return ToolListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
        facets=facets,
    )


# ===========================================================================
# M2：创建 / 编辑 / 删除 / 详情
# ===========================================================================
#: slug 主干的最大长度（DB 列 String(128)，留出 -hhhh 后缀与去重余量）
SLUG_MAX_LENGTH = 100

#: 状态的中文说明 —— 由服务端下发，前端「我的工具」列表直接显示
STATUS_LABELS: dict[str, str] = {
    ToolStatus.DRAFT.value: "草稿",
    ToolStatus.PENDING.value: "待审批",
    ToolStatus.APPROVED.value: "已发布",
    ToolStatus.REJECTED.value: "已驳回",
    ToolStatus.PENDING_UPDATE.value: "有新版本待审",
    ToolStatus.OFFLINE.value: "已下架",
}


def generate_slug_base(name: str) -> str:
    """由名称转写 slug 主干（FR-TOOL-08）。

    不做拼音转换（那需要额外依赖且质量参差）：只保留 ASCII 字母数字，
    其余（含中文）折叠成 `-`。纯中文名会得到空串，由调用方回退成
    `tool-<短哈希>`，这样 URL 仍然可读且稳定。
    """
    normalized = unicodedata.normalize("NFKD", name)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only).strip("-").lower()
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:SLUG_MAX_LENGTH].strip("-")


async def allocate_slug(session: AsyncSession, name: str) -> str:
    """生成全局唯一 slug。冲突时追加 4 位短哈希（FR-TOOL-08）。"""
    base = generate_slug_base(name)
    if not base:
        # 纯中文/纯符号名称：直接带哈希，避免所有中文工具都叫 "tool"
        base = "tool"
        candidate = f"{base}-{secrets.token_hex(2)}"
        while await tools_repo.slug_exists(session, candidate):
            candidate = f"{base}-{secrets.token_hex(2)}"
        return candidate

    if not await tools_repo.slug_exists(session, base):
        return base

    for _ in range(20):
        candidate = f"{base}-{secrets.token_hex(2)}"
        if not await tools_repo.slug_exists(session, candidate):
            return candidate
    # 极端情况（同一名称被创建几百次）下加大熵
    return f"{base}-{secrets.token_hex(8)}"


def compute_permissions(
    tool: Tool,
    *,
    user_id: int | None,
    roles: frozenset[str] | set[str],
    permissions: frozenset[str] | set[str],
    visibility_ok: bool,
) -> ToolPermissions:
    """服务端算好权限布尔值（docs/03 §3.4）。

    前端不重复实现权限逻辑，直接读这些布尔值 —— 避免「按钮显示了但调用失败」。
    """
    role_set = set(roles)
    perm_set = set(permissions)
    is_owner = user_id is not None and tool.owner_id == user_id
    is_approver = bool(role_set & {RoleCode.APPROVER.value, RoleCode.SUPERADMIN.value})
    is_admin = RoleCode.SUPERADMIN.value in role_set
    alive = tool.deleted_at is None

    # FR-TOOL-11：pending 状态下不可编辑内容（要先撤回或等结果）
    can_edit = bool(is_owner and alive and tool.status != ToolStatus.PENDING.value)
    # 只有「当前版本可下载」的状态才谈得上下载
    downloadable_status = tool.status in (
        ToolStatus.APPROVED.value,
        ToolStatus.PENDING_UPDATE.value,
    )
    has_current = tool.current_version_id is not None
    can_download = bool(
        PERM_DOWNLOAD in perm_set
        and visibility_ok
        and alive
        and downloadable_status
        and has_current
    )
    can_manage = bool(
        is_owner
        and alive
        and PERM_DOWNLOAD in perm_set
        and tool.status != ToolStatus.PENDING.value
    )
    return ToolPermissions(
        can_edit=can_edit,
        can_download=can_download,
        can_manage_versions=can_manage,
        can_view_acl=bool(is_owner or is_admin),
        can_delete=bool(is_owner and alive),
        can_submit=bool(is_owner and alive and tool.status in _SUBMITTABLE_STATUSES),
        can_approve=bool(is_approver and alive),
    )


#: 可以提交审批的状态（docs/01 §4.1 的迁移表）
_SUBMITTABLE_STATUSES: frozenset[str] = frozenset(
    {ToolStatus.DRAFT.value, ToolStatus.REJECTED.value}
)


def image_urls(image_id: int) -> tuple[str, str]:
    """图片的访问与缩略图 URL（走接口而不是静态路径，接口内做可见性校验）。"""
    return f"/api/v1/images/{image_id}", f"/api/v1/images/{image_id}?variant=thumb"


def build_image_out(image: ToolImage) -> ImageOut:
    url, thumb = image_urls(image.id)
    return ImageOut(
        id=image.id,
        kind=image.kind,
        url=url,
        thumb_url=thumb,
        file_name=image.file_name,
        mime_type=image.mime_type,
        file_size=image.file_size,
        width=image.width,
        height=image.height,
        sort_order=image.sort_order,
        alt_text=image.alt_text,
        sha256=image.sha256,
        created_at=image.created_at,
    )


def build_version_detail(version: ToolVersion | None) -> CurrentVersionDetail | None:
    if version is None:
        return None
    return CurrentVersionDetail(
        id=version.id,
        tool_id=version.tool_id,
        version=version.version,
        changelog_md=version.changelog_md,
        status=version.status,
        is_current=version.is_current,
        file_name=version.file_name,
        file_size=version.file_size,
        file_sha256=version.file_sha256,
        file_sha256_short=(version.file_sha256 or "")[:16] or None,
        file_ext=version.file_ext,
        mime_type=version.mime_type,
        prompt_content=version.prompt_content,
        uploaded_by=(
            VersionUploader(
                id=version.uploader.id, display_name=version.uploader.display_name
            )
            if getattr(version, "uploader", None) is not None
            else None
        ),
        approved_at=version.approved_at,
        reject_reason=version.reject_reason,
        purged_at=version.purged_at,
        created_at=version.created_at,
        can_download=version.status in (
            VersionStatus.APPROVED.value,
            VersionStatus.SUPERSEDED.value,
        ),
        skill=(
            SkillVersionInfo(
                manifest=version.skill_manifest,
                file_tree_summary=_tree_summary(version),
                parse_error=version.skill_parse_error,
                file_tree_truncated=bool(version.skill_tree_truncated),
            )
            if version.skill_manifest is not None
            or version.skill_readme_md is not None
            or version.skill_parse_error is not None
            else None
        ),
    )


def _tree_summary(version: ToolVersion) -> SkillTreeSummary | None:
    tree = version.skill_file_tree
    if not tree:
        return None
    file_count = sum(1 for item in tree if not item.get("is_dir"))
    total_size = sum(int(item.get("size") or 0) for item in tree)
    max_depth = max(
        (len(str(item.get("path", "")).split("/")) for item in tree), default=0
    )
    return SkillTreeSummary(
        file_count=file_count, total_size=total_size, max_depth=max_depth
    )


async def build_detail(
    session: AsyncSession,
    *,
    tool: Tool,
    user_id: int | None,
    roles: frozenset[str] | set[str],
    permissions: frozenset[str] | set[str],
    visibility_ok: bool = True,
    include_acl: bool = False,
) -> ToolDetail:
    """组装 `GET /tools/{slug}` 的响应（docs/03 §3.4）。"""
    images = await tool_images_repo.list_for_tool(session, tool.id)
    current = (
        await tool_versions_repo.get_by_id(session, tool.current_version_id)
        if tool.current_version_id
        else None
    )
    pending = (
        await tool_versions_repo.get_by_id(session, tool.pending_version_id)
        if tool.pending_version_id
        else None
    )
    total_versions, history_versions = await tool_versions_repo.version_stats(session, tool.id)

    perms = compute_permissions(
        tool, user_id=user_id, roles=roles, permissions=permissions, visibility_ok=visibility_ok
    )

    skill_info: SkillDetailInfo | None = None
    prompt_info: PromptDetailInfo | None = None
    if tool.tool_type == ToolType.SKILL and current is not None:
        skill_info = SkillDetailInfo(
            manifest=current.skill_manifest,
            readme_md=None,  # 正文可能很大，交给 skill-preview 按需拉取
            file_tree_summary=_tree_summary(current),
            parse_error=current.skill_parse_error,
        )
    if tool.tool_type == ToolType.PROMPT and current is not None:
        content = current.prompt_content or ""
        prompt_info = PromptDetailInfo(content=content, char_count=len(content))

    acl_entries: list[AclEntryOut] | None = None
    if include_acl and perms.can_view_acl:
        acl_entries = await build_acl_entries(session, tool.id)

    return ToolDetail(
        id=tool.id,
        slug=tool.slug,
        name=tool.name,
        summary=tool.summary,
        description_md=tool.description_md,
        description_html=render_markdown(tool.description_md),
        tool_type=tool.tool_type,
        visibility=tool.visibility,
        status=tool.status,
        category=(
            CategoryBrief(
                id=tool.category.id,
                slug=tool.category.slug,
                name=tool.category.name,
                icon=tool.category.icon,
            )
            if tool.category is not None
            else None
        ),
        tags=[t.display_name for t in tool.tags],
        images=[build_image_out(image) for image in images],
        webapp_url=tool.webapp_url,
        current_version=build_version_detail(current),
        pending_version=(
            CurrentVersionBrief(id=pending.id, version=pending.version)
            if pending is not None
            else None
        ),
        version_count=total_versions,
        history_version_count=history_versions,
        download_count=tool.download_count,
        view_count=tool.view_count,
        owner=UserBrief(
            id=tool.owner.id,
            username=tool.owner.username,
            display_name=tool.owner.display_name,
        ),
        published_at=tool.published_at,
        last_version_at=tool.last_version_at,
        created_at=tool.created_at,
        updated_at=tool.updated_at,
        reject_reason=tool.reject_reason,
        offline_reason=tool.offline_reason,
        deleted_at=tool.deleted_at,
        can_download=perms.can_download,
        can_edit=perms.can_edit,
        permissions=perms,
        acl=acl_entries,
        skill=skill_info,
        prompt=prompt_info,
    )


async def build_acl_entries(session: AsyncSession, tool_id: int) -> list[AclEntryOut]:
    """ACL 条目 + 主体名称（避免前端再查一次）。"""
    rows = await tool_acl_repo.list_for_tool(session, tool_id)
    entries: list[AclEntryOut] = []
    for row in rows:
        name: str | None = None
        if row.subject_type == AclSubjectType.USER.value:
            user = await session.get(User, row.subject_id)
            name = user.display_name if user else None
        else:
            group = await session.get(Group, row.subject_id)
            name = group.name if group else None
        entries.append(
            AclEntryOut(
                id=row.id,
                subject_type=row.subject_type,
                subject_id=row.subject_id,
                subject_name=name,
                can_download=row.can_download,
            )
        )
    return entries
