"""create fts index

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-25 04:42:00.000000+00:00

SQLite FTS5 虚表 `tool_search_index`。

**必须判断方言**（docs/02 §6.1）：切到 PostgreSQL 时本迁移整体跳过，
另写 PG 版本的全文索引迁移（`pg_trgm` / `tsvector`）。

设计（docs/02 §3.20）：
  - `unicode61 remove_diacritics 2` 分词器
  - 中文在**写入时按单字加空格切分**（在 `app/search/base.py` 的
    `tokenize()` 里做），这样「工具」能搜到「内网工具平台」

**与 docs/02 §3.20 的一处偏差（已在 checkpoint 报告列为待裁决）**：
文档写的是 `content=''`（外部内容模式，不重复存原文）。但 FTS5 里
`content=''` 是 **contentless**（无内容）表，它**不支持 `DELETE` / `UPDATE`**
（实测：`cannot DELETE from contentless fts5 table`）。这会让
`SearchBackend.upsert()`（先删后插）与全量重建索引都无法实现。
contentless 表要支持删除需要 SQLite ≥ 3.43 的 `contentless_delete=1`，
而目标环境 openEuler 24.03 自带的 SQLite 版本无法保证 ≥ 3.43。

因此这里建**普通 FTS5 表**（保留原文副本）。代价是 name/summary/description/tags
被多存一份：按 docs/02 §7 的估算是约 1.5 MB/年，相对 50 MB/年的数据库总量可以忽略。
索引与原文的一致性不依赖数据库触发器，而是由应用层在同一事务内显式维护
（docs/02 §3.20 的原始要求，保持不变）。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FTS_TABLE = "tool_search_index"

CREATE_FTS = f"""
CREATE VIRTUAL TABLE {FTS_TABLE} USING fts5(
    name,
    summary,
    description,
    tags,
    tokenize='unicode61 remove_diacritics 2'
)
"""


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        # PostgreSQL：FTS5 不存在。将来在这里实现 pg_trgm / tsvector 版本。
        return
    op.execute(CREATE_FTS)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    op.execute(f"DROP TABLE IF EXISTS {FTS_TABLE}")
