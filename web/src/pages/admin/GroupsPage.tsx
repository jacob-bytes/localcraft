import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as React from "react";
import { Link, useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import { deleteGroup, fetchGroups, groupsQueryKey, updateGroup } from "@/api/admin";
import { getErrorMessage, isApiError, type ApiError } from "@/api/client";
import type { GroupInUseDetail, GroupOut } from "@/api/types";
import { GroupManager } from "@/components/admin/GroupManager";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/lib/utils";

/**
 * `/admin/groups` —— 用户组管理（docs/04 §6.14，FR-GRP-01~06）。
 *
 * 页面只负责 URL 状态、组列表查询，以及两条破坏性路径：
 *  - 启用/停用：直接调 `PATCH /admin/groups/{id}`。
 *  - 删除：**先展示影响面**。被 ACL 引用的组，`DELETE` 返回 `409 GROUP_IN_USE`
 *    并附 `details.tools`；这里解析出来列给管理员看，勾选确认后才带 `force=true`
 *    重发（连带清理悬空 ACL）。列表里已知 `acl_reference_count > 0` 时直接走这条
 *    路径，不再多一次无意义的普通确认。
 */

const GROUPS_KEY_PREFIX = ["admin", "groups"] as const;
const GROUP_MEMBERS_KEY_PREFIX = ["admin", "group-members"] as const;

function parsePositiveInt(raw: string | null): number | null {
  const parsed = Number.parseInt(raw ?? "", 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

/**
 * 从 `409 GROUP_IN_USE` 的 `details` 里解析影响面。
 *
 * 注意：后端 `GroupInUseDetail.tools` 实际只给 `{id, name}`（见
 * `backend/app/services/group_service.py`），**没有 slug**，所以工具名无法在这里
 * 直接变成详情页链接；`slug` 存在时才渲染 `Link`（types.ts 已把它标为可选）。
 */
function parseGroupInUse(error: ApiError, fallbackId: number): GroupInUseDetail {
  const details = error.details;
  const rawTools = details?.["tools"];
  const tools: GroupInUseDetail["tools"] = Array.isArray(rawTools)
    ? rawTools.flatMap((entry) => {
        if (!entry || typeof entry !== "object") return [];
        const record = entry as Record<string, unknown>;
        const id = typeof record["id"] === "number" ? record["id"] : undefined;
        const slug = typeof record["slug"] === "string" ? record["slug"] : undefined;
        const name = typeof record["name"] === "string" ? record["name"] : undefined;
        return [{ id, slug, name }];
      })
    : [];

  const groupId = details?.["group_id"];
  const groupName = details?.["group_name"];
  const toolCount = details?.["tool_count"];

  return {
    group_id: typeof groupId === "number" ? groupId : fallbackId,
    group_name: typeof groupName === "string" ? groupName : "",
    tool_count: typeof toolCount === "number" ? toolCount : tools.length,
    tools,
  };
}

export default function GroupsPage() {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();

  const groupQuery = searchParams.get("q") ?? "";
  const selectedGroupId = parsePositiveInt(searchParams.get("group"));
  const memberQuery = searchParams.get("member_q") ?? "";
  const memberPage = parsePositiveInt(searchParams.get("member_page")) ?? 1;

  const [deleteTarget, setDeleteTarget] = React.useState<GroupOut | null>(null);
  const [plainConfirmOpen, setPlainConfirmOpen] = React.useState(false);
  const [inUse, setInUse] = React.useState<GroupInUseDetail | null>(null);
  const [acknowledged, setAcknowledged] = React.useState(false);

  const updateParams = React.useCallback(
    (patch: Record<string, string | null>) => {
      setSearchParams(
        (previous) => {
          const next = new URLSearchParams(previous);
          for (const [key, value] of Object.entries(patch)) {
            if (value === null) next.delete(key);
            else next.set(key, value);
          }
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const groupsQuery = useQuery({
    queryKey: groupsQueryKey(groupQuery),
    queryFn: ({ signal }) => fetchGroups({ q: groupQuery || undefined, page_size: 50 }, signal),
    placeholderData: (previous) => previous,
  });

  const groups = groupsQuery.data?.items ?? [];

  const invalidateGroups = React.useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: GROUPS_KEY_PREFIX });
    void queryClient.invalidateQueries({ queryKey: GROUP_MEMBERS_KEY_PREFIX });
  }, [queryClient]);

  const closeDeleteDialogs = () => {
    setDeleteTarget(null);
    setPlainConfirmOpen(false);
    setInUse(null);
    setAcknowledged(false);
  };

  const toggleActiveMutation = useMutation({
    mutationFn: (group: GroupOut) => updateGroup(group.id, { is_active: !group.is_active }),
    onSuccess: (saved) => {
      toast.success(saved.is_active ? "已启用用户组" : "已停用用户组，组内成员不再通过该组获得可见性");
      invalidateGroups();
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  });

  const deleteMutation = useMutation({
    mutationFn: ({ id, force }: { id: number; force: boolean }) => deleteGroup(id, force),
    onSuccess: (_data, variables) => {
      toast.success("已删除用户组");
      invalidateGroups();
      if (variables.id === selectedGroupId) updateParams({ group: null, member_page: null });
      closeDeleteDialogs();
    },
    onError: (error, variables) => {
      // FR-GRP-04：必须先展示影响面，勾选确认后才用 force=true 重发。
      if (isApiError(error) && error.is("GROUP_IN_USE")) {
        setPlainConfirmOpen(false);
        setInUse(parseGroupInUse(error, variables.id));
        return;
      }
      toast.error(getErrorMessage(error));
      closeDeleteDialogs();
    },
  });

  const forceDeleteMutation = useMutation({
    mutationFn: (id: number) => deleteGroup(id, true),
    onSuccess: (_data, id) => {
      toast.success("已删除用户组，并清理了引用它的授权");
      invalidateGroups();
      if (id === selectedGroupId) updateParams({ group: null, member_page: null });
      closeDeleteDialogs();
    },
    onError: (error) => {
      toast.error(getErrorMessage(error));
      closeDeleteDialogs();
    },
  });

  const handleDeleteRequest = (group: GroupOut) => {
    setDeleteTarget(group);
    setAcknowledged(false);
    setInUse(null);
    if (group.acl_reference_count > 0) {
      // 已知被引用：直接拿服务端的影响面明细（它才是权威）。
      setPlainConfirmOpen(false);
      deleteMutation.mutate({ id: group.id, force: false });
    } else {
      setPlainConfirmOpen(true);
    }
  };

  const handleSelectGroup = React.useCallback(
    (groupId: number) => updateParams({ group: String(groupId), member_q: null, member_page: null }),
    [updateParams],
  );

  const handleGroupQueryChange = React.useCallback(
    (value: string) => updateParams({ q: value || null }),
    [updateParams],
  );

  const handleMemberQueryChange = React.useCallback(
    (value: string) => updateParams({ member_q: value || null, member_page: null }),
    [updateParams],
  );

  const handleMemberPageChange = React.useCallback(
    (next: number) => updateParams({ member_page: next > 1 ? String(next) : null }),
    [updateParams],
  );

  const tools = inUse?.tools ?? [];
  const deletePending = deleteMutation.isPending || forceDeleteMutation.isPending;

  return (
    <div data-testid="groups-page" className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">用户组管理</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          用户组只用于可见性授权，不承载权限角色；成员变更在下次请求立即生效，无需重新登录。
        </p>
      </div>

      <GroupManager
        groups={groups}
        groupsTotal={groupsQuery.data?.total ?? 0}
        groupsPending={groupsQuery.isPending}
        groupsError={groupsQuery.isError ? getErrorMessage(groupsQuery.error) : null}
        onRetryGroups={() => void groupsQuery.refetch()}
        groupQuery={groupQuery}
        onGroupQueryChange={handleGroupQueryChange}
        selectedGroupId={selectedGroupId}
        onSelectGroup={handleSelectGroup}
        onToggleGroupActive={(group) => toggleActiveMutation.mutate(group)}
        onDeleteGroup={handleDeleteRequest}
        memberQuery={memberQuery}
        onMemberQueryChange={handleMemberQueryChange}
        memberPage={memberPage}
        onMemberPageChange={handleMemberPageChange}
        onGroupsChanged={invalidateGroups}
      />

      {/* 无引用时的普通二次确认。 */}
      <AlertDialog
        open={plainConfirmOpen && deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) {
            setPlainConfirmOpen(false);
            setDeleteTarget(null);
          }
        }}
      >
        <AlertDialogContent data-testid="group-delete-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>
              删除用户组「{deleteTarget?.name ?? ""}」？
            </AlertDialogTitle>
            <AlertDialogDescription>
              删除后组内成员不再通过该组获得工具可见性，操作不可撤销。若该组已被工具授权引用，
              下一步会列出受影响的工具。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deletePending}>取消</AlertDialogCancel>
            <AlertDialogAction
              data-testid="group-delete-confirm"
              disabled={deletePending}
              className={cn(
                "bg-destructive text-destructive-foreground hover:bg-destructive/90",
              )}
              onClick={(event) => {
                event.preventDefault();
                if (deleteTarget) deleteMutation.mutate({ id: deleteTarget.id, force: false });
              }}
            >
              {deletePending ? "删除中…" : "确认删除"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 影响面确认：列出引用该组的工具，勾选后才允许强制删除（验收 #8）。 */}
      <AlertDialog
        open={inUse !== null}
        onOpenChange={(open) => {
          if (!open) closeDeleteDialogs();
        }}
      >
        <AlertDialogContent data-testid="group-in-use-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>
              该用户组仍被 {inUse?.tool_count ?? 0} 个工具的授权引用
            </AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-3 text-sm text-muted-foreground">
                <p>
                  删除用户组「{inUse?.group_name || deleteTarget?.name || ""}」后，
                  下面这些工具针对该组的可见性授权会一并失效，相关用户将立即无法看到这些工具。
                </p>

                {tools.length > 0 ? (
                  <ul className="max-h-48 divide-y overflow-y-auto rounded-md border">
                    {tools.map((tool, index) => (
                      <li key={tool.id ?? `${tool.name ?? "tool"}-${index}`} className="px-3 py-1.5">
                        {tool.slug ? (
                          <Link
                            to={`/tools/${tool.slug}`}
                            className="text-primary underline-offset-4 hover:underline"
                          >
                            {tool.name ?? tool.slug}
                          </Link>
                        ) : (
                          <span className="text-foreground">
                            {tool.name ?? `工具 #${tool.id ?? "?"}`}
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p>服务端未返回具体工具清单，请谨慎确认后再继续。</p>
                )}

                <label className="flex items-start gap-2 text-foreground">
                  <Checkbox
                    data-testid="group-in-use-ack"
                    checked={acknowledged}
                    aria-label="我已了解，删除后这些授权将失效"
                    onCheckedChange={(value) => setAcknowledged(value === true)}
                  />
                  <span>我已了解，删除后这些授权将失效</span>
                </label>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={forceDeleteMutation.isPending}>取消</AlertDialogCancel>
            <AlertDialogAction
              disabled={!acknowledged || forceDeleteMutation.isPending}
              className={cn(
                "bg-destructive text-destructive-foreground hover:bg-destructive/90",
              )}
              onClick={(event) => {
                event.preventDefault();
                if (deleteTarget) forceDeleteMutation.mutate(deleteTarget.id);
              }}
            >
              {forceDeleteMutation.isPending ? "删除中…" : "仍要删除"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
