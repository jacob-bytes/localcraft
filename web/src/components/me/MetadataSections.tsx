import * as React from "react";
import type { UseFormReturn } from "react-hook-form";

import type { Category, ToolImage, ToolType } from "@/api/types";
import { AclEditor, type AclDraftEntry } from "@/components/me/AclEditor";
import { ImageUploader, type PendingImage } from "@/components/me/ImageUploader";
import { TagInput } from "@/components/me/TagInput";
import { MarkdownEditor, ToolTypeRadioGroup } from "@/components/me/ToolFormParts";
import { TypeSpecificSection, type UploadedFile } from "@/components/me/ToolTypeFields";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import {
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { ToolFormValues } from "@/components/me/toolFormSchema";
import { cn } from "@/lib/utils";

export interface MetadataSectionsProps {
  form: UseFormReturn<ToolFormValues>;
  mode: "create" | "edit";
  locked: boolean;
  categories: Category[];
  summaryLength: number;
  visibility: ToolFormValues["visibility"];
  toolType: ToolType;
  images: ToolImage[];
  /** `null` in create mode — the uploader queues files until the tool exists. */
  toolId: number | null;
  pendingImages: PendingImage[];
  aclEntries: AclDraftEntry[];
  dragging: boolean;
  progress: number | null;
  file: File | null;
  uploadedFile: UploadedFile | null;
  promptContent: string;
  onAutosave: () => void;
  onTypeChange: (next: ToolType) => void;
  onPromptChange: (value: string) => void;
  onDragOver: (event: React.DragEvent<HTMLDivElement>) => void;
  onDragLeave: () => void;
  onDropFile: (event: React.DragEvent<HTMLDivElement>) => void;
  onPickFile: (file: File) => void;
  onRemoveFile: () => void;
  onCancelUpload: () => void;
  onImagesChange: (images: ToolImage[]) => void;
  onPendingImagesChange: (pending: PendingImage[]) => void;
  onAclChange: (entries: AclDraftEntry[]) => void;
}

/**
 * 工具的五个表单区块（docs/04 §6.7）：基本信息 / 工具类型 / 详情说明 / 截图 /
 * 可见性。Extracted from `ToolForm` so each file stays readable; the sticky
 * header, the alerts and the save actions stay in `ToolForm`.
 */
export function MetadataSections(props: MetadataSectionsProps) {
  const {
    form,
    mode,
    locked,
    categories,
    summaryLength,
    toolType,
    dragging,
    uploadedFile,
    onAutosave,
    onTypeChange,
  } = props;
  return (
    <>
<section aria-labelledby="section-basic" className="space-y-4 rounded-xl border bg-card p-4">
  <h2 id="section-basic" className="text-base font-semibold">
    基本信息
  </h2>

  <FormField
    control={form.control}
    name="name"
    render={({ field }) => (
      <FormItem>
        <FormLabel htmlFor="tool-field-name">工具名称 *</FormLabel>
        <FormControl>
          <Input
            {...field}
            id="tool-field-name"
            disabled={locked}
            maxLength={128}
            onBlur={onAutosave}
          />
        </FormControl>
        <FormMessage />
      </FormItem>
    )}
  />

  <FormField
    control={form.control}
    name="summary"
    render={({ field }) => (
      <FormItem>
        <FormLabel htmlFor="tool-field-summary">简介 *</FormLabel>
        <FormControl>
          <Input
            {...field}
            id="tool-field-summary"
            disabled={locked}
            maxLength={500}
            onBlur={onAutosave}
          />
        </FormControl>
        <FormDescription>
          一句话说明用途，将显示在门户卡片上（
          <span className="tabular-nums">{summaryLength}</span>/500）
        </FormDescription>
        <FormMessage />
      </FormItem>
    )}
  />

  <FormField
    control={form.control}
    name="category_id"
    render={({ field }) => (
      <FormItem>
        <FormLabel htmlFor="tool-field-category_id">分类</FormLabel>
        <Select
          value={field.value > 0 ? String(field.value) : ""}
          disabled={locked}
          onValueChange={(value) => {
            field.onChange(Number.parseInt(value, 10));
            onAutosave();
          }}
        >
          <FormControl>
            <SelectTrigger id="tool-field-category_id" aria-label="分类">
              <SelectValue placeholder="选择分类" />
            </SelectTrigger>
          </FormControl>
          <SelectContent>
            {categories.map((category) => (
              <SelectItem key={category.id} value={String(category.id)}>
                {category.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <FormMessage />
      </FormItem>
    )}
  />

  <FormField
    control={form.control}
    name="tags"
    render={({ field }) => (
      <FormItem>
        <FormLabel htmlFor="tool-field-tags">标签</FormLabel>
        <TagInput
          inputId="tool-field-tags"
          value={field.value}
          disabled={locked}
          onChange={(tags) => {
            field.onChange(tags);
            onAutosave();
          }}
        />
        <FormMessage />
      </FormItem>
    )}
  />
</section>

  {/*
    M8 · F10（契约 §23.5）：预计节省时长。

    这一节的三条文案是任务书点名要求的，缺一不可：
    1. 明确是**单次使用**的预计节省分钟数（不是总量、不是每次迭代）；
    2. 说明它会被平台用于效率估算 —— 这不是一个可有可无的备注字段；
    3. 明说**留空会让该工具从估算里消失**，不要让作者以为不填也没事。
    第 3 条在编辑态另有「留空 = 不修改」的语义（契约没有把该字段放进
    `ToolDetail`，编辑页读不到当前值），所以在编辑态额外给一行说明。
  */}
  <section
    aria-labelledby="section-efficiency"
    data-testid="section-efficiency"
    className="space-y-4 rounded-xl border bg-card p-4"
  >
    <h2 id="section-efficiency" className="text-base font-semibold">
      效率估算
    </h2>
    <FormField
      control={form.control}
      name="estimated_saving_minutes"
      render={({ field }) => (
        <FormItem>
          <FormLabel htmlFor="tool-field-estimated_saving_minutes">
            预计节省时长（分钟，可选）
          </FormLabel>
          <FormControl>
            <Input
              {...field}
              id="tool-field-estimated_saving_minutes"
              type="number"
              inputMode="numeric"
              min={1}
              max={1440}
              step={1}
              disabled={locked}
              placeholder="例如 30"
              onBlur={onAutosave}
            />
          </FormControl>
          <FormDescription className="space-y-1">
            <span className="block">
              填「单次使用」这个工具大约能省下多少分钟（1 ~ 1440，最大 24 小时）。
            </span>
            <span className="block">
              这个数字会被平台汇总成「效率估算」，显示在管理概览里，并标注为基于作者自述的估算（非实测）。
            </span>
            <span className="block">
              {mode === "create"
                ? "留空也允许；但如果作者都不填，平台就算不出节省量，这个工具也不会被计入估算覆盖率。"
                : "留空 = 清空该值：这个工具会从平台的效率估算里消失，不再计入覆盖率。"}
            </span>
          </FormDescription>
          <FormMessage />
        </FormItem>
      )}
    />
  </section>
  <section aria-labelledby="section-type" className="space-y-4 rounded-xl border bg-card p-4">
    <h2 id="section-type" className="text-base font-semibold">
      工具类型
    </h2>
    <FormField
      control={form.control}
      name="tool_type"
      render={({ field }) => (
        <FormItem>
          <ToolTypeRadioGroup
            value={field.value}
            readOnly={mode === "edit"}
            onChange={(next) => {
              // Switching a type that already has content must be confirmed by
              // the caller (docs/04 §6.7) — it owns the AlertDialog.
              if (next !== field.value) onTypeChange(next);
            }}
          />
          <FormMessage />
        </FormItem>
      )}
    />

    <TypeSpecificSection
      toolType={toolType}
      locked={locked}
      dragging={dragging}
      progress={props.progress}
      file={props.file}
      uploadedFile={uploadedFile}
      promptContent={props.promptContent}
      onPromptChange={props.onPromptChange}
      onDragOver={props.onDragOver}
      onDragLeave={props.onDragLeave}
      onDropFile={props.onDropFile}
      onPickFile={props.onPickFile}
      onRemoveFile={props.onRemoveFile}
      onCancelUpload={props.onCancelUpload}
      webappField={
        <FormField
          control={form.control}
          name="webapp_url"
          render={({ field }) => (
            <FormItem>
              <FormLabel htmlFor="tool-field-webapp_url">工具 URL *</FormLabel>
              <FormControl>
                <Input
                  {...field}
                  id="tool-field-webapp_url"
                  disabled={locked}
                  placeholder="http://内网地址/"
                  onBlur={onAutosave}
                />
              </FormControl>
              <FormDescription>
                须以 http:// 或 https:// 开头（FR-TOOL-05，不做可达性阻断校验）
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
      }
      healthField={
        <FormField
          control={form.control}
          name="webapp_health_url"
          render={({ field }) => (
            <FormItem>
              <FormLabel htmlFor="tool-field-webapp_health_url">健康检查 URL（可选）</FormLabel>
              <FormControl>
                <Input
                  {...field}
                  id="tool-field-webapp_health_url"
                  disabled={locked}
                  placeholder="http://内网地址/healthz"
                  onBlur={onAutosave}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
      }
    />
  </section>

  
  <section aria-labelledby="section-detail" className="space-y-4 rounded-xl border bg-card p-4">
    <h2 id="section-detail" className="text-base font-semibold">
      详情说明
    </h2>
    <FormField
      control={form.control}
      name="description_md"
      render={({ field }) => (
        <FormItem>
          <MarkdownEditor
            id="tool-field-description_md"
            value={field.value}
            disabled={locked}
            onChange={field.onChange}
            hint="渲染配置与详情页完全一致，所见即所得。"
          />
          <FormMessage />
        </FormItem>
      )}
    />
  </section>

  
  <section aria-labelledby="section-images" className="space-y-4 rounded-xl border bg-card p-4">
    <h2 id="section-images" className="text-base font-semibold">
      截图
    </h2>
    <ImageUploader
      images={props.images}
      onImagesChange={props.onImagesChange}
      pending={props.pendingImages}
      onPendingChange={props.onPendingImagesChange}
      toolId={props.toolId}
      disabled={locked}
    />
  </section>

  
  <section aria-labelledby="section-visibility" className="space-y-4 rounded-xl border bg-card p-4">
    <h2 id="section-visibility" className="text-base font-semibold">
      可见性
    </h2>
    <FormField
      control={form.control}
      name="visibility"
      render={({ field }) => (
        <FormItem>
          <RadioGroup
            value={field.value}
            disabled={locked}
            aria-label="可见性"
            onValueChange={(value) => {
              field.onChange(value);
              onAutosave();
            }}
            className="gap-2"
          >
            {(
              [
                { value: "public", label: "公开", hint: "所有登录用户可见可下载" },
                { value: "restricted", label: "指定可见", hint: "仅指定用户或用户组可见" },
                { value: "private", label: "私有", hint: "仅自己可见" },
              ] as const
            ).map((option) => (
              <Label
                key={option.value}
                htmlFor={`tool-field-visibility-${option.value}`}
                className={cn(
                  "flex cursor-pointer items-start gap-2 rounded-lg border p-3 text-sm font-normal",
                  field.value === option.value && "border-primary bg-primary/5",
                )}
              >
                <RadioGroupItem
                  id={`tool-field-visibility-${option.value}`}
                  value={option.value}
                  className="mt-0.5"
                />
                <span>
                  <span className="font-medium">{option.label}</span>
                  <span className="ml-2 text-xs text-muted-foreground">{option.hint}</span>
                </span>
              </Label>
            ))}
          </RadioGroup>
          <FormMessage />
        </FormItem>
      )}
    />

    {props.visibility === "restricted" ? (
      <AclEditor
        entries={props.aclEntries}
        disabled={locked}
        onChange={props.onAclChange}
      />
    ) : null}
  </section>
    </>
  );
}
