import { useQuery } from "@tanstack/react-query";
import { Search, Tags } from "lucide-react";
import * as React from "react";
import { useSearchParams } from "react-router-dom";

import { adminTagsQueryKey, fetchAdminTags } from "@/api/admin";
import { getErrorMessage } from "@/api/client";
import { TagManager } from "@/components/admin/TagManager";
import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { PageSkeleton } from "@/components/common/PageSkeleton";
import { Pagination } from "@/components/common/Pagination";
import { Input } from "@/components/ui/input";
import { useDebounce } from "@/hooks/useDebounce";

/**
 * `/admin/tags` —— 标签管理（docs/04 §6.16，FR-TAX-03/05/06）。
 *
 * 页面只负责「列表 + 搜索 + 分页」这段可分享的状态：`q` 与 `page` 都写进 URL
 * （docs/04 §6.3 的约定），搜索防抖 300ms 后才落 URL 并触发请求（docs/04 §9）。
 * 重命名 / 合并 / 清理三个动作都在 `TagManager` 里。
 */
export default function TagsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const q = searchParams.get("q") ?? "";
  const parsedPage = Number.parseInt(searchParams.get("page") ?? "1", 10);
  const page = Number.isFinite(parsedPage) && parsedPage >= 1 ? parsedPage : 1;

  const [searchInput, setSearchInput] = React.useState(q);
  const debouncedSearch = useDebounce(searchInput, 300);

  /* URL 是唯一事实来源：浏览器前进/后退、外部跳转都要回灌输入框。 */
  const [syncedQuery, setSyncedQuery] = React.useState(q);
  if (syncedQuery !== q) {
    setSyncedQuery(q);
    setSearchInput(q);
  }

  React.useEffect(() => {
    const trimmed = debouncedSearch.trim();
    // 防抖还没追上输入时不写 URL，否则外部改动会被旧关键词覆盖（同 PortalPage）。
    if (searchInput.trim() !== trimmed) return;
    if (trimmed === q) return;
    setSearchParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        if (trimmed) next.set("q", trimmed);
        else next.delete("q");
        next.delete("page");
        return next;
      },
      { replace: true },
    );
  }, [debouncedSearch, searchInput, q, setSearchParams]);

  const tagsQuery = useQuery({
    // 键里额外带上 `page`：合并对话框的目标搜索用的是同一个
    // `adminTagsQueryKey(q)`，不带 page 的话翻页结果会被对话框的搜索覆盖。
    queryKey: [...adminTagsQueryKey(q), page],
    queryFn: ({ signal }) => fetchAdminTags({ q, page }, signal),
  });

  const data = tagsQuery.data;
  const tags = React.useMemo(() => data?.items ?? [], [data]);

  const setPage = React.useCallback(
    (nextPage: number) => {
      setSearchParams(
        (current) => {
          const next = new URLSearchParams(current);
          if (nextPage > 1) next.set("page", String(nextPage));
          else next.delete("page");
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  return (
    <div className="flex flex-col gap-4" data-testid="tags-page">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">标签管理</h1>
        <p className="mt-1 text-xs text-muted-foreground">
          标签由上传者自由使用（FR-TAX-03），这里负责治理重复项：重命名、合并与清理零引用。
          删除标签请用「合并」——直接把 A 并入 B 不会打断已有工具的引用。
        </p>
      </div>

      <div className="relative max-w-sm">
        <Search
          aria-hidden="true"
          className="pointer-events-none absolute top-1/2 left-2 size-4 -translate-y-1/2 text-muted-foreground"
        />
        <Input
          value={searchInput}
          onChange={(event) => setSearchInput(event.target.value)}
          placeholder="搜索标签（服务端前缀匹配）"
          aria-label="搜索标签"
          data-testid="tag-search-input"
          className="pl-8"
        />
      </div>

      {tagsQuery.isPending ? (
        <PageSkeleton variant="list" />
      ) : tagsQuery.isError ? (
        <ErrorState
          message={getErrorMessage(tagsQuery.error)}
          onRetry={() => void tagsQuery.refetch()}
        />
      ) : tags.length === 0 ? (
        <EmptyState
          icon={Tags}
          title={q ? "没有匹配的标签" : "还没有标签"}
          description={
            q
              ? `没有归一化名以「${q}」开头的标签。清空搜索可以看全部。`
              : "标签会随着工具上传自动创建，上传第一个带标签的工具后再回来治理。"
          }
        />
      ) : (
        <>
          <TagManager tags={tags} total={data?.total ?? tags.length} />
          <Pagination page={data?.page ?? page} pages={data?.pages ?? 1} onPageChange={setPage} />
        </>
      )}
    </div>
  );
}
