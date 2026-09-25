import { Eye, FileCode2, Globe, Sparkles, Wrench } from "lucide-react";
import * as React from "react";

import type { ToolType } from "@/api/types";
import { Markdown } from "@/components/common/Markdown";
import { Textarea } from "@/components/me/Textarea";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { RadioGroup, RadioGroupItem } from "@/components/me/RadioGroup";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/me/Tabs";

/** 四种类型的中文说明（docs/01 FR-TOOL-02, docs/04 §6.7 类型专属区）。 */
const TYPE_OPTIONS: ReadonlyArray<{
  value: ToolType;
  label: string;
  hint: string;
  icon: typeof Wrench;
}> = [
  { value: "file", label: "文件包", hint: "上传 zip / tar.gz / whl 等交付物", icon: Wrench },
  { value: "webapp", label: "在线工具", hint: "填写内网访问地址", icon: Globe },
  { value: "skill", label: "Skill", hint: "上传含 SKILL.md 的 zip 包", icon: Sparkles },
  { value: "prompt", label: "提示词", hint: "直接填写提示词正文", icon: FileCode2 },
];

/**
 * 工具类型单选。
 *
 * `Edit` mode is locked: the type decides the shape of every version's content,
 * so changing it after versions exist would orphan them (docs/04 §6.7
 * 「编辑模式下不允许修改类型」).
 */
export function ToolTypeRadioGroup({
  value,
  onChange,
  readOnly = false,
}: {
  value: ToolType;
  onChange: (value: ToolType) => void;
  readOnly?: boolean;
}) {
  return (
    <div className="space-y-2">
      <RadioGroup
        value={value}
        disabled={readOnly}
        onValueChange={(next) => onChange(next as ToolType)}
        className="grid gap-2 sm:grid-cols-2"
        aria-label="工具类型"
      >
        {TYPE_OPTIONS.map((option) => {
          const Icon = option.icon;
          const id = `tool-type-${option.value}`;
          return (
            <Label
              key={option.value}
              htmlFor={id}
              className={cn(
                "flex cursor-pointer items-start gap-2 rounded-lg border p-3 text-sm font-normal",
                value === option.value && "border-primary bg-primary/5",
                readOnly && "cursor-not-allowed opacity-70",
              )}
            >
              <RadioGroupItem id={id} value={option.value} className="mt-0.5" />
              <span className="space-y-0.5">
                <span className="flex items-center gap-1.5 font-medium">
                  <Icon aria-hidden="true" className="size-4" />
                  {option.label}
                </span>
                <span className="block text-xs text-muted-foreground">{option.hint}</span>
              </span>
            </Label>
          );
        })}
      </RadioGroup>
      {readOnly ? (
        <p className="text-xs text-muted-foreground">
          编辑模式下不可修改类型（会导致已有版本内容不匹配），如需更换类型请新建工具。
        </p>
      ) : null}
    </div>
  );
}

/** 详情说明的 Markdown 编辑 / 预览（预览复用详情页的 `Markdown`，保证一致）。 */
export function MarkdownEditor({
  value,
  onChange,
  id,
  ariaDescribedBy,
  disabled = false,
  rows = 10,
  label = "详情说明",
  placeholder,
  hint,
}: {
  value: string;
  onChange: (value: string) => void;
  id: string;
  ariaDescribedBy?: string;
  disabled?: boolean;
  rows?: number;
  label?: string;
  placeholder?: string;
  hint?: string;
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      <Tabs defaultValue="edit">
        <TabsList aria-label={`${label}编辑方式`}>
          <TabsTrigger value="edit">编辑</TabsTrigger>
          <TabsTrigger value="preview">
            <Eye aria-hidden="true" className="size-3.5" />
            预览
          </TabsTrigger>
        </TabsList>
        <TabsContent value="edit">
          <Textarea
            id={id}
            aria-describedby={ariaDescribedBy}
            rows={rows}
            value={value}
            disabled={disabled}
            placeholder={placeholder ?? "支持 Markdown，例如：## 用途"}
            className="font-mono text-sm"
            onChange={(event) => onChange(event.target.value)}
          />
        </TabsContent>
        <TabsContent value="preview">
          <div className="min-h-20 rounded-md border bg-card px-3 py-2">
            {value.trim() ? (
              <Markdown source={value} />
            ) : (
              <p className="text-sm text-muted-foreground">暂无内容可预览</p>
            )}
          </div>
        </TabsContent>
      </Tabs>
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

/** Small shared wrapper so every editor action row looks the same. */
export function EditorActions({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return <div className={cn("flex flex-wrap items-center gap-2", className)}>{children}</div>;
}
