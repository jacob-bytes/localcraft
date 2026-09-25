"""工具版本仓储。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import VersionStatus
from app.models.tool import ToolVersion

#: 可下载的版本状态（FR-VER-07 / docs/02 §3.11：purged 不可下载）
DOWNLOADABLE_STATUSES: frozenset[str] = frozenset(
    {VersionStatus.APPROVED.value, VersionStatus.SUPERSEDED.value}
)


async def get_by_id(session: AsyncSession, version_id: int) -> ToolVersion | None:
    return await session.get(ToolVersion, version_id)


async def get_by_version(
    session: AsyncSession, tool_id: int, version: str
) -> ToolVersion | None:
    result = await session.execute(
        select(ToolVersion).where(
            ToolVersion.tool_id == tool_id, ToolVersion.version == version
        )
    )
    return result.scalar_one_or_none()


async def list_for_tool(
    session: AsyncSession, tool_id: int, *, order_desc: bool = True
) -> list[ToolVersion]:
    """工具的全部版本（含待审、已驳回、已归档）—— FR-VER-13 的时间线用。"""
    order = ToolVersion.created_at.desc() if order_desc else ToolVersion.created_at.asc()
    result = await session.execute(
        select(ToolVersion)
        .where(ToolVersion.tool_id == tool_id)
        .order_by(order, ToolVersion.id.desc())
    )
    # `.unique()` 是必须的：uploader 是 joined eager load，虽然是多对一
    # 不会产生笛卡尔积，但 SQLAlchemy 对 joined load 一律要求去重。
    return list(result.scalars().unique().all())


async def count_for_tool(session: AsyncSession, tool_id: int) -> int:
    result = await session.execute(
        select(func.count()).select_from(ToolVersion).where(ToolVersion.tool_id == tool_id)
    )
    return int(result.scalar_one())


async def count_superseded(session: AsyncSession, tool_id: int) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(ToolVersion)
        .where(
            ToolVersion.tool_id == tool_id,
            ToolVersion.status == VersionStatus.SUPERSEDED.value,
        )
    )
    return int(result.scalar_one())


async def list_purge_candidates(
    session: AsyncSession, tool_id: int, *, keep: int
) -> list[ToolVersion]:
    """超出保留份数的 `superseded` 版本，按 created_at **倒序**取第 keep+1 个之后。

    FR-VER-08：历史版本保留上限不含当前版本，超出后淘汰**最旧**的。
    只挑 `superseded` —— `purged` 的已经处理过，`rejected`/`pending` 不属于历史。
    """
    if keep < 0:
        keep = 0
    kept_ids = (
        select(ToolVersion.id)
        .where(
            ToolVersion.tool_id == tool_id,
            ToolVersion.status == VersionStatus.SUPERSEDED.value,
        )
        .order_by(ToolVersion.created_at.desc(), ToolVersion.id.desc())
        .limit(keep)
        .scalar_subquery()
    )
    result = await session.execute(
        select(ToolVersion)
        .where(
            ToolVersion.tool_id == tool_id,
            ToolVersion.status == VersionStatus.SUPERSEDED.value,
            ToolVersion.id.not_in(kept_ids),
        )
        .order_by(ToolVersion.created_at.asc(), ToolVersion.id.asc())
    )
    return list(result.scalars().all())


async def clear_current_flags(
    session: AsyncSession, tool_id: int, *, except_id: int | None = None
) -> int:
    """把该工具所有版本的 `is_current` 置 False。

    在设置新当前版本之前调用，保证「至多一个 is_current」的不变量。
    部分唯一索引 `uq_tool_versions_current` 是最后一道兜底，
    但代码不能依赖报错来发现不一致 —— 所以每次切换都显式清一遍。
    """
    stmt = (
        update(ToolVersion)
        .where(ToolVersion.tool_id == tool_id, ToolVersion.is_current.is_(True))
        .values(is_current=False)
    )
    if except_id is not None:
        stmt = stmt.where(ToolVersion.id != except_id)
    result = await session.execute(stmt)
    return int(result.rowcount or 0)


async def version_stats(session: AsyncSession, tool_id: int) -> tuple[int, int]:
    """返回 `(总版本数, 历史版本数)`，供详情页的 version_count / history_version_count。"""
    total = await count_for_tool(session, tool_id)
    history = await count_superseded(session, tool_id)
    return total, history


async def list_all_for_owner(
    session: AsyncSession, owner_id: int
) -> list[ToolVersion]:
    """某用户上传的全部版本（个人统计用）。"""
    from app.models.tool import Tool

    result = await session.execute(
        select(ToolVersion)
        .join(Tool, Tool.id == ToolVersion.tool_id)
        .where(Tool.owner_id == owner_id, Tool.deleted_at.is_(None))
    )
    return list(result.scalars().all())


async def sum_file_size_for_user(session: AsyncSession, owner_id: int) -> int:
    """用户已用存储：其名下工具的所有版本文件字节数之和（FR-FILE-12 配额）。"""
    from app.models.tool import Tool

    result = await session.execute(
        select(func.coalesce(func.sum(ToolVersion.file_size), 0))
        .select_from(ToolVersion)
        .join(Tool, Tool.id == ToolVersion.tool_id)
        .where(Tool.owner_id == owner_id)
    )
    return int(result.scalar_one())


async def sum_file_size_all(session: AsyncSession) -> int:
    """平台已用存储。"""
    from app.models.tool import Tool

    result = await session.execute(
        select(func.coalesce(func.sum(ToolVersion.file_size), 0))
        .select_from(ToolVersion)
        .join(Tool, Tool.id == ToolVersion.tool_id)
    )
    return int(result.scalar_one())


async def mark_purged(session: AsyncSession, version: ToolVersion, *, now: datetime) -> None:
    """标记为已归档 —— **保留数据库行**，只删磁盘文件。

    删行会打断 `approval_records` 与 `download_logs` 的外键引用，
    审计链就断了（docs/01 FR-VER-08）。
    """
    version.status = VersionStatus.PURGED.value
    version.purged_at = now
    version.is_current = False
    version.updated_at = now
    await session.flush()
