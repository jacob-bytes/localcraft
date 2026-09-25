#!/usr/bin/env bash
# ============================================================
# scripts/m4-drill-perf.sh — 100 并发性能验证（NFR-PERF-01）
#
# 用法: scripts/m4-drill-perf.sh [端口] [工具数]
#
# 目标（docs/01 NFR-PERF-01）：门户列表与工具详情在 100 并发下 P95 < 300ms。
#
# 环境约定（易错点清单第 13 条）：服务必须是**单 worker**
# （docs/05 §6.3：SQLite WAL 单写者，多 worker 只会加剧写锁竞争）。
# 本脚本显式用 --workers 1 启动，并在结论里声明。
#
# 用 `ab` 做读接口的并发压测（它自带 P95 百分比行）；
# 下载接口用自写异步脚本（要逐个签发票据，ab 表达不了）。
# ============================================================
set -uo pipefail

PORT="${1:-8000}"
TOOL_COUNT="${2:-200}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND="$ROOT/backend"
SANDBOX="${SELTOOL_DRILL_SANDBOX:-$BACKEND/dist/drill}"
RELEASE_DIR="$(find "$SANDBOX/releases" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | head -1)"
VPY="$SANDBOX/opt/venv/bin/python"
VDATA="$SANDBOX/var"
VENVF="$SANDBOX/etc/selftool.env"
BASE="http://127.0.0.1:${PORT}"
OUT="$SANDBOX/perf"
AB="$(command -v ab || echo /usr/sbin/ab)"

log()  { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
step() { printf '\n\033[1m--- %s ---\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31m[error] %s\033[0m\n' "$*" >&2; exit 1; }

[ -x "$VPY" ] || die "沙箱不存在，请先跑 scripts/m4-drill-install.sh"
[ -x "$AB" ] || die "缺少 ab（httpd-tools / apache2-utils）"

start_service() {
    set -a
    # shellcheck disable=SC1090  # $VENVF 由命令行动态决定，shellcheck 无法静态跟踪
    . "$VENVF"
    set +a
    ( cd "$SANDBOX/opt/app/current" || exit 1
      nohup "$VPY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --workers 1 \
          >> "$SANDBOX/log/selftool-stdout.log" 2>&1 &
      echo $! > "$VDATA/selftool.pid" )
    "$RELEASE_DIR/scripts/wait-healthy.sh" --url "${BASE}/readyz" --timeout 60 >/dev/null \
        || die "服务起不来"
}
stop_service() {
    if [ -f "$VDATA/selftool.pid" ] && kill -0 "$(cat "$VDATA/selftool.pid")" 2>/dev/null; then
        kill "$(cat "$VDATA/selftool.pid")" 2>/dev/null || true
        for _ in $(seq 1 30); do
            kill -0 "$(cat "$VDATA/selftool.pid")" 2>/dev/null || break
            sleep 1
        done
    fi
}
trap stop_service EXIT

mkdir -p "$OUT"
stop_service
log "启动单 worker 服务（--workers 1，与 docs/05 §6.3 一致）"
start_service
ok() { printf '  \033[1;32m✓\033[0m %s\n' "$*"; }
ok "服务就绪：$(curl -s "${BASE}/healthz")"

ADMIN_PW="Drill@M4passw0rd"
TOKEN="$(curl -s -X POST "${BASE}/api/v1/auth/login" -H 'Content-Type: application/json' \
    -d "{\"username\":\"admin\",\"password\":\"${ADMIN_PW}\"}" | jq -r .access_token)"
[ -n "$TOKEN" ] && [ "$TOKEN" != "null" ] || die "登录失败（先跑 m4-drill-maintenance.sh 播种）"
AUTH="Authorization: Bearer ${TOKEN}"

# ---------------------------------------------------------------------------
log "准备数据集（目标 ${TOOL_COUNT} 个已发布工具）"
# ---------------------------------------------------------------------------
CUR="$(sqlite3 "$VDATA/selftool.db" 'SELECT COUNT(*) FROM tools;')"
if [ "$CUR" -lt "$TOOL_COUNT" ]; then
    step "用 SQL 直接灌到 ${TOOL_COUNT} 个（避免走 200 次 HTTP 上传，只为压测造数据）"
    "$VPY" - "$VDATA/selftool.db" "$TOOL_COUNT" <<'PYEOF'
import sqlite3, sys
db, target = sys.argv[1], int(sys.argv[2])
con = sqlite3.connect(db)
owner = con.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()[0]
cat = con.execute("SELECT id FROM categories LIMIT 1").fetchone()
cat_id = cat[0] if cat else None
cur = con.execute("SELECT COUNT(*) FROM tools").fetchone()[0]
for i in range(cur, target):
    con.execute(
        "INSERT INTO tools (slug,name,summary,description_md,tool_type,visibility,status,"
        "owner_id,category_id,download_count,view_count,version_seq,published_at,"
        "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (f"perf-tool-{i:04d}", f"压测工具 {i}", "压测简介 " + "x" * 80, "# 正文\n" + "y" * 400,
         "file", "public", "approved", owner, cat_id,
         i % 500, i % 2000, 1, "2026-01-01 00:00:00",
         "2026-01-01 00:00:00", "2026-01-01 00:00:00"),
    )
con.commit()
print(f"  tools 行数 → {con.execute('SELECT COUNT(*) FROM tools').fetchone()[0]}")
PYEOF
    ok "数据集就绪"
else
    ok "已有 ${CUR} 个工具，跳过灌数据"
fi

TOTAL="$(sqlite3 "$VDATA/selftool.db" "SELECT COUNT(*) FROM tools WHERE status='approved';")"
SLUG="$(curl -s "${BASE}/api/v1/tools?page_size=1" -H "$AUTH" | jq -r '.items[0].slug')"
[ -n "$SLUG" ] && [ "$SLUG" != "null" ] || die "拿不到一个可用的 slug"
# 下载压测要挑**有版本文件**的工具（直连 SQL 灌的压测工具没有版本）
DL_SLUG="$(sqlite3 "$VDATA/selftool.db" "SELECT t.slug FROM tools t JOIN tool_versions v ON v.tool_id=t.id WHERE v.storage_path IS NOT NULL AND t.status='approved' LIMIT 1;")"
[ -n "$DL_SLUG" ] || die "找不到任何带版本文件的已发布工具（先跑 m4-drill-maintenance.sh 播种）"
ok "已发布工具 ${TOTAL} 个；详情压测样本 slug=${SLUG}"

# ---------------------------------------------------------------------------
log "验收 18a：门户列表 100 并发（目标 P95 < 300ms）"
# ---------------------------------------------------------------------------
LIST_URL="${BASE}/api/v1/tools?page_size=24"
"$AB" -q -k -c 100 -n 1000 -H "Authorization: Bearer ${TOKEN}" "$LIST_URL" > "$OUT/ab-list.txt" 2>&1
grep -E "Concurrency Level|Complete requests|Failed requests|Requests per second|Time per request|50%|95%|99%|100%" "$OUT/ab-list.txt" | sed 's/^/  /'
if grep -q "Connection refused" "$OUT/ab-list.txt"; then
    die "ab 中途连接被拒 —— 服务在压测中挂了。看 $SANDBOX/log/selftool-stdout.log"
fi
LIST_P95="$(awk '/^ *95%/ {print $2}' "$OUT/ab-list.txt")"
printf '\n  → 列表接口 P95 = %s ms\n' "$LIST_P95"

# ---------------------------------------------------------------------------
log "验收 18b：工具详情 100 并发（目标 P95 < 300ms）"
# ---------------------------------------------------------------------------
DETAIL_URL="${BASE}/api/v1/tools/${SLUG}"
"$AB" -q -k -c 100 -n 1000 -H "Authorization: Bearer ${TOKEN}" "$DETAIL_URL" > "$OUT/ab-detail.txt" 2>&1
grep -E "Concurrency Level|Complete requests|Failed requests|Requests per second|Time per request|50%|95%|99%|100%" "$OUT/ab-detail.txt" | sed 's/^/  /'
if grep -q "Connection refused" "$OUT/ab-detail.txt"; then
    die "ab 中途连接被拒 —— 服务在压测中挂了"
fi
DETAIL_P95="$(awk '/^ *95%/ {print $2}' "$OUT/ab-detail.txt")"
printf '\n  → 详情接口 P95 = %s ms\n' "$DETAIL_P95"

# ---------------------------------------------------------------------------
log "验收 19a：并发阶梯（找出 P95 越过 300ms 的拐点）"
# ---------------------------------------------------------------------------
# 单点压测只能回答「100 并发行不行」，阶梯能回答「到底能扛多少」——后者对容量规划更有用。
printf '  %4s %10s %10s %10s\n' "并发" "P50(ms)" "P95(ms)" "req/s"
for c in 1 5 10 20 50 100; do
    "$AB" -q -k -c "$c" -n $((c * 20)) -H "Authorization: Bearer ${TOKEN}" "$LIST_URL" \
        > "$OUT/ab-c${c}.txt" 2>&1
    p50="$(awk '/^ *50%/ {print $2}' "$OUT/ab-c${c}.txt")"
    p95="$(awk '/^ *95%/ {print $2}' "$OUT/ab-c${c}.txt")"
    rps="$(awk '/Requests per second/ {print $4}' "$OUT/ab-c${c}.txt")"
    printf '  %4s %10s %10s %10s\n' "$c" "${p50:-?}" "${p95:-?}" "${rps:-?}"
done

# ---------------------------------------------------------------------------
log "验收 18c：20 并发下载（确认无 database is locked）"
# ---------------------------------------------------------------------------
MARK="$(wc -l < "$SANDBOX/log/selftool-stdout.log")"
"$VPY" - "$BASE" "$TOKEN" "$DL_SLUG" <<'PYEOF' | sed 's/^/  /'
import asyncio, statistics, sys, time
import httpx

base, token, slug = sys.argv[1], sys.argv[2], sys.argv[3]
CONC, ROUNDS = 20, 3
headers = {"Authorization": f"Bearer {token}"}

async def one(client: httpx.AsyncClient) -> float:
    t0 = time.perf_counter()
    r = await client.post(f"{base}/api/v1/tools/{slug}/download-ticket", headers=headers)
    url = r.json()["url"]
    d = await client.get(f"{base}{url}")
    elapsed = (time.perf_counter() - t0) * 1000
    assert d.status_code == 200, (d.status_code, d.text[:120])
    return elapsed

async def main() -> None:
    async with httpx.AsyncClient(timeout=60) as client:
        await asyncio.gather(*[one(client) for _ in range(5)])   # 预热
        all_ms: list[float] = []
        for i in range(ROUNDS):
            ms = await asyncio.gather(*[one(client) for _ in range(CONC)])
            all_ms.extend(ms)
            print(f"  第 {i+1} 轮：{CONC} 并发，max={max(ms):.0f}ms mean={statistics.mean(ms):.0f}ms")
        all_ms.sort()
        p95 = all_ms[int(len(all_ms) * 0.95) - 1]
        print(f"  合计 {len(all_ms)} 次下载：P50={all_ms[len(all_ms)//2]:.0f}ms "
              f"P95={p95:.0f}ms max={all_ms[-1]:.0f}ms")

asyncio.run(main())
PYEOF
LOCKED="$(tail -n "+$((MARK + 1))" "$SANDBOX/log/selftool-stdout.log" | grep -ci 'database is locked' || true)"
printf '\n  下载期间日志中 "database is locked" 次数 = %s\n' "$LOCKED"

# ---------------------------------------------------------------------------
log "验收 19：SQLite 写锁瓶颈点（逐步加压测写入）"
# ---------------------------------------------------------------------------
"$VPY" - "$BASE" "$TOKEN" <<'PYEOF' | sed 's/^/  /'
import asyncio, statistics, sys, time
import httpx

base, token = sys.argv[1], sys.argv[2]
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

async def one(client: httpx.AsyncClient, i: int) -> tuple[int, float]:
    t0 = time.perf_counter()
    # 写请求：创建工具（真写库，最能体现 SQLite 单写者瓶颈）
    r = await client.post(f"{base}/api/v1/me/tools", headers=headers,
                          json={"name": f"写压测 {i}", "summary": "s", "description_md": "",
                                "tool_type": "file", "visibility": "public"})
    return r.status_code, (time.perf_counter() - t0) * 1000

async def main() -> None:
    async with httpx.AsyncClient(timeout=120) as client:
        # 预热（丢弃）：第一次请求要建连接、JIT 预热 SQLAlchemy 编译缓存，
        # 把它算进来会让并发=1 的 P95 虚高到 2s+
        await asyncio.gather(*[one(client, 900000 + i) for i in range(5)])
        for conc in (1, 5, 10, 20, 40):
            ms: list[float] = []
            codes: list[int] = []
            for batch in range(3):
                res = await asyncio.gather(*[one(client, batch * 1000 + i) for i in range(conc)])
                for code, m in res:
                    codes.append(code)
                    ms.append(m)
            ms.sort()
            p95 = ms[int(len(ms) * 0.95) - 1]
            bad = sum(1 for c in codes if c >= 500)
            print(f"  写并发={conc:3d}  请求={len(ms):3d}  P50={ms[len(ms)//2]:6.0f}ms "
                  f"P95={p95:7.0f}ms  max={ms[-1]:7.0f}ms  5xx={bad}")

asyncio.run(main())
PYEOF
W_LOCKED="$(tail -n "+$((MARK + 1))" "$SANDBOX/log/selftool-stdout.log" | grep -ci 'database is locked' || true)"
printf '\n  写入加压期间 "database is locked" 次数 = %s\n' "$W_LOCKED"

log "结论"
cat <<EOF
  列表 P95 = ${LIST_P95} ms     详情 P95 = ${DETAIL_P95} ms     目标 300 ms
  下载 20 并发：见上（database is locked 次数 ${LOCKED}）
  写入加压：见上（database is locked 次数 ${W_LOCKED}）
  原始输出保留在 $OUT/ab-list.txt 与 $OUT/ab-detail.txt
  环境：单 worker（--workers 1）、SQLite WAL、${TOTAL} 个已发布工具、macOS 本机
EOF
