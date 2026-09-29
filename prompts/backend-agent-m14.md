# 后端开发 Agent — 任务书（M14 · 探活 / 限流 / 迁移 0008 / 导入终态检查 / 守卫补盲）

## 你的身份与边界

你独占并只允许修改：`backend/`、`scripts/`、`deploy/`

**绝对不可以修改**：`docs/`、`contracts/`、`README.md`、`web/`。
**有另一个 agent 正在并行改 `web/`**（M14 前端）。

**不要执行任何 git 命令**。版本控制由监控方处理。

工作目录：`/Users/jlthzy/Documents/selftool`

---

## 第一步：先读

1. **`contracts/CONTRACT.md` §29 —— 本文的唯一规格来源。** §29.2 的探测规则、
   §29.3 的字段名、§29.4 的限流口径、§29.5 的迁移内容、§29.6 的终态检查、
   §29.7 的守卫扩展，**都是冻结的，逐条照做**。
2. `contracts/CONTRACT.md` §24.1（loopback 豁免的先例）、§26.3（导入守卫缺口的原始分析）。
3. `backend/app/cli.py` 的 `maintenance` 命令与 `backend/app/services/maintenance_service.py`。
4. `backend/app/migrations/versions/0007_*.py` —— 迁移的先例（Core + `sa.JSON`）。
5. `backend/app/core/deps.py` 的 `get_client_info()` —— 取客户端 IP 的既有实现，**复用，不要新写**。

> 上一轮 M13 已收口并合入。**不要重做 M12/M13 的任何一项。**

---

## 为什么是这一轮：三个「东西已存在但没接通」的半成品

| 项 | 已存在 | 缺 |
| --- | --- | --- |
| 在线工具探活 | 设置项 + `/meta` 的 feature + **三个数据库列** | **零实现**（预览库 26 行状态全 NULL） |
| 应用层限流 | 错误码 `RATE_LIMITED`（已映射 429） | **零实现**（全仓没有一处抛出它） |
| `images.signature_ttl_hours` | 清单里有、运行时兜底生效 | **从未被任何迁移播种** |

它们会以「看起来有」的形态误导人 —— 这正是要修的原因。

---

## 任务（5 项）

### B1 · 在线工具探活（最大的一项）

按 §29.2 实现：新增 maintenance 任务 **`webapp-health`**，接进
`app.cli maintenance --task` 与 `run_all`。**不新增 systemd 单元**。

- 受 `webapp.health_check_enabled` 控制（**终于让这个开关有意义**）
- 只测 `deleted_at IS NULL` + `tool_type == 'webapp'` + `webapp_health_url` 非空
- **探测规则严格照 §29.2 的表**：HEAD→GET 回退、5s 超时、并发 8、每次上限 200、
  **不跟随重定向**（3xx 记为 ok 但不继续请求）
- 写 `webapp_health_status`（`ok`/`fail`/`timeout`）与 `webapp_checked_at`
- **`NULL` 表示从未检测，与 `fail` 是两回事**，不要用 `fail` 当初始值

按 §29.3 暴露字段：`ToolListItem` 加 `webapp_unhealthy: bool = False`（派生，
仅 `fail`/`timeout` 为真，**NULL 不算不健康**）；`ToolDetail` 加
`webapp_health_status: str | null` 与 `webapp_checked_at: datetime | null`。

`--dry-run` / `--json` 的既有语义要沿用（`--dry-run` 不得写库）。

### B2 · 应用层限流

按 §29.4：进程内滑动窗口，键为 `(scope, client_ip)`，**复用 `get_client_info()`**。

- **★ loopback 豁免**（`127.0.0.1` / `::1` / `localhost` / `testclient`），理由见 §29.4
- 429 + `code = RATE_LIMITED` + **`Retry-After` 响应头**
- 4 个设置项按 §29.4 的表加进 `SETTING_DEFAULTS`（默认值照抄）
- 接入：`/api/v1` 全局总配额 + 登录 + 上传三档

**★ 测试要求（最容易做错的一点）**：因为 loopback 被豁免，**TestClient 打不到限流**。
所以**必须直接测限流器本身** —— 用一个非 loopback 的伪 IP 调用它，验证：
窗口内放行到阈值、超阈值拒绝、窗口滑过后恢复、`Retry-After` 值合理、
loopback 永远放行、开关关闭时永远放行。
**不要**试图通过 TestClient 发几百次请求来触发（既慢又不可靠）。

另外：**必须确认现有 548 个用例不会被限流打破**。若某个用例大量请求同一端点，
在 `conftest` 里关掉限流即可（但**不要**因此把生产默认值改成 `false`）。

### B3 · 迁移 `0008`

§29.5：`0007` **已随 v1.1.0 发布**，**不得再改它**。新建 `0008`，`down_revision = "0007"`。

内容 5 行：`images.signature_ttl_hours`（补播种）+ §29.4 的 4 个限流设置项。

沿用既有要求：逐 key 幂等 upgrade、downgrade 能跑、**Core + `sa.column("value", sa.JSON)`**。
跑一遍 **downgrade → upgrade 与重复 upgrade**，贴真实输出（SQLite 与 PG 都要）。

### B4 · 导入/恢复的终态检查

§29.6：在导入事务**提交前**检查「本次导入后活跃超管数」，
**为 0 则整体回滚并报错**。

**不要**给这条路径逐步加 `_cas_guard_last_superadmin` —— 合法的「从备份恢复」
必须允许超管数低于当前值（比如备份里就只有 1 个）。要堵的只是
「恢复完一个超管都没有」这个把自己锁在门外的结果。

新增测试：构造一份「只有一个超管、且该超管被降级」的导入数据，
断言**整体回滚**且库里仍有超管；另构造一份正常数据，断言仍然成功。

### B5 · 守卫补盲

§29.7：把 `tests/test_shell_portability.py` 从「只扫 `.sh`」扩展为
**同时扫 `.github/workflows/*.yml` / `*.yaml`**（按文本扫）。

§29.7 已写明这个做法的**已知取舍**（可能命中 YAML 注释与中文说明），
这是可接受的。若你跑完发现**确有误报**，**先看是不是真的应该加花括号**，
再考虑调整匹配范围，并在报告里说明。

---

## 通用硬约束

1. **基线：后端 548 passed（SQLite）**，不得回归。
2. **PG 也要过**，且 CI 有**时区矩阵（UTC + Asia/Shanghai）的阻塞门禁**。
   本地验证 PG 时**每次全量跑前先重建库**（脏库会让全量套件变成几百个 error，
   监控方实测过）。`scripts/pg-dialect-suite.sh` 可用。
3. `backend/openapi.json` 重新导出（本轮**有接口面变化**：§29.3 的新字段）。
   **接口数不变**（仍 99 operations / 79 paths）—— 只加字段，不新增端点。
4. ruff 全绿；Python 3.11 语法目标；**不新增依赖**（限流自己写，别引 slowapi）。
5. 若新增 `LOCALCRAFT_` 前缀环境变量，必须配 `validation_alias`（`docs/09` §15 的坑）。
   **本任务不该需要新环境变量**（限流走设置项）。
6. **只改 `backend/`、`scripts/`、`deploy/`**。

---

## 交付要求

1. B1~B5 逐项的**状态**与**改动文件清单**。
2. B1：探测规则的**实测**（至少一个真实可达 URL 与一个不可达 URL 的结果），
   以及「不跟随重定向」的证据（用一个返回 302 的地址，断言状态是 ok 且**没有**请求 Location）。
3. B2：**限流器直接测试**的用例清单与通过数；以及 `Retry-After` 的实测值。
4. B3：迁移 `0008` 的 **downgrade → upgrade 与重复 upgrade 真实输出**（两方言）。
5. B4：两条测试（回滚 / 正常成功）的断言说明。
6. B5：扩展后的守卫**在两种文件上都生效**的证据（比如故意在 .yml 里放一个
   `$VAR（` 看它是否报错，验完撤掉）。
7. 两种方言的测试**命令与真实通过数**；`openapi.json` 的接口面复核。
8. **任何未做到、未验证（尤其 openEuler 目标硬件侧）、或与 §29 有出入的地方，
   明确说出来。**
