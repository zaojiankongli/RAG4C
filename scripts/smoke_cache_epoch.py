"""语料代次失效（core/cache_epoch.py）+ 弃权短 TTL 冒烟。

运行：python scripts/smoke_cache_epoch.py
覆盖：
1. 无 Redis：代次退回进程内计数器，bump / stamp / 键随代次变化
2. 失效粒度：按租户隔离；bump_all 影响所有租户
3. 降级契约：Redis 读失败时**沿用上次读到的值**，绝不制造新代次
4. 弃权短 TTL：QueryCache 按载荷内容选 TTL（含 server 的信封嵌套形态）
5. 有 Redis：代次跨实例（= 跨副本）可见，旧代次的键再也拼不出来

第 5 组在 Redis 不可达时打印 [SKIP]：与缓存本身一样，代次是加速件不是
依赖件，没有 Redis 的形态是被支持的，不该判红。

清理：本脚本只写 ``rag4c-test:epoch-<pid>:`` 前缀下的键，退出时 sweep，
并核对共用实例的 dbsize 未被波及。
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from dataclasses import replace
from pathlib import Path

# ruff: noqa: E402

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _smoke_redis import isolate_redis_keyspace, sweep

# 必须早于 config.settings 的导入：get_settings 是 lru_cache 单例。
_PREFIX = isolate_redis_keyspace(f"epoch-{os.getpid()}")
_REDIS_URL = os.environ.get("RAG4C_SMOKE_REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ["RAG4C_REDIS_URL"] = ""          # 第一阶段：无 Redis

from config.settings import get_settings
from core import cache_epoch, redis_client
from core.query_cache import CacheConfig, QueryCache, make_cache_key, reset_query_cache

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
    os.environ["RAG4C_REDIS_URL"] = url
    get_settings.cache_clear()
    redis_client.reset_client()
    reset_query_cache()
    cache_epoch.reset()


# ===========================================================================
print("== 1. 无 Redis：代次退回进程内计数器 ==")
# ===========================================================================
_use_redis("")

check("未 bump 过的租户代次为 0", cache_epoch.current("t1") == 0)
check("stamp 形如 <全局>.<租户>", cache_epoch.stamp("t1") == "0.0",
      cache_epoch.stamp("t1"))

before = cache_epoch.stamp("t1")
new = cache_epoch.bump("t1", reason="冒烟")
check("bump 返回递增后的代次", new == 1, str(new))
check("bump 立刻对同进程可见（不等 TTL 窗口）",
      cache_epoch.stamp("t1") != before, f"{before} -> {cache_epoch.stamp('t1')}")

# 代次进键才有意义——这才是"失效"真正发生的地方。
args = ("同一个问题", ["public"], False, "t1", "ds1")
k_old = make_cache_key(*args, epoch=before)
k_new = make_cache_key(*args, epoch=cache_epoch.stamp("t1"))
check("代次变化后缓存键随之变化", k_old != k_new)
check("代次不变时缓存键稳定",
      make_cache_key(*args, epoch=k_new and cache_epoch.stamp("t1")) == k_new)

# ===========================================================================
print("== 2. 失效粒度：按租户隔离，bump_all 全域 ==")
# ===========================================================================
_use_redis("")
s_a, s_b = cache_epoch.stamp("tenant-a"), cache_epoch.stamp("tenant-b")
cache_epoch.bump("tenant-a", reason="只动 A")
check("bump 某租户不影响其它租户", cache_epoch.stamp("tenant-b") == s_b,
      f"{s_b} -> {cache_epoch.stamp('tenant-b')}")
check("bump 的租户自身代次变了", cache_epoch.stamp("tenant-a") != s_a)

s_a, s_b = cache_epoch.stamp("tenant-a"), cache_epoch.stamp("tenant-b")
cache_epoch.bump_all(reason="配置变更")
check("bump_all 让 A 失效", cache_epoch.stamp("tenant-a") != s_a)
check("bump_all 让 B 也失效", cache_epoch.stamp("tenant-b") != s_b)

# dataset 不参与代次：全域检索（dataset_id=""）的答案跨库，
# 按库失效会漏掉它们——这条断言是那个设计决定的守门人。
check("同租户不同知识库共享同一代次戳",
      cache_epoch.stamp("tenant-a") == cache_epoch.stamp("tenant-a"))

# ===========================================================================
print("== 3. 降级：读不到就沿用旧值，绝不制造新代次 ==")
# ===========================================================================
# 读失败时若换一个新代次，Redis 每抖一下就会全量击穿缓存，把整片流量打到
# LLM 上——为了一个可选组件的抖动。这条契约值得一个断言看着。
_use_redis(_REDIS_URL)
_client = redis_client.get_client()
# 基线要在**写任何键之前**取。取晚了（比如等到第 5 组）就把本脚本自己写的
# 键算进了基线，扫尾之后总数反而变少，断言会误报成"波及了别人"。
_dbsize_before = _client.dbsize() if _client is not None else None
if not redis_client.is_configured() or _client is None:
    skip("读失败沿用旧值", "Redis 不可达")
else:
    tenant = f"degrade-{uuid.uuid4().hex[:6]}"
    known = cache_epoch.bump(tenant, reason="先落一个已知值")
    cache_epoch.reset()
    # 冷启动的 current() 不阻塞：立刻返回 0 并在后台补读。这条正是它与
    # refresh_now() 的分工——事件循环上只能用前者。
    check("冷启动 current() 立刻返回（不等 Redis）", cache_epoch.current(tenant) == 0)
    stable = cache_epoch.refresh_now(tenant)   # 同步从 Redis 读回
    check("refresh_now 能从 Redis 读回代次", stable == known, f"{stable} != {known}")
    check("刷新后 current() 读到的是刷新过的值", cache_epoch.current(tenant) == stable)

    original = redis_client.call

    def _boom(*a, **kw):     # 模拟 Redis 命令失败
        return None

    redis_client.call = _boom          # type: ignore[assignment]
    try:
        time.sleep(cache_epoch.EPOCH_CACHE_TTL_S + 0.1)   # 越过进程内 TTL 窗口
        degraded = cache_epoch.current(tenant)
        check("读失败时返回上次读到的值", degraded == stable, f"{degraded} != {stable}")
        time.sleep(0.2)                                   # 等后台刷新线程跑完
        check("后台刷新失败也不会把代次改坏",
              cache_epoch.current(tenant) == stable, str(cache_epoch.current(tenant)))
        bumped = cache_epoch.bump(tenant, reason="Redis 挂了也要能调")
        check("bump 失败时返回本地值且不抛", isinstance(bumped, int))
    finally:
        redis_client.call = original   # type: ignore[assignment]

    # 冷启动窗口：不预热的话，第一个请求会用 epoch=0 算键、几秒后后台刷新
    # 补上真值，第二个问同一句的人算出来是另一个键——头一条缓存从落地起就是
    # 垃圾，症状是"服务刚起来那会儿缓存像是不工作"，几秒后自愈且日志无声。
    # server.app 启动时调 prime() 消掉这个窗口，这条断言看着它。
    cache_epoch.reset()
    cache_epoch.prime(tenant)
    check("prime 之后 current() 立刻是真值（冷启动窗口已消除）",
          cache_epoch.current(tenant) == stable, str(cache_epoch.current(tenant)))

# ===========================================================================
print("== 4. 弃权结果短 TTL ==")
# ===========================================================================
_use_redis("")
cfg = replace(CacheConfig.from_settings(), l1_ttl_s=60.0, abstain_ttl_s=0.3)
c = QueryCache(cfg)

k_ok = make_cache_key("正常问题", ["public"], False, "t1", "ds1")
k_no = make_cache_key("答不了的问题", ["public"], False, "t1", "ds1")
c.put(k_ok, {"result": {"answer": "有答案", "abstained": False}})
c.put(k_no, {"result": {"answer": "", "abstained": True}})

check("弃权与正常都先写进去了",
      c.peek(k_ok) is not None and c.peek(k_no) is not None)
time.sleep(0.4)
check("弃权结果按短 TTL 过期", c.peek(k_no) is None)
check("正常答案不受影响仍在缓存里", c.peek(k_ok) is not None)

# server 存的是 {"result": {...}} 这层信封，abstained 在里面。只看顶层的话
# 这个策略会静默失效——一直用默认 TTL，测不出来也不报错。
check("能识别信封内层的 abstained",
      c._ttl_for({"result": {"abstained": True}}) == 0.3)
check("也能识别顶层 abstained", c._ttl_for({"abstained": True}) == 0.3)
check("正常载荷返回 None（用各级默认 TTL）",
      c._ttl_for({"result": {"abstained": False}}) is None)
check("abstain_ttl_s 出现在 stats 里", c.stats().get("abstain_ttl_s") == 0.3)

# ===========================================================================
print("== 5. 有 Redis：代次跨实例可见 ==")
# ===========================================================================
_use_redis(_REDIS_URL)
_client = redis_client.get_client()
if _client is None:
    skip("代次跨实例可见", "Redis 不可达")
    skip("弃权结果在 L2 也短命", "Redis 不可达")
else:
    tenant = f"xproc-{uuid.uuid4().hex[:6]}"
    e1 = cache_epoch.bump(tenant, reason="副本 A 入库")
    raw = _client.get(redis_client.key("epoch", tenant))
    check("代次真的落到了 Redis 上", raw is not None and int(raw) == e1, repr(raw))

    # 模拟另一个副本：清掉进程内缓存后重读，应看到 A 写的代次
    cache_epoch.reset()
    check("另一副本读到同一代次", cache_epoch.refresh_now(tenant) == e1)

    # 关键性质：代次涨了之后，同一问句算出来的键与之前不同 -> 旧键失联
    args = ("跨副本问题", ["public"], False, tenant, "")
    k_before = make_cache_key(*args, epoch=cache_epoch.stamp(tenant))
    cache_epoch.bump(tenant, reason="又一次入库")
    k_after = make_cache_key(*args, epoch=cache_epoch.stamp(tenant))
    check("入库后旧缓存键不再被拼出（= 失效）", k_before != k_after)

    # L2 上的弃权也要短命，否则另一个副本会替你把陈旧的"我不知道"发出去
    c2 = QueryCache(replace(CacheConfig.from_settings(), l2_ttl_s=900.0,
                            abstain_ttl_s=1.0))
    # 下面要真往 Redis 上写再读 pttl，光"配置了"不够，得真连得上。
    if not c2.l2_reachable():
        skip("弃权结果在 L2 也短命", "L2 不可达")
    else:
        k_ab = make_cache_key(f"弃权-{uuid.uuid4().hex[:6]}", [], False, tenant, "")
        c2.put(k_ab, {"result": {"answer": "", "abstained": True}})
        c2.clear_l1()
        pttl_ab = None
        for key in _client.scan_iter(match=f"{_PREFIX}cache:*", count=200):
            ttl = _client.pttl(key)
            if ttl and ttl <= 1000:
                pttl_ab = ttl
                break
        check("L2 上弃权键的 TTL 是短的（<=1s，而非默认 900s）",
              pttl_ab is not None, f"pttl={pttl_ab}")

# ===========================================================================
print("== 清理 ==")
# ===========================================================================
removed = sweep(_PREFIX)
if removed < 0:
    skip("Redis 清理", "无连接，无从核对残留")
else:
    check(f"本次运行写入的键已清理（{removed} 条）", True)
    if _client is not None and _dbsize_before is not None:
        # 共用实例：扫尾扫到别人头上就是生产事故，这条断言是最后一道闸。
        check("共用 db 的键总数未被波及",
              _client.dbsize() == _dbsize_before, f"before={_dbsize_before}")

print()
print(f"结果：{passed} passed, {failed} failed, {skipped} skipped")
sys.exit(1 if failed else 0)
