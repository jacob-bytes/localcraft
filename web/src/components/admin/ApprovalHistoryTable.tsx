import { ChevronDown, ChevronUp } from "lucide-react";
import * as React from "react";
import { Link } from "react-router-dom";

import type { ApprovalAction, ApprovalRecord } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * 审批历史表格（docs/04 §6.11）。
 *
 * 动作徽标配色按 docs/04 §6.11 的映射实现；每个徽标都带文字，
 * 不允许只靠颜色区分（docs/04 §8.1）。系统自动产生的记录额外标注「系统自动」。
 */

type BadgeVariant = "secondary" | "success" | "destructive" | "outline";

export const ACTION_LABELS: Record<ApprovalAction, string> = {
  submit: "提交",
  withdraw: "撤回",
  resubmit: "重新提交",
  approve: "批准",
  reject: "驳回",
  offline: "下架",
  relist: "重新上架",
  purge_version: "归档版本",
  transfer_owner: "转移负责人",
};

/** docs/04 §6.11 配色表（transfer_owner 在文档里没有单列，按中性处理）。 */
export const ACTION_VARIANTS: Record<ApprovalAction, BadgeVariant> = {
  submit: "secondary",
  withdraw: "secondary",
  resubmit: "secondary",
  approve: "success",
  reject: "destructive",
  offline: "destructive",
  relist: "success",
  purge_version: "outline",
  transfer_owner: "secondary",
};

export const ACTION_OPTIONS: ReadonlyArray<{ value: ApprovalAction; label: string }> = (
  Object.keys(ACTION_LABELS) as ApprovalAction[]
).map((value) => ({ value, label: ACTION_LABELS[value] }));

/** `from → to`，两边都有版本状态时追加在括号里（docs/04 §6.11）。 */
export function describeStatusChange(record: ApprovalRecord): string {
  const parts: string[] = [];
  if (record.from_status || record.to_status) {
    parts.push(`${record.from_status ?? "—"} → ${record.to_status}`);
  }
  if (record.version_from_status || record.version_to_status) {
    const versionPart = `版本 ${record.version_from_status ?? "—"} → ${
      record.version_to_status ?? "—"
    }`;
    parts.push(parts.length > 0 ? `（${versionPart}）` : versionPart);
  }
  return parts.length > 0 ? parts.join(" ") : "—";
}

export interface ApprovalHistoryTableProps {
  records: readonly ApprovalRecord[];
  expandedIds: readonly number[];
  onToggleExpand: (recordId: number) => void;
}

export function ApprovalHistoryTable({
  records,
  expandedIds,
  onToggleExpand,
}: ApprovalHistoryTableProps) {
  const expanded = React.useMemo(() => new Set(expandedIds), [expandedIds]);

  return (
    <div className="overflow-x-auto rounded-xl border">
      <table className="w-full min-w-[960px] border-collapse text-sm">
        <caption className="sr-only">审批历史记录</caption>
        <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
          <tr>
            <th scope="col" className="px-3 py-2 font-medium">
              时间
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              工具
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              动作
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              操作人
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              状态变更
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              理由 / 备注
            </th>
          </tr>
        </thead>
        <tbody>
          {records.map((record) => {
            const isExpanded = expanded.has(record.id);
            const reason = record.reason ?? "";
            const note = record.note ?? "";
            const text = [reason, note].filter(Boolean).join(" · ");
            const isLong = text.length > 40;

            return (
              <tr key={record.id} className="border-t align-top">
                <td className="px-3 py-2 whitespace-nowrap tabular-nums">
                  <span title={record.created_at ?? undefined}>
                    {formatDateTime(record.created_at)}
                  </span>
                </td>
                <td className="px-3 py-2">
                  {record.tool_slug ? (
                    <Link
                      to={`/tools/${record.tool_slug}`}
                      className="rounded font-medium underline-offset-4 hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                    >
                      {record.tool_name ?? record.tool_slug}
                    </Link>
                  ) : (
                    <span>{record.tool_name ?? `#${record.tool_id}`}</span>
                  )}
                  {record.version ? (
                    <span className="ml-1 text-xs text-muted-foreground tabular-nums">
                      v{record.version}
                    </span>
                  ) : null}
                </td>
                <td className="px-3 py-2">
                  <ActionBadge action={record.action} isAutomatic={record.is_automatic} />
                </td>
                <td className="px-3 py-2">
                  <span>{record.actor_label}</span>
                  {record.is_automatic ? (
                    <Badge variant="outline" className="ml-1 text-[11px]">
                      系统自动
                    </Badge>
                  ) : null}
                </td>
                <td className="px-3 py-2 text-xs text-muted-foreground">
                  {describeStatusChange(record)}
                </td>
                <td className="max-w-[18rem] px-3 py-2">
                  {text.length === 0 ? (
                    <span className="text-muted-foreground">—</span>
                  ) : (
                    <div className="space-y-1">
                      <p
                        className={cn("text-xs", !isExpanded && "line-clamp-2")}
                        title={isExpanded ? undefined : text}
                      >
                        {text}
                      </p>
                      {isLong ? (
                        <Button
                          type="button"
                          variant="ghost"
                          size="xs"
                          aria-expanded={isExpanded}
                          onClick={() => onToggleExpand(record.id)}
                        >
                          {isExpanded ? (
                            <ChevronUp aria-hidden="true" className="size-3" />
                          ) : (
                            <ChevronDown aria-hidden="true" className="size-3" />
                          )}
                          {isExpanded ? "收起" : "展开"}
                        </Button>
                      ) : null}
                    </div>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ActionBadge({
  action,
  isAutomatic,
}: {
  action: ApprovalAction;
  isAutomatic: boolean;
}) {
  const variant = ACTION_VARIANTS[action];
  const label = isAutomatic ? "系统自动" : ACTION_LABELS[action];
  return (
    <Badge variant={variant} title={ACTION_LABELS[action]}>
      {label}
    </Badge>
  );
}

export default ApprovalHistoryTable;
