"""用户、角色、用户组、API Token、登录会话、免审白名单。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.timeutil import utcnow
from app.db.base import Base
from app.models.enums import AuthSource, RoleCode, UserStatus


class User(Base):
    """docs/02 §3.1 `users`

    注意：`username` 创建后**不可修改**（docs/02 §3.1），因为它是审批记录与
    日志中的可读标识。`display_name` 与 `email` 不设唯一约束。
    """

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','disabled')",
            name="status",
        ),
        Index("ix_users_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=UserStatus.ACTIVE.value
    )
    auth_source: Mapped[str] = mapped_column(
        String(32), nullable=False, default=AuthSource.LOCAL.value
    )
    # 部分唯一索引：SQLite 与 PG 的唯一索引都允许多个 NULL，行为一致（docs/02 §3.1）
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    roles: Mapped[list[Role]] = relationship(
        secondary="user_roles",
        # user_roles 上有两个指向 users 的外键（user_id 与 granted_by_id），
        # 必须显式给出 join 条件，否则 SQLAlchemy 无法判定用哪一个。
        primaryjoin="User.id == UserRole.user_id",
        secondaryjoin="Role.id == UserRole.role_id",
        back_populates="users",
        lazy="selectin",
        viewonly=True,
    )

    # ---------- 状态便捷判定 ----------
    @property
    def is_active(self) -> bool:
        return self.status == UserStatus.ACTIVE.value

    @property
    def role_codes(self) -> list[str]:
        return sorted(r.code for r in self.roles)


class Role(Base):
    """docs/02 §3.2 `roles`

    四角色在 Alembic 迁移中以 `op.bulk_insert` 插入，保证任何环境初始状态一致。
    `permissions` 字段**不作为权限判定来源** —— 判定在 `app.core.permissions`
    里以 `frozenset` 常量定义，避免数据库被改后提权（docs/02 §3.2 的明确决策）。
    """

    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    permissions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    users: Mapped[list[User]] = relationship(
        secondary="user_roles",
        primaryjoin="Role.id == UserRole.role_id",
        secondaryjoin="User.id == UserRole.user_id",
        back_populates="roles",
        lazy="raise",
        viewonly=True,
    )


class UserRole(Base):
    """docs/02 §3.3 `user_roles` —— 复合主键 `(user_id, role_id)`"""

    __tablename__ = "user_roles"

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    granted_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class Group(Base):
    """docs/02 §3.4 `groups` —— 用户组仅用于可见性授权，不承载角色权限（FR-GRP-03）"""

    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class GroupMember(Base):
    """docs/02 §3.5 `group_members` —— 复合主键 `(group_id, user_id)`"""

    __tablename__ = "group_members"
    __table_args__ = (
        # 反查「我属于哪些组」是可见性判定的热点路径，必须有（docs/02 §3.5）
        Index("ix_group_members_user_id", "user_id"),
    )

    group_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    added_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class ApiToken(Base):
    """docs/02 §3.15 `api_tokens` —— M3 才签发，M1 只做鉴权侧解析"""

    __tablename__ = "api_tokens"
    __table_args__ = (Index("ix_api_tokens_created_by", "created_by_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_by_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    creator: Mapped[User] = relationship(foreign_keys=[created_by_id], lazy="joined")


class AuthSession(Base):
    """docs/02 §3.16 `auth_sessions`

    refresh token 需要服务端可吊销（FR-AUTH-07），所以必须落库。
    access token 是无状态 JWT，不落库。
    """

    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_user", "user_id"),
        Index("ix_auth_sessions_expires", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    refresh_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    user: Mapped[User] = relationship(lazy="joined")


class ApprovalWhitelist(Base):
    """docs/02 §3.14 `approval_whitelist` —— `user_id` 即主键（一个用户最多一条）"""

    __tablename__ = "approval_whitelist"

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    added_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


# 便于类型检查器解析上面的前向引用
__all__ = [
    "ApiToken",
    "ApprovalWhitelist",
    "AuthSession",
    "Group",
    "GroupMember",
    "Role",
    "RoleCode",
    "User",
    "UserRole",
]
