"""密码哈希、JWT 签发/校验、refresh token 生成与哈希、密码强度校验。"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import settings
from app.core.errors import (
    TokenExpiredError,
    UnauthenticatedError,
    ValidationError,
)

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
        # 签名不对 / 结构不对 —— 属于「凭证格式非法」，不是「已签发后被吊销」。
        # 两者都会让前端做不同的事：UNAUTHENTICATED 直接跳登录，
        # TOKEN_REVOKED 提示「登录已失效」。这里必须是前者。
        raise UnauthenticatedError("凭证无效") from exc

    if payload.get("type") != TOKEN_TYPE_ACCESS:
        raise UnauthenticatedError("凭证类型不正确")
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


# ---------------------------------------------------------------------------
# 下载票据 —— HMAC-SHA256，密钥是从 SECRET_KEY 派生的**子密钥**
# ---------------------------------------------------------------------------
# 为什么必须派生子密钥：SECRET_KEY 同时用于签 JWT。如果下载票据直接复用主密钥，
# 任何一次票据签名/校验的实现缺陷都会把主密钥的用途扩大一圈（签名预言机），
# 而且将来轮换票据密钥就必须同时作废所有登录态。用途隔离（key separation）
# 是标准做法。
#
# 这里用 HKDF-SHA256（RFC 5869）的 extract+expand 两步，不引入额外依赖。
_HKDF_SALT = b"localcraft-hkdf-v1"


def derive_subkey(purpose: str, *, length: int = 32) -> bytes:
    """由 SECRET_KEY 派生用途隔离的子密钥（HKDF-SHA256）。"""
    ikm = settings.secret_key.encode("utf-8")
    prk = hmac.new(_HKDF_SALT, ikm, hashlib.sha256).digest()
    info = f"localcraft:{purpose}".encode()
    okm = b""
    block = b""
    counter = 1
    while len(okm) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        okm += block
        counter += 1
    return okm[:length]


# ---------------------------------------------------------------------------
# 图片能力签名（契约 §14.3）
# ---------------------------------------------------------------------------
# 为什么要签名：图片 URL 是**原样下发给前端**的（前端只做 onError 占位降级），
# <img> 标签带不上 Authorization 头。若不做能力签名，就只能二选一：
#   - 图片接口要求 Header → 前端所有图片都加载不出来
#   - 图片接口完全公开 → 猜到 ID 就能看 private 工具的截图
# 签名 URL 把「访问能力」编码进 URL 本身：拿到 URL 就能取图，URL 过期即失效。
#
# 子密钥同样是**派生**的，不复用 SECRET_KEY（与下载票据同理，用途隔离）。
IMAGE_SIGNATURE_PURPOSE = "image-signature"

#: 签名有效期默认 168 小时 = 7 天（可由设置项 `images.signature_ttl_hours` 覆盖）
IMAGE_SIGNATURE_DEFAULT_TTL_HOURS = 168


def image_signature(image_id: int, variant: str, *, ttl_hours: int) -> str:
    """生成图片 URL 的能力签名。

    签名内容刻意**只含** `image_id|variant`，不含过期时间：
    过期由 URL 上的 `exp` 参数表达并在校验时比对。这样同一张图在同一有效期内
    生成的 URL 是稳定的（便于浏览器缓存与前端去重）。
    """
    exp = int((datetime.now(UTC) + timedelta(hours=ttl_hours)).timestamp())
    message = f"{image_id}|{variant}|{exp}".encode()
    mac = hmac.new(derive_subkey(IMAGE_SIGNATURE_PURPOSE), message, hashlib.sha256).digest()
    return f"{_b64url_encode(mac)}.{exp}"


def verify_image_signature(image_id: int, variant: str, sig: str | None) -> bool:
    """校验图片签名。**常量时间比较**，且对格式错误一律返回 False（不抛异常）。"""
    if not sig or "." not in sig:
        return False
    mac_part, _, exp_part = sig.partition(".")
    try:
        exp = int(exp_part)
    except ValueError:
        return False
    if exp <= int(datetime.now(UTC).timestamp()):
        return False
    expected = hmac.new(
        derive_subkey(IMAGE_SIGNATURE_PURPOSE),
        f"{image_id}|{variant}|{exp}".encode(),
        hashlib.sha256,
    ).digest()
    try:
        provided = _b64url_decode(mac_part)
    except Exception:
        return False
    # compare_digest：不能用 == （会因短路比较泄露时序信息）
    return hmac.compare_digest(expected, provided)


DOWNLOAD_TICKET_PURPOSE = "download-ticket"

#: 票据有效期（秒）—— docs/03 §3.15 规定 60 秒
DOWNLOAD_TICKET_TTL_SECONDS = 60


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def create_download_ticket(
    *,
    tool_id: int,
    version_id: int,
    user_id: int,
    ttl_seconds: int = DOWNLOAD_TICKET_TTL_SECONDS,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    """签发下载票据，返回 `(token, expires_at)`。

    载荷绑定 `user_id`：票据泄露给别人也用不了（横向越权防护）。
    """
    issued = now or datetime.now(UTC)
    expires_at = issued + timedelta(seconds=ttl_seconds)
    payload = {
        "tool_id": tool_id,
        "version_id": version_id,
        "user_id": user_id,
        "exp": int(expires_at.timestamp()),
        "iat": int(issued.timestamp()),
        "jti": secrets.token_hex(8),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(
        derive_subkey(DOWNLOAD_TICKET_PURPOSE), raw, hashlib.sha256
    ).digest()
    return f"{_b64url_encode(raw)}.{_b64url_encode(signature)}", expires_at


class TicketError(Exception):
    """票据无效（签名错/格式错/篡改）。"""


class TicketExpiredError(TicketError):
    """票据已过期。"""


def verify_download_ticket(token: str, *, now: datetime | None = None) -> dict[str, Any]:
    """校验票据并返回载荷。

    签名比对用 `hmac.compare_digest`（常量时间），避免时序侧信道。
    """
    if not token or "." not in token:
        raise TicketError("票据格式不正确")
    raw_b64, sig_b64 = token.rsplit(".", 1)
    try:
        raw = _b64url_decode(raw_b64)
        signature = _b64url_decode(sig_b64)
    except (ValueError, binascii.Error) as exc:
        raise TicketError("票据编码不正确") from exc

    expected = hmac.new(derive_subkey(DOWNLOAD_TICKET_PURPOSE), raw, hashlib.sha256).digest()
    # compare_digest 而不是 == ：字符串/字节比较会短路，泄露签名前缀
    if not hmac.compare_digest(signature, expected):
        raise TicketError("票据签名校验失败")

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TicketError("票据内容不正确") from exc

    current = now or datetime.now(UTC)
    if int(payload.get("exp", 0)) <= int(current.timestamp()):
        raise TicketExpiredError("票据已过期")
    return payload
