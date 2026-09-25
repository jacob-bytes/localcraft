import { RadioGroup as RadioGroupPrimitive } from "radix-ui";
import * as React from "react";

import { cn } from "@/lib/utils";

function RadioGroup({
  className,
  ...props
}: React.ComponentProps<typeof RadioGroupPrimitive.Root>) {
  return (
    <RadioGroupPrimitive.Root
      data-slot="radio-group"
      className={cn("grid gap-2", className)}
      {...props}
    />
  );
}

const RadioGroupItem = React.forwardRef<
  React.ComponentRef<typeof RadioGroupPrimitive.Item>,
  React.ComponentPropsWithoutRef<typeof RadioGroupPrimitive.Item>
>(({ className, ...props }, ref) => (
  <RadioGroupPrimitive.Item
    ref={ref}
    data-slot="radio-group-item"
    className={cn(
      "aspect-square size-4 shrink-0 rounded-full border border-input shadow-xs transition-[color,box-shadow] outline-none",
      "focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50",
      "disabled:cursor-not-allowed disabled:opacity-50",
      "data-[state=checked]:border-primary data-[state=checked]:bg-primary data-[state=checked]:text-primary-foreground",
      "aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:bg-input/30",
      className,
    )}
    {...props}
  >
    <RadioGroupPrimitive.Indicator
      data-slot="radio-group-indicator"
      className="grid place-content-center"
    >
      <span className="size-2 rounded-full bg-primary-foreground" />
    </RadioGroupPrimitive.Indicator>
  </RadioGroupPrimitive.Item>
));
RadioGroupItem.displayName = "RadioGroupItem";

/**
 * 单选组（docs/04 §6.7「工具类型」「可见性」两处都用 RadioGroup）。
 *
 * `web/src/components/ui/` ships no radio-group primitive (it is not an M1
 * need) and that directory is out of scope for the 个人中心 work, so this
 * project-local copy sits next to its consumers. Built on the already-installed
 * `radix-ui` umbrella package — no new dependency (docs/04 §1).
 */
export { RadioGroup, RadioGroupItem };
