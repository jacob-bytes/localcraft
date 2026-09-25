import { KeyRound } from "lucide-react";

import type { ApiTokenOut } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDateTime } from "@/lib/format";

/**
 * API Token 列表（docs/04 §6.18）。
 *
 * 只做渲染与回调：吊销 / 删除的二次确认由页面负责，因为两者语义不同
 * （吊销保留审计痕迹，删除是物理删除）。状态徽标同时给出颜色与文字
 * （docs/04 §8.1：状态不能只靠颜色区分）。
 */
export interface TokenStatusMeta {
  label: string;
  /** `Badge` 变体：有效=success / 已吊销=destructive / 已过期=secondary。 */
  variant: "success" | "destructive" | "secondary";
}

export function tokenStatusMeta(token: ApiTokenOut, now: number = Date.now()): TokenStatusMeta {
  if (token.revoked_at) return { label: "已吊销", variant: "destructive" };
  if (token.expires_at) {
    const expiresAt = new Date(token.expires_at).getTime();
    if (Number.isFinite(expiresAt) && expiresAt <= now) {
      return { label: "已过期", variant: "secondary" };
    }
  }
  if (token.is_active) return { label: "有效", variant: "success" };
  return { label: "已失效", variant: "secondary" };
}

export interface TokenManagerProps {
  tokens: readonly ApiTokenOut[];
  /** 已吊销的 Token 不能再吊销，但可以删除记录。 */
  onRevokeRequest: (token: ApiTokenOut) => void;
  onDeleteRequest: (token: ApiTokenOut) => void;
}

export function TokenManager({ tokens, onRevokeRequest, onDeleteRequest }: TokenManagerProps) {
  return (
    <div className="overflow-hidden rounded-xl border">
      <Table className="min-w-[1000px]">
        <TableHeader className="bg-muted/50">
          <TableRow>
            <TableHead>名称</TableHead>
            <TableHead>Token 前缀</TableHead>
            <TableHead>权限范围</TableHead>
            <TableHead>创建人</TableHead>
            <TableHead>创建时间</TableHead>
            <TableHead>最后使用</TableHead>
            <TableHead>过期时间</TableHead>
            <TableHead>状态</TableHead>
            <TableHead className="text-right">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {tokens.map((token) => {
            const status = tokenStatusMeta(token);
            return (
              <TableRow key={token.id} data-testid="token-row">
                <TableCell className="max-w-[14rem] font-medium">
                  <span className="line-clamp-1" title={token.name}>
                    {token.name}
                  </span>
                </TableCell>
                <TableCell>
                  <span className="inline-flex items-center gap-1 font-mono text-xs">
                    <KeyRound aria-hidden="true" className="size-3.5 text-muted-foreground" />
                    {token.token_prefix}…
                  </span>
                </TableCell>
                <TableCell>
                  {token.scopes.length > 0 ? (
                    <span className="flex max-w-[16rem] flex-wrap gap-1">
                      {token.scopes.map((scope) => (
                        <Badge key={scope} variant="outline" className="font-mono text-[11px]">
                          {scope}
                        </Badge>
                      ))}
                    </span>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </TableCell>
                <TableCell className="text-xs">{token.created_by_name ?? "—"}</TableCell>
                <TableCell className="text-xs whitespace-nowrap tabular-nums">
                  <span title={token.created_at ?? undefined}>{formatDateTime(token.created_at)}</span>
                </TableCell>
                <TableCell className="text-xs whitespace-nowrap">
                  {token.last_used_at ? (
                    <span className="flex flex-col">
                      <span className="tabular-nums" title={token.last_used_at}>
                        {formatDateTime(token.last_used_at)}
                      </span>
                      <span className="font-mono text-[11px] text-muted-foreground">
                        {token.last_used_ip ?? "IP 未知"}
                      </span>
                    </span>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </TableCell>
                <TableCell className="text-xs whitespace-nowrap tabular-nums">
                  {token.expires_at ? (
                    <span title={token.expires_at}>{formatDateTime(token.expires_at)}</span>
                  ) : (
                    "永不过期"
                  )}
                </TableCell>
                <TableCell>
                  <Badge variant={status.variant}>{status.label}</Badge>
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex justify-end gap-1">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={token.revoked_at !== null}
                      aria-label={`吊销 ${token.name}`}
                      onClick={() => onRevokeRequest(token)}
                    >
                      吊销
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="text-destructive hover:text-destructive"
                      aria-label={`删除 ${token.name} 的记录`}
                      onClick={() => onDeleteRequest(token)}
                    >
                      删除记录
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
