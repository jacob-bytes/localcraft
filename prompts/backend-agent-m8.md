# 后端开发 Agent — 任务书（M8 · 收藏 / 点赞 / 去重 / 数字概览）

## 你的身份与边界

你独占并只允许修改：`backend/`、`scripts/`、`deploy/`

**绝对不可以修改**：`docs/`、`contracts/`、`README.md`、`web/`。
前端由另一个 agent 并行开发。

**不要执行任何 git 命令**。版本控制由监控方处理。

工作目录：`/Users/jlthzy/Documents/selftool`

---

## 第一步：先读这些（不要跳过）

1. **`contracts/CONTRACT.md` §23 —— 本文是你的唯一规格来源。** 它是监控方在本轮开工前
   冻结的接口形状，**逐条照做**。§23 里写明的数字、列名、路径、公式都不要再自己发挥。
   特别是 §23.1（三条已核实的事实）、§23.2（去重为什么必须在服务端）、
   §23.3（计数用反规范化列）、§23.5（禁止给列表加 JOIN）、§23.6（savings 公式）。
2. `docs/00-关键决策.md` —— D35~D39 是本轮的依据，看清「明确不做的事」。
3. `docs/02-数据模型设计.md` —— 现有 20 张表的字段级设计、索引与迁移策略。
   新增表要跟既有风格一致（命名、索引命名、注释密度）。
4. `docs/11-优化点分析.md` §2.2 —— 理解为什么 §23.3 禁止实时聚合。
5. `backend/app/models/stats.py` 与 `backend/app/models/tool.py` —— 你要动的两张既有表。

> **上一轮的 M7（可观测性 / 性能根因）已收口并合入。** 不要重复做 O4/O12/M1/M2 那几项。

---

## 本轮的 7 项任务

### B5 · 迁移 `0006`：新增 2 表 + 3 列

`backend/migrations/versions/0006_*.py`，`down_revision = "0005"`。按契约 §23.3：

- 新表 `tool_favorites` / `tool_likes`：`UNIQUE(user_id, tool_id)`、
  `INDEX(user_id, created_at DESC)`、`INDEX(tool_id)`，两个外键都 `ON DELETE CASCADE`。
- `tools` 新增 `favorite_count` / `like_count`（`Integer`，`NOT NULL`，默认 0）、
  `estimated_saving_minutes`（`Integer`，可空）。

**要求**：

1. **`upgrade` / `downgrade` 都要实现**，且要**真的跑一遍 downgrade 再 upgrade**，
   把输出贴进报告。既有迁移（如 `0005`）都做到了这一点，照这个标准。
2. 新表要有数据时 `downgrade` 也不报错。
3. 新增列用 `server_default` 保证既有行有值（`NOT NULL` 加列在 SQLite 上尤其要注意）。
4. 迁移必须**幂等性检查**：重复跑不炸。参考 `0005` 里处理「行不存在」的写法。

### B6 · 收藏与点赞端点（4 个，全部幂等）

按契约 §23.4：`PUT`/`DELETE` × `{favorite, like}`，路径 `POST` 语义不用，就用 PUT/DELETE。

**幂等是硬要求**（契约 §23.8 台账已登记）：

- 重复 `PUT` → 成功，计数**只加一次**
- 未收藏时 `DELETE` → 成功，不报 404
- 并发重复 `PUT` → 靠 `UNIQUE(user_id, tool_id)` 兜底，捕获唯一约束冲突后按成功处理，
  **不要**让它变成 500

计数维护必须与关系表写入在**同一事务**内。计数**不允许**出现负数。

### B7 · `GET /api/v1/me/favorites`

分页，**响应复用门户列表项的 `ToolListItem` 形状**（不要新造一个形状），
按 `created_at DESC` 排序。

**可见性必须走既有规则**：用户收藏的工具若之后被下线/转私有/被删，
**不能**因为「在收藏表里」就泄漏出来。复用 `VisibilityContext.clause()` 与
`detail_scope_clause()`，不要在收藏查询里另写一套可见性判断。这是本轮最容易出错的地方。

### B8 · 列表与详情新增 4 个字段

`ToolListItem` 与 `ToolDetail` 各加 `favorite_count`、`like_count`、
`is_favorited`、`is_liked`（契约 §23.5）。

**实现约束（契约 §23.5，照做）**：`is_favorited` / `is_liked` **不得**通过给列表
查询加 JOIN 实现，用**一条附加查询**取当前用户的 `tool_id` 集合，在 Python 侧合并。

- 匿名请求者两个 bool 恒 `false`，两个计数照常返回。
- 分页列表中**没有**的工具不应出现在附加查询的结果里 —— 别把用户全部收藏都捞出来。

### B9 · 上传去重检测（服务端，响应回带）

**先读契约 §23.2，它解释了为什么不在浏览器算哈希**（`crypto.subtle` 需要安全上下文，
本项目按 D39 用 `http://<ip>:<port>` 直连）。

按契约 §23.2 的裁定：**不新增端点**。在上传响应里回带命中信息。

- 依据 `tool_versions.file_sha256`（**已有索引**，不要新增哈希计算）。
- 命中范围：**其他工具**已有的版本。同一工具的新版本**不算重复**（那是正常迭代）。
- 命中时**不阻断上传**，只回带信息让前端提示。
- 响应字段名与形状由你定，但**必须在报告里明确写出来**供前端对接，
  且要同步进 `backend/openapi.json`。命名要自解释（如 `duplicate_of`）。

### B10 · `GET /api/v1/admin/stats/insights`

按契约 §23.6 的**冻结形状**实现。**公式不得自行改动**：

```
total_minutes = Σ ( estimated_saving_minutes × COUNT(DISTINCT download_logs.user_id) )
                仅计入 estimated_saving_minutes IS NOT NULL 且 user_id IS NOT NULL
```

**注意**：

1. `downloads_daily` 必须**恰好 30 条**，缺口补 0（用 `tool_stats_daily` 或
   `download_logs` 聚合，自行判断哪个更合适并说明理由）。
2. `basis` 恒为字符串 `"author_estimate"`。**它不是可以省掉的装饰** ——
   契约 §23.6 说明了它的作用（让前端无法脱离口径单独渲染估算数字）。
3. 权限：**仅管理员可访问**，走既有管理端鉴权依赖，不要新写一套。
4. 不要新增图表数据结构（D10 / D35：数字与列表，不做图表）。
5. **性能**：这是重聚合，确认它不会拖慢 `/admin/overview`。若需要，加缓存并说明。

### B11 · `estimated_saving_minutes` 接入创建/更新

`ToolCreateRequest` / `ToolUpdateRequest` 新增可选字段 `estimated_saving_minutes`。
约束 **1 ~ 1440**，超出返回 `VALIDATION_ERROR`（用既有的错误码体系）。
**`NULL` 表示未填写，不是一个可以当成 0 的值** —— 不要给它默认 0。

### B12 · 契约同步与守卫更新

1. 重新生成 `backend/openapi.json`（§15.6 是形状权威）。
2. **接口总数从 93 变为 99**。以下 **3 处硬编码了 93**，都要更新并说明：
   - `backend/tests/test_cli.py:353`
   - `backend/tests/test_guard.py:196`
   - `backend/tests/test_guard.py:288`
3. 新端点的请求/响应模型要**齐全**（不要留 `Any` / 无模型的裸 dict）——
   `test_guard.py` 里有「所有端点都有响应模型」这类守卫，别绕过它。

---

## 通用硬约束

1. **基线：后端 465 测试全过**。新增测试后总数应上升，**不得有失败**。
2. **不得修改 `/api/v1` 已冻结的既有操作的形状**（加字段是允许的，改/删字段不允许）。
   若你认为必须改，停下来在报告里说明，不要自行改。
3. **SQLite 与 PostgreSQL 都要考虑**。你是能跑 SQLite 的；PG 侧如果没条件跑，
   **必须在报告里明确说明哪些部分未在 PG 上验证**，不要含糊。
4. 不新增运行时依赖。
5. Python 语法目标 **3.11**（本机可能是 3.13），有静态守卫，不要绕过。
6. 新增测试要覆盖：
   - 四个端点的**幂等性**（重复调用计数不变）
   - 匿名请求下列表/详情的 4 个字段取值
   - **收藏了不可见工具时不泄漏**（B7 的要求）
   - `estimated_saving_minutes` 的边界（0 / 1 / 1440 / 1441 / null）
   - `savings` 公式：构造已知数据，**手算期望值**再断言，不要用实现的输出反推期望
7. **若你新增任何带 `LOCALCRAFT_` 前缀的环境变量，必须同时配 `validation_alias` /
   `AliasChoices`。** pydantic-settings **不做前缀映射**，字段 `foo_bar` 只认
   `FOO_BAR`，写成 `LOCALCRAFT_FOO_BAR` 会被 `extra="ignore"` **静默吞掉**——
   表现为配置看起来生效（shell、systemd unit 里都有值）却完全不起作用，且**不报错**。
   这是 M7 实测踩到的坑（`docs/09` §15、`contracts/CONTRACT.md` §24.2），
   本轮 B10 若要加缓存 TTL 之类的变量，务必照做。
   另外注意：**32 个既有环境变量名里只有 4 个带 `LOCALCRAFT_` 前缀**，
   不要顺手给新变量都加前缀 —— 带不带前缀取决于有没有配 alias，与"规范"无关。

---

## 交付要求

报告需包含：

1. 7 项任务逐项的**状态**与**改动文件清单**。
2. 迁移 `0006` 的 **downgrade → upgrade 真实输出**。
3. B9 的**响应字段名与完整形状**（前端要照着对接）。
4. B10 的**手算验算过程**：用什么数据、期望多少、实际多少。
5. 接口数守卫 3 处的更新前后值，以及新的 `openapi.json` 接口总数。
6. 后端测试**命令与真实输出**（通过数）。
7. **任何未验证（尤其 PG 侧）、未做到、或与契约 §23 有出入的地方，明确说出来。**
   若有出入，说明为什么，不要静默偏离契约。
