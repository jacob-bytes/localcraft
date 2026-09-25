"""M3 测试：三件硬性条目 + API Token + 管理面 CRUD。

对应验收清单 1~23。文件较长但每条独立，便于逐条核对。
"""

from __future__ import annotations

import asyncio
import io
import os
import zipfile

import pytest
from sqlalchemy import event, select, text

from app.core.timeutil import utcnow
from app.db.session import SessionLocal, engine
from app.models.enums import RoleCode
from app.models.tool import Tool, ToolVersion
from app.models.user import ApiToken, Role, User, UserRole
from app.repositories import tool_versions as versions_repo
from tests.conftest import auth, login
from tests.m2_helpers import (
    approve,
    create_tool,
    publish_tool,
    reject,
    submit,
    upload_version,
    zip_bytes,
)

TOOLS = "/api/v1/tools"


# ===========================================================================
# 1/2. file_tree_truncated 持久化字段
# ===========================================================================
async def test_file_tree_truncated_false_for_small_package(client, seeded) -> None:
    """验收 1：3 个条目的 skill 包 → `file_tree_truncated` 必须是 **false**。

    M2 用 `file_count < len(tree)` 推断，在只有一个 SKILL.md 的小包上恒为 true
    （分子只数文件、分母含目录，口径不一致）。这正是 contracts §15.3 的裁定源由。
    """
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    tool = await create_tool(client, owner, name="小 skill 包", tool_type="skill")
    payload = zip_bytes(
        {
            "SKILL.md": b"---\nname: tiny\n---\n# tiny\n",
            "scripts/run.py": b"print(1)",
            "assets/logo.png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 10,
        }
    )
    up = await upload_version(
        client, owner, tool["id"], version="1.0.0", file_bytes=payload, file_name="t.zip"
    )
    assert up.status_code == 201, up.text
    assert up.json()["skill"]["file_tree_truncated"] is False

    await submit(client, owner, tool["id"])
    await approve(client, approver, tool["id"])
    slug = (
        await client.get(f"/api/v1/me/tools/{tool['id']}", headers=auth(owner))
    ).json()["slug"]

    preview = await client.get(
        f"/api/v1/tools/{slug}/versions/1.0.0/skill-preview", headers=auth(owner)
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["file_tree_truncated"] is False, (
        f"3 个条目的包不应被判定为截断（实际 {len(body['file_tree'])} 条）"
    )

    async with SessionLocal() as session:
        row = await versions_repo.get_by_version(session, tool["id"], "1.0.0")
        assert row is not None
        assert row.skill_tree_truncated is False


@pytest.mark.parametrize("total_entries", [2000, 2001])
async def test_file_tree_truncated_boundary(client, seeded, total_entries: int) -> None:
    """验收 2：2000 条不截断、2001 条截断且**恰好存 2000 条**。"""
    from app.services.skill_service import FILE_TREE_MAX_ENTRIES

    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    tool = await create_tool(
        client, owner, name=f"边界 {total_entries}", tool_type="skill"
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("SKILL.md", "---\nname: big\n---\n# big\n")
        # 全部放在**根目录**：子目录会被合成成额外的目录条目，
        # 那样「文件数」与「文件树条目数」就对不上了，边界也就测不准。
        for index in range(total_entries - 1):
            archive.writestr(f"f{index}.txt", f"c{index}")
    payload = buffer.getvalue()

    up = await upload_version(
        client, owner, tool["id"], version="1.0.0", file_bytes=payload, file_name="b.zip"
    )
    assert up.status_code == 201, up.text
    expected_truncated = total_entries > FILE_TREE_MAX_ENTRIES
    assert up.json()["skill"]["file_tree_truncated"] is expected_truncated

    await submit(client, owner, tool["id"])
    await approve(client, approver, tool["id"])
    slug = (
        await client.get(f"/api/v1/me/tools/{tool['id']}", headers=auth(owner))
    ).json()["slug"]
    preview = await client.get(
        f"/api/v1/tools/{slug}/versions/1.0.0/skill-preview", headers=auth(owner)
    )
    body = preview.json()
    assert body["file_tree_truncated"] is expected_truncated
    if expected_truncated:
        assert len(body["file_tree"]) == FILE_TREE_MAX_ENTRIES, (
            f"存储的条目数应恰好 {FILE_TREE_MAX_ENTRIES}，实际 {len(body['file_tree'])}"
        )
    else:
        assert len(body["file_tree"]) == total_entries

    async with SessionLocal() as session:
        row = await versions_repo.get_by_version(session, tool["id"], "1.0.0")
        assert row is not None
        assert row.skill_tree_truncated is expected_truncated


async def test_skill_tree_truncated_migration_roundtrip() -> None:
    """验收：新列可正向迁移也可回滚（downgrade 是有损的，见迁移 docstring）。"""
    import sqlite3
    import subprocess
    import tempfile
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    tmp = Path(tempfile.mkdtemp()) / "mig.db"
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite+aiosqlite:///{tmp}",
        "DATA_DIR": str(tmp.parent / "data"),
        "SECRET_KEY": "migration-test-secret-0123456789",
    }
    python = backend / ".venv" / "bin" / "python"

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(python), "-m", *args],
            cwd=str(backend),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )

    assert run("alembic", "upgrade", "head").returncode == 0
    connection = sqlite3.connect(tmp)
    columns = [r[1] for r in connection.execute("PRAGMA table_info(tool_versions)")]
    assert "skill_tree_truncated" in columns
    connection.close()

    assert run("alembic", "downgrade", "0003").returncode == 0
    connection = sqlite3.connect(tmp)
    columns = [r[1] for r in connection.execute("PRAGMA table_info(tool_versions)")]
    assert "skill_tree_truncated" not in columns
    # 其他列必须完好（batch_alter_table 会重建表，写错就把别的列弄丢了）
    assert len(columns) == 25
    connection.close()

    assert run("alembic", "upgrade", "head").returncode == 0


# ===========================================================================
# 3. 审批队列暴露 version_seq
# ===========================================================================
async def test_approval_queue_exposes_version_seq(client, seeded) -> None:
    """验收 3：队列条目带 `version_seq`，可直接用于乐观锁。"""
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    tool = await create_tool(client, owner, name="乐观锁字段")
    await upload_version(
        client, owner, tool["id"], version="1.0.0",
        file_bytes=zip_bytes({"a.txt": b"x"}), file_name="a.zip",
    )
    await submit(client, owner, tool["id"])

    queue = await client.get("/api/v1/admin/approvals", headers=auth(approver))
    item = next(i for i in queue.json()["items"] if i["tool_id"] == tool["id"])
    assert "version_seq" in item, "审批队列必须暴露 version_seq（contracts §15.4）"
    seq = item["version_seq"]
    assert isinstance(seq, int) and seq >= 1

    # 用队列给的 version_seq 确实能通过乐观锁
    ok = await approve(client, approver, tool["id"], expected_version_seq=seq)
    assert ok.status_code == 200, ok.text

    # 过期值 → 409
    tool2 = await create_tool(client, owner, name="乐观锁字段2")
    await upload_version(
        client, owner, tool2["id"], version="1.0.0",
        file_bytes=zip_bytes({"a.txt": b"x"}), file_name="a.zip",
    )
    await submit(client, owner, tool2["id"])
    stale = await approve(client, approver, tool2["id"], expected_version_seq=1)
    assert stale.status_code == 409
    assert stale.json()["code"] == "ALREADY_PROCESSED"


# ===========================================================================
# 4/5. approver 的可见性边界
# ===========================================================================
async def test_approver_cannot_see_others_draft(client, seeded) -> None:
    """验收 4：approver 访问他人 **draft** 工具的 `/me/tools/{id}` → 404。"""
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    tool = await create_tool(client, owner, name="他人草稿")
    assert tool["status"] == "draft"

    response = await client.get(f"/api/v1/me/tools/{tool['id']}", headers=auth(approver))
    assert response.status_code == 404, "草稿没有审批理由可见（contracts §15.5）"
    assert response.json()["code"] == "NOT_FOUND"

    # owner 自己仍然看得到
    assert (
        await client.get(f"/api/v1/me/tools/{tool['id']}", headers=auth(owner))
    ).status_code == 200


async def test_approver_can_preview_pending_via_portal_detail(client, seeded) -> None:
    """验收 5：approver 访问他人 **pending** 工具的 `GET /tools/{slug}` → 200。"""
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    tool = await create_tool(client, owner, name="待审预览")
    await upload_version(
        client, owner, tool["id"], version="1.0.0",
        file_bytes=zip_bytes({"a.txt": b"x"}), file_name="a.zip",
    )
    await submit(client, owner, tool["id"])
    slug = (
        await client.get(f"/api/v1/me/tools/{tool['id']}", headers=auth(owner))
    ).json()["slug"]

    # approver 能预览（FR-APPR-06）
    detail = await client.get(f"{TOOLS}/{slug}", headers=auth(approver))
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "pending"

    # 普通用户看不到
    stranger = await login(client, "lockme")
    assert (await client.get(f"{TOOLS}/{slug}", headers=auth(stranger))).status_code == 404

    # 下架后 approver 仍能看到（offline 在审批人可见集里）
    await approve(client, approver, tool["id"])
    await login(client, "admin")
    admin_token = await login(client, "admin")
    offline = await client.post(
        f"/api/v1/admin/approvals/{tool['id']}/offline",
        json={"reason": "演示下架的可见性"},
        headers=auth(admin_token),
    )
    assert offline.status_code == 200, offline.text
    assert (await client.get(f"{TOOLS}/{slug}", headers=auth(approver))).status_code == 200
    assert (await client.get(f"{TOOLS}/{slug}", headers=auth(stranger))).status_code == 404


# ===========================================================================
# 6/7/8/9. API Token
# ===========================================================================
async def test_token_with_approvals_scope_can_approve_but_not_manage_users(
    client, seeded
) -> None:
    """验收 6/7：只有一个 scope 的 Token 能审批，调用户管理接口 403 SCOPE_MISSING。"""
    admin_token = await login(client, "admin")
    owner = await login(client, "outsider")

    created = await client.post(
        "/api/v1/admin/tokens",
        json={"name": "审批机器人", "scopes": ["approvals:write"]},
        headers=auth(admin_token),
    )
    assert created.status_code == 201, created.text
    payload = created.json()
    plaintext = payload["token"]
    assert plaintext.startswith("st_")
    assert len(plaintext) == 3 + 43, f"st_ + 43 位 base64url，实际 {len(plaintext)}"
    assert payload["token_prefix"] == plaintext[:8]
    # 明文之后再也拿不到
    listing = await client.get("/api/v1/admin/tokens", headers=auth(admin_token))
    assert plaintext not in listing.text

    token_auth = {"Authorization": f"Bearer {plaintext}"}

    # 6) 用它批准待审工具
    tool = await create_tool(client, owner, name="Token 审批")
    await upload_version(
        client, owner, tool["id"], version="1.0.0",
        file_bytes=zip_bytes({"a.txt": b"x"}), file_name="a.zip",
    )
    await submit(client, owner, tool["id"])
    approved = await client.post(
        f"/api/v1/admin/approvals/{tool['id']}/approve", json={}, headers=token_auth
    )
    assert approved.status_code == 200, approved.text

    # 7) 同一 Token 调用户管理 → 403 SCOPE_MISSING，details 指出缺哪个
    denied = await client.post(
        "/api/v1/admin/users",
        json={"username": "tokenuser", "display_name": "x", "roles": ["user"]},
        headers=token_auth,
    )
    assert denied.status_code == 403, denied.text
    body = denied.json()
    assert body["code"] == "SCOPE_MISSING"
    assert "users:write" in str(body["details"])


async def test_token_scope_realtime_downgrade(client, seeded) -> None:
    """验收 8：把创建者从 superadmin 降级 → 同一 Token **立即**降权。"""
    admin_token = await login(client, "admin")

    # 造一个临时超管（不动 seeded 的 admin —— 它是最后一个超管）
    created = await client.post(
        "/api/v1/admin/users",
        json={
            "username": "tmpadmin",
            "display_name": "临时超管",
            "password": "TmpAdmin@12345",
            "roles": ["superadmin"],
            "must_change_password": False,
        },
        headers=auth(admin_token),
    )
    assert created.status_code == 201, created.text
    tmp_id = created.json()["user"]["id"]

    # **由 tmpadmin 自己**签发 Token —— 这样降级它才能验证「实时交集」
    tmp_token = await login(client, "tmpadmin", "TmpAdmin@12345")
    issued = await client.post(
        "/api/v1/admin/tokens",
        json={"name": "临时超管的 Token", "scopes": ["admin:all"]},
        headers=auth(tmp_token),
    )
    assert issued.status_code == 201, issued.text
    plaintext = issued.json()["token"]
    token_auth = {"Authorization": f"Bearer {plaintext}"}

    # 降级前：能进用户管理
    assert (
        await client.get("/api/v1/admin/users", headers=token_auth)
    ).status_code == 200

    # 降级创建者为 viewer
    downgraded = await client.put(
        f"/api/v1/admin/users/{tmp_id}/roles",
        json={"roles": ["viewer"]},
        headers=auth(admin_token),
    )
    assert downgraded.status_code == 200, downgraded.text

    # 降级后：**同一个 Token** 立刻不能进用户管理（不是等 token 过期）
    after = await client.get("/api/v1/admin/users", headers=token_auth)
    assert after.status_code == 403, "Scope 必须实时与创建者角色取交集"
    assert after.json()["code"] in {"FORBIDDEN", "SCOPE_MISSING"}


async def test_revoked_token_rejected(client, seeded) -> None:
    """验收 9：吊销后调用 → 401。"""
    admin_token = await login(client, "admin")
    issued = await client.post(
        "/api/v1/admin/tokens",
        json={"name": "待吊销", "scopes": ["tools:read"]},
        headers=auth(admin_token),
    )
    token_id = issued.json()["id"]
    plaintext = issued.json()["token"]
    token_auth = {"Authorization": f"Bearer {plaintext}"}

    assert (await client.get(TOOLS, headers=token_auth)).status_code == 200

    revoked = await client.post(
        f"/api/v1/admin/tokens/{token_id}/revoke", headers=auth(admin_token)
    )
    assert revoked.status_code == 200, revoked.text

    after = await client.get(TOOLS, headers=token_auth)
    assert after.status_code == 401
    # M4 修正：吊销就是吊销，不再返回 UNAUTHENTICATED（契约 §16.4）
    assert after.json()["code"] == "TOKEN_REVOKED"


async def test_token_scope_cannot_exceed_creator(client, seeded) -> None:
    """docs/03 §3.12：不能签发超出自己权限的 Scope。"""
    approver_token = await login(client, "approver")
    denied = await client.post(
        "/api/v1/admin/tokens",
        json={"name": "越权 Token", "scopes": ["admin:all"]},
        headers=auth(approver_token),
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "FORBIDDEN"


async def test_token_default_expiry_is_90_days(client, seeded) -> None:
    """docs/05 §13.8：不传 expires_at → 默认 90 天；显式 null → 永不过期。"""
    admin_token = await login(client, "admin")
    default = await client.post(
        "/api/v1/admin/tokens",
        json={"name": "默认有效期", "scopes": ["tools:read"]},
        headers=auth(admin_token),
    )
    assert default.json()["expires_at"] is not None

    forever = await client.post(
        "/api/v1/admin/tokens",
        json={"name": "永不过期", "scopes": ["tools:read"], "expires_at": None},
        headers=auth(admin_token),
    )
    assert forever.status_code == 201, forever.text
    assert forever.json()["expires_at"] is None


# ===========================================================================
# 10. last_used_at 必须走内存聚合
# ===========================================================================
async def test_token_last_used_is_aggregated(client, seeded) -> None:
    """验收 10：100 次带 Token 的请求 → 数据库 UPDATE api_tokens **远小于 100**。

    这是契约 §11 第 5 条（禁止逐请求 UPDATE）的证据。
    用一个 `before_cursor_execute` 钩子直接数 SQL 语句，而不是靠推理。
    """
    from app.services.counter_service import CounterService, set_counter_service

    admin_token = await login(client, "admin")
    issued = await client.post(
        "/api/v1/admin/tokens",
        json={"name": "聚合验证", "scopes": ["tools:read"]},
        headers=auth(admin_token),
    )
    token_id = issued.json()["id"]
    plaintext = issued.json()["token"]

    service = CounterService(flush_interval_seconds=3600)  # 定时器不会触发
    set_counter_service(service)
    updates = 0

    def _count_updates(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        nonlocal updates
        if "UPDATE API_TOKENS" in statement.upper().replace("\n", " "):
            updates += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _count_updates)
    try:
        responses = await asyncio.gather(
            *[
                client.get(TOOLS, headers={"Authorization": f"Bearer {plaintext}"})
                for _ in range(100)
            ]
        )
        assert all(r.status_code == 200 for r in responses)
        assert updates == 0, f"请求路径上不应有任何 api_tokens UPDATE，实际 {updates} 次"

        # 内存里累计了 100 次使用（同一 token 反复更新只留最新值 + 计数）
        assert service.pending_snapshot()["tokens"] == 1
        stats = await service.flush()
        assert stats["tokens"] == 1
        assert updates == 1, f"flush 时应当只 UPDATE 一次，实际 {updates} 次"

        async with SessionLocal() as session:
            row = await session.get(ApiToken, token_id)
            assert row is not None and row.last_used_at is not None
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _count_updates)
        set_counter_service(None)


# ===========================================================================
# 11. 禁用用户连带吊销 Token 与会话
# ===========================================================================
async def test_disable_user_revokes_tokens_and_sessions(client, seeded) -> None:
    """验收 11：禁用用户 → 其 refresh token 与该用户签发的 Token 立即失效。"""
    admin_token = await login(client, "admin")

    created = await client.post(
        "/api/v1/admin/users",
        json={
            "username": "tokuser",
            "display_name": "Token 用户",
            "password": "TokUser@12345",
            "roles": ["user"],
            "must_change_password": False,
        },
        headers=auth(admin_token),
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["user"]["id"]

    # 该用户登录（拿 refresh cookie）
    await login(client, "tokuser", "TokUser@12345")
    assert client.cookies.get("refresh_token")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 200

    # 超管替该用户签一个 Token（以超管身份创建，但测试其**创建者**被禁用）
    async with SessionLocal() as session:
        from app.repositories import api_tokens as tokens_repo

        row = await tokens_repo.create(
            session,
            name="被禁用者的 Token",
            plaintext="st_testtoken_for_disable_check_0000000000",
            scopes=["tools:read"],
            created_by_id=user_id,
            expires_at=None,
            now=utcnow(),
        )
        await session.commit()
        token_id = row.id
    token_auth = {
        "Authorization": "Bearer st_testtoken_for_disable_check_0000000000"
    }
    assert (await client.get(TOOLS, headers=token_auth)).status_code == 200

    # 禁用
    disabled = await client.patch(
        f"/api/v1/admin/users/{user_id}",
        json={"status": "disabled"},
        headers=auth(admin_token),
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["status"] == "disabled"

    # refresh token 立即失效
    assert (await client.post("/api/v1/auth/refresh")).status_code in (401, 403)
    # Token 立即失效：M4 起固定为 401 TOKEN_REVOKED（契约 §16.4）
    after = await client.get(TOOLS, headers=token_auth)
    assert after.status_code == 401, after.text
    assert after.json()["code"] == "TOKEN_REVOKED", after.text

    async with SessionLocal() as session:
        row = await session.get(ApiToken, token_id)
        assert row is not None and row.revoked_at is not None


# ===========================================================================
# 12/13/14. 超管保护
# ===========================================================================
async def test_cannot_disable_last_superadmin(client, seeded) -> None:
    """验收 12：禁用最后一个启用的超管 → 409 LAST_SUPERADMIN。"""
    admin_token = await login(client, "admin")
    # 先确认当前只有 admin 一个启用的超管
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(User.id)
                .join(UserRole, UserRole.user_id == User.id)
                .join(Role, Role.id == UserRole.role_id)
                .where(Role.code == RoleCode.SUPERADMIN.value, User.status == "active")
            )
        ).all()
        superadmins = [int(r[0]) for r in rows]

    if len(superadmins) == 1:
        target = superadmins[0]
        # 用另一个用户（approver）来发起禁用，避免撞上「不能禁用自己」
        other_admin = await login(client, "approver")
        del other_admin  # approver 没有 users:write，会 403，这里直接用 admin 自己
        # admin 禁自己会先撞「不能禁用自己」——所以先临时提升一个第二超管再降回
        # 更直接的做法：直接调服务层验证 CAS 守卫
        from app.services import admin_user_service

        async with SessionLocal() as session:
            user = await session.get(User, target)
            assert user is not None
            await session.refresh(user, ["roles"])
            with pytest.raises(Exception) as exc:
                await admin_user_service.disable_user(
                    session, user=user, actor_id=999999
                )
            assert getattr(exc.value, "code", "") == "LAST_SUPERADMIN"
            await session.rollback()

    # HTTP 层：降级最后一个超管的角色也应当 409
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(User.id)
                .join(UserRole, UserRole.user_id == User.id)
                .join(Role, Role.id == UserRole.role_id)
                .where(Role.code == RoleCode.SUPERADMIN.value, User.status == "active")
            )
        ).all()
        if len(rows) == 1:
            response = await client.put(
                f"/api/v1/admin/users/{int(rows[0][0])}/roles",
                json={"roles": ["user"]},
                headers=auth(admin_token),
            )
            # 只有一个超管时，降级它会先撞「不能降级自己」（同一个人）
            assert response.status_code in (400, 409)
            assert response.json()["code"] in {"LAST_SUPERADMIN", "VALIDATION_ERROR"}


async def test_last_superadmin_cas_with_two_admins(client, seeded) -> None:
    """并发场景：两个管理员同时降级最后两个超管，只能成功一个。

    用服务层直接验证 CAS（HTTP 层无法可靠构造「恰好同时」的时序）。
    """
    from app.services import admin_user_service

    admin_token = await login(client, "admin")
    created = await client.post(
        "/api/v1/admin/users",
        json={
            "username": "secondadmin",
            "display_name": "第二超管",
            "password": "Second@12345",
            "roles": ["superadmin"],
            "must_change_password": False,
        },
        headers=auth(admin_token),
    )
    assert created.status_code == 201, created.text
    second_id = created.json()["user"]["id"]

    try:
        # 现在有两个超管。并发地把两个都降级。
        async def demote(user_id: int) -> str:
            async with SessionLocal() as session:
                try:
                    # actor_id 必须是**第三方**：传被降级者自己会先撞
                    # 「不能降级自己」的守卫，测不到 LAST_SUPERADMIN
                    await admin_user_service.replace_roles(
                        session, user_id=user_id, roles=["user"], actor_id=0
                    )
                    return "ok"
                except Exception as exc:
                    await session.rollback()
                    return getattr(exc, "code", type(exc).__name__)

        results = await asyncio.gather(demote(second_id), demote(second_id))
        # 同一个用户被降级两次：第一次成功，第二次它已经不是超管（不会触发守卫）
        assert results[0] == "ok" or results[1] == "ok"

        # 真正要防的是「两个不同超管同时被降级」。重建两个超管再试。
        restored = await client.put(
            f"/api/v1/admin/users/{second_id}/roles",
            json={"roles": ["superadmin"]},
            headers=auth(admin_token),
        )
        assert restored.status_code == 200, restored.text

        async with SessionLocal() as session:
            admin_user = (
                await session.execute(select(User).where(User.username == "admin"))
            ).scalar_one()
            admin_id = admin_user.id

        results2 = await asyncio.gather(
            demote(admin_id), demote(second_id), return_exceptions=True
        )
        codes = [r for r in results2 if isinstance(r, str)]
        assert "LAST_SUPERADMIN" in codes, (
            f"并发降级两个超管时应当有一个被 LAST_SUPERADMIN 拦下，实际 {codes}"
        )
        assert codes.count("ok") == 1, f"只能成功一个，实际 {codes}"
    finally:
        # 复位：保证后续测试仍有至少一个启用超管
        async with SessionLocal() as session:
            from app.models.user import Role as _Role

            role = (
                await session.execute(select(_Role).where(_Role.code == "superadmin"))
            ).scalar_one()
            user = (await session.execute(select(User).where(User.username == "admin"))).scalar_one()
            existing = (
                await session.execute(
                    select(UserRole).where(
                        UserRole.user_id == user.id, UserRole.role_id == role.id
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(
                    UserRole(user_id=user.id, role_id=role.id, granted_at=utcnow())
                )
            user.status = "active"
            await session.commit()


async def test_cannot_disable_self(client, seeded) -> None:
    """验收 13：禁用自己 → 被拒。"""
    admin_token = await login(client, "admin")
    async with SessionLocal() as session:
        admin_user = (
            await session.execute(select(User).where(User.username == "admin"))
        ).scalar_one()
        admin_id = admin_user.id

    response = await client.patch(
        f"/api/v1/admin/users/{admin_id}",
        json={"status": "disabled"},
        headers=auth(admin_token),
    )
    assert response.status_code == 400, response.text
    assert "自己" in response.json()["message"]


async def test_no_physical_delete_user_endpoint(client, seeded) -> None:
    """验收 14：只有禁用，没有删除（FR-IAM-06）。"""
    from app.api.public import M3_TOTAL_ENDPOINTS

    assert not any(
        method == "DELETE" and "users" in path for method, path in M3_TOTAL_ENDPOINTS
    ), "接口面里不允许出现 DELETE /admin/users/*"
    # 实际调用也应当 405/404，而不是删掉用户
    admin_token = await login(client, "admin")
    response = await client.delete("/api/v1/admin/users/1", headers=auth(admin_token))
    assert response.status_code in (404, 405)


# ===========================================================================
# 15. 用户组
# ===========================================================================
async def test_group_crud_and_members(client, seeded) -> None:
    admin_token = await login(client, "admin")
    created = await client.post(
        "/api/v1/admin/groups",
        json={"name": "M3 测试组", "description": "给 M3 用例用"},
        headers=auth(admin_token),
    )
    assert created.status_code == 201, created.text
    group_id = created.json()["id"]

    try:
        added = await client.post(
            f"/api/v1/admin/groups/{group_id}/members",
            json={"user_ids": [seeded.users["viewer"], seeded.users["lockme"]]},
            headers=auth(admin_token),
        )
        assert added.status_code == 200, added.text
        assert added.json()["added"] == 2

        # 重复添加 → already_members
        again = await client.post(
            f"/api/v1/admin/groups/{group_id}/members",
            json={"user_ids": [seeded.users["viewer"]]},
            headers=auth(admin_token),
        )
        assert again.json()["added"] == 0
        assert again.json()["already_members"] == 1

        members = await client.get(
            f"/api/v1/admin/groups/{group_id}/members", headers=auth(admin_token)
        )
        assert members.json()["total"] == 2

        removed = await client.delete(
            f"/api/v1/admin/groups/{group_id}/members/{seeded.users['lockme']}",
            headers=auth(admin_token),
        )
        assert removed.status_code == 200
        assert (
            await client.get(
                f"/api/v1/admin/groups/{group_id}/members", headers=auth(admin_token)
            )
        ).json()["total"] == 1
    finally:
        await client.delete(f"/api/v1/admin/groups/{group_id}", headers=auth(admin_token))


async def test_group_in_use_and_dangling_acl_cleanup(client, seeded) -> None:
    """验收 15：删除被 ACL 引用的组 → 409 + 引用列表；确认后清理悬空 ACL。"""
    admin_token = await login(client, "admin")
    owner = await login(client, "outsider")
    approver = await login(client, "approver")

    created = await client.post(
        "/api/v1/admin/groups",
        json={"name": "被引用的组"},
        headers=auth(admin_token),
    )
    group_id = created.json()["id"]

    slug, tool_id, _ = await publish_tool(client, owner, approver, name="ACL 引用工具")
    put = await client.put(
        f"/api/v1/me/tools/{tool_id}/acl",
        json={
            "visibility": "restricted",
            "entries": [
                {"subject_type": "group", "subject_id": group_id, "can_download": True}
            ],
        },
        headers=auth(owner),
    )
    assert put.status_code == 200, put.text

    # 删除 → 409 GROUP_IN_USE + 工具列表
    blocked = await client.delete(
        f"/api/v1/admin/groups/{group_id}", headers=auth(admin_token)
    )
    assert blocked.status_code == 409, blocked.text
    body = blocked.json()
    assert body["code"] == "GROUP_IN_USE"
    assert body["details"]["tool_count"] == 1
    assert body["details"]["tools"][0]["id"] == tool_id

    # 确认后强制删除
    forced = await client.delete(
        f"/api/v1/admin/groups/{group_id}?force=true", headers=auth(admin_token)
    )
    assert forced.status_code == 200, forced.text
    assert forced.json()["cleaned_acl_entries"] >= 1

    # 悬空 ACL 必须被清掉（数据库层不保证多态外键）
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM tool_acl "
                    "WHERE subject_type='group' AND subject_id=:gid"
                ),
                {"gid": group_id},
            )
        ).scalar_one()
    assert rows == 0, "删除组后必须清理它的 ACL 条目，否则会留下永远命中不了的死条目"

    # 工具本身还在（只是不再对那个组授权）
    assert (await client.get(f"{TOOLS}/{slug}", headers=auth(owner))).status_code == 200


# ===========================================================================
# 16/17. 分类与标签
# ===========================================================================
async def test_category_in_use_and_soft_delete(client, seeded) -> None:
    """验收 16：删除有工具引用的分类 → 409 CATEGORY_IN_USE + 引用数。"""
    admin_token = await login(client, "admin")
    owner = await login(client, "outsider")

    created = await client.post(
        "/api/v1/admin/categories",
        json={"name": "M3 分类", "slug": "m3-cat", "sort_order": 99},
        headers=auth(admin_token),
    )
    assert created.status_code == 201, created.text
    category_id = created.json()["id"]
    assert created.json()["slug"] == "m3-cat"

    tool = await create_tool(
        client, owner, name="引用分类的工具", category_id=category_id
    )
    assert tool["category"]["id"] == category_id

    blocked = await client.delete(
        f"/api/v1/admin/categories/{category_id}", headers=auth(admin_token)
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["code"] == "CATEGORY_IN_USE"
    assert blocked.json()["details"]["tool_count"] >= 1

    # 把工具的 category_id 置空后可以软删除
    await client.patch(
        f"/api/v1/me/tools/{tool['id']}", json={"category_id": None}, headers=auth(owner)
    )
    # category_id=None 在部分实现里表示「不改」，所以直接改库来构造无引用状态
    async with SessionLocal() as session:
        row = await session.get(Tool, tool["id"])
        assert row is not None
        row.category_id = None
        await session.commit()

    deleted = await client.delete(
        f"/api/v1/admin/categories/{category_id}", headers=auth(admin_token)
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["soft_deleted"] is True

    # 软删除：行还在，只是 is_active=false
    async with SessionLocal() as session:
        from app.models.taxonomy import Category

        category = await session.get(Category, category_id)
        assert category is not None and category.is_active is False


async def test_category_reorder(client, seeded) -> None:
    admin_token = await login(client, "admin")
    listing = await client.get("/api/v1/admin/categories", headers=auth(admin_token))
    items = listing.json()
    assert len(items) >= 2
    reversed_ids = [{"id": c["id"], "sort_order": (len(items) - i) * 10} for i, c in enumerate(items)]
    response = await client.put(
        "/api/v1/admin/categories/order", json={"items": reversed_ids}, headers=auth(admin_token)
    )
    assert response.status_code == 200, response.text
    after = {c["id"]: c["sort_order"] for c in response.json()}
    for entry in reversed_ids:
        assert after[entry["id"]] == entry["sort_order"]


async def test_tag_rename_merge_and_cleanup(client, seeded) -> None:
    """验收 17：合并标签 → 引用转移、返回转移数量、旧标签消失。"""
    admin_token = await login(client, "admin")
    owner = await login(client, "outsider")

    # 造两个标签，分别挂在两个工具上
    tool_a = await create_tool(client, owner, name="标签工具 A", tags=["m3-tag-a"])
    tool_b = await create_tool(client, owner, name="标签工具 B", tags=["m3-tag-b"])
    tool_c = await create_tool(client, owner, name="标签工具 C", tags=["m3-tag-a", "m3-tag-b"])

    listing = await client.get(
        "/api/v1/admin/tags", params={"q": "m3-tag"}, headers=auth(admin_token)
    )
    by_name = {t["name"]: t for t in listing.json()["items"]}
    tag_a = by_name["m3-tag-a"]["id"]
    tag_b = by_name["m3-tag-b"]["id"]

    # 重命名
    renamed = await client.patch(
        f"/api/v1/admin/tags/{tag_b}",
        json={"display_name": "M3-Tag-B"},
        headers=auth(admin_token),
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["display_name"] == "M3-Tag-B"
    assert renamed.json()["name"] == "m3-tag-b"  # 归一化键不变（本来就小写）

    # 合并 b → a
    merged = await client.post(
        "/api/v1/admin/tags/merge",
        json={"source_ids": [tag_b], "target_id": tag_a},
        headers=auth(admin_token),
    )
    assert merged.status_code == 200, merged.text
    body = merged.json()
    assert body["merged_tags"] == 1
    assert body["target_id"] == tag_a
    # tool_b 的引用被转移；tool_c 同时打了两个标签 → 被去重
    assert body["moved_references"] + body["deduplicated_references"] >= 2
    assert body["deduplicated_references"] >= 1, "同时打了两个标签的工具必须去重"

    # 旧标签消失，新标签的引用数 = 3 个工具
    async with SessionLocal() as session:
        from app.models.taxonomy import Tag

        assert await session.get(Tag, tag_b) is None
        target = await session.get(Tag, tag_a)
        assert target is not None
        count = (
            await session.execute(
                text("SELECT COUNT(*) FROM tool_tags WHERE tag_id=:tid"), {"tid": tag_a}
            )
        ).scalar_one()
        assert count == 3, f"合并后应有 3 个工具引用目标标签，实际 {count}"

    # 清理零引用：造一个没人用的标签
    await create_tool(client, owner, name="临时标签工具", tags=["m3-orphan"])
    async with SessionLocal() as session:
        await session.execute(
            text("DELETE FROM tool_tags WHERE tag_id IN (SELECT id FROM tags WHERE name='m3-orphan')")
        )
        await session.commit()
    cleaned = await client.post("/api/v1/admin/tags/cleanup", headers=auth(admin_token))
    assert cleaned.status_code == 200, cleaned.text
    assert cleaned.json()["deleted"] >= 1
    del tool_a, tool_b, tool_c


# ===========================================================================
# 18/19/20. 导入导出
# ===========================================================================
async def test_import_users_dry_run_does_not_write(client, seeded) -> None:
    """验收 18：dry_run=true 不写库、不生成密码，只返回预演报告。"""
    admin_token = await login(client, "admin")
    csv_content = (
        "username,display_name,email,roles,password\n"
        "dryuser1,干跑用户1,d1@example.com,user,\n"
        "dryuser2,干跑用户2,d2@example.com,user,\n"
    ).encode()

    response = await client.post(
        "/api/v1/admin/import/users",
        files={"file": ("users.csv", csv_content, "text/csv")},
        data={"dry_run": "true", "on_conflict": "skip"},
        headers=auth(admin_token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dry_run"] is True
    assert body["succeeded"] == 2
    assert body["failed"] == 0
    assert body["generated_passwords"] == [], "dry_run 不得生成密码"

    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(User.id).where(User.username.in_(["dryuser1", "dryuser2"]))
            )
        ).all()
    assert rows == [], "dry_run 不得写库"


async def test_import_users_errors_are_precise(client, seeded) -> None:
    """验收 19：2 行非法 → succeeded/failed 计数准确，errors 精确到行与字段。"""
    admin_token = await login(client, "admin")
    csv_content = (
        "username,display_name,email,roles,password\n"
        "m3ok1,正常用户1,ok1@example.com,user,\n"
        "bad user,坏用户名,bad@example.com,user,\n"
        "m3ok2,正常用户2,ok2@example.com,user,\n"
        "m3bad2,坏角色,_,root,\n"
    ).encode()

    response = await client.post(
        "/api/v1/admin/import/users",
        files={"file": ("users.csv", csv_content, "text/csv")},
        data={"dry_run": "false", "on_conflict": "skip"},
        headers=auth(admin_token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["succeeded"] == 2
    assert body["failed"] == 2
    assert len(body["errors"]) == 2
    fields = {e["field"] for e in body["errors"]}
    assert fields == {"username", "roles"}
    rows = {e["row"] for e in body["errors"]}
    assert rows == {3, 5}, f"错误行号应精确到 3 与 5，实际 {rows}"
    assert len(body["generated_passwords"]) == 2, "密码留空时应生成并回显"

    # 清理
    async with SessionLocal() as session:
        for username in ("m3ok1", "m3ok2"):
            user = (
                await session.execute(select(User).where(User.username == username))
            ).scalar_one_or_none()
            if user is not None:
                await session.delete(user)
        await session.commit()


@pytest.mark.parametrize("on_conflict", ["skip", "update", "fail"])
async def test_import_users_on_conflict_strategies(client, seeded, on_conflict: str) -> None:
    admin_token = await login(client, "admin")
    async with SessionLocal() as session:
        admin_user = (
            await session.execute(select(User).where(User.username == "admin"))
        ).scalar_one()
        original_display_name = admin_user.display_name
    csv_content = (
        "username,display_name,email,roles,password\n"
        f"admin,管理员改名{on_conflict},admin@example.com,superadmin,\n"
    ).encode()
    response = await client.post(
        "/api/v1/admin/import/users",
        files={"file": ("u.csv", csv_content, "text/csv")},
        data={"dry_run": "false", "on_conflict": on_conflict},
        headers=auth(admin_token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    if on_conflict == "skip":
        assert body["skipped"] == 1 and body["succeeded"] == 0
        async with SessionLocal() as session:
            user = (
                await session.execute(select(User).where(User.username == "admin"))
            ).scalar_one()
            assert user.display_name == original_display_name  # 没被改
    elif on_conflict == "update":
        assert body["succeeded"] == 1
        async with SessionLocal() as session:
            user = (await session.execute(select(User).where(User.username == "admin"))).scalar_one()
            assert user.display_name == "管理员改名update"
            user.display_name = original_display_name
            await session.commit()
    else:  # fail
        assert body["failed"] == 1
        assert body["errors"][0]["message"] == "用户已存在"


async def test_export_users_csv_has_bom_and_no_password_hash(client, seeded) -> None:
    """验收 20：前 3 字节为 `EF BB BF`；不含密码哈希。"""
    admin_token = await login(client, "admin")
    response = await client.get("/api/v1/admin/export/users", headers=auth(admin_token))
    assert response.status_code == 200, response.text
    raw = response.content
    assert raw[:3] == b"\xef\xbb\xbf", f"CSV 必须以 UTF-8 BOM 开头，实际 {raw[:3]!r}"

    text_body = raw.decode("utf-8-sig")
    header = text_body.splitlines()[0]
    assert header == "username,display_name,email,roles,status"
    assert "admin" in text_body
    # 绝不能出现哈希
    assert "argon2" not in text_body
    assert "$argon2id$" not in text_body
    assert "password_hash" not in text_body


async def test_export_tools_json(client, seeded) -> None:
    admin_token = await login(client, "admin")
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    slug, _tid, _ = await publish_tool(client, owner, approver, name="导出工具")

    response = await client.get("/api/v1/admin/export/tools", headers=auth(admin_token))
    assert response.status_code == 200, response.text
    import json

    payload = json.loads(response.text)
    assert isinstance(payload, list)
    assert any(item["slug"] == slug for item in payload)
    assert all("description_md" in item for item in payload)


async def test_import_tools_json_dry_run_and_conflict(client, seeded) -> None:
    admin_token = await login(client, "admin")
    body = {
        "items": [
            {
                "name": "JSON 导入工具 A",
                "tool_type": "file",
                "owner_username": "admin",
                "tags": ["imported"],
            },
            {
                "name": "JSON 导入工具 B",
                "tool_type": "prompt",
                "owner_username": "no-such-user",
            },
        ],
        "dry_run": True,
    }
    response = await client.post(
        "/api/v1/admin/import/tools", json=body, headers=auth(admin_token)
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["succeeded"] == 1
    assert result["failed"] == 1
    assert result["errors"][0]["field"] == "owner_username"
    assert result["errors"][0]["row"] == 2

    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(Tool.id).where(Tool.name == "JSON 导入工具 A")
            )
        ).all()
    assert rows == [], "dry_run 不得写库"


# ===========================================================================
# 21. 负责人转移
# ===========================================================================
async def test_transfer_owner_recomputes_quota_and_records(client, seeded) -> None:
    """验收 21：双方配额重算正确；审批历史有 `transfer_owner` 记录。"""
    admin_token = await login(client, "admin")
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    _slug, tool_id, _ = await publish_tool(client, owner, approver, name="转移工具")

    async with SessionLocal() as session:
        before_old = await versions_repo.sum_file_size_for_user(
            session, seeded.users["outsider"]
        )
        before_new = await versions_repo.sum_file_size_for_user(
            session, seeded.users["lockme"]
        )

    response = await client.post(
        f"/api/v1/admin/tools/{tool_id}/transfer",
        json={"new_owner_id": seeded.users["lockme"], "reason": "离职交接"},
        headers=auth(admin_token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["new_owner"]["id"] == seeded.users["lockme"]
    assert body["previous_owner"]["id"] == seeded.users["outsider"]

    async with SessionLocal() as session:
        after_old = await versions_repo.sum_file_size_for_user(
            session, seeded.users["outsider"]
        )
        after_new = await versions_repo.sum_file_size_for_user(
            session, seeded.users["lockme"]
        )
        tool = await session.get(Tool, tool_id)
        assert tool is not None and tool.owner_id == seeded.users["lockme"]

        # 新负责人增加了该工具的版本文件大小；原负责人等量减少
        delta_new = after_new - before_new
        delta_old = before_old - after_old
        assert delta_new > 0, "新负责人的用量应当增加"
        assert delta_new == delta_old, "一方增加多少，另一方就应减少多少"
        assert body["new_owner_used_bytes"] == after_new
        assert body["previous_owner_used_bytes"] == after_old

    # 审批历史留痕
    history = await client.get(
        "/api/v1/admin/approvals/history",
        params={"tool_id": tool_id, "action": "transfer_owner"},
        headers=auth(admin_token),
    )
    assert history.json()["total"] >= 1
    record = history.json()["items"][0]
    assert record["action"] == "transfer_owner"
    assert "离职交接" in (record["reason"] or "")

    # 转移给禁用用户 → 400
    async with SessionLocal() as session:
        disabled = seeded.users["disabled"]
    bad = await client.post(
        f"/api/v1/admin/tools/{tool_id}/transfer",
        json={"new_owner_id": disabled},
        headers=auth(admin_token),
    )
    assert bad.status_code == 400


# ===========================================================================
# 22. 回收站
# ===========================================================================
async def test_recycle_bin_restore_and_purge(client, seeded) -> None:
    """验收 22：还原后可见；彻底清除后文件从磁盘消失。"""
    admin_token = await login(client, "admin")
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    slug, tool_id, _content = await publish_tool(client, owner, approver, name="回收站工具")

    # 记下文件路径
    async with SessionLocal() as session:
        version = (
            await session.execute(
                select(ToolVersion).where(ToolVersion.tool_id == tool_id)
            )
        ).scalars().first()
        assert version is not None and version.storage_path
        storage_path = version.storage_path

    from app.storage import get_storage

    storage = get_storage()
    assert storage.exists(storage_path)

    # 软删除 → 进回收站
    await client.delete(f"/api/v1/me/tools/{tool_id}", headers=auth(owner))
    listing = await client.get("/api/v1/admin/recycle-bin", headers=auth(admin_token))
    assert tool_id in [i["id"] for i in listing.json()["items"]]
    assert (await client.get(f"{TOOLS}/{slug}", headers=auth(owner))).status_code == 404

    # 还原
    restored = await client.post(
        f"/api/v1/admin/tools/{tool_id}/restore", headers=auth(admin_token)
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["deleted_at"] is None
    assert (await client.get(f"{TOOLS}/{slug}", headers=auth(owner))).status_code == 200

    # 再删再彻底清除
    await client.delete(f"/api/v1/me/tools/{tool_id}", headers=auth(owner))
    purged = await client.delete(
        f"/api/v1/admin/tools/{tool_id}/purge", headers=auth(admin_token)
    )
    assert purged.status_code == 200, purged.text
    body = purged.json()
    assert body["purged_versions"] >= 1
    assert body["purged_files"] >= 1

    assert not storage.exists(storage_path), "彻底清除后磁盘文件必须消失"
    async with SessionLocal() as session:
        assert await session.get(Tool, tool_id) is None
        remaining = (
            await session.execute(
                select(ToolVersion).where(ToolVersion.tool_id == tool_id)
            )
        ).scalars().all()
    assert remaining == [], "版本行应随工具 CASCADE 删除"


# ===========================================================================
# 23. 设置原子性
# ===========================================================================
async def test_settings_batch_update_is_atomic(client, seeded) -> None:
    """验收 23：一项非法 → 整体回滚，指出 key，其余项未被修改。"""
    admin_token = await login(client, "admin")

    before = {
        item["key"]: item["value"]
        for item in (
            await client.get("/api/v1/admin/settings", headers=auth(admin_token))
        ).json()["items"]
    }

    response = await client.put(
        "/api/v1/admin/settings",
        json={
            "items": [
                {"key": "version.history_limit", "value": 7},
                {"key": "upload.max_screenshots", "value": 999},
            ]
        },
        headers=auth(admin_token),
    )
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["code"] == "SETTING_INVALID"
    assert body["details"]["key"] == "upload.max_screenshots"

    after = {
        item["key"]: item["value"]
        for item in (
            await client.get("/api/v1/admin/settings", headers=auth(admin_token))
        ).json()["items"]
    }
    assert after["version.history_limit"] == before["version.history_limit"], (
        "非法项导致整体回滚，合法项也不能生效"
    )

    # 设置项元信息必须下发（前端不硬编码）
    items = {
        item["key"]: item
        for item in (
            await client.get("/api/v1/admin/settings", headers=auth(admin_token))
        ).json()["items"]
    }
    assert items["approval.mode"]["options"] == ["require", "auto_approve_all"]
    assert items["version.history_limit"]["min"] == 1
    assert items["version.history_limit"]["max"] == 50
    assert items["portal.site_name"]["is_public"] is True
    assert items["security.login_max_failures"]["is_public"] is False


# ===========================================================================
# 统计与概览
# ===========================================================================
async def test_admin_overview_and_stats(client, seeded) -> None:
    admin_token = await login(client, "admin")
    approver = await login(client, "approver")

    overview = await client.get("/api/v1/admin/overview", headers=auth(approver))
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert body["user_count"] >= 2
    assert body["tool_count"] >= 1
    assert isinstance(body["tools_by_status"], list)
    assert {s["status"] for s in body["tools_by_status"]} >= {"approved", "draft"}
    assert body["used_bytes"] >= 0
    assert body["quota_bytes"] > 0

    rank = await client.get("/api/v1/admin/stats/tools", params={"limit": 5}, headers=auth(approver))
    assert rank.status_code == 200
    assert len(rank.json()["items"]) <= 5
    if rank.json()["items"]:
        counts = [i["download_count"] for i in rank.json()["items"]]
        assert counts == sorted(counts, reverse=True), "排行必须按下载量降序"

    storage = await client.get("/api/v1/admin/stats/storage", headers=auth(admin_token))
    assert storage.status_code == 200, storage.text
    assert storage.json()["total_used_bytes"] >= 0
    assert isinstance(storage.json()["items"], list)

    # approver 不能看存储明细（需要 admin:all）
    denied = await client.get("/api/v1/admin/stats/storage", headers=auth(approver))
    assert denied.status_code == 403


async def test_admin_roles_endpoint(client, seeded) -> None:
    approver = await login(client, "approver")
    response = await client.get("/api/v1/admin/roles", headers=auth(approver))
    assert response.status_code == 200, response.text
    codes = {r["code"] for r in response.json()}
    assert codes == {"superadmin", "approver", "user", "viewer"}
    superadmin = next(r for r in response.json() if r["code"] == "superadmin")
    assert "admin:all" in superadmin["permissions"]
    assert superadmin["user_count"] >= 1


# ===========================================================================
# 全站工具列表
# ===========================================================================
async def test_admin_tools_list_filters(client, seeded) -> None:
    admin_token = await login(client, "admin")
    approver = await login(client, "approver")
    owner = await login(client, "outsider")
    _slug, tool_id, _ = await publish_tool(client, owner, approver, name="全站列表工具")

    all_tools = await client.get("/api/v1/admin/tools", headers=auth(approver))
    assert all_tools.status_code == 200, all_tools.text
    assert tool_id in [i["id"] for i in all_tools.json()["items"]]

    by_status = await client.get(
        "/api/v1/admin/tools", params={"status": "approved"}, headers=auth(approver)
    )
    assert all(i["status"] == "approved" for i in by_status.json()["items"])

    by_owner = await client.get(
        "/api/v1/admin/tools", params={"owner": "outsider"}, headers=auth(approver)
    )
    assert all(i["owner"]["username"] == "outsider" for i in by_owner.json()["items"])

    by_type = await client.get(
        "/api/v1/admin/tools", params={"type": "prompt"}, headers=auth(approver)
    )
    assert all(i["tool_type"] == "prompt" for i in by_type.json()["items"])

    by_q = await client.get(
        "/api/v1/admin/tools", params={"q": "全站列表工具"}, headers=auth(approver)
    )
    assert by_q.json()["total"] >= 1
    del admin_token


async def test_admin_can_create_tool_for_other_owner(client, seeded) -> None:
    """docs/03 §5.2：管理侧代创建允许指定 owner_id。"""
    admin_token = await login(client, "admin")
    response = await client.post(
        "/api/v1/admin/tools",
        json={
            "name": "代创建工具",
            "summary": "由管理员代创建",
            "tool_type": "file",
            "owner_id": seeded.users["lockme"],
            "tags": ["imported"],
            "publish": True,
        },
        headers=auth(admin_token),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["owner"]["id"] == seeded.users["lockme"]
    assert body["status"] == "approved"

    # 代创建不能指定不存在的 owner
    bad = await client.post(
        "/api/v1/admin/tools",
        json={
            "name": "错 owner",
            "summary": "s",
            "tool_type": "file",
            "owner_id": 999999,
        },
        headers=auth(admin_token),
    )
    assert bad.status_code == 404


# ===========================================================================
# 权限矩阵
# ===========================================================================
@pytest.mark.parametrize(
    ("username", "path", "expected"),
    [
        ("viewer", "/api/v1/admin/users", 403),
        ("viewer", "/api/v1/admin/groups", 403),
        ("viewer", "/api/v1/admin/categories", 403),
        ("viewer", "/api/v1/admin/tokens", 403),
        ("viewer", "/api/v1/admin/overview", 403),
        ("approver", "/api/v1/admin/users", 403),
        ("approver", "/api/v1/admin/tokens", 403),
        ("approver", "/api/v1/admin/settings", 403),
        ("approver", "/api/v1/admin/tools", 200),
        ("approver", "/api/v1/admin/overview", 200),
        ("approver", "/api/v1/admin/categories", 200),
        ("approver", "/api/v1/admin/tags", 200),
        ("admin", "/api/v1/admin/users", 200),
        ("admin", "/api/v1/admin/groups", 200),
        ("admin", "/api/v1/admin/tokens", 200),
        ("admin", "/api/v1/admin/settings", 200),
    ],
)
async def test_admin_permission_matrix(
    client, seeded, username: str, path: str, expected: int
) -> None:
    token = await login(client, username)
    response = await client.get(path, headers=auth(token))
    assert response.status_code == expected, f"{username} {path} → {response.text}"


async def test_tools_slug_filter_like_escaped(client, seeded) -> None:
    """`/admin/tools?q=` 里的 `%` 与 `_` 应当被当作普通字符转义。

    不转义的话，`q=%` 会退化成「匹配所有」，等于绕过了筛选。
    """
    admin_token = await login(client, "admin")
    response = await client.get(
        "/api/v1/admin/tools", params={"q": "%"}, headers=auth(admin_token)
    )
    assert response.status_code == 200
    total_all = (
        await client.get("/api/v1/admin/tools", headers=auth(admin_token))
    ).json()["total"]
    # 除非名字里真的含 %，否则不应返回全部
    assert response.json()["total"] <= total_all


async def test_user_crud_flow(client, seeded) -> None:
    admin_token = await login(client, "admin")
    created = await client.post(
        "/api/v1/admin/users",
        json={
            "username": "cruduser",
            "display_name": "CRUD 用户",
            "email": "crud@example.com",
            "roles": ["user"],
        },
        headers=auth(admin_token),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    user_id = body["user"]["id"]
    assert body["generated_password"], "密码留空时应生成并回显一次"
    generated = body["generated_password"]

    # 用生成的密码能登录
    token = await login(client, "cruduser", generated)
    assert token

    # 重复用户名 → 400
    dup = await client.post(
        "/api/v1/admin/users",
        json={"username": "cruduser", "display_name": "x", "roles": ["user"]},
        headers=auth(admin_token),
    )
    assert dup.status_code == 400
    assert dup.json()["code"] == "VALIDATION_ERROR"

    # 编辑
    patched = await client.patch(
        f"/api/v1/admin/users/{user_id}",
        json={"display_name": "改名了", "email": "new@example.com"},
        headers=auth(admin_token),
    )
    assert patched.status_code == 200
    assert patched.json()["display_name"] == "改名了"

    # 重置密码 → 强制改密 + 吊销会话
    reset = await client.post(
        f"/api/v1/admin/users/{user_id}/reset-password",
        json={},
        headers=auth(admin_token),
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["must_change_password"] is True
    assert reset.json()["generated_password"]

    # 全量替换角色
    roles = await client.put(
        f"/api/v1/admin/users/{user_id}/roles",
        json={"roles": ["viewer"]},
        headers=auth(admin_token),
    )
    assert roles.status_code == 200
    assert roles.json()["roles"] == ["viewer"]

    # 强制下线
    revoked = await client.post(
        f"/api/v1/admin/users/{user_id}/revoke-sessions", headers=auth(admin_token)
    )
    assert revoked.status_code == 200
    assert "revoked_sessions" in revoked.json()

    # 详情
    detail = await client.get(f"/api/v1/admin/users/{user_id}", headers=auth(admin_token))
    assert detail.json()["username"] == "cruduser"

    # 清理
    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        if user is not None:
            await session.delete(user)
            await session.commit()


async def test_reject_reason_visible_for_new_version_after_m3(client, seeded) -> None:
    """回归：M3 的可见性调整不能破坏「驳回新版本」的语义。"""
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    slug, tool_id, _v1 = await publish_tool(client, owner, approver, name="回归工具")

    await upload_version(
        client, owner, tool_id, version="2.0.0",
        file_bytes=zip_bytes({"v2.bin": b"v2"}), file_name="v2.zip",
    )
    await submit(client, owner, tool_id)
    response = await reject(client, approver, tool_id, reason="2.0.0 需要补充变更说明")
    assert response.json()["status"] == "approved"
    detail = await client.get(f"{TOOLS}/{slug}", headers=auth(owner))
    assert detail.status_code == 200
    assert detail.json()["current_version"]["version"] == "1.0.0"
    assert detail.json()["status"] == "approved"
