"""M4 公共辅助：解析 `docs/03-API接口清单.md` 的权威接口清单。

契约 §16.5 把「docs/03 §2.5 管理后台接口总表」定为权威清单，
守卫测试据此逐条比对，而不是维护一份人手抄写的常量 —— 那样抄错了也没人发现。
"""

from __future__ import annotations

import pathlib
import re

DOCS_03 = (
    pathlib.Path(__file__).resolve().parents[2] / "docs" / "03-API接口清单.md"
)

#: §2.5 总表**刻意未单列**的两个接口（docs/03 第 1099 行有明确说明）：
#: 「这两个接口在总表中未单列，属于 `/api/v1/admin/tools` 资源下的子操作，
#:   实现时一并提供。」所以比对时要显式把它们排除在「多余」之外。
DOCUMENTED_TABLE_OMISSIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/api/v1/admin/tools"),
        ("POST", "/api/v1/admin/tools/{id}/versions"),
    }
)


def normalized_omissions() -> set[tuple[str, str]]:
    """`DOCUMENTED_TABLE_OMISSIONS` 的归一化形式（比对时必须用这个）。"""
    return {(m, normalize_path(p)) for m, p in DOCUMENTED_TABLE_OMISSIONS}


def normalize_path(path: str) -> str:
    """把路径参数名归一化：`{tool_id}` 与 `{id}` 视为同一个位置参数。

    文档用 `{id}` / `{user_id}`，实现用 `{tool_id}` / `{category_id}` 等更具体的名字 ——
    这是**参数命名**差异，不是接口差异，比对时归一化掉。
    """
    return re.sub(r"\{[^}]*\}", "{}", path)


def parse_docs_03_section_2_5() -> list[tuple[str, str]]:
    """从 §2.5 管理后台接口总表解析出 `(METHOD, path)` 列表（保持文档顺序）。"""
    text = DOCS_03.read_text(encoding="utf-8")
    match = re.search(r"^### 2\.5\b.*?(?=^### |^## |\Z)", text, re.M | re.S)
    assert match is not None, "docs/03 里找不到 §2.5 管理后台接口总表"
    section = match.group(0)

    found: list[tuple[str, str]] = []
    for line in section.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        method = cells[0].upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            continue
        path = cells[1]
        if not path.startswith("/"):
            continue
        path = re.split(r"[\s（(]", path)[0]
        found.append((method, path))
    return found


def documented_admin_endpoints() -> set[tuple[str, str]]:
    """docs/03 §2.5 的管理侧接口（路径参数已归一化）。"""
    return {(m, normalize_path(p)) for m, p in parse_docs_03_section_2_5()}
