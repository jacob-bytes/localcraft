import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight } from "lucide-react";
import * as React from "react";

import { getErrorMessage } from "@/api/client";
import { fetchToolVersions, toolVersionsQueryKey } from "@/api/tools";
import type { VersionSummary } from "@/api/types";
import { ErrorState } from "@/components/common/ErrorState";
import { Markdown } from "@/components/common/Markdown";
import { PageSkeleton } from "@/components/common/PageSkeleton";
import { DownloadButton } from "@/components/tools/DownloadButton";
import { Badge } from "@/components/ui/badge";
import { formatDateTime, formatFileSize, formatRelativeTime } from "@/lib/format";
import { cn } from "@/lib/utils";

type BadgeVariant = React.ComponentProps<typeof Badge>["variant"];

interface StatusDisplay {
  label: string;
  variant: BadgeVariant;
  /** 时间线圆点色 —— 与文字徽标同时出现，绝不只靠颜色区分（docs/04 §8.1）。 */
  dotClass: string;
}

/**
 * 五种状态必须视觉可区分（docs/04 §6.4 版本历史）：
 * 当前（success）/ 待审（warning）/ 历史（中性）/ 已驳回（destructive）/ 已归档（outline）。
 * `purged` 版本**保留在列表里**（用户需要看到历史），只是禁用下载。
 *
 * ★ 状态点的颜色一律走**语义令牌**（`bg-warning` / `bg-success` / `bg-destructive` /
 * `bg-muted-foreground`），不要写调色板色（`bg-amber-500` / `bg-emerald-500`）。
 * 两处这样做过，结果是同一件事有两种来源：
 *   - `pages/me/VersionsPage.tsx` 对**同样的**"待审 / 当前"用 `bg-warning` / `bg-success`；
 *   - `components/tools/WebappHealthMark.tsx` 的注释也把这条约定写明了。
 * 调色板色不随主题变（要人工补 `dark:`），语义令牌由 CSS 变量在明暗两态各自取值，
 * 所以换主题时不需要（也不该）在每个站点再写一遍。
 */
function statusDisplay(version: VersionSummary): StatusDisplay {
  if (version.status === "purged") {
    return { label: "已归档", variant: "outline", dotClass: "bg-muted-foreground/50" };
  }
  if (version.status === "pending") {
    return { label: "待审", variant: "warning", dotClass: "bg-warning" };
  }
  if (version.status === "rejected") {
    return { label: "已驳回", variant: "destructive", dotClass: "bg-destructive" };
  }
  if (version.status === "approved" && version.is_current) {
    return { label: "当前", variant: "success", dotClass: "bg-success" };
  }
  return { label: "历史", variant: "secondary", dotClass: "bg-muted-foreground/60" };
}

function downloadState(version: VersionSummary): { disabled: boolean; reason: string } {
  if (version.status === "purged") {
    return { disabled: true, reason: "该版本已超出保留期限" };
  }
  if (!version.can_download) {
    return { disabled: true, reason: "当前角色无下载权限" };
  }
  return { disabled: false, reason: "" };
}

export interface VersionTimelineProps {
  slug: string;
  /** `detail.version_count` — 请求返回前先用它显示数量。 */
  fallbackCount: number;
}

/** 版本历史时间线（docs/04 §6.4）。版本列表是裸数组（docs/03 §3.4）。 */
export function VersionTimeline({ slug, fallbackCount }: VersionTimelineProps) {
  const versionsQuery = useQuery({
    queryKey: toolVersionsQueryKey(slug),
    queryFn: ({ signal }) => fetchToolVersions(slug, signal),
    staleTime: 5 * 60_000,
  });

  const [expanded, setExpanded] = React.useState<readonly number[]>([]);

  const versions = versionsQuery.data ?? [];
  const total = versionsQuery.data ? versions.length : fallbackCount;

  const toggleChangelog = (id: number) => {
    setExpanded((previous) =>
      previous.includes(id) ? previous.filter((item) => item !== id) : [...previous, id],
    );
  };

  return (
    <section aria-labelledby="version-history-title" className="space-y-4">
      <h2 id="version-history-title" className="text-lg font-semibold">
        版本历史 ({total})
      </h2>

      {versionsQuery.isPending ? (
        <PageSkeleton variant="list" count={3} />
      ) : versionsQuery.isError ? (
        <ErrorState
          message={getErrorMessage(versionsQuery.error)}
          onRetry={() => {
            void versionsQuery.refetch();
          }}
        />
      ) : versions.length === 0 ? (
        <p className="text-sm text-muted-foreground">该工具还没有上传版本。</p>
      ) : (
        <ol className="relative space-y-5 before:absolute before:bottom-2 before:left-[3px] before:top-2 before:w-px before:bg-border">
          {versions.map((version) => {
            const status = statusDisplay(version);
            const download = downloadState(version);
            const changelog = (version.changelog_md ?? "").trim();
            const isExpanded = expanded.includes(version.id);

            return (
              <li
                key={version.id}
                data-testid="version-item"
                data-version={version.version}
                data-status={version.status}
                data-current={version.status === "approved" && version.is_current ? "true" : undefined}
                className={cn(
                  "relative space-y-2 pl-5",
                  // V5（docs/12 §6.2）：当前版本是用户最关心的那一条，
                  // 但此前只靠徽标文字区分，在长列表里不够显眼。
                  // 加一条左侧强调条 + 极淡底色，扫视时一眼能定位。
                  version.status === "approved" &&
                    version.is_current &&
                    "-ml-2 rounded-r-md border-l-2 border-success bg-success/[0.04] py-1.5 pr-2 pl-4",
                )}
              >
                <span
                  aria-hidden="true"
                  className={cn(
                    "absolute left-0 top-1.5 size-2 rounded-full ring-4 ring-background",
                    // 当前版本的圆点用成功色 ring，与左侧强调条呼应
                    version.status === "approved" && version.is_current && "ring-success/15",
                    status.dotClass,
                  )}
                />

                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="font-mono text-sm font-medium">{version.version}</span>
                  <Badge variant={status.variant} data-status={version.status}>
                    {status.label}
                  </Badge>
                  <time
                    dateTime={version.created_at ?? undefined}
                    title={formatDateTime(version.created_at)}
                    className="text-xs text-muted-foreground"
                  >
                    {formatRelativeTime(version.created_at)}
                  </time>
                  <span className="text-xs text-muted-foreground tabular-nums">
                    {formatFileSize(version.file_size)}
                  </span>
                  <div className="ml-auto">
                    <DownloadButton
                      slug={slug}
                      versionId={version.id}
                      disabled={download.disabled}
                      disabledReason={download.reason}
                      fileName={version.file_name}
                      label="下载"
                      variant="outline"
                      ariaLabel={`下载版本 ${version.version}`}
                      testId="version-download"
                      version={version.version}
                    />
                  </div>
                </div>

                <p className="text-xs text-muted-foreground">
                  上传者：{version.uploaded_by?.display_name ?? "—"}
                  {version.approved_at ? ` · 通过于 ${formatDateTime(version.approved_at)}` : ""}
                  {version.purged_at ? ` · 归档于 ${formatDateTime(version.purged_at)}` : ""}
                </p>

                {version.status === "rejected" && version.reject_reason ? (
                  <p className="rounded-md border border-destructive/30 bg-destructive/5 px-2 py-1 text-xs text-destructive">
                    驳回原因：{version.reject_reason}
                  </p>
                ) : null}

                {changelog ? (
                  <div className="space-y-1">
                    <button
                      type="button"
                      aria-expanded={isExpanded}
                      aria-controls={`version-changelog-${version.id}`}
                      onClick={() => toggleChangelog(version.id)}
                      className="inline-flex items-center gap-1 rounded text-xs font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50"
                    >
                      {isExpanded ? (
                        <ChevronDown aria-hidden="true" className="size-3.5" />
                      ) : (
                        <ChevronRight aria-hidden="true" className="size-3.5" />
                      )}
                      变更说明
                    </button>
                    {isExpanded ? (
                      <div
                        id={`version-changelog-${version.id}`}
                        data-testid="version-changelog"
                        className="rounded-md border bg-muted/30 px-3 py-2"
                      >
                        <Markdown source={changelog} className="prose-sm" />
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
