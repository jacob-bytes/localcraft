"""系统设置服务。

设置项运行时修改后**立即生效**（FR-CFG-01），因此不做进程内缓存：
每次请求读表，百人规模下这是一次主键点查。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings as env_settings
from app.core.timeutil import utcnow
from app.models.enums import AuthSource
from app.repositories import approvals as approvals_repo
from app.repositories import system_settings as settings_repo
from app.schemas.meta import MetaFeatures, MetaResponse
from app.schemas.setting import (
    SettingItem,
    SettingUpdatedBy,
    SettingWarning,
)


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


# ===========================================================================
# M2：上传限额、配额、自动审批判定
# ===========================================================================
@dataclass(frozen=True)
class UploadLimits:
    """上传相关的生效限额（系统设置优先，环境变量兜底）。"""

    max_file_size_mb: int
    max_file_size_bytes: int
    allowed_extensions: frozenset[str]
    max_screenshots: int
    max_tags: int
    per_user_quota_bytes: int
    total_quota_bytes: int
    history_limit: int


@dataclass(frozen=True)
class AutoApprovalDecision:
    """自动审批判定结果。"""

    approved: bool
    rule: str | None = None


async def get_upload_limits(session: AsyncSession) -> UploadLimits:
    """读齐上传相关限额。

    这些走**系统设置表**（docs/02 §3.19）而不是环境变量 —— 运营需要在管理台
    调整单文件上限、截图数、配额。缺失行时回退到 `SETTING_DEFAULTS`。
    """
    max_mb = await settings_repo.get_effective_int(
        session, "upload.max_file_size_mb", 200
    )
    extensions = await settings_repo.get(
        session,
        "upload.allowed_extensions",
        None,
    )
    if not isinstance(extensions, list) or not extensions:
        extensions = list(env_settings.allowed_extensions)

    per_user_mb = await settings_repo.get_effective_int(session, "quota.per_user_mb", 2048)
    total_mb = await settings_repo.get_effective_int(session, "quota.total_mb", 51200)

    return UploadLimits(
        max_file_size_mb=max_mb,
        max_file_size_bytes=max_mb * 1024 * 1024,
        allowed_extensions=frozenset(str(e).lower().lstrip(".") for e in extensions),
        max_screenshots=await settings_repo.get_effective_int(
            session, "upload.max_screenshots", 8
        ),
        max_tags=await settings_repo.get_effective_int(session, "upload.max_tags", 8),
        # 0 表示不限
        per_user_quota_bytes=per_user_mb * 1024 * 1024,
        total_quota_bytes=total_mb * 1024 * 1024,
        history_limit=await settings_repo.get_effective_int(
            session, "version.history_limit", 10
        ),
    )


async def check_storage_quota(
    session: AsyncSession,
    *,
    owner_id: int,
    incoming_bytes: int,
    limits: UploadLimits,
) -> None:
    """配额校验（FR-FILE-12）。

    这是**预检**：只知道即将写入的大小。真正的兜底是流式写入时的实际字节计数。
    0 表示不限。
    """
    from app.core.errors import InsufficientStorageError, UserQuotaExceededError
    from app.repositories import tool_versions as versions_repo

    if limits.per_user_quota_bytes > 0:
        used = await versions_repo.sum_file_size_for_user(session, owner_id)
        if used + incoming_bytes > limits.per_user_quota_bytes:
            raise UserQuotaExceededError(
                details={
                    "used_mb": used // (1024 * 1024),
                    "quota_mb": limits.per_user_quota_bytes // (1024 * 1024),
                }
            )

    if limits.total_quota_bytes > 0:
        total_used = await versions_repo.sum_file_size_all(session)
        if total_used + incoming_bytes > limits.total_quota_bytes:
            raise InsufficientStorageError(
                details={
                    "used_mb": total_used // (1024 * 1024),
                    "quota_mb": limits.total_quota_bytes // (1024 * 1024),
                }
            )


async def evaluate_auto_approval(
    session: AsyncSession, *, user_id: int, now=None
) -> AutoApprovalDecision:
    """判断本次提交是否命中自动放行（FR-APPR-01 / FR-APPR-03）。

    两条规则，先看全局开关再看白名单：
      - `approval.mode == auto_approve_all`  → 全部放行
      - `approval.whitelist_enabled` 且该用户在有效白名单内 → 放行
    """
    from app.core.timeutil import utcnow
    from app.models.enums import ApprovalMode
    from app.repositories import approvals as approvals_repo

    moment = now or utcnow()

    mode = await settings_repo.get_effective_str(session, "approval.mode", "require")
    if mode == ApprovalMode.AUTO_APPROVE_ALL.value:
        return AutoApprovalDecision(approved=True, rule="auto_approve_all")

    whitelist_enabled = await settings_repo.get_effective_bool(
        session, "approval.whitelist_enabled", True
    )
    if whitelist_enabled and await approvals_repo.is_whitelisted(session, user_id, now=moment):
        return AutoApprovalDecision(approved=True, rule="whitelist")

    return AutoApprovalDecision(approved=False)


# ===========================================================================
# M2：设置读写（docs/03 §3.13）
# ===========================================================================
async def list_settings(session: AsyncSession) -> list[SettingItem]:
    """`GET /admin/settings` —— 全部设置项 + 控件元信息。"""
    from app.repositories.system_settings import SETTING_SPECS

    rows = await settings_repo.list_all(session)
    # 一次查出所有 updated_by，避免 N+1
    user_ids = {row.updated_by_id for row in rows if row.updated_by_id}
    names: dict[int, str] = {}
    if user_ids:
        from sqlalchemy import select as _select

        from app.models.user import User as _User

        result = await session.execute(
            _select(_User.id, _User.display_name).where(_User.id.in_(user_ids))
        )
        names = {int(r[0]): str(r[1]) for r in result.all()}

    items: list[SettingItem] = []
    for row in rows:
        spec = SETTING_SPECS.get(row.key)
        items.append(
            SettingItem(
                key=row.key,
                value=row.value,
                value_type=row.value_type,
                is_public=row.is_public,
                description=row.description,
                options=list(spec.options) if spec and spec.options else None,
                minimum=spec.minimum if spec else None,
                maximum=spec.maximum if spec else None,
                updated_at=row.updated_at,
                updated_by=(
                    SettingUpdatedBy(
                        id=row.updated_by_id, display_name=names.get(row.updated_by_id, "")
                    )
                    if row.updated_by_id
                    else None
                ),
            )
        )
    return items


async def update_settings(
    session: AsyncSession, *, updates: list[tuple[str, object]], actor_id: int
) -> list[SettingWarning]:
    """`PUT /admin/settings` —— 批量部分更新。

    **任何一项非法则整体回滚**（docs/03 §3.13 的原子性要求）：
    先在内存里逐项校验，全部通过后才开始写；写的过程中任何异常也不提交。
    """
    from app.core.errors import SettingInvalidError
    from app.models.enums import ApprovalMode
    from app.repositories.system_settings import validate_setting_value

    # ---- 阶段一：只读校验，一行都不写 ----
    resolved: list[tuple[object, object]] = []
    for key, value in updates:
        row = await settings_repo.get_row(session, key)
        if row is None:
            raise SettingInvalidError(
                message=f"未知的设置项: {key}", details={"key": key}
            )
        error = validate_setting_value(key, value, row.value_type)
        if error is not None:
            raise SettingInvalidError(
                message=f"设置项 {key} 的值非法：{error}",
                details={"key": key, "value": value, "reason": error},
            )
        resolved.append((row, value))

    # ---- 阶段二：一次性写入 ----
    now = utcnow()
    warnings: list[SettingWarning] = []
    mode_changed_to_auto = False
    for row, value in resolved:
        if (
            row.key == "approval.mode"
            and value == ApprovalMode.AUTO_APPROVE_ALL.value
            and row.value != value
        ):
            mode_changed_to_auto = True
        await settings_repo.update_value(
            session, row, value=value, updated_by_id=actor_id, now=now
        )

    # FR-APPR-02：切换为全部放行时，**已处于 pending 的条目不会被自动处理**，
    # 用 warnings 告知待审数量。
    if mode_changed_to_auto:
        pending_count = await approvals_repo.count_pending(session)
        if pending_count:
            warnings.append(
                SettingWarning(
                    code="PENDING_ITEMS_NOT_AFFECTED",
                    message=f"当前有 {pending_count} 个待审条目，切换为全部放行不会自动处理它们。",
                    pending_count=pending_count,
                )
            )

    await session.commit()

    # 设置变更立即生效（FR-CFG-01）—— 本服务每次请求都读表，不做进程内缓存，
    # 所以这里不需要额外通知任何组件。
    return warnings
