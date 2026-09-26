#!/usr/bin/env python3
"""B4 / O4：sync vs async 数据访问栈的并发开销对比实验。

对应 `docs/11-优化点分析.md` §2.2 **O4**。
**本轮只做实验与测量，不改 `backend/app/` 的生产代码路径**（任务书 B4 第 1 条）。

要回答的问题
------------

`docs/11` §1.4 实测：`/api/v1/tools?page_size=24` 的 **CPU 时间随并发放大**
（c=1 时 4.87 ms/请求 → c=20 时 56.28 ms/请求，约 10~11.6 倍），
而 §2.1 的 O1~O3 只降低了常数项，**没有触及放大本身**。

假设（§2.2 O4）：放大来自 `aiosqlite` + greenlet 桥接 + 线程池在 GIL 下的
上下文切换开销。

本脚本用**同一组真实 SQL**（从生产栈上抓下来的 trace）跑两套数据访问栈：

    A. async：`create_async_engine` + `sqlite+aiosqlite`（生产同款）
    B. sync ：`create_engine` + `sqlite`（stdlib sqlite3）+ `run_in_threadpool`

两套的差别**只有数据访问栈**：同一个 ASGI 框架、同一个 uvicorn、
同一个 worker 数、同一份数据、同一批语句、同一个连接池大小、同一个压测客户端。

为什么要做成「重放真实 SQL」而不是临时写几条查询
------------------------------------------------

临时写的查询很容易在无意间变成「测我自己写的那条 SQL」。
本脚本先用 `--capture-trace` 从**生产 app** 上抓一次
`GET /api/v1/tools?page_size=24` 实际发出的语句与参数（19 条），
再让两套栈逐字重放 —— 这样对比的就是生产真实负载，
而不是一个为了让 benchmark 好看而简化过的负载。

怎么排除压测工具本身的影响（`docs/11` §1.6 的教训）
---------------------------------------------------

§1.6 记着：同一端点、同为 c=20，`ab -k` 3592 req/s，而 Python `httpx`
异步客户端只有 332 req/s —— **差 10.8 倍**，Python 客户端自己先饱和了。

因此本实验：

1. 用 **C 写的多线程客户端**（`scripts/bench-loadgen.c`），一个独立进程，
   没有 GIL、没有事件循环、没有 Python 对象分配；
2. **同时测量客户端自己的 CPU**，并在报告里给出 —— 如果客户端 CPU
   接近主机核数，该组数据即不可信，脚本会显式标 `CLIENT-BOUND`；
3. 客户端与服务端是**不同进程**，`process_time()` 不混在一起；
4. 每组配置前**预热**，避免把首次建连/首次编译语句算进稳态。

必须报 **CPU/请求**（`docs/11` §2.4 O10 的教训）
------------------------------------------------

只报延迟与吞吐无法区分「饱和」与「放大」：饱和时 CPU/请求恒定、
吞吐平台化；放大时 CPU/请求随并发上升。所以每行都给出
`CPU ms/请求`，它是本实验最关键的列。

先跑 `--prepare`（或直接跑，脚本会自动准备）
--------------------------------------------

    python3 scripts/bench-sync-vs-async.py --prepare     # 造库（~200 工具）
    python3 scripts/bench-sync-vs-async.py               # 跑实验并打印表格
    python3 scripts/bench-sync-vs-async.py --no-io       # 附加：非 keep-alive 对照

原始 JSON 落在 `--out`（默认 `backend/var/bench-b4/`）。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"
DEFAULT_OUT = BACKEND / "var" / "bench-b4"
DEFAULT_DB = DEFAULT_OUT / "bench.db"
TRACE_NAME = "portal-list-trace.json"

#: 默认并发梯度。与 docs/11 §1.4 的梯度对齐（1/5/10/20/50），
#: 这样本实验的结论可以直接与那组数字对照。
DEFAULT_LEVELS = (1, 5, 10, 20, 50)
#: 每线程请求数。选够大让 CPU 测量的相对误差足够小：
#: macOS 的 CPU 采样精度是 10 ms，c=1 时 200 请求 × ~5 ms ≈ 1 s，
#: 10 ms / 1000 ms = 1% 量级；c=50 时同一请求量只要 ~0.3 s，
#: 所以按并发放大请求数（见 `requests_for`）。
BASE_REQUESTS_PER_THREAD = 200
#: 单次运行的墙钟上限（秒）。超过就停止加请求数，避免实验失控。
MAX_RUN_SECONDS = 30.0

#: 同步栈用的线程数上限。anyio 默认是 40；c=50 时若不提高，
#: 测到的就是「anyio 线程池排队」而不是「同步栈的开销」。
SYNC_THREAD_LIMIT = 96


# ---------------------------------------------------------------------------
# 环境自述
# ---------------------------------------------------------------------------
def environment_facts() -> dict[str, object]:
    """把「这份数字是在什么机器上得到的」记全 —— 报告里要写。"""
    facts: dict[str, object] = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count_logical": os.cpu_count(),
        "python": sys.version.split()[0],
        "python_implementation": platform.python_implementation(),
        "sqlite3_lib_version": sqlite3.sqlite_version,
    }
    # macOS：拿具体型号与物理核数，比 platform.processor() 的 "arm" 有用
    try:
        facts["cpu_brand"] = (
            subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
        facts["cpu_physical"] = int(
            subprocess.run(
                ["sysctl", "-n", "hw.physicalcpu"], capture_output=True, text=True, check=True
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError, ValueError):
        pass
    try:
        facts["cpu_model_linux"] = next(
            line.split(":", 1)[1].strip()
            for line in Path("/proc/cpuinfo").read_text().splitlines()
            if line.startswith("model name")
        )
    except (OSError, StopIteration):
        pass
    try:
        import sqlalchemy

        facts["sqlalchemy"] = sqlalchemy.__version__
    except Exception:  # pragma: no cover
        pass
    try:
        import aiosqlite

        facts["aiosqlite"] = getattr(aiosqlite, "__version__", "unknown")
    except Exception:  # pragma: no cover
        pass
    return facts


# ---------------------------------------------------------------------------
# 准备数据库与 trace
# ---------------------------------------------------------------------------
def build_database(db_path: Path, tool_count: int) -> None:
    """建一个与生产 schema 相同的库，并灌 `tool_count` 个已发布工具。

    为什么不用生产库：本实验要可重复、不依赖某个人的开发库状态。
    为什么不用 httpx 逐个上传造数据：那是 200 次 HTTP 上传，
    造数据的时间比实验本身还长，而且注入的是写路径的负载。
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    for suffix in ("-wal", "-shm"):
        side = db_path.with_name(db_path.name + suffix)
        if side.exists():
            side.unlink()

    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    env["DATA_DIR"] = str(db_path.parent / "data")
    env["SECRET_KEY"] = "bench-b4-secret-key-not-for-production"
    env["LOG_LEVEL"] = "WARNING"
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        env=env,
        check=True,
        capture_output=True,
    )

    # 直接用 sqlite3 灌数据（避免走 ORM，造数据不该成为被测量的对象）
    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        now = "2026-01-01 00:00:00"
        con.execute(
            "INSERT OR IGNORE INTO categories (slug,name,sort_order,is_active,created_at,"
            "updated_at) VALUES (?,?,?,?,?,?)",
            ("bench-cat", "压测分类", 10, 1, now, now),
        )
        cat_id = con.execute("SELECT id FROM categories WHERE slug='bench-cat'").fetchone()[0]
        # 迁移只 seed 角色与设置，**不 seed 用户** —— 而工具需要 owner_id。
        # 这里建一个属主用户，密码哈希走真实实现（argon2id），
        # 免得将来有人往这个库里加需要登录的负载时踩到假哈希。
        if con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            import sys as _sys

            _sys.path.insert(0, str(BACKEND))
            from app.core.security import hash_password

            con.execute(
                "INSERT INTO users (username,display_name,email,password_hash,"
                "must_change_password,status,auth_source,failed_login_count,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    "bench-owner",
                    "压测属主",
                    "bench@example.invalid",
                    hash_password("Bench@12345"),
                    0,
                    "active",
                    "local",
                    0,
                    now,
                    now,
                ),
            )
            con.commit()
        owner_id = con.execute(
            "SELECT id FROM users WHERE username='bench-owner'"
        ).fetchone()[0]
        for i in range(tool_count):
            con.execute(
                "INSERT OR IGNORE INTO tools (slug,name,summary,description_md,tool_type,"
                "visibility,status,owner_id,category_id,download_count,view_count,version_seq,"
                "published_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"bench-tool-{i:05d}",
                    f"压测工具 {i}",
                    "压测简介 " + "x" * 60,
                    "# 正文\n" + "y" * 300,
                    "file" if i % 2 == 0 else "prompt",
                    "public",
                    "approved",
                    owner_id,
                    cat_id,
                    i % 500,
                    i % 2000,
                    1,
                    now,
                    now,
                    now,
                ),
            )
        # FTS 索引（表名与列名以迁移 0003 为准：`tool_search_index`，
        # 四列 name/summary/description/tags）。
        # 列表端点本身不查 FTS，但保持一致可以让这个库直接用于别的负载。
        con.execute(
            "INSERT INTO tool_search_index(rowid, name, summary, description, tags) "
            "SELECT id, name, summary, description_md, '' FROM tools"
        )
        con.commit()
        total = con.execute(
            "SELECT COUNT(*) FROM tools WHERE status='approved' AND deleted_at IS NULL"
        ).fetchone()[0]
    finally:
        con.close()
    print(f"[prepare] 库就绪：{db_path}（已发布工具 {total} 个）")
    # 立刻 checkpoint 一次，避免 WAL 里堆着造数据的事务影响第一组测量
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()


def capture_trace(db_path: Path, trace_path: Path) -> dict:
    """从**生产 app** 上抓一次门户列表的 SQL trace。

    这一步必须跑真实的 `app.main` —— 本实验要重放的是**生产负载**，
    而不是一条我为了让 benchmark 好看而手写的查询。
    """
    script = f'''
import asyncio, json, os, sys
from pathlib import Path
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///{db_path}"
os.environ["DATA_DIR"] = "{db_path.parent / 'data'}"
os.environ["SECRET_KEY"] = "bench-b4-secret-key-not-for-production"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["LOCALCRAFT_SQL_COUNT"] = "1"
os.environ["LOCALCRAFT_DISABLE_FACET_CACHE"] = "1"
sys.path.insert(0, "{BACKEND}")
import httpx
from sqlalchemy import event
from app.main import app
from app.db.session import engine

trace = []
capturing = False

def hook(conn, cursor, statement, parameters, context, executemany):
    if capturing:
        trace.append({{"sql": statement, "params": parameters, "many": bool(executemany)}})

event.listen(engine.sync_engine, "before_cursor_execute", hook)

async def main():
    global capturing
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://bench") as c:
        r = await c.get("/api/v1/tools?page_size=24")   # 预热
        assert r.status_code == 200, r.text
        trace.clear()
        capturing = True
        r = await c.get("/api/v1/tools?page_size=24")
        capturing = False
        assert r.status_code == 200, r.text
    Path(r"{trace_path}").write_text(json.dumps(trace, ensure_ascii=False))
    print(f"[capture] {{len(trace)}} 条语句 → {trace_path}")

asyncio.run(main())
'''
    subprocess.run([sys.executable, "-c", script], check=True, cwd=BACKEND)
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    # 参数在 JSON 里可能是 list（SQLAlchemy 传的是 tuple，json 转成 list）
    for item in trace:
        if isinstance(item.get("params"), list):
            item["params"] = tuple(item["params"])
    print(f"[capture] trace 载入 {len(trace)} 条语句")
    return {"trace": trace}


def load_trace(trace_path: Path) -> list[dict]:
    raw = json.loads(trace_path.read_text(encoding="utf-8"))
    trace = raw["trace"] if isinstance(raw, dict) else raw
    for item in trace:
        if isinstance(item.get("params"), list):
            item["params"] = tuple(item["params"])
    return trace


# ---------------------------------------------------------------------------
# 被测量的服务端（子进程）
# ---------------------------------------------------------------------------
SERVER_PROGRAM = r'''
"""B4 的服务端：只做「重放同一批 SQL」，不掺别的东西。"""
from __future__ import annotations

import argparse, json, re, sys
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


MODE = None
TRACE = None
ENGINE = None
SESSION_FACTORY = None
EXECUTED = 0


def _pragma(dbapi_conn, _rec):
    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA synchronous=NORMAL")
    finally:
        cur.close()


def build_sync(db_path: str):
    """B：同步引擎。NullPool —— 一个请求一个连接，用完就关。

    为什么用 NullPool 而不是 QueuePool：sqlite3 的连接**默认只能在创建它的
    线程里使用**，而 `run_in_threadpool` 会把请求派到不同的池化线程上。
    QueuePool 会因此抛 `SQLite objects created in a thread can only be used
    in that same thread`。NullPool 让连接与线程一一对应，语义与 aiosqlite
    （每个 Request 建连接、跑完关掉）**最接近**，因而是最干净的对照。
    """
    eng = create_engine(db_path, poolclass=NullPool, future=True)
    event.listen(eng, "connect", _pragma)
    return eng


def build_async(db_path: str):
    """A：异步引擎（生产同款）：sqlite+aiosqlite + 显式连接池。"""
    eng = create_async_engine(
        db_path,
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=False,   # 与生产一致（O1：SQLite 下关闭）
        future=True,
    )
    event.listen(eng.sync_engine, "connect", _pragma)
    return eng


#: 变体 C 用的「同步 DBAPI + greenlet_spawn」引擎。
#:
#: 这是为了把假设拆开：O4 的假设是「aiosqlite + greenlet + 线程池」共同造成放大。
#: 变体 C 保留 greenlet 桥接与前面的 `AsyncSession`，但把 **aiosqlite 换掉** ——
#: 每个连接就是一个普通的 `sqlite3.Connection`，由 SQLAlchemy 通过
#: `greenlet_spawn` 在**调用者所在线程**上直接执行（没有 aiosqlite 的专用工作线程、
#: 没有跨线程 `call_soon_threadsafe`）。
#:
#: 三者一对比就能回答：放大来自 **aiosqlite 的线程**，还是来自
#: **async/await + greenlet 这一层本身**，还是来自**同步 DB 调用被搬到别处执行**。
def build_async_thread(db_path: str):
    """变体 C 用的引擎：**同步** SQLite DBAPI（stdlib sqlite3）+ QueuePool。

    注意这里返回的是一个普通同步 `Engine` —— SQLAlchemy 不允许给
    `create_async_engine` 传同步驱动（会抛
    `InvalidRequestError: The asyncio extension requires an async driver`）。
    变体 C 因此不使用 `AsyncSession`，而是自己用
    `sqlalchemy.util.greenlet_spawn` 把同步执行桥接成可 await 的协程 ——
    这正是「async 栈里除 aiosqlite 之外的那一层」。
    """
    eng = create_engine(
        db_path,
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=False,
        future=True,
    )
    event.listen(eng, "connect", _pragma)
    return eng


def _to_named(sql: str, params):
    """把 trace 里的 **qmark 风格** SQL 与位置参数转成 `text()` 能用的命名参数。

    为什么必须做这一步：SQLAlchemy 2.0 的 `text()` **不支持位置参数**
    （实测直接抛 `ArgumentError: List argument must consist only of dictionaries`），
    而 trace 里记录的是 DBAPI 最终发出的 `... = ?` 语句 + tuple。`qmark`
    paramstyle 的 `?` 不可能表示「SQLite 的 `?` 运算符」，
    所以按出现顺序替换成 `:p0, :p1, ...` 是安全的。

    这样两套栈重放的语句与参数值**完全一致**，差异只剩数据访问栈本身。
    """
    if params is None:
        return sql, {}
    values = tuple(params) if isinstance(params, (list, tuple)) else (params,)
    out: list[str] = []
    index = 0
    for char in sql:
        if char == "?":
            out.append(f":p{index}")
            index += 1
        else:
            out.append(char)
    if index != len(values):
        raise ValueError(
            f"占位符数量({index})与参数个数({len(values)})不一致：{sql[:120]!r}"
        )
    return "".join(out), {f"p{i}": v for i, v in enumerate(values)}


def _statements(trace):
    """把 trace 展开成 (sql, params) 列表，两套栈拿到的完全一致。"""
    out = []
    for item in trace:
        params = item.get("params")
        if isinstance(params, list):
            params = tuple(params)
        if item.get("many") and params and isinstance(params[0], (list, tuple)):
            for one in params:
                out.append(_to_named(item["sql"], tuple(one)))
        else:
            out.append(_to_named(item["sql"], params))
    return out


async def replay_async():
    global EXECUTED
    assert SESSION_FACTORY is not None
    async with SESSION_FACTORY() as session:
        for sql, params in TRACE:
            await session.execute(text(sql), params or {})
            EXECUTED += 1


def _replay_greenlet_sync():
    """在 greenlet 桥接下同步执行整批语句。"""
    global EXECUTED
    with ENGINE.connect() as conn:
        for sql, params in TRACE:
            conn.execute(text(sql), params or {})
            EXECUTED += 1


async def replay_greenlet():
    from sqlalchemy.util import greenlet_spawn

    await greenlet_spawn(_replay_greenlet_sync)


def replay_sync():
    """同步重放。由 run_in_threadpool 调用。"""
    global EXECUTED
    assert ENGINE is not None
    with ENGINE.connect() as conn:
        for sql, params in TRACE:
            conn.execute(text(sql), params or {})
            EXECUTED += 1


def main() -> None:
    global MODE, TRACE, ENGINE, SESSION_FACTORY
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=("async", "sync", "async-thread"))
    ap.add_argument("--db", required=True)
    ap.add_argument("--trace", required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--threads", type=int, default=96)
    args = ap.parse_args()

    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse
    from starlette.concurrency import run_in_threadpool

    import uvicorn

    MODE = args.mode
    raw = json.loads(Path(args.trace).read_text(encoding="utf-8"))
    TRACE = _statements(raw["trace"] if isinstance(raw, dict) else raw)

    app = FastAPI()
    db_url = f"sqlite:///{args.db}"

    if MODE == "async":
        ENGINE = build_async(f"sqlite+aiosqlite:///{args.db}")
        SESSION_FACTORY = async_sessionmaker(ENGINE, expire_on_commit=False)

        @app.get("/tools")
        async def tools_async():
            await replay_async()
            return PlainTextResponse("ok")

    elif MODE == "async-thread":
        ENGINE = build_async_thread(f"sqlite:///{args.db}")

        @app.get("/tools")
        async def tools_greenlet():
            # 变体 C：与变体 A **同一套 async/await + greenlet 桥接**，
            # 唯一区别是底层 DBAPI 从 aiosqlite 换成 stdlib sqlite3，
            # 因此查询在**调用者所在的事件循环线程**上同步执行。
            # 若放大主要来自 aiosqlite 的专用工作线程，这一变体应当明显更好；
            # 若它与 A 一样差，则问题在 async/greenlet 这一层（或根本不在 DB 层）。
            await replay_greenlet()
            return PlainTextResponse("ok")

    else:
        ENGINE = build_sync(db_url)
        # 提高 anyio 的线程上限，否则 c=50 时测到的是线程池排队
        try:
            import anyio

            anyio.to_thread.current_default_thread_limiter().total_tokens = args.threads
        except Exception as exc:  # pragma: no cover
            print(f"warn: cannot raise thread limit: {exc}", file=sys.stderr, flush=True)

        @app.get("/tools")
        async def tools_sync():
            await run_in_threadpool(replay_sync)
            return PlainTextResponse("ok")

    @app.get("/healthz")
    async def healthz():
        """控制组：完全不碰数据库。"""
        return PlainTextResponse("ok")

    # 不让 app 被外部配置影响：这个进程只做测量
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="error", access_log=False)


if __name__ == "__main__":
    main()
'''


def wait_port(port: int, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError as exc:
            last_error = exc
            time.sleep(0.1)
    raise RuntimeError(f"端口 {port} 在 {timeout}s 内没有就绪：{last_error}")


#: 端口分配从固定基址递增。
#: **不能**用「bind(0) 拿一个空闲端口再关掉」那种做法：
#: 从关闭到 uvicorn 真正 bind 之间存在竞态窗口，另一个进程（或本脚本的
#: 上一次运行）可能刚好占掉它 —— 而这种失败是间歇性的，最难排查。
_PORT_BASE = 18100
_PORT_NEXT = _PORT_BASE


def next_port() -> int:
    """顺序取一个端口，并确认当下可以 bind。"""
    global _PORT_NEXT
    for _ in range(200):
        port = _PORT_NEXT
        _PORT_NEXT += 1
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise RuntimeError("找不到可用端口")


class ServerProcess:
    """被测量的服务端子进程。

    刻意让它 **fork 出一个子进程再跑 uvicorn**：
    uvicorn 在某些配置下会重启/派生，把 PID 认错会让 CPU 读数对不上。
    这里用 `subprocess.Popen` 直接跑 `python -c`，读取 `psutil` 无关的
    `bench-cpu-monitor`（C 实现）取 CPU 时间。
    """

    def __init__(self, monitor: Path, program_path: Path) -> None:
        self.monitor = monitor
        self.program_path = program_path

    def start(
        self, mode: str, db: Path, trace: Path, port: int, threads: int
    ) -> subprocess.Popen:
        self._log = tempfile.NamedTemporaryFile(
            prefix=f"b4-{mode}-", suffix=".log", delete=False
        )
        proc = subprocess.Popen(
            [
                sys.executable,
                str(self.program_path),
                "--mode",
                mode,
                "--db",
                str(db),
                "--trace",
                str(trace),
                "--port",
                str(port),
                "--threads",
                str(threads),
            ],
            stdout=self._log,
            stderr=subprocess.STDOUT,
            cwd=BACKEND,
        )
        try:
            wait_port(port)
        except RuntimeError:
            proc.kill()
            self._log.flush()
            raise RuntimeError(
                f"{mode} 服务端起不来，日志：\n"
                + Path(self._log.name).read_text(encoding="utf-8", errors="replace")
            ) from None
        return proc

    def cpu_seconds(self, pid: int) -> float:
        out = subprocess.run(
            [str(self.monitor), str(pid)], capture_output=True, text=True, check=True
        ).stdout
        return float(json.loads(out)["cpu_s"])


def loadgen_binary() -> Path:
    """编译/定位 C 压测客户端。"""
    src = REPO_ROOT / "scripts" / "bench-loadgen.c"
    out = DEFAULT_OUT / "bench-loadgen"
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists() or out.stat().st_mtime < src.stat().st_mtime:
        subprocess.run(["cc", "-O2", "-pthread", "-o", str(out), str(src)], check=True)
    return out


def monitor_binary() -> Path:
    src = REPO_ROOT / "scripts" / "bench-cpu-monitor.c"
    out = DEFAULT_OUT / "bench-cpu-monitor"
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists() or out.stat().st_mtime < src.stat().st_mtime:
        subprocess.run(["cc", "-O2", "-o", str(out), str(src)], check=True)
    return out


def requests_for(level: int) -> int:
    """按并发放大每线程请求数，保证墙钟足够长、CPU 读数够准。"""
    per_thread = max(BASE_REQUESTS_PER_THREAD // max(level, 1), 40)
    return per_thread


def _one_pass(
    *,
    loadgen: Path,
    server: ServerProcess,
    proc: subprocess.Popen,
    port: int,
    level: int,
    path: str,
    keepalive: bool,
    per_thread: int,
) -> dict:
    cpu0 = server.cpu_seconds(proc.pid)
    started = time.monotonic()
    result = subprocess.run(
        [
            str(loadgen),
            "127.0.0.1",
            str(port),
            path,
            str(level),
            str(per_thread),
            "1" if keepalive else "0",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=MAX_RUN_SECONDS * 10,
    )
    wall = time.monotonic() - started
    cpu1 = server.cpu_seconds(proc.pid)
    if not result.stdout.strip():
        raise RuntimeError(f"loadgen 失败（rc={result.returncode}）：{result.stderr.strip()}")
    stats = json.loads(result.stdout.strip().splitlines()[-1])
    completed = max(int(stats["completed"]), 1)
    server_cpu = cpu1 - cpu0
    return {
        "completed": int(stats["completed"]),
        "errors": int(stats["errors"]),
        "elapsed_s": float(stats["elapsed_s"]),
        "server_cpu_s": server_cpu,
        "server_cpu_ms_per_req": server_cpu * 1000.0 / completed,
        "rps": float(stats["rps"]),
        "client_cpu_s": float(stats["client_cpu_s"]),
        "client_cpu_cores": float(stats["cpu_cores"]),
        "wall_ms_per_req": float(stats["elapsed_s"]) * 1000.0 / completed,
        "outer_wall_ms_per_req": wall * 1000.0 / completed,
    }


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _spread(values: list[float]) -> float:
    """相对离散度 (max-min)/median —— 用来判断这组数**能不能下结论**。

    单次测量之间如果有 30% 的抖动，那么「A 比 B 快 20%」这种结论就是噪声。
    把离散度直接打出来，比事后再解释「差异可能不显著」要诚实得多。
    """
    if not values:
        return 0.0
    center = _median(values)
    if center == 0:
        return 0.0
    return (max(values) - min(values)) / center


def run_once(
    *,
    loadgen: Path,
    server: ServerProcess,
    mode: str,
    db: Path,
    trace: Path,
    port: int,
    level: int,
    path: str,
    keepalive: bool,
    threads: int,
    samples: int = 1,
) -> dict:
    """启动一次服务端，做 `samples` 次**独立测量**，返回中位数与离散度。

    为什么要多次测量：c=1 时一次测量的服务端 CPU 总量只有几十毫秒，
    进程 CPU 读数与调度抖动带来的相对误差可达 ±30%。
    单次跑出来的「A 比 B 快 20%」很可能只是抖动。
    这里把**每一次**的样本都留在 JSON 里，报告中位数与离散度，
    让读者自己判断差异是否超过噪声。
    """
    proc = server.start(mode, db, trace, port, threads)
    try:
        # 预热（不计入）：连接池、语句编译、page cache、CPU 频率爬升
        subprocess.run(
            [
                str(loadgen),
                "127.0.0.1",
                str(port),
                path,
                str(max(level, 1)),
                "20",
                "1" if keepalive else "0",
            ],
            capture_output=True,
            check=False,
        )
        per_thread = requests_for(level)
        passes = [
            _one_pass(
                loadgen=loadgen,
                server=server,
                proc=proc,
                port=port,
                level=level,
                path=path,
                keepalive=keepalive,
                per_thread=per_thread,
            )
            for _ in range(max(samples, 1))
        ]
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)

    cpu_values = [p["server_cpu_ms_per_req"] for p in passes]
    wall_values = [p["wall_ms_per_req"] for p in passes]
    rps_values = [p["rps"] for p in passes]
    client_values = [p["client_cpu_cores"] for p in passes]

    row = {
        "mode": mode,
        "path": path,
        "concurrency": level,
        "keepalive": keepalive,
        "samples": len(passes),
        "completed": sum(p["completed"] for p in passes),
        "errors": sum(p["errors"] for p in passes),
        "server_cpu_ms_per_req": _median(cpu_values),
        "cpu_spread": _spread(cpu_values),
        "wall_ms_per_req": _median(wall_values),
        "rps": _median(rps_values),
        "client_cpu_cores": _median(client_values),
        "requests_per_thread": per_thread,
        "passes": passes,
    }
    # 客户端是不是自己先饱和了？§1.6 的教训必须显式检查。
    row["client_bound"] = row["client_cpu_cores"] > (os.cpu_count() or 1) * 0.8
    return row


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
def print_table(rows: list[dict], title: str) -> None:
    print(f"\n=== {title} ===")
    header = (
        f"{'并发':>4} {'栈':<6} {'样本':>4} {'完成':>7} {'错误':>5} "
        f"{'墙钟ms/req':>10} {'CPUms/req':>10} {'CPU离散':>8} {'req/s':>9} "
        f"{'客户端cores':>11} {'客户端受限':>10}"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{row['concurrency']:>4} {row['mode']:<6} {row['samples']:>4} "
            f"{row['completed']:>7} {row['errors']:>5} "
            f"{row['wall_ms_per_req']:>10.3f} {row['server_cpu_ms_per_req']:>10.3f} "
            f"{row['cpu_spread']:>7.1%} {row['rps']:>9.1f} {row['client_cpu_cores']:>11.3f} "
            f"{('YES' if row['client_bound'] else 'no'):>10}"
        )


def amplification(rows: list[dict], mode: str, path: str) -> float | None:
    """CPU/请求 从 c=1 到 c=50 的放大倍数。"""
    series = {
        r["concurrency"]: r["server_cpu_ms_per_req"]
        for r in rows
        if r["mode"] == mode and r["path"] == path
    }
    if 1 in series and 50 in series and series[1] > 0:
        return series[50] / series[1]
    return None


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prepare", action="store_true", help="只造库与 trace，然后退出")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--tools", type=int, default=200, help="造多少个已发布工具")
    ap.add_argument("--levels", type=int, nargs="+", default=list(DEFAULT_LEVELS))
    ap.add_argument("--threads", type=int, default=SYNC_THREAD_LIMIT, help="同步栈的线程上限")
    ap.add_argument(
        "--samples",
        type=int,
        default=3,
        help="每种配置独立测量几次（取中位数；离散度会一起报出来）",
    )
    ap.add_argument("--no-io", action="store_true", help="附加：非 keep-alive 模式对照")
    ap.add_argument("--skip-healthz", action="store_true", help="跳过 /healthz 控制组")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    trace_path = args.out / TRACE_NAME
    facts = environment_facts()
    print("=== 实验环境 ===")
    for key, value in facts.items():
        print(f"  {key}: {value}")

    if not args.db.exists() or args.prepare:
        build_database(args.db, args.tools)
    if args.prepare or not trace_path.exists():
        capture_trace(args.db, trace_path)
    if args.prepare:
        print("\n[prepare] 完成。现在可以直接跑：python3 scripts/bench-sync-vs-async.py")
        return 0

    trace = load_trace(trace_path)
    print(f"\n[trace] {len(trace)} 条语句（逐条重放，两套栈完全一致）")

    loadgen = loadgen_binary()
    monitor = monitor_binary()
    program_path = args.out / "_b4_server.py"
    program_path.write_text(SERVER_PROGRAM, encoding="utf-8")
    server = ServerProcess(monitor, program_path)

    rows: list[dict] = []
    keepalive = True
    try:
        for level in args.levels:
            for mode in ("async", "async-thread", "sync"):
                row = run_once(
                    loadgen=loadgen,
                    server=server,
                    mode=mode,
                    db=args.db,
                    trace=trace_path,
                    port=next_port(),
                    level=level,
                    path="/tools",
                    keepalive=keepalive,
                    threads=args.threads,
                    samples=args.samples,
                )
                rows.append(row)
                print(
                    f"  c={level:<3} {mode:<6} cpu/req={row['server_cpu_ms_per_req']:8.3f} ms  "
                    f"wall/req={row['wall_ms_per_req']:8.3f} ms  rps={row['rps']:8.1f}  "
                    f"client={row['client_cpu_cores']:.2f} cores",
                    flush=True,
                )

        if not args.skip_healthz:
            for level in (1, 20, 50):
                for mode in ("async", "sync"):
                    row = run_once(
                        loadgen=loadgen,
                        server=server,
                        mode=mode,
                        db=args.db,
                        trace=trace_path,
                        port=next_port(),
                        level=level,
                        path="/healthz",
                        keepalive=keepalive,
                        threads=args.threads,
                        samples=args.samples,
                    )
                    rows.append(row)

        if args.no_io:
            for level in (1, 20, 50):
                for mode in ("async", "sync"):
                    row = run_once(
                        loadgen=loadgen,
                        server=server,
                        mode=mode,
                        db=args.db,
                        trace=trace_path,
                        port=next_port(),
                        level=level,
                        path="/tools",
                        keepalive=False,
                        threads=args.threads,
                    )
                    rows.append(row)
    finally:
        shutil.rmtree(args.out / "data", ignore_errors=True)

    print_table([r for r in rows if r["path"] == "/tools"], "/tools（重放 19 条真实 SQL）")
    healthz_rows = [r for r in rows if r["path"] == "/healthz"]
    if healthz_rows:
        print_table(healthz_rows, "/healthz（控制组：不碰数据库）")
    if args.no_io:
        print_table(
            [r for r in rows if r["path"] == "/tools" and not r["keepalive"]],
            "/tools（非 keep-alive：每请求新建连接）",
        )

    summary = {
        "environment": facts,
        "config": {
            "levels": args.levels,
            "sync_thread_limit": args.threads,
            "keepalive": keepalive,
            "base_requests_per_thread": BASE_REQUESTS_PER_THREAD,
            "samples_per_config": args.samples,
            "async_pool": "aiosqlite + pool_size=5, max_overflow=5, pre_ping=False",
            "sync_pool": "sqlite3 + NullPool + run_in_threadpool",
            "trace_statements": len(trace),
        },
        "rows": rows,
        "amplification_c1_to_c50": {
            mode: amplification(rows, mode, "/tools")
            for mode in ("async", "async-thread", "sync")
        },
        "hypothesis": (
            "aiosqlite + greenlet 桥接 + 线程池在 GIL 下的上下文切换开销 "
            "导致 CPU/请求随并发放大"
        ),
    }
    result_path = args.out / "b4-result.json"
    result_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n原始数据：{result_path}")
    print(f"trace：{trace_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
