"""下载明细与每日统计汇总。"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import utcnow
from app.db.base import Base


class DownloadLog(Base):
    """docs/02 §3.17 `download_logs`

    写入策略：**批量插入**，应用侧维护内存队列，每 100 条或每 10 秒 flush
    一次（`bulk_insert_mappings`），不逐条提交 —— 规避 SQLite 写锁。
    M1 不产生下载，此表仅建结构。
    """

    __tablename__ = "download_logs"
    __table_args__ = (
        Index("ix_download_logs_tool_created", "tool_id", "created_at"),
        Index("ix_download_logs_user_created", "user_id", "created_at"),
        Index("ix_download_logs_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tool_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tools.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("tool_versions.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 区分人工下载与脚本下载
    via_api_token_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("api_tokens.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class ToolStatsDaily(Base):
    """docs/02 §3.18 `tool_stats_daily`

    写入方式：UPSERT（`INSERT ... ON CONFLICT(tool_id, stat_date) DO UPDATE`），
    SQLite 3.24+ 与 PG 9.5+ 语法一致，可跨库复用。M1 仅建结构。
    """

    __tablename__ = "tool_stats_daily"
    __table_args__ = (
        Index("uq_tool_stats_daily", "tool_id", "stat_date", unique=True),
        Index("ix_tool_stats_daily_date", "stat_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tool_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tools.id", ondelete="CASCADE"), nullable=False
    )
    stat_date: Mapped[date] = mapped_column(Date, nullable=False)
    views: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    downloads: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unique_visitors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
