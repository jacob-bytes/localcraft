import { delay, http, HttpResponse } from "msw";

import type {
  AclEntry,
  AclResponse,
  ApprovalQueueItem,
  ApprovalRecord,
  ApproveResponse,
  ApprovalHistoryParams,
  AuthProviderResponse,
  BatchApproveResponse,
  CurrentVersionBrief,
  DownloadLogItem,
  DownloadTicket,
  ErrorDetails,
  FieldError,
  ImagePatchRequest,
  LoginField,
  Meta,
  MyToolListItem,
  Paginated,
  PendingVersionBrief,
  Profile,
  PromptDetailInfo,
  SettingItem,
  SettingListResponse,
  SettingWarning,
  SkillDetailInfo,
  SkillPreview,
  SkillVersionInfo,
  StatusResponse,
  SubmitResponse,
  SupersededVersionBrief,
  ToolDetail,
  ToolFacets,
  ToolImage,
  ToolListItem,
  ToolPermissions,
  ToolSort,
  ToolStats,
  ToolStatus,
  ToolType,
  TokenPair,
  Usage,
  User,
  VersionDetail,
  VersionSummary,
  VersionUploadResponse,
  Visibility,
  WithdrawResponse,
} from "@/api/types";
import {
  buildInitialApprovalRecords,
  buildInitialDownloadLogs,
  buildInitialToolRecords,
  buildInitialWhitelist,
  buildExtraSubmissionRecords,
  buildSkillFileTree,
  countByCategory,
  countByType,
  coverImageId,
  EXTRA_SUBMISSION_SPECS,
  fakeSha256,
  findMockUserById,
  imageUrl,
  LONG_TOOL_NAME,
  MOCK_CATEGORIES,
  MOCK_GROUPS,
  MOCK_SETTINGS,
  MOCK_SKILL_MANIFEST,
  MOCK_SKILL_README,
  MOCK_TAGS,
  MOCK_TOOLS,
  MOCK_USERS,
  MOCK_VERSION_HISTORY_LIMIT,
  skillTreeSummary,
  toolSeedToListItem,
  waitingHoursFor,
  type MockApprovalRecord,
  type MockDownloadLog,
  type MockSettingRecord,
  type MockToolRecord,
  type MockUser,
  type MockVersion,
  type MockWhitelistEntry,
} from "./data";

/**
 * MSW handlers for the M1 endpoints (CONTRACT §6) plus the 38 M2 endpoints
 * (CONTRACT §6.1). Shapes are `src/api/types.ts` verbatim; that file mirrors the
 * backend Pydantic schemas, which CONTRACT §14.2 ruled are the authority.
 *
 * The mock is stateful on purpose, so the real flows are exercised:
 *   - business endpoints require a valid bearer token
 *   - `/me/tools` create → submit → approve/reject, version upload, ACL, images,
 *     approvals, whitelist and settings all mutate in-memory state
 *   - the access token is only ever held in memory, so an F5 wipes it and the
 *     next request 401s with TOKEN_EXPIRED → the client silently refreshes
 *   - the refresh token is a real cookie (path `/`), which is how it survives an
 *     F5 the same way an HttpOnly cookie would. Sessions live in localStorage
 *     because a mock has no server-side session table.
 * The access token is NEVER written to storage (CONTRACT §3.1).
 *
 * Tool/version/approval mutations are deliberately in-memory only: an F5 resets
 * the seed (a page reload keeps its session, see above). This keeps the mock
 * idempotent across Playwright runs, and is documented in the checkpoint report.
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

/** One-time download ticket lifetime (docs/03 §1.10 / §3.15: 60 s). */
const TICKET_TTL_MS = 60 * 1000;
const IMAGE_SIGNATURE = "dev";

/* -------------------------------------------------------------------------- */
/* Auth state (M1, unchanged apart from the 200 + {status:"ok"} bodies)        */
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
/* M2 state (in memory, seeded deterministically)                             */
/* -------------------------------------------------------------------------- */

const toolRecords: MockToolRecord[] = [
  ...buildInitialToolRecords(),
  ...buildExtraSubmissionRecords(),
];
const toolBySlug = new Map<string, MockToolRecord>(
  toolRecords.map((record) => [record.seed.slug, record]),
);
const approvalRecords: MockApprovalRecord[] = buildInitialApprovalRecords(toolRecords);
const whitelist: MockWhitelistEntry[] = buildInitialWhitelist();
const settings: MockSettingRecord[] = MOCK_SETTINGS.map((item) => ({ ...item }));
const downloadLogs: MockDownloadLog[] = buildInitialDownloadLogs();

/** Fill in `version_id` now that the version ids have been assigned. */
for (const log of downloadLogs) {
  const record = toolRecords.find((item) => item.seed.id === log.tool_id);
  const current = record?.versions.find((version) => version.status === "approved");
  log.version_id = current?.id ?? null;
  if (current) log.file_size = current.file_size;
}

let nextToolId = 400;
let nextApprovalRecordId = 10_000;
let nextDownloadLogId = 11_000;
let nextImageId = 20_000;

/** ticket → { tool_id, version_id, user_id, expires_at } */
const downloadTickets = new Map<
  string,
  { tool_id: number; version_id: number; user_id: number; expires_at: number }
>();

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

function validationError(fields: FieldError[]) {
  return errorResponse(400, "VALIDATION_ERROR", "请求参数校验失败", { fields });
}

function forbidden(message = "没有操作权限"): Response {
  return errorResponse(403, "FORBIDDEN", message);
}

function notFound(message = "资源不存在"): Response {
  return errorResponse(404, "NOT_FOUND", message);
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
    status: user.status,
    last_login_at: user.last_login_at,
    created_at: user.created_at,
  };
}

function findUser(username: string): MockUser | undefined {
  const normalized = username.trim().toLowerCase();
  return MOCK_USERS.find((user) => user.username === normalized);
}

function ownerOf(record: MockToolRecord): MockUser {
  const owner = findUser(record.seed.owner_username);
  if (!owner) throw new Error(`mock error: unknown owner ${record.seed.owner_username}`);
  return owner;
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

function isApprover(user: MockUser): boolean {
  return user.roles.includes("approver") || user.roles.includes("superadmin");
}

function pageOf(request: Request): { page: number; pageSize: number } {
  const params = new URL(request.url).searchParams;
  const page = Math.max(1, Number.parseInt(params.get("page") ?? "1", 10) || 1);
  const pageSize = Math.min(
    200,
    Math.max(1, Number.parseInt(params.get("page_size") ?? "20", 10) || 20),
  );
  return { page, pageSize };
}

function paginate<T>(items: T[], page: number, pageSize: number): Paginated<T> {
  const total = items.length;
  const start = (page - 1) * pageSize;
  return {
    items: items.slice(start, start + pageSize),
    total,
    page,
    page_size: pageSize,
    pages: Math.ceil(total / pageSize),
  };
}

function toolMatchesQuery(record: MockToolRecord, keyword: string): boolean {
  const needle = keyword.trim().toLowerCase();
  if (!needle) return true;
  return (
    record.seed.name.toLowerCase().includes(needle) ||
    record.seed.summary.toLowerCase().includes(needle) ||
    record.seed.description_md.toLowerCase().includes(needle) ||
    record.seed.tags.some((tag) => tag.toLowerCase().includes(needle))
  );
}

function sortListItems(items: ToolListItem[], sort: ToolSort): ToolListItem[] {
  const sorted = [...items];
  switch (sort) {
    case "new":
      sorted.sort((a, b) => (b.published_at ?? "").localeCompare(a.published_at ?? ""));
      break;
    case "name":
      sorted.sort((a, b) => a.name.localeCompare(b.name, "zh-Hans-CN"));
      break;
    case "-updated_at":
      sorted.sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""));
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

/* -------------------------------------------------------------------------- */
/* Tool projections                                                           */
/* -------------------------------------------------------------------------- */

function currentVersionOf(record: MockToolRecord): MockVersion | undefined {
  return record.versions.find((version) => version.status === "approved");
}

function pendingVersionOf(record: MockToolRecord): MockVersion | undefined {
  return record.versions.find((version) => version.status === "pending");
}

function rejectedVersionOf(record: MockToolRecord): MockVersion | undefined {
  return [...record.versions].reverse().find((version) => version.status === "rejected");
}

function isOwner(record: MockToolRecord, user: MockUser): boolean {
  return ownerOf(record).id === user.id;
}

/** `true` when the tool is visible to this user (public / owner / approver / ACL). */
function canView(record: MockToolRecord, user: MockUser): boolean {
  if (isOwner(record, user) || isApprover(user)) return true;
  if (record.acl_visibility === "public") return true;
  if (record.acl_visibility === "private") return false;
  return record.acl.some(
    (entry) => entry.subject_type === "user" && entry.subject_id === user.id,
  );
}

function canDownload(record: MockToolRecord, user: MockUser): boolean {
  // FR-ACL-05：viewer 角色即使在 public 工具上也不能下载，只能查看详情。
  if (user.roles.includes("viewer")) return false;
  if (isOwner(record, user) || isApprover(user)) return true;
  if (record.status !== "approved") return false;
  if (record.acl_visibility === "public") return true;
  if (record.acl_visibility === "private") return false;
  return record.acl.some(
    (entry) => entry.subject_type === "user" && entry.subject_id === user.id && entry.can_download,
  );
}

function canEdit(record: MockToolRecord, user: MockUser): boolean {
  if (isApprover(user)) return true;
  if (!isOwner(record, user)) return false;
  return record.status !== "pending" && record.status !== "pending_update";
}

function permissionsFor(record: MockToolRecord, user: MockUser): ToolPermissions {
  const own = isOwner(record, user);
  return {
    can_edit: canEdit(record, user),
    can_download: canDownload(record, user),
    can_manage_versions: own && record.status !== "pending_update",
    can_view_acl: own || isApprover(user),
    can_delete: own || isApprover(user),
    can_submit: own && (record.status === "draft" || record.status === "rejected"),
    can_approve: isApprover(user),
  };
}

function toToolListItem(record: MockToolRecord, user: MockUser): ToolListItem {
  const base = toolSeedToListItem(record.seed);
  const pending = pendingVersionOf(record);
  return {
    ...base,
    slug: record.seed.slug,
    visibility: record.acl_visibility,
    cover_url: coverUrlFor(record),
    current_version: currentVersionOf(record)?.version ?? null,
    file_size: currentVersionOf(record)?.file_size ?? null,
    has_pending_version: (isOwner(record, user) || isApprover(user)) && pending !== undefined,
    can_download: canDownload(record, user),
    published_at: record.seed.published_at || null,
    updated_at: record.seed.updated_at || null,
  };
}

function coverUrlFor(record: MockToolRecord): string | null {
  const cover = record.images.find((image) => image.kind === "cover");
  if (!cover) return null;
  return cover.thumb_url;
}

function toVersionSummary(record: MockToolRecord, version: MockVersion): VersionSummary {
  const uploader = findMockUserById(version.uploaded_by_id);
  return {
    id: version.id,
    tool_id: version.tool_id,
    version: version.version,
    changelog_md: version.changelog_md,
    status: version.status,
    is_current: currentVersionOf(record)?.id === version.id,
    file_name: version.file_name,
    file_size: version.file_size,
    file_sha256_short: version.file_sha256 ? version.file_sha256.slice(0, 16) : null,
    file_ext: version.file_ext,
    mime_type: version.mime_type,
    uploaded_by: uploader ? { id: uploader.id, display_name: uploader.display_name } : null,
    approved_at: version.approved_at,
    reject_reason: version.reject_reason,
    purged_at: version.purged_at,
    created_at: version.created_at,
    can_download: version.status === "approved" || version.status === "superseded",
  };
}

function skillInfoFor(record: MockToolRecord, version: MockVersion): SkillVersionInfo | null {
  if (record.seed.tool_type !== "skill") return null;
  const tree = buildSkillFileTree(record.seed.name);
  return {
    manifest: { ...MOCK_SKILL_MANIFEST, name: record.seed.slug, version: version.version },
    file_tree_summary: skillTreeSummary(tree),
    parse_error: null,
  };
}

function toVersionDetail(record: MockToolRecord, version: MockVersion): VersionDetail {
  return {
    ...toVersionSummary(record, version),
    file_sha256: version.file_sha256,
    skill: skillInfoFor(record, version),
    prompt_content: version.prompt_content,
  };
}

function skillDetailFor(record: MockToolRecord): SkillDetailInfo | null {
  if (record.seed.tool_type !== "skill") return null;
  const current = currentVersionOf(record) ?? pendingVersionOf(record);
  const tree = buildSkillFileTree(record.seed.name);
  return {
    manifest: current ? { ...MOCK_SKILL_MANIFEST, name: record.seed.slug, version: current.version } : null,
    readme_md: MOCK_SKILL_README,
    file_tree_summary: skillTreeSummary(tree),
    parse_error: null,
  };
}

function promptDetailFor(record: MockToolRecord): PromptDetailInfo | null {
  if (record.seed.tool_type !== "prompt") return null;
  const current = currentVersionOf(record) ?? pendingVersionOf(record);
  const content = current?.prompt_content ?? "";
  return { content, char_count: content.length };
}

function renderMarkdown(md: string): string {
  const escaped = md
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  const lines = escaped.split("\n");
  const html: string[] = [];
  let inList = false;
  let inCode = false;
  for (const line of lines) {
    if (line.startsWith("```")) {
      if (inCode) {
        html.push("</code></pre>");
        inCode = false;
      } else {
        if (inList) {
          html.push("</ul>");
          inList = false;
        }
        html.push("<pre><code>");
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      html.push(`${line}\n`);
      continue;
    }
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      if (inList) {
        html.push("</ul>");
        inList = false;
      }
      const level = heading[1]?.length ?? 2;
      html.push(`<h${level}>${heading[2] ?? ""}</h${level}>`);
      continue;
    }
    if (/^[-*]\s+/.test(line)) {
      if (!inList) {
        html.push("<ul>");
        inList = true;
      }
      html.push(`<li>${line.replace(/^[-*]\s+/, "")}</li>`);
      continue;
    }
    if (inList) {
      html.push("</ul>");
      inList = false;
    }
    if (line.trim()) html.push(`<p>${line}</p>`);
  }
  if (inList) html.push("</ul>");
  if (inCode) html.push("</code></pre>");
  return html.join("\n");
}

function toToolDetail(record: MockToolRecord, user: MockUser): ToolDetail {
  const current = currentVersionOf(record);
  const pending = pendingVersionOf(record);
  return {
    id: record.seed.id,
    slug: record.seed.slug,
    name: record.seed.name,
    summary: record.seed.summary,
    description_md: record.seed.description_md,
    description_html: renderMarkdown(record.seed.description_md),
    tool_type: record.seed.tool_type,
    visibility: record.acl_visibility,
    status: record.status,
    category: categoryRef(record),
    tags: [...record.seed.tags],
    images: record.images.map((image) => ({ ...image })),
    webapp_url: record.webapp_url,
    current_version: current ? toVersionDetail(record, current) : null,
    pending_version: pending ? { id: pending.id, version: pending.version } : null,
    version_count: record.versions.length,
    history_version_count: record.versions.filter((version) => version.status !== "pending").length,
    download_count: record.seed.download_count,
    view_count: record.seed.view_count,
    owner: {
      id: ownerOf(record).id,
      username: ownerOf(record).username,
      display_name: ownerOf(record).display_name,
    },
    published_at: record.seed.published_at || null,
    last_version_at: record.last_version_at,
    created_at: record.created_at,
    updated_at: record.seed.updated_at || null,
    reject_reason: record.reject_reason,
    offline_reason: record.offline_reason,
    deleted_at: record.deleted_at,
    can_download: canDownload(record, user),
    can_edit: canEdit(record, user),
    permissions: permissionsFor(record, user),
    acl:
      record.acl_visibility === "public" && record.acl.length === 0
        ? null
        : record.acl.map((entry) => aclEntryOut(entry)),
    skill: skillDetailFor(record),
    prompt: promptDetailFor(record),
  };
}

function categoryRef(record: MockToolRecord) {
  const category = MOCK_CATEGORIES.find((item) => item.slug === record.seed.category_slug);
  if (!category) return null;
  return {
    id: category.id,
    slug: category.slug,
    name: category.name,
    icon: category.icon,
  };
}

function aclEntryOut(entry: MockToolRecord["acl"][number]): AclEntry {
  if (entry.subject_type === "user") {
    const user = findMockUserById(entry.subject_id);
    return {
      id: entry.id,
      subject_type: "user",
      subject_id: entry.subject_id,
      subject_name: user?.display_name ?? null,
      can_download: entry.can_download,
    };
  }
  const group = MOCK_GROUPS.find((item) => item.id === entry.subject_id);
  return {
    id: entry.id,
    subject_type: "group",
    subject_id: entry.subject_id,
    subject_name: group?.name ?? null,
    can_download: entry.can_download,
  };
}

const STATUS_LABELS: Record<ToolStatus, string> = {
  draft: "草稿",
  pending: "待审核",
  approved: "已发布",
  rejected: "已驳回",
  pending_update: "新版本待审",
  offline: "已下架",
};

function toMyToolListItem(record: MockToolRecord): MyToolListItem {
  const current = currentVersionOf(record);
  const pending = pendingVersionOf(record);
  return {
    id: record.seed.id,
    slug: record.seed.slug,
    name: record.seed.name,
    summary: record.seed.summary,
    tool_type: record.seed.tool_type,
    visibility: record.acl_visibility,
    status: record.status,
    category: categoryRef(record),
    tags: [...record.seed.tags],
    cover_url: coverUrlFor(record),
    current_version: current?.version ?? null,
    pending_version: pending?.version ?? null,
    file_size: current?.file_size ?? record.seed.file_size,
    download_count: record.seed.download_count,
    view_count: record.seed.view_count,
    version_seq: record.version_seq,
    reject_reason: record.reject_reason,
    offline_reason: record.offline_reason,
    published_at: record.seed.published_at || null,
    created_at: record.created_at,
    updated_at: record.seed.updated_at || null,
    status_label: STATUS_LABELS[record.status],
  };
}

function usageFor(user: MockUser): Usage {
  const owned = toolRecords.filter((record) => isOwner(record, user));
  const usedBytes = owned
    .flatMap((record) => record.versions)
    .reduce((total, version) => total + (version.file_size ?? 0), 0);
  const quotaBytes = 2 * 1024 * 1024 * 1024;
  const platformUsed = toolRecords
    .flatMap((record) => record.versions)
    .reduce((total, version) => total + (version.file_size ?? 0), 0);
  return {
    tool_count: owned.length,
    published_tool_count: owned.filter((record) => record.status === "approved").length,
    draft_tool_count: owned.filter((record) => record.status === "draft").length,
    pending_tool_count: owned.filter(
      (record) => record.status === "pending" || record.status === "pending_update",
    ).length,
    version_count: owned.reduce((total, record) => total + record.versions.length, 0),
    download_count: owned.reduce((total, record) => total + record.seed.download_count, 0),
    used_bytes: usedBytes,
    quota_bytes: quotaBytes,
    total_quota_bytes: 64 * 1024 * 1024 * 1024,
    platform_used_bytes: platformUsed,
    used_percent: Math.round((usedBytes / quotaBytes) * 1000) / 10,
  };
}

function toProfile(user: MockUser): Profile {
  return {
    id: user.id,
    username: user.username,
    display_name: user.display_name,
    email: user.email,
    roles: [...user.roles],
    permissions: [...user.permissions],
    status: user.status,
    auth_source: user.auth_source,
    must_change_password: user.must_change_password,
    last_login_at: user.last_login_at,
    created_at: user.created_at,
    usage: usageFor(user),
  };
}

function toDownloadLogItem(log: MockDownloadLog): DownloadLogItem {
  const record = toolRecords.find((item) => item.seed.id === log.tool_id);
  const version = record?.versions.find((item) => item.id === log.version_id);
  return {
    id: log.id,
    tool_id: log.tool_id,
    tool_slug: record?.seed.slug ?? null,
    tool_name: record?.seed.name ?? null,
    version_id: log.version_id,
    version: version?.version ?? null,
    file_name: log.file_name,
    file_size: log.file_size,
    via_api_token_id: null,
    created_at: log.created_at,
  };
}

function toPendingVersionBrief(version: MockVersion): PendingVersionBrief {
  return {
    id: version.id,
    version: version.version,
    changelog_md: version.changelog_md,
    file_name: version.file_name,
    file_size: version.file_size,
    file_sha256: version.file_sha256,
  };
}

function toCurrentVersionBrief(version: MockVersion | undefined): CurrentVersionBrief | null {
  return version ? { id: version.id, version: version.version } : null;
}

function toSupersededBrief(version: MockVersion | undefined): SupersededVersionBrief | null {
  return version ? { id: version.id, version: version.version } : null;
}

function toApprovalRecord(record: MockApprovalRecord): ApprovalRecord {
  return {
    id: record.id,
    tool_id: record.tool_id,
    tool_name: record.tool_name,
    tool_slug: record.tool_slug,
    version_id: record.version_id,
    version: record.version,
    action: record.action,
    from_status: record.from_status,
    to_status: record.to_status,
    version_from_status: record.version_from_status,
    version_to_status: record.version_to_status,
    actor_id: record.actor_id,
    actor_label: record.actor_label,
    is_automatic: record.is_automatic,
    auto_rule: record.auto_rule,
    reason: record.reason,
    note: record.note,
    created_at: record.created_at,
  };
}

function toSettingItem(setting: MockSettingRecord): SettingItem {
  const updatedBy = setting.updated_by_id ? findMockUserById(setting.updated_by_id) : undefined;
  return {
    key: setting.key,
    value: setting.value,
    value_type: setting.value_type,
    is_public: setting.is_public,
    description: setting.description,
    options: setting.options ? [...setting.options] : null,
    min: setting.min,
    max: setting.max,
    updated_at: setting.updated_at,
    updated_by: updatedBy ? { id: updatedBy.id, display_name: updatedBy.display_name } : null,
  };
}

function settingListResponse(warnings: SettingWarning[] = []): SettingListResponse {
  return {
    items: settings.map(toSettingItem),
    warnings,
  };
}

/**
 * Waiting time in hours, from the fixed `submitted_at` seed (see `waitingHoursFor`)
 * — 3.4 / 26 / 80 / 8 / 50 / 1 / 2 hours for the queued items.
 */
function waitingHours(record: MockToolRecord): number {
  return waitingHoursFor(record.seed.id, record.submitted_at);
}

function isQueued(record: MockToolRecord): boolean {
  return record.status === "pending" || record.status === "pending_update";
}

function pendingSubmissionCount(): number {
  return toolRecords.filter(isQueued).length;
}

/** Human-visible name for an approval action (used in logs/notes only). */
function nowIso(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

function appendApprovalRecord(init: {
  tool: MockToolRecord;
  action: MockApprovalRecord["action"];
  to_status: string;
  actor: MockUser;
  version?: MockVersion | undefined;
  from_status?: string | null;
  version_from_status?: string | null;
  version_to_status?: string | null;
  is_automatic?: boolean;
  auto_rule?: string | null;
  reason?: string | null;
  note?: string | null;
}): MockApprovalRecord {
  const record: MockApprovalRecord = {
    id: nextApprovalRecordId++,
    tool_id: init.tool.seed.id,
    tool_name: init.tool.seed.name,
    tool_slug: init.tool.seed.slug,
    version_id: init.version?.id ?? null,
    version: init.version?.version ?? null,
    action: init.action,
    from_status: init.from_status ?? null,
    to_status: init.to_status,
    version_from_status: init.version_from_status ?? null,
    version_to_status: init.version_to_status ?? null,
    actor_id: init.actor.id,
    actor_label: init.actor.display_name,
    is_automatic: init.is_automatic ?? false,
    auto_rule: init.auto_rule ?? null,
    reason: init.reason ?? null,
    note: init.note ?? null,
    created_at: nowIso(),
  };
  approvalRecords.push(record);
  return record;
}

/** A version is uploadable when the tool is not sitting in the review queue. */
function ensureEditable(record: MockToolRecord, user: MockUser): Response | null {
  if (!isOwner(record, user)) return forbidden("只能编辑自己的工具");
  if (record.status === "pending" || record.status === "pending_update") {
    return errorResponse(409, "TOOL_NOT_EDITABLE", "工具正在审批中，先撤回再修改");
  }
  return null;
}

function findToolBySlug(slug: string): MockToolRecord | undefined {
  return toolBySlug.get(slug);
}

function nextSlug(name: string, id: number): string {
  const ascii = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  const base = ascii.length >= 3 ? ascii : `tool-${id}`;
  if (!toolBySlug.has(base)) return base;
  return `${base}-${fakeSha256(base).slice(0, 4)}`;
}

function makeNewRecord(
  user: MockUser,
  fields: {
    name: string;
    summary: string;
    description_md: string;
    tool_type: ToolType;
    category_slug: string;
    tags: string[];
    visibility: Visibility;
    webapp_url: string | null;
  },
): MockToolRecord {
  const id = nextToolId++;
  const slug = nextSlug(fields.name, id);
  const seed = {
    id,
    slug,
    name: fields.name,
    summary: fields.summary,
    description_md: fields.description_md,
    tool_type: fields.tool_type,
    visibility: fields.visibility,
    category_slug: fields.category_slug,
    tags: fields.tags,
    cover: false,
    owner_username: user.username,
    current_version: "",
    file_size: null,
    download_count: 0,
    view_count: 0,
    published_at: "",
    updated_at: nowIso(),
    webapp_url: fields.webapp_url,
  };
  const record: MockToolRecord = {
    seed,
    status: "draft",
    version_seq: 0,
    created_at: nowIso(),
    last_version_at: null,
    reject_reason: null,
    offline_reason: null,
    deleted_at: null,
    webapp_url: fields.webapp_url,
    versions: [],
    images: [],
    acl_visibility: fields.visibility,
    acl: [],
    submitted_at: null,
    submission_type: null,
    history_version_limit: MOCK_VERSION_HISTORY_LIMIT,
  };
  toolRecords.push(record);
  toolBySlug.set(record.seed.slug, record);
  return record;
}

function categoryIdToSlug(categoryId: number | null | undefined): string | null {
  if (categoryId === null || categoryId === undefined) return null;
  const category = MOCK_CATEGORIES.find((item) => item.id === categoryId);
  return category ? category.slug : null;
}

/** Submit a draft / rejected tool (or an uploaded version) into the queue. */
function submitForReview(
  record: MockToolRecord,
  actor: MockUser,
  options: { newVersion?: MockVersion; autoRule?: string | null } = {},
): { auto_approved: boolean; record_id: number | null } {
  const fromStatus = record.status;
  let version = options.newVersion;
  if (!version) {
    version = rejectedVersionOf(record) ?? undefined;
    if (version) {
      version.status = "pending";
      version.reject_reason = null;
    } else if (record.versions.length === 0) {
      version = createVersion(record, actor, {
        version: "0.1.0",
        changelog_md: "首次提交，等待审批。",
        fileSize: record.seed.tool_type === "webapp" || record.seed.tool_type === "prompt" ? null : 512_000,
      });
    }
  }
  if (!version) return { auto_approved: false, record_id: null };

  const previous = currentVersionOf(record);
  const isNewVersion = previous !== undefined;
  record.status = isNewVersion ? "pending_update" : "pending";
  record.submission_type = isNewVersion ? "new_version" : "new_tool";
  record.submitted_at = nowIso();
  record.reject_reason = null;
  record.seed.updated_at = record.submitted_at;
  const approval = appendApprovalRecord({
    tool: record,
    action: fromStatus === "rejected" ? "resubmit" : "submit",
    from_status: fromStatus,
    to_status: record.status,
    version,
    version_from_status: null,
    version_to_status: "pending",
    actor,
    note: isNewVersion ? "版本更新，等待审批。" : "首次提交，等待审批。",
  });
  return { auto_approved: false, record_id: approval.id };
}

function createVersion(
  record: MockToolRecord,
  actor: MockUser,
  fields: {
    version: string;
    changelog_md: string;
    fileName?: string | null;
    fileSize?: number | null;
    fileSha256?: string | null;
    fileExt?: string | null;
    mimeType?: string | null;
    promptContent?: string | null;
  },
): MockVersion {
  const version: MockVersion = {
    id: 100_000 + toolRecords.reduce((total, item) => total + item.versions.length, 0),
    tool_id: record.seed.id,
    version: fields.version,
    changelog_md: fields.changelog_md,
    status: "pending",
    file_name: fields.fileName ?? null,
    file_size: fields.fileSize ?? null,
    file_sha256: fields.fileSha256 ?? null,
    file_ext: fields.fileExt ?? null,
    mime_type: fields.mimeType ?? null,
    uploaded_by_id: actor.id,
    approved_at: null,
    reject_reason: null,
    purged_at: null,
    created_at: nowIso(),
    prompt_content: fields.promptContent ?? null,
    submitted_at: null,
  };
  record.versions.push(version);
  return version;
}

function extOf(fileName: string): string {
  const parts = fileName.split(".");
  return parts.length > 1 ? (parts[parts.length - 1] ?? "").toLowerCase() : "";
}

function mimeOf(ext: string): string | null {
  switch (ext) {
    case "zip":
      return "application/zip";
    case "tar":
      return "application/x-tar";
    case "gz":
      return "application/gzip";
    case "md":
      return "text/markdown";
    case "txt":
      return "text/plain";
    case "sh":
      return "application/x-sh";
    case "png":
      return "image/png";
    case "jpg":
    case "jpeg":
      return "image/jpeg";
    case "webp":
      return "image/webp";
    case "svg":
      return "image/svg+xml";
    default:
      return ext ? "application/octet-stream" : null;
  }
}

/* -------------------------------------------------------------------------- */
/* Approval queue projection                                                  */
/* -------------------------------------------------------------------------- */

function toQueueItem(record: MockToolRecord): ApprovalQueueItem {
  const pending = pendingVersionOf(record);
  const current = currentVersionOf(record);
  const owner = ownerOf(record);
  return {
    tool_id: record.seed.id,
    tool_slug: record.seed.slug,
    tool_name: record.seed.name,
    tool_type: record.seed.tool_type,
    summary: record.seed.summary,
    visibility: record.acl_visibility,
    category: categoryRef(record),
    tags: [...record.seed.tags],
    submission_type: record.submission_type ?? (current ? "new_version" : "new_tool"),
    status: record.status,
    pending_version: pending ? toPendingVersionBrief(pending) : null,
    current_version: toCurrentVersionBrief(current),
    owner: { id: owner.id, username: owner.username, display_name: owner.display_name },
    submitted_at: record.submitted_at,
    waiting_hours: waitingHours(record),
    version_seq: record.version_seq,
  };
}

/* -------------------------------------------------------------------------- */
/* Images                                                                     */
/* -------------------------------------------------------------------------- */

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
  skills: ["#ddd6fe", "#c4b5fd"],
  prompts: ["#fde68a", "#fcd34d"],
};

function imageSvg(label: string, categorySlug: string | null, width: number, height: number): string {
  const [from, to] = COVER_TINTS[categorySlug ?? ""] ?? ["#e2e8f0", "#cbd5e1"];
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeXml(label)}">
  <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="${from}"/><stop offset="100%" stop-color="${to}"/>
  </linearGradient></defs>
  <rect width="${width}" height="${height}" fill="url(#g)"/>
  <text x="50%" y="50%" text-anchor="middle" dominant-baseline="middle"
        font-family="PingFang SC, Microsoft YaHei, sans-serif" font-size="28" fill="#1e293b">${escapeXml(label)}</text>
</svg>`;
}

/** Find the tool behind an image id (seeded covers or uploaded images). */
function findImageOwner(imageId: number): MockToolRecord | undefined {
  return toolRecords.find((record) => record.images.some((image) => image.id === imageId));
}

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
      features: {
        webapp_health_check: false,
        skill_preview: true,
        anonymous_view: false,
        change_password: true,
      },
    };
    return HttpResponse.json(meta);
  }),

  /* ---- auth ---- */
  http.get(`${API}/auth/provider`, async () => {
    await delay(20);
    const loginFields: LoginField[] = [
      { name: "username", label: "用户名", type: "text", required: true },
      { name: "password", label: "密码", type: "password", required: true },
    ];
    const body: AuthProviderResponse = {
      provider: "local",
      display_name: "本地账号",
      login_fields: loginFields,
      password_change_supported: true,
      refresh_supported: true,
    };
    return HttpResponse.json(body);
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
    user.last_login_at = nowIso();

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

  /* CONTRACT §14.2: logout answers `200` + `{"status":"ok"}`, NOT 204. */
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
    const ok: StatusResponse = { status: "ok" };
    return HttpResponse.json(ok);
  }),

  http.get(`${API}/auth/me`, async ({ request }) => {
    await delay(30);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    return HttpResponse.json(toUserDto(user));
  }),

  /* CONTRACT §14.2: change-password also answers `200` + `{"status":"ok"}`. */
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
    const ok: StatusResponse = { status: "ok" };
    return HttpResponse.json(ok);
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

    const params = new URL(request.url).searchParams;
    const q = params.get("q") ?? "";
    const categories = params.getAll("category");
    const tags = params.getAll("tag");
    const types = params.getAll("type") as ToolType[];
    const owner = params.get("owner");
    const sort = (params.get("sort") ?? "hot") as ToolSort;
    const { page, pageSize } = pageOf(request);

    let items = toolRecords
      // `pending_update` 也必须出现在门户：它的**当前版本继续对外服务**（FR-VER-03），
      // 只有待审的新版本不可见。与真实后端 app/repositories/tools.py 的
      // `PORTAL_STATUSES = {approved, pending_update}` 保持一致。
      .filter(
        (record) =>
          (record.status === "approved" || record.status === "pending_update") &&
          !record.deleted_at,
      )
      .filter((record) => canView(record, user))
      .filter((record) => toolMatchesQuery(record, q))
      .filter((record) => categories.length === 0 || categories.includes(record.seed.category_slug))
      .filter((record) => types.length === 0 || types.includes(record.seed.tool_type))
      .filter((record) => tags.length === 0 || tags.some((tag) => record.seed.tags.includes(tag)))
      .filter((record) => !owner || record.seed.owner_username === owner)
      .map((record) => toToolListItem(record, user));

    items = sortListItems(items, sort);
    const pageItems = items.slice((page - 1) * pageSize, (page - 1) * pageSize + pageSize);

    return HttpResponse.json({
      items: pageItems,
      total: items.length,
      page,
      page_size: pageSize,
      pages: Math.ceil(items.length / pageSize),
      // Key always present: object on page 1, null when paging (docs/03 §3.3).
      facets: page === 1 ? buildFacets() : null,
    });
  }),

  /* ---- portal detail (CONTRACT §6.1) ---- */
  http.get(`${API}/tools/:slug`, async ({ request, params }) => {
    await delay(SIMULATED_LATENCY_MS);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    const slug = String(params.slug);

    // `/tools/:slug/versions` and friends are more specific handlers, but MSW
    // matches in declaration order, so those are declared *before* this one.
    const record = findToolBySlug(slug);
    if (!record || record.deleted_at) return notFound("工具不存在或已被删除");
    if (record.status !== "approved" && !isOwner(record, user) && !isApprover(user)) {
      return notFound("工具不存在或已被删除");
    }
    if (!canView(record, user)) return notFound("工具不存在或已被删除");
    return HttpResponse.json(toToolDetail(record, user));
  }),

  http.get(`${API}/tools/:slug/versions`, async ({ request, params }) => {
    await delay(80);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    const record = findToolBySlug(String(params.slug));
    if (!record || record.deleted_at || !canView(record, user)) return notFound("工具不存在或已被删除");
    const versions = [...record.versions]
      .sort((a, b) => b.created_at.localeCompare(a.created_at))
      .map((version) => toVersionSummary(record, version));
    return HttpResponse.json(versions);
  }),

  http.get(`${API}/tools/:slug/versions/:version/skill-preview`, async ({ request, params }) => {
    await delay(80);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    const record = findToolBySlug(String(params.slug));
    if (!record || record.deleted_at || !canView(record, user)) return notFound("工具不存在或已被删除");
    if (record.seed.tool_type !== "skill") {
      return errorResponse(400, "VALIDATION_ERROR", "该工具不是 Skill 类型", {
        fields: [{ field: "slug", message: "只有 Skill 类型支持包内预览" }],
      });
    }
    const versionString = String(params.version);
    const version = record.versions.find((item) => item.version === versionString);
    if (!version) return notFound(`版本 ${versionString} 不存在`);
    const tree = buildSkillFileTree(record.seed.name);
    const body: SkillPreview = {
      version: version.version,
      manifest: { ...MOCK_SKILL_MANIFEST, name: record.seed.slug, version: version.version },
      readme_md: MOCK_SKILL_README,
      file_tree: tree.map((entry) => ({
        path: entry.path,
        size: entry.size,
        is_dir: entry.is_dir,
        sha256: entry.is_dir ? null : entry.sha256,
      })),
      file_tree_truncated: false,
      total_size: skillTreeSummary(tree).total_size,
    };
    return HttpResponse.json(body);
  }),

  http.get(`${API}/tools/:slug/stats`, async ({ request, params }) => {
    await delay(60);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    const record = findToolBySlug(String(params.slug));
    if (!record || record.deleted_at || !canView(record, user)) return notFound("工具不存在或已被删除");
    // Deterministic 30-day trend derived from the tool's own counters.
    const daily = Array.from({ length: 30 }, (_, index) => {
      const day = index + 1;
      const views = Math.round((record.seed.view_count / 30) * (0.6 + ((day * 7) % 13) / 10));
      const downloads = Math.round((record.seed.download_count / 30) * (0.5 + ((day * 5) % 11) / 10));
      return {
        stat_date: `2025-03-${String(day).padStart(2, "0")}`,
        views,
        downloads,
      };
    });
    const body: ToolStats = {
      tool_id: record.seed.id,
      download_count: record.seed.download_count,
      view_count: record.seed.view_count,
      daily,
    };
    return HttpResponse.json(body);
  }),

  /* One-time download ticket; `GET .../download` accepts it via `?ticket=`. */
  http.post(`${API}/tools/:slug/download-ticket`, async ({ request, params }) => {
    await delay(60);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    const record = findToolBySlug(String(params.slug));
    if (!record || record.deleted_at || !canView(record, user)) return notFound("工具不存在或已被删除");
    if (!canDownload(record, user)) return forbidden("没有该工具的下载权限");

    const requested = new URL(request.url).searchParams.get("version_id");
    const requestedId = requested ? Number.parseInt(requested, 10) : null;
    const version =
      (requestedId !== null
        ? record.versions.find((item) => item.id === requestedId)
        : undefined) ?? currentVersionOf(record);
    if (!version) return notFound("该工具还没有可下载的版本");
    if (version.status !== "approved" && version.status !== "superseded") {
      return errorResponse(409, "STATE_CONFLICT", "该版本尚未通过审批，不能下载");
    }

    const ticket = `${crypto.randomUUID().replace(/-/g, "")}`;
    const expiresAt = Date.now() + TICKET_TTL_MS;
    downloadTickets.set(ticket, {
      tool_id: record.seed.id,
      version_id: version.id,
      user_id: user.id,
      expires_at: expiresAt,
    });

    const body: DownloadTicket = {
      url: `${API}/tools/${encodeURIComponent(record.seed.slug)}/download?version_id=${version.id}&ticket=${ticket}`,
      expires_at: new Date(expiresAt).toISOString().replace(/\.\d{3}Z$/, "Z"),
      file_name: version.file_name,
      file_size: version.file_size,
      file_sha256: version.file_sha256,
    };
    return HttpResponse.json(body);
  }),

  http.get(`${API}/tools/:slug/download`, async ({ request, params }) => {
    await delay(120);
    const url = new URL(request.url);
    const ticket = url.searchParams.get("ticket");
    const record = findToolBySlug(String(params.slug));
    if (!record || record.deleted_at) return notFound("工具不存在或已被删除");

    let version: MockVersion | undefined;
    let user: MockUser | null = null;
    if (ticket) {
      const entry = downloadTickets.get(ticket);
      if (!entry || entry.expires_at <= Date.now() || entry.tool_id !== record.seed.id) {
        // An expired / forged ticket is an auth failure, not a missing resource.
        return tokenExpired();
      }
      version = record.versions.find((item) => item.id === entry.version_id);
      user = findMockUserById(entry.user_id) ?? null;
    } else {
      user = authenticate(request);
      if (!user) return tokenExpired();
      const requested = url.searchParams.get("version_id");
      const requestedId = requested ? Number.parseInt(requested, 10) : null;
      version =
        (requestedId !== null
          ? record.versions.find((item) => item.id === requestedId)
          : undefined) ?? currentVersionOf(record);
    }

    if (!user || !canDownload(record, user) || !canView(record, user)) {
      return notFound("没有该工具的下载权限");
    }
    if (!version || (version.status !== "approved" && version.status !== "superseded")) {
      return notFound("该版本不可下载");
    }

    const fileName = version.file_name ?? `${record.seed.slug}.bin`;
    // A real, downloadable body (the mock has no stored zip). Size/headers mirror
    // the real FileResponse contract (docs/03 §1.10) without lying about length.
    const body = [
      `# ${record.seed.name}`,
      `slug: ${record.seed.slug}`,
      `version: ${version.version}`,
      `sha256: ${version.file_sha256 ?? "(none)"}`,
      "",
      "这是 MSW mock 生成的占位下载内容（真实后端会返回文件本体）。",
    ].join("\n");
    const bytes = new TextEncoder().encode(body);

    downloadLogs.push({
      id: nextDownloadLogId++,
      user_id: user.id,
      tool_id: record.seed.id,
      version_id: version.id,
      file_name: fileName,
      file_size: version.file_size,
      created_at: nowIso(),
    });

    return new HttpResponse(bytes, {
      status: 200,
      headers: {
        "Content-Type": version.mime_type ?? "application/octet-stream",
        "Content-Disposition": `attachment; filename="${fileName}"; filename*=UTF-8''${encodeURIComponent(fileName)}`,
        "Content-Length": String(bytes.byteLength),
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
      },
    });
  }),

  /* ---- images (signed capability URL, CONTRACT §14.3) ---- */
  http.get(`${API}/images/:id`, async ({ request, params }) => {
    await delay(40);
    const url = new URL(request.url);
    const sig = url.searchParams.get("sig");
    const variant = url.searchParams.get("variant");
    // Two auth paths: a valid `sig`, OR a bearer token — both absent → 404.
    const signed = sig !== null && sig.length > 0;
    const bearer = authenticate(request) !== null;
    if (!signed && !bearer) return notFound("图片不存在");
    if (signed && sig !== IMAGE_SIGNATURE && !bearer) return notFound("图片不存在");

    const id = Number.parseInt(String(params.id), 10);
    const record = findImageOwner(id);
    if (!record) return notFound("图片不存在");
    const [width, height] = variant === "thumb" ? [480, 270] : [1200, 630];
    const svg = imageSvg(record.seed.name.slice(0, 12), record.seed.category_slug, width, height);
    return new HttpResponse(svg, {
      headers: { "Content-Type": "image/svg+xml", "Cache-Control": "no-store" },
    });
  }),

  /* ---- me: profile & stats ---- */
  http.get(`${API}/me/profile`, async ({ request }) => {
    await delay(60);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    return HttpResponse.json(toProfile(user));
  }),

  http.patch(`${API}/me/profile`, async ({ request }) => {
    await delay(80);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    const body = (await request.json()) as { display_name?: string; email?: string | null };
    const fields: FieldError[] = [];
    if (body.display_name !== undefined && body.display_name.trim().length === 0) {
      fields.push({ field: "display_name", message: "展示名不能为空" });
    }
    if (body.display_name !== undefined && body.display_name.length > 128) {
      fields.push({ field: "display_name", message: "展示名不能超过 128 个字符" });
    }
    if (body.email !== undefined && body.email !== null && !body.email.includes("@")) {
      fields.push({ field: "email", message: "邮箱格式不正确" });
    }
    if (fields.length > 0) return validationError(fields);

    if (body.display_name !== undefined) user.display_name = body.display_name.trim();
    if (body.email !== undefined) user.email = body.email;
    return HttpResponse.json(toProfile(user));
  }),

  http.get(`${API}/me/stats`, async ({ request }) => {
    await delay(60);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    return HttpResponse.json(usageFor(user));
  }),

  http.get(`${API}/me/downloads`, async ({ request }) => {
    await delay(60);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    const { page, pageSize } = pageOf(request);
    const items = downloadLogs
      .filter((log) => log.user_id === user.id)
      .sort((a, b) => b.created_at.localeCompare(a.created_at))
      .map(toDownloadLogItem);
    return HttpResponse.json(paginate(items, page, pageSize));
  }),

  /* ---- me: tools ---- */
  http.get(`${API}/me/tools`, async ({ request }) => {
    await delay(SIMULATED_LATENCY_MS);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    const params = new URL(request.url).searchParams;
    const statuses = params.getAll("status") as ToolStatus[];
    const types = params.getAll("type") as ToolType[];
    const categories = params.getAll("category");
    const q = params.get("q") ?? "";
    const sort = (params.get("sort") ?? "-updated_at") as ToolSort;
    const { page, pageSize } = pageOf(request);

    const myList = toolRecords
      .filter((record) => isOwner(record, user) && !record.deleted_at)
      .filter((record) => statuses.length === 0 || statuses.includes(record.status))
      .filter((record) => types.length === 0 || types.includes(record.seed.tool_type))
      .filter((record) => categories.length === 0 || categories.includes(record.seed.category_slug))
      .filter((record) => toolMatchesQuery(record, q));

    const mapped = myList.map(toMyToolListItem);
    const sorted = [...mapped].sort((a, b) => {
      if (sort === "name") return a.name.localeCompare(b.name, "zh-Hans-CN");
      if (sort === "hot") return b.download_count - a.download_count;
      if (sort === "new") return (b.published_at ?? "").localeCompare(a.published_at ?? "");
      return (b.updated_at ?? "").localeCompare(a.updated_at ?? "");
    });
    return HttpResponse.json(paginate(sorted, page, pageSize));
  }),

  http.post(`${API}/me/tools`, async ({ request }) => {
    await delay(SIMULATED_LATENCY_MS);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    const body = (await request.json()) as {
      name?: string;
      summary?: string;
      description_md?: string;
      tool_type?: ToolType;
      category_id?: number | null;
      tags?: string[];
      visibility?: Visibility;
      webapp_url?: string | null;
    };
    const fields: FieldError[] = [];
    const name = (body.name ?? "").trim();
    const summary = (body.summary ?? "").trim();
    const toolType = body.tool_type;
    if (!name || name.length > 128) {
      fields.push({ field: "name", message: "名称必填且不超过 128 个字符" });
    }
    if (!summary || summary.length > 500) {
      fields.push({ field: "summary", message: "简介必填且不超过 500 个字符" });
    }
    if (!toolType || !["file", "webapp", "skill", "prompt"].includes(toolType)) {
      fields.push({ field: "tool_type", message: "工具类型不合法" });
    }
    if (toolType === "webapp" && !(body.webapp_url ?? "").match(/^https?:\/\//)) {
      fields.push({ field: "webapp_url", message: "Webapp 类型必须填写 http(s):// 链接" });
    }
    if (body.category_id != null && categoryIdToSlug(body.category_id) === null) {
      fields.push({ field: "category_id", message: "分类不存在或已停用" });
    }
    const tags = body.tags ?? [];
    if (tags.length > 8) fields.push({ field: "tags", message: "标签最多 8 个" });
    if (fields.length > 0) return validationError(fields);

    const record = makeNewRecord(user, {
      name,
      summary,
      description_md: body.description_md ?? "",
      tool_type: toolType as ToolType,
      category_slug: categoryIdToSlug(body.category_id) ?? "dev-tools",
      tags,
      visibility: body.visibility ?? "public",
      webapp_url: body.webapp_url ?? null,
    });
    return HttpResponse.json(toToolDetail(record, user), { status: 201 });
  }),

  http.get(`${API}/me/tools/:id`, async ({ request, params }) => {
    await delay(60);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    const record = toolRecords.find((item) => item.seed.id === Number(params.id));
    if (!record || record.deleted_at) return notFound("工具不存在或已被删除");
    if (!isOwner(record, user) && !isApprover(user)) return forbidden("只能查看自己的工具");
    return HttpResponse.json(toToolDetail(record, user));
  }),

  http.patch(`${API}/me/tools/:id`, async ({ request, params }) => {
    await delay(80);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    const record = toolRecords.find((item) => item.seed.id === Number(params.id));
    if (!record || record.deleted_at) return notFound("工具不存在或已被删除");
    const guard = ensureEditable(record, user);
    if (guard) return guard;

    const body = (await request.json()) as {
      name?: string;
      summary?: string;
      description_md?: string;
      category_id?: number | null;
      tags?: string[];
      visibility?: Visibility;
      webapp_url?: string | null;
    };
    const fields: FieldError[] = [];
    if (body.name !== undefined) {
      if (!body.name.trim() || body.name.length > 128) {
        fields.push({ field: "name", message: "名称必填且不超过 128 个字符" });
      }
    }
    if (body.summary !== undefined && (!body.summary.trim() || body.summary.length > 500)) {
      fields.push({ field: "summary", message: "简介必填且不超过 500 个字符" });
    }
    if (body.category_id != null && categoryIdToSlug(body.category_id) === null) {
      fields.push({ field: "category_id", message: "分类不存在或已停用" });
    }
    if ((body.tags ?? []).length > 8) fields.push({ field: "tags", message: "标签最多 8 个" });
    if (fields.length > 0) return validationError(fields);

    if (body.name !== undefined) record.seed.name = body.name.trim();
    if (body.summary !== undefined) record.seed.summary = body.summary.trim();
    if (body.description_md !== undefined) record.seed.description_md = body.description_md;
    if (body.category_id !== undefined) {
      record.seed.category_slug = categoryIdToSlug(body.category_id) ?? record.seed.category_slug;
    }
    if (body.tags !== undefined) record.seed.tags = [...body.tags];
    if (body.visibility !== undefined) {
      record.acl_visibility = body.visibility;
      record.seed.visibility = body.visibility;
    }
    if (body.webapp_url !== undefined) {
      record.webapp_url = body.webapp_url;
      record.seed.webapp_url = body.webapp_url;
    }
    record.seed.updated_at = nowIso();
    return HttpResponse.json(toToolDetail(record, user));
  }),

  http.delete(`${API}/me/tools/:id`, async ({ request, params }) => {
    await delay(60);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    const record = toolRecords.find((item) => item.seed.id === Number(params.id));
    if (!record || record.deleted_at) return notFound("工具不存在或已被删除");
    if (!isOwner(record, user) && !isApprover(user)) return forbidden("只能删除自己的工具");
    record.deleted_at = nowIso();
    record.status = "offline";
    return HttpResponse.json({ status: "ok" } satisfies StatusResponse);
  }),

  http.post(`${API}/me/tools/:id/submit`, async ({ request, params }) => {
    await delay(120);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    const record = toolRecords.find((item) => item.seed.id === Number(params.id));
    if (!record || record.deleted_at) return notFound("工具不存在或已被删除");
    if (!isOwner(record, user)) return forbidden("只能提交自己的工具");
    if (record.status !== "draft" && record.status !== "rejected") {
      return errorResponse(409, "STATE_CONFLICT", "只有草稿或已驳回的工具可以提交");
    }
    if (record.versions.length === 0) {
      return errorResponse(409, "STATE_CONFLICT", "请先上传一个版本再提交审批");
    }

    const mode = settings.find((item) => item.key === "approval.mode")?.value;
    const whitelisted =
      settings.find((item) => item.key === "approval.whitelist_enabled")?.value === true &&
      whitelist.some((entry) => entry.user_id === user.id && entry.is_effective);
    if (mode === "auto_approve_all" || whitelisted) {
      const pending = pendingVersionOf(record) ?? record.versions[record.versions.length - 1];
      if (pending) {
        pending.status = "approved";
        pending.approved_at = nowIso();
      }
      record.status = "approved";
      record.seed.published_at = record.seed.published_at || nowIso();
      record.seed.updated_at = nowIso();
      if (!record.seed.current_version && pending) record.seed.current_version = pending.version;
      const approval = appendApprovalRecord({
        tool: record,
        action: "submit",
        from_status: "draft",
        to_status: "approved",
        version: pending,
        version_from_status: "pending",
        version_to_status: "approved",
        actor: user,
        is_automatic: true,
        auto_rule: mode === "auto_approve_all" ? "approval.mode=auto_approve_all" : "approval.whitelist",
        note: "命中自动放行规则，无需人工审批。",
      });
      const body: SubmitResponse = {
        tool_id: record.seed.id,
        status: record.status,
        version_seq: record.version_seq,
        auto_approved: true,
        auto_approved_rule:
          mode === "auto_approve_all" ? "approval.mode=auto_approve_all" : "approval.whitelist",
        approval_record_id: approval.id,
      };
      return HttpResponse.json(body);
    }

    const result = submitForReview(record, user);
    const body: SubmitResponse = {
      tool_id: record.seed.id,
      status: record.status,
      version_seq: record.version_seq,
      auto_approved: result.auto_approved,
      auto_approved_rule: null,
      approval_record_id: result.record_id,
    };
    return HttpResponse.json(body);
  }),

  http.post(`${API}/me/tools/:id/withdraw`, async ({ request, params }) => {
    await delay(100);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    const record = toolRecords.find((item) => item.seed.id === Number(params.id));
    if (!record || record.deleted_at) return notFound("工具不存在或已被删除");
    if (!isOwner(record, user)) return forbidden("只能撤回自己的工具");
    if (record.status !== "pending" && record.status !== "pending_update") {
      return errorResponse(409, "STATE_CONFLICT", "只有待审核的工具可以撤回");
    }
    const pending = pendingVersionOf(record);
    if (pending) {
      pending.status = "rejected";
      pending.reject_reason = null;
    }
    const fromStatus = record.status;
    const hadApproved = record.versions.some((version) => version.status === "approved");
    record.status = hadApproved ? "approved" : "draft";
    record.submission_type = null;
    const approval = appendApprovalRecord({
      tool: record,
      action: "withdraw",
      from_status: fromStatus,
      to_status: record.status,
      version: pending,
      version_from_status: "pending",
      version_to_status: pending ? "rejected" : null,
      actor: user,
      note: "作者主动撤回。",
    });
    record.submitted_at = null;
    const body: WithdrawResponse = {
      tool_id: record.seed.id,
      status: record.status,
      approval_record_id: approval.id,
    };
    return HttpResponse.json(body);
  }),

  http.put(`${API}/me/tools/:id/acl`, async ({ request, params }) => {
    await delay(120);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    const record = toolRecords.find((item) => item.seed.id === Number(params.id));
    if (!record || record.deleted_at) return notFound("工具不存在或已被删除");
    if (!isOwner(record, user) && !isApprover(user)) return forbidden("只能修改自己工具的授权");
    const body = (await request.json()) as {
      visibility?: Visibility;
      entries?: Array<{ subject_type: "user" | "group"; subject_id: number; can_download: boolean }>;
    };
    const visibility = body.visibility ?? record.acl_visibility;
    const entries = body.entries ?? [];

    if (visibility === "restricted" && entries.length === 0) {
      return errorResponse(400, "ACL_REQUIRED", "受限可见性至少需要 1 条授权记录");
    }
    const seen = new Set<string>();
    for (const entry of entries) {
      const key = `${entry.subject_type}:${entry.subject_id}`;
      if (seen.has(key)) {
        return errorResponse(400, "DUPLICATE_ENTRY", "授权名单中存在重复条目", {
          field: "entries",
          value: key,
        });
      }
      seen.add(key);
      if (entry.subject_type === "user" && entry.subject_id === ownerOf(record).id) {
        return errorResponse(400, "SELF_GRANT", "不能给工具所有者自己授权", {
          field: "entries",
          value: entry.subject_id,
        });
      }
      const exists =
        entry.subject_type === "user"
          ? findMockUserById(entry.subject_id) !== undefined
          : MOCK_GROUPS.some((group) => group.id === entry.subject_id);
      if (!exists) {
        return errorResponse(400, "SUBJECT_NOT_FOUND", "被授权的主体不存在或已停用", {
          field: "entries",
          value: entry.subject_id,
        });
      }
    }

    record.acl_visibility = visibility;
    record.seed.visibility = visibility;
    let nextAclId = record.acl.reduce((max, entry) => Math.max(max, entry.id), 0) + 1;
    record.acl = entries.map((entry) => ({
      id: nextAclId++,
      subject_type: entry.subject_type,
      subject_id: entry.subject_id,
      can_download: entry.can_download,
    }));
    record.seed.updated_at = nowIso();

    const permissions = permissionsFor(record, user);
    const body200: AclResponse = {
      tool_id: record.seed.id,
      visibility: record.acl_visibility,
      entries: record.acl.map((entry) => aclEntryOut(entry)),
      permissions: {
        can_edit: permissions.can_edit,
        can_download: permissions.can_download,
        can_manage_versions: permissions.can_manage_versions,
        can_view_acl: permissions.can_view_acl,
        can_delete: permissions.can_delete,
        can_submit: permissions.can_submit,
        can_approve: permissions.can_approve,
      },
      effective: record.acl_visibility === "restricted",
    };
    return HttpResponse.json(body200);
  }),

  /* ---- me: versions ---- */
  http.get(`${API}/me/tools/:id/versions`, async ({ request, params }) => {
    await delay(60);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    const record = toolRecords.find((item) => item.seed.id === Number(params.id));
    if (!record || record.deleted_at) return notFound("工具不存在或已被删除");
    if (!isOwner(record, user) && !isApprover(user)) return forbidden("只能查看自己工具的版本");
    return HttpResponse.json(
      [...record.versions]
        .sort((a, b) => b.created_at.localeCompare(a.created_at))
        .map((version) => toVersionSummary(record, version)),
    );
  }),

  /**
   * `POST /me/tools/{id}/versions` — multipart/form-data (docs/03 §3.7).
   *
   * Escape hatches for the editor's error paths:
   *   - a file whose name contains `broken-skill`, or a FormData field
   *     `simulate=bad-zip`, returns `422 SKILL_MD_NOT_FOUND`;
   *   - `simulate=zip-bomb` returns `422 ZIP_BOMB_DETECTED`;
   *   - `simulate=too-many-files` returns `422 ZIP_TOO_MANY_FILES`.
   * These exist only in the mock and are documented here so the frontend can
   * exercise the failure UI without a real malformed archive.
   */
  http.post(`${API}/me/tools/:id/versions`, async ({ request, params }) => {
    await delay(200);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    const record = toolRecords.find((item) => item.seed.id === Number(params.id));
    if (!record || record.deleted_at) return notFound("工具不存在或已被删除");
    const guard = ensureEditable(record, user);
    if (guard) return guard;

    const form = await request.formData();
    const versionString = String(form.get("version") ?? "").trim();
    const changelog = String(form.get("changelog_md") ?? "");
    const autoSubmit = String(form.get("auto_submit") ?? "false") === "true";
    const simulate = String(form.get("simulate") ?? "");
    const promptContent = form.get("prompt_content");
    const webappUrl = form.get("webapp_url");
    const file = form.get("file");
    const fileObj = typeof File !== "undefined" && file instanceof File ? file : null;
    const fileName = fileObj?.name ?? null;
    const fileSize = fileObj?.size ?? null;

    if (!versionString) {
      return validationError([{ field: "version", message: "版本号必填" }]);
    }
    if (!/^\d+\.\d+\.\d+/.test(versionString)) {
      return validationError([{ field: "version", message: "版本号需符合 x.y.z 格式" }]);
    }
    if (record.versions.some((version) => version.version === versionString)) {
      return errorResponse(409, "VERSION_EXISTS", `版本号 ${versionString} 已存在`, {
        field: "version",
        value: versionString,
      });
    }
    if (record.seed.tool_type === "file" || record.seed.tool_type === "skill") {
      if (!fileObj) return validationError([{ field: "file", message: "该类型必须上传文件" }]);
    }
    if (record.seed.tool_type === "prompt" && !String(promptContent ?? "").trim()) {
      return validationError([{ field: "prompt_content", message: "提示词内容必填" }]);
    }
    if (record.seed.tool_type === "webapp" && !String(webappUrl ?? "").match(/^https?:\/\//)) {
      return validationError([{ field: "webapp_url", message: "Webapp 类型必须填写 http(s):// 链接" }]);
    }

    if (simulate === "bad-zip" || (fileName ?? "").includes("broken-skill")) {
      return errorResponse(422, "SKILL_MD_NOT_FOUND", "包内未找到 SKILL.md", {
        searched: ["SKILL.md", "*/SKILL.md"],
      });
    }
    if (simulate === "zip-bomb") {
      return errorResponse(422, "ZIP_BOMB_DETECTED", "压缩包解压后体积超过限制", {
        limit_mb: 1024,
        detected_mb: 10240,
      });
    }
    if (simulate === "too-many-files") {
      return errorResponse(422, "ZIP_TOO_MANY_FILES", "包内文件数超过限制", {
        limit: 5000,
        count: 12000,
      });
    }
    if (fileSize !== null && fileSize > 200 * 1024 * 1024) {
      return errorResponse(413, "PAYLOAD_TOO_LARGE", "文件超过 200MB 上限", {
        limit_mb: 200,
        actual_mb: Math.round(fileSize / 1024 / 1024),
      });
    }

    const ext = fileName ? extOf(fileName) : record.seed.tool_type === "prompt" ? "md" : null;
    const version = createVersion(record, user, {
      version: versionString,
      changelog_md: changelog || "（未填写变更说明）",
      fileName,
      fileSize,
      fileSha256: fileObj ? fakeSha256(`${record.seed.slug}|${versionString}|${fileSize}`) : null,
      fileExt: ext,
      mimeType: mimeOf(ext ?? ""),
      promptContent: typeof promptContent === "string" ? promptContent : null,
    });
    if (webappUrl) {
      record.webapp_url = String(webappUrl);
      record.seed.webapp_url = String(webappUrl);
    }
    record.version_seq += 1;
    record.seed.updated_at = nowIso();

    if (autoSubmit) {
      submitForReview(record, user, { newVersion: version });
    } else if (record.status === "approved" || record.status === "pending_update") {
      // FR-VER-03：已发布工具上传新版本 → pending_update，**旧版本继续服务**。
      record.status = "pending_update";
      record.submission_type = "new_version";
      record.submitted_at = nowIso();
    } else {
      // 草稿 / 驳回状态下传到版本仍保持原状态：提交审批是独立的显式动作
      // （docs/01 §4.1 的状态机；与 backend/app/services/version_service.py 一致）。
      record.submission_type = "new_tool";
    }

    const skill: SkillVersionInfo | null =
      record.seed.tool_type === "skill"
        ? {
            manifest: { ...MOCK_SKILL_MANIFEST, name: record.seed.slug, version: version.version },
            file_tree_summary: skillTreeSummary(buildSkillFileTree(record.seed.name)),
            parse_error: null,
          }
        : null;
    const body: VersionUploadResponse = {
      id: version.id,
      tool_id: record.seed.id,
      version: version.version,
      status: version.status,
      file_name: version.file_name,
      file_size: version.file_size,
      file_sha256: version.file_sha256,
      prompt_content: version.prompt_content,
      skill,
      tool_status: record.status,
      created_at: version.created_at,
    };
    return HttpResponse.json(body, { status: 201 });
  }),

  http.patch(`${API}/me/tools/:id/versions/:version`, async ({ request, params }) => {
    await delay(80);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    const record = toolRecords.find((item) => item.seed.id === Number(params.id));
    if (!record || record.deleted_at) return notFound("工具不存在或已被删除");
    const guard = ensureEditable(record, user);
    if (guard) return guard;
    const version = record.versions.find((item) => item.version === String(params.version));
    if (!version) return notFound("版本不存在");
    const body = (await request.json()) as { changelog_md?: string };
    if (typeof body.changelog_md !== "string") {
      return validationError([{ field: "changelog_md", message: "变更说明必填" }]);
    }
    version.changelog_md = body.changelog_md;
    return HttpResponse.json(toVersionSummary(record, version));
  }),

  http.delete(`${API}/me/tools/:id/versions/:version`, async ({ request, params }) => {
    await delay(80);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    const record = toolRecords.find((item) => item.seed.id === Number(params.id));
    if (!record || record.deleted_at) return notFound("工具不存在或已被删除");
    const guard = ensureEditable(record, user);
    if (guard) return guard;
    const index = record.versions.findIndex((item) => item.version === String(params.version));
    const version = record.versions[index];
    if (!version) return notFound("版本不存在");
    if (version.status === "approved" || version.status === "superseded") {
      return errorResponse(409, "STATE_CONFLICT", "已发布的历史版本不能删除");
    }
    record.versions.splice(index, 1);
    if (version.status === "pending") {
      const hadApproved = record.versions.some((item) => item.status === "approved");
      record.status = hadApproved ? "approved" : "draft";
      record.submitted_at = null;
      record.submission_type = null;
    }
    return HttpResponse.json({ status: "ok" } satisfies StatusResponse);
  }),

  /* ---- me: images ---- */
  http.post(`${API}/me/tools/:id/images`, async ({ request, params }) => {
    await delay(160);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    const record = toolRecords.find((item) => item.seed.id === Number(params.id));
    if (!record || record.deleted_at) return notFound("工具不存在或已被删除");
    if (!isOwner(record, user) && !isApprover(user)) return forbidden("只能修改自己工具的图片");
    const form = await request.formData();
    const file = form.get("file");
    const fileObj = typeof File !== "undefined" && file instanceof File ? file : null;
    if (!fileObj) return validationError([{ field: "file", message: "请选择图片文件" }]);
    const kindParam = String(form.get("kind") ?? "");
    const kind = kindParam === "screenshot" ? "screenshot" : "cover";
    const ext = extOf(fileObj.name);
    const mime = mimeOf(ext);
    if (mime === null || !mime.startsWith("image/")) {
      return errorResponse(415, "UNSUPPORTED_MEDIA_TYPE", "只支持图片文件", {
        ext,
        allowed: ["png", "jpg", "jpeg", "webp", "svg"],
      });
    }
    if (kind === "cover") {
      for (const image of record.images) {
        if (image.kind === "cover") image.kind = "screenshot";
      }
    }
    const id = nextImageId++;
    const image: ToolImage = {
      id,
      kind,
      url: imageUrl(id, "orig"),
      thumb_url: imageUrl(id, "thumb"),
      file_name: fileObj.name,
      mime_type: mime,
      file_size: fileObj.size,
      width: kind === "cover" ? 1200 : 1440,
      height: kind === "cover" ? 630 : 900,
      sort_order: record.images.length,
      alt_text: null,
      sha256: fakeSha256(`${record.seed.slug}|image|${id}`).slice(0, 16),
      created_at: nowIso(),
    };
    record.images.push(image);
    return HttpResponse.json(image, { status: 201 });
  }),

  http.patch(`${API}/me/tools/:id/images/:imageId`, async ({ request, params }) => {
    await delay(80);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    const record = toolRecords.find((item) => item.seed.id === Number(params.id));
    if (!record || record.deleted_at) return notFound("工具不存在或已被删除");
    if (!isOwner(record, user) && !isApprover(user)) return forbidden("只能修改自己工具的图片");
    const image = record.images.find((item) => item.id === Number(params.imageId));
    if (!image) return notFound("图片不存在");
    const body = (await request.json()) as ImagePatchRequest;
    if (body.sort_order !== undefined) image.sort_order = body.sort_order;
    if (body.alt_text !== undefined) image.alt_text = body.alt_text;
    if (body.set_as_cover === true) {
      for (const item of record.images) item.kind = item.id === image.id ? "cover" : "screenshot";
    }
    return HttpResponse.json(image);
  }),

  http.delete(`${API}/me/tools/:id/images/:imageId`, async ({ request, params }) => {
    await delay(80);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    const record = toolRecords.find((item) => item.seed.id === Number(params.id));
    if (!record || record.deleted_at) return notFound("工具不存在或已被删除");
    if (!isOwner(record, user) && !isApprover(user)) return forbidden("只能修改自己工具的图片");
    const index = record.images.findIndex((item) => item.id === Number(params.imageId));
    if (index < 0) return notFound("图片不存在");
    record.images.splice(index, 1);
    // Coverless tools fall back to the placeholder block; keep sort order dense.
    record.images.forEach((item, order) => {
      item.sort_order = order;
    });
    return HttpResponse.json({ status: "ok" } satisfies StatusResponse);
  }),

  /* ---- admin: approvals ---- */
  http.get(`${API}/admin/approvals`, async ({ request }) => {
    await delay(SIMULATED_LATENCY_MS);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    if (!isApprover(user)) return forbidden("只有审批人可以访问审批队列");
    const params = new URL(request.url).searchParams;
    const status = (params.get("status") ?? "pending").trim();
    const toolType = params.get("tool_type") as ToolType | null;
    const owner = params.get("owner");
    const { page, pageSize } = pageOf(request);

    // 与真实后端一致：pending_all（或省略）= pending + pending_update
    const rawStatuses = status
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
    const wanted =
      rawStatuses.length === 0 || rawStatuses.includes("pending_all")
        ? new Set(["pending", "pending_update"])
        : new Set(rawStatuses);
    const queued = toolRecords
      .filter((record) => !record.deleted_at)
      .filter((record) => {
        if (wanted.has("all") || wanted.size === 0) return true;
        return wanted.has(record.status);
      })
      .filter((record) => !toolType || record.seed.tool_type === toolType)
      .filter((record) => !owner || record.seed.owner_username === owner)
      // Oldest submission first — the queue is a FIFO backlog (FR-APPR-05).
      .sort((a, b) => {
        const left = a.submitted_at ?? "";
        const right = b.submitted_at ?? "";
        if (!left && !right) return a.seed.id - b.seed.id;
        if (!left) return 1;
        if (!right) return -1;
        return left.localeCompare(right);
      })
      .map(toQueueItem);

    return HttpResponse.json(paginate(queued, page, pageSize));
  }),

  http.post(`${API}/admin/approvals/:toolId/approve`, async ({ request, params }) => {
    await delay(150);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    if (!isApprover(user)) return forbidden("只有审批人可以批准");
    const record = toolRecords.find((item) => item.seed.id === Number(params.toolId));
    if (!record || record.deleted_at) return notFound("工具不存在或已被删除");
    if (!isQueued(record)) {
      return errorResponse(409, "ALREADY_PROCESSED", "该条目已被处理，请刷新列表");
    }
    const body = (await request.json().catch(() => ({}))) as {
      version_id?: number | null;
      note?: string | null;
      expected_version_seq?: number | null;
    };
    if (
      body.expected_version_seq !== undefined &&
      body.expected_version_seq !== null &&
      body.expected_version_seq !== record.version_seq
    ) {
      return errorResponse(409, "ALREADY_PROCESSED", "版本序号已变化，请刷新后重试");
    }
    const pending = pendingVersionOf(record);
    if (!pending) return errorResponse(409, "STATE_CONFLICT", "没有待审版本");
    if (body.version_id != null && body.version_id !== pending.id) {
      return errorResponse(409, "ALREADY_PROCESSED", "待审版本已变化，请刷新后重试");
    }

    const previous = currentVersionOf(record);
    if (previous) {
      previous.status = "superseded";
    }
    pending.status = "approved";
    pending.approved_at = nowIso();
    record.status = "approved";
    record.version_seq += 1;
    record.seed.current_version = pending.version;
    record.seed.published_at = record.seed.published_at || nowIso();
    record.seed.updated_at = nowIso();
    record.last_version_at = pending.approved_at;
    record.submission_type = null;
    record.submitted_at = null;
    record.reject_reason = null;

    const approval = appendApprovalRecord({
      tool: record,
      action: "approve",
      from_status: "pending",
      to_status: "approved",
      version: pending,
      version_from_status: "pending",
      version_to_status: "approved",
      actor: user,
      note: body.note ?? null,
    });
    const responseBody: ApproveResponse = {
      tool_id: record.seed.id,
      status: record.status,
      version_seq: record.version_seq,
      current_version: toCurrentVersionBrief(pending),
      superseded_version: toSupersededBrief(previous),
      purged_versions: [],
      approval_record_id: approval.id,
    };
    return HttpResponse.json(responseBody);
  }),

  http.post(`${API}/admin/approvals/:toolId/reject`, async ({ request, params }) => {
    await delay(150);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    if (!isApprover(user)) return forbidden("只有审批人可以驳回");
    const record = toolRecords.find((item) => item.seed.id === Number(params.toolId));
    if (!record || record.deleted_at) return notFound("工具不存在或已被删除");
    if (!isQueued(record)) {
      return errorResponse(409, "ALREADY_PROCESSED", "该条目已被处理，请刷新列表");
    }
    const body = (await request.json().catch(() => ({}))) as {
      reason?: string;
      version_id?: number | null;
    };
    const reason = (body.reason ?? "").trim();
    if (reason.length < 5) {
      // FR-APPR-08 / docs/03 §3.10: reason is required, 5~2000 chars.
      return validationError([
        { field: "reason", message: "驳回理由不能少于 5 个字", value_length: reason.length },
      ]);
    }
    if (reason.length > 2000) {
      return validationError([
        { field: "reason", message: "驳回理由不能超过 2000 个字", value_length: reason.length },
      ]);
    }

    const pending = pendingVersionOf(record);
    if (!pending) return errorResponse(409, "STATE_CONFLICT", "没有待审版本");
    pending.status = "rejected";
    pending.reject_reason = reason;
    record.reject_reason = reason;
    record.seed.updated_at = nowIso();

    // docs/03 §3.10: rejecting a *new version* leaves the tool published; only a
    // first submission turns the tool itself into `rejected`.
    const hasApproved = record.versions.some((version) => version.status === "approved");
    const fromStatus = record.status;
    record.status = hasApproved ? "approved" : "rejected";
    record.submission_type = null;
    record.submitted_at = null;

    const approval = appendApprovalRecord({
      tool: record,
      action: "reject",
      from_status: fromStatus,
      to_status: record.status,
      version: pending,
      version_from_status: "pending",
      version_to_status: "rejected",
      actor: user,
      reason,
    });
    const responseBody = {
      tool_id: record.seed.id,
      status: record.status,
      pending_version: null,
      rejected_version: toSupersededBrief(pending),
      approval_record_id: approval.id,
    };
    return HttpResponse.json(responseBody);
  }),

  http.post(`${API}/admin/approvals/:toolId/offline`, async ({ request, params }) => {
    await delay(120);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    if (!isApprover(user)) return forbidden("只有审批人可以下架");
    const record = toolRecords.find((item) => item.seed.id === Number(params.toolId));
    if (!record || record.deleted_at) return notFound("工具不存在或已被删除");
    const body = (await request.json().catch(() => ({}))) as { reason?: string };
    const reason = (body.reason ?? "").trim();
    if (reason.length < 5) {
      return validationError([
        { field: "reason", message: "下架理由不能少于 5 个字", value_length: reason.length },
      ]);
    }
    const fromStatus = record.status;
    record.status = "offline";
    record.offline_reason = reason;
    const approval = appendApprovalRecord({
      tool: record,
      action: "offline",
      from_status: fromStatus,
      to_status: "offline",
      version: currentVersionOf(record),
      version_from_status: "approved",
      version_to_status: "approved",
      actor: user,
      reason,
    });
    const responseBody: ApproveResponse = {
      tool_id: record.seed.id,
      status: record.status,
      version_seq: record.version_seq,
      current_version: toCurrentVersionBrief(currentVersionOf(record)),
      superseded_version: null,
      purged_versions: [],
      approval_record_id: approval.id,
    };
    return HttpResponse.json(responseBody);
  }),

  http.post(`${API}/admin/approvals/:toolId/relist`, async ({ request, params }) => {
    await delay(120);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    if (!isApprover(user)) return forbidden("只有审批人可以重新上架");
    const record = toolRecords.find((item) => item.seed.id === Number(params.toolId));
    if (!record || record.deleted_at) return notFound("工具不存在或已被删除");
    if (record.status !== "offline") {
      return errorResponse(409, "STATE_CONFLICT", "只有已下架的工具可以重新上架");
    }
    const body = (await request.json().catch(() => ({}))) as { reason?: string | null };
    record.status = "approved";
    record.offline_reason = null;
    record.seed.updated_at = nowIso();
    const approval = appendApprovalRecord({
      tool: record,
      action: "relist",
      from_status: "offline",
      to_status: "approved",
      version: currentVersionOf(record),
      version_from_status: "approved",
      version_to_status: "approved",
      actor: user,
      reason: body.reason ?? null,
    });
    const responseBody: ApproveResponse = {
      tool_id: record.seed.id,
      status: record.status,
      version_seq: record.version_seq,
      current_version: toCurrentVersionBrief(currentVersionOf(record)),
      superseded_version: null,
      purged_versions: [],
      approval_record_id: approval.id,
    };
    return HttpResponse.json(responseBody);
  }),

  http.post(`${API}/admin/approvals/batch-approve`, async ({ request }) => {
    await delay(220);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    if (!isApprover(user)) return forbidden("只有审批人可以批量批准");
    const body = (await request.json().catch(() => ({}))) as {
      tool_ids?: number[];
      note?: string | null;
    };
    const ids = body.tool_ids ?? [];
    if (ids.length === 0) {
      return validationError([{ field: "tool_ids", message: "至少选择一个条目" }]);
    }

    const results: BatchApproveResponse["results"] = [];
    for (const toolId of ids) {
      const record = toolRecords.find((item) => item.seed.id === toolId);
      if (!record || record.deleted_at) {
        results.push({
          tool_id: toolId,
          ok: false,
          status: null,
          error_code: "NOT_FOUND",
          message: "工具不存在或已被删除",
        });
        continue;
      }
      if (!isQueued(record)) {
        results.push({
          tool_id: toolId,
          ok: false,
          status: record.status,
          error_code: "ALREADY_PROCESSED",
          message: "该条目已被处理",
        });
        continue;
      }
      const pending = pendingVersionOf(record);
      if (!pending) {
        results.push({
          tool_id: toolId,
          ok: false,
          status: record.status,
          error_code: "STATE_CONFLICT",
          message: "没有待审版本",
        });
        continue;
      }
      const previous = currentVersionOf(record);
      if (previous) previous.status = "superseded";
      pending.status = "approved";
      pending.approved_at = nowIso();
      record.status = "approved";
      record.version_seq += 1;
      record.seed.current_version = pending.version;
      record.seed.published_at = record.seed.published_at || nowIso();
      record.seed.updated_at = nowIso();
      record.last_version_at = pending.approved_at;
      record.submission_type = null;
      record.submitted_at = null;
      appendApprovalRecord({
        tool: record,
        action: "approve",
        from_status: "pending",
        to_status: "approved",
        version: pending,
        version_from_status: "pending",
        version_to_status: "approved",
        actor: user,
        note: body.note ?? "批量批准",
      });
      results.push({
        tool_id: toolId,
        ok: true,
        status: record.status,
        error_code: null,
        message: null,
      });
    }

    const responseBody: BatchApproveResponse = {
      succeeded: results.filter((item) => item.ok).length,
      failed: results.filter((item) => !item.ok).length,
      results,
    };
    return HttpResponse.json(responseBody);
  }),

  http.get(`${API}/admin/approvals/history`, async ({ request }) => {
    await delay(100);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    if (!isApprover(user)) return forbidden("只有审批人可以查看审批历史");
    const params = new URL(request.url).searchParams;
    const filters: ApprovalHistoryParams = {
      tool_id: params.get("tool_id") ? Number(params.get("tool_id")) : undefined,
      actor_id: params.get("actor_id") ? Number(params.get("actor_id")) : undefined,
      action: (params.get("action") as ApprovalHistoryParams["action"]) ?? undefined,
      date_from: params.get("date_from") ?? undefined,
      date_to: params.get("date_to") ?? undefined,
    };
    const { page, pageSize } = pageOf(request);

    const items = approvalRecords
      .filter((record) => !filters.tool_id || record.tool_id === filters.tool_id)
      .filter((record) => !filters.actor_id || record.actor_id === filters.actor_id)
      .filter((record) => !filters.action || record.action === filters.action)
      .filter((record) => !filters.date_from || record.created_at >= filters.date_from)
      .filter((record) => !filters.date_to || record.created_at <= filters.date_to)
      .sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id - a.id)
      .map(toApprovalRecord);
    return HttpResponse.json(paginate(items, page, pageSize));
  }),

  /* ---- admin: whitelist ---- */
  http.get(`${API}/admin/approval-whitelist`, async ({ request }) => {
    await delay(60);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    if (!user.roles.includes("superadmin")) return forbidden("只有超级管理员可以查看免审白名单");
    return HttpResponse.json(whitelist.map((entry) => ({ ...entry })));
  }),

  http.post(`${API}/admin/approval-whitelist`, async ({ request }) => {
    await delay(80);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    if (!user.roles.includes("superadmin")) return forbidden("只有超级管理员可以维护免审白名单");
    const body = (await request.json()) as {
      user_id?: number;
      reason?: string | null;
      expires_at?: string | null;
    };
    if (typeof body.user_id !== "number") {
      return validationError([{ field: "user_id", message: "请选择用户" }]);
    }
    const target = findMockUserById(body.user_id);
    if (!target) {
      return errorResponse(400, "SUBJECT_NOT_FOUND", "用户不存在或已停用", {
        field: "user_id",
        value: body.user_id,
      });
    }
    if (whitelist.some((entry) => entry.user_id === body.user_id)) {
      return errorResponse(400, "DUPLICATE_ENTRY", "该用户已在免审白名单中", {
        field: "user_id",
        value: body.user_id,
      });
    }
    const entry: MockWhitelistEntry = {
      user_id: target.id,
      username: target.username,
      display_name: target.display_name,
      reason: body.reason ?? null,
      added_by_id: user.id,
      added_by_name: user.display_name,
      expires_at: body.expires_at ?? null,
      created_at: nowIso(),
      is_effective: true,
    };
    whitelist.push(entry);
    return HttpResponse.json({ ...entry }, { status: 201 });
  }),

  http.delete(`${API}/admin/approval-whitelist/:userId`, async ({ request, params }) => {
    await delay(60);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    if (!user.roles.includes("superadmin")) return forbidden("只有超级管理员可以维护免审白名单");
    const index = whitelist.findIndex((entry) => entry.user_id === Number(params.userId));
    if (index < 0) return notFound("白名单记录不存在");
    whitelist.splice(index, 1);
    return HttpResponse.json({ status: "ok" } satisfies StatusResponse);
  }),

  /* ---- admin: settings ---- */
  http.get(`${API}/admin/settings`, async ({ request }) => {
    await delay(80);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    if (!user.roles.includes("superadmin")) return forbidden("只有超级管理员可以查看系统设置");
    return HttpResponse.json(settingListResponse());
  }),

  http.put(`${API}/admin/settings`, async ({ request }) => {
    await delay(140);
    const user = authenticate(request);
    if (!user) return tokenExpired();
    if (!user.roles.includes("superadmin")) return forbidden("只有超级管理员可以修改系统设置");
    const body = (await request.json().catch(() => ({}))) as {
      items?: Array<{ key: string; value: unknown }>;
    };
    const items = body.items ?? [];
    if (items.length === 0) {
      return validationError([{ field: "items", message: "至少提交一个设置项" }]);
    }

    // Atomic: validate the whole batch before mutating anything (docs/03 §3.13).
    for (const item of items) {
      const setting = settings.find((entry) => entry.key === item.key);
      if (!setting) {
        return errorResponse(400, "SETTING_INVALID", `未知的设置项：${item.key}`, {
          field: "key",
          value: item.key,
        });
      }
      if (setting.value_type === "bool" && typeof item.value !== "boolean") {
        return errorResponse(400, "SETTING_INVALID", `${item.key} 需要布尔值`, {
          field: item.key,
          value: item.value,
        });
      }
      if (setting.value_type === "int" && typeof item.value !== "number") {
        return errorResponse(400, "SETTING_INVALID", `${item.key} 需要整数`, {
          field: item.key,
          value: item.value,
        });
      }
      if (setting.options && !setting.options.includes(String(item.value))) {
        return errorResponse(400, "SETTING_INVALID", `${item.key} 的取值不在允许范围内`, {
          field: item.key,
          value: item.value,
          options: setting.options,
        });
      }
      if (
        setting.value_type === "string" &&
        !setting.options &&
        typeof item.value !== "string"
      ) {
        return errorResponse(400, "SETTING_INVALID", `${item.key} 需要字符串`, {
          field: item.key,
          value: item.value,
        });
      }
    }

    const warnings: SettingWarning[] = [];
    for (const item of items) {
      const setting = settings.find((entry) => entry.key === item.key);
      if (!setting) continue;
      const before = setting.value;
      setting.value = item.value;
      setting.updated_at = nowIso();
      setting.updated_by_id = user.id;
      if (
        setting.key === "approval.mode" &&
        item.value === "auto_approve_all" &&
        before !== "auto_approve_all"
      ) {
        const pendingCount = pendingSubmissionCount();
        warnings.push({
          code: "PENDING_ITEMS_NOT_AFFECTED",
          message: `当前有 ${pendingCount} 个待审条目，切换为全部放行不会自动处理它们。`,
          pending_count: pendingCount,
        });
      }
    }
    return HttpResponse.json(settingListResponse(warnings));
  }),
];

/** Exported for the mock self-checks (seed spec conformance). */
export const MOCK_SEED_NOTES = {
  longToolNameLength: LONG_TOOL_NAME.length,
  toolsWithoutCover: MOCK_TOOLS.filter((tool) => tool.cover_url === null).length,
  totalTools: MOCK_TOOLS.length,
  waitingHours: {
    101: waitingHoursFor(101, "2025-03-13T20:00:00Z"),
    103: waitingHoursFor(103, "2025-03-14T03:00:00Z"),
    106: waitingHoursFor(106, "2025-03-12T02:00:00Z"),
    202: waitingHoursFor(202, "2025-03-13T22:00:00Z"),
    205: waitingHoursFor(205, "2025-03-07T00:00:00Z"),
    207: waitingHoursFor(207, "2025-03-13T04:00:00Z"),
    208: waitingHoursFor(208, "2025-03-14T05:00:00Z"),
  },
  coverImageIds: MOCK_TOOLS.map((tool) => coverImageId(tool.id)),
  extraSubmissionIds: EXTRA_SUBMISSION_SPECS.map((spec) => spec.id),
};
