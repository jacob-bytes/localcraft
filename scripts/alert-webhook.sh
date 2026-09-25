#!/usr/bin/env bash
# ============================================================
# scripts/alert-webhook.sh — 把一段文本推送到内网 webhook
#
# 用途：被 LOCALCRAFT_ALERT_CMD 引用（docs/05 §10.5），把 disk-alert.sh 等脚本
#       生成的告警文本推给企业微信 / 钉钉 / 自建告警网关。
#
# 关键点：用 jq 生成 JSON，**绝不用字符串拼接**。
#   告警文本里几乎必然出现引号、反斜杠、换行、中文标点（例如把 journal 片段
#   贴进消息）。手工拼接 JSON 只要有一个 `"` 就会让整个载荷非法，接收端
#   直接 400 —— 而告警脚本恰恰是最不能"静默失败"的地方。
#   `jq -n --arg` 会把值按 JSON 字符串规范转义，这是唯一稳妥的做法。
#
# 用法:
#   alert-webhook.sh "告警文本"                # 参数传文本
#   echo "告警文本" | alert-webhook.sh         # 从 stdin 读文本
#   alert-webhook.sh --raw "纯文本"            # 不套 JSON，直接 POST text/plain
#   LOCALCRAFT_ALERT_WEBHOOK_DRY_RUN=1 alert-webhook.sh "文本"   # 只打印载荷不发请求
#
# 配置（放在 /etc/localcraft/localcraft.env，或调用方环境里）:
#   LOCALCRAFT_ALERT_WEBHOOK_URL       必填，接收端地址
#   LOCALCRAFT_ALERT_WEBHOOK_TOKEN     可选，作为 Authorization: Bearer 发送
#   LOCALCRAFT_ALERT_WEBHOOK_TIMEOUT   可选，默认 10 秒
#   LOCALCRAFT_ALERT_WEBHOOK_TEMPLATE  可选，自定义 jq 过滤器（默认企业微信/钉钉文本格式）
#
# 在 localcraft.env 中的推荐配法：
#   LOCALCRAFT_ALERT_CMD=/opt/localcraft/scripts/alert-webhook.sh
#
# 依据: docs/05《部署与运维方案》§10.5（磁盘水位告警脚本 → webhook 部分）
# ============================================================
set -uo pipefail

URL="${LOCALCRAFT_ALERT_WEBHOOK_URL:-}"
TOKEN="${LOCALCRAFT_ALERT_WEBHOOK_TOKEN:-}"
TIMEOUT="${LOCALCRAFT_ALERT_WEBHOOK_TIMEOUT:-10}"
# 默认模板就是企业微信/钉钉通用的文本消息结构；换成别的网关时改这个 jq 过滤器。
TEMPLATE="${LOCALCRAFT_ALERT_WEBHOOK_TEMPLATE:-{msgtype:\"text\", text:{content:\$t}}}"
RAW=0
DRY_RUN=0
if [ "${LOCALCRAFT_ALERT_WEBHOOK_DRY_RUN:-0}" = "1" ]; then
    DRY_RUN=1
fi

log()  { printf '[alert-webhook] %s\n' "$*"; }
die()  { printf '[alert-webhook][error] %s\n' "$*" >&2; exit 1; }

usage() { sed -n '2,32p' "$0"; }

TEXT_ARG=""
HAS_TEXT=0
while [ $# -gt 0 ]; do
    case "$1" in
        --raw)       RAW=1; shift ;;
        --dry-run)   DRY_RUN=1; shift ;;
        -h|--help)   usage; exit 0 ;;
        -*)          die "未知参数: $1（用 --help 查看用法）" ;;
        *)           TEXT_ARG="$1"; HAS_TEXT=1; shift ;;
    esac
done

if [ "$HAS_TEXT" -eq 1 ]; then
    TEXT="$TEXT_ARG"
else
    # 没有参数就从 stdin 读；用 -d '' 之外的写法避免把 NUL 截断（告警文本不会有 NUL）
    TEXT="$(cat)"
fi

[ -n "$TEXT" ] || die "消息文本为空，拒绝发送（空告警只会制造噪音）"

if [ "$DRY_RUN" -ne 1 ]; then
    [ -n "$URL" ] || die "未配置 LOCALCRAFT_ALERT_WEBHOOK_URL"
    command -v curl >/dev/null 2>&1 || die "缺少 curl 命令"
fi

CONTENT_TYPE_HEADER=()
if [ "$RAW" -eq 1 ]; then
    CONTENT_TYPE_HEADER=(-H 'Content-Type: text/plain; charset=utf-8')
    PAYLOAD="$TEXT"
else
    command -v jq >/dev/null 2>&1 || die "缺少 jq 命令（用 jq 生成合法 JSON 是本脚本的前提，见文件头说明）"
    # --arg 做规范转义；-c 输出紧凑单行，避免接收端把换行当载荷截断
    PAYLOAD="$(jq -nc --arg t "$TEXT" "$TEMPLATE")" \
        || die "jq 生成 JSON 失败（检查 LOCALCRAFT_ALERT_WEBHOOK_TEMPLATE 是否是合法过滤器）"
    CONTENT_TYPE_HEADER=(-H 'Content-Type: application/json; charset=utf-8')
fi

if [ "$DRY_RUN" -eq 1 ]; then
    log "dry-run：不发送请求。目标=${URL:-（未配置）}"
    printf '%s\n' "$PAYLOAD"
    exit 0
fi

AUTH_HEADER=()
if [ -n "$TOKEN" ]; then
    AUTH_HEADER=(-H "Authorization: Bearer ${TOKEN}")
fi

# 用 -o /dev/null -w '%{http_code}' 而不是 -f：告警失败时我们想**看到状态码**，
# 而 -f 会把响应体丢掉、只留一句 "The requested URL returned error: 400"。
HTTP_CODE="$(curl -sS -o /dev/null -w '%{http_code}' --max-time "$TIMEOUT" \
    "${CONTENT_TYPE_HEADER[@]}" ${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"} \
    -X POST --data-binary "$PAYLOAD" "$URL" 2>/dev/null)" || {
        die "curl 请求失败（网络不通 / DNS / 超时 ${TIMEOUT}s）: ${URL}"
    }

case "$HTTP_CODE" in
    2*) log "已发送（HTTP ${HTTP_CODE}）"; exit 0 ;;
    *)  die "webhook 返回 HTTP ${HTTP_CODE}（非 2xx），告警未送达: ${URL}" ;;
esac
