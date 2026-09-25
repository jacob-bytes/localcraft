"""角色 → 权限点映射。

**这是权限判定的唯一来源**（docs/02 §3.2 的明确决策）：
`roles.permissions` 数据库字段只是冗余备份，**不作为鉴权依据** ——
否则数据库被改一行就能提权。此决策在此以注释形式固化。

权限点词汇表取自 docs/03 §1.4「权限点与 Scope 映射」，与 API Token 的
`scopes` 同名 —— 这样「JWT 走角色 / API Token 走 scopes，两者取交集」
（docs/03 §1.3）可以用同一套字符串做子集判定。
"""

from __future__ import annotations

from app.models.enums import ApiScope, RoleCode

# 下载能力单独一个权限点：docs/01 §3.2 权限矩阵里 viewer 可浏览但**不可下载**。
PERM_DOWNLOAD = "download"

#: 全部权限点
ALL_PERMISSIONS: frozenset[str] = frozenset(
    {scope.value for scope in ApiScope} | {PERM_DOWNLOAD}
)

#: 只读访客：只能浏览 public 工具，不能下载受限内容、不能上传（docs/01 §3.1）
_VIEWER: frozenset[str] = frozenset({ApiScope.TOOLS_READ.value})

#: 普通用户：浏览 + 下载 + 管理自己的工具
_USER: frozenset[str] = _VIEWER | {
    ApiScope.TOOLS_WRITE.value,
    PERM_DOWNLOAD,
}

#: 审批管理员：再加审批与分类/标签管理。**明确不给**用户管理与系统设置
#: （docs/01 §3.2「明确不给 approver 的权限」，职责分离）
_APPROVER: frozenset[str] = _USER | {
    ApiScope.APPROVALS_WRITE.value,
    ApiScope.TAXONOMY_WRITE.value,
}

#: 超级管理员：全部权限。`admin:all` 隐含全部其他 Scope（docs/03 §1.4）
_SUPERADMIN: frozenset[str] = ALL_PERMISSIONS


ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    RoleCode.VIEWER.value: _VIEWER,
    RoleCode.USER.value: _USER,
    RoleCode.APPROVER.value: _APPROVER,
    RoleCode.SUPERADMIN.value: _SUPERADMIN,
}


def permissions_for_roles(roles: frozenset[str] | set[str]) -> frozenset[str]:
    """角色集合 → 权限点并集（docs/01 §3.1：一个用户可拥有多个角色，权限取并集）。"""
    result: set[str] = set()
    for role in roles:
        result |= ROLE_PERMISSIONS.get(role, frozenset())
    return frozenset(result)


def has_any_role(roles: frozenset[str] | set[str], required: set[str]) -> bool:
    return bool(set(roles) & required)
