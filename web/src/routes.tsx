import { lazy, Suspense } from "react";
import { createBrowserRouter, Outlet } from "react-router-dom";

import { RequireAuth } from "@/components/auth/RequireAuth";
import { FullScreenLoader } from "@/components/common/PageSkeleton";
import { AppShell } from "@/components/layout/AppShell";
import { AuthProvider } from "@/hooks/useAuth";

/**
 * Route table (docs/04 §4). Only the M1 routes exist; `/me/*` and `/admin/*`
 * are M2/M3 and therefore fall through to the 404 page rather than to a
 * half-built screen (CONTRACT §12).
 *
 * Every page is code-split with `React.lazy` (docs/04 §9); the shell provides a
 * Suspense boundary for authenticated routes.
 */
const LoginPage = lazy(() => import("@/pages/LoginPage"));
const ChangePasswordPage = lazy(() => import("@/pages/ChangePasswordPage"));
const PortalPage = lazy(() => import("@/pages/PortalPage"));
const NotFoundPage = lazy(() => import("@/pages/NotFoundPage"));

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
          { path: "change-password", element: <ChangePasswordPage /> },
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
]);
