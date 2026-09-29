#!/usr/bin/env python
"""M15 §31.5②：方言一致性实测 —— 同一批工具 + 同一个查询，SQLite 与 PG 的 id 集合必须相同。

**为什么是独立脚本而不是 pytest 用例**：一个 pytest 进程只连一种方言
（`tests/conftest.py` 用 `LOCALCRAFT_TEST_DATABASE_URL` 二选一），所以
「两个方言给出同一答案」这件事**没法在单个进程里断言**。这里改成：
同一份脚本跑两次（各一种方言），各输出一份 JSON，由
`scripts/m15-dialect-consistency.sh` 逐查询比对。

语料与查询都写死在本文件里 —— 两趟必须构造出**一模一样**的工具，
否则比出来的差异说明不了任何事。

用法（一般由 shell 包装调用，不直接跑）：

    LOCALCRAFT_DIALECT_DB="sqlite+aiosqlite:////tmp/dc.db" \
        backend/.venv/bin/python scripts/m15-dialect-consistency.py --out /tmp/sqlite.json

输出：`--out` 指定的 JSON 文件（含 slug→id 与 每个查询命中的 id/slug 集合）。
**不写 stdout** —— alembic 的日志与迁移里的进度打印都在 stdout 上，
混进去会破坏 JSON。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"

DB_URL = os.environ.get("LOCALCRAFT_DIALECT_DB")
if not DB_URL:
    print("必须通过 LOCALCRAFT_DIALECT_DB 指定数据库 URL", file=sys.stderr)
    raise SystemExit(2)

# ---- 环境变量必须在 import app 之前定死（settings 是模块级单例）----
os.environ["DATABASE_URL"] = DB_URL
_tmp = Path(tempfile.mkdtemp(prefix="m15-dc-"))
os.environ["DATA_DIR"] = str(_tmp / "data")
os.environ["SECRET_KEY"] = "m15-dialect-consistency-not-for-production"
os.environ["COOKIE_SECURE"] = "false"
os.environ["LOG_LEVEL"] = "WARNING"

sys.path.insert(0, str(BACKEND_DIR))

import asyncio  # noqa: E402

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

# ---------------------------------------------------------------------------
# 语料：工具（name / summary / description / tags 四路都要被检索到）
# ---------------------------------------------------------------------------
CORPUS: tuple[dict[str, object], ...] = (
    {
        "slug": "cjk-name",
        "name": "内网工具平台",
        "summary": "企业内网的工具聚合",
        "description": "# 内网工具\n\n把内网里散落的工具聚到一起。",
        "tags": ["内网", "平台"],
    },
    {
        "slug": "en-name",
        "name": "Internal Tool Platform",
        "summary": "A platform for internal tools",
        "description": "Deploy internal tools easily.",
        "tags": ["platform"],
    },
    {
        "slug": "mixed",
        "name": "Kubernetes 集群管理工具",
        "summary": "K8s 集群运维",
        "description": "用来管理 Kubernetes 集群。",
        "tags": ["运维", "k8s"],
    },
    {
        "slug": "versioned",
        "name": "Data Sync 2.0",
        "summary": "数据同步组件 v2.0",
        "description": "在 MySQL 与 PostgreSQL 之间同步数据。",
        "tags": ["sync"],
    },
    {
        "slug": "cjk-desc-only",
        "name": "Report Hub",
        "summary": "Reporting",
        "description": "支持导出 Excel 报表，中文描述在这里。",
        "tags": ["report"],
    },
    {
        "slug": "en-desc-only",
        "name": "报表中心",
        "summary": "报表",
        "description": "Export reports to Excel and CSV files.",
        "tags": ["报表"],
    },
    {
        "slug": "tag-only",
        "name": "Alpha Service",
        "summary": "无关键词",
        "description": "没有任何相关词。",
        "tags": ["日志分析"],
    },
    {
        "slug": "code",
        "name": "Code Helper",
        "summary": "代码辅助",
        "description": "支持 C++ / C# / node.js / scikit-learn。",
        "tags": ["code"],
    },
    {
        "slug": "numbers",
        "name": "工具 2024",
        "summary": "版本 2024 发布",
        "description": "编号 12345678。",
        "tags": ["2024"],
    },
    {
        "slug": "tools-words",
        "name": "tools",
        "summary": "tool",
        "description": "tooling tools tool",
        "tags": ["tools"],
    },
    {
        "slug": "long-desc",
        "name": "长描述工具",
        "summary": "很长的描述",
        "description": "日志分析 " * 5000,
        "tags": ["长描述"],
    },
    {
        "slug": "admin-console",
        "name": "后台管理控制台",
        "summary": "管理后台",
        "description": "用户、角色、系统设置的统一入口。",
        "tags": ["管理", "后台"],
    },
)

#: 查询集合：中文单/多字、英文整词、大小写、混合、数字版本号、标签、AND 语义、不存在的词。
QUERIES: tuple[str, ...] = (
    # 中文
    "内网",
    "工具",
    "工具平台",
    "内",
    "平台",
    "集群",
    "管理",
    "报表",
    "日志",
    "分析",
    "日志分析",
    "长描述",
    "后台",
    # 英文
    "internal",
    "Internal TOOL",
    "kubernetes",
    "platform",
    "report",
    "excel",
    "csv",
    "code",
    "tool",
    "tools",
    "tooling",
    "sync",
    # 中英混合
    "Kubernetes 集群",
    "内网 工具",
    "Data Sync",
    "k8s 运维",
    # 数字 / 版本号
    "2.0",
    "v2.0",
    "2024",
    "12345678",
    # 符号
    "c++",
    "node.js",
    # AND 语义：两个词必须同时出现在同一个工具里
    "内网 平台",
    "工具 平台",
    "报表 excel",
    # 必然不命中
    "nonexistent-zzz-123",
)


#: **已知会在两种方言间产生差异**的语料（M15 实测，见交付报告）。
#:
#: 单独列出来的原因：契约 §31.5② 要求「同一查询在两个方言上返回同一 id 集合」，
#: 代表性语料做到了 0 差异；但这**两个字符层面的边界**做不到，而且**不是**本实现
#: 的疏忽 —— 它们是两套分词器的固有差别（SQLite 的 `unicode61 remove_diacritics 2`
#: 会去变音符，PG 的 `simple` 不会；PG 会丢弃超过 2047 字符的超长 token）。
#: 把它们混进主比对会让比对永远失败，从而**失去回归价值**；所以单独测量、
#: 单独报告，主比对保持严格。
KNOWN_DIVERGENCE_CORPUS: tuple[dict[str, object], ...] = (
    {
        "slug": "accented",
        "name": "Café 管理控制台",
        "summary": "Crème brûlée",
        "description": "Résumé of naïve façade.",
        "tags": ["café"],
    },
    {
        # 单个 3000 字符的 token（无分隔符），放在 description（`Text`，不设上限）
        # 而不是 name（`varchar(128)`）—— PG 会直接丢弃它并打 NOTICE：
        # `word is too long to be indexed (Words longer than 2047 characters are ignored)`。
        "slug": "long-token",
        "name": "超长 token 探针",
        "summary": "不可分词的超长标识符",
        "description": "A" * 3000,
        "tags": ["longtoken"],
    },
    {
        # 带 `.` / `-` 的标识符：FTS5 的 unicode61 把 `.` `-` 当**分隔符**
        # （node.js → node + js），PG 的默认 parser 把它们**留在词里**
        # （node.js → 单个 lexeme 'node.js'）。于是「搜 node」在 SQLite 命中、
        # 在 PG 不命中。这是两套分词器的固有差别，M15 记录在案。
        "slug": "dotted",
        "name": "node.js 工具",
        "summary": "v1.2.3 / scikit-learn / a-b-c",
        "description": "点号、连字符标识符探针。",
        "tags": ["dotted"],
    },
)

#: 已知差异的探针查询：(查询, 期望说明)。这些**不参与**主比对的通过/失败判定。
KNOWN_DIVERGENCE_QUERIES: tuple[str, ...] = (
    "cafe",
    "café",
    "creme",
    "crème",
    "resume",
    "résumé",
    "naive",
    "naïve",
    "façade",
    "longtoken",
    "超长不可分词标识符",
    "不可分词的超长标识符",
    "A" * 3000,
    # 第 3 类差异：`.` / `-` 在两种分词器里的归属不同（FTS5 当分隔符、PG 当词的一部分）
    "node.js",
    "node",
    "js",
    "scikit-learn",
    "scikit",
    "learn",
    "v1.2.3",
    "1.2.3",
    "a-b-c",
    "点号、连字符标识符探针",
)


async def _build_and_query() -> dict[str, object]:
    from app.db.session import SessionLocal
    from app.models.enums import ToolStatus, ToolType, ToolVisibility, UserStatus
    from app.models.taxonomy import Category, Tag
    from app.models.tool import Tool, ToolTag
    from app.models.user import User
    from app.search import SearchDocument, get_search_backend

    backend = get_search_backend()
    slug_to_id: dict[str, int] = {}

    async with SessionLocal() as session:
        user = User(
            username="dc-owner",
            display_name="方言一致性",
            email="dc@example.com",
            password_hash="x" * 60,
            status=UserStatus.ACTIVE.value,
        )
        session.add(user)
        await session.flush()
        category = Category(slug="dc-cat", name="一致性", sort_order=1, is_active=True)
        session.add(category)
        await session.flush()

        for spec in CORPUS:
            tool = Tool(
                slug=str(spec["slug"]),
                name=str(spec["name"]),
                summary=str(spec["summary"]),
                description_md=str(spec["description"]),
                tool_type=ToolType.PROMPT.value,
                visibility=ToolVisibility.PUBLIC.value,
                status=ToolStatus.APPROVED.value,
                owner_id=user.id,
                category_id=category.id,
                version_seq=1,
            )
            session.add(tool)
            await session.flush()
            slug_to_id[str(spec["slug"])] = int(tool.id)

            for tag_name in spec["tags"]:  # type: ignore[union-attr]
                tag = Tag(name=str(tag_name), display_name=str(tag_name))
                session.add(tag)
                await session.flush()
                session.add(ToolTag(tool_id=tool.id, tag_id=tag.id))

            # 走**真实写入路径**（与接口层 `_sync_index` 一致）
            await backend.upsert(
                session,
                int(tool.id),
                SearchDocument(
                    name=str(spec["name"]),
                    summary=str(spec["summary"]),
                    description=str(spec["description"]),
                    tags=[str(t) for t in spec["tags"]],  # type: ignore[union-attr]
                ),
            )

        await session.commit()

        # ---- 第 1 段：主比对（代表性语料），必须在**干净**语料上算完 ----
        results: dict[str, object] = {}
        for query in QUERIES:
            ids = await backend.matching_tool_ids(session, query)
            if ids is None:
                results[query] = None  # 后端处理不了 → 仓储走 LIKE，不属于本比对
                continue
            slug_by_id = {v: k for k, v in slug_to_id.items()}
            results[query] = {
                "ids": sorted(ids),
                "slugs": sorted(slug_by_id[i] for i in ids if i in slug_by_id),
                "unexpected_ids": sorted(i for i in ids if i not in slug_by_id),
            }

        # ---- 第 2 段：已知差异探针（**不参与**主比对的通过/失败判定）----
        for spec in KNOWN_DIVERGENCE_CORPUS:
            tool = Tool(
                slug=str(spec["slug"]),
                name=str(spec["name"]),
                summary=str(spec["summary"]),
                description_md=str(spec["description"]),
                tool_type=ToolType.PROMPT.value,
                visibility=ToolVisibility.PUBLIC.value,
                status=ToolStatus.APPROVED.value,
                owner_id=user.id,
                category_id=category.id,
                version_seq=1,
            )
            session.add(tool)
            await session.flush()
            slug_to_id[str(spec["slug"])] = int(tool.id)
            await backend.upsert(
                session,
                int(tool.id),
                SearchDocument(
                    name=str(spec["name"]),
                    summary=str(spec["summary"]),
                    description=str(spec["description"]),
                    tags=[str(t) for t in spec["tags"]],  # type: ignore[union-attr]
                ),
            )
        await session.commit()

        slug_by_id = {v: k for k, v in slug_to_id.items()}
        divergence: dict[str, object] = {}
        for query in KNOWN_DIVERGENCE_QUERIES:
            ids = await backend.matching_tool_ids(session, query)
            label = query if len(query) < 40 else f"<{len(query)} 个 'A' 字符的单个 token>"
            divergence[label] = (
                None
                if ids is None
                else sorted(slug_by_id[i] for i in ids if i in slug_by_id)
            )

    return {
        "dialect": "sqlite" if DB_URL.startswith("sqlite") else "postgresql",
        "backend": backend.name,
        "slug_to_id": slug_to_id,
        "results": results,
        "known_divergence": divergence,
    }


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] != "--out":
        print("用法: m15-dialect-consistency.py --out <json 路径>", file=sys.stderr)
        raise SystemExit(2)

    # 迁移必须在 asyncio.run() **之前**跑：`migrations/env.py` 自己会调用
    # `asyncio.run()`，在事件循环里再调一次会直接炸
    # （`asyncio.run() cannot be called from a running event loop`）。
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(cfg, "head")

    payload = asyncio.run(_build_and_query())
    Path(sys.argv[2]).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"[m15-dc] 方言={payload['dialect']} 后端={payload['backend']} 已写出 {sys.argv[2]}")


if __name__ == "__main__":
    main()
