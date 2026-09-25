"""主体目录（J-1）。

**为什么需要这个接口**：`docs/01` FR-ACL-02（P0）与 `docs/04` §6.7 要求
「搜索用户/组后添加」到可见性授权名单，但在此之前的接口面里，
普通用户**没有任何可用的搜索接口** —— `GET /admin/users` 与 `GET /admin/groups`
都要求超管。结果是设置 `restricted` 可见性时必须**手填数字 ID**，功能实际不可用。

这是**对 92 接口冻结的刻意例外**（contracts/CONTRACT.md §20.4③）：接口面 92 → 93。
冻结的目的是防范围蔓延，而这是一个**功能空洞**（P0 需求无法交付），不是新功能。

**最小披露原则**：这是任何已登录用户都能调的接口，因此只返回
- 用户：`id` / `username` / `display_name`
- 用户组：`id` / `name` / `member_count`

**不返回**邮箱、状态、角色、最后登录时间、创建时间。
100 人内网里工具作者本就能看到彼此的名字，最小披露是安全的；
但邮箱与账号状态属于管理信息，不应通过这个接口泄露。
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Principal, require_authenticated
from app.db.session import get_db
from app.models.user import Group, GroupMember, User, UserStatus

router = APIRouter(tags=["directory"])

#: 单次返回上限 —— 目录接口是给「输入即联想」用的，不需要一次给全量。
MAX_LIMIT = 50
DEFAULT_LIMIT = 20


class DirectoryUser(BaseModel):
    """最小披露的用户条目。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str


class DirectoryGroup(BaseModel):
    """最小披露的用户组条目。"""

    id: int
    name: str
    member_count: int


class DirectoryResponse(BaseModel):
    users: list[DirectoryUser] = Field(default_factory=list)
    groups: list[DirectoryGroup] = Field(default_factory=list)


@router.get(
    "/directory",
    response_model=DirectoryResponse,
    summary="主体目录（用于 ACL 授权时搜索用户/用户组）",
)
async def search_directory(
    session: Annotated[AsyncSession, Depends(get_db)],
    # 用 `require_authenticated` 而非 `get_principal` —— 前者带**强制改密拦截**
    # （FR-AUTH-03）。守卫测试断言「除豁免前缀外每个路由都被改密拦截覆盖」，
    # 初次实现漏了这点，被它抓出来。
    principal: Annotated[Principal, Depends(require_authenticated)],
    q: Annotated[str, Query(max_length=64)] = "",
    type_: Annotated[Literal["user", "group"] | None, Query(alias="type")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> DirectoryResponse:
    """按关键词模糊匹配用户与用户组。

    - `q` 为空时返回前 N 条（供「点开下拉即看到常用主体」的交互）
    - `type` 省略则两者都返回
    - 任何**已登录用户**都可调用；未登录返回 401

    只返回**启用状态**的用户与用户组 —— 已禁用的账号不该再被授权。
    """
    del principal  # 仅用于强制鉴权；接口本身对所有登录用户开放
    keyword = q.strip().lower()
    pattern = f"%{keyword}%"

    users: list[DirectoryUser] = []
    groups: list[DirectoryGroup] = []

    if type_ in (None, "user"):
        stmt = (
            select(User)
            .where(User.status == UserStatus.ACTIVE.value)
            .order_by(User.id.asc())
            .limit(limit)
        )
        if keyword:
            stmt = stmt.where(
                or_(
                    func.lower(User.username).like(pattern),
                    func.lower(User.display_name).like(pattern),
                )
            )
        users = [
            DirectoryUser(id=u.id, username=u.username, display_name=u.display_name)
            for u in (await session.execute(stmt)).scalars()
        ]

    if type_ in (None, "group"):
        # member_count 用子查询一次取回，避免逐组再查一次（N+1）
        member_count = (
            select(func.count(GroupMember.user_id))
            .where(GroupMember.group_id == Group.id)
            .scalar_subquery()
        )
        stmt = (
            select(Group, member_count.label("member_count"))
            .where(Group.is_active.is_(True))
            .order_by(Group.id.asc())
            .limit(limit)
        )
        if keyword:
            stmt = stmt.where(func.lower(Group.name).like(pattern))
        groups = [
            DirectoryGroup(id=g.id, name=g.name, member_count=int(cnt or 0))
            for g, cnt in (await session.execute(stmt)).all()
        ]

    return DirectoryResponse(users=users, groups=groups)


__all__ = ["router"]
