import { Check, X } from "lucide-react";
import * as React from "react";

import type { Category, Tag, ToolType } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { TOOL_TYPE_META, TOOL_TYPES } from "@/lib/toolMeta";
import { cn } from "@/lib/utils";

const VISIBLE_TAG_COUNT = 20;

export interface ToolFilterSelection {
  category: string | null;
  types: ToolType[];
  tags: string[];
  /** Current `?q=` value — shown as a removable chip like any other filter. */
  query: string;
}

export interface ToolFiltersProps {
  categories: Category[] | undefined;
  /** slug → visible tool count, from `facets` (only page 1 returns it). */
  categoryCounts: Record<string, number> | undefined;
  typeCounts: Record<string, number> | undefined;
  tags: Tag[] | undefined;
  selection: ToolFilterSelection;
  onSelectCategory: (slug: string | null) => void;
  onToggleType: (type: ToolType) => void;
  onToggleTag: (tag: string) => void;
  onClearQuery: () => void;
  onClearAll: () => void;
  /** Total number of visible tools, used for the "全部" counter. */
  total: number | undefined;
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="px-2 pb-1 pt-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
      {children}
    </h3>
  );
}

/**
 * Left-hand filter rail for the portal (docs/04 §6.3): category single-select
 * with counts, type multi-select, hot tags, and the applied-filter badge set.
 * Below `lg` the same component is rendered inside a Sheet.
 */
export function ToolFilters({
  categories,
  categoryCounts,
  typeCounts,
  tags,
  selection,
  onSelectCategory,
  onToggleType,
  onToggleTag,
  onClearQuery,
  onClearAll,
  total,
}: ToolFiltersProps) {
  const [showAllTags, setShowAllTags] = React.useState(false);

  const categoryList = categories ?? [];
  const visibleTags = showAllTags ? (tags ?? []) : (tags ?? []).slice(0, VISIBLE_TAG_COUNT);
  const hasSelection =
    selection.category !== null ||
    selection.types.length > 0 ||
    selection.tags.length > 0 ||
    selection.query.trim() !== "";

  const selectedCategoryName = categoryList.find(
    (category) => category.slug === selection.category,
  )?.name;

  return (
    <div className="space-y-3">
      {hasSelection ? (
        <div className="rounded-lg border bg-card p-2">
          <div className="flex items-center justify-between gap-2 px-1 pb-1">
            <span className="text-xs font-semibold text-muted-foreground">已选条件</span>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-6 px-1.5 text-xs"
              onClick={onClearAll}
            >
              清除全部
            </Button>
          </div>
          <ul className="flex flex-wrap gap-1.5 px-1 pt-1">
            {selection.category ? (
              <li>
                <FilterChip
                  label={`分类：${selectedCategoryName ?? selection.category}`}
                  onRemove={() => onSelectCategory(null)}
                />
              </li>
            ) : null}
            {selection.types.map((type) => (
              <li key={type}>
                <FilterChip
                  label={`类型：${TOOL_TYPE_META[type].label}`}
                  onRemove={() => onToggleType(type)}
                />
              </li>
            ))}
            {selection.tags.map((tag) => (
              <li key={tag}>
                <FilterChip label={`#${tag}`} onRemove={() => onToggleTag(tag)} />
              </li>
            ))}
            {selection.query.trim() ? (
              <li>
                <FilterChip
                  label={`搜索：${selection.query.trim()}`}
                  onRemove={onClearQuery}
                />
              </li>
            ) : null}
          </ul>
        </div>
      ) : null}

      <nav aria-label="按分类筛选">
        <SectionTitle>分类</SectionTitle>
        <ul className="space-y-0.5">
          <li>
            <CategoryButton
              active={selection.category === null}
              name="全部"
              count={total}
              onClick={() => onSelectCategory(null)}
            />
          </li>
          {categoryList.map((category) => (
            <li key={category.slug}>
              <CategoryButton
                active={selection.category === category.slug}
                name={category.name}
                count={
                  categoryCounts ? categoryCounts[category.slug] : category.tool_count
                }
                onClick={() => onSelectCategory(category.slug)}
              />
            </li>
          ))}
        </ul>
      </nav>

      <div className="border-t pt-1">
        <SectionTitle>类型</SectionTitle>
        <ul className="space-y-1 px-2 pt-1">
          {TOOL_TYPES.map((type) => {
            const id = `filter-type-${type}`;
            const meta = TOOL_TYPE_META[type];
            const Icon = meta.icon;
            const checked = selection.types.includes(type);
            const count = typeCounts?.[type];
            return (
              <li key={type} className="flex items-center gap-2">
                <Checkbox
                  id={id}
                  checked={checked}
                  onCheckedChange={() => onToggleType(type)}
                />
                <Label
                  htmlFor={id}
                  className="flex flex-1 cursor-pointer items-center gap-1.5 text-sm font-normal"
                >
                  <Icon aria-hidden="true" className="size-3.5 text-muted-foreground" />
                  {meta.label}
                  {count !== undefined ? (
                    <span className="ml-auto text-xs tabular-nums text-muted-foreground">
                      {count}
                    </span>
                  ) : null}
                </Label>
              </li>
            );
          })}
        </ul>
      </div>

      {visibleTags.length > 0 ? (
        <div className="border-t pt-1">
          <SectionTitle>标签</SectionTitle>
          <ul className="flex flex-wrap gap-1.5 px-2 pt-1">
            {visibleTags.map((tag) => {
              const active = selection.tags.includes(tag.name);
              return (
                <li key={tag.id}>
                  <Button
                    type="button"
                    variant={active ? "default" : "outline"}
                    size="sm"
                    className="h-6 gap-1 px-2 font-mono text-xs"
                    aria-pressed={active}
                    onClick={() => onToggleTag(tag.name)}
                  >
                    #{tag.display_name || tag.name}
                    <span className="tabular-nums opacity-70">{tag.usage_count}</span>
                  </Button>
                </li>
              );
            })}
          </ul>
          {(tags?.length ?? 0) > VISIBLE_TAG_COUNT ? (
            <div className="px-2 pt-2">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-6 px-1 text-xs"
                aria-expanded={showAllTags}
                onClick={() => setShowAllTags((previous) => !previous)}
              >
                {showAllTags ? "收起标签" : `更多标签（${(tags?.length ?? 0) - VISIBLE_TAG_COUNT}）`}
              </Button>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function FilterChip({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-secondary px-2 py-0.5 text-xs text-secondary-foreground">
      {label}
      <button
        type="button"
        className="grid size-4 place-items-center rounded-full hover:bg-background/60"
        aria-label={`移除筛选条件 ${label}`}
        onClick={onRemove}
      >
        <X aria-hidden="true" className="size-3" />
      </button>
    </span>
  );
}

function CategoryButton({
  active,
  name,
  count,
  onClick,
}: {
  active: boolean;
  name: string;
  count: number | undefined;
  onClick: () => void;
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "h-8 w-full justify-start gap-2 px-2 font-normal",
        active && "bg-accent font-medium text-accent-foreground",
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          "grid size-3.5 place-items-center rounded-full border",
          active ? "border-primary bg-primary text-primary-foreground" : "border-input",
        )}
      >
        {active ? <Check className="size-2.5" /> : null}
      </span>
      <span className="truncate">{name}</span>
      {count !== undefined ? (
        <Badge variant={active ? "default" : "secondary"} className="ml-auto tabular-nums">
          {count}
        </Badge>
      ) : null}
    </Button>
  );
}
