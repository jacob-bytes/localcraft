# 前端开发 Agent — 任务书（M2）

## 先看这一节：M1 验收结论

你的 M1 已通过监控方实证验收。**你提交的 4 处偏差与 7 条待裁决全部已被裁定**，结论写在
`contracts/CONTRACT.md` 新增的 **§14**，请先完整读一遍。这里只摘与 M2 直接相关的部分：

### 实测通过（无需动作）

- 真实前后端联调 24 项检查 **23 项通过**（唯一"失败"是监控方脚本的过滤规则，非应用问题）
- **refresh cookie 的 `Path=/api/v1/auth` 在真实浏览器下工作正常** —— F5 后会话恢复且不闪登录页
- **React 18 下无任何 ref 相关警告** —— 你的 shadcn React 19 回归修复确实生效
- `safeRedirectPath()` 实现正确；`dist/` 无 MSW 残留；首屏 gzip ≈ 225 KB（预算 500 KB）
- SPA fallback 正确：深链返回 HTML 由前端路由接管，未匹配 `/api` 返回 JSON 404

### 类型对账结果（以后端为准）

监控方逐字段 dump 了真实后端响应与你的 `types.ts` 比对。**你是"少写"而不是"编造"，这很好**：

| 接口 | 结论 | M2 动作 |
| --- | --- | --- |
| `GET /categories`、`GET /tags` | **完全一致** | 冻结，不动 |
| 旧密码错误响应 | **完全一致**（`400 VALIDATION_ERROR` + `details.fields[old_password]`） | 冻结，不动 |
| `GET /auth/me` | 后端多 3 个字段：`status`、`last_login_at`、`created_at` | **补进类型**（`status: "active" \| "disabled"`，另两个 `string \| null`） |
| `GET /auth/provider` | 后端多 2 个：`password_change_supported`、`refresh_supported` | **补进类型**（`boolean`） |
| **登出 / 改密成功** | 你假定 **204 无体**，后端实际是 **`200` + `{"status":"ok"}`** | **以后端为准**。改类型为 `Promise<{status: string}>`，`request` 不要吞掉响应体 |

### 需要你在 M2 落实的裁定

| # | 裁定 | 出处 |
| --- | --- | --- |
| 1 | **图片改为签名能力 URL**。后端会直接下发带 `?sig=` 的 `cover_url` / `thumb_url`。**前端必须原样使用后端给的 URL，不要自己拼 `/api/v1/images/{id}`**，也不要给 `<img>` 附加鉴权。保留你已有的 `onError` 占位降级 | §14.3 |
| 2 | **`Badge` 正式增加 `warning` 与 `success` 变体**，替换掉散落的显式 amber 类 | §14.7 |
| 3 | **⌘K 面板改为跳 `/tools/:slug`**（详情页在 M2 落地） | §14.10 |
| 4 | **UserMenu 启用「个人中心」「我的工具」**，「管理后台」在 M2 也启用（审批队列属于 M2） | §14.10 |
| 5 | **新增 `web/scripts/check-ui-primitives.mjs`**（防 shadcn 回归复发），并挂到 `npm run verify` | §14.9 |
| 6 | **E2E 必须增加"真实后端"运行模式**并作为 checkpoint 证据。只在 mock 下跑过的套件会漏掉真实集成问题 —— 监控方这次就是靠真实联调才发现 mock 的 cookie `Path` 与后端不一致 | §14.1 |

---

## 边界（硬性）

你仍然独占并只允许修改：`web/`

**绝对不可修改**：`backend/`、`docs/`、`contracts/`、`deploy/`、`scripts/`、`README.md`、`prompts/`。
后端 agent 正在并行开发 M2，动 `backend/` 会直接冲突。

**特别说明**：`contracts/acceptance/real-backend-check.cjs` 是**监控方的验收脚本**，不属于你，
**不要修改它**。你要自己写一套等价或更完善的真实后端 E2E。

不执行任何 git 命令。工作目录：`/Users/jlthzy/Documents/localcraft`

---

## 第一步：先读这些

1. **`contracts/CONTRACT.md`** —— 先读 **§14**（全部裁定），再读 **§6.1**（M2 冻结的 38 个新接口）。
   实现后接口面总数为 50
2. `docs/04-前端页面与交互清单.md` —— 这是你的主要规格书。M2 涉及 §6.4（工具详情）、
   §6.5（个人资料）、§6.6（我的工具）、§6.7（工具编辑器）、§6.8（版本管理）、
   §6.10（审批队列）、§6.11（审批历史）、§6.17（免审白名单）、§7（通用组件）
3. `docs/01-需求规格说明书.md` —— §3.2 权限矩阵（决定按钮显隐）、§4.1/§4.2 双状态机
   （决定状态徽标与可用操作）、5.5~5.12
4. `docs/03-API接口清单.md` —— §3.4 详情、§3.5 skill-preview、§3.6 创建工具、§3.7 上传版本、
   §3.8 审批队列、§3.9 批准、§3.10 驳回、§3.11 ACL、§3.12 下载票据、§4 错误码
5. 你自己的 M1 代码

---

## M2 目标

把平台从「能登录能看列表」补成**完整可用的产品**：用户能建工具、传文件、发版本；
审批人能看包内容、批准驳回；所有人都能进详情页、看版本、下载。

M1 的门户列表已经好了，M2 把它接上详情与下载，再补上个人中心与审批台。

---

## 交付清单

### 1. 工具详情页 `/tools/:slug`（M2 的重头戏）

按 `docs/04` §6.4 的两栏布局实现。核心是**四种类型的差异化内容区**：

| 类型 | 内容区 |
| --- | --- |
| `file` | 文件信息卡（文件名、大小、格式、SHA256 + `CopyButton`、下载按钮）。扩展名为 `.exe/.sh/.ps1/.bat/.msi` 时显示风险提示 `Alert` |
| `webapp` | 大号「打开工具」按钮（`ExternalLink` 图标，`target="_blank" rel="noopener noreferrer"`）+ URL 文本 + 复制链接 |
| `skill` | `Tabs` 三页：`SKILL.md` 渲染 / 包内文件树 / 元信息。**按需加载**（TanStack Query 的 `enabled`，切到 Tab 才请求 `skill-preview`）。文件树中仅 `.md`/文本文件可点击弹 `Dialog` 查看，二进制只显示大小 |
| `prompt` | 代码样式内容块 + 右上角「复制」+ 字符数统计 |

其他要点：

- **右侧信息栏**：版本号、SHA256、更新时间、作者、下载量、浏览量、分类、分享（复制链接）
- **版本历史**：时间线样式。当前版本用 `success` 圆点 + 「当前」徽标；`pending` 版本**仅 owner 与审批人可见**，用 `warning` 标注；`purged` 版本灰色「已归档」+ 禁用下载 + tooltip
- **权限驱动渲染**：详情响应里有 `permissions` 对象（`can_edit` / `can_download` / `can_manage_versions` / `can_view_acl`）。**直接读它，不要在前端重新实现权限判断**
- `can_download=false`（viewer 角色）时下载按钮 `disabled` + tooltip 说明原因
- **下载走票据**：调 `POST /tools/{slug}/download-ticket` 拿短时效 URL，再用隐藏 `<a>` 触发。见 `docs/03` §1.10
- 历史版本下载传 `?version_id=`
- 图片用后端下发的 `cover_url` / `thumb_url` **原样渲染**（已带签名，见裁定 1）
- 无权访问时后端返回 404，前端渲染 `NotFoundPage`

### 2. 个人中心

**`/me`（个人资料）** 按 `docs/04` §6.5：账号信息卡（头像、显示名、用户名、邮箱、角色徽标）、
存储用量卡（`StorageUsageBar`，70%/90% 阈值变色）、修改密码入口、我的下载列表。

**`/me/tools`（我的工具）** 按 `docs/04` §6.6：

- 顶部 `Tabs` 按状态分组：全部 / 草稿 / 待审 / 已发布 / 已驳回 / 已下架，各带数量徽标
- 已驳回的行下方展开 `Alert variant="destructive"` 显示驳回理由 + 「修改后重新提交」按钮
  —— **这是个人中心最高频的痛点场景，必须显眼**
- 行操作 `DropdownMenu`：编辑、版本管理、提交审批/撤回、复制链接、删除（二次确认）
- 有新版待审时额外提示

### 3. 工具编辑器 `/me/tools/new` 与 `/me/tools/:id/edit`

按 `docs/04` §6.7 的**单页分区块表单**（不做多步向导）：

- 基本信息：名称、简介（≤500 字带计数）、分类、标签（`TagInput`，≤8 个）
- 工具类型：`RadioGroup` 四选一。**切换类型时若已有内容要弹 `AlertDialog` 确认**
- 类型专属区：随类型切换（文件上传区 / URL 输入 / Skill zip 上传 / Prompt 文本域）
- 详情说明：Markdown 编辑/预览 `Tabs`。预览用**与详情页同一个 `Markdown` 组件**保证所见即所得
- 截图：拖拽上传、可排序、最多 8 张、第 1 张自动作封面
- 可见性：`RadioGroup` 三级；选「指定」时展开 `AclEditor`（搜索用户/用户组后添加，全量提交）
- **编辑模式不允许改类型**（会导致版本内容不匹配），字段只读并说明原因
- **草稿自动保存**：每 30 秒或字段失焦静默 `PATCH`，顶部显示「已保存 · 刚刚」
- 离开页面有未保存修改时用 `beforeunload` + 路由拦截确认
- **上传交互**：拖拽区、进度条 + 百分比 + 取消（`AbortController`）、成功后显示文件名/大小/SHA256 前 16 位 + 移除
- **Skill 解析失败**：`Alert variant="warning"` 显示具体错误（如「未找到 SKILL.md」或
  「条目 `../../etc/passwd` 包含非法路径」），**允许保存草稿但禁用「提交审批」**并 tooltip 说明
- 提交前 zod 全量校验，失败时滚动到第一个错误字段并聚焦

### 4. 版本管理 `/me/tools/:id/versions`

按 `docs/04` §6.8：左侧版本时间线 + 右侧详情面板。

- 顶部显示「历史版本保留 10 份，当前 N 份」，接近上限（≥8）时转 `warning` 并预告会被归档
- 「上传新版本」`Dialog`：版本号（提示语义化版本）、变更说明（Markdown，为空时软校验确认）、
  文件/内容输入、`auto_submit` 开关（默认开）
- **上传后 toast 必须说明**：「新版本 1.3.0 已提交审批。当前版本 1.2.0 继续对外提供服务。」
  —— 这句话直接消除用户「会不会影响现有用户」的疑虑
- 被驳回的版本展示驳回理由 + 「修改后重新提交」
- 五种版本状态要视觉可区分：`pending`(warning) / `approved`(success+当前) / `superseded`(中性+历史) /
  `rejected`(destructive) / `purged`(灰+已归档)

### 5. 审批台（`/admin/*`）

M2 需要搭出**管理台布局**（`docs/04` §5.2 的左侧菜单 + 右侧内容，`lg` 以下折叠为 `Sheet`）：

**`/admin/approvals`（审批队列）** 按 `docs/04` §6.10，左列表 + 右 `Sheet` 详情抽屉：

- 列表项展示：工具名、`submission_type` 徽标（新工具/新版本）、类型、提交人、
  **等待时长**（>24h 转 warning，>72h 转 destructive）、变更说明首行
- 分段筛选：全部 / 新工具 / 新版本
- 右侧抽屉**复用详情页的渲染组件**（`Markdown`、`SkillPreview`、图片），不要跳转到门户
- 「批准」需填选填备注；「驳回」**必填理由（≥5 字）+ 二次确认**；「下架」必填理由
- **批量批准**（多选，按钮显示已选数量）；**不提供批量驳回** —— 界面不提供该按钮，
  而不是提供了再报错
- **键盘快捷键**：`J`/`K` 上下切换、`A` 批准、`R` 驳回、`Esc` 关抽屉
- 批准后自动选中下一条 + toast（批准是低风险操作，不弹二次确认）

**`/admin/approvals/history`（审批历史）** 按 `docs/04` §6.11：筛选栏（工具、操作人、动作类型、
时间范围）+ 表格 + 导出 CSV。动作徽标配色见文档。

**`/admin/whitelist`（免审白名单）** 按 `docs/04` §6.17：顶部说明 + 总开关关闭时的 warning 提示 +
表格 + 添加 `Dialog`（搜索用户 + 原因 + 可选过期时间）。

**`/admin/settings`（M2 只做审批分组）**：审批模式切换（含 `AlertDialog` 警告 +
响应里的 `warnings` 展示待审数量）、免审白名单开关、新版本需重新审批开关。
其余设置分组留到 M3。

**`/admin`（index）**：M2 做简单跳转到 `/admin/approvals` 即可，完整概览页留 M3。

**角色渲染**：`approver` 只看到审批相关的 4 项；`superadmin` 多看到白名单与设置。
用 `RequireRole` 包住超管专属路由。

### 6. MSW 扩展

为 M2 的 38 个新接口补 mock handler，保持并行开发能力：

- 有状态：提交后状态变化、批准后当前版本切换、驳回后回到 approved
- mock 数据按 `contracts/CONTRACT.md` §14.6 扩到 **26 个工具**（让 12/24/48 档分别得到 3/2/1 页）
- 补 3 个作者账号 `zhangsan`/`lisi`/`wangwu`（与后端 M2 种子一致）
- 图片 URL 带假的 `?sig=`
- 仍由 `VITE_ENABLE_MOCKS` 控制，**生产构建必须完全剔除**

### 7. 通用组件补齐

- `StorageUsageBar`（带阈值变色）
- `VersionTimeline`
- `SkillPreview`（三 Tab）
- `PromptViewer`（含一键复制）
- `AclEditor`（全量提交语义）
- `ImageUploader`（拖拽 + 排序 + 封面标记）
- `TagInput`（≤8 个，支持联想 `GET /tags?q=`）
- `Badge` 的 `warning` / `success` 变体（裁定 2）
- **接入 M1 已实现但未被引用的** `ConfirmDialog` 与 `CopyButton`，并补上运行时证据

### 8. 类型与 API 层同步（裁定见 §14.2）

- `User` 补 `status` / `last_login_at` / `created_at`
- provider 补 `password_change_supported` / `refresh_supported`
- `logout` / `changePassword` 返回类型改为 `{status: string}`（后端是 200 + body，不是 204）
- `facets` 类型必须是 `ToolFacets | null`（**不是** `facets?: ToolFacets`）
- 图片 URL 原样使用后端下发值，不自行拼接

### 9. 真实后端 E2E（裁定 6，必须做）

新增 `web/playwright.real.config.ts`（或等价机制）：

- `baseURL` 指向 `http://127.0.0.1:8000`（后端托管 `web/dist`，与生产拓扑一致）
- **不启用 webServer**（假定后端已由外部启动）
- 用 `channel: "chrome"`（本机 Chrome，沙箱无法下载 Playwright 自带浏览器）
- 用**生产构建产物**跑（`VITE_ENABLE_MOCKS=false`）

**必须覆盖这些 mock 测不到的点**：

| 场景 | 为什么 mock 测不到 |
| --- | --- |
| 登出/改密成功响应的真实形状 | mock 返回的是你假定的 204 |
| `refresh` cookie 的真实 `Path` | mock 用 `Path=/` |
| 真实 `facets: null` 序列化 | mock 可能返回 `undefined` |
| 真实 404 语义（无权工具返回 404 而非 403） | mock 容易写成 403 |
| 真实分页与筛选的 SQL 行为 | mock 是内存过滤 |
| 图片签名 URL | mock 无签名 |

### 10. 防回归脚手架（裁定 5）

`web/scripts/check-ui-primitives.mjs`，断言：

1. 交互型原子组件（`button` / `input` / `select` / `checkbox` / `sheet` / `dialog` /
   `alert-dialog` / `dropdown-menu` / `popover` / `tooltip`）都使用 `forwardRef`
2. `src/lib/utils.ts` 的 `cn` 是本地 `clsx` + `tailwind-merge` 实现
3. `package.json` 不存在 `cn` 与 `next-themes` 依赖

挂到 `npm run verify`（= `tsc --noEmit` + `oxlint` + 本脚本 + `npm run build` + dist 无 MSW 检查）。

---

## M2 验收清单（监控方会逐条实证）

| # | 场景 | 期望 |
| --- | --- | --- |
| 1 | 门户点卡片进详情 | `/tools/:slug` 正常渲染，无 404 |
| 2 | `file` 类型详情 | 文件信息卡、SHA256 可复制、下载按钮可用 |
| 3 | 下载文件 | 走票据，文件真实落盘，SHA256 与页面一致 |
| 4 | 历史版本下载 | `?version_id=` 生效，下载到的是该版本 |
| 5 | `webapp` 类型详情 | 「打开工具」新标签页打开正确 URL |
| 6 | `skill` 类型详情 | 三 Tab：SKILL.md 渲染、文件树、元信息；切 Tab 才发请求 |
| 7 | `prompt` 类型详情 | 一键复制内容与原文完全一致 |
| 8 | `purged` 版本 | 显示「已归档」，下载禁用 |
| 9 | viewer 角色看详情 | 可看但 `can_download=false`，按钮禁用且有说明 |
| 10 | 无权访问的工具 | 渲染 404 页（不是 403、不是崩溃） |
| 11 | 创建 file 工具并提交 | 表单校验、上传进度、提交后进入「待审」 |
| 12 | 切换工具类型 | 弹确认框，确认后清空已填内容 |
| 13 | Skill 包解析失败 | 显示具体错误，可存草稿但「提交审批」禁用 |
| 14 | 草稿自动保存 | 失焦后顶部出现「已保存 · 刚刚」 |
| 15 | 上传新版本 | toast 明确说明「旧版本继续服务」 |
| 16 | 被驳回的工具 | 「我的工具」中展开显示理由 + 重新提交入口 |
| 17 | 版本时间线 | 五种状态视觉可区分 |
| 18 | 审批队列 | 等待时长 >24h 高亮；分段筛选生效 |
| 19 | 审批详情抽屉 | 不跳转门户即可预览包内容 |
| 20 | 驳回 | 理由 <5 字被拦；二次确认；提交后队列移除 |
| 21 | 批量批准 | 按钮显示已选数量，批准后列表刷新 |
| 22 | 键盘快捷键 | J/K/A/R/Esc 全部生效 |
| 23 | 免审白名单 | 总开关关闭时显示 warning 提示 |
| 24 | 审批模式切换 | 弹警告并展示待审数量 |
| 25 | `Badge variant="warning"/"success"` | 正常渲染（不再用显式 amber 类） |
| 26 | 分页（26 个工具） | 12/24/48 档分别显示 3/2/1 页，可实际翻页 |
| 27 | ⌘K 搜索选中工具 | 跳 `/tools/:slug` 而非 `/?q=` |
| 28 | UserMenu | 个人中心/我的工具/管理后台均可用（非 disabled） |
| 29 | 深色主题 + 刷新 | 主题保持，首屏无白闪 |
| 30 | dist 洁净 | 无 MSW 字样；首屏 gzip < 500 KB |

---

## 报告要求

按 `contracts/CONTRACT.md` §10 格式回报。额外要求：

- **必须给出真实后端 E2E 的运行证据**，不能只给 mock 模式的。贴出命令与通过数
- 验收 26、27、28、30 要贴具体证据（分页控件截图描述/页码数、跳转后的实际 URL、
  菜单项可点击、`grep -ri msw dist/` 的实际输出）
- 任何新增依赖必须列出并说明用途
- **如果发现 `docs/04` 或 `docs/03` 的某个页面/响应示例与你实现的不一致，报告它**，
  不要自己拍板。上一轮你的 7 条待裁决质量很高，正是这种报告避免了两条线的漂移

---

## 易错点清单

**语义类**

1. **权限判断直接读详情响应里的 `permissions` 对象**，不要在前端重新实现一遍可见性/角色逻辑
2. **`has_pending_version` 只对 owner 与审批人可见**，其他用户后端恒返回 `false`，前端不要再猜
3. **`purged` 版本保留在列表里但不可下载**，别把它过滤掉（用户需要看到历史）
4. **下载必须走票据**，不要用 `<a href="/api/v1/tools/.../download">` —— 那带不了鉴权
5. **图片用后端下发的签名 URL 原样渲染**，不要自己拼 `/api/v1/images/{id}`
6. **`facets` 是 `ToolFacets | null`**，不是可选属性。翻页时它存在但为 `null`

**交互类**

7. **审批抽屉复用详情页组件**，不要在审批台重写一套渲染逻辑（必然漂移）
8. **不提供批量驳回按钮**，而不是提供了再报错
9. **类型切换必须确认**，否则用户填了半天的内容被静默清空
10. **上传新版本的 toast 要说明旧版本继续服务**，这是用户最担心的事
11. **草稿自动保存不要过于频繁**，30 秒或失焦，避免 SQLite 侧写压力

**工程类**

12. **E2E 必须跑真实后端模式**，只在 mock 下通过不算验收（这一条是 M1 的教训）
13. **`check-ui-primitives.mjs` 要挂进 verify**，否则 shadcn 回归会再次复发
14. **`request<null>` 要能处理 200 + body**，不要假设 204
15. **管理台路由要用 `RequireRole` 包住超管专属项**，approver 不该看到白名单菜单

---

## 不要做的事

- 不实现 M3 页面（用户管理、用户组管理、分类管理、标签管理、API Token、
  批量导入导出、回收站、全站工具管理、完整设置页、完整概览页）
- 不写后端代码，不改 `backend/`
- **不改 `contracts/acceptance/real-backend-check.cjs`**（监控方的验收脚本）
- 不重构 `docs/` 或 `contracts/`
- 不引入状态管理库、UI 组件库（Ant Design / MUI）、CSS-in-JS
- 不执行 git 命令
- 不做数据可视化大屏、推荐位轮播图（`docs/01` 第 10 章明确不做）

有阻塞或需要裁决时，立刻按 §10 格式报告，不要自己猜着往下做。
