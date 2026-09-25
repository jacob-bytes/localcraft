import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"
import { Slot } from "radix-ui"

const badgeVariants = cva(
  "inline-flex w-fit shrink-0 items-center justify-center gap-1 overflow-hidden rounded-full border border-transparent px-2 py-0.5 text-xs font-medium whitespace-nowrap transition-[color,box-shadow] focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 [&>svg]:pointer-events-none [&>svg]:size-3",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground [a&]:hover:bg-primary/90",
        secondary:
          "bg-secondary text-secondary-foreground [a&]:hover:bg-secondary/90",
        // U2（docs/12 §2）：原为 `text-white` + `dark:bg-destructive/60`。
        // 硬编码白色忽略了 --destructive-foreground：它在 .dark 下是**深色文字**，
        // 于是深色主题里出现「白字 + 60% 透明红底」，实测只有 3.54:1（12px 要求 4.5）。
        // 改用语义 token 后，浅色仍是白字（值相同，无变化），深色改为深字。
        // 同时去掉 dark 下的 /60 —— 那会把红底冲淡，进一步压低对比度。
        destructive:
          "bg-destructive text-destructive-foreground focus-visible:ring-destructive/20 dark:focus-visible:ring-destructive/40 [a&]:hover:bg-destructive/90",
        outline:
          "border-border text-foreground [a&]:hover:bg-accent [a&]:hover:text-accent-foreground",
        ghost: "[a&]:hover:bg-accent [a&]:hover:text-accent-foreground",
        link: "text-primary underline-offset-4 [a&]:hover:underline",
        /**
         * Project variants (CONTRACT §14.7). docs/04 refers to
         * `variant="warning"` / `variant="success"` throughout (待审 of versions,
         * quota alarms, "新版待审" corner badges). Colour is never the only
         * signal — every use site also carries text (docs/04 §8.1).
         */
        warning:
          "bg-amber-100 text-amber-900 ring-1 ring-inset ring-amber-300/60 dark:bg-amber-950/70 dark:text-amber-200 dark:ring-amber-700/60",
        success:
          "bg-emerald-100 text-emerald-800 ring-1 ring-inset ring-emerald-300/60 dark:bg-emerald-950/70 dark:text-emerald-200 dark:ring-emerald-700/60",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

function Badge({
  className,
  variant = "default",
  asChild = false,
  ...props
}: React.ComponentProps<"span"> &
  VariantProps<typeof badgeVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot.Root : "span"

  return (
    <Comp
      data-slot="badge"
      data-variant={variant}
      className={cn(badgeVariants({ variant }), className)}
      {...props}
    />
  )
}

export { Badge, badgeVariants }
