import { Boxes, KeyRound } from "lucide-react";
import { Link } from "react-router-dom";

import { GlobalSearch } from "@/components/layout/GlobalSearch";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { UserMenu } from "@/components/layout/UserMenu";
import { useAuth } from "@/hooks/useAuth";
import { siteName, useMeta } from "@/hooks/useMeta";

/**
 * Sticky top bar (docs/04 §5.1). No global left sidebar — the portal's
 * information architecture puts navigation in the content area, and the admin
 * console is the only place that gets a vertical menu (M3).
 *
 * `minimal` is the mandatory-password-change mode: all navigation entries are
 * hidden (docs/04 §6.2).
 */
export function TopNav() {
  const { user } = useAuth();
  const { data: meta } = useMeta();
  const minimal = user?.must_change_password === true;

  return (
    <header
      data-testid="top-nav"
      className="sticky top-0 z-40 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80">
      <div className="mx-auto flex h-14 w-full max-w-[1400px] items-center gap-3 px-4 sm:px-6 lg:px-8">
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

        {minimal ? (
          <span className="ml-1 inline-flex items-center gap-1.5 rounded-md bg-amber-100 px-2 py-1 text-xs font-medium text-amber-900 dark:bg-amber-950/70 dark:text-amber-200">
            <KeyRound aria-hidden="true" className="size-3.5" />
            需先修改密码
          </span>
        ) : (
          <div className="ml-2 flex flex-1 justify-start sm:justify-center">
            <GlobalSearch />
          </div>
        )}

        <div className="ml-auto flex shrink-0 items-center gap-1">
          <ThemeToggle />
          <UserMenu minimal={minimal} />
        </div>
      </div>
    </header>
  );
}
