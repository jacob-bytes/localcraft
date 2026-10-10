"""健壮性加固：启动期「库结构 == 代码」的校验（`app/core/schema_guard.py`）。

## 为什么要有这个校验

`install.sh` / `upgrade.sh` 会在起服务前跑 `alembic upgrade head`，所以**自动路径是安全的**；
但手工照 `docs/05` 部署、以及本项目 `preview.sh`（原先只在库不存在时才迁移）都可能让
**旧 schema 配新代码**。那种情况下服务正常起、`/readyz` 也通过（它只 `SELECT 1`），
然后在某个具体查询上抛难懂的 500 —— 开发过程中真的踩过一次（库 `0009` / 代码 `0010`）。

`httpx.ASGITransport` **不触发 lifespan**，所以单测覆盖不到这条；这里直接测函数本身。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.core import schema_guard
from app.core.schema_guard import SchemaDriftError, assert_db_at_head, db_revision, head_revision
from tests.test_m12_portal_settings import _run_alembic


def _env(tmp_path: Path, name: str = "guard.db") -> dict[str, str]:
    """给 alembic 子进程用的环境（独立库，不碰任何真实数据）。"""
    return {
        **os.environ,
        "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / name}",
        "DATA_DIR": str(tmp_path / "data"),
        "SECRET_KEY": "schema-guard-test-secret-0123456789",
        "COOKIE_SECURE": "false",
    }


async def _revision_of(url: str) -> str | None:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return await db_revision(conn)
    finally:
        await engine.dispose()


def test_head_revision_comes_from_the_code_not_the_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`script_location` 用绝对路径解析 —— 换个工作目录也必须拿到同一个 head。"""
    expected = head_revision()
    assert expected, "迁移链的 head 不该为空"
    monkeypatch.chdir(tmp_path)  # 换到无关目录
    assert head_revision() == expected, (
        "head_revision() 依赖了当前工作目录 —— systemd 拉起服务时 CWD 是 WorkingDirectory，"
        "手工在别处启动就会解析不到 migrations/"
    )


async def test_guard_passes_on_the_ambient_test_database() -> None:
    """在**套件自己配置的那个库**上跑一次守卫 —— 这是唯一能让它在 PostgreSQL 下被验证的测试。

    本文件其它用例都**自己建临时 sqlite 库**（本仓库迁移类测试的既有约定），
    于是它们即使在 PostgreSQL 套件里跑，验证的仍是 SQLite 路径。而
    `scripts/pg-dialect-suite.sh` 会把 `LOCALCRAFT_TEST_DATABASE_URL` 指向 PG，
    `conftest.py` 再把它映射成 `DATABASE_URL`、并用 alembic 升到 head ——
    所以读环境里的 `DATABASE_URL` 就等于"跑在套件当前真正使用的那个数据库上"。

    ★ **只做只读检查**（读版本 + 断言在 head）：那个库是整套测试共享的，
    这里跑 `downgrade` 或改 schema 会连累其它用例。
    """

    url = os.environ["DATABASE_URL"]
    assert await _revision_of(url) == head_revision(), (
        "套件的库应当在 head 上（conftest 用 alembic 升级到 head）"
    )

    engine = create_async_engine(url)
    try:
        await assert_db_at_head(engine)  # 不抛即通过
    finally:
        await engine.dispose()


async def test_db_at_head_passes(tmp_path: Path) -> None:
    env = _env(tmp_path)
    result = _run_alembic(env, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    assert await _revision_of(env["DATABASE_URL"]) == head_revision()

    engine = create_async_engine(env["DATABASE_URL"])
    try:
        await assert_db_at_head(engine)  # 不抛即通过
    finally:
        await engine.dispose()


async def test_db_behind_head_is_rejected(tmp_path: Path) -> None:
    """库落后（升级漏跑迁移）→ 拒绝启动，并说明「落后」与修法。"""
    env = _env(tmp_path)
    assert _run_alembic(env, "upgrade", "head").returncode == 0
    assert _run_alembic(env, "downgrade", "-1").returncode == 0

    engine = create_async_engine(env["DATABASE_URL"])
    try:
        with pytest.raises(SchemaDriftError) as excinfo:
            await assert_db_at_head(engine)
    finally:
        await engine.dispose()

    message = str(excinfo.value)
    assert "落后" in message
    assert "alembic upgrade head" in message, "错误信息必须给出可直接执行的修法"


async def test_unmigrated_db_is_rejected(tmp_path: Path) -> None:
    """从未跑过迁移的空库 → 拒绝启动（这是最容易被忽略的一种：`/readyz` 会通过）。"""
    env = _env(tmp_path, name="empty.db")
    engine = create_async_engine(env["DATABASE_URL"])
    try:
        assert await _revision_of(env["DATABASE_URL"]) is None, "空库不该有 alembic_version"
        with pytest.raises(SchemaDriftError) as excinfo:
            await assert_db_at_head(engine)
    finally:
        await engine.dispose()

    assert "从未跑过迁移" in str(excinfo.value)


async def test_db_ahead_of_code_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """库超前（回滚了代码但没还原库）同样拒绝 —— 旧代码不认识新 schema。

    构造方式：把「代码的 head」伪装成更早的 revision，于是当前库看起来"超前"。
    """
    env = _env(tmp_path)
    assert _run_alembic(env, "upgrade", "head").returncode == 0

    current = await _revision_of(env["DATABASE_URL"])
    assert current is not None
    earlier = f"{max(int(current) - 1, 0):04d}"
    monkeypatch.setattr(schema_guard, "head_revision", lambda: earlier)

    engine = create_async_engine(env["DATABASE_URL"])
    try:
        with pytest.raises(SchemaDriftError) as excinfo:
            await assert_db_at_head(engine)
    finally:
        await engine.dispose()

    message = str(excinfo.value)
    assert "超前" in message
    assert "还原升级前的备份" in message, "库超前时不该引导用户去 downgrade"
