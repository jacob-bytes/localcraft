import { useQuery } from "@tanstack/react-query";
import { CornerDownLeft, Loader2, Search } from "lucide-react";
import * as React from "react";
import { useNavigate } from "react-router-dom";

import { fetchTools, toolsQueryKey } from "@/api/tools";
import { Button } from "@/components/ui/button";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { ToolTypeBadge } from "@/components/tools/ToolTypeBadge";
import { useDebounce } from "@/hooks/useDebounce";

/**
 * Global search in the top bar: a Command palette opened with ⌘K / Ctrl+K
 * (docs/04 §5.1, §6.3).
 *
 * M1 note: selecting a result lands on the portal filtered to that tool, because
 * `/tools/:slug` is an M2 route (CONTRACT §12). Swap the navigation target for
 * `/tools/${slug}` once M2 lands.
 */
export function GlobalSearch() {
  const [open, setOpen] = React.useState(false);
  const [term, setTerm] = React.useState("");
  const debounced = useDebounce(term, 300);
  const navigate = useNavigate();

  React.useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((previous) => !previous);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const keyword = debounced.trim();
  const { data, isFetching } = useQuery({
    queryKey: toolsQueryKey({ q: keyword, page_size: 8 }),
    queryFn: ({ signal }) => fetchTools({ q: keyword, page_size: 8 }, signal),
    enabled: open && keyword.length > 0,
  });

  function goToPortal(value: string) {
    const q = value.trim();
    if (!q) return;
    setOpen(false);
    navigate(`/?q=${encodeURIComponent(q)}`);
  }

  const items = data?.items ?? [];

  return (
    <>
      <Button
        type="button"
        variant="outline"
        onClick={() => setOpen(true)}
        className="h-9 w-full justify-start gap-2 px-3 text-muted-foreground sm:w-64 lg:w-80"
        aria-label="搜索工具（快捷键 Command K）"
      >
        <Search aria-hidden="true" className="size-4" />
        <span className="truncate">搜索工具、Skill、提示词…</span>
        <kbd className="ml-auto hidden shrink-0 rounded border bg-muted px-1.5 font-mono text-[10px] text-muted-foreground sm:inline-block">
          ⌘K
        </kbd>
      </Button>

      <CommandDialog
        open={open}
        onOpenChange={setOpen}
        title="搜索工具"
        description="输入关键词搜索平台上的工具与 Skill"
        className="sm:max-w-xl"
      >
        <CommandInput
          value={term}
          onValueChange={setTerm}
          placeholder="搜索工具名称、简介、标签…"
          aria-label="搜索关键词"
        />
        <CommandList>
          {keyword.length === 0 ? (
            <div className="px-3 py-6 text-center text-sm text-muted-foreground">
              输入关键词后回车，在门户中查看结果
            </div>
          ) : (
            <CommandEmpty>
              {isFetching ? (
                <span className="inline-flex items-center gap-2">
                  <Loader2 aria-hidden="true" className="size-3.5 animate-spin" />
                  搜索中…
                </span>
              ) : (
                "没有匹配的工具"
              )}
            </CommandEmpty>
          )}

          {items.length > 0 ? (
            <CommandGroup heading="工具">
              {items.map((tool) => (
                <CommandItem
                  key={tool.id}
                  value={tool.name}
                  onSelect={() => goToPortal(tool.name)}
                >
                  <ToolTypeBadge type={tool.tool_type} />
                  <span className="min-w-0 flex-1 truncate">{tool.name}</span>
                  <span className="max-w-[10rem] truncate text-xs text-muted-foreground">
                    {tool.category?.name}
                  </span>
                </CommandItem>
              ))}
            </CommandGroup>
          ) : null}

          {keyword.length > 0 ? (
            <CommandGroup heading="门户">
              <CommandItem value={`__portal__${keyword}`} onSelect={() => goToPortal(keyword)}>
                <CornerDownLeft aria-hidden="true" className="size-4" />
                <span className="flex-1 truncate">
                  在门户中搜索「<span className="font-medium">{keyword}</span>」
                </span>
              </CommandItem>
            </CommandGroup>
          ) : null}
        </CommandList>
      </CommandDialog>
    </>
  );
}
