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
  testId = "tool-grid",
}: {
  tools: ToolListItem[];
  variant?: "grid" | "list";
  className?: string;
  /**
   * M16：面板（「继续使用」/「我的收藏」tab）也用这个组件渲染网格，但
   * `tool-grid` 这个 testid 的既有语义是「**门户卡片墙**」（`helpers.ts` 的
   * `openTool`、m8/m9 的用例都靠它把卡片墙那张卡与别处的同 slug 卡片区分开）。
   * 所以面板传自己的 id，卡片墙保持默认值 —— 任何时刻 `[data-testid="tool-grid"]`
   * 都只指卡片墙。
   *
   * 仅在 `variant === "grid"` 时生效（list 走 `ToolListTable`，不暴露这个锚点）。
   */
  testId?: string;
}) {
  if (variant === "list") {
    return <ToolListTable tools={tools} className={className} />;
  }

  return (
    <ul
      /*
       * M14：门户里同一个工具可能同时出现在两个地方 ——「继续使用」/「我的
       * 收藏」区块，以及卡片墙本身。`data-tool-slug` 两处都有（它由 `ToolCard`
       * 渲染），所以「**卡片墙上的那张卡**」需要一个稳定锚点；测试里
       * `[data-testid="tool-grid"] [data-tool-slug=…]` 就是它。
       */
      data-testid={testId}
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
