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

__all__ = [
    "NON_SPA_PREFIXES",
    "PASSWORD_GATE_EXEMPT_PREFIXES",
    "PUBLIC_ENDPOINTS",
]
