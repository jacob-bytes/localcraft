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

    # ---- M12 站点定制信息（契约 §27.3）----
    # **向后兼容的新增**：既有字段一个都没动。
    # 全部 `str`、**未配置时为空串而不是 `null`** —— §27.2 定的默认值就是空串，
    # 保持类型稳定，前端可以直接按字符串判空（不必处理 null）。
    #
    # 刻意**不给模型层默认值**（与 `api_version` / `MetaFeatures` 的写法不同）：
    # 这 5 个字段服务端**永远**会填（空串也是值）。不给默认值 = 它们进
    # openapi 的 `required`，前端 `check:api-types` 生成的是 `field: string`
    # 而不是 `field?: string` —— 后者会逼前端写一堆 `?? ""` 或 `?.`。
    # 版本号**不是**设置项：页脚要显示版本时取上面已有的 `app_version`。
    site_subtitle: str
    footer_org: str
    footer_contact_email: str
    footer_contact_phone: str
    footer_notice: str

    # ---- M13 站点定制信息（契约 §28.4）----
    # `portal.footer_tagline`：AppShell 里原本**写死**的标语，§28.3 把它做成
    # 可配置项。与上面 5 个字段一样：永远是 `str`、不给模型层默认值。
    #
    # ★ 语义差别只有一处：它的**默认值不是空串**（§28.3 的定点例外）——
    # 未配置的库读出来是「内网工具与 Skill 共享平台」，前端据此渲染页脚标语；
    # 管理员显式留空时才是空串（= 不显示）。判空逻辑在前端，契约 §28.6。
    footer_tagline: str
