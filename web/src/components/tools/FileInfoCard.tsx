import { AlertTriangle, FileArchive } from "lucide-react";

import type { ToolDetail } from "@/api/types";
import { CopyButton } from "@/components/common/CopyButton";
import { DownloadButton } from "@/components/tools/DownloadButton";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { formatDateTime, formatFileSize } from "@/lib/format";

/** 会触发浏览器「下载后可执行」告警的扩展名（docs/04 §6.4 可执行类型）。 */
const EXECUTABLE_EXTENSIONS = new Set(["exe", "sh", "ps1", "bat", "msi"]);

function fileExtension(fileName: string | null, fileExt: string | null): string | null {
  if (fileExt) return fileExt.replace(/^\./, "").toLowerCase();
  if (!fileName) return null;
  const dot = fileName.lastIndexOf(".");
  if (dot < 0 || dot === fileName.length - 1) return null;
  return fileName.slice(dot + 1).toLowerCase();
}

/**
 * `file` 类型专属区块（docs/04 §6.4）：文件名、大小、格式、完整 SHA256（可复制）
 * 与下载按钮。SHA256 用详情接口的 `current_version.file_sha256`（完整值），
 * 列表接口只有 `file_sha256_short`（FR-VER-06）。
 */
export interface FileInfoCardProps {
  detail: ToolDetail;
}

export function FileInfoCard({ detail }: FileInfoCardProps) {
  const version = detail.current_version;
  const sha256 = version?.file_sha256 ?? null;
  const ext = fileExtension(version?.file_name ?? null, version?.file_ext ?? null);
  const executable = ext !== null && EXECUTABLE_EXTENSIONS.has(ext);
  const canDownload = detail.permissions.can_download && version !== null;

  return (
    <section
      aria-labelledby="file-info-title"
      className="space-y-4 rounded-xl border bg-card p-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="file-info-title" className="flex items-center gap-2 text-lg font-semibold">
          <FileArchive aria-hidden="true" className="size-4 text-muted-foreground" />
          文件信息
        </h2>
        <DownloadButton
          slug={detail.slug}
          versionId={version?.id ?? null}
          disabled={!canDownload}
          disabledReason={
            version === null ? "该工具暂无可用版本" : "当前角色无下载权限"
          }
          fileName={version?.file_name ?? null}
          label="下载文件"
          variant="outline"
          ariaLabel={`下载 ${version?.file_name ?? detail.name}`}
        />
      </div>

      {executable ? (
        <Alert variant="destructive">
          <AlertTriangle aria-hidden="true" />
          <AlertTitle className="line-clamp-none">
            此文件包含可执行内容，请确认来源后运行
          </AlertTitle>
          <AlertDescription>
            扩展名 .{ext} 属于可执行文件，运行前请核对 SHA256 与来源。
          </AlertDescription>
        </Alert>
      ) : null}

      {version === null ? (
        <p className="text-sm text-muted-foreground">该工具暂无可下载的版本。</p>
      ) : (
        <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
          <div className="min-w-0 space-y-1">
            <dt className="text-xs text-muted-foreground">文件名</dt>
            <dd className="break-all font-medium">{version.file_name ?? "—"}</dd>
          </div>
          <div className="space-y-1">
            <dt className="text-xs text-muted-foreground">大小</dt>
            <dd className="tabular-nums">{formatFileSize(version.file_size)}</dd>
          </div>
          <div className="space-y-1">
            <dt className="text-xs text-muted-foreground">格式</dt>
            <dd className="font-mono text-xs uppercase">{ext ?? "未知"}</dd>
          </div>
          <div className="space-y-1">
            <dt className="text-xs text-muted-foreground">上传时间</dt>
            <dd>
              <time dateTime={version.created_at ?? undefined} title={formatDateTime(version.created_at)}>
                {formatDateTime(version.created_at)}
              </time>
            </dd>
          </div>
          <div className="min-w-0 space-y-1 sm:col-span-2">
            <dt className="text-xs text-muted-foreground">SHA256</dt>
            <dd className="flex items-start gap-1">
              <code className="min-w-0 break-all font-mono text-xs leading-5">{sha256 ?? "—"}</code>
              {sha256 ? <CopyButton value={sha256} label="复制 SHA256" className="shrink-0" /> : null}
            </dd>
          </div>
        </dl>
      )}
    </section>
  );
}
