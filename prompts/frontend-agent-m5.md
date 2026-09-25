# 前端开发 Agent — 任务书（M5 · 冻结前最后一轮）

## 前置条件（**先确认再动手**）

本轮**依赖两个前置**，缺一不可：

1. **你的 M4 已交付**（flake 修复 + 连续 10 次真实 E2E 全绿 + 后端 M5 落地后的联调重跑）
2. **后端 M6 已交付**，具体需要这 9 项（见 `contracts/CONTRACT.md` §21.5）：
   - J-1 `GET /api/v1/directory`（接口面 92 → **93**）
   - J-2 ACL `can_download` 生效
   - J-3 `revoke-sessions` 一并吊销 API Token
   - J-4 `orphan_file_count` 接线
   - J-5 删除待审版本后状态回落
   - J-6 注释/docstring 同步
   - **J-7 `GROUP_IN_USE` 的 `details.tools[]` 补 `slug`**
   - **J-8 10 个端点补 `response_model` + 重新导出 `openapi.json` + 加守卫**
   - **J-9 `current_version.can_download` 与顶层一致**

**如果后端 M6 还没交付，不要开始本轮** —— 本轮的 3 个功能点全依赖它。
可以先做第 0 节的准备工作。

背景裁定见 `contracts/CONTRACT.md` **§20**，勘误见 **`docs/09-勘误与已知限制.md`**。

---

## 边界（硬性）

你仍然独占并只允许修改：`web/`

**绝对不可修改**：`backend/`、`docs/`、`contracts/`、`deploy/`、`scripts/`、`README.md`、`prompts/`

不执行任何 git 命令，含 `git status`。工作目录：`/Users/jlthzy/Documents/selftool`

---

## 交付清单（4 项）

### 0. 准备（可与 M4 并行，不依赖后端）

**0.1 跟进 `docs/09` 的勘误**

`docs/09-勘误与已知限制.md` 里有 26 条「改文档」的裁定，其中涉及前端的已在 `docs/04` 就地修订。
请核对你的实现与修订后的 `docs/04` 一致，特别是：

- §6.12 **不渲染「移入回收站」入口**（无该端点）；批量下架/上架是前端循环单条调用，要加注说明
- §6.13 **不做「最后一个超管」置灰**，仅在 `409 LAST_SUPERADMIN` 时给明确文案
- §6.21 回收站**不显示「删除人」列**（`AdminToolItem` 不返回 `deleted_by`）
- §6.4 / FR-SKILL-07 文件树**只有 `SKILL.md` 可预览**，其余条目只显示路径与大小
- §6.7 可见性文案以 `toolMeta.ts` 的「公开/指定可见/私有」为准

**0.2 `check:api-types` 的覆盖复查**（M4 已要求，此处确认）

**关于当前 `npm run verify` 是红的（重要）**：它红在两个输入不满足 ——
`backend/openapi.json` **陈旧**（缺 `storage_warning` 与 `/versions` 的 201 schema），
以及 10 个端点没有字段定义。**这两项都是后端 M6 的 J-8 要修的**（详见 `contracts/CONTRACT.md`
§22.1、§22.3）。**在你动手本轮之前，先确认 J-8 已交付。**

**绝对不要把那些端点加进 `OPERATIONS_WITHOUT_SHAPE` 白名单来「让它变绿」。**
你在 M4 报告里主动提出「不建议加白名单」，**监控方采纳了这个判断**（§22.4）：
白名单会把「缺少字段定义」从**失败**变成**静默通过**，正是 §19.4 要消除的漂移源。
**宁可红着，也不要一个看不见的盲区。**



后端 M6 新增 `GET /directory` 后，确认守卫覆盖到它；并确认对「无字段定义的响应」
是**报错而非跳过**。

### 1. ACL 编辑器：用 `/directory` 做搜索（本轮核心）

**背景**：`docs/01` FR-ACL-02（P0）要求「搜索用户/组后添加」，但此前**普通用户没有任何
可用搜索接口**（`/admin/users`、`/admin/groups` 都要超管），设置 `restricted` 可见性时
只能手填数字 ID —— 功能实际不可用。后端 M6 新增了 `GET /api/v1/directory`。

**要做的**：

- `AclEditor` 的「添加用户」/「添加用户组」改为**搜索下拉**：输入触发
  `GET /api/v1/directory?q=<输入>&type=user|group`（防抖 300ms）
- 选项展示：用户显示 `display_name`（`username` 作为次要文字）；组显示 `name` +
  `member_count`
- 选中后加入授权列表；**不再要求手填数字 ID**（移除那个输入框）
- **拿掉 M2 时代的那句「M2 无用户搜索接口」提示** —— 它现在过时了
- **同时移除 `/admin/whitelist` 添加对话框里只能填数字 `user_id` 的限制**：
  它现在可以用 `GET /admin/users`（M3 已提供，超管可用）或 `/directory` 做搜索。
  二选一，选更合适的并在报告里说明

### 2. ACL 编辑器：暴露 `can_download` 开关

> **注意与 M4 报的 F4 的关系**：你在 M4 发现「viewer 的 `current_version.can_download` 为 `true`
> 但票据接口正确拒绝」。监控方实测确认并定位为**后端缺陷**（顶层 `can_download=False` 正确，
> 只有嵌套的 `current_version.can_download` 硬编码为 `true`），已列为 M6 的 **J-9**。
> 修好后该字段就准确了 —— **前端不需要改读取逻辑**，它本来就读对了字段，
> 是后端给了错的值。J-9 落地后请把 M4 里那条 gap 注解改为正式断言。


**背景**：`tool_acl.can_download` 此前是**无效字段**（前后端都存、文档描述为生效、
但无任何读取点）。后端 M6 已实现它。

**要做的**：

- 每条授权条目增加一个「允许下载」开关（默认开）
- 关闭时语义：对方**能看到工具、能看详情**，但**不能下载**
- 需要给用户说清这个语义 —— 用 `Tooltip` 或条目内的次要文字说明，
  例如「关闭后对方可见但不可下载」
- 提交时随 `PUT /me/tools/{id}/acl` 的 `entries[].can_download` 一起发送
- 后端给的错误码（`DOWNLOAD_NOT_ALLOWED` 或等价码）要在下载失败时给出清晰文案，
  不要只弹一个笼统 toast

### 3. `pending_update` 的「撤回」改走删除版本

**背景**：目前「我的工具」对 `pending_update` 显示「撤回提交」，但 `WITHDRAWAL` 动作只含
`pending`，点击会得到 `409 STATE_CONFLICT`。真实可用路径是**删除待审版本**。

**要做的**：

- `pending_update` 状态下，操作文案改为**「撤回待审版本」**
- 走 `DELETE /me/tools/{id}/versions/{version}`，**不再调用 `withdraw`**
- 二次确认要说清后果：「撤回后该版本将被删除，工具回退到当前版本继续对外服务」
- 撤回成功后刷新：工具状态变为 `approved`（后端 M6 已保证状态回落），
  版本时间线里该版本消失
- `pending`（首次提交）状态**保持原来的 withdraw 行为**，不要一起改

### 4. 冻结前收尾

- **真实 E2E 补 3 个场景**：`/directory` 搜索可用；`can_download=false` 时下载被拒且文案清晰；
  `pending_update` 撤回后工具回落到 `approved`
- 重跑 `npm run e2e:real` **连续 10 次**（沿用 M4 的证据标准），确认新增场景不引入新的 flake
- `npm run verify` 全过；首屏 gzip < 500 KB
- mock 同步：`/directory`、`can_download` 生效、删待审版本后状态回落
- 深色主题 / 响应式 / 无障碍抽查新增的 UI（搜索下拉、开关）

---

## 验收清单（监控方会逐条核验）

| # | 场景 | 期望 |
| --- | --- | --- |
| 1 | ACL 编辑器添加用户 | 可搜索（下拉），**无需手填 ID** |
| 2 | ACL 编辑器添加用户组 | 可搜索，显示成员数 |
| 3 | 免审白名单添加 | 同样可搜索，不再要求数字 ID |
| 4 | 授权条目「允许下载」开关 | 可切换，随 ACL 一起提交 |
| 5 | `can_download=false` 的用户 | 能看详情，下载被拒且文案清晰 |
| 6 | `pending_update` 的操作项 | 文案为「撤回待审版本」，走 DELETE 版本 |
| 7 | 撤回后 | 工具回落 `approved`，门户可见，该版本消失 |
| 8 | `pending`（首次提交）的撤回 | **行为未变**（仍走 withdraw） |
| 9 | 真实 E2E 连续 10 次 | 全绿（含新增的 3 个场景） |
| 10 | `check:api-types` | 覆盖 `/directory`；无字段定义时报错不跳过 |
| 11 | `npm run verify` | 全过，dist 无 msw |
| 12 | 首屏 gzip | < 500 KB |
| 13 | mock 与真实后端 | 三处新增能力均对齐 |
| 14 | **`npm run verify` 转绿** | J-8 落地后，`check:api-types` 不再有失败项 |
| 15 | **未使用白名单** | `OPERATIONS_WITHOUT_SHAPE` 为空或不存在（§22.4） |
| 16 | 影响面工具可点击 | J-7 补 `slug` 后渲染 `Link`，点击能跳详情 |
| 17 | `current_version.can_download` | J-9 落地后，viewer 得到 `false`，M4 的 gap 注解改为正式断言 |

---

## 报告要求

按 `contracts/CONTRACT.md` §10 格式回报。额外要求：

- 验收 **9 必须贴连续 10 次的原始输出**
- 验收 **1、4、6** 要贴具体证据（截图描述或实际请求/响应片段）
- 后端 M6 若尚未交付，**明确说明哪些项无法验证**，不要用 mock 的结果冒充
- 任何新增依赖、任何你发现但未修的问题，都列出

---

## 易错点清单

1. **搜索下拉要有防抖**，输入每个字符都打接口会把 `/directory` 打爆
2. **`can_download` 的默认值必须是 `true`** —— 别把既有授权全部变成不可下载
3. **`pending_update` 与 `pending` 的撤回是两条不同路径**，别一起改成 DELETE
4. **删版本的二次确认要写清后果**（版本会被删除），这比普通确认更重
5. **移除「需要数字 ID」的提示时，别只删文案而留下那个输入框**
6. **`/directory` 只返回最小披露字段**，前端不要期待邮箱等字段存在

---

## 不要做的事

- **不新增后端接口**（接口面现在是 93 = 92 + `/directory`，见 CONTRACT §20.4③）
- 不做性能优化（§20.2 已裁定方向）
- 不写后端代码，不改 `backend/`
- 不改 `contracts/acceptance/real-backend-check.cjs`
- 不重构 `docs/`、`docs/09` 或 `contracts/`
- 不引入状态管理库、UI 组件库、CSS-in-JS、图表库
- 不执行任何 git 命令，含 `git status`
- 不做 `webapp` 探活状态点（`docs/01` 第 10 章明确不做）

有阻塞或需要裁决时，立刻按 §10 格式报告，不要自己猜着往下做。
