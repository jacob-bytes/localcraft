#!/usr/bin/env bash
# ============================================================
# /opt/localcraft/scripts/wait-healthy.sh
# 用途：轮询就绪探针，通过则退出 0，超时退出非零
#       被 localcraft.service 的 ExecStartPost 调用，使 systemctl start
#       的返回语义等价于"应用真正可用"
# 来源: docs/05《部署与运维方案》§11
# 用法: wait-healthy.sh [--url URL] [--timeout SECONDS] [--interval SECONDS]
# ============================================================
set -uo pipefail

URL="http://127.0.0.1:8000/readyz"
TIMEOUT=60
INTERVAL=1

while [ $# -gt 0 ]; do
    case "$1" in
        --url)      URL="$2"; shift 2 ;;
        --timeout)  TIMEOUT="$2"; shift 2 ;;
        --interval) INTERVAL="$2"; shift 2 ;;
        -h|--help)
            echo "用法: $0 [--url URL] [--timeout SECONDS] [--interval SECONDS]"
            exit 0
            ;;
        *) echo "未知参数: $1" >&2; exit 2 ;;
    esac
done

if ! command -v curl >/dev/null 2>&1; then
    echo "wait-healthy: 缺少 curl 命令" >&2
    exit 2
fi

DEADLINE=$(( $(date +%s) + TIMEOUT ))
ATTEMPT=0
CODE=000

while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    ATTEMPT=$((ATTEMPT + 1))
    CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$URL" 2>/dev/null || echo 000)"
    if [ "$CODE" = "200" ]; then
        echo "wait-healthy: ${URL} 就绪（HTTP 200，第 ${ATTEMPT} 次探测）"
        exit 0
    fi
    echo "wait-healthy: 第 ${ATTEMPT} 次探测 HTTP ${CODE}，等待 ${INTERVAL}s"
    sleep "$INTERVAL"
done

echo "wait-healthy: ${TIMEOUT}s 内未通过 ${URL}（最后状态 HTTP ${CODE:-000}）" >&2
echo "wait-healthy: 请检查 journalctl -u localcraft.service -n 100 --no-pager" >&2
exit 1
