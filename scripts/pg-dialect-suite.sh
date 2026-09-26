#!/usr/bin/env bash
#
# 在本机 PostgreSQL 上跑后端全量测试套件（方言 = PostgreSQL）。
#
# 为什么需要这个脚本（M10）：
#
#   1. **每次都必须重建库。** `tests/conftest.py` 的 `seeded` 夹具是 session 级
#      且**不判重**地插入固定用户/工具，迁移也不会重头来过。所以套件**不能跑在
#      脏库上** —— 残留 schema + 残留数据会让全量套件从「全绿」直接变成几百个
#      error（监控方实测：521 errors）。本脚本每次都 dropdb + createdb。
#   2. **时区必须能固化并复现。** PG 的会话时区决定 aware datetime 读回来的
#      偏移（`Asia/Shanghai` 会暴露 UTC 下看不到的表示不一致，见 docs/09 §16.8）。
#      CI 用矩阵跑 UTC + Asia/Shanghai，本地要能一键复现同样的两个条件。
#
# 用法：
#
#   scripts/pg-dialect-suite.sh                 # 默认 Asia/Shanghai
#   scripts/pg-dialect-suite.sh UTC
#   scripts/pg-dialect-suite.sh Asia/Shanghai -k test_token  # 额外参数透传给 pytest
#
# 连接参数可用环境变量覆盖（默认指向本机 5432 上的 localcraft 超级用户）：
#
#   PGHOST=127.0.0.1 PGPORT=55432 PGUSER=localcraft PGPASSWORD=... \
#     scripts/pg-dialect-suite.sh UTC
#
# 需要 PATH 里有 psql / createdb / dropdb（macOS Homebrew：
# `export PATH=/opt/homebrew/bin:$PATH`）。
#
# 注意：本脚本**只**允许操作名字以 `_test` 结尾的库（防手滑 drop 掉真库）。
set -euo pipefail

TZ_NAME="${1:-Asia/Shanghai}"
shift || true

PGHOST="${PGHOST:-127.0.0.1}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-localcraft}"
PGDB="${PGDB:-localcraft_test}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
PYTHON="${PYTHON:-$BACKEND_DIR/.venv/bin/python}"

case "$PGDB" in
  *_test) ;;
  *)
    echo "[error] 拒绝操作库名不以 _test 结尾的数据库：${PGDB}（本脚本会 dropdb）" >&2
    exit 2
    ;;
esac

if [ ! -x "$PYTHON" ]; then
  echo "[error] 找不到解释器 ${PYTHON}（用 PYTHON=... 覆盖）" >&2
  exit 2
fi

for bin in dropdb createdb psql; do
  command -v "$bin" >/dev/null 2>&1 || {
    echo "[error] PATH 里没有 ${bin}（Homebrew 可 export PATH=/opt/homebrew/bin:\${PATH}）" >&2
    exit 2
  }
done

echo "== 重建测试库 ${PGDB}@${PGHOST}:${PGPORT}（套件不能跑在脏库上）=="
dropdb -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" --if-exists "$PGDB"
createdb -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -O "$PGUSER" "$PGDB"
psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDB" -q -v ON_ERROR_STOP=1 \
  -c "ALTER DATABASE $PGDB SET timezone TO '$TZ_NAME';"
echo "== PG 会话时区：$(psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDB" -tAc 'show timezone;') =="

cd "$BACKEND_DIR"
LOCALCRAFT_TEST_DATABASE_URL="postgresql+psycopg://$PGUSER@$PGHOST:$PGPORT/$PGDB" \
  "$PYTHON" -m pytest "$@"
