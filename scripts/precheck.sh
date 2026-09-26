#!/usr/bin/env bash
# ============================================================
# scripts/precheck.sh — localcraft 安装前环境自检
# 来源: docs/05《部署与运维方案》§2.6
# 用法: ./precheck.sh
# 退出码: 0 = 无 FAIL 项（WARN 需人工确认）；1 = 存在 FAIL 项
# ============================================================
set -uo pipefail

FAIL=0
ok()   { echo "[ OK ]   $*"; }
warn() { echo "[ WARN ] $*"; }
err()  { echo "[ FAIL ] $*"; FAIL=1; }

# `date -Is` 是 GNU 扩展，macOS/BSD 的 date 不认。用可移植写法。
now_iso() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }

echo "=== localcraft 前置条件自检 ==="
echo "时间: $(now_iso)"
echo

# 1) 发行版
if [ -f /etc/openEuler-release ]; then
    ok "发行版: $(cat /etc/openEuler-release)"
else
    warn "未检测到 /etc/openEuler-release，非 openEuler 系统，仅供测试使用"
fi

# 2) 架构
ARCH="$(uname -m)"
case "$ARCH" in
    x86_64|aarch64) ok "CPU 架构: $ARCH" ;;
    *) err "不支持的 CPU 架构: ${ARCH}（仅支持 x86_64 / aarch64）" ;;
esac

# 3) glibc
GLIBC="$(ldd --version 2>/dev/null | head -n1 | grep -oE '[0-9]+\.[0-9]+$')"
if [ -n "$GLIBC" ]; then
    MAJ="${GLIBC%%.*}"; MIN="${GLIBC##*.}"
    if [ "$MAJ" -gt 2 ] || { [ "$MAJ" -eq 2 ] && [ "$MIN" -ge 28 ]; }; then
        ok "glibc ${GLIBC}（>= 2.28，可安装 manylinux_2_28 及更早标签的 wheel）"
    elif [ "$MAJ" -eq 2 ] && [ "$MIN" -ge 17 ]; then
        warn "glibc ${GLIBC}，仅可安装 manylinux2014 及更早标签的 wheel"
    else
        err "glibc $GLIBC 过旧，无法满足本项目要求"
    fi
else
    warn "无法探测 glibc 版本"
fi

# 4) python3.11
if command -v python3.11 >/dev/null 2>&1; then
    PYVER="$(python3.11 -c 'import sys;print("%d.%d.%d"%sys.version_info[:3])')"
    case "$PYVER" in
        3.11.*) ok "python3.11 可用: $PYVER" ;;
        *)      err "python3.11 版本异常: ${PYVER}（要求 3.11.x）" ;;
    esac
    python3.11 -c 'import venv, ssl, sqlite3, ctypes' 2>/dev/null \
        && ok "标准库模块 venv/ssl/sqlite3/ctypes 均可导入" \
        || err "标准库模块缺失（缺 python3.11-devel 或 python3.11-libs？）"
else
    err "未找到 python3.11，请先执行 docs/05 §2.3 的 rpm 安装"
fi

# 5) pip
if python3.11 -m pip --version >/dev/null 2>&1; then
    ok "pip: $(python3.11 -m pip --version)"
else
    warn "python3.11 -m pip 不可用，请安装 python3.11-pip 或使用 ensurepip"
fi

# 6) 必需命令（与前端托管方式无关的那些）
for c in sqlite3 rsync tar openssl systemctl journalctl curl; do
    if command -v "$c" >/dev/null 2>&1; then ok "命令可用: $c"; else err "缺少命令: $c"; fi
done

# 6b) 按部署模式检查前端托管方式
#
# 生产拓扑有两种（docs/05 §7）：
#   direct —— 后端自己托管前端构建产物，直接 `IP:PORT` 访问，不需要任何反代
#   nginx  —— nginx 反代，负责 TLS / 限流 / 静态加速
#
# 之前 nginx 被无条件列为**必需命令**，于是「直连」这种完全合法的部署方式
# 会在自检阶段就被判 FAIL。现在按模式区分。
WEB_MODE="${LOCALCRAFT_WEB_MODE:-nginx}"
case "$WEB_MODE" in
    direct)
        ok "部署模式: direct —— 应用自己托管前端，跳过 nginx 检查"
        warn "直连请确认 localcraft.env 里 LOCALCRAFT_HOST=0.0.0.0，否则只有本机能访问"
        warn "直连是明文 HTTP：登录密码在局域网内不加密（可用 uvicorn 自带 TLS，或改回 nginx 模式）"
        ;;
    nginx)
        if command -v nginx >/dev/null 2>&1; then
            ok "命令可用: nginx（部署模式 nginx）"
        else
            err "缺少命令: nginx。若本来就不打算用反代，设 LOCALCRAFT_WEB_MODE=direct 后重跑"
        fi
        ;;
    *)
        err "LOCALCRAFT_WEB_MODE 取值无效: ${WEB_MODE}（可用 direct / nginx）"
        ;;
esac

# 7) 可选命令
for c in semanage restorecon setsebool firewall-cmd jq logrotate; do
    if command -v "$c" >/dev/null 2>&1; then ok "命令可用: $c"; else warn "缺少可选命令: $c"; fi
done

# 8) 目录可写性
for d in /etc/localcraft /var/lib/localcraft /opt; do
    if [ -d "$d" ]; then
        ok "目录已存在: $d"
    elif [ -w "$(dirname "$d")" ]; then
        ok "目录可创建: $d"
    else
        warn "路径不可写: $d"
    fi
done

# 9) 端口
if command -v ss >/dev/null 2>&1; then
    for p in 80 443 8000; do
        if ss -lntH 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${p}\$"; then
            warn "端口 $p 已被占用"; else ok "端口 $p 空闲"; fi
    done
else
    warn "缺少 ss 命令，跳过端口占用检查"
fi

# 10) SELinux / 时钟
if command -v getenforce >/dev/null 2>&1; then ok "SELinux: $(getenforce)"; fi
ok "系统时间: $(now_iso)"

echo
if [ "$FAIL" -eq 0 ]; then
    echo "=== 自检通过（WARN 项请人工确认） ==="
    exit 0
else
    echo "=== 自检失败，请先解决 FAIL 项 ==="
    exit 1
fi
