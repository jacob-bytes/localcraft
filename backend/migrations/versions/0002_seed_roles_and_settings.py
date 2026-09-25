"""seed roles and system settings

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-25 04:40:00.000000+00:00

数据播种与结构变更分开成不同迁移文件，便于回滚时区分（docs/02 §6.1）。

  - 4 个内置角色（docs/02 §3.2 / docs/01 §3.1）
  - docs/02 §3.19 的全部系统设置默认值

**幂等**：先查已有行，只插缺失的部分。这样即使有人在半途手工补过数据，
或者迁移在异常后重跑，也不会因主键冲突失败。

注意：本文件的数据是**冻结快照**，不 import 应用代码 ——
迁移一旦发布就不应随应用代码变动（否则历史环境重放会得到不同结果）。
运行时缺失设置的兜底默认值在 `app/repositories/system_settings.py`。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 迁移执行时间作为种子的 created_at/updated_at（应用层填时间戳，不用数据库 default）
SEEDED_AT = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

#: 内置角色。`permissions` 仅是冗余备份 —— 权限判定以
#: `app/core/permissions.py` 的 frozenset 为准，避免数据库被改后提权
#: （docs/02 §3.2 的明确决策）。
ROLES: tuple[dict[str, object], ...] = (
    {
        "id": 1,
        "code": "superadmin",
        "name": "超级管理员",
        "description": "平台最高权限，管理用户、角色、系统设置、API Token",
        "is_builtin": True,
        "permissions": [
            "admin:all",
            "approvals:write",
            "download",
            "groups:write",
            "settings:write",
            "taxonomy:write",
            "tools:read",
            "tools:write",
            "users:write",
        ],
    },
    {
        "id": 2,
        "code": "approver",
        "name": "审批管理员",
        "description": "审批工具上架、下架工具、管理分类标签、查看审批历史",
        "is_builtin": True,
        "permissions": [
            "approvals:write",
            "download",
            "taxonomy:write",
            "tools:read",
            "tools:write",
        ],
    },
    {
        "id": 3,
        "code": "user",
        "name": "普通用户",
        "description": "浏览、下载、上传自己的工具",
        "is_builtin": True,
        "permissions": ["download", "tools:read", "tools:write"],
    },
    {
        "id": 4,
        "code": "viewer",
        "name": "只读访客",
        "description": "只能浏览 public 工具，不能下载受限内容、不能上传",
        "is_builtin": True,
        "permissions": ["tools:read"],
    },
)

#: docs/02 §3.19 的系统设置默认值：(key, value, value_type, is_public, description)
SETTINGS: tuple[tuple[str, object, str, bool, str], ...] = (
    ("approval.mode", "require", "string", False, "审批模式：require / auto_approve_all"),
    ("approval.whitelist_enabled", True, "bool", False, "免审白名单总开关"),
    ("approval.version_reapproval", True, "bool", False, "已发布工具发新版本是否需再审"),
    ("version.history_limit", 10, "int", False, "历史版本保留份数"),
    ("upload.max_file_size_mb", 200, "int", False, "单文件上传上限（MB）"),
    (
        "upload.allowed_extensions",
        [
            "zip",
            "tar.gz",
            "tgz",
            "whl",
            "tar",
            "gz",
            "7z",
            "rar",
            "exe",
            "msi",
            "deb",
            "rpm",
            "sh",
            "py",
            "md",
            "txt",
            "json",
            "yaml",
            "pdf",
            "png",
            "jpg",
        ],
        "list",
        False,
        "允许上传的扩展名白名单",
    ),
    ("upload.max_screenshots", 8, "int", False, "每个工具最多截图数"),
    ("upload.max_tags", 8, "int", False, "单个工具最多标签数"),
    ("quota.per_user_mb", 2048, "int", False, "单用户配额（MB），0 = 不限"),
    ("quota.total_mb", 51200, "int", False, "平台总配额（MB），0 = 不限"),
    ("quota.warn_threshold_pct", 85, "int", False, "配额告警阈值（百分比）"),
    ("security.access_token_minutes", 30, "int", False, "access token 有效期（分钟）"),
    ("security.refresh_token_days", 7, "int", False, "refresh token 有效期（天）"),
    ("security.login_max_failures", 5, "int", False, "连续登录失败锁定阈值"),
    ("security.lockout_minutes", 15, "int", False, "锁定时长（分钟）"),
    ("portal.site_name", "工具与 Skill 平台", "string", True, "站点名称"),
    ("portal.announcement_md", "", "string", True, "首页公告（Markdown）"),
    ("portal.allow_anonymous_view", False, "bool", True, "是否允许未登录浏览门户"),
    ("portal.allow_admin_view_private", True, "bool", False, "超管是否可见他人 private 工具"),
    ("portal.default_sort", "hot", "string", True, "门户默认排序"),
    ("portal.page_size", 24, "int", True, "门户每页条数"),
    ("stats.download_log_retention_days", 180, "int", False, "下载明细保留天数"),
    ("stats.view_dedup_minutes", 60, "int", False, "浏览去重窗口（分钟）"),
    ("webapp.health_check_enabled", False, "bool", False, "在线工具探活开关（二期）"),
    ("api.docs_enabled", False, "bool", False, "是否开放 /docs"),
)


def upgrade() -> None:
    bind = op.get_bind()

    # ---- 角色 ----
    existing_codes = {
        row[0] for row in bind.execute(sa.text("SELECT code FROM roles")).fetchall()
    }
    role_rows = [
        {
            "id": role["id"],
            "code": role["code"],
            "name": role["name"],
            "description": role["description"],
            "is_builtin": role["is_builtin"],
            "permissions": role["permissions"],
            "created_at": SEEDED_AT,
            "updated_at": SEEDED_AT,
        }
        for role in ROLES
        if role["code"] not in existing_codes
    ]
    if role_rows:
        op.bulk_insert(
            sa.table(
                "roles",
                sa.column("id", sa.Integer),
                sa.column("code", sa.String),
                sa.column("name", sa.String),
                sa.column("description", sa.String),
                sa.column("is_builtin", sa.Boolean),
                sa.column("permissions", sa.JSON),
                sa.column("created_at", sa.DateTime(timezone=True)),
                sa.column("updated_at", sa.DateTime(timezone=True)),
            ),
            role_rows,
        )

    # ---- 系统设置 ----
    existing_keys = {
        row[0] for row in bind.execute(sa.text("SELECT key FROM system_settings")).fetchall()
    }
    setting_rows = [
        {
            "key": key,
            "value": value,
            "value_type": value_type,
            "is_public": is_public,
            "description": description,
            "updated_by_id": None,
            "updated_at": SEEDED_AT,
        }
        for key, value, value_type, is_public, description in SETTINGS
        if key not in existing_keys
    ]
    if setting_rows:
        op.bulk_insert(
            sa.table(
                "system_settings",
                sa.column("key", sa.String),
                sa.column("value", sa.JSON),
                sa.column("value_type", sa.String),
                sa.column("is_public", sa.Boolean),
                sa.column("description", sa.String),
                sa.column("updated_by_id", sa.Integer),
                sa.column("updated_at", sa.DateTime(timezone=True)),
            ),
            setting_rows,
        )


def downgrade() -> None:
    """只删除**本迁移播种的**行，不碰后来新增的角色与设置。

    角色的删除会因为 `user_roles` 的外键 CASCADE 而连带清理授权关系 ——
    回滚 0002 意味着回到「没有任何内置角色」的状态，这是刻意的。
    """
    bind = op.get_bind()
    codes = ", ".join(f"'{role['code']}'" for role in ROLES)
    keys = ", ".join(f"'{key}'" for key, *_ in SETTINGS)
    bind.execute(sa.text(f"DELETE FROM system_settings WHERE key IN ({keys})"))  # noqa: S608
    bind.execute(sa.text(f"DELETE FROM roles WHERE code IN ({codes})"))  # noqa: S608
