import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Download, ExternalLink, Package } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { getErrorMessage } from "@/api/client";
import { createDownloadTicket, fetchSkillPreview, skillPreviewQueryKey } from "@/api/tools";
import type { ApprovalQueueItem, SkillPreview } from "@/api/types";
import { SubmissionTypeBadge, WaitingBadge } from "@/components/admin/ApprovalQueue";
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
 * 预览一律复用项目的公共渲染件（`Markdown`），不重新实现 Markdown / Skill 渲染。
 * 详情字段全部来自队列条目本身（`GET /admin/approvals`），不额外拉工具详情 ——
 * 审批接口已下发审批所需的全部信息，少一次请求也少一处越权面。
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

  const previewEnabled = item.tool_type === "skill" && expanded && pendingVersionId !== null;
  const {
    data: preview,
    isFetching,
    isError,
    refetch,
  } = useQuery({
    queryKey: skillPreviewQueryKey(item.tool_slug, version),
    queryFn: ({ signal }) => fetchSkillPreview(item.tool_slug, version, signal),
    enabled: previewEnabled && version.length > 0,
    staleTime: 5 * 60_000,
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

function PackagePreview({
  item,
  preview,
  loading,
  error,
  onRetry,
}: {
  item: ApprovalQueueItem;
  preview: SkillPreview | null;
  loading: boolean;
  error: boolean;
  onRetry: () => void;
}) {
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
      </dl>
    );
  }

  if (item.tool_type === "webapp") {
    return (
      <p className="flex items-start gap-2 text-xs text-muted-foreground">
        <ExternalLink aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
        在线工具没有包体，审批时请确认提交人填写的访问地址。M2 冻结接口里没有把地址放进审批队列，
        可在门户详情页核对。
      </p>
    );
  }

  if (item.tool_type === "prompt") {
    return (
      <p className="text-xs text-muted-foreground">
        提示词正文随版本内容审核；M2 的预览接口只覆盖 Skill 包（docs/03 §3.5），因此这里只展示
        变更说明，不渲染提示词全文。
      </p>
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
