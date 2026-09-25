import type {
  Category,
  Role,
  Tag,
  ToolListItem,
  ToolType,
  Visibility,
} from "@/api/types";

/**
 * MSW seed data — built to the CONTRACT §7 specification so the frontend can be
 * developed and demoed without the backend:
 *
 *  - 1 superadmin `admin` / `Admin@12345` (must_change_password = false)
 *  - 1 forced-password-change user `newbie` / `Newbie@12345`
 *  - 4 categories: 研发工具 / 运维工具 / Skill / 提示词
 *  - 8 approved, public tools covering all 4 types across all 4 categories,
 *    each with a current version and several tags
 *  - exactly 2 tools without a cover (placeholder block + type icon)
 *  - 1 tool whose name is > 40 characters and whose summary is > 3 lines
 *  - fixed download/view counts and timestamps → the seed is deterministic
 *
 * Mock-only extras (documented in the checkpoint report): three extra owners so
 * cards show different authors, and generated SVG covers for the image endpoint.
 */

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
  email: string | null = null,
): MockUser {
  return {
    id,
    username,
    password,
    display_name,
    email: email ?? `${username}@example.com`,
    roles,
    permissions: permissionsFor(roles),
    must_change_password,
    auth_source: "local",
    status: "active",
  };
}

export const MOCK_USERS: MockUser[] = [
  makeUser(1, "admin", "Admin@12345", "管理员", ["superadmin"]),
  makeUser(2, "newbie", "Newbie@12345", "新同学", ["user"], true),
  makeUser(3, "zhangsan", "Zhang@12345", "张三", ["user"]),
  makeUser(4, "lisi", "Lisi@12345", "李四", ["user"]),
  makeUser(5, "wangwu", "Wang@12345", "王五", ["approver"]),
];

export const MOCK_CATEGORIES: Category[] = [
  {
    id: 1,
    slug: "dev-tools",
    name: "研发工具",
    description: "编码、构建、测试相关的效率工具",
    icon: "wrench",
    sort_order: 10,
    is_active: true,
    tool_count: 0,
  },
  {
    id: 2,
    slug: "ops-tools",
    name: "运维工具",
    description: "部署、巡检、监控相关的运维脚本与工具",
    icon: "server",
    sort_order: 20,
    is_active: true,
    tool_count: 0,
  },
  {
    id: 3,
    slug: "skill",
    name: "Skill",
    description: "可被 AI 助手调用的 Skill 包",
    icon: "sparkles",
    sort_order: 30,
    is_active: true,
    tool_count: 0,
  },
  {
    id: 4,
    slug: "prompt",
    name: "提示词",
    description: "沉淀下来的高质量提示词",
    icon: "message-square",
    sort_order: 40,
    is_active: true,
    tool_count: 0,
  },
];

export const MOCK_TAGS: Tag[] = [
  { id: 1, name: "python", display_name: "python", usage_count: 3 },
  { id: 2, name: "log", display_name: "log", usage_count: 2 },
  { id: 3, name: "ops", display_name: "ops", usage_count: 4 },
  { id: 4, name: "review", display_name: "review", usage_count: 2 },
  { id: 5, name: "shell", display_name: "shell", usage_count: 2 },
  { id: 6, name: "k8s", display_name: "k8s", usage_count: 3 },
  { id: 7, name: "deploy", display_name: "deploy", usage_count: 2 },
  { id: 8, name: "sql", display_name: "sql", usage_count: 1 },
  { id: 9, name: "prompt", display_name: "prompt", usage_count: 2 },
  { id: 10, name: "architecture", display_name: "architecture", usage_count: 1 },
  { id: 11, name: "fastapi", display_name: "fastapi", usage_count: 1 },
  { id: 12, name: "internal", display_name: "internal", usage_count: 1 },
];

interface ToolSeed {
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
}

/** Name is deliberately > 40 characters (CONTRACT §7 truncation check). */
export const LONG_TOOL_NAME =
  "统一日志采集与多集群灰度发布自动回滚一体化运维编排工具集（内部试用版，含完整使用手册与故障排查指南）";

/** Summary is deliberately long enough to exceed 3 rendered lines. */
export const LONG_TOOL_SUMMARY =
  "把日志采集、多集群灰度发布、健康检查与自动回滚串成一条可复用的流水线：先在预发集群按 1% 流量验证，确认指标无异常后逐级放大到 100%，任何一步失败都会自动回滚到上一个稳定版本，并把全过程的关键指标、变更记录与责任人写进审计日志，便于事后复盘与责任追溯。";

const TOOL_SEEDS: ToolSeed[] = [
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
    category_slug: "skill",
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
    category_slug: "prompt",
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
    category_slug: "skill",
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

/** Cover image id = tool id (the mock image endpoint serves a generated SVG). */
export const MOCK_TOOLS: ToolListItem[] = TOOL_SEEDS.map((seed) => {
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
    cover_url: seed.cover ? `/api/v1/images/${seed.id}?variant=thumb` : null,
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
});

/** Searchable body text per tool (`description_md`), kept out of the list DTO. */
export const MOCK_TOOL_BODIES: Record<number, string> = Object.fromEntries(
  TOOL_SEEDS.map((seed) => [seed.id, seed.description_md]),
);

export function countByCategory(): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const tool of MOCK_TOOLS) {
    counts[tool.category.slug] = (counts[tool.category.slug] ?? 0) + 1;
  }
  return counts;
}

export function countByType(): Record<ToolType, number> {
  const counts: Record<ToolType, number> = { file: 0, webapp: 0, skill: 0, prompt: 0 };
  for (const tool of MOCK_TOOLS) counts[tool.tool_type] += 1;
  return counts;
}
