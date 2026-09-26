"""应用配置：pydantic-settings 从环境变量读取。

约定（docs/01 FR-CFG-05）：密钥类配置（SECRET_KEY、数据库密码）**只走环境变量**，
不入系统设置表、不入数据库。
"""

from __future__ import annotations

import contextlib
import logging
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

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
    localcraft_version: str = "1.0.0"
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
    secret_key: str = "dev-only-insecure-secret-change-me"
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
    allowed_extensions: list[str] = [
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
