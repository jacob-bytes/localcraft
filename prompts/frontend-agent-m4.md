# 前端开发 Agent — 任务书（M4 · 收口）

## 先看这一节：M3 验收结论

你的 M3 已通过监控方实证验收，报告质量很高 —— **`sort_order` 那条把「PATCH 显式 null = 不改」
的语义与字段可空性联系起来看，不是机械对齐字段名；`check:api-types.mjs` 把漂移变成了会失败的检查。**

完整裁定见 `contracts/CONTRACT.md` **§19**，请先完整读一遍。摘要：

| # | 裁定 |
| --- | --- |
| 19.2 | openapi × types.ts 的 5 项修正 **接受**，`check:api-types` 守卫正确 |
| 19.3 | `docs/03` §2.5 缺 2 行 + 计数过期 **你说得对，监控方已补齐**（§16.5 守卫措辞不变） |
| 19.4 | 响应示例改为「语义 + 指向 openapi」—— 这是**结构性收口**，不再逐条修补 |
| 19.5 | `details.tools` 补 `slug` → **已入 M5**（后端 J1） |
| 19.6 | `docs/04` §6.13 的「置灰」**删除**，保持现状；§6.9/§6.12/§6.14/§6.18/§6.20 **已由监控方修订** |
| 19.7 | 6 个端点补 `response_model` → **已入 M5**（后端 J2） |
| 19.8 | `value_type` 维持 5 值；TagInput 上限维持现状（工具 8/64，设置列表不设限） |
| **19.9** | **真实 E2E 的 flake 未修净，要求韧性修复 + 证据标准提到连续 10 次** |

---

## M4 目标

**不新增页面。修掉那个 flake，等后端 M5 落地后重跑联调，然后把交付收尾。**

这是最后一个里程碑。做完之后前端应当处于「可以冻结」的状态。

---

## 边界（硬性）

你仍然独占并只允许修改：`web/`

**绝对不可修改**：`backend/`、`docs/`、`contracts/`、`deploy/`、`scripts/`、`README.md`、`prompts/`
（`scripts/` 指仓库根的那个；`web/scripts/` 是你的）

不执行任何 git 命令 —— **包括 `git status`**（它会刷新并可能写入索引）。
需要确认改动范围时用 `find web -newer <ref> -type f` 之类的文件系统手段。

工作目录：`/Users/jlthzy/Documents/selftool`

---

## 交付清单

### 1. 修掉懒加载 chunk 的 flake（最高优先级）

**监控方复跑结果**：13 次真实 E2E 中失败 2 次（一次测试 B、一次测试 F）。测试 A 的竞态确实修好了，
但新的 flake 出现。失败模式已从 Playwright 的 error-context 与截图定位：

```
Unexpected Application Error!
Failed to fetch dynamically imported module:
http://127.0.0.1:8000/assets/ToolDetailPage-CgGYlRAi.js
```

即**路由级懒加载 chunk 拉取失败**，被 React 错误边界接住，因此渲染的是
「Unexpected Application Error」而不是 404 页。

**监控方已排除的成因**（不必重复排查）：

- chunk 确实存在于磁盘（`ls dist/assets/` 可见）
- 构建是**确定性**的（同源码两次构建，产物文件名完全一致）
- 5 次连续运行中 `dist/index.html` 的 mtime **全程未变** —— 不是「构建替换导致旧 chunk 消失」
- 查询重试策略正确（4xx 不重试）

**未能定位根因。因此要求的是「对任何成因都有效的韧性修复」，而不是继续刨根因。**

要做四件事：

**1.1 加 `lazyWithRetry`**

路由级动态 `import()` 包一层：失败时**重载页面一次**，用 `sessionStorage` 打标防重载循环
（例如 `selftool:chunk-retry:<chunkName>`，成功导航后清除标记）。

这是该类失败的标准缓解手段，覆盖部署替换 chunk、瞬时网络抖动、请求被中断等所有成因。
**并且这是对用户的真实收益** —— 目前用户撞上会看到错误边界白屏，而不是自动恢复。

**1.2 错误边界要能区分「chunk 加载失败」与「其他渲染错误」**

chunk 失败走重载路径；其他错误保持现有展示。不要把所有错误都变成重载。

**1.3 在测试的失败输出里打印诊断信息**

失败时输出：失败 chunk 的完整 URL、服务端对该 URL 的实际响应（status + content-type）、
以及 `dist/index.html` 的当前 mtime。**本轮监控方只能从错误边界的文案里读到 URL** ——
下一次复现必须能直接定位。

**1.4 测试要断言经得起一次重试**

不要假设首次加载必然成功。若 `lazyWithRetry` 生效，页面会自动恢复并最终渲染出 404 页，
断言应当仍然成立 —— 但要给它足够的时间窗，并在失败信息里说明「已允许一次 chunk 重试」。

### 2. 证据标准：连续 10 次全绿

**监控方把标准从「连续 3 次」提高到「连续 10 次」**（§19.9）。理由：按观测到的约 15% 失败率，
3 次通过的概率约 61%，**不足以证明修好了**。

```bash
# 后端需先就绪（见下）
for i in $(seq 1 10); do npm run e2e:real || break; done
```

**贴出 10 次的实际输出。** 任何一次失败都要如实报告并分析，不要只贴通过的几次。

### 3. 等后端 M5 落地后重跑真实联调

后端 M5 会改变以下事实，**其中前两项会直接影响你的真实 E2E 与 mock**：

| 后端 M5 的改动 | 对你的影响 |
| --- | --- |
| **图片下发签名 URL**（`cover_url` 含 `sig=`） | mock 的图片 URL 要同步加假 `sig`；真实环境下封面**不再 404**，`onError` 降级路径应仍保留但不再被触发 |
| **种子扩到 26 工具 + viewer 账号 + 3 个作者 + 真实文件** | mock 已对齐；区别是**真实后端现在也有真实文件**，验收 #3/#4（下载落盘、SHA256 一致）可以直接在种子工具上跑，不必再临时上传 |
| **`/admin/tools/{id}/versions` 从 404 变为可用** | 你当前不调用它，无需改动；但 mock 里那个「保持 404 + `details.hint`」的桩要**改成可用**，否则 mock 与真实后端又不一致 |
| **`GET /admin/overview` 新增 `storage_warning`** | 概览页改用它渲染存储告警（不要再前端自己拿 `used_percent` 与阈值比较 —— 判断口径应集中在一处） |
| **`GET /admin/groups` 的 `details.tools[]` 新增 `slug`** | 影响面列表改为**渲染 `Link`**（`docs/04` §6.14 的「可点击」现在成立了） |
| **6 个端点补 `response_model`** | `openapi.json` 会变，`check:api-types` 可能报出新差异 —— **按 openapi 补齐 `types.ts`** |
| **设置项新增 `images.signature_ttl_hours`** | 设置页是 key 驱动的，会自动出现；确认它落在「其他」或合适的兜底分组，不要丢项 |
| **`approval.version_reapproval` 接线、`quota.warn_threshold_pct` 接线** | 设置项本身无 UI 变化；确认它们仍正常渲染 |

**流程**：先确认后端 M5 已交付并提交（看 `git log` 的 checkpoint 提交，或问监控方），
再重跑真实 E2E 并更新 mock。**不要在后端 M5 未落地时假装对齐。**

### 4. `check:api-types` 的覆盖复查

`check:api-types.mjs` 目前覆盖的是 openapi 与 `types.ts` 的一致性。
后端 M5 补完 6 个 `response_model` 后，请确认：

- 守卫能覆盖到这 6 个端点（之前它们是 `additionalProperties: true`，可能被守卫跳过）
- 若守卫对无字段定义的响应是「跳过而非报错」，要**改成报错** ——
  否则这 6 个端点会长期处在守卫盲区里

### 5. 收尾复查（最后一个里程碑）

- 深色主题：管理台全部页面（含 M3 新增的 10 个）在深色下无硬编码浅色类
- 响应式：1280px 最小宽度下管理台可用；窄屏侧栏折叠；宽表格横向滚动不破版
- 空态/加载态/错误态：统一使用 `EmptyState`/`PageSkeleton`/`ErrorState`
- 无障碍：图标按钮有 `aria-label`；`DataTable` 排序可键盘操作；`Dialog` 焦点陷阱正常
- `npm run verify` 全过；首屏 gzip < 500 KB
- **清理**：确认无遗留的临时调试文件（M2 曾清理过 `e2e/tmp-*.spec.ts` 与 `e2e/debug.mjs`，本轮再查一遍）

---

## M4 验收清单（监控方会逐条核验）

| # | 场景 | 期望 |
| --- | --- | --- |
| 1 | **连续 10 次 `npm run e2e:real`** | **全部 12 项通过，10/10** |
| 2 | `lazyWithRetry` 存在 | 路由级动态 import 包了重试；有 `sessionStorage` 防循环 |
| 3 | 错误边界区分 chunk 失败 | chunk 失败走重载；其他错误不变 |
| 4 | 测试失败时输出诊断 | 含失败 chunk URL + 服务端响应 + index.html mtime |
| 5 | mock 的图片 URL | 含假 `sig=` |
| 6 | mock 的 `/admin/tools/{id}/versions` | 从 404 桩改为可用 |
| 7 | 概览页存储告警 | 使用后端 `storage_warning`，前端不再自己比阈值 |
| 8 | 组删除影响面 | 渲染 `Link`（后端已给 `slug`） |
| 9 | `check:api-types` | 覆盖 6 个新补 `response_model` 的端点；无字段定义时报错而非跳过 |
| 10 | `types.ts` 与最新 openapi | 一致 |
| 11 | 设置页 | 新增的 `images.signature_ttl_hours` 正常渲染，不丢项 |
| 12 | 真实 E2E 的种子断言 | 26 工具 / viewer 账号 / 真实文件下载 SHA256 一致 |
| 13 | 深色主题 | 管理台全部页面正常 |
| 14 | `npm run verify` | 全过，dist 无 msw |
| 15 | 首屏 gzip | < 500 KB |
| 16 | 无遗留调试文件 | `e2e/` 下只有正式套件与产物 |

---

## 报告要求

按 `contracts/CONTRACT.md` §10 格式回报。额外要求：

- **验收 1 必须贴连续 10 次的原始输出**。任何一次失败都要分析，不许只贴通过的几次
- 验收 2/3/4 要贴代码片段与一次人为制造的 chunk 失败验证（例如临时把 chunk 改名，
  确认应用能自动重载恢复）
- 如果 10 次里仍有失败，**如实报告**并给出你判断的成因与下一步 ——
  这比硬凑一个「全绿」有价值得多
- 后端 M5 若尚未落地，**明确说明哪些项因此无法验证**，不要用 mock 的结果冒充真实后端的结果

---

## 易错点清单

1. **`lazyWithRetry` 的防循环标记要在成功之后清除**，否则用户在一次失败后永久失去重试能力
2. **不要把所有错误都变成重载** —— 渲染错误重载会变成无限循环
3. **`storage_warning` 的判断口径只应存在于后端**，前端再算一遍就会两处漂移
4. **mock 与真实后端的对齐是双向的** —— 后端改了，mock 也要改，否则 mock 会掩盖回归
5. **`check:api-types` 对无字段定义的响应不能静默跳过**，否则守卫有盲区
6. **不要为了让 flake「消失」而删掉或弱化断言** —— 失败模式下用户看到的是白屏，那是真问题

---

## 不要做的事

- **不新增页面、不新增后端接口**（接口面冻结于 92）
- 不写后端代码，不改 `backend/`
- 不改 `contracts/acceptance/real-backend-check.cjs`（监控方的验收脚本）
- 不重构 `docs/` 或 `contracts/`
- 不引入状态管理库、UI 组件库、CSS-in-JS、图表库
- 不执行任何 git 命令，含 `git status`
- 不做 `webapp` 探活状态点（`docs/01` 第 10 章明确不做）

有阻塞或需要裁决时，立刻按 §10 格式报告，不要自己猜着往下做。
