import type {
  Category,
  Role,
  SubmissionType,
  Tag,
  ToolImage,
  ToolListItem,
  ToolStatus,
  ToolType,
  VersionStatus,
  Visibility,
} from "@/api/types";

/**
 * MSW seed data — the M1 CONTRACT §7 seed grown to the CONTRACT §14.6 shape:
 *
 *  - 5 users: 1 superadmin `admin` / `Admin@12345`, 1 forced-password-change user
 *    `newbie` / `Newbie@12345`, and the 3 demo authors `zhangsan` / `lisi` /
 *    `wangwu` (`Author@12345`). The authors are contract §14.8 accounts, and the
 *    backend `seed-demo` creates the same 5.
 *  - 4 categories with the backend's slugs (`dev-tools` / `ops-tools` /
 *    `skills` / `prompts` — `backend/app/cli.py` DEMO_CATEGORIES).
 *  - **26 tools** (CONTRACT §14.6): the original 8 hand-authored boundary tools
 *    (same slugs / names / cover split / >40-char name / >3-line summary) plus 18
 *    deterministic generated ones, so `page_size` 12/24/48 gives 3/2/1 pages.
 *    All 26 are `approved` + `public`, cover all 4 types and all 4 categories.
 *  - every tool has >= 1 version; `admin` additionally owns one tool in each
 *    state (`draft` / `pending` / `approved` / `rejected` / `pending_update` /
 *    `offline`) so 我的工具 tabs and the rejected-alert scenario are demoable.
 *  - the approval queue gets 4 `new_tool` + 3 `new_version` submissions with
 *    fixed `submitted_at` values => waiting times of exactly 3.4 / 26 / 80 / 2 /
 *    8 / 50 / 1 hours (see MOCK_WAITING_HOURS).
 *
 * Determinism: everything is derived from fixed seeds — `mulberry32` with a
 * constant seed for the generated numbers, and a SHA-256 helper with a constant
 * prefix for the fake file hashes. No `Math.random()` anywhere.
 *
 * This module is seed data only; the stateful behaviour lives in `handlers.ts`.
 */

/* -------------------------------------------------------------------------- */
/* Deterministic pseudo-randomness                                            */
/* -------------------------------------------------------------------------- */

/**
 * `mulberry32` — tiny deterministic PRNG (same algorithm family as the backend's
 * fixed-seed `random.Random`). Only used at module scope with a constant seed, so
 * repeated runs and reloads produce identical data.
 */
export function mulberry32(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Deterministic 64-char hex digest (fake SHA-256 for mock uploads/files). */
export function fakeSha256(seed: string): string {
  const chunks: string[] = [];
  for (const salt of ["a", "b", "c", "d"]) {
    const rng = mulberry32(hashString(`${seed}|${salt}`));
    let chunk = "";
    while (chunk.length < 16) chunk += Math.floor(rng() * 16 ** 8).toString(16).padStart(8, "0");
    chunks.push(chunk.slice(0, 16));
  }
  return chunks.join("");
}

function hashString(value: string): number {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

/* -------------------------------------------------------------------------- */
/* Users                                                                      */
/* -------------------------------------------------------------------------- */

export interface MockUser {
  id: number;
  username: string;
  password: string;
  display_name: string;
  email: string | null;
  roles: Role[];
  permissions: string[];
  must_change_password: boolean;
  auth_source: "local";
  status: "active" | "disabled";
  last_login_at: string | null;
  /** M3 管理侧字段（`AdminUserItem` 需要，登录时会更新）。 */
  failed_login_count: number;
  locked_until: string | null;
  last_login_ip: string | null;
  created_at: string;
  updated_at: string;
}

const USER_PERMISSIONS: Record<Role, string[]> = {
  viewer: ["tool:read"],
  user: ["tool:read", "tool:write:own", "download"],
  approver: ["tool:read", "tool:write:own", "download", "approval:review", "taxonomy:write"],
  superadmin: [
    "tool:read",
    "tool:write:own",
    "download",
    "approval:review",
    "taxonomy:write",
    "user:manage",
    "settings:write",
    "admin:all",
  ],
};

function permissionsFor(roles: Role[]): string[] {
  const set = new Set<string>();
  for (const role of roles) {
    for (const permission of USER_PERMISSIONS[role]) set.add(permission);
  }
  return [...set];
}

function makeUser(
  id: number,
  username: string,
  password: string,
  display_name: string,
  roles: Role[],
  must_change_password = false,
  last_login_at: string | null = null,
): MockUser {
  return {
    id,
    username,
    password,
    display_name,
    email: `${username}@example.com`,
    roles,
    permissions: permissionsFor(roles),
    must_change_password,
    auth_source: "local",
    status: "active",
    last_login_at,
    failed_login_count: 0,
    locked_until: null,
    last_login_ip: last_login_at ? "10.20.30.40" : null,
    created_at: "2024-11-01T02:00:00Z",
    updated_at: "2025-03-01T02:00:00Z",
  };
}

export const MOCK_USERS: MockUser[] = [
  makeUser(1, "admin", "Admin@12345", "管理员", ["superadmin"], false, "2025-03-14T01:05:00Z"),
  makeUser(2, "newbie", "Newbie@12345", "新人小张", ["user"], true, null),
  makeUser(3, "zhangsan", "Author@12345", "张三", ["user"], false, "2025-03-13T09:12:00Z"),
  makeUser(4, "lisi", "Author@12345", "李四", ["user"], false, "2025-03-12T07:40:00Z"),
  // wangwu 兼具 approver：approver 视角（菜单裁剪、403 语义）需要一个非超管账号
  makeUser(5, "wangwu", "Author@12345", "王五", ["approver", "user"], false, "2025-03-11T03:20:00Z"),
  // 验收 #9 需要只读访客：viewer 能看详情但 can_download 恒为 false（docs/01 §3.2）。
  makeUser(6, "viewer", "Viewer@12345", "只读访客", ["viewer"], false, null),
];

/**
 * Demo groups. The backend M2 seed has no groups yet, but the ACL editor offers
 * `subject_type: "group"`, so the mock accepts these and rejects anything else
 * with `400 SUBJECT_NOT_FOUND` (docs/03 §3.11).
 *
 * M3 grows them into full records (description / members / timestamps) so the
 * admin group console is stateful. 运维值班组 is deliberately referenced by tool
 * 108's ACL, which is what makes `DELETE /admin/groups/{id}` return
 * `409 GROUP_IN_USE` (FR-GRP-04).
 */
export interface MockGroupMember {
  user_id: number;
  added_at: string;
  added_by_id: number | null;
}

export interface MockGroup {
  id: number;
  name: string;
  description: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  members: MockGroupMember[];
}

export const MOCK_GROUPS: MockGroup[] = [
  {
    id: 1,
    name: "研发一组",
    description: "研发线核心工具维护者。",
    is_active: true,
    created_at: "2025-01-10T02:00:00Z",
    updated_at: "2025-02-20T02:00:00Z",
    members: [
      { user_id: 3, added_at: "2025-01-11T02:00:00Z", added_by_id: 1 },
      { user_id: 4, added_at: "2025-01-12T03:00:00Z", added_by_id: 1 },
    ],
  },
  {
    id: 2,
    name: "运维值班组",
    description: "负责线上巡检与应急响应的值班同学。",
    is_active: true,
    created_at: "2025-01-15T02:00:00Z",
    updated_at: "2025-02-25T02:00:00Z",
    members: [{ user_id: 5, added_at: "2025-01-16T02:00:00Z", added_by_id: 1 }],
  },
];

/* -------------------------------------------------------------------------- */
/* Roles (M3 `GET /admin/roles`)                                              */
/* -------------------------------------------------------------------------- */

export interface MockRoleRecord {
  id: number;
  code: Role;
  name: string;
  description: string;
  /** 权限点来自 `app/core/permissions.py` 的 frozenset，不是数据库冗余列。 */
  permissions: string[];
}

/** 四个内置角色，顺序与 `migrations/versions/0002_seed_roles_and_settings.py` 一致。 */
export const MOCK_ROLES: MockRoleRecord[] = [
  {
    id: 1,
    code: "superadmin",
    name: "超级管理员",
    description: "平台最高权限，管理用户、角色、系统设置、API Token",
    permissions: [
      "admin:all",
      "approvals:write",
      "download",
      "groups:write",
      "settings:write",
      "taxonomy:write",
      "tools:read",
      "tools:write",
      "users:write",
    ],
  },
  {
    id: 2,
    code: "approver",
    name: "审批管理员",
    description: "审批工具上架、下架工具、管理分类标签、查看审批历史",
    permissions: ["approvals:write", "download", "taxonomy:write", "tools:read", "tools:write"],
  },
  {
    id: 3,
    code: "user",
    name: "普通用户",
    description: "浏览、下载、上传自己的工具",
    permissions: ["download", "tools:read", "tools:write"],
  },
  {
    id: 4,
    code: "viewer",
    name: "只读访客",
    description: "只能浏览 public 工具，不能下载受限内容、不能上传",
    permissions: ["tools:read"],
  },
];

export function findMockUserById(id: number): MockUser | undefined {
  return MOCK_USERS.find((user) => user.id === id);
}

/* -------------------------------------------------------------------------- */
/* Taxonomy                                                                   */
/* -------------------------------------------------------------------------- */

export interface MockCategoryRecord extends Category {
  created_at: string;
  updated_at: string;
}

/** 4 categories with the backend's slugs (`backend/app/cli.py` DEMO_CATEGORIES). */
export const MOCK_CATEGORIES: MockCategoryRecord[] = [
  {
    id: 1,
    slug: "dev-tools",
    name: "研发工具",
    description: "面向研发日常的工具与脚手架",
    icon: "wrench",
    sort_order: 10,
    is_active: true,
    tool_count: 0,
    created_at: "2024-10-01T02:00:00Z",
    updated_at: "2025-02-10T02:00:00Z",
  },
  {
    id: 2,
    slug: "ops-tools",
    name: "运维工具",
    description: "部署、巡检、备份相关工具",
    icon: "server",
    sort_order: 20,
    is_active: true,
    tool_count: 0,
    created_at: "2024-10-01T02:00:00Z",
    updated_at: "2025-02-10T02:00:00Z",
  },
  {
    id: 3,
    slug: "skills",
    name: "Skill",
    description: "可被 AI 助手调用的 Skill 包",
    icon: "puzzle",
    sort_order: 30,
    is_active: true,
    tool_count: 0,
    created_at: "2024-10-01T02:00:00Z",
    updated_at: "2025-02-10T02:00:00Z",
  },
  {
    id: 4,
    slug: "prompts",
    name: "提示词",
    description: "可复用的 Prompt 模板",
    icon: "message-square",
    sort_order: 40,
    is_active: true,
    tool_count: 0,
    created_at: "2024-10-01T02:00:00Z",
    updated_at: "2025-02-10T02:00:00Z",
  },
];

export interface MockTagRecord extends Tag {
  /** 种子标签允许缺省；投影时回退到平台初始化时间。 */
  created_at?: string;
}

/**
 * Tag registry. `usage_count` is **recomputed from the tool records** in
 * `handlers.ts` (the backend does the same full recount, docs/02 §3.7), so the
 * numbers written here are only the anonymous-seed view.
 *
 * The last two entries deliberately have zero references so
 * `POST /admin/tags/cleanup` has something to delete.
 */
export const MOCK_TAGS: MockTagRecord[] = [
  { id: 1, name: "python", display_name: "python", usage_count: 5 },
  { id: 2, name: "log", display_name: "log", usage_count: 4 },
  { id: 3, name: "ops", display_name: "ops", usage_count: 6 },
  { id: 4, name: "review", display_name: "review", usage_count: 3 },
  { id: 5, name: "shell", display_name: "shell", usage_count: 3 },
  { id: 6, name: "k8s", display_name: "k8s", usage_count: 4 },
  { id: 7, name: "deploy", display_name: "deploy", usage_count: 4 },
  { id: 8, name: "sql", display_name: "sql", usage_count: 2 },
  { id: 9, name: "prompt", display_name: "prompt", usage_count: 4 },
  { id: 10, name: "architecture", display_name: "architecture", usage_count: 1 },
  { id: 11, name: "fastapi", display_name: "fastapi", usage_count: 2 },
  { id: 12, name: "internal", display_name: "internal", usage_count: 1 },
  { id: 13, name: "node", display_name: "node", usage_count: 2 },
  { id: 14, name: "test", display_name: "test", usage_count: 3 },
  { id: 15, name: "openapi", display_name: "openapi", usage_count: 2 },
  { id: 16, name: "backup", display_name: "backup", usage_count: 2 },
  { id: 17, name: "database", display_name: "database", usage_count: 2 },
  { id: 18, name: "monitor", display_name: "monitor", usage_count: 3 },
  { id: 19, name: "ai", display_name: "ai", usage_count: 6 },
  { id: 20, name: "skill", display_name: "skill", usage_count: 5 },
  { id: 21, name: "pdf", display_name: "pdf", usage_count: 1 },
  { id: 22, name: "frontend", display_name: "frontend", usage_count: 2 },
  { id: 23, name: "security", display_name: "security", usage_count: 3 },
  { id: 24, name: "observability", display_name: "observability", usage_count: 3 },
  { id: 25, name: "docker", display_name: "docker", usage_count: 3 },
  { id: 26, name: "release", display_name: "release", usage_count: 2 },
  { id: 27, name: "performance", display_name: "performance", usage_count: 2 },
  { id: 28, name: "cache", display_name: "cache", usage_count: 2 },
  { id: 29, name: "sre", display_name: "sre", usage_count: 3 },
  { id: 30, name: "oncall", display_name: "oncall", usage_count: 2 },
  { id: 31, name: "writing", display_name: "writing", usage_count: 2 },
  { id: 32, name: "testcase", display_name: "testcase", usage_count: 2 },
  { id: 33, name: "refactor", display_name: "refactor", usage_count: 2 },
  { id: 34, name: "compliance", display_name: "compliance", usage_count: 1 },
  // 零引用标签：`POST /admin/tags/cleanup` 的演示对象。
  { id: 35, name: "legacy-spike", display_name: "legacy-spike", usage_count: 0 },
  { id: 36, name: "已下线", display_name: "已下线", usage_count: 0 },
];

/* -------------------------------------------------------------------------- */
/* Tool seeds                                                                 */
/* -------------------------------------------------------------------------- */

export interface ToolSeed {
  id: number;
  slug: string;
  name: string;
  summary: string;
  description_md: string;
  tool_type: ToolType;
  visibility: Visibility;
  category_slug: string;
  tags: string[];
  cover: boolean;
  owner_username: string;
  current_version: string;
  file_size: number | null;
  download_count: number;
  view_count: number;
  published_at: string;
  updated_at: string;
  webapp_url?: string | null;
}

/** Name is deliberately > 40 characters (CONTRACT §7 truncation check). */
export const LONG_TOOL_NAME =
  "统一日志采集与多集群灰度发布自动回滚一体化运维编排工具集（内部试用版，含完整使用手册与故障排查指南）";

/** Summary is deliberately long enough to exceed 3 rendered lines. */
export const LONG_TOOL_SUMMARY =
  "把日志采集、多集群灰度发布、健康检查与自动回滚串成一条可复用的流水线：先在预发集群按 1% 流量验证，确认指标无异常后逐级放大到 100%，任何一步失败都会自动回滚到上一个稳定版本，并把全过程的关键指标、变更记录与责任人写进审计日志，便于事后复盘与责任追溯。";

/**
 * The original 8 hand-authored M1 boundary tools. Their slugs, names, cover
 * split and metrics are frozen: `e2e/m1-acceptance.spec.ts` pins
 * `log-analyzer-a3f2` (cover), `code-review-skill` (no cover),
 * `gray-release-orchestrator` (long name + long summary) and the `Nginx` search.
 * The only change is the category slugs (`skill`/`prompt` → `skills`/`prompts`)
 * to match `backend/app/cli.py`.
 */
export const BOUNDARY_TOOL_SEEDS: ToolSeed[] = [
  {
    id: 1,
    slug: "log-analyzer-a3f2",
    name: "日志分析器",
    summary: "一键分析 Nginx 与 Tomcat 日志，输出 Top 错误与耗时分布。",
    description_md:
      "## 用途\n\n把混乱的访问日志变成可读的 Top 错误与耗时分布。\n\n## 用法\n\n```bash\n./log-analyzer --path /var/log/nginx/access.log\n```\n\n- 支持 gzip 归档\n- 输出 Markdown 报告",
    tool_type: "file",
    visibility: "public",
    category_slug: "dev-tools",
    tags: ["python", "log", "ops"],
    cover: true,
    owner_username: "zhangsan",
    current_version: "1.2.0",
    file_size: 4821043,
    download_count: 137,
    view_count: 892,
    published_at: "2025-01-08T09:12:00Z",
    updated_at: "2025-03-02T14:31:00Z",
  },
  {
    id: 2,
    slug: "deploy-assistant",
    name: "部署助手",
    summary: "把构建产物一键发布到测试环境，自动串联审批与回滚点。",
    description_md:
      "## 用途\n\n面向测试环境的自助发布入口。\n\n> 生产发布仍需走审批流程。",
    tool_type: "webapp",
    visibility: "public",
    category_slug: "ops-tools",
    tags: ["deploy", "ops", "k8s"],
    cover: true,
    owner_username: "lisi",
    current_version: "2.4.1",
    file_size: null,
    download_count: 96,
    view_count: 640,
    published_at: "2025-01-22T03:05:00Z",
    updated_at: "2025-02-26T07:18:00Z",
    webapp_url: "http://deploy.intra.example.com",
  },
  {
    id: 3,
    slug: "code-review-skill",
    name: "代码审查 Skill",
    summary: "按团队规约审查 diff，给出可执行的修改建议。",
    description_md:
      "## 用途\n\n把团队规约固化成一条可复用的审查指令。\n\n## 输入\n\n- git diff\n- 项目规约文档",
    tool_type: "skill",
    visibility: "public",
    category_slug: "skills",
    tags: ["review", "python"],
    cover: false, // 无封面 #1 — placeholder block + type icon
    owner_username: "wangwu",
    current_version: "1.0.3",
    file_size: 128400,
    download_count: 76,
    view_count: 431,
    published_at: "2025-02-01T06:40:00Z",
    updated_at: "2025-03-05T02:12:00Z",
  },
  {
    id: 4,
    slug: "inspection-scripts",
    name: "巡检脚本集",
    summary: "覆盖 CPU、内存、磁盘、网络与中间件的日常巡检脚本合集。",
    description_md:
      "## 用途\n\n每天 09:00 定时巡检，输出统一格式的报告。\n\n```bash\n./inspect.sh --all\n```",
    tool_type: "file",
    visibility: "public",
    category_slug: "ops-tools",
    tags: ["shell", "ops", "k8s"],
    cover: true,
    owner_username: "lisi",
    current_version: "3.1.0",
    file_size: 720896,
    download_count: 54,
    view_count: 318,
    published_at: "2024-12-11T01:20:00Z",
    updated_at: "2025-02-18T09:55:00Z",
  },
  {
    id: 5,
    slug: "architecture-review-prompt",
    name: "架构评审提示词",
    summary: "按可靠性、可观测性、成本三个维度评审架构方案。",
    description_md:
      "## 用途\n\n把架构评审从「凭感觉」变成「按清单」。\n\n## 使用\n\n把方案文档粘贴在提示词后面。",
    tool_type: "prompt",
    visibility: "public",
    category_slug: "prompts",
    tags: ["prompt", "architecture"],
    cover: false, // 无封面 #2
    owner_username: "admin",
    current_version: "1.1.0",
    file_size: null,
    download_count: 41,
    view_count: 502,
    published_at: "2025-02-14T08:00:00Z",
    updated_at: "2025-03-01T11:26:00Z",
  },
  {
    id: 6,
    slug: "openapi-doc-generator",
    name: "接口文档生成器",
    summary: "从 FastAPI 项目一键导出 OpenAPI 文档并生成可分享的网页。",
    description_md:
      "## 用途\n\n把 `openapi.json` 变成一份可以发给同事的文档站。\n\n## 特性\n\n- 支持鉴权示例\n- 支持离线导出",
    tool_type: "webapp",
    visibility: "public",
    category_slug: "dev-tools",
    tags: ["fastapi", "python", "internal"],
    cover: true,
    owner_username: "zhangsan",
    current_version: "0.9.7",
    file_size: null,
    download_count: 63,
    view_count: 407,
    published_at: "2025-02-20T05:45:00Z",
    updated_at: "2025-03-06T03:02:00Z",
    webapp_url: "http://apidoc.intra.example.com",
  },
  {
    id: 7,
    slug: "gray-release-orchestrator",
    name: LONG_TOOL_NAME,
    summary: LONG_TOOL_SUMMARY,
    description_md:
      "## 用途\n\n面向多集群的灰度发布编排。\n\n## 步骤\n\n1. 预发 1% 流量\n2. 指标校验\n3. 逐级放量\n4. 失败自动回滚",
    tool_type: "skill",
    visibility: "public",
    category_slug: "skills",
    tags: ["k8s", "deploy", "ops", "review"],
    cover: true,
    owner_username: "wangwu",
    current_version: "1.4.2",
    file_size: 2048576,
    download_count: 88,
    view_count: 559,
    published_at: "2025-01-30T10:10:00Z",
    updated_at: "2025-03-07T06:48:00Z",
  },
  {
    id: 8,
    slug: "slow-sql-helper",
    name: "SQL 慢查询排查助手",
    summary: "给定慢查询与执行计划，输出索引建议与改写方案。",
    description_md:
      "## 用途\n\n把执行计划翻译成可执行的优化建议。\n\n## 输入\n\n- 慢查询 SQL\n- `EXPLAIN` 输出",
    tool_type: "prompt",
    visibility: "public",
    category_slug: "dev-tools",
    tags: ["sql", "prompt", "log"],
    cover: true,
    owner_username: "zhangsan",
    current_version: "1.0.0",
    file_size: null,
    download_count: 29,
    view_count: 264,
    published_at: "2025-03-03T02:30:00Z",
    updated_at: "2025-03-08T04:15:00Z",
  },
];

interface GeneratedSpec {
  id: number;
  slug: string;
  name: string;
  summary: string;
  tool_type: ToolType;
  category_slug: string;
  tags: string[];
  cover: boolean;
  owner_username: string;
  current_version: string;
  webapp_url?: string;
}

/**
 * The 18 generated tools (CONTRACT §14.6 "新增 18 个普通工具"). Names are short
 * and summaries are < 3 lines, and none of them mentions `Nginx` or uses the
 * `architecture` tag — the M1 acceptance spec asserts on those discriminators.
 *
 * Type spread: file 5 / webapp 4 / skill 5 / prompt 4 (totals with the boundary
 * subset: 7 / 6 / 7 / 6).
 * Category spread: dev-tools 6 / ops-tools 5 / skills 4 / prompts 3
 * (totals: 8 / 6 / 6 / 6).
 * Coverless: `ai-commit-skill` and `test-case-generator-prompt` (4 in total with
 * the M1 pair).
 */
const GENERATED_SPECS: GeneratedSpec[] = [
  {
    id: 101,
    slug: "k8s-inspect-2d47",
    name: "Kubernetes 巡检",
    summary: "集群健康巡检面板：节点资源水位、Pod 重启 Top 与证书过期提醒。",
    tool_type: "webapp",
    category_slug: "ops-tools",
    tags: ["k8s", "ops", "monitor"],
    cover: true,
    owner_username: "admin",
    current_version: "1.0.0",
    webapp_url: "http://k8s-inspect.intra.example.com",
  },
  {
    id: 102,
    slug: "api-mock-server-7b1c",
    name: "API Mock 服务",
    summary: "按 OpenAPI 描述一键起 Mock 服务，支持延迟注入与错误码模拟。",
    tool_type: "webapp",
    category_slug: "dev-tools",
    tags: ["node", "test", "openapi"],
    cover: true,
    owner_username: "wangwu",
    current_version: "2.0.0",
    webapp_url: "http://mock.intra.example.com",
  },
  {
    id: 103,
    slug: "db-backup-toolkit-5e90",
    name: "数据库备份工具箱",
    summary: "PostgreSQL / MySQL 逻辑备份与一致性校验脚本集。",
    tool_type: "file",
    category_slug: "ops-tools",
    tags: ["shell", "backup", "database"],
    cover: true,
    owner_username: "lisi",
    current_version: "1.1.0",
  },
  {
    id: 104,
    slug: "ui-screenshot-diff",
    name: "UI 截图对比",
    summary: "对同一页面做像素级截图对比，输出差异热力图。",
    tool_type: "webapp",
    category_slug: "dev-tools",
    tags: ["frontend", "test", "internal"],
    cover: true,
    owner_username: "zhangsan",
    current_version: "0.6.2",
    webapp_url: "http://uidiff.intra.example.com",
  },
  {
    id: 105,
    slug: "secret-scanner-cli",
    name: "敏感信息扫描器",
    summary: "扫描仓库与镜像中的密钥、令牌与私钥，支持基线豁免。",
    tool_type: "file",
    category_slug: "prompts",
    tags: ["security", "shell", "internal"],
    cover: true,
    owner_username: "admin",
    current_version: "0.8.0",
  },
  {
    id: 106,
    slug: "trace-collector-webapp",
    name: "链路追踪采集台",
    summary: "采集 OpenTelemetry spans，按服务与耗时区间聚合展示。",
    tool_type: "webapp",
    category_slug: "ops-tools",
    tags: ["observability", "ops", "monitor"],
    cover: true,
    owner_username: "zhangsan",
    current_version: "0.5.0",
    webapp_url: "http://trace.intra.example.com",
  },
  {
    id: 107,
    slug: "log-cleanup-scripts",
    name: "日志清理脚本",
    summary: "按保留策略清理历史日志与归档，支持 dry-run 预演。",
    tool_type: "file",
    category_slug: "ops-tools",
    tags: ["log", "shell", "ops"],
    cover: true,
    owner_username: "wangwu",
    current_version: "1.2.0",
  },
  {
    id: 108,
    slug: "docker-image-helper",
    name: "Docker 镜像助手",
    summary: "分析镜像分层与体积，给出可落地的瘦身建议。",
    tool_type: "file",
    category_slug: "dev-tools",
    tags: ["docker", "ops", "performance"],
    cover: true,
    owner_username: "lisi",
    current_version: "0.9.1",
  },
  {
    id: 109,
    slug: "frontend-scaffold-skill",
    name: "前端脚手架 Skill",
    summary: "按团队规范生成 React + TypeScript 页面骨架与测试。",
    tool_type: "skill",
    category_slug: "skills",
    tags: ["ai", "frontend", "skill"],
    cover: true,
    owner_username: "zhangsan",
    current_version: "1.0.0",
  },
  {
    id: 110,
    slug: "ai-commit-skill",
    name: "AI 提交信息 Skill",
    summary: "根据 diff 生成符合规约的提交信息与变更说明。",
    tool_type: "skill",
    category_slug: "skills",
    tags: ["ai", "skill", "internal"],
    cover: false, // 无封面 #3
    owner_username: "wangwu",
    current_version: "0.3.0",
  },
  {
    id: 111,
    slug: "terminal-tutor-skill",
    name: "命令行导师 Skill",
    summary: "解释命令与报错信息，给出可执行的下一条命令。",
    tool_type: "skill",
    category_slug: "skills",
    tags: ["ai", "shell", "skill"],
    cover: true,
    owner_username: "lisi",
    current_version: "0.2.1",
  },
  {
    id: 112,
    slug: "test-case-generator-prompt",
    name: "测试用例生成提示词",
    summary: "根据接口描述生成边界与异常用例清单。",
    tool_type: "prompt",
    category_slug: "prompts",
    tags: ["prompt", "test", "testcase"],
    cover: false, // 无封面 #4
    owner_username: "admin",
    current_version: "1.0.0",
  },
  {
    id: 113,
    slug: "release-note-prompt",
    name: "发布公告提示词",
    summary: "把合并记录整理成对内发布公告与变更清单。",
    tool_type: "prompt",
    category_slug: "prompts",
    tags: ["prompt", "release", "writing"],
    cover: true,
    owner_username: "lisi",
    current_version: "1.1.0",
  },
  {
    id: 114,
    slug: "cache-design-prompt",
    name: "缓存设计提示词",
    summary: "评审缓存键、过期策略与击穿防护的三个必答问题。",
    tool_type: "prompt",
    category_slug: "dev-tools",
    tags: ["prompt", "cache", "architecture"],
    cover: true,
    owner_username: "wangwu",
    current_version: "0.4.0",
  },
  {
    id: 115,
    slug: "oncall-handover-skill",
    name: "值班交接 Skill",
    summary: "汇总当日告警与处理进展，生成结构化交接文档。",
    tool_type: "skill",
    category_slug: "skills",
    tags: ["ai", "sre", "oncall"],
    cover: true,
    owner_username: "lisi",
    current_version: "0.5.2",
  },
  {
    id: 116,
    slug: "refactor-advisor-prompt",
    name: "重构建议提示词",
    summary: "识别长函数与重复逻辑，输出分步重构计划。",
    tool_type: "prompt",
    category_slug: "prompts",
    tags: ["prompt", "refactor", "review"],
    cover: true,
    owner_username: "zhangsan",
    current_version: "0.7.0",
  },
  {
    id: 117,
    slug: "permission-audit-skill",
    name: "权限审计 Skill",
    summary: "核对账号与授权清单，输出越权风险与整改建议。",
    tool_type: "skill",
    category_slug: "skills",
    tags: ["ai", "security", "skill"],
    cover: true,
    owner_username: "lisi",
    current_version: "0.6.0",
  },
  {
    id: 118,
    slug: "compliance-check-prompt",
    name: "合规检查提示词",
    summary: "按内控条款检查方案文档，标出缺失的必备章节。",
    tool_type: "prompt",
    category_slug: "prompts",
    tags: ["prompt", "compliance", "security"],
    cover: true,
    owner_username: "wangwu",
    current_version: "0.5.0",
  },
];

/** Fixed timestamps + metrics for the generated tools — deterministic, varied. */
const GENERATED_PUBLISHED = [
  "2024-10-08T02:10:00Z",
  "2024-10-21T06:35:00Z",
  "2024-11-04T09:05:00Z",
  "2024-11-19T01:40:00Z",
  "2024-12-02T05:25:00Z",
  "2024-12-16T08:50:00Z",
  "2024-12-29T03:15:00Z",
  "2025-01-06T07:30:00Z",
  "2025-01-15T04:05:00Z",
  "2025-01-24T10:20:00Z",
  "2025-02-03T06:55:00Z",
  "2025-02-11T02:45:00Z",
  "2025-02-19T09:30:00Z",
  "2025-02-27T05:10:00Z",
  "2025-03-04T08:25:00Z",
  "2025-03-06T03:50:00Z",
  "2025-03-08T07:05:00Z",
  "2025-03-10T01:35:00Z",
] as const;

const GENERATED_DOWNLOADS = [
  12, 204, 38, 175, 61, 5, 149, 27, 96, 233, 44, 118, 72, 190, 19, 85, 131, 58,
] as const;

const GENERATED_VIEWS = [
  210, 1320, 480, 1180, 640, 96, 1520, 330, 870, 1660, 520, 940, 610, 1440, 260,
  700, 1080, 590,
] as const;

function fileSizeFor(spec: GeneratedSpec, index: number): number | null {
  if (spec.tool_type === "webapp" || spec.tool_type === "prompt") return null;
  const rng = mulberry32(9000 + index * 17);
  const base = spec.tool_type === "skill" ? 90_000 : 320_000;
  return base + Math.floor(rng() * 2_400_000);
}

const GENERATED_TOOL_SEEDS: ToolSeed[] = GENERATED_SPECS.map((spec, index) => ({
  id: spec.id,
  slug: spec.slug,
  name: spec.name,
  summary: spec.summary,
  description_md: `## 用途\n\n${spec.summary}\n\n## 使用\n\n在门户下载或直接打开链接，按 README 中的步骤使用。\n\n- 分类：${spec.category_slug}\n- 标签：${spec.tags.join(" / ")}`,
  tool_type: spec.tool_type,
  visibility: "public" as const,
  category_slug: spec.category_slug,
  tags: [...spec.tags],
  cover: spec.cover,
  owner_username: spec.owner_username,
  current_version: spec.current_version,
  file_size: fileSizeFor(spec, index),
  download_count: GENERATED_DOWNLOADS[index] ?? 20,
  view_count: GENERATED_VIEWS[index] ?? 300,
  published_at: GENERATED_PUBLISHED[index] ?? "2025-03-10T01:35:00Z",
  updated_at: updatedAtFor(index),
  webapp_url: spec.webapp_url ?? null,
}));

function updatedAtFor(index: number): string {
  const day = 11 + (index % 18);
  const hour = 1 + (index * 3) % 12;
  return `2025-03-${String(day).padStart(2, "0")}T${String(hour).padStart(2, "0")}:20:00Z`;
}

/** All 26 approved public portal tools = boundary 8 + generated 18. */
export const MOCK_TOOL_SEEDS: ToolSeed[] = [...BOUNDARY_TOOL_SEEDS, ...GENERATED_TOOL_SEEDS];

function categoryBySlug(slug: string): Category {
  const category = MOCK_CATEGORIES.find((item) => item.slug === slug);
  if (!category) throw new Error(`mock seed error: unknown category ${slug}`);
  return category;
}

function ownerByUsername(username: string): MockUser {
  const user = MOCK_USERS.find((item) => item.username === username);
  if (!user) throw new Error(`mock seed error: unknown owner ${username}`);
  return user;
}

/**
 * Capability URL (CONTRACT §14.3): images are fetched with `?sig=`, not a header.
 *
 * 假签名的**形状**与真实后端一致：`<token>.<exp>`（M5 起真后端也下发 `sig=`）。
 * 早期版本写的是 `sig=dev`，形状对不上，容易掩盖「前端其实在拼 URL」这类回归。
 */
export function imageUrl(imageId: number, variant: "orig" | "thumb" = "orig"): string {
  return `/api/v1/images/${imageId}?variant=${variant}&sig=mock-${imageId}-${variant}.4102444800`;
}

/** Cover image id = tool id + 1000 (the mock image endpoint serves a generated SVG). */
export function coverImageId(toolId: number): number {
  return 1000 + toolId;
}

/** Portal envelope item for a seeded tool (matches `ToolListItem` exactly). */
export function toolSeedToListItem(seed: ToolSeed): ToolListItem {
  const category = categoryBySlug(seed.category_slug);
  const owner = ownerByUsername(seed.owner_username);
  return {
    id: seed.id,
    slug: seed.slug,
    name: seed.name,
    summary: seed.summary,
    tool_type: seed.tool_type,
    visibility: seed.visibility,
    category: {
      id: category.id,
      slug: category.slug,
      name: category.name,
      icon: category.icon,
    },
    tags: seed.tags,
    cover_url: seed.cover ? imageUrl(coverImageId(seed.id), "thumb") : null,
    owner: {
      id: owner.id,
      username: owner.username,
      display_name: owner.display_name,
    },
    current_version: seed.current_version,
    file_size: seed.file_size,
    download_count: seed.download_count,
    view_count: seed.view_count,
    has_pending_version: false,
    can_download: true,
    published_at: seed.published_at,
    updated_at: seed.updated_at,
  };
}

/**
 * The 26 seeded tools in `ToolListItem` form, i.e. `/tools` for a logged-in user.
 * `has_pending_version` / `can_download` are recomputed per request in
 * `handlers.ts`; the values here are the anonymous / plain-reader view.
 */
export const MOCK_TOOLS: ToolListItem[] = MOCK_TOOL_SEEDS.map(toolSeedToListItem);

/** Searchable body text per tool (`description_md`), kept out of the list DTO. */
export const MOCK_TOOL_BODIES: Record<number, string> = Object.fromEntries(
  MOCK_TOOL_SEEDS.map((seed) => [seed.id, seed.description_md]),
);

export function findToolSeed(id: number): ToolSeed | undefined {
  return MOCK_TOOL_SEEDS.find((seed) => seed.id === id);
}

export function countByCategory(): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const tool of MOCK_TOOLS) {
    const slug = tool.category?.slug ?? "uncategorised";
    counts[slug] = (counts[slug] ?? 0) + 1;
  }
  return counts;
}

export function countByType(): Record<ToolType, number> {
  const counts: Record<ToolType, number> = { file: 0, webapp: 0, skill: 0, prompt: 0 };
  for (const tool of MOCK_TOOLS) counts[tool.tool_type] += 1;
  return counts;
}

/* -------------------------------------------------------------------------- */
/* Skill package seed                                                         */
/* -------------------------------------------------------------------------- */

export interface MockSkillFile {
  path: string;
  size: number;
  is_dir: boolean;
  sha256: string | null;
}

/** Fixed, realistic SKILL.md manifest (no YAML parsing in the mock). */
export const MOCK_SKILL_MANIFEST: Record<string, unknown> = {
  name: "internal-skill",
  description: "内网工具平台的示例 Skill 包",
  version: "1.0.0",
  "allowed-tools": ["Bash", "Read", "Write"],
};

export const MOCK_SKILL_README = `# 内网示例 Skill

## 用法

\`\`\`bash
skill run --input ./examples/input.txt
\`\`\`

- 支持 Markdown 输入
- 输出结构化结果
`;

/** A realistic file tree: two directories, three files, one nested directory. */
export function buildSkillFileTree(toolName: string): MockSkillFile[] {
  const files: Array<{ path: string; size: number }> = [
    { path: "SKILL.md", size: 2048 },
    { path: "README.md", size: 1360 },
    { path: "scripts", size: 0 },
    { path: "scripts/main.py", size: 8192 },
    { path: "scripts/parser.py", size: 4096 },
    { path: "assets", size: 0 },
    { path: "assets/logo.png", size: 24576 },
    { path: "assets/icon.svg", size: 1840 },
    { path: "examples", size: 0 },
    { path: "examples/input.txt", size: 512 },
    { path: "examples/nested", size: 0 },
    { path: "examples/nested/expected.md", size: 768 },
  ];
  return files.map((file) => ({
    path: file.path,
    size: file.size,
    is_dir: file.size === 0 && !file.path.includes("."),
    sha256: file.size === 0 ? null : fakeSha256(`${toolName}|${file.path}`).slice(0, 16),
  }));
}

/** Roll-up used by `ToolDetail.skill.file_tree_summary` / version `skill` blocks. */
export function skillTreeSummary(tree: MockSkillFile[]): {
  file_count: number;
  total_size: number;
  max_depth: number;
} {
  const files = tree.filter((entry) => !entry.is_dir);
  return {
    file_count: files.length,
    total_size: files.reduce((total, entry) => total + entry.size, 0),
    max_depth: tree.reduce((max, entry) => Math.max(max, entry.path.split("/").length), 0),
  };
}

/* -------------------------------------------------------------------------- */
/* Stateful seed — versions, images, ACL, approvals                           */
/* -------------------------------------------------------------------------- */

export interface MockVersion {
  id: number;
  tool_id: number;
  version: string;
  changelog_md: string;
  status: VersionStatus;
  file_name: string | null;
  file_size: number | null;
  file_sha256: string | null;
  file_ext: string | null;
  mime_type: string | null;
  uploaded_by_id: number;
  approved_at: string | null;
  reject_reason: string | null;
  purged_at: string | null;
  created_at: string;
  prompt_content: string | null;
  submitted_at: string | null;
}

export interface MockToolRecord {
  seed: ToolSeed;
  status: ToolStatus;
  version_seq: number;
  created_at: string;
  last_version_at: string | null;
  reject_reason: string | null;
  offline_reason: string | null;
  deleted_at: string | null;
  webapp_url: string | null;
  versions: MockVersion[];
  images: ToolImage[];
  acl_visibility: Visibility;
  acl: Array<{
    id: number;
    subject_type: "user" | "group";
    subject_id: number;
    can_download: boolean;
  }>;
  submitted_at: string | null;
  submission_type: SubmissionType | null;
  history_version_limit: number;
}

export interface MockDownloadLog {
  id: number;
  user_id: number;
  tool_id: number;
  version_id: number | null;
  file_name: string | null;
  file_size: number | null;
  created_at: string;
}

export const MOCK_VERSION_HISTORY_LIMIT = 10;

const VERSION_SEQ_START = 5000;
let nextVersionId = VERSION_SEQ_START + 1;

function mimeForExt(ext: string | null): string | null {
  switch (ext) {
    case "zip":
      return "application/zip";
    case "tar":
      return "application/x-tar";
    case "gz":
      return "application/gzip";
    case "md":
      return "text/markdown";
    case "sh":
      return "application/x-sh";
    default:
      return ext ? "application/octet-stream" : null;
  }
}

function extForSeed(seed: ToolSeed): string | null {
  if (seed.tool_type === "webapp") return null;
  if (seed.tool_type === "prompt") return "md";
  if (seed.tool_type === "skill") return "zip";
  return "zip";
}

function versionFileName(seed: ToolSeed, version: string, ext: string | null): string | null {
  if (!ext) return null;
  const base = seed.slug.replace(/[^a-z0-9-]/g, "");
  return `${base}-${version}.${ext}`;
}

function makeVersion(
  seed: ToolSeed,
  version: string,
  status: VersionStatus,
  createdAt: string,
  options: {
    changelog?: string;
    approvedAt?: string | null;
    rejectReason?: string | null;
    uploadedById?: number;
    submittedAt?: string | null;
    fileSize?: number | null;
    /** 归档时间（status === "purged"）。 */
    purgedAt?: string | null;
  } = {},
): MockVersion {
  const ext = extForSeed(seed);
  const size = options.fileSize === undefined ? seed.file_size : options.fileSize;
  const promptContent =
    seed.tool_type === "prompt"
      ? `你是团队内的资深工程师。请阅读下面的输入，按以下结构输出：\n\n1. 结论\n2. 依据\n3. 可执行建议\n\n输入：\n{{input}}`
      : null;
  return {
    id: nextVersionId++,
    tool_id: seed.id,
    version,
    changelog_md: options.changelog ?? "示例变更说明：修复已知问题并补充文档。",
    status,
    file_name: versionFileName(seed, version, ext),
    file_size: ext ? size : null,
    file_sha256: ext ? fakeSha256(`${seed.slug}|${version}`) : null,
    file_ext: ext,
    mime_type: mimeForExt(ext),
    uploaded_by_id: options.uploadedById ?? ownerByUsername(seed.owner_username).id,
    approved_at: options.approvedAt ?? null,
    reject_reason: options.rejectReason ?? null,
    purged_at: options.purgedAt ?? null,
    created_at: createdAt,
    prompt_content: promptContent,
    submitted_at: options.submittedAt ?? null,
  };
}

/**
 * `admin`-owned tools that carry the stateful demo scenarios (one per status).
 * They are *not* part of the 26-tool portal set — a `draft` or `offline` tool has
 * no business in the public list; they exist so 我的工具 / 审批 can be driven.
 */
const ADMIN_STATE_SPECS: Array<{
  id: number;
  slug: string;
  name: string;
  summary: string;
  tool_type: ToolType;
  category_slug: string;
  tags: string[];
  status: ToolStatus;
  version: string;
  version_status: VersionStatus;
  version_created_at: string;
  submitted_at?: string;
  submission_type?: SubmissionType;
  reject_reason?: string;
  offline_reason?: string;
}> = [
  {
    id: 201,
    slug: "admin-grafana-dashboard-template",
    name: "监控大盘模板",
    summary: "开箱即用的 Grafana 大盘模板，覆盖 RED 与 USE 两类指标。",
    tool_type: "file",
    category_slug: "ops-tools",
    tags: ["monitor", "ops", "internal"],
    status: "draft",
    version: "0.1.0",
    version_status: "rejected",
    version_created_at: "2025-03-06T02:40:00Z",
  },
  {
    id: 202,
    slug: "admin-internal-cli-toolkit",
    name: "内部 CLI 工具箱",
    summary: "把常用运维命令封装成带补全的统一 CLI。",
    tool_type: "file",
    category_slug: "ops-tools",
    tags: ["shell", "internal", "ops"],
    status: "pending",
    version: "0.3.0",
    version_status: "pending",
    version_created_at: "2025-03-13T22:00:00Z",
    submitted_at: "2025-03-13T22:00:00Z",
    submission_type: "new_tool",
  },
  {
    id: 203,
    slug: "admin-cost-report-prompt",
    name: "成本分析提示词",
    summary: "把云账单拆成可归因的团队与业务维度。",
    tool_type: "prompt",
    category_slug: "prompts",
    tags: ["prompt", "internal"],
    status: "approved",
    version: "1.0.0",
    version_status: "approved",
    version_created_at: "2025-02-25T03:30:00Z",
  },
  {
    id: 204,
    slug: "admin-legacy-build-scripts",
    name: "遗留构建脚本集",
    summary: "老项目的构建脚本归档，等待迁移到统一流水线。",
    tool_type: "file",
    category_slug: "dev-tools",
    tags: ["shell", "internal"],
    status: "rejected",
    version: "0.2.0",
    version_status: "rejected",
    version_created_at: "2025-03-02T07:20:00Z",
    reject_reason: "包内 scripts/build.sh 引用了已下线的内部制品库地址，请更新后重新提交。",
  },
  {
    id: 205,
    slug: "admin-alert-rules-skill",
    name: "告警规则 Skill",
    summary: "根据服务指标生成告警规则草案并解释阈值来源。",
    tool_type: "skill",
    category_slug: "skills",
    tags: ["ai", "monitor", "sre"],
    status: "pending_update",
    version: "1.1.0",
    version_status: "pending",
    version_created_at: "2025-03-07T00:00:00Z",
    submitted_at: "2025-03-07T00:00:00Z",
    submission_type: "new_version",
  },
  {
    id: 206,
    slug: "admin-deprecated-api-doc",
    name: "旧版接口文档站",
    summary: "已下线接口的存档文档，仅供历史查询。",
    tool_type: "webapp",
    category_slug: "dev-tools",
    tags: ["internal", "openapi"],
    status: "offline",
    version: "0.4.0",
    version_status: "approved",
    version_created_at: "2024-12-20T06:00:00Z",
    offline_reason: "接口已全部下线，为避免误导新同学，暂时下架归档。",
  },
];

interface PendingOverride {
  version: string;
  changelog: string;
  created_at: string;
  submitted_at: string;
  uploaded_by_id: number;
  file_size?: number | null;
}

/**
 * Pending *new versions* on already-published tools (待审新版本). `submitted_at`
 * is fixed so `waiting_hours` is a stable 8 / 50 / 1 hours.
 */
const PENDING_VERSION_OVERRIDES: Record<number, PendingOverride> = {
  101: {
    version: "1.1.0",
    changelog: "新增证书过期提醒与事件聚合视图。",
    created_at: "2025-03-13T20:00:00Z",
    submitted_at: "2025-03-13T20:00:00Z",
    uploaded_by_id: 3,
    file_size: null,
  },
  106: {
    version: "0.6.0",
    changelog: "支持按服务维度下钻并导出 CSV。",
    created_at: "2025-03-12T02:00:00Z",
    submitted_at: "2025-03-12T02:00:00Z",
    uploaded_by_id: 3,
  },
  103: {
    version: "1.2.0",
    changelog: "备份校验增加行数比对。",
    created_at: "2025-03-14T03:00:00Z",
    submitted_at: "2025-03-14T03:00:00Z",
    uploaded_by_id: 2,
  },
};

/** Generated cover URL for a tool that has a cover (fake SVG served by the mock). */
function seedImage(seed: ToolSeed, kind: "cover" | "screenshot", index: number): ToolImage {
  const id = coverImageId(seed.id) + index;
  return {
    id,
    kind,
    url: imageUrl(id, "orig"),
    thumb_url: imageUrl(id, "thumb"),
    file_name: kind === "cover" ? `${seed.slug}-cover.png` : `${seed.slug}-screen-${index}.png`,
    mime_type: "image/png",
    file_size: 180_000 + index * 1024,
    width: kind === "cover" ? 1200 : 1440,
    height: kind === "cover" ? 630 : 900,
    sort_order: index,
    alt_text: kind === "cover" ? `${seed.name} 封面` : `${seed.name} 界面截图`,
    sha256: fakeSha256(`${seed.slug}|image|${index}`).slice(0, 16),
    created_at: seed.published_at,
  };
}

/**
 * Build the in-memory tool records: the 26 approved public tools (one approved
 * current version each; a few also carry pending / rejected / superseded history)
 * plus the 6 admin state tools.
 */
export function buildInitialToolRecords(): MockToolRecord[] {
  const records: MockToolRecord[] = MOCK_TOOL_SEEDS.map((seed) => {
    const pending = PENDING_VERSION_OVERRIDES[seed.id];
    const status: ToolStatus = pending ? "pending_update" : "approved";
    const history = seed.id === 1 ? 3 : seed.id >= 101 && seed.id % 5 === 0 ? 2 : 1;
    const versions: MockVersion[] = [];

    if (history >= 2) {
      versions.push(
        makeVersion(seed, previousVersion(seed.current_version), "superseded", "2025-01-15T02:00:00Z", {
          changelog: "上一稳定版本，已被当前版本取代。",
          approvedAt: "2025-01-20T02:00:00Z",
        }),
      );
    }
    if (seed.id === 1) {
      versions.push(
        makeVersion(seed, "1.1.0", "superseded", "2025-02-10T02:00:00Z", {
          changelog: "新增按状态码聚合。",
          approvedAt: "2025-02-15T02:00:00Z",
        }),
      );
      // 超出 history_limit 被归档的版本：保留在时间线里但不可下载（FR-VER-08）
      versions.push(
        makeVersion(seed, "1.0.0", "purged", "2024-12-01T02:00:00Z", {
          changelog: "首个公开版本，已超出保留期限归档。",
          approvedAt: "2024-12-05T02:00:00Z",
          purgedAt: "2025-03-01T02:00:00Z",
        }),
      );
    }
    const pendingVersion = PENDING_VERSION_OVERRIDES[seed.id];
    if (pendingVersion) {
      versions.push(
        makeVersion(seed, pendingVersion.version, "pending", pendingVersion.created_at, {
          changelog: pendingVersion.changelog,
          submittedAt: pendingVersion.submitted_at,
          uploadedById: pendingVersion.uploaded_by_id,
          fileSize: pendingVersion.file_size ?? undefined,
        }),
      );
    }
    versions.push(
      makeVersion(seed, seed.current_version, "approved", seed.published_at, {
        changelog: "首个稳定版本。",
        approvedAt: seed.published_at,
      }),
    );

    const images: ToolImage[] = [];
    if (seed.cover) images.push(seedImage(seed, "cover", 0));
    if (seed.id % 3 === 0) images.push(seedImage(seed, "screenshot", 1));

    const record: MockToolRecord = {
      seed,
      status,
      version_seq: versions.length,
      created_at: seed.published_at,
      last_version_at: seed.updated_at,
      reject_reason: null,
      offline_reason: null,
      deleted_at: null,
      webapp_url: seed.webapp_url ?? null,
      versions,
      images,
      acl_visibility: "public",
      acl: [],
      submitted_at: pending?.submitted_at ?? null,
      submission_type: pending ? "new_version" : null,
      history_version_limit: MOCK_VERSION_HISTORY_LIMIT,
    };
    return record;
  });

  for (const spec of ADMIN_STATE_SPECS) {
    const seed: ToolSeed = {
      id: spec.id,
      slug: spec.slug,
      name: spec.name,
      summary: spec.summary,
      description_md: `## 用途\n\n${spec.summary}`,
      tool_type: spec.tool_type,
      visibility: spec.status === "draft" ? "private" : "public",
      category_slug: spec.category_slug,
      tags: [...spec.tags],
      cover: spec.id !== 204,
      owner_username: "admin",
      current_version: spec.status === "draft" ? "" : spec.version,
      file_size: spec.tool_type === "webapp" ? null : 246_800 + spec.id,
      download_count: spec.id * 3,
      view_count: spec.id * 11,
      published_at: spec.status === "approved" || spec.status === "offline" ? spec.version_created_at : "",
      updated_at: spec.version_created_at,
      webapp_url:
        spec.tool_type === "webapp" ? `http://${spec.slug}.intra.example.com` : null,
    };
    if (spec.status === "draft") seed.current_version = "0.1.0";

    const versions: MockVersion[] = [];
    if (spec.status === "pending_update" || spec.id === 205) {
      versions.push(
        makeVersion(seed, "1.0.0", "approved", "2025-02-22T03:30:00Z", {
          changelog: "首个稳定版本。",
          approvedAt: "2025-02-22T03:30:00Z",
        }),
      );
    }
    versions.push(
      makeVersion(seed, spec.version, spec.version_status, spec.version_created_at, {
        changelog:
          spec.version_status === "rejected"
            ? "尝试升级依赖并调整脚本。"
            : "补充文档与示例。",
        approvedAt: spec.version_status === "approved" ? spec.version_created_at : null,
        rejectReason: spec.reject_reason ?? null,
        submittedAt: spec.submitted_at ?? null,
      }),
    );

    records.push({
      seed,
      status: spec.status,
      version_seq: versions.length,
      created_at: "2025-01-28T02:00:00Z",
      last_version_at: spec.version_created_at,
      reject_reason: spec.reject_reason ?? null,
      offline_reason: spec.offline_reason ?? null,
      deleted_at: null,
      webapp_url: seed.webapp_url ?? null,
      versions,
      images: spec.id === 204 ? [] : [seedImage(seed, "cover", 0)],
      acl_visibility: "public",
      acl: [],
      submitted_at: spec.submitted_at ?? null,
      submission_type: spec.submission_type ?? null,
      history_version_limit: MOCK_VERSION_HISTORY_LIMIT,
    });
  }

  // A restricted tool with a pre-populated ACL, so the ACL editor and the
  // "restricted requires >= 1 entry" rule have both states available.
  const restricted = records.find((record) => record.seed.id === 108);
  if (restricted) {
    restricted.acl_visibility = "restricted";
    restricted.seed = { ...restricted.seed, visibility: "restricted" };
    restricted.acl = [
      { id: 1, subject_type: "user", subject_id: 3, can_download: true },
      { id: 2, subject_type: "group", subject_id: 2, can_download: false },
    ];
  }

  return records;
}

function previousVersion(version: string): string {
  const parts = version.split(".").map((part) => Number.parseInt(part, 10));
  const [major = 1, minor = 0, patch = 0] = parts;
  if (patch > 0) return `${major}.${minor}.${patch - 1}`;
  if (minor > 0) return `${major}.${minor - 1}.0`;
  return `${Math.max(0, major - 1)}.0.0`;
}

/**
 * Waiting time in hours, rounded to one decimal — computed from the fixed
 * `submitted_at` + the frozen `MOCK_WAITING_HOURS` offsets, so the queue shows
 * the 3.4 / 26 / 80 … values regardless of when the suite runs.
 */
export function waitingHoursFor(toolId: number, submittedAt?: string | null): number {
  const pinned = MOCK_WAITING_HOURS[toolId];
  if (pinned !== undefined) return pinned;
  if (!submittedAt) return 0;
  return Math.round(((MOCK_APPROVAL_EPOCH_MS - Date.parse(submittedAt)) / 3_600_000) * 10) / 10;
}

/**
 * The instant the seeded `submitted_at` values are relative to: the freshest
 * submission (208, 2h) is `2025-03-14T05:00:00Z`, the oldest (205, 80h) is
 * `2025-03-11T01:00:00Z`.
 */
const MOCK_APPROVAL_EPOCH_MS = Date.parse("2025-03-14T07:00:00Z");

/** `submitted_at` values behind the approval queue's waiting-hour highlights. */
export const MOCK_WAITING_HOURS: Record<number, number> = {
  101: 8,
  103: 1,
  106: 50,
  202: 3.4,
  205: 80,
  207: 26,
  208: 2,
};

/** 待审新版本 tools keep `status='pending_update'` and report `submission_type='new_version'`. */
export const PENDING_UPDATE_TOOL_IDS: readonly number[] = Object.keys(
  PENDING_VERSION_OVERRIDES,
).map((key) => Number.parseInt(key, 10));

/**
 * Two extra `new_tool` submissions owned by other users so the approval queue has
 * more than one author and both `submission_type` values. They are drafts that
 * were submitted (modeled as `pending` tools owned by 张三 / 王五).
 */
export const EXTRA_SUBMISSION_SPECS: Array<{
  id: number;
  slug: string;
  name: string;
  summary: string;
  tool_type: ToolType;
  category_slug: string;
  tags: string[];
  owner_username: string;
  version: string;
  file_size: number | null;
  submitted_at: string;
}> = [
  {
    id: 207,
    slug: "zhangsan-log-rotate-tool",
    name: "日志轮转配置生成器",
    summary: "根据目录规模生成 logrotate 配置并校验保留策略。",
    tool_type: "file",
    category_slug: "ops-tools",
    tags: ["log", "ops", "shell"],
    owner_username: "zhangsan",
    version: "0.1.0",
    file_size: 348_160,
    submitted_at: "2025-03-13T04:00:00Z",
  },
  {
    id: 208,
    slug: "wangwu-incident-review-prompt",
    name: "故障复盘提示词",
    summary: "按时间线、根因、改进项三段式整理故障复盘。",
    tool_type: "prompt",
    category_slug: "prompts",
    tags: ["prompt", "sre", "oncall"],
    owner_username: "wangwu",
    version: "0.2.0",
    file_size: null,
    submitted_at: "2025-03-14T05:00:00Z",
  },
];

export function buildExtraSubmissionRecords(): MockToolRecord[] {
  return EXTRA_SUBMISSION_SPECS.map((spec) => {
    const seed: ToolSeed = {
      id: spec.id,
      slug: spec.slug,
      name: spec.name,
      summary: spec.summary,
      description_md: `## 用途\n\n${spec.summary}`,
      tool_type: spec.tool_type,
      visibility: "public",
      category_slug: spec.category_slug,
      tags: [...spec.tags],
      cover: true,
      owner_username: spec.owner_username,
      current_version: "",
      file_size: spec.file_size,
      download_count: 0,
      view_count: 0,
      published_at: "",
      updated_at: spec.submitted_at,
    };
    const version = makeVersion(seed, spec.version, "pending", spec.submitted_at, {
      changelog: "首次提交，等待审批。",
      submittedAt: spec.submitted_at,
    });
    return {
      seed,
      status: "pending" as const,
      version_seq: 1,
      created_at: spec.submitted_at,
      last_version_at: null,
      reject_reason: null,
      offline_reason: null,
      deleted_at: null,
      webapp_url: null,
      versions: [version],
      images: [seedImage(seed, "cover", 0)],
      acl_visibility: "public" as const,
      acl: [],
      submitted_at: spec.submitted_at,
      submission_type: "new_tool" as const,
      history_version_limit: MOCK_VERSION_HISTORY_LIMIT,
    };
  });
}

/* -------------------------------------------------------------------------- */
/* Recycle bin seed (M3 `GET /admin/recycle-bin`)                             */
/* -------------------------------------------------------------------------- */

/**
 * Soft-deleted tools. The backend only produces these through
 * `DELETE /me/tools/{id}`, but the recycle-bin console needs a non-empty list
 * on first paint, so two finished tools are seeded as already deleted. They are
 * invisible to the portal (each query filters `deleted_at`), and restoring one
 * puts it straight back into the portal / 全站工具 lists.
 */
const RECYCLE_BIN_SPECS: Array<{
  id: number;
  slug: string;
  name: string;
  summary: string;
  tool_type: ToolType;
  category_slug: string;
  tags: string[];
  owner_username: string;
  version: string;
  deleted_at: string;
  offline_reason: string;
}> = [
  {
    id: 301,
    slug: "legacy-log-crawler",
    name: "遗留日志采集器",
    summary: "上一代日志采集脚本，已被统一采集平台取代。",
    tool_type: "file",
    category_slug: "ops-tools",
    tags: ["log", "ops", "legacy-spike"],
    owner_username: "lisi",
    version: "0.9.4",
    deleted_at: "2025-03-09T02:00:00Z",
    offline_reason: "已被统一日志平台取代，保留 30 天后彻底清除。",
  },
  {
    id: 302,
    slug: "legacy-report-webapp",
    name: "旧版报表工具",
    summary: "老报表入口，数据口径已迁移到新平台。",
    tool_type: "webapp",
    category_slug: "dev-tools",
    tags: ["internal", "legacy-spike"],
    owner_username: "zhangsan",
    version: "1.2.0",
    deleted_at: "2025-03-11T06:30:00Z",
    offline_reason: "报表口径迁移完成，旧入口下架回收。",
  },
];

export function buildRecycleBinRecords(): MockToolRecord[] {
  return RECYCLE_BIN_SPECS.map((spec) => {
    const seed: ToolSeed = {
      id: spec.id,
      slug: spec.slug,
      name: spec.name,
      summary: spec.summary,
      description_md: `## 用途\n\n${spec.summary}\n\n## 状态\n\n已进入回收站，等待还原或彻底清除。`,
      tool_type: spec.tool_type,
      visibility: "public",
      category_slug: spec.category_slug,
      tags: [...spec.tags],
      cover: true,
      owner_username: spec.owner_username,
      current_version: spec.version,
      file_size: spec.tool_type === "webapp" ? null : 1_284_000 + spec.id,
      download_count: spec.id - 280,
      view_count: (spec.id - 280) * 9,
      published_at: "2024-11-20T02:00:00Z",
      updated_at: spec.deleted_at,
      webapp_url:
        spec.tool_type === "webapp" ? `http://${spec.slug}.intra.example.com` : null,
    };
    const version = makeVersion(seed, spec.version, "approved", "2024-11-20T02:00:00Z", {
      changelog: "末版，此后进入维护冻结。",
      approvedAt: "2024-11-21T02:00:00Z",
    });
    return {
      seed,
      status: "offline" as const,
      version_seq: 1,
      created_at: "2024-11-20T02:00:00Z",
      last_version_at: "2024-11-21T02:00:00Z",
      reject_reason: null,
      offline_reason: spec.offline_reason,
      deleted_at: spec.deleted_at,
      webapp_url: seed.webapp_url ?? null,
      versions: [version],
      images: [seedImage(seed, "cover", 0)],
      acl_visibility: "public" as const,
      acl: [],
      submitted_at: null,
      submission_type: null,
      history_version_limit: MOCK_VERSION_HISTORY_LIMIT,
    };
  });
}

/* -------------------------------------------------------------------------- */
/* API Token seed (M3 `GET /admin/tokens`)                                    */
/* -------------------------------------------------------------------------- */

export interface MockTokenRecord {
  id: number;
  name: string;
  /** 前 8 位明文（`st_` + 6）；**明文本体从不落库/从不返回**（FR-ADMIN-10）。 */
  token_prefix: string;
  scopes: string[];
  note: string | null;
  created_by_id: number | null;
  created_at: string;
  expires_at: string | null;
  revoked_at: string | null;
  last_used_at: string | null;
  last_used_ip: string | null;
}

export const MOCK_TOKENS: MockTokenRecord[] = [
  {
    id: 1,
    name: "CI 发布流水线",
    token_prefix: "st_9xK2mN",
    scopes: ["tools:read"],
    note: "仅用于流水线拉取已发布工具元数据。",
    created_by_id: 1,
    created_at: "2025-02-10T02:00:00Z",
    expires_at: "2027-06-30T02:00:00Z",
    revoked_at: null,
    last_used_at: "2025-03-13T22:10:00Z",
    last_used_ip: "10.20.31.55",
  },
  {
    id: 2,
    name: "临时巡检脚本",
    token_prefix: "st_3pQ7wZ",
    scopes: ["tools:read", "tools:write"],
    note: "巡检脚本临时凭证，已用完吊销。",
    created_by_id: 1,
    created_at: "2025-01-20T02:00:00Z",
    expires_at: null,
    revoked_at: "2025-02-01T02:00:00Z",
    last_used_at: "2025-01-31T09:00:00Z",
    last_used_ip: "10.20.32.18",
  },
];

/* -------------------------------------------------------------------------- */
/* Approval history & whitelist seed                                          */
/* -------------------------------------------------------------------------- */

export interface MockApprovalRecord {
  id: number;
  tool_id: number;
  tool_name: string | null;
  tool_slug: string | null;
  version_id: number | null;
  version: string | null;
  action:
    | "submit"
    | "withdraw"
    | "resubmit"
    | "approve"
    | "reject"
    | "offline"
    | "relist"
    | "purge_version"
    | "transfer_owner";
  from_status: string | null;
  to_status: string;
  version_from_status: string | null;
  version_to_status: string | null;
  actor_id: number | null;
  actor_label: string;
  is_automatic: boolean;
  auto_rule: string | null;
  reason: string | null;
  note: string | null;
  created_at: string;
}

/**
 * Approval history for the demo scenarios. Besides the hand-written decisions
 * (approve / reject / offline / relist) it synthesises one `submit` record per
 * queued tool, so `/admin/approvals/history?tool_id=…` is never empty for a tool
 * that is sitting in the queue.
 */
export function buildInitialApprovalRecords(
  toolRecords: MockToolRecord[],
): MockApprovalRecord[] {
  const byId = new Map(toolRecords.map((record) => [record.seed.id, record]));

  function describe(toolId: number): { name: string | null; slug: string | null } {
    const record = byId.get(toolId);
    return { name: record?.seed.name ?? null, slug: record?.seed.slug ?? null };
  }

  function make(
    id: number,
    toolId: number,
    action: MockApprovalRecord["action"],
    toStatus: string,
    createdAt: string,
    extra: Partial<MockApprovalRecord> = {},
  ): MockApprovalRecord {
    const { name, slug } = describe(toolId);
    return {
      id,
      tool_id: toolId,
      tool_name: name,
      tool_slug: slug,
      version_id: null,
      version: null,
      action,
      from_status: null,
      to_status: toStatus,
      version_from_status: null,
      version_to_status: null,
      actor_id: 1,
      actor_label: "管理员",
      is_automatic: false,
      auto_rule: null,
      reason: null,
      note: null,
      created_at: createdAt,
      ...extra,
    };
  }

  const records: MockApprovalRecord[] = [
    make(1, 203, "approve", "approved", "2025-03-01T02:30:00Z", {
      from_status: "pending",
      version: "1.0.0",
      version_from_status: "pending",
      version_to_status: "approved",
      note: "已核对提示词内容与示例。",
    }),
    make(2, 204, "reject", "rejected", "2025-03-03T06:10:00Z", {
      from_status: "pending",
      version: "0.2.0",
      version_from_status: "pending",
      version_to_status: "rejected",
      reason: "包内 scripts/build.sh 引用了已下线的内部制品库地址，请更新后重新提交。",
    }),
    make(3, 206, "offline", "offline", "2025-03-05T09:45:00Z", {
      from_status: "approved",
      version: "0.4.0",
      version_from_status: "approved",
      version_to_status: "approved",
      reason: "接口已全部下线，为避免误导新同学，暂时下架归档。",
    }),
    make(4, 206, "relist", "offline", "2025-03-13T08:00:00Z", {
      from_status: "offline",
      version: "0.4.0",
      version_from_status: "approved",
      version_to_status: "approved",
      reason: "复核后仍保持下架。",
    }),
  ];

  // One `submit` record per queued submission (new tool or new version).
  let nextId = 100;
  for (const record of toolRecords) {
    if (record.status !== "pending" && record.status !== "pending_update") continue;
    if (!record.submitted_at) continue;
    const pendingVersion = record.versions.find((version) => version.status === "pending");
    const owner = findMockUserById(ownerIdOf(record));
    records.push(
      make(nextId, record.seed.id, "submit", record.status, record.submitted_at, {
        from_status: record.status === "pending" ? "draft" : "approved",
        version_id: pendingVersion?.id ?? null,
        version: pendingVersion?.version ?? null,
        version_from_status: null,
        version_to_status: "pending",
        actor_id: owner?.id ?? null,
        actor_label: owner?.display_name ?? "未知用户",
        note: record.submission_type === "new_version" ? "版本更新，等待审批。" : "首次提交，等待审批。",
      }),
    );
    nextId += 1;
  }

  records.sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id - b.id);
  return records;
}

/** Owner id behind a record's seed username (falls back to the superadmin). */
function ownerIdOf(record: MockToolRecord): number {
  return MOCK_USERS.find((user) => user.username === record.seed.owner_username)?.id ?? 1;
}

export interface MockWhitelistEntry {
  user_id: number;
  username: string | null;
  display_name: string | null;
  reason: string | null;
  added_by_id: number | null;
  added_by_name: string | null;
  expires_at: string | null;
  created_at: string | null;
  is_effective: boolean;
}

export function buildInitialWhitelist(): MockWhitelistEntry[] {
  return [
    {
      user_id: 3,
      username: "zhangsan",
      display_name: "张三",
      reason: "核心维护者，历史提交质量稳定。",
      added_by_id: 1,
      added_by_name: "管理员",
      expires_at: null,
      created_at: "2025-02-18T02:00:00Z",
      is_effective: true,
    },
  ];
}

/* -------------------------------------------------------------------------- */
/* Settings seed (docs/03 §3.13 — M2 approval group)                          */
/* -------------------------------------------------------------------------- */

export interface MockSettingSeed {
  key: string;
  value: unknown;
  value_type: "bool" | "int" | "string" | "json" | "list";
  is_public: boolean;
  description: string | null;
  options: string[] | null;
  min: number | null;
  max: number | null;
  updated_at: string | null;
}

export interface MockSettingRecord extends MockSettingSeed {
  updated_by_id: number | null;
}

/**
 * The full system-setting catalogue — the first three keys are what the M2
 * approval page drives, the rest are the M3 groups (docs/04 §6.17).
 *
 * Values/types/`is_public`/descriptions come from the backend
 * `SETTING_DEFAULTS`, and `min`/`max`/`options` from `SETTING_SPECS`
 * (`app/repositories/system_settings.py`). The frontend renders its controls
 * from exactly this metadata, so the ranges must be data-driven (验收 #14).
 *
 * `upload.max_images` / `quota.alert_percent` / `stats.download_retention_days`
 * are kept as aliases of the backend's `upload.max_screenshots` /
 * `quota.warn_threshold_pct` / `stats.download_log_retention_days` so both the
 * contract key list and the backend catalogue resolve.
 */
export const MOCK_SETTINGS: MockSettingRecord[] = [
  {
    key: "approval.mode",
    value: "require",
    value_type: "string",
    is_public: false,
    description: "审批模式：全部需人工审批，或全部自动放行。",
    options: ["require", "auto_approve_all"],
    min: null,
    max: null,
    updated_at: "2025-03-01T10:00:00Z",
    updated_by_id: 1,
  },
  {
    key: "approval.whitelist_enabled",
    value: true,
    value_type: "bool",
    is_public: false,
    description: "免审白名单总开关。",
    options: null,
    min: null,
    max: null,
    updated_at: "2025-03-01T10:00:00Z",
    updated_by_id: 1,
  },
  {
    key: "approval.version_reapproval",
    value: true,
    value_type: "bool",
    is_public: false,
    description: "已发布工具发新版本是否需再审。",
    options: null,
    min: null,
    max: null,
    updated_at: "2025-03-01T10:00:00Z",
    updated_by_id: 1,
  },
  {
    key: "images.signature_ttl_hours",
    value: 168,
    value_type: "int",
    is_public: false,
    description: "图片签名 URL 有效期（小时），默认 7 天。",
    options: null,
    min: 1,
    max: 8760,
    updated_at: "2025-03-01T10:00:00Z",
    updated_by_id: 1,
  },
  {
    key: "version.history_limit",
    value: 10,
    value_type: "int",
    is_public: false,
    description: "历史版本保留份数。",
    options: null,
    min: 1,
    max: 50,
    updated_at: "2025-02-18T10:00:00Z",
    updated_by_id: 1,
  },
  {
    key: "upload.max_file_size_mb",
    value: 200,
    value_type: "int",
    is_public: false,
    description: "单文件上传上限（MB）。",
    options: null,
    min: 1,
    max: 2048,
    updated_at: "2025-02-18T10:00:00Z",
    updated_by_id: 1,
  },
  {
    key: "upload.allowed_extensions",
    value: [
      "zip",
      "tar.gz",
      "tgz",
      "whl",
      "tar",
      "gz",
      "7z",
      "rar",
      "exe",
      "msi",
      "deb",
      "rpm",
      "sh",
      "py",
      "md",
      "txt",
      "json",
      "yaml",
      "pdf",
      "png",
      "jpg",
    ],
    value_type: "list",
    is_public: false,
    description: "允许上传的扩展名白名单。",
    options: null,
    min: null,
    max: null,
    updated_at: "2025-02-18T10:00:00Z",
    updated_by_id: 1,
  },
  {
    key: "upload.max_screenshots",
    value: 8,
    value_type: "int",
    is_public: false,
    description: "每个工具最多截图数。",
    options: null,
    min: 0,
    max: 20,
    updated_at: "2025-02-18T10:00:00Z",
    updated_by_id: 1,
  },
  {
    key: "upload.max_images",
    value: 8,
    value_type: "int",
    is_public: false,
    description: "每个工具最多图片数（`upload.max_screenshots` 的契约别名）。",
    options: null,
    min: 0,
    max: 20,
    updated_at: "2025-02-18T10:00:00Z",
    updated_by_id: 1,
  },
  {
    key: "upload.max_tags",
    value: 8,
    value_type: "int",
    is_public: false,
    description: "单个工具最多标签数。",
    options: null,
    min: 1,
    max: 20,
    updated_at: "2025-02-18T10:00:00Z",
    updated_by_id: 1,
  },
  {
    key: "quota.per_user_mb",
    value: 2048,
    value_type: "int",
    is_public: false,
    description: "单用户配额（MB），0 = 不限。",
    options: null,
    min: 0,
    max: 1_048_576,
    updated_at: "2025-02-18T10:00:00Z",
    updated_by_id: 1,
  },
  {
    key: "quota.total_mb",
    value: 51_200,
    value_type: "int",
    is_public: false,
    description: "平台总配额（MB），0 = 不限。",
    options: null,
    min: 0,
    max: 1_048_576,
    updated_at: "2025-02-18T10:00:00Z",
    updated_by_id: 1,
  },
  {
    key: "quota.warn_threshold_pct",
    value: 85,
    value_type: "int",
    is_public: false,
    description: "配额告警阈值百分比。",
    options: null,
    min: 1,
    max: 100,
    updated_at: "2025-02-18T10:00:00Z",
    updated_by_id: 1,
  },
  {
    key: "quota.alert_percent",
    value: 85,
    value_type: "int",
    is_public: false,
    description: "配额告警阈值百分比（`quota.warn_threshold_pct` 的契约别名）。",
    options: null,
    min: 1,
    max: 100,
    updated_at: "2025-02-18T10:00:00Z",
    updated_by_id: 1,
  },
  {
    key: "security.access_token_minutes",
    value: 30,
    value_type: "int",
    is_public: false,
    description: "access token 有效期（分钟）。",
    options: null,
    min: 1,
    max: 1440,
    updated_at: "2025-02-18T10:00:00Z",
    updated_by_id: 1,
  },
  {
    key: "security.refresh_token_days",
    value: 7,
    value_type: "int",
    is_public: false,
    description: "refresh token 有效期（天）。",
    options: null,
    min: 1,
    max: 365,
    updated_at: "2025-02-18T10:00:00Z",
    updated_by_id: 1,
  },
  {
    key: "security.login_max_failures",
    value: 5,
    value_type: "int",
    is_public: false,
    description: "连续登录失败锁定阈值。",
    options: null,
    min: 1,
    max: 20,
    updated_at: "2025-02-18T10:00:00Z",
    updated_by_id: 1,
  },
  {
    key: "security.lockout_minutes",
    value: 15,
    value_type: "int",
    is_public: false,
    description: "锁定时长（分钟）。",
    options: null,
    min: 1,
    max: 1440,
    updated_at: "2025-02-18T10:00:00Z",
    updated_by_id: 1,
  },
  {
    key: "portal.site_name",
    value: "工具与 Skill 平台",
    value_type: "string",
    is_public: true,
    description: "站点名称。",
    options: null,
    min: null,
    max: null,
    updated_at: "2025-03-01T10:00:00Z",
    updated_by_id: 1,
  },
  {
    key: "portal.announcement_md",
    value: "**本周五 20:00** 进行例行维护，期间门户只读。",
    value_type: "string",
    is_public: true,
    description: "首页公告（Markdown）。",
    options: null,
    min: null,
    max: null,
    updated_at: "2025-03-01T10:00:00Z",
    updated_by_id: 1,
  },
  {
    key: "portal.allow_anonymous_view",
    value: true,
    value_type: "bool",
    is_public: true,
    description: "是否允许未登录浏览门户。",
    options: null,
    min: null,
    max: null,
    updated_at: "2025-03-01T10:00:00Z",
    updated_by_id: 1,
  },
  {
    key: "portal.allow_admin_view_private",
    value: true,
    value_type: "bool",
    is_public: false,
    description: "超管是否可见他人 private 工具。",
    options: null,
    min: null,
    max: null,
    updated_at: "2025-03-01T10:00:00Z",
    updated_by_id: 1,
  },
  {
    key: "portal.default_sort",
    value: "hot",
    value_type: "string",
    is_public: true,
    description: "门户默认排序。",
    options: ["hot", "new", "name"],
    min: null,
    max: null,
    updated_at: "2025-03-01T10:00:00Z",
    updated_by_id: 1,
  },
  {
    key: "portal.page_size",
    value: 24,
    value_type: "int",
    is_public: true,
    description: "门户每页条数。",
    options: null,
    min: 1,
    max: 200,
    updated_at: "2025-03-01T10:00:00Z",
    updated_by_id: 1,
  },
  {
    key: "stats.download_log_retention_days",
    value: 180,
    value_type: "int",
    is_public: false,
    description: "下载明细保留天数。",
    options: null,
    min: 1,
    max: 3650,
    updated_at: "2025-02-18T10:00:00Z",
    updated_by_id: 1,
  },
  {
    key: "stats.download_retention_days",
    value: 180,
    value_type: "int",
    is_public: false,
    description: "下载明细保留天数（`stats.download_log_retention_days` 的契约别名）。",
    options: null,
    min: 1,
    max: 3650,
    updated_at: "2025-02-18T10:00:00Z",
    updated_by_id: 1,
  },
  {
    key: "stats.view_dedup_minutes",
    value: 60,
    value_type: "int",
    is_public: false,
    description: "浏览去重窗口（分钟）。",
    options: null,
    min: 0,
    max: 1440,
    updated_at: "2025-02-18T10:00:00Z",
    updated_by_id: 1,
  },
  {
    key: "webapp.health_check_enabled",
    value: false,
    value_type: "bool",
    is_public: false,
    description: "在线工具探活（二期）。",
    options: null,
    min: null,
    max: null,
    updated_at: "2025-02-18T10:00:00Z",
    updated_by_id: 1,
  },
  {
    key: "api.docs_enabled",
    value: false,
    value_type: "bool",
    is_public: false,
    description: "是否开放 /docs。",
    options: null,
    min: null,
    max: null,
    updated_at: "2025-02-18T10:00:00Z",
    updated_by_id: 1,
  },
];

/* -------------------------------------------------------------------------- */
/* Download-history seed                                                      */
/* -------------------------------------------------------------------------- */

/**
 * Download history rows. `version_id` is left null here and filled in by
 * `handlers.ts` (the version ids are assigned while the tool records are built,
 * after this seed data is declared).
 */
export function buildInitialDownloadLogs(): MockDownloadLog[] {
  const seeds: Array<[number, number, string]> = [
    // [tool_id, user_id, created_at]
    [1, 1, "2025-03-12T08:20:00Z"],
    [4, 1, "2025-03-11T02:40:00Z"],
    [7, 1, "2025-03-10T06:15:00Z"],
    [1, 3, "2025-03-09T01:30:00Z"],
    [8, 2, "2025-03-08T09:05:00Z"],
    [3, 1, "2025-03-07T04:25:00Z"],
  ];
  return seeds.map(([toolId, userId, createdAt], index) => {
    const seed = findToolSeed(toolId);
    return {
      id: 3_000 + index,
      user_id: userId,
      tool_id: toolId,
      version_id: null,
      file_name: seed ? versionFileName(seed, seed.current_version, extForSeed(seed)) : null,
      file_size: seed?.file_size ?? null,
      created_at: createdAt,
    };
  });
}

/** Exported for the mock self-checks (seed spec conformance). */
export const MOCK_SEED_NOTES = {
  longToolNameLength: LONG_TOOL_NAME.length,
  toolsWithoutCover: MOCK_TOOLS.filter((tool) => tool.cover_url === null).length,
  totalTools: MOCK_TOOLS.length,
  totalUsers: MOCK_USERS.length,
  totalCategories: MOCK_CATEGORIES.length,
  waitingHours: MOCK_WAITING_HOURS,
};
