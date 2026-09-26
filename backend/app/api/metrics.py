"""`/metrics` 端点（Prometheus 文本格式，docs/11 §3.4 **M1**）。

六条硬要求（任务书 B3 的监控方裁定）逐条落点
--------------------------------------------

1. **位于 `/api/v1` 冻结面之外** —— 路径就是 `/metrics`，不是 `/api/v1/metrics`，
   因此不计入 93 operations / 75 paths 的冻结基线。
2. **`include_in_schema=False`** —— 不出现在 `backend/openapi.json` 里。
   理由：前端 `check:api-types` 读 `openapi.json` 生成类型，
   把运维端点塞进去会制造一次无意义的前后端耦合。
   `tests/test_metrics.py::test_metrics_not_in_openapi` 钉住这一条。
3. **默认关闭** —— 只有 `LOCALCRAFT_METRICS_ENABLED=true` 时 `main.py` 才注册。
4. **默认不可匿名读** —— `Authorization: Bearer <LOCALCRAFT_METRICS_TOKEN>`
   或来源为 `127.0.0.1`/`::1`。**未配置 token 且非本机来源时 401**，绝不裸奔。
5. **与全局鉴权共存** —— 本项目**没有**强制认证中间件（鉴权是每路由的依赖，
   见 `app/core/deps.py` 的设计说明）。因此本模块自带依赖，不会被别的层拦成 401，
   也不会因为漏挂依赖而变成公开端点。`tests/test_metrics.py` 用真实的四种来源
   组合验证状态码，而不是只读代码。
6. **不引入 Prometheus 客户端依赖** —— 文本格式手写在 `app/core/metrics.py`。
"""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import PlainTextResponse

from app.core.config import settings
from app.core.errors import UnauthenticatedError
from app.core.metrics import render_prometheus
from app.core.sql_counter import counting_enabled
from app.db.session import engine

logger = logging.getLogger(__name__)

router = APIRouter()

#: 视为「本机」的来源地址。uvicorn 以 `--proxy-headers --forwarded-allow-ips=127.0.0.1`
#: 启动（docs/05 §6.2），因此经 nginx 反代来的请求，`request.client.host` 是
#: **真实客户端 IP** 而不是 127.0.0.1 —— 这正是我们要的：反代来源不算本机。
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


def _bearer(request: Request) -> str | None:
    header = request.headers.get("Authorization")
    if not header:
        return None
    value = header.strip()
    if not value.lower().startswith("bearer "):
        return None
    token = value[len("bearer ") :].strip()
    return token or None


def metrics_access(request: Request) -> None:
    """`/metrics` 的准入依赖。

    规则（严格按任务书 B3 第 4 条）：

    - 配置了 `LOCALCRAFT_METRICS_TOKEN`：必须带 Bearer 且**常量时间比较**
      （`hmac.compare_digest`，避免用响应时间一位一位地试出 token）
    - 未配置 token：只有本机来源放行
    - 两者都不成立：**401**。选 401 而不是 404 的理由：
      本端点默认根本不注册（关闭时是 404，与「不存在」无法区分）；
      一旦注册就说明管理员**有意**开了它，此时「没带凭证」应当是 401，
      与全站「未认证 → 401 UNAUTHENTICATED」的错误码语义一致，
      运维也不会把 401 误读成「路径写错了」。
    """
    if settings.metrics_token:
        provided = _bearer(request)
        if provided is not None and hmac.compare_digest(provided, settings.metrics_token):
            return
        raise UnauthenticatedError("指标端点需要有效的 Bearer Token")

    client_host = request.client.host if request.client else None
    if client_host in LOOPBACK_HOSTS:
        return

    raise UnauthenticatedError("指标端点仅允许本机来源，或配置 LOCALCRAFT_METRICS_TOKEN")


@router.get(
    "/metrics",
    summary="Prometheus 指标（运维端点，不在开放 API 面内）",
    # 第 2 条硬要求：绝不进 openapi.json
    include_in_schema=False,
    dependencies=[Depends(metrics_access)],
    response_class=PlainTextResponse,
    responses={401: {"description": "缺少有效凭证且来源非本机"}},
)
async def metrics(request: Request) -> Response:
    """输出 Prometheus 文本格式指标。

    **不参与 `/api/v1` 契约**：它的字段可以随实现变化，前端不得依赖。
    """
    del request
    body = render_prometheus(
        engine,
        sql_enabled=counting_enabled(),
        slow_threshold_ms=settings.slow_request_ms,
    )
    # `text/plain; version=0.0.4` 是 Prometheus 约定的 exposition 内容类型。
    # 不带 `charset` 会有一些抓取器报错，所以显式给出。
    return PlainTextResponse(
        content=body,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


__all__ = ["LOOPBACK_HOSTS", "metrics_access", "router"]
