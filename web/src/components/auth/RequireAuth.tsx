import * as React from "react";
import { Navigate, useLocation } from "react-router-dom";

import { FullScreenLoader } from "@/components/common/PageSkeleton";
import { useAuth } from "@/hooks/useAuth";
import { safeRedirectPath } from "@/lib/navigation";

/**
 * Route guard for authenticated areas (contracts/CONTRACT.md §3.2 ②, docs/04 §4).
 *
 * Three states — the `unknown` one is what stops F5 from flashing the login
 * page (acceptance #4):
 *   unknown         → the refresh call that restores the session hasn't
 *                     answered yet → full-screen loader, no redirect
 *   authenticated   → children, unless a password change is mandatory
 *   unauthenticated → /login with ?redirect=…
 *
 * Not a security boundary: the API is the authority (docs/04 §4).
 */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { status, user } = useAuth();
  const location = useLocation();

  if (status === "unknown") {
    return <FullScreenLoader />;
  }

  if (status === "unauthenticated") {
    const current = `${location.pathname}${location.search}`;
    const target = safeRedirectPath(current, "/");
    const search = target === "/" ? "" : `?redirect=${encodeURIComponent(target)}`;
    return <Navigate to={`/login${search}`} replace />;
  }

  /* Forced password change blocks every other route (CONTRACT §3.2 ⑥). */
  if (user?.must_change_password && location.pathname !== "/change-password") {
    return <Navigate to="/change-password" replace />;
  }

  return <>{children}</>;
}
