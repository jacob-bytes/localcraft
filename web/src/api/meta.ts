import { request } from "./client";
import type { Meta } from "./types";

/** GET /api/v1/meta — public config, no auth (docs/03 §3.1). */
export function fetchMeta(signal?: AbortSignal): Promise<Meta> {
  return request<Meta>("/meta", { auth: false, signal });
}

/** Cache key for `/meta`; `staleTime: Infinity` in the query client (docs/04 §9). */
export const metaQueryKey = ["meta"] as const;
