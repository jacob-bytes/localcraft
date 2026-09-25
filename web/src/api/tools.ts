import type { QueryValue, RequestOptions } from "./client";
import { request } from "./client";
import type {
  Category,
  Tag,
  ToolListParams,
  ToolListResponse,
  ToolSort,
  ToolType,
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
