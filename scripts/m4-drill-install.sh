#!/usr/bin/env bash
# ============================================================
# scripts/m4-drill-install.sh — 发布包端到端安装演练
#
# 用法: scripts/m4-drill-install.sh [端口]
#
# 本脚本做**两件不同的事**，结论也要分开读：
#
#   A. 离线可解性证明（针对真实发布包里的 linux wheelhouse）
#      用 pip install --dry-run --no-index --find-links=wheelhouse-<arch>
#      对目标平台做一次完整解析：证明 lock 里**每一个**包都能在该 wheelhouse 里
#      找到可用的 wheel，不需要任何网络。
#
#   B. install.sh 全流程演练（10 步全跑）
#      在沙箱前缀里跑真正的 install.sh，直到 /readyz 返回 200。
#      本机是 macOS，**装不了 linux wheel**（ELF 无法加载），
#      所以 B 用一个**本机架构的 wheelhouse**（同样 --no-index 离线装），
#      走的是与生产完全相同的代码路径。
#
# ★ 本机**做不到**的事（不许含糊）：
#   1) 用发布包里的 linux wheelhouse 真的把服务跑起来 —— 需要 linux 主机或
#      容器/qemu；本机既无 docker/podman 也无 qemu。
#   2) 验证 systemd 路径（useradd / chown / systemctl）—— 本机无 systemd，
#      用 SELTOOL_SKIP_SYSTEMD=1 跳过（改由 nohup 拉起同一个 uvicorn 命令）。
#   3) aarch64 的安装验证 —— 完全没有 aarch64 环境。
# ============================================================
set -uo pipefail

PORT="${1:-8000}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND="$ROOT/backend"
DIST="$BACKEND/dist"
PY="${SELTOOL_PYTHON311:-$BACKEND/.venv/bin/python}"
SANDBOX="${SELTOOL_DRILL_SANDBOX:-$DIST/drill}"

log()  { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
step() { printf '\n\033[1m--- %s ---\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31m[error] %s\033[0m\n' "$*" >&2; exit 1; }

TARBALL="$(find "$DIST" -maxdepth 1 -name 'selftool-*-offline-*.tar.gz' | sort | tail -1)"
[ -n "$TARBALL" ] || die "找不到发布包，请先执行 scripts/make-release.sh <version>"
log "发布包: $(basename "$TARBALL")  ($(du -h "$TARBALL" | cut -f1))"

rm -rf "$SANDBOX"
mkdir -p "$SANDBOX/releases"
tar -C "$SANDBOX/releases" -xzf "$TARBALL"
RELEASE_DIR="$(find "$SANDBOX/releases" -maxdepth 1 -mindepth 1 -type d | head -1)"
[ -n "$RELEASE_DIR" ] || die "解压后没有版本目录"

if ! grep -q 'SELTOOL_SKIP_SYSTEMD' "$RELEASE_DIR/install.sh"; then
    die "发布包内的 install.sh 是旧版（不支持 SELTOOL_SKIP_SYSTEMD）。多半是先打包后改脚本，请重新 make-release.sh"
fi

LOCK="$RELEASE_DIR/requirements/requirements.lock.txt"

# ===========================================================================
log "A. 离线可解性证明（发布包内的 linux wheelhouse）"
# ===========================================================================
resolve_check() {  # resolve_check <wheelhouse 目录> <平台标签...>
    local wh="$1"; shift
    local tmp; tmp="$(mktemp -d)"
    local plats=(); local p
    for p in "$@"; do plats+=(--platform "$p"); done
    if "$PY" -m pip install --dry-run --no-index --find-links="$wh" \
            --only-binary=:all: --python-version 3.11 --implementation cp \
            "${plats[@]}" --target "$tmp" -r "$LOCK" > "$tmp/report.txt" 2>&1; then
        echo "  ✓ 解析成功：$(grep -c 'Would install' "$tmp/report.txt") 行 Would install"
        rm -rf "$tmp"; return 0
    fi
    tail -20 "$tmp/report.txt"; rm -rf "$tmp"; return 1
}

step "x86_64（目标 glibc 2.38，openEuler 24.03）"
resolve_check "$RELEASE_DIR/wheelhouse-x86_64" \
    manylinux_2_38_x86_64 manylinux_2_36_x86_64 manylinux_2_34_x86_64 \
    manylinux_2_28_x86_64 manylinux_2_17_x86_64 manylinux2014_x86_64 linux_x86_64 \
    || die "x86_64 离线解析失败 —— wheelhouse 不完整，目标机离线安装会失败"

step "aarch64（同一份 lock，aarch64 标签）"
resolve_check "$RELEASE_DIR/wheelhouse-aarch64" \
    manylinux_2_38_aarch64 manylinux_2_34_aarch64 manylinux_2_28_aarch64 \
    manylinux_2_17_aarch64 manylinux2014_aarch64 linux_aarch64 \
    || die "aarch64 离线解析失败"
echo "    注意：这只证明**能解析**，不证明这些 ELF 能在 aarch64 上加载（本机无 aarch64）"

# ===========================================================================
log "B. install.sh 全流程演练（沙箱前缀，10 步全跑）"
# ===========================================================================
step "目录结构核对（docs/05 §4.1）"
for expect in VERSION SHA256SUMS install.sh app migrations alembic.ini requirements \
              web/dist wheelhouse-x86_64 wheelhouse-aarch64 scripts deploy; do
    if [ -e "$RELEASE_DIR/$expect" ]; then printf '  ✓ %s\n' "$expect"
    else printf '  \033[1;31m✗ %s 缺失\033[0m\n' "$expect"; fi
done

NATIVE_WH="$SANDBOX/wheelhouse-native"
step "构建本机架构 wheelhouse（供沙箱安装使用；生产不需要这一步）"
mkdir -p "$NATIVE_WH"
# 公网可能抖（实测遇到 ChunkedEncodingError）。带重试下载，最多 3 轮。
native_ok=0
for attempt in 1 2 3; do
    if "$PY" -m pip download --dest "$NATIVE_WH" --no-cache-dir --disable-pip-version-check \
            --retries 5 --timeout 60 \
            --requirement "$LOCK" pip setuptools wheel > "$SANDBOX/native-wh.log" 2>&1; then
        native_ok=1; break
    fi
    echo "  第 ${attempt} 轮下载失败，重试..." >&2
    sleep 3
done
[ "$native_ok" -eq 1 ] || { tail -20 "$SANDBOX/native-wh.log"; die "本机 wheelhouse 构建失败（网络？）"; }
echo "  ✓ 本机 wheelhouse: $(find "$NATIVE_WH" -name '*.whl' | wc -l | tr -d ' ') 个 wheel"

if command -v lsof >/dev/null 2>&1 && lsof -ti:"$PORT" >/dev/null 2>&1; then
    die "端口 $PORT 已被占用，请先释放或换一个端口"
fi

step "执行 install.sh（SELTOOL_SKIP_SYSTEMD=1 + SELTOOL_WHEELHOUSE=<本机>）"
SELTOOL_SKIP_SYSTEMD=1 \
SELTOOL_PREFIX="$SANDBOX/opt" \
SELTOOL_ETC_DIR="$SANDBOX/etc" \
SELTOOL_DATA_DIR="$SANDBOX/var" \
SELTOOL_LOG_DIR="$SANDBOX/log" \
SELTOOL_WHEELHOUSE="$NATIVE_WH" \
SELTOOL_PYTHON311="$PY" \
    "$RELEASE_DIR/install.sh"
RC=$?
if [ "$RC" -ne 0 ]; then
    echo "--- 应用日志尾部 ---"
    tail -n 60 "$SANDBOX/log/selftool-stdout.log" 2>/dev/null || true
    die "install.sh 退出码 $RC"
fi

step "独立复核（不依赖 install.sh 自己的结论）"
CODE="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}/readyz")"
printf '  GET /readyz       -> HTTP %s\n' "$CODE"
printf '  GET /healthz      -> %s\n' "$(curl -s "http://127.0.0.1:${PORT}/healthz")"
printf '  GET /api/v1/meta  -> %s\n' "$(curl -s "http://127.0.0.1:${PORT}/api/v1/meta" | head -c 160)"
printf '  SPA fallback      -> HTTP %s\n' "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}/portal")"
printf '  未认证 API        -> HTTP %s\n' "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}/api/v1/auth/me")"

step "venv 里的关键扩展（在**本机架构** wheel 上导入成功）"
"$SANDBOX/opt/venv/bin/python" - <<'PYEOF'
import importlib
for mod in ("PIL", "greenlet", "pydantic_core", "argon2", "sqlalchemy", "uvloop", "aiosqlite"):
    try:
        importlib.import_module(mod)
        print(f"  ✓ {mod}")
    except Exception as exc:
        print(f"  ✗ {mod}: {exc}")
PYEOF

step "停止演练服务"
if [ -f "$SANDBOX/var/selftool.pid" ]; then
    kill "$(cat "$SANDBOX/var/selftool.pid")" 2>/dev/null || true
    sleep 2
fi

log "结论"
if [ "$CODE" = "200" ]; then
    echo "  B）install.sh 十步全跑通，/readyz = 200（本机架构 wheel，离线 --no-index 路径）"
    echo "  A）linux wheelhouse 的离线可解性已证明（解析层）"
    echo
    echo "  ⚠ 未验证：linux wheel 在本机无法加载，因此「用发布包里的 linux wheelhouse"
    echo "     真的把服务跑起来」在本机做不到（无 docker/podman/qemu、无 Linux 主机）。"
    echo "     aarch64 的安装与运行同样未验证。"
    echo "  沙箱保留在 ${SANDBOX}（可继续用于备份/恢复演练）"
    exit 0
fi
die "结论：/readyz 未返回 200（实际 ${CODE}）"
