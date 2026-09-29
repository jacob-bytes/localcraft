import { useQueries, useQuery, type QueryFunctionContext } from "@tanstack/react-query";
import * as React from "react";
import { Link } from "react-router-dom";

import { favoritesQueryKey, fetchFavorites } from "@/api/engagement";
import { DEFAULT_PAGE_SIZE, fetchToolDetail, toolDetailQueryKey } from "@/api/tools";
import type { ToolDetail, ToolListItem } from "@/api/types";
import { PageSkeleton } from "@/components/common/PageSkeleton";
import { ToolGrid } from "@/components/tools/ToolGrid";
import { useAuth } from "@/hooks/useAuth";
import { canEngage } from "@/lib/permissions";
import { readRecentTools, type RecentToolEntry } from "@/lib/recentTools";
import { readWebappHealthStatus } from "@/lib/webappHealth";

/**
 * M16 · F1（CONTRACT §32.2）：门户的「继续使用」/「我的收藏」**两个 tab 面板**。
 *
 * 这里取代了 M14 的 §29.8 横向区块（`PortalHighlights` / `ContinueUsingSection` /
 * `FavoritesSection`）。为什么改，§32.1 写得很清楚：那块用了**全尺寸 `ToolCard`**，
 * 高 363px、把主网格往下推 430px —— 「复用组件」是对的，错在**尺寸层级**。
 * 所以这次不是推翻 M14 做对的事，只是把它从「堆在门户顶部」换成「平行的一个视图」：
 *
 *   - 宽容解析（详情 404 / 已下线就静默不出现）、匿名不发 `/me/favorites`、
 *     「未检测 ≠ 失败」—— 全部原样保留；
 *   - 上限不再是 6：tab 是一个**完整视图**，没有理由截断（§32.2）。
 *
 * 职责边界（**重要**）：本文件**不做 tab 栏**。tab 栏由 `PortalPage` 用
 * `components/ui/tabs`（Radix）渲染 —— 只有它知道「一共几个 tab、默认选哪个、
 * 选中的是不是有内容」。这里只负责「给定这个 tab 有内容，面板长什么样」，
 * 并把「有没有内容」用 hook 暴露出去（`useRecentToolEntries` / `useFavoritesHighlight`），
 * 因为 Radix 的 `TabsContent` 只在激活时挂载，计数不能等面板挂载才知道。
 */

/** 门户 tab 的三个取值（§32.2）。默认 `all`。 */
export type PortalTab = "all" | "recent" | "favorites";

const PORTAL_TAB_SET: ReadonlySet<string> = new Set(["all", "recent", "favorites"]);

/**
 * `?tab=` 的解析：缺失或非法一律回落 `all`（默认 tab）。
 * URL 是唯一状态源（与门户既有的 q/category/type/tag/sort/page 一致）。
 */
export function readPortalTab(value: string | null): PortalTab {
  return value !== null && PORTAL_TAB_SET.has(value) ? (value as PortalTab) : "all";
}

/**
 * 「我的收藏」tab 只取**第一页**（§32.2 冻结）。`DEFAULT_PAGE_SIZE` = 24，
 * 同时是与 `/me/favorites` 页**完全相同**的 pageSize —— 两个界面共用同一份缓存。
 */
const FAVORITES_TAB_PAGE_SIZE = DEFAULT_PAGE_SIZE;

/* -------------------------------------------------------------------------- */
/* 「继续使用」：本地最近访问（M7 · F6，最多 8 条）                             */
/* -------------------------------------------------------------------------- */

/**
 * 挂载时读一次 `localStorage` 的最近访问。
 *
 * 为什么提成 hook：tab 的**存在性**（有没有本地记录 → 要不要出现这个 tab）
 * 必须在面板挂载之前就知道，所以读取这一层要在 `PortalPage`。
 *
 * 「只读既有键、不新增存储」是 M7 的硬约束，这里原样沿用 `readRecentTools`。
 */
export function useRecentToolEntries(): RecentToolEntry[] {
  // 门户页进入详情时会卸载，退回来就是一次重新挂载（重新读）。
  const [entries] = React.useState<RecentToolEntry[]>(readRecentTools);
  return entries;
}

/**
 * `ToolDetail` → `ToolListItem`。
 *
 * 为什么需要它：`recentTools` 只存 `{slug, name, at}`（§29.8 明确不许改存储形状），
 * 而卡片吃的是完整的 `ToolListItem`。列表接口没有「按 slug 批量取」的能力
 * （`openapi.json` 里 `/tools` 只有 q/category/tag/type/owner/sort 参数），
 * 所以这里用详情接口补全字段。
 *
 * 映射是**无损**的：`ToolDetail` 有上面每一个字段，只有三处换了来源 ——
 * 封面在 `images` 里（`kind === "cover"`）、版本号在 `current_version.version`、
 * `has_pending_version` 由 `pending_version !== null` 派生。
 */
function detailToListItem(detail: ToolDetail): ToolListItem {
  const cover = detail.images.find((image) => image.kind === "cover");
  const version = detail.current_version;
  const status = readWebappHealthStatus(detail.webapp_health_status);
  return {
    id: detail.id,
    slug: detail.slug,
    name: detail.name,
    summary: detail.summary,
    tool_type: detail.tool_type,
    visibility: detail.visibility,
    category: detail.category,
    tags: detail.tags,
    cover_url: cover?.thumb_url ?? cover?.url ?? null,
    owner: detail.owner,
    current_version: version?.version ?? null,
    file_size: version?.file_size ?? null,
    download_count: detail.download_count,
    view_count: detail.view_count,
    has_pending_version: detail.pending_version !== null,
    can_download: detail.can_download,
    published_at: detail.published_at,
    updated_at: detail.updated_at,
    favorite_count: detail.favorite_count,
    like_count: detail.like_count,
    is_favorited: detail.is_favorited,
    is_liked: detail.is_liked,
    /* §29.3 的派生规则（详情只有原始值，列表只给派生布尔）：fail / timeout 才算不健康。 */
    webapp_unhealthy: status === "fail" || status === "timeout",
  };
}

/**
 * 「继续使用」面板：`localStorage` 里的**全部**本地记录（`recentTools` 上限 8 条，
 * 不再像 §29.8 那样只取前 6 —— §32.2 冻结为「一个完整视图，不截断」）。
 *
 * **匿名同样显示** —— 这是本地数据，登录与否不影响。
 *
 * 取数说明：`ToolDetailPage` 用的是**同一个 queryKey**（`toolDetailQueryKey`），
 * 且 `staleTime` 与详情页一致（5 分钟）。所以主场景 —— 「刚看完那个工具，退回门户，
 * 再点『继续使用』」—— 数据已经在 react-query 缓存里，**一个请求都不发**。
 *
 * 面板只在 tab 激活时挂载（Radix 默认 `forceMount` 关闭），所以**非激活的 tab 不会
 * 白白去打这些详情请求**；切过去时命中缓存则同样不打。
 */
export function RecentToolsPanel({ entries }: { entries: RecentToolEntry[] }) {
  const queries = useQueries({
    queries: entries.map((entry) => ({
      queryKey: toolDetailQueryKey(entry.slug),
      queryFn: ({ signal }: QueryFunctionContext<ReturnType<typeof toolDetailQueryKey>>) =>
        fetchToolDetail(entry.slug, signal),
      staleTime: 5 * 60_000,
    })),
  });

  // 取不到详情的条目（已下线 / 转私有 / 被删）直接不出现，不占位、不报错。
  const tools = queries
    .map((query) => query.data)
    .filter((detail): detail is ToolDetail => Boolean(detail))
    .map(detailToListItem);

  if (tools.length === 0) {
    // 还在补详情：给骨架屏，不要先闪一句「不可用」。
    if (queries.some((query) => query.isPending)) {
      return <PageSkeleton variant="cards" count={Math.min(entries.length, 4)} />;
    }
    return (
      <p className="text-sm text-muted-foreground" data-testid="portal-recent-empty">
        这些本地记录对应的工具当前都不可用。
      </p>
    );
  }

  return <ToolGrid tools={tools} testId="portal-recent-grid" />;
}

/* -------------------------------------------------------------------------- */
/* 「我的收藏」：GET /me/favorites 第一页                                       */
/* -------------------------------------------------------------------------- */

/**
 * 我的收藏第一页（§32.2）。**在 `PortalPage` 与面板里各调一次**：
 * 两个调用共用同一个 queryKey，所以只有一个请求、一份缓存。
 *
 * `enabled` 在「未认证 **或没有收藏能力**」时**关掉查询** —— 匿名（以及会话还没
 * 恢复完的 `unknown`）连请求都不发，不白打一个必然 401 的请求（M14 的 F1 硬要求，
 * M16 原样保留）。
 *
 * 监控方裁定（M14 集成期 §30.2）：门用 `canEngage(user)`，**不是** `status === "authenticated"`。
 * §29.8 的字面是「仅登录用户」，但 §25.1 已裁定 `viewer` 不能收藏 ——
 * 它的收藏夹**必然恒空**，为它发这一次请求纯属浪费；用 `canEngage` 与收藏控件的
 * 可见性门保持一致（同一份角色判定，不会两处漂移）。
 */
export function useFavoritesHighlight() {
  const { status, user } = useAuth();
  const enabled = status === "authenticated" && canEngage(user);

  const query = useQuery({
    queryKey: favoritesQueryKey(1, FAVORITES_TAB_PAGE_SIZE),
    queryFn: ({ signal }) => fetchFavorites(1, FAVORITES_TAB_PAGE_SIZE, signal),
    staleTime: 60_000,
    enabled,
  });

  return { enabled, query };
}

/**
 * 「我的收藏」面板：第一页 24 条（`FAVORITES_TAB_PAGE_SIZE`）。
 *
 * `total > 24` 时给一条「查看全部 N 个 →」指向既有的 `/me/favorites` 页
 * —— **不在 tab 内再造一套分页**（§32.2 明确冻结）。那里有完整分页，
 * 而且与这里共用第一页的缓存（同 queryKey）。
 */
export function FavoritesPanel() {
  const { query } = useFavoritesHighlight();

  if (query.isPending) {
    return <PageSkeleton variant="cards" count={8} />;
  }

  const items = query.data?.items ?? [];
  const total = query.data?.total ?? 0;

  return (
    <div className="space-y-4">
      <ToolGrid tools={items} testId="portal-favorites-grid" />
      {total > FAVORITES_TAB_PAGE_SIZE ? (
        <Link
          to="/me/favorites"
          data-testid="portal-favorites-see-all"
          className="inline-flex rounded text-sm text-primary underline-offset-4 hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          查看全部 {total} 个 →
        </Link>
      ) : null}
    </div>
  );
}
