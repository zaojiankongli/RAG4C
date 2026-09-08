@echo off
REM ============================================================
REM RAG4C 桥服务启动脚本（Windows）
REM
REM 用法：
REM   start_server.bat            默认单 worker（推荐，见下）
REM   set RAG4C_WORKERS=2 ^&^& start_server.bat    多 worker 扩容
REM
REM 【为什么默认 1 worker】
REM   本服务的 metrics / 查询 TTL 缓存 / 健康探测缓存 / 并发信号量
REM   都是【进程内】状态。多 worker 时：
REM   - 每个 worker 独立统计（Prometheus 指标分裂，需外部聚合）
REM   - 查询缓存不共享（命中靠负载均衡，仍正确但效率降低）
REM   - 总并发 = workers x QUERY_MAX_CONCURRENT（Semaphore 各自独立）
REM   单机控制台场景 1 worker 是正确的；workers>=2 仅在
REM   追求横向吞吐且接受指标分裂时使用。
REM
REM 虚拟环境：优先使用 .workbuddy 管理的 rag4c venv（pymilvus 3.x）
REM ============================================================

setlocal

set RAG4C_WORKERS=%RAG4C_WORKERS%
if "%RAG4C_WORKERS%"=="" set RAG4C_WORKERS=1
if "%RAG4C_HOST%"=="" set RAG4C_HOST=127.0.0.1
set PORT=8000

REM 项目根（脚本位于 scripts/ 下）
set "ROOT=%~dp0.."
pushd "%ROOT%"

set "PY=C:\Users\饶策\.workbuddy\binaries\python\envs\rag4c\Scripts\python.exe"
if not exist "%PY%" set PY=python

echo [RAG4C] 启动桥服务  host=%RAG4C_HOST% port=%PORT% workers=%RAG4C_WORKERS%
echo [RAG4C] python: %PY%
"%PY%" -m uvicorn server.app:app --host %RAG4C_HOST% --port %PORT% --workers %RAG4C_WORKERS%

popd
endlocal
