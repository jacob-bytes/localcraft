import { useMemo } from "react";

import type { Role } from "@/api/types";
import { useAuth } from "@/hooks/useAuth";
import { can as canPermission, hasAnyRole } from "@/lib/permissions";

/**
 * UI permission helper (docs/04 §7.8). Reads `permissions` / `roles` from the
 * session state restored by `/auth/refresh`.
 *
 * Reminder: this only decides whether to *render* something. The server is the
 * only authority (docs/04 §4).
 *
 * ```tsx
 * const { can } = usePermission();
 * {can("tool:create") && <Button>上传工具</Button>}
 * ```
 */
export function usePermission() {
  const { user } = useAuth();

  return useMemo(
    () => ({
      can: (permission: string) => canPermission(user, permission),
      hasRole: (roles: Role[]) => hasAnyRole(user, roles),
      permissions: user?.permissions ?? [],
      roles: user?.roles ?? [],
    }),
    [user],
  );
}
