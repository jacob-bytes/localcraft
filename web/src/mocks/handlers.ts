import { delay, http, HttpResponse } from "msw";

import type {
  ErrorDetails,
  Meta,
  ToolFacets,
  ToolListResponse,
  ToolListItem,
  ToolSort,
  ToolType,
  TokenPair,
  User,
} from "@/api/types";
import {
  countByCategory,
  countByType,
  LONG_TOOL_NAME,
  MOCK_CATEGORIES,
  MOCK_TAGS,
  MOCK_TOOL_BODIES,
  MOCK_TOOLS,
  MOCK_USERS,
  type MockUser,
} from "./data";

/**
 * MSW handlers for the 12 M1 endpoints in CONTRACT §6 (plus the image endpoint
 * the seeded `cover_url`s point at).
 *
 * The mock is stateful on purpose, so the real auth flow is exercised:
 *   - business endpoints require a valid bearer token
 *   - the access token is only ever held in memory, so an F5 wipes it and the
 *     next request 401s with TOKEN_EXPIRED → the client silently refreshes
 *   - the refresh token is a real cookie (path `/api/v1/auth`), which is how it
 *     survives an F5 the same way an HttpOnly cookie would. Sessions live in
 *     localStorage because a mock has no server-side session table.
 * The access token is NEVER written to storage (CONTRACT §3.1).
 */

const API = "/api/v1";
const ACCESS_TTL_MS = 30 * 60 * 1000;
const LOCK_THRESHOLD = 5;
const LOCK_SECONDS = 15 * 60;
const REFRESH_COOKIE = "refresh_token";
/**
 * The real backend scopes the refresh cookie to `Path=/api/v1/auth` and marks it
 * HttpOnly (CONTRACT §2/§3.2). A service-worker mock has no HttpOnly storage, and
 * a JS-set cookie whose path is not a prefix of the document path is invisible to
 * `document.cookie`. The mock therefore re-reads it through `path=/`; the
 * frontend behaviour under test (restore after F5, revoke on logout) is identical.
 */
const REFRESH_COOKIE_PATH = "/";
const SESSION_STORAGE_KEY = "selftool.msw.sessions";
const CREDENTIALS_STORAGE_KEY = "selftool.msw.credentials";
const SIMULATED_LATENCY_MS = 150;

/* -------------------------------------------------------------------------- */
/* State                                                                      */
/* -------------------------------------------------------------------------- */

type SessionMap = Record<string, string>;

function loadSessions(): SessionMap {
  try {
    const raw = window.localStorage.getItem(SESSION_STORAGE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (parsed && typeof parsed === "object") return parsed as SessionMap;
  } catch {
    /* ignore */
  }
  return {};
}

function saveSessions(next: SessionMap): void {
  sessions = next;
  try {
    window.localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(next));
  } catch {
    /* ignore */
  }
}

interface CredentialOverride {
  password: string;
  must_change_password: boolean;
}

function loadCredentialOverrides(): Record<string, CredentialOverride> {
  try {
    const raw = window.localStorage.getItem(CREDENTIALS_STORAGE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (parsed && typeof parsed === "object") {
      return parsed as Record<string, CredentialOverride>;
    }
  } catch {
    /* ignore */
  }
  return {};
}

function saveCredentialOverride(username: string, override: CredentialOverride): void {
  try {
    window.localStorage.setItem(
      CREDENTIALS_STORAGE_KEY,
      JSON.stringify({ ...loadCredentialOverrides(), [username]: override }),
    );
  } catch {
    /* ignore */
  }
}

let sessions: SessionMap = loadSessions();

/* Re-apply password changes so "改密 → 用新密码登录" also works after an F5:
   a browser mock has no database, so the override is kept in localStorage. */
for (const [username, override] of Object.entries(loadCredentialOverrides())) {
  const user = MOCK_USERS.find((item) => item.username === username);
  if (user) {
    user.password = override.password;
    user.must_change_password = override.must_change_password;
  }
}

/** access token → { username, expiresAt }; memory only, cleared by F5. */
const accessTokens = new Map<string, { username: string; expiresAt: number }>();
const failedLogins = new Map<string, number>();
const lockedUntil = new Map<string, number>();

/* -------------------------------------------------------------------------- */
/* Helpers                                                                    */
/* -------------------------------------------------------------------------- */

function newRequestId(): string {
  return crypto.randomUUID().replace(/-/g, "").slice(0, 26).toUpperCase();
}

function errorResponse(
  status: number,
  code: string,
  message: string,
  details: ErrorDetails | null = null,
) {
  const requestId = newRequestId();
  return HttpResponse.json(
    { code, message, details, request_id: requestId },
    { status, headers: { "X-Request-Id": requestId } },
  );
}

function validationError(fields: Array<{ field: string; message: string }>) {
  return errorResponse(400, "VALIDATION_ERROR", "请求参数校验失败", { fields });
}

function toUserDto(user: MockUser): User {
  return {
    id: user.id,
    username: user.username,
    display_name: user.display_name,
    email: user.email,
    roles: [...user.roles],
    permissions: [...user.permissions],
    must_change_password: user.must_change_password,
    auth_source: user.auth_source,
  };
}

function findUser(username: string): MockUser | undefined {
  const normalized = username.trim().toLowerCase();
  return MOCK_USERS.find((user) => user.username === normalized);
}

function issueAccessToken(username: string): string {
  const token = `mock.${crypto.randomUUID().replace(/-/g, "")}`;
  accessTokens.set(token, { username, expiresAt: Date.now() + ACCESS_TTL_MS });
  return token;
}

function issueRefreshToken(username: string): string {
  const token = crypto.randomUUID().replace(/-/g, "");
  saveSessions({ ...sessions, [token]: username });
  return token;
}

function readRefreshCookie(): string | null {
  const match = document.cookie.match(/(?:^|;\s*)refresh_token=([^;]+)/);
  const raw = match?.[1];
  if (!raw) return null;
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

function writeRefreshCookie(token: string): void {
  // Non-HttpOnly because a service-worker mock runs inside the page; everything
  // else (name, path, max-age, SameSite=Lax) mirrors CONTRACT §3.2.
  document.cookie = `${REFRESH_COOKIE}=${encodeURIComponent(token)}; path=${REFRESH_COOKIE_PATH}; max-age=604800; samesite=lax`;
}

function clearRefreshCookie(): void {
  document.cookie = `${REFRESH_COOKIE}=; path=${REFRESH_COOKIE_PATH}; max-age=0; samesite=lax`;
}

/** Returns the authenticated user, or null when the bearer token is unusable. */
function authenticate(request: Request): MockUser | null {
  const header = request.headers.get("Authorization");
  if (!header || !header.startsWith("Bearer ")) return null;
  const token = header.slice("Bearer ".length);
  const entry = accessTokens.get(token);
  if (!entry) return null;
  if (entry.expiresAt <= Date.now()) {
    accessTokens.delete(token);
    return null;
  }
  const user = findUser(entry.username);
  if (!user || user.status !== "active") return null;
  return user;
}

/** 401 TOKEN_EXPIRED — what the client turns into a silent refresh + replay. */
function tokenExpired() {
  return errorResponse(401, "TOKEN_EXPIRED", "登录状态已过期，请重新登录");
}

function toolMatchesQuery(tool: ToolListItem, keyword: string): boolean {
  const needle = keyword.trim().toLowerCase();
  if (!needle) return true;
  const body = MOCK_TOOL_BODIES[tool.id] ?? "";
  return (
    tool.name.toLowerCase().includes(needle) ||
    tool.summary.toLowerCase().includes(needle) ||
    body.toLowerCase().includes(needle) ||
    tool.tags.some((tag) => tag.toLowerCase().includes(needle))
  );
}

function sortTools(tools: ToolListItem[], sort: ToolSort): ToolListItem[] {
  const sorted = [...tools];
  switch (sort) {
    case "new":
      sorted.sort((a, b) => (b.published_at ?? "").localeCompare(a.published_at ?? ""));
      break;
    case "name":
      sorted.sort((a, b) => a.name.localeCompare(b.name, "zh-Hans-CN"));
      break;
    case "-updated_at":
      sorted.sort((a, b) => b.updated_at.localeCompare(a.updated_at));
      break;
    case "hot":
    default:
      sorted.sort((a, b) => b.download_count - a.download_count);
      break;
  }
  return sorted;
}

function buildFacets(): ToolFacets {
  const categories = countByCategory();
  const types = countByType();
  return {
    categories: MOCK_CATEGORIES.filter((category) => category.is_active).map((category) => ({
      slug: category.slug,
      name: category.name,
      count: categories[category.slug] ?? 0,
    })),
    types: (Object.keys(types) as ToolType[]).map((value) => ({
      value,
      count: types[value],
    })),
  };
}

const XML_ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&apos;",
};

function escapeXml(value: string): string {
  return value.replace(/[&<>"']/g, (char) => XML_ESCAPES[char] ?? char);
}

const COVER_TINTS: Record<string, [string, string]> = {
  "dev-tools": ["#c7d2fe", "#a5b4fc"],
  "ops-tools": ["#99f6e4", "#5eead4"],
  skill: ["#ddd6fe", "#c4b5fd"],
  prompt: ["#fde68a", "#fcd34d"],
};

/* -------------------------------------------------------------------------- */
/* Handlers                                                                   */
/* -------------------------------------------------------------------------- */

export const handlers = [
  /* ---- probes ---- */
  http.get("/healthz", () => HttpResponse.json({ status: "ok" })),
  http.get("/readyz", () =>
    HttpResponse.json({ status: "ok", database: "ok", storage: "ok" }),
  ),

  /* ---- meta ---- */
  http.get(`${API}/meta`, async () => {
    await delay(30);
    const meta: Meta = {
      site_name: "工具与 Skill 平台",
      announcement_md: "**本周五 20:00** 进行例行维护，期间门户只读。",
      auth_provider: "local",
      allow_anonymous_view: false,
      default_sort: "hot",
      page_size: 24,
      app_version: "1.0.0",
      api_version: "v1",
      features: { webapp_health_check: false, skill_preview: true },
    };
    return HttpResponse.json(meta);
  }),

  /* ---- auth ---- */
  http.get(`${API}/auth/provider`, async () => {
    await delay(20);
    return HttpResponse.json({
      provider: "local",
      display_name: "本地账号",
      login_fields: ["username", "password"],
    });
  }),

  http.post(`${API}/auth/login`, async ({ request }) => {
    await delay(SIMULATED_LATENCY_MS);
    const body = (await request.json()) as { username?: string; password?: string };
    const username = (body.username ?? "").trim().toLowerCase();
    const password = body.password ?? "";

    if (!username || !password) {
      return validationError([
        ...(username ? [] : [{ field: "username", message: "请输入用户名" }]),
        ...(password ? [] : [{ field: "password", message: "请输入密码" }]),
      ]);
    }

    const user = findUser(username);
    if (!user) {
      return errorResponse(401, "INVALID_CREDENTIALS", "用户名或密码错误");
    }

    const locked = lockedUntil.get(user.username) ?? 0;
    if (locked > Date.now()) {
      const retryAfter = Math.ceil((locked - Date.now()) / 1000);
      return errorResponse(423, "ACCOUNT_LOCKED", "账号已锁定，请稍后重试", {
        retry_after_seconds: retryAfter,
      });
    }

    if (user.status !== "active") {
      return errorResponse(403, "ACCOUNT_DISABLED", "账号已禁用，请联系管理员");
    }

    if (user.password !== password) {
      const failures = (failedLogins.get(user.username) ?? 0) + 1;
      failedLogins.set(user.username, failures);
      if (failures >= LOCK_THRESHOLD) {
        lockedUntil.set(user.username, Date.now() + LOCK_SECONDS * 1000);
      }
      // Same code and message for unknown user and wrong password (CONTRACT §3.3).
      return errorResponse(401, "INVALID_CREDENTIALS", "用户名或密码错误");
    }

    failedLogins.delete(user.username);
    lockedUntil.delete(user.username);

    const refreshToken = issueRefreshToken(user.username);
    writeRefreshCookie(refreshToken);

    const pair: TokenPair = {
      access_token: issueAccessToken(user.username),
      token_type: "bearer",
      expires_in: Math.floor(ACCESS_TTL_MS / 1000),
      user: toUserDto(user),
    };
    return HttpResponse.json(pair);
  }),

  http.post(`${API}/auth/refresh`, async () => {
    await delay(60);
    const refreshToken = readRefreshCookie();
    const username = refreshToken ? sessions[refreshToken] : undefined;
    const user = username ? findUser(username) : undefined;
    if (!user || user.status !== "active") {
      clearRefreshCookie();
      return errorResponse(401, "UNAUTHENTICATED", "登录状态已失效，请重新登录");
    }
    const pair: TokenPair = {
      access_token: issueAccessToken(user.username),
      token_type: "bearer",
      expires_in: Math.floor(ACCESS_TTL_MS / 1000),
      user: toUserDto(user),
    };
    return HttpResponse.json(pair);
  }),

  http.post(`${API}/auth/logout`, async ({ request }) => {
    await delay(40);
    const refreshToken = readRefreshCookie();
    if (refreshToken) {
      const next = { ...sessions };
      delete next[refreshToken];
      saveSessions(next);
    }
    clearRefreshCookie();
    // Invalidate the in-flight access token too.
    const header = request.headers.get("Authorization");
    if (header?.startsWith("Bearer ")) accessTokens.delete(header.slice(7));
    return new HttpResponse(null, { status: 204 });
  }),

  http.get(`${API}/auth/me`, async ({ request }) => {
    await delay(30);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    return HttpResponse.json(toUserDto(user));
  }),

  http.post(`${API}/auth/change-password`, async ({ request }) => {
    await delay(SIMULATED_LATENCY_MS);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    const body = (await request.json()) as { old_password?: string; new_password?: string };
    const oldPassword = body.old_password ?? "";
    const newPassword = body.new_password ?? "";

    if (user.password !== oldPassword) {
      return validationError([{ field: "old_password", message: "原密码不正确" }]);
    }
    if (newPassword.length < 10) {
      return validationError([{ field: "new_password", message: "新密码至少 10 个字符" }]);
    }
    const classes =
      (/[a-z]/.test(newPassword) ? 1 : 0) +
      (/[A-Z]/.test(newPassword) ? 1 : 0) +
      (/[0-9]/.test(newPassword) ? 1 : 0) +
      (/[^A-Za-z0-9]/.test(newPassword) ? 1 : 0);
    if (classes < 3) {
      return validationError([
        {
          field: "new_password",
          message: "需包含大写字母、小写字母、数字、符号中的至少 3 类",
        },
      ]);
    }
    if (newPassword === oldPassword) {
      return validationError([{ field: "new_password", message: "新密码不能与原密码相同" }]);
    }

    user.password = newPassword;
    user.must_change_password = false;
    saveCredentialOverride(user.username, {
      password: newPassword,
      must_change_password: false,
    });

    // Changing the password revokes every session for that user, including this
    // one (CONTRACT §3.2 ⑥) — the client then forces a fresh login.
    const next: SessionMap = {};
    for (const [token, owner] of Object.entries(sessions)) {
      if (owner !== user.username) next[token] = owner;
    }
    saveSessions(next);
    clearRefreshCookie();
    accessTokens.clear();
    return new HttpResponse(null, { status: 204 });
  }),

  /* ---- taxonomy ---- */
  http.get(`${API}/categories`, async () => {
    await delay(60);
    const counts = countByCategory();
    return HttpResponse.json(
      MOCK_CATEGORIES.filter((category) => category.is_active)
        .sort((a, b) => a.sort_order - b.sort_order)
        .map((category) => ({ ...category, tool_count: counts[category.slug] ?? 0 })),
    );
  }),

  http.get(`${API}/tags`, async ({ request }) => {
    await delay(60);
    const q = (new URL(request.url).searchParams.get("q") ?? "").trim().toLowerCase();
    const tags = MOCK_TAGS.filter(
      (tag) => !q || tag.name.startsWith(q) || tag.display_name.toLowerCase().startsWith(q),
    ).sort((a, b) => b.usage_count - a.usage_count || a.name.localeCompare(b.name));
    return HttpResponse.json(tags);
  }),

  /* ---- portal list ---- */
  http.get(`${API}/tools`, async ({ request }) => {
    await delay(SIMULATED_LATENCY_MS);
    const user = authenticate(request);
    if (!user) return tokenExpired();

    const url = new URL(request.url);
    const params = url.searchParams;
    const q = params.get("q") ?? "";
    const categories = params.getAll("category");
    const tags = params.getAll("tag");
    const types = params.getAll("type") as ToolType[];
    const owner = params.get("owner");
    const sort = (params.get("sort") ?? "hot") as ToolSort;
    const page = Math.max(1, Number.parseInt(params.get("page") ?? "1", 10) || 1);
    const pageSize = Math.min(
      200,
      Math.max(1, Number.parseInt(params.get("page_size") ?? "24", 10) || 24),
    );

    let items = MOCK_TOOLS.filter((tool) => tool.visibility === "public" || tool.owner.id === user.id)
      .filter((tool) => toolMatchesQuery(tool, q))
      .filter((tool) => categories.length === 0 || categories.includes(tool.category.slug))
      .filter((tool) => types.length === 0 || types.includes(tool.tool_type))
      .filter((tool) => tags.length === 0 || tags.some((tag) => tool.tags.includes(tag)))
      .filter((tool) => !owner || tool.owner.username === owner);

    items = sortTools(items, sort);

    const total = items.length;
    const pages = Math.ceil(total / pageSize);
    const start = (page - 1) * pageSize;
    const pageItems = items.slice(start, start + pageSize);

    const body: ToolListResponse = {
      items: pageItems.map((tool) => ({
        ...tool,
        has_pending_version: false,
        can_download: user.roles.includes("viewer") ? false : true,
      })),
      total,
      page,
      page_size: pageSize,
      pages,
      // Key always present: object on page 1, null when paging (docs/03 §3.3).
      facets: page === 1 ? buildFacets() : null,
    };

    return HttpResponse.json(body);
  }),

  /* ---- images (referenced by cover_url) ---- */
  http.get(`${API}/images/:id`, async ({ params }) => {
    await delay(40);
    const id = Number.parseInt(String(params.id), 10);
    const tool = MOCK_TOOLS.find((item) => item.id === id);
    if (!tool) return errorResponse(404, "NOT_FOUND", "图片不存在");
    const [from, to] = COVER_TINTS[tool.category.slug] ?? ["#e2e8f0", "#cbd5e1"];
    const label = escapeXml(tool.name.slice(0, 12));
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="480" height="270" viewBox="0 0 480 270" role="img" aria-label="${escapeXml(tool.name)}">
  <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="${from}"/><stop offset="100%" stop-color="${to}"/>
  </linearGradient></defs>
  <rect width="480" height="270" fill="url(#g)"/>
  <text x="50%" y="50%" text-anchor="middle" dominant-baseline="middle"
        font-family="PingFang SC, Microsoft YaHei, sans-serif" font-size="28" fill="#1e293b">${label}</text>
</svg>`;
    return new HttpResponse(svg, {
      headers: { "Content-Type": "image/svg+xml", "Cache-Control": "no-store" },
    });
  }),
];

/** Exported for the mock self-checks (seed spec conformance). */
export const MOCK_SEED_NOTES = {
  longToolNameLength: LONG_TOOL_NAME.length,
  toolsWithoutCover: MOCK_TOOLS.filter((tool) => tool.cover_url === null).length,
  totalTools: MOCK_TOOLS.length,
};
