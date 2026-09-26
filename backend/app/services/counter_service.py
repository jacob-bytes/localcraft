"""计数器聚合服务（README「SQLite 风险说明」第 4 条 + 契约 §11 第 5 条）。

**禁止逐请求 UPDATE** —— SQLite 是单写者模型，每个下载都写一次
`UPDATE tools SET download_count = download_count + 1` 会直接把写锁打死。

所以计数走「内存累计 + 定时批量落库」：

    浏览/下载 → 内存计数器（无锁竞争）
    每 30 秒（可配）→ 一次事务里批量 UPDATE + UPSERT 每日汇总 + 批量插入明细

三条硬要求：

  1. **落库失败不得影响用户请求** —— 下载/浏览必须成功返回，失败只记日志
  2. **优雅关闭时必须 flush**（SIGTERM）—— 否则丢计数
  3. **浏览量去重** —— 同一用户对同一工具在 `stats.view_dedup_minutes`
     窗口内只计一次
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import update

from app.core.metrics import record_flush_failure
from app.core.timeutil import utcnow
from app.db.session import SessionLocal
from app.models.user import ApiToken
from app.repositories import downloads as downloads_repo

logger = logging.getLogger(__name__)

#: 默认落库间隔（秒）。docs/02 §5 建议 30 秒。
DEFAULT_FLUSH_INTERVAL_SECONDS = 30
#: 下载明细队列的批量阈值（docs/02 §3.17：每 100 条或每 10 秒）
DOWNLOAD_LOG_BATCH_SIZE = 100
#: 浏览去重窗口兜底值（分钟），实际取系统设置 `stats.view_dedup_minutes`
DEFAULT_VIEW_DEDUP_MINUTES = 60


class CounterService:
    """内存计数器。进程内单例，由应用 lifespan 启动/停止。"""

    def __init__(self, *, flush_interval_seconds: int = DEFAULT_FLUSH_INTERVAL_SECONDS) -> None:
        self._flush_interval = flush_interval_seconds

        self._download_deltas: dict[int, int] = defaultdict(int)
        self._view_deltas: dict[int, int] = defaultdict(int)
        #: (stat_date, tool_id) → 当日增量
        self._daily_views: dict[tuple[date, int], int] = defaultdict(int)
        self._daily_downloads: dict[tuple[date, int], int] = defaultdict(int)
        #: 浏览去重：viewer_key → {tool_id: 上次计数时间}
        self._view_seen: dict[str, dict[int, datetime]] = defaultdict(dict)
        #: 待批量插入的下载明细
        self._log_queue: list[dict[str, Any]] = []
        #: api_tokens.last_used_at / last_used_ip 的内存聚合（M3）
        self._token_last_used: dict[int, tuple[datetime, str | None]] = {}

        # 落库互斥锁**不在这里建** —— 见 `_flush_lock()`：
        # asyncio 的同步原语在首次使用时绑定当时的事件循环，跨 loop 复用会抛
        # `RuntimeError: ... is bound to a different event loop`。
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    # ------------------------------------------------------------------
    # 事件循环无关性（M10）
    # ------------------------------------------------------------------
    def _flush_lock(self) -> asyncio.Lock:
        """取「当前事件循环」的落库锁（惰性创建，按 loop 隔离）。

        为什么不在 `__init__` 里建一把 `asyncio.Lock`：`asyncio` 的同步原语
        （Lock / Queue / Event）在**首次使用**时把 `_loop` 钉死为当时那个事件循环，
        之后在另一个 loop 上用就抛
        `RuntimeError: ... is bound to a different event loop`。
        `CounterService` 是**进程内单例**，所以只要进程内重建过事件循环
        （`uvicorn --reload`、测试里每个用例一个 loop、将来可能的多 loop 场景），
        那把锁就会变成跨 loop 的雷 —— 与 M10 修掉的连接池队列是同一类问题。

        为什么按 loop 惰性重建是安全的：

          1. **状态本身不依赖 loop。** 增量缓冲全是普通 Python 容器
             （`dict` / `defaultdict` / `list`），`record_*` 是同步方法、不 await、
             不创建任何 loop 对象；因此换 loop 不会让缓冲失效。
          2. **真正需要原子性的临界区是同步的。** `flush()` 里「取走增量 + 置空缓冲」
             这一段没有任何 `await`，在单线程事件循环里它本身就是一次不可分割的
             执行片段 —— 锁只是让两个并发 `flush()` 不重复做同一批 DB 工作，
             不是数据正确性的唯一依赖。
          3. **一个进程同一时刻只有一个运行中的 loop。** 新 loop 是在旧 loop
             结束后才出现的（`asyncio.run` / 新用例），不存在两个 loop 同时用这个
             单例的情形；所以「每个 loop 一把锁」不会退化成「两把锁各管各的」。
        """
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    # ------------------------------------------------------------------
    # 记录（同步、非阻塞 —— 请求路径上只碰内存）
    # ------------------------------------------------------------------
    def record_view(
        self, tool_id: int, *, viewer_key: str, dedup_minutes: int | None = None
    ) -> bool:
        """记一次浏览。返回是否**真的计入**（去重后）。

        去重窗口内重复浏览不计数，但仍算「浏览成功」——
        调用方不需要区分。
        """
        window = DEFAULT_VIEW_DEDUP_MINUTES if dedup_minutes is None else dedup_minutes
        now = utcnow()
        if window > 0:
            seen = self._view_seen[viewer_key]
            last = seen.get(tool_id)
            if last is not None and now - last < timedelta(minutes=window):
                return False
            seen[tool_id] = now
            # 顺手淘汰过期条目，避免长跑进程内存单调增长
            if len(seen) > 5000:
                cutoff = now - timedelta(minutes=window)
                for key in [k for k, v in seen.items() if v < cutoff]:
                    seen.pop(key, None)

        self._view_deltas[tool_id] += 1
        self._daily_views[(now.date(), tool_id)] += 1
        return True

    def record_download(
        self,
        *,
        tool_id: int,
        version_id: int | None,
        user_id: int | None,
        ip: str | None,
        user_agent: str | None,
        via_api_token_id: int | None = None,
    ) -> None:
        """记一次下载。只入内存队列，不碰数据库。"""
        now = utcnow()
        self._download_deltas[tool_id] += 1
        self._daily_downloads[(now.date(), tool_id)] += 1
        self._log_queue.append(
            {
                "tool_id": tool_id,
                "version_id": version_id,
                "user_id": user_id,
                "ip": ip,
                # user_agent 列是 String(255)，在这里截断而不是让 DB 报错
                "user_agent": (user_agent or None) and user_agent[:255],
                "via_api_token_id": via_api_token_id,
                "created_at": now,
            }
        )

    def record_token_use(self, token_id: int, *, ip: str | None = None) -> None:
        """记录 API Token 的使用（FR-API-05）。

        **绝不逐请求写库**（易错点 1）：SQLite 是单写者，每个带 Token 的请求
        写一次 `api_tokens` 会直接把写锁打死。这里只更新内存里的最新值，
        由后台任务批量落库 —— 与下载计数复用同一套聚合框架。
        """
        self._token_last_used[token_id] = (utcnow(), ip)

    # ------------------------------------------------------------------
    # 落库
    # ------------------------------------------------------------------
    async def flush(self) -> dict[str, int]:
        """把内存里的增量写库。**任何异常都只记日志，不向上抛**。"""
        async with self._flush_lock():
            if not self._has_pending():
                return {"counter_tools": 0, "daily": 0, "logs": 0, "tokens": 0}

            # 先「取走」再落库：即使落库失败也不会阻塞后续计数继续累积
            download_deltas = dict(self._download_deltas)
            view_deltas = dict(self._view_deltas)
            daily_views = dict(self._daily_views)
            daily_downloads = dict(self._daily_downloads)
            log_queue = self._log_queue
            token_uses = dict(self._token_last_used)

            self._download_deltas = defaultdict(int)
            self._view_deltas = defaultdict(int)
            self._daily_views = defaultdict(int)
            self._daily_downloads = defaultdict(int)
            self._log_queue = []
            self._token_last_used = {}

        stats = {"counter_tools": 0, "daily": 0, "logs": 0, "tokens": 0}
        try:
            async with SessionLocal() as session:
                stats["counter_tools"] = await downloads_repo.apply_counters(
                    session, downloads=download_deltas, views=view_deltas
                )

                by_date: dict[date, tuple[dict[int, int], dict[int, int]]] = {}
                for (stat_date, tool_id), value in daily_views.items():
                    by_date.setdefault(stat_date, ({}, {}))[0][tool_id] = value
                for (stat_date, tool_id), value in daily_downloads.items():
                    by_date.setdefault(stat_date, ({}, {}))[1][tool_id] = value
                for stat_date, (views, dl) in by_date.items():
                    stats["daily"] += await downloads_repo.upsert_daily_stats(
                        session, stat_date=stat_date, views=views, downloads=dl
                    )

                if log_queue:
                    stats["logs"] = await downloads_repo.bulk_insert_logs(
                        session, log_queue, now=utcnow()
                    )

                for token_id, (used_at, used_ip) in token_uses.items():
                    values: dict[str, Any] = {"last_used_at": used_at}
                    if used_ip:
                        values["last_used_ip"] = used_ip[:45]
                    await session.execute(
                        update(ApiToken).where(ApiToken.id == token_id).values(**values)
                    )
                    stats["tokens"] += 1

                await session.commit()
        except Exception:
            # 关键：计数落库失败不能影响用户请求，也不能让后台任务死掉
            logger.exception("计数器落库失败，已丢弃本批增量（用户请求不受影响）")
            # M3（docs/11 §3.4）：这里此前**只记日志**，失败次数没有任何可观测出口 ——
            # 「静默丢计数」是这套内存聚合设计最危险的失效模式（丢的是下载/浏览量，
            # 不会有人立刻发现）。现在累进指标，由 /metrics 暴露。
            # `record_flush_failure` 自身只动内存整数、绝不抛异常，
            # 不能让它把「已经处理好的失败」变成新的失败。
            record_flush_failure()
            return {k: 0 for k in stats}

        logger.debug("计数器落库完成 %s", stats)
        return stats

    def _has_pending(self) -> bool:
        return bool(
            self._download_deltas
            or self._view_deltas
            or self._daily_views
            or self._daily_downloads
            or self._log_queue
            or self._token_last_used
        )

    # ------------------------------------------------------------------
    # 后台任务
    # ------------------------------------------------------------------
    async def _run(self) -> None:
        """定时 flush。

        使用「每轮 sleep 一小段并检查是否有积压」而不是「sleep 满 30 秒」，
        这样下载日志队列达到批量阈值时能更快落库。
        """
        tick = 1.0
        elapsed = 0.0
        while not self._stopping:
            await asyncio.sleep(tick)
            elapsed += tick
            need_flush = elapsed >= self._flush_interval or (
                len(self._log_queue) >= DOWNLOAD_LOG_BATCH_SIZE
            )
            if need_flush:
                elapsed = 0.0
                await self.flush()

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        if self._task is not None and not self._task.done():
            if self._task.get_loop() is loop:
                return  # 幂等：当前 loop 里已经在跑
            # 任务属于另一个（多半已结束的）事件循环。跨 loop await 会抛
            # "attached to a different loop"，所以只尽力取消、丢弃引用，
            # 在当前 loop 重建 —— 否则新 loop 里 flush 循环永远不会跑起来。
            logger.warning("计数器后台任务属于另一个事件循环，已丢弃并在当前循环重建")
            with contextlib.suppress(Exception):
                self._task.cancel()
            self._task = None
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="localcraft-counter-flush")
        logger.info("计数器后台任务已启动（每 %s 秒落库）", self._flush_interval)

    async def stop(self) -> None:
        """优雅关闭：停掉循环并做最后一次 flush（SIGTERM 不能丢计数）。"""
        self._stopping = True
        task = self._task
        self._task = None
        if task is not None:
            if task.get_loop() is asyncio.get_running_loop():
                task.cancel()
                # 取消是预期路径；任务自身的异常也已经在自己内部记过日志了
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            else:
                # 同上：不能跨 loop await，尽力取消后放弃引用。
                # 最后的 flush 仍在当前 loop 里执行，计数不会因此丢失。
                logger.warning("计数器后台任务属于另一个事件循环，取消时跳过等待")
                with contextlib.suppress(Exception):
                    task.cancel()
        # 最后一次落库 —— 这一步失败也只是记日志
        await self.flush()
        logger.info("计数器后台任务已停止，残留计数已落库")

    # ------------------------------------------------------------------
    # 观测（测试与排障用）
    # ------------------------------------------------------------------
    def pending_snapshot(self) -> dict[str, int]:
        return {
            "downloads": sum(self._download_deltas.values()),
            "views": sum(self._view_deltas.values()),
            "logs": len(self._log_queue),
            "tokens": len(self._token_last_used),
        }


#: 进程内单例。测试里可以替换。
_counter_service: CounterService | None = None


def get_counter_service() -> CounterService:
    global _counter_service
    if _counter_service is None:
        _counter_service = CounterService()
    return _counter_service


def set_counter_service(service: CounterService | None) -> None:
    """测试用：注入一个全新的实例（避免用例之间互相污染）。"""
    global _counter_service
    _counter_service = service


def viewer_key_for(user_id: int | None, ip: str | None) -> str:
    """浏览去重的键：登录用户按 user_id，匿名按 IP。

    FR-统计只要求「同一用户」，但匿名流量也要去重，
    否则一个刷新脚本就能把浏览量刷爆。
    """
    if user_id is not None:
        return f"u:{user_id}"
    return f"ip:{ip or 'unknown'}"


__all__ = [
    "DEFAULT_FLUSH_INTERVAL_SECONDS",
    "DOWNLOAD_LOG_BATCH_SIZE",
    "CounterService",
    "get_counter_service",
    "set_counter_service",
    "viewer_key_for",
]
