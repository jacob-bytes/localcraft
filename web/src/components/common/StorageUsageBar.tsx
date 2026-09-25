import { Progress } from "@/components/ui/progress";
import { formatFileSize } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * Storage quota bar (docs/04 §7.5).
 *
 * Thresholds: < 70% primary, 70~90% warning, > 90% destructive. The colour is
 * always paired with the "已用 x / y（z%）" text so it is not colour-only
 * (docs/04 §8.1).
 */
export interface StorageUsageBarProps {
  usedBytes: number;
  quotaBytes: number;
  className?: string;
  /** Extra context under the bar, e.g. platform-wide usage. */
  hint?: string;
}

export function StorageUsageBar({
  usedBytes,
  quotaBytes,
  className,
  hint,
}: StorageUsageBarProps) {
  const unlimited = quotaBytes <= 0;
  const percent = unlimited ? 0 : Math.min(100, Math.max(0, (usedBytes / quotaBytes) * 100));

  const tone =
    percent > 90
      ? { indicator: "bg-destructive", text: "text-destructive" }
      : percent >= 70
        ? { indicator: "bg-warning", text: "text-amber-600 dark:text-amber-400" }
        : { indicator: "bg-primary", text: "text-muted-foreground" };

  return (
    <div className={cn("space-y-2", className)}>
      <div className="flex items-baseline justify-between gap-2 text-sm">
        <span className={cn("font-medium", tone.text)}>
          {unlimited
            ? `已用 ${formatFileSize(usedBytes)}（不限配额）`
            : `已用 ${formatFileSize(usedBytes)} / ${formatFileSize(quotaBytes)}（${percent.toFixed(0)}%）`}
        </span>
        {!unlimited && percent >= 90 ? (
          <span className="text-xs font-medium text-destructive">空间即将用尽</span>
        ) : null}
      </div>
      <Progress
        value={unlimited ? 0 : percent}
        aria-label="存储用量"
        indicatorClassName={tone.indicator}
      />
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}
