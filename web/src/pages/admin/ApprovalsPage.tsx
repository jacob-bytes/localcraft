import { Inbox } from "lucide-react";
import * as React from "react";
import { useSearchParams } from "react-router-dom";

import { getErrorMessage } from "@/api/client";
import { ApprovalDetailContent, TextareaField } from "@/components/admin/ApprovalDetailSheet";
import { ApprovalQueue, type ApprovalTypeFilter } from "@/components/admin/ApprovalQueue";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { ErrorState } from "@/components/common/ErrorState";
import { PageSkeleton } from "@/components/common/PageSkeleton";
import { Pagination } from "@/components/common/Pagination";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { APPROVAL_PAGE_SIZE, useApprovalWorkflow } from "@/pages/admin/useApprovalWorkflow";

const FILTER_VALUES: readonly ApprovalTypeFilter[] = ["all", "new_tool", "new_version"];

function isTypeFilter(value: string | null): value is ApprovalTypeFilter {
  return value !== null && (FILTER_VALUES as readonly string[]).includes(value);
}

/**
 * 与 Tailwind 的 `xl` 断点（80rem = 1280px）保持一致，用于「内联右栏 vs 抽屉」的
 * 必要互斥渲染。两份详情同时挂载会带来重复的可访问区域和 Radix 的滚动锁，
 * 所以必须先判断视口再决定渲染哪一个，而不是用 CSS 隐藏。
 *
 * `useSyncExternalStore` 是订阅 `matchMedia` 的官方写法：没有 effect 里的
 * setState，也不会在 SSR/首帧出现 hydration 抖动。
 */
const XL_QUERY = "(min-width: 1280px)";

function subscribeToMediaQuery(query: string, onChange: () => void): () => void {
  const list = window.matchMedia(query);
  list.addEventListener("change", onChange);
  return () => list.removeEventListener("change", onChange);
}

function useMediaQuery(query: string): boolean {
  return React.useSyncExternalStore(
    React.useCallback((onChange: () => void) => subscribeToMediaQuery(query, onChange), [query]),
    () => window.matchMedia(query).matches,
    () => false,
  );
}

/**
 * `/admin/approvals` —— 审批队列（docs/04 §6.10）。
 *
 * 队列按服务端顺序（先提交先处理）渲染，前端只做 `submission_type` 的
 * 客户端分段筛选。业务规则都在 `useApprovalWorkflow` 里，这里只负责渲染：
 * `xl` 以上内联右栏，`xl` 以下用 `Sheet` 抽屉，两者都是同一个详情组件，
 * 且都**不跳转门户**。
 */
export default function ApprovalsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const rawFilter = searchParams.get("type");
  const typeFilter: ApprovalTypeFilter = isTypeFilter(rawFilter) ? rawFilter : "all";
  const parsedPage = Number.parseInt(searchParams.get("page") ?? "1", 10);
  const page = Number.isFinite(parsedPage) && parsedPage >= 1 ? parsedPage : 1;
  const isWide = useMediaQuery(XL_QUERY);

  const setPage = React.useCallback(
    (nextPage: number) => {
      setSearchParams(
        (current) => {
          const next = new URLSearchParams(current);
          if (nextPage > 1) next.set("page", String(nextPage));
          else next.delete("page");
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const workflow = useApprovalWorkflow({
    typeFilter,
    page,
    onPageOutOfRange: setPage,
  });
  const {
    queueQuery,
    total,
    pages,
    visibleItems,
    selectedItem,
    selectedToolId,
    setSelectedToolId,
    checkedIds,
    setCheckedIds,
    detailActions,
  } = workflow;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-xl font-semibold tracking-tight">审批队列</h1>
        <p className="text-xs text-muted-foreground">
          按提交时间正序（先提交先处理）· 快捷键 J/K 切换、A 批准、R 填驳回理由、Esc 关闭
        </p>
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_400px]">
        <div className="min-w-0">
          {queueQuery.isPending ? (
            <PageSkeleton variant="list" />
          ) : queueQuery.isError ? (
            <ErrorState
              message={getErrorMessage(queueQuery.error)}
              onRetry={() => void queueQuery.refetch()}
            />
          ) : (
            <>
              <ApprovalQueue
                items={visibleItems}
                total={total}
                typeFilter={typeFilter}
                onTypeFilterChange={(value) =>
                  setSearchParams(
                    (current) => {
                      const next = new URLSearchParams(current);
                      if (value === "all") next.delete("type");
                      else next.set("type", value);
                      next.delete("page");
                      return next;
                    },
                    { replace: true },
                  )
                }
                selectedToolId={selectedToolId}
                onSelect={setSelectedToolId}
                checkedIds={checkedIds}
                onToggleChecked={(toolId, isChecked) =>
                  setCheckedIds((current) =>
                    isChecked
                      ? current.includes(toolId)
                        ? current
                        : [...current, toolId]
                      : current.filter((id) => id !== toolId),
                  )
                }
                onBatchApprove={workflow.batchApproveSelected}
                batchPending={workflow.batchPending}
              />

              {total > APPROVAL_PAGE_SIZE ? (
                <div className="mt-4 space-y-2">
                  <Pagination
                    page={page}
                    pages={pages}
                    onPageChange={setPage}
                  />
                  <p className="text-center text-xs text-muted-foreground">
                    第 {page} 页 / 共 {total} 条待处理（先提交先处理，越靠后越旧）
                  </p>
                </div>
              ) : null}
            </>
          )}
        </div>

        {/* xl 以上：内联右栏，保留列表上下文（docs/04 §6.10）。 */}
        <aside className="hidden xl:block">
          {selectedItem ? (
            <div
              data-testid="approval-detail"
              className="sticky top-20 max-h-[calc(100vh-6rem)] overflow-hidden rounded-xl border bg-card"
            >
              {/* key 让「包内容预览」的展开态随条目重置，不用在 effect 里 setState。 */}
              <ApprovalDetailContent
                key={selectedItem.tool_id}
                item={selectedItem}
                actions={detailActions}
              />
            </div>
          ) : (
            <div className="sticky top-20 rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">
              <Inbox aria-hidden="true" className="mx-auto mb-2 size-6" />
              从左侧选择一条待审内容查看详情
            </div>
          )}
        </aside>
      </div>

      {/* xl 以下才挂载抽屉：同一内容组件，不跳转门户。 */}
      {isWide ? null : (
        <Sheet
          open={selectedItem !== null}
          onOpenChange={(open) => {
            if (!open) setSelectedToolId(null);
          }}
        >
          <SheetContent
            side="right"
            data-testid="approval-detail-sheet"
            className="w-full overflow-hidden p-0 sm:max-w-lg"
          >
            <SheetHeader className="sr-only">
              <SheetTitle>{selectedItem?.tool_name ?? "审批详情"}</SheetTitle>
              <SheetDescription>审批详情与批准、驳回操作</SheetDescription>
            </SheetHeader>
            {selectedItem ? (
              <ApprovalDetailContent
                key={selectedItem.tool_id}
                item={selectedItem}
                actions={detailActions}
              />
            ) : null}
          </SheetContent>
        </Sheet>
      )}

      {/* 驳回二次确认：展示理由原文（docs/04 §6.10）。 */}
      <ConfirmDialog
        open={workflow.rejectTarget !== null}
        onOpenChange={(open) => {
          if (!open) workflow.setRejectTarget(null);
        }}
        title="确认驳回该提交？"
        description={
          <div className="space-y-2">
            <p>提交者会收到以下理由，驳回后需重新提交才能再次进入审批队列。</p>
            <blockquote className="rounded border bg-muted/40 p-2 text-sm whitespace-pre-wrap">
              {workflow.rejectReason}
            </blockquote>
          </div>
        }
        confirmLabel="确认驳回"
        pending={workflow.rejectPending}
        onConfirm={workflow.confirmReject}
      />

      {/* 下架理由必填（FR-APPR-10）。 */}
      <ConfirmDialog
        open={workflow.offlineTarget !== null}
        onOpenChange={(open) => {
          if (!open) {
            workflow.setOfflineTarget(null);
            workflow.setOfflineError(null);
          }
        }}
        title={`下架「${workflow.offlineTarget?.tool_name ?? ""}」？`}
        description={
          <div className="space-y-2">
            <p>下架后门户立即不可见，owner 会在个人中心看到「已下架」与理由。</p>
            <TextareaField
              id="offline-reason"
              label="下架理由（必填）"
              value={workflow.offlineReason}
              onChange={(value) => {
                workflow.setOfflineReason(value);
                if (workflow.offlineError) workflow.setOfflineError(null);
              }}
              rows={2}
              error={workflow.offlineError}
            />
          </div>
        }
        confirmLabel="确认下架"
        pending={workflow.offlinePending}
        onConfirm={workflow.confirmOffline}
      />

      <p className="text-xs text-muted-foreground">
        等待时长由服务端计算：超过 24 小时标黄、超过 72 小时标红，便于发现积压。
      </p>
    </div>
  );
}
