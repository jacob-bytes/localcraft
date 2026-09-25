import type { QueryValue, RequestOptions } from "./client";
import { request } from "./client";
import type {
  Category,
  DownloadTicket,
  SkillPreview,
  Tag,
  ToolDetail,
  ToolListParams,
  ToolListResponse,
  ToolSort,
  ToolStats,
  ToolType,
  VersionSummary,
} from "./types";

/** Portal page size default (CONTRACT §4.2 / docs/03 §3.3). */
export const DEFAULT_PAGE_SIZE = 24;

export const PAGE_SIZE_OPTIONS = [12, 24, 48] as const;

export const TOOL_TYPE_VALUES: readonly ToolType[] = ["file", "webapp", "skill", "prompt"];

export const SORT_OPTIONS: ReadonlyArray<{ value: ToolSort; label: string }> = [
  { value: "hot", label: "热门" },
  { value: "new", label: "最新发布" },
  { value: "name", label: "名称" },
];

/** Canonical form of the portal list parameters (docs/04 §9 — query key normalisation). */
export interface NormalizedToolParams {
  q: string;
  categories: string[];
  tags: string[];
  types: string[];
  sort: ToolSort;
  page: number;
  pageSize: number;
}

export function normalizeToolParams(params: ToolListParams = {}): NormalizedToolParams {
  return {
    q: (params.q ?? "").trim(),
    categories: [...(params.category ?? [])].sort(),
    tags: [...(params.tag ?? [])].sort(),
    types: [...(params.type ?? [])].sort(),
    sort: params.sort ?? "hot",
    page: params.page ?? 1,
    pageSize: params.page_size ?? DEFAULT_PAGE_SIZE,
  };
}

/** `["tools", { q, category, tags, type, sort, page, pageSize }]` (docs/04 §9). */
export function toolsQueryKey(params: ToolListParams = {}) {
  const normalized = normalizeToolParams(params);
  return [
    "tools",
    {
      q: normalized.q,
      category: normalized.categories,
      tags: normalized.tags,
      type: normalized.types,
      sort: normalized.sort,
      page: normalized.page,
      pageSize: normalized.pageSize,
    },
  ] as const;
}

function toQuery(params: ToolListParams): Record<string, QueryValue> {
  const normalized = normalizeToolParams(params);
  return {
    q: normalized.q || undefined,
    category: normalized.categories,
    tag: normalized.tags,
    type: normalized.types,
    sort: normalized.sort,
    page: normalized.page,
    page_size: normalized.pageSize,
  };
}

/** GET /api/v1/tools (docs/03 §3.3). `facets` is an object on page 1, else null. */
export function fetchTools(
  params: ToolListParams = {},
  signal?: AbortSignal,
): Promise<ToolListResponse> {
  return request<ToolListResponse>("/tools", {
    query: toQuery(params),
    signal,
  } satisfies RequestOptions);
}

export const categoriesQueryKey = ["categories"] as const;

/** GET /api/v1/categories — active categories only (CONTRACT §6). */
export function fetchCategories(signal?: AbortSignal): Promise<Category[]> {
  return request<Category[]>("/categories", { signal });
}

export const tagsQueryKey = (q: string) => ["tags", { q }] as const;

/** GET /api/v1/tags — `?q=` prefix search for the tag autocomplete (docs/03 §2.3). */
export function fetchTags(q?: string, signal?: AbortSignal): Promise<Tag[]> {
  return request<Tag[]>("/tags", { query: { q: q || undefined }, signal });
}

/* -------------------------------------------------------------------------- */
/* M2 —— 详情 / 版本 / Skill 预览 / 下载票据                                    */
/* -------------------------------------------------------------------------- */

export const toolDetailQueryKey = (slug: string) => ["tools", "detail", slug] as const;

/** GET /tools/{slug} (docs/03 §3.4). 404 also means "no permission" (FR-FILE-08). */
export function fetchToolDetail(slug: string, signal?: AbortSignal): Promise<ToolDetail> {
  return request<ToolDetail>(`/tools/${encodeURIComponent(slug)}`, { signal });
}

export const toolVersionsQueryKey = (slug: string) => ["tools", "versions", slug] as const;

/** GET /tools/{slug}/versions — bare array, newest first (backend `list[VersionSummary]`). */
export function fetchToolVersions(
  slug: string,
  signal?: AbortSignal,
): Promise<VersionSummary[]> {
  return request<VersionSummary[]>(`/tools/${encodeURIComponent(slug)}/versions`, { signal });
}

export const skillPreviewQueryKey = (slug: string, version: string) =>
  ["tools", "skill-preview", slug, version] as const;

/**
 * GET /tools/{slug}/versions/{version}/skill-preview (docs/03 §3.5).
 * Fetched lazily — only when the user opens the "包内文件" tab, because the
 * payload can be large (docs/04 §6.4).
 */
export function fetchSkillPreview(
  slug: string,
  version: string,
  signal?: AbortSignal,
): Promise<SkillPreview> {
  return request<SkillPreview>(
    `/tools/${encodeURIComponent(slug)}/versions/${encodeURIComponent(version)}/skill-preview`,
    { signal },
  );
}

export const toolStatsQueryKey = (slug: string) => ["tools", "stats", slug] as const;

/** GET /tools/{slug}/stats. */
export function fetchToolStats(slug: string, signal?: AbortSignal): Promise<ToolStats> {
  return request<ToolStats>(`/tools/${encodeURIComponent(slug)}/stats`, { signal });
}

/**
 * POST /tools/{slug}/download-ticket (docs/03 §3.15).
 *
 * Downloads must go through a ticket — an `<a href="/api/v1/tools/…/download">`
 * cannot carry the bearer token (docs/03 §1.10, docs/04 §6.4).
 */
export function createDownloadTicket(
  slug: string,
  versionId?: number | null,
): Promise<DownloadTicket> {
  return request<DownloadTicket>(`/tools/${encodeURIComponent(slug)}/download-ticket`, {
    method: "POST",
    query: { version_id: versionId ?? undefined },
  });
}
