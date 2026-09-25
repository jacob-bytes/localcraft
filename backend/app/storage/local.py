"""本地文件系统存储实现。"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from app.core.config import settings
from app.storage.base import (
    PathNotAllowedError,
    StagedUpload,
    StoredFile,
    UploadTooLargeError,
    display_filename,
    sanitize_filename,
)

logger = logging.getLogger(__name__)

#: 受管数据根目录的相对前缀（docs/05 §13.6：所有文件访问必须落在 DATA_DIR/files 内）
FILES_PREFIX = "files"

#: 临时文件目录（相对 DATA_DIR）
TMP_DIR = "tmp"


class LocalStorage:
    """本地磁盘存储。

    目录结构（FR-FILE-05 / 任务书 §1）：

        {DATA_DIR}/files/tools/{tool_id}/{version_id}/{safe_name}
        {DATA_DIR}/files/images/{tool_id}/{uuid}.{ext}
        {DATA_DIR}/tmp/{uuid}.part            ← 临时区
    """

    def __init__(self, data_dir: Path | None = None) -> None:
        self._data_dir = Path(data_dir or settings.data_dir).resolve()

    # ---------- 基本属性 ----------
    @property
    def name(self) -> str:
        return "local"

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    @property
    def files_root(self) -> Path:
        return self._data_dir / FILES_PREFIX

    @property
    def tmp_dir(self) -> Path:
        return self._data_dir / TMP_DIR

    async def ensure_dirs(self) -> None:
        await asyncio.to_thread(self._ensure_dirs_sync)

    def _ensure_dirs_sync(self) -> None:
        for path in (self.files_root, self.tmp_dir):
            path.mkdir(parents=True, exist_ok=True)

    # ---------- 路径解析与穿越防护 ----------
    def _resolve_within_files(self, relative: str) -> Path:
        """相对路径 → 绝对路径，并强制落在 `DATA_DIR/files` 之内。

        用 `realpath` 归一化后再做前缀比较，这样 `../`、符号链接、
        绝对路径都无法逃逸（docs/05 §13.6）。
        """
        if not relative:
            raise PathNotAllowedError("空路径")
        candidate = (self._data_dir / relative).resolve()
        root = self.files_root.resolve()
        if candidate != root and root not in candidate.parents:
            raise PathNotAllowedError(f"路径越界: {relative}")
        return candidate

    def absolute(self, storage_path: str) -> Path:
        return self._resolve_within_files(storage_path)

    def exists(self, storage_path: str) -> bool:
        try:
            return self._resolve_within_files(storage_path).is_file()
        except PathNotAllowedError:
            return False

    # ---------- 写入 ----------
    async def stage(
        self,
        stream: AsyncIterator[bytes],
        *,
        max_bytes: int,
        file_name: str,
    ) -> StagedUpload:
        """流式写入临时文件，同时增量计算 SHA256，并在过程中强制大小上限。

        关键点：**不用 `Content-Length` 做判断**，只累计真实写出的字节数。
        超限时立刻抛错并删除临时文件 —— 不能等上传完再判断，那时攻击者
        已经吃掉了磁盘。
        """
        await self.ensure_dirs()
        safe_name = sanitize_filename(file_name)
        display_name = display_filename(file_name)
        temp_path = self.tmp_dir / f"{uuid.uuid4().hex}.part"

        hasher = hashlib.sha256()
        total = 0
        try:
            with temp_path.open("wb") as fp:
                async for chunk in stream:
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        # 立刻中断：关文件、删临时文件，不把剩余数据读完
                        raise UploadTooLargeError(limit_bytes=max_bytes, actual_bytes=total)
                    hasher.update(chunk)
                    fp.write(chunk)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise

        return StagedUpload(
            temp_path=temp_path,
            file_name=display_name,
            safe_name=safe_name,
            size=total,
            sha256=hasher.hexdigest(),
        )

    async def commit(
        self,
        staged: StagedUpload,
        *,
        directory: str,
        file_name: str | None = None,
    ) -> StoredFile:
        """把临时文件原子 rename 到最终目录。

        `os.replace()` 在同一文件系统内是原子的 —— 目标位置要么是完整的
        旧文件，要么是完整的新文件，不会出现半个文件被下载到。
        """
        target_dir = self._resolve_within_files(f"{FILES_PREFIX}/{directory}".replace("//", "/"))
        await asyncio.to_thread(target_dir.mkdir, parents=True, exist_ok=True)

        final_name = sanitize_filename(file_name or staged.file_name)
        target = target_dir / final_name
        # 同名冲突（同一版本重复上传同名文件）时加短哈希，避免覆盖别人
        if target.exists():
            stem, dot, ext = final_name.rpartition(".")
            suffix = uuid.uuid4().hex[:8]
            final_name = f"{stem}-{suffix}.{ext}" if dot else f"{final_name}-{suffix}"
            target = target_dir / final_name

        await asyncio.to_thread(os.replace, staged.temp_path, target)

        relative = str(target.relative_to(self._data_dir))
        return StoredFile(
            storage_path=relative,
            absolute_path=target,
            size=staged.size,
            sha256=staged.sha256,
        )

    async def write_bytes_atomic(
        self, data: bytes, *, directory: str, file_name: str
    ) -> StoredFile:
        """小文件便捷写入（图片用）。同样走 tmp + 原子 rename。"""

        async def _one_chunk() -> AsyncIterator[bytes]:
            yield data

        staged = await self.stage(_one_chunk(), max_bytes=len(data) + 1, file_name=file_name)
        return await self.commit(staged, directory=directory, file_name=file_name)

    # ---------- 删除 ----------
    async def delete(self, storage_path: str) -> bool:
        try:
            target = self._resolve_within_files(storage_path)
        except PathNotAllowedError:
            logger.warning("拒绝删除越界路径: %s", storage_path)
            return False
        if not target.is_file():
            return False
        try:
            await asyncio.to_thread(target.unlink)
        except OSError as exc:
            logger.warning("删除文件失败 %s: %s", storage_path, exc)
            return False
        self._prune_empty_parents(target.parent)
        return True

    def delete_sync(self, storage_path: str) -> bool:
        """同步删除，供「事务提交后触发的淘汰」使用（不阻塞事件循环之外）。"""
        try:
            target = self._resolve_within_files(storage_path)
        except PathNotAllowedError:
            return False
        if not target.is_file():
            return False
        try:
            target.unlink()
        except OSError as exc:
            logger.warning("删除文件失败 %s: %s", storage_path, exc)
            return False
        self._prune_empty_parents(target.parent)
        return True

    def _prune_empty_parents(self, directory: Path) -> None:
        """删掉因为淘汰版本而变空的目录，避免 files/tools/ 下堆积空目录。"""
        root = self.files_root.resolve()
        current = directory
        while current != root and root in current.parents:
            try:
                current.rmdir()  # 非空会抛 OSError，正好用来停止
            except OSError:
                return
            current = current.parent

    # ---------- 容量 ----------
    def usage_bytes(self, relative_dir: str) -> int:
        """统计某个目录下已用字节数（配额校验用）。"""
        try:
            base = self._resolve_within_files(relative_dir)
        except PathNotAllowedError:
            return 0
        if not base.exists():
            return 0
        total = 0
        for path in base.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
        return total

    def count_stale_temp_files(self, *, older_than_seconds: int = 3600) -> int:
        """统计残留的临时文件（排障与 GC 用）。"""
        import time

        if not self.tmp_dir.exists():
            return 0
        cutoff = time.time() - older_than_seconds
        return sum(
            1
            for path in self.tmp_dir.glob("*.part")
            if path.is_file() and path.stat().st_mtime < cutoff
        )
