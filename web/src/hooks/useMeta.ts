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
