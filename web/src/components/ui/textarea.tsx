import * as React from "react";

import { cn } from "@/lib/utils";

const Textarea = React.forwardRef<HTMLTextAreaElement, React.ComponentProps<"textarea">>(
  ({ className, ...props }, ref) => (
    <textarea
      ref={ref}
      data-slot="textarea"
      className={cn(
        "flex min-h-20 w-full rounded-md border border-input bg-transparent px-3 py-2 text-base shadow-xs transition-[color,box-shadow] outline-none",
        "placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50 md:text-sm dark:bg-input/30",
        "focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50",
        "aria-invalid:border-destructive aria-invalid:ring-destructive/20",
        className,
      )}
      {...props}
    />
  ),
);
Textarea.displayName = "Textarea";

/**
 * 多行文本输入。
 *
 * `web/src/components/ui/` has no `textarea.tsx` (it is not an M1 need) and that
 * directory belongs to another agent's scope, so this project-local primitive
 * lives next to the editor components that need it (docs/04 §6.7).
 * Styling mirrors `ui/input.tsx` so the two stay visually identical.
 */
export { Textarea };
