import { KeyRound, LogOut, Pencil, Power } from "lucide-react";
import * as React from "react";

import type { AdminUserItem, RoleOut, UserStatus } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatDateTime, formatRelativeTime, initials } from "@/lib/format";
import { ROLE_LABELS } from "@/lib/permissions";
import { cn } from "@/lib/utils";

/**
 * 用户管理表格（docs/04 §6.13）。
 *
 * 关键约束（验收 #6）：
 *  - **没有删除入口** —— 用户与工具/审批/下载记录有外键关联，只能禁用（FR-IAM-06）。
 *    面向用户的文案里刻意不含「删除」二字（验收 #6）。
 *  - 当前登录用户那一行的「禁用」菜单项 `disabled`（FR-IAM-08）。
 *  - 状态徽标始终带文字，不靠颜色单独表意（docs/04 §8.1）。
 *
 * 头像没有图片可用，用「显示名首字母 + 由 display_name 决定的底色」代替，
 * 颜色只取自语义 token，浅色/深色主题下对比度都成立（docs/04 §3.4）。
 */

/** 底色候选：只改背景，文字统一 `text-foreground`，避免浅色模式下低对比。 */
const AVATAR_TONES = [
  "bg-primary/15",
  "bg-success/20",
  "bg-warning/25",
  "bg-destructive/15",
  "bg-accent",
  "bg-muted",
] as const;

/** 稳定哈希（同一显示名永远同一个底色，列表重排不会闪色）。 */
export function avatarToneClass(displayName: string): string {
  let hash = 0;
  for (let index = 0; index < displayName.length; index += 1) {
    hash = (hash + displayName.charCodeAt(index) * (index + 1)) % 997;
  }
  return AVATAR_TONES[hash % AVATAR_TONES.length] ?? "bg-muted";
}

/**
 * 角色显示名以服务端 `RoleOut.name` 为准（docs/01 §3.3：前端不重算权限），
 * `ROLE_LABELS` 只作为角色清单尚未加载时的兜底文案。
 */
export function roleLabel(code: string, roles: readonly RoleOut[]): string {
  const fromServer = roles.find((role) => role.code === code)?.name;
  if (fromServer) return fromServer;
  const fallback = (ROLE_LABELS as Record<string, string | undefined>)[code];
  return fallback ?? code;
}

const ROLE_BADGE_VARIANTS: Record<string, "default" | "secondary" | "success" | "outline"> = {
  superadmin: "default",
  approver: "success",
  user: "secondary",
  viewer: "outline",
};

/** 状态徽标：启用 = success / 禁用 = secondary，两种都带文字。 */
export function UserStatusBadge({ status }: { status: UserStatus }) {
  return (
    <Badge variant={status === "active" ? "success" : "secondary"}>
      {status === "active" ? "启用" : "禁用"}
    </Badge>
  );
}

export interface UserTableProps {
  users: readonly AdminUserItem[];
  /** 服务端角色清单，用于把 `user.roles` 的 code 显示成中文名。 */
  roles: readonly RoleOut[];
  /** `useAuth().user.id` —— 自己的行不能禁用（FR-IAM-08）。 */
  currentUserId: number | null;
  selectedIds: readonly number[];
  onToggleSelected: (userId: number, checked: boolean) => void;
  onToggleAll: (checked: boolean) => void;
  onEdit: (user: AdminUserItem) => void;
  onResetPassword: (user: AdminUserItem) => void;
  onRevokeSessions: (user: AdminUserItem) => void;
  onToggleStatus: (user: AdminUserItem) => void;
}

export function UserTable({
  users,
  roles,
  currentUserId,
  selectedIds,
  onToggleSelected,
  onToggleAll,
  onEdit,
  onResetPassword,
  onRevokeSessions,
  onToggleStatus,
}: UserTableProps) {
  const selected = React.useMemo(() => new Set(selectedIds), [selectedIds]);
  const allSelected = users.length > 0 && users.every((user) => selected.has(user.id));
  const someSelected = !allSelected && users.some((user) => selected.has(user.id));

  return (
    <Table data-testid="user-table">
      <TableCaption className="sr-only">用户列表</TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead className="w-10">
            {/*
              M7 · F2（docs/12 §U3）：行选择 checkbox 的真实命中区原本只有 16×16。
              外层 <label> 把它撑到 32×32，`-m-2` 抵消掉多出来的 8px 边距，
              表格布局与视觉尺寸（仍是 16×16）都不变。
            */}
            <label className="-m-2 inline-flex size-8 cursor-pointer items-center justify-center">
              <Checkbox
                checked={allSelected ? true : someSelected ? "indeterminate" : false}
                aria-label={allSelected ? "取消全选本页用户" : "全选本页用户"}
                onCheckedChange={(value) => onToggleAll(value === true)}
              />
            </label>
          </TableHead>
          <TableHead>用户</TableHead>
          <TableHead>用户名</TableHead>
          <TableHead>邮箱</TableHead>
          <TableHead>角色</TableHead>
          <TableHead>状态</TableHead>
          <TableHead>最后登录</TableHead>
          <TableHead className="text-right">操作</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {users.map((user) => {
          const displayName = user.display_name || user.username;
          const isSelf = currentUserId !== null && user.id === currentUserId;
          const isActive = user.status === "active";

          return (
            <TableRow key={user.id} data-testid="user-row" data-user-id={user.id}>
              <TableCell>
                {/* M7 · F2：命中区 32×32，视觉仍是 16×16（同表头）。 */}
                <label className="-m-2 inline-flex size-8 cursor-pointer items-center justify-center">
                  <Checkbox
                    checked={selected.has(user.id)}
                    aria-label={`选择用户 ${displayName}`}
                    onCheckedChange={(value) => onToggleSelected(user.id, value === true)}
                  />
                </label>
              </TableCell>

              <TableCell>
                <div className="flex items-center gap-2">
                  <span
                    aria-hidden="true"
                    className={cn(
                      "grid size-8 shrink-0 place-items-center rounded-full text-xs font-semibold text-foreground",
                      avatarToneClass(displayName),
                    )}
                  >
                    {initials(displayName)}
                  </span>
                  <span className="min-w-0">
                    <span className="block max-w-[12rem] truncate font-medium">{displayName}</span>
                    {isSelf ? (
                      <span className="block text-xs text-muted-foreground">当前登录账号</span>
                    ) : null}
                  </span>
                </div>
              </TableCell>

              <TableCell className="font-mono text-xs">{user.username}</TableCell>

              <TableCell className="text-xs">
                {user.email ? (
                  <span className="block max-w-[14rem] truncate" title={user.email}>
                    {user.email}
                  </span>
                ) : (
                  <span className="text-muted-foreground">—</span>
                )}
              </TableCell>

              <TableCell>
                <div className="flex flex-wrap gap-1">
                  {user.roles.length === 0 ? (
                    <span className="text-xs text-muted-foreground">—</span>
                  ) : (
                    user.roles.map((code) => (
                      <Badge key={code} variant={ROLE_BADGE_VARIANTS[code] ?? "outline"}>
                        {roleLabel(code, roles)}
                      </Badge>
                    ))
                  )}
                </div>
              </TableCell>

              <TableCell>
                <UserStatusBadge status={user.status} />
              </TableCell>

              <TableCell className="text-xs text-muted-foreground">
                {user.last_login_at ? (
                  <span title={formatDateTime(user.last_login_at)}>
                    {formatRelativeTime(user.last_login_at)}
                  </span>
                ) : (
                  <span title="从未登录">—</span>
                )}
              </TableCell>

              <TableCell className="text-right">
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label={`用户 ${displayName} 的操作`}
                    >
                      <span aria-hidden="true" className="text-lg leading-none">
                        ⋯
                      </span>
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="w-44">
                    <DropdownMenuLabel className="truncate">{user.username}</DropdownMenuLabel>
                    <DropdownMenuSeparator />

                    <DropdownMenuItem onSelect={() => onEdit(user)}>
                      <Pencil aria-hidden="true" />
                      编辑
                    </DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => onResetPassword(user)}>
                      <KeyRound aria-hidden="true" />
                      重置密码
                    </DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => onRevokeSessions(user)}>
                      <LogOut aria-hidden="true" />
                      强制下线
                    </DropdownMenuItem>

                    <DropdownMenuSeparator />

                    {/*
                      FR-IAM-06：没有「删除用户」——用户只能被禁用。
                      FR-IAM-08：自己的「禁用」项禁用（服务端同样兜底拒绝）。
                    */}
                    <DropdownMenuItem
                      data-testid={isSelf ? "user-disable-self" : undefined}
                      variant={isActive && !isSelf ? "destructive" : "default"}
                      disabled={isSelf && isActive}
                      title={isSelf && isActive ? "不能禁用自己" : undefined}
                      onSelect={() => onToggleStatus(user)}
                    >
                      <Power aria-hidden="true" />
                      {isActive ? "禁用" : "启用"}
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

export default UserTable;
