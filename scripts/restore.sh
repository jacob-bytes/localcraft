#!/usr/bin/env bash
# ============================================================
# scripts/restore.sh — localcraft 恢复脚本
#   停服务 → 备份现状 → 还原数据库 → 还原 files → 校验 → 起服务
#
# 用法:
#   restore.sh --db /var/lib/localcraft/backups/db/localcraft-20250101-030000.db.gz \
#              --files /var/lib/localcraft/backups/files/files-20250101-030000
#   restore.sh --latest                # 使用最近一份备份
#   restore.sh --db <路径> --yes       # 跳过交互确认（供演练脚本使用）
#
# 依据: docs/05《部署与运维方案》§8.5
#
# 三条关键点（都在脚本里显式实现，不是注释摆设）:
#   1) 恢复前**必须删除 -wal 与 -shm**。残留的 WAL 属于旧库，与新库文件不匹配，
#      SQLite 会把它当成新库的 WAL 去重放 —— 轻则读到旧数据，重则
#      "database disk image is malformed"。
#   2) 动手前先把**现状**搬到 rollback 目录，恢复错了还能退回去。
#   3) 恢复后必须做 `PRAGMA integrity_check` 与行数比对，不通过就不起服务。
# ============================================================
set -uo pipefail

DATA_DIR="${LOCALCRAFT_DATA_DIR:-/var/lib/localcraft}"
DB_FILE="${LOCALCRAFT_DB:-$DATA_DIR/localcraft.db}"
FILES_DIR="${LOCALCRAFT_FILES_DIR:-$DATA_DIR/files}"
BACKUP_ROOT="${LOCALCRAFT_BACKUP_DIR:-$DATA_DIR/backups}"
DB_DIR="$BACKUP_ROOT/db"
FILES_BACKUP_DIR="$BACKUP_ROOT/files"

DB_ARG=""
FILES_ARG=""
USE_LATEST=0
ASSUME_YES=0
START_AFTER=1

log()  { printf '[restore] %s\n' "$*"; }
warn() { printf '[restore][warn] %s\n' "$*" >&2; }
die()  { printf '[restore][error] %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --db)        DB_ARG="$2"; shift 2 ;;
        --files)     FILES_ARG="$2"; shift 2 ;;
        --latest)    USE_LATEST=1; shift ;;
        --yes|-y)    ASSUME_YES=1; shift ;;
        --no-start)  START_AFTER=0; shift ;;
        -h|--help)   sed -n '2,20p' "$0"; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

command -v sqlite3 >/dev/null 2>&1 || die "缺少 sqlite3 命令"

if [ "$USE_LATEST" -eq 1 ]; then
    DB_ARG="$(find "$DB_DIR" -maxdepth 1 \( -name 'localcraft-*.db' -o -name 'localcraft-*.db.gz' \) -print 2>/dev/null | sort | tail -1)"
    FILES_ARG="$(find "$FILES_BACKUP_DIR" -maxdepth 1 -type d -name 'files-*' -print 2>/dev/null | sort | tail -1)"
fi

[ -n "$DB_ARG" ] || die "未指定数据库备份（--db <路径> 或 --latest）"
[ -f "$DB_ARG" ] || die "数据库备份不存在: $DB_ARG"

# ------------------------------------------------------------
# 1) 二次确认
# ------------------------------------------------------------
printf '\n即将执行恢复：\n'
printf '  数据库备份 : %s\n' "$DB_ARG"
printf '  文件备份   : %s\n' "${FILES_ARG:-（不恢复 files）}"
printf '  目标数据库 : %s\n' "$DB_FILE"
printf '  目标文件区 : %s\n' "$FILES_DIR"
printf '\n\033[1;33m这会覆盖当前数据。\033[0m\n'
if [ "$ASSUME_YES" -ne 1 ]; then
    printf '确认请输入 restore：'
    read -r ANSWER
    [ "$ANSWER" = "restore" ] || die "已取消"
fi

# ------------------------------------------------------------
# 2) 校验备份，先确认它是可用的再动手
# ------------------------------------------------------------
log "校验备份可用性"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/localcraft-restore-XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

DB_SRC="$DB_ARG"
case "$DB_ARG" in
    *.gz)
        log "解压 gzip 快照"
        gzip -dc "$DB_ARG" > "$WORK/restore.db" || die "解压失败（备份可能损坏）"
        DB_SRC="$WORK/restore.db"
        ;;
    *)  cp "$DB_ARG" "$WORK/restore.db"; DB_SRC="$WORK/restore.db" ;;
esac

CHECK="$(sqlite3 "$DB_SRC" 'PRAGMA integrity_check;' 2>&1 || true)"
[ "$CHECK" = "ok" ] || die "备份 integrity_check 未通过: $CHECK"
EXP_USERS="$(sqlite3 "$DB_SRC" 'SELECT COUNT(*) FROM users;' 2>/dev/null || echo '?')"
EXP_TOOLS="$(sqlite3 "$DB_SRC" 'SELECT COUNT(*) FROM tools;' 2>/dev/null || echo '?')"
EXP_VERS="$(sqlite3 "$DB_SRC" 'SELECT COUNT(*) FROM tool_versions;' 2>/dev/null || echo '?')"
log "备份校验通过：users=${EXP_USERS} tools=${EXP_TOOLS} versions=${EXP_VERS}"

# ------------------------------------------------------------
# 3) 停服务
# ------------------------------------------------------------
SERVICE_STOPPED=0
PID_FILE="$DATA_DIR/localcraft.pid"
if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet localcraft.service 2>/dev/null; then
    log "停止 localcraft.service"
    systemctl stop localcraft.service
    SERVICE_STOPPED=1
elif [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    log "停止演练模式进程（PID $(cat "$PID_FILE")）"
    kill "$(cat "$PID_FILE")" 2>/dev/null || true
    for _ in $(seq 1 30); do
        kill -0 "$(cat "$PID_FILE")" 2>/dev/null || break
        sleep 1
    done
    SERVICE_STOPPED=1
else
    warn "服务没有在运行；继续恢复"
fi

if [ "$SERVICE_STOPPED" -eq 1 ]; then
    # 确认进程确实退出，避免文件句柄仍被占用
    sleep 1
fi

# ------------------------------------------------------------
# 4) 保留现状（可回退到恢复前的状态）
# ------------------------------------------------------------
ROLLBACK="$BACKUP_ROOT/pre-restore-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$ROLLBACK"
if [ -f "$DB_FILE" ]; then
    cp -a "$DB_FILE" "$ROLLBACK/" 2>/dev/null || warn "备份现状数据库失败"
    log "已保留恢复前数据库到 $ROLLBACK"
fi
if [ -d "$FILES_DIR" ]; then
    mv "$FILES_DIR" "$ROLLBACK/files" 2>/dev/null && log "已保留恢复前 files 到 $ROLLBACK/files"
fi

# ------------------------------------------------------------
# 5) 还原数据库
# ------------------------------------------------------------
log "还原数据库"
mkdir -p "$(dirname "$DB_FILE")"
cp "$DB_SRC" "$DB_FILE"

# ★ 清理旧的 -wal / -shm —— 这一步漏了就会恢复出一个损坏的库。
#   残留的 WAL 属于**旧**库；SQLite 打开新库文件时会把它当成新库的 WAL 重放。
for suffix in -wal -shm; do
    if [ -e "${DB_FILE}${suffix}" ]; then
        rm -f "${DB_FILE}${suffix}"
        log "已清理残留的 ${DB_FILE}${suffix}"
    fi
done

# ------------------------------------------------------------
# 6) 还原 files
# ------------------------------------------------------------
if [ -n "$FILES_ARG" ] && [ -d "$FILES_ARG" ]; then
    log "还原 files 目录"
    mkdir -p "$FILES_DIR"
    if command -v rsync >/dev/null 2>&1; then
        rsync -a "$FILES_ARG/" "$FILES_DIR/" || die "files 还原失败"
    else
        cp -a "$FILES_ARG/." "$FILES_DIR/" || die "files 还原失败"
    fi
    RESTORED_FILES="$(find "$FILES_DIR" -type f | wc -l | tr -d ' ')"
    log "files 还原完成（${RESTORED_FILES} 个文件）"
else
    RESTORED_FILES=0
    warn "未提供 files 备份，跳过文件还原（数据库记录可能指向不存在的文件）"
fi

# ------------------------------------------------------------
# 7) 校验与起服务
# ------------------------------------------------------------
log "恢复后校验"
CHECK2="$(sqlite3 "$DB_FILE" 'PRAGMA integrity_check;' 2>&1 || true)"
[ "$CHECK2" = "ok" ] || die "恢复后 integrity_check 未通过: $CHECK2"

GOT_USERS="$(sqlite3 "$DB_FILE" 'SELECT COUNT(*) FROM users;' 2>/dev/null || echo '?')"
GOT_TOOLS="$(sqlite3 "$DB_FILE" 'SELECT COUNT(*) FROM tools;' 2>/dev/null || echo '?')"
GOT_VERS="$(sqlite3 "$DB_FILE" 'SELECT COUNT(*) FROM tool_versions;' 2>/dev/null || echo '?')"
log "行数比对：users ${EXP_USERS}→${GOT_USERS}  tools ${EXP_TOOLS}→${GOT_TOOLS}  versions ${EXP_VERS}→${GOT_VERS}"
[ "$EXP_USERS" = "$GOT_USERS" ] || die "users 行数不一致，恢复可能不完整"
[ "$EXP_TOOLS" = "$GOT_TOOLS" ] || die "tools 行数不一致，恢复可能不完整"
[ "$EXP_VERS"  = "$GOT_VERS"  ] || die "tool_versions 行数不一致，恢复可能不完整"

# 恢复后不应存在 -wal / -shm（服务未启动，还轮不到它建）
for suffix in -wal -shm; do
    [ -e "${DB_FILE}${suffix}" ] && warn "恢复后仍存在 ${DB_FILE}${suffix}（服务启动后会重建，属正常）"
done

if [ "$START_AFTER" -eq 1 ]; then
    if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files 2>/dev/null | grep -q '^localcraft.service'; then
        log "启动 localcraft.service"
        systemctl start localcraft.service
    else
        warn "未找到 systemd 单元，请手工启动服务后再验证 /readyz"
    fi
fi

cat <<EOF

恢复完成。
  数据库 : $DB_FILE
  文件区 : ${FILES_DIR}（${RESTORED_FILES} 个文件）
  回退点 : $ROLLBACK

建议紧接着做三件事：
  1) scripts/verify-backup.sh            # 确认备份体系本身可用
  2) curl -sS http://127.0.0.1:8000/readyz
  3) 抽查一个工具的下载，确认文件与记录对得上
EOF
