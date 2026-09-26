#!/usr/bin/env bash
# ============================================================
# scripts/disk-alert.sh — 磁盘 / inode / WAL / 备份新鲜度 / 配额水位告警
#
# 用途：在没有专业监控系统时，用定时器（建议每 15 分钟）跑一遍关键水位检查，
#       有问题的写 journal + 落一份 /var/lib/localcraft/ALERT.txt（便于路过的
#       运维一眼看到）+ 可选调用外部告警命令。
#
# 告警方式（可同时使用）:
#   1) stdout → 由 systemd 收集进 journal（默认，始终执行）
#   2) 写 ${DATA_DIR}/ALERT.txt（文件存在 = 有告警；恢复正常时自动删除）
#   3) LOCALCRAFT_ALERT_CMD 指定的命令（例：scripts/alert-webhook.sh）
#
# ── 配额水位阈值与后端**同一口径**（重要） ──────────────────────────────
# 后端 `backend/app/services/settings_service.py` 里：
#     STORAGE_WARNING_DEFAULT_PCT = 85
#     def storage_warning(used_bytes, quota_bytes, threshold_pct):
#         if quota_bytes <= 0:          # 0 表示「不限配额」→ 永不告警
#             return False
#         return used_bytes * 100 >= quota_bytes * threshold_pct
# 本脚本用 shell 复现**完全相同的三条语义**（契约 §18.6：只允许一处口径，
# 另一处必须严格对齐，所以这里连 `<= 0` 短路和 `>=` 边界都照着写）：
#   * 阈值与总量从 SQLite 的 `system_settings` 表读取（`quota.warn_threshold_pct`
#     与 `quota.total_mb`，value 是 JSON 列），读不到时分别回落到 85 与 51200
#     （51200 与后端 stats_service 的兜底一致）；
#   * `quota.total_mb = 0` → quota_bytes = 0 → **永不告警**；
#   * 用整数比较 `used*100 >= quota*pct`，与后端写法逐字对应，避免浮点/取整漂移；
#   * used_bytes 取 `SUM(tool_versions.file_size)`，与后端 stats_service 的
#     `used_bytes` 同一个查询（含历史版本，不按删除状态过滤）。
# 若后端口径变了，这里必须同步改；反之亦然。
#
# 用法:
#   scripts/disk-alert.sh                 # 检查并输出
#   scripts/disk-alert.sh --quiet         # 仅在有告警时输出
#
# 建议的 timer：localcraft-disk-alert.timer（15 分钟一次，本仓库未随包提供该 unit，
# 可用 systemd-run --on-calendar 或 cron 承载，见 docs/05 §10.5）。
#
# 依据: docs/05《部署与运维方案》§10.5（磁盘水位告警）、§9.6（容量监控阈值）
#       contracts/CONTRACT.md §18.6（存储水位口径唯一）
#
# 退出码: 0 = 正常；1 = 有 WARN；2 = 有 CRIT（便于 systemd 标记 failed 并被告警捕获）
# ============================================================
set -uo pipefail

# DATA_DIR / BACKUP_DIR 与 localcraft.env 里的真实变量名一致（docs/05 §5.4）；
# 两者都没设时才用生产默认值，方便在沙箱里演练。
DATA_DIR="${DATA_DIR:-${LOCALCRAFT_DATA_DIR:-/var/lib/localcraft}}"
BACKUP_DIR="${BACKUP_DIR:-${LOCALCRAFT_BACKUP_DIR:-${DATA_DIR}/backups}}"
# DB 路径由 DATABASE_URL 派生（应用只用这一个变量描述数据库位置）。
# `sqlite+aiosqlite:///` 之后 3 个斜杠是相对路径、4 个是绝对路径，
# 两种都靠“去掉这 3 个斜杠”得到正确结果。
case "${DATABASE_URL:-}" in
    sqlite+aiosqlite:///*) _DB_FROM_URL="${DATABASE_URL#sqlite+aiosqlite:///}" ;;
    *) _DB_FROM_URL="" ;;
esac
DB="${_DB_FROM_URL:-${LOCALCRAFT_DB:-${DATA_DIR}/localcraft.db}}"
unset _DB_FROM_URL
ALERT_FILE="${LOCALCRAFT_ALERT_FILE:-${DATA_DIR}/ALERT.txt}"

# 阈值（百分比 / MB / 小时），默认值与 docs/05 §9.6、§10.5 一致
WARN_PCT="${LOCALCRAFT_ALERT_DISK_WARN:-80}"
CRIT_PCT="${LOCALCRAFT_ALERT_DISK_CRIT:-90}"
INODE_WARN_PCT="${LOCALCRAFT_ALERT_INODE_WARN:-80}"
WAL_WARN_MB="${LOCALCRAFT_ALERT_WAL_WARN_MB:-1024}"
#: 备份过期阈值（小时）。48 小时 = 连错过两轮每日备份，一定是链路坏了
BACKUP_STALE_H="${LOCALCRAFT_ALERT_BACKUP_STALE_H:-48}"
#: 数据库体积提示阈值（MB），docs/05 §10.5 用 10240（即 10 GB，接近 SQLite 实践上限）
DB_HINT_MB="${LOCALCRAFT_ALERT_DB_HINT_MB:-10240}"

#: 与后端 settings_service.STORAGE_WARNING_DEFAULT_PCT 对齐的回落值
QUOTA_WARN_DEFAULT_PCT=85
#: 与后端 stats_service 的 quota.total_mb 兜底一致
QUOTA_TOTAL_DEFAULT_MB=51200

ALERT_CMD="${LOCALCRAFT_ALERT_CMD:-}"
SKIP_SYSTEMD="${LOCALCRAFT_SKIP_SYSTEMD:-0}"
QUIET=0

LEVEL=0            # 0=正常 1=警告 2=严重
MSG=()             # 各条告警文本

# `date -Is` 是 GNU 扩展，macOS/BSD 的 date 不认（会打印 "invalid argument 's'"）。
# 与 scripts/precheck.sh 采用同一可移植写法，演练机上也能正常出时间戳。
now_iso() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }

log()  { printf '[disk-alert %s] %s\n' "$(now_iso)" "$*"; }
warn() { printf '[disk-alert][warn] %s\n' "$*" >&2; }

usage() { sed -n '2,44p' "$0"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --quiet|-q) QUIET=1; shift ;;
        -h|--help)  usage; exit 0 ;;
        *) warn "未知参数: $1"; exit 2 ;;
    esac
done

add() {  # add <级别 1|2> <文本>
    local lvl="$1"; shift
    [ "$lvl" -gt "$LEVEL" ] && LEVEL="$lvl"
    local tag="WARN"
    [ "$lvl" -eq 2 ] && tag="CRIT"
    MSG+=("[${tag}] $*")
}

# ------------------------------------------------------------
# 读取 system_settings（value 是 JSON 列）
# ------------------------------------------------------------
read_setting_int() {  # read_setting_int <key> <默认值> → 整数
    local key="$1" default="$2" raw num
    if [ ! -f "$DB" ] || ! command -v sqlite3 >/dev/null 2>&1; then
        echo "$default"; return 0
    fi
    raw="$(sqlite3 "$DB" "SELECT value FROM system_settings WHERE key='${key}';" 2>/dev/null || true)"
    # JSON 列里整数存成 `85`，字符串会带引号（`"85"`）。tr 只留数字，
    # 两种形态都能取到；取不到（空/非数字）就回落默认值。
    num="$(printf '%s' "$raw" | tr -dc '0-9')"
    if [ -z "$num" ]; then
        echo "$default"
    else
        echo "$num"
    fi
}

# ------------------------------------------------------------
# 1) 磁盘使用率（数据分区 / 备份分区 / 日志分区 / 根分区）
# ------------------------------------------------------------
check_fs() {  # check_fs <路径> <标签>
    local path="$1" label="$2" pct avail
    [ -d "$path" ] || return 0
    pct="$(df -P "$path" 2>/dev/null | awk 'NR==2{gsub("%","",$5); print $5}')"
    avail="$(df -Ph "$path" 2>/dev/null | awk 'NR==2{print $4}')"
    case "$pct" in ''|*[!0-9]*) return 0 ;; esac
    if [ "$pct" -ge "$CRIT_PCT" ]; then
        add 2 "${label} 使用率 ${pct}%（可用 ${avail}），已超过严重阈值 ${CRIT_PCT}%"
    elif [ "$pct" -ge "$WARN_PCT" ]; then
        add 1 "${label} 使用率 ${pct}%（可用 ${avail}），已超过告警阈值 ${WARN_PCT}%"
    fi
}

check_fs "$DATA_DIR"   "数据分区(${DATA_DIR})"
check_fs "$BACKUP_DIR" "备份分区(${BACKUP_DIR})"
check_fs "/var/log"    "日志分区(/var/log)"
check_fs "/"           "根分区(/)"

# ------------------------------------------------------------
# 2) inode（小文件爆炸不会体现在空间上，只体现在 inode 上）
# ------------------------------------------------------------
if [ -d "$DATA_DIR" ]; then
    IPCT="$(df -Pi "$DATA_DIR" 2>/dev/null | awk 'NR==2{gsub("%","",$5); print $5}')"
    case "$IPCT" in
        ''|*[!0-9]*) : ;;
        *) [ "$IPCT" -ge "$INODE_WARN_PCT" ] && \
               add 1 "inode 使用率 ${IPCT}%（可能存在大量小文件或未清理的临时目录）" ;;
    esac
fi

# ------------------------------------------------------------
# 3) SQLite WAL 大小
# ------------------------------------------------------------
if [ -f "${DB}-wal" ]; then
    if stat -c %s "${DB}-wal" >/dev/null 2>&1; then
        WAL_BYTES="$(stat -c %s "${DB}-wal")"
    else
        WAL_BYTES="$(stat -f %z "${DB}-wal" 2>/dev/null || echo 0)"
    fi
    WAL_MB=$(( WAL_BYTES / 1024 / 1024 ))
    if [ "$WAL_MB" -ge "$WAL_WARN_MB" ]; then
        add 1 "SQLite WAL 达 ${WAL_MB}MB（>= ${WAL_WARN_MB}MB），说明 checkpoint 未能回收"
        MSG+=("       处置：确认没有长事务/长读连接；低峰期执行 PRAGMA wal_checkpoint(TRUNCATE) 或重启服务")
    fi
fi

# ------------------------------------------------------------
# 4) 数据库体积
# ------------------------------------------------------------
if [ -f "$DB" ]; then
    if stat -c %s "$DB" >/dev/null 2>&1; then
        DB_BYTES="$(stat -c %s "$DB")"
    else
        DB_BYTES="$(stat -f %z "$DB" 2>/dev/null || echo 0)"
    fi
    DB_MB=$(( DB_BYTES / 1024 / 1024 ))
    [ "$DB_MB" -ge "$DB_HINT_MB" ] && \
        add 1 "数据库已达 ${DB_MB}MB，建议归档审计/下载明细，或评估迁移 PostgreSQL（docs/05 §9.5）"
fi

# ------------------------------------------------------------
# 5) 配额水位 —— 与后端 storage_warning() 同一口径
# ------------------------------------------------------------
QUOTA_NOTE="（未检查）"
if [ -f "$DB" ] && command -v sqlite3 >/dev/null 2>&1; then
    QUOTA_TOTAL_MB="$(read_setting_int "quota.total_mb" "$QUOTA_TOTAL_DEFAULT_MB")"
    QUOTA_PCT="$(read_setting_int "quota.warn_threshold_pct" "$QUOTA_WARN_DEFAULT_PCT")"
    # 与后端 SettingSpec(minimum=1, maximum=100) 对齐：越界值按默认处理，
    # 否则 0 会让 `used*100 >= quota*0` 恒真，制造永久假告警。
    if [ "$QUOTA_PCT" -lt 1 ] || [ "$QUOTA_PCT" -gt 100 ]; then
        QUOTA_PCT="$QUOTA_WARN_DEFAULT_PCT"
    fi
    USED_BYTES="$(sqlite3 "$DB" 'SELECT COALESCE(SUM(file_size),0) FROM tool_versions;' 2>/dev/null || true)"
    case "$USED_BYTES" in
        ''|*[!0-9]*) QUOTA_NOTE="（读不到 tool_versions.file_size，跳过；迁移是否已执行？）" ;;
        *)
            QUOTA_BYTES=$(( QUOTA_TOTAL_MB * 1024 * 1024 ))
            if [ "$QUOTA_BYTES" -le 0 ]; then
                # 后端：quota_bytes <= 0（quota.total_mb=0）表示不限配额，永不告警
                QUOTA_NOTE="不限配额（quota.total_mb=0），按后端口径永不告警；已用 $(( USED_BYTES / 1024 / 1024 ))MB"
            else
                UPCT=$(( USED_BYTES * 100 / QUOTA_BYTES ))
                QUOTA_NOTE="已用 $(( USED_BYTES / 1024 / 1024 ))MB / ${QUOTA_TOTAL_MB}MB（${UPCT}%，阈值 ${QUOTA_PCT}%）"
                if [ "$(( USED_BYTES * 100 ))" -ge "$(( QUOTA_BYTES * QUOTA_PCT ))" ]; then
                    add 1 "平台配额水位 ${UPCT}% 已达阈值 ${QUOTA_PCT}%（已用 $(( USED_BYTES / 1024 / 1024 ))MB / ${QUOTA_TOTAL_MB}MB）"
                fi
            fi
            ;;
    esac
fi

# ------------------------------------------------------------
# 6) 备份新鲜度与最近结果
# ------------------------------------------------------------
LATEST_BK="$(find "$BACKUP_DIR/db" -maxdepth 1 \( -name 'localcraft-*.db' -o -name 'localcraft-*.db.gz' \) -print 2>/dev/null | sort | tail -1)"
if [ -z "$LATEST_BK" ]; then
    add 2 "找不到任何数据库备份，备份链路可能从未成功运行（${BACKUP_DIR}/db）"
else
    if stat -c %Y "$LATEST_BK" >/dev/null 2>&1; then
        BK_MTIME="$(stat -c %Y "$LATEST_BK")"
    else
        BK_MTIME="$(stat -f %m "$LATEST_BK" 2>/dev/null || echo 0)"
    fi
    AGE_H=$(( ( $(date +%s) - BK_MTIME ) / 3600 ))
    [ "$AGE_H" -ge "$BACKUP_STALE_H" ] && \
        add 2 "最近备份已过 ${AGE_H} 小时（阈值 ${BACKUP_STALE_H}h），备份可能失败：$(basename "$LATEST_BK")"
fi

if [ "$SKIP_SYSTEMD" != "1" ] && command -v systemctl >/dev/null 2>&1; then
    if systemctl is-failed --quiet localcraft-backup.service 2>/dev/null; then
        add 2 "localcraft-backup.service 处于 failed 状态，请查看 journalctl -t localcraft-backup -n 100"
    fi
    if ! systemctl is-active --quiet localcraft-backup.timer 2>/dev/null; then
        add 1 "localcraft-backup.timer 未在运行，自动备份不会触发"
    fi
    # 主服务
    if ! systemctl is-active --quiet localcraft.service 2>/dev/null; then
        add 2 "localcraft.service 未在运行"
    fi
    NRESTART="$(systemctl show -p NRestarts --value localcraft.service 2>/dev/null || echo 0)"
    case "$NRESTART" in
        ''|*[!0-9]*) : ;;
        *) [ "$NRESTART" -gt 5 ] && add 1 "localcraft.service 累计重启 ${NRESTART} 次，请排查崩溃原因" ;;
    esac
fi

# ------------------------------------------------------------
# 7) 输出与告警
# ------------------------------------------------------------
if [ "$LEVEL" -eq 0 ]; then
    # 正常时清除告警文件，保持"文件存在 = 当前有告警"这一简单语义
    [ -f "$ALERT_FILE" ] && rm -f "$ALERT_FILE" 2>/dev/null
    [ "$QUIET" -eq 1 ] || log "全部检查项正常；配额水位 ${QUOTA_NOTE}"
    exit 0
fi

HOSTNAME_S="$(hostname 2>/dev/null || echo unknown)"
LEVEL_NAME="WARN"
[ "$LEVEL" -eq 2 ] && LEVEL_NAME="CRIT"
SUMMARY="localcraft 告警（主机 ${HOSTNAME_S}，级别 ${LEVEL_NAME}）"

if [ "$QUIET" -eq 0 ]; then
    log "${SUMMARY}"
    printf '%s\n' "${MSG[@]}"
    log "配额水位 ${QUOTA_NOTE}"
fi

# 落盘告警文件，便于"不查监控也能发现"
if [ -d "$DATA_DIR" ] && [ -w "$DATA_DIR" ]; then
    {
        echo "$SUMMARY"
        echo "时间: $(now_iso)"
        echo "主机: ${HOSTNAME_S}"
        echo "配额水位: ${QUOTA_NOTE}"
        echo
        printf '%s\n' "${MSG[@]}"
        echo
        echo "详细排查命令:"
        echo "  df -h; df -i; du -sh ${DATA_DIR}/*"
        echo "  journalctl -u localcraft.service --since '1 hour ago' -p warning"
        echo "  journalctl -t localcraft-backup -n 100"
        echo "  ${LOCALCRAFT_PREFIX:-/opt/localcraft}/scripts/verify-backup.sh"
    } > "$ALERT_FILE" 2>/dev/null || warn "写告警文件失败: ${ALERT_FILE}"
    chmod 0644 "$ALERT_FILE" 2>/dev/null || true
else
    warn "数据目录不存在或不可写，跳过写告警文件: ${ALERT_FILE}"
fi

# 可选外部告警（例如 alert-webhook.sh）。
# LOCALCRAFT_ALERT_CMD 允许带参数，所以按空白切成数组后再执行；
# 不用 eval，也不用无引号展开（那会踩 SC2086 且容易被消息内容注入）。
if [ -n "$ALERT_CMD" ]; then
    ALERT_CMD_ARR=()
    read -r -a ALERT_CMD_ARR <<< "$ALERT_CMD"
    if [ "${#ALERT_CMD_ARR[@]}" -gt 0 ]; then
        PAYLOAD="${SUMMARY}
配额水位: ${QUOTA_NOTE}
$(printf '%s\n' "${MSG[@]}")"
        if ! "${ALERT_CMD_ARR[@]}" "$PAYLOAD"; then
            warn "外部告警命令执行失败: ${ALERT_CMD}"
        fi
    fi
fi

# 退出码：严重时非零，便于 systemd 标记 failed 并被监控发现
[ "$LEVEL" -eq 2 ] && exit 2
exit 1
