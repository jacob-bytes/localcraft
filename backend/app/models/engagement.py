"""收藏与点赞关系表（M8 / contracts/CONTRACT.md §23.3）。

两张表结构**完全一致**，仅表名不同（契约 §23.3 ②）：

| 列 | 类型 | 约束 |
| --- | --- | --- |
| `id` | Integer | PK |
| `user_id` | Integer | NOT NULL, FK `users.id` ON DELETE CASCADE |
| `tool_id` | Integer | NOT NULL, FK `tools.id` ON DELETE CASCADE |
| `created_at` | DateTime(tz) | NOT NULL |

约束与索引：

- `UNIQUE(user_id, tool_id)` —— 每人每工具至多一条（D37：至多一票、可取消）。
  它同时是**并发重复 PUT 的兜底**：两个请求同时插入时，后到的那个撞唯一约束，
  服务层捕获后按「已收藏」处理，而不是变成 500（契约 §23.4）。
- `INDEX(user_id, created_at DESC)` —— 支撑「我的收藏」按收藏时间倒序分页。
- `INDEX(tool_id)` —— 支撑「收藏了某工具的人」与计数核对。

对应的反规范化计数列在 `tools.favorite_count` / `tools.like_count` 上
（契约 §23.3：禁止改为实时聚合，`docs/11` §2.2 记录了列表侧的性能约束）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, UniqueConstraint, desc
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import utcnow
from app.db.base import Base


class ToolFavorite(Base):
    """docs/02 之外的 M8 新增表 `tool_favorites`。"""

    __tablename__ = "tool_favorites"
    __table_args__ = (
        UniqueConstraint("user_id", "tool_id", name="uq_tool_favorites_user_tool"),
        Index("ix_tool_favorites_user_created", "user_id", desc("created_at")),
        Index("ix_tool_favorites_tool_id", "tool_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    tool_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tools.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class ToolLike(Base):
    """docs/02 之外的 M8 新增表 `tool_likes`。

    结构与 `ToolFavorite` **完全一致**（契约 §23.3 ②）—— 点赞的语义被 D37
    收窄为「每人每工具至多一票、可取消、只展示整数计数」，因此不需要评分列。
    """

    __tablename__ = "tool_likes"
    __table_args__ = (
        UniqueConstraint("user_id", "tool_id", name="uq_tool_likes_user_tool"),
        Index("ix_tool_likes_user_created", "user_id", desc("created_at")),
        Index("ix_tool_likes_tool_id", "tool_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    tool_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tools.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


__all__ = ["ToolFavorite", "ToolLike"]
