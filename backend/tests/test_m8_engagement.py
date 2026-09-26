"""M8：收藏 / 点赞 / 我的收藏 / 上传去重 / 管理数字概览（contracts §23）。

覆盖任务书 B6~B11 的硬要求：

- 4 个端点的**幂等性**（重复调用计数不变）+ 并发重复 PUT 的唯一约束兜底
- 匿名请求下列表/详情的 4 个字段取值
- **收藏了不可见工具时不泄漏**（B7）
- `estimated_saving_minutes` 的边界（0 / 1 / 1440 / 1441 / null）
- `savings` 公式：**手算期望值**再断言（不拿实现输出反推）
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import delete, select

from app.core.timeutil import ensure_utc, utcnow
from app.db.session import SessionLocal
from app.models.engagement import ToolFavorite, ToolLike
from app.models.stats import DownloadLog
from app.models.tool import Tool, ToolVersion
from app.repositories import engagement as engagement_repo
from tests.conftest import auth, login
from tests.m2_helpers import (
    approve,
    create_tool,
    submit,
    upload_version,
    zip_bytes,
)

TOOLS = "/api/v1/tools"
FAVORITES = "/api/v1/me/favorites"
INSIGHTS = "/api/v1/admin/stats/insights"


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
async def _publish(
    client, owner_token: str, approver_token: str, *, name: str, content: bytes
) -> tuple[int, str]:
    """创建 → 上传 → 提交 → 批准；返回 `(tool_id, slug)`。"""
    tool = await create_tool(client, owner_token, name=name)
    created = await upload_version(
        client, owner_token, tool["id"], version="1.0.0", file_bytes=content, file_name="p.zip"
    )
    assert created.status_code == 201, created.text
    assert (await submit(client, owner_token, tool["id"])).status_code == 200
    assert (await approve(client, approver_token, tool["id"])).status_code == 200
    detail = await client.get(f"/api/v1/me/tools/{tool['id']}", headers=auth(owner_token))
    assert detail.status_code == 200, detail.text
    return tool["id"], detail.json()["slug"]


async def _clear_engagement(user_id: int) -> None:
    """清掉某用户的收藏/点赞关系，保证用例之间互不干扰。"""
    async with SessionLocal() as session:
        await session.execute(delete(ToolFavorite).where(ToolFavorite.user_id == user_id))
        await session.execute(delete(ToolLike).where(ToolLike.user_id == user_id))
        await session.commit()


async def _counter_values(tool_id: int) -> tuple[int, int]:
    async with SessionLocal() as session:
        row = (
            await session.execute(
                select(Tool.favorite_count, Tool.like_count).where(Tool.id == tool_id)
            )
        ).one()
    return int(row[0]), int(row[1])


async def _mutate_tool(tool_id: int, **values: object) -> None:
    async with SessionLocal() as session:
        await session.execute(
            Tool.__table__.update().where(Tool.id == tool_id).values(**values)
        )
        await session.commit()


async def _add_download(tool_id: int, *, user_id: int | None, when: datetime | None = None) -> None:
    async with SessionLocal() as session:
        session.add(
            DownloadLog(tool_id=tool_id, user_id=user_id, created_at=when or utcnow())
        )
        await session.commit()


# ---------------------------------------------------------------------------
# B6 —— 4 个端点：幂等
# ---------------------------------------------------------------------------
async def test_favorite_and_like_are_idempotent(client, seeded) -> None:
    approver = await login(client, "approver")
    outsider = await login(client, "outsider")
    await _clear_engagement(seeded.users["outsider"])
    tool_id, slug = await _publish(
        client, approver, approver, name="M8 幂等工具", content=zip_bytes({"a.txt": b"idem"})
    )

    # 重复 PUT × 3：都成功，计数只加一次
    for expected_round in (1, 2, 3):
        response = await client.put(f"{TOOLS}/{slug}/favorite", headers=auth(outsider))
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["is_favorited"] is True
        assert body["favorite_count"] == 1, f"第 {expected_round} 次 PUT 把计数加多了"
    assert await _counter_values(tool_id) == (1, 0)

    for _ in range(3):
        response = await client.put(f"{TOOLS}/{slug}/like", headers=auth(outsider))
        assert response.status_code == 200, response.text
        assert response.json()["like_count"] == 1
        assert response.json()["is_liked"] is True
    assert await _counter_values(tool_id) == (1, 1)

    # DELETE：第一次减计数，之后重复调用仍然成功且计数不再降
    for _ in range(2):
        response = await client.delete(f"{TOOLS}/{slug}/favorite", headers=auth(outsider))
        assert response.status_code == 200, response.text
        assert response.json()["is_favorited"] is False
        assert response.json()["favorite_count"] == 0
    for _ in range(2):
        response = await client.delete(f"{TOOLS}/{slug}/like", headers=auth(outsider))
        assert response.status_code == 200, response.text
        assert response.json()["is_liked"] is False
        assert response.json()["like_count"] == 0
    assert await _counter_values(tool_id) == (0, 0)

    # 「未收藏时 DELETE」不报 404 —— 上面第二轮 DELETE 已经证明了这一点
    # （它是在计数已为 0、关系行已删除的情况下再次调用的）。


async def test_engagement_response_is_self_explanatory(client, seeded) -> None:
    approver = await login(client, "approver")
    outsider = await login(client, "outsider")
    await _clear_engagement(seeded.users["outsider"])
    _tool_id, slug = await _publish(
        client, approver, approver, name="M8 响应形状", content=zip_bytes({"b.txt": b"shape"})
    )
    response = await client.put(f"{TOOLS}/{slug}/like", headers=auth(outsider))
    assert response.status_code == 200, response.text
    assert set(response.json()) == {
        "tool_id",
        "slug",
        "is_favorited",
        "is_liked",
        "favorite_count",
        "like_count",
    }


async def test_concurrent_duplicate_put_hits_unique_constraint_not_500(
    client, seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    """并发重复 PUT：唯一约束兜底，**不能变成 500**（契约 §23.4）。

    真并发的时序无法在单进程里稳定复现，这里直接构造「关系已存在、但存在性检查
    说没有」的竞态窗口：把 `relation_exists` 打桩成恒 False，插入就会撞
    `UNIQUE(user_id, tool_id)`，服务层必须捕获它并按成功处理、且**不重复加计数**。
    """
    approver = await login(client, "approver")
    outsider = await login(client, "outsider")
    await _clear_engagement(seeded.users["outsider"])
    tool_id, slug = await _publish(
        client, approver, approver, name="M8 并发兜底", content=zip_bytes({"c.txt": b"race"})
    )

    first = await client.put(f"{TOOLS}/{slug}/favorite", headers=auth(outsider))
    assert first.status_code == 200 and first.json()["favorite_count"] == 1

    async def _always_missing(*_args: object, **_kwargs: object) -> bool:
        return False

    monkeypatch.setattr(engagement_repo, "relation_exists", _always_missing)

    second = await client.put(f"{TOOLS}/{slug}/favorite", headers=auth(outsider))
    assert second.status_code == 200, second.text
    assert second.json()["is_favorited"] is True
    assert second.json()["favorite_count"] == 1, "并发兜底路径不该再加一次计数"
    assert await _counter_values(tool_id) == (1, 0)


async def test_engagement_requires_authentication_and_non_viewer_role(client, seeded) -> None:
    approver = await login(client, "approver")
    _tool_id, slug = await _publish(
        client, approver, approver, name="M8 权限", content=zip_bytes({"d.txt": b"perm"})
    )

    anonymous = await client.put(f"{TOOLS}/{slug}/favorite")
    assert anonymous.status_code == 401, anonymous.text

    viewer = await login(client, "viewer")
    forbidden = await client.put(f"{TOOLS}/{slug}/favorite", headers=auth(viewer))
    assert forbidden.status_code == 403, forbidden.text
    assert forbidden.json()["code"] == "FORBIDDEN"

    # 只读角色仍可读自己的收藏列表（read_guard 允许 viewer）
    listed = await client.get(FAVORITES, headers=auth(viewer))
    assert listed.status_code == 200, listed.text


async def test_counter_never_goes_negative(client, seeded) -> None:
    """契约 §23.4：「计数不允许出现负数」。

    构造一个**不一致的历史状态**：关系行存在但计数为 0（人工改库 / 老数据）。
    此时取消收藏不能让计数变成 -1 —— 减法是 `CASE WHEN count > 0` 夹到 0。
    """
    approver = await login(client, "approver")
    outsider = await login(client, "outsider")
    await _clear_engagement(seeded.users["outsider"])
    tool_id, slug = await _publish(
        client, approver, approver, name="M8 计数不为负", content=zip_bytes({"neg.txt": b"neg"})
    )
    async with SessionLocal() as session:
        session.add(
            ToolFavorite(
                user_id=seeded.users["outsider"], tool_id=tool_id, created_at=utcnow()
            )
        )
        await session.commit()
    assert await _counter_values(tool_id) == (0, 0)

    response = await client.delete(f"{TOOLS}/{slug}/favorite", headers=auth(outsider))
    assert response.status_code == 200, response.text
    assert response.json()["favorite_count"] == 0
    assert response.json()["is_favorited"] is False
    assert await _counter_values(tool_id) == (0, 0), "计数掉到了负数区间"


async def test_engagement_on_invisible_tool_is_404(client, seeded) -> None:
    approver = await login(client, "approver")
    outsider = await login(client, "outsider")
    await _clear_engagement(seeded.users["outsider"])
    tool_id, slug = await _publish(
        client, approver, approver, name="M8 不可见工具", content=zip_bytes({"e.txt": b"hid"})
    )
    await _mutate_tool(tool_id, status="offline")

    response = await client.put(f"{TOOLS}/{slug}/favorite", headers=auth(outsider))
    assert response.status_code == 404, response.text


# ---------------------------------------------------------------------------
# B8 —— 匿名请求下 4 个字段
# ---------------------------------------------------------------------------
async def test_anonymous_list_and_detail_return_counts_but_false_flags(client, seeded) -> None:
    approver = await login(client, "approver")
    outsider = await login(client, "outsider")
    await _clear_engagement(seeded.users["outsider"])
    _tool_id, slug = await _publish(
        client, approver, approver, name="M8 匿名字段", content=zip_bytes({"f.txt": b"anon"})
    )
    # 一个已登录用户先收藏 + 点赞，让计数非 0
    assert (await client.put(f"{TOOLS}/{slug}/favorite", headers=auth(outsider))).status_code == 200
    assert (await client.put(f"{TOOLS}/{slug}/like", headers=auth(outsider))).status_code == 200

    detail = await client.get(f"{TOOLS}/{slug}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["favorite_count"] == 1
    assert body["like_count"] == 1
    assert body["is_favorited"] is False, "匿名请求者的 is_favorited 必须恒为 false"
    assert body["is_liked"] is False, "匿名请求者的 is_liked 必须恒为 false"

    listing = await client.get(TOOLS, params={"q": "M8 匿名字段"})
    assert listing.status_code == 200, listing.text
    item = listing.json()["items"][0]
    assert item["favorite_count"] == 1
    assert item["like_count"] == 1
    assert item["is_favorited"] is False
    assert item["is_liked"] is False


# ---------------------------------------------------------------------------
# B7 —— 我的收藏
# ---------------------------------------------------------------------------
async def test_favorites_list_shape_order_and_pagination(client, seeded) -> None:
    approver = await login(client, "approver")
    outsider = await login(client, "outsider")
    await _clear_engagement(seeded.users["outsider"])

    _first_id, first_slug = await _publish(
        client, approver, approver, name="M8 收藏甲", content=zip_bytes({"g1.txt": b"one"})
    )
    _second_id, second_slug = await _publish(
        client, approver, approver, name="M8 收藏乙", content=zip_bytes({"g2.txt": b"two"})
    )
    for slug in (first_slug, second_slug):
        assert (await client.put(f"{TOOLS}/{slug}/favorite", headers=auth(outsider))).status_code == 200

    listing = await client.get(FAVORITES, headers=auth(outsider))
    assert listing.status_code == 200, listing.text
    page = listing.json()
    assert page["total"] == 2
    assert page["pages"] == 1
    assert [item["slug"] for item in page["items"]] == [second_slug, first_slug], (
        "必须按收藏时间 created_at DESC 排序（后收藏的在前）"
    )
    # 复用门户列表项形状（contracts §23.4）
    portal = await client.get(TOOLS, params={"q": "M8 收藏甲"})
    assert set(page["items"][0]) == set(portal.json()["items"][0])
    assert page["items"][0]["is_favorited"] is True
    assert page["facets"] is None

    # 分页
    paged = await client.get(FAVORITES, params={"page": 1, "page_size": 1}, headers=auth(outsider))
    assert paged.json()["total"] == 2
    assert paged.json()["pages"] == 2
    assert len(paged.json()["items"]) == 1
    second_page = await client.get(
        FAVORITES, params={"page": 2, "page_size": 1}, headers=auth(outsider)
    )
    assert len(second_page.json()["items"]) == 1
    assert second_page.json()["items"][0]["slug"] != paged.json()["items"][0]["slug"]


async def test_favorites_never_leak_invisible_tools(client, seeded) -> None:
    """**本轮最容易出错的地方**（任务书 B7）。

    outsider 收藏 4 个已发布工具，随后它们分别变成 下架 / 转私有（且 outsider
    不是 owner）/ 软删除，只有 1 个保持已发布。`/me/favorites` 必须只返回那 1 个。
    """
    approver = await login(client, "approver")
    outsider = await login(client, "outsider")
    await _clear_engagement(seeded.users["outsider"])

    visible_id, visible_slug = await _publish(
        client, approver, approver, name="M8 仍可见", content=zip_bytes({"h1.txt": b"keep"})
    )
    offline_id, offline_slug = await _publish(
        client, approver, approver, name="M8 转下架", content=zip_bytes({"h2.txt": b"off"})
    )
    private_id, private_slug = await _publish(
        client, approver, approver, name="M8 转私有", content=zip_bytes({"h3.txt": b"priv"})
    )
    deleted_id, deleted_slug = await _publish(
        client, approver, approver, name="M8 被删除", content=zip_bytes({"h4.txt": b"del"})
    )
    for slug in (visible_slug, offline_slug, private_slug, deleted_slug):
        assert (await client.put(f"{TOOLS}/{slug}/favorite", headers=auth(outsider))).status_code == 200

    await _mutate_tool(offline_id, status="offline")
    # 私有：outsider **不是** owner，因此看不到
    await _mutate_tool(private_id, visibility="private")
    await _mutate_tool(deleted_id, deleted_at=utcnow(), deleted_by_id=seeded.users["admin"])

    listing = await client.get(FAVORITES, headers=auth(outsider))
    assert listing.status_code == 200, listing.text
    body = listing.json()
    returned = {item["slug"] for item in body["items"]}
    assert returned == {visible_slug}, (
        f"收藏列表泄漏了不可见工具：{returned - {visible_slug}}"
    )
    assert body["total"] == 1, "total 必须与列表同口径，否则分页会撒谎"
    assert visible_id  # 保持引用，避免被 lint 判为未使用


# ---------------------------------------------------------------------------
# B11 —— estimated_saving_minutes 边界
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("value", "expected_status"),
    [
        (0, 400),
        (1, 201),
        (1440, 201),
        (1441, 400),
        (-1, 400),
    ],
)
async def test_estimated_saving_minutes_boundaries_on_create(
    client, seeded, value: int, expected_status: int
) -> None:
    approver = await login(client, "approver")
    response = await client.post(
        "/api/v1/me/tools",
        json={
            "name": f"M8 估算边界 {value}",
            "summary": "s",
            "tool_type": "prompt",
            "estimated_saving_minutes": value,
        },
        headers=auth(approver),
    )
    assert response.status_code == expected_status, response.text
    if expected_status == 201:
        assert response.json()["estimated_saving_minutes"] == value
    else:
        assert response.json()["code"] == "VALIDATION_ERROR"


async def test_estimated_saving_minutes_null_is_not_zero(client, seeded) -> None:
    """`NULL` 表示未填写，**不是一个可以当成 0 的值** —— 不设默认 0。"""
    approver = await login(client, "approver")
    created = await client.post(
        "/api/v1/me/tools",
        json={"name": "M8 估算未填", "summary": "s", "tool_type": "prompt"},
        headers=auth(approver),
    )
    assert created.status_code == 201, created.text
    assert created.json()["estimated_saving_minutes"] is None

    tool_id = created.json()["id"]
    # 显式传 null 也是「未填写」，不是 0
    explicit_null = await client.post(
        "/api/v1/me/tools",
        json={
            "name": "M8 估算显式 null",
            "summary": "s",
            "tool_type": "prompt",
            "estimated_saving_minutes": None,
        },
        headers=auth(approver),
    )
    assert explicit_null.status_code == 201, explicit_null.text
    assert explicit_null.json()["estimated_saving_minutes"] is None
    assert tool_id


async def test_estimated_saving_minutes_update_semantics(client, seeded) -> None:
    """PATCH 局部更新：省略 = 不改；显式 null = 清空。"""
    approver = await login(client, "approver")
    created = await client.post(
        "/api/v1/me/tools",
        json={
            "name": "M8 估算更新",
            "summary": "s",
            "tool_type": "prompt",
            "estimated_saving_minutes": 60,
        },
        headers=auth(approver),
    )
    assert created.status_code == 201, created.text
    tool_id = created.json()["id"]

    # 省略该字段 → 不变
    untouched = await client.patch(
        f"/api/v1/me/tools/{tool_id}", json={"summary": "改简介"}, headers=auth(approver)
    )
    assert untouched.status_code == 200, untouched.text
    assert untouched.json()["estimated_saving_minutes"] == 60

    # 越界 → 400
    too_big = await client.patch(
        f"/api/v1/me/tools/{tool_id}",
        json={"estimated_saving_minutes": 1441},
        headers=auth(approver),
    )
    assert too_big.status_code == 400, too_big.text
    assert too_big.json()["code"] == "VALIDATION_ERROR"

    # 显式 null → 清空
    cleared = await client.patch(
        f"/api/v1/me/tools/{tool_id}",
        json={"estimated_saving_minutes": None},
        headers=auth(approver),
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["estimated_saving_minutes"] is None


# ---------------------------------------------------------------------------
# B9 —— 上传去重检测（服务端）
# ---------------------------------------------------------------------------
async def test_duplicate_upload_is_reported_but_does_not_block(client, seeded) -> None:
    approver = await login(client, "approver")
    same_bytes = zip_bytes({"same.txt": b"identical-content"})

    # 1) 第一个工具上传：没有任何其他工具持有该哈希 → 不命中
    tool_a = await create_tool(client, approver, name="M8 去重甲")
    first = await upload_version(
        client, approver, tool_a["id"], version="1.0.0", file_bytes=same_bytes, file_name="s.zip"
    )
    assert first.status_code == 201, first.text
    assert first.json()["duplicate_of"] is None

    # 2) **同一工具**的新版本：正常迭代，不算重复
    second = await upload_version(
        client, approver, tool_a["id"], version="2.0.0", file_bytes=same_bytes, file_name="s.zip"
    )
    assert second.status_code == 201, second.text
    assert second.json()["duplicate_of"] is None, "同一工具的新版本不该被当成重复"

    # 3) **另一个工具**上传同样内容 → 命中甲工具，且上传仍然成功（不阻断）
    tool_b = await create_tool(client, approver, name="M8 去重乙")
    third = await upload_version(
        client, approver, tool_b["id"], version="1.0.0", file_bytes=same_bytes, file_name="s.zip"
    )
    assert third.status_code == 201, third.text
    match = third.json()["duplicate_of"]
    assert match is not None
    assert match["tool_id"] == tool_a["id"]
    # 甲工具有两个同哈希版本；命中排序是「当前版本优先 → created_at 倒序」，
    # 两个都不是当前版本，因此取**最新**的 2.0.0。
    assert match["version"] == "2.0.0"
    assert match["file_sha256"] == first.json()["file_sha256"] == second.json()["file_sha256"]
    assert set(match) == {
        "tool_id",
        "slug",
        "name",
        "version_id",
        "version",
        "file_sha256",
        "is_current",
        "uploaded_at",
    }


async def test_duplicate_check_skipped_when_no_file(client, seeded) -> None:
    """prompt / webapp 类型没有文件，`duplicate_of` 必须是 `null`（不报错）。"""
    approver = await login(client, "approver")
    tool = await create_tool(client, approver, name="M8 去重无文件", tool_type="prompt")
    response = await upload_version(
        client, approver, tool["id"], version="1.0.0", prompt_content="提示词正文"
    )
    assert response.status_code == 201, response.text
    assert response.json()["duplicate_of"] is None


# ---------------------------------------------------------------------------
# B10 —— /admin/stats/insights
# ---------------------------------------------------------------------------
async def test_insights_requires_admin_role(client, seeded) -> None:
    anonymous = await client.get(INSIGHTS)
    assert anonymous.status_code == 401, anonymous.text

    approver = await login(client, "approver")
    forbidden = await client.get(INSIGHTS, headers=auth(approver))
    assert forbidden.status_code == 403, forbidden.text

    admin = await login(client, "admin")
    allowed = await client.get(INSIGHTS, headers=auth(admin))
    assert allowed.status_code == 200, allowed.text


async def test_insights_shape_and_daily_series_is_exactly_30_days(client, seeded) -> None:
    admin = await login(client, "admin")
    approver = await login(client, "approver")
    tool = await create_tool(client, approver, name="M8 序列工具", tool_type="prompt")
    # 造 1 条落在一个明确的历史日，验证按天分桶
    when = utcnow() - timedelta(days=10)
    await _add_download(tool["id"], user_id=seeded.users["outsider"], when=when)

    response = await client.get(INSIGHTS, headers=auth(admin))
    assert response.status_code == 200, response.text
    body = response.json()

    assert set(body) == {
        "downloads_last_30_days",
        "downloads_daily",
        "active_contributors_30d",
        "tools_by_category",
        "savings",
    }
    assert set(body["savings"]) == {
        "total_minutes",
        "covered_tool_count",
        "total_tool_count",
        "basis",
    }
    assert body["savings"]["basis"] == "author_estimate"
    for row in body["tools_by_category"]:
        assert set(row) == {"category_id", "name", "tool_count", "download_count"}
        assert isinstance(row["name"], str)

    daily = body["downloads_daily"]
    assert len(daily) == 30, "downloads_daily 必须恰好 30 条（契约 §23.6）"
    days = [datetime.fromisoformat(row["date"]).date() for row in daily]
    today = utcnow().date()
    assert days[-1] == today, "序列末位应是今天（UTC）"
    assert days[0] == today - timedelta(days=29)
    assert days == [today - timedelta(days=29 - index) for index in range(30)], "日期必须连续"
    assert body["downloads_last_30_days"] == sum(row["downloads"] for row in daily)

    # 独立实现（Python 侧分桶）交叉核对 SQL 分桶与「缺口补 0」
    since = datetime.combine(days[0], time.min, tzinfo=UTC)
    async with SessionLocal() as session:
        stamps = (
            await session.execute(
                select(DownloadLog.created_at).where(DownloadLog.created_at >= since)
            )
        ).scalars().all()
    expected: dict[str, int] = defaultdict(int)
    for stamp in stamps:
        day = ensure_utc(stamp).date()
        if days[0] <= day <= today:
            expected[day.isoformat()] += 1
    assert [(row["date"], row["downloads"]) for row in daily] == [
        (day.isoformat(), expected.get(day.isoformat(), 0)) for day in days
    ]
    # 至少有缺口被补成 0（测试库里 30 天内不可能每天都有下载）
    assert any(row["downloads"] == 0 for row in daily), "缺口没有被补 0"

    # 未分类工具必须出现，且 `name` 是字符串（不是 null）
    uncategorized = [row for row in body["tools_by_category"] if row["category_id"] is None]
    assert uncategorized, "刚创建的未分类工具应当出现在 tools_by_category 里"
    assert all(row["name"] == "未分类" for row in uncategorized)

    # 30 天内贡献者数：与独立实现的去重计数一致
    async with SessionLocal() as session:
        contributors = (
            await session.execute(
                select(ToolVersion.uploaded_by_id)
                .where(ToolVersion.created_at >= since)
                .distinct()
            )
        ).scalars().all()
    assert body["active_contributors_30d"] == len(
        [value for value in contributors if value is not None]
    )


async def test_portal_list_attachment_query_is_constant_not_per_favorite(
    client, seeded
) -> None:
    """契约 §23.5：`is_favorited` / `is_liked` 用**一条附加查询**。

    实测两点，证明它不是 JOIN、也不随用户收藏总数增长：

      1. 有 0 个收藏与有 30 个收藏时，认证用户的列表请求 SQL 条数**相同**；
      2. 同一收藏数下，`page_size=1` 与 `page_size=24` 的 SQL 条数相同。

    第 1 点正是任务书 B8「分页列表中没有的工具不应出现在附加查询的结果里 ——
    别把用户全部收藏都捞出来」的可验证形式：若把用户全部收藏都查出来，
    收藏数变化会体现在往返次数或行数上；这里条数恒定。
    """
    outsider = await login(client, "outsider")

    def _sql_count(response) -> int:
        return int(response.headers["X-SQL-Count"])

    await _clear_engagement(seeded.users["outsider"])
    # 预热：第一次请求会填充 facet 短时缓存 / 图片签名 TTL 缓存，
    # 那会让「第一次」与「之后」的 SQL 条数不同 —— 先跑一次把它们排除掉。
    warm = await client.get(TOOLS, params={"page_size": 24}, headers=auth(outsider))
    assert warm.status_code == 200, warm.text
    without = await client.get(TOOLS, params={"page_size": 24}, headers=auth(outsider))
    assert without.status_code == 200, without.text
    baseline = _sql_count(without)

    async with SessionLocal() as session:
        tool_ids = (
            await session.execute(select(Tool.id).where(Tool.deleted_at.is_(None)).limit(30))
        ).scalars().all()
        for tool_id in tool_ids:
            session.add(
                ToolFavorite(
                    user_id=seeded.users["outsider"],
                    tool_id=int(tool_id),
                    created_at=utcnow(),
                )
            )
        await session.commit()

    with_favorites = await client.get(TOOLS, params={"page_size": 24}, headers=auth(outsider))
    assert with_favorites.status_code == 200, with_favorites.text
    assert _sql_count(with_favorites) == baseline, "附加查询随收藏总数增长了"

    small_page = await client.get(TOOLS, params={"page_size": 1}, headers=auth(outsider))
    assert _sql_count(small_page) == baseline, "附加查询随列表页大小变化了"

    # 附带确认：收藏确实生效（is_favorited 为 true），不是「没查所以条数一样」
    assert any(item["is_favorited"] for item in with_favorites.json()["items"])


async def test_savings_formula_matches_hand_computed_expectation(client, seeded) -> None:
    """**手算期望值**再断言，不拿实现的输出反推期望（任务书 B10）。

    构造（全部在同一个事务窗口内、对同一份 insights 取前后差值）：

      - T1：estimated_saving_minutes = 30，下载用户 = {u1, u2, u3} ∪ {NULL}
            → COUNT(DISTINCT user_id) = **3**（NULL 不计）→ 30 × 3 = **90**
      - T2：estimated_saving_minutes = 45，下载用户 = {u1, u1, u4}
            → COUNT(DISTINCT user_id) = **2**（同一人重复下载只算一次）→ 45 × 2 = **90**
      - T3：estimated_saving_minutes = NULL，下载用户 = {u5, u6}
            → 未填 → 贡献 **0**（且不计入 covered）

    手算 total_minutes = 90 + 90 = **180**；covered_tool_count += 2；
    total_tool_count += 3；downloads_last_30_days += **9**（4 + 3 + 2 条明细）。
    """
    admin = await login(client, "admin")
    approver = await login(client, "approver")

    before = (await client.get(INSIGHTS, headers=auth(admin))).json()

    users = seeded.users

    async def _new_tool(name: str, saving: int | None) -> int:
        payload: dict = {"name": name, "summary": "s", "tool_type": "prompt"}
        if saving is not None:
            payload["estimated_saving_minutes"] = saving
        response = await client.post("/api/v1/me/tools", json=payload, headers=auth(approver))
        assert response.status_code == 201, response.text
        return int(response.json()["id"])

    t1 = await _new_tool("M8 节省甲", 30)
    t2 = await _new_tool("M8 节省乙", 45)
    t3 = await _new_tool("M8 节省无估算", None)

    for user_key in ("admin", "newbie", "approver"):
        await _add_download(t1, user_id=users[user_key])
    await _add_download(t1, user_id=None)  # 匿名下载：不计入去重人数

    for user_key in ("admin", "admin", "outsider"):
        await _add_download(t2, user_id=users[user_key])

    for user_key in ("viewer", "lockme"):
        await _add_download(t3, user_id=users[user_key])

    after = (await client.get(INSIGHTS, headers=auth(admin))).json()

    assert after["savings"]["total_minutes"] - before["savings"]["total_minutes"] == 180
    assert after["savings"]["covered_tool_count"] - before["savings"]["covered_tool_count"] == 2
    assert after["savings"]["total_tool_count"] - before["savings"]["total_tool_count"] == 3
    assert after["downloads_last_30_days"] - before["downloads_last_30_days"] == 9
    assert after["savings"]["basis"] == "author_estimate"


async def test_insights_does_not_affect_overview_query_cost(client, seeded) -> None:
    """确认重聚合不会拖慢 `/admin/overview`（任务书 B10 第 5 点）。

    insights 是**独立端点**：它不在 `/admin/overview` 的任何代码路径上。
    这里用 M7 的 `X-SQL-Count` 诊断头实测两件事：

      1. 调 insights 前后，`/admin/overview` 的 SQL 条数**完全一致**；
      2. insights 自身的 SQL 条数是**有界的小常数**（不是按工具数增长）。
    """
    admin = await login(client, "admin")

    def _sql_count(response) -> int:
        raw = response.headers.get("X-SQL-Count")
        assert raw is not None, "X-SQL-Count 未启用（应仅在诊断开关下出现）"
        return int(raw)

    overview_before = await client.get("/api/v1/admin/overview", headers=auth(admin))
    assert overview_before.status_code == 200, overview_before.text
    baseline = _sql_count(overview_before)

    insights = await client.get(INSIGHTS, headers=auth(admin))
    assert insights.status_code == 200, insights.text
    insights_queries = _sql_count(insights)

    overview_after = await client.get("/api/v1/admin/overview", headers=auth(admin))
    assert _sql_count(overview_after) == baseline, (
        "insights 改变了 /admin/overview 的 SQL 条数 —— 说明两者被耦合了"
    )
    assert insights_queries <= 12, (
        f"insights 的 SQL 条数应是有界小常数，实际 {insights_queries}"
    )
