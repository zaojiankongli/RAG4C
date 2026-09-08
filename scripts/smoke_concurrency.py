"""Concurrency regressions for query, SSE, cache, and ingest capacity."""
from __future__ import annotations

import asyncio
import importlib
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _smoke_redis import isolate_redis_keyspace  # noqa: E402

# 必须在 import server.app 之前：配置是 lru_cache 单例，第一次 get_settings()
# 之后再改环境变量就不生效了，缓存会照着生产前缀往共用实例里写。
isolate_redis_keyspace("concurrency")

bridge = importlib.import_module("server.app")
documents = importlib.import_module("server.documents")

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}: {detail}")


def request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/query/stream",
            "headers": [],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("test", 1234),
            "scheme": "http",
        }
    )


async def test_timeout_holds_slot() -> None:
    original_run = bridge._run_query
    original_slots = bridge._query_slots
    original_timeout = bridge.QUERY_TIMEOUT_S
    calls = 0
    lock = threading.Lock()

    def slow(_: object) -> dict:
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.18)
        return {"ok": True}

    bridge._run_query = slow
    bridge._query_slots = asyncio.Semaphore(1)
    bridge.QUERY_TIMEOUT_S = 0.05
    with bridge._pending_lock:
        bridge._pending = 0
    try:
        first_status = 0
        second_status = 0
        try:
            await bridge.query(bridge.QueryRequest(query="timeout-owner"))
        except HTTPException as exc:
            first_status = exc.status_code
        pending_after_timeout = bridge._queue_stats()["pending"]
        try:
            await bridge.query(bridge.QueryRequest(query="timeout-waiter"))
        except HTTPException as exc:
            second_status = exc.status_code
        calls_while_owner_running = calls
        await asyncio.sleep(0.2)
        check("query timeout returns 503", first_status == 503, str(first_status))
        check(
            "timed-out worker still owns slot",
            pending_after_timeout == 1 and calls_while_owner_running == 1,
            f"pending={pending_after_timeout} calls={calls_while_owner_running}",
        )
        check("queued request times out without executing", second_status == 503 and calls == 1)
        check("slot released after real worker completes", bridge._queue_stats()["pending"] == 0)
    finally:
        bridge._run_query = original_run
        bridge._query_slots = original_slots
        bridge.QUERY_TIMEOUT_S = original_timeout


async def test_stream_shares_slot() -> None:
    original_stream = bridge.answer_query_stream
    original_run = bridge._run_query
    original_slots = bridge._query_slots
    original_timeout = bridge.QUERY_TIMEOUT_S
    release = threading.Event()
    normal_calls = 0

    def stream_stub(*args, **kwargs):
        yield {"type": "phase", "name": "started"}
        release.wait(timeout=1.0)
        yield {"type": "done", "result": {"query": args[0], "citations": []}}

    def normal_stub(_: object) -> dict:
        nonlocal normal_calls
        normal_calls += 1
        return {"ok": True}

    bridge.answer_query_stream = stream_stub
    bridge._run_query = normal_stub
    bridge._query_slots = asyncio.Semaphore(1)
    bridge.QUERY_TIMEOUT_S = 0.06
    with bridge._pending_lock:
        bridge._pending = 0
    try:
        await bridge.query_stream(
            bridge.QueryRequest(query="stream-owner"), request()
        )
        status = 0
        try:
            await bridge.query(bridge.QueryRequest(query="normal-waiter"))
        except HTTPException as exc:
            status = exc.status_code
        check(
            "SSE and normal query share capacity",
            status == 503 and normal_calls == 0,
            f"status={status} calls={normal_calls}",
        )
        release.set()
        await asyncio.sleep(0.1)
        check("SSE releases slot when producer ends", bridge._queue_stats()["pending"] == 0)
        # 上面那次 query_stream 拿到响应就扔了，从没迭代过 body——这正是"客户端
        # 在首字节之前断开"的形态。此时 event_source 的 finally 永远不会跑，
        # 在途登记若不由 worker 的完成回调兜底，就会留下一个**永不 resolve** 的
        # future：之后每一个问同一句的人都在合并分支上白等一个 QUERY_TIMEOUT_S
        # 再吃 503，进程不重启就好不了。这条断言看着那个兜底。
        # 断言写成"没有任何未 resolve 的在途登记"而不是去比对具体的键：缓存键
        # 里拌了语料代次戳，它会在后台按 TTL 刷新，拿键去比会在刷新恰好落在
        # 这中间时静默变成一条永远为真的断言。未 resolve 的 future 才是本体。
        orphans = [k for k, f in bridge._aio_flights.items() if not f.done()]
        check(
            "SSE never-consumed body leaves no orphan inflight",
            not orphans,
            f"orphans={orphans}",
        )
    finally:
        release.set()
        bridge.answer_query_stream = original_stream
        bridge._run_query = original_run
        bridge._query_slots = original_slots
        bridge.QUERY_TIMEOUT_S = original_timeout


async def test_stream_backpressure() -> None:
    original_stream = bridge.answer_query_stream
    original_slots = bridge._query_slots
    original_timeout = bridge.QUERY_TIMEOUT_S
    event_count = 600

    def stream_stub(*args, **kwargs):
        for index in range(event_count):
            yield {"type": "token", "text": str(index)}
        yield {
            "type": "done",
            "result": {"query": args[0], "citations": [], "traces": []},
        }

    bridge.answer_query_stream = stream_stub
    bridge._query_slots = asyncio.Semaphore(1)
    bridge.QUERY_TIMEOUT_S = 3.0
    with bridge._pending_lock:
        bridge._pending = 0
    try:
        response = await bridge.query_stream(
            bridge.QueryRequest(query="backpressure"), request()
        )
        chunks = [chunk async for chunk in response.body_iterator]
        check(
            "SSE backpressure preserves every event",
            len(chunks) == event_count + 1,
            f"events={len(chunks)}",
        )
        deadline = time.monotonic() + 1.0
        while bridge._queue_stats()["pending"] and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        check("SSE terminator completes producer", bridge._queue_stats()["pending"] == 0)
    finally:
        bridge.answer_query_stream = original_stream
        bridge._query_slots = original_slots
        bridge.QUERY_TIMEOUT_S = original_timeout


async def test_repeated_lifespan() -> None:
    states: list[bool] = []
    for _ in range(2):
        async with bridge.lifespan(bridge.app):
            states.append(
                bridge._query_executor is not None
                and documents._ingest_executor is not None
                and not bridge._metrics_persist_stop.is_set()
            )
        states.append(
            bridge._query_executor is None
            and documents._ingest_executor is None
            and bridge._metrics_persist_stop.is_set()
        )
    check("app lifespan can start and stop twice", all(states), str(states))


def test_singleflight() -> None:
    original_answer = bridge.answer_query
    original_serialize = bridge._serialize
    calls = 0
    lock = threading.Lock()

    def answer_stub(*args, **kwargs):
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.1)
        return SimpleNamespace(
            query=args[0], route="hybrid", abstained=False,
            citations=[], traces=[],
        )

    bridge.answer_query = answer_stub
    bridge._serialize = lambda result, duration: {
        "result": {"query": result.query},
        "using_mock": False,
        "duration_ms": duration,
    }
    # 缓存搬进了 core.query_cache（L1+L2），这里不再直接摸 app 的内部字典。
    # 只清 L1：L2 在共用的 Redis 实例上，测试无权也不该去扫别人的键空间。
    bridge.get_query_cache().clear_l1()
    # 问句带上唯一后缀：清 L1 清不掉 L2，而 L2 可能残留上一次运行写进去的
    # 同名答案（本机开了 Redis 时就会），那样 calls==1 会因为"根本没算"而
    # 蒙混过关——断言看着是绿的，测的东西却已经不是 single-flight 了。
    unique_q = f"singleflight-unique-{uuid.uuid4().hex[:12]}"
    req = bridge.QueryRequest(query=unique_q, retry=False, tenant_id="t1")
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(bridge._run_query, [req, req]))
        check("concurrent cache miss executes once", calls == 1, f"calls={calls}")
        check("singleflight followers receive result", len(results) == 2)
        key_retry = bridge._cache_key(req.query, req.acl, True, req.tenant_id)
        key_tenant = bridge._cache_key(req.query, req.acl, False, "t2")
        key_base = bridge._cache_key(req.query, req.acl, False, req.tenant_id)
        check("cache key isolates retry and tenant", len({key_base, key_retry, key_tenant}) == 3)
    finally:
        bridge.answer_query = original_answer
        bridge._serialize = original_serialize


def test_ingest_capacity() -> None:
    release = threading.Event()
    active = 0
    peak = 0
    lock = threading.Lock()

    def job() -> None:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        release.wait(timeout=2.0)
        with lock:
            active -= 1

    ids = [f"capacity-{index}-{time.time_ns()}" for index in range(documents.INGEST_QUEUE_MAX)]
    rejected = 0
    try:
        for doc_id in ids:
            documents._submit_job(doc_id, job)
        try:
            documents._submit_job(f"overflow-{time.time_ns()}", job)
        except HTTPException as exc:
            rejected = exc.status_code
        check("ingest queue rejects overflow", rejected == 429, str(rejected))
        time.sleep(0.05)
        check(
            "ingest workers obey concurrency limit",
            peak <= documents.INGEST_MAX_CONCURRENT,
            f"peak={peak}",
        )
    finally:
        release.set()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            with documents._jobs_lock:
                if all(not documents._jobs.get(doc_id, {}).get("running") for doc_id in ids):
                    break
            time.sleep(0.02)


def test_executor_lifecycle() -> None:
    completed = threading.Event()
    documents.shutdown_ingest_executor()
    documents.start_ingest_executor()
    doc_id = f"restart-{time.time_ns()}"
    documents._submit_job(doc_id, completed.set)
    check("ingest executor restarts after shutdown", completed.wait(timeout=2.0))
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        with documents._jobs_lock:
            if not documents._jobs.get(doc_id, {}).get("running"):
                break
        time.sleep(0.01)
    documents.shutdown_ingest_executor()

    bridge._shutdown_query_executor()
    first = bridge._start_query_executor()
    check("query executor restarts after shutdown", first.submit(lambda: 42).result() == 42)
    bridge._shutdown_query_executor()


async def main() -> int:
    print("== Query capacity and timeout ==")
    await test_timeout_holds_slot()
    print("== Shared SSE capacity and backpressure ==")
    await test_stream_shares_slot()
    await test_stream_backpressure()
    print("== Cache singleflight ==")
    await asyncio.to_thread(test_singleflight)
    print("== Ingest capacity ==")
    await asyncio.to_thread(test_ingest_capacity)
    print("== Executor lifecycle ==")
    await asyncio.to_thread(test_executor_lifecycle)
    await test_repeated_lifespan()
    print(f"summary: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
