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
from app.storage import get_storage

cli = typer.Typer(
    add_completion=False,
    help="localcraft 后端命令行工具",
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
#: 只读访客（契约 §17.8）。与前端 mock 对齐 —— mock 比真实种子更完整是一种倒挂。
DEMO_VIEWER = ("viewer", "Viewer@12345", "只读访客")
#: 三个作者账号（契约 §14.6）。与前端 mock 对齐，也让「多作者」这一维度在种子里真实存在。
DEMO_AUTHORS: tuple[tuple[str, str, str], ...] = (
    ("zhangsan", "Author@12345", "张三"),
    ("lisi", "Author@12345", "李四"),
    ("wangwu", "Author@12345", "王五"),
)
#: 26 个工具的分配：前 8 个是边界子集（历史保留，含 2 个无封面、1 个超长名称），
#: 其余 18 个是普通工具。按顺序轮流分给 admin / 三个作者，保证「多作者」可见。
BOUNDARY_TOOL_COUNT = 8

DEMO_CATEGORIES: tuple[tuple[str, str, str, str, int], ...] = (
    ("dev-tools", "研发工具", "面向研发日常的工具与脚手架", "wrench", 10),
    ("ops-tools", "运维工具", "部署、巡检、备份相关工具", "server", 20),
    ("skills", "Skill", "Agent Skill 包", "puzzle", 30),
    ("prompts", "提示词", "可复用的 Prompt 模板", "message-square", 40),
)

# ---------------------------------------------------------------------------
# 契约 §14.6：种子扩到 26 个工具
# ---------------------------------------------------------------------------
# 目的：让 page_size 12 / 24 / 48 分别得到 3 / 2 / 1 页，分页在真实数据上可验证；
# 同时让「多作者、多分类、多类型」这三个维度都有足量样本。
# 这 18 个用**参数化生成**而不是手写 18 段字面量 —— 它们的差异只在类型/分类/作者，
# 手写会带来 500 行噪声，且改一个字段要改 18 处。
_EXTRA_TOOL_BLUEPRINT: tuple[tuple[str, str, str, str], ...] = (
    # (中文名, tool_type, category, 主要标签)
    ("接口压测台", "webapp", "dev-tools", "perf"),
    ("依赖漏洞扫描", "file", "dev-tools", "security"),
    ("SQL 慢查询分析", "file", "dev-tools", "database"),
    ("前端脚手架", "file", "dev-tools", "frontend"),
    ("代码规范检查器", "file", "dev-tools", "lint"),
    ("提交信息生成器", "prompt", "prompts", "git"),
    ("技术方案评审 Prompt", "prompt", "prompts", "review"),
    ("故障复盘模板", "prompt", "prompts", "postmortem"),
    ("周报汇总 Prompt", "prompt", "prompts", "report"),
    ("日志聚类巡检", "file", "ops-tools", "log"),
    ("证书到期巡检", "webapp", "ops-tools", "tls"),
    ("磁盘水位看板", "webapp", "ops-tools", "storage"),
    ("容器镜像瘦身", "file", "ops-tools", "docker"),
    ("变更窗口检查", "file", "ops-tools", "change"),
    ("需求拆解 Skill", "skill", "skills", "planning"),
    ("单元测试生成 Skill", "skill", "skills", "testing"),
    ("文档翻译 Skill", "skill", "skills", "i18n"),
    ("接口文档校对 Skill", "skill", "skills", "openapi"),
)


def _extra_tool_specs() -> tuple[dict[str, Any], ...]:
    """按蓝图生成 18 个普通工具定义（字段齐全，可直接交给播种逻辑）。"""
    specs: list[dict[str, Any]] = []
    for index, (name, tool_type, category, tag) in enumerate(_EXTRA_TOOL_BLUEPRINT, start=1):
        slug = f"demo-tool-{index:02d}"
        is_webapp = tool_type == "webapp"
        specs.append(
            {
                "slug": slug,
                "name": name,
                "summary": f"{name}：面向内网的{name}，开箱即用，含使用说明与示例。",
                "description_md": (
                    f"# {name}\n\n## 用途\n\n{name}。\n\n"
                    f"## 快速开始\n\n```bash\n{slug} --help\n```\n"
                ),
                "tool_type": tool_type,
                "category": category,
                "tags": [tag, "demo"],
                "cover": index % 5 != 0,  # 每 5 个留 1 个无封面，让占位色块也有样本
                "version": f"1.{index % 10}.0",
                "webapp_url": f"http://{slug}.intra.example.com" if is_webapp else None,
                "changelog": f"{name} 首次发布。",
            }
        )
    return tuple(specs)


DEMO_TOOLS_EXTRA: tuple[dict[str, Any], ...] = _extra_tool_specs()

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

#: 全部种子工具 = 8 个边界工具 + 18 个普通工具 = 26（契约 §14.6）
#: 放在 DEMO_TOOLS 之后，因为 DEMO_TOOLS_EXTRA 由蓝图生成、定义在前面。
ALL_DEMO_TOOLS: tuple[dict[str, Any], ...] = DEMO_TOOLS + DEMO_TOOLS_EXTRA


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
    extra_role_code: str | None = None,
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
    # J-11（contracts/CONTRACT.md §17.7）：允许一个种子账号同时拥有两个角色。
    # 目前只用于 wangwu（user + approver）—— 真实环境需要一个可登录的审批员，
    # 否则 approver 的角色菜单渲染与权限边界无法验证（用 admin 看不到
    # 「approver 不该看到某些菜单」这一行为），且与前端 mock 不一致。
    if extra_role_code is not None:
        extra = await _get_role(session, extra_role_code)
        session.add(UserRole(user_id=user.id, role_id=extra.id, granted_at=now))
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


def _build_seed_package(*, slug: str, version: str, tool_type: str) -> bytes:
    """生成一个**真实的** zip 占位包（契约 §17.9）。

    为什么必须真落盘：种子里 file/skill 工具的版本原先只有 `file_size` 没有
    `storage_path`/`sha256`，于是接口向用户与验收测试广告「可下载、有大小」，
    点下去必然失败 —— 这比诚实地说「不可下载」更有害。
    """
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        if tool_type == ToolType.SKILL.value:
            archive.writestr(
                "SKILL.md",
                f"---\nname: {slug}\nversion: {version}\n---\n"
                f"# {slug}\n\n演示用 Skill 包（seed-demo 生成）。\n",
            )
            archive.writestr("scripts/run.py", "print('demo skill')\n")
        else:
            archive.writestr(
                "README.md",
                f"# {slug}\n\n版本 {version}，由 seed-demo 生成的演示包。\n",
            )
            archive.writestr("bin/run.sh", "#!/bin/sh\necho demo\n")
    return buffer.getvalue()


#: 种子演示封面的色板 —— **浅中性（米白 / 浅灰），契约 §34.4**。
#:
#: 为什么不沿用 `((seed*53)%256, (seed*97)%256, (seed*193)%256)` 那套公式：
#: 它产出的是 (53,97,193) 深蓝紫、(106,194,130) 绿、(221,105,201) 亮粉这类**纯色**，
#: 而 26 个演示工具里有 21 个带封面 —— 用户在预览里看到的大色块主要就是它。
#: 演示数据不该这么花；真实用户上传的封面不受影响。
#:
#: 三个刻意的设计点：
#: 1. **浅中性**：五个档位全在同一族里（R≈G≈B，最大偏暖 7），不再有五颜六色。
#: 2. **带轻微差异**：相邻档位差约 7/255，同屏 21 张能看出深浅变化 ——
#:    完全同色会被误读成「图片加载失败」。
#: 3. **取中间调**：区间 200~228。种子图是**位图**，不随主题变，所以不能取纯白
#:    （深色主题下刺眼），也不能太暗（浅色卡片上就不是「浅色」了）。
#:    228 而非 255 是这一条的直接体现。
#:
#: 长度取 5 还有一层原因：缩略图用 `seed + 500` 生成，而 500 % 5 == 0，
#: 于是同一工具的封面与缩略图必然落在**同一档位**（缩略图不会跟封面串色）。
SEED_COVER_PALETTE: tuple[tuple[int, int, int], ...] = (
    (228, 226, 221),  # 米白（偏暖）
    (221, 220, 216),
    (214, 214, 211),
    (207, 208, 206),
    (200, 202, 201),  # 浅灰（偏冷）
)


def _build_seed_png(seed: int, *, width: int = 64, height: int = 36) -> bytes:
    """生成一张真实的小 PNG（浅中性纯色），用于种子封面。

    同样必须真落盘：`/api/v1/images/{id}` 会检查文件是否存在，
    只写数据库路径的话，所有种子封面都会 404 —— 而前端会把它当成破图。
    用 Pillow 生成，不引入新的二进制资源文件。

    颜色**不是**装饰，是图片管线的载荷：这几个文件的用途是打通
    落盘 → 缩略图 → 签名 URL 的整条链路，所以仍然生成真实 PNG，
    没有改成 base64、没有去掉落盘、也没有让多个工具复用同一个文件。
    """
    from PIL import Image

    color = SEED_COVER_PALETTE[seed % len(SEED_COVER_PALETTE)]
    image = Image.new("RGB", (width, height), color)
    import io

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


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
        # viewer（契约 §17.8）+ 三个作者（契约 §14.6）。
        # 与前端 mock 对齐：mock 比真实种子更完整会掩盖端到端差异。
        _viewer, viewer_created = await _upsert_user(
            session,
            username=DEMO_VIEWER[0],
            password=DEMO_VIEWER[1],
            display_name=DEMO_VIEWER[2],
            role_code=RoleCode.VIEWER.value,
            must_change_password=False,
            now=now,
        )
        author_objs: list[User] = []
        authors_created = 0
        for username, password, display_name in DEMO_AUTHORS:
            author, created = await _upsert_user(
                session,
                username=username,
                password=password,
                display_name=display_name,
                role_code=RoleCode.USER.value,
                must_change_password=False,
                now=now,
                # wangwu 额外拥有 approver（J-11）：真实环境需要一个可登录的审批员。
                extra_role_code=(
                    RoleCode.APPROVER.value if username == "wangwu" else None
                ),
            )
            author_objs.append(author)
            authors_created += int(created)
        # 26 个工具的归属：前 8 个边界工具归 admin，其余轮流给三个作者，
        # 这样「按作者筛选」在种子上就有真实样本。
        owner_pool: list[User] = [admin, *author_objs]

        # ---- 分类 ----
        categories: dict[str, Category] = {}
        for spec in DEMO_CATEGORIES:
            categories[spec[0]] = await _upsert_category(session, spec, now)

        # ---- 工具 ----
        created_tools = 0
        for index, spec in enumerate(ALL_DEMO_TOOLS):
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

            owner = owner_pool[0] if index < BOUNDARY_TOOL_COUNT else (
                owner_pool[1 + (index - BOUNDARY_TOOL_COUNT) % len(owner_pool[1:])]
                if len(owner_pool) > 1
                else owner_pool[0]
            )
            tool = Tool(
                slug=spec["slug"],
                name=spec["name"],
                summary=spec["summary"],
                description_md=spec["description_md"],
                tool_type=spec["tool_type"],
                visibility=ToolVisibility.PUBLIC.value,
                status=ToolStatus.APPROVED.value,
                owner_id=owner.id,
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

            # ---- 真实落盘（契约 §17.9）----
            # file / skill 必须有真实包；webapp / prompt 本就无文件，
            # 因此 file_size 一律为 None —— 不允许「有大小、可下载、但无文件」。
            has_package = spec["tool_type"] in (
                ToolType.FILE.value,
                ToolType.SKILL.value,
            )
            stored_path: str | None = None
            stored_size: int | None = None
            stored_sha: str | None = None
            stored_name: str | None = None
            if has_package:
                payload = _build_seed_package(
                    slug=spec["slug"], version=spec["version"], tool_type=spec["tool_type"]
                )
                stored = await get_storage().write_bytes_atomic(
                    payload,
                    directory=f"tools/{tool.id}",
                    file_name=f"{spec['slug']}-{spec['version']}.zip",
                )
                stored_path = stored.storage_path
                stored_size = stored.size
                stored_sha = stored.sha256
                stored_name = f"{spec['slug']}-{spec['version']}.zip"

            version = ToolVersion(
                tool_id=tool.id,
                version=spec["version"],
                changelog_md=spec["changelog"],
                status=VersionStatus.APPROVED.value,
                is_current=True,
                storage_path=stored_path,
                file_name=stored_name,
                file_size=stored_size,
                file_sha256=stored_sha,
                file_ext="zip" if has_package else None,
                mime_type="application/zip" if has_package else None,
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
                # 真实 PNG（原图 + 缩略图都落盘），否则 /api/v1/images/{id} 会 404
                cover_bytes = _build_seed_png(index, width=320, height=180)
                thumb_bytes = _build_seed_png(index + 500, width=160, height=90)
                cover_file = await get_storage().write_bytes_atomic(
                    cover_bytes, directory=f"images/{tool.id}", file_name="cover.png"
                )
                thumb_file = await get_storage().write_bytes_atomic(
                    thumb_bytes, directory=f"images/{tool.id}", file_name="cover.thumb.png"
                )
                image = ToolImage(
                    tool_id=tool.id,
                    version_id=version.id,
                    kind=ImageKind.COVER.value,
                    storage_path=cover_file.storage_path,
                    thumb_path=thumb_file.storage_path,
                    file_name="cover.png",
                    mime_type="image/png",
                    file_size=cover_file.size,
                    width=320,
                    height=180,
                    sha256=cover_file.sha256,
                    sort_order=0,
                    alt_text=f"{spec['name']} 封面",
                    uploaded_by_id=owner.id,
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
    typer.echo(
        f"        {DEMO_VIEWER[0]} / {DEMO_VIEWER[1]}（角色 viewer）"
        f"{'（新建）' if viewer_created else '（已存在，跳过）'}"
    )
    typer.echo("        wangwu / Author@12345（额外拥有 approver，可用于验证审批台）")
    for username, password, _dn in DEMO_AUTHORS:
        typer.echo(f"        {username} / {password}{'（新建）' if authors_created else ''}")
    typer.echo(f"  分类：{len(DEMO_CATEGORIES)} 个")
    typer.echo(
        f"  工具：{created_tools} 个新建（共 {len(ALL_DEMO_TOOLS)} 个定义，已存在的跳过）"
    )
    typer.echo(
        "  边界工具 8 个：含 2 个无封面（db-backup-toolkit-5e90、"
        "sql-optimize-prompt-4c02）与 1 个超长名称"
    )
    typer.echo("  file/skill 工具均已落盘真实占位包并写入 sha256（契约 §17.9）")


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

    M2 起工具写操作会实时同步索引；本命令用于**索引漂移后的修复**。

    **可用的方言**：SQLite（FTS5 虚表 `tool_search_index`）与
    PostgreSQL（`tools.search_vector` 列 + GIN 索引，M15 / 契约 §31）。

    **不可用时必须非 0 退出**（M10 的不变量，M15 把触发条件扩到两种）：

      - 后端是 LIKE 回退（`supports_reindex is False`）—— 没有索引可重建；
      - 或后端自称支持重建、但**列/虚表其实不存在**（迁移没跑到）——
        M15 起 PG 也会走到这里：它先乐观地允许进入重建，真正探测到
        `tools.search_vector` 缺失后降级，命令复查 `supports_reindex`
        发现已为 False，于是明确报错退出。

    为什么这一点必须钉住：M10 之前本命令在 PG 上「静默空转却报成功」——
    打印 `[ ok ] 已用 like_fallback 重建 0 条索引` 并退出 0，运维会以为
    索引已经建好。0 条与「重建成功但索引本来就是空的」在返回值上无法区分，
    所以只能靠这个能力位。
    """
    _run(_reindex_search())


def _no_reindex_message(backend_name: str) -> str:
    """「没有可重建索引」时的统一报错文案。"""
    return (
        f"当前检索后端（{backend_name}）没有可重建的全文索引，本次未做任何改动。\n"
        "        SQLite 之外的方言若仍是 LIKE 回退，检索直接查 tools 表，无需也无法重建索引；\n"
        "        PostgreSQL 上出现本提示，说明迁移 0009 没有跑到、tools.search_vector 列不存在。\n"
        "        请先执行 `alembic upgrade head`（会建列 + 建 GIN 索引 + 回填既有工具），再重试。"
    )


async def _reindex_search() -> None:
    backend = get_search_backend()
    if not backend.supports_reindex:
        await _dispose()
        # 明确报错（exit 1）而不是打印一行绿色 [ ok ]：
        # 「什么都没做」必须与「做了、但索引本来就是空的」区分开，
        # 否则运维拿不到任何信号（这是 M10 查实的真实缺陷）。
        _fail(_no_reindex_message(backend.name))

    failure: str | None = None
    count = 0
    async with SessionLocal() as session:
        count = await backend.reindex_all(session)
        if backend.supports_reindex:
            await session.commit()
        else:
            # M15：PG 后端的 supports_reindex 是**乐观初值**（同步属性拿不到
            # session，没法在构造时探测），真正的列存在性探测发生在
            # reindex_all() 内部。探测为否时后端降级、count 必然是 0 ——
            # 若继续走成功分支，就是 M10 那个「静默空转报成功」原样复发。
            await session.rollback()
            failure = _no_reindex_message(backend.name)
    await _dispose()
    if failure is not None:
        _fail(failure)
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
    from app.core.errors import LastSuperadminError
    from app.services import import_export_service

    content = csv_path.read_bytes()
    try:
        async with SessionLocal() as session:
            result = await import_export_service.import_users_csv(
                session, content=content, dry_run=dry_run, on_conflict=on_conflict
            )
    except LastSuperadminError as exc:
        # M14（契约 §29.6）：导入后活跃超管数为 0 → 服务层已整体回滚。
        # 这里给出**一句话结论 + 非零退出码**，而不是把 traceback 甩给运维。
        await _dispose()
        _fail(f"导入已整体回滚：{exc.message}")
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


@cli.command("maintenance")
def maintenance_cmd(
    task: str = typer.Option(
        "all",
        "--task",
        help=(
            "只跑某一项：all / download-logs / sessions / orphans / acl / "
            "tags / recycle-bin / gc-versions / webapp-health"
        ),
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="只统计不落库（回收站与版本淘汰本就跳过）"
    ),
    json_out: bool = typer.Option(False, "--json", help="以 JSON 输出汇总，便于脚本消费"),
) -> None:
    """一次性执行 docs/02 §5 的数据保留与清理任务。

    `scripts/run-maintenance.sh` 与 `localcraft-maintenance.timer` 调用的就是本命令。

    与 `purge-recycle-bin` / `gc-versions` 的关系：那两个是**单项**命令，
    本命令按 docs/02 §5 的顺序跑全套，并对单项失败做隔离（一项失败不影响其余）。
    """
    _run(_maintenance(task, dry_run, json_out))


async def _maintenance(task: str, dry_run: bool, json_out: bool) -> None:
    from app.core.config import settings
    from app.repositories import system_settings as settings_repo
    from app.services import maintenance_service, webapp_health_service

    files_root = settings.data_dir / "files"

    async with SessionLocal() as session:
        retention = await settings_repo.get_effective_int(
            session, "stats.download_log_retention_days", 180
        )
        history_limit = await settings_repo.get_effective_int(
            session, "version.history_limit", 10
        )

        if task == "all":
            result = await maintenance_service.run_all(
                session,
                files_root=files_root,
                download_log_retention_days=retention,
                recycle_bin_retention_days=30,
                version_history_limit=history_limit,
                dry_run=dry_run,
            )
            payload = result.as_dict()
            errors = list(result.errors)
        else:
            payload = {}
            errors = []
            try:
                if task == "download-logs":
                    payload["download_logs_purged"] = await maintenance_service.purge_download_logs(
                        session, retention_days=retention
                    )
                elif task == "sessions":
                    payload["sessions_purged"] = await maintenance_service.purge_sessions(session)
                elif task == "orphans":
                    trashed, deleted = await maintenance_service.cleanup_orphan_files(
                        session, files_root=files_root, dry_run=dry_run
                    )
                    payload["orphan_files_trashed"] = trashed
                    payload["trash_files_deleted"] = deleted
                elif task == "acl":
                    payload["dangling_acl_purged"] = await maintenance_service.cleanup_dangling_acl(
                        session
                    )
                elif task == "tags":
                    payload["tags_recounted"] = await maintenance_service.recompute_tag_counts(
                        session
                    )
                elif task == "recycle-bin":
                    payload["recycle_bin_purged"] = await maintenance_service.purge_recycle_bin(
                        session, retention_days=30
                    )
                elif task == "gc-versions":
                    payload["versions_gc"] = await maintenance_service.gc_versions(
                        session, keep=history_limit
                    )
                elif task == "webapp-health":
                    # M14（契约 §29.2）：在线工具探活。受设置项
                    # `webapp.health_check_enabled` 控制，关闭时直接跳过
                    # （`webapp_skipped=true`），不静默空转。
                    health = await webapp_health_service.run(session, dry_run=dry_run)
                    payload.update(health.as_dict())
                else:
                    _fail(
                        "未知任务："
                        f"{task}（可选 all/download-logs/sessions/orphans/acl/tags/"
                        "recycle-bin/gc-versions/webapp-health）"
                    )
            except Exception as exc:  # 单项失败也要给出清晰结论
                errors.append(f"{task}: {type(exc).__name__}: {exc}")

    await _dispose()

    if json_out:
        typer.echo(json.dumps({"result": payload, "errors": errors}, ensure_ascii=False))
    else:
        _ok("维护任务完成" + ("（dry-run）" if dry_run else ""))
        for key, value in payload.items():
            if isinstance(value, list):
                typer.echo(f"  {key}: {len(value)} 项 {value[:10]}")
            else:
                typer.echo(f"  {key}: {value}")
        for err in errors:
            typer.echo(f"  [失败] {err}", err=True)
    if errors:
        raise typer.Exit(code=1)


@cli.command("gc-versions")
def gc_versions_cmd(
    apply: bool = typer.Option(False, "--apply", help="真正执行；不加则只报告"),
) -> None:
    """清理超出 `version.history_limit` 的历史版本（由 `localcraft-maintenance.timer` 调度）。

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
