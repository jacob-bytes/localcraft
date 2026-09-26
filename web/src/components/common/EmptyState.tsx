import type { LucideIcon } from "lucide-react";
import { PackageOpen } from "lucide-react";
import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Unified empty state (docs/04 §7.4). Every list page must use this instead of
 * hand-rolling its own, so copy and spacing stay consistent.
 *
 * M7 · F5：新增可选的 `illustration`（内联 SVG）。给了它就用插画替代
 * 「圆底 + lucide 图标」那一块；**标题与描述文字照常渲染**，插画不承载信息
 * （docs/04 §8.1：状态不能只靠图形/颜色表达）。没给 `illustration` 的调用方
 * 行为完全不变，所以其余空态无需改动。
 */
export interface EmptyStateProps {
  icon?: LucideIcon;
  /** 内联 SVG 插画（建议用 `@/components/common/EmptyStateArt` 里的三个）。 */
  illustration?: React.ReactNode;
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}

export function EmptyState({
  icon: Icon = PackageOpen,
  illustration,
  title,
  description,
  action,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed px-6 py-14 text-center",
        className,
      )}
      role="status"
    >
      {illustration ? (
        illustration
      ) : (
        <span className="grid size-12 place-items-center rounded-full bg-muted">
          <Icon aria-hidden="true" className="size-6 text-muted-foreground" />
        </span>
      )}
      <div className="space-y-1">
        <p className="text-base font-semibold">{title}</p>
        {description ? (
          <p className="mx-auto max-w-md text-sm text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {action}
    </div>
  );
}
