"""FastAPI 应用装配。

顺序很关键（docs/03 §6.5）：

1. 异常处理器与中间件
2. `/healthz`、`/readyz`
3. `/api/v1` 路由
4. 静态资源（`web/dist/assets`）
5. **显式** SPA fallback（`/{full_path:path}`）

**不能用 `app.mount("/", StaticFiles(html=True))`** —— 那会让所有未匹配的 API
路径返回 `index.html`，前端 `JSON.parse` 直接炸（docs/03 §6.5 明确警告）。
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Match, Route
from starlette.types import Scope

from app.api.public import NON_SPA_PREFIXES
from app.api.v1 import api_router
from app.core.config import settings
from app.core.errors import DomainError, code_for_status, message_for_code
from app.core.logging import configure_logging
from app.core.request_id import resolve_request_id
from app.core.timeutil import utcnow
from app.db.session import SessionLocal, engine
from app.schemas.meta import HealthResponse, ReadyCheck, ReadyResponse
from app.services.counter_service import get_counter_service

configure_logging()
logger = logging.getLogger("app.request")


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """应用生命周期。

    起停**计数器后台任务**：它在后台每 30 秒把内存里的下载/浏览增量落库。

    关闭时（SIGTERM → uvicorn 优雅关闭 → lifespan 退出）必须 flush，
    否则最后一批计数会丢 —— 这是契约 §11 第 5 条「内存聚合批量落库」的
    配套要求，也是任务清单里的易错点第 18 条。
    """
    counters = get_counter_service()
    await counters.start()
    try:
        yield
    finally:
        # 放在 finally 里：即使应用启动后抛异常，也要把已累计的计数落库
        await counters.stop()
        await engine.dispose()


app = FastAPI(
    lifespan=lifespan,
    title="localcraft API",
    version=settings.localcraft_version,
    description="localcraft — 内网工具 / Skill 共享平台后端",
    # 生产默认关闭（SRS FR-API-07），运维可通过 API_DOCS_ENABLED 临时打开
    docs_url="/docs" if settings.api_docs_enabled else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.api_docs_enabled else None,
)


# ---------------------------------------------------------------------------
# 请求 ID + 访问日志中间件
# ---------------------------------------------------------------------------
class RequestContextMiddleware(BaseHTTPMiddleware):
    """读或生成 `X-Request-Id`，写响应头，并输出一条结构化访问日志。"""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = resolve_request_id(request.headers.get("X-Request-Id"))
        request.state.request_id = request_id
        request.state.user_id = None

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # 交给外层 ServerErrorMiddleware 的兜底处理器构造响应；
            # 这里只负责把失败也记进访问日志（否则 500 请求会「凭空消失」）。
            logger.exception(
                "请求处理异常",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": 500,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            raise

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers["X-Request-Id"] = request_id
        logger.info(
            "request",
            extra={
                "request_id": request_id,
                "user_id": getattr(request.state, "user_id", None),
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response


app.add_middleware(RequestContextMiddleware)


# ---------------------------------------------------------------------------
# 异常处理器
# ---------------------------------------------------------------------------
def _error_payload(
    code: str, message: str, details: dict[str, Any] | None, request_id: str
) -> dict[str, Any]:
    """所有非 2xx 的统一形状（契约 §4.3）。`details` 允许为 `null`。"""
    return {
        "code": code,
        "message": message,
        "details": details,
        "request_id": request_id,
    }


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "") or ""


@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    request_id = _request_id(request)
    return JSONResponse(
        status_code=exc.http_status,
        content=_error_payload(exc.code, exc.message, exc.details, request_id),
        headers={"X-Request-Id": request_id},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """把 Pydantic 的错误结构转成 `details.fields` 数组形状（docs/03 §4）。

    前端只需处理**一种**错误形状，不用同时兼容 Pydantic 原生结构。
    """
    request_id = _request_id(request)
    fields: list[dict[str, Any]] = []
    for error in exc.errors():
        loc = [str(part) for part in error.get("loc", ()) if part not in ("body", "query", "path")]
        fields.append(
            {
                "field": ".".join(loc) or "body",
                "message": error.get("msg", "参数不合法"),
            }
        )
    return JSONResponse(
        status_code=400,
        content=_error_payload(
            "VALIDATION_ERROR", "请求参数校验失败", {"fields": fields}, request_id
        ),
        headers={"X-Request-Id": request_id},
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """把框架自己抛出的 `HTTPException` 也收敛成契约 §4.3 的统一形状。

    不处理的话，未匹配的路径会返回 Starlette 默认的 `{"detail":"Not Found"}`，
    前端拿到的错误结构就和约定不一致了（本可以只处理 404，但那等于承认
    框架其他状态码可以漏出去）。

    映射规则只用 docs/03 §4 表里已有的码（`code_for_status`）。
    """
    request_id = _request_id(request)
    if exc.status_code in (307, 308) or 300 <= exc.status_code < 400:
        # 重定向类交给框架原样处理
        raise exc
    code = code_for_status(exc.status_code)
    # 一律用我们自己的中文文案：框架抛出的 HTTPException 只带
    # Starlette 的英文短语（"Not Found" / "Method Not Allowed"），
    # 直接透出去会让同一份错误信封出现中英混排。
    # 业务错误全部走 DomainError，不会经过这里。
    headers = dict(exc.headers or {})
    headers["X-Request-Id"] = request_id
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_payload(code, message_for_code(code), None, request_id),
        headers=headers,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """兜底：返回 `INTERNAL_ERROR` + `request_id`，**不把堆栈泄露到响应体**。

    完整异常写进日志（含 request_id），线上排障靠 journalctl 关联。
    """
    request_id = _request_id(request)
    logger.exception(
        "未捕获异常",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status": 500,
        },
    )
    details: dict[str, Any] | None = None
    if settings.localcraft_debug:
        details = {"exception": type(exc).__name__}
    return JSONResponse(
        status_code=500,
        content=_error_payload(
            "INTERNAL_ERROR", "服务器内部错误，请将 request_id 提供给管理员", details, request_id
        ),
        headers={"X-Request-Id": request_id},
    )


# ---------------------------------------------------------------------------
# 探针
# ---------------------------------------------------------------------------
@app.get("/healthz", response_model=HealthResponse, tags=["system"], summary="存活探针")
async def healthz() -> HealthResponse:
    """只回答「进程活着」，不碰数据库。"""
    return HealthResponse(status="ok")


async def _check_database() -> ReadyCheck:
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return ReadyCheck(name="database", ok=True, detail="SELECT 1 成功")
    except Exception as exc:  # pragma: no cover - 依赖环境
        return ReadyCheck(name="database", ok=False, detail=f"{type(exc).__name__}: {exc}")


def _check_data_dir() -> ReadyCheck:
    """`DATA_DIR` 可写性 —— 用真实写入探测，而不是只看 stat 权限位。

    只读挂载、磁盘满、SELinux 上下文不对都能被这一步发现。
    """
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        probe = settings.data_dir / ".readyz-probe"
        probe.write_text(utcnow().isoformat(), encoding="utf-8")
        probe.unlink(missing_ok=True)
        return ReadyCheck(name="storage", ok=True, detail=f"{settings.data_dir} 可写")
    except Exception as exc:
        return ReadyCheck(name="storage", ok=False, detail=f"{type(exc).__name__}: {exc}")


@app.get("/readyz", tags=["system"], summary="就绪探针")
async def readyz() -> Response:
    """真的探测：数据库 `SELECT 1` 能通 + `DATA_DIR` 可写。

    任一失败返回 **503**，这样 systemd 的 `ExecStartPost=wait-healthy.sh`
    与 nginx 的 upstream 摘除都能据此判断。
    """
    checks = [_check_data_dir(), await _check_database()]
    ok = all(c.ok for c in checks)
    payload = ReadyResponse(status="ok" if ok else "error", checks=checks)
    return JSONResponse(
        status_code=200 if ok else 503,
        content=payload.model_dump(mode="json"),
    )


# ---------------------------------------------------------------------------
# API 路由（必须在静态挂载之前）
# ---------------------------------------------------------------------------
app.include_router(api_router, prefix="/api/v1")


# ---------------------------------------------------------------------------
# 前端静态产物 + SPA fallback
# ---------------------------------------------------------------------------
class _SpaFallbackRoute(Route):
    """SPA 兜底路由：非 SPA 前缀（`api/`、`docs` 等）**不参与匹配**。

    Starlette 的路由匹配语义是：先找「路径 + 方法」都匹配的路由；全都匹配不上时，
    如果存在「路径匹配但方法不匹配」的路由，返回 **405**，否则 **404**。

    一个无差别的 `GET /{full_path:path}` 兜底路由会让**所有** GET 请求都在兜底处
    匹配成功，于是 API 的 405 再也没机会产生 —— 一个 `GET` 打到只支持
    `PATCH`/`DELETE` 的 `/api/v1/admin/groups/{id}` 会得到 404 而不是 405。

    要命的是这个差异**只在 `web/dist` 存在时才出现**：dist 不存在就不注册兜底路由，
    同一个请求于是返回 405。也就是说 API 的可观察行为取决于前端构建产物在不在，
    而 CI 上没有 dist、开发者本机通常有 —— 两边行为不一致，「本机全绿」是假的。

    在**匹配阶段**就排除非 SPA 前缀，状态码便与构建产物无关。未匹配的 API 路径
    仍然返回 JSON 404（docs/03 §6.5 的要求不变）。
    """

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        if scope["type"] == "http" and scope["path"].lstrip("/").startswith(NON_SPA_PREFIXES):
            return Match.NONE, {}
        return super().matches(scope)


def _mount_spa(application: FastAPI) -> None:
    """挂载 `web/dist`。

    `web/dist` 不存在时**优雅降级**：不挂任何静态路由，服务照常启动，
    未匹配路径返回 JSON 404。后端先行开发（前端还没构建）时不会启动失败。
    """
    web_dist = settings.web_dist_dir
    index_file = web_dist / "index.html"
    if not index_file.is_file():
        logger.warning(
            "web/dist 不存在或缺少 index.html，已跳过前端托管（仅提供 API）: %s", web_dist
        )
        return

    assets_dir = web_dist / "assets"
    if assets_dir.is_dir():
        application.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    # 静态根下的散装文件（favicon.ico、robots.txt 等）与前端路由兜底。
    # 为什么不能用 `@application.get(...)`：见 `_SpaFallbackRoute` 的 docstring。
    async def spa_fallback(request: Request) -> Response:
        full_path = request.path_params["full_path"]
        candidate = (web_dist / full_path).resolve()
        try:
            candidate.relative_to(web_dist.resolve())
        except ValueError:
            # 目录穿越尝试：当作前端路由，交给 index.html
            return FileResponse(index_file)
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index_file)

    application.router.routes.append(
        _SpaFallbackRoute(
            "/{full_path:path}",
            spa_fallback,
            methods=["GET"],
            name="spa",
            include_in_schema=False,
        )
    )


_mount_spa(app)
