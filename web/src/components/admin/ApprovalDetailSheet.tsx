import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Download, ExternalLink, Package } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { getErrorMessage } from "@/api/client";
import {
  createDownloadTicket,
  fetchSkillPreview,
  fetchToolDetail,
  skillPreviewQueryKey,
  toolDetailQueryKey,
} from "@/api/tools";
import type { ApprovalQueueItem, SkillPreview, ToolDetail } from "@/api/types";
import { SubmissionTypeBadge, WaitingBadge } from "@/components/admin/ApprovalQueue";
import { CopyButton } from "@/components/common/CopyButton";
import { Markdown } from "@/components/common/Markdown";
import { ToolTypeBadge } from "@/components/tools/ToolTypeBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { formatDateTime, formatFileSize } from "@/lib/format";
import { VISIBILITY_LABELS } from "@/lib/toolMeta";
import { cn } from "@/lib/utils";

/**
 * 审批详情（docs/04 §6.10）。同一个组件服务两种容器：
 *  - `xl` 及以上：内联在右侧栏（保留列表上下文，不跳转门户）
 *  - `xl` 以下：塞进 `Sheet` 抽屉
 *
 * 预览一律复用项目的公共渲染件（`Markdown` / `PromptViewer` 的排版），不重新实现渲染。
 *
 * **数据源分工**（CONTRACT §17.11）：列表与头部信息来自 `GET /admin/approvals`
 * （队列数据），而**内容预览**来自 `GET /tools/{slug}` —— 因为队列条目的
 * `pending_version` 不含 prompt 正文，也没有 webapp URL；后端 M3 已让该接口对
 * approver 开放 `pending` / `pending_update` / `offline`，于是四种类型都能预览。
 * 详情请求只在用户展开「预览包内容」时才发出，关闭/切换条目时不产生额外请求。
 */

/** 审批动作区（备注 / 理由 / 按钮）由页面注入，两种容器共用同一套状态。 */
export interface ApprovalDetailActions {
  note: string;
  onNoteChange: (value: string) => void;
  rejectReason: string;
  onRejectReasonChange: (value: string) => void;
  rejectError: string | null;
  showRejectInput: boolean;
  onApprove: () => void;
  onReject: () => void;
  onOffline: () => void;
  approvePending: boolean;
  rejectPending: boolean;
  /** 由页面持有的驳回理由框引用，`R` 快捷键与错误聚焦都靠它。 */
  rejectReasonRef: React.RefObject<HTMLTextAreaElement>;
}

export interface ApprovalDetailProps {
  item: ApprovalQueueItem;
  actions: ApprovalDetailActions;
  className?: string;
}

/**
 * 项目里没有 `ui/textarea` 原子件，而本次任务不允许新增 `components/ui/*`。
 * 这里用一个与 `Input` 视觉一致的本地 textarea，并把 label / 错误提示一起封装，
 * 保证 `aria-describedby` / `aria-invalid` 在两种容器中行为一致（docs/04 §8.1）。
 */
export function TextareaField({
  id,
  label,
  value,
  onChange,
  placeholder,
  rows = 3,
  error,
  testId,
  inputRef,
  hint,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  rows?: number;
  error?: string | null;
  testId?: string;
  inputRef?: React.RefObject<HTMLTextAreaElement>;
  hint?: string;
}) {
  const descriptionId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [descriptionId, errorId].filter(Boolean).join(" ") || undefined;

  return (
    <div className="grid gap-2">
      <label htmlFor={id} className="text-xs font-semibold text-muted-foreground">
        {label}
      </label>
      <textarea
        id={id}
        ref={inputRef}
        data-testid={testId}
        rows={rows}
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        className={cn(
          "w-full min-w-0 rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs transition-[color,box-shadow] outline-none",
          "placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50",
          "aria-invalid:border-destructive aria-invalid:ring-destructive/20 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30",
        )}
      />
      {hint ? (
        <p id={descriptionId} className="text-xs text-muted-foreground">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} role="alert" className="text-xs text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export function ApprovalDetailContent({ item, actions, className }: ApprovalDetailProps) {
  const [downloading, setDownloading] = React.useState(false);

  async function handleDownload() {
    if (!item.pending_version || downloading) return;
    setDownloading(true);
    try {
      const ticket = await createDownloadTicket(item.tool_slug, item.pending_version.id);
      const anchor = document.createElement("a");
      anchor.href = ticket.url;
      if (ticket.file_name) anchor.download = ticket.file_name;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      toast.success(`开始下载 ${ticket.file_name ?? item.tool_name}`);
    } catch (error) {
      toast.error(getErrorMessage(error));
    } finally {
      setDownloading(false);
    }
  }

  // 下架只对「已发布工具的新版本」有意义（docs/01 §4.1 状态机）。新工具的
  // 首次提交走驳回即可，队列里不会出现 approved 的新工具。
  const canOffline =
    item.submission_type === "new_version" &&
    (item.status === "pending_update" || item.status === "approved");

  return (
    <div className={cn("flex h-full flex-col", className)}>
      <div className="flex flex-col gap-2 border-b p-4">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-base font-semibold">{item.tool_name}</h2>
          <SubmissionTypeBadge type={item.submission_type} />
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <ToolTypeBadge type={item.tool_type} />
          <Badge variant="secondary">{VISIBILITY_LABELS[item.visibility]}</Badge>
          {item.category ? (
            <span className="text-muted-foreground">{item.category.name}</span>
          ) : null}
          <WaitingBadge hours={item.waiting_hours} />
        </div>
        {item.tags.length > 0 ? (
          <div className="flex flex-wrap gap-1">
            {item.tags.map((tag) => (
              <Badge key={tag} variant="outline" className="text-[11px]">
                {tag}
              </Badge>
            ))}
          </div>
        ) : null}
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        {item.summary ? (
          <section className="space-y-1">
            <h3 className="text-xs font-semibold text-muted-foreground">简介</h3>
            <p className="whitespace-pre-line text-sm">{item.summary}</p>
          </section>
        ) : null}

        <section className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-sm">
          <span className="text-muted-foreground">提交类型</span>
          <span>{item.submission_type === "new_tool" ? "新工具" : "新版本"}</span>

          <span className="text-muted-foreground">待审版本</span>
          <span className="tabular-nums">{item.pending_version?.version ?? "—"}</span>

          <span className="text-muted-foreground">当前版本</span>
          <span className="tabular-nums">{item.current_version?.version ?? "—"}</span>

          <span className="text-muted-foreground">提交人</span>
          <span>{item.owner?.display_name ?? "未知"}</span>

          <span className="text-muted-foreground">提交时间</span>
          <span title={item.submitted_at ?? undefined}>{formatDateTime(item.submitted_at)}</span>
        </section>

        <Separator />

        <section className="space-y-1">
          <h3 className="text-xs font-semibold text-muted-foreground">变更说明</h3>
          {item.pending_version?.changelog_md ? (
            <Markdown source={item.pending_version.changelog_md} />
          ) : (
            <p className="text-sm text-muted-foreground">未填写变更说明。</p>
          )}
        </section>

        <Separator />

        <section className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-xs font-semibold text-muted-foreground">包内容预览</h3>
            <div className="flex flex-wrap gap-2">
              <PackagePreviewButton item={item} />
              {item.pending_version ? (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={downloading}
                  onClick={handleDownload}
                >
                  <Download aria-hidden="true" className="size-4" />
                  {downloading ? "申请下载中…" : "下载包"}
                </Button>
              ) : null}
            </div>
          </div>
        </section>

        <Separator />

        {/* 批准：备注选填（FR-APPR-07）。 */}
        <TextareaField
          id="approval-note"
          label="批准备注（选填）"
          value={actions.note}
          onChange={actions.onNoteChange}
          placeholder="例如：已核对 changelog 与包内脚本"
          rows={2}
        />

        {actions.showRejectInput ? (
          <TextareaField
            id="reject-reason"
            label="驳回理由（必填，至少 5 个字）"
            value={actions.rejectReason}
            onChange={actions.onRejectReasonChange}
            placeholder="请写明问题所在，理由会原文展示给提交者"
            rows={3}
            error={actions.rejectError}
            testId="reject-reason-input"
            inputRef={actions.rejectReasonRef}
          />
        ) : null}
      </div>

      <div className="space-y-2 border-t p-4">
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            data-testid="approve-button"
            disabled={actions.approvePending}
            onClick={actions.onApprove}
          >
            {actions.approvePending ? "批准中…" : "批准"}
          </Button>
          <Button
            type="button"
            variant="destructive"
            data-testid="reject-button"
            disabled={actions.rejectPending}
            onClick={actions.onReject}
          >
            驳回
          </Button>
          {canOffline ? (
            <Button type="button" variant="outline" onClick={actions.onOffline}>
              下架
            </Button>
          ) : null}
        </div>
        {/* 键盘快捷键提示（docs/04 §6.10）。 */}
        <p className="text-xs text-muted-foreground">
          快捷键：<kbd className="rounded border px-1">J</kbd>/
          <kbd className="rounded border px-1">K</kbd> 切换条目 ·{" "}
          <kbd className="rounded border px-1">A</kbd> 批准 ·{" "}
          <kbd className="rounded border px-1">R</kbd> 填写驳回理由 ·{" "}
          <kbd className="rounded border px-1">Esc</kbd> 关闭
        </p>
      </div>
    </div>
  );
}

/**
 * 「预览包内容」按钮 + 懒加载面板（docs/04 §6.10）。
 *
 * 预览状态故意放在这个子组件里并通过 `key={item.tool_id}` 重挂载：
 * 切换条目时展开态自动回到折叠，既不用 effect 里 setState，也不会让
 * 上一条的 Skill 大 payload 跟着下一条显示。
 */
function PackagePreviewButton({ item }: { item: ApprovalQueueItem }) {
  const [expanded, setExpanded] = React.useState(false);
  const version = item.pending_version?.version ?? "";
  const pendingVersionId = item.pending_version?.id ?? null;

  const skillPreviewEnabled =
    item.tool_type === "skill" && expanded && pendingVersionId !== null && version.length > 0;
  const {
    data: preview,
    isFetching,
    isError,
    refetch,
  } = useQuery({
    queryKey: skillPreviewQueryKey(item.tool_slug, version),
    queryFn: ({ signal }) => fetchSkillPreview(item.tool_slug, version, signal),
    enabled: skillPreviewEnabled,
    staleTime: 5 * 60_000,
  });

  // 详情只在展开时拉（`GET /tools/{slug}` 对 approver 开放 pending/offline）。
  const {
    data: detail,
    isFetching: detailFetching,
    isError: detailFailed,
    refetch: refetchDetail,
  } = useQuery({
    queryKey: toolDetailQueryKey(item.tool_slug),
    queryFn: ({ signal }) => fetchToolDetail(item.tool_slug, signal),
    enabled: expanded,
    staleTime: 60_000,
  });

  return (
    <div className="w-full space-y-2">
      <Button
        type="button"
        variant="outline"
        size="sm"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        <Package aria-hidden="true" className="size-4" />
        {expanded ? "收起包内容" : "预览包内容"}
      </Button>
      {expanded ? (
        <div className="rounded-md border bg-muted/30 p-3">
          <PackagePreview
            item={item}
            detail={detail ?? null}
            detailLoading={detailFetching}
            detailError={detailFailed}
            onRetryDetail={() => void refetchDetail()}
            preview={preview ?? null}
            loading={isFetching}
            error={isError}
            onRetry={() => void refetch()}
          />
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">展开后按需加载；Skill 包预览可能较大。</p>
      )}
    </div>
  );
}

/** 详情接口失败的统一重试行（四种类型共用）。 */
function DetailRetry({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <AlertTriangle aria-hidden="true" className="size-3.5" />
      <span className="text-muted-foreground">内容预览加载失败。</span>
      <Button type="button" variant="ghost" size="xs" onClick={onRetry}>
        重试
      </Button>
    </div>
  );
}

function PackagePreview({
  item,
  detail,
  detailLoading,
  detailError,
  onRetryDetail,
  preview,
  loading,
  error,
  onRetry,
}: {
  item: ApprovalQueueItem;
  /** `GET /tools/{slug}` —— prompt 正文 / webapp URL / skill manifest 的来源。 */
  detail: ToolDetail | null;
  detailLoading: boolean;
  detailError: boolean;
  onRetryDetail: () => void;
  preview: SkillPreview | null;
  loading: boolean;
  error: boolean;
  onRetry: () => void;
}) {
  const detailPending = detailLoading && detail === null;
  const detailFailedToLoad = detailError && detail === null;
  if (item.tool_type === "file") {
    return (
      <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-muted-foreground">文件名</dt>
        <dd className="break-all font-mono">{item.pending_version?.file_name ?? "—"}</dd>
        <dt className="text-muted-foreground">大小</dt>
        <dd className="tabular-nums">{formatFileSize(item.pending_version?.file_size)}</dd>
        <dt className="text-muted-foreground">SHA256</dt>
        <dd className="break-all font-mono">
          {item.pending_version?.file_sha256?.slice(0, 16) ?? "—"}
        </dd>
        <dt className="text-muted-foreground">当前版本</dt>
        <dd className="font-mono">
          {detail?.current_version?.version ?? item.current_version?.version ?? "—"}
          {detail?.current_version?.file_size
            ? `（${formatFileSize(detail.current_version.file_size)}）`
            : ""}
        </dd>
      </dl>
    );
  }

  if (item.tool_type === "webapp") {
    if (detailPending) return <p className="text-xs text-muted-foreground">加载访问地址…</p>;
    if (detailFailedToLoad) return <DetailRetry onRetry={onRetryDetail} />;
    const url = detail?.webapp_url ?? null;
    if (!url) {
      return (
        <p className="text-xs text-muted-foreground">
          该在线工具没有填写访问地址，建议驳回并要求补充。
        </p>
      );
    }
    return (
      <div className="space-y-2 text-xs">
        <p className="text-muted-foreground">在线工具没有包体，审批时请确认访问地址可用：</p>
        <p className="flex items-center gap-2">
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 break-all font-mono text-primary underline-offset-4 hover:underline"
          >
            <ExternalLink aria-hidden="true" className="size-3.5 shrink-0" />
            {url}
          </a>
          <CopyButton value={url} label="复制访问地址" testId="approval-webapp-url-copy" />
        </p>
      </div>
    );
  }

  if (item.tool_type === "prompt") {
    if (detailPending) return <p className="text-xs text-muted-foreground">加载提示词正文…</p>;
    if (detailFailedToLoad) return <DetailRetry onRetry={onRetryDetail} />;
    const content = detail?.prompt?.content ?? "";
    if (content.trim() === "") {
      return <p className="text-xs text-muted-foreground">该提示词没有正文内容。</p>;
    }
    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs text-muted-foreground">
            提示词正文（{detail?.prompt?.char_count ?? content.length} 字）
          </span>
          <CopyButton value={content} label="复制提示词正文" testId="approval-prompt-copy" />
        </div>
        <pre
          data-testid="approval-prompt-content"
          className="max-h-72 overflow-auto rounded-md border bg-background p-3 font-mono text-xs whitespace-pre-wrap"
        >
          {content}
        </pre>
      </div>
    );
  }

  if (loading) return <p className="text-xs text-muted-foreground">加载包内容…</p>;

  if (error) {
    return (
      <div className="flex items-center gap-2 text-xs">
        <AlertTriangle aria-hidden="true" className="size-3.5" />
        <span className="text-muted-foreground">包内容预览加载失败。</span>
        <Button type="button" variant="ghost" size="xs" onClick={onRetry}>
          重试
        </Button>
      </div>
    );
  }
  if (!preview) return <p className="text-xs text-muted-foreground">暂无预览数据。</p>;

  return (
    <div className="space-y-2 text-xs">
      <p className="text-muted-foreground">
        共 {preview.file_tree.length} 个条目 · 合计 {formatFileSize(preview.total_size)}
        {preview.file_tree_truncated ? "（列表已截断）" : ""}
      </p>
      <ul className="max-h-56 space-y-0.5 overflow-y-auto font-mono">
        {preview.file_tree.map((entry) => (
          <li key={entry.path} className="flex items-center justify-between gap-2">
            <span className="truncate">
              {entry.is_dir ? "[目录]" : "[文件]"} {entry.path}
            </span>
            <span className="shrink-0 tabular-nums text-muted-foreground">
              {entry.is_dir ? "" : formatFileSize(entry.size)}
            </span>
          </li>
        ))}
      </ul>
      {preview.readme_md ? (
        <details className="rounded border bg-background p-2">
          <summary className="cursor-pointer">SKILL.md / README</summary>
          <Markdown source={preview.readme_md} className="mt-2" />
        </details>
      ) : null}
    </div>
  );
}

export default ApprovalDetailContent;
