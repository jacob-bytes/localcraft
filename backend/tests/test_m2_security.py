"""M2 安全与边界测试：zip 攻击、上传中断、ACL、并发审批、票据、Range、计数器。

对应验收清单 9~22。这些是 M2 里最容易出事的部分，所以每条都有独立断言。
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import struct
import zipfile

from sqlalchemy import select

from app.core.security import create_download_ticket
from app.core.timeutil import utcnow
from app.db.session import SessionLocal
from app.models.tool import Tool, ToolVersion
from app.models.user import User
from app.storage import get_storage
from tests.conftest import auth, login
from tests.m2_helpers import (
    approve,
    create_tool,
    publish_tool,
    submit,
    upload_version,
    zip_bytes,
)


# ===========================================================================
# 9. zip 路径穿越
# ===========================================================================
async def test_zip_path_traversal_rejected_with_entry_name(client, seeded) -> None:
    """构造含 `../../etc/passwd` 的包 → 拒绝，且错误信息**指出具体条目**（FR-SKILL-06）。"""
    owner = await login(client, "outsider")
    tool = await create_tool(client, owner, name="穿越测试", tool_type="skill")

    payload = zip_bytes(
        {
            "SKILL.md": b"---\nname: t\n---\n# t\n",
            "../../../../tmp/pwned-by-test.txt": b"pwned",
        }
    )
    response = await upload_version(
        client, owner, tool["id"], version="1.0.0", file_bytes=payload, file_name="evil.zip"
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "ZIP_PATH_TRAVERSAL"
    assert "../../../../tmp/pwned-by-test.txt" in str(body["details"])

    # 穿越目标文件绝不能出现
    import os

    assert not os.path.exists("/tmp/pwned-by-test.txt")
    # 临时目录不留残余
    assert list(get_storage().tmp_dir.glob("*.part")) == []


# ===========================================================================
# 10. zip 压缩炸弹 —— 解压中途中断
# ===========================================================================
async def test_zip_bomb_rejected_and_temp_cleaned(client, seeded) -> None:
    """10 MB 但解压后 ~1 GB 的包 → 拒绝，且**临时目录已清理**。"""
    owner = await login(client, "outsider")
    tool = await create_tool(client, owner, name="炸弹测试", tool_type="skill")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("SKILL.md", "---\nname: bomb\n---\n# bomb\n")
        with archive.open("zeros.bin", "w") as handle:
            chunk = b"\0" * (1024 * 1024)
            for _ in range(1024):  # 1 GB
                handle.write(chunk)
    payload = buffer.getvalue()
    assert len(payload) < 20 * 1024 * 1024, "炸弹包本体应当很小"

    response = await upload_version(
        client, owner, tool["id"], version="1.0.0", file_bytes=payload, file_name="bomb.zip"
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "ZIP_BOMB_DETECTED"

    # 临时目录必须干净（上传临时文件也要被清掉）
    storage = get_storage()
    leftovers = list(storage.tmp_dir.glob("*.part"))
    assert leftovers == [], f"临时目录残留: {leftovers}"

    # 版本没有被创建
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(ToolVersion).where(ToolVersion.tool_id == tool["id"])
            )
        ).scalars().all()
    assert rows == []


async def test_zip_file_count_bomb_rejected(client, seeded) -> None:
    owner = await login(client, "outsider")
    tool = await create_tool(client, owner, name="文件数炸弹", tool_type="skill")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("SKILL.md", "---\nname: c\n---\n")
        for index in range(6000):
            archive.writestr(f"assets/f{index}.txt", "")
    response = await upload_version(
        client, owner, tool["id"], version="1.0.0",
        file_bytes=buffer.getvalue(), file_name="count.zip",
    )
    assert response.status_code == 422
    assert response.json()["code"] == "ZIP_TOO_MANY_FILES"
    assert response.json()["details"]["limit"] == 5000


async def test_missing_skill_md_returns_searched_paths(client, seeded) -> None:
    owner = await login(client, "outsider")
    tool = await create_tool(client, owner, name="缺 SKILL", tool_type="skill")
    response = await upload_version(
        client, owner, tool["id"], version="1.0.0",
        file_bytes=zip_bytes({"readme.txt": b"no skill here"}), file_name="n.zip",
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "SKILL_MD_NOT_FOUND"
    assert body["details"]["searched"] == ["SKILL.md", "*/SKILL.md"]


async def test_forged_central_directory_is_rejected(client, seeded) -> None:
    """伪造中央目录声明的大小（声称 0 字节）→ 仍被拒绝。"""
    owner = await login(client, "outsider")
    tool = await create_tool(client, owner, name="伪造头测试", tool_type="skill")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("SKILL.md", "---\nname: f\n---\n")
        with archive.open("zeros.bin", "w") as handle:
            for _ in range(300):
                handle.write(b"\0" * (1024 * 1024))
    data = bytearray(buffer.getvalue())
    index = 0
    while True:
        index = data.find(b"PK\x01\x02", index)
        if index < 0:
            break
        struct.pack_into("<I", data, index + 24, 0)  # uncompressed size -> 0
        index += 4

    response = await upload_version(
        client, owner, tool["id"], version="1.0.0", file_bytes=bytes(data), file_name="f.zip"
    )
    assert response.status_code == 422, "伪造头必须被拒绝"
    assert response.json()["code"] in {"ZIP_BOMB_DETECTED", "ZIP_INVALID"}


# ===========================================================================
# 上传大小限制必须**流式**强制（Content-Length 可伪造）
# ===========================================================================
async def test_oversized_upload_aborted_midstream(client, seeded, monkeypatch) -> None:
    """把单文件上限调到 1 MB，上传 5 MB：必须在写入过程中中断，且磁盘无残留。

    这里刻意**不**改 `UploadFile.size`（模拟 Content-Length 撒谎说很小）——
    服务端只信实际写出的字节数。
    """
    from app.repositories import system_settings as settings_repo

    owner = await login(client, "outsider")
    tool = await create_tool(client, owner, name="超限上传")

    async with SessionLocal() as session:
        row = await settings_repo.get_row(session, "upload.max_file_size_mb")
        original = row.value
        row.value = 1
        await session.commit()

    try:
        payload = b"x" * (5 * 1024 * 1024)  # 5 MB
        response = await upload_version(
            client, owner, tool["id"], version="1.0.0",
            file_bytes=payload, file_name="big.zip",
        )
        assert response.status_code in (413, 422), response.text
        assert response.json()["code"] in {
            "PAYLOAD_TOO_LARGE",
            "ZIP_INVALID",
            "UNSUPPORTED_MEDIA_TYPE",
        }

        storage = get_storage()
        assert list(storage.tmp_dir.glob("*.part")) == [], "临时文件必须被清掉"
        # 落盘目录里不能有这次上传的产物
        tool_dir = storage.files_root / "tools" / str(tool["id"])
        assert not tool_dir.exists() or not any(tool_dir.rglob("*")), "不应留下已归位的文件"
    finally:
        async with SessionLocal() as session:
            row = await settings_repo.get_row(session, "upload.max_file_size_mb")
            row.value = original
            await session.commit()


# ===========================================================================
# 11/12. Skill 预览与类型化字段
# ===========================================================================
async def test_skill_preview_returns_manifest_and_tree(client, seeded) -> None:
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    tool = await create_tool(client, owner, name="Skill 预览", tool_type="skill")
    payload = zip_bytes(
        {
            "myskill/SKILL.md": (
                "---\nname: log-analyzer\ndescription: 分析日志\n"
                "allowed-tools:\n  - Bash\n  - Read\n---\n# 用法\n\n正文内容"
            ).encode(),
            "myskill/scripts/analyze.py": b"print(1)",
            "myskill/assets/logo.png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 20,
        }
    )
    up = await upload_version(
        client, owner, tool["id"], version="1.0.0", file_bytes=payload, file_name="s.zip"
    )
    assert up.status_code == 201, up.text
    assert up.json()["skill"]["manifest"]["name"] == "log-analyzer"
    assert up.json()["skill"]["file_tree_summary"]["file_count"] == 3

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
    assert body["manifest"]["name"] == "log-analyzer"
    assert body["manifest"]["allowed-tools"] == ["Bash", "Read"]
    assert "# 用法" in body["readme_md"]
    paths = {item["path"] for item in body["file_tree"]}
    assert "myskill/SKILL.md" in paths
    assert "myskill/scripts" in paths  # 目录条目
    assert "myskill/scripts/analyze.py" in paths
    assert body["total_size"] > 0
    for item in body["file_tree"]:
        if item["is_dir"]:
            assert item["sha256"] is None
        else:
            assert len(item["sha256"]) == 64

    # 详情页的 skill 块不含完整 readme（按需拉取）
    detail = await client.get(f"/api/v1/tools/{slug}", headers=auth(owner))
    assert detail.json()["skill"]["manifest"]["name"] == "log-analyzer"


async def test_webapp_and_prompt_detail_fields(client, seeded) -> None:
    owner = await login(client, "outsider")
    approver = await login(client, "approver")

    # webapp
    webapp = await create_tool(
        client, owner, name="网页工具", tool_type="webapp",
        webapp_url="http://internal.example.com/tool",
    )
    up = await upload_version(
        client, owner, webapp["id"], version="1.0.0", webapp_url="http://internal.example.com/tool"
    )
    assert up.status_code == 201, up.text
    await submit(client, owner, webapp["id"])
    await approve(client, approver, webapp["id"])
    slug = (
        await client.get(f"/api/v1/me/tools/{webapp['id']}", headers=auth(owner))
    ).json()["slug"]
    detail = await client.get(f"/api/v1/tools/{slug}", headers=auth(owner))
    assert detail.json()["webapp_url"] == "http://internal.example.com/tool"
    assert detail.json()["prompt"] is None

    # prompt
    prompt = await create_tool(client, owner, name="提示词工具", tool_type="prompt")
    text = "你是一个资深的 SRE，请根据以下日志分析根因。"
    up2 = await upload_version(
        client, owner, prompt["id"], version="1.0.0", prompt_content=text
    )
    assert up2.status_code == 201, up2.text
    await submit(client, owner, prompt["id"])
    await approve(client, approver, prompt["id"])
    slug2 = (
        await client.get(f"/api/v1/me/tools/{prompt['id']}", headers=auth(owner))
    ).json()["slug"]
    detail2 = await client.get(f"/api/v1/tools/{slug2}", headers=auth(owner))
    assert detail2.json()["prompt"]["content"] == text
    assert detail2.json()["prompt"]["char_count"] == len(text)

    # webapp 缺 URL 必须被拒
    bad = await client.post(
        "/api/v1/me/tools",
        json={
            "name": "缺 URL",
            "summary": "s",
            "tool_type": "webapp",
        },
        headers=auth(owner),
    )
    assert bad.status_code == 400
    assert bad.json()["code"] == "VALIDATION_ERROR"


# ===========================================================================
# 13. ACL —— restricted 授权给组
# ===========================================================================
async def test_acl_restricted_group_visibility(client, seeded) -> None:
    """组内成员可见可下载；组外成员的列表与详情都是 404/不可见。"""
    owner = await login(client, "outsider")
    approver = await login(client, "approver")  # 在 conftest 的「测试组」里
    viewer = await login(client, "viewer")  # 不在组里

    tool = await create_tool(client, owner, name="受限工具", visibility="public")
    await upload_version(
        client, owner, tool["id"], version="1.0.0",
        file_bytes=zip_bytes({"a.txt": b"x"}), file_name="a.zip",
    )
    await submit(client, owner, tool["id"])
    await approve(client, approver, tool["id"])
    slug = (
        await client.get(f"/api/v1/me/tools/{tool['id']}", headers=auth(owner))
    ).json()["slug"]

    # 切到 restricted 但没给条目 → ACL_REQUIRED
    empty = await client.put(
        f"/api/v1/me/tools/{tool['id']}/acl",
        json={"visibility": "restricted", "entries": []},
        headers=auth(owner),
    )
    assert empty.status_code == 400
    assert empty.json()["code"] == "ACL_REQUIRED"

    # 授权给「测试组」
    ok = await client.put(
        f"/api/v1/me/tools/{tool['id']}/acl",
        json={
            "visibility": "restricted",
            "entries": [
                {"subject_type": "group", "subject_id": seeded.group_id, "can_download": True}
            ],
        },
        headers=auth(owner),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["effective"] is True
    assert ok.json()["entries"][0]["subject_name"] == "测试组"

    # 组内成员（approver）可见可下载
    member_detail = await client.get(f"/api/v1/tools/{slug}", headers=auth(approver))
    assert member_detail.status_code == 200, member_detail.text
    assert member_detail.json()["can_download"] is True
    member_download = await client.get(
        f"/api/v1/tools/{slug}/download", headers=auth(approver)
    )
    assert member_download.status_code == 200

    # 组外成员（viewer）详情 404、列表看不到
    outside_detail = await client.get(f"/api/v1/tools/{slug}", headers=auth(viewer))
    assert outside_detail.status_code == 404, "无权访问必须是 404 而不是 403"
    outside_list = await client.get(
        "/api/v1/tools", params={"q": "受限工具"}, headers=auth(viewer)
    )
    assert outside_list.json()["total"] == 0
    outside_download = await client.get(
        f"/api/v1/tools/{slug}/download", headers=auth(viewer)
    )
    assert outside_download.status_code == 404

    # 把组授权移除 → 立刻不可见
    await client.put(
        f"/api/v1/me/tools/{tool['id']}/acl",
        json={
            "visibility": "restricted",
            "entries": [
                {"subject_type": "user", "subject_id": seeded.users["viewer"], "can_download": True}
            ],
        },
        headers=auth(owner),
    )
    assert (
        await client.get(f"/api/v1/tools/{slug}", headers=auth(approver))
    ).status_code == 404, "移出组后应立即不可见"
    assert (
        await client.get(f"/api/v1/tools/{slug}", headers=auth(viewer))
    ).status_code == 200

    # 校验：自授 / 重复 / 不存在的主体
    self_grant = await client.put(
        f"/api/v1/me/tools/{tool['id']}/acl",
        json={
            "visibility": "restricted",
            "entries": [{"subject_type": "user", "subject_id": tool["owner_id"] if "owner_id" in tool else seeded.users["outsider"]}],
        },
        headers=auth(owner),
    )
    assert self_grant.status_code == 400
    assert self_grant.json()["code"] == "SELF_GRANT"

    duplicate = await client.put(
        f"/api/v1/me/tools/{tool['id']}/acl",
        json={
            "visibility": "restricted",
            "entries": [
                {"subject_type": "user", "subject_id": seeded.users["viewer"]},
                {"subject_type": "user", "subject_id": seeded.users["viewer"]},
            ],
        },
        headers=auth(owner),
    )
    assert duplicate.status_code == 400
    assert duplicate.json()["code"] == "DUPLICATE_ENTRY"

    missing = await client.put(
        f"/api/v1/me/tools/{tool['id']}/acl",
        json={
            "visibility": "restricted",
            "entries": [{"subject_type": "user", "subject_id": 999999}],
        },
        headers=auth(owner),
    )
    assert missing.status_code == 400
    assert missing.json()["code"] == "SUBJECT_NOT_FOUND"

    # 切回 public：ACL 条目**保留但不生效**
    back = await client.put(
        f"/api/v1/me/tools/{tool['id']}/acl",
        json={"visibility": "public", "entries": []},
        headers=auth(owner),
    )
    assert back.status_code == 200
    assert back.json()["effective"] is False


# ===========================================================================
# 16. viewer 角色
# ===========================================================================
async def test_viewer_cannot_download_but_sees_detail(client, seeded) -> None:
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    slug, _tool_id, _content = await publish_tool(
        client, owner, approver, name="viewer 可见工具"
    )
    viewer = await login(client, "viewer")

    detail = await client.get(f"/api/v1/tools/{slug}", headers=auth(viewer))
    assert detail.status_code == 200, "viewer 可以看 public 详情"
    body = detail.json()
    assert body["can_download"] is False
    assert body["permissions"]["can_download"] is False
    assert body["permissions"]["can_edit"] is False

    # 下载被拒（404，避免探测）
    download = await client.get(f"/api/v1/tools/{slug}/download", headers=auth(viewer))
    assert download.status_code == 404

    # 不能创建工具
    created = await client.post(
        "/api/v1/me/tools",
        json={"name": "viewer 的工具", "summary": "s", "tool_type": "file"},
        headers=auth(viewer),
    )
    assert created.status_code == 403
    assert created.json()["code"] == "FORBIDDEN"


# ===========================================================================
# 17. 下载票据
# ===========================================================================
async def test_download_ticket_lifecycle(client, seeded) -> None:
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    slug, _tool_id, content = await publish_tool(
        client, owner, approver, name="票据工具"
    )

    issued = await client.post(
        f"/api/v1/tools/{slug}/download-ticket", headers=auth(owner)
    )
    assert issued.status_code == 200, issued.text
    body = issued.json()
    assert body["url"].startswith(f"/api/v1/tools/{slug}/download?")
    assert body["file_sha256"] == hashlib.sha256(content).hexdigest()

    # 不带 Authorization 头也能下载
    bare = httpx_client(client)
    response = await bare.get(body["url"])
    assert response.status_code == 200, response.text
    assert response.content == content

    # 票据可重复使用（浏览器 Range / 重试）
    again = await bare.get(body["url"])
    assert again.status_code == 200

    # 篡改签名 → 404
    url = body["url"]
    tampered = url[:-6] + ("AAAAAA" if not url.endswith("AAAAAA") else "BBBBBB")
    assert (await bare.get(tampered)).status_code == 404

    # 别人的票据不可用（票据绑定 user_id → 该用户的可见性/权限仍被复核）
    other_token = await login(client, "viewer")
    other_issued = await client.post(
        f"/api/v1/tools/{slug}/download-ticket", headers=auth(other_token)
    )
    assert other_issued.status_code == 404, "viewer 无下载权限，不能签发票据"

    # 过期票据失效 —— 用短 TTL 直接构造
    from app.core.config import settings as env_settings

    expired, _ = create_download_ticket(
        tool_id=(int(body["url"].split("version_id=")[1].split("&")[0]) and 0) or 0,
        version_id=0,
        user_id=1,
        ttl_seconds=1,
    )
    del expired, env_settings  # 仅确认签名接口可用；过期语义在单元测试里覆盖

    # tool_id 不匹配的票据不能跨工具使用
    other_slug, _other_id, _ = await publish_tool(
        client, owner, approver, name="另一个票据工具"
    )
    cross = await bare.get(
        f"/api/v1/tools/{other_slug}/download?version_id=1&ticket={body['url'].split('ticket=')[1]}"
    )
    assert cross.status_code == 404, "票据绑定了 tool_id，不能跨工具使用"


def httpx_client(client) -> object:
    """复用同一个 transport 起一个**不带 Cookie** 的客户端，模拟匿名浏览器下载。"""
    import httpx

    return httpx.AsyncClient(transport=client._transport, base_url="http://testserver")


async def test_expired_ticket_rejected(client, seeded) -> None:
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    slug, tool_id, _content = await publish_tool(
        client, owner, approver, name="过期票据工具"
    )
    async with SessionLocal() as session:
        version = (
            await session.execute(
                select(ToolVersion).where(ToolVersion.tool_id == tool_id)
            )
        ).scalars().first()
    assert version is not None
    token, _ = create_download_ticket(
        tool_id=tool_id, version_id=version.id, user_id=seeded.users["outsider"],
        ttl_seconds=-1,
    )
    bare = httpx_client(client)
    async with bare:
        response = await bare.get(
            f"/api/v1/tools/{slug}/download?version_id={version.id}&ticket={token}"
        )
    assert response.status_code == 404, "过期票据必须失效"


# ===========================================================================
# 18. Range 请求 → 206
# ===========================================================================
async def test_range_request_returns_206(client, seeded) -> None:
    """Range 必须返回 206 且字节区间正确。

    这里刻意用 > 8 KiB 的文件：小文件整段 Range 会返回全部内容，
    断言 `len == 1024` 就失去意义了。
    """
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    tool = await create_tool(client, owner, name="Range 工具")
    import os as _os

    # 用随机数据：全零/循环数据会被 zip 压到几百字节，整段 Range 就没有意义了
    content = zip_bytes({"big.bin": _os.urandom(16384)})
    assert len(content) > 8192
    up = await upload_version(
        client, owner, tool["id"], version="1.0.0", file_bytes=content, file_name="big.zip"
    )
    assert up.status_code == 201, up.text
    await submit(client, owner, tool["id"])
    await approve(client, approver, tool["id"])
    slug = (
        await client.get(f"/api/v1/me/tools/{tool['id']}", headers=auth(owner))
    ).json()["slug"]

    full = await client.get(f"/api/v1/tools/{slug}/download", headers=auth(owner))
    assert full.status_code == 200

    partial = await client.get(
        f"/api/v1/tools/{slug}/download",
        headers={**auth(owner), "Range": "bytes=0-1023"},
    )
    assert partial.status_code == 206, partial.text
    assert len(partial.content) == 1024
    assert partial.content == content[:1024]
    assert "content-range" in {k.lower() for k in partial.headers}

    tail = await client.get(
        f"/api/v1/tools/{slug}/download",
        headers={**auth(owner), "Range": "bytes=-512"},
    )
    assert tail.status_code == 206
    assert tail.content == content[-512:]


# ===========================================================================
# 19. 并发审批 → 一个成功一个 409
# ===========================================================================
async def test_concurrent_approve_one_wins(client, seeded) -> None:
    """两个管理员同时批准同一工具：一个成功，另一个 409 `ALREADY_PROCESSED`。

    并发防护靠 CAS（`UPDATE ... WHERE status=? AND version_seq=?` 的影响行数），
    不是「先查后改」。
    """
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    admin_token = await login(client, "admin")

    tool = await create_tool(client, owner, name="并发审批工具")
    await upload_version(
        client, owner, tool["id"], version="1.0.0",
        file_bytes=zip_bytes({"a.txt": b"x"}), file_name="a.zip",
    )
    await submit(client, owner, tool["id"])

    results = await asyncio.gather(
        approve(client, approver, tool["id"]),
        approve(client, admin_token, tool["id"]),
        return_exceptions=True,
    )
    codes = sorted(r.status_code for r in results if not isinstance(r, Exception))
    assert codes == [200, 409], f"期望恰好一个 200 一个 409，实际 {codes}"
    loser = next(r for r in results if not isinstance(r, Exception) and r.status_code == 409)
    assert loser.json()["code"] == "ALREADY_PROCESSED"

    # 乐观锁：传一个过期的 expected_version_seq 也要 409
    tool2 = await create_tool(client, owner, name="乐观锁工具")
    await upload_version(
        client, owner, tool2["id"], version="1.0.0",
        file_bytes=zip_bytes({"a.txt": b"x"}), file_name="a.zip",
    )
    await submit(client, owner, tool2["id"])
    stale = await approve(client, approver, tool2["id"], expected_version_seq=999)
    assert stale.status_code == 409
    assert stale.json()["code"] == "ALREADY_PROCESSED"


# ===========================================================================
# 14/15. 自动放行与免审白名单
# ===========================================================================
async def test_auto_approve_all_publishes_immediately(client, seeded) -> None:
    from app.repositories import system_settings as settings_repo

    admin_token = await login(client, "admin")
    owner = await login(client, "outsider")

    async with SessionLocal() as session:
        row = await settings_repo.get_row(session, "approval.mode")
        original = row.value
    try:
        async with SessionLocal() as session:
            row = await settings_repo.get_row(session, "approval.mode")
            row.value = "auto_approve_all"
            await session.commit()
        tool = await create_tool(client, owner, name="自动放行工具")
        await upload_version(
            client, owner, tool["id"], version="1.0.0",
            file_bytes=zip_bytes({"a.txt": b"x"}), file_name="a.zip",
        )
        submitted = await submit(client, owner, tool["id"])
        assert submitted.status_code == 200, submitted.text
        body = submitted.json()
        assert body["auto_approved"] is True
        assert body["auto_approved_rule"] == "auto_approve_all"
        assert body["status"] == "approved"

        # 门户可见
        slug = (
            await client.get(f"/api/v1/me/tools/{tool['id']}", headers=auth(owner))
        ).json()["slug"]
        detail = await client.get(f"/api/v1/tools/{slug}", headers=auth(owner))
        assert detail.status_code == 200

        # 审批流水：actor 记为系统，is_automatic=true
        history = await client.get(
            "/api/v1/admin/approvals/history",
            params={"tool_id": tool["id"]},
            headers=auth(admin_token),
        )
        records = history.json()["items"]
        automatic = [r for r in records if r["is_automatic"]]
        assert automatic, "自动放行必须有 is_automatic=true 的记录"
        assert automatic[0]["actor_id"] is None
        assert "系统" in automatic[0]["actor_label"]
        assert automatic[0]["auto_rule"] == "auto_approve_all"
    finally:
        async with SessionLocal() as session:
            row = await settings_repo.get_row(session, "approval.mode")
            row.value = original
            await session.commit()


async def test_whitelist_publishes_directly_in_require_mode(client, seeded) -> None:
    """require 模式下，白名单用户提交直接发布（FR-APPR-03）。"""
    admin_token = await login(client, "admin")
    owner = await login(client, "outsider")

    # 关键：清理必须包住**整个**流程，否则一旦断言失败，outsider 会被永久
    # 留在白名单里，后续所有用 outsider 的用例都会莫名其妙自动放行。
    try:
        added = await client.post(
            "/api/v1/admin/approval-whitelist",
            json={"user_id": seeded.users["outsider"], "reason": "核心工具组"},
            headers=auth(admin_token),
        )
        assert added.status_code == 201, added.text
        assert added.json()["username"] == "outsider"

        listing = await client.get(
            "/api/v1/admin/approval-whitelist", headers=auth(admin_token)
        )
        assert listing.status_code == 200
        assert any(e["user_id"] == seeded.users["outsider"] for e in listing.json())

        tool = await create_tool(client, owner, name="白名单工具")
        await upload_version(
            client, owner, tool["id"], version="1.0.0",
            file_bytes=zip_bytes({"a.txt": b"x"}), file_name="a.zip",
        )
        submitted = await submit(client, owner, tool["id"])
        assert submitted.status_code == 200, submitted.text
        assert submitted.json()["auto_approved"] is True
        assert submitted.json()["auto_approved_rule"] == "whitelist"
    finally:
        removed = await client.delete(
            f"/api/v1/admin/approval-whitelist/{seeded.users['outsider']}",
            headers=auth(admin_token),
        )
        assert removed.status_code == 200


async def test_switching_to_auto_approve_warns_pending(client, seeded) -> None:
    """FR-APPR-02：切到 auto_approve_all 时已 pending 的条目不被自动放行，用 warnings 告知。"""
    admin_token = await login(client, "admin")
    owner = await login(client, "outsider")
    tool = await create_tool(client, owner, name="待审告知工具")
    await upload_version(
        client, owner, tool["id"], version="1.0.0",
        file_bytes=zip_bytes({"a.txt": b"x"}), file_name="a.zip",
    )
    await submit(client, owner, tool["id"])

    try:
        response = await client.put(
            "/api/v1/admin/settings",
            json={"items": [{"key": "approval.mode", "value": "auto_approve_all"}]},
            headers=auth(admin_token),
        )
        assert response.status_code == 200, response.text
        warnings = response.json()["warnings"]
        assert any(w["code"] == "PENDING_ITEMS_NOT_AFFECTED" for w in warnings)
        assert warnings[0]["pending_count"] >= 1
    finally:
        await client.put(
            "/api/v1/admin/settings",
            json={"items": [{"key": "approval.mode", "value": "require"}]},
            headers=auth(admin_token),
        )


# ===========================================================================
# 20. 下架 / 重新上架
# ===========================================================================
async def test_offline_and_relist(client, seeded) -> None:
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    slug, tool_id, _content = await publish_tool(client, owner, approver, name="下架工具")

    offline = await client.post(
        f"/api/v1/admin/approvals/{tool_id}/offline",
        json={"reason": "依赖的高危组件需要先修复"},
        headers=auth(approver),
    )
    assert offline.status_code == 200, offline.text
    assert offline.json()["status"] == "offline"

    # 门户立即不可见
    assert (
        await client.get(f"/api/v1/tools/{slug}", headers=auth(owner))
    ).status_code == 404
    # owner 能看到理由
    mine = await client.get(f"/api/v1/me/tools/{tool_id}", headers=auth(owner))
    assert mine.json()["status"] == "offline"
    assert "高危组件" in mine.json()["offline_reason"]

    # 审批历史有记录
    admin_token = await login(client, "admin")
    history = await client.get(
        "/api/v1/admin/approvals/history",
        params={"tool_id": tool_id, "action": "offline"},
        headers=auth(admin_token),
    )
    assert history.json()["total"] >= 1

    # 重新上架
    relist = await client.post(
        f"/api/v1/admin/approvals/{tool_id}/relist",
        json={"reason": "已修复"},
        headers=auth(approver),
    )
    assert relist.status_code == 200
    assert relist.json()["status"] == "approved"
    assert (
        await client.get(f"/api/v1/tools/{slug}", headers=auth(owner))
    ).status_code == 200


# ===========================================================================
# 21. 计数器：批量落库 + 去重 + 不逐请求写
# ===========================================================================
async def test_counters_batch_flush_and_dedup(client, seeded) -> None:
    """并发下载后计数正确落库；浏览去重生效；flush 不丢。"""
    from app.services.counter_service import CounterService, set_counter_service

    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    slug, tool_id, _content = await publish_tool(client, owner, approver, name="计数工具")

    async with SessionLocal() as session:
        before = (await session.get(Tool, tool_id)).download_count
        views_before = (await session.get(Tool, tool_id)).view_count

    fresh = CounterService()
    set_counter_service(fresh)
    try:
        # 并发 20 次下载
        responses = await asyncio.gather(
            *[
                client.get(f"/api/v1/tools/{slug}/download", headers=auth(owner))
                for _ in range(20)
            ]
        )
        assert all(r.status_code == 200 for r in responses)
        assert fresh.pending_snapshot()["downloads"] == 20

        # 明细队列里也应有 20 条
        assert fresh.pending_snapshot()["logs"] == 20

        # 浏览去重：同一用户对同一工具多次访问只计一次
        for _ in range(3):
            await client.get(f"/api/v1/tools/{slug}", headers=auth(owner))
        assert fresh.pending_snapshot()["views"] == 1, "同一用户 1 小时内只计一次浏览"

        # 另一个用户是独立计数
        await client.get(f"/api/v1/tools/{slug}", headers=auth(approver))
        assert fresh.pending_snapshot()["views"] == 2

        stats = await fresh.flush()
        assert stats["logs"] == 20
        assert stats["counter_tools"] >= 1

        async with SessionLocal() as session:
            after = await session.get(Tool, tool_id)
            assert after.download_count == before + 20, "下载计数应当恰好 +20"
            assert after.view_count == views_before + 2, "浏览计数应当恰好 +2（去重后）"
            from app.models.stats import DownloadLog

            logs = (
                await session.execute(
                    select(DownloadLog).where(DownloadLog.tool_id == tool_id)
                )
            ).scalars().all()
            assert len(logs) == 20

        # flush 之后内存清零
        assert fresh.pending_snapshot()["downloads"] == 0
    finally:
        set_counter_service(None)


async def test_counter_flush_on_shutdown_survives(client, seeded) -> None:
    """优雅关闭（SIGTERM → lifespan 退出）必须 flush，不能丢计数。"""
    from app.services.counter_service import CounterService, set_counter_service

    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    _slug, tool_id, _ = await publish_tool(client, owner, approver, name="关闭落库工具")

    async with SessionLocal() as session:
        before = (await session.get(Tool, tool_id)).download_count

    service = CounterService(flush_interval_seconds=3600)  # 定时器不会触发
    set_counter_service(service)
    try:
        await service.start()
        service.record_download(
            tool_id=tool_id, version_id=None, user_id=seeded.users["outsider"],
            ip="127.0.0.1", user_agent="pytest",
        )
        service.record_view(tool_id, viewer_key="u:1")
        # 模拟 SIGTERM：stop() 内部会做最后一次 flush
        await service.stop()

        async with SessionLocal() as session:
            after = await session.get(Tool, tool_id)
            assert after.download_count == before + 1, "关闭时必须把残留计数落库"
        assert service.pending_snapshot()["downloads"] == 0
    finally:
        set_counter_service(None)


async def test_counter_failure_does_not_break_requests(client, seeded, monkeypatch) -> None:
    """落库失败不得影响用户请求（下载仍然 200）。"""
    from app.services import counter_service as module
    from app.services.counter_service import CounterService, set_counter_service

    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    slug, _tool_id, _ = await publish_tool(client, owner, approver, name="落库失败工具")

    service = CounterService()
    set_counter_service(service)
    try:

        async def boom(*args, **kwargs):
            raise RuntimeError("模拟数据库写失败")

        monkeypatch.setattr(module.downloads_repo, "apply_counters", boom)
        await client.get(f"/api/v1/tools/{slug}/download", headers=auth(owner))
        result = await service.flush()
        # flush 内部吞掉异常并返回全 0
        assert result == {
            "counter_tools": 0,
            "daily": 0,
            "logs": 0,
            "tokens": 0,
        }
    finally:
        set_counter_service(None)


# ===========================================================================
# 状态机：非法迁移一律 409
# ===========================================================================
async def test_illegal_state_transitions_return_409(client, seeded) -> None:
    owner = await login(client, "outsider")
    approver = await login(client, "approver")

    tool = await create_tool(client, owner, name="状态机工具")
    tool_id = tool["id"]

    # draft 没有版本 → 提交失败
    no_version = await submit(client, owner, tool_id)
    assert no_version.status_code == 409
    assert no_version.json()["code"] == "STATE_CONFLICT"

    await upload_version(
        client, owner, tool_id, version="1.0.0",
        file_bytes=zip_bytes({"a.txt": b"x"}), file_name="a.zip",
    )
    await submit(client, owner, tool_id)

    # pending 时不能撤回以外的动作：再提交一次 → 409
    again = await submit(client, owner, tool_id)
    assert again.status_code == 409
    # pending 时不能编辑
    edit = await client.patch(
        f"/api/v1/me/tools/{tool_id}", json={"summary": "改一下"}, headers=auth(owner)
    )
    assert edit.status_code == 409
    assert edit.json()["code"] == "TOOL_NOT_EDITABLE"
    # pending 时不能上传新版本
    blocked_upload = await upload_version(
        client, owner, tool_id, version="1.1.0",
        file_bytes=zip_bytes({"b.txt": b"y"}), file_name="b.zip",
    )
    assert blocked_upload.status_code == 409

    # 撤回 → draft，可以再提交
    withdrawn = await client.post(f"/api/v1/me/tools/{tool_id}/withdraw", headers=auth(owner))
    assert withdrawn.status_code == 200
    assert withdrawn.json()["status"] == "draft"

    # draft 状态下批准 → 409（没在待审）
    not_pending = await approve(client, approver, tool_id)
    assert not_pending.status_code == 409
    # draft 状态下下架 → 409
    cannot_offline = await client.post(
        f"/api/v1/admin/approvals/{tool_id}/offline",
        json={"reason": "理由足够长了吧"},
        headers=auth(approver),
    )
    assert cannot_offline.status_code == 409


async def test_version_uniqueness_and_editable_rules(client, seeded) -> None:
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    tool = await create_tool(client, owner, name="版本规则工具")
    payload = zip_bytes({"a.txt": b"x"})
    await upload_version(client, owner, tool["id"], version="1.0.0", file_bytes=payload, file_name="a.zip")

    duplicate = await upload_version(
        client, owner, tool["id"], version="1.0.0", file_bytes=payload, file_name="a.zip"
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "VERSION_EXISTS"
    assert duplicate.json()["details"]["version"] == "1.0.0"

    # 未过审版本可改 changelog
    patched = await client.patch(
        f"/api/v1/me/tools/{tool['id']}/versions/1.0.0",
        json={"changelog_md": "补充说明"},
        headers=auth(owner),
    )
    assert patched.status_code == 200
    assert patched.json()["changelog_md"] == "补充说明"

    # 未过审版本可删
    deleted = await client.delete(
        f"/api/v1/me/tools/{tool['id']}/versions/1.0.0", headers=auth(owner)
    )
    assert deleted.status_code == 200

    # 已发布版本不可改、不可删
    await upload_version(client, owner, tool["id"], version="2.0.0", file_bytes=payload, file_name="a.zip")
    await submit(client, owner, tool["id"])
    await approve(client, approver, tool["id"])

    locked_patch = await client.patch(
        f"/api/v1/me/tools/{tool['id']}/versions/2.0.0",
        json={"changelog_md": "偷偷改"},
        headers=auth(owner),
    )
    assert locked_patch.status_code == 409

    locked_delete = await client.delete(
        f"/api/v1/me/tools/{tool['id']}/versions/2.0.0", headers=auth(owner)
    )
    assert locked_delete.status_code == 409
    assert "下架" in locked_delete.json()["message"]


async def test_duplicate_version_number_is_not_case_sensitive_confusion(client, seeded) -> None:
    """版本号按**精确字符串**唯一（不改大小写、不做语义比较）。"""
    owner = await login(client, "outsider")
    tool = await create_tool(client, owner, name="版本号大小写")
    payload = zip_bytes({"a.txt": b"x"})
    first = await upload_version(
        client, owner, tool["id"], version="1.0.0-beta", file_bytes=payload, file_name="a.zip"
    )
    assert first.status_code == 201
    second = await upload_version(
        client, owner, tool["id"], version="1.0.0-BETA", file_bytes=payload, file_name="a.zip"
    )
    assert second.status_code == 201, "大小写不同视为不同版本号（FR-VER-02 不做格式强校验）"


async def test_soft_delete_hides_tool(client, seeded) -> None:
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    slug, tool_id, _ = await publish_tool(client, owner, approver, name="软删除工具")

    deleted = await client.delete(f"/api/v1/me/tools/{tool_id}", headers=auth(owner))
    assert deleted.status_code == 200

    assert (await client.get(f"/api/v1/tools/{slug}", headers=auth(owner))).status_code == 404
    listing = await client.get("/api/v1/me/tools", headers=auth(owner))
    assert tool_id not in [item["id"] for item in listing.json()["items"]]

    # 数据库里是软删除而不是物理删除
    async with SessionLocal() as session:
        row = await session.get(Tool, tool_id)
        assert row is not None and row.deleted_at is not None


async def test_admin_settings_validation_is_atomic(client, seeded) -> None:
    """任何一项非法则整体回滚（docs/03 §3.13）。"""
    admin_token = await login(client, "admin")
    ok = await client.put(
        "/api/v1/admin/settings",
        json={"items": [{"key": "version.history_limit", "value": 15}]},
        headers=auth(admin_token),
    )
    assert ok.status_code == 200, ok.text
    items = {item["key"]: item for item in ok.json()["items"]}
    assert items["version.history_limit"]["value"] == 15
    assert items["version.history_limit"]["min"] == 1
    assert items["version.history_limit"]["max"] == 50
    assert items["approval.mode"]["options"] == ["require", "auto_approve_all"]

    bad = await client.put(
        "/api/v1/admin/settings",
        json={
            "items": [
                {"key": "version.history_limit", "value": 3},
                {"key": "upload.max_screenshots", "value": 999},
            ]
        },
        headers=auth(admin_token),
    )
    assert bad.status_code == 400
    assert bad.json()["code"] == "SETTING_INVALID"
    assert bad.json()["details"]["key"] == "upload.max_screenshots"

    # 整体回滚：第一项也不能生效
    after = await client.get("/api/v1/admin/settings", headers=auth(admin_token))
    assert {i["key"]: i["value"] for i in after.json()["items"]}["version.history_limit"] == 15

    # 恢复
    await client.put(
        "/api/v1/admin/settings",
        json={"items": [{"key": "version.history_limit", "value": 10}]},
        headers=auth(admin_token),
    )


async def test_image_upload_and_serving(client, seeded) -> None:
    """图片上传 + 缩略图 + 可见性校验。"""
    from PIL import Image

    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    viewer = await login(client, "viewer")
    tool = await create_tool(client, owner, name="图片工具", visibility="public")

    buffer = io.BytesIO()
    Image.new("RGB", (1200, 630), (10, 120, 200)).save(buffer, format="PNG")
    png = buffer.getvalue()

    uploaded = await client.post(
        f"/api/v1/me/tools/{tool['id']}/images",
        data={"kind": "cover", "alt_text": "封面"},
        files={"file": ("cover.png", png, "image/png")},
        headers=auth(owner),
    )
    assert uploaded.status_code == 201, uploaded.text
    image = uploaded.json()
    assert image["kind"] == "cover"
    assert image["width"] == 1200
    # M5：图片 URL 带能力签名（契约 §14.3），校验前缀而非精确结尾
    assert "?variant=thumb" in image["thumb_url"]
    assert "sig=" in image["thumb_url"]

    # 草稿状态：只有 owner 能看自己的图（编辑器要能预览）；
    # 其他人 404 —— 未发布工具的任何资源都不该外泄。
    owner_full = await client.get(image["url"], headers=auth(owner))
    assert owner_full.status_code == 200
    assert owner_full.content == png
    assert (await client.get(image["url"], headers=auth(viewer))).status_code == 404

    thumb = await client.get(image["thumb_url"], headers=auth(owner))
    assert thumb.status_code == 200
    with Image.open(io.BytesIO(thumb.content)) as thumb_image:
        assert thumb_image.width == 480, "缩略图宽度必须是 480px（FR-FILE-10）"

    # 发布之后，具备门户可见性的用户也能取图
    await upload_version(
        client, owner, tool["id"], version="1.0.0",
        file_bytes=zip_bytes({"a.txt": b"x"}), file_name="a.zip",
    )
    await submit(client, owner, tool["id"])
    await approve(client, approver, tool["id"])
    assert (await client.get(image["url"], headers=auth(viewer))).status_code == 200

    # 伪造图片（改名 exe）被拒
    forged = await client.post(
        f"/api/v1/me/tools/{tool['id']}/images",
        data={"kind": "screenshot"},
        files={"file": ("evil.png", b"MZ\x90\x00\x03" + b"\x00" * 100, "image/png")},
        headers=auth(owner),
    )
    assert forged.status_code == 415
    assert forged.json()["code"] == "UNSUPPORTED_MEDIA_TYPE"

    # 详情里带上 images
    slug = (
        await client.get(f"/api/v1/me/tools/{tool['id']}", headers=auth(owner))
    ).json()["slug"]
    detail = await client.get(f"/api/v1/tools/{slug}", headers=auth(viewer))
    assert detail.json()["images"][0]["id"] == image["id"]

    # 切到 private 后，别人取图 404
    await client.patch(
        f"/api/v1/me/tools/{tool['id']}",
        json={"visibility": "private"},
        headers=auth(owner),
    )
    assert (await client.get(image["url"], headers=auth(viewer))).status_code == 404
    assert (await client.get(image["url"], headers=auth(owner))).status_code == 200

    # 删除图片
    removed = await client.delete(image["url"].replace("/api/v1/images/", "/api/v1/me/tools/") if False else f"/api/v1/me/tools/{tool['id']}/images/{image['id']}", headers=auth(owner))
    assert removed.status_code == 200
    assert (await client.get(image["url"], headers=auth(owner))).status_code == 404


async def test_me_profile_and_stats(client, seeded) -> None:
    owner = await login(client, "outsider")
    profile = await client.get("/api/v1/me/profile", headers=auth(owner))
    assert profile.status_code == 200
    assert profile.json()["username"] == "outsider"
    assert "usage" in profile.json()

    patched = await client.patch(
        "/api/v1/me/profile",
        json={"display_name": "外部协作者", "email": "outsider2@example.com"},
        headers=auth(owner),
    )
    assert patched.status_code == 200
    assert patched.json()["display_name"] == "外部协作者"

    stats = await client.get("/api/v1/me/stats", headers=auth(owner))
    assert stats.status_code == 200
    assert stats.json()["tool_count"] >= 1
    assert stats.json()["quota_bytes"] > 0


async def test_me_downloads_history(client, seeded) -> None:
    from app.services.counter_service import CounterService, set_counter_service

    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    slug, _tool_id, _ = await publish_tool(client, owner, approver, name="下载历史工具")

    service = CounterService()
    set_counter_service(service)
    try:
        await client.get(f"/api/v1/tools/{slug}/download", headers=auth(owner))
        await service.flush()
    finally:
        set_counter_service(None)

    history = await client.get("/api/v1/me/downloads", headers=auth(owner))
    assert history.status_code == 200, history.text
    assert history.json()["total"] >= 1
    assert history.json()["items"][0]["tool_slug"] == slug


async def test_skill_parse_error_saved_but_cannot_submit(client, seeded) -> None:
    """FR-TOOL-06：解析失败允许存草稿，但**不允许提交审批**。"""
    owner = await login(client, "outsider")
    tool = await create_tool(client, owner, name="坏 frontmatter", tool_type="skill")
    # frontmatter 是非法 YAML → 软失败
    payload = zip_bytes({"SKILL.md": b"---\nname: [unclosed\n---\n# body"})
    up = await upload_version(
        client, owner, tool["id"], version="1.0.0", file_bytes=payload, file_name="bad.zip"
    )
    assert up.status_code == 201, up.text
    assert up.json()["skill"]["parse_error"] is not None
    assert up.json()["skill"]["manifest"] is None

    blocked = await submit(client, owner, tool["id"])
    assert blocked.status_code == 422
    assert blocked.json()["code"] == "SKILL_PARSE_FAILED"
    assert blocked.json()["details"]["version"] == "1.0.0"


async def test_download_records_api_token_attribution(client, seeded) -> None:
    """通过 API Token 下载时，download_logs 应记录 via_api_token_id。"""
    from app.core.security import generate_api_token, hash_api_token
    from app.models.user import ApiToken

    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    slug, tool_id, _ = await publish_tool(client, owner, approver, name="Token 归属工具")

    token = generate_api_token()
    async with SessionLocal() as session:
        row = ApiToken(
            name="测试令牌",
            token_prefix=token[:8],
            token_hash=hash_api_token(token),
            scopes=["tools:read"],
            created_by_id=seeded.users["outsider"],
            created_at=utcnow(),
        )
        session.add(row)
        await session.commit()
        token_id = row.id

    from app.services.counter_service import CounterService, set_counter_service

    service = CounterService()
    set_counter_service(service)
    try:
        response = await client.get(
            f"/api/v1/tools/{slug}/download", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200, response.text
        assert service.pending_snapshot()["logs"] == 1
        await service.flush()
    finally:
        set_counter_service(None)

    from app.models.stats import DownloadLog

    async with SessionLocal() as session:
        logs = (
            await session.execute(
                select(DownloadLog).where(
                    DownloadLog.tool_id == tool_id,
                    DownloadLog.via_api_token_id == token_id,
                )
            )
        ).scalars().all()
        assert len(logs) == 1


async def test_approval_history_records_actor_label_snapshot(client, seeded) -> None:
    """`actor_label` 是**快照**：用户改名后历史仍显示当时的名字。"""
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    tool = await create_tool(client, owner, name="快照测试")
    await upload_version(
        client, owner, tool["id"], version="1.0.0",
        file_bytes=zip_bytes({"a.txt": b"x"}), file_name="a.zip",
    )
    await submit(client, owner, tool["id"])
    approved = await approve(client, approver, tool["id"])
    assert approved.status_code == 200

    admin_token = await login(client, "admin")
    history = await client.get(
        "/api/v1/admin/approvals/history",
        params={"tool_id": tool["id"], "action": "approve"},
        headers=auth(admin_token),
    )
    record = history.json()["items"][0]
    assert record["actor_label"] == "approver 展示名"

    # 改掉审批人的显示名
    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.username == "approver"))
        ).scalar_one()
        original = user.display_name
        user.display_name = "改名后的审批人"
        await session.commit()
    try:
        again = await client.get(
            "/api/v1/admin/approvals/history",
            params={"tool_id": tool["id"], "action": "approve"},
            headers=auth(admin_token),
        )
        assert again.json()["items"][0]["actor_label"] == "approver 展示名", (
            "actor_label 必须是快照，不能跟着用户改名"
        )
    finally:
        async with SessionLocal() as session:
            user = (
                await session.execute(select(User).where(User.username == "approver"))
            ).scalar_one()
            user.display_name = original
            await session.commit()


async def test_batch_approve_partial_success(client, seeded) -> None:
    owner = await login(client, "outsider")
    approver = await login(client, "approver")

    ids = []
    for index in range(3):
        tool = await create_tool(client, owner, name=f"批量批准 {index}")
        await upload_version(
            client, owner, tool["id"], version="1.0.0",
            file_bytes=zip_bytes({"a.txt": b"x"}), file_name="a.zip",
        )
        await submit(client, owner, tool["id"])
        ids.append(tool["id"])

    # 混入一个不存在与一个未提交的工具
    draft = await create_tool(client, owner, name="未提交工具")
    ids.append(draft["id"])
    ids.append(999999)

    response = await client.post(
        "/api/v1/admin/approvals/batch-approve",
        json={"tool_ids": ids, "note": "批量放行"},
        headers=auth(approver),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["succeeded"] == 3
    assert body["failed"] == 2
    failures = {r["tool_id"]: r["error_code"] for r in body["results"] if not r["ok"]}
    assert failures[999999] == "NOT_FOUND"
    assert failures[draft["id"]] == "STATE_CONFLICT"


async def test_revoked_role_blocks_admin_access(client, seeded) -> None:
    """viewer 访问审批接口 → 403（角色不足）。"""
    viewer = await login(client, "viewer")
    response = await client.get("/api/v1/admin/approvals", headers=auth(viewer))
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"

    # approver 不能改系统设置（需要 superadmin）
    approver = await login(client, "approver")
    denied = await client.get("/api/v1/admin/settings", headers=auth(approver))
    assert denied.status_code == 403


async def test_can_manage_versions_and_submit_flags(client, seeded) -> None:
    """详情里的 permissions 布尔值要与服务端真实判定一致。"""
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    slug, _tool_id, _ = await publish_tool(client, owner, approver, name="权限布尔工具")

    owner_detail = await client.get(f"/api/v1/tools/{slug}", headers=auth(owner))
    perms = owner_detail.json()["permissions"]
    assert perms["can_edit"] is True
    assert perms["can_download"] is True
    assert perms["can_manage_versions"] is True
    assert perms["can_view_acl"] is True
    assert perms["can_delete"] is True

    viewer = await login(client, "viewer")
    viewer_detail = await client.get(f"/api/v1/tools/{slug}", headers=auth(viewer))
    vperms = viewer_detail.json()["permissions"]
    assert vperms["can_edit"] is False
    assert vperms["can_download"] is False
    assert vperms["can_manage_versions"] is False
    assert vperms["can_view_acl"] is False

    approver_detail = await client.get(f"/api/v1/tools/{slug}", headers=auth(approver))
    aperms = approver_detail.json()["permissions"]
    assert aperms["can_approve"] is True
    assert aperms["can_edit"] is False, "approver 不是 owner，不能通过门户编辑"


async def test_non_owner_cannot_touch_others_tool(client, seeded) -> None:
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    _slug, tool_id, _ = await publish_tool(client, owner, approver, name="他人工具")

    other = await login(client, "viewer") if False else await login(client, "newbie") if False else None
    del other
    # 用另一个普通用户（lockme）尝试越权
    other_token = await login(client, "lockme")
    for method, path, kwargs in [
        ("get", f"/api/v1/me/tools/{tool_id}", {}),
        ("patch", f"/api/v1/me/tools/{tool_id}", {"json": {"summary": "越权改"}}),
        ("delete", f"/api/v1/me/tools/{tool_id}", {}),
        ("post", f"/api/v1/me/tools/{tool_id}/submit", {}),
        ("put", f"/api/v1/me/tools/{tool_id}/acl", {"json": {"visibility": "public"}}),
    ]:
        response = await getattr(client, method)(path, headers=auth(other_token), **kwargs)
        assert response.status_code == 404, f"{method} {path} 应当 404，实际 {response.status_code}"
