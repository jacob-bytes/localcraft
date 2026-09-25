import { Download, Loader2 } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { getErrorMessage } from "@/api/client";
import { createDownloadTicket } from "@/api/tools";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatFileSize } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * 「申请票据 → 触发下载 → 失败提示」的完整链路（docs/04 §7.3、§6.4；docs/03 §1.10）。
 *
 * 为什么不能直接用 `<a href="/api/v1/tools/…/download">`：该请求需要
 * `Authorization` 头，而 `<a>` 无法携带；refresh cookie 又被限制在
 * `Path=/api/v1/auth`。所以先 POST 拿短时效票据，再用隐藏 `<a>` 打开票据 URL，
 * 让浏览器原生下载管理器接管（支持 Range、大文件不占 JS 内存）。
 */
export interface DownloadButtonProps {
  slug: string;
  versionId?: number | null;
  disabled?: boolean;
  /** Shown in a tooltip when `disabled` (e.g.「当前角色无下载权限」). */
  disabledReason?: string;
  /** Fallback name when the ticket does not carry one. */
  fileName?: string | null;
  /** Bytes — rendered as a muted caption under the button. */
  size?: number | null;
  label?: string;
  variant?: "default" | "outline";
  /** `aria-label`; the caller knows the tool / version context (docs/04 §8.1). */
  ariaLabel?: string;
  /** E2E hook: `download-button` (sidebar) / `version-download` (timeline). */
  testId?: string;
  /** Rendered as `data-version` so E2E can target one timeline row. */
  version?: string;
  className?: string;
}

export function DownloadButton({
  slug,
  versionId = null,
  disabled = false,
  disabledReason,
  fileName,
  size,
  label = "立即下载",
  variant = "default",
  ariaLabel,
  testId,
  version,
  className,
}: DownloadButtonProps) {
  const [pending, setPending] = React.useState(false);

  async function handleDownload() {
    // Repeat clicks while a ticket is in flight are ignored (docs/04 §7.3).
    if (pending || disabled) return;
    setPending(true);
    try {
      const ticket = await createDownloadTicket(slug, versionId);
      const name = ticket.file_name ?? fileName ?? "";
      const anchor = document.createElement("a");
      anchor.href = ticket.url;
      if (name) anchor.download = name;
      anchor.rel = "noopener";
      anchor.hidden = true;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      toast.success(name ? `开始下载 ${name}` : "开始下载");
    } catch (error) {
      toast.error(getErrorMessage(error));
    } finally {
      setPending(false);
    }
  }

  const button = (
    <Button
      type="button"
      variant={variant}
      size={variant === "outline" ? "sm" : "default"}
      disabled={disabled || pending}
      aria-label={ariaLabel ?? label}
      data-testid={testId}
      data-version={version}
      onClick={() => {
        void handleDownload();
      }}
      className={cn(variant === "default" && "w-full", className)}
    >
      {pending ? (
        <Loader2 aria-hidden="true" className="animate-spin" />
      ) : (
        <Download aria-hidden="true" />
      )}
      {pending ? "准备中…" : label}
    </Button>
  );

  return (
    <div className={cn("flex flex-col gap-1", variant === "default" && "w-full")}>
      {disabled ? (
        // Radix `Tooltip` 不能直接挂到 `disabled` 的按钮上（禁用的按钮不派发事件），
        // 所以用一个可聚焦的 `<span>` 作为 trigger，键盘用户也能读到原因。
        <Tooltip>
          <TooltipTrigger asChild>
            <span
              tabIndex={0}
              aria-label={`${ariaLabel ?? label}：${disabledReason ?? "当前不可下载"}`}
              className="inline-flex w-full rounded-md"
            >
              {button}
            </span>
          </TooltipTrigger>
          <TooltipContent>{disabledReason ?? "当前不可下载"}</TooltipContent>
        </Tooltip>
      ) : (
        button
      )}

      {typeof size === "number" && size > 0 ? (
        <span className="text-center text-xs text-muted-foreground">
          {formatFileSize(size)}
        </span>
      ) : null}
    </div>
  );
}
