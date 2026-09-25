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
    roles: frozenset[str] = frozenset()
    is_superadmin: bool = False
    allow_admin_view_private: bool = True

    def cache_key(self) -> str:
        """facet 缓存的键（M5 性能优化）。

        **必须包含全部影响可见性的输入**（易错点清单第 3 条）：
        缓存跨用户复用会直接造成越权 —— 把 A 用户能看到的工具名与计数泄露给 B。
        这里把 user_id / group_ids / roles / 超管位 / 私有可见开关全部编进键，
        任何一个不同就是不同的缓存条目。
        """
        return "|".join(
            (
                str(self.user_id),
                ",".join(str(g) for g in sorted(self.group_ids)),
                ",".join(sorted(self.roles)),
                "1" if self.is_superadmin else "0",
                "1" if self.allow_admin_view_private else "0",
            )
        )

    @property
    def sees_everything(self) -> bool:
        """超管 + `portal.allow_admin_view_private` → 可见全部工具（D32）。"""
        return self.user_id is not None and self.is_superadmin and self.allow_admin_view_private

    @property
    def is_approver(self) -> bool:
        """是否具备审批角色（approver / superadmin）。

        用于详情页的状态可见范围（contracts §15.5）：审批人需要预览待审与已下架工具。
        """
        return bool(self.roles & {"approver", "superadmin"})

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
        role_set = frozenset(roles)
        return cls(
            user_id=user_id,
            group_ids=tuple(group_ids),
            roles=role_set,
            is_superadmin="superadmin" in role_set,
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


async def combined_tool_counts(
    session: AsyncSession, *, visibility: VisibilityContext
) -> tuple[dict[int | None, int], dict[str, int]]:
    """**一次查询**同时算出「按分类」与「按类型」的可见计数。

    M5 性能优化点之一：原先 `category_tool_counts` 与 `type_tool_counts`
    各发一条聚合查询，两条都要重算一遍可见性 EXISTS 子查询。
    合成 `GROUP BY category_id, tool_type` 后只有一条，
    行数上限是「分类数 × 类型数」（个位数 × 4），在 Python 里再汇总即可。
    """
    vis = visibility.clause()
    stmt = (
        select(Tool.category_id, Tool.tool_type, func.count(Tool.id))
        .select_from(Tool)
        .where(_base_clause())
        .group_by(Tool.category_id, Tool.tool_type)
    )
    if vis is not None:
        stmt = stmt.where(vis)
    result = await session.execute(stmt)

    by_category: dict[int | None, int] = {}
    by_type: dict[str, int] = {}
    for category_id, tool_type, count in result.all():
        value = int(count)
        key = int(category_id) if category_id is not None else None
        by_category[key] = by_category.get(key, 0) + value
        by_type[str(tool_type)] = by_type.get(str(tool_type), 0) + value
    return by_category, by_type


async def type_tool_counts(
    session: AsyncSession, *, visibility: VisibilityContext
) -> dict[str, int]:
    """按 `tool_type` 的可见工具数，用于 `facets.types`。"""
    _by_category, by_type = await combined_tool_counts(session, visibility=visibility)
    return by_type


#: facet 短时缓存（M5 性能优化点之二）
#:
#: 为什么值得缓存：facets 是两次聚合（现在合成一次），且**只在 `page == 1` 时**才需要。
#: 它在门户首页每次刷新都跑一遍，而结果只在「工具有增删改」时才变。
#:
#: 缓存键：`VisibilityContext.cache_key()` —— 含 user_id / group_ids / roles /
#:   超管位 / 私有可见开关。**任何影响可见性的输入变化都会落到不同条目**，
#:   绝不复用（易错点清单第 3 条：跨用户复用会越权泄露）。
#: TTL：30 秒。选 30 秒的理由：这是「管理员刚发布一个工具，首页 facet 计数多久跟上」
#:   的上界。30 秒对计数类展示完全可接受，而它把首页的聚合查询摊薄到约 1/30。
#: 失效条件：**只有 TTL**。不按写入失效是刻意的 —— 要在所有工具写路径上挂失效钩子，
#:   耦合面大且容易漏（漏一处就是脏缓存）。代价是「最多 30 秒的计数陈旧」，
#:   而列表与详情**不走这个缓存**，所以用户点进去看到的永远是最新的。
_FACET_CACHE: dict[str, tuple[float, list[CategoryFacet], list[TypeFacet]]] = {}


def _cache_enabled() -> bool:
    """缓存开关。`LOCALCRAFT_DISABLE_FACET_CACHE=1` 时绕过缓存。

    存在的意义有两个：
      1) **可验证性** —— 没有开关就没法在同一份构建上做「开/关缓存」的 A/B 对比，
         性能结论就只能靠跨版本比较，容易被环境差异干扰；
      2) 排障 —— 怀疑 facet 计数陈旧时，临时关掉即可确认是不是缓存造成的。
    生产默认开启。
    """
    import os

    return os.environ.get("LOCALCRAFT_DISABLE_FACET_CACHE") != "1"


_FACET_CACHE_TTL_SECONDS = 30.0
#: 缓存条目上限。超过就整体清空（简单的防膨胀策略；门户的可见性组合数远小于此）
_FACET_CACHE_MAX_ENTRIES = 256


def clear_facet_cache() -> None:
    """清空 facet 缓存。测试用；也供将来「立刻刷新」的管理动作调用。"""
    _FACET_CACHE.clear()


def facet_cache_stats() -> dict[str, int]:
    """缓存规模，供排查与测试断言。"""
    return {
        "entries": len(_FACET_CACHE),
        "ttl_seconds": int(_FACET_CACHE_TTL_SECONDS),
        "enabled": int(_cache_enabled()),
    }


async def build_facets(
    session: AsyncSession, *, visibility: VisibilityContext
) -> tuple[list[CategoryFacet], list[TypeFacet]]:
    """`facets.categories` + `facets.types`（带 30 秒短时缓存）。"""
    import time

    key = visibility.cache_key()
    now = time.monotonic()
    if _cache_enabled():
        cached = _FACET_CACHE.get(key)
        if cached is not None and now - cached[0] < _FACET_CACHE_TTL_SECONDS:
            return cached[1], cached[2]

    counts, type_counts = await combined_tool_counts(session, visibility=visibility)
    result = await session.execute(
        select(Category.id, Category.slug, Category.name)
        .where(Category.is_active.is_(True))
        .order_by(Category.sort_order.asc(), Category.id.asc())
    )
    categories = [
        CategoryFacet(slug=row[1], name=row[2], count=counts.get(int(row[0]), 0))
        for row in result.all()
    ]
    types = [TypeFacet(value=t.value, count=type_counts.get(t.value, 0)) for t in ToolType]

    if _cache_enabled():
        if len(_FACET_CACHE) >= _FACET_CACHE_MAX_ENTRIES:
            _FACET_CACHE.clear()
        _FACET_CACHE[key] = (now, categories, types)
    return categories, types


async def build_category_facets(
    session: AsyncSession, *, visibility: VisibilityContext
) -> list[CategoryFacet]:
    """全部分类（含 0）的可见计数，顺序与 `GET /categories` 一致。"""
    categories, _types = await build_facets(session, visibility=visibility)
    return categories


async def build_type_facets(
    session: AsyncSession, *, visibility: VisibilityContext
) -> list[TypeFacet]:
    """四种类型全部返回（含 0），顺序固定为 `ToolType` 的定义顺序。"""
    _categories, types = await build_facets(session, visibility=visibility)
    return types


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


# ===========================================================================
# M2：详情、我的工具
# ===========================================================================
#: 门户详情对**普通用户**开放的状态。
#:
#: docs/01 §4.1 的状态表把 `draft`/`pending`/`rejected`/`offline` 的
#: 「门户可见」一列标为「否」，所以普通人只能看到这两个。
DETAIL_VISIBLE_STATUSES: tuple[str, ...] = (
    ToolStatus.APPROVED.value,
    ToolStatus.PENDING_UPDATE.value,
)

#: **审批人**额外可见的状态（contracts §15.5，配套修订后的 docs/01 §4.3 第 1 步）。
#:
#: 这解决了「§4.3 第 1 步只给 owner 与 superadmin / FR-APPR-06 又要求审批人能预览」
#: 的文档自相矛盾：审批人的预览入口就从这里走，`/me/tools/*` 则收为 owner 命名空间。
#: `draft` **不在内** —— 未提交的草稿没有审批理由可见。
APPROVER_VISIBLE_STATUSES: tuple[str, ...] = (
    ToolStatus.PENDING.value,
    ToolStatus.PENDING_UPDATE.value,
    ToolStatus.OFFLINE.value,
    ToolStatus.APPROVED.value,
)


def detail_scope_clause(visibility: VisibilityContext) -> ColumnElement[bool]:
    """详情/二级资源的统一可见范围。

    与门户**列表**的差别在于多了状态维度与角色维度（docs/01 §4.3 步骤 1，
    按 contracts §15.5 修订后）：

      - 所有人：`approved` / `pending_update`
      - 审批人（approver / superadmin）：额外可见 `pending` / `offline`
      - `draft` 与 `rejected`：**任何角色**都不通过门户详情暴露
        （owner 走 `/me/tools/{id}`）
      - 超管：可见任意状态

    `deleted_at IS NULL` 对所有角色都生效 —— 软删除的工具从任何入口都消失
    （回收站走 `/admin/recycle-bin`）。
    """
    clauses: list[ColumnElement[bool]] = [Tool.deleted_at.is_(None)]
    if visibility.is_superadmin:
        return and_(*clauses)

    allowed = set(DETAIL_VISIBLE_STATUSES)
    if visibility.is_approver:
        allowed |= set(APPROVER_VISIBLE_STATUSES)
    clauses.append(Tool.status.in_(tuple(allowed)))
    return and_(*clauses)


async def get_visible_by_slug(
    session: AsyncSession, slug: str, *, visibility: VisibilityContext
) -> Tool | None:
    """按 slug 取工具，**可见性判定下推到 SQL**。

    无权访问返回 `None`（调用方转成 404）—— 而不是先查出来再在 Python 里判断，
    也不是返回 403（FR-FILE-08：403 会泄露资源存在性）。

    与 M1 的门户列表复用同一个 `VisibilityContext.clause()`，
    保证「列表里看得到 = 详情能打开」。
    """
    stmt = (
        select(Tool)
        .options(selectinload(Tool.tags))
        .where(Tool.slug == slug, detail_scope_clause(visibility))
    )
    vis = visibility.clause()
    if vis is not None:
        stmt = stmt.where(vis)
    result = await session.execute(stmt)
    return result.scalars().unique().one_or_none()


async def get_visible_by_id(
    session: AsyncSession, tool_id: int, *, visibility: VisibilityContext
) -> Tool | None:
    """按 id 取工具并做同样的可见性过滤（下载、图片等二级资源用）。"""
    stmt = (
        select(Tool)
        .options(selectinload(Tool.tags))
        .where(Tool.id == tool_id, detail_scope_clause(visibility))
    )
    vis = visibility.clause()
    if vis is not None:
        stmt = stmt.where(vis)
    result = await session.execute(stmt)
    return result.scalars().unique().one_or_none()


async def slug_exists(session: AsyncSession, slug: str) -> bool:
    result = await session.execute(select(Tool.id).where(Tool.slug == slug).limit(1))
    return result.first() is not None


async def list_owned_tools(
    session: AsyncSession,
    *,
    owner_id: int,
    statuses: list[str] | None = None,
    q: str | None = None,
    tool_types: list[str] | None = None,
    category_slugs: list[str] | None = None,
    sort: str = "-updated_at",
    limit: int = 20,
    offset: int = 0,
) -> list[Tool]:
    """`GET /me/tools` —— 含草稿/待审/驳回/下架，**排除软删除**。

    与门户列表不同：这里不做可见性过滤（owner 看自己的全部），
    但必须排除 `deleted_at`（软删除的工具只可能在回收站里，那是 M3）。
    """
    stmt = (
        select(Tool)
        .options(selectinload(Tool.tags))
        .where(Tool.owner_id == owner_id, Tool.deleted_at.is_(None))
    )
    if statuses:
        stmt = stmt.where(Tool.status.in_(statuses))
    if tool_types:
        stmt = stmt.where(Tool.tool_type.in_(tool_types))
    if category_slugs:
        stmt = stmt.where(
            Tool.category_id.in_(
                select(Category.id).where(Category.slug.in_(category_slugs))
            )
        )
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(or_(Tool.name.ilike(pattern), Tool.summary.ilike(pattern)))

    order_by = SORT_EXPRESSIONS.get(sort) or (
        Tool.updated_at.desc(),
        Tool.id.desc(),
    )
    return list(
        (
            await session.execute(stmt.order_by(*order_by).limit(limit).offset(offset))
        )
        .scalars()
        .unique()
        .all()
    )


async def count_owned_tools(
    session: AsyncSession,
    *,
    owner_id: int,
    statuses: list[str] | None = None,
    q: str | None = None,
    tool_types: list[str] | None = None,
    category_slugs: list[str] | None = None,
) -> int:
    stmt = (
        select(func.count())
        .select_from(Tool)
        .where(Tool.owner_id == owner_id, Tool.deleted_at.is_(None))
    )
    if statuses:
        stmt = stmt.where(Tool.status.in_(statuses))
    if tool_types:
        stmt = stmt.where(Tool.tool_type.in_(tool_types))
    if category_slugs:
        stmt = stmt.where(
            Tool.category_id.in_(
                select(Category.id).where(Category.slug.in_(category_slugs))
            )
        )
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(or_(Tool.name.ilike(pattern), Tool.summary.ilike(pattern)))
    return int((await session.execute(stmt)).scalar_one())


async def owner_status_counts(session: AsyncSession, owner_id: int) -> dict[str, int]:
    """个人中心的状态分布（`GET /me/stats`）。"""
    result = await session.execute(
        select(Tool.status, func.count(Tool.id))
        .where(Tool.owner_id == owner_id, Tool.deleted_at.is_(None))
        .group_by(Tool.status)
    )
    return {str(row[0]): int(row[1]) for row in result.all()}
