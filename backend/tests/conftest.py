"""pytest 全局夹具。

关键点：**必须在导入任何 app 模块之前设置环境变量** ——
`app.core.config.settings` 与 `app.db.session.engine` 都是模块级单例，
导入即固化数据库 URL。因此本文件顶部无条件改环境变量。
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 1) 先把环境变量定死，再导入 app
# ---------------------------------------------------------------------------
TMP_ROOT = Path(tempfile.mkdtemp(prefix="localcraft-tests-"))
TEST_DB = TMP_ROOT / "test.db"

# 默认用一次性 SQLite 文件库；但若外部（如 M4 的 PG 迁移演练）已经指定了
# LOCALCRAFT_TEST_DATABASE_URL，就用它 —— 这样同一套测试可以在 PostgreSQL 上跑，
# 用来暴露 SQLite 专属语法。用独立变量名是为了避免误把生产的 DATABASE_URL 带进来。
if os.environ.get("LOCALCRAFT_TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["LOCALCRAFT_TEST_DATABASE_URL"]
else:
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB}"
os.environ["DATA_DIR"] = str(TMP_ROOT / "data")
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["COOKIE_SECURE"] = "false"
os.environ["API_DOCS_ENABLED"] = "true"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["ACCESS_TOKEN_MINUTES"] = "30"
os.environ["REFRESH_TOKEN_DAYS"] = "7"
# M7 / O12：测试里启用每请求 SQL 条数计数。
# 用一个**显式开关**而不是复用 LOCALCRAFT_DEBUG，是因为 debug 会顺带打开
# 别的行为（比如把异常类名写进 500 响应体），测试不该被那些牵连。
# 它同时决定了「SQLAlchemy 事件监听器挂不挂」，所以必须在 import app 之前设好。
os.environ["LOCALCRAFT_SQL_COUNT"] = "1"

BACKEND_DIR = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# 2) 导入 app
# ---------------------------------------------------------------------------
import httpx  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.core.timeutil import utcnow  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.enums import (  # noqa: E402
    AclSubjectType,
    ImageKind,
    RoleCode,
    ToolStatus,
    ToolType,
    ToolVisibility,
    UserStatus,
    VersionStatus,
)
from app.models.taxonomy import Category, Tag  # noqa: E402
from app.models.tool import Tool, ToolAcl, ToolImage, ToolTag, ToolVersion  # noqa: E402
from app.models.user import Group, GroupMember, Role, User, UserRole  # noqa: E402
from app.search import SearchDocument, get_search_backend  # noqa: E402


# ---------------------------------------------------------------------------
# 3) Schema：跑真实的 Alembic 迁移（顺带验证三个迁移可用）
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def _migrated_schema() -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(cfg, "head")


# ---------------------------------------------------------------------------
# 4) 测试数据
# ---------------------------------------------------------------------------
PASSWORDS = {
    "admin": "Admin@12345",
    "newbie": "Newbie@12345",
    "approver": "Approver@12345",
    "viewer": "Viewer@12345",
    "disabled": "Disabled@12345",
    "lockme": "Lockme@12345",
    "outsider": "Outsider@12345",
}


class Seeded:
    """播种好的固定 ID / slug，供各测试引用。"""

    def __init__(self) -> None:
        self.users: dict[str, int] = {}
        self.categories: dict[str, int] = {}
        self.tools: dict[str, int] = {}
        self.group_id: int = 0
        self.public_slugs: list[str] = []
        self.all_slugs: list[str] = []


@pytest.fixture(scope="session", autouse=True)
def seeded(_migrated_schema) -> Seeded:
    """播种测试数据。

    **autouse**：不这样做的话，只在签名里写了 `client` 而没写 `seeded` 的测试
    会跑在空库上，得到「total = 0」这种看起来像功能缺陷实则是夹具没跑的结果。
    """
    import asyncio

    return asyncio.run(_seed())


async def _seed() -> Seeded:
    data = Seeded()
    now = utcnow()

    async with SessionLocal() as session:
        role_ids: dict[str, int] = {}
        for code in (
            RoleCode.SUPERADMIN,
            RoleCode.APPROVER,
            RoleCode.USER,
            RoleCode.VIEWER,
        ):
            from sqlalchemy import select

            result = await session.execute(select(Role).where(Role.code == code.value))
            role_ids[code.value] = result.scalar_one().id

        # ---- 用户 ----
        user_specs = [
            ("admin", RoleCode.SUPERADMIN.value, UserStatus.ACTIVE, False),
            ("newbie", RoleCode.USER.value, UserStatus.ACTIVE, True),
            ("approver", RoleCode.APPROVER.value, UserStatus.ACTIVE, False),
            ("viewer", RoleCode.VIEWER.value, UserStatus.ACTIVE, False),
            ("disabled", RoleCode.USER.value, UserStatus.DISABLED, False),
            ("lockme", RoleCode.USER.value, UserStatus.ACTIVE, False),
            ("outsider", RoleCode.USER.value, UserStatus.ACTIVE, False),
        ]
        for username, role_code, status, must_change in user_specs:
            user = User(
                username=username,
                display_name=f"{username} 展示名",
                email=f"{username}@example.com",
                password_hash=hash_password(PASSWORDS[username]),
                password_changed_at=now,
                must_change_password=must_change,
                status=status.value,
                created_at=now,
                updated_at=now,
            )
            session.add(user)
            await session.flush()
            session.add(UserRole(user_id=user.id, role_id=role_ids[role_code], granted_at=now))
            data.users[username] = user.id

        # ---- 用户组（restricted 走组授权的场景）----
        group = Group(name="测试组", description="用于可见性测试", created_at=now, updated_at=now)
        session.add(group)
        await session.flush()
        data.group_id = group.id
        session.add(
            GroupMember(
                group_id=group.id, user_id=data.users["approver"], added_at=now
            )
        )

        # ---- 分类 ----
        for index, (slug, name) in enumerate(
            [("cat-a", "分类甲"), ("cat-b", "分类乙")], start=1
        ):
            category = Category(
                slug=slug,
                name=name,
                sort_order=index * 10,
                is_active=index == 1,  # cat-b 停用，用于验证 is_active 过滤
                created_at=now,
                updated_at=now,
            )
            session.add(category)
            await session.flush()
            data.categories[slug] = category.id

        # ---- 标签 ----
        tag_objs: list[Tag] = []
        for name in ("alpha", "beta"):
            tag = Tag(name=name, display_name=name.upper(), created_at=now, updated_at=now)
            session.add(tag)
            await session.flush()
            tag_objs.append(tag)

        # ---- 工具 ----
        tool_specs = [
            # (slug, visibility, status, owner, category, acl_subject)
            ("public-approved", ToolVisibility.PUBLIC, ToolStatus.APPROVED, "admin", "cat-a", None),
            ("public-pending", ToolVisibility.PUBLIC, ToolStatus.PENDING, "admin", "cat-a", None),
            ("public-draft", ToolVisibility.PUBLIC, ToolStatus.DRAFT, "admin", "cat-a", None),
            ("private-owned", ToolVisibility.PRIVATE, ToolStatus.APPROVED, "outsider", "cat-a", None),
            ("restricted-user", ToolVisibility.RESTRICTED, ToolStatus.APPROVED, "admin", "cat-a", "user:outsider"),
            ("restricted-group", ToolVisibility.RESTRICTED, ToolStatus.APPROVED, "admin", "cat-a", "group"),
            ("restricted-none", ToolVisibility.RESTRICTED, ToolStatus.APPROVED, "admin", "cat-a", None),
            ("deleted-public", ToolVisibility.PUBLIC, ToolStatus.APPROVED, "admin", "cat-a", "deleted"),
            # 第二个 approved public，用于分页边界测试
            ("public-approved-2", ToolVisibility.PUBLIC, ToolStatus.APPROVED, "admin", "cat-a", None),
        ]

        for index, (slug, visibility, status, owner, category, acl) in enumerate(tool_specs):
            published = now - timedelta(days=index)
            tool = Tool(
                slug=slug,
                name=f"工具 {slug}",
                summary=f"简介 {slug}",
                description_md=f"# {slug}\n\n正文内容",
                tool_type=(
                    ToolType.FILE.value if index % 2 == 0 else ToolType.PROMPT.value
                ),
                visibility=visibility.value,
                status=status.value,
                owner_id=data.users[owner],
                category_id=data.categories[category],
                download_count=(len(tool_specs) - index) * 10,
                view_count=(len(tool_specs) - index) * 100,
                published_at=published if status == ToolStatus.APPROVED else None,
                deleted_at=now if acl == "deleted" else None,
                version_seq=1,
                created_at=published,
                updated_at=published,
            )
            session.add(tool)
            await session.flush()

            version = ToolVersion(
                tool_id=tool.id,
                version="1.0.0",
                changelog_md="首个版本",
                status=VersionStatus.APPROVED.value,
                is_current=True,
                file_name=f"{slug}.zip",
                file_size=1000 + index,
                file_ext="zip",
                uploaded_by_id=data.users[owner],
                approved_at=published,
                created_at=published,
                updated_at=published,
            )
            session.add(version)
            await session.flush()
            tool.current_version_id = version.id

            if index < 2:
                session.add(ToolTag(tool_id=tool.id, tag_id=tag_objs[index].id))

            if acl == "user:outsider":
                session.add(
                    ToolAcl(
                        tool_id=tool.id,
                        subject_type=AclSubjectType.USER.value,
                        subject_id=data.users["outsider"],
                        created_at=now,
                    )
                )
            elif acl == "group":
                session.add(
                    ToolAcl(
                        tool_id=tool.id,
                        subject_type=AclSubjectType.GROUP.value,
                        subject_id=group.id,
                        created_at=now,
                    )
                )

            # 全文索引必须与业务数据同事务写入，否则搜索测试会因为
            # 「索引可用但没有条目」而返回空集
            await get_search_backend().upsert(
                session,
                tool.id,
                SearchDocument(
                    name=tool.name,
                    summary=tool.summary,
                    description=tool.description_md,
                    tags=[],
                ),
            )

            data.tools[slug] = tool.id

        # ---- 一张封面图，用于验证 cover_url ----
        cover_tool = data.tools["public-approved"]
        # 封面必须是**真文件**：`/api/v1/images/{id}` 会检查磁盘上有没有它，
        # 只写数据库路径的话，所有带封面的断言都会 404（M5 发现并修正）。
        import io as _io

        from PIL import Image as _Image

        from app.storage import get_storage as _get_storage

        def _png(color: tuple[int, int, int], size: tuple[int, int]) -> bytes:
            buf = _io.BytesIO()
            _Image.new("RGB", size, color).save(buf, format="PNG")
            return buf.getvalue()

        _storage = _get_storage()
        _cover = await _storage.write_bytes_atomic(
            _png((40, 90, 160), (320, 180)), directory="images/1", file_name="cover.png"
        )
        _thumb = await _storage.write_bytes_atomic(
            _png((90, 140, 200), (160, 90)), directory="images/1", file_name="cover.thumb.png"
        )
        image = ToolImage(
            tool_id=cover_tool,
            kind=ImageKind.COVER.value,
            storage_path=_cover.storage_path,
            thumb_path=_thumb.storage_path,
            file_name="cover.png",
            mime_type="image/png",
            file_size=_cover.size,
            sha256=_cover.sha256,
            uploaded_by_id=data.users["admin"],
            created_at=now,
        )
        session.add(image)
        await session.flush()
        tool_obj = await session.get(Tool, cover_tool)
        assert tool_obj is not None
        tool_obj.cover_image_id = image.id

        await session.commit()

    # 「门户可见」的公共工具：approved 且未删除
    data.public_slugs = ["public-approved", "public-approved-2", "restricted-none"]
    data.all_slugs = [spec[0] for spec in tool_specs]
    return data


# ---------------------------------------------------------------------------
# 5) HTTP 客户端
# ---------------------------------------------------------------------------
@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """匿名客户端。每个测试一个实例 → Cookie jar 互相隔离。"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as s:
        yield s


async def login(client: httpx.AsyncClient, username: str, password: str | None = None) -> str:
    """登录并返回 access token（Cookie 由 client 自动保存）。"""
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password or PASSWORDS[username]},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
async def _reset_engine_pool() -> AsyncIterator[None]:
    """每个用例结束后清空连接池。

    为什么必须做：pytest-asyncio 默认**每个测试一个新的事件循环**，而
    `app.db.session.engine` 是模块级单例，连接池跨用例共享。aiosqlite 的每个
    连接有一个工作线程，`future.get_loop()` 指向**创建它的那个 loop**；上个用例
    结束后 loop 关闭，连接还回池里，下个用例拿到它再发查询时，工作线程会往一个
    已关闭的 loop 上 `call_soon_threadsafe`，抛 `RuntimeError: Event loop is closed`。

    这个异常**不是** DBAPI 错误，所以 `pool_pre_ping` 不会把它当作连接失效信号，
    它会直接冒到请求路径上（M3 的 100 并发用例表现为 500 / "No response returned."，
    且只在整包运行时复现 —— 单跑该文件时池里没有跨 loop 的陈旧连接）。

    放在测试侧而不是给 SQLite 换 NullPool：生产运行时是单 loop 常驻进程，
    连接复用本身没问题，不该为了测试改运行时行为。
    """
    yield
    if engine.dialect.name == "sqlite":
        await engine.dispose()
