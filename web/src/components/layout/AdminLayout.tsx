import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeftRight,
  ClipboardList,
  FolderTree,
  Gauge,
  History as HistoryIcon,
  KeyRound,
  Menu,
  ShieldCheck,
  SlidersHorizontal,
  Tags as TagsIcon,
  Trash2,
  Users as UsersIcon,
  UsersRound,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import * as React from "react";
import { NavLink, Outlet } from "react-router-dom";

import { approvalsQueryKey, fetchApprovals } from "@/api/admin";
import type { Role } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { useAuth } from "@/hooks/useAuth";
import { PAGE_CONTAINER } from "@/lib/layout";
import { cn } from "@/lib/utils";

/**
 * 管理台外壳（docs/04 §5.2）。AppShell 已经渲染了 TopNav，这里只负责
 * 「左侧竖向菜单 + 右侧内容区」，页面本身再由 `<Outlet/>` 接管。
 *
 * 菜单按角色**过滤**而不是「渲染出来再禁用」：禁用态的菜单项会泄露功能边界
 * （docs/01 §3.2 权限矩阵 + docs/04 §5.2 的分组说明）。
 *   - approver：概览 / 审批队列 / 审批历史 / 全站工具 / 分类 / 标签
 *   - superadmin：再加 用户 / 用户组 / 免审白名单 / API Token / 系统设置 / 导入导出 / 回收站
 * 前端守卫只是体验优化，服务端才是权威（docs/04 §4）。
 */

interface AdminMenuItem {
  to: string;
  label: string;
  icon: LucideIcon;
  /** 只对超管可见（其余全部是 M3 的超管专属功能）。 */
  superadminOnly?: boolean;
  /** 精确匹配才高亮（否则 `/admin/approvals` 会同时点亮 `/admin/approvals/history`）。 */
  end?: boolean;
}

const MENU_ITEMS: readonly AdminMenuItem[] = [
  { to: "/admin", label: "概览", icon: Gauge, end: true },
  { to: "/admin/approvals", label: "审批队列", icon: ClipboardList, end: true },
  { to: "/admin/approvals/history", label: "审批历史", icon: HistoryIcon },
  { to: "/admin/tools", label: "全站工具", icon: Wrench },
  { to: "/admin/categories", label: "分类", icon: FolderTree },
  { to: "/admin/tags", label: "标签", icon: TagsIcon },
  { to: "/admin/users", label: "用户", icon: UsersIcon, superadminOnly: true },
  { to: "/admin/groups", label: "用户组", icon: UsersRound, superadminOnly: true },
  { to: "/admin/whitelist", label: "免审白名单", icon: ShieldCheck, superadminOnly: true },
  { to: "/admin/tokens", label: "API Token", icon: KeyRound, superadminOnly: true },
  { to: "/admin/settings", label: "系统设置", icon: SlidersHorizontal, superadminOnly: true },
  { to: "/admin/import-export", label: "导入导出", icon: ArrowLeftRight, superadminOnly: true },
  { to: "/admin/recycle-bin", label: "回收站", icon: Trash2, superadminOnly: true },
];

/** Tolerates the odd `null` role entry so a malformed session cannot crash the shell. */
function hasSuperadmin(roles: readonly (Role | null | undefined)[]): boolean {
  return roles.includes("superadmin");
}

export function AdminLayout() {
  const { user } = useAuth();
  const [mobileNavOpen, setMobileNavOpen] = React.useState(false);

  const isSuperadmin = hasSuperadmin(user?.roles ?? []);
  const items = React.useMemo(
    () => MENU_ITEMS.filter((item) => !item.superadminOnly || isSuperadmin),
    [isSuperadmin],
  );

  // 待审角标（§5.2）：只取第一页，用 `total` 当计数，30 秒内不重复请求。
  const { data: pending } = useQuery({
    queryKey: approvalsQueryKey({ status: "pending_all", page_size: 1 }),
    queryFn: ({ signal }) => fetchApprovals({ status: "pending_all", page_size: 1 }, signal),
    staleTime: 30_000,
  });
  const pendingCount = pending?.total ?? 0;

  const closeMobileNav = React.useCallback(() => setMobileNavOpen(false), []);

  const nav = <AdminNav items={items} pendingCount={pendingCount} onNavigate={closeMobileNav} />;

  return (
    <div
      data-testid="admin-layout"
      className={`${PAGE_CONTAINER} flex flex-col gap-4 py-6 lg:flex-row lg:gap-8`}
    >
      {/* `lg` 以下折叠为 Sheet（docs/04 §5.2 / §8.2）。 */}
      <div className="lg:hidden">
        <Sheet open={mobileNavOpen} onOpenChange={setMobileNavOpen}>
          <SheetTrigger asChild>
            <Button variant="outline" size="sm" aria-label="打开管理台菜单">
              <Menu aria-hidden="true" className="size-4" />
              菜单
            </Button>
          </SheetTrigger>
          <SheetContent side="left" className="w-72 max-w-[85vw] overflow-y-auto p-0">
            <SheetHeader>
              <SheetTitle>管理台</SheetTitle>
            </SheetHeader>
            <div className="px-2 pb-6">{nav}</div>
          </SheetContent>
        </Sheet>
      </div>

      <aside className="hidden w-60 shrink-0 lg:block" aria-label="管理台导航">
        {nav}
      </aside>

      <div className="min-w-0 flex-1">
        <React.Suspense fallback={null}>
          <Outlet />
        </React.Suspense>
      </div>
    </div>
  );
}

function AdminNav({
  items,
  pendingCount,
  onNavigate,
}: {
  items: readonly AdminMenuItem[];
  pendingCount: number;
  onNavigate: () => void;
}) {
  return (
    <nav data-testid="admin-nav" aria-label="管理台" className="flex flex-col gap-1 text-sm">
      {items.map((item, index) => {
        // 分隔线把「审批」与「超管专属」两组隔开（docs/04 §5.2）。
        const previous = items[index - 1];
        const showSeparator =
          item.superadminOnly === true && previous !== undefined && previous.superadminOnly !== true;
        return (
          <React.Fragment key={item.to}>
            {showSeparator ? <div className="my-2 border-t" role="presentation" /> : null}
            <AdminNavLink item={item} pendingCount={pendingCount} onNavigate={onNavigate} />
          </React.Fragment>
        );
      })}
    </nav>
  );
}

function AdminNavLink({
  item,
  pendingCount,
  onNavigate,
}: {
  item: AdminMenuItem;
  pendingCount: number;
  onNavigate: () => void;
}) {
  const Icon = item.icon;
  const showBadge = item.to === "/admin/approvals";

  return (
    <NavLink
      to={item.to}
      end={item.end}
      onClick={onNavigate}
      className={({ isActive }) =>
        cn(
          "flex items-center gap-2 rounded-md px-3 py-2 font-medium transition-colors",
          "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:outline-none",
          isActive
            ? "bg-accent text-accent-foreground"
            : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
        )
      }
    >
      <Icon aria-hidden="true" className="size-4 shrink-0" />
      <span className="truncate">{item.label}</span>
      {showBadge ? (
        <Badge
          className="ml-auto tabular-nums"
          variant={pendingCount > 0 ? "warning" : "secondary"}
          aria-label={`待处理 ${pendingCount} 条`}
        >
          {pendingCount}
        </Badge>
      ) : null}
    </NavLink>
  );
}

export default AdminLayout;
