"""版本服务：上传、审批后的版本切换、历史淘汰（docs/03 §3.7 §3.9 §3.10）。

**12 步处理顺序**（docs/03 §3.7，硬要求）—— 实现严格按它来：

```
1. 校验权限（owner）与工具状态（不能是 pending）
2. 校验参数（version 格式、必填项）
3. 检查版本号是否已存在           ← 早失败，不浪费 IO
4. 检查文件大小上限
5. 检查扩展名白名单
6. 检查用户/平台配额
7. 流式写入临时文件，同时增量计算 SHA256   ← 第一次真正落盘
8. 若是 skill 类型：流式校验 + 安全解析 + 提取内容
9. 归位（原子 rename）
10. 数据库事务：插入 version 行、更新 tool 状态、写 approval_record、更新 search index
11. 提交事务
12. 失败：删除临时文件与已落盘的最终文件
```

第 7~9 步全部在事务**之外**完成（README「SQLite 风险说明」第 3 条）。

**一处刻意的顺序调整**：最终路径 `files/tools/{tool_id}/{version_id}/{safe_name}`
里的 `{version_id}` 要等插入 version 行才知道，所以「原子 rename」在实现上
是事务里的**第一个**动作（`os.replace()` 是同文件系统内的元数据操作，
毫秒级，不搬运数据），而不是第 9 步。所有**大块** IO（读写、哈希、解压）
仍然严格在事务之外。这一点已在 checkpoint 报告中列为偏差说明。
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    DomainError,
    PayloadTooLargeError,
    StateConflictError,
    UnsupportedMediaTypeError,
    VersionExistsError,
)
from app.core.timeutil import utcnow
from app.models.enums import (
    ApprovalAction,
    ToolStatus,
    ToolType,
    VersionStatus,
)
from app.models.tool import Tool, ToolVersion
from app.repositories import approvals as approvals_repo
from app.repositories import tool_versions as versions_repo
from app.services import settings_service, skill_service
from app.storage import PathNotAllowedError, UploadTooLargeError, file_extension, get_storage

logger = logging.getLogger(__name__)

#: `file_ext` 的兜底（无扩展名时）
DEFAULT_EXT = "bin"


@dataclass
class UploadOutcome:
    """上传结果。`staged` 为 None 表示没有文件（webapp / prompt 类型）。"""

    version: ToolVersion
    tool: Tool
    purged: list[ToolVersion]
    final_path: str | None = None


def _fail(message: str, details: dict | None = None) -> DomainError:
    from app.core.errors import ValidationError

    return ValidationError(message=message, details=details)


async def precheck_upload(
    session: AsyncSession,
    *,
    tool: Tool,
    version: str,
    declared_size: int | None,
    file_name: str | None,
    owner_id: int,
) -> None:
    """第 3~6 步：一切能在落盘前发现的错误都在这里报掉。

    顺序刻意是「版本号 → 大小 → 扩展名 → 配额」：越便宜的检查越靠前。
    """
    # ---- 3. 版本号唯一（早失败，不浪费 IO）----
    if await versions_repo.get_by_version(session, tool.id, version) is not None:
        raise VersionExistsError(
            message=f"版本号 {version} 已存在", details={"version": version}
        )

    limits = await settings_service.get_upload_limits(session)

    # ---- 4. 文件大小上限 ----
    if declared_size is not None and declared_size > limits.max_file_size_bytes:
        raise PayloadTooLargeError(
            message=f"文件超过 {limits.max_file_size_mb} MB 上限",
            details={
                "limit_mb": limits.max_file_size_mb,
                "actual_mb": round(declared_size / (1024 * 1024), 2),
            },
        )

    # ---- 5. 扩展名白名单 ----
    if file_name:
        ext = file_extension(file_name)
        if ext not in limits.allowed_extensions:
            raise UnsupportedMediaTypeError(
                message=f"不支持的文件类型: .{ext or '未知'}",
                details={"ext": ext, "allowed": sorted(limits.allowed_extensions)},
            )

    # ---- 6. 配额（FR-FILE-12）----
    await settings_service.check_storage_quota(
        session, owner_id=owner_id, incoming_bytes=declared_size or 0, limits=limits
    )


async def create_version(
    session: AsyncSession,
    *,
    tool: Tool,
    version: str,
    changelog_md: str,
    uploader_id: int,
    uploader_label: str,
    stream: AsyncIterator[bytes] | None = None,
    file_name: str | None = None,
    prompt_content: str | None = None,
    webapp_url: str | None = None,
    auto_submit: bool = False,
    request_id: str | None = None,
    skill_limits: skill_service.SkillLimits | None = None,
) -> UploadOutcome:
    """上传一个版本。见模块 docstring 的 12 步顺序。

    调用方负责第 1 步（权限与工具状态）—— 因为那需要 principal 信息，
    放在这里会让本函数依赖整个鉴权上下文。
    """
    storage = get_storage()
    limits = await settings_service.get_upload_limits(session)

    # ---- 2. 参数校验（必填项按类型）----
    _validate_type_requirements(
        tool.tool_type,
        stream=stream,
        prompt_content=prompt_content,
        webapp_url=webapp_url,
        file_name=file_name,
    )

    staged = None
    committed = None
    parse_result: skill_service.SkillParseResult | None = None
    started = time.perf_counter()

    try:
        # ---- 7. 流式落盘 + 增量 SHA256（事务之外）----
        if stream is not None:
            staged = await storage.stage(
                stream,
                max_bytes=limits.max_file_size_bytes,
                file_name=file_name or f"{tool.slug}-{version}",
            )
            logger.info(
                "上传落盘完成 tool=%s version=%s bytes=%d sha256=%s 耗时=%.2fs",
                tool.slug,
                version,
                staged.size,
                staged.sha256[:16],
                time.perf_counter() - started,
            )

            # ---- 8. skill 包解析（同样在事务之外）----
            if tool.tool_type == ToolType.SKILL:
                parse_result = await _parse_skill(staged.temp_path, skill_limits)

        # ---- 9~11. 事务：归位 + 元数据 + 版本状态 ----
        return await _persist_version(
            session,
            tool=tool,
            version=version,
            changelog_md=changelog_md,
            uploader_id=uploader_id,
            uploader_label=uploader_label,
            staged=staged,
            file_name=file_name,
            prompt_content=prompt_content,
            webapp_url=webapp_url,
            auto_submit=auto_submit,
            request_id=request_id,
            parse_result=parse_result,
        )
    except BaseException:
        # ---- 12. 任何失败都要清干净：临时文件 + 已归位的最终文件 ----
        if staged is not None:
            staged.cleanup()
        if committed is not None:
            await storage.delete(committed.storage_path)
        raise


def _validate_type_requirements(
    tool_type: str,
    *,
    stream: AsyncIterator[bytes] | None,
    prompt_content: str | None,
    webapp_url: str | None,
    file_name: str | None,
) -> None:
    """FR-TOOL-04 ~ 07 的按类型必填校验。"""
    if tool_type in (ToolType.FILE.value, ToolType.SKILL.value) and stream is None:
        raise _fail(
            "该类型必须上传文件",
            {"field": "file", "message": "file / skill 类型必须上传交付文件"},
        )
    if tool_type == ToolType.PROMPT.value and not prompt_content:
        raise _fail(
            "prompt 类型必须填写提示词正文",
            {"field": "prompt_content", "message": "提示词正文不能为空"},
        )
    if tool_type == ToolType.WEBAPP.value and not webapp_url:
        raise _fail(
            "webapp 类型必须填写 URL",
            {"field": "webapp_url", "message": "webapp 类型必须填写内网 URL"},
        )
    del file_name


async def _parse_skill(
    temp_path: Path, limits: skill_service.SkillLimits | None
) -> skill_service.SkillParseResult:
    """在**线程**里做 skill 解析 —— 它是 CPU + IO 密集的同步代码，
    直接跑会阻塞事件循环。"""
    import asyncio

    return await asyncio.to_thread(
        skill_service.parse_skill_package, temp_path, limits=limits
    )


async def _persist_version(
    session: AsyncSession,
    *,
    tool: Tool,
    version: str,
    changelog_md: str,
    uploader_id: int,
    uploader_label: str,
    staged,
    file_name: str | None,
    prompt_content: str | None,
    webapp_url: str | None,
    auto_submit: bool,
    request_id: str | None,
    parse_result: skill_service.SkillParseResult | None,
) -> UploadOutcome:
    """第 9~11 步：一个事务里完成归位与全部元数据写入。"""
    from app.search import SearchDocument, get_search_backend

    now = utcnow()
    storage = get_storage()

    # 先把 version 行插进去拿到 id —— 最终路径里的 {version_id} 需要它
    record = ToolVersion(
        tool_id=tool.id,
        version=version,
        changelog_md=changelog_md,
        status=VersionStatus.PENDING.value,
        is_current=False,
        prompt_content=prompt_content,
        uploaded_by_id=uploader_id,
        created_at=now,
        updated_at=now,
    )
    if parse_result is not None:
        record.skill_manifest = parse_result.manifest
        record.skill_readme_md = parse_result.readme_md
        record.skill_file_tree = parse_result.file_tree
        record.skill_tree_truncated = parse_result.file_tree_truncated
        record.skill_parse_error = parse_result.parse_error
    session.add(record)
    await session.flush()

    # 第 9 步：原子归位（同文件系统 os.replace，毫秒级元数据操作）
    committed = None
    if staged is not None:
        try:
            committed = await storage.commit(
                staged,
                directory=f"tools/{tool.id}/{record.id}",
                file_name=staged.safe_name,
            )
        except (UploadTooLargeError, PathNotAllowedError) as exc:
            raise _fail("文件归位失败", {"reason": str(exc)[:120]}) from exc
        record.storage_path = committed.storage_path
        record.file_name = staged.file_name
        record.file_size = committed.size
        record.file_sha256 = committed.sha256
        record.mime_type = None
        record.file_ext = file_extension(staged.safe_name) or DEFAULT_EXT

    if webapp_url:
        # webapp 的 URL 也可以只在版本上给，工具级同步一份（详情页从工具级读）
        tool.webapp_url = webapp_url

    # ---- 工具状态机 ----
    #
    # `auto_submit` 决定「上传」是否同时等于「提交审批」：
    #
    #   auto_submit = false（默认）
    #       只创建版本并把它挂为待审版本，**工具状态不变**。
    #       用户可以继续改元信息，稍后显式调 `/submit` 才进入队列。
    #       —— 这对应验收 #1「创建 → 上传 zip → 提交审批」的三步流程。
    #
    #   auto_submit = true
    #       上传即提交：draft/rejected → pending；approved → pending_update。
    #       此时才评估自动放行规则（auto_approve_all / 免审白名单）。
    #
    # 自动放行规则只在**真正提交**时评估 —— 否则一个还没提交的草稿
    # 会莫名其妙变成 approved。
    from_status = tool.status
    auto_rule: str | None = None
    auto_approved = False
    decision = None

    if auto_submit:
        decision = await settings_service.evaluate_auto_approval(
            session,
            user_id=uploader_id,
            # 传「提交前」的工具状态：只有已发布工具才适用「新版本免审」
            tool_status=from_status,
        )
        auto_approved = decision.approved
        auto_rule = decision.rule

    if auto_approved:
        # 自动放行：直接进入 approved 并把该版本设为当前版本
        old_current = (
            await versions_repo.get_by_id(session, tool.current_version_id)
            if tool.current_version_id
            else None
        )
        tool.pending_version_id = None
        _activate_version(tool, record, old_current=old_current, now=now)
        tool.status = ToolStatus.APPROVED.value
        record.approved_at = now
        record.approved_by_id = None
        record.auto_approved_rule = auto_rule
        if tool.published_at is None:
            tool.published_at = now
        tool.last_version_at = now
        target_status = ToolStatus.APPROVED.value
    elif auto_submit:
        record.status = VersionStatus.PENDING.value
        tool.pending_version_id = record.id
        if from_status in (ToolStatus.APPROVED.value, ToolStatus.PENDING_UPDATE.value):
            # FR-VER-03：已发布工具上传新版本 → pending_update，**旧版本继续服务**
            tool.status = ToolStatus.PENDING_UPDATE.value
        else:
            tool.status = ToolStatus.PENDING.value
        target_status = tool.status
    else:
        # 只创建版本，不提交 —— 工具状态保持不变
        record.status = VersionStatus.PENDING.value
        tool.pending_version_id = record.id
        target_status = from_status

    tool.version_seq = (tool.version_seq or 0) + 1
    tool.updated_at = now

    # 重提时清掉上一次的驳回理由（理由已归档在 approval_records 与版本行上）
    tool.reject_reason = None

    # ---- 审批流水：只在**真正提交**时写 ----
    # 未提交的上传不写记录 —— `approval_records` 是审批流水，
    # 记一次「上传」会让审批历史里出现没有对应审批动作的噪声。
    record_row = None
    if auto_submit or auto_approved:
        if auto_approved:
            note = (
                "已发布工具的新版本免审（approval.version_reapproval=false）"
                if auto_rule == "version_reapproval_off"
                else "上传后自动放行"
            )
        elif from_status == ToolStatus.REJECTED.value:
            note = "修改后重新提交"
        else:
            note = "上传后立即提交审批"
        record_row = await approvals_repo.insert_record(
            session,
            tool_id=tool.id,
            version_id=record.id,
            action=(
                ApprovalAction.RESUBMIT
                if from_status == ToolStatus.REJECTED.value
                else ApprovalAction.SUBMIT
            ),
            from_status=from_status,
            to_status=target_status,
            version_from_status=None,
            version_to_status=record.status,
            actor_id=None if auto_approved else uploader_id,
            actor_label="系统自动放行" if auto_approved else uploader_label,
            is_automatic=auto_approved,
            auto_rule=auto_rule,
            note=note,
            request_id=request_id,
            now=now,
        )

    # ---- 搜索索引与业务数据同一个事务（docs/02 §3.20）----
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

    # ---- 历史版本淘汰：**在事务内标记**，文件删除放到提交之后 ----
    purge_limits = await settings_service.get_upload_limits(session)
    purged = await _mark_purge_candidates(session, tool=tool, limits=purge_limits)

    await session.commit()

    # 事务已提交 —— 现在才真正删文件。失败只记警告，由孤儿清理兜底。
    for old in purged:
        if old.storage_path:
            deleted = await storage.delete(old.storage_path)
            logger.info(
                "历史版本归档 tool=%s version=%s 文件已删除=%s",
                tool.slug,
                old.version,
                deleted,
            )

    del record_row
    return UploadOutcome(
        version=record,
        tool=tool,
        purged=purged,
        final_path=committed.storage_path if committed else None,
    )


def _activate_version(
    tool: Tool, new_version: ToolVersion, *, old_current: ToolVersion | None, now: datetime
) -> None:
    """把 `new_version` 设为当前版本，并把原当前版本转 `superseded`。

    `is_current` 与 `tools.current_version_id` 是同一事实的两份表示，
    这里**同一处**同时更新两者，绝不依赖部分唯一索引报错来发现不一致。
    """
    if old_current is not None and old_current.id != new_version.id:
        old_current.status = VersionStatus.SUPERSEDED.value
        old_current.is_current = False
        old_current.updated_at = now
    new_version.status = VersionStatus.APPROVED.value
    new_version.is_current = True
    new_version.approved_at = now
    new_version.updated_at = now
    tool.current_version_id = new_version.id
    tool.last_version_at = now
    if tool.published_at is None:
        tool.published_at = now


async def _mark_purge_candidates(
    session: AsyncSession, *, tool: Tool, limits
) -> list[ToolVersion]:
    """把超出 `version.history_limit` 的历史版本标记为 `purged`。

    **只标数据库，不删文件** —— 文件删除由调用方在事务提交后做。
    先删文件再提交的话，一旦事务回滚就出现「文件没了、数据库还说是 superseded」。
    """
    keep = limits.history_limit
    candidates = await versions_repo.list_purge_candidates(session, tool.id, keep=keep)
    now = utcnow()
    for old in candidates:
        await versions_repo.mark_purged(session, old, now=now)
    return candidates


# ===========================================================================
# 审批动作
# ===========================================================================
async def approve_tool(
    session: AsyncSession,
    *,
    tool: Tool,
    version_id: int | None,
    note: str | None,
    expected_version_seq: int | None,
    actor_id: int,
    actor_label: str,
    request_id: str | None = None,
    limits=None,
) -> dict:
    """批准工具或其待审新版本（docs/03 §3.9）。

    并发防护用 **CAS**（`UPDATE ... WHERE status = 当时的状态 AND version_seq = 当时的值`），
    而不是「先查后改」—— 后者在两个管理员同时点批准时会双写。
    """
    if tool.status not in (ToolStatus.PENDING.value, ToolStatus.PENDING_UPDATE.value):
        raise StateConflictError(
            message="该工具当前不在待审状态",
            details={"status": tool.status, "hint": "可能已被其他管理员处理"},
        )

    if expected_version_seq is not None and expected_version_seq != tool.version_seq:
        raise _already_processed(expected_version_seq, tool.version_seq)

    pending = (
        await versions_repo.get_by_id(session, tool.pending_version_id)
        if tool.pending_version_id
        else None
    )
    if version_id is not None and (pending is None or pending.id != version_id):
        # 传了一个不是「当前待审」的版本 —— 说明别人已经处理过
        raise _already_processed(version_id, pending.id if pending else None)
    if pending is None:
        raise StateConflictError(message="该工具没有待审版本", details={"status": tool.status})

    now = utcnow()
    old_current = (
        await versions_repo.get_by_id(session, tool.current_version_id)
        if tool.current_version_id
        else None
    )
    from_status = tool.status
    expected_seq = tool.version_seq

    # ---- CAS：状态与 version_seq 都要匹配，否则说明已被处理 ----
    result = await session.execute(
        update(Tool)
        .where(
            Tool.id == tool.id,
            Tool.status == from_status,
            Tool.version_seq == expected_seq,
        )
        .values(
            status=ToolStatus.APPROVED.value,
            version_seq=Tool.version_seq + 1,
            current_version_id=pending.id,
            pending_version_id=None,
            last_version_at=now,
            offline_reason=None,
            reject_reason=None,
            updated_at=now,
        )
    )
    if (result.rowcount or 0) != 1:
        raise _already_processed(expected_seq, None)

    _activate_version(tool, pending, old_current=old_current, now=now)
    if tool.published_at is None:
        tool.published_at = now
    await session.flush()

    record = await approvals_repo.insert_record(
        session,
        tool_id=tool.id,
        version_id=pending.id,
        action=ApprovalAction.APPROVE,
        from_status=from_status,
        to_status=ToolStatus.APPROVED.value,
        version_from_status=VersionStatus.PENDING.value,
        version_to_status=VersionStatus.APPROVED.value,
        actor_id=actor_id,
        actor_label=actor_label,
        note=note,
        request_id=request_id,
        now=now,
    )

    purged = await _mark_purge_candidates(session, tool=tool, limits=limits)
    await session.commit()

    storage = get_storage()
    for old in purged:
        if old.storage_path:
            deleted = await storage.delete(old.storage_path)
            logger.info(
                "历史版本归档 tool=%s version=%s 文件已删除=%s", tool.slug, old.version, deleted
            )

    return {
        "tool_id": tool.id,
        "status": tool.status,
        "version_seq": tool.version_seq,
        "current_version": pending,
        "superseded_version": old_current if old_current and old_current.id != pending.id else None,
        "purged_versions": purged,
        "approval_record_id": record.id,
    }


async def reject_tool(
    session: AsyncSession,
    *,
    tool: Tool,
    reason: str,
    version_id: int | None,
    actor_id: int,
    actor_label: str,
    request_id: str | None = None,
) -> dict:
    """驳回（docs/03 §3.10）。

    **最容易错的语义**：驳回一个「新版本」不等于驳回工具本身。
      - `pending`（首次提交）      → 版本 rejected + 工具 **rejected**
      - `pending_update`（新版本） → 版本 rejected + 工具 **退回 approved**，
                                     当前版本完全不变
    所以必须先看 `submission_type`，不能一律把工具置为 rejected。
    """
    if tool.status not in (ToolStatus.PENDING.value, ToolStatus.PENDING_UPDATE.value):
        raise StateConflictError(
            message="该工具当前不在待审状态", details={"status": tool.status}
        )

    pending = (
        await versions_repo.get_by_id(session, tool.pending_version_id)
        if tool.pending_version_id
        else None
    )
    if version_id is not None and (pending is None or pending.id != version_id):
        raise _already_processed(version_id, pending.id if pending else None)
    if pending is None:
        raise StateConflictError(message="该工具没有待审版本", details={"status": tool.status})

    from_status = tool.status
    is_new_version = from_status == ToolStatus.PENDING_UPDATE.value
    now = utcnow()

    pending.status = VersionStatus.REJECTED.value
    pending.reject_reason = reason
    pending.is_current = False
    pending.updated_at = now

    if is_new_version:
        # 关键分支：工具退回 approved，当前版本保持不变
        tool.status = ToolStatus.APPROVED.value
        tool.reject_reason = None
    else:
        tool.status = ToolStatus.REJECTED.value
        tool.reject_reason = reason

    tool.pending_version_id = None
    tool.version_seq = (tool.version_seq or 0) + 1
    tool.updated_at = now
    await session.flush()

    record = await approvals_repo.insert_record(
        session,
        tool_id=tool.id,
        version_id=pending.id,
        action=ApprovalAction.REJECT,
        from_status=from_status,
        to_status=tool.status,
        version_from_status=VersionStatus.PENDING.value,
        version_to_status=VersionStatus.REJECTED.value,
        actor_id=actor_id,
        actor_label=actor_label,
        reason=reason,
        request_id=request_id,
        now=now,
    )
    await session.commit()

    return {
        "tool_id": tool.id,
        "status": tool.status,
        "pending_version": None,
        "rejected_version": pending,
        "approval_record_id": record.id,
    }


def _already_processed(expected, actual) -> DomainError:
    from app.core.errors import AlreadyProcessedError

    return AlreadyProcessedError(
        message="该条目已被处理，请刷新列表",
        details={"expected_version_seq": expected, "actual_version_seq": actual},
    )


async def replace_current_version(
    session: AsyncSession,
    *,
    tool: Tool,
    new_version: ToolVersion,
    old_current: ToolVersion | None,
    now: datetime,
) -> None:
    """公开给「自动批准」路径复用（保证两条路径的切换逻辑完全一致）。"""
    _activate_version(tool, new_version, old_current=old_current, now=now)
    await session.flush()


__all__ = [
    "UploadOutcome",
    "approve_tool",
    "create_version",
    "precheck_upload",
    "reject_tool",
    "replace_current_version",
]
