"""图片能力签名 URL 的构造与 TTL 解析（契约 §14.3）。

为什么单独成模块：`cover_url` / `thumb_url` 的下发点有 5 处（门户列表、门户详情、
管理侧列表、管理侧单项、`/me` 的图片接口），而签名 TTL 来自系统设置。
把「读 TTL」与「拼 URL」集中在一处，避免 5 个地方各写一遍 `f"...?sig={...}"`。

TTL 的读取做了一层 **60 秒进程内缓存**：
  - 这个值几乎不变（默认 168 小时），但每个构造 URL 的请求都要用
  - 缓存过期只是让新签名的有效期晚 60 秒生效，**不影响正确性**：
    每个签名把 `exp` 编进了 URL 自身，校验时比的是 URL 里的 exp，不是当前设置
  - 缓存的失效条件是**时间**（60 秒）与**进程重启**；不按写入失效是刻意的，
    少一次跨模块耦合，代价最多 60 秒的延迟生效
"""

from __future__ import annotations

import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    IMAGE_SIGNATURE_DEFAULT_TTL_HOURS,
    image_signature,
)
from app.repositories import system_settings as settings_repo

#: 设置键（默认 168 小时 = 7 天）
TTL_SETTING_KEY = "images.signature_ttl_hours"

#: 进程内缓存的存活秒数
_TTL_CACHE_SECONDS = 60
_cache: tuple[float, int] | None = None


def _reset_cache_for_tests() -> None:
    """仅供测试使用：清掉 TTL 缓存。"""
    global _cache
    _cache = None


async def get_ttl_hours(session: AsyncSession) -> int:
    """取图片签名有效期（小时）。带 60 秒进程内缓存。"""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < _TTL_CACHE_SECONDS:
        return _cache[1]
    value = await settings_repo.get_effective_int(
        session, TTL_SETTING_KEY, IMAGE_SIGNATURE_DEFAULT_TTL_HOURS
    )
    # 防御：设置项被写成 0 或负数时，签名会立即失效（页面全裂）——
    # 回落到默认值比让整站图片挂掉更好。SETTING_SPECS 里也限制了范围，这是第二道。
    if value <= 0:
        value = IMAGE_SIGNATURE_DEFAULT_TTL_HOURS
    _cache = (now, value)
    return value


def image_url(image_id: int, variant: str, *, ttl_hours: int) -> str:
    """带能力签名的图片 URL。

    形如 `/api/v1/images/88?variant=thumb&sig=<b64url>.<exp>`。
    前端**原样使用**这个 URL（只做 onError 占位降级），不需要任何改动。
    """
    sig = image_signature(image_id, variant, ttl_hours=ttl_hours)
    if variant == "full":
        return f"/api/v1/images/{image_id}?variant=full&sig={sig}"
    return f"/api/v1/images/{image_id}?variant={variant}&sig={sig}"


def cover_url_for(cover_image_id: int | None, *, ttl_hours: int) -> str | None:
    """工具封面（缩略图）的签名 URL；没有封面则 None。"""
    if not cover_image_id:
        return None
    return image_url(cover_image_id, "thumb", ttl_hours=ttl_hours)


__all__ = [
    "TTL_SETTING_KEY",
    "cover_url_for",
    "get_ttl_hours",
    "image_url",
]
