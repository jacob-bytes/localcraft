"""请求 ID：ULID 生成与格式校验（契约 §4.4 / docs/03 §1.8）。

客户端可通过 `X-Request-Id` 传入 26 位 ULID 或 UUID 以贯穿调用链；
**格式非法则服务端重新生成**（不做静默透传，避免日志被注入奇怪内容）。
"""

from __future__ import annotations

import os
import re
import time
import uuid

#: Crockford Base32 字母表（ULID 规范）
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

_ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def new_ulid() -> str:
    """生成 26 位 ULID：48 bit 毫秒时间戳 + 80 bit 随机数。"""
    timestamp_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    randomness = int.from_bytes(os.urandom(10), "big")

    chars = [""] * 26
    for i in range(25, -1, -1):
        chars[i] = _CROCKFORD[randomness & 0x1F]
        randomness >>= 5
    # 前 10 位放时间戳（48 bit / 5 bit = 9.6 → 10 位）
    for i in range(9, -1, -1):
        chars[i] = _CROCKFORD[timestamp_ms & 0x1F]
        timestamp_ms >>= 5
    return "".join(chars)


def is_valid_request_id(value: str) -> bool:
    return bool(_ULID_RE.match(value) or _UUID_RE.match(value))


def resolve_request_id(incoming: str | None) -> str:
    """采用客户端传来的合法 ID，否则新生成一个。"""
    if incoming:
        candidate = incoming.strip()
        if is_valid_request_id(candidate):
            return candidate
    return new_ulid()


def new_uuid() -> str:
    return str(uuid.uuid4())
