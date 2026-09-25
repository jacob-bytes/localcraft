# 并行开发契约（冻结版）

**维护者**：监控方（我）。**本文件是前后端两条开发线之间唯一的约定来源。**

| 角色 | 范围 |
| --- | --- |
| 后端 agent | `backend/`、`deploy/`、`scripts/` |
| 前端 agent | `web/` |
| 监控方（我） | `README.md`、`docs/`、`contracts/`、git 提交 |

**任何一方不得修改不属于自己范围的文件。** `docs/` 与 `contracts/` 对所有 agent **只读**。

---

## 1. 仓库布局（冻结）

```
selftool/
├── README.md
├── docs/                          # 只读：01~05 号文档
├── contracts/
│   └── CONTRACT.md                # 本文件，只读
├── backend/                       # ← 后端 agent 独占
│   ├── app/
│   │   ├── main.py
│   │   ├── core/                  # config security deps errors pagination
│   │   ├── models/                # SQLAlchemy 2.0 模型
│   │   ├── schemas/               # Pydantic v2，请求/响应分开
│   │   ├── services/              # 业务逻辑，事务边界
│   │   ├── repositories/          # 数据访问，唯一写 SQL 的地方
│   │   ├── api/v1/                # 路由
│   │   ├── search/                # SearchBackend 抽象 + SQLite FTS5 实现
│   │   ├── storage/               # 文件存储抽象 + 本地实现
│   │   ├── tasks/                 # 后台任务
│   │   └── cli.py
│   ├── migrations/versions/
│   ├── tests/
│   ├── pyproject.toml
│   ├── requirements.txt
│   ├── requirements.lock
│   └── openapi.json               # 由脚本导出，接口契约真源
├── web/                           # ← 前端 agent 独占
│   ├── src/
│   ├── public/
│   ├── package.json
│   ├── vite.config.ts
│   └── tsconfig.json
├── deploy/                        # ← 后端 agent
│   ├── selftool.service
│   ├── selftool.slice
│   ├── nginx-selftool.conf
│   └── selftool.env.example
└── scripts/                       # ← 后端 agent
    ├── make-release.sh
    ├── backup.sh  restore.sh  verify-backup.sh
    └── precheck.sh  install.sh  wait-healthy.sh
```

**发布包布局映射**：`docs/05` 描述的是**发布包**（tar.gz）内的布局，不是仓库布局。`scripts/make-release.sh` 负责映射：

| 仓库内 | 发布包内 |
| --- | --- |
| `backend/app/` | `app/` |
| `backend/migrations/` | `migrations/` |
| `web/dist/` | `web/dist/` |
| `deploy/` | `deploy/` |
| `scripts/` | `scripts/` |

---

## 2. 开发拓扑

| 项 | 值 |
| --- | --- |
| 后端监听 | `127.0.0.1:8000` |
| 前端监听 | `127.0.0.1:5173` |
| 前端访问后端 | **Vite dev proxy**：`/api` → `http://127.0.0.1:8000` |
| CORS | **不启用**。浏览器视角同源（dev 走 proxy，prod 后端托管 SPA），因此后端**不得**添加 CORSMiddleware |
| 包管理器 | 前端用 **npm**（与 `docs/05` §3.8 的 `npm ci` 一致），不用 pnpm/yarn |
| Python | 后端用 **`backend/.venv`**，`python3.11 -m venv` 创建 |
| 前端构建 | `npm ci && npm run build` → 产物 `web/dist/` |

**Cookie 与代理的配合**：refresh token 的 `Path=/api/v1/auth`。由于走同源代理，`SameSite=Lax` 即可，**不需要** `SameSite=None`，也**不需要** `Secure`（dev 是 http）。用环境变量控制：`COOKIE_SECURE=false`（dev）/ `true`（prod）。

---

## 3. 认证握手（冻结 —— 双方最容易在这里对不上）

### 3.1 access token 的存放位置

**前端把 access token 只放在内存（模块级变量 / React Context），绝不写入 localStorage 或 sessionStorage。**

理由：localStorage 可被任何 XSS 读取；内存中的 token 在页面刷新后自然丢失，攻击面显著更小。

代价：**页面刷新后必须能恢复会话**，靠下面的启动流程。

### 3.2 完整流程

```
① 登录
   前端 POST /api/v1/auth/login {username, password}
   后端 200 → {access_token, token_type:"bearer", expires_in, user:{...}}
   后端同时 Set-Cookie: refresh_token=<opaque>; HttpOnly; SameSite=Lax;
                        Path=/api/v1/auth; Max-Age=604800
   前端：access_token 存内存，user 存内存，跳转到 ?redirect= 或 /

② 页面刷新 / 首次挂载（关键）
   前端启动时 access_token 为空 → 立即 POST /api/v1/auth/refresh（凭 Cookie）
     成功 200 → {access_token, token_type, expires_in, user}   ← 前端据此恢复会话
     失败 401 → 视为未登录，跳 /login
   在这个请求返回之前，路由守卫必须处于「loading」态，不能先渲染登录页
   （否则用户每次刷新都会被闪一下登录页）

③ 业务请求
   前端 Authorization: Bearer <access_token>

④ access token 过期（401 TOKEN_EXPIRED）
   前端拦截 → 静默 POST /api/v1/auth/refresh → 成功则用新 token 重放原请求一次
   → 失败或重放仍 401 → 清空内存状态，跳 /login
   并发请求同时 401 时，只允许发起一次 refresh（用单例 Promise 去重），
   其余请求 await 同一个 Promise，避免刷新风暴

⑤ 登出
   前端 POST /api/v1/auth/logout → 后端吊销该 refresh token 并清 Cookie
   → 前端清空内存状态，跳 /login

⑥ 强制改密
   登录响应中 user.must_change_password=true → 前端跳 /change-password
   任何业务接口返回 403 PASSWORD_CHANGE_REQUIRED → 前端同样跳 /change-password
   改密成功后：清空内存状态，跳 /login（强制用新密码重新登录）
```

### 3.3 后端必须保证的细节

- `POST /auth/refresh` **也要返回 `user` 对象**（前端靠它恢复用户态，避免额外再调 `/auth/me`）
- 用户名错误与密码错误返回**同一个** `INVALID_CREDENTIALS` 与同一句文案（防用户名枚举）
- 账号锁定返回 `423 ACCOUNT_LOCKED`，`details.retry_after_seconds` 为整数
- 账号禁用返回 `403 ACCOUNT_DISABLED`
- 登录成功后 `failed_login_count` 清零
- 用户名大小写不敏感（存归一化小写，查询时也归一化）

---

## 4. 响应信封（冻结）

### 4.1 成功

直接返回资源对象。**不做** `{code:0, data:{...}}` 这种包装。

### 4.2 分页

```json
{ "items": [], "total": 137, "page": 1, "page_size": 24, "pages": 6 }
```

请求参数 `page`（≥1，默认 1）、`page_size`（1~200，默认 20）。门户首页前端固定传 24。

### 4.3 错误（所有非 2xx 统一为此形状）

```json
{
  "code": "VERSION_EXISTS",
  "message": "版本号 1.0.0 已存在",
  "details": { "field": "version", "value": "1.0.0" },
  "request_id": "01HQ8X5K2M9PQR3TVWXYZ4ABCD"
}
```

参数校验失败时 `details.fields` 为数组：

```json
{ "code": "VALIDATION_ERROR", "message": "请求参数校验失败",
  "details": { "fields": [ {"field":"summary","message":"简介不能超过 500 字"} ] },
  "request_id": "..." }
```

`details` 允许为 `null`。**错误码与 HTTP 状态码的完整映射见 `docs/03` 第 4 章，后端不得自行发明新码，前端不得依赖未列出的码。**

### 4.4 请求追踪

每个响应带 `X-Request-Id`。客户端可传 `X-Request-Id`（26 位 ULID 或 UUID）以贯穿调用链。

---

## 5. 数据格式约定（冻结）

| 项 | 约定 |
| --- | --- |
| 时间 | ISO 8601 UTC 带 `Z`，如 `2025-03-14T08:21:33Z`。前端负责转本地时区显示 |
| 字段命名 | 请求与响应一律 `snake_case`（与 Python/DB 一致，不转 camelCase） |
| 可空字段 | 显式返回 `null`，不省略 |
| 布尔 | `true`/`false`，不接受 `0`/`1`/`"true"` |
| 文件大小 | 字节整数（`file_size`），前端负责格式化 |
| ID | 整数。工具对外标识优先用 `slug` |
| 枚举 | 后端用 `enum.StrEnum` 定义一次，Pydantic 复用；前端在 `types.ts` 里用字符串字面量联合类型镜像 |

---

## 6. M1 冻结接口清单

**M1 只实现这些。** 其余接口（工具 CRUD、版本、审批、管理台）在 M2/M3 按 `docs/03` 实现，届时再走变更流程。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/healthz` | `{"status":"ok"}` |
| GET | `/readyz` | 探测数据库可读写 + 存储目录可写 |
| GET | `/api/v1/meta` | 公开配置（见 `docs/03` 3.1） |
| GET | `/api/v1/auth/provider` | 当前认证方式 |
| POST | `/api/v1/auth/login` | 登录 |
| POST | `/api/v1/auth/refresh` | 刷新，返回 `{access_token, token_type, expires_in, user}` |
| POST | `/api/v1/auth/logout` | 登出并吊销 |
| GET | `/api/v1/auth/me` | 当前用户（含 `roles`、`permissions`、`must_change_password`） |
| POST | `/api/v1/auth/change-password` | `{old_password, new_password}` |
| GET | `/api/v1/categories` | 分类列表（含 `is_active` 过滤后的） |
| GET | `/api/v1/tags` | 标签列表，支持 `?q=` 前缀搜索 |
| GET | `/api/v1/tools` | 门户列表，参数与响应见 `docs/03` 3.3。`facets` 键**始终存在**：`page==1` 为对象，翻页为 `null`（聚合只在 `page==1` 计算）。前端类型写 `facets: ToolFacets \| null` |

**M1 不实现**：`/api/v1/tools/{slug}` 详情、任何写接口、任何 `/admin/*`。

前端在 M1 阶段通过 **MSW mock** 覆盖自己的全部页面，**不阻塞**等后端。

---

## 7. 种子数据（M1 联调必需）

后端必须提供幂等命令：

```bash
backend/.venv/bin/python -m app.cli seed-demo
```

要求：

- 幂等，可重复执行
- 创建一个超管：`admin` / `Admin@12345`（`must_change_password=false`，便于联调）
- 创建一个需强制改密的用户：`newbie` / `Newbie@12345`（`must_change_password=true`）
- 创建 4 个分类：研发工具、运维工具、Skill、提示词
- 创建 **8 个工具**，覆盖全部 4 种类型，跨全部 4 个分类，`status='approved'`，`visibility='public'`，各带 1 个版本与若干标签
- 其中至少 2 个工具**不设封面图**（用于验证占位色块）
- 其中至少 1 个工具名称超过 40 个字符、简介超过 3 行（用于验证截断）
- 下载量/浏览量填入随机但确定的值（用固定随机种子，保证每次 seed 结果一致）

---

## 8. M1 联调验收（双方共同的完成标准）

在**不使用任何 mock** 的真实前后端下，以下全部通过：

| # | 场景 | 期望 |
| --- | --- | --- |
| 1 | 后端 `alembic upgrade head` 后 `seed-demo`，启动服务 | `/readyz` 返回 200 且 `journalctl` 无 ERROR |
| 2 | 前端 `npm run dev`，访问 `:5173` | 未登录时跳 `/login`，不出现空白页或报错 |
| 3 | 用 `admin` / `Admin@12345` 登录 | 跳转门户，顶栏显示「管理员」与头像 |
| 4 | **刷新页面（F5）** | 仍在门户，**不闪登录页**，会话通过 refresh 恢复 |
| 5 | 门户展示 8 个工具卡片 | 分类筛选、类型筛选、标签筛选、搜索、排序、分页均生效 |
| 6 | 筛选状态 | 全部体现在 URL query 上，复制 URL 到新标签页能还原同样结果 |
| 7 | 无封面工具 | 显示分类色占位块 + 类型图标，无破图 |
| 8 | 超长名称与简介 | 名称单行省略、简介两行省略，卡片高度不错乱 |
| 9 | 登出 | 回到 `/login`，再刷新不能恢复会话 |
| 10 | 用 `newbie` 登录 | 强制跳 `/change-password`，顶栏导航隐藏 |
| 11 | `newbie` 改密成功后 | 跳 `/login`，用新密码可登录 |
| 12 | 连续 5 次错误密码 | 第 6 次返回锁定提示与倒计时 |
| 13 | 切换深色主题后刷新 | 主题保持，**首屏无白闪** |

**监控方会在每个 checkpoint 亲自复跑 1~13。**

---

## 9. 变更流程

任何一方需要偏离本契约时：

1. **停止**相关实现，不要先写再报
2. 在 checkpoint 报告里以 `待裁决` 条目提出，写明：
   - 要改什么（精确到路径/字段/错误码）
   - 为什么现有约定不可行
   - 提议的新约定
   - 对另一方的破坏面
3. 由监控方裁定并更新本文件后，双方再继续

**禁止**：单方面新增接口、改字段名、改错误码、改响应形状、加依赖后不报告。

---

## 10. Checkpoint 报告格式（双方统一）

在每个里程碑完成时、以及任何阻塞发生时，按此格式回报：

```
[M1-CHECKPOINT] <后端|前端> · <里程碑或模块>
状态: 完成 | 进行中 | 阻塞

本次完成:
  - ...

产出/变更文件:
  - path （新增 | 修改）

执行的验证:
  - <命令> → <结果摘要>（测试通过数/覆盖率/lint 结果/手动验证步骤）

契约偏差: 无 | 有 →
  - 位置 / 现状 / 影响

待裁决: 无 | 有 →
  - 问题 / 备选方案 / 我的建议

下一步:
  - ...
```

**硬性要求**：`执行的验证` 一栏必须贴**真实跑过的命令与输出摘要**。不接受「应该没问题」「已测试」这类无证据表述。

---

## 11. 双方共用的硬约束

| # | 约束 | 来源 |
| --- | --- | --- |
| 1 | 后端语法目标 py311：`ruff` 配 `target-version="py311"`，`requires-python=">=3.11"` | README「Python 版本策略」 |
| 2 | 前端 TS `strict: true`，不用 `any`（必要时 `unknown` + 收窄） | `docs/04` §1 |
| 3 | SQLite 每个连接必须设 `PRAGMA foreign_keys=ON`、`journal_mode=WAL`、`busy_timeout=5000`、`synchronous=NORMAL` | `docs/02` §1.1 |
| 4 | 禁止在数据库事务内做文件 IO / 哈希 / 网络 | README「SQLite 风险说明」第 3 条 |
| 5 | 计数与 `last_used_at` 必须内存聚合批量落库，禁止逐请求 UPDATE | README 第 4 条 |
| 6 | 所有业务路由必须显式声明鉴权依赖，并有一个遍历 `app.routes` 的守卫测试 | `docs/03` §6.2 |
| 7 | 数据库变更一律走 Alembic，禁止手工改表 | `docs/01` NFR-AVAIL-04 |
| 8 | 枚举在 Python 与 TS 各自定义一次并镜像，不散落字符串字面量 | `docs/02` §8 |
| 9 | **不执行 git 命令**（commit/branch/checkout 等）。并发写同一 git 索引会冲突，版本控制由监控方在 checkpoint 统一处理 | 本契约 |
| 10 | 不安装任何未在 `docs` 中出现的重量级依赖（如状态管理库、UI 组件库、ORM 替代品）；引入任何新依赖前必须报告 | 本契约 |
| 11 | 前端 MSW mock 必须由 `VITE_ENABLE_MOCKS` 控制，且**生产构建必须完全剔除**（验证 `web/dist` 中不含 msw 字样） | 本契约 |

---

## 12. M1 边界（明确不做）

| 不做 | 归属 |
| --- | --- |
| 工具详情页 `/tools/:slug` | M2 |
| 工具创建/编辑/上传/版本管理 | M2 |
| 审批流、审批队列、白名单 | M2 |
| 用户/组/分类/标签管理台 | M3 |
| API Token | M3 |
| Skill 包解析与预览 | M2 |
| 文件上传与下载 | M2 |
| 埋点统计落库与外显 | M3 |

M1 的唯一目标是：**一条打通「登录 → 恢复会话 → 门户列表 → 筛选 → 登出」的真实竖切**，外加可部署的骨架。

---

## 13. 监控方在每个 checkpoint 会做的事

1. 按 §8 亲自复跑验收场景
2. 导出 `backend/openapi.json`，与 `docs/03` 及 `web/src/api/types.ts` 三方比对，找出漂移
3. 跑 `ruff check`、`tsc --noEmit`、后端测试、`npm run build`
4. 检查 `web/dist` 里是否残留 mock
5. 检查是否有越界改动（改了不属于自己的目录）
6. 检查是否有未报告的依赖变更
7. 通过后打一次 git commit 作为可回退点
