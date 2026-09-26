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

/**
 * M9 · 收藏 / 点赞是 `user`+ 能力，**`viewer` 被刻意排除**（CONTRACT §25.1）。
 *
 * 权威在服务端：`backend/app/api/v1/tools.py` 的 `engagement_guard` 要求
 * `user` / `approver` / `superadmin` + `tools:write`，`viewer` 实测得 403。
 * 这里的判定只是「要不要渲染控件」的体验优化（docs/04 §4：前端不是安全边界）。
 *
 * 角色集合与 `canUpload` 恰好相同，但**语义不同**（投稿能力 vs 投票能力），所以
 * 单独命名：日后其中之一收窄时，不该悄悄牵连另一个。
 *
 * 注意用「有任一允许角色」而不是「等于 viewer」：多角色账号（如
 * `["viewer", "user"]`）在服务端是放行的，前端也必须放行。
 */
export function canEngage(user: User | null): boolean {
  return hasAnyRole(user, ["user", "approver", "superadmin"]);
}
