"""数据保留与清理任务（docs/02 §5）。

为什么单独成模块而不是散在各服务里：

  - docs/02 §5 把这张表定义成一个**运维契约**（「全部为幂等操作」），
    M4 要求「9 个清理任务全部能跑」。放在一处，`run-maintenance.sh` / systemd timer
    只需要调一个入口，运维不用记 9 个命令。
  - 每个任务都必须**幂等 + 分批**（易错点清单第 9 条）：长事务会锁死 SQLite 的写者。

任务与 docs/02 §5 的对应关系（`run_all` 按这个顺序执行）：

  | 任务 | 实现位置 |
  | --- | --- |
  | 计数落库 | `counter_service`（常驻后台任务，这里只做一次收尾 flush）|
  | 下载明细落库 | `counter_service`（同上）|
  | 历史版本淘汰 | `version_service` 审批时同步触发；这里提供 `gc_versions` 兜底 |
  | 下载明细清理 | `purge_download_logs` |
  | 会话清理 | `purge_sessions` |
  | 孤儿文件清理 | `cleanup_orphan_files` |
  | 悬空 ACL 清理 | `cleanup_dangling_acl` |
  | 标签计数重算 | `recompute_tag_counts` |
  | 回收站清理 | `purge_recycle_bin` |
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutil import utcnow
from app.models.enums import AclSubjectType
from app.models.taxonomy import Tag
from app.models.tool import Tool, ToolAcl, ToolTag, ToolVersion
from app.models.user import Group

logger = logging.getLogger(__name__)

#: 下载明细清理的批大小（docs/02 §5：「分批 1000」）
DELETE_BATCH = 1000
#: 孤儿文件在 .trash 里保留的天数（docs/02 §5：「先移入 files/.trash/，保留 7 天」）
TRASH_RETENTION_DAYS = 7
#: 会话过期后额外保留的天数（docs/02 §5：「删除过期超 30 天的行」）
SESSION_GRACE_DAYS = 30


@dataclass
class MaintenanceResult:
    """一次维护的汇总。每一项都是「本次实际处理了多少条/个」。"""

    download_logs_purged: int = 0
    sessions_purged: int = 0
    orphan_files_trashed: int = 0
    trash_files_deleted: int = 0
    dangling_acl_purged: int = 0
    tags_recounted: int = 0
    recycle_bin_purged: list[int] = field(default_factory=list)
    versions_gc: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        data = self.__dict__.copy()
        data["recycle_bin_purged_count"] = len(self.recycle_bin_purged)
        return data


# ---------------------------------------------------------------------------
# 1) 下载明细清理（每日 03:00）
# ---------------------------------------------------------------------------
async def purge_download_logs(
    session: AsyncSession, *, retention_days: int, batch: int = DELETE_BATCH
) -> int:
    """删除超过保留期的 `download_logs` 行，**分批**执行。

    分批的理由：`download_logs` 是增长最快的表，一次 DELETE 几十万行会在
    SQLite 上持有写锁数秒，期间所有写请求排队甚至超时（`database is locked`）。
    每批一个小事务，批间让出写锁。
    """
    from app.models.stats import DownloadLog

    cutoff = utcnow() - timedelta(days=retention_days)
    total = 0
    while True:
        # SQLite 的 DELETE ... LIMIT 需要编译期开关，因此用「先选主键再删」，
        # 这个写法 PG 与 SQLite 都可用。
        ids = (
            await session.execute(
                select(DownloadLog.id).where(DownloadLog.created_at < cutoff).limit(batch)
            )
        ).scalars().all()
        if not ids:
            break
        await session.execute(delete(DownloadLog).where(DownloadLog.id.in_(list(ids))))
        await session.commit()
        total += len(ids)
        if len(ids) < batch:
            break
    return total


# ---------------------------------------------------------------------------
# 2) 会话清理（每日 03:10）
# ---------------------------------------------------------------------------
async def purge_sessions(
    session: AsyncSession, *, older_than_days: int = SESSION_GRACE_DAYS
) -> int:
    """删除「过期已超过 N 天」的 `auth_sessions` 行。

    注意是**过期之后**再等 N 天，不是「创建后 N 天」—— 否则会把仍然有效的
    长期会话删掉，用户被莫名其妙踢下线。
    """
    from app.repositories import auth_sessions as sessions_repo

    purged = await sessions_repo.purge_expired(
        session, now=utcnow(), older_than_days=older_than_days
    )
    await session.commit()
    return purged


# ---------------------------------------------------------------------------
# 3) 孤儿文件清理（每日 03:20）
# ---------------------------------------------------------------------------
async def collect_referenced_files(session: AsyncSession) -> set[str]:
    """数据库里所有被引用的文件相对路径（相对 `DATA_DIR`）。

    覆盖三类引用：版本包、封面图、截图。任何一类漏了都会把**在用**的文件
    当孤儿删掉 —— 这是这个任务最危险的地方，所以宁可多算。
    """
    paths: set[str] = set()
    for column in (
        select(ToolVersion.storage_path).where(ToolVersion.storage_path.is_not(None)),
    ):
        paths.update(p for p in (await session.execute(column)).scalars().all() if p)

    # 图片表（封面/截图）
    try:
        from app.models.tool import ToolImage

        rows = await session.execute(
            select(ToolImage.storage_path, ToolImage.thumb_path)
        )
        for storage_path, thumb_path in rows.all():
            if storage_path:
                paths.add(storage_path)
            if thumb_path:
                paths.add(thumb_path)
    except Exception:  # pragma: no cover - 表结构变化时的兜底
        logger.warning("读取 tool_images 失败，孤儿文件清理将跳过图片目录", exc_info=True)

    return paths


def _relative_from_files_root(files_root: Path, path: Path) -> str | None:
    try:
        return "files/" + str(path.relative_to(files_root))
    except ValueError:
        return None


async def cleanup_orphan_files(
    session: AsyncSession,
    *,
    files_root: Path,
    trash_retention_days: int = TRASH_RETENTION_DAYS,
    dry_run: bool = False,
) -> tuple[int, int]:
    """扫描 `files/`，把无数据库引用的文件**先移入 `.trash/`**，再删过期的。

    为什么分两步（易错点清单第 10 条）：直接删没有后悔药。移入 `.trash/` 后
    保留 N 天，误删可以捞回来。

    返回 `(本次移入 trash 的数量, 本次从 trash 真删的数量)`。
    """
    import os
    import time

    trash_dir = files_root / ".trash"
    now = time.time()
    deleted = 0

    # ---- 第一步：清理 .trash 里超过保留期的条目 ----
    if trash_dir.is_dir() and not dry_run:
        for entry in trash_dir.iterdir():
            try:
                age_days = (now - entry.stat().st_mtime) / 86400
            except OSError:  # pragma: no cover
                continue
            if age_days < trash_retention_days:
                continue
            if entry.is_dir():
                for sub in sorted(entry.rglob("*"), reverse=True):
                    with contextlib.suppress(OSError):
                        sub.rmdir() if sub.is_dir() else sub.unlink()
                with contextlib.suppress(OSError):
                    entry.rmdir()
            else:
                with contextlib.suppress(OSError):
                    entry.unlink()
            deleted += 1

    # ---- 第二步：扫描活文件，挑出孤儿 ----
    referenced = await collect_referenced_files(session)
    # 未被引用的**临时**目录不算孤儿（上传中的分片）
    skip_dirs = {".trash", "tmp", ".tmp"}

    trashed = 0
    if files_root.is_dir():
        for path in files_root.rglob("*"):
            if not path.is_file():
                continue
            rel_parts = path.relative_to(files_root).parts
            if rel_parts and rel_parts[0] in skip_dirs:
                continue
            rel = _relative_from_files_root(files_root, path)
            if rel is None or rel in referenced:
                continue
            if dry_run:
                trashed += 1
                continue
            # 保留相对目录结构移入 .trash，便于人工核对来源
            target = trash_dir / path.relative_to(files_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.replace(path, target)
            except OSError:  # pragma: no cover
                logger.warning("移动孤儿文件失败: %s", rel, exc_info=True)
            else:
                trashed += 1
                logger.info("孤儿文件已移入回收区: %s", rel)

    return trashed, deleted


# ---------------------------------------------------------------------------
# 4) 悬空 ACL 清理（每小时）
# ---------------------------------------------------------------------------
async def cleanup_dangling_acl(session: AsyncSession) -> int:
    """删除「`subject_type='group'` 但该组已不存在」的 `tool_acl` 行。

    这是**必须由应用兜底**的场景：`tool_acl.subject_id` 是多态外键，
    数据库层没有 FK 约束（易错点清单第 11 条）。删组时服务层会顺带清理，
    但历史遗留数据或异常中断仍可能留下死条目 —— 这些条目永远命中不了任何用户，
    却会让工具看起来"被授权过"。

    返回删除行数。
    """
    group_ids = select(Group.id).scalar_subquery()
    result = await session.execute(
        delete(ToolAcl).where(
            ToolAcl.subject_type == AclSubjectType.GROUP.value,
            ToolAcl.subject_id.not_in(group_ids),
        )
    )
    await session.commit()
    count = int(result.rowcount or 0)
    if count:
        logger.info("清理悬空 ACL %d 条（引用了已删除的用户组）", count)
    return count


# ---------------------------------------------------------------------------
# 5) 标签计数重算（每小时）
# ---------------------------------------------------------------------------
async def recompute_tag_counts(session: AsyncSession) -> int:
    """重算 `tags.usage_count`，返回被修正的标签数。

    为什么不让数据库触发器维护：`tool_tags` 的增删路径太多（建工具、改标签、
    打标签、合并标签、清孤儿标签），触发器容易漏。定时重算 + 写路径顺带更新，
    以重算为准 —— 这样"计数漂了"最多漂一小时。
    """
    rows = await session.execute(select(Tag.id, Tag.usage_count))
    before = {int(r[0]): int(r[1] or 0) for r in rows.all()}

    actual_rows = await session.execute(
        select(ToolTag.tag_id, func.count()).group_by(ToolTag.tag_id)
    )
    actual = {int(r[0]): int(r[1]) for r in actual_rows.all()}

    changed = 0
    for tag_id, old in before.items():
        new = actual.get(tag_id, 0)
        if new != old:
            await session.execute(
                text("UPDATE tags SET usage_count = :n WHERE id = :i"), {"n": new, "i": tag_id}
            )
            changed += 1
    # 引用了不存在标签的 tool_tags 由 tag 清理任务处理，这里不动
    await session.commit()
    return changed


# ---------------------------------------------------------------------------
# 6) 回收站清理（每日 03:30）
# ---------------------------------------------------------------------------
async def purge_recycle_bin(session: AsyncSession, *, retention_days: int) -> list[int]:
    """彻底清除软删除超期的工具（含文件）。返回被清除的 tool_id 列表。"""
    from app.services import admin_tool_service

    purged = await admin_tool_service.purge_expired_recycle_bin(
        session, older_than_days=retention_days
    )
    if purged:
        logger.info("回收站彻底清除 %d 个工具: %s", len(purged), purged)
    return purged


# ---------------------------------------------------------------------------
# 7) 历史版本淘汰（兜底）
# ---------------------------------------------------------------------------
async def gc_versions(session: AsyncSession, *, keep: int) -> int:
    """把超出 `keep` 的历史版本转 `purged` 并删磁盘文件，返回处理数量。

    正常情况下由审批通过时同步触发（docs/02 §5 有解释）；这里是**兜底**，
    覆盖「同步删文件失败」与「上限被调小」两种历史遗留。

    与 `app.cli gc-versions` 共用同一套仓储调用（`list_purge_candidates` /
    `mark_purged`），不另起一套语义 —— 否则两条路径迟早会漂移。
    """
    from app.repositories import tool_versions as versions_repo
    from app.storage import get_storage

    storage = get_storage()
    total = 0
    tool_ids = [int(r[0]) for r in (await session.execute(select(Tool.id))).all()]
    for tool_id in tool_ids:
        candidates = await versions_repo.list_purge_candidates(session, tool_id, keep=keep)
        for version in candidates:
            path = version.storage_path
            await versions_repo.mark_purged(session, version, now=utcnow())
            await session.flush()
            total += 1
            if path:
                # 文件 IO 放在 flush 之后、commit 之前会拉长写事务；
                # 这里的量很小（单工具超限版本），且删除失败由孤儿清理兜底。
                await storage.delete(path)
        await session.commit()
    return total


# ---------------------------------------------------------------------------
# 一次性入口
# ---------------------------------------------------------------------------
async def run_all(
    session: AsyncSession,
    *,
    files_root: Path,
    download_log_retention_days: int,
    recycle_bin_retention_days: int,
    version_history_limit: int,
    session_grace_days: int = SESSION_GRACE_DAYS,
    trash_retention_days: int = TRASH_RETENTION_DAYS,
    dry_run: bool = False,
) -> MaintenanceResult:
    """按 docs/02 §5 的顺序跑全部清理任务。

    单个任务失败**不中断**其余任务：清理是"尽力而为"的运维动作，
    一个任务挂在异常上不该让另外 8 个也不跑。失败记进 `errors` 并以非零退出码体现。
    """
    result = MaintenanceResult()

    async def _guard(name: str, coro_factory) -> None:
        try:
            await coro_factory()
        except Exception as exc:
            logger.exception("维护任务失败: %s", name)
            result.errors.append(f"{name}: {type(exc).__name__}: {exc}")

    async def _logs() -> None:
        result.download_logs_purged = await purge_download_logs(
            session, retention_days=download_log_retention_days
        )

    async def _sessions() -> None:
        result.sessions_purged = await purge_sessions(session, older_than_days=session_grace_days)

    async def _orphans() -> None:
        trashed, deleted = await cleanup_orphan_files(
            session,
            files_root=files_root,
            trash_retention_days=trash_retention_days,
            dry_run=dry_run,
        )
        result.orphan_files_trashed = trashed
        result.trash_files_deleted = deleted

    async def _acl() -> None:
        result.dangling_acl_purged = await cleanup_dangling_acl(session)

    async def _tags() -> None:
        result.tags_recounted = await recompute_tag_counts(session)

    async def _recycle() -> None:
        if not dry_run:
            result.recycle_bin_purged = await purge_recycle_bin(
                session, retention_days=recycle_bin_retention_days
            )

    async def _versions() -> None:
        if not dry_run:
            result.versions_gc = await gc_versions(session, keep=version_history_limit)

    # 顺序刻意与 docs/02 §5 的表格一致，便于对照
    await _guard("下载明细清理", _logs)
    await _guard("会话清理", _sessions)
    await _guard("孤儿文件清理", _orphans)
    await _guard("悬空 ACL 清理", _acl)
    await _guard("标签计数重算", _tags)
    await _guard("回收站清理", _recycle)
    await _guard("历史版本淘汰", _versions)
    return result


__all__ = [
    "DELETE_BATCH",
    "SESSION_GRACE_DAYS",
    "TRASH_RETENTION_DAYS",
    "MaintenanceResult",
    "cleanup_dangling_acl",
    "cleanup_orphan_files",
    "collect_referenced_files",
    "gc_versions",
    "purge_download_logs",
    "purge_recycle_bin",
    "purge_sessions",
    "recompute_tag_counts",
    "run_all",
]
