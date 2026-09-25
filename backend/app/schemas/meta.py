"""系统元信息与就绪探针 Schema。"""

from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str = "ok"


class ReadyCheck(BaseModel):
    name: str
    ok: bool
    detail: str | None = None


class ReadyResponse(BaseModel):
    """`GET /readyz` —— 真的探测：`SELECT 1` 能通 + `DATA_DIR` 可写。"""

    status: str
    checks: list[ReadyCheck]


class MetaFeatures(BaseModel):
    """前端据此决定是否渲染某些区块，避免前后端发布不同步时的白屏（docs/03 §3.1）。"""

    webapp_health_check: bool = False
    skill_preview: bool = True
    anonymous_view: bool = False
    change_password: bool = True


class MetaResponse(BaseModel):
    """`GET /api/v1/meta`（docs/03 §3.1）—— **只返回 `is_public = true` 的设置项**。"""

    site_name: str
    announcement_md: str
    auth_provider: str
    allow_anonymous_view: bool
    default_sort: str
    page_size: int
    app_version: str
    api_version: str = "v1"
    features: MetaFeatures
