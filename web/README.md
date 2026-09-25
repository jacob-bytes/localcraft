# selftool web（前端）

内网工具 / Skill 共享平台的前端。技术栈与目录约定见 `docs/04-前端页面与交互清单.md`，
与后端的接口约定见 `contracts/CONTRACT.md`（M1 裁决在 §14，M2 接口清单在 §6.1）。

## 命令

```bash
npm ci                  # 安装依赖（Node 24 / npm）
npm run dev             # 开发服务器 http://127.0.0.1:5173（默认启用 MSW）
npm run typecheck       # tsc -b（strict + noUncheckedIndexedAccess，含 e2e）
npm run lint            # oxlint --deny-warnings src
npm run build           # tsc -b && vite build → dist/
npm run verify          # typecheck + lint + check:primitives + build + check:dist（提交前跑这个）
npm run e2e             # Playwright：M1 §8 + M2 30 项验收（MSW mock 模式）
npm run e2e:real        # Playwright：真实后端模式（后端托管 dist，见下）
npm run evidence        # 打印 #26 分页 / #27 ⌘K / #28 菜单 / #30 产物洁净度 的证据
npm run check:primitives # shadcn 覆盖回归守卫（CONTRACT §14.9）
```

## 数据来源

| 变量 | 值 | 效果 |
| --- | --- | --- |
| `VITE_ENABLE_MOCKS` | `true`（`.env.development`） | 启动 MSW；账号见下 |
| `VITE_ENABLE_MOCKS` | `false`（`.env.production`） | 走 Vite dev proxy → `127.0.0.1:8000` 的真实后端 |

该开关在构建期被替换成字面量（`vite.config.ts` 的 `define`），因此生产产物里既没有
MSW，也没有 `public/mockServiceWorker.js`（`npm run check:dist` 会断言这一点）。

mock 账号（与后端 `seed-demo` 对齐，CONTRACT §14.6/§14.8）：

| 账号 | 密码 | 角色 |
| --- | --- | --- |
| `admin` | `Admin@12345` | superadmin |
| `newbie` | `Newbie@12345` | user（`must_change_password`） |
| `zhangsan` / `lisi` / `wangwu` | `Author@12345` | user（`wangwu` 兼具 approver，用于 approver 视角） |
| `viewer` | `Viewer@12345` | viewer（验收 #9：可看详情、不可下载） |

## 真实后端 E2E

`npm run e2e:real` 打的是**后端托管的生产产物**（与生产拓扑一致），默认
`http://127.0.0.1:8000`，可用 `REAL_BASE` 覆盖。它专门覆盖 mock 测不到的路径：

- refresh cookie 的真实 `Path=/api/v1/auth` 与 F5 会话恢复
- 登出/改密成功的真实响应形状（`200` + `{"status":"ok"}`，不是 204）
- 翻页时 `facets` 的真实 `null` 序列化
- 真实 404 语义（无权访问返回 404）
- 真实 SQL 分页/筛选/排序
- 真实文件上传 → 提交 → 批准 → 下载 → SHA256 一致

步骤：

```bash
cd web
npm run build                       # 生产构建（mock 关闭）
npm run backend:isolated            # 另开一个终端：起一个隔离的后端实例（默认 :8010）
REAL_BASE=http://127.0.0.1:8010 npm run e2e:real
```

`scripts/start-isolated-backend.sh` 用独立的 `DATA_DIR` / `DATABASE_URL` 起
`backend/.venv` 里的服务（不碰 `backend/var/`，避免与后端 agent 的联调数据互相干扰），
并自动跑 `alembic upgrade head` + `seed-demo`。若后端已由他人启动在 8000，直接
`npm run e2e:real` 即可。

## 几个容易踩的点

- **access token 只在内存**（`src/api/client.ts` 模块级变量），刷新页面靠
  `/auth/refresh` 恢复会话；`RequireAuth` 有 `unknown` 态，所以 F5 不会闪登录页。
- **并发 refresh 去重**：`refreshSession()` 是单例 Promise，页面初始化时多个请求同时
  401 也只会发出一次 `POST /auth/refresh`。
- **门户状态全在 URL**：筛选/排序/分页都写进 query string，刷新、分享、前进后退都能还原。
  搜索框状态与 URL 的同步带「防抖追上输入」保护，避免外部跳转被过期防抖值覆盖。
- **权限一律读服务端给的 `permissions` 布尔值**（`ToolDetail.permissions`），前端不重新
  实现可见性/角色判断（docs/03 §3.4、docs/04 §4）。
- **下载走票据**：`POST /tools/{slug}/download-ticket` → 隐藏 `<a>` 触发；直接
  `<a href="/api/v1/tools/.../download">` 带不了 Authorization 头。
- **图片 URL 原样使用后端的 `cover_url` / `thumb_url` / `images[].url`**（签名能力 URL，
  CONTRACT §14.3），不要自己拼 `/api/v1/images/{id}`；加载失败由 `onError` 降级为占位块。
- **审批队列用 `status=pending_all`**：后端把「待处理」定义为 `pending + pending_update`
  （`backend/app/repositories/approvals.py`），传 `pending` 会漏掉新版本待审条目。
- **`components/ui/` 是 shadcn 生成的源码**。上游已改为 React 19 写法（ref 作为 prop），
  本项目锁 React 18，因此按钮/输入/各 `*Overlay` 等已改回 `forwardRef`、`cn` 用本地实现、
  sonner 用项目自身的 `useTheme`。再次运行 `npx shadcn@latest add …` 后请跑
  `npm run check:primitives`（已挂在 `verify` 里）确认没有回归。
