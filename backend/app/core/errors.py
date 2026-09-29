"""领域异常与错误码。

**错误码与 HTTP 状态码逐条对应 docs/03 §4「错误码总表」，不得自行发明新码**
（契约 §4.3）。前端只处理这张表里列出的码。

每个异常四要素齐全：`code` / `http_status` / `message` / `details`。
"""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """所有业务异常的基类。"""

    code: str = "INTERNAL_ERROR"
    http_status: int = 500
    default_message: str = "服务器内部错误"

    #: 附加响应头（如 429 的 `Retry-After`）。默认无 —— 绝大多数业务错误
    #: 只需要状态码与错误信封。`app/main.py` 的处理器会把它合并到响应上。
    headers: dict[str, str] | None = None

    def __init__(
        self,
        message: str | None = None,
        details: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.details = details
        if headers:
            # 复制一份：异常实例可能被复用/缓存，不该让调用方之后改到响应头
            self.headers = dict(headers)
        super().__init__(self.message)


# ---------------------------------------------------------------------------
# 400 —— 参数校验失败
# ---------------------------------------------------------------------------
class ValidationError(DomainError):
    code = "VALIDATION_ERROR"
    http_status = 400
    default_message = "请求参数校验失败"

    def __init__(
        self,
        message: str | None = None,
        details: dict[str, Any] | None = None,
        fields: list[dict[str, Any]] | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        merged = dict(details or {})
        if fields is not None:
            merged["fields"] = fields
        super().__init__(message, merged or None, headers=headers)


class InvalidSortError(DomainError):
    code = "INVALID_SORT"
    http_status = 400
    default_message = "排序字段非法"


class AclRequiredError(DomainError):
    code = "ACL_REQUIRED"
    http_status = 400
    default_message = "restricted 可见性必须至少有一条授权"


class SubjectNotFoundError(DomainError):
    code = "SUBJECT_NOT_FOUND"
    http_status = 400
    default_message = "授权主体不存在"


class DuplicateEntryError(DomainError):
    code = "DUPLICATE_ENTRY"
    http_status = 400
    default_message = "条目重复"


class SelfGrantError(DomainError):
    code = "SELF_GRANT"
    http_status = 400
    default_message = "不能授权给自己"


class SettingInvalidError(DomainError):
    code = "SETTING_INVALID"
    http_status = 400
    default_message = "设置值非法"


class InvalidCsvError(DomainError):
    code = "INVALID_CSV"
    http_status = 400
    default_message = "CSV 格式错误"


# ---------------------------------------------------------------------------
# 401 —— 未认证 / 凭证失效
# ---------------------------------------------------------------------------
class UnauthenticatedError(DomainError):
    code = "UNAUTHENTICATED"
    http_status = 401
    default_message = "未携带有效凭证"


class InvalidCredentialsError(DomainError):
    """用户名错误与密码错误**共用**此错误码与同一句文案（契约 §3.3，防用户名枚举）。"""

    code = "INVALID_CREDENTIALS"
    http_status = 401
    default_message = "用户名或密码错误"


class TokenExpiredError(DomainError):
    code = "TOKEN_EXPIRED"
    http_status = 401
    default_message = "登录已过期"


class TokenRevokedError(DomainError):
    code = "TOKEN_REVOKED"
    http_status = 401
    default_message = "凭证已被吊销"


# ---------------------------------------------------------------------------
# 403 —— 已认证但无权限
# ---------------------------------------------------------------------------
class ForbiddenError(DomainError):
    code = "FORBIDDEN"
    http_status = 403
    default_message = "无权限执行该操作"


class ScopeMissingError(DomainError):
    code = "SCOPE_MISSING"
    http_status = 403
    default_message = "API Token 权限范围不足"

    def __init__(self, missing: list[str] | None = None) -> None:
        super().__init__(details={"missing_scopes": missing or []})


class AccountDisabledError(DomainError):
    code = "ACCOUNT_DISABLED"
    http_status = 403
    default_message = "账号已禁用，请联系管理员"


class PasswordChangeRequiredError(DomainError):
    code = "PASSWORD_CHANGE_REQUIRED"
    http_status = 403
    default_message = "请先修改初始密码"


# ---------------------------------------------------------------------------
# 404 / 423
# ---------------------------------------------------------------------------
class NotFoundError(DomainError):
    """资源不存在**或无权查看**（docs/03 §1.7：避免通过状态码探测资源存在性）。"""

    code = "NOT_FOUND"
    http_status = 404
    default_message = "资源不存在"


class AccountLockedError(DomainError):
    code = "ACCOUNT_LOCKED"
    http_status = 423
    default_message = "账号已锁定"

    def __init__(self, retry_after_seconds: int) -> None:
        minutes = max(1, -(-retry_after_seconds // 60))  # 向上取整
        super().__init__(
            message=f"账号已锁定，请 {minutes} 分钟后重试",
            details={"retry_after_seconds": int(retry_after_seconds)},
        )
        self.retry_after_seconds = int(retry_after_seconds)


# ---------------------------------------------------------------------------
# 409 —— 状态冲突
# ---------------------------------------------------------------------------
class VersionExistsError(DomainError):
    code = "VERSION_EXISTS"
    http_status = 409
    default_message = "版本号已存在"


class AlreadyProcessedError(DomainError):
    code = "ALREADY_PROCESSED"
    http_status = 409
    default_message = "该条目已被处理"


class LastSuperadminError(DomainError):
    code = "LAST_SUPERADMIN"
    http_status = 409
    default_message = "不能禁用或降级最后一个超级管理员"


class CategoryInUseError(DomainError):
    code = "CATEGORY_IN_USE"
    http_status = 409
    default_message = "分类仍被工具引用"


class GroupInUseError(DomainError):
    code = "GROUP_IN_USE"
    http_status = 409
    default_message = "用户组仍被可见性授权引用"


class ToolNotEditableError(DomainError):
    code = "TOOL_NOT_EDITABLE"
    http_status = 409
    default_message = "待审状态下不可编辑，请先撤回"


class DownloadNotAllowedError(DomainError):
    """J-2：ACL 明确把该用户标记为「可见但不可下载」。

    与 `NotFoundError` 的区别是刻意的：**这里要明确告诉用户为什么下不了**。
    对「无权看这个工具」我们用 404 隐藏资源存在性（FR-FILE-08），
    但用户既然能看到详情页、能看到下载按钮，再返回 404 只会让他困惑。
    """

    code = "DOWNLOAD_NOT_ALLOWED"
    http_status = 403
    default_message = "该工具的授权中未包含下载权限"


class StateConflictError(DomainError):
    code = "STATE_CONFLICT"
    http_status = 409
    default_message = "当前状态不允许该操作"


# ---------------------------------------------------------------------------
# 413 / 415 / 422
# ---------------------------------------------------------------------------
class PayloadTooLargeError(DomainError):
    code = "PAYLOAD_TOO_LARGE"
    http_status = 413
    default_message = "文件过大"


class UnsupportedMediaTypeError(DomainError):
    code = "UNSUPPORTED_MEDIA_TYPE"
    http_status = 415
    default_message = "文件类型不允许"


class SkillParseFailedError(DomainError):
    code = "SKILL_PARSE_FAILED"
    http_status = 422
    default_message = "Skill 包解析失败"


class SkillMdNotFoundError(DomainError):
    code = "SKILL_MD_NOT_FOUND"
    http_status = 422
    default_message = "未找到 SKILL.md"


class ZipBombDetectedError(DomainError):
    code = "ZIP_BOMB_DETECTED"
    http_status = 422
    default_message = "解压后体积超限"


class ZipPathTraversalError(DomainError):
    code = "ZIP_PATH_TRAVERSAL"
    http_status = 422
    default_message = "压缩包含非法路径条目"


class ZipTooManyFilesError(DomainError):
    code = "ZIP_TOO_MANY_FILES"
    http_status = 422
    default_message = "压缩包文件数超限"


class ZipInvalidError(DomainError):
    code = "ZIP_INVALID"
    http_status = 422
    default_message = "不是合法的 zip 文件"


# ---------------------------------------------------------------------------
# 429 / 500 / 503 / 507
# ---------------------------------------------------------------------------
class RateLimitedError(DomainError):
    code = "RATE_LIMITED"
    http_status = 429
    default_message = "请求过于频繁"


class InternalError(DomainError):
    code = "INTERNAL_ERROR"
    http_status = 500
    default_message = "服务器内部错误"


class StorageFullError(DomainError):
    code = "STORAGE_FULL"
    http_status = 503
    default_message = "磁盘空间不足"


class DatabaseUnavailableError(DomainError):
    code = "DATABASE_UNAVAILABLE"
    http_status = 503
    default_message = "数据库不可用"


class InsufficientStorageError(DomainError):
    code = "INSUFFICIENT_STORAGE"
    http_status = 507
    default_message = "平台配额耗尽"


class UserQuotaExceededError(DomainError):
    code = "USER_QUOTA_EXCEEDED"
    http_status = 507
    default_message = "用户配额耗尽"


# ---------------------------------------------------------------------------
# 文档化的错误码注册表 —— 测试用它断言「没有发明新码」
# ---------------------------------------------------------------------------
ERROR_REGISTRY: dict[str, tuple[int, str]] = {
    "VALIDATION_ERROR": (400, "参数校验失败"),
    "INVALID_SORT": (400, "排序字段非法"),
    "ACL_REQUIRED": (400, "restricted 但无授权条目"),
    "SUBJECT_NOT_FOUND": (400, "授权主体不存在"),
    "DUPLICATE_ENTRY": (400, "授权条目重复"),
    "SELF_GRANT": (400, "不能授权给自己"),
    "SETTING_INVALID": (400, "设置值非法"),
    "INVALID_CSV": (400, "CSV 格式错误"),
    "UNAUTHENTICATED": (401, "未携带凭证"),
    "INVALID_CREDENTIALS": (401, "用户名或密码错误"),
    "TOKEN_EXPIRED": (401, "access token 过期"),
    "TOKEN_REVOKED": (401, "Token 已吊销"),
    "FORBIDDEN": (403, "角色不足"),
    "SCOPE_MISSING": (403, "API Token Scope 不足"),
    "ACCOUNT_DISABLED": (403, "账号禁用"),
    "PASSWORD_CHANGE_REQUIRED": (403, "需先改密"),
    "NOT_FOUND": (404, "不存在或无权查看"),
    "ACCOUNT_LOCKED": (423, "账号锁定中"),
    "VERSION_EXISTS": (409, "版本号重复"),
    "ALREADY_PROCESSED": (409, "审批已被他人处理"),
    "LAST_SUPERADMIN": (409, "不能禁用最后一个超管"),
    "CATEGORY_IN_USE": (409, "分类被引用"),
    "GROUP_IN_USE": (409, "用户组被 ACL 引用"),
    "TOOL_NOT_EDITABLE": (409, "待审状态下不可编辑"),
    "STATE_CONFLICT": (409, "状态机不允许该操作"),
    "DOWNLOAD_NOT_ALLOWED": (403, "ACL 未授予下载权限"),
    "PAYLOAD_TOO_LARGE": (413, "文件过大"),
    "UNSUPPORTED_MEDIA_TYPE": (415, "文件类型不允许"),
    "SKILL_PARSE_FAILED": (422, "Skill 包解析失败"),
    "SKILL_MD_NOT_FOUND": (422, "未找到 SKILL.md"),
    "ZIP_BOMB_DETECTED": (422, "解压后体积超限"),
    "ZIP_PATH_TRAVERSAL": (422, "含非法路径条目"),
    "ZIP_TOO_MANY_FILES": (422, "文件数超限"),
    "ZIP_INVALID": (422, "不是合法 zip"),
    "INSUFFICIENT_STORAGE": (507, "平台配额耗尽"),
    "USER_QUOTA_EXCEEDED": (507, "用户配额耗尽"),
    "STORAGE_FULL": (503, "磁盘空间不足"),
    "DATABASE_UNAVAILABLE": (503, "数据库不可用"),
    "INTERNAL_ERROR": (500, "未预期异常"),
    "RATE_LIMITED": (429, "请求过频"),
}


def _build_default_messages() -> dict[str, str]:
    """从各 `DomainError` 子类收集 code → 默认文案。

    这样「HTTPException 转成统一信封」时能给出中文提示，
    而不是把 Starlette 的英文 `Not Found` 直接透出去。
    """
    messages: dict[str, str] = {}
    stack: list[type[DomainError]] = [DomainError]
    while stack:
        cls = stack.pop()
        stack.extend(cls.__subclasses__())
        if cls.code and cls.default_message:
            messages.setdefault(cls.code, cls.default_message)
    return messages


CODE_DEFAULT_MESSAGES: dict[str, str] = _build_default_messages()


#: HTTP 状态码 → 文档化错误码。用于把框架自己抛出的 `HTTPException`
#: 收敛成契约 §4.3 的形状。
#:
#: 关于 405：docs/03 §4 没有为 405 定义错误码，而契约禁止「自行发明新码」，
#: 因此复用语义最近的 `NOT_FOUND`（对该方法而言确实不存在可调用的端点），
#: HTTP 状态码仍保留 405。这是刻意的取舍，已在 checkpoint 报告中列出。
STATUS_TO_CODE: dict[int, str] = {
    400: "VALIDATION_ERROR",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "NOT_FOUND",
    409: "STATE_CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_ERROR",
    423: "ACCOUNT_LOCKED",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    503: "DATABASE_UNAVAILABLE",
    507: "INSUFFICIENT_STORAGE",
}


def code_for_status(status_code: int) -> str:
    """把任意 HTTP 状态码映射到文档化的错误码。"""
    if status_code in STATUS_TO_CODE:
        return STATUS_TO_CODE[status_code]
    # 不在表内的状态码：5xx 归为服务端异常，其余 4xx 归为资源不存在。
    # 仍然只用表里已有的码。
    return "INTERNAL_ERROR" if status_code >= 500 else "NOT_FOUND"


def message_for_code(code: str) -> str:
    return CODE_DEFAULT_MESSAGES.get(code) or ERROR_REGISTRY.get(code, (500, "服务器内部错误"))[1]
