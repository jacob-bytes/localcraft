import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Download, RotateCcw } from "lucide-react";
import * as React from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import {
  approvalHistoryQueryKey,
  fetchApprovalHistory,
} from "@/api/admin";
import { getErrorMessage } from "@/api/client";
import type { ApprovalAction, ApprovalRecord } from "@/api/types";
import {
  ACTION_LABELS,
  ACTION_OPTIONS,
  ApprovalHistoryTable,
  describeStatusChange,
} from "@/components/admin/ApprovalHistoryTable";
import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { PageSkeleton } from "@/components/common/PageSkeleton";
import { Pagination } from "@/components/common/Pagination";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const PAGE_SIZE = 20;

/** 导出上限：10 页 × 200 条 = 2000 条（见 handleExport 的说明）。 */
const EXPORT_PAGE_SIZE = 200;
const EXPORT_MAX_PAGES = 10;

/** 空字符串代表「不筛选」。 */
interface HistoryFilters {
  toolId: string;
  actorId: string;
  action: ApprovalAction | "";
  dateFrom: string;
  dateTo: string;
}

const EMPTY_FILTERS: HistoryFilters = {
  toolId: "",
  actorId: "",
  action: "",
  dateFrom: "",
  dateTo: "",
};

/** 正整数字符串 → number；非法/空 → undefined（= 不传该参数）。 */
function toOptionalId(value: string): number | undefined {
  const trimmed = value.trim();
  if (!/^\d+$/.test(trimmed)) return undefined;
  const parsed = Number.parseInt(trimmed, 10);
  return parsed > 0 ? parsed : undefined;
}

/** `type="datetime-local"` 的值是本地时间，转成 ISO（UTC）再发请求。 */
function localToIso(value: string): string | undefined {
  if (!value) return undefined;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

/** 会被写进 URL 的筛选键（其余查询参数原样保留）。 */
const FILTER_KEYS = ["tool_id", "actor_id", "action", "date_from", "date_to"] as const;

const ACTION_VALUES: readonly string[] = ACTION_OPTIONS.map((option) => option.value);

/**
 * URL → 筛选状态。非法值（`tool_id=abc`、坏掉的时间…）一律当作「不筛选」，
 * 免得手改 URL 后输入框显示一个发不出去的怪值。
 */
function filtersFromSearchParams(searchParams: URLSearchParams): HistoryFilters {
  const rawToolId = searchParams.get("tool_id") ?? "";
  const rawActorId = searchParams.get("actor_id") ?? "";
  const action = searchParams.get("action") ?? "";
  const dateFrom = searchParams.get("date_from") ?? "";
  const dateTo = searchParams.get("date_to") ?? "";
  return {
    toolId: toOptionalId(rawToolId) === undefined ? "" : rawToolId.trim(),
    actorId: toOptionalId(rawActorId) === undefined ? "" : rawActorId.trim(),
    action: ACTION_VALUES.includes(action) ? (action as ApprovalAction) : "",
    dateFrom: localToIso(dateFrom) === undefined ? "" : dateFrom,
    dateTo: localToIso(dateTo) === undefined ? "" : dateTo,
  };
}

/**
 * `/admin/approvals/history` —— 审批历史（docs/04 §6.11，FR-APPR-12）。
 *
 * 筛选栏用数字 ID + 原生时间输入：M2 没有用户/工具检索接口，
 * `docs/03 §3.8` 只定义 `tool_id` / `actor_id` 数值筛选（契约缺口，见报告）。
 *
 * 筛选条件与 URL 查询串双向同步：`AdminToolTable` 的「审批历史」入口用
 * `/admin/approvals/history?tool_id=N` 带参跳转，落页即按该工具过滤。
 */
export default function ApprovalHistoryPage() {
  const [searchParams, setSearchParams] = useSearchParams();

  /**
   * 筛选条件的事实来源是 URL：`/admin/tools` 会链到 `?tool_id=…`，
   * 刷新 / 分享链接 / 浏览器后退都能还原当前视图（与 `AdminToolsPage` 一致）。
   * `draft` 只是筛选栏输入框的草稿。
   */
  const applied = React.useMemo(() => filtersFromSearchParams(searchParams), [searchParams]);
  const appliedSignature = [
    applied.toolId,
    applied.actorId,
    applied.action,
    applied.dateFrom,
    applied.dateTo,
  ].join("\u0000");

  const [draft, setDraft] = React.useState<HistoryFilters>(applied);
  const [page, setPage] = React.useState(1);
  const [expandedIds, setExpandedIds] = React.useState<number[]>([]);
  const [exporting, setExporting] = React.useState(false);

  // 外部改 URL（链接跳转、浏览器后退、「重置」）时同步草稿并回到第一页。
  const [syncedSignature, setSyncedSignature] = React.useState(appliedSignature);
  if (syncedSignature !== appliedSignature) {
    setSyncedSignature(appliedSignature);
    setDraft(applied);
    setPage(1);
    setExpandedIds([]);
  }

  const params = React.useMemo(
    () => ({
      tool_id: toOptionalId(applied.toolId),
      actor_id: toOptionalId(applied.actorId),
      action: applied.action === "" ? undefined : applied.action,
      date_from: localToIso(applied.dateFrom),
      date_to: localToIso(applied.dateTo),
      page,
      page_size: PAGE_SIZE,
    }),
    [applied, page],
  );

  const historyQuery = useQuery({
    queryKey: approvalHistoryQueryKey(params),
    queryFn: ({ signal }) => fetchApprovalHistory(params, signal),
    placeholderData: keepPreviousData,
  });

  const records = historyQuery.data?.items ?? [];
  const total = historyQuery.data?.total ?? 0;
  const pages = historyQuery.data?.pages ?? 1;

  /** 把筛选条件写回 URL（空值 = 删除该参数）。 */
  function writeFilters(filters: HistoryFilters) {
    setSearchParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        for (const key of FILTER_KEYS) next.delete(key);
        if (filters.toolId.trim() !== "") next.set("tool_id", filters.toolId.trim());
        if (filters.actorId.trim() !== "") next.set("actor_id", filters.actorId.trim());
        if (filters.action !== "") next.set("action", filters.action);
        if (filters.dateFrom !== "") next.set("date_from", filters.dateFrom);
        if (filters.dateTo !== "") next.set("date_to", filters.dateTo);
        return next;
      },
      { replace: false },
    );
  }

  function applyFilters(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    writeFilters(draft);
    setPage(1);
    setExpandedIds([]);
  }

  function resetFilters() {
    setDraft(EMPTY_FILTERS);
    writeFilters(EMPTY_FILTERS);
    setPage(1);
    setExpandedIds([]);
  }

  /**
   * 「导出 CSV」——M2 冻结清单里**没有**导出接口（`/admin/import|export*` 属于 M3，
   * CONTRACT §6.1），所以这里在浏览器端分页拉取当前筛选条件下的记录再拼 CSV。
   *
   * 局限：最多 10 页 × 200 条 = 2000 条；超出部分不会出现在文件里。
   * 后端补上导出接口后应改为直接下载服务端生成的文件。
   */
  async function handleExport() {
    setExporting(true);
    try {
      const rows: ApprovalRecord[] = [];
      let currentPage = 1;
      let totalPages = 1;
      while (currentPage <= EXPORT_MAX_PAGES && currentPage <= totalPages) {
        const response = await fetchApprovalHistory({
          ...params,
          page: currentPage,
          page_size: EXPORT_PAGE_SIZE,
        });
        rows.push(...response.items);
        totalPages = response.pages;
        if (response.items.length === 0) break;
        currentPage += 1;
      }

      const csv = buildCsv(rows);
      // BOM 让 Excel 正确识别 UTF-8 中文。
      const blob = new Blob([`\uFEFF${csv}`], { type: "text/csv;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `审批历史-${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);

      if (total > rows.length) {
        toast.warning(
          `已导出 ${rows.length} 条（上限 ${EXPORT_MAX_PAGES * EXPORT_PAGE_SIZE} 条），请缩小筛选范围后再次导出`,
        );
      } else {
        toast.success(`已导出 ${rows.length} 条记录`);
      }
    } catch (error) {
      toast.error(getErrorMessage(error));
    } finally {
      setExporting(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-xl font-semibold tracking-tight">审批历史</h1>
        <p className="text-xs text-muted-foreground">
          记录工具与版本的全部状态流转（FR-APPR-12）
        </p>
      </div>

      <form
        onSubmit={applyFilters}
        className="flex flex-wrap items-end gap-3 rounded-xl border bg-card p-3"
        aria-label="审批历史筛选"
      >
        <div className="grid gap-1.5">
          <Label htmlFor="history-tool-id">工具 ID</Label>
          <Input
            id="history-tool-id"
            type="number"
            inputMode="numeric"
            min={1}
            className="w-28"
            value={draft.toolId}
            onChange={(event) => setDraft({ ...draft, toolId: event.target.value })}
            placeholder="如 12"
          />
        </div>

        <div className="grid gap-1.5">
          <Label htmlFor="history-actor-id">操作人 ID</Label>
          <Input
            id="history-actor-id"
            type="number"
            inputMode="numeric"
            min={1}
            className="w-28"
            value={draft.actorId}
            onChange={(event) => setDraft({ ...draft, actorId: event.target.value })}
            placeholder="如 3"
          />
        </div>

        <div className="grid gap-1.5">
          <Label htmlFor="history-action">动作类型</Label>
          <Select
            value={draft.action === "" ? "all" : draft.action}
            onValueChange={(value) =>
              setDraft({ ...draft, action: value === "all" ? "" : (value as ApprovalAction) })
            }
          >
            <SelectTrigger id="history-action" className="w-40">
              <SelectValue placeholder="全部动作" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部动作</SelectItem>
              {ACTION_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="grid gap-1.5">
          <Label htmlFor="history-date-from">开始时间</Label>
          <Input
            id="history-date-from"
            type="datetime-local"
            className="w-52"
            value={draft.dateFrom}
            onChange={(event) => setDraft({ ...draft, dateFrom: event.target.value })}
          />
        </div>

        <div className="grid gap-1.5">
          <Label htmlFor="history-date-to">结束时间</Label>
          <Input
            id="history-date-to"
            type="datetime-local"
            className="w-52"
            value={draft.dateTo}
            onChange={(event) => setDraft({ ...draft, dateTo: event.target.value })}
          />
        </div>

        <div className="flex items-center gap-2">
          <Button type="submit" size="sm">
            筛选
          </Button>
          <Button type="button" size="sm" variant="outline" onClick={resetFilters}>
            <RotateCcw aria-hidden="true" className="size-4" />
            重置
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={exporting}
            onClick={() => void handleExport()}
          >
            <Download aria-hidden="true" className="size-4" />
            {exporting ? "导出中…" : "导出 CSV"}
          </Button>
        </div>
      </form>

      {historyQuery.isPending ? (
        <PageSkeleton variant="list" />
      ) : historyQuery.isError ? (
        <ErrorState
          message={getErrorMessage(historyQuery.error)}
          onRetry={() => void historyQuery.refetch()}
        />
      ) : records.length === 0 ? (
        <EmptyState
          title="没有匹配的审批记录"
          description="调整筛选条件，或点「重置」查看全部历史。"
        />
      ) : (
        <>
          <ApprovalHistoryTable
            records={records}
            expandedIds={expandedIds}
            onToggleExpand={(recordId) =>
              setExpandedIds((current) =>
                current.includes(recordId)
                  ? current.filter((id) => id !== recordId)
                  : [...current, recordId],
              )
            }
          />
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground">共 {total} 条记录</p>
            <Pagination page={page} pages={pages} onPageChange={setPage} />
          </div>
        </>
      )}
    </div>
  );
}

/** RFC4180 风格转义 + Excel 公式前缀防护。 */
function csvCell(value: string | number | null | undefined): string {
  const raw = value === null || value === undefined ? "" : String(value);
  // 以 = + - @ 开头的单元格可能被 Excel 当公式执行，前面补一个单引号。
  const safe = /^[=+\-@]/.test(raw) ? `'${raw}` : raw;
  if (/[",\n\r]/.test(safe)) return `"${safe.replace(/"/g, '""')}"`;
  return safe;
}

function buildCsv(rows: readonly ApprovalRecord[]): string {
  const header = ["时间", "工具", "版本", "动作", "操作人", "状态变更", "理由", "备注"];
  const lines = [header.map(csvCell).join(",")];
  for (const record of rows) {
    lines.push(
      [
        record.created_at ?? "",
        record.tool_name ?? record.tool_slug ?? String(record.tool_id),
        record.version ?? "",
        record.is_automatic ? "系统自动" : ACTION_LABELS[record.action],
        record.actor_label,
        describeStatusChange(record),
        record.reason ?? "",
        record.note ?? "",
      ]
        .map(csvCell)
        .join(","),
    );
  }
  return lines.join("\r\n");
}
