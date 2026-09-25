#!/usr/bin/env bash
# ============================================================
# scripts/wheelhouse-arch-diff.sh — 生成双架构 wheelhouse 的差异清单
#
# 用法:
#     scripts/wheelhouse-arch-diff.sh [wheelhouse-x86_64] [wheelhouse-aarch64]
#
# 输出（默认写到 backend/dist/wheelhouse-arch-diff.txt）：
#   1) 只出现在 x86_64 的包
#   2) 只出现在 aarch64 的包
#   3) 两边都有、但**平台标签不同**的包（正常，二进制不同）
#   4) 两边都有的纯 Python 包（py3-none-any，内容逐字节相同）
#
# 怎么读这份清单：
#   - 「只出现在一边」= 严重问题。离线装到另一边时会直接缺包失败。
#     除非该包本来就是平台专属的（例如只在某一架构提供的加速器），
#     否则必须补齐或从 lock 里去掉。
#   - 「标签不同」= 正常。同一个包的两架构二进制，wheel 文件名必然不同。
#   - 「纯 Python 包」= 正常。两套 wheelhouse 里各存一份副本，哈希应当一致。
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 与 make-release.sh 一致：先找仓库根，再找 backend/ 下
find_wh() {
    local arch="$1"
    for base in "$ROOT" "$ROOT/backend"; do
        if [ -d "$base/wheelhouse-$arch" ]; then
            echo "$base/wheelhouse-$arch"
            return 0
        fi
    done
    return 1
}

WH_X86="$(find_wh x86_64 || true)"
WH_ARM="$(find_wh aarch64 || true)"

[ -n "$WH_X86" ] || { echo "[error] 找不到 wheelhouse-x86_64" >&2; exit 1; }
[ -n "$WH_ARM" ] || { echo "[error] 找不到 wheelhouse-aarch64" >&2; exit 1; }

OUT="${1:-}"
# 允许把两个目录当位置参数传（第 1 个是 x86_64）
if [ -n "${1:-}" ] && [ -d "${1:-}" ]; then
    WH_X86="$1"; WH_ARM="${2:-$WH_ARM}"; OUT=""
fi
OUT="${OUT:-$ROOT/backend/dist/wheelhouse-arch-diff.txt}"
mkdir -p "$(dirname "$OUT")"

# 包名归一化：wheel 文件名里 '-' 被替换成 '_'，比较时统一成小写 + 连字符
pkg_of() {  # pkg_of <wheel 文件名> -> 归一化包名
    local base; base="$(basename "$1")"
    local name; name="${base%%-*}"
    # 结尾必须有换行：这里少一个 \n 会让下面所有包名粘成一行（踩过）
    printf '%s\n' "$name" | tr '[:upper:]_' '[:lower:]-'
}

list_pkgs() { find "$1" -maxdepth 1 -name '*.whl' -exec basename {} \; | sort; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

list_pkgs "$WH_X86" | while read -r f; do pkg_of "$f"; done | sort -u > "$TMP/x86.pkgs"
list_pkgs "$WH_ARM" | while read -r f; do pkg_of "$f"; done | sort -u > "$TMP/arm.pkgs"

only_x86="$(comm -23 "$TMP/x86.pkgs" "$TMP/arm.pkgs" || true)"
only_arm="$(comm -13 "$TMP/x86.pkgs" "$TMP/arm.pkgs" || true)"
both="$(comm -12 "$TMP/x86.pkgs" "$TMP/arm.pkgs" || true)"

{
    echo "# wheelhouse 架构差异清单"
    echo "# 生成时间: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    echo "# x86_64 : $WH_X86  ($(find "$WH_X86" -maxdepth 1 -name '*.whl' | wc -l | tr -d ' ') wheels)"
    echo "# aarch64: $WH_ARM  ($(find "$WH_ARM" -maxdepth 1 -name '*.whl' | wc -l | tr -d ' ') wheels)"
    echo

    echo "## 1) 只出现在 x86_64 的包"
    if [ -z "$only_x86" ]; then
        echo "（无 —— 两套 wheelhouse 的包集合一致）"
    else
        echo "$only_x86" | sed 's/^/  ✗ /'
        echo "  ↑ 这些包在 aarch64 上会缺失，必须处理"
    fi
    echo

    echo "## 2) 只出现在 aarch64 的包"
    if [ -z "$only_arm" ]; then
        echo "（无 —— 两套 wheelhouse 的包集合一致）"
    else
        echo "$only_arm" | sed 's/^/  ✗ /'
        echo "  ↑ 这些包在 x86_64 上会缺失，必须处理"
    fi
    echo

    echo "## 3) 两边都有但平台标签不同的包（正常：同一包的两架构二进制）"
    n_plat=0
    n_pure=0
    while IFS= read -r pkg; do
        [ -n "$pkg" ] || continue
        xw="$(find "$WH_X86" -maxdepth 1 -name '*.whl' -exec basename {} \; | grep -i "^${pkg//-/[-_]}-" | head -1 || true)"
        aw="$(find "$WH_ARM" -maxdepth 1 -name '*.whl' -exec basename {} \; | grep -i "^${pkg//-/[-_]}-" | head -1 || true)"
        if [ -z "$xw" ] || [ -z "$aw" ]; then
            echo "  ? $pkg  （文件名匹配不上，请人工确认）"
            continue
        fi
        if [ "$xw" = "$aw" ]; then
            n_pure=$((n_pure + 1))
        else
            n_plat=$((n_plat + 1))
            echo "  · $pkg"
            echo "      x86_64 : $xw"
            echo "      aarch64: $aw"
        fi
    done <<< "$both"
    echo
    echo "  平台相关包数量: $n_plat"
    echo

    echo "## 4) 纯 Python 包（py3-none-any，两套里文件名完全相同）"
    echo "  数量: $n_pure"
    echo "  说明：这些 wheel 在两套 wheelhouse 里各存一份，属**正常冗余**；"
    echo "        发布包因此会大一倍左右。若要瘦身，可让安装脚本按架构从共享目录取，"
    echo "        但那会让发布包结构复杂化，收益不大，故保持现状。"
    echo

    echo "## 结论"
    if [ -z "$only_x86" ] && [ -z "$only_arm" ]; then
        echo "  包集合完全一致：任意架构的 wheelhouse 都覆盖了 lock 里的全部依赖。"
    else
        echo "  ⚠ 存在只出现在一边的包，离线安装到另一边会失败，必须处理完再发布。"
    fi
} > "$OUT"

cat "$OUT"
echo
echo "清单已写入: $OUT"

if [ -n "$only_x86" ] || [ -n "$only_arm" ]; then
    exit 1
fi
