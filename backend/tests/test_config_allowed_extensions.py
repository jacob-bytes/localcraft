"""`ALLOWED_EXTENSIONS` 的两种写法都要能用（逗号串 / JSON 数组）。

## 为什么有这个文件

`allowed_extensions` 是 `list[str]`，pydantic-settings 对复杂类型**按 JSON 解析**。
而「列表类环境变量写成逗号串」是最符合直觉的写法，`docs/05` 的两处示例也正是逗号串
（`§6.3` 的完整清单与 `§14.2` 的 `zip,tar.gz,tgz,whl,exe,...`）。

两者相遇的后果不是静默忽略，而是**启动期硬失败**：

    pydantic_settings.exceptions.SettingsError:
    error parsing value for field "allowed_extensions" from source "EnvSettingsSource"

报错里没有一个字提到 JSON —— 照着文档做的人会卡在启动这一步且看不出怎么改。
所以 `_coerce_extension_list` 两种写法都接受；本文件把它钉住。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

#: 一个长度足够的真密钥（规避 SECRET_KEY 的公开值校验，与本文件主题无关）
GOOD_SECRET = "a" * 64


def _build(**overrides) -> Settings:
    return Settings(_env_file=None, secret_key=GOOD_SECRET, **overrides)


def test_default_list_is_the_full_whitelist() -> None:
    """不设环境变量时用代码兜底清单（权威清单是 docs/01 §8，此处只钉住规模不退化）。"""
    settings = _build()
    assert "xlsx" in settings.allowed_extensions
    assert "jsx" in settings.allowed_extensions
    assert len(settings.allowed_extensions) == len(set(settings.allowed_extensions)), "不该有重复项"


def test_accepts_comma_separated() -> None:
    """docs/05 的写法：#1 的直接动机。"""
    assert _build(allowed_extensions="zip,tar.gz,png").allowed_extensions == [
        "zip",
        "tar.gz",
        "png",
    ]


def test_accepts_comma_with_spaces() -> None:
    """`zip, tar.gz` 这种带空格的写法要容错，且不能把空格带进扩展名。"""
    assert _build(allowed_extensions="zip, tar.gz , png").allowed_extensions == [
        "zip",
        "tar.gz",
        "png",
    ]


def test_accepts_whitespace_separated() -> None:
    assert _build(allowed_extensions="zip png").allowed_extensions == ["zip", "png"]


def test_accepts_json_array() -> None:
    """原来的（也是 pydantic 原生的）写法必须继续可用。"""
    assert _build(allowed_extensions='["zip","png"]').allowed_extensions == ["zip", "png"]


def test_accepts_a_real_list() -> None:
    """直接传 list（测试与代码内构造的常见方式）不受影响。"""
    assert _build(allowed_extensions=["zip", "png"]).allowed_extensions == ["zip", "png"]


def test_empty_string_means_empty_list() -> None:
    """空串表示"一个都不允许"，而不是报错 —— 语义明确比猜要好。"""
    assert _build(allowed_extensions="").allowed_extensions == []


def test_json_garbage_still_fails_loudly() -> None:
    """以 `[` 开头但 JSON 非法时仍然报错：不要用"容错"掩盖真正的配置错误。"""
    with pytest.raises(ValidationError):
        _build(allowed_extensions='["zip", ]')


def test_numbers_are_still_rejected() -> None:
    """逗号切分只对字符串生效；传入数字仍然按类型错误处理。"""
    with pytest.raises(ValidationError):
        _build(allowed_extensions=123)
