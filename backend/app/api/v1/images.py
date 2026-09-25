"""图片读取路由（docs/02 §1.4：图片走接口做可见性校验，不是静态文件直出）。

为什么不做成静态目录：`/data/files/images/...` 直出的话，任何人猜到 ID
就能看到 `private` 工具的截图。走接口可以在每次读取时校验父工具的可见性。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import PortalAccess, get_visibility_context, image_access
from app.core.errors import NotFoundError
from app.db.session import get_db
from app.models.enums import RoleCode
from app.repositories import tool_images as images_repo
from app.repositories import tools as tools_repo
from app.repositories.tools import VisibilityContext
from app.storage import get_storage

router = APIRouter(tags=["portal"])

#: 允许的变体
_VARIANTS = frozenset({"full", "thumb"})


@router.get(
    "/images/{image_id}",
    summary="图片获取（做可见性校验后输出文件）",
    response_class=FileResponse,
)
async def get_image(
    image_id: int,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    access: Annotated[PortalAccess, Depends(image_access)],
    visibility: Annotated[VisibilityContext, Depends(get_visibility_context)],
    variant: Annotated[str, Query(pattern="^(full|thumb)$")] = "full",
) -> FileResponse:
    """按 `?variant=thumb|full` 输出缩略图或原图。

    **两条鉴权路径**（契约 §14.3）：

      1. `?sig=<b64url>.<exp>` —— 能力签名。`<img>` 标签带不上 Authorization 头，
         所以列表/详情下发的 `cover_url` / `thumb_url` 都自带签名。
      2. `Authorization: Bearer <token>` —— 已登录用户直接取图（编辑器预览等场景）。

    两条都没有 → **404**。无权访问也一律 404（不是 403）—— 与图片是否存在无关，
    避免用状态码差异探测资源。

    注意：签名**只证明「这个 URL 是服务端下发的」**，
    父工具的可见性仍然照常校验 —— 签名不是绕过可见性的后门。
    这样设计是因为签发的 URL 里只含 image_id 与 variant（不含用户身份），
    若把它当成完全授权，URL 一旦外泄就等于把图送出去了。
    """
    image = await images_repo.get_by_id(session, image_id)
    if image is None:
        raise NotFoundError(message="资源不存在")

    # 关键一步：父工具必须对该用户可见。
    #
    # 这里分两种情况 —— 图片**不能**只走门户可见性，否则 owner 在自己的
    # 编辑器里看不到刚上传的草稿封面（草稿在门户是不可见的）。
    tool = await tools_repo.get_by_id(session, image.tool_id)
    if tool is None or tool.deleted_at is not None:
        raise NotFoundError(message="资源不存在")

    is_owner = access.principal is not None and tool.owner_id == access.principal.user_id
    is_approver = bool(
        access.roles & {RoleCode.APPROVER.value, RoleCode.SUPERADMIN.value}
    )
    if not (is_owner or is_approver):
        # 其他人必须满足门户可见性（含状态与 ACL）
        visible = await tools_repo.get_visible_by_id(
            session, image.tool_id, visibility=visibility
        )
        if visible is None:
            raise NotFoundError(message="资源不存在")

    relative = image.thumb_path if variant == "thumb" and image.thumb_path else image.storage_path
    if variant == "thumb" and not image.thumb_path:
        # 没有缩略图（历史数据）时退回原图，而不是报错
        relative = image.storage_path

    try:
        absolute = get_storage().absolute(relative)
    except Exception as exc:  # PathNotAllowedError 等
        raise NotFoundError(message="资源不存在") from exc
    if not absolute.is_file():
        raise NotFoundError(message="资源不存在")

    return FileResponse(
        absolute,
        media_type=image.mime_type,
        headers={
            # 图片内容不可变（按 id 寻址），可以长缓存；但 private 的图片
            # 不应被共享缓存留存，所以用 private
            "Cache-Control": "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
        },
    )


__all__ = ["router"]
