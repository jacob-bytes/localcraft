"""健壮性加固：`SECRET_KEY` / `JWT_ALGORITHM` 的配置期硬拦（契约见 §3.2 与 docs/01 FR-CFG-05）。

## 背景

`Settings.secret_key` 的代码默认值是 `dev-only-insecure-secret-change-me` ——
**它公开在这个仓库里**。原先只有一条「短于 32 字节就 warning」的检查，而默认值 34 字节
**连 warning 都不触发**，于是：

  - 走 `docs/05` §5.1~5.9 手工部署而漏设 `SECRET_KEY` → 服务照常启动；
  - JWT 与图片签名都由它派生子密钥 → **任何人拿仓库里的这个值就能伪造超管令牌**。

`scripts/security-check.sh` 本来会查"占位符"，但它查的是示例 env 文件里的
`REPLACE_WITH_openssl_rand_hex_32`，**漏了代码默认值**，而且只在安装后手工跑一次。
现在由 `_reject_public_secret` 在**配置加载期**一律拒绝（`INSECURE_SECRET_VALUES` 是权威清单）。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import INSECURE_SECRET_VALUES, Settings

#: 一个长度足够的真密钥（测试用；不用于任何真实环境）
GOOD_SECRET = "a" * 64


def _build(**overrides) -> Settings:
    """构造一个**不受环境变量影响**的 Settings（`_env_file=None` 跳过 .env）。"""
    return Settings(_env_file=None, **overrides)


def test_insensitive_list_covers_both_known_sources() -> None:
    """权威清单必须同时覆盖「代码默认值」与「示例 env 文件的占位符」。"""
    assert "dev-only-insecure-secret-change-me" in INSECURE_SECRET_VALUES
    assert "REPLACE_WITH_openssl_rand_hex_32" in INSECURE_SECRET_VALUES


@pytest.mark.parametrize("bad", sorted(INSECURE_SECRET_VALUES))
def test_rejects_public_secret_values(bad: str) -> None:
    """两个公开值都必须被拒绝，且消息里要给出可执行的修法。"""
    with pytest.raises(ValidationError) as excinfo:
        _build(secret_key=bad)

    message = str(excinfo.value)
    assert "公开" in message, f"错误信息没说明这是公开值：{message}"
    assert "openssl rand -hex 32" in message, "错误信息没给出修法"
    assert "LOCALCRAFT_ALLOW_INSECURE_SECRET=1" in message, "没告诉本地开发怎么放行"


def test_escape_hatch_allows_placeholder_secret() -> None:
    """逃生阀是本地开发/CI 的唯一放行方式，且必须显式打开。"""
    settings = _build(
        secret_key="dev-only-insecure-secret-change-me",
        localcraft_allow_insecure_secret=True,
    )
    assert settings.secret_key == "dev-only-insecure-secret-change-me"


def test_real_secret_passes() -> None:
    assert _build(secret_key=GOOD_SECRET).secret_key == GOOD_SECRET


def test_empty_secret_still_rejected() -> None:
    """空值走的是另一条分支（`_non_empty_secret`），不能被这条改动绕过。"""
    with pytest.raises(ValidationError):
        _build(secret_key="")


@pytest.mark.parametrize("bad", ["none", "RS256", "ES256", "hs256"])
def test_rejects_non_hmac_or_wrong_case_jwt_algorithm(bad: str) -> None:
    """`JWT_ALGORITHM` 只允许 HS256/384/512（大小写敏感 —— 取值要能直接喂给 PyJWT）。"""
    with pytest.raises(ValidationError):
        _build(secret_key=GOOD_SECRET, jwt_algorithm=bad)


@pytest.mark.parametrize("good", ["HS256", "HS384", "HS512"])
def test_accepts_hmac_algorithms(good: str) -> None:
    assert _build(secret_key=GOOD_SECRET, jwt_algorithm=good).jwt_algorithm == good


def test_default_algorithm_is_still_hs256() -> None:
    """默认值不能因为加了白名单而变。"""
    assert _build(secret_key=GOOD_SECRET).jwt_algorithm == "HS256"
