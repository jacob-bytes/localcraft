"""M13：第 6 个设置项 `portal.footer_tagline`（contracts/CONTRACT.md §28）。

背景（§28.1）：§27 写「既有部署零变化」时漏看了 `AppShell` 里**一直存在**的全局页脚
（版本行 + 写死的标语「内网工具与 Skill 共享平台」）。§28 的修正是：

- **§28.3**：把那条写死的标语做成第 6 个设置项，**默认值刻意是原串**（定点例外）；
- **§28.4**：`/meta` 再新增 `footer_tagline`；
- **§28.5**：把它**并进迁移 `0007`**，不新建 `0008`（0007 未提交、未发布、没库跑过）。

本文件覆盖任务书 C1~C4：

- **C1**：6 个 key 都在 `SETTING_DEFAULTS` 且 `is_public=True`；只有
  `portal.footer_tagline` 默认非空（= 原标语串），其余 5 个是空串。
- **C2**：迁移 `0007` 的**冻结快照**与权威清单逐字一致；`revision`/`down_revision`
  仍是 `0007`/`0006`；仓库里**没有** `0008`；真实 upgrade / 重复 upgrade /
  downgrade → upgrade 都能跑且播种 6 行。
- **C3**：`/meta` 返回 `footer_tagline`，**全新库上等于原标语串**。
- **C4**：既有字段与 M12 的 5 项一个没动（字段集合在 M12 文件里冻结）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from app.db.session import SessionLocal
from app.models.setting import SystemSetting
from app.repositories.system_settings import SETTING_DEFAULTS, SETTING_DEFAULTS_BY_KEY
from app.services.settings_service import _public_text
from tests.conftest import auth, login
from tests.test_m12_portal_settings import (
    _call_migration_body,
    _load_migration,
    _run_alembic,
    _setting_count,
    _sync_engine,
)

META = "/api/v1/meta"
ADMIN_SETTINGS = "/api/v1/admin/settings"

BACKEND_DIR = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = BACKEND_DIR / "migrations" / "versions"
MIGRATION_0007 = MIGRATIONS_DIR / "0007_portal_customization_settings.py"

#: ★ §28.3 的原标语串（逐字）。`AppShell` 里原本写死的就是它。
TAGLINE = "内网工具与 Skill 共享平台"

#: 第 6 项自己的 key（§28.3 / §28.4）。
TAGLINE_KEY = "portal.footer_tagline"
TAGLINE_FIELD = "footer_tagline"

#: §27.2 的 5 项 + §28.3 的第 6 项 —— **逐字冻结** key → (value_type, 默认值, description)。
#: description 就是管理端 label，所以这里比的是**文案本身**，不是「存在即可」。
EXPECTED_SIX: dict[str, tuple[str, str, str]] = {
    "portal.site_subtitle": (
        "string",
        "",
        "站点副标题（显示在门户与登录页，留空则不显示）",
    ),
    TAGLINE_KEY: (
        "string",
        TAGLINE,
        "页脚标语（显示在所有页面底部，留空则不显示）",
    ),
    "portal.footer_org": ("string", "", "页脚·运营方"),
    "portal.footer_contact_email": ("string", "", "页脚·支持邮箱"),
    "portal.footer_contact_phone": ("string", "", "页脚·内线电话"),
    "portal.footer_notice": ("string", "", "页脚·备案号 / 版权声明"),
}

#: M12 的 5 项（§27.2）—— 用来钉住「默认全空」这条**没有被 §28 改动**。
M12_KEYS: frozenset[str] = frozenset(EXPECTED_SIX) - {TAGLINE_KEY}

#: 迁移链（冻结）：M13 **并进** 0007，所以 M13 当时这条链与 M12 时逐字相同。
#: M14（contracts §29.5）追加 `0008`：`0007` 已随 v1.1.0 发布，只能新建 0008。
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

#: 全新库跑到 head 的设置项行数：0002 的 25 + 0007 的 6 + M14 的 0008 的 5 = 36。
EXPECTED_FRESH_DB_SETTING_ROWS = 36

#: 只播种 0002 那批（= 0006 的库）时的行数 —— 「downgrade 到 0006」后的对照值。
_ROWS_AT_0006 = 25

#: 0007 的行被直接删掉、但 **0008 的行还在**时的行数（25 + 5）——
#: `_call_migration_body(["downgrade"])` 只执行 0007 的迁移体，不会碰 0008。
_ROWS_WITHOUT_0007 = 30

#: 读 JSON 列必须声明 `sa.JSON`（SQLite 里值物理上是 JSON 文本）。
_rows_table = sa.table(
    "system_settings",
    sa.column("key", sa.String),
    sa.column("value", sa.JSON),
    sa.column("value_type", sa.String),
    sa.column("is_public", sa.Boolean),
    sa.column("description", sa.String),
)


def _six_rows(db_path: Path) -> dict[str, tuple[Any, str, bool, str]]:
    """读回 0007 负责的那 6 行（值经 JSON 解码）。"""
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
                ).where(_rows_table.c.key.in_(list(EXPECTED_SIX)))
            ).fetchall()
        return {row[0]: (row[1], row[2], bool(row[3]), row[4]) for row in result}
    finally:
        engine.dispose()


def _migration_env(tmp_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'mig0007-m13.db'}",
        "DATA_DIR": str(tmp_path / "data"),
        "SECRET_KEY": "migration-test-secret-0123456789",
    }


async def _admin_items(client) -> list[dict[str, Any]]:
    admin = await login(client, "admin")
    response = await client.get(ADMIN_SETTINGS, headers=auth(admin))
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def _restore_tagline(value: Any) -> None:
    """按 key 写回 `portal.footer_tagline`；行不存在则按 §28.3 重建。"""
    async with SessionLocal() as session:
        row = await session.get(SystemSetting, TAGLINE_KEY)
        if row is None:
            _, value_type, is_public, description = SETTING_DEFAULTS_BY_KEY[TAGLINE_KEY]
            session.add(
                SystemSetting(
                    key=TAGLINE_KEY,
                    value=value,
                    value_type=value_type,
                    is_public=is_public,
                    description=description,
                )
            )
        else:
            row.value = value
        await session.commit()


# ===========================================================================
# C1 · SETTING_DEFAULTS 里的第 6 项（含「默认非空」这个定点例外）
# ===========================================================================
def test_six_new_settings_are_public_with_the_tagline_exception() -> None:
    """6 个 key 都在权威清单里，且类型 / 默认值 / 公开性 / 文案逐字对齐 §27.2 + §28.3。"""
    missing = [key for key in EXPECTED_SIX if key not in SETTING_DEFAULTS_BY_KEY]
    assert not missing, f"SETTING_DEFAULTS 缺少：{missing}"

    for key, (value_type, default, description) in EXPECTED_SIX.items():
        value, actual_type, is_public, actual_description = SETTING_DEFAULTS_BY_KEY[key]
        assert value == default, f"{key} 默认值应为 {default!r}，实际 {value!r}"
        assert isinstance(value, str), f"{key} 默认值必须是 str"
        assert actual_type == value_type, f"{key} value_type 应为 {value_type}"
        assert is_public is True, f"{key} 必须 is_public=True（/meta 只暴露公开项）"
        assert actual_description == description, (
            f"{key} 的 description 是管理端 label，必须与契约逐字一致"
        )


def test_only_the_tagline_has_a_non_empty_default() -> None:
    """★ 定点例外的边界：**只有** `portal.footer_tagline` 默认非空。

    这条是防「顺手给别的项也塞个默认值」——§27.2 的「默认全空」只被 §28.3
    修正了一项，其余 5 项仍然必须空，否则既有部署的视觉零变化就破了。
    """
    non_empty = [
        key
        for key in EXPECTED_SIX
        if SETTING_DEFAULTS_BY_KEY[key][0] != ""
    ]
    assert non_empty == [TAGLINE_KEY], f"只有标语项允许非空默认值，实际：{non_empty}"

    for key in M12_KEYS:
        assert SETTING_DEFAULTS_BY_KEY[key][0] == "", f"{key} 必须仍然默认空串（§27.2）"

    assert SETTING_DEFAULTS_BY_KEY[TAGLINE_KEY][0] == TAGLINE


def test_tagline_setting_is_in_the_authoritative_list_once() -> None:
    """权威清单里 `portal.footer_tagline` 恰好出现一次（重复 key 会静默覆盖）。"""
    keys = [key for key, *_ in SETTING_DEFAULTS]
    assert keys.count(TAGLINE_KEY) == 1, "portal.footer_tagline 在 SETTING_DEFAULTS 里重复了"
    assert len(keys) == len(set(keys)), "SETTING_DEFAULTS 里有重复 key"


# ===========================================================================
# C2 · 迁移 0007（并进第 6 项，不新建 0008）
# ===========================================================================
def test_migration_0007_snapshot_matches_authoritative_list() -> None:
    """迁移里的**冻结快照**必须与 `SETTING_DEFAULTS` 这 6 条逐字一致。

    迁移刻意不 import 应用代码（否则历史环境重放会漂移），代价是两份常量
    可能走散 —— 这条用例就是那份保险。
    """
    module = _load_migration(MIGRATION_0007)
    snapshot = {
        key: (value, value_type, is_public, description)
        for key, value, value_type, is_public, description in module.NEW_SETTINGS
    }
    assert set(snapshot) == set(EXPECTED_SIX), (
        f"0007 应恰好播种这 6 行，实际 {sorted(snapshot)}"
    )
    assert len(module.NEW_SETTINGS) == 6, "0007 现在应播种 6 行（§28.5 并进第 6 项）"
    for key in EXPECTED_SIX:
        assert snapshot[key] == SETTING_DEFAULTS_BY_KEY[key], (
            f"{key} 的迁移快照与 SETTING_DEFAULTS 不一致："
            f"{snapshot[key]!r} != {SETTING_DEFAULTS_BY_KEY[key]!r}"
        )
    assert snapshot[TAGLINE_KEY][0] == TAGLINE


def test_migration_0007_revision_unchanged_and_0008_follows() -> None:
    """`0007` 的 `revision`/`down_revision` 不变；M14 的 `0008` 挂在其后。

    ★ 本条原为 `test_migration_0007_revision_and_no_0008`，断言「仓库里没有 0008」
    —— 那是 **M13 当时的**正确断言（§28.5：0007 未发布，所以第 6 项并进 0007）。
    M14 的 §29.5 反过来要求**新建** `0008`，因为 `0007` 已随 v1.1.0 发布。
    两条裁定不矛盾，判据是「迁移是否已经发布」，所以这里改成断言同一判据的
    新形态：`0007` 一字未动 + `0008` 紧挂在 `0007` 后面（链仍是直线）。
    """
    module = _load_migration(MIGRATION_0007)
    assert module.revision == "0007", "并进迁移不该改 revision"
    assert module.down_revision == "0006", "并进迁移不该改 down_revision"

    revisions: dict[str, str | None] = {}
    for path in sorted(MIGRATIONS_DIR.glob("0*.py")):
        loaded = _load_migration(path)
        revisions[loaded.revision] = loaded.down_revision

    assert revisions == EXPECTED_REVISIONS, (
        "迁移链被改动了\n"
        f"  实际: {revisions}\n"
        f"  预期: {EXPECTED_REVISIONS}"
    )
    assert revisions.get("0008") == "0007", "M14 的 0008 必须挂在 0007 后面（§29.5）"
    assert MIGRATION_0007.is_file(), "0007 仍是本节制的迁移"
    # `0007` 已发布，M14 不得改它：内容级断言由 M12/M13 的其它用例覆盖
    # （快照与 SETTING_DEFAULTS 逐字一致、6 行、值/文案不变）。
    module_0008 = _load_migration(MIGRATIONS_DIR / "0008_rate_limit_and_image_ttl_settings.py")
    assert module_0008.revision == "0008"
    assert module_0008.down_revision == "0007"


def test_migration_0007_roundtrip_and_repeat_upgrade(tmp_path: Path) -> None:
    """真实跑：upgrade → 6 行、重复 upgrade 幂等、downgrade → upgrade 往返。

    **SQLite 侧**的实测（PG 侧在交付报告里贴真实输出，CI 由 PG 套件覆盖）。
    """
    env = _migration_env(tmp_path)
    db_path = Path(env["DATABASE_URL"].split("///")[1])

    # ---- ① 正向迁移到 head：6 行都在，值/类型/公开性/文案逐字正确 ----
    result = _run_alembic(env, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    rows = _six_rows(db_path)
    assert set(rows) == set(EXPECTED_SIX), f"0007 应播种 6 行，实际 {sorted(rows)}"
    for key, (value_type, default, description) in EXPECTED_SIX.items():
        value, actual_type, is_public, actual_description = rows[key]
        assert value == default, f"{key} 的 JSON 值应为 {default!r}，实际 {value!r}"
        assert actual_type == value_type
        assert is_public is True
        assert actual_description == description
    assert _setting_count(db_path) == EXPECTED_FRESH_DB_SETTING_ROWS
    # 中文标语经 JSON 列往返后必须一字不差（不是 mojibake，也不是 `None`）
    assert rows[TAGLINE_KEY][0] == TAGLINE

    # ---- ② 幂等：直接重复执行迁移体（含 downgrade 的重复调用）----
    _call_migration_body(db_path, ["upgrade", "upgrade"])
    assert set(_six_rows(db_path)) == set(EXPECTED_SIX), "重复 upgrade 仍应是 6 行"
    assert _setting_count(db_path) == EXPECTED_FRESH_DB_SETTING_ROWS, (
        "重复 upgrade 不该插出重复行"
    )

    _call_migration_body(db_path, ["downgrade", "downgrade"])
    assert _six_rows(db_path) == {}, "重复 downgrade 不该报错，6 行都应消失"
    # 直接执行 0007 的迁移体不会碰 M14 的 0008 的 5 行，所以对照值是 25 + 5
    assert _setting_count(db_path) == _ROWS_WITHOUT_0007, "downgrade 不该误删别的设置项"

    _call_migration_body(db_path, ["upgrade", "upgrade"])
    assert set(_six_rows(db_path)) == set(EXPECTED_SIX), "再次 upgrade 应恢复 6 行"

    # ---- ③ 走正规 downgrade → upgrade 往返 ----
    result = _run_alembic(env, "downgrade", "0006")
    assert result.returncode == 0, result.stderr
    assert _six_rows(db_path) == {}, "downgrade 到 0006 后 0007 的 6 行必须消失"
    assert _setting_count(db_path) == _ROWS_AT_0006

    result = _run_alembic(env, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    assert set(_six_rows(db_path)) == set(EXPECTED_SIX), "往返后 6 行必须回来"
    assert _six_rows(db_path)[TAGLINE_KEY][0] == TAGLINE, "往返后标语值必须原样恢复"
    assert _setting_count(db_path) == EXPECTED_FRESH_DB_SETTING_ROWS


def test_migration_module_uses_core_json_not_raw_sql() -> None:
    """§27.1 事实 3 的规矩不变：**Core + `sa.JSON`**，不是裸 SQL。

    （PG 不接受 text→json 的隐式转换；裸 SQL 在 SQLite 上看不出来。）
    """
    source = MIGRATION_0007.read_text(encoding="utf-8")
    assert 'sa.column("value", sa.JSON)' in source, "value 列必须声明为 sa.JSON"
    for forbidden in ("op.execute(", "sa.text("):
        assert forbidden not in source, f"迁移里不该出现裸 SQL：{forbidden}"


# ===========================================================================
# C3 · GET /meta 的 footer_tagline
# ===========================================================================
async def test_meta_footer_tagline_on_freshly_migrated_db(client) -> None:
    """**全新库**（真跑了迁移）：行在、值就是原标语串，`/meta` 照原样透出。"""
    async with SessionLocal() as session:
        row = await session.get(SystemSetting, TAGLINE_KEY)
    assert row is not None, "前置条件：0007 必须在全新库上播种 portal.footer_tagline"
    assert row.value == TAGLINE, f"行值应为原标语串，实际 {row.value!r}"

    response = await client.get(META)
    assert response.status_code == 200, response.text
    body = response.json()
    assert TAGLINE_FIELD in body, "/meta 缺少 footer_tagline 字段"
    assert body[TAGLINE_FIELD] == TAGLINE, (
        f"全新库上 footer_tagline 应等于原标语串，实际 {body[TAGLINE_FIELD]!r}"
    )
    assert isinstance(body[TAGLINE_FIELD], str)


async def test_meta_footer_tagline_falls_back_to_tagline_when_row_missing(client) -> None:
    """**既有库还没跑 0007 时**（行缺失）也必须是原标语串，不能是 `null` / 空串。

    §28.3 的理由就是「未配置的既有部署在页脚上与改动前**完全一致**」——
    监控方核实过预览库当时还停在 `0006`（§28.5），所以这条不是假想场景。
    与 M12 那 5 项的差别只在**回退到哪个默认值**：这里回退到非空的原标语。
    """
    async with SessionLocal() as session:
        row = await session.get(SystemSetting, TAGLINE_KEY)
        original: Any = row.value if row is not None else TAGLINE
    try:
        async with SessionLocal() as session:
            current = await session.get(SystemSetting, TAGLINE_KEY)
            if current is not None:
                await session.delete(current)
                await session.commit()
            assert await session.get(SystemSetting, TAGLINE_KEY) is None, (
                "前置条件：行确实被删掉了"
            )

        response = await client.get(META)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["site_name"], "删掉这行不该影响既有字段"
        assert body[TAGLINE_FIELD] == TAGLINE, (
            f"缺行时应回退到原标语串（§28.3），实际 {body[TAGLINE_FIELD]!r}"
        )
    finally:
        await _restore_tagline(original)


async def test_meta_footer_tagline_reflects_configured_value(client) -> None:
    """配置后 `/meta` 真的跟随（走既有 `is_public` 机制，不是写死默认值）。"""
    admin = await login(client, "admin")
    async with SessionLocal() as session:
        row = await session.get(SystemSetting, TAGLINE_KEY)
        original: Any = row.value if row is not None else TAGLINE
    custom = "某事业部 · 内部平台"
    try:
        response = await client.put(
            ADMIN_SETTINGS,
            json={"items": [{"key": TAGLINE_KEY, "value": custom}]},
            headers=auth(admin),
        )
        assert response.status_code == 200, response.text
        body = (await client.get(META)).json()
        assert body[TAGLINE_FIELD] == custom, "footer_tagline 应跟随设置项 portal.footer_tagline"
    finally:
        await _restore_tagline(original)


async def test_meta_footer_tagline_empty_when_admin_blanks_it(client) -> None:
    """管理员**显式留空** → `/meta` 是空串（§28.3「留空则不显示」，前端据此不渲染）。"""
    admin = await login(client, "admin")
    async with SessionLocal() as session:
        row = await session.get(SystemSetting, TAGLINE_KEY)
        original: Any = row.value if row is not None else TAGLINE
    try:
        response = await client.put(
            ADMIN_SETTINGS,
            json={"items": [{"key": TAGLINE_KEY, "value": ""}]},
            headers=auth(admin),
        )
        assert response.status_code == 200, response.text
        body = (await client.get(META)).json()
        assert body[TAGLINE_FIELD] == "", "显式留空应当是空串（不是回退到默认标语）"
    finally:
        await _restore_tagline(original)


async def test_public_text_helper_degrades_malformed_values(client) -> None:
    """畸形值（JSON `null` / 数字）不能让 `/meta` 变 500 或吐出 `"None"`。

    `system_settings.value` 是 `NOT NULL`，所以 JSON `null` / 数字只能在
    应用层直接喂给取值函数（真实触发路径是「有人手工改库」或旧数据）。
    """
    assert _public_text({TAGLINE_KEY: None}, TAGLINE_KEY) == TAGLINE
    assert _public_text({TAGLINE_KEY: 12345}, TAGLINE_KEY) == TAGLINE
    assert _public_text({}, TAGLINE_KEY) == TAGLINE, "缺 key 时回退代码默认值"
    assert _public_text({TAGLINE_KEY: ""}, TAGLINE_KEY) == "", "显式空串不该被默认值顶掉"
    assert _public_text({TAGLINE_KEY: "标语"}, TAGLINE_KEY) == "标语"
    # M12 那 5 项的默认值仍是空串，行为与改动前逐字一致
    for key in M12_KEYS:
        assert _public_text({}, key) == ""
        assert _public_text({key: None}, key) == ""
        assert _public_text({key: 1}, key) == ""

    response = await client.get(META)
    assert response.status_code == 200, response.text
    assert isinstance(response.json()[TAGLINE_FIELD], str)


async def test_meta_does_not_gain_a_version_setting(client) -> None:
    """§28.3：**版本号仍不是设置项** —— 页脚版本取既有的 `app_version`。"""
    items = {item["key"] for item in await _admin_items(client)}
    assert not [key for key in items if key.startswith("portal.") and "version" in key], (
        "站点定制不该引入版本号设置项"
    )
    body = (await client.get(META)).json()
    assert "app_version" in body and "api_version" in body, "既有版本字段必须还在"


# ===========================================================================
# C1/C3 · 管理端零改动就会出现标语编辑器（§27.1 事实 1 的后端侧证据）
# ===========================================================================
async def test_admin_settings_exposes_tagline_with_metadata(client) -> None:
    """`GET /admin/settings` 下发第 6 项及其控件元信息，默认值就是原标语。"""
    items = {item["key"]: item for item in await _admin_items(client)}
    assert TAGLINE_KEY in items, f"管理端设置清单缺少 {TAGLINE_KEY}"
    item = items[TAGLINE_KEY]
    value_type, default, description = EXPECTED_SIX[TAGLINE_KEY]
    assert item["value_type"] == value_type
    assert item["description"] == description
    assert item["is_public"] is True
    async with SessionLocal() as session:
        row = await session.get(SystemSetting, TAGLINE_KEY)
    expected_value = row.value if row is not None else default
    assert item["value"] == expected_value
    # 字符串项不该带 options / min / max（响应里是别名 min / max）
    assert item["options"] is None
    assert item["min"] is None
    assert item["max"] is None


async def test_admin_can_write_tagline(client) -> None:
    """`PUT /admin/settings` 对第 6 项可用（审核权限之外的既有校验路径不变）。"""
    admin = await login(client, "admin")
    async with SessionLocal() as session:
        row = await session.get(SystemSetting, TAGLINE_KEY)
        original: Any = row.value if row is not None else TAGLINE
    try:
        response = await client.put(
            ADMIN_SETTINGS,
            json={"items": [{"key": TAGLINE_KEY, "value": "标语 A"}]},
            headers=auth(admin),
        )
        assert response.status_code == 200, response.text
        assert response.json()["warnings"] == []
        async with SessionLocal() as session:
            row = await session.get(SystemSetting, TAGLINE_KEY)
            assert row is not None
            assert row.value == "标语 A"
    finally:
        await _restore_tagline(original)
