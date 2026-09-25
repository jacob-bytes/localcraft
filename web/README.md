# selftool web（前端）

内网工具 / Skill 共享平台的前端。技术栈与目录约定见 `docs/04-前端页面与交互清单.md`，
与后端的接口约定见 `contracts/CONTRACT.md`。

## 命令

```bash
npm ci                # 安装依赖（Node 24 / npm）
npm run dev           # 开发服务器 http://127.0.0.1:5173
npm run typecheck     # tsc -b（strict + noUncheckedIndexedAccess）
npm run lint          # oxlint（0 warning 才算通过）
npm run build         # tsc -b && vite build → dist/
npm run preview       # 预览生产产物
npm run e2e           # Playwright 走 CONTRACT §8 验收（需要本机 Chrome）
```

## 开发态数据来源

M1 阶段前端不依赖后端，全部接口由 **MSW** 按 `contracts/CONTRACT.md` §7 的种子数据模拟：

| 变量 | 值 | 效果 |
| --- | --- | --- |
| `VITE_ENABLE_MOCKS` | `true`（`.env.development`） | 启动 MSW，`admin/Admin@12345`、`newbie/Newbie@12345` 可登录 |
| `VITE_ENABLE_MOCKS` | `false`（`.env.production`） | 走 Vite dev proxy → `127.0.0.1:8000` 的真实后端 |

该开关在构建期被替换成字面量（`vite.config.ts` 的 `define`），因此生产产物里既没有
MSW，也没有 `public/mockServiceWorker.js`：

```bash
npm run build
grep -ri "msw" dist/ && echo "失败：mock 被打进生产产物" || echo "通过：产物干净"
```

## 几个容易踩的点

- **access token 只在内存**（`src/api/client.ts` 模块级变量），刷新页面靠
  `/auth/refresh` 恢复会话；`RequireAuth` 有 `unknown` 态，所以 F5 不会闪登录页。
- **并发 refresh 去重**：`refreshSession()` 是单例 Promise，页面初始化时多个请求同时
  401 也只会发出一次 `POST /auth/refresh`。
- **门户状态全在 URL**：筛选/排序/分页都写进 query string，刷新、分享、前进后退都能还原。
  搜索框状态与 URL 的同步带「防抖追上输入」保护，避免外部跳转被过期防抖值覆盖。
- **`components/ui/` 是 shadcn 生成的源码**。上游已改为 React 19 写法（ref 作为 prop），
  本项目锁 React 18，因此 `button` / `input` / 各 `*Overlay` 已改回 `forwardRef`；
  再次运行 `npx shadcn@latest add …` 覆盖这些文件时要注意保留。
