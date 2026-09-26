import { useMutation, useQueryClient, type QueryClient, type QueryKey } from "@tanstack/react-query";
import { useCallback } from "react";
import { toast } from "sonner";

import { getErrorMessage } from "@/api/client";
import {
  applyEngagementPatch,
  FAVORITES_QUERY_ROOT,
  readEngagementState,
  setFavorite,
  setLike,
} from "@/api/engagement";
import type { ToolDetail, ToolListItem } from "@/api/types";

/**
 * M8 · 收藏 / 点赞的乐观更新（任务书 F7.5、F8）。
 *
 * 用 TanStack Query 的既有模式（`onMutate` → 快照 + 打补丁，`onError` → 回滚，
 * `onSettled` → invalidate），**不手写状态机**。
 *
 * 三条刻意设计：
 *
 * 1. **快照 = 缓存里所有持有该工具的分页信封与详情**。因为 `is_favorited` 与
 *    `favorite_count` 写在**同一个补丁对象**里，回滚是「整块数据还原」，
 *    不存在「计数回滚了、按钮没回滚」这种半截状态（F7.5 的硬要求）。
 * 2. **计数从缓存读取**（不是从组件 props），所以卡片墙里同一工具的多个视图
 *    （列表页 / 详情页 / 我的收藏）会一起变，不会出现两个数字。
 * 3. **服务端响应是权威的**：`onSuccess` 里用响应把缓存校正一次；但响应字段名不在
 *    §23 的冻结范围（§23.4 只冻结路径与幂等），所以拿不到就跳过校正，
 *    由 `onSettled` 的 invalidate 兜底 —— 字段名漂移不会让 UI 卡在错误状态。
 */

type EngagementKind = "favorite" | "like";

const CACHE_ROOTS: QueryKey[] = [["tools"], FAVORITES_QUERY_ROOT];

interface EngagementSnapshot {
  key: QueryKey;
  data: unknown;
}

interface ToggleContext {
  snapshots: EngagementSnapshot[];
}

interface OptimisticResult {
  context: ToggleContext;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function usableData(data: unknown): data is ToolListItem | ToolDetail {
  return isRecord(data) && typeof data["slug"] === "string" && "favorite_count" in data;
}

/**
 * 缓存里这个工具当前的互动状态 + 计数。
 *
 * 真实后端尚未落地 M8 时字段是 `undefined` → 计数返回 `null`，此时不做 `±1`
 * 猜测（宁可等服务端响应，也不显示一个凭空的数字）。
 */
function readCached(
  queryClient: QueryClient,
  slug: string,
  kind: EngagementKind,
): { active: boolean | null; count: number | null } {
  for (const root of CACHE_ROOTS) {
    for (const [, data] of queryClient.getQueriesData({ queryKey: root as QueryKey })) {
      if (!isRecord(data)) continue;
      const items = data["items"];
      const candidates = Array.isArray(items) ? items : [data];
      for (const item of candidates) {
        if (!usableData(item) || item["slug"] !== slug) continue;
        const record = item as unknown as Record<string, unknown>;
        const active =
          kind === "favorite" ? record["is_favorited"] : record["is_liked"];
        const count =
          kind === "favorite" ? record["favorite_count"] : record["like_count"];
        return {
          active: typeof active === "boolean" ? active : null,
          count: typeof count === "number" && Number.isFinite(count) ? count : null,
        };
      }
    }
  }
  return { active: null, count: null };
}

type EngagementPatch = {
  is_favorited?: boolean;
  favorite_count?: number;
  is_liked?: boolean;
  like_count?: number;
};

function patchFor(
  kind: EngagementKind,
  next: boolean,
  previousCount: number | null,
  changed: boolean,
): EngagementPatch {
  const count =
    previousCount === null ? null : Math.max(0, previousCount + (changed ? (next ? 1 : -1) : 0));
  if (kind === "favorite") {
    return count === null
      ? { is_favorited: next }
      : { is_favorited: next, favorite_count: count };
  }
  return count === null ? { is_liked: next } : { is_liked: next, like_count: count };
}

function useEngagementToggle(slug: string, kind: EngagementKind) {
  const queryClient = useQueryClient();

  const mutation = useMutation<unknown, unknown, boolean, OptimisticResult>({
    mutationFn: (next) => (kind === "favorite" ? setFavorite(slug, next) : setLike(slug, next)),
    onMutate: async (next) => {
      // 1) 先取消在途请求，否则它落地后会把乐观值覆盖掉。
      for (const root of CACHE_ROOTS) {
        await queryClient.cancelQueries({ queryKey: root });
      }

      // 2) 快照：所有持有这个工具的缓存条目一起存（回滚时整块还原）。
      const snapshots: EngagementSnapshot[] = [];
      for (const root of CACHE_ROOTS) {
        for (const [key, data] of queryClient.getQueriesData({ queryKey: root })) {
          snapshots.push({ key, data });
        }
      }

      // 3) 乐观打补丁：状态与计数**一次写入**。
      const cached = readCached(queryClient, slug, kind);
      const changed = cached.active === null ? true : cached.active !== next;
      const patch = patchFor(kind, next, cached.count, changed);
      for (const root of CACHE_ROOTS) {
        queryClient.setQueriesData({ queryKey: root }, (data) =>
          applyEngagementPatch(data, slug, patch),
        );
      }

      return { context: { snapshots } };
    },
    onError: (error, _next, result) => {
      // 4) 回滚：`is_favorited` 与 `favorite_count` 在同一次 setQueryData 里还原。
      for (const snapshot of result?.context.snapshots ?? []) {
        queryClient.setQueryData(snapshot.key, snapshot.data);
      }
      toast.error(
        kind === "favorite"
          ? `收藏失败，已恢复原状态：${getErrorMessage(error)}`
          : `点赞失败，已恢复原状态：${getErrorMessage(error)}`,
      );
    },
    onSuccess: (raw) => {
      /*
       * 后端对 4 个端点回**同一个** `ToolEngagementResponse`（操作后的完整状态：
       * 两个计数 + 两个布尔），所以一次响应就把收藏与点赞一起校正 —— 不需要按
       * `kind` 分支挑字段。
       */
      const state = readEngagementState(raw);
      if (!state) return; // 形状不可用 → 交给 onSettled 的 invalidate
      const patch: EngagementPatch = {
        is_favorited: state.is_favorited,
        favorite_count: state.favorite_count,
        is_liked: state.is_liked,
        like_count: state.like_count,
      };
      for (const root of CACHE_ROOTS) {
        queryClient.setQueriesData({ queryKey: root }, (data) =>
          applyEngagementPatch(data, slug, patch),
        );
      }
    },
    onSettled: () => {
      // 5) 以服务端为准收敛（幂等端点，重复调用无副作用）。
      for (const root of CACHE_ROOTS) {
        void queryClient.invalidateQueries({ queryKey: root });
      }
    },
  });

  const toggle = useCallback(
    (next: boolean) => {
      mutation.mutate(next);
    },
    [mutation],
  );

  return { toggle, isPending: mutation.isPending };
}

/** 收藏切换：`toggle(next)` 里传**目标状态**（不是「切换」语义）。 */
export function useFavoriteToggle(slug: string) {
  return useEngagementToggle(slug, "favorite");
}

/** 点赞切换：与收藏完全对称（§23.4）。 */
export function useLikeToggle(slug: string) {
  return useEngagementToggle(slug, "like");
}
