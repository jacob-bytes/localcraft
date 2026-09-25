"""守卫测试：防「新接口忘加权限」（docs/03 §6.2 明确要求）。

遍历 `app.routes`，断言：

1. 每个路由要么在 `PUBLIC_ENDPOINTS` 白名单里，要么**挂了鉴权依赖**
2. 没有鉴权依赖的路由集合**恰好等于**白名单 —— 新增公开接口必须同步改白名单
3. 除 FR-AUTH-03 的豁免前缀（`/auth/*`、`/meta`、`/healthz`、`/readyz`）外，
   每个路由都被**强制改密拦截**覆盖
4. M1 冻结的 12 个接口一一在列，且没有多出来的接口（契约 §6「只实现这些」）

实现说明：FastAPI 0.141 的 `include_router` 不会把子路由摊平进
`app.routes`，而是留下一个带 `original_router` 的中间对象，
因此必须递归展开（前缀累加）。
"""

from __future__ import annotations

from typing import Any

from fastapi.routing import APIRoute

from app.api.public import (
    M2_TOTAL_ENDPOINTS,
    M3_FORBIDDEN_PREFIXES,
    PASSWORD_GATE_EXEMPT_PREFIXES,
    PUBLIC_ENDPOINTS,
)
from app.core.deps import AUTH_DEP_ATTR, PASSWORD_GATE_ATTR
from app.main import app

HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")


# ---------------------------------------------------------------------------
# 路由遍历
# ---------------------------------------------------------------------------
def iter_api_routes(routes: Any, prefix: str = "") -> list[tuple[str, str, APIRoute]]:
    """递归展开路由树，返回 `(method, full_path, route)`。"""
    collected: list[tuple[str, str, APIRoute]] = []
    for route in routes:
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            include_context = getattr(route, "include_context", None)
            sub_prefix = getattr(include_context, "prefix", "") or ""
            collected.extend(iter_api_routes(original_router.routes, prefix + sub_prefix))
            continue
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods or ()):
            if method in HTTP_METHODS:
                collected.append((method, prefix + route.path, route))
    return collected


def _dependant_tree_flags(route: APIRoute) -> tuple[bool, bool]:
    """在依赖树里找 AUTH_DEP_ATTR / PASSWORD_GATE_ATTR 标记。"""
    has_auth = False
    has_gate = False
    seen: set[int] = set()
    stack = [route.dependant]
    while stack:
        dependant = stack.pop()
        if id(dependant) in seen:
            continue
        seen.add(id(dependant))
        call = dependant.call
        if call is not None:
            if getattr(call, AUTH_DEP_ATTR, False):
                has_auth = True
            if getattr(call, PASSWORD_GATE_ATTR, False):
                has_gate = True
        stack.extend(dependant.dependencies)
    return has_auth, has_gate


#: 前端 SPA 回退路由。它 `include_in_schema=False`，不是 API 的一部分：
#: 只做「静态文件 / index.html」，且对 `api/`、`healthz` 等前缀显式抛 404
#: （docs/03 §6.5）。因此它不参与「API 面」的断言，单独在下面验证。
SPA_FALLBACK_PATH = "/{full_path:path}"


def _api_routes() -> list[tuple[str, str, APIRoute]]:
    """只保留真正的 API 路由（排除 SPA 回退与文档路由）。"""
    return [
        (method, path, route)
        for method, path, route in iter_api_routes(app.routes)
        if path != SPA_FALLBACK_PATH and route.include_in_schema
    ]


ALL_ROUTES = _api_routes()


def test_route_walk_finds_all_m1_endpoints() -> None:
    """遍历器本身要可靠 —— 否则守卫测试会变成空转。"""
    assert len(ALL_ROUTES) >= 12, f"只遍历到 {len(ALL_ROUTES)} 个路由，遍历器可能失效"


def test_every_route_is_either_public_or_authenticated() -> None:
    unguarded: list[str] = []
    for method, path, route in ALL_ROUTES:
        if (method, path) in PUBLIC_ENDPOINTS:
            continue
        has_auth, _ = _dependant_tree_flags(route)
        if not has_auth:
            unguarded.append(f"{method} {path}")

    assert not unguarded, (
        "以下路由既不在公开白名单里，又没有挂鉴权依赖：\n  "
        + "\n  ".join(unguarded)
        + "\n请显式声明 require_role / require_scope / get_current_user / portal_access。"
    )


def test_public_endpoint_list_matches_reality() -> None:
    """反向断言：没挂鉴权的路由**必须**都在白名单里，且白名单不能有僵尸项。"""
    actually_public = {
        (method, path)
        for method, path, route in ALL_ROUTES
        if not _dependant_tree_flags(route)[0]
    }
    assert actually_public == set(PUBLIC_ENDPOINTS), (
        "公开端点白名单与实际不符。\n"
        f"  实际公开但不在白名单: {sorted(actually_public - set(PUBLIC_ENDPOINTS))}\n"
        f"  在白名单但实际有鉴权: {sorted(set(PUBLIC_ENDPOINTS) - actually_public)}"
    )


def test_password_gate_covers_all_non_exempt_routes() -> None:
    """FR-AUTH-03：除豁免前缀外，每个路由都必须被强制改密拦截覆盖。"""
    uncovered: list[str] = []
    for method, path, route in ALL_ROUTES:
        if path.startswith(PASSWORD_GATE_EXEMPT_PREFIXES):
            continue
        _, has_gate = _dependant_tree_flags(route)
        if not has_gate:
            uncovered.append(f"{method} {path}")

    assert not uncovered, (
        "以下路由没有强制改密拦截，must_change_password=true 的用户可以绕过：\n  "
        + "\n  ".join(uncovered)
    )


def test_password_gate_exempt_prefixes_are_exactly_the_documented_ones() -> None:
    """豁免前缀就是 FR-AUTH-03 写的那几个，不能悄悄扩大。"""
    assert set(PASSWORD_GATE_EXEMPT_PREFIXES) == {
        "/api/v1/auth/",
        "/api/v1/meta",
        "/healthz",
        "/readyz",
    }


# ---------------------------------------------------------------------------
# M2 冻结接口清单（契约 §6 + §6.1）
# ---------------------------------------------------------------------------
def test_m2_surface_is_exactly_the_frozen_list() -> None:
    """契约 §6.1：M1 的 12 个 + M2 的 38 个 = **50**。

    多出或缺少任何一个都应该在这里失败，逼迫走契约 §9 的变更流程。
    """
    actual = {(method, path) for method, path, _ in ALL_ROUTES}
    assert len(M2_TOTAL_ENDPOINTS) == 50, "冻结清单本身应当是 50 条"
    assert actual == set(M2_TOTAL_ENDPOINTS), (
        f"多出的接口（契约未冻结）: {sorted(actual - set(M2_TOTAL_ENDPOINTS))}\n"
        f"缺失的接口: {sorted(set(M2_TOTAL_ENDPOINTS) - actual)}"
    )


def test_spa_fallback_is_the_only_non_api_catch_all() -> None:
    """SPA 回退只能有一个，且必须由 `_mount_spa` 注册、不参与 API 面。"""
    catch_alls = [
        (method, path, route)
        for method, path, route in iter_api_routes(app.routes)
        if path == SPA_FALLBACK_PATH
    ]
    for method, _path, route in catch_alls:
        assert method == "GET"
        assert route.include_in_schema is False, "SPA 回退不应出现在 OpenAPI 里"
    # 无论 web/dist 在不在，未匹配的 API 路径都必须是 JSON 404。
    # （web/dist 不存在时由框架默认 404 兜底，也存在时由 catch-all 抛 DomainError）
    assert all(not path.startswith("/api") for _m, path, _r in catch_alls)


def test_no_m3_endpoints_in_m2() -> None:
    """M2 的边界（契约 §6.1「M2 不做」）。

    M1 时代的 `test_no_admin_or_write_endpoints_in_m1` 会被 M2 **合法地打破**
    （工具详情与写接口本来就是 M2 的交付物），所以它在这里被替换成 M3 版本的断言。
    """
    leaked: list[str] = []
    for _method, path, _route in ALL_ROUTES:
        for prefix in M3_FORBIDDEN_PREFIXES:
            if path.startswith(prefix):
                leaked.append(path)
                break
    assert not leaked, f"M2 不应包含 M3 的接口: {sorted(set(leaked))}"


def test_m2_portal_routes_are_read_only_except_ticket() -> None:
    """门户侧只有 `download-ticket` 是写动作（签发票据），其余都是只读。"""
    write_methods = {"POST", "PUT", "PATCH", "DELETE"}
    for method, path, _route in ALL_ROUTES:
        if not path.startswith("/api/v1/tools"):
            continue
        if method in write_methods:
            assert path.endswith("/download-ticket"), f"门户侧意外的写接口: {method} {path}"


def test_all_write_endpoints_declare_a_write_scope() -> None:
    """写接口必须走 `tools:write` / `approvals:write` / `settings:write` 守卫。

    这条防的是「新加的写接口只挂了 get_current_user」——
    那样 API Token 会绕过 scope 限制。
    """
    write_methods = {"POST", "PUT", "PATCH", "DELETE"}
    exempt = {
        # 认证类：登录/刷新/登出/改密是自助操作，不走业务 scope
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
        "/api/v1/auth/change-password",
    }
    for method, path, route in ALL_ROUTES:
        if method not in write_methods or path in exempt:
            continue
        _, has_gate = _dependant_tree_flags(route)
        assert has_gate, f"写接口缺少强制改密拦截: {method} {path}"
        has_auth, _ = _dependant_tree_flags(route)
        assert has_auth, f"写接口缺少鉴权依赖: {method} {path}"
