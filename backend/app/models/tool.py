"""工具及其版本、标签关联、可见性授权、图片。

**循环外键**：`tools.current_version_id` / `pending_version_id` / `cover_image_id`
与 `tool_versions` / `tool_images` 互为引用。docs/02 §2 要求
「`tools.current_version_id` 作为可空外键在 `ALTER TABLE` 中后加」，
因此这三个外键都带 `use_alter=True`，由 Alembic 在建表后单独
`create_foreign_key` 添加。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.timeutil import utcnow
from app.db.base import Base
from app.models.enums import (
    AclSubjectType,
    ImageKind,
    ToolStatus,
    ToolType,
    ToolVisibility,
    VersionStatus,
)


class Tool(Base):
    """docs/02 §3.8 `tools` —— 核心表

    软删除**只在此表使用**（`deleted_at`），其他表用物理删除或状态字段
    （docs/02 §1.3）。
    """

    __tablename__ = "tools"
    __table_args__ = (
        CheckConstraint(
            "tool_type IN ('file','webapp','skill','prompt')",
            name="type",
        ),
        CheckConstraint(
            "visibility IN ('public','restricted','private')",
            name="visibility",
        ),
        CheckConstraint(
            "status IN ('draft','pending','approved','rejected','pending_update','offline')",
            name="status",
        ),
        CheckConstraint(
            "tool_type <> 'webapp' OR webapp_url IS NOT NULL",
            name="webapp_url",
        ),
        # 门户列表主查询
        Index("ix_tools_status_visibility", "status", "visibility"),
        # 「我的工具」
        Index("ix_tools_owner_status", "owner_id", "status"),
        # 按分类筛选
        Index("ix_tools_category_status", "category_id", "status"),
        Index("ix_tools_deleted_at", "deleted_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    description_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tool_type: Mapped[str] = mapped_column(String(16), nullable=False)
    visibility: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ToolVisibility.PUBLIC.value
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ToolStatus.DRAFT.value
    )
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    category_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("categories.id", ondelete="RESTRICT"), nullable=True
    )
    # ---- 循环外键，use_alter=True：建表后由迁移单独 ADD CONSTRAINT ----
    current_version_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "tool_versions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_tools_current_version_id",
        ),
        nullable=True,
    )
    pending_version_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "tool_versions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_tools_pending_version_id",
        ),
        nullable=True,
    )
    cover_image_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "tool_images.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_tools_cover_image_id",
        ),
        nullable=True,
    )
    webapp_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    webapp_health_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    webapp_health_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    webapp_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 冗余计数：内存聚合后批量落库，禁止逐请求 UPDATE（README 第 4 条）
    download_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    view_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_version_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # reject_reason / offline_reason 是 approval_records 的冗余副本，
    # 只在状态变更的同一事务里由同一个服务方法同时写两处（docs/02 §3.8）
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    offline_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    version_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    owner: Mapped[User] = relationship(foreign_keys=[owner_id], lazy="joined")  # noqa: F821
    category: Mapped[Category | None] = relationship(lazy="joined")  # noqa: F821
    current_version: Mapped[ToolVersion | None] = relationship(
        foreign_keys=[current_version_id], lazy="joined", post_update=True
    )
    tags: Mapped[list[Tag]] = relationship(  # noqa: F821
        secondary="tool_tags", lazy="selectin", viewonly=True
    )


class ToolTag(Base):
    """docs/02 §3.9 `tool_tags` —— 复合主键 `(tool_id, tag_id)`"""

    __tablename__ = "tool_tags"
    __table_args__ = (Index("ix_tool_tags_tag_id", "tag_id"),)

    tool_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tools.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )


class ToolAcl(Base):
    """docs/02 §3.10 `tool_acl` —— 多态外键，`subject_id` 不建数据库级 FK"""

    __tablename__ = "tool_acl"
    __table_args__ = (
        Index("uq_tool_acl_subject", "tool_id", "subject_type", "subject_id", unique=True),
        # 可见性判定的关键索引
        Index("ix_tool_acl_subject", "subject_type", "subject_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tool_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tools.id", ondelete="CASCADE"), nullable=False
    )
    subject_type: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_id: Mapped[int] = mapped_column(Integer, nullable=False)
    can_download: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class ToolVersion(Base):
    """docs/02 §3.11 `tool_versions` —— 核心表

    `is_current` 与 `tools.current_version_id` 是同一事实的两份表示，
    一致性由**唯一部分索引**强制（迁移 0001 里手写 DDL）：
        CREATE UNIQUE INDEX uq_tool_versions_current ON tool_versions(tool_id)
        WHERE is_current = 1
    """

    __tablename__ = "tool_versions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','rejected','superseded','purged')",
            name="status",
        ),
        Index("uq_tool_versions_tool_version", "tool_id", "version", unique=True),
        Index("ix_tool_versions_tool_created", "tool_id", "created_at"),
        Index("ix_tool_versions_status", "status"),
        Index("ix_tool_versions_sha256", "file_sha256"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tool_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tools.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    changelog_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=VersionStatus.PENDING.value
    )
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    storage_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    file_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_ext: Mapped[str | None] = mapped_column(String(16), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    skill_readme_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    skill_manifest: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    skill_file_tree: Mapped[list | None] = mapped_column(JSON, nullable=True)
    skill_parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_by_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    auto_approved_rule: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class ToolImage(Base):
    """docs/02 §3.12 `tool_images`

    业务约束（应用层）：每个工具最多 1 张 `cover`、最多 8 张 `screenshot`。
    """

    __tablename__ = "tool_images"
    __table_args__ = (
        CheckConstraint("kind IN ('cover','screenshot','inline')", name="kind"),
        Index("ix_tool_images_tool_kind", "tool_id", "kind", "sort_order"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tool_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tools.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("tool_versions.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    thumb_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    alt_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uploaded_by_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


__all__ = [
    "AclSubjectType",
    "ImageKind",
    "Tool",
    "ToolAcl",
    "ToolImage",
    "ToolTag",
    "ToolType",
    "ToolVersion",
]
