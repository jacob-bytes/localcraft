"""统计与概览（FR-ADMIN-14 / FR-STAT-*）。

**纯数字，不做图表**（docs/01 第 10 章第 2 条明确不做可视化大屏）。

读取侧的聚合查询都在这里；计数的**写入**侧（内存聚合 + 定时落库）
在 `app/services/counter_service.py`，M2 已完成，本轮不加写入逻辑。
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import Date, cast, func, select
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
    AdminInsightsResponse,
    AdminOverviewResponse,
    CategoryInsightItem,
    DailyDownloadPoint,
    SavingsInsight,
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

#: M8（contracts §23.6）：`downloads_daily` **恰好 30 条**，缺口补 0。
INSIGHTS_WINDOW_DAYS = 30

#: `category_id IS NULL` 的工具在 `tools_by_category` 里的展示名
#: （契约 §23.6 的形状要求 `name` 是字符串）。
UNCATEGORIZED_LABEL = "未分类"


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


# ===========================================================================
# M8：`GET /admin/stats/insights`（contracts §23.6，形状冻结）
# ===========================================================================
def _day_bucket(session: AsyncSession):
    """把 `download_logs.created_at` 按 **UTC 日历日** 截断的跨方言表达式。

    `func.date(col)` 只有 SQLite 认识；PostgreSQL 里 `date(x)` 不是函数，
    必须 `CAST(... AS date)`。`downloads.py` 的 UPSERT 已有同类方言分支先例。

    PG 侧先把 `timestamptz` 转成 UTC 的 wall time 再取 date
    （`timezone('UTC', ts)`），否则结果会随数据库会话的 `TimeZone` 设置漂移 ——
    而全库约定是 UTC 存储（docs/02 §1.1）。
    """
    bind = session.bind
    is_pg = bind is not None and getattr(bind.dialect, "name", "") == "postgresql"
    if is_pg:
        return cast(func.timezone("UTC", DownloadLog.created_at), Date)
    return func.date(DownloadLog.created_at)


def _normalize_day(value: object) -> str:
    """SQLite 返回 `'YYYY-MM-DD'` 字符串，PG 返回 `datetime.date` —— 归一成字符串。"""
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


async def insights(session: AsyncSession) -> AdminInsightsResponse:
    """`GET /admin/stats/insights`（契约 §23.6）。

    与 `/admin/overview` 的关系：**独立端点**。契约 §23.4 说明了理由 ——
    insights 含 30 天序列与去重人数聚合，明显更重；独立端点让管理台首页先渲染
    轻量数字，重聚合可以后到/懒加载。因此本函数**不参与** `/admin/overview`
    的任何代码路径，不会拖慢它。

    **`downloads_daily` 的数据源选择**：用 `download_logs`（明细表）而不是
    `tool_stats_daily`（每日汇总）。理由：

      1. `tool_stats_daily` 只在**后台计数任务 flush 过之后**才有数据；
         全新部署、刚重启、或 flush 失败时它是空的，而 `download_logs`
         与 `tools.download_count` 是同一批 flush 写入的明细，能对上。
      2. `/admin/overview` 的 `downloads_last_7_days` 已经用 `download_logs` ——
         同口径才能让两个端点上的「下载量」互相解释。
      3. 这样能保证 `downloads_last_30_days == sum(downloads_daily)` 恒成立。
    """
    now = utcnow()
    today = now.date()
    start_day = today - timedelta(days=INSIGHTS_WINDOW_DAYS - 1)
    since = datetime.combine(start_day, time.min, tzinfo=UTC)

    # ---- 30 天每日下载序列 ----
    day_bucket = _day_bucket(session)
    day_rows = (
        await session.execute(
            select(day_bucket.label("day"), func.count().label("downloads"))
            .select_from(DownloadLog)
            .where(DownloadLog.created_at >= since)
            .group_by(day_bucket)
        )
    ).all()
    counts: dict[str, int] = {}
    for day_value, downloads in day_rows:
        key = _normalize_day(day_value)
        counts[key] = counts.get(key, 0) + int(downloads)

    downloads_daily: list[DailyDownloadPoint] = []
    downloads_last_30_days = 0
    for offset in range(INSIGHTS_WINDOW_DAYS):
        day = start_day + timedelta(days=offset)
        value = counts.get(day.isoformat(), 0)  # 缺口补 0
        downloads_last_30_days += value
        downloads_daily.append(DailyDownloadPoint(date=day, downloads=value))

    # ---- 30 天内上传/更新过工具的去重用户数 ----
    active_contributors = int(
        (
            await session.execute(
                select(func.count(func.distinct(ToolVersion.uploaded_by_id))).where(
                    ToolVersion.created_at >= since,
                    ToolVersion.uploaded_by_id.is_not(None),
                )
            )
        ).scalar_one()
    )

    # ---- 按分类的工具数 / 下载量 ----
    # 只统计**未软删除**的工具，与 `/admin/overview.tool_count` 同口径。
    category_rows = (
        await session.execute(
            select(
                Tool.category_id,
                func.count(Tool.id),
                func.coalesce(func.sum(Tool.download_count), 0),
            )
            .where(Tool.deleted_at.is_(None))
            .group_by(Tool.category_id)
        )
    ).all()
    category_meta: dict[int, tuple[str, int]] = {
        int(row[0]): (str(row[1]), int(row[2] or 0))
        for row in (
            await session.execute(
                select(Category.id, Category.name, Category.sort_order)
            )
        ).all()
    }

    ordered: list[tuple[tuple[int, int, int], CategoryInsightItem]] = []
    for category_id, tool_count, download_count in category_rows:
        if category_id is None:
            # 未分类排最后（分类内按 sort_order，再按 id 保证确定性）
            sort_key = (1, 0, 0)
            name = UNCATEGORIZED_LABEL
        else:
            cid = int(category_id)
            meta = category_meta.get(cid)
            sort_key = (0, meta[1] if meta else 0, cid)
            name = meta[0] if meta else f"#{cid}"
        ordered.append(
            (
                sort_key,
                CategoryInsightItem(
                    category_id=int(category_id) if category_id is not None else None,
                    name=name,
                    tool_count=int(tool_count),
                    download_count=int(download_count or 0),
                ),
            )
        )
    ordered.sort(key=lambda pair: pair[0])
    tools_by_category = [item for _key, item in ordered]

    # ---- 效率估算（公式冻结，见 contracts §23.6）----
    #
    # total_minutes = Σ_over_tools ( estimated_saving_minutes
    #                                × COUNT(DISTINCT download_logs.user_id) )
    #   仅计入 estimated_saving_minutes IS NOT NULL 且 user_id IS NOT NULL
    #
    # 「去重受益人数」用 COUNT(DISTINCT user_id) 而不是原始下载量 ——
    # 原始量会把「同一人重复下载 / 版本升级」重复计数，使估算进一步虚高
    # （契约 §23.1 ②）。
    per_tool_users = (
        select(
            DownloadLog.tool_id.label("tool_id"),
            func.count(func.distinct(DownloadLog.user_id)).label("distinct_users"),
        )
        .where(DownloadLog.user_id.is_not(None))
        .group_by(DownloadLog.tool_id)
        .subquery()
    )
    total_minutes = int(
        (
            await session.execute(
                select(
                    func.coalesce(
                        func.sum(
                            per_tool_users.c.distinct_users * Tool.estimated_saving_minutes
                        ),
                        0,
                    )
                )
                .select_from(Tool)
                .join(per_tool_users, per_tool_users.c.tool_id == Tool.id)
                .where(
                    Tool.deleted_at.is_(None),
                    Tool.estimated_saving_minutes.is_not(None),
                )
            )
        ).scalar_one()
    )
    # 覆盖率的分母/分子必须同口径（都排除软删除），否则比率会失真。
    covered_tool_count = int(
        (
            await session.execute(
                select(func.count())
                .select_from(Tool)
                .where(
                    Tool.deleted_at.is_(None),
                    Tool.estimated_saving_minutes.is_not(None),
                )
            )
        ).scalar_one()
    )
    total_tool_count = int(
        (
            await session.execute(
                select(func.count()).select_from(Tool).where(Tool.deleted_at.is_(None))
            )
        ).scalar_one()
    )

    return AdminInsightsResponse(
        downloads_last_30_days=downloads_last_30_days,
        downloads_daily=downloads_daily,
        active_contributors_30d=active_contributors,
        tools_by_category=tools_by_category,
        savings=SavingsInsight(
            total_minutes=total_minutes,
            covered_tool_count=covered_tool_count,
            total_tool_count=total_tool_count,
            # 常量口径声明，**不可省**（契约 §23.6）。
            basis="author_estimate",
        ),
    )


__all__ = [
    "INSIGHTS_WINDOW_DAYS",
    "STATUS_ORDER",
    "UNCATEGORIZED_LABEL",
    "ApiToken",
    "insights",
    "overview",
    "storage_stats",
    "tool_ranking",
]
