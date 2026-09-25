"""命令行入口：`python -m app.cli <command>`

docs/03 §6.1 建议用 Typer。命令清单（M1 部分）：

    create-superadmin   交互式创建超管（支持 --password-stdin，密码不进 shell 历史）
    reset-password      重置密码并置 must_change_password=true
    list-users          列出用户、角色、状态、存储用量
    seed-demo           按 contracts/CONTRACT.md §7 播种 M1 联调数据（幂等）
    export-openapi      导出 openapi.json（监控方做契约比对用）
    reindex-search      全量重建全文索引
    health              命令行方式检查数据库可读写

**注意**：CLI 不会自动执行迁移 —— 迁移必须在服务启动之前显式执行
（docs/02 §6.3）。
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import typer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import BASE_DIR, settings
from app.core.security import (
    hash_password,
    normalize_username,
    password_strength_errors,
)
from app.core.timeutil import utcnow
from app.db.session import SessionLocal, engine
from app.models.enums import (
    ImageKind,
    RoleCode,
    ToolStatus,
    ToolType,
    ToolVisibility,
    UserStatus,
    VersionStatus,
)
from app.models.taxonomy import Category, Tag
from app.models.tool import Tool, ToolImage, ToolTag, ToolVersion
from app.models.user import Role, User, UserRole
from app.repositories.tags import normalize_tag_name
from app.search import get_search_backend

cli = typer.Typer(
    add_completion=False,
    help="selftool 后端命令行工具",
    no_args_is_help=True,
)


# ---------------------------------------------------------------------------
# 公共辅助
# ---------------------------------------------------------------------------
def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _dispose() -> None:
    await engine.dispose()


def _fail(message: str, code: int = 1) -> None:
    typer.secho(f"[error] {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code)


def _ok(message: str) -> None:
    typer.secho(f"[ ok ] {message}", fg=typer.colors.GREEN)


def _info(message: str) -> None:
    typer.echo(f"[info] {message}")


def _read_password_from_stdin() -> str:
    """从标准输入读密码，支持两种格式：单行，或「密码\\n确认密码」两行。"""
    data = sys.stdin.read()
    lines = [line for line in data.splitlines() if line != ""]
    if not lines:
        _fail("标准输入为空，未读到密码")
    password = lines[0]
    if len(lines) > 1 and lines[1] != password:
        _fail("两次输入的密码不一致")
    return password


def _prompt_password(for_username: str) -> str:
    """交互式输入，不回显，不进入 shell 历史。"""
    while True:
        password = typer.prompt("Password", hide_input=True)
        if not password_strength_errors(password, for_username):
            confirm = typer.prompt("Confirm password", hide_input=True)
            if confirm != password:
                typer.secho("两次输入不一致，请重试", fg=typer.colors.YELLOW)
                continue
            return password
        for error in password_strength_errors(password, for_username):
            typer.secho(f"  - {error}", fg=typer.colors.YELLOW)
        typer.secho("密码强度不足，请重试", fg=typer.colors.YELLOW)


async def _get_role(session: AsyncSession, code: str) -> Role:
    result = await session.execute(select(Role).where(Role.code == code))
    role = result.scalar_one_or_none()
    if role is None:
        _fail(f"角色 {code} 不存在，请先执行 `alembic upgrade head`")
    return role


async def _get_user(session: AsyncSession, username: str) -> User:
    result = await session.execute(
        select(User).where(User.username == normalize_username(username))
    )
    user = result.scalar_one_or_none()
    if user is None:
        _fail(f"用户 {username} 不存在")
    return user


# ---------------------------------------------------------------------------
# create-superadmin
# ---------------------------------------------------------------------------
@cli.command("create-superadmin")
def create_superadmin(
    username: str = typer.Option(..., "--username", "-u", prompt="Username", help="登录名"),
    display_name: str | None = typer.Option(
        None, "--display-name", help="展示名，默认与登录名相同"
    ),
    email: str | None = typer.Option(None, "--email", help="邮箱（选填）"),
    password_stdin: bool = typer.Option(
        False,
        "--password-stdin",
        help="从标准输入读取密码（推荐用于无人值守初始化脚本）",
    ),
    force_change: bool = typer.Option(
        True,
        "--force-change/--no-force-change",
        help="是否强制首次登录改密（FR-AUTH-03 要求新账号为 true）",
    ),
) -> None:
    """创建超级管理员。

    **不要用 `--password '<明文>'` 传参** —— 密码会进入 shell 历史、`ps` 输出
    与审计日志（docs/05 §5.6）。本命令只支持交互式输入与 `--password-stdin`。
    """
    _run(
        _create_superadmin(
            username, display_name or username, email, password_stdin, force_change
        )
    )


async def _create_superadmin(
    username: str,
    display_name: str,
    email: str | None,
    password_stdin: bool,
    force_change: bool,
) -> None:
    normalized = normalize_username(username)
    if not normalized:
        _fail("用户名不能为空")

    password = _read_password_from_stdin() if password_stdin else _prompt_password(normalized)

    async with SessionLocal() as session:
        existing = await session.execute(select(User).where(User.username == normalized))
        if existing.scalar_one_or_none() is not None:
            await _dispose()
            _fail(f"用户 {normalized} 已存在")

        role = await _get_role(session, RoleCode.SUPERADMIN.value)
        now = utcnow()
        user = User(
            username=normalized,
            display_name=display_name,
            email=email,
            password_hash=hash_password(password),
            password_changed_at=now,
            must_change_password=force_change,
            status=UserStatus.ACTIVE.value,
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        await session.flush()
        session.add(UserRole(user_id=user.id, role_id=role.id, granted_at=now))
        await session.commit()
        user_id = user.id

    await _dispose()
    _ok(f"已创建超级管理员 {normalized} (id={user_id}, role=superadmin)")
    if force_change:
        _info("该账号首次登录后必须修改密码（must_change_password=true）")


# ---------------------------------------------------------------------------
# reset-password
# ---------------------------------------------------------------------------
@cli.command("reset-password")
def reset_password(
    username: str = typer.Argument(..., help="要重置密码的用户名"),
    password_stdin: bool = typer.Option(False, "--password-stdin", help="从标准输入读取新密码"),
    revoke_sessions: bool = typer.Option(
        True, "--revoke-sessions/--keep-sessions", help="是否同时吊销该用户全部会话"
    ),
) -> None:
    """重置密码并置 `must_change_password = true`（FR-IAM-03）。"""
    _run(_reset_password(username, password_stdin, revoke_sessions))


async def _reset_password(username: str, password_stdin: bool, revoke_sessions: bool) -> None:
    normalized = normalize_username(username)
    new_password = (
        _read_password_from_stdin() if password_stdin else _prompt_password(normalized)
    )

    from app.repositories import auth_sessions as sessions_repo

    async with SessionLocal() as session:
        user = await _get_user(session, normalized)
        now = utcnow()
        user.password_hash = hash_password(new_password)
        user.password_changed_at = now
        user.must_change_password = True
        user.failed_login_count = 0
        user.locked_until = None
        user.updated_at = now
        revoked = 0
        if revoke_sessions:
            revoked = await sessions_repo.revoke_all_for_user(session, user.id, now=now)
        await session.commit()
        user_id = user.id

    await _dispose()
    _ok(f"已重置 {normalized} (id={user_id}) 的密码，该用户下次登录必须改密")
    if revoke_sessions:
        _info(f"已吊销 {revoked} 个会话")


# ---------------------------------------------------------------------------
# list-users
# ---------------------------------------------------------------------------
@cli.command("list-users")
def list_users(
    status: str | None = typer.Option(None, "--status", help="按状态过滤：active / disabled"),
) -> None:
    """列出用户、角色、状态与存储用量。"""
    _run(_list_users(status))


async def _list_users(status: str | None) -> None:
    async with SessionLocal() as session:
        # 存储用量：该用户上传的版本文件字节数之和（M1 只做只读统计）
        usage_subq = (
            select(
                ToolVersion.uploaded_by_id.label("uid"),
                func.coalesce(func.sum(ToolVersion.file_size), 0).label("bytes"),
            )
            .group_by(ToolVersion.uploaded_by_id)
            .subquery()
        )
        stmt = (
            select(User, usage_subq.c.bytes)
            .outerjoin(usage_subq, usage_subq.c.uid == User.id)
            .order_by(User.id)
        )
        if status:
            stmt = stmt.where(User.status == status)

        result = await session.execute(stmt)
        rows = result.all()

        header = (
            f"{'ID':>4}  {'USERNAME':<20} {'DISPLAY NAME':<20} {'STATUS':<9} "
            f"{'ROLES':<22} {'STORAGE':>12}  MUST_CHANGE"
        )
        typer.echo(header)
        typer.echo("-" * len(header))
        for user, used in rows:
            roles = ",".join(user.role_codes) or "-"
            used_bytes = int(used or 0)
            typer.echo(
                f"{user.id:>4}  {user.username:<20} {user.display_name:<20} "
                f"{user.status:<9} {roles:<22} {_human_size(used_bytes):>12}  "
                f"{'yes' if user.must_change_password else 'no'}"
            )
        typer.echo(f"\n共 {len(rows)} 个用户")

    await _dispose()


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"  # pragma: no cover - 不可达


# ---------------------------------------------------------------------------
# seed-demo —— contracts/CONTRACT.md §7
# ---------------------------------------------------------------------------
#: 固定随机种子：保证每次执行的随机值完全一致（契约 §7）
SEED_RANDOM_SEED = 20250101

DEMO_ADMIN = ("admin", "Admin@12345", "管理员")
DEMO_NEWBIE = ("newbie", "Newbie@12345", "新人小张")

DEMO_CATEGORIES: tuple[tuple[str, str, str, str, int], ...] = (
    ("dev-tools", "研发工具", "面向研发日常的工具与脚手架", "wrench", 10),
    ("ops-tools", "运维工具", "部署、巡检、备份相关工具", "server", 20),
    ("skills", "Skill", "Agent Skill 包", "puzzle", 30),
    ("prompts", "提示词", "可复用的 Prompt 模板", "message-square", 40),
)

#: 8 个工具，覆盖全部 4 种类型、跨全部 4 个分类。
#: `cover=False` 的两个（db-backup-toolkit / sql-optimize-prompt）用于验证占位色块。
DEMO_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "slug": "log-analyzer-a3f2",
        "name": "日志分析器",
        "summary": (
            "一键分析 Nginx 与 Tomcat 日志，输出 Top 错误、耗时分布与异常时间线，支持自定义正则规则"
            "。"
        ),
        "description_md": (
            "# 日志分析器\n\n## 功能\n\n- 解析 Nginx / Tomcat / 应用自定义日志\n- 输出 Top N 错误与"
            "状态码分布\n- 按时"
            "间窗聚合请求耗时 P95 / P99\n\n## 用法\n\n```bash\nlog-analyzer --path /var/log/nginx/a"
            "ccess.log --top 20\n```"
        ),
        "tool_type": ToolType.FILE.value,
        "category": "dev-tools",
        "tags": ["python", "log", "ops"],
        "cover": True,
        "version": "1.4.2",
        "file_size": 4821043,
        "changelog": "新增 P99 耗时统计与自定义正则规则。",
    },
    {
        "slug": "api-mock-server-7b1c",
        "name": "API Mock 服务",
        "summary": (
            "按 OpenAPI 描述一键起 Mock 服务，支持延迟注入与错误码模拟，前端联调不再等后端。"
        ),
        "description_md": (
            "# API Mock 服务\n\n上传 OpenAPI 3.x 描述文件即可获得可调用的 Mock 接口。\n\n支持：\n- "
            "固定/随机响应\n- 延"
            "迟与错误率注入\n- 请求录制回放"
        ),
        "tool_type": ToolType.WEBAPP.value,
        "category": "dev-tools",
        "tags": ["node", "test", "openapi"],
        "cover": True,
        "version": "2.0.0",
        "file_size": None,
        "webapp_url": "http://mock.intra.example.com",
        "changelog": "支持 OpenAPI 3.1 与请求录制回放。",
    },
    {
        "slug": "db-backup-toolkit-5e90",
        "name": "数据库备份工具箱",
        "summary": (
            "PostgreSQL / MySQL 逻辑备份与校验脚本集，含一致性校验、留存策略与恢复演练模板。"
        ),
        "description_md": (
            "# 数据库备份工具箱\n\n## 包含\n\n- `backup.sh` 逻辑备份（pg_dump / mysqldump）\n- `ver"
            "ify.sh"
            "` 备份可用性校验\n- `restore.sh` 恢复演练\n"
            "- 留存策略：日备保留 7 份，周备保留 4 份\n\n"
            "> 所有脚本均支持 `--dry"
            "-run`。"
        ),
        "tool_type": ToolType.FILE.value,
        "category": "ops-tools",
        "tags": ["shell", "backup", "database"],
        "cover": False,  # 无封面：验证占位色块
        "version": "1.1.0",
        "file_size": 215487,
        "changelog": "新增 --dry-run 与恢复演练模板。",
    },
    {
        "slug": "k8s-inspect-2d47",
        "name": "Kubernetes 巡检",
        "summary": "集群健康巡检面板：节点资源水位、Pod 重启Top、证书过期提醒与事件聚合。",
        "description_md": (
            "# Kubernetes 巡检\n\n接入 kubeconfig 后自动巡检：\n\n1. 节点 CPU / 内存 / 磁盘水位\n2."
            " 异常重启 Po"
            "d Top N\n3. 证书与 Token 过期时间\n4. 近期 Warning 事件聚合"
        ),
        "tool_type": ToolType.WEBAPP.value,
        "category": "ops-tools",
        "tags": ["k8s", "ops", "monitor"],
        "cover": True,
        "version": "0.9.3",
        "file_size": None,
        "webapp_url": "http://k8s-inspect.intra.example.com",
        "changelog": "新增证书过期提醒。",
    },
    {
        "slug": "code-review-skill-9f31",
        "name": "代码评审助手",
        "summary": (
            "Agent Skill：按团队规约评审 diff，输出分级问题清单与修改建议，支持 Python / Java / Go"
            "。"
        ),
        "description_md": (
            "# 代码评审助手 Skill\n\n## 能力\n\n- 按团队编码规约评审 diff\n- 输出 blocker / major /"
            " minor 三级"
            "问题\n- 给出可直接采纳的修改建议\n\n## 使用\n\n在支持 Skill 的 Agent 中安装本包后，用 "
            "`@code-review` 触发。"
        ),
        "tool_type": ToolType.SKILL.value,
        "category": "skills",
        "tags": ["ai", "review", "skill"],
        "cover": True,
        "version": "1.0.0",
        "file_size": 34028,
        "changelog": "首个版本，支持三种语言。",
    },
    {
        "slug": "pdf-extract-skill-6a8b",
        "name": "PDF 内容提取",
        "summary": (
            "Agent Skill：从 PDF 提取正文、表格与图表标题，输出结构化 Markdown，保留章节层级。"
        ),
        "description_md": (
            "# PDF 内容提取 Skill\n\n解析 PDF 并输出结构化 Markdown：\n\n- 保留标题层级\n- 表格转 M"
            "arkdown tabl"
            "e\n- 图表标题独立成段\n\n对扫描件会明确提示「需 OCR」。"
        ),
        "tool_type": ToolType.SKILL.value,
        "category": "skills",
        "tags": ["ai", "pdf", "skill"],
        "cover": True,
        "version": "0.4.1",
        "file_size": 51200,
        "changelog": "改进表格识别准确率。",
    },
    {
        "slug": "sql-optimize-prompt-4c02",
        "name": "SQL 优化提示词",
        "summary": "给定慢 SQL 与表结构，输出执行计划解读、索引建议与改写后的等价 SQL。",
        "description_md": (
            "# SQL 优化提示词\n\n输入：慢 SQL + 表结构 DDL + 数据量级。\n\n输出：\n1. 执行计划逐行"
            "解读\n2. 索引建议（含理由与代"
            "价）\n3. 改写后的等价 SQL"
        ),
        "tool_type": ToolType.PROMPT.value,
        "category": "prompts",
        "tags": ["prompt", "sql", "performance"],
        "cover": False,  # 无封面：验证占位色块
        "version": "1.0.0",
        "file_size": None,
        "changelog": "首个版本。",
    },
    {
        # 超长名称（> 40 字符）+ 超过 3 行的简介：验证前端截断（契约 §7）
        "slug": "enterprise-kb-qa-prompt-8e5d",
        "name": (
            "企业内网知识库智能问答与多轮对话提示词模板（含引用溯源、拒答策略与安全边界约束）V2"
        ),
        "summary": (
            "面向企业内网知识库的问答提示词模板，覆盖引用溯源、追问澄清与拒答策略。\n"
            "第一段：要求模型仅依据检索片段作答，每条结论都要给出可点击的出处编号，"
            "不得引入片段之外的常识性补充，避免出现看似合理但无法溯源的内容。\n"
            "第二段：当检索片段不足以支撑结论时，模型必须明确说明「当前资料不足以回答」，"
            "并列出缺失的信息类别，而不是猜测或编造，同时给出一条可行的补充检索建议。\n"
            "第三段：多轮对话中需要维护上下文与已知事实，避免重复追问已经确认过的信息，"
            "并在每轮回答末尾标注本轮新增引用与失效引用。\n"
            "第四段：涉及安全边界与合规话题时，模板提供固定的拒绝话术与升级路径，"
            "确保输出不会越过企业内部的信息披露红线。"
        ),
        "description_md": (
            "# 企业内网知识库智能问答提示词模板 V2\n\n## 变更\n\nV2 在 V1 的基础上补充了「引用失效"
            "」标注与安全边界拒绝话术。\n\n## 适用"
            "场景\n\n- 内部文档问答\n- 制度与流程查询\n- 技术方案比对"
        ),
        "tool_type": ToolType.PROMPT.value,
        "category": "prompts",
        "tags": ["prompt", "rag", "writing"],
        "cover": True,
        "version": "2.0.0",
        "file_size": None,
        "changelog": "补充引用失效标注与安全边界拒绝话术。",
    },
)


@cli.command("seed-demo")
def seed_demo() -> None:
    """按 contracts/CONTRACT.md §7 播种 M1 联调数据。

    **幂等**：已存在的工具/分类/用户按 slug / username 跳过，可重复执行。
    随机值使用固定种子，保证每次执行结果完全一致。
    """
    _run(_seed_demo())


async def _upsert_user(
    session: AsyncSession,
    *,
    username: str,
    password: str,
    display_name: str,
    role_code: str,
    must_change_password: bool,
    now: datetime,
) -> tuple[User, bool]:
    normalized = normalize_username(username)
    result = await session.execute(select(User).where(User.username == normalized))
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing, False

    role = await _get_role(session, role_code)
    user = User(
        username=normalized,
        display_name=display_name,
        email=f"{normalized}@intra.example.com",
        password_hash=hash_password(password),
        password_changed_at=now,
        must_change_password=must_change_password,
        status=UserStatus.ACTIVE.value,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    await session.flush()
    session.add(UserRole(user_id=user.id, role_id=role.id, granted_at=now))
    return user, True


async def _upsert_category(
    session: AsyncSession, spec: tuple[str, str, str, str, int], now: datetime
) -> Category:
    slug, name, description, icon, sort_order = spec
    result = await session.execute(select(Category).where(Category.slug == slug))
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing
    category = Category(
        slug=slug,
        name=name,
        description=description,
        icon=icon,
        sort_order=sort_order,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    session.add(category)
    await session.flush()
    return category


async def _get_or_create_tag(
    session: AsyncSession, name: str, *, created_by_id: int, now: datetime
) -> Tag:
    normalized = normalize_tag_name(name)
    result = await session.execute(select(Tag).where(Tag.name == normalized))
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing
    tag = Tag(
        name=normalized,
        display_name=name,
        usage_count=0,
        created_by_id=created_by_id,
        created_at=now,
        updated_at=now,
    )
    session.add(tag)
    await session.flush()
    return tag


async def _seed_demo() -> None:
    rng = random.Random(SEED_RANDOM_SEED)
    now = utcnow()
    backend = get_search_backend()

    async with SessionLocal() as session:
        # ---- 用户 ----
        admin, admin_created = await _upsert_user(
            session,
            username=DEMO_ADMIN[0],
            password=DEMO_ADMIN[1],
            display_name=DEMO_ADMIN[2],
            role_code=RoleCode.SUPERADMIN.value,
            must_change_password=False,
            now=now,
        )
        _newbie, newbie_created = await _upsert_user(
            session,
            username=DEMO_NEWBIE[0],
            password=DEMO_NEWBIE[1],
            display_name=DEMO_NEWBIE[2],
            role_code=RoleCode.USER.value,
            must_change_password=True,
            now=now,
        )

        # ---- 分类 ----
        categories: dict[str, Category] = {}
        for spec in DEMO_CATEGORIES:
            categories[spec[0]] = await _upsert_category(session, spec, now)

        # ---- 工具 ----
        created_tools = 0
        for index, spec in enumerate(DEMO_TOOLS):
            result = await session.execute(select(Tool).where(Tool.slug == spec["slug"]))
            if result.scalar_one_or_none() is not None:
                continue

            # 固定种子 → 每次执行的随机值完全一致（契约 §7）
            download_count = rng.randint(5, 900)
            view_count = download_count * rng.randint(3, 12)
            published_at = now - timedelta(days=rng.randint(1, 240))

            tag_objs: list[Tag] = []
            for tag_name in spec["tags"]:
                tag_objs.append(
                    await _get_or_create_tag(
                        session, tag_name, created_by_id=admin.id, now=now
                    )
                )

            tool = Tool(
                slug=spec["slug"],
                name=spec["name"],
                summary=spec["summary"],
                description_md=spec["description_md"],
                tool_type=spec["tool_type"],
                visibility=ToolVisibility.PUBLIC.value,
                status=ToolStatus.APPROVED.value,
                owner_id=admin.id,
                category_id=categories[spec["category"]].id,
                webapp_url=spec.get("webapp_url"),
                download_count=download_count,
                view_count=view_count,
                published_at=published_at,
                last_version_at=published_at,
                version_seq=1,
                created_at=published_at,
                updated_at=published_at,
            )
            session.add(tool)
            await session.flush()

            version = ToolVersion(
                tool_id=tool.id,
                version=spec["version"],
                changelog_md=spec["changelog"],
                status=VersionStatus.APPROVED.value,
                is_current=True,
                file_name=(
                    None
                    if spec["tool_type"] == ToolType.WEBAPP.value
                    else f"{spec['slug']}-{spec['version']}.zip"
                ),
                file_size=spec.get("file_size"),
                file_ext=None if spec["tool_type"] == ToolType.WEBAPP.value else "zip",
                mime_type=None if spec["tool_type"] == ToolType.WEBAPP.value else "application/zip",
                prompt_content=(
                    spec["description_md"] if spec["tool_type"] == ToolType.PROMPT.value else None
                ),
                skill_readme_md=(
                    spec["description_md"] if spec["tool_type"] == ToolType.SKILL.value else None
                ),
                skill_manifest=(
                    {
                        "name": spec["slug"],
                        "version": spec["version"],
                        "description": spec["summary"][:80],
                    }
                    if spec["tool_type"] == ToolType.SKILL.value
                    else None
                ),
                skill_file_tree=(
                    [
                        {"path": "SKILL.md", "size": 2048, "is_dir": False, "sha256": "0" * 64},
                        {"path": "scripts", "size": 0, "is_dir": True, "sha256": None},
                        {
                            "path": "scripts/run.py",
                            "size": 1024,
                            "is_dir": False,
                            "sha256": "1" * 64,
                        },
                    ]
                    if spec["tool_type"] == ToolType.SKILL.value
                    else None
                ),
                uploaded_by_id=admin.id,
                approved_at=published_at,
                approved_by_id=None,
                created_at=published_at,
                updated_at=published_at,
            )
            session.add(version)
            await session.flush()

            tool.current_version_id = version.id

            for tag in tag_objs:
                session.add(ToolTag(tool_id=tool.id, tag_id=tag.id))
                tag.usage_count = (tag.usage_count or 0) + 1

            if spec["cover"]:
                image = ToolImage(
                    tool_id=tool.id,
                    version_id=version.id,
                    kind=ImageKind.COVER.value,
                    # 相对 DATA_DIR 的路径；M1 尚未实现 /api/v1/images/{id}，
                    # 详见 checkpoint 报告的「待裁决」条目
                    storage_path=f"files/images/{tool.id}/cover.png",
                    thumb_path=f"files/images/{tool.id}/cover.thumb.png",
                    file_name="cover.png",
                    mime_type="image/png",
                    file_size=48_000 + index * 1_000,
                    width=1280,
                    height=720,
                    sha256=f"{index:064d}",
                    sort_order=0,
                    alt_text=f"{spec['name']} 封面",
                    uploaded_by_id=admin.id,
                    created_at=published_at,
                )
                session.add(image)
                await session.flush()
                tool.cover_image_id = image.id

            # 全文索引与业务数据在**同一个事务**内写入（docs/02 §3.20）
            await backend.upsert(
                session,
                tool.id,
                _search_doc(spec),
            )
            created_tools += 1

        await session.commit()

    await _dispose()

    _ok("seed-demo 完成")
    typer.echo(
        f"  用户：{DEMO_ADMIN[0]} / {DEMO_ADMIN[1]}"
        f"{'（新建）' if admin_created else '（已存在，跳过）'}"
    )
    typer.echo(
        f"        {DEMO_NEWBIE[0]} / {DEMO_NEWBIE[1]}"
        f"{'（新建）' if newbie_created else '（已存在，跳过）'}"
    )
    typer.echo(f"  分类：{len(DEMO_CATEGORIES)} 个")
    typer.echo(f"  工具：{created_tools} 个新建（共 {len(DEMO_TOOLS)} 个定义，已存在的跳过）")
    typer.echo("  无封面工具：db-backup-toolkit-5e90、sql-optimize-prompt-4c02")


def _search_doc(spec: dict[str, Any]) -> Any:
    from app.search import SearchDocument

    return SearchDocument(
        name=spec["name"],
        summary=spec["summary"],
        description=spec["description_md"],
        tags=list(spec["tags"]),
    )


# ---------------------------------------------------------------------------
# export-openapi
# ---------------------------------------------------------------------------
@cli.command("export-openapi")
def export_openapi(
    output: Path = typer.Option(
        BASE_DIR / "openapi.json", "--output", "-o", help="输出路径"
    ),
) -> None:
    """导出 `openapi.json`。

    监控方在每个 checkpoint 靠它做「docs/03 ↔ 实现 ↔ web/src/api/types.ts」
    三方比对，所以这个命令必须能一键生成且**不依赖数据库**。
    """
    from app.main import app

    spec = app.openapi()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    paths = spec.get("paths", {})
    operations = sum(
        1
        for item in paths.values()
        for key in item
        if key in ("get", "post", "put", "patch", "delete")
    )
    _ok(f"已导出 {output}（{len(paths)} 个路径 / {operations} 个操作）")


# ---------------------------------------------------------------------------
# reindex-search
# ---------------------------------------------------------------------------
@cli.command("reindex-search")
def reindex_search() -> None:
    """全量重建全文索引（docs/02 §3.20）。

    M2 起工具写操作会实时同步索引；本命令用于**索引漂移后的修复**
    与切换到 PostgreSQL 后的重建。
    """
    _run(_reindex_search())


async def _reindex_search() -> None:
    backend = get_search_backend()
    async with SessionLocal() as session:
        count = await backend.reindex_all(session)
        await session.commit()
    await _dispose()
    _ok(f"已用 {backend.name} 重建 {count} 条索引")


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------
@cli.command("health")
def health() -> None:
    """命令行方式检查数据库可读写与 DATA_DIR 可写（排障用）。"""
    _run(_health())


async def _health() -> None:
    from sqlalchemy import text

    failures = 0

    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        _ok(f"数据库可读: {settings.database_url}")
    except Exception as exc:
        failures += 1
        typer.secho(f"[fail] 数据库不可用: {exc}", fg=typer.colors.RED)

    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        probe = settings.data_dir / ".cli-health-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        _ok(f"数据目录可写: {settings.data_dir}")
    except Exception as exc:
        failures += 1
        typer.secho(f"[fail] 数据目录不可写: {exc}", fg=typer.colors.RED)

    await _dispose()
    if failures:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# whoami（排障辅助，M1 附带）
# ---------------------------------------------------------------------------
@cli.command("gen-password")
def gen_password() -> None:
    """生成一个符合强度要求的随机初始口令（供管理员发放）。"""
    import secrets
    import string

    alphabet = string.ascii_letters + string.digits
    while True:
        candidate = "".join(secrets.choice(alphabet) for _ in range(16)) + "!A9"
        if not password_strength_errors(candidate):
            typer.echo(candidate)
            return


# ---------------------------------------------------------------------------
# M3 补全：批量导入导出 / Token 签发 / 清理任务
# ---------------------------------------------------------------------------
@cli.command("import-users")
def import_users_cmd(
    csv_path: Path = typer.Argument(..., exists=True, readable=True, help="CSV 文件路径"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只预演，不写库"),
    on_conflict: str = typer.Option("skip", "--on-conflict", help="skip / update / fail"),
) -> None:
    """离线批量导入用户（FR-API-14：运维在服务器上直接执行，无需依赖 HTTP）。"""
    _run(_import_users(csv_path, dry_run, on_conflict))


async def _import_users(csv_path: Path, dry_run: bool, on_conflict: str) -> None:
    from app.services import import_export_service

    content = csv_path.read_bytes()
    async with SessionLocal() as session:
        result = await import_export_service.import_users_csv(
            session, content=content, dry_run=dry_run, on_conflict=on_conflict
        )
    await _dispose()

    _ok(
        f"导入完成 dry_run={result.dry_run} 成功={result.succeeded} "
        f"失败={result.failed} 跳过={result.skipped}"
    )
    for item in result.errors[:50]:
        typer.echo(f"  第 {item.row} 行 {item.field or '-'}: {item.message}")
    if result.generated_passwords:
        # 明文只打印到这里，**不进日志**。
        typer.secho("  生成的初始密码（请立即转交用户，不会再次显示）:", fg=typer.colors.YELLOW)
        for generated in result.generated_passwords:
            typer.echo(f"    {generated.username} / {generated.password}")


@cli.command("export-users")
def export_users_cmd(
    csv_path: Path = typer.Argument(..., help="输出 CSV 路径"),
) -> None:
    """离线导出用户（带 UTF-8 BOM，Excel 打开中文不乱码）。"""
    _run(_export_users(csv_path))


async def _export_users(csv_path: Path) -> None:
    from app.services import import_export_service

    async with SessionLocal() as session:
        chunks = [chunk async for chunk in _aiter(session, import_export_service)]
    csv_path.write_text("".join(chunks), encoding="utf-8")
    await _dispose()
    _ok(f"已导出 {csv_path}（前 3 字节应为 UTF-8 BOM: {csv_path.read_bytes()[:3]!r}）")


async def _aiter(session: Any, module: Any) -> Any:
    for chunk in module.export_users_csv(session):
        yield chunk


@cli.command("create-token")
def create_token_cmd(
    name: str = typer.Option(..., "--name", help="Token 名称，如「CI 发布脚本」"),
    scopes: str = typer.Option(
        "tools:read", "--scopes", help="逗号分隔，如 tools:read,tools:write"
    ),
    username: str = typer.Option("admin", "--username", help="以哪个超管的身份签发"),
    note: str | None = typer.Option(None, "--note", help="备注"),
    expires_days: int = typer.Option(90, "--expires-days", help="有效天数，0 表示永不过期"),
) -> None:
    """离线签发 API Token（服务器上应急用）。

    明文只打印一次 —— 请立即复制保存。
    """
    _run(_create_token(name, scopes, username, note, expires_days))


async def _create_token(
    name: str, scopes: str, username: str, note: str | None, expires_days: int
) -> None:
    from datetime import timedelta

    from app.core.security import generate_api_token
    from app.repositories import api_tokens as tokens_repo

    scope_list = [s.strip() for s in scopes.split(",") if s.strip()]
    async with SessionLocal() as session:
        user = await _get_user(session, username)
        plaintext = generate_api_token()
        now = utcnow()
        row = await tokens_repo.create(
            session,
            name=name,
            plaintext=plaintext,
            scopes=scope_list,
            created_by_id=user.id,
            expires_at=(now + timedelta(days=expires_days)) if expires_days > 0 else None,
            now=now,
        )
        await session.commit()
        token_id, prefix = row.id, row.token_prefix

    await _dispose()
    _ok(f"已签发 Token id={token_id} prefix={prefix} scopes={scope_list}")
    typer.secho(f"\n  {plaintext}\n", fg=typer.colors.YELLOW, bold=True)
    typer.secho("  请立即复制保存 —— 此 Token 不会再次显示。", fg=typer.colors.YELLOW)


@cli.command("revoke-tokens")
def revoke_tokens_cmd(
    username: str = typer.Option(..., "--user", help="要吊销全部 Token 的用户"),
) -> None:
    """一键吊销某用户的全部 API Token（docs/05 §13.8 的轮换要求）。"""
    _run(_revoke_tokens(username))


async def _revoke_tokens(username: str) -> None:
    from app.repositories import api_tokens as tokens_repo

    async with SessionLocal() as session:
        user = await _get_user(session, username)
        count = await tokens_repo.revoke_all_for_user(
            session, user.id, revoked_by_id=user.id, now=utcnow()
        )
        await session.commit()
    await _dispose()
    _ok(f"已吊销 {username} 的 {count} 个 API Token")


@cli.command("purge-recycle-bin")
def purge_recycle_bin_cmd(
    days: int = typer.Option(30, "--days", help="软删除超过多少天的工具被彻底清除"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只列出，不删除"),
) -> None:
    """手动清理回收站（docs/02 §5 的每日任务也走同一段逻辑）。"""
    _run(_purge_recycle_bin(days, dry_run))


async def _purge_recycle_bin(days: int, dry_run: bool) -> None:
    from app.services import admin_tool_service

    async with SessionLocal() as session:
        if dry_run:
            from datetime import timedelta

            from sqlalchemy import select

            from app.models.tool import Tool

            cutoff = utcnow() - timedelta(days=days)
            result = await session.execute(
                select(Tool.id, Tool.slug)
                .where(Tool.deleted_at.is_not(None), Tool.deleted_at < cutoff)
                .order_by(Tool.deleted_at.asc())
            )
            rows = result.all()
            _ok(f"回收站中超过 {days} 天的工具有 {len(rows)} 个（dry-run，未删除）")
            for tool_id, slug in rows[:50]:
                typer.echo(f"  {tool_id}  {slug}")
        else:
            purged = await admin_tool_service.purge_expired_recycle_bin(
                session, older_than_days=days
            )
            _ok(f"已彻底清除 {len(purged)} 个工具（含磁盘文件）")


@cli.command("gc-versions")
def gc_versions_cmd(
    apply: bool = typer.Option(False, "--apply", help="真正执行；不加则只报告"),
) -> None:
    """清理超出 `version.history_limit` 的历史版本（docs/05 的 selftool-gc.timer 依赖它）。

    正常路径下淘汰在「审批通过」时同步触发；本命令是**兜底与修复**用
    （例如直接改过设置、或历史上有失败的删除）。
    """
    _run(_gc_versions(apply))


async def _gc_versions(apply: bool) -> None:
    from sqlalchemy import select

    from app.models.tool import Tool
    from app.repositories import system_settings as settings_repo
    from app.repositories import tool_versions as versions_repo
    from app.storage import get_storage

    storage = get_storage()
    total = 0
    async with SessionLocal() as session:
        keep = await settings_repo.get_effective_int(session, "version.history_limit", 10)
        tool_ids = [
            int(r[0])
            for r in (await session.execute(select(Tool.id))).all()
        ]
        for tool_id in tool_ids:
            candidates = await versions_repo.list_purge_candidates(
                session, tool_id, keep=keep
            )
            for version in candidates:
                total += 1
                if apply:
                    path = version.storage_path
                    await versions_repo.mark_purged(session, version, now=utcnow())
                    await session.flush()
                    if path:
                        await storage.delete(path)
            if apply:
                await session.commit()
        if not apply:
            await session.rollback()
    await _dispose()
    verb = "已归档" if apply else "待归档（未执行，加 --apply 生效）"
    _ok(f"{verb} {total} 个历史版本（保留上限 {keep}）")


if __name__ == "__main__":
    cli()
