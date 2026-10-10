"""应用配置：pydantic-settings 从环境变量读取。

约定（docs/01 FR-CFG-05）：密钥类配置（SECRET_KEY、数据库密码）**只走环境变量**，
不入系统设置表、不入数据库。
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

logger = logging.getLogger(__name__)

#: **公开在仓库里**的 `SECRET_KEY` 取值 —— 命中任何一个都说明「忘了设 SECRET_KEY」。
#: 两个来源不同、后果一样严重：
#:   1. `Settings.secret_key` 的**代码默认值**（本地开发用）；
#:   2. `deploy/localcraft.env.example` 里的**占位符**（照抄示例文件的人会拿到它）。
#:
#: 为什么必须硬拦：JWT 与图片签名都用它派生子密钥，而它**在公开仓库里**——
#: 漏设时任何人都能伪造超管令牌。这条以前只在 `scripts/security-check.sh` 里查占位符，
#: 而那个脚本查的是 ②、**漏了 ①**，且只在安装后手工跑。现在由 `_reject_public_secret` 在
#: **配置加载时**一律拒绝。
#:
#: ★ 这里是权威清单；改它时**同步改 `scripts/security-check.sh`**（shell 侧无法 import 本文件）。
INSECURE_SECRET_VALUES: frozenset[str] = frozenset(
    {
        "dev-only-insecure-secret-change-me",
        "REPLACE_WITH_openssl_rand_hex_32",
    }
)

# backend/ 目录（本文件位于 backend/app/core/config.py）
BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """全部运行期配置。

    字段名大小写不敏感地匹配环境变量（pydantic-settings 默认行为），
    因此 `SECRET_KEY` / `secret_key` 都能读到。
    """

    model_config = SettingsConfigDict(
        env_file=(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- 基础运行参数 ----------
    localcraft_host: str = "127.0.0.1"
    localcraft_port: int = 8000
    localcraft_debug: bool = False
    localcraft_version: str = "1.2.2"
    # 曾经有一个 localcraft_public_base_url，用来"生成下载链接与 OpenAPI 里的 server"。
    # 实测**没有任何读取点**：图片签名 URL 与下载票据 URL 都是相对路径
    # （`/api/v1/images/…`、`/api/v1/tools/{slug}/download?…`），OpenAPI 也没有 servers 段。
    # 一个"被文档描述、被 env 接受、却毫无作用"的字段比没有它更糟，故删除（docs/09 §13）。
    log_level: str = "INFO"

    # ---------- 数据库 ----------
    # SQLite 异步驱动需要四个斜杠表示绝对路径：
    #   sqlite+aiosqlite:////var/lib/localcraft/localcraft.db
    database_url: str = f"sqlite+aiosqlite:///{BASE_DIR / 'var' / 'localcraft.db'}"
    db_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_busy_timeout: int = 5000

    # ---------- 数据目录 ----------
    data_dir: Path = BASE_DIR / "var"

    # ---------- 安全 / 会话 ----------
    #: 代码默认值 —— **它公开在仓库里**，所以只在开发/测试有意义。
    #: 命中 `INSECURE_SECRET_VALUES` 且没开逃生阀时，**配置加载就会失败**
    #: （见 `_reject_public_secret`）。生产由 `install.sh` 用 `openssl rand -hex 32` 生成。
    secret_key: str = "dev-only-insecure-secret-change-me"
    #: 逃生阀：本地开发 / CI 确实要用占位密钥时显式打开（`LOCALCRAFT_ALLOW_INSECURE_SECRET=1`）。
    #: **生产不要设** —— 它的唯一用途是不让本地与 CI 被这条硬拦卡住。
    localcraft_allow_insecure_secret: bool = False
    jwt_algorithm: str = "HS256"
    # 契约 §3.2：refresh Cookie Max-Age=604800（7 天）
    access_token_minutes: int = Field(
        default=30,
        validation_alias=AliasChoices("ACCESS_TOKEN_MINUTES", "ACCESS_TOKEN_EXPIRE_MINUTES"),
    )
    refresh_token_days: int = Field(
        default=7,
        validation_alias=AliasChoices("REFRESH_TOKEN_DAYS", "REFRESH_TOKEN_EXPIRE_DAYS"),
    )
    # dev 走 http 同源代理，无需 Secure；prod 必须 true
    cookie_secure: bool = False
    cookie_domain: str | None = None
    # 连续失败锁定阈值与锁定时长（FR-AUTH-06）
    login_max_failures: int = 5
    lockout_minutes: int = 15
    # refresh token Cookie 路径（契约 §2：Path=/api/v1/auth）
    refresh_cookie_path: str = "/api/v1/auth"
    refresh_cookie_name: str = "refresh_token"

    # ---------- 文档 ----------
    api_docs_enabled: bool = False

    # ---------- 可观测性（M7：docs/11 §2.4 O12 / §3.4 M1、M2）----------
    # 命名与**读取方式**（这里踩过一次，记下来）：
    # pydantic-settings 默认只按**字段名**匹配环境变量，**不做前缀映射** ——
    # 字段 `metrics_enabled` 只认 `METRICS_ENABLED`，**不认** `LOCALCRAFT_METRICS_ENABLED`。
    # 仓库里既有的 4 个带前缀变量（host/port/debug/version）之所以工作，
    # 是因为字段名本身写作 `localcraft_host`（见本文件开头）。
    #
    # 本轮新增的运维开关按任务书 B3 与 `deploy/localcraft.env.example` 的既有惯例
    # 用 `LOCALCRAFT_` 前缀，因此必须显式声明 `validation_alias`；
    # 同时保留不带前缀的别名，让「按字段名猜」的人也能生效
    # （`extra="ignore"` 会把写错的名字静默吞掉，那正是最坏的行为）。
    # 写法与已有的 `access_token_minutes`（`AliasChoices`）保持一致。
    #
    # 慢请求告警阈值（毫秒）。0 表示关闭该告警。
    # 默认 1000 ms 的理由：docs/11 §1.1 实测本机单请求中位 0.28~5.7 ms、
    # §1.4 显示 c=20 时单请求已被放大到 18.6 ms 墙钟；而 NFR-PERF-01 的
    # 验收线是 **100 并发 P95 < 300 ms**。1000 ms 是验收线的 3 倍多 ——
    # 取这个值的目的是「只报真正异常的单请求」，让阈值告警的假阳性接近零。
    # 想更灵敏就调低，不想被吵就设 0。
    #
    # 注意：`LOCALCRAFT_SQL_COUNT` **不在这里** —— 它由
    # `app/core/sql_counter.counting_enabled()` 直接读环境变量，
    # 因为「挂不挂 SQLAlchemy 事件监听器」必须在 import 期就能决定。
    slow_request_ms: int = Field(
        default=1000,
        validation_alias=AliasChoices("SLOW_REQUEST_MS", "LOCALCRAFT_SLOW_REQUEST_MS"),
    )
    # /metrics 默认关闭。打开才会注册路由（任务书 B3 第 3 条）。
    metrics_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("LOCALCRAFT_METRICS_ENABLED", "METRICS_ENABLED"),
    )
    # /metrics 的 Bearer Token。留空 + 非本机来源 → 401（任务书 B3 第 4 条）。
    metrics_token: str = Field(
        default="",
        validation_alias=AliasChoices("LOCALCRAFT_METRICS_TOKEN", "METRICS_TOKEN"),
    )
    # 每路由保留的耗时样本上限（分位数估计精度 vs 内存）。见 app/core/metrics.py。
    metrics_sample_max: int = Field(
        default=1024,
        validation_alias=AliasChoices(
            "LOCALCRAFT_METRICS_SAMPLE_MAX", "METRICS_SAMPLE_MAX"
        ),
    )

    # ---------- 上传与解压防护（docs/05 §13.5 的五个限制 + 超时）----------
    # 这些走**环境变量**而不是系统设置表：它们是安全基线，
    # 不应该被一个「改设置」的请求在线放宽。docs/05 §13.5 也是按环境变量列的。
    # 上传的**业务**上限（单文件 MB、截图数、标签数、配额）走系统设置表，
    # 因为运营需要在管理台调整（docs/02 §3.19）。
    #
    # 解压后总体积上限：1 GB
    unzip_max_total_size: int = 1024 * 1024 * 1024
    # 压缩比上限：正常 zip 通常 < 20，200 足以识别炸弹
    unzip_max_ratio: int = 200
    # 条目数上限（防 inode 耗尽）
    unzip_max_files: int = 5000
    # 目录递归深度上限
    unzip_max_depth: int = 16
    # 单个条目解压后上限。
    # 注意：docs/05 §13.5 的表格建议 100 MB，而 docs/01 FR-SKILL-05（P0 需求）
    # 写的是 200 MB。这里取需求文档的 200 MB，需要更严时可改环境变量收紧。
    unzip_max_file_size: int = 200 * 1024 * 1024
    # 解压整体超时（秒）—— 防止「慢速炸弹」长时间占住 worker
    unzip_timeout_seconds: int = 60

    #: 上传文件大小上限的兜底值（系统设置 upload.max_file_size_mb 优先）
    max_upload_size: int = 200 * 1024 * 1024
    #: 允许上传的扩展名兜底值（系统设置 upload.allowed_extensions 优先）
    #: J-10（docs/09 §6.1 / docs/01 §8）：原先只有 21 项，**不含 .xlsx/.csv/.docx/.jpeg
    #: 等内网常用类型** —— 用户传个 Excel 会被 415 拒绝，而文档说允许。
    #: 平台从不执行上传的文件，只以 Content-Disposition: attachment 提供下载，
    #: 因此放宽类型白名单的风险很低，而「传 Excel 被拒」会直接产生支持工单。
    #: 权威清单是 docs/01 §8；下面的顺序按类别分组，便于人工核对。
    #:
    #: `NoDecode` 是必须的：pydantic-settings 默认在**数据源层**就对该字段做 JSON 解码，
    #: 逗号串会在我们的校验器之前抛 `SettingsError`（见 `_coerce_extension_list`）。
    #: 加上它之后原始字符串才会交到校验器手里。
    allowed_extensions: Annotated[list[str], NoDecode] = [
        # 压缩包
        "zip", "tar.gz", "tgz", "whl", "tar", "gz", "7z", "rar",
        # 制品与可执行
        "exe", "msi", "deb", "rpm", "jar", "war", "bin", "iso", "img",
        # 脚本
        "sh", "bat", "ps1", "py", "js", "ts", "jsx", "tsx", "go", "java", "sql",
        # 文档与数据
        "md", "txt", "pdf", "json", "yaml", "yml", "csv", "xlsx", "docx", "pptx",
        # 图片
        "png", "jpg", "jpeg", "webp", "gif", "svg",
    ]

    @field_validator("allowed_extensions", mode="before")
    @classmethod
    def _coerce_extension_list(cls, v: object) -> object:
        """接受「逗号/空白分隔的字符串」与 JSON 数组两种写法。

        ## 为什么需要它

        `list[str]` 在 pydantic-settings 里是按 **JSON** 解析的，而「列表类环境变量写逗号串」
        是更符合直觉的写法 —— `docs/05` 的两处示例本来就是逗号串。两者相遇的后果**不是**
        "静默忽略"，而是**启动期硬失败**，且报错完全没提"应该写 JSON"：

            $ ALLOWED_EXTENSIONS=zip,png python -m uvicorn app.main:app
            pydantic_settings.exceptions.SettingsError: error parsing value for
            field "allowed_extensions" from source "EnvSettingsSource"

        照着文档做的人会直接卡在启动这一步，而且看不出该怎么改。所以两种写法都收下：
        以 `[` 开头的按 JSON 解析（源层解码已被字段注解上的 `NoDecode` 关掉，
        所以 JSON 也在这里解析），否则按逗号/空白切分。

        扩展名不可能含逗号，所以按逗号切分无歧义；把空白也当分隔符是为了容错
        `zip, tar.gz` 这种带空格的写法。
        """
        if isinstance(v, str):
            text = v.strip()
            if not text:
                return []
            if text.startswith("["):
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        "ALLOWED_EXTENSIONS 既不是合法的 JSON 数组、也不是逗号分隔清单："
                        f"{exc}。例如 `zip,tar.gz,png` 或 `[\"zip\",\"png\"]`"
                    ) from exc
                if not isinstance(parsed, list):
                    raise ValueError("ALLOWED_EXTENSIONS 的 JSON 写法必须是一个数组")
                return parsed
            return [part for part in re.split(r"[,\s]+", text) if part]
        return v

    @property
    def unzip_limits(self) -> dict[str, int]:
        """打包给 `skill_service` 用，避免它到处读 settings。"""
        return {
            "max_total_size": self.unzip_max_total_size,
            "max_ratio": self.unzip_max_ratio,
            "max_files": self.unzip_max_files,
            "max_depth": self.unzip_max_depth,
            "max_file_size": self.unzip_max_file_size,
            "timeout_seconds": self.unzip_timeout_seconds,
        }

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, v: str) -> str:
        return v.upper()

    @field_validator("secret_key")
    @classmethod
    def _non_empty_secret(cls, v: str) -> str:
        if not v:
            raise ValueError("SECRET_KEY 不能为空")
        # HS256 的密钥短于 32 字节时，PyJWT 只会打一条 InsecureKeyLengthWarning
        # 然后照常签发 —— 也就是说「弱密钥」在功能上完全看不出来。
        # 这里显式告警一次（不抛异常，避免打断本地开发），
        # 生产环境由 install.sh 用 `openssl rand -hex 32` 生成（64 字符）。
        if len(v.encode("utf-8")) < 32:
            logger.warning(
                "SECRET_KEY 只有 %d 字节，低于 HS256 建议的 32 字节；"
                "生产环境请用 `openssl rand -hex 32` 重新生成",
                len(v.encode("utf-8")),
            )
        return v

    @field_validator("jwt_algorithm")
    @classmethod
    def _hmac_algorithm_only(cls, v: str) -> str:
        """只允许 HMAC 家族。

        `jwt.decode()` 已经把 `algorithms` 固定成这个字段的取值，所以这不是在补洞，
        而是**防呆**：把 `JWT_ALGORITHM` 误设成 `none` / `RS256` 之类，会在运行期以
        很难懂的方式失败（`none` 会让 PyJWT 拒绝带密钥的签发；`RS256` 需要另一套
        非对称密钥体系，本项目没有）。收窄到三种取值，错配在配置加载期就报出来。
        """
        allowed = {"HS256", "HS384", "HS512"}
        if v not in allowed:
            raise ValueError(
                f"JWT_ALGORITHM 只支持 {sorted(allowed)}，当前是 {v!r}"
                "（本项目只有对称密钥，RSA/EC 与 none 均不受支持）"
            )
        return v

    @model_validator(mode="after")
    def _reject_public_secret(self) -> Settings:
        """拒绝**公开在仓库里**的 `SECRET_KEY`（除非显式开逃生阀）。

        为什么用 `mode="after"` 而不是给 `secret_key` 挂 `field_validator`：
        这条判断要**同时**看到 `secret_key` 与 `localcraft_allow_insecure_secret`，
        而 `mode="after"` 不依赖字段的声明顺序（`info.data` 才有那个约束）。
        """
        if self.secret_key in INSECURE_SECRET_VALUES and not self.localcraft_allow_insecure_secret:
            raise ValueError(
                f"SECRET_KEY 仍是仓库里公开的默认值/占位符（{self.secret_key!r}）。\n"
                "  JWT 与图片签名都由它派生子密钥 —— 任何人拿这个公开值都能伪造超管令牌。\n"
                "  生产修法：SECRET_KEY=$(openssl rand -hex 32)"
                "（或重跑 install.sh，它会自动生成）\n"
                "  本地开发 / CI 确实要用占位密钥时，设 "
                "LOCALCRAFT_ALLOW_INSECURE_SECRET=1 显式放行。"
            )
        return self

    # ---------- 派生属性 ----------
    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def access_token_seconds(self) -> int:
        return self.access_token_minutes * 60

    @property
    def refresh_token_seconds(self) -> int:
        return self.refresh_token_days * 24 * 3600

    @property
    def web_dist_dir(self) -> Path:
        """前端构建产物目录。

        仓库布局下位于 backend/../web/dist；发布包布局下位于 app/current/web/dist。
        两个候选路径都探测，取存在的那个；都不存在时返回仓库布局路径，
        由 main.py 优雅降级（不挂载静态文件，不启动失败）。
        """
        repo_layout = BASE_DIR.parent / "web" / "dist"
        if repo_layout.is_dir():
            return repo_layout
        release_layout = BASE_DIR / "web" / "dist"
        if release_layout.is_dir():
            return release_layout
        return repo_layout

    @property
    def db_path(self) -> Path | None:
        """SQLite 文件路径；非 SQLite 返回 None。"""
        if not self.is_sqlite:
            return None
        # sqlite+aiosqlite:////abs/path.db → /abs/path.db
        _, _, tail = self.database_url.partition(":///")
        return Path(tail) if tail else None


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def ensure_runtime_dirs() -> None:
    """确保 `DATA_DIR` 与 SQLite 数据库文件所在目录存在。

    为什么必须有这一步：`alembic upgrade head` 在**服务启动之前**执行
    （docs/02 §6.3），而 SQLite 的 `connect` **不会自动创建父目录** ——
    全新机器上如果 `backend/var/`（或 `/var/lib/localcraft/`）不存在，
    迁移会直接以 `unable to open database file` 失败。

    生产环境由 `install.sh` 用 `install -d` 建好目录；开发机没有这一层，
    所以在建 engine 之前统一兜一次。已存在时 `mkdir` 是幂等的。
    """
    # 权限问题时不要在这里抛：让后续的连接失败报出更明确的错（含路径与 errno）
    with contextlib.suppress(OSError):
        settings.data_dir.mkdir(parents=True, exist_ok=True)

    db_path = settings.db_path
    if db_path is not None and str(db_path.parent):
        with contextlib.suppress(OSError):
            db_path.parent.mkdir(parents=True, exist_ok=True)
