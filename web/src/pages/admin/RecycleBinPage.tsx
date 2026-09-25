import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Info, Trash2 } from "lucide-react";
import * as React from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import {
  fetchRecycleBin,
  overviewQueryKey,
  purgeAdminTool,
  restoreAdminTool,
} from "@/api/admin";
import { getErrorMessage } from "@/api/client";
import type { AdminToolItem } from "@/api/types";
import { RecycleBinTable } from "@/components/admin/RecycleBinTable";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { PageSkeleton } from "@/components/common/PageSkeleton";
import { Pagination } from "@/components/common/Pagination";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/**
 * `/admin/recycle-bin` —— 回收站（docs/04 §6.21）。
 *
 * 「还原」是一次确认；「彻底删除」要求**输入工具名**才能确认（docs/04 §7.7 的
 * 高危操作模式），并明确说明文件与记录都无法恢复。成功后失效回收站、全站工具与
 * 概览三个缓存，toast 里回报 `purged_versions` / `purged_files`。
 *
 * 列表接口返回 `AdminToolListResponse`，没有「删除人」字段 —— 见
 * `RecycleBinTable` 的说明。
 */

const recycleBinQueryKey = (page: number) => ["admin", "recycle-bin", { page }] as const;

export default function RecycleBinPage() {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const parsedPage = Number.parseInt(searchParams.get("page") ?? "1", 10);
  const page = Number.isFinite(parsedPage) && parsedPage >= 1 ? parsedPage : 1;

  const binQuery = useQuery({
    queryKey: recycleBinQueryKey(page),
    queryFn: ({ signal }) => fetchRecycleBin({ page }, signal),
  });

  const [restoreTarget, setRestoreTarget] = React.useState<AdminToolItem | null>(null);
  const [purgeTarget, setPurgeTarget] = React.useState<AdminToolItem | null>(null);
  const [purgeInput, setPurgeInput] = React.useState("");

  function invalidateAfterMutation() {
    void queryClient.invalidateQueries({ queryKey: ["admin", "recycle-bin"] });
    void queryClient.invalidateQueries({ queryKey: ["admin", "tools"] });
    void queryClient.invalidateQueries({ queryKey: overviewQueryKey });
  }

  const restoreMutation = useMutation({
    mutationFn: (toolId: number) => restoreAdminTool(toolId),
    onSuccess: (tool) => {
      toast.success(`已还原「${tool.name}」`);
      setRestoreTarget(null);
      invalidateAfterMutation();
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  });

  const purgeMutation = useMutation({
    mutationFn: (toolId: number) => purgeAdminTool(toolId),
    onSuccess: (response) => {
      toast.success(
        `已彻底删除：清除 ${response.purged_versions} 个版本、${response.purged_files} 个文件`,
      );
      setPurgeTarget(null);
      setPurgeInput("");
      invalidateAfterMutation();
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  });

  const items = binQuery.data?.items ?? [];
  const purgeConfirmed =
    purgeTarget !== null && purgeInput.trim() === purgeTarget.name.trim() && purgeTarget.name.trim() !== "";

  return (
    <div data-testid="recycle-bin-page" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-xl font-semibold tracking-tight">回收站</h1>
        <span className="text-xs text-muted-foreground">共 {binQuery.data?.total ?? 0} 个工具</span>
      </div>

      <Alert>
        <Info aria-hidden="true" />
        <AlertTitle>保留 30 天</AlertTitle>
        <AlertDescription>软删除的工具将在 30 天后自动彻底清除。</AlertDescription>
      </Alert>

      {binQuery.isPending ? (
        <PageSkeleton variant="list" count={4} />
      ) : binQuery.isError ? (
        <ErrorState
          message={getErrorMessage(binQuery.error)}
          onRetry={() => void binQuery.refetch()}
        />
      ) : items.length === 0 ? (
        <EmptyState
          icon={Trash2}
          title="回收站是空的"
          description="被软删除的工具会出现在这里，30 天内可以还原；超期后自动彻底清除。"
        />
      ) : (
        <>
          <RecycleBinTable
            items={items}
            restoringId={restoreMutation.isPending ? (restoreTarget?.id ?? null) : null}
            purgingId={purgeMutation.isPending ? (purgeTarget?.id ?? null) : null}
            onRestoreRequest={setRestoreTarget}
            onPurgeRequest={(tool) => {
              setPurgeInput("");
              setPurgeTarget(tool);
            }}
          />
          <Pagination
            page={page}
            pages={binQuery.data?.pages ?? 1}
            onPageChange={(nextPage) => {
              setSearchParams(
                (current) => {
                  const next = new URLSearchParams(current);
                  if (nextPage > 1) next.set("page", String(nextPage));
                  else next.delete("page");
                  return next;
                },
                { replace: true },
              );
            }}
          />
        </>
      )}

      {/* 还原：一次确认即可（可逆操作）。 */}
      <ConfirmDialog
        open={restoreTarget !== null}
        onOpenChange={(open) => {
          if (!open) setRestoreTarget(null);
        }}
        destructive={false}
        title={`还原「${restoreTarget?.name ?? ""}」？`}
        description="还原后工具回到删除前的状态与可见性，文件与历史版本都还在。"
        confirmLabel="确认还原"
        pending={restoreMutation.isPending}
        onConfirm={() => {
          if (restoreTarget) restoreMutation.mutate(restoreTarget.id);
        }}
      />

      {/* 彻底删除：要求输入工具名（docs/04 §6.21）。 */}
      <AlertDialog
        open={purgeTarget !== null}
        onOpenChange={(open) => {
          if (!open) {
            setPurgeTarget(null);
            setPurgeInput("");
          }
        }}
      >
        <AlertDialogContent data-testid="recycle-purge-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>彻底删除「{purgeTarget?.name ?? ""}」？</AlertDialogTitle>
            <AlertDialogDescription>
              彻底删除后文件与记录都无法恢复：所有历史版本与磁盘文件会被一并清除，工具行也不留。
              请确认这是你要的结果。
            </AlertDialogDescription>
          </AlertDialogHeader>

          <div className="space-y-2">
            <Label htmlFor="recycle-purge-confirm">请输入工具名以确认</Label>
            <Input
              id="recycle-purge-confirm"
              data-testid="recycle-purge-confirm-input"
              autoComplete="off"
              placeholder={purgeTarget?.name ?? ""}
              value={purgeInput}
              onChange={(event) => setPurgeInput(event.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              需要与工具名完全一致：<span className="font-mono">{purgeTarget?.name ?? ""}</span>
            </p>
          </div>

          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              data-testid="recycle-purge-confirm"
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              disabled={!purgeConfirmed || purgeMutation.isPending}
              onClick={(event) => {
                event.preventDefault();
                if (purgeTarget) purgeMutation.mutate(purgeTarget.id);
              }}
            >
              {purgeMutation.isPending ? "删除中…" : "彻底删除"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
