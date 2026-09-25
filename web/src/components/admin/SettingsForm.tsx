import * as React from "react";

import { SETTING_KEYS } from "@/api/admin";
import type { SettingItem } from "@/api/types";
import { TagInput } from "@/components/me/TagInput";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

/**
 * 系统设置表单（docs/04 §6.19、docs/03 §3.13）。
 *
 * **核心不变式：控件完全由后端元信息驱动**（`value_type` / `options` / `min` / `max`），
 * 这里不允许出现「某个 key 一定是数字/一定是下拉」式的硬编码。唯一的两处本地知识是：
 *  1. 分组标题与分组顺序（导航文案，不是控件元信息）；
 *  2. `approval.mode === "auto_approve_all"` 的二次确认（业务规则 FR-APPR-02），
 *     以及已知枚举值的**纯展示文案**（未知取值仍按后端原样渲染）。
 *
 * 脏值语义：`values` / `initialValues` 保存的是「草稿表示」（int 用字符串、json 用
 * 文本、list 用数组），`toDraftValue` / `draftToPayload` 负责与接口值互转。
 */

/* -------------------------------------------------------------------------- */
/* 分组（本地导航文案，非控件元信息）                                          */
/* -------------------------------------------------------------------------- */

interface GroupSpec {
  id: string;
  title: string;
  hint: string;
  prefixes: readonly string[];
}

const GROUP_SPECS: readonly GroupSpec[] = [
  {
    id: "approval",
    title: "审批",
    hint: "提交是否需要审批、免审白名单是否生效，以及新版本是否重新走审批。",
    prefixes: ["approval."],
  },
  {
    id: "version-upload",
    title: "版本与上传",
    hint: "历史版本保留、上传体积与类型限制、截图与标签上限。",
    prefixes: ["version.", "upload."],
  },
  {
    id: "quota",
    title: "配额",
    hint: "单用户与平台总配额、告警阈值（0 通常表示不限，以设置项说明为准）。",
    prefixes: ["quota."],
  },
  {
    id: "security",
    title: "安全",
    hint: "令牌有效期与登录失败锁定策略。",
    prefixes: ["security."],
  },
  {
    id: "images",
    title: "图片",
    hint: "封面/截图的签名 URL 有效期（M5 起后端下发 sig= 能力 URL）。",
    prefixes: ["images."],
  },
  {
    id: "portal",
    title: "门户",
    hint: "站点名称、首页公告、匿名浏览与默认排序。",
    prefixes: ["portal."],
  },
  {
    id: "stats",
    title: "统计",
    hint: "下载明细保留与浏览次数去重窗口。",
    prefixes: ["stats."],
  },
  {
    id: "advanced",
    title: "高级",
    hint: "开放 API 文档等高级开关，改动前请确认影响面。",
    prefixes: ["api.", "advanced."],
  },
];

/** 后端新增了未登记前缀时，设置项仍然会被渲染，不会被静默丢弃。 */
const FALLBACK_GROUP: GroupSpec = {
  id: "other",
  title: "其他",
  hint: "未归入上述分组的设置项。",
  prefixes: [],
};

function groupOf(item: SettingItem): GroupSpec {
  return (
    GROUP_SPECS.find((group) => group.prefixes.some((prefix) => item.key.startsWith(prefix))) ??
    FALLBACK_GROUP
  );
}

export interface SettingGroup {
  spec: GroupSpec;
  items: SettingItem[];
}

/** 稳定顺序：按 `GROUP_SPECS` 的顺序输出非空分组，兜底分组永远在最后。 */
export function groupSettings(items: readonly SettingItem[]): SettingGroup[] {
  const grouped = new Map<string, SettingItem[]>();
  for (const item of items) {
    const spec = groupOf(item);
    const bucket = grouped.get(spec.id);
    if (bucket) bucket.push(item);
    else grouped.set(spec.id, [item]);
  }
  const ordered: SettingGroup[] = [];
  for (const spec of [...GROUP_SPECS, FALLBACK_GROUP]) {
    const bucket = grouped.get(spec.id);
    if (bucket && bucket.length > 0) ordered.push({ spec, items: bucket });
  }
  return ordered;
}

/* -------------------------------------------------------------------------- */
/* 草稿值 <-> 接口值                                                           */
/* -------------------------------------------------------------------------- */

/**
 * 后端 `value_type` 收窄为 `string`：当前枚举是 bool/int/string/json/list，
 * 但契约里 int 与 number 视为同族数字，用字符串比较可以兼容后端将来新增 `number`
 * 而不需要改这里的映射（TS 直接比较联合类型会报「无重叠」错误）。
 */
function typeOf(item: SettingItem): string {
  return item.value_type;
}

/** 接口值 → 草稿值（int 用字符串，避免清空输入框时被写成 0）。 */
export function toDraftValue(item: SettingItem): unknown {
  const type = typeOf(item);
  if (type === "bool") return item.value === true;
  if (type === "int" || type === "number") {
    return item.value === null || item.value === undefined ? "" : String(item.value);
  }
  if (type === "list") {
    return Array.isArray(item.value) ? item.value.map((entry) => String(entry)) : [];
  }
  if (type === "json") {
    if (item.value === null || item.value === undefined) return "";
    try {
      return JSON.stringify(item.value, null, 2);
    } catch {
      return "";
    }
  }
  return item.value === null || item.value === undefined ? "" : String(item.value);
}

/** 草稿值 → 提交值；调用前必须先通过 `validateSettingDraft`（数字/JSON 已合法）。 */
export function draftToPayload(item: SettingItem, draft: unknown): unknown {
  const type = typeOf(item);
  if (type === "int" || type === "number") return Number(draft);
  if (type === "json") return JSON.parse(String(draft)) as unknown;
  return draft;
}

/** 草稿相等判断（list 是数组，需要按内容比较）。 */
export function draftEquals(a: unknown, b: unknown): boolean {
  if (Array.isArray(a) || Array.isArray(b)) {
    return JSON.stringify(a ?? []) === JSON.stringify(b ?? []);
  }
  return a === b;
}

/**
 * 本地校验：只覆盖「客户端一定能判定的格式问题」，范围与选项合法性仍由服务端裁定
 * （后端没给 `min` / `max` 时这里也不编一个区间出来）。
 */
export function validateSettingDraft(
  items: readonly SettingItem[],
  values: Readonly<Record<string, unknown>>,
): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const item of items) {
    const draft = values[item.key];
    const type = typeOf(item);
    if (type === "int" || type === "number") {
      const text = String(draft ?? "").trim();
      if (text === "" || !Number.isFinite(Number(text))) {
        errors[item.key] = "请填写数字";
      }
      continue;
    }
    if (type === "json") {
      const text = String(draft ?? "").trim();
      if (text === "") {
        errors[item.key] = "请填写 JSON";
        continue;
      }
      try {
        JSON.parse(text);
      } catch {
        errors[item.key] = "JSON 格式不正确，请检查括号、引号与逗号";
      }
    }
  }
  return errors;
}

/* -------------------------------------------------------------------------- */
/* 表单                                                                        */
/* -------------------------------------------------------------------------- */

export interface SettingsFormProps {
  items: readonly SettingItem[];
  values: Readonly<Record<string, unknown>>;
  initialValues: Readonly<Record<string, unknown>>;
  changedKeys: readonly string[];
  saving: boolean;
  /** 服务端 / 本地判定为非法的 key（用于高亮与滚动定位）。 */
  invalidKeys: readonly string[];
  /** key → 内联错误文案（`data-testid="setting-error"`）。 */
  fieldErrors: Readonly<Record<string, string>>;
  onChange: (key: string, value: unknown) => void;
  onSave: () => void;
  /** 审批模式切到「全部放行」时由页面弹 `AlertDialog`；确认后才执行 `apply`。 */
  onRequestApprovalModeChange: (nextMode: string, apply: () => void) => void;
}

export function SettingsForm({
  items,
  values,
  initialValues,
  changedKeys,
  saving,
  invalidKeys,
  fieldErrors,
  onChange,
  onSave,
  onRequestApprovalModeChange,
}: SettingsFormProps) {
  const changed = React.useMemo(() => new Set(changedKeys), [changedKeys]);
  const invalid = React.useMemo(() => new Set(invalidKeys), [invalidKeys]);
  const groups = React.useMemo(() => groupSettings(items), [items]);
  const dirtyCount = changedKeys.length;

  return (
    <div className="flex flex-col gap-4 pb-2">
      {groups.length === 0 ? (
        <p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
          服务端没有下发任何设置项，请检查 `GET /admin/settings`。
        </p>
      ) : (
        groups.map((group) => (
          <Card key={group.spec.id} data-testid="settings-group" data-setting-group={group.spec.id}>
            <CardHeader className="border-b">
              <CardTitle className="text-base">{group.spec.title}</CardTitle>
              <CardDescription>{group.spec.hint}</CardDescription>
            </CardHeader>
            <CardContent className="divide-y p-0">
              {group.items.map((item) => (
                <SettingField
                  key={item.key}
                  item={item}
                  value={values[item.key]}
                  changed={
                    changed.has(item.key) ||
                    !draftEquals(values[item.key], initialValues[item.key])
                  }
                  invalid={invalid.has(item.key)}
                  error={fieldErrors[item.key]}
                  onChange={onChange}
                  onRequestApprovalModeChange={onRequestApprovalModeChange}
                />
              ))}
            </CardContent>
          </Card>
        ))
      )}

      {/* 置底 sticky 保存条（docs/04 §6.19「单一保存按钮置底」）。 */}
      <div className="sticky bottom-0 z-10 flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-background/95 px-4 py-3 shadow-lg backdrop-blur">
        <span
          className={cn(
            "text-sm",
            dirtyCount > 0 ? "font-medium text-foreground" : "text-muted-foreground",
          )}
          role="status"
          aria-live="polite"
        >
          {dirtyCount > 0 ? `有 ${dirtyCount} 项未保存` : "设置与服务端一致"}
        </span>
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
      </div>
    </div>
  );
}

function SettingField({
  item,
  value,
  changed,
  invalid,
  error,
  onChange,
  onRequestApprovalModeChange,
}: {
  item: SettingItem;
  value: unknown;
  changed: boolean;
  invalid: boolean;
  error: string | undefined;
  onChange: (key: string, value: unknown) => void;
  onRequestApprovalModeChange: (nextMode: string, apply: () => void) => void;
}) {
  /**
   * 控件 id：沿用 M2 的 `setting-<key 中的点换成连字符>` 规则
   * （既有验收用例用 `#setting-approval-whitelist_enabled` 定位开关）。
   * 精确的 key 由外层容器的 `data-setting-key` 承载，M3 用它定位出错的项。
   */
  const controlId = `setting-${item.key.replace(/\./g, "-")}`;
  const label = item.description ?? item.key;

  return (
    <div
      data-testid="setting-field"
      data-setting-key={item.key}
      data-invalid={invalid ? "true" : undefined}
      className={cn(
        "flex flex-col gap-3 p-4 sm:flex-row sm:items-start sm:justify-between",
        changed && "bg-accent/30",
        invalid && "border border-destructive bg-destructive/5",
      )}
    >
      <div className="space-y-1 sm:max-w-[55%]">
        <Label htmlFor={controlId} className="text-sm font-medium">
          {label}
          {changed ? <span className="text-xs text-muted-foreground">（已修改）</span> : null}
        </Label>
        <p className="font-mono text-[11px] text-muted-foreground">{item.key}</p>
        {error ? (
          <p data-testid="setting-error" role="alert" className="text-xs text-destructive">
            {error}
          </p>
        ) : null}
      </div>

      <div className="sm:min-w-[280px]">
        <SettingControl
          item={item}
          controlId={controlId}
          value={value}
          onChange={onChange}
          onRequestApprovalModeChange={onRequestApprovalModeChange}
        />
      </div>
    </div>
  );
}

/**
 * **控件映射函数**（docs/04 §6.19「控件的类型与范围由后端下发的 `value_type` /
 * `min` / `max` / `options` 驱动」，docs/03 §3.13）：
 *
 *   bool             → Switch
 *   int / number     → Input type="number"（min/max 直接取后端字段，后端没给就不设）
 *   json             → Textarea（提交前 `JSON.parse` 校验）
 *   list             → TagInput（多值）
 *   string + options → Select（选项集合完全来自后端 `options`）
 *   string 且 key 以 `_md` 结尾或 description 含 Markdown → Textarea
 *   string 其它       → Input
 *
 * 唯一「本地判断」是最后一条的 Markdown 渲染提示：它是显示形式的偏好，不改变取值集合、
 * 也不引入任何范围限制。
 */
function SettingControl({
  item,
  controlId,
  value,
  onChange,
  onRequestApprovalModeChange,
}: {
  item: SettingItem;
  controlId: string;
  value: unknown;
  onChange: (key: string, value: unknown) => void;
  onRequestApprovalModeChange: (nextMode: string, apply: () => void) => void;
}) {
  const type = typeOf(item);

  if (type === "bool") {
    return (
      <div className="flex items-center gap-2">
        <Switch
          id={controlId}
          checked={value === true}
          aria-label={item.description ?? item.key}
          onCheckedChange={(checked) => onChange(item.key, checked)}
        />
        <span className="text-sm text-muted-foreground">
          {value === true ? "已开启" : "已关闭"}
        </span>
      </div>
    );
  }

  if (type === "int" || type === "number") {
    return (
      <Input
        id={controlId}
        type="number"
        inputMode="numeric"
        className="w-40"
        value={typeof value === "string" ? value : String(value ?? "")}
        // 区间来自后端元信息；后端没给就不设，绝不写死（docs/03 §3.13）。
        min={item.min ?? undefined}
        max={item.max ?? undefined}
        onChange={(event) => onChange(item.key, event.target.value)}
      />
    );
  }

  if (type === "json") {
    return (
      <Textarea
        id={controlId}
        rows={5}
        className="font-mono text-xs"
        value={typeof value === "string" ? value : ""}
        onChange={(event) => onChange(item.key, event.target.value)}
      />
    );
  }

  if (type === "list") {
    const list = Array.isArray(value) ? value.map((entry) => String(entry)) : [];
    return (
      <TagInput
        value={list}
        inputId={controlId}
        // 契约没给 list 型设置项的条数/长度上限（`SETTING_SPECS` 不含
        // `upload.allowed_extensions`，list 校验只查元素类型），所以都不设上限，
        // 免得默认的 8 项 / 64 字符（工具标签上限）把 21 个默认扩展名之外的值堵死。
        max={null}
        maxItemLength={null}
        itemNoun="条目"
        onChange={(next) => onChange(item.key, next)}
      />
    );
  }

  // ---- string ----
  const options = item.options ?? [];
  if (options.length > 0) {
    const current = typeof value === "string" ? value : "";
    return (
      <Select
        value={current === "" ? undefined : current}
        onValueChange={(next) => {
          // FR-APPR-02：切到「全部放行」前必须二次确认（业务规则，与控件元信息无关）。
          if (item.key === SETTING_KEYS.approvalMode && next === "auto_approve_all") {
            onRequestApprovalModeChange(next, () => onChange(item.key, next));
            return;
          }
          onChange(item.key, next);
        }}
      >
        <SelectTrigger
          id={controlId}
          className="w-full sm:w-56"
          data-testid={item.key === SETTING_KEYS.approvalMode ? "settings-approval-mode" : undefined}
        >
          <SelectValue placeholder="请选择…" />
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem key={option} value={option}>
              {OPTION_LABELS[option] ?? option}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    );
  }

  if (isMarkdownHint(item)) {
    return (
      <Textarea
        id={controlId}
        rows={6}
        value={typeof value === "string" ? value : ""}
        onChange={(event) => onChange(item.key, event.target.value)}
      />
    );
  }

  return (
    <Input
      id={controlId}
      type="text"
      value={typeof value === "string" ? value : ""}
      onChange={(event) => onChange(item.key, event.target.value)}
    />
  );
}

/**
 * 渲染提示（不是元信息）：`_md` 后缀或说明里出现 Markdown 时用多行输入框。
 * 它不会改变取值类型，也不会限制任何取值范围。
 */
function isMarkdownHint(item: SettingItem): boolean {
  if (item.key.endsWith("_md")) return true;
  return /markdown/i.test(item.description ?? "");
}

/**
 * 已知枚举值的展示文案（纯 UI 文案，不参与元信息）：`options` 里出现的任何取值都会
 * 被渲染，未登记的取值直接显示原始字符串。
 */
const OPTION_LABELS: Readonly<Record<string, string>> = {
  require: "需审批",
  auto_approve_all: "全部放行",
  hot: "按热度",
  new: "按最新",
  name: "按名称",
};
