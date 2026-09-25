import {
  ChevronDown,
  KeyRound,
  LogOut,
  Package as PackageIcon,
  ShieldCheck,
  User as UserIcon,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAuth } from "@/hooks/useAuth";
import { initials } from "@/lib/format";
import { canApprove, primaryRoleLabel } from "@/lib/permissions";

/**
 * Top-bar user menu (docs/04 §5.1).
 *
 * M1 exposes only the entries whose pages exist: 修改密码 and 退出登录.
 * 个人中心 / 我的工具 / 管理后台 are M2/M3 routes and are deliberately absent
 * rather than linking to a 404 — see the checkpoint report.
 */
export function UserMenu({ minimal = false }: { minimal?: boolean }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  if (!user) return null;

  const roleLabel = primaryRoleLabel(user);
  const displayName = user.display_name || user.username;

  async function handleLogout() {
    await logout();
    toast.success("已退出登录");
    navigate("/login", { replace: true });
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          data-testid="user-menu-trigger"
          className="h-9 gap-2 px-2"
          aria-label={`用户菜单：${displayName}`}
        >
          <Avatar className="size-7">
            <AvatarFallback className="bg-primary/10 text-xs font-medium text-primary">
              {initials(displayName)}
            </AvatarFallback>
          </Avatar>
          <span className="hidden max-w-[8rem] items-center gap-1.5 sm:flex">
            <span className="truncate text-sm font-medium">{roleLabel || displayName}</span>
            <ChevronDown aria-hidden="true" className="size-3.5 text-muted-foreground" />
          </span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuLabel className="flex flex-col gap-0.5">
          <span className="truncate text-sm font-medium text-foreground">{displayName}</span>
          <span className="truncate font-mono text-xs text-muted-foreground">
            {user.username}
          </span>
          {roleLabel ? (
            <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
              <ShieldCheck aria-hidden="true" className="size-3" />
              {roleLabel}
            </span>
          ) : null}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />

        {minimal ? (
          <DropdownMenuLabel className="flex items-center gap-1.5 text-xs font-normal text-muted-foreground">
            <UserIcon aria-hidden="true" className="size-3.5" />
            请先修改初始密码后再使用其他功能
          </DropdownMenuLabel>
        ) : (
          <>
            {/* M2 启用：个人中心 / 我的工具 / 管理后台（审批台已在 M2 落地） */}
            <DropdownMenuItem
              onSelect={() => {
                navigate("/me");
              }}
            >
              <UserIcon aria-hidden="true" className="size-4" />
              个人中心
            </DropdownMenuItem>
            <DropdownMenuItem
              onSelect={() => {
                navigate("/me/tools");
              }}
            >
              <PackageIcon aria-hidden="true" className="size-4" />
              我的工具
            </DropdownMenuItem>
            {canApprove(user) ? (
              <DropdownMenuItem
                onSelect={() => {
                  navigate("/admin/approvals");
                }}
              >
                <ShieldCheck aria-hidden="true" className="size-4" />
                管理后台
              </DropdownMenuItem>
            ) : null}
          </>
        )}

        <DropdownMenuItem
          onSelect={() => {
            navigate("/change-password");
          }}
        >
          <KeyRound aria-hidden="true" className="size-4" />
          修改密码
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onSelect={() => {
            void handleLogout();
          }}
        >
          <LogOut aria-hidden="true" className="size-4" />
          退出登录
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
