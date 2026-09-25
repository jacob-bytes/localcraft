#!/usr/bin/env bash
# ============================================================
# scripts/verify-wheelhouse.sh — 校验 wheelhouse 的完整性（**不安装**）
#
# 用法:
#     scripts/verify-wheelhouse.sh [架构] [wheelhouse 目录]
#     scripts/verify-wheelhouse.sh                 # 两套都查
#     scripts/verify-wheelhouse.sh x86_64 backend/wheelhouse-x86_64
#
# 它检查什么（按 wheel 文件名的平台标签，逐条）：
#   1. 目录存在且非空
#   2. **零个 sdist**（.tar.gz / .zip）—— 源码包意味着目标机要现场编译，离线必炸
#   3. 每个 wheel 都是合法的 wheel 文件名（至少 5 段：name-ver-py-abi-plat）
#   4. 非纯 Python 的 wheel（abi 不是 none）必须带**目标架构**的平台标签
#   5. 反面检查：不得出现**别的架构**的标签（x86_64 目录里混进 aarch64 的包）
#   6. 不得出现 musllinux_*（openEuler 是 glibc，装 musl wheel 运行期必崩）
#   7. 平台标签的 glibc 下限不得高于目标机（openEuler 24.03 = glibc 2.38）
#   8. MANIFEST.sha256 校验通过，且**覆盖全部 wheel**
#
# 它**不做**什么（重要，别把两者混为一谈）：
#   - 不校验二进制是否能在这台机器上加载
#   - 不校验 aarch64 的 wheel 在真实 aarch64 机器上能装能跑
#   想证明后者，只能在对应架构的机器（或 qemu 用户态模拟）上真跑一遍
#   `pip install --no-index --find-links=<wheelhouse> -r requirements.lock.txt`。
# ============================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

#: 目标机 glibc 上限（openEuler 24.03 LTS = glibc 2.38）
MAX_GLIBC_MINOR="${MAX_GLIBC_MINOR:-38}"

PASS=0
FAIL=0
ok()   { printf '  \033[1;32m✓\033[0m %s\n' "$*"; PASS=$((PASS + 1)); }
bad()  { printf '  \033[1;31m✗\033[0m %s\n' "$*"; FAIL=$((FAIL + 1)); }
info() { printf '  · %s\n' "$*"; }

find_wh() {
    local arch="$1"
    for base in "$ROOT" "$ROOT/backend"; do
        [ -d "$base/wheelhouse-$arch" ] && { echo "$base/wheelhouse-$arch"; return 0; }
    done
    return 1
}

verify_one() {
    local arch="$1" dir="$2"
    printf '\n\033[1m== wheelhouse-%s (%s) ==\033[0m\n' "$arch" "$dir"

    [ -d "$dir" ] || { bad "目录不存在"; return; }
    local total
    total="$(find "$dir" -maxdepth 1 -name '*.whl' | wc -l | tr -d ' ')"
    [ "$total" -gt 0 ] && ok "含 $total 个 wheel" || { bad "目录为空"; return; }

    # ---- 1b) 关键包必须在场 ----
    # 这些包缺了会导致特定路径不可用而非整体装不上，pip 解析不会报错，
    # 所以必须显式点名检查（契约 §18.5 的 psycopg 就是这一类）。
    local pkg missing_pkgs=""
    for pkg in psycopg psycopg_binary pip setuptools wheel; do
        if ! find "$dir" -maxdepth 1 -name "${pkg}-*.whl" | grep -q .; then
            missing_pkgs="${missing_pkgs} ${pkg}"
        fi
    done
    if [ -z "$missing_pkgs" ]; then
        ok "关键包在场（psycopg / psycopg_binary / pip / setuptools / wheel）"
    else
        bad "缺少关键包：${missing_pkgs}（离线时相应路径不可用）"
    fi
    # 原生扩展重点核验（M4/M5 明确列出）
    for pkg in pillow greenlet pydantic_core argon2_cffi_bindings; do
        if find "$dir" -maxdepth 1 -name "${pkg}-*.whl" | grep -q .; then
            ok "原生扩展在场：${pkg}"
        else
            bad "缺少原生扩展：${pkg}"
        fi
    done

    # ---- 2) 零 sdist ----
    local sdists
    sdists="$(find "$dir" -maxdepth 1 -type f \( -name '*.tar.gz' -o -name '*.tgz' -o -name '*.zip' \) -exec basename {} \;)"
    if [ -z "$sdists" ]; then
        ok "零个 sdist（源码包）"
    else
        bad "存在 sdist，离线安装会现场编译："
        printf '      %s\n' $sdists
    fi

    # ---- 3~7) 逐 wheel 检查文件名 ----
    local bad_name=0 bad_arch=0 bad_musl=0 bad_glibc=0 pure=0 native=0
    local other_arch
    case "$arch" in
        x86_64)  other_arch="aarch64|arm64|i686|ppc64|s390x" ;;
        aarch64) other_arch="x86_64|amd64|i686|ppc64|s390x" ;;
        *)       other_arch="aarch64|arm64|x86_64|amd64|i686" ;;
    esac

    while IFS= read -r whl; do
        local base; base="$(basename "$whl")"
        # 去掉 .whl 后按 '-' 切分；name 与 version 里不会出现 '-'（wheel 规范要求归一化为 '_'）
        local stem="${base%.whl}"
        local nseg; nseg="$(awk -F'-' '{print NF}' <<< "$stem")"
        if [ "$nseg" -lt 5 ]; then
            bad_name=$((bad_name + 1)); info "文件名段数不足（${nseg}）：$base"; continue
        fi
        local tag_plat; tag_plat="$(cut -d'-' -f5- <<< "$stem")"
        local tag_abi;  tag_abi="$(cut -d'-' -f4 <<< "$stem")"

        # musl 必须没有
        if grep -qi 'musllinux' <<< "$tag_plat"; then
            bad_musl=$((bad_musl + 1)); info "musl 平台标签：$base"; continue
        fi

        # 「纯 Python」的判定必须是 abi=none **且** 平台标签里有一个**恰好等于** any 的项。
        # 不能用 `grep -q any` —— `manylinux` 里就含子串 "any"，
        # 那会把 ruff 这类 `py3-none-manylinux_*`（Rust 二进制）误判成纯 Python，
        # 从而跳过下面的平台标签校验。平台标签是点分列表，逐项精确比较。
        if [ "$tag_abi" = "none" ] && tr '.' '\n' <<< "$tag_plat" | grep -qx 'any'; then
            pure=$((pure + 1))
            continue
        fi
        native=$((native + 1))

        # 必须带目标架构标签
        if ! grep -qE "$arch" <<< "$tag_plat"; then
            bad_arch=$((bad_arch + 1)); info "缺少 $arch 标签：$base"; continue
        fi
        # 不得带别的架构标签
        if grep -qE "$other_arch" <<< "$tag_plat"; then
            bad_arch=$((bad_arch + 1)); info "混入了其他架构标签：$base"; continue
        fi
        # glibc 下限不得高于目标机
        local minors; minors="$(grep -oE 'manylinux_2_[0-9]+' <<< "$tag_plat" | sed 's/manylinux_2_//' | sort -n)"
        if [ -n "$minors" ]; then
            local lowest; lowest="$(head -1 <<< "$minors")"
            if [ "$lowest" -gt "$MAX_GLIBC_MINOR" ]; then
                bad_glibc=$((bad_glibc + 1))
                info "要求 glibc 2.$lowest > 目标机 2.${MAX_GLIBC_MINOR}：$base"
            fi
        fi
    done < <(find "$dir" -maxdepth 1 -name '*.whl' | sort)

    [ "$bad_name" -eq 0 ] && ok "所有 wheel 文件名格式合法" || bad "$bad_name 个 wheel 文件名不合法"
    [ "$bad_musl" -eq 0 ] && ok "无 musl 标签（openEuler 是 glibc）" || bad "$bad_musl 个 wheel 带 musl 标签"
    [ "$bad_arch" -eq 0 ] && ok "非纯 Python wheel（$native 个）平台标签与 $arch 匹配" \
                          || bad "$bad_arch 个 wheel 的平台标签与 $arch 不匹配"
    [ "$bad_glibc" -eq 0 ] && ok "无 wheel 要求高于 glibc 2.$MAX_GLIBC_MINOR" \
                           || bad "$bad_glibc 个 wheel 的 glibc 下限过高"
    info "纯 Python wheel（any）: $pure 个；平台相关 wheel: $native 个"

    # ---- 8) MANIFEST ----
    local manifest="$dir/MANIFEST.sha256"
    if [ ! -f "$manifest" ]; then
        bad "缺少 MANIFEST.sha256"
    else
        local n_manifest n_files
        n_manifest="$(grep -c . "$manifest" || true)"
        n_files="$(find "$dir" -maxdepth 1 -name '*.whl' | wc -l | tr -d ' ')"
        if [ "$n_manifest" -eq "$n_files" ]; then
            ok "MANIFEST.sha256 覆盖全部 $n_files 个 wheel"
        else
            bad "MANIFEST 覆盖 $n_manifest 个，实际 $n_files 个 wheel"
        fi
        if (cd "$dir" && { command -v sha256sum >/dev/null 2>&1 && sha256sum -c MANIFEST.sha256 \
            || shasum -a 256 -c MANIFEST.sha256; } >/dev/null 2>&1); then
            ok "MANIFEST.sha256 校验通过"
        else
            bad "MANIFEST.sha256 校验失败（文件被改动或清单过期）"
        fi
    fi
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    sed -n '2,30p' "$0"; exit 0
fi

if [ -n "${1:-}" ] && [ -d "${1:-}" ]; then
    # 直接给目录：从路径推断架构
    verify_one "$(basename "$1" | sed 's/^wheelhouse-//')" "$1"
elif [ -n "${1:-}" ]; then
    dir="${2:-$(find_wh "$1" || true)}"
    [ -n "$dir" ] || { echo "[error] 找不到 wheelhouse-$1" >&2; exit 1; }
    verify_one "$1" "$dir"
else
    for arch in x86_64 aarch64; do
        dir="$(find_wh "$arch" || true)"
        [ -n "$dir" ] && verify_one "$arch" "$dir" || bad "找不到 wheelhouse-$arch"
    done
fi

printf '\n\033[1m===== 汇总 =====\033[0m\n'
printf '  通过 %d 项，失败 %d 项\n' "$PASS" "$FAIL"
echo
echo "提醒：以上全部是**文件级**检查，等价于「标签对得上」。"
echo "      它**不能**代替在真实 aarch64 机器上跑一遍离线安装。"
[ "$FAIL" -eq 0 ] || exit 1
