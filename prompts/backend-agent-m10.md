# 后端开发 Agent — 任务书（M10 · 修掉 PostgreSQL 方言的 7 条失败）

## 你的身份与边界

你独占并只允许修改：`backend/`、`scripts/`、`deploy/`

**绝对不可以修改**：`docs/`、`contracts/`、`README.md`、`web/`。
**有另一个 agent 正在并行改 `web/`**（M9 前端 viewer 修复），你碰它会冲突。

**不要执行任何 git 命令**。版本控制由监控方处理。

工作目录：`/Users/jlthzy/Documents/selftool`

---

## 背景：这 7 条是从未被验证过的方言问题

`tests/conftest.py` 早就支持用 `LOCALCRAFT_TEST_DATABASE_URL` 切换方言
（注释写明「用来暴露 SQLite 专属语法」），但这句承诺**从交付至今从未被兑现**。
监控方在 M7 收口时补了 CI job `backend-postgres`，**首跑即失败**。

清单与分类已登记在 **`docs/09` §16**（含 §16.8 的时区部分）与
**`contracts/CONTRACT.md` §24.2**。**先完整读这两处**，再动手。

你有本地 PostgreSQL 可用（Homebrew 16.14），**必须真的在 PG 上验证**，
不许只改代码就声称修好。

---

## 第一步：先读这些

1. **`docs/09` §16（含 §16.8）** —— 7 条失败的完整清单、分类与根因分析。
2. `contracts/CONTRACT.md` §24.2、§25 —— 相关裁定与台账。
3. `docs/11` §2.2 / §2.5 —— 为什么"改代码前先想清楚"。
4. `.github/workflows/ci.yml` 的 `backend-postgres` job —— 它**有时区矩阵**
   （UTC + Asia/Shanghai），你修完要保证两种时区都过。

---

## 要修的东西（按重要性排序）

### P1 · ★ 最后一个超管的保护在 PG 上失效（**安全相关，最重要**）

`app/services/admin_user_service.py::_cas_guard_last_superadmin`

现状：条件 UPDATE + 子查询断言「除自己外还有活跃超管」。docstring 声称靠
「**该行的写锁**」把判断与写入串行化，**但这个说法不成立** —— 两个并发请求
UPDATE 的是**不同的行**（各自的目标用户），行锁不冲突，没有任何互斥。

- **SQLite**：库级写锁使两者天然串行 → 侥幸正确
- **PostgreSQL（默认 READ COMMITTED）**：两者都在对方提交前读到「对方仍是超管」
  → **双双通过** → 超管归零（典型 write-skew）

**要求**：

1. 给出一个**在 SQLite 与 PG 上都正确**的串行化方案。可行方向（自行评估并说明取舍）：
   - 对**全部**活跃超管行取 `SELECT ... FOR UPDATE`（注意：必须锁**全集**，
     只锁「除自己外」的那部分**不管用** —— 两个事务会锁到互不相交的集合，
     仍然不互斥。这一点务必自己想清楚再写）
   - 或 PG 侧用 `pg_advisory_xact_lock`（SQLite 侧是空操作，因为库级锁本就串行）
   - 或提升隔离级别（要评估对整体性能与既有代码的影响）
2. **SQLite 路径的行为不得改变。**
3. `tests/test_m3_admin.py::test_last_superadmin_cas_with_two_admins`
   **必须在两种方言上都通过**。
4. 报告里要**论证**你选的锁为什么真的能互斥，而不是只说「加了锁应该好了」。

### P2 · 计数队列绑死在事件循环上

`app/services/counter_service.py` 的内存批量落库队列在**创建时绑定了当时的
事件循环**，跨 loop 复用就抛 `is bound to a different event loop`
（CI 日志里出现 262 次）。

失败用例：`tests/test_m3_admin.py::test_token_last_used_is_aggregated`

**要求**：让队列**不绑定**创建时的事件循环（例如把 `asyncio.Queue` 换成
显式的「缓冲列表 + 定时 flush 任务」，或按 loop 惰性重建）。
注意 `docs/11` 与本仓既有约束：**计数必须内存聚合批量落库，禁止逐请求 UPDATE**。
报告里说明你的方案在**进程内重建事件循环**时为什么是安全的。

### P3 · 时间字段的 UTC 偏移不一致

失败用例：`tests/test_m3_cov.py::test_token_revoke_is_idempotent`（**只在 PG 会话
时区非 UTC 时失败** —— 这正是 CI 时区矩阵要覆盖的）。详见 `docs/09` §16.8。

两个问题：

1. **测试缺陷**：它用 `.replace(tzinfo=None)` 剥掉时区后比较，只有在两个值恰好
   同偏移时才成立。**改成直接比较瞬时**（带 tz 比较，或都 `.astimezone(utc)`）。
2. **实现（较轻）不一致**：同一个 `revoked_at` 在不同代码路径下带不同 UTC 偏移
   （一次来自内存对象的 UTC，一次来自数据库回读的 PG 会话时区）。
   **瞬时值是对的**，所以不是数据错误；但同一接口对同一实体给出不同偏移的表示，
   对按字符串比较的客户端不友好。**请查明根因并给出统一口径**
   （例如所有出参时间统一 `astimezone(utc)`），并在报告里说明你改了什么。

### P4 · 两条测试无条件断言了 SQLite 专属指标（**这是测试的错，不是实现的错**）

- `tests/test_metrics.py::test_metrics_body_is_parseable_prometheus_text`
- `tests/test_observability.py::test_prometheus_output_parses_and_carries_required_metrics`

两者都断言 `localcraft_sqlite_wal_bytes` 存在，而该指标在 PG 上**正确地**不存在
（`wal_size_bytes()` 返回 `None`）。**实现行为是对的**，测试写错了。
改成按方言断言（SQLite 下要求存在、PG 下要求**不存在**——后者同样是有意义的断言，
不要只是删掉）。

### P5 · FTS 在 PG 上没有实现（**需要你调查并如实报告，不要硬修**）

- `tests/test_permission_deps.py::test_reindex_search_rebuilds_index`（`assert 0 >= 1`）
- `tests/test_permission_deps.py::test_fts_backend_degrades_when_table_missing`

迁移 `0003` 建的是 SQLite **FTS5 虚表**，且**正确地**做了方言守卫
（`if bind.dialect.name != "sqlite": return`）。所以 PG 上根本没有
`tool_search_index` 表。

**要求**：

1. **先调查**：PG 上跑 `python -m app.cli reindex-search` 会发生什么？
   **是否静默失败/静默空转却报成功？** 如果 CLI 报了成功但实际什么都没做，
   那是**真实缺陷**（运维会以为索引建好了），请修成显式行为
   （明确报错、或明确日志说明「当前方言不支持全文检索」）。
2. 然后决定这两条测试的处置：**如果该功能在 PG 上确实不存在**，
   按方言 skip 并写明理由是可以接受的；**但不要为了让 CI 变绿而 skip** ——
   必须先在报告里说清「PG 上搜索功能到底是什么行为」。
3. **不要**在本轮实现 PG 的 `tsvector` 全文检索 —— 那是产品决策
   （监控方会另行确认），超出本任务范围。

---

## 通用硬约束

1. **SQLite 与 PostgreSQL 都必须跑通**。基线：
   - SQLite：**521 passed**（当前）
   - PG：目前 514 passed / 7 failed，**你的目标是 7 failed → 0**
2. 你必须**真的起一个本地 PG** 验证（Homebrew `initdb` + 独立端口即可，
   用后停掉并清理）。**注意：套件不能跑在脏库上** —— 监控方实测过，
   残留 schema 会让全量套件变成 521 errors。每次全量跑之前**重建库**。
3. **两条 CI job 的时区都要过**：`UTC` 与 `Asia/Shanghai`。
   本地可以用 `psql -c "ALTER DATABASE <db> SET timezone TO 'Asia/Shanghai'"` 复现。
4. **不得绕过守卫**。若你必须改某条测试的断言，解释为什么改后的断言**依然有意义**。
5. ruff 必须全绿；Python 语法目标 **3.11**；不新增运行时依赖
   （PG 驱动 `psycopg` 是 `pyproject` 里已声明的可选 extra，允许 pip 装进 `.venv`，
   **但不要改 `pyproject.toml` / `requirements.lock`**）。
6. **不要动 `web/`**；`backend/openapi.json` 若有变化要重新导出并保持同步
   （本任务理论上不该改接口形状；若你认为必须改，先停下来报告）。

---

## 交付要求

报告需包含：

1. P1~P5 逐项的**状态**与**改动文件清单**。
2. **P1 的锁方案论证**：为什么它能真正互斥（这是本次最重要的产出）。
3. **两种方言的测试真实输出**：SQLite 与 PG 各自的 `passed/failed`。
4. **两种时区的 PG 结果**（UTC 与 Asia/Shanghai）。
5. P3 的根因结论与统一口径的做法。
6. P5 的**调查结论**：PG 上 `reindex-search` 到底做了什么、是否静默空转。
7. **任何未做到、未验证、或你不确定的地方，明确说出来。**
   特别是：**openEuler 目标硬件侧无法在本机验证**，如实说明。
