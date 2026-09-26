#!/usr/bin/env bash
# ============================================================
# scripts/verify-backup.sh — 备份可用性验证（**不触碰生产数据**）
#
# 它回答的是一个很具体的问题：**最近那份备份，现在真的能恢复吗？**
# 「备份文件存在」不等于「备份能用」—— 本脚本做的是后者。
#
# 用法:
#   scripts/verify-backup.sh [--dir <备份目录>] [--expect-tools N] [--expect-files N]
#
# 建议每周执行一次（可挂 timer），并在每次恢复演练前执行。
# 依据: docs/05《部署与运维方案》§8.6
#
# 退出码: 0 = 全部通过；1 = 发现问题（任何一个 FAIL 都返回非零，供监控捕获）
# ============================================================
set -uo pipefail

# 配置解析：**应用读的变量名优先**，历史 LOCALCRAFT_ 前缀名作兼容别名，最后才是默认值。
# 为什么必须这样：localcraft.env 里写的是应用读的名字，而 systemd 单元用
# EnvironmentFile 把它们注入脚本环境。脚本若只认 LOCALCRAFT_DATA_DIR，
# 运维改了 DATA_DIR 后脚本会静默回落到 /var/lib/localcraft ——
# 备份跑成功、退出码 0、日志正常，备的却是错的目录。
DATA_DIR="${DATA_DIR:-${LOCALCRAFT_DATA_DIR:-/var/lib/localcraft}}"
BACKUP_ROOT="${LOCALCRAFT_BACKUP_DIR:-$DATA_DIR/backups}"
DB_DIR="$BACKUP_ROOT/db"
FILES_BACKUP_DIR="$BACKUP_ROOT/files"

EXPECT_TOOLS=""
EXPECT_FILES=""
SAMPLE_LIMIT="${LOCALCRAFT_VERIFY_SAMPLE:-5}"

while [ $# -gt 0 ]; do
    case "$1" in
        --dir)           BACKUP_ROOT="$2"; DB_DIR="$BACKUP_ROOT/db"; FILES_BACKUP_DIR="$BACKUP_ROOT/files"; shift 2 ;;
        --expect-tools)  EXPECT_TOOLS="$2"; shift 2 ;;
        --expect-files)  EXPECT_FILES="$2"; shift 2 ;;   # 预留：按期望文件数校验
        -h|--help)       sed -n '2,16p' "$0"; exit 0 ;;
        *) echo "未知参数: $1" >&2; exit 2 ;;
    esac
done

FAIL=0
ok()   { printf '  [ OK ]   %s\n' "$*"; }
warn() { printf '  [ WARN ] %s\n' "$*"; }
bad()  { printf '  [ FAIL ] %s\n' "$*"; FAIL=1; }

echo "=== 备份可用性验证 ==="
echo "时间: $(date -Is)"
echo "备份根目录: $BACKUP_ROOT"
echo

command -v sqlite3 >/dev/null 2>&1 || { echo "  [ FAIL ] 缺少 sqlite3"; exit 1; }

# ------------------------------------------------------------
# 1) 备份文件存在性
# ------------------------------------------------------------
echo "-- 1) 备份文件存在性"
LATEST_DB="$(find "$DB_DIR" -maxdepth 1 \( -name 'localcraft-*.db' -o -name 'localcraft-*.db.gz' \) -print 2>/dev/null | sort | tail -1)"
if [ -z "$LATEST_DB" ]; then
    bad "找不到任何数据库备份（${DB_DIR}）"
else
    ok "最近数据库备份: $(basename "$LATEST_DB")  ($(du -h "$LATEST_DB" | cut -f1))"
fi
LATEST_FILES="$(find "$FILES_BACKUP_DIR" -maxdepth 1 -type d -name 'files-*' -print 2>/dev/null | sort | tail -1)"
[ -n "$LATEST_FILES" ] && ok "最近文件备份: $(basename "$LATEST_FILES")" \
                       || warn "没有文件快照（只在从未跑过 files 备份时属正常）"

[ -n "$LATEST_DB" ] || { echo; echo "=== 验证失败 ==="; exit 1; }

# ------------------------------------------------------------
# 2) 元信息文件
# ------------------------------------------------------------
echo
echo "-- 2) 元信息（.meta）"
META="${LATEST_DB}.meta"
if [ -f "$META" ]; then
    ok "存在 .meta"
    # shellcheck disable=SC1090
    grep -E '^(timestamp|mode|integrity_check|users_rows|tools_rows|alembic_revision)=' "$META" | sed 's/^/           /'
    if grep -q '^integrity_check=ok$' "$META"; then
        ok "备份时 integrity_check 记录为 ok"
    else
        bad "备份时 integrity_check 记录不是 ok"
    fi
    # 新鲜度：超过 48 小时要提醒
    TS_STR="$(grep '^timestamp=' "$META" | cut -d= -f2)"
    if [ -n "$TS_STR" ]; then
        AGE_H=$(( ( $(date +%s) - $(date -j -f "%Y%m%d-%H%M%S" "$TS_STR" +%s 2>/dev/null \
                     || date -d "${TS_STR:0:8} ${TS_STR:9:2}:${TS_STR:11:2}:${TS_STR:13:2}" +%s 2>/dev/null \
                     || echo 0) ) / 3600 ))
        if [ "$AGE_H" -gt 48 ] 2>/dev/null && [ "$AGE_H" -ne 0 ]; then
            warn "最近备份已 ${AGE_H} 小时未更新（> 48h）"
        elif [ "$AGE_H" -ne 0 ]; then
            ok "最近备份 ${AGE_H} 小时前生成"
        fi
    fi
else
    warn "缺少 .meta（老版本备份或手工放进去的文件）"
fi

# ------------------------------------------------------------
# 3) 真解压 + integrity_check（关键：证明备份不是坏文件）
# ------------------------------------------------------------
echo
echo "-- 3) 真实解压 + PRAGMA integrity_check"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/localcraft-verify-XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

CAND="$WORK/candidate.db"
if ! { case "$LATEST_DB" in
        *.gz) gzip -dc "$LATEST_DB" > "$CAND" ;;
        *)    cp "$LATEST_DB" "$CAND" ;;
       esac }; then
    bad "解压/复制失败 —— 备份文件已损坏"
else
    SIZE="$(wc -c < "$CAND" | tr -d ' ')"
    [ "$SIZE" -gt 0 ] && ok "解压得到 ${SIZE} 字节" || bad "解压结果为空"

    IC="$(sqlite3 "$CAND" 'PRAGMA integrity_check;' 2>&1 || true)"
    if [ "$IC" = "ok" ]; then
        ok "integrity_check = ok"
    else
        bad "integrity_check = $IC"
    fi

    # 关键表可读且行数对得上
    for tbl in users tools tool_versions; do
        N="$(sqlite3 "$CAND" "SELECT COUNT(*) FROM $tbl;" 2>/dev/null || echo '?')"
        if [ "$N" = "?" ]; then bad "表 $tbl 读不出来"; else ok "表 $tbl 可读，行数 $N"; fi
    done

    if [ -n "$EXPECT_TOOLS" ]; then
        GOT="$(sqlite3 "$CAND" 'SELECT COUNT(*) FROM tools;' 2>/dev/null || echo '?')"
        [ "$GOT" = "$EXPECT_TOOLS" ] && ok "tools 行数与期望一致（${GOT}）" \
                                     || bad "tools 行数不符：期望 ${EXPECT_TOOLS}，实际 $GOT"
    fi

    # 引用完整性（外键在快照里也必须成立）
    FK="$(sqlite3 "$CAND" 'PRAGMA foreign_key_check;' 2>&1 || true)"
    if [ -z "$FK" ]; then ok "PRAGMA foreign_key_check 无违规"; else bad "外键违规：$FK"; fi

    # 抽查：随机几个工具的版本文件是否在 files 快照里存在
    if [ -n "$LATEST_FILES" ]; then
        MISSING=0; CHECKED=0
        # tool_versions.storage_path 是相对 **DATA_DIR** 的路径，形如
        #   files/tools/1/1/t1.zip
        # 而文件快照里 rsync 的是 "$DATA_DIR/files/" 的**内容**，因此少一层 files/ 前缀：
        #   <快照>/tools/1/1/t1.zip
        # 这里必须把前缀剥掉再查，否则会对每一份健康备份都误报「文件缺失」。
        while IFS= read -r rel; do
            [ -n "$rel" ] || continue
            CHECKED=$((CHECKED + 1))
            rel_in_snap="${rel#files/}"
            if [ ! -e "$LATEST_FILES/$rel_in_snap" ]; then
                MISSING=$((MISSING + 1)); warn "文件快照里缺少: ${rel}（查的是 ${rel_in_snap}）"
            fi
        done < <(sqlite3 "$CAND" "SELECT storage_path FROM tool_versions WHERE storage_path IS NOT NULL ORDER BY RANDOM() LIMIT ${SAMPLE_LIMIT};" 2>/dev/null)
        if [ "$CHECKED" -eq 0 ]; then
            warn "快照里没有带 storage_path 的版本，跳过文件抽检"
        elif [ "$MISSING" -eq 0 ]; then
            ok "文件抽检通过（${CHECKED} 个样本都能在文件快照里找到）"
        else
            bad "文件抽检 ${MISSING}/${CHECKED} 缺失 —— 备份不完整"
        fi
    fi
fi

# ------------------------------------------------------------
# 4) 保留份数与磁盘
# ------------------------------------------------------------
echo
echo "-- 4) 保留份数与磁盘"
N_DB="$(find "$DB_DIR" -maxdepth 1 \( -name 'localcraft-*.db' -o -name 'localcraft-*.db.gz' \) | wc -l | tr -d ' ')"
N_FILES="$(find "$FILES_BACKUP_DIR" -maxdepth 1 -type d -name 'files-*' 2>/dev/null | wc -l | tr -d ' ')"
ok "数据库快照 ${N_DB} 份 / 文件快照 ${N_FILES} 份"
# --expect-files：把"文件快照里到底有多少个文件"与期望值对账。
# 数量对不上通常意味着某次 rsync 中断或源目录被清理过，属于"备份存在但内容不完整"。
case "$EXPECT_FILES" in
    '') : ;;
    *[!0-9]*) warn "忽略非数字的 --expect-files 取值: ${EXPECT_FILES}" ;;
    *)
        if [ -n "$LATEST_FILES" ]; then
            GOT_FILES="$(find "$LATEST_FILES" -type f 2>/dev/null | wc -l | tr -d ' ')"
            if [ "$GOT_FILES" -eq "$EXPECT_FILES" ]; then
                ok "文件快照文件数符合期望：${GOT_FILES}"
            else
                bad "文件快照文件数不符：期望 ${EXPECT_FILES}，实际 ${GOT_FILES}"
            fi
        else
            warn "没有文件快照，跳过 --expect-files 对账"
        fi
        ;;
esac
FREE_MB="$(df -Pk "$BACKUP_ROOT" | awk 'NR==2 {printf "%d", $4/1024}')"
if [ "$FREE_MB" -lt 1024 ]; then bad "备份分区剩余仅 ${FREE_MB} MB"; else ok "备份分区剩余 ${FREE_MB} MB"; fi

echo
if [ "$FAIL" -eq 0 ]; then
    echo "=== 验证通过：最近一份备份可恢复 ==="
    exit 0
else
    echo "=== 验证失败：最近一份备份**不可**直接用于恢复 ==="
    exit 1
fi
