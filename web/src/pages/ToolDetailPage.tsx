import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@/hooks/useAuth";
import { AlertCircle, Home, Lock, Pencil, RotateCcw } from "lucide-react";
import * as React from "react";
import { Link, useParams } from "react-router-dom";

import { getErrorMessage, isApiError } from "@/api/client";
import { readCount } from "@/api/engagement";
import { fetchToolDetail, toolDetailQueryKey } from "@/api/tools";
import type { ToolDetail, Visibility } from "@/api/types";
import { CopyButton } from "@/components/common/CopyButton";
import { ErrorState } from "@/components/common/ErrorState";
import { Markdown } from "@/components/common/Markdown";
import { PageSkeleton } from "@/components/common/PageSkeleton";
import { DownloadButton } from "@/components/tools/DownloadButton";
import {
  EngagementPermissionNote,
  FavoriteToggle,
  LikeToggle,
} from "@/components/tools/EngagementActions";
import { FileInfoCard } from "@/components/tools/FileInfoCard";
import { PromptViewer } from "@/components/tools/PromptViewer";
import { SkillPreview } from "@/components/tools/SkillPreview";
import { ToolImageGallery } from "@/components/tools/ToolImageGallery";
import { ToolTypeBadge } from "@/components/tools/ToolTypeBadge";
import { VersionTimeline } from "@/components/tools/VersionTimeline";
import { WebappPanel } from "@/components/tools/WebappPanel";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatCount, formatDateTime, formatRelativeTime } from "@/lib/format";
import { PAGE_CONTAINER } from "@/lib/layout";
import { recordRecentTool } from "@/lib/recentTools";
import { VISIBILITY_LABELS } from "@/lib/toolMeta";
import NotFoundPage from "@/pages/NotFoundPage";

const CONTAINER = `${PAGE_CONTAINER} py-6`;

/**
 * `/tools/:slug` — 工具详情（docs/04 §6.4）。
 *
 * 两条硬约束：
 * - 权限完全来自服务端 `detail.permissions`，前端不重算角色/可见性（docs/03 §3.4）。
 * - 无权访问时后端返回 **404**（不是 403），前端直接渲染 404 页，避免泄露资源存在性（验收 #10）。
 */
export default function ToolDetailPage() {
  const { slug } = useParams<{ slug: string }>();
  const detailQuery = useQuery({
    queryKey: toolDetailQueryKey(slug ?? ""),
    queryFn: ({ signal }) => fetchToolDetail(slug ?? "", signal),
    enabled: Boolean(slug),
    // docs/04 §9：工具详情 staleTime 5 分钟。
    staleTime: 5 * 60_000,
  });

  /*
   * M7 · F6「最近访问」：真正打开过详情页才算一次访问（只从 ⌘K 面板记录会漏掉
   * 从卡片墙点进来的绝大多数访问）。写入的是 `@/lib/recentTools` 的
   * {slug, name, at}，不含任何凭据或个人信息；写失败静默忽略。
   * 必须在下面的提前 return 之前调用，否则违反 hooks 规则。
   */
  const detailName = detailQuery.data?.name;
  React.useEffect(() => {
    if (!slug || !detailName) return;
    recordRecentTool({ slug, name: detailName });
  }, [slug, detailName]);

  if (!slug) return <NotFoundPage />;

  if (detailQuery.isPending) {
    return (
      <div className={CONTAINER}>
        <PageSkeleton variant="page" />
      </div>
    );
  }

  if (detailQuery.isError) {
    const error = detailQuery.error;
    if (isApiError(error) && error.code === "NOT_FOUND") return <NotFoundPage />;
    return (
      <div className={CONTAINER}>
        <ErrorState
          message={getErrorMessage(error)}
          onRetry={() => {
            void detailQuery.refetch();
          }}
        />
      </div>
    );
  }

  const detail = detailQuery.data;
  if (!detail) return <NotFoundPage />;

  const description = (detail.description_md ?? "").trim();

  return (
    <div data-testid="tool-detail" className={CONTAINER}>
      <Breadcrumb detail={detail} />

      <div className="mt-4 grid gap-6 lg:grid-cols-3">
        {/* 主内容 2/3；lg 以下单列，信息栏按 docs/04 §8.2 移到最前面 */}
        <div className="order-2 min-w-0 space-y-6 lg:order-1 lg:col-span-2">
          <ToolStatusAlerts detail={detail} />

          <ToolImageGallery
            images={detail.images}
            toolName={detail.name}
            toolType={detail.tool_type}
          />

          <header className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-2xl font-semibold tracking-tight">{detail.name}</h1>
              <ToolTypeBadge type={detail.tool_type} />
              <VisibilityBadge visibility={detail.visibility} />
              {detail.pending_version ? (
                <Badge
                  variant="warning"
                  data-testid="pending-version-badge"
                  title={`待审版本 ${detail.pending_version.version}`}
                >
                  新版待审
                </Badge>
              ) : null}
            </div>

            <p className="text-sm text-muted-foreground">{detail.summary}</p>

            {detail.tags.length > 0 ? (
              <ul className="flex flex-wrap items-center gap-1">
                {detail.tags.map((tag) => (
                  <li
                    key={tag}
                    className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs text-muted-foreground"
                  >
                    #{tag}
                  </li>
                ))}
              </ul>
            ) : null}

            {/*
              M8 · F7.2 / F8.1：标题区的收藏与点赞。
              - 收藏是**切换控件**：`aria-pressed` + 实心/空心星 + 「收藏 / 已收藏」文字，
                状态不靠颜色单独表达。
              - 点赞**只有整数计数**（CONTRACT §23.7 / D37）：没有平均分、没有星级。
              - 匿名访客这两个按钮**不渲染**（与「登录后可下载」同一套匿名策略），
                计数仍在页面上（信息栏 + 卡片）。
              - M9 · §25.1：`viewer` 同样**不渲染**这两个按钮（后端 `engagement_guard`
                不含 `viewer`，点下去必 403），改用一行说明告知是权限问题而不是坏了 ——
                与下载按钮「当前角色无下载权限」同一套做法。`EngagementPermissionNote`
                对匿名与有投票角色的账号都返回 `null`，所以这里不会多出一行字。
            */}
            <div className="flex flex-wrap items-center gap-2">
              <FavoriteToggle tool={detail} labeled />
              <LikeToggle tool={detail} labeled />
              <EngagementPermissionNote tool={detail} />
            </div>
          </header>

          <TypeSection detail={detail} />

          <section aria-labelledby="description-title" className="space-y-2">
            <h2 id="description-title" className="text-lg font-semibold">
              详情
            </h2>
            {description ? (
              <Markdown source={detail.description_md} />
            ) : (
              <p className="text-sm text-muted-foreground">作者还没有填写详细介绍。</p>
            )}
          </section>

          <VersionTimeline slug={detail.slug} fallbackCount={detail.version_count} />
        </div>

        <aside className="order-1 lg:order-2" aria-label="工具信息与操作">
          <ToolSidebar detail={detail} />
        </aside>
      </div>
    </div>
  );
}

/**
 * 面包屑「首页 / {分类} / {工具名}」（docs/04 §6.4）。前两级是链接，当前页用
 * `aria-current="page"`。分类链接回到门户的 `?category=` 筛选（门户状态全在 URL）。
 */
function Breadcrumb({ detail }: { detail: ToolDetail }) {
  return (
    <nav aria-label="面包屑" data-testid="tool-breadcrumb" className="text-sm">
      <ol className="flex flex-wrap items-center gap-1.5 text-muted-foreground">
        <li>
          <Link
            to="/"
            className="inline-flex items-center gap-1 rounded transition-colors hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50"
          >
            <Home aria-hidden="true" className="size-3.5" />
            首页
          </Link>
        </li>
        <li aria-hidden="true">/</li>
        {detail.category ? (
          <>
            <li>
              <Link
                to={`/?category=${encodeURIComponent(detail.category.slug)}`}
                className="rounded transition-colors hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50"
              >
                {detail.category.name}
              </Link>
            </li>
            <li aria-hidden="true">/</li>
          </>
        ) : (
          <>
            <li>未分类</li>
            <li aria-hidden="true">/</li>
          </>
        )}
        <li aria-current="page" className="min-w-0 truncate font-medium text-foreground">
          {detail.name}
        </li>
      </ol>
    </nav>
  );
}

/** 状态徽标永远「图标 + 文字」，不靠颜色单独表意（docs/04 §8.1）。 */
function VisibilityBadge({ visibility }: { visibility: Visibility }) {
  const label = VISIBILITY_LABELS[visibility];
  if (visibility === "public") {
    return (
      <Badge variant="secondary" data-testid="visibility-badge">
        {label}
      </Badge>
    );
  }
  return (
    <Badge variant="outline" data-testid="visibility-badge" title={`可见范围：${label}`}>
      <Lock aria-hidden="true" />
      {label}
    </Badge>
  );
}

/** 下架 / 驳回原因只对能看到这个工具的人有意义，原因由后端下发。 */
function ToolStatusAlerts({ detail }: { detail: ToolDetail }) {
  if (detail.status === "offline") {
    return (
      <Alert variant="destructive" data-testid="offline-reason">
        <AlertCircle aria-hidden="true" />
        <AlertTitle>该工具已下架</AlertTitle>
        <AlertDescription>{detail.offline_reason ?? "管理员未填写下架原因。"}</AlertDescription>
      </Alert>
    );
  }
  if (detail.status === "rejected") {
    return (
      <Alert variant="destructive" data-testid="reject-reason">
        <AlertCircle aria-hidden="true" />
        <AlertTitle>该工具未通过审核</AlertTitle>
        <AlertDescription>{detail.reject_reason ?? "审批人未填写驳回原因。"}</AlertDescription>
      </Alert>
    );
  }
  return null;
}

/**
 * 类型专属区块（docs/04 §6.4 类型差异化内容区），插在详情 Markdown 之上。
 * prompt / webapp 的字段只在 `tool_type` 匹配时才由后端下发。
 */
function TypeSection({ detail }: { detail: ToolDetail }) {
  switch (detail.tool_type) {
    case "file":
      return <FileInfoCard detail={detail} />;
    case "webapp":
      return <WebappPanel detail={detail} />;
    case "skill":
      return (
        <SkillPreview
          slug={detail.slug}
          version={detail.current_version?.version ?? null}
          skill={detail.skill}
          versionSkill={detail.current_version?.skill ?? null}
        />
      );
    case "prompt":
      return <PromptViewer prompt={detail.prompt} />;
  }
}

function InfoRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 py-2">
      <dt className="shrink-0 text-xs text-muted-foreground">{label}</dt>
      <dd className="min-w-0 text-right text-sm">{children}</dd>
    </div>
  );
}

/** 右侧信息栏：下载、版本、SHA256、元信息、分享与作者操作（docs/04 §6.4）。 */
function ToolSidebar({ detail }: { detail: ToolDetail }) {
  const { status } = useAuth();
  const version = detail.current_version;
  const sha256 = version?.file_sha256 ?? null;
  const updatedAt = detail.last_version_at ?? detail.updated_at;
  // SPA 内 `window.location.href` 就是当前详情页地址，刷新后即最新值。
  const shareUrl = window.location.href;
  const canManage = detail.permissions.can_manage_versions;

  /*
   * 禁用原因要区分「没权限」与「没登录」。
   *
   * 匿名访客（门户默认允许未登录浏览）若看到「当前角色无下载权限」会以为
   * 自己被禁用；实际只是没登录 —— 顶栏有登录入口，文案应当把人指过去。
   * 服务端对这两种情况都返回 404（不泄漏资源存在性），所以只能由前端说清。
   */
  const downloadDisabledReason =
    version === null
      ? "该工具暂无可用版本"
      : detail.permissions.can_download
        ? undefined
        : status === "unauthenticated"
          ? "登录后可下载"
          : "当前角色无下载权限";

  return (
    <div className="space-y-4 lg:sticky lg:top-20">
      <div className="space-y-2 rounded-xl border bg-card p-4">
        <DownloadButton
          slug={detail.slug}
          versionId={version?.id ?? null}
          disabled={!detail.permissions.can_download || version === null}
          disabledReason={downloadDisabledReason}
          fileName={version?.file_name ?? null}
          size={version?.file_size ?? null}
          ariaLabel={`下载 ${detail.name}`}
          testId="download-button"
        />

        <dl className="divide-y text-sm">
          <InfoRow label="当前版本">
            <span className="font-mono text-xs">{version?.version ?? "—"}</span>
          </InfoRow>
          <InfoRow label="SHA256">
            <span className="flex items-start justify-end gap-1">
              <span
                data-testid="sha256-value"
                className="min-w-0 break-all font-mono text-xs leading-5"
              >
                {sha256 ?? "—"}
              </span>
              {sha256 ? (
                <CopyButton value={sha256} label="复制 SHA256" className="shrink-0" />
              ) : null}
            </span>
          </InfoRow>
          <InfoRow label="更新时间">
            <time dateTime={updatedAt ?? undefined} title={formatDateTime(updatedAt)}>
              {formatRelativeTime(updatedAt)}
            </time>
          </InfoRow>
          <InfoRow label="作者">{detail.owner.display_name}</InfoRow>
          <InfoRow label="下载量">
            <span className="tabular-nums">{formatCount(detail.download_count)}</span>
          </InfoRow>
          <InfoRow label="浏览量">
            <span className="tabular-nums">{formatCount(detail.view_count)}</span>
          </InfoRow>
          {/*
            M8：收藏 / 点赞计数**始终可见**（匿名也是），按钮另有权限门 —— 这两行
            让匿名访客也能看到互动数据。字段缺失（后端未落地）时整行不渲染，
            避免显示编造的 0。
          */}
          {readCount(detail.favorite_count) !== null ? (
            <InfoRow label="收藏">
              <span className="tabular-nums">{formatCount(detail.favorite_count)}</span>
            </InfoRow>
          ) : null}
          {readCount(detail.like_count) !== null ? (
            <InfoRow label="点赞">
              <span className="tabular-nums">{formatCount(detail.like_count)}</span>
            </InfoRow>
          ) : null}
          <InfoRow label="分类">{detail.category?.name ?? "未分类"}</InfoRow>
        </dl>
      </div>

      <div className="space-y-2 rounded-xl border bg-card p-4">
        <h2 className="text-sm font-semibold">分享</h2>
        <div className="flex items-center gap-1">
          <span className="min-w-0 flex-1 truncate font-mono text-xs text-muted-foreground" title={shareUrl}>
            {shareUrl}
          </span>
          <CopyButton value={shareUrl} label="复制分享链接" className="shrink-0" />
        </div>
      </div>

      {detail.permissions.can_edit ? (
        <div className="flex flex-col gap-2">
          <Button asChild variant="outline">
            <Link to={`/me/tools/${detail.id}/edit`}>
              <Pencil aria-hidden="true" />
              编辑
            </Link>
          </Button>
          <Button asChild variant="outline">
            <Link to={`/me/tools/${detail.id}/versions`}>
              <RotateCcw aria-hidden="true" />
              版本管理
            </Link>
          </Button>
        </div>
      ) : canManage ? (
        <div className="flex flex-col gap-2">
          <Button asChild variant="outline">
            <Link to={`/me/tools/${detail.id}/versions`}>
              <RotateCcw aria-hidden="true" />
              版本管理
            </Link>
          </Button>
        </div>
      ) : null}
    </div>
  );
}
