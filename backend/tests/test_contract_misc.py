"""权限矩阵、错误码契约、搜索分词、元信息与探针、SPA fallback。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import select

from app.api.public import NON_SPA_PREFIXES
from app.core.errors import ERROR_REGISTRY, DomainError, code_for_status, message_for_code
from app.core.permissions import (
    ALL_PERMISSIONS,
    PERM_DOWNLOAD,
    ROLE_PERMISSIONS,
    permissions_for_roles,
)
from app.core.security import (
    hash_password,
    normalize_username,
    password_strength_errors,
    verify_password,
)
from app.db.session import SessionLocal
from app.models.enums import ApiScope, RoleCode
from app.models.setting import SystemSetting
from app.search import tokenize, tokenize_query

BACKEND_DIR = Path(__file__).resolve().parents[1]
DOCS_DIR = BACKEND_DIR.parent / "docs"


# ===========================================================================
# 权限矩阵（docs/01 §3.2）
# ===========================================================================
def test_role_permission_matrix_matches_srs() -> None:
    viewer = ROLE_PERMISSIONS[RoleCode.VIEWER.value]
    user = ROLE_PERMISSIONS[RoleCode.USER.value]
    approver = ROLE_PERMISSIONS[RoleCode.APPROVER.value]
    superadmin = ROLE_PERMISSIONS[RoleCode.SUPERADMIN.value]

    # 逐级包含
    assert viewer <= user <= approver <= superadmin

    # viewer：只能浏览，不能下载、不能上传（docs/01 §3.2）
    assert viewer == {ApiScope.TOOLS_READ.value}
    assert PERM_DOWNLOAD not in viewer

    # user：加下载与自己的工具管理
    assert PERM_DOWNLOAD in user
    assert ApiScope.TOOLS_WRITE.value in user
    assert ApiScope.APPROVALS_WRITE.value not in user

    # approver：加审批与分类标签；**明确不给**用户管理、系统设置、API Token
    assert ApiScope.APPROVALS_WRITE.value in approver
    assert ApiScope.TAXONOMY_WRITE.value in approver
    assert ApiScope.USERS_WRITE.value not in approver
    assert ApiScope.SETTINGS_WRITE.value not in approver
    assert ApiScope.GROUPS_WRITE.value not in approver

    # superadmin：全部
    assert superadmin == ALL_PERMISSIONS
    assert ApiScope.ADMIN_ALL.value in superadmin


def test_permissions_union_for_multiple_roles() -> None:
    """docs/01 §3.1：一个用户可拥有多个角色，权限取并集。"""
    combined = permissions_for_roles({"viewer", "approver"})
    assert combined == ROLE_PERMISSIONS["approver"]


def test_unknown_role_grants_nothing() -> None:
    assert permissions_for_roles({"not-a-role"}) == frozenset()


# ===========================================================================
# 错误码契约（docs/03 §4）—— 直接解析文档，防止自行发明新码
# ===========================================================================
def _parse_documented_error_codes() -> dict[str, int]:
    doc = DOCS_DIR / "03-API接口清单.md"
    assert doc.is_file(), f"缺少契约文档: {doc}"
    text = doc.read_text(encoding="utf-8")

    # 只取第 4 章「错误码总表」的表体
    section = text.split("## 4. 错误码总表", 1)[1].split("## 5.", 1)[0]
    codes: dict[str, int] = {}
    pattern = re.compile(r"^\|\s*`([A-Z_]+)`\s*\|\s*(\d{3})\s*\|", re.MULTILINE)
    for match in pattern.finditer(section):
        codes[match.group(1)] = int(match.group(2))
    return codes


def _codes_mentioned_in_section_1_7() -> set[str]:
    """docs/03 §1.7 的「HTTP 语义 典型 code」表也列了若干错误码。

    该表与 §4 的总表**不完全一致**：`RATE_LIMITED` 只出现在 §1.7，
    §4 的总表里漏了它（文档问题，已在 checkpoint 报告里列出）。
    因此判定「是否发明新码」时必须两张表一起看。
    """
    doc = DOCS_DIR / "03-API接口清单.md"
    text = doc.read_text(encoding="utf-8")
    section = text.split("### 1.7 错误响应", 1)[1].split("### 1.8", 1)[0]
    return set(re.findall(r"`([A-Z][A-Z_]{3,})`", section))


def test_error_registry_matches_docs_table() -> None:
    documented = _parse_documented_error_codes()
    assert len(documented) >= 35, f"解析到的错误码偏少（{len(documented)}），文档解析可能失效"

    for code, status in documented.items():
        assert code in ERROR_REGISTRY, f"文档里的错误码 {code} 未在实现中注册"
        assert ERROR_REGISTRY[code][0] == status, (
            f"错误码 {code} 的 HTTP 状态不一致：文档 {status}，实现 {ERROR_REGISTRY[code][0]}"
        )

    also_documented = _codes_mentioned_in_section_1_7()
    invented = set(ERROR_REGISTRY) - set(documented) - also_documented
    assert not invented, f"实现里出现了文档未定义的错误码: {sorted(invented)}"


def test_rate_limited_is_documented_only_in_section_1_7() -> None:
    """固化上面那条文档不一致，避免它被无声地「修好」或忘记。"""
    assert "RATE_LIMITED" in _codes_mentioned_in_section_1_7()
    assert "RATE_LIMITED" not in _parse_documented_error_codes()


def test_every_domain_error_subclass_is_registered() -> None:
    """每个 DomainError 子类的 code/status 都要在总表里。"""
    stack = [DomainError]
    seen: list[type[DomainError]] = []
    while stack:
        cls = stack.pop()
        stack.extend(cls.__subclasses__())
        if cls is not DomainError:
            seen.append(cls)

    assert len(seen) >= 30, f"只找到 {len(seen)} 个异常子类"
    for cls in seen:
        assert cls.code in ERROR_REGISTRY, f"{cls.__name__} 的 {cls.code} 未注册"
        assert cls.http_status == ERROR_REGISTRY[cls.code][0], cls.__name__


def test_status_code_mapping_only_uses_documented_codes() -> None:
    """框架抛出的 HTTPException 也只能映射到文档里的码。"""
    for status in (400, 401, 403, 404, 405, 409, 413, 415, 422, 423, 429, 500, 503, 507, 418, 599):
        assert code_for_status(status) in ERROR_REGISTRY
    assert code_for_status(404) == "NOT_FOUND"
    assert code_for_status(599) == "INTERNAL_ERROR"
    assert code_for_status(418) == "NOT_FOUND"


def test_documented_messages_are_chinese() -> None:
    assert message_for_code("NOT_FOUND") == "资源不存在"
    assert "内部" in message_for_code("INTERNAL_ERROR")


# ===========================================================================
# 密码与归一化
# ===========================================================================
@pytest.mark.parametrize(
    "password",
    ["Str0ng!Passw0rd", "Abcdefg123!", "aB3$xYz123", "长密码Abc123456!"],
)
def test_password_strength_accepts_valid(password: str) -> None:
    assert password_strength_errors(password, "someone") == []


@pytest.mark.parametrize(
    ("password", "reason"),
    [
        ("Sh0rt!a", "长度"),
        ("alllowercase1", "类别"),
        ("ALLUPPERCASE1", "类别"),
        ("OnlyUpperAndLower", "类别"),
    ],
)
def test_password_strength_rejects_weak(password: str, reason: str) -> None:
    errors = password_strength_errors(password, "someone")
    assert errors, f"{password!r} 应被拒绝（{reason}）"


def test_password_must_differ_from_username() -> None:
    """FR-AUTH-04：密码不得与用户名相同。"""
    # 完全相同
    errors = password_strength_errors("Admin@12345", "Admin@12345")
    assert any("用户名" in e for e in errors)
    # 仅大小写/空白不同也算相同（用户名大小写不敏感）
    errors = password_strength_errors("admin", "  ADMIN  ")
    assert any("用户名" in e for e in errors)
    # 只是「包含」用户名不算相同 —— FR-AUTH-04 只禁止相同
    assert password_strength_errors("Admin@12345", "admin") == []


def test_password_hash_roundtrip() -> None:
    hashed = hash_password("Str0ng!Passw0rd")
    assert hashed.startswith("$argon2id$")
    assert verify_password("Str0ng!Passw0rd", hashed)
    assert not verify_password("wrong", hashed)
    assert not verify_password("Str0ng!Passw0rd", "not-a-hash")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  ADMIN ", "admin"),
        ("ZhangSan", "zhangsan"),
        ("ｆｕｌｌｗｉｄｔｈ", "fullwidth"),  # 全角转半角
    ],
)
def test_username_normalization(raw: str, expected: str) -> None:
    assert normalize_username(raw) == expected


# ===========================================================================
# 搜索分词（docs/02 §3.20）
# ===========================================================================
def test_tokenize_splits_cjk_into_single_chars() -> None:
    assert tokenize("内网工具") == "内 网 工 具"


def test_tokenize_keeps_ascii_words_intact() -> None:
    assert tokenize("log analyzer v1.2") == "log analyzer v1.2"


def test_tokenize_mixed_text() -> None:
    result = tokenize("日志分析器 log-analyzer")
    assert "日" in result.split()
    assert "log-analyzer" in result.split()


def test_tokenize_drops_punctuation() -> None:
    assert "," not in tokenize("a,b;c")


def test_tokenize_query_returns_tokens() -> None:
    assert tokenize_query("内网 工具") == ["内", "网", "工", "具"]
    assert tokenize_query("") == []


# ===========================================================================
# meta 只暴露公开设置（FR-CFG-03）
# ===========================================================================
async def test_meta_exposes_only_public_settings(client) -> None:
    async with SessionLocal() as session:
        public_keys = {
            row.key
            for row in (
                await session.execute(
                    select(SystemSetting).where(SystemSetting.is_public.is_(True))
                )
            )
            .scalars()
            .all()
        }
        private_keys = {
            row.key
            for row in (
                await session.execute(
                    select(SystemSetting).where(SystemSetting.is_public.is_(False))
                )
            )
            .scalars()
            .all()
        }

    assert "portal.site_name" in public_keys
    assert "security.login_max_failures" in private_keys

    response = await client.get("/api/v1/meta")
    assert response.status_code == 200
    body = response.json()

    # 私有设置项的名字绝不能出现在响应里
    raw = response.text
    for key in private_keys:
        assert key not in raw, f"私有设置项 {key} 泄漏到 /meta"
        assert key.split(".")[-1] not in body, f"私有设置项 {key} 泄漏到 /meta"

    assert set(body) == {
        "site_name",
        "announcement_md",
        "auth_provider",
        "allow_anonymous_view",
        "default_sort",
        "page_size",
        "app_version",
        "api_version",
        "features",
        # M12（契约 §27.3）：站点定制的 5 个新字段 —— 只新增，既有字段未动。
        # 逐字冻结在 tests/test_m12_portal_settings.py 里。
        "site_subtitle",
        "footer_org",
        "footer_contact_email",
        "footer_contact_phone",
        "footer_notice",
        # M13（契约 §28.4）：第 6 个字段 —— 同上，只新增。
        # 逐字冻结在 tests/test_m13_footer_tagline.py 里。
        "footer_tagline",
    }


async def test_meta_is_reachable_without_auth(client) -> None:
    assert (await client.get("/api/v1/meta")).status_code == 200


# ===========================================================================
# 探针
# ===========================================================================
async def test_healthz(client) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-Id"]


async def test_readyz_checks_database_and_storage(client) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    names = {check["name"]: check["ok"] for check in body["checks"]}
    assert names == {"database": True, "storage": True}


# ===========================================================================
# 请求 ID
# ===========================================================================
async def test_request_id_is_echoed_when_valid(client) -> None:
    provided = "01HQ8X5K2M9PQR3TVWXYZ4ABCD"
    response = await client.get("/healthz", headers={"X-Request-Id": provided})
    assert response.headers["X-Request-Id"] == provided


async def test_request_id_regenerated_when_invalid(client) -> None:
    response = await client.get("/healthz", headers={"X-Request-Id": "not-valid!!"})
    generated = response.headers["X-Request-Id"]
    assert generated != "not-valid!!"
    assert len(generated) == 26


async def test_request_id_appears_in_error_body(client) -> None:
    """错误响应体里必须带 `request_id`，且与响应头一致。

    用「不存在的 API 路径 → 404」作为载体，而不是原先的「未登录 → 401」：
    后者依赖 `/api/v1/tools` 对匿名返回 401，而匿名浏览的默认值改成 true 之后
    那条路径已经是 200 了。404 这个载体与鉴权配置无关，更稳。
    """
    response = await client.get("/api/v1/definitely-not-a-route")
    assert response.status_code == 404
    assert response.json()["request_id"] == response.headers["X-Request-Id"]


# ===========================================================================
# SPA fallback —— 不能用 StaticFiles(html=True) 挂根路径
# ===========================================================================
def test_non_spa_prefixes_include_api() -> None:
    assert "api/" in NON_SPA_PREFIXES


async def test_unmatched_api_path_returns_json_not_html(client) -> None:
    """关键回归：`/api/v1/typo` 必须返回 JSON 错误，不能是 index.html。"""
    response = await client.get("/api/v1/typo-does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["code"] == "NOT_FOUND"


async def test_spa_fallback_serves_index_for_client_routes(tmp_path, monkeypatch) -> None:
    """构造一个带 `web/dist` 的应用，验证前端路由回退而 API 不回退。"""
    from fastapi import FastAPI

    from app.api.v1 import api_router
    from app.core.config import Settings
    from app.main import _mount_spa

    (tmp_path / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log(1)", encoding="utf-8")

    monkeypatch.setattr(Settings, "web_dist_dir", property(lambda self: tmp_path))

    from app.core.errors import DomainError
    from app.main import domain_error_handler

    test_app = FastAPI()
    test_app.include_router(api_router, prefix="/api/v1")
    test_app.add_exception_handler(DomainError, domain_error_handler)
    _mount_spa(test_app)

    import httpx

    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        # 前端路由 → index.html
        spa = await c.get("/tools/some-slug")
        assert spa.status_code == 200
        assert "spa" in spa.text

        # 静态资源
        asset = await c.get("/assets/app.js")
        assert asset.status_code == 200

        # API 未匹配 → JSON 404，绝不是 index.html
        api = await c.get("/api/v1/typo")
        assert api.status_code == 404
        assert api.headers["content-type"].startswith("application/json")
        assert "spa" not in api.text

        # 路径存在但方法不对 → 405，同样不能被 SPA 兜底吃成 404。
        # 这条断言钉的是「API 的可观察行为不得随 web/dist 是否存在而变化」：
        # 兜底路由若参与匹配，就会把 405 变成 404，而它只在 dist 存在时才注册，
        # 于是 CI（无 dist）与开发者本机（有 dist）对同一个请求给出不同状态码。
        # 本用例自建 dist，因此两种环境下都真正跑得到这条断言。
        wrong_method = await c.get("/api/v1/admin/groups/999999")
        assert wrong_method.status_code == 405, (
            f"应为 405（方法不允许），实际 {wrong_method.status_code} —— "
            "SPA 兜底是否把 API 的 405 吃成了 404？"
        )
        assert wrong_method.headers["content-type"].startswith("application/json")


# ===========================================================================
# 配置层：弱 SECRET_KEY 必须显式告警
# ===========================================================================
def test_short_secret_key_warns(caplog) -> None:
    """HS256 密钥短于 32 字节时，PyJWT 只打一条 warning 就照常签发。

    也就是说「弱密钥」在功能上完全看不出来 —— 所以在配置层显式告警一次。
    生产由 install.sh 的 `openssl rand -hex 32` 生成，不受影响。
    """
    from app.core.config import Settings

    with caplog.at_level("WARNING", logger="app.core.config"):
        Settings(secret_key="short", _env_file=None)
    assert any("SECRET_KEY" in r.message and "32" in r.message for r in caplog.records)

    caplog.clear()
    with caplog.at_level("WARNING", logger="app.core.config"):
        Settings(secret_key="a" * 64, _env_file=None)
    assert not caplog.records, "足够长的密钥不应产生告警"


def test_empty_secret_key_is_rejected() -> None:
    import pytest as _pytest

    from app.core.config import Settings

    with _pytest.raises(ValueError):
        Settings(secret_key="", _env_file=None)


def test_env_var_aliases_for_token_ttl() -> None:
    """docs/05 §5.4 用 ACCESS_TOKEN_EXPIRE_MINUTES，任务书用 ACCESS_TOKEN_MINUTES。

    两个名字都要认（AliasChoices），否则按 docs/05 生成的
    /etc/localcraft/localcraft.env 会被静默忽略。
    """
    from app.core.config import Settings

    assert Settings(_env_file=None, ACCESS_TOKEN_MINUTES=45).access_token_minutes == 45
    assert (
        Settings(_env_file=None, ACCESS_TOKEN_EXPIRE_MINUTES=50).access_token_minutes == 50
    )
    assert Settings(_env_file=None, REFRESH_TOKEN_DAYS=3).refresh_token_days == 3
    assert Settings(_env_file=None, REFRESH_TOKEN_EXPIRE_DAYS=4).refresh_token_days == 4


def test_refresh_default_matches_frozen_contract() -> None:
    """契约 §3.2 的 Cookie Max-Age=604800（7 天）必须与默认值一致。"""
    from app.core.config import Settings

    settings = Settings(_env_file=None)
    assert settings.refresh_token_days == 7
    assert settings.refresh_token_seconds == 604800
    assert settings.access_token_minutes == 30


# ===========================================================================
# 回归：进程内跑 Alembic 之后，应用日志不能被静默关闭
# ===========================================================================
def test_alembic_fileconfig_does_not_disable_app_loggers() -> None:
    """`logging.config.fileConfig` 默认 `disable_existing_loggers=True`，
    会把所有已存在的 logger 关掉。

    migrations/env.py 必须传 False，否则在进程内调用 alembic（测试夹具、
    将来的升级 API、被嵌入的工具）之后应用日志会「凭空消失」，
    pytest 的 caplog 也会一并失效 —— 这类现象极难定位，所以钉一条回归。
    """
    import logging

    from alembic.config import Config

    env_py = BACKEND_DIR / "migrations" / "env.py"
    source = env_py.read_text(encoding="utf-8")
    assert "disable_existing_loggers=False" in source, (
        "migrations/env.py 的 fileConfig 必须显式传 disable_existing_loggers=False"
    )

    # 真跑一次迁移入口（库里已是最新版本，等价于 no-op），再确认 logger 还活着
    from alembic import command

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(cfg, "head")

    probe = logging.getLogger("app.core.config")
    assert probe.disabled is False, "跑完 alembic 后应用 logger 不应被禁用"
    assert probe.isEnabledFor(logging.WARNING)
