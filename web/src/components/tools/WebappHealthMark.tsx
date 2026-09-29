import { TriangleAlert } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { readWebappHealthStatus, webappHealthLabel } from "@/lib/webappHealth";

/**
 * M14 · F2（CONTRACT §29.3）：在线工具探活的两个展示件。
 *
 * **为什么颜色全部走语义令牌**：`docs/12` §U2 的教训是一次硬编码 `text-white`
 * 在深色主题下掉到 3.54:1。这里一个色值都不写死 ——
 *  - 卡片标记用既有 `Badge` 的 `destructive` 变体（`bg-destructive` +
 *    `text-destructive-foreground`，正是 §U2 修好后复测过的那一组）；
 *  - 详情状态点的圆点用 `bg-success` / `bg-destructive` / `bg-muted-foreground`。
 * 实测对比度见 `e2e/m14-portal-highlights.spec.ts`（在真实渲染的 DOM 上量）。
 *
 * **颜色从不单独表意**（docs/04 §8.1）：标记有文字「可能已失效」，详情行有
 * 「运行正常 / 检测失败 / 检测超时 / 尚未检测」，圆点只是辅助。
 */

/**
 * 卡片上的轻量标记：只在 `webapp_unhealthy === true` 时由调用方渲染。
 *
 * 只放一个短徽标 + `title`，**不在卡片上放大段文字**（F2.1）。悬停 / 读屏能拿到
 * 完整解释，卡片布局不受影响。
 */
export function WebappUnhealthyMark() {
  return (
    <Badge
      variant="destructive"
      data-testid="webapp-unhealthy-badge"
      title="该在线工具可能已失效（最近一次健康检查未通过）"
    >
      <TriangleAlert aria-hidden="true" />
      可能已失效
    </Badge>
  );
}

/**
 * 详情页的一行健康状态（只对 `tool_type === 'webapp'` 渲染，见 `WebappPanel`）。
 *
 * `status` / `checkedAt` 收 `unknown`：字段可能整个缺失（后端未落地），
 * 这里一律宽容解析 —— 缺失落到「尚未检测」，**绝不落到「检测失败」**（F2.2）。
 */
export function WebappHealthRow({
  status,
  checkedAt,
}: {
  status: unknown;
  checkedAt: unknown;
}) {
  const normalized = readWebappHealthStatus(status);
  const checked = typeof checkedAt === "string" && checkedAt.length > 0 ? checkedAt : null;
  const label = webappHealthLabel(status);

  return (
    <div
      data-testid="webapp-health"
      data-health={normalized ?? "unchecked"}
      className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm"
    >
      <span
        aria-hidden="true"
        className={cn(
          "size-2 shrink-0 rounded-full",
          normalized === null
            ? "bg-muted-foreground"
            : normalized === "ok"
              ? "bg-success"
              : "bg-destructive",
        )}
      />
      <span data-testid="webapp-health-label" className="font-medium">
        {label}
      </span>
      <span className="text-muted-foreground">
        {checked ? (
          <>
            最后检查{" "}
            <time dateTime={checked} title={formatDateTime(checked)}>
              {formatDateTime(checked)}
            </time>
          </>
        ) : (
          "从未检测"
        )}
      </span>
    </div>
  );
}
