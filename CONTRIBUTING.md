# 参与贡献

感谢你愿意花时间。这个项目面向内网自托管场景，最看重的是**可靠**和**可维护**，其次才是功能数量 —— 一个没人敢升级的工具平台，功能再多也是负债。

## 先开 issue

改动之前建议先开一个 issue 说明场景：你想解决什么问题、现在的行为是什么、期望是什么。特别是以下几类，请务必先讨论，避免做完发现方向不对：

- 新增接口或修改已有接口的**响应结构**
- 修改数据库表结构
- 引入新的运行时依赖（离线部署对依赖体积敏感）
- 改变权限 / 可见性语义

## 开发环境

```bash
bash preview.sh          # 一条命令搞定 venv、依赖、建库、播种、构建、起服务
```

演示账号见 README。预览数据落在 `.preview/`，`bash preview.sh --reset` 可随时重来。

## 提交前必须通过

**后端**（在 `backend/` 下）：

```bash
.venv/bin/python -m ruff check .      # 必须 clean
.venv/bin/python -m pytest            # 449 个测试必须全绿
```

**前端**（在 `web/` 下）：

```bash
npm run verify
```

`npm run verify` 会依次跑：TypeScript 类型检查 → oxlint → UI 设计原语约束 → **API 类型与 `backend/openapi.json` 一致性** → 生产构建 → 构建产物纯净性检查。

改了接口的话，先重新导出 OpenAPI 快照，否则 `check:api-types` 会拦下你：

```bash
cd backend && .venv/bin/python -m app.cli export-openapi
```

## 几条硬约束

这些不是风格偏好，是这个项目的技术前提，改动前请先理解原因：

### 1. Python 语法必须停在 3.11

本机可以用更新的 Python 开发和调试，但**代码不能使用 3.12+ 的语法**（PEP 695 泛型、`type` 别名语句、f-string 内层同类引号等）。目标环境 openEuler 自带的是 Python 3.11，这类问题发生在**解析阶段**，连 `try/except` 都兜不住，服务会直接起不来。

`ruff` 的 `target-version = "py311"` 是自动防线，不要改它。

### 2. 不允许引入容器

生产环境是 systemd 原生部署，**禁止 Docker / Podman / 任何容器化方案**。请不要提交 Dockerfile、docker-compose、或依赖容器才能跑的脚本。

### 3. 写入路径必须短事务

SQLite 在 WAL 下仍是单写者。**禁止在数据库事务内做文件 IO、解压、哈希计算、HTTP 探测**。上传流程的正确顺序是：先落盘 → 算哈希 → 再开事务写元数据。

同理，计数类字段（下载量、浏览量）必须走内存聚合 + 定时批量落库，**不要**写 `UPDATE ... SET count = count + 1`。

### 4. 单 worker 是刻意设计

不要为了"提升吞吐"去加 uvicorn worker。SQLite 单写者模型下，加 worker 只会加剧锁竞争而不会提升写吞吐。真要扩容，先切 PostgreSQL。

### 5. 接口形状的权威是 `backend/openapi.json`

它被纳入版本控制，且有测试守卫其时效性。前端类型从这个文件生成 —— 手写一份"差不多的"类型会被 `check:api-types` 拦下。

### 6. 图片与下载的鉴权方式不要"简化"

- 图片 URL 是**签名 capability URL**（`?sig=` HMAC，7 天 TTL）—— 因为 `<img>` 标签无法携带 `Authorization` 头
- 下载走 **60 秒一次性 ticket** 并绑定用户

这两处看起来啰嗦，但换成"公开 URL"或"长效 token 放 query"都会造成越权访问。

## 代码风格

- 注释与文档用**中文**，与现有代码保持一致
- 注释解释**为什么**，而不是复述代码在做什么
- 提交信息用中文，简明描述改动意图

## 提交 PR

1. Fork 并基于 `main` 开分支
2. 改动带上对应测试 —— 修 bug 附回归测试，加功能附覆盖测试
3. 跑通上面「提交前必须通过」的全部命令
4. PR 描述里说明：改了什么、为什么这么改、怎么验证的

CI 会在 ubuntu 上跑 ruff + pytest + 前端静态检查。CI 绿了不代表完事 —— 涉及部署脚本、systemd、双架构 wheel 的改动，CI 覆盖不到，请在 PR 里说明你的验证方式。

## 报告安全问题

请不要通过公开 issue 报告安全漏洞。改用 GitHub 的 [Security Advisory](https://github.com/jacob-bytes/localcraft/security/advisories/new) 私下提交，注明复现步骤与影响范围。

## 许可证

贡献的代码将以 [MIT](LICENSE) 许可证发布。
