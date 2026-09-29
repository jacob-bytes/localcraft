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

    ★ 实现上走 **`get_effective_many`（一条 `IN` 查询）**，不是 11 次点查。
    改之前是 11 个并排的 `get_effective_*`，也就是 11 条独立语句 ——
    在匿名 `GET /api/v1/tools` 上占整请求语句数的六成（`tests/test_observability.py`
    把这个构成逐条列了出来）。这些键在**语义上是一个整体**（同表、同批、同一时刻），
    拆成 11 次只是写法副作用；而 `aiosqlite` 每条语句都要跨一次专用工作线程
    （`docs/11` §2.5 定位的放大器），所以这是实打实的成本，不是风格问题。

    ⚠️ 每个键的 `default` 必须**原样**带过来（有的是环境变量、有的是字面量）：
    第三层兜底用的是调用方的 default，统一兜一个值会改变「迁移还没跑」时的行为。
    """
    values = await settings_repo.get_effective_many(
        session,
        (
            ("security.access_token_minutes", "int", env_settings.access_token_minutes),
            ("security.refresh_token_days", "int", env_settings.refresh_token_days),
            ("security.login_max_failures", "int", env_settings.login_max_failures),
            ("security.lockout_minutes", "int", env_settings.lockout_minutes),
            ("portal.allow_anonymous_view", "bool", False),
            ("portal.allow_admin_view_private", "bool", True),
            ("portal.site_name", "str", "工具与 Skill 平台"),
            ("portal.announcement_md", "str", ""),
            ("portal.default_sort", "str", "hot"),
            ("portal.page_size", "int", 24),
            ("api.docs_enabled", "bool", env_settings.api_docs_enabled),
        ),
    )
    return SecurityPolicy(
        access_token_minutes=values["security.access_token_minutes"],
        refresh_token_days=values["security.refresh_token_days"],
        login_max_failures=values["security.login_max_failures"],
        lockout_minutes=values["security.lockout_minutes"],
        allow_anonymous_view=values["portal.allow_anonymous_view"],
        allow_admin_view_private=values["portal.allow_admin_view_private"],
        site_name=values["portal.site_name"],
        announcement_md=values["portal.announcement_md"],
        default_sort=values["portal.default_sort"],
        page_size=values["portal.page_size"],
        api_docs_enabled=values["api.docs_enabled"],
    )


def _public_text(public: dict[str, object], key: str) -> str:
    """`/meta` 的站点定制字段取值：**永远是 `str`**（契约 §27.3 / §28.4）。

    M12 新增的 5 个字段与 M13 的 `portal.footer_tagline` 都走这里，
    既有字段的行为一字未动。

    为什么不直接 `str(public.get(key, ""))`（既有字段的写法）：`/meta` 是**公开**
    端点，一个畸形值就会让整个门户白屏。两种情况必须挡住：

      - JSON `null` → `str(None)` 会得到字面量 `"None"`，前端会把它当副标题显示；
      - 非字符串（有人手工改库塞了个数字）→ pydantic v2 **不**做 int→str 强转，
        会抛 ValidationError 变成 500。

    两种都退化成该 key 在 `SETTING_DEFAULTS` 里的**代码默认值**（缺失则空串）。

    ★ 行**根本不存在**时（迁移还没跑 / 行被删过）也回退到同一个代码默认值：
    对 M12 那 5 项默认值就是空串，行为与之前**逐字不变**；对 M13 的
    `portal.footer_tagline`（§28.3）则是回退到原标语串 —— 这正是 §28.6
    「未配置的既有部署在页脚上与改动前完全一致」的要求，且让 `/meta` 与
    `get_effective_str` 的既有兜底口径一致（「缺失行 = 未配置 = 用默认值」）。
    """
    default = settings_repo.SETTING_DEFAULTS_BY_KEY.get(key)
    fallback = default[0] if default is not None else ""
    if not isinstance(fallback, str):
        fallback = ""
    # 行缺失（迁移没跑 / 行被删）→ 代码默认值；行在但值是 JSON `null` 或非字符串
    # （有人手工改库塞了数字）→ 同样退化成代码默认值。对 M12 那 5 项，两者都是
    # 空串，与改动前逐字一致（§27.3）；对 `portal.footer_tagline` 则是原标语串（§28.3）。
    value = public.get(key)
    return value if isinstance(value, str) else fallback


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
        app_version=env_settings.localcraft_version,
        api_version="v1",
        # M12 站点定制信息（契约 §27.3）：全部走**既有机制**（`is_public = true`
        # 的设置项），不另写读取逻辑。未配置（行缺失）时回退代码默认值 —— 与 §27.2
        # 定的默认值一致，保证「既有部署不做任何配置时视觉上零变化」。
        site_subtitle=_public_text(public, "portal.site_subtitle"),
        footer_org=_public_text(public, "portal.footer_org"),
        footer_contact_email=_public_text(public, "portal.footer_contact_email"),
        footer_contact_phone=_public_text(public, "portal.footer_contact_phone"),
        footer_notice=_public_text(public, "portal.footer_notice"),
        # M13（契约 §28.4）：页脚标语。同一条既有机制，唯一区别是它的代码默认值
        # 非空（§28.3 的定点例外）—— 行缺失时读出来就是原标语串（ §28.6 的
        # 「与改动前完全一致」）。版本号仍不是设置项，前端取上面的 `app_version`。
        footer_tagline=_public_text(public, "portal.footer_tagline"),
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


async def is_admin_private_view_allowed(session: AsyncSession) -> bool:
    """`portal.allow_admin_view_private`：超管是否可见他人 `private` 工具（FR-ACL-04）。

    ★ 存在的理由就是**为了只读这一个键**：调用点（`deps.get_visibility_context`）
    原先走 `get_security_policy()` —— 为了拿这一个布尔，把 11 个设置项全查了一遍，
    即 11 条语句换 1 个字段。而 `aiosqlite` 每条语句跨一次专用线程
    （`docs/11` §2.5 定位的放大器），这类「多查」在热路径上是直接的成本。
    默认值 `True` 与 `get_security_policy` 里那一处**必须保持一致**。
    """
    return await settings_repo.get_effective_bool(
        session, "portal.allow_admin_view_private", True
    )


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


#: 存储水位告警的默认阈值（百分比）——设置项 `quota.warn_threshold_pct`
STORAGE_WARNING_DEFAULT_PCT = 85


def storage_warning(used_bytes: int, quota_bytes: int, threshold_pct: int) -> bool:
    """是否应当就存储水位告警。

    **这是全项目唯一的判断口径**（契约 §18.6）：
    `GET /admin/overview` 的 `storage_warning` 与 `scripts/disk-alert.sh`
    都复用它 —— 一个用 Python、一个用 shell，靠这个函数的语义对齐，
    而不是各写一份阈值比较（那样迟早会漂移）。

    边界：`quota_bytes <= 0` 表示「不限配额」，此时永不告警。
    """
    if quota_bytes <= 0:
        return False
    return used_bytes * 100 >= quota_bytes * threshold_pct


async def evaluate_auto_approval(
    session: AsyncSession,
    *,
    user_id: int,
    tool_status: str | None = None,
    now=None,
) -> AutoApprovalDecision:
    """判断本次提交是否命中自动放行（FR-APPR-01 / FR-APPR-03）。

    三条规则，从「最具体」到「最宽泛」：

      1. `approval.version_reapproval == false` **且**工具已处于 `approved`
         → 放行，规则 `version_reapproval_off`。
         这是契约 §18.6 的裁定：`approval.mode` 管的是**首次发布**是否需审，
         `version_reapproval` 管的是**已发布工具的新版本**是否需再审，
         两者是不同的策略维度（「可信工具，更新免审」）。
      2. `approval.mode == auto_approve_all` → 全部放行
      3. `approval.whitelist_enabled` 且该用户在有效白名单内 → 放行

    注意 `tool_status` 是**本次提交前**的状态：只有已经发布过的工具才谈得上
    「新版本免审」。draft 首次发布仍然照 `approval.mode` 走。
    """
    from app.core.timeutil import utcnow
    from app.models.enums import ApprovalMode, ToolStatus
    from app.repositories import approvals as approvals_repo

    moment = now or utcnow()

    # 规则 1：已发布工具的新版本免审（默认开启「需再审」，所以默认不命中）
    reapproval_required = await settings_repo.get_effective_bool(
        session, "approval.version_reapproval", True
    )
    if not reapproval_required and tool_status == ToolStatus.APPROVED.value:
        return AutoApprovalDecision(approved=True, rule="version_reapproval_off")

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
    #
    # 例外（M14 / 契约 §29.4）：**限流配置有 5 秒进程内缓存**（它读的是
    # `security.rate_limit_*`，在限流依赖里读）。若不在这里显式失效，
    # 管理员改完配额最多 5 秒后才生效 —— 那就把 FR-CFG-01 打了折。
    from app.core.rate_limit import reset_config_cache

    reset_config_cache()
    return warnings
