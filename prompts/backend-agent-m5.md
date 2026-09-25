# 后端开发 Agent — 任务书（M5 · 收口）

## 先看这一节：M4 验收结论与监控方的失误

你的 M4 大部分做成了，但**有四项没做，其中三项不是你的责任** —— 是监控方从未把裁定送达给你。

完整裁定与**裁定传达台账**见 `contracts/CONTRACT.md` **§18**，请先完整读一遍。摘要：

### 你没做但你无从知道的四项（监控方失误）

| 裁定 | 我写在哪 | 为什么你没做 |
| --- | --- | --- |
| §14.3 图片签名 | 补写进了 M4 任务书 item 1.5 | **补写时间 20:54 晚于你开工时间（≈14:35），你从未看到** |
| §17.8 种子补 `viewer` 账号 | 只在契约里写了「已加入 M4 任务书」 | **实际从未写入该文件** |
| §17.9 种子补真实文件与 sha256 | 同上 | **实际从未写入该文件** |
| §14.6 种子扩到 26 工具 | 契约里写「M2 裁定」 | **只写进了前端任务书，后端从未收到** |

根因有两层，我在契约 §18.0 写明了：**契约里写「已加入任务书」却没有验证机制**；
以及**修改在途任务书后没有通知正在执行的 agent**。

**这四项全部归入 M5，不追究你的责任。** 从本节起，任何裁定都必须在 §18.1 的台账里登记为
「已送达」才生效 —— 没有登记的一律视为未生效。

### M4 中你做得好的地方

- **主动质疑验收口径**：性能目标未达标时，你没有粉饰，而是给出三条处置方案并附实测数据。这是对的
- **主动上报越界**：子 agent 执行了 `git status`，你如实上报而不是隐瞒。契约 §18.9 已裁定接受自曝，
  并补充了「为什么 `git status` 也算写操作」的理由与替代做法
- **PG 演练产出的两处迁移修正**（`WHERE is_current = 1` → `IS TRUE`、`server_default="0"` → `"false"`）
  是货真价实的跨库缺陷修复。契约 §18.8 已裁定本次接受回改，并要求此后 fix-forward
- **`TOKEN_REVOKED` 四态已实证正确**（有效/格式非法/查无此凭证/已吊销）

---

## 边界（硬性）

你仍然独占并只允许修改：`backend/`、`deploy/`、`scripts/`

**继续授权**：`docs/06-*`、`docs/07-*`、`docs/08-*`（三份手册在 M4 已交付，本轮只做增量修订）。
`docs/01`~`docs/05`、`contracts/`、`README.md`、`web/` **仍然只读**。

不执行任何 git 命令 —— **包括 `git status` 这类只读命令**（它会刷新并可能写入索引）。
需要确认改动范围时用 `find backend -newer <ref> -type f` 之类的文件系统手段。
委派子 agent 时必须在提示里显式写这一条，并对子 agent 的产出做一次越界复核。

工作目录：`/Users/jlthzy/Documents/selftool`

---

## M5 目标

**不新增功能。把欠的账补齐，把接口面的死桩修活，把性能基线落地，把交付件收全。**

一句话验收：`docs/05` §4.1 的目录树里**每一项都真实存在**；`docs/03` 里提到的每个接口
**都能被真的调通**；并发阶梯写进了运维手册；四份种子数据符合契约。

---

## 交付清单

### A. 补做监控方未送达的四项（优先级最高）

**A1. 图片签名能力 URL（§14.3，即 M4 item 1.5）**

- 后端在列表/详情响应里下发带签名的 `cover_url` / `thumb_url`，形如
  `/api/v1/images/88?variant=thumb&sig=<hmac>`
- `sig` = HMAC-SHA256(从 `SECRET_KEY` **派生的子密钥**, `"{image_id}|{variant}"`)，
  有效期由**新设置项** `images.signature_ttl_hours` 控制，默认 **168**（7 天）。
  派生方式与下载票据一致，**不要直接复用主密钥**
- `GET /api/v1/images/{id}` 接受两条鉴权路径：有效 `sig`，**或**有效 `Authorization` 头。
  两者都无 → `404`
- 签名比对用 `hmac.compare_digest`（常量时间）
- 前端**无需改动**（它已按「原样使用后端下发的 URL」实现并有 `onError` 占位降级）
- 测试：有效签名可取图、篡改签名 404、无签名无 Header 404、带 Header 可取图、过期签名 404
- **不新增接口**，92 操作数不变

**A2. 种子补 `viewer` 账号（§17.8）**

`viewer` / `Viewer@12345`，角色 `viewer`。与前端 mock 对齐。

**A3. 种子工具补真实文件与 sha256（§17.9）**

当前 8 个版本的 `file_sha256` 与 `storage_path` **全部为 null**，
**但 API 仍返回 `file_size`（如 4821043）且 `can_download=true`** ——
这是向用户与验收测试**广告了「可下载、有大小」，点下去必然失败**，比诚实显示不可下载更有害。

- 为 `file` / `skill` 种子工具写入**真实的占位包**，计算并写入 `sha256` 与 `storage_path`
- 若某个种子工具确实不提供文件，必须同时把 `file_size` 置 `null` 且 `can_download=false`
- **不允许出现「有大小、可下载、但无文件」的中间态**
- `webapp` / `prompt` 类型本来就无文件，保持 `file_size=null` 即可

**A4. 种子扩到 26 个工具（§14.6）**

保留原 8 个作为边界子集（含 2 个无封面、1 个超长名称），新增 18 个普通工具（含真实占位文件），
使 `page_size` 12/24/48 分别得到 **3/2/1** 页。

同时补 3 个作者账号 `zhangsan` / `lisi` / `wangwu`（`Author@12345`，角色 `user`），
与前端 mock 保持一致 —— **mock 比真实种子更完整是一种倒挂，会掩盖差异**。

`seed-demo` 必须继续幂等。

### B. 实现死桩路由（§18.4）

`POST /api/v1/admin/tools/{tool_id}/versions` 目前恒定返回 `404`（响应体是一句提示）。
**一个在冻结清单里、被 `docs/03` §5.2 示例脚本引用、却永远失败的路由，比没有它更糟。**

实现为可用的别名，委托同一个 service。保持接口面为 **92** 不变（不增不减）。

补测试：以 `admin:all` scope 的 API Token 走 `docs/03` §5.2 的示例流程能跑通。

### C. `psycopg` 正式声明（§18.5）

**当前状态使 D21 的承诺为假**：干净安装的机器上驱动根本没装，
而 `requirements.txt` / `.lock` / wheelhouse 里都没有它。

- `pyproject.toml` 增加 `[project.optional-dependencies] pg = ["psycopg[binary]"]`
- **两套 wheelhouse 都要补入 psycopg 的 wheel**（离线环境要能按需装）
- `scripts/verify-wheelhouse.sh` 的检查要覆盖它
- 监控方已修订 `docs/02` §6.2，把「装驱动」写进迁移前置步骤

### D. 两个无消费方的设置项接线（§18.6）

**D1. `approval.version_reapproval` —— 接线，不删**

（监控方否决了「删除」的建议：它与 `approval.mode` 不是同一维度。
`approval.mode` 管**首次发布**是否需审；`version_reapproval` 管**已发布工具的新版本**是否需再审。
「可信工具，更新免审」是合理策略。）

行为：为 `false` 时，对**已处于 `approved`** 的工具上传新版本，直接批准并转正
（写入 `approval_records`，`is_automatic=true`，`auto_rule="version_reapproval_off"`）。
默认保持 `true`，与当前行为一致，**无迁移影响**。

**D2. `quota.warn_threshold_pct` —— 接线**

在 `GET /admin/overview` 增加计算字段 `storage_warning: bool`（由该设置与当前用量算出）。
前端直接渲染，`scripts/disk-alert.sh` 复用同一口径（判断逻辑集中在一处，不要各算各的）。

**D3. `webapp.health_check_enabled` —— 保持现状**（已通过 `/meta.features` 声明能力为关闭，
`docs/01` 第 10 章明确不做探活）。

接线完成后，报告里给出「已定义未消费」清单 —— 目标应为**空**。

### E. 发布件补齐（§18.7）

`docs/05` §4.1 的目录树列了但仓库没有的项，本轮补齐：

| 文件 | 要求 |
| --- | --- |
| `scripts/uninstall.sh` | 停止并禁用服务、移除 unit、**保留数据目录并要求显式 `--purge` 才删** |
| `RELEASE-NOTES.md` | 升级流程依赖它判断「本次是否含数据库迁移」（`docs/05` §11 已引用）。首版写 M1~M5 的概要 |
| `deploy/nginx/` 下的 TLS 生产配置 | `docs/05` §7 的生产版目前只存在于文档里，需要有可部署文件 |
| `deploy/selftool-limits.conf` | systemd 加固 / 文件描述符上限 |
| `deploy/selftool.tmpfiles` | 运行目录的创建与权限 |
| `deploy/systemd/selftool-gc.*` | **先检查是否与已有的 `selftool-maintenance.*` 重复**；若重复则只保留一套并在报告里说明取舍，不要两套做同一件事 |
| `scripts/upgrade.sh` / `scripts/rollback.sh` | `docs/05` §11 的升级回滚流程需要可执行载体 |
| `scripts/notify-ready.sh` | systemd `Type=notify` 变体需要（`docs/05` §6.4 有说明） |
| `scripts/metrics-snapshot.sh` | `docs/05` §10 的指标清单 |
| `scripts/disk-alert.sh` | 复用 D2 的阈值口径 |
| `scripts/alert-webhook.sh` | 供上面几个脚本调用 |
| `scripts/security-check.sh` | `docs/05` §13 的安全基线自查 |

**要求**：补齐后把「`docs/05` §4.1 目录树 vs 实际文件」的差异清单交给我（该文档由我修订）。
不允许出现「文档列了、包里没有」或「包里有了、文档没写」两种偏差。

### F. 性能：有界优化 + 基线落地（§18.3）

**当前实测**（监控方复跑，macOS 笔记本 + 沙箱，无 nginx）：

```
并发    P50(ms)    P95(ms)      req/s
   1          8          9     124.47
   5         25         29     193.24   ← 峰值
  10        107        136      97.76
  20        279        379      69.78
  50        748       1098      64.90
 100       1414       1586      70.31
```

瓶颈是**单 Python 进程的 CPU/GIL**，不是 SQLite（写加压到并发 40，`database is locked` **0 次**）。

**F1. 做一轮有界优化，不新增接口**：

- 列表接口的 `COUNT(*)` 与 `facets` 聚合是热点。候选手段：
  - `facets` 短时缓存（键需含**可见性哈希**，不能跨用户复用；TTL 建议 30 秒）
  - 消除同一请求内的重复 `COUNT`（分页计数与 facets 各自算了一次）
  - 排查列表查询的 N+1（分类、标签、当前版本、封面是否各发一次查询）
- **禁止为了数字好看而加缓存却不说明**：报告里要写明缓存键、TTL、失效条件
- 优化后**重跑 `scripts/m4-drill-perf.sh` 并贴完整阶梯**

**F2. 把容量基线写进 `docs/08-运维手册.md`**：

- 完整的并发阶梯表 + 测量环境说明（**必须写明 macOS 笔记本 + 无 nginx，不代表生产**）
- **扩展触发条件**：持续在途并发 > 20，或写事务 > 50/s → 迁 PostgreSQL 并放开多 worker
- 说明为什么单 worker：SQLite 单写者（`docs/05` §6.3）
- **待办**：生产数字需在目标硬件（openEuler + nginx）上重测

### G. 文档不一致清单（§18.10）

三份手册在 M4 附了约 **25 条**「文档 vs 实现」不一致（用户 9 / 管理员 12 / 运维 12）。

**本轮必须给出：去重后的完整清单**，每条标注：

| 字段 | 说明 |
| --- | --- |
| 位置 | `docs/xx` 的哪一节 |
| 现状 | 文档怎么写、实现怎么做 |
| 影响 | 谁会因此踩坑 |
| 处置建议 | 改文档 / 改实现 / 不改（附理由） |

已知影响最大的三条：`version_reapproval` 无消费方（D1 解决）、
`/admin/tools/{id}/versions` 固定 404（B 解决）、**CSV 导出列与 `docs/03` §3.14 示例不一致**。

> CSV 那条请特别核实：如果实现是对的而文档示例过时，我改文档；
> 如果实现漏了列，你补实现并在报告里说明。

**不允许只处理影响大的三条就把其余丢掉。**

### H. 迁移纪律写入运维手册（§18.8）

`docs/08` 需写明：**一旦发布了 tar.gz 或有人跑过某条迁移，该迁移文件即冻结，只能新增迁移修正，
不得回改**。并说明本次 M4 为何是例外（项目尚未有任何生产部署，且改动在 SQLite 上语义等价）。

### J. 前端 M3 checkpoint 追加的两项（监控方补记，2025-03）

这两项由前端 M3 的联调报告提出，属于**响应字段/元数据完善，不新增接口**，92 操作数不变。

**J1. `GET /admin/groups` 的删除影响面要带 `slug`**

`group_service.py` 的 `details.tools` 目前只含 `{id, name}`。`docs/04` §6.14 要求
「影响面工具可点击跳转」，但前端拿不到 `slug` 就无法生成详情页链接，只能渲染纯文本。

**要**：在 `details.tools[]` 中增加 `slug`（附加字段，非破坏性）。
补测试：`GROUP_IN_USE` 的 `details.tools[].slug` 非空且能用于 `GET /tools/{slug}`。

**J2. 补齐 6 个端点的 `response_model`**

`contracts/CONTRACT.md` §15.6 宣布「`openapi.json` 是响应形状的权威来源」，
但以下 6 个端点的响应在 openapi 里仍是 `additionalProperties: true`，**没有字段定义** ——
形状目前只存在于前端的 `types.ts` 里，这让「形状权威」这句话对这 6 个端点名不副实：

```
GET    /api/v1/admin/groups
GET    /api/v1/admin/groups/{group_id}/members
DELETE /api/v1/admin/groups/{group_id}
DELETE /api/v1/admin/groups/{group_id}/members/{user_id}
POST   /api/v1/admin/users/{user_id}/revoke-sessions
GET    /api/v1/admin/tokens
```

**要**：为它们补显式的 Pydantic 响应模型，并重新导出 `openapi.json`。
补测试：断言这 6 个端点在 openapi 里有非空的 `properties`（不是 `additionalProperties: true`）。

> 这会改变 `openapi.json`，前端需要同步 `types.ts` —— 属预期影响，不是破坏性变更。

### I. 全量回归

- 补 A~E 涉及的所有测试
- 覆盖率目标：整体 **≥ 90%**（当前 91%，不要因为加代码而掉下去）
- 接口面：**恰为 92**（B 是别名实现，不增不减）
- `ruff check app tests` 全过；对 `scripts/` 下新增的 shell 脚本跑 `shellcheck`
  （缺工具就跳过并说明）

---

## M5 验收清单（监控方会逐条核验）

| # | 场景 | 期望 |
| --- | --- | --- |
| 1 | 列表/详情下发的 `cover_url` | 含 `sig=` |
| 2 | 无鉴权头访问图片（带 sig） | `200` 且返回图片 |
| 3 | 篡改 `sig` / 无 `sig` 无 Header | `404` |
| 4 | 带 `Authorization` 头访问图片 | `200` |
| 5 | 种子账号 | 含 `viewer`（角色 `viewer`）与 3 个作者账号 |
| 6 | 种子工具数 | 26 个（8 边界 + 18 普通） |
| 7 | 种子 `file`/`skill` 版本 | `sha256` 与 `storage_path` 均非空，且下载能成功、SHA256 对得上 |
| 8 | 种子 `webapp`/`prompt` 版本 | `file_size=null` 且 `can_download` 语义正确（无「有大小无文件」的中间态） |
| 9 | `seed-demo` 幂等 | 连续执行两次结果一致 |
| 10 | `POST /admin/tools/{id}/versions` | **能真的上传成功**（不再是 404） |
| 11 | `docs/03` §5.2 的示例流程 | 用 API Token 跑通 |
| 12 | `psycopg` | 在 `pyproject.toml` 的 `pg` extra 里；**两套 wheelhouse 都含其 wheel** |
| 13 | `verify-wheelhouse.sh` | 覆盖 psycopg，且无 `sdist` 混入 |
| 14 | `version_reapproval=false` | 已发布工具发新版本 → 直接转正，审批记录 `is_automatic=true` |
| 15 | `version_reapproval=true`（默认） | 行为与现在一致（走审批），无回归 |
| 16 | `GET /admin/overview` | 含 `storage_warning` 字段，且随设置与用量变化 |
| 17 | 「已定义未消费」设置项 | 清单为**空** |
| 18 | `docs/05` §4.1 的每一项 | 仓库中都真实存在 |
| 19 | `uninstall.sh` | 默认保留数据目录；`--purge` 才删 |
| 20 | `RELEASE-NOTES.md` | 存在且写明是否含迁移 |
| 21 | performance 优化后阶梯 | 重跑并贴完整表格；`c=20` 的 P95 应明显低于 379ms |
| 22 | `docs/08` 容量章节 | 含完整阶梯 + 测量环境局限 + 扩展触发条件 |
| 23 | 文档不一致清单 | **去重后完整**（约 25 条），每条有处置建议 |
| 24 | `docs/08` 迁移纪律 | 写明 fix-forward 规则 |
| 25 | 接口面 | 恰为 92 |
| 26 | 整体覆盖率 | ≥ 90% |
| 27 | `ruff` / `shellcheck` | 全过 |
| 28 | `GROUP_IN_USE` 的 `details.tools[]` | 含 `slug` 且可用于跳转 |
| 29 | 上述 6 个端点的 openapi 响应 | 有显式 `properties`，不再是 `additionalProperties: true` |

---

## 报告要求

按 `contracts/CONTRACT.md` §10 格式回报。额外要求：

- 验收 **1、7、10、12、21、23** 必须贴原始命令与输出
- **验收 21 要贴优化前后的完整阶梯对比**，并说明每项优化做了什么、
  缓存键与 TTL 是什么、失效条件是什么。**如果某项优化没效果，如实说没效果**
- **验收 23 的清单要完整**，不允许只列影响大的几条
- `docs/05` §4.1 的差异清单交给我（我改文档）
- 如果某项做不了，明确说明做不了与原因，不要用模糊表述代替
- 委派子 agent 时，提示里必须含「不得执行任何 git 命令（含 `git status`）」，并复核其产出

---

## 易错点清单

1. **图片签名的子密钥必须是派生的**，不要直接复用 `SECRET_KEY`（与下载票据同理）
2. **签名比对用 `compare_digest`**，不要用 `==`
3. **`facets` 缓存的键必须含可见性哈希** —— 缓存跨用户复用会导致**越权泄露工具名与计数**
4. **种子占位文件要有真实内容与 sha256**，不能只填 `storage_path` 而不落盘
5. **`seed-demo` 必须保持幂等**，新增 18 个工具后仍要如此
6. **别名路由不要复制粘贴业务逻辑**，委托同一个 service，否则两处会漂移
7. **`version_reapproval=false` 的自动批准也要写 `approval_records`**，
   否则审批历史出现空洞（与 `auto_approve_all` 同一要求）
8. **`storage_warning` 的判断逻辑只写一处**，`/admin/overview` 与 `disk-alert.sh` 复用
9. **`uninstall.sh` 默认不能删数据** —— 误删生产数据是不可逆事故
10. **不要为了让 benchmark 好看而牺牲正确性**（如跳过权限过滤、放宽可见性）

---

## 不要做的事

- **不新增接口**（接口面冻结于 92；B 是别名，不改变数量）
- 不新增功能特性。M5 是收口
- 不改 `docs/01`~`docs/05` 与 `contracts/`，发现问题报告给我
- 不写前端代码，不改 `web/`
- 不引入 Celery / Redis / MQ
- **不执行任何 git 命令，含 `git status`**
- 不做数据可视化图表（`docs/01` 第 10 章明确不做）
- 不把「检查通过」说成「验证通过」

有阻塞或需要裁决时，立刻按 §10 格式报告，不要自己猜着往下做。
