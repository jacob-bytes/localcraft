import type { Role, User } from "@/api/types";

/**
 * Frontend permission helpers. UI-only convenience — the server is the only
 * authority (docs/04 §4, docs/01 §3.3).
 */

export const ROLE_LABELS: Record<Role, string> = {
  viewer: "只读访客",
  user: "普通用户",
  approver: "审批管理员",
  superadmin: "超级管理员",
};

export function hasAnyRole(user: User | null, roles: Role[]): boolean {
  if (!user) return false;
  return user.roles.some((role) => roles.includes(role));
}

export function can(user: User | null, permission: string): boolean {
  if (!user) return false;
  return user.permissions.includes(permission);
}

/** Highest-privilege label for the top bar (acceptance #3). */
export function primaryRoleLabel(user: User | null): string {
  if (!user || user.roles.length === 0) return "";
  const order: Role[] = ["superadmin", "approver", "user", "viewer"];
  for (const role of order) {
    if (user.roles.includes(role)) return ROLE_LABELS[role];
  }
  const first = user.roles[0];
  return first ? ROLE_LABELS[first] : "";
}

export function canApprove(user: User | null): boolean {
  return hasAnyRole(user, ["approver", "superadmin"]);
}

/** Uploading/editing own tools is a `user`+ capability (docs/01 §3.2). */
export function canUpload(user: User | null): boolean {
  return hasAnyRole(user, ["user", "approver", "superadmin"]);
}
