import { ChevronLeft, ChevronRight, MoreHorizontal } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Pagination with ellipsis folding (docs/04 §6.3 "页码省略号折叠").
 * Page numbers are buttons (keyboard reachable), never `div onClick`.
 */
export interface PaginationProps {
  page: number;
  pages: number;
  onPageChange: (page: number) => void;
  className?: string;
}

/** 1 … 4 [5] 6 … 20 */
function buildPageItems(page: number, pages: number): Array<number | "gap"> {
  if (pages <= 7) {
    return Array.from({ length: pages }, (_, index) => index + 1);
  }
  const items: Array<number | "gap"> = [1];
  const start = Math.max(2, page - 1);
  const end = Math.min(pages - 1, page + 1);
  if (start > 2) items.push("gap");
  for (let current = start; current <= end; current += 1) items.push(current);
  if (end < pages - 1) items.push("gap");
  items.push(pages);
  return items;
}

export function Pagination({ page, pages, onPageChange, className }: PaginationProps) {
  // Hidden only when there is genuinely nothing to page through. An
  // out-of-range page (`?page=9` with 6 pages) keeps the control visible so the
  // user can navigate back (docs/04 §6.3 "分页边界").
  if (pages <= 1 && page <= 1) return null;
  const items = buildPageItems(page, Math.max(pages, 1));

  return (
    <nav aria-label="分页" className={cn("flex items-center justify-center gap-1", className)}>
      <Button
        type="button"
        variant="outline"
        size="icon"
        aria-label="上一页"
        disabled={page <= 1}
        onClick={() => onPageChange(page - 1)}
      >
        <ChevronLeft aria-hidden="true" className="size-4" />
      </Button>

      {items.map((item, index) =>
        item === "gap" ? (
          <span
            key={`gap-${index}`}
            aria-hidden="true"
            className="grid size-8 place-items-center text-muted-foreground"
          >
            <MoreHorizontal className="size-4" />
          </span>
        ) : (
          <Button
            key={item}
            type="button"
            variant={item === page ? "default" : "outline"}
            size="icon"
            aria-label={`第 ${item} 页`}
            aria-current={item === page ? "page" : undefined}
            onClick={() => onPageChange(item)}
          >
            {item}
          </Button>
        ),
      )}

      <Button
        type="button"
        variant="outline"
        size="icon"
        aria-label="下一页"
        disabled={page >= pages}
        onClick={() => onPageChange(page + 1)}
      >
        <ChevronRight aria-hidden="true" className="size-4" />
      </Button>
    </nav>
  );
}
