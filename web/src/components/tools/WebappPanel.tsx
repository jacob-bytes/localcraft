import { ExternalLink, Globe } from "lucide-react";

import type { ToolDetail } from "@/api/types";
import { CopyButton } from "@/components/common/CopyButton";
import { Button } from "@/components/ui/button";

/**
 * `webapp` 类型专属区块（docs/04 §6.4）：大号「打开工具」+ 可复制的 URL。
 *
 * 探活状态点（绿/红/灰）在 M2 接口清单里没有对应字段 —— `docs/03` §3.4 未下发
 * 健康检查结果，故不渲染该状态点（已在报告中提出）。
 */
export interface WebappPanelProps {
  detail: ToolDetail;
}

export function WebappPanel({ detail }: WebappPanelProps) {
  const url = detail.webapp_url;

  if (!url) {
    return (
      <section
        aria-labelledby="webapp-title"
        className="space-y-3 rounded-xl border bg-card p-4"
      >
        <h2 id="webapp-title" className="flex items-center gap-2 text-lg font-semibold">
          <Globe aria-hidden="true" className="size-4 text-muted-foreground" />
          在线工具
        </h2>
        <p className="text-sm text-muted-foreground">
          该工具暂未配置访问地址，请联系作者补充。
        </p>
      </section>
    );
  }

  return (
    <section
      aria-labelledby="webapp-title"
      className="space-y-3 rounded-xl border bg-card p-4"
    >
      <h2 id="webapp-title" className="flex items-center gap-2 text-lg font-semibold">
        <Globe aria-hidden="true" className="size-4 text-muted-foreground" />
        在线工具
      </h2>

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
    </section>
  );
}
