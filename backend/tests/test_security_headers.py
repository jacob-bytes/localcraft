"""安全响应头与 CSP 的守卫测试。

两块关注点：

1. **直连部署下安全头真的发出去了** —— 生产拓扑有两种（nginx 反代 / 直连 IP:PORT），
   直连时没有 nginx，这些头全靠应用自己发。
2. **CSP 里的脚本哈希不会过期** —— 这是本项目踩过的真实事故：
   `deploy/nginx-localcraft-tls.conf` 里写死的 sha256 与 `web/dist/index.html`
   实际算出来的对不上，一旦真挂上 nginx，浏览器会**拦掉**内联主题脚本。
   应用侧改为运行时现算，这里同时给 nginx 侧那份写死的哈希加一道守卫。
"""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.core import security_headers
from app.core.security_headers import (
    SecurityHeadersMiddleware,
    build_csp,
    csp_header,
    inline_script_hashes,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: 必须出现在**所有**响应上的头（CSP 单独断言，因为它是动态的）。
EXPECTED_STATIC = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "SAMEORIGIN",
    "referrer-policy": "strict-origin-when-cross-origin",
    "permissions-policy": "geolocation=(), microphone=(), camera=(), usb=()",
    "cross-origin-resource-policy": "same-origin",
}


# ---------------------------------------------------------------------------
# 纯函数层：哈希与 CSP 构造
# ---------------------------------------------------------------------------
def _sha256_csp(body: str) -> str:
    return "sha256-" + base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode()


def test_inline_script_hashes_only_covers_inline_scripts(tmp_path: Path) -> None:
    """外链 `<script src=...>` 不该被算进 CSP 哈希 —— 它们由 `'self'` 放行。"""
    html = tmp_path / "index.html"
    html.write_text(
        "<html><head>"
        "<script>var a = 1;</script>"
        '<script type="module" src="/assets/app.js"></script>'
        "<script>var b = 2;</script>"
        "</head></html>",
        encoding="utf-8",
    )
    hashes = inline_script_hashes(html)
    assert hashes == (_sha256_csp("var a = 1;"), _sha256_csp("var b = 2;"))


def test_inline_script_hashes_missing_file_returns_empty(tmp_path: Path) -> None:
    """前端没构建时不该抛异常 —— 后端单独起也要能跑。"""
    assert inline_script_hashes(tmp_path / "nope.html") == ()


def test_csp_quotes_the_script_hashes(tmp_path: Path) -> None:
    """CSP 里的哈希必须带单引号 —— 不带引号的 token 是非法来源，浏览器会忽略它。

    这个坑实际踩过一次：响应头里看着有 `sha256-…`，但浏览器照样拦掉内联脚本，
    只在控制台留一行警告，从响应头上完全看不出问题。
    所以断言必须**带引号**匹配，否则等于没测。
    """
    html = tmp_path / "index.html"
    html.write_text("<script>var a = 1;</script>", encoding="utf-8")
    csp = build_csp(html)

    bare = _sha256_csp("var a = 1;")
    assert f"'{bare}'" in csp, f"哈希没有带单引号：{csp}"

    # 逐个确认：CSP 里每一处 sha256-… 都被单引号包着
    for m in re.finditer(r"sha256-[A-Za-z0-9+/=]+", csp):
        before = csp[m.start() - 1] if m.start() > 0 else ""
        after = csp[m.end()] if m.end() < len(csp) else ""
        assert (before, after) == ("'", "'"), f"裸哈希（未加引号）出现在 CSP 里：{m.group(0)}"


def test_csp_falls_back_to_self_without_inline_hashes(tmp_path: Path) -> None:
    """算不出哈希时 `script-src` 退化为 `'self'`，**绝不能**退化成 `unsafe-inline`。"""
    csp = build_csp(tmp_path / "nope.html")
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp.split("script-src")[1]
    # style-src 允许 unsafe-inline 是刻意的（Tailwind 运行时会注入内联样式）
    assert "style-src 'self' 'unsafe-inline'" in csp


def test_hash_cache_invalidates_when_file_changes(tmp_path: Path) -> None:
    """缓存键带 mtime+size，前端重新构建后必须自动失效，不需要重启进程。"""
    html = tmp_path / "index.html"
    html.write_text("<script>var v = 1;</script>", encoding="utf-8")
    first = inline_script_hashes(html)
    assert first == (_sha256_csp("var v = 1;"),)

    # 长度也变，确保 mtime 精度不够时仍能失效
    html.write_text("<script>var v = 22222;</script>", encoding="utf-8")
    assert inline_script_hashes(html) == (_sha256_csp("var v = 22222;"),)


def test_inline_script_same_in_source_and_dist() -> None:
    """Vite 不改写那段内联脚本 —— 源码与产物逐字节相同。

    这条是后面几条守卫能在**没有 dist 的环境**（CI）里用源码代替产物的前提。
    哪天 Vite 开始改写它，这条会失败，提醒我们守卫的替代前提不成立了。
    """
    src = REPO_ROOT / "web" / "index.html"
    dist = REPO_ROOT / "web" / "dist" / "index.html"
    if not dist.is_file():
        pytest.skip("web/dist 不存在（前端未构建）")
    assert inline_script_hashes(src) == inline_script_hashes(dist)


def test_csp_matches_the_shipped_nginx_hash() -> None:
    """`deploy/nginx-localcraft-*.conf` 里写死的 CSP 哈希必须与前端内联脚本一致。

    nginx 直接读盘发 SPA 的 HTML（`root` + `try_files`），请求**不经过应用**，
    所以那条路径上的 CSP 只能写死在 nginx 里。写死就会过期 —— 这里守住它。

    **用 `web/index.html`（源文件）而不是 `web/dist/index.html`**：两者那段内联脚本
    逐字节相同（见上一条测试），而源文件在任何环境下都存在。这一点很关键 ——
    如果依赖 dist，这条守卫在 CI 上会被 skip，等于**没在 CI 里生效**。
    """
    actual = set(inline_script_hashes(REPO_ROOT / "web" / "index.html"))
    assert actual, "web/index.html 里应当有内联脚本（防主题闪烁那段）"

    confs = sorted((REPO_ROOT / "deploy").glob("nginx-localcraft*.conf"))
    assert confs, "找不到 nginx 配置"

    for conf in confs:
        text = conf.read_text(encoding="utf-8")
        for declared in re.findall(r"'sha256-[A-Za-z0-9+/=]+'", text):
            bare = declared.strip("'")
            assert bare in actual, (
                f"{conf.name} 里的 CSP 哈希已过期：{bare}\n"
                f"  实际应为：{sorted(actual)}\n"
                "  改前端内联脚本后必须同步这里的哈希（或直接用应用侧运行时计算的那份）"
            )


# ---------------------------------------------------------------------------
# 中间件层：所有响应都要带上
# ---------------------------------------------------------------------------
def _assert_headers(response: httpx.Response) -> None:
    for name, value in EXPECTED_STATIC.items():
        assert response.headers.get(name) == value, f"缺少或错误的响应头：{name}"
    assert "content-security-policy" in response.headers


async def test_headers_present_on_normal_and_error_responses() -> None:
    """应用**自己生成**的响应都要带头：正常 200 / 路由 404 / 领域错误。

    已知边界（刻意不覆盖）：由 `ServerErrorMiddleware` 生成的**未捕获异常 500**
    拿不到这批头 —— 它是中间件栈最外层，没有任何 `add_middleware` 注册的中间件
    能包住它。要覆盖就得把应用整体再包一层，并把 uvicorn 入口从
    `app.main:app` 改成别的名字，连带 systemd / preview.sh / 文档一起改。
    而 500 返回的是纯文本，CSP、X-Frame-Options 对它没有实际意义，
    收益近乎为零，所以**明确接受这个边界**。挂 nginx 时那条路径由
    `add_header ... always` 覆盖。

    下面用真实应用栈验证「应用自己生成的错误响应」确实带头 ——
    这是绝大多数错误响应的来源。
    """

    async def ok(_request):  # type: ignore[no-untyped-def]
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/ok", ok)])
    app.add_middleware(SecurityHeadersMiddleware)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        _assert_headers(await c.get("/ok"))
        _assert_headers(await c.get("/no-such-route"))  # 404


async def test_domain_errors_also_carry_headers(client) -> None:
    """领域错误（404/401 等由 ExceptionMiddleware 处理）必须带头。

    这批响应是「应用自己生成」的，会经过安全头中间件 —— 与上一条注释里
    那个 500 边界不同，这里没有任何豁免。
    """
    for path, expect in [
        ("/api/v1/definitely-not-a-route", 404),
        ("/api/v1/admin/users", 401),  # 需要鉴权
    ]:
        r = await client.get(path)
        assert r.status_code == expect, f"{path} 期望 {expect}，实际 {r.status_code}"
        _assert_headers(r)


async def test_app_responses_carry_security_headers(client) -> None:
    """真实应用上抽样验证：正常响应与错误响应都要带头。"""
    _assert_headers(await client.get("/healthz"))

    missing = await client.get("/api/v1/definitely-not-a-route")
    assert missing.status_code == 404
    _assert_headers(missing)


async def test_spa_entry_is_not_cacheable(tmp_path, monkeypatch) -> None:
    """index.html 不能长缓存：否则升级后旧入口引用已删除的 assets，页面白屏。

    **自建一个带 dist 的应用**而不是依赖仓库里的 `web/dist` ——
    否则 CI 上没有构建产物时这条会被 skip，`no-store` 就没人守了。
    """
    from fastapi import FastAPI

    from app.core.config import Settings
    from app.main import _mount_spa

    (tmp_path / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    monkeypatch.setattr(Settings, "web_dist_dir", property(lambda self: tmp_path))

    test_app = FastAPI()
    test_app.add_middleware(SecurityHeadersMiddleware)
    _mount_spa(test_app)

    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/some/frontend/route")  # 前端路由 → 回退 index.html
        assert r.status_code == 200
        assert "no-store" in (r.headers.get("cache-control") or ""), (
            f"SPA 入口必须 no-store，实际 {r.headers.get('cache-control')!r}"
        )
        _assert_headers(r)

        # 真实存在的散装静态文件（favicon 之类）可以缓存，不该被一并加上 no-store
        (tmp_path / "favicon.ico").write_bytes(b"\x00")
        icon = await c.get("/favicon.ico")
        assert icon.status_code == 200
        assert "no-store" not in (icon.headers.get("cache-control") or "")


def test_csp_header_is_cached_not_recomputed_per_call(tmp_path, monkeypatch) -> None:
    """缓存键带 mtime+size，重复调用不该重复读盘。

    **把 `web_dist_dir` 指到 tmp_path**，而不是依赖仓库里的 `web/dist`：
    产物不存在时 `csp_header()` 走的是"退化"分支（没有文件可 stat，也就没有缓存），
    在 CI 上会让断言变成 `currsize == 0` 而失败 —— 我第一版就是这么挂的。
    """
    from app.core.config import Settings

    (tmp_path / "index.html").write_text("<script>var a = 1;</script>", encoding="utf-8")
    monkeypatch.setattr(Settings, "web_dist_dir", property(lambda self: tmp_path))
    security_headers._csp_cached.cache_clear()

    first = csp_header()
    assert csp_header() == first
    info = security_headers._csp_cached.cache_info()
    assert info.currsize >= 1, "有产物时应当走缓存"
    assert info.hits >= 1, "第二次调用应命中缓存，而不是重新读盘"

    # 产物变化后必须失效（mtime+size 参与缓存键）
    (tmp_path / "index.html").write_text("<script>var a = 22222;</script>", encoding="utf-8")
    assert csp_header() != first, "构建产物变了，CSP 必须跟着变（缓存要失效）"
