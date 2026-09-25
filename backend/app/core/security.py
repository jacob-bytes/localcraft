"""密码哈希、JWT 签发/校验、refresh token 生成与哈希、密码强度校验。"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import settings
from app.core.errors import TokenExpiredError, TokenRevokedError, ValidationError

# ---------------------------------------------------------------------------
# 密码哈希 —— Argon2id（docs/01 FR-AUTH-05）
# ---------------------------------------------------------------------------
# 参数取 argon2-cffi 的默认值（time_cost=3, memory_cost=64MiB, parallelism=4,
# hash_len=32, salt_len=16），输出形如 $argon2id$v=19$m=65536,t=3,p=4$...，
# 长度约 97 字符，正好落在 users.password_hash String(255) 内（docs/02 §3.1）。
_ph = PasswordHasher()

#: 供测试与 CLI 复用的假哈希：当用户名不存在时也要做一次验密，避免
#: 通过响应时间差枚举用户名。它在模块导入时算一次，不占请求路径。
_DUMMY_HASH = _ph.hash("dummy-password-for-timing-equalization")


def hash_password(password: str) -> str:
    """Argon2id 哈希。"""
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """校验密码。哈希损坏或不匹配都返回 False，不抛异常给调用方。"""
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """哈希参数是否需要升级（argon2 参数变更后调用）。"""
    try:
        return _ph.check_needs_rehash(password_hash)
    except (InvalidHashError, ValueError):
        return True


def dummy_verify() -> None:
    """用户名不存在时执行的等时验密，抹平响应时间差。"""
    verify_password("dummy-password-for-timing-equalization", _DUMMY_HASH)


# ---------------------------------------------------------------------------
# 归一化
# ---------------------------------------------------------------------------
def normalize_username(username: str) -> str:
    """用户名归一化：去首尾空白、NFKC 全角转半角、转小写。

    契约 §3.3：「用户名大小写不敏感（存归一化小写，查询时也归一化）」。
    """
    return unicodedata.normalize("NFKC", username.strip()).lower()


# ---------------------------------------------------------------------------
# 密码强度（docs/01 FR-AUTH-04）
# ---------------------------------------------------------------------------
MIN_PASSWORD_LENGTH = 10
REQUIRED_CLASSES = 3  # 大写/小写/数字/符号 四类中至少 3 类


def password_strength_errors(password: str, username: str | None = None) -> list[str]:
    """返回强度问题列表；空列表表示通过。

    前端也校验一次，但**服务端是权威**（docs/01 §3.3）。
    """
    errors: list[str] = []
    if len(password) < MIN_PASSWORD_LENGTH:
        errors.append(f"密码长度不能少于 {MIN_PASSWORD_LENGTH} 位")

    classes = 0
    if any(c.isupper() for c in password):
        classes += 1
    if any(c.islower() for c in password):
        classes += 1
    if any(c.isdigit() for c in password):
        classes += 1
    if any(not c.isalnum() for c in password):
        classes += 1
    if classes < REQUIRED_CLASSES:
        errors.append("密码需至少包含大写字母、小写字母、数字、符号中的 3 类")

    if username and normalize_username(password) == normalize_username(username):
        errors.append("密码不能与用户名相同")

    return errors


def validate_password_strength(password: str, username: str | None = None) -> None:
    """强度不足时抛 `VALIDATION_ERROR`，`details.fields` 为逐条提示。"""
    errors = password_strength_errors(password, username)
    if errors:
        raise ValidationError(
            message="密码强度不足",
            fields=[{"field": "new_password", "message": e} for e in errors],
        )


# ---------------------------------------------------------------------------
# JWT（access token，无状态，不落库）
# ---------------------------------------------------------------------------
TOKEN_TYPE_ACCESS = "access"


def create_access_token(
    *,
    user_id: int,
    username: str,
    roles: list[str],
    must_change_password: bool = False,
    expires_delta: timedelta | None = None,
) -> tuple[str, int]:
    """签 access token。

    返回 `(token, expires_in_seconds)`。`expires_in` 是契约 §3.2 要求的字段。
    """
    now = datetime.now(UTC)
    ttl = expires_delta or timedelta(minutes=settings.access_token_minutes)
    expire = now + ttl
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "username": username,
        "roles": roles,
        "mcp": must_change_password,
        "type": TOKEN_TYPE_ACCESS,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "jti": secrets.token_hex(8),
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
    return token, int(ttl.total_seconds())


def decode_access_token(token: str) -> dict[str, Any]:
    """解析并校验 access token。

    - 过期 → `TOKEN_EXPIRED`（前端据此静默 refresh 后重试一次）
    - 其他不合法（签名错、格式错、类型不对）→ `UNAUTHENTICATED`
    """
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError() from exc
    except jwt.PyJWTError as exc:
        raise TokenRevokedError("凭证无效") from exc

    if payload.get("type") != TOKEN_TYPE_ACCESS:
        raise TokenRevokedError("凭证类型不正确")
    return payload


# ---------------------------------------------------------------------------
# refresh token —— 256 bit 高熵随机串，只在服务端存 SHA256
# ---------------------------------------------------------------------------
REFRESH_TOKEN_PREFIX = "rt_"


def generate_refresh_token() -> str:
    """生成不透明 refresh token 明文（只在 Set-Cookie 中出现一次）。"""
    return REFRESH_TOKEN_PREFIX + secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """SHA256 十六进制小写。

    与 docs/02 §3.15 对 API Token 的取舍一致：高熵凭证用快哈希即可，
    慢哈希会把每个请求的鉴权开销打死。
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# API Token（M3 才签发，M1 只做鉴权侧的解析与哈希比对）
# ---------------------------------------------------------------------------
API_TOKEN_PREFIX = "st_"


def generate_api_token() -> str:
    return API_TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_api_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def api_token_prefix(token: str) -> str:
    """前 8 位明文，用于列表页识别（docs/02 §3.15 `token_prefix`）。"""
    return token[:8]


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)
