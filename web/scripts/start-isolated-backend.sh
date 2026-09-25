#!/usr/bin/env bash
#
# 启动一个**隔离的**后端实例，供真实后端 E2E（playwright.real.config.ts）使用。
#
# 为什么隔离：`backend/` 归后端 agent 所有，它的 `backend/var/localcraft.db`
# 是并行开发中的共享状态。这个脚本用独立的 DATA_DIR / DATABASE_URL 起一个
# 干净实例（默认 127.0.0.1:8010），因此：
#   - 不碰 `backend/var/`，不与后端 agent 的联调数据互相干扰
#   - 每次运行都是全新的 seed 数据，测试结果可复现
# 它**只运行**后端代码（alembic upgrade + seed-demo + uvicorn），不修改 backend/ 任何文件。
#
# 用法：
#   web/scripts/start-isolated-backend.sh          # 前台启动（Ctrl-C 结束）
#   PORT=8020 DATA_ROOT=/tmp/xxx web/scripts/start-isolated-backend.sh
#
# 然后在另一个终端：
#   cd web && REAL_BASE=http://127.0.0.1:8010 npm run e2e:real

set -euo pipefail

WEB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_DIR="$(cd "${WEB_DIR}/.." && pwd)"
BACKEND_DIR="${REPO_DIR}/backend"
PYTHON="${BACKEND_DIR}/.venv/bin/python"

PORT="${PORT:-8010}"
DATA_ROOT="${DATA_ROOT:-/tmp/localcraft-e2e/backend-${PORT}}"
DB_PATH="${DATA_ROOT}/localcraft.db"

if [[ ! -x "${PYTHON}" ]]; then
  echo "找不到 backend/.venv（${PYTHON}）—— 后端环境未就绪" >&2
  exit 1
fi

mkdir -p "${DATA_ROOT}"

export DATABASE_URL="sqlite+aiosqlite:///${DB_PATH}"
export DATA_DIR="${DATA_ROOT}"
export LOCALCRAFT_PORT="${PORT}"
export COOKIE_SECURE=false
export API_DOCS_ENABLED=true
export SECRET_KEY="${SECRET_KEY:-isolated-e2e-secret-not-for-production}"
export LOG_LEVEL="${LOG_LEVEL:-warning}"

cd "${BACKEND_DIR}"

echo "[isolated-backend] DATA_DIR=${DATA_ROOT}"
echo "[isolated-backend] DB=${DB_PATH}"

"${PYTHON}" -m alembic upgrade head >/dev/null
"${PYTHON}" -m app.cli seed-demo >/dev/null

echo "[isolated-backend] 已迁移并 seed，启动 http://127.0.0.1:${PORT}"
exec "${PYTHON}" -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT}" --log-level warning
