import { Switch as SwitchPrimitive } from "radix-ui"
import * as React from "react"

import { cn } from "@/lib/utils"

/**
 * Radix Switch（docs/04 §6.19 的开关控件）。
 *
 * 生成器目前没有产出 `switch.tsx`，M2 的系统设置里曾用本地 `role="switch"` 按钮
 * 顶替；M3 统一到 Radix 原语，键盘/无障碍行为与其它控件一致。
 */
const Switch = React.forwardRef<
  React.ComponentRef<typeof SwitchPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof SwitchPrimitive.Root>
>(({ className, ...props }, ref) => (
  <SwitchPrimitive.Root
    ref={ref}
    data-slot="switch"
    className={cn(
      "peer inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent shadow-xs transition-colors",
      "focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none",
      "disabled:cursor-not-allowed disabled:opacity-50",
      "data-[state=checked]:bg-primary data-[state=unchecked]:bg-input",
      className
    )}
    {...props}
  >
    <SwitchPrimitive.Thumb
      data-slot="switch-thumb"
      className={cn(
        "pointer-events-none block size-4 rounded-full bg-background shadow-lg ring-0 transition-transform",
        "data-[state=checked]:translate-x-4 data-[state=unchecked]:translate-x-0"
      )}
    />
  </SwitchPrimitive.Root>
))
Switch.displayName = "Switch"

export { Switch }
