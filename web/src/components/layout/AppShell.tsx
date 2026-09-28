import * as React from "react";
import { Outlet, useMatches } from "react-router-dom";

import { PageSkeleton } from "@/components/common/PageSkeleton";
import { SiteFooter } from "@/components/layout/SiteFooter";
import { TopNav } from "@/components/layout/TopNav";

/** 路由 `handle` 上与本层有关的开关（CONTRACT §27.4 的页脚范围裁定）。 */
interface AppShellRouteHandle {
  /** `true` 时这一条路由渲染站点页脚（仅门户列表页与工具详情页）。 */
  siteFooter?: boolean;
}

function isAppShellRouteHandle(handle: unknown): handle is AppShellRouteHandle {
  return typeof handle === "object" && handle !== null;
}

/**
 * Application shell: sticky top bar + content area + optional footer
 * (docs/04 §5.1). Route-level code splitting (docs/04 §9) means every page
 * arrives through `React.lazy`, so the outlet is wrapped in Suspense here.
 *
 * M12 改动（CONTRACT §27.4）：
 *
 * - 原先这里有一段**全局页脚**（`localcraft v… · API v1` + 写死的
 *   「内网工具与 Skill 共享平台」）。§27.4 把页脚范围裁定为**仅门户列表页与工具
 *   详情页**，且要求页脚内容来自结构化设置项，所以那段全局页脚被 `SiteFooter`
 *   取代。副作用：个人中心与管理台不再出现这一行版本号（这正是「页脚只在门户
 *   相关页」的字面含义；它的存在本身就会与「/admin 没有页脚」矛盾）。
 * - 页脚渲染在 `<main>` **之外**：ARIA 规定 `<footer>` 落在 `main` 内时不映射
 *   `contentinfo` 地标，见 `SiteFooter` 的注释。
 * - 是否渲染由路由 `handle.siteFooter` 决定（`routes.tsx` 只在 `/` 与
 *   `/tools/:slug` 上打了这个标记），而不是在 shell 里比对路径字符串 —— 路由表
 *   是唯一的真相来源。
 */
export function AppShell() {
  const matches = useMatches();
  const showSiteFooter = matches.some((match) =>
    isAppShellRouteHandle(match.handle) ? match.handle.siteFooter === true : false,
  );

  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      <a className="skip-link" href="#main-content">
        跳到主内容
      </a>

      <TopNav />

      <main id="main-content" className="flex-1">
        <React.Suspense fallback={<PageSkeleton variant="page" />}>
          <Outlet />
        </React.Suspense>
      </main>

      {showSiteFooter ? <SiteFooter /> : null}
    </div>
  );
}
