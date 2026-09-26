"""安全响应头。

为什么这件事必须在**应用**里做，而不是只写在 nginx 配置里
--------------------------------------------------------------------------
生产拓扑有两种，二者都要能跑：

  ① nginx 反代（`deploy/nginx-localcraft-*.conf`）
  ② 直连 `IP:PORT`（后端自己托管前端产物，无 nginx）

第 ② 种没有任何前置组件，安全头只能由应用自己发。原先这套头只写在 nginx 里，
于是直连部署会**静默地**丢掉整整一层防护 —— nginx 的 `add_header` 有
「某个 location 一旦自己写了 add_header，就完全丢弃 server 级那批」的规则，
本身也很容易漏（现有配置里同一批头重复了 4 遍）。

CSP 里的脚本哈希为什么在**运行时**算
--------------------------------------------------------------------------
`web/index.html` 里有一个防主题闪烁的内联脚本，CSP 放行它需要一个 sha256。
这个哈希一旦写死就会随前端改动**静默过期**，而后果是浏览器直接拦掉该脚本。

本项目已经真实发生过一次：`deploy/nginx-localcraft-tls.conf` 里记的是
`sha256-sIS7ps8NKLNJRghvSw4Kg3MM3Cuo1LG6IOHN9pSfcsg=`，
而当前 `web/dist/index.html` 的内联脚本算出来是
`sha256-mYPiuzMIfPvMwrHpbRsc2iNjKDRwSn5hn45A1jbNJss=` —— 对不上。
（试过 strip / 含标签 / 归一化换行 / 去空白等多种口径，全都不匹配，
所以不是口径问题，是那个哈希真的过期了。）

运行时从构建产物现算，就不存在「过期哈希」这回事。
"""

from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

from starlette.datastructures import MutableHeaders

from app.core.config import settings

#: 匹配**内联** `<script>`（没有 `src=` 属性的）。`(?![^>]*\bsrc=)` 是负向先行断言，
#: 用来跳过 `<script type="module" src="/src/main.tsx">` 这类外链脚本。
_INLINE_SCRIPT_RE = re.compile(r"<script\b(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)

#: 与部署脚本各自独立维护的原因见模块 docstring —— 这里只放**不随构建变化**的部分。
#: 逐条对齐 `deploy/nginx-localcraft-tls.conf`，值原样照搬，不"顺手优化"。
_STATIC_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), usb=()",
    "Cross-Origin-Resource-Policy": "same-origin",
}

#: CSP 的固定部分；`script-src` 会拼上运行时算出的内联脚本哈希。
_CSP_DIRECTIVES: tuple[str, ...] = (
    "default-src 'self'",
    "img-src 'self' data: blob:",
    "style-src 'self' 'unsafe-inline'",  # Tailwind 运行时会注入内联样式
    "font-src 'self' data:",
    "connect-src 'self'",
    "frame-ancestors 'self'",
    "base-uri 'self'",
    "form-action 'self'",
)


@lru_cache(maxsize=8)
def _hashes_for(path_str: str, mtime_ns: int, size: int) -> tuple[str, ...]:
    """算出某个 `index.html` 里所有内联脚本的 CSP sha256。

    缓存键带上 `mtime_ns` 与 `size`：前端重新构建后文件必然变，缓存自动失效，
    不需要重启进程，也不需要谁记得手动清缓存。
    """
    del mtime_ns, size  # 仅作为缓存键使用
    try:
        html = Path(path_str).read_text(encoding="utf-8")
    except OSError:
        return ()
    out: list[str] = []
    for match in _INLINE_SCRIPT_RE.finditer(html):
        digest = hashlib.sha256(match.group(1).encode("utf-8")).digest()
        out.append("sha256-" + base64.b64encode(digest).decode("ascii"))
    return tuple(out)


def inline_script_hashes(index_html: Path | None = None) -> tuple[str, ...]:
    """`index_html` 里内联脚本的哈希；文件不存在（如后端单独起、前端没构建）时返回空。"""
    path = index_html if index_html is not None else settings.web_dist_dir / "index.html"
    try:
        st = path.stat()
    except OSError:
        return ()
    return _hashes_for(str(path), st.st_mtime_ns, st.st_size)


def build_csp(index_html: Path | None = None) -> str:
    """构造 Content-Security-Policy。

    注意哈希**必须加单引号**（`'sha256-…'`）。CSP 里不带引号的 token 是非法来源，
    浏览器会忽略它 —— 结果是内联脚本照样被拦，而且只在浏览器控制台留一行警告，
    从响应头上完全看不出问题（这个坑实际踩过一次）。

    算不出内联脚本哈希时（前端产物不在），`script-src` 退化为 `'self'` ——
    这是**安全侧**的退化：宁可拦掉内联脚本，也不要为了"能跑"而放开 `unsafe-inline`。
    这种情形下应用只是在提供 API，HTML 不由它托管，所以拦不到任何真实页面。
    """
    hashes = inline_script_hashes(index_html)
    script_src = " ".join(("'self'", *(f"'{h}'" for h in hashes)))
    return "; ".join((*_CSP_DIRECTIVES, f"script-src {script_src}"))


@lru_cache(maxsize=8)
def _csp_cached(path_str: str, mtime_ns: int, size: int) -> str:
    return build_csp(Path(path_str))


def csp_header() -> str:
    """当前构建产物对应的 CSP（带缓存，构建变化后自动失效）。"""
    index = settings.web_dist_dir / "index.html"
    try:
        st = index.stat()
    except OSError:
        # 前端没构建时（后端单独起）：CSP 仍返回，只是 script-src 退化为 'self'。
        # 这种情形下 HTML 不由应用托管，拦不到真实页面。
        return build_csp(None)
    return _csp_cached(str(index), st.st_mtime_ns, st.st_size)


#: 要补的响应头。`Content-Security-Policy` 单独处理（它是动态的）。
_HEADER_NAMES = (*_STATIC_HEADERS, "Content-Security-Policy")


class SecurityHeadersMiddleware:
    """给**所有**响应补上安全头，包括错误响应。

    为什么不用 `BaseHTTPMiddleware` + `add_middleware`：
    那种写法注册出来的中间件位于 `ServerErrorMiddleware` **内层**，
    一旦视图里抛出未捕获异常，500 响应由外层 ServerErrorMiddleware 直接生成，
    根本不会路过这里 —— 错误响应就成了唯一没有安全头的一类响应。
    nginx 的 `add_header ... always` 正是为了覆盖这种情况，语义要对齐。

    纯 ASGI 中间件在 `http.response.start` 上补头，无论响应是谁生成的都会经过，
    因此不需要在别的中间件之后再包一层。
    """

    def __init__(self, app: Callable) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _STATIC_HEADERS.items():
                    headers.setdefault(name, value)
                headers.setdefault("Content-Security-Policy", csp_header())
            await send(message)

        await self.app(scope, receive, send_with_headers)


__all__ = [
    "SecurityHeadersMiddleware",
    "build_csp",
    "inline_script_hashes",
]
