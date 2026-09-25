# 后端开发 Agent — 任务书（M1）

## 你的身份与边界

你是 **localcraft 平台的后端开发 agent**。你独占并只允许修改这三个目录：

- `backend/`
- `deploy/`
- `scripts/`

**你绝对不可以修改**：`docs/`、`contracts/`、`README.md`、`web/`。这些是只读的。前端由另一个 agent 并行开发，你去动 `web/` 会直接造成冲突。

**不要执行任何 git 命令**（commit / branch / checkout / stash 等一个都不要）。版本控制由监控方在 checkpoint 统一处理，并发操作 git 索引会冲突。

工作目录：`/Users/jlthzy/Documents/localcraft`

---

## 第一步：必须先读完这些（不要跳过）

按顺序读，读完再动手：

1. `contracts/CONTRACT.md` —— **最重要**。这是你和前端之间的唯一约定来源，M1 的接口清单、认证握手、响应信封、种子数据要求、验收标准全在里面。
2. `docs/02-数据模型设计.md` —— 20 张表的字段级设计。你要照着它建全部模型和迁移，**字段名一个字都不能改**。
3. `docs/01-需求规格说明书.md` —— 第 3 章权限矩阵、第 4 章状态机、5.1 认证需求、5.16 系统设置。
4. `docs/03-API接口清单.md` —— §1 通用约定、§3.1～3.2、§4 错误码总表、§6.6 FastAPI 实现要点。
5. `README.md` 的「Python 版本策略」与「关于 SQLite 的风险说明」两节 —— 这两节是硬约束，违反了后面会返工。
6. `docs/05-部署与运维方案.md` —— 只需读 §4（发布包目录结构）、§6（systemd unit）、§3.8（前端产物交付），确认你的 `deploy/` 与 `scripts/` 产出能与它对接。其余章节 M1 不用管。

---

## M1 目标

**一条打通的真实竖切**：登录 → 刷新恢复会话 → 门户列表（含筛选/搜索/分页/facets）→ 登出。外加一个能真的部署到 openEuler 的骨架。

`contracts/CONTRACT.md` §6 列出了 M1 要实现的全部 12 个接口。**只实现这些**，不要提前做工具 CRUD、版本管理、审批、管理台 —— 那些是 M2/M3。

---

## 交付清单

### 1. 工程骨架与配置

- `backend/pyproject.toml`：`requires-python = ">=3.11"`，`[tool.ruff] target-version = "py311"`，`line-length = 100`，lint 选 `E,F,W,I,UP,B,SIM,RUF`
- `backend/requirements.txt`（直接依赖）与 `backend/requirements.lock`（全量锁定，含传递依赖）
- 虚拟环境建在 `backend/.venv`。**优先用真实的 python3.11**：

  ```bash
  command -v python3.11 || brew install python@3.11
  cd backend && python3.11 -m venv .venv
  ```

  如果本机确实装不上 3.11，**可以**在 3.13 上开发，但必须额外做到：
  - `ruff` 的 `target-version="py311"` 已配好且 CI 检查通过
  - 每次提交前跑 `python3.13 -m compileall -q app/`（这只能证明 3.13 语法正确，**不能**证明 3.11 兼容）
  - **在报告里明确写出「本机无 3.11，兼容性未在真实 3.11 上验证」**，这是必须上报的风险项，不要隐瞒

- `backend/app/core/config.py`：`pydantic-settings` 读环境变量。至少包含 `DATABASE_URL`、`DATA_DIR`、`SECRET_KEY`、`COOKIE_SECURE`、`ACCESS_TOKEN_MINUTES`、`REFRESH_TOKEN_DAYS`、`API_DOCS_ENABLED`、`LOG_LEVEL`、`LOCALCRAFT_HOST`、`LOCALCRAFT_PORT`
- `.env.example`（放 `backend/`），并在 `deploy/localcraft.env.example` 放生产版

### 2. 数据层

- `backend/app/models/`：按 `docs/02` 实现**全部 20 张表**的 SQLAlchemy 2.0 模型（`Mapped[]` 注解风格）。M1 虽然只用其中几张，但一次建全，避免后面反复改迁移
- 所有枚举用 `enum.StrEnum`（Python 3.11 原生支持）定义在 `app/models/enums.py`，Pydantic 与模型共用同一份
- SQLite 连接必须挂事件钩子设 PRAGMA：

  ```python
  @event.listens_for(engine, "connect")
  def _set_sqlite_pragma(dbapi_conn, _):
      cur = dbapi_conn.cursor()
      cur.execute("PRAGMA foreign_keys=ON")      # 默认是 OFF，不设外键形同虚设
      cur.execute("PRAGMA journal_mode=WAL")
      cur.execute("PRAGMA busy_timeout=5000")
      cur.execute("PRAGMA synchronous=NORMAL")
      cur.close()
  ```

  这段只在方言是 sqlite 时执行，别把 PG 也套上。

- `backend/migrations/`：Alembic 配置好，三个迁移文件：
  - `0001_initial_schema.py` —— 全部表结构。注意 `tools.current_version_id` / `pending_version_id` / `cover_image_id` 与 `tool_versions` / `tool_images` 是循环引用，用 `use_alter=True` 或建表后再 `create_foreign_key`
  - `0002_seed_roles_and_settings.py` —— 用 `op.bulk_insert` 幂等插入 4 个角色与 `docs/02` §3.19 的全部系统设置默认值
  - `0003_create_fts_index.py` —— SQLite FTS5 虚表。**必须判断方言**（`op.get_bind().dialect.name == 'sqlite'`），非 sqlite 直接跳过
  - `tool_versions` 上要有「每个 tool 至多一个 `is_current=true`」的**部分唯一索引**，用 `op.execute()` 手写 DDL：`CREATE UNIQUE INDEX ... ON tool_versions(tool_id) WHERE is_current = 1`

- `backend/app/repositories/`：M1 只做 `users`、`categories`、`tags`、`tools` 四个仓储。`tools` 的门户列表查询要按 `docs/02` §4 的 Q1/Q2 写，把可见性判定**下推到 SQL**（`EXISTS` 子查询），不要在 Python 里过滤

### 3. 认证与权限

- `app/core/security.py`：Argon2id 密码哈希（`argon2-cffi`）、JWT 签发校验（`pyjwt`）、refresh token 随机串生成与 SHA256
- `app/core/deps.py`：
  - `get_principal()` —— 解析 Bearer，区分 `st_` 前缀（API Token，M1 不用但留接口）与 JWT，返回统一的 `Principal`
  - `require_role(*roles)`、`require_scope(*scopes)` 依赖工厂
  - `get_current_user()`
- `app/services/auth_service.py`：
  - 登录：归一化用户名 → 查用户 → 校验锁定与状态 → 验密 → 清失败计数 → 签 token → 写 `auth_sessions` → 返回
  - 失败：用户名不存在与密码错误返回**同一个** `INVALID_CREDENTIALS`；失败计数 +1，达阈值设 `locked_until`
  - 刷新：查 `auth_sessions` 校验未吊销未过期 → 新 access token → **同时返回 `user` 对象**（契约 §3.3）
  - 登出：吊销该 session 行
  - 改密：校验旧密码 → 强度校验 → 更新哈希 → 清 `must_change_password` → **吊销该用户全部其他 session**
- 强制改密拦截：一个全局依赖，`must_change_password=true` 时除 `/auth/*` 与 `/meta`、`/healthz`、`/readyz` 外一律返回 `403 PASSWORD_CHANGE_REQUIRED`
- 密码强度校验函数（长度 ≥ 10，大写/小写/数字/符号四类中至少 3 类），**前端也会校验一次，但服务端是权威**

### 4. 横切关注点

- `app/core/errors.py`：`DomainError` 基类 + 各错误码子类，按 `docs/03` §4 的表实现。`code`/`http_status`/`message`/`details` 四要素齐全
- `app/main.py` 里注册异常处理器：`DomainError`、`RequestValidationError`（**必须转成 `details.fields` 数组形状**）、`Exception` 兜底（返回 `INTERNAL_ERROR` + request_id，且**不泄露堆栈到响应体**）
- `request_id` 中间件：读或生成 `X-Request-Id`，塞进 `request.state`，写响应头，注入日志
- 结构化日志：`structlog` 或标准库 `logging` + JSON formatter 均可，但必须带 `request_id`、`user_id`、`method`、`path`、`status`、`duration_ms`，输出到 stdout
- `app/core/pagination.py`：`PageParams`（`page` ≥1，`page_size` 1~200）与泛型 `Page[T]` 响应模型
- 静态托管 + SPA fallback：`/api` 先注册，`web/dist/assets` 挂 StaticFiles，其余路径显式 fallback 到 `index.html`。**不要用 `app.mount("/", StaticFiles(html=True))`** —— 那会让未匹配的 API 路径返回 HTML，前端 `JSON.parse` 直接炸。`web/dist` 不存在时（前端还没构建）要能优雅降级，不能启动失败

### 5. M1 的 12 个接口

严格按 `contracts/CONTRACT.md` §6 与 `docs/03` 实现。其中：

- `GET /readyz` 要真的探测：`SELECT 1` 能通 + `DATA_DIR` 可写
- `GET /api/v1/meta` 只返回 `is_public=true` 的设置项
- `GET /api/v1/categories` 要返回每个分类下**当前用户可见的**工具数（按 `docs/01` FR-TAX-07）
- `GET /api/v1/tools` 的 `facets` **只在 `page == 1` 时返回**（契约与 `docs/03` 3.3 都写了）。`sort` 参数必须白名单校验，**绝不能把 sort 拼进 SQL**
- `GET /api/v1/tools` 对未登录用户按 `portal.allow_anonymous_view` 设置决定 401 还是返回公开数据

### 6. CLI

`python -m app.cli` 至少提供：

| 命令 | 作用 |
| --- | --- |
| `create-superadmin` | 交互式创建超管，支持 `--password-stdin`；密码不进 shell 历史 |
| `reset-password <username>` | 重置密码并置 `must_change_password=true` |
| `list-users` | 列出用户、角色、状态、存储用量 |
| `seed-demo` | **按 `contracts/CONTRACT.md` §7 严格实现**。前端联调全靠它，字段数量和边界情况（无封面、超长名称）都要有 |
| `export-openapi` | 导出 `backend/openapi.json`（监控方要靠它做契约比对，必须能一键生成） |

`seed-demo` 的随机值要用**固定种子**，保证每次执行结果完全一致。

### 7. 部署产物

- `deploy/localcraft.service`、`deploy/localcraft.slice` —— 内容以 `docs/05` §6 为准，按仓库布局调整路径（`WorkingDirectory` 在生产是 `/opt/localcraft/app/current`）
- `deploy/nginx-localcraft.conf` —— 以 `docs/05` §7 为准，M1 只需 HTTP 快速验证版
- `deploy/localcraft.env.example`
- `scripts/precheck.sh`、`scripts/install.sh`、`scripts/wait-healthy.sh` —— 以 `docs/05` §5 为准
- `scripts/make-release.sh` —— 按 `contracts/CONTRACT.md` §1 的映射表把仓库布局打成 `docs/05` §4 描述的发布包布局

部署脚本 M1 先做到「能在一台 openEuler 上从零装起来」，**不必**实测双架构 wheelhouse（那是 M4）。

### 8. 测试

- `backend/tests/`，pytest + httpx AsyncClient
- 必须覆盖：
  - 登录成功的完整链路（含 Cookie 下发）
  - 用户名不存在 / 密码错误返回**同一个**错误码
  - 连续失败达阈值后锁定，锁定期内即使密码正确也拒绝
  - 禁用账号登录被拒
  - **`must_change_password=true` 时访问 `/api/v1/tools` 返回 403 `PASSWORD_CHANGE_REQUIRED`**
  - refresh 流程返回 `user` 对象
  - 登出后 refresh 失效
  - 改密成功后其他 session 被吊销
  - **守卫测试**：遍历 `app.routes`，断言每个非公开路由都挂了鉴权依赖（这是防「新接口忘加权限」的关键，`docs/03` §6.2 明确要求）
  - 分页边界（`page=0`、`page_size=999`、超范围页码返回空 `items` 而不是 404）
  - `sort` 传非法值返回 `400 INVALID_SORT` 而不是 500
- 覆盖率目标：整体 ≥ 70%，`services/auth_service.py` 与权限判定 ≥ 90%
- 命令：`backend/.venv/bin/python -m pytest --cov=app --cov-report=term-missing`

---

## 完成前必须自己跑一遍并贴出结果

```bash
cd backend
.venv/bin/ruff check app tests
.venv/bin/python -m pytest -q
.venv/bin/alembic upgrade head
.venv/bin/python -m app.cli seed-demo
.venv/bin/python -m app.cli export-openapi
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 &
curl -sS localhost:8000/readyz
curl -sS localhost:8000/api/v1/meta
# 用 curl 走一遍登录 → 带 token 拉 /tools → refresh → logout，手动确认 Cookie 行为
```

`contracts/CONTRACT.md` §8 的 13 条验收里，**第 1、10、11、12 条属于你的责任范围**，请自己验证这两条并贴证据。

---

## 报告要求

完成后按 `contracts/CONTRACT.md` §10 的格式回报。硬性要求：

- `执行的验证` 一栏贴**真实跑过的命令与输出摘要**（通过数、覆盖率数字、curl 的实际响应片段）。不接受「应该没问题」「已测试通过」这类没有证据的表述
- 任何 **契约偏差** 或 **待裁决** 必须显式列出。宁可报一个「我拿不准」，也不要自己拍板改了接口 —— 前端是并行开发的，你改一个字段名就能让对方白干半天
- 如果本机没装上 Python 3.11，必须作为风险项明确上报

---

## 易错点清单（这些是最可能翻车的地方）

1. **SQLite 外键默认关闭**。不设 `PRAGMA foreign_keys=ON`，本地测试全过，切 PG 后到处报错
2. **`must_change_password` 拦截漏掉某些路由**，导致用户没改密就能用平台
3. **`RequestValidationError` 没转成 `details.fields` 形状**，前端只能拿到 FastAPI 默认的错误结构，两边的错误处理对不上
4. **SPA fallback 用 `StaticFiles(html=True)` 挂根路径**，导致 `/api/v1/typo` 返回 HTML
5. **`refresh` 不返回 `user`**，前端刷新页面时还得再调一次 `/auth/me`，或者干脆恢复不了会话
6. **`facets` 在每页都算**，翻页时白白多跑一次聚合查询
7. **`sort` 直接拼进 SQL**，注入风险
8. **JWT 里塞了权限快照**，导致管理员改了权限必须等 token 过期才生效（`docs/01` FR-GRP-06 要求实时判定，所以权限每次请求实时查）
9. **在事务里做密码哈希或文件 IO**，SQLite 写锁会被长时间占住
10. **`seed-demo` 不幂等**，前端联调时反复执行会造出一堆重复数据

---

## 不要做的事

- 不要实现 M2/M3 的接口（工具 CRUD、版本、审批、管理台、API Token）—— 现在做会让契约比对失去意义
- 不要写前端代码，不要改 `web/`
- 不要顺手重构 `docs/` 或 `contracts/`，发现文档有问题就在报告里提
- 不要引入 `docs` 里没出现的重量级依赖（富文本、ORM 替代品、Celery 等）。想加任何新依赖先报告
- 不要执行 git 命令
- 不要为了「看起来完整」而写没人调用的抽象层

有阻塞或需要裁决时，立刻按 §10 格式报告，不要自己猜着往下做。
