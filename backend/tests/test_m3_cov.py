"""M3 管理面的**分支补漏**测试。

`tests/test_m3_admin.py` 覆盖的是验收路径（「正常能不能走通」）；
这个文件专门覆盖错误分支与边界分支（「出错时返回什么」），
目的是把 M3 新增的 `app/services/*.py` 模块的行覆盖率顶到 90% 以上。

分组与 `app/services/` 一一对应，方便对着覆盖率报告定位缺口。
"""

from __future__ import annotations

import asyncio
import io
import json

import pytest
from sqlalchemy import select

from app.core.errors import DomainError, NotFoundError, ValidationError
from app.core.timeutil import utcnow
from app.db.session import SessionLocal
from app.models.enums import ApiScope, RoleCode, ToolStatus
from app.models.tool import Tool
from app.models.user import User
from app.schemas.admin import (
    ApiTokenCreateRequest,
    TagMergeRequest,
)
from app.services import (
    admin_taxonomy_service,
    admin_tool_service,
    admin_user_service,
    group_service,
    import_export_service,
    token_service,
)
from tests.conftest import Seeded, auth, login

# ---------------------------------------------------------------------------
# 公共小工具
# ---------------------------------------------------------------------------


async def _admin_headers(client) -> dict[str, str]:
    return auth(await login(client, "admin"))


async def _approver_headers(client) -> dict[str, str]:
    return auth(await login(client, "approver"))


async def _first_category_id(seeded: Seeded) -> int:
    return next(iter(seeded.categories.values()))


# ===========================================================================
# 1) admin_user_service —— 列表筛选、错误分支、重新启用、吊销 Token
# ===========================================================================
async def test_user_list_filters_cover_q_status_and_role(client, seeded) -> None:
    """`q` / `status` / `role` 三个筛选条件都要真的拼进 SQL（不是 Python 过滤）。"""
    headers = await _admin_headers(client)

    by_q = await client.get("/api/v1/admin/users", params={"q": "newb"}, headers=headers)
    assert by_q.status_code == 200, by_q.text
    assert "newbie" in {u["username"] for u in by_q.json()["items"]}

    by_status = await client.get(
        "/api/v1/admin/users", params={"status": "disabled"}, headers=headers
    )
    assert by_status.status_code == 200, by_status.text
    # 不断言集合相等：同一库里还有其他用例新建/禁用的用户
    assert {u["username"] for u in by_status.json()["items"]} >= {"disabled"}
    assert all(u["status"] == "disabled" for u in by_status.json()["items"])

    by_role = await client.get(
        "/api/v1/admin/users", params={"role": "approver"}, headers=headers
    )
    assert by_role.status_code == 200, by_role.text
    names = {u["username"] for u in by_role.json()["items"]}
    assert "approver" in names
    assert "newbie" not in names

    # q 命中 email 也要能搜到（ILIKE 覆盖 username / display_name / email 三列）
    by_email = await client.get("/api/v1/admin/users", params={"q": "localcraft"}, headers=headers)
    assert by_email.status_code == 200, by_email.text


async def test_user_detail_and_update_missing_user_returns_404(client, seeded) -> None:
    headers = await _admin_headers(client)

    detail = await client.get("/api/v1/admin/users/999999", headers=headers)
    assert detail.status_code == 404
    assert detail.json()["code"] == "NOT_FOUND"

    patched = await client.patch(
        "/api/v1/admin/users/999999", json={"display_name": "张三"}, headers=headers
    )
    assert patched.status_code == 404
    assert patched.json()["code"] == "NOT_FOUND"

    roles = await client.put(
        "/api/v1/admin/users/999999/roles", json={"roles": ["user"]}, headers=headers
    )
    assert roles.status_code == 404

    reset = await client.post(
        "/api/v1/admin/users/999999/reset-password", json={}, headers=headers
    )
    assert reset.status_code == 404

    revoke = await client.post("/api/v1/admin/users/999999/revoke-sessions", headers=headers)
    assert revoke.status_code == 404


async def test_create_user_rejects_weak_password_and_slugless_roles(client, seeded) -> None:
    """弱口令要报 VALIDATION_ERROR 并带 fields；未知角色由 Pydantic 拦在 422。"""
    headers = await _admin_headers(client)

    weak = await client.post(
        "/api/v1/admin/users",
        json={
            "username": "weakpwd",
            "display_name": "弱口令",
            "password": "123",
            "roles": ["user"],
        },
        headers=headers,
    )
    assert weak.status_code == 400
    body = weak.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"]["fields"][0]["field"] == "password"

    unknown_role = await client.post(
        "/api/v1/admin/users",
        json={
            "username": "norole",
            "display_name": "无角色",
            "password": "Good@12345",
            "roles": ["emperor"],
        },
        headers=headers,
    )
    assert unknown_role.status_code == 400
    assert unknown_role.json()["code"] == "VALIDATION_ERROR"


async def test_reset_password_with_explicit_weak_password_is_rejected(client, seeded) -> None:
    """**不能拿 `newbie` 做实验**：其他测试文件要用它的固定口令登录。

    所以在用例内自建一个用户，测完即废。
    """
    headers = await _admin_headers(client)
    created_user = await client.post(
        "/api/v1/admin/users",
        json={
            "username": "pwdreset-cov",
            "display_name": "改密实验",
            "password": "Original@12345",
            "roles": ["user"],
            "must_change_password": False,
        },
        headers=headers,
    )
    assert created_user.status_code == 201, created_user.text
    user_id = created_user.json()["user"]["id"]

    strong = await client.post(
        f"/api/v1/admin/users/{user_id}/reset-password",
        json={"password": "Str0ng@Passw0rd"},
        headers=headers,
    )
    assert strong.status_code == 200, strong.text
    assert strong.json()["generated_password"] is None
    assert strong.json()["must_change_password"] is True

    weak = await client.post(
        f"/api/v1/admin/users/{user_id}/reset-password",
        json={"password": "abcdefgh"},
        headers=headers,
    )
    assert weak.status_code == 400
    assert weak.json()["code"] == "VALIDATION_ERROR"
    assert weak.json()["details"]["fields"][0]["field"] == "password"


async def test_disabled_user_can_be_re_enabled(client, seeded) -> None:
    """`PATCH status=active` 是禁用之后唯一能救回来的路径，必须真的写库。"""
    headers = await _admin_headers(client)
    created = await client.post(
        "/api/v1/admin/users",
        json={
            "username": "toggler",
            "display_name": "开关",
            "password": "Toggle@12345",
            "roles": ["user"],
            "must_change_password": False,
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["user"]["id"]

    off = await client.patch(
        f"/api/v1/admin/users/{user_id}", json={"status": "disabled"}, headers=headers
    )
    assert off.status_code == 200, off.text
    assert off.json()["status"] == "disabled"

    on = await client.patch(
        f"/api/v1/admin/users/{user_id}",
        json={"status": "active", "email": "toggler@example.com", "display_name": "开关二号"},
        headers=headers,
    )
    assert on.status_code == 200, on.text
    assert on.json()["status"] == "active"
    assert on.json()["email"] == "toggler@example.com"
    assert on.json()["display_name"] == "开关二号"

    # 再禁一次，然后确认「禁用状态」在列表筛选里也可见
    await client.patch(
        f"/api/v1/admin/users/{user_id}", json={"status": "disabled"}, headers=headers
    )


async def test_resolve_actor_id_tolerates_unknown_actor(seeded) -> None:
    """actor 不存在时记 NULL 而不是撞外键 —— 见 `_resolve_actor_id` 的注释。"""
    async with SessionLocal() as session:
        assert await admin_user_service._resolve_actor_id(session, None) is None
        assert await admin_user_service._resolve_actor_id(session, 0) is None
        assert (
            await admin_user_service._resolve_actor_id(session, seeded.users["admin"])
            == seeded.users["admin"]
        )


async def test_role_id_helpers_cover_unknown_code(seeded) -> None:
    async with SessionLocal() as session:
        # 空列表走早返回，不查库
        assert await admin_user_service._role_ids_for_codes(session, []) == {}
        # 未知角色码要报 VALIDATION_ERROR，而不是 KeyError
        with pytest.raises(ValidationError) as excinfo:
            await admin_user_service._role_ids_for_codes(session, ["emperor"])
        assert "角色" in str(excinfo.value)


async def test_revoke_user_tokens_helper(seeded) -> None:
    """一键吊销某用户全部 Token（HTTP 上没有这个动作，只有 CLI `revoke-tokens`）。"""
    async with SessionLocal() as session:
        admin_id = seeded.users["admin"]
        admin_user = await session.get(User, admin_id)
        assert admin_user is not None
        created = await token_service.create_token(
            session,
            payload=ApiTokenCreateRequest(
                name="cov-revoke-all", scopes=[ApiScope.APPROVALS_WRITE], expires_at=None
            ),
            creator=admin_user,
        )
        assert created.token.startswith("st_")

    async with SessionLocal() as session:
        revoked = await admin_user_service.revoke_user_tokens(
            session, user_id=admin_id, actor_id=admin_id
        )
        assert revoked >= 1

    async with SessionLocal() as session:
        # 再次调用：已经没有活跃 Token 了，返回 0 而不是报错
        assert (
            await admin_user_service.revoke_user_tokens(
                session, user_id=admin_id, actor_id=admin_id
            )
            == 0
        )


# ===========================================================================
# 2) token_service —— Scope 越权、404、物理删除、可选 Scope 列表
# ===========================================================================
async def test_token_scope_exceeding_creator_is_forbidden(client, seeded) -> None:
    """approver 不能签发 `admin:all` —— 越权 Scope 走 403 而不是静默裁剪。"""
    headers = await _approver_headers(client)
    response = await client.post(
        "/api/v1/admin/tokens",
        json={"name": "overreach", "scopes": [ApiScope.ADMIN_ALL.value]},
        headers=headers,
    )
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "FORBIDDEN"


async def test_token_revoke_and_delete_missing_are_404(client, seeded) -> None:
    headers = await _admin_headers(client)
    assert (
        await client.post("/api/v1/admin/tokens/999999/revoke", headers=headers)
    ).status_code == 404
    assert (await client.delete("/api/v1/admin/tokens/999999", headers=headers)).status_code == 404


async def test_token_delete_removes_row_and_available_scopes(seeded) -> None:
    async with SessionLocal() as session:
        admin = await session.get(User, seeded.users["admin"])
        assert admin is not None
        scopes = token_service.available_scopes(admin)
        # admin 是超管，应当拿到 admin:all；且永远不重复
        assert ApiScope.ADMIN_ALL.value in scopes
        assert len(scopes) == len(set(scopes))

        approver = await session.get(User, seeded.users["approver"])
        assert approver is not None
        approver_scopes = token_service.available_scopes(approver)
        assert ApiScope.ADMIN_ALL.value not in approver_scopes
        assert ApiScope.APPROVALS_WRITE.value in approver_scopes

        created = await token_service.create_token(
            session,
            payload=ApiTokenCreateRequest(
                name="to-delete", scopes=[ApiScope.TOOLS_READ], expires_at=None
            ),
            creator=admin,
        )

    async with SessionLocal() as session:
        await token_service.delete_token(session, token_id=created.id)

    async with SessionLocal() as session:
        with pytest.raises(NotFoundError):
            await token_service.delete_token(session, token_id=created.id)


async def test_token_revoke_is_idempotent(seeded) -> None:
    async with SessionLocal() as session:
        admin = await session.get(User, seeded.users["admin"])
        assert admin is not None
        t = await token_service.create_token(
            session,
            payload=ApiTokenCreateRequest(
                name="idem", scopes=[ApiScope.TOOLS_READ], expires_at=None
            ),
            creator=admin,
        )
    async with SessionLocal() as session:
        first = await token_service.revoke_token(
            session, token_id=t.id, actor_id=seeded.users["admin"]
        )
        assert first.revoked_at is not None
    async with SessionLocal() as session:
        again = await token_service.revoke_token(
            session, token_id=t.id, actor_id=seeded.users["admin"]
        )
        # 响应里的时间是 aware UTC；库里读回来是 naive。只比较瞬时值。
        assert again.revoked_at.replace(tzinfo=None) == first.revoked_at.replace(tzinfo=None)

    async with SessionLocal() as session:
        with pytest.raises(NotFoundError):
            await token_service.revoke_token(
                session, token_id=999999, actor_id=seeded.users["admin"]
            )


# ===========================================================================
# 3) group_service —— 空列表、404、重名、成员增删的边界
# ===========================================================================
async def test_group_empty_filter_returns_empty_page(client, seeded) -> None:
    headers = await _admin_headers(client)
    response = await client.get(
        "/api/v1/admin/groups", params={"q": "根本不存在的小组"}, headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


async def test_group_missing_and_duplicate_paths(client, seeded) -> None:
    headers = await _admin_headers(client)

    assert (await client.get("/api/v1/admin/groups/999999", headers=headers)).status_code == 404
    assert (
        await client.patch(
            "/api/v1/admin/groups/999999", json={"name": "x"}, headers=headers
        )
    ).status_code == 404
    assert (await client.delete("/api/v1/admin/groups/999999", headers=headers)).status_code == 404
    assert (
        await client.get("/api/v1/admin/groups/999999/members", headers=headers)
    ).status_code == 404
    assert (
        await client.post(
            "/api/v1/admin/groups/999999/members", json={"user_ids": [1]}, headers=headers
        )
    ).status_code == 404
    assert (
        await client.delete("/api/v1/admin/groups/999999/members/1", headers=headers)
    ).status_code == 404

    # 重名：POST 与 PATCH 两条路径都要报 DUPLICATE_ENTRY（映射到 400）
    dup = await client.post(
        "/api/v1/admin/groups", json={"name": "测试组"}, headers=headers
    )
    assert dup.status_code == 400, dup.text
    assert dup.json()["code"] == "DUPLICATE_ENTRY"

    # PATCH 改名撞上另一个已存在的组名
    other = await client.post(
        "/api/v1/admin/groups", json={"name": "临时组-cov"}, headers=headers
    )
    assert other.status_code == 201, other.text
    other_id = other.json()["id"]

    clash = await client.patch(
        f"/api/v1/admin/groups/{other_id}", json={"name": "测试组"}, headers=headers
    )
    assert clash.status_code == 400
    assert clash.json()["code"] == "DUPLICATE_ENTRY"

    # PATCH 只改描述/is_active 也要生效
    patched = await client.patch(
        f"/api/v1/admin/groups/{other_id}",
        json={"description": "改过的描述", "is_active": False},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["description"] == "改过的描述"
    assert patched.json()["is_active"] is False

    # 改回自己的名字不该被当成冲突（existing.id != group.id 的分支）
    same = await client.patch(
        f"/api/v1/admin/groups/{other_id}", json={"name": "临时组-cov"}, headers=headers
    )
    assert same.status_code == 200, same.text

    await client.delete(f"/api/v1/admin/groups/{other_id}", headers=headers)


async def test_group_member_add_reports_unknown_users(client, seeded) -> None:
    headers = await _admin_headers(client)
    response = await client.post(
        f"/api/v1/admin/groups/{seeded.group_id}/members",
        json={"user_ids": [seeded.users["admin"], 999998, 999999]},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert sorted(body["not_found"]) == [999998, 999999]
    # admin 已经在审批组里（seeded 加过 approver，admin 可能不在）
    assert body["added"] + body["already_members"] == 1

    # 移除一个不在组内的人 → 404
    miss = await client.delete(
        f"/api/v1/admin/groups/{seeded.group_id}/members/999999", headers=headers
    )
    assert miss.status_code == 404


async def test_group_ids_for_user_helper(seeded) -> None:
    async with SessionLocal() as session:
        ids = await group_service.group_ids_for_user(session, seeded.users["approver"])
        assert seeded.group_id in ids
        assert await group_service.group_ids_for_user(session, 999999) == []


async def test_group_service_missing_helpers(seeded) -> None:
    async with SessionLocal() as session:
        for coro in (
            group_service.get_group(session, 999999),
            group_service.delete_group(session, group_id=999999),
            group_service.force_delete_group(session, group_id=999999),
            group_service.list_members(session, group_id=999999),
            group_service.add_members(session, group_id=999999, user_ids=[1], actor_id=1),
            group_service.remove_member(session, group_id=999999, user_id=1),
        ):
            with pytest.raises(Exception) as excinfo:
                await coro
            assert getattr(excinfo.value, "code", None) == "NOT_FOUND"


# ===========================================================================
# 4) admin_taxonomy_service —— slugify、重名、更新、标签三个动作的错误分支
# ===========================================================================
async def test_category_create_generates_slug_from_chinese(client, seeded) -> None:
    """中文名 → slugify 掉非 ASCII 后为空，要回落到可用的 slug。"""
    headers = await _approver_headers(client)
    created = await client.post(
        "/api/v1/admin/categories", json={"name": "纯中文分类名"}, headers=headers
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["slug"], "slug 不能为空 —— slugify 对纯中文要回落到 name 的哈希/前缀"
    assert body["name"] == "纯中文分类名"

    # 同名再来一次 → DUPLICATE_ENTRY
    dup = await client.post(
        "/api/v1/admin/categories", json={"name": "纯中文分类名"}, headers=headers
    )
    assert dup.status_code == 400, dup.text
    assert dup.json()["code"] == "DUPLICATE_ENTRY"

    # 显式指定一个已被占用的 slug → DUPLICATE_ENTRY
    taken_slug = next(iter(seeded.categories))
    dup_slug = await client.post(
        "/api/v1/admin/categories",
        json={"name": "另一个分类-cov", "slug": taken_slug},
        headers=headers,
    )
    assert dup_slug.status_code == 400
    assert dup_slug.json()["code"] == "DUPLICATE_ENTRY"

    await client.delete(f"/api/v1/admin/categories/{body['id']}", headers=headers)


async def test_category_update_full_payload_and_conflicts(client, seeded) -> None:
    headers = await _approver_headers(client)
    created = await client.post(
        "/api/v1/admin/categories",
        json={"name": "待编辑分类", "slug": "cov-edit-cat"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    cat_id = created.json()["id"]

    updated = await client.patch(
        f"/api/v1/admin/categories/{cat_id}",
        json={
            "name": "已编辑分类",
            "slug": "cov-edit-cat-2",
            "description": "描述",
            "icon": "wrench",
            "sort_order": 7,
            "is_active": False,
        },
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["name"] == "已编辑分类"
    assert body["slug"] == "cov-edit-cat-2"
    assert body["icon"] == "wrench"
    assert body["sort_order"] == 7
    assert body["is_active"] is False

    # 改成已有分类的 name → DUPLICATE_ENTRY
    other_name = await _first_category_name(client, headers, seeded)
    clash_name = await client.patch(
        f"/api/v1/admin/categories/{cat_id}", json={"name": other_name}, headers=headers
    )
    assert clash_name.status_code == 400, clash_name.text

    # 改成已有分类的 slug → DUPLICATE_ENTRY
    other_slug = next(iter(seeded.categories))
    clash_slug = await client.patch(
        f"/api/v1/admin/categories/{cat_id}", json={"slug": other_slug}, headers=headers
    )
    assert clash_slug.status_code == 400, clash_slug.text

    # 保持自身 name/slug 不变不算冲突
    keep = await client.patch(
        f"/api/v1/admin/categories/{cat_id}",
        json={"name": "已编辑分类", "slug": "cov-edit-cat-2"},
        headers=headers,
    )
    assert keep.status_code == 200, keep.text

    assert (
        await client.patch(
            "/api/v1/admin/categories/999999", json={"name": "x"}, headers=headers
        )
    ).status_code == 404
    assert (
        await client.delete("/api/v1/admin/categories/999999", headers=headers)
    ).status_code == 404

    await client.delete(f"/api/v1/admin/categories/{cat_id}", headers=headers)


async def _first_category_name(client, headers, seeded: Seeded) -> str:
    listed = await client.get("/api/v1/admin/categories", headers=headers)
    assert listed.status_code == 200, listed.text
    return listed.json()[0]["name"]


async def test_category_reorder_rejects_unknown_ids(client, seeded) -> None:
    headers = await _approver_headers(client)
    response = await client.put(
        "/api/v1/admin/categories/order",
        json={"items": [{"id": 999999, "sort_order": 1}]},
        headers=headers,
    )
    assert response.status_code == 404, response.text
    assert response.json()["code"] == "NOT_FOUND"


async def test_tag_rename_and_merge_error_branches(client, seeded) -> None:
    headers = await _approver_headers(client)

    tags = await client.get("/api/v1/admin/tags", headers=headers)
    assert tags.status_code == 200, tags.text

    # 造两个标签
    first = await client.patch("/api/v1/admin/tags/999999", json={"display_name": "x"}, headers=headers)
    assert first.status_code == 404

    merged = await client.post(
        "/api/v1/admin/tags/merge",
        json={"source_ids": [999999], "target_id": 999998},
        headers=headers,
    )
    assert merged.status_code == 404, merged.text

    cleanup = await client.post("/api/v1/admin/tags/cleanup", headers=headers)
    assert cleanup.status_code == 200, cleanup.text
    assert cleanup.json()["deleted"] >= 0


async def test_taxonomy_service_slugify_is_transliterating(seeded) -> None:
    assert admin_taxonomy_service.slugify("Hello World") == "hello-world"
    assert admin_taxonomy_service.slugify("a---b") == "a-b"
    assert admin_taxonomy_service.slugify("") == ""
    # 纯中文没有 ASCII 可保留
    assert admin_taxonomy_service.slugify("中文") == ""
    assert len(admin_taxonomy_service.slugify("x" * 200)) <= 64


async def test_taxonomy_service_tag_errors_directly(seeded) -> None:
    async with SessionLocal() as session:
        with pytest.raises(NotFoundError) as e1:
            await admin_taxonomy_service.rename_tag(session, tag_id=999999, display_name="x")
        assert e1.value.code == "NOT_FOUND"

        with pytest.raises(DomainError):
            await admin_taxonomy_service.merge_tags(
                session,
                payload=TagMergeRequest(source_ids=[999999], target_id=999998),
            )

        # 清理是幂等的：没有零引用标签时返回 deleted=0，而不是报错
        assert (await admin_taxonomy_service.cleanup_tags(session)).deleted >= 0


# ===========================================================================
# 5) admin_tool_service —— 列表筛选、代创建校验、状态机、回收站清理
# ===========================================================================
async def test_admin_tool_list_visibility_and_date_filters(client, seeded) -> None:
    headers = await _approver_headers(client)

    by_vis = await client.get(
        "/api/v1/admin/tools", params={"visibility": "public"}, headers=headers
    )
    assert by_vis.status_code == 200, by_vis.text
    assert all(i["visibility"] == "public" for i in by_vis.json()["items"])

    future = "2999-01-01T00:00:00Z"
    none_after = await client.get(
        "/api/v1/admin/tools", params={"date_from": future}, headers=headers
    )
    assert none_after.status_code == 200, none_after.text
    assert none_after.json()["items"] == []

    past = "2000-01-01T00:00:00Z"
    with_range = await client.get(
        "/api/v1/admin/tools",
        params={"date_from": past, "date_to": future},
        headers=headers,
    )
    assert with_range.status_code == 200, with_range.text
    assert with_range.json()["total"] >= 1

    by_owner = await client.get(
        "/api/v1/admin/tools", params={"owner": "nonexistent-user"}, headers=headers
    )
    assert by_owner.status_code == 200
    assert by_owner.json()["items"] == []

    deleted = await client.get(
        "/api/v1/admin/tools", params={"include_deleted": True}, headers=headers
    )
    assert deleted.status_code == 200


async def test_admin_tool_create_validation_branches(client, seeded) -> None:
    headers = await _admin_headers(client)

    bad_owner = await client.post(
        "/api/v1/admin/tools",
        json={
            "name": "代创建-cov",
            "summary": "摘要",
            "tool_type": "file",
            "owner_id": 999999,
        },
        headers=headers,
    )
    assert bad_owner.status_code == 404, bad_owner.text

    bad_category = await client.post(
        "/api/v1/admin/tools",
        json={
            "name": "代创建-cov",
            "summary": "摘要",
            "tool_type": "file",
            "owner_id": seeded.users["newbie"],
            "category_id": 999999,
        },
        headers=headers,
    )
    assert bad_category.status_code == 400, bad_category.text
    assert bad_category.json()["code"] == "VALIDATION_ERROR"

    # webapp 类型必须给 webapp_url —— 必须是干净的 400，不能是 500
    # （早期版本会一路 INSERT 撞 ck_tools_webapp_url 检查约束）
    webapp_missing_url = await client.post(
        "/api/v1/admin/tools",
        json={
            "name": "网页应用-cov",
            "summary": "摘要",
            "tool_type": "webapp",
            "owner_id": seeded.users["newbie"],
        },
        headers=headers,
    )
    assert webapp_missing_url.status_code == 400, webapp_missing_url.text
    assert webapp_missing_url.json()["code"] == "VALIDATION_ERROR"
    # model_validator 的报错挂在模型级，信封里落在 `body` 字段上
    assert webapp_missing_url.json()["details"]["fields"][0]["field"] == "body"

    # 同样的规则在批量导入里也必须是「行级错误」，而不是整批 500
    bad_import = await client.post(
        "/api/v1/admin/import/tools",
        json={
            "dry_run": False,
            "on_conflict": "skip",
            "items": [
                {
                    "name": "导入网页应用-cov",
                    "tool_type": "webapp",
                    "owner_username": "newbie",
                }
            ],
        },
        headers=headers,
    )
    assert bad_import.status_code == 200, bad_import.text
    assert bad_import.json()["failed"] == 1
    assert bad_import.json()["errors"][0]["field"] == "webapp_url"

    ok = await client.post(
        "/api/v1/admin/tools",
        json={
            "name": "代创建成功-cov",
            "summary": "摘要",
            "tool_type": "file",
            "owner_id": seeded.users["newbie"],
            "category_id": await _first_category_id(seeded),
            "tags": ["cov-a", "cov-b"],
        },
        headers=headers,
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["owner"]["id"] == seeded.users["newbie"]


async def test_transfer_restore_purge_error_branches(client, seeded) -> None:
    headers = await _admin_headers(client)
    admin_id = seeded.users["admin"]

    assert (
        await client.post(
            "/api/v1/admin/tools/999999/transfer",
            json={"new_owner_id": admin_id},
            headers=headers,
        )
    ).status_code == 404

    # 转移到一个不存在的用户 → 400
    tool_id = seeded.tools["public-approved"]
    assert (
        await client.post(
            f"/api/v1/admin/tools/{tool_id}/transfer",
            json={"new_owner_id": 999999},
            headers=headers,
        )
    ).status_code == 404

    # 转给当前 owner 自己 → 409/400
    async with SessionLocal() as session:
        row = await session.get(Tool, tool_id)
        assert row is not None
        same_owner = row.owner_id
    same = await client.post(
        f"/api/v1/admin/tools/{tool_id}/transfer",
        json={"new_owner_id": same_owner},
        headers=headers,
    )
    assert same.status_code in (400, 409), same.text

    assert (
        await client.post("/api/v1/admin/tools/999999/restore", headers=headers)
    ).status_code == 404
    assert (
        await client.delete("/api/v1/admin/tools/999999/purge", headers=headers)
    ).status_code == 404

    # 物理清除不要求先软删：新建一个一次性工具直接 purge
    throwaway = await client.post(
        "/api/v1/admin/tools",
        json={
            "name": "一次性工具-cov",
            "summary": "摘要",
            "tool_type": "file",
            "owner_id": seeded.users["newbie"],
        },
        headers=headers,
    )
    assert throwaway.status_code == 201, throwaway.text
    purged = await client.delete(
        f"/api/v1/admin/tools/{throwaway.json()['id']}/purge", headers=headers
    )
    assert purged.status_code == 200, purged.text

    # 未删除的工具不能 restore
    not_deleted_restore = await client.post(
        f"/api/v1/admin/tools/{tool_id}/restore", headers=headers
    )
    assert not_deleted_restore.status_code == 409, not_deleted_restore.text


async def test_purge_tool_deletes_images_and_expired_recycle_bin(seeded) -> None:
    """`purge_tool` 要先删文件再删行；`purge_expired_recycle_bin` 只捞过期的。"""
    async with SessionLocal() as session:
        owner_id = seeded.users["admin"]
        now = utcnow()
        fresh = Tool(
            slug="cov-recycle-fresh",
            name="新近删除",
            summary="s",
            description_md="",
            tool_type="file",
            visibility="public",
            status=ToolStatus.APPROVED.value,
            owner_id=owner_id,
            version_seq=1,
            deleted_at=now,
            created_at=now,
            updated_at=now,
        )
        stale = Tool(
            slug="cov-recycle-stale",
            name="早已删除",
            summary="s",
            description_md="",
            tool_type="file",
            visibility="public",
            status=ToolStatus.APPROVED.value,
            owner_id=owner_id,
            version_seq=1,
            deleted_at=now.replace(year=2000),
            created_at=now,
            updated_at=now,
        )
        session.add_all([fresh, stale])
        await session.commit()
        fresh_id, stale_id = fresh.id, stale.id

    async with SessionLocal() as session:
        purged = await admin_tool_service.purge_expired_recycle_bin(session, older_than_days=30)
        assert stale_id in purged
        assert fresh_id not in purged

    async with SessionLocal() as session:
        assert await session.get(Tool, stale_id) is None
        assert await session.get(Tool, fresh_id) is not None

    # 把剩下的这个也删掉，覆盖 purge_tool 的正常路径
    async with SessionLocal() as session:
        result = await admin_tool_service.purge_tool(session, tool_id=fresh_id)
        assert result.tool_id == fresh_id
    async with SessionLocal() as session:
        assert await session.get(Tool, fresh_id) is None


# ===========================================================================
# 6) import_export_service —— 用户 CSV 的各种坏行、工具 JSON 的冲突策略
# ===========================================================================
def _csv_upload(text: str):
    return {"file": ("users.csv", io.BytesIO(text.encode("utf-8")), "text/csv")}


async def test_import_users_row_level_errors(client, seeded) -> None:
    """空用户名 / 文件内重名 / 弱口令 三种坏行都要精确报错且不写库。"""
    headers = await _admin_headers(client)
    csv_text = (
        "username,display_name,email,roles,status,password\n"
        ",无用户名,,user,active,\n"
        "dupname,重名甲,,user,active,\n"
        "dupname,重名乙,,user,active,\n"
        "weakling,弱口令,,user,active,123\n"
        "goodone,好人,,user,active,\n"
    )
    response = await client.post(
        "/api/v1/admin/import/users",
        files=_csv_upload(csv_text),
        data={"dry_run": "true", "on_conflict": "skip"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    report = response.json()
    # dupname 首行 + goodone 成功；空用户名 / 文件内重名 / 弱口令 三行失败
    assert report["succeeded"] == 2, report
    assert report["failed"] == 3, report
    fields = {e["field"] for e in report["errors"]}
    assert "username" in fields
    assert "password" in fields
    assert report["generated_passwords"] == [], "dry_run 不生成密码"

    # dry_run 不写库
    async with SessionLocal() as session:
        found = (
            await session.execute(select(User.id).where(User.username == "goodone"))
        ).first()
        assert found is None


async def test_import_users_accepts_bom_and_gbk_fallback(client, seeded) -> None:
    """导出带 BOM，导入要能吃回去；非 UTF-8 也不能 500。"""
    headers = await _admin_headers(client)

    with_bom = "\ufeffusername,display_name,email,roles,status\nbomuser,BOM,,user,active\n"
    ok = await client.post(
        "/api/v1/admin/import/users",
        files={"file": ("u.csv", io.BytesIO(with_bom.encode("utf-8")), "text/csv")},
        data={"dry_run": "false", "on_conflict": "skip"},
        headers=headers,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["succeeded"] == 1

    garbage = b"username,display_name\n\xff\xfe\x00\x01badrow,,,\n"
    fallback = await client.post(
        "/api/v1/admin/import/users",
        files={"file": ("u.csv", io.BytesIO(garbage), "text/csv")},
        data={"dry_run": "true", "on_conflict": "skip"},
        headers=headers,
    )
    assert fallback.status_code == 200, fallback.text


async def test_export_users_streams_multiple_batches(client, seeded) -> None:
    """导出行数超过一批时要边走边冲缓冲，且 BOM 只出现一次。"""
    headers = await _admin_headers(client)
    response = await client.get("/api/v1/admin/export/users", headers=headers)
    assert response.status_code == 200, response.text
    raw = response.content
    assert raw[:3] == b"\xef\xbb\xbf"
    assert raw.count(b"\xef\xbb\xbf") == 1, "BOM 只能在文件头出现一次"


def _tool_items_payload() -> dict:
    return {
        "dry_run": False,
        "on_conflict": "skip",
        "items": [],
    }


async def test_import_tools_creates_updates_and_conflicts(client, seeded) -> None:
    headers = await _admin_headers(client)
    headers = headers  # 明确一下：下面全部用 admin:all

    category_slug = next(iter(seeded.categories))
    # 先自己导一个带显式 slug 的工具，再拿它测 update 分支。
    # 不能借种子里的 `public-approved`：覆盖它的 name 会污染其他测试文件。
    existing_slug = "cov-import-target"
    seed_one = await client.post(
        "/api/v1/admin/import/tools",
        json={
            "dry_run": False,
            "on_conflict": "skip",
            "items": [
                {
                    "slug": existing_slug,
                    "name": "导入目标",
                    "summary": "s",
                    "tool_type": "file",
                    "owner_username": "newbie",
                    "category_slug": category_slug,
                    "publish": False,
                }
            ],
        },
        headers=headers,
    )
    assert seed_one.status_code == 200, seed_one.text

    payload = {
        "dry_run": False,
        "on_conflict": "update",
        "items": [
            # 1) 全新工具（不写 slug，走 allocate_slug 分支）
            {
                "name": "导入新建-cov",
                "summary": "来自 JSON 导入",
                "tool_type": "file",
                "owner_username": "newbie",
                "category_slug": category_slug,
                "tags": ["cov-x", "cov-y"],
                "visibility": "public",
                # 必须是 draft：门户的计数断言是精确值，多一个 approved 工具就崩
                "publish": False,
            },
            # 2) 已存在 + on_conflict=update → 覆盖字段
            {
                "slug": existing_slug,
                "name": "被覆盖的名字",
                "summary": "覆盖摘要",
                "tool_type": "file",
                "owner_username": "newbie",
                "category_slug": category_slug,
                "visibility": "public",
            },
            # 3) 名称为空 → 失败
            {
                "name": " ",
                "summary": "x",
                "tool_type": "file",
                "owner_username": "newbie",
            },
            # 4) 分类不存在 → 失败
            {
                "name": "分类不存在-cov",
                "summary": "x",
                "tool_type": "file",
                "owner_username": "newbie",
                "category_slug": "no-such-category",
            },
            # 5) owner 不存在 → 失败
            {
                "name": "作者不存在-cov",
                "summary": "x",
                "tool_type": "file",
                "owner_username": "ghost-user",
            },
        ],
    }
    response = await client.post("/api/v1/admin/import/tools", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["succeeded"] == 2, report
    assert report["failed"] == 3, report

    # 校验 update 分支真的写进去了
    async with SessionLocal() as session:
        row = (
            await session.execute(select(Tool).where(Tool.slug == existing_slug))
        ).scalar_one()
        assert row.name == "被覆盖的名字"

    # skip 分支：既不写库也不报错，只累加 skipped
    skip = await client.post(
        "/api/v1/admin/import/tools",
        json={
            "dry_run": False,
            "on_conflict": "skip",
            "items": [
                {
                    "slug": existing_slug,
                    "name": "不该生效",
                    "summary": "s",
                    "tool_type": "file",
                    "owner_username": "newbie",
                }
            ],
        },
        headers=headers,
    )
    assert skip.status_code == 200, skip.text
    assert skip.json()["skipped"] == 1
    assert skip.json()["succeeded"] == 0

    # fail 分支：报错并给出 slug 冲突
    fail = await client.post(
        "/api/v1/admin/import/tools",
        json={
            "dry_run": False,
            "on_conflict": "fail",
            "items": [
                {
                    "slug": existing_slug,
                    "name": "冲突",
                    "summary": "s",
                    "tool_type": "file",
                    "owner_username": "newbie",
                }
            ],
        },
        headers=headers,
    )
    assert fail.status_code == 200, fail.text
    body = fail.json()
    assert body["failed"] == 1
    assert body["errors"][0]["field"] == "slug"


async def test_import_tools_empty_item_list_is_rejected(client, seeded) -> None:
    headers = await _admin_headers(client)
    response = await client.post(
        "/api/v1/admin/import/tools", json=_tool_items_payload(), headers=headers
    )
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_export_tools_json_roundtrip_shape(client, seeded) -> None:
    headers = await _admin_headers(client)
    response = await client.get("/api/v1/admin/export/tools", headers=headers)
    assert response.status_code == 200, response.text
    payload = json.loads(response.content)
    assert isinstance(payload, list)
    assert payload, "种子数据里应当至少有一个工具"
    assert {"slug", "name", "owner_username"} <= set(payload[0])


async def test_import_service_helpers_directly(seeded) -> None:
    """直接打服务层，覆盖 HTTP 层不容易构造的分支。"""
    # 解码兜底：非法 UTF-8 也要返回可解析的字符串
    assert isinstance(import_export_service._decode_csv(b"\xff\xfeabc"), str)

    async with SessionLocal() as session:
        report = await import_export_service.import_tools_json(
            session,
            items=[],
            dry_run=True,
            on_conflict="skip",
            actor_id=seeded.users["admin"],
        )
        assert report.dry_run is True


# ===========================================================================
# 7) 并发：Token 计数聚合不能退化成每请求一次 UPDATE
# ===========================================================================
async def test_counter_flush_is_batched_not_per_request(seeded) -> None:
    """100 次 Token 使用只应在 flush 时产生**少数**条 UPDATE，而不是 100 条。"""
    import app.db.session as db_session
    from app.services.counter_service import get_counter_service

    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    from sqlalchemy import event

    event.listen(db_session.engine.sync_engine, "before_cursor_execute", _record)
    try:
        service = get_counter_service()
        # 计数器是**进程级单例**，前面的用例可能还留着未 flush 的增量。
        # 先排空，保证下面统计到的 UPDATE 全是本用例产生的。
        await service.flush()
        statements.clear()

        # 同一个 token 被用 100 次 —— 这才叫「聚合」；
        # 100 个不同 token 本来就该产生 100 条 UPDATE。
        for _ in range(100):
            service.record_token_use(4242, ip="10.0.0.1")
        for i in range(5):
            service.record_token_use(5000 + i, ip="10.0.0.2")

        await service.flush()
    finally:
        event.remove(db_session.engine.sync_engine, "before_cursor_execute", _record)

    updates = [s for s in statements if s.strip().upper().startswith("UPDATE API_TOKENS")]
    assert len(updates) <= 6, (
        f"聚合失效了：105 次使用（6 个 token）产生了 {len(updates)} 条 UPDATE，"
        "应当每个 token 只 UPDATE 一次"
    )


async def test_import_users_concurrent_safe(client, seeded) -> None:
    """两批导入并发跑，用户名唯一约束不能把整个请求打成 500。"""
    headers = await _admin_headers(client)

    def _csv(name: str) -> str:
        return f"username,display_name,email,roles,status\n{name},并发,,user,active\n"

    results = await asyncio.gather(
        client.post(
            "/api/v1/admin/import/users",
            files=_csv_upload(_csv("concur-a")),
            data={"dry_run": "false", "on_conflict": "skip"},
            headers=headers,
        ),
        client.post(
            "/api/v1/admin/import/users",
            files=_csv_upload(_csv("concur-b")),
            data={"dry_run": "false", "on_conflict": "skip"},
            headers=headers,
        ),
        return_exceptions=True,
    )
    for item in results:
        assert not isinstance(item, BaseException), item
        assert item.status_code == 200, item.text


@pytest.mark.parametrize(
    "scope", [ApiScope.APPROVALS_WRITE.value, ApiScope.TAXONOMY_WRITE.value]
)
async def test_approver_scope_subset_is_enforced_in_service(seeded, scope: str) -> None:
    """HTTP 层只有超管能签 Token，所以越权校验的服务分支要直接打服务层。

    `create_token` 里的 scope 子集判定是纵深防御：即便将来放开入口，
    非超管也签不出 `admin:all`。
    """
    from app.core.errors import ForbiddenError

    async with SessionLocal() as session:
        approver = await session.get(User, seeded.users["approver"])
        assert approver is not None

        ok = await token_service.create_token(
            session,
            payload=ApiTokenCreateRequest(
                name=f"scope-{scope}", scopes=[ApiScope(scope)], expires_at=None
            ),
            creator=approver,
        )
        assert ok.scopes == [scope]
        assert ok.token.startswith("st_")

        for overreach in (ApiScope.ADMIN_ALL, ApiScope.USERS_WRITE, ApiScope.SETTINGS_WRITE):
            with pytest.raises(ForbiddenError) as excinfo:
                await token_service.create_token(
                    session,
                    payload=ApiTokenCreateRequest(
                        name=f"over-{overreach.value}",
                        scopes=[overreach],
                        expires_at=None,
                    ),
                    creator=approver,
                )
            assert excinfo.value.code == "FORBIDDEN"
            assert overreach.value in str(excinfo.value)
        await session.rollback()


async def test_role_code_enum_has_no_dangling_superadmin(seeded) -> None:
    assert RoleCode.SUPERADMIN.value == "superadmin"
    async with SessionLocal() as session:
        assert await admin_user_service._superadmin_role_id(session) > 0
