import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Info } from "lucide-react";

import { fetchInsights, insightsQueryKey } from "@/api/admin";
import { getErrorMessage, isApiError } from "@/api/client";
import type { AdminInsightsResponse, CategoryInsightItem, SavingsInsight } from "@/api/types";
import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { PageSkeleton } from "@/components/common/PageSkeleton";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { formatCount } from "@/lib/format";

/**
 * M8 · F11 —— 管理概览的数字增强（CONTRACT §23.6、§23.8）。
 *
 * ## 为什么没有图表
 *
 * D10 与 D35 都明确「不做图表、不做投屏」；`docs/12` §6.3 也把「加图表/可视化大屏」
 * 列进「不建议做」。所以 30 日趋势在这里是**逐日数字 + 迷你进度条列表**，
 * 没有折线/饼图，也没有引入任何图表依赖（§23.0 Q1、§23.8 台账）。
 *
 * ## 效率估算：口径、覆盖率、数字必须同时可见
 *
 * §23.6 特意让 `savings.basis` 随数字一起下发，目的就是**让前端无法脱离口径单独
 * 渲染 `total_minutes`**。这个组件把三样东西放在**同一个卡片、同一次渲染**里：
 *
 *   1. 口径文案（「基于工具作者自述的估算，非实测」+ `basis` 原始值）
 *   2. 覆盖率（`covered_tool_count / total_tool_count`，以及未填多少、未计入）
 *   3. 估算数字（换算成人日 / 人时后**仍然**带「估算」二字）
 *
 * 任一项缺失就没有第二个出口 —— 任务书 F11 明说「禁止只显示一个孤零零的
 * 『已节省 320 人时』」，这个数字是要拿去汇报的。
 *
 * 覆盖率低于 `LOW_COVERAGE_PCT` 时额外给一条「数据不足以支撑结论」的显式提示，
 * 而不是把大数字漂亮地摆出来。
 */

/** 低于这个覆盖率就显式提示「数据不足以支撑结论」。 */
const LOW_COVERAGE_PCT = 50;
/** 30 天趋势里最多列出多少天（契约保证 30 条，这里全部列出，不截断）。 */
const DAILY_ROWS = 30;
/** 一个「人日」按 8 小时计。 */
const MINUTES_PER_WORKDAY = 8 * 60;

export function InsightsPanel() {
  const insightsQuery = useQuery({
    queryKey: insightsQueryKey,
    queryFn: ({ signal }) => fetchInsights(signal),
    // 后端尚未部署该端点时是 404，重试没有意义（也不会把首页拖慢）。
    retry: false,
    staleTime: 5 * 60_000,
  });

  return (
    <section aria-labelledby="admin-insights-title" data-testid="admin-insights" className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="admin-insights-title" className="text-base font-semibold">
          30 日数字概览
        </h2>
        <p className="text-xs text-muted-foreground">
          数字与列表，不做图表（D10 / D35）
        </p>
      </div>

      {insightsQuery.isPending ? (
        <PageSkeleton variant="cards" count={4} />
      ) : insightsQuery.isError ? (
        /*
         * `GET /admin/stats/insights` 在真机上是**超管专属**（approver 会得到
         * `403 FORBIDDEN`，与 `/admin/overview` 不同）。管理概览页本身对 approver
         * 开放，所以这里必须**降级成说明性空态**，而不是让整页报错 —— 与
         * 「存储占用 Top 10」区块对 approver 的处理完全一致（同一页既有惯例）。
         */
        isForbidden(insightsQuery.error) ? (
          <div data-testid="insights-forbidden">
            <EmptyState
              title="数字概览仅超级管理员可见"
              description="近 30 日趋势、活跃贡献者、分类分布与效率估算走的是管理统计接口，仅超级管理员有权读取。"
            />
          </div>
        ) : (
          <ErrorState
            message={`数字概览暂时不可用：${getErrorMessage(insightsQuery.error)}`}
            onRetry={() => void insightsQuery.refetch()}
          />
        )
      ) : (
        <InsightsBody insights={insightsQuery.data} />
      )}
    </section>
  );
}

/** `403 FORBIDDEN`（含后端 `SCOPE_MISSING`）。 */
function isForbidden(error: unknown): boolean {
  return isApiError(error) && (error.status === 403 || error.code === "FORBIDDEN");
}

function InsightsBody({ insights }: { insights: AdminInsightsResponse }) {
  const daily = Array.isArray(insights.downloads_daily) ? insights.downloads_daily.slice(-DAILY_ROWS) : [];
  const dailyMax = daily.reduce((max, point) => Math.max(max, point.downloads), 0);

  return (
    <div className="grid gap-4 xl:grid-cols-2">
      {/* ---- 30 日下载趋势（逐日数字，不做图表） ---- */}
      <Card data-testid="insights-downloads" className="gap-3">
        <CardHeader className="gap-1">
          <CardDescription>近 30 日下载</CardDescription>
          <CardTitle className="text-3xl font-semibold tabular-nums">
            {formatCount(insights.downloads_last_30_days)}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <p className="text-xs text-muted-foreground">
            下面按天列出每天的次数（条形只表示当天占当月峰值的比例），合计{" "}
            <span className="tabular-nums">{formatCount(insights.downloads_last_30_days)}</span> 次。
          </p>
          {daily.length === 0 ? (
            <p className="text-sm text-muted-foreground">没有可用的按天数据。</p>
          ) : (
            <ul
              data-testid="insights-daily-list"
              className="grid max-h-64 grid-cols-1 gap-x-4 gap-y-1 overflow-y-auto pr-1 sm:grid-cols-2"
            >
              {daily.map((point) => {
                const percent = dailyMax > 0 ? Math.round((point.downloads / dailyMax) * 100) : 0;
                return (
                  <li key={point.date} className="flex items-center gap-2 text-xs">
                    <span className="w-[5.5rem] shrink-0 font-mono tabular-nums text-muted-foreground">
                      {point.date}
                    </span>
                    <Progress
                      value={percent}
                      className="h-1.5"
                      aria-label={`${point.date} 下载 ${point.downloads} 次`}
                    />
                    <span className="w-10 shrink-0 text-right tabular-nums">
                      {point.downloads}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>

      {/* ---- 活跃贡献者 + 分类分布 ---- */}
      <div className="space-y-4">
        <Card data-testid="insights-contributors" className="gap-3">
          <CardHeader className="gap-1">
            <CardDescription>30 天内活跃贡献者</CardDescription>
            <CardTitle className="text-3xl font-semibold tabular-nums">
              {formatCount(insights.active_contributors_30d)}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-xs text-muted-foreground">
              30 天内上传或更新过工具的去重用户数（同一人多次提交只算一次）。
            </p>
          </CardContent>
        </Card>

        <Card data-testid="insights-categories" className="gap-3">
          <CardHeader className="gap-1">
            <CardDescription>分类分布</CardDescription>
            <CardTitle className="text-base font-semibold">工具数与下载量</CardTitle>
          </CardHeader>
          <CardContent>
            <CategoryList items={insights.tools_by_category} />
          </CardContent>
        </Card>
      </div>

      {/* ---- 效率估算（口径 + 覆盖率 + 数字，三者同屏） ---- */}
      <SavingsCard savings={insights.savings} />
    </div>
  );
}

function CategoryList({ items }: { items: CategoryInsightItem[] }) {
  const list = Array.isArray(items) ? items : [];
  const maxTools = list.reduce((max, item) => Math.max(max, item.tool_count), 0);
  if (list.length === 0) {
    return <p className="text-sm text-muted-foreground">还没有分类数据。</p>;
  }
  return (
    <ul data-testid="insights-category-list" className="space-y-2">
      {list.map((item) => {
        const percent = maxTools > 0 ? Math.round((item.tool_count / maxTools) * 100) : 0;
        return (
          <li
            key={`${item.category_id ?? "none"}-${item.name}`}
            className="space-y-1"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-x-2 text-sm">
              <span className="truncate">{item.name}</span>
              <span className="text-xs text-muted-foreground tabular-nums">
                工具 {item.tool_count} · 下载 {formatCount(item.download_count)}
              </span>
            </div>
            <Progress
              value={percent}
              className="h-1.5"
              aria-label={`${item.name}：工具 ${item.tool_count} 个，下载 ${item.download_count} 次`}
            />
          </li>
        );
      })}
    </ul>
  );
}

/**
 * 把分钟换算成人日 / 人时。**返回值里始终带「估算」**，调用方不需要再补 ——
 * 这样一个数字永远不可能脱离口径被单独复制出去。
 */
function formatSavingEstimate(minutes: number): string {
  const safe = Number.isFinite(minutes) && minutes > 0 ? minutes : 0;
  const hours = safe / 60;
  const workdays = safe / MINUTES_PER_WORKDAY;
  if (safe === 0) return "0 人时（估算）";
  if (workdays >= 1) {
    return `${workdays.toFixed(1)} 人日（≈ ${hours.toFixed(1)} 人时，估算）`;
  }
  return `${hours.toFixed(1)} 人时（估算）`;
}

/** 覆盖率的展示口径：`covered / total`（百分比），`total === 0` 时不给百分比。 */
function coverageOf(savings: SavingsInsight): {
  covered: number;
  total: number;
  uncovered: number;
  percent: number | null;
} {
  const total = Math.max(0, savings.total_tool_count);
  const covered = Math.max(0, Math.min(total, savings.covered_tool_count));
  return {
    covered,
    total,
    uncovered: Math.max(0, total - covered),
    percent: total > 0 ? (covered / total) * 100 : null,
  };
}

function SavingsCard({ savings }: { savings: SavingsInsight }) {
  const coverage = coverageOf(savings);
  const lowCoverage = coverage.percent !== null && coverage.percent < LOW_COVERAGE_PCT;
  const noData = coverage.covered === 0;
  const percentLabel =
    coverage.percent === null ? "—" : `${coverage.percent.toFixed(1)}%`;

  return (
    <Card data-testid="insights-savings" className="gap-3">
      <CardHeader className="gap-1">
        <CardDescription>效率估算</CardDescription>
        <CardTitle
          data-testid="insights-savings-number"
          className="text-3xl font-semibold tabular-nums"
        >
          {noData ? "暂无" : formatSavingEstimate(savings.total_minutes)}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {/*
          口径文案。`basis` 的原始值也一并显示：它是接口自己声明的口径标识，
          前端不该把它藏起来（§23.6：basis 让接口无法脱离口径渲染数字）。
        */}
        <div className="space-y-1 text-xs text-muted-foreground">
          <p data-testid="insights-savings-basis">
            口径：
            <span className="font-medium text-foreground">
              基于工具作者自述的「单次使用预计节省时长」汇总，非实测
            </span>
            （接口口径标识 <span className="font-mono">{savings.basis}</span>）。
          </p>
          <p>
            估算方式：作者自填分钟数 × 该工具的去重受益人数（同一人重复下载只算一次）。
          </p>
        </div>

        {/* 覆盖率：分子/分母 + 未填数量，和上面的数字在同一次渲染里。 */}
        <div data-testid="insights-savings-coverage" className="space-y-1">
          <p className="text-sm">
            填写覆盖率{" "}
            <span className="font-medium tabular-nums">
              {coverage.covered} / {coverage.total}
            </span>{" "}
            <span className="tabular-nums text-muted-foreground">（{percentLabel}）</span>
          </p>
          <Progress
            value={coverage.percent ?? 0}
            aria-label={`效率估算填写覆盖率 ${coverage.covered} / ${coverage.total}`}
          />
          <p className="text-xs text-muted-foreground">
            {coverage.uncovered > 0
              ? `${coverage.uncovered} 个工具未填写预计节省时长，未计入估算。`
              : "全部工具都已填写预计节省时长。"}
          </p>
        </div>

        {noData ? (
          <Alert variant="warning" data-testid="insights-savings-empty">
            <Info aria-hidden="true" />
            <AlertTitle>还没有工具填写预计节省时长</AlertTitle>
            <AlertDescription>
              目前没有任何工具参与估算，因此上方的「效率估算」没有数字可给。请作者在工具编辑页的
              「效率估算」一节填写单次使用预计节省的分钟数。
            </AlertDescription>
          </Alert>
        ) : lowCoverage ? (
          <Alert variant="warning" data-testid="insights-savings-low-coverage">
            <AlertTriangle aria-hidden="true" />
            <AlertTitle>覆盖率不足 {LOW_COVERAGE_PCT}%，数据不足以支撑结论</AlertTitle>
            <AlertDescription>
              只有 {coverage.covered} / {coverage.total} 个工具填写了预计节省时长，上面的数字
              高度依赖少数作者的估计，不要把它当成平台整体效率结论。
            </AlertDescription>
          </Alert>
        ) : null}
      </CardContent>
    </Card>
  );
}

export default InsightsPanel;
