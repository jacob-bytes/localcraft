#!/usr/bin/env bash
# =============================================================================
# localcraft 交付前置检查清单（监控方维护，可重复执行）
#
#   bash contracts/acceptance/pre-delivery-checks.sh
#
# 产出：每项一行 PASS / FAIL / BLOCKED，末尾给汇总。
# 退出码：有 FAIL 时为 1（BLOCKED 不算 FAIL，但会在汇总里单列）。
#
# 设计原则：
#   - 只报告**实测**结果，不采信任何 agent 的自述
#   - 已知未交付项（M6 待做）标记为 BLOCKED 而不是 FAIL —— 它们不是回归
#   - 每一项都留下可复核的命令或证据片段
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BE="${ROOT}/backend"
FE="${ROOT}/web"
PY="${BE}/.venv/bin/python"
PORT="${PORT:-8071}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/localcraft-pdc-XXXXXX")"

PASS=0; FAIL=0; BLOCK=0
declare -a FAILED_ITEMS=() BLOCKED_ITEMS=()

green() { printf '\033[32m%s\033[0m' "$1"; }
red()   { printf '\033[31m%s\033[0m' "$1"; }
yel()   { printf '\033[33m%s\033[0m' "$1"; }

chk() { # chk <id> <desc> <ok:0/1> <evidence>
  local id="$1" desc="$2" ok="$3" ev="${4:-}"
  if [[ "$ok" == "0" ]]; then
    printf '  [%s] %-6s %s\n' "$(green PASS)" "$id" "$desc"
    [[ -n "$ev" ]] && printf '         └ %s\n' "$ev"
    PASS=$((PASS+1))
  else
    printf '  [%s] %-6s %s\n' "$(red FAIL)" "$id" "$desc"
    [[ -n "$ev" ]] && printf '         └ %s\n' "$ev"
    FAIL=$((FAIL+1)); FAILED_ITEMS+=("$id $desc")
  fi
}

blocked() { # blocked <id> <desc> <reason>
  printf '  [%s] %-6s %s\n' "$(yel BLOCKED)" "$1" "$2"
  printf '         └ %s\n' "$3"
  BLOCK=$((BLOCK+1)); BLOCKED_ITEMS+=("$1 $2")
}

section() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

cleanup() {
  [[ -n "${SRV_PID:-}" ]] && kill "$SRV_PID" 2>/dev/null
  wait 2>/dev/null
  echo
  echo "临时目录保留：$WORK"
}
trap cleanup EXIT

# =============================================================================
section "A. 代码与质量门禁"
# =============================================================================

OPS="$(cd "$BE" && $PY -c "import json;s=json.load(open('openapi.json'));print(sum(len(v) for v in s['paths'].values()))" 2>/dev/null)"
chk A1 "接口操作数（M5 冻结基线 92；M6 后应为 93）" "$([[ "$OPS" == "92" || "$OPS" == "93" ]] && echo 0 || echo 1)" "实测 $OPS"

# 注意：pyproject 的 addopts 已含 "-q"，不要再传 -q（会变成 -qq 而**抑制摘要行**）。
# 单次运行同时拿到退出码、用例数与覆盖率，避免跑两遍全套。
(cd "$BE" && $PY -m pytest --cov=app --cov-report=term > "$WORK/pytest.txt" 2>&1); PYTEST_RC=$?
TP="$(grep -oE '[0-9]+ passed' "$WORK/pytest.txt" | head -1)"
BAD="$(grep -oE '[0-9]+ (failed|error)' "$WORK/pytest.txt" | head -1)"
COV="$(grep -E '^TOTAL' "$WORK/pytest.txt" | awk '{print $NF}')"
chk A2 "后端测试全绿" "$([[ "$PYTEST_RC" == "0" ]] && echo 0 || echo 1)" "${TP:-未见摘要行}${BAD:+  ← ${BAD}}"
chk A3 "覆盖率 ≥ 90%" "$([[ "${COV%\%}" -ge 90 ]] 2>/dev/null && echo 0 || echo 1)" "TOTAL ${COV:-未知}"

RUFF="$(cd "$BE" && $PY -m ruff check app tests migrations 2>&1 | tail -1)"
chk A4 "后端 ruff" "$(echo "$RUFF" | grep -q 'All checks passed' && echo 0 || echo 1)" "$RUFF"

if command -v shellcheck >/dev/null 2>&1; then
  SC="$(shellcheck -S warning "${ROOT}"/scripts/*.sh 2>&1 | grep -c '^In ' || true)"
  chk A5 "shellcheck（scripts/*.sh，0 warning）" "$([[ "$SC" == "0" ]] && echo 0 || echo 1)" "warning 组数 $SC"
else
  blocked A5 "shellcheck" "本机未安装 shellcheck"
fi

if [[ -d "$FE/node_modules" ]]; then
  TS="$(cd "$FE" && npx tsc --noEmit 2>&1 | tail -3)"
  chk A6 "前端 tsc --noEmit" "$([[ -z "$TS" ]] && echo 0 || echo 1)" "${TS:-无输出}"
  OXL="$(cd "$FE" && npm run lint 2>&1 | grep -oE '[0-9]+ errors' | head -1)"
  chk A7 "前端 oxlint" "$([[ "$OXL" == "0 errors" ]] && echo 0 || echo 1)" "$OXL"
  PRI="$(cd "$FE" && npm run check:primitives 2>&1 | tail -1)"
  chk A8 "check:primitives（防 shadcn React19 回归）" "$(echo "$PRI" | grep -qiE '通过|pass' && echo 0 || echo 1)" "$PRI"
  DIST="$(cd "$FE" && npm run check:dist 2>&1 | tail -1)"
  chk A9 "dist 无 MSW 残留" "$(echo "$DIST" | grep -qiE '通过|pass' && echo 0 || echo 1)" "$DIST"
  APIT="$(cd "$FE" && npm run check:api-types 2>&1 | tail -2 | tr -d '\n')"
  if (cd "$FE" && npm run check:api-types >/dev/null 2>&1); then
    chk A10 "check:api-types（openapi × types.ts）" 0 "$APIT"
  else
    chk A10 "check:api-types（openapi × types.ts）" 1 "${APIT:-见上方输出}"
  fi
else
  blocked A6 "前端检查" "web/node_modules 不存在（未安装依赖）"
fi

# =============================================================================
section "B. 契约一致性（静态检查 openapi.json）"
# =============================================================================

GREP_SHAPELESS="$(cd "$BE" && $PY - <<'PYEOF'
import json
s = json.load(open('openapi.json'))
bad = []
for p, ops in s['paths'].items():
    for m, o in ops.items():
        for code, r in (o.get('responses') or {}).items():
            if not str(code).startswith('2'):
                continue
            sch = (r.get('content', {}).get('application/json', {}) or {}).get('schema')
            if sch and sch.get('additionalProperties') is True:
                bad.append(f"{m.upper()} {p}")
print(len(bad))
for b in bad:
    print(b)
PYEOF
)"
NSHAPE="$(echo "$GREP_SHAPELESS" | head -1)"
chk B1 "无 additionalProperties:true 的端点（§19.7 J2 / M6 J-8）" "$([[ "$NSHAPE" == "0" ]] && echo 0 || echo 1)" "实测 $NSHAPE 个： $(echo "$GREP_SHAPELESS" | tail -n +2 | tr '\n' ' ')"

HAS_SW="$(cd "$BE" && $PY -c "
import json;s=json.load(open('openapi.json'))
print('yes' if 'storage_warning' in s['components']['schemas'].get('AdminOverviewResponse',{}).get('properties',{}) else 'no')")"
chk B2 "openapi.json 含 storage_warning（F1 陈旧产物）" "$([[ "$HAS_SW" == "yes" ]] && echo 0 || echo 1)" "$HAS_SW"
chk B3 "openapi.json 含 test_openapi_artifact_is_current 守卫" \
  "$(grep -rq 'test_openapi_artifact_is_current' "$BE/tests" 2>/dev/null && echo 0 || echo 1)" \
  "守卫测试文件是否含该用例"

DIR_EP="$(cd "$BE" && $PY -c "
import json;s=json.load(open('openapi.json'));print('yes' if '/api/v1/directory' in s['paths'] else 'no')")"
chk B4 "GET /api/v1/directory 已交付（M6 J-1，接口面 93）" "$([[ "$DIR_EP" == "yes" ]] && echo 0 || echo 1)" "$DIR_EP"

SLUG="$(grep -c '"slug": slug' "$BE/app/services/group_service.py" 2>/dev/null | head -1)"
SLUG="${SLUG:-0}"
chk B5 "GROUP_IN_USE 的 details.tools 含 slug（M6 J-7）" "$([[ "$SLUG" -gt 0 ]] && echo 0 || echo 1)" "group_service.py 命中 $SLUG"
# =============================================================================
section "C. 文档完整性"
# =============================================================================
DOCS_OK=0
for f in 01-需求规格说明书 02-数据模型设计 03-API接口清单 04-前端页面与交互清单 \
         05-部署与运维方案 06-用户手册 07-管理员手册 08-运维手册 09-勘误与已知限制; do
  [[ -f "${ROOT}/docs/${f}.md" ]] || { DOCS_OK=1; echo "    缺失 docs/${f}.md"; }
done
chk C1 "docs/01~09 九份文档齐全" "$DOCS_OK"
chk C2 "RELEASE-NOTES.md 存在" "$([[ -f "${ROOT}/RELEASE-NOTES.md" ]] && echo 0 || echo 1)"
chk C3 "contracts/CONTRACT.md 存在且含台账" \
  "$(grep -q '裁定传达台账\|裁定传达规则' "${ROOT}/contracts/CONTRACT.md" && echo 0 || echo 1)"
chk C4 "监控方验收脚本存在" "$([[ -f "${ROOT}/contracts/acceptance/real-backend-check.cjs" ]] && echo 0 || echo 1)"

# =============================================================================
section "D. 运维交付件"
# =============================================================================
MISSING=""
for f in scripts/install.sh scripts/uninstall.sh scripts/backup.sh scripts/restore.sh \
         scripts/verify-backup.sh scripts/upgrade.sh scripts/rollback.sh \
         scripts/run-maintenance.sh scripts/disk-alert.sh scripts/security-check.sh \
         scripts/make-release.sh scripts/precheck.sh scripts/wait-healthy.sh \
         scripts/verify-wheelhouse.sh scripts/build-wheelhouse.sh \
         deploy/localcraft.service deploy/localcraft.slice deploy/localcraft.tmpfiles \
         deploy/localcraft-limits.conf deploy/localcraft-maintenance.timer \
         deploy/localcraft-backup.timer deploy/nginx-localcraft-tls.conf; do
  [[ -f "${ROOT}/${f}" ]] || MISSING="${MISSING}${f} "
done
chk D1 "运维交付件齐全（22 项）" "$([[ -z "$MISSING" ]] && echo 0 || echo 1)" "${MISSING:-全部存在}"
for a in x86_64 aarch64; do
  d="$BE/wheelhouse-$a"
  if [[ -d "$d" ]]; then
    n=$(ls "$d" | wc -l | tr -d ' ')
    sd=$(ls "$d" | grep -c '\.tar\.gz' || true)
    ps=$(ls "$d" | grep -c psycopg || true)
    chk "D2-$a" "wheelhouse-${a}（${n} 包 / sdist ${sd} / psycopg ${ps}）" \
      "$([[ "$n" -gt 40 && "$sd" == "0" && "$ps" -ge 2 ]] && echo 0 || echo 1)"
  else
    chk "D2-$a" "wheelhouse-$a 存在" 1 "目录不存在"
  fi
done

# =============================================================================
section "E. 活体检查（起真实服务）"
# =============================================================================
export DATABASE_URL="sqlite+aiosqlite:///$WORK/pdc.db"
export DATA_DIR="$WORK/data"
export SECRET_KEY="pdc-check-secret"
export COOKIE_SECURE=false LOG_LEVEL=warning
cd "$BE" || exit 1
$PY -m alembic upgrade head >/dev/null 2>&1
$PY -m app.cli seed-demo >/dev/null 2>&1
$PY -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --log-level warning > "$WORK/srv.log" 2>&1 &
SRV_PID=$!
for _ in $(seq 1 60); do curl -fsS "http://127.0.0.1:${PORT}/readyz" >/dev/null 2>&1 && break; sleep 0.5; done
A="http://127.0.0.1:${PORT}"

jqv() { $PY -c "import json,sys;d=json.load(sys.stdin);print(eval('d'+sys.argv[1]))" "$1" 2>/dev/null; }
TOK="$(curl -sS -X POST "$A/api/v1/auth/login" -H 'Content-Type: application/json' \
        -d '{"username":"admin","password":"Admin@12345"}' | jqv "['access_token']")"
H="Authorization: Bearer $TOK"

chk E1 "admin 登录 + /readyz" "$([[ -n "$TOK" ]] && echo 0 || echo 1)"

# 注意：这段 Python 嵌在 bash 双引号里，**内部只能用单引号**，
# 否则双引号会提前终止 bash 字符串（这是本项目脚本里踩过的坑）。
SEED="$(cd "$BE" && $PY -c "
import sqlite3
c = sqlite3.connect('$WORK/pdc.db')
q = lambda s: c.execute(s).fetchone()
tools = q('select count(*) from tools')[0]
bad = q('select count(*) from tool_versions where (file_size is not null and storage_path is null) or (file_size is null and storage_path is not null)')[0]
acc = [u for u, _ in c.execute('select username, group_concat(r.code) from users u left join user_roles ur on ur.user_id = u.id left join roles r on r.id = ur.role_id group by u.id')]
has_viewer = 'viewer' in acc
print('|'.join([str(tools), str(bad), str(has_viewer), str(len(acc))]))
")"
IFS='|' read -r SEED_TOOLS SEED_BAD SEED_VIEWER SEED_ACCS <<< "$SEED"
chk E2 "种子 26 工具（M6 后应为 26+）" "$([[ "$SEED_TOOLS" -ge 26 ]] && echo 0 || echo 1)" "实测 $SEED_TOOLS"
chk E3 "无「有大小无文件」中间态" "$([[ "$SEED_BAD" == "0" ]] && echo 0 || echo 1)" "实测 $SEED_BAD"
chk E4 "种子含 viewer 账号" "$(echo "$SEED_VIEWER" | grep -qi true && echo 0 || echo 1)" "账号数 $SEED_ACCS"

IMG="$(curl -sS -H "$H" "$A/api/v1/tools?page_size=24" | $PY -c "
import json,sys
for i in json.load(sys.stdin)['items']:
    if i.get('cover_url'): print(i['cover_url']); break")"
chk E5 "cover_url 含签名" "$(echo "$IMG" | grep -q 'sig=' && echo 0 || echo 1)" "${IMG:0:70}"
chk E6 "无鉴权头凭签名取图 = 200" \
  "$([[ "$(curl -sS -o /dev/null -w '%{http_code}' "$A$IMG")" == "200" ]] && echo 0 || echo 1)"
chk E7 "篡改签名 = 404" \
  "$([[ "$(curl -sS -o /dev/null -w '%{http_code}' "$A${IMG%??}zz")" == "404" ]] && echo 0 || echo 1)"

VT="$(curl -sS -X POST "$A/api/v1/auth/login" -H 'Content-Type: application/json' \
      -d '{"username":"viewer","password":"Viewer@12345"}' | jqv "['access_token']")"
SLUG_V="$(curl -sS -H "Authorization: Bearer $VT" "$A/api/v1/tools?page_size=1" | jqv "['items'][0]['slug']")"
CD="$(curl -sS -H "Authorization: Bearer $VT" "$A/api/v1/tools/$SLUG_V" | $PY -c "
import json,sys;d=json.load(sys.stdin)
print(f\"{d.get('can_download')}|{(d.get('current_version') or {}).get('can_download')}\")")"
IFS='|' read -r CD_TOP CD_VER <<< "$CD"
chk E8 "viewer 顶层 can_download=False" "$([[ "$CD_TOP" == "False" ]] && echo 0 || echo 1)" "$CD_TOP"
chk E9 "viewer current_version.can_download 与顶层一致（M6 J-9）" \
  "$([[ "$CD_TOP" == "$CD_VER" ]] && echo 0 || echo 1)" "顶层=$CD_TOP 嵌套=$CD_VER"

# zip 防护
$PY - <<PYEOF
import zipfile
with zipfile.ZipFile("$WORK/trav.zip","w") as z:
    z.writestr("SKILL.md","# x\n"); z.writestr("../../etc/passwd","x")
PYEOF
TID="$(curl -sS -X POST "$A/api/v1/me/tools" -H "$H" -H 'Content-Type: application/json' \
  -d '{"name":"PDC 安全验证","summary":"检查 zip 防护","description_md":"x","tool_type":"skill","visibility":"public"}' | jqv "['id']")"
TRAV="$(curl -sS -X POST "$A/api/v1/me/tools/$TID/versions" -H "$H" \
  -F "version=1.0.0" -F "changelog_md=x" -F "file=@$WORK/trav.zip;type=application/zip" | jqv "['code']")"
chk E10 "zip 路径穿越被拒（ZIP_PATH_TRAVERSAL）" "$([[ "$TRAV" == "ZIP_PATH_TRAVERSAL" ]] && echo 0 || echo 1)" "$TRAV"

# 账号锁定
LOCK=""
for i in 1 2 3 4 5 6; do
  LOCK="$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$A/api/v1/auth/login" \
    -H 'Content-Type: application/json' -d '{"username":"newbie","password":"wrong"}')"
done
chk E11 "连续 5 次错密后锁定（423）" "$([[ "$LOCK" == "423" ]] && echo 0 || echo 1)" "第 6 次 HTTP $LOCK"

# revoke-sessions 是否吊销 Token（M6 J-3 应使其通过）
NEWTOK="$(curl -sS -X POST "$A/api/v1/admin/tokens" -H "$H" -H 'Content-Type: application/json' \
  -d '{"name":"pdc","scopes":["tools:read"]}' | jqv "['token']")"
curl -sS -X POST "$A/api/v1/admin/users/1/revoke-sessions" -H "$H" -o /dev/null
AFTER_RAW="$(curl -sS -w '\n%{http_code}' -H "Authorization: Bearer $NEWTOK" "$A/api/v1/tools")"
AFTER_CODE="$(echo "$AFTER_RAW" | sed '$d' | jqv "['code']")"
AFTER_HTTP="$(echo "$AFTER_RAW" | tail -1)"
chk E12 "revoke-sessions 一并吊销 API Token（M6 J-3）" \
  "$([[ "$AFTER_CODE" == "TOKEN_REVOKED" ]] && echo 0 || echo 1)" \
  "吊销后 HTTP ${AFTER_HTTP}，code=${AFTER_CODE:-（无，说明仍返回了数据=Token 仍可用）}"

ORPH="$(curl -sS -H "$H" "$A/api/v1/admin/stats/storage" | jqv "['orphan_file_count']")"
chk E13 "orphan_file_count 字段存在（M6 J-4 接线后才有意义）" \
  "$([[ -n "$ORPH" ]] && echo 0 || echo 1)" "当前值 $ORPH"

# --- E14：ACL can_download 的行为验证（唯一可信的判据；静态检查区分不了「序列化」与「授权判定」）---
# 必须用 **role=user** 的账号：viewer 角色本来就不能下载（FR-ACL-05），
# 用 viewer 会让「下载被拒」无法区分「ACL can_download 生效」与「角色就不允许」——
# 这是一个混淆变量，会得到假阳性。zhangsan 是种子里的 role=user 账号。
VID_U="$(curl -sS -H "$H" "$A/api/v1/admin/users?q=zhangsan" | jqv "['items'][0]['id']")"
printf 'acl-probe' > "$WORK/acl.txt"
AID="$(curl -sS -X POST "$A/api/v1/me/tools" -H "$H" -H 'Content-Type: application/json' \
  -d '{"name":"PDC ACL 验证","summary":"验证 can_download 是否生效","description_md":"x","tool_type":"file","visibility":"restricted"}' | jqv "['id']")"
curl -sS -X POST "$A/api/v1/me/tools/$AID/versions" -H "$H" \
  -F "version=1.0.0" -F "changelog_md=x" -F "auto_submit=true" \
  -F "file=@$WORK/acl.txt;type=text/plain" -o /dev/null
# 默认 approval.mode=require，必须先批准，否则工具是 pending，非 owner 一律 404
curl -sS -X POST "$A/api/v1/admin/approvals/$AID/approve" -H "$H" \
  -H 'Content-Type: application/json' -d '{}' -o /dev/null
SLUG_A="$(curl -sS -H "$H" "$A/api/v1/me/tools/$AID" | jqv "['slug']")"
curl -sS -X PUT "$A/api/v1/me/tools/$AID/acl" -H "$H" -H 'Content-Type: application/json' \
  -d "{\"visibility\":\"restricted\",\"entries\":[{\"subject_type\":\"user\",\"subject_id\":$VID_U,\"can_download\":false}]}" -o /dev/null
VT2="$(curl -sS -X POST "$A/api/v1/auth/login" -H 'Content-Type: application/json' \
  -d '{"username":"zhangsan","password":"Author@12345"}' | jqv "['access_token']")"
SEE_OK="$(curl -sS -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $VT2" "$A/api/v1/tools/$SLUG_A")"
DLGATE="$(curl -sS -o /dev/null -w '%{http_code}' -X POST -H "Authorization: Bearer $VT2" \
  -H 'Content-Type: application/json' -d '{}' "$A/api/v1/tools/$SLUG_A/download-ticket")"
# 对照组：把 can_download 改回 true，zhangsan 应当能拿到票据。
# 没有这个对照，「下载被拒」无法排除「他本来就没有下载权限」。
curl -sS -X PUT "$A/api/v1/me/tools/$AID/acl" -H "$H" -H 'Content-Type: application/json' \
  -d "{\"visibility\":\"restricted\",\"entries\":[{\"subject_type\":\"user\",\"subject_id\":$VID_U,\"can_download\":true}]}" -o /dev/null
CTRL="$(curl -sS -o /dev/null -w '%{http_code}' -X POST -H "Authorization: Bearer $VT2" \
  -H 'Content-Type: application/json' -d '{}' "$A/api/v1/tools/$SLUG_A/download-ticket")"
chk E14 "ACL can_download 生效：can_download=false 时拒绝、=true 时放行（M6 J-2）" \
  "$([[ "$SEE_OK" == "200" && "$DLGATE" != "200" && "$CTRL" == "200" ]] && echo 0 || echo 1)" \
  "详情 HTTP ${SEE_OK}（应 200）／can_download=false 取票据 HTTP ${DLGATE}（不应 200）／对照组 can_download=true 取票据 HTTP ${CTRL}（应 200）"

# =============================================================================
section "汇总"
# =============================================================================
printf '  PASS=%s  FAIL=%s  BLOCKED=%s\n' "$PASS" "$FAIL" "$BLOCK"
if ((${#FAILED_ITEMS[@]})); then
  echo; echo "  失败项："
  for i in "${FAILED_ITEMS[@]}"; do echo "    - $i"; done
fi
if ((${#BLOCKED_ITEMS[@]})); then
  echo; echo "  未能检查（需目标环境或缺工具）："
  for i in "${BLOCKED_ITEMS[@]}"; do echo "    - $i"; done
fi
echo
echo "  提示：BLOCKED 不算 FAIL；已知未交付项（M6 待做）也计为 FAIL，"
echo "        因为清单的用途是「交付前必须全绿」，它们到期必须消失。"
exit $(( FAIL > 0 ? 1 : 0 ))
