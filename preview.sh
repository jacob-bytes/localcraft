#!/usr/bin/env bash
# =============================================================================
# localcraft 本地预览（生产拓扑：后端直接托管前端构建产物，单一 URL）
#
#   bash preview.sh            # 首次会自动建库+播种，然后起服务
#   bash preview.sh --reset    # 丢弃预览数据，重新建库+播种
#   PORT=8090 bash preview.sh  # 换端口
#
# 数据落在 <仓库根>/.preview/（已在 .gitignore 中），不碰 backend/var。
# 访问 http://127.0.0.1:<PORT>  —— 不需要单独起前端，也没有代理。
#
# 与生产的差异（预览时请知悉）：
#   - HTTP 而非 HTTPS；COOKIE_SECURE=false
#   - 单进程直接监听，前面没有 nginx
#   - 数据库是临时库，随便折腾；--reset 可随时重来
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BE="$ROOT/backend"
PORT="${PORT:-8000}"
PREVIEW="$ROOT/.preview"
PY="$BE/.venv/bin/python"

RESET=0
[[ "${1:-}" == "--reset" ]] && RESET=1

# ---- 后端虚拟环境：不存在就自动创建 ----------------------------------------
# 目标是把「git clone 之后一条命令跑起来」这件事做实，所以这里不做「请先手动
# 建 venv」的失败退出。优先用 python3.11（与生产一致），退而求其次用 python3，
# 但会检查版本 —— 低于 3.11 直接报错，避免跑出「本地能跑、生产语法不兼容」。
if [[ ! -x "$PY" ]]; then
  PYTHON_BIN="${PYTHON:-}"
  if [[ -z "$PYTHON_BIN" ]]; then
    if command -v python3.11 >/dev/null 2>&1; then
      PYTHON_BIN=python3.11
    elif command -v python3 >/dev/null 2>&1; then
      PYTHON_BIN=python3
    else
      echo "找不到 python3，请先安装 Python 3.11 或更高版本" >&2
      exit 1
    fi
  fi

  if ! "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    echo "$PYTHON_BIN 版本过低：需要 Python 3.11+，当前 $("$PYTHON_BIN" -V 2>&1)" >&2
    exit 1
  fi

  echo "==> 创建后端虚拟环境（$BE/.venv，使用 $PYTHON_BIN）"
  "$PYTHON_BIN" -m venv "$BE/.venv"
  "$PY" -m pip install --quiet --upgrade pip

  if [[ -f "$BE/requirements.lock" ]]; then
    echo "==> 安装后端依赖（requirements.lock，全量锁定）"
    "$PY" -m pip install --quiet -r "$BE/requirements.lock"
  else
    echo "==> 安装后端依赖（pyproject 的 dev extra，未找到 lock）"
    "$PY" -m pip install --quiet -e "$BE[dev]"
  fi
fi

# ---- 前端：依赖与构建产物都按需补齐 -----------------------------------------
if [[ ! -d "$ROOT/web/node_modules" ]]; then
  if [[ -f "$ROOT/web/package-lock.json" ]]; then
    echo "==> 安装前端依赖（npm ci）"
    (cd "$ROOT/web" && npm ci)
  else
    echo "==> 安装前端依赖（npm install）"
    (cd "$ROOT/web" && npm install)
  fi
fi

if [[ ! -f "$ROOT/web/dist/index.html" ]]; then
  echo "==> 构建前端产物（npm run build）"
  (cd "$ROOT/web" && npm run build)
fi

if [[ "$RESET" == "1" ]]; then
  echo "==> 丢弃预览数据（${PREVIEW}）"
  rm -rf "$PREVIEW"
fi

mkdir -p "$PREVIEW"

# 预览专用配置。SECRET_KEY 固定，这样重启后已签发的图片 URL 与登录态仍有效。
export DATABASE_URL="sqlite+aiosqlite:///$PREVIEW/preview.db"
export DATA_DIR="$PREVIEW/data"
export SECRET_KEY="local-preview-only-not-for-production"
export COOKIE_SECURE=false
export LOG_LEVEL="${LOG_LEVEL:-info}"
export API_DOCS_ENABLED=true

if [[ ! -f "$PREVIEW/preview.db" ]]; then
  echo "==> 初始化数据库（alembic upgrade head）"
  (cd "$BE" && "$PY" -m alembic upgrade head)
  echo "==> 播种演示数据（26 个工具 / 6 个账号）"
  (cd "$BE" && "$PY" -m app.cli seed-demo)
fi

cat <<EOF

============================================================
  预览地址   http://127.0.0.1:${PORT}
  接口文档   http://127.0.0.1:${PORT}/docs
  数据目录   ${PREVIEW}
  接口文档开关 API_DOCS_ENABLED=true（生产默认关闭）

  可用账号（全部为演示数据）
    超管      admin      / Admin@12345    ← 用它可以访问全部页面
    审批员    wangwu     / Author@12345   ← user + approver，用它预览审批台
    普通用户  zhangsan   / Author@12345
    普通用户  lisi       / Author@12345
    只读访客  viewer     / Viewer@12345   ← 能看详情但下载被拒（FR-ACL-05）
    需改密    newbie     / Newbie@12345   ← 登录后会被强制跳转改密页

  注：wangwu 是种子中唯一的 approver（用户 J-11）。用它验证「审批员看不到
      超管菜单」这类角色边界；admin 也能进审批台，但看不出角色差异。

  停止：Ctrl-C
============================================================

EOF

cd "$BE"
exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --log-level warning
