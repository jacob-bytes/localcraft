# 后端开发 Agent — 任务书（M13 · 补第 6 个设置项 `portal.footer_tagline`）

## 你的身份与边界

你独占并只允许修改：`backend/`、`scripts/`、`deploy/`

**绝对不可以修改**：`docs/`、`contracts/`、`README.md`、`web/`。
**有另一个 agent 正在并行改 `web/`**（M13 前端）。

**不要执行任何 git 命令**。版本控制由监控方处理。

工作目录：`/Users/jlthzy/Documents/selftool`

---

## 这是一次小范围修正，不是新功能

M12 后端你（或同级）已交付：5 个设置项 + 迁移 `0007` + `/meta` 5 字段。
**但监控方的 §27 契约本身有缺陷**（写契约时漏看了既有的全局页脚），
导致与前端产生矛盾。用户已裁定，详见 **`contracts/CONTRACT.md` §28**。

**本轮只做三件事**，不要重做 M12 的任何一项。

---

## 第一步：先读

1. **`contracts/CONTRACT.md` §28** —— 本轮的权威依据。§28.1 讲清了缺陷由来，
   §28.3 是设置项定义，§28.5 是「并进 0007」的裁定。
2. `contracts/CONTRACT.md` §27 —— M12 的原文（作为对照，**§27.2 的「默认全空」
   与 §27.4 的「字段全空则整个页脚不渲染」已被 §28 定点修正/废止**）。

---

## 任务

### C1 · 加第 6 个设置项

按 §28.3：`portal.footer_tagline`（string，`is_public=True`，description 照抄）。

**★ 默认值是 `内网工具与 Skill 共享平台`，不是空串。** 这是对 §27.2「默认全空」的
**定点例外**：它的作用是**恢复**一个原本写死的字符串，所以默认值就是那个原串，
这样未配置的既有部署在页脚上与改动前完全一致。

### C2 · **并进迁移 `0007`，不要新建 `0008`**

§28.5 已核实：`0007` **尚未提交、未发布、没有任何数据库跑过它**
（预览库停在 `0006`，验证用的临时库已删除）。因此把这一项并进 `0007`。

- 保持 `revision = "0007"` / `down_revision = "0006"` 不变。
- upgrade / downgrade 的幂等性要求不变（逐 key 检查存在性；downgrade 能跑）。
- **仍然用 Core + `sa.JSON`，不要裸 SQL**（`system_settings.value` 是 JSON 列，
  PG 不接受 text→json）。
- 把 **downgrade → upgrade 与重复 upgrade** 重跑一遍，贴真实输出。

### C3 · `/meta` 加 `footer_tagline`

`MetaResponse` 增加 `footer_tagline: str`，走既有 `list_public` 机制。
**其余 5 个字段与既有字段一律不动。**

### C4 · 测试

- 断言 6 个新 key 都在 `SETTING_DEFAULTS` 且 `is_public=True`；其中
  **`portal.footer_tagline` 的默认值是原标语串**，另外 5 个是空串。
- 断言 `/meta` 返回 `footer_tagline`，且**全新库上等于原标语串**。
- 保留并更新 M12 已有的断言（字段集、行数等）。

---

## 通用硬约束

1. **基线：后端 533 passed（SQLite）**，不得回归。
2. **PG 也要过**，且 CI 有**时区矩阵（UTC + Asia/Shanghai）的阻塞门禁**。
   本地验证 PG 时**每次全量跑前先重建库**（脏库会让全量套件变成几百个 error，
   这个坑监控方实测过）。仓库里 `scripts/pg-dialect-suite.sh` 可用。
3. `backend/openapi.json` 重新导出并保持一致（CI 有同步守卫）。
4. **不新增接口**：仍是 **99 operations / 79 paths**。
5. ruff 全绿；Python 3.11 语法目标；不新增依赖；不新增环境变量。
6. **只改 `backend/`**。

---

## 交付要求

1. C1~C4 的**状态**与**改动文件清单**。
2. 迁移 `0007` 的 **downgrade → upgrade 与重复 upgrade 真实输出**（SQLite **与** PG）。
3. 全新库上 `/meta` 的 `footer_tagline` 实测值（应为原标语串）。
4. 两种方言的测试**命令与真实通过数**。
5. 任何未做到/未验证（尤其 openEuler 目标硬件侧）的地方，明确说出来。
