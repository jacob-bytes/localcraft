"""全部枚举，用 `enum.StrEnum`（Python 3.11 原生）定义一次。

**模型、Pydantic Schema、服务层共用这一份**，不在多处重复写字符串字面量
（docs/02 §8「实现建议」）。前端在 `types.ts` 里用字符串字面量联合类型镜像。

注意：数据库列类型是 `String(n)` + `CHECK` 约束，**不使用数据库原生 ENUM**
（docs/02 §1.1：SQLite 无原生 ENUM，PG 有，迁移麻烦）。
"""

from __future__ import annotations

from enum import StrEnum


class UserStatus(StrEnum):
    """docs/02 §3.1 `users.status`"""

    ACTIVE = "active"
    DISABLED = "disabled"


class AuthSource(StrEnum):
    """docs/02 §3.1 `users.auth_source`"""

    LOCAL = "local"
    OIDC = "oidc"


class RoleCode(StrEnum):
    """docs/02 §3.2 `roles.code` —— 四角色固定集合（docs/01 §3.1）"""

    SUPERADMIN = "superadmin"
    APPROVER = "approver"
    USER = "user"
    VIEWER = "viewer"


class ToolType(StrEnum):
    """docs/02 §3.8 `tools.tool_type`（D1：四种工具形态）"""

    FILE = "file"
    WEBAPP = "webapp"
    SKILL = "skill"
    PROMPT = "prompt"


class ToolVisibility(StrEnum):
    """docs/02 §3.8 `tools.visibility`（D5：三级可见性）"""

    PUBLIC = "public"
    RESTRICTED = "restricted"
    PRIVATE = "private"


class ToolStatus(StrEnum):
    """docs/02 §3.8 `tools.status`（docs/01 §4.1 工具状态机）"""

    DRAFT = "draft"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    PENDING_UPDATE = "pending_update"
    OFFLINE = "offline"


class VersionStatus(StrEnum):
    """docs/02 §3.11 `tool_versions.status`（docs/01 §4.2 版本状态机）"""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    PURGED = "purged"


class AclSubjectType(StrEnum):
    """docs/02 §3.10 `tool_acl.subject_type`"""

    USER = "user"
    GROUP = "group"


class ImageKind(StrEnum):
    """docs/02 §3.12 `tool_images.kind`"""

    COVER = "cover"
    SCREENSHOT = "screenshot"
    INLINE = "inline"


class ApprovalAction(StrEnum):
    """docs/02 §3.13 `approval_records.action`"""

    SUBMIT = "submit"
    WITHDRAW = "withdraw"
    RESUBMIT = "resubmit"
    APPROVE = "approve"
    REJECT = "reject"
    OFFLINE = "offline"
    RELIST = "relist"
    PURGE_VERSION = "purge_version"
    TRANSFER_OWNER = "transfer_owner"


class ApiScope(StrEnum):
    """docs/02 §8 `api_tokens.scopes[]`（docs/03 §1.4 权限点与 Scope 映射）"""

    TOOLS_READ = "tools:read"
    TOOLS_WRITE = "tools:write"
    APPROVALS_WRITE = "approvals:write"
    USERS_WRITE = "users:write"
    GROUPS_WRITE = "groups:write"
    TAXONOMY_WRITE = "taxonomy:write"
    SETTINGS_WRITE = "settings:write"
    ADMIN_ALL = "admin:all"


class ApprovalMode(StrEnum):
    """docs/02 §3.19 `system_settings["approval.mode"]`（D11）"""

    REQUIRE = "require"
    AUTO_APPROVE_ALL = "auto_approve_all"


class PortalSort(StrEnum):
    """门户列表排序白名单（docs/03 §3.3 `sort` 参数）。

    合法取值固定为这 4 个。非法值返回 400 `INVALID_SORT`，
    **绝不把 sort 直接拼进 SQL**（docs/03 §1.6）。
    """

    HOT = "hot"
    NEW = "new"
    NAME = "name"
    UPDATED_DESC = "-updated_at"


class SettingValueType(StrEnum):
    """docs/02 §3.19 `system_settings.value_type`"""

    BOOL = "bool"
    INT = "int"
    STRING = "string"
    JSON = "json"
    LIST = "list"
