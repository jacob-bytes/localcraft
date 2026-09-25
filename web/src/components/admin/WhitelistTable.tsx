import { Trash2, UserRound } from "lucide-react";

import type { WhitelistEntry } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatDateTime } from "@/lib/format";

/**
 * 免审白名单表格（docs/04 §6.17，FR-APPR-04）。
 *
 * 移除是破坏性操作，由页面用 `ConfirmDialog` 统一确认；这里只负责渲染
 * 并回调 `onRemoveRequest`。头像用首字母兜底，避免白名单里出现破图。
 */

export interface WhitelistTableProps {
  entries: readonly WhitelistEntry[];
  onRemoveRequest: (entry: WhitelistEntry) => void;
}

export function WhitelistTable({ entries, onRemoveRequest }: WhitelistTableProps) {
  return (
    <div data-testid="whitelist-table" className="overflow-x-auto rounded-xl border">
      <table className="w-full min-w-[880px] border-collapse text-sm">
        <caption className="sr-only">免审白名单</caption>
        <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
          <tr>
            <th scope="col" className="px-3 py-2 font-medium">
              用户
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              加入原因
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              加入人
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              加入时间
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              过期时间
            </th>
            <th scope="col" className="px-3 py-2 text-right font-medium">
              操作
            </th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => {
            const displayName = entry.display_name ?? entry.username ?? `用户 #${entry.user_id}`;
            return (
              <tr key={entry.user_id} className="border-t align-middle">
                <td className="px-3 py-2">
                  <div className="flex items-center gap-2">
                    <span
                      aria-hidden="true"
                      className="grid size-7 shrink-0 place-items-center rounded-full bg-muted text-xs font-medium"
                    >
                      {displayName.slice(0, 1).toUpperCase()}
                    </span>
                    <span className="min-w-0">
                      <span className="block truncate font-medium">{displayName}</span>
                      <span className="block truncate text-xs text-muted-foreground">
                        {entry.username ?? `#${entry.user_id}`}
                      </span>
                    </span>
                    {entry.is_effective ? null : (
                      <Badge variant="secondary" title="已过期或已失效">
                        已失效
                      </Badge>
                    )}
                  </div>
                </td>
                <td className="max-w-[16rem] px-3 py-2 text-xs">
                  {entry.reason ? (
                    <span className="line-clamp-2" title={entry.reason}>
                      {entry.reason}
                    </span>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </td>
                <td className="px-3 py-2 text-xs">{entry.added_by_name ?? "—"}</td>
                <td className="px-3 py-2 text-xs whitespace-nowrap tabular-nums">
                  <span title={entry.created_at ?? undefined}>
                    {formatDateTime(entry.created_at)}
                  </span>
                </td>
                <td className="px-3 py-2 text-xs whitespace-nowrap tabular-nums">
                  {entry.expires_at ? (
                    <span title={entry.expires_at}>{formatDateTime(entry.expires_at)}</span>
                  ) : (
                    "永不过期"
                  )}
                </td>
                <td className="px-3 py-2 text-right">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    aria-label={`移除 ${displayName} 的免审资格`}
                    onClick={() => onRemoveRequest(entry)}
                  >
                    <Trash2 aria-hidden="true" className="size-4" />
                    移除
                  </Button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {entries.length === 0 ? (
        <p className="flex items-center justify-center gap-2 px-3 py-6 text-sm text-muted-foreground">
          <UserRound aria-hidden="true" className="size-4" />
          白名单为空，所有用户提交都需要审批。
        </p>
      ) : null}
    </div>
  );
}

export default WhitelistTable;
