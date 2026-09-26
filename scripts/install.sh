#!/usr/bin/env bash
# ============================================================
# install.sh — localcraft 离线安装脚本（幂等，可重复执行）
#
# 在**解压后的发布包根目录**里以 root 执行：
#     tar -C /opt/localcraft/releases -xzf localcraft-<version>-offline-<date>.tar.gz
#     cd /opt/localcraft/releases/localcraft-<version>
#     ./install.sh
#
# 依据: docs/05《部署与运维方案》§5.0~§5.9 / §5.12
# 落位: docs/05 §4.2 的「发布包内路径 → 目标机路径」对应表
#
# ---------------------------------------------------------------------------
# 生产用法（openEuler，root，systemd）就是上面那三行，不需要任何额外变量。
#
# 演练/沙箱用法（非 root、无 systemd 的机器上验证安装链路）：
#     LOCALCRAFT_SKIP_SYSTEMD=1 \
#     LOCALCRAFT_PREFIX=$PWD/sandbox/opt \
#     LOCALCRAFT_ETC_DIR=$PWD/sandbox/etc \
#     LOCALCRAFT_DATA_DIR=$PWD/sandbox/var \
#     LOCALCRAFT_LOG_DIR=$PWD/sandbox/log \
#     ./install.sh
#   此时跳过 useradd / chown / systemctl，改用 nohup 直接拉起 uvicorn，
#   其余步骤（发布包校验 → 离线 wheelhouse 安装 → 迁移 → 就绪探针）**完全一致**。
#   这不是生产路径，只是让「安装链路」在没有 openEuler 的机器上也能被真正跑一遍。
# ---------------------------------------------------------------------------
set -euo pipefail

LOCALCRAFT_VERSION="$(cat "$(dirname "$0")/VERSION")"
RELEASE_DIR="$(cd "$(dirname "$0")" && pwd)"

# ---------- 路径（可用环境变量覆盖，默认与 docs/05 §4.2 一致）----------
PREFIX="${LOCALCRAFT_PREFIX:-/opt/localcraft}"
# 本脚本是**生成器**：把这里的取值写进 localcraft.env（第 201 行 sed DATA_DIR=…），
# 应用与运维脚本之后读的都是无前缀的 DATA_DIR。所以这里的优先级与那些消费者脚本相反 ——
# LOCALCRAFT_DATA_DIR 是安装期的显式开关（docs/05 与 m4 演练都用它），必须排在最前，
# 否则环境里一个陈旧的 DATA_DIR 就能悄悄改变安装目标目录。
DATA_DIR="${LOCALCRAFT_DATA_DIR:-${DATA_DIR:-/var/lib/localcraft}}"
ETC_DIR="${LOCALCRAFT_ETC_DIR:-/etc/localcraft}"
LOG_DIR="${LOCALCRAFT_LOG_DIR:-/var/log/localcraft}"

VENV="$PREFIX/venv"
APP_LINK="$PREFIX/app/current"
ENV_FILE="$ETC_DIR/localcraft.env"
SKIP_SYSTEMD="${LOCALCRAFT_SKIP_SYSTEMD:-0}"
PID_FILE="$DATA_DIR/localcraft.pid"
# systemd 相关落位。做成变量是为了让"演练/沙箱"也能验证安装链路，
# 生产默认值与 docs/05 §4.2 的对应表完全一致。
SYSTEMD_UNIT_DIR="${LOCALCRAFT_SYSTEMD_UNIT_DIR:-/etc/systemd/system}"
TMPFILES_DIR="${LOCALCRAFT_TMPFILES_DIR:-/usr/lib/tmpfiles.d}"

log()  { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
warn() { printf '\n\033[1;33m[warn] %s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31m[error] %s\033[0m\n' "$*" >&2; exit 1; }

# ---------- 校验和工具（GNU coreutils 优先，BSD/macOS 回退）----------
if command -v sha256sum >/dev/null 2>&1; then
    SHA256_BIN="sha256sum"
elif command -v shasum >/dev/null 2>&1; then
    SHA256_BIN="shasum -a 256"
else
    die "缺少 sha256sum / shasum，无法校验发布包"
fi
verify_sums() {  # verify_sums <目录>
    ( cd "$1" && $SHA256_BIN -c SHA256SUMS --quiet 2>/dev/null \
        || $SHA256_BIN -c SHA256SUMS >/dev/null )
}

# ---------- sed -i 的 GNU/BSD 差异 ----------
sed_i() {  # sed_i <表达式> <文件>
    if sed --version >/dev/null 2>&1; then
        sed -i "$1" "$2"
    else
        sed -i '' "$1" "$2"   # BSD sed 需要显式空后缀
    fi
}

if [ "$SKIP_SYSTEMD" = "1" ]; then
    warn "LOCALCRAFT_SKIP_SYSTEMD=1 —— 演练模式：跳过 useradd / chown / systemctl"
    warn "这不是生产路径。生产安装请以 root 直接执行 ./install.sh"
else
    [ "$(id -u)" -eq 0 ] || die "请以 root 执行（或在演练时设 LOCALCRAFT_SKIP_SYSTEMD=1）"
fi

# ------------------------------------------------------------
log "1/10 环境自检"
# precheck 的 FAIL 不直接终止：某些 FAIL 项（如端口占用）需要现场判断，
# 但要把结论显式打出来，避免静默带病安装。
if ! "$RELEASE_DIR/scripts/precheck.sh"; then
    warn "自检存在 FAIL 项，请人工确认后重新执行；本次继续"
fi

# ------------------------------------------------------------
log "2/10 校验发布包"
verify_sums "$RELEASE_DIR" || die "发布包校验失败（SHA256SUMS）"

ARCH="$(uname -m)"
case "$ARCH" in
    x86_64|amd64)   WHEELHOUSE="$RELEASE_DIR/wheelhouse-x86_64" ;;
    aarch64|arm64)  WHEELHOUSE="$RELEASE_DIR/wheelhouse-aarch64" ;;
    *) die "不支持的架构: $ARCH" ;;
esac

# 演练时可显式指定 wheelhouse（生产不需要，走上面的按架构自动选择）
[ -n "${LOCALCRAFT_WHEELHOUSE:-}" ] && WHEELHOUSE="$LOCALCRAFT_WHEELHOUSE"

if [ -d "$WHEELHOUSE" ] && [ -n "$(ls -A "$WHEELHOUSE" 2>/dev/null || true)" ]; then
    # wheelhouse 的清单文件名是 MANIFEST.sha256（build-wheelhouse.sh 产出）；
    # 若手工放了 SHA256SUMS 也认，两种都支持。
    if [ -f "$WHEELHOUSE/MANIFEST.sha256" ]; then
        ( cd "$WHEELHOUSE" && $SHA256_BIN -c MANIFEST.sha256 --quiet 2>/dev/null \
            || $SHA256_BIN -c MANIFEST.sha256 >/dev/null ) || die "wheelhouse 校验失败"
    elif [ -f "$WHEELHOUSE/SHA256SUMS" ]; then
        verify_sums "$WHEELHOUSE" || die "wheelhouse 校验失败"
    fi
    OFFLINE_FLAGS=(--no-index "--find-links=$WHEELHOUSE")
    log "    使用离线 wheelhouse: $WHEELHOUSE"
else
    WHEELHOUSE=""
    OFFLINE_FLAGS=()
    warn "发布包内没有 wheelhouse，将改为从 PyPI 在线安装依赖"
    warn "完全离线的环境请先在构建机上执行 scripts/build-wheelhouse.sh"
fi

# ------------------------------------------------------------
log "3/10 创建系统用户与目录"
if [ "$SKIP_SYSTEMD" != "1" ]; then
    # 系统用户：无登录 shell。它的唯一用途是运行服务。
    id localcraft >/dev/null 2>&1 || useradd \
        --system \
        --shell /sbin/nologin \
        --home-dir "$DATA_DIR" \
        --comment "localcraft service account" \
        localcraft
    install -d -m 0750 -o localcraft -g localcraft \
        "$DATA_DIR" \
        "$DATA_DIR/files" \
        "$DATA_DIR/files/uploads" \
        "$DATA_DIR/files/images" \
        "$DATA_DIR/backups"
    install -d -m 0750 -o root -g localcraft "$ETC_DIR"
    install -d -m 0750 -o localcraft -g localcraft "$LOG_DIR"
else
    install -d -m 0750 \
        "$DATA_DIR" \
        "$DATA_DIR/files" \
        "$DATA_DIR/files/uploads" \
        "$DATA_DIR/files/images" \
        "$DATA_DIR/backups"
    install -d -m 0750 "$ETC_DIR" "$LOG_DIR"
fi
install -d -m 0755 "$PREFIX/app" "$PREFIX/scripts" "$PREFIX/releases"
cp -a "$RELEASE_DIR/scripts/." "$PREFIX/scripts/"
chmod +x "$PREFIX/scripts/"*.sh

# ------------------------------------------------------------
log "4/10 建立代码软链（升级/回滚靠切换它）"
ln -sfn "$RELEASE_DIR" "$APP_LINK"
ls -l "$APP_LINK"

# ------------------------------------------------------------
log "5/10 创建虚拟环境"
PY311="${LOCALCRAFT_PYTHON311:-}"
if [ -z "$PY311" ]; then
    if command -v python3.11 >/dev/null 2>&1; then
        PY311="python3.11"
    else
        die "未找到 python3.11，请先安装（docs/05 §2.3），或用 LOCALCRAFT_PYTHON311 指定绝对路径"
    fi
fi
[ -x "$VENV/bin/python" ] || "$PY311" -m venv "$VENV"
# 升级工具链。离线时若 wheelhouse 里没有 bootstrap 包（老版本发布包），
# 只告警不终止：venv 自带的 pip/setuptools 足够完成后面的 wheel 安装。
if [ -n "$WHEELHOUSE" ]; then
    if ! "$VENV/bin/python" -m pip install --quiet --no-index "--find-links=$WHEELHOUSE" \
            --upgrade pip setuptools wheel; then
        warn "wheelhouse 里缺少 pip/setuptools/wheel，跳过工具链升级（用 venv 自带版本继续）"
        warn "建议用 scripts/build-wheelhouse.sh 重新构建包含 bootstrap 包的 wheelhouse"
    fi
else
    "$VENV/bin/python" -m pip install --quiet --upgrade pip setuptools wheel
fi

# ------------------------------------------------------------
log "6/10 安装依赖（离线）"
LOCK="$RELEASE_DIR/requirements/requirements.lock.txt"
[ -f "$LOCK" ] || die "缺少依赖锁定清单: $LOCK"
"$VENV/bin/python" -m pip install "${OFFLINE_FLAGS[@]}" --requirement "$LOCK"

# ------------------------------------------------------------
log "7/10 环境变量文件"
if [ -f "$ENV_FILE" ]; then
    warn "$ENV_FILE 已存在，保留现有配置（不覆盖 SECRET_KEY）"
else
    if [ "$SKIP_SYSTEMD" != "1" ]; then
        install -m 0640 -o root -g localcraft "$RELEASE_DIR/deploy/localcraft.env.example" "$ENV_FILE"
    else
        install -m 0640 "$RELEASE_DIR/deploy/localcraft.env.example" "$ENV_FILE"
    fi
    SECRET="$(openssl rand -hex 32)"
    sed_i "s|^SECRET_KEY=.*|SECRET_KEY=${SECRET}|" "$ENV_FILE"
    sed_i "s|^LOCALCRAFT_VERSION=.*|LOCALCRAFT_VERSION=${LOCALCRAFT_VERSION}|" "$ENV_FILE"
    # 自定义前缀时把示例里的绝对路径改到实际位置，否则迁移会写到 /var/lib
    if [ "$DATA_DIR" != "/var/lib/localcraft" ]; then
        sed_i "s|^DATA_DIR=.*|DATA_DIR=${DATA_DIR}|" "$ENV_FILE"
        sed_i "s|^DATABASE_URL=.*|DATABASE_URL=sqlite+aiosqlite:///${DATA_DIR}/localcraft.db|" "$ENV_FILE"
    fi
    warn "已生成 ${ENV_FILE}，请检查 DATABASE_URL / LOCALCRAFT_PUBLIC_BASE_URL / COOKIE_SECURE 后继续"
fi

# 数据库目录必须由服务账号可写，否则首次迁移会失败
if [ "$SKIP_SYSTEMD" != "1" ]; then
    chown -R localcraft:localcraft "$DATA_DIR"
fi

# ------------------------------------------------------------
log "8/10 数据库迁移（必须在服务启动之前执行，docs/02 §6.3）"
(
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
    cd "$APP_LINK"
    "$VENV/bin/alembic" upgrade head
)

# ------------------------------------------------------------
log "9/10 安装 systemd 单元与日志轮转"
if [ "$SKIP_SYSTEMD" != "1" ]; then
    for unit in localcraft.slice localcraft.service localcraft-backup.service \
                localcraft-backup.timer localcraft-maintenance.service localcraft-maintenance.timer; do
        if [ -f "$RELEASE_DIR/deploy/systemd/$unit" ]; then
            install -m 0644 "$RELEASE_DIR/deploy/systemd/$unit" "$SYSTEMD_UNIT_DIR/"
        fi
    done
    # 资源上限/加固片段必须装进 drop-in 目录：systemd 不支持 unit 之间 include，
    # 只有 <unit>.d/*.conf 才会被自动叠加（详见 deploy/localcraft-limits.conf 的文件头说明）。
    if [ -f "$RELEASE_DIR/deploy/systemd/localcraft.service.d/limits.conf" ]; then
        install -d -m 0755 "$SYSTEMD_UNIT_DIR/localcraft.service.d"
        install -m 0644 "$RELEASE_DIR/deploy/systemd/localcraft.service.d/limits.conf" \
            "$SYSTEMD_UNIT_DIR/localcraft.service.d/limits.conf"
    else
        warn "发布包内没有 deploy/systemd/localcraft.service.d/limits.conf，跳过 drop-in 安装"
    fi
    # systemd-tmpfiles：声明式创建运行目录与权限；随后立刻 apply 一次，
    # 免得"配置装了但要等下次开机才生效"。
    if [ -f "$RELEASE_DIR/deploy/localcraft.tmpfiles" ]; then
        install -d -m 0755 "$TMPFILES_DIR"
        install -m 0644 "$RELEASE_DIR/deploy/localcraft.tmpfiles" "$TMPFILES_DIR/localcraft.conf"
        if command -v systemd-tmpfiles >/dev/null 2>&1; then
            systemd-tmpfiles --create "$TMPFILES_DIR/localcraft.conf" || \
                warn "systemd-tmpfiles --create 返回非零（目录可能已由 install -d 建好），继续"
        fi
    else
        warn "发布包内没有 deploy/localcraft.tmpfiles，跳过 tmpfiles 安装"
    fi
    if [ -f "$RELEASE_DIR/deploy/logrotate/localcraft" ]; then
        install -m 0644 "$RELEASE_DIR/deploy/logrotate/localcraft" /etc/logrotate.d/localcraft
    fi
    systemctl daemon-reload
    systemctl enable localcraft.service
    systemctl restart localcraft.service
else
    # 演练模式：没有 systemd，用 nohup 直接拉起同一个 uvicorn 命令（参数与 unit 一致）
    set -a
    # shellcheck disable=SC1090  # 动态路径 source，shellcheck 无法静态跟踪
    . "$ENV_FILE"
    set +a
    : > "$LOG_DIR/localcraft-stdout.log"
    (
        cd "$APP_LINK"
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
    echo "  已启动 uvicorn（PID $(cat "$PID_FILE")），日志 $LOG_DIR/localcraft-stdout.log"
    echo "  停止：kill \$(cat $PID_FILE)"
fi

# ------------------------------------------------------------
log "10/10 启动后验证"
if "$RELEASE_DIR/scripts/wait-healthy.sh" \
        --url "http://127.0.0.1:${LOCALCRAFT_PORT:-8000}/readyz" --timeout 90; then
    echo "服务已就绪"
else
    if [ "$SKIP_SYSTEMD" = "1" ]; then
        echo "--- 最近 40 行应用日志 ---" >&2
        tail -n 40 "$LOG_DIR/localcraft-stdout.log" >&2 || true
    fi
    die "服务 90 秒内未通过 /readyz"
fi

cat <<EOF

============================================================
安装完成。接下来还有三件事：

1) 创建超级管理员（密码走交互输入或 --password-stdin，不要用明文参数）
   sudo -u localcraft bash -c '
     set -a; . $ENV_FILE; set +a
     cd $APP_LINK
     $VENV/bin/python -m app.cli create-superadmin --username admin --email admin@intra.example.com
   '

2) 让外部能访问 —— 两种方式，按 LOCALCRAFT_WEB_MODE 选一种即可
   （当前 LOCALCRAFT_WEB_MODE=${LOCALCRAFT_WEB_MODE:-nginx}）

   ── 方式 A：direct（不用 nginx，直接 IP:PORT）
      只需确认 $ENV_FILE 里：
        LOCALCRAFT_HOST=0.0.0.0        # 不改的话只有本机能访问
        LOCALCRAFT_PORT=${LOCALCRAFT_PORT:-8000}
      然后放行防火墙：
        firewall-cmd --add-port=${LOCALCRAFT_PORT:-8000}/tcp --permanent && firewall-cmd --reload
      访问 http://<本机IP>:${LOCALCRAFT_PORT:-8000}

      注意：直连是**明文 HTTP**，登录密码在局域网内不加密，refresh cookie 也不是 Secure。
      内网可信环境下可以接受；若要加密又不想上 nginx，可让 uvicorn 自己终结 TLS：
        Environment=... --ssl-certfile=$ETC_DIR/tls/fullchain.pem --ssl-keyfile=$ETC_DIR/tls/privkey.pem
      （自签证书即可；同时把 $ENV_FILE 里的 COOKIE_SECURE 改成 true）
      安全响应头（CSP / X-Frame-Options / nosniff …）应用会自己发，两种方式都不缺。

   ── 方式 B：nginx（反代，负责 TLS + 限流 + 静态加速）
      先用 HTTP 快速验证版：
        install -m 0644 $RELEASE_DIR/deploy/nginx/localcraft-http.conf /etc/nginx/conf.d/localcraft.conf
        nginx -t && systemctl reload nginx
      验证通过后切生产 TLS（证书放到 $ETC_DIR/tls/）：
        rm -f /etc/nginx/conf.d/localcraft-http.conf
        install -m 0644 $RELEASE_DIR/deploy/nginx/localcraft-limits.conf /etc/nginx/conf.d/localcraft-limits.conf
        install -m 0644 $RELEASE_DIR/deploy/nginx/localcraft.conf       /etc/nginx/conf.d/localcraft.conf
        nginx -t && systemctl reload nginx
      （localcraft-http.conf 自带一份 zone/log_format 定义，与 localcraft-limits.conf 同名，
        两者不能同时安装，否则 nginx 会因重复定义而启动失败）

3) 验证
   curl -sS http://127.0.0.1:${LOCALCRAFT_PORT:-8000}/healthz
   curl -sS http://127.0.0.1:${LOCALCRAFT_PORT:-8000}/readyz
   curl -sS http://127.0.0.1:${LOCALCRAFT_PORT:-8000}/api/v1/meta
============================================================
EOF
