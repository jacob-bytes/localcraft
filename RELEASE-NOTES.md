# localcraft 发布说明

**版本：1.2.2** ｜ **发布日：2026-10-10** ｜ **目标平台：openEuler 24.03 LTS（x86_64 / aarch64）+ systemd + nginx**

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

1.2.2 是 v1.2.1 之后的**维护版本**（**没有新功能**），但它包含**两条会改变启动行为**的硬拦 ——
这是本项目第一次把「配置错了」与「库结构不对」从"带着病跑"改成**拒绝启动**：

1. **两条启动期硬拦（★ 行为变更，见 §1.1）**；
2. **健壮性**：搜索词封顶、删掉一处死代码、安全关键模块的错误分支覆盖率补到 100%；
3. **可访问性**：修掉一个真实不合规的图标，并把「对比度 ≥3:1 / ≥4.5:1」与
   「命中区 ≥24×24」变成**可执行检查**（含变异测试证明它不是空转）；
4. **文档**：补上发版流程（含一个**两次踩到**的 `git tag` 陷阱）；纠正与代码不符的记录。

### 1.1 ★ 两条行为变更（升级前必读）

| 变更 | 之前的行为 | 现在的行为 |
| --- | --- | --- |
| `SECRET_KEY` 仍是**仓库里公开的**默认值或占位符 | 照常启动。JWT 与图片签名都由它派生子密钥 ⇒ **任何人都能伪造超管令牌** | **拒绝启动**。错误信息给出修法与逃生阀 `LOCALCRAFT_ALLOW_INSECURE_SECRET=1`（本地开发/CI 用，生产不要设） |
| 数据库结构**不在**代码的 head 上 | 照常启动、`/readyz` 也通过（它只 `SELECT 1`），然后在某个具体查询上抛难懂的 500 | **拒绝启动**，且**库落后与库超前都拒绝**。错误信息分别给出「跑 `alembic upgrade head`」与「还原升级前的备份，不要 downgrade」 |

**自动安装/升级路径完全不受影响**（两条守卫的顺序都对得上）：

  - `install.sh`：7/10 写环境变量文件（内含 `openssl rand -hex 32` 生成的真密钥）
    → 8/10 数据库迁移 → 9/10 装 systemd 单元 → 10/10 启动并验证；
  - `upgrade.sh`：1/9 停服务 → 2/9 备份 → … → **6/9 迁移 → 7/9 启动**；
  - `rollback.sh`：**它本来就有自己的同类保护**（读 `alembic_version` 比对，
    库比目标版本新且没给 `--restore-db` / `--allow-db-ahead` 就中止，并明说"这是保护，不是故障"）。
    它与本次新增的**运行时**校验问的是**不同的问题** ——
    回滚脚本问「我要回滚到的那个版本，能跑在这个库上吗」，
    守卫问「我这个进程要跑的库，和我的代码是一对吗」——所以是互补，不是重复实现。

**会被这两条拦住的只有"手工/非常规"路径**：手抄 `docs/05` §5.1~5.9 逐步部署而漏设密钥或漏跑迁移；
或在迁移之前就 `systemctl restart`。**这两种情况以前都是静默出问题。**

---

## 二、本版具体改了什么

### 2.1 两条启动期硬拦

- **`SECRET_KEY` 公开值一律拒绝**（`app/core/config.py`）：
  新增权威清单 `INSECURE_SECRET_VALUES`，覆盖**两个**来源 ——
  `Settings.secret_key` 的代码默认值 `dev-only-insecure-secret-change-me`，以及
  `deploy/localcraft.env.example` 里的占位符 `REPLACE_WITH_openssl_rand_hex_32`。
  由 `_reject_public_secret`（`model_validator(mode="after")`，不依赖字段声明顺序）在
  **配置加载期**拒绝。

  > **为什么值得单列**：原先只有一条「短于 32 字节就 warning」的检查，而代码默认值
  > 是 **34 字节**，**连那条 warning 都不触发** —— 也就是说漏设密钥时应用静默启动。
  > 更值得注意的是：**这条检查其实早就存在，只是没接到启动路径上** ——
  > `scripts/security-check.sh` 会查占位符，但它查的是示例 env 文件里的那个，
  > **漏了代码默认值**，而且只在安装后手工跑一次。本版把两侧补齐并让它们一致。

- **库结构不在 head 上就拒绝启动**（新增 `app/core/schema_guard.py`，在 `main.py` 的 lifespan 里调用）：
  用 `ScriptDirectory` 取代码的 head、`MigrationContext` 读库里的 revision，不一致即抛
  `SchemaDriftError`。`script_location` 显式用**绝对路径**（`alembic.ini` 里写的是相对的
  `migrations`，Alembic 按**当前工作目录**解析 —— systemd 拉起时的 CWD 是 `WorkingDirectory=`，
  不赌这个）。

  > **动机是一个真实踩过的坑**：开发中预览库停在 `0009`、代码已到 `0010`，
  > 而 `preview.sh` 原先**只在库不存在时**才跑迁移，于是服务带着旧 schema 起来、
  > `/readyz` 照样 200，排查方向被完全带偏。本版同时修了那个根因：
  > `preview.sh` 改为**每次都跑幂等迁移**（注意"是否首次建库"必须在**迁移之前**判断，
  > 否则新建的库会被当成已存在而跳过播种）。

- **`JWT_ALGORITHM` 收窄取值**（HS256/HS384/HS512）：`jwt.decode` 已经把 `algorithms`
  固定成这个字段的值，所以这不是补洞而是**防呆** —— 误设成 `none` / `RS256`
  会在运行期以很难懂的方式失败。

### 2.2 健壮性

- **★ `ALLOWED_EXTENSIONS` 现在同时接受逗号串与 JSON 数组**（原来只接受 JSON）。
  这是**修一个"照文档做就起不来"的坑**：`list[str]` 在 pydantic-settings 里按 **JSON** 解析，
  而 `docs/05` 的两处示例写的都是逗号串 —— 实测后果是**启动期硬失败**，且报错里一个字都没提 JSON：

  ```
  $ ALLOWED_EXTENSIONS=zip,png python -m uvicorn app.main:app
  pydantic_settings.exceptions.SettingsError:
  error parsing value for field "allowed_extensions" from source "EnvSettingsSource"
  ```

  修法不是把文档改成 JSON（在 env 文件里写 JSON 数组很别扭，而且**文档已经证明人会写逗号串**），
  而是让代码两种都收：字段加 `NoDecode`（跳过 pydantic-settings 的源层 JSON 解码 ——
  否则 `SettingsError` 会在校验器之前抛出，`mode="before"` 校验器根本救不了）+ 一个
  `_coerce_extension_list` 校验器。已端到端实测：逗号串 15 项 ✅ / JSON 数组 ✅ / 空串 → `[]` ✅ /
  以 `[` 开头但 JSON 非法时**仍然报错**（不用"容错"掩盖真正的配置错误）。
  另附 9 条单测（`tests/test_config_allowed_extensions.py`）。

- **搜索词 `q` 封顶 200 字符**（后端 `max_length=200` → 超限 422；前端搜索框同步 `maxLength`）。
  > 说明清楚：**这不是在补安全洞**。SQLite 那条路对 FTS5 元字符本来就是安全的 ——
  > 每个 token 都被双引号包成短语且 `"` 被转义，查询是绑定参数而非拼接，异常还会优雅降级。
  > 这里纯粹是边界封顶（内网也不该假设客户端守规矩）。
- **删掉 `LocalStorage.delete_sync()` 死代码**：它的 docstring 写着"供事务提交后触发的淘汰使用"，
  但**全仓零调用** —— 真实淘汰用的是异步 `storage.delete()`（提交后执行、失败只记
  `deleted=False` 并交给孤儿清理兜底），`StorageBackend` 协议里也从未声明它。
- **安全关键模块的错误分支补到 100%**：`app/storage/local.py` 从 **83% → 100%**
  （分母也从 146 行降到 132 行）。其中两条**必须用故障注入**才到得了：
  `usage_bytes()` 遇到 `stat()` 失败（正常路径走不到 —— `Path.is_file()` 会吞掉 `OSError`）、
  `delete()` 遇到 `unlink()` 失败（**必须返回 False 而不是抛** —— 一抛就会把已经提交的审批结果变成 500）。

### 2.3 可访问性（`web/e2e/a11y.spec.ts`）

- **修掉一个真实不合规的图标**：`TokensPage` 的权限提示图标原先是 `text-amber-500`，
  浅色下实测 **2.1:1**（非文本图形要求 ≥3:1），而且它是全站**最后一个不带 `dark:` 的调色板前景色**。
  改为 `amber-700 / dark:amber-400`（实测 **4.95:1 / 11.13:1**）。

  > ★ **一个反直觉的实测结论**：**不能**改成语义令牌 `text-warning` ——
  > `--warning` 的取值（浅色 `oklch(0.75 0.15 75)`）是给**背景**用的，
  > 当图标色只有 **2.24:1**，同样不合规。

- **把标准变成会失败的检查**（原先 §34.5① 只有 M9/M12/M14 里的**定点**断言，
  而且上一轮的复测是用临时脚本跑的、判据没留下来）：

  | 检查 | 覆盖 | 判据 |
  | --- | --- | --- |
  | 全站文本对比度（**通用扫描，不看清单**） | 15 页 × 浅/深两态，`main` 下每个直接含文本的元素 | 4.5:1（大字号 3:1） |
  | 非管理页深色「近白大块」 | 门户 / 我的工具 / 我的收藏 / 详情 / 404 | 深色下无 r,g,b>240 且高>40 的块 |
  | 权限提示图标 | `/admin/tokens` 的对话框 | 3:1 |
  | 表格 checkbox 命中区 | `/admin/users` 表头与行 | ≥24×24（WCAG 2.5.8，实测 **32×32**） |

  通用扫描的价值是**能发现新加的元素**（定点断言做不到）；而非文本图形仍靠策展清单
  （"哪些图标算有意义"无法机械判定，装饰性图标按 WCAG 豁免）。

- **这套检查做过变异测试**：把图标改回 `amber-500` → 图标那条失败；
  把门户一段文字改成 `text-gray-300` → 通用扫描失败并报 `[light] / 需要 4.5:1，实测 1.45:1`。
  还原后 4 条全过。**两个踩过的坑写在 spec 的文档注释里**（背景解析必须从元素**自身**开始找，
  否则一次误报 28 处；Tailwind v4 只生成源码里用到的类，所以临时换"没被编译"的类不会有任何效果）。

### 2.4 文档

- **`CONTRIBUTING.md` 补上「发版流程」**（原先**完全没有**这一节，这正是同一个坑被踩两次的原因），
  含：版本号 5 处对齐、`RELEASE-NOTES.md` 那条行首关键行、打 tag 的完整命令、
  **推 tag 之前的自查**、推 tag 之后要验证的三件事。
  > 并写明一个**代价很实的陷阱**：`git tag -a -F <file>` 的默认清理模式是 `strip`
  > （`git tag --help` 原文 "The _strip mode is default"），它会**删掉所有以 `#` 开头的行** ——
  > 也就是 Markdown 的全部标题。难发现是因为 tag 注释与 Release 正文**逐字一致**，
  > 从 Release 页看不出少了东西。**v1.2.0 与 v1.2.1 都踩过**（v1.2.1 已用
  > `--cleanup=verbatim` 重建修复；v1.2.0 的正文见下）。
- **`docs/12` 纠正一处与代码不符的记录**：`U3 表格 checkbox 命中区` 的状态表长期写着
  「⬜ 待做」，而代码早已按该节建议修好（注释还引用了 `§U3`）。已标 ✅、补上"已修"说明，
  并**补上原本完全缺失的守卫**（上面那条 ≥24×24 的检查）。
- 其余与代码不符之处见 §2.5。

### 2.5 文档与代码矛盾清扫（本轮系统扫了一遍）

见文末「本版修正的文档矛盾」小节。

---

## 三、本次是否包含数据库迁移

本次是否包含数据库迁移：**否**

相对 **v1.2.1（head `0010`）**，本版本**没有新增迁移**，head 保持 **`0010`**。
`upgrade.sh` 仍会执行一次 `alembic upgrade head`（应为空操作，脚本自己也会提示"本次发布包声明不含迁移"）。

- 从 **v1.2.0 或更早**升级时，`0010`（以及更早的 `0008`/`0009`）仍会被依次执行。
- 回滚纪律不变：整条链的回滚仍以**还原升级前备份**为第一选择 ——
  `0008` 的 `downgrade()` 是**有损**的（会删除那 5 行设置，管理员改过的值会丢）。
- ★ 本版新增的启动守卫会**让"只换代码不还原库"的回滚直接起不来** ——
  这是有意的（旧代码跑在新 schema 上会写坏数据），且 `rollback.sh` 原本就会拦这一种。

---

## 四、升级与回滚入口

```bash
# 升级（先在测试机演练；发布包由 GitHub Actions 在 Release 页产出）
/opt/localcraft/scripts/upgrade.sh /root/localcraft-1.2.2-offline-<date>.tar.gz

# 只看会做什么，不动系统
/opt/localcraft/scripts/upgrade.sh --dry-run <tarball>

# 回滚
/opt/localcraft/scripts/rollback.sh --list              # 先看有哪些 release 可回
/opt/localcraft/scripts/rollback.sh                     # 切回最近一个 release
/opt/localcraft/scripts/rollback.sh --to localcraft-1.2.1
```

---

## 五、安装与卸载

```bash
tar -C /opt/localcraft/releases -xzf localcraft-1.2.2-offline-<date>.tar.gz
cd /opt/localcraft/releases/localcraft-1.2.2 && ./install.sh
```

发布包内自带**编好的前端产物**与 **x86_64 / aarch64 两套 wheelhouse**，
目标机**不需要 Node、不需要联网**。

> ### ★ `install.sh` 跑完只是「服务起来了」，**还登录不了**
>
> 它只在半程 `enable` 主服务（两个定时器刻意留给你决定）。装完还有 4 件事：
> 建超级管理员 → 让外部能访问 → 启用两个定时器 → 验证。
> 完整命令见 `docs/13-新手部署指南` 第 3~6 步与仓库根 `README.md`。

```bash
# 卸载（默认保留数据与配置，必须显式 --purge 才删除）
/opt/localcraft/scripts/uninstall.sh --yes
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
| 其他 | systemd、nginx、sqlite3、rsync、tar、openssl、curl、jq（告警脚本所需，可选） |
| 浏览器 | 现代 Chromium / Firefox（SPA 直服形态依赖 `try_files`，无 IE 支持） |

★ 本版新增的**配置要求**：`SECRET_KEY` 必须是**非公开值**（用 `openssl rand -hex 32` 生成，
`install.sh` 会自动做）；`JWT_ALGORITHM` 只能是 `HS256` / `HS384` / `HS512`。

---

## 七、已知限制与注意事项

- **单 worker**：SQLite WAL 单写者模型下 `--workers 1` 是刻意选择。
  限流器是**进程内**状态，因此**调大 worker 数会让实际总配额 ≈ 配置值 × worker 数**。
- **★ 启动守卫的边界**：它比对的是 `alembic_version` 与**代码里的 head**。
  如果你手工改过 `migrations/`（例如删掉某支迁移文件），head 会变，守卫可能拦下正常的库 ——
  请改代码而不是改库。
- **★ 种子封面的变化不会自动作用于已播种的实例**：`seed-demo` 对已存在的工具是幂等的，
  既不重写 `tool_images` 行也不重新生成封面文件。**在 1.2.0 之前播种的实例**升级后，
  演示数据封面仍是旧尺寸（320×180、无标记）。做法与依据见 `docs/09` §20。
- **限流的 loopback 豁免**：来自 `127.0.0.1` / `::1` / `localhost` 的请求不计入限流。
  自建 nginx 配置时务必确认 `--proxy-headers --forwarded-allow-ips=127.0.0.1`
  与 `X-Forwarded-For` 都已就位，否则限流会静默整体失效。
- **探活不等于可用性**：探活**不跟随重定向**（3xx 即记为 ok），且**不会**自动下线工具。
  探活受两个开关共同控制：`localcraft-maintenance.timer` 与
  管理台「设置」里的「在线工具探活」（默认关闭）——**两者都满足**才会自动探测。
- **`/metrics` 里没有限流指标**：`localcraft_request_errors_total` 只统计 5xx，429 不计入。
- **PostgreSQL 检索的字符层面差异**：7 条已知差异不修（`docs/09` §19）。
- **CSP 使用 sha256 放行内联主题脚本**：改动 `index.html` 里的内联 `<script>`
  必须同步更新 nginx CSP 里的哈希。
- **`deploy/` 在仓库中是平铺的**：`scripts/make-release.sh` 负责整理成发布包内的
  `deploy/systemd/`、`deploy/nginx/`、`deploy/logrotate/` 子目录。

---

## 八、本版本的验证边界（诚实说明）

- 开发与自动化验证在 **macOS aarch64** 上完成：SQLite 全量测试；
  PostgreSQL 16 在 UTC 与 Asia/Shanghai 两个时区下各 **637 passed / 1 skipped**（v1.2.1 基线）；
  前端 `npm run verify` 退出码 0；mock E2E **125 passed**；
  `contracts/acceptance/pre-delivery-checks.sh` **PASS=35 / FAIL=0 / BLOCKED=1**
  （A5 shellcheck 在本机未安装）；CI 五类 job 全绿。
- **未在 openEuler 目标机上实机安装运行**；**aarch64 的 wheel 未在真实 arm 机器上加载验证**；
  systemd 单元、离线 wheelhouse 的实际加载、探活对真实内网 URL 的探测、
  nginx 反代链路均属**未验证**。本发布包是**构建物**，不等于"已在目标环境验证过的发布物"。
- **前端视觉未做人工审美评审**：本版只有一个可见变化（权限提示图标的颜色档位），
  它只经过 computed-style 与对比度实测（4.95:1 / 11.13:1），**观感需人眼确认**。

---

## 附：本版修正的文档矛盾

本轮对 `docs/`（14 份）、`README.md`、`CONTRIBUTING.md`、`contracts/CONTRACT.md` 做了一次
**系统扫描**（三个方向：状态类断言 / 数字与默认值 / 路径与命令），
每一处都**先在代码里复核过**才改。摘要：

### A. 文档说"没做"，代码其实做了（产品被写得比实际弱）

| 文档 | 原说法 | 实际 |
| --- | --- | --- |
| `docs/06` | 健康检查 URL「（当前不生效）」 | 探活已完整接通（服务 + 维护任务 + CLI 入口）；**同一份文档自己另一处写着已实现** |
| `docs/06` | 「界面上没有『搜索成员』功能，只能填数字 ID」 | 已实现（防抖搜索 + 候选下拉，`GET /api/v1/directory`）；文档引用的那句提示串全仓已不存在 |
| `docs/07` | 「没有『一键吊销某用户全部 Token』的接口」 | `POST /admin/users/{id}/revoke-sessions` 就是它，且会一并吊销 Token；前端已在用 |
| `docs/07` | `POST /admin/tools/{id}/versions`「固定抛 404」 | 已是真实实现（201，走同一个 `version_service`） |
| `docs/07` | `orphan_file_count`「恒为 0 / 从不赋值」（三处） | 已接线（复用清理任务的 dry-run 扫描） |
| `docs/08` | `verify-backup.sh --expect-files`「预留、未参与判定」 | 参与对账，不符即 FAIL |
| `docs/09` | 「PG 的 `tsvector` 仍未实现」 | M15 已实现；**同一份文档另一处写着已补上** |
| `docs/09` | 探活「未启用」「二期预留」 | 已实现并接通（只保留"详情不返回该 URL 本身"为真） |
| `docs/02` | `webapp_health_url`「二期用，本期不写入」 | 创建/编辑都会写入 |
| `docs/02` | `can_download`「预留」 | 已生效（签发下载票据与实际下载两处都强制） |
| `docs/11` | 池参数「对 SQLite 静默失效」「只对非 SQLite 设」 | 已修（现对所有非内存库都显式设置） |
| `README` | PG 迁移「后续里程碑未再重跑」 | CI 已在 `UTC` + `Asia/Shanghai` 两个时区复跑 |

### B. 文档说"可用"，其实**命令不存在**（这类最伤：照着做会失败）

| 文档 | 原说法 | 实际 |
| --- | --- | --- |
| `docs/02` | PG 迁移用 `python -m app.cli export-data` / `import-data` / `verify-integrity` | **这三个子命令都不存在**（`cli.py` 只有 15 个子命令）；`docs/09`/`docs/08` 都记着"不存在" |
| `docs/05` | `--help` 示例里列着 `export-meta` | **不存在** |

### C. 过期计数（会让人对项目规模误判）

`README` 的项目结构块全线过期：接口 75/93 → **79/99**、表 19~20 → **21**、
迁移 `0001~0004` → **`0001~0010`**、测试 449 → **666**、脚本 24 → **26**、
docs 12/13（自相矛盾）→ **14**、wheelhouse「59」→ **58 wheel + MANIFEST**、
`LOCALCRAFT_` 前缀字段 4 → **5**（新增的逃生阀）。
`docs/05` 的迁移清单写「9 支」且止于 `0009` → **10 支、head `0010`**；
`docs/09` 的「26 张表」→ **28**、「设置项 31」→ **36**（后者标注为 M14 快照）。

### D. 默认值与范围写错

`docs/05` 说 `portal.allow_anonymous_view` 默认 `false` → 真值 **`true`**（另有 5 处文档写 true）；
`docs/05` 的 `LOCALCRAFT_VERSION` 默认 `1.0.0` → **1.2.2**；
`ALLOWED_EXTENSIONS` 兜底 **44** 项（DB 设置默认 21 项，是子集）；
`docs/01` §8 白名单缺 `.jsx`/`.tsx`/`.pptx`；
`docs/03` 说 tools 的 `page_size` 默认 24 → **20**（24 是设置项与前端传值）；
`docs/01` 有 6 处范围与 `SETTING_SPECS` 不符（配额、token、锁定、保留天数）。

### E. 顺手记下的一类：**文档里数不清、只能靠人写的数字**

上面 C 类全是"数出来的事实"（路径数、表数、测试数…），它们**每加一个功能就会过期**。
本轮把它们改对了，并给最易变的两个（测试数、覆盖率）加了「截至 v1.2.2」的口径 ——
但**根治办法是把它们变成 CI 能核的东西**，这属于后续工作（`docs/11` 的优化清单）。

### F. 照文档做**会直接失败**的（本轮最该记的一类）

| 文档 | 原说法 | 实际后果 |
| --- | --- | --- |
| `docs/05` ×2 | `ALLOWED_EXTENSIONS=zip,tar.gz,...`（逗号串） | **服务起不来**（`SettingsError`）。★ 本版**改了代码**让它可用（见 §2.2），不是改文档 |
| `docs/05` ×9 | 对 wheelhouse 执行 `sha256sum -c SHA256SUMS` | 清单实际叫 **`MANIFEST.sha256`** → `No such file`。（发布包根的 `SHA256SUMS` 是对的，未动） |
| `docs/02` | PG 迁移用 `app.cli export-data` / `import-data` / `verify-integrity` | **三个命令都不存在** → 已改为 `pg_dump`/`pg_restore` + SQLite 拷文件 + 现有 `export-users`，并声明"应用层 CLI 未实现" |
| `docs/05` | `--help` 示例里列着 `export-meta` | **不存在** → 已删 |
| `docs/05` | `LOCALCRAFT_BACKUP_GZIP_DB` | 全仓**无读取点**（真名是 `LOCALCRAFT_BACKUP_GZIP`）→ 设了备份不会被 gzip |
| `docs/05` | 手工安装只装 `localcraft.service` + backup 两件 | 随包单元里有 `Slice=localcraft.slice`，不装 slice 会出问题；还漏 `maintenance` 两件与 `limits.conf` drop-in |
| `docs/05` | 给出 `localcraft-disk-alert.*` / `localcraft-gc.*` 单元全文 | 这些单元**都不存在**（`docs/09` 早已裁定删除）→ 已标注为"自建"或"已废弃" |
| `docs/08` | 「`deploy/systemd/` 里只有 `localcraft.service` 与 `localcraft.slice`」 | 实际 **6 个单元 + 1 个 drop-in**（同文件另一处自己就写着有 backup 两套） |
| `docs/08` | 附录称若干脚本「`scripts/` 下均不存在」 | 那些脚本**全部存在**（只有 `localcraft-gc.*` 那半边对） |
| `docs/08` | 说 `DB_FILE` 是"脚本自己的开关" | 脚本里是无条件赋值、**没有读取点**；真正的开关是 `LOCALCRAFT_FILES_DIR` / `LOCALCRAFT_BACKUP_DIR` |
| `docs/08` | 发布包树漏了 nginx 三个配置、`localcraft.tmpfiles`、`uninstall.sh`、`RELEASE-NOTES.md` | 已按 `docs/05 §4.1` 补齐 |
| `docs/05 §4.3` | 给了一份"生成发布包"的伪代码（`cp "${ROOT}/install.sh"` 等） | 仓库根**没有** `install.sh`，且 flat `rsync deploy/` 与真实 `make-release.sh` 的重组逻辑矛盾 → 改为指向 `scripts/make-release.sh` |

### G. 反向发现：代码在读、文档从未提

`LOCALCRAFT_DISABLE_FACET_CACHE`（门户分面 30 秒缓存的旁路开关，用于跨版本性能 A/B 与
排查计数陈旧）在 `docs/`、`README`、两个 env 样例里**一次都没出现**，
但代码在读它、两个测试在用它。已补进 `deploy/localcraft.env.example` 与 `backend/.env.example`。

> **数据来源的诚实说明**：本轮扫描的"代码真值"均来自实际命令输出或 `文件:行号` 代码；
> 覆盖率 **90%** 是独立复核过的（仍然准确，只改了测试计数）；
> 测试总数改为 **676**（`pytest --collect-only` 实测，本版树）；
> `docs/05` 里「v1.2.0 包 `SHA256SUMS` 384 条」**是对的**（v1.2.0 实测 384 条；
> v1.2.1 因新增迁移 `0010` 变为 385 条），无需修改；
> `README` 说检查脚本「36 项」也是对的（35 PASS + 0 FAIL + 1 BLOCKED）。

