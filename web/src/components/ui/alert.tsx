import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"

const alertVariants = cva(
  "relative grid w-full grid-cols-[0_1fr] items-start gap-y-0.5 rounded-lg border px-4 py-3 text-sm has-[>svg]:grid-cols-[calc(var(--spacing)*4)_1fr] has-[>svg]:gap-x-3 [&>svg]:size-4 [&>svg]:translate-y-0.5 [&>svg]:text-current",
  {
    variants: {
      variant: {
        default: "bg-card text-card-foreground",
        destructive:
          "bg-card text-destructive *:data-[slot=alert-description]:text-destructive/90 [&>svg]:text-current",
        /**
         * Project variants (CONTRACT §14.7). `warning` is used for
         * "needs attention but not an error" (skill parse problems, quota near
         * the limit, backlog > 24h); `success` for confirmations such as
         * "已发布". Both pair the colour with text.
         */
        warning:
          "border-amber-300/60 bg-amber-50 text-amber-900 *:data-[slot=alert-description]:text-amber-900/90 dark:border-amber-700/60 dark:bg-amber-950/40 dark:text-amber-100 dark:*:data-[slot=alert-description]:text-amber-100/90",
        success:
          "border-emerald-300/60 bg-emerald-50 text-emerald-900 *:data-[slot=alert-description]:text-emerald-900/90 dark:border-emerald-700/60 dark:bg-emerald-950/40 dark:text-emerald-100 dark:*:data-[slot=alert-description]:text-emerald-100/90",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

function Alert({
  className,
  variant,
  ...props
}: React.ComponentProps<"div"> & VariantProps<typeof alertVariants>) {
  return (
    <div
      data-slot="alert"
      role="alert"
      className={cn(alertVariants({ variant }), className)}
      {...props}
    />
  )
}

function AlertTitle({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="alert-title"
      className={cn(
        "col-start-2 line-clamp-1 min-h-4 font-medium tracking-tight",
        className
      )}
      {...props}
    />
  )
}

function AlertDescription({
  className,
  ...props
}: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="alert-description"
      className={cn(
        "col-start-2 grid justify-items-start gap-1 text-sm text-muted-foreground [&_p]:leading-relaxed",
        className
      )}
      {...props}
    />
  )
}

export { Alert, AlertTitle, AlertDescription }
