#!/usr/bin/env bash
# ============================================================
# RAG4C 桥服务启动脚本（Linux/macOS）
#
# 用法：
#   ./scripts/start_server.sh              默认单 worker（推荐）
#   RAG4C_WORKERS=2 ./scripts/start_server.sh   多 worker 扩容
#
# 【为什么默认 1 worker】
#   metrics / 查询 TTL 缓存 / 健康探测缓存 / 并发信号量均为进程内状态。
#   多 worker 时：指标分裂、缓存不共享、总并发=workers×QUERY_MAX_CONCURRENT。
#   单机控制台用 1 worker；workers>=2 仅在横向扩容且接受指标分裂时用。
#
# 虚拟环境：优先 .workbuddy 管理的 rag4c venv（pymilvus 3.x）
# ============================================================
set -euo pipefail

RAG4C_WORKERS="${RAG4C_WORKERS:-1}"
HOST="${RAG4C_HOST:-127.0.0.1}"
PORT="${RAG4C_PORT:-8000}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="C:/Users/饶策/.workbuddy/binaries/python/envs/rag4c/Scripts/python.exe"
[ -x "$PY" ] || PY="python"

echo "[RAG4C] host=$HOST port=$PORT workers=$RAG4C_WORKERS  python=$PY"
exec "$PY" -m uvicorn server.app:app --host "$HOST" --port "$PORT" --workers "$RAG4C_WORKERS"
