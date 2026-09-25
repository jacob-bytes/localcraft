"""公开路由清单与强制改密豁免前缀。

放在这里而不是散在路由文件里，是为了让**守卫测试**和**改密拦截**引用
同一份定义 —— 两处一旦不一致就说明有人悄悄放开了某个接口。
"""

from __future__ import annotations

#: 完全公开（无需任何凭证）的端点：`(方法, 路径)`。
#: 新增公开接口必须同时改这里，并说明理由 —— 守卫测试会盯着。
PUBLIC_ENDPOINTS: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/healthz"),
        ("GET", "/readyz"),
        ("GET", "/api/v1/meta"),
        ("GET", "/api/v1/auth/provider"),
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/refresh"),
        # 登出依赖 refresh Cookie 而不是 Bearer，且必须幂等：
        # access token 过期后用户仍要能可靠登出（契约 §3.2 ⑤）
        ("POST", "/api/v1/auth/logout"),
    }
)

#: 强制改密拦截的豁免（FR-AUTH-03）：
#: 「除 /auth/* 与 /meta、/healthz、/readyz 外一律返回 403 PASSWORD_CHANGE_REQUIRED」
#: 注意 `/auth/me` 与 `/auth/change-password` 虽然在豁免内，但它们仍然
#: **要求已认证**（用 `get_current_user`），只是不触发改密拦截。
PASSWORD_GATE_EXEMPT_PREFIXES: tuple[str, ...] = (
    "/api/v1/auth/",
    "/api/v1/meta",
    "/healthz",
    "/readyz",
)

#: 前端 SPA fallback 必须放行的前缀（返回 JSON 404 而不是 index.html）
NON_SPA_PREFIXES: tuple[str, ...] = (
    "api/",
    "healthz",
    "readyz",
    "docs",
    "redoc",
    "openapi.json",
)




# ---------------------------------------------------------------------------
# M2 冻结接口清单（contracts/CONTRACT.md §6.1）
# ---------------------------------------------------------------------------
#: M2 新增的 38 个接口。实现后接口面总数 = 12（M1）+ 38 = **50**。
M2_ENDPOINTS: frozenset[tuple[str, str]] = frozenset(
    {
        # ---- 门户 / 阅读侧（7）----
        ("GET", "/api/v1/tools/{slug}"),
        ("GET", "/api/v1/tools/{slug}/versions"),
        ("GET", "/api/v1/tools/{slug}/versions/{version}/skill-preview"),
        ("POST", "/api/v1/tools/{slug}/download-ticket"),
        ("GET", "/api/v1/tools/{slug}/download"),
        ("GET", "/api/v1/tools/{slug}/stats"),
        ("GET", "/api/v1/images/{image_id}"),
        # ---- 个人中心（19）----
        ("GET", "/api/v1/me/profile"),
        ("PATCH", "/api/v1/me/profile"),
        ("GET", "/api/v1/me/stats"),
        ("GET", "/api/v1/me/tools"),
        ("POST", "/api/v1/me/tools"),
        ("GET", "/api/v1/me/tools/{tool_id}"),
        ("PATCH", "/api/v1/me/tools/{tool_id}"),
        ("DELETE", "/api/v1/me/tools/{tool_id}"),
        ("POST", "/api/v1/me/tools/{tool_id}/submit"),
        ("POST", "/api/v1/me/tools/{tool_id}/withdraw"),
        ("PUT", "/api/v1/me/tools/{tool_id}/acl"),
        ("GET", "/api/v1/me/tools/{tool_id}/versions"),
        ("POST", "/api/v1/me/tools/{tool_id}/versions"),
        ("PATCH", "/api/v1/me/tools/{tool_id}/versions/{version}"),
        ("DELETE", "/api/v1/me/tools/{tool_id}/versions/{version}"),
        ("POST", "/api/v1/me/tools/{tool_id}/images"),
        ("PATCH", "/api/v1/me/tools/{tool_id}/images/{image_id}"),
        ("DELETE", "/api/v1/me/tools/{tool_id}/images/{image_id}"),
        ("GET", "/api/v1/me/downloads"),
        # ---- 审批与治理（12）----
        ("GET", "/api/v1/admin/approvals"),
        ("POST", "/api/v1/admin/approvals/{tool_id}/approve"),
        ("POST", "/api/v1/admin/approvals/{tool_id}/reject"),
        ("POST", "/api/v1/admin/approvals/{tool_id}/offline"),
        ("POST", "/api/v1/admin/approvals/{tool_id}/relist"),
        ("POST", "/api/v1/admin/approvals/batch-approve"),
        ("GET", "/api/v1/admin/approvals/history"),
        ("GET", "/api/v1/admin/approval-whitelist"),
        ("POST", "/api/v1/admin/approval-whitelist"),
        ("DELETE", "/api/v1/admin/approval-whitelist/{user_id}"),
        ("GET", "/api/v1/admin/settings"),
        ("PUT", "/api/v1/admin/settings"),
    }
)

M1_ONLY_ENDPOINTS: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/healthz"),
        ("GET", "/readyz"),
        ("GET", "/api/v1/meta"),
        ("GET", "/api/v1/auth/provider"),
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/refresh"),
        ("POST", "/api/v1/auth/logout"),
        ("GET", "/api/v1/auth/me"),
        ("POST", "/api/v1/auth/change-password"),
        ("GET", "/api/v1/categories"),
        ("GET", "/api/v1/tags"),
        ("GET", "/api/v1/tools"),
    }
)

#: **M3 的边界**：这些前缀在 M2 阶段必须完全不存在。
#: 守卫测试逐条断言，防止有人「顺手」把 M3 的接口提前写进来。
M3_FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "/api/v1/admin/tools",
    "/api/v1/admin/users",
    "/api/v1/admin/groups",
    "/api/v1/admin/categories",
    "/api/v1/admin/tags",
    "/api/v1/admin/tokens",
    "/api/v1/admin/import",
    "/api/v1/admin/export",
    "/api/v1/admin/stats",
    "/api/v1/admin/overview",
    "/api/v1/admin/recycle-bin",
    "/api/v1/admin/roles",
)


#: M2 实现后的**全部**接口面（50 个）
M2_TOTAL_ENDPOINTS: frozenset[tuple[str, str]] = (
    M1_ONLY_ENDPOINTS | M2_ENDPOINTS
)


__all__ = [
    "M1_ONLY_ENDPOINTS",
    "M2_ENDPOINTS",
    "M2_TOTAL_ENDPOINTS",
    "M3_FORBIDDEN_PREFIXES",
    "NON_SPA_PREFIXES",
    "PASSWORD_GATE_EXEMPT_PREFIXES",
    "PUBLIC_ENDPOINTS",
]
