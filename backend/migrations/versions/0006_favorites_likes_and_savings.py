"""M8: tool_favorites / tool_likes + tools 的 3 个新列

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-26 12:00:00.000000+00:00

对应 contracts/CONTRACT.md **§23.3「新增数据模型（3 项）」**，逐条照做：

① `tool_favorites`（新表）—— `UNIQUE(user_id, tool_id)`、
   `INDEX(user_id, created_at DESC)`、`INDEX(tool_id)`，两个外键都
   `ON DELETE CASCADE`。
② `tool_likes`（新表）—— 与 `tool_favorites` **结构完全一致**，仅表名不同。
③ `tools` 新增 `favorite_count` / `like_count`（Integer, NOT NULL, 默认 0）与
   `estimated_saving_minutes`（Integer, 可空）。

## 为什么计数是反规范化列

列表接口否则要 JOIN + GROUP BY，而 `docs/11` §2.2 已记录列表当前是 4 条查询、
CPU 随并发放大尚未定位。契约 §23.3 明令**禁止**改成实时聚合，因此计数与关系表
写入在同一事务内维护（`app/services/engagement_service.py`）。

## 幂等性

`upgrade()` / `downgrade()` 都按「表/列/索引是否已存在」做检查，重复执行不炸。
Alembic 自身有版本表，正常情况下不会重复执行；这里显式检查是因为**半途失败**的
迁移（DDL 已执行、版本未登记）会留下「表已存在但 version 还差一步」的状态，
那时重跑必须能自愈。参考 `0005` 对「行不存在」的处理思路。

## downgrade() 说明与风险

- **有数据也能回滚**：`DROP TABLE` 本身不关心表里有多少行；`tool_favorites` /
  `tool_likes` 上没有别的外键指向它们，直接删表不会连带报错。
- **有损**：回滚会永久丢掉全部收藏/点赞关系与两个计数列。降级前如需保留，
  请先导出这两张表。
- `estimated_saving_minutes` 的回滚会丢掉作者自述的预计节省时长 ——
  它是**无法从别处推导**的输入数据（与 `0004` 的 `skill_tree_truncated` 同类）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: `INDEX(user_id, created_at DESC)`。
#:
#: 这里用手写 DDL 而不是 `op.create_index`：Alembic 的 `create_index` 只接受
#: 列名或 `Column` 对象，表达不了 **DESC 排序方向**（`sa.text("created_at DESC")`
#: 会因「索引未关联到任何表」而 CompileError）。0001 里那个部分唯一索引
#: `uq_tool_versions_current` 也是手写 DDL，此处沿用同一做法。
#: `IF NOT EXISTS` 在 SQLite 3.3+ 与 PostgreSQL 9.5+ 都支持，为幂等性兜底。
_DESC_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS {name} ON {table} (user_id, created_at DESC)"
)


def _inspector() -> sa.Inspector:
    """每次现取一个新的 Inspector —— 它会缓存反射结果，复用会读到旧 schema。"""
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return _inspector().has_table(name)


def _column_names(table: str) -> set[str]:
    inspector = _inspector()
    if not inspector.has_table(table):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def _ensure_engagement_table(table: str) -> None:
    """建关系表 + 两个索引（表已存在则跳过建表，但索引仍补齐）。"""
    if not _has_table(table):
        op.create_table(
            table,
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "tool_id",
                sa.Integer(),
                sa.ForeignKey("tools.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            # 每人每工具至多一条 —— 并发重复 PUT 的最终兜底（契约 §23.4）。
            sa.UniqueConstraint("user_id", "tool_id", name=f"uq_{table}_user_tool"),
        )

    existing_indexes = {index["name"] for index in _inspector().get_indexes(table)}
    if f"ix_{table}_tool_id" not in existing_indexes:
        op.create_index(f"ix_{table}_tool_id", table, ["tool_id"])
    if f"ix_{table}_user_created" not in existing_indexes:
        op.execute(_DESC_INDEX_SQL.format(name=f"ix_{table}_user_created", table=table))


def upgrade() -> None:
    # ---- ① + ② 两张关系表 ----
    _ensure_engagement_table("tool_favorites")
    _ensure_engagement_table("tool_likes")

    # ---- ③ tools 的 3 个新列 ----
    columns = _column_names("tools")
    with op.batch_alter_table("tools", schema=None) as batch_op:
        if "favorite_count" not in columns:
            # `server_default="0"` 是硬要求：`NOT NULL` 加列必须让既有行有值。
            # 用 `sa.text("0")` 而不是裸 `"0"`，PG 与 SQLite 都按常量表达式处理。
            batch_op.add_column(
                sa.Column(
                    "favorite_count",
                    sa.Integer(),
                    nullable=False,
                    server_default=sa.text("0"),
                )
            )
        if "like_count" not in columns:
            batch_op.add_column(
                sa.Column(
                    "like_count",
                    sa.Integer(),
                    nullable=False,
                    server_default=sa.text("0"),
                )
            )
        if "estimated_saving_minutes" not in columns:
            # 可空列没有默认值 —— NULL 表示作者未填写，**不是一个可以当成 0 的值**
            # （契约 §23.3）。
            batch_op.add_column(
                sa.Column("estimated_saving_minutes", sa.Integer(), nullable=True)
            )


def downgrade() -> None:
    # ---- ③ 先删 `tools` 的 3 个列 ----
    columns = _column_names("tools")
    with op.batch_alter_table("tools", schema=None) as batch_op:
        for name in ("estimated_saving_minutes", "like_count", "favorite_count"):
            if name in columns:
                batch_op.drop_column(name)

    # ---- ② 再删两张关系表（表里的数据随表一起丢弃，不会再报错）----
    for table in ("tool_likes", "tool_favorites"):
        if not _has_table(table):
            continue
        # 先删索引再删表：SQLite 会随表删索引，但 PG/显式写法更清楚，
        # 也让「重复执行 downgrade」在半途失败后能自愈。
        existing_indexes = {index["name"] for index in _inspector().get_indexes(table)}
        for name in (f"ix_{table}_user_created", f"ix_{table}_tool_id"):
            if name in existing_indexes:
                op.drop_index(name, table_name=table)
        op.drop_table(table)
