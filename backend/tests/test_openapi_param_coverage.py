"""守卫：应用代码里**裸读**的查询参数，必须在 `openapi.json` 里有声明。

契约 §15.6 规定 `backend/openapi.json` 是接口形状的唯一权威。
但 FastAPI 只把**显式声明的函数参数**写进 spec —— 如果代码用
`request.query_params.get("x")` 裸读，`x` 就**不会**出现在 openapi 里：
「形状权威」上出现一个洞，任何按 openapi 生成的客户端都会漏掉这个参数。

这不是假想的风险，是 M18 收尾时实测到的两处：

  - `GET /api/v1/images/{image_id}` 的 `sig` —— 能力签名，`<img>` 标签**唯一**的鉴权途径
  - `GET /api/v1/tools/{slug}/download` 的 `ticket` —— 一次性下载票据

两者都在 `app/core/deps.py` 里裸读，于是都不在 openapi 里。前端之所以没出问题，
只是因为前端不依赖生成的客户端 —— 「权威」不完整却没人发现，正是这类洞的危险之处。

守卫分两层，缺一不可：

  1. **通用层** —— 扫 `app/` 下所有 `query_params.get("...")` 字面量，
     每个名字都必须出现在运行时 spec 的某个 query 参数里。
     将来新写一个裸读参数，CI 立刻变红。
  2. **定点层** —— `sig` / `ticket` 必须声明在**各自那个路由**上。
     只做第 1 层的话，把 `sig` 声明到别的路由上也能骗过它。

修法（不用猜）：把参数声明成 `Annotated[..., Query(...)]`，**优先声明在「读它的那个依赖」里**
（FastAPI 会把依赖声明的查询参数合并进使用它的 operation），然后用参数替换裸读 ——
让「读到的」与「声明的」成为同一个来源。`sig` 与 `ticket` 就是这么修的。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.main import app

#: `app/` 的绝对路径。只扫源码，不扫 tests / docs —— 本文件的 docstring 里
#: 就写着示例文本，扫进来会自指。
APP_DIR = Path(__file__).resolve().parent.parent / "app"

#: 匹配 `request.query_params.get("name")`；容忍单双引号与参数间的空白/换行。
_QUERY_READ_RE = re.compile(r"""query_params\.get\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']""")


def _bare_query_reads() -> dict[str, list[str]]:
    """返回 `{参数名: ["app/core/deps.py:391", ...]}`，即所有裸读点。"""
    found: dict[str, list[str]] = {}
    for path in sorted(APP_DIR.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            for name in _QUERY_READ_RE.findall(line):
                rel = path.relative_to(APP_DIR.parent).as_posix()
                found.setdefault(name, []).append(f"{rel}:{lineno}")
    return found


def _declared_query_params(spec: dict[str, Any]) -> set[str]:
    """运行时 spec 里**所有** operation 声明过的 query 参数名。"""
    names: set[str] = set()
    for path_item in spec.get("paths", {}).values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            for param in operation.get("parameters") or []:
                if param.get("in") == "query":
                    names.add(param["name"])
    return names


def test_guard_regex_still_matches_something() -> None:
    """扫描器自身的守卫：**正则失效时这条会红**，而不是让上面那条静默通过。

    「守卫静默失效」比「没有守卫」更糟 —— 它会让人以为有保护。
    """
    bare = _bare_query_reads()
    assert bare, (
        "在 app/ 下没扫到任何 `query_params.get(...)`。若这是真的，"
        "说明裸读已全部消除，请删掉本守卫与上面那条；若是正则/路径失效，请修它。"
    )
    # `variant` 是已知仍然存在的裸读点（它同时被路由显式声明，所以是合规的）。
    assert "variant" in bare, (
        f"已知的裸读点 `variant` 没被扫到，扫描器可能失效。实际扫到：{sorted(bare)}"
    )


def test_every_bare_query_param_is_declared_in_openapi() -> None:
    """裸读的每个查询参数，都必须在运行时 spec 里被声明过。"""
    bare = _bare_query_reads()
    declared = _declared_query_params(app.openapi())

    missing = {name: where for name, where in bare.items() if name not in declared}
    assert not missing, (
        "以下查询参数在代码里被裸读，但没有声明进 openapi（契约 §15.6 的形状权威）：\n"
        + "\n".join(
            f"  ?{name}  ← {', '.join(where)}" for name, where in sorted(missing.items())
        )
        + "\n\n修法：声明成 `Annotated[..., Query(...)]`，优先放在**读它的那个依赖**里，"
        "\n然后用参数替换裸读。改完重导出：cd backend && python -m app.cli export-openapi"
    )


def test_signature_and_ticket_are_declared_on_their_own_routes() -> None:
    """定点层：`sig` / `ticket` 必须声明在**对的路由**上，而不是随便哪个路由。"""

    def query_names(path: str) -> set[str]:
        operation = app.openapi()["paths"][path]["get"]
        return {
            param["name"]
            for param in operation.get("parameters") or []
            if param.get("in") == "query"
        }

    images = query_names("/api/v1/images/{image_id}")
    assert {"variant", "sig"} <= images, (
        f"图片接口缺少查询参数声明：{sorted({'variant', 'sig'} - images)}"
        "（`sig` 是 <img> 标签唯一的鉴权途径，必须在 spec 里可见）"
    )

    download = query_names("/api/v1/tools/{slug}/download")
    assert {"version_id", "ticket"} <= download, (
        f"下载接口缺少查询参数声明：{sorted({'version_id', 'ticket'} - download)}"
    )
