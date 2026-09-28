#!/bin/bash
# 复盘看板打分三连 —— ⛔ 串行铁律（2026-09-23 事故固化）：
#   build_sector_full → build_zt_quality → build_auction_baseline
#   并行 = 方向景气分归零、全池质量分被压低约 6 分（dir_score 覆盖率自检应 ≈100%）
set -euo pipefail

: "${KANBAN_ROOT:=/srv/kanban}"
: "${SHORT_TERM_DATA_DIR:=/srv/data/shortterm}"
: "${DETAILS_DATA_DIR:=/srv/data/details}"
export KANBAN_ROOT SHORT_TERM_DATA_DIR DETAILS_DATA_DIR

D="${1:?用法: run_scoring.sh YYYYMMDD}"
DS="${D:0:4}-${D:4:2}-${D:6:2}"          # build_* 用 YYYY-MM-DD
PY="${PYTHON:-python3}"

cd "$KANBAN_ROOT"
echo "=== 打分链路 $D（严格串行）==="

echo "--- [1/3] build_sector_full（板块全量）"
"$PY" scripts/build_sector_full.py --dates "$DS"

echo "--- [2/3] build_zt_quality（涨停质量五维）"
"$PY" scripts/build_zt_quality.py --dates "$DS"

echo "--- [3/3] build_auction_baseline（竞价预期基准）"
"$PY" scripts/build_auction_baseline.py --date "$DS"

echo "=== 自检：dir_score 覆盖率应 ≈100%（见上方脚本输出）==="
echo "=== 打分链路完成：$KANBAN_ROOT/daily/$D/{板块全量,涨停质量,竞价预期基准}.json ==="
