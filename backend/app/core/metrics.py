"""进程内指标聚合 + Prometheus 文本格式输出（docs/11 §3.4 **M1**）。

范围与边界
----------

- **不引入 `prometheus_client`**（任务书 B3 第 6 条）。项目重视离线可安装与依赖最小化；
  Prometheus 的文本 exposition 格式本身很简单，手写即可，也不需要
  `# HELP` / `# TYPE` 之外的任何东西。
- **单进程内存态**。指标不跨进程共享 —— 多 worker 下每个 worker 各报一份，
  由 Prometheus 侧的 `sum by` 聚合。这一点在 `/metrics` 的文档里写明，
  避免有人以为读到的就是全站总数。
- **`/metrics` 位于 `/api/v1` 冻结面之外**，不注册进 OpenAPI（任务书 B3 第 1、2 条）。
  本模块只管数据，路由在 `app/api/metrics.py`。

关于延迟分位数
--------------

用**每路由一个有界蓄水池**（reservoir）保存耗时样本，而不是直方图桶：
桶的边界一旦写死就会在「哪一档都落不进去」或者「全落进第一档」时失去分辨率，
而本项目真正关心的是「P95 是多少」这一个数。蓄水池大小 1024 —— 在
`LOCALCRAFT_METRICS_SAMPLE_MAX` 之内存全量，超过后按**水塘抽样**替换，
保证任意时刻的样本都是无偏的（简单「保留前 N 个」会有严重的时间偏置：
压测刚开始的预热请求会永久占据样本）。

代价是分位数是**估计值**，与真实分位数有抽样误差（1024 样本下 P95 的
标准误约在百分位量级）。单机、单端点、万级请求下够用；要精确分位数
就得上直方图，那是引入 Prometheus 客户端之后的事，本轮明确不做。
"""

from __future__ import annotations

import random
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.core.config import settings

if TYPE_CHECKING:  # pragma: no cover - 仅为类型检查
    from sqlalchemy.ext.asyncio import AsyncEngine

#: 每个路由保留的耗时样本上限（见模块 docstring「关于延迟分位数」）。
DEFAULT_SAMPLE_MAX = 1024

#: `_route_template` 取不到模板时用的兜底桶名。把它做成常量便于测试引用，
#: 也避免两处各写一遍字面量导致对不上。
UNMATCHED_ROUTE = "<unmatched>"


@dataclass
class RouteMetrics:
    """单个 `(method, route_template)` 的累计量。"""

    requests: int = 0
    errors: int = 0  # 5xx
    slow: int = 0
    sql_total: int = 0
    sql_requests: int = 0  # 参与 sql_total 的请求数（SQL 计数未启用时两者都是 0）
    samples: list[float] = field(default_factory=list)
    seen: int = 0  # 进入过蓄水池的样本总数（用于水塘抽样）

    def observe(self, duration_ms: float, sql_count: int, status: int, slow: bool) -> None:
        self.requests += 1
        if status >= 500:
            self.errors += 1
        if slow:
            self.slow += 1
        self.sql_total += sql_count
        if sql_count > 0 or sql_counting_on:
            self.sql_requests += 1
        self.samples.append(duration_ms)
        self.seen += 1
        if len(self.samples) > _sample_max():
            # 水塘抽样：随机挑一个位置替换，再丢掉尾部。
            # `random.randrange(self.seen)` 覆盖 `[0, seen)`，落在**新区间之外**
            # 时说明新样本本身就该被丢掉 —— 此时什么都不做，直接丢尾部。
            # （先 pop 再替换会把刚放进去的那个又扔掉，等于永远保留前 N 个。）
            index = random.randrange(self.seen)
            if index < len(self.samples):
                self.samples[index] = duration_ms
            self.samples.pop()

    def quantile(self, q: float) -> float:
        """样本分位数（最近邻法，不插值）。无样本时返回 0.0。"""
        if not self.samples:
            return 0.0
        ordered = sorted(self.samples)
        index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
        return ordered[index]


#: SQL 计数是否开启 —— 决定 `sql_requests` 的分母意义。
#: 在第一次记请求时惰性求值（避免 import 期就依赖 settings），
#: 之后固定不变；它只影响 `sql_requests` 的累加，不会让已记的数字变化。
sql_counting_on: bool = False


def _sample_max() -> int:
    value = settings.metrics_sample_max
    return value if value > 0 else DEFAULT_SAMPLE_MAX


_lock = threading.Lock()
_routes: dict[tuple[str, str], RouteMetrics] = defaultdict(RouteMetrics)
_started_at: float = time.monotonic()

#: 连接池等待时长（毫秒）样本。**全局一份**，因为池是进程级的，
#: 按端点拆开只会让每条曲线都稀疏到看不出东西。
_pool_wait_samples: list[float] = []
_pool_wait_seen: int = 0

#: 计数器后台 flush 失败的次数（docs/11 §3.4 M3 —— 否则「静默丢计数」不可见）。
_flush_failures: int = 0


def monitoring_enabled() -> bool:
    """是否采集进程内指标。默认关闭（任务书 B3 第 3 条：默认不注册路由）。

    注意这与「注册 `/metrics` 路由」是同一个开关 —— 两者分开会造出
    「路由开着但指标永远为空」这种半吊子状态。
    """
    return settings.metrics_enabled


def record_request(
    *,
    method: str,
    route_template: str,
    path: str,
    status: int,
    duration_ms: float,
    sql_count: int,
    slow: bool,
) -> None:
    """记一次请求。`path` 只用于诊断，聚合键是 `route_template`（防基数爆炸）。"""
    del path  # 聚合按模板；原始路径不参与，避免 /tools/a、/tools/b 各占一个 series
    global sql_counting_on
    with _lock:
        metrics = _routes[(method, route_template)]
        # 惰性求值：只有在真的开始收集时才去问 SQL 计数是否开着
        if metrics.requests == 0:
            from app.core.sql_counter import counting_enabled

            sql_counting_on = counting_enabled() or sql_counting_on
        metrics.observe(duration_ms, sql_count, status, slow)


def record_pool_wait(duration_ms: float) -> None:
    """记一次「取连接」耗时。由 `app.db.session.get_db` 调用。"""
    global _pool_wait_seen
    with _lock:
        _pool_wait_seen += 1
        _pool_wait_samples.append(duration_ms)
        if len(_pool_wait_samples) > _sample_max():
            index = random.randrange(_pool_wait_seen)
            if index < len(_pool_wait_samples):
                _pool_wait_samples[index] = duration_ms
            _pool_wait_samples.pop()


def record_flush_failure() -> None:
    global _flush_failures
    with _lock:
        _flush_failures += 1


def reset() -> None:
    """清空全部进程内指标（测试用）。"""
    global sql_counting_on, _pool_wait_seen, _flush_failures
    with _lock:
        _routes.clear()
        _pool_wait_samples.clear()
        _pool_wait_seen = 0
        _flush_failures = 0
        sql_counting_on = False


def _quantile_of(samples: list[float], q: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


def snapshot() -> dict[str, Any]:
    """当前指标的只读快照（测试断言用，也便于将来加 JSON 格式的调试端点）。"""
    with _lock:
        return {
            "uptime_seconds": time.monotonic() - _started_at,
            "routes": {
                key: {
                    "requests": m.requests,
                    "errors": m.errors,
                    "slow": m.slow,
                    "sql_total": m.sql_total,
                    "sql_requests": m.sql_requests,
                    "p50_ms": m.quantile(0.50),
                    "p95_ms": m.quantile(0.95),
                    "sample_count": len(m.samples),
                }
                for key, m in _routes.items()
            },
            "pool_wait": {
                "samples": len(_pool_wait_samples),
                "p50_ms": _quantile_of(_pool_wait_samples, 0.50),
                "p95_ms": _quantile_of(_pool_wait_samples, 0.95),
                "max_ms": max(_pool_wait_samples) if _pool_wait_samples else 0.0,
            },
            "flush_failures": _flush_failures,
        }


# ---------------------------------------------------------------------------
# 数据源相关的指标（连接池 / WAL）
# ---------------------------------------------------------------------------
def _label_escape(value: str) -> str:
    """Prometheus 文本格式的 label 值转义：反斜杠、双引号、换行。"""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def pool_metrics(engine: AsyncEngine) -> dict[str, float]:
    """连接池状态。`pool` 不存在（如 StaticPool）时返回空。"""
    pool = getattr(engine, "pool", None)
    if pool is None:
        return {}
    out: dict[str, float] = {}
    for name, getter in (
        ("pool_size", "size"),
        ("pool_checked_out", "checkedout"),
        ("pool_overflow", "overflow"),
    ):
        method = getattr(pool, getter, None)
        if callable(method):
            try:
                out[name] = float(method())
            except Exception:  # pragma: no cover - 依赖具体池实现
                continue
    return out


def wal_size_bytes(engine: AsyncEngine) -> int | None:
    """SQLite 的 WAL 文件大小。

    **只对 SQLite 有意义**，其他方言返回 `None` —— PostgreSQL 的 WAL 是
    集群级概念（`pg_wal` 目录），既不属于某个连接也不属于某个应用，
    要观测它得上 `pg_ls_waldir()` 之类的专用查询，本轮不做（报告里已声明）。

    优先用 `PRAGMA wal_checkpoint(PASSIVE)` **之前**测量的文件大小：
    这里不触发 checkpoint，只是 `stat()` 一下 `-wal` 旁路文件，
    **完全不碰数据库**。选文件而不是 `pragma wal_checkpoint` 的理由：
    后者会真的做一次检查点，那是**改变被测系统状态**的观测手段 ——
    一个指标端点不该在每次被拉取时推进 WAL。
    """
    db_path = settings.db_path
    if db_path is None:
        return None
    try:
        return (db_path.parent / (db_path.name + "-wal")).stat().st_size
    except OSError:
        # WAL 文件不存在 = 尚未写过（或已 checkpoint 回收），等价于 0
        return 0


def render_prometheus(
    engine: AsyncEngine,
    *,
    sql_enabled: bool,
    slow_threshold_ms: int,
) -> str:
    """把当前指标渲染成 Prometheus 文本 exposition 格式。"""
    lines: list[str] = []

    def emit(name: str, help_text: str, kind: str, samples: list[tuple[str, float]]) -> None:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {kind}")
        for label_suffix, value in samples:
            lines.append(f"{name}{label_suffix} {_format_value(value)}")

    with _lock:
        snap_routes = {
            key: (m.requests, m.errors, m.slow, m.sql_total, m.sql_requests, list(m.samples))
            for key, m in _routes.items()
        }
        pool_wait = list(_pool_wait_samples)
        flush_failures = _flush_failures
        uptime = time.monotonic() - _started_at

    emit(
        "localcraft_uptime_seconds",
        "进程启动至今的秒数",
        "gauge",
        [("", uptime)],
    )
    # 慢请求阈值一并暴露：否则看板上的 localcraft_slow_requests_total
    # 无法解释「多慢才算慢」，只能去翻环境变量。
    emit(
        "localcraft_slow_request_threshold_ms",
        "慢请求告警阈值（毫秒），0 表示关闭告警",
        "gauge",
        [("", float(slow_threshold_ms))],
    )

    request_samples: list[tuple[str, float]] = []
    error_samples: list[tuple[str, float]] = []
    slow_samples: list[tuple[str, float]] = []
    for (method, route), (_req, errors, slow, _sql_total, _sql_req, _samples) in sorted(
        snap_routes.items()
    ):
        labels = f'{{method="{_label_escape(method)}",route="{_label_escape(route)}"}}'
        request_samples.append((labels, float(_req)))
        error_samples.append((labels, float(errors)))
        slow_samples.append((labels, float(slow)))
    emit(
        "localcraft_requests_total",
        "按方法/路由模板统计的请求数",
        "counter",
        request_samples,
    )
    emit(
        "localcraft_request_errors_total",
        "响应 5xx 的请求数",
        "counter",
        error_samples,
    )
    emit(
        "localcraft_slow_requests_total",
        "耗时超过阈值的请求数",
        "counter",
        slow_samples,
    )

    for quantile, suffix in ((0.5, "50"), (0.95, "95")):
        samples = []
        for (method, route), (_r, _e, _s, _st, _sr, durations) in sorted(snap_routes.items()):
            labels = (
                f'{{method="{_label_escape(method)}",route="{_label_escape(route)}",'
                f'quantile="{quantile}"}}'
            )
            samples.append((labels, _quantile_of(durations, quantile)))
        emit(
            f"localcraft_request_duration_ms_p{suffix}",
            f"请求耗时的 P{suffix}（进程内蓄水池估计值，非精确分位数）",
            "gauge",
            samples,
        )

    # 每端点 SQL 条数：同时给「总条数」与「平均条数」。
    # 平均值的分母用 `sql_requests` 而不是 `requests` —— 见下面 emit 的说明。
    sql_total_samples: list[tuple[str, float]] = []
    sql_avg_samples: list[tuple[str, float]] = []
    for (method, route), (_r, _e, _s, sql_total, sql_requests, _d) in sorted(snap_routes.items()):
        labels = f'{{method="{_label_escape(method)}",route="{_label_escape(route)}"}}'
        sql_total_samples.append((labels, float(sql_total)))
        avg = (sql_total / sql_requests) if sql_requests else 0.0
        sql_avg_samples.append((labels, avg))
    if sql_enabled:
        emit(
            "localcraft_sql_statements_total",
            "按方法/路由模板统计的 SQL 语句数",
            "counter",
            sql_total_samples,
        )
        emit(
            "localcraft_sql_statements_per_request_avg",
            (
                "每请求 SQL 条数的均值。分母是**计数开启期间**的请求数"
                "（localcraft_sql_counting_enabled 为 0 时该项恒为 0，无意义）"
            ),
            "gauge",
            sql_avg_samples,
        )
    emit(
        "localcraft_sql_counting_enabled",
        "1=SQL 条数计数已启用（LOCALCRAFT_SQL_COUNT 或调试/指标开关）",
        "gauge",
        [("", 1.0 if sql_enabled else 0.0)],
    )

    # 连接池
    pool = pool_metrics(engine)
    for name in ("pool_size", "pool_checked_out", "pool_overflow"):
        if name in pool:
            emit(
                f"localcraft_db_{name}",
                f"SQLAlchemy 连接池的 {name}",
                "gauge",
                [("", pool[name])],
            )
    emit(
        "localcraft_db_pool_wait_ms_p50",
        "取连接的等待时长 P50（进程内蓄水池估计值）",
        "gauge",
        [("", _quantile_of(pool_wait, 0.50))],
    )
    emit(
        "localcraft_db_pool_wait_ms_p95",
        "取连接的等待时长 P95（进程内蓄水池估计值）",
        "gauge",
        [("", _quantile_of(pool_wait, 0.95))],
    )
    emit(
        "localcraft_db_pool_wait_ms_max",
        "取连接的最长等待时长",
        "gauge",
        [("", max(pool_wait) if pool_wait else 0.0)],
    )

    # WAL 大小（SQLite 专属）
    wal = wal_size_bytes(engine)
    if wal is not None:
        emit(
            "localcraft_sqlite_wal_bytes",
            "SQLite -wal 旁路文件的字节数（非 SQLite 方言不输出本指标）",
            "gauge",
            [("", float(wal))],
        )

    # M3：计数器 flush 失败
    emit(
        "localcraft_counter_flush_failures_total",
        "计数器后台落库失败的次数（docs/11 §3.4 M3）",
        "counter",
        [("", float(flush_failures))],
    )

    return "\n".join(lines) + "\n"


def _format_value(value: float) -> str:
    """整数一律输出成整数形式 —— `1.0` 在 Prometheus 里合法但难读。"""
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return repr(round(value, 6))


__all__ = [
    "DEFAULT_SAMPLE_MAX",
    "UNMATCHED_ROUTE",
    "RouteMetrics",
    "monitoring_enabled",
    "pool_metrics",
    "record_flush_failure",
    "record_pool_wait",
    "record_request",
    "render_prometheus",
    "reset",
    "snapshot",
    "wal_size_bytes",
]
