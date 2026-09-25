#!/usr/bin/env bash
# ============================================================
# install.sh — selftool 离线安装脚本（幂等，可重复执行）
#
# 在**解压后的发布包根目录**里以 root 执行：
#     tar -C /opt/selftool/releases -xzf selftool-<version>-offline-<date>.tar.gz
#     cd /opt/selftool/releases/selftool-<version>
#     ./install.sh
#
# 依据: docs/05《部署与运维方案》§5.0~§5.9 / §5.12
# 落位: docs/05 §4.2 的「发布包内路径 → 目标机路径」对应表
#
# M1 范围：单机、单架构、能装起来。双架构 wheelhouse 的构建与校验属于 M4。
# ============================================================
set -euo pipefail

SELTOOL_VERSION="$(cat "$(dirname "$0")/VERSION")"
RELEASE_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV=/opt/selftool/venv
APP_LINK=/opt/selftool/app/current
ENV_FILE=/etc/selftool/selftool.env
DATA_DIR=/var/lib/selftool
LOG_DIR=/var/log/selftool

log()  { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
warn() { printf '\n\033[1;33m[warn] %s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31m[error] %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "请以 root 执行"

# ------------------------------------------------------------
log "1/10 环境自检"
# precheck 的 FAIL 不直接终止：某些 FAIL 项（如端口占用）需要现场判断，
# 但要把结论显式打出来，避免静默带病安装。
if ! "$RELEASE_DIR/scripts/precheck.sh"; then
    warn "自检存在 FAIL 项，请人工确认后重新执行；本次继续"
fi

# ------------------------------------------------------------
log "2/10 校验发布包"
( cd "$RELEASE_DIR" && sha256sum -c SHA256SUMS --quiet ) || die "发布包校验失败"

case "$(uname -m)" in
    x86_64)  WHEELHOUSE="$RELEASE_DIR/wheelhouse-x86_64" ;;
    aarch64) WHEELHOUSE="$RELEASE_DIR/wheelhouse-aarch64" ;;
    *) die "不支持的架构: $(uname -m)" ;;
esac

if [ -d "$WHEELHOUSE" ] && [ -n "$(ls -A "$WHEELHOUSE" 2>/dev/null || true)" ]; then
    ( cd "$WHEELHOUSE" && sha256sum -c SHA256SUMS --quiet ) || die "wheelhouse 校验失败"
    OFFLINE_FLAGS=(--no-index "--find-links=$WHEELHOUSE")
    log "    使用离线 wheelhouse: $WHEELHOUSE"
else
    # M1 允许发布包里不带 wheelhouse（双架构 wheel 是 M4 的交付物）。
    # 此时退化为联网安装：需要目标机能访问公网或内网 PyPI 源。
    WHEELHOUSE=""
    OFFLINE_FLAGS=()
    warn "发布包内没有 wheelhouse，将改为从 PyPI 在线安装依赖"
    warn "完全离线的环境请先在构建机上执行 make-release.sh 生成 wheelhouse"
fi

# ------------------------------------------------------------
log "3/10 创建系统用户与目录"
# 系统用户：无登录 shell。它的唯一用途是运行服务。
id selftool >/dev/null 2>&1 || useradd \
    --system \
    --shell /sbin/nologin \
    --home-dir "$DATA_DIR" \
    --comment "selftool service account" \
    selftool

install -d -m 0750 -o selftool -g selftool \
    "$DATA_DIR" \
    "$DATA_DIR/files" \
    "$DATA_DIR/files/uploads" \
    "$DATA_DIR/files/images" \
    "$DATA_DIR/backups"
install -d -m 0750 -o root -g selftool /etc/selftool
install -d -m 0750 -o selftool -g selftool "$LOG_DIR"
install -d -m 0755 -o root -g root /opt/selftool/app /opt/selftool/scripts /opt/selftool/releases
cp -a "$RELEASE_DIR/scripts/." /opt/selftool/scripts/
chmod +x /opt/selftool/scripts/*.sh

# ------------------------------------------------------------
log "4/10 建立代码软链（升级/回滚靠切换它）"
ln -sfn "$RELEASE_DIR" "$APP_LINK"
ls -l "$APP_LINK"

# ------------------------------------------------------------
log "5/10 创建虚拟环境"
command -v python3.11 >/dev/null 2>&1 || die "未找到 python3.11，请先安装（docs/05 §2.3）"
[ -x "$VENV/bin/python" ] || python3.11 -m venv "$VENV"
if [ -n "$WHEELHOUSE" ]; then
    "$VENV/bin/python" -m pip install --quiet --no-index "--find-links=$WHEELHOUSE" \
        --upgrade pip setuptools wheel
else
    "$VENV/bin/python" -m pip install --quiet --upgrade pip setuptools wheel
fi

# ------------------------------------------------------------
log "6/10 安装依赖"
LOCK="$RELEASE_DIR/requirements/requirements.lock.txt"
[ -f "$LOCK" ] || die "缺少依赖锁定清单: $LOCK"
"$VENV/bin/python" -m pip install "${OFFLINE_FLAGS[@]}" --requirement "$LOCK"

# ------------------------------------------------------------
log "7/10 环境变量文件"
if [ -f "$ENV_FILE" ]; then
    warn "$ENV_FILE 已存在，保留现有配置（不覆盖 SECRET_KEY）"
else
    install -m 0640 -o root -g selftool "$RELEASE_DIR/deploy/selftool.env.example" "$ENV_FILE"
    SECRET="$(openssl rand -hex 32)"
    sed -i "s|^SECRET_KEY=.*|SECRET_KEY=${SECRET}|" "$ENV_FILE"
    sed -i "s|^SELTOOL_VERSION=.*|SELTOOL_VERSION=${SELTOOL_VERSION}|" "$ENV_FILE"
    warn "已生成 $ENV_FILE，请检查 DATABASE_URL / SELTOOL_PUBLIC_BASE_URL / COOKIE_SECURE 后继续"
fi

# 数据库目录必须由 selftool 可写，否则首次迁移会失败
chown -R selftool:selftool "$DATA_DIR"

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
install -m 0644 "$RELEASE_DIR/deploy/systemd/selftool.slice"   /etc/systemd/system/
install -m 0644 "$RELEASE_DIR/deploy/systemd/selftool.service" /etc/systemd/system/
[ -f "$RELEASE_DIR/deploy/logrotate/selftool" ] && \
    install -m 0644 "$RELEASE_DIR/deploy/logrotate/selftool" /etc/logrotate.d/selftool
systemctl daemon-reload
systemctl enable selftool.service
systemctl restart selftool.service

# ------------------------------------------------------------
log "10/10 启动后验证"
if "$RELEASE_DIR/scripts/wait-healthy.sh" --url "http://127.0.0.1:${SELTOOL_PORT:-8000}/readyz" --timeout 90; then
    echo "服务已就绪"
else
    die "服务 90 秒内未通过 /readyz，请查看 journalctl -u selftool -n 100 --no-pager"
fi

cat <<EOF

============================================================
安装完成。接下来还有三件事：

1) 创建超级管理员（密码走交互输入或 --password-stdin，不要用明文参数）
   sudo -u selftool bash -c '
     set -a; . $ENV_FILE; set +a
     cd $APP_LINK
     $VENV/bin/python -m app.cli create-superadmin --username admin --email admin@intra.example.com
   '

2) 配置 nginx（先用 HTTP 快速验证版）
   install -m 0644 $RELEASE_DIR/deploy/nginx/selftool-http.conf /etc/nginx/conf.d/selftool.conf
   nginx -t && systemctl reload nginx

3) 验证
   curl -sS http://127.0.0.1:8000/healthz
   curl -sS http://127.0.0.1:8000/readyz
   curl -sS http://127.0.0.1:8000/api/v1/meta
============================================================
EOF
