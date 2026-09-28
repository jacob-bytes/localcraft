import { useQuery } from "@tanstack/react-query";

import { fetchMeta, metaQueryKey } from "@/api/meta";
import type { Meta } from "@/api/types";

/**
 * Public site config (docs/03 §3.1). Cached forever within a session
 * (docs/04 §9). Failures are tolerated: every consumer falls back to sensible
 * defaults so a `/meta` hiccup cannot white-screen the app.
 */
export function useMeta() {
  return useQuery<Meta>({
    queryKey: metaQueryKey,
    queryFn: ({ signal }) => fetchMeta(signal),
    staleTime: Number.POSITIVE_INFINITY,
    retry: 1,
  });
}

export const DEFAULT_SITE_NAME = "工具与 Skill 平台";

export function siteName(meta: Meta | undefined): string {
  return meta?.site_name?.trim() || DEFAULT_SITE_NAME;
}

/**
 * M12 · F1 —— 站点副标题（`portal.site_subtitle`，CONTRACT §27.2/§27.4）。
 *
 * 与 `siteName()` 不同，这里**没有默认值**：未配置时返回 `null`，调用方据此
 * **完全不渲染**（不留空元素、不留空白间距）。空白串按「未配置」处理 ——
 * 一个只打了空格的设置项渲染出来只会是一行看不见的空隙。
 */
export function siteSubtitle(meta: Meta | undefined): string | null {
  return meta?.site_subtitle?.trim() || null;
}
