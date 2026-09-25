import { Check, Copy } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Copy-to-clipboard button (docs/04 §7.6): ghost icon button, icon flips to a
 * check for 2 seconds, falls back to `document.execCommand` when the async
 * Clipboard API is unavailable (non-HTTPS intranet hosts).
 */
export interface CopyButtonProps {
  value: string;
  /** Accessible name, e.g. "复制 SHA256". */
  label?: string;
  className?: string;
  onCopied?: () => void;
  /** Forwarded as `data-testid` so E2E can click the real button. */
  testId?: string;
}

async function writeToClipboard(value: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch {
    /* fall through to the legacy path */
  }
  try {
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    const ok = document.execCommand("copy");
    textarea.remove();
    return ok;
  } catch {
    return false;
  }
}

export function CopyButton({
  value,
  label = "复制",
  className,
  onCopied,
  testId,
}: CopyButtonProps) {
  const [copied, setCopied] = React.useState(false);
  const timerRef = React.useRef<number | null>(null);

  React.useEffect(() => {
    return () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    };
  }, []);

  async function handleCopy() {
    const ok = await writeToClipboard(value);
    if (!ok) return;
    setCopied(true);
    onCopied?.();
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => setCopied(false), 2000);
  }

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      data-testid={testId}
      className={cn("size-8", className)}
      aria-label={copied ? "已复制" : label}
      onClick={() => {
        void handleCopy();
      }}
    >
      {copied ? (
        <Check aria-hidden="true" className="size-4 text-success" />
      ) : (
        <Copy aria-hidden="true" className="size-4" />
      )}
    </Button>
  );
}
