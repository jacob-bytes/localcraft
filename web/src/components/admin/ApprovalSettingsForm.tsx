import * as React from "react";

import type { SettingItem, SettingListResponse, SettingWarning } from "@/api/types";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { AlertTriangle } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * 审批分组设置表单（docs/04 §6.19 审批组）。
 *
 * 三条铁律：
 *  1. 控件类型由服务端元数据（`value_type` / `options` / `min` / `max`）驱动，
 *     前端不为某个 key 硬编码控件（docs/03 §3.13）。
 *  2. 只有改动过的项才会进 `PUT` 的 `items`，保存按钮显示「有 N 项未保存」。
 *  3. `approval.mode` 切到 `auto_approve_all` 必须先弹 `AlertDialog` 二次确认
 *     （FR-APPR-02）—— 由页面负责弹窗，本组件只负责发出「请求切换」。
 *
 * 其余设置分组（版本与上传 / 配额 / 安全 / 门户 / 统计 / 高级）属于 M3，
 * 本组件不渲染，只在卡片底部留一句说明。
 */

export interface ApprovalSettingsFormProps {
  items: readonly SettingItem[];
  values: Readonly<Record<string, unknown>>;
  /** 已保存的值快照，用于逐行标记「已修改」。 */
  initialValues: Readonly<Record<string, unknown>>;
  changedKeys: readonly string[];
  saving: boolean;
  invalidKeys: readonly string[];
  /** 切换审批模式：页面据此弹出二次确认；回调在用户确认后才执行。 */
  onRequestModeChange: (nextMode: string, save: () => void) => void;
  onChange: (key: string, value: unknown) => void;
  onSave: () => void;
}

export function ApprovalSettingsForm({
  items,
  values,
  initialValues,
  changedKeys,
  saving,
  invalidKeys,
  onRequestModeChange,
  onChange,
  onSave,
}: ApprovalSettingsFormProps) {
  const changed = React.useMemo(() => new Set(changedKeys), [changedKeys]);
  const invalid = React.useMemo(() => new Set(invalidKeys), [invalidKeys]);
  const dirtyCount = changedKeys.length;

  return (
    <section
      aria-labelledby="approval-settings-title"
      className="rounded-xl border bg-card"
    >
      <header className="border-b p-4">
        <h2 id="approval-settings-title" className="text-base font-semibold">
          审批
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          控制提交是否需要审批、免审白名单是否生效，以及新版本是否重新走审批。
        </p>
      </header>

      <div className="divide-y">
        {items.map((item) => (
          <SettingRow
            key={item.key}
            item={item}
            value={values[item.key]}
            changed={values[item.key] !== initialValues[item.key] || changed.has(item.key)}
            invalid={invalid.has(item.key)}
            onChange={onChange}
            onRequestModeChange={onRequestModeChange}
          />
        ))}
        {items.length === 0 ? (
          <p className="p-4 text-sm text-muted-foreground">
            服务端没有下发审批分组设置项，请检查 `GET /admin/settings`。
          </p>
        ) : null}
      </div>

      <div className="space-y-2 border-t p-4">
        <p className="text-xs text-muted-foreground">
          其余设置分组（版本与上传 / 配额 / 安全 / 门户 / 统计 / 高级）属于 M3，本版本暂未提供。
        </p>
        <div className="flex items-center gap-3">
          <button
            type="button"
            data-testid="settings-save"
            disabled={dirtyCount === 0 || saving}
            onClick={onSave}
            className={cn(
              "inline-flex h-9 items-center gap-2 rounded-md px-4 text-sm font-medium transition-colors",
              "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:outline-none",
              "disabled:pointer-events-none disabled:opacity-50",
              dirtyCount > 0
                ? "bg-primary text-primary-foreground hover:bg-primary/90"
                : "bg-secondary text-secondary-foreground",
            )}
          >
            {saving ? "保存中…" : dirtyCount > 0 ? `保存（有 ${dirtyCount} 项未保存）` : "保存"}
          </button>
          <span className="text-xs text-muted-foreground" role="status" aria-live="polite">
            {dirtyCount > 0 ? `有 ${dirtyCount} 项未保存` : "没有未保存的修改"}
          </span>
        </div>
      </div>
    </section>
  );
}

function SettingRow({
  item,
  value,
  changed,
  invalid,
  onChange,
  onRequestModeChange,
}: {
  item: SettingItem;
  value: unknown;
  changed: boolean;
  invalid: boolean;
  onChange: (key: string, value: unknown) => void;
  onRequestModeChange: (nextMode: string, save: () => void) => void;
}) {
  const label = item.description ?? item.key;
  const controlId = `setting-${item.key.replace(/\./g, "-")}`;

  return (
    <div
      className={cn(
        "flex flex-col gap-2 p-4 sm:flex-row sm:items-start sm:justify-between",
        changed && "bg-accent/30",
        invalid && "bg-destructive/10 ring-1 ring-destructive/40 ring-inset",
      )}
      data-invalid={invalid ? "true" : undefined}
    >
      <div className="space-y-1 sm:max-w-[55%]">
        <label className="flex items-center gap-2 text-sm font-medium" htmlFor={controlId}>
          {label}
          {changed ? (
            <span className="text-xs text-muted-foreground">（已修改）</span>
          ) : null}
        </label>
        <p className="font-mono text-[11px] text-muted-foreground">{item.key}</p>
        {invalid ? (
          <p role="alert" className="text-xs text-destructive">
            该设置项的值不合法，服务端已整批回滚，请修正后重试。
          </p>
        ) : null}
      </div>

      <div className="sm:min-w-[280px]">
        <SettingControl
          item={item}
          controlId={controlId}
          value={value}
          onChange={onChange}
          onRequestModeChange={onRequestModeChange}
        />
      </div>
    </div>
  );
}

function SettingControl({
  item,
  controlId,
  value,
  onChange,
  onRequestModeChange,
}: {
  item: SettingItem;
  controlId: string;
  value: unknown;
  onChange: (key: string, value: unknown) => void;
  onRequestModeChange: (nextMode: string, save: () => void) => void;
}) {
  // 控件类型完全由服务端元数据决定（docs/03 §3.13）。
  if (item.value_type === "bool") {
    return (
      <SwitchControl
        id={controlId}
        checked={value === true}
        label={item.description ?? item.key}
        onChange={(next) => onChange(item.key, next)}
      />
    );
  }

  if (item.options && item.options.length > 0) {
    return (
      <RadioControl
        id={controlId}
        options={item.options}
        value={typeof value === "string" ? value : ""}
        testId={item.key === "approval.mode" ? "settings-approval-mode" : undefined}
        onChange={(next) => {
          // `approval.mode` 切到「全部放行」要先确认（FR-APPR-02）。
          if (item.key === "approval.mode" && next === "auto_approve_all") {
            onRequestModeChange(next, () => onChange(item.key, next));
            return;
          }
          onChange(item.key, next);
        }}
      />
    );
  }

  if (item.value_type === "int") {
    return (
      <input
        id={controlId}
        type="number"
        value={typeof value === "number" ? value : ""}
        min={item.min ?? undefined}
        max={item.max ?? undefined}
        onChange={(event) => {
          const parsed = Number.parseInt(event.target.value, 10);
          onChange(item.key, Number.isNaN(parsed) ? 0 : parsed);
        }}
        className="h-9 w-32 rounded-md border border-input bg-transparent px-3 text-sm shadow-xs focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none dark:bg-input/30"
      />
    );
  }

  return (
    <input
      id={controlId}
      type="text"
      value={typeof value === "string" ? value : ""}
      onChange={(event) => onChange(item.key, event.target.value)}
      className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none dark:bg-input/30"
    />
  );
}

/** 仅审批分组出现的枚举控件；选项文案由服务端 `options` 驱动。 */
const OPTION_LABELS: Record<string, string> = {
  require: "需审批",
  auto_approve_all: "全部放行",
};

function RadioControl({
  id,
  options,
  value,
  onChange,
  testId,
}: {
  id: string;
  options: readonly string[];
  value: string;
  onChange: (next: string) => void;
  testId?: string;
}) {
  return (
    <fieldset id={id} data-testid={testId} className="flex flex-col gap-2">
      <legend className="sr-only">审批模式</legend>
      {options.map((option) => {
        const optionId = `${id}-${option}`;
        return (
          <div key={option} className="flex items-center gap-2">
            <input
              id={optionId}
              type="radio"
              name={id}
              value={option}
              checked={value === option}
              onChange={() => onChange(option)}
              className="size-4 accent-primary focus-visible:ring-2 focus-visible:ring-ring"
            />
            <label htmlFor={optionId} className="text-sm">
              {OPTION_LABELS[option] ?? option}
            </label>
          </div>
        );
      })}
    </fieldset>
  );
}

function SwitchControl({
  id,
  checked,
  label,
  onChange,
}: {
  id: string;
  checked: boolean;
  label: string;
  onChange: (next: boolean) => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        id={id}
        role="switch"
        aria-checked={checked}
        aria-label={label}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors",
          "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:outline-none",
          checked ? "bg-primary" : "bg-input",
        )}
      >
        <span
          aria-hidden="true"
          className={cn(
            "block size-4 rounded-full bg-background shadow transition-transform",
            checked ? "translate-x-4" : "translate-x-0.5",
          )}
        />
      </button>
      <span className="text-sm text-muted-foreground">{checked ? "已开启" : "已关闭"}</span>
    </div>
  );
}

/** 保存响应里的 `warnings[]`（如「当前有 5 个待审条目」）由页面用 AlertDialog 展示。 */
export function SettingWarningsAlert({ warnings }: { warnings: readonly SettingWarning[] }) {
  if (warnings.length === 0) return null;
  return (
    <Alert variant="destructive">
      <AlertTriangle aria-hidden="true" />
      <AlertTitle>设置已保存，但请注意</AlertTitle>
      <AlertDescription>
        <ul className="list-disc space-y-1 pl-4">
          {warnings.map((warning) => (
            <li key={warning.code}>
              {warning.message}
              {warning.pending_count !== null ? `（待审 ${warning.pending_count} 条）` : ""}
            </li>
          ))}
        </ul>
      </AlertDescription>
    </Alert>
  );
}

/** 供页面判断「是否有任何改动」的纯函数，避免两处实现漂移。 */
export function hasChanges(
  items: readonly SettingItem[],
  values: Readonly<Record<string, unknown>>,
  initialValues: Readonly<Record<string, unknown>>,
): boolean {
  return items.some((item) => values[item.key] !== initialValues[item.key]);
}

/** 审批分组要渲染的三个 key（docs/03 §3.13 / backend migration 0002）。 */
export function pickApprovalItems(response: SettingListResponse): SettingItem[] {
  const keys = new Set(["approval.mode", "approval.whitelist_enabled", "approval.version_reapproval"]);
  return response.items.filter((item) => keys.has(item.key));
}
