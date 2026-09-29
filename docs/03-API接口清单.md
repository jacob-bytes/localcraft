# API 接口清单

**项目**：localcraft — 内网工具 / Skill 共享平台
**基础路径**：`/api/v1`
**风格**：REST + JSON
**框架**：FastAPI（自动生成 OpenAPI 3.1 文档）

---

## 1. 通用约定

### 1.1 请求与响应

| 项目 | 约定 |
| --- | --- |
| 传输 | 全站 HTTPS。生产环境 HTTP 一律 301 跳转 |
| 请求体 | `application/json; charset=utf-8`；文件上传用 `multipart/form-data` |
| 响应体 | `application/json; charset=utf-8`（文件下载除外） |
| 时间格式 | ISO 8601 带时区，如 `2025-03-14T08:21:33Z`。**服务端一律返回 UTC**，由前端按本地时区渲染 |
| 字段命名 | 请求与响应统一 `snake_case`（与数据库、Python 一致，减少一层转换） |
| 空值 | 可空字段显式返回 `null`，不省略字段。前端可依赖字段存在性 |
| 布尔 | 真值用 `true` / `false`，不接受 `0`/`1`/`"true"` |
| ID | 整数。对外 URL 中的工具标识优先用 `slug` |

### 1.2 版本化

路径中带 `/v1`。破坏性变更时新增 `/v2`，`/v1` 保留至少两个发布周期。新增可选字段、新增枚举值属于**非破坏性变更**，不升版本。

### 1.3 鉴权

三种方式，按优先级依次尝试：

| 方式 | 头部 | 用途 |
| --- | --- | --- |
| API Token | `Authorization: Bearer st_xxxxxxxx` | 脚本、CI。前缀固定 `st_` |
| JWT | `Authorization: Bearer eyJhbGci...` | 浏览器前端 |
| 会话 Cookie | `Cookie: refresh_token=...` | 仅用于刷新 access token |

服务端通过 Token 前缀区分类型：`st_` 开头走 `api_tokens` 表查哈希；否则按 JWT 解析。**这两种凭证的权限求交方式不同**：

- JWT：用户角色 → 权限点全集
- API Token：Token 的 `scopes` → 权限点子集（**与创建者角色取交集**，即 Token 权限不能超过创建者）

> **重要**：API Token 创建者被禁用或降级后，其签发的 Token 应立即失效或降权。实现上在鉴权时实时读取创建者的当前角色，取交集。不要只在签发时快照权限。

### 1.4 权限点与 Scope 映射

| 操作类别 | 所需角色 | 所需 API Scope |
| --- | --- | --- |
| 门户浏览、详情、搜索 | 任意已登录用户 | `tools:read` |
| 下载工具 | `user` 及以上（`viewer` 除外） | `tools:read` |
| 收藏 / 点赞（`PUT` / `DELETE`） | `user` 及以上（**`viewer` 除外**，契约 §25.1） | `tools:write`。计数是**全站可见的公共数字**，所以不复用只读 Scope |
| 我的收藏（`GET /me/favorites`） | 任意已登录用户（`viewer` 可读，但其收藏夹必然为空） | `tools:read` |
| 个人中心：管理自己的工具与版本 | `user` 及以上 | `tools:write` |
| 审批：批准/驳回/下架/上架、查看队列与历史 | `approver` / `superadmin` | `approvals:write` |
| 分类、标签管理 | `approver` / `superadmin` | `taxonomy:write` |
| 用户管理 | `superadmin` | `users:write` |
| 用户组管理 | `superadmin` | `groups:write` |
| 用户组管理（仅查看） | `superadmin` | `groups:write` |
| 免审白名单、系统设置、API Token | `superadmin` | `settings:write` |
| 全站工具管理（含他人工具）、批量导入导出 | `superadmin` | `admin:all` |

`admin:all` 隐含全部其他 Scope。

### 1.5 分页

**请求参数**：

| 参数 | 类型 | 默认 | 范围 | 说明 |
| --- | --- | --- | --- | --- |
| `page` | int | `1` | ≥ 1 | 页码，从 1 开始 |
| `page_size` | int | `20` | 1 ~ 200 | 每页条数。门户首页前端默认传 24 |

**响应包装**：

```json
{
  "items": [ ... ],
  "total": 137,
  "page": 1,
  "page_size": 24,
  "pages": 6
}
```

`pages` 为 `ceil(total / page_size)`，服务端计算返回，避免前端重复算。

**超过最大页数**时返回空 `items` 而非 404。

### 1.6 排序

统一用 `sort` 参数，取值由各接口定义。格式为 `field` 或 `-field`（前缀 `-` 表示降序）。白名单校验，非法值返回 `400 INVALID_SORT`，**绝不把 `sort` 直接拼进 SQL**。

### 1.7 错误响应

统一格式：

```json
{
  "code": "VERSION_EXISTS",
  "message": "版本号 1.0.0 已存在",
  "details": {
    "field": "version",
    "value": "1.0.0"
  },
  "request_id": "01HQ8X5K2M9PQR3TVWXYZ4ABCD"
}
```

| HTTP | 语义 | 典型 `code` |
| --- | --- | --- |
| 400 | 参数校验失败 | `VALIDATION_ERROR`、`INVALID_SORT`、`INVALID_VISIBILITY` |
| 401 | 未认证 / 凭证失效 | `UNAUTHENTICATED`、`TOKEN_EXPIRED`、`TOKEN_REVOKED` |
| 403 | 已认证但无权限 | `FORBIDDEN`、`SCOPE_MISSING`、`PASSWORD_CHANGE_REQUIRED` |
| 404 | 资源不存在**或无权查看** | `NOT_FOUND` |
| 409 | 状态冲突 | `VERSION_EXISTS`、`ALREADY_PROCESSED`、`LAST_SUPERADMIN`、`CATEGORY_IN_USE`、`GROUP_IN_USE` |
| 413 | 文件过大 | `PAYLOAD_TOO_LARGE` |
| 415 | 文件类型不允许 | `UNSUPPORTED_MEDIA_TYPE` |
| 422 | 语义错误（文件内容非法） | `SKILL_PARSE_FAILED`、`ZIP_BOMB_DETECTED`、`ZIP_PATH_TRAVERSAL` |
| 429 | 请求过频 | `RATE_LIMITED`（**M14 起真实启用**，附 `Retry-After` 响应头，见 §1.11） |
| 500 | 服务端异常 | `INTERNAL_ERROR` |
| 503 | 依赖不可用 | `DATABASE_UNAVAILABLE`、`STORAGE_FULL` |
| 507 | 配额耗尽 | `INSUFFICIENT_STORAGE`、`USER_QUOTA_EXCEEDED` |

**401 与 404 的取舍**：对无权访问的资源返回 **404 而非 403**（SRS FR-FILE-08），避免通过状态码差异探测资源是否存在。但「已认证、角色不足」（如 viewer 尝试上传）返回 **403**，因为这不是隐私问题，明确提示对用户体验更好。

### 1.8 请求追踪

每个响应带 `X-Request-Id` 头。客户端可主动传 `X-Request-Id` 以便贯穿调用链（格式需为 26 位 ULID 或 UUID，非法则服务端重新生成）。该 ID 同时出现在错误响应体与应用日志中。

### 1.9 幂等性

| 场景 | 幂等手段 |
| --- | --- |
| 上传版本 | `POST /me/tools/{id}/versions` 依赖 `(tool_id, version)` 唯一约束天然幂等；重复提交返回 `409` |
| 审批操作 | 通过 `If-Match` 传 `version_seq`，或依赖状态机 CAS（`UPDATE ... WHERE status='pending'`）返回 `409 ALREADY_PROCESSED` |
| 批量导入 | 请求体带 `dry_run: true` 可先预演；`on_conflict` 参数可选 `skip` / `update` / `fail` |

### 1.10 文件下载

`GET /api/v1/tools/{id}/download` **不返回 JSON**，而是：

- 成功：`200` + `Content-Disposition: attachment; filename*=UTF-8''...` + `Content-Length` + `Accept-Ranges: bytes`，走 ASGI FileResponse
- 带 `Range` 头：返回 `206 Partial Content`
- 无权或不存在：`404` + JSON 错误体

**注意**：前端不能直接用 `<a href>` 触发下载，因为需要携带 `Authorization` 头。实现方式有两种，二选一：

1. 先 `fetch` 拿 `blob`，再用 `URL.createObjectURL` 触发下载（简单，但大文件会全部进内存）
2. 后端签发一次性下载票据：`POST /tools/{id}/download-ticket` 返回 `{url, expires_at}`，前端用 `<a>` 打开该短时效 URL（推荐，支持 Range 与浏览器原生下载管理器）

**推荐方案 2**，票据为 HMAC 签名，有效期 60 秒，绑定 `(tool_id, version_id, user_id)`。下载接口在存在 `ticket` 查询参数时跳过 Header 鉴权。

### 1.11 限流

**M14 起真实启用**（`contracts/CONTRACT.md` §29.4）。按**客户端 IP** 做进程内滑动窗口计数，键为 `(scope, ip)`。

命中限流时返回统一错误信封，并**额外带 `Retry-After` 响应头**（与 `X-Request-Id` 同时出现，二者不互斥）：

```json
{
  "code": "RATE_LIMITED",
  "message": "请求过于频繁，请稍后再试",
  "details": { "scope": "api", "limit": 1200, "retry_after_seconds": 12 },
  "request_id": "01HQ8X5K2M9PQR3TVWXYZ4ABCD"
}
```

```
HTTP/1.1 429 Too Many Requests
Retry-After: 12
X-Request-Id: 01HQ8X5K2M9PQR3TVWXYZ4ABCD
```

**三档配额**（值来自系统设置，管理端可改；下表为默认值）：

| scope | 覆盖范围 | 设置键 | 默认 |
| --- | --- | --- | --- |
| `api` | 全部 `/api/v1/*`（挂在路由挂载处，一次覆盖全部 99 个操作，不逐个路由加依赖） | `security.rate_limit_per_minute` | `1200` / 分钟 |
| `login` | `POST /api/v1/auth/login` | `security.rate_limit_login_per_minute` | `10` / 分钟 |
| `upload` | 上传版本包、上传封面/截图、管理侧代上传版本、CSV 导入 | `security.rate_limit_upload_per_minute` | `30` / 分钟 |

总开关 `security.rate_limit_enabled`（默认 `true`）。

**封面图也走 `api` 额度**：`images` 路由在 `/api/v1` 下，一次门户加载约 19 张封面 + `/meta` + `/tools`。这正是 `api` 默认值由 300 上调到 1200 的原因 —— 300/min 时超限的症状是**封面裂图**，会被误当成缺陷上报（`contracts/CONTRACT.md` §30.1）。

**loopback 豁免**：来源为 `127.0.0.1` / `::1` / `localhost` / `testclient` 的请求**不计入限流**（健康检查、运维脚本、测试客户端）。因此测试套件打不到限流，覆盖方式是直接测限流器本身。

**已知限制**：计数是**进程内**的 —— 多 worker 部署下实际配额会按 worker 数放宽。当前部署脚本为 `--workers 1`，实际配额与配置一致（`contracts/CONTRACT.md` §30.3）。

---

## 2. 接口总表

### 2.1 系统与元信息

| 方法 | 路径 | 说明 | 鉴权 |
| --- | --- | --- | --- |
| GET | `/healthz` | 存活探针，直接返回 `{"status":"ok"}` | 否 |
| GET | `/readyz` | 就绪探针，探测数据库读写与存储目录可写 | 否 |
| GET | `/api/v1/meta` | 公开配置：站点名、公告、认证方式、默认排序、是否允许匿名浏览 | 否 |

### 2.2 认证

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/auth/provider` | 当前启用的认证方式与登录表单字段 |
| POST | `/api/v1/auth/login` | 登录 |
| POST | `/api/v1/auth/refresh` | 用 refresh cookie 换新 access token |
| POST | `/api/v1/auth/logout` | 登出并吊销 refresh token |
| GET | `/api/v1/auth/me` | 当前用户信息（含角色、权限点、是否需改密） |
| POST | `/api/v1/auth/change-password` | 修改自己的密码 |

### 2.3 门户（工具读取）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/tools` | 工具列表（搜索、筛选、排序、分页） |
| GET | `/api/v1/tools/{slug}` | 工具详情 |
| GET | `/api/v1/tools/{slug}/versions` | 版本列表 |
| GET | `/api/v1/tools/{slug}/versions/{version}/skill-preview` | Skill 包预览（manifest / readme / 文件树） |
| GET | `/api/v1/tools/{slug}/download` | 下载（支持 `?version_id=` 与 `?ticket=`） |
| POST | `/api/v1/tools/{slug}/download-ticket` | 签发一次性下载票据 |
| GET | `/api/v1/tools/{slug}/stats` | 该工具的统计（浏览量、下载量、趋势） |
| PUT | `/api/v1/tools/{slug}/favorite` | 收藏。**幂等**：已收藏再调仍成功，计数只加一次（契约 §23.4）。响应 `ToolEngagementResponse` |
| DELETE | `/api/v1/tools/{slug}/favorite` | 取消收藏。**幂等**：未收藏再调仍成功，**不报 404** |
| PUT | `/api/v1/tools/{slug}/like` | 点赞。**幂等**，形状与收藏对称 |
| DELETE | `/api/v1/tools/{slug}/like` | 取消点赞。**幂等** |
| GET | `/api/v1/categories` | 分类列表（含各分类可见工具数） |
| GET | `/api/v1/tags` | 标签列表（支持前缀搜索，用于输入联想） |
| GET | `/api/v1/directory` | **主体目录**：ACL 授权时搜索用户/用户组。任何已登录用户可调，**最小披露**（用户只给 id/username/display_name，组只给 id/name/member_count）。M6 新增，是对 92 冻结的刻意例外 —— 见 `contracts/CONTRACT.md` §20.4③ |
| GET | `/api/v1/images/{id}` | 图片获取。**签名能力 URL**：接受 `?variant=full\|thumb` + `?sig=`（HMAC，**签名绑定 `variant`**，后端在列表/详情响应中下发）或 `Authorization` 头，两者都无返回 **404**（不是 403）。详见 §3.16 与 `contracts/CONTRACT.md` §14.3 |

### 2.4 个人中心

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/me/profile` | 个人资料与用量 |
| PATCH | `/api/v1/me/profile` | 修改显示名、邮箱 |
| GET | `/api/v1/me/stats` | 我的统计：工具数、存储用量、配额 |
| GET | `/api/v1/me/tools` | 我的工具列表 |
| POST | `/api/v1/me/tools` | 创建工具（草稿） |
| GET | `/api/v1/me/tools/{id}` | 我的工具详情（含草稿、被驳回的） |
| PATCH | `/api/v1/me/tools/{id}` | 编辑工具元信息 |
| DELETE | `/api/v1/me/tools/{id}` | 软删除工具 |
| POST | `/api/v1/me/tools/{id}/submit` | 提交审批 |
| POST | `/api/v1/me/tools/{id}/withdraw` | 撤回提交 |
| PUT | `/api/v1/me/tools/{id}/acl` | 全量替换可见性授权列表 |
| GET | `/api/v1/me/tools/{id}/versions` | 我的工具版本列表（含待审与已驳回） |
| POST | `/api/v1/me/tools/{id}/versions` | 上传新版本（multipart） |
| PATCH | `/api/v1/me/tools/{id}/versions/{version}` | 修改版本变更说明 |
| DELETE | `/api/v1/me/tools/{id}/versions/{version}` | 删除未过审的版本 |
| POST | `/api/v1/me/tools/{id}/images` | 上传封面或截图 |
| PATCH | `/api/v1/me/tools/{id}/images/{image_id}` | 更新排序、alt 文本、设为封面 |
| DELETE | `/api/v1/me/tools/{id}/images/{image_id}` | 删除图片 |
| GET | `/api/v1/me/downloads` | 我的下载历史 |
| GET | `/api/v1/me/favorites` | 我的收藏。分页（`page` / `page_size`），**复用门户列表项形状**（`ToolListItem`），按收藏时间倒序（契约 §23.4），`facets` 恒为 `null`。可见性走既有规则：被收藏的工具若已下线/转私有/被删除，会从结果里消失。`viewer` 可读（`tools:read`），但其收藏夹必然为空 |

### 2.5 管理后台

| 方法 | 路径 | 说明 | 最低角色 |
| --- | --- | --- | --- |
| GET | `/api/v1/admin/overview` | 管理概览数字 | approver |
| GET | `/api/v1/admin/users` | 用户列表 | superadmin |
| POST | `/api/v1/admin/users` | 创建用户 | superadmin |
| GET | `/api/v1/admin/users/{id}` | 用户详情 | superadmin |
| PATCH | `/api/v1/admin/users/{id}` | 编辑用户 | superadmin |
| POST | `/api/v1/admin/users/{id}/reset-password` | 重置密码 | superadmin |
| PUT | `/api/v1/admin/users/{id}/roles` | 全量替换角色 | superadmin |
| POST | `/api/v1/admin/users/{id}/revoke-sessions` | 强制下线 | superadmin |
| GET | `/api/v1/admin/roles` | 角色列表 | approver |
| GET | `/api/v1/admin/groups` | 用户组列表 | superadmin |
| POST | `/api/v1/admin/groups` | 创建用户组 | superadmin |
| PATCH | `/api/v1/admin/groups/{id}` | 编辑用户组 | superadmin |
| DELETE | `/api/v1/admin/groups/{id}` | 删除用户组 | superadmin |
| GET | `/api/v1/admin/groups/{id}/members` | 成员列表 | superadmin |
| POST | `/api/v1/admin/groups/{id}/members` | 添加成员（支持批量） | superadmin |
| DELETE | `/api/v1/admin/groups/{id}/members/{user_id}` | 移除成员 | superadmin |
| GET | `/api/v1/admin/categories` | 分类列表（含禁用） | approver |
| POST | `/api/v1/admin/categories` | 创建分类 | approver |
| PATCH | `/api/v1/admin/categories/{id}` | 编辑分类 | approver |
| DELETE | `/api/v1/admin/categories/{id}` | 删除分类 | approver |
| PUT | `/api/v1/admin/categories/order` | 批量调整排序 | approver |
| GET | `/api/v1/admin/tags` | 标签列表 | approver |
| PATCH | `/api/v1/admin/tags/{id}` | 重命名标签 | approver |
| POST | `/api/v1/admin/tags/merge` | 合并标签 | approver |
| POST | `/api/v1/admin/tags/cleanup` | 清理零引用标签 | approver |
| GET | `/api/v1/admin/approvals` | 审批队列 | approver |
| POST | `/api/v1/admin/approvals/{tool_id}/approve` | 批准 | approver |
| POST | `/api/v1/admin/approvals/{tool_id}/reject` | 驳回（理由必填） | approver |
| POST | `/api/v1/admin/approvals/{tool_id}/offline` | 下架（理由必填） | approver |
| POST | `/api/v1/admin/approvals/{tool_id}/relist` | 重新上架 | approver |
| POST | `/api/v1/admin/approvals/batch-approve` | 批量批准 | approver |
| GET | `/api/v1/admin/approvals/history` | 审批历史 | approver |
| GET | `/api/v1/admin/approval-whitelist` | 免审白名单 | superadmin |
| POST | `/api/v1/admin/approval-whitelist` | 添加 | superadmin |
| DELETE | `/api/v1/admin/approval-whitelist/{user_id}` | 移除 | superadmin |
| GET | `/api/v1/admin/tokens` | API Token 列表 | superadmin |
| POST | `/api/v1/admin/tokens` | 签发 Token | superadmin |
| POST | `/api/v1/admin/tokens/{id}/revoke` | 吊销 | superadmin |
| DELETE | `/api/v1/admin/tokens/{id}` | 删除记录 | superadmin |
| GET | `/api/v1/admin/settings` | 系统设置 | superadmin |
| PUT | `/api/v1/admin/settings` | 批量更新设置 | superadmin |
| GET | `/api/v1/admin/tools` | 全站工具列表（含所有状态） | approver |
| POST | `/api/v1/admin/tools` | **管理侧代创建工具**（可指定 `owner_id`），供脚本批量导入 | superadmin |
| POST | `/api/v1/admin/tools/{id}/versions` | **管理侧代上传版本**（委托与 `/me/tools/{id}/versions` 同一 service） | superadmin |
| POST | `/api/v1/admin/tools/{id}/transfer` | 转移负责人 | superadmin |
| POST | `/api/v1/admin/tools/{id}/restore` | 从回收站还原 | superadmin |
| DELETE | `/api/v1/admin/tools/{id}/purge` | 彻底清除 | superadmin |
| GET | `/api/v1/admin/recycle-bin` | 回收站列表 | superadmin |
| GET | `/api/v1/admin/stats/tools` | 工具统计排行 | approver |
| GET | `/api/v1/admin/stats/storage` | 存储用量明细 | superadmin |
| GET | `/api/v1/admin/stats/insights` | 数字概览增强（30 日趋势 / 活跃贡献者 / 分类分布 / 效率估算，**纯数字**，契约 §23.6） | superadmin |
| POST | `/api/v1/admin/import/users` | 批量导入用户 | superadmin |
| GET | `/api/v1/admin/export/users` | 导出用户 CSV | superadmin |
| POST | `/api/v1/admin/import/tools` | 批量导入工具元数据 | superadmin |
| GET | `/api/v1/admin/export/tools` | 导出工具 JSON | superadmin |

合计 **99 个操作 / 79 条路径**（M6 新增 `GET /api/v1/directory`，M8 新增收藏/点赞/我的收藏 5 个操作，见下）。

> **计数口径（M3 前端 checkpoint 补入，2025-03）**：本节早先写作「约 85 个接口」是过期数字，
> 且漏列了管理侧代创建的两个接口 —— 这导致「`docs/03` §2.5 清单」与「冻结的 92 操作」无法同时成立，
> 使守卫测试出现自相矛盾的断言。现已补齐。**权威来源是 `backend/openapi.json`**（`contracts/CONTRACT.md` §15.6）。
>
> **计数口径（M18 收尾补入）**：上一版写作「93 个操作 / 75 条路径」同样是过期数字 ——
> 它既没算 M6 的 `GET /api/v1/directory`，也没算 **M8 的 5 个收藏/点赞操作 / 3 条路径**
> （`contracts/CONTRACT.md` §23.4）。当前冻结面是 **99 操作 / 79 路径**（§23 / §35），
> 与 `backend/openapi.json` 逐字一致（`python -m app.cli export-openapi` 导出结果为
> 「79 个路径 / 99 个操作」）。M14~M18 期间**只加字段、不加端点**，所以这个数字不再变。

---

## 3. 关键接口详解

### 3.1 `GET /api/v1/meta`

无需鉴权。用于前端启动时拉取站点配置。

```json
{
  "site_name": "工具与 Skill 平台",
  "site_subtitle": "",
  "announcement_md": "本周五 20:00 进行例行维护。",
  "auth_provider": "local",
  "allow_anonymous_view": true,
  "default_sort": "hot",
  "page_size": 24,
  "app_version": "1.0.0",
  "api_version": "v1",
  "footer_org": "某事业部 · 数字化组",
  "footer_contact_email": "ops@example.com",
  "footer_contact_phone": "8888",
  "footer_notice": "内部系统，请勿外传",
  "footer_tagline": "内网工具与 Skill 共享平台",
  "features": {
    "webapp_health_check": false,
    "skill_preview": true,
    "anonymous_view": true,
    "change_password": true
  }
}
```

**只返回 `is_public = true` 的设置项**。`system_settings` 是带 `is_public` 标记的 KV 表，`/meta` 的数据来源严格限定为公开项 —— 私有项（上传配额、审批开关、`security.rate_limit_*` 限流配置、`portal.allow_admin_view_private` 等）**绝不外泄**（FR-CFG-03）。

**字段 ↔ 设置键映射**（M12 / M13 的 6 个站点定制字段全部走这条既有机制）：

| 响应字段 | 来源 | 默认值 | 备注 |
| --- | --- | --- | --- |
| `site_name` | `portal.site_name` | `工具与 Skill 平台` | 未配置时的回退文案 |
| `site_subtitle` | `portal.site_subtitle` | `""` | M12。空串 = 前端不渲染副标题 |
| `announcement_md` | `portal.announcement_md` | `""` | |
| `allow_anonymous_view` | `portal.allow_anonymous_view` | `true` | 迁移 `0005` 把默认值改为 `true`（M5） |
| `default_sort` | `portal.default_sort` | `hot` | |
| `page_size` | `portal.page_size` | `24` | |
| `footer_org` | `portal.footer_org` | `""` | M12 |
| `footer_contact_email` | `portal.footer_contact_email` | `""` | M12 |
| `footer_contact_phone` | `portal.footer_contact_phone` | `""` | M12 |
| `footer_notice` | `portal.footer_notice` | `""` | M12 |
| `footer_tagline` | `portal.footer_tagline` | `内网工具与 Skill 共享平台` | M13。**默认值刻意不是空串**（定点例外），用于恢复原本写死的标语 |
| `app_version` | 非设置项（版本环境变量） | — | 页脚显示版本时取此字段 |
| `api_version` | 非设置项，固定 `v1` | `v1` | |
| `auth_provider` | 非设置项，当前固定 `local` | `local` | |

**6 个定制字段都是 `str` 且始终返回**（未配置时为空串而不是 `null`），前端可直接按字符串判空，不必处理 `null`。它们全部**恰好是这 6 个**：`site_subtitle`、`footer_org`、`footer_contact_email`、`footer_contact_phone`、`footer_notice`、`footer_tagline`。

前端渲染规则（详见 `contracts/CONTRACT.md` §27.4 / §28.6）：4 个页脚信息字段（运营方 / 邮箱 / 电话 / 备案号）**全部为空时整个页脚信息块不渲染**；`footer_tagline` 与版本行显示在所有 AppShell 页。

**`features` 是可扩展的能力声明**：前端只读取自己需要的键，后端新增键属于非破坏性变更（不升 API 版本）。当前实际返回上述 4 个键，其中 `anonymous_view` 与 `change_password` 供前端决定是否渲染「匿名浏览入口」与「修改密码入口」。`webapp_health_check` 取自**私有**设置项 `webapp.health_check_enabled`（它不是 `portal.*` 公开项，只以这个布尔的形式对外暴露）。

`features` 用于前端根据后端能力决定是否渲染某些区块，避免前后端发布不同步时的白屏。

---

### 3.2 `POST /api/v1/auth/login`

**请求**：

```json
{
  "username": "zhangsan",
  "password": "Str0ng!Passw0rd"
}
```

**成功响应** `200`：

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "expires_in": 1800,
  "user": {
    "id": 42,
    "username": "zhangsan",
    "display_name": "张三",
    "email": "zhangsan@example.com",
    "roles": ["user"],
    "permissions": ["tool:read", "tool:write:own", "download"],
    "must_change_password": false,
    "auth_source": "local"
  }
}
```

同时下发 Cookie：

```
Set-Cookie: refresh_token=<opaque>; HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth; Max-Age=604800
```

**失败响应**：

| 场景 | HTTP | code | message |
| --- | --- | --- | --- |
| 用户名或密码错误 | 401 | `INVALID_CREDENTIALS` | 用户名或密码错误 |
| 账号被禁用 | 403 | `ACCOUNT_DISABLED` | 账号已禁用，请联系管理员 |
| 账号锁定中 | 423 | `ACCOUNT_LOCKED` | 账号已锁定，请 12 分钟后重试。`details.retry_after_seconds` |
| 需改密 | 200 | — | 正常返回，但 `user.must_change_password = true` |

**注意**：用户名错误与密码错误返回**同一个错误码与文案**，避免用户名枚举。账号禁用/锁定则明确提示，因为这对合法用户是必要信息，且攻击者已知道用户名存在（否则不会触发这两个分支）。

---

### 3.3 `GET /api/v1/tools`

门户主列表接口。

**查询参数**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `q` | string | 关键词，搜索名称、简介、详情、标签 |
| `category` | string | 分类 slug，可重复传实现多选 |
| `tag` | string | 标签名，可重复传 |
| `type` | string | `file` / `webapp` / `skill` / `prompt`，可重复传 |
| `owner` | string | 按作者 username 筛选 |
| `sort` | string | `hot`（默认）/ `new` / `name` / `-updated_at` |
| `page` | int | 默认 1 |
| `page_size` | int | 默认 24，最大 200 |

**响应**：

```json
{
  "items": [
    {
      "id": 12,
      "slug": "log-analyzer-a3f2",
      "name": "日志分析器",
      "summary": "一键分析 Nginx 与 Tomcat 日志，输出 Top 错误与耗时分布。",
      "tool_type": "file",
      "visibility": "public",
      "category": { "id": 3, "slug": "dev-tools", "name": "研发工具", "icon": "wrench" },
      "tags": ["python", "log", "ops"],
      "cover_url": "/api/v1/images/88?variant=thumb&sig=<b64url>.<exp>",
      "owner": { "id": 42, "username": "zhangsan", "display_name": "张三" },
      "current_version": "1.2.0",
      "file_size": 4821043,
      "download_count": 137,
      "view_count": 892,
      "has_pending_version": false,
      "can_download": true,
      "favorite_count": 12,
      "like_count": 31,
      "is_favorited": false,
      "is_liked": true,
      "published_at": "2025-01-08T09:12:00Z",
      "updated_at": "2025-03-02T14:31:00Z",
      "webapp_unhealthy": false
    }
  ],
  "total": 137,
  "page": 1,
  "page_size": 24,
  "pages": 6,
  "facets": {
    "categories": [
      { "slug": "dev-tools", "name": "研发工具", "count": 42 },
      { "slug": "ops-tools", "name": "运维工具", "count": 28 }
    ],
    "types": [
      { "value": "file", "count": 80 },
      { "value": "skill", "count": 31 }
    ]
  }
}
```

**`facets` 字段说明**：分类与类型的计数**已按当前用户可见性过滤**（SRS FR-TAX-07）。这是为了让左侧分类导航显示的计数与实际能看到的工具数一致。代价是每次列表请求都要算一次聚合，因此建议：
- 分类计数单独缓存（按用户可见性分组缓存，例如按「是否超管 + 所属组集合的哈希」做键）
- 或者前端把 `facets` 请求拆成独立的 `GET /categories` 调用，列表接口不返回 facets

**本实现选择（M1 checkpoint 裁定，2025-03 修订）**：`facets` 的**聚合计算**只在 `page == 1` 时执行，翻页时**不重复算**（这是性能要求）。

但 `facets` **这个键在响应中始终存在**：`page == 1` 时是 `ToolFacets` 对象，翻页时为 `null`。

> **为什么是 `null` 而不是省略整个键**：本节早先版本写的是「翻页时省略键」，与 §1.1「可空字段显式返回 `null`，不省略字段。前端可依赖字段存在性」直接矛盾。裁定以 §1.1 为准 —— 稳定的 schema 让前端可以把类型写成 `facets: ToolFacets | null`，而不必处理 `undefined` 与 `null` 两种缺失语义。§3.3 的真实意图是「不重复计算聚合」，该意图已由服务层满足，与序列化形状无关。
>
> 前端类型应写：`facets: ToolFacets | null`（**不要**写成 `facets?: ToolFacets`）。

**`has_pending_version` 的可见性**：仅当请求者是 owner 或 approver/superadmin 时为 `true`，对其他用户恒为 `false`（SRS 待确认 Q3）。

**M8 追加的 4 个互动字段**（`contracts/CONTRACT.md` §23.5）：`favorite_count` / `like_count` 是两个**反规范化计数**，照常对匿名请求者返回；`is_favorited` / `is_liked` 表示**当前请求者**是否已收藏/点赞，对匿名请求者恒为 `false`。`viewer` 角色**不能收藏/点赞**（调用 `PUT`/`DELETE` 返回 403，见 §25.1），其两个布尔同样恒为 `false`。

**`webapp_unhealthy`（M14，`contracts/CONTRACT.md` §29.3）**：一个**派生**布尔，类型 `bool`，序列化名就是 `webapp_unhealthy`。仅当 `tool_type == "webapp"` **且** `webapp_health_status ∈ {"fail", "timeout"}` 时为 `true`，其余一律 `false`。

- **从未检测过（`webapp_health_status` 为 `NULL`）不算不健康** —— 否则刚打开探活开关时会满屏告警，而那是「还没测」而不是「坏了」。
- 列表项**只给这个派生布尔**，不给 `webapp_health_status` / `webapp_checked_at` 两个原始字段（列表页 24 条不必背两个完整字段，卡片只需要知道要不要打标记）。两个原始字段只在**详情**里给，见 §3.4。
- 该字段只出现在**门户列表项** `ToolListItem` 上，即恰好两个响应：`GET /api/v1/tools` 与 `GET /api/v1/me/favorites`。`MyToolListItem`（`/me/tools`）与 `AdminToolItem`（`/admin/tools`、`/admin/recycle-bin`）**都没有**这个字段（`openapi.json` 可核）。

> **`?tab=all|recent|favorites` 不是本接口的请求参数**。它是门户页的**纯前端 URL 状态**（`contracts/CONTRACT.md` §32.4 把 tab 结构、默认值、URL 状态全部判给前端）：`GET /api/v1/tools` 不认识 `tab`，传了也会被忽略。「继续使用」来自浏览器本地记录，「我的收藏」由前端单独调 `GET /api/v1/me/favorites`。

---

### 3.4 `GET /api/v1/tools/{slug}`

**响应**（在列表字段基础上增加）：

```json
{
  "id": 12,
  "slug": "log-analyzer-a3f2",
  "name": "日志分析器",
  "summary": "...",
  "description_md": "## 用途\n\n...",
  "description_html": "<h2>用途</h2>...",
  "tool_type": "file",
  "visibility": "public",
  "status": "approved",
  "category": { "id": 3, "slug": "dev-tools", "name": "研发工具" },
  "tags": ["python", "log", "ops"],
  "images": [
    { "id": 88, "kind": "cover", "url": "/api/v1/images/88?variant=full&sig=<b64url>.<exp>", "thumb_url": "/api/v1/images/88?variant=thumb&sig=<b64url>.<exp>", "width": 1280, "height": 720 },
    { "id": 89, "kind": "screenshot", "url": "/api/v1/images/89?variant=full&sig=<b64url>.<exp>", "thumb_url": "/api/v1/images/89?variant=thumb&sig=<b64url>.<exp>", "alt_text": "分析结果界面" }
  ],
  "webapp_url": null,
  "webapp_health_status": "ok",
  "webapp_checked_at": "2025-03-14T07:00:00Z",
  "current_version": {
    "id": 345,
    "version": "1.2.0",
    "changelog_md": "### 新增\n- 支持 gzip 日志\n### 修复\n- 修复时间解析错误的 bug",
    "file_name": "log-analyzer-1.2.0.zip",
    "file_size": 4821043,
    "file_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "file_ext": "zip",
    "approved_at": "2025-03-02T14:31:00Z",
    "uploaded_by": { "id": 42, "display_name": "张三" }
  },
  "version_count": 4,
  "history_version_count": 3,
  "download_count": 137,
  "view_count": 892,
  "owner": { "id": 42, "username": "zhangsan", "display_name": "张三" },
  "published_at": "2025-01-08T09:12:00Z",
  "last_version_at": "2025-03-02T14:31:00Z",
  "created_at": "2025-01-05T10:00:00Z",
  "can_download": true,
  "can_edit": false,
  "permissions": {
    "can_edit": false,
    "can_download": true,
    "can_manage_versions": false,
    "can_view_acl": false
  }
}
```

**`permissions` 对象**是刻意的设计：前端不需要自己实现一遍权限逻辑，直接读服务端给的布尔值。这避免了「前端判断和后端不一致导致按钮显示了但调用失败」。服务端仍是唯一权威（SRS 3.3）。

**`images[]` 的 `url` / `thumb_url` 是带签名的最长时效能力 URL**（形如 `?variant=full&sig=<b64url>.<exp>`，见 §3.16），前端原样使用。`width` / `height` 是**该图实际落盘的像素尺寸**，逐图返回、不是固定常量 —— 上例给的是 `seed-demo` 种子封面：**M18 起全尺寸封面为 1280×720**（缩略图仍为 160×90）。已播种过的老实例不会自动变（幂等播种器不重写既有行），那里仍是 320×180，见 `docs/09-勘误与已知限制.md` §20。

**M14 追加的 2 个探活字段**（`contracts/CONTRACT.md` §29.3）—— 详情给**原始值**，列表给派生布尔（§3.3 的 `webapp_unhealthy`）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `webapp_health_status` | `str \| null` | `"ok"` / `"fail"` / `"timeout"` / `null`。**`null` = 从未检测过**，与 `"fail"` 是两回事 |
| `webapp_checked_at` | `datetime \| null` | 最近一次检测时间（UTC，带 `Z`，与 `published_at` 同口径）。从未检测过时为 `null` |

探活结果只在 `webapp.health_check_enabled = true` 时由 maintenance 任务 `webapp-health` 写入，因此关闭该开关时这两个字段恒为 `null`（不新增端点、不新增响应字段之外的东西）。

**M8 另外追加的字段**（本示例不逐字段复写，理由见下文「关于响应示例的维护方式」）：`favorite_count` / `like_count` / `is_favorited` / `is_liked`（语义同 §3.3），以及只读回显字段 `estimated_saving_minutes`（`int | null`，范围 1~1440，`null` = 作者未填写；**不在列表项里返回**，列表保持精简）。

**`skill` 类型额外字段**（仅当 `tool_type == "skill"`）：

```json
{
  "skill": {
    "manifest": {
      "name": "log-analyzer",
      "description": "分析日志并生成报告",
      "allowed-tools": ["Bash", "Read", "Write"],
      "version": "1.2.0"
    },
    "readme_md": "# 日志分析器\n\n## 用法\n...",
    "file_tree_summary": { "file_count": 14, "total_size": 121034, "max_depth": 4 },
    "parse_error": null
  }
}
```

Skill 的 `readme_md` 正文与完整文件树**不在此接口返回**（可能很大），由 `GET /versions/{version}/skill-preview` 按需拉取。

**`prompt` 类型额外字段**：

```json
{ "prompt": { "content": "你是一个资深的 SRE...", "char_count": 842 } }
```

---

### 3.5 `GET /api/v1/tools/{slug}/versions/{version}/skill-preview`

**响应**：

```json
{
  "version": "1.2.0",
  "manifest": {
    "name": "log-analyzer",
    "description": "分析日志并生成报告",
    "allowed-tools": ["Bash", "Read", "Write"]
  },
  "readme_md": "# 日志分析器\n\n## 用法\n\n```bash\npython analyze.py access.log\n```",
  "file_tree": [
    { "path": "SKILL.md", "size": 2048, "is_dir": false, "sha256": "a1b2..." },
    { "path": "scripts", "size": 0, "is_dir": true, "sha256": null },
    { "path": "scripts/analyze.py", "size": 8192, "is_dir": false, "sha256": "c3d4..." },
    { "path": "scripts/parser.py", "size": 4096, "is_dir": false, "sha256": "e5f6..." },
    { "path": "assets/logo.png", "size": 24576, "is_dir": false, "sha256": "07a8..." }
  ],
  "file_tree_truncated": false,
  "total_size": 38912
}
```

**`file_tree_truncated`（M2 checkpoint 修正，2025-03）**：表示**返回的 `file_tree` 不完整**（存在两级上限，见下）。这是一个**在解析时确定的持久化布尔值**，不由响应字段推断。

存在两个不同的上限，早期文档把它们混为一谈，导致实现出现误报：

| 上限 | 值 | 行为 |
| --- | --- | --- |
| 上传校验上限 | 5000 个条目 | 超过则**拒绝上传**（`ZIP_TOO_MANY_FILES`），不产生版本 |
| 文件树存储上限 | 2000 个条目 | 超过则**截断存储**，`file_tree_truncated = true`，工具正常可用 |

因此「正常情况不会为 `true`」的旧说法是**错的** —— 一个 3000 条目的包会被正常接收，但其文件树是截断的。前端在 `file_tree` 较长时（>200 行）应做虚拟滚动。

> **不要用「`file_count` < `len(file_tree)`」来推断截断**。这两个量的统计口径不同（前者不含 `SKILL.md` 等元文件、后者含），在小包上会**恒为真**造成误报。必须用独立持久化的布尔字段。

**权限**：需要该工具的可读权限。`private` 工具对无权用户返回 `404`。

---

### 3.6 `POST /api/v1/me/tools`

创建工具草稿。

**请求**：

```json
{
  "name": "日志分析器",
  "summary": "一键分析 Nginx 与 Tomcat 日志。",
  "description_md": "## 用途\n\n...",
  "tool_type": "file",
  "category_id": 3,
  "tags": ["python", "log", "ops"],
  "visibility": "public",
  "webapp_url": null,
  "webapp_health_url": null,
  "estimated_saving_minutes": 15
}
```

**校验规则**：

| 字段 | 规则 |
| --- | --- |
| `name` | 必填，1~128 字符 |
| `summary` | 必填，1~500 字符 |
| `description_md` | 选填，≤ 100000 字符 |
| `tool_type` | 必填，枚举 |
| `category_id` | 选填，必须存在且 `is_active` |
| `tags` | 选填，≤ 8 个，每个 ≤ 64 字符 |
| `visibility` | 选填，默认 `public` |
| `webapp_url` | `tool_type == "webapp"` 时**必填**，且必须匹配 `^https?://` |
| `webapp_health_url` | 选填，≤ 1024 字符，必须以 `http://` 或 `https://` 开头。**探活任务只检测 `webapp_health_url` 非空的 webapp**（`contracts/CONTRACT.md` §29.2） |
| `estimated_saving_minutes` | 选填，1 ~ 1440 整数。**`null` = 作者未填写**，不是一个可以当成 `0` 的值，因此默认值就是 `null`（不设默认 0） |

`PATCH /api/v1/me/tools/{id}` 接受同一组字段（全部可选，局部更新语义）。其中 `webapp_url` / `webapp_health_url` / `estimated_saving_minutes` 有**显式区分**：**省略该字段 = 不改**，显式传 `null` = 清空（回到「未填写」）。

**响应** `201`：返回工具详情（同 3.4 的结构，`status` 为 `draft`）。

**`slug` 生成**：服务端由 `name` 转写（中文转拼音或直接用 `tool-{id}`），冲突时追加 4 位短哈希。响应中返回最终 `slug`。

---

### 3.7 `POST /api/v1/me/tools/{id}/versions`

上传新版本。**`multipart/form-data`**。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `version` | string | 必填，SemVer 风格字符串，同工具内唯一 |
| `changelog_md` | string | 选填（建议前端要求必填），变更说明 |
| `file` | file | `file` / `skill` 类型必填 |
| `prompt_content` | string | `prompt` 类型必填 |
| `webapp_url` | string | `webapp` 类型必填（也可只在工具级维护） |
| `auto_submit` | bool | 默认 `false`。为 `true` 时上传后立即提交审批 |

**响应** `201`：

```json
{
  "id": 346,
  "tool_id": 12,
  "version": "1.3.0",
  "status": "pending",
  "file_name": "log-analyzer-1.3.0.zip",
  "file_size": 5102341,
  "file_sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
  "skill": {
    "manifest": { "name": "log-analyzer", "description": "..." },
    "file_tree_summary": { "file_count": 15, "total_size": 128901, "max_depth": 4 },
    "parse_error": null
  },
  "tool_status": "pending_update",
  "created_at": "2025-03-14T08:21:33Z",
  "duplicate_of": {
    "tool_id": 9,
    "slug": "log-analyzer-legacy-b7c1",
    "name": "日志分析器（旧版）",
    "version_id": 210,
    "version": "1.0.0",
    "file_sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "is_current": true,
    "uploaded_at": "2024-11-02T03:10:00Z"
  }
}
```

**`duplicate_of`（M8，`contracts/CONTRACT.md` §23.2）**：上传**去重命中**信息，`null` = 未命中，或本次没有文件可比（`webapp` / `prompt` 类型没有 `file_sha256`）。

- 命中依据是既有的 `tool_versions.file_sha256` 索引，不新增哈希计算。
- **同一工具的新版本不算重复**（那是正常迭代），只报**其他工具**里的相同文件。
- **命中不阻断上传** —— 版本照常入库，前端据此展示**非阻塞**提示（例如「与 xx 的 v1.0.0 内容相同，可直接使用」）。
- 检测放在**服务端**是刻意的：浏览器端算 SHA-256 需要 `crypto.subtle`，而它只在安全上下文（HTTPS / localhost）可用，本项目按 D39 用 `http://<ip>:<port>` 直连，那里 `crypto.subtle` 为 `undefined`。契约因此裁定**不新增端点、不做客户端预检**。

**错误场景**：

| 场景 | HTTP | code | details |
| --- | --- | --- | --- |
| 版本号重复 | 409 | `VERSION_EXISTS` | `{version: "1.3.0"}` |
| 文件超限 | 413 | `PAYLOAD_TOO_LARGE` | `{limit_mb: 200, actual_mb: 312}` |
| 扩展名不允许 | 415 | `UNSUPPORTED_MEDIA_TYPE` | `{ext: "xyz", allowed: [...]}` |
| zip 含路径穿越 | 422 | `ZIP_PATH_TRAVERSAL` | `{entry: "../../etc/passwd"}` |
| zip 解压后体积超限 | 422 | `ZIP_BOMB_DETECTED` | `{limit_mb: 1024, detected_mb: 10240, checked_entries: 128}` |
| 文件数超限 | 422 | `ZIP_TOO_MANY_FILES` | `{limit: 5000, count: 12000}` |
| 缺 SKILL.md | 422 | `SKILL_MD_NOT_FOUND` | `{searched: ["SKILL.md", "*/SKILL.md"]}` |
| 用户配额耗尽 | 507 | `USER_QUOTA_EXCEEDED` | `{used_mb: 2048, quota_mb: 2048}` |
| 平台配额耗尽 | 507 | `INSUFFICIENT_STORAGE` | `{used_mb: 51000, quota_mb: 51200}` |
| 磁盘空间不足 | 503 | `STORAGE_FULL` | — |

**处理顺序**（重要，这决定了错误提示的准确性与资源消耗）：

```
1. 校验权限（owner）与工具状态（不能是 pending）
2. 校验参数（version 格式、必填项）
3. 检查版本号是否已存在           ← 早失败，不浪费 IO
4. 检查文件大小上限
5. 检查扩展名白名单
6. 检查用户/平台配额
7. 流式写入临时文件，同时增量计算 SHA256   ← 第一次真正落盘
8. 若是 skill 类型：流式解压 + 安全校验 + 提取内容
9. 全部校验通过后：移动临时文件到最终位置（原子 rename）
10. 开启数据库事务：插入 version 行、更新 tool 状态、写 approval_record、更新 search index
11. 提交事务
12. 若失败：删除临时文件与已落盘的最终文件
```

第 7~9 步在事务**之外**完成，这是「禁止在事务内做文件 IO」原则的落实（README 风险说明第 3 条）。

---

### 3.8 `GET /api/v1/admin/approvals`

**查询参数**：`status`、`tool_type`、`owner`、`page`、`page_size`。

**排序是固定的**：按提交时间正序（先提交先处理），**没有 `sort` 参数**（`openapi.json` 可核）。
本节早先版本写了一个 `sort` 参数，那是笔误 —— 队列顺序由「先提交先处理」这条业务规则决定，
不应由客户端改。若将来要支持自定义排序，需走契约变更流程。

**`status` 的取值（M2 checkpoint 补入，2025-03）**：

| 取值 | 含义 |
| --- | --- |
| `pending` | **仅**首次提交待审（`tools.status = 'pending'`） |
| `pending_update` | **仅**已发布工具的新版本待审 |
| `pending_all` | **两者并集**；**省略 `status` 时等价于此，也是默认语义** |

> **这是一个会咬人的坑**：本节早先版本写「默认 `pending`」。若客户端照此传 `status=pending`，
> **新版本待审的条目不会出现在队列里** —— 已发布工具发新版本后将无人审批，而门户仍在正常服务，
> 管理员很难察觉。前端在 M2 联调时发现并改为 `pending_all`。
> 后端 `app/repositories/approvals.py` 三个取值都支持。

**响应**：

```json
{
  "items": [
    {
      "tool_id": 12,
      "tool_slug": "log-analyzer-a3f2",
      "tool_name": "日志分析器",
      "tool_type": "skill",
      "summary": "一键分析 Nginx 与 Tomcat 日志。",
      "visibility": "public",
      "category": { "slug": "dev-tools", "name": "研发工具" },
      "tags": ["python", "log"],
      "submission_type": "new_version",
      "status": "pending_update",
      "pending_version": {
        "id": 346,
        "version": "1.3.0",
        "changelog_md": "### 新增\n- 支持 gzip",
        "file_name": "log-analyzer-1.3.0.zip",
        "file_size": 5102341
      },
      "current_version": { "id": 345, "version": "1.2.0" },
      "owner": { "id": 42, "username": "zhangsan", "display_name": "张三" },
      "submitted_at": "2025-03-14T08:21:33Z",
      "waiting_hours": 3.4
    }
  ],
  "total": 5,
  "page": 1,
  "page_size": 20,
  "pages": 1
}
```

`submission_type` 取值 `new_tool` / `new_version`，让审批人一眼区分是新工具还是版本更新。`waiting_hours` 是服务端算好的等待时长，用于前端高亮「积压超过 24 小时」的条目。

---

### 3.9 `POST /api/v1/admin/approvals/{tool_id}/approve`

**请求**：

```json
{
  "version_id": 346,
  "note": "已核对 changelog 与包内脚本",
  "expected_version_seq": 7
}
```

| 字段 | 说明 |
| --- | --- |
| `version_id` | 选填。若工具有待审版本则必填（或服务端自动取 `pending_version_id`） |
| `note` | 选填，批准备注 |
| `expected_version_seq` | 选填，乐观锁。传入 `tools.version_seq`，不匹配返回 `409 ALREADY_PROCESSED` |

**响应** `200`：

```json
{
  "tool_id": 12,
  "status": "approved",
  "version_seq": 8,
  "current_version": { "id": 346, "version": "1.3.0" },
  "superseded_version": { "id": 345, "version": "1.2.0" },
  "purged_versions": [],
  "approval_record_id": 9021
}
```

`purged_versions` 列出因超出 10 份上限而被归档淘汰的版本，便于前端提示「版本 1.0.0 已归档」。

---

### 3.10 `POST /api/v1/admin/approvals/{tool_id}/reject`

**请求**：

```json
{
  "reason": "包内 scripts/deploy.sh 包含硬编码的生产环境密码，请移除后重新提交。",
  "version_id": 346
}
```

**校验**：`reason` 必填，长度 5~2000。

**响应** `200`：

```json
{
  "tool_id": 12,
  "status": "approved",
  "pending_version": null,
  "rejected_version": { "id": 346, "version": "1.3.0" },
  "approval_record_id": 9022
}
```

**注意 `status` 为 `approved`**：如果被驳回的是「新版本」而非「首次提交」，工具本身仍处于已发布状态（SRS 4.1），当前版本不受影响。这个语义容易搞错，实现时必须区分 `submission_type`。

---

### 3.11 `PUT /api/v1/me/tools/{id}/acl`

全量替换授权列表（SRS FR-ACL-03）。

**请求**：

```json
{
  "visibility": "restricted",
  "entries": [
    { "subject_type": "user", "subject_id": 7, "can_download": true },
    { "subject_type": "group", "subject_id": 3, "can_download": true },
    { "subject_type": "group", "subject_id": 5, "can_download": false }
  ]
}
```

**响应** `200`：返回工具详情的 `permissions` 与 `acl` 部分。

**校验**：

| 规则 | 违反时 |
| --- | --- |
| `visibility == "restricted"` 时 `entries` 不能为空 | `400 ACL_REQUIRED` |
| `subject_type` 为 `user` 时该用户必须存在且启用 | `400 SUBJECT_NOT_FOUND` |
| `subject_type` 为 `group` 时该组必须存在且启用 | `400 SUBJECT_NOT_FOUND` |
| `subject_id` 不能是 owner 自己（owner 天然可见） | `400 SELF_GRANT` |
| 同一 `(subject_type, subject_id)` 不能重复 | `400 DUPLICATE_ENTRY` |

**可见性变更与 ACL 的关系**：切到 `public` 或 `private` 时，ACL 条目**保留但不生效**（不删除）。这样用户在 `restricted` ↔ `public` 之间来回切换时不会丢失已配置的授权名单。这是刻意的用户体验优化。

---

### 3.12 `POST /api/v1/admin/tokens`

**请求**：

```json
{
  "name": "CI 发布脚本",
  "note": "供 GitLab CI 的发布流水线使用",
  "scopes": ["tools:read", "tools:write", "approvals:write"],
  "expires_at": "2026-03-14T00:00:00Z"
}
```

**响应** `201`（**明文 Token 只在此处出现一次**）：

```json
{
  "id": 5,
  "name": "CI 发布脚本",
  "token": "st_9xK2mNpQ7vR4tY8uI1oP3aS6dF0gH5jL2zX4cV7bN9mQ1wE3rT",
  "token_prefix": "st_9xK2mN",
  "scopes": ["tools:read", "tools:write", "approvals:write"],
  "expires_at": "2026-03-14T00:00:00Z",
  "created_at": "2025-03-14T08:21:33Z",
  "warning": "请立即复制保存。此 Token 不会再次显示。"
}
```

**Scope 校验**：Token 的 Scope 必须是创建者角色权限的**子集**。例如 `user` 角色不能签发 `approvals:write` 的 Token，即使他是通过管理台操作 —— 但能进管理台说明他是 superadmin，所以实际不会触发。这个校验的价值在于**防止管理员误操作签发了超出预期的 Token**。

---

### 3.13 `GET /api/v1/admin/settings` / `PUT /api/v1/admin/settings`

**GET 响应**：

```json
{
  "items": [
    {
      "key": "approval.mode",
      "value": "require",
      "value_type": "string",
      "is_public": false,
      "description": "审批模式",
      "options": ["require", "auto_approve_all"],
      "updated_at": "2025-03-01T10:00:00Z",
      "updated_by": { "id": 1, "display_name": "管理员" }
    },
    {
      "key": "version.history_limit",
      "value": 10,
      "value_type": "int",
      "is_public": false,
      "min": 1,
      "max": 50,
      "updated_at": "2025-03-01T10:00:00Z"
    }
  ]
}
```

**`options` / `min` / `max`** 由服务端下发，前端据此渲染正确的控件（下拉框 vs 数字输入），避免前端硬编码设置项的元信息。这是「设置项 schema 由后端驱动」的做法。

**PUT 请求**（批量，部分更新）：

```json
{
  "items": [
    { "key": "approval.mode", "value": "auto_approve_all" },
    { "key": "version.history_limit", "value": 15 }
  ]
}
```

**响应** `200`：返回更新后的设置列表。若有非法值，**整个请求回滚**（原子性），返回 `400` 并指出哪个 key 有问题。

**切换 `approval.mode` 为 `auto_approve_all` 的响应附加提示**：

```json
{
  "items": [ ... ],
  "warnings": [
    {
      "code": "PENDING_ITEMS_NOT_AFFECTED",
      "message": "当前有 5 个待审条目，切换为全部放行不会自动处理它们。",
      "pending_count": 5
    }
  ]
}
```

---

### 3.14 `POST /api/v1/admin/import/users`

**请求**：`multipart/form-data`，字段 `file`（CSV）+ `dry_run`（bool）+ `on_conflict`（`skip`/`update`/`fail`）。

**CSV 格式**：

**导入格式**（`password` 可留空，留空则系统生成并回显一次）：

```csv
username,display_name,email,roles,password
lisi,李四,lisi@example.com,user,
wangwu,王五,,user;approver,
```

**导出格式与之不同**（M3 前端 checkpoint 核实，2025-03）—— 导出**不含 `password`**，改为包含 `status`：

```csv
username,display_name,email,roles,status
lisi,李四,lisi@example.com,user,active
```

本节早先只给了一个 CSV 示例，把导入与导出的列混为一谈。权威定义在 `backend/app/services/import_export_service.py` 的 `USER_CSV_FIELDS`。

**导入忽略 `status` 列，导出不含 `password` 列** —— 这是刻意的：导出绝不包含凭据。

**响应** `200`：

```json
{
  "dry_run": false,
  "on_conflict": "skip",
  "succeeded": 8,
  "failed": 2,
  "skipped": 1,
  "generated_passwords": [
    { "username": "lisi", "password": "Xy7#mK9pQ2" }
  ],
  "errors": [
    { "row": 4, "field": "username", "value": "wang wu", "message": "用户名只能包含字母、数字、下划线、连字符" },
    { "row": 7, "field": "roles", "value": "root", "message": "未知角色：root" }
  ]
}
```

**`generated_passwords`** 是明文回显的**唯一例外**（密码留空时由系统生成），因为管理员需要把初始密码告知用户。此响应**不写入日志**，且 `dry_run=true` 时不生成密码。

---

### 3.15 `POST /api/v1/tools/{slug}/download-ticket`

签发一次性下载票据（SRS 见 1.10）。

```json
{
  "url": "/api/v1/tools/log-analyzer-a3f2/download?version_id=345&ticket=eyJ0b29sX2lkIjoxMiwi...",
  "expires_at": "2025-03-14T08:22:33Z",
  "file_name": "log-analyzer-1.2.0.zip",
  "file_size": 4821043,
  "file_sha256": "e3b0c442..."
}
```

票据为 HMAC-SHA256 签名，载荷 `{tool_id, version_id, user_id, exp}`，密钥来自 `SECRET_KEY` 派生的子密钥。有效期 60 秒，**一次性不强制**（允许多次使用，但在有效期内），因为浏览器可能发起 Range 请求或重试。

---

### 3.16 `GET /api/v1/images/{id}` —— 能力签名 URL

图片不做静态目录直出（否则猜到 ID 就能看到 `private` 工具的截图），每次读取都校验**父工具**的可见性。两条鉴权路径**二选一**：

| 路径 | 形式 | 适用场景 |
| --- | --- | --- |
| 能力签名 | `?variant=full\|thumb&sig=<b64url>.<exp>` | `<img>` 标签带不上 `Authorization` 头 |
| Bearer 凭证 | `Authorization: Bearer <JWT / st_ token>` | 编辑器预览等已登录场景 |

`variant` 取值 `full`（默认）或 `thumb`，非法值由参数校验拦成 `422`。

**签名绑定 `variant`**。HMAC 的报文是 `f"{image_id}|{variant}|{exp}"`，所以**把 `variant=thumb` 改成 `full` 会让签名失效**（反之亦然）—— 不能拿缩略图的签名去取原图。`exp` 编在 URL 自身里（`?sig=<mac>.<exp>`），校验时比的是 URL 里的 `exp`，不是当前设置值，因此改设置不影响已签发的 URL。

**有效期**：系统设置 `images.signature_ttl_hours`，默认 `168` 小时（7 天）。它是**私有设置项，不经 `/meta` 外泄**。签发的 TTL 读取带 60 秒进程内缓存，所以改设置后最多 60 秒生效。

**状态码**：

| 情况 | 结果 |
| --- | --- |
| 签名有效（或 Bearer 有效）且父工具可见 | `200` + 图片字节，`Cache-Control: private, max-age=86400` |
| **无签名且无 Bearer 凭证** | **`404`**（**不是 403**） |
| **签名无效 / 错签名且无 Bearer 凭证** | **`404`**（与上一条**同码**，避免用状态码差异探测资源） |
| Bearer 有效，但父工具对该用户不可见 | `404` |
| 父工具已软删除，或图片行/落盘文件不存在 | `404` |
| Bearer 是 API Token 但缺 `tools:read` | `403 SCOPE_MISSING` |
| Bearer 有效但 `must_change_password = true` | `403 PASSWORD_CHANGE_REQUIRED` |

**签名不是绕过可见性的后门**：签名只证明「这个 URL 是服务端下发的」，报文里只含 `image_id` / `variant` / `exp`（**不含用户身份**）；父工具的可见性仍照常校验 —— URL 外泄不等于图片外泄。

**`thumb` 回退**：历史数据没有缩略图文件时，`variant=thumb` 回退返回原图，而不是报错。

---

> **关于响应示例的维护方式（M3 前端 checkpoint 裁定，2025-03）**
>
> 本文档早先逐字段复写每个响应，结果持续与实现漂移（本轮就发现 5 处：`version_seq`、
> `note`、Token 的 8 vs 15 个字段、CSV 列、`sort` 参数）。**从本轮起改为**：
>
> - **`backend/openapi.json` 是响应形状的唯一权威**（`contracts/CONTRACT.md` §15.6）
> - 本文档只负责**语义、业务规则、错误码与边界条件**；示例仅用于说明意图，不保证字段完备
> - 前端类型由 `web/src/api/types.ts` 镜像 openapi，并由 `web/scripts/check-api-types.mjs` 守卫生效
> - 发现示例与 openapi 不一致时，**以 openapi 为准**，并报告文档需要修订
>
> **已知例外**：以下 6 个端点的响应在 openapi 里仍是 `additionalProperties: true`（无字段定义），
> 形状目前只存在于 `types.ts`。已在 M5 任务书中要求补 `response_model`：
> `GET /admin/groups`、`GET /admin/groups/{id}/members`、`DELETE /admin/groups/{id}`、
> `DELETE /admin/groups/{id}/members/{user_id}`、`POST /admin/users/{id}/revoke-sessions`、
> `GET /admin/tokens`。

## 4. 错误码总表

| code | HTTP | 含义 | 前端处理建议 |
| --- | --- | --- | --- |
| `VALIDATION_ERROR` | 400 | 参数校验失败 | 逐字段高亮，`details.fields` 为字段错误数组 |
| `INVALID_SORT` | 400 | 排序字段非法 | 重置为默认排序 |
| `ACL_REQUIRED` | 400 | restricted 但无授权条目 | 定位到授权区块 |
| `SUBJECT_NOT_FOUND` | 400 | 授权主体不存在 | 移除失效条目并提示 |
| `DUPLICATE_ENTRY` | 400 | 授权条目重复 | 前端去重 |
| `SELF_GRANT` | 400 | 不能授权给自己 | 提示 |
| `SETTING_INVALID` | 400 | 设置值非法 | 高亮该设置项 |
| `INVALID_CSV` | 400 | CSV 格式错误 | 提示下载模板 |
| `UNAUTHENTICATED` | 401 | 未携带凭证 | 跳登录页 |
| `INVALID_CREDENTIALS` | 401 | 用户名或密码错误 | 表单内提示 |
| `TOKEN_EXPIRED` | 401 | access token 过期 | 静默 refresh 后重试一次 |
| `TOKEN_REVOKED` | 401 | Token 已吊销 | 跳登录页 |
| `FORBIDDEN` | 403 | 角色不足 | 提示无权限 |
| `SCOPE_MISSING` | 403 | API Token Scope 不足 | 脚本侧提示所需 Scope |
| `ACCOUNT_DISABLED` | 403 | 账号禁用 | 提示联系管理员 |
| `PASSWORD_CHANGE_REQUIRED` | 403 | 需先改密 | 强制跳改密页 |
| `NOT_FOUND` | 404 | 不存在或无权查看 | 展示 404 页 |
| `ACCOUNT_LOCKED` | 423 | 账号锁定中 | 展示剩余时间倒计时 |
| `RATE_LIMITED` | 429 | 请求过频（M14 起真实启用，见 §1.11） | 按 `Retry-After` 头退避重试；`details.scope` 指出是哪一档配额。封面裂图也可能是命中 `api` 额度 |
| `VERSION_EXISTS` | 409 | 版本号重复 | 定位版本号输入框 |
| `ALREADY_PROCESSED` | 409 | 审批已被他人处理 | 刷新列表 |
| `LAST_SUPERADMIN` | 409 | 不能禁用最后一个超管 | 提示 |
| `CATEGORY_IN_USE` | 409 | 分类被引用 | 展示引用数量 |
| `GROUP_IN_USE` | 409 | 用户组被 ACL 引用 | 展示引用工具列表 |
| `TOOL_NOT_EDITABLE` | 409 | 待审状态下不可编辑 | 提示先撤回 |
| `STATE_CONFLICT` | 409 | 状态机不允许该操作 | 刷新详情 |
| `DOWNLOAD_NOT_ALLOWED` | 403 | ACL 未授予该用户下载权限（可见但不可下载） | 提示「该工具的授权中未包含下载权限」，引导向作者申请 |
| `PAYLOAD_TOO_LARGE` | 413 | 文件过大 | 前端预校验 + 明确上限提示 |
| `UNSUPPORTED_MEDIA_TYPE` | 415 | 文件类型不允许 | 展示允许的扩展名列表 |
| `SKILL_PARSE_FAILED` | 422 | Skill 包解析失败 | 展示具体错误，允许另存草稿 |
| `SKILL_MD_NOT_FOUND` | 422 | 未找到 SKILL.md | 提示根目录需有 SKILL.md |
| `ZIP_BOMB_DETECTED` | 422 | 解压后体积超限 | 提示 |
| `ZIP_PATH_TRAVERSAL` | 422 | 含非法路径条目 | 展示具体条目名 |
| `ZIP_TOO_MANY_FILES` | 422 | 文件数超限 | 提示 |
| `ZIP_INVALID` | 422 | 不是合法 zip | 提示重新打包 |
| `INSUFFICIENT_STORAGE` | 507 | 平台配额耗尽 | 提示联系管理员 |
| `USER_QUOTA_EXCEEDED` | 507 | 用户配额耗尽 | 展示用量与清理入口 |
| `STORAGE_FULL` | 503 | 磁盘空间不足 | 提示联系管理员 |
| `DATABASE_UNAVAILABLE` | 503 | 数据库不可用 | 展示重试按钮 |
| `INTERNAL_ERROR` | 500 | 未预期异常 | 展示 request_id 供反馈 |

**`details.fields` 的标准格式**（用于 `VALIDATION_ERROR`）：

```json
{
  "code": "VALIDATION_ERROR",
  "message": "请求参数校验失败",
  "details": {
    "fields": [
      { "field": "summary", "message": "简介不能超过 500 字", "value_length": 612 },
      { "field": "webapp_url", "message": "webapp 类型必须填写 URL" }
    ]
  },
  "request_id": "..."
}
```

---

## 5. 脚本化使用示例

### 5.1 用 curl 完成一次完整的「建用户 → 建工具 → 审批」流程

```bash
#!/usr/bin/env bash
set -euo pipefail

BASE="https://localcraft.internal/api/v1"
TOKEN="st_9xK2mNpQ7vR4tY8uI1oP3aS6dF0gH5jL2zX4cV7bN9mQ1wE3rT"
AUTH=(-H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json")

echo "==> 1. 查看当前审批模式"
curl -sS "${AUTH[@]}" "$BASE/admin/settings" | jq '.items[] | select(.key=="approval.mode")'

echo "==> 2. 批量导入用户（先 dry-run 预演）"
curl -sS "${AUTH[@]}" -F "file=@users.csv" -F "dry_run=true" \
  "$BASE/admin/import/users" | jq '{succeeded, failed, errors}'

echo "==> 3. 正式导入"
curl -sS "${AUTH[@]}" -F "file=@users.csv" -F "dry_run=false" -F "on_conflict=skip" \
  "$BASE/admin/import/users" | jq '{succeeded, failed, generated_passwords}'

echo "==> 4. 把某个用户加入免审白名单"
curl -sS "${AUTH[@]}" -X POST "$BASE/admin/approval-whitelist" \
  -d '{"user_id": 42, "reason": "核心工具组成员"}' | jq .

echo "==> 5. 查看待审队列"
curl -sS "${AUTH[@]}" "$BASE/admin/approvals?status=pending" \
  | jq '.items[] | {tool_id, tool_name, submission_type, submitted_at}'

echo "==> 6. 一键批准所有待审条目"
IDS=$(curl -sS "${AUTH[@]}" "$BASE/admin/approvals?status=pending&page_size=200" | jq -r '.items[].tool_id')
if [ -n "$IDS" ]; then
  IDS_JSON=$(echo "$IDS" | jq -R . | jq -s .)
  curl -sS "${AUTH[@]}" -X POST "$BASE/admin/approvals/batch-approve" \
    -d "{\"tool_ids\": ${IDS_JSON}, \"note\": \"批量放行\"}" | jq .
fi

echo "==> 7. 导出全站工具清单"
curl -sS "${AUTH[@]}" "$BASE/admin/export/tools" -o tools-export.json
jq 'length' tools-export.json
```

### 5.2 用 Python 脚本自动发布一个工具

```python
import hashlib
import requests

BASE = "https://localcraft.internal/api/v1"
s = requests.Session()
s.headers["Authorization"] = "Bearer st_9xK2mNpQ7vR4tY8uI1oP3aS6dF0gH5jL2zX4cV7bN9mQ1wE3rT"

# 1. 创建工具草稿
tool = s.post(f"{BASE}/admin/tools", json={
    "name": "巡检脚本集",
    "summary": "日常巡检常用脚本合集",
    "description_md": "## 包含\n\n- 磁盘检查\n- 服务状态",
    "tool_type": "file",
    "category_id": 3,
    "tags": ["ops", "shell"],
    "visibility": "public",
    "owner_id": 42,          # 管理接口可代创建
}).json()
print("created tool", tool["id"], tool["slug"])

# 2. 上传版本
path = "inspect-tools-1.0.0.zip"
sha = hashlib.sha256(open(path, "rb").read()).hexdigest()
with open(path, "rb") as f:
    ver = s.post(
        f"{BASE}/admin/tools/{tool['id']}/versions",
        data={"version": "1.0.0", "changelog_md": "首个版本", "auto_submit": "true"},
        files={"file": (path, f, "application/zip")},
    ).json()
print("uploaded version", ver["version"], "status", ver["status"])

# 3. 批准
res = s.post(f"{BASE}/admin/approvals/{tool['id']}/approve", json={
    "version_id": ver["id"],
    "note": "脚本已人工审核",
}).json()
print("approved ->", res["status"], res["current_version"]["version"])
```

> **注意**：示例中的 `POST /admin/tools` 与 `POST /admin/tools/{id}/versions` 是**管理侧代创建接口**，用于脚本批量导入场景，与 `/me/tools` 是不同路径。管理侧接口允许指定 `owner_id`，且绕过 owner 校验（由 `admin:all` Scope 授权）。这两个接口在总表中未单列，属于 `/api/v1/admin/tools` 资源下的子操作，实现时一并提供。

### 5.3 从 CI 检查待审积压并告警

```bash
#!/usr/bin/env bash
PENDING=$(curl -sS -H "Authorization: Bearer ${LOCALCRAFT_TOKEN}" \
  "${BASE}/admin/approvals?status=pending&page_size=1" | jq -r '.total')
if [ "$PENDING" -gt 20 ]; then
  echo "WARN: 待审积压 ${PENDING} 条" >&2
  exit 1
fi
```

---

## 6. FastAPI 实现要点

### 6.1 目录组织

```
app/
  main.py                 # 应用装配、中间件、异常处理器、静态文件挂载
  core/
    config.py             # Pydantic Settings，读环境变量
    security.py           # JWT、密码哈希、下载票据签名
    deps.py               # 依赖注入：current_user、current_principal、require_scope
    errors.py             # 领域异常类 + 全局异常处理器
    pagination.py         # 分页参数与包装模型
  models/                 # SQLAlchemy 模型
  schemas/                # Pydantic 模型（请求/响应分开）
  services/               # 业务逻辑，事务边界在这里
  repositories/           # 数据访问，唯一允许写 SQL 的地方
  api/v1/
    system.py  auth.py  tools.py  me.py
    admin/  users.py groups.py categories.py tags.py approvals.py
            whitelist.py tokens.py settings.py tools.py import_export.py stats.py
  search/                 # SearchBackend 抽象 + SQLite/PG 实现
  storage/                # 文件存储抽象 + 本地实现
  tasks/                  # 后台任务（计数落库、清理）
  cli.py                  # Typer 命令行
```

### 6.2 依赖注入与权限

```python
# 伪代码，展示鉴权依赖的组合方式
async def get_principal(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Principal:
    """解析 Bearer Token，区分 API Token 与 JWT，返回统一的 Principal。"""
    ...

def require_role(*roles: str):
    async def _dep(p: Principal = Depends(get_principal)) -> Principal:
        if not p.roles & set(roles):
            raise ForbiddenError()
        return p
    return _dep

def require_scope(*scopes: str):
    async def _dep(p: Principal = Depends(get_principal)) -> Principal:
        if p.kind == "api_token" and not set(scopes) <= p.scopes:
            raise ScopeMissingError(missing=list(set(scopes) - p.scopes))
        return p
    return _dep
```

**每个路由必须显式声明权限依赖**，例如：

```python
@router.post("/{tool_id}/reject")
async def reject_tool(
    tool_id: int,
    body: RejectRequest,
    p: Principal = Depends(require_scope("approvals:write")),
    db: AsyncSession = Depends(get_db),
):
    ...
```

建议写一个 **测试**：遍历 `app.routes`，断言每个非公开路由都带了鉴权依赖。这能防止「新加的接口忘了加权限」这类事故。这是很值得做的一个守卫测试。

### 6.3 事务边界

- 事务由 **service 层**控制，不在 repository 或 router 层
- 一个 HTTP 请求对应**最多一个写事务**，且事务内**不做 IO**（见 3.7 的处理顺序）
- 使用 `async with db.begin():` 显式管理，避免隐式 autocommit 导致的半提交状态
- SQLite 下设置 `PRAGMA busy_timeout=5000`，让写锁冲突时自动等待而不是立即报错

### 6.4 统一响应与异常

```python
@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError):
    return JSONResponse(
        status_code=exc.http_status,
        content={
            "code": exc.code,
            "message": exc.message,
            "details": exc.details,
            "request_id": request.state.request_id,
        },
    )
```

`RequestValidationError` 也需覆盖，把 Pydantic 的错误结构转成 `details.fields` 格式，保证前端只需处理一种错误形状。

### 6.5 静态文件与 SPA

```python
# API 路由先注册，静态文件最后挂载（顺序不能反）
app.include_router(api_router, prefix="/api/v1")

@app.get("/healthz")
async def healthz(): return {"status": "ok"}

# SPA fallback：非 /api 开头的路径都返回 index.html
app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

@app.get("/{full_path:path}")
async def spa(full_path: str):
    if full_path.startswith(("api/", "healthz", "readyz", "docs", "openapi.json")):
        raise HTTPException(404)
    file = WEB_DIST / full_path
    if file.is_file():
        return FileResponse(file)
    return FileResponse(WEB_DIST / "index.html")
```

**注意**：`app.mount("/", StaticFiles(..., html=True))` 这种写法会让所有未匹配的 API 路径返回 `index.html` 而不是 404，导致前端拿到 HTML 去 `JSON.parse` 而报错。必须用上面的显式 fallback。

### 6.6 OpenAPI 文档

```python
app = FastAPI(
    title="localcraft API",
    version="1.0.0",
    docs_url="/docs" if settings.api_docs_enabled else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.api_docs_enabled else None,
)
```

生产环境默认关闭（SRS FR-API-07）。运维可在需要时通过环境变量临时打开。

**建议**：即使生产关闭，也要在 CI 里把 `openapi.json` 导出成产物并归档 —— 这样脚本编写者能拿到接口定义，而运行环境不暴露接口清单。
