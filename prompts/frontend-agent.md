# 前端开发 Agent — 任务书（M1）

## 你的身份与边界

你是 **localcraft 平台的前端开发 agent**。你独占并只允许修改一个目录：

- `web/`

**你绝对不可以修改**：`docs/`、`contracts/`、`README.md`、`backend/`、`deploy/`、`scripts/`。这些是只读的。后端由另一个 agent 并行开发，你去动 `backend/` 会直接造成冲突。

**不要执行任何 git 命令**（commit / branch / checkout / stash 等一个都不要）。版本控制由监控方在 checkpoint 统一处理，并发操作 git 索引会冲突。

工作目录：`/Users/jlthzy/Documents/localcraft`

---

## 第一步：必须先读完这些（不要跳过）

按顺序读，读完再动手：

1. `contracts/CONTRACT.md` —— **最重要**。这是你和后端之间的唯一约定来源。认证握手（§3）、响应信封（§4）、数据格式（§5）、M1 接口清单（§6）、种子数据（§7）、联调验收（§8）全在里面。**§3.1 的「access token 只放内存」是硬要求，不要改成 localStorage。**
2. `docs/04-前端页面与交互清单.md` —— **本文档是你的主要规格书**。§1 技术选型、§2 工程目录、§3 设计令牌、§4 路由表、§5 布局、§6 页面详述、§7 通用组件、§8 无障碍与响应式、§9 性能。
3. `docs/03-API接口清单.md` —— §1 通用约定、§2.1～2.3 接口表、§3.1～3.3 关键接口的完整请求响应示例、§4 错误码总表。你要照着 §3.3 实现门户列表的筛选与 facets。
4. `docs/01-需求规格说明书.md` —— 只需读第 3 章权限矩阵（决定按钮显隐）、5.11 门户与检索、5.12 个人中心。
5. `README.md` 的「Python 版本策略」一节 —— 你不需要 Python，但要知道前后端约定的 Python 版本背景。

---

## M1 目标

**一条打通的真实竖切**：登录 → 刷新恢复会话 → 门户列表（含筛选/搜索/分页）→ 登出。外加设计体系、布局骨架与通用组件。

M1 **只做** `docs/04` §6 里的这四个页面：`/login`、`/change-password`、`/`（门户）、`/404`。以及对 `AppShell` + `TopNav`。**不要**做工具详情页、个人中心、管理台 —— 那些是 M2/M3。

**注意**：你不需要等后端。M1 全程用 **MSW mock** 开发，mock 数据严格按 `contracts/CONTRACT.md` §7 的种子数据规格来造。这样你和后端可以真正并行，最后在 checkpoint 用真实接口对一次。

---

## 交付清单

### 1. 工程初始化

```bash
cd /Users/jlthzy/Documents/localcraft
npm create vite@latest web -- --template react-ts
cd web && npm install
```

- **包管理器用 npm**（不用 pnpm/yarn）。Node 24 已就绪
- Vite + React 18 + TypeScript，`tsconfig.json` 开 `strict: true`、`noUncheckedIndexedAccess: true`
- Tailwind CSS v4 + shadcn/ui：`npx shadcn@latest init`
- `vite.config.ts` 配 dev proxy（契约 §2）：

  ```ts
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } }
  }
  ```

  走 proxy 后浏览器视角同源，所以**后端不启用 CORS，你也不需要处理跨域**。

- 依赖按 `docs/04` §1 引入：`react-router-dom`、`@tanstack/react-query`、`react-hook-form`、`zod`、`@hookform/resolvers`、`markdown-it`、`dompurify`、`lucide-react`、`date-fns`、`sonner`、`@tanstack/react-table`、`msw`(dev)。**想加别的先报告。**

### 2. 设计令牌

严格按 `docs/04` §3.1 把 CSS 变量写进 `src/index.css`（`:root` 与 `.dark` 两套，用 oklch）。§3.2 的排版、§3.3 的间距圆角阴影规则、§3.4 的主题切换机制都要落地。

**首屏防白闪**：在 `index.html` 里放一段阻塞式内联脚本，在 React 挂载前根据 `localStorage` 设置 `<html class="dark">`。这是唯一允许的内联脚本。

### 3. API 层（`src/api/`）

`client.ts` 是 M1 技术含量最高的文件，必须严格实现契约 §3 的完整握手：

- 模块级变量持有 `accessToken`（**内存，不落 storage**）
- **单例 refresh Promise 去重**：并发多个请求同时收到 401 时，只发起一次 `POST /auth/refresh`，其余 `await` 同一个 Promise。不做这个去重，页面初始化时会打出刷新风暴
- 401 `TOKEN_EXPIRED` → 静默 refresh → 用新 token **重放原请求一次**；重放仍失败 → 清状态跳 `/login`
- 403 `PASSWORD_CHANGE_REQUIRED` → 跳 `/change-password`
- 错误规范化：把后端的 `{code, message, details, request_id}` 转成一个 `ApiError` 类抛出，`details.fields` 可直接喂给表单
- `refresh()` 成功要**同时返回 user 对象**（契约 §3.3），据此恢复用户态

`types.ts` 镜像 `docs/03` 的全部响应类型。枚举用字符串字面量联合类型（如 `type ToolType = "file" | "webapp" | "skill" | "prompt"`），**与后端的 `StrEnum` 一一对应**。

### 4. 认证与会话

- `useAuth` / `AuthProvider`：持有 `accessToken` + `user`，暴露 `login` / `logout` / `refresh` / `hasRole`
- **`RequireAuth` 的三态**：`unknown`（启动中，refresh 还没返回）→ 渲染全屏 loading；`authenticated` → 渲染子路由；`unauthenticated` → 跳 `/login`。
  **关键**：启动时必须处于 `unknown` 态，不能先判成未登录再纠正，否则用户每次刷新都会闪一下登录页（验收第 4 条会挂）
- `RequireRole`：角色不足时渲染 403 页面。**这不是安全边界**，只是体验优化，服务端才是权威
- `must_change_password=true` → 除 `/change-password` 与 `/login` 外全部重定向

### 5. 布局与页面

- `AppShell` + `TopNav` + `UserMenu` + `ThemeToggle`，按 `docs/04` §5.1。**不做全局左侧固定侧边栏**（管理台才有，那是 M3）
- 顶栏的全局搜索用 shadcn `Command`，支持 `⌘K` / `Ctrl+K`
- **`/login`**：按 `docs/04` §6.1。含账号锁定倒计时、凭证错误不清空密码框、`autoComplete` 属性正确、已登录访问自动重定向
- **`/change-password`**：按 `docs/04` §6.2。强制模式隐藏导航、密码强度指示条、成功后清状态跳登录页
- **`/`（门户）**：按 `docs/04` §6.3 **完整实现**。这是 M1 的重头戏：
  - Hero 搜索区（`h-12`，防抖 300ms 后写 URL query）
  - 左侧筛选栏：分类单选（带计数）+ 类型多选 + 标签 + 已选条件 Badge 集合 + 清除全部
  - 卡片栅格：`grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4`
  - 排序切换（热门/最新/名称）、每页条数（12/24/48）、分页
  - **全部筛选/排序/分页状态同步到 URL query**，用 `useSearchParams`。刷新和复制链接都要能还原（验收第 6 条）
  - `facets` 只在 `page === 1` 时从响应里取（契约与 `docs/03` 3.3）
- **`ToolCard`**：封面区 `aspect-[16/9]`；无封面时按分类映射柔和色块 + 类型图标（**不能破图**，验收第 7 条）；类型徽标绝对定位在封面左上角；名称 `line-clamp-1`、简介 `line-clamp-2`（验收第 8 条）；标签最多 3 个 + `+N`；底部下载量/浏览量/相对时间。整卡用 `<Link>` 包裹，**不要**用 `onClick`（否则中键新标签、右键复制链接都失效）

### 6. 通用组件（`src/components/common/`）

按 `docs/04` §7 实现：`Markdown`、`EmptyState`、`ErrorState`、`PageSkeleton`、`CopyButton`、`ConfirmDialog`。

`Markdown` 要点：`markdown-it` 配 `html: false`，再过 `DOMPurify.sanitize` 白名单，`ALLOWED_URI_REGEXP` 只放行 `https?`/`mailto`（禁 `javascript:`/`data:`）。外链加 `target="_blank" rel="noopener noreferrer"`。

**所有列表页统一用 `EmptyState`/`ErrorState`/`PageSkeleton`**，不允许各页面自己写一套。

### 7. MSW mock（M1 的并行开发关键）

- 建 `src/mocks/handlers.ts`，覆盖 `contracts/CONTRACT.md` §6 的全部 12 个接口
- mock 数据严格按 §7 的规格造：1 个超管 + 1 个需改密用户、4 个分类、**8 个工具覆盖 4 种类型**、其中 **2 个无封面**、**1 个名称超 40 字符且简介超 3 行**
- mock 要**有状态**：登录成功后才允许拉 `/tools`；登出后拒绝；这样能验证真实的鉴权跳转逻辑
- 由 `VITE_ENABLE_MOCKS=true` 控制启用。`vite.config.ts` 里用 `import.meta.env` 或 `loadEnv` 判断，**生产构建必须完全剔除 msw**
- 交付前必须验证：

  ```bash
  npm run build
  grep -ri "msw" dist/ && echo "失败：mock 被打进生产产物" || echo "通过"
  ```

### 8. 质量要求

- **无障碍**（`docs/04` §8.1）：可点击元素用 `<button>`/`<a>` 而非 `<div onClick>`；表单字段有 `<FormLabel>`；图标按钮带 `aria-label`；焦点可见（不要 `outline: none`）；状态徽标不能只靠颜色区分，必须有文字
- **响应式**（`docs/04` §8.2）：< sm 单列 + 筛选折叠为 `Sheet`；lg 三列；xl 四列
- **性能**（`docs/04` §9）：路由级 `React.lazy`；TanStack Query 缓存（门户列表 `staleTime: 60s`）；封面 `loading="lazy"` + 固定 `aspect-ratio` 容器；查询键规范化
- 不用 `any`。必要时 `unknown` + 收窄
- 不引入状态管理库（Redux/Zustand）。服务端状态归 TanStack Query，全局只有 `user` 和 `theme`，用 Context 就够

---

## 完成前必须自己跑一遍并贴出结果

```bash
cd web
npx tsc --noEmit
npm run lint          # 若已配 eslint
npm run build
grep -ri "msw" dist/ && echo "失败：mock 被打进生产产物" || echo "通过：产物干净"
npm run dev           # 手动走一遍下方验收
```

**`contracts/CONTRACT.md` §8 的 13 条验收里，第 2~9、13 条属于你的责任范围**（用 mock 状态下验证）。请逐条手动走一遍并贴出证据（截图描述或操作步骤 + 观察到的结果）。

重点自查这三条最容易翻车的：

- **第 4 条**：F5 刷新后仍停留在门户，**不能闪登录页**。检查 `RequireAuth` 的 `unknown` 态是否真的存在
- **第 7 条**：无封面工具显示占位色块而不是破图
- **第 13 条**：切换到深色主题后刷新，主题保持且首屏无白闪

---

## 报告要求

完成后按 `contracts/CONTRACT.md` §10 的格式回报。硬性要求：

- `执行的验证` 一栏贴**真实跑过的命令与输出摘要**（`tsc` 无错误、`build` 产物体积、dist 里无 msw 的实际输出）。不接受「应该没问题」「已测试通过」这类没有证据的表述
- 任何 **契约偏差** 或 **待裁决** 必须显式列出。宁可报一个「我拿不准」，也不要自己拍板改了接口 —— 后端是并行开发的，你改一个字段名就能让对方白干半天
- 如果你在实现中发现 `docs/03` 的某个响应示例缺少字段（比如门户列表需要但文档没定义），**报告它**，不要自己编一个字段然后指望后端也这么返回

---

## 易错点清单（这些是最可能翻车的地方）

1. **access token 写进 localStorage**。契约 §3.1 明确禁止。放内存，靠 refresh 恢复
2. **refresh 请求没有去重**。页面初始化时多个 Query 同时 401，打出 N 个并发 refresh，后端会话表会被写爆
3. **`RequireAuth` 没有 unknown 态**，刷新页面必闪登录页
4. **筛选状态只放 React state 不放 URL**，验收第 6 条直接挂，用户也没法分享筛选结果
5. **整卡用 `onClick` 而不是 `<Link>`**，中键和右键行为坏掉
6. **无封面时 `img src=""`**，浏览器显示破图边框
7. **`facets` 在每页都取**，翻页时左侧分类计数突然消失，视觉跳变
8. **MSW 被打进生产构建**。这是会被 checkpoint 直接拦下的硬伤
9. **深色主题在 React 挂载后才设置**，首屏白闪
10. **`markdown-it` 开了 `html: true`**，XSS 风险直接引入
11. **错误处理只读 `message` 不读 `details.fields`**，表单拿不到字段级错误，只能弹一个笼统的 toast

---

## 不要做的事

- 不要实现 M2/M3 的页面（工具详情、个人中心、工具编辑器、审批队列、管理台）—— 现在做会让契约比对失去意义
- 不要写后端代码，不要改 `backend/`
- 不要顺手重构 `docs/` 或 `contracts/`，发现文档有问题就在报告里提
- 不要引入状态管理库、UI 组件库（Ant Design / MUI 等）、CSS-in-JS 方案。shadcn/ui + Tailwind 已经定了
- 不要执行 git 命令
- 不要为了「看起来完整」而做数据可视化大屏、推荐位轮播图 —— `docs/01` 第 10 章和第 04 号文档 §10 都明确列了不做

有阻塞或需要裁决时，立刻按 §10 格式报告，不要自己猜着往下做。
