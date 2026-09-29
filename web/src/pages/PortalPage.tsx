import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  Info,
  LayoutGrid,
  List,
  Search,
  SlidersHorizontal,
  X,
} from "lucide-react";
import * as React from "react";
import { useSearchParams } from "react-router-dom";

import {
  categoriesQueryKey,
  DEFAULT_PAGE_SIZE,
  fetchCategories,
  fetchTags,
  fetchTools,
  PAGE_SIZE_OPTIONS,
  SORT_OPTIONS,
  tagsQueryKey,
  toolsQueryKey,
} from "@/api/tools";
import type { ToolFacets, ToolListParams, ToolSort, ToolType } from "@/api/types";
import { EmptyState } from "@/components/common/EmptyState";
import { NoResultsArt, NoToolsArt } from "@/components/common/EmptyStateArt";
import { ErrorState } from "@/components/common/ErrorState";
import { Markdown } from "@/components/common/Markdown";
import { PageSkeleton } from "@/components/common/PageSkeleton";
import { Pagination } from "@/components/common/Pagination";
import { PortalSideNav } from "@/components/layout/PortalSideNav";
import {
  FavoritesPanel,
  readPortalTab,
  RecentToolsPanel,
  useFavoritesHighlight,
  useRecentToolEntries,
  type PortalTab,
} from "@/components/portal/PortalHighlights";
import { ToolFilters } from "@/components/tools/ToolFilters";
import { ToolGrid } from "@/components/tools/ToolGrid";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useDebounce } from "@/hooks/useDebounce";
import { siteSubtitle, useMeta } from "@/hooks/useMeta";
import { TOOL_TYPE_META } from "@/lib/toolMeta";
import { cn } from "@/lib/utils";

const VIEW_STORAGE_KEY = "localcraft-portal-view";
const ANNOUNCEMENT_STORAGE_KEY = "localcraft-announcement-dismissed";

type PortalView = "grid" | "list";

const TOOL_TYPE_SET = new Set<string>(Object.keys(TOOL_TYPE_META));
const SORT_SET = new Set<string>(SORT_OPTIONS.map((option) => option.value));

function isToolType(value: string): value is ToolType {
  return TOOL_TYPE_SET.has(value);
}

function isToolSort(value: string | null): value is ToolSort {
  return value !== null && SORT_SET.has(value);
}

function readStoredView(): PortalView {
  try {
    const stored = window.localStorage.getItem(VIEW_STORAGE_KEY);
    if (stored === "grid" || stored === "list") return stored;
  } catch {
    /* ignore */
  }
  return "grid";
}

interface AppliedPatch {
  [key: string]: string | string[] | null;
}

/**
 * `/` — the portal (docs/04 §6.3). The M1 centrepiece.
 *
 * All filter / sort / pagination state lives in the URL query string, so the
 * view is shareable, survives F5 and works with browser back/forward
 * (acceptance #6). `facets` is only returned by the API for `page === 1`, so the
 * last known facets are cached in state — otherwise the category counters would
 * disappear as soon as the user paged forward (and the layout would jump).
 */
export default function PortalPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { data: meta } = useMeta();
  const subtitle = siteSubtitle(meta);

  /* ---- read state from the URL ---- */
  const q = searchParams.get("q") ?? "";
  const category = searchParams.get("category");
  const types = searchParams.getAll("type").filter(isToolType);
  const tags = searchParams.getAll("tag");
  const sortParam = searchParams.get("sort");
  const sort: ToolSort = isToolSort(sortParam) ? sortParam : (meta?.default_sort ?? "hot");
  const parsedPage = Number.parseInt(searchParams.get("page") ?? "1", 10);
  const page = Number.isFinite(parsedPage) && parsedPage >= 1 ? parsedPage : 1;
  const parsedPageSize = Number.parseInt(searchParams.get("page_size") ?? "", 10);
  const pageSize = (PAGE_SIZE_OPTIONS as readonly number[]).includes(parsedPageSize)
    ? parsedPageSize
    : (meta?.page_size ?? DEFAULT_PAGE_SIZE);

  const [view, setView] = React.useState<PortalView>(readStoredView);
  const [facets, setFacets] = React.useState<ToolFacets | null>(null);
  const [searchInput, setSearchInput] = React.useState(q);
  const [filtersOpen, setFiltersOpen] = React.useState(false);
  const debouncedSearch = useDebounce(searchInput, 300);

  const updateParams = React.useCallback(
    (patch: AppliedPatch, options?: { resetPage?: boolean; replace?: boolean }) => {
      setSearchParams(
        (previous) => {
          const next = new URLSearchParams(previous);
          for (const [key, value] of Object.entries(patch)) {
            next.delete(key);
            if (value === null) continue;
            if (Array.isArray(value)) {
              for (const item of value) next.append(key, item);
            } else {
              next.set(key, value);
            }
          }
          if (options?.resetPage && patch["page"] === undefined) next.delete("page");
          return next;
        },
        { replace: options?.replace ?? false },
      );
    },
    [setSearchParams],
  );

  /* Keep the search box in sync when the URL changes from the outside
     (browser back/forward, ⌘K jump, a shared link). This is the "store
     information from previous renders" pattern — no effect, no extra render
     round trip. */
  const [syncedQuery, setSyncedQuery] = React.useState(q);
  if (syncedQuery !== q) {
    setSyncedQuery(q);
    setSearchInput(q);
  }

  /* ---- debounced input → URL (docs/04 §9: 300ms) ---- */
  React.useEffect(() => {
    const trimmed = debouncedSearch.trim();
    // Ignore a debounced value that has not caught up with the input yet.
    // Without this guard an *external* URL change (⌘K jump, browser
    // back/forward, 清除全部) would immediately be overwritten by the previous
    // keyword still sitting in the debounce timer.
    if (searchInput.trim() !== trimmed) return;
    if (trimmed === q) return;
    updateParams({ q: trimmed || null }, { resetPage: true, replace: true });
  }, [debouncedSearch, searchInput, q, updateParams]);

  const typesKey = types.join(",");
  const tagsKey = tags.join(",");
  // Rebuilt from primitives only: `types`/`tags` are fresh arrays on every
  // render, so depending on them by identity would defeat the memo.
  const params = React.useMemo<ToolListParams>(
    () => ({
      q: q || undefined,
      category: category ? [category] : undefined,
      type: typesKey ? (typesKey.split(",") as ToolType[]) : undefined,
      tag: tagsKey ? tagsKey.split(",") : undefined,
      sort,
      page,
      page_size: pageSize,
    }),
    [q, category, typesKey, tagsKey, sort, page, pageSize],
  );

  const toolsQuery = useQuery({
    queryKey: toolsQueryKey(params),
    queryFn: ({ signal }) => fetchTools(params, signal),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });

  /* facets only arrive on page 1 — remember the last set so the category
     counters do not vanish (and the layout does not jump) while paging.
     Guarded by identity, so this settles after one extra render. */
  const incomingFacets = toolsQuery.data?.facets;
  if (incomingFacets && incomingFacets !== facets) {
    setFacets(incomingFacets);
  }

  const categoriesQuery = useQuery({
    queryKey: categoriesQueryKey,
    queryFn: ({ signal }) => fetchCategories(signal),
    staleTime: 5 * 60_000,
  });

  const tagsQuery = useQuery({
    queryKey: tagsQueryKey(""),
    queryFn: ({ signal }) => fetchTags(undefined, signal),
    staleTime: 5 * 60_000,
  });

  const categoryCounts = React.useMemo(() => {
    if (!facets) return undefined;
    const counts: Record<string, number> = {};
    for (const entry of facets.categories) counts[entry.slug] = entry.count;
    return counts;
  }, [facets]);

  const typeCounts = React.useMemo(() => {
    if (!facets) return undefined;
    const counts: Record<string, number> = {};
    for (const entry of facets.types) counts[entry.value] = entry.count;
    return counts;
  }, [facets]);

  const hasFilters = q.trim() !== "" || category !== null || types.length > 0 || tags.length > 0;

  const toggleType = (type: ToolType) => {
    const next = types.includes(type) ? types.filter((item) => item !== type) : [...types, type];
    updateParams({ type: next }, { resetPage: true });
  };

  const toggleTag = (tag: string) => {
    const next = tags.includes(tag) ? tags.filter((item) => item !== tag) : [...tags, tag];
    updateParams({ tag: next }, { resetPage: true });
  };

  const selectCategory = (slug: string | null) => {
    updateParams({ category: slug }, { resetPage: true });
  };

  const clearAll = () => {
    setSearchInput("");
    updateParams({ category: null, type: null, tag: null, q: null }, { resetPage: true });
  };

  const changeView = (next: string) => {
    if (next !== "grid" && next !== "list") return;
    setView(next);
    try {
      window.localStorage.setItem(VIEW_STORAGE_KEY, next);
    } catch {
      /* ignore */
    }
  };

  const items = toolsQuery.data?.items ?? [];
  const total = toolsQuery.data?.total;
  const pages = toolsQuery.data?.pages ?? 1;
  const isFirstLoad = toolsQuery.isPending;

  const filterProps = {
    categories: categoriesQuery.data,
    categoryCounts,
    typeCounts,
    tags: tagsQuery.data,
    selection: { category, types, tags, query: q },
    onSelectCategory: selectCategory,
    onToggleType: toggleType,
    onToggleTag: toggleTag,
    onClearQuery: () => {
      setSearchInput("");
      updateParams({ q: null }, { resetPage: true });
    },
    onClearAll: clearAll,
    total,
  } as const;

  /* ---- M16 · F1（CONTRACT §32.2）：门户展示结构改为 tab ---- */

  /*
   * 三个 tab 的**候选**，只列有内容的（§32.2）：
   *   - 「继续使用」：本地有最近访问记录才出现，计数 = 本地记录条数；
   *   - 「我的收藏」：登录（且可收藏）**且**收藏夹非空才出现，计数 = `total`。
   * 两者都在这里判定，而不是在面板里 —— Radix 的 TabsContent 只在激活时挂载，
   * 「这个 tab 该不该出现」不能等面板挂载才知道。
   */
  const recentEntries = useRecentToolEntries();
  const favorites = useFavoritesHighlight();
  const favoritesTotal = favorites.query.data?.total ?? 0;
  const hasRecentTab = recentEntries.length > 0;
  const hasFavoritesTab = favorites.enabled && favoritesTotal > 0;

  const tabs: { value: PortalTab; label: string; count: number | undefined }[] = [
    { value: "all", label: "全部工具", count: total },
    ...(hasRecentTab
      ? [{ value: "recent" as PortalTab, label: "继续使用", count: recentEntries.length }]
      : []),
    ...(hasFavoritesTab
      ? [{ value: "favorites" as PortalTab, label: "我的收藏", count: favoritesTotal }]
      : []),
  ];

  /*
   * `?tab=` 是唯一状态源（与 q/category/type/tag/sort/page 同一套做法：可分享、
   * F5 保持、e2e 能直接访问）。取不到 / 非法 / **当前不可用**（例如清空收藏后再
   * 打开一个旧的 `?tab=favorites` 链接）→ 一律回落「全部工具」，
   * 不渲染空面板；也不回写 URL（URL 只在用户**点 tab** 时变）。
   */
  const requestedTab = readPortalTab(searchParams.get("tab"));
  const activeTab = tabs.some((entry) => entry.value === requestedTab) ? requestedTab : "all";

  /*
   * ★ 只有一个 tab 时**整条 tab 栏都不渲染**（§32.2 冻结）。
   * 默认部署（无最近访问、无收藏）因此与改动前**逐字一致**：下面直接渲染
   * `allToolsContent`，连 Radix 的 Tabs 外壳都不会出现（没有空壳容器、
   * 没有「一行只有一个 tab」的控件、没有指向不存在触发器的 tabpanel）。
   */
  const showTabs = tabs.length > 1;

  const changeTab = (next: string) => {
    /*
     * 默认值不进 URL（`all` 就是默认 tab）—— 与既有的 page/page_size 归位是同一套
     * 约定：`?tab=all` 照样被接受（readPortalTab），只是不作为规范形式写回去。
     */
    const value = readPortalTab(next);
    updateParams({ tab: value === "all" ? null : value });
  };

  /*
   * 「全部工具」这一屏：筛选栏 + 工具栏 + 结果 + 分页，**与改动前逐字相同**。
   * 单 tab 时它被直接渲染，多 tab 时它是 `<TabsContent value="all">` 的内容 ——
   * 工具栏与分页因此只在「全部工具」下存在（§32.2）。
   */
  const allToolsContent = (
    <div className="lg:grid lg:grid-cols-[16rem_minmax(0,1fr)] lg:gap-6">
      {/* ---- Left rail (lg+) ---- */}
      <aside aria-label="筛选条件" className="hidden lg:block">
        <div className="sticky top-20 rounded-xl border bg-card p-2">
          {/* M8 · F7.4：「我的收藏」入口（匿名不渲染；M9/§25.1 起 `viewer`
              同样不渲染 —— 它收藏不了任何东西。判定都在 PortalSideNav 里）。 */}
          <PortalSideNav />
          <ToolFilters {...filterProps} />
        </div>
      </aside>

      <div className="min-w-0">
        {/* ---- Toolbar ---- */}
        <div className="flex flex-wrap items-center gap-2 pb-3">
          <p className="text-sm text-muted-foreground" aria-live="polite">
            {total === undefined ? "加载中…" : `共 ${total} 个工具`}
          </p>

          <div className="ml-auto flex flex-wrap items-center gap-2">
            <Sheet open={filtersOpen} onOpenChange={setFiltersOpen}>
              <SheetTrigger asChild>
                <Button type="button" variant="outline" size="sm" className="lg:hidden">
                  <SlidersHorizontal aria-hidden="true" className="size-4" />
                  筛选
                  {hasFilters ? (
                    <span className="ml-1 rounded-full bg-primary px-1.5 text-xs text-primary-foreground">
                      {[category !== null, types.length > 0, tags.length > 0, q.trim() !== ""]
                        .filter(Boolean)
                        .length}
                    </span>
                  ) : null}
                </Button>
              </SheetTrigger>
              <SheetContent side="left" className="w-[19rem] overflow-y-auto">
                <SheetHeader>
                  <SheetTitle>筛选</SheetTitle>
                </SheetHeader>
                <div className="px-4 pb-8">
                  <PortalSideNav />
                  <ToolFilters {...filterProps} />
                </div>
              </SheetContent>
            </Sheet>

            <Select
              value={sort}
              onValueChange={(value) => updateParams({ sort: value }, { resetPage: true })}
            >
              <SelectTrigger size="sm" className="w-[8.5rem]" aria-label="排序方式">
                <SelectValue placeholder="排序" />
              </SelectTrigger>
              <SelectContent>
                {SORT_OPTIONS.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <ToggleGroup
              type="single"
              value={view}
              onValueChange={changeView}
              variant="outline"
              size="sm"
              aria-label="视图切换"
            >
              <ToggleGroupItem value="grid" aria-label="卡片视图">
                <LayoutGrid aria-hidden="true" className="size-4" />
              </ToggleGroupItem>
              <ToggleGroupItem value="list" aria-label="列表视图">
                <List aria-hidden="true" className="size-4" />
              </ToggleGroupItem>
            </ToggleGroup>

            <Select
              value={String(pageSize)}
              onValueChange={(value) =>
                updateParams({ page_size: value }, { resetPage: true })
              }
            >
              <SelectTrigger size="sm" className="w-[7.5rem]" aria-label="每页条数">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PAGE_SIZE_OPTIONS.map((option) => (
                  <SelectItem key={option} value={String(option)}>
                    每页 {option}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        {/* ---- Results ---- */}
        {toolsQuery.isError ? (
          <ErrorState
            message={
              toolsQuery.error instanceof Error ? toolsQuery.error.message : "请稍后重试"
            }
            onRetry={() => {
              void toolsQuery.refetch();
            }}
          />
        ) : isFirstLoad ? (
          <PageSkeleton variant={view === "grid" ? "cards" : "list"} count={view === "grid" ? 8 : 10} />
        ) : items.length === 0 ? (
          hasFilters ? (
            <EmptyState
              illustration={<NoResultsArt />}
              title="没有找到匹配的工具"
              description="试试减少筛选条件，或换一个关键词。"
              action={
                <Button type="button" variant="outline" onClick={clearAll}>
                  清除筛选条件
                </Button>
              }
            />
          ) : (
            <EmptyState
              illustration={<NoToolsArt />}
              title="平台上还没有工具"
              description="等第一个工具上架后，这里就会热闹起来。"
            />
          )
        ) : (
          <div
            className={cn(
              "transition-opacity",
              toolsQuery.isFetching && "opacity-60",
            )}
            aria-busy={toolsQuery.isFetching}
          >
            <ToolGrid tools={items} variant={view} />
          </div>
        )}

        {/* Pagination stays visible on out-of-range pages (docs/04 §6.3) */}
        <div className="pt-6">
          <Pagination
            page={page}
            pages={pages}
            onPageChange={(next) => updateParams({ page: String(next) })}
          />
        </div>
      </div>
    </div>
  );

  return (
    <div className="mx-auto w-full max-w-[1400px] px-4 py-6 sm:px-6 lg:px-8">
      {/* ---- Hero search ---- */}
      <section aria-labelledby="portal-hero-title" className="pb-4">
        <h1 id="portal-hero-title" className="text-2xl font-semibold tracking-tight">
          发现内网工具与 Skill
        </h1>
        {/* M12 · F1：副标题紧跟 H1（CONTRACT §27.4）。未配置时**不渲染元素**，
            不是渲染一个空 <p> —— 空元素会留下 margin，等于给既有部署加了一行空隙。 */}
        {subtitle ? (
          <p data-testid="site-subtitle" className="mt-1 text-base text-muted-foreground">
            {subtitle}
          </p>
        ) : null}
        <p className="mt-1 text-sm text-muted-foreground">
          搜索工具名称、简介与标签，或按分类、类型、标签筛选。
        </p>
        <div className="relative mt-4 max-w-2xl">
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            type="search"
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            placeholder="搜索工具名称、简介、标签…"
            aria-label="搜索工具"
            className="h-12 pl-10 pr-10 text-base"
          />
          {searchInput ? (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="absolute right-1 top-1.5 size-9"
              aria-label="清空搜索"
              onClick={() => setSearchInput("")}
            >
              <X aria-hidden="true" className="size-4" />
            </Button>
          ) : null}
        </div>
      </section>

      <Announcement markdown={meta?.announcement_md ?? null} />

      {showTabs ? (
        <Tabs value={activeTab} onValueChange={changeTab} className="gap-4">
          {/*
            tab 栏用既有的 components/ui/tabs（Radix）—— role/aria/方向键导航都是它
            自带的，手写一排按钮会漏掉键盘可达性（§32.2「无障碍（冻结）」）。
          */}
          <TabsList
            aria-label="门户视图"
            data-testid="portal-tabs"
            className="max-w-full overflow-x-auto"
          >
            {tabs.map((entry) => (
              <TabsTrigger key={entry.value} value={entry.value}>
                {entry.label}
                {entry.count === undefined ? null : (
                  <span className="text-xs tabular-nums">({entry.count})</span>
                )}
              </TabsTrigger>
            ))}
          </TabsList>

          <TabsContent value="all">{allToolsContent}</TabsContent>

          {hasRecentTab ? (
            <TabsContent value="recent">
              <RecentToolsPanel entries={recentEntries} />
            </TabsContent>
          ) : null}

          {hasFavoritesTab ? (
            <TabsContent value="favorites">
              <FavoritesPanel />
            </TabsContent>
          ) : null}
        </Tabs>
      ) : (
        allToolsContent
      )}
    </div>
  );
}

/**
 * Dismissible announcement (docs/04 §6.3, FR-PORTAL-08). Rendered as Markdown
 * through the shared pipeline; the dismissal is remembered per announcement text.
 */
function Announcement({ markdown }: { markdown: string | null }) {
  const [dismissed, setDismissed] = React.useState(() => {
    try {
      return window.localStorage.getItem(ANNOUNCEMENT_STORAGE_KEY) === markdown;
    } catch {
      return false;
    }
  });

  if (!markdown || dismissed) return null;

  return (
    <section
      aria-label="公告"
      className="mb-4 flex items-start gap-3 rounded-xl border border-primary/25 bg-accent/40 px-4 py-3"
    >
      <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-primary" />
      <div className="min-w-0 flex-1 text-sm [&_p]:my-0">
        <Markdown source={markdown} className="prose-sm" />
      </div>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="size-7 shrink-0"
        aria-label="关闭公告"
        onClick={() => {
          setDismissed(true);
          try {
            window.localStorage.setItem(ANNOUNCEMENT_STORAGE_KEY, markdown);
          } catch {
            /* ignore */
          }
        }}
      >
        <X aria-hidden="true" className="size-4" />
      </Button>
    </section>
  );
}
