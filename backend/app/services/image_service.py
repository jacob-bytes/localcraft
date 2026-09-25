"""图片处理（FR-FILE-09 / FR-FILE-10）。

三件事：

  1. **校验真实格式**，不只看扩展名 —— 把 `.exe` 改名成 `.png` 必须被拒。
     做法是 `PIL.Image.open()` 让 Pillow 按魔数嗅探，再 `verify()` 校验完整性。
  2. 生成 **480px 宽**缩略图，门户卡片只加载缩略图（FR-FILE-10）。
  3. 限制单张 ≤ 5 MB、封面 1 张、截图 ≤ 8 张（可配）。

另外设了 `Image.MAX_IMAGE_PIXELS`：Pillow 默认约 1.78 亿像素才告警，
这里收紧到 5000 万并把告警升级成异常 —— 「解压炸弹」在图片上同样成立
（一张 30000×30000 的 PNG 可以只有几十 KB，解码后却要 3.6 GB 内存）。
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.errors import UnsupportedMediaTypeError, ValidationError

logger = logging.getLogger(__name__)

#: 单张图片大小上限（FR-FILE-09：5 MB）
MAX_IMAGE_BYTES = 5 * 1024 * 1024

#: 缩略图宽度（FR-FILE-10）
THUMBNAIL_WIDTH = 480

#: 允许的 Pillow 格式 → 归一化扩展名与 MIME
ALLOWED_FORMATS: dict[str, tuple[str, str]] = {
    "PNG": ("png", "image/png"),
    "JPEG": ("jpg", "image/jpeg"),
    "WEBP": ("webp", "image/webp"),
    "GIF": ("gif", "image/gif"),
}

#: 解码像素上限（防「图片炸弹」）
MAX_IMAGE_PIXELS = 50_000_000

# Pillow 对超过 MAX_IMAGE_PIXELS 只是发 DecompressionBombWarning，默认不阻断。
# 这里改成硬失败。
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


@dataclass(frozen=True)
class ImageInfo:
    """校验通过的图片元信息。"""

    format: str  # Pillow 格式名，如 PNG
    ext: str  # 归一化扩展名，如 png
    mime_type: str
    width: int
    height: int
    size: int


def inspect_image(data: bytes, *, declared_name: str = "") -> ImageInfo:
    """校验图片并返回元信息。不合法抛 `UNSUPPORTED_MEDIA_TYPE`。

    **必须用 Pillow 嗅探真实格式**：扩展名是用户可控的字符串，
    只按扩展名放行等于允许上传任意可执行文件。
    """
    if not data:
        raise UnsupportedMediaTypeError(
            message="图片内容为空", details={"file_name": declared_name}
        )
    if len(data) > MAX_IMAGE_BYTES:
        raise ValidationError(
            message=f"图片超过 {MAX_IMAGE_BYTES // (1024 * 1024)} MB 上限",
            details={
                "limit_mb": MAX_IMAGE_BYTES // (1024 * 1024),
                "actual_mb": round(len(data) / (1024 * 1024), 2),
            },
        )

    try:
        with Image.open(io.BytesIO(data)) as probe:
            detected = (probe.format or "").upper()
            width, height = probe.size
            # verify() 会校验文件完整性（CRC / 结构），但会让对象不可再用，
            # 所以它是「校验」而不是「读取」。
            probe.verify()
    except UnidentifiedImageError as exc:
        raise UnsupportedMediaTypeError(
            message="不是有效的图片文件",
            details={"file_name": declared_name, "allowed": sorted(ALLOWED_FORMATS)},
        ) from exc
    except Image.DecompressionBombError as exc:
        raise UnsupportedMediaTypeError(
            message="图片像素数超过上限",
            details={"limit_pixels": MAX_IMAGE_PIXELS},
        ) from exc
    except (OSError, ValueError) as exc:
        raise UnsupportedMediaTypeError(
            message="图片已损坏或无法解析",
            details={"file_name": declared_name, "reason": str(exc)[:120]},
        ) from exc

    if detected not in ALLOWED_FORMATS:
        raise UnsupportedMediaTypeError(
            message=f"不支持的图片格式: {detected or 'unknown'}",
            details={"detected": detected, "allowed": sorted(ALLOWED_FORMATS)},
        )
    if width <= 0 or height <= 0:
        raise UnsupportedMediaTypeError(message="图片尺寸不合法")
    if width * height > MAX_IMAGE_PIXELS:
        raise UnsupportedMediaTypeError(
            message="图片像素数超过上限",
            details={"pixels": width * height, "limit_pixels": MAX_IMAGE_PIXELS},
        )

    ext, mime = ALLOWED_FORMATS[detected]
    return ImageInfo(
        format=detected, ext=ext, mime_type=mime, width=width, height=height, size=len(data)
    )


def make_thumbnail(data: bytes, *, width: int = THUMBNAIL_WIDTH) -> bytes:
    """生成缩略图。

    - 用 `ImageOps.exif_transpose` 处理手机照片的 EXIF 方向，否则会在卡片上躺倒
    - 等比缩放，**不放大**（小图保持原尺寸，避免糊）
    - 统一转 RGB 后用 PNG 存：GIF 的调色板 / 透明度在缩放后容易出噪点，
      统一成 PNG 最稳；缩略图本来就只有几十 KB
    """
    with Image.open(io.BytesIO(data)) as source:
        source.load()
        image = ImageOps.exif_transpose(source)
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
        if image.width > width:
            ratio = width / image.width
            new_size = (width, max(1, round(image.height * ratio)))
            image = image.resize(new_size, Image.Resampling.LANCZOS)

        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()


def thumbnail_extension() -> str:
    """缩略图统一存 PNG。"""
    return "png"


__all__ = [
    "ALLOWED_FORMATS",
    "MAX_IMAGE_BYTES",
    "MAX_IMAGE_PIXELS",
    "THUMBNAIL_WIDTH",
    "ImageInfo",
    "inspect_image",
    "make_thumbnail",
    "thumbnail_extension",
]
