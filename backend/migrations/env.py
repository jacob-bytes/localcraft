"""Alembic 环境。

要点（docs/02 §6.3）：
  - 迁移在**服务启动之前**执行，不允许应用启动时自动迁移
  - 数据库 URL 从环境变量读取（`DATABASE_URL`），不写进 alembic.ini
  - SQLite 下开启 `render_as_batch`，否则 ALTER 类操作会失败
  - 迁移脚本必须支持在事务中执行，失败则整体回滚
"""

from __future__ import annotations

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# 让 `python -m alembic` 与 `alembic` 都能找到 app 包
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import ensure_runtime_dirs, settings  # noqa: E402
from app.models import Base  # noqa: E402

# SQLite 的 connect 不会自动创建父目录，而迁移是**服务启动之前**的第一步，
# 所以这里必须先把 DATA_DIR 与数据库文件所在目录建好，
# 否则全新机器上会以 "unable to open database file" 失败。
ensure_runtime_dirs()

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    # disable_existing_loggers=False 是必须的。
    # `logging.config.fileConfig` 的默认值是 True —— 它会把**所有已存在的 logger
    # 的 disabled 置为 True**，只有 alembic.ini 里列出的那三个例外。
    # 影响：
    #   - 在进程内调用 alembic（测试夹具、将来的升级 API、被嵌入的工具）之后，
    #     应用自己的日志会彻底静默，且现象是「日志凭空消失」，极难定位；
    #   - pytest 的 caplog 也会因此失效。
    # CLI 方式跑 alembic 时进程本就短命，看不出问题，
    # 所以这个坑只会在进程内复用时才炸 —— 更要在这里一次性关掉。
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _configure(connection: Connection | None = None, **kwargs: object) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # SQLite 不支持大部分 ALTER，用 batch 模式重建成临时表再改名
        render_as_batch=settings.is_sqlite,
        compare_type=True,
        compare_server_default=True,
        **kwargs,
    )


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连库。"""
    _configure(
        url=settings.database_url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
