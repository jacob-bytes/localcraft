"""可见性授权服务（docs/03 §3.11 / FR-ACL-01~04）。

全量替换语义 + 四条校验。切到 public/private 时 **ACL 条目保留但不生效**，
这样用户在两种可见性之间来回切换不会丢配置。
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    AclRequiredError,
    DuplicateEntryError,
    SelfGrantError,
    SubjectNotFoundError,
)
from app.core.timeutil import utcnow
from app.models.enums import AclSubjectType, ToolVisibility
from app.models.tool import Tool
from app.models.user import Group, User
from app.repositories import tool_acl as acl_repo
from app.schemas.acl import AclEntryIn, AclReplaceRequest, AclResponse
from app.services.tool_service import build_acl_entries, compute_permissions

logger = logging.getLogger(__name__)


async def replace_acl(
    session: AsyncSession,
    *,
    tool: Tool,
    payload: AclReplaceRequest,
    actor_id: int,
) -> AclResponse:
    """`PUT /me/tools/{id}/acl`。

    校验顺序刻意是「空列表 → 重复 → 自授 → 主体存在」：
    前三条是纯内存判断，不查库；主体存在性要查库（最贵），放最后。
    这样常见的错误不会白跑数据库。
    """
    entries = payload.entries

    # ---- restricted 必须有授权条目（FR-TOOL-12 / FR-ACL-01）----
    if payload.visibility == ToolVisibility.RESTRICTED and not entries:
        raise AclRequiredError(
            message="visible 设为 restricted 时至少需要一条授权",
            details={"visibility": payload.visibility.value, "entries": 0},
        )

    # ---- 重复条目 ----
    seen: set[tuple[str, int]] = set()
    for entry in entries:
        key = (entry.subject_type.value, entry.subject_id)
        if key in seen:
            raise DuplicateEntryError(
                message=f"授权条目重复：{entry.subject_type.value}#{entry.subject_id}",
                details={"subject_type": entry.subject_type.value, "subject_id": entry.subject_id},
            )
        seen.add(key)

    # ---- 不能授权给自己（owner 天然可见）----
    for entry in entries:
        if entry.subject_type == AclSubjectType.USER and entry.subject_id == tool.owner_id:
            raise SelfGrantError(
                message="不能把工具授权给 owner 自己（owner 天然可见）",
                details={"subject_id": entry.subject_id},
            )

    # ---- 主体必须存在且启用 ----
    for entry in entries:
        await _ensure_subject_active(session, entry)

    now = utcnow()
    await acl_repo.replace_all(
        session,
        tool.id,
        entries=[(e.subject_type.value, e.subject_id, e.can_download) for e in entries],
        created_by_id=actor_id,
        now=now,
    )

    visibility_changed = tool.visibility != payload.visibility.value
    tool.visibility = payload.visibility.value
    tool.updated_at = now
    # 注意：这里**没有**删除 ACL —— 切到 public/private 时条目保留但不生效
    # （docs/03 §3.11 刻意的用户体验优化）。上面的 replace_all 是「显式提交的
    # 完整列表」，用户提交什么就存什么。
    await session.commit()

    perms = compute_permissions(
        tool, user_id=actor_id, roles={"user"}, permissions={"download"}, visibility_ok=True
    )
    response = AclResponse(
        tool_id=tool.id,
        visibility=tool.visibility,
        entries=await build_acl_entries(session, tool.id),
        permissions={"can_edit": perms.can_edit, "can_view_acl": perms.can_view_acl},
        # ACL 只在 restricted 下真正参与判定
        effective=tool.visibility == ToolVisibility.RESTRICTED.value,
    )
    logger.info(
        "ACL 已替换 tool=%s visibility=%s entries=%d changed=%s",
        tool.id,
        tool.visibility,
        len(entries),
        visibility_changed,
    )
    return response


async def _ensure_subject_active(session: AsyncSession, entry: AclEntryIn) -> None:
    if entry.subject_type == AclSubjectType.USER:
        user = await session.get(User, entry.subject_id)
        if user is None or user.status != "active":
            raise SubjectNotFoundError(
                message=f"用户不存在或已禁用：{entry.subject_id}",
                details={"subject_type": "user", "subject_id": entry.subject_id},
            )
        return

    group = await session.get(Group, entry.subject_id)
    if group is None or not group.is_active:
        raise SubjectNotFoundError(
            message=f"用户组不存在或已停用：{entry.subject_id}",
            details={"subject_type": "group", "subject_id": entry.subject_id},
        )


async def get_acl_view(session: AsyncSession, *, tool: Tool, user_id: int) -> AclResponse:
    """只读地返回 ACL（`GET /me/tools/{id}` 里需要时用）。"""
    perms = compute_permissions(
        tool, user_id=user_id, roles={"user"}, permissions={"download"}, visibility_ok=True
    )
    return AclResponse(
        tool_id=tool.id,
        visibility=tool.visibility,
        entries=await build_acl_entries(session, tool.id),
        permissions={"can_edit": perms.can_edit, "can_view_acl": perms.can_view_acl},
        effective=tool.visibility == ToolVisibility.RESTRICTED.value,
    )


__all__ = ["get_acl_view", "replace_acl"]
