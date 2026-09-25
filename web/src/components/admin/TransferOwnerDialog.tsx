import { useQuery } from "@tanstack/react-query";
import { Search, UserRoundCog } from "lucide-react";
import * as React from "react";

import { adminUsersQueryKey, fetchAdminUsers } from "@/api/admin";
import { getErrorMessage } from "@/api/client";
import type { AdminToolItem, AdminUserItem } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useDebounce } from "@/hooks/useDebounce";
import { cn } from "@/lib/utils";

/**
 * 转移负责人 Dialog（docs/04 §6.12 行操作「转移负责人（Dialog 搜索用户）」）。
 *
 * 后端 `POST /admin/tools/{id}/transfer` 属 `admin_all_guard`（仅 superadmin，
 * docs/01 §3.2 权限矩阵），因此入口本身在调用方按角色隐藏。
 *
 * 用户检索走 `GET /admin/users?q=`（同样仅 superadmin 可读），输入 300ms 防抖
 * （docs/04 §9）。
 */
export interface TransferOwnerDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  tool: AdminToolItem | null;
  pending: boolean;
  error?: string | null;
  onConfirm: (newOwnerId: number, reason: string | null) => void;
}

const SEARCH_LIMIT = 20;

export function TransferOwnerDialog({
  open,
  onOpenChange,
  tool,
  pending,
  error,
  onConfirm,
}: TransferOwnerDialogProps) {
  const [term, setTerm] = React.useState("");
  const [selected, setSelected] = React.useState<AdminUserItem | null>(null);
  const [reason, setReason] = React.useState("");
  const [wasOpen, setWasOpen] = React.useState(open);

  const debouncedTerm = useDebounce(term, 300);
  const keyword = debouncedTerm.trim();

  // 每次打开都是一张干净的表单（不在渲染中做副作用，只同步状态）。
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) {
      setTerm("");
      setSelected(null);
      setReason("");
    }
  }

  const usersQuery = useQuery({
    queryKey: adminUsersQueryKey({ q: keyword, page_size: SEARCH_LIMIT }),
    queryFn: ({ signal }) =>
      fetchAdminUsers({ q: keyword || undefined, page_size: SEARCH_LIMIT }, signal),
    enabled: open,
    staleTime: 30_000,
  });

  const users = usersQuery.data?.items ?? [];
  const currentOwnerId = tool?.owner?.id ?? null;

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    const trimmed = reason.trim();
    onConfirm(selected.id, trimmed.length > 0 ? trimmed : null);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="transfer-owner-dialog" className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>转移「{tool?.name ?? ""}」的负责人</DialogTitle>
          <DialogDescription>
            当前负责人：
            {tool?.owner ? `${tool.owner.display_name}（${tool.owner.username}）` : "无"}。
            转移后原负责人失去该工具的管理权限，其存储占用随工具一并转移。
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid gap-1.5">
            <Label htmlFor="transfer-owner-search-input">搜索新负责人</Label>
            <div className="relative">
              <Search
                aria-hidden="true"
                className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
              />
              <Input
                id="transfer-owner-search-input"
                data-testid="transfer-owner-search"
                className="pl-8"
                value={term}
                placeholder="输入用户名或显示名"
                autoComplete="off"
                onChange={(event) => setTerm(event.target.value)}
              />
            </div>
          </div>

          <div
            role="listbox"
            aria-label="候选用户"
            className="max-h-56 overflow-y-auto rounded-md border"
          >
            {usersQuery.isFetching && users.length === 0 ? (
              <p className="p-3 text-sm text-muted-foreground">搜索中…</p>
            ) : usersQuery.isError ? (
              <p role="alert" className="p-3 text-sm text-destructive">
                {getErrorMessage(usersQuery.error)}
              </p>
            ) : users.length === 0 ? (
              <p className="p-3 text-sm text-muted-foreground">
                {keyword ? "没有匹配的用户" : "输入关键词开始搜索"}
              </p>
            ) : (
              <ul className="divide-y">
                {users.map((user) => {
                  const isCurrent = user.id === currentOwnerId;
                  const isSelected = selected?.id === user.id;
                  return (
                    <li key={user.id}>
                      <button
                        type="button"
                        role="option"
                        aria-selected={isSelected}
                        disabled={isCurrent}
                        onClick={() => setSelected(user)}
                        className={cn(
                          "flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors",
                          "focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                          isSelected ? "bg-accent text-accent-foreground" : "hover:bg-accent/50",
                          isCurrent && "cursor-not-allowed opacity-60",
                        )}
                      >
                        <span className="min-w-0 flex-1">
                          <span className="block truncate font-medium">{user.display_name}</span>
                          <span className="block truncate font-mono text-xs text-muted-foreground">
                            {user.username}
                          </span>
                        </span>
                        {isCurrent ? <Badge variant="outline">当前负责人</Badge> : null}
                        {user.status === "disabled" ? (
                          <Badge variant="warning">已禁用</Badge>
                        ) : null}
                        {isSelected ? <Badge variant="success">已选择</Badge> : null}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          <div className="grid gap-1.5">
            <Label htmlFor="transfer-owner-reason">转移理由（选填）</Label>
            <Textarea
              id="transfer-owner-reason"
              rows={2}
              maxLength={2000}
              value={reason}
              disabled={pending}
              onChange={(event) => setReason(event.target.value)}
              placeholder="会写入审批历史，便于日后追溯"
            />
          </div>

          {error ? (
            <p role="alert" className="text-xs text-destructive">
              {error}
            </p>
          ) : null}

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={pending}
              onClick={() => onOpenChange(false)}
            >
              取消
            </Button>
            <Button type="submit" disabled={pending || !selected}>
              <UserRoundCog aria-hidden="true" className="size-4" />
              {pending ? "转移中…" : "确认转移"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default TransferOwnerDialog;
