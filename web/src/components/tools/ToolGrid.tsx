import type { ToolListItem } from "@/api/types";
import { ToolCard } from "@/components/tools/ToolCard";
import { ToolListTable } from "@/components/tools/ToolListTable";
import { cn } from "@/lib/utils";

/**
 * 门户卡片墙 / 列表（docs/04 §6.3）。
 *
 * M7 · F4：两种视图不再只是密度差别 —— `list` 走表格式的 `ToolListTable`
 * （名称/分类/类型/版本/下载数/更新时间），`grid` 保持封面浏览的卡片墙。
 */
export function ToolGrid({
  tools,
  variant = "grid",
  className,
}: {
  tools: ToolListItem[];
  variant?: "grid" | "list";
  className?: string;
}) {
  if (variant === "list") {
    return <ToolListTable tools={tools} className={className} />;
  }

  return (
    <ul
      className={cn(
        "grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4",
        className,
      )}
    >
      {tools.map((tool) => (
        <li key={tool.id} className="flex">
          <ToolCard tool={tool} />
        </li>
      ))}
    </ul>
  );
}
