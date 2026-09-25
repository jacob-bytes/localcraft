import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Download,
  Eye,
  Globe,
  Link2,
  Lock,
  MoreHorizontal,
  Pencil,
  Plus,
  Send,
  Trash2,
  TriangleAlert,
  Undo2,
  Layers,
} from "lucide-react";
import * as React from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import { getErrorMessage } from "@/api/client";
import {
  deleteTool,
  deleteVersion,
  fetchMyStats,
  fetchMyTools,
  myStatsQueryKey,
  myToolsQueryKey,
  submitTool,
  withdrawTool,
} from "@/api/me";
import type { MyToolListItem, ToolStatus, Usage } from "@/api/types";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { CopyButton } from "@/components/common/CopyButton";
import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { PageSkeleton } from "@/components/common/PageSkeleton";
import { Pagination } from "@/components/common/Pagination";
import { ToolTypeBadge } from "@/components/tools/ToolTypeBadge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { formatCount, formatDateTime, formatRelativeTime } from "@/lib/format";
import { categoryTintClass, VISIBILITY_LABELS } from "@/lib/toolMeta";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 10;

type TabKey = "all" | "draft" | "pending" | "published" | "rejected" | "offline";

interface TabSpec {
  key: TabKey;
  label: string;
  /** `status` query values; `undefined` = no filter (全部). */
  statuses: ToolStatus[] | undefined;
  /** Which `Usage` counter to show, or `null` when the backend has none. */
  countKey: keyof Usage | null;
}

/**
 * Tab → status mapping. 待审 includes `pending_update` (a submitted tool with a
 * version waiting) and 已发布 also includes `pending_update` because the current
 * version keeps serving (docs/01 §4.1).
 *
 * 已驳回 / 已下架 carry NO count badge on purpose: `UsageOut` has no counter for
 * them and there is no "count per status" endpoint. See the report's contract
 * gap list.
 */
const TABS: readonly TabSpec[] = [
  { key: "all", label: "全部", statuses: undefined, countKey: "tool_count" },
  { key: "draft", label: "草稿", statuses: ["draft"], countKey: "draft_tool_count" },
  { key: "pending", label: "待审", statuses: ["pending", "pending_update"], countKey: "pending_tool_count" },
  { key: "published", label: "已发布", statuses: ["approved", "pending_update"], countKey: "published_tool_count" },
  { key: "rejected", label: "已驳回", statuses: ["rejected"], countKey: null },
  { key: "offline", label: "已下架", statuses: ["offline"], countKey: null },
];

const STATUS_VARIANTS: Record<ToolStatus, "secondary" | "warning" | "success" | "destructive" | "outline"> = {
  draft: "secondary",
  pending: "warning",
  approved: "success",
  rejected: "destructive",
  pending_update: "warning",
  offline: "outline",
};

function tabFromParam(value: string | null): TabKey {
  const found = TABS.find((tab) => tab.key === value);
  return found?.key ?? "all";
}

/**
 * `/me/tools` — 我的工具（docs/04 §6.6）。
 *
 * The active tab lives in `?status=` so the view survives a refresh and can be
 * linked (docs/04 §9 "查询键规范化"). Rejected rows get an expanded, destructive
 * alert with a one-click 修改后重新提交 — the highest-value flow on this page.
 */
export default function MyToolsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const tab = tabFromParam(searchParams.get("status"));
  const spec = TABS.find((item) => item.key === tab) ?? TABS[0]!;
  const parsedPage = Number.parseInt(searchParams.get("page") ?? "1", 10);
  const page = Number.isFinite(parsedPage) && parsedPage >= 1 ? parsedPage : 1;
  const [pendingDelete, setPendingDelete] = React.useState<MyToolListItem | null>(null);

  const params = React.useMemo(
    () => ({ status: spec.statuses, page, page_size: PAGE_SIZE }),
    [spec.statuses, page],
  );

  const toolsQuery = useQuery({
    queryKey: myToolsQueryKey(params),
    queryFn: ({ signal }) => fetchMyTools(params, signal),
    placeholderData: (previous) => previous,
  });

  const statsQuery = useQuery({
    queryKey: myStatsQueryKey,
    queryFn: ({ signal }) => fetchMyStats(signal),
    staleTime: 30_000,
  });

  const statusMutation = useMutation({
    mutationFn: ({
      id,
      action,
      version,
    }: {
      id: number;
      action: "submit" | "withdraw" | "withdraw_version";
      version?: string;
    }) => {
      if (action === "submit") return submitTool(id);
      // M6（前端 M5 第 3 节）：`pending_update` 的撤回必须走**删除待审版本** ——
      // `WITHDRAWAL` 动作只覆盖 `pending`，对 `pending_update` 会返回 409 STATE_CONFLICT。
      // 后端 J-5 保证删掉最后一个待审版本后工具状态回落为 approved。
      if (action === "withdraw_version") {
        if (!version) throw new Error("缺少待审版本号");
        return deleteVersion(id, version);
      }
      return withdrawTool(id);
    },
    onSuccess: (_data, variables) => {
      toast.success(
        variables.action === "submit"
          ? "已提交审批"
          : variables.action === "withdraw_version"
            ? "已撤回待审版本，工具回退到当前版本继续服务"
            : "已撤回提交",
      );
      // Prefix invalidation (no args) on purpose — the live key carries the
      // active tab's statuses and page size (docs/04 §9).
      void queryClient.invalidateQueries({ queryKey: ["me", "tools"] });
      void queryClient.invalidateQueries({ queryKey: myStatsQueryKey });
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteTool(id),
    onSuccess: () => {
      toast.success("工具已删除");
      setPendingDelete(null);
      // Prefix invalidation (no args) on purpose — the live key carries the
      // active tab's statuses and page size (docs/04 §9).
      void queryClient.invalidateQueries({ queryKey: ["me", "tools"] });
      void queryClient.invalidateQueries({ queryKey: myStatsQueryKey });
    },
    onError: (error) => {
      toast.error(getErrorMessage(error));
      setPendingDelete(null);
    },
  });

  function updateParams(patch: Record<string, string | null>) {
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      for (const [key, value] of Object.entries(patch)) {
        if (value === null) next.delete(key);
        else next.set(key, value);
      }
      return next;
    });
  }

  const items = toolsQuery.data?.items ?? [];
  const usage = statsQuery.data;

  return (
    <div
      data-testid="my-tools-page"
      className="mx-auto w-full max-w-5xl space-y-5 px-4 py-6 sm:px-6 lg:px-8"
    >
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">我的工具</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            管理草稿、审批进度与已发布工具的版本。
          </p>
        </div>
        <Button asChild data-testid="create-tool-button">
          <Link to="/me/tools/new">
            <Plus aria-hidden="true" className="size-4" />
            新建工具
          </Link>
        </Button>
      </header>

      <div
        role="tablist"
        aria-label="按状态筛选我的工具"
        className="flex flex-wrap gap-1 rounded-lg border bg-card p-1"
      >
        {TABS.map((item) => {
          const count = item.countKey && usage ? usage[item.countKey] : null;
          const active = item.key === tab;
          return (
            <Button
              key={item.key}
              type="button"
              role="tab"
              aria-selected={active}
              variant={active ? "secondary" : "ghost"}
              size="sm"
              className={cn("gap-1.5", active && "font-semibold")}
              onClick={() => updateParams({ status: item.key === "all" ? null : item.key, page: null })}
            >
              {item.label}
              {count === null ? null : (
                <Badge variant={item.key === "rejected" ? "destructive" : "secondary"}>{count}</Badge>
              )}
            </Button>
          );
        })}
      </div>

      {toolsQuery.isError ? (
        <ErrorState
          message={getErrorMessage(toolsQuery.error)}
          onRetry={() => {
            void toolsQuery.refetch();
          }}
        />
      ) : toolsQuery.isPending ? (
        <PageSkeleton variant="list" count={5} />
      ) : items.length === 0 ? (
        <EmptyState
          title={tab === "all" ? "还没有工具" : "该状态下没有工具"}
          description={
            tab === "all"
              ? "上传第一个工具，让同事在门户里找到它。"
              : "换个状态看看，或新建一个工具。"
          }
          action={
            <Button asChild>
              <Link to="/me/tools/new">
                <Plus aria-hidden="true" className="size-4" />
                创建第一个工具
              </Link>
            </Button>
          }
        />
      ) : (
        <ul className="space-y-3" aria-busy={toolsQuery.isFetching}>
          {items.map((tool) => (
            <MyToolRow
              key={tool.id}
              tool={tool}
              onNavigate={(path) => navigate(path)}
              onSubmit={() => statusMutation.mutate({ id: tool.id, action: "submit" })}
              onWithdraw={() =>
                tool.status === "pending_update"
                  ? statusMutation.mutate({
                      id: tool.id,
                      action: "withdraw_version",
                      version: tool.pending_version ?? undefined,
                    })
                  : statusMutation.mutate({ id: tool.id, action: "withdraw" })
              }
              onDelete={() => setPendingDelete(tool)}
            />
          ))}
        </ul>
      )}

      <Pagination
        page={page}
        pages={toolsQuery.data?.pages ?? 1}
        onPageChange={(next) => updateParams({ page: String(next) })}
      />

      <ConfirmDialog
        open={pendingDelete !== null}
        title={`删除「${pendingDelete?.name ?? ""}」？`}
        description="删除后工具会立即从所有列表消失，存储文件由清理任务延迟回收（软删除，FR-TOOL-10）。"
        confirmLabel="删除工具"
        pending={deleteMutation.isPending}
        onOpenChange={(open) => {
          if (!open) setPendingDelete(null);
        }}
        onConfirm={() => {
          if (pendingDelete) deleteMutation.mutate(pendingDelete.id);
        }}
      />
    </div>
  );
}

function MyToolRow({
  tool,
  onNavigate,
  onSubmit,
  onWithdraw,
  onDelete,
}: {
  tool: MyToolListItem;
  onNavigate: (path: string) => void;
  onSubmit: () => void;
  onWithdraw: () => void;
  onDelete: () => void;
}) {
  const currentOrigin = window.location.origin;
  const shareUrl = `${currentOrigin}/tools/${tool.slug}`;
  const pending = tool.status === "pending" || tool.status === "pending_update";

  return (
    <li
      data-testid="my-tool-row"
      data-tool-id={tool.id}
      className="overflow-hidden rounded-xl border bg-card text-card-foreground"
    >
      <div className="flex flex-wrap items-start gap-3 p-3">
        <div className="size-16 shrink-0 overflow-hidden rounded-lg bg-muted">
          {tool.cover_url ? (
            <img
              src={tool.cover_url}
              alt={`${tool.name} 封面`}
              loading="lazy"
              decoding="async"
              className="cover-media size-full object-cover"
            />
          ) : (
            <div
              aria-hidden="true"
              className={cn(
                "grid size-full place-items-center bg-gradient-to-br text-xs text-foreground/40",
                categoryTintClass(tool.category?.slug),
              )}
            >
              {tool.category?.name ?? "无图"}
            </div>
          )}
        </div>

        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <Link
              to={`/tools/${tool.slug}`}
              className="truncate font-medium underline-offset-4 hover:underline"
            >
              {tool.name}
            </Link>
            <ToolTypeBadge type={tool.tool_type} />
            <Badge data-testid="my-tool-status" variant={STATUS_VARIANTS[tool.status]}>
              {tool.status_label}
            </Badge>
            {tool.pending_version ? (
              <Badge variant="warning" data-status="pending">
                新版 {tool.pending_version} 待审
              </Badge>
            ) : null}
          </div>

          <p className="line-clamp-1 text-sm text-muted-foreground" title={tool.summary}>
            {tool.summary}
          </p>

          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <span>
              当前版本 <span className="font-mono">{tool.current_version ?? "—"}</span>
            </span>
            <span className="inline-flex items-center gap-1">
              {tool.visibility === "public" ? (
                <Globe aria-hidden="true" className="size-3" />
              ) : (
                <Lock aria-hidden="true" className="size-3" />
              )}
              {VISIBILITY_LABELS[tool.visibility]}
            </span>
            <span className="inline-flex items-center gap-1">
              <Download aria-hidden="true" className="size-3" />
              <span className="tabular-nums">{formatCount(tool.download_count)}</span>
              <span className="sr-only">次下载</span>
            </span>
            <span className="inline-flex items-center gap-1">
              <Eye aria-hidden="true" className="size-3" />
              <span className="tabular-nums">{formatCount(tool.view_count)}</span>
              <span className="sr-only">次浏览</span>
            </span>
            <time dateTime={tool.updated_at ?? undefined} title={formatDateTime(tool.updated_at)}>
              更新于 {formatRelativeTime(tool.updated_at)}
            </time>
          </div>
        </div>

        <div className="flex items-center gap-1">
          <CopyButton value={shareUrl} label={`复制 ${tool.name} 的链接`} />
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label={`${tool.name} 的操作菜单`}
              >
                <MoreHorizontal aria-hidden="true" className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-48">
              <DropdownMenuItem onSelect={() => onNavigate(`/me/tools/${tool.id}/edit`)}>
                <Pencil aria-hidden="true" className="size-4" />
                编辑
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => onNavigate(`/me/tools/${tool.id}/versions`)}>
                <Layers aria-hidden="true" className="size-4" />
                版本管理
              </DropdownMenuItem>
              {tool.status === "draft" || tool.status === "rejected" ? (
                <DropdownMenuItem onSelect={onSubmit}>
                  <Send aria-hidden="true" className="size-4" />
                  提交审批
                </DropdownMenuItem>
              ) : null}
              {pending ? (
                <DropdownMenuItem onSelect={onWithdraw}>
                  <Undo2 aria-hidden="true" className="size-4" />
                  {tool.status === "pending_update" ? "撤回待审版本" : "撤回提交"}
                </DropdownMenuItem>
              ) : null}
              <DropdownMenuItem onSelect={() => onNavigate(`/tools/${tool.slug}`)}>
                <Link2 aria-hidden="true" className="size-4" />
                查看详情
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onSelect={onDelete}>
                <Trash2 aria-hidden="true" className="size-4" />
                删除
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      {tool.status === "rejected" ? (
        <Alert
          variant="destructive"
          data-testid="reject-alert"
          className="rounded-none border-x-0 border-b-0"
        >
          <TriangleAlert aria-hidden="true" />
          <AlertTitle>审批未通过</AlertTitle>
          <AlertDescription className="space-y-2">
            <p>{tool.reject_reason ?? "审批人未填写驳回理由。"}</p>
            <Button
              type="button"
              size="sm"
              data-testid="resubmit-button"
              onClick={() => onNavigate(`/me/tools/${tool.id}/edit`)}
            >
              <Pencil aria-hidden="true" className="size-4" />
              修改后重新提交
            </Button>
          </AlertDescription>
        </Alert>
      ) : null}

      {tool.status === "offline" ? (
        <Alert className="rounded-none border-x-0 border-b-0 border-amber-300/60 bg-amber-50/60 dark:bg-amber-950/20">
          <TriangleAlert aria-hidden="true" />
          <AlertTitle>工具已下架</AlertTitle>
          <AlertDescription>
            {tool.offline_reason ?? "管理员未填写下架理由。"}如需重新上架，请联系审批管理员。
          </AlertDescription>
        </Alert>
      ) : null}
    </li>
  );
}
