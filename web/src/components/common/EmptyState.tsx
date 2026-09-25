import type { LucideIcon } from "lucide-react";
import { PackageOpen } from "lucide-react";
import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Unified empty state (docs/04 §7.4). Every list page must use this instead of
 * hand-rolling its own, so copy and spacing stay consistent.
 */
export interface EmptyStateProps {
  icon?: LucideIcon;
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}

export function EmptyState({
  icon: Icon = PackageOpen,
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
      <span className="grid size-12 place-items-center rounded-full bg-muted">
        <Icon aria-hidden="true" className="size-6 text-muted-foreground" />
      </span>
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
