import { AlertTriangle, ClipboardList, Inbox } from "lucide-react";
import * as React from "react";

import type { ApprovalQueueItem, SubmissionType } from "@/api/types";
import { EmptyState } from "@/components/common/EmptyState";
import { ToolTypeBadge } from "@/components/tools/ToolTypeBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";

/**
 * 审批队列左栏（docs/04 §6.10）。
 *
 * 排序完全交给服务端：`GET /admin/approvals` 默认 `created_at` 正序，
 * 即「先提交先处理」（docs/03 §3.8 / FR-APPR-05）。前端只做
 * `submission_type` 的**客户端**分段筛选，因为筛选不改排序。
 */

export type ApprovalTypeFilter = "all" | SubmissionType;

export const SUBMISSION_TYPE_LABELS: Record<SubmissionType, string> = {
  new_tool: "新工具",
  new_version: "新版本",
};

const TYPE_FILTERS: ReadonlyArray<{ value: ApprovalTypeFilter; label: string; testId: string }> = [
  { value: "all", label: "全部", testId: "approval-filter-all" },
  { value: "new_tool", label: "新工具", testId: "approval-filter-new-tool" },
  { value: "new_version", label: "新版本", testId: "approval-filter-new-version" },
];

export function submissionTypeLabel(type: SubmissionType): string {
  return SUBMISSION_TYPE_LABELS[type];
}

/** 提交类型徽标（新工具 / 新版本）。审批详情与队列共用，避免文案漂移。 */
export function SubmissionTypeBadge({ type }: { type: SubmissionType }) {
  return (
    <Badge variant={type === "new_tool" ? "default" : "outline"}>
      {submissionTypeLabel(type)}
    </Badge>
  );
}

/** 待审版本的变更说明首行；新工具没有 changelog，退回 summary（docs/04 §6.10）。 */
export function queueItemSummaryLine(item: ApprovalQueueItem): string {
  const changelog = item.pending_version?.changelog_md ?? "";
  const firstLine = changelog
    .split("\n")
    .map((line) => line.replace(/^#+\s*/, "").trim())
    .find((line) => line.length > 0);
  if (firstLine) return firstLine;
  return item.summary || "（无简介）";
}

/** FR-APPR-05：等待时长始终带数字，>24h 用 warning，>72h 用 destructive。 */
export function WaitingBadge({ hours, className }: { hours: number; className?: string }) {
  const safe = Number.isFinite(hours) ? Math.max(0, hours) : 0;
  // 整数小时不显示小数点，「等待 26 小时」比「等待 26.0 小时」自然。
  const display = safe >= 10 ? String(Math.round(safe)) : safe.toFixed(1);
  const variant = safe > 72 ? "destructive" : safe > 24 ? "warning" : "secondary";

  return (
    <Badge variant={variant} className={cn("gap-1 tabular-nums", className)} data-testid="waiting-badge">
      {safe > 24 ? <AlertTriangle aria-hidden="true" className="size-3" /> : null}
      等待 {display} 小时
    </Badge>
  );
}

export interface ApprovalQueueProps {
  items: readonly ApprovalQueueItem[];
  /** 全量（未筛选）队列长度，用于「共 N 条待处理」。 */
  total: number;
  typeFilter: ApprovalTypeFilter;
  onTypeFilterChange: (value: ApprovalTypeFilter) => void;
  selectedToolId: number | null;
  onSelect: (toolId: number) => void;
  checkedIds: readonly number[];
  onToggleChecked: (toolId: number, checked: boolean) => void;
  onBatchApprove: () => void;
  batchPending: boolean;
}

export function ApprovalQueue({
  items,
  total,
  typeFilter,
  onTypeFilterChange,
  selectedToolId,
  onSelect,
  checkedIds,
  onToggleChecked,
  onBatchApprove,
  batchPending,
}: ApprovalQueueProps) {
  const checked = React.useMemo(() => new Set(checkedIds), [checkedIds]);
  const visible = items.length;
  const availableBadgeCount = typeFilter === "all" ? 0 : total - visible;

  return (
    <section data-testid="approval-queue" aria-label="审批队列" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-base font-semibold">审批队列</h2>
        <span className="text-sm text-muted-foreground">共 {total} 条待处理</span>
        {availableBadgeCount > 0 ? (
          <span className="text-xs text-muted-foreground">（当前筛选 {visible} 条）</span>
        ) : null}
      </div>

      <ToggleGroup
        type="single"
        value={typeFilter}
        onValueChange={(value) => {
          if (value) onTypeFilterChange(value as ApprovalTypeFilter);
        }}
        variant="outline"
        size="sm"
        aria-label="按提交类型筛选"
      >
        {TYPE_FILTERS.map((filter) => (
          <ToggleGroupItem
            key={filter.value}
            value={filter.value}
            data-testid={filter.testId}
            aria-label={filter.label}
          >
            {filter.label}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>

      <div className="flex items-center justify-between gap-2">
        <Button
          type="button"
          size="sm"
          data-testid="batch-approve-button"
          disabled={checkedIds.length === 0 || batchPending}
          onClick={onBatchApprove}
        >
          {batchPending ? "批量批准中…" : `批量批准 (${checkedIds.length})`}
        </Button>
        {/* FR-APPR-09：不提供批量驳回，理由必须逐条填写，所以这里只有一个按钮。 */}
        <span className="text-xs text-muted-foreground">驳回需逐条填写理由</span>
      </div>

      {visible === 0 ? (
        <EmptyState
          icon={typeFilter === "all" ? Inbox : ClipboardList}
          title="没有待处理的审批"
          description={
            typeFilter === "all"
              ? "队列已清空。新提交会按「先提交先处理」的顺序出现在这里。"
              : "当前筛选类型下没有条目，试试切换到「全部」。"
          }
        />
      ) : (
        <ul className="flex flex-col gap-2">
          {items.map((item) => (
            <li key={item.tool_id}>
              <QueueItem
                item={item}
                active={item.tool_id === selectedToolId}
                checked={checked.has(item.tool_id)}
                onSelect={onSelect}
                onToggleChecked={onToggleChecked}
              />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function QueueItem({
  item,
  active,
  checked,
  onSelect,
  onToggleChecked,
}: {
  item: ApprovalQueueItem;
  active: boolean;
  checked: boolean;
  onSelect: (toolId: number) => void;
  onToggleChecked: (toolId: number, checked: boolean) => void;
}) {
  const checkboxId = `approval-check-${item.tool_id}`;

  return (
    // 整行是一个真正的 <button>，checkbox 用 label + stopPropagation 独立出来，
    // 避免把可点击元素嵌套进另一个可点击元素（docs/04 §8.1）。
    <div
      data-testid="approval-item"
      data-tool-id={item.tool_id}
      className={cn(
        "flex items-start gap-3 rounded-lg border bg-card p-3 transition-colors",
        active ? "border-primary ring-1 ring-primary/40" : "hover:bg-accent/40",
      )}
    >
      <label
        className="mt-0.5 flex cursor-pointer items-center rounded-sm p-0.5 focus-within:ring-2 focus-within:ring-ring"
        htmlFor={checkboxId}
        onClick={(event) => event.stopPropagation()}
      >
        <Checkbox
          id={checkboxId}
          checked={checked}
          aria-label={`选择 ${item.tool_name} 加入批量批准`}
          onCheckedChange={(value) => onToggleChecked(item.tool_id, value === true)}
        />
      </label>

      <button
        type="button"
        onClick={() => onSelect(item.tool_id)}
        aria-current={active ? "true" : undefined}
        className="flex min-w-0 flex-1 flex-col gap-1 rounded-md text-left focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
      >
        <span className="flex flex-wrap items-center gap-2">
          <span className="truncate font-medium">{item.tool_name}</span>
          <SubmissionTypeBadge type={item.submission_type} />
          {item.pending_version ? (
            <span className="text-xs text-muted-foreground tabular-nums">
              v{item.pending_version.version}
            </span>
          ) : null}
        </span>

        <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
          <ToolTypeBadge type={item.tool_type} />
          <span className="truncate">{item.owner?.display_name ?? "未知提交人"}</span>
          <WaitingBadge hours={item.waiting_hours} />
        </span>

        <span className="line-clamp-1 text-xs text-muted-foreground">
          {queueItemSummaryLine(item)}
        </span>
      </button>
    </div>
  );
}
