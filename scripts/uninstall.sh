#!/usr/bin/env bash
# ============================================================
# scripts/uninstall.sh — localcraft 卸载脚本
#
# 设计立场（很重要）：**默认卸载"程序"，不卸载"数据"。**
#   - 停止并禁用 localcraft.service 与已启用的 localcraft-*.timer
#   - 移除 /etc/systemd/system/ 下本项目安装的 unit 与 drop-in
#   - 移除 /opt/localcraft（代码 + venv + 脚本 + releases）
#   - **保留** /var/lib/localcraft（数据库与 files/）与 /etc/localcraft/localcraft.env（含 SECRET_KEY）
#   - **保留** /var/log/localcraft（卸载后的日志是排障证据，不该顺手删掉）
#   只有显式传 --purge 才删除数据目录、配置目录与日志目录，并删除系统用户。
#
# 为什么默认保留数据：误删一次卸载就可能造成不可逆的数据丢失，而"重装再删"
# 的成本只是几分钟。破坏性动作必须由人显式要求，不能被默认值带出来。
#
# 用法:
#   scripts/uninstall.sh                  # 交互确认，保留数据与配置
#   scripts/uninstall.sh --yes            # 跳过交互（供自动化）
#   scripts/uninstall.sh --purge --yes    # 连数据/配置/日志一起删（不可恢复）
#   scripts/uninstall.sh --keep-code      # 只摘服务与 unit，保留 /opt/localcraft
#   scripts/uninstall.sh --dry-run        # 只打印将执行的步骤，不动系统
#
# 演练/无 systemd 环境（与 install.sh 同一套变量）:
#   LOCALCRAFT_SKIP_SYSTEMD=1 \
#   LOCALCRAFT_PREFIX=$PWD/sandbox/opt \
#   LOCALCRAFT_DATA_DIR=$PWD/sandbox/var \
#   LOCALCRAFT_ETC_DIR=$PWD/sandbox/etc \
#   LOCALCRAFT_LOG_DIR=$PWD/sandbox/log \
#   ./uninstall.sh --yes
#   此时不调用 systemctl、不动 /etc/systemd 与 /usr/lib/tmpfiles.d；
#   但仍会结束演练模式用 nohup 拉起的那条 uvicorn（靠 $DATA_DIR/localcraft.pid）。
#
# 依据: docs/05《部署与运维方案》§5（安装步骤的逆过程）、§12.6（权限）、§14.3（路径速查）
# ============================================================
set -uo pipefail

# ---------- 路径（可用环境变量覆盖，与 install.sh 同名同默认值）----------
PREFIX="${LOCALCRAFT_PREFIX:-/opt/localcraft}"
DATA_DIR="${LOCALCRAFT_DATA_DIR:-/var/lib/localcraft}"
ETC_DIR="${LOCALCRAFT_ETC_DIR:-/etc/localcraft}"
LOG_DIR="${LOCALCRAFT_LOG_DIR:-/var/log/localcraft}"

ENV_FILE="$ETC_DIR/localcraft.env"
PID_FILE="$DATA_DIR/localcraft.pid"
SKIP_SYSTEMD="${LOCALCRAFT_SKIP_SYSTEMD:-0}"
UNIT_DIR="${LOCALCRAFT_SYSTEMD_UNIT_DIR:-/etc/systemd/system}"
TMPFILES_FILE="${LOCALCRAFT_TMPFILES_FILE:-/usr/lib/tmpfiles.d/localcraft.conf}"

# 本项目安装的全部 unit。slice 也要 disable（它没有 [Install] 段，
# disable 只是幂等空操作，写进来是为了"清单完整、便于核对"）。
UNITS=(
    localcraft.service
    localcraft-backup.service
    localcraft-backup.timer
    localcraft-maintenance.service
    localcraft-maintenance.timer
    localcraft.slice
)
# 需要 disable 的定时器（service 是 oneshot，由 timer 拉起，不需要单独 enable）
TIMERS=(
    localcraft-backup.timer
    localcraft-maintenance.timer
)

PURGE=0
ASSUME_YES=0
KEEP_CODE=0
DRY_RUN=0

log()  { printf '[uninstall] %s\n' "$*"; }
warn() { printf '[uninstall][warn] %s\n' "$*" >&2; }
die()  { printf '[uninstall][error] %s\n' "$*" >&2; exit 1; }

usage() { sed -n '2,40p' "$0"; }

# 打印并（非 dry-run 时）执行。刻意用函数而不是 `set -x`：
# 输出格式统一，且 dry-run 与真跑走的是同一条代码路径，不会"演练过了但真跑走另一条路"。
run() {
    printf '[uninstall] $ %s\n' "$*"
    [ "$DRY_RUN" -eq 1 ] && return 0
    "$@"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --purge)      PURGE=1; shift ;;
        --yes|-y)     ASSUME_YES=1; shift ;;
        --keep-code)  KEEP_CODE=1; shift ;;
        --dry-run|-n) DRY_RUN=1; shift ;;
        -h|--help)    usage; exit 0 ;;
        *) die "未知参数: $1（用 --help 查看用法）" ;;
    esac
done

# ---------- 路径安全闸门 ----------
# rm -rf 的作用对象全部来自环境变量，一旦有人把它写成 "/" 或 "/opt"，
# 后果不是"卸载失败"而是"系统被删"。所以先做一次显式的健全性检查。
guard_path() {  # guard_path <变量名> <值>
    local name="$1" value="$2"
    case "$value" in
        ""|"/"|"/opt"|"/etc"|"/var"|"/var/lib"|"/var/log"|"/usr"|"/usr/lib")
            die "${name} 取值不安全（${value}），拒绝执行删除" ;;
    esac
}

if [ "$PURGE" -eq 1 ]; then
    guard_path LOCALCRAFT_DATA_DIR "$DATA_DIR"
    guard_path LOCALCRAFT_ETC_DIR "$ETC_DIR"
    guard_path LOCALCRAFT_LOG_DIR "$LOG_DIR"
fi

if [ "$SKIP_SYSTEMD" = "1" ]; then
    warn "LOCALCRAFT_SKIP_SYSTEMD=1 —— 演练模式：不调用 systemctl，不动 /etc/systemd 与 ${TMPFILES_FILE}"
    if [ "$PURGE" -eq 1 ]; then
        die "演练模式不支持 --purge（数据目录是沙箱路径，删除没有意义且容易误伤）"
    fi
elif [ "$DRY_RUN" -eq 0 ]; then
    [ "$(id -u)" -eq 0 ] || die "请以 root 执行（或设 LOCALCRAFT_SKIP_SYSTEMD=1 仅做演练）"
fi

# ------------------------------------------------------------
# 1) 确认
# ------------------------------------------------------------
printf '\n即将执行卸载：\n'
printf '  systemd 单元目录 : %s\n' "$UNIT_DIR"
printf '  tmpfiles 配置    : %s\n' "$TMPFILES_FILE"
printf '  程序目录         : %s\n' "$PREFIX"
printf '  数据目录         : %s\n' "$DATA_DIR"
printf '  配置文件         : %s\n' "$ENV_FILE"
printf '  日志目录         : %s\n' "$LOG_DIR"
printf '\n'
printf '  \033[1;32m保留\033[0m：%s（数据）、%s（配置）、%s（日志）\n' "$DATA_DIR" "$ENV_FILE" "$LOG_DIR"
printf '  \033[1;31m删除\033[0m：systemd unit%s、%s\n' \
    "$([ "$KEEP_CODE" -eq 1 ] && echo "（不动 ${PREFIX}）" || echo " 与 ${PREFIX}")" "$TMPFILES_FILE"
if [ "$PURGE" -eq 1 ]; then
    printf '\n  \033[1;31m⚠ --purge 已启用：数据目录、配置目录、日志目录与 localcraft 用户都会删除，不可恢复！\033[0m\n'
fi
if [ "$DRY_RUN" -eq 1 ]; then
    printf '\n  （--dry-run：下面只打印，不会真的执行）\n'
fi

if [ "$ASSUME_YES" -ne 1 ] && [ "$DRY_RUN" -ne 1 ]; then
    if [ "$PURGE" -eq 1 ]; then
        printf '\n确认请输入 purge（其它任何输入都会取消）：'
    else
        printf '\n确认请输入 uninstall（其它任何输入都会取消）：'
    fi
    read -r ANSWER
    if [ "$PURGE" -eq 1 ]; then
        [ "$ANSWER" = "purge" ] || die "已取消"
    else
        [ "$ANSWER" = "uninstall" ] || die "已取消"
    fi
fi

# ------------------------------------------------------------
# 2) 停止并禁用服务与定时器
# ------------------------------------------------------------
if [ "$SKIP_SYSTEMD" != "1" ]; then
    log "2/7 停止并禁用 systemd 单元"
    for u in "${UNITS[@]}"; do
        # disable --now：先断开自启，再停止。对当前未启用的单元是幂等空操作。
        run systemctl disable --now "$u" 2>/dev/null || true
    done
    for t in "${TIMERS[@]}"; do
        # 双保险：即使上面 disable 失败（例如 unit 文件已被手工删掉），
        # 这里也要确保定时器不再触发。
        run systemctl stop "$t" 2>/dev/null || true
    done
else
    log "2/7 跳过 systemctl（LOCALCRAFT_SKIP_SYSTEMD=1）"
fi

# 演练模式：结束 install.sh 用 nohup 拉起的那条 uvicorn
if [ -f "$PID_FILE" ]; then
    PID="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        log "结束演练模式进程（PID ${PID}）"
        run kill "$PID" 2>/dev/null || true
        # 等服务退出，避免后面删目录时句柄仍被占用
        for _ in $(seq 1 15); do
            kill -0 "$PID" 2>/dev/null || break
            [ "$DRY_RUN" -eq 1 ] && break
            sleep 1
        done
    fi
    run rm -f "$PID_FILE"
fi

# ------------------------------------------------------------
# 3) 移除 unit 文件与 drop-in
# ------------------------------------------------------------
if [ "$SKIP_SYSTEMD" != "1" ]; then
    log "3/7 移除 unit 文件与 drop-in 片段"
    for u in "${UNITS[@]}"; do
        [ -e "$UNIT_DIR/$u" ] && run rm -f "$UNIT_DIR/$u"
    done
    # drop-in 目录：只删本项目的 limits.conf，目录非空就不强删（可能有人放了别的片段）
    if [ -d "$UNIT_DIR/localcraft.service.d" ]; then
        [ -e "$UNIT_DIR/localcraft.service.d/limits.conf" ] && \
            run rm -f "$UNIT_DIR/localcraft.service.d/limits.conf"
        run rmdir "$UNIT_DIR/localcraft.service.d" 2>/dev/null || true
    fi
    run systemctl daemon-reload
    for u in "${UNITS[@]}"; do
        # 清掉因重启次数超限或启动失败留下的 failed 状态，否则同名单元再次安装会被污染
        run systemctl reset-failed "$u" 2>/dev/null || true
    done
else
    log "3/7 跳过 unit 移除（LOCALCRAFT_SKIP_SYSTEMD=1）"
fi

# ------------------------------------------------------------
# 4) 移除 systemd-tmpfiles 规则
# ------------------------------------------------------------
if [ "$SKIP_SYSTEMD" != "1" ]; then
    log "4/7 移除 tmpfiles 规则"
    if [ -e "$TMPFILES_FILE" ]; then
        run rm -f "$TMPFILES_FILE"
    else
        log "    未安装 ${TMPFILES_FILE}，跳过"
    fi
else
    log "4/7 跳过 tmpfiles 移除（LOCALCRAFT_SKIP_SYSTEMD=1）"
fi

# ------------------------------------------------------------
# 5) 移除程序目录
# ------------------------------------------------------------
if [ "$KEEP_CODE" -eq 1 ]; then
    log "5/7 --keep-code：保留 ${PREFIX}"
else
    guard_path LOCALCRAFT_PREFIX "$PREFIX"
    log "5/7 移除程序目录 ${PREFIX}（代码 + venv + scripts + releases）"
    run rm -rf "$PREFIX"
fi

# ------------------------------------------------------------
# 6) --purge：删除数据 / 配置 / 日志 / 系统用户
# ------------------------------------------------------------
if [ "$PURGE" -eq 1 ]; then
    log "6/7 --purge：删除数据、配置与日志"
    run rm -rf "$DATA_DIR" "$ETC_DIR" "$LOG_DIR"
    if id localcraft >/dev/null 2>&1; then
        run userdel localcraft 2>/dev/null || warn "userdel localcraft 失败（可能有进程仍在运行），请手工确认"
    fi
else
    log "6/7 保留数据与配置（未指定 --purge）"
fi

# ------------------------------------------------------------
# 7) 汇总：明确说清"保留了什么、删除了什么"，并给出恢复路径
# ------------------------------------------------------------
log "7/7 卸载结果"
echo
echo "============================================================"
echo "删除的内容"
printf '  - systemd 单元: %s\n' "${UNITS[*]}"
printf '                   （及 %s/localcraft.service.d/limits.conf）\n' "$UNIT_DIR"
printf '  - tmpfiles 规则: %s\n' "$TMPFILES_FILE"
if [ "$KEEP_CODE" -eq 1 ]; then
    printf '  - 程序目录:     （--keep-code，未删除）%s\n' "$PREFIX"
else
    printf '  - 程序目录:     %s\n' "$PREFIX"
fi
echo
echo "保留的内容"
if [ "$PURGE" -eq 1 ]; then
    echo "  - （--purge 已启用：数据、配置、日志与 localcraft 用户均已删除，无保留项）"
else
    printf '  - 数据目录:   %s   （数据库 localcraft.db、files/、backups/）\n' "$DATA_DIR"
    printf '  - 配置文件:   %s   （含 SECRET_KEY，权限应为 0640 root:localcraft）\n' "$ENV_FILE"
    printf '  - 日志目录:   %s\n' "$LOG_DIR"
fi
echo
echo "如何恢复"
echo "  1) 重新安装（发布包仍在时）："
echo "       tar -C /opt/localcraft/releases -xzf localcraft-<version>-offline-<date>.tar.gz"
echo "       cd /opt/localcraft/releases/localcraft-<version> && ./install.sh"
printf '     安装脚本检测到 %s 已存在会保留原配置（不覆盖 SECRET_KEY）；\n' "$ENV_FILE"
echo "     数据库已存在时 alembic upgrade head 是幂等空操作，数据原样可用。"
echo "  2) 数据目录被误删时，用备份恢复："
echo "       /opt/localcraft/scripts/verify-backup.sh          # 先确认备份可用"
echo "       /opt/localcraft/scripts/restore.sh --latest --yes # 再恢复"
echo "     备份若已随数据目录一起删除，请从远端同步（LOCALCRAFT_BACKUP_REMOTE）或离线拷贝取回。"
if [ "$PURGE" -eq 1 ]; then
    echo "  3) --purge 之后 SECRET_KEY 已销毁：所有会话与 API Token 失效，用户需重新登录。"
fi
echo "============================================================"

exit 0
