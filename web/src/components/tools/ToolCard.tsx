import { Download, Eye, Lock } from "lucide-react";
import * as React from "react";
import { Link } from "react-router-dom";

import type { ToolListItem } from "@/api/types";
import { ToolTypeBadge } from "@/components/tools/ToolTypeBadge";
import { Badge } from "@/components/ui/badge";
import { formatCount, formatRelativeTime } from "@/lib/format";
import { categoryTintClass, TOOL_TYPE_META, VISIBILITY_LABELS } from "@/lib/toolMeta";
import { cn } from "@/lib/utils";

const MAX_TAGS = 3;

/**
 * Portal tool card (docs/04 §6.3, §7.2).
 *
 * The WHOLE card is a `<Link>` — never `onClick` — so middle-click (new tab),
 * cmd-click and "copy link address" all behave (docs/04 §6.3, acceptance #6).
 *
 * M7 · F4：原来的 `variant="row"` 紧凑列表已删除，列表视图改走
 * `ToolListTable`（表格式）。卡片视图只负责封面浏览，保持低信息密度。
 */
export interface ToolCardProps {
  tool: ToolListItem;
}

export function ToolCard({ tool }: ToolCardProps) {
  return <ToolCardGrid tool={tool} />;
}

function ToolTags({ tags, limit = MAX_TAGS }: { tags: string[]; limit?: number }) {
  if (tags.length === 0) {
    return <span className="text-xs text-muted-foreground">暂无标签</span>;
  }
  const visible = tags.slice(0, limit);
  const overflow = tags.length - visible.length;
  return (
    <ul className="flex flex-wrap items-center gap-1">
      {visible.map((tag) => (
        <li
          key={tag}
          className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs text-muted-foreground"
        >
          #{tag}
        </li>
      ))}
      {overflow > 0 ? (
        <li className="text-xs text-muted-foreground" title={tags.slice(limit).join(", ")}>
          +{overflow}
        </li>
      ) : null}
    </ul>
  );
}

/**
 * Cover area. `aspect-[16/9]` keeps the layout stable while the image loads and
 * gives the placeholder the same box, so cards never jump (docs/04 §9).
 * A tool without a cover gets a category-tinted block + type icon — never a
 * broken `<img>` (acceptance #7).
 *
 * M7 · F3（docs/12 §6.2 V1）：占位再设计 —— 按分类的**柔和**渐变 + **大号**
 * 类型图标（size-10 → size-16），图标用较低不透明度的语义令牌 `foreground`，
 * 在浅色（深字）与深色（浅字）两端都留出余量。图标本身 `aria-hidden`，
 * 不承载信息；类型仍由左上角的 `ToolTypeBadge` 文字徽标表达（docs/04 §8.1）。
 */
function ToolCover({ tool }: { tool: ToolListItem }) {
  const meta = TOOL_TYPE_META[tool.tool_type];
  const Icon = meta.icon;
  // A cover URL can still fail (404, expired ticket, restricted visibility), so
  // fall back to the placeholder instead of showing a broken image
  // (acceptance #7). The image endpoint is an M2 interface while M1 seeds carry
  // `cover_url`, which is exactly the gap this guards against.
  const [coverFailed, setCoverFailed] = React.useState(false);
  const showImage = Boolean(tool.cover_url) && !coverFailed;

  return (
    <div className="relative aspect-[16/9] w-full overflow-hidden rounded-t-xl bg-muted">
      {showImage ? (
        <img
          data-testid="cover-image"
          src={tool.cover_url ?? undefined}
          alt={`${tool.name} 封面`}
          loading="lazy"
          decoding="async"
          onError={() => setCoverFailed(true)}
          className="cover-media size-full object-cover transition-transform duration-150 group-hover:scale-105"
        />
      ) : (
        <div
          data-testid="cover-placeholder"
          aria-hidden="true"
          className={cn(
            "flex size-full items-center justify-center bg-gradient-to-br",
            categoryTintClass(tool.category?.slug),
          )}
        >
          <Icon className="size-16 text-foreground/55" strokeWidth={1.25} />
        </div>
      )}

      <ToolTypeBadge type={tool.tool_type} className="absolute left-2 top-2 shadow-sm" />

      {tool.visibility !== "public" ? (
        <span
          className="absolute bottom-2 left-2 inline-flex items-center gap-1 rounded-md bg-background/85 px-1.5 py-0.5 text-xs font-medium text-foreground ring-1 ring-inset ring-border"
          title={`可见范围：${VISIBILITY_LABELS[tool.visibility]}`}
        >
          <Lock aria-hidden="true" className="size-3" />
          {VISIBILITY_LABELS[tool.visibility]}
        </span>
      ) : null}

      {tool.has_pending_version ? (
        <Badge variant="warning" className="absolute right-2 top-2">
          新版待审
        </Badge>
      ) : null}
    </div>
  );
}

function ToolCardGrid({ tool }: { tool: ToolListItem }) {
  return (
    <Link
      to={`/tools/${tool.slug}`}
      data-testid="tool-card"
      data-tool-slug={tool.slug}
      aria-label={`查看工具 ${tool.name}`}
      className={cn(
        "group flex h-full w-full flex-col overflow-hidden rounded-xl border bg-card text-card-foreground",
        "transition duration-150 hover:-translate-y-0.5 hover:shadow-md focus-visible:-translate-y-0.5 focus-visible:shadow-md",
      )}
    >
      <ToolCover tool={tool} />

      <div className="flex flex-1 flex-col gap-2 p-3">
        <h3 data-testid="tool-card-name" className="line-clamp-1 text-base font-semibold" title={tool.name}>
          {tool.name}
        </h3>
        <p
          data-testid="tool-card-summary"
          className="line-clamp-2 text-sm text-muted-foreground"
          title={tool.summary}
        >
          {tool.summary}
        </p>

        <div className="mt-auto space-y-2">
          <ToolTags tags={tool.tags} />
          <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-2">
              <span className="inline-flex items-center gap-1">
                <Download aria-hidden="true" className="size-3" />
                <span className="tabular-nums">{formatCount(tool.download_count)}</span>
                <span className="sr-only">次下载</span>
              </span>
              <span className="inline-flex items-center gap-1">
                <Eye aria-hidden="true" className="size-3" />
                <span className="tabular-nums">{formatCount(tool.view_count)}</span>
                <span className="sr-only">次浏览</span>
              </span>
              <span className="max-w-[6rem] truncate" title={tool.owner?.display_name}>
                {tool.owner?.display_name ?? "—"}
              </span>
            </span>
            <span className="inline-flex items-center gap-1">
              <span className="max-w-[5rem] truncate">{tool.category?.name ?? "未分类"}</span>
              <span aria-hidden="true">·</span>
              <time dateTime={tool.updated_at ?? undefined}>{formatRelativeTime(tool.updated_at)}</time>
            </span>
          </div>
        </div>
      </div>
    </Link>
  );
}
