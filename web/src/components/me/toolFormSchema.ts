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
    /**
     * M8 · F10（契约 §23.5）：作者自述的**单次使用**预计节省分钟数。
     *
     * 表单里是**字符串**（受控 `<Input>` 无法区分「空」与「0」，而契约明确说
     * `NULL` 不是一个可以当成 0 的值），提交前由 `savingMinutesToPayload()` 转换：
     * 空串 → `null`，绝不变成 `0`。校验 1 ~ 1440 的整数。
     */
    estimated_saving_minutes: z.string(),
  })
  .superRefine((values, ctx) => {
    const raw = values.estimated_saving_minutes.trim();
    if (raw !== "") {
      if (!/^\d+$/.test(raw)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["estimated_saving_minutes"],
          message: "请填写整数分钟数（例如 30）",
        });
      } else {
        const minutes = Number.parseInt(raw, 10);
        if (minutes < 1 || minutes > 1440) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: ["estimated_saving_minutes"],
            message: "预计节省时长需在 1 ~ 1440 分钟之间（最大 24 小时）",
          });
        }
      }
    }
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
  /**
   * M8 · F10。**可选**：`undefined` = 不改动服务端已有值。
   *
   * 后端实现把 `estimated_saving_minutes` 也放进了 `ToolDetail`（`openapi.json` 为
   * 形状权威），所以编辑页能回显当前值，这里也就**总是**带上该字段：留空 = 提交
   * `null` = 清空（与创建态一致，也与「留空必须真的提交 null」的要求一致）。
   */
  estimated_saving_minutes?: number | null;
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
  estimated_saving_minutes: string;
}

/**
 * 表单里的字符串 → 请求体里的整数或 `null`（契约 §23.5）。
 *
 * 「留空必须真的提交 null，不要提交 0」—— 这个函数是那句话的唯一实现点：
 * 空串、纯空白、非法值一律得到 `null`，任何情况下都不会把空值变成 `0`。
 */
export function savingMinutesToPayload(raw: string): number | null {
  const trimmed = raw.trim();
  if (trimmed === "" || !/^\d+$/.test(trimmed)) return null;
  const minutes = Number.parseInt(trimmed, 10);
  return minutes >= 1 && minutes <= 1440 ? minutes : null;
}

/**
 * Form values → request body. `webapp_url` is only meaningful for `webapp`
 * (`ToolCreateRequest.webapp_url` max 1024); blank strings become `null` so a
 * cleared field is actually cleared.
 *
 * `estimated_saving_minutes` **总是**被带上（空 → `null`）：创建与编辑同语义，
 * 因此「留空必须真的提交 null，不要提交 0」在任何一条路径上都成立。
 */
export function toMetadataPayload(
  values: ToolMetadataInput,
  options: { includeSavingMinutes?: boolean } = { includeSavingMinutes: true },
): ToolMetadataPayload {
  const trimmedOrNull = (value: string) => {
    const trimmed = value.trim();
    return trimmed === "" ? null : trimmed;
  };
  const payload: ToolMetadataPayload = {
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
  if (options.includeSavingMinutes) {
    payload.estimated_saving_minutes = savingMinutesToPayload(values.estimated_saving_minutes);
  }
  return payload;
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
    estimated_saving_minutes: "",
  };
}

/**
 * `ToolDetail` → form values (edit mode).
 *
 * `webapp_health_url` is not part of `ToolDetail` (docs/03 §3.4 does not return
 * it) so the health-check input starts empty and is only sent when the user
 * fills it in — see the report's contract gap list.
 *
 * **`estimated_saving_minutes` 可以回显**：后端把它也放进了 `ToolDetail`
 * （以 `backend/openapi.json` 为准）。契约 §23.5 的字段表里没有列这一条 ——
 * 它属于「向后兼容的新增字段」，前端跟上，已在报告里登记。
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
    estimated_saving_minutes:
      tool.estimated_saving_minutes === null ? "" : String(tool.estimated_saving_minutes),
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
