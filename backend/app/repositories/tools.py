"""工具仓储 —— 门户列表查询（docs/02 §4 的 Q1/Q2）。

**可见性判定必须下推到 SQL 的 WHERE 子句**（docs/01 §4.3「性能要求」、
docs/02 §4 Q2），不得先查全量再在 Python 里过滤。

本模块是唯一写这段 SQL 的地方。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import ColumnElement, and_, exists, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enums import AclSubjectType, PortalSort, ToolStatus, ToolType, ToolVisibility
from app.models.taxonomy import Category, Tag
from app.models.tool import Tool, ToolAcl, ToolTag
from app.models.user import User
from app.schemas.taxonomy import CategoryFacet, TypeFacet
from app.search import get_search_backend

#: 门户可见的状态：已发布 + 存在待审新版本（docs/02 §4 Q1/Q2）
PORTAL_STATUSES: tuple[str, ...] = (
    ToolStatus.APPROVED.value,
    ToolStatus.PENDING_UPDATE.value,
)

#: sort 白名单 → ORDER BY（docs/03 §3.3 / §1.6）
#: **绝不把 sort 拼进 SQL**：这里是查表，不是字符串拼接。
SORT_EXPRESSIONS: dict[str, tuple[ColumnElement[object], ...]] = {
    PortalSort.HOT.value: (Tool.download_count.desc(), Tool.id.desc()),
    PortalSort.NEW.value: (Tool.published_at.is_(None), Tool.published_at.desc(), Tool.id.desc()),
    PortalSort.NAME.value: (Tool.name.asc(), Tool.id.asc()),
    PortalSort.UPDATED_DESC.value: (Tool.updated_at.desc(), Tool.id.desc()),
}

#: `type` 查询参数的合法取值（非法值不报错，只是匹配不到 —— 与 sort 不同，
#: 类型筛选的非法值由一个显式的校验拦截，见 api/v1/tools.py）
VALID_TOOL_TYPES: frozenset[str] = frozenset(t.value for t in ToolType)


#: 恒假谓词（空结果），用于「全文索引明确返回零命中」时短路。
_FALSE = false()


@dataclass(frozen=True)
class VisibilityContext:
    """一次查询的可见性上下文。

    对应 docs/01 §4.3 的判定算法，但**编译成 SQL 谓词**而不是 Python 循环。
    """

    user_id: int | None = None
    group_ids: tuple[int, ...] = ()
    is_superadmin: bool = False
    allow_admin_view_private: bool = True

    @property
    def sees_everything(self) -> bool:
        """超管 + `portal.allow_admin_view_private` → 可见全部工具（D32）。"""
        return self.user_id is not None and self.is_superadmin and self.allow_admin_view_private

    @property
    def is_anonymous(self) -> bool:
        return self.user_id is None

    def clause(self) -> ColumnElement[bool] | None:
        """可见性谓词。

        返回 `None` 表示「无可见性限制」（超管全见）。
        """
        if self.is_anonymous:
            # Q1：未登录只看 public
            return Tool.visibility == ToolVisibility.PUBLIC.value

        if self.sees_everything:
            return None

        # Q2：EXISTS 子查询判定 restricted 的 ACL 命中
        group_ids = list(self.group_ids)
        subject_match = [
            and_(
                ToolAcl.subject_type == AclSubjectType.USER.value,
                ToolAcl.subject_id == self.user_id,
            )
        ]
        if group_ids:
            subject_match.append(
                and_(
                    ToolAcl.subject_type == AclSubjectType.GROUP.value,
                    ToolAcl.subject_id.in_(group_ids),
                )
            )
        acl_hit = exists(
            select(ToolAcl.id).where(
                ToolAcl.tool_id == Tool.id,
                or_(*subject_match),
            )
        )

        # Q2 的形状：public / private(owner) / restricted(EXISTS ACL) 三类并集。
        #
        # 额外补上 `owner_id = uid`：docs/01 §4.3 的判定算法第 2 步是
        # 「若 U 是 T.owner → 可见、可下载」，且它在可见性分支**之前**。
        # 少了这一条，用户会看不到自己创建的、尚未配置 ACL 的 restricted 工具
        # （Q2 的示例 SQL 没有覆盖这种情况）。两者在这里合并成同一个谓词，
        # 判定依然完全下推到 SQL。
        return or_(
            Tool.owner_id == self.user_id,
            Tool.visibility == ToolVisibility.PUBLIC.value,
            and_(
                Tool.visibility == ToolVisibility.RESTRICTED.value,
                acl_hit,
            ),
        )

    @classmethod
    async def build(
        cls,
        session: AsyncSession,
        *,
        user_id: int | None,
        roles: Sequence[str] = (),
        allow_admin_view_private: bool = True,
    ) -> VisibilityContext:
        group_ids: list[int] = []
        if user_id is not None:
            from app.repositories import users as users_repo

            group_ids = await users_repo.group_ids_for_user(session, user_id)
        return cls(
            user_id=user_id,
            group_ids=tuple(group_ids),
            is_superadmin="superadmin" in set(roles),
            allow_admin_view_private=allow_admin_view_private,
        )


@dataclass
class PortalFilters:
    """门户列表的筛选条件（docs/03 §3.3 查询参数）。"""

    q: str | None = None
    category_slugs: list[str] = field(default_factory=list)
    tag_names: list[str] = field(default_factory=list)
    tool_types: list[str] = field(default_factory=list)
    owner_username: str | None = None


def _base_clause() -> ColumnElement[bool]:
    """Q1/Q2 共有的基础条件：未删除 + 门户可见状态。"""
    return and_(
        Tool.deleted_at.is_(None),
        Tool.status.in_(PORTAL_STATUSES),
    )


def _search_clause(search_ids: set[int] | None, q: str) -> ColumnElement[bool]:
    """关键词条件。

    首选全文索引（SQLite FTS5），索引不可用时回退到 LIKE 扫描。
    回退路径同样覆盖标签名，保证「搜标签」在两种后端下行为一致。
    """
    if search_ids is not None:
        # 索引可用但一条没命中 → 直接返回恒假，不再退化成 LIKE
        # （否则会出现「索引说没有、LIKE 说有」的不一致结果）
        return Tool.id.in_(sorted(search_ids)) if search_ids else _FALSE

    pattern = f"%{q}%"
    tag_match = exists(
        select(ToolTag.tool_id)
        .join(Tag, Tag.id == ToolTag.tag_id)
        .where(ToolTag.tool_id == Tool.id, Tag.name.ilike(pattern))
    )
    return or_(
        Tool.name.ilike(pattern),
        Tool.summary.ilike(pattern),
        Tool.description_md.ilike(pattern),
        tag_match,
    )


def _filter_clauses(filters: PortalFilters) -> list[ColumnElement[bool]]:
    clauses: list[ColumnElement[bool]] = []

    if filters.category_slugs:
        clauses.append(
            Tool.category_id.in_(
                select(Category.id).where(Category.slug.in_(filters.category_slugs))
            )
        )

    if filters.tag_names:
        # 多选标签之间是 **OR**（命中任一即可）。docs/03 §3.3 未明确，此处按
        # 常见多选筛选语义实现，已在 checkpoint 报告里列为待确认项。
        clauses.append(
            Tool.id.in_(
                select(ToolTag.tool_id)
                .join(Tag, Tag.id == ToolTag.tag_id)
                .where(Tag.name.in_(filters.tag_names))
            )
        )

    if filters.tool_types:
        clauses.append(Tool.tool_type.in_(filters.tool_types))

    if filters.owner_username:
        clauses.append(
            Tool.owner_id.in_(select(User.id).where(User.username == filters.owner_username))
        )

    return clauses


def _portal_where(
    visibility: VisibilityContext, filters: PortalFilters, search_ids: set[int] | None
) -> list[ColumnElement[bool]]:
    clauses: list[ColumnElement[bool]] = [_base_clause()]
    vis = visibility.clause()
    if vis is not None:
        clauses.append(vis)
    clauses.extend(_filter_clauses(filters))
    if filters.q:
        clauses.append(_search_clause(search_ids, filters.q))
    return clauses


async def count_portal_tools(
    session: AsyncSession,
    *,
    visibility: VisibilityContext,
    filters: PortalFilters,
) -> int:
    search_ids: set[int] | None = None
    if filters.q:
        search_ids = await get_search_backend().matching_tool_ids(session, filters.q)
    stmt = (
        select(func.count())
        .select_from(Tool)
        .where(*_portal_where(visibility, filters, search_ids))
    )
    result = await session.execute(stmt)
    return int(result.scalar_one())


async def list_portal_tools(
    session: AsyncSession,
    *,
    visibility: VisibilityContext,
    filters: PortalFilters,
    sort: str,
    limit: int,
    offset: int,
) -> list[Tool]:
    search_ids: set[int] | None = None
    if filters.q:
        search_ids = await get_search_backend().matching_tool_ids(session, filters.q)

    order_by = SORT_EXPRESSIONS.get(sort)
    if order_by is None:  # 调用方已白名单校验，这里是兜底
        order_by = SORT_EXPRESSIONS[PortalSort.HOT.value]

    stmt = (
        select(Tool)
        .options(selectinload(Tool.tags))
        .where(*_portal_where(visibility, filters, search_ids))
        .order_by(*order_by)
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return list(result.scalars().unique().all())


async def category_tool_counts(
    session: AsyncSession, *, visibility: VisibilityContext
) -> dict[int, int]:
    """每个分类下**当前用户可见的**工具数（FR-TAX-07）。

    `facets.categories[].count` 与 `GET /api/v1/categories` 的 `tool_count`
    共用本函数，保证两处数字一致（docs/03 §3.3 的说明）。
    """
    vis = visibility.clause()
    stmt = (
        select(Category.id, func.count(Tool.id))
        .select_from(Tool)
        .join(Category, Category.id == Tool.category_id)
        .where(_base_clause())
        .group_by(Category.id)
    )
    if vis is not None:
        stmt = stmt.where(vis)
    result = await session.execute(stmt)
    return {int(row[0]): int(row[1]) for row in result.all()}


async def type_tool_counts(
    session: AsyncSession, *, visibility: VisibilityContext
) -> dict[str, int]:
    """按 `tool_type` 的可见工具数，用于 `facets.types`。"""
    vis = visibility.clause()
    stmt = (
        select(Tool.tool_type, func.count(Tool.id))
        .select_from(Tool)
        .where(_base_clause())
        .group_by(Tool.tool_type)
    )
    if vis is not None:
        stmt = stmt.where(vis)
    result = await session.execute(stmt)
    return {str(row[0]): int(row[1]) for row in result.all()}


async def build_category_facets(
    session: AsyncSession, *, visibility: VisibilityContext
) -> list[CategoryFacet]:
    """全部分类（含 0）的可见计数，顺序与 `GET /categories` 一致。"""
    counts = await category_tool_counts(session, visibility=visibility)
    result = await session.execute(
        select(Category.id, Category.slug, Category.name)
        .where(Category.is_active.is_(True))
        .order_by(Category.sort_order.asc(), Category.id.asc())
    )
    return [
        CategoryFacet(slug=row[1], name=row[2], count=counts.get(int(row[0]), 0))
        for row in result.all()
    ]


async def build_type_facets(
    session: AsyncSession, *, visibility: VisibilityContext
) -> list[TypeFacet]:
    """四种类型全部返回（含 0），顺序固定为 `ToolType` 的定义顺序。"""
    counts = await type_tool_counts(session, visibility=visibility)
    return [TypeFacet(value=t.value, count=counts.get(t.value, 0)) for t in ToolType]


async def get_by_id(session: AsyncSession, tool_id: int) -> Tool | None:
    result = await session.execute(
        select(Tool).options(selectinload(Tool.tags)).where(Tool.id == tool_id)
    )
    return result.scalar_one_or_none()


async def get_by_slug(session: AsyncSession, slug: str) -> Tool | None:
    result = await session.execute(
        select(Tool).options(selectinload(Tool.tags)).where(Tool.slug == slug)
    )
    return result.scalar_one_or_none()
