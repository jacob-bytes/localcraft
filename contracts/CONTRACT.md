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

### 6.1 M2 冻结接口清单（后端先开工，前端在 M2 阶段消费）

M1 的 12 个接口保持不变。M2 新增以下 **38** 个，实现后接口面总数应为 **50**。

**门户 / 阅读侧（7）**

| 方法 | 路径 |
| --- | --- |
| GET | `/api/v1/tools/{slug}` |
| GET | `/api/v1/tools/{slug}/versions` |
| GET | `/api/v1/tools/{slug}/versions/{version}/skill-preview` |
| POST | `/api/v1/tools/{slug}/download-ticket` |
| GET | `/api/v1/tools/{slug}/download` |
| GET | `/api/v1/tools/{slug}/stats` |
| GET | `/api/v1/images/{image_id}` |

**个人中心（19）**

| 方法 | 路径 |
| --- | --- |
| GET / PATCH | `/api/v1/me/profile` |
| GET | `/api/v1/me/stats` |
| GET / POST | `/api/v1/me/tools` |
| GET / PATCH / DELETE | `/api/v1/me/tools/{tool_id}` |
| POST | `/api/v1/me/tools/{tool_id}/submit` |
| POST | `/api/v1/me/tools/{tool_id}/withdraw` |
| PUT | `/api/v1/me/tools/{tool_id}/acl` |
| GET / POST | `/api/v1/me/tools/{tool_id}/versions` |
| PATCH / DELETE | `/api/v1/me/tools/{tool_id}/versions/{version}` |
| POST | `/api/v1/me/tools/{tool_id}/images` |
| PATCH / DELETE | `/api/v1/me/tools/{tool_id}/images/{image_id}` |
| GET | `/api/v1/me/downloads` |

**审批与治理（12）**

| 方法 | 路径 |
| --- | --- |
| GET | `/api/v1/admin/approvals` |
| POST | `/api/v1/admin/approvals/{tool_id}/approve` |
| POST | `/api/v1/admin/approvals/{tool_id}/reject` |
| POST | `/api/v1/admin/approvals/{tool_id}/offline` |
| POST | `/api/v1/admin/approvals/{tool_id}/relist` |
| POST | `/api/v1/admin/approvals/batch-approve` |
| GET | `/api/v1/admin/approvals/history` |
| GET / POST | `/api/v1/admin/approval-whitelist` |
| DELETE | `/api/v1/admin/approval-whitelist/{user_id}` |
| GET / PUT | `/api/v1/admin/settings` |

**路径参数裁定（监控方）**：版本相关路径**一律用版本字符串**（`{version}`，如 `1.2.0`），不用整数 id。理由：版本号是用户可见、可手写、稳定的标识，`docs/03` §3.5 的 skill-preview 已是此形状。整数 `version_id` 仅作为**请求体字段**或**查询参数**出现（如批准时 `{"version_id": 346}`、下载时 `?version_id=`），不进入路径。`docs/03` §2.4 已据此修订。

**M2 不做**（留给 M3）：`/api/v1/admin/tools*`、`/api/v1/admin/users*`、`/api/v1/admin/groups*`、`/api/v1/admin/categories*`、`/api/v1/admin/tags*`、`/api/v1/admin/tokens*`、`/api/v1/admin/import|export*`、`/api/v1/admin/stats/*`、`/api/v1/admin/overview`、`/api/v1/admin/recycle-bin`。

> `docs/03` §5.2 脚本示例里的 `POST /admin/tools` 与 `POST /admin/tools/{id}/versions`（管理侧代创建）**属于 M3**，是给脚本批量导入用的另一组路径，与 `/me/tools` 并存。

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

---

## 14. M1 Checkpoint 裁定（前端，2025-03）

前端 M1 交付后，监控方对账了 `web/src/api/types.ts` 与真实后端响应（逐字段 dump 比对），
并对开发 agent 提出的 4 处偏差与 7 条待裁决作出如下裁定。**这些裁定是 M2 的输入，不要再议。**

### 14.1 已实测通过的部分（无需动作）

真实前后端联调 24 项检查中 23 项通过。特别确认：

- **refresh cookie 的 `Path=/api/v1/auth` 在真实浏览器下工作正常**（F5 后会话恢复，不闪登录页）
- **React 18 下无 ref 相关警告** —— shadcn 新模板的 React 19 写法回归已确实修复
- `safeRedirectPath()` 实现正确（拒绝非 `/` 开头、`//`、反斜杠、换行）
- `dist/` 中无 MSW 残留，首屏 gzip ≈ 225 KB（预算 500 KB）
- SPA fallback 正确：未匹配深链返回 HTML 由前端路由接管；未匹配 `/api` 路径返回 **JSON 404**

### 14.2 类型对账结果（裁定：以后端为准，补进 `docs/03`）

| 接口 | 前端推断 | 后端实际 | 裁定 |
| --- | --- | --- | --- |
| `GET /auth/me` | 8 字段 | 11 字段（多 `status` / `last_login_at` / `created_at`） | 补进类型，三个字段均为 `string \| null`（`status` 除外，为 `"active" \| "disabled"`） |
| `GET /auth/provider` | 3 字段 | 5 字段（多 `password_change_supported` / `refresh_supported`） | 补进类型，均为 `boolean` |
| `GET /categories` | 8 字段 | 完全一致 | 冻结 |
| `GET /tags` | 4 字段 | 完全一致 | 冻结 |
| 旧密码错误 | `400 VALIDATION_ERROR` + `details.fields[{field:"old_password"}]` | 完全一致 | 冻结 |
| **登出 / 改密成功** | **假定 204 无体** | **`200` + `{"status":"ok"}`** | **以后端为准：一律 `200` + `{"status":"ok"}`**。前端 `request<null>` 忽略响应体，两种都能跑，但类型与文档统一按 200 |

### 14.3 裁定：图片改为签名能力 URL（原待裁决 2）

**问题**：`<img>` 无法携带 `Authorization` 头；refresh cookie 又被限制在 `Path=/api/v1/auth`。
若 `GET /api/v1/images/{id}` 强制鉴权，则**封面与截图在浏览器中永远 404**。这是一个真实的设计缺陷，
不是前端 bug。

**裁定**：复用本项目已有的下载票据模式，把图片 URL 改成**签名能力 URL**。

- 后端在列表/详情响应里下发 `cover_url` / `thumb_url`，形如
  `/api/v1/images/88?variant=thumb&sig=<hmac>`
- `sig` = HMAC-SHA256(从 `SECRET_KEY` 派生的子密钥, `"{image_id}|{variant}"`)，有效期由
  `images.signature_ttl_hours` 控制，**默认 168 小时（7 天）**
- `GET /api/v1/images/{id}` 接受**两条鉴权路径**：有效 `sig`，**或**有效 `Authorization` 头
  （后者供脚本/API 使用）。两者都无 → `404`
- **已知取舍**：工具可见性被收紧后，此前签发的签名在 TTL 内仍有效。这是能力 URL 模型的固有性质，
  用有界 TTL 限制影响面。若需立即失效，把 TTL 调小。
- **M1 阶段**：后端还没实现该接口，`seed-demo` 下发的 `cover_url` 会 404。前端已实现 `onError`
  降级为占位块，**验收 #7「无破图」在任何情况下都成立**，因此不阻塞。
- **M2 阶段**：后端实现签名下发后，M1 的 6 次 404 自然消失。

### 14.4 裁定：内联主题脚本用 sha256 而非 nonce（原待裁决 3）

同意前端的建议 **(b)**。

`dist/index.html` 是静态文件，无法携带每请求 nonce；为注入 nonce 而把 index.html 模板化，
会破坏其可缓存性并让后端托管逻辑变复杂。该内联脚本内容固定，其 sha256 稳定。

**同时修订 `docs/04` §3.4**：把「CSP 里用 nonce 放行」改为「用 sha256 哈希放行」，
并在构建产物校验中记录该哈希（M3 的 nginx CSP 头按此配置）。

### 14.5 裁定：react-router 保持 v6（原待裁决 4）

- 实测 `npm audit --omit=dev` 为 **2 条 moderate**（agent 报告的 3 moderate + 1 high 含 dev 依赖），
  且 `react-router` 6.x **无可用修复**（`No fix available`）
- 纯 SPA 下 SSR hydration 那条公告**不适用**
- 后斜杠开放重定向那条，应用侧已由 `safeRedirectPath()` 覆盖；项目中也未把用户可控字符串
  传给 `<Link to>` 或 `useNavigate`
- **裁定**：M1/M2 保持 v6；**M3 单独安排一次 RR7 升级**（`createBrowserRouter` 用法基本兼容），
  同期修订 `docs/04` §1 的版本号。不把破坏性升级混在功能里程碑里

### 14.6 裁定：扩种子数据以支持 UI 级分页验证（原待裁决 5）

同意「不属前端问题」。M1 的 8 个工具在 12/24/48 档下 `pages` 恒为 1，分页控件不可见。

- **监控方已验证**：用 URL 强制 `page_size=3` 时 `pages=3`，前端第 2 页渲染正常、facets=null 不崩
  （联调 C1/C3 通过）。所以分页逻辑本身是好的，只是 UI 档位看不到
- **M2 裁定**：后端 `seed-demo` 扩到 **26 个工具**（保留原 8 个作为边界子集，新增 18 个普通工具），
  使 12/24/48 档分别得到 3/2/1 页，UI 级分页可验证
- 同时补 3 个作者账号 `zhangsan` / `lisi` / `wangwu`（`Author@12345`，角色 `user`），
  让 mock 与真实数据在「作者多样性」上一致（见 14.8）

### 14.7 裁定：Badge 增加 warning / success 变体（原待裁决 6）

前端的 amber 显式类实现视觉上没问题，但 `docs/04` 多处引用 `Badge variant="warning"`，
且 M2 会大量使用（待审徽标、配额告警、新版待审角标）。

**裁定**：在项目的 `web/src/components/ui/badge.tsx` 中**正式增加 `warning` 与 `success` 变体**
（shadcn 模型下组件源码归项目所有，扩展变体是正确做法），当前 amber 样式即 `warning` 的实现。
不要再用散落的显式颜色类。

### 14.8 裁定：保留 mock 的额外作者账号（原待裁决 7）

**保留** `zhangsan` / `lisi` / `wangwu`。单一作者的 mock 数据不真实，会掩盖「我的工具」过滤、
所有权校验、作者展示等一类问题。这些数据只存在于 mock 中（已实测 `dist/` 无 MSW）。

同时按 14.6 把同名的 3 个账号补进后端 `seed-demo`，使 mock 与真实环境一致。

> `contracts/CONTRACT.md` §7 的「2 个账号」指的是**后端种子**的最小要求（`admin` + `newbie`），
> 现扩充为 5 个（再加 3 个作者）。mock 与后端种子应保持一致，避免联调时出现「mock 有、真实没有」的错觉。

### 14.9 裁定：`npx shadcn@latest add` 的回归必须有守卫（原偏差 1）

前端的处理是对的，但「每次 add 都要重做」是个会持续复发的隐患。

**裁定**：M2 增加一个仓库内检查脚本 `web/scripts/check-ui-primitives.mjs`，断言：

1. 交互型原子组件（`button` / `input` / `select` / `checkbox` / `sheet` / `dialog` /
   `alert-dialog` / `dropdown-menu` / `popover` / `tooltip`）都使用 `forwardRef`
2. `src/lib/utils.ts` 的 `cn` 是本地 `clsx` + `tailwind-merge` 实现
3. `package.json` 中不存在 `cn` 与 `next-themes` 依赖

并把它挂到 `npm run lint` 或 `npm run verify` 里。这样「记得重做」变成「检查会失败」。

### 14.10 裁定：其余偏差

| 偏差 | 裁定 |
| --- | --- |
| 偏差 2：mock 的 refresh cookie 用 `Path=/` | **接受**。mock 无 HttpOnly 存储，且前端从不读 cookie。真实路径已实测正常（14.1） |
| 偏差 3：⌘K 面板跳 `/?q=` 而非 `/tools/:slug` | **接受**，M1 正确取舍（详情页是 M2）。**M2 改为跳 `/tools/:slug`** |
| 偏差 4：个人中心/管理后台以 disabled 项占位 | **接受**。不制造死链是正确做法。M2 启用「个人中心/我的工具」，M3 启用「管理后台」 |
| 偏差 5：依赖增删（radix-ui 伞包、cmdk、shiki、typography、tw-animate-css、oxlint、playwright；移除 cn/next-themes） | **批准**。已核验 dist 无 MSW、首屏预算达标。新依赖用途明确 |
