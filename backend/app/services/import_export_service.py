"""批量导入导出（FR-IAM-09/10 / FR-API-08/09 / docs/03 §3.14）。

三条硬要求：

  1. **`generated_passwords` 绝不写日志** —— 这是全平台唯一的明文回显例外
     （密码留空时由系统生成，管理员需要告知用户）。响应体本身也不进访问日志。
  2. **导出 CSV 必须带 UTF-8 BOM**（`EF BB BF`）—— 否则 Excel 打开中文是乱码。
     内网用户大量用 Excel，这条很实际。
  3. **错误精确到行与字段**，不能只给一句「导入失败」。

性能约束（易错点 2、3）：
  - 导入**整批一个事务**（不是一行一个事务），避免长事务锁库
  - 导出**流式**产出，不把全部行载入内存
"""

from __future__ import annotations

import csv
import io
import json
import logging
from collections.abc import Iterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, normalize_username, password_strength_errors
from app.core.timeutil import utcnow
from app.models.enums import RoleCode, ToolStatus
from app.models.taxonomy import Category
from app.models.tool import Tool, ToolTag
from app.models.user import Role, User, UserRole
from app.schemas.admin import (
    GeneratedPassword,
    ImportErrorItem,
    ImportResultResponse,
)
from app.services.admin_user_service import generate_password
from app.services.tool_service import allocate_slug

logger = logging.getLogger(__name__)

#: CSV 的列顺序（导出与导入共用，保证往返一致）
USER_CSV_FIELDS = ("username", "display_name", "email", "roles", "status")

#: UTF-8 BOM —— Excel 靠它识别 UTF-8
UTF8_BOM = "\ufeff"

#: roles 列的分隔符（docs/03 §3.14 的示例用 `;`）
ROLE_SEPARATOR = ";"

#: 单次导入的行数上限
MAX_IMPORT_ROWS = 2000


# ===========================================================================
# 用户导入（CSV）
# ===========================================================================
async def import_users_csv(
    session: AsyncSession,
    *,
    content: bytes,
    dry_run: bool = False,
    on_conflict: str = "skip",
    actor_id: int | None = None,
) -> ImportResultResponse:
    """导入用户 CSV。

    `dry_run=True` 时**完全不写库、不生成密码**，只返回预演报告。
    """
    if on_conflict not in ("skip", "update", "fail"):
        return ImportResultResponse(
            dry_run=dry_run,
            on_conflict=on_conflict,
            failed=1,
            errors=[
                ImportErrorItem(
                    row=0,
                    field="on_conflict",
                    value=on_conflict,
                    message="on_conflict 只能是 skip / update / fail",
                )
            ],
        )

    text = _decode_csv(content)
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if len(rows) > MAX_IMPORT_ROWS:
        return ImportResultResponse(
            dry_run=dry_run,
            on_conflict=on_conflict,
            failed=len(rows),
            errors=[
                ImportErrorItem(
                    row=0,
                    field="file",
                    value=len(rows),
                    message=f"一次最多导入 {MAX_IMPORT_ROWS} 行",
                )
            ],
        )

    # 预加载角色映射，避免逐行查库
    role_result = await session.execute(select(Role.code, Role.id))
    role_ids = {str(r[0]): int(r[1]) for r in role_result.all()}

    # 预加载已存在的用户名 → 1 次查询代替 N 次
    existing_result = await session.execute(select(User.username, User.id))
    existing = {str(r[0]): int(r[1]) for r in existing_result.all()}

    result = ImportResultResponse(dry_run=dry_run, on_conflict=on_conflict)
    now = utcnow()
    seen_in_file: set[str] = set()

    for index, raw in enumerate(rows, start=2):  # 第 1 行是表头，数据从第 2 行起
        row_number = index
        username_raw = (raw.get("username") or "").strip()
        username = normalize_username(username_raw)

        # ---- 逐字段校验，错误精确到行与字段 ----
        if not username:
            result.errors.append(
                ImportErrorItem(row=row_number, field="username", message="用户名不能为空")
            )
            result.failed += 1
            continue
        if username in seen_in_file:
            result.errors.append(
                ImportErrorItem(
                    row=row_number,
                    field="username",
                    value=username_raw,
                    message="文件内用户名重复",
                )
            )
            result.failed += 1
            continue
        seen_in_file.add(username)

        if not _valid_username(username):
            result.errors.append(
                ImportErrorItem(
                    row=row_number,
                    field="username",
                    value=username_raw,
                    message="用户名只能包含字母、数字、下划线、点、连字符",
                )
            )
            result.failed += 1
            continue

        display_name = (raw.get("display_name") or "").strip() or username
        email = (raw.get("email") or "").strip() or None
        role_codes = [
            r.strip()
            for r in (raw.get("roles") or RoleCode.USER.value).split(ROLE_SEPARATOR)
            if r.strip()
        ]
        unknown_roles = [r for r in role_codes if r not in role_ids]
        if unknown_roles:
            result.errors.append(
                ImportErrorItem(
                    row=row_number,
                    field="roles",
                    value=ROLE_SEPARATOR.join(unknown_roles),
                    message=f"未知角色：{', '.join(unknown_roles)}",
                )
            )
            result.failed += 1
            continue

        password = (raw.get("password") or "").strip()
        if password:
            strength = password_strength_errors(password, username)
            if strength:
                result.errors.append(
                    ImportErrorItem(
                        row=row_number,
                        field="password",
                        value="***",
                        message=strength[0],
                    )
                )
                result.failed += 1
                continue

        # ---- 冲突处理 ----
        if username in existing:
            if on_conflict == "fail":
                result.errors.append(
                    ImportErrorItem(
                        row=row_number,
                        field="username",
                        value=username_raw,
                        message="用户已存在",
                    )
                )
                result.failed += 1
                continue
            if on_conflict == "skip":
                result.skipped += 1
                continue
            # update：更新显示名/邮箱/角色，**不动密码**
            if not dry_run:
                user = await session.get(User, existing[username])
                if user is not None:
                    user.display_name = display_name
                    user.email = email
                    user.updated_at = now
                    await _replace_roles(session, user.id, role_codes, role_ids, now)
            result.succeeded += 1
            continue

        # ---- 新建 ----
        generated: str | None = None
        if not password:
            # dry_run 时**不生成密码**（docs/03 §3.14 的明确要求）
            if dry_run:
                pass
            else:
                generated = generate_password()

        if not dry_run:
            effective_password = password or generated or generate_password()
            user = User(
                username=username,
                display_name=display_name,
                email=email,
                password_hash=hash_password(effective_password),
                password_changed_at=now,
                must_change_password=True,
                status="active",
                created_by_id=actor_id,
                created_at=now,
                updated_at=now,
            )
            session.add(user)
            await session.flush()
            await _replace_roles(session, user.id, role_codes, role_ids, now)
            if generated:
                # 明文只进响应体，**不进日志**
                result.generated_passwords.append(
                    GeneratedPassword(username=username, password=generated)
                )
        result.succeeded += 1

    if not dry_run and result.succeeded:
        # 整批一个事务：dry_run 时一次都不提交
        #
        # M14（契约 §29.6）：**提交前的终态检查**。这条路径（CSV 导入 / 从备份恢复）
        # 通过 `_replace_roles` 整体替换角色，**不走** `_cas_guard_last_superadmin`
        # —— 那是刻意的：合法的「从备份恢复」必须允许超管数**低于当前值**
        # （备份里可能就只有 1 个超管）。真正该堵的只有一种结果：
        # 「恢复完一个超管都没有」——那是把自己锁在门外。
        #
        # 所以这里不在过程的每一步加守卫，只在**事务提交前看一眼终态**：
        # 0 个活跃超管 → 整体回滚 + 报错（409 LAST_SUPERADMIN）。
        # 此时 `_replace_roles` 的 DELETE/INSERT 都已 flush 进本事务，
        # 所以这个计数就是「本次导入之后」的真实状态。
        await _assert_superadmin_survives(session)
        await session.commit()
    elif dry_run:
        await session.rollback()

    logger.info(
        "导入用户 dry_run=%s on_conflict=%s succeeded=%s failed=%s skipped=%s",
        dry_run,
        on_conflict,
        result.succeeded,
        result.failed,
        result.skipped,
    )
    return result


async def _assert_superadmin_survives(session: AsyncSession) -> None:
    """终态检查：导入后必须仍有活跃超管，否则整体回滚并报错（§29.6）。

    错误码复用既有的 `LAST_SUPERADMIN`（409）：语义完全一致
    （「不能失去最后一个超级管理员」），且契约 §4.3 禁止自行发明新错误码。
    """
    from app.core.errors import LastSuperadminError
    from app.repositories import users as users_repo

    remaining = await users_repo.count_active_superadmins(session)
    if remaining > 0:
        return
    await session.rollback()
    logger.warning("导入被拒绝：终态检查发现活跃超管数为 0，已整体回滚")
    raise LastSuperadminError(
        message=(
            "导入后系统将没有任何活跃超级管理员，已整体回滚（本次导入未生效）。"
            "请确保导入内容中至少有一个 status=active 的 superadmin。"
        ),
        details={"active_superadmins_after_import": 0},
    )


def _valid_username(username: str) -> bool:
    import re

    return bool(re.fullmatch(r"[a-z0-9_.-]{1,64}", username))


async def _replace_roles(
    session: AsyncSession,
    user_id: int,
    role_codes: list[str],
    role_ids: dict[str, int],
    now,
) -> None:
    from sqlalchemy import delete as sa_delete

    await session.execute(sa_delete(UserRole).where(UserRole.user_id == user_id))
    for code in role_codes:
        session.add(
            UserRole(user_id=user_id, role_id=role_ids[code], granted_at=now)
        )
    await session.flush()


def _decode_csv(content: bytes) -> str:
    """解码 CSV，容错 BOM 与常见中文编码。"""
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


# ===========================================================================
# 用户导出（CSV，带 BOM）
# ===========================================================================
async def export_users_csv(session: AsyncSession) -> Iterator[str]:
    """流式导出用户 CSV。

    第一块就是 BOM + 表头，保证即使后面流式吐出很多行，
    客户端拿到的前 3 个字节也一定是 `EF BB BF`。

    **绝不含 password_hash**（FR-IAM-10）。
    """
    yield UTF8_BOM + ",".join(USER_CSV_FIELDS) + "\r\n"

    result = await session.execute(select(User).order_by(User.id.asc()))
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    batch = 0
    for user in result.scalars().all():
        writer.writerow(
            [
                user.username,
                user.display_name,
                user.email or "",
                ROLE_SEPARATOR.join(user.role_codes),
                user.status,
            ]
        )
        batch += 1
        if batch >= 200:
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)
            batch = 0
    if batch:
        yield buffer.getvalue()


# ===========================================================================
# 工具导入（JSON）
# ===========================================================================
async def import_tools_json(
    session: AsyncSession,
    *,
    items: list[dict],
    dry_run: bool = False,
    on_conflict: str = "skip",
    actor_id: int | None = None,
) -> ImportResultResponse:
    """导入工具元数据（JSON）。

    `on_conflict` 按 **slug** 判重（slug 是工具的对外稳定标识）。
    """
    if on_conflict not in ("skip", "update", "fail"):
        return ImportResultResponse(
            dry_run=dry_run,
            on_conflict=on_conflict,
            failed=1,
            errors=[
                ImportErrorItem(
                    row=0,
                    field="on_conflict",
                    value=on_conflict,
                    message="on_conflict 只能是 skip / update / fail",
                )
            ],
        )

    # 预加载映射（1~3 次查询代替 3N 次）
    users_result = await session.execute(select(User.username, User.id))
    user_ids = {str(r[0]): int(r[1]) for r in users_result.all()}
    cats_result = await session.execute(select(Category.slug, Category.id))
    category_ids = {str(r[0]): int(r[1]) for r in cats_result.all()}
    slugs_result = await session.execute(select(Tool.slug, Tool.id))
    existing = {str(r[0]): int(r[1]) for r in slugs_result.all()}

    result = ImportResultResponse(dry_run=dry_run, on_conflict=on_conflict)
    now = utcnow()

    for row_number, item in enumerate(items, start=1):
        name = str(item.get("name") or "").strip()
        if not name:
            result.errors.append(
                ImportErrorItem(row=row_number, field="name", message="名称不能为空")
            )
            result.failed += 1
            continue

        # webapp 必须有 URL（FR-TOOL-05）。这里吃的是原始 dict，绕过了 Pydantic，
        # 所以要显式查一遍：否则这一行会走到 INSERT，撞 `ck_tools_webapp_url`
        # 检查约束，整批导入被一个坏行打成 500（M3 自查时发现）。
        if str(item.get("tool_type") or "file") == "webapp" and not str(
            item.get("webapp_url") or ""
        ).strip():
            result.errors.append(
                ImportErrorItem(
                    row=row_number,
                    field="webapp_url",
                    message="webapp 类型必须填写 URL",
                )
            )
            result.failed += 1
            continue

        owner_username = normalize_username(str(item.get("owner_username") or "admin"))
        owner_id = user_ids.get(owner_username)
        if owner_id is None:
            result.errors.append(
                ImportErrorItem(
                    row=row_number,
                    field="owner_username",
                    value=owner_username,
                    message=f"用户不存在：{owner_username}",
                )
            )
            result.failed += 1
            continue

        category_slug = item.get("category_slug")
        category_id = None
        if category_slug:
            category_id = category_ids.get(str(category_slug))
            if category_id is None:
                result.errors.append(
                    ImportErrorItem(
                        row=row_number,
                        field="category_slug",
                        value=str(category_slug),
                        message=f"分类不存在：{category_slug}",
                    )
                )
                result.failed += 1
                continue

        slug = str(item.get("slug") or "").strip()
        if slug and slug in existing:
            if on_conflict == "fail":
                result.errors.append(
                    ImportErrorItem(
                        row=row_number, field="slug", value=slug, message="slug 已存在"
                    )
                )
                result.failed += 1
                continue
            if on_conflict == "skip":
                result.skipped += 1
                continue
            if not dry_run:
                tool = await session.get(Tool, existing[slug])
                if tool is not None:
                    tool.name = name
                    tool.summary = str(item.get("summary") or tool.summary)
                    tool.description_md = str(
                        item.get("description_md") or tool.description_md
                    )
                    tool.category_id = category_id if category_id else tool.category_id
                    tool.visibility = str(item.get("visibility") or tool.visibility)
                    tool.owner_id = owner_id
                    tool.updated_at = now
            result.succeeded += 1
            continue

        if not dry_run:
            resolved_slug = slug or await allocate_slug(session, name)
            publish = bool(item.get("publish", True))
            tool = Tool(
                slug=resolved_slug,
                name=name,
                summary=str(item.get("summary") or "")[:500] or name[:500],
                description_md=str(item.get("description_md") or ""),
                tool_type=str(item.get("tool_type") or "file"),
                visibility=str(item.get("visibility") or "public"),
                status=ToolStatus.APPROVED.value if publish else ToolStatus.DRAFT.value,
                owner_id=owner_id,
                category_id=category_id,
                webapp_url=item.get("webapp_url"),
                published_at=now if publish else None,
                version_seq=1,
                created_at=now,
                updated_at=now,
            )
            session.add(tool)
            await session.flush()
            existing[resolved_slug] = tool.id

            from app.repositories import tags as tags_repo

            for tag_name in list(item.get("tags") or [])[:8]:
                tag = await tags_repo.get_or_create(
                    session, str(tag_name), created_by_id=actor_id
                )
                session.add(ToolTag(tool_id=tool.id, tag_id=tag.id))
        result.succeeded += 1

    if not dry_run and result.succeeded:
        await session.commit()
    elif dry_run:
        await session.rollback()

    logger.info(
        "导入工具 dry_run=%s succeeded=%s failed=%s skipped=%s",
        dry_run,
        result.succeeded,
        result.failed,
        result.skipped,
    )
    return result


async def export_tools_json(session: AsyncSession) -> Iterator[str]:
    """流式导出工具元数据 JSON（数组形式，按 owner 分批查）。"""
    yield "["
    first = True
    offset = 0
    batch_size = 200
    while True:
        result = await session.execute(
            select(Tool)
            .where(Tool.deleted_at.is_(None))
            .order_by(Tool.id.asc())
            .limit(batch_size)
            .offset(offset)
        )
        rows = list(result.scalars().unique().all())
        if not rows:
            break
        for tool in rows:
            payload = {
                "id": tool.id,
                "slug": tool.slug,
                "name": tool.name,
                "summary": tool.summary,
                "description_md": tool.description_md,
                "tool_type": tool.tool_type,
                "visibility": tool.visibility,
                "status": tool.status,
                "owner_username": tool.owner.username if tool.owner else None,
                "category_slug": tool.category.slug if tool.category else None,
                "tags": [t.display_name for t in tool.tags],
                "webapp_url": tool.webapp_url,
                "download_count": tool.download_count,
                "view_count": tool.view_count,
                "published_at": tool.published_at.isoformat() if tool.published_at else None,
                "created_at": tool.created_at.isoformat() if tool.created_at else None,
                "updated_at": tool.updated_at.isoformat() if tool.updated_at else None,
            }
            yield ("" if first else ",") + json.dumps(payload, ensure_ascii=False)
            first = False
        offset += batch_size
    yield "]"


__all__ = [
    "MAX_IMPORT_ROWS",
    "ROLE_SEPARATOR",
    "USER_CSV_FIELDS",
    "UTF8_BOM",
    "export_tools_json",
    "export_users_csv",
    "import_tools_json",
    "import_users_csv",
]
