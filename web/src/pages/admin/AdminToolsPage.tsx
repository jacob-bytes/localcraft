import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, RotateCcw } from "lucide-react";
import * as React from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import {
  adminCategoriesQueryKey,
  adminToolsQueryKey,
  adminUsersQueryKey,
  fetchAdminCategories,
  fetchAdminTools,
  fetchAdminUsers,
  offlineTool,
  overviewQueryKey,
  relistTool,
  transferAdminTool,
} from "@/api/admin";
import { getErrorMessage } from "@/api/client";
import type { AdminToolItem, AdminToolListParams, ToolStatus, ToolType, Visibility } from "@/api/types";
import {
  AdminToolTable,
  TOOL_STATUS_OPTIONS,
  VISIBILITY_OPTIONS,
} from "@/components/admin/AdminToolTable";
import { OfflineReasonDialog } from "@/components/admin/OfflineReasonDialog";
import { TransferOwnerDialog } from "@/components/admin/TransferOwnerDialog";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { PageSkeleton } from "@/components/common/PageSkeleton";
import { Pagination } from "@/components/common/Pagination";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useDebounce } from "@/hooks/useDebounce";
import { usePermission } from "@/hooks/usePermission";
import { TOOL_TYPE_META, TOOL_TYPES } from "@/lib/toolMeta";

/**
 * `/admin/tools` —— 全站工具管理（docs/04 §6.12，FR-ADMIN-12）。
 *
 * 全部筛选与分页状态放在 URL（`useSearchParams`），刷新 / 分享链接可还原。
 * 行内动作只调用 `src/api/*` 的现有函数；**不自己 fetch**。
 *
 * 关于批量操作（验收 #22）：92 个冻结接口里**没有批量下架 / 批量重新上架**
 * （只有 `POST /admin/approvals/batch-approve`），所以这里逐个 `await` 单条端点，
 * 收集成功/失败后汇总提示，执行期间禁用按钮并显示进度。
 *
 * **不提供「移入回收站」**：没有管理侧软删除端点（`DELETE /me/tools/{id}` 只对
 * owner 生效，CONTRACT §16.5 接口面冻结于 92），界面不制造做不到的操作。
 */

const PAGE_SIZE = 20;
const ALL_VALUE = "__all__";

/** 批量下架必须带理由（FR-APPR-10，后端 `min_length=5`）。 */
const BATCH_OFFLINE_REASON = "管理员批量下架（全站工具管理）";

const TOOL_STATUS_VALUES: readonly ToolStatus[] = [
  "draft",
  "pending",
  "approved",
  "rejected",
  "pending_update",
  "offline",
];

const VISIBILITY_VALUES: readonly Visibility[] = ["public", "restricted", "private"];

function isToolStatus(value: string): value is ToolStatus {
  return (TOOL_STATUS_VALUES as readonly string[]).includes(value);
}

function isToolType(value: string): value is ToolType {
  return (TOOL_TYPES as readonly string[]).includes(value);
}

function isVisibility(value: string): value is Visibility {
  return (VISIBILITY_VALUES as readonly string[]).includes(value);
}

/** `<input type="date">` 的值是本地日期；转成当天 00:00 / 23:59 的 ISO（UTC）。 */
function dateToIso(value: string, endOfDay: boolean): string | undefined {
  if (!value) return undefined;
  const date = new Date(`${value}T${endOfDay ? "23:59:59.999" : "00:00:00"}`);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

type FilterPatch = Record<string, string | string[] | null>;

export default function AdminToolsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const { hasRole } = usePermission();

  /* ---------------------------- URL → 筛选状态 ---------------------------- */

  const q = searchParams.get("q") ?? "";
  const statuses = searchParams.getAll("status").filter(isToolStatus);
  const types = searchParams.getAll("type").filter(isToolType);
  const categories = searchParams.getAll("category");
  const visibilities = searchParams.getAll("visibility").filter(isVisibility);
  const owner = searchParams.get("owner") ?? "";
  const dateFrom = searchParams.get("date_from") ?? "";
  const dateTo = searchParams.get("date_to") ?? "";
  const parsedPage = Number.parseInt(searchParams.get("page") ?? "1", 10);
  const page = Number.isFinite(parsedPage) && parsedPage >= 1 ? parsedPage : 1;

  const updateParams = React.useCallback(
    (patch: FilterPatch, options?: { resetPage?: boolean; replace?: boolean }) => {
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

  /* ------------------- 关键词 / 作者文本：300ms 防抖写 URL ------------------- */

  const [searchInput, setSearchInput] = React.useState(q);
  const debouncedSearch = useDebounce(searchInput, 300);

  // 外部改 URL（分享链接、浏览器后退、「重置」）时同步输入框。
  const [syncedQuery, setSyncedQuery] = React.useState(q);
  if (syncedQuery !== q) {
    setSyncedQuery(q);
    setSearchInput(q);
  }

  React.useEffect(() => {
    const trimmed = debouncedSearch.trim();
    if (searchInput.trim() !== trimmed) return;
    if (trimmed === q) return;
    updateParams({ q: trimmed || null }, { resetPage: true, replace: true });
  }, [debouncedSearch, searchInput, q, updateParams]);

  const [ownerInput, setOwnerInput] = React.useState(owner);
  const debouncedOwner = useDebounce(ownerInput, 300);

  const [syncedOwner, setSyncedOwner] = React.useState(owner);
  if (syncedOwner !== owner) {
    setSyncedOwner(owner);
    setOwnerInput(owner);
  }

  React.useEffect(() => {
    const trimmed = debouncedOwner.trim();
    if (ownerInput.trim() !== trimmed) return;
    if (trimmed === owner) return;
    updateParams({ owner: trimmed || null }, { resetPage: true, replace: true });
  }, [debouncedOwner, ownerInput, owner, updateParams]);

  /* -------------------------------- 查询参数 -------------------------------- */

  // 只用原始值做依赖：数组每次渲染都是新引用，按引用做依赖会让 memo 失效。
  const statusKey = statuses.join(",");
  const typeKey = types.join(",");
  const categoryKey = categories.join(",");
  const visibilityKey = visibilities.join(",");

  const params = React.useMemo<AdminToolListParams>(
    () => ({
      q: q || undefined,
      status: statusKey ? (statusKey.split(",") as ToolStatus[]) : undefined,
      type: typeKey ? (typeKey.split(",") as ToolType[]) : undefined,
      category: categoryKey ? categoryKey.split(",") : undefined,
      visibility: visibilityKey ? (visibilityKey.split(",") as Visibility[]) : undefined,
      owner: owner || undefined,
      date_from: dateToIso(dateFrom, false),
      date_to: dateToIso(dateTo, true),
      page,
      page_size: PAGE_SIZE,
    }),
    [q, statusKey, typeKey, categoryKey, visibilityKey, owner, dateFrom, dateTo, page],
  );

  const toolsQuery = useQuery({
    queryKey: adminToolsQueryKey(params),
    queryFn: ({ signal }) => fetchAdminTools(params, signal),
    placeholderData: keepPreviousData,
  });

  const categoriesQuery = useQuery({
    queryKey: adminCategoriesQueryKey,
    queryFn: ({ signal }) => fetchAdminCategories(signal),
    staleTime: 5 * 60_000,
  });

  /**
   * 作者下拉的数据源。`GET /admin/users` 属 `users_guard`（仅 superadmin），
   * approver 打开本页时它会 403 —— 此时降级为 username 文本输入，
   * 而不是让这一行渲染失败。
   */
  const usersQuery = useQuery({
    queryKey: adminUsersQueryKey({ page_size: 200 }),
    queryFn: ({ signal }) => fetchAdminUsers({ page_size: 200 }, signal),
    staleTime: 5 * 60_000,
    retry: false,
  });

  const items = toolsQuery.data?.items ?? [];
  const total = toolsQuery.data?.total ?? 0;
  const pages = toolsQuery.data?.pages ?? 1;

  // `?page=99` 这类越界页码自动回到最后一页（Pagination 仍会显示控件）。
  React.useEffect(() => {
    const data = toolsQuery.data;
    if (!data) return;
    if (page > data.pages && data.pages >= 1) {
      updateParams({ page: String(data.pages) }, { replace: true });
    }
  }, [toolsQuery.data, page, updateParams]);

  /* --------------------------------- 勾选 --------------------------------- */

  const [checkedIds, setCheckedIds] = React.useState<number[]>([]);
  const filterSignature = [
    q,
    statusKey,
    typeKey,
    categoryKey,
    visibilityKey,
    owner,
    dateFrom,
    dateTo,
    page,
  ].join("|");

  // 换筛选条件或翻页后清空勾选，避免批量动作落到已看不见的行上。
  // 用「渲染期同步上一个值」的写法而不是 effect，省掉一次级联渲染。
  const [selectionSignature, setSelectionSignature] = React.useState(filterSignature);
  if (selectionSignature !== filterSignature) {
    setSelectionSignature(filterSignature);
    setCheckedIds([]);
  }

  /* -------------------------------- 失效刷新 -------------------------------- */

  const invalidateAfterMutation = React.useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["admin", "tools"] });
    void queryClient.invalidateQueries({ queryKey: overviewQueryKey });
    void queryClient.invalidateQueries({ queryKey: ["admin", "approval-history"] });
  }, [queryClient]);

  /* -------------------------------- 单个动作 -------------------------------- */

  const [offlineTarget, setOfflineTarget] = React.useState<AdminToolItem | null>(null);
  const [offlineError, setOfflineError] = React.useState<string | null>(null);

  const offlineMutation = useMutation({
    mutationFn: ({ toolId, reason }: { toolId: number; reason: string }) =>
      offlineTool(toolId, { reason }),
    onSuccess: (_data, variables) => {
      toast.success(`已下架「${offlineTarget?.name ?? `#${variables.toolId}`}」`);
      setOfflineTarget(null);
      setOfflineError(null);
      invalidateAfterMutation();
    },
    onError: (error) => setOfflineError(getErrorMessage(error)),
  });

  const [relistTarget, setRelistTarget] = React.useState<AdminToolItem | null>(null);
  const [relistReason, setRelistReason] = React.useState("");
  const [relistError, setRelistError] = React.useState<string | null>(null);

  const relistMutation = useMutation({
    mutationFn: ({ toolId, reason }: { toolId: number; reason: string | null }) =>
      relistTool(toolId, { reason }),
    onSuccess: (_data, variables) => {
      toast.success(`已重新上架「${relistTarget?.name ?? `#${variables.toolId}`}」`);
      setRelistTarget(null);
      setRelistReason("");
      setRelistError(null);
      invalidateAfterMutation();
    },
    onError: (error) => setRelistError(getErrorMessage(error)),
  });

  const [transferTarget, setTransferTarget] = React.useState<AdminToolItem | null>(null);
  const [transferError, setTransferError] = React.useState<string | null>(null);

  const transferMutation = useMutation({
    mutationFn: ({
      toolId,
      newOwnerId,
      reason,
    }: {
      toolId: number;
      newOwnerId: number;
      reason: string | null;
    }) => transferAdminTool(toolId, { new_owner_id: newOwnerId, reason }),
    onSuccess: () => {
      toast.success(`已转移「${transferTarget?.name ?? ""}」的负责人`);
      setTransferTarget(null);
      setTransferError(null);
      invalidateAfterMutation();
    },
    onError: (error) => setTransferError(getErrorMessage(error)),
  });

  /* -------------------------------- 批量动作 -------------------------------- */

  type BatchKind = "offline" | "relist";
  interface BatchFailure {
    name: string;
    message: string;
  }

  const [batch, setBatch] = React.useState<{ kind: BatchKind; done: number; total: number } | null>(
    null,
  );
  const [batchFailures, setBatchFailures] = React.useState<BatchFailure[] | null>(null);
  const [batchSummary, setBatchSummary] = React.useState<string | null>(null);

  // 依赖用 `toolsQuery.data?.items` 而不是派生的 `items`：后者每次渲染都是新数组。
  const dataItems = toolsQuery.data?.items;
  const batchTargets = React.useMemo(
    () => (dataItems ?? []).filter((item) => checkedIds.includes(item.id)),
    [dataItems, checkedIds],
  );

  async function runBatch(kind: BatchKind) {
    const targets = batchTargets;
    if (targets.length === 0) return;
    setBatch({ kind, done: 0, total: targets.length });

    const failures: BatchFailure[] = [];
    let succeeded = 0;

    // 没有批量端点 → 顺序调用单条端点，逐个收集结果（验收 #22）。
    for (const [index, tool] of targets.entries()) {
      try {
        if (kind === "offline") {
          await offlineTool(tool.id, { reason: BATCH_OFFLINE_REASON });
        } else {
          await relistTool(tool.id, { reason: null });
        }
        succeeded += 1;
      } catch (error) {
        failures.push({ name: tool.name, message: getErrorMessage(error) });
      }
      setBatch({ kind, done: index + 1, total: targets.length });
    }

    setBatch(null);
    setCheckedIds([]);

    const actionLabel = kind === "offline" ? "批量下架" : "批量重新上架";
    const summary = `成功 ${succeeded} 条，失败 ${failures.length} 条`;
    if (failures.length > 0) {
      setBatchSummary(`${actionLabel}完成：${summary}`);
      setBatchFailures(failures);
      toast.error(`${actionLabel}完成：${summary}`, {
        description: failures.map((failure) => `${failure.name}：${failure.message}`).join("\n"),
      });
    } else {
      toast.success(`${actionLabel}完成：${summary}`);
    }
    invalidateAfterMutation();
  }

  const batchRunning = batch !== null;

  /* -------------------------------- 筛选栏渲染 ------------------------------- */

  const categoryOptions = React.useMemo(
    () =>
      (categoriesQuery.data ?? []).map((category) => ({
        value: category.slug,
        label: `${category.name}（${category.tool_count}）`,
      })),
    [categoriesQuery.data],
  );

  const typeOptions = React.useMemo(
    () => TOOL_TYPES.map((type) => ({ value: type, label: TOOL_TYPE_META[type].label })),
    [],
  );

  const ownerOptions = React.useMemo(() => {
    const options = (usersQuery.data?.items ?? []).map((user) => ({
      value: user.username,
      label: `${user.display_name}（${user.username}）`,
    }));
    // URL 里的作者可能不在前 200 个用户里 —— 保留它，否则下拉会显示为空。
    if (owner && !options.some((option) => option.value === owner)) {
      options.unshift({ value: owner, label: owner });
    }
    return options;
  }, [usersQuery.data, owner]);

  function resetFilters() {
    setSearchInput("");
    setOwnerInput("");
    updateParams({
      q: null,
      status: null,
      type: null,
      category: null,
      visibility: null,
      owner: null,
      date_from: null,
      date_to: null,
      page: null,
    });
  }

  const canTransfer = hasRole(["superadmin"]);

  return (
    <div data-testid="admin-tools-page" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-xl font-semibold tracking-tight">全站工具管理</h1>
        <p className="text-xs text-muted-foreground">
          含其他作者与全部状态（FR-ADMIN-12）· 下架理由必填、重新上架理由选填
        </p>
      </div>

      <section
        data-testid="admin-tools-filters"
        aria-label="工具筛选"
        className="flex flex-col gap-3 rounded-xl border bg-card p-3"
      >
        <div className="flex flex-wrap items-end gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="admin-tools-q">关键词</Label>
            <Input
              id="admin-tools-q"
              className="w-56"
              value={searchInput}
              placeholder="名称 / 简介 / slug"
              onChange={(event) => setSearchInput(event.target.value)}
            />
          </div>

          <MultiSelectFilter
            label="状态"
            options={TOOL_STATUS_OPTIONS}
            selected={statuses}
            onChange={(next) => updateParams({ status: next }, { resetPage: true })}
          />
          <MultiSelectFilter
            label="类型"
            options={typeOptions}
            selected={types}
            onChange={(next) => updateParams({ type: next }, { resetPage: true })}
          />
          <MultiSelectFilter
            label="分类"
            options={categoryOptions}
            selected={categories}
            disabled={categoriesQuery.isPending}
            onChange={(next) => updateParams({ category: next }, { resetPage: true })}
          />

          {usersQuery.isError ? (
            <div className="grid gap-1.5">
              <Label htmlFor="admin-tools-owner-text">作者（username）</Label>
              <Input
                id="admin-tools-owner-text"
                className="w-44"
                value={ownerInput}
                placeholder="如 zhangsan"
                onChange={(event) => setOwnerInput(event.target.value)}
              />
            </div>
          ) : (
            <div className="grid gap-1.5">
              <Label htmlFor="admin-tools-owner">作者</Label>
              <Select
                value={owner || ALL_VALUE}
                onValueChange={(value) =>
                  updateParams({ owner: value === ALL_VALUE ? null : value }, { resetPage: true })
                }
              >
                <SelectTrigger id="admin-tools-owner" className="w-52">
                  <SelectValue placeholder="全部作者" />
                </SelectTrigger>
                <SelectContent className="max-h-72">
                  <SelectItem value={ALL_VALUE}>全部作者</SelectItem>
                  {ownerOptions.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          <MultiSelectFilter
            label="可见性"
            options={VISIBILITY_OPTIONS}
            selected={visibilities}
            onChange={(next) => updateParams({ visibility: next }, { resetPage: true })}
          />

          <div className="grid gap-1.5">
            <Label htmlFor="admin-tools-date-from">更新时间从</Label>
            <Input
              id="admin-tools-date-from"
              type="date"
              className="w-40"
              value={dateFrom}
              onChange={(event) =>
                updateParams({ date_from: event.target.value || null }, { resetPage: true })
              }
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="admin-tools-date-to">到</Label>
            <Input
              id="admin-tools-date-to"
              type="date"
              className="w-40"
              value={dateTo}
              onChange={(event) =>
                updateParams({ date_to: event.target.value || null }, { resetPage: true })
              }
            />
          </div>

          <Button type="button" variant="outline" size="sm" onClick={resetFilters}>
            <RotateCcw aria-hidden="true" className="size-4" />
            重置
          </Button>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <p className="text-xs text-muted-foreground">
            共 {total} 个工具 · 每页 {PAGE_SIZE} 条
            {toolsQuery.isFetching && !toolsQuery.isPending ? " · 加载中…" : ""}
          </p>

          {/* 勾选后才出现批量操作；没有批量端点，所以是顺序单条调用。 */}
          {checkedIds.length > 0 ? (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-muted-foreground">已选 {checkedIds.length} 个</span>
              <Button
                type="button"
                size="sm"
                variant="destructive"
                data-testid="admin-tools-batch-offline"
                disabled={batchRunning}
                onClick={() => void runBatch("offline")}
              >
                {batch?.kind === "offline"
                  ? `批量下架中… ${batch.done}/${batch.total}`
                  : `批量下架 (${checkedIds.length})`}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                data-testid="admin-tools-batch-relist"
                disabled={batchRunning}
                onClick={() => void runBatch("relist")}
              >
                {batch?.kind === "relist"
                  ? `批量重新上架中… ${batch.done}/${batch.total}`
                  : `批量重新上架 (${checkedIds.length})`}
              </Button>
              {/* 语义上容易误操作，明确不做批量转移负责人（docs/04 §6.12）。 */}
            </div>
          ) : null}
        </div>
      </section>

      {toolsQuery.isPending ? (
        <PageSkeleton variant="list" count={10} />
      ) : toolsQuery.isError ? (
        <ErrorState
          message={getErrorMessage(toolsQuery.error)}
          onRetry={() => void toolsQuery.refetch()}
        />
      ) : items.length === 0 ? (
        <EmptyState
          title="没有匹配的工具"
          description="调整筛选条件，或点「重置」查看全部工具。"
        />
      ) : (
        <>
          <AdminToolTable
            items={items}
            checkedIds={checkedIds}
            busy={batchRunning}
            canTransfer={canTransfer}
            onToggleChecked={(toolId, checked) =>
              setCheckedIds((current) =>
                checked
                  ? current.includes(toolId)
                    ? current
                    : [...current, toolId]
                  : current.filter((id) => id !== toolId),
              )
            }
            onToggleAll={(checked) => setCheckedIds(checked ? items.map((item) => item.id) : [])}
            onOffline={(tool) => {
              setOfflineError(null);
              setOfflineTarget(tool);
            }}
            onRelist={(tool) => {
              setRelistError(null);
              setRelistReason("");
              setRelistTarget(tool);
            }}
            onTransfer={(tool) => {
              setTransferError(null);
              setTransferTarget(tool);
            }}
          />

          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground">
              第 {page} / {pages} 页 · 共 {total} 个工具
            </p>
            <Pagination
              page={page}
              pages={pages}
              onPageChange={(nextPage) =>
                updateParams({ page: nextPage > 1 ? String(nextPage) : null })
              }
            />
          </div>
        </>
      )}

      {/* 下架：理由必填（FR-APPR-10）。 */}
      <OfflineReasonDialog
        open={offlineTarget !== null}
        onOpenChange={(open) => {
          if (!open) {
            setOfflineTarget(null);
            setOfflineError(null);
          }
        }}
        tool={offlineTarget}
        pending={offlineMutation.isPending}
        error={offlineError}
        onConfirm={(reason) => {
          if (offlineTarget) offlineMutation.mutate({ toolId: offlineTarget.id, reason });
        }}
      />

      {/* 重新上架：一次确认，理由选填（FR-APPR-11）。 */}
      <ConfirmDialog
        open={relistTarget !== null}
        onOpenChange={(open) => {
          if (!open) {
            setRelistTarget(null);
            setRelistReason("");
            setRelistError(null);
          }
        }}
        destructive={false}
        title={`重新上架「${relistTarget?.name ?? ""}」？`}
        description={
          <div className="space-y-2">
            <p>重新上架后门户立即可见，当前版本继续对外服务。理由选填（FR-APPR-11）。</p>
            <div className="grid gap-1.5">
              <Label htmlFor="admin-tool-relist-reason">上架理由（选填）</Label>
              <Textarea
                id="admin-tool-relist-reason"
                rows={2}
                maxLength={2000}
                value={relistReason}
                disabled={relistMutation.isPending}
                onChange={(event) => setRelistReason(event.target.value)}
              />
            </div>
            {relistError ? (
              <p role="alert" className="text-xs text-destructive">
                {relistError}
              </p>
            ) : null}
          </div>
        }
        confirmLabel="确认上架"
        pending={relistMutation.isPending}
        onConfirm={() => {
          if (relistTarget) {
            relistMutation.mutate({
              toolId: relistTarget.id,
              reason: relistReason.trim() || null,
            });
          }
        }}
      />

      {/* 转移负责人：superadmin 专属（docs/01 §3.2，后端 admin_all_guard）。 */}
      <TransferOwnerDialog
        open={transferTarget !== null}
        onOpenChange={(open) => {
          if (!open) {
            setTransferTarget(null);
            setTransferError(null);
          }
        }}
        tool={transferTarget}
        pending={transferMutation.isPending}
        error={transferError}
        onConfirm={(newOwnerId, reason) => {
          if (transferTarget) {
            transferMutation.mutate({ toolId: transferTarget.id, newOwnerId, reason });
          }
        }}
      />

      {/* 批量结果：失败明细必须可见（验收 #22）。 */}
      <AlertDialog
        open={batchFailures !== null}
        onOpenChange={(open) => {
          if (!open) {
            setBatchFailures(null);
            setBatchSummary(null);
          }
        }}
      >
        <AlertDialogContent data-testid="admin-tools-batch-result">
          <AlertDialogHeader>
            <AlertDialogTitle>批量操作未全部成功</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2 text-sm text-muted-foreground">
                <p>{batchSummary}</p>
                <ul className="max-h-60 space-y-1 overflow-y-auto">
                  {batchFailures?.map((failure) => (
                    <li key={failure.name}>
                      <span className="font-medium text-foreground">{failure.name}</span>：
                      {failure.message}
                    </li>
                  ))}
                </ul>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>关闭</AlertDialogCancel>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

/** 多选筛选（状态 / 类型 / 分类 / 可见性）：DropdownMenu + CheckboxItem，不关闭面板。 */
function MultiSelectFilter({
  label,
  options,
  selected,
  onChange,
  disabled = false,
}: {
  label: string;
  options: ReadonlyArray<{ value: string; label: string }>;
  selected: readonly string[];
  onChange: (next: string[]) => void;
  disabled?: boolean;
}) {
  return (
    <div className="grid gap-1.5">
      <span className="text-sm leading-none font-medium">{label}</span>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="outline"
            className="w-40 justify-between font-normal"
            aria-label={`${label}筛选`}
            disabled={disabled}
          >
            <span className="truncate">
              {selected.length > 0 ? `${label}（${selected.length}）` : `全部${label}`}
            </span>
            <ChevronDown aria-hidden="true" className="size-4 opacity-60" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="max-h-72 w-56 overflow-y-auto">
          <DropdownMenuLabel>{label}</DropdownMenuLabel>
          <DropdownMenuSeparator />
          {options.length === 0 ? (
            <DropdownMenuLabel className="font-normal text-muted-foreground">
              暂无可选项
            </DropdownMenuLabel>
          ) : (
            options.map((option) => (
              <DropdownMenuCheckboxItem
                key={option.value}
                checked={selected.includes(option.value)}
                // 保持面板打开，方便连续勾选（多选筛选的常规交互）。
                onSelect={(event) => event.preventDefault()}
                onCheckedChange={() =>
                  onChange(
                    selected.includes(option.value)
                      ? selected.filter((value) => value !== option.value)
                      : [...selected, option.value],
                  )
                }
              >
                {option.label}
              </DropdownMenuCheckboxItem>
            ))
          )}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
