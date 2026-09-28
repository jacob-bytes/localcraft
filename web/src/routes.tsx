import { Suspense } from "react";
import { createBrowserRouter, Outlet } from "react-router-dom";

import { AllowAnonymous } from "@/components/auth/AllowAnonymous";
import { RequireAuth } from "@/components/auth/RequireAuth";
import { RequireRole } from "@/components/auth/RequireRole";
import { FullScreenLoader } from "@/components/common/PageSkeleton";
import { RouteErrorBoundary } from "@/components/common/RouteErrorBoundary";
import { AdminLayout } from "@/components/layout/AdminLayout";
import { AppShell } from "@/components/layout/AppShell";
import { AuthProvider } from "@/hooks/useAuth";
import { lazyWithRetry } from "@/lib/lazyWithRetry";

/**
 * Route table (docs/04 §4).
 *
 * M2 adds the tool detail page, the personal centre and the approval console.
 * `/admin/*` beyond approvals / whitelist / settings stays out of the table
 * (M3, CONTRACT §6.1) so it falls through to the 404 page instead of a
 * half-built screen.
 *
 * Every page is code-split with `React.lazy` (docs/04 §9); the shells provide
 * Suspense boundaries. M4 wraps every route-level `import()` in `lazyWithRetry`
 * (CONTRACT §19.9) so a failed chunk fetch reloads the page once instead of
 * showing the error boundary — the chunk-file names change on every deploy, and
 * a stale tab must be able to recover on its own.
 */
const LoginPage = lazyWithRetry(() => import("@/pages/LoginPage"), "LoginPage");
const ChangePasswordPage = lazyWithRetry(
  () => import("@/pages/ChangePasswordPage"),
  "ChangePasswordPage",
);
const PortalPage = lazyWithRetry(() => import("@/pages/PortalPage"), "PortalPage");
const ToolDetailPage = lazyWithRetry(() => import("@/pages/ToolDetailPage"), "ToolDetailPage");
const NotFoundPage = lazyWithRetry(() => import("@/pages/NotFoundPage"), "NotFoundPage");

const ProfilePage = lazyWithRetry(() => import("@/pages/me/ProfilePage"), "ProfilePage");
const MyToolsPage = lazyWithRetry(() => import("@/pages/me/MyToolsPage"), "MyToolsPage");
const MyFavoritesPage = lazyWithRetry(
  () => import("@/pages/me/MyFavoritesPage"),
  "MyFavoritesPage",
);
const ToolEditorPage = lazyWithRetry(() => import("@/pages/me/ToolEditorPage"), "ToolEditorPage");
const VersionsPage = lazyWithRetry(() => import("@/pages/me/VersionsPage"), "VersionsPage");

const AdminIndexPage = lazyWithRetry(
  () => import("@/pages/admin/AdminIndexPage"),
  "AdminIndexPage",
);
const ApprovalsPage = lazyWithRetry(() => import("@/pages/admin/ApprovalsPage"), "ApprovalsPage");
const ApprovalHistoryPage = lazyWithRetry(
  () => import("@/pages/admin/ApprovalHistoryPage"),
  "ApprovalHistoryPage",
);
const AdminToolsPage = lazyWithRetry(
  () => import("@/pages/admin/AdminToolsPage"),
  "AdminToolsPage",
);
const CategoriesPage = lazyWithRetry(
  () => import("@/pages/admin/CategoriesPage"),
  "CategoriesPage",
);
const TagsPage = lazyWithRetry(() => import("@/pages/admin/TagsPage"), "TagsPage");
const UsersPage = lazyWithRetry(() => import("@/pages/admin/UsersPage"), "UsersPage");
const GroupsPage = lazyWithRetry(() => import("@/pages/admin/GroupsPage"), "GroupsPage");
const WhitelistPage = lazyWithRetry(() => import("@/pages/admin/WhitelistPage"), "WhitelistPage");
const TokensPage = lazyWithRetry(() => import("@/pages/admin/TokensPage"), "TokensPage");
const SettingsPage = lazyWithRetry(() => import("@/pages/admin/SettingsPage"), "SettingsPage");
const ImportExportPage = lazyWithRetry(
  () => import("@/pages/admin/ImportExportPage"),
  "ImportExportPage",
);
const RecycleBinPage = lazyWithRetry(
  () => import("@/pages/admin/RecycleBinPage"),
  "RecycleBinPage",
);

/** Roles allowed to create/edit their own tools (docs/01 §3.2 — viewer excluded). */
const AUTHOR_ROLES = ["user", "approver", "superadmin"] as const;
/** Roles allowed into the approval console. */
const REVIEWER_ROLES = ["approver", "superadmin"] as const;
/** Roles allowed to manage the whitelist and system settings. */
const ADMIN_ROLES = ["superadmin"] as const;

/** AuthProvider lives inside the router so 401 handling can navigate. */
function AuthBoundary() {
  return (
    <AuthProvider>
      <Outlet />
    </AuthProvider>
  );
}

export const router = createBrowserRouter([
  {
    element: <AuthBoundary />,
    // 兜底：AuthProvider 自身出错时也有可读的界面，而不是开发态的默认错误页
    errorElement: <RouteErrorBoundary />,
    children: [
      {
        path: "/login",
        element: (
          <Suspense fallback={<FullScreenLoader label="加载中…" />}>
            <LoginPage />
          </Suspense>
        ),
      },
      {
        // 这里**不再**包 RequireAuth：门户首页与工具详情允许匿名访问（是否放行
        // 由 AllowAnonymous 依 portal.allow_anonymous_view 判定），守卫因此下沉到
        // 下面两层 —— 公开层用 AllowAnonymous，需要登录的用 RequireAuth。
        element: <AppShell />,
        errorElement: <RouteErrorBoundary />,
        children: [
          {
            /*
             * 无路径的「错误边界层」：页面级失败（含懒加载 chunk 拉取失败）由它接管，
             * 顶栏与侧栏保持挂载 —— 用户还能直接切到别处，而不是整屏被替换掉。
             */
            errorElement: <RouteErrorBoundary />,
            children: [
              /* ---------- 公开层：匿名可看，是否放行由 portal.allow_anonymous_view 决定 ---------- */
              {
                element: (
                  <AllowAnonymous>
                    <Outlet />
                  </AllowAnonymous>
                ),
                children: [
                  /*
                   * M12 · F2（CONTRACT §27.4）：站点页脚**只**出现在门户列表页与工具
                   * 详情页。范围写在路由表上（`handle.siteFooter`），由 `AppShell`
                   * 读取 —— 页脚因此渲染在 `<main>` 之外（`footer` 只有在 `main`
                   * 之外才映射 `contentinfo` 地标）。个人中心 / 管理台 / 登录页没有
                   * 这个标记，也就一个 `<footer>` 都不会有。
                   */
                  { index: true, element: <PortalPage />, handle: { siteFooter: true } },
                  {
                    path: "tools/:slug",
                    element: <ToolDetailPage />,
                    handle: { siteFooter: true },
                  },
                ],
              },

              /* ---------- 以下需要登录 ---------- */
              {
                element: (
                  <RequireAuth>
                    <Outlet />
                  </RequireAuth>
                ),
                children: [
                  { path: "change-password", element: <ChangePasswordPage /> },

                  /*
                   * M8 · F7.3：我的收藏。
                   *
                   * M9 修正（§25.1）：M8 当时把它**刻意放在 `RequireRole` 之外**，
                   * 理由是「契约 §23 对收藏没有任何角色限制，`viewer` 也应该能收藏」。
                   * 那个假设已被裁定推翻 —— §23.4 只是**漏写**了授权，权威是后端
                   * `engagement_guard`（`user` / `approver` / `superadmin`，
                   * **不含 `viewer`**，实测 viewer 收藏必 403）。`viewer` 既然永远
                   * 收藏不了，这一页对它必然恒空，且空态里「点卡片右上角的收藏」这句
                   * 指引对它是假的，所以这里补上与 `/me/*` 同一个角色门：
                   * `viewer` 直达 URL 会看到「没有访问权限」，而不是一个空收藏夹。
                   */
                  {
                    path: "me/favorites",
                    element: (
                      <RequireRole roles={[...AUTHOR_ROLES]}>
                        <MyFavoritesPage />
                      </RequireRole>
                    ),
                  },

                  {
                    path: "me",
                    element: (
                      <RequireRole roles={[...AUTHOR_ROLES]}>
                        <Outlet />
                      </RequireRole>
                    ),
                    children: [
                      { index: true, element: <ProfilePage /> },
                      { path: "tools", element: <MyToolsPage /> },
                      { path: "tools/new", element: <ToolEditorPage mode="create" /> },
                      { path: "tools/:id/edit", element: <ToolEditorPage mode="edit" /> },
                      { path: "tools/:id/versions", element: <VersionsPage /> },
                    ],
                  },

                  {
                    path: "admin",
                    element: (
                      <RequireRole roles={[...REVIEWER_ROLES]}>
                        <AdminLayout />
                      </RequireRole>
                    ),
                    children: [
                      { index: true, element: <AdminIndexPage /> },
                      { path: "approvals", element: <ApprovalsPage /> },
                      { path: "approvals/history", element: <ApprovalHistoryPage /> },
                      // approver 也能用的两项（docs/04 §5.2 的菜单分组）
                      { path: "tools", element: <AdminToolsPage /> },
                      { path: "categories", element: <CategoriesPage /> },
                      { path: "tags", element: <TagsPage /> },
                      // 以下全部是超管专属（服务端同样有 users:write / groups:write /
                      // settings:write / admin:all 的 Scope 守卫）
                      {
                        path: "users",
                        element: (
                          <RequireRole roles={[...ADMIN_ROLES]}>
                            <UsersPage />
                          </RequireRole>
                        ),
                      },
                      {
                        path: "groups",
                        element: (
                          <RequireRole roles={[...ADMIN_ROLES]}>
                            <GroupsPage />
                          </RequireRole>
                        ),
                      },
                      {
                        path: "whitelist",
                        element: (
                          <RequireRole roles={[...ADMIN_ROLES]}>
                            <WhitelistPage />
                          </RequireRole>
                        ),
                      },
                      {
                        path: "tokens",
                        element: (
                          <RequireRole roles={[...ADMIN_ROLES]}>
                            <TokensPage />
                          </RequireRole>
                        ),
                      },
                      {
                        path: "settings",
                        element: (
                          <RequireRole roles={[...ADMIN_ROLES]}>
                            <SettingsPage />
                          </RequireRole>
                        ),
                      },
                      {
                        path: "import-export",
                        element: (
                          <RequireRole roles={[...ADMIN_ROLES]}>
                            <ImportExportPage />
                          </RequireRole>
                        ),
                      },
                      {
                        path: "recycle-bin",
                        element: (
                          <RequireRole roles={[...ADMIN_ROLES]}>
                            <RecycleBinPage />
                          </RequireRole>
                        ),
                      },
                    ],
                  },

                ],
              },

              { path: "*", element: <NotFoundPage /> },
            ],
          },
        ],
      },
    ],
  },
]);
