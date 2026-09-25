# 后端开发 Agent — 任务书（M6 · 收尾修正）

## 先看这一节：M5 验收结论

你的 M5 **通过验收**。448 用例全绿、覆盖率 90%、接口面 92、ruff 全过；
A1 图片签名、A2~A4 种子、B 死桩修复、C psycopg、D2 storage_warning、E 交付件全部实测确认。

完整裁定见 `contracts/CONTRACT.md` **§20**，请先读一遍。要特别说的是：

**验收 21（性能）未达成，但你的处理方式是本项目的范例。** 你做了我在 §18.3 指定的两项优化、
实测无效、**如实报告并附了成本定位实验**（成本随条目数近似线性，facets 只占约 20%），
而不是用缓存把数字做漂亮。**我据此撤销了自己在 §18.3 的优化目标判断** —— 我让你优化 facets，
方向是错的；真正的大头是每条目的序列化开销。

你附的 36 条文档不一致清单质量很高，尤其这三处：

- **`docs/05` §5.10 的 disk-alert service 缺 `EnvironmentFile`** —— 照原文部署会让外部告警命令
  **永不执行**。这是真缺陷
- **旧 `make-release.sh` 不打包 `selftool-backup.*` / `selftool-maintenance.*`**，
  而 `install.sh` 用 `[ -f ]` 静默跳过 → **按旧脚本产出的发布包装完没有定时备份，且安装当天看不出来**。
  发现并修掉这个「静默跳过导致功能缺失」很有价值
- **CSV 列那条你特别核实后确认「实现是对的」** —— 导出绝不能带明文口令。这是「实现比文档正确」的
  典型，没有盲目按文档改实现

我已建立 **`docs/09-勘误与已知限制.md`** 作为统一勘误表（效力高于被勘误的原文），
36 条逐条裁定完毕。

---

## 边界（硬性）

你仍然独占并只允许修改：`backend/`、`deploy/`、`scripts/`
继续授权：`docs/06`、`docs/07`、`docs/08`
**`RELEASE-NOTES.md`（仓库根）继续授权**（M5 已按任务书创建，本轮只需增补）

`docs/01`~`docs/05`、**`docs/09`**、`contracts/`、`README.md`、`web/` **只读**

不执行任何 git 命令，**包括 `git status`**。委派子 agent 时在提示里显式写明这一条，
并对其产出做一次越界复核。

工作目录：`/Users/jlthzy/Documents/selftool`

---

## M6 目标

**只有 6 项修正，全部是监控方实测确认的真实缺陷。不新增功能，不做优化。**

一句话验收：一个普通用户能在界面上搜索到同事并授权 `restricted` 可见性；
一个被「强制下线」的用户，其 API Token 也立即失效；ACL 的「取消允许下载」真的能挡住下载。

**这是最后一个后端里程碑。** 做完之后后端进入冻结状态。

---

## 交付清单（6 项，逐项都小）

### J-1. 新增 `GET /api/v1/directory` —— 接口面 92 → **93**

**这是对 92 冻结的刻意例外，理由见 §20.4③。**

**问题**：`docs/01` FR-ACL-02（P0）与 `docs/04` §6.7 要求「搜索用户/组后添加」到 ACL，
但普通用户**没有任何可用的搜索接口** —— `GET /admin/users` 与 `GET /admin/groups` 都要求超管。
结果是设置 `restricted` 可见性必须**手填数字 ID**，功能实际不可用。

**实现**：

- 路径：`GET /api/v1/directory`
- 鉴权：**任何已登录用户**（不是超管）
- 参数：`?q=`（模糊匹配，建议对 `username`/`display_name`/组名做 `LIKE`）、
  `?type=user|group`（可选，省略则两者都返回）、`?limit=`（默认 20，上限 50）
- **最小披露原则**（重要）：
  - 用户只返回 `{id, username, display_name}`
  - 用户组只返回 `{id, name, member_count}`
  - **不返回**邮箱、状态、角色、最后登录时间、创建时间
- 返回形状：
  ```json
  {
    "users":  [{"id": 4, "username": "zhangsan", "display_name": "张三"}],
    "groups": [{"id": 1, "name": "运维组", "member_count": 12}]
  }
  ```
- `q` 为空时返回前 N 条（供「点击下拉即看到常用人」的交互）
- **注意**：这是 93 个操作了。守卫测试的冻结清单要同步更新为 93，
  并在断言里写明「93 = 92 + directory，见 CONTRACT §20.4③」，
  避免以后有人看到 93 以为是越界

**测试**：普通用户（非超管）能搜到；超管同样能搜到；`type` 过滤生效；
返回体**不含**邮箱/状态/角色（用一个断言钉死，防止将来有人「顺手」加字段）。

### J-2. 让 ACL 的 `can_download` 真正生效

**问题**（监控方核实）：`tool_acl.can_download` 前后端都存、`docs/03` §3.11 描述为生效字段，
但**没有任何读取点** —— `deps.py` 里的 `can_download` 是 `Principal` 的角色派生属性，
与 ACL 无关。「取消允许下载」静默无效。

**裁定：实现它**，理由与 §18.4 修死桩路由相同 —— 被文档描述、被 API 接受、
却毫无作用的字段比没有它更糟。

**实现**：

- 下载授权在**通过可见性检查之后**追加判断：若**命中的那条 ACL 条目** `can_download=false` → 拒绝
- 拒绝的错误码：新增 `403 DOWNLOAD_NOT_ALLOWED`（**不新增接口，只新增错误码**），
  或复用现有语义相近的码 —— 你判断哪个更合适，在报告里说明选择
- `owner` 与 `superadmin` **不受此限**
- 语义：可见（能看详情、能在门户看到）但不可下载。用例：「让人知道有这个工具，
  但下载需另行申请」
- 默认值 `true`，**对既有数据无行为变化**

**测试**：ACL 条目 `can_download=false` 的用户能看详情但下载被拒；
同组其他条目 `can_download=true` 的用户能下载；owner 与超管不受限；
**未命中任何 ACL 条目但通过 `public` 可见的用户不受影响**（别把 public 也挡了）。

### J-3. `revoke-sessions` 一并吊销该用户的 API Token

**问题**（监控方实测确认）：

```
建 Token 后可用: 200
revoke-sessions → HTTP 200
revoke 后同一 Token: 仍可用!     ← 缺陷
```

管理员「把这个人踢出去」之后，该用户签发的 API Token 仍然有效。

**实现**：扩展 `POST /api/v1/admin/users/{id}/revoke-sessions` 的行为，
一并吊销该用户签发的全部 API Token。**复用已有的
`admin_user_service.revoke_user_tokens`，不新增接口。**

> SRS FR-IAM-05 在「禁用用户」时已要求两者同时失效，此处只是把同一语义补到
> 「强制下线」这个更轻的动作上。`docs/05` §13.8 的表述由此对**管理界面**成立。

**测试**：revoke 后同一 Token 返回 401；revoke 后 refresh 也失效；
**其他用户**的 Token 不受影响（别误伤）。

### J-4. `orphan_file_count` 接线

`StorageStatsResponse.orphan_file_count` 已声明，但 `stats_service` 从不设置它，恒为 0。
孤儿文件清理任务已能统计数量，**把它接进 `GET /admin/stats/storage`**。

（备选是删字段；选接线 —— 管理员确实需要知道有无孤儿文件。）

**测试**：手工放一个无引用文件 → 接口返回的 `orphan_file_count` 为 1。

### J-5. 删除最后一个待审版本时，工具状态要回落

**问题**：`pending_update` 状态下删除待审版本后，工具仍停在 `pending_update`，
而 `pending_version_id` 已为空 —— 状态与数据不一致。

**实现**：删除版本后，若工具不再有待审版本：

- 曾发布过（`published_at` 非空）→ 状态回落为 `approved`
- 从未发布过 → 回落为 `draft`

**测试**：`pending_update` 删掉待审版本 → 工具变 `approved` 且门户仍可见；
`pending` 状态下删掉唯一版本 → 工具变 `draft`。

### J-6. 两处注释/docstring 同步（各 1 行）

- ~~`app/services/tool_service.py` 的 facets 注释~~ —— **监控方在实施 O3 时已顺手修正**，本项无需再做
- `app/cli.py` 的 `gc-versions` docstring 仍需改（引用已删除的 `selftool-gc.*`）
- `app/cli.py` 的 `gc-versions` docstring：仍在引用 `selftool-gc.*`
  （该 unit 已判定为与 `selftool-maintenance.*` 重复而删除）。改为引用
  `selftool-maintenance.timer`

### J-7. `GET /api/v1/admin/groups` 的删除影响面补 `slug`（M5 漏做）

**背景**：这条本应在 M5 做（`contracts/CONTRACT.md` §19.5），但**监控方是在 M5 agent 开工后才
补写进任务书的，因此 M5 从未看到它 —— 责任在监控方，不在实现方。**

**现状**：`app/services/group_service.py` 的 `GroupInUseError.details.tools` 仍是
`[{"id": tid, "name": name}]`，没有 `slug`。

**要**：增加 `slug`（附加字段，非破坏性）。`docs/04` §6.14 要求「影响面工具可点击跳转」，
前端已写成「有 `slug` 才渲染 `Link`，否则纯文本」，后端补上即自动生效。

**测试**：`GROUP_IN_USE` 的 `details.tools[].slug` 非空，且该 slug 能用于 `GET /tools/{slug}`。

### J-8. 补齐无字段定义的端点 + `openapi.json` 陈旧（M5 漏做 + 真实缺陷）

**两个问题叠加**：

**问题 1（J2 未交付）**：这条本应在 M5 做（§19.7），同样是**监控方补写太晚**，
M5 从未看到。实测仍有以下端点的响应是 `additionalProperties: true`（无字段定义）：

```
GET    /api/v1/admin/groups
GET    /api/v1/admin/groups/{group_id}/members
DELETE /api/v1/admin/groups/{group_id}
DELETE /api/v1/admin/groups/{group_id}/members/{user_id}
GET    /api/v1/admin/tags
GET    /api/v1/admin/tokens
DELETE /api/v1/admin/tokens/{token_id}
DELETE /api/v1/admin/categories/{category_id}
POST   /api/v1/admin/users/{user_id}/revoke-sessions
POST   /api/v1/admin/tools/{tool_id}/versions
```

**要**：为它们补显式的 Pydantic 响应模型。

**问题 2（`backend/openapi.json` 陈旧 —— 这是新发现的真实缺陷）**：

磁盘上的 `backend/openapi.json`（mtime 14:14、md5 `210fc35f…`）**缺**
`AdminOverviewResponse.storage_warning`，也**缺** `POST /admin/tools/{id}/versions` 的 201 schema；
而运行时从 `:8000/openapi.json` 取到的**两者都有**。

`openapi.json` 是 §15.6 宣布的**形状权威**，也是前端 `check:api-types` 的输入。
它陈旧意味着：

- 前端只能对着**实时 spec** 改代码，而 `npm run check:api-types` 却永远红
- 「openapi 是权威」这句话对它自己不自洽

**要**：

1. 补齐上面的响应模型后，**重新执行 `export-openapi`** 并确认磁盘文件与运行时一致
2. **加一个守卫测试**：`test_openapi_artifact_is_current` ——
   在内存里重新生成 spec，与已提交的 `backend/openapi.json` 比对；
   不一致即失败，并提示「请运行 `export-openapi` 并提交产物」。
   **这从结构上杜绝了这类陈旧**，而不是靠人记得导出
3. 在 `RELEASE-NOTES.md` 或构建流程中说明：**改动任何路由/响应模型后必须重导出 openapi.json**

**注意**：`openapi.json` 是**生成产物但必须入库**（它是形状权威、且前端要消费）。
守卫测试是保证它不陈旧的手段。

### J-9. `current_version.can_download` 与顶层 `can_download` 不一致

**监控方实测确认**（viewer 访问 `file` 类型工具详情）：

```
顶层 can_download            = False     ← 正确
current_version.can_download = True      ← 错误
```

而票据接口正确拒绝（`NOT_FOUND`，`details.reason = no_download_permission`）。

**后果**：前端读的是 `current_version.can_download`，据此把下载按钮渲染为**可用**，
点下去才 404 —— **广告了「可下载」，实际不能**。这与 §17.9 修掉的种子问题同一性质。

**要**：`current_version.can_download` **必须由同一套授权判定派生**，
不能硬编码 `true`。它应当与顶层 `can_download` 恒等（除非将来版本级授权有独立语义，
那就需要显式设计并改文档）。

**测试**：对 `viewer` / `user` / `owner` / `superadmin` 四种身份，
断言 `current_version.can_download == 顶层 can_download`。

### J-10. 上传扩展名默认白名单与文档不符（监控方实测发现）

**实测**：`app/core/config.py` 的 `allowed_extensions` 默认只有 **21 项**：

```
zip tar.gz tgz whl tar gz 7z rar exe msi deb rpm sh py md txt json yaml pdf png jpg
```

而 `docs/01` §8 的清单还包括 `.jpeg .webp .gif .svg .csv .xlsx .docx .js .ts .go .java .sql .bat .ps1 .jar .war .bin .iso .img` 等。

**后果**：内网用户上传 `.xlsx`（很常见）或 `.csv` 会被 `415 UNSUPPORTED_MEDIA_TYPE` 拒绝，
而文档说这是允许的。**这是实际可用性问题，不是纯文档问题。**

**裁定：扩充默认白名单以匹配 `docs/01` §8 的意图**，补入：

```
jpeg webp gif svg csv xlsx docx pptx
js ts jsx tsx go java sql
bat ps1 jar war bin iso img
```

**新增 J-10。** 理由：平台**从不执行**上传的文件，只以
`Content-Disposition: attachment` 提供下载，因此放宽类型白名单的风险很低；
而「传 Excel 被拒」会直接产生支持工单。

（`docs/09` 勘误里我原先裁定「以实现的 21 项为准」，**现改为以 `docs/01` §8 的清单为准** ——
那条勘误需同步修正，由监控方处理。）

**测试**：断言 `docs/01` §8 列出的每个扩展名都在默认白名单内（用一条测试钉死两者一致，
防止再次漂移）。

### J-11. 种子缺少 approver 账号，与 mock 不一致（监控方预览时发现）

**实测**：`seed-demo` 的 6 个账号角色是 `admin=superadmin`、`newbie/viewer/zhangsan/lisi/wangwu`，
其中 **`wangwu` 是 `user`**。

但 `contracts/CONTRACT.md` §14.8 明确要求「把同名的 3 个账号补进后端 `seed-demo`，
使 mock 与真实环境一致」，而**前端 mock 里的 `wangwu` 是 `approver` + `user`**
（§17.7 接受该设定，理由：需要 approver 视角的测试）。

**后果**：

- 真实环境下**没有可登录的审批员账号**，`approver` 的角色菜单渲染与权限边界无法验证
  （用 `admin` 能看到全部页面，但看不到「approver 不该看到某些菜单」这一行为）
- mock 与真实种子不一致 —— 属 §17.9 那类「倒挂」，会掩盖差异

**裁定：给 `wangwu` 加上 `approver` 角色**（保留 `user`），与 mock 对齐。
这是 1 行 seed 改动。

**测试**：断言 `seed-demo` 后存在至少一个 `approver` 角色的账号，
且该账号登录后能访问 `/admin/approvals`、不能访问 `/admin/users`。

---

## M6 验收清单（监控方会逐条核验）

| # | 场景 | 期望 |
| --- | --- | --- |
| 1 | 接口面 | **恰为 93**（92 + directory），且守卫清单注明了这个加法 |
| 2 | 普通用户调 `GET /directory?q=` | 200，能搜到用户与组 |
| 3 | `/directory` 的返回体 | **不含**邮箱/状态/角色/最后登录时间 |
| 4 | `?type=user` / `?type=group` | 过滤生效 |
| 5 | ACL 条目 `can_download=false` | 能看详情，**下载被拒** |
| 6 | 同组另一条 `can_download=true` | 能下载 |
| 7 | `public` 可见且未命中 ACL | 能下载（不受影响） |
| 8 | owner / superadmin | 不受 `can_download=false` 限制 |
| 9 | `revoke-sessions` 后同一 API Token | **401** |
| 10 | `revoke-sessions` 后其他用户的 Token | 不受影响 |
| 11 | `orphan_file_count` | 有孤儿文件时不为 0 |
| 12 | `pending_update` 删待审版本 | 工具回落 `approved`，门户仍可见 |
| 13 | `pending` 删唯一版本 | 工具回落 `draft` |
| 14 | 全量回归 | ≥ 448 用例全绿，覆盖率 ≥ 90%，ruff 全过 |
| 15 | `docs/06`/`07`/`08` | 若 §20.3 的裁定影响到手册内容（尤其 §13.8 的吊销表述），同步修订 |
| 16 | `GROUP_IN_USE` 的 `details.tools[].slug` | 非空且可用于 `GET /tools/{slug}` |
| 17 | 上述 10 个端点的 openapi 响应 | 均有显式 `properties`，不再有 `additionalProperties: true` |
| 18 | `backend/openapi.json` 与运行时一致 | 无差异；守卫测试 `test_openapi_artifact_is_current` 存在且通过 |
| 19 | viewer 看 `file` 详情 | `current_version.can_download == 顶层 can_download == False` |
| 20 | 四种身份的 `can_download` 一致性 | 每个身份都满足 `current_version.can_download == 顶层` |
| 21 | 扩展名默认白名单 | 覆盖 `docs/01` §8 列出的全部类型（含 `.xlsx`/`.csv`/`.jpeg`） |
| 22 | 种子含 `approver` 账号 | 该账号能访问 `/admin/approvals`、不能访问 `/admin/users` |

---

## 报告要求

按 `contracts/CONTRACT.md` §10 格式回报。额外要求：

- 验收 **2、3、5、9、11、12** 必须贴原始命令与输出（`3` 要贴完整响应体）
- **J-2 的错误码选择**要说明理由（新增 `DOWNLOAD_NOT_ALLOWED` 还是复用现有码）
- 如果 J-5 的状态回落与你已有的状态机实现有冲突（例如别处依赖「有待审版本 ⇒ pending_update」），
  **报告冲突**，不要悄悄改语义
- 任何新增的测试文件、任何你发现但未修的问题，都列出

---

## 易错点清单

1. **`/directory` 的最小披露要测试钉死** —— 否则以后有人「顺手」把邮箱加进去
2. **J-2 别把 `public` 可见的用户也挡了** —— 他们根本没命中 ACL 条目
3. **J-2 要判断「命中的那条」条目**，一个用户可能同时命中多条（自己 + 所属组），
   语义上应当是「任一命中且 `can_download=true` 即可下载」
4. **J-3 只吊销目标用户的 Token**，不要误伤
5. **J-5 的回落要考虑 `published_at`**，没发布过的工具不该变成 `approved`
6. **守卫清单更新到 93 时要在注释里写明原因**，否则看着像越界
7. **不要顺手做 §20.3 之外的重构** —— 这是收尾，不是改造

---

## 不要做的事

- **不新增除 `/directory` 之外的任何接口**（§20.4③ 是唯一获批的例外）
- 不做性能优化（§20.2 已裁定方向，留下次）
- 不改 `docs/01`~`docs/05` 与 `docs/09`、`contracts/`，发现问题报告给我
- 不写前端代码，不改 `web/`
- 不引入新依赖
- 不执行任何 git 命令，含 `git status`
- 不把「检查通过」说成「验证通过」

有阻塞或需要裁决时，立刻按 §10 格式报告，不要自己猜着往下做。
