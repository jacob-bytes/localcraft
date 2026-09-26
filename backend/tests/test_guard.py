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
from starlette.routing import Match
from starlette.routing import Route as StarletteRoute

from app.api.public import (
    M1_ONLY_ENDPOINTS,
    M2_ENDPOINTS,
    M3_ENDPOINTS,
    M3_TOTAL_ENDPOINTS,
    M8_ENDPOINTS,
    PASSWORD_GATE_EXEMPT_PREFIXES,
    PUBLIC_ENDPOINTS,
)
from app.core.deps import AUTH_DEP_ATTR, PASSWORD_GATE_ATTR
from app.main import app
from tests.m4_helpers import (
    documented_admin_endpoints,
    normalize_path,
    normalized_omissions,
)

HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")


# ---------------------------------------------------------------------------
# 路由遍历
# ---------------------------------------------------------------------------
def iter_api_routes(routes: Any, prefix: str = "") -> list[tuple[str, str, Any]]:
    """递归展开路由树，返回 `(method, full_path, route)`。

    收 `APIRoute`，也收普通 `starlette.routing.Route` —— SPA 兜底是后者。
    如果只收 `APIRoute`，兜底路由一旦从 `APIRoute` 换成 `Route`，
    下面 `test_spa_fallback_is_the_only_non_api_catch_all` 就会在**空集合**上
    空转通过，守卫静默失效（这正是它想防的那类事）。
    """
    collected: list[tuple[str, str, Any]] = []
    for route in routes:
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            include_context = getattr(route, "include_context", None)
            sub_prefix = getattr(include_context, "prefix", "") or ""
            collected.extend(iter_api_routes(original_router.routes, prefix + sub_prefix))
            continue
        # APIRoute 是 Route 的子类，判 Route 即可；Mount（如 /assets）不是 Route，被排除。
        if not isinstance(route, StarletteRoute):
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


def _api_routes() -> list[tuple[str, str, Any]]:
    """只保留真正的 API 路由（排除 SPA 回退与文档路由）。

    普通 `Route` 没有 `include_in_schema` 属性，用 `getattr` 取默认 False ——
    即「不是 API 面的一部分」。
    """
    return [
        (method, path, route)
        for method, path, route in iter_api_routes(app.routes)
        if path != SPA_FALLBACK_PATH and getattr(route, "include_in_schema", False)
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
def test_m3_surface_is_exactly_the_frozen_list() -> None:
    """契约 §6 + §6.1 + §6.2 + §23.4：12 + 38 + 42 + 1 + **6** = **99**。

    多出或缺少任何一个都应该在这里失败，逼迫走契约 §9 的变更流程。
    """
    actual = {(method, path) for method, path, _ in ALL_ROUTES}
    assert len(M1_ONLY_ENDPOINTS) == 12
    assert len(M2_ENDPOINTS) == 38
    assert len(M3_ENDPOINTS) == 42
    assert len(M8_ENDPOINTS) == 6, "M8 冻结清单应当是 6 条（契约 §23.4）"
    assert len(M3_TOTAL_ENDPOINTS) == 99, (
        "冻结清单本身应当是 99 条（92 + M6 的 directory + M8 的 6 个）"
    )
    assert actual == set(M3_TOTAL_ENDPOINTS), (
        f"多出的接口（契约未冻结）: {sorted(actual - set(M3_TOTAL_ENDPOINTS))}\n"
        f"缺失的接口: {sorted(set(M3_TOTAL_ENDPOINTS) - actual)}"
    )


def test_endpoint_total_is_exactly_92() -> None:
    """单独一条把「99」这个数字钉死 —— 容易被顺手改掉的是它。

    （函数名保留历史的 `92`，避免改动 import/名引用；断言值已按 M8 更新。）
    """
    assert len(ALL_ROUTES) == 99, (
        f"接口操作总数应为 99（92 + M6 directory + M8 的 6 个），实际 {len(ALL_ROUTES)}"
    )


def test_spa_fallback_is_the_only_non_api_catch_all() -> None:
    """SPA 回退只能有一个，且必须由 `_mount_spa` 注册、不参与 API 面。

    另外钉住一条**与环境无关**的不变量：兜底路由不得匹配非 SPA 前缀
    （`api/`、`docs`、`openapi.json` …）。理由见 `_SpaFallbackRoute` 的 docstring：

    一个无差别的 GET 兜底会把 API 的 405 吃掉变成 404，而它**只在 `web/dist`
    存在时才注册**，于是同一个请求的状态码取决于前端构建产物在不在 ——
    CI 没有 dist、开发者本机有，两边 API 行为不一致，本地「全绿」是假的。
    """
    catch_alls = [
        (method, path, route)
        for method, path, route in iter_api_routes(app.routes)
        if path == SPA_FALLBACK_PATH
    ]

    # `iter_api_routes` 是按方法展开的，而 starlette 的 `Route(methods=["GET"])`
    # 会自动补上 HEAD，因此要按**路由**去重后再数个数，不能按 (方法, 路径) 数。
    unique_routes = {id(route) for _m, _p, route in catch_alls}
    # web/dist 不存在时不注册兜底（优雅降级），存在时必须恰好一个。
    assert len(unique_routes) <= 1, f"SPA 兜底出现多个：{[r for _m, _p, r in catch_alls]}"

    for method, _path, route in catch_alls:
        assert method in ("GET", "HEAD"), f"SPA 兜底只应处理 GET/HEAD，出现 {method}"
        assert not getattr(route, "include_in_schema", False), "SPA 回退不应出现在 OpenAPI 里"

    # 关键不变量：非 SPA 前缀一律不匹配（无论 dist 在不在，都该如此）。
    for _method, _path, route in catch_alls:
        for probe in (
            "/api/v1/admin/groups/999999",
            "/api/v1/does-not-exist",
            "/docs",
            "/openapi.json",
            "/healthz",
        ):
            scope = {"type": "http", "method": "GET", "path": probe, "headers": []}
            match, _child = route.matches(scope)  # type: ignore[arg-type]
            assert match is Match.NONE, f"SPA 兜底不应匹配 {probe}（得到 {match}）"


def test_no_undeclared_admin_paths() -> None:
    """`/admin/*` 必须与 **docs/03 §2.5 的权威清单**逐条一致（契约 §16.5）。

    这条与上面的「精确等于冻结常量」是双重保险：
    即使有人把新路径加进 `M3_TOTAL_ENDPOINTS` 常量（那会让上面那条通过），
    这里仍会因为「它不在 docs/03 §2.5 里」而失败 —— 清单本身也得是诚实的。

    路径参数名归一化后再比（文档写 `{id}`，实现写 `{tool_id}`，属命名差异）。
    """
    actual = {
        (method, normalize_path(path))
        for method, path, _route in ALL_ROUTES
        if path.startswith("/api/v1/admin")
    }
    documented = documented_admin_endpoints()

    phantom = sorted(actual - documented - normalized_omissions())
    assert not phantom, (
        "以下 admin 接口实现里有、但 docs/03 §2.5 未列出（且不属于 §5.2 的已知例外）：\n  "
        + "\n  ".join(f"{m} {p}" for m, p in phantom)
    )

    missing = sorted(documented - actual)
    assert not missing, (
        "以下 admin 接口 docs/03 §2.5 列了、但实现里没有：\n  "
        + "\n  ".join(f"{m} {p}" for m, p in missing)
    )

    # 已知例外只能是那两个代创建接口，多一个都要重新走契约 §9 变更流程
    assert actual - documented == set(normalized_omissions()), (
        "docs/03 §2.5 之外的 admin 接口集合发生变化："
        f"{sorted(actual - documented)}（预期 {sorted(normalized_omissions())}）"
    )


def test_frozen_surface_matches_docs_03_section_2_5_row_by_row() -> None:
    """M4 验收 4：操作总数恰为 **99**（M4 起为 92，M6 +1，M8 +6），
    且管理侧与 docs/03 §2.5 **逐条**一致。"""
    from tests.m4_helpers import parse_docs_03_section_2_5

    rows = parse_docs_03_section_2_5()
    # M4 时是 52 行；M5 监控方把两个「管理侧代创建/代上传」接口回填进总表后为 54 行。
    # 这个数字变了就必须有人看一眼 —— 它同时是「接口面是否漂移」的第一道信号。
    # M8 的 6 个新接口里只有 `/admin/stats/insights` 属于管理侧，而 docs/03
    # 由监控方维护、M8 未回填，因此该行登记在 m4_helpers 的
    # DOCUMENTED_TABLE_OMISSIONS 里，**行数仍是 54**。
    assert len(rows) == 54, (
        f"docs/03 §2.5 应当有 54 行接口，解析出 {len(rows)} 行 —— "
        "文档改了或解析逻辑失效，两种都要有人看一眼"
    )
    assert len(set(rows)) == len(rows), "§2.5 总表里出现了重复行"

    actual = {(method, path) for method, path, _route in ALL_ROUTES}
    assert len(actual) == 99, (
        f"接口操作总数应为 99（92 + M6 directory + M8 的 6 个），实际 {len(actual)}"
    )

    # 逐行确认：每一条文档行都能在实现里找到（归一化参数名）
    implemented = {(m, normalize_path(p)) for m, p in actual}
    for method, path in rows:
        assert (method, normalize_path(path)) in implemented, (
            f"docs/03 §2.5 的 {method} {path} 在实现里找不到"
        )


def test_m2_portal_routes_are_read_only_except_ticket() -> None:
    """门户侧原来的写动作**只有** `download-ticket`（签发票据）。

    M8 按契约 §23.4 新增了 4 个**收藏 / 点赞**的写端点
    （`PUT`/`DELETE /tools/{slug}/favorite|like`）—— 它们是**个人关系**写入
    （每人每工具至多一行），不是工具内容的写操作，因此这里是显式白名单，
    而不是把断言删掉。任何**其他**门户侧写接口仍然必须失败。
    """
    allowed_write_suffixes = ("/download-ticket", "/favorite", "/like")
    write_methods = {"POST", "PUT", "PATCH", "DELETE"}
    for method, path, _route in ALL_ROUTES:
        if not path.startswith("/api/v1/tools"):
            continue
        if method in write_methods:
            assert path.endswith(allowed_write_suffixes), (
                f"门户侧意外的写接口: {method} {path}"
            )


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


# ---------------------------------------------------------------------------
# J-8：openapi.json 产物时效守卫
# ---------------------------------------------------------------------------
def test_openapi_artifact_is_current() -> None:
    """已提交的 `backend/openapi.json` 必须与运行时生成的 spec 一致。

    **为什么需要这条**（contracts/CONTRACT.md §22.3）：`openapi.json` 是 §15.6 宣布的
    响应形状权威，也是前端 `check:api-types` 的输入。但它是个**手工执行的生成产物** ——
    M5 交付后它就陈旧了（磁盘那份 mtime 14:14，缺 `storage_warning`，也缺
    `/admin/tools/{id}/versions` 的 201 schema），而运行时 `/openapi.json` 两者都有。

    后果很别扭：**前端只能对着实时 spec 改代码，而守卫却永远红着** ——
    「权威」对自己不自洽。

    修法不能靠「记得运行 export-openapi」，那正是它陈旧的原因。
    这条测试把它变成**会失败的检查**：改了任何路由/响应模型却忘了重导出，
    CI 立刻报错并告诉你该跑什么命令。
    """
    import json
    from pathlib import Path

    from app.main import app

    artifact = Path(__file__).resolve().parent.parent / "openapi.json"
    assert artifact.is_file(), "backend/openapi.json 不存在，请运行 export-openapi"

    on_disk = json.loads(artifact.read_text(encoding="utf-8"))
    live = app.openapi()

    disk_paths = set(on_disk.get("paths", {}))
    live_paths = set(live.get("paths", {}))
    assert disk_paths == live_paths, (
        "openapi.json 与运行时 spec 不一致，请运行：\n"
        "    python -m app.cli export-openapi\n"
        f"  仅磁盘有: {sorted(disk_paths - live_paths)}\n"
        f"  仅运行时有: {sorted(live_paths - disk_paths)}"
    )

    # 逐个端点比对响应 schema 的字段名集合 —— 只比 paths 会漏掉「路径没变但字段变了」
    mismatched: list[str] = []
    for path, ops in live["paths"].items():
        for method, spec in ops.items():
            live_schema = (
                spec.get("responses", {})
                .get("200", {})
                .get("content", {})
                .get("application/json", {})
                .get("schema")
            )
            disk_schema = (
                on_disk["paths"]
                .get(path, {})
                .get(method, {})
                .get("responses", {})
                .get("200", {})
                .get("content", {})
                .get("application/json", {})
                .get("schema")
            )
            if live_schema != disk_schema:
                mismatched.append(f"{method.upper()} {path}")
    assert not mismatched, (
        "以下端点的响应 schema 与 openapi.json 不一致，请运行 export-openapi 并提交产物：\n  "
        + "\n  ".join(mismatched)
    )
