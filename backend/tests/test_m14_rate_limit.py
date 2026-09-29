"""M14 · B2：应用层限流（`contracts/CONTRACT.md` §29.4，冻结）。

背景：错误码 `RATE_LIMITED` 早已映射到 429，但**全仓没有一处抛出它** ——
「看起来有、实际没有」的半成品（§29.1）。本文件是它接通之后的证据。

## ★ 为什么大部分用例直接测限流器，而不是用 TestClient 刷请求

§29.4 明写：**loopback 被豁免**（`127.0.0.1` / `::1` / `localhost` / `testclient`），
所以测试客户端**打不到限流**。用 TestClient 发几百次请求去撞阈值既慢又不可靠
（还依赖 testclient 的 host 恰好不是名单里的值）。

因此本文件分两层：

1. **限流器本身**（`SlidingWindowLimiter`，用非 loopback 的伪 IP + 可注入时钟）：
   阈值、超限、窗口滑动恢复、`Retry-After` 计算、按 IP/作用域隔离、
   开关关闭、loopback 永远放行。这是**主要覆盖**。
2. **接线**（少量 HTTP 用例）：把 `get_client_info` 换成一个公网伪 IP，
   验证「全局 `/api/v1` 配额 / 登录配额 / 上传配额」三档真的接在路由上、
   429 的响应体与 `Retry-After` 头真的出得来。每个用例只发 2~3 个请求。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import select

from app.core.deps import ClientInfo
from app.core.errors import RateLimitedError
from app.core.rate_limit import (
    SCOPE_API,
    SCOPE_LOGIN,
    SCOPE_UPLOAD,
    SlidingWindowLimiter,
    enforce,
    get_config,
    is_loopback_source,
    limiter,
    reset_config_cache,
)
from app.db.session import SessionLocal
from app.models.setting import SystemSetting
from tests.conftest import auth, login

#: 非 loopback 的伪 IP（RFC 5737 的文档用网段，永远不会是真实来源）
FAKE_IP = "203.0.113.7"
FAKE_IP_2 = "203.0.113.8"


@pytest.fixture(autouse=True)
def _clean_rate_limit_state() -> Iterator[None]:
    """每个用例前后都清空限流桶与配置缓存 —— 它们是进程级单例。"""
    limiter.reset()
    reset_config_cache()
    yield
    limiter.reset()
    reset_config_cache()


def _fake_client_info(ip: str):
    """构造一个「把任何请求都当成来自 `ip`」的 `get_client_info` 替身。

    ★ 这里替换的是 `app.core.rate_limit` **模块命名空间里**的那个名字 ——
    依赖函数调用它时走的就是这个全局名。同时证明「实现确实复用了
    `get_client_info()`」：换掉它，限流看到的来源就变了。
    """

    def _fake(request) -> ClientInfo:
        del request
        return ClientInfo(ip=ip, user_agent="m14-test")

    return _fake


class _SettingsOverride:
    """临时改设置行的值，退出时还原（并让限流配置缓存失效）。"""

    def __init__(self, values: dict[str, object]) -> None:
        self._values = values
        self._originals: dict[str, object] = {}

    async def __aenter__(self) -> None:
        async with SessionLocal() as session:
            for key, value in self._values.items():
                row = await session.get(SystemSetting, key)
                if row is None:  # pragma: no cover - 迁移 0008 之后不该缺行
                    raise AssertionError(f"设置行不存在：{key}（迁移 0008 没跑？）")
                self._originals[key] = row.value
                row.value = value
            await session.commit()
        reset_config_cache()

    async def __aexit__(self, *exc: object) -> None:
        async with SessionLocal() as session:
            for key, value in self._originals.items():
                row = await session.get(SystemSetting, key)
                if row is not None:
                    row.value = value
            await session.commit()
        reset_config_cache()


# ===========================================================================
# ① 限流器本身（§29.4 的主要覆盖面）
# ===========================================================================
def test_limiter_allows_up_to_threshold_then_rejects() -> None:
    """窗口内放行到阈值，第 limit+1 个被拒绝。"""
    engine = SlidingWindowLimiter()
    for index in range(3):
        decision = engine.check(SCOPE_API, FAKE_IP, limit=3, now=1000.0 + index * 0.1)
        assert decision.allowed is True, f"第 {index + 1} 个请求应当放行"
        assert decision.limit == 3
        assert decision.remaining == 2 - index

    rejected = engine.check(SCOPE_API, FAKE_IP, limit=3, now=1000.4)
    assert rejected.allowed is False, "第 4 个请求必须被拒绝"
    assert rejected.remaining == 0
    assert rejected.retry_after >= 1, "被拒时必须给出 Retry-After（秒）"


def test_limiter_window_slides_and_recovers() -> None:
    """窗口滑过后恢复放行（精确滑动窗口，不是「永久拉黑」）。"""
    engine = SlidingWindowLimiter(window_seconds=60.0)
    for index in range(2):
        assert engine.check("api", FAKE_IP, limit=2, now=1000.0 + index).allowed

    assert not engine.check("api", FAKE_IP, limit=2, now=1002.0).allowed

    # 第一个请求（t=1000）离开窗口后，又能放行一个
    assert engine.check("api", FAKE_IP, limit=2, now=1060.5).allowed
    # 此刻窗口里是 [1001, 1060.5]，仍然满，第 3 个还是被拒
    assert not engine.check("api", FAKE_IP, limit=2, now=1060.6).allowed
    # 窗口整体滑过（1001 也出去了）
    assert engine.check("api", FAKE_IP, limit=2, now=1061.1).allowed


def test_retry_after_is_seconds_until_oldest_leaves_window() -> None:
    """`Retry-After` = 「窗口内最早那次请求 + 一个完整窗口」距现在的秒数（向上取整）。"""
    engine = SlidingWindowLimiter(window_seconds=60.0)
    engine.check("login", FAKE_IP, limit=1, now=500.0)
    decision = engine.check("login", FAKE_IP, limit=1, now=500.0)
    assert decision.allowed is False
    assert decision.retry_after == 60, "刚用完配额时应是整整一个窗口"

    decision = engine.check("login", FAKE_IP, limit=1, now=530.0)
    assert decision.retry_after == 30, "过了 30 秒应只剩 30 秒"

    decision = engine.check("login", FAKE_IP, limit=1, now=559.2)
    assert decision.retry_after == 1, "不足 1 秒也要向上取整成 1（不能是 0）"


def test_rejected_requests_do_not_extend_the_window() -> None:
    """被拒的请求**不**写入窗口 —— 否则持续重试的客户端永远出不来。"""
    engine = SlidingWindowLimiter(window_seconds=60.0)
    engine.check("api", FAKE_IP, limit=1, now=100.0)
    for moment in (100.1, 100.2, 100.3, 100.4):
        decision = engine.check("api", FAKE_IP, limit=1, now=moment)
        assert decision.allowed is False
        # 若被拒请求也进窗口，这个值会一路涨到 60 以上
        assert decision.retry_after == 60

    assert engine.check("api", FAKE_IP, limit=1, now=160.1).allowed


def test_limiter_is_isolated_per_ip_and_per_scope() -> None:
    """键是 `(scope, client_ip)`：不同 IP、不同作用域互不影响。"""
    engine = SlidingWindowLimiter()
    assert engine.check(SCOPE_API, FAKE_IP, limit=1, now=10.0).allowed
    assert not engine.check(SCOPE_API, FAKE_IP, limit=1, now=10.1).allowed

    # 另一个 IP 不受影响
    assert engine.check(SCOPE_API, FAKE_IP_2, limit=1, now=10.2).allowed
    # 同一个 IP 的另一个作用域也不受影响
    assert engine.check(SCOPE_LOGIN, FAKE_IP, limit=1, now=10.3).allowed
    assert engine.check(SCOPE_UPLOAD, FAKE_IP, limit=1, now=10.4).allowed


def test_limit_below_one_means_unlimited() -> None:
    """`limit < 1` 视为不限流（fail-open）—— 配置成 0 不该把平台锁死。"""
    engine = SlidingWindowLimiter()
    for moment in range(50):
        decision = engine.check("api", FAKE_IP, limit=0, now=float(moment))
        assert decision.allowed is True


def test_bucket_count_is_bounded() -> None:
    """伪造成千上万个源 IP 也不会把内存吃光（LRU 淘汰）。"""
    engine = SlidingWindowLimiter(max_buckets=5)
    for index in range(50):
        engine.check("api", f"198.51.100.{index}", limit=1, now=float(index))
    assert engine.bucket_count() == 5


# ===========================================================================
# ② loopback 豁免（§29.4 的 ★ 条目）
# ===========================================================================
@pytest.mark.parametrize("ip", ["127.0.0.1", "::1", "localhost", "testclient"])
def test_loopback_sources_are_recognised(ip: str) -> None:
    assert is_loopback_source(ip) is True


@pytest.mark.parametrize("ip", ["203.0.113.7", "10.0.0.5", "::2", "127.0.0.2", "", None])
def test_non_loopback_sources_are_not_exempt(ip: str | None) -> None:
    """`127.0.0.2` 也**不**豁免 —— 豁免名单是逐字冻结的四个值，不是「127/8 网段」。

    （`127.0.0.2` 在实践中仍然是本机回环，但 §29.4 把名单写死成四个值；
    按名单实现才不会因为「顺手放宽网段」而把某个反代来源一起放过。）
    """
    assert is_loopback_source(ip) is False


async def test_loopback_is_exempt_without_touching_the_database() -> None:
    """loopback 请求**连配置都不读** —— 传 `session=None` 也不会炸。

    这既是「豁免生效」的证据，也是「测试套件不会被限流拖慢/加查询」的机制说明
    （测试客户端的 host 是 `127.0.0.1` 或 `testclient`）。
    """
    decision = await enforce(
        None,  # type: ignore[arg-type] - 豁免路径不该碰 session
        scope=SCOPE_API,
        client=ClientInfo(ip="127.0.0.1", user_agent="pytest"),
    )
    assert decision is None
    assert limiter.bucket_count() == 0, "被豁免的请求不该产生任何桶"


async def test_real_ip_is_limited_and_raises_429_domain_error() -> None:
    """非 loopback 来源：超阈值时抛 `RateLimitedError`（含 `Retry-After` 头）。"""
    async with SessionLocal() as session, _SettingsOverride(
        {"security.rate_limit_per_minute": 1}
    ):
        first = await enforce(
            session, scope=SCOPE_API, client=ClientInfo(ip=FAKE_IP, user_agent="pytest")
        )
        assert first is not None and first.allowed is True

        with pytest.raises(RateLimitedError) as excinfo:
            await enforce(
                session, scope=SCOPE_API, client=ClientInfo(ip=FAKE_IP, user_agent="pytest")
            )
        error = excinfo.value
        assert error.code == "RATE_LIMITED"
        assert error.http_status == 429
        assert error.headers is not None
        assert int(error.headers["Retry-After"]) >= 1
        assert error.details is not None
        assert error.details["scope"] == SCOPE_API


async def test_master_switch_off_allows_everything() -> None:
    """总开关关闭时永远放行（即使配额是 1、已经用满）。"""
    async with SessionLocal() as session, _SettingsOverride(
        {"security.rate_limit_enabled": False, "security.rate_limit_per_minute": 1}
    ):
        for _ in range(5):
            decision = await enforce(
                session, scope=SCOPE_API, client=ClientInfo(ip=FAKE_IP, user_agent="pytest")
            )
            assert decision is None, "开关关闭时应直接放行（返回 None = 未参与限流）"
        assert limiter.bucket_count() == 0


# ===========================================================================
# ③ 配置解析（来自设置表 + 代码兜底）
# ===========================================================================
async def test_config_comes_from_seeded_settings_rows() -> None:
    """迁移 0008 播种的 4 行 → 配置读到的是 §29.4 表格里的默认值。"""
    async with SessionLocal() as session:
        config = await get_config(session)
    assert config.enabled is True
    assert config.limits == {SCOPE_API: 1200, SCOPE_LOGIN: 10, SCOPE_UPLOAD: 30}


async def test_config_change_takes_effect_immediately_when_cache_reset() -> None:
    """改设置行 + `reset_config_cache()` → 下一次读到的就是新值。

    （HTTP 路径上由 `settings_service.update_settings` 负责失效缓存，
    见 `tests/test_m14_rate_limit.py` 的接线用例与 `settings_service` 的注释。）
    """
    async with SessionLocal() as session, _SettingsOverride(
        {"security.rate_limit_login_per_minute": 2}
    ):
        config = await get_config(session)
        assert config.limits[SCOPE_LOGIN] == 2

        row_reset = await session.get(SystemSetting, "security.rate_limit_login_per_minute")
        assert row_reset is not None and row_reset.value == 2


# ===========================================================================
# ④ 接线：三档配额真的挂在路由上（少量 HTTP 用例）
# ===========================================================================
async def test_login_scope_returns_429_with_retry_after_header(
    client, seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    """登录档：第 2 次登录就被 429，响应体是契约 §4.3 的形状，且带 `Retry-After`。"""
    monkeypatch.setattr("app.core.rate_limit.get_client_info", _fake_client_info(FAKE_IP))
    async with _SettingsOverride({"security.rate_limit_login_per_minute": 1}):
        body = {"username": "admin", "password": "Admin@12345"}
        first = await client.post("/api/v1/auth/login", json=body)
        assert first.status_code == 200, first.text

        second = await client.post("/api/v1/auth/login", json=body)
        assert second.status_code == 429, second.text
        payload = second.json()
        assert payload["code"] == "RATE_LIMITED"
        assert payload["details"]["scope"] == "login"
        assert payload["request_id"]

        retry_after = second.headers.get("Retry-After")
        assert retry_after is not None, "429 必须带 Retry-After 响应头（§29.4）"
        assert int(retry_after) >= 1
        # 同一响应上 X-Request-Id 不能因为多了 Retry-After 而消失
        assert second.headers.get("X-Request-Id")


async def test_api_scope_is_wired_globally(
    client, seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    """全局 `/api/v1` 总配额：挂在 `include_router` 上，普通读接口也受管。"""
    monkeypatch.setattr("app.core.rate_limit.get_client_info", _fake_client_info(FAKE_IP))
    async with _SettingsOverride(
        {"security.rate_limit_per_minute": 1, "security.rate_limit_login_per_minute": 99}
    ):
        first = await client.get("/api/v1/tools")
        assert first.status_code == 200, first.text
        second = await client.get("/api/v1/tools")
        assert second.status_code == 429, second.text
        assert second.json()["code"] == "RATE_LIMITED"
        assert second.json()["details"]["scope"] == "api"


async def test_upload_scope_is_wired_on_upload_endpoints(
    client, seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    """上传档：multipart 上传接口受更严的配额管辖。"""
    monkeypatch.setattr("app.core.rate_limit.get_client_info", _fake_client_info(FAKE_IP))
    admin_token = await login(client, "admin")
    csv_content = (
        "username,display_name,email,roles,password\nm14upload,上传档,m14@example.com,user,\n"
    ).encode()
    async with _SettingsOverride(
        {
            "security.rate_limit_per_minute": 99,
            "security.rate_limit_upload_per_minute": 1,
        }
    ):
        first = await client.post(
            "/api/v1/admin/import/users",
            files={"file": ("u.csv", csv_content, "text/csv")},
            data={"dry_run": "true", "on_conflict": "skip"},
            headers=auth(admin_token),
        )
        assert first.status_code == 200, first.text

        second = await client.post(
            "/api/v1/admin/import/users",
            files={"file": ("u.csv", csv_content, "text/csv")},
            data={"dry_run": "true", "on_conflict": "skip"},
            headers=auth(admin_token),
        )
        assert second.status_code == 429, second.text
        assert second.json()["details"]["scope"] == "upload"


async def test_loopback_requests_are_never_rate_limited(
    client, seeded
) -> None:
    """★ 反面证据：**不**替换 `get_client_info` 时（测试客户端 = loopback），
    即使把配额调到 1，连续请求也全部正常 —— 这正是「TestClient 打不到限流」
    的原因，也是本文件主要覆盖放在限流器本身的原因。"""
    async with _SettingsOverride(
        {
            "security.rate_limit_per_minute": 1,
            "security.rate_limit_login_per_minute": 1,
        }
    ):
        for _ in range(4):
            response = await client.get("/api/v1/tools")
            assert response.status_code == 200, response.text
        for _ in range(3):
            response = await client.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": "Admin@12345"},
            )
            assert response.status_code == 200, response.text


async def test_login_success_returns_a_real_response_through_the_limiter(
    client, seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    """反面证据的二：伪 IP 下**只要没超配额**，登录本身仍然正常工作。"""
    monkeypatch.setattr("app.core.rate_limit.get_client_info", _fake_client_info(FAKE_IP))
    async with _SettingsOverride({"security.rate_limit_login_per_minute": 5}):
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "Admin@12345"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["access_token"]


# ===========================================================================
# ⑤ 与「生产默认值」有关的硬约束
# ===========================================================================
async def test_production_default_is_enabled_with_contract_values() -> None:
    """生产默认**必须是开着的**、且配额是 §29.4 的值（不许把默认改成 false）。"""
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(SystemSetting).where(
                    SystemSetting.key.in_(
                        [
                            "security.rate_limit_enabled",
                            "security.rate_limit_per_minute",
                            "security.rate_limit_login_per_minute",
                            "security.rate_limit_upload_per_minute",
                        ]
                    )
                )
            )
        ).scalars().all()
    values = {row.key: row.value for row in rows}
    assert values["security.rate_limit_enabled"] is True
    assert values["security.rate_limit_per_minute"] == 1200
    assert values["security.rate_limit_login_per_minute"] == 10
    assert values["security.rate_limit_upload_per_minute"] == 30
