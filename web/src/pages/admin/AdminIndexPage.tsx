import { useQuery } from "@tanstack/react-query";
import * as React from "react";
import { Link } from "react-router-dom";

import {
  approvalHistoryQueryKey,
  fetchApprovalHistory,
  fetchOverview,
  fetchStorageStats,
  overviewQueryKey,
  storageStatsQueryKey,
} from "@/api/admin";
import { getErrorMessage } from "@/api/client";
import type { StorageOwnerItem } from "@/api/types";
import {
  ACTION_LABELS,
  ACTION_VARIANTS,
} from "@/components/admin/ApprovalHistoryTable";
import { OverviewCards } from "@/components/admin/OverviewCards";
import { InsightsPanel } from "@/components/admin/InsightsPanel";
import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { PageSkeleton } from "@/components/common/PageSkeleton";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatDateTime, formatFileSize, formatRelativeTime } from "@/lib/format";

/**
 * `/admin` —— 管理概览（docs/04 §6.9，FR-ADMIN-14）。
 *
 * 四个数字卡 + 两个列表，「一眼看清平台健康度」。**没有任何图表**
 * （docs/04 §6.9、docs/01 第 10 章都明确不做折线图 / 饼图 / 趋势图）。
 *
 * 数据来源（CONTRACT §16.5 冻结的 92 个接口）：
 *  - `GET /admin/overview`（approver 可见）
 *  - `GET /admin/approvals/history?page_size=10`（approver 可见）
 *  - `GET /admin/stats/storage?limit=10`（**仅 superadmin**，approver 会 403 →
 *    该区块降级为说明性空态，而不是让整页报错）
 */

/** 最近审批条数与存储 Top N。 */
const RECENT_APPROVAL_LIMIT = 10;
const STORAGE_TOP_LIMIT = 10;

export default function AdminIndexPage() {
  const overviewQuery = useQuery({
    queryKey: overviewQueryKey,
    queryFn: ({ signal }) => fetchOverview(signal),
  });

  const historyParams = React.useMemo(
    () => ({ page: 1, page_size: RECENT_APPROVAL_LIMIT }),
    [],
  );
  const historyQuery = useQuery({
    queryKey: approvalHistoryQueryKey(historyParams),
    queryFn: ({ signal }) => fetchApprovalHistory(historyParams, signal),
  });

  const storageQuery = useQuery({
    queryKey: storageStatsQueryKey(STORAGE_TOP_LIMIT, 0),
    queryFn: ({ signal }) => fetchStorageStats({ limit: STORAGE_TOP_LIMIT }, signal),
    // 403（approver 无权）不重试：这不是瞬时故障。
    retry: false,
  });

  const records = historyQuery.data?.items ?? [];
  const storageItems = storageQuery.data?.items ?? [];

  return (
    <div data-testid="admin-overview" className="flex flex-col gap-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-xl font-semibold tracking-tight">管理概览</h1>
        <p className="text-xs text-muted-foreground">平台健康度概览（FR-ADMIN-14）· 只做数字与列表</p>
      </div>

      {overviewQuery.isPending ? (
        <PageSkeleton variant="cards" count={4} />
      ) : overviewQuery.isError ? (
        <ErrorState
          message={getErrorMessage(overviewQuery.error)}
          onRetry={() => void overviewQuery.refetch()}
        />
      ) : (
        <>
          <OverviewCards overview={overviewQuery.data} />

          {/*
            M8 · F11：30 日数字概览（下载趋势 / 活跃贡献者 / 分类分布 / 效率估算）。
            它有自己的 query key 与自己的 loading / error 边界，所以这个重聚合端点
            迟到或失败**不会**影响上面那四个轻量数字卡（§23.4 的拆分理由）。
          */}
          <InsightsPanel />

          <div className="grid gap-6 xl:grid-cols-2">
            {/* 最近审批（docs/04 §6.9）。 */}
            <section
              data-testid="overview-recent-approvals"
              aria-label="最近审批"
              className="flex flex-col gap-3"
            >
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h2 className="text-base font-semibold">最近审批</h2>
                <Link
                  to="/admin/approvals/history"
                  className="rounded text-sm text-primary underline-offset-4 hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                >
                  查看全部
                </Link>
              </div>

              {historyQuery.isPending ? (
                <PageSkeleton variant="list" count={5} />
              ) : historyQuery.isError ? (
                <ErrorState
                  message={getErrorMessage(historyQuery.error)}
                  onRetry={() => void historyQuery.refetch()}
                />
              ) : records.length === 0 ? (
                <EmptyState title="暂无审批记录" description="有工具提交审批后，这里会显示最近 10 条。" />
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>时间</TableHead>
                      <TableHead>工具</TableHead>
                      <TableHead>动作</TableHead>
                      <TableHead>操作人</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {records.map((record) => (
                      <TableRow key={record.id}>
                        <TableCell className="text-xs text-muted-foreground tabular-nums">
                          <time
                            dateTime={record.created_at ?? undefined}
                            title={formatDateTime(record.created_at)}
                          >
                            {formatRelativeTime(record.created_at)}
                          </time>
                        </TableCell>
                        <TableCell className="max-w-[14rem] truncate">
                          {record.tool_slug ? (
                            <Link
                              to={`/tools/${record.tool_slug}`}
                              className="rounded font-medium underline-offset-4 hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                            >
                              {record.tool_name ?? record.tool_slug}
                            </Link>
                          ) : (
                            <span>{record.tool_name ?? `#${record.tool_id}`}</span>
                          )}
                        </TableCell>
                        <TableCell>
                          <Badge variant={ACTION_VARIANTS[record.action]}>
                            {ACTION_LABELS[record.action]}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-sm">
                          <span>{record.actor_label}</span>
                          {record.is_automatic ? (
                            <Badge variant="outline" className="ml-1 text-[11px]">
                              系统自动
                            </Badge>
                          ) : null}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </section>

            {/* 存储占用 Top 10（按用户聚合，见 AdminOverviewResponse 与 docs/03 统计语义）。 */}
            <section
              data-testid="overview-storage-top"
              aria-label="存储占用 Top 10"
              className="flex flex-col gap-3"
            >
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h2 className="text-base font-semibold">存储占用 Top 10</h2>
                {storageQuery.data ? (
                  <span className="text-xs text-muted-foreground">
                    全站已用 {formatFileSize(storageQuery.data.total_used_bytes)} /{" "}
                    {formatFileSize(storageQuery.data.total_quota_bytes)} · 文件{" "}
                    {storageQuery.data.file_count} 个
                  </span>
                ) : null}
              </div>

              {storageQuery.isPending ? (
                <PageSkeleton variant="list" count={5} />
              ) : storageQuery.isError ? (
                // approver 无权调用 `/admin/stats/storage`（后端 admin_all_guard）。
                <EmptyState
                  title="无法查看存储明细"
                  description={`存储占用明细仅超级管理员可查看。${getErrorMessage(storageQuery.error)}`}
                />
              ) : storageItems.length === 0 ? (
                <EmptyState title="暂无存储占用数据" description="还没有用户上传文件。" />
              ) : (
                <ul className="space-y-3 rounded-xl border bg-card p-4">
                  {storageItems.map((item) => (
                    <StorageRow
                      key={item.owner_id}
                      item={item}
                      warningThresholdPct={overviewQuery.data?.storage_warning_threshold_pct}
                    />
                  ))}
                </ul>
              )}
            </section>
          </div>
        </>
      )}
    </div>
  );
}

/** 单行：用户 + 已用容量 + 占比条 + 工具数/版本数。 */
function StorageRow({
  item,
  warningThresholdPct,
}: {
  item: StorageOwnerItem;
  /**
   * 后端下发的告警阈值（`storage_warning_threshold_pct`）—— 前端不再自带 70/90。
   * 拿不到（概览还没回来 / 无权看概览）时不猜，只用满额标红。
   */
  warningThresholdPct?: number;
}) {
  const percent = Number.isFinite(item.used_percent)
    ? Math.max(0, Math.min(100, item.used_percent))
    : 0;
  const percentLabel = `${percent >= 10 ? percent.toFixed(0) : percent.toFixed(1)}%`;

  // 纯展示：达到后端阈值标黄，满额标红。真正的告警判定在 overview.storage_warning。
  const indicator =
    percent >= 100
      ? "bg-destructive"
      : warningThresholdPct !== undefined && percent >= warningThresholdPct
        ? "bg-warning"
        : "bg-primary";

  return (
    <li className="space-y-1.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-2">
        <span className="truncate text-sm font-medium">{item.display_name}</span>
        <span className="text-sm tabular-nums text-muted-foreground">
          {formatFileSize(item.used_bytes)}
          {item.quota_bytes > 0 ? ` / ${formatFileSize(item.quota_bytes)}` : "（不限配额）"}
        </span>
      </div>
      <p className="font-mono text-xs text-muted-foreground">{item.username}</p>
      <Progress
        value={percent}
        aria-label={`${item.display_name} 的存储占用 ${percentLabel}`}
        indicatorClassName={indicator}
      />
      <p className="text-xs text-muted-foreground">
        工具 {item.tool_count} · 版本 {item.version_count} · 占比 {percentLabel}
      </p>
    </li>
  );
}
