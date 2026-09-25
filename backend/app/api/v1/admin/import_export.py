"""批量导入导出路由（docs/03 §2.5 的 4 个接口 / FR-API-08/09）。

两条硬要求：

  - **导出 CSV 必须带 UTF-8 BOM**：否则 Excel 打开中文是乱码。
    内网用户大量用 Excel，这条很实际。
  - **`generated_passwords` 绝不写日志**：这是全平台唯一的明文回显例外。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.admin._guards import admin_all_guard
from app.core.deps import Principal
from app.db.session import get_db
from app.schemas.admin import ImportResultResponse, ToolImportRequest
from app.services import import_export_service

router = APIRouter(prefix="/admin", tags=["admin"])

#: 导出响应头。`Content-Disposition` 用 RFC 5987 形式，中文文件名才不炸。
def _attachment_header(filename: str) -> dict[str, str]:
    from urllib.parse import quote

    return {
        "Content-Disposition": (
            f"attachment; filename=\"{filename}\"; "
            f"filename*=UTF-8''{quote(filename, safe='')}"
        ),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
    }


@router.post(
    "/import/users", response_model=ImportResultResponse, summary="批量导入用户（CSV）"
)
async def import_users(
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(admin_all_guard)],
    file: Annotated[UploadFile, File()],
    dry_run: Annotated[bool, Form()] = False,
    on_conflict: Annotated[str, Form()] = "skip",
) -> ImportResultResponse:
    """docs/03 §3.14。

    `dry_run=true` 时**不写库、不生成密码**，只返回预演报告。
    部分失败不影响成功行，`errors` 精确到行与字段。
    """
    content = await file.read()
    return await import_export_service.import_users_csv(
        session,
        content=content,
        dry_run=dry_run,
        on_conflict=on_conflict,
        actor_id=principal.user_id,
    )


@router.get("/export/users", summary="导出用户 CSV（带 UTF-8 BOM）")
async def export_users(
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(admin_all_guard)],
) -> StreamingResponse:
    """流式导出，**不含密码哈希**（FR-IAM-10）。

    第一块就带 BOM，保证客户端拿到的前 3 字节一定是 `EF BB BF`。
    """
    del principal
    return StreamingResponse(
        import_export_service.export_users_csv(session),
        media_type="text/csv; charset=utf-8",
        headers=_attachment_header("users.csv"),
    )


@router.post(
    "/import/tools", response_model=ImportResultResponse, summary="批量导入工具（JSON）"
)
async def import_tools(
    body: ToolImportRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(admin_all_guard)],
) -> ImportResultResponse:
    """按 **slug** 判重；`on_conflict` 支持 skip / update / fail。"""
    return await import_export_service.import_tools_json(
        session,
        items=[item.model_dump() for item in body.items],
        dry_run=body.dry_run,
        on_conflict=body.on_conflict,
        actor_id=principal.user_id,
    )


@router.get("/export/tools", summary="导出工具元数据（JSON）")
async def export_tools(
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(admin_all_guard)],
) -> StreamingResponse:
    """流式导出（分批查询，不把全表载入内存）。"""
    del principal
    return StreamingResponse(
        import_export_service.export_tools_json(session),
        media_type="application/json; charset=utf-8",
        headers=_attachment_header("tools.json"),
    )


__all__ = ["router"]
