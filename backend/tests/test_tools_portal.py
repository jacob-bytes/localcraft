"""门户列表测试：分页边界、sort 白名单、facets、可见性下推、搜索。"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.setting import SystemSetting
from tests.conftest import PASSWORDS, auth, login

TOOLS = "/api/v1/tools"
CATEGORIES = "/api/v1/categories"
TAGS = "/api/v1/tags"

#: 播种数据全部落在 cat-a 下；M2 的测试会往同一个库里加不带分类的工具，
#: 因此所有**精确计数**断言都必须收敛到 cat-a，否则会互相污染。
SEEDED_CATEGORY = "cat-a"


def seeded_params(**extra) -> dict:
    """带播种分类过滤的查询参数。"""
    return {"category": SEEDED_CATEGORY, **extra}


#: 各身份在门户里能看到的工具数（由 conftest 的播种数据决定）
EXPECTED_VISIBLE = {
    "admin": 6,  # 超管 + allow_admin_view_private → 全部 approved 且未删除
    "outsider": 4,  # public×2 + 自己的 private + user ACL 命中的 restricted
    "approver": 3,  # public×2 + 组 ACL 命中的 restricted
    "viewer": 2,  # 只有 public
}


async def _set_setting(key: str, value: object) -> None:
    async with SessionLocal() as session:
        row = (
            await session.execute(select(SystemSetting).where(SystemSetting.key == key))
        ).scalar_one()
        row.value = value
        await session.commit()


# ---------------------------------------------------------------------------
# 未登录用户：由 portal.allow_anonymous_view 决定
# ---------------------------------------------------------------------------
async def test_anonymous_tools_allowed_by_default(client, seeded) -> None:
    """**新默认**：未登录即可浏览公开工具（FR-ACL-06 默认 true，迁移 0005）。

    默认值是 1.0.0 之后改的：门户主页不登录即可访问，便于在内网里直接分享链接。
    """
    response = await client.get(TOOLS, params=seeded_params())
    assert response.status_code == 200, response.text
    body = response.json()
    # 匿名只能看到 public
    assert body["total"] == EXPECTED_VISIBLE["viewer"]
    assert all(item["visibility"] == "public" for item in body["items"])
    # 匿名不能下载
    assert all(item["can_download"] is False for item in body["items"])


async def test_anonymous_tools_requires_login_when_setting_disabled(client) -> None:
    """契约 §6：默认值改掉之后，**「关」这条路径必须依然有效**。

    这条是本轮改动里最需要守的东西 —— 守卫或开关写错的话，"关"会静默失效，
    等于把所有部署都变成允许匿名访问。
    """
    await _set_setting("portal.allow_anonymous_view", False)
    try:
        response = await client.get(TOOLS)
        assert response.status_code == 401
        assert response.json()["code"] == "UNAUTHENTICATED"
    finally:
        await _set_setting("portal.allow_anonymous_view", True)


# ---------------------------------------------------------------------------
# 分页
# ---------------------------------------------------------------------------
async def test_page_zero_is_400_with_fields(client, seeded) -> None:
    token = await login(client, "admin")
    response = await client.get(TOOLS, params={"page": 0}, headers=auth(token))
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    # 契约 §4.3：details.fields 必须是数组
    assert isinstance(body["details"]["fields"], list)
    assert body["details"]["fields"][0]["field"] == "page"


async def test_page_size_over_max_is_400(client, seeded) -> None:
    token = await login(client, "admin")
    response = await client.get(TOOLS, params={"page_size": 999}, headers=auth(token))
    assert response.status_code == 400
    assert response.json()["details"]["fields"][0]["field"] == "page_size"


async def test_page_size_bounds_are_inclusive(client, seeded) -> None:
    token = await login(client, "admin")
    for size in (1, 200):
        response = await client.get(TOOLS, params={"page_size": size}, headers=auth(token))
        assert response.status_code == 200, f"page_size={size}: {response.text}"
        assert response.json()["page_size"] == size


async def test_page_beyond_last_returns_empty_items_not_404(client, seeded) -> None:
    """docs/03 §1.5：超过最大页数返回空 items 而非 404。"""
    token = await login(client, "admin")
    response = await client.get(
        TOOLS, params=seeded_params(page=9999), headers=auth(token)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"] == []
    assert body["total"] == EXPECTED_VISIBLE["admin"]
    assert body["pages"] >= 1


async def test_pagination_walks_the_full_set(client, seeded) -> None:
    token = await login(client, "admin")
    seen: list[str] = []
    page = 1
    while True:
        response = await client.get(
            TOOLS, params=seeded_params(page=page, page_size=2), headers=auth(token)
        )
        body = response.json()
        if not body["items"]:
            break
        seen.extend(item["slug"] for item in body["items"])
        page += 1
        assert page < 20, "分页没有收敛"
    assert len(seen) == EXPECTED_VISIBLE["admin"]
    assert len(set(seen)) == len(seen), "分页出现重复条目"


# ---------------------------------------------------------------------------
# sort 白名单
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "bad_sort",
    [
        "; DROP TABLE tools",
        "id",
        "download_count desc",
        "-name",
        "1",
        "hot,new",
    ],
)
async def test_invalid_sort_is_400_not_500(client, seeded, bad_sort: str) -> None:
    """docs/03 §1.6：非法 sort 返回 400 INVALID_SORT，绝不拼进 SQL。"""
    token = await login(client, "admin")
    response = await client.get(TOOLS, params={"sort": bad_sort}, headers=auth(token))
    assert response.status_code == 400, f"sort={bad_sort!r}: {response.text}"
    assert response.json()["code"] == "INVALID_SORT"


@pytest.mark.parametrize("good_sort", ["hot", "new", "name", "-updated_at"])
async def test_valid_sort_values_accepted(client, seeded, good_sort: str) -> None:
    token = await login(client, "admin")
    response = await client.get(TOOLS, params={"sort": good_sort}, headers=auth(token))
    assert response.status_code == 200, response.text


async def test_default_sort_comes_from_settings(client, seeded) -> None:
    token = await login(client, "admin")
    default_response = await client.get(TOOLS, headers=auth(token))
    hot_response = await client.get(TOOLS, params={"sort": "hot"}, headers=auth(token))
    assert [i["slug"] for i in default_response.json()["items"]] == [
        i["slug"] for i in hot_response.json()["items"]
    ]


async def test_tools_table_survives_sql_injection_attempt(client, seeded) -> None:
    """sort 只做白名单查表，非法值不可能进入 SQL。"""
    token = await login(client, "admin")
    await client.get(TOOLS, params={"sort": "name; DROP TABLE tools"}, headers=auth(token))
    ok = await client.get(TOOLS, params=seeded_params(), headers=auth(token))
    assert ok.status_code == 200
    assert ok.json()["total"] == EXPECTED_VISIBLE["admin"]


# ---------------------------------------------------------------------------
# facets
# ---------------------------------------------------------------------------
async def test_facets_only_on_first_page(client, seeded) -> None:
    token = await login(client, "admin")

    first = await client.get(TOOLS, params={"page": 1, "page_size": 2}, headers=auth(token))
    assert first.json()["facets"] is not None

    second = await client.get(TOOLS, params={"page": 2, "page_size": 2}, headers=auth(token))
    assert "facets" not in second.json() or second.json()["facets"] is None


async def test_facets_are_visibility_filtered(client, seeded) -> None:
    """FR-TAX-07：facets 计数必须与当前用户可见性一致。"""
    for username, expected in EXPECTED_VISIBLE.items():
        token = await login(client, username)
        response = await client.get(TOOLS, params=seeded_params(), headers=auth(token))
        facets = response.json()["facets"]
        category_counts = {c["slug"]: c["count"] for c in facets["categories"]}
        assert category_counts.get("cat-a") == expected, username
        # 所有分类的计数之和 == total
        assert sum(category_counts.values()) == response.json()["total"], username
        # 类型 facet 是**全部门户工具**的分布（不受分类筛选影响），
        # 所以这里只能断言「不少于该身份在 cat-a 下看到的数量」
        assert sum(t["count"] for t in facets["types"]) >= expected, username


async def test_categories_endpoint_matches_facets(client, seeded) -> None:
    """`GET /categories` 的 tool_count 必须与 `facets.categories[].count` 一致。"""
    for username in EXPECTED_VISIBLE:
        token = await login(client, username)
        categories = (await client.get(CATEGORIES, headers=auth(token))).json()
        facets = (
            await client.get(TOOLS, params=seeded_params(), headers=auth(token))
        ).json()["facets"]["categories"]
        assert {c["slug"]: c["tool_count"] for c in categories} == {
            c["slug"]: c["count"] for c in facets
        }, username


async def test_categories_exclude_inactive(client, seeded) -> None:
    token = await login(client, "admin")
    categories = (await client.get(CATEGORIES, headers=auth(token))).json()
    slugs = {c["slug"] for c in categories}
    assert "cat-a" in slugs
    assert "cat-b" not in slugs, "停用分类不应出现在门户分类列表里"


# ---------------------------------------------------------------------------
# 可见性（下推到 SQL）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("username", ["admin", "outsider", "approver", "viewer"])
async def test_visibility_matrix(client, seeded, username: str) -> None:
    token = await login(client, username)
    response = await client.get(
        TOOLS, params=seeded_params(page_size=200), headers=auth(token)
    )
    slugs = {item["slug"] for item in response.json()["items"]}

    assert "deleted-public" not in slugs, "软删除的工具不得出现"
    assert "public-draft" not in slugs, "草稿不得出现在门户"
    assert "public-pending" not in slugs, "待审不得出现在门户"
    assert "public-approved" in slugs
    assert "public-approved-2" in slugs

    if username in ("admin", "outsider"):
        # admin：超管 + allow_admin_view_private（D32）
        # outsider：private-owned 的 owner，owner 始终可见自己的工具
        assert "private-owned" in slugs
    else:
        assert "private-owned" not in slugs

    if username in ("outsider",):
        assert "restricted-user" in slugs, "用户级 ACL 命中应可见"
        assert "restricted-group" not in slugs, "非组成员不应可见"
        assert "restricted-none" not in slugs

    if username == "approver":
        assert "restricted-group" in slugs, "组成员应可见"
        assert "restricted-user" not in slugs

    if username == "viewer":
        assert slugs == {"public-approved", "public-approved-2"}


async def test_superadmin_private_visibility_respects_setting(client, seeded) -> None:
    """D32：`portal.allow_admin_view_private=false` 时超管也看不到他人 private。"""
    await _set_setting("portal.allow_admin_view_private", False)
    try:
        token = await login(client, "admin")
        response = await client.get(
            TOOLS, params=seeded_params(page_size=200), headers=auth(token)
        )
        slugs = {item["slug"] for item in response.json()["items"]}
        assert "private-owned" not in slugs
        assert response.json()["total"] == EXPECTED_VISIBLE["admin"] - 1
    finally:
        await _set_setting("portal.allow_admin_view_private", True)


async def test_viewer_sees_but_cannot_download(client, seeded) -> None:
    """docs/01 §3.2：viewer 可浏览 public，但不能下载。"""
    token = await login(client, "viewer")
    body = (await client.get(TOOLS, params=seeded_params(), headers=auth(token))).json()
    assert body["total"] > 0
    assert all(item["can_download"] is False for item in body["items"])

    admin_token = await login(client, "admin")
    admin_body = (
        await client.get(TOOLS, params=seeded_params(), headers=auth(admin_token))
    ).json()
    assert all(item["can_download"] is True for item in admin_body["items"])


# ---------------------------------------------------------------------------
# 筛选
# ---------------------------------------------------------------------------
async def test_filter_by_category(client, seeded) -> None:
    token = await login(client, "admin")
    response = await client.get(TOOLS, params={"category": "cat-a"}, headers=auth(token))
    assert response.json()["total"] == EXPECTED_VISIBLE["admin"]

    empty = await client.get(TOOLS, params={"category": "no-such"}, headers=auth(token))
    assert empty.status_code == 200
    assert empty.json()["total"] == 0


async def test_filter_by_type_accepts_repeats(client, seeded) -> None:
    token = await login(client, "admin")
    response = await client.get(
        TOOLS,
        params=[("category", SEEDED_CATEGORY), ("type", "file"), ("type", "prompt")],
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == EXPECTED_VISIBLE["admin"]


async def test_filter_by_invalid_type_is_400(client, seeded) -> None:
    token = await login(client, "admin")
    response = await client.get(TOOLS, params={"type": "not-a-type"}, headers=auth(token))
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_filter_by_tag_is_normalized(client, seeded) -> None:
    token = await login(client, "admin")
    # 播种时 tag 的 display_name 是 "ALPHA"，归一化名是 "alpha"
    for value in ("alpha", "ALPHA", "  Alpha  "):
        response = await client.get(TOOLS, params={"tag": value}, headers=auth(token))
        assert response.status_code == 200, response.text
        slugs = {item["slug"] for item in response.json()["items"]}
        assert slugs == {"public-approved"}, f"tag={value!r} → {slugs}"


async def test_filter_by_owner(client, seeded) -> None:
    token = await login(client, "admin")
    response = await client.get(TOOLS, params={"owner": "admin"}, headers=auth(token))
    assert response.status_code == 200
    assert response.json()["total"] > 0


# ---------------------------------------------------------------------------
# 搜索
# ---------------------------------------------------------------------------
async def test_search_matches_name(client, seeded) -> None:
    token = await login(client, "admin")
    response = await client.get(TOOLS, params={"q": "restricted-user"}, headers=auth(token))
    slugs = {item["slug"] for item in response.json()["items"]}
    assert "restricted-user" in slugs


async def test_search_matches_tag(client, seeded) -> None:
    token = await login(client, "admin")
    response = await client.get(TOOLS, params={"q": "alpha"}, headers=auth(token))
    assert response.status_code == 200


async def test_search_no_match_returns_empty(client, seeded) -> None:
    token = await login(client, "admin")
    response = await client.get(TOOLS, params={"q": "zzz-not-present-zzz"}, headers=auth(token))
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total"] == 0


# ---------------------------------------------------------------------------
# 响应形状
# ---------------------------------------------------------------------------
async def test_item_shape_matches_contract(client, seeded) -> None:
    """docs/03 §3.3 的响应示例字段必须逐一存在（可空字段显式返回 null）。"""
    token = await login(client, "admin")
    response = await client.get(TOOLS, params={"q": "public-approved-2"}, headers=auth(token))
    item = response.json()["items"][0]

    expected = {
        "id",
        "slug",
        "name",
        "summary",
        "tool_type",
        "visibility",
        "category",
        "tags",
        "cover_url",
        "owner",
        "current_version",
        "file_size",
        "download_count",
        "view_count",
        "has_pending_version",
        "can_download",
        "published_at",
        "updated_at",
    }
    assert set(item) == expected
    assert set(item["category"]) == {"id", "slug", "name", "icon"}
    assert set(item["owner"]) == {"id", "username", "display_name"}
    assert item["current_version"] == "1.0.0"
    assert item["tags"] == []


async def test_tool_list_tags_use_display_name(client, seeded) -> None:
    """卡片上的标签用 display_name（保留原始大小写），筛选入参仍按归一化处理。"""
    token = await login(client, "admin")
    response = await client.get(TOOLS, params={"tag": "alpha"}, headers=auth(token))
    item = response.json()["items"][0]
    assert item["tags"] == ["ALPHA"]


async def test_cover_url_present_only_when_cover_exists(client, seeded) -> None:
    token = await login(client, "admin")
    response = await client.get(TOOLS, params={"page_size": 200}, headers=auth(token))
    by_slug = {item["slug"]: item for item in response.json()["items"]}

    with_cover = by_slug["public-approved"]
    # M5 起封面 URL 带能力签名（契约 §14.3）：形如 <path>?variant=thumb&sig=<b64url>.<exp>
    url = with_cover["cover_url"]
    assert url.startswith("/api/v1/images/1?variant=thumb&sig="), url
    assert by_slug["public-approved-2"]["cover_url"] is None


async def test_timestamps_are_iso8601_utc_with_z(client, seeded) -> None:
    """契约 §5：时间必须是 ISO 8601 UTC 带 Z。"""
    token = await login(client, "admin")
    response = await client.get(TOOLS, params={"page_size": 200}, headers=auth(token))
    for item in response.json()["items"]:
        if item["published_at"] is not None:
            assert item["published_at"].endswith("Z"), item["published_at"]
        assert item["updated_at"].endswith("Z"), item["updated_at"]


async def test_pages_field_is_ceiling(client, seeded) -> None:
    token = await login(client, "admin")
    body = (
        await client.get(TOOLS, params=seeded_params(page_size=4), headers=auth(token))
    ).json()
    assert body["pages"] == 2  # ceil(6 / 4)


# ---------------------------------------------------------------------------
# 标签接口
# ---------------------------------------------------------------------------
async def test_tags_prefix_search(client, seeded) -> None:
    token = await login(client, "admin")
    response = await client.get(TAGS, params={"q": "al"}, headers=auth(token))
    assert response.status_code == 200
    names = {t["name"] for t in response.json()}
    assert "alpha" in names
    assert "beta" not in names, "前缀搜索不应命中非前缀标签"


async def test_tags_returns_display_name(client, seeded) -> None:
    token = await login(client, "admin")
    response = await client.get(TAGS, headers=auth(token))
    by_name = {t["name"]: t for t in response.json()}
    assert by_name["alpha"]["display_name"] == "ALPHA"


async def test_tags_require_login_when_anonymous_disabled(client) -> None:
    """`/tags` 与 `/tools` 走同一个 `portal_access`：默认放行，关掉后 401。"""
    allowed = await client.get(TAGS)
    assert allowed.status_code == 200, allowed.text

    await _set_setting("portal.allow_anonymous_view", False)
    try:
        denied = await client.get(TAGS)
        assert denied.status_code == 401
        assert denied.json()["code"] == "UNAUTHENTICATED"
    finally:
        await _set_setting("portal.allow_anonymous_view", True)


async def test_login_helper_password_constant_is_used(client) -> None:
    """防止 conftest 的密码表被误改（其它测试依赖它）。"""
    assert PASSWORDS["admin"] == "Admin@12345"
