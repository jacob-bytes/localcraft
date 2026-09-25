import { Check, CloudUpload, X } from "lucide-react";
import * as React from "react";

import type { ToolType } from "@/api/types";
import { CopyButton } from "@/components/common/CopyButton";
import { MarkdownEditor } from "@/components/me/ToolFormParts";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Label } from "@/components/ui/label";
import { formatFileSize } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * Non-fatal package problems (FR-TOOL-06): a Skill zip that cannot be parsed
 * may still be saved as a draft, but it can never be submitted for approval.
 * The zip-hardening codes come from `ErrorCode` in `api/types.ts`.
 */
const SKILL_ERROR_CODES = new Set([
  "SKILL_MD_NOT_FOUND",
  "SKILL_PARSE_FAILED",
  "ZIP_PATH_TRAVERSAL",
  "ZIP_BOMB_DETECTED",
  "ZIP_TOO_MANY_FILES",
  "ZIP_INVALID",
]);

/** True when an upload error is a non-fatal Skill parse problem (draft saving stays allowed). */
export function isSkillParseError(code: string): boolean {
  return SKILL_ERROR_CODES.has(code);
}

/** FR-FILE-02 default single-file cap (docs/01 §8). */
export const MAX_UPLOAD_BYTES = 200 * 1024 * 1024;

/** Result of the last successful version upload, shown as file metadata. */
export interface UploadedFile {
  name: string;
  size: number;
  sha256: string;
  version: string;
}

export interface TypeSpecificSectionProps {
  toolType: ToolType;
  locked: boolean;
  dragging: boolean;
  progress: number | null;
  file: File | null;
  uploadedFile: UploadedFile | null;
  promptContent: string;
  webappField: React.ReactNode;
  healthField: React.ReactNode;
  onPromptChange: (value: string) => void;
  onDragOver: (event: React.DragEvent<HTMLDivElement>) => void;
  onDragLeave: () => void;
  onDropFile: (event: React.DragEvent<HTMLDivElement>) => void;
  onPickFile: (file: File) => void;
  onRemoveFile: () => void;
  onCancelUpload: () => void;
}

/**
 * 类型专属区（docs/04 §6.7）：`file`/`skill` 拖拽上传，`webapp` URL，
 * `prompt` 正文。
 *
 * The upload progress bar and its 取消 button use the `Signal` that
 * `uploadVersion` forwards to XHR (`api/client.ts` `upload()`), so cancelling
 * really aborts the request instead of merely hiding the bar.
 */
export function TypeSpecificSection(props: TypeSpecificSectionProps) {
  const { toolType, locked, dragging, progress, file, uploadedFile, promptContent } = props;

  if (toolType === "webapp") {
    return (
      <div className="space-y-3">
        {props.webappField}
        {props.healthField}
      </div>
    );
  }

  if (toolType === "prompt") {
    return (
      <MarkdownEditor
        id="tool-field-prompt_content"
        label="提示词正文"
        value={promptContent}
        disabled={locked}
        rows={8}
        onChange={props.onPromptChange}
        placeholder="直接填写提示词模板正文，详情页提供一键复制"
        hint="版本内容变更才会触发审批（FR-TOOL-13）；已发布工具请到「版本管理」发布新版本。"
      />
    );
  }

  const skill = toolType === "skill";
  return (
    <div className="space-y-3">
      <Label htmlFor="version-content-file">{skill ? "Skill 包（zip）" : "交付文件"}</Label>
      <div
        onDragOver={props.onDragOver}
        onDragLeave={props.onDragLeave}
        onDrop={props.onDropFile}
        className={cn(
          "flex flex-col items-center gap-1.5 rounded-lg border-2 border-dashed px-4 py-6 text-center text-sm",
          dragging ? "border-primary bg-primary/5" : "border-input",
          locked && "pointer-events-none opacity-60",
        )}
      >
        <CloudUpload aria-hidden="true" className="size-5 text-muted-foreground" />
        {file ? (
          <span className="flex items-center gap-2">
            <span className="font-medium">{file.name}</span>
            <span className="text-xs text-muted-foreground">{formatFileSize(file.size)}</span>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label="移除已选文件"
              onClick={props.onRemoveFile}
            >
              <X aria-hidden="true" className="size-4" />
            </Button>
          </span>
        ) : (
          <>
            <span>{dragging ? "松开以上传" : "拖拽文件到此处"}</span>
            <label
              htmlFor="version-content-file"
              className="cursor-pointer text-xs text-primary underline-offset-4 hover:underline"
            >
              或点击选择文件
            </label>
            <span className="text-xs text-muted-foreground">
              单个文件 ≤ {formatFileSize(MAX_UPLOAD_BYTES)}
              {skill ? "；包根或单层子目录下需存在 SKILL.md" : ""}
            </span>
          </>
        )}
      </div>
      <input
        id="version-content-file"
        type="file"
        className="sr-only"
        disabled={locked}
        onChange={(event) => {
          const picked = event.target.files?.[0];
          if (picked) props.onPickFile(picked);
          event.target.value = "";
        }}
      />

      {progress !== null ? (
        <div className="space-y-1" data-testid="upload-progress" aria-live="polite">
          <Progress value={progress} aria-label="版本上传进度" />
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>上传中… {progress}%</span>
            <Button type="button" variant="outline" size="sm" onClick={props.onCancelUpload}>
              取消上传
            </Button>
          </div>
        </div>
      ) : null}

      {uploadedFile ? (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-muted/40 px-3 py-2 text-sm">
          <Check aria-hidden="true" className="size-4 text-success" />
          <span className="font-medium">{uploadedFile.name}</span>
          <span className="text-xs text-muted-foreground">
            {formatFileSize(uploadedFile.size)} · v{uploadedFile.version}
          </span>
          {uploadedFile.sha256 ? (
            <span className="flex items-center gap-1">
              <span className="font-mono text-xs text-muted-foreground">
                SHA256 {uploadedFile.sha256.slice(0, 16)}…
              </span>
              <CopyButton value={uploadedFile.sha256} label="复制完整 SHA256" />
            </span>
          ) : null}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="ml-auto"
            onClick={props.onRemoveFile}
          >
            移除
          </Button>
        </div>
      ) : null}
    </div>
  );
}
