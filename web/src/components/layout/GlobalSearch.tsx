import { useQuery } from "@tanstack/react-query";
import { CornerDownLeft, History, Loader2, Search } from "lucide-react";
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
import { readRecentTools, recordRecentTool } from "@/lib/recentTools";

/**
 * Global search in the top bar: a Command palette opened with ⌘K / Ctrl+K
 * (docs/04 §5.1, §6.3).
 *
 * Selecting a result opens `/tools/:slug` (CONTRACT §14.10 — M2 landed the detail
 * page). The "在门户中搜索" row still jumps to the portal with `?q=` for free-text
 * searches that match nothing by name.
 *
 * M7 · F6：加了分组标题与「最近访问」。最近访问存在 localStorage
 * （`localcraft:recent-tools`，最多 8 条，只存 slug/name/at，见
 * `@/lib/recentTools`），读取失败静默降级为空列表。
 *
 * M7 · F1：触发器在窄屏（< sm）退化成纯图标按钮 —— 它现在位于右侧集群，
 * `w-full` 会把 logo 与右侧图标挤爆。图标态没有文字也没有 ⌘K 提示，
 * 但 `aria-label` 保留，可访问名不丢。
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

  /*
   * 「最近访问」在每次面板打开时重新读一遍（`open` 变化时 useMemo 重算）。
   * 用 useMemo 而不是 effect + setState：读 localStorage 是同步的派生值，
   * 放进 effect 会多一次级联渲染（oxlint `react(set-state-in-effect)`）。
   * `readRecentTools` 内部已吞掉所有异常，localStorage 不可用时就是 []。
   */
  const recent = React.useMemo(() => (open ? readRecentTools() : []), [open]);

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

  /** M2：详情页已落地，选中结果直接跳 /tools/:slug（CONTRACT §14.10）。 */
  function goToTool(slug: string, name: string) {
    setOpen(false);
    // 只记 slug/name/at，绝不写任何凭据或个人信息（任务书 F6）。
    // 面板随之关闭，无需回写 state；下次打开时 useMemo 会重新读取。
    recordRecentTool({ slug, name });
    navigate(`/tools/${encodeURIComponent(slug)}`);
  }

  const items = data?.items ?? [];

  return (
    <>
      <Button
        type="button"
        variant="outline"
        size="icon"
        onClick={() => setOpen(true)}
        data-testid="global-search-trigger"
        /*
          纯图标按钮（**全部宽度**，不再在 sm 以上展开成带文字的搜索框）。
          
          为什么改（用户直接提出）：门户页本身已有一个真正的搜索框
          （`PortalPage` 的 `?q=` 筛选输入框），而顶栏这个是**命令面板触发器**
          —— 两者共用同一个 API，且面板里那条「在门户中搜索」就是把关键词
          交给门户搜索框。并排放在一屏里会看起来像两个重复的搜索框，
          所以顶栏这个只保留图标，视觉上只留一个搜索框。
          
          ⌘K、最近访问、快速跳转**功能全部不变**，只是入口变紧凑。
          `aria-label` 保留可访问名（e2e 与读屏都依赖它）；`title` 给鼠标用户
          一个悬停提示 —— 否则 ⌘K 会变得无从发现。
        */
        className="text-muted-foreground"
        aria-label="搜索工具（快捷键 Command K）"
        title="搜索（⌘K）"
      >
        <Search aria-hidden="true" className="size-4" />
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
          {/* ---- 最近访问（仅在未输入关键词时出现）---- */}
          {keyword.length === 0 && recent.length > 0 ? (
            <CommandGroup
              heading="最近访问"
              data-testid="recent-tools-group"
              /* 入场动效由 index.css 的 prefers-reduced-motion 全局规则自动关闭 */
              className="animate-in fade-in-0 duration-150"
            >
              {recent.map((item) => (
                <CommandItem
                  key={item.slug}
                  value={`__recent__${item.slug}`}
                  onSelect={() => goToTool(item.slug, item.name)}
                >
                  <History aria-hidden="true" className="size-4" />
                  <span className="min-w-0 flex-1 truncate">{item.name}</span>
                  <span className="max-w-[10rem] truncate font-mono text-xs text-muted-foreground">
                    {item.slug}
                  </span>
                </CommandItem>
              ))}
            </CommandGroup>
          ) : null}

          {keyword.length === 0 && recent.length === 0 ? (
            <div className="px-3 py-6 text-center text-sm text-muted-foreground">
              输入关键词后回车，在门户中查看结果
            </div>
          ) : null}

          {keyword.length > 0 ? (
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
          ) : null}

          {items.length > 0 ? (
            <CommandGroup heading="工具">
              {items.map((tool) => (
                <CommandItem
                  key={tool.id}
                  value={tool.name}
                  onSelect={() => goToTool(tool.slug, tool.name)}
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

        {/*
          V8 的「快捷键提示」：面板底部固定一行，不参与 cmdk 的键盘选择。
          纯装饰，用 aria-hidden 避免读屏重复播报。
        */}
        <div
          aria-hidden="true"
          className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t px-3 py-2 text-[11px] text-muted-foreground"
        >
          <span className="inline-flex items-center gap-1">
            <kbd className="rounded border bg-muted px-1 font-mono">↑</kbd>
            <kbd className="rounded border bg-muted px-1 font-mono">↓</kbd>
            选择
          </span>
          <span className="inline-flex items-center gap-1">
            <kbd className="rounded border bg-muted px-1 font-mono">↵</kbd>
            打开
          </span>
          <span className="inline-flex items-center gap-1">
            <kbd className="rounded border bg-muted px-1 font-mono">esc</kbd>
            关闭
          </span>
        </div>
      </CommandDialog>
    </>
  );
}
