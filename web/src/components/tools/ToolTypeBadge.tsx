import type { ToolType } from "@/api/types";
import { cn } from "@/lib/utils";
import { TOOL_TYPE_META } from "@/lib/toolMeta";

/**
 * Type badge: icon + text label. Never colour-only (docs/04 §8.1).
 */
export function ToolTypeBadge({
  type,
  className,
}: {
  type: ToolType;
  className?: string;
}) {
  const meta = TOOL_TYPE_META[type];
  const Icon = meta.icon;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset",
        meta.badgeClass,
        className,
      )}
    >
      <Icon aria-hidden="true" className="size-3" />
      {meta.label}
    </span>
  );
}
