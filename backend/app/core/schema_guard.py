"""启动期「库结构 vs 代码」一致性校验（migration drift）。

## 为什么需要它

代码与库是**两个独立演进的东西**：发布包里带着新的迁移文件，而目标机上的库可能是
旧版本。`install.sh` / `upgrade.sh` 会在起服务之前跑 `alembic upgrade head`，
所以**自动路径是安全的** —— 但下面这些路径不安全：

  - 手工照 `docs/05` §5.1~5.9 逐步部署（容易漏掉迁移那一步）；
  - 本仓库的 `preview.sh`：它原先**只在库不存在时**才跑迁移，于是"改代码 + 加迁移 +
    重启预览"会带着旧 schema 起服务；
  - 只换代码不还原库的回滚。

这类问题的表现是**很难懂**：服务正常启动、`/readyz` 也通过（它只 `SELECT 1`），
然后在某个具体查询上抛 `no such column` 之类的 500 —— 排查方向完全被带偏。
（本项目的开发过程中真的踩过一次：库停在 `0009`、代码已到 `0010`。）

所以这里把它变成**启动即失败**：库的 `alembic_version` 不等于代码的 head 就拒绝启动，
并把「差在哪、怎么修」直接写进错误信息。

## 两个方向都拒绝

  - **库落后于代码**（最常见的升级漏步）：新代码会用到还不存在的列/表。
  - **库超前于代码**（只换代码没还原库的回滚）：旧代码不认识新 schema，
    而且它可能已经写坏数据。

`rollback.sh` 的正规做法是**还原升级前的备份**（那份库的版本与旧代码匹配），
所以不会被这条拦住；只有"只换代码不还库"才会被拦 —— 那正是要拦的。
"""

from __future__ import annotations

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.core.config import BASE_DIR


class SchemaDriftError(RuntimeError):
    """库的迁移版本与代码不一致 —— 拒绝在未知 schema 上启动。"""


def head_revision() -> str:
    """代码里迁移链的 head（读 `migrations/`，不连库）。

    `script_location` 显式设成**绝对路径**：`alembic.ini` 里写的是相对的
    `migrations`，而 Alembic 按**当前工作目录**解析它 —— 服务由 systemd 拉起时
    CWD 是 `WorkingDirectory=`，手工在别处启动就可能解析不到。这里不赌 CWD。
    """
    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE_DIR / "migrations"))
    return ScriptDirectory.from_config(cfg).get_current_head()


async def db_revision(conn: AsyncConnection) -> str | None:
    """库里记录的 revision；**从未迁移过的库**返回 `None`。

    `alembic_version` 表不存在时 `MigrationContext` 也会返回 `None`，
    与"表在但没行"同义 —— 两种都表示"还没跑过迁移"。
    """

    def _read(sync_conn):  # run_sync 传进来的是同步 Connection
        return MigrationContext.configure(sync_conn).get_current_revision()

    return await conn.run_sync(_read)


async def assert_db_at_head(engine: AsyncEngine) -> None:
    """库不在代码的 head 上就抛 `SchemaDriftError`（含可执行的修法）。"""
    head = head_revision()
    async with engine.connect() as conn:
        current = await db_revision(conn)

    if current == head:
        return

    if current is None:
        detail = "库里的 `alembic_version` 是空的 —— 这个库**从未跑过迁移**"
    elif current < head:
        # revision 是零填充的字符串序号（0001…0010），字典序即时间序
        detail = f"库停在 `{current}`，代码已到 `{head}`（**库落后**）"
    else:
        detail = f"库在 `{current}`，代码只到 `{head}`（**库超前**）"

    raise SchemaDriftError(
        f"数据库结构与应用代码不一致：{detail}。\n"
        "  拒绝在未知 schema 上启动 —— 否则会在某个具体查询上抛难懂的 500。\n"
        "  修法（在发布包根目录 / 本仓库 `backend/` 下执行）：\n"
        "      python -m alembic upgrade head\n"
        "  · 库落后：跑上面的命令即可（幂等）。\n"
        "  · 库超前（回滚过代码但没还原库）：请**还原升级前的备份**，不要 downgrade。\n"
        "  · 只是想让服务先起来：先跑迁移，不要跳过这一步。"
    )
