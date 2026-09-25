"""全部 ORM 模型。导入本包即完成 `Base.metadata` 的注册（Alembic autogenerate 依赖它）。

20 张表 = 19 个 SQLAlchemy 模型 + 1 个 SQLite FTS5 虚表 `tool_search_index`
（虚表由迁移 0003 用原生 DDL 创建，不是 ORM 模型）。
"""

from __future__ import annotations

from app.db.base import Base
from app.models.approval import ApprovalRecord
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
    "ToolImage",
    "ToolStatsDaily",
    "ToolTag",
    "ToolVersion",
    "User",
    "UserRole",
]

#: docs/02 §3 定义的 20 张表（含 FTS5 虚表）
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
    }
)
