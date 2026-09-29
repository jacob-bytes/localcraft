import { Boxes, KeyRound, LogIn } from "lucide-react";
import { Link } from "react-router-dom";

import { GlobalSearch } from "@/components/layout/GlobalSearch";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { UserMenu } from "@/components/layout/UserMenu";
import { useAuth } from "@/hooks/useAuth";
import { siteName, useMeta } from "@/hooks/useMeta";
import { PAGE_CONTAINER } from "@/lib/layout";

/**
 * Sticky top bar (docs/04 §5.1). No global left sidebar — the portal's
 * information architecture puts navigation in the content area, and the admin
 * console is the only place that gets a vertical menu (M3).
 *
 * `minimal` is the mandatory-password-change mode: all navigation entries are
 * hidden (docs/04 §6.2).
 */
export function TopNav() {
  const { status, user } = useAuth();
  const { data: meta } = useMeta();
  const minimal = user?.must_change_password === true;
  /* 匿名访客（门户默认允许未登录浏览）——顶栏要给出登录入口，否则没地方登 */
  const anonymous = status === "unauthenticated";

  return (
    <header
      data-testid="top-nav"
      className="sticky top-0 z-40 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80">
      <div className={`${PAGE_CONTAINER} flex h-14 items-center gap-3`}>
        <Link
          to="/"
          className="flex shrink-0 items-center gap-2 rounded-md py-1 font-semibold"
          aria-label={`${siteName(meta)} 首页`}
        >
          <span className="grid size-7 place-items-center rounded-md bg-primary text-primary-foreground">
            <Boxes aria-hidden="true" className="size-4" />
          </span>
          <span className="hidden max-w-[10rem] truncate sm:inline">{siteName(meta)}</span>
        </Link>

        {/*
          M7 · F1：搜索触发器从「正中」移到右侧集群，排在 ThemeToggle 左侧。
          视觉顺序 = [logo] …(留白)… [搜索] [主题切换] [登录/用户菜单]。

          左侧留白由本集群的 `ml-auto` 产生（不再用 `flex-1` + `justify-center`，
          那种写法在窄屏会把 logo 和右侧图标挤爆）。

          `min-w-0` + 搜索触发器的 `sm:shrink` 是防溢出的关键：窗口刚到 sm（640）
          时 logo 站点名与用户菜单的角色文字同时展开，固定宽度会溢出；允许搜索框
          先收缩（内部文字 `truncate`）比产生横向滚动条更好。

          `minimal`（强制改密）下搜索被 Badge 取代；它同样留在右侧集群里，
          这样不会出现一个孤立的左对齐徽标（任务书 F1 第 3 条）。
        */}
        <div className="ml-auto flex min-w-0 items-center gap-1">
          {minimal ? (
            <Badge variant="warning" className="gap-1.5 py-1">
              <KeyRound aria-hidden="true" className="size-3.5" />
              需先修改密码
            </Badge>
          ) : (
            <GlobalSearch />
          )}

          <ThemeToggle />
          {anonymous ? (
            <Button asChild size="sm" data-testid="login-link">
              <Link to="/login">
                <LogIn aria-hidden="true" className="size-4" />
                登录
              </Link>
            </Button>
          ) : (
            <UserMenu minimal={minimal} />
          )}
        </div>
      </div>
    </header>
  );
}
