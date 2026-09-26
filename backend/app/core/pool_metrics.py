"""连接池等待时长（docs/11 §3.4 **M4**：连接池饱和度不可见）。

为什么不能只报 `pool.checkedout()`
----------------------------------

`checkedout()` 是**瞬时快照**：运维在两次抓取之间看到的永远是「此刻有几个在用」。
而真正回答问题的是「**请求等连接等了多久**」——
池饱和时 checkout 会排队，排队时间直接变成用户看到的延迟，
但 `checkedout()` 在排队发生时可能显示得好好的（连接刚被还回去）。

所以这里报的是**每请求的等待时长**：从 `get_db` 建立会话（请求进入 DB 路径）
到**第一条语句真正执行**之间的时间。这个差值包含：

- SQLAlchemy 池的 checkout 排队（池满时的主要成本）
- 连接创建（池需要新建连接时）
- `sqlite3.connect` 与随后 PRAGMA 的开销（首次建连，SQLite 特有）

它是「连接到 DB 之前的等待」的一个**上界代理量**，不是池内部 `_do_get` 的
精确计时 —— 后者要 hook SQLAlchemy 的私有方法，脆弱且不划算。
对一个「池是否成为瓶颈」的判断题，这个量足够；报告里已如实声明它是代理量。

实现上的两个必须注意的点
------------------------

1. `AsyncSessionLocal()` **不**获取连接 —— SQLAlchemy 的 `Session.execute`
   是惰性连库的。因此「建会话」到「第一条语句」之间的时间**才是**取连接的时间；
   如果在 `get_db` 里直接计时 `async with SessionLocal()`，量到的几乎是 0
   （而且会给出「池从来不排队」这种错误的安全感）。

2. 时间戳与「是否已记录」都挂在 **`SqlCounter` 对象**上，而不是另开
   `ContextVar`：Starlette 的 `BaseHTTPMiddleware` 在复制出来的 context 里
   跑下游应用，下游对 `ContextVar` 的 `set()` 传不回中间件（见
   `app/core/sql_counter.py` 的 docstring 第 2 条）。挂共享对象才可靠。

计数通过 `app/core/sql_counter.py` 的 `before_cursor_execute` 钩子实现 ——
那个钩子本来就要遍历每条语句，顺手算一次差值不额外增加遍历。
**未启用计数时不安装任何钩子**，生产零开销。
"""

from __future__ import annotations

import time

#: 只记**第一条**语句的差值：第二条语句的差值反映的是查询本身耗时，
#: 不是取连接的等待，混进去会把 P95 抬高一个数量级。
#: 该标志存在 `SqlCounter.wait_recorded` 上（默认 True = 不记）。


def begin_session_timing() -> None:
    """标记「请求已进入 DB 路径」，开始计时。由 `app.db.session.get_db` 调用。"""
    from app.core.sql_counter import current_counter

    counter = current_counter()
    if counter is None:
        return
    counter.session_started_at = time.perf_counter()
    counter.wait_recorded = False


def end_session_timing() -> None:
    """结束计时（请求收尾）。"""
    from app.core.sql_counter import current_counter

    counter = current_counter()
    if counter is None:
        return
    counter.session_started_at = None
    counter.wait_recorded = True


def note_first_statement() -> None:
    """由 SQL 计数钩子在**每条**语句前调用；只有第一条会真正记录。"""
    from app.core.sql_counter import current_counter

    counter = current_counter()
    if counter is None or counter.wait_recorded or counter.session_started_at is None:
        return
    counter.wait_recorded = True
    from app.core.metrics import record_pool_wait

    record_pool_wait((time.perf_counter() - counter.session_started_at) * 1000)


__all__ = [
    "begin_session_timing",
    "end_session_timing",
    "note_first_statement",
]
