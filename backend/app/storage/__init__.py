"""存储后端工厂。

业务代码只通过 `get_storage()` 拿接口，不直接依赖本地文件系统的细节 ——
将来换对象存储时只替换这里的实现。
"""

from __future__ import annotations

from functools import lru_cache

from app.storage.base import (
    MAX_SAFE_NAME_LENGTH,
    PathNotAllowedError,
    StagedUpload,
    StorageBackend,
    StorageError,
    StoredFile,
    UploadTooLargeError,
    display_filename,
    file_extension,
    sanitize_filename,
)
from app.storage.local import FILES_PREFIX, TMP_DIR, LocalStorage


@lru_cache
def get_storage() -> LocalStorage:
    return LocalStorage()


def reset_storage_cache() -> None:
    """测试里换了 DATA_DIR 之后需要清掉缓存的实例。"""
    get_storage.cache_clear()


__all__ = [
    "FILES_PREFIX",
    "MAX_SAFE_NAME_LENGTH",
    "TMP_DIR",
    "LocalStorage",
    "PathNotAllowedError",
    "StagedUpload",
    "StorageBackend",
    "StorageError",
    "StoredFile",
    "UploadTooLargeError",
    "display_filename",
    "file_extension",
    "get_storage",
    "reset_storage_cache",
    "sanitize_filename",
]
