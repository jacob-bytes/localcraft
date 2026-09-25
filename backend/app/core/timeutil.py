"""时间工具。

全库统一：**UTC 存储、UTC 序列化、ISO 8601 带 `Z`**（docs/02 §1.1、docs/03 §1.1）。

踩坑点：SQLite 没有原生时区类型，`DateTime(timezone=True)` 读回来的是
naive datetime。因此序列化时必须显式把 naive 值当作 UTC 处理并补上 `Z`，
否则前端 `new Date("2025-03-14T08:21:33")` 会按**本地时区**解析，时间整体偏移。
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """当前 UTC 时间（timezone-aware）。应用层统一用它填充时间戳。

    docs/02 §1.3：时间戳由应用层填充，不用数据库 default，
    以避免 SQLite/PG 默认值差异。
    """
    return datetime.now(UTC)


def ensure_utc(value: datetime | None) -> datetime | None:
    """把可能来自 SQLite 的 naive datetime 标记为 UTC。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def to_iso_z(value: datetime | None) -> str | None:
    """序列化为 `2025-03-14T08:21:33Z` 形式（契约 §5 时间约定）。"""
    if value is None:
        return None
    dt = ensure_utc(value)
    assert dt is not None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
