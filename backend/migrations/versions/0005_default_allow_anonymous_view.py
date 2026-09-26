"""default portal.allow_anonymous_view to true

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-26 10:00:00.000000+00:00

需求变更：门户主页改为**默认允许匿名访问** —— 未登录即可浏览公开工具，
登录（且有权限）后才可下载等。

## 为什么改默认值，而不是让运维手工打开

`portal.allow_anonymous_view` 早已存在（docs/01 FR-ACL-06），后端也**完整实现**
并有测试。但前端此前**没有接线**：`RequireAuth` 包住了整个 `AppShell`，
匿名访客在发出任何请求之前就被重定向到 `/login`。也就是说这个开关一直是
「能改、但改了不起作用」。前端接线与本迁移同批交付，默认值直接对齐产品意图。

## 为什么新增迁移而不是改 0002

`0002` 是**已发布的**迁移。修改已应用的迁移会让"新建库"与"存量库"看到不同的
`0002` 历史，而且存量库根本不会因此得到新值。正确做法是新增本迁移做 UPDATE。

## downgrade() 说明与风险

回滚把值改回 `false`。后果是：所有**尚未登录**的访客在打开 `/` 时会被重定向到
登录页 —— 即回到本迁移之前的行为。

已知的有损之处：本迁移**无法区分**「值仍是默认值」与「运维主动设成了 true」，
因为表里只存最终值、没有来源标记。所以回滚会一并覆盖掉运维有意的选择。
在 v1.0.0 刚发布、尚无正式部署的前提下这是可接受的；若将来已有生产库，
回滚前请先导出该行的当前值。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEY = "portal.allow_anonymous_view"

#: 用 Core 的 `table()` + `sa.JSON` 而不是裸 SQL 改值。
#:
#: 原因：`system_settings.value` 是 JSON 列。PostgreSQL **不接受** text→json 的
#: 隐式转换（`UPDATE ... SET value = 'true'` 会报 DatatypeMismatch），
#: 而 `sa.JSON` 会让 SQLAlchemy 按方言做绑定处理 —— SQLite 存 `'true'` 文本、
#: PG 存 json `true`，两边都对。0002 的播种也是走这条路。
_settings = sa.table(
    "system_settings",
    sa.column("key", sa.String),
    sa.column("value", sa.JSON),
    sa.column("value_type", sa.String),
    sa.column("is_public", sa.Boolean),
    sa.column("description", sa.String),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def _current(bind: sa.Connection) -> bool | None:
    row = bind.execute(sa.select(_settings.c.value).where(_settings.c.key == KEY)).fetchone()
    return None if row is None else bool(row[0])


def upgrade() -> None:
    bind = op.get_bind()
    current = _current(bind)

    if current is None:
        # 该行缺失（例如库里的设置被人删过）。为了让「默认开着」真的成立，
        # 补一行显式的 true —— 否则运行时会回退到 SETTING_DEFAULTS_BY_KEY，
        # 虽然那里也已改成 true，但缺行本身是异常状态，不值得留白。
        # 用 `insert().values()` 而不是 `op.bulk_insert()`：后者是 executemany，
        # 不接受 `sa.func.now()` 这类 SQL 表达式作为值。
        bind.execute(
            _settings.insert().values(
                key=KEY,
                value=True,
                # 这三列都是 NOT NULL（`description` 可空），必须一并给出，
                # 取值与 `SETTING_DEFAULTS_BY_KEY` 里那一条保持一致。
                value_type="bool",
                is_public=True,
                description="是否允许未登录浏览门户",
                updated_at=sa.func.now(),
            )
        )
        return

    if current:
        return  # 已经是 true，幂等

    bind.execute(
        _settings.update()
        .where(_settings.c.key == KEY)
        .values(value=True, updated_at=sa.func.now())
    )


def downgrade() -> None:
    """改回 `false`（未登录访问门户将重新被重定向到登录页）。

    有损：会覆盖运维主动设置的 true —— 表里没有「值从哪来」的标记，
    无法只回滚"默认值那部分"。详见模块 docstring。
    """
    bind = op.get_bind()
    current = _current(bind)
    if current is None or not current:
        return

    bind.execute(
        _settings.update()
        .where(_settings.c.key == KEY)
        .values(value=False, updated_at=sa.func.now())
    )
