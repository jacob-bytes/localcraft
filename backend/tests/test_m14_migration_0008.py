"""M14 · B3：迁移 `0008`（`contracts/CONTRACT.md` §29.5，冻结）。

本轮要收口的第三件「半成品」是 `images.signature_ttl_hours`：
它在 `SETTING_DEFAULTS` 里、运行时兜底也真的生效，但**从未被任何迁移播种** ——
于是管理端看不到它、`PUT /admin/settings` 传它会报「未知的设置项」。

`0007` **已随 v1.1.0 发布**，所以本轮只能**新建** `0008`
（`down_revision = "0007"`），内容 5 行：

  - `images.signature_ttl_hours`（补播种，168，int，非 public）
  - §29.4 的 4 个限流设置项（`security.rate_limit_*`）

覆盖：

1. 冻结快照与 `SETTING_DEFAULTS` 逐字一致（迁移刻意不 import 应用代码，靠这条保险）；
2. `revision` / `down_revision` 与迁移链是一条直线，且 `0007` 一字未动；
3. 真实 `upgrade` → 5 行、重复 upgrade 幂等、`downgrade` 能跑、往返一致；
4. 补播种在**接口层**的效果：管理端能看到 `images.signature_ttl_hours`，
   且 `PUT /admin/settings` 不再报未知设置项。

> 两方言的**真实输出**（SQLite 与 PG）贴在交付报告里；本文件跑的是 SQLite
> （与 CI 的 SQLite 作业同口径），PG 侧由 `scripts/pg-dialect-suite.sh` 覆盖同一条用例。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from app.db.session import SessionLocal
from app.models.setting import SystemSetting
from app.repositories.system_settings import SETTING_DEFAULTS, SETTING_DEFAULTS_BY_KEY
from tests.conftest import auth, login
from tests.test_m12_portal_settings import (
    _load_migration,
    _run_alembic,
    _setting_count,
    _sync_engine,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = BACKEND_DIR / "migrations" / "versions"
MIGRATION_0007 = MIGRATIONS_DIR / "0007_portal_customization_settings.py"
MIGRATION_0008 = MIGRATIONS_DIR / "0008_rate_limit_and_image_ttl_settings.py"

#: 0008 负责的 5 行 —— 逐字冻结（key → (值, value_type, is_public, description)）。
EXPECTED_FIVE: dict[str, tuple[Any, str, bool, str]] = {
    "images.signature_ttl_hours": (
        168,
        "int",
        False,
        "图片签名 URL 有效期（小时），默认 7 天",
    ),
    "security.rate_limit_enabled": (True, "bool", False, "限流总开关"),
    "security.rate_limit_per_minute": (1200, "int", False, "每 IP 每分钟的 API 总配额"),
    "security.rate_limit_login_per_minute": (10, "int", False, "每 IP 每分钟的登录配额"),
    "security.rate_limit_upload_per_minute": (30, "int", False, "每 IP 每分钟的上传配额"),
}

#: 迁移链（M14 之后）：0008 挂在 0007 后面，链仍是一条直线。
EXPECTED_REVISIONS: dict[str, str | None] = {
    "0001": None,
    "0002": "0001",
    "0003": "0002",
    "0004": "0003",
    "0005": "0004",
    "0006": "0005",
    "0007": "0006",
    "0008": "0007",
    # M15：PostgreSQL 全文检索（tools.search_vector + GIN，契约 §31.3）
    "0009": "0008",
}

#: 全新库跑到 head 的设置项行数：0002 的 25 + 0007 的 6 + 0008 的 5。
EXPECTED_FRESH_DB_SETTING_ROWS = 36

#: 0008 的行被删掉之后（= 停在 0007）剩下的行数
_ROWS_AT_0007 = 31

#: 读 JSON 列必须声明 `sa.JSON`（SQLite 里值物理上是 JSON 文本）。
_rows_table = sa.table(
    "system_settings",
    sa.column("key", sa.String),
    sa.column("value", sa.JSON),
    sa.column("value_type", sa.String),
    sa.column("is_public", sa.Boolean),
    sa.column("description", sa.String),
)


def _call_0008_body(db_path: Path, calls: list[str]) -> None:
    """在同一个连接上直接调用 **0008** 迁移体的 `upgrade()` / `downgrade()`。

    为什么不能复用 `tests/test_m12_portal_settings.py::_call_migration_body`：
    那个 helper 写死了加载 `0007` 的模块（它服务于 M12/M13 的用例）。
    这里是「幂等」的真正测试面：`alembic upgrade head` 第二次调用**不会执行
    迁移体**（版本表已 stamped），证明不了迁移体自身幂等；要防的是
    「DML 已执行、版本未登记」的半途失败状态 —— 那时重跑会真的再执行一次。
    """
    module = _load_migration(MIGRATION_0008)
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


def _five_rows(db_path: Path) -> dict[str, tuple[Any, str, bool, str]]:
    engine = _sync_engine(db_path)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                sa.select(
                    _rows_table.c.key,
                    _rows_table.c.value,
                    _rows_table.c.value_type,
                    _rows_table.c.is_public,
                    _rows_table.c.description,
                ).where(_rows_table.c.key.in_(list(EXPECTED_FIVE)))
            ).fetchall()
        return {row[0]: (row[1], row[2], bool(row[3]), row[4]) for row in rows}
    finally:
        engine.dispose()


def _migration_env(tmp_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'mig0008.db'}",
        "DATA_DIR": str(tmp_path / "data"),
        "SECRET_KEY": "migration-test-secret-0123456789",
    }


# ===========================================================================
# ① 冻结快照 ↔ 权威清单
# ===========================================================================
def test_migration_0008_snapshot_matches_authoritative_list() -> None:
    """迁移里的**冻结快照**必须与 `SETTING_DEFAULTS` 这 5 条逐字一致。"""
    module = _load_migration(MIGRATION_0008)
    snapshot = {
        key: (value, value_type, is_public, description)
        for key, value, value_type, is_public, description in module.NEW_SETTINGS
    }
    assert set(snapshot) == set(EXPECTED_FIVE), (
        f"0008 应恰好播种这 5 行，实际 {sorted(snapshot)}"
    )
    assert len(module.NEW_SETTINGS) == 5, "§29.5 规定 0008 播 5 行"
    assert snapshot == EXPECTED_FIVE
    for key in EXPECTED_FIVE:
        assert snapshot[key] == SETTING_DEFAULTS_BY_KEY[key], (
            f"{key} 的迁移快照与 SETTING_DEFAULTS 不一致："
            f"{snapshot[key]!r} != {SETTING_DEFAULTS_BY_KEY[key]!r}"
        )


# ===========================================================================
# ② revision / 迁移链（§29.5：0007 已发布，不得再改）
# ===========================================================================
def test_migration_0008_revision_and_chain() -> None:
    module = _load_migration(MIGRATION_0008)
    assert module.revision == "0008", "新迁移必须是 0008"
    assert module.down_revision == "0007", "0008 必须挂在 0007 后面"

    revisions: dict[str, str | None] = {}
    for path in sorted(MIGRATIONS_DIR.glob("0*.py")):
        loaded = _load_migration(path)
        revisions[loaded.revision] = loaded.down_revision
    assert revisions == EXPECTED_REVISIONS, (
        "迁移链变了\n"
        f"  实际: {revisions}\n"
        f"  预期: {EXPECTED_REVISIONS}"
    )

    # `0007` 已随 v1.1.0 发布 —— 本轮**不得**改它：revision/down_revision 不变，
    # 内容仍恰好是它自己那 6 行（M12/M13 的用例逐字钉住了内容）。
    old = _load_migration(MIGRATION_0007)
    assert (old.revision, old.down_revision) == ("0007", "0006")
    assert len(old.NEW_SETTINGS) == 6, "0007 必须仍是 6 行（M14 不得往里加东西）"
    old_keys = {key for key, *_ in old.NEW_SETTINGS}
    assert not (old_keys & set(EXPECTED_FIVE)), "M14 的 5 行不得混进 0007"


def test_migration_0008_uses_core_json_not_raw_sql() -> None:
    """§29.5：Core + `sa.column("value", sa.JSON)`，不要裸 SQL（0005 的坑）。"""
    text = MIGRATION_0008.read_text(encoding="utf-8")
    assert 'sa.column("value", sa.JSON)' in text
    import re

    body = text.split("def upgrade()", 1)[1]
    # 只看代码行（去掉注释）—— 文档里刻意解释了「为什么不用 bulk_insert」，
    # 那是要保留的，不该被这条守卫误判。
    code_lines = [
        line for line in body.splitlines() if not line.lstrip().startswith("#")
    ]
    code = "\n".join(code_lines)
    assert not re.search(r"\bop\.execute\(", code), "upgrade 里不该出现裸 SQL"
    assert "op.bulk_insert(" not in code, "幂等要求逐 key 检查，不用 bulk_insert"
    assert "_existing_keys" in code, "upgrade 必须逐 key 检查是否已存在（幂等）"


# ===========================================================================
# ③ SQLite 上的真实 upgrade / 重复 upgrade / downgrade
# ===========================================================================
def test_migration_0008_roundtrip_and_repeat_upgrade(tmp_path: Path) -> None:
    """真实跑：upgrade → 5 行、重复 upgrade 幂等、downgrade → upgrade 往返。"""
    env = _migration_env(tmp_path)
    db_path = Path(env["DATABASE_URL"].split("///")[1])

    # ---- ① 正向迁移到 head ----
    result = _run_alembic(env, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    rows = _five_rows(db_path)
    assert set(rows) == set(EXPECTED_FIVE), f"0008 应播种 5 行，实际 {sorted(rows)}"
    for key, (value, value_type, is_public, description) in EXPECTED_FIVE.items():
        actual_value, actual_type, actual_public, actual_description = rows[key]
        assert actual_value == value, f"{key} 的 JSON 值应为 {value!r}，实际 {actual_value!r}"
        assert actual_type == value_type
        assert actual_public is is_public
        assert actual_description == description
    assert _setting_count(db_path) == EXPECTED_FRESH_DB_SETTING_ROWS

    # ---- ② 幂等：直接重复执行迁移体 ----
    _call_0008_body(db_path, ["upgrade", "upgrade"])
    assert set(_five_rows(db_path)) == set(EXPECTED_FIVE), "重复 upgrade 仍应是 5 行"
    assert _setting_count(db_path) == EXPECTED_FRESH_DB_SETTING_ROWS, (
        "重复 upgrade 不该插出重复行"
    )

    _call_0008_body(db_path, ["downgrade", "downgrade"])
    assert _five_rows(db_path) == {}, "重复 downgrade 不该报错，5 行都应消失"
    assert _setting_count(db_path) == _ROWS_AT_0007, "downgrade 不该误删别人的设置项"

    _call_0008_body(db_path, ["upgrade", "upgrade"])
    assert set(_five_rows(db_path)) == set(EXPECTED_FIVE), "再次 upgrade 应恢复 5 行"

    # ---- ③ 走正规 downgrade → upgrade 往返 ----
    result = _run_alembic(env, "downgrade", "0007")
    assert result.returncode == 0, result.stderr
    assert _five_rows(db_path) == {}, "downgrade 到 0007 后 0008 的 5 行必须消失"
    assert _setting_count(db_path) == _ROWS_AT_0007

    result = _run_alembic(env, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    assert set(_five_rows(db_path)) == set(EXPECTED_FIVE), "往返后 5 行必须回来"
    assert _setting_count(db_path) == EXPECTED_FRESH_DB_SETTING_ROWS


# ===========================================================================
# ④ 补播种在接口层的效果（「东西已存在但没接通」的第三例收口）
# ===========================================================================
async def test_admin_can_now_see_and_set_image_ttl(client, seeded) -> None:
    """`images.signature_ttl_hours` 播种后：管理端**看得到**、`PUT` **改得动**。

    这正是 §29.1 记录的缺陷：迁移没播种 → 管理端看不到（页面由行驱动），
    `PUT` 报「未知的设置项」（`get_row` 为 None）。
    """
    admin = await login(client, "admin")
    response = await client.get("/api/v1/admin/settings", headers=auth(admin))
    assert response.status_code == 200, response.text
    items = {item["key"]: item for item in response.json()["items"]}
    assert "images.signature_ttl_hours" in items, (
        "管理端设置页看不到 images.signature_ttl_hours —— 迁移 0008 没生效？"
    )
    assert items["images.signature_ttl_hours"]["value"] == 168
    assert items["images.signature_ttl_hours"]["value_type"] == "int"
    assert items["images.signature_ttl_hours"]["is_public"] is False

    # 4 个限流项也必须在（§29.4）
    for key in EXPECTED_FIVE:
        assert key in items, f"管理端看不到 {key}"

    # PUT 能改（原先会报「未知的设置项」）
    original = items["images.signature_ttl_hours"]["value"]
    try:
        response = await client.put(
            "/api/v1/admin/settings",
            json={"items": [{"key": "images.signature_ttl_hours", "value": 24}]},
            headers=auth(admin),
        )
        assert response.status_code == 200, response.text
        async with SessionLocal() as session:
            row = await session.get(SystemSetting, "images.signature_ttl_hours")
            assert row is not None
            assert row.value == 24
            row.value = original
            await session.commit()
    finally:
        async with SessionLocal() as session:
            row = await session.get(SystemSetting, "images.signature_ttl_hours")
            if row is not None:
                row.value = original
                await session.commit()


def test_setting_defaults_and_db_rows_now_agree() -> None:
    """M14 之后，权威清单与「迁移播种出来的行」的集合**终于一致**。

    此前两者差 1（`images.signature_ttl_hours` 从未被播种）。这条断言把
    「再新增设置项必须同时写迁移」变成会失败的检查（§27.1 事实 2）：
    读的是各迁移**真实的冻结快照常量**，不是注释文本。
    """
    assert len(SETTING_DEFAULTS) == len({key for key, *_ in SETTING_DEFAULTS}), (
        "SETTING_DEFAULTS 里有重复 key"
    )

    migration_0002 = _load_migration(MIGRATIONS_DIR / "0002_seed_roles_and_settings.py")
    migration_0005 = _load_migration(MIGRATIONS_DIR / "0005_default_allow_anonymous_view.py")
    seeded = {key for key, *_ in migration_0002.SETTINGS}
    # 0005 只改一个既有项，但它播种了该行（默认值 true），所以也算「有迁移负责」
    seeded.add(str(migration_0005.KEY))
    seeded |= {key for key, *_ in _load_migration(MIGRATION_0007).NEW_SETTINGS}
    seeded |= {key for key, *_ in _load_migration(MIGRATION_0008).NEW_SETTINGS}

    authoritative = {key for key, *_ in SETTING_DEFAULTS}
    missing = authoritative - seeded
    assert not missing, (
        "以下设置项在 SETTING_DEFAULTS 里，但没有任何迁移播种"
        "（新增设置项必须配一个迁移，否则既有库拿不到行）："
        f"{sorted(missing)}"
    )
    assert not (seeded - authoritative), (
        "迁移里播种了 SETTING_DEFAULTS 之外的 key —— 权威清单与迁移已走散："
        f"{sorted(seeded - authoritative)}"
    )
