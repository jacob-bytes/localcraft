"""CLI 测试。

`seed-demo` 会往数据库里插 8 个工具，会污染其它测试精确断言的计数，
因此这里**用子进程 + 独立的临时数据库**跑真实命令 ——
顺带把「发布包里的安装流程」中最关键的两条命令真正验证了一遍。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.api.public import M3_TOTAL_ENDPOINTS
from app.cli import cli
from app.core.security import password_strength_errors

BACKEND_DIR = Path(__file__).resolve().parents[1]

#: CLI 子进程使用的解释器 —— **当前解释器**，而不是写死的 `backend/.venv/bin/python`。
#:
#: 原先写死 venv 路径，导致这套测试**只在开发者本机建过 venv 时才跑得过**：
#: CI（setup-python 提供解释器，没有 .venv）与全新 clone 上全部直接
#: FileNotFoundError。而这条依赖从来不是测试的本意 —— 真正要守的不变量是
#: 「CLI 跑在与目标机一致的 Python 3.11 上」（README D34），
#: 这一点用当前解释器同样能断言，且在哪里、由谁安装都不影响。
PYTHON = Path(sys.executable)

runner = CliRunner()


def _plain(text: str) -> str:
    """去掉 ANSI 转义，并把折行/缩进展开成单行。

    Typer 用 Rich 渲染 help 面板，而 Rich 会**按终端宽度折行**、并按是否
    支持颜色决定要不要插转义序列 —— 这两点都随环境变化
    （CI 无 TTY、开发机终端宽度不同）。断言 help 内容之前先归一化，
    否则测试的成败会取决于终端几何，而不是 CLI 本身。
    """
    no_ansi = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    return re.sub(r"\s+", " ", no_ansi)


def _cli_env(db_path: Path, data_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
            "DATA_DIR": str(data_dir),
            "SECRET_KEY": "cli-test-secret-key-0123456789abcdef",
            "LOG_LEVEL": "ERROR",
            "API_DOCS_ENABLED": "false",
            # 固定终端几何与颜色：help 面板由 Rich 按宽度折行，
            # 不固定就会出现「本机过、CI 挂」而差异只是宽度不同。
            "COLUMNS": "200",
            "NO_COLOR": "1",
        }
    )
    return env


def _run(args: list[str], env: dict[str, str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(PYTHON), "-m", *args],
        cwd=str(BACKEND_DIR),
        env=env,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


@pytest.fixture(scope="module")
def cli_db(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, str]]:
    """独立迁移过的数据库 + 环境变量。"""
    root = tmp_path_factory.mktemp("cli-db")
    db_path = root / "cli.db"
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    env = _cli_env(db_path, data_dir)

    result = _run(["alembic", "upgrade", "head"], env)
    assert result.returncode == 0, result.stderr
    return db_path, env


def _query(db_path: Path, sql: str) -> list[tuple]:
    import sqlite3

    connection = sqlite3.connect(db_path)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# seed-demo（契约 §7）
# ---------------------------------------------------------------------------
def test_seed_demo_creates_contract_fixture(cli_db) -> None:
    db_path, env = cli_db

    first = _run(["app.cli", "seed-demo"], env)
    assert first.returncode == 0, first.stderr

    assert _query(db_path, "SELECT COUNT(*) FROM tools")[0][0] == 26
    assert _query(db_path, "SELECT COUNT(*) FROM tool_versions")[0][0] == 26
    assert _query(db_path, "SELECT COUNT(*) FROM categories")[0][0] == 4

    # 用户
    users = dict(
        _query(db_path, "SELECT username, must_change_password FROM users")
    )
    assert users["admin"] == 0, "admin 必须 must_change_password=false（契约 §7）"
    assert users["newbie"] == 1, "newbie 必须 must_change_password=true（契约 §7）"

    # 四种类型、四个分类都要覆盖
    # M5（契约 §14.6）：种子从 8 个扩到 26 个 —— 前 8 个是边界子集，另加 18 个普通工具。
    # 这条断言真正想保证的是「四种类型都有样本」，数量本身由上面的总数断言覆盖。
    by_type = dict(_query(db_path, "SELECT tool_type, COUNT(*) FROM tools GROUP BY tool_type"))
    assert set(by_type) == {"file", "skill", "prompt", "webapp"}, by_type
    assert sum(by_type.values()) == 26, by_type
    assert all(count >= 2 for count in by_type.values()), by_type
    assert _query(
        db_path, "SELECT COUNT(DISTINCT category_id) FROM tools"
    )[0][0] == 4

    # 状态与可见性
    assert _query(
        db_path,
        "SELECT COUNT(*) FROM tools WHERE status='approved' AND visibility='public' "
        "AND deleted_at IS NULL",
    )[0][0] == 26

    # 至少 2 个无封面
    assert _query(db_path, "SELECT COUNT(*) FROM tools WHERE cover_image_id IS NULL")[0][0] >= 2

    # 至少 1 个名称超过 40 字符
    assert _query(db_path, "SELECT MAX(LENGTH(name)) FROM tools")[0][0] > 40

    # 至少 1 个简介超过 3 行
    assert (
        _query(
            db_path,
            "SELECT MAX(LENGTH(summary) - LENGTH(REPLACE(summary, char(10), ''))) FROM tools",
        )[0][0]
        >= 3
    )

    # 每个工具都有当前版本
    assert _query(
        db_path, "SELECT COUNT(*) FROM tools WHERE current_version_id IS NULL"
    )[0][0] == 0

    # 每个工具都有标签
    assert _query(
        db_path,
        "SELECT COUNT(*) FROM tools t WHERE NOT EXISTS "
        "(SELECT 1 FROM tool_tags tt WHERE tt.tool_id = t.id)",
    )[0][0] == 0


def test_seed_demo_is_idempotent(cli_db) -> None:
    db_path, env = cli_db
    before = _query(
        db_path,
        "SELECT COUNT(*), SUM(download_count), SUM(view_count) FROM tools",
    )[0]

    second = _run(["app.cli", "seed-demo"], env)
    assert second.returncode == 0, second.stderr
    assert "0 个新建" in second.stdout

    after = _query(
        db_path,
        "SELECT COUNT(*), SUM(download_count), SUM(view_count) FROM tools",
    )[0]
    assert before == after, "重复执行 seed-demo 不能改变数据"


def test_seed_demo_random_values_are_deterministic(cli_db, tmp_path_factory) -> None:
    """固定随机种子 → 两次全新播种的结果必须完全一致（契约 §7）。"""
    root = tmp_path_factory.mktemp("cli-db-2")
    db_path = root / "cli2.db"
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    env = _cli_env(db_path, data_dir)
    assert _run(["alembic", "upgrade", "head"], env).returncode == 0
    assert _run(["app.cli", "seed-demo"], env).returncode == 0

    source_db, _ = cli_db
    query = "SELECT slug, download_count, view_count FROM tools ORDER BY slug"
    assert _query(db_path, query) == _query(source_db, query)


def test_seed_demo_populates_search_index(cli_db) -> None:
    db_path, _ = cli_db
    assert _query(db_path, "SELECT COUNT(*) FROM tool_search_index")[0][0] == 26


# ---------------------------------------------------------------------------
# create-superadmin / reset-password / list-users
# ---------------------------------------------------------------------------
def test_create_superadmin_from_stdin(cli_db) -> None:
    db_path, env = cli_db
    result = _run(
        [
            "app.cli",
            "create-superadmin",
            "--username",
            "opsboss",
            "--email",
            "opsboss@example.com",
            "--password-stdin",
        ],
        env,
        input_text="Sup3rSecret!Pass\nSup3rSecret!Pass\n",
    )
    assert result.returncode == 0, result.stderr + result.stdout

    rows = _query(
        db_path,
        "SELECT u.username, u.must_change_password, r.code FROM users u "
        "JOIN user_roles ur ON ur.user_id = u.id JOIN roles r ON r.id = ur.role_id "
        "WHERE u.username='opsboss'",
    )
    assert rows == [("opsboss", 1, "superadmin")], "新建超管必须强制首次改密"

    # `--password` 明文选项必须不存在。
    #
    # 这一条才是真正的不变量：它检查 CLI **拒绝**明文口令参数。
    # 注意上面已经用 `--password-stdin` 真实建出了一个超管并校验了库里的行，
    # 所以「`--password-stdin` 存在」这件事本身**已被更强的证据覆盖**了 ——
    # 原先额外去断言 help 文本里出现该字符串，是在检查 Rich 的排版，
    # 而排版随终端几何与颜色支持变化（CI 上就因此挂过一次）。
    # 保留为「弱断言 + 归一化」：只要求选项名出现在 help 里，
    # 不要求它落在某一行、某一种样式下。
    help_result = _run(["app.cli", "create-superadmin", "--help"], env)
    help_text = _plain(help_result.stdout)
    if "--password-stdin" not in help_text:
        # 把足以复现渲染差异的信息一次性带出来，避免再来一轮盲猜。
        raise AssertionError(
            "help 里没找到 --password-stdin。"
            f"\n  returncode={help_result.returncode}"
            f"\n  stdout 长度={len(help_result.stdout)}  stderr 长度={len(help_result.stderr)}"
            f"\n  COLUMNS={env.get('COLUMNS')!r} NO_COLOR={env.get('NO_COLOR')!r}"
            f" LINES={env.get('LINES')!r} TERM={env.get('TERM')!r}"
            f"\n  归一化后（前 2000 字符）：\n{help_text[:2000]}"
            f"\n  原始 repr（前 600 字符）：\n{help_result.stdout[:600]!r}"
        )

    plain = _run(
        ["app.cli", "create-superadmin", "--username", "x", "--password", "Plain12345!"],
        env,
    )
    assert plain.returncode != 0, "不应该存在 --password 明文选项"


def test_create_superadmin_rejects_duplicate(cli_db) -> None:
    _, env = cli_db
    result = _run(
        ["app.cli", "create-superadmin", "--username", "opsboss", "--password-stdin"],
        env,
        input_text="Sup3rSecret!Pass\nSup3rSecret!Pass\n",
    )
    assert result.returncode != 0
    assert "已存在" in result.stdout + result.stderr


def test_create_superadmin_rejects_mismatched_stdin(cli_db) -> None:
    _, env = cli_db
    result = _run(
        ["app.cli", "create-superadmin", "--username", "someone", "--password-stdin"],
        env,
        input_text="Sup3rSecret!Pass\nDifferent!Pass123\n",
    )
    assert result.returncode != 0
    assert "不一致" in result.stdout + result.stderr


def test_reset_password_sets_must_change(cli_db) -> None:
    db_path, env = cli_db
    result = _run(
        ["app.cli", "reset-password", "admin", "--password-stdin"],
        env,
        input_text="Res3t!Password\nRes3t!Password\n",
    )
    assert result.returncode == 0, result.stderr + result.stdout

    row = _query(
        db_path,
        "SELECT must_change_password, failed_login_count, locked_until FROM users "
        "WHERE username='admin'",
    )[0]
    assert row[0] == 1
    assert row[1] == 0
    assert row[2] is None


def test_reset_password_unknown_user_fails(cli_db) -> None:
    _, env = cli_db
    result = _run(
        ["app.cli", "reset-password", "ghost", "--password-stdin"],
        env,
        input_text="Res3t!Password\nRes3t!Password\n",
    )
    assert result.returncode != 0
    assert "不存在" in result.stdout + result.stderr


def test_list_users_shows_roles_and_storage(cli_db) -> None:
    _, env = cli_db
    result = _run(["app.cli", "list-users"], env)
    assert result.returncode == 0, result.stderr
    assert "admin" in result.stdout
    assert "superadmin" in result.stdout
    assert "newbie" in result.stdout
    assert "STORAGE" in result.stdout

    filtered = _run(["app.cli", "list-users", "--status", "disabled"], env)
    assert filtered.returncode == 0


# ---------------------------------------------------------------------------
# export-openapi（监控方做契约比对用）
# ---------------------------------------------------------------------------
def test_export_openapi_writes_all_frozen_paths(tmp_path) -> None:
    output = tmp_path / "openapi.json"
    result = runner.invoke(cli, ["export-openapi", "--output", str(output)])
    assert result.exit_code == 0, result.output
    assert output.is_file()

    spec = json.loads(output.read_text(encoding="utf-8"))
    operations = {
        (method.upper(), path)
        for path, item in spec["paths"].items()
        for method in item
        if method in ("get", "post", "put", "patch", "delete")
    }
    # M1 的 12 + M2 的 38 + M3 的 42 = 92（契约 §6.2）
    # + M6 的 /directory（§20.4③）= 93；+ M8 的 6 个（§23.4）= 99
    assert operations == set(M3_TOTAL_ENDPOINTS), (
        f"多出: {sorted(operations - set(M3_TOTAL_ENDPOINTS))}\n"
        f"缺失: {sorted(set(M3_TOTAL_ENDPOINTS) - operations)}"
    )
    assert len(operations) == 99  # 93 + M8 的 6 个（CONTRACT §23.4）
    assert ("GET", "/api/v1/tools") in operations
    assert ("POST", "/api/v1/auth/refresh") in operations


def test_export_openapi_default_path_is_backend_dir() -> None:
    import app.cli as cli_module

    # 默认输出路径必须落在 backend/ 下（监控方按这个路径取产物）
    assert cli_module.BASE_DIR / "openapi.json" == BACKEND_DIR / "openapi.json"


# ---------------------------------------------------------------------------
# health / gen-password
# ---------------------------------------------------------------------------
def test_health_command(cli_db) -> None:
    _, env = cli_db
    result = _run(["app.cli", "health"], env)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "数据库可读" in result.stdout
    assert "数据目录可写" in result.stdout


def test_gen_password_produces_strong_password() -> None:
    result = runner.invoke(cli, ["gen-password"])
    assert result.exit_code == 0
    candidate = result.stdout.strip()
    assert password_strength_errors(candidate) == []


def test_cli_help_lists_m1_commands() -> None:
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    for command in (
        "create-superadmin",
        "reset-password",
        "list-users",
        "seed-demo",
        "export-openapi",
    ):
        assert command in result.stdout


def test_cli_python_is_311() -> None:
    """CLI 子进程用的解释器必须是 3.11（README D34）。

    注意断言的是**运行测试的这个解释器**，不是某个固定的 venv 路径 ——
    目标机 openEuler 自带 3.11，CI 也用 3.11，这里守的是同一件事。
    """
    assert PYTHON.is_file(), f"未找到解释器: {PYTHON}"
    result = subprocess.run(
        [str(PYTHON), "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.stdout.strip() == "3.11"
    assert sys.version_info[:2] == (3, 11)


# ---------------------------------------------------------------------------
# 回归：全新机器上「数据库父目录不存在」时迁移必须能自愈
# ---------------------------------------------------------------------------
def test_alembic_creates_missing_parent_directory(tmp_path) -> None:
    """SQLite 的 connect 不会自动建父目录，而迁移是部署的第一步。

    没修之前，在干净仓库上执行 `alembic upgrade head` 会直接报
    `unable to open database file` —— 必须由 `ensure_runtime_dirs()` 兜住。
    """
    nested_db = tmp_path / "not" / "created" / "yet" / "localcraft.db"
    assert not nested_db.parent.exists()

    data_dir = tmp_path / "also-missing-data-dir"
    env = _cli_env(nested_db, data_dir)
    result = _run(["alembic", "upgrade", "head"], env)

    assert result.returncode == 0, result.stderr
    assert nested_db.is_file(), "迁移后数据库文件应该已创建"
    assert data_dir.is_dir(), "DATA_DIR 应该被自动创建"
    assert _query(nested_db, "SELECT COUNT(*) FROM roles")[0][0] == 4
    assert _query(nested_db, "SELECT COUNT(*) FROM system_settings")[0][0] == 25
