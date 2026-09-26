#!/usr/bin/env bash
# 一键端到端演示：自动启动模拟监管接口，演示 超时重试 / 500 / 业务驳回 / 异常列表 / 报告持久化
set -euo pipefail
cd "$(dirname "$0")/.."

DB="data/demo.db"
PORT=9100
rm -f "$DB" "$DB"-*
export INSPECTION_DB="$DB"
export REGULATOR_URL="http://127.0.0.1:${PORT}/v1/inspection/batch"
export REGULATOR_TIMEOUT=2 REGULATOR_MAX_RETRY=2 REGULATOR_RETRY_BACKOFF=0.1
export BATCH_SIZE=5 MOCK_DELAY=4
export MOCK_TIMEOUT_PLATES="京A88888" MOCK_ERROR_PLATES="沪B99999" MOCK_REJECT_PLATES="粤B22222"

echo ">>> 启动模拟监管接口 :${PORT}"
python3 -m tools.mock_regulator --port "$PORT" >/tmp/mock_demo.log 2>&1 &
MOCK_PID=$!
trap 'kill $MOCK_PID 2>/dev/null || true' EXIT
sleep 1.2

echo ">>> 1/5 导入样例"; python3 run.py import sample_data/vehicles.csv
echo ">>> 2/5 分批校验（不合格记录进入异常列表）"; python3 run.py validate
echo ">>> 3/5 提交监管接口（超时/500 自动重试，业务驳回入异常）"; python3 run.py process

echo ">>> 4/5 监管恢复，重试超时批次"
kill $MOCK_PID 2>/dev/null || true; sleep 0.5
MOCK_TIMEOUT_PLATES="__NONE__" MOCK_ERROR_PLATES="__NONE__" MOCK_REJECT_PLATES="粤B22222" \
  python3 -m tools.mock_regulator --port "$PORT" >>/tmp/mock_demo.log 2>&1 &
MOCK_PID=$!
sleep 1.2
python3 run.py retry

echo ">>> 5/5 生成并持久化汇总报告"; python3 run.py report
echo ">>> 异常列表"; python3 run.py exceptions
echo ">>> 演示数据库: $DB"
