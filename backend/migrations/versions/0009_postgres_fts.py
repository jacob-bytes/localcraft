"""postgres full text search column

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-08 09:00:00.000000+00:00

M15（契约 §31.3）：PostgreSQL 的全文检索索引。

`tools.search_vector tsvector` + GIN 索引。**PG 专属** —— 非 PostgreSQL
直接 `return`（照 `0003` 对 SQLite 的写法，两者的方言守卫互为镜像）。

为什么是新开 `0009` 而不是改 `0003`：`0008` 已提交并推送，按既定规则历史
迁移不得再改；`0003` 同理。`down_revision = "0008"`。

**★ 该列不进 ORM 模型**（契约 §31.2①）：仓储到处用 `select(Tool)`，
写进模型会让门户列表查询每次多拉一个体积可观的 tsvector。
它只在这里存在，由 `app/search/postgres_fts.py` 用原生 SQL 读写。

**★ 必须回填既有行**：只加列的话，升级后旧工具全部搜不到（新写入的才有
向量）。回填在下面 `_backfill()` 里做。

**幂等**：`ADD COLUMN IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS`，
重复执行不报错；回填是纯重算，重跑结果相同。

**为什么回填 import 应用代码（与 `0002` 的「迁移不 import 应用代码」注释
有出入，特此说明）**：`tokenize()` 必须与运行时**完全同一份**，否则迁移
回填出来的 lexeme 与之后 `upsert()` 写出来的会不一致（同一批数据、同一个
查询，结果却取决于它是升级前还是升级后创建的）。本仓实测过纯 SQL 方案
（`regexp_replace` 给汉字加空格）**不等价**：PG 的默认 parser 把
`tools?q=` 当成一个 lexeme，而 `tokenize()` 切成 `tools`/`q`。

`0002` 那条注释约束的是**播种数据快照**（数据值不能随后续代码漂移）；
这里 import 的是一个纯函数，且迁移执行的瞬间应用版本就是当前版本，
回填结果与运行时写入必然一致 —— 这正是本迁移要保证的性质。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMN = "search_vector"
INDEX = "ix_tools_search_vector"


def _backfill(bind: Connection) -> int:
    """把既有工具全部索引一遍，返回回填条数。

    与 `PostgresFtsBackend.reindex_all()` 走**同一条路径**
    （`SearchDocument` → `index_text()` → `to_tsvector('simple', …)`），
    保证回填出来的向量与运行时写出来的一模一样。

    只回填未软删除的行（`deleted_at IS NULL`），与 `reindex_all()` 一致。
    """
    # 局部 import：让「非 PG 直接 return」的路径完全不碰应用代码
    from app.search.postgres_fts import index_text
    from app.search.base import SearchDocument

    rows = bind.execute(
        text(
            "SELECT t.id, t.name, t.summary, t.description_md, "
            "COALESCE(string_agg(tg.display_name, ' '), '') AS tag_names "
            "FROM tools t "
            "LEFT JOIN tool_tags tt ON tt.tool_id = t.id "
            "LEFT JOIN tags tg ON tg.id = tt.tag_id "
            "WHERE t.deleted_at IS NULL "
            "GROUP BY t.id"
        )
    ).all()

    update = text(f"UPDATE tools SET {COLUMN} = to_tsvector('simple', :txt) WHERE id = :tid")
    for row in rows:
        doc = SearchDocument(
            name=row[1] or "",
            summary=row[2] or "",
            description=row[3] or "",
            tags=[row[4]] if row[4] else [],
        )
        bind.execute(update, {"txt": index_text(doc), "tid": row[0]})
    return len(rows)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite：FTS5 虚表由 0003 建立，本迁移整体跳过。
        return

    op.execute(f"ALTER TABLE tools ADD COLUMN IF NOT EXISTS {COLUMN} tsvector")
    op.execute(f"CREATE INDEX IF NOT EXISTS {INDEX} ON tools USING GIN ({COLUMN})")
    count = _backfill(bind)
    print(f"[0009] 已回填 {count} 个工具的全文索引向量")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(f"DROP INDEX IF EXISTS {INDEX}")
    op.execute(f"ALTER TABLE tools DROP COLUMN IF EXISTS {COLUMN}")
