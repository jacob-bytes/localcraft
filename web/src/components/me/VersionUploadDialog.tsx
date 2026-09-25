import { FileUp, Loader2, UploadCloud, X } from "lucide-react";
import * as React from "react";

import { ApiError, getErrorMessage } from "@/api/client";
import { uploadVersion } from "@/api/me";
import type { ToolDetail, VersionUploadResponse } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Textarea } from "@/components/me/Textarea";
import { formatFileSize } from "@/lib/format";
import { cn } from "@/lib/utils";

/** FR-FILE-02 / `UploadLimits.max_file_size_bytes` default (docs/01 §8). */
const MAX_FILE_BYTES = 200 * 1024 * 1024;

export interface VersionUploadDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  tool: ToolDetail;
  onUploaded: (result: VersionUploadResponse) => void;
}

function nextPatch(version: string | null): string {
  if (!version) return "1.0.0";
  const match = /^(\d+)\.(\d+)\.(\d+)$/.exec(version.trim());
  if (!match) return version;
  const major = match[1] ?? "1";
  const minor = match[2] ?? "0";
  const patch = match[3] ?? "0";
  return `${major}.${minor}.${Number.parseInt(patch, 10) + 1}`;
}

/**
 * 「上传新版本」对话框（docs/04 §6.8）。
 *
 * The success toast wording is an acceptance item: it must say the pending
 * version will not disturb the version currently in service.
 */
export function VersionUploadDialog({
  open,
  onOpenChange,
  tool,
  onUploaded,
}: VersionUploadDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* Radix unmounts closed dialog content, so the body is re-created (and its
          state re-seeded) on every open — no reset effect needed. */}
      {open ? (
        <VersionUploadDialogBody
          tool={tool}
          onClose={() => onOpenChange(false)}
          onUploaded={onUploaded}
        />
      ) : null}
    </Dialog>
  );
}

function VersionUploadDialogBody({
  tool,
  onClose,
  onUploaded,
}: {
  tool: ToolDetail;
  onClose: () => void;
  onUploaded: (result: VersionUploadResponse) => void;
}) {
  const [version, setVersion] = React.useState(() =>
    nextPatch(tool.current_version?.version ?? null),
  );
  const [changelog, setChangelog] = React.useState("");
  const [file, setFile] = React.useState<File | null>(null);
  const [webappUrl, setWebappUrl] = React.useState(tool.webapp_url ?? "");
  const [promptContent, setPromptContent] = React.useState("");
  const [autoSubmit, setAutoSubmit] = React.useState(true);
  const [progress, setProgress] = React.useState<number | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [dragging, setDragging] = React.useState(false);
  const [confirmSoft, setConfirmSoft] = React.useState(false);

  const abortRef = React.useRef<AbortController | null>(null);
  const versionInputRef = React.useRef<HTMLInputElement>(null);
  const uploading = progress !== null;

  /* Cancel an in-flight upload if the dialog is torn down. */
  React.useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  const needsFile = tool.tool_type === "file" || tool.tool_type === "skill";

  function pickFile(list: FileList | null) {
    const picked = list?.[0];
    if (!picked) return;
    if (picked.size > MAX_FILE_BYTES) {
      setError(`文件超过 ${formatFileSize(MAX_FILE_BYTES)} 限制`);
      return;
    }
    setError(null);
    setFile(picked);
  }

  function validate(): string | null {
    if (version.trim() === "") return "请填写版本号";
    if (version.trim().length > 64) return "版本号不能超过 64 个字符";
    if (needsFile && !file) return "请选择要上传的文件";
    if (tool.tool_type === "webapp" && !/^https?:\/\//.test(webappUrl.trim())) {
      return "URL 必须以 http:// 或 https:// 开头";
    }
    return null;
  }

  async function submit() {
    const message = validate();
    if (message) {
      setError(message);
      return;
    }
    // Soft check: an empty changelog is allowed, but the user confirms it first
    // (docs/04 §6.8「变更说明建议必填 —— 前端做软校验」).
    if (changelog.trim() === "" && !confirmSoft) {
      setConfirmSoft(true);
      return;
    }

    const controller = new AbortController();
    abortRef.current = controller;
    setError(null);
    setProgress(0);
    try {
      const result = await uploadVersion(
        tool.id,
        {
          version: version.trim(),
          changelog_md: changelog,
          file: needsFile ? file : null,
          prompt_content: tool.tool_type === "prompt" ? promptContent : null,
          webapp_url: tool.tool_type === "webapp" ? webappUrl.trim() : null,
          auto_submit: autoSubmit,
        },
        {
          signal: controller.signal,
          onProgress: (loaded, total) =>
            setProgress(total > 0 ? Math.round((loaded / total) * 100) : null),
        },
      );
      onUploaded(result);
      onClose();
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      if (caught instanceof ApiError && caught.is("VERSION_EXISTS")) {
        setError(`版本 ${version.trim()} 已存在，请换一个版本号`);
        // FR-VER-02: focus the field so the fix is one keystroke away.
        versionInputRef.current?.focus();
        versionInputRef.current?.select();
      } else {
        setError(getErrorMessage(caught));
      }
    } finally {
      abortRef.current = null;
      setProgress(null);
    }
  }

  return (
    <DialogContent className="max-w-2xl" data-testid="version-upload-dialog">
      <DialogHeader>
        <DialogTitle>上传新版本 · {tool.name}</DialogTitle>
        <DialogDescription>
          新版本需经审批后才会成为当前版本，当前版本 {tool.current_version?.version ?? "—"}{" "}
          在此期间继续对外提供服务。
        </DialogDescription>
      </DialogHeader>

      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="version-number">版本号 *</Label>
            <Input
              id="version-number"
              ref={versionInputRef}
              value={version}
              disabled={uploading}
              aria-invalid={error?.includes("版本") ? true : undefined}
              onChange={(event) => setVersion(event.target.value)}
            />
            <p className="text-xs text-muted-foreground">建议使用语义化版本，如 1.2.0</p>
          </div>

          <div className="space-y-1.5">
            {/* One label only: a separate <Label> for the group plus the control's
                own label would give the checkbox a doubled accessible name. */}
            <label
              htmlFor="version-auto-submit"
              className="flex h-9 items-center gap-2 text-sm"
            >
              <Checkbox
                id="version-auto-submit"
                checked={autoSubmit}
                disabled={uploading}
                onCheckedChange={(checked) => setAutoSubmit(checked === true)}
              />
              上传后立即提交审批
            </label>
            <p className="text-xs text-muted-foreground">
              关闭后版本会先存为待审草稿，稍后可在版本管理里再提交。
            </p>
          </div>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="version-changelog">变更说明</Label>
          <Textarea
            id="version-changelog"
            rows={5}
            value={changelog}
            disabled={uploading}
            placeholder="支持 Markdown，例如：### 新增&#10;- 支持 gzip 日志"
            onChange={(event) => setChangelog(event.target.value)}
          />
          {confirmSoft ? (
            <p className="text-xs text-amber-600 dark:text-amber-400" role="status">
              变更说明为空，再次点击「上传版本」将直接提交。
            </p>
          ) : (
            <p className="text-xs text-muted-foreground">建议填写，便于审批人评估影响面。</p>
          )}
        </div>

        {needsFile ? (
          <div className="space-y-1.5">
            <Label htmlFor="version-file">
              {tool.tool_type === "skill" ? "Skill 包（zip）*" : "交付文件 *"}
            </Label>
            <div
              onDragOver={(event) => {
                event.preventDefault();
                if (!uploading) setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                if (!uploading) pickFile(event.dataTransfer.files);
              }}
              className={cn(
                "flex flex-col items-center gap-1.5 rounded-lg border-2 border-dashed px-4 py-5 text-center text-sm",
                dragging ? "border-primary bg-primary/5" : "border-input",
              )}
            >
              <UploadCloud aria-hidden="true" className="size-5 text-muted-foreground" />
              {file ? (
                <span className="flex items-center gap-2">
                  <span className="font-medium">{file.name}</span>
                  <span className="text-xs text-muted-foreground">
                    {formatFileSize(file.size)}
                  </span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    aria-label="移除已选文件"
                    disabled={uploading}
                    onClick={() => setFile(null)}
                  >
                    <X aria-hidden="true" className="size-4" />
                  </Button>
                </span>
              ) : (
                <>
                  <span>{dragging ? "松开以上传" : "拖拽文件到此处"}</span>
                  <label
                    htmlFor="version-file"
                    className="cursor-pointer text-xs text-primary underline-offset-4 hover:underline"
                  >
                    或点击选择文件
                  </label>
                  <span className="text-xs text-muted-foreground">
                    单个文件 ≤ {formatFileSize(MAX_FILE_BYTES)}
                  </span>
                </>
              )}
            </div>
            <input
              id="version-file"
              type="file"
              className="sr-only"
              disabled={uploading}
              onChange={(event) => {
                pickFile(event.target.files);
                event.target.value = "";
              }}
            />
          </div>
        ) : null}

        {tool.tool_type === "webapp" ? (
          <div className="space-y-1.5">
            <Label htmlFor="version-webapp-url">工具 URL *</Label>
            <Input
              id="version-webapp-url"
              value={webappUrl}
              disabled={uploading}
              onChange={(event) => setWebappUrl(event.target.value)}
              placeholder="http://内网地址/"
            />
            <p className="text-xs text-muted-foreground">
              不做可达性阻断式校验（FR-TOOL-05：内网可能限制服务端出网）
            </p>
          </div>
        ) : null}

        {tool.tool_type === "prompt" ? (
          <div className="space-y-1.5">
            <Label htmlFor="version-prompt">提示词正文</Label>
            <Textarea
              id="version-prompt"
              rows={6}
              value={promptContent}
              disabled={uploading}
              className="font-mono"
              onChange={(event) => setPromptContent(event.target.value)}
            />
          </div>
        ) : null}

        {progress !== null ? (
          <div className="space-y-1" data-testid="upload-progress" aria-live="polite">
            <Progress value={progress} aria-label="版本上传进度" />
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>上传中… {progress}%</span>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => abortRef.current?.abort()}
              >
                取消上传
              </Button>
            </div>
          </div>
        ) : null}

        {error ? (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        ) : null}
      </div>

      <DialogFooter>
        <Button type="button" variant="outline" disabled={uploading} onClick={onClose}>
          取消
        </Button>
        <Button type="button" disabled={uploading} onClick={() => void submit()}>
          {uploading ? (
            <>
              <Loader2 aria-hidden="true" className="size-4 animate-spin" />
              上传中…
            </>
          ) : (
            <>
              <FileUp aria-hidden="true" className="size-4" />
              上传版本
            </>
          )}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}
