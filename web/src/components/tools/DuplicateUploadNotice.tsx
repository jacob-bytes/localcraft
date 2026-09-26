import { CopyCheck, X } from "lucide-react";
import { Link } from "react-router-dom";

import type { DuplicateVersionMatch } from "@/api/types";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

/**
 * M8 · F9 —— 上传去重提示（CONTRACT §23.2 / 后端 B9）。
 *
 * **为什么前端不算哈希**：浏览器端 SHA-256 需要 `crypto.subtle`，而它只在安全上下文
 * 可用（HTTPS / localhost）；本项目按 D39 用 `http://<ip>:<port>` 直连，该 API 在
 * 那里是 `undefined`，而且 Web Crypto 没有可接受的回退（引 JS 哈希库既增依赖又慢）。
 * 所以命中信息**由上传响应回带**，前端只负责展示。
 *
 * 三条展示规则：
 *
 * 1. **非阻塞**：上传是成功的，提示只是「可能重复」。它是一条可关闭的
 *    `Alert`（`role="status"` 由 Alert 自带），不遮挡成功 toast、不跳转、不改按钮状态。
 * 2. **可关闭**：用户看过就走，不反复出现。
 * 3. **有跳转**：直接链到命中的那个工具，让用户自己判断。
 *
 * 依赖浏览器能力的代码要有降级路径（复用 `CopyButton` 的守卫惯例）：
 * `readDuplicateOf()` 在字段缺失或形状不认识时返回 `null`，本组件随之**整体不渲染**，
 * 不会因为后端没落地这个字段而白屏或抛错。
 */
export interface DuplicateUploadNoticeProps {
  duplicate: DuplicateVersionMatch | null;
  onDismiss: () => void;
  className?: string;
}

export function DuplicateUploadNotice({
  duplicate,
  onDismiss,
  className,
}: DuplicateUploadNoticeProps) {
  if (!duplicate) return null;

  return (
    <Alert
      variant="warning"
      data-testid="duplicate-upload-notice"
      className={className}
    >
      <CopyCheck aria-hidden="true" />
      <AlertTitle>上传成功，但这个文件可能与已有版本重复</AlertTitle>
      <AlertDescription className="space-y-2">
        <p>
          服务端比对文件指纹后发现：与工具{" "}
          <Link
            to={`/tools/${duplicate.slug}`}
            data-testid="duplicate-upload-link"
            className="font-medium text-primary underline-offset-4 hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          >
            {duplicate.name}
          </Link>{" "}
          的版本 <span className="font-mono text-xs">{duplicate.version}</span>{" "}
          完全相同。上传**没有被拦截** —— 如果这是有意的重复归档，可以忽略这条提示。
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Button asChild variant="outline" size="sm">
            <Link to={`/tools/${duplicate.slug}`}>查看该工具</Link>
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            data-testid="duplicate-upload-dismiss"
            onClick={onDismiss}
          >
            <X aria-hidden="true" className="size-4" />
            知道了
          </Button>
        </div>
      </AlertDescription>
    </Alert>
  );
}
