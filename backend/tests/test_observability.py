"""M7 可观测性回归：SQL 条数上限、慢请求日志、`/metrics`。

对应任务书 B1（O12）、B2（M2）、B3（M1）。三条都是「防静默回归」类：
功能测试全绿、响应形状一字不差，问题只在数据里显形。

关于 SQL 条数的**口径**（读断言前必须先读这段）
------------------------------------------------

本文件断言的是 `before_cursor_execute` 事件数，即**应用交给 DBAPI 的语句总数**，
其中包含读系统设置表 `system_settings` 的那批点查。

为什么不把它们排除掉再断言：那需要按 SQL 文本做白名单过滤，
一旦写法变化（加个字段、换个别名）过滤就会漏，于是守卫**静默失效** ——
而「静默失效的守卫」比没有守卫更糟。这里选择把真实全量钉住。

实测构成（单进程 SQLite，`page_size=24`，`before_cursor_execute` 逐条打印出来数的）：

**改前（M7 当时）**

    匿名 `GET /api/v1/tools`（首页，含 facets），进程内的**第一次**请求 —— 20 条
         1  `system_settings` = portal.allow_anonymous_view   （portal_access 依赖）
         4  `system_settings` = security.*                    （get_security_policy）
         1  `system_settings` = portal.allow_anonymous_view   （get_security_policy 里再读一次）
         5  `system_settings` = portal.* / api.docs_enabled   （get_security_policy）
         1  `system_settings` = images.signature_ttl_hours    （get_ttl_hours）
         1  `SELECT count(*)`（total）
         1  列表 SELECT（含 tags 的 selectin 加载）
         2  `user_roles`（关系的 selectin 加载）
         2  facets 聚合 + categories
        ────
        20（**第二次**起 19：`get_ttl_hours` 有 60 秒进程内缓存，
        测试里用 `_warm_up()` 把状态推到稳态再测）

**改后（M18 收尾的「合并设置读取」）**——`system_settings` 点查由 12 条降到 3 条：

  1. `deps.get_visibility_context` 原先调 `get_security_policy()` **只为拿
     `allow_admin_view_private` 一个布尔**（11 条语句换 1 个字段），
     改为只读那一个键（`settings_service.is_admin_private_view_allowed`）；
  2. `get_security_policy` 自身从 11 次点查改为 **1 条 `IN` 查询**
     （`system_settings.get_effective_many`），三层兜底逐字保持不变。
     它已不在本端点路径上，但登录 / 刷新路径仍在调它，同样受益。

实测（同一套 `_warm_up` + `_facets_uncached` harness，改前 / 改后各跑一次）：

| 端点 | 改前 | 改后 |
| --- | --- | --- |
| 门户列表首页（含 facets，稳态） | 19 | **9** |
| 门户列表第 2 页（不算 facets） | 14 | **4** |
| 工具详情 | 20 | **10** |

三条路径**各少 10 条** —— 正是「11 条点查 → 1 条批量」的净差。

为什么值得做：`docs/11` §2.5 的 O4 实验把「CPU 随并发放大」的放大器定位到
**`aiosqlite` 的专用工作线程那一跳**（每条语句跨一次），少发语句 = 少跳。
本文件只钉住**语句数**这个可观测的量；**CPU 收益本轮未实测**。

`docs/11` §2.2 O5 写的是「门户列表当前已知是 4 条查询」—— 那 4 条指的是
**业务查询**（total / list / facets / categories），不含设置点查。
两者不矛盾，但口径必须说清（本文件断言的是含设置点查的全量，因此上限比 4 大）。

原先这里写着「合并设置读取是一条真实存在的优化空间，值得记进 `docs/11`
（监控方裁定）」—— 该裁定已在本轮兑现，`docs/11` §2.1 新增 **O13**。

**仍然不做进程内缓存**：`settings_service` 的取舍是「设置项运行时修改后立即生效
（FR-CFG-01）」，所以每次请求都读表。本轮省的是**语句数**（11 → 1），
不是「少读请求」—— 语义上仍然是每请求都读到最新值。
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager

import httpx
import pytest

from app.core import metrics as metrics_module
from app.core import sql_counter
from app.core.config import settings
from app.db.session import engine

# ---------------------------------------------------------------------------
# 上限常量 —— 集中在这里，改动时必须显式面对「我为什么放宽」
# ---------------------------------------------------------------------------
#: 门户列表**首页**（含 facets）的语句数上限。
#: 实测 **9**（进程级缓存已热，见 `_warm_up`）。取 12 = 9 + 3。
#: 为什么留 3 而不是 0：这几条查询里仍有设置点查（`portal_access` 读
#: `allow_anonymous_view`、可见性上下文读 `allow_admin_view_private`），
#: 数量随「设置读取点」增减；留 3 的余量让「多加一次设置读取」这类无害改动不报警，
#: 而**任何 N+1**（随条目数或结果行数增长的查询）都会立刻越过 12。
#:
#: 历史上这里是 25（实测 19 + 6）。M18 收尾把 11 次设置点查并成 1 条 `IN` 之后
#: 实测降到 9，上限随之下调 —— **上限不跟着实测走就会变成一条越来越松的守卫**：
#: 留着 25 的话，等于给未来加回 16 条语句留了免费额度，那正是它要防的事。
PORTAL_LIST_FIRST_PAGE_MAX_STATEMENTS = 12

#: 门户列表翻页（`page >= 2`，不算 facets）。实测 **4**。上限 7 = 4 + 3。
#: 选上限而不是精确等号的理由同上；这条的主要作用是钉住
#: 「facets 只在第一页算」这一条既有优化不被悄悄改回去。
#: （历史上是 19：实测 14 + 5。）
PORTAL_LIST_LATER_PAGE_MAX_STATEMENTS = 7

#: 工具详情上限。实测 **10**。上限 13 = 10 + 3。（历史上是 25：实测 20 + 5。）
TOOL_DETAIL_MAX_STATEMENTS = 13

#: 不碰数据库的探测端点必须是 0 条语句。
#: 用**精确等号**而不是上限：`/healthz` 的契约就是「只回答进程活着，不碰数据库」
#: （`app/main.py` 的 docstring），给它加任何查询都是契约回归，
#: 没有「合理的多一点」，所以不存在放宽的理由。
HEALTHZ_EXACT_STATEMENTS = 0


def _sql_count(response: httpx.Response) -> int:
    """从响应头取本次请求的语句数。

    依赖 `X-SQL-Count`。**如果哪天计数被关掉，这里会 KeyError 而不是静默返回 0**
    —— 那正是想要的：守卫失效时应当报错，而不是变成一条永远通过的断言。
    """
    raw = response.headers.get("X-SQL-Count")
    assert raw is not None, (
        "响应里没有 X-SQL-Count —— SQL 计数未启用（LOCALCRAFT_SQL_COUNT / "
        "LOCALCRAFT_DEBUG / LOCALCRAFT_METRICS_ENABLED 全为假），"
        "本文件的断言会变成空转。"
    )
    return int(raw)


@contextmanager
def _facets_uncached() -> Iterator[None]:
    """临时关掉 facets 的 30 秒进程内缓存。

    为什么测试里必须做这件事：`_FACET_CACHE` 是**进程级**的，跨用例复用。
    整包跑的时候前面的用例已经把缓存填热了，于是「首页多一次聚合」这个
    差异被缓存吃掉，`page1 > page2` 这种断言会随**测试执行顺序**时红时绿。
    那是最坏的一类测试：它测的是顺序，不是代码。

    `LOCALCRAFT_DISABLE_FACET_CACHE=1` 这个开关本来就是为可验证性准备的
    （见 `app/repositories/tools.py` 的 `_cache_enabled` docstring），
    这里正是它该被用上的场合。
    """
    previous = os.environ.get("LOCALCRAFT_DISABLE_FACET_CACHE")
    os.environ["LOCALCRAFT_DISABLE_FACET_CACHE"] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("LOCALCRAFT_DISABLE_FACET_CACHE", None)
        else:
            os.environ["LOCALCRAFT_DISABLE_FACET_CACHE"] = previous


async def _warm_up(client: httpx.AsyncClient, url: str) -> None:
    """先发一次同样的请求，让**进程级**缓存进入稳态。

    实测：进程里第一次访问门户列表是 **20** 条语句，之后稳定在 **19** ——
    差的 1 条是 `images.signature_ttl_hours` 的设置点查，
    `image_signature_service.get_ttl_hours` 有 60 秒进程内缓存，
    第二次起就不再查了。

    这类「首次多一条」的差异如果不预热掉，断言就会变成在测
    「这个进程此前跑过什么」，而不是在测代码。预热是显式的一步，
    比把上限放宽到覆盖两种情形要诚实。
    """
    response = await client.get(url)
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# B1 · 计数器本身
# ---------------------------------------------------------------------------
def test_counter_hook_is_installed_under_test() -> None:
    """测试环境必须真的挂上了钩子 —— 否则下面所有断言都在空转。"""
    assert sql_counter.counting_enabled(), "测试环境应启用 SQL 计数"
    assert sql_counter.is_installed(engine), "before_cursor_execute 钩子未安装"


def test_install_is_idempotent() -> None:
    """重复安装不得让计数翻倍（这是最容易静默出错的地方）。"""
    assert sql_counter.install_sql_counter(engine) is False, "已安装时不应重复挂"
    assert sql_counter.is_installed(engine)


async def test_counter_counts_are_isolated_per_request(client: httpx.AsyncClient) -> None:
    """计数不跨请求泄漏 —— `ContextVar` 而不是模块级累加的意义就在这里。"""
    first = _sql_count(await client.get("/api/v1/tags"))
    second = _sql_count(await client.get("/api/v1/tags"))
    third = _sql_count(await client.get("/api/v1/tags"))
    assert first == second == third, (
        f"同一端点的语句数应当稳定，实测 {first}/{second}/{third} —— "
        "计数可能被泄漏到下一个请求里"
    )


async def test_counter_is_zero_for_non_database_endpoint(client: httpx.AsyncClient) -> None:
    """`/healthz` 不碰库。"""
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert _sql_count(response) == HEALTHZ_EXACT_STATEMENTS


async def test_counting_can_be_switched_off_and_back() -> None:
    """A/B 对照：摘掉钩子后计数不再增长，装回去又能用。

    这条同时是「生产零开销」这个说法的**可验证形式**：
    摘掉钩子后**真的没有监听器**（`event.contains` 为假），
    而不是「钩子还在、里面 return」。因此这里跑一条**真实查询**，
    而不是手动调 `add_statements()` —— 后者验证不了「没有监听器」这件事。
    """
    from sqlalchemy import text

    from app.db.session import SessionLocal

    async def one_query() -> int:
        counter = sql_counter.start_counting()
        try:
            async with SessionLocal() as session:
                await session.execute(text("SELECT 1"))
        finally:
            sql_counter.stop_counting()
        return counter.statements

    assert sql_counter.uninstall_sql_counter(engine) is True
    try:
        assert not sql_counter.is_installed(engine)
        assert await one_query() == 0, "钩子已摘掉，真实查询也不应被计入"
    finally:
        assert sql_counter.install_sql_counter(engine) is True
        assert sql_counter.is_installed(engine)

    assert await one_query() == 1, "钩子装回后应当重新计数"


# ---------------------------------------------------------------------------
# B1 · 热读端点的查询数上限断言
# ---------------------------------------------------------------------------
async def test_portal_list_first_page_statement_budget(client: httpx.AsyncClient) -> None:
    """门户列表首页：SQL 条数不超过上限（防 N+1 回归）。

    在「facets 未命中缓存」的条件下测量 —— 这是**最坏情况**，
    守卫应当盯最坏情况而不是最幸运的那个。
    """
    with _facets_uncached():
        await _warm_up(client, "/api/v1/tools?page_size=24")
        response = await client.get("/api/v1/tools?page_size=24")
    assert response.status_code == 200
    count = _sql_count(response)
    assert count <= PORTAL_LIST_FIRST_PAGE_MAX_STATEMENTS, (
        f"门户列表首页发了 {count} 条 SQL，超过上限 "
        f"{PORTAL_LIST_FIRST_PAGE_MAX_STATEMENTS} —— 很可能是 N+1 回归"
        "（随 items 数增长的查询）。实测构成见本文件模块 docstring。"
    )


async def test_portal_list_later_page_does_not_compute_facets(
    client: httpx.AsyncClient,
) -> None:
    """翻页不算 facets（既有的短时缓存 + page==1 条件不能被改回去）。"""
    with _facets_uncached():
        await _warm_up(client, "/api/v1/tools?page_size=24")
        first = await client.get("/api/v1/tools?page_size=24")
        later = await client.get("/api/v1/tools?page=2&page_size=24")
        first_count = _sql_count(first)
        later_count = _sql_count(later)

    assert later.status_code == 200
    assert later_count <= PORTAL_LIST_LATER_PAGE_MAX_STATEMENTS, (
        f"门户列表第 2 页发了 {later_count} 条 SQL，超过上限 "
        f"{PORTAL_LIST_LATER_PAGE_MAX_STATEMENTS} —— "
        "facets 聚合只在 page==1 时计算（docs/03 §3.3），这条可能被改回去了"
    )
    assert later_count < first_count, (
        "第 2 页的语句数应少于首页（首页多一次 facets 聚合），"
        f"实测首页 {first_count} / 第 2 页 {later_count}"
    )


async def test_tool_detail_statement_budget(client: httpx.AsyncClient) -> None:
    """工具详情：SQL 条数不超过上限。"""
    await _warm_up(client, "/api/v1/tools/public-approved")
    response = await client.get("/api/v1/tools/public-approved")
    assert response.status_code == 200
    count = _sql_count(response)
    assert count <= TOOL_DETAIL_MAX_STATEMENTS, (
        f"工具详情发了 {count} 条 SQL，超过上限 {TOOL_DETAIL_MAX_STATEMENTS} "
        "—— 很可能是 N+1 回归"
    )


async def test_statement_budget_does_not_grow_with_page_size(
    client: httpx.AsyncClient,
) -> None:
    """**这条才是真正的 N+1 探针**：条数不随 `page_size` 增长。

    上限断言回答不了「有没有 N+1」—— 它只回答「有没有超过某个数」。
    真正的 N+1 特征是这个：条目数翻倍，SQL 条数跟着涨。
    所以这里直接比较 `page_size=1` 与 `page_size=24` 的语句数，
    要求**完全相等**（列表用一条 SELECT 取回，条目再多也不多发语句）。
    """
    with _facets_uncached():
        await _warm_up(client, "/api/v1/tools?page_size=24")
        one = await client.get("/api/v1/tools?page_size=1")
        many = await client.get("/api/v1/tools?page_size=24")
    assert one.status_code == many.status_code == 200
    assert _sql_count(one) == _sql_count(many), (
        f"SQL 条数随 page_size 增长：page_size=1 → {_sql_count(one)} 条，"
        f"page_size=24 → {_sql_count(many)} 条。这是 N+1 的典型特征。"
    )


# ---------------------------------------------------------------------------
# B2 · 慢请求告警
# ---------------------------------------------------------------------------
async def test_slow_request_emits_warning_with_request_id(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """超过阈值时必须单独 warn，且**必须带 request_id**。"""
    monkeypatch.setattr(settings, "slow_request_ms", 1)  # 1 ms：任何请求都超
    with caplog.at_level(logging.WARNING, logger="app.request"):
        response = await client.get("/api/v1/tools?page_size=24")
    assert response.status_code == 200

    slow_records = [r for r in caplog.records if r.getMessage() == "慢请求"]
    assert slow_records, "超过阈值却没有 warn 日志"
    record = slow_records[0]
    assert getattr(record, "request_id", None), "慢请求日志缺少 request_id，无法对账"
    assert record.request_id == response.headers["X-Request-Id"], (
        "日志里的 request_id 与响应头不一致 —— 对不上账"
    )
    assert getattr(record, "duration_ms", None) is not None
    # SQL 条数一并给出：收到告警的人要能区分「做多了」与「做慢了」
    assert getattr(record, "sql_count", None) is not None
    assert getattr(record, "path", None) == "/api/v1/tools"


async def test_normal_request_is_not_warned(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """阈值很大时不得打 warn（否则告警会被噪声淹没，等于没有）。"""
    monkeypatch.setattr(settings, "slow_request_ms", 10_000_000)
    with caplog.at_level(logging.WARNING, logger="app.request"):
        response = await client.get("/healthz")
    assert response.status_code == 200
    assert not [r for r in caplog.records if r.getMessage() == "慢请求"]


async def test_slow_request_warning_can_be_disabled(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """阈值置 0 = 关闭该告警（给运维一个「别吵我」的开关）。"""
    monkeypatch.setattr(settings, "slow_request_ms", 0)
    with caplog.at_level(logging.WARNING, logger="app.request"):
        response = await client.get("/api/v1/tools?page_size=24")
    assert response.status_code == 200
    assert not [r for r in caplog.records if r.getMessage() == "慢请求"]


def test_slow_request_threshold_has_a_sane_default() -> None:
    """默认值必须有意义：太小会被噪声淹没，太大等于没有。"""
    from app.core.config import Settings

    default = Settings.model_fields["slow_request_ms"].default
    assert 50 <= default <= 10_000, f"慢请求阈值默认值 {default} ms 不合理"
    assert Settings.model_fields["metrics_enabled"].default is False, (
        "/metrics 必须默认关闭（任务书 B3 第 3 条）"
    )
    assert Settings.model_fields["metrics_token"].default == "", (
        "默认不得带 token —— 默认状态应当是「仅本机可读」而不是「已配置凭证」"
    )


# ---------------------------------------------------------------------------
# M1 · 指标聚合（渲染部分；HTTP 层在 tests/test_metrics.py）
# ---------------------------------------------------------------------------
def test_prometheus_output_parses_and_carries_required_metrics() -> None:
    """渲染出来的文本必须能被最小解析器读懂，且含任务书点名的那几项。"""
    metrics_module.reset()
    metrics_module.record_request(
        method="GET",
        route_template="/api/v1/tools",
        path="/api/v1/tools",
        status=200,
        duration_ms=5.0,
        sql_count=20,
        slow=False,
    )
    metrics_module.record_request(
        method="GET",
        route_template="/api/v1/tools",
        path="/api/v1/tools",
        status=200,
        duration_ms=15.0,
        sql_count=20,
        slow=True,
    )
    metrics_module.record_pool_wait(2.5)

    text = metrics_module.render_prometheus(
        engine, sql_enabled=True, slow_threshold_ms=1000
    )

    # 最小解析器：不接受任何解析不了的样本行
    parsed: dict[str, list[tuple[str, float]]] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})? +([0-9eE.+-]+)$", line)
        assert match, f"无法解析的指标行：{line!r}"
        parsed.setdefault(match.group(1), []).append((match.group(2) or "", float(match.group(3))))

    required = {
        # 任务书 B3 点名要暴露的五项
        "localcraft_requests_total",
        "localcraft_request_duration_ms_p50",
        "localcraft_request_duration_ms_p95",
        "localcraft_sql_statements_total",
        "localcraft_db_pool_wait_ms_p95",
        # 另外两项（B2 的阈值、M3 的 flush 失败）
        "localcraft_slow_request_threshold_ms",
        "localcraft_counter_flush_failures_total",
    }
    # WAL 大小是 **SQLite 专属**指标（`wal_size_bytes()` 在其他方言返回 None）。
    # M10 修：原先它被无条件放进 required，PG 上因此必然失败 —— 实现是对的，测试写错了。
    # 现在两个方向都断言，且都要求「一定存在 / 一定不存在」这种可证伪的形式：
    #   - SQLite：必须出现（否则 WAL 膨胀失去观测口）
    #   - PG：必须不出现（出现才是缺陷：PG 的 WAL 是集群级概念，
    #     任何由应用侧算出来的「WAL 字节数」都是假信号）
    if settings.is_sqlite:
        required.add("localcraft_sqlite_wal_bytes")
    missing = required - set(parsed)
    assert not missing, f"缺少指标：{sorted(missing)}"
    if not settings.is_sqlite:
        assert "localcraft_sqlite_wal_bytes" not in parsed, (
            "非 SQLite 方言（PostgreSQL）下不应输出 SQLite 专属的 WAL 指标"
        )

    assert parsed["localcraft_requests_total"][0][1] == 2
    assert 'route="/api/v1/tools"' in parsed["localcraft_requests_total"][0][0]
    assert parsed["localcraft_slow_requests_total"][0][1] == 1
    assert parsed["localcraft_sql_statements_total"][0][1] == 40
    assert parsed["localcraft_sql_statements_per_request_avg"][0][1] == 20
    metrics_module.reset()


def test_no_new_runtime_dependency_for_metrics() -> None:
    """任务书 B3 第 6 条：不得引入 Prometheus 客户端依赖。

    只查**依赖声明与实际 import**，不查字符串出现 —— 本模块的 docstring
    里就写着「不引入 prometheus_client」这句话，按字符串查会把文档判成违规。
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    declared = pyproject.split("dependencies = [", 1)[1].split("]", 1)[0]
    for banned in ("prometheus_client", "prometheus-client", "prometheus-client-python"):
        assert banned not in declared, f"pyproject.toml 的运行时依赖里出现了 {banned}"

    # 实际 import 语句（含 `from x import y`）。注释与 docstring 里的名字不算。
    import_re = re.compile(r"^\s*(?:from|import)\s+prometheus_client\b", re.MULTILINE)
    for path in (root / "app").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert not import_re.search(source), f"{path} 实际 import 了 prometheus_client"

    # 反向确认：文本格式是手写的，没有引用任何指标库
    metrics_source = (root / "app" / "core" / "metrics.py").read_text(encoding="utf-8")
    assert "# TYPE " in metrics_source and "# HELP " in metrics_source, (
        "指标输出应当是手写的 Prometheus 文本格式"
    )


async def test_route_template_prevents_series_explosion() -> None:
    """聚合键是路由模板，不是原始路径 —— 否则 1000 个工具就是 1000 条 series。"""
    from starlette.requests import Request

    from app.core.deps import route_template

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/tools/public-approved",
        "headers": [],
        "query_string": b"",
        "route": type("R", (), {"path": "/api/v1/tools/{slug}"})(),
    }
    assert route_template(Request(scope)) == "/api/v1/tools/{slug}"
    # 无匹配路由（404）落到显式兜底桶，而不是随便归到某个端点名下
    scope.pop("route")
    assert route_template(Request(scope)) == metrics_module.UNMATCHED_ROUTE
