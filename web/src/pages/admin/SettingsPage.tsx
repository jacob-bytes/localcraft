import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as React from "react";
import { toast } from "sonner";

import {
  fetchSettings,
  settingsQueryKey,
  updateSettings,
} from "@/api/admin";
import { ApiError, getErrorMessage } from "@/api/client";
import type { SettingItem, SettingWarning } from "@/api/types";
import {
  ApprovalSettingsForm,
  hasChanges,
  pickApprovalItems,
} from "@/components/admin/ApprovalSettingsForm";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { ErrorState } from "@/components/common/ErrorState";
import { PageSkeleton } from "@/components/common/PageSkeleton";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

/**
 * `/admin/settings` —— 系统设置（docs/04 §6.19）。M2 只做「审批」分组，
 * 其余分组是 M3（CONTRACT §6.1：`/admin/settings` 只有 GET/PUT 两个接口，
 * 但设置项分组会随里程碑扩展）。
 *
 * 表单行为（docs/04 §6.19）：单一保存按钮置底、显示未保存项数、
 * 保存后渲染响应里的 `warnings[]`、`SETTING_INVALID` 时高亮出错的 key。
 */
export default function SettingsPage() {
  const queryClient = useQueryClient();

  const settingsQuery = useQuery({
    queryKey: settingsQueryKey,
    queryFn: ({ signal }) => fetchSettings(signal),
  });

  const items = React.useMemo<SettingItem[]>(
    () => (settingsQuery.data ? pickApprovalItems(settingsQuery.data) : []),
    [settingsQuery.data],
  );

  const [values, setValues] = React.useState<Record<string, unknown>>({});
  const [initialValues, setInitialValues] = React.useState<Record<string, unknown>>({});
  const [invalidKeys, setInvalidKeys] = React.useState<string[]>([]);
  const [warnings, setWarnings] = React.useState<SettingWarning[]>([]);
  const [warningsOpen, setWarningsOpen] = React.useState(false);
  const [modeDialogOpen, setModeDialogOpen] = React.useState(false);
  const [syncedData, setSyncedData] = React.useState(settingsQuery.data);
  const pendingModeSave = React.useRef<(() => void) | null>(null);

  /**
   * 服务端数据到达（首次加载或保存后失效重取）时重置本地快照。
   * 用「渲染期同步」而不是 effect：这是 React 官方推荐的「props 变化即重置
   * state」写法，也避免 react-hooks 的 set-state-in-effect 规则。
   */
  if (settingsQuery.data && settingsQuery.data !== syncedData) {
    const next: Record<string, unknown> = {};
    for (const item of pickApprovalItems(settingsQuery.data)) {
      next[item.key] = item.value;
    }
    setSyncedData(settingsQuery.data);
    setValues(next);
    setInitialValues(next);
    setInvalidKeys([]);
  }

  const changedKeys = React.useMemo(
    () =>
      items.filter((item) => values[item.key] !== initialValues[item.key]).map((item) => item.key),
    [items, values, initialValues],
  );

  const saveMutation = useMutation({
    mutationFn: (payload: Array<{ key: string; value: unknown }>) =>
      updateSettings({ items: payload }),
    onSuccess: (response) => {
      toast.success("设置已保存");
      setInvalidKeys([]);
      // 重新拉取，让「已保存基线」与其它分组（M3 之后）保持一致。
      void queryClient.invalidateQueries({ queryKey: settingsQueryKey });
      // 响应里带 warnings（例如「当前有 5 个待审条目，切换为全部放行不会自动处理它们」）时展示。
      if (response.warnings.length > 0) {
        setWarnings(response.warnings);
        setWarningsOpen(true);
      }
    },
    onError: (error) => {
      if (error instanceof ApiError && error.is("SETTING_INVALID")) {
        // `details` 里带出错的 key；服务端整批回滚，所以这里只高亮不部分保存。
        const details = error.details;
        const rawKey = typeof details?.key === "string" ? details.key : details?.field;
        const key = typeof rawKey === "string" && rawKey.length > 0 ? rawKey : null;
        setInvalidKeys(key ? [key] : changedKeys);
        toast.error(key ? `设置项 ${key} 的值不合法，已整批回滚` : "设置有误，已整批回滚");
        return;
      }
      toast.error(getErrorMessage(error));
    },
  });

  function handleSave() {
    if (changedKeys.length === 0) return;
    saveMutation.mutate(
      changedKeys.map((key) => ({ key, value: values[key] })),
    );
  }

  const dirty = hasChanges(items, values, initialValues);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-xl font-semibold tracking-tight">系统设置</h1>
        <p className="text-xs text-muted-foreground">
          M2 仅提供「审批」分组；其余分组随 M3 开放
        </p>
      </div>

      {settingsQuery.isPending ? (
        <PageSkeleton variant="list" count={3} />
      ) : settingsQuery.isError ? (
        <ErrorState
          message={getErrorMessage(settingsQuery.error)}
          onRetry={() => void settingsQuery.refetch()}
        />
      ) : (
        <>
          <ApprovalSettingsForm
            items={items}
            values={values}
            initialValues={initialValues}
            changedKeys={changedKeys}
            saving={saveMutation.isPending}
            invalidKeys={invalidKeys}
            onRequestModeChange={(_nextMode, save) => {
              // FR-APPR-02：切到「全部放行」前必须二次确认并说明影响范围。
              pendingModeSave.current = save;
              setModeDialogOpen(true);
            }}
            onChange={(key, value) => {
              setValues((current) => ({ ...current, [key]: value }));
              setInvalidKeys((current) => current.filter((item) => item !== key));
            }}
            onSave={handleSave}
          />

          {/* 提交按钮置底提示：即使卡片不长也保持一致的「有 N 项未保存」文案。 */}
          <p className="text-xs text-muted-foreground" role="status" aria-live="polite">
            {dirty ? `有 ${changedKeys.length} 项未保存` : "设置与服务端一致"}
          </p>
        </>
      )}

      {/* 切换为 auto_approve_all 的二次确认（FR-APPR-02）。 */}
      <ConfirmDialog
        open={modeDialogOpen}
        onOpenChange={(open) => {
          setModeDialogOpen(open);
          if (!open) pendingModeSave.current = null;
        }}
        destructive={false}
        title="切换为「全部放行」？"
        description={
          <div className="space-y-2">
            <p>切换后新提交的工具与版本将立即发布，不再进入审批队列。</p>
            <p>
              已处于 <span className="font-mono">pending</span>{" "}
              的条目**不会**被自动放行，仍需在审批队列中手动处理或使用批量批准。
            </p>
          </div>
        }
        confirmLabel="确认切换"
        onConfirm={() => {
          pendingModeSave.current?.();
          pendingModeSave.current = null;
          setModeDialogOpen(false);
        }}
      />

      {/* 保存响应里的 warnings[]（docs/03 §3.13）。 */}
      <AlertDialog open={warningsOpen} onOpenChange={setWarningsOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>设置已保存，但请注意</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2 text-sm text-muted-foreground">
                {warnings.map((warning) => (
                  <p key={warning.code}>
                    {warning.message}
                    {warning.pending_count !== null
                      ? `（当前待审 ${warning.pending_count} 条）`
                      : ""}
                  </p>
                ))}
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogAction onClick={() => setWarningsOpen(false)}>知道了</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
