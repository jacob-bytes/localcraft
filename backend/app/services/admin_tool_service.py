"""全站工具管理与回收站（FR-ADMIN-12 / FR-ADMIN-15 / FR-IAM-11）。

与 `/me/tools` 的区别：
  - 这里能看**所有作者、所有状态**的工具
  - 管理侧代创建接口允许指定 `owner_id`，供脚本批量导入用（docs/03 §5.2）
  - 多了转移负责人、还原、彻底清除三个动作

**回收站的物理清除**必须同时删文件：`tools` 行删掉后 `tool_versions`
会因外键 CASCADE 一起消失，但磁盘文件不会自己走 —— 必须显式删。
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, StateConflictError, ValidationError
from app.core.timeutil import utcnow
from app.models.enums import ApprovalAction, ToolStatus
from app.models.taxonomy import Category
from app.models.tool import Tool, ToolTag, ToolVersion
from app.models.user import User
from app.repositories import approvals as approvals_repo
from app.repositories import tool_versions as versions_repo
from app.repositories import tools as tools_repo
from app.schemas.admin import (
    AdminToolCreateRequest,
    AdminToolItem,
    AdminToolListResponse,
    PurgeToolResponse,
    TransferOwnerRequest,
    TransferOwnerResponse,
)
from app.services.tool_service import allocate_slug

logger = logging.getLogger(__name__)

#: 回收站保留天数（docs/02 §5：软删除超 30 天由清理任务彻底清除）
RECYCLE_BIN_RETENTION_DAYS = 30


def _to_item(tool: Tool) -> AdminToolItem:
    return AdminToolItem(
        id=tool.id,
        slug=tool.slug,
        name=tool.name,
        summary=tool.summary,
        tool_type=tool.tool_type,
        visibility=tool.visibility,
        status=tool.status,
        category=(
            {
                "id": tool.category.id,
                "slug": tool.category.slug,
                "name": tool.category.name,
                "icon": tool.category.icon,
            }
            if tool.category is not None
            else None
        ),
        tags=[t.display_name for t in tool.tags],
        cover_url=(
            f"/api/v1/images/{tool.cover_image_id}?variant=thumb"
            if tool.cover_image_id
            else None
        ),
        owner=(
            {
                "id": tool.owner.id,
                "username": tool.owner.username,
                "display_name": tool.owner.display_name,
            }
            if tool.owner is not None
            else None
        ),
        current_version=tool.current_version.version if tool.current_version else None,
        download_count=tool.download_count,
        view_count=tool.view_count,
        version_seq=tool.version_seq,
        reject_reason=tool.reject_reason,
        offline_reason=tool.offline_reason,
        deleted_at=tool.deleted_at,
        published_at=tool.published_at,
        created_at=tool.created_at,
        updated_at=tool.updated_at,
    )


async def list_all_tools(
    session: AsyncSession,
    *,
    q: str | None = None,
    statuses: list[str] | None = None,
    tool_types: list[str] | None = None,
    category_slugs: list[str] | None = None,
    visibilities: list[str] | None = None,
    owner_username: str | None = None,
    include_deleted: bool = False,
    only_deleted: bool = False,
    date_from=None,
    date_to=None,
    limit: int = 20,
    offset: int = 0,
) -> AdminToolListResponse:
    """全站工具列表（FR-ADMIN-12）。

    筛选组合很多，所以每个条件都走已经建好的索引：
    `ix_tools_status_visibility` / `ix_tools_owner_status` /
    `ix_tools_category_status` / `ix_tools_deleted_at`。

    `deleted_at` 的条件刻意放在最前面：它是选择性最高的一维
    （回收站里的行通常只占极小比例），能最快把候选集缩小。
    """
    stmt = select(Tool)
    if only_deleted:
        stmt = stmt.where(Tool.deleted_at.is_not(None))
    elif not include_deleted:
        stmt = stmt.where(Tool.deleted_at.is_(None))

    if statuses:
        stmt = stmt.where(Tool.status.in_(statuses))
    if tool_types:
        stmt = stmt.where(Tool.tool_type.in_(tool_types))
    if visibilities:
        stmt = stmt.where(Tool.visibility.in_(visibilities))
    if category_slugs:
        stmt = stmt.where(
            Tool.category_id.in_(select(Category.id).where(Category.slug.in_(category_slugs)))
        )
    if owner_username:
        stmt = stmt.where(
            Tool.owner_id.in_(
                select(User.id).where(User.username == owner_username.strip().lower())
            )
        )
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(Tool.name.ilike(pattern) | Tool.summary.ilike(pattern))
    if date_from is not None:
        stmt = stmt.where(Tool.created_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(Tool.created_at <= date_to)

    total = int(
        (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    )
    rows = list(
        (
            await session.execute(
                stmt.order_by(Tool.updated_at.desc(), Tool.id.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .unique()
        .all()
    )
    return AdminToolListResponse(
        items=[_to_item(t) for t in rows],
        total=total,
        page=offset // limit + 1 if limit else 1,
        page_size=limit,
        pages=(total + limit - 1) // limit if limit else 0,
    )


async def create_tool_as_admin(
    session: AsyncSession, *, payload: AdminToolCreateRequest, actor_id: int
) -> AdminToolItem:
    """管理侧代创建（docs/03 §5.2）：允许指定 `owner_id`，可选直接发布。

    与 `/me/tools` 的关键差别是**绕过 owner 校验**（由 `admin:all` scope 授权）。
    """
    owner = await session.get(User, payload.owner_id)
    if owner is None:
        raise NotFoundError(
            message="指定的负责人不存在", details={"owner_id": payload.owner_id}
        )
    if owner.status != "active":
        raise ValidationError(
            message="不能把工具指派给已禁用的用户", details={"owner_id": payload.owner_id}
        )
    if payload.category_id is not None:
        category = await session.get(Category, payload.category_id)
        if category is None or not category.is_active:
            raise ValidationError(
                message="分类不存在或已停用",
                fields=[{"field": "category_id", "message": "分类不存在或已停用"}],
            )

    now = utcnow()
    slug = await allocate_slug(session, payload.name)
    tool = Tool(
        slug=slug,
        name=payload.name.strip(),
        summary=payload.summary.strip(),
        description_md=payload.description_md,
        tool_type=payload.tool_type.value,
        visibility=payload.visibility.value,
        # 直接发布时状态为 approved；否则是草稿（与 /me/tools 一致）
        status=ToolStatus.APPROVED.value if payload.publish else ToolStatus.DRAFT.value,
        owner_id=owner.id,
        category_id=payload.category_id,
        webapp_url=payload.webapp_url,
        published_at=now if payload.publish else None,
        version_seq=1,
        created_at=now,
        updated_at=now,
    )
    session.add(tool)
    await session.flush()

    if payload.tags:
        from app.repositories import tags as tags_repo

        for name in payload.tags[:8]:
            tag = await tags_repo.get_or_create(session, name, created_by_id=actor_id)
            session.add(ToolTag(tool_id=tool.id, tag_id=tag.id))

    # 代创建不写审批记录：它不是一次审批动作。
    # 但如果是直接发布，要写一条 approve 记录，让审计链完整。
    if payload.publish:
        await approvals_repo.insert_record(
            session,
            tool_id=tool.id,
            version_id=None,
            action=ApprovalAction.APPROVE,
            from_status=ToolStatus.DRAFT.value,
            to_status=ToolStatus.APPROVED.value,
            actor_id=actor_id,
            actor_label="管理侧代创建",
            is_automatic=False,
            note="admin 代创建并直接发布",
            now=now,
        )
    await session.commit()

    tool = await tools_repo.get_by_id(session, tool.id)
    assert tool is not None
    return _to_item(tool)


async def transfer_owner(
    session: AsyncSession,
    *,
    tool_id: int,
    payload: TransferOwnerRequest,
    actor_id: int,
    actor_label: str,
) -> TransferOwnerResponse:
    """转移负责人（FR-IAM-11）。

    「同步校验原负责人的存储配额扣减与新负责人的配额增加」的实现方式：
    配额是**按 owner 实时聚合**的（`sum_file_size_for_user`），不是一张余额表，
    所以转移本身就是配额变化 —— 这里显式把转移前后的两个值算出来回传，
    让管理员能立刻看到双方的新用量，而不是等下一次查询才发现。
    """
    tool = await session.get(Tool, tool_id)
    if tool is None or tool.deleted_at is not None:
        raise NotFoundError(message="工具不存在", details={"tool_id": tool_id})

    new_owner = await session.get(User, payload.new_owner_id)
    if new_owner is None:
        raise NotFoundError(
            message="目标负责人不存在", details={"new_owner_id": payload.new_owner_id}
        )
    if new_owner.status != "active":
        raise ValidationError(
            message="不能把工具转移给已禁用的用户",
            details={"new_owner_id": payload.new_owner_id},
        )
    if new_owner.id == tool.owner_id:
        raise StateConflictError(
            message="目标负责人与当前负责人相同",
            details={"tool_id": tool_id, "owner_id": new_owner.id},
        )

    previous_owner = await session.get(User, tool.owner_id)
    now = utcnow()
    tool.owner_id = new_owner.id
    tool.version_seq = (tool.version_seq or 0) + 1
    tool.updated_at = now
    await session.flush()

    record = await approvals_repo.insert_record(
        session,
        tool_id=tool.id,
        version_id=tool.current_version_id,
        action=ApprovalAction.TRANSFER_OWNER,
        from_status=tool.status,
        to_status=tool.status,
        actor_id=actor_id,
        actor_label=actor_label,
        reason=payload.reason,
        note=(
            f"负责人由 {previous_owner.display_name if previous_owner else '未知'}"
            f" 转移给 {new_owner.display_name}"
        ),
        now=now,
    )

    # 转移后重新聚合双方的用量（这就是「配额重算」）
    previous_used = (
        await versions_repo.sum_file_size_for_user(session, previous_owner.id)
        if previous_owner is not None
        else 0
    )
    new_used = await versions_repo.sum_file_size_for_user(session, new_owner.id)
    await session.commit()

    logger.info(
        "转移负责人 tool=%s from=%s to=%s", tool.slug, tool.owner_id, new_owner.id
    )
    return TransferOwnerResponse(
        tool_id=tool.id,
        previous_owner=(
            {
                "id": previous_owner.id,
                "username": previous_owner.username,
                "display_name": previous_owner.display_name,
            }
            if previous_owner is not None
            else None
        ),
        new_owner={
            "id": new_owner.id,
            "username": new_owner.username,
            "display_name": new_owner.display_name,
        },
        previous_owner_used_bytes=previous_used,
        new_owner_used_bytes=new_used,
        approval_record_id=record.id,
    )


# ===========================================================================
# 回收站
# ===========================================================================
async def list_recycle_bin(
    session: AsyncSession, *, limit: int = 20, offset: int = 0
) -> AdminToolListResponse:
    return await list_all_tools(session, only_deleted=True, limit=limit, offset=offset)


async def restore_tool(session: AsyncSession, *, tool_id: int) -> AdminToolItem:
    """还原软删除的工具。"""
    tool = await session.get(Tool, tool_id)
    if tool is None:
        raise NotFoundError(message="工具不存在", details={"tool_id": tool_id})
    if tool.deleted_at is None:
        raise StateConflictError(
            message="该工具不在回收站中", details={"tool_id": tool_id}
        )

    now = utcnow()
    tool.deleted_at = None
    tool.deleted_by_id = None
    tool.updated_at = now
    # 重新进搜索索引
    from app.search import SearchDocument, get_search_backend

    await session.flush()
    await get_search_backend().upsert(
        session,
        tool.id,
        SearchDocument(
            name=tool.name,
            summary=tool.summary,
            description=tool.description_md,
            tags=[t.display_name for t in tool.tags],
        ),
    )
    await session.commit()
    tool = await tools_repo.get_by_id(session, tool_id)
    assert tool is not None
    return _to_item(tool)


async def purge_tool(session: AsyncSession, *, tool_id: int) -> PurgeToolResponse:
    """**彻底清除**：删文件 + 删数据库行（带 CASCADE）。

    顺序很重要：**先删文件再删行**。反过来的话，如果删文件失败，
    数据库里已经没有记录指向那些文件了 —— 它们就变成孤儿，
    只能靠孤儿清理任务扫盘（那是 P1 的兜底，不该是主路径）。
    """
    tool = await session.get(Tool, tool_id)
    if tool is None:
        raise NotFoundError(message="工具不存在", details={"tool_id": tool_id})

    versions = list(
        (
            await session.execute(
                select(ToolVersion).where(ToolVersion.tool_id == tool_id)
            )
        )
        .scalars()
        .all()
    )
    from app.storage import get_storage

    storage = get_storage()
    purged_files = 0
    for version in versions:
        if version.storage_path and await storage.delete(version.storage_path):
            purged_files += 1

    # 图片文件
    from app.models.tool import ToolImage

    images = list(
        (await session.execute(select(ToolImage).where(ToolImage.tool_id == tool_id)))
        .scalars()
        .all()
    )
    for image in images:
        for path in (image.storage_path, image.thumb_path):
            if path and await storage.delete(path):
                purged_files += 1

    # 搜索索引
    from app.search import get_search_backend

    await get_search_backend().delete(session, tool_id)

    # 最后删数据库行（tool_versions / tool_tags / tool_acl / approval_records
    # 都有 ON DELETE CASCADE，会跟着走）
    await session.delete(tool)
    await session.commit()

    logger.info(
        "彻底清除工具 id=%s slug=%s 删除文件=%s 版本=%s",
        tool_id,
        tool.slug,
        purged_files,
        len(versions),
    )
    return PurgeToolResponse(
        tool_id=tool_id, purged_files=purged_files, purged_versions=len(versions)
    )


async def purge_expired_recycle_bin(
    session: AsyncSession, *, older_than_days: int = RECYCLE_BIN_RETENTION_DAYS
) -> list[int]:
    """清理回收站里超过保留期的工具（docs/02 §5 的每日 03:30 任务）。

    返回被清除的 tool_id 列表。走 `purge_tool` 复用同一套清理逻辑，
    保证「文件先删、行后删」的顺序只有一个实现。
    """
    cutoff = utcnow() - timedelta(days=older_than_days)
    result = await session.execute(
        select(Tool.id).where(Tool.deleted_at.is_not(None), Tool.deleted_at < cutoff)
    )
    ids = [int(r[0]) for r in result.all()]
    for tool_id in ids:
        await purge_tool(session, tool_id=tool_id)
    return ids


__all__ = [
    "RECYCLE_BIN_RETENTION_DAYS",
    "create_tool_as_admin",
    "list_all_tools",
    "list_recycle_bin",
    "purge_expired_recycle_bin",
    "purge_tool",
    "restore_tool",
    "transfer_owner",
]
