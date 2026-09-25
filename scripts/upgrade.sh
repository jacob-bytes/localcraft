#!/usr/bin/env bash
# ============================================================
# scripts/upgrade.sh — localcraft 升级封装
#
# 流程（docs/05 §11.2 的 9 步）:
#   1) 停服务
#   2) 升级前备份（数据库 .backup + files/ + env 文件 + journal 尾部）
#   3) 解压新发布包并校验 SHA256SUMS / wheelhouse
#   4) 原子切换 current 软链（ln -sfn + mv -Tf）并同步运维脚本
#   5) 从新 wheelhouse 更新 venv 依赖
#   6) alembic upgrade head
#   7) 起服务并等待 /readyz
#   8) 结果验证（/api/v1/meta、经 nginx 的 /healthz、SPA fallback）
#   9) 收尾提示（旧 release 目录保留，清理命令只打印不执行）
#
# 用法:
#   upgrade.sh <发布包.tar.gz> [--yes] [--dry-run] [--skip-migrate] [--notes <path>]
#
# 关键设计：
#   * **失败不自动回滚**。自动回滚在"迁移做了一半"的状态上很容易造成二次事故：
#     代码切回旧版、数据库却停在新版结构中间态，比停在"新代码 + 已迁移"更难诊断。
#     所以失败时只打印**可直接复制执行**的回滚命令，把决定权交给人（docs/05 §11.3）。
#   * 每一步失败都立刻停止，不做"继续往下试试"。
#   * 读 RELEASE-NOTES.md 判断"本次是否含数据库迁移"，含迁移时给出醒目提示
#     并要求二次确认（--yes 可跳过），避免运维在不知情的情况下动库结构。
#
# 依据: docs/05《部署与运维方案》§11.1 / §11.2 / §11.3、§5.1、§5.3
# ============================================================
set -uo pipefail

# ---------- 路径（与 install.sh 同一套变量名与默认值）----------
PREFIX="${LOCALCRAFT_PREFIX:-/opt/localcraft}"
DATA_DIR="${LOCALCRAFT_DATA_DIR:-/var/lib/localcraft}"
ETC_DIR="${LOCALCRAFT_ETC_DIR:-/etc/localcraft}"
LOG_DIR="${LOCALCRAFT_LOG_DIR:-/var/log/localcraft}"

ENV_FILE="$ETC_DIR/localcraft.env"
VENV="$PREFIX/venv"
APP_LINK="$PREFIX/app/current"
RELEASES_DIR="$PREFIX/releases"
SCRIPTS_DIR="$PREFIX/scripts"
BACKUP_ROOT="${LOCALCRAFT_BACKUP_DIR:-$DATA_DIR/backups}"
DB_FILE="${LOCALCRAFT_DB:-$DATA_DIR/localcraft.db}"
FILES_DIR="${LOCALCRAFT_FILES_DIR:-$DATA_DIR/files}"
PID_FILE="$DATA_DIR/localcraft.pid"
HEALTH_URL="http://127.0.0.1:${LOCALCRAFT_PORT:-8000}/readyz"

SKIP_SYSTEMD="${LOCALCRAFT_SKIP_SYSTEMD:-0}"
ASSUME_YES=0
DRY_RUN=0
SKIP_MIGRATE=0
NOTES_OVERRIDE=""

log()  { printf '\n\033[1;32m[upgrade] %s\033[0m\n' "$*"; }
info() { printf '[upgrade] %s\n' "$*"; }
warn() { printf '[upgrade][warn] %s\n' "$*" >&2; }
die()  { printf '[upgrade][error] %s\n' "$*" >&2; exit 1; }

# 所有"会改动系统"的动作都过 run()：dry-run 与真跑共用一条代码路径，
# 保证 --dry-run 打印的就是真正会执行的东西。
run() {
    printf '[upgrade] $ %s\n' "$*"
    [ "$DRY_RUN" -eq 1 ] && return 0
    "$@"
}

usage() { sed -n '2,30p' "$0"; }

# ------------------------------------------------------------
# 0) 参数与前置检查
# ------------------------------------------------------------
TARBALL=""
while [ $# -gt 0 ]; do
    case "$1" in
        --yes|-y)        ASSUME_YES=1; shift ;;
        --dry-run|-n)    DRY_RUN=1; shift ;;
        --skip-migrate)  SKIP_MIGRATE=1; shift ;;
        --notes)         NOTES_OVERRIDE="$2"; shift 2 ;;
        -h|--help)       usage; exit 0 ;;
        -*)              die "未知参数: $1（用 --help 查看用法）" ;;
        *)               TARBALL="$1"; shift ;;
    esac
done

[ -n "$TARBALL" ] || die "用法: $(basename "$0") <发布包.tar.gz> [--yes] [--dry-run]"
[ -f "$TARBALL" ] || die "发布包不存在: ${TARBALL}"

if [ "$SKIP_SYSTEMD" = "1" ]; then
    warn "LOCALCRAFT_SKIP_SYSTEMD=1 —— 演练模式：不调用 systemctl，改用 pid 文件 + nohup 拉起服务"
elif [ "$DRY_RUN" -eq 0 ]; then
    [ "$(id -u)" -eq 0 ] || die "请以 root 执行"
fi

for c in tar sqlite3 rsync; do
    command -v "$c" >/dev/null 2>&1 || die "缺少命令: ${c}"
done
[ -f "$ENV_FILE" ] || die "找不到环境变量文件: ${ENV_FILE}"
[ -x "$VENV/bin/python" ] || die "找不到 venv: ${VENV}/bin/python"
[ -e "$APP_LINK" ] || die "找不到 current 软链: ${APP_LINK}"

# GNU coreutils 的 sha256sum 与 macOS/BSD 的 shasum -a 256 都能 -c 校验；
# 存成数组是为了避免 shellcheck SC2086（"shasum -a 256" 含空格）。
if command -v sha256sum >/dev/null 2>&1; then
    SHA256_BIN=(sha256sum)
elif command -v shasum >/dev/null 2>&1; then
    SHA256_BIN=(shasum -a 256)
else
    die "缺少 sha256sum / shasum"
fi

# `mv -T`（--no-target-directory）是 GNU coreutils 选项：它是"原子替换软链"的关键。
# 目标机 openEuler 一定有；演练机（macOS）没有，此时退化为 `ln -sfn` 直接替换
# （BSD ln 对软链目标本身就是替换语义，只是不如 rename(2) 原子）。
MV_T=0
if mv --help 2>/dev/null | grep -q -- '--no-target-directory'; then
    MV_T=1
fi

resolve_link() {  # 解析软链为目标绝对路径；GNU readlink -f 优先，BSD 回退
    if readlink -f "$1" >/dev/null 2>&1; then
        readlink -f "$1"
    else
        ( cd "$1" 2>/dev/null && pwd -P )
    fi
}

verify_sums() {  # verify_sums <目录>
    ( cd "$1" && "${SHA256_BIN[@]}" -c SHA256SUMS --quiet 2>/dev/null \
        || "${SHA256_BIN[@]}" -c SHA256SUMS >/dev/null )
}

STAMP="$(date +%Y%m%d-%H%M%S)"
PREV_LINK="$(resolve_link "$APP_LINK" || true)"
PREV_VERSION="$(cat "$APP_LINK/VERSION" 2>/dev/null || echo unknown)"
PREV_REV="$(sqlite3 "$DB_FILE" 'SELECT version_num FROM alembic_version;' 2>/dev/null | head -n1 || true)"

# 备份/回滚提示都依赖这两个变量，先算出来
BACKUP_DB="$BACKUP_ROOT/db/pre-upgrade-${PREV_VERSION}-${STAMP}.db"
BACKUP_FILES="$BACKUP_ROOT/files/pre-upgrade-${PREV_VERSION}-${STAMP}"
BACKUP_ENV="$BACKUP_ROOT/pre-upgrade-localcraft-${STAMP}.env"

# 升级日志：只有真正动手时才落盘（dry-run 不该在 /var/log 留文件）
LOG="(未启用)"
if [ "$DRY_RUN" -eq 0 ]; then
    if [ -d "$LOG_DIR" ] && [ -w "$LOG_DIR" ]; then
        LOG="$LOG_DIR/localcraft-upgrade-${STAMP}.log"
    else
        LOG="/tmp/localcraft-upgrade-${STAMP}.log"
    fi
    exec > >(tee -a "$LOG") 2>&1
fi

echo "============================================================"
echo "localcraft 升级"
echo "  当前版本   : ${PREV_VERSION}"
echo "  当前代码   : ${PREV_LINK}"
echo "  当前迁移版本: ${PREV_REV:-（读不到，可能是空库）}"
echo "  升级日志   : ${LOG}"
echo "  发布包     : ${TARBALL}"
echo "============================================================"

# ------------------------------------------------------------
# 先探清发布包内容（不改动系统），以便提前告知"是否含迁移"
# ------------------------------------------------------------
TOP="$(tar -tzf "$TARBALL" | sed -n '1p' | cut -d/ -f1 || true)"
[ -n "$TOP" ] || die "无法读取发布包目录结构: ${TARBALL}"
NEW_DIR="$RELEASES_DIR/$TOP"

# 从包里直接读 VERSION / RELEASE-NOTES，无需解压
NEW_VERSION="$(tar -xzOf "$TARBALL" "${TOP}/VERSION" 2>/dev/null | head -n1 || true)"
[ -n "$NEW_VERSION" ] || NEW_VERSION="${TOP#localcraft-}"

NOTES_TMP=""
if [ -n "$NOTES_OVERRIDE" ]; then
    NOTES_FILE="$NOTES_OVERRIDE"
elif tar -tzf "$TARBALL" 2>/dev/null | grep -qx "${TOP}/RELEASE-NOTES.md"; then
    # 即使 dry-run 也真的把 RELEASE-NOTES.md 抽到 /tmp 再判定：
    # 「是否含迁移」是决定这次升级能不能只做 dry-run 的关键信息，
    # 只写一句"将会读取"等于没判定。写 /tmp 的临时文件不属于改动系统。
    NOTES_TMP="${TMPDIR:-/tmp}/localcraft-notes-${STAMP}.md"
    tar -xzOf "$TARBALL" "${TOP}/RELEASE-NOTES.md" > "$NOTES_TMP" 2>/dev/null || NOTES_TMP=""
    NOTES_FILE="$NOTES_TMP"
else
    NOTES_FILE=""
    warn "发布包里没有 RELEASE-NOTES.md，无法自动判定是否含数据库迁移"
    warn '升级脚本会按「可能含迁移」处理；如有把握可用 --notes <path> 指定'
fi

# detect_migration <notes 文件> -> yes / no / unknown
# 依赖 RELEASE-NOTES.md 里以**行首**开始的 `本次是否包含数据库迁移：是|否` 关键行
# （见 RELEASE-NOTES.md 开头的说明，改格式会让这里失效）。
detect_migration() {
    local notes="$1" line="" value=""
    [ -n "$notes" ] && [ -f "$notes" ] || { echo unknown; return 0; }
    line="$(grep -m1 '^本次是否包含数据库迁移' "$notes" 2>/dev/null || true)"
    [ -n "$line" ] || { echo unknown; return 0; }
    # 必须只取冒号**之后**的值再判断：行首的「是否」里自带一个「否」，
    # 直接对整行做 *否* 匹配会把"是"误判成"否"（实测踩到过）。
    value="$line"
    case "$line" in
        *：*) value="${line#*：}" ;;
        *:*)  value="${line#*:}" ;;
    esac
    # 去掉空白与 markdown 强调符（**是** / `是`），再要求以「是」或「否」开头。
    # 判不出来一律返回 unknown —— 调用方按"可能含迁移"这一保守分支处理。
    value="$(printf '%s' "$value" | tr -d ' *`')"
    case "$value" in
        否*) echo no ;;
        是*) echo yes ;;
        *)   echo unknown ;;
    esac
}

MIGRATION="$(detect_migration "$NOTES_FILE")"
case "$MIGRATION" in
    yes|unknown) MIGRATION_CONFIRM_NEEDED=1 ;;
    no)          MIGRATION_CONFIRM_NEEDED=0 ;;
esac

echo
echo "  新版本     : ${NEW_VERSION}"
echo "  新代码目录 : ${NEW_DIR}"
case "$MIGRATION" in
    yes)
        printf '\n\033[1;33m';
        echo "  ⚠⚠ 本次升级【包含数据库迁移】（RELEASE-NOTES.md 判定）⚠⚠"
        echo "     迁移在服务停止后执行；执行前会自动做一份 pre-upgrade 备份。"
        echo "     回滚的第一选择是还原这份备份，而不是 alembic downgrade（docs/05 §11.4）。"
        printf '\033[0m\n'
        ;;
    no)
        echo "  数据库迁移 : 本次发布包声明【不含】数据库迁移（仍会执行 alembic upgrade head，应为空操作）"
        ;;
    unknown)
        printf '\n\033[1;33m';
        echo "  ⚠ 无法判定本次是否含数据库迁移（缺少/无法解析 RELEASE-NOTES.md）"
        echo "    按「可能含迁移」处理：会执行 alembic upgrade head，并提醒你保留备份。"
        printf '\033[0m\n'
        ;;
esac

# ------------------------------------------------------------
# 确认
# ------------------------------------------------------------
if [ "$ASSUME_YES" -ne 1 ] && [ "$DRY_RUN" -ne 1 ]; then
    printf '确认执行升级请输入 upgrade（其它任何输入都会取消）：'
    read -r ANSWER
    [ "$ANSWER" = "upgrade" ] || die "已取消"
    if [ "$MIGRATION_CONFIRM_NEEDED" -eq 1 ] && [ "$SKIP_MIGRATE" -eq 0 ]; then
        printf '本次含（或可能含）数据库迁移，确认继续动库结构请输入 migrate：'
        read -r ANSWER2
        [ "$ANSWER2" = "migrate" ] || die "已取消（未确认迁移）"
    fi
fi

# ------------------------------------------------------------
# 失败时的回滚指引（只提示，不自动执行）
# ------------------------------------------------------------
rollback_hint() {
    cat <<EOF

============================================================
升级未完成。**脚本不会自动回滚**，请按下面的顺序处置：

 1) 停服务
      systemctl stop localcraft.service

 2) 查看失败点（先别急着回滚，很多问题看一眼日志就能修）
      journalctl -u localcraft.service -n 200 --no-pager
      ${LOG}

 3) 回滚代码（自动选上一个 release；也可显式 --to）
      ${SCRIPTS_DIR}/rollback.sh --yes
      # 或 ${SCRIPTS_DIR}/rollback.sh --to ${PREV_LINK} --yes

 4) 若已执行迁移且新迁移是破坏性的，必须还原升级前备份：
      ${SCRIPTS_DIR}/restore.sh --db ${BACKUP_DB} --files ${BACKUP_FILES} --yes

 5) 起服务并确认
      systemctl start localcraft.service
      ${SCRIPTS_DIR}/wait-healthy.sh --url ${HEALTH_URL} --timeout 90

 升级前状态：版本 ${PREV_VERSION}，代码 ${PREV_LINK}，迁移 ${PREV_REV:-未知}
 备份产物：${BACKUP_ROOT}
============================================================
EOF
}

fail() {
    printf '\n\033[1;31m[upgrade][error] %s\033[0m\n' "$*" >&2
    rollback_hint
    exit 1
}

# ------------------------------------------------------------
log "1/9 停止服务"
stop_service() {
    if [ "$SKIP_SYSTEMD" = "1" ]; then
        if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
            run kill "$(cat "$PID_FILE")" 2>/dev/null || true
            for _ in $(seq 1 30); do
                [ "$DRY_RUN" -eq 1 ] && break
                kill -0 "$(cat "$PID_FILE")" 2>/dev/null || break
                sleep 1
            done
        fi
        return 0
    fi
    run systemctl stop localcraft.service || warn "systemctl stop 返回非零（服务可能本就没在跑），继续"
    # 确认进程真的退出了：文件句柄若仍被占用，后面切软链/装依赖会出怪问题
    if command -v pgrep >/dev/null 2>&1; then
        if pgrep -f 'uvicorn app.main:app' >/dev/null 2>&1; then
            [ "$DRY_RUN" -eq 1 ] || sleep 3
            if pgrep -f 'uvicorn app.main:app' >/dev/null 2>&1; then
                fail "uvicorn 进程未退出，拒绝在服务仍在运行时升级"
            fi
        fi
    fi
    return 0
}
stop_service || exit 1
info "服务已停止"

# ------------------------------------------------------------
log "2/9 升级前备份（数据库 / files / 环境变量 / journal）"
# 2.1 数据库一致性快照。服务已停，但 .backup 仍是最稳的方式：
#     它会把 -wal 里未 checkpoint 的内容一并折进快照，产物自包含。
run mkdir -p "$BACKUP_ROOT/db" "$BACKUP_ROOT/files"
run sqlite3 "$DB_FILE" ".backup '${BACKUP_DB}'" || fail "数据库备份失败"
if [ "$DRY_RUN" -eq 0 ]; then
    CHECK="$(sqlite3 "$BACKUP_DB" 'PRAGMA integrity_check;' 2>&1 || true)"
    [ "$CHECK" = "ok" ] || fail "升级前备份 integrity_check 未通过: ${CHECK}"
    info "数据库快照校验通过: $(basename "$BACKUP_DB")"
fi
# 2.2 files 目录完整快照（服务已停，不存在一致性风险，用 rsync 保留硬链接属性最省事）
run rsync -a --delete "$FILES_DIR/" "$BACKUP_FILES/" || fail "files 目录备份失败"
# 2.3 环境变量文件（含 SECRET_KEY，必须保密，权限 0600）
run install -m 0600 "$ENV_FILE" "$BACKUP_ENV" || fail "环境变量文件备份失败"
# 2.4 journal 尾部（升级前最后的现场，出问题时能对照"升级前是否已经不正常"）
if command -v journalctl >/dev/null 2>&1; then
    run sh -c "journalctl -u localcraft.service -n 200 --no-pager > '${BACKUP_ROOT}/pre-upgrade-journal-${STAMP}.log' 2>/dev/null" \
        || warn "journalctl 导出失败（不影响升级）"
fi
info "备份完成：${BACKUP_ROOT}"

# ------------------------------------------------------------
log "3/9 解压并校验新发布包"
run mkdir -p "$RELEASES_DIR"
run tar -C "$RELEASES_DIR" -xzf "$TARBALL" || fail "解压发布包失败"
if [ "$DRY_RUN" -eq 0 ]; then
    verify_sums "$NEW_DIR" || fail "发布包 SHA256SUMS 校验失败: ${NEW_DIR}"
    info "发布包校验通过"
    # 双重确认：包里 VERSION 与解压目录里的 VERSION 必须一致
    [ "$(cat "$NEW_DIR/VERSION" 2>/dev/null || echo '')" = "$NEW_VERSION" ] \
        || warn "包内 VERSION 与目录名推断不一致，以 ${NEW_DIR}/VERSION 为准"
fi

ARCH="$(uname -m)"
case "$ARCH" in
    x86_64|amd64)  WHEELHOUSE="$NEW_DIR/wheelhouse-x86_64"; WH_NAME="wheelhouse-x86_64" ;;
    aarch64|arm64) WHEELHOUSE="$NEW_DIR/wheelhouse-aarch64"; WH_NAME="wheelhouse-aarch64" ;;
    *) fail "不支持的架构: ${ARCH}" ;;
esac
# dry-run 时新版本还没解压，所以"有没有 wheelhouse"要从**包清单**里看，
# 而不是去看解压后的目录 —— 否则会出现"演练说缺 wheelhouse、真跑其实有"的假象。
HAVE_WHEELHOUSE=0
if [ "$DRY_RUN" -eq 1 ]; then
    if tar -tzf "$TARBALL" 2>/dev/null | grep -q "^${TOP}/${WH_NAME}/"; then
        HAVE_WHEELHOUSE=1
    fi
elif [ -d "$WHEELHOUSE" ]; then
    HAVE_WHEELHOUSE=1
fi
if [ "$HAVE_WHEELHOUSE" -eq 1 ]; then
    if [ "$DRY_RUN" -eq 0 ]; then
        verify_sums "$WHEELHOUSE" || fail "wheelhouse 校验失败: ${WHEELHOUSE}"
    fi
    OFFLINE_FLAGS=(--no-index "--find-links=$WHEELHOUSE")
    info "使用离线 wheelhouse: ${WHEELHOUSE}"
else
    OFFLINE_FLAGS=()
    warn "发布包内没有 wheelhouse，将退化为联网 pip 安装（完全离线环境请先构建 wheelhouse）"
fi

# ------------------------------------------------------------
log "4/9 原子切换 current 软链"
# 先建 current.new 再 mv -Tf 覆盖：rename(2) 是原子的，
# 任何时刻 current 都指向一个完整目录，不会出现"半切换"窗口。
if [ "$MV_T" -eq 1 ]; then
    run ln -sfn "$NEW_DIR" "$APP_LINK.new" || fail "建立新软链失败"
    run mv -Tf "$APP_LINK.new" "$APP_LINK" || fail "原子切换软链失败"
else
    warn "当前 mv 不支持 -T（非 GNU coreutils，例如 macOS 演练机）：退化为 ln -sfn 直接替换软链"
    run ln -sfn "$NEW_DIR" "$APP_LINK" || fail "切换软链失败"
fi
if [ "$DRY_RUN" -eq 0 ]; then
    info "current -> $(resolve_link "$APP_LINK")"
    [ "$(resolve_link "$APP_LINK")" = "$NEW_DIR" ] || fail "软链切换后指向不符，已中止"
fi
# 运维脚本也可能随版本更新，一并同步（否则新版本的服务会用旧脚本）
if [ -d "$NEW_DIR/scripts" ] || [ "$DRY_RUN" -eq 1 ]; then
    run cp -a "$NEW_DIR/scripts/." "$SCRIPTS_DIR/"
    run chmod +x "$SCRIPTS_DIR"/*.sh
fi

# ------------------------------------------------------------
log "5/9 更新 venv 依赖"
# 先升级工具链（离线）。老发布包的 wheelhouse 可能没有 bootstrap 包，
# 此时只告警不中断：venv 自带版本足够完成后面的安装。
run "$VENV/bin/python" -m pip install --quiet ${OFFLINE_FLAGS[@]+"${OFFLINE_FLAGS[@]}"} --upgrade pip setuptools wheel \
    || warn "pip/setuptools/wheel 升级失败（wheelhouse 可能缺 bootstrap 包），继续"
LOCK="$NEW_DIR/requirements/requirements.lock.txt"
LOCK_REL="${TOP}/requirements/requirements.lock.txt"
if [ "$DRY_RUN" -eq 1 ]; then
    # 同上：dry-run 只能从包清单判断依赖清单是否存在
    tar -tzf "$TARBALL" 2>/dev/null | grep -qx "$LOCK_REL" \
        || fail "发布包内缺少依赖锁定清单: ${LOCK_REL}"
else
    [ -f "$LOCK" ] || fail "缺少依赖锁定清单: ${LOCK}"
fi
# --upgrade 让版本按新 lock 对齐；不加 --force-reinstall，避免无谓地重装全部包
run "$VENV/bin/python" -m pip install ${OFFLINE_FLAGS[@]+"${OFFLINE_FLAGS[@]}"} --upgrade --requirement "$LOCK" \
    || fail "依赖安装失败"
if [ "$DRY_RUN" -eq 0 ]; then
    "$VENV/bin/python" -m pip check || warn "pip check 报告依赖冲突，请人工确认（不阻断升级）"
fi

# ------------------------------------------------------------
log "6/9 数据库迁移"
if [ "$MIGRATION_CONFIRM_NEEDED" -eq 1 ]; then
    info "RELEASE-NOTES 判定：含（或无法排除）数据库迁移 —— 迁移前备份已就绪"
fi
if [ "$SKIP_MIGRATE" -eq 1 ]; then
    warn "--skip-migrate：跳过 alembic upgrade head（仅当确认本次不含迁移时使用！）"
elif [ "$DRY_RUN" -eq 1 ]; then
    printf '[upgrade] $ ( cd %s && set -a; . %s; set +a; %s/bin/alembic upgrade head )\n' \
        "$APP_LINK" "$ENV_FILE" "$VENV"
else
    (
        set -a
        # shellcheck disable=SC1090  # 动态路径 source，shellcheck 无法静态跟踪
        . "$ENV_FILE"
        set +a
        cd "$APP_LINK" || exit 1
        echo "--- 迁移前版本 ---"
        "$VENV/bin/alembic" current
        echo "--- 待执行迁移 ---"
        "$VENV/bin/alembic" history -r current:head
        echo "--- 执行 upgrade head ---"
        "$VENV/bin/alembic" upgrade head
        echo "--- 迁移后版本 ---"
        "$VENV/bin/alembic" current
    ) || fail "数据库迁移失败（回滚请优先还原 ${BACKUP_DB}）"
fi

# ------------------------------------------------------------
log "7/9 启动服务并等待就绪"
start_service() {
    if [ "$SKIP_SYSTEMD" = "1" ]; then
        if [ "$DRY_RUN" -eq 1 ]; then
            printf '[upgrade] $ ( cd %s && nohup %s/bin/uvicorn app.main:app ... & )\n' "$APP_LINK" "$VENV"
            return 0
        fi
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
        return 0
    fi
    run systemctl start localcraft.service || fail "systemctl start 失败"
    return 0
}
start_service || exit 1

if [ "$DRY_RUN" -eq 1 ]; then
    printf '[upgrade] $ %s/wait-healthy.sh --url %s --timeout 90\n' "$SCRIPTS_DIR" "$HEALTH_URL"
else
    "$SCRIPTS_DIR/wait-healthy.sh" --url "$HEALTH_URL" --timeout 90 \
        || fail "服务 90 秒内未通过 ${HEALTH_URL}"
fi

# ------------------------------------------------------------
log "8/9 结果验证"
if [ "$DRY_RUN" -eq 1 ]; then
    printf '[upgrade] $ curl -fsS http://127.0.0.1:%s/api/v1/meta\n' "${LOCALCRAFT_PORT:-8000}"
    echo '[upgrade] $ curl -o /dev/null -w "经 nginx /healthz HTTP %%{http_code}" http://127.0.0.1/healthz'
else
    if command -v curl >/dev/null 2>&1; then
        META="$(curl -fsS --max-time 5 "http://127.0.0.1:${LOCALCRAFT_PORT:-8000}/api/v1/meta" 2>/dev/null || true)"
        if [ -n "$META" ]; then
            if command -v jq >/dev/null 2>&1; then
                printf '%s\n' "$META" | jq . || printf '%s\n' "$META"
            else
                printf '%s\n' "$META"
            fi
        else
            warn "/api/v1/meta 暂时取不到（不影响升级判定，请人工确认）"
        fi
        NGINX_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1/healthz 2>/dev/null || echo 000)"
        info "经 nginx 的 /healthz: HTTP ${NGINX_CODE}"
        SPA_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1/tools/1 2>/dev/null || echo 000)"
        info "SPA fallback (/tools/1): HTTP ${SPA_CODE}"
    else
        warn "缺少 curl，跳过 HTTP 验证"
    fi
fi

# ------------------------------------------------------------
log "9/9 升级结果"
NEW_REV="$(sqlite3 "$DB_FILE" 'SELECT version_num FROM alembic_version;' 2>/dev/null | head -n1 || true)"
echo
echo "  版本    : ${PREV_VERSION} -> ${NEW_VERSION}"
echo "  迁移版本: ${PREV_REV:-未知} -> ${NEW_REV:-未知}"
echo "  代码    : ${NEW_DIR}"
echo "  日志    : ${LOG}"
echo
echo "  旧版本目录仍保留在 ${PREV_LINK}（确认稳定后再清理）"
echo "  观察 10 分钟后，确认无误再清理过老版本："
echo "      ls -1dt ${RELEASES_DIR}/localcraft-* | tail -n +4   # 先看要删哪些"
echo "      ls -1dt ${RELEASES_DIR}/localcraft-* | tail -n +4 | xargs -r rm -rf"
echo "  升级前备份保留在 ${BACKUP_ROOT}（至少留到观察期结束）"
echo
echo "============================================================"
echo "升级完成。"
echo "============================================================"
exit 0
