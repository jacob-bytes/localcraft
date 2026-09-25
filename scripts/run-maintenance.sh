#!/usr/bin/env bash
# ============================================================
# scripts/run-maintenance.sh — 一次性触发全部数据保留与清理任务
#
# 用法:
#   run-maintenance.sh                 # 跑全套
#   run-maintenance.sh --task tags     # 只跑某一项
#   run-maintenance.sh --dry-run       # 只统计不落库
#   run-maintenance.sh --json          # 输出 JSON（供监控采集）
#
# 由 localcraft-maintenance.service（oneshot）+ localcraft-maintenance.timer 调用，
# 也可手工执行。实际逻辑在应用侧：`python -m app.cli maintenance`
# （docs/02 §5 那张表的 9 项，全部幂等 + 分批）。
#
# 依据: docs/02《数据模型设计》§5
# ============================================================
set -uo pipefail

# 路径与生产落位一致（docs/05 §4.2）；可用环境变量覆盖以便演练
PREFIX="${LOCALCRAFT_PREFIX:-/opt/localcraft}"
DATA_DIR="${LOCALCRAFT_DATA_DIR:-/var/lib/localcraft}"
ENV_FILE="${LOCALCRAFT_ENV_FILE:-/etc/localcraft/localcraft.env}"
APP_DIR="$PREFIX/app/current"
VENV="$PREFIX/venv"

TASK="all"
DRY_RUN=""
JSON_OUT=""

while [ $# -gt 0 ]; do
    case "$1" in
        --task)     TASK="$2"; shift 2 ;;
        --task=*)   TASK="${1#--task=}"; shift ;;
        --dry-run)  DRY_RUN="--dry-run"; shift ;;
        --json)     JSON_OUT="--json"; shift ;;
        -h|--help)  sed -n '2,18p' "$0"; exit 0 ;;
        *) echo "未知参数: $1" >&2; exit 2 ;;
    esac
done

log()  { printf '[maintenance] %s\n' "$*"; }
die()  { printf '[maintenance][error] %s\n' "$*" >&2; exit 1; }

[ -x "$VENV/bin/python" ] || die "找不到 venv：$VENV/bin/python（用 LOCALCRAFT_PREFIX 覆盖）"
[ -d "$APP_DIR" ]         || die "找不到应用目录：$APP_DIR"
[ -f "$ENV_FILE" ]        || die "找不到环境变量文件：${ENV_FILE}（用 LOCALCRAFT_ENV_FILE 覆盖）"

log "任务=${TASK} dry_run=${DRY_RUN:-false} 应用=${APP_DIR}"

# 与 systemd 的 EnvironmentFile 等价：把 env 文件里的变量导进来
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

# 数据目录以 env 文件为准（生产固定绝对路径）
DATA_DIR="${DATA_DIR:-$DATA_DIR}"

# 孤儿文件清理要删磁盘文件，先确认数据目录可写，否则会报一堆假失败
[ -w "$DATA_DIR" ] || log "警告：$DATA_DIR 不可写，孤儿文件清理会失败（其余任务不受影响）"

cd "$APP_DIR" || die "无法进入 $APP_DIR"

START="$(date +%s)"
if [ -n "$JSON_OUT" ]; then
    "$VENV/bin/python" -m app.cli maintenance --task "$TASK" $DRY_RUN --json
else
    "$VENV/bin/python" -m app.cli maintenance --task "$TASK" $DRY_RUN
fi
RC=$?
END="$(date +%s)"

log "退出码=${RC} 耗时=$((END - START))s"
exit "$RC"
