import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Info, Loader2, TriangleAlert } from "lucide-react";
import * as React from "react";
import { useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";

import { ApiError, getErrorMessage } from "@/api/client";
import { readDuplicateOf } from "@/api/engagement";
import {
  createTool,
  fetchMyVersions,
  myToolDetailQueryKey,
  myVersionsQueryKey,
  replaceAcl,
  submitTool,
  updateTool,
  uploadImage,
  uploadVersion,
  withdrawTool,
} from "@/api/me";
import { categoriesQueryKey, fetchCategories } from "@/api/tools";
import type {
  DuplicateVersionMatch,
  ToolDetail,
  ToolImage,
  ToolType,
  VersionUploadResponse,
} from "@/api/types";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import type { AclDraftEntry } from "@/components/me/AclEditor";
import type { PendingImage } from "@/components/me/ImageUploader";
import { MetadataSections } from "@/components/me/MetadataSections";
import { isSkillParseError, MAX_UPLOAD_BYTES, type UploadedFile } from "@/components/me/ToolTypeFields";
import { DuplicateUploadNotice } from "@/components/tools/DuplicateUploadNotice";
import {
  emptyFormValues,
  emptyVersionContent,
  savingMinutesToPayload,
  toMetadataPayload,
  toolToFormValues,
  toolFormSchema,
  type ToolFormValues,
  type VersionContentDraft,
} from "@/components/me/toolFormSchema";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Form } from "@/components/ui/form";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatFileSize, formatRelativeTime } from "@/lib/format";

/** docs/04 §6.7: autosave at most once per 30s — SQLite write pressure is real. */
const AUTOSAVE_INTERVAL_MS = 30_000;

/** `true` within the first minute, so the header can say 「已保存 · 刚刚」. */
function isJustNow(at: Date): boolean {
  return Date.now() - at.getTime() < 60_000;
}

/**
 * Last successful save, keyed by tool id. The form remounts when the create flow
 * navigates from `/me/tools/new` to `/me/tools/{id}/edit`, so a component-local
 * timestamp would forget that the draft was just written; a module-level map
 * survives the route change for the lifetime of the tab (never persisted).
 */
const lastSavedAtByTool = new Map<number, Date>();

/**
 * M8 · F9：最近一次上传的**去重命中**，按工具 id 记住。
 *
 * 为什么需要模块级：创建流程上传完版本后会从 `/me/tools/new` 跳到
 * `/me/tools/{id}/edit`，本组件被**重建**，组件内的提示 state 会随之丢失
 * —— 那样「新建工具时上传了重复文件」这条路径就永远看不到提示。
 * 与 `lastSavedAtByTool` 同一套做法（只活在当前标签页，不落任何持久存储）。
 */
const lastDuplicateByTool = new Map<number, DuplicateVersionMatch>();

/** Newest entry in `lastSavedAtByTool` — the create flow's tool id is unknown to
 *  the remounted form until its detail query lands. */
function latestSavedAt(): Date | null {
  let newest: Date | null = null;
  for (const at of lastSavedAtByTool.values()) {
    if (newest === null || at.getTime() > newest.getTime()) newest = at;
  }
  return newest;
}

interface ApiErrorState {
  code: string;
  message: string;
}

/**
 * 默认版本号：新工具 `1.0.0`；已有当前版本时下一个 patch。
 * 用户可改（CONTRACT §17.3），这里只是预填值。
 */
function defaultVersionLabel(tool: ToolDetail | null): string {
  const current = tool?.current_version?.version ?? null;
  if (!current) return "1.0.0";
  const match = /^(\d+)\.(\d+)\.(\d+)$/.exec(current);
  if (!match) return current;
  return `${match[1] ?? "1"}.${match[2] ?? "0"}.${Number.parseInt(match[3] ?? "0", 10) + 1}`;
}

export interface ToolFormProps {
  mode: "create" | "edit";
  tool?: ToolDetail | null;
  onCreated: (tool: ToolDetail) => void;
  onSubmitted: () => void;
  onDirtyChange?: (dirty: boolean) => void;
}

type Busy = "draft" | "submit" | "withdraw" | null;

/**
 * 工具编辑器表单（docs/04 §6.7）. One scrollable page, five sections.
 *
 * Everything the user types is metadata except the type-specific content, which
 * is a *version*: metadata goes through `PATCH /me/tools/{id}` (no re-approval,
 * FR-TOOL-13) while content goes through `uploadVersion`. That split is why the
 * two save paths look different below.
 */
export function ToolForm({ mode, tool = null, onCreated, onSubmitted, onDirtyChange }: ToolFormProps) {
  return (
    <ToolFormInner
      // Remounting with a fresh key re-seeds every field when a *different* tool
      // is opened. `ToolEditorPage` only renders the form once the detail query
      // resolved, so `initialValues` is always the authoritative server state and
      // no "sync state in an effect" pass is needed.
      key={tool?.id ?? "create"}
      mode={mode}
      tool={tool}
      initialValues={tool ? toolToFormValues(tool) : emptyFormValues("file")}
      initialAcl={tool ? toolToAcl(tool) : []}
      initialImages={tool?.images ?? []}
      onCreated={onCreated}
      onSubmitted={onSubmitted}
      onDirtyChange={onDirtyChange}
    />
  );
}

function ToolFormInner({
  mode,
  tool = null,
  initialValues,
  initialAcl,
  initialImages,
  onCreated,
  onSubmitted,
  onDirtyChange,
}: ToolFormProps & {
  initialValues: ToolFormValues;
  initialAcl: AclDraftEntry[];
  initialImages: ToolImage[];
}) {
  const queryClient = useQueryClient();
  const form = useForm<ToolFormValues>({
    resolver: zodResolver(toolFormSchema),
    defaultValues: initialValues,
    mode: "onSubmit",
  });

  const [toolId, setToolId] = React.useState<number | null>(tool?.id ?? null);
  /**
   * 版本号（CONTRACT §17.3）：FR-VER-01 是 P0 且要求「手填」，所以首版也是可编辑的。
   * 预填默认值（新工具 `1.0.0`，已有版本时下一个 patch），失焦/提交时做唯一性校验。
   */
  const [versionLabel, setVersionLabel] = React.useState<string>(() => defaultVersionLabel(tool));
  const [versionError, setVersionError] = React.useState<string | null>(null);
  /** 最近一次上传是否因 Skill 包解析失败而失败（跨 await 传递，见 uploadContent）。 */
  const skillParseErrorRef = React.useRef<{ code: string; message: string } | null>(null);
  const [aclEntries, setAclEntries] = React.useState<AclDraftEntry[]>(initialAcl);
  const [images, setImages] = React.useState<ToolImage[]>(initialImages);
  const [pendingImages, setPendingImages] = React.useState<PendingImage[]>([]);
  const [versionContent, setVersionContent] = React.useState<VersionContentDraft>(() =>
    tool?.tool_type === "prompt"
      ? { ...emptyVersionContent(), promptContent: tool.prompt?.content ?? "" }
      : emptyVersionContent(),
  );
  const existingVersionsQuery = useQuery({
    queryKey: myVersionsQueryKey(toolId ?? 0),
    queryFn: ({ signal }) => fetchMyVersions(toolId ?? 0, signal),
    enabled: toolId !== null,
    staleTime: 30_000,
  });
  const existingVersionLabels = React.useMemo(
    () => new Set((existingVersionsQuery.data ?? []).map((item) => item.version)),
    [existingVersionsQuery.data],
  );

  const [uploadedFile, setUploadedFile] = React.useState<UploadedFile | null>(() => {
    // Surface the version already on the server so the editor does not look
    // empty and 提交审批 stays meaningful without a re-upload. A *draft* has no
    // `current_version` — its version is `pending_version`, which carries only
    // id + version (docs/03 §3.4), and that is enough to allow submitting.
    if (tool?.current_version) {
      return {
        name: tool.current_version.file_name ?? "—",
        size: tool.current_version.file_size ?? 0,
        sha256: tool.current_version.file_sha256 ?? "",
        version: tool.current_version.version,
      };
    }
    if (tool?.pending_version) {
      return {
        name: "—",
        size: 0,
        sha256: "",
        version: tool.pending_version.version,
      };
    }
    return null;
  });
  const [skillError, setSkillError] = React.useState<ApiErrorState | null>(() =>
    tool?.current_version?.skill?.parse_error
      ? { code: "SKILL_PARSE_FAILED", message: tool.current_version.skill.parse_error }
      : null,
  );
  const [formError, setFormError] = React.useState<ApiErrorState | null>(null);
  /** M8 · F9：最近一次上传的去重命中（编辑器里还有第二个上传入口）。 */
  const [duplicateHit, setDuplicateHit] = React.useState<DuplicateVersionMatch | null>(() =>
    tool ? (lastDuplicateByTool.get(tool.id) ?? null) : null,
  );

  /** 关闭提示：同时清掉模块级的记忆，重新上传前不再出现。 */
  const dismissDuplicate = React.useCallback(() => {
    setDuplicateHit(null);
    if (toolId !== null) lastDuplicateByTool.delete(toolId);
  }, [toolId]);
  const [busy, setBusy] = React.useState<Busy>(null);
  const [uploadProgress, setUploadProgress] = React.useState<number | null>(null);
  const [dragging, setDragging] = React.useState(false);
  const [savedAt, setSavedAtState] = React.useState<Date | null>(() =>
    tool ? (lastSavedAtByTool.get(tool.id) ?? latestSavedAt()) : null,
  );
  const setSavedAt = React.useCallback(
    (at: Date) => {
      if (toolId !== null) lastSavedAtByTool.set(toolId, at);
      setSavedAtState(at);
    },
    [toolId],
  );
  const [typeSwitchTo, setTypeSwitchTo] = React.useState<ToolType | null>(null);

  const uploadAbortRef = React.useRef<AbortController | null>(null);
  const savingRef = React.useRef(false);
  const lastAutosaveRef = React.useRef(0);

  const toolType = useWatch({ control: form.control, name: "tool_type" });
  const summary = useWatch({ control: form.control, name: "summary" }) ?? "";
  const visibility = useWatch({ control: form.control, name: "visibility" });

  const locked = tool?.status === "pending" || tool?.status === "pending_update";
  const isSubmitting = busy === "submit";

  const categoriesQuery = useQuery({
    queryKey: categoriesQueryKey,
    queryFn: ({ signal }) => fetchCategories(signal),
    staleTime: 5 * 60_000,
  });

  /* ---- dirty tracking: beforeunload + autosave + the parent's guard ---- */
  // `aclEntries` is seeded from the server, so comparing lengths would mark a
  // restricted tool dirty forever. Compare against the loaded snapshot instead.
  const aclChanged =
    aclEntries.length !== initialAcl.length ||
    aclEntries.some((entry, index) => {
      const original = initialAcl[index];
      return (
        original === undefined ||
        original.subject_type !== entry.subject_type ||
        original.subject_id !== entry.subject_id ||
        original.can_download !== entry.can_download
      );
    });
  const dirty = form.formState.isDirty || aclChanged || pendingImages.length > 0;
  React.useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);

  React.useEffect(() => {
    if (!dirty) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      // Legacy browsers need returnValue to be set; the string is ignored.
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);

  /** PATCH metadata + PUT the ACL list. Returns false when the server refuses. */
  const saveMetadata = React.useCallback(
    async (values: ToolFormValues, silent: boolean): Promise<boolean> => {
      const id = toolId;
      if (id === null) return false;
      /*
       * M8 · F10：`estimated_saving_minutes` **总是**进 PATCH 体 —— 与创建态同语义
       * （留空 → `null` = 未填写，**不是 0**，契约 §23.3）。后端把该字段也放进了
       * `ToolDetail`（`openapi.json` 为准），所以编辑页能回显，不存在「一次自动保存
       * 把作者已填的值抹掉」的情况。
       */
      try {
        await updateTool(id, toMetadataPayload(values, { includeSavingMinutes: true }));
        await replaceAcl(id, {
          visibility: values.visibility,
          entries: aclEntries.map((entry) => ({
            subject_type: entry.subject_type,
            subject_id: entry.subject_id,
            can_download: entry.can_download,
          })),
        });
        if (!silent) setSavedAt(new Date());
        return true;
      } catch (error) {
        if (error instanceof ApiError && error.is("TOOL_NOT_EDITABLE")) {
          // FR-TOOL-11: content is frozen until the pending submission is withdrawn.
          setFormError({
            code: error.code,
            message: "待审状态下不可编辑，请先撤回提交后再修改。",
          });
          return false;
        }
        if (!silent) setFormError({ code: "error", message: getErrorMessage(error) });
        return false;
      }
    },
    [aclEntries, setSavedAt, toolId],
  );

  /* ---- autosave: 30s while dirty + on blur (docs/04 §6.7) ---- */
  React.useEffect(() => {
    if (toolId === null || locked) return;
    const timer = window.setInterval(() => {
      const values = form.getValues();
      if (!form.formState.isDirty || savingRef.current || uploadProgress !== null) return;
      savingRef.current = true;
      void (async () => {
        const ok = await saveMetadata(values, true);
        if (ok) {
          form.reset(values, { keepValues: true });
          setSavedAt(new Date());
          lastAutosaveRef.current = Date.now();
        }
        savingRef.current = false;
      })();
    }, AUTOSAVE_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [form, locked, saveMetadata, setSavedAt, toolId, uploadProgress]);

  function handleBlurAutosave() {
    if (toolId === null || locked || savingRef.current || uploadProgress !== null) return;
    // docs/04 §6.7 caps autosave at once per 30s (SQLite write pressure);
    // tabbing through four fields must not fire four PATCHes.
    if (Date.now() - lastAutosaveRef.current < AUTOSAVE_INTERVAL_MS) return;
    const values = form.getValues();
    if (!form.formState.isDirty) return;
    savingRef.current = true;
    void (async () => {
      const ok = await saveMetadata(values, true);
      if (ok) {
        form.reset(values, { keepValues: true });
        setSavedAt(new Date());
        lastAutosaveRef.current = Date.now();
      }
      savingRef.current = false;
    })();
  }

  /* ---- version content ---- */
  function onDropFile(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files[0];
    if (file) pickFile(file);
  }

  function pickFile(file: File) {
    if (file.size > MAX_UPLOAD_BYTES) {
      setFormError({
        code: "PAYLOAD_TOO_LARGE",
        message: `文件超过 ${formatFileSize(MAX_UPLOAD_BYTES)} 限制`,
      });
      return;
    }
    setFormError(null);
    setSkillError(null);
    setVersionContent((previous) => ({ ...previous, file }));
    setUploadedFile(null);
  }

  /** Upload the queued version content; returns the server response or null. */
  async function uploadContent(
    id: number,
    version: string,
    autoSubmit: boolean,
  ): Promise<VersionUploadResponse | null> {
    const controller = new AbortController();
    uploadAbortRef.current = controller;
    setUploadProgress(0);
    try {
      const result = await uploadVersion(
        id,
        {
          version,
          changelog_md: "",
          file: versionContent.file,
          prompt_content: toolType === "prompt" ? versionContent.promptContent : null,
          webapp_url: toolType === "webapp" ? form.getValues().webapp_url.trim() : null,
          auto_submit: autoSubmit,
        },
        {
          signal: controller.signal,
          onProgress: (loaded, total) =>
            setUploadProgress(total > 0 ? Math.round((loaded / total) * 100) : null),
        },
      );
      setUploadedFile({
        name: result.file_name ?? versionContent.file?.name ?? "—",
        size: result.file_size ?? versionContent.file?.size ?? 0,
        sha256: result.file_sha256 ?? "",
        version: result.version,
      });
      // M8 · F9：上传成功是主结果，去重只是**附加**提示（非阻塞、可关闭）。
      const hit = readDuplicateOf(result);
      setDuplicateHit(hit);
      // 记住它：创建流程随后会跳到编辑页并重建本组件（见 lastDuplicateByTool）。
      if (hit) lastDuplicateByTool.set(id, hit);
      else lastDuplicateByTool.delete(id);
      skillParseErrorRef.current = null;
      setSkillError(
        result.skill?.parse_error
          ? { code: "SKILL_PARSE_FAILED", message: result.skill.parse_error }
          : null,
      );
      // Only the *file* is consumed by the upload; the prompt body stays in the
      // form because it is also the tool's detail content (`ToolDetail.prompt`).
      setVersionContent((previous) => ({ ...previous, file: null }));
      await queryClient.invalidateQueries({ queryKey: myVersionsQueryKey(id) });
      return result;
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return null;
      // A Skill package that fails to parse is a warning, not a dead end:
      // the draft stays saveable (FR-TOOL-06).
      if (error instanceof ApiError && error.is("VERSION_EXISTS")) {
        // 与服务端保持同一句话（VersionUploadDialog 也是这条文案）
        setVersionError(`版本 ${versionLabel.trim()} 已存在，请换一个版本号`);
        return null;
      }
      if (error instanceof ApiError && isSkillParseError(error.code)) {
        setSkillError({ code: error.code, message: error.message });
        // Remember it for the caller: after `await`, the state update is not
        // visible yet, and the caller must not navigate away (the warning lives
        // in this component).
        skillParseErrorRef.current = { code: error.code, message: error.message };
        return null;
      }
      setFormError({ code: "upload", message: getErrorMessage(error) });
      return null;
    } finally {
      uploadAbortRef.current = null;
      setUploadProgress(null);
    }
  }

  /* ---- submit paths ---- */
  async function handleSubmit(onSubmit: boolean) {
    // `trigger()` runs the zod schema and focuses/scrolls the first bad field
    // (docs/04 §6.7「提交审批前校验：滚动到第一个错误字段并聚焦」).
    const valid = await form.trigger(undefined, { shouldFocus: false });
    if (!valid) {
      scrollToFirstError(form.formState.errors as Record<string, unknown>);
      return;
    }

    const values = form.getValues();
    // 只有「本次要上传版本内容」时才需要版本号（纯元信息保存不涉及版本）
    if (hasNewVersionContent(values)) {
      const versionIssue = validateVersionLabel(versionLabel);
      if (versionIssue) {
        setVersionError(versionIssue);
        document.getElementById("tool-field-version")?.focus();
        return;
      }
      setVersionError(null);
    }
    setFormError(null);
    setBusy(onSubmit ? "submit" : "draft");

    try {
      let id = toolId;
      if (id === null) {
        const created = await createTool({
          name: values.name.trim(),
          summary: values.summary.trim(),
          description_md: values.description_md,
          tool_type: values.tool_type,
          category_id: values.category_id,
          tags: values.tags,
          visibility: values.visibility,
          webapp_url: values.tool_type === "webapp" ? values.webapp_url.trim() : null,
          webapp_health_url:
            values.tool_type === "webapp" ? values.webapp_health_url.trim() || null : null,
          /*
           * M8 · F10：**总是**带上这个字段（空 → `null`，不是 `0`）。
           * 契约 §23.3：「`NULL` 表示作者未填写，不是一个可以当成 0 的值」。
           */
          estimated_saving_minutes: savingMinutesToPayload(values.estimated_saving_minutes),
        });
        setToolId(created.id);
        await afterCreate(created.id, values, created, onSubmit);
        return;
      }

      const saved = await saveMetadata(values, false);
      if (!saved) return;

      if (onSubmit) {
        if (!hasNewVersionContent(values) && !hasAnyVersionContent(values)) {
          setFormError({
            code: "no_content",
            message: "提交审批前需要先上传版本内容（文件 / URL / 提示词正文）。",
          });
          return;
        }
        if (!hasNewVersionContent(values)) {
          // Content is already on the server (uploaded here, or an existing
          // `pending_version`) — submitting the tool is enough.
          const outcome = await submitTool(id);
          toast.success(
            outcome.auto_approved ? "已提交并自动通过审批" : "已提交审批，请等待审批结果",
          );
          onSubmitted();
          return;
        }
        const result = await uploadContent(id, versionLabel.trim(), true);
        if (!result) return;
        toast.success(
          `新版本 ${result.version} 已提交审批。当前版本 ${
            tool?.current_version?.version ?? "—"
          } 继续对外提供服务。`,
        );
        onSubmitted();
        return;
      }

      if (hasNewVersionContent(values)) {
        const result = await uploadContent(id, versionLabel.trim(), false);
        if (result) toast.success("草稿已保存，版本内容已上传");
        else toast.success("草稿已保存");
      } else {
        toast.success("草稿已保存");
      }
      setSavedAt(new Date());
      form.reset(values, { keepValues: true });
      await invalidate(id);
    } catch (error) {
      handleApiError(error);
    } finally {
      setBusy(null);
    }
  }

  /**
   * First save of a brand new tool: the metadata row exists, now attach the
   * queued content. `onSubmit` decides whether the first version goes up as a
   * draft (`auto_submit: false`) or straight into the approval queue
   * (`auto_submit: true`) — the two header buttons must not behave alike.
   */
  async function afterCreate(
    id: number,
    values: ToolFormValues,
    created: ToolDetail,
    onSubmit: boolean,
  ) {
    if (values.visibility === "restricted" && aclEntries.length > 0) {
      await replaceAcl(id, {
        visibility: "restricted",
        entries: aclEntries.map((entry) => ({
          subject_type: entry.subject_type,
          subject_id: entry.subject_id,
          can_download: entry.can_download,
        })),
      });
    }
    let hasExistingImages = images.length > 0;
    for (const item of pendingImages) {
      // The first image overall becomes the cover; the rest are screenshots.
      await uploadImage(id, item.file, { kind: hasExistingImages ? "screenshot" : "cover" });
      hasExistingImages = true;
    }
    setPendingImages([]);

    if (hasNewVersionContent(values)) {
      const uploaded = await uploadContent(id, versionLabel.trim(), onSubmit);
      if (uploaded) {
        setSavedAt(new Date());
        await invalidate(id);
        if (onSubmit) {
          toast.success(
            `新版本 ${uploaded.version} 已提交审批。当前版本 ${
              created.current_version?.version ?? "—"
            } 继续对外提供服务。`,
          );
          onSubmitted();
          return;
        }
        toast.success("工具草稿已创建，版本内容已上传");
        onCreated(created);
        return;
      }
      if (skillParseErrorRef.current) {
        // FR-TOOL-06：解析失败允许保存草稿，但要让用户留在这里看到具体错误，
        // 并且「提交审批」保持禁用 + tooltip 说明原因。
        await invalidate(id);
        toast.warning("Skill 包解析未通过，草稿已保存。修正后需重新选择包再提交审批。");
        return;
      }
      toast.warning("工具草稿已创建，但版本内容上传失败，请在编辑页重试");
      onCreated(created);
      return;
    }

    if (onSubmit) {
      // Nothing to upload and no version exists yet → the server cannot accept
      // the submission (docs/01 §4.1: `draft → pending` needs ≥1 version).
      toast.warning("工具草稿已创建，请先上传版本内容再提交审批");
      onCreated(created);
      return;
    }

    toast.success("工具草稿已创建");
    // The navigate to `/me/tools/{id}/edit` remounts this form, so the
    // "已保存 · 刚刚" marker has to be seeded from the create itself.
    setSavedAt(new Date());
    await invalidate(id);
    onCreated(created);
  }

  async function invalidate(id: number) {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: myToolDetailQueryKey(id) }),
      // Prefix invalidation on purpose: the list query key carries the active
      // tab's statuses and page size, so `myToolsQueryKey()` (no args) would not
      // match anything (docs/04 §9 query-key normalisation).
      queryClient.invalidateQueries({ queryKey: ["me", "tools"] }),
    ]);
  }

  function handleApiError(error: unknown) {
    if (error instanceof ApiError && error.is("TOOL_NOT_EDITABLE")) {
      setFormError({ code: error.code, message: "待审状态下不可编辑，请先撤回提交后再修改。" });
      return;
    }
    setFormError({ code: "error", message: getErrorMessage(error) });
  }

  async function handleWithdraw() {
    if (toolId === null) return;
    setFormError(null);
    setBusy("withdraw");
    try {
      await withdrawTool(toolId);
      toast.success("已撤回提交，现在可以继续编辑");
      await invalidate(toolId);
    } catch (error) {
      handleApiError(error);
    } finally {
      setBusy(null);
    }
  }

  /**
   * 版本号校验：非空 + 同工具内唯一。
   *
   * 客户端先按已拉取的版本清单给出即时反馈，服务端的 `VERSION_EXISTS` 仍然兜底
   * （`uploadContent` 的 catch 会把它映射到同一个内联提示）。
   */
  function validateVersionLabel(label: string): string | null {
    const trimmed = label.trim();
    if (trimmed === "") return "请填写版本号";
    if (trimmed.length > 64) return "版本号不能超过 64 个字符";
    // 已经上传过、并且没有再传新内容的那个版本不算冲突
    if (existingVersionLabels.has(trimmed) && trimmed !== uploadedFile?.version) {
      return `版本 ${trimmed} 已存在，请换一个版本号`;
    }
    return null;
  }

  function handleVersionBlur() {
    setVersionError(validateVersionLabel(versionLabel));
  }

  /**
   * `true` only when the user has supplied *new* content that still has to be
   * uploaded. Already-uploaded content (`uploadedFile`) is a version on the
   * server; re-uploading it would create a duplicate version.
   */
  function hasNewVersionContent(values: ToolFormValues): boolean {
    if (values.tool_type === "file" || values.tool_type === "skill") {
      return versionContent.file !== null;
    }
    if (values.tool_type === "prompt") return versionContent.promptContent.trim() !== "";
    return values.webapp_url.trim() !== "" && tool?.current_version == null;
  }

  /** Nothing to upload, but a version already exists → the tool can be submitted. */
  function hasAnyVersionContent(values: ToolFormValues): boolean {
    // A version that already exists on the server (current OR pending) counts:
    // otherwise reopening a draft in the editor and pressing 提交审批 would be
    // rejected with "请先上传版本内容" even though the version is there.
    if (tool?.current_version || tool?.pending_version) return true;
    if (values.tool_type === "file" || values.tool_type === "skill") {
      return uploadedFile !== null;
    }
    if (values.tool_type === "prompt") return versionContent.promptContent.trim() !== "";
    return values.webapp_url.trim() !== "";
  }

  /** Anything typed into the type-specific area — decides the switch confirmation. */
  function hasTypeContent(): boolean {
    return (
      versionContent.file !== null ||
      versionContent.promptContent.trim() !== "" ||
      uploadedFile !== null ||
      (form.getValues().webapp_url ?? "").trim() !== ""
    );
  }

  /** docs/04 §6.7: scroll to and focus the first invalid field after a failed submit. */
  function scrollToFirstError(errors: Record<string, unknown>) {
    const order: Array<keyof ToolFormValues> = [
      "name",
      "summary",
      "description_md",
      "category_id",
      "tags",
      "tool_type",
      "webapp_url",
      "webapp_health_url",
      "prompt_content",
      "visibility",
    ];
    const first = order.find((key) => errors[key] !== undefined);
    if (!first) return;
    // `visibility` renders one control per option (`tool-field-visibility-public`
    // …), so fall back to the first prefix match — a plain id lookup would miss.
    const element =
      document.getElementById(`tool-field-${first}`) ??
      document.querySelector<HTMLElement>(`[id^="tool-field-${first}"], [id^="tool-field-${first}-"]`);
    if (!element) return;
    element.scrollIntoView({ behavior: "smooth", block: "center" });
    element.focus({ preventScroll: true });
  }

  // A queued file counts: on `/me/tools/new` the upload happens inside the same
  // submit flow, so `uploadedFile` is still null when the button is first used.
  const hasSkillPackage = uploadedFile !== null || versionContent.file !== null;
  const canSubmit = !locked && skillError === null && (toolType !== "skill" || hasSkillPackage);
  const errorCount = Object.keys(form.formState.errors).length;
  const skillBlockedReason = locked
    ? "待审状态下不可提交，请先撤回"
    : skillError
      ? `Skill 包解析未通过：${skillError.message}`
      : "请先选择 Skill 包；解析失败时仍可保存草稿，但不能提交审批";

  return (
    <Form {...form}>
      <form
        data-testid="tool-form"
        noValidate
        className="space-y-6 pb-24"
        onSubmit={(event) => {
          event.preventDefault();
          void handleSubmit(true);
        }}
      >
        {/* ---- sticky header ---- */}
        <div className="sticky top-14 z-20 -mx-4 flex flex-wrap items-center gap-3 border-b bg-background/95 px-4 py-3 backdrop-blur sm:-mx-6 sm:px-6">
          <div className="min-w-0">
            <h1 className="truncate text-lg font-semibold">
              {mode === "create" ? "新建工具" : `编辑工具 · ${tool?.name ?? ""}`}
            </h1>
            <p
              data-testid="autosave-indicator"
              className="text-xs text-muted-foreground"
              aria-live="polite"
            >
              {uploadProgress !== null ? (
                <span className="inline-flex items-center gap-1">
                  <Loader2 aria-hidden="true" className="size-3 animate-spin" />
                  上传中 {uploadProgress}%
                </span>
              ) : toolId === null ? (
                "新建工具：保存草稿后才会开始自动保存"
              ) : locked ? (
                "待审中：内容已锁定，撤回后可编辑"
              ) : savedAt ? (
                // docs/04 §6.7 asks for 「已保存 · 刚刚」; date-fns' zhCN relative
                // formatter renders "不到 1 分钟前" for the same instant.
                isJustNow(savedAt)
                  ? "已保存 · 刚刚"
                  : `已保存 · ${formatRelativeTime(savedAt.toISOString())}`
              ) : (
                "自动保存已开启（每 30 秒或失焦时）"
              )}
            </p>
          </div>

          <div className="ml-auto flex flex-wrap items-center gap-2">
            {locked ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={busy !== null}
                onClick={() => void handleWithdraw()}
              >
                {busy === "withdraw" ? "撤回中…" : "撤回提交"}
              </Button>
            ) : null}
            <Button
              type="button"
              variant="outline"
              disabled={busy !== null || uploadProgress !== null || locked}
              onClick={() => void handleSubmit(false)}
            >
              {busy === "draft" ? (
                <>
                  <Loader2 aria-hidden="true" className="size-4 animate-spin" />
                  保存中…
                </>
              ) : (
                "保存草稿"
              )}
            </Button>

            {!canSubmit ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span tabIndex={0}>
                    <Button type="button" disabled>
                      提交审批
                    </Button>
                  </span>
                </TooltipTrigger>
                <TooltipContent>{skillBlockedReason}</TooltipContent>
              </Tooltip>
            ) : (
              <Button type="submit" disabled={busy !== null || uploadProgress !== null}>
                {isSubmitting ? (
                  <>
                    <Loader2 aria-hidden="true" className="size-4 animate-spin" />
                    提交中…
                  </>
                ) : (
                  "提交审批"
                )}
              </Button>
            )}
          </div>
        </div>

        {locked ? (
          <Alert variant="warning">
            <TriangleAlert aria-hidden="true" />
            <AlertTitle>内容已锁定</AlertTitle>
            <AlertDescription>
              {tool?.status === "pending" ? "首次提交" : "新版本"}正在审批中，元信息与版本内容不可修改
              （FR-TOOL-11）。如需继续编辑，请先「撤回提交」。
            </AlertDescription>
          </Alert>
        ) : null}

        {skillError ? (
          // CONTRACT §14.7：Alert 已有 warning 变体，不再散落显式 amber 类。
          <Alert variant="warning" data-testid="skill-parse-error">
            <Info aria-hidden="true" />
            <AlertTitle>Skill 包解析未通过</AlertTitle>
            <AlertDescription>
              <span className="font-mono text-xs">{skillError.code}</span>：{skillError.message}
              <br />
              仍可保存草稿（FR-TOOL-06），但提交审批已禁用。
            </AlertDescription>
          </Alert>
        ) : null}

        {duplicateHit ? (
          <DuplicateUploadNotice duplicate={duplicateHit} onDismiss={dismissDuplicate} />
        ) : null}

        {formError ? (
          <Alert variant="destructive" data-testid="form-error">
            <AlertCircle aria-hidden="true" />
            <AlertTitle>操作失败</AlertTitle>
            <AlertDescription className="flex flex-wrap items-center gap-2">
              {formError.message}
              {formError.code === "TOOL_NOT_EDITABLE" && toolId !== null ? (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={busy !== null}
                  onClick={() => void handleWithdraw()}
                >
                  撤回提交
                </Button>
              ) : null}
            </AlertDescription>
          </Alert>
        ) : null}

        {errorCount > 0 ? (
          <Alert variant="destructive" data-testid="validation-summary">
            <AlertCircle aria-hidden="true" />
            <AlertTitle>有 {errorCount} 处需要修正</AlertTitle>
            <AlertDescription>请检查下方标红的字段后重新提交。</AlertDescription>
          </Alert>
        ) : null}

        {/* 版本号（FR-VER-01：SemVer 字符串，手填 —— CONTRACT §17.3） */}
        <div className="space-y-1.5 rounded-xl border bg-card p-4">
          <Label htmlFor="tool-field-version" className="text-sm font-medium">
            版本号 *
          </Label>
          <Input
            id="tool-field-version"
            value={versionLabel}
            onChange={(event) => {
              setVersionLabel(event.target.value);
              if (versionError) setVersionError(null);
            }}
            onBlur={handleVersionBlur}
            disabled={locked}
            maxLength={64}
            autoComplete="off"
            spellCheck={false}
            className="font-mono"
            aria-invalid={versionError !== null}
            aria-describedby={versionError ? "tool-field-version-error" : "tool-field-version-hint"}
          />
          {versionError ? (
            <p
              id="tool-field-version-error"
              role="alert"
              data-testid="version-error"
              className="text-xs text-destructive"
            >
              {versionError}
            </p>
          ) : (
            <p id="tool-field-version-hint" className="text-xs text-muted-foreground">
              建议语义化版本，如 1.2.0；同一工具内不可重复
            </p>
          )}
        </div>

        <MetadataSections
          form={form}
          mode={mode}
          locked={locked}
          categories={categoriesQuery.data ?? []}
          summaryLength={summary.length}
          visibility={visibility ?? "public"}
          toolType={toolType}
          images={images}
          toolId={toolId}
          pendingImages={pendingImages}
          aclEntries={aclEntries}
          dragging={dragging}
          progress={uploadProgress}
          file={versionContent.file}
          uploadedFile={uploadedFile}
          promptContent={versionContent.promptContent}
          onAutosave={handleBlurAutosave}
          onTypeChange={(next) => {
            if (hasTypeContent()) {
              setTypeSwitchTo(next);
              return;
            }
            form.setValue("tool_type", next, { shouldDirty: true });
          }}
          onPromptChange={(text) =>
            setVersionContent((previous) => ({ ...previous, promptContent: text }))
          }
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDropFile={onDropFile}
          onPickFile={pickFile}
          onRemoveFile={() => {
            setVersionContent((previous) => ({ ...previous, file: null }));
            setUploadedFile(null);
          }}
          onCancelUpload={() => uploadAbortRef.current?.abort()}
          onImagesChange={setImages}
          onPendingImagesChange={setPendingImages}
          onAclChange={setAclEntries}
        />
      </form>

      {/* ---- type switch confirmation (docs/04 §6.7) ---- */}
      <ConfirmDialog
        open={typeSwitchTo !== null}
        destructive
        title="切换类型会清空已上传的文件，是否继续？"
        description="类型决定了版本内容的形态，切换后当前已选的文件与正文会被丢弃。"
        confirmLabel="切换类型"
        onOpenChange={(open) => {
          if (!open) setTypeSwitchTo(null);
        }}
        onConfirm={() => {
          if (typeSwitchTo) {
            form.setValue("tool_type", typeSwitchTo, { shouldDirty: true });
            setVersionContent(emptyVersionContent());
            setUploadedFile(null);
            setSkillError(null);
          }
          setTypeSwitchTo(null);
        }}
      />
    </Form>
  );
}

/* ------------------------------------------------------------------------- */
/* Tool loading → form values                                                  */
/* ------------------------------------------------------------------------- */

/** `ToolDetail.acl` → editor rows (`subject_name` comes from the server). */
function toolToAcl(tool: ToolDetail): AclDraftEntry[] {
  return (tool.acl ?? []).map((entry) => ({
    subject_type: entry.subject_type,
    subject_id: entry.subject_id,
    can_download: entry.can_download,
    subject_name: entry.subject_name,
  }));
}
