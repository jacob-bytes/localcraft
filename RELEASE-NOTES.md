# localcraft 发布说明

**版本：1.0.0** ｜ **发布日：2026-09-25** ｜ **目标平台：openEuler 24.03 LTS（x86_64 / aarch64）+ systemd + nginx**

本文件是 `docs/05-部署与运维方案` §11「升级与回滚流程」的**机器可读前置输入**：
`scripts/upgrade.sh` 与 `scripts/rollback.sh` 会读下面「本次是否包含数据库迁移」
小节，决定是否要执行 / 警告 `alembic upgrade`。因此：

> ⚠ 每次发版都必须更新本文件；判断迁移与否的**关键行必须从行首开始写**
> （形如 `本次是否包含数据库迁移：是`）。脚本用 `^本次是否包含数据库迁移`
> 定位该行，写成引用块或加粗前缀会导致定位失败。

---

## 一、本版本概要

localcraft 是内网「工具 / Skill」共享平台：上传 → 审批 → 发布 → 下载的完整闭环，
带三级可见性、版本管理与回收站，全部数据落在单机 SQLite（可选切 PostgreSQL），
以 systemd 原生方式部署，**目标机不需要联网、不需要 Node**。

1.0.0 是**首发版本**，覆盖 M1~M6 六个里程碑的全部交付内容。

---

## 二、里程碑概要

### M1 — 真实竖切与可部署骨架

- 打通「登录 → 刷新恢复会话 → 门户列表（筛选 / 搜索 / 分页 / facets）→ 登出」，
  共 **12 个冻结接口**（`contracts/CONTRACT.md` §6），不多做一个管理接口。
- 一次性建全 **19 张业务表**（迁移 `0001`），避免后续反复改迁移；
  枚举用 `String + CHECK` 而非数据库原生 ENUM，保证 SQLite / PostgreSQL 行为一致。
- 启用 SQLite **WAL** 与四个关键 PRAGMA（`journal_mode` / `busy_timeout` /
  `foreign_keys` / `synchronous`）；`--workers 1` 与 WAL 单写者模型对齐。
- 认证与权限骨架：JWT access/refresh、argon2 口令哈希、会话吊销、账号锁定、
  强制改密、防用户名枚举；权限判定以代码内 frozenset 为准（数据库被改也不能提权）。
- 横切能力：统一错误响应、`X-Request-Id` 请求追踪、结构化日志、
  `/healthz` `/readyz` `/api/v1/meta` 三个探针 / 元信息端点。
- 部署产物与 CLI：systemd 单元、nginx 配置、`install.sh` / `precheck.sh`、
  `create-superadmin` / `reset-password` / `list-users` / `health` / `export-openapi`。

### M2 — 上传 / 审批 / 下载闭环

- 存储层与 Skill 包解析：zip / tar 解压、`SKILL.md` 预览、sha256 计算、
  文件树（含截断标记），解压防护覆盖 **压缩比 / 总体积 / 条目数 / 递归深度 / 单文件大小 / 超时** 六项。
- 4 类工具（`file` / `skill` / `prompt` / `webapp`）的 CRUD 与状态机；
  三级可见性（public / restricted / private）+ 用户组 ACL。
- 版本管理：每个工具至多一个 `is_current`（部分唯一索引强制）；
  历史版本按 `version.history_limit` 保留，淘汰时**物理删除**磁盘文件，被淘汰版本下载返回 404。
- 审批流：提交 / 批准 / 驳回、`version_seq` 乐观并发（CAS）；
  已发布工具发新版本是否再审由 `approval.version_reapproval` 控制。
- 下载票据 + HTTP **Range** 断点续传；图片上传处理与缩略图、签名 URL。
- 计数器聚合（浏览 / 下载 / 版本数）与系统设置读写，为 M3 管理台打底。

### M3 — 管理面与脚本化能力

- 接口面扩展到 **92 个操作**：用户 / 角色 / 用户组 / 分类 / 标签 / 全站工具 /
  回收站 / 统计概览 / 批量导入导出 / 系统设置。
- **API Token**（scope 化）：只有 `approvals:write` 的 Token 能走审批，
  调用户管理接口返回 403；令牌可吊销、可设有效期。
- 迁移 `0003`：SQLite **FTS5 全文索引** `tool_search_index`
  （`unicode61 remove_diacritics 2` + 写入时中文单字切分；PG 方言整体跳过）。
- 迁移 `0004`：`tool_versions.skill_tree_truncated` **持久化**截断事实
  （原先是事后推断，口径不一致会误报；该事实无法从存储数据反推，必须落库）。
- CLI 补全：`gc-versions`、`purge-recycle-bin`、`maintenance`（一键跑全部清理项）等。
- 清理任务拆分为 9 项，全部幂等 + 分批，单项失败互相隔离。

### M4 — 能交付、能运维、能恢复

- **双架构离线 wheelhouse**（x86_64 / aarch64）：按架构下载 wheel、生成
  `MANIFEST.sha256` / `SHA256SUMS`、架构差异清单与校验脚本。
- 离线发布包生成（`scripts/make-release.sh`）与目标机一键安装
  （`scripts/install.sh`：校验包 → 建用户与目录 → 建 venv → 离线装依赖 → 迁移 → 起服务 → 就绪探针）。
- 备份 / 恢复 / 校验三件套：`backup.sh`（SQLite `.backup` 一致性快照 + `files/`
  `rsync --link-dest` 增量 + 保留份数清理）、`restore.sh`、`verify-backup.sh`，
  配套 `localcraft-backup.service` + `.timer`（每日 03:00）。
- **恢复演练真的跑过一遍**：删库 → 用备份恢复 → `integrity_check` + 行数对账 → 端到端验证。
- PostgreSQL 迁移演练：驱动与迁移路径验证（`psycopg` 正式声明见 M5）。
- 100 并发性能验证并落地基线；产出用户手册 / 管理员手册 / 运维手册三份文档。

### M5 — 收口（本版本交付件补齐）

- **图片签名能力 URL**：列表 / 详情下发带 HMAC 签名的 `cover_url` / `thumb_url`，
  有效期由新设置项 `images.signature_ttl_hours`（默认 168 小时）控制；
  `GET /api/v1/images/{id}` 接受有效 `sig` **或**有效 `Authorization` 头，两者皆无返回 404。
- **种子数据补齐**：新增 `viewer` 与 3 个作者账号；为种子工具写入真实占位包 +
  `sha256` + `storage_path`；扩到 **26 个工具**，使 `page_size` 12/24/48 分别得到 3/2/1 页。
- **死桩路由修活**：`POST /api/v1/admin/tools/{tool_id}/versions` 由"恒 404"
  改为可用别名（委托同一 service），接口面保持 92 不变。
- **`psycopg` 正式声明**：`pyproject.toml` 增加 `pg` 可选依赖，两套 wheelhouse 均补入 wheel。
- **两个"无消费方"设置项接线**：`approval.version_reapproval`（已发布工具新版本免审）
  与 `quota.warn_threshold_pct`（与 `storage_warning()` 同一口径，`disk-alert.sh` 复用）。
- **运维交付件补齐**：`uninstall.sh`、`upgrade.sh`、`rollback.sh`、`notify-ready.sh`、
  `metrics-snapshot.sh`、`disk-alert.sh`、`alert-webhook.sh`、`security-check.sh`，
  以及生产 TLS nginx 配置、nginx 限流 zone、systemd 加固 drop-in、systemd-tmpfiles 规则。

### M6 — 审阅期缺陷收口

M5 交付后做了两轮实测审阅，M6 专修**实测确认的真实缺陷**，不加新功能。

- **接口面 92 → 93（唯一的刻意破例）**：新增 `GET /api/v1/directory`。
  原因是功能空洞而非范围蔓延 —— FR-ACL-02（P0）要求「搜索用户/组后添加 ACL」，
  但普通用户没有任何可用的搜索接口（`/admin/users`、`/admin/groups` 均需超管），
  导致设置 `restricted` 可见性时必须手填数字 ID，该 P0 需求实际无法交付。
  新接口最小披露：用户只返回 `{id, username, display_name}`，组只返回
  `{id, name, member_count}`，不含邮箱、状态、角色、最后登录时间。
- **`tool_acl.can_download` 从「静默无效」变为真正生效**：该字段原先前后端都存、
  `docs/03` §3.11 描述其生效，但**没有任何读取点**，取消勾选「允许下载」毫无作用。
  现在「能看到详情但无权下载」返回 **403 `DOWNLOAD_NOT_ALLOWED`**
  （不是 404 —— 用户能看见详情，报 404 只会让人困惑）。
- **`revoke-sessions` 一并吊销 API Token**：原先管理员「强制下线」后，
  该用户签发的 API Token **仍然可用**，与「禁用用户」的语义不一致 ——
  同一个「让这个人失去访问」的意图，轻动作反而比重动作宽松。现在同一事务内一并吊销。
- **6 个端点补 `response_model`**：此前这 6 处响应结构不受约束，
  且**没有字段定义**。同时新增 `test_openapi_artifact_is_current` 守卫，
  确保 `backend/openapi.json` 不会再与实现漂移。
- **`current_version.can_download` 由授权与版本状态共同派生**：原先前端拿到的
  可下载标志与实际下载判定不同源，会出现「按钮可点但下载 403」。
- **附件白名单 21 → 44 项**：补齐常见开发产物与文档格式。
- **种子补 approver 账号**：`wangwu` 现在同时具有 `user` + `approver` 角色，
  否则「审批员不该看到超管菜单」这类角色边界无法在真实环境验证（用 admin 看不出差异）。
- **ACL 主体选择器接入 `/directory`**：前端把数字 ID 输入框换成搜索下拉。
- **待审新版本的撤回语义修正**：个人页的「撤回待审版本」原先语义含糊，改为明确的
  「撤回待审版本」动作。
- **清理悬空引用**：`localcraft-gc.*` 从未创建（与 `localcraft-maintenance.*` 重复），
  但 `docs/05` 与 `cli.py` 仍在引用，已统一为 `localcraft-maintenance.timer`。

---

## 三、本次是否包含数据库迁移

本次是否包含数据库迁移：**是**

1.0.0 是首发版本，从**空库**到 `head` 需要依次执行全部 4 个迁移。
已存在旧库时同样执行 `alembic upgrade head`（Alembic 按 revision 链幂等推进）。

| revision | 文件 | 内容 | 对旧代码向后兼容 |
| --- | --- | --- | --- |
| `0001` | `backend/migrations/versions/0001_initial_schema.py` | 建全 19 张业务表与索引；循环外键用 `use_alter=True` 后置添加；`tool_versions.is_current` 用 `op.execute()` 手写部分唯一索引 | 是（纯建表；空库前进） |
| `0002` | `backend/migrations/versions/0002_seed_roles_and_settings.py` | 播种 4 个内置角色与 `docs/02` §3.19 全部系统设置默认值（幂等，只插缺失行） | 是（种子数据，不建非空约束） |
| `0003` | `backend/migrations/versions/0003_create_fts_index.py` | 建 SQLite FTS5 虚表 `tool_search_index`；**非 SQLite 方言整体跳过**（PG 另写 `pg_trgm` / `tsvector` 迁移） | 是（只增表） |
| `0004` | `backend/migrations/versions/0004_add_skill_tree_truncated.py` | `tool_versions` 增加 `skill_tree_truncated` 布尔列（`server_default='0'`） | 是（加列 + 有默认值） |

- **`head` 指向：`0004`**
- 迁移必须在**服务启动之前**执行（`docs/05` §5.5 / §11.2 第 6 步）。
- 执行前务必先备份：`scripts/upgrade.sh` 会在迁移前自动做一份
  `backups/db/pre-upgrade-<旧版本>-<时间戳>.db`。
- **回滚纪律**：`0004` 的 `downgrade()` 是**有损**的（删除该列会永久丢掉
  "这个版本的文件树是否被截断"这一事实，且无法回填）。
  因此回滚的第一选择永远是**还原升级前备份**，而不是 `alembic downgrade`
  （`docs/05` §11.4 的完整论证）。`scripts/rollback.sh` 会在检测到数据库里有
  目标版本不认识的 revision 时给出明确提示。

---

## 四、升级与回滚入口

```bash
# 升级（先在测试机演练）
/opt/localcraft/scripts/upgrade.sh /root/localcraft-1.0.0-offline-<date>.tar.gz

# 只看会做什么，不动系统
/opt/localcraft/scripts/upgrade.sh --dry-run <tarball>

# 回滚到上一个 release
/opt/localcraft/scripts/rollback.sh            # 默认切回 current 之外的最近一个 release
/opt/localcraft/scripts/rollback.sh --list     # 先看有哪些 release 可回
/opt/localcraft/scripts/rollback.sh --to localcraft-0.9.0
```

升级前的准备清单见 `docs/05` §11.1；升级失败时脚本**只提示回滚命令，不自动回滚**
（自动回滚容易在"部分迁移已完成"的状态上造成二次事故）。

---

## 五、安装与卸载

```bash
# 安装（解压后的发布包根目录，root 执行；幂等）
tar -C /opt/localcraft/releases -xzf localcraft-1.0.0-offline-<date>.tar.gz
cd /opt/localcraft/releases/localcraft-1.0.0 && ./install.sh

# 卸载（默认保留数据与配置，必须显式 --purge 才删除）
/opt/localcraft/scripts/uninstall.sh            # 交互确认
/opt/localcraft/scripts/uninstall.sh --yes      # 自动化
/opt/localcraft/scripts/uninstall.sh --purge --yes   # 连数据目录与配置目录一起删（不可恢复）
```

---

## 六、兼容性与系统要求

| 项 | 要求 |
| --- | --- |
| 操作系统 | openEuler 24.03 LTS（同架构、同 SP 的构建机产出发布包） |
| CPU 架构 | `x86_64` 或 `aarch64`（wheelhouse 与架构严格对应，不可混用） |
| Python | 3.11.x（目标机需 `python3.11` / `python3.11-pip`，不要求联网） |
| 数据库 | SQLite（默认，WAL）或 PostgreSQL（需另装 `psycopg`，见 `pyproject.toml` 的 `pg` extra） |
| 其他 | systemd、nginx、sqlite3、rsync、tar、openssl、curl、jq（jq 为告警脚本所需，可选） |
| 浏览器 | 现代 Chromium / Firefox（SPA 直服形态依赖 `try_files`，无 IE 支持） |

---

## 七、已知限制与注意事项

- **单 worker**：SQLite WAL 单写者模型下 `--workers 1` 是刻意选择；
  若切 PostgreSQL，可在 `localcraft.service` 中调大 worker 数并重新做并发基线。
- **SQLite 实践上限**：接近 `docs/05` §9.5 的触发条件时应迁移 PostgreSQL。
- **`0004` 的 downgrade 有损**：见上文第三节。
- **CSP 使用 sha256 放行内联主题脚本**：前端若改动 `index.html` 里的内联
  `<script>`，必须同步更新 nginx CSP 里的哈希，否则首屏主题脚本被拦。
- **`deploy/` 在仓库中是平铺的**：`scripts/make-release.sh` 负责整理成发布包内的
  `deploy/systemd/`、`deploy/nginx/`、`deploy/logrotate/` 子目录。
- **告警口径**：`scripts/disk-alert.sh` 的存储水位阈值与后端
  `app/services/settings_service.py` 的 `storage_warning()` **同一口径**，
  阈值取自数据库 `system_settings` 表的 `quota.warn_threshold_pct`（读不到时回落 85）。
