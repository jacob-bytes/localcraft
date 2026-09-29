"""M14 · B1：在线工具探活（`contracts/CONTRACT.md` §29.2 / §29.3，冻结）。

背景：设置项、`/meta` 的 feature、以及三个数据库列**都在**，但没有任何实现
（预览库 26 行的 `webapp_health_status` 全是 NULL）—— §29.1 的第一例。

本文件覆盖三件事：

1. **探测规则**（§29.2 的表）：HEAD、405/501 回退 GET、5s 超时、不跟随重定向、
   2xx/3xx=ok、4xx/5xx=fail、连接失败/DNS=fail、超时=timeout。
   用一台**真实的本地 HTTP 服务器**（`http.server`，随机端口）产生真的 302 / 405 /
   404 / 慢响应 —— 不去 mock `urlopen`，因为「不跟随重定向」这件事恰恰要在
   真实协议交互上才可信。
2. **任务语义**：受 `webapp.health_check_enabled` 控制、只测
   `deleted_at IS NULL` + `tool_type='webapp'` + URL 非空、上限 200、
   `NULL` 与 `fail` 是两回事、`--dry-run` 不写库。
3. **暴露字段**（§29.3）：列表的派生 `webapp_unhealthy`、详情的两个原始字段。
"""

from __future__ import annotations

import http.server
import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.core.timeutil import utcnow
from app.db.session import SessionLocal
from app.models.enums import ToolType
from app.models.setting import SystemSetting
from app.models.tool import Tool
from app.services import webapp_health_service
from app.services.webapp_health_service import (
    STATUS_FAIL,
    STATUS_OK,
    STATUS_TIMEOUT,
    probe_health_url,
)


# ===========================================================================
# 一台真实的本地 HTTP 服务器（不 mock urllib）
# ===========================================================================
class _ProbeHandler(http.server.BaseHTTPRequestHandler):
    """记录每个被请求的路径，并按路径给出不同响应。

    `hits` 是「服务端真的收到了什么」这唯一可信的记录 ——
    「不跟随重定向」的断言就靠它：302 之后 `Location` 指向的目标
    **不能**出现在 `hits` 里。
    """

    protocol_version = "HTTP/1.1"

    def _record_and_respond(self) -> None:
        self.server.hits.append(self.path)  # type: ignore[attr-defined]
        path = self.path.split("?", 1)[0]
        if path == "/ok":
            self._respond(200)
        elif path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/redirect-target")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/redirect-target":
            self._respond(200)
        elif path == "/head-not-allowed":
            # HEAD 返回 405（§29.2 要回退 GET），GET 正常
            self._respond(200 if self.command == "GET" else 405)
        elif path == "/missing":
            self._respond(404)
        elif path == "/boom":
            self._respond(500)
        elif path == "/slow":
            time.sleep(1.0)
            self._respond(200)
        else:  # pragma: no cover - 用例里不会请求未知路径
            self._respond(404)

    def _respond(self, code: int) -> None:
        body = b"ok"
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    do_GET = _record_and_respond
    do_HEAD = _record_and_respond

    def log_message(self, *args: object) -> None:
        del args


class _ProbeServer:
    def __init__(self) -> None:
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ProbeHandler)
        self.httpd.hits = []  # type: ignore[attr-defined]
        self.port = int(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def hits(self) -> list[str]:
        return self.httpd.hits  # type: ignore[attr-defined]

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def probe_server() -> Iterator[_ProbeServer]:
    server = _ProbeServer()
    try:
        yield server
    finally:
        server.stop()


def _closed_port_url() -> str:
    """一个**确定连不上**的地址：本机上一个刚被释放的端口（连接被拒）。"""
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()
    return f"http://127.0.0.1:{port}/"


# ===========================================================================
# ① 探测规则（§29.2）
# ===========================================================================
def test_reachable_url_is_ok(probe_server: _ProbeServer) -> None:
    """真实可达 URL → ok（并且用的是 HEAD）。"""
    status, http_status = probe_health_url(probe_server.url("/ok"), timeout=5.0)
    assert (status, http_status) == (STATUS_OK, 200)
    assert probe_server.hits == ["/ok"]


def test_unreachable_url_is_fail() -> None:
    """不可达 URL（连接被拒）→ fail。"""
    status, http_status = probe_health_url(_closed_port_url(), timeout=2.0)
    assert status == STATUS_FAIL
    assert http_status is None


def test_dns_failure_is_fail() -> None:
    """DNS 解析失败 → fail（不是 timeout）。"""
    status, _ = probe_health_url("http://localcraft-m14-does-not-exist.invalid/", timeout=5.0)
    assert status == STATUS_FAIL


def test_invalid_scheme_is_fail() -> None:
    status, _ = probe_health_url("ftp://127.0.0.1/whatever", timeout=2.0)
    assert status == STATUS_FAIL


def test_redirect_is_ok_but_not_followed(probe_server: _ProbeServer) -> None:
    """★ §29.2 的核心：3xx 记为 **ok**，但**不跟随** `Location`。

    证据：服务端记录到的请求里只有 `/redirect`，**没有** `/redirect-target`
    —— 也就是 `Location` 指向的地址从未被访问（不做「以服务端身份访问任意主机」
    的跳板）。
    """
    status, http_status = probe_health_url(probe_server.url("/redirect"), timeout=5.0)
    assert status == STATUS_OK, "3xx 必须算可达"
    assert http_status == 302
    assert "/redirect" in probe_server.hits
    assert "/redirect-target" not in probe_server.hits, (
        "探测跟随了重定向 —— 这会把探活变成访问任意主机的跳板（§29.2 明确禁止）"
    )


def test_head_405_falls_back_to_get(probe_server: _ProbeServer) -> None:
    """HEAD 收到 405 → 回退 GET（§29.2）。"""
    status, http_status = probe_health_url(probe_server.url("/head-not-allowed"), timeout=5.0)
    assert (status, http_status) == (STATUS_OK, 200)
    assert probe_server.hits.count("/head-not-allowed") == 2, (
        "应当先 HEAD 再 GET（回退各一次），实际 "
        f"{probe_server.hits}"
    )


def test_4xx_is_fail(probe_server: _ProbeServer) -> None:
    status, http_status = probe_health_url(probe_server.url("/missing"), timeout=5.0)
    assert (status, http_status) == (STATUS_FAIL, 404)


def test_5xx_is_fail(probe_server: _ProbeServer) -> None:
    status, http_status = probe_health_url(probe_server.url("/boom"), timeout=5.0)
    assert (status, http_status) == (STATUS_FAIL, 500)


def test_timeout_is_timeout(probe_server: _ProbeServer) -> None:
    """超时 → `timeout`（与 `fail` 区分开）。"""
    started = time.monotonic()
    status, http_status = probe_health_url(probe_server.url("/slow"), timeout=0.3)
    elapsed = time.monotonic() - started
    assert status == STATUS_TIMEOUT
    assert http_status is None
    assert elapsed < 1.0, f"超时应当及时返回，实测 {elapsed:.2f}s"


def test_default_timeout_and_concurrency_are_contract_values() -> None:
    """§29.2 的 5 秒 / 并发 8 / 上限 200 是冻结值，不是随手写的。"""
    assert webapp_health_service.HTTP_TIMEOUT_SECONDS == 5.0
    assert webapp_health_service.CONCURRENCY == 8
    assert webapp_health_service.MAX_TOOLS_PER_RUN == 200
    assert {405, 501} == webapp_health_service.FALLBACK_TO_GET_ON


def test_probe_opener_has_no_redirect_and_no_proxy() -> None:
    """探测用的 opener：**不跟随重定向**，且**不接受任何代理处理器**。

    代理必须显式关掉：urllib 默认读 `http_proxy` / `https_proxy` 环境变量，
    那会让「被探测方是否可达」变成「环境里有没有代理」（本机实测：一个解析不了
    的域名在系统代理存在时返回 **502**，于是 `fail` 的成因其实是代理，不是 DNS；
    关掉代理后同一域名立刻以 DNS 失败收场）。

    结构性证据：`ProxyHandler({})` 在代理表为空时**不注册任何 `<type>_open`**，
    因此 opener 里根本不会出现代理处理器 —— 请求只会由 `HTTPHandler` 直连。
    """
    import urllib.request

    redirects = [
        h for h in webapp_health_service._OPENER.handlers
        if isinstance(h, urllib.request.HTTPRedirectHandler)
    ]
    assert redirects, "opener 里必须有不跟随重定向的处理器"
    assert all(
        isinstance(h, webapp_health_service._NoRedirectHandler) for h in redirects
    ), "默认的 HTTPRedirectHandler 必须被替换掉，否则它会跟随重定向"

    registered = list(webapp_health_service._OPENER.handlers)
    for chain in webapp_health_service._OPENER.handle_open.values():
        registered.extend(chain)
    assert not [h for h in registered if isinstance(h, urllib.request.ProxyHandler)], (
        "opener 里出现了代理处理器 —— 探测会受 http_proxy 环境变量影响"
    )


# ===========================================================================
# ② 任务语义（受开关控制 / 选谁 / 写什么 / dry-run）
# ===========================================================================
_UNSET: object = object()


class _HealthTool:
    """临时造一个 webapp 工具，退出时删掉（不污染种子数据）。"""

    def __init__(
        self,
        *,
        url: str | None,
        health_url: object = _UNSET,
        deleted: bool = False,
        tool_type: str = ToolType.WEBAPP.value,
        checked_at=None,
        status: str | None = None,
        slug_suffix: str = "",
    ) -> None:
        self.url = url
        #: `webapp_health_url`。缺省（未传）与 `url` 相同；显式传 `None` 表示
        #: 「有 webapp_url 但没有 health_url」—— 注意不能把 `webapp_url` 也留空：
        #: 表上有 M1 就有的 `ck_tools_webapp_url`（webapp 必须有 webapp_url）。
        self.health_url = url if health_url is _UNSET else health_url
        self.deleted = deleted
        self.tool_type = tool_type
        self.checked_at = checked_at
        self.status = status
        self.slug_suffix = slug_suffix
        self.tool_id: int = 0
        self.slug: str = ""

    async def __aenter__(self) -> _HealthTool:
        from sqlalchemy import select

        from app.models.taxonomy import Category
        from app.models.user import User

        async with SessionLocal() as session:
            owner = (await session.execute(select(User).where(User.username == "admin"))).scalar_one()
            category = (await session.execute(select(Category).limit(1))).scalar_one()
            stamp = utcnow().strftime("%H%M%S%f")
            self.slug = f"m14-health-{stamp}{self.slug_suffix}"
            tool = Tool(
                slug=self.slug,
                name=f"探活测试 {stamp}",
                summary="M14 探活用例临时工具",
                description_md="# 探活测试",
                tool_type=self.tool_type,
                visibility="public",
                status="approved",
                owner_id=owner.id,
                category_id=category.id,
                webapp_url=self.url,
                webapp_health_url=self.health_url,
                webapp_health_status=self.status,
                webapp_checked_at=self.checked_at,
                deleted_at=utcnow() if self.deleted else None,
                version_seq=1,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            session.add(tool)
            await session.commit()
            self.tool_id = tool.id
        return self

    async def __aexit__(self, *exc: object) -> None:
        async with SessionLocal() as session:
            tool = await session.get(Tool, self.tool_id)
            if tool is not None:
                await session.delete(tool)
                await session.commit()

    async def reload(self) -> Tool | None:
        async with SessionLocal() as session:
            return await session.get(Tool, self.tool_id)


class _HealthSetting:
    """打开/关闭 `webapp.health_check_enabled`，退出时还原。"""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.original: object = None

    async def __aenter__(self) -> _HealthSetting:
        async with SessionLocal() as session:
            row = await session.get(SystemSetting, webapp_health_service.ENABLED_SETTING_KEY)
            if row is None:  # pragma: no cover - 0002 就播种了它
                raise AssertionError("webapp.health_check_enabled 行不存在")
            self.original = row.value
            row.value = self.enabled
            await session.commit()
        return self

    async def __aexit__(self, *exc: object) -> None:
        async with SessionLocal() as session:
            row = await session.get(SystemSetting, webapp_health_service.ENABLED_SETTING_KEY)
            if row is not None:
                row.value = self.original
                await session.commit()


async def test_task_skips_when_setting_is_false() -> None:
    """★ 开关为 false → 直接跳过（**终于让这个开关有意义**，§29.2）。"""
    async with _HealthTool(url="http://127.0.0.1:9/never") as tool, _HealthSetting(False):
        async with SessionLocal() as session:
            result = await webapp_health_service.run(session)
        assert result.skipped is True
        assert result.checked == 0
        reloaded = await tool.reload()
        assert reloaded is not None
        assert reloaded.webapp_health_status is None, "跳过时不得写状态"
        assert reloaded.webapp_checked_at is None


async def test_task_probes_and_writes_ok(probe_server: _ProbeServer) -> None:
    """开关为 true → 真的探测并写回 `ok` + `webapp_checked_at`。"""
    async with _HealthTool(url=probe_server.url("/ok")) as tool, _HealthSetting(True):
        async with SessionLocal() as session:
            result = await webapp_health_service.run(session)
        assert result.skipped is False
        assert result.checked == 1
        assert (result.ok, result.fail, result.timeout) == (1, 0, 0)

        reloaded = await tool.reload()
        assert reloaded is not None
        assert reloaded.webapp_health_status == STATUS_OK
        assert reloaded.webapp_checked_at is not None


async def test_task_writes_fail_for_unreachable(probe_server: _ProbeServer) -> None:
    """4xx/连接失败 → `fail`（与「从未检测」的 NULL 是两回事）。"""
    async with _HealthTool(url=_closed_port_url()) as tool, _HealthSetting(True):
        async with SessionLocal() as session:
            result = await webapp_health_service.run(session)
        assert (result.checked, result.ok, result.fail, result.timeout) == (1, 0, 1, 0)
        reloaded = await tool.reload()
        assert reloaded is not None
        assert reloaded.webapp_health_status == STATUS_FAIL
        assert reloaded.webapp_checked_at is not None


async def test_dry_run_probes_but_does_not_write(probe_server: _ProbeServer) -> None:
    """`--dry-run` 不得写库（沿用既有语义）。"""
    async with _HealthTool(url=probe_server.url("/ok")) as tool, _HealthSetting(True):
        async with SessionLocal() as session:
            result = await webapp_health_service.run(session, dry_run=True)
        assert result.checked == 1, "dry-run 仍应真的探测（运维要能预览结果）"
        reloaded = await tool.reload()
        assert reloaded is not None
        assert reloaded.webapp_health_status is None, "dry-run 不得写库"
        assert reloaded.webapp_checked_at is None


async def test_candidates_filter_deleted_non_webapp_and_empty_url(
    probe_server: _ProbeServer,
) -> None:
    """只测「未删除 + webapp + health_url 非空」（§29.2）。"""
    good_url = probe_server.url("/ok")
    async with (
        _HealthTool(url=good_url, slug_suffix="-hit") as hit,
        _HealthTool(url=good_url, deleted=True, slug_suffix="-deleted") as deleted,
        _HealthTool(url=good_url, tool_type=ToolType.FILE.value, slug_suffix="-file") as file_tool,
        _HealthTool(
            url=good_url, health_url=None, slug_suffix="-nourl"
        ) as no_url,
        _HealthSetting(True),
    ):
        async with SessionLocal() as session:
            candidates = await webapp_health_service.list_candidates(session, limit=500)
        ids = {tool_id for tool_id, _ in candidates}
        assert hit.tool_id in ids
        assert deleted.tool_id not in ids, "已软删除的工具不该被探测"
        assert file_tool.tool_id not in ids, "非 webapp 不该被探测"
        assert no_url.tool_id not in ids, "health_url 为空的工具不该被探测"


async def test_run_limit_is_honoured_and_reports_truncation() -> None:
    """每次运行上限（§29.2）会被遵守，并如实报告「被截断」。"""
    async with (
        _HealthTool(url="http://127.0.0.1:9/x", slug_suffix="-a") as first,
        _HealthTool(url="http://127.0.0.1:9/y", slug_suffix="-b"),
        _HealthSetting(True),
    ):
        async with SessionLocal() as session:
            candidates = await webapp_health_service.list_candidates(session, limit=500)
            assert any(tool_id == first.tool_id for tool_id, _ in candidates)
            assert len(candidates) >= 2, "本用例需要至少两个候选工具"
            # 直接调 run(limit=1) → 只会测 1 个，且 truncated=True
            result = await webapp_health_service.run(
                session, limit=1, concurrency=1, timeout=0.5, dry_run=True
            )
        assert result.checked == 1, "limit=1 时只该测 1 个"
        assert result.truncated is True, "候选多于上限时必须报告截断"


async def test_nulls_are_ordered_first_so_nothing_starves(probe_server: _ProbeServer) -> None:
    """从未测过的排在最前（限 200 时不让后面的工具永远轮不到）。"""
    async with (
        _HealthTool(
            url=probe_server.url("/ok"),
            checked_at=utcnow(),
            status=STATUS_OK,
            slug_suffix="-checked",
        ) as checked,
        _HealthSetting(True),
    ):
        async with SessionLocal() as session:
            candidates = await webapp_health_service.list_candidates(session, limit=500)
        ids = [tool_id for tool_id, _ in candidates]
        assert checked.tool_id in ids
        # 从未检测的工具（种子里可能有，也可能没有）必须排在已检测的之前
        null_positions = [
            index
            for index, (tool_id, _) in enumerate(candidates)
            if tool_id != checked.tool_id
        ]
        if null_positions:  # pragma: no cover - 依赖是否恰好有别的候选
            assert null_positions[0] < ids.index(checked.tool_id)


async def test_run_all_includes_webapp_health_fields(seeded, tmp_path: Path) -> None:
    """`run_all` 必须带上探活任务（§29.2：接进 run_all）。"""
    from app.services import maintenance_service

    files_root = tmp_path / "files"
    files_root.mkdir()
    async with SessionLocal() as session:
        result = await maintenance_service.run_all(
            session,
            files_root=files_root,
            download_log_retention_days=180,
            recycle_bin_retention_days=30,
            version_history_limit=10,
            dry_run=True,
        )
    data = result.as_dict()
    for key in (
        "webapp_checked",
        "webapp_ok",
        "webapp_fail",
        "webapp_timeout",
        "webapp_skipped",
        "webapp_truncated",
    ):
        assert key in data, f"run_all 的汇总缺少 {key}"
    # 种子库里 webapp.health_check_enabled 默认 false → 如实报告 skipped
    assert result.webapp_skipped is True


# ===========================================================================
# ③ 暴露字段（§29.3）
# ===========================================================================
class _SeededToolMutation:
    """临时改种子里某个工具的探活字段，退出时还原。"""

    def __init__(self, slug: str) -> None:
        self.slug = slug
        self.original: dict[str, object] = {}

    async def __aenter__(self) -> _SeededToolMutation:
        from sqlalchemy import select

        async with SessionLocal() as session:
            tool = (await session.execute(select(Tool).where(Tool.slug == self.slug))).scalar_one()
            self.original = {
                "tool_type": tool.tool_type,
                "webapp_url": tool.webapp_url,
                "webapp_health_status": tool.webapp_health_status,
                "webapp_checked_at": tool.webapp_checked_at,
                "webapp_health_url": tool.webapp_health_url,
            }
        return self

    async def set_health(self, status: str | None, *, checked: bool = False) -> None:
        from sqlalchemy import select

        async with SessionLocal() as session:
            tool = (await session.execute(select(Tool).where(Tool.slug == self.slug))).scalar_one()
            tool.tool_type = ToolType.WEBAPP.value
            tool.webapp_url = "http://m14-health.example.com"
            tool.webapp_health_url = "http://m14-health.example.com"
            tool.webapp_health_status = status
            tool.webapp_checked_at = utcnow() if checked else None
            await session.commit()

    async def __aexit__(self, *exc: object) -> None:
        from sqlalchemy import select

        async with SessionLocal() as session:
            tool = (await session.execute(select(Tool).where(Tool.slug == self.slug))).scalar_one()
            for key, value in self.original.items():
                setattr(tool, key, value)
            await session.commit()


@pytest.mark.parametrize(
    ("status", "expected_unhealthy"),
    [("fail", True), ("timeout", True), ("ok", False), (None, False)],
)
async def test_list_item_webapp_unhealthy_is_derived(
    client, seeded, status: str | None, expected_unhealthy: bool
) -> None:
    """§29.3：列表只给派生布尔；`fail`/`timeout` 为真，`ok` 与 **NULL 都不算**。"""
    async with _SeededToolMutation("public-approved") as mutation:
        await mutation.set_health(status, checked=status is not None)
        response = await client.get("/api/v1/tools?page_size=48")
        assert response.status_code == 200, response.text
        item = next(
            (i for i in response.json()["items"] if i["slug"] == "public-approved"), None
        )
        assert item is not None, "种子里应有 public-approved"
        assert item["webapp_unhealthy"] is expected_unhealthy, (
            f"health_status={status!r} 时 webapp_unhealthy 应为 {expected_unhealthy}"
        )


async def test_list_item_does_not_expose_raw_health_fields(client, seeded) -> None:
    """§29.3：列表**只**给派生布尔，不给两个原始字段（保持列表精简）。"""
    async with _SeededToolMutation("public-approved") as mutation:
        await mutation.set_health("fail", checked=True)
        response = await client.get("/api/v1/tools?page_size=48")
        item = next(
            i for i in response.json()["items"] if i["slug"] == "public-approved"
        )
        assert "webapp_unhealthy" in item
        assert "webapp_health_status" not in item
        assert "webapp_checked_at" not in item


async def test_detail_exposes_raw_health_fields(client, seeded) -> None:
    """§29.3：详情给原始值，`NULL`（未检测）与 `'fail'` 可区分。"""
    async with _SeededToolMutation("public-approved") as mutation:
        await mutation.set_health("fail", checked=True)
        response = await client.get("/api/v1/tools/public-approved")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["webapp_health_status"] == "fail"
        assert body["webapp_checked_at"] is not None

        await mutation.set_health(None, checked=False)
        response = await client.get("/api/v1/tools/public-approved")
        body = response.json()
        assert body["webapp_health_status"] is None, "从未检测必须是 null，不是 'fail'"
        assert body["webapp_checked_at"] is None


async def test_never_checked_webapp_is_not_flagged_on_a_fresh_portal(
    client, seeded
) -> None:
    """★ 刚部署时的行为：所有 webapp 都是 NULL → 一个「不健康」标记都不该出现。

    否则刚打开探活开关的门户会满屏告警，而那是「还没测」不是「坏了」。
    """
    async with _SeededToolMutation("public-approved") as mutation:
        await mutation.set_health(None, checked=False)
        response = await client.get("/api/v1/tools?page_size=48")
        assert all(item["webapp_unhealthy"] is False for item in response.json()["items"])


# ===========================================================================
# ④ CLI 接线：`maintenance --task webapp-health`
# ===========================================================================
def test_cli_task_webapp_health_is_wired(tmp_path: Path) -> None:
    """§29.2：任务必须接进 `app.cli maintenance --task`（用子进程跑真实命令）。

    **不新增 systemd 单元**：它复用既有 `localcraft-maintenance.timer`
    （`run_all` 已包含本任务，见 `test_run_all_includes_webapp_health_fields`）。

    这里用**独立临时库**跑真实 CLI（与 `tests/test_cli.py` 同口径）：
    默认库上 `webapp.health_check_enabled=false` → 输出必须如实报告
    `webapp_skipped=true`，而不是「什么都没做却报成功」。
    """
    from tests.test_cli import _cli_env, _run

    db_path = tmp_path / "cli-webapp-health.db"
    env = _cli_env(db_path, tmp_path / "data")

    upgraded = _run(["alembic", "upgrade", "head"], env)
    assert upgraded.returncode == 0, upgraded.stderr

    result = _run(["app.cli", "maintenance", "--task", "webapp-health", "--json"], env)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["errors"] == []
    assert payload["result"]["webapp_skipped"] is True, (
        "默认 webapp.health_check_enabled=false → 必须如实报告「跳过」"
    )
    assert payload["result"]["webapp_checked"] == 0
    assert payload["result"]["webapp_truncated"] is False


def test_cli_unknown_task_message_lists_webapp_health(tmp_path: Path) -> None:
    """未知任务的提示里要包含新任务名（否则运维不知道该写什么）。"""
    from tests.test_cli import _cli_env, _run

    db_path = tmp_path / "cli-unknown-task.db"
    env = _cli_env(db_path, tmp_path / "data")
    assert _run(["alembic", "upgrade", "head"], env).returncode == 0

    result = _run(["app.cli", "maintenance", "--task", "nope"], env)
    assert result.returncode != 0
    assert "webapp-health" in (result.stdout + result.stderr)
