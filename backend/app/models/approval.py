"""审批记录（不可变流水）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import utcnow
from app.db.base import Base


class ApprovalRecord(Base):
    """docs/02 §3.13 `approval_records`

    **这张表只插入，不更新，不删除。** 应用层不暴露任何修改接口。

    `actor_label` 冗余展示名是刻意的：用户改了显示名之后，历史记录必须
    还原当时的称呼，所以它存的是**快照**。
    """

    __tablename__ = "approval_records"
    __table_args__ = (
        Index("ix_approval_records_tool_created", "tool_id", "created_at"),
        Index("ix_approval_records_actor", "actor_id", "created_at"),
        Index("ix_approval_records_action_created", "action", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tool_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tools.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("tool_versions.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    to_status: Mapped[str] = mapped_column(String(24), nullable=False)
    version_from_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    version_to_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # actor_id 为 NULL 表示系统自动
    actor_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_label: Mapped[str] = mapped_column(String(128), nullable=False)
    is_automatic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auto_rule: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
