"""系统设置仓储。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.setting import SystemSetting

#: 设置项默认值（docs/02 §3.19 的设置项清单）。
#: 迁移 0002 用同一份数据幂等插入，保证任何环境初始状态一致。
#: 元组为 `(key, value, value_type, is_public, description)`。
SETTING_DEFAULTS: tuple[tuple[str, Any, str, bool, str], ...] = (
    ("approval.mode", "require", "string", False, "approval.mode = require / auto_approve_all"),
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
    ("quota.warn_threshold_pct", 85, "int", False, "配额告警阈值百分比"),
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
    ("webapp.health_check_enabled", False, "bool", False, "在线工具探活（二期）"),
    ("api.docs_enabled", False, "bool", False, "是否开放 /docs"),
)

SETTING_DEFAULTS_BY_KEY: dict[str, tuple[Any, str, bool, str]] = {
    key: (value, value_type, is_public, description)
    for key, value, value_type, is_public, description in SETTING_DEFAULTS
}


async def get(session: AsyncSession, key: str, default: Any = None) -> Any:
    row = await session.get(SystemSetting, key)
    return row.value if row is not None else default


async def get_many(session: AsyncSession, keys: list[str]) -> dict[str, Any]:
    if not keys:
        return {}
    result = await session.execute(
        select(SystemSetting).where(SystemSetting.key.in_(keys))
    )
    return {row.key: row.value for row in result.scalars().all()}


async def list_all(session: AsyncSession) -> list[SystemSetting]:
    result = await session.execute(select(SystemSetting).order_by(SystemSetting.key))
    return list(result.scalars().all())


async def list_public(session: AsyncSession) -> dict[str, Any]:
    """只取 `is_public = true` 的项 —— `GET /api/v1/meta` 的数据源（FR-CFG-03）。"""
    result = await session.execute(
        select(SystemSetting).where(SystemSetting.is_public.is_(True))
    )
    return {row.key: row.value for row in result.scalars().all()}


async def get_effective_bool(session: AsyncSession, key: str, default: bool = False) -> bool:
    """读布尔设置，缺失时回退到代码里的默认值。

    运行时立即生效（FR-CFG-01），所以每次请求都读表，不做进程内缓存 ——
    百人规模下这是一次主键点查，代价可以忽略。
    """
    row = await session.get(SystemSetting, key)
    if row is None:
        fallback = SETTING_DEFAULTS_BY_KEY.get(key)
        return bool(fallback[0]) if fallback else default
    return bool(row.value)


async def get_effective_int(session: AsyncSession, key: str, default: int = 0) -> int:
    row = await session.get(SystemSetting, key)
    if row is None:
        fallback = SETTING_DEFAULTS_BY_KEY.get(key)
        return int(fallback[0]) if fallback else default
    return int(row.value)


async def get_effective_str(session: AsyncSession, key: str, default: str = "") -> str:
    row = await session.get(SystemSetting, key)
    if row is None:
        fallback = SETTING_DEFAULTS_BY_KEY.get(key)
        return str(fallback[0]) if fallback else default
    return str(row.value)
