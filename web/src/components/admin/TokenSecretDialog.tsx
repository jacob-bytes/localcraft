import { ShieldAlert, TriangleAlert } from "lucide-react";
import * as React from "react";

import { CopyButton } from "@/components/common/CopyButton";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

/**
 * 签发成功后的**一次性**明文展示（docs/04 §6.18、FR-ADMIN-10）。
 *
 * 安全约束（CONTRACT §3.1 的同一条思路：绝不把凭证写到可持久化的地方）：
 *  - 明文只来自 `POST /admin/tokens` 的响应，**不写 localStorage、不进 URL、不 console.log**；
 *  - 组件卸载即销毁，父组件负责在关闭时清空 state；
 *  - 遮罩点击**不关闭**（`AlertDialog` 默认行为），Esc / 关闭按钮都会先走一次
 *    「确认已复制？」二次确认，避免手滑后永久丢失明文。
 *
 * 组件没有 `open` prop：挂载即打开，父组件用 `secret === null` 决定是否渲染
 * （这样内部状态天然随会话重置，不需要在 effect 里做 setState）。
 */
export interface TokenSecretDialogProps {
  tokenName: string;
  /** 明文 Token —— 内存在组件里，仅存活到本对话框关闭。 */
  token: string;
  /** 后端附带的提示文案（`ApiTokenCreateResponse.warning`）。 */
  warning: string;
  onClose: () => void;
}

export function TokenSecretDialog({ tokenName, token, warning, onClose }: TokenSecretDialogProps) {
  const [confirmOpen, setConfirmOpen] = React.useState(false);

  return (
    <>
      <AlertDialog
        open
        onOpenChange={(next) => {
          // 任何「想关闭」的意图都先转成二次确认，绝不直接关闭（docs/04 §6.18）。
          if (!next) setConfirmOpen(true);
        }}
      >
        <AlertDialogContent data-testid="token-secret-dialog" className="max-w-2xl">
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <ShieldAlert aria-hidden="true" className="size-5 text-destructive" />
              Token 已签发：{tokenName}
            </AlertDialogTitle>
            <AlertDialogDescription>
              这是唯一一次展示明文。请立即复制并保存到密码管理器或 CI 的受保护变量中。
            </AlertDialogDescription>
          </AlertDialogHeader>

          <div className="space-y-4">
            <Alert variant="destructive">
              <TriangleAlert aria-hidden="true" />
              <AlertTitle>此 Token 不会再次显示</AlertTitle>
              <AlertDescription>
                关闭此窗口后你将无法再查看。若未保存，只能吊销后重新签发。
              </AlertDescription>
            </Alert>

            <div className="flex items-start gap-2 rounded-lg border bg-muted/40 p-3">
              <code
                data-testid="token-plaintext"
                className="min-w-0 flex-1 font-mono text-sm break-all select-all"
              >
                {token}
              </code>
              {/* 大号复制按钮：明文只有一次机会，复制入口要显眼（docs/04 §6.18）。 */}
              <CopyButton
                value={token}
                label="复制 Token 明文"
                testId="token-copy-button"
                className="size-10 shrink-0"
              />
            </div>

            {warning ? (
              <p className="text-xs text-muted-foreground" role="note">
                {warning}
              </p>
            ) : null}
          </div>

          <AlertDialogFooter>
            <AlertDialogAction
              className="bg-primary text-primary-foreground hover:bg-primary/90"
              onClick={(event) => {
                event.preventDefault();
                setConfirmOpen(true);
              }}
            >
              我已保存，关闭
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 关闭前的二次确认（docs/04 §6.18「关闭时二次确认」）。 */}
      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent data-testid="token-secret-close-confirm">
          <AlertDialogHeader>
            <AlertDialogTitle>确认已复制？</AlertDialogTitle>
            <AlertDialogDescription>
              关闭后此 Token 的明文将永久消失，本平台无法再次展示。请确认已经复制或保存。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>还没复制</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={(event) => {
                event.preventDefault();
                setConfirmOpen(false);
                onClose();
              }}
            >
              已复制并关闭
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
