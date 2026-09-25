"""管理侧路由共用的守卫。

**角色与 Scope 必须一起校验**（这是 M2 就踩过的坑）：

  - 只查 Scope → `require_scope` 对 JWT 一律放行（角色由 `require_role` 负责），
    于是 viewer 也能进审批队列
  - 只查角色 → API Token 的 `roles` 是创建者的角色，会绕过 `scopes` 限制

所以统一走 `require_roles_and_scope`。

Scope 的分配依据 docs/03 §1.4 的「权限点与 Scope 映射」表：
  - 用户管理 → `users:write`（superadmin）
  - 用户组   → `groups:write`（superadmin）
  - 分类标签 → `taxonomy:write`（approver / superadmin）
  - 审批相关 → `approvals:write`（approver / superadmin）
  - 系统设置 / 免审白名单 / API Token → `settings:write`（superadmin）
  - 全站工具管理 / 批量导入导出 → `admin:all`（superadmin）
"""

from __future__ import annotations

from app.core.deps import require_roles_and_scope
from app.models.enums import ApiScope, RoleCode

_APPROVER_ROLES = (RoleCode.APPROVER.value, RoleCode.SUPERADMIN.value)
_ADMIN_ROLES = (RoleCode.SUPERADMIN.value,)

#: 用户管理（全部动作，含只读 —— docs/03 §1.4 把用户管理整体归到 users:write）
users_guard = require_roles_and_scope(*_ADMIN_ROLES, scope=ApiScope.USERS_WRITE.value)
#: 用户组管理
groups_guard = require_roles_and_scope(*_ADMIN_ROLES, scope=ApiScope.GROUPS_WRITE.value)
#: 分类与标签管理（approver 即可，docs/01 §3.2）
taxonomy_guard = require_roles_and_scope(*_APPROVER_ROLES, scope=ApiScope.TAXONOMY_WRITE.value)
#: 审批队列 / 审批历史 / 只读的治理视图
approver_guard = require_roles_and_scope(*_APPROVER_ROLES, scope=ApiScope.APPROVALS_WRITE.value)
#: 系统设置 / 免审白名单 / API Token
settings_guard = require_roles_and_scope(*_ADMIN_ROLES, scope=ApiScope.SETTINGS_WRITE.value)
#: 全站工具管理（含他人工具）、回收站、批量导入导出
admin_all_guard = require_roles_and_scope(*_ADMIN_ROLES, scope=ApiScope.ADMIN_ALL.value)
#: 系统角色列表（approver 可见，docs/03 §2.5）
roles_guard = require_roles_and_scope(*_APPROVER_ROLES, scope=ApiScope.APPROVALS_WRITE.value)

__all__ = [
    "admin_all_guard",
    "approver_guard",
    "groups_guard",
    "roles_guard",
    "settings_guard",
    "taxonomy_guard",
    "users_guard",
]
