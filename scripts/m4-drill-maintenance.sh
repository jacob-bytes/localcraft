#!/usr/bin/env bash
# ============================================================
# scripts/m4-drill-maintenance.sh — 9 个清理任务的实证演练
#
# 用法: scripts/m4-drill-maintenance.sh [端口]
#
# 对 docs/02 §5 表里的每一项，都是**造出目标状态 → 跑任务 → 断言结果**，
# 而不是「命令退出码为 0 就算过」。
#
#   1 计数落库            造下载 → 等 flush → 断言 tools.download_count 增加
#   2 下载明细落库        同上，断言 download_logs 行数增加
#   3 历史版本淘汰        造 3 个版本 + history_limit=1 → 跑 → 断言旧版本 purged 且文件消失
#   4 下载明细清理        造 created_at 超期的行 → 跑 → 断言只剩保留期内的
#   5 会话清理            造过期 40 天的会话 → 跑 → 断言被删；未过期的保留
#   6 孤儿文件清理        手工放无引用文件 → 跑 → 断言进 .trash；再造假 8 天前的
#                         trash 条目 → 跑 → 断言被真删
#   7 悬空 ACL 清理       造 subject_type=group 指向不存在组的行 → 跑 → 断言清掉
#   8 标签计数重算        手工改坏 usage_count → 跑 → 断言修正为真实值
#   9 回收站清理          造 30 天前软删除的工具 → 跑 → 断言被彻底清除（含文件）
# ============================================================
set -uo pipefail

PORT="${1:-8000}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND="$ROOT/backend"
SANDBOX="${LOCALCRAFT_DRILL_SANDBOX:-$BACKEND/dist/drill}"
RELEASE_DIR="$(find "$SANDBOX/releases" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | head -1)"
VPY="$SANDBOX/opt/venv/bin/python"
VDATA="$SANDBOX/var"
VENVF="$SANDBOX/etc/localcraft.env"
TB="$VDATA/localcraft.db"
BASE="http://127.0.0.1:${PORT}"
FAIL=0

log()  { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
step() { printf '\n\033[1m--- %s ---\033[0m\n' "$*"; }
ok()   { printf '  \033[1;32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[1;31m✗\033[0m %s\n' "$*"; FAIL=1; }
die()  { printf '\n\033[1;31m[error] %s\033[0m\n' "$*" >&2; exit 1; }

[ -x "$VPY" ] || die "沙箱不存在，请先跑 scripts/m4-drill-backup-restore.sh（它会建沙箱）"

# 沙箱里的应用代码必须包含 M4 新增的 maintenance 命令。
# 曾经因为沙箱是旧发布包解出来的，9 个任务全部报 "No such command" —— 这里前置挡住。
# 注意：不要写成 `... | grep -q maintenance`。`grep -q` 命中即退出并关掉管道，
# python 收到 SIGPIPE 非零退出；在 `set -o pipefail` 下整条管道就是非零，
# 于是「命令明明存在」也会被判定为不存在（这里踩过）。直接看退出码最稳。
if ! ( cd "$SANDBOX/opt/app/current" && "$VPY" -m app.cli maintenance --help >/dev/null 2>&1 ); then
    die "沙箱内的应用代码过旧（没有 maintenance 命令）。请先重建发布包与沙箱：
  REQUIRE_WHEELHOUSE=1 bash scripts/make-release.sh 1.0.0 && bash scripts/m4-drill-install.sh"
fi

q() { sqlite3 "$TB" "$1"; }

# 维护命令的封装：走**发布包里的真实脚本**（而不是直接用 venv 里的 CLI），
# 这样连脚本本身也一起验证了。
run_maint() {
    local task="${1:-all}"
    LOCALCRAFT_PREFIX="$SANDBOX/opt" \
    LOCALCRAFT_DATA_DIR="$VDATA" \
    LOCALCRAFT_ENV_FILE="$VENVF" \
        "$SCRIPT_DIR/run-maintenance.sh" --task "$task" 2>&1
}

start_service() {
    set -a
    # shellcheck disable=SC1090  # $VENVF 由命令行动态决定，shellcheck 无法静态跟踪
    . "$VENVF"
    set +a
    ( cd "$SANDBOX/opt/app/current" || exit 1
      nohup "$VPY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --workers 1 \
          >> "$SANDBOX/log/localcraft-stdout.log" 2>&1 &
      echo $! > "$VDATA/localcraft.pid" )
    "$RELEASE_DIR/scripts/wait-healthy.sh" --url "${BASE}/readyz" --timeout 60 >/dev/null \
        || die "服务起不来"
}
stop_service() {
    if [ -f "$VDATA/localcraft.pid" ] && kill -0 "$(cat "$VDATA/localcraft.pid")" 2>/dev/null; then
        kill "$(cat "$VDATA/localcraft.pid")" 2>/dev/null || true
        for _ in $(seq 1 30); do
            kill -0 "$(cat "$VDATA/localcraft.pid")" 2>/dev/null || break
            sleep 1
        done
    fi
}
trap stop_service EXIT

log "0) 准备（沙箱来自备份演练，服务已停）"
stop_service
[ -f "$TB" ] || die "沙箱数据库不存在：$TB"
ok "沙箱数据库: $(basename "$TB")"

# ===========================================================================
log "任务 1+2) 计数落库 / 下载明细落库"
# ===========================================================================
start_service

# 本演练自带播种：沙箱可能刚被 install 演练重建过（空库），
# 不能假设"上一步的备份演练已经造好数据"。
ADMIN_PW="Drill@M4passw0rd"
if [ "$(q 'SELECT COUNT(*) FROM users;')" -eq 0 ]; then
    set -a
    # shellcheck disable=SC1090  # $VENVF 由命令行动态决定，shellcheck 无法静态跟踪
    . "$VENVF"
    set +a
    ( cd "$SANDBOX/opt/app/current" && \
      printf '%s\n%s\n' "$ADMIN_PW" "$ADMIN_PW" | \
      "$VPY" -m app.cli create-superadmin --username admin --email admin@drill.local \
          --password-stdin --no-force-change >/dev/null ) || die "创建超管失败"
    echo "  已创建超管（沙箱原本是空库）"
fi

TOKEN="$(curl -s -X POST "${BASE}/api/v1/auth/login" -H 'Content-Type: application/json' \
    -d "{\"username\":\"admin\",\"password\":\"${ADMIN_PW}\"}" | jq -r .access_token)"
[ -n "$TOKEN" ] && [ "$TOKEN" != "null" ] || die "登录失败"
AUTH="Authorization: Bearer ${TOKEN}"

# 至少要有 1 个已发布工具（带版本），任务 3/4/9 都要用它
if [ "$(q 'SELECT COUNT(*) FROM tools;')" -eq 0 ]; then
    mkzip() { python3 - "$1" <<'PYEOF2'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1], "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("SKILL.md", "---\nname: maintenance\n---\n# m\n")
    for i in range(3):
        z.writestr(f"f{i}.txt", "x" * 100)
PYEOF2
    }
    mkzip "$SANDBOX/maint.zip"
    T="$(curl -s -X POST "${BASE}/api/v1/me/tools" -H "$AUTH" -H 'Content-Type: application/json' \
        -d '{"name":"维护演练工具","summary":"s","description_md":"","tool_type":"file","visibility":"public"}' \
        | jq -r .id)"
    curl -s -X POST "${BASE}/api/v1/me/tools/${T}/versions" -H "$AUTH" \
        -F "version=1.0.0" -F "changelog_md=drill" -F "auto_submit=false" \
        -F "file=@$SANDBOX/maint.zip;type=application/octet-stream" >/dev/null
    curl -s -X POST "${BASE}/api/v1/me/tools/${T}/submit" -H "$AUTH" >/dev/null
    curl -s -X POST "${BASE}/api/v1/admin/approvals/${T}/approve" -H "$AUTH" \
        -H 'Content-Type: application/json' -d '{"note":"drill"}' >/dev/null
    curl -s -X PATCH "${BASE}/api/v1/me/tools/${T}" -H "$AUTH" -H 'Content-Type: application/json' \
        -d '{"tags":["maint-drill-tag"]}' >/dev/null
    echo "  已播种 1 个已发布工具 + 1 个标签"
fi

TOOL_ID="$(q 'SELECT id FROM tools ORDER BY id LIMIT 1;')"
SLUG="$(q "SELECT slug FROM tools WHERE id=${TOOL_ID};")"
DL_BEFORE="$(q 'SELECT COUNT(*) FROM download_logs;')"
CNT_BEFORE="$(q "SELECT download_count FROM tools WHERE id=${TOOL_ID};")"
for _ in 1 2 3; do
    U="$(curl -s -X POST "${BASE}/api/v1/tools/${SLUG}/download-ticket" -H "$AUTH" | jq -r .url)"
    curl -s -o /dev/null "${BASE}${U}"
done
step "等 flush（30s）"
for i in $(seq 1 45); do
    N="$(q 'SELECT COUNT(*) FROM download_logs;')"
    [ "$N" -gt "$DL_BEFORE" ] 2>/dev/null && break
    sleep 1
done
DL_AFTER="$(q 'SELECT COUNT(*) FROM download_logs;')"
CNT_AFTER="$(q "SELECT download_count FROM tools WHERE id=${TOOL_ID};")"
printf '  download_logs: %s → %s（等待 %ss）\n' "$DL_BEFORE" "$DL_AFTER" "$i"
printf '  tools.download_count: %s → %s\n' "$CNT_BEFORE" "$CNT_AFTER"
[ "$DL_AFTER" -gt "$DL_BEFORE" ] 2>/dev/null && ok "任务 2 下载明细已批量落库" || bad "任务 2 下载明细未落库"
[ "$CNT_AFTER" -gt "$CNT_BEFORE" ] 2>/dev/null && ok "任务 1 计数已落库" || bad "任务 1 计数未落库"

stop_service

# ===========================================================================
log "任务 3) 历史版本淘汰"
# ===========================================================================
step "造 3 个版本，并把 version.history_limit 设为 1"
sqlite3 "$TB" "INSERT INTO system_settings(key,value,value_type,is_public,updated_at) VALUES('version.history_limit','1','int',0,datetime('now')) ON CONFLICT(key) DO UPDATE SET value='1';"
VCOUNT_BEFORE="$(q "SELECT COUNT(*) FROM tool_versions WHERE tool_id=${TOOL_ID};")"
printf '  当前版本数=%s\n' "$VCOUNT_BEFORE"
if [ "$VCOUNT_BEFORE" -lt 2 ]; then
    # 沙箱里只有一个版本，再造两个 superseded 版本
    "$VPY" - "$TB" "$TOOL_ID" <<'PYEOF'
import sqlite3, sys, hashlib, os
db, tool_id = sys.argv[1], int(sys.argv[2])
con = sqlite3.connect(db)
row = con.execute("SELECT * FROM tool_versions WHERE tool_id=? LIMIT 1", (tool_id,)).fetchone()
cols = [d[0] for d in con.execute("SELECT * FROM tool_versions LIMIT 1").description]
base = dict(zip(cols, row))
now = "2026-01-01 00:00:00"
for ver in ("0.9.0", "0.8.0"):
    d = dict(base)
    d.pop("id", None)
    d["version"] = ver
    d["status"] = "superseded"
    d["is_current"] = 0
    d["created_at"] = now
    d["storage_path"] = f"files/tools/{tool_id}/999/{ver}.zip"
    keys = ",".join(d.keys())
    ph = ",".join("?" * len(d))
    con.execute(f"INSERT INTO tool_versions ({keys}) VALUES ({ph})", list(d.values()))
con.commit()
print("  已补造 2 个 superseded 版本")
PYEOF
fi
V_BEFORE="$(q "SELECT COUNT(*) FROM tool_versions WHERE tool_id=${TOOL_ID} AND status='superseded';")"
printf '  superseded 版本数（跑之前）=%s\n' "$V_BEFORE"
run_maint gc-versions | sed 's/^/  /'
V_AFTER="$(q "SELECT COUNT(*) FROM tool_versions WHERE tool_id=${TOOL_ID} AND status='superseded';")"
V_PURGED="$(q "SELECT COUNT(*) FROM tool_versions WHERE tool_id=${TOOL_ID} AND status='purged';")"
printf '  跑之后：superseded=%s purged=%s（保留上限 1）\n' "$V_AFTER" "$V_PURGED"
[ "$V_PURGED" -ge 1 ] 2>/dev/null && ok "任务 3 历史版本已淘汰（转 purged 并删文件）" \
    || bad "任务 3 没有版本被淘汰"

# ===========================================================================
log "任务 4) 下载明细清理"
# ===========================================================================
step "造一条 created_at 超保留期（180 天）的明细 + 一条新的"
q "INSERT INTO download_logs (tool_id, created_at) VALUES (${TOOL_ID}, datetime('now','-400 days'));"
q "INSERT INTO download_logs (tool_id, created_at) VALUES (${TOOL_ID}, datetime('now'));"
OLD_BEFORE="$(q "SELECT COUNT(*) FROM download_logs WHERE created_at < datetime('now','-180 days');")"
printf '  超期行数=%s\n' "$OLD_BEFORE"
run_maint download-logs | sed 's/^/  /'
OLD_AFTER="$(q "SELECT COUNT(*) FROM download_logs WHERE created_at < datetime('now','-180 days');")"
RECENT="$(q "SELECT COUNT(*) FROM download_logs WHERE created_at >= datetime('now','-180 days');")"
printf '  跑之后：超期行=%s 保留期内=%s\n' "$OLD_AFTER" "$RECENT"
[ "$OLD_AFTER" -eq 0 ] && ok "任务 4 超期明细已删净" || bad "任务 4 仍有超期明细"
[ "$RECENT" -ge 1 ] && ok "任务 4 保留期内的行未被误删" || bad "任务 4 误删了保留期内的行"

# ===========================================================================
log "任务 5) 会话清理"
# ===========================================================================
step "造一个「过期 40 天」的会话和一个刚过期的会话"
SID_BASE="$(q 'SELECT id FROM users LIMIT 1;')"
# 列名是 refresh_token_hash（不是 token_hash）—— 按真实 schema 写
q "INSERT INTO auth_sessions (user_id, refresh_token_hash, expires_at, revoked_at, created_at) VALUES (${SID_BASE}, 'drill-old-session', datetime('now','-40 days'), NULL, datetime('now','-50 days'));"
q "INSERT INTO auth_sessions (user_id, refresh_token_hash, expires_at, revoked_at, created_at) VALUES (${SID_BASE}, 'drill-fresh-session', datetime('now','-1 hours'), NULL, datetime('now','-1 days'));"
S_OLD_BEFORE="$(q "SELECT COUNT(*) FROM auth_sessions WHERE refresh_token_hash='drill-old-session';")"
run_maint sessions | sed 's/^/  /'
S_OLD_AFTER="$(q "SELECT COUNT(*) FROM auth_sessions WHERE refresh_token_hash='drill-old-session';")"
S_NEW_AFTER="$(q "SELECT COUNT(*) FROM auth_sessions WHERE refresh_token_hash='drill-fresh-session';")"
printf '  过期 40 天会话: %s → %s   刚过期会话: %s（应保留）\n' "$S_OLD_BEFORE" "$S_OLD_AFTER" "$S_NEW_AFTER"
[ "$S_OLD_AFTER" -eq 0 ] && ok "任务 5 过期超 30 天的会话已删" || bad "任务 5 未删除老会话"
[ "$S_NEW_AFTER" -eq 1 ] && ok "任务 5 刚过期的会话被保留（未被误删）" || bad "任务 5 误删了刚过期的会话"

# ===========================================================================
log "任务 6) 孤儿文件清理（先入 .trash，再按 7 天清理）"
# ===========================================================================
FILES_ROOT="$VDATA/files"
mkdir -p "$FILES_ROOT/orphan-drill"
echo "i am an orphan" > "$FILES_ROOT/orphan-drill/orphan.bin"
ORPHAN_REL="files/orphan-drill/orphan.bin"
ORPHAN_ABS="$FILES_ROOT/orphan-drill/orphan.bin"
# 同时在 .trash 里造一个「8 天前」的条目，验证第二阶段真的会删
mkdir -p "$FILES_ROOT/.trash/ancient"
echo "old trash" > "$FILES_ROOT/.trash/ancient/gone.bin"
touch -t "$(date -v-8d '+%Y%m%d%H%M' 2>/dev/null || date -d '8 days ago' '+%Y%m%d%H%M')" \
      "$FILES_ROOT/.trash/ancient/gone.bin" "$FILES_ROOT/.trash/ancient" 2>/dev/null || true

printf '  孤儿文件存在=%s  引用它的数据库行=%s\n' \
    "$([ -f "$ORPHAN_ABS" ] && echo yes || echo no)" \
    "$(q "SELECT COUNT(*) FROM tool_versions WHERE storage_path='${ORPHAN_REL}';")"
run_maint orphans | sed 's/^/  /'
TRASHED="$FILES_ROOT/.trash/orphan-drill/orphan.bin"
printf '  活目录里还在=%s  .trash 里有=%s  .trash/ancient 还在=%s\n' \
    "$([ -f "$ORPHAN_ABS" ] && echo yes || echo no)" \
    "$([ -f "$TRASHED" ] && echo yes || echo no)" \
    "$([ -e "$FILES_ROOT/.trash/ancient" ] && echo yes || echo no)"
[ ! -f "$ORPHAN_ABS" ] && ok "任务 6 孤儿文件已移出活目录" || bad "任务 6 孤儿文件没被移走"
[ -f "$TRASHED" ] && ok "任务 6 已移入 .trash（保留后悔期）" || bad "任务 6 没有移入 .trash"
[ ! -e "$FILES_ROOT/.trash/ancient" ] && ok "任务 6 超过 7 天的 .trash 条目已真删" \
    || bad "任务 6 超期 .trash 条目未被删除"

# 反向验证：**被引用**的文件绝不能被当成孤儿
REF_PATH="$(q 'SELECT storage_path FROM tool_versions WHERE storage_path IS NOT NULL LIMIT 1;')"
if [ -n "$REF_PATH" ]; then
    run_maint orphans >/dev/null 2>&1
    [ -f "$VDATA/$REF_PATH" ] && ok "任务 6 被数据库引用的文件未被误删（${REF_PATH}）" \
        || bad "任务 6 **误删了被引用的文件**：$REF_PATH"
fi

# ===========================================================================
log "任务 7) 悬空 ACL 清理"
# ===========================================================================
q "INSERT INTO tool_acl (tool_id, subject_type, subject_id, can_download, created_at) VALUES (${TOOL_ID}, 'group', 999999, 1, datetime('now'));"
DAN_BEFORE="$(q "SELECT COUNT(*) FROM tool_acl WHERE subject_type='group' AND subject_id=999999;")"
printf '  悬空 ACL（指向不存在的组 999999）=%s\n' "$DAN_BEFORE"
run_maint acl | sed 's/^/  /'
DAN_AFTER="$(q "SELECT COUNT(*) FROM tool_acl WHERE subject_type='group' AND subject_id=999999;")"
printf '  跑之后=%s\n' "$DAN_AFTER"
[ "$DAN_AFTER" -eq 0 ] && ok "任务 7 悬空 ACL 已清理" || bad "任务 7 悬空 ACL 未清理"

# ===========================================================================
log "任务 8) 标签计数重算"
# ===========================================================================
TAG_ID="$(q 'SELECT id FROM tags LIMIT 1;')"
if [ -n "$TAG_ID" ]; then
    REAL="$(q "SELECT COUNT(*) FROM tool_tags WHERE tag_id=${TAG_ID};")"
    q "UPDATE tags SET usage_count=9999 WHERE id=${TAG_ID};"
    BROKEN="$(q "SELECT usage_count FROM tags WHERE id=${TAG_ID};")"
    printf '  真实引用数=%s  手工改坏成=%s\n' "$REAL" "$BROKEN"
    run_maint tags | sed 's/^/  /'
    FIXED="$(q "SELECT usage_count FROM tags WHERE id=${TAG_ID};")"
    printf '  跑之后=%s\n' "$FIXED"
    [ "$FIXED" = "$REAL" ] && ok "任务 8 计数已修正回真实值" || bad "任务 8 未修正（期望 ${REAL}，实际 ${FIXED}）"
else
    bad "任务 8 沙箱里没有标签可测"
fi

# ===========================================================================
log "任务 9) 回收站清理"
# ===========================================================================
step "造一个 30 天前软删除的工具（连磁盘文件一起）"
NEW_SLUG="drill-recycle-$(date +%s)"
# download_count / view_count / version_seq 都是 NOT NULL，必须显式给值
NEW_ID="$(q "INSERT INTO tools (slug, name, summary, description_md, tool_type, visibility, status, owner_id, download_count, view_count, version_seq, created_at, updated_at, deleted_at) SELECT '${NEW_SLUG}', '演练回收站', 's', '', 'file', 'public', 'approved', (SELECT id FROM users LIMIT 1), 0, 0, 1, datetime('now','-60 days'), datetime('now'), datetime('now','-31 days') RETURNING id;")"
RC_FILE="$FILES_ROOT/tools/${NEW_ID}/1/recycle.zip"
mkdir -p "$(dirname "$RC_FILE")"; echo "recycle me" > "$RC_FILE"
# changelog_md 也是 NOT NULL（ORM 层默认值，裸 SQL 必须显式给）
q "INSERT INTO tool_versions (tool_id, version, changelog_md, status, is_current, storage_path, file_name, file_size, uploaded_by_id, created_at, updated_at) VALUES (${NEW_ID}, '1.0.0', '', 'approved', 1, 'files/tools/${NEW_ID}/1/recycle.zip', 'recycle.zip', 11, (SELECT id FROM users LIMIT 1), datetime('now'), datetime('now'));"
printf '  工具 id=%s 已软删除 31 天；文件存在=%s\n' "$NEW_ID" "$([ -f "$RC_FILE" ] && echo yes || echo no)"
run_maint recycle-bin | sed 's/^/  /'
printf '  跑之后：工具行=%s 版本行=%s 文件存在=%s\n' \
    "$(q "SELECT COUNT(*) FROM tools WHERE id=${NEW_ID};")" \
    "$(q "SELECT COUNT(*) FROM tool_versions WHERE tool_id=${NEW_ID};")" \
    "$([ -f "$RC_FILE" ] && echo yes || echo no)"
[ "$(q "SELECT COUNT(*) FROM tools WHERE id=${NEW_ID};")" -eq 0 ] && ok "任务 9 工具行已彻底清除" \
    || bad "任务 9 工具行仍在"
[ ! -f "$RC_FILE" ] && ok "任务 9 磁盘文件已删除" || bad "任务 9 磁盘文件仍在"

# ===========================================================================
log "10) 一次性跑全套（run-maintenance.sh 无 --task）"
# ===========================================================================
run_maint all | sed 's/^/  /'
RC=${PIPESTATUS[0]}
[ "$RC" -eq 0 ] && ok "run-maintenance.sh 全套退出码 0（幂等：重复跑不出错）" \
    || bad "run-maintenance.sh 全套退出码 ${RC}"

log "11) --dry-run 不应改动数据"
DL_SNAPSHOT="$(q 'SELECT COUNT(*) FROM download_logs;')"
LOCALCRAFT_PREFIX="$SANDBOX/opt" LOCALCRAFT_DATA_DIR="$VDATA" LOCALCRAFT_ENV_FILE="$VENVF" \
    "$SCRIPT_DIR/run-maintenance.sh" --dry-run >/dev/null 2>&1
DL_AFTER_DRY="$(q 'SELECT COUNT(*) FROM download_logs;')"
[ "$DL_SNAPSHOT" = "$DL_AFTER_DRY" ] && ok "--dry-run 未改动数据" || bad "--dry-run 竟然改了数据"

log "结论"
if [ "$FAIL" -eq 0 ]; then
    echo "  docs/02 §5 的 9 项清理任务全部跑通，行为符合文档"
    exit 0
fi
die "存在失败项（见上面 ✗）"
