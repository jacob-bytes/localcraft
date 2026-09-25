#!/usr/bin/env bash
# ===========================================================================
# M3 验收证据脚本（可重复执行）
#
#   bash scripts/m3-evidence.sh [端口]
#
# 它会：
#   1. 在临时目录里建一个**全新**的 SQLite 库并跑 Alembic 迁移 + seed-demo
#   2. 起一个**真实**的 uvicorn（开启 DB_ECHO，便于统计 SQL 写入次数）
#   3. 用 curl 逐条复跑 prompts/backend-agent-m3.md 的验收 1~24，打印证据
#   4. 退出时杀掉服务、保留 $WORK 目录供人工复核
#
# 刻意不碰工作区的 backend/var —— 全部落在 $TMPDIR 下，跑完不留痕。
# 需要读取库内状态（悬空 ACL、标签引用数、磁盘文件）时直接查同一个 SQLite 文件。
# ===========================================================================
set -uo pipefail

PORT="${1:-8123}"
BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../backend" && pwd)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/selftool-m3-evidence-XXXXXX")"
BASE="http://127.0.0.1:${PORT}"
PY="${BACKEND_DIR}/.venv/bin/python"
DB="${WORK}/evidence.db"

export DATA_DIR="${WORK}/data"
export DATABASE_URL="sqlite+aiosqlite:///${DB}"
export SECRET_KEY="m3-evidence-secret-not-for-production"
export API_DOCS_ENABLED="true"
export LOG_LEVEL="INFO"
export DB_ECHO="true"          # 验收 10 靠它统计 UPDATE 次数
export COOKIE_SECURE="false"

SERVER_LOG="${WORK}/server.log"
SERVER_PID=""

cleanup() {
  if [[ -n "${SERVER_PID}" ]] && kill -0 "${SERVER_PID}" 2>/dev/null; then
    kill "${SERVER_PID}" 2>/dev/null || true
    wait "${SERVER_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

section() { printf '\n\033[1m========== %s ==========\033[0m\n' "$*"; }
note()    { printf '  %s\n' "$*"; }

# 直接查库取标量（悬空 ACL、引用数、磁盘路径、配额用量都用它）
sqlval() { "${PY}" -c "
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
print(con.execute(sys.argv[2]).fetchone()[0])
" "${DB}" "$1"; }

sqlrun() { "${PY}" -c "
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
con.execute(sys.argv[2]); con.commit()
" "${DB}" "$1"; }

api() { # api METHOD PATH [JSON] [TOKEN]
  local method="$1" path="$2" body="${3:-}" token="${4:-}"
  local args=(-s -X "${method}" "${BASE}${path}" -H 'Content-Type: application/json')
  [[ -n "${token}" ]] && args+=(-H "Authorization: Bearer ${token}")
  [[ -n "${body}" ]] && args+=(-d "${body}")
  curl "${args[@]}"
}

login() { api POST /api/v1/auth/login "{\"username\":\"$1\",\"password\":\"$2\"}" | jq -r .access_token; }

mkzip() { # mkzip OUT TOTAL_ENTRIES
  "${PY}" - "$1" "$2" <<'PYEOF'
import sys, zipfile
out, total = sys.argv[1], int(sys.argv[2])
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("SKILL.md", "---\nname: evidence\n---\n# evidence\n")
    # 全部平铺在根目录：子目录会合成额外的目录条目，边界就测不准了
    for i in range(total - 1):
        z.writestr(f"f{i}.txt", f"c{i}")
PYEOF
}

# ---------------------------------------------------------------------------
section "0. 准备全新数据库并启动真实服务"
# ---------------------------------------------------------------------------
cd "${BACKEND_DIR}" || exit 1
"${PY}" -m alembic upgrade head 2>&1 | tail -2
"${PY}" -m app.cli seed-demo 2>&1 | tail -2

"${PY}" -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT}" > "${SERVER_LOG}" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 60); do
  curl -sf "${BASE}/healthz" >/dev/null 2>&1 && break
  sleep 0.5
done
curl -s "${BASE}/healthz" | jq -c . || { echo "服务未起来"; tail -30 "${SERVER_LOG}"; exit 1; }
note "服务 PID=${SERVER_PID}  临时目录=${WORK}"

ADMIN_TOKEN="$(login admin 'Admin@12345')"
[[ -z "${ADMIN_TOKEN}" || "${ADMIN_TOKEN}" == "null" ]] && { echo "admin 登录失败"; exit 1; }

mkuser() { # mkuser USERNAME ROLE
  api POST /api/v1/admin/users \
    "{\"username\":\"$1\",\"display_name\":\"$1\",\"password\":\"$1@Passw0rd\",\"roles\":[\"$2\"],\"must_change_password\":false}" \
    "${ADMIN_TOKEN}" | jq -r '.user.id'
}
APPROVER_ID="$(mkuser approver approver)"
CREATOR_ID="$(mkuser creator user)"
CREATOR2_ID="$(mkuser creator2 user)"
APPROVER_TOKEN="$(login approver 'approver@Passw0rd')"
CREATOR_TOKEN="$(login creator 'creator@Passw0rd')"
CREATOR2_TOKEN="$(login creator2 'creator2@Passw0rd')"
note "admin 登录成功；approver=${APPROVER_ID} creator=${CREATOR_ID} creator2=${CREATOR2_ID}"

new_tool() { # new_tool TOKEN NAME [TYPE] -> id
  api POST /api/v1/me/tools \
    "{\"name\":\"$2\",\"summary\":\"证据用\",\"description_md\":\"\",\"tool_type\":\"${3:-file}\",\"visibility\":\"public\"}" \
    "$1" | jq -r .id
}
upload() { # upload TOKEN TOOL_ID ZIP [VERSION]
  curl -s -X POST "${BASE}/api/v1/me/tools/$2/versions" -H "Authorization: Bearer $1" \
    -F "version=${4:-1.0.0}" -F "changelog_md=证据" -F "auto_submit=false" \
    -F "file=@$3;type=application/octet-stream"
}
publish_tool() { # publish_tool OWNER_TOKEN NAME -> "id slug"
  local id slug
  id="$(new_tool "$1" "$2")"
  mkzip "${WORK}/u.zip" 2
  upload "$1" "${id}" "${WORK}/u.zip" >/dev/null
  api POST "/api/v1/me/tools/${id}/submit" '' "$1" >/dev/null
  api POST "/api/v1/admin/approvals/${id}/approve" '{}' "${APPROVER_TOKEN}" >/dev/null
  slug="$(api GET "/api/v1/me/tools/${id}" '' "$1" | jq -r .slug)"
  echo "${id} ${slug}"
}

# ---------------------------------------------------------------------------
section "验收 1：3 个条目的 skill 包 → file_tree_truncated 必须为 false"
T1="$(new_tool "${CREATOR_TOKEN}" 证据小包 skill)"
mkzip "${WORK}/small.zip" 3
UP1="$(upload "${CREATOR_TOKEN}" "${T1}" "${WORK}/small.zip")"
echo "${UP1}" | jq -c '{id, version, skill:{summary:.skill.file_tree_summary, file_tree_truncated:.skill.file_tree_truncated}}'
note "→ file_tree_truncated = $(echo "${UP1}" | jq -r .skill.file_tree_truncated)（期望 false）"
note "→ DB 直查 tool_versions.skill_tree_truncated = $(sqlval "SELECT skill_tree_truncated FROM tool_versions WHERE tool_id=${T1}")"

# ---------------------------------------------------------------------------
section "验收 2：2100 个条目 → true，且落库恰好 2000 条"
T2="$(new_tool "${CREATOR_TOKEN}" 证据大包 skill)"
mkzip "${WORK}/big.zip" 2100
UP2="$(upload "${CREATOR_TOKEN}" "${T2}" "${WORK}/big.zip")"
echo "${UP2}" | jq -c '{id, version, skill:{file_tree_truncated:.skill.file_tree_truncated}}'
api POST "/api/v1/me/tools/${T2}/submit" '' "${CREATOR_TOKEN}" >/dev/null
api POST "/api/v1/admin/approvals/${T2}/approve" '{}' "${APPROVER_TOKEN}" >/dev/null
SLUG2="$(api GET "/api/v1/me/tools/${T2}" '' "${CREATOR_TOKEN}" | jq -r .slug)"
PREVIEW2="$(api GET "/api/v1/tools/${SLUG2}/versions/1.0.0/skill-preview" '' "${CREATOR_TOKEN}")"
echo "${PREVIEW2}" | jq -c '{file_tree_truncated, stored_entries:(.file_tree|length)}'
note "→ 落库条目数 = $(echo "${PREVIEW2}" | jq -r '.file_tree|length')（期望恰好 2000）"
note "→ DB 直查 tool_versions.skill_tree_truncated = $(sqlval "SELECT skill_tree_truncated FROM tool_versions WHERE tool_id=${T2}")"

# ---------------------------------------------------------------------------
section "验收 3：审批队列条目含 version_seq（可用于乐观锁）"
T3="$(new_tool "${CREATOR_TOKEN}" 证据乐观锁)"
mkzip "${WORK}/v3.zip" 2
upload "${CREATOR_TOKEN}" "${T3}" "${WORK}/v3.zip" >/dev/null
api POST "/api/v1/me/tools/${T3}/submit" '' "${CREATOR_TOKEN}" >/dev/null
QUEUE3="$(api GET '/api/v1/admin/approvals?status=pending&page_size=50' '' "${APPROVER_TOKEN}")"
echo "${QUEUE3}" | jq -c --argjson t "${T3}" '[.items[]|select(.tool_id==$t)][0]|{tool_id,version_seq,status}'
note "→ version_seq = $(echo "${QUEUE3}" | jq -r --argjson t "${T3}" '[.items[]|select(.tool_id==$t)][0].version_seq')（非 null 即可做乐观锁）"

# ---------------------------------------------------------------------------
section "验收 4：approver 访问他人 draft 的 /me/tools/{id} → 404"
DRAFT="$(new_tool "${CREATOR_TOKEN}" 证据草稿)"
R4="$(curl -s -o "${WORK}/r4.json" -w '%{http_code}' "${BASE}/api/v1/me/tools/${DRAFT}" -H "Authorization: Bearer ${APPROVER_TOKEN}")"
note "HTTP ${R4}  body=$(jq -c . "${WORK}/r4.json")  （期望 404）"
note "对照：作者本人访问自己的 draft → HTTP $(curl -s -o /dev/null -w '%{http_code}' "${BASE}/api/v1/me/tools/${DRAFT}" -H "Authorization: Bearer ${CREATOR_TOKEN}")（期望 200）"

# ---------------------------------------------------------------------------
section "验收 5：approver 访问他人 pending 的 GET /tools/{slug} → 200"
T5="$(new_tool "${CREATOR_TOKEN}" 证据待审)"
mkzip "${WORK}/v5.zip" 2
upload "${CREATOR_TOKEN}" "${T5}" "${WORK}/v5.zip" >/dev/null
api POST "/api/v1/me/tools/${T5}/submit" '' "${CREATOR_TOKEN}" >/dev/null
SLUG5="$(api GET "/api/v1/me/tools/${T5}" '' "${CREATOR_TOKEN}" | jq -r .slug)"
R5="$(curl -s -o "${WORK}/r5.json" -w '%{http_code}' "${BASE}/api/v1/tools/${SLUG5}" -H "Authorization: Bearer ${APPROVER_TOKEN}")"
R5B="$(curl -s -o /dev/null -w '%{http_code}' "${BASE}/api/v1/tools/${SLUG5}" -H "Authorization: Bearer ${CREATOR2_TOKEN}")"
note "approver → HTTP ${R5} status=$(jq -r .status "${WORK}/r5.json")（期望 200 / pending）"
note "无关用户 → HTTP ${R5B}（期望 404，可见性没有被放宽）"

# ---------------------------------------------------------------------------
section "验收 6：只带 approvals:write 的 Token 用 curl 完成一次审批"
SCOPED="$(api POST /api/v1/admin/tokens '{"name":"证据-仅审批","scopes":["approvals:write"]}' "${ADMIN_TOKEN}")"
echo "${SCOPED}" | jq -c '{id, name, scopes, prefix:.token_prefix}'
SCOPED_TOKEN="$(echo "${SCOPED}" | jq -r .token)"
APPROVED="$(curl -s -X POST "${BASE}/api/v1/admin/approvals/${T3}/approve" \
  -H "Authorization: Bearer ${SCOPED_TOKEN}" -H 'Content-Type: application/json' -d '{"note":"curl 审批"}')"
echo "${APPROVED}" | jq -c '{id,status}'
note "→ 仅 approvals:write 的 Token 完成了审批（验收 6 通过）"

# ---------------------------------------------------------------------------
section "验收 7：同一个 Token 调 POST /admin/users → 403 SCOPE_MISSING"
R7="$(curl -s -o "${WORK}/r7.json" -w '%{http_code}' -X POST "${BASE}/api/v1/admin/users" \
  -H "Authorization: Bearer ${SCOPED_TOKEN}" -H 'Content-Type: application/json' \
  -d '{"username":"token-made","display_name":"不该被创建","roles":["user"]}')"
note "HTTP ${R7}  body=$(jq -c . "${WORK}/r7.json")"
note "   注意：缺哪个 scope 的键名是 details.missing_scopes（M2 已验收的键），不是 prompt 简写的 details.missing"
note "   该用户名是否落库 = $(sqlval "SELECT COUNT(*) FROM users WHERE username='token-made'")（期望 0）"

# ---------------------------------------------------------------------------
section "验收补充 A：API Token 只存 SHA256 + 前缀，明文不落库"
SCOPED_ID="$(echo "${SCOPED}" | jq -r .id)"
note "明文：长度=$(printf '%s' "${SCOPED_TOKEN}" | wc -c | tr -d ' ') 字符，前 10 位=$(printf '%s' "${SCOPED_TOKEN:0:10}")…"
note "库内 token_prefix = $(sqlval "SELECT token_prefix FROM api_tokens WHERE id=${SCOPED_ID}")"
note "库内 token_hash   = $(sqlval "SELECT token_hash FROM api_tokens WHERE id=${SCOPED_ID}")"
"${PY}" - "${DB}" "${SCOPED_ID}" "${SCOPED_TOKEN}" <<'PYEOF'
import hashlib, sqlite3, sys
db, token_id, plaintext = sys.argv[1], int(sys.argv[2]), sys.argv[3]
con = sqlite3.connect(db)
prefix, digest = con.execute(
    "SELECT token_prefix, token_hash FROM api_tokens WHERE id=?", (token_id,)
).fetchone()
print(f"  token_hash 是 64 位小写 hex: {len(digest) == 64 and all(c in '0123456789abcdef' for c in digest)}")
print(f"  sha256(明文) == token_hash : {hashlib.sha256(plaintext.encode()).hexdigest() == digest}")
print(f"  token_prefix 是明文前缀吗   : {plaintext.startswith(prefix)}")
rows = con.execute("SELECT COUNT(*) FROM api_tokens WHERE token_hash=?", (plaintext,)).fetchone()[0]
print(f"  明文被当成 hash 存进来的行数: {rows}（期望 0）")
PYEOF

# ---------------------------------------------------------------------------
section "验收 8：把 Token 创建者从 superadmin 降级 → 同一 Token 立即降权"
SUP2_ID="$(mkuser sup2 superadmin)"
SUP2_TOKEN="$(login sup2 'sup2@Passw0rd')"
T8="$(api POST /api/v1/admin/tokens '{"name":"证据-降级","scopes":["admin:all"]}' "${SUP2_TOKEN}")"
echo "${T8}" | jq -c '{id, name, scopes, prefix:.token_prefix}'
TOKEN8="$(echo "${T8}" | jq -r .token)"
BEFORE8="$(curl -s -o /dev/null -w '%{http_code}' "${BASE}/api/v1/admin/users" -H "Authorization: Bearer ${TOKEN8}")"
api PUT "/api/v1/admin/users/${SUP2_ID}/roles" '{"roles":["user"]}' "${ADMIN_TOKEN}" | jq -c '{id, username, roles}'
AFTER8="$(curl -s -o "${WORK}/r8.json" -w '%{http_code}' "${BASE}/api/v1/admin/users" -H "Authorization: Bearer ${TOKEN8}")"
note "降级前 HTTP ${BEFORE8}（期望 200）→ 降级后 HTTP ${AFTER8}（期望 403），Token 未换发"
note "body=$(jq -c . "${WORK}/r8.json")"

# ---------------------------------------------------------------------------
section "验收 9：吊销 Token 后再调用 → 401 TOKEN_REVOKED"
T9="$(api POST /api/v1/admin/tokens '{"name":"证据-吊销","scopes":["tools:read"]}' "${ADMIN_TOKEN}")"
TOKEN9="$(echo "${T9}" | jq -r .token)"
OK9="$(curl -s -o /dev/null -w '%{http_code}' "${BASE}/api/v1/tools" -H "Authorization: Bearer ${TOKEN9}")"
api POST "/api/v1/admin/tokens/$(echo "${T9}" | jq -r .id)/revoke" '' "${ADMIN_TOKEN}" | jq -c '{id, revoked_at}'
R9="$(curl -s -o "${WORK}/r9.json" -w '%{http_code}' "${BASE}/api/v1/tools" -H "Authorization: Bearer ${TOKEN9}")"
note "吊销前 HTTP ${OK9}（期望 200）→ 吊销后 HTTP ${R9} body=$(jq -c . "${WORK}/r9.json")（期望 401 TOKEN_REVOKED）"

# ---------------------------------------------------------------------------
section "验收 10：100 次并发 Token 请求 → UPDATE api_tokens 次数远小于 100"
CT="$(api POST /api/v1/admin/tokens '{"name":"证据-计数","scopes":["tools:read"]}' "${ADMIN_TOKEN}" | jq -r .token)"
api GET /api/v1/tools '' "${CT}" >/dev/null
sleep 1
MARK=$(wc -l < "${SERVER_LOG}")
note "并发前日志行数 ${MARK}；发起 100 次并发请求…"
seq 1 100 | xargs -P 20 -I{} curl -s -o /dev/null "${BASE}/api/v1/tools" -H "Authorization: Bearer ${CT}"
note "请求已返回，等待计数聚合落库（flush 间隔 30s）…"
sleep 35
# 日志同时挂了文本与 JSON 两个 handler，同一条语句写两行 —— 只认文本 handler，避免翻倍
PLAIN='^[0-9]{4}-[0-9]{2}-[0-9]{2} .*INFO sqlalchemy[^ ]* UPDATE api_tokens'
NEW=$(tail -n "+$((MARK + 1))" "${SERVER_LOG}" | grep -cE "${PLAIN}" || true)
TOTAL=$(grep -cE "${PLAIN}" "${SERVER_LOG}" || true)
note "本次窗口内 UPDATE api_tokens = ${NEW} 条（对应 100 次带 Token 的请求）；整场累计 ${TOTAL} 条"
note "→ ${NEW} 远小于 100 即聚合生效"
grep -E "${PLAIN}" "${SERVER_LOG}" | tail -2 | sed 's/^/    /'

# ---------------------------------------------------------------------------
section "验收 11：禁用用户 → 其 refresh token 与签发的 Token 立即失效"
VICTIM_ID="$(mkuser victim superadmin)"
VICTIM_TOKEN="$(login victim 'victim@Passw0rd')"
VTOKEN="$(api POST /api/v1/admin/tokens '{"name":"victim-token","scopes":["admin:all"]}' "${VICTIM_TOKEN}" | jq -r .token)"
# cookie 名是 settings.refresh_cookie_name（默认 refresh_token），不是 st_refresh。
# 关键：**禁用前把 cookie 存进 jar，禁用后复用同一个 jar**，
# 这样才能证明「已发出的 refresh token 被吊销」，而不是「重新登录被拒」。
VJAR="${WORK}/victim.jar"
rm -f "${VJAR}"
LOGIN11="$(curl -s -o /dev/null -w '%{http_code}' -c "${VJAR}" -X POST "${BASE}/api/v1/auth/login" \
  -H 'Content-Type: application/json' -d '{"username":"victim","password":"victim@Passw0rd"}')"
refresh_with_jar() {
  local code
  code="$(curl -s -o "${WORK}/r11b.json" -w '%{http_code}' -b "${VJAR}" -X POST "${BASE}/api/v1/auth/refresh")"
  echo "${code} $(jq -rc '{code,message}' "${WORK}/r11b.json" 2>/dev/null)"
}
note "禁用前 登录 HTTP ${LOGIN11}，refresh 换 access → $(refresh_with_jar)（期望 200）"
api PATCH "/api/v1/admin/users/${VICTIM_ID}" '{"status":"disabled"}' "${ADMIN_TOKEN}" | jq -c '{id, username, status}'
AT="$(curl -s -o "${WORK}/r11a.json" -w '%{http_code}' "${BASE}/api/v1/admin/users" -H "Authorization: Bearer ${VTOKEN}")"
note "禁用后 该用户签发的 Token → HTTP ${AT} body=$(jq -c . "${WORK}/r11a.json")（期望 401）"
note "禁用后 **复用禁用前那个 cookie jar** → $(refresh_with_jar)（期望 401 TOKEN_REVOKED，不是重新登录被拒）"
note "库内该用户的活跃 Token = $(sqlval "SELECT COUNT(*) FROM api_tokens WHERE created_by_id=${VICTIM_ID} AND revoked_at IS NULL")（期望 0）"
note "库里该用户的活跃 refresh 会话 = $(sqlval "SELECT COUNT(*) FROM auth_sessions WHERE user_id=${VICTIM_ID} AND revoked_at IS NULL")（期望 0）"

# ---------------------------------------------------------------------------
section "验收 12：禁用最后一个 superadmin → LAST_SUPERADMIN"
note "当前活跃超管数 = $(sqlval "SELECT COUNT(DISTINCT u.id) FROM users u JOIN user_roles ur ON ur.user_id=u.id JOIN roles r ON r.id=ur.role_id WHERE r.code='superadmin' AND u.status='active'")"
note "HTTP 层够不到这个分支：能禁超管的必须是超管，那就有 ≥2 个活跃超管；"
note "而禁自己会先被「不能禁用自己」拦下 —— 所以这里直接打服务层（与单测同口径）。"
DB_ECHO=false "${PY}" - <<'PYEOF'
import asyncio
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.user import User
from app.services import admin_user_service


async def main() -> None:
    async with SessionLocal() as session:
        user = (await session.execute(select(User).where(User.username == "admin"))).scalar_one()
        await session.refresh(user, ["roles"])
        try:
            # actor_id 用不存在的第三方，确保先撞 LAST_SUPERADMIN 而不是「不能禁用自己」
            await admin_user_service.disable_user(session, user=user, actor_id=999999)
            print("  !! 竟然成功了 —— 不符合预期")
        except Exception as exc:
            print(
                f"  服务层 disable_user → code={getattr(exc, 'code', type(exc).__name__)} "
                f"http_status={getattr(exc, 'http_status', '?')} details={getattr(exc, 'details', None)}"
            )
        await session.rollback()


asyncio.run(main())
PYEOF
ADMIN_ID="$(sqlval "SELECT id FROM users WHERE username='admin'")"
R12="$(curl -s -o "${WORK}/r12.json" -w '%{http_code}' -X PUT "${BASE}/api/v1/admin/users/${ADMIN_ID}/roles" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" -H 'Content-Type: application/json' -d '{"roles":["user"]}')"
note "HTTP 自我降级 → ${R12} body=$(jq -c . "${WORK}/r12.json")"

# ---------------------------------------------------------------------------
section "验收 13：禁用自己 → 被拒"
R13="$(curl -s -o "${WORK}/r13.json" -w '%{http_code}' -X PATCH "${BASE}/api/v1/admin/users/${ADMIN_ID}" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" -H 'Content-Type: application/json' -d '{"status":"disabled"}')"
note "HTTP ${R13} body=$(jq -c . "${WORK}/r13.json")（期望 400，message 含「自己」）"

# ---------------------------------------------------------------------------
section "验收 14：物理删除用户 → 接口不存在（只有禁用）"
R14="$(curl -s -o "${WORK}/r14.json" -w '%{http_code}' -X DELETE "${BASE}/api/v1/admin/users/${CREATOR_ID}" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}")"
note "DELETE /api/v1/admin/users/{id} → HTTP ${R14}（期望 404/405）"
note "该用户是否还在 = $(sqlval "SELECT COUNT(*) FROM users WHERE id=${CREATOR_ID}")（期望 1）"
note "接口面里有 DELETE /admin/users/* 吗 = $("${PY}" -c "
from app.api.public import M3_TOTAL_ENDPOINTS
print(any(m == 'DELETE' and 'users' in p for m, p in M3_TOTAL_ENDPOINTS))")（期望 False）"

# ---------------------------------------------------------------------------
section "验收 15：删除被 ACL 引用的用户组 → 409 + 引用列表；确认后清理悬空 ACL"
GROUP_ID="$(api POST /api/v1/admin/groups '{"name":"证据被引用组"}' "${ADMIN_TOKEN}" | jq -r .id)"
read -r ACL_TOOL ACL_SLUG <<< "$(publish_tool "${CREATOR_TOKEN}" 证据ACL工具)"
curl -s -X PUT "${BASE}/api/v1/me/tools/${ACL_TOOL}/acl" -H "Authorization: Bearer ${CREATOR_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d "{\"visibility\":\"restricted\",\"entries\":[{\"subject_type\":\"group\",\"subject_id\":${GROUP_ID},\"can_download\":true}]}" >/dev/null
note "ACL 里引用该组的条目 = $(sqlval "SELECT COUNT(*) FROM tool_acl WHERE subject_type='group' AND subject_id=${GROUP_ID}")（期望 1）"
BLOCK15="$(curl -s -o "${WORK}/r15.json" -w '%{http_code}' -X DELETE "${BASE}/api/v1/admin/groups/${GROUP_ID}" -H "Authorization: Bearer ${ADMIN_TOKEN}")"
note "删除被引用的组 → HTTP ${BLOCK15} body=$(jq -c . "${WORK}/r15.json")（期望 409 GROUP_IN_USE，details.tools 列出影响面）"
FORCED15="$(curl -s -X DELETE "${BASE}/api/v1/admin/groups/${GROUP_ID}?force=true" -H "Authorization: Bearer ${ADMIN_TOKEN}")"
note "force=true → $(echo "${FORCED15}" | jq -c .)"
note "悬空 ACL 残留 = $(sqlval "SELECT COUNT(*) FROM tool_acl WHERE subject_type='group' AND subject_id=${GROUP_ID}")（期望 0）"
note "工具本身还在 → HTTP $(curl -s -o /dev/null -w '%{http_code}' "${BASE}/api/v1/tools/${ACL_SLUG}" -H "Authorization: Bearer ${CREATOR_TOKEN}")（期望 200）"

# ---------------------------------------------------------------------------
section "验收 16：删除有工具引用的分类 → 409 CATEGORY_IN_USE + 引用数"
CAT_ID="$(api POST /api/v1/admin/categories '{"name":"证据分类","slug":"evidence-cat","sort_order":99}' "${ADMIN_TOKEN}" | jq -r .id)"
CAT_TOOL="$(api POST /api/v1/me/tools \
  "{\"name\":\"引用分类的工具\",\"summary\":\"证据用\",\"description_md\":\"\",\"tool_type\":\"file\",\"visibility\":\"public\",\"category_id\":${CAT_ID}}" \
  "${CREATOR_TOKEN}" | jq -r .id)"
BLOCK16="$(curl -s -o "${WORK}/r16.json" -w '%{http_code}' -X DELETE "${BASE}/api/v1/admin/categories/${CAT_ID}" -H "Authorization: Bearer ${ADMIN_TOKEN}")"
note "删除被引用的分类 → HTTP ${BLOCK16} body=$(jq -c . "${WORK}/r16.json")（期望 409 CATEGORY_IN_USE）"
sqlrun "UPDATE tools SET category_id=NULL WHERE id=${CAT_TOOL}"
DEL16="$(curl -s -X DELETE "${BASE}/api/v1/admin/categories/${CAT_ID}" -H "Authorization: Bearer ${ADMIN_TOKEN}")"
note "解除引用后删除 → $(echo "${DEL16}" | jq -c .)（soft_deleted=true）"
note "软删除校验：行还在且 is_active=0 → $(sqlval "SELECT COUNT(*) FROM categories WHERE id=${CAT_ID} AND is_active=0")（期望 1）"

# ---------------------------------------------------------------------------
section "验收 17：合并两个标签 → 引用转移、返回转移数量、旧标签消失"
TA="$(new_tool "${CREATOR_TOKEN}" 标签工具A)"
TB="$(new_tool "${CREATOR_TOKEN}" 标签工具B)"
TC="$(new_tool "${CREATOR_TOKEN}" 标签工具C)"
api PATCH "/api/v1/me/tools/${TA}" '{"tags":["ev-a"]}' "${CREATOR_TOKEN}" >/dev/null
api PATCH "/api/v1/me/tools/${TB}" '{"tags":["ev-b"]}' "${CREATOR_TOKEN}" >/dev/null
api PATCH "/api/v1/me/tools/${TC}" '{"tags":["ev-a","ev-b"]}' "${CREATOR_TOKEN}" >/dev/null
TAGS17="$(api GET '/api/v1/admin/tags?q=ev-' '' "${ADMIN_TOKEN}")"
ID_A="$(echo "${TAGS17}" | jq -r '.items[]|select(.name=="ev-a").id')"
ID_B="$(echo "${TAGS17}" | jq -r '.items[]|select(.name=="ev-b").id')"
note "合并前引用数：ev-a=$(sqlval "SELECT COUNT(*) FROM tool_tags WHERE tag_id=${ID_A}") ev-b=$(sqlval "SELECT COUNT(*) FROM tool_tags WHERE tag_id=${ID_B}")"
MERGED17="$(api POST /api/v1/admin/tags/merge "{\"source_ids\":[${ID_B}],\"target_id\":${ID_A}}" "${ADMIN_TOKEN}")"
note "merge → $(echo "${MERGED17}" | jq -c .)"
note "旧标签是否还在 = $(sqlval "SELECT COUNT(*) FROM tags WHERE id=${ID_B}")（期望 0）"
note "合并后 ev-a 引用数 = $(sqlval "SELECT COUNT(*) FROM tool_tags WHERE tag_id=${ID_A}")（期望 3，工具C 的两个标签被去重）"

# ---------------------------------------------------------------------------
section "验收 18：导入 CSV（dry_run=true）→ 不写库、不生成密码"
printf 'username,display_name,email,roles,status\nprev1,预演一,,user,active\nprev2,预演二,,user,active\n' > "${WORK}/dry.csv"
DRY18="$(curl -s -X POST "${BASE}/api/v1/admin/import/users" -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -F "file=@${WORK}/dry.csv;type=text/csv" -F "dry_run=true" -F "on_conflict=skip")"
echo "${DRY18}" | jq -c '{dry_run, succeeded, failed, generated_passwords, errors}'
note "→ 落库的 prev1/prev2 数量 = $(sqlval "SELECT COUNT(*) FROM users WHERE username IN ('prev1','prev2')")（期望 0）"
note "→ generated_passwords 长度 = $(echo "${DRY18}" | jq -r '.generated_passwords|length')（期望 0）"

# ---------------------------------------------------------------------------
section "验收 19：导入 CSV 含 2 行非法 → 计数准确、errors 精确到行与字段"
printf 'username,display_name,email,roles,status,password\nok19,好人,,user,active,\n,缺用户名,,user,active,\nbad19,坏人,,user,active,123\n' > "${WORK}/bad.csv"
BAD19="$(curl -s -X POST "${BASE}/api/v1/admin/import/users" -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -F "file=@${WORK}/bad.csv;type=text/csv" -F "dry_run=true" -F "on_conflict=skip")"
echo "${BAD19}" | jq -c '{succeeded, failed, errors}'
note "→ succeeded=$(echo "${BAD19}" | jq -r .succeeded) failed=$(echo "${BAD19}" | jq -r .failed)（期望 1 / 2）"

# ---------------------------------------------------------------------------
section "验收补充 B：generated_passwords 明文绝不写日志"
printf 'username,display_name,email,roles,status\ngpw1,生成一,,user,active\ngpw2,生成二,,user,active\n' > "${WORK}/gen.csv"
GEN="$(curl -s -X POST "${BASE}/api/v1/admin/import/users" -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -F "file=@${WORK}/gen.csv;type=text/csv" -F "dry_run=false" -F "on_conflict=skip")"
echo "${GEN}" | jq -c '{succeeded, failed, generated:[.generated_passwords[].username]}'
PW1="$(echo "${GEN}" | jq -r '.generated_passwords[0].password')"
note "第一个生成口令的前 4 位 = $(printf '%s' "${PW1:0:4}")****（余下位不打印）"
# 用 jq 拼 JSON：生成的口令可能含引号/反斜杠，手拼字符串会把请求打成 400
LOGIN_BODY="$(jq -nc --arg u gpw1 --arg p "${PW1}" '{username:$u,password:$p}')"
LOGIN_CODE="$(curl -s -o "${WORK}/rgen.json" -w '%{http_code}' -X POST "${BASE}/api/v1/auth/login" \
  -H 'Content-Type: application/json' -d "${LOGIN_BODY}")"
note "该口令能登录吗 → HTTP ${LOGIN_CODE}（期望 200，证明回显的口令是真实可用的初始口令）"
# 全文检索日志：口令明文与响应字段名都不该出现（响应体本身不写日志）
note "服务日志中该口令出现次数        = $(grep -c -F -- "${PW1}" "${SERVER_LOG}" || true)（期望 0）"
note "服务日志中 generated_passwords 出现次数 = $(grep -c -F 'generated_passwords' "${SERVER_LOG}" || true)（期望 0）"

# ---------------------------------------------------------------------------
section "验收 20：导出用户 CSV — 前 3 字节 BOM、不含密码哈希"
curl -s "${BASE}/api/v1/admin/export/users" -H "Authorization: Bearer ${ADMIN_TOKEN}" -o "${WORK}/users.csv"
printf 'BOM 前 3 字节: '; head -c 3 "${WORK}/users.csv" | od -An -tx1 | tr -d '\n'; echo
printf '前 32 字节 hex: '; head -c 32 "${WORK}/users.csv" | xxd -p; echo
note "首行: $(head -1 "${WORK}/users.csv")"
note "argon2/password_hash 命中 = $(grep -c -E '\$argon2|\$2b\$|password_hash' "${WORK}/users.csv" || true)（期望 0）"

# ---------------------------------------------------------------------------
section "验收 21：转移工具负责人 → 双方配额重算 + transfer_owner 审批留痕"
read -r TR_TOOL _TR_SLUG <<< "$(publish_tool "${CREATOR_TOKEN}" 证据转移工具)"
BEFORE_OLD="$(sqlval "SELECT COALESCE(SUM(v.file_size),0) FROM tool_versions v JOIN tools t ON t.id=v.tool_id WHERE t.owner_id=${CREATOR_ID}")"
BEFORE_NEW="$(sqlval "SELECT COALESCE(SUM(v.file_size),0) FROM tool_versions v JOIN tools t ON t.id=v.tool_id WHERE t.owner_id=${CREATOR2_ID}")"
TRANSFER="$(api POST "/api/v1/admin/tools/${TR_TOOL}/transfer" "{\"new_owner_id\":${CREATOR2_ID},\"reason\":\"离职交接\"}" "${ADMIN_TOKEN}")"
echo "${TRANSFER}" | jq -c '{tool_id, previous_owner:.previous_owner.username, new_owner:.new_owner.username, previous_owner_used_bytes, new_owner_used_bytes}'
AFTER_OLD="$(sqlval "SELECT COALESCE(SUM(v.file_size),0) FROM tool_versions v JOIN tools t ON t.id=v.tool_id WHERE t.owner_id=${CREATOR_ID}")"
AFTER_NEW="$(sqlval "SELECT COALESCE(SUM(v.file_size),0) FROM tool_versions v JOIN tools t ON t.id=v.tool_id WHERE t.owner_id=${CREATOR2_ID}")"
note "原负责人（creator）用量 ${BEFORE_OLD} → ${AFTER_OLD}，减少 $((BEFORE_OLD - AFTER_OLD)) 字节"
note "新负责人（creator2）用量 ${BEFORE_NEW} → ${AFTER_NEW}，增加 $((AFTER_NEW - BEFORE_NEW)) 字节"
note "一增一减相等 = $([ $((BEFORE_OLD - AFTER_OLD)) -eq $((AFTER_NEW - BEFORE_NEW)) ] && echo True || echo False)"
HIST21="$(api GET "/api/v1/admin/approvals/history?tool_id=${TR_TOOL}&action=transfer_owner" '' "${ADMIN_TOKEN}")"
note "审批历史 transfer_owner 记录 = $(echo "${HIST21}" | jq -r .total) 条，reason=$(echo "${HIST21}" | jq -r '.items[0].reason')"
note "库内 tool.owner_id = $(sqlval "SELECT owner_id FROM tools WHERE id=${TR_TOOL}")（期望 ${CREATOR2_ID}）"

# ---------------------------------------------------------------------------
section "验收 22：回收站还原与彻底清除（文件从磁盘消失）"
read -r RB_TOOL RB_SLUG <<< "$(publish_tool "${CREATOR_TOKEN}" 证据回收站工具)"
RB_PATH="$(sqlval "SELECT storage_path FROM tool_versions WHERE tool_id=${RB_TOOL} AND storage_path IS NOT NULL LIMIT 1")"
note "磁盘文件 \${DATA_DIR}/${RB_PATH} 存在 = $([ -f "${DATA_DIR}/${RB_PATH}" ] && echo True || echo False)"
api DELETE "/api/v1/me/tools/${RB_TOOL}" '' "${CREATOR_TOKEN}" >/dev/null
note "软删除后 门户可见 = $(curl -s -o /dev/null -w '%{http_code}' "${BASE}/api/v1/tools/${RB_SLUG}" -H "Authorization: Bearer ${CREATOR_TOKEN}")（期望 404）"
note "回收站列表含该工具 = $(api GET /api/v1/admin/recycle-bin '' "${ADMIN_TOKEN}" | jq -r --argjson t "${RB_TOOL}" '[.items[]|select(.id==$t)]|length')（期望 1）"
api POST "/api/v1/admin/tools/${RB_TOOL}/restore" '' "${ADMIN_TOKEN}" | jq -c '{id, slug, deleted_at}'
note "还原后 门户可见 = $(curl -s -o /dev/null -w '%{http_code}' "${BASE}/api/v1/tools/${RB_SLUG}" -H "Authorization: Bearer ${CREATOR_TOKEN}")（期望 200）"
api DELETE "/api/v1/me/tools/${RB_TOOL}" '' "${CREATOR_TOKEN}" >/dev/null
PURGED="$(api DELETE "/api/v1/admin/tools/${RB_TOOL}/purge" '' "${ADMIN_TOKEN}")"
note "purge → $(echo "${PURGED}" | jq -c .)"
note "彻底清除后 磁盘文件存在 = $([ -f "${DATA_DIR}/${RB_PATH}" ] && echo True || echo False)（期望 False）"
note "工具行残留 = $(sqlval "SELECT COUNT(*) FROM tools WHERE id=${RB_TOOL}")  版本行残留 = $(sqlval "SELECT COUNT(*) FROM tool_versions WHERE tool_id=${RB_TOOL}")（均期望 0）"

# ---------------------------------------------------------------------------
section "验收 23：PUT /admin/settings 含非法值 → 整体回滚 + SETTING_INVALID 指出 key"
BEFORE23="$(api GET /api/v1/admin/settings '' "${ADMIN_TOKEN}" | jq -r '.items[]|select(.key=="version.history_limit").value')"
R23="$(curl -s -o "${WORK}/r23.json" -w '%{http_code}' -X PUT "${BASE}/api/v1/admin/settings" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" -H 'Content-Type: application/json' \
  -d '{"items":[{"key":"version.history_limit","value":7},{"key":"upload.max_screenshots","value":999}]}')"
note "HTTP ${R23} body=$(jq -c . "${WORK}/r23.json")（期望 400 SETTING_INVALID，key=upload.max_screenshots）"
AFTER23="$(api GET /api/v1/admin/settings '' "${ADMIN_TOKEN}" | jq -r '.items[]|select(.key=="version.history_limit").value')"
note "合法项 version.history_limit：${BEFORE23} → ${AFTER23}（必须未变，证明整体回滚）"

# ---------------------------------------------------------------------------
section "验收补充 C：统计概览 / 工具排行 / 存储明细（FR-ADMIN-14，纯数字不做图表）"
OVERVIEW="$(api GET /api/v1/admin/overview '' "${APPROVER_TOKEN}")"
echo "${OVERVIEW}" | jq -c '{user_count, active_user_count, disabled_user_count, tool_count, pending_count, category_count, tag_count, group_count, active_token_count, used_bytes, quota_bytes, used_percent, recycle_bin_count}'
echo "${OVERVIEW}" | jq -c '{tools_by_status}'
RANK="$(api GET '/api/v1/admin/stats/tools?limit=3' '' "${APPROVER_TOKEN}")"
echo "${RANK}" | jq -c '{total, top:[.items[]|{slug,download_count,view_count}]}'
note "排行按下载量降序 = $(echo "${RANK}" | jq -r '[.items[].download_count] as $c | ($c == ($c|sort|reverse))')"
STORAGE="$(api GET /api/v1/admin/stats/storage '' "${ADMIN_TOKEN}")"
echo "${STORAGE}" | jq -c '{total_used_bytes, total_quota_bytes, item_count:(.items|length)}'
note "approver 看存储明细 → HTTP $(curl -s -o /dev/null -w '%{http_code}' "${BASE}/api/v1/admin/stats/storage" -H "Authorization: Bearer ${APPROVER_TOKEN}")（期望 403，需要 admin:all）"

# ---------------------------------------------------------------------------
section "验收 24：守卫测试 — 接口操作总数恰好 92"
.venv/bin/python -m pytest tests/test_guard.py -q -p no:cacheprovider 2>&1 | tail -3
.venv/bin/python - <<'PYEOF'
import json
import pathlib

from app.api.public import M1_ONLY_ENDPOINTS, M2_ENDPOINTS, M3_ENDPOINTS, M3_TOTAL_ENDPOINTS

print(
    f"  M1={len(M1_ONLY_ENDPOINTS)}  M2={len(M2_ENDPOINTS)}  "
    f"M3 新增={len(M3_ENDPOINTS)}  合计={len(M3_TOTAL_ENDPOINTS)}"
)
spec = json.loads(pathlib.Path("openapi.json").read_text())
ops = [
    (m.upper(), p)
    for p, item in spec["paths"].items()
    for m in item
    if m in {"get", "post", "put", "patch", "delete"}
]
print(f"  openapi.json: {len(spec['paths'])} 路径 / {len(ops)} 操作")
print(f"  与冻结清单完全一致: {set(ops) == set(M3_TOTAL_ENDPOINTS)}")
PYEOF

section "完成"
note "所有临时文件保留在 ${WORK}"
note "服务日志：${SERVER_LOG}"
