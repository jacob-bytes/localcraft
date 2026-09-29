"""守卫：shell（含 workflow YAML 内的 shell 片段）里 `$VAR` 不能紧跟非 ASCII 字符。

**为什么需要这条守卫**：这不是风格洁癖，是一个会**静默破坏输出**的真实缺陷。

macOS 自带 bash 3.2.57 下实测：

    X=hello; echo "A: $X（全角括号）"      →  ��全角括号）        ← 变量和全角字符一起被吃掉
    X=hello; echo "B: ${X}（全角括号）"    →  hello（全角括号）   ← 正常

bash 在解析 `$X（` 时把多字节字符的字节当成了变量名的一部分，于是既没展开变量，
又把那个字符嚼碎了。bash 5 上不复现，所以**只在 macOS 本机暴露** ——
而本项目的开发机就是 macOS，部署目标是 openEuler（bash 5）。
也就是说：本机看着正常的脚本，可能一跑到目标机才好的那种"反向"问题；
更糟的是加了 `set -u` 时会直接 `unbound variable` 中止。

本项目已经因为这个踩了三次（`pre-delivery-checks.sh` 的 `$ps）`、
`preview.sh` 的 `$PREVIEW）`、`precheck.sh` 的 `$WEB_MODE（`），
所以用测试把它钉住，而不是靠"记得写花括号"。

修法永远是加花括号：`$X` → `${X}`。

## M14（contracts/CONTRACT.md §29.7）：扫描范围扩到 workflow YAML

原先只扫 `_SHELL_SUFFIXES = {".sh"}`，于是 `.github/workflows/*.yml` 里的
**shell 片段**（`run: |` 块）完全不在范围内 —— 而 CI 里恰好大量使用 `$VAR`
拼中文提示。**这是真实的漏检面**：同样的片段被复制进脚本就会踩坑
（CI runner 是 bash 5 不复现，本机 bash 3.2 才现形）。

**已知取舍（§29.7 明写）**：按**文本**扫 YAML 会把注释与中文说明也纳入，
可能命中「其实不是 shell」的地方。**这是可接受的** —— 本守卫的修法
（`$VAR` → `${VAR}`）在任何上下文里都无害，而漏检的代价是一个会在
bash 3.2 上崩掉的脚本。若确有误报，改文案或加花括号即可。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: `$VAR` 之后紧跟一个非 ASCII 字符（多字节字符的首字节 >= 0x80）
_BARE_VAR_BEFORE_MULTIBYTE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)(?=[^\x00-\x7F])")

#: 只看这些类型的文件：shell 脚本，以及会被当作 shell 执行的内嵌片段
_SHELL_SUFFIXES = {".sh"}
_SHELL_NAMES = {"preview.sh"}

#: M14（§29.7）：GitHub Actions workflow 的目录与后缀 —— 里面同样有 shell 片段
_WORKFLOW_DIR_PARTS = (".github", "workflows")
_WORKFLOW_SUFFIXES = {".yml", ".yaml"}


def is_scanned_file(rel_path: Path) -> bool:
    """该文件是否在守卫的扫描范围内（按**仓库相对路径**判断）。

    抽成独立函数是为了能被直接反向验证：`test_workflow_files_are_in_scope`
    会断言 `.github/workflows/ci.yml` 命中 —— 否则「扩了范围」这件事
    可能只是注释里的一句话，「守卫已补盲」就成了空转。
    """
    if rel_path.suffix in _SHELL_SUFFIXES or rel_path.name in _SHELL_NAMES:
        return True
    parts = rel_path.parts
    return (
        len(parts) >= 3
        and tuple(parts[:2]) == _WORKFLOW_DIR_PARTS
        and rel_path.suffix in _WORKFLOW_SUFFIXES
    )


def _tracked_scanned_files() -> list[Path]:
    """取 git 跟踪的 shell 脚本与 workflow YAML。

    用 `git ls-files` 而不是 `rglob`：只关心入库的文件，
    且用 `-z` + `core.quotepath=false` 正确处理中文路径。
    """
    out = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    files = []
    for name in filter(None, out.split("\0")):
        p = REPO_ROOT / name
        if is_scanned_file(Path(name)) and p.is_file():
            files.append(p)
    return files


def test_shell_files_were_found() -> None:
    """遍历器本身要可靠 —— 否则下面的守卫会变成空转。"""
    files = _tracked_scanned_files()
    shells = [f for f in files if f.suffix in _SHELL_SUFFIXES or f.name in _SHELL_NAMES]
    assert len(shells) >= 20, f"只找到 {len(shells)} 个 shell 脚本，遍历器可能失效"


def test_workflow_files_are_found() -> None:
    """M14（§29.7）：workflow YAML 必须**真的**进了扫描范围。

    与上一条同一个目的：防止「扩展范围」变成一句注释。
    """
    workflows = [
        f
        for f in _tracked_scanned_files()
        if tuple(f.relative_to(REPO_ROOT).parts[:2]) == _WORKFLOW_DIR_PARTS
    ]
    assert len(workflows) >= 1, (
        "没有扫到任何 .github/workflows/*.yml —— 扩展范围可能失效"
    )


def test_workflow_files_are_in_scope() -> None:
    """反向验证判定函数本身（不依赖仓库里恰好有什么文件）。"""
    assert is_scanned_file(Path(".github/workflows/ci.yml")) is True
    assert is_scanned_file(Path(".github/workflows/release.yaml")) is True
    assert is_scanned_file(Path("scripts/precheck.sh")) is True
    assert is_scanned_file(Path("preview.sh")) is True
    # 不在范围内：别处的 yml、workflows 目录下的非 YAML
    assert is_scanned_file(Path("docker-compose.yml")) is False
    assert is_scanned_file(Path(".github/workflows/notes.md")) is False
    assert is_scanned_file(Path("web/package.json")) is False


def test_no_bare_variable_before_multibyte_char() -> None:
    offenders: list[str] = []
    for path in _tracked_scanned_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:  # pragma: no cover - 仓库里不该有二进制 .sh
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in _BARE_VAR_BEFORE_MULTIBYTE.finditer(line):
                rel = path.relative_to(REPO_ROOT)
                offenders.append(f"{rel}:{lineno}: ${match.group(1)}… ← {line.strip()[:90]}")

    assert not offenders, (
        "以下位置 `$VAR` 紧跟非 ASCII 字符，bash 3.2（macOS 自带）会解析歪，"
        "加 `set -u` 时直接 unbound variable 中止。请改成 `${VAR}`：\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("snippet", ["$X（", "$ARM，", "$PERM）"])
def test_the_detector_actually_detects(snippet: str) -> None:
    """反向验证：探测器本身要能命中，否则上面那条测试可能是假绿。"""
    assert _BARE_VAR_BEFORE_MULTIBYTE.search(snippet), f"探测器漏掉了 {snippet!r}"
    assert not _BARE_VAR_BEFORE_MULTIBYTE.search("${X}（"), "花括号写法不该被判为违规"
    assert not _BARE_VAR_BEFORE_MULTIBYTE.search("$X = 1"), "后跟 ASCII 空格不该被判为违规"
