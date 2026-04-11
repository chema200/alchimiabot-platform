#!/bin/bash
# Start real data capture — runs as background service
# Captures: raw trades/book, feature snapshots, to Parquet + PostgreSQL

cd ~/agentbot-platform
source .venv/bin/activate

# Ensure DB is migrated
alembic upgrade head

echo "Starting data capture (live mode)..."
nohup python main.py --mode live > logs/capture.out 2>&1 &
echo "PID: $!"
echo $! > logs/capture.pid
echo "Logs: logs/capture.out"
