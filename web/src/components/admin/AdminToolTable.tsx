import {
  ExternalLink,
  History as HistoryIcon,
  MoreHorizontal,
  RotateCcw,
  ShieldOff,
  UserRoundCog,
} from "lucide-react";
import * as React from "react";
import { Link } from "react-router-dom";

import type { AdminToolItem, ToolStatus, Visibility } from "@/api/types";
import { ToolTypeBadge } from "@/components/tools/ToolTypeBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatCount, formatDateTime, formatRelativeTime } from "@/lib/format";
import { VISIBILITY_LABELS } from "@/lib/toolMeta";

/**
 * 全站工具管理的数据表（docs/04 §6.12）。
 *
 * 列：工具（封面缩略图 + 名称 + slug）/ 类型 / 状态 / 可见性 / 作者 /
 * 当前版本 / 下载量 / 更新时间 / 操作（`DropdownMenu`）。
 *
 * **不提供「移入回收站」**：冻结的 92 个接口里没有管理侧软删除端点
 * （`DELETE /me/tools/{id}` 只对 owner 生效，CONTRACT §16.5），
 * 界面不制造做不到的操作（docs/04 §6.20 的回收站页只能还原/彻底清除）。
 */

type BadgeVariant = "secondary" | "warning" | "success" | "destructive" | "outline";

/** 状态中文名（docs/04 §6.9 的映射，徽标、状态列、筛选共用一份）。 */
export const TOOL_STATUS_LABELS: Record<ToolStatus, string> = {
  draft: "草稿",
  pending: "待审",
  approved: "已发布",
  rejected: "已驳回",
  pending_update: "有新版待审",
  offline: "已下架",
};

/**
 * 徽标配色：draft=secondary、pending=warning、approved=success、
 * rejected=destructive、offline=outline（任务书指定）。
 * `pending_update` 语义上仍是「待处理」，沿用 warning。
 * 每个徽标都带文字，不靠颜色区分（docs/04 §8.1）。
 */
export const TOOL_STATUS_VARIANTS: Record<ToolStatus, BadgeVariant> = {
  draft: "secondary",
  pending: "warning",
  approved: "success",
  rejected: "destructive",
  pending_update: "warning",
  offline: "outline",
};

/** 概览卡与筛选栏的固定展示顺序。 */
export const TOOL_STATUS_ORDER: readonly ToolStatus[] = [
  "draft",
  "pending",
  "approved",
  "pending_update",
  "rejected",
  "offline",
];

export const TOOL_STATUS_OPTIONS: ReadonlyArray<{ value: ToolStatus; label: string }> =
  TOOL_STATUS_ORDER.map((value) => ({ value, label: TOOL_STATUS_LABELS[value] }));

/**
 * `tools_by_status` 的 `status` 在类型上是 `string`，未知值原样展示而不是崩溃
 * （服务端未来加状态时前端仍可读）。
 */
export function toolStatusLabel(status: string): string {
  return TOOL_STATUS_LABELS[status as ToolStatus] ?? status;
}

export function toolStatusVariant(status: string): BadgeVariant {
  return TOOL_STATUS_VARIANTS[status as ToolStatus] ?? "secondary";
}

/** 状态徽标：颜色 + 文字（docs/04 §8.1）。 */
export function ToolStatusBadge({
  status,
  className,
}: {
  status: ToolStatus;
  className?: string;
}) {
  return (
    <Badge variant={TOOL_STATUS_VARIANTS[status]} className={className}>
      {TOOL_STATUS_LABELS[status]}
    </Badge>
  );
}

export const VISIBILITY_OPTIONS: ReadonlyArray<{ value: Visibility; label: string }> = (
  Object.keys(VISIBILITY_LABELS) as Visibility[]
).map((value) => ({ value, label: VISIBILITY_LABELS[value] }));

export interface AdminToolTableProps {
  items: readonly AdminToolItem[];
  checkedIds: readonly number[];
  onToggleChecked: (toolId: number, checked: boolean) => void;
  onToggleAll: (checked: boolean) => void;
  onOffline: (tool: AdminToolItem) => void;
  onRelist: (tool: AdminToolItem) => void;
  onTransfer: (tool: AdminToolItem) => void;
  /** 转移负责人是 superadmin 专属（docs/01 §3.2、后端 `admin_all_guard`）。 */
  canTransfer: boolean;
  /** 批量动作执行中：禁用会并发写状态的行内操作。 */
  busy: boolean;
}

export function AdminToolTable({
  items,
  checkedIds,
  onToggleChecked,
  onToggleAll,
  onOffline,
  onRelist,
  onTransfer,
  canTransfer,
  busy,
}: AdminToolTableProps) {
  const checked = React.useMemo(() => new Set(checkedIds), [checkedIds]);
  const allChecked = items.length > 0 && items.every((item) => checked.has(item.id));
  const someChecked = !allChecked && items.some((item) => checked.has(item.id));

  return (
    <Table className="min-w-[1080px]">
      <TableHeader>
        <TableRow>
          <TableHead className="w-10">
            <Checkbox
              checked={allChecked ? true : someChecked ? "indeterminate" : false}
              aria-label="全选本页工具"
              onCheckedChange={(value) => onToggleAll(value === true)}
            />
          </TableHead>
          <TableHead>工具</TableHead>
          <TableHead>类型</TableHead>
          <TableHead>状态</TableHead>
          <TableHead>可见性</TableHead>
          <TableHead>作者</TableHead>
          <TableHead>当前版本</TableHead>
          <TableHead className="text-right">下载量</TableHead>
          <TableHead>更新时间</TableHead>
          <TableHead className="w-14 text-right">操作</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((tool) => (
          <TableRow key={tool.id} data-testid="admin-tool-row" data-tool-id={tool.id}>
            <TableCell>
              <Checkbox
                checked={checked.has(tool.id)}
                aria-label={`选择 ${tool.name}`}
                onCheckedChange={(value) => onToggleChecked(tool.id, value === true)}
              />
            </TableCell>

            <TableCell className="max-w-[20rem] whitespace-normal">
              <div className="flex items-center gap-2">
                <CoverThumb url={tool.cover_url} />
                <div className="min-w-0">
                  <p className="truncate font-medium">{tool.name}</p>
                  <p className="truncate font-mono text-xs text-muted-foreground">{tool.slug}</p>
                </div>
              </div>
            </TableCell>

            <TableCell>
              <ToolTypeBadge type={tool.tool_type} />
            </TableCell>

            <TableCell>
              <ToolStatusBadge status={tool.status} />
            </TableCell>

            <TableCell>
              <Badge variant="outline">{VISIBILITY_LABELS[tool.visibility]}</Badge>
            </TableCell>

            <TableCell>
              {tool.owner ? (
                <span title={tool.owner.username}>{tool.owner.display_name}</span>
              ) : (
                <span className="text-muted-foreground">—</span>
              )}
            </TableCell>

            <TableCell className="font-mono text-xs tabular-nums">
              {tool.current_version ?? "—"}
            </TableCell>

            <TableCell className="text-right tabular-nums">
              {formatCount(tool.download_count)}
            </TableCell>

            <TableCell>
              <time dateTime={tool.updated_at ?? undefined} title={formatDateTime(tool.updated_at)}>
                {formatRelativeTime(tool.updated_at)}
              </time>
            </TableCell>

            <TableCell className="text-right">
              <RowActions
                tool={tool}
                canTransfer={canTransfer}
                busy={busy}
                onOffline={onOffline}
                onRelist={onRelist}
                onTransfer={onTransfer}
              />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/** 封面缩略图：加载失败就地隐藏，避免破图（docs/04 §6.12）。 */
function CoverThumb({ url }: { url: string | null }) {
  const [failed, setFailed] = React.useState(false);

  if (!url || failed) {
    return <div aria-hidden="true" className="size-9 shrink-0 rounded-md border bg-muted" />;
  }
  return (
    <img
      src={url}
      alt=""
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
      className="size-9 shrink-0 rounded-md border object-cover"
    />
  );
}

function RowActions({
  tool,
  canTransfer,
  busy,
  onOffline,
  onRelist,
  onTransfer,
}: {
  tool: AdminToolItem;
  canTransfer: boolean;
  busy: boolean;
  onOffline: (tool: AdminToolItem) => void;
  onRelist: (tool: AdminToolItem) => void;
  onTransfer: (tool: AdminToolItem) => void;
}) {
  // 状态机决定可用动作（docs/01 §4.1）：只有已发布/有新版待审能下架，只有已下架能重新上架。
  const canOffline = tool.status === "approved" || tool.status === "pending_update";
  const canRelist = tool.status === "offline";
  const showStateActions = canOffline || canRelist || canTransfer;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label={`操作：${tool.name}`}>
          <MoreHorizontal aria-hidden="true" className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-44">
        {/*
          查看详情用原生 <a target="_blank">：管理员正在筛选/翻页，新标签页保留
          列表上下文（也可以在同一 SPA 内 <Link> 跳转，这里选前者）。
        */}
        <DropdownMenuItem asChild>
          <a href={`/tools/${tool.slug}`} target="_blank" rel="noopener noreferrer">
            <ExternalLink aria-hidden="true" className="size-4" />
            查看详情
          </a>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <Link to={`/admin/approvals/history?tool_id=${tool.id}`}>
            <HistoryIcon aria-hidden="true" className="size-4" />
            查看审批历史
          </Link>
        </DropdownMenuItem>

        {showStateActions ? <DropdownMenuSeparator /> : null}

        {canOffline ? (
          <DropdownMenuItem variant="destructive" disabled={busy} onSelect={() => onOffline(tool)}>
            <ShieldOff aria-hidden="true" className="size-4" />
            下架
          </DropdownMenuItem>
        ) : null}
        {canRelist ? (
          <DropdownMenuItem disabled={busy} onSelect={() => onRelist(tool)}>
            <RotateCcw aria-hidden="true" className="size-4" />
            重新上架
          </DropdownMenuItem>
        ) : null}
        {canTransfer ? (
          <DropdownMenuItem disabled={busy} onSelect={() => onTransfer(tool)}>
            <UserRoundCog aria-hidden="true" className="size-4" />
            转移负责人
          </DropdownMenuItem>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export default AdminToolTable;
