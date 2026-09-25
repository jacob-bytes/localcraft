import * as React from "react";

import { FullScreenLoader } from "@/components/common/PageSkeleton";
import { ForbiddenPage } from "@/pages/ForbiddenPage";
import { useAuth } from "@/hooks/useAuth";
import type { Role } from "@/api/types";

/**
 * Role gate for UI only (docs/04 §4 — "路由守卫不是安全边界").
 * Insufficient roles render a 403 page; the server still returns 403 for the
 * underlying requests, which is what actually protects the data.
 */
export function RequireRole({
  roles,
  children,
}: {
  roles: Role[];
  children: React.ReactNode;
}) {
  const { status, hasRole } = useAuth();

  if (status === "unknown") {
    return <FullScreenLoader />;
  }

  if (!hasRole(roles)) {
    return <ForbiddenPage />;
  }

  return <>{children}</>;
}
