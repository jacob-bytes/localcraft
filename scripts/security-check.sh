#!/usr/bin/env bash
# ============================================================
# scripts/security-check.sh — localcraft 安全基线自查
#
# 用途：一键核查 docs/05《部署与运维方案》§13 的安全基线项，输出 OK / FAIL / WARN 清单。
#       设计成"可以随时跑、只读不改"：不使用任何有副作用的命令，
#       唯一的网络动作是对本机探针路径发 GET（用于确认文档端点是否关闭）。
#
# 检查项（对应 §13.9 的 10 条，另补 2 条 §13.1/§13.2 的显式项）:
#   1) 监听地址只绑回环，不得出现 0.0.0.0 / [::]
#   2) env 文件权限 0640 root:localcraft，且 SECRET_KEY 不是占位符、长度足够
#   3) TLS 私钥权限不得过宽
#   4) 数据目录权限 0750 localcraft:localcraft
#   5) 脚本目录不得对 group/other 可写（防止被篡改后由 systemd 执行）
#   6) /docs /redoc /openapi.json 已关闭（期望 404）
#   7) systemd 加固项与 LimitNOFILE
#   8) worker 数为 1（与 SQLite 写锁模型匹配）
#   9) SELinux 状态与 httpd_can_network_connect
#  10) 防火墙是否放通 https、是否仍开着 http
#  11) 超级管理员数量与示例账号
#  12) 备份新鲜度
#
# 用法:
#   scripts/security-check.sh
#   scripts/security-check.sh --base-url http://127.0.0.1
#   LOCALCRAFT_SKIP_SYSTEMD=1 scripts/security-check.sh   # 无 systemd 环境：相关项记 WARN
#
# 依据: docs/05《部署与运维方案》§13（安全基线）、§13.9（自查脚本）、§13.1、§13.2
#
# 退出码: 0 = 无 FAIL 项（WARN 需人工确认）；1 = 存在 FAIL 项
# ============================================================
set -uo pipefail

PREFIX="${LOCALCRAFT_PREFIX:-/opt/localcraft}"
DATA_DIR="${LOCALCRAFT_DATA_DIR:-/var/lib/localcraft}"
ETC_DIR="${LOCALCRAFT_ETC_DIR:-/etc/localcraft}"
LOG_DIR="${LOCALCRAFT_LOG_DIR:-/var/log/localcraft}"
DB_FILE="${LOCALCRAFT_DB:-$DATA_DIR/localcraft.db}"
BACKUP_ROOT="${LOCALCRAFT_BACKUP_DIR:-$DATA_DIR/backups}"
ENV_FILE="$ETC_DIR/localcraft.env"
TLS_KEY="$ETC_DIR/tls/localcraft.key"
SCRIPTS_DIR="$PREFIX/scripts"
APP_LINK="$PREFIX/app/current"

PORT="${LOCALCRAFT_PORT:-8000}"
BASE_URL="${LOCALCRAFT_BASE_URL:-http://127.0.0.1}"
SKIP_SYSTEMD="${LOCALCRAFT_SKIP_SYSTEMD:-0}"
BACKUP_STALE_H="${LOCALCRAFT_ALERT_BACKUP_STALE_H:-48}"

FAIL=0
ok()   { printf '[ OK ]   %s\n' "$*"; }
bad()  { printf '[ FAIL ] %s\n' "$*"; FAIL=1; }
warn() { printf '[ WARN ] %s\n' "$*"; }

usage() { sed -n '2,42p' "$0"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --base-url)  BASE_URL="$2"; shift 2 ;;
        --base-url=*) BASE_URL="${1#--base-url=}"; shift ;;
        -h|--help)   usage; exit 0 ;;
        *) printf '[security-check][warn] 未知参数: %s\n' "$1" >&2; exit 2 ;;
    esac
done

# GNU coreutils 的 stat 与 BSD/macOS 的 stat 参数完全不同，必须探测而不是假定。
# `stat --version` 在 BSD 上会失败（没有 --version），据此分流。
file_stat() {  # file_stat <路径> → "mode owner group"，不存在输出 missing
    [ -e "$1" ] || { echo "missing"; return 0; }
    if stat --version >/dev/null 2>&1; then
        stat -c '%a %U %G' "$1"
    else
        stat -f '%Lp %Su %Sg' "$1"
    fi
}
mtime_epoch() {  # mtime_epoch <文件>
    if stat --version >/dev/null 2>&1; then
        stat -c %Y "$1"
    else
        stat -f %m "$1" 2>/dev/null || echo 0
    fi
}
http_code() {  # http_code <url>
    # curl 连接失败时仍会按 -w 输出 000 并非零退出；`|| echo 000` 会变成 "000000"。
    local code=""
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$1" 2>/dev/null)" || code=""
    echo "${code:-000}"
}

# `date -Is` 是 GNU 扩展，macOS/BSD 不支持；与 precheck.sh 用同一可移植写法
now_iso() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }

echo "=== localcraft 安全基线自查 $(now_iso) ==="
echo

# ------------------------------------------------------------
# 1) 监听地址
# ------------------------------------------------------------
if command -v ss >/dev/null 2>&1; then
    LISTEN_ALL="$(ss -lntH 2>/dev/null | awk '{print $4}' | grep -E "^(0\.0\.0\.0|\[::\]|::|\*):${PORT}\$" || true)"
    LISTEN_LOOP="$(ss -lntH 2>/dev/null | awk '{print $4}' | grep -E "^(127\.0\.0\.1|\[::1\]):${PORT}\$" || true)"
    if [ -n "$LISTEN_ALL" ]; then
        bad "应用监听在所有网卡（${LISTEN_ALL}），外部可绕过 nginx 直连；应为 127.0.0.1:${PORT}"
    elif [ -n "$LISTEN_LOOP" ]; then
        ok "应用只监听回环（${LISTEN_LOOP}）"
    else
        warn "未检测到 ${PORT} 端口在监听（服务可能没在运行）"
    fi
else
    warn "缺少 ss 命令，跳过监听地址检查"
fi

# ------------------------------------------------------------
# 2) 环境变量文件权限与 SECRET_KEY
# ------------------------------------------------------------
PERM="$(file_stat "$ENV_FILE")"
if [ "$PERM" = "640 root localcraft" ]; then
    ok "env 文件权限: ${PERM}"
else
    bad "env 文件权限异常: ${PERM}（期望 640 root localcraft）"
fi

if [ -r "$ENV_FILE" ]; then
    KEY="$(grep -E '^SECRET_KEY=' "$ENV_FILE" 2>/dev/null | head -n1 | cut -d= -f2- || true)"
    KEY_LEN="${#KEY}"
    if [ -z "$KEY" ]; then
        bad "env 文件里没有 SECRET_KEY"
    elif [ "$KEY" = "REPLACE_WITH_openssl_rand_hex_32" ]; then
        bad "SECRET_KEY 仍是占位符，必须用 openssl rand -hex 32 重新生成"
    elif [ "$KEY_LEN" -lt 32 ]; then
        bad "SECRET_KEY 长度 ${KEY_LEN} < 32 字节（HS256 建议至少 32 字节）"
    else
        ok "SECRET_KEY 已设置（长度 ${KEY_LEN}，不打印明文）"
    fi
else
    warn "无法读取 ${ENV_FILE}（权限不足？非 root 执行时属正常）"
fi

# ------------------------------------------------------------
# 3) TLS 私钥权限
# ------------------------------------------------------------
PERM="$(file_stat "$TLS_KEY")"
MODE_OWNER="$(printf '%s' "$PERM" | cut -d' ' -f1-2)"
case "$PERM" in
    missing)           warn "未找到 TLS 私钥（HTTP 快速验证阶段可忽略）: ${TLS_KEY}" ;;
    "640 root "*)      ok "TLS 私钥权限: ${PERM}" ;;
    "600 root")        ok "TLS 私钥权限: ${PERM}" ;;
    *)                 bad "TLS 私钥权限过宽: ${PERM}（期望 600/640 且属主 root，当前 ${MODE_OWNER}）" ;;
esac

# ------------------------------------------------------------
# 4) 数据目录权限
# ------------------------------------------------------------
PERM="$(file_stat "$DATA_DIR")"
if [ "$PERM" = "750 localcraft localcraft" ]; then
    ok "数据目录权限: ${PERM}"
elif [ "$PERM" = "missing" ]; then
    bad "数据目录不存在: ${DATA_DIR}"
else
    warn "数据目录权限: ${PERM}（期望 750 localcraft localcraft）"
fi

# ------------------------------------------------------------
# 4.1) 日志目录权限 与 current 软链落位
# ------------------------------------------------------------
PERM="$(file_stat "$LOG_DIR")"
if [ "$PERM" = "750 localcraft localcraft" ]; then
    ok "日志目录权限: ${PERM}"
elif [ "$PERM" = "missing" ]; then
    warn "日志目录不存在: ${LOG_DIR}（纯 journald 部署且未开启文件日志时可忽略）"
else
    warn "日志目录权限: ${PERM}（期望 750 localcraft localcraft）"
fi

# 升级/回滚全靠 current 软链切换。它一旦被替换成真目录，
# 后续 `ln -sfn` 会把新软链写进那个目录里，而不是替换它 —— 升级会静默失败。
if [ -L "$APP_LINK" ]; then
    TARGET_LINK="$(readlink -f "$APP_LINK" 2>/dev/null || echo '')"
    case "$TARGET_LINK" in
        "$PREFIX"/releases/*) ok "current 是指向 releases/ 的软链（${TARGET_LINK}）" ;;
        "")                   warn "current 是软链但无法解析目标: ${APP_LINK}" ;;
        *)                    warn "current 指向了 releases/ 之外: ${TARGET_LINK}" ;;
    esac
elif [ -e "$APP_LINK" ]; then
    bad "current 不是软链而是实体（${APP_LINK}）：升级/回滚的原子切换会失效，请改回软链"
else
    warn "找不到 current: ${APP_LINK}"
fi

# ------------------------------------------------------------
# 5) 脚本目录不可被非 root 写
# ------------------------------------------------------------
PERM="$(file_stat "$SCRIPTS_DIR")"
case "$PERM" in
    missing) warn "脚本目录不存在: ${SCRIPTS_DIR}" ;;
    *)
        MODE="$(printf '%s' "$PERM" | cut -d' ' -f1)"
        # 只看 group 与 other 两位权限（mode 的最后两位）。
        # 若其中出现 2/3/6/7，说明 group 或 other 有写权限。
        GROUP_OTHER="${MODE: -2}"
        case "$GROUP_OTHER" in
            *2*|*3*|*6*|*7*) bad "脚本目录对 group/other 可写（mode ${MODE}）：被篡改后会被 systemd 以服务身份执行" ;;
            *)               ok "脚本目录权限 ${PERM}（非 root 不可写）" ;;
        esac
        ;;
esac

# ------------------------------------------------------------
# 6) 文档端点已关闭
# ------------------------------------------------------------
if command -v curl >/dev/null 2>&1; then
    for p in /docs /redoc /openapi.json; do
        CODE="$(http_code "${BASE_URL}${p}")"
        if [ "$CODE" = "404" ]; then
            ok "${p} 已关闭 (404)"
        elif [ "$CODE" = "000" ]; then
            warn "${p} 无法探测（连接失败，服务没在跑？）"
        else
            bad "${p} 返回 ${CODE}，生产应关闭（设 API_DOCS_ENABLED=false）"
        fi
    done
else
    warn "缺少 curl，跳过文档端点检查"
fi

# ------------------------------------------------------------
# 7) systemd 加固项
# ------------------------------------------------------------
if [ "$SKIP_SYSTEMD" = "1" ] || ! command -v systemctl >/dev/null 2>&1; then
    warn "无 systemd（或 LOCALCRAFT_SKIP_SYSTEMD=1），跳过 systemd 加固与 LimitNOFILE 检查"
else
    for opt in NoNewPrivileges PrivateTmp ProtectSystem ProtectHome; do
        V="$(systemctl show localcraft.service -p "$opt" --value 2>/dev/null || true)"
        case "$V" in
            yes|strict|true) ok "systemd ${opt}=${V}" ;;
            *)               bad "systemd ${opt}=${V:-空}（期望已启用；检查 drop-in 是否安装）" ;;
        esac
    done
    NF="$(systemctl show localcraft.service -p LimitNOFILE --value 2>/dev/null || echo 0)"
    case "$NF" in
        ''|*[!0-9]*) bad "LimitNOFILE 读取异常: ${NF}" ;;
        *) [ "$NF" -ge 65536 ] && ok "LimitNOFILE=${NF}" \
               || bad "LimitNOFILE=${NF} 偏低（建议 >= 65536，见 docs/05 §6.5）" ;;
    esac

    # --------------------------------------------------------
    # 8) worker 数
    # --------------------------------------------------------
    if systemctl show localcraft.service -p ExecStart --value 2>/dev/null | grep -q -- '--workers 1'; then
        ok "worker 数为 1（与 SQLite WAL 单写者匹配）"
    else
        warn "worker 数不是 1：请确认已了解 SQLite 写锁风险（docs/05 §6.3）"
    fi
fi

# ------------------------------------------------------------
# 9) SELinux
# ------------------------------------------------------------
if command -v getenforce >/dev/null 2>&1; then
    E="$(getenforce 2>/dev/null || echo unknown)"
    if [ "$E" = "Enforcing" ]; then
        ok "SELinux=${E}"
    else
        warn "SELinux=${E}（生产建议 Enforcing，见 docs/05 §12.7）"
    fi
    if command -v getsebool >/dev/null 2>&1; then
        B="$(getsebool httpd_can_network_connect 2>/dev/null | awk '{print $3}' || true)"
        if [ "$B" = "on" ]; then
            ok "httpd_can_network_connect=on"
        else
            bad "httpd_can_network_connect=${B:-unknown}（nginx 反代会 502，见 docs/05 §12.7）"
        fi
    fi
fi

# ------------------------------------------------------------
# 10) 防火墙
# ------------------------------------------------------------
if command -v firewall-cmd >/dev/null 2>&1; then
    SERVICES="$(firewall-cmd --list-services 2>/dev/null || true)"
    if printf '%s' "$SERVICES" | grep -qw https; then
        ok "防火墙已放通 https"
    else
        warn "防火墙未放通 https（${SERVICES:-读不到规则}）"
    fi
    if printf '%s' "$SERVICES" | grep -qw http; then
        warn "防火墙放通了 http（仅用于 80→443 跳转，请确认 80 上没有别的服务）"
    fi
fi

# ------------------------------------------------------------
# 11) 超级管理员与示例账号
# ------------------------------------------------------------
if [ -f "$DB_FILE" ] && command -v sqlite3 >/dev/null 2>&1; then
    # 超管身份通过 user_roles → roles.code='superadmin'，users 表本身没有该字段
    N="$(sqlite3 "$DB_FILE" "
        SELECT COUNT(DISTINCT u.id) FROM users u
        JOIN user_roles ur ON ur.user_id = u.id
        JOIN roles r       ON r.id = ur.role_id
        WHERE r.code = 'superadmin' AND u.status = 'active';" 2>/dev/null || echo '?')"
    if [ "$N" = "1" ]; then
        ok "启用的超级管理员数量: 1"
    else
        warn "启用的超级管理员数量: ${N}（建议保留 1~2 个，且不得为 0）"
    fi
    D="$(sqlite3 "$DB_FILE" "SELECT COUNT(*) FROM users WHERE username IN ('test','demo','guest');" 2>/dev/null || echo '?')"
    # 查询失败（返回非数字 '?'）不能算 FAIL：数据库可能还没迁移。静默地把 '?' 当成
    # "有示例账号" 会制造假 FAIL，而假 FAIL 会让这条基线检查很快被忽略。
    case "$D" in
        ''|*[!0-9]*) warn "无法查询示例账号（数据库不可读或尚未迁移）: ${D}" ;;
        0)           ok "无示例账号" ;;
        *)           bad "存在示例/测试账号 ${D} 个，请清理" ;;
    esac
else
    warn "未找到数据库（${DB_FILE}）或缺少 sqlite3，跳过账号检查"
fi

# ------------------------------------------------------------
# 12) 备份新鲜度
# ------------------------------------------------------------
LATEST_BK="$(find "$BACKUP_ROOT/db" -maxdepth 1 \( -name 'localcraft-*.db' -o -name 'localcraft-*.db.gz' \) -print 2>/dev/null | sort | tail -1)"
if [ -n "$LATEST_BK" ]; then
    H=$(( ( $(date +%s) - $(mtime_epoch "$LATEST_BK") ) / 3600 ))
    if [ "$H" -le "$BACKUP_STALE_H" ]; then
        ok "最近备份 ${H} 小时前（$(basename "$LATEST_BK")）"
    else
        bad "最近备份 ${H} 小时前，超过 ${BACKUP_STALE_H}h 阈值"
    fi
else
    bad "未找到任何数据库备份（${BACKUP_ROOT}/db）"
fi

# ------------------------------------------------------------
echo
if [ "$FAIL" -eq 0 ]; then
    echo "=== 安全基线自查通过（WARN 项请人工确认） ==="
    exit 0
fi
echo "=== 安全基线自查存在 FAIL 项，请逐条处置 ==="
exit 1
