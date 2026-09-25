import { AlertCircle, RotateCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Unified error state for list pages (docs/04 §7.4, §6.3 "加载失败").
 * Always offers a retry wired to TanStack Query's `refetch`.
 */
export interface ErrorStateProps {
  title?: string;
  message?: string;
  onRetry?: () => void;
  className?: string;
}

export function ErrorState({
  title = "加载失败",
  message,
  onRetry,
  className,
}: ErrorStateProps) {
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-xl border border-destructive/30 bg-destructive/5 px-6 py-12 text-center",
        className,
      )}
    >
      <span className="grid size-12 place-items-center rounded-full bg-destructive/10">
        <AlertCircle aria-hidden="true" className="size-6 text-destructive" />
      </span>
      <div className="space-y-1">
        <p className="text-base font-semibold">{title}</p>
        {message ? (
          <p className="mx-auto max-w-md text-sm text-muted-foreground">{message}</p>
        ) : null}
      </div>
      {onRetry ? (
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RotateCw aria-hidden="true" className="size-4" />
          重试
        </Button>
      ) : null}
    </div>
  );
}
