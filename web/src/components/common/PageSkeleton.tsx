import { Loader2 } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/**
 * Unified loading placeholders (docs/04 §7.4, §6.3).
 * Skeletons mirror the real layout so nothing jumps when data arrives
 * (docs/04 §9).
 */

export interface PageSkeletonProps {
  variant?: "cards" | "list" | "page";
  count?: number;
  className?: string;
}

function CardSkeleton() {
  return (
    <div className="overflow-hidden rounded-xl border bg-card">
      <div className="aspect-[16/9] w-full animate-pulse bg-muted" />
      <div className="space-y-2 p-3">
        <Skeleton className="h-5 w-3/4" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-2/3" />
        <div className="flex gap-2 pt-1">
          <Skeleton className="h-4 w-12" />
          <Skeleton className="h-4 w-12" />
          <Skeleton className="h-4 w-8" />
        </div>
      </div>
    </div>
  );
}

function RowSkeleton() {
  return (
    <div className="flex items-center gap-3 rounded-lg border bg-card px-3 py-3">
      <Skeleton className="h-5 w-16" />
      <div className="flex-1 space-y-2">
        <Skeleton className="h-4 w-1/3" />
        <Skeleton className="h-3 w-2/3" />
      </div>
      <Skeleton className="h-4 w-20" />
    </div>
  );
}

export function PageSkeleton({ variant = "cards", count, className }: PageSkeletonProps) {
  const items = count ?? (variant === "list" ? 10 : 8);

  if (variant === "page") {
    return (
      <div
        className={cn("flex min-h-[60vh] items-center justify-center gap-3", className)}
        role="status"
        aria-live="polite"
      >
        <Loader2 aria-hidden="true" className="size-5 animate-spin text-muted-foreground" />
        <span className="text-sm text-muted-foreground">加载中…</span>
      </div>
    );
  }

  return (
    <div
      role="status"
      aria-live="polite"
      aria-busy="true"
      className={cn(
        variant === "cards"
          ? "grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
          : "space-y-2",
        className,
      )}
    >
      <span className="sr-only">加载中…</span>
      {Array.from({ length: items }, (_, index) =>
        variant === "cards" ? <CardSkeleton key={index} /> : <RowSkeleton key={index} />,
      )}
    </div>
  );
}

/** Full-viewport loader used while the session state is still `unknown`. */
export function FullScreenLoader({ label = "正在恢复会话…" }: { label?: string }) {
  return (
    <div
      data-testid="full-screen-loader"
      className="grid min-h-screen place-items-center bg-background"
      role="status"
      aria-live="polite"
    >
      <div className="flex flex-col items-center gap-3">
        <Loader2 aria-hidden="true" className="size-6 animate-spin text-primary" />
        <p className="text-sm text-muted-foreground">{label}</p>
      </div>
    </div>
  );
}
