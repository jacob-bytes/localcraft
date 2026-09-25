#!/usr/bin/env bash
# ============================================================
# scripts/build-wheelhouse.sh — 在构建机上产出双架构离线 wheelhouse
#
# 用法:
#     scripts/build-wheelhouse.sh [x86_64|aarch64|all] [--force]
#
# 例:
#     scripts/build-wheelhouse.sh            # 两套都建
#     scripts/build-wheelhouse.sh aarch64    # 只建 aarch64
#
# 依据: docs/05《部署与运维方案》§3.4（分架构下载 wheel）
#
# 产物位置说明:
#   docs/05 §3.4 的示例把 wheelhouse 建在「源码根目录」。本仓库把构建产物
#   统一放在 backend/ 下（backend/wheelhouse-<arch>），原因有二：
#     1) 仓库根目录的写权限不属于后端 agent 的边界；
#     2) backend/dist 已经是发布包的暂存目录，构建产物放一起更好清理。
#   make-release.sh 会依次在 <repo>/wheelhouse-<arch> 与
#   <repo>/backend/wheelhouse-<arch> 里找，两种布局都能用。
#
# 三条铁律（docs/05 §3.4.1）:
#   1) --only-binary=:all:  绝不下载 sdist。缺 wheel 就报错，暴露在构建机上。
#   2) --python-version 3.11  构建机可能是 3.12，不指定会拉错 ABI。
#   3) --platform 可以重复多次，穷举目标机能接受的所有标签。
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND="$ROOT/backend"
LOCK="$BACKEND/requirements.lock"

TARGET="${1:-all}"
FORCE=0
for arg in "$@"; do
    [ "$arg" = "--force" ] && FORCE=1
done

PYTHON_BIN="${PYTHON_BIN:-$BACKEND/.venv/bin/python}"
[ -x "$PYTHON_BIN" ] || PYTHON_BIN="$(command -v python3.11 || command -v python3)"

log()  { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[warn] %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m[error] %s\033[0m\n' "$*" >&2; exit 1; }

[ -f "$LOCK" ] || die "找不到 $LOCK"
[ -n "$PYTHON_BIN" ] || die "找不到可用的 Python 3.11 解释器（可用 PYTHON_BIN 指定）"

# ------------------------------------------------------------
# 平台标签集合
# ------------------------------------------------------------
# 目标机是 openEuler 24.03（glibc 2.38），因此 manylinux_2_X（X ≤ 38）都可用。
# **manylinux 不向上兼容**：目标机能跑 2_28，但只给 manylinux2014(2_17) 标签时
# pip 找不到只发布了 2_28 的包。所以从高到低全列上（docs/05 §3.4.4）。
#
# 刻意**不列** musllinux_*：openEuler 是 glibc，装 musl wheel 会在运行期
# 因找不到动态链接器直接崩。
platforms_for() {
    case "$1" in
        x86_64)
            cat <<'EOF'
manylinux_2_38_x86_64
manylinux_2_36_x86_64
manylinux_2_34_x86_64
manylinux_2_31_x86_64
manylinux_2_28_x86_64
manylinux_2_27_x86_64
manylinux_2_24_x86_64
manylinux_2_17_x86_64
manylinux2014_x86_64
manylinux2010_x86_64
manylinux1_x86_64
linux_x86_64
EOF
            ;;
        aarch64)
            cat <<'EOF'
manylinux_2_38_aarch64
manylinux_2_36_aarch64
manylinux_2_34_aarch64
manylinux_2_31_aarch64
manylinux_2_28_aarch64
manylinux_2_27_aarch64
manylinux_2_24_aarch64
manylinux_2_17_aarch64
manylinux2014_aarch64
linux_aarch64
EOF
            ;;
        *) die "未知架构: $1（只支持 x86_64 / aarch64）" ;;
    esac
}

build_one() {
    local arch="$1"
    local dest="$BACKEND/wheelhouse-$arch"

    log "构建 wheelhouse-$arch"

    if [ -d "$dest" ] && [ "$FORCE" -ne 1 ]; then
        if [ -n "$(ls -A "$dest" 2>/dev/null || true)" ]; then
            warn "$dest 已存在且非空，跳过（用 --force 重建）"
            return 0
        fi
    fi

    rm -rf "$dest"
    mkdir -p "$dest"

    local args=()
    while IFS= read -r p; do
        [ -n "$p" ] && args+=(--platform "$p")
    done < <(platforms_for "$arch")

    # --only-binary=:all: 是关键：缺 wheel 会**明确失败**，而不是悄悄下 sdist。
    # 失败时脚本非零退出，绝不产出一个「看起来完整、实际装不上」的 wheelhouse。
    "$PYTHON_BIN" -m pip download \
        --dest "$dest" \
        --requirement "$LOCK" \
        --only-binary=:all: \
        --python-version 3.11 \
        --implementation cp \
        "${args[@]}" \
        --no-cache-dir \
        --disable-pip-version-check

    # ---- bootstrap 包：pip / setuptools / wheel ----
    # 这三个**不在** requirements.lock 里（它们是工具链不是应用依赖），
    # 但 install.sh 第 5 步要 `pip install --upgrade pip setuptools wheel`。
    # 完全离线的目标机上没有它们就会直接失败 —— 这是 M4 演练时实测到的真实缺陷
    # （python3.11 -m venv 自带 pip + setuptools，但**不带 wheel**）。
    # 因此把它们一起下进 wheelhouse，让离线 bootstrap 自洽。
    log "  补齐 bootstrap 包（pip / setuptools / wheel）"
    "$PYTHON_BIN" -m pip download \
        --dest "$dest" \
        --only-binary=:all: \
        --python-version 3.11 \
        --implementation cp \
        "${args[@]}" \
        --no-cache-dir \
        --disable-pip-version-check \
        pip setuptools wheel

    # PostgreSQL 驱动（契约 §18.5）：默认不装，但离线环境要能按需装上。
    # 它是 optional extra（pyproject 的 [project.optional-dependencies] pg），
    # 所以不在 requirements.lock 里，必须在这里单独下。
    log "  补齐 PostgreSQL 驱动（psycopg[binary]）"
    "$PYTHON_BIN" -m pip download \
        --dest "$dest" \
        --only-binary=:all: \
        --python-version 3.11 \
        --implementation cp \
        "${args[@]}" \
        --no-cache-dir \
        --disable-pip-version-check \
        "psycopg[binary]"

    # ---- 门禁 1：绝不能出现 sdist ----
    local sdists
    sdists="$(find "$dest" -maxdepth 1 -type f ! -name '*.whl' -print)"
    if [ -n "$sdists" ]; then
        printf '%s\n' "$sdists" >&2
        die "wheelhouse-$arch 里出现了非 wheel 文件（源码包会导致离线安装现场编译失败）"
    fi

    # ---- 门禁 2：包数量要与 lock 对得上 ----
    local n_lock n_wheel
    # +3 = bootstrap（pip/setuptools/wheel）；psycopg 是 extra，不在 lock 里，单独核
    n_lock=$(( $(grep -cE '^[A-Za-z0-9._-]+==' "$LOCK" || true) + 3 ))
    n_wheel="$(find "$dest" -maxdepth 1 -name '*.whl' | wc -l | tr -d ' ')"
    printf '  lock 中包数=%s  wheel 数=%s\n' "$n_lock" "$n_wheel"

    # ---- 生成 MANIFEST.sha256（相对路径，搬迁后仍可校验）----
    generate_manifest "$dest"

    printf '  → %s\n' "$dest"
    du -sh "$dest" | sed 's/^/  /'
}

# MANIFEST 必须在 wheelhouse 目录内生成，且用相对路径：
# 发布包解压到别的路径后校验仍然成立。
generate_manifest() {
    local dir="$1"
    (
        cd "$dir"
        if command -v sha256sum >/dev/null 2>&1; then
            find . -maxdepth 1 -name '*.whl' -print0 | sort -z | xargs -0 sha256sum \
                | sed 's|  \./|  |' > MANIFEST.sha256
        else
            find . -maxdepth 1 -name '*.whl' -print0 | sort -z | xargs -0 shasum -a 256 \
                | sed 's|  \./|  |' > MANIFEST.sha256
        fi
    )
}

case "$TARGET" in
    all)     build_one x86_64; build_one aarch64 ;;
    x86_64|aarch64) build_one "$TARGET" ;;
    *) die "用法: $(basename "$0") [x86_64|aarch64|all] [--force]" ;;
esac

log "完成"
echo "下一步（可选）："
echo "  scripts/wheelhouse-arch-diff.sh      # 列出只出现在一边的包"
echo "  scripts/verify-wheelhouse.sh         # 校验每个 wheel 的平台标签"
