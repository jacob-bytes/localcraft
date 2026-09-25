import { request, type QueryValue } from "./client";
import type {
  ApprovalHistoryParams,
  ApprovalQueueItem,
  ApprovalQueueParams,
  ApprovalRecord,
  ApproveRequest,
  ApproveResponse,
  BatchApproveRequest,
  BatchApproveResponse,
  OfflineRequest,
  Paginated,
  RejectRequest,
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

/** POST /admin/approvals/{tool_id}/offline — reason required (FR-APPR-10). */
export function offlineTool(toolId: number, payload: OfflineRequest): Promise<ApproveResponse> {
  return request<ApproveResponse>(`/admin/approvals/${toolId}/offline`, {
    method: "POST",
    body: payload,
  });
}

/** POST /admin/approvals/{tool_id}/relist — reason optional (FR-APPR-11). */
export function relistTool(toolId: number, payload: { reason?: string | null } = {}) {
  return request<ApproveResponse>(`/admin/approvals/${toolId}/relist`, {
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
