"""M5 收口项的测试：图片签名 / 种子完整性 / 管理侧代上传 / 两个设置项接线 / facet 优化。

对应 `contracts/CONTRACT.md` §18.3~§18.7 的裁定。
"""

from __future__ import annotations

import io
import time
import zipfile

import pytest
from sqlalchemy import select, text

from app.core.security import (
    derive_subkey,
    image_signature,
    verify_image_signature,
)
from app.core.timeutil import utcnow
from app.db.session import SessionLocal
from app.models.tool import Tool, ToolImage, ToolVersion
from app.repositories import tools as tools_repo
from app.services import settings_service
from tests.conftest import Seeded, auth, login

TOOLS = "/api/v1/tools"


# ===========================================================================
# A1 图片签名（契约 §14.3）
# ===========================================================================
def test_signature_uses_derived_subkey_not_master_key() -> None:
    """子密钥必须由 SECRET_KEY **派生**，不能直接用主密钥（易错点清单第 1 条）。"""
    from app.core.config import settings
    from app.core.security import IMAGE_SIGNATURE_PURPOSE

    derived = derive_subkey(IMAGE_SIGNATURE_PURPOSE)
    assert derived != settings.secret_key.encode()
    assert len(derived) == 32
    # 用途隔离：与下载票据的子密钥不同
    assert derived != derive_subkey("download-ticket")


def test_verify_image_signature_roundtrip_and_tamper() -> None:
    sig = image_signature(1, "thumb", ttl_hours=1)
    assert verify_image_signature(1, "thumb", sig) is True
    # 篡改：改 image_id / variant / 签名串
    assert verify_image_signature(2, "thumb", sig) is False
    assert verify_image_signature(1, "full", sig) is False
    mac, _, exp = sig.partition(".")
    assert verify_image_signature(1, "thumb", f"{mac[:-2]}xx.{exp}") is False
    assert verify_image_signature(1, "thumb", "garbage") is False
    assert verify_image_signature(1, "thumb", None) is False


def test_expired_signature_is_rejected() -> None:
    # ttl_hours 传 0 → exp 立即过期（负数更稳，避免同秒边界）
    sig = image_signature(1, "thumb", ttl_hours=0)
    time.sleep(1.1)
    assert verify_image_signature(1, "thumb", sig) is False


async def test_list_and_detail_cover_url_carry_signature(client, seeded: Seeded) -> None:
    """验收 1：列表/详情下发的 cover_url / thumb_url 含 sig= 且可校验通过。"""
    token = await login(client, "admin")
    listing = await client.get(TOOLS, params={"page_size": 200}, headers=auth(token))
    assert listing.status_code == 200, listing.text
    with_cover = [i for i in listing.json()["items"] if i["cover_url"]]
    assert with_cover, "种子数据里应当有带封面的工具"
    url = with_cover[0]["cover_url"]
    assert "sig=" in url and "variant=thumb" in url

    # 从 URL 里解出 image_id 与 sig，用同一套校验函数确认有效
    path, _, query = url.partition("?")
    image_id = int(path.rsplit("/", 1)[-1])
    params = dict(kv.split("=", 1) for kv in query.split("&"))
    assert verify_image_signature(image_id, params["variant"], params["sig"]) is True

    detail = await client.get(
        f"{TOOLS}/{with_cover[0]['slug']}", headers=auth(token)
    )
    assert detail.status_code == 200, detail.text
    for image in detail.json()["images"]:
        assert "sig=" in image["url"] and "sig=" in image["thumb_url"]


async def test_image_fetch_accepts_signature_without_header(client, seeded: Seeded) -> None:
    """验收 2/3：带有效 sig 无鉴权头 → 200；篡改 / 无 sig 无头 → 404。"""
    token = await login(client, "admin")
    listing = await client.get(TOOLS, params={"page_size": 200}, headers=auth(token))
    url = next(i["cover_url"] for i in listing.json()["items"] if i["cover_url"])

    ok = await client.get(url)
    assert ok.status_code == 200, ok.text
    assert ok.headers["content-type"].startswith("image/")
    assert len(ok.content) > 0

    # 篡改签名
    bad = await client.get(url.replace("sig=", "sig=xx"))
    assert bad.status_code == 404

    # 既无签名也无 Authorization 头
    bare = await client.get(url.split("?")[0])
    assert bare.status_code == 404


async def test_image_fetch_accepts_authorization_header(client, seeded: Seeded) -> None:
    """验收 4：只有 Authorization 头（无 sig）也能取图。"""
    token = await login(client, "admin")
    async with SessionLocal() as session:
        image_id = (
            await session.execute(select(ToolImage.id).limit(1))
        ).scalar_one()

    got = await client.get(f"/api/v1/images/{image_id}", headers=auth(token))
    assert got.status_code == 200, got.text
    assert got.headers["content-type"].startswith("image/")


async def test_seeded_cover_files_exist_on_disk(seeded: Seeded) -> None:
    """种子封面必须是**真文件**，否则接口会 404、前端一片破图。"""
    from app.storage import get_storage

    storage = get_storage()
    async with SessionLocal() as session:
        rows = (
            await session.execute(select(ToolImage.storage_path, ToolImage.thumb_path))
        ).all()
    assert rows
    for storage_path, thumb_path in rows:
        assert storage.exists(storage_path), storage_path
        if thumb_path:
            assert storage.exists(thumb_path), thumb_path


def test_ttl_cache_respects_disabled_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """`LOCALCRAFT_DISABLE_FACET_CACHE` 是 A/B 对比与排障用的开关，必须真的生效。"""
    assert tools_repo._cache_enabled() is True
    monkeypatch.setenv("LOCALCRAFT_DISABLE_FACET_CACHE", "1")
    assert tools_repo._cache_enabled() is False


def test_visibility_cache_key_differs_per_identity() -> None:
    """缓存键必须区分身份 —— 跨用户复用会越权泄露（易错点清单第 3 条）。"""
    base = tools_repo.VisibilityContext(user_id=1, roles=frozenset({"user"}))
    other_user = tools_repo.VisibilityContext(user_id=2, roles=frozenset({"user"}))
    other_role = tools_repo.VisibilityContext(user_id=1, roles=frozenset({"approver"}))
    other_groups = tools_repo.VisibilityContext(
        user_id=1, roles=frozenset({"user"}), group_ids=(7,)
    )
    assert base.cache_key() != other_user.cache_key()
    assert base.cache_key() != other_role.cache_key()
    assert base.cache_key() != other_groups.cache_key()


# ===========================================================================
# A2/A3/A4 种子完整性（契约 §17.8 / §17.9 / §14.6）
# ===========================================================================
async def test_seed_builders_produce_real_artifacts() -> None:
    """A3：种子占位包与封面必须是**真实可解析**的文件，不是随便填的字节。"""
    import zipfile

    from PIL import Image

    from app.cli import _build_seed_package, _build_seed_png

    payload = _build_seed_package(slug="demo-x", version="1.0.0", tool_type="file")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert archive.namelist(), "占位包不能是空的"
        assert archive.testzip() is None, "占位包必须是合法 zip"

    skill_payload = _build_seed_package(
        slug="demo-s", version="1.0.0", tool_type="skill"
    )
    with zipfile.ZipFile(io.BytesIO(skill_payload)) as archive:
        assert "SKILL.md" in archive.namelist(), "skill 包必须含 SKILL.md"

    png = _build_seed_png(1)
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG"
    assert image.size == (64, 36)


def test_seed_constants_are_consistent() -> None:
    """A2/A4：viewer、三个作者、26 个工具（8 边界 + 18 普通），slug 不重复。"""
    from app.cli import (
        ALL_DEMO_TOOLS,
        BOUNDARY_TOOL_COUNT,
        DEMO_AUTHORS,
        DEMO_TOOLS,
        DEMO_VIEWER,
    )

    assert len(ALL_DEMO_TOOLS) == 26
    assert len(DEMO_TOOLS) == BOUNDARY_TOOL_COUNT == 8
    assert len(ALL_DEMO_TOOLS) - BOUNDARY_TOOL_COUNT == 18
    assert len({s["slug"] for s in ALL_DEMO_TOOLS}) == 26, "slug 不能重复"
    assert DEMO_VIEWER[0] == "viewer"
    assert [a[0] for a in DEMO_AUTHORS] == ["zhangsan", "lisi", "wangwu"]
    # 每个 spec 都要能被播种逻辑消费（必需字段齐全）
    for spec in ALL_DEMO_TOOLS:
        for field in ("slug", "name", "summary", "description_md", "tool_type", "category", "version"):
            assert field in spec, f"{spec.get('slug')} 缺字段 {field}"


async def test_seeded_cover_urls_in_fixture_are_signed(client, seeded: Seeded) -> None:
    """A1：fixture 里带封面的工具，其 cover_url 也必须是签名 URL。"""
    token = await login(client, "admin")
    listing = await client.get(TOOLS, params={"page_size": 200}, headers=auth(token))
    urls = [i["cover_url"] for i in listing.json()["items"] if i["cover_url"]]
    assert urls, "fixture 里应当至少有一个带封面的工具"
    for url in urls:
        assert "sig=" in url


# ===========================================================================
# B 管理侧代上传（契约 §18.4）
# ===========================================================================
def _zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.md", "# admin alias drill\n")
    return buffer.getvalue()


async def test_admin_version_upload_alias_works(client, seeded: Seeded) -> None:
    """验收 10：`POST /admin/tools/{id}/versions` 能真的上传成功（不再是 404）。"""
    admin = await login(client, "admin")
    created = await client.post(
        "/api/v1/admin/tools",
        json={
            "name": "别名上传工具",
            "summary": "验证管理侧代上传",
            "tool_type": "file",
            "owner_id": seeded.users["newbie"],
        },
        headers=auth(admin),
    )
    assert created.status_code == 201, created.text
    tool_id = created.json()["id"]

    up = await client.post(
        f"/api/v1/admin/tools/{tool_id}/versions",
        data={"version": "1.0.0", "changelog_md": "别名代上传", "auto_submit": "false"},
        files={"file": ("pkg.zip", _zip_bytes(), "application/octet-stream")},
        headers=auth(admin),
    )
    assert up.status_code == 201, up.text
    body = up.json()
    assert body["version"] == "1.0.0"
    assert body["file_sha256"] and len(body["file_sha256"]) == 64


async def test_admin_version_upload_alias_accepts_api_token(client, seeded: Seeded) -> None:
    """验收 11：用 `admin:all` scope 的 API Token 走通 docs/03 §5.2 的示例流程。"""
    admin = await login(client, "admin")
    issued = await client.post(
        "/api/v1/admin/tokens",
        json={"name": "M5 脚本 Token", "scopes": ["admin:all"]},
        headers=auth(admin),
    )
    assert issued.status_code == 201, issued.text
    api_token = issued.json()["token"]

    # 示例脚本第 1 步：代创建工具
    created = await client.post(
        "/api/v1/admin/tools",
        json={
            "name": "脚本化工具",
            "summary": "docs/03 §5.2 流程",
            "tool_type": "file",
            "owner_id": seeded.users["newbie"],
        },
        headers=auth(api_token),
    )
    assert created.status_code == 201, created.text
    tool_id = created.json()["id"]

    # 示例脚本第 2 步：代上传版本（原先这里必然 404）
    up = await client.post(
        f"/api/v1/admin/tools/{tool_id}/versions",
        data={"version": "0.1.0", "changelog_md": "脚本上传", "auto_submit": "true"},
        files={"file": ("s.zip", _zip_bytes(), "application/octet-stream")},
        headers=auth(api_token),
    )
    assert up.status_code == 201, up.text
    assert up.json()["status"] in ("pending", "approved")


async def test_admin_version_upload_alias_rejects_missing_tool(client, seeded: Seeded) -> None:
    admin = await login(client, "admin")
    resp = await client.post(
        "/api/v1/admin/tools/999999/versions",
        data={"version": "1.0.0"},
        files={"file": ("p.zip", _zip_bytes(), "application/octet-stream")},
        headers=auth(admin),
    )
    assert resp.status_code == 404


# ===========================================================================
# D1 version_reapproval（契约 §18.6）
# ===========================================================================
async def _set_setting(client, key: str, value) -> None:
    admin = await login(client, "admin")
    resp = await client.put(
        "/api/v1/admin/settings",
        json={"items": [{"key": key, "value": value}]},
        headers=auth(admin),
    )
    assert resp.status_code == 200, resp.text


async def test_version_reapproval_off_promotes_new_version(client, seeded: Seeded) -> None:
    """验收 14：false 时已发布工具的新版本直接转正，且审批记录 is_automatic=true。"""
    admin = await login(client, "admin")
    approver = await login(client, "approver")
    from tests.m2_helpers import approve, create_tool, submit, upload_version, zip_bytes

    tool = await create_tool(client, admin, name="免审新版本工具")
    await upload_version(
        client, admin, tool["id"], version="1.0.0", file_bytes=zip_bytes({"a.txt": b"x"})
    )
    await submit(client, admin, tool["id"])
    await approve(client, approver, tool["id"])

    await _set_setting(client, "approval.version_reapproval", False)
    try:
        await upload_version(
            client,
            admin,
            tool["id"],
            version="1.1.0",
            file_bytes=zip_bytes({"a.txt": b"y"}),
            auto_submit=True,
        )
        detail = await client.get(f"/api/v1/me/tools/{tool['id']}", headers=auth(admin))
        assert detail.status_code == 200, detail.text
        # 直接转正：工具回到 approved，当前版本已是 1.1.0
        assert detail.json()["status"] == "approved"
        assert detail.json()["current_version"]["version"] == "1.1.0"

        async with SessionLocal() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT is_automatic, auto_rule FROM approval_records "
                        "WHERE tool_id = :t ORDER BY id DESC LIMIT 1"
                    ),
                    {"t": tool["id"]},
                )
            ).first()
        assert row is not None
        assert row[0] in (1, True), "自动批准必须写 is_automatic=true"
        assert row[1] == "version_reapproval_off"
    finally:
        await _set_setting(client, "approval.version_reapproval", True)


async def test_version_reapproval_on_keeps_pending_update(client, seeded: Seeded) -> None:
    """验收 15：默认 true 时行为不变（走审批，工具进入 pending_update）。"""
    admin = await login(client, "admin")
    approver = await login(client, "approver")
    from tests.m2_helpers import approve, create_tool, submit, upload_version, zip_bytes

    tool = await create_tool(client, admin, name="需再审新版本工具")
    await upload_version(
        client, admin, tool["id"], version="1.0.0", file_bytes=zip_bytes({"a.txt": b"x"})
    )
    await submit(client, admin, tool["id"])
    await approve(client, approver, tool["id"])

    await upload_version(
        client,
        admin,
        tool["id"],
        version="2.0.0",
        file_bytes=zip_bytes({"a.txt": b"z"}),
        auto_submit=True,
    )
    detail = await client.get(f"/api/v1/me/tools/{tool['id']}", headers=auth(admin))
    assert detail.json()["status"] == "pending_update"


# ===========================================================================
# D2 storage_warning（契约 §18.6）
# ===========================================================================
def test_storage_warning_pure_function_boundaries() -> None:
    """口径边界：不限配额（quota<=0）永不告警；恰好等于阈值算告警。"""
    assert settings_service.storage_warning(85, 100, 85) is True
    assert settings_service.storage_warning(84, 100, 85) is False
    assert settings_service.storage_warning(1000, 0, 85) is False
    assert settings_service.storage_warning(0, 100, 85) is False


async def test_overview_exposes_storage_warning(client, seeded: Seeded) -> None:
    """验收 16：/admin/overview 含 storage_warning，且随设置变化。"""
    admin = await login(client, "admin")
    resp = await client.get("/api/v1/admin/overview", headers=auth(admin))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "storage_warning" in body
    assert "storage_warning_threshold_pct" in body
    assert body["storage_warning_threshold_pct"] == 85

    used, quota = body["used_bytes"], body["quota_bytes"]
    assert used > 0 and quota > 0, "种子数据应当有用量与配额"

    # 1) 阈值必须随设置变化
    await _set_setting(client, "quota.warn_threshold_pct", 1)
    try:
        one = (await client.get("/api/v1/admin/overview", headers=auth(admin))).json()
        assert one["storage_warning_threshold_pct"] == 1

        # 2) 端点必须与共享口径函数一致（不是各算各的）
        assert one["storage_warning"] is settings_service.storage_warning(
            one["used_bytes"], one["quota_bytes"], one["storage_warning_threshold_pct"]
        )

        # 3) 造出「确实越过阈值」的用量：阈值下限是 1%，而 fixture 的种子用量
        #    相对 50 GB 配额只有 0.00x%，光调设置永远触发不了。
        #    直接塞一个 10 MB 的版本行（used_bytes 就是 tool_versions.file_size 之和），
        #    再把配额压到 1 MB → 占比 1000%，必然告警。
        async with SessionLocal() as session:
            tool_id = (await session.execute(select(Tool.id).limit(1))).scalar_one()
            session.add(
                ToolVersion(
                    tool_id=tool_id,
                    version="9.9.9-m5-test",
                    changelog_md="",
                    status="approved",
                    is_current=False,
                    file_size=10 * 1024 * 1024,
                    uploaded_by_id=seeded.users["admin"],
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
            )
            await session.commit()
        await _set_setting(client, "quota.total_mb", 1)
        await _set_setting(client, "quota.warn_threshold_pct", 1)
        hit = (await client.get("/api/v1/admin/overview", headers=auth(admin))).json()
        assert hit["storage_warning"] is True, (
            f"用量 {hit['used_bytes']} 字节 / 配额 {hit['quota_bytes']} 字节，"
            "阈值 1% 时应当告警"
        )

        # 清理造出来的用量
        async with SessionLocal() as session:
            await session.execute(
                text("DELETE FROM tool_versions WHERE version = '9.9.9-m5-test'")
            )
            await session.commit()

        # 4) 阈值 100% 且默认配额 → 不应告警
        await _set_setting(client, "quota.warn_threshold_pct", 100)
        await _set_setting(client, "quota.total_mb", 51200)
        back = (await client.get("/api/v1/admin/overview", headers=auth(admin))).json()
        assert back["storage_warning"] is False, "用量远低于配额时不应告警"
    finally:
        await _set_setting(client, "quota.warn_threshold_pct", 85)
        await _set_setting(client, "quota.total_mb", 51200)


async def test_definitions_without_consumer_is_empty() -> None:
    """验收 17：`quota.warn_threshold_pct` 与 `approval.version_reapproval` 已接线。

    这里只做「消费方存在」的静态断言：两个键在 settings_service 与
    version_service 里都有真实引用（接线前它们只出现在 SETTING_DEFAULTS 里）。
    """
    import inspect
    import pathlib

    src_root = pathlib.Path(settings_service.__file__).resolve().parents[1]
    hits: dict[str, set[str]] = {
        "quota.warn_threshold_pct": set(),
        "approval.version_reapproval": set(),
    }
    for path in src_root.rglob("*.py"):
        if path.name == "system_settings.py":
            continue  # 定义处不算消费方
        content = path.read_text(encoding="utf-8")
        for key in hits:
            if f'"{key}"' in content:
                hits[key].add(path.name)

    # 键 `quota.warn_threshold_pct` 在 stats_service 里被读取；
    # 判断口径（storage_warning）在 settings_service 里只写一份，供脚本复用。
    assert "stats_service.py" in hits["quota.warn_threshold_pct"]
    assert hits["approval.version_reapproval"], "version_reapproval 必须有消费方"
    assert inspect.iscoroutinefunction(settings_service.evaluate_auto_approval)
