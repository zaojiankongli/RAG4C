"""Redis Streams 跨进程准入队列冒烟测试。

运行：python scripts/smoke_queue_stream.py

覆盖：
1. 降级路径（无 Redis 也必跑）：backend=memory / url 不可达 -> 未激活、
   submit 返回 inactive、stats 形状与 _queue_stats() 兼容、全程无异常。
2. 真实 Redis（连不上则自动跳过后续断言，CI 无 Redis 也不会红）：
   入队 -> 消费 -> 结果回传 闭环；深度上报；饱和拒绝；worker 异常回传；
   **XAUTOCLAIM 捡回崩溃 worker 的任务**（读了不 ACK，模拟进程被 kill，
   另一个 consumer 过 min-idle 后接管）；Worker 后台线程闭环。

安全约束：目标 Redis 与其它应用共用同一个 db（实测 dbsize=19）。
本脚本用独占前缀 ``rag4c-test-queue-<pid>:``，只删自己造的键，
**绝不** FLUSHDB；退出前校验 dbsize 未变。
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

# ruff: noqa: E402

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# 必须在 import config.settings 之前落定：get_settings() 带 lru_cache，
# 且 .env 以 override=False 加载，先设的环境变量优先。
_PREFIX = f"rag4c-test-queue-{os.getpid()}-{uuid.uuid4().hex[:6]}:"
_LIVE_URL = os.environ.get("RAG4C_TEST_REDIS_URL", "redis://127.0.0.1:6379/0")

os.environ["RAG4C_REDIS_KEY_PREFIX"] = _PREFIX
os.environ["RAG4C_REDIS_QUEUE_BACKEND"] = "memory"
os.environ["RAG4C_REDIS_URL"] = ""
os.environ["RAG4C_REDIS_SOCKET_TIMEOUT_S"] = "1.0"
os.environ["RAG4C_REDIS_SOCKET_CONNECT_TIMEOUT_S"] = "1.0"
os.environ["RAG4C_REDIS_STREAM_MAXLEN"] = "1000"

from config.settings import get_settings
from core import queue_stream as q
from core import redis_client

passed = 0
failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} {detail}")


def reconfigure(**kwargs: str) -> None:
    """改环境变量并让配置 / 连接单例重新生效。"""
    for name, value in kwargs.items():
        os.environ[name] = value
    get_settings.cache_clear()
    redis_client.reset_client()
    q.reset()


# ===========================================================================
# 1. 降级路径（无 Redis 也必须通过）
# ===========================================================================
print("== 1. 降级：backend=memory ==")
check("未配置 url 时 is_active()=False", q.is_active() is False)
adm = q.submit({"query": "降级"}, timeout_s=1.0)
check("submit 返回 inactive", adm.state == "inactive", adm.detail)
check("inactive 应触发调用方回退", adm.should_fallback is True)
check("inactive 不是成功", adm.ok is False)

st = q.stats()
check(
    "stats() 形状兼容 _queue_stats()",
    {"pending", "max_concurrent", "queue_max"} <= set(st),
    str(sorted(st)),
)
check("未激活时 pending=0", st["pending"] == 0 and st["active"] is False, str(st))
check("未激活时 depth() 为 None", q.depth() is None)
check("未激活时 ensure_group() 为 False", q.ensure_group() is False)
check("未激活时 consume_once() 为 0", q.consume_once(lambda p: p) == 0)
check("未激活时 fetch()/reclaim() 返回空", q.fetch("c") == [] and q.reclaim("c") == [])

print("== 1b. 降级：backend=redis 但地址不可达 ==")
# 127.0.0.1:1 上没有监听者，连接会被立刻拒绝（比不可路由地址更快、更确定）
reconfigure(RAG4C_REDIS_QUEUE_BACKEND="redis", RAG4C_REDIS_URL="redis://127.0.0.1:1/0")
t0 = time.monotonic()
check("连不上时 is_active()=False", q.is_active() is False)
adm = q.submit({"query": "不可达"}, timeout_s=1.0)
check("连不上时 submit 返回 inactive", adm.state == "inactive", adm.detail)
st = q.stats()
check("连不上时 stats.active=False", st["active"] is False and st["backend"] == "redis", str(st))
check("降级判定必须快（不阻塞请求）", time.monotonic() - t0 < 10.0, f"{time.monotonic() - t0:.1f}s")

# ===========================================================================
# 2. 真实 Redis
# ===========================================================================
print("== 2. 连接真实 Redis ==")
reconfigure(RAG4C_REDIS_QUEUE_BACKEND="redis", RAG4C_REDIS_URL=_LIVE_URL)
live = q.is_active()
if not live:
    print(f"  [SKIP] {_LIVE_URL} 不可用，跳过在线断言（降级路径已验证）")
    print()
    print(f"结果：{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)

check("is_active()=True", live)
check("流键名带测试前缀", q.stream_key().startswith(_PREFIX), q.stream_key())
_dbsize_before = redis_client.call("dbsize")

try:
    q.destroy_stream()  # 清掉上一次异常退出可能残留的同名流
    check("ensure_group() 创建流与组", q.ensure_group() is True)
    check("ensure_group() 幂等（BUSYGROUP 视为成功）", q.ensure_group(force=True) is True)

    # -------------------------------------------------------------------
    print("== 3. 入队 -> 消费 -> 结果回传 闭环 ==")
    seen: list[dict] = []

    def handler(payload: dict) -> dict:
        seen.append(payload)
        return {"answer": f"回答:{payload.get('query')}", "n": payload.get("n", 0) + 1}

    # 生产者在本线程同步等，所以消费者必须在别的线程：先起 Worker 再 submit。
    worker = q.Worker(handler, consumer="smoke-w1", block_ms=200, idle_sleep_s=0.05).start()
    adm = q.submit({"query": "你好", "n": 1}, timeout_s=15.0)
    check("submit 拿到结果", adm.state == "done", f"{adm.state}/{adm.detail}")
    check(
        "结果内容正确（中文往返无损）",
        isinstance(adm.result, dict) and adm.result.get("answer") == "回答:你好",
        str(adm.result),
    )
    check("worker 收到的载荷完整", seen and seen[0].get("n") == 1, str(seen))
    # 不断言 waited_s > 0：Windows 上 time.monotonic() 走 GetTickCount64，
    # 分辨率 15.6ms，本地 Redis 往返可能整个落在一个 tick 里而得到 0.0。
    check(
        "等待耗时被记录且有界",
        0.0 <= adm.waited_s <= 15.0,
        repr(adm.waited_s),
    )

    print("== 3b. worker 抛异常 -> failed 而非挂起 ==")

    def boom(_payload: dict) -> dict:
        raise ValueError("下游炸了")

    worker.stop()
    worker2 = q.Worker(boom, consumer="smoke-w2", block_ms=200, idle_sleep_s=0.05).start()
    adm = q.submit({"query": "炸"}, timeout_s=15.0)
    worker2.stop()
    check("异常被回传为 failed", adm.state == "failed", f"{adm.state}/{adm.detail}")
    check("错误摘要含异常类型", "ValueError" in adm.detail, adm.detail)

    # -------------------------------------------------------------------
    print("== 4. 深度上报 ==")
    # 此刻无消费者。直接 XADD 而不走 submit()：submit 是同步等待的，
    # 超时后会撤单，观察不到稳定的深度。
    q.destroy_stream()
    q.ensure_group()
    for i in range(3):
        redis_client.call(
            "xadd", q.stream_key(), {"job": f"d{i}", "ts": "0", "data": "{}"},
            maxlen=1000, approximate=True,
        )
    st = q.stats(max_concurrent=8, queue_max=99)
    check("pending 反映流内存活条目", st["pending"] == 3, str(st))
    check("queued=3 / unacked=0（尚未投递）", st["queued"] == 3 and st["unacked"] == 0, str(st))
    check("stats 透传传入的上限", st["max_concurrent"] == 8 and st["queue_max"] == 99, str(st))
    check("depth() 与 stats.pending 一致", q.depth() == 3)

    jobs = q.fetch("smoke-peek", count=3, block_ms=200)
    check("fetch 取到 3 条", len(jobs) == 3, str(len(jobs)))
    st = q.stats()
    check("投递后 unacked=3、queued=0", st["unacked"] == 3 and st["queued"] == 0, str(st))
    check("消费者数上报", st["consumers"] == 1, str(st))
    for job in jobs:
        q.complete(job, {"ok": True})
    st = q.stats()
    check("XACK+XDEL 后深度归零（XLEN 不残留）", st["pending"] == 0 and st["unacked"] == 0, str(st))

    # -------------------------------------------------------------------
    print("== 5. 饱和拒绝（背压） ==")
    for i in range(5):
        redis_client.call(
            "xadd", q.stream_key(), {"job": f"s{i}", "ts": "0", "data": "{}"},
            maxlen=1000, approximate=True,
        )
    adm = q.submit({"query": "溢出"}, timeout_s=5.0, queue_max=5)
    check("深度达上限时拒绝", adm.state == "rejected", f"{adm.state}/{adm.detail}")
    check("拒绝原因可读（供 429 文案）", "队列已满" in adm.detail, adm.detail)
    check("拒绝不落盘：深度未增加", q.depth() == 5, str(q.depth()))
    adm = q.submit({"query": "放宽"}, timeout_s=0.3, queue_max=100)
    check("放宽上限后不再拒绝（转为超时）", adm.state == "timeout", f"{adm.state}/{adm.detail}")
    check("超时后撤单，深度回到 5", q.depth() == 5, str(q.depth()))

    # -------------------------------------------------------------------
    print("== 6. XAUTOCLAIM 捡回崩溃 worker 的任务 ==")
    q.destroy_stream()
    q.ensure_group()
    entry = redis_client.call(
        "xadd", q.stream_key(),
        {"job": "orphan-1", "ts": repr(time.time()), "data": '{"query":"孤儿"}'},
        maxlen=1000, approximate=True,
    )
    check("孤儿任务已入队", entry is not None)

    # 模拟 worker 崩溃：XREADGROUP 读走（进 PEL），然后**永不 XACK**。
    dead = q.fetch("dead-worker", count=1, block_ms=200)
    check("崩溃前的 worker 拿到了任务", len(dead) == 1, str(dead))
    check("任务此时挂在 PEL 上", q.stats()["unacked"] == 1, str(q.stats()))

    # min-idle 未到：别的 worker 不许抢（否则会把活人手里的活抢走重做）
    claimed = q.reclaim("rescuer", min_idle_ms=60_000, count=8)
    check("min-idle 未到时不接管", claimed == [], str(claimed))

    time.sleep(0.4)
    claimed = q.reclaim("rescuer", min_idle_ms=200, count=8)
    check("min-idle 到点后 XAUTOCLAIM 接管", len(claimed) == 1, str(claimed))
    check("接管的是同一条任务", claimed and claimed[0].job_id == "orphan-1", str(claimed))
    check("载荷完好", claimed and claimed[0].payload.get("query") == "孤儿", str(claimed))
    check("标记为 reclaimed（可用于埋点/告警）", claimed and claimed[0].reclaimed is True)
    # XPENDING 摘要只列**当前持有条目**的消费者，所以接管后 dead-worker
    # 会从列表里消失、总数仍为 1；要证明"换了主人"得看扩展形式。
    owners = redis_client.call("xpending_range", q.stream_key(), q.GROUP, "-", "+", 10) or []
    check(
        "PEL 归属已转移到接管者",
        len(owners) == 1 and owners[0]["consumer"] == b"rescuer",
        str(owners),
    )
    check(
        "投递次数 +1（证明是重投而非新任务）",
        len(owners) == 1 and int(owners[0]["times_delivered"]) == 2,
        str(owners),
    )
    check("接管后未 ACK 数仍为 1（没被重复放大）", q.stats()["unacked"] == 1, str(q.stats()))
    q.complete(claimed[0], {"recovered": True})
    check("接管者完成后深度归零", q.depth() == 0, str(q.depth()))

    print("== 6b. 端到端恢复：等待方最终仍拿到结果 ==")
    q.destroy_stream()
    q.ensure_group()
    # 先把任务读走不 ACK（模拟死掉的 worker），再让救援 worker 上线。
    entry = redis_client.call(
        "xadd", q.stream_key(),
        {"job": "e2e-1", "ts": repr(time.time()), "data": '{"query":"救我"}'},
        maxlen=1000, approximate=True,
    )
    q.fetch("dead-worker-2", count=1, block_ms=200)
    rescuer = q.Worker(
        lambda p: {"answer": f"已恢复:{p.get('query')}"},
        consumer="rescue-w",
        block_ms=200,
        min_idle_ms=200,
        idle_sleep_s=0.05,
    ).start()
    deadline = time.monotonic() + 10.0
    raw = None
    while time.monotonic() < deadline:
        raw = redis_client.call("get", redis_client.key("res", "e2e-1"))
        if raw is not None:
            break
        time.sleep(0.05)
    rescuer.stop()
    check("崩溃任务被后台 worker 自动救回并写出结果", raw is not None)
    check("恢复结果内容正确", raw is not None and "已恢复:救我" in raw.decode("utf-8"), str(raw))
    redis_client.call("delete", redis_client.key("res", "e2e-1"))

finally:
    # ===================================================================
    print("== 7. 清理（只删本前缀下的键） ==")
    q.destroy_stream()
    leftovers = redis_client.call("keys", (_PREFIX + "*").encode())
    for k in leftovers or ():
        redis_client.call("delete", k)
    leftovers = redis_client.call("keys", (_PREFIX + "*").encode())
    check("测试前缀下无残留键", not leftovers, str(leftovers))
    check(
        "共用实例的 dbsize 未被改动",
        redis_client.call("dbsize") == _dbsize_before,
        f"{_dbsize_before} -> {redis_client.call('dbsize')}",
    )
    redis_client.reset_client()

print()
print(f"结果：{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
