import { request } from "./client";
import type {
  DuplicateVersionMatch,
  FavoriteListResponse,
  ToolDetail,
  ToolEngagementResponse,
  ToolListItem,
  VersionUploadResponse,
} from "./types";

/**
 * M8 —— 收藏 / 点赞 / 上传去重（CONTRACT §23.2、§23.4）。
 *
 * 三条来自契约的硬约束，全部体现在这个文件里：
 *
 * 1. **收藏 / 点赞的 PUT 与 DELETE 都是幂等的**（§23.4）：重复调用不报错，
 *    所以前端不需要「先查再写」，直接按目标状态发请求即可。
 * 2. **`is_favorited` / `is_liked` 是「当前请求者」的状态**，匿名恒为 `false`；
 *    两个计数字段照常返回（§23.5）。因此切换按钮只在登录后渲染。
 * 3. **去重信息由上传响应回带**（§23.2），前端不算任何哈希。
 */

/* -------------------------------------------------------------------------- */
/* 收藏                                                                        */
/* -------------------------------------------------------------------------- */

export const FAVORITES_QUERY_ROOT = ["me", "favorites"] as const;

export const favoritesQueryKey = (page: number, pageSize: number) =>
  [...FAVORITES_QUERY_ROOT, { page, pageSize }] as const;

/**
 * `GET /api/v1/me/favorites` —— 分页，按 `created_at DESC`（§23.4）。
 *
 * 返回的形状**复用门户列表项**（`ToolListItem`），所以「我的收藏」页可以直接用
 * `ToolGrid`，不需要另写一套卡片（任务书 F7.3）。
 */
export function fetchFavorites(
  page = 1,
  pageSize = 24,
  signal?: AbortSignal,
): Promise<FavoriteListResponse> {
  return request<FavoriteListResponse>("/me/favorites", {
    query: { page, page_size: pageSize },
    signal,
  });
}

/** `PUT /tools/{slug}/favorite`（幂等）或 `DELETE …`（幂等）。 */
export function setFavorite(slug: string, favorited: boolean): Promise<ToolEngagementResponse> {
  return request<ToolEngagementResponse>(`/tools/${encodeURIComponent(slug)}/favorite`, {
    method: favorited ? "PUT" : "DELETE",
  });
}

/* -------------------------------------------------------------------------- */
/* 点赞                                                                        */
/* -------------------------------------------------------------------------- */

/** `PUT /tools/{slug}/like`（幂等）或 `DELETE …`（幂等）。形状与收藏完全对称。 */
export function setLike(slug: string, liked: boolean): Promise<ToolEngagementResponse> {
  return request<ToolEngagementResponse>(`/tools/${encodeURIComponent(slug)}/like`, {
    method: liked ? "PUT" : "DELETE",
  });
}

/* -------------------------------------------------------------------------- */
/* 运行时形状守卫（字段缺失 / 后端未落地时宁可退回 invalidate，也不要崩）         */
/* -------------------------------------------------------------------------- */

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function optionalString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function optionalNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** 操作后的完整互动状态（两个计数 + 两个当前请求者布尔）。 */
export interface EngagementState {
  is_favorited: boolean;
  favorite_count: number;
  is_liked: boolean;
  like_count: number;
}

/**
 * 从统一响应 `ToolEngagementResponse` 里取出权威状态。
 *
 * 后端把这 4 个字段都设了默认值，所以正常路径一定拿得到；这里仍做一次运行时校验，
 * 拿不到就返回 `null`，让调用方退回 `invalidateQueries` 重新拉取，而不是把
 * `undefined` 写进缓存（复用 `CopyButton` 的守卫惯例：依赖外部能力的地方都要有降级）。
 */
export function readEngagementState(response: unknown): EngagementState | null {
  if (!isRecord(response)) return null;
  const isFavorited = response["is_favorited"];
  const favoriteCount = response["favorite_count"];
  const isLiked = response["is_liked"];
  const likeCount = response["like_count"];
  if (
    typeof isFavorited !== "boolean" ||
    typeof isLiked !== "boolean" ||
    typeof favoriteCount !== "number" ||
    typeof likeCount !== "number"
  ) {
    return null;
  }
  return {
    is_favorited: isFavorited,
    favorite_count: favoriteCount,
    is_liked: isLiked,
    like_count: likeCount,
  };
}

/**
 * 从上传响应里读出「与工具 X 的版本 Y 完全相同」（§23.2 / 任务书 F9）。
 *
 * 字段名与形状照 `backend/openapi.json` 的 `DuplicateVersionMatch`（§15.6：openapi
 * 是形状权威）。**宽容解析，永不抛错**（对应用户任务书里「复用 CopyButton 的守卫
 * 惯例」）：该字段缺失（后端未落地）→ 返回 `null` → 不展示任何提示；字段存在但缺少
 * 跳转所必需的 `slug` / `version` 时同样返回 `null`，宁可不提示，也不给一个点不动的
 * 链接或编造一个版本号。
 */
export function readDuplicateOf(
  response: VersionUploadResponse | null | undefined,
): DuplicateVersionMatch | null {
  if (!isRecord(response)) return null;
  const nested = response["duplicate_of"];
  if (!isRecord(nested)) return null;
  const slug = optionalString(nested["slug"]);
  const version = optionalString(nested["version"]);
  if (!slug || !version) return null;
  return {
    tool_id: optionalNumber(nested["tool_id"]) ?? 0,
    slug,
    name: optionalString(nested["name"]) ?? slug,
    version_id: optionalNumber(nested["version_id"]) ?? 0,
    version,
    file_sha256: optionalString(nested["file_sha256"]) ?? "",
    is_current: nested["is_current"] === true,
    uploaded_at: optionalString(nested["uploaded_at"]),
  };
}

/* -------------------------------------------------------------------------- */
/* 缓存打补丁（乐观更新共用）                                                    */
/* -------------------------------------------------------------------------- */

type EngagementPatch = {
  is_favorited?: boolean;
  favorite_count?: number;
  is_liked?: boolean;
  like_count?: number;
};

function patchItem<T extends ToolListItem | ToolDetail>(item: T, patch: EngagementPatch): T {
  const next = { ...item } as T & EngagementPatch;
  if (patch.is_favorited !== undefined) next.is_favorited = patch.is_favorited;
  if (patch.favorite_count !== undefined) next.favorite_count = patch.favorite_count;
  if (patch.is_liked !== undefined) next.is_liked = patch.is_liked;
  if (patch.like_count !== undefined) next.like_count = patch.like_count;
  return next;
}

/**
 * 把补丁同时应用到**所有**持有这个工具的缓存里：门户列表、详情、我的收藏。
 *
 * 这是「计数与按钮状态一起回滚」（任务书 F7.5）的前提 —— 二者写在同一个补丁对象
 * 里，由同一次快照/回滚覆盖，不可能只回滚其中一个。
 */
export function applyEngagementPatch(
  data: unknown,
  slug: string,
  patch: EngagementPatch,
): unknown {
  if (!isRecord(data)) return data;

  // 分页信封（`/tools`、`/me/favorites`）：{ items: ToolListItem[], … }
  const items = data["items"];
  if (Array.isArray(items)) {
    let touched = false;
    const nextItems = items.map((item) => {
      if (!isRecord(item) || item["slug"] !== slug) return item;
      touched = true;
      return patchItem(item as unknown as ToolListItem, patch);
    });
    return touched ? { ...data, items: nextItems } : data;
  }

  // 详情：ToolDetail（顶层就有 slug 与那 4 个字段）
  if (data["slug"] === slug && "favorite_count" in data) {
    return patchItem(data as unknown as ToolDetail, patch);
  }
  return data;
}

/**
 * 从缓存里的 `ToolListItem` / `ToolDetail` 读出当前计数。
 *
 * 真实后端尚未落地 M8 时这些字段是 `undefined` —— 返回 `null` 让 UI 直接隐藏计数，
 * 而不是显示一个假的 `0`（`formatCount(undefined)` 会渲染成 "0"）。
 */
export function readCount(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
