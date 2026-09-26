#!/usr/bin/env bash
# ============================================================
# scripts/rollback.sh — localcraft 回滚封装
#
# 对应 docs/05 §11.4 的三种情况：
#   情况 A：只换了代码与前端，未执行数据库迁移 —— 只切软链 + 回退依赖，最安全
#   情况 B：已执行数据库迁移，但迁移"只增不改"（加表 / 加可空列）—— 也只切软链
#   情况 C：已执行破坏性迁移 —— 必须还原升级前的数据库备份（--restore-db）
#
# 本脚本只做"能自动做对"的部分（切软链、回退依赖、校验、起服务）；
# 情况 C 的数据库还原**不会自动触发**，必须由人显式传 --restore-db。
# 理由同 upgrade.sh：在"迁移做了一半"的状态上自动还原备份，会把
# "新代码 + 部分迁移"变成"旧代码 + 旧数据"，丢掉升级期间用户新产生的数据，
# 属于不可逆动作，必须由人拍板（docs/05 §11.4 末尾的论证）。
#
# 用法:
#   rollback.sh --list                       # 列出可回滚的 release 与当前软链
#   rollback.sh                              # 切回 current 之外的最近一个 release
#   rollback.sh --to localcraft-0.9.0          # 按版本名
#   rollback.sh --to /opt/localcraft/releases/localcraft-0.9.0
#   rollback.sh --restore-db /var/lib/localcraft/backups/db/pre-upgrade-1.0.0-<时间戳>.db --yes
#   rollback.sh --dry-run                    # 只打印将执行的步骤
#
# 依据: docs/05《部署与运维方案》§11.4（回滚流程）、§11.5（升级回滚对照表）
# ============================================================
set -uo pipefail

PREFIX="${LOCALCRAFT_PREFIX:-/opt/localcraft}"
# 配置解析：**应用读的变量名优先**，历史 LOCALCRAFT_ 前缀名作兼容别名，最后才是默认值。
# 为什么必须这样：localcraft.env 里写的是应用读的名字，而 systemd 单元用
# EnvironmentFile 把它们注入脚本环境。脚本若只认 LOCALCRAFT_DATA_DIR，
# 运维改了 DATA_DIR 后脚本会静默回落到 /var/lib/localcraft ——
# 备份跑成功、退出码 0、日志正常，备的却是错的目录。
DATA_DIR="${DATA_DIR:-${LOCALCRAFT_DATA_DIR:-/var/lib/localcraft}}"
ETC_DIR="${LOCALCRAFT_ETC_DIR:-/etc/localcraft}"
LOG_DIR="${LOCALCRAFT_LOG_DIR:-/var/log/localcraft}"

ENV_FILE="$ETC_DIR/localcraft.env"
VENV="$PREFIX/venv"
APP_LINK="$PREFIX/app/current"
RELEASES_DIR="$PREFIX/releases"
SCRIPTS_DIR="$PREFIX/scripts"
# DB 路径由 DATABASE_URL 派生（应用只用这一个变量描述数据库位置）。
# `sqlite+aiosqlite:///` 之后 3 个斜杠是相对路径、4 个是绝对路径，
# 两种都靠“去掉这 3 个斜杠”得到正确结果。
case "${DATABASE_URL:-}" in
    sqlite+aiosqlite:///*) _DB_FROM_URL="${DATABASE_URL#sqlite+aiosqlite:///}" ;;
    *) _DB_FROM_URL="" ;;
esac
DB_FILE="${_DB_FROM_URL:-${LOCALCRAFT_DB:-$DATA_DIR/localcraft.db}}"
unset _DB_FROM_URL
PID_FILE="$DATA_DIR/localcraft.pid"
HEALTH_URL="http://127.0.0.1:${LOCALCRAFT_PORT:-8000}/readyz"

SKIP_SYSTEMD="${LOCALCRAFT_SKIP_SYSTEMD:-0}"
TARGET=""
ASSUME_YES=0
DRY_RUN=0
NO_DEPS=0
LIST_ONLY=0
RESTORE_DB=""
ALLOW_DB_AHEAD=0
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="(未启用)"

log()  { printf '\n\033[1;32m[rollback] %s\033[0m\n' "$*"; }
info() { printf '[rollback] %s\n' "$*"; }
warn() { printf '[rollback][warn] %s\n' "$*" >&2; }
die()  { printf '[rollback][error] %s\n' "$*" >&2; exit 1; }

run() {
    printf '[rollback] $ %s\n' "$*"
    [ "$DRY_RUN" -eq 1 ] && return 0
    "$@"
}

usage() { sed -n '2,32p' "$0"; }

resolve_link() {
    if readlink -f "$1" >/dev/null 2>&1; then
        readlink -f "$1"
    else
        ( cd "$1" 2>/dev/null && pwd -P )
    fi
}

if command -v sha256sum >/dev/null 2>&1; then
    SHA256_BIN=(sha256sum)
elif command -v shasum >/dev/null 2>&1; then
    SHA256_BIN=(shasum -a 256)
else
    SHA256_BIN=()
fi

# `mv -T` 是 GNU coreutils 选项（原子替换软链的关键）；演练机（macOS）没有，
# 此时退化为 `ln -sfn` 直接替换（BSD ln 对软链目标本身就是替换语义）。
MV_T=0
if mv --help 2>/dev/null | grep -q -- '--no-target-directory'; then
    MV_T=1
fi

verify_sums() {
    [ "${#SHA256_BIN[@]}" -gt 0 ] || return 0
    ( cd "$1" && "${SHA256_BIN[@]}" -c SHA256SUMS --quiet 2>/dev/null \
        || "${SHA256_BIN[@]}" -c SHA256SUMS >/dev/null )
}

# ------------------------------------------------------------
# 0) 参数
# ------------------------------------------------------------
while [ $# -gt 0 ]; do
    case "$1" in
        --to)              TARGET="$2"; shift 2 ;;
        --to=*)            TARGET="${1#--to=}"; shift ;;
        --list)            LIST_ONLY=1; shift ;;
        --yes|-y)          ASSUME_YES=1; shift ;;
        --dry-run|-n)      DRY_RUN=1; shift ;;
        --no-deps)         NO_DEPS=1; shift ;;
        --restore-db)      RESTORE_DB="$2"; shift 2 ;;
        --restore-db=*)    RESTORE_DB="${1#--restore-db=}"; shift ;;
        --allow-db-ahead)  ALLOW_DB_AHEAD=1; shift ;;
        -h|--help)         usage; exit 0 ;;
        *) die "未知参数: $1（用 --help 查看用法）" ;;
    esac
done

CURRENT_LINK="$(resolve_link "$APP_LINK" 2>/dev/null || true)"

# ------------------------------------------------------------
# --list：先让人看清楚有哪些可回
# ------------------------------------------------------------
list_releases() {
    echo "当前 current -> ${CURRENT_LINK:-（未建立）}"
    echo
    echo "可用的 release 目录（按修改时间倒序）："
    # 不用 ls -t 排序解析输出，改为 find -printf 拿 mtime 再排，
    # 这样"最新"的判定不受文件名版本号排序方式影响。
    if [ -d "$RELEASES_DIR" ]; then
        find "$RELEASES_DIR" -maxdepth 1 -mindepth 1 -type d -print 2>/dev/null \
            | while IFS= read -r d; do
                v="$(cat "$d/VERSION" 2>/dev/null || echo '?')"
                mark="  "
                [ -n "$CURRENT_LINK" ] && [ "$(resolve_link "$d")" = "$CURRENT_LINK" ] && mark="* "
                printf '%s%-45s VERSION=%s\n' "$mark" "$(basename "$d")" "$v"
              done
        echo
        echo "（带 * 的是当前正在使用的版本；默认回滚目标 = 除它之外最新的一个）"
    else
        echo "  ${RELEASES_DIR} 不存在"
    fi
}

if [ "$LIST_ONLY" -eq 1 ]; then
    list_releases
    exit 0
fi

if [ "$SKIP_SYSTEMD" = "1" ]; then
    warn "LOCALCRAFT_SKIP_SYSTEMD=1 —— 演练模式：不调用 systemctl，改用 pid 文件 + nohup 拉起服务"
elif [ "$DRY_RUN" -eq 0 ]; then
    [ "$(id -u)" -eq 0 ] || die "请以 root 执行"
fi

[ -d "$RELEASES_DIR" ] || die "找不到 releases 目录: ${RELEASES_DIR}"
[ -f "$ENV_FILE" ]     || die "找不到环境变量文件: ${ENV_FILE}"
[ -x "$VENV/bin/python" ] || die "找不到 venv: ${VENV}/bin/python"

# ------------------------------------------------------------
# 1) 解析回滚目标
# ------------------------------------------------------------
if [ -z "$TARGET" ]; then
    TARGET="$(
        find "$RELEASES_DIR" -maxdepth 1 -mindepth 1 -type d -print 2>/dev/null \
            | while IFS= read -r d; do
                [ -n "$CURRENT_LINK" ] && [ "$(resolve_link "$d")" = "$CURRENT_LINK" ] && continue
                printf '%s %s\n' "$(stat -c %Y "$d" 2>/dev/null || stat -f %m "$d" 2>/dev/null || echo 0)" "$d"
              done | sort -rn | sed -n '1p' | cut -d' ' -f2-
    )"
    [ -n "$TARGET" ] || die "没有可回滚的 release（releases 目录里只有当前版本）。用 --list 查看。"
    info "未指定 --to，自动选择除 current 之外最近的一个 release"
fi

# 支持 --to <版本名>：补全成 releases/<name>
case "$TARGET" in
    /*) : ;;
    *)  if [ -d "$RELEASES_DIR/$TARGET" ]; then TARGET="$RELEASES_DIR/$TARGET";
        elif [ -d "$TARGET" ]; then : ;
        else die "找不到回滚目标: ${TARGET}（用 --list 查看可用 release）"; fi ;;
esac
[ -d "$TARGET" ] || die "回滚目标不是目录: ${TARGET}"

TARGET="$(resolve_link "$TARGET")"
TARGET_VERSION="$(cat "$TARGET/VERSION" 2>/dev/null || echo unknown)"
CURRENT_VERSION="$(cat "$APP_LINK/VERSION" 2>/dev/null || echo unknown)"

[ "$TARGET" != "$CURRENT_LINK" ] || die "回滚目标与当前版本相同（${TARGET}），无需回滚"

# 升级日志（与 upgrade.sh 同目录，便于把一次升级 + 回滚串起来看）
if [ "$DRY_RUN" -eq 0 ]; then
    if [ -d "$LOG_DIR" ] && [ -w "$LOG_DIR" ]; then
        LOG="$LOG_DIR/localcraft-rollback-${STAMP}.log"
    else
        LOG="/tmp/localcraft-rollback-${STAMP}.log"
    fi
    exec > >(tee -a "$LOG") 2>&1
fi

# ------------------------------------------------------------
# 2) 读 RELEASE-NOTES.md，判断"被回滚掉的那次升级是否含迁移"
# ------------------------------------------------------------
# 依赖 RELEASE-NOTES.md 里以行首开始的关键行（格式见 RELEASE-NOTES.md 开头说明）
detect_migration() {
    local notes="$1" line="" value=""
    [ -n "$notes" ] && [ -f "$notes" ] || { echo unknown; return 0; }
    line="$(grep -m1 '^本次是否包含数据库迁移' "$notes" 2>/dev/null || true)"
    [ -n "$line" ] || { echo unknown; return 0; }
    # 只取冒号之后的值："本次是否包含数据库迁移" 行首的「是否」自带一个「否」，
    # 对整行做 *否* 匹配会把"是"误判成"否"。
    value="$line"
    case "$line" in
        *：*) value="${line#*：}" ;;
        *:*)  value="${line#*:}" ;;
    esac
    value="$(printf '%s' "$value" | tr -d ' *`')"
    case "$value" in
        否*) echo no ;;
        是*) echo yes ;;
        *)   echo unknown ;;
    esac
}

# 只读"即将被回滚掉的"当前版本的说明：它决定本次回滚属于 docs/05 §11.4 的哪一种情况。
# 目标版本自己的说明对"回滚到它"没有指导意义，读了反而容易误导。
CUR_NOTES="$(detect_migration "$APP_LINK/RELEASE-NOTES.md")"
DB_REV="$(sqlite3 "$DB_FILE" 'SELECT version_num FROM alembic_version;' 2>/dev/null | head -n1 || true)"
DB_REV="${DB_REV:-unknown}"

echo "============================================================"
echo "localcraft 回滚"
echo "  从版本     : ${CURRENT_VERSION}（${CURRENT_LINK}）"
echo "  回滚到     : ${TARGET_VERSION}（${TARGET}）"
echo "  数据库版本 : ${DB_REV}"
echo "  回滚日志   : ${LOG}"
echo "============================================================"
echo
case "$CUR_NOTES" in
    yes)
        printf '\033[1;33m';
        echo "⚠ 当前版本（${CURRENT_VERSION}）的 RELEASE-NOTES.md 声明【包含数据库迁移】。"
        echo "  请先确认该迁移是「只增不改」（情况 B）还是破坏性的（情况 C）："
        echo "      grep -nE 'drop_|alter_column|op\\.drop|nullable=False' ${APP_LINK}/migrations/versions/*.py"
        echo "  破坏性迁移必须用 --restore-db 还原升级前备份，否则旧代码会跑在新结构上。"
        printf '\033[0m\n' ;;
    no)
        echo "当前版本的 RELEASE-NOTES.md 声明不含数据库迁移（情况 A，最安全）。" ;;
    *)
        echo "无法从当前版本的 RELEASE-NOTES.md 判定是否含迁移，请人工确认（见 docs/05 §11.4）。" ;;
esac

# 关键校验：数据库当前 revision 在目标版本的迁移目录里是否存在。
# 不存在 = 数据库已经"比目标代码新"，旧代码没有任何接住它的可能。
DB_AHEAD=0
if [ "$DB_REV" != "unknown" ] && [ -d "$TARGET/migrations/versions" ]; then
    if ! find "$TARGET/migrations/versions" -maxdepth 1 -name "${DB_REV}_*.py" -print 2>/dev/null | grep -q .; then
        DB_AHEAD=1
        printf '\n\033[1;31m';
        echo "⚠⚠ 数据库 revision ${DB_REV} 在目标版本 ${TARGET_VERSION} 的 migrations/ 里不存在。"
        echo "    说明已经执行过比目标版本更新的迁移 —— 只切代码不做数据库还原，属于情况 C 的误操作。"
        echo "    正确做法："
        echo "      ${SCRIPTS_DIR}/rollback.sh --to ${TARGET_VERSION} \\"
        echo "          --restore-db ${DATA_DIR}/backups/db/pre-upgrade-<旧版本>-<升级时间戳>.db --yes"
        echo "    若你已人工确认该迁移向后兼容（只加表 / 只加可空列），"
        echo "    可加 --allow-db-ahead 继续（脚本仍会保留数据库不动）。"
        printf '\033[0m\n'
    fi
fi

if [ -n "$RESTORE_DB" ]; then
    [ -f "$RESTORE_DB" ] || die "指定的数据库备份不存在: ${RESTORE_DB}"
    printf '\n\033[1;33m将先还原数据库备份（这会丢弃升级后产生的所有新数据）：%s\033[0m\n' "$RESTORE_DB"
fi

# ------------------------------------------------------------
# 3) 确认
# ------------------------------------------------------------
# 这个闸门是本次回滚的"安全锁"：数据库已经比目标代码新、又没有给出数据库还原方案时，
# 真跑会被拒绝。单独算成变量，是为了让 dry-run 也能明确报告"真跑会被拒绝"。
DB_AHEAD_BLOCK=0
if [ "$DB_AHEAD" -eq 1 ] && [ -z "$RESTORE_DB" ] && [ "$ALLOW_DB_AHEAD" -eq 0 ]; then
    DB_AHEAD_BLOCK=1
fi

if [ "$DRY_RUN" -eq 1 ] && [ "$DB_AHEAD_BLOCK" -eq 1 ]; then
    printf '\n\033[1;31m[dry-run 判定] 真跑会被拒绝：数据库比目标版本新，且未给出 --restore-db / --allow-db-ahead。\033[0m\n'
    printf '\033[1;31m下面按"已给出 --allow-db-ahead"继续打印计划，仅供评估，请勿照抄执行。\033[0m\n'
fi

if [ "$ASSUME_YES" -ne 1 ] && [ "$DRY_RUN" -ne 1 ]; then
    if [ "$DB_AHEAD_BLOCK" -eq 1 ]; then
        die "数据库比目标版本新，且未指定 --restore-db / --allow-db-ahead：已中止（这是保护，不是故障）"
    fi
    printf '\n确认回滚到 %s 请输入 rollback（其它任何输入都会取消）：' "$TARGET_VERSION"
    read -r ANSWER
    [ "$ANSWER" = "rollback" ] || die "已取消"
fi

# ------------------------------------------------------------
# 4) 停服务
# ------------------------------------------------------------
log "1/7 停止服务"
if [ "$SKIP_SYSTEMD" = "1" ]; then
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        run kill "$(cat "$PID_FILE")" 2>/dev/null || true
        for _ in $(seq 1 30); do
            [ "$DRY_RUN" -eq 1 ] && break
            kill -0 "$(cat "$PID_FILE")" 2>/dev/null || break
            sleep 1
        done
    fi
else
    run systemctl stop localcraft.service || warn "systemctl stop 返回非零（服务可能本就没在跑），继续"
    if command -v pgrep >/dev/null 2>&1; then
        if pgrep -f 'uvicorn app.main:app' >/dev/null 2>&1; then
            [ "$DRY_RUN" -eq 1 ] || sleep 3
            pgrep -f 'uvicorn app.main:app' >/dev/null 2>&1 && die "uvicorn 进程未退出，拒绝继续"
        fi
    fi
fi

# ------------------------------------------------------------
# 5) 还原数据库（仅当显式指定）—— 放在切代码之前，
#    这样 restore.sh 用的是仍在运行的旧（= 新版本）venv，兼容性更好
# ------------------------------------------------------------
log "2/7 数据库还原"
if [ -n "$RESTORE_DB" ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '[rollback] $ %s/restore.sh --db %s --yes --no-start\n' "$SCRIPTS_DIR" "$RESTORE_DB"
    else
        "$SCRIPTS_DIR/restore.sh" --db "$RESTORE_DB" --yes --no-start \
            || die "数据库还原失败，已中止（代码尚未切换）"
        info "数据库已还原: ${RESTORE_DB}"
    fi
else
    info "未指定 --restore-db，保持数据库现状（情况 A / B）"
fi

# ------------------------------------------------------------
# 6) 切软链 + 回退依赖
# ------------------------------------------------------------
log "3/7 原子切换 current 软链"
# 旧版本的代码在切换前再校验一次完整性（防止有人手工改过 release 目录）
if [ -f "$TARGET/SHA256SUMS" ]; then
    if [ "$DRY_RUN" -eq 0 ]; then
        verify_sums "$TARGET" || die "目标 release 的 SHA256SUMS 校验失败，拒绝切到可能被改动的代码"
        info "目标 release 校验通过"
    fi
fi
if [ "$MV_T" -eq 1 ]; then
    run ln -sfn "$TARGET" "$APP_LINK.new" || die "建立新软链失败"
    run mv -Tf "$APP_LINK.new" "$APP_LINK" || die "原子切换软链失败"
else
    warn "当前 mv 不支持 -T（非 GNU coreutils，例如 macOS 演练机）：退化为 ln -sfn 直接替换软链"
    run ln -sfn "$TARGET" "$APP_LINK" || die "切换软链失败"
fi
if [ "$DRY_RUN" -eq 0 ]; then
    info "current -> $(resolve_link "$APP_LINK")"
fi
# 同步运维脚本（脚本随版本走；旧版本的脚本更匹配旧版本的服务行为）
if [ -d "$TARGET/scripts" ]; then
    run cp -a "$TARGET/scripts/." "$SCRIPTS_DIR/"
    run chmod +x "$SCRIPTS_DIR"/*.sh
fi

log "4/7 回退 venv 依赖"
if [ "$NO_DEPS" -eq 1 ]; then
    warn "--no-deps：跳过依赖回退（仅当确认新旧依赖一致时使用）"
elif [ ! -f "$TARGET/requirements/requirements.lock.txt" ]; then
    warn "目标 release 没有 requirements.lock.txt，跳过依赖回退"
else
    ARCH="$(uname -m)"
    case "$ARCH" in
        x86_64|amd64)  WHEELHOUSE="$TARGET/wheelhouse-x86_64" ;;
        aarch64|arm64) WHEELHOUSE="$TARGET/wheelhouse-aarch64" ;;
        *) die "不支持的架构: ${ARCH}" ;;
    esac
    if [ -d "$WHEELHOUSE" ]; then
        OFFLINE_FLAGS=(--no-index "--find-links=$WHEELHOUSE")
    else
        OFFLINE_FLAGS=()
        warn "目标 release 内没有 wheelhouse，将联网回退依赖"
    fi
    run "$VENV/bin/python" -m pip install ${OFFLINE_FLAGS[@]+"${OFFLINE_FLAGS[@]}"} \
        --upgrade --requirement "$TARGET/requirements/requirements.lock.txt" \
        || die "依赖回退失败，已中止（代码已切到目标版本，请人工处理）"
    [ "$DRY_RUN" -eq 0 ] && { "$VENV/bin/python" -m pip check || warn "pip check 有输出，请人工确认"; }
fi

# ------------------------------------------------------------
# 7) 校验 + 起服务
# ------------------------------------------------------------
log "5/7 校验数据库与目标代码是否匹配"
if [ "$DRY_RUN" -eq 0 ]; then
    NEW_DB_REV="$(sqlite3 "$DB_FILE" 'SELECT version_num FROM alembic_version;' 2>/dev/null | head -n1 || true)"
    info "数据库 revision: ${NEW_DB_REV:-unknown}"
    (
        set -a
        # shellcheck disable=SC1090  # 动态路径 source，shellcheck 无法静态跟踪
        . "$ENV_FILE"
        set +a
        cd "$APP_LINK" || exit 1
        "$VENV/bin/alembic" current
    ) || warn "alembic current 执行失败（数据库可能已按 --restore-db 还原，属预期）"
    if [ "$NEW_DB_REV" != "$DB_REV" ]; then
        info "数据库 revision 已从 ${DB_REV} 变为 ${NEW_DB_REV:-unknown}"
    fi
fi

log "6/7 启动服务并等待就绪"
if [ "$SKIP_SYSTEMD" = "1" ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '[rollback] $ ( cd %s && nohup %s/bin/uvicorn app.main:app ... & )\n' "$APP_LINK" "$VENV"
    else
        (
            set -a
            # shellcheck disable=SC1090  # 动态路径 source，shellcheck 无法静态跟踪
            . "$ENV_FILE"
            set +a
            mkdir -p "$LOG_DIR"
            cd "$APP_LINK" || exit 1
            nohup "$VENV/bin/uvicorn" app.main:app \
                --host "${LOCALCRAFT_HOST:-127.0.0.1}" \
                --port "${LOCALCRAFT_PORT:-8000}" \
                --workers 1 \
                --proxy-headers \
                --forwarded-allow-ips 127.0.0.1 \
                --timeout-graceful-shutdown 45 \
                >> "$LOG_DIR/localcraft-stdout.log" 2>&1 &
            echo $! > "$PID_FILE"
        )
        info "已用 nohup 拉起（PID $(cat "$PID_FILE" 2>/dev/null || echo '?')）"
    fi
else
    run systemctl start localcraft.service || die "systemctl start 失败"
fi

if [ "$DRY_RUN" -eq 1 ]; then
    printf '[rollback] $ %s/wait-healthy.sh --url %s --timeout 60\n' "$SCRIPTS_DIR" "$HEALTH_URL"
else
    "$SCRIPTS_DIR/wait-healthy.sh" --url "$HEALTH_URL" --timeout 60 \
        || die "服务 60 秒内未通过 ${HEALTH_URL}，请看 ${LOG} 与 journalctl -u localcraft.service -n 100"
fi

# ------------------------------------------------------------
log "7/7 回滚结果"
if [ "$DRY_RUN" -eq 0 ] && command -v curl >/dev/null 2>&1; then
    META="$(curl -fsS --max-time 5 "http://127.0.0.1:${LOCALCRAFT_PORT:-8000}/api/v1/meta" 2>/dev/null || true)"
    if [ -n "$META" ]; then
        if command -v jq >/dev/null 2>&1; then
            printf '%s\n' "$META" | jq . || printf '%s\n' "$META"
        else
            printf '%s\n' "$META"
        fi
    else
        warn "/api/v1/meta 暂时取不到，请人工确认"
    fi
fi
echo
echo "  版本: ${CURRENT_VERSION} -> ${TARGET_VERSION}"
echo "  代码: ${TARGET}"
echo "  日志: ${LOG}"
if [ -z "$RESTORE_DB" ] && [ "$DB_AHEAD" -eq 1 ]; then
    printf '\033[1;33m';
    echo "  ⚠ 数据库仍比目标代码新（--allow-db-ahead 已生效）。"
    echo "    请立即用真实的读写操作验证目标代码不会破坏数据；"
    echo "    若发现异常，用 restore.sh 还原升级前备份（这会丢弃升级后的新数据）。"
    printf '\033[0m\n'
fi
echo
echo "============================================================"
echo "回滚完成。"
echo "============================================================"
exit 0
