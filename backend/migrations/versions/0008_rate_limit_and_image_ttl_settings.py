"""M14: 应用层限流设置项 + 补播种 `images.signature_ttl_hours`

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-29 12:00:00.000000+00:00

对应 `contracts/CONTRACT.md` **§29.5「迁移 0008」（冻结）**，逐字照做。
共 **5 行**：

    | key                                   | 类型 | 默认 | is_public | description（= 管理端 label）        |
    | ------------------------------------- | ---- | ---- | --------- | ------------------------------------ |
    | images.signature_ttl_hours            | int  | 168  | false     | 图片签名 URL 有效期（小时），默认 7 天 |
    | security.rate_limit_enabled           | bool | true | false     | 限流总开关                           |
    | security.rate_limit_per_minute        | int  | 1200 | false     | 每 IP 每分钟的 API 总配额            |
    | security.rate_limit_login_per_minute  | int  | 10   | false     | 每 IP 每分钟的登录配额               |
    | security.rate_limit_upload_per_minute | int  | 30   | false     | 每 IP 每分钟的上传配额               |

## 为什么 `0007` 一行都不能改，必须新建 `0008`（§29.5）

`0007` **已随 v1.1.0 发布**：它跑过的库、发布件、以及其他人的环境都以
`0007` 的内容为准。再往 `0007` 里加行，会让「同一版本号在不同环境重放得到不同
结果」—— 这正是迁移文件必须冻结的原因。所以 M14 的 5 行走新迁移 `0008`，
`down_revision = "0007"`，链保持直线。

（对比 M13 / §28.5：当时 `0007` **尚未提交、未发布、没有任何库跑过它**，
所以那一次是「扩展 0007」，并且 §28.5 明确「不新建 0008」。本轮 §29.5 反过来
要求新建 `0008` —— 两条裁定并不矛盾，判据是「迁移是否已经发布」。）

## 为什么 `images.signature_ttl_hours` 要在这里补播种

它是 `SETTING_DEFAULTS` 里的既有项，运行时靠 `get_effective_int` 的**代码兜底**
生效（图片签名 TTL 一直是 168 小时，功能上没坏）。但**从来没有被任何迁移播种过**，
后果是：

  - 管理端设置页看不到它（页面由数据库里的行驱动，不是由 `SETTING_DEFAULTS` 驱动）；
  - `PUT /api/v1/admin/settings` 传它会报「未知的设置项」（`get_row` 为 None）；
  - 也就是说，一个**运行时真的生效**的配置项，管理员却无法查看与修改。

这是「东西已存在但没接通」的第三例（§29.1），本轮一并收口。

## 为什么也重复一份常量，而不是 import 应用代码

与 0002 / 0005 / 0007 同规矩：迁移一旦发布就不应随应用代码变动，
否则历史环境重放会得到不同结果。`tests/test_m14_migration_0008.py` 会逐字比对
本文件的冻结快照与 `app/repositories/system_settings.py::SETTING_DEFAULTS`。

## 为什么用 SQLAlchemy Core 而不是裸 SQL（§29.5）

`system_settings.value` 是 **JSON 列**。PostgreSQL 不接受 text→json 的隐式转换
（`INSERT ... VALUES (..., '')` 报 DatatypeMismatch），而 SQLite 是弱类型，
裸 SQL 在本地测试里看不出来（0005 记录并修掉的坑）。用 `sa.table(...)` +
`sa.column("value", sa.JSON)` 让 SQLAlchemy 按方言做绑定处理。

## 幂等性与 downgrade

`upgrade()` 逐 key 检查是否已存在，已存在就跳过（应对「DML 已执行、版本未登记」
的半途失败状态）。`downgrade()` 用 `WHERE key IN (...)` 删除，缺行也不报错。

回滚会**删除这 5 行**：4 个限流设置项删掉后，限流器回退到 `SETTING_DEFAULTS`
的代码默认值（总开关仍为 `true`、配额同值），行为不变；`images.signature_ttl_hours`
删掉后，图片签名 TTL 回退到代码默认 168 小时，行为同样不变。已知有损之处与 0007
相同：表里没有「值从哪来」的标记，无法区分「仍是默认值」与「管理员改过」，
回滚前如需保留配置请先导出这 5 行。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 本迁移播种的设置项 `(key, value, value_type, is_public, description)`。
#: **冻结快照**，取值与 `SETTING_DEFAULTS` 里这 5 条逐字一致（但刻意不 import）。
NEW_SETTINGS: tuple[tuple[str, object, str, bool, str], ...] = (
    (
        "images.signature_ttl_hours",
        168,
        "int",
        False,
        "图片签名 URL 有效期（小时），默认 7 天",
    ),
    ("security.rate_limit_enabled", True, "bool", False, "限流总开关"),
    (
        "security.rate_limit_per_minute",
        1200,
        "int",
        False,
        "每 IP 每分钟的 API 总配额",
    ),
    (
        "security.rate_limit_login_per_minute",
        10,
        "int",
        False,
        "每 IP 每分钟的登录配额",
    ),
    (
        "security.rate_limit_upload_per_minute",
        30,
        "int",
        False,
        "每 IP 每分钟的上传配额",
    ),
)

#: 用 Core 的 `table()` + `sa.JSON` 而不是裸 SQL —— 原因见模块 docstring。
_settings = sa.table(
    "system_settings",
    sa.column("key", sa.String),
    sa.column("value", sa.JSON),
    sa.column("value_type", sa.String),
    sa.column("is_public", sa.Boolean),
    sa.column("description", sa.String),
    sa.column("updated_by_id", sa.Integer),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def _existing_keys(bind: sa.Connection) -> set[str]:
    """当前库里已存在的 key（只查这 5 个，避免全表扫描）。"""
    wanted = [key for key, *_ in NEW_SETTINGS]
    rows = bind.execute(
        sa.select(_settings.c.key).where(_settings.c.key.in_(wanted))
    ).fetchall()
    return {str(row[0]) for row in rows}


def upgrade() -> None:
    bind = op.get_bind()
    existing = _existing_keys(bind)

    # `insert().values()` 逐条执行，而不是 `op.bulk_insert()` 的 executemany：
    # 与 0005 / 0007 同理，Core 的 insert 会走 `sa.JSON` 的绑定处理（方言正确），
    # 且逐条 insert 天然配合「已存在就跳过」的幂等策略。
    for key, value, value_type, is_public, description in NEW_SETTINGS:
        if key in existing:
            continue
        bind.execute(
            _settings.insert().values(
                key=key,
                value=value,
                value_type=value_type,
                is_public=is_public,
                description=description,
                # `updated_by_id` 可空（播种阶段没有操作人）。
                updated_by_id=None,
                updated_at=sa.func.now(),
            )
        )


def downgrade() -> None:
    """删除本迁移新增的 5 行（有损，见模块 docstring）。缺行也不报错。"""
    bind = op.get_bind()
    keys = [key for key, *_ in NEW_SETTINGS]
    # 走 Core 的 delete（不是裸 SQL 字符串）：参数化绑定，且与本文件 upgrade
    # 保持同一种写法。JSON 列不参与 WHERE，所以这里没有 0005 那个类型坑。
    bind.execute(_settings.delete().where(_settings.c.key.in_(keys)))
