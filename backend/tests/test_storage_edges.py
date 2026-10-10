"""`app/storage/local.py` 的**错误分支与边界**覆盖。

## 为什么单独一个文件

`storage/local.py` 是安全关键模块（路径穿越防护、淘汰时的物理删除、临时文件清理），
但它的错误分支长期没被测到：

  - 它是全仓安全模块里覆盖率最低的一个（83%，其余都在 90%+）；
  - 未覆盖的大块是**错误分支**：越界路径返回 `False`/`0`、`unlink` 失败、`stat` 失败。

这些分支恰恰是"出事时才会执行"的代码 —— 平时永远走不到，一旦走到就是磁盘/权限真出问题了。
所以这里用**故障注入**把它们逐个钉住，而不是等生产环境第一次执行它们。

（顺带：本文件也是 M18 之后清理 `delete_sync()` 死代码的配套 —— 那个函数没有任何调用方，
它的 13 行本身就是覆盖率缺口的大头。）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.storage import LocalStorage


def _storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(data_dir=tmp_path)


def test_data_dir_is_resolved(tmp_path: Path) -> None:
    """`data_dir` 属性必须返回 resolve 过的绝对路径（后续所有前缀比较都基于它）。"""
    storage = _storage(tmp_path)
    assert storage.data_dir == tmp_path.resolve()
    assert storage.files_root == tmp_path.resolve() / "files"


def test_exists_rejects_traversal(tmp_path: Path) -> None:
    """越界路径不是"不存在"，而是"不允许" —— 两种都要返回 False，但后者必须走 except 分支。"""
    storage = _storage(tmp_path)
    assert storage.exists("../../etc/passwd") is False
    assert storage.exists("files/../../etc/passwd") is False


def test_usage_bytes_rejects_traversal(tmp_path: Path) -> None:
    """配额统计遇到越界路径时返回 0（而不是抛异常把整个上传流程带崩）。"""
    storage = _storage(tmp_path)
    assert storage.usage_bytes("../../") == 0


async def test_usage_bytes_counts_only_existing_files(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    stored = await storage.write_bytes_atomic(b"0123456789", directory="images/1", file_name="a.png")

    assert storage.usage_bytes("files/images/1") == 10
    assert storage.usage_bytes("files/images/2") == 0, "目录不存在时应当是 0，而不是抛错"
    assert Path(stored.absolute_path).exists()


def test_usage_bytes_tolerates_stat_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """**故障注入**：`is_file()` 说在、`stat()` 却失败（权限/竞态）时，跳过该文件而不是崩掉。

    正常路径下走不到这里 —— 因为 `Path.is_file()` 内部会吞掉 `OSError` 并返回 False，
    于是根本到不了 `stat()`。要用故障注入才能钉住这条防御分支的存在意义：
    配额统计是"尽力而为"的，不能因为一个读不到的文件就让上传失败。
    """
    storage = _storage(tmp_path)
    target = storage.files_root / "images" / "1" / "ghost.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"12345")

    real_stat = Path.stat

    def fake_stat(self: Path, *args, **kwargs):
        if self.name == "ghost.png":
            raise OSError(13, "Permission denied")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.setattr(Path, "stat", fake_stat)

    assert storage.usage_bytes("files/images/1") == 0


async def test_delete_reports_failure_when_unlink_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**故障注入**：`unlink` 失败时返回 False 并记日志，不抛异常。

    调用方（版本淘汰）据此只记「文件已删除=False」并交给孤儿清理兜底，
    所以这里绝不能抛 —— 一抛就会把已经提交的审批结果变成 500。
    """
    storage = _storage(tmp_path)
    stored = await storage.write_bytes_atomic(b"payload", directory="tools/1/1", file_name="v.zip")

    def boom(self: Path, *args, **kwargs):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(Path, "unlink", boom)

    assert await storage.delete(stored.storage_path) is False
    assert Path(stored.absolute_path).exists(), "删除失败时文件应当还在（不是删了一半）"


async def test_stage_ignores_empty_chunks(tmp_path: Path) -> None:
    """上游分块读取可能给出空 chunk（`UploadFile.read()` 结束前的那次），必须跳过。"""
    storage = _storage(tmp_path)

    async def stream():
        yield b""
        yield b"abc"
        yield b""
        yield b"de"
        yield b""

    staged = await storage.stage(stream(), max_bytes=100, file_name="x.bin")
    assert staged.size == 5
    assert staged.temp_path.read_bytes() == b"abcde"


def test_count_stale_temp_files_without_tmp_dir(tmp_path: Path) -> None:
    """临时目录还不存在（全新数据目录）时返回 0 —— 它是排障/GC 用的，不该因为目录缺失就报错。"""
    storage = _storage(tmp_path)
    assert not storage.tmp_dir.exists()
    assert storage.count_stale_temp_files() == 0


def test_count_stale_temp_files_distinguishes_age(tmp_path: Path) -> None:
    """只有**超过阈值**的 `.part` 才算残留；刚写入的不算。"""
    import os
    import time

    storage = _storage(tmp_path)
    storage.tmp_dir.mkdir(parents=True, exist_ok=True)
    fresh = storage.tmp_dir / "fresh.part"
    stale = storage.tmp_dir / "stale.part"
    fresh.write_bytes(b"x")
    stale.write_bytes(b"x")
    old = time.time() - 7200
    os.utime(stale, (old, old))

    assert storage.count_stale_temp_files(older_than_seconds=3600) == 1
    assert storage.count_stale_temp_files(older_than_seconds=3600 * 3) == 0
