import { ChevronDown, ChevronUp, MessageSquare } from "lucide-react";
import * as React from "react";

import type { PromptDetailInfo } from "@/api/types";
import { CopyButton } from "@/components/common/CopyButton";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * `prompt` 类型专属区块（docs/04 §6.4）：代码风格内容块（`font-mono`、保留换行、
 * 最大高度 480px）+ 「复制」+ 字符数统计。
 *
 * 「复制」复制的值**必须恰好**是 `detail.prompt.content`（验收 #7）：不加缩进、
 * 不加省略号、不 trim。
 */
export interface PromptViewerProps {
  prompt: PromptDetailInfo | null;
}

export function PromptViewer({ prompt }: PromptViewerProps) {
  const [expanded, setExpanded] = React.useState(false);

  const content = prompt?.content ?? "";
  // `char_count` 由后端计算；缺失时退回前端长度（`docs/03` §3.4 保证存在）。
  const charCount = prompt?.char_count ?? content.length;

  if (prompt === null) {
    return (
      <section aria-labelledby="prompt-title" className="rounded-xl border bg-card p-4">
        <h2 id="prompt-title" className="flex items-center gap-2 text-lg font-semibold">
          <MessageSquare aria-hidden="true" className="size-4 text-muted-foreground" />
          提示词
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">该工具暂未提供提示词内容。</p>
      </section>
    );
  }

  return (
    <section aria-labelledby="prompt-title" className="space-y-3 rounded-xl border bg-card p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="prompt-title" className="flex items-center gap-2 text-lg font-semibold">
          <MessageSquare aria-hidden="true" className="size-4 text-muted-foreground" />
          提示词
        </h2>
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground tabular-nums">{charCount} 字符</span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-expanded={expanded}
            onClick={() => setExpanded((previous) => !previous)}
          >
            {expanded ? (
              <ChevronUp aria-hidden="true" className="size-4" />
            ) : (
              <ChevronDown aria-hidden="true" className="size-4" />
            )}
            {expanded ? "收起" : "展开"}
          </Button>
          {/* `CopyButton` 不接受 `data-testid`（共享组件），用零内边距的
              inline-flex 包装层承载 `prompt-copy`，点击落点仍是按钮本身。 */}
          <span data-testid="prompt-copy" className="inline-flex">
            <CopyButton value={content} label="复制提示词" />
          </span>
        </div>
      </div>

      <div className="relative">
        <pre
          data-testid="prompt-content"
          className={cn(
            "overflow-x-auto rounded-lg border bg-muted/40 p-3 font-mono text-sm leading-relaxed",
            "whitespace-pre-wrap break-words",
            !expanded && "max-h-[480px] overflow-y-hidden",
          )}
        >
          {content}
        </pre>
        {!expanded && content.length > 0 ? (
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-x-0 bottom-0 h-12 rounded-b-lg bg-gradient-to-t from-card to-transparent"
          />
        ) : null}
      </div>
    </section>
  );
}
