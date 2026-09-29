import { useQueries, useQuery, type QueryFunctionContext } from "@tanstack/react-query";
import * as React from "react";

import { favoritesQueryKey, fetchFavorites } from "@/api/engagement";
import { DEFAULT_PAGE_SIZE, fetchToolDetail, toolDetailQueryKey } from "@/api/tools";
import type { ToolDetail, ToolListItem } from "@/api/types";
import { ToolCard } from "@/components/tools/ToolCard";
import { useAuth } from "@/hooks/useAuth";
import { canEngage } from "@/lib/permissions";
import { readRecentTools, type RecentToolEntry } from "@/lib/recentTools";
import { cn } from "@/lib/utils";
import { readWebappHealthStatus } from "@/lib/webappHealth";

/**
 * M14 · F1（CONTRACT §29.8）：门户首页的两个「亮点」区块 —— 公告下方、工具栏上方。
 *
 * 存在的理由（用户洞察）：这两份数据**早就有了**，但页面上一个字都看不到 ——
 * 「最近访问」只喂给 ⌘K 面板（`lib/recentTools`），「我的收藏」只有专门页面。
 * 而日常使用者 90% 的动作是「打开门户 → 找那个我常用的工具」。
 *
 * 三条硬约束，全部落在这个文件里：
 *
 * 1. **任一为空则整块不渲染**（不放空标题、不留空白间距）。默认部署（没收藏、
 *    也没访问过）页面必须与加这个功能之前**逐字一致** —— 所以每个区块是
 *    「自己 return null」，而不是渲染一个空壳；也没有共同的外层容器，
 *    否则那层容器自己的 margin 就是凭空多出来的空白。
 * 2. **「继续使用」只读既有 `localStorage`，不新增存储键**（`readRecentTools`），
 *    且**匿名也显示** —— 它本来就是本地数据，与服务端无关。
 * 3. **「我的收藏」仅登录用户取数**：匿名连请求都不发（`enabled`），
 *    不白打一个必 401 的请求。
 *
 * 卡片**复用既有 `ToolCard`**（§29.8 末条），没有第二套卡片组件。
 */

/** 两个区块各自最多（§29.8 / F1 表格：「最多 6 个」）。 */
export const HIGHLIGHT_TOOLS_LIMIT = 6;

/** 横向列表里每张卡的宽度。`shrink-0` 是「不产生页级横向滚动」的关键。 */
const CARD_ITEM_CLASS = "w-64 shrink-0 snap-start sm:w-72";

/**
 * 区块外壳：可访问的标题语义 + 一个**自身可横向滚动**的列表。
 *
 * 窄屏下卡片不换行、不压缩，溢出被 `<ul>` 的 `overflow-x-auto` 吃掉，
 * **不会**把页面撑宽（`overscroll-x-contain` 顺带阻止滚动链传到页面）。
 */
function HighlightSection({
  id,
  title,
  testId,
  children,
}: {
  id: string;
  title: string;
  testId: string;
  children: React.ReactNode;
}) {
  return (
    <section aria-labelledby={id} data-testid={testId} className="mb-4">
      <h2 id={id} className="text-sm font-semibold tracking-tight">
        {title}
      </h2>
      <ul className="mt-2 flex snap-x gap-4 overflow-x-auto overscroll-x-contain pb-1">
        {children}
      </ul>
    </section>
  );
}

function HighlightItem({ children }: { children: React.ReactNode }) {
  return <li className={cn("flex", CARD_ITEM_CLASS)}>{children}</li>;
}

/**
 * `ToolDetail` → `ToolListItem`。
 *
 * 为什么需要它：`recentTools` 只存 `{slug, name, at}`（§29.8 明确不许改存储形状），
 * 而 `ToolCard` 吃的是完整的 `ToolListItem`。列表接口没有「按 slug 批量取」的能力
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
 * 「继续使用」：`localStorage` 里的最近访问（M7 建的，最多 8 条，这里取前 6）。
 *
 * **匿名同样显示** —— 这是本地数据，登录与否不影响（F1 硬要求 2）。
 *
 * 取数说明：`ToolDetailPage` 用的是**同一个 queryKey**（`toolDetailQueryKey`），
 * 且 `staleTime` 与详情页一致（5 分钟）。所以主场景 —— 「刚看完那个工具，退回门户」
 * —— 数据已经在 react-query 缓存里，**一个请求都不发**；只有上一次会话留下的旧条目
 * 才需要补一次详情请求（每个 slug 最多 1 次，4xx 不重试，见 `lib/queryClient`）。
 */
export function ContinueUsingSection() {
  // 挂载时读一次：门户页在进入详情时会卸载，退回来就是一次重新挂载（重新读）。
  const [entries] = React.useState<RecentToolEntry[]>(() =>
    readRecentTools().slice(0, HIGHLIGHT_TOOLS_LIMIT),
  );

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

  if (tools.length === 0) return null;

  return (
    <HighlightSection id="portal-continue-title" title="继续使用" testId="portal-continue-using">
      {tools.map((tool) => (
        <HighlightItem key={tool.slug}>
          <ToolCard tool={tool} />
        </HighlightItem>
      ))}
    </HighlightSection>
  );
}

/**
 * 「我的收藏」：`GET /me/favorites`（既有接口，§23.4）。
 *
 * `enabled` 在「未认证 **或没有收藏能力**」时**关掉查询** —— 匿名（以及会话还没
 * 恢复完的 `unknown`）连请求都不发，不白打一个必然 401 的请求（F1 硬要求 3）。
 *
 * 监控方裁定（M14 集成期）：门用 `canEngage(user)`，**不是** `status === "authenticated"`。
 * §29.8 的字面是「仅登录用户」，但 §25.1 已裁定 `viewer` 不能收藏 ——
 * 它的收藏夹**必然恒空**，为它发这一次请求纯属浪费；用 `canEngage` 与收藏控件的
 * 可见性门保持一致（同一份角色判定，不会两处漂移）。
 *
 * queryKey / pageSize 与 `/me/favorites` 页面**完全一致**（`favoritesQueryKey(1, 24)`），
 * 所以这两个界面共享同一份缓存：从门户点进「我的收藏」不会再拉一次。
 * 只渲染前 6 张（§29.8）。
 */
export function FavoritesSection() {
  const { status, user } = useAuth();
  const enabled = status === "authenticated" && canEngage(user);

  const favoritesQuery = useQuery({
    queryKey: favoritesQueryKey(1, DEFAULT_PAGE_SIZE),
    queryFn: ({ signal }) => fetchFavorites(1, DEFAULT_PAGE_SIZE, signal),
    staleTime: 60_000,
    enabled,
  });

  const items = (favoritesQuery.data?.items ?? []).slice(0, HIGHLIGHT_TOOLS_LIMIT);
  if (!enabled || items.length === 0) return null;

  return (
    <HighlightSection id="portal-favorites-title" title="我的收藏" testId="portal-favorites-highlight">
      {items.map((tool) => (
        <HighlightItem key={tool.id}>
          <ToolCard tool={tool} />
        </HighlightItem>
      ))}
    </HighlightSection>
  );
}

/** 两个区块。任一为空时它自己不渲染，外层不留任何壳。 */
export function PortalHighlights() {
  return (
    <>
      <ContinueUsingSection />
      <FavoritesSection />
    </>
  );
}
