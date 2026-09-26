import { Link } from "react-router-dom";

import type { ToolListItem } from "@/api/types";
import { ToolTypeBadge } from "@/components/tools/ToolTypeBadge";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatCount, formatDateTime, formatRelativeTime } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * 门户「列表视图」（docs/04 §6.3 的紧凑列表；M7 · F4 把它做成真正的表格式）。
 *
 * 与卡片视图的分工：
 *  - 列表视图 = **表格式、可扫读**：名称 / 分类 / 类型 / 版本 / 下载数 / 更新时间，
 *    6 列固定宽度（`table-fixed`），一屏能横着读完，不再显示封面；
 *  - 卡片视图 = 封面浏览、低信息密度（`ToolCard`）。
 *
 * **字段来源**：全部来自列表响应里已有的 `ToolListItem` 字段
 * （`name` / `category.name` / `tool_type` / `current_version` /
 * `download_count` / `updated_at`），没有为这一列要求后端加任何接口或字段。
 * 注意 docs/04 §6.3 的原始描述只列了 5 列（名称、类型、分类、下载量、更新时间），
 * M7 任务书 F4 明确要求加「版本」——`current_version` 本来就在列表响应里，故照做。
 *
 * **整行可点**：docs/04 §6.3 验收 #6 要求整卡可点且支持中键/⌘-click。表格里
 * `<tr>` 不能包在 `<a>` 里，所以用「拉伸链接」——第一格放一个 `absolute inset-0`
 * 的 `<Link>`，盖住整行（`<tr>` 设 `relative`）。这样中键新标签、右键复制链接
 * 都成立，同时保留了 `<table>` 的语义（读屏能按行列朗读）。
 */
export function ToolListTable({
  tools,
  className,
}: {
  tools: ToolListItem[];
  className?: string;
}) {
  return (
    <div className={cn("overflow-hidden rounded-xl border bg-card", className)}>
      <Table className="min-w-[880px] table-fixed">
        <TableCaption className="sr-only">
          工具列表，共 {tools.length} 行，列：名称、分类、类型、版本、下载数、更新时间
        </TableCaption>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead>名称</TableHead>
            <TableHead className="w-[10rem]">分类</TableHead>
            <TableHead className="w-[8.5rem]">类型</TableHead>
            <TableHead className="w-[7rem]">版本</TableHead>
            <TableHead className="w-[6.5rem] text-right">下载数</TableHead>
            <TableHead className="w-[8rem] text-right">更新时间</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {tools.map((tool) => (
            <TableRow
              key={tool.id}
              data-testid="tool-list-row"
              data-tool-slug={tool.slug}
              className="relative"
            >
              <TableCell>
                <Link
                  to={`/tools/${tool.slug}`}
                  data-testid="tool-list-link"
                  aria-label={`查看工具 ${tool.name}`}
                  className="absolute inset-0 z-10 rounded-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset focus-visible:outline-none"
                />
                <div className="min-w-0">
                  <p className="truncate font-medium" title={tool.name}>
                    {tool.name}
                  </p>
                  <p className="truncate text-xs text-muted-foreground" title={tool.summary}>
                    {tool.summary}
                  </p>
                </div>
              </TableCell>

              <TableCell className="truncate text-muted-foreground">
                {tool.category?.name ?? "未分类"}
              </TableCell>

              <TableCell>
                <ToolTypeBadge type={tool.tool_type} />
              </TableCell>

              <TableCell className="truncate font-mono text-xs tabular-nums">
                {tool.current_version ?? "—"}
              </TableCell>

              <TableCell className="text-right tabular-nums">
                {formatCount(tool.download_count)}
              </TableCell>

              <TableCell className="truncate text-right text-muted-foreground">
                <time
                  dateTime={tool.updated_at ?? undefined}
                  title={formatDateTime(tool.updated_at)}
                >
                  {formatRelativeTime(tool.updated_at)}
                </time>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
