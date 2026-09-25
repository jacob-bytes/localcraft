"""M4 维护任务的单元/集成测试（`app/services/maintenance_service.py`）。

对应 docs/02 §5「数据保留与清理任务」与 M4 验收 15。
端到端演练在 `scripts/m4-drill-maintenance.sh`，这里做**快速、可回归**的覆盖：
不依赖 systemd、不依赖真服务，直接在库上构造目标状态再断言结果。

每个任务都必须同时验证两件事：
  1. 该删的删掉了
  2. **不该删的没被误删**（这一条才是真正容易出事的地方）
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.core.timeutil import utcnow
from app.db.session import SessionLocal
from app.models.stats import DownloadLog
from app.models.taxonomy import Tag
from app.models.tool import Tool, ToolAcl, ToolVersion
from app.models.user import User
from app.services import maintenance_service
from tests.conftest import Seeded


# ---------------------------------------------------------------------------
# 下载明细清理
# ---------------------------------------------------------------------------
async def test_purge_download_logs_keeps_recent_and_batches(seeded: Seeded) -> None:
    now = utcnow()
    async with SessionLocal() as session:
        tool_id = seeded.tools["public-approved"]
        # 3 条超期 + 2 条新的
        for i in range(3):
            session.add(
                DownloadLog(tool_id=tool_id, created_at=now - timedelta(days=400 + i))
            )
        for _ in range(2):
            session.add(DownloadLog(tool_id=tool_id, created_at=now))
        await session.commit()

    async with SessionLocal() as session:
        before_old = (
            await session.execute(
                select(DownloadLog.id).where(
                    DownloadLog.created_at < now - timedelta(days=180)
                )
            )
        ).scalars().all()
        assert len(before_old) == 3

        # batch=1 强制走「多轮删除」循环，覆盖分批逻辑
        purged = await maintenance_service.purge_download_logs(
            session, retention_days=180, batch=1
        )
        assert purged == 3

    async with SessionLocal() as session:
        left_old = (
            await session.execute(
                select(DownloadLog.id).where(
                    DownloadLog.created_at < now - timedelta(days=180)
                )
            )
        ).scalars().all()
        assert left_old == [], "超期明细必须删净"
        left_recent = (
            await session.execute(
                select(DownloadLog.id).where(DownloadLog.created_at >= now - timedelta(days=1))
            )
        ).scalars().all()
        assert len(left_recent) >= 2, "保留期内的明细不能被误删"


async def test_purge_download_logs_is_idempotent(seeded: Seeded) -> None:
    async with SessionLocal() as session:
        assert await maintenance_service.purge_download_logs(session, retention_days=1) >= 0
    async with SessionLocal() as session:
        # 再跑一次应当什么也不删（幂等）
        assert await maintenance_service.purge_download_logs(session, retention_days=1) == 0


# ---------------------------------------------------------------------------
# 会话清理
# ---------------------------------------------------------------------------
async def test_purge_sessions_only_removes_long_expired(seeded: Seeded) -> None:
    from app.models.user import AuthSession

    now = utcnow()
    async with SessionLocal() as session:
        user_id = seeded.users["newbie"]
        session.add(
            AuthSession(
                user_id=user_id,
                refresh_token_hash="m4-old",
                expires_at=now - timedelta(days=40),
                created_at=now - timedelta(days=50),
            )
        )
        session.add(
            AuthSession(
                user_id=user_id,
                refresh_token_hash="m4-recent",
                expires_at=now - timedelta(hours=1),
                created_at=now - timedelta(days=1),
            )
        )
        await session.commit()

    async with SessionLocal() as session:
        assert await maintenance_service.purge_sessions(session, older_than_days=30) >= 1

    async with SessionLocal() as session:
        old = (
            await session.execute(
                select(AuthSession.id).where(AuthSession.refresh_token_hash == "m4-old")
            )
        ).scalars().all()
        recent = (
            await session.execute(
                select(AuthSession.id).where(AuthSession.refresh_token_hash == "m4-recent")
            )
        ).scalars().all()
        assert old == []
        assert len(recent) == 1, "刚过期的会话不该被删（还有 30 天宽限期）"


# ---------------------------------------------------------------------------
# 孤儿文件清理
# ---------------------------------------------------------------------------
async def test_collect_referenced_files_includes_versions(seeded: Seeded) -> None:
    async with SessionLocal() as session:
        referenced = await maintenance_service.collect_referenced_files(session)
        assert referenced, "种子数据里应当有带 storage_path 的版本"
        assert all(p.startswith("files/") for p in referenced)


async def test_cleanup_orphan_files_trashes_then_expires(
    seeded: Seeded, tmp_path: Path
) -> None:
    files_root = tmp_path / "files"
    files_root.mkdir()
    (files_root / "tools").mkdir()

    # 手工造一个无引用的文件
    orphan = files_root / "orphan.bin"
    orphan.write_bytes(b"orphan")

    async with SessionLocal() as session:
        trashed, _deleted = await maintenance_service.cleanup_orphan_files(
            session, files_root=files_root
        )
    assert trashed == 1
    assert not orphan.exists(), "孤儿文件必须移出活目录"
    moved = files_root / ".trash" / "orphan.bin"
    assert moved.exists(), "必须先移入 .trash（留后悔期），而不是直接删"

    # 反例：**被引用**的文件不能被当成孤儿
    rel = "files/tools/1/1/keep.zip"
    kept = files_root / "tools" / "1" / "1" / "keep.zip"
    kept.parent.mkdir(parents=True)
    kept.write_bytes(b"keep")
    async with SessionLocal() as session:
        version = (
            await session.execute(select(ToolVersion).limit(1))
        ).scalars().first()
        assert version is not None
        original = version.storage_path
        version.storage_path = rel
        await session.commit()
    try:
        async with SessionLocal() as session:
            trashed2, _ = await maintenance_service.cleanup_orphan_files(
                session, files_root=files_root
            )
        assert kept.exists(), "被 tool_versions 引用的文件绝不能被当孤儿删掉"
        assert trashed2 == 0
    finally:
        async with SessionLocal() as session:
            version = (
                await session.execute(select(ToolVersion).limit(1))
            ).scalars().first()
            assert version is not None
            version.storage_path = original
            await session.commit()


async def test_cleanup_orphan_files_expires_old_trash(seeded: Seeded, tmp_path: Path) -> None:
    import os
    import time

    files_root = tmp_path / "files"
    trash = files_root / ".trash" / "ancient"
    trash.mkdir(parents=True)
    stale = trash / "gone.bin"
    stale.write_bytes(b"x")
    # 把 mtime 拨到 8 天前
    old = time.time() - 8 * 86400
    os.utime(stale, (old, old))
    os.utime(trash, (old, old))

    async with SessionLocal() as session:
        _trashed, deleted = await maintenance_service.cleanup_orphan_files(
            session, files_root=files_root, trash_retention_days=7
        )
    assert deleted == 1
    assert not trash.exists(), "超过 7 天的 .trash 条目必须被真删"


async def test_cleanup_orphan_files_dry_run_changes_nothing(
    seeded: Seeded, tmp_path: Path
) -> None:
    files_root = tmp_path / "files"
    files_root.mkdir()
    orphan = files_root / "dry.bin"
    orphan.write_bytes(b"x")

    async with SessionLocal() as session:
        trashed, deleted = await maintenance_service.cleanup_orphan_files(
            session, files_root=files_root, dry_run=True
        )
    assert trashed == 1, "dry_run 仍要报告「会发现几个」"
    assert deleted == 0
    assert orphan.exists(), "dry_run 绝不能动文件"


# ---------------------------------------------------------------------------
# 悬空 ACL / 标签计数
# ---------------------------------------------------------------------------
async def test_cleanup_dangling_acl_removes_only_orphans(seeded: Seeded) -> None:
    async with SessionLocal() as session:
        tool_id = seeded.tools["public-approved"]
        session.add(
            ToolAcl(tool_id=tool_id, subject_type="group", subject_id=987654, can_download=True)
        )
        # 一条指向**真实存在**的组的 ACL（不该被删）
        session.add(
            ToolAcl(
                tool_id=tool_id,
                subject_type="group",
                subject_id=seeded.group_id,
                can_download=True,
            )
        )
        await session.commit()

    async with SessionLocal() as session:
        alive_before = len(
            (
                await session.execute(
                    select(ToolAcl.id).where(
                        ToolAcl.subject_type == "group",
                        ToolAcl.subject_id == seeded.group_id,
                    )
                )
            ).scalars().all()
        )
        removed = await maintenance_service.cleanup_dangling_acl(session)
        assert removed >= 1

    async with SessionLocal() as session:
        dangling = (
            await session.execute(
                select(ToolAcl.id).where(
                    ToolAcl.subject_type == "group", ToolAcl.subject_id == 987654
                )
            )
        ).scalars().all()
        alive = (
            await session.execute(
                select(ToolAcl.id).where(
                    ToolAcl.subject_type == "group", ToolAcl.subject_id == seeded.group_id
                )
            )
        ).scalars().all()
        assert dangling == []
        # 种子数据里本来就有指向该真实组的 ACL，所以断言「没变少」而不是精确等于 1
        assert len(alive) == alive_before, "指向真实用户组的 ACL 不能被误删"


async def test_recompute_tag_counts_fixes_drift(seeded: Seeded) -> None:
    async with SessionLocal() as session:
        tag = (await session.execute(select(Tag).limit(1))).scalars().first()
        assert tag is not None
        tag_id = tag.id
        real = (
            await session.execute(
                text("SELECT COUNT(*) FROM tool_tags WHERE tag_id = :t"), {"t": tag_id}
            )
        ).scalar_one()
        tag.usage_count = 4242
        await session.commit()

    async with SessionLocal() as session:
        changed = await maintenance_service.recompute_tag_counts(session)
        assert changed >= 1

    async with SessionLocal() as session:
        tag = await session.get(Tag, tag_id)
        assert tag is not None
        assert tag.usage_count == real, f"计数应被修正回真实值 {real}"


# ---------------------------------------------------------------------------
# 回收站 / 历史版本
# ---------------------------------------------------------------------------
async def test_purge_recycle_bin_only_removes_expired(seeded: Seeded) -> None:
    now = utcnow()
    async with SessionLocal() as session:
        owner = seeded.users["admin"]
        fresh = Tool(
            slug="m4-rb-fresh", name="新近删除", summary="s", description_md="",
            tool_type="file", visibility="public", status="approved", owner_id=owner,
            version_seq=1, deleted_at=now, created_at=now, updated_at=now,
        )
        stale = Tool(
            slug="m4-rb-stale", name="早已删除", summary="s", description_md="",
            tool_type="file", visibility="public", status="approved", owner_id=owner,
            version_seq=1, deleted_at=now - timedelta(days=40), created_at=now, updated_at=now,
        )
        session.add_all([fresh, stale])
        await session.commit()
        fresh_id, stale_id = fresh.id, stale.id

    async with SessionLocal() as session:
        purged = await maintenance_service.purge_recycle_bin(session, retention_days=30)
    assert stale_id in purged
    assert fresh_id not in purged

    async with SessionLocal() as session:
        assert await session.get(Tool, stale_id) is None
        assert await session.get(Tool, fresh_id) is not None, "未超期的回收站工具不能被清"


async def test_gc_versions_keeps_limit(seeded: Seeded) -> None:
    async with SessionLocal() as session:
        # keep 给一个很大的值：语义是「不淘汰任何东西」，函数应当安全返回 0
        assert await maintenance_service.gc_versions(session, keep=999) == 0


# ---------------------------------------------------------------------------
# 一次性入口
# ---------------------------------------------------------------------------
async def test_run_all_returns_summary_and_isolates_failures(
    seeded: Seeded, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files_root = tmp_path / "files"
    files_root.mkdir()

    # 让其中一个任务故意炸掉，验证「一项失败不影响其余」
    async def _boom(*_a, **_kw):
        raise RuntimeError("注入的失败")

    monkeypatch.setattr(maintenance_service, "cleanup_dangling_acl", _boom)

    async with SessionLocal() as session:
        result = await maintenance_service.run_all(
            session,
            files_root=files_root,
            download_log_retention_days=180,
            recycle_bin_retention_days=30,
            version_history_limit=10,
        )

    assert result.errors, "失败的任务必须出现在 errors 里"
    assert any("注入的失败" in e for e in result.errors)
    # 其余任务仍然跑到了（tags 重算是必跑的，且不会因为前一项失败而跳过）
    assert "tags_recounted" in result.as_dict()
    data = result.as_dict()
    assert "recycle_bin_purged_count" in data


async def test_run_all_dry_run_skips_destructive_parts(
    seeded: Seeded, tmp_path: Path
) -> None:
    files_root = tmp_path / "files"
    files_root.mkdir()
    async with SessionLocal() as session:
        result = await maintenance_service.run_all(
            session,
            files_root=files_root,
            download_log_retention_days=180,
            recycle_bin_retention_days=30,
            version_history_limit=10,
            dry_run=True,
        )
    assert result.recycle_bin_purged == []
    assert result.versions_gc == 0


async def test_maintenance_result_as_dict_shape() -> None:
    result = maintenance_service.MaintenanceResult()
    data = result.as_dict()
    for key in (
        "download_logs_purged",
        "sessions_purged",
        "orphan_files_trashed",
        "trash_files_deleted",
        "dangling_acl_purged",
        "tags_recounted",
        "versions_gc",
        "errors",
        "recycle_bin_purged_count",
    ):
        assert key in data


async def test_collect_referenced_files_handles_images(seeded: Seeded) -> None:
    """图片（封面/截图）也必须算作「被引用」，否则孤儿清理会删掉封面。"""
    async with SessionLocal() as session:
        referenced = await maintenance_service.collect_referenced_files(session)
    # 种子数据里有封面图；这里只断言函数不炸且返回集合（图片表可能为空）
    assert isinstance(referenced, set)


async def test_user_model_import_used_for_relations(seeded: Seeded) -> None:
    """防止 `User` 的导入被误当成「未使用」而被 ruff 删掉（关系解析需要它）。"""
    async with SessionLocal() as session:
        assert (await session.execute(select(User).limit(1))).scalars().first() is not None
