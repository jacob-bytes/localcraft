"""分类与标签管理服务（FR-TAX-01~07 / docs/01 §5.4）。

**分类禁止物理删除**（FR-TAX-02）：`tools.category_id` 有外键，硬删会让工具悬空。
有引用时返回 `409 CATEGORY_IN_USE` 并给出引用数；无引用时软删除（`is_active=false`）。

`tags.usage_count` **不在增删关联时维护**，由定时任务重算（docs/02 §3.7）：
并发下增量维护必然漂移，而这只是展示用的排序依据，不是强一致需求。
"""

from __future__ import annotations

import logging
import re
import unicodedata

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import CategoryInUseError, DuplicateEntryError, NotFoundError, ValidationError
from app.core.timeutil import utcnow
from app.models.taxonomy import Category, Tag
from app.models.tool import Tool, ToolTag
from app.repositories import tags as tags_repo
from app.schemas.admin import (
    AdminCategoryCreateRequest,
    AdminCategoryOut,
    AdminCategoryUpdateRequest,
    AdminTagOut,
    CategoryOrderItem,
    TagCleanupResponse,
    TagMergeRequest,
    TagMergeResponse,
)

logger = logging.getLogger(__name__)


def slugify(name: str) -> str:
    """中文名转 slug 的兜底：保留 ASCII 字母数字，其余折叠成 `-`。

    分类是管理员维护的少量数据，所以不做短哈希；转不出可读 slug 时
    回退成 `cat-<序号>`，由调用方保证唯一。
    """
    normalized = unicodedata.normalize("NFKD", name)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only).strip("-").lower()
    return re.sub(r"-{2,}", "-", slug)[:64]


# ===========================================================================
# 分类
# ===========================================================================
async def _tool_counts(session: AsyncSession) -> dict[int, int]:
    result = await session.execute(
        select(Tool.category_id, func.count(Tool.id))
        .where(Tool.deleted_at.is_(None))
        .group_by(Tool.category_id)
    )
    return {int(r[0]): int(r[1]) for r in result.all() if r[0] is not None}


def _to_out(category: Category, tool_count: int) -> AdminCategoryOut:
    return AdminCategoryOut(
        id=category.id,
        slug=category.slug,
        name=category.name,
        description=category.description,
        icon=category.icon,
        sort_order=category.sort_order,
        is_active=category.is_active,
        tool_count=tool_count,
        created_at=category.created_at,
        updated_at=category.updated_at,
    )


async def list_categories(session: AsyncSession) -> list[AdminCategoryOut]:
    """管理侧列表 —— **含禁用的**（与门户的 `/categories` 不同）。"""
    result = await session.execute(
        select(Category).order_by(Category.sort_order.asc(), Category.id.asc())
    )
    categories = list(result.scalars().all())
    counts = await _tool_counts(session)
    return [_to_out(c, counts.get(c.id, 0)) for c in categories]


async def create_category(
    session: AsyncSession, *, payload: AdminCategoryCreateRequest
) -> AdminCategoryOut:
    name = payload.name.strip()
    slug = (payload.slug or "").strip() or slugify(name) or f"cat-{int(utcnow().timestamp())}"

    if (
        await session.execute(select(Category.id).where(Category.name == name))
    ).first() is not None:
        raise DuplicateEntryError(message=f"分类名已存在：{name}", details={"field": "name"})
    if (
        await session.execute(select(Category.id).where(Category.slug == slug))
    ).first() is not None:
        raise DuplicateEntryError(message=f"分类 slug 已存在：{slug}", details={"field": "slug"})

    now = utcnow()
    category = Category(
        slug=slug,
        name=name,
        description=payload.description,
        icon=payload.icon,
        sort_order=payload.sort_order,
        is_active=payload.is_active,
        created_at=now,
        updated_at=now,
    )
    session.add(category)
    await session.commit()
    return _to_out(category, 0)


async def update_category(
    session: AsyncSession, *, category_id: int, payload: AdminCategoryUpdateRequest
) -> AdminCategoryOut:
    category = await session.get(Category, category_id)
    if category is None:
        raise NotFoundError(message="分类不存在", details={"category_id": category_id})

    if payload.name is not None:
        name = payload.name.strip()
        conflict = (
            await session.execute(
                select(Category.id).where(Category.name == name, Category.id != category.id)
            )
        ).first()
        if conflict is not None:
            raise DuplicateEntryError(message=f"分类名已存在：{name}", details={"field": "name"})
        category.name = name
    if payload.slug is not None:
        slug = payload.slug.strip()
        conflict = (
            await session.execute(
                select(Category.id).where(Category.slug == slug, Category.id != category.id)
            )
        ).first()
        if conflict is not None:
            raise DuplicateEntryError(
                message=f"分类 slug 已存在：{slug}", details={"field": "slug"}
            )
        category.slug = slug
    if payload.description is not None:
        category.description = payload.description
    if payload.icon is not None:
        category.icon = payload.icon
    if payload.sort_order is not None:
        category.sort_order = payload.sort_order
    if payload.is_active is not None:
        category.is_active = payload.is_active
    category.updated_at = utcnow()
    await session.commit()

    counts = await _tool_counts(session)
    return _to_out(category, counts.get(category.id, 0))


async def delete_category(session: AsyncSession, *, category_id: int) -> dict:
    """删除分类 —— **软删除**，有引用时 409（FR-TAX-02）。"""
    category = await session.get(Category, category_id)
    if category is None:
        raise NotFoundError(message="分类不存在", details={"category_id": category_id})

    result = await session.execute(
        select(func.count())
        .select_from(Tool)
        .where(Tool.category_id == category_id, Tool.deleted_at.is_(None))
    )
    referenced = int(result.scalar_one())
    if referenced:
        raise CategoryInUseError(
            message=f"分类「{category.name}」仍被 {referenced} 个工具引用，不能删除",
            details={"category_id": category_id, "tool_count": referenced},
        )

    category.is_active = False
    category.updated_at = utcnow()
    await session.commit()
    logger.info("软删除分类 id=%s name=%s", category_id, category.name)
    return {"status": "ok", "soft_deleted": True}


async def reorder_categories(
    session: AsyncSession, *, items: list[CategoryOrderItem]
) -> list[AdminCategoryOut]:
    """批量调整排序（FR-ADMIN-04 的拖拽排序）。

    一次事务写完，不逐条提交。
    """
    ids = [item.id for item in items]
    result = await session.execute(select(Category).where(Category.id.in_(ids)))
    found = {c.id: c for c in result.scalars().all()}
    missing = [i for i in ids if i not in found]
    if missing:
        raise NotFoundError(
            message="部分分类不存在", details={"missing_ids": sorted(missing)}
        )
    now = utcnow()
    for item in items:
        found[item.id].sort_order = item.sort_order
        found[item.id].updated_at = now
    await session.commit()
    return await list_categories(session)


# ===========================================================================
# 标签
# ===========================================================================
async def list_tags(
    session: AsyncSession, *, q: str | None = None, limit: int = 500, offset: int = 0
) -> tuple[list[AdminTagOut], int]:
    stmt = (
        select(Tag)
        .order_by(Tag.usage_count.desc(), Tag.name.asc())
        .limit(limit)
        .offset(offset)
    )
    if q:
        stmt = stmt.where(Tag.name.startswith(tags_repo.normalize_tag_name(q)))
    rows = list((await session.execute(stmt)).scalars().all())
    count_stmt = select(func.count()).select_from(Tag)
    if q:
        count_stmt = count_stmt.where(Tag.name.startswith(tags_repo.normalize_tag_name(q)))
    total = int((await session.execute(count_stmt)).scalar_one())
    return (
        [
            AdminTagOut(
                id=t.id,
                name=t.name,
                display_name=t.display_name,
                usage_count=t.usage_count,
                created_at=t.created_at,
            )
            for t in rows
        ],
        total,
    )


async def rename_tag(
    session: AsyncSession, *, tag_id: int, display_name: str
) -> AdminTagOut:
    """重命名标签。

    `name`（归一化键）与 `display_name`（展示名）都要更新：
    只改展示名的话，归一化键会与实际显示长期不一致（FR-TAX-03）。
    """
    tag = await session.get(Tag, tag_id)
    if tag is None:
        raise NotFoundError(message="标签不存在", details={"tag_id": tag_id})

    new_display = display_name.strip()
    new_name = tags_repo.normalize_tag_name(new_display)
    if not new_name:
        raise ValidationError(
            message="标签名不能为空", fields=[{"field": "display_name", "message": "不能为空"}]
        )

    conflict = (
        await session.execute(select(Tag.id).where(Tag.name == new_name, Tag.id != tag_id))
    ).first()
    if conflict is not None:
        raise DuplicateEntryError(
            message=f"标签「{new_name}」已存在，请改用合并",
            details={"field": "display_name", "value": new_name},
        )

    tag.name = new_name
    tag.display_name = new_display
    tag.updated_at = utcnow()
    await session.commit()
    return AdminTagOut(
        id=tag.id,
        name=tag.name,
        display_name=tag.display_name,
        usage_count=tag.usage_count,
        created_at=tag.created_at,
    )


async def merge_tags(session: AsyncSession, *, payload: TagMergeRequest) -> TagMergeResponse:
    """合并标签（FR-TAX-05）：把 source 的引用转移到 target。

    需要一个**去重**步骤：如果某个工具同时打了 source 与 target，
    直接改 `tool_tags.tag_id` 会撞复合主键 `(tool_id, tag_id)`。
    所以先删掉会冲突的关联，再转移其余。
    """
    target = await session.get(Tag, payload.target_id)
    if target is None:
        raise NotFoundError(message="目标标签不存在", details={"tag_id": payload.target_id})
    if payload.target_id in payload.source_ids:
        raise ValidationError(
            message="目标标签不能同时出现在待合并列表里",
            details={"target_id": payload.target_id},
        )

    result = await session.execute(select(Tag).where(Tag.id.in_(payload.source_ids)))
    sources = list(result.scalars().all())
    missing = set(payload.source_ids) - {t.id for t in sources}
    if missing:
        raise NotFoundError(message="部分标签不存在", details={"missing_ids": sorted(missing)})

    source_ids = [t.id for t in sources]
    target_tool_ids = {
        int(r[0])
        for r in (
            await session.execute(
                select(ToolTag.tool_id).where(ToolTag.tag_id == target.id)
            )
        ).all()
    }

    # 1) 删掉「同时打了 source 与 target」的 source 关联（去重）
    dedup = 0
    if target_tool_ids:
        del_result = await session.execute(
            sa_delete(ToolTag).where(
                ToolTag.tag_id.in_(source_ids), ToolTag.tool_id.in_(target_tool_ids)
            )
        )
        dedup = int(del_result.rowcount or 0)

    # 2) 其余关联转移到 target
    from sqlalchemy import update as sa_update

    move_result = await session.execute(
        sa_update(ToolTag)
        .where(ToolTag.tag_id.in_(source_ids))
        .values(tag_id=target.id)
    )
    moved = int(move_result.rowcount or 0)

    # 3) 删掉 source 标签本身，并重算计数
    deleted_names = [t.display_name for t in sources]
    await session.execute(sa_delete(Tag).where(Tag.id.in_(source_ids)))
    await session.flush()
    await tags_repo.recompute_usage_counts(session)

    # 重新载入以拿到最新 usage_count
    await session.refresh(target)
    await session.commit()
    logger.info(
        "合并标签 target=%s sources=%s 转移=%s 去重=%s",
        target.name,
        source_ids,
        moved,
        dedup,
    )
    return TagMergeResponse(
        target_id=target.id,
        target_name=target.name,
        merged_tags=len(sources),
        moved_references=moved,
        deduplicated_references=dedup,
        deleted_tags=deleted_names,
    )


async def cleanup_tags(session: AsyncSession) -> TagCleanupResponse:
    """清理零引用标签（FR-TAX-06）。

    先重算 `usage_count`（它可能已经漂移），再删 `usage_count = 0` 且
    确实没有任何 `tool_tags` 关联的标签 —— 双重确认，避免误删。
    """
    await tags_repo.recompute_usage_counts(session)
    orphan_ids = select(ToolTag.tag_id).scalar_subquery()
    result = await session.execute(select(Tag).where(Tag.id.not_in(orphan_ids)))
    orphans = list(result.scalars().all())
    names = [t.display_name for t in orphans]
    if orphans:
        await session.execute(sa_delete(Tag).where(Tag.id.in_([t.id for t in orphans])))
    await session.commit()
    logger.info("清理零引用标签 %s 个: %s", len(names), names)
    return TagCleanupResponse(deleted=len(names), tags=names)


__all__ = [
    "cleanup_tags",
    "create_category",
    "delete_category",
    "list_categories",
    "list_tags",
    "merge_tags",
    "rename_tag",
    "reorder_categories",
    "slugify",
    "update_category",
]
