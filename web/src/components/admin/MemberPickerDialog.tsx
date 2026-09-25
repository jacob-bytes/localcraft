import { useQuery } from "@tanstack/react-query";
import { Search, UserPlus } from "lucide-react";
import * as React from "react";

import { adminUsersQueryKey, fetchAdminUsers } from "@/api/admin";
import { getErrorMessage } from "@/api/client";
import type { AdminUserItem } from "@/api/types";
import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { PageSkeleton } from "@/components/common/PageSkeleton";
import { Pagination } from "@/components/common/Pagination";
import { UserStatusBadge, avatarToneClass } from "@/components/admin/UserTable";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useDebounce } from "@/hooks/useDebounce";
import { formatDateTime, initials } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * 成员选择器（docs/04 §6.14，FR-GRP-02）。
 *
 * 复用 `GET /admin/users?q=`（用户管理接口，M3 冻结清单内）做搜索，
 * 支持多选；已在组内的用户置灰并标注，避免重复提交（服务端也会返回
 * `already_members`，但前端先挡掉一层更清楚）。
 */

const PAGE_SIZE = 20;

export interface MemberPickerDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 当前已在组内的用户 id —— 行会被禁用。 */
  existingUserIds: readonly number[];
  onConfirm: (userIds: number[]) => void;
  pending: boolean;
}

export function MemberPickerDialog({
  open,
  onOpenChange,
  existingUserIds,
  onConfirm,
  pending,
}: MemberPickerDialogProps) {
  const [searchInput, setSearchInput] = React.useState("");
  const [page, setPage] = React.useState(1);
  const [selected, setSelected] = React.useState<number[]>([]);
  const debouncedSearch = useDebounce(searchInput, 300);
  const q = debouncedSearch.trim();

  // 搜索词变化回到第一页：渲染期修正 state，不额外多一个渲染回合。
  const [syncedQuery, setSyncedQuery] = React.useState(q);
  if (syncedQuery !== q) {
    setSyncedQuery(q);
    setPage(1);
  }

  // 打开/关闭由父组件用 `key` 重新挂载，`selected` 与搜索词天然是干净的。

  const usersQuery = useQuery({
    queryKey: adminUsersQueryKey({ q: q || undefined, page, page_size: PAGE_SIZE }),
    queryFn: ({ signal }) => fetchAdminUsers({ q: q || undefined, page, page_size: PAGE_SIZE }, signal),
    enabled: open,
    placeholderData: (previous) => previous,
  });

  const existing = React.useMemo(() => new Set(existingUserIds), [existingUserIds]);
  const users = usersQuery.data?.items ?? [];
  const total = usersQuery.data?.total ?? 0;
  const pages = usersQuery.data?.pages ?? 0;

  const toggle = (userId: number, checked: boolean) => {
    setSelected((current) =>
      checked
        ? current.includes(userId)
          ? current
          : [...current, userId]
        : current.filter((id) => id !== userId),
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="member-picker"
        className="max-h-[calc(100vh-2rem)] overflow-y-auto sm:max-w-lg"
      >
        <DialogHeader>
          <DialogTitle>添加成员</DialogTitle>
          <DialogDescription>
            搜索并勾选要加入该用户组的用户。已经在组内的用户不可重复选择。
          </DialogDescription>
        </DialogHeader>

        <div className="relative">
          <Search
            aria-hidden="true"
            className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            placeholder="搜索用户名或显示名"
            aria-label="搜索待添加用户"
            className="pl-8"
          />
        </div>

        <div className="max-h-[22rem] overflow-y-auto rounded-lg border">
          {usersQuery.isPending ? (
            <div className="p-3">
              <PageSkeleton variant="list" count={4} />
            </div>
          ) : usersQuery.isError ? (
            <div className="p-3">
              <ErrorState
                message={getErrorMessage(usersQuery.error)}
                onRetry={() => void usersQuery.refetch()}
              />
            </div>
          ) : users.length === 0 ? (
            <div className="p-3">
              <EmptyState
                className="py-8"
                title={q ? "没有匹配的用户" : "还没有用户"}
                description={q ? "换个关键词再试。" : "先在用户管理中创建用户。"}
              />
            </div>
          ) : (
            <ul className="divide-y">
              {users.map((item) => (
                <MemberCandidateRow
                  key={item.id}
                  user={item}
                  alreadyMember={existing.has(item.id)}
                  checked={selected.includes(item.id)}
                  onCheckedChange={(checked) => toggle(item.id, checked)}
                />
              ))}
            </ul>
          )}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
          <span>
            共 {total} 位用户 · 已选 {selected.length} 位
          </span>
          {pages > 1 ? (
            <Pagination page={usersQuery.data?.page ?? page} pages={pages} onPageChange={setPage} />
          ) : null}
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            type="button"
            data-testid="member-picker-confirm"
            disabled={selected.length === 0 || pending}
            onClick={() => onConfirm(selected)}
          >
            <UserPlus aria-hidden="true" className="size-4" />
            {pending ? "添加中…" : `添加 (${selected.length})`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function MemberCandidateRow({
  user,
  alreadyMember,
  checked,
  onCheckedChange,
}: {
  user: AdminUserItem;
  alreadyMember: boolean;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
}) {
  const displayName = user.display_name || user.username;
  const checkboxId = `member-candidate-${user.id}`;

  return (
    <li
      data-testid="member-picker-row"
      className={cn("flex items-center gap-3 px-3 py-2", alreadyMember && "opacity-60")}
    >
      <Checkbox
        id={checkboxId}
        checked={alreadyMember ? true : checked}
        disabled={alreadyMember}
        aria-label={alreadyMember ? `${displayName} 已在组内` : `选择 ${displayName}`}
        onCheckedChange={(value) => onCheckedChange(value === true)}
      />
      <span
        aria-hidden="true"
        className={cn(
          "grid size-7 shrink-0 place-items-center rounded-full text-xs font-semibold text-foreground",
          avatarToneClass(displayName),
        )}
      >
        {initials(displayName)}
      </span>
      <span className="min-w-0 flex-1">
        <label htmlFor={checkboxId} className="block truncate text-sm font-medium">
          {displayName}
        </label>
        <span className="block truncate font-mono text-xs text-muted-foreground">
          {user.username}
        </span>
      </span>
      {alreadyMember ? <Badge variant="secondary">已在组内</Badge> : null}
      <UserStatusBadge status={user.status} />
      <span className="hidden text-xs text-muted-foreground sm:block" title={user.last_login_at ?? undefined}>
        {user.last_login_at ? formatDateTime(user.last_login_at) : "从未登录"}
      </span>
    </li>
  );
}

export default MemberPickerDialog;
