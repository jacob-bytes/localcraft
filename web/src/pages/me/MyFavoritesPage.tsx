import { useQuery } from "@tanstack/react-query";
import { Star } from "lucide-react";
import * as React from "react";
import { Link, useSearchParams } from "react-router-dom";

import { getErrorMessage } from "@/api/client";
import { favoritesQueryKey, fetchFavorites } from "@/api/engagement";
import { DEFAULT_PAGE_SIZE } from "@/api/tools";
import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { PageSkeleton } from "@/components/common/PageSkeleton";
import { Pagination } from "@/components/common/Pagination";
import { ToolGrid } from "@/components/tools/ToolGrid";
import { Button } from "@/components/ui/button";

/**
 * `/me/favorites` —— 「我的收藏」（任务书 F7.3，CONTRACT §23.4）。
 *
 * 两个刻意的选择：
 *
 * 1. **复用门户列表项组件**（`ToolGrid` → `ToolCard`），不新写一套卡片。契约 §23.4
 *    也要求 `/me/favorites` 复用 `ToolListItem` 形状，所以这里连字段映射都不需要。
 * 2. **排序由服务端决定**（`created_at DESC`，§23.3 的索引就是为它建的），前端
 *    不再排序 —— 排序口径只在一个地方。
 *
 * 卡片上的收藏按钮在这里也是切换控件，取消收藏后该工具会从列表消失：
 * `useFavoriteToggle` 的 `onSettled` 会 invalidate `["me","favorites"]`，
 * 所以不需要在本页手写「移除」逻辑。
 */
export default function MyFavoritesPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const parsedPage = Number.parseInt(searchParams.get("page") ?? "1", 10);
  const page = Number.isFinite(parsedPage) && parsedPage >= 1 ? parsedPage : 1;
  const pageSize = DEFAULT_PAGE_SIZE;

  const favoritesQuery = useQuery({
    queryKey: favoritesQueryKey(page, pageSize),
    queryFn: ({ signal }) => fetchFavorites(page, pageSize, signal),
    staleTime: 60_000,
  });

  const items = favoritesQuery.data?.items ?? [];
  const total = favoritesQuery.data?.total;
  const pages = favoritesQuery.data?.pages ?? 1;

  const goToPage = React.useCallback(
    (next: number) => {
      setSearchParams(
        (previous) => {
          const params = new URLSearchParams(previous);
          if (next <= 1) params.delete("page");
          else params.set("page", String(next));
          return params;
        },
        { replace: false },
      );
      window.scrollTo({ top: 0, behavior: "smooth" });
    },
    [setSearchParams],
  );

  return (
    <div
      data-testid="my-favorites-page"
      className="mx-auto w-full max-w-[1400px] space-y-5 px-4 py-6 sm:px-6 lg:px-8"
    >
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">我的收藏</h1>
        <p className="mt-1 text-sm text-muted-foreground" aria-live="polite">
          {total === undefined
            ? "正在加载收藏…"
            : `已收藏 ${total} 个工具，按收藏时间倒序。`}
        </p>
      </header>

      {favoritesQuery.isError ? (
        <ErrorState
          message={getErrorMessage(favoritesQuery.error)}
          onRetry={() => {
            void favoritesQuery.refetch();
          }}
        />
      ) : favoritesQuery.isPending ? (
        <PageSkeleton variant="cards" count={8} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={Star}
          title="还没有收藏任何工具"
          description="在门户卡片右上角或工具详情页点「收藏」，之后就能从这里直达。"
          action={
            <Button asChild variant="outline">
              <Link to="/">去门户看看</Link>
            </Button>
          }
        />
      ) : (
        <div
          className={favoritesQuery.isFetching ? "opacity-60 transition-opacity" : undefined}
          aria-busy={favoritesQuery.isFetching}
        >
          <ToolGrid tools={items} variant="grid" />
        </div>
      )}

      {pages > 1 ? (
        <div className="pt-2">
          <Pagination page={page} pages={pages} onPageChange={goToPage} />
        </div>
      ) : null}
    </div>
  );
}
