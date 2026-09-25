"""审批与治理路由（M2 的 12 个接口）。

    GET    /admin/approvals
    POST   /admin/approvals/{tool_id}/approve|reject|offline|relist
    POST   /admin/approvals/batch-approve
    GET    /admin/approvals/history
    GET    /admin/approval-whitelist
    POST   /admin/approval-whitelist
    DELETE /admin/approval-whitelist/{user_id}
    GET    /admin/settings
    PUT    /admin/settings

全部要求 `approvals:write`（= approver / superadmin）。白名单与系统设置
额外要求 superadmin（docs/03 §1.4：`settings:write`）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Principal, require_roles_and_scope
from app.core.errors import NotFoundError
from app.core.pagination import Page, PageParams
from app.db.session import get_db
from app.models.enums import ApiScope, ApprovalAction, RoleCode
from app.repositories import approvals as approvals_repo
from app.repositories import tool_versions as versions_repo
from app.repositories import tools as tools_repo
from app.schemas.approval import (
    ApprovalQueueItem,
    ApprovalRecordOut,
    ApproveRequest,
    ApproveResponse,
    BatchApproveRequest,
    BatchApproveResponse,
    CurrentVersionBrief,
    OfflineRequest,
    OfflineResponse,
    PendingVersionBrief,
    RejectRequest,
    RejectResponse,
    RelistRequest,
    RelistResponse,
    SupersededVersionBrief,
    WhitelistEntryIn,
    WhitelistEntryOut,
)
from app.schemas.setting import SettingListResponse, SettingUpdateRequest
from app.services import approval_service, settings_service, version_service

#: 审批队列/审批动作：**角色与 Scope 都要**。
#:
#: 只查 scope 是不够的 —— `require_scope` 对 JWT 请求一律放行（角色由
#: `require_role` 负责），所以单独用它会让 viewer 也能进审批队列。
#: 只查角色也不够 —— API Token 的 `roles` 是创建者的角色，会绕过 scopes 限制。
approver_guard = require_roles_and_scope(
    RoleCode.APPROVER.value,
    RoleCode.SUPERADMIN.value,
    scope=ApiScope.APPROVALS_WRITE.value,
)
#: 系统设置与免审白名单需要 superadmin（docs/03 §1.4）
settings_guard = require_roles_and_scope(
    RoleCode.SUPERADMIN.value, scope=ApiScope.SETTINGS_WRITE.value
)

router = APIRouter(prefix="/admin", tags=["admin"])

APPROVAL_ACTIONS = frozenset(a.value for a in ApprovalAction)


# ===========================================================================
# 审批队列
# ===========================================================================
@router.get("/approvals", summary="审批队列")
async def list_approvals(
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(approver_guard)],
    params: Annotated[PageParams, Depends()],
    status: Annotated[
        str | None, Query(description="pending / pending_update / pending_all")
    ] = None,
    tool_type: Annotated[str | None, Query()] = None,
    owner: Annotated[str | None, Query(description="按提交人 username 过滤")] = None,
) -> Page[ApprovalQueueItem]:
    """`sort` 固定为提交时间**正序**（FR-APPR-05：先提交先处理），
    所以本接口不提供 sort 参数 —— 队列的顺序是业务规则，不是用户偏好。
    """
    del principal
    rows = await approvals_repo.list_queue(
        session,
        status=status,
        tool_type=tool_type,
        owner_username=(owner or "").strip().lower() or None,
        limit=params.limit,
        offset=params.offset,
    )
    total = await approvals_repo.count_queue(
        session,
        status=status,
        tool_type=tool_type,
        owner_username=(owner or "").strip().lower() or None,
    )
    now = _now()
    submitted = await approvals_repo.latest_submission_times(session, [t.id for t in rows])

    items: list[ApprovalQueueItem] = []
    for tool in rows:
        pending = (
            await versions_repo.get_by_id(session, tool.pending_version_id)
            if tool.pending_version_id
            else None
        )
        current = (
            await versions_repo.get_by_id(session, tool.current_version_id)
            if tool.current_version_id
            else None
        )
        submitted_at = submitted.get(tool.id) or tool.updated_at
        waiting_hours = 0.0
        if submitted_at is not None:
            from app.core.timeutil import ensure_utc

            moment = ensure_utc(submitted_at)
            if moment is not None:
                waiting_hours = round((now - moment).total_seconds() / 3600, 1)

        items.append(
            ApprovalQueueItem(
                tool_id=tool.id,
                tool_slug=tool.slug,
                tool_name=tool.name,
                tool_type=tool.tool_type,
                summary=tool.summary,
                visibility=tool.visibility,
                category=(
                    {
                        "id": tool.category.id,
                        "slug": tool.category.slug,
                        "name": tool.category.name,
                        "icon": tool.category.icon,
                    }
                    if tool.category is not None
                    else None
                ),
                tags=[t.display_name for t in tool.tags],
                # 让审批人一眼区分「新工具」与「版本更新」（docs/03 §3.8）
                submission_type=(
                    "new_version"
                    if tool.status == "pending_update" or tool.current_version_id
                    else "new_tool"
                ),
                status=tool.status,
                pending_version=(
                    PendingVersionBrief(
                        id=pending.id,
                        version=pending.version,
                        changelog_md=pending.changelog_md,
                        file_name=pending.file_name,
                        file_size=pending.file_size,
                        file_sha256=pending.file_sha256,
                    )
                    if pending is not None
                    else None
                ),
                current_version=(
                    CurrentVersionBrief(id=current.id, version=current.version)
                    if current is not None
                    else None
                ),
                owner={
                    "id": tool.owner.id,
                    "username": tool.owner.username,
                    "display_name": tool.owner.display_name,
                },
                submitted_at=submitted_at,
                waiting_hours=waiting_hours,
            )
        )

    return Page.build(items, total, params)


def _now() -> datetime:
    from app.core.timeutil import utcnow

    return utcnow()


# ===========================================================================
# 审批动作
# ===========================================================================
async def _load_tool(session: AsyncSession, tool_id: int):
    tool = await tools_repo.get_by_id(session, tool_id)
    if tool is None or tool.deleted_at is not None:
        raise NotFoundError(message="工具不存在", details={"tool_id": tool_id})
    return tool


@router.post("/approvals/{tool_id}/approve", response_model=ApproveResponse, summary="批准")
async def approve(
    tool_id: int,
    body: ApproveRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(approver_guard)],
) -> ApproveResponse:
    tool = await _load_tool(session, tool_id)
    limits = await settings_service.get_upload_limits(session)
    outcome = await version_service.approve_tool(
        session,
        tool=tool,
        version_id=body.version_id,
        note=body.note,
        expected_version_seq=body.expected_version_seq,
        actor_id=principal.user_id,
        actor_label=principal.display_name,
        request_id=request.state.request_id,
        limits=limits,
    )
    current = outcome["current_version"]
    superseded = outcome["superseded_version"]
    return ApproveResponse(
        tool_id=outcome["tool_id"],
        status=outcome["status"],
        version_seq=outcome["version_seq"],
        current_version=CurrentVersionBrief(id=current.id, version=current.version),
        superseded_version=(
            SupersededVersionBrief(id=superseded.id, version=superseded.version)
            if superseded is not None
            else None
        ),
        purged_versions=[
            SupersededVersionBrief(id=v.id, version=v.version) for v in outcome["purged_versions"]
        ],
        approval_record_id=outcome["approval_record_id"],
    )


@router.post("/approvals/{tool_id}/reject", response_model=RejectResponse, summary="驳回")
async def reject(
    tool_id: int,
    body: RejectRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(approver_guard)],
) -> RejectResponse:
    """驳回。

    **注意返回的 `status`**：驳回「新版本」时工具仍是 `approved`
    （docs/03 §3.10 的语义陷阱），只有驳回首次提交才是 `rejected`。
    """
    tool = await _load_tool(session, tool_id)
    outcome = await version_service.reject_tool(
        session,
        tool=tool,
        reason=body.reason,
        version_id=body.version_id,
        actor_id=principal.user_id,
        actor_label=principal.display_name,
        request_id=request.state.request_id,
    )
    rejected = outcome["rejected_version"]
    return RejectResponse(
        tool_id=outcome["tool_id"],
        status=outcome["status"],
        pending_version=None,
        rejected_version=SupersededVersionBrief(id=rejected.id, version=rejected.version),
        approval_record_id=outcome["approval_record_id"],
    )


@router.post("/approvals/{tool_id}/offline", response_model=OfflineResponse, summary="下架")
async def offline(
    tool_id: int,
    body: OfflineRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(approver_guard)],
) -> OfflineResponse:
    tool = await _load_tool(session, tool_id)
    outcome = await approval_service.offline_tool(
        session,
        tool=tool,
        reason=body.reason,
        actor_id=principal.user_id,
        actor_label=principal.display_name,
        request_id=request.state.request_id,
    )
    return OfflineResponse(**outcome)


@router.post("/approvals/{tool_id}/relist", response_model=RelistResponse, summary="重新上架")
async def relist(
    tool_id: int,
    body: RelistRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(approver_guard)],
) -> RelistResponse:
    tool = await _load_tool(session, tool_id)
    outcome = await approval_service.relist_tool(
        session,
        tool=tool,
        reason=body.reason,
        actor_id=principal.user_id,
        actor_label=principal.display_name,
        request_id=request.state.request_id,
    )
    return RelistResponse(**outcome)


@router.post("/approvals/batch-approve", response_model=BatchApproveResponse, summary="批量批准")
async def batch_approve(
    body: BatchApproveRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(approver_guard)],
) -> BatchApproveResponse:
    """**不提供批量驳回** —— 驳回必须逐条写理由（FR-APPR-09）。"""
    return await approval_service.batch_approve(
        session,
        tool_ids=body.tool_ids,
        note=body.note,
        actor_id=principal.user_id,
        actor_label=principal.display_name,
        request_id=request.state.request_id,
    )


# ===========================================================================
# 审批历史
# ===========================================================================
@router.get("/approvals/history", summary="审批历史")
async def approval_history(
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(approver_guard)],
    params: Annotated[PageParams, Depends()],
    tool_id: Annotated[int | None, Query()] = None,
    actor_id: Annotated[int | None, Query()] = None,
    action: Annotated[str | None, Query()] = None,
    date_from: Annotated[datetime | None, Query()] = None,
    date_to: Annotated[datetime | None, Query()] = None,
) -> Page[ApprovalRecordOut]:
    """可按工具、操作人、动作类型、时间范围筛选（FR-APPR-12）。"""
    del principal
    if action is not None and action not in APPROVAL_ACTIONS:
        from app.core.errors import ValidationError

        raise ValidationError(
            message="动作类型非法",
            fields=[
                {
                    "field": "action",
                    "message": f"未知动作：{action}",
                }
            ],
        )

    rows = await approvals_repo.list_history(
        session,
        tool_id=tool_id,
        actor_id=actor_id,
        action=action,
        date_from=date_from,
        date_to=date_to,
        limit=params.limit,
        offset=params.offset,
    )
    total = await approvals_repo.count_history(
        session,
        tool_id=tool_id,
        actor_id=actor_id,
        action=action,
        date_from=date_from,
        date_to=date_to,
    )

    items: list[ApprovalRecordOut] = []
    for record in rows:
        tool = await tools_repo.get_by_id(session, record.tool_id)
        version = (
            await versions_repo.get_by_id(session, record.version_id)
            if record.version_id
            else None
        )
        items.append(
            ApprovalRecordOut(
                id=record.id,
                tool_id=record.tool_id,
                tool_name=tool.name if tool else None,
                tool_slug=tool.slug if tool else None,
                version_id=record.version_id,
                version=version.version if version else None,
                action=record.action,
                from_status=record.from_status,
                to_status=record.to_status,
                version_from_status=record.version_from_status,
                version_to_status=record.version_to_status,
                actor_id=record.actor_id,
                # 快照，不是 JOIN 出来的当前显示名
                actor_label=record.actor_label,
                is_automatic=record.is_automatic,
                auto_rule=record.auto_rule,
                reason=record.reason,
                note=record.note,
                created_at=record.created_at,
            )
        )
    return Page.build(items, total, params)


# ===========================================================================
# 免审白名单
# ===========================================================================
@router.get("/approval-whitelist", response_model=list[WhitelistEntryOut], summary="免审白名单")
async def list_whitelist(
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(settings_guard)],
) -> list[WhitelistEntryOut]:
    del principal
    return await approval_service.list_whitelist(session)


@router.post(
    "/approval-whitelist",
    response_model=WhitelistEntryOut,
    status_code=201,
    summary="加入免审白名单",
)
async def add_whitelist(
    body: WhitelistEntryIn,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(settings_guard)],
) -> WhitelistEntryOut:
    return await approval_service.add_to_whitelist(
        session,
        user_id=body.user_id,
        reason=body.reason,
        expires_at=body.expires_at,
        actor_id=principal.user_id,
    )


@router.delete("/approval-whitelist/{user_id}", status_code=200, summary="移出免审白名单")
async def remove_whitelist(
    user_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(settings_guard)],
) -> dict[str, str]:
    del principal
    await approval_service.remove_from_whitelist(session, user_id=user_id)
    return {"status": "ok"}


# ===========================================================================
# 系统设置
# ===========================================================================
@router.get("/settings", response_model=SettingListResponse, summary="系统设置")
async def get_settings(
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(settings_guard)],
) -> SettingListResponse:
    del principal
    return SettingListResponse(items=await settings_service.list_settings(session))


@router.put("/settings", response_model=SettingListResponse, summary="批量更新设置")
async def put_settings(
    body: SettingUpdateRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(settings_guard)],
) -> SettingListResponse:
    """批量**部分**更新；任何一项非法则整体回滚（docs/03 §3.13）。"""
    warnings = await settings_service.update_settings(
        session,
        updates=[(item.key, item.value) for item in body.items],
        actor_id=principal.user_id,
    )
    return SettingListResponse(
        items=await settings_service.list_settings(session), warnings=warnings
    )


__all__ = ["router"]
