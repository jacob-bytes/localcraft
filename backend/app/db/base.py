"""SQLAlchemy 2.0 声明式基类与命名约定。

命名约定照 docs/02 §1.2：
  表名小写复数下划线、索引 `ix_{表}_{字段}`、唯一 `uq_{表}_{字段}`、
  检查 `ck_{表}_{语义}`、外键 `fk_{表}_{字段}`。
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """全部 ORM 模型的基类。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        pk = getattr(self, "id", None)
        return f"<{type(self).__name__} id={pk}>"
