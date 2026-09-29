# localcraft 发布说明

**版本：1.2.0** ｜ **发布日：2026-09-29** ｜ **目标平台：openEuler 24.03 LTS（x86_64 / aarch64）+ systemd + nginx**

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

1.2.0 是 v1.1.0 之后的**功能版本**（上个版本为 1.1.0，head 为迁移 `0007`），
主要包含四块：

1. **运维能力补全** —— 应用侧限流、在线工具探活（此前完全没有），配套管理端设置项；
2. **PostgreSQL 全文检索** —— PG 从「能跑」变为「检索能力与 SQLite 对齐」；
3. **门户分页签** —— 全部工具 / 最近使用 / 我的收藏；
4. **预览图与占位整改** —— 去掉按分类上色的彩色渐变；详情页无图时图区完全收起；
   种子演示封面改为浅中性底 + 淡几何标记。

**默认行为的变化是刻意最小化的**：限流默认**开启**但配额宽松（1200 次/分钟/IP，
一次门户加载约 21 个请求）；探活默认**关闭**；新增设置项的默认值与升级前一致。

---

## 二、里程碑概要

### M14 — 应用侧限流 + 在线工具探活（迁移 `0008`）

- **应用侧限流**：进程内滑动窗口（键 `(scope, client_ip)`，窗口 60 秒，桶上限 10000），
  三档配额 `api` 1200 / `login` 10 / `upload` 30（次/分钟/IP），总开关
  `security.rate_limit_enabled`。被拒返回 **429 + `RATE_LIMITED` + `Retry-After`**。
  这是与 nginx `limit_req` **叠加**的第二层，不是替代品。
- **★ loopback 豁免**：来自 `127.0.0.1` / `::1` / `localhost` 的请求不计入限流
  （与 `/metrics` 同源的理由：本机健康检查与监控不能被限流打死）。
  本仓库自带的 systemd 单元与 nginx 配置**都已正确透传真实客户端 IP**，
  故按本方案部署时限流正常生效；**自写/裁剪配置时若漏掉透传，限流会静默整体失效**
  （所有请求都被当成来自本机）—— 见 `docs/05` §5.14.4。
- **在线工具探活**：对 `tool_type='webapp'` 且填了 `webapp_health_url` 的工具做
  `HEAD` 探测（**405/501 回退 `GET`**），超时 5 秒、并发 8、单次上限 200；
  **不跟随重定向**（3xx 记为 ok 且不请求 Location）、**显式禁用环境代理**。
  结果写 `webapp_health_status` / `webapp_checked_at`，**`NULL`（从未探测）不等于 `fail`**。
  **不会自动下线工具**。受 `webapp.health_check_enabled` 控制，**默认关闭**。
  调度入口是 `localcraft-maintenance.timer`（每小时 :20 → `--task all`），
  但 `install.sh` 只 enable 主服务，**timer 需手工 enable**。
- **迁移 `0008`**：补播种 `images.signature_ttl_hours`（默认 168 小时）
  与 4 个限流设置项。M5 引入的签名有效期设置项此前**从未被任何迁移播种**，一并补上。
- 修掉三个「存在但未接通」的半成品（死代码 `RateLimitedError`、未播种的设置项、
  没接调度入口的探活），详见 `docs/09` §18。

### M15 — PostgreSQL 全文检索（迁移 `0009`）

- `tools` 增加 `search_vector`（`tsvector`，**`simple` 配置**）+ GIN 索引，
  由 `0009` 一次性回填。检索词与 SQLite FTS5 走**同一套切分**：
  中文按单字切分，英文/数字保持整词。
- **PG 中文检索不需要任何扩展**（不需要 `zhparser`）—— 单字切分对
  `to_tsvector('simple', …)` 同样成立。
- 迁移 `0009` **只对 PostgreSQL 生效**；SQLite 继续走既有的 FTS5 虚表（迁移 `0003`）。
- **已知差异（不修）**：PG 与 SQLite 在字符层面有 7 条检索差异
  （例如 PG 搜 `node` 匹配不到 `node.js`），已用 23 条探针量化并记录在 `docs/09` §19。

### M16 — 门户分页签

- 门户新增「全部工具 / 最近使用 / 我的收藏」三个分页签，`?tab=all|recent|favorites`。
  **这是纯前端的 URL 状态**，后端 `GET /api/v1/tools` 不认识 `tab` 参数。
- **只有一个分页签有内容时，连 Tabs 外壳都不渲染**；工具栏与分页控件只在
  「全部工具」下出现。

### M17 — 预览图与占位统一为中性色

- 去掉按分类 / 按类型两套彩色渐变，统一走唯一零参常量
  `TOOL_PLACEHOLDER_CLASS = "bg-muted"`（语义令牌，深色主题下是深中性而非白色）。
- **类型徽标（文件包 / 在线工具 / Skill）保持彩色** —— 它是功能性小标签，不属于预览图。
- 用户**真实上传**的封面一律不动。

### M18 — 详情页图区收起 + 种子封面加淡标记

- **无图时详情页图廊完全不渲染**（元素不存在，不是 `hidden` / `0` 高度）：
  两种情形都收起 —— 后端没下发任何图，**或每一张图都加载失败**。
  加载中不收起（按 `onError` 累积判定），避免「先收起再冒出来」。
  ★ 范围限定：**只作用于详情页图廊**；门户卡片 / 我的工具 / 回收站的缩略图占位照旧。
- **种子演示封面**：浅中性底上画淡几何标记（圆角矩形轮廓 + 内切圆），
  颜色 = 底色各通道**同减 14**（零色相，不引入彩色）；
  全尺寸 **320×180 → 1280×720**（详情页拉伸近 4 倍，画了标记后小图会糊），
  缩略图仍 160×90。

---

## 三、本次是否包含数据库迁移

本次是否包含数据库迁移：**是**

相对 **v1.1.0（head `0007`）**，本版本新增 `0008`、`0009` 两支迁移。
从更早的 **v1.0.0（head `0004`）** 直接升级时，还会依次执行 `0005` / `0006` / `0007`
（v1.1.0 引入）。Alembic 按 revision 链幂等推进，已执行过的不重复执行。

| revision | 文件 | 内容 | 对旧代码向后兼容 |
| --- | --- | --- | --- |
| `0008` | `backend/migrations/versions/0008_rate_limit_and_image_ttl_settings.py` | 播种 5 个设置项：`images.signature_ttl_hours`、`security.rate_limit_enabled` / `rate_limit_per_minute` / `rate_limit_login_per_minute` / `rate_limit_upload_per_minute`（幂等，只插缺失行） | 是（纯种子数据） |
| `0009` | `backend/migrations/versions/0009_postgres_fts.py` | PostgreSQL：`tools.search_vector`（`tsvector`，`simple`）+ GIN 索引 + 回填。**非 PostgreSQL 方言整体跳过** | 是（加可空列 + 索引） |

- **`head` 指向：`0009`**
- 迁移必须在**服务启动之前**执行（`docs/05` §5.5 / §11.2 第 6 步）。
- 执行前务必先备份：`scripts/upgrade.sh` 会在迁移前自动做一份
  `backups/db/pre-upgrade-<旧版本>-<时间戳>.db`。
- **回滚纪律**：
  - `0009` 的 `downgrade()` **可重建**（删掉的是由 `tools` 派生的索引列，
    重新升级会再次回填），且**只对 PostgreSQL 有动作**。
  - `0008` 的 `downgrade()` **有损**：它删除那 5 行设置，管理员改过的值会丢失，
    重新升级只会还原成默认值。
  - 因此回滚的第一选择仍是**还原升级前备份**，而不是 `alembic downgrade`
    （`docs/05` §11.4 的完整论证）。`scripts/rollback.sh` 会在检测到数据库里有
    目标版本不认识的 revision 时给出明确提示。

---

## 四、升级与回滚入口

```bash
# 升级（先在测试机演练；发布包由 GitHub Actions 在 Release 页产出）
/opt/localcraft/scripts/upgrade.sh /root/localcraft-1.2.0-offline-<date>.tar.gz

# 只看会做什么，不动系统
/opt/localcraft/scripts/upgrade.sh --dry-run <tarball>

# 回滚到上一个 release
/opt/localcraft/scripts/rollback.sh            # 默认切回 current 之外的最近一个 release
/opt/localcraft/scripts/rollback.sh --list     # 先看有哪些 release 可回
/opt/localcraft/scripts/rollback.sh --to localcraft-1.1.0
```

升级前的准备清单见 `docs/05` §11.1；升级失败时脚本**只提示回滚命令，不自动回滚**
（自动回滚容易在"部分迁移已完成"的状态上造成二次事故）。

---

## 五、安装与卸载

```bash
# 安装（解压后的发布包根目录，root 执行；幂等）
tar -C /opt/localcraft/releases -xzf localcraft-1.2.0-offline-<date>.tar.gz
cd /opt/localcraft/releases/localcraft-1.2.0 && ./install.sh

# 卸载（默认保留数据与配置，必须显式 --purge 才删除）
/opt/localcraft/scripts/uninstall.sh            # 交互确认
/opt/localcraft/scripts/uninstall.sh --yes      # 自动化
/opt/localcraft/scripts/uninstall.sh --purge --yes   # 连数据目录与配置目录一起删（不可恢复）
```

发布包内自带前端产物与 **x86_64 / aarch64 两套 wheelhouse**，
目标机**不需要 Node、不需要联网**。包内的 `SHA256SUMS` 可校验全部文件；
Release 页另附 tar.gz 自身的 `.sha256`。

---

## 六、兼容性与系统要求

| 项 | 要求 |
| --- | --- |
| 操作系统 | openEuler 24.03 LTS SP1~SP4（x86_64 与 aarch64 都有对应 wheelhouse） |
| CPU 架构 | `x86_64` 或 `aarch64`（wheelhouse 与架构严格对应，不可混用） |
| Python | 3.11.x（目标机需 `python3.11` / `python3.11-pip`，不要求联网） |
| 数据库 | SQLite（默认，WAL）或 PostgreSQL（需另装 `psycopg`，见 `pyproject.toml` 的 `pg` extra） |
| 其他 | systemd、nginx、sqlite3、rsync、tar、openssl、curl、jq（jq 为告警脚本所需，可选） |
| 浏览器 | 现代 Chromium / Firefox（SPA 直服形态依赖 `try_files`，无 IE 支持） |

---

## 七、已知限制与注意事项

- **单 worker**：SQLite WAL 单写者模型下 `--workers 1` 是刻意选择。
  限流器是**进程内**状态，因此**调大 worker 数会让实际总配额 ≈ 配置值 × worker 数**；
  若切 PostgreSQL 并调大 worker，请同时重算限流配额与并发基线。
- **SQLite 实践上限**：接近 `docs/05` §9.5 的触发条件时应迁移 PostgreSQL。
- **★ 种子封面的变化不会自动作用于已播种的实例**：`seed-demo` 对已存在的工具是幂等的，
  它既不重写 `tool_images` 行也不重新生成封面文件。所以升级到 1.2.0 之后，
  **演示数据的封面仍是旧尺寸（320×180、无标记）**，除非重建库或原地重生封面文件。
  详见 `docs/09` §20（含原地重生的做法与「签名不绑定内容因而可安全覆盖」的依据）。
- **限流的 loopback 豁免**：见上文 M14。自建 nginx 配置时务必确认
  `--proxy-headers --forwarded-allow-ips=127.0.0.1` 与 `X-Forwarded-For` 都已就位。
- **探活不等于可用性**：探活**不跟随重定向**（3xx 即记为 ok），因此「探活成功」
  ≠「端到端可用」；且探活**不会**自动把工具下线或改状态。
  `webapp.health_check_enabled` 在管理端的描述文案仍写着「（二期）」，M14 已实现，
  文案待改（`docs/09` §18.3）。
- **`/metrics` 里没有限流指标**：`localcraft_request_errors_total` 只统计 5xx，429 不计入。
  观测限流只能靠应用 JSON 日志（grep `"status": 429`），见 `docs/08` §6.12。
- **PostgreSQL 检索的字符层面差异**：7 条已知差异不修（`docs/09` §19）。
- **CSP 使用 sha256 放行内联主题脚本**：前端若改动 `index.html` 里的内联
  `<script>`，必须同步更新 nginx CSP 里的哈希，否则首屏主题脚本被拦。
- **`deploy/` 在仓库中是平铺的**：`scripts/make-release.sh` 负责整理成发布包内的
  `deploy/systemd/`、`deploy/nginx/`、`deploy/logrotate/` 子目录。
- **告警口径**：`scripts/disk-alert.sh` 的存储水位阈值与后端
  `app/services/settings_service.py` 的 `storage_warning()` **同一口径**，
  阈值取自数据库 `system_settings` 表的 `quota.warn_threshold_pct`（读不到时回落 85）。

---

## 八、本版本的验证边界（诚实说明）

- 开发与自动化验证在 **macOS aarch64** 上完成：SQLite 全量测试、PostgreSQL 16
  在两个时区（UTC / Asia/Shanghai）下的全量测试、前端 `verify`、mock 与真实后端两套 E2E
  均为绿色；CI 上这五类 job 全部通过。
- **未在 openEuler 目标机上实机安装运行**；**aarch64 的 wheel 未在真实 arm 机器上加载验证**；
  systemd 单元、离线 wheelhouse 的实际加载、探活对真实内网 URL 的探测、
  nginx 反代链路均属**未验证**。
- 本发布包是**构建物**，不等于「已在目标环境验证过的发布物」。
- 前端视觉仅做过对比度与几何量化验证（含像素级解码核对），
  **未做人工审美评审**。
