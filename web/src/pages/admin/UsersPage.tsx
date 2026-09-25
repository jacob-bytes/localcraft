import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, Download, Filter, Plus, Search, Upload, UserRound } from "lucide-react";
import * as React from "react";
import { Link, useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import {
  adminUsersQueryKey,
  fetchAdminUsers,
  fetchRoles,
  revokeAdminUserSessions,
  rolesQueryKey,
  updateAdminUser,
} from "@/api/admin";
import { getErrorMessage, isApiError } from "@/api/client";
import type { AdminUserItem, AdminUserListParams, Role, UserStatus } from "@/api/types";
import { ResetPasswordDialog } from "@/components/admin/ResetPasswordDialog";
import { UserFormDialog, type UserFormMode } from "@/components/admin/UserFormDialog";
import { UserTable, roleLabel } from "@/components/admin/UserTable";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { PageSkeleton } from "@/components/common/PageSkeleton";
import { Pagination } from "@/components/common/Pagination";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useAuth } from "@/hooks/useAuth";
import { useDebounce } from "@/hooks/useDebounce";

/**
 * `/admin/users` —— 用户管理（docs/04 §6.13，FR-IAM-01~08）。
 *
 * 分工：
 *  - 服务端是唯一权威：角色中文名来自 `GET /admin/roles`，状态、`roles` 直接读接口值。
 *  - 用户**不可删除**（FR-IAM-06），界面只提供禁用；自己的行禁用项置灰（FR-IAM-08）。
 *  - 导入/导出按钮只做跳转，真正的实现在 `/admin/import-export`（另一页负责）。
 *
 * 筛选状态（`q` / `role` / `status` / `page`）全部写在 URL 上，
 * 刷新或分享链接都能还原（docs/04 §6.3 的同一约定）。
 */

const ROLES: readonly Role[] = ["viewer", "user", "approver", "superadmin"];
const PAGE_SIZE = 20;
const USERS_KEY_PREFIX = ["admin", "users"] as const;

type StatusFilter = UserStatus | "all";

function isRole(value: string): value is Role {
  return (ROLES as readonly string[]).includes(value);
}

function parsePage(raw: string | null): number {
  const parsed = Number.parseInt(raw ?? "1", 10);
  return Number.isFinite(parsed) && parsed >= 1 ? parsed : 1;
}

export default function UsersPage() {
  const { user: currentUser } = useAuth();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();

  const q = searchParams.get("q") ?? "";
  const roleFilter = searchParams.getAll("role").filter(isRole);
  const statusParam = searchParams.get("status");
  const status: StatusFilter =
    statusParam === "active" || statusParam === "disabled" ? statusParam : "all";
  const page = parsePage(searchParams.get("page"));

  const roleKey = roleFilter.join(",");
  /** 只由影响查询结果的原始值构成，用于「筛选变了就清空选择」。 */
  const paramsKey = `${q}|${roleKey}|${status}|${page}`;

  const [searchInput, setSearchInput] = React.useState(q);
  const debouncedSearch = useDebounce(searchInput, 300);

  const [selectedIds, setSelectedIds] = React.useState<number[]>([]);
  const [formOpen, setFormOpen] = React.useState(false);
  const [formMode, setFormMode] = React.useState<UserFormMode>("create");
  const [formUser, setFormUser] = React.useState<AdminUserItem | null>(null);
  const [resetTarget, setResetTarget] = React.useState<AdminUserItem | null>(null);
  const [revokeTarget, setRevokeTarget] = React.useState<AdminUserItem | null>(null);
  const [statusTarget, setStatusTarget] = React.useState<AdminUserItem | null>(null);

  const updateParams = React.useCallback(
    (patch: Record<string, string | string[] | null>, options?: { resetPage?: boolean }) => {
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
        { replace: true },
      );
    },
    [setSearchParams],
  );

  /* 外部 URL 变化（返回/前进、分享链接）要把搜索框同步过来（PortalPage 同款做法）。 */
  const [syncedQuery, setSyncedQuery] = React.useState(q);
  if (syncedQuery !== q) {
    setSyncedQuery(q);
    setSearchInput(q);
  }

  /* 输入防抖 300ms 后才写 URL 并触发请求（docs/04 §9）。 */
  React.useEffect(() => {
    const trimmed = debouncedSearch.trim();
    if (searchInput.trim() !== trimmed) return;
    if (trimmed === q) return;
    updateParams({ q: trimmed || null }, { resetPage: true });
  }, [debouncedSearch, searchInput, q, updateParams]);

  const params = React.useMemo<AdminUserListParams>(
    () => ({
      q: q || undefined,
      role: roleKey ? (roleKey.split(",").filter(isRole) as Role[]) : undefined,
      status: status === "all" ? undefined : status,
      page,
      page_size: PAGE_SIZE,
    }),
    [q, roleKey, status, page],
  );

  const usersQuery = useQuery({
    queryKey: adminUsersQueryKey(params),
    queryFn: ({ signal }) => fetchAdminUsers(params, signal),
    placeholderData: (previous) => previous,
  });

  const rolesQuery = useQuery({
    queryKey: rolesQueryKey,
    queryFn: ({ signal }) => fetchRoles(signal),
    staleTime: 5 * 60_000,
  });

  const users = usersQuery.data?.items ?? [];
  const total = usersQuery.data?.total ?? 0;
  const pages = usersQuery.data?.pages ?? 0;
  const roles = rolesQuery.data ?? [];
  const hasFilters = q !== "" || roleFilter.length > 0 || status !== "all";

  /* 翻页/改筛选后旧的选择没有意义，清空避免误操作。
     用「渲染期修正 state」而不是 effect：不触发额外的渲染回合（PortalPage 同款）。 */
  const [syncedParamsKey, setSyncedParamsKey] = React.useState(paramsKey);
  if (syncedParamsKey !== paramsKey) {
    setSyncedParamsKey(paramsKey);
    setSelectedIds([]);
  }

  const invalidateUsers = React.useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: USERS_KEY_PREFIX });
    void queryClient.invalidateQueries({ queryKey: rolesQueryKey });
  }, [queryClient]);

  const revokeMutation = useMutation({
    mutationFn: (userId: number) => revokeAdminUserSessions(userId),
    onSuccess: (response) => {
      toast.success(`已强制下线，吊销会话 ${response.revoked_sessions} 个`);
      setRevokeTarget(null);
    },
    onError: (error) => {
      toast.error(getErrorMessage(error));
      setRevokeTarget(null);
    },
  });

  const statusMutation = useMutation({
    mutationFn: ({ userId, next }: { userId: number; next: UserStatus }) =>
      updateAdminUser(userId, { status: next }),
    onSuccess: (updated) => {
      toast.success(updated.status === "disabled" ? "已禁用该用户" : "已启用该用户");
      invalidateUsers();
      setStatusTarget(null);
    },
    onError: (error) => {
      // FR-IAM-07：服务端兜底返回 409 LAST_SUPERADMIN。
      if (isApiError(error) && error.is("LAST_SUPERADMIN")) {
        toast.error("不能禁用或降级最后一个超级管理员");
      } else {
        toast.error(getErrorMessage(error));
      }
      setStatusTarget(null);
    },
  });

  const toggleSelected = React.useCallback((userId: number, checked: boolean) => {
    setSelectedIds((current) =>
      checked
        ? current.includes(userId)
          ? current
          : [...current, userId]
        : current.filter((id) => id !== userId),
    );
  }, []);

  const toggleAll = (checked: boolean) =>
    setSelectedIds(checked ? users.map((item) => item.id) : []);

  const openCreate = React.useCallback(() => {
    setFormMode("create");
    setFormUser(null);
    setFormOpen(true);
  }, []);

  const openEdit = React.useCallback((target: AdminUserItem) => {
    setFormMode("edit");
    setFormUser(target);
    setFormOpen(true);
  }, []);

  const toggleRole = React.useCallback(
    (code: Role, checked: boolean) => {
      const next = checked
        ? [...roleFilter.filter((item) => item !== code), code]
        : roleFilter.filter((item) => item !== code);
      updateParams({ role: next.length > 0 ? next : null }, { resetPage: true });
    },
    [roleFilter, updateParams],
  );

  return (
    <div data-testid="users-page" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">用户管理</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            账号只支持禁用，平台保留其历史记录与审计关联；禁用会立即吊销其全部会话与 API Token。
          </p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[14rem] flex-1 sm:max-w-xs">
          <Search
            aria-hidden="true"
            className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            placeholder="搜索用户名或显示名"
            aria-label="搜索用户"
            className="pl-8"
          />
        </div>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button type="button" variant="outline" size="sm">
              <Filter aria-hidden="true" className="size-4" />
              角色
              {roleFilter.length > 0 ? `（${roleFilter.length}）` : ""}
              <ChevronDown aria-hidden="true" className="size-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-52">
            <DropdownMenuLabel>按角色筛选</DropdownMenuLabel>
            <DropdownMenuSeparator />
            {ROLES.map((code) => (
              <DropdownMenuCheckboxItem
                key={code}
                checked={roleFilter.includes(code)}
                // 多选时不要让菜单关掉（Radix 默认选中即关闭）。
                onSelect={(event) => event.preventDefault()}
                onCheckedChange={(checked) => toggleRole(code, checked === true)}
              >
                {roleLabel(code, roles)}
              </DropdownMenuCheckboxItem>
            ))}
            {roleFilter.length > 0 ? (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  onSelect={() => updateParams({ role: null }, { resetPage: true })}
                >
                  清除角色筛选
                </DropdownMenuItem>
              </>
            ) : null}
          </DropdownMenuContent>
        </DropdownMenu>

        <Select
          value={status}
          onValueChange={(value) =>
            updateParams({ status: value === "all" ? null : value }, { resetPage: true })
          }
        >
          <SelectTrigger className="w-32" aria-label="按状态筛选">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部状态</SelectItem>
            <SelectItem value="active">启用</SelectItem>
            <SelectItem value="disabled">禁用</SelectItem>
          </SelectContent>
        </Select>

        {hasFilters ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => updateParams({ q: null, role: null, status: null }, { resetPage: true })}
          >
            清除筛选
          </Button>
        ) : null}

        <div className="ml-auto flex flex-wrap items-center gap-2">
          {/* 导入/导出由 `/admin/import-export` 负责，这里只做跳转（docs/04 §6.13）。 */}
          <Button type="button" variant="outline" size="sm" asChild>
            <Link to="/admin/import-export">
              <Upload aria-hidden="true" className="size-4" />
              导入 CSV
            </Link>
          </Button>
          <Button type="button" variant="outline" size="sm" asChild>
            <Link to="/admin/import-export">
              <Download aria-hidden="true" className="size-4" />
              导出 CSV
            </Link>
          </Button>
          <Button type="button" size="sm" data-testid="user-create-button" onClick={openCreate}>
            <Plus aria-hidden="true" className="size-4" />
            新建用户
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        <span>共 {total} 位用户</span>
        {selectedIds.length > 0 ? (
          <span data-testid="user-selection-hint">
            已选 {selectedIds.length} 项 · 接口未提供批量端点，请逐行操作
          </span>
        ) : null}
      </div>

      {usersQuery.isPending ? (
        <PageSkeleton variant="list" />
      ) : usersQuery.isError ? (
        <ErrorState
          message={getErrorMessage(usersQuery.error)}
          onRetry={() => void usersQuery.refetch()}
        />
      ) : users.length === 0 ? (
        <EmptyState
          icon={UserRound}
          title={hasFilters ? "没有匹配的用户" : "还没有用户"}
          description={
            hasFilters
              ? "换个关键词或清除筛选条件再试。"
              : "创建第一个用户后，可在这里管理角色与启用状态。"
          }
          action={
            hasFilters ? (
              <Button
                type="button"
                variant="outline"
                onClick={() =>
                  updateParams({ q: null, role: null, status: null }, { resetPage: true })
                }
              >
                清除筛选
              </Button>
            ) : (
              <Button type="button" variant="outline" onClick={openCreate}>
                新建用户
              </Button>
            )
          }
        />
      ) : (
        <UserTable
          users={users}
          roles={roles}
          currentUserId={currentUser?.id ?? null}
          selectedIds={selectedIds}
          onToggleSelected={toggleSelected}
          onToggleAll={toggleAll}
          onEdit={openEdit}
          onResetPassword={setResetTarget}
          onRevokeSessions={setRevokeTarget}
          onToggleStatus={setStatusTarget}
        />
      )}

      {total > PAGE_SIZE ? (
        <Pagination
          page={usersQuery.data?.page ?? page}
          pages={pages}
          onPageChange={(next) => updateParams({ page: String(next) })}
        />
      ) : null}

      {/* `key` 让每次打开都是全新实例：表单默认值、一次性密码面板都不会串场。 */}
      <UserFormDialog
        key={`${formMode}-${formUser?.id ?? "new"}-${formOpen ? "open" : "closed"}`}
        open={formOpen}
        onOpenChange={setFormOpen}
        mode={formMode}
        user={formUser}
        roles={roles}
        rolesPending={rolesQuery.isPending}
        onSaved={invalidateUsers}
      />

      <ResetPasswordDialog
        key={`${resetTarget?.id ?? "none"}-${resetTarget ? "open" : "closed"}`}
        open={resetTarget !== null}
        onOpenChange={(open) => {
          if (!open) setResetTarget(null);
        }}
        user={resetTarget}
        onDone={invalidateUsers}
      />

      <ConfirmDialog
        open={revokeTarget !== null}
        onOpenChange={(open) => {
          if (!open) setRevokeTarget(null);
        }}
        title={`强制下线「${revokeTarget?.display_name ?? revokeTarget?.username ?? ""}」？`}
        description="该用户当前的登录会话会立即失效，需要重新登录。已签发的 API Token 不受影响。"
        confirmLabel="强制下线"
        pending={revokeMutation.isPending}
        onConfirm={() => {
          if (revokeTarget) revokeMutation.mutate(revokeTarget.id);
        }}
      />

      <ConfirmDialog
        open={statusTarget !== null}
        onOpenChange={(open) => {
          if (!open) setStatusTarget(null);
        }}
        title={
          statusTarget?.status === "active"
            ? `禁用「${statusTarget.display_name || statusTarget.username}」？`
            : `启用「${statusTarget?.display_name ?? statusTarget?.username ?? ""}」？`
        }
        description={
          statusTarget?.status === "active"
            ? "禁用后该用户无法登录，其全部会话与 API Token 立即失效（FR-IAM-05）。账号与历史记录保留，可随时重新启用。"
            : "启用后该用户可以重新登录并使用平台。"
        }
        confirmLabel={statusTarget?.status === "active" ? "确认禁用" : "确认启用"}
        destructive={statusTarget?.status === "active"}
        pending={statusMutation.isPending}
        onConfirm={() => {
          if (!statusTarget) return;
          statusMutation.mutate({
            userId: statusTarget.id,
            next: statusTarget.status === "active" ? "disabled" : "active",
          });
        }}
      />
    </div>
  );
}
