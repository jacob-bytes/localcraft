import { ChevronLeft, ChevronRight, ImageOff } from "lucide-react";
import * as React from "react";

import type { ToolImage, ToolType } from "@/api/types";
import { TOOL_PLACEHOLDER_CLASS, TOOL_TYPE_META } from "@/lib/toolMeta";
import { cn } from "@/lib/utils";

/**
 * 详情页图片区（docs/04 §6.4）：封面 + 截图，主视图用原图 `url`，缩略图用
 * `thumb_url`（docs/04 §9「详情页才用原图」）。
 *
 * 图片 URL 一律来自后端 payload（CONTRACT §14.3 的签名能力 URL），前端不得自行
 * 拼接 `/api/v1/images/...`。签名 URL 仍可能失效（TTL 过期、可见性收紧），所以
 * 用 `onError` 降级成中性占位块 —— 与 ToolCard 封面一致，永不出现破图。
 */
export interface ToolImageGalleryProps {
  images: ToolImage[];
  toolName: string;
  toolType: ToolType;
}

function orderImages(images: ToolImage[]): ToolImage[] {
  // 封面固定在最前，其余按 sort_order；`sort` 稳定，后端顺序得以保留。
  return [...images].sort((a, b) => {
    if (a.kind !== b.kind) return a.kind === "cover" ? -1 : 1;
    return a.sort_order - b.sort_order;
  });
}

/**
 * 无图 / 加载失败时的占位：中性背景 + 类型图标（docs/04 §6.3）。
 * M17 · F1（CONTRACT §34.3）：不再按分类上色，`categorySlug` 参数随之删除
 * （同 `TOOL_PLACEHOLDER_CLASS`，零参）。
 */
function GalleryPlaceholder({ toolType, className }: { toolType: ToolType; className?: string }) {
  const Icon = TOOL_TYPE_META[toolType].icon;
  return (
    <div
      data-testid="gallery-placeholder"
      aria-hidden="true"
      className={cn(
        "flex size-full items-center justify-center",
        TOOL_PLACEHOLDER_CLASS,
        className,
      )}
    >
      {/* M7 · F3：与卡片占位统一 —— 大号类型图标 + 较低不透明度的语义前景色。 */}
      <Icon className="size-12 text-foreground/55" strokeWidth={1.25} />
    </div>
  );
}

export function ToolImageGallery({ images, toolName, toolType }: ToolImageGalleryProps) {
  const ordered = React.useMemo(() => orderImages(images), [images]);
  const [requestedIndex, setRequestedIndex] = React.useState(0);
  const [failedIds, setFailedIds] = React.useState<readonly number[]>([]);

  const count = ordered.length;
  // 切换工具时组件实例可能复用，索引越界时回退到第一张。
  const index = count > 0 ? Math.min(requestedIndex, count - 1) : 0;
  const current = ordered[index];
  const currentFailed = current ? failedIds.includes(current.id) : false;

  const markFailed = React.useCallback((id: number) => {
    setFailedIds((previous) => (previous.includes(id) ? previous : [...previous, id]));
  }, []);

  const step = (delta: number) => {
    if (count === 0) return;
    setRequestedIndex((previous) => (previous + delta + count) % count);
  };

  return (
    <div className="space-y-2">
      <div className="relative aspect-[16/9] w-full overflow-hidden rounded-xl border bg-muted">
        {current && !currentFailed ? (
          <img
            key={current.id}
            data-testid="gallery-main-image"
            src={current.url}
            alt={current.alt_text ?? `${toolName} ${index + 1}`}
            decoding="async"
            onError={() => markFailed(current.id)}
            className="cover-media size-full object-contain"
          />
        ) : (
          <GalleryPlaceholder toolType={toolType} />
        )}

        {count > 1 ? (
          <>
            <button
              type="button"
              aria-label="上一张图片"
              onClick={() => step(-1)}
              className="absolute left-2 top-1/2 grid size-8 -translate-y-1/2 place-items-center rounded-full bg-background/85 text-foreground ring-1 ring-inset ring-border transition hover:bg-background"
            >
              <ChevronLeft aria-hidden="true" className="size-4" />
            </button>
            <button
              type="button"
              aria-label="下一张图片"
              onClick={() => step(1)}
              className="absolute right-2 top-1/2 grid size-8 -translate-y-1/2 place-items-center rounded-full bg-background/85 text-foreground ring-1 ring-inset ring-border transition hover:bg-background"
            >
              <ChevronRight aria-hidden="true" className="size-4" />
            </button>
          </>
        ) : null}

        {count > 1 ? (
          <p className="absolute bottom-2 right-2 rounded-md bg-background/85 px-1.5 py-0.5 text-xs text-muted-foreground tabular-nums ring-1 ring-inset ring-border">
            {index + 1} / {count}
          </p>
        ) : null}
      </div>

      {count > 1 ? (
        <ul aria-label="图片缩略图" className="flex flex-wrap gap-2">
          {ordered.map((image, position) => {
            const active = position === index;
            const failed = failedIds.includes(image.id);
            return (
              <li key={image.id}>
                <button
                  type="button"
                  aria-label={image.alt_text ?? `查看第 ${position + 1} 张图片`}
                  aria-current={active ? "true" : undefined}
                  onClick={() => setRequestedIndex(position)}
                  className={cn(
                    "block size-16 overflow-hidden rounded-md border bg-muted transition",
                    active
                      ? "ring-2 ring-ring ring-offset-1 ring-offset-background"
                      : "opacity-70 hover:opacity-100",
                  )}
                >
                  {failed ? (
                    <span className="grid size-full place-items-center">
                      <ImageOff aria-hidden="true" className="size-4 text-muted-foreground" />
                    </span>
                  ) : (
                    <img
                      src={image.thumb_url}
                      alt=""
                      loading="lazy"
                      decoding="async"
                      onError={() => markFailed(image.id)}
                      className="cover-media size-full object-cover"
                    />
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
