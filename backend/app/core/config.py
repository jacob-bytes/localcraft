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
    selftool_host: str = "127.0.0.1"
    selftool_port: int = 8000
    selftool_debug: bool = False
    selftool_version: str = "1.0.0"
    selftool_public_base_url: str = "http://127.0.0.1:8000"
    log_level: str = "INFO"

    # ---------- 数据库 ----------
    # SQLite 异步驱动需要四个斜杠表示绝对路径：
    #   sqlite+aiosqlite:////var/lib/selftool/selftool.db
    database_url: str = f"sqlite+aiosqlite:///{BASE_DIR / 'var' / 'selftool.db'}"
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
    全新机器上如果 `backend/var/`（或 `/var/lib/selftool/`）不存在，
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
