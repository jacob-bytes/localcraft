"""每请求 SQL 语句计数器（docs/11 §2.4 **O12**）。

**为什么需要它**：在此之前没有任何地方记录「一个请求发了几条 SQL」。
N+1 回归是**静默**的 —— 功能测试全过、响应形状一字不差、延迟只多几毫秒，
只有并发上去了才看得出来。O5（合并 COUNT 与 facets）与 O4（sync vs async）
两个后续优化都以「查询数」为输入，没有它连现状都说不清。

设计要点
--------

1. **只在测试 / 调试 / 显式开启指标收集时挂事件监听器。**
   生产默认**完全不挂** `before_cursor_execute`，不给每个请求增加常数开销
   —— 而不是「挂了钩子但里面 return」。两者的差别是真实的：
   挂了钩子意味着每条 SQL 都要过一次 Python 函数调用与一次 `ContextVar.get()`。

2. **`ContextVar` 里放的是一个可变对象（`SqlCounter`），不是一个整数。**
   这一条是被实测逼出来的，值得单独记下来：Starlette 的
   `BaseHTTPMiddleware` 在**复制出来的 context** 里跑下游应用，
   于是下游对 `ContextVar` 的 `set()` **不会**被中间件看到 ——
   「在中间件里 `set(0)`、在端点里 `+1`、回中间件读」恒为 0，
   而且完全静默（计数永远是 0，看起来像「这个端点没查库」）。
   实测：`in_endpoint` 能读到中间件设的值，但中间件读不到端点改的值。
   改成「`set` 一个可变对象、之后只改对象字段」后两边指向同一个对象，才能看见。

3. **计数发生在 `before_cursor_execute`**，即语句真正要发到 DBAPI 之前。
   因此它数的是「应用发出的语句」，**不含** SQLAlchemy 连接池的探活
   （池不经过 cursor 事件）与 SQLite 的 PRAGMA（那不是 cursor 执行）。

4. 计数对**当前请求**可见（`request.state.sql_count`，以及只在计数开启时
   出现的响应头 `X-SQL-Count`），同时汇总进 `app.core.metrics`
   （B3 的「每端点 SQL 条数」指标）。

与 `app.core.metrics` 的分工：本模块只负责「数」，聚合与文本输出在 metrics
—— 指标端点默认关闭，而计数器在测试里始终可用，两者的启用条件不同。
"""

from __future__ import annotations

import contextvars
import logging
import os
from dataclasses import dataclass
from typing import Any

from sqlalchemy import event

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class SqlCounter:
    """一次请求（或一段代码块）的语句计数。

    可变对象 —— 见模块 docstring 第 2 条：跨 `BaseHTTPMiddleware` 的
    task/context 边界传递的**必须是同一个对象**，不能是 rebind 出来的新值。
    """

    statements: int = 0
    #: 由 `app.core.pool_metrics` 用的「会话开始时刻」。挂在同一个对象上，
    #: 就避免了再引入一个需要跨 context 边界的 ContextVar。
    session_started_at: float | None = None
    wait_recorded: bool = True

    def add(self, count: int = 1) -> None:
        self.statements += count


#: 当前 context 正在使用的计数器。`None` = 未在计数。
_CURRENT: contextvars.ContextVar[SqlCounter | None] = contextvars.ContextVar(
    "localcraft_sql_counter", default=None
)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def counting_enabled() -> bool:
    """是否启用每请求 SQL 计数。

    三个来源，任一成立即可：

    - `LOCALCRAFT_DEBUG=true`（本地排障）
    - `LOCALCRAFT_SQL_COUNT=1`（显式开关；`tests/conftest.py` 用它，
      这样测试**不依赖** debug 取值 —— debug 会顺带打开别的行为，不该被测试牵连）
    - `LOCALCRAFT_METRICS_ENABLED=true`（B3 的 `/metrics` 要暴露每端点 SQL 条数）
    """
    return (
        settings.localcraft_debug
        or _truthy(os.environ.get("LOCALCRAFT_SQL_COUNT"))
        or _truthy(os.environ.get("LOCALCRAFT_METRICS_ENABLED"))
    )


def current_counter() -> SqlCounter | None:
    """当前 context 的计数器；未在计数时返回 `None`。"""
    return _CURRENT.get()


def start_counting(
    *, session_started_at: float | None = None, wait_recorded: bool = True
) -> SqlCounter:
    """在当前 context 上开始计数，返回计数器对象。

    调用方负责在结束时用 `stop_counting()` 还原（通常用 `try/finally`）。
    """
    counter = SqlCounter(
        session_started_at=session_started_at, wait_recorded=wait_recorded
    )
    _CURRENT.set(counter)
    return counter


def stop_counting() -> None:
    """停止计数。只解除当前 context 的绑定，**不**动已经累加的数字。"""
    _CURRENT.set(None)


def add_statements(count: int = 1) -> None:
    """把 `count` 条语句记到当前计数器。未在计数时是空操作。"""
    counter = _CURRENT.get()
    if counter is not None:
        counter.add(count)


def _on_before_cursor_execute(
    conn: Any,
    cursor: Any,
    statement: str,
    parameters: Any,
    context: Any,
    executemany: bool,
) -> None:
    del conn, cursor, statement, parameters, context, executemany
    add_statements(1)
    # M4：顺手记一次「取连接等待时长」（第一条语句才算，见 pool_metrics）。
    # 放在这里而不是另外挂一个事件：这个钩子本来就要为每条语句跑一次，
    # 多一次 `ContextVar.get()` 的代价远低于再挂一个监听器。
    from app.core.pool_metrics import note_first_statement

    note_first_statement()


def install_sql_counter(engine: Any) -> bool:
    """在 `engine`（`AsyncEngine` 或同步 `Engine`）上挂计数器。

    **幂等**：重复调用不会挂两次（`event.contains` 判定），
    否则计数会翻倍 —— 这是最容易静默出错的地方。

    生产默认不调用本函数（见模块 docstring 第 1 条）。
    """
    sync_engine = getattr(engine, "sync_engine", engine)
    if event.contains(sync_engine, "before_cursor_execute", _on_before_cursor_execute):
        return False
    event.listen(sync_engine, "before_cursor_execute", _on_before_cursor_execute)
    return True


def uninstall_sql_counter(engine: Any) -> bool:
    """摘掉计数器（测试里做 A/B 对照、或测「生产零开销」时用）。"""
    sync_engine = getattr(engine, "sync_engine", engine)
    if not event.contains(sync_engine, "before_cursor_execute", _on_before_cursor_execute):
        return False
    event.remove(sync_engine, "before_cursor_execute", _on_before_cursor_execute)
    return True


def is_installed(engine: Any) -> bool:
    """计数器是否已挂在 `engine` 上。测试用它验证「生产默认不挂」。"""
    sync_engine = getattr(engine, "sync_engine", engine)
    return event.contains(sync_engine, "before_cursor_execute", _on_before_cursor_execute)


__all__ = [
    "SqlCounter",
    "add_statements",
    "counting_enabled",
    "current_counter",
    "install_sql_counter",
    "is_installed",
    "start_counting",
    "stop_counting",
    "uninstall_sql_counter",
]
