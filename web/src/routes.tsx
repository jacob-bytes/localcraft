import { lazy, Suspense } from "react";
import { createBrowserRouter, Outlet } from "react-router-dom";

import { RequireAuth } from "@/components/auth/RequireAuth";
import { RequireRole } from "@/components/auth/RequireRole";
import { FullScreenLoader } from "@/components/common/PageSkeleton";
import { AdminLayout } from "@/components/layout/AdminLayout";
import { AppShell } from "@/components/layout/AppShell";
import { AuthProvider } from "@/hooks/useAuth";

/**
 * Route table (docs/04 §4).
 *
 * M2 adds the tool detail page, the personal centre and the approval console.
 * `/admin/*` beyond approvals / whitelist / settings stays out of the table
 * (M3, CONTRACT §6.1) so it falls through to the 404 page instead of a
 * half-built screen.
 *
 * Every page is code-split with `React.lazy` (docs/04 §9); the shells provide
 * Suspense boundaries.
 */
const LoginPage = lazy(() => import("@/pages/LoginPage"));
const ChangePasswordPage = lazy(() => import("@/pages/ChangePasswordPage"));
const PortalPage = lazy(() => import("@/pages/PortalPage"));
const ToolDetailPage = lazy(() => import("@/pages/ToolDetailPage"));
const NotFoundPage = lazy(() => import("@/pages/NotFoundPage"));

const ProfilePage = lazy(() => import("@/pages/me/ProfilePage"));
const MyToolsPage = lazy(() => import("@/pages/me/MyToolsPage"));
const ToolEditorPage = lazy(() => import("@/pages/me/ToolEditorPage"));
const VersionsPage = lazy(() => import("@/pages/me/VersionsPage"));

const AdminIndexPage = lazy(() => import("@/pages/admin/AdminIndexPage"));
const ApprovalsPage = lazy(() => import("@/pages/admin/ApprovalsPage"));
const ApprovalHistoryPage = lazy(() => import("@/pages/admin/ApprovalHistoryPage"));
const AdminToolsPage = lazy(() => import("@/pages/admin/AdminToolsPage"));
const CategoriesPage = lazy(() => import("@/pages/admin/CategoriesPage"));
const TagsPage = lazy(() => import("@/pages/admin/TagsPage"));
const UsersPage = lazy(() => import("@/pages/admin/UsersPage"));
const GroupsPage = lazy(() => import("@/pages/admin/GroupsPage"));
const WhitelistPage = lazy(() => import("@/pages/admin/WhitelistPage"));
const TokensPage = lazy(() => import("@/pages/admin/TokensPage"));
const SettingsPage = lazy(() => import("@/pages/admin/SettingsPage"));
const ImportExportPage = lazy(() => import("@/pages/admin/ImportExportPage"));
const RecycleBinPage = lazy(() => import("@/pages/admin/RecycleBinPage"));

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
        element: (
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        ),
        children: [
          { index: true, element: <PortalPage /> },
          { path: "tools/:slug", element: <ToolDetailPage /> },
          { path: "change-password", element: <ChangePasswordPage /> },

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

          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
]);
