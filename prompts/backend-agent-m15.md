# 后端开发 Agent — 任务书（M15 · PostgreSQL 全文检索）

## 你的身份与边界

你独占并只允许修改：`backend/`、`scripts/`、`deploy/`

**绝对不可以修改**：`docs/`、`contracts/`、`README.md`、`web/`。
**本轮没有并行的前端 agent**，`web/` 也不需要改（检索是后端能力，前端走的是同一个列表接口）。

**不要执行任何 git 命令**。版本控制由监控方处理。

工作目录：`/Users/jlthzy/Documents/selftool`

---

## 第一步：先读

1. **`contracts/CONTRACT.md` §31 —— 本文的唯一规格来源。** §31.1 是已核实的现状、
   §31.2 的六条设计、§31.3 的迁移、§31.4 的 CLI 恢复、§31.5 的验证要求，**都冻结了**。
2. `backend/app/search/base.py` —— `tokenize()` / `tokenize_query()` / `SearchBackend` 接口。
3. `backend/app/search/sqlite_fts.py` —— SQLite 的实现与 `LikeSearchBackend` 回退。
4. `backend/app/search/__init__.py` 的 `get_search_backend()`。
5. `backend/migrations/versions/0003_create_fts_index.py` —— **方言守卫的写法照抄它**。

> 上一轮 M14 已收口并合入。**不要重做 M14 的任何一项。**

---

## 好消息：这不是一套新设计

`search/base.py` 里**已经有** `is_cjk()` / `tokenize()` / `tokenize_query()`，
中文按单字切分（`内网工具` → `内 网 工 具`）、英文数字保持整词。**这套切分对 FTS5
和 PG 的 `to_tsvector` 同样适用** —— 也就是说 PG 搜索中文**不需要任何扩展**
（不需要 `zhparser`，那要编译 C 扩展，在离线 openEuler 上是部署灾难）。

所以你要做的只是：**照 `SearchBackend` 接口补一个 PG 实现 + 一个迁移。**

---

## ★ 第一步不是写代码，是验证分词假设

§31.5① 要求的顺序**不能颠倒**：先在真 PG 上跑

```sql
SELECT to_tsvector('simple', '内 网 工 具');
SELECT to_tsvector('simple','内 网 工 具') @@ plainto_tsquery('simple','工 具');
```

确认「单字切分后中文能匹配」。**把原始输出贴进报告。**
如果这个假设不成立，整个方案要重来 —— 所以先做这一步，别写完再试。

---

## 任务

### C1 · `PostgresFtsBackend`

新增 `backend/app/search/postgres_fts.py`，按 §31.2 的六条实现。逐条都有理由，
不要自行放宽：

1. **存储用 `tools.search_vector` 列 + GIN 索引**，而且
   **★ 这一列不得加进 ORM 模型** —— 仓储到处用 `select(Tool)`，
   写进模型会让门户列表每次多拉一个体积可观的 tsvector。
   它只在迁移里存在，由本后端用原生 SQL 读写。
   与 SQLite 虚表的不对称要在 docstring 里写明。
2. **用 `simple` 配置，不要 `english`** —— SQLite 的 `unicode61` 不做词干还原，
   用 `english` 会让两种方言对同一查询给出不同结果。
3. 查询用 `tokenize_query(query)` → 空格连接 → `plainto_tsquery('simple', …)`
   （AND 语义，与 SQLite 侧的 `" AND "` 拼接一致）。
4. **不引入 `ts_rank`** —— 排序交给主查询，保持方言一致。
5. **索引文本必须截断** —— `to_index_text()` 会拼进可能很长的 Markdown 描述，
   而 PG 的 `tsvector` 有硬上限、超了直接报错。截断长度要说明依据。
6. **列缺失时优雅降级**（迁移没跑到）→ `available=False` → `matching_tool_ids`
   返回 `None` → 仓储走既有 LIKE 回退。**服务不得因此起不来**（SQLite 侧就是这个行为）。

### C2 · 迁移 `0009`（PG 专属）

§31.3：`0008` **已提交并推送**，按既定规则不得再改 → 新建 `0009`，
`down_revision = "0008"`。

- **方言守卫**：非 PostgreSQL 直接 `return`（照 `0003` 对 SQLite 的写法）
- 加列 `search_vector tsvector`
- 建 GIN 索引
- **回填既有行** —— 否则升级后搜不到旧数据
- `downgrade` 删索引 + 删列
- 重复 upgrade 不炸

### C3 · 接线与 CLI 恢复

- `get_search_backend()` 加 PG 分支
- PG 上 `supports_reindex` 变回 `True`（列存在时）→ **`reindex-search` 恢复可用**
- **M10 的那条断言要跟着改**（不是删）：它原本钉「PG 上没有索引 → 必须非 0 退出」，
  现在改为钉「**回退后端 / 列不存在时**必须非 0 退出」—— 那条不变量依然成立。

### C4 · 验证

§31.5 的六条，其中 **② 方言一致性是最强的检查**：造一批工具，
**同一个查询在 SQLite 与 PG 上返回的 id 集合必须相同**。不一致就是设计有问题，
**不许含糊过去**。

---

## 通用硬约束

1. **基线：后端 618 passed（SQLite）**，不得回归。
2. **PG 也要过**，且 CI 有**时区矩阵（UTC + Asia/Shanghai）的阻塞门禁**。
   本地验证 PG 时**每次全量跑前先重建库**（脏库会让全量套件变成几百个 error，
   监控方实测过）。`scripts/pg-dialect-suite.sh` 可用。
3. `backend/openapi.json`：**本轮不应有接口面变化**（检索是内部实现）。若确实变了，说明理由。
4. ruff 全绿；Python 3.11 语法目标；**不新增依赖**（`to_tsvector` 是 PG 内置）。
5. **只改 `backend/`、`scripts/`、`deploy/`**。

---

## 交付要求

1. C1~C4 的**状态**与**改动文件清单**。
2. **§31.5① 的原始 SQL 输出**（分词假设的验证）。
3. **§31.5② 方言一致性的实测**：查询、SQLite 返回的 id 集合、PG 返回的 id 集合；
   若有不一致，说明原因（**这正是最需要诚实的地方**）。
4. 长描述截断的长度与依据；列缺失时降级的实测（服务仍可用、走 LIKE）。
5. `reindex-search` 在 PG 上的实测输出与条数。
6. 迁移 `0009` 的 **downgrade → upgrade 与重复 upgrade 真实输出**。
7. 两种方言的测试**命令与真实通过数**。
8. **任何未做到、未验证（尤其 openEuler 目标硬件侧）、或与 §31 有出入的地方，
   明确说出来。**
