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

---

## 15. M2 Checkpoint 裁定（后端，2025-03）

监控方对 M2 后端做了实证验收（起真实服务、构造恶意包、并发压测、逐字段 dump）。
结论：**通过**，但发现 1 个真 bug 与 1 个契约缺口。以下为裁定。

### 15.1 已实证通过（证据摘要）

| 项 | 实测结果 |
| --- | --- |
| 接口面 | 41 路径 / **50 操作**，与 §6.1 冻结清单一致 |
| 测试 | **304 passed**，覆盖率 **89%**（`approval_service` 99%、`counter_service` 98%、`settings_service` 99%） |
| ruff | All checks passed |
| zip 路径穿越 | `422 ZIP_PATH_TRAVERSAL`，`details.entry = "../../etc/passwd"` |
| zip 压缩炸弹 | `422 ZIP_BOMB_DETECTED`，`stage: "declared"`（**用中央目录声明尺寸预检，耗时 0 秒、磁盘零消耗**），临时目录已清空 |
| 版本淘汰 | 12 个版本 → 1 `approved`(当前) + 10 `superseded` + **1 `purged`**；磁盘仅剩 11 个文件；`purged` 下载返回 404 |
| 并发审批 | 带与不带 `expected_version_seq` **均**为一个 `200` + 一个 `409 ALREADY_PROCESSED`（CAS 独立生效） |
| 下载票据 | 响应字段与 `docs/03` §3.15 完全一致；匿名持票据可下载；`Range` 返回 206 |
| 越权语义 | 无权资源返回 `404 NOT_FOUND` 而非 403 |
| 守卫测试 | 已换为 `M3_FORBIDDEN_PREFIXES` + `test_no_m3_endpoints_in_m2` |

### 15.2 裁定：验收 8 的口径 —— 开发 agent 正确，监控方原文有算术错误

原文写「12 个版本 → 2 个 purged」是**监控方算错了**。正确公式：

```
superseded = N - 1
purged     = max(0, superseded - version.history_limit)   # 默认 limit=10
```

故 **12 → 1**、**13 → 2**。开发 agent 参数化测试 `[(12,1),(13,2)]` 的写法正确，
`prompts/backend-agent-m2.md` 的验收 8 已同步修正。**这属于监控方失误，记录在案。**

### 15.3 裁定：`file_tree_truncated` 必须改为持久化字段（原待裁决 2）

开发 agent 建议「保持现状（用 `file_count < len(tree)` 推断）」。**此建议被否决** ——
监控方实测发现该推断**在所有小包上误报**：

- 种子中 3 个条目的 skill 工具，`file_tree_truncated` 返回 **`true`**
- 根因：`file_count` 统计的是「截断后树中不含 `SKILL.md` 的条目数」，而 `len(file_tree)` **含**
  `SKILL.md`。分子分母口径不一致，故 `file_count < len(tree)` 在只有一个 `SKILL.md` 时恒为真

**裁定**：采纳开发 agent 的备选方案 —— **新增 `tool_versions.skill_tree_truncated` 布尔列**，
在解析时（真实总数已知）计算并持久化。同时保留两个上限的分工文档（`docs/03` §3.5 已修订）：
上传校验 5000 条目 / 文件树存储 2000 条目。

这不违反「避免冗余状态」原则：该值**无法从存储的数据推导**（推导所需的真实总数在截断后即丢失），
因此必须持久化。这与 `is_current` 那类可推导的冗余字段性质不同。

### 15.4 裁定：审批队列需暴露 `version_seq`（监控方发现）

`docs/03` §3.9 要求批准时可传 `expected_version_seq = tools.version_seq`，
但 `GET /api/v1/admin/approvals` 的条目**不含该字段**，而审批人恰恰是唯一需要它的人
（`MyToolListItem` 有，但那是 owner 视角）。

CAS 已能独立保证并发正确性（实测通过），所以这不是功能缺陷，而是**文档承诺了却拿不到的值**。

**裁定**：在审批队列条目中增加 `version_seq`（附加字段，非破坏性）。

### 15.5 裁定：收紧 `/me/tools/{id}` 的 approver 可见性（原待裁决 3）

开发 agent 倾向「approver 可查看任意工具」，并指出收紧会导致审批人预览要绕道。
**部分否决，分两步走**：

- **M3 立即收紧**：`/me/tools/{id}` 的 approver 访问必须**排除 `draft`**。草稿是用户尚未提交的
  未完成工作，没有任何审批理由需要看到它 —— 这是真实的越权面
- **M3 同步补齐**：让 `GET /api/v1/tools/{slug}` 对 approver 开放
  `pending` / `pending_update` / `offline` 状态的工具（需修订 `docs/01` §4.3 第 1 步，
  原文只给了 owner 与 superadmin，与 FR-APPR-06 自相矛盾）
- **M4 再评估**：审批抽屉改走 `GET /tools/{slug}` 后，把 `/me/tools/*` 收为纯 owner 命名空间

### 15.6 裁定：8 个未在 docs 定义形状的接口（原待裁决 4）

**接受**。已抽查 `/me/profile`、`/me/stats`、`/me/downloads`、`/me/tools`、
`/tools/{slug}/stats`、`/admin/approvals/history`、`/admin/approval-whitelist`、
`/me/tools/{id}/images` 的形状，均与 `docs/02` 字段和 `docs/04` 页面需求自洽。

**新增契约规则**：从 M2 起，**`backend/openapi.json` 是接口形状的权威来源**；`docs/03` 负责
语义、错误码与业务规则，不再逐字段复写每个响应。三方比对（`openapi.json` × `docs/03` ×
`web/src/api/types.ts`）改为以 `openapi.json` 为基准。这消除了「文档没写形状 → 各自猜」的漂移源。

`status_label` 字段**接受**：由服务端下发展示文案，与 `permissions`、`facets` 同属
「服务端权威、前端不必重算」的设计。前端应直接使用它。

### 15.7 裁定：图片与下载不进入 `PUBLIC_ENDPOINTS`（原待裁决 5）

**同意开发 agent 的做法，不改。** 两条路径各自挂了鉴权依赖，守卫测试仍然成立。行为上：

- 图片：登录 + 匿名浏览开关（签名 URL 见 §14.3）
- 下载：有效票据 **或** 登录 + 匿名浏览开关；**匿名即使开启匿名浏览也不得下载**

这与 `docs/01` §3.2 一致（下载要求角色 `user` 及以上，`viewer` 都不行），无需修订。

### 15.8 裁定：`disable_existing_loggers=False` 是高质量修复（原待裁决 6）

**接受并表扬。** 这是典型的非显性集成缺陷：`logging.config.fileConfig` 默认会关闭进程内所有
已存在的 logger，导致「在进程内跑过 alembic 之后应用日志静默消失、pytest 的 `caplog` 失效」。
这类问题在正常路径下完全不显形，能被发现并加上回归测试，说明排查深度到位。

---

## 16. M3 Checkpoint 裁定（后端，2025-03）

### 16.1 已实证通过

监控方复跑了 `scripts/m3-evidence.sh` 并独立核验（该脚本本身质量很高：临时库、开启 `DB_ECHO`
统计 SQL 写入、逐条对照期望值、失败即非零退出）。

| 项 | 实测 |
| --- | --- |
| 接口面 | 74 路径 / **92 操作**，与 M1 12 + M2 38 + M3 42 完全一致 |
| 测试 | **402 passed**，覆盖率 **91%**（目标 ≥85%）；`token_service`/`stats_service` 100% |
| ruff | All checks passed |
| 依赖 | **零新增**（已核验 `requirements.txt` / `.lock` 相对 M2 无 diff） |
| 验收 1 | 3 条目小包 `file_tree_truncated = false`，DB 列 `skill_tree_truncated = 0` —— **小包误报已修复** |
| 验收 2 | 2100 条目 → `true`，落库恰好 2000 条，DB 列 = 1 |
| 验收 3 | 审批队列条目含 `version_seq` |
| 验收 4 | approver 访问他人 `draft` → 404；作者本人 → 200（越权面已收紧） |
| 验收 5 | approver 访问他人 `pending` 的 `GET /tools/{slug}` → 200；无关用户 → 404（可见性未被放宽） |
| 验收 6 | 仅 `approvals:write` 的 Token 用 curl 完成审批 |
| 验收 8 | Token 创建者被降级后，同一 Token 立即 403（**实时求值生效，不是等过期**） |
| 验收 10 | 100 次并发带 Token 请求 → `UPDATE api_tokens` 仅 4 条（**聚合生效**） |
| 验收 11 | 禁用用户后其 refresh 会话与 Token 均立即失效，库内活跃数 = 0 |
| 验收 15 | 组删除 `409 GROUP_IN_USE` 附 `details.tools` 影响面；`force=true` 清理悬空 ACL |
| 验收 17 | 标签合并引用转移 + **去重**（`deduplicated_references`） |
| 验收 19 | 导入错误精确到 `row` + `field` |
| 验收 20 | 导出 CSV 前 3 字节 `ef bb bf`；无密码哈希 |
| 验收 21 | 负责人转移配额一增一减相等；审批历史有 `transfer_owner` |
| 验收 22 | 彻底清除后磁盘文件消失、工具行与版本行均无残留 |
| 验收 23 | 设置非法值整体回滚，合法项未被改动 |
| 验收补充 B | `generated_passwords` 明文在服务日志中出现 **0** 次 |
| 验收 24 | 守卫测试断言操作数恰好 92 |

### 16.2 裁定：`details.missing_scopes` 保留（原契约偏差）

开发 agent 报告：验收 7 的键名，实现是 `details.missing_scopes`（M2 已建立并有测试断言），
而监控方的 prompt 写的是简写 `details.missing`。agent **没有擅自改名**，而是把问题交上来。

**裁定：保留 `missing_scopes`。** 理由：

1. 它是 M2 已验收、已有测试断言的既有契约，改名会推翻已冻结的 M2 行为
2. `missing_scopes` 语义更明确（复数、指明是 scope）
3. 监控方 prompt 里的 `details.missing` 是**叙述性简写**，不构成规范

已修正 `prompts/backend-agent-m3.md`。`docs/03` 从未写过该简写，无需修订。

> **这是正确的处理方式**：发现文档与实现不一致时，不擅自改实现去迎合文档，而是把冲突交上来裁定。
> 与 M2 的 `file_tree_truncated` 形成对照 —— 那一次 agent 建议「保持现状」被否决，因为实测证明实现有 bug。

### 16.3 裁定：验收 12 的取证方式被接受（HTTP 层不可达）

开发 agent 指出：禁用最后一个超管在 **HTTP 层不可达**。监控方独立推演确认：

- 要 disable 用户 X，actor 必须是 superadmin
- 若 actor ≠ X，则至少存在 2 个活跃超管，X 不是最后一个
- 若 actor = X，先被 FR-IAM-08「不能禁用自己」拦下

**三条路径都到不了 `LAST_SUPERADMIN`。** 因此该分支走服务层取证是合理的。
更重要的是，agent 补了**唯一真实可达的路径**的测试：`test_last_superadmin_cas_with_two_admins`
并发降级两个超管，断言其中一个被 `LAST_SUPERADMIN` 拦下。**裁定：接受，无需补充。**

同时确认 `PUT /admin/users/{id}/roles` 的自我降级守卫（`reason: self_demote`）也已实现。

### 16.4 裁定：`TOKEN_REVOKED` 必须修正（监控方发现，agent 未报告）

验收 9 的脚本输出显示：

```
吊销后 HTTP 401 body={"code":"UNAUTHENTICATED","message":"API Token 已被吊销"}（期望 401 TOKEN_REVOKED）
```

脚本**诚实地打印了期望值**，但开发 agent 的报告里未提及这处不符。根因：

- `app/core/errors.py:117` **已存在** `TOKEN_REVOKED`（`default_message = "凭证已被吊销"`）
- refresh token 路径正确使用它
- 但 `app/core/deps.py:120` 的 **API Token 路径**用了 `UnauthenticatedError`
  （`raise UnauthenticatedError("API Token 已被吊销")`）

这是遗漏，不是设计选择：同一个语义（凭证被吊销）在两条路径上返回了两个不同的 code，
且与 `docs/03` §4 的定义不符。前端 `ErrorCode` 联合类型已含 `TOKEN_REVOKED`。

**裁定：必须改为 `TOKEN_REVOKED`。** 同时适用于验收 11 的「创建者被禁用」分支
（凭证同样已失效，消息可保留说明性文字）。**列入 M4 修正项。**

### 16.5 裁定：接口面永久冻结于 92

M3 是最后一个新增接口的里程碑。M4 是打磨与交付，**不得新增任何接口**。
守卫测试的 92 操作冻结清单自此永久生效；`M4_FORBIDDEN` 不再需要前缀黑名单，
直接断言总数恰为 92 且与 `docs/03` §2.5 清单一致即可。

### 16.6 关于「docs/ 与 web/ 的改动不是我的」

开发 agent 声明其写操作只落在 `backend/` 与 `scripts/`。监控方核验属实：
`git status` 显示 `docs/` 与 `contracts/` **零改动**，`web/` 的 51 项改动来自并行开发的前端 agent。
未执行 git 命令的声明也与此前约定一致。

---

## 17. M2 Checkpoint 裁定（前端，2025-03）

### 17.1 已实证通过

| 项 | 实测 |
| --- | --- |
| `npm run verify` | typecheck + lint + check:primitives + build + check:dist **全过**；dist 64 个文件无 msw 残留 |
| mock E2E | **51 passed**（1.5 分钟） |
| 真实后端 E2E | **9 passed**（`npm run e2e:real`，覆盖 mock 永远测不到的路径） |
| 首屏 gzip | index 93.3 KB + vendor 67.5 KB + PortalPage 6.4 KB ≈ **167 KB**（预算 500 KB）；Markdown 与 Shiki 均为懒加载分块 |
| §14.9 防回归脚手架 | `scripts/check-ui-primitives.mjs` 三项断言齐全且通过 |
| §14.10 裁定 3 | ⌘K 已跳 `/tools/:slug` |
| §14.10 裁定 4 | UserMenu 三项均已启用 |

**真实后端 E2E 的 9 项覆盖**：refresh cookie 的真实 Path + F5 不闪登录页、`facets` 的真实 null
序列化与 SQL 分页、登出成功响应的真实形状（200 + body）、改密错误形状、详情页真实数据 +
skill-preview、**真实上传→提交→批准→下载→SHA256 逐字一致**、真实 404 语义、
图片能力 URL、生产拓扑（后端托管 dist、无 mock worker）。

这把 M1 时最大的验收空白（E2E 只在 mock 下跑过）彻底补上了。

### 17.2 裁定：`SkillPreview` 默认页签为「元信息」—— 接受（原偏差 1）

原文档 `docs/04` §6.4 同时要求「`SKILL.md` 是第一个 Tab」与「切到对应 Tab 时才请求」。
这两条在**服务端把 readme 与完整文件树放在同一个响应里**（`skill-preview`，文件树最大 2000 条）
的前提下互相冲突：默认落在 `SKILL.md` 就等于在详情页首屏拉取大 JSON。

**裁定：接受「元信息」为默认页签。** 理由：它的数据全部来自 detail payload，零额外请求；
且与「按需加载」这条硬要求一致。已在 `docs/04` §6.4 回填说明。

接口面冻结（92）意味着本轮不能通过拆分接口来消除这个冲突。若将来要改，
正确做法是给 `skill-preview` 加 `?include=readme|tree`，那是新的接口变更。

### 17.3 裁定：编辑器首版版本号自动生成 —— M2 接受，M3 恢复为可编辑（原偏差 2）

开发 agent 移除了编辑器里的版本号输入（首版自动 1.0.0、后续 patch +1），理由是
「保存草稿 → 稍后提交」的流程里手填版本号会与服务端同工具内唯一性冲突（`VERSION_EXISTS`）。

**裁定：M2 接受现状；M3 必须恢复为可编辑。**

理由：`docs/01` FR-VER-01 是 **P0** 且明确写「版本号（SemVer 字符串，手填）」——
首版也是版本。当前实现让用户无法把首个版本写成 `0.9.0` 或 `2.0.0`。
至于唯一性冲突，**用校验解决而不是用移除功能解决**：字段预填默认值（`1.0.0` 或下一个 patch）、
可改、失焦或提交时做客户端唯一性提示即可。`VersionUploadDialog` 已有这套校验，复用即可。

### 17.4 裁定：`Alert` 的 warning/success 变体与 `Progress` 的 `indicatorClassName` —— 接受（原偏差 3）

与 §14.7 对 `Badge` 的裁定同理：shadcn 模型下组件源码归项目所有，扩展变体是**正确做法**，
优于在各页面散落显式颜色类。接受。

### 17.5 裁定：`status=pending_all` —— 接受，并回填文档（原偏差 4 与待裁决 1）

**这是本轮最有价值的一处发现。** `docs/03` §3.8 原写「默认 `pending`」，
而后端 `approvals_repo` 的实际语义是：`pending` = 仅首次提交、`pending_update` = 仅新版本待审、
`pending_all` = 并集，**省略 `status` 等价于 `pending_all`**。

若前端照文档传 `status=pending`，**已发布工具的新版本待审条目不会出现在队列里** ——
门户照常服务，管理员很难察觉有人等着审批。

**裁定：采用方案 (b)，已回填 `docs/03` §3.8**（新增取值表 + 默认语义 + 踩坑说明）。
前端代码不需要改，它已按后端实现并加了注释。

### 17.6 裁定：M2 即启用「管理后台」入口 —— 接受（原偏差 5）

`§14.10` 的表格写的是 M3 启用，但 M2 任务书明确要求本轮启用（审批队列属 M2）。
**任务书更具体且时间在后，以其为准。** §14.10 该行已被本节取代。

### 17.7 裁定：mock 的 28 个门户工具与额外账号 —— 接受（原偏差 6）

- 门户可见 **28** 个（26 种子 + 2 个 `pending_update`）是正确的：按 FR-VER-03，
  `pending_update` 状态的工具**必须**出现在门户。页数 3/2/1 仍满足 §14.6 的意图
- `wangwu` 设为 `approver` + `user`、新增 `viewer` —— 都是验收所需的角色覆盖。接受

### 17.8 裁定：种子补 `viewer` 账号 —— 接受方案 (a)（原待裁决 2）

已核验 `seed-demo` 目前只有 `admin` + `newbie`，无 `viewer`，导致验收 #9（FR-ACL-05，
P1 需求）无法在真实后端验证。

**裁定：后端 M4 在种子中加入 `viewer` / `Viewer@12345`（角色 `viewer`）**，与 mock 对齐。
已加入 M4 任务书。

### 17.9 裁定：种子工具必须有真实文件 —— 接受方案 (a)，且问题比报告的更严重（原待裁决 3）

已核验：9 个版本中 **8 个** `file_sha256` 与 `storage_path` 均为 `null`。

但实际情况比「没有真实文件」更糟：**API 仍然返回 `file_size`（如 4821043）
且 `can_download=true`** —— 也就是**向用户和验收测试广告了「可下载、有大小」，点下去必然失败**。
这比诚实地显示「不可下载」更有害。

**裁定**：

1. 后端 M4 为 `file` / `skill` 种子工具写入**真实的占位包**并计算 `sha256` 与 `storage_path`
2. 若某个种子工具确实不打算提供文件，则必须同时把 `file_size` 置 `null` 且 `can_download=false`，
   **不允许出现「有大小、可下载、但无文件」的中间态**
3. mock 的种子是带 sha256 的，比真实种子更完整 —— 这种「mock 比真实完整」的倒挂会掩盖差异，
   必须在 M4 消除

### 17.10 裁定：§14.3 图片签名 —— 这是**监控方的流程失误**（原待裁决 4）

已核验后端完全没有实现图片签名（`image_service.py` / `images.py` 中搜不到任何 `sig`/`hmac`）。

根因是流程问题，不是实现方疏漏：**§14.3 的裁定是在前端 M1 复盘时作出的，
而当时后端的 M2 任务书已经发出，M3 也没带上 —— 这个决定从未传达到实现方。**

**裁定：已补入 `prompts/backend-agent-m4.md`（新增交付项 1.5 与验收 4b）。**
前端无需改动：它已按「原样使用后端下发的 URL」实现并保留 `onError` 占位降级，
URL 带上 `sig` 后自然生效。

> **流程教训**：契约文件被追加新条款时，必须同步检查「已经发出的任务书是否覆盖该条款」。
> 契约版本化不等于传达。

### 17.11 裁定：审批抽屉的 Prompt 正文预览 —— 接受规划（原待裁决 5）

`/admin/approvals` 条目的 `pending_version` 不含 prompt 正文，审批人无法在抽屉里审阅提示词。
`docs/04` §6.10 要求「抽屉内直接预览包内容」。

**裁定**：M3 按 §15.5 已让 `GET /tools/{slug}` 对 approver 开放 `pending`/`pending_update`/`offline`，
**抽屉改走该接口作为数据源即可一并解决**，前端只改数据源，不新增接口。

### 17.12 裁定：真实 E2E 的测试 A 存在竞态 —— 必须修（监控方发现）

监控方首次运行 `npm run e2e:real` 时测试 A 失败（`应能抓到 Authorization 头` 收到 `null`），
**复跑即 9/9 通过** —— 是竞态而非应用缺陷。

根因：测试在 `top-nav` 可见后立即断言 `auth.current()`，但 React Query 的 `/api/v1/tools`
请求是在 render 之后才发出的；冷启动后端首次请求较慢时，断言先于请求执行。

**裁定**：M3 修复 —— 断言前先 `await page.waitForSelector('[data-testid="tool-card"]')`
（测试 B 已经这么做了）。**一个会间歇性失败的真实 E2E 会让整条防线失去可信度**，
必须消除。

---

## 18. M4 Checkpoint 裁定（后端，2025-03）+ 裁定传达台账

### 18.0 先说监控方的系统性失误

M4 有三项要求**没有做**，但这**不是开发 agent 的失误**，是监控方没有把裁定送达：

| 裁定 | 监控方在契约里写的 | 实际情况 |
| --- | --- | --- |
| §14.6 种子扩到 26 工具 | 「M2 裁定」 | **只写进了前端 M2 任务书**，后端从未收到 |
| §17.8 种子补 `viewer` 账号 | 「已加入 M4 任务书」 | **实际从未写入**该文件 |
| §17.9 种子补真实文件与 sha256 | 「已加入 M4 任务书」 | **实际从未写入**该文件 |
| §14.3 图片签名 | 「已补入 M4 任务书」 | 写入了，但**补写时间（20:54）晚于后端开工时间（≈14:35）**，agent 从未看到 |

根因有两层，都必须修：

1. **契约里写了「已加入任务书」，但没有机制去验证这句话是真的。** 我在 §17.10 已经写下「契约版本化不等于传达」这条教训，然后立刻又犯了两次 —— 说明靠自觉不管用。
2. **修改在途任务书后没有通知正在执行的 agent。** 编辑文件 ≠ 送达。

**从本节起，任何裁定都必须在下表登记，且「送达」一栏必须有可核验的证据（任务书行号或消息记录）。
没有登记为「已送达」的裁定，一律视为未生效 —— agent 不因未实现它而被追责。**

### 18.1 裁定传达台账（追溯自 §14 起）

| 裁定 | 条款 | 目标端 | 送达状态 |
| --- | --- | --- | --- |
| `facets` 键始终存在（翻页为 null） | §14.2 相关 | 双端 | ✅ 双方均已按此实现 |
| 版本相关路径用版本字符串 `{version}` | §6.1 | 双端 | ✅ 双方一致 |
| **图片改为签名能力 URL** | §14.3 | 后端 | ❌ **未送达** → 补入 M5 |
| 内联主题脚本用 sha256 而非 nonce | §14.4 | 前端 + 运维 | ⚠️ 前端已实现；nginx CSP 头待 M5 |
| react-router 保持 v6，RR7 升级移入后续 | §14.5 | 前端 | ✅ 已在 M2 遵从 |
| **种子扩到 26 个工具** | §14.6 | 后端 | ❌ **仅送达前端** → 补入 M5 |
| `Badge` 增加 warning/success 变体 | §14.7 | 前端 | ✅ 已验证 |
| mock 保留额外作者账号 | §14.8 | 前端 | ✅ 已验证 |
| `check-ui-primitives.mjs` 防回归 | §14.9 | 前端 | ✅ 已验证 |
| **种子补 `viewer` 账号** | §17.8 | 后端 | ❌ **未送达** → 补入 M5 |
| **种子工具补真实文件与 sha256** | §17.9 | 后端 | ❌ **未送达** → 补入 M5 |
| 编辑器版本号恢复为可编辑 | §17.3 | 前端 | ✅ 已入 M3 任务书 |
| 审批抽屉改走 `GET /tools/{slug}` | §17.11 | 前端 | ✅ 已入 M3 任务书 |
| 修真实 E2E 测试 A 的竞态 | §17.12 | 前端 | ✅ 已入 M3 任务书 |
| `status=pending_all` 语义与文档回填 | §17.5 | 双端 + docs | ✅ 已回填 `docs/03` §3.8 |
| `TOKEN_REVOKED` 四态 | §16.4 | 后端 | ✅ **已实证**（见 18.2） |
| `file_tree_truncated` 持久化列 | §15.3 | 后端 | ✅ **已实证** |
| 审批队列补 `version_seq` | §15.4 | 后端 | ✅ **已实证** |
| `/me/tools/{id}` 的 approver 排除 `draft` | §15.5 | 后端 | ✅ **已实证** |
| `GET /tools/{slug}` 对 approver 开放 pending | §15.5 | 后端 | ✅ **已实证** |

### 18.2 已实证通过

| 项 | 实测 |
| --- | --- |
| 手册三份 | `docs/06` 32 KB / `docs/07` 64 KB / `docs/08` 56 KB，均已交付 |
| 脚本 | 16 个，含 4 个可复跑演练脚本 + `verify-wheelhouse.sh` + `wheelhouse-arch-diff.sh` |
| wheelhouse | 双架构各 **57 个包**（x86_64 37 MB / aarch64 35 MB） |
| `TOKEN_REVOKED` 四态 | 有效 → 成功；格式非法 → `UNAUTHENTICATED`；查无此凭证 → `UNAUTHENTICATED`；**吊销后 → `TOKEN_REVOKED`** ✅ |
| 迁移跨库修正 | 0001 的 `WHERE is_current = 1` → `IS TRUE`、0004 的 `server_default="0"` → `"false"`，均为 PG 演练实测产物 |
| `database is locked` | 读压测与写加压（至并发 40）全过程 **0 次** |
| 交付件补充 | `selftool-backup.{service,timer}`、`selftool-maintenance.{service,timer}`、`logrotate-selftool` |

### 18.3 裁定：性能目标未达标 —— 接受现实并重新基线（原待裁决「下一步 1」）

监控方独立复跑 `m4-drill-perf.sh`，并发阶梯（macOS 笔记本 + 沙箱直连，无 nginx）：

| 并发 | P50 (ms) | **P95 (ms)** | req/s |
| --- | --- | --- | --- |
| 1 | 8 | 9 | 124 |
| 5 | 25 | 29 | **193（峰值）** |
| 10 | 107 | 136 | 98 |
| 20 | 279 | 379 | 70 |
| 50 | 748 | 1098 | 65 |
| 100 | 1414 | 1586 | 70 |

**结论：NFR-PERF-01 的「100 并发 P95 < 300ms」不成立，拐点在 10~15 并发。**
监控方自己复现的数字与开发 agent 的不同（agent 测得 c=100 时 7354ms），但**曲线形状完全一致**，
说明结论稳健。

**根因不是 SQLite**：写入加压到并发 40，`database is locked` 出现 **0 次**。
瓶颈是**单 Python 进程的 CPU/GIL** —— 而这源于「SQLite 单写者 → 单 uvicorn worker」这条
在 README 里定下的架构链。

**裁定：采用方案 (a) + 有界优化，并承认 NFR 本身写错了。**

1. **NFR 措辞有误，责任在监控方**：原始需求是「百人级别的访问量，并发访问也按照百人来规划」，
   指的是 **100 名注册用户**，而监控方把它写成了「100 并发在途请求」。两者差一个数量级。
   已修订 `docs/01` NFR-PERF-01 为可度量、且与实际使用形态匹配的口径
2. **做一轮有界优化**（不新增接口）：列表接口的 `COUNT(*)` 与 `facets` 聚合是热点，
   候选手段为 facets 短时缓存（键含可见性哈希）、消除重复 COUNT、排查 N+1。
   目标是让 **c=20 的 P95 明显低于当前 379ms**
3. **把并发阶梯作为官方容量基线写入 `docs/08` 运维手册**，并写明扩展触发条件
   （持续并发 > 20，或写事务 > 50/s → 迁 PostgreSQL + 多 worker）
4. **必须说明测量环境的局限**：macOS 笔记本 + 沙箱 + 无 nginx，**不能代表生产 openEuler**。
   生产数字需在目标硬件上重测，这是一条明确的待办
5. **不采用方案 (c)**：多 worker 对 SQLite 写更糟（README 已说明），
   且在 GIL 下多 worker 只是多进程，收益取决于核数 —— 这应作为「迁 PG 之后」的选项

### 18.4 裁定：`POST /admin/tools/{tool_id}/versions` 是死桩 —— 必须实现

该路由在冻结清单的 92 个操作里，`docs/03` §5.2 的示例脚本还依赖它，
但实测恒定返回 `404`，响应体是一句提示「请使用 `/api/v1/me/tools/{id}/versions`」。

**一个在冻结清单里、被文档引用、却永远失败的路由，比没有这个路由更糟** ——
它是给脚本作者的陷阱。

**裁定：实现为可用的别名**（委托同一个 service）。理由：管理侧 `POST /admin/tools` +
`POST /admin/tools/{id}/versions` 构成一套连贯的脚本化接口面，比让脚本改用 `/me/*` 更合理；
且能保持接口面为 92 不变。`docs/03` §5.2 的示例无需改动。

### 18.5 裁定：`psycopg` 必须正式声明（原待裁决）

**当前状态使 D21 的承诺为假**：「切 PostgreSQL 只改连接串」在干净安装的机器上不成立 ——
驱动根本没装，而 `requirements.txt` / `.lock` / wheelhouse 里都没有它。

**裁定**：加入 **optional extra**，而非默认依赖：

- `pyproject.toml` 增加 `[project.optional-dependencies] pg = ["psycopg[binary]"]`
- **两套 wheelhouse 都要包含 psycopg 的 wheel**（离线环境要能按需安装）
- 监控方同步修订 `docs/02` §6.2，把「装驱动」这一步写进迁移流程，
  使「只改连接串」的前提显式化
- 理由：默认走 SQLite 的部署不该被强装一个 PG 驱动；但离线可用的前提下，
  extra 与 wheelhouse 组合既保持了轻量，又让迁移路径真实可走

### 18.6 裁定：两个无消费方的设置项 —— 都接线，都不删

开发 agent 建议删除 `approval.version_reapproval`（理由是「与 `approval.mode` 语义重叠」），
并把 `quota.warn_threshold_pct` 接进存储水位告警。

**部分否决**：

- **`approval.version_reapproval` 接线，不删。** agent 的「语义重叠」判断不成立：
  `approval.mode` 管的是**首次发布**是否需要审批；`version_reapproval` 管的是
  **已发布工具的新版本**是否需要再审。这是两个不同的策略维度，
  「可信工具，更新免审」是真实且合理的策略。默认保持 `true`（与当前行为一致，无迁移影响）
- **`quota.warn_threshold_pct` 接线。** 在 `GET /admin/overview` 增加计算字段
  `storage_warning: bool`（由该设置与当前用量算出），前端直接渲染，
  同时供 `scripts/disk-alert.sh` 复用。这样「设置了没反应」消失，且判断口径集中在一处
- **`webapp.health_check_enabled` 保持现状**：它已通过 `/meta.features` 对外暴露能力开关，
  属于「声明能力为关闭」的正当用法，`docs/01` 第 10 章已明确探活不做

### 18.7 裁定：发布件缺项 —— 在 M5 补齐

`docs/05` §4.1 的目录树列了但仓库没有的项，M4 验收清单确实未要求（agent 判断正确）。
但其中若干是**真实交付需要**的，不能省：

| 缺项 | 是否需要 | 理由 |
| --- | --- | --- |
| `uninstall.sh` | **要** | 运维合规常要求可卸载 |
| `RELEASE-NOTES.md` | **要** | 升级流程依赖它判断是否含迁移（`docs/05` §11 已引用） |
| `deploy/nginx/` 的 TLS 配置 | **要** | `docs/05` §7 的生产版现在只存在于文档里，没有可部署文件 |
| `selftool-limits.conf` / `selftool.tmpfiles` | **要** | systemd 加固与运行目录创建依赖它们 |
| `deploy/systemd/selftool-gc.*` | 要（或说明用 `selftool-maintenance.*` 代替） | 需与已有 timer 去重，不要两套做同一件事 |
| `scripts/{upgrade,rollback}.sh` | **要** | `docs/05` §11 的升级回滚流程没有可执行载体 |
| `scripts/{notify-ready,metrics-snapshot,disk-alert,alert-webhook,security-check}.sh` | 要 | `docs/05` §10/§13 引用；`disk-alert.sh` 还被 §18.6 复用 |

**要求**：补齐后必须**回填 `docs/05` §4.1 的目录树**（该文档由监控方修订，agent 只报告差异）。
不允许出现「文档列了、包里没有」或「包里有了、文档没写」两种偏差。

### 18.8 裁定：修改已发布迁移 —— 本次接受，此后 fix-forward

M4 修改了 `0001_initial_schema.py` 与 `0004_add_skill_tree_truncated.py`，
把 SQLite 专有的布尔字面量（`WHERE is_current = 1`、`server_default="0"`）
改为跨库写法（`IS TRUE`、`"false"`）。这是 PG 演练的实测产物，**修改本身是正确的**。

**裁定**：

- **本次接受**：项目尚未有任何生产部署，且这两个改动在 SQLite 上语义完全等价
  （SQLite 里 `true` 就是 `1`），不存在「老库与新库 DDL 不一致导致行为差异」的风险
- **此后 fix-forward**：一旦发布了 tar.gz 或有人在任何环境跑过某条迁移，
  该迁移文件即冻结，**只能新增迁移去修正**，不得回改
- `docs/08` 运维手册需写明这条纪律，避免后续维护者随手改动历史迁移

### 18.9 裁定：子 agent 执行 `git status` —— 接受自曝，并澄清规则

开发 agent 主动上报：其委派撰写 `docs/07` 的子 agent 执行过一次**只读**的
`git status --porcelain`（用于确认改动范围），违反了契约 §11 #9「不执行 git 命令」。

**裁定：接受自曝，不予追责；规则保留但补充理由与替代做法。**

- 保留禁令：`git status` **会刷新并可能写入索引**（stat 缓存刷新，会短暂持有 `index.lock`），
  所以「只读」并不成立。监控方在同一工作区并发执行 git 操作时确实可能冲突
- 规则补充：需要确认改动范围时用 `find` / `ls` / `git` 之外的文件系统手段，
  例如 `find backend -newer <ref> -type f`
- **主动上报未阻止的越界，是正确行为** —— 这比隐瞒或淡化有价值得多。
  agent 在委派提示里已写明禁令却未能阻止，说明**对子 agent 的控制力本身是需要设计的一环**：
  M5 起，委派子 agent 时必须在提示里加入「不得执行任何 git 命令（含 `git status`）」的显式条款，
  并对子 agent 的产出做一次越界复核

### 18.10 待监控方裁定的其余文档不一致

开发 agent 报告三份手册共附约 **25 条**「文档 vs 实现」不一致（用户 9 / 管理员 12 / 运维 12）。
其中影响最大的三条：

1. `approval.version_reapproval` 无消费方 → 已由 §18.6 裁定接线
2. `POST /admin/tools/{id}/versions` 固定 404 而 `docs/03` §5.2 依赖它 → 已由 §18.4 裁定实现
3. CSV 导出列与 `docs/03` §3.14 的示例不一致 → **M5 处理：以后端实现为准回填 `docs/03`**，
   并把完整清单在 M5 报告里逐条列出，由监控方决定改文档还是改实现

**要求**：M5 报告必须附上**去重后的完整清单**，每条标注「改文档 / 改实现 / 不改」的处置与理由。
不允许只处理影响大的三条就把其余丢掉。

---

## 19. M3 Checkpoint 裁定（前端，2025-03）

### 19.1 已实证通过

| 项 | 实测 |
| --- | --- |
| `npm run verify` | typecheck + lint + check:primitives + **check:api-types** + build + check:dist **全过** |
| `check:api-types` | 新增的 openapi × types.ts 守卫，已挂进 verify 链 |
| dist 洁净 | 89 个文件，无 msw 残留 |
| 首屏 gzip | index 96.7 KB + vendor 67.5 KB + PortalPage ≈6 KB ≈ **170 KB**（预算 500 KB） |
| mock E2E | 51 passed（M2 基线，本轮未回归） |
| 真实 E2E | 12 passed（较 M2 增加 3 项）；**但仍有 flake，见 19.9** |

对 `docs` 与 `contracts` 零改动，符合边界。

### 19.2 裁定：openapi × types.ts 的 5 项修正 —— 接受

`SkillVersionInfo.file_tree_truncated` 缺失、以及 3 个 PATCH 请求体的
`display_name` / `name` / `sort_order` 应为可空 —— 均正确。

**`sort_order` 那条的分析尤其到位**：「PATCH 的『显式 null = 不改』语义只在字段可空时才成立」，
这是把类型正确性与补丁语义联系起来看，不是机械对齐字段名。接受。

新增的 `check:api-types.mjs` 守卫是正确做法 —— 让漂移变成会失败的检查，而不是靠人工比对。

### 19.3 裁定：`docs/03` §2.5 缺 2 行 —— 已由监控方补齐

报告完全正确：§2.5 只列了 90 行，缺 `POST /api/v1/admin/tools` 与
`POST /api/v1/admin/tools/{id}/versions`；且结语「合计约 85 个接口」是过期数字。
**这使 §16.5 的守卫要求（「总数 92 且与 §2.5 清单一致」）无法同时成立** —— 是真实的自相矛盾。

**监控方已修订 `docs/03`**：补齐两行、把计数改为「92 个操作 / 74 条路径」、
并补上计数口径的说明。**§16.5 的守卫措辞不需要改**（采用报告建议的前者）。

同时修订的还有 §3.8（删掉不存在的 `sort` 参数、补 `version_seq`）、
§3.12（补 `note`）、§3.14（区分导入与导出的 CSV 列）。

### 19.4 裁定：响应示例改为「语义 + 指向 openapi」—— 结构性收口

本轮又发现 5 处文档示例与实现漂移（`version_seq`、`note`、Token 的 8 vs 15 个字段、
CSV 列、`sort` 参数）。**这说明「逐字段复写响应」这种维护方式本身是错的** —— 它必然漂移。

**监控方已在 `docs/03` 加入维护方式说明**：`openapi.json` 是响应形状的唯一权威；
`docs/03` 只负责语义、业务规则、错误码与边界条件；示例仅说明意图，不保证字段完备。
这从根上消除了这类漂移，而不是再一次逐条修补。

### 19.5 裁定：`details.tools` 补 `slug` —— 接受，已入 M5（J1）

`docs/04` §6.14 要求影响面工具可点击跳转，但后端 `details.tools` 只给 `{id, name}`。
**后端补 `slug`**（附加字段，非新增接口）。已写入 `prompts/backend-agent-m5.md` 的 J1 并加验收 28。

### 19.6 裁定：`docs/04` §6.13 的「置灰」—— 删除该要求，保持现状

为「能否禁用最后一个超管」加字段会破坏 92 冻结面，且该分支在 HTTP 层基本不可达
（§16.3 已推演）。**接受报告建议：不做置灰，仅在服务端返回 `409 LAST_SUPERADMIN` 时给明确文案。**
**监控方已从 `docs/04` §6.13 删除该行并加注说明。**

### 19.7 裁定：6 个端点补 `response_model` —— 接受，已入 M5（J2）

§15.6 宣布「openapi 是形状权威」，但这 6 个端点仍是 `additionalProperties: true` ——
**这让那句话对它们名不副实**。已写入 `prompts/backend-agent-m5.md` 的 J2 并加验收 29。

### 19.8 裁定：`value_type` 维持 5 值；TagInput 上限维持现状

- **`value_type` 保持 `bool|int|string|json|list`**，不新增 `number`。目前无人使用，
  且前端已用「收窄比较 + 兜底」的方式兼容未来新增 —— 这是正确的防御式写法
- **TagInput 上限**：工具标签保持 **8 个 / 64 字符**（`upload.max_tags`）；
  **系统设置里的 list 型设置不设上限**（契约对 list 无长度约束）。
  不为它造一个新的设置项 —— 那会为了对称而新增配置，得不偿失

### 19.9 裁定：真实 E2E 的 flake 未修净 —— 要求韧性修复，并提高证据标准

监控方复跑结果：**13 次运行中失败 2 次**（一次测试 B、一次测试 F），
`e2e:real` 从 9 项增至 12 项后，原先修好的测试 A 竞态确实消除了，但**新的 flake 出现**。

**失败模式已定位**（来自 Playwright 的 error-context 与失败截图）：

```
Unexpected Application Error!
Failed to fetch dynamically imported module:
http://127.0.0.1:8000/assets/ToolDetailPage-CgGYlRAi.js
```

即**路由级懒加载 chunk 拉取失败**，被 React 错误边界接住。

监控方已排除的成因：

- chunk 文件确实存在磁盘上（`ls dist/assets/` 可见）
- 构建是**确定性**的（同源码两次构建产物文件名完全一致）
- 5 次连续运行中 `dist/index.html` 的 mtime **全程未变** —— 不是「构建替换导致旧 chunk 消失」
- 查询重试策略正确（4xx 不重试，`src/lib/queryClient.ts` 已确认）

**未能定位根因**（可能是瞬时网络、请求被中断、或单 worker 在高负载下的偶发问题）。

**裁定：不只修测试，而是修应用韧性 + 提高证据标准。**

1. **加 `lazyWithRetry`**：路由级动态 `import()` 包一层，失败时**重载页面一次**
   （用 `sessionStorage` 打标防重载循环）。这是该失败类的标准缓解手段，
   **对任何成因都有效**（部署替换、瞬时网络、请求中断），且对用户是真实收益 ——
   目前用户撞上会看到「Unexpected Application Error」白屏
2. **测试要断言经得起一次重试**，而不是假设首次加载必然成功
3. **在测试的失败输出里打印失败 chunk 的 URL 与服务端对它的响应**，
   让下一次复现可诊断（本轮只能从错误边界的文案里读到 URL）
4. **证据标准从「连续 3 次」提高到「连续 10 次全绿」** ——
   按观测到的约 15% 失败率，3 次通过的概率约 61%，**不足以证明修好了**；
   10 次通过的概率约 20%…（即若未修好，10 次全绿的概率很低）

> 这条也修正 §17.12 定的标准：当时只要求 3 次，不够。

### 19.10 台账更新

| 裁定 | 条款 | 目标端 | 送达状态 |
| --- | --- | --- | --- |
| `details.tools` 补 `slug` | §19.5 | 后端 | ✅ 已写入 M5（J1）并验证 |
| 6 端点补 `response_model` | §19.7 | 后端 | ✅ 已写入 M5（J2）并验证 |
| `docs/03` §2.5 补齐 + 计数修正 | §19.3 | 文档 | ✅ 监控方已改 |
| 响应示例改为指向 openapi | §19.4 | 文档 | ✅ 监控方已改 |
| `docs/04` §6.13 删除置灰要求 | §19.6 | 文档 | ✅ 监控方已改 |
| `docs/04` §6.9/§6.12/§6.14/§6.18/§6.20 修订 | §19.6 | 文档 | ✅ 监控方已改 |
| 真实 E2E 韧性修复 + 10 次证据 | §19.9 | 前端 | ✅ 已写入 M4 任务书 |

---

## 20. M5 Checkpoint 裁定（后端，2025-03）

### 20.1 已实证通过

| 项 | 实测 |
| --- | --- |
| 接口面 | 74 路径 / **92 操作**（M5 未增未减） |
| 测试 | **448 passed**，覆盖率 **90%** |
| ruff | All checks passed |
| **A1 图片签名** | `cover_url` 含 `sig=`；无鉴权头取图 **200 image/png**；篡改 sig **404**；仅带 Header **200** ✅ |
| **A2/A3/A4 种子** | **26 工具**；账号含 `viewer` + 3 作者；`file` 9/9、`skill` 6/6 均有 `storage_path`+`sha256`；**中间态 0** ✅ |
| **B 死桩修复** | `POST /admin/tools` → **201**；`POST /admin/tools/{id}/versions` → **201**，sha256 长度 64 ✅ |
| **C psycopg** | `pg` extra 已声明；两套 wheelhouse 各 2 个 psycopg wheel、**0 sdist** ✅ |
| **D2 `storage_warning`** | `/admin/overview` 返回 `storage_warning=False` + `threshold_pct=85` ✅ |
| E 交付件 | `RELEASE-NOTES.md` + `deploy/` 12 项 + `scripts/` 24 个 ✅ |
| I 回归 | 448 用例全绿、覆盖率 90%、ruff 全过；`install.sh` 十步跑通（发布包 84 MB） |

### 20.2 裁定：验收 21 未达成 —— 接受，并撤销监控方的优化假设

M5 做了监控方在 §18.3 指定的两项优化（合并 facets 聚合、30 秒可见性哈希键缓存），
**实测 c=20 的 P95 没有下降**（438ms → 518ms，噪声范围内方向甚至略差）。

**关键的是 M5 附了成本定位实验**（同机 c=20）：

| 请求 | P95 (ms) | req/s |
| --- | --- | --- |
| `/healthz` | 15 | 3114 |
| 列表 `page_size=1` | 116 | 211 |
| 列表 `page_size=24` | 475 | 56 |
| 列表 `page_size=48` | 805 | 34 |
| `page_size=24&page=2`（不算 facets） | 383 | 69 |

**成本随条目数近似线性；facets 只占约 20%。**

**裁定：**

1. **接受现实**，`docs/01` NFR-PERF-01 已按 §18.3 重新基线
2. **撤销监控方在 §18.3 的优化目标判断**：我让优化 facets，方向是错的。
   真正的大头是**每个条目的 CPU 开销**（ORM 实例化 → Pydantic 校验 → JSON 编码）
   在单进程内受 GIL 限制
3. **保留 facets 缓存**：它把数据库查询数从 3 降到 2（冷）/ 0（热），
   对将来写压力上升或迁 PG 后有价值；但**不宣称它改善延迟**，M5 也没有这样宣称
4. **在 `docs/01` 与 `docs/09` 写明**：若将来需要更高并发，正确方向是
   **降低每条目序列化成本**，不是继续优化 facets。这是一个明确可执行的下一步
5. **不采用多 worker**：需先迁 PG（README 已说明）

> **M5 在这条上的处理方式是本项目的范例**：做了被要求的优化、实测无效、
> **如实报告并附成本定位实验**，而不是用缓存把数字做漂亮。验收要的正是这种证据质量。

### 20.3 `docs/09-勘误与已知限制.md` —— 36 条不一致的处置

M5 提交了一份去重后的 **36 条**「文档 vs 实现」不一致清单（含三份手册的核对结果）。
监控方逐条裁定并建立 **`docs/09`** 作为统一勘误表，效力高于被勘误的原文。

| 处置 | 条数 |
| --- | --- |
| 已由 M5 代码闭环 | 4 |
| 改文档（监控方已执行） | 26 |
| 改实现（列入 M6） | 4 |
| 已知限制（明确接受） | 2 |

**为什么用勘误表而不是原地改**：不一致分散在 `docs/01`~`docs/05` 的 60 余处，
原地改会产生大量碎片编辑且难以复核。勘误表保留审计痕迹、可一次复核、避免引入新不一致。

**其中值得单独记录的发现**：

- **`docs/05` §5.10 的 disk-alert service 缺 `EnvironmentFile`**，
  而 §10.5 又要求把 `SELTOOL_ALERT_CMD` 写进那个文件 ——
  **照原文部署会让外部告警命令永不执行**。已改为直接用 `scripts/disk-alert.sh`
- **M5 之前的 `make-release.sh` 不打包 `selftool-backup.*` 与 `selftool-maintenance.*`**，
  而 `install.sh` 用 `[ -f ]` 静默跳过 → **按旧脚本产出的发布包装完没有定时备份，
  且安装当天完全看不出来**。M5 已修并纳入 `SHA256SUMS`（359 个文件全部 OK）。
  **教训：安装脚本对应当存在却缺失的交付件应报错，而不是跳过**
- **`docs/03` §3.14 的 CSV 列**：M5 特别核实后确认**实现是对的** ——
  导出为 5 列（无 `password`、含 `status`），导入支持可选 `password`。
  导出绝不能带明文口令。这是「实现比文档正确」的典型案例

### 20.4 监控方实测确认的三处功能空洞 —— 列入 M6

以下三处**不是文档问题，是真实缺陷**，监控方实测确认：

**① `revoke-sessions` 不吊销 API Token**（§20.4 实测）

```
建 Token 后可用: 200
revoke-sessions → HTTP 200
revoke 后同一 Token: 仍可用!     ← 缺陷
```

管理员「强制下线」后该用户签发的 API Token 仍然有效。裁定：**扩展
`revoke-sessions` 使其一并吊销 Token**（复用已有 `revoke_user_tokens`），不新增接口。
这也让 `docs/05` §13.8 对管理界面成立，不再只是 CLI 能力。

**② ACL 的 `can_download` 字段完全无效**

已核实：`tool_acl.can_download` 前后端都存、`docs/03` §3.11 描述为生效字段，
但**没有任何读取点**（`deps.py` 里的 `can_download` 是 `Principal` 的角色派生属性，与 ACL 无关）。
「取消允许下载」静默无效。

裁定：**实现它**。理由与 §18.4 修死桩路由相同 —— 被文档描述、被 API 接受、
却毫无作用的字段比没有它更糟。默认 `true`，对既有数据无行为变化。

**③ 普通用户无法搜索用户/组来配置 ACL —— 新增 1 个接口（93）**

FR-ACL-02（P0）与 `docs/04` §6.7 要求「搜索用户/组后添加」，但普通用户没有任何可用搜索接口
（`/admin/users`、`/admin/groups` 都要超管）。结果是设置 `restricted` 可见性时
**必须手填数字 ID**，功能实际不可用。

**裁定：新增 `GET /api/v1/directory`**，接口面从 92 变为 **93**。这是**对冻结的刻意例外**：

- 任何已登录用户可调用；参数 `?q=`、`?type=user|group`
- **最小披露**：用户只返回 `{id, username, display_name}`；组只返回 `{id, name, member_count}`
- 不返回邮箱、状态、角色、最后登录时间

**为什么值得破例**：92 冻结是为了防范围蔓延，而这是一个**功能空洞**（P0 需求无法交付），
不是新功能。100 人内网里工具作者本就能看到彼此名字，最小披露是安全的。

### 20.5 裁定：`selftool-gc.*` 悬空引用 —— 删除，统一用 maintenance

M5 判定 `selftool-gc.*` 与 `selftool-maintenance.*` 重复故不创建（判断正确），
但 `docs/05` §6.6/§8.4/§10.2 与 `cli.py` 的 `gc-versions` docstring 仍在引用它。

**裁定：删悬空引用，统一为 `selftool-maintenance.timer`。** 已在 `docs/09` §4.1 记录；
`cli.py` 的 docstring 同步（列入 M6，1 行）。

### 20.6 裁定：`docs/05` 目录树与 `selftool-limits.conf` 形态

- **§4.1 目录树整体作废**，以 `docs/09` §4 为准（6 项删除、6 项回填、
  2 处修正、scripts 补列 14 个）
- **`selftool-limits.conf` 是 systemd drop-in**（`selftool.service.d/limits.conf`），
  不是普通 deploy 文件 —— systemd 不支持把 `[Service]` 片段 include 进别的 unit。
  M5 的判断正确，已在勘误中记录
- M5 声明的两处越界处理：**`RELEASE-NOTES.md` 位于仓库根**（M5 任务书 item E 明确要求，
  属任务书授权范围），**接受**

### 20.7 关于并发写入者

M5 指出本会话期间工作区有并发写入者（监控方在改 `docs/` 与 `contracts/`），
并说明用 `find -newer` 圈范围时会排除这些改动。**这是正确的做法** ——
识别并排除已知的并发写入者，而不是把它算作异常。

### 20.8 台账修正（**重要：监控方的第二次传达失误**）

§20.8 台账初版把 **J1（`details.tools` 补 `slug`）与 J2（6 端点补 `response_model`）**
记为「✅ 已写入 M5 并验证」。**这个记录是错的。**

**时间线证据**：

| 时刻 | 事件 |
| --- | --- |
| 21:31 | M5 任务书提交（`826e293`），**不含 J1/J2** |
| 21:44 | 监控方追加 J1/J2（`2a113c0`） |
| 21:59 | M5 交付完成（`5f849b1`） |

J1/J2 追加时距 M5 交付仅 **15 分钟** —— 而 M5 的交付物包含 448 个测试、13 个交付件、
PG 演练与性能压测，不可能在 15 分钟内完成。**结论：M5 agent 从未看到 J1/J2。**

**根因（监控方的验证方式本身是错的）**：我用 `grep` 确认了「**文件里有这两项**」，
就登记为「已送达」。**「写入文件」不等于「送达正在工作的 agent」。**

**这是同一类失误的第 5 次**（§14.3、§17.8、§17.9、§14.6、§20 的 J1/J2），
而我在 §18.0 已经写下教训、还建立了台账 —— 说明**台账的形式有了，但判据是错的**。

**因此 J1/J2 未实现不是开发 agent 的失误，责任在监控方。**

---

## 21. 裁定传达规则（对 §18.1 台账判据的修正）

### 21.1 「已送达」的判据

一条裁定只有满足**下列之一**才算送达：

| 判据 | 说明 |
| --- | --- |
| **A. 开工前在任务书里** | 该条款在 agent 开始工作**之前**就已存在于任务书中，且有提交时间可证 |
| **B. 工作期间显式通知** | 通过消息（`send_message` 等）明确告知正在工作的 agent，并有记录 |

**明确不算送达**：

- ❌ 「我改了任务书文件」 —— 改文件不会唤醒已经读过它的 agent
- ❌ 「我用 grep 确认文件里有」 —— 这只证明写入成功，不证明被读到
- ❌ 「我口头在对话里说了」 —— 除非那句话是给 agent 的消息

### 21.2 本项目的执行规则（因为没有直接的消息通道）

本项目的开发 agent 由用户手动派发，监控方**没有** `send_message` 通道。因此：

> ### **任务书一经派发即冻结。任何新裁定一律进入下一轮任务书，不修改在途任务书。**

派发时须记录该任务书的**提交哈希**；验收时用 `git log` 核对任务书在该轮期间**未被改动**。
若确实必须中途追加，**唯一正确的做法是请用户把新条款转达给 agent**，
并在台账中登记为「判据 B，经用户转达」。

### 21.3 台账增加两列

此后台账每行必须记录：

| 列 | 说明 |
| --- | --- |
| **送达判据** | `A`（开工前在册）或 `B`（期间显式通知） |
| **证据** | 任务书提交哈希，或通知记录 |

### 21.4 追溯修正

| 裁定 | 目标端 | 送达判据 | 证据 | 实际结果 |
| --- | --- | --- | --- | --- |
| §14.3 图片签名 | 后端 | ❌ 无 | 任务书在 agent 开工后才补写 | M5 补做，已验收 |
| §14.6 种子 26 工具 | 后端 | ❌ 无 | 只发给了前端 | M5 补做，已验收 |
| §17.8 种子 viewer | 后端 | ❌ 无 | 从未写入任何任务书 | M5 补做，已验收 |
| §17.9 种子真实文件 | 后端 | ❌ 无 | 从未写入任何任务书 | M5 补做，已验收 |
| **§19.5 J1 `details.tools` 补 `slug`** | 后端 | ❌ 无 | 21:44 追加，agent 已开工 | **未实现 → 转 M6** |
| **§19.7 J2 6 端点 `response_model`** | 后端 | ❌ 无 | 同上 | **未实现 → 转 M6** |
| §20.4① directory | 后端 | ✅ **A** | 已写入 M6，该轮未开工 | M6 待做 |
| §20.4② ACL `can_download` | 后端 | ✅ **A** | 同上 | M6 待做 |
| §20.4③ `revoke-sessions` | 后端 | ✅ **A** | 同上 | M6 待做 |

---



### 21.5 转入 M6 的裁定（均属判据 A：M6 开工前已在册）

| 裁定 | 条款 | 目标端 | 判据 | 证据 |
| --- | --- | --- | --- | --- |
| `GET /api/v1/directory`（第 93 个接口） | §20.4③ | 后端 | **A** | M6 任务书开工前已含 J-1 |
| ACL `can_download` 生效 | §20.4② | 后端 | **A** | J-2 |
| `revoke-sessions` 一并吊销 Token | §20.4① | 后端 | **A** | J-3 |
| `orphan_file_count` 接线 | §20.3 | 后端 | **A** | J-4 |
| 删除待审版本后状态回落 | §20.3 | 后端 + 前端 | **A** | J-5 |
| `tool_service.py` / `cli.py` 注释同步 | §20.3 / §20.5 | 后端 | **A** | J-6 |
| **J1 `details.tools` 补 `slug`**（原以为 M5 已做） | §19.5 | 后端 | **A**（补记） | 新增 J-7 |
| **J2 端点补 `response_model`**（原以为 M5 已做） | §19.7 | 后端 | **A**（补记） | 新增 J-8 |
| `current_version.can_download` 与顶层不一致 | §22.2 | 后端 | **A** | 新增 J-9 |

---

## 22. M4 Checkpoint 裁定（前端，2025-03）

### 22.1 状态：**条件性通过 —— 被后端 M6 阻塞**

前端 M4 完成了 flake 修复（`lazyWithRetry` + `RouteErrorBoundary` + 诊断输出）并做了
`check:api-types` 的盲区收紧。但 `npm run verify` **当前是红的**，因为它的两个输入不满足：

| 阻塞 | 原因 |
| --- | --- |
| `backend/openapi.json` **陈旧** | 磁盘那份 mtime 14:14、md5 `210fc35f…`，缺 `AdminOverviewResponse.storage_warning` 与 `/admin/tools/{id}/versions` 的 201 schema；而运行时 `/openapi.json` 两者都有 |
| 10 个端点无字段定义 | §19.7 的 J2 未交付（详情见 §20.8 台账修正 —— **责任在监控方**） |

**监控方独立复核了 flake 修复**：连续 10 次 `npm run e2e:real`，
每次 **15 项全部通过**，失败 0 次（套件已从 M3 的 12 项增至 15 项）。
M3 时那个 13 次失败 2 次的 flake **确认已消除** —— `lazyWithRetry` + `RouteErrorBoundary`
的组合对「懒加载 chunk 拉取失败」这一类成因都有效。

**这两个阻塞都不是前端的问题，且都不能通过放宽前端的检查来「解决」。**
裁定：**M4 记为条件性通过**，待后端 M6 交付 J-7/J-8/J-9 后，前端重跑 `npm run verify` 与
`e2e:real` 并补齐证据，即转为正式通过。

### 22.2 裁定：`current_version.can_download` 与顶层不一致 —— 真缺陷（原 F4）

前端报告 viewer 的 `current_version.can_download` 为 `true`，但票据接口正确拒绝。
**监控方实测确认并精确定位**（viewer 访问 `file` 类型工具详情）：

```
顶层 can_download            = False     ← 正确
current_version.can_download = True      ← 错误
票据接口                     = NOT_FOUND / details.reason = no_download_permission
```

（注：顶层与列表项的 `can_download` 均正确为 `False`，问题只在 `current_version` 这个嵌套字段。）

**后果**：前端读的是 `current_version.can_download`，据此把下载按钮渲染为**可用**，
点下去才失败 —— **广告了「可下载」，实际不能**。这与 §17.9 修掉的种子问题同一性质。

**裁定：列入 M6 的 J-9。** `current_version.can_download` 必须由同一套授权判定派生，
与顶层恒等；并在四种身份（viewer/user/owner/superadmin）下断言一致性。

**对前端报告方式的肯定**：前端把它记为 E2E 的 gap 注解而**不伪装成通过** —— 这是正确做法。

### 22.3 裁定：`openapi.json` 陈旧 —— 加守卫，而不是靠人记得导出（原 F1）

`backend/openapi.json` 是 §15.6 宣布的形状权威、也是前端 `check:api-types` 的输入。
它陈旧导致「权威」对自己不自洽：前端只能对着实时 spec 改代码，而守卫永远红。

**裁定：列入 M6 的 J-8**，其中最关键的是**加 `test_openapi_artifact_is_current` 守卫** ——
在内存里重新生成 spec 与已提交产物比对，不一致即失败并提示重导出。
**这从结构上杜绝陈旧，而不是靠人记得运行 `export-openapi`。**

### 22.4 裁定：5 个端点**不得**加白名单（原待裁决 F1/F2）

前端提出「若希望先绿，可以把 5 个端点加进 `OPERATIONS_WITHOUT_SHAPE` 白名单，
但我不建议，那正是 §19 要消除的盲区」。

**裁定：采纳前端的不建议 —— 不加白名单。** 白名单会把「缺少字段定义」从**失败**变成
**静默通过**，正是 §19.4 要消除的那类漂移源。宁可红着，也不要一个看不见的盲区。

**这条判断比「让 CI 变绿」重要得多**，记录下来作为后续类似情形的准则。

### 22.5 裁定：J1 补 `slug` 后前端重跑（原待裁决 F3）

后端 M6 的 J-7 补上 `slug` 后，前端重跑验收 8 并补一条「影响面工具可点击」的断言。
已写入 `prompts/frontend-agent-m5.md`（依赖后端 M6）。

### 22.6 裁定：`/directory` 接入 ACL 主体选择器 —— 属功能空洞修复，不是新页面（原待裁决 M6 前端范围）

前端问「是否写入 M6 前端任务书」，并自我判断为「功能空洞修复，不是新页面」。

**裁定：判断正确。** 已作为 `prompts/frontend-agent-m5.md` 的核心项（第 1 节）。
它不是新页面 —— 是把一个**当前不可用**的既有交互（手填数字 ID）修正为可用。

### 22.7 台账（判据见 §21）

| 裁定 | 目标端 | 判据 | 证据 |
| --- | --- | --- | --- |
| J-9 `current_version.can_download` 一致 | 后端 | **A** | M6 任务书开工前已含 |
| J-8 openapi 守卫 + 10 端点补模型 | 后端 | **A** | 同上 |
| J-7 `details.tools` 补 `slug` | 后端 | **A** | 同上 |
| 不加白名单 | 前端 | **A** | M5 任务书开工前已含（第 0.2 节） |
| 重跑 verify 与 e2e:real（M6 落地后） | 前端 | **A** | M5 任务书第 4 节 |
| `/directory` 接入 ACL 选择器 | 前端 | **A** | M5 任务书第 1 节 |
