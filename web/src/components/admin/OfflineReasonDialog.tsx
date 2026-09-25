import * as React from "react";

import type { AdminToolItem } from "@/api/types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

/**
 * 下架理由 Dialog（docs/04 §6.12 行操作「下架（理由必填，Dialog）」）。
 *
 * FR-APPR-10：下架理由必填。后端 `OfflineRequest.reason` 的约束是 5~2000 字
 * （openapi.json），前端在提交前做同样的软校验，避免浪费一次往返。
 */
export interface OfflineReasonDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  tool: AdminToolItem | null;
  pending: boolean;
  /** 服务端返回的错误（409 状态冲突、403 等）。 */
  error?: string | null;
  onConfirm: (reason: string) => void;
}

/** 与后端 `min_length=5` 对齐。 */
export const OFFLINE_REASON_MIN_LENGTH = 5;

export function OfflineReasonDialog({
  open,
  onOpenChange,
  tool,
  pending,
  error,
  onConfirm,
}: OfflineReasonDialogProps) {
  const [reason, setReason] = React.useState("");
  const [localError, setLocalError] = React.useState<string | null>(null);
  const [trackedToolId, setTrackedToolId] = React.useState<number | null>(null);

  // 换一个工具就清空草稿（docs/04 §8.1：不在渲染中产生副作用，只做状态同步）。
  if (open && tool && trackedToolId !== tool.id) {
    setTrackedToolId(tool.id);
    setReason("");
    setLocalError(null);
  }

  const trimmed = reason.trim();
  const message = localError ?? error ?? null;

  // 关闭时清空草稿，避免下次打开同一工具时看到上次的残留理由。
  function handleOpenChange(next: boolean) {
    if (!next) {
      setTrackedToolId(null);
      setReason("");
      setLocalError(null);
    }
    onOpenChange(next);
  }

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (trimmed.length < OFFLINE_REASON_MIN_LENGTH) {
      setLocalError(`请填写下架理由（至少 ${OFFLINE_REASON_MIN_LENGTH} 个字）`);
      return;
    }
    setLocalError(null);
    onConfirm(trimmed);
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent data-testid="admin-tool-offline-dialog" className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>下架「{tool?.name ?? ""}」？</DialogTitle>
          <DialogDescription>
            下架后门户立即不可见，owner 会在个人中心看到「已下架」与这里填写的理由（FR-APPR-10）。
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid gap-1.5">
            <Label htmlFor="admin-tool-offline-reason-input">下架理由（必填）</Label>
            <Textarea
              id="admin-tool-offline-reason-input"
              data-testid="admin-tool-offline-reason"
              rows={3}
              maxLength={2000}
              value={reason}
              disabled={pending}
              aria-invalid={message !== null}
              aria-describedby={message ? "admin-tool-offline-error" : undefined}
              onChange={(event) => {
                setReason(event.target.value);
                if (localError) setLocalError(null);
              }}
              placeholder="例如：依赖的内部接口已下线，需整改后重新提交"
            />
            {message ? (
              <p id="admin-tool-offline-error" role="alert" className="text-xs text-destructive">
                {message}
              </p>
            ) : null}
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={pending}
              onClick={() => handleOpenChange(false)}
            >
              取消
            </Button>
            <Button type="submit" variant="destructive" disabled={pending}>
              {pending ? "下架中…" : "确认下架"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default OfflineReasonDialog;
