# 后端开发 Agent — 任务书（M12 · 站点定制设置项与 /meta 扩展）

## 你的身份与边界

你独占并只允许修改：`backend/`、`scripts/`、`deploy/`

**绝对不可以修改**：`docs/`、`contracts/`、`README.md`、`web/`。
**有另一个 agent 正在并行改 `web/`**（M12 前端），你碰它会冲突。

**不要执行任何 git 命令**。版本控制由监控方处理。

工作目录：`/Users/jlthzy/Documents/selftool`

---

## 第一步：先读这些

1. **`contracts/CONTRACT.md` §27 —— 本文是你的唯一规格来源。** §27.1 的三条既有事实、
   §27.2 的 5 个设置项定义、§27.3 的 `/meta` 字段，都是冻结的，**逐字照做**。
2. `contracts/CONTRACT.md` §24 / §25 —— 相关裁定与台账惯例。
3. `backend/app/repositories/system_settings.py` —— 看 `SETTING_DEFAULTS` 的结构与注释。
4. `backend/migrations/versions/0005_default_allow_anonymous_view.py` ——
   **重点看它怎么处理 JSON 列**，那是你要模仿的先例。
5. `backend/app/schemas/meta.py` 与 `backend/app/services/settings_service.py`
   （`get_meta`）—— 你要改的地方。

> **上一轮 M10 已收口并合入**（PG 7 条失败全修、PG CI 门禁已恢复阻塞）。
> 不要重做 M10 的任何一项。

---

## 背景：为什么是「加设置项」而不是「加一套新机制」

平台已有 `system_settings` KV 表 + `is_public` 标记 + `GET /api/v1/meta` 公开出口。
而且管理端设置页**完全由后端元信息驱动**（label 取 `description`、控件取 `value_type`、
按前缀自动分组）——**所以新增 `portal.*` 设置项，管理端零改动就会自动出现编辑器。**

你要做的只是：把设置项加进权威清单、写迁移让既有库拿到新行、并把它们暴露到 `/meta`。

---

## 任务

### B1 · 把 5 个设置项加进 `SETTING_DEFAULTS`

按 §27.2 的表格逐字添加（key / value_type / 默认值 / `is_public=True` / description）。
description 就是管理端的 label，**照抄 §27.2 表格里的文案，不要自己改写**。

### B2 · 迁移 `0007`（**必须写，不能只改 `SETTING_DEFAULTS`**）

§27.1 事实 2：迁移 `0002` 用的是**冻结快照**（文件里明确写「不 import 应用代码」），
所以「加进 `SETTING_DEFAULTS`」**不会**让既有库拿到新行。

**要求**：

1. `revision = "0007"`，`down_revision = "0006"`。
2. **`upgrade` 幂等**：逐项检查 key 是否已存在，已存在就跳过（别用 `bulk_insert` 无脑插，
   重复跑会主键冲突）。参考 `0005` 的写法。
3. **`downgrade` 要真的能跑**：删除这 5 个 key。注意 downgrade 时如果管理员已经改过这些值，
   删除是预期行为（它们是本轮新增的），但仍要能不报错。
4. **★ 不要用裸 SQL 插 JSON 列**：`system_settings.value` 是 JSON 列，
   PostgreSQL **不接受** text→json（`0005` 的注释与 `docs/09` 都记了这个坑）。
   用 SQLAlchemy Core：
   `sa.table("system_settings", sa.column("key", sa.String), sa.column("value", sa.JSON), …)`。
5. **实测 downgrade → upgrade**，把真实输出贴进报告（`0005` 那次就是这么验的）。

### B3 · `/meta` 新增 5 个字段

按 §27.3：`site_subtitle` / `footer_org` / `footer_contact_email` /
`footer_contact_phone` / `footer_notice`，全部 `str`。

- **只新增，不动任何既有字段**（向后兼容）。
- 未配置时返回**空串**（不是 `null`）—— §27.2 定了默认空串，保持类型稳定。
- 走既有机制：`settings_service.get_meta` 从 `is_public=True` 的设置项取值。
  **不要**新写一套读取逻辑。
- 版本号**不要**加到设置项里，`app_version` 已经有了。

### B4 · 测试

- 断言 5 个新 key 存在于 `SETTING_DEFAULTS` 且 `is_public=True`、默认空串。
- 断言 `/meta` 返回这 5 个字段，且**未配置时是空串**。
- 断言既有 `/meta` 字段未变（防止顺手改坏）。
- 迁移幂等：upgrade 跑两次不炸。

---

## 通用硬约束

1. **基线：后端 522 passed（SQLite）**，不得回归。
2. **PostgreSQL 也要过**。CI 现在有 **PG 阻塞门禁**（时区矩阵 UTC + Asia/Shanghai），
   你的迁移**必须两种方言都能跑**。本地验证：起临时 PG（Homebrew `initdb` + 独立端口）
   并且**每次全量跑前先 dropdb/createdb 重建库**（脏库会让全量套件变成几百个 error，
   这个坑监控方实测过）。也可以直接用仓库里已有的 `scripts/pg-dialect-suite.sh`。
3. `backend/openapi.json` 要重新导出并保持一致（CI 有同步守卫）。
4. **不新增接口**——只加 `/meta` 的字段，接口面仍是 **99 operations / 79 paths**。
5. ruff 全绿；Python 语法目标 **3.11**；不新增依赖。
6. 若新增带 `LOCALCRAFT_` 前缀的环境变量，必须配 `validation_alias`
   （`docs/09` §15 记了它被 pydantic-settings 静默吞掉的坑）。**本任务不该需要新环境变量。**

---

## 交付要求

报告需包含：

1. B1~B4 的**状态**与**改动文件清单**。
2. 迁移 `0007` 的 **downgrade → upgrade 真实输出**（含「重复 upgrade 不炸」那一步）。
3. `/meta` 的**真实响应样本**（未配置时的空串形态）。
4. 两种方言的测试**命令与真实通过数**。
5. `openapi.json` 的接口面复核（应仍为 99 ops / 79 paths）。
6. **任何未做到、未验证、或与 §27 有出入的地方，明确说出来。**
   特别是 openEuler 目标硬件侧无法在本机验证，如实说明。
