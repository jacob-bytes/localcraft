import { Star } from "lucide-react";
import { Link, useLocation } from "react-router-dom";

import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";

/**
 * M8 · F7.4：门户左栏顶部的导航项（「我的收藏」）。
 *
 * 为什么放在这里而不是全局侧边栏：docs/04 §5.1 明确「不做左侧固定侧边栏」——
 * 门户的信息架构是「分类筛选 + 卡片墙」，分类导航本来就在内容区左侧。这一块挂在
 * 那个左栏的顶部，`lg` 以下随筛选栏一起进 Sheet，因此手机上也有入口。
 *
 * **匿名整块不渲染**：收藏是登录态功能（§23.5：匿名 `is_favorited` 恒为 false），
 * 未登录时给一个点进去就被重定向的入口只会制造困惑 —— 与卡片上「不显示收藏按钮、
 * 只显示计数」是同一套匿名策略。
 */
export function PortalSideNav() {
  const { status } = useAuth();
  const location = useLocation();

  if (status !== "authenticated") return null;

  const active = location.pathname.startsWith("/me/favorites");

  return (
    <nav
      aria-label="门户导航"
      data-testid="portal-side-nav"
      className="mb-3 border-b pb-3"
    >
      <ul>
        <li>
          <Link
            to="/me/favorites"
            data-testid="nav-my-favorites"
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
              "focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
              active
                ? "bg-accent font-medium text-accent-foreground"
                : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
            )}
          >
            <Star aria-hidden="true" className="size-4" />
            我的收藏
          </Link>
        </li>
      </ul>
    </nav>
  );
}
