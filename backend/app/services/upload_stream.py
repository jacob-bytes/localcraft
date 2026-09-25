"""管理员/用户上传路径共用的流式辅助（M5 从 `api/v1/me.py` 提出来）。

为什么要提出来：`/admin/tools/{id}/versions` 与 `/me/tools/{id}/versions`
必须共用同一套落盘逻辑（契约 §18.4：「不要复制粘贴业务逻辑，委托同一个 service」）。
如果 admin 路由去 `from app.api.v1.me import _stream_upload`，就成了路由 import 路由 ——
会引出循环依赖，也让「路由层不该被别处复用」这条边界破掉。
放在 services 层，两条路由都向下依赖它。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import UploadFile

#: 流式读取块大小（1 MiB）
CHUNK_SIZE = 1024 * 1024


def settings_unzip_limits() -> dict[str, int]:
    """从环境配置取解压防护上限（供 `SkillLimits.from_settings` 使用）。"""
    from app.core.config import settings

    return settings.unzip_limits


async def stream_upload(upload: UploadFile) -> AsyncIterator[bytes]:
    """把 `UploadFile` 变成异步分块迭代器。

    这样 `storage.stage()` 可以边收边写边算哈希，**不会把整个文件读进内存**。
    """
    while True:
        chunk = await upload.read(CHUNK_SIZE)
        if not chunk:
            break
        yield chunk


__all__ = ["CHUNK_SIZE", "settings_unzip_limits", "stream_upload"]
