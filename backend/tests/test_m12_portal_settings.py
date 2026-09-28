"""M12：站点定制设置项与 `/meta` 扩展（contracts/CONTRACT.md §27）。

覆盖任务书 B1~B4：

- **B1/B4**：5 个 `portal.*` 设置项在 `SETTING_DEFAULTS` 里，key / value_type /
  默认空串 / `is_public=True` / description **逐字**对齐 §27.2 的表格。
- **B2/B4**：迁移 `0007` 幂等（重复 `upgrade()` 不炸）、`downgrade()` 真的能跑、
  **downgrade → upgrade 往返**后与迁移前一致，且不误删别的设置项。
- **B3/B4**：`/meta` 返回 5 个新字段；**未配置时是空串而不是 `null`**（行在但值为
  空串、行被人删掉两种「未配置」都断言）；既有字段一个都没变（下面用**冻结的
  字段清单**钉住）。
- 管理端设置页零改动就会出现编辑器所需的元信息（description / value_type /
  is_public）确实由 `GET /admin/settings` 下发（§27.1 事实 1 的后端侧证据）。

刻意**不**在本文件里断言接口总数 —— 那是 `tests/test_guard.py` 的职责，
且本轮不新增接口（仍是 99 operations / 79 paths）。
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy import select

from app.core.config import settings as env_settings
from app.db.session import SessionLocal
from app.models.setting import SystemSetting
from app.repositories.system_settings import SETTING_DEFAULTS, SETTING_DEFAULTS_BY_KEY
from tests.conftest import auth, login

META = "/api/v1/meta"
ADMIN_SETTINGS = "/api/v1/admin/settings"

BACKEND_DIR = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = BACKEND_DIR / "migrations" / "versions"
MIGRATION_0007 = MIGRATIONS_DIR / "0007_portal_customization_settings.py"

#: §27.2 的 5 项 —— **逐字冻结**：key → (value_type, description)。
#: description 就是管理端的 label，所以这里比的是**文案本身**，不是「存在即可」。
EXPECTED_SETTINGS: dict[str, tuple[str, str]] = {
    "portal.site_subtitle": ("string", "站点副标题（显示在门户与登录页，留空则不显示）"),
    "portal.footer_org": ("string", "页脚·运营方"),
    "portal.footer_contact_email": ("string", "页脚·支持邮箱"),
    "portal.footer_contact_phone": ("string", "页脚·内线电话"),
    "portal.footer_notice": ("string", "页脚·备案号 / 版权声明"),
}

#: §27.3 的 5 个 `/meta` 字段 → 对应的设置项 key。
NEW_META_FIELDS: dict[str, str] = {
    "site_subtitle": "portal.site_subtitle",
    "footer_org": "portal.footer_org",
    "footer_contact_email": "portal.footer_contact_email",
    "footer_contact_phone": "portal.footer_contact_phone",
    "footer_notice": "portal.footer_notice",
}

#: **既有** `/meta` 顶层字段（§27.3：只新增，不改既有）。冻结在这里，
#: 任何「顺手改坏」都会让下面的相等断言变红。
LEGACY_META_FIELDS: frozenset[str] = frozenset(
    {
        "site_name",
        "announcement_md",
        "auth_provider",
        "allow_anonymous_view",
        "default_sort",
        "page_size",
        "app_version",
        "api_version",
        "features",
    }
)

#: 既有的 `features` 子字段（同上，冻结）。
LEGACY_FEATURE_FIELDS: frozenset[str] = frozenset(
    {"webapp_health_check", "skill_preview", "anonymous_view", "change_password"}
)

#: 迁移链（冻结）：0007 必须挂在 0006 后面，且链是一条直线。
EXPECTED_REVISIONS: dict[str, str | None] = {
    "0001": None,
    "0002": "0001",
    "0003": "0002",
    "0004": "0003",
    "0005": "0004",
    "0006": "0005",
    "0007": "0006",
}

#: 全新库跑到 head 后的设置项行数：0002 播种 25 项 + 0007 的 5 项。
#:
#: ★ 不等于 `len(SETTING_DEFAULTS)`（31）—— 权威清单里的
#: `images.signature_ttl_hours` **从来没有被任何迁移播种过**（0002 的冻结快照
#: 里没有它），只靠 `get_effective_int` 的运行时兜底。这是本用例发现的**既有**
#: 缺陷，不属于 M12 范围，所以这里写成硬编码的 30 而不是等式，避免把既有问题
#: 伪装成「M12 改坏了」。（已上报，见 M12 交付报告。）
_EXPECTED_FRESH_DB_SETTING_ROWS = 30


async def _snapshot_new_values() -> dict[str, Any]:
    """记录这 5 项当前的**值**，用于用例结束后还原（避免用例间串味）。"""
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(SystemSetting).where(
                    SystemSetting.key.in_(list(NEW_META_FIELDS.values()))
                )
            )
        ).scalars().all()
        return {row.key: row.value for row in rows}


async def _delete_new_rows() -> None:
    """删掉这 5 行，模拟「迁移还没跑」的既有库。"""
    async with SessionLocal() as session:
        for key in NEW_META_FIELDS.values():
            row = await session.get(SystemSetting, key)
            if row is not None:
                await session.delete(row)
        await session.commit()


async def _restore_new_values(values: dict[str, Any]) -> None:
    """按 key 写回值；行不存在则按 §27.2 的定义重建。"""
    async with SessionLocal() as session:
        for key, value in values.items():
            row = await session.get(SystemSetting, key)
            if row is None:
                _, value_type, is_public, description = SETTING_DEFAULTS_BY_KEY[key]
                session.add(
                    SystemSetting(
                        key=key,
                        value=value,
                        value_type=value_type,
                        is_public=is_public,
                        description=description,
                    )
                )
            else:
                row.value = value
        await session.commit()


async def _admin_items(client) -> list[dict[str, Any]]:
    admin = await login(client, "admin")
    response = await client.get(ADMIN_SETTINGS, headers=auth(admin))
    assert response.status_code == 200, response.text
    return response.json()["items"]


# ===========================================================================
# B1 · SETTING_DEFAULTS
# ===========================================================================
def test_new_settings_are_in_setting_defaults() -> None:
    """5 个 key 在权威清单里，且类型 / 默认值 / 公开性 / 文案逐字对齐 §27.2。"""
    missing = [key for key in EXPECTED_SETTINGS if key not in SETTING_DEFAULTS_BY_KEY]
    assert not missing, f"SETTING_DEFAULTS 缺少：{missing}"

    for key, (value_type, description) in EXPECTED_SETTINGS.items():
        value, actual_type, is_public, actual_description = SETTING_DEFAULTS_BY_KEY[key]
        assert value == "", f"{key} 默认值必须是空串（§27.2），实际 {value!r}"
        assert isinstance(value, str), f"{key} 默认值必须是 str"
        assert actual_type == value_type, f"{key} value_type 应为 {value_type}"
        # 全部公开：/meta 只暴露 is_public = true 的项（§27.1 事实 1）
        assert is_public is True, f"{key} 必须 is_public=True"
        assert actual_description == description, (
            f"{key} 的 description 是管理端 label，必须与 §27.2 逐字一致"
        )


def test_setting_defaults_keys_are_unique() -> None:
    """权威清单里重复 key 会让后一条静默覆盖前一条。"""
    keys = [key for key, *_ in SETTING_DEFAULTS]
    assert len(keys) == len(set(keys)), "SETTING_DEFAULTS 里有重复 key"


# ===========================================================================
# B3 · GET /meta
# ===========================================================================
async def test_meta_returns_new_fields_as_empty_strings_when_unconfigured(client) -> None:
    """**未配置 = 空串，不是 `null`**（§27.2 / §27.3）。

    这是真实迁移后的库：5 行都在、值都是空串。
    """
    response = await client.get(META)
    assert response.status_code == 200, response.text
    body = response.json()

    for field in NEW_META_FIELDS:
        assert field in body, f"/meta 缺少字段 {field}"
        assert body[field] == "", (
            f"{field} 未配置时必须是空串（不是 null / None），实际 {body[field]!r}"
        )
        assert isinstance(body[field], str)


async def test_meta_falls_back_to_empty_string_when_row_missing(client) -> None:
    """**既有库没有这 5 行时**（还没跑 0007）也必须返回空串。

    这是本轮最要紧的向后兼容点：迁移没跑 / 行被人删过，`/meta` 不能变成
    `null`，也不能因为缺行而 500。用「删行 → 断言 → 还原」真实复现。
    """
    values = await _snapshot_new_values()
    assert len(values) == len(NEW_META_FIELDS), "前置条件：迁移后这 5 行应当都在"
    try:
        await _delete_new_rows()
        async with SessionLocal() as session:
            assert await session.get(SystemSetting, "portal.footer_org") is None, (
                "前置条件：行确实被删掉了"
            )
        response = await client.get(META)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["site_name"], "删掉新行不该影响既有字段"
        for field in NEW_META_FIELDS:
            assert body[field] == "", f"缺行时 {field} 应回退空串，实际 {body[field]!r}"
    finally:
        await _restore_new_values(values)


async def test_meta_reflects_configured_values(client) -> None:
    """配置后 `/meta` 真的带上值（走既有 `is_public` 机制，不是写死空串）。"""
    admin = await login(client, "admin")
    values = await _snapshot_new_values()
    configured = {
        "portal.site_subtitle": "某事业部工具中心",
        "portal.footer_org": "某事业部数字化部",
        "portal.footer_contact_email": "ops@example.com",
        "portal.footer_contact_phone": "1234",
        "portal.footer_notice": "京ICP备00000000号",
    }
    try:
        response = await client.put(
            ADMIN_SETTINGS,
            json={"items": [{"key": k, "value": v} for k, v in configured.items()]},
            headers=auth(admin),
        )
        assert response.status_code == 200, response.text

        body = (await client.get(META)).json()
        for field, key in NEW_META_FIELDS.items():
            assert body[field] == configured[key], f"{field} 应跟随设置项 {key}"
    finally:
        await _restore_new_values(values)


async def test_meta_legacy_fields_unchanged(client) -> None:
    """§27.3：只新增。既有字段的名字、类型、默认值**一个都没动**。"""
    body = (await client.get(META)).json()

    expected_fields = LEGACY_META_FIELDS | set(NEW_META_FIELDS)
    assert set(body) == expected_fields, (
        "顶层字段集合变了 —— 本轮只允许新增那 5 个\n"
        f"  多了: {sorted(set(body) - expected_fields)}\n"
        f"  少了: {sorted(expected_fields - set(body))}"
    )
    assert set(body["features"]) == LEGACY_FEATURE_FIELDS, "features 子字段被改动了"

    # 类型与取值（不是「存在即可」）
    assert body["site_name"] == "工具与 Skill 平台"
    assert body["announcement_md"] == ""
    assert body["auth_provider"] == "local"
    assert body["allow_anonymous_view"] is True
    assert body["default_sort"] == "hot"
    assert body["page_size"] == 24
    assert body["api_version"] == "v1"
    assert body["app_version"] == env_settings.localcraft_version, (
        "§27.3：版本号不是设置项，页脚直接用既有的 app_version"
    )
    assert isinstance(body["page_size"], int) and not isinstance(body["page_size"], bool)


async def test_no_portal_version_setting_was_introduced(client) -> None:
    """§27.3 明确：**版本号不是设置项** —— 不要偷偷加 `portal.app_version`。"""
    keys = {item["key"] for item in await _admin_items(client)}
    polluted = sorted(key for key in keys if key.startswith("portal.") and "version" in key)
    assert not polluted, f"站点定制不该引入版本号设置项：{polluted}"


# ===========================================================================
# 管理端零改动自动出现编辑器（§27.1 事实 1 的后端侧证据）
# ===========================================================================
async def test_admin_settings_exposes_new_items_with_metadata(client) -> None:
    """`GET /admin/settings` 必须下发这 5 项及其控件元信息。

    设置页完全由后端元信息驱动（label 取 description、控件取 value_type），
    这一条成立 = 管理端零改动就会出现编辑器。
    """
    items = {item["key"]: item for item in await _admin_items(client)}
    for key, (value_type, description) in EXPECTED_SETTINGS.items():
        assert key in items, f"管理端设置清单缺少 {key}"
        item = items[key]
        assert item["value_type"] == value_type
        assert item["description"] == description
        assert item["is_public"] is True
        assert item["value"] == ""
        # 字符串项不该带 options / min / max（后端不下发多余约束）。
        # 注意：响应里是别名 `min` / `max`（`SettingItem` 的 serialization_alias）。
        assert item["options"] is None
        assert item["min"] is None
        assert item["max"] is None


async def test_new_settings_reject_non_string_values(client) -> None:
    """类型校验照旧生效：给字符串项塞数字必须 400 且整体回滚（FR-CFG-04）。"""
    admin = await login(client, "admin")
    response = await client.put(
        ADMIN_SETTINGS,
        json={"items": [{"key": "portal.footer_org", "value": 123}]},
        headers=auth(admin),
    )
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "SETTING_INVALID"
    assert response.json()["details"]["key"] == "portal.footer_org"

    body = (await client.get(META)).json()
    assert body["footer_org"] == "", "非法写入必须整体回滚"


# ===========================================================================
# B2 · 迁移 0007
# ===========================================================================
def _run_alembic(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    """在子进程里跑 alembic（用**当前解释器**，不写死 venv 路径）。"""
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def _load_migration(path: Path):
    """按路径加载迁移模块（文件名以数字开头，不是合法模块名，只能这样导）。"""
    spec = importlib.util.spec_from_file_location(f"mig_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sync_engine(db_path: Path) -> sa.Engine:
    """临时库是普通 SQLite 文件，用同步驱动读一次。"""
    return sa.create_engine(f"sqlite:///{db_path}")


#: 读 JSON 列必须声明 `sa.JSON`：SQLite 里空串物理上存的是 `'""'`，
#: 裸 SQL 读回来是字符串 `'""'` 而不是 `""`（这正是 0005 记录的那个坑）。
_rows_table = sa.table(
    "system_settings",
    sa.column("key", sa.String),
    sa.column("value", sa.JSON),
    sa.column("value_type", sa.String),
    sa.column("is_public", sa.Boolean),
    sa.column("description", sa.String),
)


def _new_rows(db_path: Path) -> dict[str, tuple[Any, str, bool, str]]:
    """读回 0007 负责的那 5 行（值经 JSON 解码）。"""
    engine = _sync_engine(db_path)
    try:
        with engine.connect() as connection:
            result = connection.execute(
                sa.select(
                    _rows_table.c.key,
                    _rows_table.c.value,
                    _rows_table.c.value_type,
                    _rows_table.c.is_public,
                    _rows_table.c.description,
                ).where(_rows_table.c.key.in_(list(EXPECTED_SETTINGS)))
            ).fetchall()
        return {row[0]: (row[1], row[2], bool(row[3]), row[4]) for row in result}
    finally:
        engine.dispose()


def _setting_count(db_path: Path) -> int:
    engine = _sync_engine(db_path)
    try:
        with engine.connect() as connection:
            return int(
                connection.execute(
                    sa.text("SELECT COUNT(*) FROM system_settings")
                ).scalar_one()
            )
    finally:
        engine.dispose()


def _call_migration_body(db_path: Path, calls: list[str]) -> None:
    """在同一个连接上直接调用迁移体的 `upgrade()` / `downgrade()`。

    这是「幂等」的真正测试面：`alembic upgrade head` 第二次调用**不会执行
    迁移体**（版本表已 stamped），所以它证明不了迁移体自身幂等。要防的是
    「DML 已执行、版本未登记」的半途失败状态 —— 那时重跑会真的再执行一次。
    """
    module = _load_migration(MIGRATION_0007)
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


def test_migration_0007_roundtrip_and_idempotency(tmp_path: Path) -> None:
    """0007：正向播种 5 行、重复 upgrade 不炸、downgrade 真的能跑、往返一致。"""
    db_path = tmp_path / "mig0007.db"
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
        "DATA_DIR": str(tmp_path / "data"),
        "SECRET_KEY": "migration-test-secret-0123456789",
    }

    # ---- ① 正向迁移到 head ----
    result = _run_alembic(env, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    rows = _new_rows(db_path)
    assert set(rows) == set(EXPECTED_SETTINGS), f"0007 应插入 5 行，实际 {sorted(rows)}"
    for key, (value_type, description) in EXPECTED_SETTINGS.items():
        value, actual_type, is_public, actual_description = rows[key]
        assert value == "", f"{key} 的 JSON 值应为空串，实际 {value!r}"
        assert actual_type == value_type
        assert is_public is True
        assert actual_description == description

    # ---- ② 幂等：直接重复执行迁移体（含 downgrade 的重复调用）----
    _call_migration_body(db_path, ["upgrade", "upgrade"])
    assert set(_new_rows(db_path)) == set(EXPECTED_SETTINGS), "重复 upgrade 仍应是 5 行"

    _call_migration_body(db_path, ["downgrade", "downgrade"])
    assert _new_rows(db_path) == {}, "重复 downgrade 不该报错，5 行都应消失"

    _call_migration_body(db_path, ["upgrade", "upgrade"])
    assert set(_new_rows(db_path)) == set(EXPECTED_SETTINGS), "再次 upgrade 应恢复 5 行"

    # ---- ③ 走正规 downgrade → upgrade 往返 ----
    result = _run_alembic(env, "downgrade", "0006")
    assert result.returncode == 0, result.stderr
    assert _new_rows(db_path) == {}, "downgrade 到 0006 后 0007 的 5 行必须消失"

    result = _run_alembic(env, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    assert set(_new_rows(db_path)) == set(EXPECTED_SETTINGS), "往返后 5 行必须回来"

    # ---- ④ downgrade 不能误删别人的设置项 ----
    assert _setting_count(db_path) == _EXPECTED_FRESH_DB_SETTING_ROWS, (
        "全新库跑到 head 的设置项行数应为 "
        f"{_EXPECTED_FRESH_DB_SETTING_ROWS}（0002 的 25 + 0007 的 5）"
    )


def test_migration_chain_is_linear() -> None:
    """迁移链是一条直线，`0007` 挂在 `0006` 后面（不能出现分叉）。"""
    modules = {}
    for path in sorted(MIGRATIONS_DIR.glob("0*.py")):
        module = _load_migration(path)
        modules[module.revision] = module.down_revision

    assert modules == EXPECTED_REVISIONS, (
        "迁移链变了\n"
        f"  实际: {modules}\n"
        f"  预期: {EXPECTED_REVISIONS}"
    )
