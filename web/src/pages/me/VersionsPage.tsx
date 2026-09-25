import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Download,
  Loader2,
  Pencil,
  Plus,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import * as React from "react";
import { useNavigate, useParams } from "react-router-dom";import { toast } from "sonner";

import { getErrorMessage } from "@/api/client";
import {
  deleteVersion,
  fetchMyTool,
  fetchMyVersions,
  myToolDetailQueryKey,
  myVersionsQueryKey,
  patchVersion,
} from "@/api/me";
import { createDownloadTicket } from "@/api/tools";
import type { VersionStatus, VersionSummary } from "@/api/types";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { CopyButton } from "@/components/common/CopyButton";
import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { Markdown } from "@/components/common/Markdown";
import { PageSkeleton } from "@/components/common/PageSkeleton";
import { VersionUploadDialog } from "@/components/me/VersionUploadDialog";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/me/Textarea";
import { formatDateTime, formatFileSize, formatRelativeTime } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * FR-VER-08's documented default. The real value is the `version.history_limit`
 * system setting, which a normal user cannot read (`GET /admin/settings` is
 * superadmin-only), so this is hard-coded and flagged in the report.
 */
const HISTORY_LIMIT = 10;
/** "接近上限" threshold from docs/04 §6.8. */
const HISTORY_WARN_AT = 8;

type VersionBadgeVariant = "secondary" | "warning" | "success" | "destructive" | "outline";

const VERSION_STATUS_META: Record<
  VersionStatus,
  { label: string; variant: VersionBadgeVariant; dot: string }
> = {
  pending: { label: "待审", variant: "warning", dot: "bg-warning" },
  approved: { label: "当前", variant: "success", dot: "bg-success" },
  superseded: { label: "历史", variant: "secondary", dot: "bg-muted-foreground/50" },
  rejected: { label: "已驳回", variant: "destructive", dot: "bg-destructive" },
  purged: { label: "已归档", variant: "outline", dot: "bg-muted-foreground/30" },
};

function statusMeta(version: VersionSummary) {
  const meta = VERSION_STATUS_META[version.status];
  if (version.status === "approved" && !version.is_current) {
    return { ...meta, label: "已通过" };
  }
  return meta;
}

/**
 * `/me/tools/:id/versions` — 版本管理（docs/04 §6.8）。
 *
 * Left: the version timeline. Right: the selected version's detail.
 * `fetchMyVersions` returns `VersionSummary[]`, which carries only the first 16
 * chars of the SHA256 — the full hash lives on the detail endpoint, so the UI
 * says so instead of inventing one (FR-VER-06).
 */
export default function VersionsPage() {
  const params = useParams<{ id: string }>();
  const toolId = Number.parseInt(params.id ?? "", 10);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [selectedId, setSelectedId] = React.useState<number | null>(null);
  const [uploadOpen, setUploadOpen] = React.useState(false);
  const [pendingDelete, setPendingDelete] = React.useState<VersionSummary | null>(null);
  const [editing, setEditing] = React.useState(false);
  const [changelogDraft, setChangelogDraft] = React.useState("");

  const toolQuery = useQuery({
    queryKey: myToolDetailQueryKey(toolId),
    queryFn: ({ signal }) => fetchMyTool(toolId, signal),
    enabled: Number.isFinite(toolId),
  });

  const versionsQuery = useQuery({
    queryKey: myVersionsQueryKey(toolId),
    queryFn: ({ signal }) => fetchMyVersions(toolId, signal),
    enabled: Number.isFinite(toolId),
  });

  const versions = versionsQuery.data ?? [];
  const selected =
    versions.find((version) => version.id === selectedId) ?? versions[0] ?? null;

  // Re-seed the changelog editor when the selection changes. Done during render
  // (the "store information from previous renders" pattern) instead of in an
  // effect, which would cost an extra render pass per selection.
  const [syncedVersionId, setSyncedVersionId] = React.useState<number | null>(selected?.id ?? null);
  if (syncedVersionId !== (selected?.id ?? null)) {
    setSyncedVersionId(selected?.id ?? null);
    setEditing(false);
    setChangelogDraft(selected?.changelog_md ?? "");
  }

  const patchMutation = useMutation({
    mutationFn: (payload: { version: string; changelog_md: string }) =>
      patchVersion(toolId, payload.version, { changelog_md: payload.changelog_md }),
    onSuccess: () => {
      toast.success("变更说明已更新");
      setEditing(false);
      void queryClient.invalidateQueries({ queryKey: myVersionsQueryKey(toolId) });
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  });

  const deleteMutation = useMutation({
    mutationFn: (version: string) => deleteVersion(toolId, version),
    onSuccess: () => {
      toast.success("版本已删除");
      setPendingDelete(null);
      void queryClient.invalidateQueries({ queryKey: myVersionsQueryKey(toolId) });
      void queryClient.invalidateQueries({ queryKey: myToolDetailQueryKey(toolId) });
    },
    onError: (error) => {
      toast.error(getErrorMessage(error));
      setPendingDelete(null);
    },
  });

  async function handleDownload(version: VersionSummary) {
    const slug = toolQuery.data?.slug;
    if (!slug) {
      toast.error("工具信息尚未加载完成");
      return;
    }
    try {
      // Same ticket flow as the portal detail page: a bearer token cannot ride
      // on a plain <a href> (docs/04 §6.4 / §7.3).
      const ticket = await createDownloadTicket(slug, version.id);
      const anchor = document.createElement("a");
      anchor.href = ticket.url;
      if (ticket.file_name) anchor.download = ticket.file_name;
      anchor.rel = "noopener";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      toast.success(`开始下载 ${ticket.file_name ?? version.version}`);
    } catch (error) {
      toast.error(getErrorMessage(error));
    }
  }

  if (!Number.isFinite(toolId)) {
    return (
      <div className="mx-auto w-full max-w-3xl px-4 py-10 sm:px-6 lg:px-8">
        <ErrorState title="工具不存在" message="URL 中的工具 ID 无效。" />
      </div>
    );
  }

  if (toolQuery.isError || versionsQuery.isError) {
    const error = toolQuery.error ?? versionsQuery.error;
    return (
      <div className="mx-auto w-full max-w-3xl px-4 py-10 sm:px-6 lg:px-8">
        <ErrorState
          message={getErrorMessage(error)}
          onRetry={() => {
            void toolQuery.refetch();
            void versionsQuery.refetch();
          }}
        />
      </div>
    );
  }

  const tool = toolQuery.data;
  if (!tool || toolQuery.isPending || versionsQuery.isPending) {
    return (
      <div className="mx-auto w-full max-w-5xl px-4 py-10 sm:px-6 lg:px-8">
        <PageSkeleton variant="page" />
      </div>
    );
  }

  const historyCount = versions.filter((version) => version.status === "superseded").length;
  const nearLimit = historyCount >= HISTORY_WARN_AT;
  const canUpload = tool.permissions.can_manage_versions;

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-4 sm:px-6 lg:px-8">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="mb-2 -ml-2"
        onClick={() => navigate("/me/tools")}
      >
        <ArrowLeft aria-hidden="true" className="size-4" />
        返回我的工具
      </Button>

      <header className="flex flex-wrap items-center justify-between gap-3 pb-4">
        <div className="min-w-0">
          <h1 className="truncate text-xl font-semibold">版本管理 · {tool.name}</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            当前版本{" "}
            <span className="font-mono">{tool.current_version?.version ?? "—"}</span>
            {tool.pending_version ? (
              <>
                {" · 待审版本 "}
                <span className="font-mono">{tool.pending_version.version}</span>
              </>
            ) : null}
          </p>
        </div>
        <Button
          type="button"
          disabled={!canUpload}
          onClick={() => setUploadOpen(true)}
          title={canUpload ? undefined : "当前状态下不可上传新版本"}
        >
          <Plus aria-hidden="true" className="size-4" />
          上传新版本
        </Button>
      </header>

      <Alert
        className={cn(
          "mb-4",
          nearLimit && "border-amber-300/60 bg-amber-50/60 dark:bg-amber-950/20",
        )}
      >
        {nearLimit ? <TriangleAlert aria-hidden="true" /> : <Download aria-hidden="true" />}
        <AlertTitle>
          历史版本保留 {HISTORY_LIMIT} 份，当前 {historyCount} 份
        </AlertTitle>
        <AlertDescription>
          {nearLimit
            ? `再上传 ${HISTORY_LIMIT - historyCount} 个版本将开始归档最旧的版本（FR-VER-08）。`
            : "超出上限后最旧的历史版本会被归档（存储文件删除，记录保留）。"}
        </AlertDescription>
      </Alert>

      {versions.length === 0 ? (
        <EmptyState
          title="还没有任何版本"
          description="上传第一个版本后才能提交审批。"
          action={
            <Button type="button" disabled={!canUpload} onClick={() => setUploadOpen(true)}>
              <Plus aria-hidden="true" className="size-4" />
              上传新版本
            </Button>
          }
        />
      ) : (
        <div className="grid gap-4 lg:grid-cols-[22rem_minmax(0,1fr)]">
          {/* ---- timeline ---- */}
          <ol className="space-y-1" data-testid="version-timeline" aria-label="版本时间线">
            {versions.map((version) => {
              const meta = statusMeta(version);
              const active = selected?.id === version.id;
              return (
                <li key={version.id}>
                  <button
                    type="button"
                    aria-current={active ? "true" : undefined}
                    onClick={() => setSelectedId(version.id)}
                    className={cn(
                      "flex w-full items-start gap-2 rounded-lg border px-3 py-2 text-left transition-colors",
                      active ? "border-primary bg-primary/5" : "border-transparent hover:bg-accent/50",
                    )}
                  >
                    <span
                      aria-hidden="true"
                      className={cn("mt-1.5 size-2 shrink-0 rounded-full", meta.dot)}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-1.5">
                        <span className="font-mono text-sm font-medium">{version.version}</span>
                        <Badge variant={meta.variant}>{meta.label}</Badge>
                      </span>
                      <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                        {version.uploaded_by?.display_name ?? "—"} ·{" "}
                        <time
                          dateTime={version.created_at ?? undefined}
                          title={formatDateTime(version.created_at)}
                        >
                          {formatRelativeTime(version.created_at)}
                        </time>
                        {version.file_size !== null ? ` · ${formatFileSize(version.file_size)}` : ""}
                      </span>
                      {version.file_sha256_short ? (
                        <span className="mt-0.5 block truncate font-mono text-xs text-muted-foreground">
                          {version.file_sha256_short}…
                        </span>
                      ) : null}
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>

          {/* ---- detail ---- */}
          <Card className="min-w-0">
            <CardHeader>
              <CardTitle className="flex flex-wrap items-center gap-2">
                <span className="font-mono">{selected?.version}</span>
                {selected ? (
                  <Badge variant={statusMeta(selected).variant}>{statusMeta(selected).label}</Badge>
                ) : null}
              </CardTitle>
              <CardDescription>
                {selected?.approved_at
                  ? `通过时间 ${formatDateTime(selected.approved_at)}`
                  : `上传于 ${formatDateTime(selected?.created_at)}`}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <dl className="grid gap-2 text-sm sm:grid-cols-2">
                <div>
                  <dt className="text-xs text-muted-foreground">文件名</dt>
                  <dd className="truncate">{selected?.file_name ?? "—"}</dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">大小</dt>
                  <dd>{formatFileSize(selected?.file_size)}</dd>
                </div>
                <div className="sm:col-span-2">
                  <dt className="text-xs text-muted-foreground">SHA256（前 16 位）</dt>
                  <dd className="flex items-center gap-1">
                    <span className="font-mono text-xs">
                      {selected?.file_sha256_short ?? "—"}
                    </span>
                    {selected?.file_sha256_short ? (
                      <CopyButton value={selected.file_sha256_short} label="复制 SHA256 前 16 位" />
                    ) : null}
                  </dd>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    版本列表只返回前 16 位；完整哈希请在不重新上传的前提下按 FR-VER-06
                    约定从下载票据获取。
                  </p>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">上传者</dt>
                  <dd>{selected?.uploaded_by?.display_name ?? "—"}</dd>
                </div>
              </dl>

              <Separator />

              <div className="space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="text-sm font-medium">变更说明</h3>
                  {selected &&
                  (selected.status === "pending" || selected.status === "rejected") &&
                  !editing ? (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        setChangelogDraft(selected.changelog_md);
                        setEditing(true);
                      }}
                    >
                      <Pencil aria-hidden="true" className="size-4" />
                      编辑变更说明
                    </Button>
                  ) : null}
                </div>

                {editing && selected ? (
                  <div className="space-y-2">
                    <Textarea
                      rows={6}
                      value={changelogDraft}
                      aria-label="变更说明"
                      className="font-mono text-sm"
                      onChange={(event) => setChangelogDraft(event.target.value)}
                    />
                    <div className="flex gap-2">
                      <Button
                        type="button"
                        size="sm"
                        disabled={patchMutation.isPending}
                        onClick={() =>
                          patchMutation.mutate({
                            version: selected.version,
                            changelog_md: changelogDraft,
                          })
                        }
                      >
                        {patchMutation.isPending ? (
                          <>
                            <Loader2 aria-hidden="true" className="size-4 animate-spin" />
                            保存中…
                          </>
                        ) : (
                          "保存变更说明"
                        )}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => setEditing(false)}
                      >
                        取消
                      </Button>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      只有未通过审批的版本可以修改变更说明（FR-VER-11）。
                    </p>
                  </div>
                ) : selected?.changelog_md ? (
                  <Markdown source={selected.changelog_md} />
                ) : (
                  <p className="text-sm text-muted-foreground">该版本没有填写变更说明。</p>
                )}
              </div>

              {selected?.status === "rejected" ? (
                <Alert variant="destructive">
                  <TriangleAlert aria-hidden="true" />
                  <AlertTitle>该版本已被驳回</AlertTitle>
                  <AlertDescription className="space-y-2">
                    <p>{selected.reject_reason ?? "审批人未填写驳回理由。"}</p>
                    <Button
                      type="button"
                      size="sm"
                      onClick={() => navigate(`/me/tools/${toolId}/edit`)}
                    >
                      修改后重新提交
                    </Button>
                  </AlertDescription>
                </Alert>
              ) : null}

              {selected?.status === "purged" ? (
                <Alert>
                  <AlertTitle>该版本已归档</AlertTitle>
                  <AlertDescription>
                    超出历史保留上限后存储文件已被删除（FR-VER-08），因此不可下载。
                  </AlertDescription>
                </Alert>
              ) : null}

              <div className="flex flex-wrap items-center gap-2">
                <Button
                  type="button"
                  disabled={!selected?.can_download}
                  title={selected?.can_download ? undefined : "待审、已驳回或已归档的版本不可下载"}
                  onClick={() => selected && void handleDownload(selected)}
                >
                  <Download aria-hidden="true" className="size-4" />
                  下载此版本
                </Button>
                {selected &&
                (selected.status === "pending" || selected.status === "rejected") ? (
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setPendingDelete(selected)}
                  >
                    <Trash2 aria-hidden="true" className="size-4" />
                    删除该版本
                  </Button>
                ) : null}
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      <VersionUploadDialog
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        tool={tool}
        onUploaded={(result) => {
          toast.success(
            `新版本 ${result.version} 已提交审批。当前版本 ${
              tool.current_version?.version ?? "—"
            } 继续对外提供服务。`,
          );
          setSelectedId(result.id);
          void queryClient.invalidateQueries({ queryKey: myVersionsQueryKey(toolId) });
          void queryClient.invalidateQueries({ queryKey: myToolDetailQueryKey(toolId) });
        }}
      />

      <ConfirmDialog
        open={pendingDelete !== null}
        title={`删除版本 ${pendingDelete?.version ?? ""}？`}
        description="只有尚未通过审批的版本可以删除；当前版本不可删除，需要停用请走下架流程（FR-VER-09/10）。"
        confirmLabel="删除版本"
        pending={deleteMutation.isPending}
        onOpenChange={(open) => {
          if (!open) setPendingDelete(null);
        }}
        onConfirm={() => {
          if (pendingDelete) deleteMutation.mutate(pendingDelete.version);
        }}
      />

      {tool.status === "offline" ? (
        <p className="pt-4 text-xs text-muted-foreground">
          该工具当前已下架：{tool.offline_reason ?? "未填写下架理由"}。
        </p>
      ) : (
        <p className="pt-4 text-xs text-muted-foreground">
          需要修改版本内容？用「上传新版本」创建新版本，已通过审批的版本内容不可修改（FR-VER-11）。
        </p>
      )}

      {toolQuery.data ? (
        <p className="pt-1 text-xs text-muted-foreground">
          共 {versions.length} 个版本，其中历史版本 {historyCount} 份。
        </p>
      ) : null}
    </div>
  );
}
