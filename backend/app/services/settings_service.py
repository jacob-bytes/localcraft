"""系统设置服务。

设置项运行时修改后**立即生效**（FR-CFG-01），因此不做进程内缓存：
每次请求读表，百人规模下这是一次主键点查。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings as env_settings
from app.models.enums import AuthSource
from app.repositories import system_settings as settings_repo
from app.schemas.meta import MetaFeatures, MetaResponse


@dataclass(frozen=True)
class SecurityPolicy:
    """安全与门户相关的生效配置。"""

    access_token_minutes: int
    refresh_token_days: int
    login_max_failures: int
    lockout_minutes: int
    allow_anonymous_view: bool
    allow_admin_view_private: bool
    site_name: str
    announcement_md: str
    default_sort: str
    page_size: int
    api_docs_enabled: bool


async def get_security_policy(session: AsyncSession) -> SecurityPolicy:
    """一次读齐鉴权/门户需要的设置项。

    缺失的行回退到 `app.repositories.system_settings.SETTING_DEFAULTS`，
    再回退到环境变量 —— 三层兜底保证「迁移还没跑」时服务仍可用。
    """
    return SecurityPolicy(
        access_token_minutes=await settings_repo.get_effective_int(
            session, "security.access_token_minutes", env_settings.access_token_minutes
        ),
        refresh_token_days=await settings_repo.get_effective_int(
            session, "security.refresh_token_days", env_settings.refresh_token_days
        ),
        login_max_failures=await settings_repo.get_effective_int(
            session, "security.login_max_failures", env_settings.login_max_failures
        ),
        lockout_minutes=await settings_repo.get_effective_int(
            session, "security.lockout_minutes", env_settings.lockout_minutes
        ),
        allow_anonymous_view=await settings_repo.get_effective_bool(
            session, "portal.allow_anonymous_view", False
        ),
        allow_admin_view_private=await settings_repo.get_effective_bool(
            session, "portal.allow_admin_view_private", True
        ),
        site_name=await settings_repo.get_effective_str(
            session, "portal.site_name", "工具与 Skill 平台"
        ),
        announcement_md=await settings_repo.get_effective_str(
            session, "portal.announcement_md", ""
        ),
        default_sort=await settings_repo.get_effective_str(session, "portal.default_sort", "hot"),
        page_size=await settings_repo.get_effective_int(session, "portal.page_size", 24),
        api_docs_enabled=await settings_repo.get_effective_bool(
            session, "api.docs_enabled", env_settings.api_docs_enabled
        ),
    )


async def get_meta(session: AsyncSession) -> MetaResponse:
    """`GET /api/v1/meta`（docs/03 §3.1）。

    数据来源严格限制为 `is_public = true` 的设置项 —— 私有项（配额、审批开关等）
    绝不外泄（FR-CFG-03）。
    """
    public = await settings_repo.list_public(session)
    policy = await settings_repo.get_effective_bool(
        session, "webapp.health_check_enabled", False
    )
    return MetaResponse(
        site_name=str(public.get("portal.site_name", "工具与 Skill 平台")),
        announcement_md=str(public.get("portal.announcement_md", "")),
        auth_provider=AuthSource.LOCAL.value,
        allow_anonymous_view=bool(public.get("portal.allow_anonymous_view", False)),
        default_sort=str(public.get("portal.default_sort", "hot")),
        page_size=int(public.get("portal.page_size", 24)),
        app_version=env_settings.selftool_version,
        api_version="v1",
        features=MetaFeatures(
            webapp_health_check=policy,
            skill_preview=True,
            anonymous_view=bool(public.get("portal.allow_anonymous_view", False)),
            change_password=True,
        ),
    )


async def is_anonymous_view_allowed(session: AsyncSession) -> bool:
    """`GET /api/v1/tools` 对未登录用户是 401 还是返回公开数据（契约 §6）。"""
    return await settings_repo.get_effective_bool(session, "portal.allow_anonymous_view", False)
