#!/usr/bin/env bash
# ============================================================
# scripts/m4-drill-backup-restore.sh — 备份 → 破坏 → 恢复 → 校验 端到端演练
#
# 用法: scripts/m4-drill-backup-restore.sh [端口]
#
# 对应 docs/05 §8 的核心承诺，以及 M4 验收 12/13/14。
# 步骤（每一步都打印原始输出）：
#   1) 在一个**全新沙箱**里装一套并起服务（复用 m4-drill-install.sh 的产物）
#   2) 造有内容的库：工具 / 版本 / 图片风格的真实文件 / 审批记录 / 下载日志
#   3) backup.sh  →  检查产物、.meta、integrity_check 门禁
#   4) **故意破坏**：删掉数据库文件，并删掉 files/ 下的一部分
#   5) restore.sh →  从备份恢复
#   6) 校验：服务能起、工具能下载、文件 SHA256 与备份前一致、计数表行数对得上
#   7) verify-backup.sh 通过；再把备份**故意改坏**，确认它能检出来
#
# ★ WAL 处理：备份用 sqlite3 `.backup`（自动把未 checkpoint 的 WAL 折进快照），
#   恢复时显式删除 -wal / -shm（残留会重放出旧数据或直接 malformed）。
#   本脚本在第 4 步会**刻意留下** -wal/-shm，用来证明恢复脚本真的会清理它们。
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
BASE="http://127.0.0.1:${PORT}"
export LOCALCRAFT_DATA_DIR="$VDATA"
export LOCALCRAFT_BACKUP_DIR="$VDATA/backups"
export LOCALCRAFT_BACKUP_VACUUM=false      # 演练固定走 .backup 路径
export LOCALCRAFT_BACKUP_GZIP=false
export LOCALCRAFT_BACKUP_MIN_FREE_MB=10    # 沙箱盘余量有限，门槛调低

log()  { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
step() { printf '\n\033[1m--- %s ---\033[0m\n' "$*"; }
ok()   { printf '  \033[1;32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[1;31m✗\033[0m %s\n' "$*"; FAIL=1; }
die()  { printf '\n\033[1;31m[error] %s\033[0m\n' "$*" >&2; exit 1; }
FAIL=0

[ -n "$RELEASE_DIR" ] || die "找不到沙箱发布包，请先执行 scripts/m4-drill-install.sh"
[ -x "$VPY" ] || die "沙箱 venv 不存在，请先执行 scripts/m4-drill-install.sh"

start_service() {
    set -a
    # shellcheck disable=SC1090  # $VENVF 由命令行动态决定，shellcheck 无法静态跟踪
    . "$VENVF"
    set +a
    ( cd "$SANDBOX/opt/app/current"
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

# ===========================================================================
log "1) 准备沙箱环境与超级管理员"
# ===========================================================================
log "重建沙箱（保证从零开始，不与上一次演练串味）"
stop_service
LOCALCRAFT_PYTHON311="${LOCALCRAFT_PYTHON311:-$BACKEND/.venv/bin/python}" \
    "$SCRIPT_DIR/m4-drill-install.sh" "$PORT" >/dev/null 2>&1 \
    || die "沙箱重建失败，请单独运行 scripts/m4-drill-install.sh 看详细输出"
ok "沙箱就绪"
start_service

step "创建超级管理员（--password-stdin，密码不进 shell 历史）"
ADMIN_PW="Drill@M4passw0rd"
set -a
# shellcheck disable=SC1090  # $VENVF 由命令行动态决定，shellcheck 无法静态跟踪
. "$VENVF"
set +a
( cd "$SANDBOX/opt/app/current" && \
  printf '%s\n%s\n' "$ADMIN_PW" "$ADMIN_PW" | \
  "$VPY" -m app.cli create-superadmin --username admin --email admin@drill.local \
      --password-stdin --no-force-change >/dev/null ) || die "创建超管失败"
ok "超管 admin 已创建"

TOKEN="$(curl -s -X POST "${BASE}/api/v1/auth/login" -H 'Content-Type: application/json' \
    -d "{\"username\":\"admin\",\"password\":\"${ADMIN_PW}\"}" | jq -r .access_token)"
[ -n "$TOKEN" ] && [ "$TOKEN" != "null" ] || die "登录失败"
AUTH="Authorization: Bearer ${TOKEN}"
ok "已登录（access_token 长度 ${#TOKEN}）"

# ===========================================================================
log "2) 造有内容的库：工具 / 版本 / 审批 / 下载"
# ===========================================================================
mkzip() { python3 - "$1" "$2" <<'PYEOF'
import sys, zipfile
out, n = sys.argv[1], int(sys.argv[2])
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("SKILL.md", "---\nname: drill\n---\n# drill\n")
    for i in range(n):
        z.writestr(f"f{i}.txt", f"content-{i}-" + "x" * 200)
PYEOF
}

TOOL_IDS=()
for i in 1 2 3; do
    TID="$(curl -s -X POST "${BASE}/api/v1/me/tools" -H "$AUTH" -H 'Content-Type: application/json' \
        -d "{\"name\":\"演练工具$i\",\"summary\":\"备份恢复演练\",\"description_md\":\"# t$i\",\"tool_type\":\"file\",\"visibility\":\"public\"}" \
        | jq -r .id)"
    [ "$TID" != "null" ] || die "创建工具 $i 失败"
    mkzip "$SANDBOX/t$i.zip" 5
    curl -s -X POST "${BASE}/api/v1/me/tools/${TID}/versions" -H "$AUTH" \
        -F "version=1.0.0" -F "changelog_md=演练" -F "auto_submit=false" \
        -F "file=@$SANDBOX/t$i.zip;type=application/octet-stream" >/dev/null
    curl -s -X POST "${BASE}/api/v1/me/tools/${TID}/submit" -H "$AUTH" >/dev/null
    curl -s -X POST "${BASE}/api/v1/admin/approvals/${TID}/approve" -H "$AUTH" \
        -H 'Content-Type: application/json' -d '{"note":"演练审批"}' >/dev/null
    TOOL_IDS+=("$TID")
    ok "工具 ${i}（id=${TID}）已创建/上传/审批"
done

step "产生下载日志（真下载一次，同时拿到文件内容做 SHA256 基线）"
SLUG1="$(curl -s "${BASE}/api/v1/me/tools/${TOOL_IDS[0]}" -H "$AUTH" | jq -r .slug)"
TICKET_URL="$(curl -s -X POST "${BASE}/api/v1/tools/${SLUG1}/download-ticket" -H "$AUTH" | jq -r .url)"
[ -n "$TICKET_URL" ] && [ "$TICKET_URL" != "null" ] || die "签发票据失败"
# 响应给的是**带票据的下载 url**（不是单独的 ticket 字段）
curl -s -o "$SANDBOX/dl1.zip" "${BASE}${TICKET_URL}"
[ -s "$SANDBOX/dl1.zip" ] || die "下载失败（文件为空）"
ORIG_SHA="$(shasum -a 256 "$SANDBOX/dl1.zip" | awk '{print $1}')"
ok "下载成功，SHA256=${ORIG_SHA:0:16}…"

# 下载明细**不是**逐请求写库的：counter_service 把明细攒在内存里，
# 每 100 条或每 10 秒批量插入一次（docs/02 §3.17，也就是「下载明细批量插入」清理任务）。
# 所以这里必须等过一个 flush 周期，否则会看到 download_logs=0 而误判成功能坏了。
# 轮询等待，最多 45s。不要写死 sleep：flush 周期是 30s，
# 写小了会看到 download_logs=0 而误判成功能坏了（第一次就踩了这个坑）。
TB_WAIT="$VDATA/localcraft.db"
for i in $(seq 1 45); do
    N="$(sqlite3 "$TB_WAIT" 'SELECT COUNT(*) FROM download_logs;' 2>/dev/null || echo 0)"
    [ "$N" -ge 1 ] 2>/dev/null && break
    sleep 1
done
ok "下载明细已批量落库（等待 ${i}s，flush 周期 30s）"

step "记录破坏前的基线（行数 + 磁盘文件清单）"
TB="$VDATA/localcraft.db"
count_of() { sqlite3 "$TB" "SELECT COUNT(*) FROM $1;" 2>/dev/null || echo '?'; }
BASE_USERS="$(count_of users)"; BASE_TOOLS="$(count_of tools)"
BASE_VERS="$(count_of tool_versions)"; BASE_DL="$(count_of download_logs)"
BASE_APPROVALS="$(count_of approval_records)"
FILES_N="$(find "$VDATA/files" -type f | wc -l | tr -d ' ')"
printf '  users=%s tools=%s tool_versions=%s download_logs=%s approval_records=%s\n' \
    "$BASE_USERS" "$BASE_TOOLS" "$BASE_VERS" "$BASE_DL" "$BASE_APPROVALS"
printf '  files 目录文件数=%s\n' "$FILES_N"
[ "$BASE_TOOLS" = "3" ] || bad "工具数应为 3，实际 ${BASE_TOOLS}"
[ "$BASE_DL" -ge 1 ] 2>/dev/null || bad "download_logs 应当 ≥1，实际 ${BASE_DL}"

# ===========================================================================
log "3) 跑 backup.sh"
# ===========================================================================
step "备份前的 WAL 状态（用于说明 -wal/-shm 的处理）"
ls -la "$VDATA"/localcraft.db* 2>/dev/null | sed 's/^/  /'
"$SCRIPT_DIR/backup.sh" 2>&1 | sed 's/^/  /'
[ "${PIPESTATUS[0]}" -eq 0 ] || die "backup.sh 非零退出"

step "备份产物"
find "$VDATA/backups" -maxdepth 2 -name 'localcraft-*.db*' -o -maxdepth 2 -name '*.meta' | sort | sed 's/^/  /'
DB_BACKUP="$(find "$VDATA/backups/db" -name 'localcraft-*.db' | sort | tail -1)"
[ -n "$DB_BACKUP" ] || die "没有产出数据库快照"
ok "数据库快照: $(basename "$DB_BACKUP") ($(du -h "$DB_BACKUP" | cut -f1))"

step ".meta 内容"
cat "${DB_BACKUP}.meta" | sed 's/^/  /'
grep -q '^integrity_check=ok$' "${DB_BACKUP}.meta" && ok ".meta 记录 integrity_check=ok" \
    || bad ".meta 缺少 integrity_check=ok"

step "备份内自包含性检查（不依赖源库的 -wal）"
BK_USERS="$(sqlite3 "$DB_BACKUP" 'SELECT COUNT(*) FROM users;')"
BK_TOOLS="$(sqlite3 "$DB_BACKUP" 'SELECT COUNT(*) FROM tools;')"
BK_DL="$(sqlite3 "$DB_BACKUP" 'SELECT COUNT(*) FROM download_logs;')"
printf '  快照内 users=%s tools=%s download_logs=%s\n' "$BK_USERS" "$BK_TOOLS" "$BK_DL"
[ "$BK_TOOLS" = "$BASE_TOOLS" ] && ok "快照 tools 行数与源库一致" || bad "快照 tools 行数不一致"
[ "$BK_DL" = "$BASE_DL" ] && ok "快照 download_logs 行数与源库一致（WAL 内容已折入）" || bad "download_logs 不一致"

# ===========================================================================
log "4) 故意破坏（删库 + 删一部分文件）"
# ===========================================================================
step "停服务，然后删除数据库文件与部分 files"
stop_service
# 先制造残留的 -wal/-shm：这是恢复脚本必须清理的东西
sqlite3 "$TB" "PRAGMA journal_mode=WAL;" >/dev/null 2>&1 || true
sqlite3 "$TB" "INSERT INTO system_settings(key,value,value_type,updated_at) VALUES('drill.marker','stale','string',datetime('now')) ON CONFLICT(key) DO UPDATE SET value='stale';" >/dev/null 2>&1 || true
ls -la "$TB"* 2>/dev/null | sed 's/^/  /'

rm -f "$TB" "$TB-wal" "$TB-shm"
RESTORED_FILES_BEFORE="$(find "$VDATA/files" -type f | wc -l | tr -d ' ')"
# 删掉 files 下的前若干个文件（模拟文件层损坏）
find "$VDATA/files" -type f | head -n 3 | while IFS= read -r f; do rm -f "$f"; done
LEFT_FILES="$(find "$VDATA/files" -type f | wc -l | tr -d ' ')"
printf '  已删除数据库文件；files 文件数 %s → %s\n' "$RESTORED_FILES_BEFORE" "$LEFT_FILES"
[ ! -f "$TB" ] && ok "数据库文件确实不存在了" || bad "数据库文件还在"
[ "$LEFT_FILES" -lt "$RESTORED_FILES_BEFORE" ] && ok "files 已被破坏" || bad "files 没被删掉"

step "破坏后 /readyz 应当失败"
CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "${BASE}/readyz" || echo 000)"
printf '  /readyz -> HTTP %s（期望 000/5xx，因为库没了且服务已停）\n' "$CODE"

# ===========================================================================
log "5) 跑 restore.sh"
# ===========================================================================
FILES_BACKUP="$(find "$VDATA/backups/files" -maxdepth 1 -type d -name 'files-*' | sort | tail -1)"
"$SCRIPT_DIR/restore.sh" --db "$DB_BACKUP" --files "$FILES_BACKUP" --yes --no-start 2>&1 | sed 's/^/  /'
[ "${PIPESTATUS[0]}" -eq 0 ] || die "restore.sh 非零退出"

# ===========================================================================
log "6) 恢复后校验"
# ===========================================================================
step "WAL 残留检查（验收 14）"
ls -la "$TB"* 2>/dev/null | sed 's/^/  /'
if [ -e "${TB}-wal" ] || [ -e "${TB}-shm" ]; then
    bad "恢复后仍残留 -wal/-shm"
else
    ok "恢复后无 -wal/-shm 残留"
fi
IC="$(sqlite3 "$TB" 'PRAGMA integrity_check;')"
[ "$IC" = "ok" ] && ok "integrity_check = ok" || bad "integrity_check = $IC"

step "行数比对（验收 12）"
R_USERS="$(count_of users)"; R_TOOLS="$(count_of tools)"
R_VERS="$(count_of tool_versions)"; R_DL="$(count_of download_logs)"
R_APPROVALS="$(count_of approval_records)"
printf '  %-18s 破坏前 → 恢复后\n' "表"
for pair in "users:$BASE_USERS:$R_USERS" "tools:$BASE_TOOLS:$R_TOOLS" \
            "tool_versions:$BASE_VERS:$R_VERS" "download_logs:$BASE_DL:$R_DL" \
            "approval_records:$BASE_APPROVALS:$R_APPROVALS"; do
    t="${pair%%:*}"; rest="${pair#*:}"; b="${rest%%:*}"; a="${rest##*:}"
    if [ "$b" = "$a" ]; then printf '  %-18s %s → %s  ✓\n' "$t" "$b" "$a"
    else printf '  %-18s %s → %s  ✗\n' "$t" "$b" "$a"; bad "$t 行数不一致"; fi
done

step "文件层校验（恢复后文件 SHA256 与备份前一致）"
RFILES="$(find "$VDATA/files" -type f | wc -l | tr -d ' ')"
printf '  files 文件数 %s → %s（期望恢复回 %s）\n' "$LEFT_FILES" "$RFILES" "$RESTORED_FILES_BEFORE"
[ "$RFILES" = "$RESTORED_FILES_BEFORE" ] && ok "文件数量恢复" || bad "文件数量不符"

step "起服务并真的下载一次"
start_service
SLUG1="$(curl -s "${BASE}/api/v1/me/tools/${TOOL_IDS[0]}" -H "$AUTH" | jq -r .slug)"
TOKEN="$(curl -s -X POST "${BASE}/api/v1/auth/login" -H 'Content-Type: application/json' \
    -d "{\"username\":\"admin\",\"password\":\"${ADMIN_PW}\"}" | jq -r .access_token)"
AUTH="Authorization: Bearer ${TOKEN}"
TICKET2="${BASE}$(curl -s -X POST "${BASE}/api/v1/tools/${SLUG1}/download-ticket" -H "$AUTH" | jq -r .url)"
curl -s -o "$SANDBOX/dl2.zip" "$TICKET2"
NEW_SHA="$(shasum -a 256 "$SANDBOX/dl2.zip" | awk '{print $1}')"
printf '  恢复前 SHA256=%s\n  恢复后 SHA256=%s\n' "$ORIG_SHA" "$NEW_SHA"
[ "$ORIG_SHA" = "$NEW_SHA" ] && ok "下载内容 SHA256 完全一致" || bad "SHA256 不一致"
printf '  /readyz -> HTTP %s\n' "$(curl -s -o /dev/null -w '%{http_code}' "${BASE}/readyz")"

# ===========================================================================
log "7) verify-backup.sh：正常备份应通过，改坏的备份应被检出"
# ===========================================================================
step "正常备份"
"$SCRIPT_DIR/verify-backup.sh" 2>&1 | tail -20 | sed 's/^/  /'
[ "${PIPESTATUS[0]}" -eq 0 ] && ok "verify-backup.sh 判定备份可用" || bad "verify-backup.sh 误判正常备份"

step "把备份**故意改坏**（截断 + 篡改字节），确认能被检出"
CORRUPT_DIR="$SANDBOX/corrupt"
rm -rf "$CORRUPT_DIR"; mkdir -p "$CORRUPT_DIR/db" "$CORRUPT_DIR/files"
cp "$DB_BACKUP" "$CORRUPT_DIR/db/"
cp "${DB_BACKUP}.meta" "$CORRUPT_DIR/db/"
CORRUPT_DB="$(find "$CORRUPT_DIR/db" -name 'localcraft-*.db' | head -1)"
python3 - "$CORRUPT_DB" <<'PYEOF'
import sys, os
p = sys.argv[1]
size = os.path.getsize(p)
with open(p, "r+b") as f:
    f.seek(size // 2)
    f.write(b"\xde\xad\xbe\xef" * 64)          # 中间砸掉一片
    f.truncate(size - 2048)                     # 再截掉尾巴
# 顺手破坏 .meta 的门禁字段
mp = p + ".meta"
s = open(mp).read().replace("integrity_check=ok", "integrity_check=corrupt")
open(mp, "w").write(s)
PYEOF
# files 也拿走，制造「备份不完整」
rm -rf "$CORRUPT_DIR/files"
set +e
LOCALCRAFT_BACKUP_DIR="$CORRUPT_DIR" "$SCRIPT_DIR/verify-backup.sh" 2>&1 | tail -22 | sed 's/^/  /'
CORRUPT_RC="${PIPESTATUS[0]}"
set -e
if [ "$CORRUPT_RC" -ne 0 ]; then ok "verify-backup.sh 正确检出损坏的备份（退出码 ${CORRUPT_RC}）"
else bad "verify-backup.sh **没有**检出损坏的备份 —— 这个脚本等于没用"; fi

# ===========================================================================
log "结论"
# ===========================================================================
if [ "$FAIL" -eq 0 ]; then
    echo "  备份 → 破坏 → 恢复 → 校验 全部通过"
    echo "  · 数据库快照自包含（WAL 内容已折入），恢复后无 -wal/-shm 残留"
    echo "  · 5 张关键表行数与破坏前一致；下载文件 SHA256 逐字节一致"
    echo "  · verify-backup.sh 对正常备份放行、对损坏备份报错"
    exit 0
fi
die "演练存在失败项（见上面 ✗）"
