import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as React from "react";
import { toast } from "sonner";

import { fetchSettings, settingsQueryKey, updateSettings } from "@/api/admin";
import { ApiError, getErrorMessage } from "@/api/client";
import type { SettingItem, SettingWarning } from "@/api/types";
import {
  SettingsForm,
  draftEquals,
  draftToPayload,
  toDraftValue,
  validateSettingDraft,
} from "@/components/admin/SettingsForm";
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
 * `/admin/settings` —— 系统设置（docs/04 §6.19、docs/03 §3.13）。
 *
 * 页面负责「状态 + 提交 + 错误定位」，`SettingsForm` 负责「按后端元信息渲染控件」。
 *
 * 错误语义（验收 #15）：服务端**整体回滚**，所以出错时绝不重放部分写入 ——
 * 只把出错的 key 高亮、滚动到该项并给出内联错误，并明确告诉用户其余设置未被改动。
 *
 * warnings（验收 #16）：保存响应里的 `warnings[]`（例如
 * `PENDING_ITEMS_NOT_AFFECTED` + `pending_count`）用 `AlertDialog` 展示。
 */
export default function SettingsPage() {
  const queryClient = useQueryClient();

  const settingsQuery = useQuery({
    queryKey: settingsQueryKey,
    queryFn: ({ signal }) => fetchSettings(signal),
  });

  const items = React.useMemo<SettingItem[]>(() => settingsQuery.data?.items ?? [], [
    settingsQuery.data,
  ]);

  const [values, setValues] = React.useState<Record<string, unknown>>({});
  const [initialValues, setInitialValues] = React.useState<Record<string, unknown>>({});
  const [serverErrors, setServerErrors] = React.useState<Record<string, string>>({});
  const [warnings, setWarnings] = React.useState<SettingWarning[]>([]);
  const [warningsOpen, setWarningsOpen] = React.useState(false);
  const [modeDialogOpen, setModeDialogOpen] = React.useState(false);
  const [syncedData, setSyncedData] = React.useState(settingsQuery.data);
  const pendingModeApply = React.useRef<(() => void) | null>(null);

  /**
   * 服务端数据到达（首次加载或保存后失效重取）时重置本地快照。
   * 采用「渲染期同步」而不是 effect —— React 官方推荐的「props 变化即重置 state」
   * 写法，也避免 lint 对 effect 内 setState 的告警。
   */
  if (settingsQuery.data && settingsQuery.data !== syncedData) {
    const next: Record<string, unknown> = {};
    for (const item of settingsQuery.data.items) next[item.key] = toDraftValue(item);
    setSyncedData(settingsQuery.data);
    setValues(next);
    setInitialValues(next);
    setServerErrors({});
  }

  const changedKeys = React.useMemo(
    () =>
      items
        .filter((item) => !draftEquals(values[item.key], initialValues[item.key]))
        .map((item) => item.key),
    [items, values, initialValues],
  );

  /** 本地格式校验（数字 / JSON）与脏值同步展示；范围与枚举合法性仍由服务端裁定。 */
  const localErrors = React.useMemo(() => validateSettingDraft(items, values), [items, values]);

  const fieldErrors = React.useMemo<Record<string, string>>(
    () => ({ ...localErrors, ...serverErrors }),
    [localErrors, serverErrors],
  );

  const invalidKeys = React.useMemo(() => Object.keys(fieldErrors), [fieldErrors]);

  /** 滚动到出错的设置项（外层容器带 `data-setting-key`）。 */
  const revealSetting = React.useCallback((key: string) => {
    window.requestAnimationFrame(() => {
      const element = document.querySelector(`[data-setting-key="${key}"]`);
      element?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }, []);

  const saveMutation = useMutation({
    mutationFn: (payload: Array<{ key: string; value: unknown }>) =>
      updateSettings({ items: payload }),
    onSuccess: (response) => {
      toast.success("设置已保存");
      setServerErrors({});
      // 用响应里的 items 覆盖本地状态：同一批 key 的草稿与「已保存基线」都对齐。
      setValues((current) => {
        const next = { ...current };
        for (const item of response.items) next[item.key] = toDraftValue(item);
        return next;
      });
      setInitialValues((current) => {
        const next = { ...current };
        for (const item of response.items) next[item.key] = toDraftValue(item);
        return next;
      });
      // 让白名单页等其它消费者看到最新设置。
      void queryClient.invalidateQueries({ queryKey: settingsQueryKey });
      if (response.warnings.length > 0) {
        setWarnings(response.warnings);
        setWarningsOpen(true);
      }
    },
    onError: (error) => {
      const errors = resolveSettingErrors(error, items, changedKeys);
      const keys = Object.keys(errors);
      if (keys.length > 0) {
        setServerErrors(errors);
        revealSetting(keys[0] as string);
        toast.error("本次保存已整体回滚，其余设置未被改动");
        return;
      }
      toast.error(getErrorMessage(error));
    },
  });

  function handleSave() {
    if (changedKeys.length === 0) return;
    const blocking = changedKeys.filter((key) => localErrors[key] !== undefined);
    if (blocking.length > 0) {
      revealSetting(blocking[0] as string);
      toast.error("有设置项格式不正确，请修正后再保存");
      return;
    }
    const payload: Array<{ key: string; value: unknown }> = [];
    for (const key of changedKeys) {
      const item = items.find((candidate) => candidate.key === key);
      if (!item) continue;
      payload.push({ key, value: draftToPayload(item, values[key]) });
    }
    if (payload.length === 0) return;
    saveMutation.mutate(payload);
  }

  return (
    <div data-testid="settings-page" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-xl font-semibold tracking-tight">系统设置</h1>
        <p className="text-xs text-muted-foreground">
          控件类型与取值范围由服务端下发的元信息决定（docs/03 §3.13）
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
        <SettingsForm
          items={items}
          values={values}
          initialValues={initialValues}
          changedKeys={changedKeys}
          saving={saveMutation.isPending}
          invalidKeys={invalidKeys}
          fieldErrors={fieldErrors}
          onChange={(key, value) => {
            setValues((current) => ({ ...current, [key]: value }));
            // 用户改动后清掉该 key 的服务端错误，避免旧错误一直挂着。
            setServerErrors((current) => {
              if (current[key] === undefined) return current;
              const next = { ...current };
              delete next[key];
              return next;
            });
          }}
          onSave={handleSave}
          onRequestApprovalModeChange={(_nextMode, apply) => {
            // FR-APPR-02：切到「全部放行」前必须二次确认并说明影响范围。
            pendingModeApply.current = apply;
            setModeDialogOpen(true);
          }}
        />
      )}

      {/* 切换为 auto_approve_all 的二次确认（FR-APPR-02）。 */}
      <ConfirmDialog
        open={modeDialogOpen}
        onOpenChange={(open) => {
          setModeDialogOpen(open);
          if (!open) pendingModeApply.current = null;
        }}
        destructive={false}
        title="切换为「全部放行」？"
        description={
          <div className="space-y-2">
            <p>切换后新提交的工具与版本将立即发布，不再进入审批队列。</p>
            <p>
              已处于 <span className="font-mono">pending</span>{" "}
              的条目不会被自动放行，仍需在审批队列中手动处理或使用批量批准。
            </p>
          </div>
        }
        confirmLabel="确认切换"
        onConfirm={() => {
          pendingModeApply.current?.();
          pendingModeApply.current = null;
          setModeDialogOpen(false);
        }}
      />

      {/* 保存响应里的 warnings[]（docs/03 §3.13，验收 #16）。 */}
      <AlertDialog open={warningsOpen} onOpenChange={setWarningsOpen}>
        <AlertDialogContent data-testid="settings-warnings">
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

/**
 * 把 `SETTING_INVALID` / `VALIDATION_ERROR` 的 details 映射成「设置项 key → 文案」。
 *
 * - `SETTING_INVALID`（真后端）：`details.key` 是设置项 key（`details.field` 也可能出现）。
 * - `VALIDATION_ERROR`：`details.fields[]` 的 `field` 形如 `items.<idx>.value`，
 *   按**提交顺序**回指到 `changedKeys[idx]`。
 * - 映射不到具体项时，退化为高亮本次提交的全部改动项 —— 服务端整体回滚，
 *   所以绝不做「部分保存」的假象。
 */
function resolveSettingErrors(
  error: unknown,
  items: readonly SettingItem[],
  changedKeys: readonly string[],
): Record<string, string> {
  if (!(error instanceof ApiError)) return {};
  const known = new Set(items.map((item) => item.key));
  const resolved: Record<string, string> = {};

  const putDirect = (field: unknown, message: string) => {
    if (typeof field !== "string" || field.length === 0) return;
    if (field === "items" || field === "key" || field === "value") return;
    // 设置项 key 才能定位到行；其它形状交给下面的索引解析或兜底。
    if (known.has(field)) resolved[field] = message;
  };

  const details = error.details;
  if (details) {
    putDirect(details.key, error.message);
    putDirect(details.field, error.message);
    if (Array.isArray(details.fields)) {
      for (const field of details.fields) {
        if (!field || typeof field.field !== "string") continue;
        const indexed = /^items\.(\d+)(?:\.|$)/.exec(field.field);
        if (indexed) {
          const key = changedKeys[Number(indexed[1])];
          if (key) {
            resolved[key] = field.message;
            continue;
          }
        }
        putDirect(field.field, field.message);
      }
    }
  }

  if (Object.keys(resolved).length === 0 && changedKeys.length > 0) {
    for (const key of changedKeys) resolved[key] = error.message;
  }
  return resolved;
}
