"""PostgreSQL 全文检索实现（`tools.search_vector` 列 + GIN 索引）。

契约 §31.2 的六条设计在这里逐条落地，每一条都有实测依据（M15）。

与 SQLite 侧的**刻意不对称**（§31.2①，免得后人以为是漏做）：

  - SQLite 的 FTS5 **只有虚表这一种形态**，所以 0003 建了独立的
    `tool_search_index` 虚表，检索要查另一张表；
  - PG 用 `tools` 表上的 `search_vector tsvector` 列 + GIN 索引更好 ——
    **不需要 JOIN**，删工具/改工具也只是同一行的一次 UPDATE。

**★ `search_vector` 不进 ORM 模型**（`app/models/tool.py` 里没有它）：

  仓储到处用 `select(Tool)`，SQLAlchemy 会按模型生成完整列清单。
  把 tsvector 写进模型，**门户列表查询每次都会多拉一个体积可观的
  tsvector**（长描述的工具可达数百 KB），而列表页根本不看它。
  所以该列**只在迁移 0009 里存在**，由本后端用原生 SQL 读写。

**★ 检索配置必须用 `simple`，不能用 `english`**（§31.2②）：

  SQLite 侧 FTS5 用的是 `unicode61`，它**不做词干还原**。
  若 PG 用 `english`，`running` 会被还原成 `run`，同一个查询在两种
  方言下会给出**不同的结果集**。`simple` 只做小写化 + 分词，与
  `unicode61` 对齐。

**★ 索引文本必须截断**（§31.2⑤），依据见 `MAX_INDEX_TEXT_CHARS`。

**★ 列缺失时优雅降级**（§31.2⑥）：

  迁移（0009）没跑到时列不存在 → `available=False` →
  `matching_tool_ids()` 返回 `None` → 仓储自动走既有 LIKE 回退，
  **服务与工具写入都不得因此失败**（与 SQLite 侧同构）。

  与 SQLite 的一处实现差异（必须如此，否则会炸）：SQLite 下「试着执行、
  失败了就降级」是安全的，因为**失败的单条语句不会中止 SQLite 事务**。
  PostgreSQL **不是**这样 —— 事务里任何一条语句报错（例如
  `column "search_vector" does not exist`）会让整个事务进入 aborted 状态，
  调用方随后的 `COMMIT` 必然失败，**建工具/改工具会 500**。
  所以这里**不靠「撞错再降级」，而是先用一次系统目录查询探测列是否存在**
  （结果缓存在后端实例上，每进程至多一次），探测为否时**根本不发那条
  会报错的 SQL**。库表结构正确时探测只发生一次，开销可忽略。
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.search.base import SearchBackend, SearchDocument, tokenize, tokenize_query

logger = logging.getLogger(__name__)

#: 承载索引的列名。**只在迁移 0009 里存在**，ORM 模型里刻意没有它（§31.2①）。
SEARCH_VECTOR_COLUMN = "search_vector"

#: GIN 索引名（迁移 0009 里创建）。
GIN_INDEX_NAME = "ix_tools_search_vector"

#: 文本检索配置（§31.2②）。**不要改成 `english`** —— 见模块 docstring。
TS_CONFIG = "simple"

#: 索引文本的截断长度（§31.2⑤）。
#:
#: **依据（M15 在本机 PostgreSQL 16.14 实测，非估算）：**
#:
#:   1. PG 的 `tsvector` 有**硬性上限 1048575 字节**，超了直接报错而不是
#:      截断：`ERROR: string is too long for tsvector (1056000 bytes,
#:      max 1048575 bytes)`。所以这里必须自己截断。
#:   2. 上限约束的是**互异 lexeme 的数量与长度**，不是原文字符数。实测：
#:      `to_tsvector` 在 131000 个互异 4 字符 ASCII token 时通过、132000 个
#:      时报 1056000 字节 —— 即短 ASCII lexeme 约 8 字节/个。
#:   3. 本 tokenizer 的输出形态下，**单字 CJK lexeme 最贵**（3 字节 UTF-8，
#:      约 10 字节/个），所以「最坏输入」= 尽可能多的互异短 token。
#:      一个 100000 字符的输入最多只能产生约 39,472 个互异 CJK lexeme
#:      （`_CJK_RANGES` 的可辨识字符总数）+ 约 1.6 万个互异 ASCII lexeme，
#:      按上界估算约 0.52–0.56 MB，约为硬上限的 **50%（约 1.9 倍余量）**；
#:      实测该构造（100000 字符 → 54604 个 token）**通过** `to_tsvector`。
#:   4. 取 100000 而不是更小的值，是因为它**正好等于 API 对
#:      `description_md` 的 `max_length=100_000`**（`app/schemas/tool.py`
#:      /`admin.py`）—— 也就是说**任何通过 API 合法写入的内容都不可能触发
#:      tsvector 上限、也不会被截断**；本常量只为「绕过 API 的写入」
#:      （直接 SQL、导入脚本、夹具、将来放宽上限）兜底。
#:   5. 之所以不取更小值：对抗性最坏情形被 CJK 字符集本身**兜住了下限**
#:      （可辨识 CJK 字符只有 39,472 个，全部用上就已约 0.4 MB），
#:      截到 40000 字符仍有约 0.4 MB，收益很小却会真的截掉合法内容。
MAX_INDEX_TEXT_CHARS = 100_000

#: 列存在性探测。用系统目录而不是 `SELECT search_vector FROM tools`
#: —— 后者在列缺失时会报错并**中止 PG 事务**（见模块 docstring）。
#: `to_regclass('tools')` 走 search_path 解析，表不存在时返回 NULL。
_SCHEMA_PROBE = text(
    "SELECT 1 FROM pg_attribute "
    "WHERE attrelid = to_regclass('tools') "
    "AND attname = :col AND NOT attisdropped"
)


def index_text(doc: SearchDocument) -> str:
    """`SearchDocument` → 送进 `to_tsvector` 的文本（已截断、已分词）。

    截断**在分词之前**做（先截原文再 `tokenize()`），这样
    `MAX_INDEX_TEXT_CHARS` 是对**原文字符数**的约束，与 API 的
    `description_md` 上限是同一个量纲，依据可直接比对。
    """
    raw = doc.to_index_text()
    if len(raw) > MAX_INDEX_TEXT_CHARS:
        raw = raw[:MAX_INDEX_TEXT_CHARS]
    return tokenize(raw)


class PostgresFtsBackend(SearchBackend):
    """`tsvector` 列 + GIN 索引的 PG 全文检索后端。"""

    def __init__(self, *, available: bool = True) -> None:
        # 初值与 SQLite 侧一样是**乐观的 True**：`supports_reindex` 是同步属性，
        # 拿不到 session，没法在构造时就探测。真正的探测在第一次用到 session 时
        # 做一次（`_probed`），CLI 另有「调用后复查」兜住（见 app/cli.py）。
        self._available = available
        self._probed = False

    @property
    def name(self) -> str:
        return "postgres_fts"

    @property
    def available(self) -> bool:
        return self._available

    @property
    def supports_reindex(self) -> bool:
        """列存在（迁移 0009 已跑到）时为 True → `reindex-search` 可用。

        §31.4：M10 时 PG 没有索引，这里返回 False 让 CLI 明确报错退出；
        现在 PG 有了真索引，恢复成 True。列不存在时仍然是 False。
        """
        return self._available

    def _mark_unavailable(self, exc: Exception) -> None:
        if self._available:
            logger.warning("PG 全文索引不可用，回退到 LIKE 匹配: %s", exc)
        self._available = False

    async def _ensure_schema(self, session: AsyncSession) -> bool:
        """确认 `tools.search_vector` 存在（每实例至多探测一次）。"""
        if not self._available:
            return False
        if self._probed:
            return True
        try:
            result = await session.execute(_SCHEMA_PROBE, {"col": SEARCH_VECTOR_COLUMN})
            present = result.first() is not None
        except Exception as exc:  # pragma: no cover - 环境相关
            self._mark_unavailable(exc)
            return False
        # 只有拿到确定答案才缓存，避免把一次连接抖动永久固化成「没有索引」
        self._probed = True
        if not present:
            logger.warning(
                "tools.%s 不存在（迁移 0009 未执行），PG 全文检索降级为 LIKE 回退",
                SEARCH_VECTOR_COLUMN,
            )
            self._available = False
            return False
        return True

    async def upsert(self, session: AsyncSession, tool_id: int, doc: SearchDocument) -> None:
        if not await self._ensure_schema(session):
            return
        try:
            await session.execute(
                text(
                    f"UPDATE tools SET {SEARCH_VECTOR_COLUMN} = "
                    f"to_tsvector('{TS_CONFIG}', :txt) WHERE id = :tid"
                ),
                {"txt": index_text(doc), "tid": tool_id},
            )
        except Exception as exc:  # pragma: no cover - 环境相关
            self._mark_unavailable(exc)

    async def delete(self, session: AsyncSession, tool_id: int) -> None:
        """清空该行的索引向量。

        工具是**软删除**（`tools.deleted_at`），行还在，所以不能像 SQLite
        虚表那样整行删掉；把向量置 NULL 即可让它在 `@@` 里不再命中。
        """
        if not await self._ensure_schema(session):
            return
        try:
            await session.execute(
                text(f"UPDATE tools SET {SEARCH_VECTOR_COLUMN} = NULL WHERE id = :tid"),
                {"tid": tool_id},
            )
        except Exception as exc:  # pragma: no cover - 环境相关
            self._mark_unavailable(exc)

    async def matching_tool_ids(self, session: AsyncSession, query: str) -> set[int] | None:
        tokens = tokenize_query(query)
        if not tokens:
            return None
        if not await self._ensure_schema(session):
            return None
        # 与 SQLite 侧一致：token 之间是 **AND** 语义。
        # `plainto_tsquery` 把空格分隔的词视为 AND，正好对齐 SQLite 的
        # `" AND "` 拼接；同时用户输入不会被当成 tsquery 语法
        # （`plainto_` 会转义 `&`、`|`、`!`、`:` 等元字符），
        # 这也是「不把用户输入拼进查询语法」在同一条原则上的一致性做法。
        #
        # 不引入 `ts_rank`（§31.2④）：`matching_tool_ids` 只返回 set，
        # 排序由主查询（热门/最新/名称）决定，与 SQLite 侧完全一致。
        try:
            result = await session.execute(
                text(
                    f"SELECT id FROM tools WHERE {SEARCH_VECTOR_COLUMN} @@ "
                    f"plainto_tsquery('{TS_CONFIG}', :q)"
                ),
                {"q": " ".join(tokens)},
            )
            return {int(row[0]) for row in result.all()}
        except Exception as exc:  # pragma: no cover - 环境相关
            self._mark_unavailable(exc)
            return None

    async def reindex_all(self, session: AsyncSession) -> int:
        """从 `tools` 表全量重建索引（`python -m app.cli reindex-search`）。

        走 Python 侧的 `tokenize()` 而不是把分词写成 SQL 表达式：只有这样才能
        与 SQLite 侧、与迁移 0009 的回填**逐 lexeme 一致**。SQL 版
        （`regexp_replace` 给汉字加空格）在本仓实测**不等价** ——
        PG 的默认 parser 会把 `tools?q=` 当成一个 lexeme，而 `tokenize()`
        会切成 `tools`/`q`，于是「搜 tools」在两条路径下结果不同。
        """
        if not await self._ensure_schema(session):
            return 0
        # 先清空，好让已软删除/已不存在的行不残留旧向量（对齐 SQLite 的 DELETE）
        await session.execute(text(f"UPDATE tools SET {SEARCH_VECTOR_COLUMN} = NULL"))
        result = await session.execute(
            text(
                "SELECT t.id, t.name, t.summary, t.description_md, "
                "COALESCE(string_agg(tg.display_name, ' '), '') AS tag_names "
                "FROM tools t "
                "LEFT JOIN tool_tags tt ON tt.tool_id = t.id "
                "LEFT JOIN tags tg ON tg.id = tt.tag_id "
                "WHERE t.deleted_at IS NULL "
                "GROUP BY t.id"
            )
        )
        rows = result.all()
        count = 0
        for row in rows:
            await self.upsert(
                session,
                int(row[0]),
                SearchDocument(
                    name=row[1] or "",
                    summary=row[2] or "",
                    description=row[3] or "",
                    tags=[row[4]] if row[4] else [],
                ),
            )
            count += 1
        return count
