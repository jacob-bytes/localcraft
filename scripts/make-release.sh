#!/usr/bin/env bash
# ============================================================
# scripts/make-release.sh — 在构建机上生成离线发布包
#
# 用法:
#     scripts/make-release.sh <version> [--allow-missing-web]
# 例:
#     scripts/make-release.sh 1.0.0
#
# 依据: contracts/CONTRACT.md §1 的「仓库布局 → 发布包布局」映射表，
#       以及 docs/05《部署与运维方案》§4（发布包目录结构）。
#
# 与 docs/05 §4.3 示例脚本的差异（已在 checkpoint 报告说明）：
#   1. 输出目录默认是 <repo>/backend/dist 而不是 <repo>/dist ——
#      后者会在仓库根新建目录，超出后端 agent 的目录边界。
#      用 OUT_DIR=<path> 或 --out=<path> 可覆盖。
#   2. M1 阶段 wheelhouse 是可选的（双架构 wheel 属于 M4 交付物），
#      缺失时给出警告并继续；M4 必须补上，届时把 REQUIRE_WHEELHOUSE=1。
#   3. require/ 目录的映射（requirements.txt → base.in、
#      requirements.lock → requirements.lock.txt）是 docs/05 §4.1 需要的，
#      契约 §1 的映射表未单列，这里一并实现。
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

VERSION=""
ALLOW_MISSING_WEB=0
OUT_DIR_OVERRIDE=""

for arg in "$@"; do
    case "$arg" in
        --allow-missing-web) ALLOW_MISSING_WEB=1 ;;
        --out=*)             OUT_DIR_OVERRIDE="${arg#--out=}" ;;
        -h|--help)
            sed -n '2,25p' "$0"
            exit 0
            ;;
        -*) echo "未知参数: $arg" >&2; exit 2 ;;
        *)  VERSION="$arg" ;;
    esac
done

[ -n "$VERSION" ] || { echo "用法: $(basename "$0") <version> 例如 1.0.0" >&2; exit 2; }

OUT="${OUT_DIR_OVERRIDE:-${OUT_DIR:-$ROOT/backend/dist}}"
STAGE="$OUT/selftool-$VERSION"
REQUIRE_WHEELHOUSE="${REQUIRE_WHEELHOUSE:-0}"

log()  { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[warn] %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m[error] %s\033[0m\n' "$*" >&2; exit 1; }

command -v rsync >/dev/null 2>&1 || die "缺少 rsync 命令"
# GNU coreutils 的 sha256sum 与 macOS/BSD 的 shasum -a 256 输出格式一致，都可直接用于校验
if command -v sha256sum >/dev/null 2>&1; then
    SHA256_BIN="sha256sum"
elif command -v shasum >/dev/null 2>&1; then
    SHA256_BIN="shasum -a 256"
else
    die "缺少 sha256sum / shasum"
fi

rm -rf "$STAGE"
mkdir -p "$STAGE"

# ------------------------------------------------------------
log "1/7 复制后端应用代码（backend/app → app/）"
rsync -a --delete \
    --exclude '__pycache__' --exclude '*.pyc' --exclude '.pytest_cache' \
    "$ROOT/backend/app/" "$STAGE/app/"

# ------------------------------------------------------------
log "2/7 复制迁移（backend/migrations → migrations/）"
rsync -a --delete \
    --exclude '__pycache__' --exclude '*.pyc' \
    "$ROOT/backend/migrations/" "$STAGE/migrations/"
cp "$ROOT/backend/alembic.ini" "$STAGE/alembic.ini"

# ------------------------------------------------------------
log "3/7 复制依赖清单（→ requirements/）"
mkdir -p "$STAGE/requirements"
cp "$ROOT/backend/requirements.txt"  "$STAGE/requirements/base.in"
cp "$ROOT/backend/requirements.lock" "$STAGE/requirements/requirements.lock.txt"
[ -f "$ROOT/backend/pyproject.toml" ] && cp "$ROOT/backend/pyproject.toml" "$STAGE/requirements/"

# ------------------------------------------------------------
log "4/7 复制前端产物（web/dist → web/dist/）"
if [ -f "$ROOT/web/dist/index.html" ]; then
    mkdir -p "$STAGE/web"
    rsync -a --delete "$ROOT/web/dist/" "$STAGE/web/dist/"
else
    if [ "$ALLOW_MISSING_WEB" -eq 1 ]; then
        warn "web/dist 不存在，按 --allow-missing-web 跳过（发布包将不含前端）"
    else
        die "web/dist 不存在。请先在构建机执行 cd web && npm ci && npm run build，或加 --allow-missing-web"
    fi
fi

# ------------------------------------------------------------
log "5/7 复制运维产物（deploy/ 与 scripts/）"
rsync -a --delete "$ROOT/scripts/" "$STAGE/scripts/"

# deploy/ 在仓库里是平铺的，发布包里按 docs/05 §4.1 重新组织成分目录。
# 映射表（左=仓库平铺文件，右=发布包内路径）：
#   deploy/selftool*.service|.timer|.slice  → deploy/systemd/
#   deploy/selftool-limits.conf             → deploy/systemd/selftool.service.d/limits.conf
#   deploy/nginx-selftool.conf              → deploy/nginx/selftool-http.conf   （HTTP 快速验证）
#   deploy/nginx-selftool-tls.conf          → deploy/nginx/selftool.conf        （生产 TLS）
#   deploy/nginx-selftool-limits.conf       → deploy/nginx/selftool-limits.conf （限流 zone）
#   deploy/logrotate-selftool               → deploy/logrotate/selftool
#   deploy/selftool.tmpfiles                → deploy/selftool.tmpfiles          （安装到 /usr/lib/tmpfiles.d/）
install -d "$STAGE/deploy/systemd" "$STAGE/deploy/systemd/selftool.service.d" \
           "$STAGE/deploy/nginx" "$STAGE/deploy/logrotate"

# systemd 单元：必须把 backup / maintenance 两套 oneshot+timer 一起打包，
# 否则 install.sh 的单元分发循环会因文件缺失而静默跳过（它用 -f 做了保护），
# 结果是"装完没有定时备份"——这种缺失在安装当天完全看不出来。
for unit in selftool.service selftool.slice \
            selftool-backup.service selftool-backup.timer \
            selftool-maintenance.service selftool-maintenance.timer; do
    if [ -f "$ROOT/deploy/$unit" ]; then
        install -m 0644 "$ROOT/deploy/$unit" "$STAGE/deploy/systemd/$unit"
    else
        warn "缺少 deploy/${unit}（发布包将不含该单元）"
    fi
done

# systemd drop-in：资源上限与加固片段
if [ -f "$ROOT/deploy/selftool-limits.conf" ]; then
    install -m 0644 "$ROOT/deploy/selftool-limits.conf" \
        "$STAGE/deploy/systemd/selftool.service.d/limits.conf"
else
    warn "缺少 deploy/selftool-limits.conf（发布包将不含 drop-in 加固片段）"
fi

# nginx 三个配置：快速验证版、生产 TLS 版、限流 zone
install -m 0644 "$ROOT/deploy/nginx-selftool.conf"        "$STAGE/deploy/nginx/selftool-http.conf"
install -m 0644 "$ROOT/deploy/nginx-selftool-tls.conf"    "$STAGE/deploy/nginx/selftool.conf"
install -m 0644 "$ROOT/deploy/nginx-selftool-limits.conf" "$STAGE/deploy/nginx/selftool-limits.conf"

install -m 0644 "$ROOT/deploy/logrotate-selftool"   "$STAGE/deploy/logrotate/selftool"
install -m 0644 "$ROOT/deploy/selftool.env.example" "$STAGE/deploy/selftool.env.example"
install -m 0644 "$ROOT/deploy/selftool.tmpfiles"    "$STAGE/deploy/selftool.tmpfiles"

# 发布包根目录的两个入口（docs/05 §4.1 的 install.sh / uninstall.sh），
# 与 scripts/ 下的同名文件是同一份内容（rsync 之后从 STAGE 里取，保证不会不同步）
cp "$STAGE/scripts/install.sh"   "$STAGE/install.sh"
if [ -f "$STAGE/scripts/uninstall.sh" ]; then
    cp "$STAGE/scripts/uninstall.sh" "$STAGE/uninstall.sh"
else
    warn "缺少 scripts/uninstall.sh（发布包根目录将不含卸载入口）"
fi

# ------------------------------------------------------------
log "6/7 处理 wheelhouse 与随包文档"
HAVE_WHEELHOUSE=0
# wheelhouse 的**源**位置：docs/05 §3.4 的示例放在源码根目录；
# 本仓库的构建脚本（scripts/build-wheelhouse.sh）默认放在 backend/ 下。
# 两处都找一遍，「源码根」的写法（照 docs 做的构建机）仍然可用。
# 注意：发布包**内部**的布局不变，仍然是根级的 wheelhouse-<arch>/（docs/05 §4.1）。
for arch in x86_64 aarch64; do
    src=""
    for base in "$ROOT" "$ROOT/backend"; do
        if [ -d "$base/wheelhouse-${arch}" ] && [ -n "$(ls -A "$base/wheelhouse-${arch}" 2>/dev/null || true)" ]; then
            src="$base/wheelhouse-${arch}"
            break
        fi
    done
    if [ -n "$src" ]; then
        rsync -a "$src/" "$STAGE/wheelhouse-${arch}/"
        HAVE_WHEELHOUSE=1
        echo "  已包含 wheelhouse-${arch}（来自 ${src}）"
    fi
done
if [ "$HAVE_WHEELHOUSE" -eq 0 ]; then
    if [ "$REQUIRE_WHEELHOUSE" -eq 1 ]; then
        die "缺少 wheelhouse-x86_64 / wheelhouse-aarch64（REQUIRE_WHEELHOUSE=1）"
    fi
    warn "未包含 wheelhouse —— 目标机安装时将退化为联网 pip 安装"
    warn "双架构 wheel 的产出与校验是 M4 的交付物（docs/05 §3.4）"
fi

# docs/ 是只读来源，只是复制进发布包（不修改）
if [ -f "$ROOT/docs/05-部署与运维方案.md" ]; then
    install -d "$STAGE/docs"
    cp "$ROOT/docs/05-部署与运维方案.md" "$STAGE/docs/"
fi
[ -f "$ROOT/RELEASE-NOTES.md" ] && cp "$ROOT/RELEASE-NOTES.md" "$STAGE/"

# ------------------------------------------------------------
log "7/7 生成元数据与校验和"
echo "$VERSION" > "$STAGE/VERSION"
chmod +x "$STAGE/install.sh" "$STAGE"/scripts/*.sh 2>/dev/null || true

# 清单里用相对路径，便于搬迁后校验
(
    cd "$STAGE"
    find . -type f ! -name SHA256SUMS -print0 \
        | sort -z \
        | xargs -0 "$SHA256_BIN" \
        | sed 's|  \./|  |' > SHA256SUMS
)

TARBALL="$OUT/selftool-$VERSION-offline-$(date +%Y%m%d).tar.gz"
( cd "$OUT" && tar -czf "$(basename "$TARBALL")" "selftool-$VERSION" )

echo
ls -lh "$TARBALL"
echo "发布包内容摘要："
du -sh "$STAGE"/* 2>/dev/null | sort -h

cat <<'EOF'

提示：
  - 安装：把 tar.gz 拷到目标机后
      tar -C /opt/selftool/releases -xzf <tarball>
      cd /opt/selftool/releases/selftool-<version> && ./install.sh
  - 契约 §1 的映射已在上面逐条落实（app/ migrations/ web/dist/ deploy/ scripts/），
    另外补了 alembic.ini 与 requirements/ —— 这两项是 docs/05 §4.1 发布包结构
    的一部分，安装脚本依赖它们。
EOF
