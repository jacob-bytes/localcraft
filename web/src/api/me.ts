import { request, upload, type QueryValue } from "./client";
import type {
  AclReplaceRequest,
  AclResponse,
  DownloadLogItem,
  ImagePatchRequest,
  MyToolListParams,
  MyToolListResponse,
  Paginated,
  Profile,
  ProfileUpdateRequest,
  SubmitResponse,
  ToolCreateRequest,
  ToolDetail,
  ToolImage,
  ToolUpdateRequest,
  Usage,
  VersionPatchRequest,
  VersionSummary,
  VersionUploadFields,
  VersionUploadResponse,
  WithdrawResponse,
} from "./types";
import { normalizeToolParams } from "./tools";

/**
 * 个人中心（docs/03 §2.4, CONTRACT §6.1 的 19 个接口）。
 *
 * Write endpoints are gated server-side by role + scope; the UI mirrors that with
 * `permissions` / roles but never treats it as a security boundary (docs/04 §4).
 */

/* -------------------------------------------------------------------------- */
/* Profile & stats                                                             */
/* -------------------------------------------------------------------------- */

export const profileQueryKey = ["me", "profile"] as const;
export const myStatsQueryKey = ["me", "stats"] as const;

/** GET /me/profile — profile + usage in one call (docs/04 §6.5). */
export function fetchProfile(signal?: AbortSignal): Promise<Profile> {
  return request<Profile>("/me/profile", { signal });
}

/** PATCH /me/profile — display name and email only (FR-AUTH-11). */
export function updateProfile(payload: ProfileUpdateRequest): Promise<Profile> {
  return request<Profile>("/me/profile", { method: "PATCH", body: payload });
}

/** GET /me/stats — quota/usage numbers for the StorageUsageBar. */
export function fetchMyStats(signal?: AbortSignal): Promise<Usage> {
  return request<Usage>("/me/stats", { signal });
}

export const myDownloadsQueryKey = (page: number) => ["me", "downloads", { page }] as const;

/** GET /me/downloads (P2 feature, docs/04 §6.5). */
export function fetchMyDownloads(
  page = 1,
  pageSize = 20,
  signal?: AbortSignal,
): Promise<Paginated<DownloadLogItem>> {
  return request<Paginated<DownloadLogItem>>("/me/downloads", {
    query: { page, page_size: pageSize },
    signal,
  });
}

/* -------------------------------------------------------------------------- */
/* My tools                                                                    */
/* -------------------------------------------------------------------------- */

export interface NormalizedMyToolParams {
  statuses: string[];
  types: string[];
  categories: string[];
  q: string;
  sort: string;
  page: number;
  pageSize: number;
}

export function normalizeMyToolParams(
  params: MyToolListParams = {},
): NormalizedMyToolParams {
  return {
    statuses: [...(params.status ?? [])].sort(),
    types: [...(params.type ?? [])].sort(),
    categories: [...(params.category ?? [])].sort(),
    q: (params.q ?? "").trim(),
    sort: params.sort ?? "-updated_at",
    page: params.page ?? 1,
    pageSize: params.page_size ?? 20,
  };
}

export function myToolsQueryKey(params: MyToolListParams = {}) {
  const normalized = normalizeMyToolParams(params);
  return ["me", "tools", normalized] as const;
}

/** GET /me/tools — includes drafts, pending, rejected and offline (never deleted). */
export function fetchMyTools(
  params: MyToolListParams = {},
  signal?: AbortSignal,
): Promise<MyToolListResponse> {
  const normalized = normalizeMyToolParams(params);
  const query: Record<string, QueryValue> = {
    status: normalized.statuses,
    type: normalized.types,
    category: normalized.categories,
    q: normalized.q || undefined,
    sort: normalized.sort,
    page: normalized.page,
    page_size: normalized.pageSize,
  };
  return request<MyToolListResponse>("/me/tools", { query, signal });
}

export const myToolDetailQueryKey = (toolId: number) => ["me", "tool", toolId] as const;

/** GET /me/tools/{id} — owner view, includes drafts and reject reasons. */
export function fetchMyTool(toolId: number, signal?: AbortSignal): Promise<ToolDetail> {
  return request<ToolDetail>(`/me/tools/${toolId}`, { signal });
}

/** POST /me/tools — creates a draft; `submit` is a separate action. */
export function createTool(payload: ToolCreateRequest): Promise<ToolDetail> {
  return request<ToolDetail>("/me/tools", { method: "POST", body: payload });
}

/** PATCH /me/tools/{id} — metadata only, does not re-trigger approval (FR-TOOL-13). */
export function updateTool(toolId: number, payload: ToolUpdateRequest): Promise<ToolDetail> {
  return request<ToolDetail>(`/me/tools/${toolId}`, { method: "PATCH", body: payload });
}

/** DELETE /me/tools/{id} — soft delete (FR-TOOL-10). */
export function deleteTool(toolId: number): Promise<null> {
  return request<null>(`/me/tools/${toolId}`, { method: "DELETE" });
}

/** POST /me/tools/{id}/submit — draft/rejected → pending. */
export function submitTool(toolId: number): Promise<SubmitResponse> {
  return request<SubmitResponse>(`/me/tools/${toolId}/submit`, { method: "POST" });
}

/** POST /me/tools/{id}/withdraw — pending → draft. */
export function withdrawTool(toolId: number): Promise<WithdrawResponse> {
  return request<WithdrawResponse>(`/me/tools/${toolId}/withdraw`, { method: "POST" });
}

/** PUT /me/tools/{id}/acl — full replacement semantics (FR-ACL-03). */
export function replaceAcl(toolId: number, payload: AclReplaceRequest): Promise<AclResponse> {
  return request<AclResponse>(`/me/tools/${toolId}/acl`, { method: "PUT", body: payload });
}

/* -------------------------------------------------------------------------- */
/* Versions                                                                    */
/* -------------------------------------------------------------------------- */

export const myVersionsQueryKey = (toolId: number) => ["me", "versions", toolId] as const;

/** GET /me/tools/{id}/versions — includes pending and rejected ones. */
export function fetchMyVersions(
  toolId: number,
  signal?: AbortSignal,
): Promise<VersionSummary[]> {
  return request<VersionSummary[]>(`/me/tools/${toolId}/versions`, { signal });
}

export interface UploadVersionOptions {
  onProgress?: (loaded: number, total: number) => void;
  signal?: AbortSignal;
}

/**
 * POST /me/tools/{id}/versions — `multipart/form-data` (docs/03 §3.7).
 * Only the fields relevant to the tool type are sent.
 */
export function uploadVersion(
  toolId: number,
  fields: VersionUploadFields,
  options: UploadVersionOptions = {},
): Promise<VersionUploadResponse> {
  const form = new FormData();
  form.append("version", fields.version);
  if (fields.changelog_md !== undefined) form.append("changelog_md", fields.changelog_md);
  if (fields.file) form.append("file", fields.file);
  if (fields.prompt_content != null) form.append("prompt_content", fields.prompt_content);
  if (fields.webapp_url != null) form.append("webapp_url", fields.webapp_url);
  if (fields.auto_submit !== undefined) {
    form.append("auto_submit", fields.auto_submit ? "true" : "false");
  }
  return upload<VersionUploadResponse>(`/me/tools/${toolId}/versions`, {
    formData: form,
    onProgress: options.onProgress,
    signal: options.signal,
  });
}

/** PATCH /me/tools/{id}/versions/{version} — changelog only (FR-VER-11). */
export function patchVersion(
  toolId: number,
  version: string,
  payload: VersionPatchRequest,
): Promise<VersionSummary> {
  return request<VersionSummary>(
    `/me/tools/${toolId}/versions/${encodeURIComponent(version)}`,
    { method: "PATCH", body: payload },
  );
}

/** DELETE /me/tools/{id}/versions/{version} — only pending/rejected (FR-VER-10). */
export function deleteVersion(toolId: number, version: string): Promise<null> {
  return request<null>(`/me/tools/${toolId}/versions/${encodeURIComponent(version)}`, {
    method: "DELETE",
  });
}

/* -------------------------------------------------------------------------- */
/* Images                                                                      */
/* -------------------------------------------------------------------------- */

export const myImagesQueryKey = (toolId: number) => ["me", "images", toolId] as const;

/** POST /me/tools/{id}/images — cover or screenshot (multipart). */
export function uploadImage(
  toolId: number,
  file: File,
  options: UploadVersionOptions & { kind?: "cover" | "screenshot" } = {},
): Promise<ToolImage> {
  const form = new FormData();
  form.append("file", file);
  if (options.kind) form.append("kind", options.kind);
  return upload<ToolImage>(`/me/tools/${toolId}/images`, {
    formData: form,
    onProgress: options.onProgress,
    signal: options.signal,
  });
}

/** PATCH /me/tools/{id}/images/{image_id} — sort order, alt text, set as cover. */
export function patchImage(
  toolId: number,
  imageId: number,
  payload: ImagePatchRequest,
): Promise<ToolImage> {
  return request<ToolImage>(`/me/tools/${toolId}/images/${imageId}`, {
    method: "PATCH",
    body: payload,
  });
}

/** DELETE /me/tools/{id}/images/{image_id}. */
export function deleteImage(toolId: number, imageId: number): Promise<null> {
  return request<null>(`/me/tools/${toolId}/images/${imageId}`, { method: "DELETE" });
}

/** Re-exported so callers do not need to import from `./tools` as well. */
export { normalizeToolParams };
