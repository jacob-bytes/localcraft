"""Agent Skill 包解析（docs/01 §5.8 / docs/05 §13.5）。

**这是全平台最大的攻击面**，所以实现上有两个刻意的选择：

1. **完全不落盘**。不把 zip 解压到临时目录，而是在内存里分块流式读取每个条目。
   docs/05 §13.5 的实现要点原文就是「只提取需要的文件，不要整包解压落盘」，
   而且一旦落盘就有了「撑爆磁盘 / 耗尽 inode」的窗口。不落盘则这个窗口不存在 ——
   比「解压完再删」更强，因为压根没有可被撑爆的目标。

2. **边解压边校验，且不信任中央目录**。`ZipInfo.file_size` 是攻击者可控的
   （可以声明 1 KB、实际输出 1 GB），所以它只用于「早失败」的预检；
   真正的判定依据是**实际读出的字节数**与**磁盘上 zip 文件的真实大小**。
   压缩比用 `实际输出 / zip 文件在磁盘上的大小` 计算 —— 后者不可伪造。

任何安全违规都是**硬失败**（抛异常、不保存），不会「以草稿形式存下来」。
只有「结构合法但内容有问题」（如 frontmatter 不是合法 YAML）才是软失败，
允许存草稿但禁止提交审批（FR-TOOL-06）。
"""

from __future__ import annotations

import hashlib
import logging
import posixpath
import re
import stat
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.core.errors import (
    SkillMdNotFoundError,
    ZipBombDetectedError,
    ZipInvalidError,
    ZipPathTraversalError,
    ZipTooManyFilesError,
)

logger = logging.getLogger(__name__)

#: 流式读取的分块大小（1 MiB）
CHUNK_SIZE = 1024 * 1024

#: SKILL.md 正文入库上限（FR-SKILL-04：超长截断至 256 KB）
README_MAX_BYTES = 256 * 1024

#: 文件树入库条数上限。docs/02 §7 按 100 KB/行估算 skill 版本，
#: 5000 条会远超该量级，因此截断存储；真实条数由 file_count 保留。
FILE_TREE_MAX_ENTRIES = 2000

#: SKILL.md 的候选位置（FR-TOOL-06：根目录，或单层子目录下）
SKILL_MD_CANDIDATES = ("SKILL.md", "*/SKILL.md")

_FRONTMATTER_RE = re.compile(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", re.DOTALL)
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")

#: 文本类扩展名 —— 这些条目的正文可以被前端点击查看（FR-SKILL-07）
TEXT_EXTENSIONS = frozenset(
    {"md", "markdown", "txt", "json", "yaml", "yml", "toml", "ini", "cfg", "csv", "log"}
)


@dataclass(frozen=True)
class SkillLimits:
    """解压防护阈值（docs/05 §13.5）。"""

    max_total_size: int = 1024 * 1024 * 1024
    max_ratio: int = 200
    max_files: int = 5000
    max_depth: int = 16
    max_file_size: int = 200 * 1024 * 1024
    timeout_seconds: int = 60

    @classmethod
    def from_settings(cls, values: dict[str, int]) -> SkillLimits:
        return cls(
            max_total_size=int(values["max_total_size"]),
            max_ratio=int(values["max_ratio"]),
            max_files=int(values["max_files"]),
            max_depth=int(values["max_depth"]),
            max_file_size=int(values["max_file_size"]),
            timeout_seconds=int(values["timeout_seconds"]),
        )


@dataclass
class SkillParseResult:
    """解析产物。字段与 `tool_versions` 的 skill_* 列一一对应。"""

    manifest: dict[str, Any] | None = None
    readme_md: str | None = None
    file_tree: list[dict[str, Any]] = field(default_factory=list)
    parse_error: str | None = None
    file_count: int = 0
    total_size: int = 0
    max_depth: int = 0
    skill_md_path: str | None = None
    #: 目录条目数（文件树里 is_dir=True 的条数）。真实条目总数 = file_count + dir_count。
    dir_count: int = 0

    @property
    def file_tree_truncated(self) -> bool:
        return self.file_count + self.dir_count > len(self.file_tree)

    def tree_summary(self) -> dict[str, Any]:
        """docs/03 §3.4 的 `skill.file_tree_summary`。"""
        return {
            "file_count": self.file_count,
            "total_size": self.total_size,
            "max_depth": self.max_depth,
        }


def _normalize_entry_name(raw: str) -> str:
    """归一化 zip 条目名，任何逃逸企图直接抛错。

    拒绝：绝对路径（`/x`、`C:\\x`）、任何 `..` 路径段、空字节。
    """
    name = raw.replace("\\", "/")
    if "\x00" in name:
        raise ZipPathTraversalError(details={"entry": raw, "reason": "包含空字节"})
    if name.startswith("/"):
        raise ZipPathTraversalError(details={"entry": raw, "reason": "绝对路径"})
    if _WINDOWS_DRIVE_RE.match(name):
        raise ZipPathTraversalError(details={"entry": raw, "reason": "绝对路径（盘符）"})

    parts: list[str] = []
    for part in name.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise ZipPathTraversalError(details={"entry": raw, "reason": "包含 .. 路径段"})
        parts.append(part)
    return "/".join(parts)


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    """zip 条目是否声明为符号链接（Unix 模式位）。"""
    mode = info.external_attr >> 16
    return bool(mode) and stat.S_ISLNK(mode)


def _extension_of(path: str) -> str:
    _, dot, ext = path.rpartition(".")
    return ext.lower() if dot else ""


def parse_skill_package(zip_path: Path, *, limits: SkillLimits | None = None) -> SkillParseResult:
    """解析 Skill 包。**同步阻塞**，调用方用 `asyncio.to_thread` 包起来。

    抛出（硬失败，绝不落草稿）：
      - `ZipInvalidError`           不是合法 zip
      - `ZipTooManyFilesError`      条目数 / 深度超限
      - `ZipPathTraversalError`     绝对路径 / `..` / 符号链接
      - `ZipBombDetectedError`      解压后体积或压缩比超限
      - `SkillMdNotFoundError`      找不到 SKILL.md

    返回结果里的 `parse_error` 只为**软失败**设置（如 frontmatter 不是合法 YAML），
    这类版本允许存草稿，但禁止提交审批（FR-TOOL-06）。
    """
    limits = limits or SkillLimits()
    deadline = time.monotonic() + limits.timeout_seconds

    try:
        zip_size_on_disk = zip_path.stat().st_size
    except OSError as exc:
        raise ZipInvalidError(message="无法读取上传的压缩包") from exc
    if zip_size_on_disk <= 0:
        raise ZipInvalidError(message="压缩包为空")

    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ZipInvalidError(message="不是合法的 zip 文件") from exc

    with zf:
        infos = zf.infolist()

        # ------------------------------------------------------------------
        # 阶段一：条目名与结构校验（**在任何解压之前**完成）
        # ------------------------------------------------------------------
        entries: list[tuple[zipfile.ZipInfo, str]] = []
        for info in infos:
            normalized = _normalize_entry_name(info.filename)
            if not normalized:
                continue  # 纯目录项 "/" 之类，忽略
            if _is_symlink(info):
                raise ZipPathTraversalError(
                    details={"entry": info.filename, "reason": "符号链接条目"}
                )
            depth = len(normalized.split("/"))
            if depth > limits.max_depth:
                raise ZipTooManyFilesError(
                    message=f"目录深度超过上限（{limits.max_depth}）",
                    details={"entry": info.filename, "depth": depth, "limit": limits.max_depth},
                )
            entries.append((info, normalized))

        file_entries = [(info, name) for info, name in entries if not info.is_dir()]
        if len(file_entries) > limits.max_files:
            raise ZipTooManyFilesError(
                message=f"压缩包文件数超过上限（{limits.max_files}）",
                details={"limit": limits.max_files, "count": len(file_entries)},
            )

        # 预检：用**中央目录声明**的大小做早失败。
        # 这一步只为省时间，判定不依赖它 —— 声明值可以伪造。
        declared_total = sum(info.file_size for info, _ in file_entries)
        if declared_total > limits.max_total_size:
            raise ZipBombDetectedError(
                details={
                    "limit_mb": limits.max_total_size // (1024 * 1024),
                    "detected_mb": declared_total // (1024 * 1024),
                    "checked_entries": len(file_entries),
                    "stage": "declared",
                }
            )
        declared_ratio = declared_total / zip_size_on_disk
        if declared_ratio > limits.max_ratio:
            raise ZipBombDetectedError(
                details={
                    "limit_ratio": limits.max_ratio,
                    "detected_ratio": round(declared_ratio, 1),
                    "checked_entries": len(file_entries),
                    "stage": "declared",
                }
            )

        # ------------------------------------------------------------------
        # 阶段二：流式读取（不落盘），按**实际字节数**二次校验
        # ------------------------------------------------------------------
        skill_md_candidates: dict[str, str] = {}
        tree: list[dict[str, Any]] = []
        directories: set[str] = set()
        total_actual = 0
        max_depth = 0

        for index, (info, name) in enumerate(file_entries, start=1):
            if time.monotonic() > deadline:
                raise ZipBombDetectedError(
                    message=f"解压超过 {limits.timeout_seconds} 秒，已中止",
                    details={"checked_entries": index, "stage": "timeout"},
                )

            hasher = hashlib.sha256()
            entry_bytes = 0
            capture: bytearray | None = bytearray() if _should_capture(name) else None

            try:
                with zf.open(info) as source:
                    while True:
                        chunk = source.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        entry_bytes += len(chunk)
                        total_actual += len(chunk)

                        # ---- 单条目上限 ----
                        if entry_bytes > limits.max_file_size:
                            raise ZipBombDetectedError(
                                message=f"条目 {name} 解压后超过单文件上限",
                                details={
                                    "entry": name,
                                    "limit_mb": limits.max_file_size // (1024 * 1024),
                                    "detected_mb": entry_bytes // (1024 * 1024),
                                    "stage": "entry_size",
                                },
                            )
                        # ---- 总体积上限 ----
                        if total_actual > limits.max_total_size:
                            raise ZipBombDetectedError(
                                details={
                                    "limit_mb": limits.max_total_size // (1024 * 1024),
                                    "detected_mb": total_actual // (1024 * 1024),
                                    "checked_entries": index,
                                    "stage": "actual_size",
                                }
                            )
                        # ---- 压缩比（分母是磁盘上 zip 的真实大小，不可伪造）----
                        ratio = total_actual / zip_size_on_disk
                        if ratio > limits.max_ratio:
                            raise ZipBombDetectedError(
                                details={
                                    "limit_ratio": limits.max_ratio,
                                    "detected_ratio": round(ratio, 1),
                                    "checked_entries": index,
                                    "stage": "actual_ratio",
                                }
                            )

                        hasher.update(chunk)
                        if capture is not None and len(capture) < README_MAX_BYTES:
                            capture.extend(chunk[: README_MAX_BYTES - len(capture)])
            except zipfile.BadZipFile as exc:
                raise ZipInvalidError(message=f"条目 {name} 数据损坏") from exc

            depth = len(name.split("/"))
            max_depth = max(max_depth, depth)
            for parent in _parents(name):
                directories.add(parent)

            tree.append(
                {
                    "path": name,
                    "size": entry_bytes,
                    "is_dir": False,
                    "sha256": hasher.hexdigest(),
                }
            )

            if _is_skill_md_candidate(name):
                skill_md_candidates[name] = _decode_readme(bytes(capture or b""))

        # ------------------------------------------------------------------
        # 阶段三：定位 SKILL.md 并提取内容
        # ------------------------------------------------------------------
        skill_md_path = _pick_skill_md(skill_md_candidates)
        if skill_md_path is None:
            raise SkillMdNotFoundError(details={"searched": list(SKILL_MD_CANDIDATES)})

        readme_raw = skill_md_candidates[skill_md_path]
        manifest, body, parse_error = _split_frontmatter(readme_raw)

        # 目录项补进文件树（zip 不一定显式带目录条目）
        for directory in sorted(directories):
            tree.append({"path": directory, "size": 0, "is_dir": True, "sha256": None})
        tree.sort(key=lambda item: (item["path"], item["is_dir"]))

        truncated = len(tree) > FILE_TREE_MAX_ENTRIES
        stored_tree = tree[:FILE_TREE_MAX_ENTRIES]

        logger.info(
            "skill 包解析完成 path=%s files=%d total=%d depth=%d ratio=%.1f truncated=%s",
            skill_md_path,
            len(file_entries),
            total_actual,
            max_depth,
            total_actual / zip_size_on_disk,
            truncated,
        )

        result = SkillParseResult(
            manifest=manifest,
            readme_md=body,
            file_tree=stored_tree,
            parse_error=parse_error,
            file_count=len(file_entries),
            total_size=total_actual,
            max_depth=max_depth,
            skill_md_path=skill_md_path,
            dir_count=len(directories),
        )
        result.dir_count = len(directories)
        return result


def _decode_readme(raw: bytes) -> str:
    """把 SKILL.md 的字节解成文本，并保证**结果**不超过 README_MAX_BYTES。

    为什么不能只在上游按字节截断：`errors="replace"` 会把每个非法字节
    变成一个 3 字节的 U+FFFD。256 KB 的二进制内容解码后能膨胀到 475 KB
    （实测），直接突破了 fr-SKILL-04 的 256 KB 上限、也撑大了数据库行。
    所以这里按「编码后的字节数」再封一次顶。
    """
    text = raw.decode("utf-8", errors="replace")
    encoded = text.encode("utf-8")
    if len(encoded) <= README_MAX_BYTES:
        return text
    # 截断提示本身也占字节，必须从预算里扣掉，否则结果还是会超上限
    notice = "\n\n> （内容超过 256 KB，已截断）"
    budget = README_MAX_BYTES - len(notice.encode("utf-8"))
    # 按字节切可能切断多字节字符，用 errors="ignore" 丢掉尾部残片
    truncated = encoded[:budget].decode("utf-8", errors="ignore")
    return truncated + notice


def _parents(path: str) -> list[str]:
    """`a/b/c.txt` → `['a', 'a/b']`（不含自身）。"""
    result: list[str] = []
    current = posixpath.dirname(path)
    while current:
        result.append(current)
        current = posixpath.dirname(current)
    return result


def _should_capture(name: str) -> bool:
    return _is_skill_md_candidate(name) or _extension_of(name) in TEXT_EXTENSIONS


def _is_skill_md_candidate(name: str) -> bool:
    """根目录的 `SKILL.md`，或**单层**子目录下的 `SKILL.md`。"""
    if name == "SKILL.md":
        return True
    parts = name.split("/")
    return len(parts) == 2 and parts[1] == "SKILL.md"


def _pick_skill_md(candidates: dict[str, str]) -> str | None:
    """优先根目录，其次按路径字典序取第一个单层子目录里的 SKILL.md。

    固定优先级是为了让「子目录名不同」的包也有确定行为，而不是依赖 dict 顺序。
    """
    if "SKILL.md" in candidates:
        return "SKILL.md"
    nested = sorted(name for name in candidates if name != "SKILL.md")
    return nested[0] if nested else None


def _split_frontmatter(text: str) -> tuple[dict[str, Any] | None, str, str | None]:
    """拆出 YAML frontmatter 与正文。

    返回 `(manifest, body, parse_error)`。frontmatter 非法 YAML 属于**软失败**：
    manifest 为 None、parse_error 有值，但正文照常返回，允许存草稿。
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None, text, None

    body = text[match.end() :]
    raw_yaml = match.group(1)
    if not raw_yaml.strip():
        return None, body, None

    try:
        # 必须用 safe_load：yaml.load 会实例化任意 Python 对象
        loaded = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        return None, body, f"SKILL.md 的 YAML frontmatter 解析失败: {exc}"

    if loaded is None:
        return None, body, None
    if not isinstance(loaded, dict):
        return None, body, "SKILL.md 的 YAML frontmatter 不是键值对结构"
    # JSON 序列化要求键是字符串
    return {str(k): v for k, v in loaded.items()}, body, None


def read_zip_entry_text(
    zip_path: Path, entry: str, *, max_bytes: int = README_MAX_BYTES
) -> str | None:
    """按需读取包内某个文本条目（skill-preview 的「查看文件内容」用）。

    同样做了条目名归一化校验，不接受来自请求的任意路径。
    """
    try:
        normalized = _normalize_entry_name(entry)
    except Exception:
        return None
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                try:
                    if _normalize_entry_name(info.filename) != normalized:
                        continue
                except Exception:
                    continue
                with zf.open(info) as source:
                    return source.read(max_bytes).decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, OSError):
        return None
    return None


__all__ = [
    "FILE_TREE_MAX_ENTRIES",
    "README_MAX_BYTES",
    "SKILL_MD_CANDIDATES",
    "TEXT_EXTENSIONS",
    "SkillLimits",
    "SkillParseResult",
    "parse_skill_package",
    "read_zip_entry_text",
]
