# 后端开发 Agent — 任务书（M2）

## 先看这一节：M1 验收结论

你的 M1 已由监控方实证验收通过，checkpoint 提交 `a8f476b`（95 个文件）。

**通过项**：12 个冻结接口无越界、179 测试全过、覆盖率 84%（`auth_service` 94%）、ruff 全过、venv 为真实 Python 3.11.16、四个 PRAGMA 全部正确、`seed-demo` 幂等、账号锁定/强制改密/会话吊销/防用户名枚举均实证通过。守卫测试质量被特别认可 —— 尤其是先断言遍历器有效这一点。

**一处裁定，请不要"修"它**：`GET /api/v1/tools` 在 `page != 1` 时返回 `"facets": null`（键存在、值为 null），而不是省略整个键。

你的服务层实现是正确的（`if page == 1` 才计算聚合）。原先 `docs/03` §3.3 写「翻页时省略键」，与 §1.1「可空字段显式返回 null，不省略字段」自相矛盾。监控方裁定以 §1.1 为准，**已改文档，代码不动**。前端会按 `facets: ToolFacets | null` 写类型。

**一处必须改**：`tests/test_guard.py` 里的 `test_no_admin_or_write_endpoints_in_m1` 和 `test_m1_surface_is_exactly_the_frozen_list` 在 M2 会**故意失败**——这是设计如此，它们是契约变更的闸门。M2 要把它们替换成 M2 版本的边界断言（见下文"守卫测试"）。

---

## 你的身份与边界

你仍然独占并只允许修改：`backend/`、`deploy/`、`scripts/`。

**绝对不可修改**：`docs/`、`contracts/`、`README.md`、`web/`。前端 agent 正在并行开发 M1/M2，动 `web/` 会直接冲突。

**不要执行任何 git 命令**。版本控制由监控方在 checkpoint 统一处理。

工作目录：`/Users/jlthzy/Documents/selftool`

---

## 第一步：先读这些

1. **`contracts/CONTRACT.md`** —— 重点看新增的 **§6.1 M2 冻结接口清单**。这是 M2 的接口边界，38 个新接口，实现后总数为 50。
2. `docs/01-需求规格说明书.md` —— **§4.1 工具状态机**（完整迁移表）、**§4.2 版本状态机**、**§4.3 可见性判定算法**、5.5~5.10（工具/版本/文件/Skill/ACL/审批的需求条目，带编号，逐条对照实现）。
3. `docs/03-API接口清单.md` —— §3.4 详情、§3.5 skill-preview、**§3.7 上传新版本（含 12 步处理顺序，这是硬要求）**、§3.9 批准、**§3.10 驳回（注意语义陷阱）**、§3.11 ACL、§3.13 设置、§3.15 下载票据。
4. `docs/02-数据模型设计.md` —— §3.8 `tools`、§3.11 `tool_versions`、§3.12 `tool_images`、§3.13 `approval_records`、§3.15 `api_tokens`（M2 用不到但字段已建）、§5 数据保留与清理任务。
5. `docs/05-部署与运维方案.md` §13.5 —— zip 压缩炸弹与路径穿越的防护要求。
6. 你自己的 M1 代码。特别是 `app/repositories/tools.py`（可见性判定已下推到 SQL，M2 的详情页要复用）、`app/core/deps.py`、`app/models/enums.py`。

---

## M2 目标

**把平台从"能登录能看列表"变成"能上传、能审批、能下载"的完整闭环。**

一句话验收：一个普通用户能创建 4 种类型中任意一种工具、上传文件、提交审批；审批管理员能看包内容、批准或驳回；批准后门户可见、能下载、SHA256 对得上；发新版本不中断旧版本的服务；超过 10 个历史版本时最旧的被归档。

---

## 交付清单

### 1. 存储层（`app/storage/`）

M1 已建目录但未实现，现在补完。

- 抽象 `StorageBackend` 接口 + `LocalStorage` 实现（为将来换对象存储留口）
- 路径规则：`files/tools/{tool_id}/{version_id}/{safe_name}`，图片 `files/images/{tool_id}/{uuid}.{ext}`
- **文件名消毒**：剥离 `/`、`\`、`..`、空字节、控制字符、前后空白；原始文件名**单独存库**用于 `Content-Disposition`，**绝不用于磁盘路径**
- **流式写入 + 增量 SHA256**：禁止把整个文件读进内存。分块（建议 1 MiB）读、边写边更新 hasher
- **大小限制必须在流式过程中强制**：`Content-Length` 可伪造，必须累计实际字节数，超限立即中断并删除临时文件。**不能**等上传完再判断——那时攻击者已经吃掉了磁盘
- **临时文件 + 原子 rename**：先写 `{DATA_DIR}/tmp/{uuid}.part`，全部校验通过后 `os.replace()` 到最终位置（同文件系统内原子）
- 任何失败路径都要清理临时文件与已落盘的最终文件

### 2. Skill 包解析（`app/services/skill_service.py`）

这是本平台**最大的攻击面**，必须严格实现。

- 在**临时目录**解压，解析完即删，**不持久化**解压内容（原 zip 是唯一持久化产物）
- `SKILL.md` 查找：根目录，或单层子目录下
- 提取 YAML frontmatter → `skill_manifest`，正文 → `skill_readme_md`（超 256 KB 截断）
- 生成文件树 → `skill_file_tree`（`[{path, size, is_dir, sha256}]`）
- **防护（缺一不可）**：
  - 解压后总体积 ≤ 1 GB（可配）
  - 条目数 ≤ 5000
  - 嵌套深度 ≤ 16
  - 单个条目解压后 ≤ 200 MB
  - 拒绝绝对路径、含 `..` 的路径、符号链接
  - **必须边解压边校验**，不能先全解开再检查——那样炸弹已经炸了
  - 用 `zipfile.ZipInfo.file_size` 预检 + 实际读取时二次校验（前者可被伪造）
- 错误必须**精确到条目**，例如「条目 `../../etc/passwd` 包含非法路径」。错误码见 `docs/03` §4
- 解析失败允许**以警告保存为草稿**，但**不允许提交审批**（FR-TOOL-06）
- 缺 `SKILL.md` 时返回 `SKILL_MD_NOT_FOUND`，附 `{searched: [...]}`

### 3. 工具 CRUD 与状态机

- 四种类型：`file` / `webapp` / `skill` / `prompt`，各自的内容要求见 FR-TOOL-04~07
- `slug` 生成：由名称转写，冲突追加 4 位短哈希
- **状态机严格按 `docs/01` §4.1 的迁移表实现**，非法迁移返回 `409 STATE_CONFLICT`
- `pending` 状态下不可编辑内容（FR-TOOL-11），返回 `409 TOOL_NOT_EDITABLE`
- 元信息变更（简介/详情/可见性/标签）**不触发重新审批**（FR-TOOL-13）
- 软删除：`deleted_at`，不物理删除
- 详情 Markdown 由后端消毒后返回 `description_html`（`markdown-it`/`mistune` + `bleach`，白名单同前端）

### 4. 版本管理（最容易做错的部分）

- `(tool_id, version)` 唯一，重复返回 `409 VERSION_EXISTS`
- **新版本提交 → 工具状态 `pending_update`，当前版本继续对外服务**（FR-VER-03）。这是核心体验，别做成一提交就下线
- **批准新版本**：新版本 → `approved` 且 `is_current=true`；原当前版本 → `superseded`；更新 `tools.current_version_id` 与 `last_version_at`
- **驳回新版本**：待审版本 → `rejected`，**工具退回 `approved`**，当前版本不变（`docs/03` §3.10）
  > 这是 M2 最容易错的语义：驳回一个「新版本」不等于驳回工具本身。实现时必须先判断 `submission_type`。
- **历史版本淘汰**：`superseded` 按 `created_at` 倒序，只保留最近 10 个（`version.history_limit`）
  - 超出部分：**物理删除存储文件**，数据库行标记 `purged`（**保留行**，因为 `approval_records` 与 `download_logs` 引用了它）
  - `purged` 版本出现在版本列表里但不可下载，界面显示「已归档」
  - 淘汰在**审批通过的事务提交之后**触发文件删除；文件删除失败只记警告，由孤儿清理兜底
- 维护 `is_current` 与 `tools.current_version_id` 的一致性 —— 数据库已有部分唯一索引兜底，但你的代码不能依赖报错来发现问题
- 已发布的当前版本不可删除（FR-VER-09）；未过审版本可删（FR-VER-10）
- 只可编辑未过审版本的 `changelog_md`；已过审版本的内容不可改，只能发新版本（FR-VER-11）

### 5. 可见性三级 + ACL

- 判定算法严格按 `docs/01` §4.3，**必须下推到 SQL**，复用 M1 已有的实现方式
- `PUT /me/tools/{id}/acl` 是**全量替换**语义
- `restricted` 时 `entries` 不能为空 → `400 ACL_REQUIRED`
- 校验主体存在且启用 → `400 SUBJECT_NOT_FOUND`；重复 → `400 DUPLICATE_ENTRY`；授权给自己 → `400 SELF_GRANT`
- **切到 `public`/`private` 时 ACL 条目保留但不生效，不删除**（`docs/03` §3.11）。这样用户在两种可见性间来回切换不会丢配置
- `viewer` 角色可看 `public` 详情但不能下载（FR-ACL-05），详情响应里 `can_download=false`
- 无权访问返回 **404 而非 403**（FR-FILE-08），避免探测资源存在性

### 6. 审批流

- 全局开关 `approval.mode`：`require` / `auto_approve_all`
- 免审白名单：命中且 `approval.whitelist_enabled=true` 时跳过审批
- 自动批准时 `actor` 记为系统，写入 `auto_approved_rule`，`approval_records.is_automatic=true`
- **切换为 `auto_approve_all` 时，已处于 `pending` 的条目不被自动放行**（FR-APPR-02），响应里用 `warnings` 告知待审数量
- 审批队列：`waiting_hours` 由服务端算好；`submission_type` 区分 `new_tool` / `new_version`
- 驳回理由**必填且 ≥5 字**；下架理由必填；批准备注选填
- **每次审批动作必须在同一个事务内完成**：改工具状态 + 改版本状态 + 写 `approval_records` + 同步搜索索引
- **并发防护**：两个管理员同时审批同一工具，后者必须拿到 `409 ALREADY_PROCESSED`。用 `expected_version_seq` 乐观锁或 `UPDATE ... WHERE status='pending'` 的影响行数判断
- `approval_records` 是**只插入、不更新、不删除**的流水表；`actor_label` 存**当时的显示名快照**（用户改名后历史仍准确）
- 支持批量批准（`batch-approve`），**不提供批量驳回**（驳回需逐条理由）

### 7. 下载（含票据与 Range）

- `GET /tools/{slug}/download` 走 ASGI `FileResponse`（sendfile 路径），支持 `Range` → `206`
- **鉴权有两条路径**：带 `Authorization` 头，或带有效的 `?ticket=`
- `POST /tools/{slug}/download-ticket` 签发 HMAC 签名票据：
  - 载荷 `{tool_id, version_id, user_id, exp}`，有效期 **60 秒**
  - **密钥必须是从 `SECRET_KEY` 派生的子密钥**，不要直接用 `SECRET_KEY`。派生方式如 `hmac.new(sha256(SECRET_KEY + b":download-ticket"), ...)` 或 HKDF。直接复用主密钥会扩大泄露面
  - 签名比对用 `hmac.compare_digest`（常量时间）
  - 票据**绑定 user_id**：别人的票据拿到也不能用
  - 允许在有效期内多次使用（浏览器可能发 Range 请求或重试），不强制一次性
- `Content-Disposition` 用 `filename*=UTF-8''<percent-encoded>` 支持中文文件名
- 下载前校验可见性 + 状态 + 版本未 `purged`
- 未授权/不存在一律 **404 + JSON 错误体**

### 8. 图片处理

- 封面 1 张、截图最多 8 张（可配）；png/jpg/jpeg/webp/gif；单张 ≤ 5 MB
- 用 Pillow 生成 **480px 宽缩略图**用于门户卡片，列表接口只给 `thumb_url`
- 校验真实图片格式（不只看扩展名），用 `PIL.Image.open` + `verify()`
- `GET /api/v1/images/{id}` 也要做可见性校验后输出，**不是静态文件直出**

### 9. 计数器聚合（必须做对，否则 M3 要返工）

下载量、浏览量、`api_tokens.last_used_at` **禁止逐请求 UPDATE**（README「SQLite 风险说明」第 4 条）。

- 内存计数器 + 定时 flush（建议 30 秒）+ 关闭时 flush
- 浏览量去重：同一用户对同一工具 1 小时内只计一次（`stats.view_dedup_minutes`）
- 每日汇总 UPSERT 到 `tool_stats_daily`，SQLite 与 PG 都支持 `ON CONFLICT ... DO UPDATE`，可复用同一段 SQL
- `download_logs` 用**内存队列批量插入**（每 100 条或 10 秒）
- **落库失败不得影响用户请求**：下载/浏览必须成功返回，失败只记日志
- 服务优雅关闭时（`SIGTERM`）要 flush，不能丢计数

### 10. 设置读写

- `GET /admin/settings` 返回全部设置项，含 `value_type` / `is_public` / `options` / `min` / `max`（前端靠这些渲染控件，不要让前端硬编码设置项元信息）
- `PUT /admin/settings` 批量部分更新，**任何一项非法则整体回滚**，返回 `400 SETTING_INVALID` 并指出 key
- 修改要记录 `updated_by_id` / `updated_at`
- 设置变更**立即生效**，不需要重启

### 11. 守卫测试更新（必须做）

把 `tests/test_guard.py` 更新到 M2：

- `M1_ENDPOINTS`（12 个）→ **50 个的 M2 冻结清单**，逐条对照 `contracts/CONTRACT.md` §6 + §6.1
- `test_no_admin_or_write_endpoints_in_m1` 会被 M2 合法地打破，**替换**为 `test_no_m3_endpoints_in_m2`：断言不存在 `/api/v1/admin/tools*`、`/admin/users*`、`/admin/groups*`、`/admin/tokens*`、`/admin/categories*`、`/admin/tags*`、`/admin/import*`、`/admin/export*`、`/admin/stats/*`、`/admin/overview`、`/admin/recycle-bin`
- 保留并继续通过：公开端点白名单反向断言、强制改密覆盖、SPA 回退唯一性、遍历器有效性自检

> 现在 `PUBLIC_ENDPOINTS` 需要扩充：`GET /api/v1/images/{image_id}` 和 `download`（凭票据）可能是公开的——请按 `docs/01` FR-ACL-06 与设置项 `portal.allow_anonymous_view` 决定，并在报告里说明你的选择。

### 12. 测试

在 M1 的 179 个测试基础上新增，重点覆盖：

- 状态机的**每一条迁移**，含全部非法迁移返回 `409`
- **驳回新版本 → 工具回到 `approved` 且当前版本不变**（这条必须有独立测试）
- 历史版本淘汰：上传 12 个版本全部批准 → 历史恰好 10 个，最旧 2 个 `purged`，**磁盘文件确实已不存在**
- **zip 路径穿越**（构造含 `../../etc/passwd` 的包）→ 拒绝且错误信息含条目名
- **zip 压缩炸弹**（构造高压缩比包）→ 拒绝且临时目录已清理
- **超限上传中途中断**：伪造 `Content-Length` 声称小、实际超大 → 被中断，磁盘无残留
- ACL：`restricted` 授权给组 → 组内可见，组外 404；移出组后立即不可见
- **并发审批** → 一个成功一个 `409 ALREADY_PROCESSED`
- 下载票据：有效可用、过期失效、篡改签名失效、**他人票据不可用**
- `Range` 请求返回 `206` 且字节区间正确
- 可见性判定下推到 SQL 的验证（断言未授权用户的列表/详情/下载三条路径都是 404）
- 计数器：并发下载后计数正确、去重生效、关闭时 flush 不丢
- 审批事务原子性：注入 mid-transaction 失败，断言工具状态与版本状态都未变

覆盖率目标：整体 ≥ 80%，`services/` 下新增模块 ≥ 90%。

---

## M2 验收清单（监控方会逐条实证）

| # | 场景 | 期望 |
| --- | --- | --- |
| 1 | 创建 `file` 工具 → 上传 zip → 提交审批 | 进入 `pending`，不出现在门户，审批队列可见 |
| 2 | 审批管理员查看该工具 | 能预览包内容、文件信息、Markdown |
| 3 | 驳回并填理由 | 用户端可见理由与状态；门户不可见 |
| 4 | 修改后重新提交 → 批准 | 门户可见；下载成功；**下载文件 SHA256 与上传时一致** |
| 5 | 上传新版本 1.1.0 并提交 | 工具**仍在门户可见**；下载的仍是 1.0.0；owner 看到「新版待审」 |
| 6 | 批准新版本 | 下载变 1.1.0；1.0.0 进入历史且仍可下载 |
| 7 | 驳回新版本 | 工具**仍为 `approved`**；当前版本不变；被驳回版本显示理由 |
| 8 | 连续上传 12 个版本并全部批准 | 历史恰好 10 个；最旧 2 个标记 `purged`；**磁盘上对应文件已消失** |
| 9 | 上传含 `../../etc/passwd` 的 zip | 拒绝，错误指出具体条目 |
| 10 | 上传 10 MB 但解压后 10 GB 的 zip | 拒绝，**解压中途中断**，临时目录已清理 |
| 11 | `skill` 类型且含 SKILL.md | skill-preview 返回 manifest + readme + 完整文件树 |
| 12 | `webapp` / `prompt` 类型 | 详情分别返回 `webapp_url` / `prompt.content` |
| 13 | `restricted` + 授权给组 G | G 成员可见可下载；非成员列表与详情均 404 |
| 14 | 超管切换 `auto_approve_all` | 新提交立即发布，`approval_records.actor_label` 为系统，`is_automatic=true` |
| 15 | 用户加入免审白名单（`require` 模式下） | 该用户提交直接发布 |
| 16 | `viewer` 角色看 `public` 详情 | 可见详情，`can_download=false`，下载接口返回 404 或 403 |
| 17 | 下载票据 | 不带 Authorization 头也能下载；60 秒后失效；他人票据不可用 |
| 18 | `Range: bytes=0-1023` | 返回 206 且内容正确 |
| 19 | 两个管理员同时批准同一工具 | 一个成功，另一个 `409 ALREADY_PROCESSED` |
| 20 | 下架已发布工具（填理由） | 门户立即不可见；owner 看到理由；审批历史有记录 |
| 21 | 并发 20 次下载 | 计数正确落库，服务日志无 `database is locked` |
| 22 | 杀掉服务（SIGTERM）再启动 | 未 flush 的计数已落库，无丢失 |

---

## 报告要求

按 `contracts/CONTRACT.md` §10 格式回报。额外要求：

- `执行的验证` 必须贴真实命令与输出摘要。**验收 8、10、19、21 这四条一定要贴证据**（文件是否真被删除、临时目录是否真被清理、409 的实际响应、并发下的日志）
- 任何新增依赖（Pillow、mistune/bleach 等）**必须列出并说明用途**
- 任何契约偏差或待裁决显式列出。M2 涉及 38 个新接口，接口形状对不上的代价比 M1 大得多
- 如果发现 `docs/01` §4.1 状态机表或 `docs/03` 的某个响应示例有遗漏或矛盾，**报告它**，不要自己拍板

---

## 易错点清单

**语义类（最容易错）**

1. **驳回新版本 = 工具退回 `approved`，不是 `rejected`**。必须先判断 `submission_type`
2. **新版本待审期间旧版本继续服务**。做成一提交就下线，用户体验直接崩
3. **淘汰版本保留数据库行、只删文件**。删行会打断 `approval_records` 与 `download_logs` 的引用
4. **切走 `restricted` 时 ACL 保留不删**，否则用户来回切换会丢配置
5. **`auto_approve_all` 切换不追溯已 pending 的条目**

**安全类**

6. **zip 炸弹必须边解压边校验**。先解开再检查等于没防
7. **上传大小限制必须在流式过程中强制**。`Content-Length` 可伪造，等收完再判断时磁盘已经满了
8. **下载票据要用派生密钥**，不要直接复用 `SECRET_KEY`；比对用 `compare_digest`
9. **票据要绑 user_id**，否则票据泄露即横向越权
10. **文件名消毒**：展示名与磁盘路径必须分开。直接用上传的文件名拼路径 = 路径穿越
11. **无权访问返回 404 而非 403**，避免探测

**工程类**

12. **文件 IO 必须在数据库事务之外**。严格按 `docs/03` §3.7 的 12 步顺序
13. **计数禁止逐请求 UPDATE**，SQLite 单写者会被打死
14. **审批并发要用 CAS**，不能依赖「先查后改」
15. **`is_current` 与 `tools.current_version_id` 双写要一致**，已有部分唯一索引兜底但不能依赖它
16. **`approval_records.actor_label` 存快照**，不要 JOIN 用户表取当前名
17. **守卫测试的清单要更新到 50 个**，否则第一条测试就挂
18. **服务关闭要 flush 计数器**，否则 SIGTERM 丢数据

---

## 不要做的事

- 不要实现 M3 的接口（`/admin/tools*`、`/admin/users*`、`/admin/groups*`、`/admin/categories*`、`/admin/tags*`、`/admin/tokens*`、`/admin/import|export*`、`/admin/stats/*`、`/admin/overview`、`/admin/recycle-bin`）
- 不要写前端代码，不要改 `web/`
- 不要"顺手修" `facets` 的 null 语义 —— 那是监控方的裁定结果
- 不要重构 `docs/` 或 `contracts/`
- 不要引入 MQ / Celery / Redis 之类的外部依赖。后台任务用 `asyncio` 任务或 APScheduler 即可
- 不要执行 git 命令
- 不要为了"看起来完整"而实现 `webapp` 的定时探活 —— `docs/01` 第 10 章第 8 条明确列为不做（数据模型已留字段，二期再说）

有阻塞或需要裁决时，立刻按 §10 格式报告，不要自己猜着往下做。
