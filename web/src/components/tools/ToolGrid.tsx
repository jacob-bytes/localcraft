import type { ToolListItem } from "@/api/types";
import { ToolCard } from "@/components/tools/ToolCard";
import { cn } from "@/lib/utils";

/** Card wall / compact list (docs/04 §6.3). */
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
    return (
      <ul className={cn("space-y-2", className)}>
        {tools.map((tool) => (
          <li key={tool.id}>
            <ToolCard tool={tool} variant="row" />
          </li>
        ))}
      </ul>
    );
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
