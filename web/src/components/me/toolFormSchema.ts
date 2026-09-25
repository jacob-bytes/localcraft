import { z } from "zod";

import type { ToolDetail, ToolType, Visibility } from "@/api/types";

/** Mirrors `ToolCreateRequest` / `ToolUpdateRequest` (docs/01 FR-TOOL-01/05). */
export const toolFormSchema = z
  .object({
    name: z
      .string()
      .trim()
      .min(1, "请填写工具名称")
      .max(128, "工具名称最多 128 个字符"),
    summary: z
      .string()
      .trim()
      .min(1, "请填写一句话简介")
      .max(500, "简介最多 500 个字符"),
    // docs/01 FR-TOOL-01 lists 详情（Markdown）与 分类 as required; the server
    // tolerates both being empty, so this is a stricter client-side gate.
    description_md: z.string().trim().min(1, "请填写详情说明"),
    // 0 = 未选择. docs/01 FR-TOOL-01 makes 分类 required, so a non-null literal is
    // used instead of `null` to keep the inferred type simple.
    category_id: z.number().int().min(1, "请选择分类"),
    tags: z
      .array(z.string())
      .max(8, "标签不能超过 8 个")
      .refine((tags) => tags.every((tag) => tag.length <= 64), "单个标签不能超过 64 个字符"),
    tool_type: z.enum(["file", "webapp", "skill", "prompt"]),
    visibility: z.enum(["public", "restricted", "private"]),
    webapp_url: z.string(),
    webapp_health_url: z.string(),
    prompt_content: z.string(),
  })
  .superRefine((values, ctx) => {
    if (values.tool_type !== "webapp") return;
    // FR-TOOL-05: webapp 必须填内网 URL，且以 http(s):// 开头。
    if (values.webapp_url.trim() === "") {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["webapp_url"],
        message: "在线工具必须填写 URL",
      });
      return;
    }
    if (!/^https?:\/\//.test(values.webapp_url.trim())) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["webapp_url"],
        message: "URL 必须以 http:// 或 https:// 开头",
      });
    }
    if (values.webapp_health_url.trim() !== "" && !/^https?:\/\//.test(values.webapp_health_url.trim())) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["webapp_health_url"],
        message: "健康检查 URL 必须以 http:// 或 https:// 开头",
      });
    }
  });

export type ToolFormValues = z.infer<typeof toolFormSchema>;

/** Metadata only — version content travels over `uploadVersion`, never PATCH. */
export interface ToolMetadataPayload {
  name: string;
  summary: string;
  description_md: string;
  tool_type: ToolType;
  /** 0 = 未选择 (see `toolFormSchema`). */
  category_id: number;
  tags: string[];
  visibility: Visibility;
  webapp_url: string | null;
  webapp_health_url: string | null;
}

export interface ToolMetadataInput {
  name: string;
  summary: string;
  description_md: string;
  tool_type: ToolType;
  /** 0 = 未选择 (see `toolFormSchema`). */
  category_id: number;
  tags: string[];
  visibility: Visibility;
  webapp_url: string;
  webapp_health_url: string;
}

/**
 * Form values → request body. `webapp_url` is only meaningful for `webapp`
 * (`ToolCreateRequest.webapp_url` max 1024); blank strings become `null` so a
 * cleared field is actually cleared.
 */
export function toMetadataPayload(values: ToolMetadataInput): ToolMetadataPayload {
  const trimmedOrNull = (value: string) => {
    const trimmed = value.trim();
    return trimmed === "" ? null : trimmed;
  };
  return {
    name: values.name.trim(),
    summary: values.summary.trim(),
    description_md: values.description_md,
    tool_type: values.tool_type,
    category_id: values.category_id,
    tags: values.tags,
    visibility: values.visibility,
    webapp_url: values.tool_type === "webapp" ? trimmedOrNull(values.webapp_url) : null,
    webapp_health_url: values.tool_type === "webapp" ? trimmedOrNull(values.webapp_health_url) : null,
  };
}

export function emptyFormValues(toolType: ToolType = "file"): ToolFormValues {
  return {
    name: "",
    summary: "",
    description_md: "",
    category_id: 0,
    tags: [],
    tool_type: toolType,
    visibility: "public",
    webapp_url: "",
    webapp_health_url: "",
    prompt_content: "",
  };
}

/**
 * `ToolDetail` → form values (edit mode).
 *
 * `webapp_health_url` is not part of `ToolDetail` (docs/03 §3.4 does not return
 * it) so the health-check input starts empty and is only sent when the user
 * fills it in — see the report's contract gap list.
 */
export function toolToFormValues(tool: ToolDetail): ToolFormValues {
  return {
    name: tool.name,
    summary: tool.summary,
    description_md: tool.description_md,
    category_id: tool.category?.id ?? 0,
    tags: [...tool.tags],
    tool_type: tool.tool_type,
    visibility: tool.visibility,
    webapp_url: tool.webapp_url ?? "",
    webapp_health_url: "",
    prompt_content: tool.prompt?.content ?? "",
  };
}

/** One version's content, held in the form until it is uploaded. */
export interface VersionContentDraft {
  file: File | null;
  promptContent: string;
  webappUrl: string;
}

export function emptyVersionContent(): VersionContentDraft {
  return { file: null, promptContent: "", webappUrl: "" };
}
