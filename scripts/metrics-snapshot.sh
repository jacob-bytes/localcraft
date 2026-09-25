#!/usr/bin/env bash
# ============================================================
# scripts/metrics-snapshot.sh — 应用侧关键指标快照
#
# 用途：没有 Prometheus / node_exporter 时，定期把一份运行摘要写进 journal，
#       便于事后回溯"出事那天服务是什么状态"。默认同时打印到 stdout。
#
# 指标口径严格对齐 docs/05《部署与运维方案》§10.4 的清单，
# 且**每一项都来自真实可取的来源**（不编造 Prometheus 指标名、不估算）：
#   /healthz、/readyz、/api/v1/meta  → HTTP 码与关键字段
#   selftool.db / -wal / -shm        → 文件大小
#   files/                           → 占用与文件数
#   backups/                         → 快照份数、最近一份的时间与年龄
#   df / df -i                       → 磁盘与 inode 使用率
#   sqlite3 直查                     → users / tools / tool_versions 计数
#   journalctl                       → 当日 5xx 与 "database is locked" 条数
#
# 用法:
#   metrics-snapshot.sh                # 打印到 stdout
#   metrics-snapshot.sh --journal      # 同时写入 journal（logger -t selftool-metrics）
#   metrics-snapshot.sh --base-url http://127.0.0.1:8000
#
# 建议：手工执行，或挂一个每日一次的 timer；输出可用
#       journalctl -t selftool-metrics --since today 回看。
#
# 依据: docs/05《部署与运维方案》§10.4（应用侧关键指标）、§10.2（journald 查询）
# ============================================================
set -uo pipefail

PREFIX="${SELTOOL_PREFIX:-/opt/selftool}"
DATA_DIR="${SELTOOL_DATA_DIR:-/var/lib/selftool}"
DB_FILE="${SELTOOL_DB:-$DATA_DIR/selftool.db}"
FILES_DIR="${SELTOOL_FILES_DIR:-$DATA_DIR/files}"
BACKUP_ROOT="${SELTOOL_BACKUP_DIR:-$DATA_DIR/backups}"
APP_LINK="$PREFIX/app/current"
SKIP_SYSTEMD="${SELTOOL_SKIP_SYSTEMD:-0}"

BASE_URL="${SELTOOL_BASE_URL:-http://127.0.0.1:${SELTOOL_PORT:-8000}}"
USE_JOURNAL=0
TAG="selftool-metrics"

log() { printf '[metrics] %s\n' "$*" >&2; }

while [ $# -gt 0 ]; do
    case "$1" in
        --journal)   USE_JOURNAL=1; shift ;;
        --base-url)  BASE_URL="$2"; shift 2 ;;
        --base-url=*) BASE_URL="${1#--base-url=}"; shift ;;
        -h|--help)   sed -n '2,30p' "$0"; exit 0 ;;
        *) log "未知参数: $1"; exit 2 ;;
    esac
done

now_iso() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }

# 人类可读大小：优先 du -h（拿不到就回落到字节数）
human_size() {  # human_size <路径>
    local p="$1"
    if [ -e "$p" ]; then
        du -sh "$p" 2>/dev/null | cut -f1
    else
        echo "(不存在)"
    fi
}
byte_size() {  # byte_size <文件>  -> 字节，取不到输出 0
    [ -f "$1" ] || { echo 0; return 0; }
    if stat -c %s "$1" >/dev/null 2>&1; then
        stat -c %s "$1"
    else
        stat -f %z "$1" 2>/dev/null || echo 0
    fi
}
mtime_epoch() {  # mtime_epoch <文件>
    if stat -c %Y "$1" >/dev/null 2>&1; then
        stat -c %Y "$1"
    else
        stat -f %m "$1" 2>/dev/null || echo 0
    fi
}
http_code() {  # http_code <url>
    # curl 在连接失败时**仍会**按 -w 输出 000 并以非零退出；若再 `|| echo 000`
    # 就会得到 "000000"。这里改成：失败时把结果清空，再统一兜底成 000。
    local code=""
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$1" 2>/dev/null)" || code=""
    echo "${code:-000}"
}
http_body() {  # http_body <url>
    curl -sS --max-time 5 "$1" 2>/dev/null || true
}
sqlite_count() {  # sqlite_count <表名>；表不存在时输出 ?
    [ -f "$DB_FILE" ] || { echo "(无数据库)"; return 0; }
    sqlite3 "$DB_FILE" "SELECT COUNT(*) FROM ${1};" 2>/dev/null || echo "?"
}

command -v curl >/dev/null 2>&1 || log "警告：缺少 curl，探针与 meta 指标会显示 000"

TMP="$(mktemp "${TMPDIR:-/tmp}/selftool-metrics-XXXXXX")" || exit 1
trap 'rm -f "$TMP"' EXIT

{
    echo "=== selftool 运行摘要 $(now_iso) ==="

    # ---------- 基础 ----------
    echo "[基础]"
    echo "  主机         : $(hostname 2>/dev/null || echo unknown)"
    echo "  架构         : $(uname -m)"
    echo "  部署版本     : $(cat "$APP_LINK/VERSION" 2>/dev/null || echo unknown)"
    echo "  代码目录     : $(readlink -f "$APP_LINK" 2>/dev/null || echo unknown)"
    echo "  采集基址     : ${BASE_URL}"

    # ---------- 服务状态 ----------
    echo "[服务]"
    if [ "$SKIP_SYSTEMD" = "1" ] || ! command -v systemctl >/dev/null 2>&1; then
        echo "  systemd      : （跳过：无 systemd 或 SELTOOL_SKIP_SYSTEMD=1）"
    else
        echo "  systemd 状态 : $(systemctl is-active selftool.service 2>/dev/null || echo unknown)"
        echo "  重启次数     : $(systemctl show -p NRestarts --value selftool.service 2>/dev/null || echo unknown)"
        echo "  备份定时器   : $(systemctl is-active selftool-backup.timer 2>/dev/null || echo unknown)"
        echo "  清理定时器   : $(systemctl is-active selftool-maintenance.timer 2>/dev/null || echo unknown)"
    fi

    # ---------- 探针 ----------
    echo "[探针]"
    echo "  /healthz     : HTTP $(http_code "${BASE_URL}/healthz")  $(http_body "${BASE_URL}/healthz" | head -c 200)"
    echo "  /readyz      : HTTP $(http_code "${BASE_URL}/readyz")"
    META_BODY="$(http_body "${BASE_URL}/api/v1/meta")"
    echo "  /api/v1/meta : HTTP $(http_code "${BASE_URL}/api/v1/meta")"
    if [ -n "$META_BODY" ]; then
        if command -v jq >/dev/null 2>&1; then
            # 只取运维关心的几个字段，避免把整个 meta 灌进 journal
            printf '%s\n' "$META_BODY" | jq -c '{version, site_name, features}' 2>/dev/null \
                | sed 's/^/    meta: /' || echo "    meta: （JSON 解析失败）"
        else
            printf '    meta(原始): %s\n' "$(printf '%s' "$META_BODY" | head -c 300)"
        fi
    fi

    # ---------- 存储 ----------
    echo "[存储]"
    echo "  数据库       : $(human_size "$DB_FILE")  ($(byte_size "$DB_FILE") 字节)"
    if [ -f "${DB_FILE}-wal" ]; then
        # 该指标是"checkpoint 是否正常回收"的探针：持续巨大说明有长事务/长读连接
        echo "  WAL          : $(human_size "${DB_FILE}-wal")  ($(byte_size "${DB_FILE}-wal") 字节)"
    else
        echo "  WAL          : (无 -wal 文件)"
    fi
    if [ -f "${DB_FILE}-shm" ]; then
        echo "  SHM          : $(human_size "${DB_FILE}-shm")  ($(byte_size "${DB_FILE}-shm") 字节)"
    else
        echo "  SHM          : (无 -shm 文件)"
    fi
    echo "  files/ 占用  : $(human_size "$FILES_DIR")"
    echo "  files/ 文件数: $(find "$FILES_DIR" -type f 2>/dev/null | wc -l | tr -d ' ')"
    echo "  backups/ 占用: $(human_size "$BACKUP_ROOT")"
    DB_SNAP_COUNT="$(find "$BACKUP_ROOT/db" -maxdepth 1 \( -name 'selftool-*.db' -o -name 'selftool-*.db.gz' \) 2>/dev/null | wc -l | tr -d ' ')"
    echo "  数据库快照数 : ${DB_SNAP_COUNT}"
    LATEST_BK="$(find "$BACKUP_ROOT/db" -maxdepth 1 \( -name 'selftool-*.db' -o -name 'selftool-*.db.gz' \) -print 2>/dev/null | sort | tail -1)"
    if [ -n "$LATEST_BK" ]; then
        AGE_H=$(( ( $(date +%s) - $(mtime_epoch "$LATEST_BK") ) / 3600 ))
        echo "  最近备份     : $(basename "$LATEST_BK")  （${AGE_H} 小时前）"
    else
        echo "  最近备份     : (无备份！)"
    fi
    if [ -d "$DATA_DIR" ]; then
        echo "  数据分区     : $(df -Ph "$DATA_DIR" 2>/dev/null | awk 'NR==2{print $5" 已用 / "$2" 总量 / "$4" 可用"}')"
        echo "  数据分区 inode: $(df -Pi "$DATA_DIR" 2>/dev/null | awk 'NR==2{print $5" 已用"}')"
    fi

    # ---------- 数据规模 ----------
    echo "[数据]"
    echo "  users        : $(sqlite_count users)"
    echo "  tools        : $(sqlite_count tools)"
    echo "  tool_versions: $(sqlite_count tool_versions)"
    echo "  回收站工具数 : $(sqlite3 "$DB_FILE" 'SELECT COUNT(*) FROM tools WHERE deleted_at IS NOT NULL;' 2>/dev/null || echo '?')"

    # ---------- 日志派生指标 ----------
    echo "[日志（今日）]"
    if command -v journalctl >/dev/null 2>&1; then
        C5XX="$(journalctl -u selftool.service --since today --no-pager 2>/dev/null | grep -cE '" 5[0-9]{2} ' || true)"
        LOCKED="$(journalctl -u selftool.service --since today --no-pager 2>/dev/null | grep -ci 'database is locked' || true)"
        UNZIP="$(journalctl -u selftool.service --since today --no-pager 2>/dev/null | grep -ciE 'zip bomb|path traversal' || true)"
        echo "  5xx 行数     : ${C5XX:-0}"
        echo "  database is locked: ${LOCKED:-0}"
        echo "  解压拦截     : ${UNZIP:-0}"
    else
        echo "  （无 journalctl）"
    fi

    echo "=== 摘要结束 ==="
} > "$TMP" 2>&1

cat "$TMP"

if [ "$USE_JOURNAL" -eq 1 ]; then
    if command -v logger >/dev/null 2>&1; then
        logger -t "$TAG" -- "$(cat "$TMP")"
        log "已写入 journal（journalctl -t ${TAG} --since today）"
    else
        log "缺少 logger 命令，未写入 journal（内容已打印到 stdout）"
    fi
fi

exit 0
