"""提交/撤回/下架/上架/批量批准/白名单 —— 状态机的写入侧。

与 `version_service` 的分工：
  - `version_service` 管「版本」的创建与审批后的版本切换
  - 本模块管「工具」自身的状态迁移（submit / withdraw / offline / relist）

两者都严格按 docs/01 §4.1 的迁移表实现，非法迁移一律 409 `STATE_CONFLICT`。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    AlreadyProcessedError,
    NotFoundError,
    StateConflictError,
    ValidationError,
)
from app.core.timeutil import utcnow
from app.models.enums import ApprovalAction, ToolStatus, VersionStatus
from app.models.tool import Tool
from app.repositories import approvals as approvals_repo
from app.repositories import tool_versions as versions_repo
from app.schemas.approval import (
    BatchApproveItemResult,
    BatchApproveResponse,
    WhitelistEntryOut,
)
from app.services import settings_service
from app.services.version_service import approve_tool

logger = logging.getLogger(__name__)

#: 允许提交审批的源状态（docs/01 §4.1）。
#:
#: `approved` 也在内：已发布的工具上传了新版本之后（上传时若未 auto_submit），
#: 需要显式提交才会把工具推进到 `pending_update`。
#: `pending` / `pending_update` 不在内 —— 那表示**已经**提交过了。
SUBMITTABLE: frozenset[str] = frozenset(
    {
        ToolStatus.DRAFT.value,
        ToolStatus.REJECTED.value,
        ToolStatus.APPROVED.value,
    }
)
#: 允许撤回的源状态
WITHDRAWABLE: frozenset[str] = frozenset({ToolStatus.PENDING.value})


@dataclass
class SubmitResult:
    status: str
    version_seq: int
    auto_approved: bool
    auto_approved_rule: str | None
    approval_record_id: int | None


async def submit_tool(
    session: AsyncSession,
    *,
    tool: Tool,
    actor_id: int,
    actor_label: str,
    request_id: str | None = None,
) -> SubmitResult:
    """`POST /me/tools/{id}/submit`（docs/01 §4.1：draft/rejected → pending）。

    自动放行（`auto_approve_all` 或免审白名单）在这条路径上直接进 `approved`，
    actor 记为**系统**、`is_automatic=true`、`auto_approved_rule` 记录命中原因。
    """
    if tool.status not in SUBMITTABLE:
        raise StateConflictError(
            message=f"当前状态（{tool.status}）不允许提交审批",
            details={"status": tool.status, "allowed": sorted(SUBMITTABLE)},
        )

    pending = await _require_submittable_version(session, tool)
    now = utcnow()
    from_status = tool.status
    # 已发布工具提交的是「新版本」→ 目标是 pending_update，不是 pending
    is_new_version = from_status == ToolStatus.APPROVED.value
    submitted_status = (
        ToolStatus.PENDING_UPDATE.value if is_new_version else ToolStatus.PENDING.value
    )
    decision = await settings_service.evaluate_auto_approval(session, user_id=actor_id)

    if decision.approved:
        # 自动放行：一步到位
        old_current = (
            await versions_repo.get_by_id(session, tool.current_version_id)
            if tool.current_version_id
            else None
        )
        from app.services.version_service import replace_current_version

        await replace_current_version(
            session, tool=tool, new_version=pending, old_current=old_current, now=now
        )
        pending.approved_by_id = None
        pending.auto_approved_rule = decision.rule
        tool.status = ToolStatus.APPROVED.value
        tool.pending_version_id = None
        tool.reject_reason = None
        if tool.published_at is None:
            tool.published_at = now
        tool.version_seq = (tool.version_seq or 0) + 1
        tool.updated_at = now
        await session.flush()

        record = await approvals_repo.insert_record(
            session,
            tool_id=tool.id,
            version_id=pending.id,
            action=ApprovalAction.SUBMIT,
            from_status=from_status,
            to_status=ToolStatus.APPROVED.value,
            version_from_status=VersionStatus.PENDING.value,
            version_to_status=VersionStatus.APPROVED.value,
            # 自动放行时 actor 记为系统（docs/01 §4.1 脚注 1）
            actor_id=None,
            actor_label="系统自动放行",
            is_automatic=True,
            auto_rule=decision.rule,
            note="命中自动放行规则",
            request_id=request_id,
            now=now,
        )
        await _sync_search_index(session, tool)
        await session.commit()
        return SubmitResult(
            status=tool.status,
            version_seq=tool.version_seq,
            auto_approved=True,
            auto_approved_rule=decision.rule,
            approval_record_id=record.id,
        )

    # 人工审批：进入队列
    tool.status = submitted_status
    tool.pending_version_id = pending.id
    tool.version_seq = (tool.version_seq or 0) + 1
    tool.updated_at = now
    await session.flush()

    action = (
        ApprovalAction.RESUBMIT
        if from_status == ToolStatus.REJECTED.value
        else ApprovalAction.SUBMIT
    )
    record = await approvals_repo.insert_record(
        session,
        tool_id=tool.id,
        version_id=pending.id,
        action=action,
        from_status=from_status,
        to_status=submitted_status,
        version_from_status=VersionStatus.PENDING.value,
        version_to_status=VersionStatus.PENDING.value,
        actor_id=actor_id,
        actor_label=actor_label,
        request_id=request_id,
        now=now,
    )
    await session.commit()
    return SubmitResult(
        status=tool.status,
        version_seq=tool.version_seq,
        auto_approved=False,
        auto_approved_rule=None,
        approval_record_id=record.id,
    )


async def _require_submittable_version(session: AsyncSession, tool: Tool) -> object:
    """提交前必须有可提交的版本，且该版本不能带 skill 解析错误。

    FR-TOOL-06：skill 解析失败「允许以非致命警告保存为草稿，但**不允许提交审批**」。
    这里用 `SKILL_PARSE_FAILED`（422），因为前端的既定处理就是「展示具体错误，
    允许另存草稿」—— 与用户此刻需要做的事完全对应。
    """
    from app.core.errors import SkillParseFailedError

    # 优先用工具上挂着的待审版本；没有就取最近一个未过审、非当前的版本
    candidate = (
        await versions_repo.get_by_id(session, tool.pending_version_id)
        if tool.pending_version_id
        else None
    )
    if candidate is None:
        versions = await versions_repo.list_for_tool(session, tool.id, order_desc=True)
        candidate = next(
            (
                v
                for v in versions
                if not v.is_current
                and v.status
                in (VersionStatus.PENDING.value, VersionStatus.REJECTED.value)
            ),
            None,
        )
    if candidate is None:
        raise StateConflictError(
            message="该工具还没有可提交的版本，请先上传内容",
            details={"tool_id": tool.id},
        )
    if candidate.skill_parse_error:
        raise SkillParseFailedError(
            message="Skill 包解析失败，无法提交审批；请修复后重新上传",
            details={
                "version": candidate.version,
                "parse_error": candidate.skill_parse_error,
            },
        )
    return candidate


async def withdraw_tool(
    session: AsyncSession,
    *,
    tool: Tool,
    actor_id: int,
    actor_label: str,
    request_id: str | None = None,
) -> dict:
    """`POST /me/tools/{id}/withdraw`（docs/01 §4.1：pending → draft）。"""
    if tool.status not in WITHDRAWABLE:
        raise StateConflictError(
            message=f"当前状态（{tool.status}）不允许撤回",
            details={"status": tool.status, "allowed": sorted(WITHDRAWABLE)},
        )

    now = utcnow()
    from_status = tool.status
    pending = (
        await versions_repo.get_by_id(session, tool.pending_version_id)
        if tool.pending_version_id
        else None
    )
    tool.status = ToolStatus.DRAFT.value
    tool.pending_version_id = None
    tool.version_seq = (tool.version_seq or 0) + 1
    tool.updated_at = now
    await session.flush()

    record = await approvals_repo.insert_record(
        session,
        tool_id=tool.id,
        version_id=pending.id if pending else None,
        action=ApprovalAction.WITHDRAW,
        from_status=from_status,
        to_status=ToolStatus.DRAFT.value,
        version_from_status=None,
        version_to_status=None,
        actor_id=actor_id,
        actor_label=actor_label,
        request_id=request_id,
        now=now,
    )
    await session.commit()
    return {"tool_id": tool.id, "status": tool.status, "approval_record_id": record.id}


async def offline_tool(
    session: AsyncSession,
    *,
    tool: Tool,
    reason: str,
    actor_id: int,
    actor_label: str,
    request_id: str | None = None,
) -> dict:
    """下架（FR-APPR-10：理由必填，下架后门户立即不可见）。"""
    if tool.status not in (ToolStatus.APPROVED.value, ToolStatus.PENDING_UPDATE.value):
        raise StateConflictError(
            message="只有已发布的工具可以下架", details={"status": tool.status}
        )
    now = utcnow()
    from_status = tool.status
    tool.status = ToolStatus.OFFLINE.value
    tool.offline_reason = reason
    # 下架时挂起的新版本一并撤回队列，避免恢复上架后状态混乱
    tool.pending_version_id = None
    tool.version_seq = (tool.version_seq or 0) + 1
    tool.updated_at = now
    await session.flush()

    record = await approvals_repo.insert_record(
        session,
        tool_id=tool.id,
        version_id=None,
        action=ApprovalAction.OFFLINE,
        from_status=from_status,
        to_status=ToolStatus.OFFLINE.value,
        actor_id=actor_id,
        actor_label=actor_label,
        reason=reason,
        request_id=request_id,
        now=now,
    )
    await session.commit()
    return {"tool_id": tool.id, "status": tool.status, "approval_record_id": record.id}


async def relist_tool(
    session: AsyncSession,
    *,
    tool: Tool,
    reason: str | None,
    actor_id: int,
    actor_label: str,
    request_id: str | None = None,
) -> dict:
    """重新上架（FR-APPR-11：理由选填）。"""
    if tool.status != ToolStatus.OFFLINE.value:
        raise StateConflictError(
            message="只有已下架的工具可以重新上架", details={"status": tool.status}
        )
    now = utcnow()
    tool.status = ToolStatus.APPROVED.value
    tool.offline_reason = None
    tool.version_seq = (tool.version_seq or 0) + 1
    tool.updated_at = now
    await session.flush()

    record = await approvals_repo.insert_record(
        session,
        tool_id=tool.id,
        version_id=None,
        action=ApprovalAction.RELIST,
        from_status=ToolStatus.OFFLINE.value,
        to_status=ToolStatus.APPROVED.value,
        actor_id=actor_id,
        actor_label=actor_label,
        reason=reason,
        request_id=request_id,
        now=now,
    )
    await session.commit()
    return {"tool_id": tool.id, "status": tool.status, "approval_record_id": record.id}


async def batch_approve(
    session: AsyncSession,
    *,
    tool_ids: list[int],
    note: str | None,
    actor_id: int,
    actor_label: str,
    request_id: str | None = None,
) -> BatchApproveResponse:
    """批量批准（FR-APPR-09）。

    **不提供批量驳回** —— 驳回必须逐条写理由。

    每条独立处理、独立提交：一条失败不影响其他条目，结果逐条回传。
    """
    from app.repositories import tools as tools_repo

    results: list[BatchApproveItemResult] = []
    succeeded = 0
    for tool_id in tool_ids:
        tool = await tools_repo.get_by_id(session, tool_id)
        if tool is None or tool.deleted_at is not None:
            results.append(
                BatchApproveItemResult(
                    tool_id=tool_id, ok=False, error_code="NOT_FOUND", message="工具不存在"
                )
            )
            continue
        try:
            limits = await settings_service.get_upload_limits(session)
            outcome = await approve_tool(
                session,
                tool=tool,
                version_id=None,
                note=note,
                expected_version_seq=None,
                actor_id=actor_id,
                actor_label=actor_label,
                request_id=request_id,
                limits=limits,
            )
            succeeded += 1
            results.append(
                BatchApproveItemResult(
                    tool_id=tool_id, ok=True, status=str(outcome["status"])
                )
            )
        except (StateConflictError, AlreadyProcessedError) as exc:
            await session.rollback()
            results.append(
                BatchApproveItemResult(
                    tool_id=tool_id, ok=False, error_code=exc.code, message=exc.message
                )
            )

    return BatchApproveResponse(
        succeeded=succeeded, failed=len(results) - succeeded, results=results
    )


# ---------------------------------------------------------------------------
# 免审白名单（FR-APPR-03/04）
# ---------------------------------------------------------------------------
async def list_whitelist(
    session: AsyncSession, *, now: datetime | None = None
) -> list[WhitelistEntryOut]:
    from app.core.timeutil import ensure_utc

    moment = now or utcnow()
    rows = await approvals_repo.list_whitelist(session)
    entries: list[WhitelistEntryOut] = []
    for row, user, adder_name in rows:
        expires = ensure_utc(row.expires_at)
        entries.append(
            WhitelistEntryOut(
                user_id=row.user_id,
                username=user.username,
                display_name=user.display_name,
                reason=row.reason,
                added_by_id=row.added_by_id,
                added_by_name=adder_name,
                expires_at=row.expires_at,
                created_at=row.created_at,
                is_effective=expires is None or expires > moment,
            )
        )
    return entries


async def add_to_whitelist(
    session: AsyncSession,
    *,
    user_id: int,
    reason: str | None,
    expires_at: datetime | None,
    actor_id: int,
) -> WhitelistEntryOut:
    from app.models.user import User

    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError(message=f"用户不存在：{user_id}", details={"user_id": user_id})
    if user.status != "active":
        raise ValidationError(
            message="不能把已禁用的用户加入白名单", details={"user_id": user_id}
        )

    now = utcnow()
    row = await approvals_repo.add_whitelist_entry(
        session,
        user_id=user_id,
        reason=reason,
        added_by_id=actor_id,
        expires_at=expires_at,
        now=now,
    )
    await session.commit()
    return WhitelistEntryOut(
        user_id=row.user_id,
        username=user.username,
        display_name=user.display_name,
        reason=row.reason,
        added_by_id=row.added_by_id,
        expires_at=row.expires_at,
        created_at=row.created_at,
        is_effective=True,
    )


async def remove_from_whitelist(session: AsyncSession, *, user_id: int) -> None:
    removed = await approvals_repo.remove_whitelist_entry(session, user_id)
    if not removed:
        raise NotFoundError(
            message=f"白名单中没有该用户：{user_id}", details={"user_id": user_id}
        )
    await session.commit()


async def _sync_search_index(session: AsyncSession, tool: Tool) -> None:
    from app.search import SearchDocument, get_search_backend

    await get_search_backend().upsert(
        session,
        tool.id,
        SearchDocument(
            name=tool.name,
            summary=tool.summary,
            description=tool.description_md,
            tags=[t.display_name for t in tool.tags],
        ),
    )


__all__ = [
    "SUBMITTABLE",
    "WITHDRAWABLE",
    "SubmitResult",
    "add_to_whitelist",
    "batch_approve",
    "list_whitelist",
    "offline_tool",
    "relist_tool",
    "remove_from_whitelist",
    "submit_tool",
    "withdraw_tool",
]
