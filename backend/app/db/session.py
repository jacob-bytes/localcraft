"""异步引擎、会话工厂与 SQLite PRAGMA 事件钩子。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import ensure_runtime_dirs, settings

# 建 engine 之前先确保数据目录与 SQLite 文件目录存在。
# Alembic、CLI 与应用都经由这里建 engine，所以这一处就够了；
# 否则全新机器上 `alembic upgrade head` 会因为父目录不存在而报
# "unable to open database file" —— SQLite 的 connect 不会自动创建目录。
ensure_runtime_dirs()


def _build_engine() -> AsyncEngine:
    # 内存库由 SQLAlchemy 强制使用 StaticPool，不接受 pool_size / max_overflow。
    is_memory = ":memory:" in settings.database_url

    kwargs: dict[str, Any] = {
        "echo": settings.db_echo,
        # O1：SQLite 下关闭 pool_pre_ping。
        # pre_ping 的用途是防「连接被中间设备悄悄切断」——这是**网络数据库**的问题；
        # SQLite 是本地文件，连接不会那样失效，pre_ping 只是每次取连接白加一次往返。
        # 实测（c=20，只做「取连接 + SELECT 1」）：CPU 1.155 → 0.840 ms，
        # 吞吐 3000 → 3968 req/s。见 docs/11-优化点分析.md §1.5 / O1。
        "pool_pre_ping": not settings.is_sqlite,
        "future": True,
    }

    if settings.is_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False}

    # O2：池参数对 SQLite 与 PG **都**显式设置。
    # 此前只在非 SQLite 分支设置，导致 SQLite 部署改了 DB_POOL_SIZE / DB_MAX_OVERFLOW
    # 却完全不生效 —— 「配置项存在但不生效」比不存在更糟，它会让人以为已经调过。
    # 实测池大小对吞吐无影响（50+50 ≈ 5+10，见 docs/11 §1.5），保留可配是为了
    # 让运维能按实际连接数调整，而不是让配置项变成摆设。
    if not is_memory:
        kwargs["pool_size"] = settings.db_pool_size
        kwargs["max_overflow"] = settings.db_max_overflow

    return create_async_engine(settings.database_url, **kwargs)


engine: AsyncEngine = _build_engine()


# ---------------------------------------------------------------------------
# PRAGMA —— 契约 §11 硬约束第 3 条
# ---------------------------------------------------------------------------
# SQLite 的 foreign_keys **默认是 OFF**，不设的话外键形同虚设：
# 本地测试全过，切到 PostgreSQL 后到处报错（docs/02 §1.1 的「踩坑提醒」）。
# 必须在**每个连接**上设置，所以挂在 connect 事件上而不是执行一次。
#
# 只在方言为 sqlite 时执行 —— 切 PG 后这些 PRAGMA 要么不存在要么语义不同。
@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection: Any, connection_record: Any) -> None:
    if engine.dialect.name != "sqlite":
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={int(settings.db_busy_timeout)}")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：每请求一个会话。

    事务边界在 **service 层**（docs/03 §6.3），这里只负责给出会话与收尾。
    """
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
