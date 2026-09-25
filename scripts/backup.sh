#!/usr/bin/env bash
# ============================================================
# scripts/backup.sh — localcraft 备份脚本
#
#   1) SQLite **一致性快照**（`.backup`，绝不用 cp —— 见 docs/05 §8.1）
#   2) files 目录 rsync 硬链接增量
#   3) 保留最近 N 份，自动清理过期备份
#   4) 每次备份做 integrity_check，失败即非零退出（供 systemd 判定失败）
#
# 由 localcraft-backup.service（oneshot）+ localcraft-backup.timer 调用，
# 也可手工执行：/opt/localcraft/scripts/backup.sh
#
# 依据: docs/05《部署与运维方案》§8.2 / §8.3
# ============================================================
set -uo pipefail

DATA_DIR="${LOCALCRAFT_DATA_DIR:-/var/lib/localcraft}"
DB_FILE="${LOCALCRAFT_DB:-$DATA_DIR/localcraft.db}"
FILES_DIR="${LOCALCRAFT_FILES_DIR:-$DATA_DIR/files}"
BACKUP_ROOT="${LOCALCRAFT_BACKUP_DIR:-$DATA_DIR/backups}"

DB_DIR="$BACKUP_ROOT/db"
FILES_BACKUP_DIR="$BACKUP_ROOT/files"
LATEST_LINK="$FILES_BACKUP_DIR/latest"

#: 数据库快照保留份数
KEEP_DB="${LOCALCRAFT_BACKUP_KEEP_DB:-14}"
#: 文件快照保留份数
KEEP_FILES="${LOCALCRAFT_BACKUP_KEEP_FILES:-7}"
#: 是否 gzip 数据库快照（gzip 后不能直接被 sqlite3 打开，恢复时脚本会自动解压）
GZIP_DB="${LOCALCRAFT_BACKUP_GZIP:-false}"
#: 备份前要求的最小剩余空间（MB）
MIN_FREE_MB="${LOCALCRAFT_BACKUP_MIN_FREE_MB:-2048}"
#: true / false / auto（auto = 周日或每月 1 日用 VACUUM INTO）
DO_VACUUM="${LOCALCRAFT_BACKUP_VACUUM:-auto}"
#: 可选远程同步（留空 = 不做）
REMOTE="${LOCALCRAFT_BACKUP_REMOTE:-}"

LOCK_FILE="$BACKUP_ROOT/.backup.lock"
LOCK_DIR="$BACKUP_ROOT/.backup.lockdir"

TS="$(date +%Y%m%d-%H%M%S)"
DB_BACKUP="$DB_DIR/localcraft-$TS.db"
FILES_BACKUP="$FILES_BACKUP_DIR/files-$TS"

log()  { printf '[backup] %s\n' "$*"; }
warn() { printf '[backup][warn] %s\n' "$*" >&2; }
die()  { printf '[backup][error] %s\n' "$*" >&2; exit 1; }

# ------------------------------------------------------------
# 0) 前置检查
# ------------------------------------------------------------
command -v sqlite3 >/dev/null 2>&1 || die "缺少 sqlite3 命令"
command -v rsync   >/dev/null 2>&1 || die "缺少 rsync 命令"

[ -f "$DB_FILE" ] || die "数据库文件不存在: $DB_FILE"

mkdir -p "$DB_DIR" "$FILES_BACKUP_DIR" "$BACKUP_ROOT"

# 用 flock 防止两次备份并发执行（例如手工执行时定时器正好触发）。
# macOS 没有 flock（util-linux 专属），退化为 mkdir 原子锁 —— 语义等价。
if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK_FILE" || die "无法打开锁文件 $LOCK_FILE"
    flock -n 9 || die "已有备份在运行（flock 未获取到锁），本次跳过"
else
    if ! mkdir "$LOCK_DIR" 2>/dev/null; then
        die "已有备份在运行（锁目录 $LOCK_DIR 存在），本次跳过"
    fi
    trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT
fi

# 磁盘空间检查：空间不足时提前失败，避免写出半截备份
FREE_MB="$(df -Pk "$BACKUP_ROOT" | awk 'NR==2 {printf "%d", $4/1024}')"
log "备份目录剩余空间 ${FREE_MB} MB（要求 ≥ ${MIN_FREE_MB} MB）"
[ "$FREE_MB" -ge "$MIN_FREE_MB" ] || die "剩余空间不足（${FREE_MB} MB < ${MIN_FREE_MB} MB）"

# 判断是否做 VACUUM 整理
DOW="$(date +%u)"; DOM="$(date +%d)"
VACUUM_NOW=false
case "$DO_VACUUM" in
    true)  VACUUM_NOW=true ;;
    false) VACUUM_NOW=false ;;
    auto)  [ "$DOW" = "7" ] || [ "$DOM" = "01" ] && VACUUM_NOW=true ;;
    *)     warn "LOCALCRAFT_BACKUP_VACUUM 取值无法识别（${DO_VACUUM}），按 auto 处理"
           [ "$DOW" = "7" ] || [ "$DOM" = "01" ] && VACUUM_NOW=true ;;
esac

# ------------------------------------------------------------
# 1) 数据库一致性备份
# ------------------------------------------------------------
# 绝不用 cp：WAL 模式下已提交的数据可能还在 -wal 里，而且 cp 存在竞态。
# `.backup` 走 SQLite Online Backup API，**自动把 WAL 中未 checkpoint 的内容
# 一并折叠进快照**，所以产物是自包含的，不需要（也不应该）单独复制 -wal/-shm。
WAL_SIZE=0
if [ -f "${DB_FILE}-wal" ]; then
    WAL_SIZE="$(wc -c < "${DB_FILE}-wal" | tr -d ' ')"
    log "检测到 ${DB_FILE}-wal（${WAL_SIZE} 字节）—— .backup 会把它一并折进快照"
fi

rm -f "${DB_BACKUP}.tmp"
if [ "$VACUUM_NOW" = "true" ]; then
    log "执行 VACUUM INTO 整理型备份（顺带去碎片）"
    sqlite3 "$DB_FILE" "VACUUM INTO '${DB_BACKUP}.tmp';" || die "VACUUM INTO 失败"
    BACKUP_MODE="vacuum-into"
else
    log "执行 .backup 一致性快照备份"
    sqlite3 "$DB_FILE" ".backup '${DB_BACKUP}.tmp'" || die ".backup 失败"
    BACKUP_MODE="online-backup"
fi

# ------------------------------------------------------------
# 2) 校验刚生成的快照（门禁：不过就直接失败，不留半成品）
# ------------------------------------------------------------
log "校验快照完整性（PRAGMA integrity_check）"
CHECK_RESULT="$(sqlite3 "${DB_BACKUP}.tmp" 'PRAGMA integrity_check;' 2>&1 || true)"
if [ "$CHECK_RESULT" != "ok" ]; then
    rm -f "${DB_BACKUP}.tmp"
    die "备份完整性校验失败: ${CHECK_RESULT}"
fi

USER_CNT="$(sqlite3 "${DB_BACKUP}.tmp" 'SELECT COUNT(*) FROM users;' 2>/dev/null || echo '?')"
TOOL_CNT="$(sqlite3 "${DB_BACKUP}.tmp" 'SELECT COUNT(*) FROM tools;' 2>/dev/null || echo '?')"
VER_CNT="$(sqlite3 "${DB_BACKUP}.tmp" 'SELECT COUNT(*) FROM tool_versions;' 2>/dev/null || echo '?')"
ALEMBIC_REV="$(sqlite3 "${DB_BACKUP}.tmp" 'SELECT version_num FROM alembic_version;' 2>/dev/null || echo unknown)"
# 快照里**不允许**残留 -wal/-shm 语义：.backup 产出的单文件必须自包含
SNAPSHOT_WAL="$(sqlite3 "${DB_BACKUP}.tmp" 'PRAGMA journal_mode;' 2>/dev/null || echo unknown)"

log "快照校验通过：users=${USER_CNT} tools=${TOOL_CNT} versions=${VER_CNT} alembic=${ALEMBIC_REV}"

mv -f "${DB_BACKUP}.tmp" "$DB_BACKUP"

if [ "$GZIP_DB" = "true" ]; then
    log "压缩数据库快照"
    gzip -9 -f "$DB_BACKUP" || die "gzip 压缩失败"
    DB_BACKUP_FINAL="${DB_BACKUP}.gz"
else
    DB_BACKUP_FINAL="$DB_BACKUP"
fi

# 元信息：恢复前先用它确认来源与完整性
cat > "${DB_BACKUP_FINAL}.meta" <<EOF
timestamp=${TS}
source_db=${DB_FILE}
mode=${BACKUP_MODE}
integrity_check=ok
journal_mode=${SNAPSHOT_WAL}
wal_bytes_at_backup=${WAL_SIZE}
users_rows=${USER_CNT}
tools_rows=${TOOL_CNT}
tool_versions_rows=${VER_CNT}
compressed=${GZIP_DB}
host=$(hostname 2>/dev/null || echo unknown)
localcraft_version=$(cat "${LOCALCRAFT_PREFIX:-/opt/localcraft}/app/current/VERSION" 2>/dev/null || echo unknown)
alembic_revision=${ALEMBIC_REV}
EOF

log "数据库备份完成: ${DB_BACKUP_FINAL} ($(du -h "$DB_BACKUP_FINAL" | cut -f1))"

# ------------------------------------------------------------
# 3) files 目录增量备份（硬链接，未变化文件不额外占空间）
# ------------------------------------------------------------
FILES_STATUS="skipped"
if [ -d "$FILES_DIR" ]; then
    log "同步 files 目录（--link-dest 硬链接增量）"
    LINK_ARGS=()
    if [ -e "$LATEST_LINK" ]; then
        LINK_ARGS=(--link-dest="$LATEST_LINK")
    fi
    mkdir -p "$FILES_BACKUP"
    # 空数组在 `set -u` + bash 3.2（macOS 自带）下用 "${arr[@]}" 会报
    # unbound variable；`${arr[@]+"${arr[@]}"}` 是同时兼容 3.2 与 4.4+ 的写法。
    if rsync -a --delete ${LINK_ARGS[@]+"${LINK_ARGS[@]}"} "$FILES_DIR/" "$FILES_BACKUP/"; then
        ln -sfn "$(basename "$FILES_BACKUP")" "$LATEST_LINK"
        FILES_STATUS="ok"
        log "文件备份完成: $FILES_BACKUP"
    else
        FILES_STATUS="failed"
        warn "files 目录同步失败（数据库备份仍然有效）"
    fi
else
    warn "files 目录不存在，跳过: $FILES_DIR"
fi

# ------------------------------------------------------------
# 4) 清理过期备份
# ------------------------------------------------------------
prune() {  # prune <目录> <前缀> <保留份数>
    local dir="$1" pattern="$2" keep="$3"
    local victims
    victims="$(find "$dir" -maxdepth 1 -name "$pattern" -print | sort -r | tail -n "+$((keep + 1))")"
    [ -z "$victims" ] && return 0
    printf '%s\n' "$victims" | while IFS= read -r v; do
        rm -rf "$v" "${v}.meta"
        log "清理过期备份: $(basename "$v")"
    done
}
prune "$DB_DIR" 'localcraft-*.db' "$KEEP_DB"
prune "$DB_DIR" 'localcraft-*.db.gz' "$KEEP_DB"
if [ "$KEEP_FILES" -gt 0 ]; then
    # 文件快照是目录，单独清理（保留 latest 软链指向的那一份）
    find "$FILES_BACKUP_DIR" -maxdepth 1 -type d -name 'files-*' -print | sort -r \
        | tail -n "+$((KEEP_FILES + 1))" | while IFS= read -r d; do
            rm -rf "$d"
            log "清理过期文件快照: $(basename "$d")"
        done
fi

# ------------------------------------------------------------
# 5) 可选远程同步
# ------------------------------------------------------------
if [ -n "$REMOTE" ]; then
    log "同步到远程: $REMOTE"
    rsync -a "$BACKUP_ROOT/" "$REMOTE/" || warn "远程同步失败（本地备份仍然有效）"
fi

# ------------------------------------------------------------
# 6) 汇总
# ------------------------------------------------------------
DB_COUNT="$(find "$DB_DIR" -maxdepth 1 \( -name 'localcraft-*.db' -o -name 'localcraft-*.db.gz' \) | wc -l | tr -d ' ')"
log "汇总: 数据库快照 ${DB_COUNT} 份（保留 ${KEEP_DB}）  文件快照状态 ${FILES_STATUS}  users=${USER_CNT}"

[ "$FILES_STATUS" = "failed" ] && exit 1
exit 0
