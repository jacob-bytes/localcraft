"""统计与概览（FR-ADMIN-14 / FR-STAT-*）。

**纯数字，不做图表**（docs/01 第 10 章第 2 条明确不做可视化大屏）。

读取侧的聚合查询都在这里；计数的**写入**侧（内存聚合 + 定时落库）
在 `app/services/counter_service.py`，M2 已完成，本轮不加写入逻辑。
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutil import utcnow
from app.models.enums import ToolStatus, UserStatus
from app.models.setting import SystemSetting
from app.models.stats import DownloadLog
from app.models.taxonomy import Category, Tag
from app.models.tool import Tool, ToolVersion
from app.models.user import ApiToken, Group, User
from app.repositories import api_tokens as tokens_repo
from app.repositories import system_settings as settings_repo
from app.schemas.admin import (
    AdminOverviewResponse,
    StatusCount,
    StorageOwnerItem,
    StorageStatsResponse,
    ToolRankItem,
    ToolRankResponse,
)
from app.services import settings_service
from app.storage import get_storage

logger = logging.getLogger(__name__)

#: 状态展示顺序（概览页按这个顺序列出，保证前端渲染稳定）
STATUS_ORDER: tuple[str, ...] = (
    ToolStatus.DRAFT.value,
    ToolStatus.PENDING.value,
    ToolStatus.APPROVED.value,
    ToolStatus.REJECTED.value,
    ToolStatus.PENDING_UPDATE.value,
    ToolStatus.OFFLINE.value,
)


async def overview(session: AsyncSession) -> AdminOverviewResponse:
    """`GET /admin/overview` —— 一次请求把概览页要的数字全给出来。"""
    now = utcnow()

    user_counts = dict(
        (
            await session.execute(
                select(User.status, func.count(User.id)).group_by(User.status)
            )
        ).all()
    )
    status_rows = dict(
        (
            await session.execute(
                select(Tool.status, func.count(Tool.id))
                .where(Tool.deleted_at.is_(None))
                .group_by(Tool.status)
            )
        ).all()
    )
    tool_total = sum(int(v) for v in status_rows.values())

    download_total = int(
        (
            await session.execute(
                select(func.coalesce(func.sum(Tool.download_count), 0)).where(
                    Tool.deleted_at.is_(None)
                )
            )
        ).scalar_one()
    )
    view_total = int(
        (
            await session.execute(
                select(func.coalesce(func.sum(Tool.view_count), 0)).where(
                    Tool.deleted_at.is_(None)
                )
            )
        ).scalar_one()
    )
    used_bytes = int(
        (
            await session.execute(
                select(func.coalesce(func.sum(ToolVersion.file_size), 0))
            )
        ).scalar_one()
    )
    # 「近 7 日下载量」取自 download_logs（明细表），不是 tools.download_count
    # （那是累计值）。两者口径不同，概览页需要的是近期活跃度。
    since = now - timedelta(days=7)
    last7 = int(
        (
            await session.execute(
                select(func.count()).select_from(DownloadLog).where(DownloadLog.created_at >= since)
            )
        ).scalar_one()
    )
    quota_mb = int(
        (
            await session.execute(
                select(SystemSetting.value).where(SystemSetting.key == "quota.total_mb")
            )
        ).scalar_one_or_none()
        or 51200
    )
    quota_bytes = quota_mb * 1024 * 1024
    # 告警阈值与判断口径统一走 settings_service（契约 §18.6：只写一处）
    warn_pct = await settings_repo.get_effective_int(
        session,
        "quota.warn_threshold_pct",
        settings_service.STORAGE_WARNING_DEFAULT_PCT,
    )
    recycle_count = int(
        (
            await session.execute(
                select(func.count()).select_from(Tool).where(Tool.deleted_at.is_not(None))
            )
        ).scalar_one()
    )

    return AdminOverviewResponse(
        user_count=sum(int(v) for v in user_counts.values()),
        active_user_count=int(user_counts.get(UserStatus.ACTIVE.value, 0)),
        disabled_user_count=int(user_counts.get(UserStatus.DISABLED.value, 0)),
        tool_count=tool_total,
        tools_by_status=[
            StatusCount(status=s, count=int(status_rows.get(s, 0))) for s in STATUS_ORDER
        ],
        pending_count=int(status_rows.get(ToolStatus.PENDING.value, 0))
        + int(status_rows.get(ToolStatus.PENDING_UPDATE.value, 0)),
        category_count=int(
            (await session.execute(select(func.count()).select_from(Category))).scalar_one()
        ),
        tag_count=int(
            (await session.execute(select(func.count()).select_from(Tag))).scalar_one()
        ),
        group_count=int(
            (await session.execute(select(func.count()).select_from(Group))).scalar_one()
        ),
        active_token_count=await tokens_repo.count_active(session, now=now),
        download_count=download_total,
        view_count=view_total,
        used_bytes=used_bytes,
        quota_bytes=quota_bytes,
        used_percent=round(used_bytes / quota_bytes * 100, 2) if quota_bytes else 0.0,
        storage_warning=settings_service.storage_warning(used_bytes, quota_bytes, warn_pct),
        storage_warning_threshold_pct=warn_pct,
        downloads_last_7_days=last7,
        recycle_bin_count=recycle_count,
    )


async def tool_ranking(
    session: AsyncSession, *, limit: int = 20, include_deleted: bool = False
) -> ToolRankResponse:
    """下载量 Top N（FR-ADMIN-14 的「工具排行」）。"""
    stmt = (
        select(Tool, User)
        .outerjoin(User, User.id == Tool.owner_id)
        .order_by(Tool.download_count.desc(), Tool.id.asc())
        .limit(limit)
    )
    if not include_deleted:
        stmt = stmt.where(Tool.deleted_at.is_(None))
    rows = (await session.execute(stmt)).all()
    total = int(
        (
            await session.execute(select(func.count()).select_from(Tool))
        ).scalar_one()
    )
    return ToolRankResponse(
        items=[
            ToolRankItem(
                tool_id=tool.id,
                slug=tool.slug,
                name=tool.name,
                tool_type=tool.tool_type,
                status=tool.status,
                owner_name=owner.display_name if owner is not None else None,
                download_count=tool.download_count,
                view_count=tool.view_count,
            )
            for tool, owner in rows
        ],
        total=total,
    )


async def storage_stats(
    session: AsyncSession, *, limit: int = 20, offset: int = 0
) -> StorageStatsResponse:
    """存储占用明细（按 owner 聚合）。"""
    usage = (
        select(
            Tool.owner_id.label("owner_id"),
            func.coalesce(func.sum(ToolVersion.file_size), 0).label("used_bytes"),
            func.count(func.distinct(Tool.id)).label("tool_count"),
            func.count(ToolVersion.id).label("version_count"),
        )
        .select_from(Tool)
        .join(ToolVersion, ToolVersion.tool_id == Tool.id)
        .where(Tool.deleted_at.is_(None))
        .group_by(Tool.owner_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(User, usage.c.used_bytes, usage.c.tool_count, usage.c.version_count)
            .join(usage, usage.c.owner_id == User.id)
            .order_by(usage.c.used_bytes.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()

    total_used = int(
        (
            await session.execute(select(func.coalesce(func.sum(ToolVersion.file_size), 0)))
        ).scalar_one()
    )
    file_count = int(
        (await session.execute(select(func.count()).select_from(ToolVersion))).scalar_one()
    )
    quota_mb = int(
        (
            await session.execute(
                select(SystemSetting.value).where(SystemSetting.key == "quota.total_mb")
            )
        ).scalar_one_or_none()
        or 51200
    )
    quota_bytes = quota_mb * 1024 * 1024
    per_user_mb = int(
        (
            await session.execute(
                select(SystemSetting.value).where(SystemSetting.key == "quota.per_user_mb")
            )
        ).scalar_one_or_none()
        or 2048
    )
    per_user_bytes = per_user_mb * 1024 * 1024

    total_owners = int(
        (
            await session.execute(select(func.count(func.distinct(Tool.owner_id))))
        ).scalar_one()
    )

    # J-4（contracts/CONTRACT.md §20.4）：接上孤儿文件计数。
    # 该字段一直声明在 StorageStatsResponse 里但从不赋值，恒为 0 ——
    # 管理员因此永远看不到「磁盘上有多少文件是数据库不认识的」。
    # 复用清理任务的 dry_run 模式（它本来就会扫描并计数，只是不移动文件），
    # 避免为统计再写一遍扫描逻辑、两处漂移。
    orphan_count = 0
    try:
        from app.services import maintenance_service

        orphan_count, _ = await maintenance_service.cleanup_orphan_files(
            session, files_root=get_storage().files_root, dry_run=True
        )
    except Exception:
        logger.warning("统计孤儿文件数失败，回退为 0", exc_info=True)

    return StorageStatsResponse(
        total_used_bytes=total_used,
        total_quota_bytes=quota_bytes,
        used_percent=round(total_used / quota_bytes * 100, 2) if quota_bytes else 0.0,
        file_count=file_count,
        items=[
            StorageOwnerItem(
                owner_id=user.id,
                username=user.username,
                display_name=user.display_name,
                tool_count=int(tool_count or 0),
                version_count=int(version_count or 0),
                used_bytes=int(used or 0),
                quota_bytes=per_user_bytes,
                used_percent=(
                    round(int(used or 0) / per_user_bytes * 100, 2) if per_user_bytes else 0.0
                ),
            )
            for user, used, tool_count, version_count in rows
        ],
        total=total_owners,
        page=offset // limit + 1 if limit else 1,
        page_size=limit,
        orphan_file_count=orphan_count,
    )


__all__ = [
    "STATUS_ORDER",
    "ApiToken",
    "overview",
    "storage_stats",
    "tool_ranking",
]
