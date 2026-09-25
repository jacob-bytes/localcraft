#!/usr/bin/env bash
# ============================================================
# scripts/notify-ready.sh — 向 systemd 上报 READY=1（Type=notify 变体用）
#
# 用途：配合 localcraft.service 的 `Type=notify` 变体（docs/05 §6.4）。
#       应用（uvicorn）本身不发送 sd_notify，所以用 ExecStartPost 调本脚本：
#       先等 /readyz 通过，再代替应用发 READY=1，从而让 `systemctl start`
#       的语义变成"应用真的可用了"，而不是"进程起来了"。
#
# 本脚本**不自己实现轮询**，而是直接调用同目录的 wait-healthy.sh ——
# 就绪判定只能有一处实现，两边各写一份迟早会漂移（例如超时、HTTP 码判定不一致）。
#
# 用法:
#   notify-ready.sh                                   # 默认 http://127.0.0.1:8000/readyz，60s
#   notify-ready.sh --url http://127.0.0.1:8000/readyz --timeout 60
#   notify-ready.sh http://127.0.0.1:8000/readyz 60   # 位置参数写法（与 docs/05 §6.4 一致）
#
# 对应的 unit 片段（见 docs/05 §6.4）：
#   [Service]
#   Type=notify
#   NotifyAccess=all
#   TimeoutStartSec=90s
#   ExecStartPost=/opt/localcraft/scripts/notify-ready.sh --url http://127.0.0.1:${LOCALCRAFT_PORT}/readyz --timeout 60
#   ExecStartPre=/bin/systemd-notify --status="localcraft starting"
#   ⚠ 使用 Type=notify 时**不要**再同时配置 wait-healthy.sh 作为 ExecStartPost，
#     两者功能重复（docs/05 §6.4 末尾的明确提醒）。
#
# 依据: docs/05《部署与运维方案》§6.4（Type=notify 变体）、§10.6（wait-healthy.sh）
# ============================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WAIT_HEALTHY="${LOCALCRAFT_WAIT_HEALTHY:-$SCRIPT_DIR/wait-healthy.sh}"

URL="http://127.0.0.1:${LOCALCRAFT_PORT:-8000}/readyz"
TIMEOUT=60
#: 位置参数计数：第 1 个是 URL，第 2 个是 TIMEOUT
POS=0

log()  { printf '[notify-ready] %s\n' "$*"; }
die()  { printf '[notify-ready][error] %s\n' "$*" >&2; exit 1; }

usage() { sed -n '2,28p' "$0"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --url)      URL="$2"; shift 2 ;;
        --timeout)  TIMEOUT="$2"; shift 2 ;;
        -h|--help)  usage; exit 0 ;;
        -*)         die "未知参数: $1（用 --help 查看用法）" ;;
        *)
            POS=$((POS + 1))
            case "$POS" in
                1) URL="$1" ;;
                2) TIMEOUT="$1" ;;
                *) die "多余的位置参数: $1" ;;
            esac
            shift
            ;;
    esac
done

# systemd-notify 来自 systemd 包。缺失时 Type=notify 方案整体不可用，
# 必须显式失败而不是"静默成功"—— 静默成功会让单元一直卡在 activating。
command -v systemd-notify >/dev/null 2>&1 \
    || die "缺少 systemd-notify 命令，Type=notify 方案不可用，请退回 Type=simple（docs/05 §6.4）"

[ -x "$WAIT_HEALTHY" ] \
    || die "找不到可执行的 wait-healthy.sh: ${WAIT_HEALTHY}（可用 LOCALCRAFT_WAIT_HEALTHY 指定）"

# NOTIFY_SOCKET 由 systemd 在本单元的环境里注入；手工在 shell 里跑时通常没有，
# 此时 systemd-notify 会失败 —— 提前说清原因，省得误判成"应用没起来"。
if [ -z "${NOTIFY_SOCKET:-}" ]; then
    log "警告：当前环境没有 NOTIFY_SOCKET，systemd-notify 会失败（本脚本应在 systemd 单元里运行）"
fi

log "等待就绪：${URL}（超时 ${TIMEOUT}s），复用 ${WAIT_HEALTHY}"

# 就绪轮询完全交给 wait-healthy.sh（它自己会打印每次探测结果）
if "$WAIT_HEALTHY" --url "$URL" --timeout "$TIMEOUT"; then
    # NotifyAccess=all 才允许非主进程（ExecStartPost 里的 systemd-notify）上报，
    # unit 里必须配这一行，否则这里的 READY=1 会被 systemd 丢弃。
    systemd-notify --ready --status="localcraft ready (readyz 200)" \
        || die "systemd-notify --ready 发送失败（检查 unit 是否配了 NotifyAccess=all）"
    log "已上报 READY=1"
    exit 0
fi

# 就绪失败：更新单元状态说明后再非零退出，让 systemd 把单元标为 failed 并可被监控发现
systemd-notify --status="localcraft 启动超时（${TIMEOUT}s 内未通过 ${URL}）" || true
printf '[notify-ready][error] %ss 内未通过 %s，未上报 READY=1\n' "$TIMEOUT" "$URL" >&2
printf '[notify-ready][error] 请检查 journalctl -u localcraft.service -n 100 --no-pager\n' >&2
exit 1
