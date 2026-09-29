"""M18 收尾：修正 `webapp.health_check_enabled` 的管理端文案（去掉过时的「（二期）」）

## 为什么「只改一行文案」也要开迁移

`GET /api/v1/admin/settings` 的 `description` **直接取自 `system_settings` 表的列**
（`app/services/settings_service.py:354` 的 `description=row.description`）——
管理端设置页完全由后端元信息驱动，而那张表的行是**迁移播种**的。
`SETTING_DEFAULTS` 只在「行不存在」时兜底，**改它不会影响既有库的显示**。

所以要让已经装好的实例看到新文案，只能新建一支迁移去 UPDATE 那一列。
`0002`（播种该行）已随 v1.0.0 发布，**不得再改**。

## 改什么，以及为什么

原文案是「在线工具探活开关（二期）」—— 那是 M2 时代写的。M14 **已经实现并接通**
了探活（`app/services/webapp_health_service.py`，由 `localcraft-maintenance.timer`
每小时调度），「（二期）」会让管理员以为这个开关还没用，从而不去启用它。
这正是 `docs/09` §18.3 记录的那类「文档/文案说的和实现不一致」。

**只改文案，不动 `value` / `value_type` / `is_public`** —— 这不是行为变更，
也不需要任何数据回填。

## 为什么用 Core 而不是裸 SQL

与 `0008` 同理：`sa.table()` + `sa.column()` 参数化绑定，SQLite 与 PostgreSQL 写法一致。
这一列不是 JSON，所以没有 `0005` 那个类型坑，但仍统一写法 ——
免得下一个改这文件的人以为这里可以用裸 SQL。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 本迁移只碰这一行。
KEY = "webapp.health_check_enabled"

#: 改前：`0002` 播种的原文。**冻结**在这里，好让 `downgrade()` 精确还原。
OLD_DESCRIPTION = "在线工具探活开关（二期）"

#: 改后。与 `SETTING_DEFAULTS` 里同一条的描述**逐字一致** ——
#: 本文件刻意不 import 应用代码（迁移要能独立运行），一致性由
#: `tests/test_m18_migration_0010.py` 钉住，与 `0008` 的冻结快照同一套做法。
NEW_DESCRIPTION = "在线工具探活开关（随维护任务定时探测）"

_settings = sa.table(
    "system_settings",
    sa.column("key", sa.String),
    sa.column("description", sa.String),
)


def upgrade() -> None:
    """改成不再声称「二期」。

    幂等且容错：行不存在时影响 0 行、不报错（全新库跑到 head 时该行一定存在，
    但手工删过行的库不该让升级失败）。
    """
    bind = op.get_bind()
    bind.execute(
        _settings.update().where(_settings.c.key == KEY).values(description=NEW_DESCRIPTION)
    )


def downgrade() -> None:
    """还原成 `0002` 的原文（无损：新旧两段文案都在本文件里冻结着）。"""
    bind = op.get_bind()
    bind.execute(
        _settings.update().where(_settings.c.key == KEY).values(description=OLD_DESCRIPTION)
    )
