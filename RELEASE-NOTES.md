# localcraft 发布说明

**版本：1.2.1** ｜ **发布日：2026-09-29** ｜ **目标平台：openEuler 24.03 LTS（x86_64 / aarch64）+ systemd + nginx**

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

1.2.1 是 v1.2.0 之后的**维护版本**（上一版 head 为迁移 `0009`）——**没有新功能**，
内容分四类：

1. **缺陷修复**：接口形状权威（`openapi.json`）漏声明两个关键查询参数；
   管理端一条过时文案；`docs/07` 里两处会误导管理员的断言。
2. **内部性能**：合并系统设置的点查，**每请求少发 10 条 SQL**（门户列表首页 19 → 9）。
3. **前端一致性**：页面容器宽度去重；两处警告横幅补齐 `variant`；状态点改用语义令牌。
4. **文档与运维体验**：`install.sh` 的收尾提示补上「启用两个定时器」；
   README / `docs/13` / `docs/05` 统一写清「装完还要做什么」；
   README 与 `docs/13` 讲清「下载 Release 的包**不需要构建机、不需要 Node**」；
   README 的 8 张界面截图按当前 UI 重抓。

> ### ★ 两处**肉眼可见**的变化（其余都是行为等价或纯文档）
>
> 1. **警告横幅的底色档位变了**：`我的工具`（「工具已下架」）与 `历史版本`（「接近保留上限」）
>    两处原先**漏写 `variant`**、靠手写 `bg-amber-50/60 dark:bg-amber-950/20` 凑观感；
>    现在改用 `variant="warning"`，**底色比原来略重**，并**新增**了深色态边框与琥珀色文字
>    （也就是"和其它警告框一致了"）。
> 2. **版本状态点的颜色微移**：`bg-amber-500` / `bg-emerald-500` 改为语义令牌
>    `bg-warning` / `bg-success`（8px 圆点上应不易察觉），
>    **并从此随主题变化** —— 改前它在明暗两态是同**一个**颜色。
>
> 这两处已做过 computed-style 与类名核对，但**未做人工审美评审**（见第八节）。

---

## 二、本版具体改了什么

### 2.1 缺陷修复

- **`openapi.json` 漏声明两个关键查询参数**（契约 §15.6 宣布它是接口形状的唯一权威，
  而**裸读**的查询参数不会进 spec）：
  - `GET /api/v1/images/{image_id}` 的 **`sig`** —— `<img>` 标签**唯一**的鉴权途径；
  - `GET /api/v1/tools/{slug}/download` 的 **`ticket`** —— 一次性下载票据。

  修法不是"再抄一份声明"，而是把参数声明成**读它的那个依赖**的查询参数
  （FastAPI 会把依赖的参数合并进使用它的 operation），并用参数替换裸读 ——
  让「读到的」与「声明的」成为同一个来源。
  另新增守卫测试，把「裸读的查询参数必须在 spec 里声明」变成会失败的检查
  （含定点断言：两个参数必须声明在**各自那个路由**上）。
- **管理端过时文案**（迁移 `0010`）：`webapp.health_check_enabled` 的描述长期写着
  「在线工具探活开关（二期）」，而探活 M14 早已实现接通 —— 那两个字会让管理员以为开关没用。
  管理端 label 取自**数据库列**，所以只能新开迁移修正。
- **`docs/07` 的两处误导性断言**：手册说 `quota.warn_threshold_pct` 与
  `approval.version_reapproval`「无消费方、设置了没有任何效果」，实际两者都已被 M5 接线
  （前者是 `/admin/overview` 的 `storage_warning` 与 `disk-alert.sh` 的**唯一口径**，
  后者控制"已发布工具的新版本是否免审"）。**而 `docs/09` 早就记着它们"已接线"** ——
  即两份文档长期互相矛盾，管理员手册是错的那一份。已连同 FAQ 与勘误表一并改正。

### 2.2 内部性能：合并设置点查（每请求 −10 条 SQL）

- `deps.get_visibility_context` 原先调 `get_security_policy()`，**只为拿
  `allow_admin_view_private` 一个布尔** —— 11 条语句换 1 个字段；改为只读那一个键。
- `get_security_policy` 自身从 11 次点查改为 **1 条 `IN` 查询**
  （三层兜底「行值 → 代码默认 → 调用方默认」逐字保持不变）。
- **实测（同一套 harness，改前 / 改后各跑一次）**：

  | 端点 | 改前 | 改后 |
  | --- | --- | --- |
  | 门户列表首页（含 facets，稳态） | 19 | **9** |
  | 门户列表第 2 页（不算 facets） | 14 | **4** |
  | 工具详情 | 20 | **10** |

- **响应形状与语义零变化**：省的是语句数，不是"少读请求" —— 仍然每请求读最新值
  （`FR-CFG-01` 的"设置改完立即生效"不受影响）。
- 每端点 SQL 上限守卫随实测收紧（25→12、19→7、25→13）。
- ⚠ **只实测了语句数，没有实测 CPU/吞吐收益**。依据是 `docs/11` §2.5 的 O4 结论
  （CPU 随并发放大的放大器是 `aiosqlite` 的专用工作线程那一跳，每条语句跨一次），
  但本版不把它折算成任何百分比性能承诺。

### 2.3 前端一致性

- **页面容器**：`max-w-[1400px]` + 响应式横向留白此前在 **8 个文件**里各写一遍
  （顶栏 / 页脚 / 两套布局 / 门户 / 详情 / 收藏 / 404 / 403），改为唯一常量
  `PAGE_CONTAINER`。**这一项零视觉变化**（已逐处比对类名集合，并用 computed style 复核
  `max-width: 1400px` / `padding-left: 32px`）。
- **两处警告横幅补齐 `variant="warning"`**（见上文"肉眼可见的变化"第 1 条）。
- **版本状态点改用语义令牌**（见上文"肉眼可见的变化"第 2 条）。

### 2.4 文档与运维体验

- **`install.sh` 收尾提示**：原来只列"三件事"，**没提两个定时器** ——
  而脚本只 `enable` 主服务，`localcraft-backup.timer` 与 `localcraft-maintenance.timer`
  装完是 disabled（这个取舍是刻意的，`docs/05` §5.13 与 `docs/08` §2.6 都有记录）。
  后果是「装完看着一切正常、几个月后才发现从不备份」。现改为"四件事"，
  并点明探活**还有第二个开关**（管理台「设置」里默认关闭，**timer 与该开关两者都满足**才会自动探测）。
- **README / `docs/13` / `docs/05` 统一"装完还要做什么"** 为同一份四步序列：
  建超级管理员 → 让外部能访问 → 启用两个定时器 → 验证。
  （改前 README 里 `create-superadmin` 与 timer **各 0 处**，照它做完会卡在登录页；
  `docs/13` 完全没有定时器那一步；`docs/05` §5.10 只 enable 了备份 timer。）
- **README 与 `docs/13` 讲清部署的两条路**：改前 README 的「部署到 openEuler」
  **只有构建者流程**（读它的人会以为必须先 `npm ci && npm run build`）——
  现在先说「方式 1：下载 Release 上构建好的包，**不需要构建机、不需要 Node / Python**」
  并给出可直接执行的下载+校验命令；构建者流程改名为「方式 2」并标注适用场景。
- **README 的 8 张界面截图重抓**（用仓库内的 `web/scripts/capture-screenshots.mjs`，
  跑在干净播种的实例上，任何人可复现）。8 张里 7 张有变化（登录页渲染确定性相同）。
- `docs/11` 记录了上述性能改动（新增 O13）并关闭了 D1~D4 四项遗留待办；
  `docs/05` §4.1 的发布包目录树按**真实发布包**逐项核对后重写。

---

## 三、本次是否包含数据库迁移

本次是否包含数据库迁移：**是**

相对 **v1.2.0（head `0009`）**，本版本新增 **1 支**迁移：

| revision | 文件 | 内容 | 对旧代码向后兼容 |
| --- | --- | --- | --- |
| `0010` | `backend/migrations/versions/0010_fix_health_check_setting_description.py` | **纯 UPDATE**：把 `webapp.health_check_enabled` 的管理端描述从「在线工具探活开关（二期）」改成「在线工具探活开关（随维护任务定时探测）」。**不改** `value` / `value_type` / `is_public`，**不增删行** | 是（只改一列文本） |

- **`head` 指向：`0010`**
- 迁移必须在**服务启动之前**执行（`docs/05` §5.5 / §11.2 第 6 步）。
- 执行前务必先备份：`scripts/upgrade.sh` 会在迁移前自动做一份
  `backups/db/pre-upgrade-<旧版本>-<时间戳>.db`。
- **回滚纪律**：
  - `0010` 的 `downgrade()` **无损**（新旧两段文案都冻结在该迁移文件里，可精确还原）；
  - 但**整条链**的回滚仍以**还原升级前备份**为第一选择 —— `0008` 的 `downgrade()`
    是**有损**的（删除那 5 行设置，管理员改过的值会丢失），见 v1.2.0 的说明与 `docs/05` §11.4。
- 从 **v1.1.0（head `0007`）或更早**升级时，还会依次跑 `0008` / `0009`（v1.2.0 引入）。

---

## 四、升级与回滚入口

```bash
# 升级（先在测试机演练；发布包由 GitHub Actions 在 Release 页产出）
/opt/localcraft/scripts/upgrade.sh /root/localcraft-1.2.1-offline-<date>.tar.gz

# 只看会做什么，不动系统
/opt/localcraft/scripts/upgrade.sh --dry-run <tarball>

# 回滚到上一个 release
/opt/localcraft/scripts/rollback.sh            # 默认切回 current 之外的最近一个 release
/opt/localcraft/scripts/rollback.sh --list     # 先看有哪些 release 可回
/opt/localcraft/scripts/rollback.sh --to localcraft-1.2.0
```

升级前的准备清单见 `docs/05` §11.1；升级失败时脚本**只提示回滚命令，不自动回滚**
（自动回滚容易在"部分迁移已完成"的状态上造成二次事故）。

---

## 五、安装与卸载

```bash
# 安装（解压后的发布包根目录，root 执行；幂等）
tar -C /opt/localcraft/releases -xzf localcraft-1.2.1-offline-<date>.tar.gz
cd /opt/localcraft/releases/localcraft-1.2.1 && ./install.sh
```

发布包内自带**编好的前端产物**与 **x86_64 / aarch64 两套 wheelhouse**，
目标机**不需要 Node、不需要联网**。包内的 `SHA256SUMS` 可校验全部文件；
Release 页另附 tar.gz 自身的 `.sha256`。

> ### ★ `install.sh` 跑完只是「服务起来了」，**还登录不了**
>
> 它只在半程 `enable` 主服务（两个定时器刻意留给你决定）。装完还有 4 件事：
>
> ```bash
> # 1) 建超级管理员（不做就没有账号；密码走交互输入，不要用明文参数）
> sudo -u localcraft bash -c '
>   set -a; . /etc/localcraft/localcraft.env; set +a
>   cd /opt/localcraft/app/current
>   /opt/localcraft/venv/bin/python -m app.cli create-superadmin --username admin
> '
> # 2) 让外部能访问（默认只听 127.0.0.1）
> sudo sed -i 's/^LOCALCRAFT_HOST=.*/LOCALCRAFT_HOST=0.0.0.0/' /etc/localcraft/localcraft.env
> sudo systemctl restart localcraft.service
> sudo firewall-cmd --add-port=8000/tcp --permanent && sudo firewall-cmd --reload
> # 3) 启用两个定时器（不做就永远没有每日备份、探活也不会自动跑）
> sudo systemctl enable --now localcraft-backup.timer localcraft-maintenance.timer
> # 4) 验证
> curl -sS http://127.0.0.1:8000/healthz && curl -sS http://127.0.0.1:8000/readyz
> ```
>
> 逐项解释见 `docs/13-新手部署指南` 的第 3~6 步。

```bash
# 卸载（默认保留数据与配置，必须显式 --purge 才删除）
/opt/localcraft/scripts/uninstall.sh            # 交互确认
/opt/localcraft/scripts/uninstall.sh --yes      # 自动化
/opt/localcraft/scripts/uninstall.sh --purge --yes   # 连数据目录与配置目录一起删（不可恢复）
```

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
  它既不重写 `tool_images` 行也不重新生成封面文件。因此**在 1.2.0 之前播种的实例**，
  升级后演示数据的封面**仍是旧尺寸（320×180、无标记）**，除非重建库或原地重生封面文件。
  详见 `docs/09` §20（含原地重生的做法与「签名不绑定内容因而可安全覆盖」的依据）。
- **限流的 loopback 豁免**：见 v1.2.0 的说明。自建 nginx 配置时务必确认
  `--proxy-headers --forwarded-allow-ips=127.0.0.1` 与 `X-Forwarded-For` 都已就位。
- **探活不等于可用性**：探活**不跟随重定向**（3xx 即记为 ok），因此「探活成功」
  ≠「端到端可用」；且探活**不会**自动把工具下线或改状态。
  （探活开关的过时文案「（二期）」**已由迁移 `0010` 修正** ——
  跑过 `0010` 的实例在管理台看到的是新文案。）
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

- 开发与自动化验证在 **macOS aarch64** 上完成：SQLite 全量测试；
  **PostgreSQL 16 在 UTC 与 Asia/Shanghai 两个时区下各 637 passed / 1 skipped**；
  前端 `npm run verify`、mock E2E 121 passed、真实后端 E2E 19 passed；
  CI 上五类 job（仓库卫生 / 前端 / 后端 ruff+pytest / 两个 PG 时区）全部通过。
- **未在 openEuler 目标机上实机安装运行**；**aarch64 的 wheel 未在真实 arm 机器上加载验证**；
  systemd 单元、离线 wheelhouse 的实际加载、探活对真实内网 URL 的探测、
  nginx 反代链路均属**未验证**。
- 本发布包是**构建物**，不等于「已在目标环境验证过的发布物」。
- **前端视觉未做人工审美评审**：本版两处可见变化（警告横幅底色档位、状态点颜色）
  只做过 computed-style、类名与像素解码核对；8 张 README 截图也只验证了
  「渲染出非空内容 + 文本指纹」，**观感需人眼确认**。
