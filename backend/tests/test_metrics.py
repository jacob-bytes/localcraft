"""B3：`/metrics` 端点的 HTTP 层验证（任务书 B3 的六条硬要求）。

这一层用**真实的 HTTP 请求**（`httpx.ASGITransport`）验证状态码，
而不是只读代码推断 —— 任务书特别点名「确认新增路由不会被现有的强制认证
中间件拦成 401 而无法按规则工作，这是本次最容易踩的集成问题」。

关于「强制认证中间件」的结论
----------------------------

本项目的鉴权**不是中间件**，而是每个路由显式声明的 FastAPI 依赖
（`app/core/deps.py` 的模块 docstring 与 `tests/test_guard.py` 的守卫都建立
在这个事实上）。`app/main.py` 只挂了两个中间件：

- `RequestContextMiddleware`（请求 ID + 访问日志 + 指标聚合）—— 不鉴权
- `SecurityHeadersMiddleware`（纯 ASGI，只补响应头）—— 不鉴权

所以 `/metrics` 不会「被中间件拦掉」。但它必须有**自己的**准入依赖，
否则会变成一个匿名可读的端点 —— 本文件用四种来源组合把它钉住。
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from app.api.metrics import LOOPBACK_HOSTS
from app.core import metrics as metrics_module
from app.core.config import settings
from app.core.security_headers import SecurityHeadersMiddleware
from app.main import RequestContextMiddleware, app


@pytest.fixture
def metrics_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """一个「已启用 metrics」的应用，装配方式与 `app/main.py` 一致。

    为什么不直接改 `app`：`app` 是模块级单例，`app.routes` 一旦被改就会
    污染同进程的其它测试（守卫测试会数出 94 个操作）。这里另建一个只挂了
    指标路由的应用，装配顺序与生产一致（含两个中间件），
    既验证了集成，也不动全局状态。
    """
    from fastapi import Request

    from app.api.metrics import router as metrics_router
    from app.core.errors import DomainError
    from app.main import domain_error_handler

    monkeypatch.setattr(settings, "metrics_enabled", True)
    application = FastAPI()
    application.add_middleware(RequestContextMiddleware)
    application.add_middleware(SecurityHeadersMiddleware)
    # 与生产一致：`DomainError` 收敛成契约 §4.3 的错误信封。
    # 少了这一层，401 会变成 FastAPI 默认的 `{"detail": ...}`，
    # 那就不是在验证「真实的拒绝长什么样」了。
    application.add_exception_handler(DomainError, domain_error_handler)
    application.include_router(metrics_router)

    # 一个无害的探针路由（生产里对应 `/healthz`）：
    # 用来验证「指标聚合是按路由模板记的」，不用去动真实的 app。
    # 注意**不写返回值注解** —— 函数内定义的类型注解在没有
    # `from __future__ import annotations` 的模块里会被 FastAPI 解析成
    # 字符串 ForwardRef，生成 openapi 时直接报 PydanticUserError。
    @application.get("/probe", include_in_schema=False)
    async def probe(request: Request):
        del request
        return PlainTextResponse("ok")

    return application


def _client(application: FastAPI, host: str) -> httpx.AsyncClient:
    """构造一个来源地址可控的客户端。

    `client=(host, port)` 是 httpx `ASGITransport` 的参数，
    它决定了 `request.client.host` —— 也就是 `/metrics` 判定「是不是本机」的依据。
    """
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application, client=(host, 12345)),
        base_url="http://testserver",
    )


# ---------------------------------------------------------------------------
# 硬要求 3：默认关闭时根本不注册
# ---------------------------------------------------------------------------
async def test_metrics_is_not_registered_by_default() -> None:
    """默认（`LOCALCRAFT_METRICS_ENABLED` 未设）时 `/metrics` 不存在 → 404。"""
    from app.main import app as real_app

    assert settings.metrics_enabled is False, "测试环境里 metrics 不应默认开启"
    assert not any(getattr(r, "path", None) == "/metrics" for r in real_app.routes)

    transport = httpx.ASGITransport(app=real_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/metrics")
    assert response.status_code == 404, (
        "关闭状态下 /metrics 必须 404（不该暴露自己的存在）。"
        f"实测 {response.status_code}"
    )


async def test_metrics_disabled_returns_json_404_even_with_spa_dist() -> None:
    """关闭状态必须是 **JSON 404**，不能因为前端产物存在就返回 index.html。

    `NON_SPA_PREFIXES` 里加上 `metrics` 就是为了这一条：
    否则同一个请求的状态码会取决于 `web/dist` 在不在，
    本机能跑、CI 上结果不同。
    """
    from app.main import app as real_app

    transport = httpx.ASGITransport(app=real_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/metrics")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json"), (
        "关闭状态下 /metrics 应返回 JSON 404，而不是前端 index.html"
    )
    assert response.json()["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# 硬要求 4：四种来源 / 凭证组合的真实状态码
# ---------------------------------------------------------------------------
async def test_metrics_local_source_without_token_is_allowed(
    metrics_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """本机来源 + 未配置 token → 200。"""
    monkeypatch.setattr(settings, "metrics_token", "")
    async with _client(metrics_app, "127.0.0.1") as client:
        response = await client.get("/metrics")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/plain")
    assert "localcraft_requests_total" in response.text
    assert response.text.startswith("# HELP ") or "# HELP " in response.text


async def test_metrics_remote_source_without_token_is_rejected(
    metrics_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """非本机来源 + 未配置 token → **401**，绝不裸奔。"""
    monkeypatch.setattr(settings, "metrics_token", "")
    async with _client(metrics_app, "10.1.2.3") as client:
        response = await client.get("/metrics")
    assert response.status_code == 401, (
        f"非本机来源且未配置 token 时必须拒绝，实测 {response.status_code}"
    )
    assert response.json()["code"] == "UNAUTHENTICATED"
    assert "localcraft_" not in response.text, "拒绝的响应里绝不能带指标内容"


async def test_metrics_with_correct_token_from_remote_is_allowed(
    metrics_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """非本机来源 + 正确 token → 200。"""
    monkeypatch.setattr(settings, "metrics_token", "s3cret-token-value")
    async with _client(metrics_app, "10.1.2.3") as client:
        response = await client.get(
            "/metrics", headers={"Authorization": "Bearer s3cret-token-value"}
        )
    assert response.status_code == 200, response.text
    assert "localcraft_uptime_seconds" in response.text


async def test_metrics_with_wrong_token_is_rejected(
    metrics_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """配了 token 时，错误的 token 必须拒绝 —— **本机来源也不例外**。

    这是刻意的取舍：一旦显式配置了 token，就说明管理员选择用凭证而不是
    来源地址来控访问。此时连本机也要带对 token，避免「以为锁了其实没锁」。
    """
    monkeypatch.setattr(settings, "metrics_token", "s3cret-token-value")
    for host in ("10.1.2.3", "127.0.0.1"):
        async with _client(metrics_app, host) as client:
            wrong = await client.get(
                "/metrics", headers={"Authorization": "Bearer not-the-token"}
            )
            missing = await client.get("/metrics")
        assert wrong.status_code == 401, f"{host} 用错 token 竟然放行了"
        assert missing.status_code == 401, f"{host} 不带 token 竟然放行了"


async def test_metrics_rejects_non_bearer_scheme(
    metrics_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Basic` / 裸 token 等非 Bearer 形式不接受（否则「凭证格式」形同虚设）。"""
    monkeypatch.setattr(settings, "metrics_token", "s3cret-token-value")
    async with _client(metrics_app, "10.1.2.3") as client:
        for header in ("Basic s3cret-token-value", "s3cret-token-value", "Bearer "):
            response = await client.get("/metrics", headers={"Authorization": header})
            assert response.status_code == 401, f"{header!r} 竟然放行了"


def test_loopback_set_is_explicit() -> None:
    """本机来源清单是显式的，不含 `0.0.0.0` 这类会误放行的写法。"""
    assert "127.0.0.1" in LOOPBACK_HOSTS
    assert "0.0.0.0" not in LOOPBACK_HOSTS
    assert "" not in LOOPBACK_HOSTS


# ---------------------------------------------------------------------------
# 环境变量的**读取方式**（M7 实测踩到的坑，必须钉住）
# ---------------------------------------------------------------------------
def test_metrics_env_vars_are_actually_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """`LOCALCRAFT_METRICS_ENABLED` / `_TOKEN` 必须真的被 Settings 读到。

    **这条测试的由来**：pydantic-settings 默认只按**字段名**匹配环境变量，
    不做前缀映射 —— 字段 `metrics_enabled` 只认 `METRICS_ENABLED`，
    **不认** `LOCALCRAFT_METRICS_ENABLED`。而 `extra="ignore"` 会把不认识的
    名字**静默吞掉**，于是「按文档设了 `LOCALCRAFT_METRICS_ENABLED=true`，
    重启后 `/metrics` 仍然 404」，且日志里一句提示都没有。

    本轮在真实 uvicorn 上实际踩到了这个坑，用显式 `AliasChoices` 修好，
    这里钉住它不再退化。
    """
    from app.core.config import Settings

    monkeypatch.setenv("LOCALCRAFT_METRICS_ENABLED", "true")
    monkeypatch.setenv("LOCALCRAFT_METRICS_TOKEN", "from-env-token")
    monkeypatch.setenv("LOCALCRAFT_METRICS_SAMPLE_MAX", "77")
    monkeypatch.setenv("SLOW_REQUEST_MS", "123")
    # `_env_file=None` 避免真实 `.env` 干扰（本仓库没有，但不该依赖这个事实）
    fresh = Settings(_env_file=None)  # type: ignore[call-arg]
    assert fresh.metrics_enabled is True, (
        "LOCALCRAFT_METRICS_ENABLED 没被读到 —— 这正是被记录过的坑"
    )
    assert fresh.metrics_token == "from-env-token"
    assert fresh.metrics_sample_max == 77
    assert fresh.slow_request_ms == 123


def test_metrics_env_vars_accept_unprefixed_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """不带 `LOCALCRAFT_` 的别名也认 —— 写错名字最坏的结果是静默无效。"""
    from app.core.config import Settings

    monkeypatch.delenv("LOCALCRAFT_METRICS_ENABLED", raising=False)
    monkeypatch.setenv("METRICS_ENABLED", "true")
    fresh = Settings(_env_file=None)  # type: ignore[call-arg]
    assert fresh.metrics_enabled is True


# ---------------------------------------------------------------------------
# 硬要求 1、2：不在冻结面内、不进 openapi.json
# ---------------------------------------------------------------------------
def test_metrics_not_in_openapi(metrics_app: FastAPI) -> None:
    """`/metrics` 不得出现在 OpenAPI 里（前端 `check:api-types` 的输入）。"""
    schema = metrics_app.openapi()
    assert "/metrics" not in schema["paths"], (
        "/metrics 进了 openapi.json —— 前端 check:api-types 会读它生成类型，"
        "运维端点不该造成这次前后端耦合"
    )
    # 反向确认路由**确实注册了**（否则上面那条断言会在「路由没挂」时空转通过）。
    # 必须用 `iter_api_routes` 递归展开：FastAPI 0.141 的 `include_router`
    # **不把子路由摊平进 `app.routes`**，而是留下一个带 `original_router`
    # 的中间对象（`tests/test_guard.py` 的遍历器就是为此写的）。
    from tests.test_guard import iter_api_routes

    paths = {path for _m, path, _r in iter_api_routes(metrics_app.routes)}
    assert "/metrics" in paths, f"指标路由没注册上，实得 {sorted(paths)}"


def test_metrics_route_is_flagged_include_in_schema_false(metrics_app: FastAPI) -> None:
    from fastapi.routing import APIRoute

    from tests.test_guard import iter_api_routes

    routes = [
        route
        for _m, path, route in iter_api_routes(metrics_app.routes)
        if isinstance(route, APIRoute) and path == "/metrics"
    ]
    assert routes, "指标路由没找到"
    assert all(r.include_in_schema is False for r in routes)


def test_metrics_is_outside_the_frozen_api_prefix() -> None:
    """硬要求 1：位于 `/api/v1` 冻结面之外，不计入 99 operations / 79 paths（M8 后）。"""
    from app.api.metrics import router as metrics_router

    for route in metrics_router.routes:
        assert not route.path.startswith("/api/v1"), (
            f"{route.path} 落在冻结前缀里，会改变 99 操作的基线"
        )


def test_metrics_has_its_own_guard_dependency() -> None:
    """`/metrics` 必须自带准入依赖 —— 它不在 `PUBLIC_ENDPOINTS` 白名单里。

    注意本项目 `tests/test_guard.py` 的守卫只遍历 `include_in_schema=True`
    的路由，因此**看不见** `/metrics`。这条测试补上那个盲区。
    """
    from fastapi.routing import APIRoute

    from app.api.metrics import metrics_access
    from app.api.metrics import router as metrics_router

    routes = [r for r in metrics_router.routes if isinstance(r, APIRoute)]
    assert routes, "指标路由没找到"
    calls = [
        d.call for d in routes[0].dependant.dependencies  # type: ignore[attr-defined]
    ]
    assert metrics_access in calls, "指标路由没有挂准入依赖（会变成匿名可读）"


# ---------------------------------------------------------------------------
# 硬要求 6 + 内容正确性：文本格式与快照
# ---------------------------------------------------------------------------
async def test_metrics_body_is_parseable_prometheus_text(
    metrics_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """真实 HTTP 响应体必须能被最小解析器读懂，且指标确实随请求增长。"""
    import re

    monkeypatch.setattr(settings, "metrics_token", "")
    metrics_module.reset()
    async with _client(metrics_app, "127.0.0.1") as client:
        await client.get("/probe")
        await client.get("/probe")
        response = await client.get("/metrics")

    assert response.status_code == 200
    parsed: dict[str, float] = {}
    labelled: dict[str, list[str]] = {}
    for line in response.text.splitlines():
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{.*\})? +([0-9eE.+-]+)$", line)
        assert match, f"无法解析的指标行：{line!r}"
        if match.group(2):
            labelled.setdefault(match.group(1), []).append(match.group(2) + f" {match.group(3)}")
        else:
            parsed[match.group(1)] = float(match.group(3))

    # 上面两次 `/probe` 应当已按**路由模板**计入。
    # 请求数指标一定带 label（method/route），所以查 `labelled` 而不是 `parsed`。
    assert "localcraft_requests_total" in labelled, f"缺请求数指标，实得 {sorted(labelled)}"
    probe_samples = labelled["localcraft_requests_total"]
    assert any('route="/probe"' in s for s in probe_samples), (
        f"/probe 的请求没被计入，实得 {probe_samples}"
    )
    assert any(" 2" in s for s in probe_samples if 'route="/probe"' in s), (
        f"/probe 的请求数应为 2，实得 {probe_samples}"
    )
    # WAL 指标按**方言**断言（M10 修：原先无条件要求存在，PG 上正确不输出）。
    # SQLite：必须存在 —— 它是 docs/02 §7 里唯一能反映 -wal 旁路文件膨胀的观测口；
    # PG：必须**不存在** —— PG 的 WAL 是集群级概念（pg_wal 目录），既不归属某个
    #   连接也不归属某个应用，`wal_size_bytes()` 返回 None 才是正确行为。
    #   反过来断言「不存在」同样有意义：它能抓住「把 SQLite 专属指标在 PG 上也发出来」
    #   这种假信号（运维会以为在观测 PG 的 WAL，实际读到的是别的数字/0）。
    if settings.is_sqlite:
        assert "localcraft_sqlite_wal_bytes" in parsed, "SQLite 方言下应输出 WAL 大小"
    else:
        assert "localcraft_sqlite_wal_bytes" not in parsed, (
            "非 SQLite 方言（PostgreSQL）下不应输出 SQLite 专属的 WAL 指标"
        )
    assert parsed["localcraft_slow_request_threshold_ms"] == settings.slow_request_ms
    metrics_module.reset()


def test_metrics_route_would_not_break_the_frozen_surface() -> None:
    """把指标路由加进真实 app 后，冻结面仍然是 99（`include_in_schema=False`）。

    M8 起冻结面是 99（93 + 契约 §23.4 的 6 个）。
    """
    from app.api.metrics import router as metrics_router
    from tests.test_guard import iter_api_routes

    before = [
        (m, p)
        for m, p, route in iter_api_routes(app.routes)
        if getattr(route, "include_in_schema", False)
    ]
    assert len(before) == 99, f"冻结面应为 99，实际 {len(before)}"

    probe = FastAPI()
    probe.include_router(metrics_router)
    after = [
        (m, p)
        for m, p, route in iter_api_routes(probe.routes)
        if getattr(route, "include_in_schema", False)
    ]
    assert after == [], (
        "指标路由出现在「参与 API 面」的集合里 —— 冻结面会被它撑破：" f"{after}"
    )
