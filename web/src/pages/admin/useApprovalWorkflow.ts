import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as React from "react";
import { toast } from "sonner";

import {
  approvalsQueryKey,
  approveTool,
  batchApprove,
  fetchApprovals,
  offlineTool,
  rejectTool,
} from "@/api/admin";
import { ApiError, getErrorMessage } from "@/api/client";
import type { ApprovalQueueItem } from "@/api/types";
import type { ApprovalTypeFilter } from "@/components/admin/ApprovalQueue";

/** 队列分页大小。审批台在桌面使用，50 条一页足够覆盖常见积压。 */
export const APPROVAL_PAGE_SIZE = 50;

/** 键盘事件不应劫持表单输入（docs/04 §6.10）。 */
function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}

/**
 * 审批队列的状态机（docs/04 §6.10）。
 *
 * 从页面组件里抽出来，是因为这里的规则密度很高而页面只需要渲染：
 *  - 批准后自动前进到下一条，不弹二次确认（FR-APPR-07）
 *  - 驳回必须 ≥5 字理由 + `AlertDialog` 二次确认（FR-APPR-08）
 *  - 批量批准只做批准，**不存在**批量驳回（FR-APPR-09）
 *  - 409 `ALREADY_PROCESSED` 单独提示并刷新（FR-APPR-14）
 *  - J/K 切换、A 批准、R 填理由、Esc 关闭，输入框内不劫持
 */
export function useApprovalWorkflow({
  typeFilter,
  page,
  onPageOutOfRange,
}: {
  typeFilter: ApprovalTypeFilter;
  page: number;
  /** 当前页被清空时由页面回退页码。 */
  onPageOutOfRange: (nextPage: number) => void;
}) {
  const queryClient = useQueryClient();

  const [selectedToolId, setSelectedToolId] = React.useState<number | null>(null);
  const [checkedIds, setCheckedIds] = React.useState<number[]>([]);
  const [note, setNote] = React.useState("");
  const [rejectReason, setRejectReason] = React.useState("");
  const [rejectError, setRejectError] = React.useState<string | null>(null);
  const [showRejectInput, setShowRejectInput] = React.useState(false);
  const [rejectTarget, setRejectTarget] = React.useState<ApprovalQueueItem | null>(null);
  const [offlineTarget, setOfflineTarget] = React.useState<ApprovalQueueItem | null>(null);
  const [offlineReason, setOfflineReason] = React.useState("");
  const [offlineError, setOfflineError] = React.useState<string | null>(null);
  const rejectReasonRef = React.useRef<HTMLTextAreaElement>(null!);

  const params = React.useMemo(
    // `pending_all` = pending + pending_update：真实后端用它表示「全部待处理」，
    // 传 `pending` 会漏掉新版本待审条目（见 api/admin.ts 的注释）。
    () => ({ status: "pending_all", page, page_size: APPROVAL_PAGE_SIZE }),
    [page],
  );

  const queueQuery = useQuery({
    queryKey: approvalsQueryKey(params),
    queryFn: ({ signal }) => fetchApprovals(params, signal),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });

  const allItems = queueQuery.data?.items ?? [];
  const total = queueQuery.data?.total ?? 0;
  const pages = queueQuery.data?.pages ?? 1;

  const visibleItems = React.useMemo(
    () =>
      typeFilter === "all"
        ? (queueQuery.data?.items ?? [])
        : (queueQuery.data?.items ?? []).filter(
            (item) => item.submission_type === typeFilter,
          ),
    [queueQuery.data?.items, typeFilter],
  );

  const selectedItem = React.useMemo(
    () => visibleItems.find((item) => item.tool_id === selectedToolId) ?? null,
    [visibleItems, selectedToolId],
  );

  // 键盘监听与 mutation 回调都要读最新列表，但不该因此重装监听器。
  const visibleItemsRef = React.useRef<readonly ApprovalQueueItem[]>(visibleItems);
  const selectedItemRef = React.useRef<ApprovalQueueItem | null>(selectedItem);
  const noteRef = React.useRef(note);
  React.useEffect(() => {
    visibleItemsRef.current = visibleItems;
    selectedItemRef.current = selectedItem;
    noteRef.current = note;
  }, [visibleItems, selectedItem, note]);

  // 当前页被清空（最后一条处理完）自动回退一页。
  React.useEffect(() => {
    if (!queueQuery.isSuccess) return;
    if (allItems.length === 0 && page > 1) onPageOutOfRange(page - 1);
  }, [allItems.length, page, queueQuery.isSuccess, onPageOutOfRange]);

  // R 快捷键 / 首次点「驳回」：展开理由框并聚焦。
  React.useEffect(() => {
    if (showRejectInput) rejectReasonRef.current?.focus();
  }, [showRejectInput]);

  /** 失效队列（所有分页 + 侧栏角标）与审批历史（任意筛选）。 */
  const invalidateApprovals = React.useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["admin", "approvals"] });
    void queryClient.invalidateQueries({ queryKey: ["admin", "approval-history"] });
  }, [queryClient]);

  /** 处理完一条后自动前进到「原来那条的下一条」（docs/04 §6.10）。 */
  const advanceAfterRemoval = React.useCallback((removedToolId: number) => {
    const list = visibleItemsRef.current;
    const removedIndex = list.findIndex((item) => item.tool_id === removedToolId);
    const nextList = list.filter((item) => item.tool_id !== removedToolId);
    const fallbackIndex = Math.max(Math.min(removedIndex, nextList.length - 1), 0);
    const next = nextList[fallbackIndex] ?? null;
    setSelectedToolId(next?.tool_id ?? null);
    setCheckedIds((current) => current.filter((id) => id !== removedToolId));
    setShowRejectInput(false);
    setRejectReason("");
    setRejectError(null);
    setNote("");
  }, []);

  /** 409 ALREADY_PROCESSED：别人先处理了，提示并刷新（FR-APPR-14）。 */
  const handleActionError = React.useCallback(
    (error: unknown) => {
      if (error instanceof ApiError && error.is("ALREADY_PROCESSED")) {
        toast.error("该条目已被其他人处理");
        invalidateApprovals();
        return;
      }
      toast.error(getErrorMessage(error));
    },
    [invalidateApprovals],
  );

  const approveMutation = useMutation({
    mutationFn: (payload: {
      toolId: number;
      versionId: number | null;
      note: string | null;
      versionSeq?: number;
    }) =>
      approveTool(payload.toolId, {
        version_id: payload.versionId,
        note: payload.note,
        // 乐观锁：不匹配时后端返回 409 ALREADY_PROCESSED（CONTRACT §15.4）
        expected_version_seq: payload.versionSeq ?? null,
      }),
    onSuccess: (_response, payload) => {
      toast.success("已批准，正在选中下一条");
      invalidateApprovals();
      advanceAfterRemoval(payload.toolId);
    },
    onError: handleActionError,
  });

  const rejectMutation = useMutation({
    mutationFn: (payload: { toolId: number; versionId: number | null; reason: string }) =>
      rejectTool(payload.toolId, { reason: payload.reason, version_id: payload.versionId }),
    onSuccess: (_response, payload) => {
      toast.success("已驳回，理由已记录");
      invalidateApprovals();
      advanceAfterRemoval(payload.toolId);
    },
    onError: handleActionError,
  });

  const offlineMutation = useMutation({
    mutationFn: (payload: { toolId: number; reason: string }) =>
      offlineTool(payload.toolId, { reason: payload.reason }),
    onSuccess: () => {
      toast.success("已下架");
      invalidateApprovals();
      setOfflineTarget(null);
      setOfflineReason("");
      setOfflineError(null);
    },
    onError: handleActionError,
  });

  const batchMutation = useMutation({
    mutationFn: (toolIds: number[]) => batchApprove({ tool_ids: toolIds }),
    onSuccess: (response) => {
      // 服务端逐条返回结果，成功/失败条数都要 toast（FR-APPR-09）。
      if (response.failed > 0) {
        toast.warning(`批量批准完成：成功 ${response.succeeded} 条，失败 ${response.failed} 条`);
      } else {
        toast.success(`批量批准成功 ${response.succeeded} 条`);
      }
      const succeeded = new Set(
        response.results.filter((result) => result.ok).map((result) => result.tool_id),
      );
      if (succeeded.size > 0) {
        setCheckedIds((current) => current.filter((id) => !succeeded.has(id)));
      }
      invalidateApprovals();
    },
    onError: handleActionError,
  });

  const approveSelected = React.useCallback(
    (item: ApprovalQueueItem | null) => {
      if (!item || approveMutation.isPending) return;
      const trimmedNote = noteRef.current.trim();
      approveMutation.mutate({
        toolId: item.tool_id,
        versionId: item.pending_version?.id ?? null,
        note: trimmedNote ? trimmedNote : null,
        versionSeq: item.version_seq,
      });
    },
    [approveMutation],
  );

  /** 校验理由（与服务端 `RejectRequest` 5~2000 字对齐），通过后开二次确认。 */
  const requestReject = React.useCallback((item: ApprovalQueueItem | null) => {
    if (!item) return;
    const reason = rejectReason.trim();
    if (reason.length < 5) {
      setShowRejectInput(true);
      setRejectError("驳回理由至少 5 个字");
      rejectReasonRef.current?.focus();
      return;
    }
    if (reason.length > 2000) {
      setRejectError("驳回理由不能超过 2000 个字");
      return;
    }
    setRejectError(null);
    setRejectTarget(item);
  }, [rejectReason]);

  const confirmReject = React.useCallback(() => {
    const item = rejectTarget;
    if (!item) return;
    rejectMutation.mutate({
      toolId: item.tool_id,
      versionId: item.pending_version?.id ?? null,
      reason: rejectReason.trim(),
    });
    setRejectTarget(null);
  }, [rejectMutation, rejectReason, rejectTarget]);

  const requestOffline = React.useCallback((item: ApprovalQueueItem | null) => {
    if (!item) return;
    setOfflineTarget(item);
    setOfflineReason("");
    setOfflineError(null);
  }, []);

  const confirmOffline = React.useCallback(() => {
    if (!offlineTarget) return;
    const reason = offlineReason.trim();
    if (reason.length < 5) {
      setOfflineError("下架理由至少 5 个字");
      return;
    }
    offlineMutation.mutate({ toolId: offlineTarget.tool_id, reason });
  }, [offlineMutation, offlineReason, offlineTarget]);

  /* ---- J/K 切换，A 批准，R 驳回，Esc 关闭（docs/04 §6.10） ---- */
  React.useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      // 带修饰键的组合一律放过（⌘K、Ctrl+R 等有自己的含义）。
      if (event.metaKey || event.ctrlKey || event.altKey) return;

      if (event.key === "Escape") {
        if (selectedItemRef.current) {
          setSelectedToolId(null);
          setShowRejectInput(false);
        }
        return;
      }

      if (isTypingTarget(event.target)) return;
      const list = visibleItemsRef.current;
      if (list.length === 0) return;
      const currentIndex = list.findIndex(
        (item) => item.tool_id === selectedItemRef.current?.tool_id,
      );

      if (event.key === "j" || event.key === "J") {
        const next = list[Math.min(currentIndex + 1, list.length - 1)] ?? list[0];
        if (next) {
          event.preventDefault();
          setSelectedToolId(next.tool_id);
        }
        return;
      }

      if (event.key === "k" || event.key === "K") {
        const next = list[Math.max(currentIndex - 1, 0)] ?? list[0];
        if (next) {
          event.preventDefault();
          setSelectedToolId(next.tool_id);
        }
        return;
      }

      const current = selectedItemRef.current;
      if (!current) return;
      if (event.key === "a" || event.key === "A") {
        event.preventDefault();
        approveSelected(current);
        return;
      }
      if (event.key === "r" || event.key === "R") {
        event.preventDefault();
        setShowRejectInput(true);
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [approveSelected]);

  const detailActions = {
    note,
    onNoteChange: setNote,
    rejectReason,
    onRejectReasonChange: (value: string) => {
      setRejectReason(value);
      if (rejectError) setRejectError(null);
    },
    rejectError,
    showRejectInput,
    onApprove: () => approveSelected(selectedItem),
    onReject: () => {
      if (!selectedItem) return;
      // 第一次点「驳回」先展开理由框，第二次才校验并弹确认。
      if (!showRejectInput) {
        setShowRejectInput(true);
        return;
      }
      requestReject(selectedItem);
    },
    onOffline: () => requestOffline(selectedItem),
    approvePending: approveMutation.isPending,
    rejectPending: rejectMutation.isPending,
    rejectReasonRef,
  };

  return {
    queueQuery,
    params,
    total,
    pages,
    visibleItems,
    selectedItem,
    selectedToolId,
    setSelectedToolId,
    checkedIds,
    setCheckedIds,
    detailActions,
    batchApproveSelected: () => batchMutation.mutate(checkedIds),
    batchPending: batchMutation.isPending,
    rejectTarget,
    setRejectTarget,
    rejectReason,
    confirmReject,
    rejectPending: rejectMutation.isPending,
    offlineTarget,
    setOfflineTarget,
    offlineReason,
    setOfflineReason,
    offlineError,
    setOfflineError,
    confirmOffline,
    offlinePending: offlineMutation.isPending,
  };
}
