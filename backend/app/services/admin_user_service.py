"""用户与角色管理服务（FR-IAM-01~11 / docs/01 §5.13）。

三条业务铁律：

  1. **不得物理删除用户**（FR-IAM-06）—— 与 tools.owner_id /
     approval_records.actor_id / download_logs.user_id 有外键关联，
     删除会打断审计链。接口层只提供「禁用」。
  2. **必须始终保留至少一个启用的 superadmin**（FR-IAM-07）。
  3. **不得禁用或降级自己**（FR-IAM-08），防止自我锁死。

第 2 条不只靠 **CAS**：把「还剩几个启用的超管」写进 UPDATE 的 WHERE 里、
用影响行数判断成败，这一步**本身不足以**实现并发互斥 —— 两个并发请求 UPDATE 的是
不同的行，行锁不冲突。因此守卫在 CAS 之前先对**全部活跃超管**取 `FOR UPDATE`
写锁（全集，见 `_lock_all_active_superadmins`），再把判断放在锁后，
两个管理员同时降级最后两个超管时才真的只有一个能成功（M10 修掉了
PostgreSQL 默认隔离级别下的 write-skew）。
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    LastSuperadminError,
    NotFoundError,
    ValidationError,
)
from app.core.permissions import permissions_for_roles
from app.core.security import (
    hash_password,
    normalize_username,
    password_strength_errors,
)
from app.core.timeutil import utcnow
from app.models.enums import RoleCode, UserStatus
from app.models.tool import Tool
from app.models.user import ApiToken, AuthSession, Role, User, UserRole
from app.repositories import api_tokens as tokens_repo
from app.schemas.admin import (
    AdminUserCreateRequest,
    AdminUserItem,
    AdminUserUpdateRequest,
    RoleOut,
)

logger = logging.getLogger(__name__)

#: 生成初始密码用的字符集（去掉了容易混淆的 0/O/1/l/I）
_PASSWORD_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"


def generate_password(length: int = 14) -> str:
    """生成一个满足强度要求的初始口令。

    前缀 `St` 保证含大小写，末尾 `!7` 保证含符号与数字，
    中间用无歧义字符集随机 —— 这样任何输出都必然通过 `password_strength_errors`。
    """
    middle = "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(max(4, length - 4)))
    return f"St{middle}!7"


async def _active_superadmin_count(session: AsyncSession) -> int:
    result = await session.execute(
        select(func.count(func.distinct(User.id)))
        .select_from(User)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(Role.code == RoleCode.SUPERADMIN.value, User.status == UserStatus.ACTIVE.value)
    )
    return int(result.scalar_one())


async def _superadmin_role_id(session: AsyncSession) -> int:
    result = await session.execute(
        select(Role.id).where(Role.code == RoleCode.SUPERADMIN.value)
    )
    role_id = result.scalar_one_or_none()
    if role_id is None:
        # 迁移 0002 保证存在；走到这里说明库被动过
        raise NotFoundError(message="superadmin 角色不存在，请检查数据库初始化")
    return int(role_id)


async def _role_ids_for_codes(session: AsyncSession, codes: list[str]) -> dict[str, int]:
    if not codes:
        return {}
    result = await session.execute(select(Role.code, Role.id).where(Role.code.in_(codes)))
    mapping = {str(r[0]): int(r[1]) for r in result.all()}
    missing = set(codes) - set(mapping)
    if missing:
        raise ValidationError(
            message="存在未知角色",
            fields=[{"field": "roles", "message": f"未知角色：{sorted(missing)}"}],
        )
    return mapping


# ===========================================================================
# 读
# ===========================================================================
async def list_users(
    session: AsyncSession,
    *,
    q: str | None = None,
    roles: list[str] | None = None,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[AdminUserItem], int]:
    """用户列表（FR-IAM-04：模糊搜索 + 角色筛选 + 状态筛选 + 分页）。"""
    stmt = select(User)
    if q:
        pattern = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            func.lower(User.username).like(pattern)
            | func.lower(User.display_name).like(pattern)
        )
    if status:
        stmt = stmt.where(User.status == status)
    if roles:
        stmt = stmt.where(
            User.id.in_(
                select(UserRole.user_id)
                .join(Role, Role.id == UserRole.role_id)
                .where(Role.code.in_(roles))
            )
        )

    total = int(
        (
            await session.execute(
                select(func.count()).select_from(stmt.subquery())
            )
        ).scalar_one()
    )
    rows = list(
        (
            await session.execute(
                stmt.order_by(User.id.asc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .unique()
        .all()
    )

    items = [await _build_user_item(session, user) for user in rows]
    return items, total


async def _build_user_item(session: AsyncSession, user: User) -> AdminUserItem:
    from app.repositories import tool_versions as versions_repo

    tool_count = int(
        (
            await session.execute(
                select(func.count())
                .select_from(Tool)
                .where(Tool.owner_id == user.id, Tool.deleted_at.is_(None))
            )
        ).scalar_one()
    )
    return AdminUserItem(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        roles=user.role_codes,
        status=user.status,
        auth_source=user.auth_source,
        must_change_password=bool(user.must_change_password),
        failed_login_count=user.failed_login_count,
        locked_until=user.locked_until,
        last_login_at=user.last_login_at,
        last_login_ip=user.last_login_ip,
        created_at=user.created_at,
        updated_at=user.updated_at,
        tool_count=tool_count,
        used_bytes=await versions_repo.sum_file_size_for_user(session, user.id),
        active_token_count=await tokens_repo.count_active_for_user(
            session, user.id, now=utcnow()
        ),
    )


async def get_user_item(session: AsyncSession, user_id: int) -> AdminUserItem:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError(message="用户不存在", details={"user_id": user_id})
    await session.refresh(user, ["roles"])
    return await _build_user_item(session, user)


async def list_roles(session: AsyncSession) -> list[RoleOut]:
    """四个内置角色 + 各自权限点与用户数。

    权限点来自**代码里的 frozenset**（`app.core.permissions`），
    不是 `roles.permissions` 列 —— 后者只是冗余备份，不作为判定来源
    （docs/02 §3.2 的明确决策，避免改库提权）。
    """
    result = await session.execute(select(Role).order_by(Role.id.asc()))
    roles = list(result.scalars().all())
    counts_result = await session.execute(
        select(Role.code, func.count(UserRole.user_id))
        .join(UserRole, UserRole.role_id == Role.id)
        .group_by(Role.code)
    )
    counts = {str(r[0]): int(r[1]) for r in counts_result.all()}
    return [
        RoleOut(
            id=role.id,
            code=role.code,
            name=role.name,
            description=role.description,
            is_builtin=role.is_builtin,
            permissions=sorted(permissions_for_roles({role.code})),
            user_count=counts.get(role.code, 0),
        )
        for role in roles
    ]


# ===========================================================================
# 写
# ===========================================================================
@dataclass
class CreatedUser:
    user: User
    generated_password: str | None


async def create_user(
    session: AsyncSession,
    *,
    payload: AdminUserCreateRequest,
    actor_id: int,
) -> CreatedUser:
    """创建用户（FR-IAM-01）。密码留空时生成并**只回显一次**。"""
    username = normalize_username(payload.username)
    existing = await session.execute(select(User.id).where(User.username == username))
    if existing.first() is not None:
        raise ValidationError(
            message="用户名已存在",
            fields=[{"field": "username", "message": f"用户名 {username} 已被占用"}],
        )

    generated: str | None = None
    raw_password = payload.password
    if not raw_password:
        raw_password = generate_password()
        generated = raw_password
    else:
        errors = password_strength_errors(raw_password, username)
        if errors:
            raise ValidationError(
                message="密码强度不足",
                fields=[{"field": "password", "message": e} for e in errors],
            )

    now = utcnow()
    role_ids = await _role_ids_for_codes(session, [r.value for r in payload.roles])
    granted_by = await _resolve_actor_id(session, actor_id)

    user = User(
        username=username,
        display_name=payload.display_name.strip(),
        email=(payload.email or "").strip() or None,
        password_hash=hash_password(raw_password),
        password_changed_at=now,
        must_change_password=payload.must_change_password,
        status=UserStatus.ACTIVE.value,
        created_by_id=granted_by,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    await session.flush()
    for role_id in role_ids.values():
        session.add(
            UserRole(
                user_id=user.id, role_id=role_id, granted_by_id=granted_by, granted_at=now
            )
        )
    await session.commit()

    await session.refresh(user, ["roles"])
    return CreatedUser(user=user, generated_password=generated)


async def update_user(
    session: AsyncSession,
    *,
    user_id: int,
    payload: AdminUserUpdateRequest,
    actor_id: int,
) -> AdminUserItem:
    """编辑显示名/邮箱/状态（FR-IAM-02）。`username` 不可改。"""
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError(message="用户不存在", details={"user_id": user_id})
    await session.refresh(user, ["roles"])

    if payload.display_name is not None:
        user.display_name = payload.display_name.strip()
    if payload.email is not None:
        user.email = payload.email.strip() or None

    if payload.status is not None and payload.status.value != user.status:
        if payload.status == UserStatus.DISABLED:
            await disable_user(session, user=user, actor_id=actor_id)
        else:
            user.status = UserStatus.ACTIVE.value
            user.updated_at = utcnow()

    user.updated_at = utcnow()
    await session.commit()
    await session.refresh(user, ["roles"])
    return await _build_user_item(session, user)


async def disable_user(session: AsyncSession, *, user: User, actor_id: int) -> None:
    """禁用用户 —— 三条约束都在这里统一处理。

      - 不能禁用自己（FR-IAM-08）
      - 不能禁用最后一个启用的超管（FR-IAM-07，CAS）
      - 禁用后**立即**吊销其全部 refresh token 与 API Token（FR-IAM-05）

    API Token 用「吊销」而不是只靠实时交集：实时交集只保证「降权」，
    但禁用是**账号失效**，Token 应当直接不可用而不是降成一个空权限集。
    """
    if user.id == actor_id:
        raise ValidationError(
            message="不能禁用自己的账号（防止自我锁死）",
            details={"user_id": user.id, "reason": "self_disable"},
        )

    now = utcnow()
    is_superadmin = RoleCode.SUPERADMIN.value in user.role_codes
    if is_superadmin:
        await _cas_guard_last_superadmin(session, target_user=user, now=now)

    user.status = UserStatus.DISABLED.value
    user.locked_until = None
    user.updated_at = now
    await session.flush()

    revoked_sessions = await _revoke_sessions(session, user.id, now=now)
    revoked_tokens = await tokens_repo.revoke_all_for_user(
        session, user.id, revoked_by_id=actor_id, now=now
    )
    logger.info(
        "禁用用户 user_id=%s 已吊销会话=%s 已吊销Token=%s",
        user.id,
        revoked_sessions,
        revoked_tokens,
    )


async def _resolve_actor_id(session: AsyncSession, actor_id: int | None) -> int | None:
    """把操作者 id 规整为「可写入 user_roles.granted_by_id 的值」。

    `user_roles.granted_by_id` 是可空列，外键是 `ON DELETE SET NULL` —— 语义上
    「授权人已不可知」是一个合法状态。但服务层如果直接把一个不存在的 actor_id
    写进去，会撞外键并抛 IntegrityError，向调用方暴露成 500。

    这里显式做一次存在性判定：actor 存在就记真实 id，不存在就记 NULL。
    真实场景（HTTP 层）actor 永远是当前登录主体；NULL 分支覆盖的是
    「主体在鉴权之后、写库之前被删除」这类竞态，以及 CLI/测试里的系统操作者。
    """
    if actor_id is None:
        return None
    exists = await session.scalar(select(User.id).where(User.id == actor_id))
    return exists


async def _lock_all_active_superadmins(session: AsyncSession, *, role_id: int) -> list[int]:
    """对**全部**启用的超管行取写锁，返回被锁住的 user_id 列表（按 id 升序）。

    这是 `_cas_guard_last_superadmin` 互斥性的来源，有三处细节不能改：

      1. **锁的是全集，不是「除自己以外的那些」。** 只锁「除自己外」的集合时，
         降级 A 的事务锁的是 {B}、降级 B 的事务锁的是 {A} —— 两个集合
         **不相交**，互不阻塞，write-skew 原样成立。锁全集则两边都要锁 {A, B}，
         必然在同一行上相遇。
      2. **`ORDER BY users.id`。** PostgreSQL 对 `SELECT ... ORDER BY ... FOR UPDATE`
         的计划是 `LockRows -> Sort`（本机 PG 16 实测 EXPLAIN 确认），即**按 id 升序**
         加锁。两个守卫事务加锁顺序一致 → 不会互相等待成环（无死锁）。
         去掉 ORDER BY 后加锁顺序不保证，理论上可能与对方交叉持锁而死锁
         （死锁只会让一方报错，不会破坏不变量，但会让并发用例的输出变得不确定）。
      3. **方言无关。** SQLite 不支持 `FOR UPDATE`，SQLAlchemy 会把它编译掉
         （实测编译结果里没有该子句）—— 而 SQLite 本就是库级单写者，
         写事务天然串行，不需要行锁。
    """
    result = await session.execute(
        select(User.id)
        .join(UserRole, UserRole.user_id == User.id)
        .where(UserRole.role_id == role_id, User.status == UserStatus.ACTIVE.value)
        .order_by(User.id)
        .with_for_update()
    )
    return [int(row[0]) for row in result.all()]


async def _cas_guard_last_superadmin(
    session: AsyncSession, *, target_user: User, now: datetime
) -> None:
    """CAS 守卫：确认「除 target 之外还有启用的超管」，并保证并发下真的互斥。

    为什么不能「先 SELECT COUNT 再判断」：两个管理员同时降级最后两个超管时，
    读-判断-写之间会互相穿插，两个请求都可能读到 count=2 然后都通过。

    为什么「条件 UPDATE + 子查询」也不够（M10 修复）：那条 UPDATE 命中的是
    **各自的目标行**（不同的行），行锁根本不冲突，所以判断与写入之间没有任何互斥。
    SQLite 靠库级写锁侥幸正确；PostgreSQL 默认 READ COMMITTED 下两个事务都在
    对方提交前读到「对方仍是超管」，双双通过 → 超管归零（典型 write-skew）。

    现在分三步，顺序不能变：

      1. `SELECT ... FOR UPDATE` 锁住**当前全部活跃超管**（见
         `_lock_all_active_superadmins` 的说明：必须锁全集）。
      2. **拿到锁之后**再读一次计数。READ COMMITTED 下每条语句取新快照 ——
         若本事务在锁上被阻塞过，阻塞解除时对方已提交，这一读就能看到
         对方那次降级的结果，于是守卫会正确地拒绝。
         （顺序反过来先读计数再取锁，读到的就是被阻塞之前的旧值，等于没修。）
      3. 保留原有的条件 UPDATE + rowcount 判定：它是「判断 + 写入」
         在同一把锁下的第二次断言，也维持既有的 `cas_failed` 错误口径。

    **互斥性论证（为什么两个并发守卫不可能都通过）**：
    设活跃超管集合为 S。任一守卫事务的加锁语句读到的集合是「其快照时刻的 S」，
    并把这个集合整体锁到事务结束（提交或回滚）为止。对任意两个并发守卫 T1、T2，
    只有两种可能：

      - T2 的加锁语句在 T1 提交**之前**取快照：此时 T1 的目标在 T2 的快照里
        仍是活跃超管（T1 尚未提交），T2 的目标在 T1 的快照里也仍是活跃超管；
        两边的集合都包含两个目标，于是 T1、T2 一定在同一行上争锁 ——
        后到者被阻塞到先到者提交，其第 2 步的计数读到的是**提交后**的状态。
      - T2 的加锁语句在 T1 提交**之后**取快照：T2 的计数语句必然在 T1 提交之后
        执行（更晚），直接看到 T1 已把超管数减 1。

    两种情况下，「计数 ≥ 2」这一步都发生在所有先前成功的降级提交之后，
    所以每次成功降级观察到的都是「至少还有 2 个活跃超管」的串行状态，
    超管数量不可能被降到 0。

    **注意**：这里只更新 `updated_at`，不改 `status` —— 守卫是守卫，
    业务字段的修改由调用方自己做。M3 排查时发现过一个真实 bug：
    守卫顺手把 status 置成 disabled，导致「降级超管」把账号也禁用了。
    """
    # 先取出标量：下面 rollback/commit 会 expire 会话内对象，
    # 之后再读 target_user.id 就是一次同步懒加载 —— 在 async 上下文里
    # 会炸成 MissingGreenlet（M3 排查时踩过，别再把 id 读回对象上）。
    target_id = target_user.id

    superadmin_role_id = await _superadmin_role_id(session)
    await _lock_all_active_superadmins(session, role_id=superadmin_role_id)

    active_count = await _active_superadmin_count(session)
    if active_count <= 1:
        raise LastSuperadminError(
            message="不能禁用或降级最后一个启用的超级管理员",
            details={"active_superadmin_count": active_count, "user_id": target_id},
        )

    result = await session.execute(
        update(User)
        .where(
            User.id == target_user.id,
            select(func.count(func.distinct(User.id)))
            .select_from(User)
            .join(UserRole, UserRole.user_id == User.id)
            .where(
                UserRole.role_id == superadmin_role_id,
                User.status == UserStatus.ACTIVE.value,
                User.id != target_user.id,
            )
            .scalar_subquery()
            > 0,
        )
        .values(updated_at=now)
    )
    if (result.rowcount or 0) != 1:
        await session.rollback()
        raise LastSuperadminError(
            message="不能禁用或降级最后一个启用的超级管理员（并发检测）",
            details={"reason": "cas_failed", "user_id": target_id},
        )


async def _revoke_sessions(session: AsyncSession, user_id: int, *, now: datetime) -> int:
    result = await session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    return int(result.rowcount or 0)


async def replace_roles(
    session: AsyncSession, *, user_id: int, roles: list[str], actor_id: int
) -> AdminUserItem:
    """全量替换角色（FR-IAM-02）。

    同样要过 LAST_SUPERADMIN 与「不能降级自己」两道闸。
    """
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError(message="用户不存在", details={"user_id": user_id})
    await session.refresh(user, ["roles"])

    current = set(user.role_codes)
    target = set(roles)
    loses_superadmin = (
        RoleCode.SUPERADMIN.value in current and RoleCode.SUPERADMIN.value not in target
    )

    if loses_superadmin:
        if user.id == actor_id:
            raise ValidationError(
                message="不能降级自己的超级管理员角色（防止自我锁死）",
                details={"user_id": user.id, "reason": "self_demote"},
            )
        await _cas_guard_last_superadmin(session, target_user=user, now=utcnow())

    now = utcnow()
    role_ids = await _role_ids_for_codes(session, list(target))
    granted_by = await _resolve_actor_id(session, actor_id)
    await session.execute(sa_delete(UserRole).where(UserRole.user_id == user.id))
    for role_id in role_ids.values():
        session.add(
            UserRole(
                user_id=user.id, role_id=role_id, granted_by_id=granted_by, granted_at=now
            )
        )
    user.updated_at = now
    await session.commit()
    await session.refresh(user, ["roles"])
    return await _build_user_item(session, user)


async def reset_password(
    session: AsyncSession, *, user_id: int, password: str | None, actor_id: int
) -> tuple[User, str | None, int]:
    """重置密码 → 强制改密 → 吊销全部会话（FR-IAM-03 + FR-IAM-05 的连带效应）。"""
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError(message="用户不存在", details={"user_id": user_id})

    generated: str | None = None
    raw = password
    if not raw:
        raw = generate_password()
        generated = raw
    else:
        errors = password_strength_errors(raw, user.username)
        if errors:
            raise ValidationError(
                message="密码强度不足",
                fields=[{"field": "password", "message": e} for e in errors],
            )

    now = utcnow()
    user.password_hash = hash_password(raw)
    user.password_changed_at = now
    user.must_change_password = True
    user.failed_login_count = 0
    user.locked_until = None
    user.updated_at = now
    await session.flush()

    revoked = await _revoke_sessions(session, user.id, now=now)
    await session.commit()
    return user, generated, revoked


async def revoke_sessions(session: AsyncSession, *, user_id: int, actor_id: int) -> int:
    """强制下线（FR-AUTH-12）：吊销该用户全部 refresh token **与其签发的 API Token**。

    J-3（contracts/CONTRACT.md §20.4）：原先只吊销 refresh token，
    **管理员「把这个人踢出去」之后，该用户签发的 API Token 仍然可用**（监控方实测确认）。
    这与 FR-IAM-05 在「禁用用户」时的语义不一致 —— 同一个「让这个人彻底失去访问」的意图，
    轻动作（强制下线）比重动作（禁用）反而更宽松。
    """
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError(message="用户不存在", details={"user_id": user_id})
    now = utcnow()
    revoked = await _revoke_sessions(session, user_id, now=now)
    # 同一事务内一并吊销 Token，避免「会话没了但 Token 还在」的中间态。
    await tokens_repo.revoke_all_for_user(
        session, user_id, revoked_by_id=actor_id, now=now
    )
    await session.commit()
    return revoked


async def revoke_user_tokens(session: AsyncSession, *, user_id: int, actor_id: int) -> int:
    """一键吊销某用户的全部 API Token（docs/05 §13.8 的轮换要求）。"""
    revoked = await tokens_repo.revoke_all_for_user(
        session, user_id, revoked_by_id=actor_id, now=utcnow()
    )
    await session.commit()
    return revoked


__all__ = [
    "ApiToken",
    "CreatedUser",
    "create_user",
    "disable_user",
    "generate_password",
    "get_user_item",
    "list_roles",
    "list_users",
    "replace_roles",
    "reset_password",
    "revoke_sessions",
    "revoke_user_tokens",
    "update_user",
]
