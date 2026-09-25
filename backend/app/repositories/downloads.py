"""下载明细与每日统计仓储（docs/02 §3.17 §3.18）。

写入策略（README「SQLite 风险说明」第 4 条 + 契约 §11 第 5 条）：

  - `download_logs` 走**内存队列批量插入**（每 100 条或每 10 秒），
    绝不逐请求 INSERT
  - `tools.download_count` / `view_count` 走内存计数器，定时批量 UPDATE
  - `tool_stats_daily` 用 **UPSERT**（`ON CONFLICT ... DO UPDATE`），
    SQLite 3.24+ 与 PG 9.5+ 语法一致，同一段 SQL 可复用
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.stats import DownloadLog, ToolStatsDaily
from app.models.tool import Tool


async def bulk_insert_logs(
    session: AsyncSession, rows: Sequence[dict], *, now: datetime
) -> int:
    """批量插入下载明细。空列表直接返回。"""
    if not rows:
        return 0
    payload = [{**row, "created_at": row.get("created_at", now)} for row in rows]
    # 直接走 Core insert（executemany 路径），比逐条 ORM add 快一个量级
    await session.execute(DownloadLog.__table__.insert(), payload)
    return len(payload)


async def apply_counters(
    session: AsyncSession, *, downloads: dict[int, int], views: dict[int, int]
) -> int:
    """把内存里累计的计数一次性加到 `tools` 上。

    每个工具一次 `UPDATE ... SET x = x + n`，在一次事务里批量提交。
    这仍然不是「逐请求 UPDATE」—— 调用频率由后台任务控制（默认 30 秒一次）。
    """
    touched = 0
    for tool_id, delta in downloads.items():
        if delta:
            await session.execute(
                update(Tool)
                .where(Tool.id == tool_id)
                .values(download_count=Tool.download_count + delta)
            )
            touched += 1
    for tool_id, delta in views.items():
        if delta:
            await session.execute(
                update(Tool).where(Tool.id == tool_id).values(view_count=Tool.view_count + delta)
            )
            touched += 1
    return touched


async def upsert_daily_stats(
    session: AsyncSession,
    *,
    stat_date: date,
    views: dict[int, int],
    downloads: dict[int, int],
) -> int:
    """按 `(tool_id, stat_date)` UPSERT 每日汇总。

    两个方言都支持 `ON CONFLICT ... DO UPDATE`，所以这段逻辑跨库一致
    （docs/02 §3.18）。这里用 SQLAlchemy 的 sqlite 方言 `insert`，
    切 PG 时只需换成 `postgresql.insert`，SQL 形状不变。
    """
    tool_ids = set(views) | set(downloads)
    if not tool_ids:
        return 0

    # 方言分支只影响「用哪个 insert 构造器」，UPSERT 的 SQL 形状两边一致
    is_pg = session.bind is not None and session.bind.dialect.name == "postgresql"
    insert_builder = postgresql_insert if is_pg else sqlite_insert

    written = 0
    for tool_id in tool_ids:
        stmt = insert_builder(ToolStatsDaily).values(
            tool_id=tool_id,
            stat_date=stat_date,
            views=views.get(tool_id, 0),
            downloads=downloads.get(tool_id, 0),
            unique_visitors=0,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[ToolStatsDaily.tool_id, ToolStatsDaily.stat_date],
            set_={
                "views": ToolStatsDaily.views + stmt.excluded.views,
                "downloads": ToolStatsDaily.downloads + stmt.excluded.downloads,
            },
        )
        await session.execute(stmt)
        written += 1
    return written


async def list_logs_for_user(
    session: AsyncSession, user_id: int, *, limit: int, offset: int
) -> list[tuple[DownloadLog, str | None, str | None, str | None]]:
    """我的下载历史 —— 左连工具与版本，一次查完（避免 N+1）。"""
    from app.models.tool import ToolVersion

    result = await session.execute(
        select(DownloadLog, Tool.slug, Tool.name, ToolVersion.version)
        .outerjoin(Tool, Tool.id == DownloadLog.tool_id)
        .outerjoin(ToolVersion, ToolVersion.id == DownloadLog.version_id)
        .where(DownloadLog.user_id == user_id)
        .order_by(DownloadLog.created_at.desc(), DownloadLog.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [(row[0], row[1], row[2], row[3]) for row in result.all()]


async def count_logs_for_user(session: AsyncSession, user_id: int) -> int:
    result = await session.execute(
        select(func.count()).select_from(DownloadLog).where(DownloadLog.user_id == user_id)
    )
    return int(result.scalar_one())


async def list_daily_for_tool(
    session: AsyncSession, tool_id: int, *, days: int = 30
) -> list[ToolStatsDaily]:
    result = await session.execute(
        select(ToolStatsDaily)
        .where(ToolStatsDaily.tool_id == tool_id)
        .order_by(ToolStatsDaily.stat_date.desc())
        .limit(days)
    )
    return list(result.scalars().all())


async def count_downloads_for_user(session: AsyncSession, user_id: int) -> int:
    result = await session.execute(
        select(func.count()).select_from(DownloadLog).where(DownloadLog.user_id == user_id)
    )
    return int(result.scalar_one())


async def delete_logs_before(session: AsyncSession, cutoff: datetime, *, batch: int = 1000) -> int:
    """按批次删除过期明细（docs/02 §5）：避免长事务锁库。"""
    subquery = (
        select(DownloadLog.id)
        .where(DownloadLog.created_at < cutoff)
        .limit(batch)
        .scalar_subquery()
    )
    result = await session.execute(
        DownloadLog.__table__.delete().where(DownloadLog.id.in_(subquery))
    )
    return int(result.rowcount or 0)
