"""应用层限流（`contracts/CONTRACT.md` §29.4，冻结）。

背景：错误码 `RATE_LIMITED` 早已映射到 429，但**全仓没有一处抛出它** ——
「看起来有、实际没有」的半成品之一（§29.1）。本模块把它接通。

## 设计与口径（逐条对应 §29.4）

| 项 | 实现 |
| --- | --- |
| 算法 | **进程内**滑动窗口计数（`deque` 存窗口内的时间戳） |
| 键 | `(scope, client_ip)` |
| 取 IP | 复用 `app.core.deps.get_client_info()`，**不新写取 IP 的逻辑** |
| loopback 豁免 | `127.0.0.1` / `::1` / `localhost` / `testclient` 的来源**不计入限流** |
| 拒绝响应 | `429` + `code = RATE_LIMITED` + **`Retry-After`** 响应头（秒） |
| 开关与配额 | 4 个设置项（`security.rate_limit_*`，迁移 0008 播种） |

**为什么 loopback 豁免**（与 `/metrics` 同源，§24.1）：uvicorn 以
`--proxy-headers --forwarded-allow-ips=127.0.0.1` 启动时，反代来源会被还原成
**真实客户端 IP**，所以被豁免的只有「真正从本机发起」的请求（健康检查、
运维脚本、本机 ops）。直连 `http://ip:port` 部署（D39）下所有真实客户端都带
自己的 IP，限流照常生效。

## 为什么是「进程内」

多 worker 部署下每个 worker 各有一份计数，实际配额是 `limit × worker 数`。
内网百人规模、单 worker 部署（`deploy/localcraft.service`）下这是准确的；
换成多 worker 或多实例需要共享存储（Redis 等），**那会引入新依赖**，
而本轮硬约束是「不新增依赖」。此取舍在模块内显式记录，不藏着。

## 配置读取为什么带 5 秒缓存

限流配置来自系统设置表（管理端可改，FR-CFG-01 要求运行时生效）。
每请求查表会给**每个 API 请求**加一次 SQL，而本项目的 SQL 条数守卫
（O12）盯的正是这个。因此：

  - 配置读一次缓存 5 秒（`CONFIG_CACHE_SECONDS`），稳态下**零额外查询**；
  - 管理端 `PUT /admin/settings` 提交后**显式失效缓存**
    （`settings_service.update_settings`），所以改完立刻生效，不靠等 5 秒；
  - **loopback 豁免的判断在读取配置之前**：本机请求（含全部测试客户端）
    连配置都不读，因此限流不会给测试套件增加任何 SQL 条数。

## 测试怎么覆盖（§29.4 的强制要求）

因为 loopback 被豁免，**测试客户端打不到限流**。所以覆盖方式是
**直接测限流器本身**（`SlidingWindowLimiter`，用非 loopback 的伪 IP 与
可注入的 `now`），见 `tests/test_m14_rate_limit.py`；HTTP 面只做少量
「接线是否接上」的用例（把 `get_client_info` 换成伪 IP），
**不靠 TestClient 刷几百次请求**去撞阈值。
"""

from __future__ import annotations

import math
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import ClientInfo, get_client_info
from app.core.errors import RateLimitedError
from app.db.session import get_db
from app.repositories import system_settings as settings_repo

# ---------------------------------------------------------------------------
# 作用域与设置键
# ---------------------------------------------------------------------------
#: `/api/v1` 全局总配额（挂在 `app.include_router(..., dependencies=...)` 上）
SCOPE_API = "api"
#: 登录（`POST /api/v1/auth/login`）
SCOPE_LOGIN = "login"
#: 上传（版本包 / 封面截图 / 管理侧代上传 / CSV 导入）
SCOPE_UPLOAD = "upload"

#: `scope → (设置键, 代码兜底值)`。兜底值与 §29.4 的表格一致，
#: 但 **API 总配额经监控方实测后由 300 上调到 1200**，理由见下方注释。
SCOPE_SETTINGS: dict[str, tuple[str, int]] = {
    #: M14 集成期修正（监控方实测，非推测）：**300 太贴近正常浏览**。
    #: 门户一页 24 个工具里 19 个有封面 → 一次加载约 21 个 `/api/v1` 请求
    #: （19 封面 + `/meta` + `/tools`），300/min 只够每分钟 14 次加载。
    #: 而超限的症状是**封面裂图** —— 用户看到的是「图片坏了」而不是「请求太快」，
    #: 很容易被当成缺陷来报。1200/min ≈ 每分钟 57 次加载，人类浏览绝无可能触及，
    #: 而失控客户端（每秒上百次）照样会被拦住。
    #: 若将来仍嫌紧，下一步**不是继续调大**，而是给封面图单独开一个更宽的 scope
    #: （封面是渲染副作用，不是用户意图）；本轮不这么做是为了不引入多余概念。
    SCOPE_API: ("security.rate_limit_per_minute", 1200),
    SCOPE_LOGIN: ("security.rate_limit_login_per_minute", 10),
    SCOPE_UPLOAD: ("security.rate_limit_upload_per_minute", 30),
}

#: 限流总开关
ENABLED_SETTING_KEY = "security.rate_limit_enabled"

#: 窗口长度（秒）。§29.4 的三档配额都是「每分钟」。
WINDOW_SECONDS = 60.0

#: **loopback 豁免名单**（§29.4 逐字）：真正从本机发起的来源不计入限流。
#: `testclient` 是 Starlette `TestClient` 的 ASGI client host；
#: httpx 的 `ASGITransport` 默认是 `127.0.0.1`。两者都在名单里，
#: 所以测试套件永远不会被限流影响。
LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})

#: 进程内最多保留多少个 `(scope, ip)` 桶 —— 防止伪造源 IP 把内存打爆。
MAX_BUCKETS = 10_000

#: 配置缓存存活秒数（见模块 docstring）
CONFIG_CACHE_SECONDS = 5.0


def is_loopback_source(ip: str | None) -> bool:
    """来源是否为本机（loopback 豁免名单，§29.4）。"""
    if not ip:
        return False
    host = ip.strip().lower()
    # `[::1]` 形态（某些反代会把 IPv6 写成带方括号的形式）
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host in LOOPBACK_HOSTS


# ---------------------------------------------------------------------------
# 滑动窗口限流器
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RateLimitDecision:
    """一次判定的结果。`retry_after` 单位是秒，放行时为 0。"""

    allowed: bool
    limit: int
    remaining: int
    retry_after: int


class SlidingWindowLimiter:
    """进程内滑动窗口计数器。

    精确滑动窗口（不是固定窗口分桶）：每个键保留窗口内**每个请求的时间戳**，
    因此「第 60 秒前后跨窗口突刺」的经典漏洞不存在（固定窗口在边界处会放行
    两倍配额）。代价是每键最多 `limit` 个时间戳，量级完全可接受。

    **不变量**：

      - 被拒绝的请求**不**写入窗口 —— 否则持续重试的客户端会把窗口一直撑住，
        永远出不来（「惩罚重试」不是这里想要的语义）。
      - `limit < 1` 视为**不限流**（fail-open）。设置项的下限是 1，这只防
        手改数据库或迁移缺失时的意外；把「配置成 0」解释成「谁都不许访问」
        会把平台直接锁死，那不是限流该有的失败模式。
      - `clock` 可注入，测试用固定时钟推进窗口（不 sleep）。
    """

    def __init__(
        self,
        *,
        window_seconds: float = WINDOW_SECONDS,
        max_buckets: int = MAX_BUCKETS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._window = float(window_seconds)
        self._max_buckets = int(max_buckets)
        self._clock = clock
        #: OrderedDict 兼作 LRU：`move_to_end` 记录「最近使用」，
        #: 桶数超限时淘汰最久未使用的那个（伪造 IP 洪水的内存兜底）。
        self._buckets: OrderedDict[tuple[str, str], deque[float]] = OrderedDict()

    def check(
        self,
        scope: str,
        client_ip: str | None,
        *,
        limit: int,
        now: float | None = None,
    ) -> RateLimitDecision:
        """记一次请求并返回判定。

        `now` 省略时用注入的时钟（生产为 `time.monotonic`）。

        `client_ip` 为 `None` 时落进一个**共享桶**（键 `""`）—— 即「拿不到来源
        地址」的请求共享一份配额，而不是免检（fail-closed）。真实部署里
        `request.client` 不会是 None（uvicorn 走 TCP）；这条只是兜底，
        免得某天出现一种拿不到 client 的接入方式时，限流被整体静默绕过。
        """
        if limit < 1:
            return RateLimitDecision(True, limit, 0, 0)

        moment = self._clock() if now is None else float(now)
        key = (scope, client_ip or "")
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = deque()
            self._buckets[key] = bucket

        cutoff = moment - self._window
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        self._buckets.move_to_end(key)

        if len(bucket) >= limit:
            # 窗口内第 limit 个请求的时间戳 + 一个完整窗口 = 最早能再放行的时刻
            retry_after = max(1, math.ceil(bucket[0] + self._window - moment))
            return RateLimitDecision(False, limit, 0, retry_after)

        bucket.append(moment)
        self._evict_overflow()
        return RateLimitDecision(True, limit, max(0, limit - len(bucket)), 0)

    def _evict_overflow(self) -> None:
        while len(self._buckets) > self._max_buckets:
            # 当前键刚被 move_to_end，所以淘汰的永远是最久未使用的**别的**键
            self._buckets.popitem(last=False)

    def bucket_count(self) -> int:
        """当前桶数（测试与排障用）。"""
        return len(self._buckets)

    def reset(self) -> None:
        """清空全部计数（测试用）。"""
        self._buckets.clear()


#: 进程级单例。路由依赖与测试都走它。
limiter = SlidingWindowLimiter()


# ---------------------------------------------------------------------------
# 配置解析（带 5 秒缓存）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RateLimitConfig:
    enabled: bool
    limits: dict[str, int]


_config_cache: tuple[float, RateLimitConfig] | None = None


def _int_or(value: object, fallback: int) -> int:
    """把设置行的原始值转成 int。bool 是 int 的子类，必须显式排除。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return fallback
    return int(value)


async def get_config(session: AsyncSession) -> RateLimitConfig:
    """读限流配置（总开关 + 三档配额），带 5 秒进程内缓存。"""
    global _config_cache
    now = time.monotonic()
    cached = _config_cache
    if cached is not None and now - cached[0] < CONFIG_CACHE_SECONDS:
        return cached[1]

    keys = [ENABLED_SETTING_KEY, *(key for key, _ in SCOPE_SETTINGS.values())]
    rows = await settings_repo.get_many(session, keys)

    def _fallback(key: str, default: object) -> object:
        """行缺失时回退到 `SETTING_DEFAULTS` 的代码默认值（与 get_effective_* 同口径）。"""
        spec = settings_repo.SETTING_DEFAULTS_BY_KEY.get(key)
        return spec[0] if spec is not None else default

    enabled_row = rows.get(ENABLED_SETTING_KEY)
    enabled = bool(
        enabled_row if enabled_row is not None else _fallback(ENABLED_SETTING_KEY, True)
    )
    limits = {
        scope: _int_or(
            rows.get(key) if rows.get(key) is not None else _fallback(key, default),
            default,
        )
        for scope, (key, default) in SCOPE_SETTINGS.items()
    }
    config = RateLimitConfig(enabled=enabled, limits=limits)
    _config_cache = (now, config)
    return config


def reset_config_cache() -> None:
    """使配置缓存失效。

    两个调用点：管理端改设置之后（`settings_service.update_settings`，
    让 FR-CFG-01 的「运行时生效」不被 5 秒缓存打折）、以及测试。
    """
    global _config_cache
    _config_cache = None


# ---------------------------------------------------------------------------
# 判定入口
# ---------------------------------------------------------------------------
async def enforce(
    session: AsyncSession,
    *,
    scope: str,
    client: ClientInfo,
    limiter_override: SlidingWindowLimiter | None = None,
) -> RateLimitDecision | None:
    """对一次请求做限流判定；被拒时抛 `RateLimitedError`（429 + Retry-After）。

    返回 `None` 表示**未参与限流**（loopback 豁免或总开关关闭）。
    """
    # ① loopback 豁免放在最前：本机请求连配置都不读（模块 docstring 有解释）
    if is_loopback_source(client.ip):
        return None

    config = await get_config(session)
    if not config.enabled:
        return None

    engine = limiter_override or limiter
    decision = engine.check(scope, client.ip, limit=config.limits[scope])
    if not decision.allowed:
        raise RateLimitedError(
            message="请求过于频繁，请稍后再试",
            details={
                "scope": scope,
                "limit": decision.limit,
                "retry_after_seconds": decision.retry_after,
            },
            headers={"Retry-After": str(decision.retry_after)},
        )
    return decision


def rate_limit_dependency(scope: str) -> Callable[..., object]:
    """生成某个作用域的 FastAPI 依赖。

    依赖里**不再创建会话**：`Depends(get_db)` 在同一个请求内是缓存的，
    所以这里拿到的就是路由自己那条会话，不会多开连接、也不会多一条查询
    （除非配置缓存刚好过期）。
    """

    async def _dependency(
        request: Request,
        session: Annotated[AsyncSession, Depends(get_db)],
    ) -> None:
        await enforce(session, scope=scope, client=get_client_info(request))

    _dependency.__name__ = f"rate_limit_{scope}"
    _dependency.__doc__ = f"限流依赖：作用域 `{scope}`（contracts/CONTRACT.md §29.4）"
    return _dependency


#: `/api/v1` 全局总配额（挂在 `app.include_router` 上，覆盖全部 99 个操作）
rate_limit_api = rate_limit_dependency(SCOPE_API)
#: 登录配额
rate_limit_login = rate_limit_dependency(SCOPE_LOGIN)
#: 上传配额
rate_limit_upload = rate_limit_dependency(SCOPE_UPLOAD)


__all__ = [
    "CONFIG_CACHE_SECONDS",
    "ENABLED_SETTING_KEY",
    "LOOPBACK_HOSTS",
    "MAX_BUCKETS",
    "SCOPE_API",
    "SCOPE_LOGIN",
    "SCOPE_SETTINGS",
    "SCOPE_UPLOAD",
    "WINDOW_SECONDS",
    "RateLimitConfig",
    "RateLimitDecision",
    "SlidingWindowLimiter",
    "enforce",
    "get_config",
    "is_loopback_source",
    "limiter",
    "rate_limit_api",
    "rate_limit_dependency",
    "rate_limit_login",
    "rate_limit_upload",
    "reset_config_cache",
]
