"""M15 · PostgreSQL 全文检索（`contracts/CONTRACT.md` §31，冻结）。

覆盖 §31.2 / §31.3 / §31.4 的设计不变量与 §31.5 的验证要求中**可以自动化**的部分。

跨方言的 id 集合一致性（§31.5②）**没法在单个 pytest 进程里断言** ——
一个进程只连一种方言（`tests/conftest.py` 用 `LOCALCRAFT_TEST_DATABASE_URL`
二选一），所以它由 `scripts/m15-dialect-consistency.sh` 各跑一次、比对 JSON
完成；两种方言的**真实 id 集合**贴在 M15 交付报告里。

需要「制造迁移 0009 没跑到」的用例会真的 `DROP COLUMN`，因此**必须**
在 `finally` 里把列、索引、向量全部还原，否则会污染同库的后续用例。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.db.session import SessionLocal
from tests.conftest import auth, login

TOOLS = "/api/v1/tools"

#: `DROP COLUMN` 之后要还原成什么样，见 `_pg_restore_column_and_reindex`。
_PG_ONLY = pytest.mark.skipif(
    settings.is_sqlite,
    reason="PG 专属：本用例要把 tools.search_vector 删掉以制造「0009 未跑到」",
)


# ---------------------------------------------------------------------------
# 造/还原 schema 的小工具（只在 PG 上用）
# ---------------------------------------------------------------------------
async def _pg_set_search_column(present: bool) -> None:
    """把 `tools.search_vector` 列（连同 GIN 索引）装上或拆掉。**会提交。**"""
    async with SessionLocal() as session:
        if present:
            await session.execute(
                text("ALTER TABLE tools ADD COLUMN IF NOT EXISTS search_vector tsvector")
            )
            await session.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_tools_search_vector "
                    "ON tools USING GIN (search_vector)"
                )
            )
        else:
            await session.execute(text("DROP INDEX IF EXISTS ix_tools_search_vector"))
            await session.execute(text("ALTER TABLE tools DROP COLUMN IF EXISTS search_vector"))
        await session.commit()


async def _pg_restore_column_and_reindex() -> None:
    """还原：装回列 + 索引，并把既有工具的向量**回填**回去。

    只装回空列是不够的 —— 后续用例（以及门户搜索）会依赖向量已经建好。
    """
    from app.search.postgres_fts import PostgresFtsBackend

    await _pg_set_search_column(True)
    async with SessionLocal() as session:
        await PostgresFtsBackend().reindex_all(session)
        await session.commit()


# ---------------------------------------------------------------------------
# §31.2 设计不变量（两方言都跑）
# ---------------------------------------------------------------------------
def test_search_vector_column_is_deliberately_absent_from_the_orm_model() -> None:
    """§31.2① 冻结：`tools.search_vector` **不得**出现在 ORM 模型里。

    理由：仓储到处用 `select(Tool)`，SQLAlchemy 会按模型生成完整列清单 ——
    写进模型会让门户列表查询**每次都多拉一个体积可观的 tsvector**
    （长描述的工具可达数百 KB），而列表页根本不看它。

    这条断言就是那个决定的回归防线：谁把它加回模型，测试立刻红。
    """
    from app.models.tool import Tool

    assert "search_vector" not in Tool.__table__.columns
    assert "search_vector" not in {attr.key for attr in Tool.__mapper__.column_attrs}


def test_pg_backend_pins_the_frozen_design_constants() -> None:
    """§31.2②/③/④/⑤ 的常量逐一钉住，防止被「顺手优化」掉。"""
    from app.search import postgres_fts

    assert postgres_fts.SEARCH_VECTOR_COLUMN == "search_vector"
    assert postgres_fts.GIN_INDEX_NAME == "ix_tools_search_vector"
    # 必须 simple：SQLite 的 unicode61 不做词干还原，用 english 两种方言结果会不同
    assert postgres_fts.TS_CONFIG == "simple", "§31.2②：不得改成 english"
    # 与 API 的 description_md max_length=100_000 同量纲（见该常量处的实测依据）
    assert postgres_fts.MAX_INDEX_TEXT_CHARS == 100_000

    source = Path(postgres_fts.__file__).read_text(encoding="utf-8")
    # §31.2④：不引入 ts_rank（排序交给主查询，否则两种方言结果顺序不一致）。
    # 注意匹配的是**调用形态**：模块注释里刻意写了「不引入 ts_rank」这句说明。
    assert "ts_rank(" not in source


def test_index_text_truncates_before_tokenizing() -> None:
    """§31.2⑤：索引文本必须先截断再分词，且截断真的生效。

    用 5,000,000 字符的描述（远超 API 的 100,000 上限，模拟绕过 API 的写入）。
    """
    from app.search.base import SearchDocument
    from app.search.postgres_fts import MAX_INDEX_TEXT_CHARS, index_text

    doc = SearchDocument(
        name="n" * 500,
        summary="s" * 1_000,
        description="d" * 5_000_000,
        tags=["x" * 100],
    )
    tokens = index_text(doc).split()
    # 截断在**原文**上做：原文 ≤ MAX 字符；分词后 token 数不可能超过原文字符数
    assert len(tokens) <= MAX_INDEX_TEXT_CHARS
    # 每个 CJK 字符会展开成 "字 " 两个字符，所以分词结果 ≤ 2× 上限
    assert len(index_text(doc)) <= 2 * MAX_INDEX_TEXT_CHARS

    # 短文本不受影响（不该无故截断合法内容）
    short = SearchDocument(name="内网工具", summary="", description="", tags=[])
    assert index_text(short) == "内 网 工 具"


# ---------------------------------------------------------------------------
# §31.5③ 长描述不报错（两方言都跑）
# ---------------------------------------------------------------------------
async def test_long_description_upserts_without_error(session, seeded) -> None:
    """§31.5③：超长 `description` 不得让 `upsert()` 抛异常。

    500,000 字符的互异短 token 在**未截断**时会让 PG 的 tsvector 直接超限
    （本机实测：105000 个互异 ASCII token 即报
    `string is too long for tsvector (1057986 bytes, max 1048575 bytes)`）。
    截断之后必须安然通过。
    """
    import itertools
    import string

    from app.search import SearchDocument, get_search_backend

    backend = get_search_backend()
    async with SessionLocal() as session:
        tool_id = int(
            (await session.execute(text("SELECT id FROM tools WHERE deleted_at IS NULL LIMIT 1")))
            .scalar_one()
        )

    # 4 字符 token：36^4 = 1,679,616 个可选，足够拼出 500,000 字符
    tokens = (
        "".join(c)
        for c in itertools.islice(
            itertools.product(string.ascii_lowercase + string.digits, repeat=4), 110_000
        )
    )
    huge = " ".join(tokens)[:500_000] + " 内网工具"
    assert len(huge) >= 500_000

    await backend.upsert(
        session,
        tool_id,
        SearchDocument(name="内网工具超长描述", summary="x", description=huge, tags=["长"]),
    )
    # 提交不得失败（PG 里若语句报错，事务会进 aborted 状态，commit 必炸）
    await session.commit()


@_PG_ONLY
async def test_untruncated_long_description_really_would_overflow_pg() -> None:
    """反向证据：**不**截断的话 PG 真的会报错 —— 说明截断是承重的，不是装饰。

    直接绕过 `index_text()`，把未截断的分词结果交给 `to_tsvector`。

    规模依据（本机 PG 16.14 实测）：短 ASCII lexeme 约 8 字节/个，
    131000 个互异 4 字符 token **通过**、132000 个报
    `string is too long for tsvector (1056000 bytes, max 1048575 bytes)`。
    这里用 132000 个，确保真的越限。
    """
    import itertools
    import string

    from app.search.base import tokenize

    tokens = (
        "".join(c)
        for c in itertools.islice(
            itertools.product(string.ascii_lowercase + string.digits, repeat=4), 132_000
        )
    )
    raw = " ".join(tokens)
    async with SessionLocal() as session:
        with pytest.raises(Exception) as excinfo:
            await session.execute(
                text("SELECT to_tsvector('simple', :t)"), {"t": tokenize(raw)}
            )
        assert "too long for tsvector" in str(excinfo.value)
        await session.rollback()


# ---------------------------------------------------------------------------
# §31.5④ / §31.2⑥ 列缺失时降级
# ---------------------------------------------------------------------------
@_PG_ONLY
async def test_search_column_missing_degrades_to_like_and_service_stays_up(
    client, seeded
) -> None:
    """§31.5④：把列删掉后搜索仍可用（走 LIKE）、服务不崩。"""
    from app.search import get_search_backend

    await _pg_set_search_column(False)
    # 清掉单例，模拟「全新进程 + 迁移 0009 没跑到」这个真实场景
    get_search_backend.cache_clear()
    try:
        backend = get_search_backend()
        assert backend.name == "postgres_fts"
        # `supports_reindex` 是同步属性、拿不到 session，所以初值是乐观的 True；
        # 真正的探测在第一次用到 session 时发生（下面这行）。
        assert backend.supports_reindex is True

        async with SessionLocal() as session:
            assert await backend.matching_tool_ids(session, "restricted") is None
            await session.rollback()

        assert backend.available is False, "探测到列不存在后必须降级"
        assert backend.supports_reindex is False

        # 接口层：搜索照旧可用（仓储回退 LIKE），**服务不崩**
        token = await login(client, "admin")
        response = await client.get(
            TOOLS, params={"q": "restricted-user"}, headers=auth(token)
        )
        assert response.status_code == 200, response.text
        assert "restricted-user" in {item["slug"] for item in response.json()["items"]}
    finally:
        await _pg_restore_column_and_reindex()
        get_search_backend.cache_clear()


@_PG_ONLY
async def test_pg_backend_upsert_is_a_noop_when_column_is_missing(session) -> None:
    """§31.2⑥：列缺失时 `upsert` / `delete` 是**无副作用的空操作**，且不炸事务。

    PG 与 SQLite 的实现差异就在这一点上：PG 里一条报错的语句会让**整个事务**
    进入 aborted 状态，调用方随后的 COMMIT 必然失败（建工具/改工具会 500）。
    所以本后端用系统目录**先探测**、探测为否时根本不发那条会报错的 SQL。
    """
    from app.search import SearchDocument
    from app.search.postgres_fts import PostgresFtsBackend

    await _pg_set_search_column(False)
    try:
        backend = PostgresFtsBackend()
        assert backend.supports_reindex is True  # 乐观初值

        async with SessionLocal() as session:
            await backend.upsert(session, 1, SearchDocument(name="内网工具"))
            await backend.delete(session, 1)
            # 关键：事务没有被 poison，commit 必须成功
            await session.commit()

        assert backend.available is False
        assert backend.supports_reindex is False
    finally:
        await _pg_restore_column_and_reindex()


# ---------------------------------------------------------------------------
# §31.4 / §31.5⑤ `reindex-search` 在 PG 上恢复可用
# ---------------------------------------------------------------------------
@_PG_ONLY
async def test_reindex_search_cli_fails_when_pg_search_column_is_missing(
    seeded, monkeypatch
) -> None:
    """§31.4：PG 上「列不存在」→ 降级 → CLI **必须非 0 退出**。

    M10 那条断言原本钉的是「PG 上没有索引 → 非 0 退出」。M15 给 PG 接上真索引后
    触发条件变成「列不存在 / 回退后端」，不变量本身不变 —— 本用例 +
    `tests/test_permission_deps.py::test_reindex_search_cli_never_reports_success_without_an_index`
    合起来把两个触发条件都钉住了。
    """
    import typer

    import app.cli as cli
    from app.search.postgres_fts import PostgresFtsBackend

    await _pg_set_search_column(False)
    try:
        monkeypatch.setattr(cli, "get_search_backend", PostgresFtsBackend)
        with pytest.raises(typer.Exit) as excinfo:
            await cli._reindex_search()
        assert excinfo.value.exit_code != 0, "列不存在时不能以 0 退出（否则等于谎报成功）"
    finally:
        await _pg_restore_column_and_reindex()


@_PG_ONLY
async def test_reindex_search_cli_succeeds_on_pg_and_reports_real_count(client, seeded) -> None:
    """§31.5⑤：PG 上 `reindex-search` 成功重建、条数正确、重建后可召回。"""
    from app.cli import _reindex_search

    async with SessionLocal() as session:
        expected = int(
            (
                await session.execute(
                    text("SELECT count(*) FROM tools WHERE deleted_at IS NULL")
                )
            ).scalar_one()
        )
        # 故意清空，证明命令真的重建了（而不是「索引本来就是好的」）
        await session.execute(text("UPDATE tools SET search_vector = NULL"))
        await session.commit()

    assert expected >= 1
    await _reindex_search()  # 不抛 typer.Exit == 退出 0

    async with SessionLocal() as session:
        indexed = int(
            (
                await session.execute(
                    text("SELECT count(*) FROM tools WHERE search_vector IS NOT NULL")
                )
            ).scalar_one()
        )
    assert indexed == expected, "重建条数必须等于未软删除的工具数"

    # 重建之后接口层能真的搜到
    token = await login(client, "admin")
    response = await client.get(
        TOOLS, params={"q": "restricted-user"}, headers=auth(token)
    )
    assert response.status_code == 200
    assert "restricted-user" in {item["slug"] for item in response.json()["items"]}
