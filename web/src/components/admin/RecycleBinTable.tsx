import { RotateCcw, Trash2 } from "lucide-react";

import type { AdminToolItem } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDateTime } from "@/lib/format";
import { TOOL_TYPE_META } from "@/lib/toolMeta";

/**
 * 回收站表格（docs/04 §6.21）。
 *
 * 契约缺口：`GET /admin/recycle-bin` 返回 `AdminToolListResponse`，条目里**没有**
 * 「删除人」字段（`AdminToolItem` 只有 `deleted_at`），因此本表不显示该列 ——
 * 宁可不显示，也不编一个服务端没给的字段。
 *
 * 剩余保留天数按 30 天规则在**前端计算**（服务端只给 `deleted_at`）。
 */

/** 软删除保留窗口（docs/01 FR-ADMIN-04：30 天后自动彻底清除）。 */
export const RETENTION_DAYS = 30;

/** `30 - floor((now - deleted_at) / 1day)`，最小 0（docs/04 §6.21）。 */
export function retentionDaysLeft(
  deletedAt: string | null,
  now: number = Date.now(),
): number | null {
  if (!deletedAt) return null;
  const deleted = new Date(deletedAt).getTime();
  if (!Number.isFinite(deleted)) return null;
  const elapsedDays = Math.floor((now - deleted) / 86_400_000);
  return Math.max(0, RETENTION_DAYS - elapsedDays);
}

export interface RecycleBinTableProps {
  items: readonly AdminToolItem[];
  restoringId: number | null;
  purgingId: number | null;
  onRestoreRequest: (tool: AdminToolItem) => void;
  onPurgeRequest: (tool: AdminToolItem) => void;
}

export function RecycleBinTable({
  items,
  restoringId,
  purgingId,
  onRestoreRequest,
  onPurgeRequest,
}: RecycleBinTableProps) {
  return (
    <div className="overflow-hidden rounded-xl border">
      <Table className="min-w-[900px]">
        <TableHeader className="bg-muted/50">
          <TableRow>
            <TableHead>工具</TableHead>
            <TableHead>类型</TableHead>
            <TableHead>原作者</TableHead>
            <TableHead>删除时间</TableHead>
            <TableHead>剩余保留</TableHead>
            <TableHead className="text-right">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((tool) => {
            const meta = TOOL_TYPE_META[tool.tool_type];
            const TypeIcon = meta.icon;
            const daysLeft = retentionDaysLeft(tool.deleted_at);
            return (
              <TableRow key={tool.id} data-testid="recycle-row">
                <TableCell>
                  <div className="flex items-center gap-3">
                    <span
                      className={
                        "grid size-10 shrink-0 place-items-center overflow-hidden rounded-md bg-gradient-to-br " +
                        meta.placeholderClass
                      }
                    >
                      {tool.cover_url ? (
                        <img
                          src={tool.cover_url}
                          alt={`${tool.name} 的封面`}
                          className="size-full object-cover"
                        />
                      ) : (
                        <TypeIcon aria-hidden="true" className="size-4 text-foreground/70" />
                      )}
                    </span>
                    <span className="min-w-0">
                      <span className="block truncate font-medium" title={tool.name}>
                        {tool.name}
                      </span>
                      <span className="block truncate font-mono text-xs text-muted-foreground">
                        {tool.slug}
                      </span>
                    </span>
                  </div>
                </TableCell>
                <TableCell>
                  <Badge variant="outline" className={meta.badgeClass}>
                    <TypeIcon aria-hidden="true" />
                    {meta.label}
                  </Badge>
                </TableCell>
                <TableCell className="text-xs">{tool.owner?.display_name ?? "—"}</TableCell>
                <TableCell className="text-xs whitespace-nowrap tabular-nums">
                  <span title={tool.deleted_at ?? undefined}>{formatDateTime(tool.deleted_at)}</span>
                </TableCell>
                <TableCell className="text-xs whitespace-nowrap">
                  {daysLeft === null ? (
                    <span className="text-muted-foreground">—</span>
                  ) : (
                    <Badge variant={daysLeft <= 3 ? "warning" : "secondary"}>
                      还剩 {daysLeft} 天
                    </Badge>
                  )}
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex justify-end gap-1">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      data-testid="recycle-restore"
                      disabled={restoringId === tool.id || purgingId === tool.id}
                      aria-label={`还原 ${tool.name}`}
                      onClick={() => onRestoreRequest(tool)}
                    >
                      <RotateCcw aria-hidden="true" className="size-4" />
                      还原
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="text-destructive hover:text-destructive"
                      data-testid="recycle-purge"
                      disabled={restoringId === tool.id || purgingId === tool.id}
                      aria-label={`彻底删除 ${tool.name}`}
                      onClick={() => onPurgeRequest(tool)}
                    >
                      <Trash2 aria-hidden="true" className="size-4" />
                      彻底删除
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
