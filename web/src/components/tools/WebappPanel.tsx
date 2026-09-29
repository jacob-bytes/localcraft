import { ExternalLink, Globe } from "lucide-react";

import type { ToolDetail } from "@/api/types";
import { CopyButton } from "@/components/common/CopyButton";
import { WebappHealthRow } from "@/components/tools/WebappHealthMark";
import { Button } from "@/components/ui/button";

/**
 * `webapp` 类型专属区块（docs/04 §6.4）：大号「打开工具」+ 可复制的 URL，
 * 以及 **M14 · F2 的探活状态**。
 *
 * 状态点的历史：M2 时 `docs/03` §3.4 没有下发健康检查结果，这里刻意不渲染状态点
 * （旧注释如此）。M14 的 §29.3 把 `webapp_health_status` / `webapp_checked_at`
 * 加进了 `ToolDetail`，于是这一块终于能显示真实状态 —— 口径仍然只有服务端一个
 * 来源，前端不自己推断「健不健康」。
 */
export interface WebappPanelProps {
  detail: ToolDetail;
}

export function WebappPanel({ detail }: WebappPanelProps) {
  const url = detail.webapp_url;

  return (
    <section
      aria-labelledby="webapp-title"
      className="space-y-3 rounded-xl border bg-card p-4"
    >
      <h2 id="webapp-title" className="flex items-center gap-2 text-lg font-semibold">
        <Globe aria-hidden="true" className="size-4 text-muted-foreground" />
        在线工具
      </h2>

      {/*
        探活状态与「有没有配置访问地址」是两件事（`webapp_health_url` 与
        `webapp_url` 在库里是两个列），所以这一段在两个分支里都渲染。
      */}
      <WebappHealthRow
        status={detail.webapp_health_status}
        checkedAt={detail.webapp_checked_at}
      />

      {url ? (
        <>
          <Button asChild size="lg" className="w-full sm:w-auto">
            <a href={url} target="_blank" rel="noopener noreferrer">
              <ExternalLink aria-hidden="true" />
              打开工具
            </a>
          </Button>

          <div className="flex items-center gap-1 rounded-md border bg-muted/40 px-2 py-1.5">
            {/* 可选中文本：用户可能想手动复制或截断后的片段 */}
            <code className="min-w-0 flex-1 select-all truncate font-mono text-xs" title={url}>
              {url}
            </code>
            <CopyButton value={url} label="复制工具地址" className="shrink-0" />
          </div>
        </>
      ) : (
        <p className="text-sm text-muted-foreground">
          该工具暂未配置访问地址，请联系作者补充。
        </p>
      )}
    </section>
  );
}
