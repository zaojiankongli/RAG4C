"""两级答案缓存（core/query_cache.py）冒烟。

运行：python scripts/smoke_query_cache.py
覆盖：
1. 无 Redis（纯 L1）：命中 / TTL 过期 / LRU 淘汰 / 进程内 single-flight
2. 有 Redis（L1+L2）：跨实例共享、L2 命中回填 L1、L2 TTL 过期
3. 跨进程 single-flight：两个实例（= 两个进程）抢同一把锁，输家等赢家的结果
4. 锁的比较删除：不删别人的锁
5. Redis 中途挂掉：不抛异常，静默退回纯 L1
6. TTL 抖动：只往短里抖（避免同批写入的条目同秒集体过期造成周期性尖峰）

Redis 不可达时第 2~5 组打印 [SKIP] 而不是 [FAIL]：本模块的核心契约就是
「Redis 缺席也能用」，CI 机器上没有 Redis 属于被支持的形态，不该判红。

清理：只删本次运行自己前缀（rag4c-smoke-<pid>-<rand>:）下的键，并核对
dbsize 不变——目标实例与其它应用共用 db，扫尾扫到别人头上就是生产事故。
"""
from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path

# ruff: noqa: E402

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# 必须在导入 config.settings 之前落地：get_settings 是 lru_cache 单例，
# 且 .env 以 override=False 加载，先写 os.environ 才能压住仓库里的 .env。
_TEST_PREFIX = f"rag4c-smoke-{os.getpid()}-{uuid.uuid4().hex[:8]}:"
_REDIS_URL = os.environ.get("RAG4C_SMOKE_REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ["RAG4C_REDIS_URL"] = ""          # 第一阶段：无 Redis
os.environ["RAG4C_REDIS_KEY_PREFIX"] = _TEST_PREFIX
os.environ["RAG4C_QUERY_CACHE_TTL_S"] = "600"
os.environ["RAG4C_QUERY_CACHE_MAX"] = "64"

from config.settings import get_settings
from core import redis_client
from core.query_cache import (
    CacheConfig,
    QueryCache,
    get_query_cache,
    make_cache_key,
    reset_query_cache,
)

passed = 0
failed = 0
skipped = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} {detail}")


def skip(name: str, reason: str) -> None:
    global skipped
    skipped += 1
    print(f"  [SKIP] {name}（{reason}）")


def _use_redis(url: str) -> None:
    """切换 Redis 配置并让配置单例 + 连接单例同时失效。"""
    os.environ["RAG4C_REDIS_URL"] = url
    get_settings.cache_clear()
    redis_client.reset_client()
    reset_query_cache()


def _key(tag: str) -> str:
    """每次运行用独立的问句，避免同一 Redis 上多次运行相互干扰。"""
    return make_cache_key(f"{tag}-{uuid.uuid4().hex[:6]}", ["public"], False, "t1", "ds1")


# ===========================================================================
print("== 1. 无 Redis：纯 L1 行为（与改造前逐条对齐） ==")
# ===========================================================================
_use_redis("")
check("未配置时 l2_configured 为 False", not QueryCache().l2_configured())

cache = QueryCache(replace(CacheConfig.from_settings(), l1_max=3, l1_ttl_s=0.4))
k1 = _key("plain")
check("首次 peek 未命中", cache.peek(k1) is None)

cached, flight, owner = cache.reserve(k1)
check("首次 reserve 取得所有权", cached is None and owner is True)
cache.put(k1, {"answer": "答案一", "citations": []})
cache.release(k1, flight)
check("put 后 peek 命中", (cache.peek(k1) or {}).get("answer") == "答案一")

got = cache.peek(k1)
got["mutated"] = True
check("返回的是浅拷贝（调用方改不脏缓存）", "mutated" not in (cache.peek(k1) or {}))

cached2, _, owner2 = cache.reserve(k1)
check("命中时 reserve 不取所有权", cached2 is not None and owner2 is False)

print("-- TTL 过期 --")
time.sleep(0.5)
check("L1 超过 ttl 后失效", cache.peek(k1) is None)

print("-- LRU 淘汰 --")
lru = QueryCache(replace(CacheConfig.from_settings(), l1_max=3, l1_ttl_s=60))
keys = [_key(f"lru{i}") for i in range(4)]
for i, k in enumerate(keys[:3]):
    lru.put(k, {"answer": str(i)})
lru.peek(keys[0])                      # 触碰 0 号，使 1 号成为最久未用
lru.put(keys[3], {"answer": "3"})
check("容量上限生效", lru.stats()["size"] == 3, str(lru.stats()))
check("淘汰的是最久未使用项", lru.peek(keys[1]) is None and lru.peek(keys[0]) is not None)

print("-- 进程内 single-flight --")
sf = QueryCache(replace(CacheConfig.from_settings(), l1_ttl_s=60))
k_sf = _key("sf-local")
_, owner_flight, is_owner = sf.reserve(k_sf)
waiter: dict[str, object] = {}


def _wait_worker() -> None:
    c, f, o = sf.reserve(k_sf)
    waiter["owner"] = o
    waiter["result"] = sf.wait(k_sf, f, timeout=5.0)


t = threading.Thread(target=_wait_worker)
t.start()
time.sleep(0.2)
check("在途期间第二个线程不是 owner", waiter.get("owner") is False)
sf.put(k_sf, {"answer": "只算一次"})
sf.release(k_sf, owner_flight)
t.join(timeout=5)
check("等待者被唤醒并拿到结果",
      isinstance(waiter.get("result"), dict) and waiter["result"]["answer"] == "只算一次")
check("统计记录了进程内等待", sf.stats()["singleflight_local_waits"] == 1, str(sf.stats()))
check("is_owner 语义正确", is_owner is True)

print("-- 无 Redis 时全部出口不抛异常 --")
try:
    nc = QueryCache()
    kx = _key("noredis")
    c, f, o = nc.reserve(kx)
    nc.put(kx, {"answer": "x"})
    nc.release(kx, f)
    nc.invalidate(kx)
    nc.clear_l1()
    nc.stats()
    check("纯内存模式全链路无异常", True)
except Exception as exc:  # noqa: BLE001
    check("纯内存模式全链路无异常", False, repr(exc))

check("默认单例可用", isinstance(get_query_cache(), QueryCache))

# ===========================================================================
print("== 2. 有 Redis：L2 共享与回填 ==")
# ===========================================================================
_use_redis(_REDIS_URL)
_redis_ok = redis_client.ping()
_client = redis_client.get_client() if _redis_ok else None
_dbsize_before = None
if _client is not None:
    try:
        _dbsize_before = _client.dbsize()
    except Exception:  # noqa: BLE001
        _dbsize_before = None

if not _redis_ok:
    for name in (
        "L2 写入后另一实例可读", "L2 命中回填 L1", "L2 TTL 过期",
        "跨进程 single-flight：输家不重复计算", "跨进程 single-flight：输家拿到赢家的结果",
        "比较删除：不删别人的锁", "release 后自己的锁被删除",
        "Redis 中途不可用不抛异常", "Redis 不可用时立刻自己算（不空等）",
    ):
        skip(name, f"{_REDIS_URL} 不可达")
else:
    check("配置后 l2_configured 为 True", QueryCache().l2_configured())
    cfg = replace(CacheConfig.from_settings(), l1_ttl_s=60, l2_ttl_s=30,
                  lock_ttl_s=10, wait_timeout_s=5, poll_interval_s=0.02)
    # 两个实例 = 两个进程：各有独立 L1 与独立的锁令牌表，只共享 Redis
    proc_a = QueryCache(cfg)
    proc_b = QueryCache(cfg)

    k_l2 = _key("l2")
    proc_a.put(k_l2, {"answer": "来自 A 的答案"})
    got_b = proc_b.peek(k_l2)
    check("L2 写入后另一实例可读",
          (got_b or {}).get("answer") == "来自 A 的答案", str(got_b))
    check("统计计入 l2_hits", proc_b.stats()["l2_hits"] == 1, str(proc_b.stats()))
    proc_b.peek(k_l2)
    check("L2 命中回填 L1",
          proc_b.stats()["l1_hits"] == 1 and proc_b.stats()["promotions"] == 1,
          str(proc_b.stats()))

    print("-- 键名不含明文问句 --")
    plain = "这是一句绝对不该出现在 Redis 键名里的问句"
    k_secret = make_cache_key(plain, None, False, "t1", "ds1")
    proc_a.put(k_secret, {"answer": "ok"})
    names = [n.decode("utf-8", "replace") for n in _client.keys(_TEST_PREFIX + "*")]
    check("Redis 键名已哈希（不含问句原文）",
          all(plain not in n for n in names) and any(len(n.rsplit(":", 1)[-1]) == 64 for n in names),
          str(names[:3]))

    print("-- L2 TTL 过期 --")
    short = QueryCache(replace(cfg, l2_ttl_s=0.5))
    k_ttl = _key("l2ttl")
    short.put(k_ttl, {"answer": "短命"})
    short.clear_l1()
    check("L2 未过期时可读", short.peek(k_ttl) is not None)
    time.sleep(0.8)
    short.clear_l1()
    check("L2 TTL 过期", short.peek(k_ttl) is None)

    print("-- 跨进程 single-flight --")
    k_sf2 = _key("sf-cross")
    _, flight_a, owner_a = proc_a.reserve(k_sf2)
    check("A 取得跨进程所有权", owner_a is True)
    result_b: dict[str, object] = {}

    def _b_worker() -> None:
        c, f, o = proc_b.reserve(k_sf2)
        result_b["owner"] = o
        result_b["cached"] = c

    tb = threading.Thread(target=_b_worker)
    tb.start()
    time.sleep(0.4)
    check("B 在 A 计算期间被挡住（仍在等）", tb.is_alive())
    proc_a.put(k_sf2, {"answer": "由 A 计算"})
    proc_a.release(k_sf2, flight_a)
    tb.join(timeout=8)
    check("跨进程 single-flight：输家不重复计算", result_b.get("owner") is False,
          str(result_b.get("owner")))
    check("跨进程 single-flight：输家拿到赢家的结果",
          (result_b.get("cached") or {}).get("answer") == "由 A 计算", str(result_b))
    check("统计记录了跨进程等待",
          proc_b.stats()["singleflight_remote_waits"] >= 1, str(proc_b.stats()))

    print("-- 锁的比较删除 --")
    k_lock = _key("lock-steal")
    _, flight_c, owner_c = proc_a.reserve(k_lock)
    check("拿到锁即成为 owner", owner_c is True)
    lock_name = redis_client.key("lock", __import__("hashlib").sha256(
        k_lock.encode("utf-8")).hexdigest())
    # 模拟：A 的锁因 TTL 过期，另一个进程重新持有了同一把锁
    _client.set(lock_name, b"another-process-token")
    proc_a.release(k_lock, flight_c)
    check("比较删除：不删别人的锁",
          _client.get(lock_name) == b"another-process-token",
          str(_client.get(lock_name)))
    _client.delete(lock_name)

    k_lock2 = _key("lock-own")
    _, flight_d, _ = proc_a.reserve(k_lock2)
    lock_name2 = redis_client.key("lock", __import__("hashlib").sha256(
        k_lock2.encode("utf-8")).hexdigest())
    check("owner 期间锁存在", _client.exists(lock_name2) == 1)
    proc_a.release(k_lock2, flight_d)
    check("release 后自己的锁被删除", _client.exists(lock_name2) == 0)

    print("-- Redis 中途挂掉 --")
    import core.query_cache as qc_module

    class _DeadClient:
        """任何命令都炸的假客户端（模拟连接在飞行中断开）。"""

        def __getattr__(self, _name: str):
            def _boom(*_a: object, **_kw: object):
                raise ConnectionError("模拟 Redis 断线")

            return _boom

    _real_get_client = redis_client.get_client
    k_dead = _key("dead")
    _, flight_e, owner_e = proc_a.reserve(k_dead)   # 飞行开始时 Redis 还活着
    try:
        qc_module._redis.get_client = lambda: _DeadClient()  # type: ignore[assignment]
        t0 = time.monotonic()
        proc_a.put(k_dead, {"answer": "断线期间算出来的"})
        proc_a.release(k_dead, flight_e)
        hit = proc_a.peek(k_dead)
        # 另一个「进程」此时既读不到 L2 也拿不到锁，必须立刻自己算而不是空等
        c2, f2, o2 = proc_b.reserve(k_dead)
        elapsed = time.monotonic() - t0
        proc_b.release(k_dead, f2)
        check("Redis 中途不可用不抛异常", owner_e is True and (hit or {}).get("answer") == "断线期间算出来的")
        check("Redis 不可用时立刻自己算（不空等）", o2 is True and elapsed < 1.0,
              f"owner={o2} elapsed={elapsed:.2f}s")
        check("L2 错误被计数", proc_a.stats()["l2_errors"] >= 1, str(proc_a.stats()))
    finally:
        qc_module._redis.get_client = _real_get_client  # type: ignore[assignment]

    # 用 ping() 而不是 l2_reachable()：后者带 5 秒探活缓存，断线那一段若被探
    # 过，这里会读到还没过期的负缓存，恢复了也报不可达。
    check("恢复后 L2 重新可用", QueryCache(cfg).l2_configured() and redis_client.ping())

# ===========================================================================
print("== 3. TTL 抖动：只往短里抖，绝不延长 ==")
# ===========================================================================
# 治的是同步过期：一批答案常常是同时写进去的（重启后的第一波流量、代次递增
# 后整租户重算、定时预热），带着一模一样的 TTL，于是也会在同一秒集体过期，
# 把负载从"平摊"整形成"周期性尖峰"，尖峰高度恰好等于命中率。
jc = QueryCache(replace(CacheConfig.from_settings(), l1_ttl_s=100.0, ttl_jitter=0.15,
                        redis_on=False))
samples = [jc._jittered(100.0) for _ in range(200)]
check("抖动后的 TTL 绝不超过配置值（上界语义不变）", max(samples) <= 100.0,
      f"max={max(samples)}")
check("抖动幅度受 ttl_jitter 约束（不会短过 85）", min(samples) >= 85.0,
      f"min={min(samples)}")
check("确实抖开了（200 次采样不是同一个值）", len(set(samples)) > 100,
      f"distinct={len(set(samples))}")

# 关掉抖动必须是精确的恒等——有人要复现问题、要断言精确 TTL 时得有这条退路。
zc = QueryCache(replace(CacheConfig.from_settings(), ttl_jitter=0.0, redis_on=False))
check("ttl_jitter=0 时 TTL 精确不变", zc._jittered(100.0) == 100.0,
      str(zc._jittered(100.0)))
check("TTL 为 0 不被抖成负数", zc._jittered(0.0) == 0.0 and jc._jittered(0.0) == 0.0)

# 抖动要真的走到写路径上，否则它只是个没人调的函数。
k_j = make_cache_key("抖动问题", [], False, "t1", "ds1")
jc.put(k_j, {"answer": "x"})
_deadline = jc._l1[k_j][0]
_left = _deadline - time.monotonic()
check("L1 条目的到期时刻带上了抖动（< 配置的 100s）", _left < 100.0, f"{_left:.3f}s")
check("ttl_jitter 出现在 stats 里", jc.stats().get("ttl_jitter") == 0.15)

# ===========================================================================
print("== 4. 清理（只删本次运行自己的前缀） ==")
# ===========================================================================
if _client is not None:
    removed = 0
    try:
        own = list(_client.scan_iter(match=_TEST_PREFIX + "*", count=100))
        if own:
            removed = _client.delete(*own)
        left = list(_client.scan_iter(match=_TEST_PREFIX + "*", count=100))
        check(f"本次运行的 {removed} 个键已清理干净", not left, str(left[:3]))
        if _dbsize_before is not None:
            check("共用 db 的键总数未被波及",
                  _client.dbsize() == _dbsize_before, f"before={_dbsize_before}")
    except Exception as exc:  # noqa: BLE001
        check("Redis 清理", False, repr(exc))
else:
    skip("Redis 清理", "无连接，未写入任何键")

# ===========================================================================
print("== 5. 测试键空间隔离工具（_smoke_redis）的护栏 ==")
# ===========================================================================
# sweep() 是全仓库唯一的批量删除入口，误传生产前缀就是清掉与别人共用的
# 那个实例上的真实数据，不可逆。所以它的**拒绝**行为必须有断言看着。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _smoke_redis import TEST_PREFIX_ROOT, sweep  # noqa: E402

for bad in ("rag4c:", "rag4c:cache:", "", "*"):
    try:
        sweep(bad)
        check(f"sweep 拒绝非测试前缀 {bad!r}", False, "竟然没有抛错")
    except ValueError:
        check(f"sweep 拒绝非测试前缀 {bad!r}", True)
    except Exception as exc:  # noqa: BLE001
        check(f"sweep 拒绝非测试前缀 {bad!r}", False, f"抛的不是 ValueError: {exc!r}")

check("测试前缀根本身就不以生产前缀开头",
      not TEST_PREFIX_ROOT.startswith("rag4c:"), TEST_PREFIX_ROOT)

# 「扫过了，没有」与「根本没看成」必须能分开：前者 0，后者 -1。
# 混成一个 0 的话，没配 Redis 的进程会报告"已清理干净"，而残留还在共用实例上。
_absent = f"{TEST_PREFIX_ROOT}nonexistent-{uuid.uuid4().hex[:8]}:"
if _client is not None:
    check("sweep 扫到空前缀返回 0（而非 -1）", sweep(_absent) == 0)
else:
    check("Redis 不可用时 sweep 返回 -1 而不是谎报 0", sweep(_absent) == -1)

print()
print(f"结果：{passed} passed, {failed} failed, {skipped} skipped")
sys.exit(1 if failed else 0)
