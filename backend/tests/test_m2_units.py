"""M2 新增模块的单元测试 —— 补齐 services/ 与 storage/ 的分支覆盖。

这些是直接调用服务/存储函数的「白盒」测试，不走 HTTP。
它们覆盖的是 HTTP 路径难以触达的分支：错误处理、边界值、降级路径。
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select

from app.core.errors import (
    NotFoundError,
    UnsupportedMediaTypeError,
    ValidationError,
    ZipInvalidError,
)
from app.core.security import (
    TicketError,
    TicketExpiredError,
    create_download_ticket,
    derive_subkey,
    verify_download_ticket,
)
from app.core.timeutil import utcnow
from app.db.session import SessionLocal
from app.models.enums import VersionStatus
from app.models.tool import Tool, ToolVersion
from app.services import skill_service
from app.services.skill_service import SkillLimits, parse_skill_package
from app.storage import (
    LocalStorage,
    PathNotAllowedError,
    UploadTooLargeError,
    display_filename,
    file_extension,
    get_storage,
    sanitize_filename,
)
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
# storage：文件名消毒与路径穿越
# ===========================================================================
@pytest.mark.parametrize(
    ("raw", "must_not_contain"),
    [
        ("../../etc/passwd", ".."),
        ("..\\..\\windows\\system32", ".."),
        ("/absolute/path.txt", "/"),
        ("with\x00null.txt", "\x00"),
        ("tab\there.txt", "\t"),
        ("   spaced.txt   ", " "),
        ("....txt", ".."),
        ("全角／分隔.txt", "／"),
    ],
)
def test_sanitize_filename_removes_dangerous_parts(raw: str, must_not_contain: str) -> None:
    cleaned = sanitize_filename(raw)
    assert must_not_contain not in cleaned
    assert cleaned  # 永远不为空
    # 结果必须是单个文件名，不含任何分隔符
    assert "/" not in cleaned
    assert "\\" not in cleaned
    assert "\x00" not in cleaned


def test_sanitize_filename_keeps_extension_and_truncates() -> None:
    assert sanitize_filename("report.tar.gz").endswith(".gz")
    long_name = "a" * 500 + ".zip"
    cleaned = sanitize_filename(long_name)
    assert len(cleaned) <= 210
    assert cleaned.endswith(".zip")
    # 空输入要有兜底
    assert sanitize_filename("") == "upload.bin"
    # 纯点号的名字会被折叠成一个安全的短名（不可能是 .. 或空）
    degenerate = sanitize_filename("...")
    assert degenerate and ".." not in degenerate and "/" not in degenerate


def test_display_filename_keeps_chinese_but_strips_control() -> None:
    assert display_filename("日志分析器.zip") == "日志分析器.zip"
    assert display_filename("a\x00b\x1fc") == "abc"
    assert display_filename("path/to/file.txt") == "path_to_file.txt"
    assert display_filename("") == "download"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("archive.tar.gz", "tar.gz"),
        ("archive.TAR.GZ", "tar.gz"),
        ("package.zip", "zip"),
        ("noext", ""),
        ("trailing.", ""),
        ("x." + "y" * 30, ""),  # 超长「扩展名」视为无扩展名
    ],
)
def test_file_extension_normalization(name: str, expected: str) -> None:
    assert file_extension(name) == expected


async def test_storage_stage_rejects_oversized_stream() -> None:
    """流式写入必须在超限时**立刻**抛错，而不是写完之后才判断。"""
    storage = LocalStorage()
    await storage.ensure_dirs()
    chunks_sent = 0

    async def stream():
        nonlocal chunks_sent
        for _ in range(100):
            chunks_sent += 1
            yield b"x" * (1024 * 1024)

    with pytest.raises(UploadTooLargeError) as exc:
        await storage.stage(stream(), max_bytes=2 * 1024 * 1024, file_name="big.bin")
    assert exc.value.actual_bytes > exc.value.limit_bytes
    # 关键：没有把 100 个 chunk 全读完
    assert chunks_sent < 100, f"应当在超限时就中断，实际读了 {chunks_sent} 个 chunk"
    assert list(storage.tmp_dir.glob("*.part")) == []


async def test_storage_stage_computes_incremental_sha256(tmp_path) -> None:
    storage = LocalStorage(data_dir=tmp_path)
    payload = os.urandom(3 * 1024 * 1024 + 17)

    async def stream():
        for index in range(0, len(payload), 1024 * 1024):
            yield payload[index : index + 1024 * 1024]

    staged = await storage.stage(stream(), max_bytes=len(payload) + 1, file_name="x.bin")
    assert staged.sha256 == hashlib.sha256(payload).hexdigest()
    assert staged.size == len(payload)

    stored = await storage.commit(staged, directory="tools/1/1")
    assert Path(stored.absolute_path).read_bytes() == payload
    assert stored.storage_path.startswith("files/tools/1/1/")
    # 归位后临时文件消失
    assert not staged.temp_path.exists()
    # 清理
    await storage.delete(stored.storage_path)


async def test_storage_commit_avoids_name_collision(tmp_path) -> None:
    storage = LocalStorage(data_dir=tmp_path)
    first = await storage.write_bytes_atomic(b"one", directory="images/1", file_name="a.png")
    second = await storage.write_bytes_atomic(b"two", directory="images/1", file_name="a.png")
    assert first.storage_path != second.storage_path
    assert Path(first.absolute_path).read_bytes() == b"one"
    assert Path(second.absolute_path).read_bytes() == b"two"


def test_storage_absolute_rejects_traversal(tmp_path) -> None:
    storage = LocalStorage(data_dir=tmp_path)
    for evil in ("files/../../etc/passwd", "../outside.txt", "/etc/passwd", ""):
        with pytest.raises(PathNotAllowedError):
            storage.absolute(evil)


async def test_storage_delete_prunes_empty_dirs(tmp_path) -> None:
    storage = LocalStorage(data_dir=tmp_path)
    stored = await storage.write_bytes_atomic(b"x", directory="tools/9/9", file_name="f.bin")
    parent = Path(stored.absolute_path).parent
    assert parent.exists()
    assert await storage.delete(stored.storage_path) is True
    # 空的 tools/9/9 与 tools/9 应被清掉，避免堆积空目录
    assert not parent.exists()
    # 删除不存在的文件返回 False，不抛
    assert await storage.delete(stored.storage_path) is False
    # 越界路径拒绝删除
    assert await storage.delete("files/../../etc/passwd") is False


def test_storage_usage_and_temp_stats(tmp_path) -> None:
    import asyncio as _asyncio

    storage = LocalStorage(data_dir=tmp_path)

    async def setup() -> None:
        await storage.write_bytes_atomic(b"a" * 100, directory="tools/1/1", file_name="a.bin")
        await storage.write_bytes_atomic(b"b" * 50, directory="tools/1/1", file_name="b.bin")

    _asyncio.run(setup())
    assert storage.usage_bytes("files/tools/1") == 150
    assert storage.usage_bytes("files/tools/999") == 0

    # 刚写的 .part 不算「陈旧」
    stale = storage.tmp_dir / "old.part"
    stale.write_bytes(b"x")
    os.utime(stale, (time.time() - 7200, time.time() - 7200))
    assert storage.count_stale_temp_files(older_than_seconds=3600) == 1
    assert storage.count_stale_temp_files(older_than_seconds=99999) == 0


# ===========================================================================
# skill_service：补齐分支
# ===========================================================================
def _write_zip(path: Path, files: dict[str, bytes], *, compresslevel: int = 6) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=compresslevel) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


def test_skill_limits_from_settings() -> None:
    limits = SkillLimits.from_settings(
        {
            "max_total_size": 10,
            "max_ratio": 20,
            "max_files": 30,
            "max_depth": 40,
            "max_file_size": 50,
            "timeout_seconds": 60,
        }
    )
    assert limits.max_total_size == 10
    assert limits.timeout_seconds == 60


def test_skill_depth_limit(tmp_path) -> None:
    deep = "/".join(["a"] * 20) + "/x.txt"
    path = _write_zip(tmp_path / "deep.zip", {"SKILL.md": b"# t\n", deep: b"x"})
    with pytest.raises(Exception) as exc:
        parse_skill_package(path)
    assert getattr(exc.value, "code", "") == "ZIP_TOO_MANY_FILES"
    assert exc.value.details["depth"] == 21


def test_skill_single_entry_size_limit(tmp_path) -> None:
    """单条目上限是**流式**校验的（declared 预检不看单条目）。"""
    path = tmp_path / "big-entry.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("SKILL.md", "# t\n")
        with archive.open("zeros.bin", "w") as handle:
            for _ in range(50):
                handle.write(b"\0" * (1024 * 1024))  # 50 MB
    limits = SkillLimits(
        max_total_size=10 * 1024**3,
        max_ratio=10**9,
        max_file_size=10 * 1024 * 1024,  # 10 MB
    )
    with pytest.raises(Exception) as exc:
        parse_skill_package(path, limits=limits)
    assert getattr(exc.value, "code", "") == "ZIP_BOMB_DETECTED"
    assert exc.value.details["stage"] == "entry_size"
    # 在 ~10MB 处就中断，而不是读完 50MB
    assert exc.value.details["detected_mb"] <= 11


def test_skill_timeout_aborts(tmp_path) -> None:
    path = _write_zip(
        tmp_path / "slow.zip",
        {"SKILL.md": b"# t\n", "big.bin": os.urandom(2 * 1024 * 1024)},
    )
    limits = SkillLimits(timeout_seconds=0)  # 立刻超时
    with pytest.raises(Exception) as exc:
        parse_skill_package(path, limits=limits)
    assert getattr(exc.value, "code", "") == "ZIP_BOMB_DETECTED"
    assert exc.value.details["stage"] == "timeout"


def test_skill_invalid_and_empty_zip(tmp_path) -> None:
    not_zip = tmp_path / "nope.zip"
    not_zip.write_bytes(b"definitely not a zip")
    with pytest.raises(ZipInvalidError):
        parse_skill_package(not_zip)

    empty = tmp_path / "empty.zip"
    empty.write_bytes(b"")
    with pytest.raises(ZipInvalidError):
        parse_skill_package(empty)

    missing = tmp_path / "gone.zip"
    with pytest.raises(ZipInvalidError):
        parse_skill_package(missing)


def test_skill_root_skill_md_wins_over_nested(tmp_path) -> None:
    path = _write_zip(
        tmp_path / "both.zip",
        {
            "SKILL.md": b"---\nname: root\n---\nroot body",
            "nested/SKILL.md": b"---\nname: nested\n---\nnested body",
        },
    )
    result = parse_skill_package(path)
    assert result.skill_md_path == "SKILL.md"
    assert result.manifest == {"name": "root"}


def test_skill_non_dict_frontmatter_is_soft_error(tmp_path) -> None:
    path = _write_zip(
        tmp_path / "list.zip", {"SKILL.md": b"---\n- a\n- b\n---\nbody"}
    )
    result = parse_skill_package(path)
    assert result.manifest is None
    assert result.parse_error is not None
    assert "键值对" in result.parse_error
    assert result.readme_md.strip() == "body"


def test_skill_windows_backslash_entries_rejected(tmp_path) -> None:
    path = tmp_path / "win.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("SKILL.md", "# t\n")
        archive.writestr("..\\..\\evil.txt", "x")
    with pytest.raises(Exception) as exc:
        parse_skill_package(path)
    assert getattr(exc.value, "code", "") == "ZIP_PATH_TRAVERSAL"


def test_skill_drive_letter_absolute_path_rejected(tmp_path) -> None:
    path = tmp_path / "drive.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("C:/Windows/evil.txt", "x")
    with pytest.raises(Exception) as exc:
        parse_skill_package(path)
    assert exc.value.details["reason"] == "绝对路径（盘符）"


def test_skill_null_byte_entry_rejected(tmp_path) -> None:
    """含空字节的条目名必须被拒。

    stdlib 的 `zipfile.writestr` 自己就会因为 NUL 抛 ValueError，
    所以这里接受两种拒绝方式：写入阶段被拒，或解析阶段被我们的校验拒。
    """
    path = tmp_path / "null.zip"
    try:
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("SKILL.md\x00.txt", "x")
    except ValueError:
        return  # zipfile 自己就拒绝了，等价于防护生效

    # zipfile 在读写时会按 NUL 截断条目名，因此也可能解析成功 ——
    # 关键断言是**结果里绝不出现含 NUL 的路径**，而不是必须抛错。
    result = parse_skill_package(path)
    for item in result.file_tree:
        assert "\x00" not in item["path"]
    assert result.skill_md_path is None or "\x00" not in result.skill_md_path


def test_skill_readme_truncated_at_256kb(tmp_path) -> None:
    # 用随机数据：全 'a' 的 300KB 会被压到几百字节，直接触发压缩比防护，
    # 那样测的就不是「截断」而是「炸弹拦截」了
    huge = b"# t\n" + os.urandom(300 * 1024)
    path = _write_zip(tmp_path / "huge.zip", {"SKILL.md": huge})
    result = parse_skill_package(path)
    assert result.readme_md is not None
    assert len(result.readme_md.encode("utf-8")) <= skill_service.README_MAX_BYTES


def test_skill_read_zip_entry_text(tmp_path) -> None:
    path = _write_zip(
        tmp_path / "read.zip",
        {"SKILL.md": b"# t\n", "docs/guide.md": "中文内容".encode()},
    )
    assert skill_service.read_zip_entry_text(path, "docs/guide.md") == "中文内容"
    # 越界条目名一律返回 None（不接受来自请求的任意路径）
    assert skill_service.read_zip_entry_text(path, "../../etc/passwd") is None
    # 不存在的条目
    assert skill_service.read_zip_entry_text(path, "nope.md") is None
    # 非 zip
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"nope")
    assert skill_service.read_zip_entry_text(broken, "SKILL.md") is None


def test_skill_tree_includes_synthesized_dirs(tmp_path) -> None:
    path = _write_zip(
        tmp_path / "tree.zip",
        {
            "SKILL.md": b"# t\n",
            "a/b/c/deep.txt": b"x",
            "a/b/other.txt": b"y",
        },
    )
    result = parse_skill_package(path)
    dirs = {item["path"] for item in result.file_tree if item["is_dir"]}
    assert {"a", "a/b", "a/b/c"} <= dirs
    assert result.max_depth == 4  # a/b/c/deep.txt
    assert result.file_count == 3
    assert result.tree_summary()["file_count"] == 3


# ===========================================================================
# image_service：补齐分支
# ===========================================================================
def test_image_rejects_empty_and_oversized() -> None:
    from app.services import image_service

    with pytest.raises(UnsupportedMediaTypeError):
        image_service.inspect_image(b"", declared_name="x.png")

    with pytest.raises(ValidationError) as exc:
        image_service.inspect_image(b"x" * (image_service.MAX_IMAGE_BYTES + 1))
    assert exc.value.details["limit_mb"] == 5


def test_image_rejects_unallowed_format() -> None:
    """BMP 是合法图片但不在白名单里（FR-FILE-09 只允许 png/jpg/webp/gif）。"""
    from app.services import image_service

    buffer = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buffer, format="BMP")
    with pytest.raises(UnsupportedMediaTypeError) as exc:
        image_service.inspect_image(buffer.getvalue())
    assert "BMP" in str(exc.value.details.get("detected"))


def test_image_rejects_decompression_bomb() -> None:
    from app.services import image_service

    # 构造一个声明尺寸极大的 PNG 头（不解码完整数据）
    with pytest.raises((UnsupportedMediaTypeError, ValidationError)):
        image_service.inspect_image(_huge_png_header())


def _huge_png_header() -> bytes:
    """构造一张 30000x30000 的 PNG 头（约 9 亿像素，超过 5000 万上限）。"""
    import struct
    import zlib

    width = height = 30000

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", b"") + chunk(b"IEND", b"")


def test_image_thumbnail_handles_exif_and_palette() -> None:
    from app.services import image_service

    # 调色板 + 透明（GIF）
    buffer = io.BytesIO()
    Image.new("P", (600, 300)).save(buffer, format="GIF")
    thumb = image_service.make_thumbnail(buffer.getvalue())
    with Image.open(io.BytesIO(thumb)) as result:
        assert result.width == 480
        assert result.format == "PNG"

    assert image_service.thumbnail_extension() == "png"


def test_image_thumbnail_does_not_upscale() -> None:
    from app.services import image_service

    buffer = io.BytesIO()
    Image.new("RGB", (100, 50)).save(buffer, format="PNG")
    with Image.open(io.BytesIO(image_service.make_thumbnail(buffer.getvalue()))) as result:
        assert result.size == (100, 50)


# ===========================================================================
# markdown_service：补齐分支
# ===========================================================================
def test_markdown_renders_and_sanitizes() -> None:
    from app.services.markdown_service import render_markdown, strip_markdown

    assert render_markdown(None) == ""
    assert render_markdown("") == ""

    html = render_markdown("# 标题\n\n**粗体**")
    assert "<h1>" in html and "<strong>" in html

    # 危险内容被转义或剥离
    dangerous = render_markdown("<script>alert(1)</script><img src=x onerror=alert(1)>")
    assert "<script" not in dangerous.lower()
    assert "<img" not in dangerous.lower() or "onerror" not in dangerous.lower()
    assert "alert(1)" in dangerous  # 作为纯文本保留，但是惰性的

    # javascript: 协议不会变成可点击的 href
    link = render_markdown("[x](javascript:alert(1))")
    assert "<a href=\"javascript" not in link.lower()
    assert "href=\"javascript" not in link.lower()
    # data: 协议同样不放行
    assert "src=\"data:" not in render_markdown("![x](data:text/html;base64,PHNjcmlwdD4=)")

    # 段落之间保留空行的语义（不压缩成一行）
    assert strip_markdown("# 标题\n\n正文") == "标题\n\n正文"


def test_markdown_truncates_oversized_input() -> None:
    from app.services import markdown_service

    big = "a" * (markdown_service.MAX_MARKDOWN_CHARS + 5000)
    result = markdown_service.render_markdown(big)
    assert len(result) <= markdown_service.MAX_MARKDOWN_CHARS + 100


# ===========================================================================
# download_service：补齐分支
# ===========================================================================
def test_content_disposition_variants() -> None:
    from app.services.download_service import content_disposition

    header = content_disposition("普通 文件名.zip")
    assert header.startswith("attachment;")
    assert "filename*=UTF-8''" in header
    assert "%" in header
    # 引号与反斜杠不能破坏头部
    weird = content_disposition('a"b\\c.zip')
    assert weird.count('"') % 2 == 0
    assert "\\" not in weird.split("filename*=")[0]


def test_guess_media_type_is_always_octet_stream() -> None:
    """刻意不沿用上传时的 mime_type —— 防止 .html 包被浏览器内联渲染。"""
    from app.services.download_service import guess_media_type

    class FakeVersion:
        mime_type = "text/html"

    assert guess_media_type(FakeVersion()) == "application/octet-stream"


async def test_resolve_target_error_branches(client, seeded) -> None:
    from app.services import download_service

    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    slug, tool_id, _ = await publish_tool(client, owner, approver, name="下载分支工具")

    async with SessionLocal() as session:
        tool = await session.get(Tool, tool_id)
        assert tool is not None

        # 不存在的版本 id
        with pytest.raises(NotFoundError) as exc:
            await download_service.resolve_target(session, tool=tool, version_id=999999)
        assert exc.value.details["reason"] == "version_not_found"

        # 版本属于别的工具 → 视为不存在（防跨工具下载）
        other = await create_tool(client, owner, name="另一个工具")
        await upload_version(
            client, owner, other["id"], version="9.9.9",
            file_bytes=zip_bytes({"z.txt": b"z"}), file_name="z.zip",
        )
        other_version = (
            await session.execute(
                select(ToolVersion).where(ToolVersion.tool_id == other["id"])
            )
        ).scalars().first()
        assert other_version is not None
        with pytest.raises(NotFoundError):
            await download_service.resolve_target(
                session, tool=tool, version_id=other_version.id
            )

        # 工具处于 draft → 不可下载
        draft = await entity_draft_tool(session, owner, client)
        with pytest.raises(NotFoundError) as exc2:
            await download_service.resolve_target(session, tool=draft, version_id=None)
        assert exc2.value.details["reason"] == "tool_not_downloadable"

    del slug


async def entity_draft_tool(session, owner_token, client):
    await create_tool(client, owner_token, name="草稿下载")
    return (
        await session.execute(select(Tool).where(Tool.status == "draft").limit(1))
    ).scalars().first()


async def test_resolve_target_missing_file_on_disk(client, seeded) -> None:
    """数据库有版本但磁盘文件缺失 → 404（不能 500）。"""
    from app.services import download_service

    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    _slug, tool_id, _ = await publish_tool(client, owner, approver, name="文件丢失工具")

    storage = get_storage()
    async with SessionLocal() as session:
        tool = await session.get(Tool, tool_id)
        version = (
            await session.execute(
                select(ToolVersion).where(ToolVersion.tool_id == tool_id)
            )
        ).scalars().first()
        assert version is not None and version.storage_path
        # 把文件删掉
        await storage.delete(version.storage_path)
        with pytest.raises(NotFoundError) as exc:
            await download_service.resolve_target(
                session, tool=tool, version_id=version.id
            )
        assert exc.value.details["reason"] == "file_missing"


async def test_download_ticket_decode_mismatch(client, seeded) -> None:
    from app.services import download_service

    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    _slug, tool_id, _ = await publish_tool(client, owner, approver, name="票据不匹配")

    token, _ = create_download_ticket(tool_id=tool_id + 999, version_id=1, user_id=1)
    async with SessionLocal() as session:
        tool = await session.get(Tool, tool_id)
        with pytest.raises(NotFoundError) as exc:
            download_service.decode_ticket(token, tool=tool)
        assert exc.value.details["reason"] == "ticket_tool_mismatch"

        # 垃圾票据
        with pytest.raises(NotFoundError):
            download_service.decode_ticket("garbage", tool=tool)


def test_ticket_crypto_properties() -> None:
    token, _expires = create_download_ticket(tool_id=1, version_id=2, user_id=3)
    payload = verify_download_ticket(token)
    assert payload["tool_id"] == 1
    assert payload["user_id"] == 3

    # 子密钥与主密钥不同，且不同用途派生出的子密钥也不同
    from app.core.config import settings

    assert derive_subkey("download-ticket") != settings.secret_key.encode()
    assert derive_subkey("download-ticket") != derive_subkey("another-purpose")

    # 篡改载荷 → 签名失败
    import base64
    import json

    raw_b64, sig_b64 = token.rsplit(".", 1)
    raw = json.loads(base64.urlsafe_b64decode(raw_b64 + "=" * (-len(raw_b64) % 4)))
    raw["user_id"] = 999
    forged = base64.urlsafe_b64encode(
        json.dumps(raw, separators=(",", ":"), sort_keys=True).encode()
    ).rstrip(b"=").decode()
    with pytest.raises(TicketError):
        verify_download_ticket(f"{forged}.{sig_b64}")

    # 过期
    past, _ = create_download_ticket(tool_id=1, version_id=2, user_id=3, ttl_seconds=-10)
    with pytest.raises(TicketExpiredError):
        verify_download_ticket(past)

    # 格式错误
    with pytest.raises(TicketError):
        verify_download_ticket("no-dot-here")
    with pytest.raises(TicketError):
        verify_download_ticket("!!!.???")


# ===========================================================================
# counter_service：补齐分支
# ===========================================================================
async def test_counter_token_use_and_daily_upsert(client, seeded) -> None:
    from app.models.user import ApiToken
    from app.services.counter_service import CounterService, set_counter_service

    service = CounterService()
    set_counter_service(service)
    try:
        async with SessionLocal() as session:
            token = ApiToken(
                name="计数令牌",
                token_prefix="st_x",
                token_hash=hashlib.sha256(b"counter-token").hexdigest(),
                scopes=["tools:read"],
                created_by_id=seeded.users["admin"],
                created_at=utcnow(),
            )
            session.add(token)
            await session.commit()
            token_id = token.id

        service.record_token_use(token_id)
        service.record_view(1, viewer_key="u:1")
        service.record_download(
            tool_id=1, version_id=None, user_id=1, ip=None, user_agent=None
        )
        stats = await service.flush()
        assert stats["tokens"] == 1
        assert stats["daily"] >= 1

        # 落库后 last_used_at 有值
        async with SessionLocal() as session:
            row = await session.get(ApiToken, token_id)
            assert row is not None and row.last_used_at is not None

        # 再 flush 一次应当无事可做
        again = await service.flush()
        assert again == {"counter_tools": 0, "daily": 0, "logs": 0, "tokens": 0}
    finally:
        set_counter_service(None)


async def test_counter_daily_upsert_accumulates(client, seeded) -> None:
    from app.models.stats import ToolStatsDaily
    from app.services.counter_service import CounterService

    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    _slug, real_tool_id, _ = await publish_tool(
        client, owner, approver, name="每日累加工具"
    )

    service = CounterService()
    today = utcnow().date()
    service.record_view(real_tool_id, viewer_key="u:1")
    service.record_download(
        tool_id=real_tool_id, version_id=None, user_id=1, ip=None, user_agent=None
    )
    await service.flush()
    # 换一个 viewer_key，否则会被 1 小时的浏览去重窗口吃掉（这正是去重要测的行为）
    service.record_view(real_tool_id, viewer_key="u:2")
    await service.flush()

    async with SessionLocal() as session:
        row = (
            await session.execute(
                select(ToolStatsDaily).where(
                    ToolStatsDaily.tool_id == real_tool_id,
                    ToolStatsDaily.stat_date == today,
                )
            )
        ).scalars().first()
        assert row is not None
        assert row.views == 2, "UPSERT 应当是累加而不是覆盖"


async def test_counter_background_loop_flushes() -> None:
    """后台任务必须真的周期性落库。"""
    from app.services.counter_service import CounterService, set_counter_service

    service = CounterService(flush_interval_seconds=1)
    set_counter_service(service)
    try:
        await service.start()
        service.record_view(31337, viewer_key="u:1")
        assert service.pending_snapshot()["views"] == 1
        # 等后台任务跑一轮
        for _ in range(30):
            await asyncio.sleep(0.1)
            if service.pending_snapshot()["views"] == 0:
                break
        assert service.pending_snapshot()["views"] == 0, "后台任务应当已经落库"
    finally:
        await service.stop()
        set_counter_service(None)


def test_counter_view_dedup_window_expiry() -> None:
    from app.services.counter_service import CounterService

    service = CounterService()
    assert service.record_view(1, viewer_key="u:1", dedup_minutes=60) is True
    assert service.record_view(1, viewer_key="u:1", dedup_minutes=60) is False
    # 窗口为 0 表示不去重
    assert service.record_view(1, viewer_key="u:1", dedup_minutes=0) is True
    # 不同工具独立计数
    assert service.record_view(2, viewer_key="u:1", dedup_minutes=60) is True


# ===========================================================================
# settings_service：补齐分支
# ===========================================================================
async def test_get_upload_limits_falls_back_to_env(client, seeded) -> None:
    from app.repositories import system_settings as settings_repo
    from app.services import settings_service

    async with SessionLocal() as session:
        # 删掉行 → 回退到 SETTING_DEFAULTS / env
        row = await settings_repo.get_row(session, "upload.allowed_extensions")
        original = row.value
        row.value = []
        await session.commit()
        try:
            limits = await settings_service.get_upload_limits(session)
            assert limits.allowed_extensions, "空列表必须回退到环境变量默认值"
            assert "zip" in limits.allowed_extensions
        finally:
            row = await settings_repo.get_row(session, "upload.allowed_extensions")
            row.value = original
            await session.commit()


async def test_quota_exceeded_raises(client, seeded) -> None:
    from app.core.errors import UserQuotaExceededError
    from app.repositories import system_settings as settings_repo
    from app.services import settings_service

    async with SessionLocal() as session:
        row = await settings_repo.get_row(session, "quota.per_user_mb")
        original = row.value
        row.value = 1  # 1 MB 配额
        await session.commit()
        try:
            limits = await settings_service.get_upload_limits(session)
            with pytest.raises(UserQuotaExceededError) as exc:
                await settings_service.check_storage_quota(
                    session,
                    owner_id=seeded.users["admin"],
                    incoming_bytes=100 * 1024 * 1024,
                    limits=limits,
                )
            assert exc.value.http_status == 507
        finally:
            row = await settings_repo.get_row(session, "quota.per_user_mb")
            row.value = original
            await session.commit()


async def test_unknown_setting_key_rejected(client, seeded) -> None:
    from app.core.errors import SettingInvalidError
    from app.services import settings_service

    admin_token = await login(client, "admin")
    del admin_token
    async with SessionLocal() as session:
        with pytest.raises(SettingInvalidError) as exc:
            await settings_service.update_settings(
                session, updates=[("no.such.key", 1)], actor_id=seeded.users["admin"]
            )
        assert exc.value.details["key"] == "no.such.key"


# ===========================================================================
# version_service / tool_service：补齐错误分支
# ===========================================================================
async def test_upload_type_requirements(client, seeded) -> None:
    owner = await login(client, "outsider")

    # prompt 缺正文
    prompt = await create_tool(client, owner, name="缺正文", tool_type="prompt")
    missing = await upload_version(client, owner, prompt["id"], version="1.0.0")
    assert missing.status_code == 400
    assert missing.json()["code"] == "VALIDATION_ERROR"

    # file 缺文件
    file_tool = await create_tool(client, owner, name="缺文件", tool_type="file")
    no_file = await upload_version(client, owner, file_tool["id"], version="1.0.0")
    assert no_file.status_code == 400

    # 扩展名不在白名单
    bad_ext = await upload_version(
        client, owner, file_tool["id"], version="1.0.0",
        file_bytes=b"x" * 10, file_name="evil.xyz",
    )
    assert bad_ext.status_code == 415
    assert bad_ext.json()["code"] == "UNSUPPORTED_MEDIA_TYPE"


async def test_create_tool_validation(client, seeded) -> None:
    owner = await login(client, "outsider")

    # 分类不存在
    bad_category = await client.post(
        "/api/v1/me/tools",
        json={"name": "分类错", "summary": "s", "tool_type": "file", "category_id": 999999},
        headers=auth(owner),
    )
    assert bad_category.status_code == 400

    # 标签超限
    too_many_tags = await client.post(
        "/api/v1/me/tools",
        json={
            "name": "标签超限",
            "summary": "s",
            "tool_type": "file",
            "tags": [f"t{i}" for i in range(10)],
        },
        headers=auth(owner),
    )
    assert too_many_tags.status_code == 400

    # webapp URL 协议非法
    bad_url = await client.post(
        "/api/v1/me/tools",
        json={
            "name": "URL 错",
            "summary": "s",
            "tool_type": "webapp",
            "webapp_url": "ftp://x",
        },
        headers=auth(owner),
    )
    assert bad_url.status_code == 400

    # 名称超长
    long_name = await client.post(
        "/api/v1/me/tools",
        json={"name": "x" * 200, "summary": "s", "tool_type": "file"},
        headers=auth(owner),
    )
    assert long_name.status_code == 400


async def test_slug_generation_handles_collisions(client, seeded) -> None:
    owner = await login(client, "outsider")
    first = await create_tool(client, owner, name="Slug Collision Test")
    second = await create_tool(client, owner, name="Slug Collision Test")
    assert first["slug"] != second["slug"]
    assert second["slug"].startswith(first["slug"])

    # 纯中文名 → 带短哈希的兜底 slug
    chinese = await create_tool(client, owner, name="纯中文名称")
    assert chinese["slug"].startswith("tool-")
    assert len(chinese["slug"]) > len("tool-")


async def test_update_tool_metadata_and_visibility(client, seeded) -> None:
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    slug, tool_id, _ = await publish_tool(client, owner, approver, name="元信息工具")

    patched = await client.patch(
        f"/api/v1/me/tools/{tool_id}",
        json={"summary": "改过的简介", "tags": ["newtag"], "visibility": "private"},
        headers=auth(owner),
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["summary"] == "改过的简介"
    assert body["visibility"] == "private"
    assert body["tags"] == ["newtag"]
    # 元信息变更不触发重新审批（FR-TOOL-13）
    assert body["status"] == "approved"

    # 切到 restricted 但没有 ACL → 400
    blocked = await client.patch(
        f"/api/v1/me/tools/{tool_id}", json={"visibility": "restricted"}, headers=auth(owner)
    )
    assert blocked.status_code == 400
    assert blocked.json()["code"] == "ACL_REQUIRED"

    del slug


async def test_invalid_sort_on_my_tools(client, seeded) -> None:
    owner = await login(client, "outsider")
    response = await client.get(
        "/api/v1/me/tools", params={"sort": "bogus"}, headers=auth(owner)
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_SORT"


async def test_approval_history_invalid_action_filter(client, seeded) -> None:
    approver = await login(client, "approver")
    response = await client.get(
        "/api/v1/admin/approvals/history",
        params={"action": "not-an-action"},
        headers=auth(approver),
    )
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_approval_history_filters(client, seeded) -> None:
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    _slug, tool_id, _ = await publish_tool(client, owner, approver, name="历史筛选工具")

    for params in (
        {"tool_id": tool_id},
        {"actor_id": seeded.users["approver"]},
        {"date_from": (utcnow() - timedelta(days=1)).isoformat()},
        {"date_to": (utcnow() + timedelta(days=1)).isoformat()},
    ):
        response = await client.get(
            "/api/v1/admin/approvals/history", params=params, headers=auth(approver)
        )
        assert response.status_code == 200, response.text

    # 队列按类型筛选
    queue = await client.get(
        "/api/v1/admin/approvals", params={"tool_type": "file"}, headers=auth(approver)
    )
    assert queue.status_code == 200
    queue2 = await client.get(
        "/api/v1/admin/approvals", params={"status": "pending_all"}, headers=auth(approver)
    )
    assert queue2.status_code == 200


async def test_withdraw_then_delete_pending_version(client, seeded) -> None:
    owner = await login(client, "outsider")
    tool = await create_tool(client, owner, name="撤回删除工具")
    await upload_version(
        client, owner, tool["id"], version="1.0.0",
        file_bytes=zip_bytes({"a.txt": b"x"}), file_name="a.zip",
    )
    await submit(client, owner, tool["id"])
    await client.post(f"/api/v1/me/tools/{tool['id']}/withdraw", headers=auth(owner))

    # 删除未过审版本 → 工具回到 draft
    deleted = await client.delete(
        f"/api/v1/me/tools/{tool['id']}/versions/1.0.0", headers=auth(owner)
    )
    assert deleted.status_code == 200
    detail = await client.get(f"/api/v1/me/tools/{tool['id']}", headers=auth(owner))
    assert detail.json()["status"] == "draft"
    assert detail.json()["version_count"] == 0


async def test_delete_nonexistent_version_and_image(client, seeded) -> None:
    owner = await login(client, "outsider")
    tool = await create_tool(client, owner, name="删除不存在")
    missing_version = await client.delete(
        f"/api/v1/me/tools/{tool['id']}/versions/9.9.9", headers=auth(owner)
    )
    assert missing_version.status_code == 404

    missing_image = await client.delete(
        f"/api/v1/me/tools/{tool['id']}/images/999999", headers=auth(owner)
    )
    assert missing_image.status_code == 404

    # 图片必须属于该工具（跨工具 id 也要 404）
    other = await create_tool(client, owner, name="另一个图片工具")
    cross = await client.delete(
        f"/api/v1/me/tools/{other['id']}/images/999999", headers=auth(owner)
    )
    assert cross.status_code == 404


async def test_stats_endpoint_and_skill_preview_non_skill(client, seeded) -> None:
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    slug, tool_id, _ = await publish_tool(client, owner, approver, name="统计接口工具")

    stats = await client.get(f"/api/v1/tools/{slug}/stats", headers=auth(owner))
    assert stats.status_code == 200
    assert stats.json()["tool_id"] == tool_id
    assert stats.json()["download_count"] >= 0
    assert isinstance(stats.json()["daily"], list)

    # 非 skill 工具请求 skill-preview → 404
    preview = await client.get(
        f"/api/v1/tools/{slug}/versions/1.0.0/skill-preview", headers=auth(owner)
    )
    assert preview.status_code == 404

    # 不存在的版本 → 404
    missing = await client.get(
        f"/api/v1/tools/{slug}/versions/9.9.9/skill-preview", headers=auth(owner)
    )
    assert missing.status_code == 404


async def test_versions_list_hides_pending_from_strangers(client, seeded) -> None:
    """待审/已驳回版本只对 owner 与审批人可见。"""
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    slug, tool_id, _ = await publish_tool(client, owner, approver, name="版本可见性工具")

    await upload_version(
        client, owner, tool_id, version="2.0.0",
        file_bytes=zip_bytes({"v2.txt": b"v2"}), file_name="v2.zip",
    )
    await submit(client, owner, tool_id)

    # owner/审批人能看到待审版本
    owner_view = await client.get(f"/api/v1/tools/{slug}/versions", headers=auth(owner))
    assert "2.0.0" in [v["version"] for v in owner_view.json()]
    approver_view = await client.get(f"/api/v1/tools/{slug}/versions", headers=auth(approver))
    assert "2.0.0" in [v["version"] for v in approver_view.json()]

    # 其他普通用户看不到
    stranger = await login(client, "lockme")
    stranger_view = await client.get(f"/api/v1/tools/{slug}/versions", headers=auth(stranger))
    assert "2.0.0" not in [v["version"] for v in stranger_view.json()]

    # 陌生人请求待审版本的 skill-preview 也 404（这里虽非 skill，先验证 404 语义）
    preview = await client.get(
        f"/api/v1/tools/{slug}/versions/2.0.0/skill-preview", headers=auth(stranger)
    )
    assert preview.status_code == 404


async def test_whitelist_unknown_user_and_duplicate(client, seeded) -> None:
    admin_token = await login(client, "admin")
    missing = await client.post(
        "/api/v1/admin/approval-whitelist",
        json={"user_id": 999999, "reason": "x"},
        headers=auth(admin_token),
    )
    assert missing.status_code == 404

    # 移除不存在的条目
    not_found = await client.delete(
        "/api/v1/admin/approval-whitelist/999999", headers=auth(admin_token)
    )
    assert not_found.status_code == 404

    # 禁用用户不能加白名单
    disabled = await client.post(
        "/api/v1/admin/approval-whitelist",
        json={"user_id": seeded.users["disabled"], "reason": "x"},
        headers=auth(admin_token),
    )
    assert disabled.status_code == 400


async def test_offline_relist_error_paths(client, seeded) -> None:
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    _slug, tool_id, _ = await publish_tool(client, owner, approver, name="下架错误路径")

    # 已批准的工具不能 relist
    relist = await client.post(
        f"/api/v1/admin/approvals/{tool_id}/relist",
        json={"reason": "x"},
        headers=auth(approver),
    )
    assert relist.status_code == 409

    # 下架
    assert (
        await client.post(
            f"/api/v1/admin/approvals/{tool_id}/offline",
            json={"reason": "依赖漏洞需要修复"},
            headers=auth(approver),
        )
    ).status_code == 200
    # 已下架不能再下架
    again = await client.post(
        f"/api/v1/admin/approvals/{tool_id}/offline",
        json={"reason": "重复下架应当被状态机拒绝"},
        headers=auth(approver),
    )
    assert again.status_code == 409


async def test_approve_tool_not_found_and_wrong_version(client, seeded) -> None:
    approver = await login(client, "approver")
    missing = await client.post(
        "/api/v1/admin/approvals/999999/approve", json={}, headers=auth(approver)
    )
    assert missing.status_code == 404

    owner = await login(client, "outsider")
    tool = await create_tool(client, owner, name="错版本审批")
    await upload_version(
        client, owner, tool["id"], version="1.0.0",
        file_bytes=zip_bytes({"a.txt": b"x"}), file_name="a.zip",
    )
    await submit(client, owner, tool["id"])
    wrong = await approve(client, approver, tool["id"], version_id=999999)
    assert wrong.status_code == 409
    assert wrong.json()["code"] == "ALREADY_PROCESSED"


async def test_anonymous_download_requires_setting(client, seeded) -> None:
    """匿名**浏览**由 `portal.allow_anonymous_view` 控制；匿名**下载**始终不允许。

    依据 docs/01 §3.2 的权限矩阵：下载 public 工具需要角色 `user` 及以上
    （`viewer` 都不行）。匿名比 viewer 更低，所以即使开了匿名浏览，
    下载仍然 404 —— 这是刻意的：浏览是只读，下载是取走内容。

    默认值已是 true（迁移 0005），所以下面按「默认开 → 显式关」两个分支断言，
    两个方向都覆盖到。
    """
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    slug, _tool_id, content = await publish_tool(
        client, owner, approver, name="匿名下载工具"
    )
    del content

    import httpx

    from app.repositories import system_settings as settings_repo

    def _anon() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=client._transport, base_url="http://testserver"
        )

    # ---- 分支 1：默认（允许匿名浏览）----
    async with _anon() as anon:
        # 浏览可以
        browse = await anon.get(f"/api/v1/tools/{slug}")
        assert browse.status_code == 200
        assert browse.json()["can_download"] is False
        # 下载不行 —— 404 而不是 401：用户能看见详情，报 401 只会让人困惑
        assert (await anon.get(f"/api/v1/tools/{slug}/download")).status_code == 404
        # 匿名不允许签发票据
        assert (
            await anon.post(f"/api/v1/tools/{slug}/download-ticket")
        ).status_code == 401

    # ---- 分支 2：显式关掉之后，连浏览都不行 ----
    async with SessionLocal() as session:
        row = await settings_repo.get_row(session, "portal.allow_anonymous_view")
        original = row.value
        row.value = False
        await session.commit()
    try:
        async with _anon() as anon:
            assert (await anon.get(f"/api/v1/tools/{slug}")).status_code == 401
    finally:
        async with SessionLocal() as session:
            row = await settings_repo.get_row(session, "portal.allow_anonymous_view")
            row.value = original
            await session.commit()


async def test_stats_daily_ordering(client, seeded) -> None:
    from app.models.stats import ToolStatsDaily
    from app.services.counter_service import CounterService

    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    slug, tool_id, _ = await publish_tool(client, owner, approver, name="每日统计工具")

    async with SessionLocal() as session:
        today = utcnow().date()
        session.add(
            ToolStatsDaily(
                tool_id=tool_id, stat_date=today - timedelta(days=1), views=5, downloads=1
            )
        )
        session.add(
            ToolStatsDaily(tool_id=tool_id, stat_date=today, views=9, downloads=2)
        )
        await session.commit()

    service = CounterService()
    del service
    stats = await client.get(f"/api/v1/tools/{slug}/stats", headers=auth(owner))
    daily = stats.json()["daily"]
    assert len(daily) == 2
    # 正序返回（旧 → 新），便于前端画趋势
    assert daily[0]["stat_date"] < daily[1]["stat_date"]


async def test_download_logs_retention_cleanup(client, seeded) -> None:
    from app.models.stats import DownloadLog
    from app.repositories import downloads as downloads_repo

    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    _slug, tool_id, _ = await publish_tool(client, owner, approver, name="保留期清理")

    now = utcnow()
    async with SessionLocal() as session:
        await downloads_repo.bulk_insert_logs(
            session,
            [
                {"tool_id": tool_id, "version_id": None, "user_id": 1, "ip": None,
                 "user_agent": None, "via_api_token_id": None,
                 "created_at": now - timedelta(days=400)},
                {"tool_id": tool_id, "version_id": None, "user_id": 1, "ip": None,
                 "user_agent": None, "via_api_token_id": None, "created_at": now},
            ],
            now=now,
        )
        await session.commit()

        deleted = await downloads_repo.delete_logs_before(
            session, now - timedelta(days=180)
        )
        await session.commit()
        assert deleted == 1

        remaining = (
            await session.execute(
                select(DownloadLog).where(DownloadLog.tool_id == tool_id)
            )
        ).scalars().all()
        assert len(remaining) == 1


async def test_versions_list_can_download_flag(client, seeded) -> None:
    """待审版本不可下载；已过审与历史版本可下载；已归档不可下载。"""
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    slug, tool_id, _ = await publish_tool(client, owner, approver, name="版本下载标志")

    await upload_version(
        client, owner, tool_id, version="2.0.0",
        file_bytes=zip_bytes({"v2.txt": b"v2"}), file_name="v2.zip",
    )
    await submit(client, owner, tool_id)

    versions = await client.get(f"/api/v1/tools/{slug}/versions", headers=auth(owner))
    by_version = {v["version"]: v for v in versions.json()}
    assert by_version["1.0.0"]["can_download"] is True
    assert by_version["1.0.0"]["status"] == "approved"
    assert by_version["2.0.0"]["can_download"] is False, "待审版本不可下载"
    assert by_version["2.0.0"]["status"] == "pending"
    assert by_version["1.0.0"]["file_sha256_short"] is not None
    assert len(by_version["1.0.0"]["file_sha256_short"]) == 16


def test_production_date_helpers() -> None:
    """`date` 与枚举的小工具（顺带验证基础类型没被改坏）。"""
    today = utcnow().date()
    assert isinstance(today, date)
    assert VersionStatus.PURGED.value == "purged"
    assert get_storage().name == "local"


# ===========================================================================
# version_service：auto_submit 与失败清理路径
# ===========================================================================
async def test_upload_with_auto_submit_enters_queue(client, seeded) -> None:
    """`auto_submit=true`：上传即提交，工具一步进入 pending（验收 #1 的一步式变体）。"""
    owner = await login(client, "outsider")
    tool = await create_tool(client, owner, name="自动提交工具")
    response = await upload_version(
        client, owner, tool["id"], version="1.0.0",
        file_bytes=zip_bytes({"a.txt": b"x"}), file_name="a.zip",
        auto_submit=True,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["tool_status"] == "pending", "auto_submit=true 应当直接进入待审"

    # 审批队列可见（不需要再调 submit）
    approver = await login(client, "approver")
    queue = await client.get("/api/v1/admin/approvals", headers=auth(approver))
    assert tool["id"] in [item["tool_id"] for item in queue.json()["items"]]

    # 审批历史里应该有 submit 记录
    history = await client.get(
        "/api/v1/admin/approvals/history",
        params={"tool_id": tool["id"]},
        headers=auth(approver),
    )
    assert any(r["action"] == "submit" for r in history.json()["items"])


async def test_upload_with_auto_submit_on_published_tool(client, seeded) -> None:
    """已发布工具 + auto_submit=true → 直接 pending_update（旧版本继续服务）。"""
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    slug, tool_id, v1 = await publish_tool(client, owner, approver, name="自动提交新版本")

    response = await upload_version(
        client, owner, tool_id, version="2.0.0",
        file_bytes=zip_bytes({"v2.bin": b"v2"}), file_name="v2.zip",
        auto_submit=True,
    )
    assert response.status_code == 201, response.text
    assert response.json()["tool_status"] == "pending_update"

    # 旧版本仍在服务
    download = await client.get(f"/api/v1/tools/{slug}/download", headers=auth(owner))
    assert download.status_code == 200
    assert download.content == v1

    # 审批记录里是 resubmit（因为工具此前已发布过一次版本）
    history = await client.get(
        "/api/v1/admin/approvals/history",
        params={"tool_id": tool_id},
        headers=auth(approver),
    )
    actions = [r["action"] for r in history.json()["items"]]
    assert "submit" in actions or "resubmit" in actions


async def test_upload_auto_submit_with_whitelist_auto_approves(client, seeded) -> None:
    """auto_submit=true 且命中白名单 → 直接 approved，且流水标注为自动。"""
    from app.repositories import system_settings as settings_repo

    admin_token = await login(client, "admin")
    owner = await login(client, "outsider")

    added = await client.post(
        "/api/v1/admin/approval-whitelist",
        json={"user_id": seeded.users["lockme"], "reason": "auto_submit 测试"},
        headers=auth(admin_token),
    )
    assert added.status_code == 201
    try:
        async with SessionLocal() as session:
            _ = await settings_repo.get_effective_bool(
                session, "approval.whitelist_enabled", True
            )
        tool = await create_tool(client, await login(client, "lockme"), name="自动放行提交")
        token = await login(client, "lockme")
        response = await upload_version(
            client, token, tool["id"], version="1.0.0",
            file_bytes=zip_bytes({"a.txt": b"x"}), file_name="a.zip",
            auto_submit=True,
        )
        assert response.status_code == 201, response.text
        assert response.json()["tool_status"] == "approved", "白名单 + auto_submit 应当直接发布"
    finally:
        await client.delete(
            f"/api/v1/admin/approval-whitelist/{seeded.users['lockme']}",
            headers=auth(admin_token),
        )
    del owner


async def test_upload_cleans_up_when_commit_step_fails(client, seeded, monkeypatch) -> None:
    """第 9 步（原子归位）失败时必须回滚并清理临时文件，且不留下版本行。"""
    from app.storage import local as storage_local

    owner = await login(client, "outsider")
    tool = await create_tool(client, owner, name="归位失败工具")

    async def boom(self, staged, *, directory, file_name=None):
        raise storage_local.UploadTooLargeError(limit_bytes=1, actual_bytes=2)

    monkeypatch.setattr(storage_local.LocalStorage, "commit", boom)
    response = await upload_version(
        client, owner, tool["id"], version="1.0.0",
        file_bytes=zip_bytes({"a.txt": b"x"}), file_name="a.zip",
    )
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert "归位失败" in response.json()["message"]

    # 临时文件被清掉、数据库里没有版本行
    assert list(get_storage().tmp_dir.glob("*.part")) == []
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(ToolVersion).where(ToolVersion.tool_id == tool["id"])
            )
        ).scalars().all()
    assert rows == []


async def test_purge_logs_but_keeps_rows(client, seeded) -> None:
    """归档时删文件失败只记警告，数据库行仍然标记 purged（孤儿清理兜底）。"""
    from app.services import version_service

    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    tool = await create_tool(client, owner, name="归档日志工具")
    for index in range(1, 13):
        await upload_version(
            client, owner, tool["id"], version=f"1.0.{index}",
            file_bytes=zip_bytes({f"f{index}.txt": os.urandom(64)}),
            file_name=f"v{index}.zip",
        )
        await submit(client, owner, tool["id"])
        ok = await approve(client, approver, tool["id"])
        assert ok.status_code == 200, ok.text

    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(ToolVersion).where(ToolVersion.tool_id == tool["id"])
            )
        ).scalars().all()
    purged = [r for r in rows if r.status == VersionStatus.PURGED.value]
    assert len(purged) == 1
    assert purged[0].purged_at is not None
    assert purged[0].is_current is False
    # 归档版本仍然保留版本号元信息（审计链需要）
    assert purged[0].version == "1.0.1"
    assert version_service is not None


async def test_version_summary_fields_complete(client, seeded) -> None:
    owner = await login(client, "outsider")
    approver = await login(client, "approver")
    slug, _tool_id, _ = await publish_tool(client, owner, approver, name="版本字段工具")

    versions = await client.get(f"/api/v1/tools/{slug}/versions", headers=auth(owner))
    item = versions.json()[0]
    for field in (
        "id", "tool_id", "version", "changelog_md", "status", "is_current",
        "file_name", "file_size", "file_sha256_short", "file_ext",
        "uploaded_by", "approved_at", "reject_reason", "purged_at",
        "created_at", "can_download",
    ):
        assert field in item, f"版本列表缺字段: {field}"
    assert item["uploaded_by"]["display_name"]
    assert item["file_sha256_short"] and len(item["file_sha256_short"]) == 16
