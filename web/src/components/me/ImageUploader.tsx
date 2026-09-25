import { ArrowLeft, ArrowRight, ImagePlus, Loader2, Star, Trash2, UploadCloud } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { getErrorMessage } from "@/api/client";
import { deleteImage, patchImage, uploadImage } from "@/api/me";
import type { ToolImage } from "@/api/types";
import { CopyButton } from "@/components/common/CopyButton";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";

/** FR-FILE-09: png/jpg/jpeg/webp/gif, ≤ 5 MB each, 1 cover + ≤ 8 screenshots. */
const MAX_SCREENSHOTS = 8;
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const ACCEPT = "image/png,image/jpeg,image/webp,image/gif";

/** One image awaiting the tool's `id` (create mode has nothing to POST to yet). */
export interface PendingImage {
  key: string;
  file: File;
  previewUrl: string;
}

async function filesToPending(list: FileList | File[]): Promise<PendingImage[]> {
  const files = Array.from(list);
  return files.map((file) => ({
    key: `${file.name}-${file.size}-${file.lastModified}`,
    file,
    // Local preview only; the real URL always comes from the upload response
    // (CONTRACT §14.3 — never construct image URLs).
    previewUrl: URL.createObjectURL(file),
  }));
}

export interface ImageUploaderProps {
  /** Already-uploaded images (edit mode, from `ToolDetail.images`). */
  images: ToolImage[];
  onImagesChange: (images: ToolImage[]) => void;
  /** Newly picked files, not yet uploaded (create mode). */
  pending: PendingImage[];
  onPendingChange: (pending: PendingImage[]) => void;
  toolId: number | null;
  disabled?: boolean;
}

/**
 * 截图上传（docs/04 §6.7「截图」区，FR-FILE-09/10）。
 *
 * The first screenshot becomes the cover: the backend promotes the oldest image
 * with `set_as_cover` and demotes the previous cover to a screenshot, so the UI
 * only ever issues the PATCH and re-reads the server array.
 *
 * Uploading needs `tool_id`; in create mode files are queued and uploaded by the
 * editor right after `createTool` resolves.
 */
export function ImageUploader({
  images,
  onImagesChange,
  pending,
  onPendingChange,
  toolId,
  disabled = false,
}: ImageUploaderProps) {
  const [dragging, setDragging] = React.useState(false);
  const [progress, setProgress] = React.useState<number | null>(null);

  const cover = images.find((image) => image.kind === "cover") ?? images[0] ?? null;
  const count = images.length + pending.length;
  const full = count >= MAX_SCREENSHOTS + 1;

  /* Revoke object URLs on unmount so a long editing session cannot leak blobs.
     A ref keeps the cleanup out of the dependency list — revoking on every
     `pending` change would kill previews still on screen. */
  const pendingRef = React.useRef(pending);
  React.useEffect(() => {
    pendingRef.current = pending;
  }, [pending]);
  React.useEffect(() => {
    return () => {
      for (const item of pendingRef.current) URL.revokeObjectURL(item.previewUrl);
    };
  }, []);

  async function acceptFiles(list: FileList | File[]) {
    const files = Array.from(list);
    if (files.length === 0) return;

    const accepted: File[] = [];
    for (const file of files) {
      if (!file.type.startsWith("image/")) {
        toast.error(`${file.name} 不是图片文件`);
        continue;
      }
      if (file.size > MAX_IMAGE_BYTES) {
        toast.error(`${file.name} 超过 5 MB 限制`);
        continue;
      }
      accepted.push(file);
    }
    if (accepted.length === 0) return;
    if (count + accepted.length > MAX_SCREENSHOTS + 1) {
      toast.error(`最多上传 ${MAX_SCREENSHOTS + 1} 张图片（含封面）`);
      return;
    }

    if (toolId === null) {
      // Create mode: queue locally, the editor uploads after createTool.
      onPendingChange([...pending, ...(await filesToPending(accepted))]);
      return;
    }

    let current = images;
    for (const [index, file] of accepted.entries()) {
      setProgress(0);
      try {
        const uploaded = await uploadImage(toolId, file, {
          kind: current.length === 0 && index === 0 ? "cover" : "screenshot",
          onProgress: (loaded, total) =>
            setProgress(total > 0 ? Math.round((loaded / total) * 100) : null),
        });
        current = [...current, uploaded];
        onImagesChange(current);
      } catch (error) {
        toast.error(getErrorMessage(error));
      } finally {
        setProgress(null);
      }
    }
  }

  async function move(index: number, delta: -1 | 1) {
    const target = index + delta;
    const image = images[index];
    const swap = images[target];
    if (!image || !swap || toolId === null) return;
    const next = [...images];
    next[index] = swap;
    next[target] = image;
    onImagesChange(next);
    try {
      await Promise.all([
        patchImage(toolId, image.id, { sort_order: target }),
        patchImage(toolId, swap.id, { sort_order: index }),
      ]);
    } catch (error) {
      onImagesChange(images);
      toast.error(getErrorMessage(error));
    }
  }

  async function makeCover(image: ToolImage) {
    if (toolId === null) return;
    try {
      await patchImage(toolId, image.id, { set_as_cover: true });
      // The server demotes the previous cover to a screenshot; mirror that
      // locally instead of re-fetching the whole detail (docs/03 §3.11).
      onImagesChange(
        images.map((item) =>
          item.id === image.id
            ? { ...item, kind: "cover" }
            : item.kind === "cover"
              ? { ...item, kind: "screenshot" }
              : item,
        ),
      );
      toast.success("已设为封面");
    } catch (error) {
      toast.error(getErrorMessage(error));
    }
  }

  async function removeImage(image: ToolImage) {
    if (toolId === null) return;
    try {
      await deleteImage(toolId, image.id);
      onImagesChange(images.filter((item) => item.id !== image.id));
    } catch (error) {
      toast.error(getErrorMessage(error));
    }
  }

  return (
    <div className="space-y-3" data-testid="image-uploader">
      {/* A <label> instead of `div role="button" onClick`: the whole zone is a
          real control for the sibling file input (docs/04 §8.1「所有可点击元素用
          语义标签」) and still takes keyboard focus. */}
      <label
        htmlFor="image-upload-input"
        aria-label="上传截图，支持拖拽"
        aria-disabled={disabled || full}
        onDragOver={(event) => {
          event.preventDefault();
          if (!disabled && !full) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          if (disabled || full) return;
          void acceptFiles(event.dataTransfer.files);
        }}
        className={cn(
          "flex cursor-pointer flex-col items-center justify-center gap-1.5 rounded-lg border-2 border-dashed px-4 py-6 text-center text-sm transition-colors",
          "focus-within:ring-[3px] focus-within:ring-ring/50 focus-visible:outline-none",
          dragging ? "border-primary bg-primary/5" : "border-input",
          (disabled || full) && "cursor-not-allowed opacity-60",
        )}
      >
        <ImagePlus aria-hidden="true" className="size-5 text-muted-foreground" />
        <span>{dragging ? "松开以上传" : "拖拽图片到此处，或点击选择文件"}</span>
        <span className="text-xs text-muted-foreground">
          最多 {MAX_SCREENSHOTS + 1} 张（第 1 张自动作为封面），支持 PNG / JPG / WebP / GIF，单张 ≤ 5 MB
        </span>
        {toolId === null ? (
          <span className="text-xs text-muted-foreground">
            创建工具后才会真正上传，已选文件会暂存在本页
          </span>
        ) : null}
      </label>

      <input
        id="image-upload-input"
        type="file"
        accept={ACCEPT}
        multiple
        className="sr-only"
        disabled={disabled || full}
        onChange={(event) => {
          if (event.target.files) void acceptFiles(event.target.files);
          event.target.value = "";
        }}
      />

      {progress !== null ? (
        <div className="space-y-1" aria-live="polite">
          <Progress value={progress} aria-label="图片上传进度" />
          <p className="text-xs text-muted-foreground">上传中… {progress}%</p>
        </div>
      ) : null}

      {count > 0 ? (
        <ul className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {pending.map((item, index) => (
            <li key={item.key} className="overflow-hidden rounded-lg border bg-card">
              <img
                src={item.previewUrl}
                alt={`待上传截图 ${index + 1}`}
                className="cover-media aspect-[16/9] w-full object-cover"
              />
              <div className="flex items-center justify-between gap-1 p-1">
                <span className="truncate px-1 text-xs text-muted-foreground">
                  {item.file.name}
                </span>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`移除待上传图片 ${item.file.name}`}
                  onClick={() => {
                    URL.revokeObjectURL(item.previewUrl);
                    onPendingChange(pending.filter((entry) => entry.key !== item.key));
                  }}
                >
                  <Trash2 aria-hidden="true" className="size-4 text-destructive" />
                </Button>
              </div>
            </li>
          ))}

          {images.map((image, index) => (
            <li key={image.id} className="overflow-hidden rounded-lg border bg-card">
              <div className="relative">
                <img
                  // thumb_url is a backend capability URL — used verbatim (CONTRACT §14.3).
                  src={image.thumb_url}
                  alt={image.alt_text ?? `工具截图 ${index + 1}`}
                  loading="lazy"
                  decoding="async"
                  className="cover-media aspect-[16/9] w-full object-cover"
                />
                {cover?.id === image.id ? (
                  <span className="absolute left-1 top-1 rounded bg-background/90 px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset ring-border">
                    封面
                  </span>
                ) : null}
              </div>
              <div className="flex items-center gap-0.5 p-1">
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`将第 ${index + 1} 张左移`}
                  disabled={disabled || index === 0}
                  onClick={() => void move(index, -1)}
                >
                  <ArrowLeft aria-hidden="true" className="size-4" />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`将第 ${index + 1} 张右移`}
                  disabled={disabled || index === images.length - 1}
                  onClick={() => void move(index, 1)}
                >
                  <ArrowRight aria-hidden="true" className="size-4" />
                </Button>
                {cover?.id !== image.id ? (
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`将第 ${index + 1} 张设为封面`}
                    disabled={disabled}
                    onClick={() => void makeCover(image)}
                  >
                    <Star aria-hidden="true" className="size-4" />
                  </Button>
                ) : null}
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  className="ml-auto"
                  aria-label={`删除第 ${index + 1} 张截图`}
                  disabled={disabled}
                  onClick={() => void removeImage(image)}
                >
                  <Trash2 aria-hidden="true" className="size-4 text-destructive" />
                </Button>
              </div>
              {image.sha256 ? (
                <div className="flex items-center gap-1 px-1 pb-1">
                  <span className="truncate font-mono text-xs text-muted-foreground">
                    {image.sha256.slice(0, 16)}…
                  </span>
                  <CopyButton
                    value={image.sha256}
                    label={`复制第 ${index + 1} 张截图的 SHA256`}
                    className="size-6"
                  />
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <UploadCloud aria-hidden="true" className="size-3.5" />
          还没有截图，门户卡片会显示分类色块占位图。
        </p>
      )}

      {progress !== null ? (
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Loader2 aria-hidden="true" className="size-3.5 animate-spin" />
          正在上传图片…
        </p>
      ) : null}
    </div>
  );
}
