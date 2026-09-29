"""在线工具探活（`contracts/CONTRACT.md` §29.2，冻结）。

背景：设置项 `webapp.health_check_enabled`、`/meta` 的
`features.webapp_health_check`、以及 `tools` 表的三个列
（`webapp_health_url` / `webapp_health_status` / `webapp_checked_at`）**早就在**，
但**没有任何探活实现** —— 预览库 26 行的 `webapp_health_status` 全是 NULL。
「东西已存在但没接通」的第一例（§29.1）。

## 探测规则（§29.2 逐条照做，不自行放宽）

| 项 | 值 |
| --- | --- |
| 方法 | `HEAD`；返回 405 / 501 时回退 `GET` |
| 超时 | 5 秒（连接 + 读取） |
| 并发 | 8 |
| 每次运行上限 | 200 个工具 |
| **重定向** | **不跟随**。3xx 视为**可达（ok）**，但不继续请求 `Location` |
| `ok` | 2xx 或 3xx |
| `timeout` | 超时 |
| `fail` | 连接错误、DNS 失败、4xx、5xx |

**不跟随重定向是刻意的**（§29.2）：`webapp_health_url` 是管理员填的，
跟随重定向会把探测变成「以服务端身份访问任意主机」的跳板。实现方式是用
`HTTPRedirectHandler` 的子类让 `redirect_request()` 返回 `None` ——
urllib 于是把 3xx 当作 `HTTPError` 抛出，我们只读 `code`，**一个字节的
`Location` 都不会被请求**（`tests/test_m14_webapp_health.py` 用一台真实
本地 HTTP 服务器记录命中路径来证明这一点）。

## 为什么用标准库而不是 httpx

硬约束是「不新增依赖」。`httpx` 在本项目里是 **dev** 依赖（`pyproject.toml`
的 `[project.optional-dependencies].dev`），生产安装（`requirements.lock`）
里没有它。用 `urllib.request` 就够：只需要 HEAD/GET、超时、不跟随重定向。

## 为什么探测在**线程**里跑

`urllib` 是阻塞 IO，`asyncio.to_thread` + `Semaphore(8)` 得到 8 路并发，
而不是在事件循环里同步阻塞（那会让整个服务停摆 5 秒 × N）。
"""

from __future__ import annotations

import asyncio
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutil import utcnow
from app.models.enums import ToolType
from app.models.tool import Tool
from app.repositories import system_settings as settings_repo

logger = logging.getLogger(__name__)

#: 探测超时（连接 + 读取），§29.2
HTTP_TIMEOUT_SECONDS = 5.0
#: 每次运行最多探测多少个工具，§29.2
MAX_TOOLS_PER_RUN = 200
#: 并发度，§29.2
CONCURRENCY = 8
#: 总开关设置键，§29.2
ENABLED_SETTING_KEY = "webapp.health_check_enabled"

#: 三个状态值（§29.2）。`NULL` 表示**从未检测过**，与 `fail` 是两回事。
STATUS_OK = "ok"
STATUS_FAIL = "fail"
STATUS_TIMEOUT = "timeout"

#: 收到这些状态码时把 `HEAD` 回退成 `GET`（§29.2）
FALLBACK_TO_GET_ON: frozenset[int] = frozenset({405, 501})

#: 探测请求的 UA —— 便于被探测方在访问日志里认出这是探活而不是攻击
USER_AGENT = "localcraft-healthcheck/1.0"


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """让 urllib **不跟随**重定向（§29.2）。

    `build_opener` 会用本类**替换**默认的 `HTTPRedirectHandler`
    （它按「传入的实例是默认处理器类的实例」判断，子类同样命中），
    所以不会出现「两个重定向处理器、默认那个仍然跟随」的情况。

    `redirect_request()` 返回 `None` 会让 `http_error_30x` 放弃处理，
    最终由 `http_error_default` 抛出携带原始状态码的 `HTTPError` ——
    我们只读它，`Location` 从头到尾没有被访问。
    """

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


#: 进程级 opener：**无重定向 + 不走代理**。
#:
#: 为什么显式关掉代理（`ProxyHandler({})`）：urllib 默认会读 `http_proxy` /
#: `https_proxy` 环境变量。systemd 的 `EnvironmentFile` 里若带了这类变量
#: （运维环境里很常见），探活就会变成「让代理替我去访问这个 URL」——
#: 既与「不跟随重定向」的初衷相悖（同样是把探测变成访问任意主机的跳板），
#: 也让探测结果取决于环境变量而不是被探测方是否可达：本机实测，
#: 一个**解析不了**的域名在代理存在时返回 502，于是它被归成 `fail` 的原因
#: 其实是代理，不是 DNS（报告里记了这条实测）。内网探活直连才是它的语义。
_OPENER = urllib.request.build_opener(
    _NoRedirectHandler, urllib.request.ProxyHandler({})
)


def _single_request(url: str, *, timeout: float, method: str) -> int:
    """发一次请求并返回状态码（**不跟随重定向**）。

    4xx / 5xx / 3xx 都以 `HTTPError` 形式抛出，这里只取 `code` 返回 ——
    状态码是判定所需的全部信息，响应体一并丢弃（探活不该下载内容）。
    """
    request = urllib.request.Request(
        url, method=method, headers={"User-Agent": USER_AGENT}
    )
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        code = int(exc.code)
        exc.close()
        return code


def probe_health_url(
    url: str, *, timeout: float = HTTP_TIMEOUT_SECONDS
) -> tuple[str, int | None]:
    """探测一个 URL，返回 `(status, http_status)`。

    `status` 是 §29.2 的三值之一；`http_status` 只在真的拿到响应码时有值
    （超时/连接失败为 `None`），仅用于日志。
    """
    try:
        status_code = _single_request(url, timeout=timeout, method="HEAD")
        if status_code in FALLBACK_TO_GET_ON:
            status_code = _single_request(url, timeout=timeout, method="GET")
    except TimeoutError:
        # 连接或读取超时。`socket.timeout` 在 3.10+ 就是 `TimeoutError` 的别名。
        return STATUS_TIMEOUT, None
    except urllib.error.URLError as exc:
        # urllib 会把底层超时包在 URLError.reason 里
        if isinstance(exc.reason, TimeoutError):
            return STATUS_TIMEOUT, None
        return STATUS_FAIL, None
    except OSError:
        # 连接被拒、DNS 失败、TLS 握手失败、重置连接……
        return STATUS_FAIL, None
    except ValueError:
        # 未知协议/非法 URL —— 管理员填错，算不可达
        return STATUS_FAIL, None

    if 200 <= status_code < 400:
        return STATUS_OK, status_code
    return STATUS_FAIL, status_code


@dataclass
class WebappHealthResult:
    """一次探活的汇总。"""

    skipped: bool = False
    checked: int = 0
    ok: int = 0
    fail: int = 0
    timeout: int = 0
    #: 候选工具数超过本次上限（§29.2 的 200）时为真 —— 剩下的下次再测
    truncated: bool = False
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "webapp_checked": self.checked,
            "webapp_ok": self.ok,
            "webapp_fail": self.fail,
            "webapp_timeout": self.timeout,
            "webapp_skipped": self.skipped,
            "webapp_truncated": self.truncated,
        }


def _candidate_statement(limit: int):
    """候选工具查询：`deleted_at IS NULL` + `tool_type='webapp'` + URL 非空。

    排序刻意用「**从未测过的优先**，然后最久没测的优先」：上限 200 是为了
    不让一次维护跑太久，但如果每次都从头取，工具数超过 200 时**后面的永远
    测不到**（饥饿）。用 `webapp_checked_at` 升序 + `NULL` 排最前，
    每次运行都会轮到被上次挤掉的那批。

    `NULLS FIRST` 在 SQLite 上要 3.30+，这里改用 `CASE WHEN ... IS NULL`，
    两种方言都稳（与 `tests/` 里对 SQLite 版本的假设解耦）。

    `limit + 1` 取一条多余的行，用来判断「是否被截断」。
    """
    return (
        select(Tool.id, Tool.webapp_health_url)
        .where(
            Tool.deleted_at.is_(None),
            Tool.tool_type == ToolType.WEBAPP.value,
            Tool.webapp_health_url.is_not(None),
            Tool.webapp_health_url != "",
        )
        .order_by(
            case((Tool.webapp_checked_at.is_(None), 0), else_=1),
            Tool.webapp_checked_at.asc(),
            Tool.id.asc(),
        )
        .limit(limit + 1)
    )


async def list_candidates(
    session: AsyncSession, *, limit: int = MAX_TOOLS_PER_RUN
) -> list[tuple[int, str]]:
    """取本次要探测的工具 `(id, url)`，最多 `limit` 个。"""
    rows = (await session.execute(_candidate_statement(limit))).all()
    return [(int(row[0]), str(row[1])) for row in rows]


async def run(
    session: AsyncSession,
    *,
    limit: int = MAX_TOOLS_PER_RUN,
    concurrency: int = CONCURRENCY,
    timeout: float = HTTP_TIMEOUT_SECONDS,
    dry_run: bool = False,
) -> WebappHealthResult:
    """执行一次在线工具探活（maintenance 任务 `webapp-health`）。

    - 总开关 `webapp.health_check_enabled` 为 `false` → **直接跳过**（§29.2）
    - `dry_run=True` → 照常探测，但**一个字段都不写库**
    """
    result = WebappHealthResult()

    enabled = await settings_repo.get_effective_bool(session, ENABLED_SETTING_KEY, False)
    if not enabled:
        result.skipped = True
        logger.info(
            "跳过在线工具探活：设置项 %s 为 false（管理端「设置」里可开启）",
            ENABLED_SETTING_KEY,
        )
        return result

    rows = await list_candidates(session, limit=limit)
    if len(rows) > limit:
        result.truncated = True
        rows = rows[:limit]
    if not rows:
        return result

    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _probe(tool_id: int, url: str) -> tuple[int, str, str, int | None]:
        async with semaphore:
            status, http_status = await asyncio.to_thread(
                probe_health_url, url, timeout=timeout
            )
        return tool_id, url, status, http_status

    outcomes = await asyncio.gather(*(_probe(tool_id, url) for tool_id, url in rows))

    now = utcnow()
    for tool_id, url, status, http_status in outcomes:
        result.checked += 1
        setattr(result, status, getattr(result, status) + 1)
        if status != STATUS_OK:
            logger.info(
                "在线工具探活未通过：tool_id=%s status=%s http=%s url=%s",
                tool_id,
                status,
                http_status,
                url,
            )
        if dry_run:
            continue
        tool = await session.get(Tool, tool_id)
        if tool is None:  # pragma: no cover - 候选集刚查出来，正常不会消失
            continue
        tool.webapp_health_status = status
        tool.webapp_checked_at = now

    if not dry_run:
        await session.commit()
    return result


__all__ = [
    "CONCURRENCY",
    "ENABLED_SETTING_KEY",
    "FALLBACK_TO_GET_ON",
    "HTTP_TIMEOUT_SECONDS",
    "MAX_TOOLS_PER_RUN",
    "STATUS_FAIL",
    "STATUS_OK",
    "STATUS_TIMEOUT",
    "USER_AGENT",
    "WebappHealthResult",
    "list_candidates",
    "probe_health_url",
    "run",
]
