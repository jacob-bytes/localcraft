"""M14 · B4：导入 / 恢复的**终态检查**（`contracts/CONTRACT.md` §29.6，冻结）。

背景（§26.3）：`import_export_service._replace_roles`（CSV 导入 / 从备份恢复）
**整体替换**角色，不经过 `_cas_guard_last_superadmin` —— 于是可以把最后一个超管
降级，系统从此没人能进管理台。

裁定（§29.6）：**不逐步加守卫**，改为**在导入事务提交前做一次终态检查** ——
「本次导入后活跃超管数为 0」→ 整体回滚并报错。

为什么不套用「不能少于当前」的守卫：合法的「从备份恢复」**必须**允许超管数
低于当前值（备份里可能就只有 1 个超管）。要堵的只是「恢复完一个超管都没有」
—— 那是把自己锁在门外。

本文件的两条主用例（任务书要求）：

1. 构造「只有一个超管、且该超管被降级」的导入数据 → 断言**整体回滚**且库里仍有超管；
2. 构造正常数据 → 断言仍然成功。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.errors import LastSuperadminError
from app.db.session import SessionLocal
from app.models.enums import RoleCode, UserStatus
from app.models.user import Role, User, UserRole
from app.repositories import users as users_repo
from app.services import import_export_service
from tests.conftest import auth, login

CSV_HEADER = "username,display_name,email,roles,password\n"


def _csv(*rows: str) -> bytes:
    return (CSV_HEADER + "".join(row + "\n" for row in rows)).encode()


async def test_import_that_demotes_the_only_superadmin_rolls_back(client, seeded) -> None:
    """★ 终态检查：把唯一的活跃超管降级 → 整体回滚，库里仍然有超管。

    这条走 **HTTP**（`POST /admin/import/users`），因为要验证的不只是服务层
    返回值，还有「回滚真的发生」与「报的是 409 LAST_SUPERADMIN」。
    """
    admin = await login(client, "admin")

    # 前提：种子里 admin 是唯一的活跃超管（M14 的用例不做别的假设）
    async with SessionLocal() as session:
        before = await users_repo.count_active_superadmins(session)
        assert before >= 1

    # admin 在 CSV 里被降级成 user（on_conflict=update 才会替换角色），
    # 同时**新建一个普通用户** —— 用它证明「整体回滚」：这一行也不该留下。
    response = await client.post(
        "/api/v1/admin/import/users",
        files={
            "file": (
                "users.csv",
                _csv(
                    "admin,管理员被降级,admin@example.com,user,",
                    "m14rollback,回滚见证,m14rollback@example.com,user,",
                ),
                "text/csv",
            )
        },
        data={"dry_run": "false", "on_conflict": "update"},
        headers=auth(admin),
    )
    assert response.status_code == 409, response.text
    payload = response.json()
    assert payload["code"] == "LAST_SUPERADMIN", payload
    assert payload["details"]["active_superadmins_after_import"] == 0

    async with SessionLocal() as session:
        after = await users_repo.count_active_superadmins(session)
        assert after >= 1, "整体回滚失败：库里已经没有任何活跃超管"

        # admin 的角色**没有被改**：仍然是 superadmin
        admin_user = (
            await session.execute(select(User).where(User.username == "admin"))
        ).scalar_one()
        assert "superadmin" in admin_user.role_codes

        # 「整体」回滚的证据：导入里新建的那一行也不该存在
        created = (
            await session.execute(select(User).where(User.username == "m14rollback"))
        ).scalar_one_or_none()
        assert created is None, "回滚不彻底：导入里的新建用户留下来了"


async def test_import_that_demotes_one_of_two_superadmins_succeeds(client, seeded) -> None:
    """§29.6 的另一半：**允许**超管数下降（合法的从备份恢复），只要不为 0。

    临时造第二个活跃超管，然后把 admin 降级 → 导入应当**成功**，
    因为终态仍有 1 个活跃超管。这证明本轮的检查不是「不能少于当前」
    （那种守卫会错误地拒绝合法的恢复）。
    """
    admin = await login(client, "admin")

    async with SessionLocal() as session:
        role = (
            await session.execute(select(Role).where(Role.code == RoleCode.SUPERADMIN.value))
        ).scalar_one()
        backup = User(
            username="m14second",
            display_name="第二个超管",
            email="m14second@example.com",
            password_hash="x",  # 不用于登录
            status=UserStatus.ACTIVE.value,
        )
        session.add(backup)
        await session.flush()
        session.add(UserRole(user_id=backup.id, role_id=role.id))
        await session.commit()
        backup_id = backup.id

    try:
        response = await client.post(
            "/api/v1/admin/import/users",
            files={
                "file": (
                    "users.csv",
                    _csv("admin,管理员被降级,admin@example.com,user,"),
                    "text/csv",
                )
            },
            data={"dry_run": "false", "on_conflict": "update"},
            headers=auth(admin),
        )
        assert response.status_code == 200, response.text
        assert response.json()["succeeded"] == 1

        async with SessionLocal() as session:
            admin_user = (
                await session.execute(select(User).where(User.username == "admin"))
            ).scalar_one()
            assert "superadmin" not in admin_user.role_codes, "降级应当生效"
            assert await users_repo.count_active_superadmins(session) == 1
    finally:
        # 还原：admin 恢复超管、删掉临时超管（否则污染其它用例）
        async with SessionLocal() as session:
            role = (
                await session.execute(
                    select(Role).where(Role.code == RoleCode.SUPERADMIN.value)
                )
            ).scalar_one()
            admin_user = (
                await session.execute(select(User).where(User.username == "admin"))
            ).scalar_one()
            existing = await session.execute(
                select(UserRole).where(
                    UserRole.user_id == admin_user.id, UserRole.role_id == role.id
                )
            )
            if existing.scalar_one_or_none() is None:
                session.add(UserRole(user_id=admin_user.id, role_id=role.id))
            backup = await session.get(User, backup_id)
            if backup is not None:
                await session.delete(backup)
            await session.commit()


async def test_normal_import_still_succeeds(client, seeded) -> None:
    """正常导入（不碰超管）→ 成功，且服务层不再抛错。"""
    admin = await login(client, "admin")
    response = await client.post(
        "/api/v1/admin/import/users",
        files={
            "file": (
                "users.csv",
                _csv("m14normal,正常导入,m14normal@example.com,user,"),
                "text/csv",
            )
        },
        data={"dry_run": "false", "on_conflict": "skip"},
        headers=auth(admin),
    )
    assert response.status_code == 200, response.text
    assert response.json()["succeeded"] == 1

    async with SessionLocal() as session:
        created = (
            await session.execute(select(User).where(User.username == "m14normal"))
        ).scalar_one_or_none()
        assert created is not None
        assert await users_repo.count_active_superadmins(session) >= 1
        # 清理
        await session.delete(created)
        await session.commit()


async def test_service_level_rolls_back_without_http(seeded) -> None:
    """服务层直调：抛 `LastSuperadminError` **且**会话内未提交任何改动。

    这条不经过 HTTP，直接证明 §29.6 的「整体回滚」是在**服务层**完成的
    （HTTP 那条只证明响应码）。
    """
    async with SessionLocal() as session:
        before = await users_repo.count_active_superadmins(session)
        assert before >= 1
        try:
            await import_export_service.import_users_csv(
                session,
                content=_csv("admin,降级,admin@example.com,user,"),
                dry_run=False,
                on_conflict="update",
            )
        except LastSuperadminError:
            pass
        else:  # pragma: no cover - 不该发生
            raise AssertionError("应当抛 LastSuperadminError")

        # 服务层已经 rollback：同一个会话里再查，admin 仍是超管
        admin_user = (
            await session.execute(select(User).where(User.username == "admin"))
        ).scalar_one()
        assert "superadmin" in admin_user.role_codes
        assert await users_repo.count_active_superadmins(session) >= 1


async def test_dry_run_never_triggers_the_terminal_check(client, seeded) -> None:
    """`dry_run=true` 本就不写库 → 不该因为终态检查而报错。

    （检查只发生在「要提交」的分支里，见 `import_users_csv`。）
    """
    admin = await login(client, "admin")
    response = await client.post(
        "/api/v1/admin/import/users",
        files={
            "file": (
                "users.csv",
                _csv("admin,降级预演,admin@example.com,user,"),
                "text/csv",
            )
        },
        data={"dry_run": "true", "on_conflict": "update"},
        headers=auth(admin),
    )
    assert response.status_code == 200, response.text
    assert response.json()["dry_run"] is True


async def test_cli_import_reports_rollback_and_exits_nonzero(tmp_path, seeded) -> None:
    """CLI（`python -m app.cli import-users`）也要给出**非零退出码**而非 traceback。

    离线导入是运维在服务器上执行的路径（FR-API-14），它没有 HTTP 信封可以承载
    `LAST_SUPERADMIN` —— 所以 `cli._import_users` 捕获该异常、打印一句话并以
    `typer.Exit(1)` 结束（脚本据此失败）。
    """
    import typer

    from app.cli import _import_users

    csv_path = tmp_path / "users.csv"
    csv_path.write_bytes(_csv("admin,降级,admin@example.com,user,"))

    with pytest.raises(typer.Exit) as excinfo:
        await _import_users(csv_path, False, "update")
    assert excinfo.value.exit_code == 1

    async with SessionLocal() as session:
        assert await users_repo.count_active_superadmins(session) >= 1, (
            "CLI 路径也必须整体回滚"
        )
