"""M2 生命周期测试：创建 → 上传 → 提交 → 审批 → 门户可见 → 下载。

覆盖验收清单 1~8、11、12、14、15、16、20。
"""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.enums import VersionStatus
from app.models.tool import ToolVersion
from app.repositories import tool_versions as versions_repo
from app.storage import get_storage
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


# ---------------------------------------------------------------------------
# 1. 创建 file 工具 → 上传 zip → 提交审批 → pending，门户不可见
# ---------------------------------------------------------------------------
async def test_create_submit_pending_not_in_portal(client, seeded) -> None:
    token = await login(client, "outsider")
    tool = await create_tool(client, token, name="生命周期工具 A")
    assert tool["status"] == "draft"
    assert tool["slug"].startswith("sheng-ming-zhou-qi") or tool["slug"]

    content = zip_bytes({"readme.txt": b"hello"})
    response = await upload_version(
        client, token, tool["id"], version="1.0.0", file_bytes=content, file_name="a.zip"
    )
    assert response.status_code == 201, response.text
    body = response.json()
    # 默认 auto_submit=false：上传只创建版本，**不**改变工具状态
    assert body["status"] == "pending"
    assert body["tool_status"] == "draft", "auto_submit=false 时上传不应把工具推进到 pending"
    assert body["file_size"] == len(content)
    # SHA256 必须与上传内容一致
    assert body["file_sha256"] == hashlib.sha256(content).hexdigest()

    # 未提交前不在审批队列里
    approver_token = await login(client, "approver")
    queue_before = await client.get("/api/v1/admin/approvals", headers=auth(approver_token))
    assert tool["id"] not in [i["tool_id"] for i in queue_before.json()["items"]]

    submitted = await submit(client, token, tool["id"])
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "pending"
    assert submitted.json()["auto_approved"] is False

    # 门户不可见（pending 不在门户状态集里）
    portal = await client.get("/api/v1/tools", params={"q": tool["name"]}, headers=auth(token))
    assert portal.json()["total"] == 0

    # 提交后审批队列可见
    queue = await client.get("/api/v1/admin/approvals", headers=auth(approver_token))
    ids = [item["tool_id"] for item in queue.json()["items"]]
    assert tool["id"] in ids
    item = next(i for i in queue.json()["items"] if i["tool_id"] == tool["id"])
    assert item["submission_type"] == "new_tool"
    assert item["pending_version"]["version"] == "1.0.0"
    assert item["waiting_hours"] >= 0


# ---------------------------------------------------------------------------
# 3. 驳回（首次提交）→ 工具 rejected，理由可见
# ---------------------------------------------------------------------------
async def test_reject_first_submission_marks_tool_rejected(client, seeded) -> None:
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    tool = await create_tool(client, owner, name="待驳回工具")
    await upload_version(
        client, owner, tool["id"], version="1.0.0",
        file_bytes=zip_bytes({"a.txt": b"x"}), file_name="a.zip",
    )
    await submit(client, owner, tool["id"])

    response = await reject(client, approver, tool["id"], reason="包内脚本含硬编码密码，请移除后重提")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "rejected"
    assert body["rejected_version"]["version"] == "1.0.0"
    assert body["pending_version"] is None

    detail = await client.get(f"/api/v1/me/tools/{tool['id']}", headers=auth(owner))
    assert detail.json()["status"] == "rejected"
    assert "硬编码" in detail.json()["reject_reason"]

    portal = await client.get("/api/v1/tools", params={"q": "待驳回工具"}, headers=auth(owner))
    assert portal.json()["total"] == 0

    # 驳回理由过短要被 Pydantic 拦掉（FR-APPR-08 ≥5 字）
    tool2 = await create_tool(client, owner, name="短理由工具")
    await upload_version(
        client, owner, tool2["id"], version="1.0.0",
        file_bytes=zip_bytes({"a.txt": b"x"}), file_name="a.zip",
    )
    await submit(client, owner, tool2["id"])
    bad = await reject(client, approver, tool2["id"], reason="太短")
    assert bad.status_code in (400, 422)


# ---------------------------------------------------------------------------
# 4. 重新提交 → 批准 → 门户可见 → 下载成功且 SHA256 一致
# ---------------------------------------------------------------------------
async def test_resubmit_approve_then_download(client, seeded) -> None:
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    tool = await create_tool(client, owner, name="可下载工具")
    content = zip_bytes({"payload.bin": b"payload-content" * 100})
    await upload_version(
        client, owner, tool["id"], version="1.0.0", file_bytes=content, file_name="pkg.zip"
    )
    await submit(client, owner, tool["id"])
    await reject(client, approver, tool["id"], reason="请补充变更说明后重新提交")
    await submit(client, owner, tool["id"])

    approved = await approve(client, approver, tool["id"])
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "approved"
    assert body["current_version"]["version"] == "1.0.0"
    assert body["version_seq"] >= 2

    slug = (await client.get(f"/api/v1/me/tools/{tool['id']}", headers=auth(owner))).json()["slug"]

    # 门户可见
    portal = await client.get("/api/v1/tools", params={"q": "可下载工具"}, headers=auth(owner))
    assert portal.json()["total"] == 1

    # 详情
    detail = await client.get(f"/api/v1/tools/{slug}", headers=auth(owner))
    assert detail.status_code == 200
    dbody = detail.json()
    assert dbody["status"] == "approved"
    assert dbody["can_download"] is True
    assert dbody["permissions"]["can_download"] is True
    assert dbody["current_version"]["file_sha256"] == hashlib.sha256(content).hexdigest()

    # 下载
    download = await client.get(f"/api/v1/tools/{slug}/download", headers=auth(owner))
    assert download.status_code == 200, download.text
    assert download.content == content
    assert hashlib.sha256(download.content).hexdigest() == hashlib.sha256(content).hexdigest()
    assert "attachment" in download.headers["content-disposition"]


# ---------------------------------------------------------------------------
# 5/6/7. 新版本语义 —— 本任务最容易做错的地方
# ---------------------------------------------------------------------------
async def test_new_version_keeps_old_version_serving(client, seeded) -> None:
    """FR-VER-03：新版本待审期间**旧版本继续对外服务**。"""
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    slug, tool_id, v1 = await publish_tool(client, owner, approver, name="多版本工具")

    v2_content = zip_bytes({"v2.bin": b"version-two"})
    up = await upload_version(
        client, owner, tool_id, version="1.1.0", file_bytes=v2_content, file_name="v11.zip"
    )
    assert up.status_code == 201, up.text
    assert up.json()["tool_status"] == "approved", "上传本身不改状态"
    # 显式提交后才进入 pending_update
    submitted = await submit(client, owner, tool_id)
    assert submitted.status_code == 200, submitted.text
    detail_after_submit = await client.get(f"/api/v1/tools/{slug}", headers=auth(owner))
    assert detail_after_submit.json()["status"] == "pending_update"

    # 门户仍然可见，下载的仍然是 1.0.0
    detail = await client.get(f"/api/v1/tools/{slug}", headers=auth(owner))
    assert detail.json()["status"] == "pending_update"
    assert detail.json()["current_version"]["version"] == "1.0.0"
    assert detail.json()["pending_version"]["version"] == "1.1.0"

    download = await client.get(f"/api/v1/tools/{slug}/download", headers=auth(owner))
    assert download.status_code == 200
    assert download.content == v1  # 旧版本内容
    assert download.headers["content-sha256"] == hashlib.sha256(v1).hexdigest()

    # owner 能看到「有新版本待审」，其他普通用户看不到
    owner_view = await client.get("/api/v1/tools", params={"q": "多版本工具"}, headers=auth(owner))
    assert owner_view.json()["items"][0]["has_pending_version"] is True
    outsider_view = await client.get(
        "/api/v1/tools", params={"q": "多版本工具"}, headers=auth(await login(client, "viewer"))
    )
    assert outsider_view.json()["items"][0]["has_pending_version"] is False

    # 批准新版本 → 下载变成 1.1.0，1.0.0 进入历史且仍可下载
    ok = await approve(client, approver, tool_id)
    assert ok.status_code == 200, ok.text
    assert ok.json()["current_version"]["version"] == "1.1.0"
    assert ok.json()["superseded_version"]["version"] == "1.0.0"

    detail2 = await client.get(f"/api/v1/tools/{slug}", headers=auth(owner))
    assert detail2.json()["current_version"]["version"] == "1.1.0"
    assert detail2.json()["history_version_count"] == 1

    new_download = await client.get(f"/api/v1/tools/{slug}/download", headers=auth(owner))
    assert new_download.content == v2_content

    versions = await client.get(f"/api/v1/tools/{slug}/versions", headers=auth(owner))
    old = next(v for v in versions.json() if v["version"] == "1.0.0")
    assert old["status"] == "superseded"
    assert old["can_download"] is True
    old_download = await client.get(
        f"/api/v1/tools/{slug}/download", params={"version_id": old["id"]}, headers=auth(owner)
    )
    assert old_download.status_code == 200
    assert old_download.content == v1


async def test_reject_new_version_keeps_tool_approved(client, seeded) -> None:
    """**独立测试**：驳回新版本 → 工具退回 approved，当前版本不变。

    这是 docs/03 §3.10 明确点名的语义陷阱：驳回「新版本」不等于驳回工具本身。
    """
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    slug, tool_id, v1 = await publish_tool(client, owner, approver, name="驳回新版本工具")

    await upload_version(
        client, owner, tool_id, version="2.0.0",
        file_bytes=zip_bytes({"v2.bin": b"bad"}), file_name="v2.zip",
    )
    await submit(client, owner, tool_id)
    before = await client.get(f"/api/v1/tools/{slug}", headers=auth(owner))
    assert before.json()["status"] == "pending_update"
    assert before.json()["current_version"]["version"] == "1.0.0"

    response = await reject(client, approver, tool_id, reason="2.0.0 的包内含有未脱敏的配置样例")
    assert response.status_code == 200, response.text
    body = response.json()
    # 关键断言：工具仍是 approved
    assert body["status"] == "approved", "驳回新版本后工具必须回到 approved"
    assert body["rejected_version"]["version"] == "2.0.0"
    assert body["pending_version"] is None

    after = await client.get(f"/api/v1/tools/{slug}", headers=auth(owner))
    assert after.json()["status"] == "approved"
    assert after.json()["current_version"]["version"] == "1.0.0", "当前版本不能变"
    assert after.json()["pending_version"] is None
    # 工具本身不应带驳回理由（理由属于那个版本）
    assert after.json()["reject_reason"] is None

    # 门户仍然可见，下载仍是 1.0.0
    portal = await client.get("/api/v1/tools", params={"q": "驳回新版本工具"}, headers=auth(owner))
    assert portal.json()["total"] == 1
    download = await client.get(f"/api/v1/tools/{slug}/download", headers=auth(owner))
    assert download.status_code == 200
    assert download.content == v1

    # 被驳回版本带着理由出现在版本列表里
    versions = await client.get(f"/api/v1/tools/{slug}/versions", headers=auth(owner))
    rejected = next(v for v in versions.json() if v["version"] == "2.0.0")
    assert rejected["status"] == "rejected"
    assert "未脱敏" in rejected["reject_reason"]
    assert rejected["can_download"] is False


# ---------------------------------------------------------------------------
# 8. 历史版本淘汰：保留 10 份，超出部分 purged 且磁盘文件真的没了
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("total_versions", "expected_purged"), [(12, 1), (13, 2)])
async def test_history_purge_keeps_ten_and_deletes_files(
    client, seeded, total_versions: int, expected_purged: int
) -> None:
    """FR-VER-08：`superseded` 按 created_at 倒序只保留 10 个。

    超出的版本：**物理删除存储文件，数据库行标记 `purged`（保留行）**。

    注意算术：N 个版本全部批准后，`superseded + purged = N - 1`（当前版本不算历史），
    其中 `superseded = min(N-1, 10)`、`purged = max(0, N-1-10)`。
    所以 12 个版本 → 1 个 purged，13 个版本 → 2 个 purged。
    """
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    tool = await create_tool(client, owner, name=f"淘汰测试 {total_versions}")

    storage = get_storage()
    storage_paths: dict[str, str] = {}
    for index in range(1, total_versions + 1):
        version = f"1.0.{index}"
        content = zip_bytes({f"f{index}.txt": f"content-{index}".encode()})
        response = await upload_version(
            client, owner, tool["id"], version=version, file_bytes=content,
            file_name=f"v{index}.zip",
        )
        assert response.status_code == 201, response.text
        submitted = await submit(client, owner, tool["id"])
        assert submitted.status_code == 200, submitted.text
        ok = await approve(client, approver, tool["id"])
        assert ok.status_code == 200, ok.text

        async with SessionLocal() as session:
            row = await versions_repo.get_by_version(session, tool["id"], version)
            assert row is not None
            storage_paths[version] = row.storage_path or ""

    # ---- 数据库状态 ----
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(ToolVersion).where(ToolVersion.tool_id == tool["id"])
            )
        ).scalars().all()
    by_status: dict[str, list[str]] = {}
    for row in rows:
        by_status.setdefault(row.status, []).append(row.version)

    assert len(rows) == total_versions, "所有版本行都必须保留（不能删行）"
    assert len(by_status.get(VersionStatus.SUPERSEDED.value, [])) == 10, (
        f"superseded 应恰好 10 个，实际 {len(by_status.get('superseded', []))}"
    )
    purged = by_status.get(VersionStatus.PURGED.value, [])
    assert len(purged) == expected_purged, f"purged 应 {expected_purged} 个，实际 {len(purged)}"

    # 被淘汰的必须是**最旧**的那几个
    purged_sorted = sorted(purged, key=lambda v: int(v.split(".")[-1]))
    assert purged_sorted == [f"1.0.{i}" for i in range(1, expected_purged + 1)], (
        f"被淘汰的应该是最旧的版本，实际 {purged_sorted}"
    )

    # ---- 磁盘状态：purged 的文件必须真的不存在 ----
    for version in purged:
        path = storage_paths[version]
        assert path, f"{version} 应该有 storage_path"
        assert not storage.exists(path), f"purged 版本 {version} 的文件仍然存在于 {path}"
    # 未被淘汰的版本文件必须都还在
    for version, path in storage_paths.items():
        if version not in purged:
            assert storage.exists(path), f"{version} 的文件不应该被删掉"

    # ---- 接口表现：purged 出现在列表里但不可下载 ----
    slug = (await client.get(f"/api/v1/me/tools/{tool['id']}", headers=auth(owner))).json()["slug"]
    versions_resp = await client.get(f"/api/v1/tools/{slug}/versions", headers=auth(owner))
    listed = {v["version"]: v for v in versions_resp.json()}
    for version in purged:
        assert version in listed, "purged 版本仍应出现在版本列表（显示为已归档）"
        assert listed[version]["can_download"] is False
        assert listed[version]["purged_at"] is not None

    purged_id = listed[purged[0]]["id"]
    blocked = await client.get(
        f"/api/v1/tools/{slug}/download", params={"version_id": purged_id}, headers=auth(owner)
    )
    assert blocked.status_code == 404, "已归档版本不可下载"
    assert blocked.json()["code"] == "NOT_FOUND"
