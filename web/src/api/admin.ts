import { API_BASE, ApiError, getAccessToken, request, upload, type QueryValue } from "./client";
import type {
  AdminCategoryCreateRequest,
  AdminCategoryOut,
  AdminCategoryUpdateRequest,
  AdminRoleReplaceRequest,
  AdminTagListResponse,
  AdminTagOut,
  AdminToolItem,
  AdminToolListParams,
  AdminToolListResponse,
  AdminUserCreateRequest,
  AdminUserCreateResponse,
  AdminUserItem,
  AdminUserListParams,
  AdminUserListResponse,
  AdminUserUpdateRequest,
  AdminOverviewResponse,
  ApiTokenCreateRequest,
  ApiTokenCreateResponse,
  ApiTokenListResponse,
  ApiTokenOut,
  ApprovalHistoryParams,
  ApprovalQueueItem,
  ApprovalQueueParams,
  ApprovalRecord,
  ApproveRequest,
  CategoryOrderRequest,
  GroupCreateRequest,
  GroupListResponse,
  GroupMemberAddRequest,
  GroupMemberAddResponse,
  GroupMemberListResponse,
  GroupOut,
  GroupUpdateRequest,
  ImportOnConflict,
  ImportResultResponse,
  PurgeToolResponse,
  ResetPasswordRequest,
  ResetPasswordResponse,
  RevokeSessionsResponse,
  RoleOut,
  StorageStatsResponse,
  TagCleanupResponse,
  TagMergeRequest,
  TagMergeResponse,
  TagRenameRequest,
  ToolImportRequest,
  ToolRankResponse,
  TransferOwnerRequest,
  TransferOwnerResponse,
  ApproveResponse,
  BatchApproveRequest,
  BatchApproveResponse,
  OfflineRequest,
  OfflineResponse,
  Paginated,
  RejectRequest,
  RelistResponse,
  RejectResponse,
  SettingListResponse,
  SettingUpdateRequest,
  WhitelistEntry,
  WhitelistEntryInput,
} from "./types";

/**
 * 审批与治理（docs/03 §3.8 ~ §3.13, CONTRACT §6.1）。
 *
 * Everything here requires `approver` or `superadmin`; whitelist and settings are
 * superadmin-only. The routes are additionally wrapped in `RequireRole`, which is
 * UI convenience only — the server is the authority (docs/04 §4).
 */

/* -------------------------------------------------------------------------- */
/* 审批队列                                                                    */
/* -------------------------------------------------------------------------- */

/**
 * 队列默认取 `pending_all`。
 *
 * 真实后端（`backend/app/repositories/approvals.py`）把「待处理」定义为
 * `pending + pending_update`，并提供 `pending_all` 这个显式取值；docs/03 §3.8
 * 只写了「默认 pending」，但传 `status=pending` 会**漏掉新版本待审条目**
 * （已报回监控方回填文档）。
 */
export function normalizeQueueParams(params: ApprovalQueueParams = {}) {
  return {
    status: params.status ?? "pending_all",
    tool_type: params.tool_type ?? "",
    owner: params.owner ?? "",
    page: params.page ?? 1,
    pageSize: params.page_size ?? 20,
  };
}

export function approvalsQueryKey(params: ApprovalQueueParams = {}) {
  return ["admin", "approvals", normalizeQueueParams(params)] as const;
}

/** GET /admin/approvals — oldest submission first (FR-APPR-05). */
export function fetchApprovals(
  params: ApprovalQueueParams = {},
  signal?: AbortSignal,
): Promise<Paginated<ApprovalQueueItem>> {
  const normalized = normalizeQueueParams(params);
  const query: Record<string, QueryValue> = {
    status: normalized.status,
    tool_type: normalized.tool_type || undefined,
    owner: normalized.owner || undefined,
    page: normalized.page,
    page_size: normalized.pageSize,
  };
  return request<Paginated<ApprovalQueueItem>>("/admin/approvals", { query, signal });
}

/** POST /admin/approvals/{tool_id}/approve (docs/03 §3.9). */
export function approveTool(
  toolId: number,
  payload: ApproveRequest = {},
): Promise<ApproveResponse> {
  return request<ApproveResponse>(`/admin/approvals/${toolId}/approve`, {
    method: "POST",
    body: payload,
  });
}

/** POST /admin/approvals/{tool_id}/reject — `reason` 5~2000 chars (docs/03 §3.10). */
export function rejectTool(toolId: number, payload: RejectRequest): Promise<RejectResponse> {
  return request<RejectResponse>(`/admin/approvals/${toolId}/reject`, {
    method: "POST",
    body: payload,
  });
}

/**
 * POST /admin/approvals/{tool_id}/offline — reason required (FR-APPR-10).
 *
 * 响应是 `OfflineResponse`（没有 `version_seq`），**不是** `ApproveResponse`
 * —— 下架不产生新版本，用错类型会让 `version_seq` 在运行时是 undefined。
 */
export function offlineTool(toolId: number, payload: OfflineRequest): Promise<OfflineResponse> {
  return request<OfflineResponse>(`/admin/approvals/${toolId}/offline`, {
    method: "POST",
    body: payload,
  });
}

/** POST /admin/approvals/{tool_id}/relist — reason optional (FR-APPR-11)；响应是 RelistResponse。 */
export function relistTool(
  toolId: number,
  payload: { reason?: string | null } = {},
): Promise<RelistResponse> {
  return request<RelistResponse>(`/admin/approvals/${toolId}/relist`, {
    method: "POST",
    body: payload,
  });
}

/** POST /admin/approvals/batch-approve — there is deliberately no batch reject. */
export function batchApprove(payload: BatchApproveRequest): Promise<BatchApproveResponse> {
  return request<BatchApproveResponse>("/admin/approvals/batch-approve", {
    method: "POST",
    body: payload,
  });
}

/* -------------------------------------------------------------------------- */
/* 审批历史                                                                    */
/* -------------------------------------------------------------------------- */

export function normalizeHistoryParams(params: ApprovalHistoryParams = {}) {
  return {
    tool_id: params.tool_id ?? 0,
    actor_id: params.actor_id ?? 0,
    action: params.action ?? "",
    date_from: params.date_from ?? "",
    date_to: params.date_to ?? "",
    page: params.page ?? 1,
    pageSize: params.page_size ?? 20,
  };
}

export function approvalHistoryQueryKey(params: ApprovalHistoryParams = {}) {
  return ["admin", "approval-history", normalizeHistoryParams(params)] as const;
}

/** GET /admin/approvals/history (FR-APPR-12). */
export function fetchApprovalHistory(
  params: ApprovalHistoryParams = {},
  signal?: AbortSignal,
): Promise<Paginated<ApprovalRecord>> {
  const normalized = normalizeHistoryParams(params);
  const query: Record<string, QueryValue> = {
    tool_id: normalized.tool_id || undefined,
    actor_id: normalized.actor_id || undefined,
    action: normalized.action || undefined,
    date_from: normalized.date_from || undefined,
    date_to: normalized.date_to || undefined,
    page: normalized.page,
    page_size: normalized.pageSize,
  };
  return request<Paginated<ApprovalRecord>>("/admin/approvals/history", { query, signal });
}

/* -------------------------------------------------------------------------- */
/* 免审白名单（superadmin）                                                    */
/* -------------------------------------------------------------------------- */

export const whitelistQueryKey = ["admin", "whitelist"] as const;

/** GET /admin/approval-whitelist — bare array. */
export function fetchWhitelist(signal?: AbortSignal): Promise<WhitelistEntry[]> {
  return request<WhitelistEntry[]>("/admin/approval-whitelist", { signal });
}

/** POST /admin/approval-whitelist. */
export function addWhitelistEntry(payload: WhitelistEntryInput): Promise<WhitelistEntry> {
  return request<WhitelistEntry>("/admin/approval-whitelist", {
    method: "POST",
    body: payload,
  });
}

/** DELETE /admin/approval-whitelist/{user_id}. */
export function removeWhitelistEntry(userId: number): Promise<null> {
  return request<null>(`/admin/approval-whitelist/${userId}`, { method: "DELETE" });
}

/* -------------------------------------------------------------------------- */
/* 系统设置（M2 只做审批分组）                                                 */
/* -------------------------------------------------------------------------- */

export const settingsQueryKey = ["admin", "settings"] as const;

/** GET /admin/settings — controls are driven by `value_type` / `options` / min / max. */
export function fetchSettings(signal?: AbortSignal): Promise<SettingListResponse> {
  return request<SettingListResponse>("/admin/settings", { signal });
}

/** PUT /admin/settings — partial batch update, atomic on the server. */
export function updateSettings(payload: SettingUpdateRequest): Promise<SettingListResponse> {
  return request<SettingListResponse>("/admin/settings", { method: "PUT", body: payload });
}

/** Settings keys used by the M2 approval group (backend migration 0002). */
export const SETTING_KEYS = {
  approvalMode: "approval.mode",
  whitelistEnabled: "approval.whitelist_enabled",
  versionReapproval: "approval.version_reapproval",
} as const;

/* ========================================================================== */
/* M3 —— 管理控制台                                                            */
/* ========================================================================== */

/* -------------------------------------------------------------------------- */
/* 角色                                                                        */
/* -------------------------------------------------------------------------- */

export const rolesQueryKey = ["admin", "roles"] as const;

/** `GET /admin/roles` — 角色清单与各角色权限点（写死的 UI 角色名会漂移）。 */
export function fetchRoles(signal?: AbortSignal): Promise<RoleOut[]> {
  return request<RoleOut[]>("/admin/roles", { signal });
}

/* -------------------------------------------------------------------------- */
/* 用户管理                                                                    */
/* -------------------------------------------------------------------------- */

export function normalizeUserParams(params: AdminUserListParams = {}) {
  return {
    q: (params.q ?? "").trim(),
    role: [...(params.role ?? [])].sort(),
    status: params.status ?? "",
    page: params.page ?? 1,
    pageSize: params.page_size ?? 20,
  };
}

export function adminUsersQueryKey(params: AdminUserListParams = {}) {
  return ["admin", "users", normalizeUserParams(params)] as const;
}

/** `GET /admin/users`（docs/04 §6.13）。 */
export function fetchAdminUsers(
  params: AdminUserListParams = {},
  signal?: AbortSignal,
): Promise<AdminUserListResponse> {
  const normalized = normalizeUserParams(params);
  const query: Record<string, QueryValue> = {
    q: normalized.q || undefined,
    role: normalized.role,
    status: normalized.status || undefined,
    page: normalized.page,
    page_size: normalized.pageSize,
  };
  return request<AdminUserListResponse>("/admin/users", { query, signal });
}

export const adminUserQueryKey = (userId: number) => ["admin", "user", userId] as const;

export function fetchAdminUser(userId: number, signal?: AbortSignal): Promise<AdminUserItem> {
  return request<AdminUserItem>(`/admin/users/${userId}`, { signal });
}

/** `POST /admin/users` — 密码留空时响应带一次性 `generated_password`。 */
export function createAdminUser(
  payload: AdminUserCreateRequest,
): Promise<AdminUserCreateResponse> {
  return request<AdminUserCreateResponse>("/admin/users", { method: "POST", body: payload });
}

/** `PATCH /admin/users/{id}` — 显示名 / 邮箱 / 启用状态（用户名不可改）。 */
export function updateAdminUser(
  userId: number,
  payload: AdminUserUpdateRequest,
): Promise<AdminUserItem> {
  return request<AdminUserItem>(`/admin/users/${userId}`, { method: "PATCH", body: payload });
}

/** `PUT /admin/users/{id}/roles` — 全量替换角色。 */
export function replaceAdminUserRoles(
  userId: number,
  payload: AdminRoleReplaceRequest,
): Promise<AdminUserItem> {
  return request<AdminUserItem>(`/admin/users/${userId}/roles`, {
    method: "PUT",
    body: payload,
  });
}

/** `POST /admin/users/{id}/reset-password` — 一次性明文密码只在响应里出现。 */
export function resetAdminUserPassword(
  userId: number,
  payload: ResetPasswordRequest = {},
): Promise<ResetPasswordResponse> {
  return request<ResetPasswordResponse>(`/admin/users/${userId}/reset-password`, {
    method: "POST",
    body: payload,
  });
}

/** `POST /admin/users/{id}/revoke-sessions` — 强制下线。 */
export function revokeAdminUserSessions(userId: number): Promise<RevokeSessionsResponse> {
  return request<RevokeSessionsResponse>(`/admin/users/${userId}/revoke-sessions`, {
    method: "POST",
  });
}

/* -------------------------------------------------------------------------- */
/* 用户组                                                                      */
/* -------------------------------------------------------------------------- */

export const groupsQueryKey = (q: string) => ["admin", "groups", { q }] as const;

/** `GET /admin/groups`。 */
export function fetchGroups(
  params: { q?: string; page?: number; page_size?: number } = {},
  signal?: AbortSignal,
): Promise<GroupListResponse> {
  return request<GroupListResponse>("/admin/groups", {
    query: {
      q: params.q?.trim() || undefined,
      page: params.page ?? 1,
      page_size: params.page_size ?? 50,
    },
    signal,
  });
}

export function createGroup(payload: GroupCreateRequest): Promise<GroupOut> {
  return request<GroupOut>("/admin/groups", { method: "POST", body: payload });
}

export function updateGroup(groupId: number, payload: GroupUpdateRequest): Promise<GroupOut> {
  return request<GroupOut>(`/admin/groups/${groupId}`, { method: "PATCH", body: payload });
}

/**
 * `DELETE /admin/groups/{id}` — 被 ACL 引用时返回 `409 GROUP_IN_USE`，
 * `details.tools` 列出引用它的工具（先展示影响面，再让用户确认）。
 */
export function deleteGroup(groupId: number, force = false): Promise<null> {
  return request<null>(`/admin/groups/${groupId}`, {
    method: "DELETE",
    query: { force: force || undefined },
  });
}

export const groupMembersQueryKey = (groupId: number, q: string) =>
  ["admin", "group-members", groupId, { q }] as const;

/** `GET /admin/groups/{id}/members`。 */
export function fetchGroupMembers(
  groupId: number,
  params: { q?: string; page?: number; page_size?: number } = {},
  signal?: AbortSignal,
): Promise<GroupMemberListResponse> {
  return request<GroupMemberListResponse>(`/admin/groups/${groupId}/members`, {
    query: {
      q: params.q?.trim() || undefined,
      page: params.page ?? 1,
      page_size: params.page_size ?? 50,
    },
    signal,
  });
}

export function addGroupMembers(
  groupId: number,
  payload: GroupMemberAddRequest,
): Promise<GroupMemberAddResponse> {
  return request<GroupMemberAddResponse>(`/admin/groups/${groupId}/members`, {
    method: "POST",
    body: payload,
  });
}

export function removeGroupMember(groupId: number, userId: number): Promise<null> {
  return request<null>(`/admin/groups/${groupId}/members/${userId}`, { method: "DELETE" });
}

/* -------------------------------------------------------------------------- */
/* 分类管理                                                                    */
/* -------------------------------------------------------------------------- */

export const adminCategoriesQueryKey = ["admin", "categories"] as const;

/** `GET /admin/categories` — 含已停用（与门户的 `/categories` 不同）。 */
export function fetchAdminCategories(signal?: AbortSignal): Promise<AdminCategoryOut[]> {
  return request<AdminCategoryOut[]>("/admin/categories", { signal });
}

export function createAdminCategory(
  payload: AdminCategoryCreateRequest,
): Promise<AdminCategoryOut> {
  return request<AdminCategoryOut>("/admin/categories", { method: "POST", body: payload });
}

export function updateAdminCategory(
  categoryId: number,
  payload: AdminCategoryUpdateRequest,
): Promise<AdminCategoryOut> {
  return request<AdminCategoryOut>(`/admin/categories/${categoryId}`, {
    method: "PATCH",
    body: payload,
  });
}

/** `DELETE /admin/categories/{id}` — **软删除**（`is_active=false`）。 */
export function deleteAdminCategory(categoryId: number): Promise<null> {
  return request<null>(`/admin/categories/${categoryId}`, { method: "DELETE" });
}

/** `PUT /admin/categories/order` — 批量提交 drag 后的新顺序。 */
export function reorderAdminCategories(
  payload: CategoryOrderRequest,
): Promise<AdminCategoryOut[]> {
  return request<AdminCategoryOut[]>("/admin/categories/order", {
    method: "PUT",
    body: payload,
  });
}

/* -------------------------------------------------------------------------- */
/* 标签管理                                                                    */
/* -------------------------------------------------------------------------- */

export const adminTagsQueryKey = (q: string) => ["admin", "tags", { q }] as const;

export function fetchAdminTags(
  params: { q?: string; page?: number; page_size?: number } = {},
  signal?: AbortSignal,
): Promise<AdminTagListResponse> {
  return request<AdminTagListResponse>("/admin/tags", {
    query: {
      q: params.q?.trim() || undefined,
      page: params.page ?? 1,
      page_size: params.page_size ?? 50,
    },
    signal,
  });
}

/** `PATCH /admin/tags/{id}` — 重命名（全站生效）。 */
export function renameAdminTag(tagId: number, payload: TagRenameRequest): Promise<AdminTagOut> {
  return request<AdminTagOut>(`/admin/tags/${tagId}`, { method: "PATCH", body: payload });
}

/** `POST /admin/tags/merge` — 把源标签的引用转移到目标标签。 */
export function mergeAdminTags(payload: TagMergeRequest): Promise<TagMergeResponse> {
  return request<TagMergeResponse>("/admin/tags/merge", { method: "POST", body: payload });
}

/** `POST /admin/tags/cleanup` — 清理零引用标签。 */
export function cleanupAdminTags(): Promise<TagCleanupResponse> {
  return request<TagCleanupResponse>("/admin/tags/cleanup", { method: "POST" });
}

/* -------------------------------------------------------------------------- */
/* API Token                                                                   */
/* -------------------------------------------------------------------------- */

export const tokensQueryKey = (includeRevoked: boolean) =>
  ["admin", "tokens", { includeRevoked }] as const;

export function fetchTokens(
  params: { include_revoked?: boolean; page?: number; page_size?: number } = {},
  signal?: AbortSignal,
): Promise<ApiTokenListResponse> {
  return request<ApiTokenListResponse>("/admin/tokens", {
    query: {
      include_revoked: params.include_revoked ?? true,
      page: params.page ?? 1,
      page_size: params.page_size ?? 50,
    },
    signal,
  });
}

/** `POST /admin/tokens` — 响应里的 `token` 是**唯一一次**明文。 */
export function createToken(payload: ApiTokenCreateRequest): Promise<ApiTokenCreateResponse> {
  return request<ApiTokenCreateResponse>("/admin/tokens", { method: "POST", body: payload });
}

/**
 * `POST /admin/tokens/{id}/revoke`。
 *
 * 注意：该端点**不接受请求体**，所以「吊销理由」无处提交 —— UI 只做二次确认，
 * 不收集理由（已作为契约缺口上报）。
 */
export function revokeToken(tokenId: number): Promise<ApiTokenOut> {
  return request<ApiTokenOut>(`/admin/tokens/${tokenId}/revoke`, { method: "POST" });
}

/** `DELETE /admin/tokens/{id}` — 物理删除记录（保留审计痕迹请用吊销）。 */
export function deleteTokenRecord(tokenId: number): Promise<null> {
  return request<null>(`/admin/tokens/${tokenId}`, { method: "DELETE" });
}

/* -------------------------------------------------------------------------- */
/* 概览与统计                                                                  */
/* -------------------------------------------------------------------------- */

export const overviewQueryKey = ["admin", "overview"] as const;
export const toolRankQueryKey = (limit: number) => ["admin", "stats", "tools", { limit }] as const;
export const storageStatsQueryKey = (limit: number, offset: number) =>
  ["admin", "stats", "storage", { limit, offset }] as const;

/** `GET /admin/overview`（docs/04 §6.9 的四个数字卡）。 */
export function fetchOverview(signal?: AbortSignal): Promise<AdminOverviewResponse> {
  return request<AdminOverviewResponse>("/admin/overview", { signal });
}

/** `GET /admin/stats/tools` — 下载排行（概览页 / 后续统计页）。 */
export function fetchToolRank(
  params: { limit?: number; include_deleted?: boolean } = {},
  signal?: AbortSignal,
): Promise<ToolRankResponse> {
  return request<ToolRankResponse>("/admin/stats/tools", {
    query: { limit: params.limit ?? 10, include_deleted: params.include_deleted ?? false },
    signal,
  });
}

/** `GET /admin/stats/storage` — 存储占用明细（按用户聚合）。 */
export function fetchStorageStats(
  params: { limit?: number; offset?: number } = {},
  signal?: AbortSignal,
): Promise<StorageStatsResponse> {
  return request<StorageStatsResponse>("/admin/stats/storage", {
    query: { limit: params.limit ?? 10, offset: params.offset ?? 0 },
    signal,
  });
}

/* -------------------------------------------------------------------------- */
/* 全站工具 / 回收站                                                            */
/* -------------------------------------------------------------------------- */

export function normalizeAdminToolParams(params: AdminToolListParams = {}) {
  return {
    q: (params.q ?? "").trim(),
    status: [...(params.status ?? [])].sort(),
    type: [...(params.type ?? [])].sort(),
    category: [...(params.category ?? [])].sort(),
    visibility: [...(params.visibility ?? [])].sort(),
    owner: (params.owner ?? "").trim(),
    include_deleted: params.include_deleted ?? false,
    date_from: params.date_from ?? "",
    date_to: params.date_to ?? "",
    page: params.page ?? 1,
    pageSize: params.page_size ?? 20,
  };
}

export function adminToolsQueryKey(params: AdminToolListParams = {}) {
  return ["admin", "tools", normalizeAdminToolParams(params)] as const;
}

/** `GET /admin/tools` — 含全部状态与全部作者（docs/04 §6.12）。 */
export function fetchAdminTools(
  params: AdminToolListParams = {},
  signal?: AbortSignal,
): Promise<AdminToolListResponse> {
  const normalized = normalizeAdminToolParams(params);
  const query: Record<string, QueryValue> = {
    q: normalized.q || undefined,
    status: normalized.status,
    type: normalized.type,
    category: normalized.category,
    visibility: normalized.visibility,
    owner: normalized.owner || undefined,
    include_deleted: normalized.include_deleted || undefined,
    date_from: normalized.date_from || undefined,
    date_to: normalized.date_to || undefined,
    page: normalized.page,
    page_size: normalized.pageSize,
  };
  return request<AdminToolListResponse>("/admin/tools", { query, signal });
}

/** `GET /admin/recycle-bin` — 软删除的工具（docs/04 §6.21）。 */
export function fetchRecycleBin(
  params: { page?: number; page_size?: number } = {},
  signal?: AbortSignal,
): Promise<AdminToolListResponse> {
  return request<AdminToolListResponse>("/admin/recycle-bin", {
    query: { page: params.page ?? 1, page_size: params.page_size ?? 20 },
    signal,
  });
}

/** `POST /admin/tools/{id}/restore` — 从回收站还原。 */
export function restoreAdminTool(toolId: number): Promise<AdminToolItem> {
  return request<AdminToolItem>(`/admin/tools/${toolId}/restore`, { method: "POST" });
}

/** `DELETE /admin/tools/{id}/purge` — 彻底清除（不可恢复）。 */
export function purgeAdminTool(toolId: number): Promise<PurgeToolResponse> {
  return request<PurgeToolResponse>(`/admin/tools/${toolId}/purge`, { method: "DELETE" });
}

/** `POST /admin/tools/{id}/transfer` — 转移负责人。 */
export function transferAdminTool(
  toolId: number,
  payload: TransferOwnerRequest,
): Promise<TransferOwnerResponse> {
  return request<TransferOwnerResponse>(`/admin/tools/${toolId}/transfer`, {
    method: "POST",
    body: payload,
  });
}

/* -------------------------------------------------------------------------- */
/* 批量导入导出                                                                */
/* -------------------------------------------------------------------------- */

/** 上传 CSV / JSON 并（可选）预演。`dry_run=true` 时不写库、不生成密码。 */
export function importUsers(
  file: File,
  options: { dryRun?: boolean; onConflict?: ImportOnConflict } = {},
): Promise<ImportResultResponse> {
  const form = new FormData();
  form.append("file", file);
  form.append("dry_run", options.dryRun ? "true" : "false");
  form.append("on_conflict", options.onConflict ?? "skip");
  return upload<ImportResultResponse>("/admin/import/users", { formData: form });
}

/** 工具导入是 JSON 请求体（不是 multipart）。 */
export function importTools(payload: ToolImportRequest): Promise<ImportResultResponse> {
  return request<ImportResultResponse>("/admin/import/tools", {
    method: "POST",
    body: payload,
  });
}

/**
 * 导出下载。
 *
 * 后端给的文件名是固定的 `users.csv` / `tools.json`（不带日期），并且响应头已经带了
 * RFC 5987 形式的 `Content-Disposition` 与 CSV 的 BOM。为了得到「文件名含日期」并且
 * 让前端能统一处理，这里取 blob 后用带日期的文件名触发下载。
 */
async function downloadExport(path: string, filename: string): Promise<void> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { Authorization: `Bearer ${getAccessToken() ?? ""}` },
    credentials: "same-origin",
  });
  if (!response.ok) {
    throw new ApiError({
      status: response.status,
      code: "EXPORT_FAILED",
      message: `导出失败（HTTP ${response.status}）`,
      details: null,
      requestId: response.headers.get("X-Request-Id"),
    });
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function datedFilename(prefix: string, extension: string): string {
  const now = new Date();
  const stamp = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, "0")}${String(
    now.getDate(),
  ).padStart(2, "0")}`;
  return `${prefix}-${stamp}.${extension}`;
}

/** 导出用户 CSV（后端已带 BOM，中文不乱码）。 */
export function exportUsers(): Promise<void> {
  return downloadExport("/admin/export/users", datedFilename("localcraft-users", "csv"));
}

/** 导出工具 JSON。 */
export function exportTools(): Promise<void> {
  return downloadExport("/admin/export/tools", datedFilename("localcraft-tools", "json"));
}
