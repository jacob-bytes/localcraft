"""用户与认证相关 Schema。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AuthSource
from app.schemas.common import OptionalUTCDateTime, ORMModel


class UserBrief(ORMModel):
    """工具卡片里的作者信息（docs/03 §3.3 `owner`）。"""

    id: int
    username: str
    display_name: str


class UserMe(ORMModel):
    """`user` 对象 —— 登录、refresh、`/auth/me` 都返回这个形状。

    契约 §3.3：`POST /auth/refresh` **也要返回 `user` 对象**，前端靠它恢复
    用户态，避免额外再调 `/auth/me`。
    """

    id: int
    username: str
    display_name: str
    email: str | None = None
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    must_change_password: bool
    auth_source: AuthSource
    status: str
    last_login_at: OptionalUTCDateTime = None
    created_at: OptionalUTCDateTime = None


class LoginRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {"username": "zhangsan", "password": "Str0ng!Passw0rd"}
        }
    )

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    """登录与 refresh 的统一成功响应（契约 §3.2）。"""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="access token 剩余有效秒数")
    user: UserMe


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class LoginField(BaseModel):
    name: str
    label: str
    type: str
    required: bool = True


class AuthProviderResponse(BaseModel):
    """`GET /api/v1/auth/provider`（FR-AUTH-09）——

    返回当前启用的认证方式与登录页需要渲染的字段，供前端适配 OIDC 等可插拔方式。

    `login_fields` 是**结构化字段描述**而不是字符串数组：FR-AUTH-09 要求前端按
    后端返回的字段动态渲染登录表单，将来接 OIDC 时不用改前端代码。
    `display_name` 是 `web/src/api/types.ts` 已声明的字段，这里补上以避免
    三方比对（docs/03 ↔ openapi.json ↔ types.ts）出现缺失项。
    """

    provider: AuthSource
    display_name: str
    login_fields: list[LoginField]
    password_change_supported: bool = True
    refresh_supported: bool = True
