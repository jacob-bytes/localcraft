# 后端开发 Agent — 任务书（M3）

## 先看这一节：M2 验收结论

你的 M2 已通过监控方实证验收。**你自己指出的验收 8 算术问题是对的，是监控方算错了** —— 已在
`contracts/CONTRACT.md` §15.2 记录在案，验收口径修正为 `purged = max(0, N-1-10)`（12→1、13→2）。

完整裁定见 **`contracts/CONTRACT.md` §15**，请先完整读一遍。摘要：

| 项 | 结论 |
| --- | --- |
| 接口面 | 41 路径 / **50 操作**，与冻结清单一致 ✅ |
| 测试 | **304 passed**，覆盖率 **89%**（`approval_service` 99%、`counter_service` 98%） ✅ |
| zip 路径穿越 | 422 + `details.entry` 指出具体条目 ✅ |
| zip 压缩炸弹 | 422，`stage: "declared"` —— **用中央目录声明尺寸预检，0 秒、磁盘零消耗**，比"解压中途中断"更强 ✅ |
| 版本淘汰 | 12 → 1 approved + 10 superseded + 1 purged；文件物理删除；purged 下载 404 ✅ |
| 并发审批 | 带与不带 `expected_version_seq` 均为一 200 + 一 409，**CAS 独立生效** ✅ |
| 下载票据 / Range / 404 语义 | 全部正确 ✅ |
| `disable_existing_loggers=False` | **高质量修复**，这类非显性缺陷能被发现并加回归测试，排查深度到位 ✅ |

### 三件必须做的事（M3 的硬性条目）

**① `file_tree_truncated` 必须改为持久化字段 —— 你的「保持现状」建议被否决。**

监控方实测发现该推断**在所有小包上误报**：种子中 3 个条目的 skill 工具返回 `true`。
根因是分子分母口径不一致 —— `file_count` 统计的是「截断后树中不含 `SKILL.md` 的条目数」，
而 `len(file_tree)` **含** `SKILL.md`，故 `file_count < len(tree)` 在只有一个 `SKILL.md` 时恒为真。

**做**：新增 `tool_versions.skill_tree_truncated` 布尔列（**要写 Alembic 迁移**），
在解析时（真实总数已知）计算并持久化。两个上限的分工已写入 `docs/03` §3.5：
上传校验 5000 条目 / 文件树存储 2000 条目。补上 2000/2001 边界的回归测试。

**② `GET /api/v1/admin/approvals` 需要暴露 `version_seq`。**

`docs/03` §3.9 要求批准时可传 `expected_version_seq = tools.version_seq`，但审批队列不返回它，
而审批人恰恰是唯一需要它的人（`MyToolListItem` 有，但那是 owner 视角）。CAS 已保证并发正确，
所以这不是功能缺陷，而是「文档承诺了却拿不到的值」。加一个附加字段即可。

**③ `/me/tools/{id}` 的 approver 访问必须排除 `draft`。**

草稿是用户尚未提交的未完成工作，没有任何审批理由需要看到它，这是真实的越权面。

---

## 边界（硬性）

你仍然独占并只允许修改：`backend/`、`deploy/`、`scripts/`

**绝对不可修改**：`docs/`、`contracts/`、`README.md`、`web/`。前端 agent 正在并行开发 M2，
动 `web/` 会直接冲突。

不执行任何 git 命令。工作目录：`/Users/jlthzy/Documents/selftool`

**注意**：`docs/01` §4.3 第 1 步与 FR-APPR-06 自相矛盾（前者只给 owner 与 superadmin 可见待审工具，
后者要求审批人能预览）。监控方会在本轮结束前修订 `docs/01`。**你按裁定 ③ 的最终形态实现**：
`GET /api/v1/tools/{slug}` 对 approver 开放 `pending` / `pending_update` / `offline` 状态。

---

## 第一步：先读这些

1. `contracts/CONTRACT.md` —— **§15 全部裁定**（M2 验收结论与本轮硬性条目）、§14（前端侧裁定，了解全貌）
2. `docs/01-需求规格说明书.md` —— §3.1/§3.2 角色权限矩阵、5.2（用户管理）、5.3（用户组）、
   5.4（分类标签）、5.13（管理控制台）、5.14（**管理 API 与 Token，逐条对照**）、5.15（统计）
3. `docs/03-API接口清单.md` —— §2.5 管理后台接口总表、§3.12 签发 Token、§3.13 设置、
   §3.14 批量导入、§5 脚本示例、§4 错误码
4. `docs/02-数据模型设计.md` —— §3.15 `api_tokens`（**注意最后那段关于 `last_used_at` 的性能警告**）、
   §3.6/§3.7 分类与标签、§3.4/§3.5 用户组、§5 清理任务
5. `docs/05-部署与运维方案.md` §13.9 —— API Token 的存储与轮换要求
6. 你自己的 M1/M2 代码。特别是 `app/core/deps.py`（Principal 与 scope 校验的骨架）、
   `app/services/counter_service.py`（**内存聚合框架，M3 的 `last_used_at` 直接复用**）、
   `app/services/approval_service.py`（**CAS 模式，M3 的并发操作复用**）

---

## M3 目标

补齐**管理面与脚本化能力**，让平台可以被运维和自动化接管。

一句话验收：管理员能在界面上管用户/组/分类/标签、签发 API Token、批量导入导出；
一个只有 `approvals:write` scope 的 Token 能通过 curl 完成审批，但调用户管理接口会 403。

---

## 交付清单

### 0. 三件硬性条目（优先做，见上方摘要）

- `tool_versions.skill_tree_truncated` 布尔列 + 迁移 + 边界测试
- 审批队列条目增加 `version_seq`
- `/me/tools/{id}` 的 approver 访问排除 `draft`
- 附带：`GET /api/v1/tools/{slug}` 对 approver 开放 `pending` / `pending_update` / `offline`

### 1. API Token 机制（M3 的核心）

按 `docs/01` FR-API-01~05 与 `docs/03` §3.12：

- Token 格式 `st_` + 43 位 base64url（256 bit 熵）
- **只存 SHA256 与可读前缀**，明文只在签发响应中出现一次
- **存 SHA256 而非 Argon2 是刻意的**：Token 是高熵随机串，不存在暴力破解可能，
  不需要慢哈希；而鉴权是每请求都要做的事，慢哈希会直接打死吞吐。
  这是「高熵凭证用快哈希、低熵密码用慢哈希」的标准做法
- Scope：`tools:read` / `tools:write` / `approvals:write` / `users:write` / `groups:write` /
  `taxonomy:write` / `settings:write` / `admin:all`
- **Scope 必须与创建者角色权限取交集并实时求值**，不能在签发时快照。
  创建者被降级或禁用后，其 Token 应立即降权或失效
- `last_used_at` / `last_used_ip` 的更新**必须走内存聚合批量落库**（复用 `counter_service`），
  **禁止逐请求 UPDATE** —— SQLite 单写者，逐请求写会直接把写锁打死。这是 M1 就定下的硬约束
- 鉴权路径：`get_principal()` 按 `st_` 前缀分流，与 JWT 走同一套 `require_role` / `require_scope`

### 2. 用户与角色管理

- 用户 CRUD（`docs/03` §2.5）：列表（搜索/角色筛选/状态筛选/分页）、创建、详情、编辑、重置密码、
  全量替换角色、强制下线
- **不得物理删除用户**（FR-IAM-06），只能禁用 —— 用户与工具、审批记录、下载记录有外键关联
- **必须始终保留至少一个启用的 `superadmin`**，违反返回 `409 LAST_SUPERADMIN`（FR-IAM-07）
- **不得禁用或降级自己**，防止自我锁死（FR-IAM-08）
- 禁用用户后**立即吊销其全部 refresh token 与其签发的所有 API Token 的可用性**（FR-IAM-05）
- `GET /admin/roles` 返回四个内置角色

### 3. 用户组管理

- 组 CRUD + 成员管理（列表、批量添加、移除）
- **删除组前必须检查 ACL 引用**，被引用时返回 `409 GROUP_IN_USE` 并列出引用它的工具（FR-GRP-04）
- 删除组时在同一事务内清理 `tool_acl` 中 `subject_type='group'` 的悬空条目
- 成员变更**立即生效**（权限每次请求实时判定，不做 token 内嵌缓存 —— FR-GRP-06）

### 4. 分类与标签管理

- 分类 CRUD + 排序调整（`PUT /admin/categories/order` 批量提交）
- **分类禁止物理删除**：有工具引用时返回 `409 CATEGORY_IN_USE` 并给出引用数（FR-TAX-02）；
  无引用时软删除（`is_active=false`）
- 标签：列表、重命名、**合并**（引用关系转移，返回转移数量）、**清理零引用**
- 标签计数 `usage_count` 由定时任务重算，不在增删关联时维护（`docs/02` §3.7 已说明理由：并发下会漂移）

### 5. 全站工具管理与回收站

- `GET /admin/tools`：全站工具列表（含所有状态、所有作者），支持状态/类型/分类/作者/可见性/时间范围筛选
- `POST /admin/tools` 与 `POST /admin/tools/{id}/versions`：**管理侧代创建**，允许指定 `owner_id`，
  供脚本批量导入用（与 `/me/tools` 并存的另一组路径，见 `docs/03` §5.2）
- `POST /admin/tools/{id}/transfer`：转移负责人（FR-IAM-11）
  - **必须同时重算原负责人与新负责人的存储配额占用**
  - 写入 `approval_records`，`action = transfer_owner`
- 软删除、回收站列表、还原、彻底清除（清除时物理删除文件）
- 回收站保留 30 天后由清理任务自动彻底清除（`docs/02` §5）

### 6. 统计与概览

- `GET /admin/overview`：待审数、工具数（按状态分布）、用户数、存储用量 —— **纯数字，不做图表**
  （`docs/04` §6.9 与 `docs/01` 第 10 章都明确不做可视化大屏）
- `GET /admin/stats/tools`：工具排行（下载量 Top N）
- `GET /admin/stats/storage`：存储占用明细
- 计数的落库框架 M2 已有（`counter_service`），本轮只加读取侧的聚合查询

### 7. 批量导入导出

按 `docs/03` §3.14：

- `POST /admin/import/users`：CSV 上传，支持 `dry_run` 与 `on_conflict`（`skip`/`update`/`fail`）
- `POST /admin/import/tools`：JSON 上传
- `GET /admin/export/users`、`GET /admin/export/tools`
- 统一返回 `{succeeded, failed, skipped, errors: [{row, field, value, message}], generated_passwords}`
- **部分失败不影响成功行**，但错误报告必须精确到行与字段
- **`generated_passwords` 是明文回显的唯一例外**（密码留空时系统生成，管理员需告知用户）：
  **该响应绝不能写入日志**，且 `dry_run=true` 时不生成密码
- **导出 CSV 必须带 UTF-8 BOM**，否则 Excel 打开中文是乱码 —— 内网用户大量用 Excel
- **导出绝不含密码哈希**

### 8. 系统设置（补全）

M2 只做了审批分组，本轮补全 `docs/02` §3.19 的**全部设置项**：

- `GET /admin/settings` 必须下发 `value_type` / `is_public` / `options` / `min` / `max`，
  前端据此渲染控件，**不要让前端硬编码设置项元信息**
- `PUT /admin/settings` 批量部分更新，**任何一项非法则整体回滚**，返回 `400 SETTING_INVALID` 并指出 key
- 设置变更**立即生效**，无需重启

### 9. CLI 补全

在既有 CLI 上增加（`docs/03` FR-API-14）：

| 命令 | 作用 |
| --- | --- |
| `import-users <csv>` | 离线批量导入，输出逐行报告 |
| `export-users <csv>` | 离线导出 |
| `create-token --name X --scopes ...` | 离线签发 Token（服务器上应急用） |
| `purge-recycle-bin` | 手动清理回收站 |
| `gc-versions` | 清理超限历史版本（`docs/05` 的 timer 依赖它） |
| `reindex-search` | 已有，本轮确认仍可用 |

所有涉及口令的命令必须支持 `--password-stdin`，避免进 shell 历史。

### 10. 守卫测试更新（必须做）

- 把 `M3_FORBIDDEN_PREFIXES` 换成 **M3 冻结清单**：M1 的 12 + M2 的 38 + M3 新增的 **42**，
  总计应为 **92** 个操作
- 反向断言：**多出任何接口都要失败**（`docs/03` §2.5 未列出的路径不许出现）

**M3 新增的 42 个接口**：

```
【用户与角色 8】GET/POST /admin/users；GET/PATCH /admin/users/{user_id}；
                POST /admin/users/{user_id}/reset-password；
                PUT /admin/users/{user_id}/roles；
                POST /admin/users/{user_id}/revoke-sessions；GET /admin/roles
【用户组  7】   GET/POST /admin/groups；PATCH/DELETE /admin/groups/{group_id}；
                GET/POST /admin/groups/{group_id}/members；
                DELETE /admin/groups/{group_id}/members/{user_id}
【分类标签 9】  GET/POST /admin/categories；PATCH/DELETE /admin/categories/{category_id}；
                PUT /admin/categories/order；GET /admin/tags；PATCH /admin/tags/{tag_id}；
                POST /admin/tags/merge；POST /admin/tags/cleanup
【工具与回收站 7】GET/POST /admin/tools；POST /admin/tools/{tool_id}/versions；
                POST /admin/tools/{tool_id}/transfer；POST /admin/tools/{tool_id}/restore；
                DELETE /admin/tools/{tool_id}/purge；GET /admin/recycle-bin
【API Token 4】 GET/POST /admin/tokens；POST /admin/tokens/{token_id}/revoke；
                DELETE /admin/tokens/{token_id}
【统计 3】      GET /admin/overview；GET /admin/stats/tools；GET /admin/stats/storage
【导入导出 4】  POST /admin/import/users；GET /admin/export/users；
                POST /admin/import/tools；GET /admin/export/tools
```

保留并继续通过：公开端点反向断言、强制改密覆盖、SPA 回退唯一性、遍历器有效性自检。

### 11. 测试

在 M2 的 304 个测试基础上新增，重点覆盖：

- **Token Scope 矩阵**：8 个 scope × 各类接口的允许/拒绝组合；`admin:all` 隐含全部
- **Scope 实时求值**：用 Token 能访问 → 把创建者降级 → 同一 Token 立即失效或降权
- **`last_used_at` 走聚合**：并发 100 次带 Token 的请求后，断言数据库写入次数远小于 100
  （用一个计数钩子断言，这是硬约束，必须有证据）
- `LAST_SUPERADMIN` / 不能禁用自己 / 不能物理删除用户
- 删除组时的 `GROUP_IN_USE` 检查 + 悬空 ACL 清理
- 分类删除的 `CATEGORY_IN_USE`
- 标签合并的引用转移数量
- 导入的 `dry_run` / `on_conflict` 三种策略 / 部分失败报告 / `generated_passwords` 不落日志
- **CSV 导出的 BOM 字节断言**（前 3 字节为 `EF BB BF`）
- 负责人转移后的**配额重算**与审批留痕
- `file_tree_truncated` 的 2000/2001 边界（新列）
- `skill_tree_truncated` 迁移可正向执行也可回滚

覆盖率目标：整体 ≥ 85%，`services/` 下新增模块 ≥ 90%。

---

## M3 验收清单（监控方会逐条实证）

| # | 场景 | 期望 |
| --- | --- | --- |
| 1 | 3 个条目的 skill 包 | `file_tree_truncated` 为 **`false`**（当前是误报 `true`） |
| 2 | 2100 个条目的 skill 包 | `file_tree_truncated` 为 `true`，`file_tree` 恰好 2000 条 |
| 3 | 审批队列条目 | 含 `version_seq`，可用于乐观锁 |
| 4 | approver 访问他人 `draft` 工具的 `/me/tools/{id}` | 404（当前可见，属越权） |
| 5 | approver 访问他人 `pending` 工具的 `GET /tools/{slug}` | 200（当前 404） |
| 6 | 签发 scope=`approvals:write` 的 Token，批准待审工具 | 成功 |
| 7 | 同一 Token 调 `POST /admin/users` | `403 SCOPE_MISSING`，`details.missing` 指出缺哪个 scope |
| 8 | 把 Token 创建者从 superadmin 降级 | 同一 Token 立即失效或降权（**不是等 token 过期**） |
| 9 | 吊销 Token 后再调用 | `401 TOKEN_REVOKED` |
| 10 | 100 次并发带 Token 的请求 | 数据库 `UPDATE api_tokens` 次数远小于 100（聚合生效） |
| 11 | 禁用用户 | 其 refresh token 与该用户签发的 Token 立即失效 |
| 12 | 禁用最后一个 superadmin | `409 LAST_SUPERADMIN` |
| 13 | 禁用自己 | 被拒 |
| 14 | 物理删除用户 | 接口不存在（只有禁用） |
| 15 | 删除被 ACL 引用的用户组 | `409 GROUP_IN_USE` + 引用工具列表；确认后悬空 ACL 被清理 |
| 16 | 删除有工具引用的分类 | `409 CATEGORY_IN_USE` + 引用数 |
| 17 | 合并两个标签 | 引用关系转移，返回转移数量，旧标签消失 |
| 18 | 导入 CSV（`dry_run=true`） | 不写库、不生成密码，只返回预演报告 |
| 19 | 导入 CSV 含 2 行非法 | `succeeded`/`failed` 计数准确，`errors` 精确到行与字段 |
| 20 | 导出用户 CSV | 前 3 字节为 `EF BB BF`；不含密码哈希 |
| 21 | 转移工具负责人 | 双方配额重算正确；审批历史有 `transfer_owner` 记录 |
| 22 | 收回站还原与彻底清除 | 还原后可见；彻底清除后文件从磁盘消失 |
| 23 | `PUT /admin/settings` 含一个非法值 | 整体回滚，`400 SETTING_INVALID` 指出 key，其余项未被修改 |
| 24 | 守卫测试 | 接口操作总数恰好 **92**，多一个就失败 |

---

## 报告要求

按 `contracts/CONTRACT.md` §10 格式回报。额外要求：

- **验收 1、2、4、5、8、10、20、24 必须贴具体证据**（响应片段、字节 dump、SQL 写入次数、
  守卫测试的实际计数）
- 任何新增依赖必须列出并说明用途
- `skill_tree_truncated` 是**新增数据库迁移**，必须说明 `downgrade()` 的实现与风险
- 任何契约偏差或待裁决显式列出。`docs/03` §2.5 是 M3 的接口边界，超出必须先报告

---

## 易错点清单

**性能类（SQLite 单写者，最容易踩）**

1. **`api_tokens.last_used_at` 禁止逐请求 UPDATE**。必须走内存聚合批量落库，复用 `counter_service`
2. **导入大批量用户时不要一行一个事务**。要么整批一个事务，要么分批提交，避免长事务锁库
3. **导出大批量数据不要一次性全载入内存**。用流式响应
4. `GET /admin/tools` 的筛选组合多，注意别让 `EXPLAIN QUERY PLAN` 退化成全表扫描

**权限类**

5. **Token 的 Scope 必须与创建者**当前**角色取交集**，不能签发时快照 —— 否则降级不生效
6. **`admin:all` 隐含全部**，但不要让它在交集运算里被误当成"无限制"而绕过创建者角色
7. **禁用用户要连带吊销其 Token**，不只是 refresh token
8. **`LAST_SUPERADMIN` 检查要考虑并发**：两个管理员同时降级最后两个超管，不能都成功
   （用与审批相同的 CAS 思路）

**数据类**

9. **用户不可物理删除**，与 `tools.owner_id` / `approval_records.actor_id` / `download_logs.user_id`
   有外键关联
10. **分类是软删除**，硬删会让 `tools.category_id` 悬空
11. **删除组必须清理悬空 ACL**，`tool_acl.subject_id` 是多态外键，数据库不管
12. **负责人转移要重算配额**，否则两个用户的用量都会算错

**格式类**

13. **导出 CSV 必须带 UTF-8 BOM**，否则 Excel 中文乱码 —— 内网用户大量用 Excel，这条很实际
14. **`generated_passwords` 响应绝不能写日志**，那是唯一的明文回显例外
15. **导入的 `errors` 要精确到行与字段**，不能只给一句"导入失败"

---

## 不要做的事

- 不实现 M4 的内容（双架构离线打包实测、用户/管理员/运维手册、完整的部署演练）——
  M4 是打磨与交付，不新增接口
- 不写前端代码，不改 `web/`
- 不重构 `docs/` 或 `contracts/`。`docs/01` §4.3 的矛盾由监控方修订，你按 §15.5 的最终形态实现
- 不引入 Celery / Redis / MQ 之类外部依赖。后台任务用 asyncio 任务或 APScheduler
- 不执行 git 命令
- **不做数据可视化图表**（`docs/01` 第 10 章第 2 条明确不做），概览页只给数字

有阻塞或需要裁决时，立刻按 §10 格式报告，不要自己猜着往下做。
