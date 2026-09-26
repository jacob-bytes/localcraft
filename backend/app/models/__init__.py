"""全部 ORM 模型。导入本包即完成 `Base.metadata` 的注册（Alembic autogenerate 依赖它）。

表数：docs/02 §3 定义的 20 张（19 个 SQLAlchemy 模型 + 1 个 SQLite FTS5 虚表
`tool_search_index`，虚表由迁移 0003 用原生 DDL 创建，不是 ORM 模型），
加上 **M8 新增的 2 张**（`tool_favorites` / `tool_likes`，contracts §23.3）= 22 张。

> docs/02 尚未登记这 2 张新表（`docs/` 由监控方维护，M8 后端不改文档）。
"""

from __future__ import annotations

from app.db.base import Base
from app.models.approval import ApprovalRecord
from app.models.engagement import ToolFavorite, ToolLike
from app.models.setting import SystemSetting
from app.models.stats import DownloadLog, ToolStatsDaily
from app.models.taxonomy import Category, Tag
from app.models.tool import Tool, ToolAcl, ToolImage, ToolTag, ToolVersion
from app.models.user import (
    ApiToken,
    ApprovalWhitelist,
    AuthSession,
    Group,
    GroupMember,
    Role,
    User,
    UserRole,
)

__all__ = [
    "ApiToken",
    "ApprovalRecord",
    "ApprovalWhitelist",
    "AuthSession",
    "Base",
    "Category",
    "DownloadLog",
    "Group",
    "GroupMember",
    "Role",
    "SystemSetting",
    "Tag",
    "Tool",
    "ToolAcl",
    "ToolFavorite",
    "ToolImage",
    "ToolLike",
    "ToolStatsDaily",
    "ToolTag",
    "ToolVersion",
    "User",
    "UserRole",
]

#: 当前实际存在的表（含 FTS5 虚表）。
#:
#: 原始来源是 docs/02 §3 的 20 张表；M8 按 contracts §23.3 追加
#: `tool_favorites` / `tool_likes` 两张，因此现在是 22 张。
EXPECTED_TABLES: frozenset[str] = frozenset(
    {
        "users",
        "roles",
        "user_roles",
        "groups",
        "group_members",
        "categories",
        "tags",
        "tools",
        "tool_tags",
        "tool_acl",
        "tool_versions",
        "tool_images",
        "approval_records",
        "approval_whitelist",
        "api_tokens",
        "auth_sessions",
        "download_logs",
        "tool_stats_daily",
        "system_settings",
        "tool_search_index",
        # ---- M8 ----
        "tool_favorites",
        "tool_likes",
    }
)
