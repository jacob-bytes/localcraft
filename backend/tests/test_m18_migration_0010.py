"""M18 收尾：迁移 `0010`（只改一行管理端文案，行为零变更）。

要收口的是 `webapp.health_check_enabled` 的描述文案「在线工具探活开关（二期）」。
M14 **已经实现并接通**了探活（`app/services/webapp_health_service.py`，由
`localcraft-maintenance.timer` 调度），「（二期）」会让管理员以为这个开关还没用。

**为什么必须开迁移**：`GET /api/v1/admin/settings` 的 `description` 取自
`system_settings` 表的列（`app/services/settings_service.py:354` 的
`description=row.description`），改 `SETTING_DEFAULTS` 只影响「行不存在」的兜底，
**不会**改变既有库的显示。`0002`（播种该行）已随 v1.0.0 发布，不得再改。

覆盖：

1. `revision` / `down_revision` 接在 `0009` 后面；
2. 本文件的**冻结文案**与 `SETTING_DEFAULTS` 逐字一致（迁移刻意不 import 应用代码）；
3. `OLD_DESCRIPTION` 确实是 `0002` 播种的原文（否则 `downgrade` 还原的是假想值）；
4. 真实 `upgrade`：文案变了、**行数不变**（这是纯文案迁移，不该增删行）；
   重复 upgrade 幂等；`downgrade` 精确还原；再 upgrade 往返；
5. **接口层**：管理端设置页看到的新描述里没有「（二期）」。
"""

from __future__ import annotations

import os
from pathlib import Path

import sqlalchemy as sa

from app.repositories.system_settings import SETTING_DEFAULTS_BY_KEY
from tests.conftest import auth, login
from tests.test_m12_portal_settings import (
    _load_migration,
    _run_alembic,
    _setting_count,
    _sync_engine,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = BACKEND_DIR / "migrations" / "versions"
MIGRATION_0002 = MIGRATIONS_DIR / "0002_seed_roles_and_settings.py"
MIGRATION_0010 = MIGRATIONS_DIR / "0010_fix_health_check_setting_description.py"

#: 全新库跑到 head 的设置项行数（0002 的 25 + 0007 的 6 + 0008 的 5）。
#: `0010` 是**纯 UPDATE**，不碰这个数 —— 这正是它要证明的事之一。
EXPECTED_FRESH_DB_SETTING_ROWS = 36

_rows_table = sa.table(
    "system_settings",
    sa.column("key", sa.String),
    sa.column("description", sa.String),
)


def _description(db_path: Path, key: str) -> str | None:
    engine = _sync_engine(db_path)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                sa.select(_rows_table.c.description).where(_rows_table.c.key == key)
            ).fetchone()
        return None if row is None else str(row[0])
    finally:
        engine.dispose()


def _call_0010_body(db_path: Path, calls: list[str]) -> None:
    """在同一个连接上直接重复调用迁移体，验证 **DML 自身幂等**。

    理由同 `0008`：`alembic upgrade head` 第二次不会执行迁移体（版本已 stamped），
    证明不了迁移体幂等；要防的是「已执行但版本未登记」的半途状态。
    """
    module = _load_migration(MIGRATION_0010)
    engine = _sync_engine(db_path)
    try:
        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        with engine.begin() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                for name in calls:
                    getattr(module, name)()
    finally:
        engine.dispose()


def _migration_env(tmp_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'mig0010.db'}",
        "DATA_DIR": str(tmp_path / "data"),
        "SECRET_KEY": "migration-test-secret-0123456789",
    }


# ===========================================================================
# ① revision 与迁移链
# ===========================================================================
def test_migration_0010_sits_on_top_of_0009() -> None:
    module = _load_migration(MIGRATION_0010)
    assert module.revision == "0010", "新迁移必须是 0010"
    assert module.down_revision == "0009", "0010 必须挂在 0009 后面"


# ===========================================================================
# ② 冻结文案 ↔ 权威清单 ↔ 被还原的原文
# ===========================================================================
def test_migration_0010_texts_are_consistent() -> None:
    """新文案与 `SETTING_DEFAULTS` 一致；旧文案确实是 `0002` 播下的那个。"""
    module = _load_migration(MIGRATION_0010)

    # 新文案 == 权威清单里的描述（迁移不 import 应用代码，靠这条钉住）
    authoritative = SETTING_DEFAULTS_BY_KEY[module.KEY]
    assert authoritative[3] == module.NEW_DESCRIPTION, (
        f"迁移的 NEW_DESCRIPTION 与 SETTING_DEFAULTS 不一致：\n"
        f"  迁移: {module.NEW_DESCRIPTION!r}\n"
        f"  代码: {authoritative[3]!r}"
    )

    # 这条迁移的前提必须是真的：原文案里确实有「（二期）」
    assert "（二期）" in module.OLD_DESCRIPTION, "OLD_DESCRIPTION 不含「（二期）」，本迁移的前提不成立"
    assert "（二期）" not in module.NEW_DESCRIPTION, "新文案里不该再有「（二期）」"

    # 旧文案必须与 0002 真正播种的逐字一致 —— 否则 downgrade 还原的是假想值
    seeded_0002 = {
        key: description
        for key, _value, _vtype, _public, description in _load_migration(MIGRATION_0002).SETTINGS
    }
    assert seeded_0002[module.KEY] == module.OLD_DESCRIPTION, (
        f"OLD_DESCRIPTION 与 0002 播种的原文不一致：\n"
        f"  0010 认为原文: {module.OLD_DESCRIPTION!r}\n"
        f"  0002 实际播种: {seeded_0002[module.KEY]!r}"
    )


def test_no_setting_description_still_claims_phase_two() -> None:
    """全量扫描：`SETTING_DEFAULTS` 里不该再有任何「（二期）」这种过时的阶段标记。

    阶段标记（「二期」）是 M2 时代给未实现功能留的占位说明。功能一旦接通，
    它就从「诚实的前置声明」变成了「误导」—— 管理员会因此不去启用这个开关。
    """
    offenders = {
        key: entry[3]
        for key, entry in SETTING_DEFAULTS_BY_KEY.items()
        if "（二期）" in str(entry[3])
    }
    assert not offenders, (
        "以下设置项的描述里仍写着「（二期）」，但功能可能已经实现 —— 请核实并改文案"
        "（改文案要配一支新迁移，因为管理端 label 取自数据库列）：\n"
        + "\n".join(f"  {key}: {desc!r}" for key, desc in sorted(offenders.items()))
    )


# ===========================================================================
# ③ SQLite 上的真实 upgrade / 重复 / downgrade / 往返
# ===========================================================================
def test_migration_0010_roundtrip(tmp_path: Path) -> None:
    module = _load_migration(MIGRATION_0010)
    env = _migration_env(tmp_path)
    db_path = Path(env["DATABASE_URL"].split("///")[1])

    # ---- 正向到 head ----
    result = _run_alembic(env, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    assert _description(db_path, module.KEY) == module.NEW_DESCRIPTION
    assert _setting_count(db_path) == EXPECTED_FRESH_DB_SETTING_ROWS, (
        "0010 是纯 UPDATE，不该改变设置项行数"
    )

    # ---- 幂等：迁移体跑两遍 ----
    _call_0010_body(db_path, ["upgrade", "upgrade"])
    assert _description(db_path, module.KEY) == module.NEW_DESCRIPTION
    assert _setting_count(db_path) == EXPECTED_FRESH_DB_SETTING_ROWS

    # ---- downgrade 精确还原 ----
    _call_0010_body(db_path, ["downgrade"])
    assert _description(db_path, module.KEY) == module.OLD_DESCRIPTION
    assert _setting_count(db_path) == EXPECTED_FRESH_DB_SETTING_ROWS

    # ---- 走正规 downgrade → upgrade 往返 ----
    result = _run_alembic(env, "downgrade", "0009")
    assert result.returncode == 0, result.stderr
    assert _description(db_path, module.KEY) == module.OLD_DESCRIPTION

    result = _run_alembic(env, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    assert _description(db_path, module.KEY) == module.NEW_DESCRIPTION


# ===========================================================================
# ④ 接口层：管理端看到的就是新文案
# ===========================================================================
async def test_admin_settings_show_the_corrected_label(client, seeded) -> None:
    module = _load_migration(MIGRATION_0010)

    admin = await login(client, "admin")
    response = await client.get("/api/v1/admin/settings", headers=auth(admin))
    assert response.status_code == 200, response.text
    items = {item["key"]: item for item in response.json()["items"]}

    assert module.KEY in items, f"管理端看不到 {module.KEY}"
    label = items[module.KEY]["description"]
    assert label == module.NEW_DESCRIPTION, (
        f"管理端显示的描述不是新文案：{label!r}（迁移 0010 没生效？）"
    )
    assert "（二期）" not in label, "管理端仍在显示「（二期）」，会误导管理员"
