import * as React from "react";
import { Outlet, useMatches } from "react-router-dom";

import { PageSkeleton } from "@/components/common/PageSkeleton";
import { SiteFooter } from "@/components/layout/SiteFooter";
import { TopNav } from "@/components/layout/TopNav";

/**
 * 路由 `handle` 上与本层有关的开关（CONTRACT §28.6 的页脚范围裁定）。
 *
 * M13 起这个开关**只**管 4 个门户配置字段 —— 页脚元素本身已恢复为全站渲染。
 */
interface AppShellRouteHandle {
  /** `true` 时这一条路由额外渲染门户配置字段（仅门户列表页与工具详情页）。 */
  portalFooterFields?: boolean;
}

function isAppShellRouteHandle(handle: unknown): handle is AppShellRouteHandle {
  return typeof handle === "object" && handle !== null;
}

/**
 * Application shell: sticky top bar + content area + footer (docs/04 §5.1).
 * Route-level code splitting (docs/04 §9) means every page arrives through
 * `React.lazy`, so the outlet is wrapped in Suspense here.
 *
 * 页脚（M12 建立、**M13 按 §28.6 重构**）：
 *
 * - M12 曾按 §27.4 把这里原有的**全局页脚**整段删掉，改由路由 `handle` 决定是否
 *   渲染 `SiteFooter`。§28.1 认定那是一条契约缺陷（写 §27 时漏看了既有页脚），
 *   §28.6 因此裁定：**版本行 + `footer_tagline` 标语恢复为全站显示**，只有 4 个
 *   门户配置字段仍限于门户相关页。
 * - 所以现在 `SiteFooter` **无条件渲染**，路由 `handle.portalFooterFields` 只决定
 *   要不要多渲染那 4 个字段。`/me`、`/admin` 有版本行与标语，没有门户字段。
 * - 页脚渲染在 `<main>` **之外**：ARIA 规定 `<footer>` 落在 `main` 内时不映射
 *   `contentinfo` 地标，见 `SiteFooter` 的注释。
 * - 范围写在路由表上（`routes.tsx` 只在 `/` 与 `/tools/:slug` 上打了标记），而不是
 *   在 shell 里比对路径字符串 —— 路由表是唯一的真相来源。
 * - 登录页不在 `AppShell` 之下，因此它没有页脚（与改动前一致）。
 */
export function AppShell() {
  const matches = useMatches();
  const showPortalFooterFields = matches.some((match) =>
    isAppShellRouteHandle(match.handle) ? match.handle.portalFooterFields === true : false,
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

      <SiteFooter portalFields={showPortalFooterFields} />
    </div>
  );
}
