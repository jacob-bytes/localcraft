import {
  Book,
  Bot,
  Box,
  Brain,
  Briefcase,
  Bug,
  ChevronsUpDown,
  CircleHelp,
  Cloud,
  Code,
  Cpu,
  Database,
  FileCode,
  FolderTree,
  GitBranch,
  Globe,
  Hammer,
  KeyRound,
  Layers,
  LifeBuoy,
  LineChart,
  Link,
  Lock,
  MessageSquare,
  Network,
  Notebook,
  Package,
  Palette,
  Plug,
  Puzzle,
  Rocket,
  Search,
  Server,
  Settings,
  Shield,
  Sparkles,
  Terminal,
  Timer,
  Users,
  Workflow,
  Wrench,
  Zap,
  type LucideIcon,
} from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

/**
 * 分类图标选择器（docs/04 §6.15「图标选择：`Popover` + lucide 图标搜索网格」）。
 *
 * **后端只认 lucide 图标名**：`category.icon` 存的是 `wrench` / `server` /
 * `file-code` 这类 lucide 图标的 kebab-case 名（docs/02 §3.6、FR-TAX-01 的
 * 「图标标识」），门户再用同一个名字去解析图标。所以这里**不做动态渲染**，
 * 只暴露一份固定白名单：
 *
 *  - 保证门户一定解析得出管理员选的名字（写脏值等于让门户显示破图）；
 *  - 避免把整个 lucide 图标库打进管理台 chunk（docs/04 §9 首屏体积预算）。
 *
 * 唯一一处白名单外的偏差：老数据里可能存着不在名单内的名字，这时选择器仍
 * 会显示原始名字并用问号图标兜底，**不会**在编辑时悄悄把值改掉。
 */

export interface CategoryIconOption {
  /** 存进 `category.icon` 的 lucide 图标名（kebab-case）。 */
  readonly name: string;
  readonly Icon: LucideIcon;
}

/**
 * 分类可选图标白名单（与工具/分类语义相关的 40 个）。
 *
 * lucide-react 0.469 没有 `tool-case`，用 `briefcase` 代替。
 */
export const CATEGORY_ICON_WHITELIST: readonly CategoryIconOption[] = [
  { name: "wrench", Icon: Wrench },
  { name: "server", Icon: Server },
  { name: "puzzle", Icon: Puzzle },
  { name: "message-square", Icon: MessageSquare },
  { name: "database", Icon: Database },
  { name: "shield", Icon: Shield },
  { name: "terminal", Icon: Terminal },
  { name: "box", Icon: Box },
  { name: "package", Icon: Package },
  { name: "code", Icon: Code },
  { name: "bug", Icon: Bug },
  { name: "rocket", Icon: Rocket },
  { name: "book", Icon: Book },
  { name: "bot", Icon: Bot },
  { name: "brain", Icon: Brain },
  { name: "cloud", Icon: Cloud },
  { name: "cpu", Icon: Cpu },
  { name: "file-code", Icon: FileCode },
  { name: "folder-tree", Icon: FolderTree },
  { name: "git-branch", Icon: GitBranch },
  { name: "globe", Icon: Globe },
  { name: "hammer", Icon: Hammer },
  { name: "key-round", Icon: KeyRound },
  { name: "layers", Icon: Layers },
  { name: "life-buoy", Icon: LifeBuoy },
  { name: "line-chart", Icon: LineChart },
  { name: "link", Icon: Link },
  { name: "lock", Icon: Lock },
  { name: "network", Icon: Network },
  { name: "notebook", Icon: Notebook },
  { name: "palette", Icon: Palette },
  { name: "plug", Icon: Plug },
  { name: "search", Icon: Search },
  { name: "settings", Icon: Settings },
  { name: "sparkles", Icon: Sparkles },
  { name: "timer", Icon: Timer },
  { name: "briefcase", Icon: Briefcase },
  { name: "users", Icon: Users },
  { name: "workflow", Icon: Workflow },
  { name: "zap", Icon: Zap },
];

/**
 * 图标名 → 组件的**模块级常量表**（组件里只做一次对象成员访问）。
 *
 * 刻意不在组件里用函数调用（`getIcon(name)`）取值：React Compiler 的
 * `react/static-components` 规则无法证明「函数返回值是模块里已有的组件」，会把
 * `<Icon />` 判成「render 中新建的组件」并告警（`npm run lint` 是
 * `--deny-warnings`，告警即失败）。对象成员访问与 `TOOL_TYPE_META[type].icon`
 * 一样是可静态判定的；查不到时得到 `undefined`，调用方用 `—` 或问号图标兜底。
 */
export const CATEGORY_ICONS: Readonly<Record<string, LucideIcon>> = Object.fromEntries(
  CATEGORY_ICON_WHITELIST.map((option) => [option.name, option.Icon]),
);

/**
 * 触发器是原生 `<button>`：继承 button 的 DOM 属性，好让 `ui/form.tsx` 的
 * `FormControl` 能把 `id` / `aria-describedby` / `aria-invalid` 透传到按钮上
 * （docs/04 §8.1：错误与说明必须与控件关联）。
 */
export type IconPickerProps = Omit<
  React.ComponentPropsWithoutRef<"button">,
  "value" | "onChange" | "type" | "role" | "aria-expanded"
> & {
  /** 当前选中的 lucide 图标名；`null` 表示未选择。 */
  value: string | null;
  onChange: (value: string | null) => void;
};

export function IconPicker({ value, onChange, disabled = false, ...triggerProps }: IconPickerProps) {
  const [open, setOpen] = React.useState(false);
  const [query, setQuery] = React.useState("");

  const selectedName = value && value.length > 0 ? value : null;
  const selected = selectedName ? (CATEGORY_ICONS[selectedName] ?? null) : null;

  const filtered = React.useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return CATEGORY_ICON_WHITELIST;
    return CATEGORY_ICON_WHITELIST.filter((option) => option.name.includes(keyword));
  }, [query]);

  const SelectedIcon = selected ?? CircleHelp;

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setQuery("");
      }}
    >
      <PopoverTrigger asChild>
        <Button
          {...triggerProps}
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          aria-label={
            selectedName ? `选择分类图标，当前为 ${selectedName}` : "选择分类图标，当前未选择"
          }
          disabled={disabled}
          data-testid="icon-picker"
          className={cn("w-full justify-between font-normal", triggerProps.className)}
        >
          <span className="flex min-w-0 items-center gap-2">
            <SelectedIcon
              aria-hidden="true"
              className={cn(
                "size-4 shrink-0",
                selectedName ? "text-foreground" : "text-muted-foreground",
              )}
            />
            <span className={cn("truncate", selectedName ? undefined : "text-muted-foreground")}>
              {selectedName ?? "未选择图标"}
            </span>
          </span>
          <ChevronsUpDown aria-hidden="true" className="size-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>

      <PopoverContent align="start" className="w-80 p-2">
        <div className="relative">
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute top-1/2 left-2 size-4 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索图标名，如 server"
            aria-label="搜索图标"
            data-testid="icon-picker-search"
            className="pl-8"
          />
        </div>

        {filtered.length === 0 ? (
          <p className="px-2 py-6 text-center text-xs text-muted-foreground">没有匹配的图标</p>
        ) : (
          <div
            role="group"
            aria-label="可选图标"
            className="mt-2 grid max-h-52 grid-cols-8 gap-1 overflow-y-auto p-1"
          >
            {filtered.map((option) => {
              const Icon = option.Icon;
              const active = option.name === selectedName;
              return (
                <button
                  key={option.name}
                  type="button"
                  aria-label={`选择图标 ${option.name}`}
                  aria-pressed={active}
                  title={option.name}
                  onClick={() => {
                    onChange(option.name);
                    setOpen(false);
                  }}
                  className={cn(
                    "grid size-8 place-items-center rounded-md border border-transparent text-muted-foreground transition-colors",
                    "hover:bg-accent hover:text-accent-foreground",
                    "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:outline-none",
                    active && "border-ring bg-accent text-accent-foreground",
                  )}
                >
                  <Icon aria-hidden="true" className="size-4" />
                </button>
              );
            })}
          </div>
        )}

        {selectedName ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="mt-1 w-full"
            onClick={() => {
              onChange(null);
              setOpen(false);
            }}
          >
            清除图标
          </Button>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}

export default IconPicker;
