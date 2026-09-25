import { useQuery } from "@tanstack/react-query";
import { Plus, X } from "lucide-react";
import * as React from "react";

import { fetchTags, tagsQueryKey } from "@/api/tools";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

/** Backend limits: `ToolCreateRequest.tags` max_length=8, each ≤ 64 (docs/01 §5.5). */
const MAX_TAGS = 8;
const MAX_TAG_LENGTH = 64;

export interface TagInputProps {
  value: string[];
  onChange: (tags: string[]) => void;
  disabled?: boolean;
  /** `id` of the visible text input, so `FormLabel htmlFor` can point at it. */
  inputId?: string;
  "aria-describedby"?: string;
}

/**
 * 标签输入（docs/04 §6.7「标签 [#python ×] [+ 添加标签]」，docs/01 FR-TOOL-01）。
 *
 * Suggestions come from `GET /tags?q=` (prefix search, docs/03 §2.3) and are
 * debounced — the doc's 300ms rule (docs/04 §9) exists to protect the SQLite
 * write path and the intranet link alike. Tags the server already knows use
 * their `display_name`; new ones are created server-side on save.
 */
export function TagInput({
  value,
  onChange,
  disabled = false,
  inputId,
  "aria-describedby": ariaDescribedBy,
}: TagInputProps) {
  const [draft, setDraft] = React.useState("");
  const [debounced, setDebounced] = React.useState("");
  const [open, setOpen] = React.useState(false);

  React.useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(draft.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [draft]);

  const suggestionsQuery = useQuery({
    queryKey: tagsQueryKey(debounced),
    queryFn: ({ signal }) => fetchTags(debounced || undefined, signal),
    staleTime: 5 * 60_000,
    enabled: open || debounced.length > 0,
  });

  const full = value.length >= MAX_TAGS;
  const lowered = value.map((tag) => tag.toLowerCase());
  const suggestions = (suggestionsQuery.data ?? [])
    .filter((tag) => !lowered.includes(tag.name.toLowerCase()))
    .slice(0, 8);

  function add(raw: string) {
    const tag = raw.trim();
    if (!tag || full) return;
    if (tag.length > MAX_TAG_LENGTH) return;
    if (lowered.includes(tag.toLowerCase())) {
      setDraft("");
      return;
    }
    onChange([...value, tag]);
    setDraft("");
    setOpen(false);
  }

  function remove(tag: string) {
    onChange(value.filter((item) => item !== tag));
  }

  return (
    <div className="space-y-2" data-testid="tag-input">
      {value.length > 0 ? (
        <ul className="flex flex-wrap gap-1.5" aria-label="已选标签">
          {value.map((tag) => (
            <li key={tag}>
              <span className="inline-flex items-center gap-1 rounded-md bg-muted px-1.5 py-0.5 font-mono text-xs">
                #{tag}
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  aria-label={`移除标签 ${tag}`}
                  disabled={disabled}
                  onClick={() => remove(tag)}
                >
                  <X aria-hidden="true" className="size-3" />
                </Button>
              </span>
            </li>
          ))}
        </ul>
      ) : null}

      <div className="relative">
        <div className="flex items-center gap-2">
          <Input
            id={inputId}
            aria-describedby={ariaDescribedBy}
            value={draft}
            disabled={disabled || full}
            placeholder={full ? `最多 ${MAX_TAGS} 个标签` : "输入标签后按回车"}
            onChange={(event) => {
              setDraft(event.target.value);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onBlur={() => {
              // Delayed so a click on a suggestion still lands.
              window.setTimeout(() => setOpen(false), 120);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === ",") {
                event.preventDefault();
                add(draft);
              } else if (event.key === "Backspace" && draft === "" && value.length > 0) {
                const last = value[value.length - 1];
                if (last !== undefined) remove(last);
              } else if (event.key === "Escape") {
                setOpen(false);
              }
            }}
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={disabled || full || draft.trim() === ""}
            onClick={() => add(draft)}
          >
            <Plus aria-hidden="true" className="size-4" />
            添加
          </Button>
        </div>

        {open && suggestions.length > 0 ? (
          <ul
            className="absolute z-20 mt-1 max-h-48 w-full overflow-y-auto rounded-md border bg-popover p-1 shadow-md"
            aria-label="标签建议"
          >
            {suggestions.map((tag) => (
              <li key={tag.id}>
                <button
                  type="button"
                  className={cn(
                    "flex w-full items-center justify-between gap-2 rounded-sm px-2 py-1.5 text-left text-sm",
                    "hover:bg-accent focus-visible:bg-accent focus-visible:outline-none",
                  )}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => add(tag.display_name)}
                >
                  <span className="font-mono text-xs">#{tag.display_name}</span>
                  <span className="text-xs text-muted-foreground">{tag.usage_count} 次使用</span>
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      <p className="text-xs text-muted-foreground">
        最多 {MAX_TAGS} 个标签，每个不超过 {MAX_TAG_LENGTH} 字符（已选 {value.length} 个）
      </p>
    </div>
  );
}
