"""系统设置（key-value，value 为 JSON）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import utcnow
from app.db.base import Base


class SystemSetting(Base):
    """docs/02 §3.19 `system_settings`

    以 `key` 作主键（不用自增 id），因为设置项天然以 key 寻址，
    且迁移脚本需要幂等地 `INSERT OR IGNORE` 初始值。

    跨库 UPSERT：`INSERT INTO system_settings ... ON CONFLICT(key) DO UPDATE`
    在 SQLite 与 PG 语法一致，可复用。

    公开项（`is_public = true`）才可通过 `GET /api/v1/meta` 匿名下发
    （FR-CFG-03）。
    """

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    value_type: Mapped[str] = mapped_column(String(16), nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    updated_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
