#!/usr/bin/env bash
# =============================================================================
# selftool 本地预览（生产拓扑：后端直接托管前端构建产物，单一 URL）
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

if [[ ! -x "$PY" ]]; then
  echo "找不到后端虚拟环境：$PY" >&2
  echo "先执行：cd backend && python3.11 -m venv .venv && .venv/bin/pip install -r requirements.lock" >&2
  exit 1
fi

if [[ ! -f "$ROOT/web/dist/index.html" ]]; then
  echo "前端产物不存在，正在构建…"
  (cd "$ROOT/web" && npm run build)
fi

if [[ "$RESET" == "1" ]]; then
  echo "==> 丢弃预览数据（$PREVIEW）"
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
    普通用户  wangwu     / Author@12345
    普通用户  zhangsan   / Author@12345
    普通用户  lisi       / Author@12345
    只读访客  viewer     / Viewer@12345   ← 能看详情但下载被拒（FR-ACL-05）
    需改密    newbie     / Newbie@12345   ← 登录后会被强制跳转改密页

  注：当前种子里没有独立的 approver 账号（wangwu 是 user），
      审批台的功能请用 admin 预览；角色菜单渲染要到 M6 J-11 补齐后才能验。

  停止：Ctrl-C
============================================================

EOF

cd "$BE"
exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --log-level warning
