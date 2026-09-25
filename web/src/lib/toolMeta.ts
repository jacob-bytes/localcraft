import { Globe, MessageSquare, Sparkles, Wrench, type LucideIcon } from "lucide-react";

import type { ToolType } from "@/api/types";

/**
 * Fixed type→colour/icon mapping so users build muscle memory (docs/04 §3.1):
 * file = slate, webapp = sky, skill = violet, prompt = amber.
 * Icons per docs/04 §6.3: Wrench / Globe / Sparkles / MessageSquare.
 */
export interface ToolTypeMeta {
  label: string;
  icon: LucideIcon;
  /** Badge styling — always paired with a text label (docs/04 §8.1). */
  badgeClass: string;
  /** Cover placeholder tint (used when `cover_url === null`). */
  placeholderClass: string;
}

export const TOOL_TYPE_META: Record<ToolType, ToolTypeMeta> = {
  file: {
    label: "文件包",
    icon: Wrench,
    badgeClass:
      "bg-slate-100 text-slate-700 ring-slate-300/60 dark:bg-slate-800/80 dark:text-slate-200 dark:ring-slate-600/60",
    placeholderClass: "from-slate-200 to-slate-300 dark:from-slate-800 dark:to-slate-700",
  },
  webapp: {
    label: "在线工具",
    icon: Globe,
    badgeClass:
      "bg-sky-100 text-sky-800 ring-sky-300/60 dark:bg-sky-950/70 dark:text-sky-200 dark:ring-sky-700/60",
    placeholderClass: "from-sky-200 to-sky-300 dark:from-sky-950 dark:to-sky-900",
  },
  skill: {
    label: "Skill",
    icon: Sparkles,
    badgeClass:
      "bg-violet-100 text-violet-800 ring-violet-300/60 dark:bg-violet-950/70 dark:text-violet-200 dark:ring-violet-700/60",
    placeholderClass: "from-violet-200 to-violet-300 dark:from-violet-950 dark:to-violet-900",
  },
  prompt: {
    label: "提示词",
    icon: MessageSquare,
    badgeClass:
      "bg-amber-100 text-amber-900 ring-amber-300/60 dark:bg-amber-950/70 dark:text-amber-200 dark:ring-amber-700/60",
    placeholderClass: "from-amber-200 to-amber-300 dark:from-amber-950 dark:to-amber-900",
  },
};

export function toolTypeLabel(type: ToolType): string {
  return TOOL_TYPE_META[type].label;
}

/** Ordered list for the "类型" filter block. */
export const TOOL_TYPES: readonly ToolType[] = ["file", "webapp", "skill", "prompt"];

/**
 * Category → soft placeholder tint (docs/04 §6.3 "按分类映射的柔和色块").
 * Known demo slugs get a stable colour; anything else falls back to a hash so
 * the same category always gets the same tint.
 */
const CATEGORY_TINTS: Record<string, string> = {
  "dev-tools": "from-indigo-200 to-indigo-300 dark:from-indigo-950 dark:to-indigo-900",
  "ops-tools": "from-teal-200 to-teal-300 dark:from-teal-950 dark:to-teal-900",
  skill: "from-violet-200 to-violet-300 dark:from-violet-950 dark:to-violet-900",
  prompt: "from-amber-200 to-amber-300 dark:from-amber-950 dark:to-amber-900",
};

const TINTS: readonly string[] = [
  "from-indigo-200 to-indigo-300 dark:from-indigo-950 dark:to-indigo-900",
  "from-teal-200 to-teal-300 dark:from-teal-950 dark:to-teal-900",
  "from-rose-200 to-rose-300 dark:from-rose-950 dark:to-rose-900",
  "from-cyan-200 to-cyan-300 dark:from-cyan-950 dark:to-cyan-900",
  "from-lime-200 to-lime-300 dark:from-lime-950 dark:to-lime-900",
];

export function categoryTintClass(slug: string | null | undefined): string {
  if (slug && CATEGORY_TINTS[slug]) return CATEGORY_TINTS[slug];
  if (!slug) return TINTS[0] as string;
  let hash = 0;
  for (let index = 0; index < slug.length; index += 1) {
    hash = (hash * 31 + slug.charCodeAt(index)) % 9973;
  }
  return TINTS[hash % TINTS.length] as string;
}

/** Options shared by the toolbar selects. */
export const VISIBILITY_LABELS = {
  public: "公开",
  restricted: "指定可见",
  private: "私有",
} as const;
