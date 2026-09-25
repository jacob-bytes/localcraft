"""add skill_tree_truncated to tool_versions

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-25 14:00:00.000000+00:00

contracts/CONTRACT.md §15.3 的裁定：`file_tree_truncated` 必须**持久化**。

背景：M2 用 `file_count + dir_count > len(file_tree)` 事后推断，但由于
`file_count` 只统计文件、`len(file_tree)` 同时含目录条目，口径不一致，
导致「3 个条目的 skill 包」也被判定为截断（监控方实测发现）。

该值**无法从存储的数据推导** —— 推导所需的真实条目总数在截断那一刻就丢了，
所以必须落库。这与 `is_current` 那种「可从别处推导」的冗余字段性质不同。

**downgrade() 说明与风险**：
- 本迁移的 downgrade 是**有损**的：删除该列会永久丢掉「这个版本的文件树是否被截断」
  这个事实。回滚后，任何依赖它的接口都会退回 M2 的推断逻辑（并在小包上误报）。
- 因此 downgrade 只在「确认不需要再回滚到 M2 行为」时使用。
- 数据行本身不做任何转换：新增列的 `server_default='0'` 让存量行自动得到一个
  安全值（`false`）。对**已截断**的历史行这个默认值是错的，但那些行本来就
  无法恢复真相（真实条目数已丢失），所以不做回填 —— 这一点在迁移里显式说明，
  避免运维误以为回填过。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite 不支持带非常量默认值的 ADD COLUMN，但布尔常量可以；
    # batch_alter_table 让 SQLite 走「重建表」路径，PG 走普通 ALTER。
    with op.batch_alter_table("tool_versions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "skill_tree_truncated",
                sa.Boolean(),
                nullable=False,
                # 用 `false` 而不是 `0`：PostgreSQL 不接受把 integer 默认值赋给
                # boolean 列（DatatypeMismatch）。SQLite 3.23+ 同样支持 TRUE/FALSE
                # 字面量，所以这一处写 `false` 两边都对 —— M4 的 PG 演练实测。
                server_default=sa.text("false"),
            )
        )

    # 存量行的默认值是 0（false）。对已被截断的历史行这并不准确，
    # 但真实条目总数已丢失、无法回填 —— 显式写在这里，避免误判为「已回填」。
    # 新解析的版本会写入真实值。


def downgrade() -> None:
    """**有损回滚**：删除该列会永久丢失「文件树是否被截断」的事实。

    回滚后相关接口会退回 M2 的推断逻辑（在小包上误报 true）。
    """
    with op.batch_alter_table("tool_versions", schema=None) as batch_op:
        batch_op.drop_column("skill_tree_truncated")
