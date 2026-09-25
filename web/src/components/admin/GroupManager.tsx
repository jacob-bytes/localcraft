import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Pencil,
  Plus,
  Power,
  Search,
  Trash2,
  UserMinus,
  UserPlus,
  UsersRound,
} from "lucide-react";
import * as React from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  addGroupMembers,
  createGroup,
  fetchGroupMembers,
  groupMembersQueryKey,
  removeGroupMember,
  updateGroup,
} from "@/api/admin";
import { getErrorMessage } from "@/api/client";
import type { GroupMemberOut, GroupOut } from "@/api/types";
import { MemberPickerDialog } from "@/components/admin/MemberPickerDialog";
import { UserStatusBadge, avatarToneClass } from "@/components/admin/UserTable";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { PageSkeleton } from "@/components/common/PageSkeleton";
import { Pagination } from "@/components/common/Pagination";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
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
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useDebounce } from "@/hooks/useDebounce";
import { formatDateTime, initials } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * 用户组管理（docs/04 §6.14，FR-GRP-01~06）。
 *
 * 左列表右详情：左侧是组列表（名称 + 成员数 + ACL 引用徽标），
 * 右侧是选中组的成员管理（搜索 → 多选添加 → 移除）。
 * 窄屏（< lg）两栏纵向堆叠（docs/04 §8.2）。
 *
 * 组只用于**可见性授权**，不承载权限角色（FR-GRP-03），所以这里没有任何角色控件。
 * 组的删除影响面在 `GroupsPage` 里处理（`409 GROUP_IN_USE`）。
 */

const MEMBER_PAGE_SIZE = 20;

/** 列表搜索：本地即时回显 + 300ms 防抖后才写 URL（docs/04 §9）。 */
function useSearchField(value: string, onChange: (next: string) => void) {
  const [input, setInput] = React.useState(value);
  const [synced, setSynced] = React.useState(value);
  // 外部 URL 变化（返回/前进、分享链接）时把输入框同步过来。
  if (synced !== value) {
    setSynced(value);
    setInput(value);
  }
  const debounced = useDebounce(input, 300);
  React.useEffect(() => {
    const trimmed = debounced.trim();
    if (input.trim() !== trimmed) return;
    if (trimmed === value) return;
    onChange(trimmed);
  }, [debounced, input, value, onChange]);
  return [input, setInput] as const;
}

const groupFormSchema = z.object({
  name: z.string().trim().min(1, "请填写组名").max(128, "组名最多 128 个字符"),
  description: z.string().trim().max(500, "描述最多 500 个字符"),
  is_active: z.boolean(),
});

type GroupFormValues = z.infer<typeof groupFormSchema>;

function GroupFormDialog({
  open,
  onOpenChange,
  group,
  pending,
  errorMessage,
  onSubmit,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  group: GroupOut | null;
  pending: boolean;
  errorMessage: string | null;
  onSubmit: (values: GroupFormValues) => void;
}) {
  const defaults = React.useMemo<GroupFormValues>(
    () => ({
      name: group?.name ?? "",
      description: group?.description ?? "",
      is_active: group?.is_active ?? true,
    }),
    [group],
  );

  const form = useForm<GroupFormValues>({
    resolver: zodResolver(groupFormSchema),
    defaultValues: defaults,
  });

  React.useEffect(() => {
    if (open) form.reset(defaults);
  }, [open, defaults, form]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="group-form-dialog"
        className="max-h-[calc(100vh-2rem)] overflow-y-auto sm:max-w-md"
      >
        <DialogHeader>
          <DialogTitle>{group ? "编辑用户组" : "新建用户组"}</DialogTitle>
          <DialogDescription>
            用户组只用于可见性授权，不承载权限角色；一个用户可以属于多个组。
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form
            className="grid gap-4"
            onSubmit={form.handleSubmit((values) => onSubmit(values))}
          >
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>组名</FormLabel>
                  <FormControl>
                    <Input placeholder="如 运维组" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="description"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>描述（选填）</FormLabel>
                  <FormControl>
                    <Textarea rows={3} placeholder="这个组包含哪些人、用于哪些工具" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="is_active"
              render={({ field }) => (
                <FormItem className="flex flex-row items-center justify-between rounded-lg border p-3">
                  <div className="space-y-0.5">
                    <FormLabel>启用该用户组</FormLabel>
                    <FormDescription>停用后组内成员不再通过该组获得可见性。</FormDescription>
                  </div>
                  <FormControl>
                    <Switch
                      checked={field.value}
                      onCheckedChange={field.onChange}
                      aria-label="启用该用户组"
                    />
                  </FormControl>
                </FormItem>
              )}
            />

            {errorMessage ? (
              <p role="alert" className="text-sm text-destructive">
                {errorMessage}
              </p>
            ) : null}

            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                取消
              </Button>
              <Button type="submit" disabled={pending}>
                {pending ? "保存中…" : group ? "保存" : "创建"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}

export interface GroupManagerProps {
  groups: readonly GroupOut[];
  /** 服务端匹配到的组总数（用于提示「仅显示前 N 个」）。 */
  groupsTotal: number;
  groupsPending: boolean;
  groupsError: string | null;
  onRetryGroups: () => void;
  /** URL 上的组列表搜索词（已防抖）。 */
  groupQuery: string;
  onGroupQueryChange: (value: string) => void;
  selectedGroupId: number | null;
  onSelectGroup: (groupId: number) => void;
  /** 启用 / 停用（由页面做二次确认后调用）。 */
  onToggleGroupActive: (group: GroupOut) => void;
  /** 删除（由页面负责影响面确认，FR-GRP-04）。 */
  onDeleteGroup: (group: GroupOut) => void;
  /** URL 上的成员搜索词（已防抖）。 */
  memberQuery: string;
  onMemberQueryChange: (value: string) => void;
  memberPage: number;
  onMemberPageChange: (page: number) => void;
  /** 组的成员数会变，成员增删后需要刷新组列表。 */
  onGroupsChanged: () => void;
}

export function GroupManager({
  groups,
  groupsTotal,
  groupsPending,
  groupsError,
  onRetryGroups,
  groupQuery,
  onGroupQueryChange,
  selectedGroupId,
  onSelectGroup,
  onToggleGroupActive,
  onDeleteGroup,
  memberQuery,
  onMemberQueryChange,
  memberPage,
  onMemberPageChange,
  onGroupsChanged,
}: GroupManagerProps) {
  const queryClient = useQueryClient();
  const [groupSearchInput, setGroupSearchInput] = useSearchField(groupQuery, onGroupQueryChange);
  const [memberSearchInput, setMemberSearchInput] = useSearchField(
    memberQuery,
    onMemberQueryChange,
  );

  const [formOpen, setFormOpen] = React.useState(false);
  const [formGroup, setFormGroup] = React.useState<GroupOut | null>(null);
  const [formError, setFormError] = React.useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = React.useState(false);
  const [removeTarget, setRemoveTarget] = React.useState<GroupMemberOut | null>(null);

  const selectedGroup = groups.find((group) => group.id === selectedGroupId) ?? null;
  const effectiveGroupId = selectedGroupId;

  const membersQuery = useQuery({
    queryKey: groupMembersQueryKey(effectiveGroupId ?? 0, memberQuery),
    queryFn: ({ signal }) =>
      fetchGroupMembers(
        effectiveGroupId ?? 0,
        { q: memberQuery || undefined, page: memberPage, page_size: MEMBER_PAGE_SIZE },
        signal,
      ),
    enabled: effectiveGroupId !== null,
    placeholderData: (previous) => previous,
  });

  const members = membersQuery.data?.items ?? [];
  const membersTotal = membersQuery.data?.total ?? 0;
  const memberPages = membersQuery.data?.pages ?? 0;

  const formMutation = useMutation({
    mutationFn: (values: GroupFormValues) => {
      const payload = {
        name: values.name.trim(),
        description: values.description.trim() ? values.description.trim() : null,
        is_active: values.is_active,
      };
      return formGroup ? updateGroup(formGroup.id, payload) : createGroup(payload);
    },
    onSuccess: (saved) => {
      toast.success(formGroup ? "已保存用户组" : "已创建用户组");
      onGroupsChanged();
      setFormOpen(false);
      setFormGroup(null);
      if (!formGroup) onSelectGroup(saved.id);
    },
    onError: (error) => {
      // 组名重复时后端返回 409 DUPLICATE_ENTRY（`details.field = "name"`），
      // 消息本身已说明是哪个组名，直接展示（不发明新的错误码）。
      setFormError(getErrorMessage(error));
    },
  });

  const addMembersMutation = useMutation({
    mutationFn: (userIds: number[]) => {
      if (effectiveGroupId === null) throw new Error("未选择用户组");
      return addGroupMembers(effectiveGroupId, { user_ids: userIds });
    },
    onSuccess: (result) => {
      const parts = [`已添加 ${result.added} 位成员`];
      if (result.already_members > 0) parts.push(`${result.already_members} 位已在组内`);
      if (result.not_found.length > 0) parts.push(`${result.not_found.length} 位用户不存在`);
      toast.success(parts.join(" · "));
      setPickerOpen(false);
      void queryClient.invalidateQueries({ queryKey: ["admin", "group-members"] });
      onGroupsChanged();
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  });

  const removeMemberMutation = useMutation({
    mutationFn: (member: GroupMemberOut) => {
      if (effectiveGroupId === null) throw new Error("未选择用户组");
      return removeGroupMember(effectiveGroupId, member.user_id);
    },
    onSuccess: () => {
      toast.success("已移出用户组");
      setRemoveTarget(null);
      void queryClient.invalidateQueries({ queryKey: ["admin", "group-members"] });
      onGroupsChanged();
    },
    onError: (error) => {
      toast.error(getErrorMessage(error));
      setRemoveTarget(null);
    },
  });

  const openCreate = () => {
    setFormGroup(null);
    setFormError(null);
    setFormOpen(true);
  };

  const openEdit = (group: GroupOut) => {
    setFormGroup(group);
    setFormError(null);
    setFormOpen(true);
  };

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)]">
      {/* ---------------- 左：组列表 ---------------- */}
      <section aria-label="用户组列表" className="flex min-w-0 flex-col gap-3">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-base font-semibold">用户组</h2>
          <Button
            type="button"
            size="sm"
            data-testid="group-create-button"
            onClick={openCreate}
          >
            <Plus aria-hidden="true" className="size-4" />
            新建用户组
          </Button>
        </div>

        <div className="relative">
          <Search
            aria-hidden="true"
            className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            value={groupSearchInput}
            onChange={(event) => setGroupSearchInput(event.target.value)}
            placeholder="搜索用户组"
            aria-label="搜索用户组"
            className="pl-8"
          />
        </div>

        {groupsPending ? (
          <PageSkeleton variant="list" count={6} />
        ) : groupsError ? (
          <ErrorState message={groupsError} onRetry={onRetryGroups} />
        ) : groups.length === 0 ? (
          <EmptyState
            icon={UsersRound}
            title={groupQuery ? "没有匹配的用户组" : "还没有用户组"}
            description={
              groupQuery ? "换个关键词再试。" : "创建用户组后，可在工具授权里按组放行可见性。"
            }
            action={
              groupQuery ? undefined : (
                <Button type="button" variant="outline" onClick={openCreate}>
                  新建用户组
                </Button>
              )
            }
          />
        ) : (
          <ul data-testid="group-list" className="flex flex-col gap-1">
            {groups.map((group) => {
              const active = group.id === selectedGroupId;
              return (
                // 行本身是 `<li>`，选中是内部的一个 `<button>` —— 行内还要放
                // 编辑/启用/删除三个按钮，把按钮嵌进按钮是非法 HTML（docs/04 §8.1）。
                <li
                  key={group.id}
                  data-testid="group-row"
                  data-group-id={group.id}
                  className={cn(
                    "flex items-start gap-1 rounded-lg border px-2 py-1.5 transition-colors",
                    active
                      ? "border-primary bg-accent/60 ring-1 ring-primary/40"
                      : "bg-card hover:bg-accent/40",
                  )}
                >
                  <button
                    type="button"
                    aria-current={active ? "true" : undefined}
                    onClick={() => onSelectGroup(group.id)}
                    className="flex min-w-0 flex-1 flex-col gap-1 rounded-md px-1 py-0.5 text-left focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                  >
                    <span className="flex items-center gap-2">
                      <span className="min-w-0 flex-1 truncate font-medium">{group.name}</span>
                      {group.is_active ? null : <Badge variant="secondary">已停用</Badge>}
                    </span>
                    <span className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                      <span>成员 {group.member_count}</span>
                      {group.acl_reference_count > 0 ? (
                        <Badge variant="warning">被 {group.acl_reference_count} 个工具引用</Badge>
                      ) : null}
                    </span>
                  </button>

                  <div className="flex shrink-0 items-center">
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label={`编辑用户组 ${group.name}`}
                      onClick={() => openEdit(group)}
                    >
                      <Pencil aria-hidden="true" className="size-4" />
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label={`${group.is_active ? "停用" : "启用"}用户组 ${group.name}`}
                      onClick={() => onToggleGroupActive(group)}
                    >
                      <Power aria-hidden="true" className="size-4" />
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label={`删除用户组 ${group.name}`}
                      onClick={() => onDeleteGroup(group)}
                    >
                      <Trash2 aria-hidden="true" className="size-4" />
                    </Button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}

        {groups.length > 0 && groupsTotal > groups.length ? (
          <p className="text-xs text-muted-foreground">
            共 {groupsTotal} 个用户组，仅显示前 {groups.length} 个，请用搜索缩小范围。
          </p>
        ) : null}
      </section>

      {/* ---------------- 右：成员管理 ---------------- */}
      <section aria-label="成员管理" className="flex min-w-0 flex-col gap-3">
        {effectiveGroupId === null ? (
          <EmptyState
            icon={UsersRound}
            title="未选择用户组"
            description="从左侧选择一个用户组，查看并管理它的成员。"
          />
        ) : (
          <>
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0">
                <h2 className="flex items-center gap-2 text-base font-semibold">
                  <span className="truncate">{selectedGroup?.name ?? "用户组"}</span>
                  {selectedGroup && !selectedGroup.is_active ? (
                    <Badge variant="secondary">已停用</Badge>
                  ) : null}
                </h2>
                <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
                  {selectedGroup?.description ?? "（无描述）"}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  成员 {selectedGroup?.member_count ?? membersTotal}
                  {selectedGroup && selectedGroup.acl_reference_count > 0
                    ? ` · 被 ${selectedGroup.acl_reference_count} 个工具的授权引用`
                    : ""}
                </p>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <div className="relative min-w-[12rem] flex-1 sm:max-w-xs">
                <Search
                  aria-hidden="true"
                  className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
                />
                <Input
                  value={memberSearchInput}
                  onChange={(event) => setMemberSearchInput(event.target.value)}
                  placeholder="搜索组内成员"
                  aria-label="搜索组内成员"
                  className="pl-8"
                />
              </div>
              <Button
                type="button"
                size="sm"
                data-testid="member-add-button"
                onClick={() => setPickerOpen(true)}
              >
                <UserPlus aria-hidden="true" className="size-4" />
                添加成员
              </Button>
            </div>

            {membersQuery.isPending ? (
              <PageSkeleton variant="list" />
            ) : membersQuery.isError ? (
              <ErrorState
                message={getErrorMessage(membersQuery.error)}
                onRetry={() => void membersQuery.refetch()}
              />
            ) : members.length === 0 ? (
              <EmptyState
                icon={UsersRound}
                title={memberQuery ? "没有匹配的成员" : "该组还没有成员"}
                description={
                  memberQuery
                    ? "换个关键词再试。"
                    : "添加成员后，他们即可通过这个组获得被授权工具的可见性。"
                }
              />
            ) : (
              <Table data-testid="member-table">
                <TableCaption className="sr-only">用户组成员</TableCaption>
                <TableHeader>
                  <TableRow>
                    <TableHead>成员</TableHead>
                    <TableHead>用户名</TableHead>
                    <TableHead>状态</TableHead>
                    <TableHead>加入时间</TableHead>
                    <TableHead>加入人</TableHead>
                    <TableHead className="text-right">操作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {members.map((member) => {
                    const displayName = member.display_name || member.username;
                    return (
                      <TableRow
                        key={member.user_id}
                        data-testid="member-row"
                        data-user-id={member.user_id}
                      >
                        <TableCell>
                          <div className="flex items-center gap-2">
                            <span
                              aria-hidden="true"
                              className={cn(
                                "grid size-7 shrink-0 place-items-center rounded-full text-xs font-semibold text-foreground",
                                avatarToneClass(displayName),
                              )}
                            >
                              {initials(displayName)}
                            </span>
                            <span className="max-w-[12rem] truncate font-medium">
                              {displayName}
                            </span>
                          </div>
                        </TableCell>
                        <TableCell className="font-mono text-xs">{member.username}</TableCell>
                        <TableCell>
                          <UserStatusBadge status={member.status} />
                        </TableCell>
                        <TableCell className="text-xs whitespace-nowrap text-muted-foreground">
                          {member.added_at ? (
                            <span title={formatDateTime(member.added_at)}>
                              {formatDateTime(member.added_at)}
                            </span>
                          ) : (
                            "—"
                          )}
                        </TableCell>
                        <TableCell className="text-xs">{member.added_by_name ?? "—"}</TableCell>
                        <TableCell className="text-right">
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            aria-label={`移除成员 ${displayName}`}
                            onClick={() => setRemoveTarget(member)}
                          >
                            <UserMinus aria-hidden="true" className="size-4" />
                            移除
                          </Button>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            )}

            {membersTotal > MEMBER_PAGE_SIZE ? (
              <Pagination
                page={membersQuery.data?.page ?? memberPage}
                pages={memberPages}
                onPageChange={onMemberPageChange}
              />
            ) : null}
          </>
        )}
      </section>

      <GroupFormDialog
        open={formOpen}
        onOpenChange={(open) => {
          setFormOpen(open);
          if (!open) {
            setFormGroup(null);
            setFormError(null);
          }
        }}
        group={formGroup}
        pending={formMutation.isPending}
        errorMessage={formError}
        onSubmit={(values) => {
          setFormError(null);
          formMutation.mutate(values);
        }}
      />

      <MemberPickerDialog
        key={`${effectiveGroupId ?? 0}-${pickerOpen ? "open" : "closed"}`}
        open={pickerOpen && effectiveGroupId !== null}
        onOpenChange={setPickerOpen}
        existingUserIds={members.map((member) => member.user_id)}
        pending={addMembersMutation.isPending}
        onConfirm={(userIds) => addMembersMutation.mutate(userIds)}
      />

      <ConfirmDialog
        open={removeTarget !== null}
        onOpenChange={(open) => {
          if (!open) setRemoveTarget(null);
        }}
        title={`将「${removeTarget?.display_name ?? removeTarget?.username ?? ""}」移出用户组？`}
        description="移出后该用户立即失去通过这个组获得的工具可见性（无需重新登录，每次请求实时判定）。"
        confirmLabel="确认移出"
        pending={removeMemberMutation.isPending}
        onConfirm={() => {
          if (removeTarget) removeMemberMutation.mutate(removeTarget);
        }}
      />
    </div>
  );
}

export default GroupManager;
