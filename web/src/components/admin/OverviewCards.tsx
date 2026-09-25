import { AlertTriangle } from "lucide-react";
import * as React from "react";
import { Link } from "react-router-dom";

import type { AdminOverviewResponse } from "@/api/types";
import {
  TOOL_STATUS_ORDER,
  toolStatusLabel,
  toolStatusVariant,
} from "@/components/admin/AdminToolTable";
import { StorageUsageBar } from "@/components/common/StorageUsageBar";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { formatCount } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * 管理概览的四个统计卡（docs/04 §6.9，FR-ADMIN-14）。
 *
 * **只有数字与徽标，没有任何图表**：docs/04 §6.9 与 docs/01 第 10 章都明确
 * 不做折线图 / 饼图 / 趋势图。
 */
export interface OverviewCardsProps {
  overview: AdminOverviewResponse;
}

const BIG_NUMBER_CLASS = "text-3xl font-semibold tracking-tight tabular-nums";

/** 小于 10% 时保留一位小数，否则整数 —— 「0.1%」比「0%」更有信息量。 */
function formatPercent(percent: number): string {
  return `${percent >= 10 ? percent.toFixed(0) : percent.toFixed(1)}%`;
}

export function OverviewCards({ overview }: OverviewCardsProps) {
  const pending = overview.pending_count;
  const usedPercent = Number.isFinite(overview.used_percent)
    ? Math.max(0, Math.min(100, overview.used_percent))
    : 0;

  /**
   * **告警口径只在后端**（M4 易错点 3）：`storage_warning` 由
   * `settings_service.storage_warning()` 用 `quota.warn_threshold_pct` 判定，
   * 前端不再自己拿 `used_percent` 与阈值比较 —— 否则两处口径迟早漂移。
   * `storage_warning_threshold_pct` 只用于文案里说明阈值是多少。
   */
  const storageWarning = overview.storage_warning;

  /** 按固定顺序展示，未知状态排在最后并原样显示（服务端字段是 `string`）。 */
  const statusCounts = React.useMemo(() => {
    const rank = (status: string) => {
      const index = TOOL_STATUS_ORDER.indexOf(status as (typeof TOOL_STATUS_ORDER)[number]);
      return index === -1 ? TOOL_STATUS_ORDER.length : index;
    };
    return [...overview.tools_by_status].sort((a, b) => rank(a.status) - rank(b.status));
  }, [overview.tools_by_status]);

  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {/* 待审：整卡可点击，用 <Link> 而不是 onClick（docs/04 §8.1）。 */}
      <Link
        to="/admin/approvals"
        data-testid="overview-pending-card"
        className="rounded-xl focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:outline-none"
      >
        <Card className="h-full gap-3 transition-colors hover:bg-accent/40">
          <CardHeader className="gap-1">
            <CardDescription>待审</CardDescription>
            <CardTitle
              className={cn(BIG_NUMBER_CLASS, pending > 0 && "text-amber-600 dark:text-amber-400")}
            >
              {pending}
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap items-center gap-2">
            <Badge variant={pending > 0 ? "warning" : "secondary"}>
              {pending > 0 ? "需处理" : "队列已清空"}
            </Badge>
            <span className="text-xs text-muted-foreground">进入审批队列</span>
          </CardContent>
        </Card>
      </Link>

      {/* 工具：总数 + 按状态细分的小徽标（必须带文字，docs/04 §8.1）。 */}
      <Card data-testid="overview-tools-card" className="gap-3">
        <CardHeader className="gap-1">
          <CardDescription>工具</CardDescription>
          <CardTitle className={BIG_NUMBER_CLASS}>{formatCount(overview.tool_count)}</CardTitle>
        </CardHeader>
        <CardContent>
          {statusCounts.length === 0 ? (
            <p className="text-xs text-muted-foreground">暂无工具</p>
          ) : (
            <ul className="flex flex-wrap gap-1.5">
              {statusCounts.map((entry) => (
                <li key={entry.status}>
                  <Badge variant={toolStatusVariant(entry.status)} className="tabular-nums">
                    {toolStatusLabel(entry.status)} {entry.count}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {/* 用户：总数 + 启用/禁用，次要信息压缩成一行 muted 小字。 */}
      <Card data-testid="overview-users-card" className="gap-3">
        <CardHeader className="gap-1">
          <CardDescription>用户</CardDescription>
          <CardTitle className={BIG_NUMBER_CLASS}>{overview.user_count}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-1.5">
          <p className="text-sm">
            启用 <span className="font-medium tabular-nums">{overview.active_user_count}</span> /
            禁用 <span className="font-medium tabular-nums">{overview.disabled_user_count}</span>
          </p>
          <p className="text-xs text-muted-foreground">
            分类 {overview.category_count} · 标签 {overview.tag_count} · 用户组{" "}
            {overview.group_count} · 有效 Token {overview.active_token_count}
          </p>
        </CardContent>
      </Card>

      {/* 存储：`/admin/overview` 已带 used_bytes / quota_bytes / used_percent。 */}
      <Card data-testid="overview-storage-card" className="gap-3">
        <CardHeader className="gap-1">
          <CardDescription>存储</CardDescription>
          <CardTitle className={cn(BIG_NUMBER_CLASS, storageWarning && "text-destructive")}>
            {overview.quota_bytes > 0 ? formatPercent(usedPercent) : "不限"}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <StorageUsageBar usedBytes={overview.used_bytes} quotaBytes={overview.quota_bytes} />
          {storageWarning ? (
            <Alert data-testid="overview-storage-warning" variant="warning">
              <AlertTriangle aria-hidden="true" className="size-4" />
              <AlertTitle>存储水位告警</AlertTitle>
              <AlertDescription>
                已用容量达到或超过 {overview.storage_warning_threshold_pct}% 阈值，请清理回收站或扩容。
              </AlertDescription>
            </Alert>
          ) : null}
          <p className="text-xs text-muted-foreground">
            近 7 日下载 {formatCount(overview.downloads_last_7_days)} · 累计下载{" "}
            {formatCount(overview.download_count)} · 浏览 {formatCount(overview.view_count)}
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

export default OverviewCards;
