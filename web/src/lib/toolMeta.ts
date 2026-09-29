import { Globe, MessageSquare, Sparkles, Wrench, type LucideIcon } from "lucide-react";

import type { ToolType } from "@/api/types";

/**
 * Fixed type→icon/badge mapping so users build muscle memory (docs/04 §3.1):
 * file = slate, webapp = sky, skill = violet, prompt = amber（仅徽标；占位不再上色）。
 * Icons per docs/04 §6.3: Wrench / Globe / Sparkles / MessageSquare.
 *
 * M17 · F1（CONTRACT §34.3）：占位背景**不再按类型上色** ——
 * `placeholderClass` 已删除，占位统一走 `TOOL_PLACEHOLDER_CLASS`（中性语义令牌）。
 * 类型仍由彩色 `badgeClass` 徽标（§34.2 裁定保留）+ 占位上的大号图标表达。
 */
export interface ToolTypeMeta {
  label: string;
  icon: LucideIcon;
  /** Badge styling — always paired with a text label (docs/04 §8.1). */
  badgeClass: string;
}

export const TOOL_TYPE_META: Record<ToolType, ToolTypeMeta> = {
  file: {
    label: "文件包",
    icon: Wrench,
    badgeClass:
      "bg-slate-100 text-slate-700 ring-slate-300/60 dark:bg-slate-800/80 dark:text-slate-200 dark:ring-slate-600/60",
  },
  webapp: {
    label: "在线工具",
    icon: Globe,
    badgeClass:
      "bg-sky-100 text-sky-800 ring-sky-300/60 dark:bg-sky-950/70 dark:text-sky-200 dark:ring-sky-700/60",
  },
  skill: {
    label: "Skill",
    icon: Sparkles,
    badgeClass:
      "bg-violet-100 text-violet-800 ring-violet-300/60 dark:bg-violet-950/70 dark:text-violet-200 dark:ring-violet-700/60",
  },
  prompt: {
    label: "提示词",
    icon: MessageSquare,
    badgeClass:
      "bg-amber-100 text-amber-900 ring-amber-300/60 dark:bg-amber-950/70 dark:text-amber-200 dark:ring-amber-700/60",
  },
};

export function toolTypeLabel(type: ToolType): string {
  return TOOL_TYPE_META[type].label;
}

/** Ordered list for the "类型" filter block. */
export const TOOL_TYPES: readonly ToolType[] = ["file", "webapp", "skill", "prompt"];

/**
 * 封面占位背景（M17 · F1，CONTRACT §34.3）——**唯一**的占位底色来源。
 *
 * 用**语义令牌** `muted`，明暗两态都由主题自己决定：
 *  - 浅色主题：`--muted = oklch(0.97 0.005 265)` → 米白 / 浅灰
 *  - 深色主题：`--muted = oklch(0.26 0.015 265)` → 深中性（**不是白的**）
 *
 * 为什么不是固定色：`bg-gray-100` / 十六进制在深色主题下**仍然是浅色**，
 * 刺眼且与卡片对比突兀 —— 这正是 M7 · V7 处理过的问题。
 *
 * 这是一个**零参常量**：占位不再按分类（原 `CATEGORY_TINTS` / `categoryTintClass(slug)`）
 * 或类型（原 `TOOL_TYPE_META[*].placeholderClass`）取色，按 slug 取色的函数签名
 * 已整体删除，不留「接受参数但忽略它」的误导性接口。类型信息由占位上的大号
 * 图标 + 左上角 `ToolTypeBadge` 表达；占位与真实封面共用同一个盒子（docs/04 §9）。
 */
export const TOOL_PLACEHOLDER_CLASS = "bg-muted";

/** Options shared by the toolbar selects. */
export const VISIBILITY_LABELS = {
  public: "公开",
  restricted: "指定可见",
  private: "私有",
} as const;
