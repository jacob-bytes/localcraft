"""存储层接口。

抽象出 `StorageBackend` 是为了将来换对象存储（S3/OSS）时业务代码不动
（契约 §1 的目录组织里 `storage/` 就是为此留的）。

**两阶段写入**是本模块的核心设计：

    stage()  —— 边收边写、边算 SHA256，落在 `{DATA_DIR}/tmp/` 下
    commit() —— 校验全部通过后原子 rename 到最终位置

这样做的理由：
  - 数据库事务期间绝不能做大块文件 IO（README「SQLite 风险说明」第 3 条）
  - 但最终路径里的 `{version_id}` 要等插入 version 行之后才知道 ——
    所以「写盘」与「归位」必须能分开
  - 中途失败时只需要删掉一个 tmp 文件，不会留下半个「已归位」的产物
"""

from __future__ import annotations

import contextlib
import re
import unicodedata
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

#: 流式读写的分块大小（1 MiB）
CHUNK_SIZE = 1024 * 1024

#: 磁盘上安全文件名的最大长度（DB 列是 String(255)，留出目录与后缀余量）
MAX_SAFE_NAME_LENGTH = 200

#: 路径分隔符的各种写法：ASCII 正斜杠/反斜杠 + 全角变体
_PATH_SEPARATORS = ("/", "\\", "\u2215", "\uff0f", "\uff3c")

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class StorageError(Exception):
    """存储层错误基类。业务层据此转换成领域异常。"""


class UploadTooLargeError(StorageError):
    """实际写入字节数超过上限。

    这里**不是**判断 Content-Length —— 那个头可以伪造。我们累计的是
    真实写出的字节数，超限立刻中断并删除临时文件。
    """

    def __init__(self, limit_bytes: int, actual_bytes: int) -> None:
        super().__init__(f"上传超过上限：{actual_bytes} > {limit_bytes}")
        self.limit_bytes = limit_bytes
        self.actual_bytes = actual_bytes


class PathNotAllowedError(StorageError):
    """目标路径落在受管目录之外（路径穿越防护）。"""


def display_filename(raw: str, *, fallback: str = "download") -> str:
    """用于展示与 `Content-Disposition` 的文件名。

    只做「不会破坏 HTTP 头」的清理：去掉控制字符与路径分隔符。
    保留中文、空格、原有大小写 —— 用户看到的名字应该尽量保持原样。
    **这个值绝不用于拼磁盘路径。**
    """
    if not raw:
        return fallback
    name = unicodedata.normalize("NFC", raw)
    name = _CONTROL_CHARS.sub("", name)
    for sep in _PATH_SEPARATORS:
        name = name.replace(sep, "_")
    name = name.replace("..", "_").strip()
    return name[:255] or fallback


def sanitize_filename(raw: str, *, fallback: str = "upload.bin") -> str:
    """磁盘安全文件名（FR-FILE-05 / docs/05 §13.6）。

    剥离：路径分隔符（含全角变体）、`..`、空字节、控制字符、前后空白与点。
    保留扩展名，超长时只截断主干。
    """
    if not raw:
        return fallback

    name = unicodedata.normalize("NFC", raw)
    name = _CONTROL_CHARS.sub("", name)
    for sep in _PATH_SEPARATORS:
        name = name.replace(sep, "_")
    # 任何形式的 .. 都不能留在文件名里（含 ...、....）
    while ".." in name:
        name = name.replace("..", "_")
    name = name.strip().strip(".")

    if not name:
        return fallback

    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    # 扩展名异常长（例如有人塞了 300 个字符的「扩展名」）就直接当主干截断
    if len(ext) > 16:
        stem, ext = name, ""
    if len(stem) > MAX_SAFE_NAME_LENGTH:
        stem = stem[:MAX_SAFE_NAME_LENGTH]
    result = f"{stem}.{ext}" if ext else stem
    return result or fallback


def file_extension(name: str) -> str:
    """归一化扩展名（小写、不含点）。

    `archive.tar.gz` → `tar.gz`：多段扩展名在白名单里是独立一项（docs/02 §3.11
    的 `file_ext` 示例就写了 `tar.gz`）。
    """
    lowered = name.lower()
    for multi in (".tar.gz", ".tar.bz2", ".tar.xz"):
        if lowered.endswith(multi):
            return multi.lstrip(".")
    _, dot, ext = lowered.rpartition(".")
    return ext if dot and len(ext) <= 16 else ""


@dataclass(frozen=True)
class StagedUpload:
    """已完整落盘到临时区、尚未归位的上传产物。"""

    temp_path: Path
    file_name: str  # 展示用原始名（display_filename 清理过）
    safe_name: str  # 磁盘安全名
    size: int
    sha256: str

    def cleanup(self) -> None:
        """删除临时文件。幂等，失败只吞掉（由孤儿清理任务兜底）。"""
        with contextlib.suppress(OSError):
            self.temp_path.unlink(missing_ok=True)


@dataclass(frozen=True)
class StoredFile:
    """已归位、可写入数据库的存储结果。"""

    storage_path: str  # 相对 DATA_DIR 的路径，入库用
    absolute_path: Path
    size: int
    sha256: str


@runtime_checkable
class StorageBackend(Protocol):
    """文件存储接口。"""

    @property
    def name(self) -> str: ...

    async def stage(
        self, stream: AsyncIterator[bytes], *, max_bytes: int, file_name: str
    ) -> StagedUpload:
        """流式写入临时文件并增量计算 SHA256。

        **必须在写入过程中累计实际字节数并在超限时立刻中断** ——
        Content-Length 可以被伪造成很小的值，等收完再判断时磁盘已经满了。
        """
        ...

    async def commit(
        self, staged: StagedUpload, *, directory: str, file_name: str | None = None
    ) -> StoredFile:
        """把临时文件原子 rename 到 `directory/{safe_name}`。"""
        ...

    async def delete(self, storage_path: str) -> bool:
        """按相对路径删除文件。文件不存在返回 False，不抛异常。"""
        ...

    def absolute(self, storage_path: str) -> Path:
        """相对路径 → 绝对路径，并校验落在受管目录内（路径穿越防护）。"""
        ...

    def exists(self, storage_path: str) -> bool: ...

    async def ensure_dirs(self) -> None: ...
