"""M12 + M13: 站点定制设置项（portal.site_subtitle + portal.footer_tagline + 4 个页脚字段）

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-27 10:00:00.000000+00:00

对应 `contracts/CONTRACT.md` **§27.2「新增 5 个设置项」**（M12）
与 **§28.3「新增第 6 个设置项 `portal.footer_tagline`」**（M13），逐字照做：

    | key                            | value_type | 默认值 | description（= 管理端 label）        |
    | ------------------------------ | ---------- | ------ | ------------------------------------ |
    | portal.site_subtitle           | string     | ""     | 站点副标题（显示在门户与登录页，留空则不显示） |
    | portal.footer_tagline          | string     | `内网工具与 Skill 共享平台` | 页脚标语（显示在所有页面底部，留空则不显示） |
    | portal.footer_org              | string     | ""     | 页脚·运营方                          |
    | portal.footer_contact_email    | string     | ""     | 页脚·支持邮箱                        |
    | portal.footer_contact_phone    | string     | ""     | 页脚·内线电话                        |
    | portal.footer_notice           | string     | ""     | 页脚·备案号 / 版权声明               |

全部 `is_public = true`（`/meta` 只暴露公开项，契约 §27.1 事实 1）。
`portal.footer_tagline` 的默认值**刻意不是空串**（§28.3）：它是对 §27.2
「默认全空」的**定点例外**，作用是恢复 `AppShell` 里原本写死的那个串 ——
默认值取原串，未配置的既有部署在页脚上与改动前才**完全一致**。

## 为什么第 6 项并进本迁移，而不是新建 `0008`（契约 §28.5）

写 §27 时漏看了既有的全局页脚，`footer_tagline` 是 §28 的修正。**本迁移尚未提交、
未发布、没有任何数据库跑过它**（监控方核实：预览库停在 `0006`，验证用临时库已删除），
所以直接扩展 `0007` —— 未发布的迁移不该留下「补丁的补丁」。
`revision` / `down_revision` 因此**保持不变**：`0007` / `0006`。

## 为什么必须写迁移（契约 §27.1 事实 2）

`app/repositories/system_settings.py` 的 `SETTING_DEFAULTS` 是权威清单，但
**迁移 0002 用的是冻结快照**（其 docstring 明写「不 import 应用代码」）。
所以「把 key 加进 `SETTING_DEFAULTS`」**不会**让既有库拿到新行 —— 存量部署
会因为缺行而在管理端设置页看不到这几项（页面由数据库里的行驱动）。

## 为什么本文件也重复一份常量，而不是 import 应用代码

同上：迁移一旦发布就不应随应用代码变动，否则同一版本号在历史环境重放会得到
不同结果。0002 的 `SETTINGS`、0005 的 `KEY` 都是这个规矩。

## 为什么用 SQLAlchemy Core 而不是裸 SQL（契约 §27.1 事实 3）

`system_settings.value` 是 **JSON 列**。PostgreSQL **不接受** text→json 的隐式
转换（`INSERT ... VALUES (..., '')` 会报 DatatypeMismatch），而 SQLite 是弱类型，
裸 SQL 在本地测试里**看不出来** —— 这正是 0005 记录并修掉的坑。用
`sa.table(...)` + `sa.column("value", sa.JSON)` 让 SQLAlchemy 按方言做绑定处理：
SQLite 存 `'""'` 文本、PG 存 json `""`，两边都对
（`footer_tagline` 两边的区别只是 json 里放的是中文串而非空串）。

## 幂等性

`upgrade()` 逐 key 检查是否已存在，已存在就跳过 —— 不用 `bulk_insert` 无脑插
（重复跑会主键冲突）。`downgrade()` 用 `WHERE key IN (...)` 删除，缺行也不报错。
Alembic 自身有版本表，正常不会重复执行；显式检查是为了「DDL/DML 已执行但版本
未登记」的半途失败状态能自愈（参考 0005 对「行不存在」的处理）。

## downgrade() 说明与风险

回滚会**删除这 6 行**（连同管理员已填的值）。这是预期行为：它们是本轮新增的，
删掉即回到 M12 之前的状态。对 5 个门户字段而言默认值本来就是空串，所以视觉上
只是「回到没有这个特性」；`footer_tagline` 行删掉后 `/meta` 会回退到**代码默认值**
（`SETTING_DEFAULTS` 里同样是原标语串，§28.3），页脚照样显示原标语 —— 因此回滚
**不会**让未配置的部署产生视觉变化。已知有损之处：表里没有「值从哪来」的标记，
**无法区分**「仍是默认值」与「管理员真的填了内容」，所以回滚前如需保留配置，
请先导出这 6 行。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 本迁移播种的设置项 `(key, value, value_type, is_public, description)`。
#: **冻结快照**，取值与 `SETTING_DEFAULTS` 里那 6 条逐字一致（但刻意不 import）。
#: 第 2 条是 M13 并进来的（§28.3 / §28.5），默认值非空是刻意的定点例外。
NEW_SETTINGS: tuple[tuple[str, object, str, bool, str], ...] = (
    (
        "portal.site_subtitle",
        "",
        "string",
        True,
        "站点副标题（显示在门户与登录页，留空则不显示）",
    ),
    (
        "portal.footer_tagline",
        "内网工具与 Skill 共享平台",
        "string",
        True,
        "页脚标语（显示在所有页面底部，留空则不显示）",
    ),
    ("portal.footer_org", "", "string", True, "页脚·运营方"),
    ("portal.footer_contact_email", "", "string", True, "页脚·支持邮箱"),
    ("portal.footer_contact_phone", "", "string", True, "页脚·内线电话"),
    ("portal.footer_notice", "", "string", True, "页脚·备案号 / 版权声明"),
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
    """当前库里已存在的 key（只查这 6 个，避免全表扫描）。"""
    wanted = [key for key, *_ in NEW_SETTINGS]
    rows = bind.execute(
        sa.select(_settings.c.key).where(_settings.c.key.in_(wanted))
    ).fetchall()
    return {str(row[0]) for row in rows}


def upgrade() -> None:
    bind = op.get_bind()
    existing = _existing_keys(bind)

    # `insert().values()` 逐条执行，而不是 `op.bulk_insert()` 的 executemany：
    # 与 0005 同理，Core 的 insert 会走 `sa.JSON` 的绑定处理（方言正确），
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
    """删除本迁移新增的 6 行（有损，见模块 docstring）。缺行也不报错。"""
    bind = op.get_bind()
    keys = [key for key, *_ in NEW_SETTINGS]
    # 走 Core 的 delete（不是裸 SQL 字符串）：参数化绑定，且与本文件 upgrade
    # 保持同一种写法。JSON 列不参与 WHERE，所以这里没有 0005 那个类型坑。
    bind.execute(_settings.delete().where(_settings.c.key.in_(keys)))
