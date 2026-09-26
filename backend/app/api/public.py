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
    # M7：`/metrics` 默认不注册（LOCALCRAFT_METRICS_ENABLED=false）。
    # 若不加这一条，关闭状态下的 `GET /metrics` 会在 `web/dist` 存在时
    # 返回 **index.html（200）**，在 dist 不存在时返回 JSON 404 ——
    # 同一个请求的状态码取决于前端构建产物在不在，正是
    # `_SpaFallbackRoute` 的 docstring 里警告过的那类不一致。
    "metrics",
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

#: M2 实现后的接口面（50 个）= M1 的 12 + M2 的 38
M2_TOTAL_ENDPOINTS: frozenset[tuple[str, str]] = M1_ONLY_ENDPOINTS | M2_ENDPOINTS


#: M3 新增的 42 个接口（contracts §6.2 / docs/03 §2.5）。
#: 实现后接口面总数 = 12（M1）+ 38（M2）+ 42（M3）= **92**。
M3_ENDPOINTS: frozenset[tuple[str, str]] = frozenset(
    {
        # ---- 用户与角色（8）----
        ("GET", "/api/v1/admin/users"),
        ("POST", "/api/v1/admin/users"),
        ("GET", "/api/v1/admin/users/{user_id}"),
        ("PATCH", "/api/v1/admin/users/{user_id}"),
        ("POST", "/api/v1/admin/users/{user_id}/reset-password"),
        ("PUT", "/api/v1/admin/users/{user_id}/roles"),
        ("POST", "/api/v1/admin/users/{user_id}/revoke-sessions"),
        ("GET", "/api/v1/admin/roles"),
        # ---- 用户组（7）----
        ("GET", "/api/v1/admin/groups"),
        ("POST", "/api/v1/admin/groups"),
        ("PATCH", "/api/v1/admin/groups/{group_id}"),
        ("DELETE", "/api/v1/admin/groups/{group_id}"),
        ("GET", "/api/v1/admin/groups/{group_id}/members"),
        ("POST", "/api/v1/admin/groups/{group_id}/members"),
        ("DELETE", "/api/v1/admin/groups/{group_id}/members/{user_id}"),
        # ---- 分类与标签（9）----
        ("GET", "/api/v1/admin/categories"),
        ("POST", "/api/v1/admin/categories"),
        ("PATCH", "/api/v1/admin/categories/{category_id}"),
        ("DELETE", "/api/v1/admin/categories/{category_id}"),
        ("PUT", "/api/v1/admin/categories/order"),
        ("GET", "/api/v1/admin/tags"),
        ("PATCH", "/api/v1/admin/tags/{tag_id}"),
        ("POST", "/api/v1/admin/tags/merge"),
        ("POST", "/api/v1/admin/tags/cleanup"),
        # ---- 全站工具与回收站（7）----
        ("GET", "/api/v1/admin/tools"),
        ("POST", "/api/v1/admin/tools"),
        ("POST", "/api/v1/admin/tools/{tool_id}/versions"),
        ("POST", "/api/v1/admin/tools/{tool_id}/transfer"),
        ("POST", "/api/v1/admin/tools/{tool_id}/restore"),
        ("DELETE", "/api/v1/admin/tools/{tool_id}/purge"),
        ("GET", "/api/v1/admin/recycle-bin"),
        # ---- API Token（4）----
        ("GET", "/api/v1/admin/tokens"),
        ("POST", "/api/v1/admin/tokens"),
        ("POST", "/api/v1/admin/tokens/{token_id}/revoke"),
        ("DELETE", "/api/v1/admin/tokens/{token_id}"),
        # ---- 统计（3）----
        ("GET", "/api/v1/admin/overview"),
        ("GET", "/api/v1/admin/stats/tools"),
        ("GET", "/api/v1/admin/stats/storage"),
        # ---- 导入导出（4）----
        ("POST", "/api/v1/admin/import/users"),
        ("GET", "/api/v1/admin/export/users"),
        ("POST", "/api/v1/admin/import/tools"),
        ("GET", "/api/v1/admin/export/tools"),
    }
)


#: M6 的接口面。
#:
#: **只有 1 个，且是对 92 冻结的刻意例外**（contracts/CONTRACT.md §20.4③）：
#: `GET /api/v1/directory` 用于 ACL 授权时搜索用户/用户组。
#: 没有它，`docs/01` FR-ACL-02（P0）「搜索用户/组后添加」无法交付 ——
#: 普通用户只能手填数字 ID，功能实际不可用。这属于**功能空洞修复**，
#: 不是范围蔓延，因此破例；除此之外 M6 不新增任何接口。
M6_ENDPOINTS: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/v1/directory"),
    }
)

#: M8 新增的 6 个接口（contracts §23.4）。
#:
#: 收藏（2）+ 点赞（2）+ 我的收藏（1）+ 管理数字概览增强（1）= **6**。
#: 实现后接口面总数 = 93（M6 后）+ 6 = **99**（契约 §23.4「93 → 99」）。
M8_ENDPOINTS: frozenset[tuple[str, str]] = frozenset(
    {
        # ---- 收藏（2）----
        ("PUT", "/api/v1/tools/{slug}/favorite"),
        ("DELETE", "/api/v1/tools/{slug}/favorite"),
        # ---- 点赞（2）---- 形状与收藏完全对称
        ("PUT", "/api/v1/tools/{slug}/like"),
        ("DELETE", "/api/v1/tools/{slug}/like"),
        # ---- 我的收藏（1）----
        ("GET", "/api/v1/me/favorites"),
        # ---- 管理数字概览增强（1）----
        ("GET", "/api/v1/admin/stats/insights"),
    }
)


#: M6 实现后的**全部**接口面（93 = 92 + directory）。**多一个都不许有** ——
#: docs/03 §2.5 未列出的路径不得出现（守卫测试反向断言）。
#:
#: **M8 起这个名字继续沿用**（虽然字面还叫 M3_TOTAL）：它是被
#: `test_cli.py` / `test_guard.py` 直接引用的冻结面常量，改名要动多处引用，
#: 收益只是名字好看。现在它等于「M8 后的全部接口面」= **99**。
M3_TOTAL_ENDPOINTS: frozenset[tuple[str, str]] = (
    M2_TOTAL_ENDPOINTS | M3_ENDPOINTS | M6_ENDPOINTS | M8_ENDPOINTS
)


__all__ = [
    "M1_ONLY_ENDPOINTS",
    "M2_ENDPOINTS",
    "M2_TOTAL_ENDPOINTS",
    "M3_ENDPOINTS",
    "M3_TOTAL_ENDPOINTS",
    "M6_ENDPOINTS",
    "M8_ENDPOINTS",
    "NON_SPA_PREFIXES",
    "PASSWORD_GATE_EXEMPT_PREFIXES",
    "PUBLIC_ENDPOINTS",
]
