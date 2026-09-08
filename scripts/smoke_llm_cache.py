"""LLM 槽位结果缓存（core/llm_cache.py + core/llm.py 的接线）冒烟。

运行：python scripts/smoke_llm_cache.py
覆盖：
1. 摘要：模型 / 端点 / 温度 / max_tokens / seed / json_mode / 消息任一变即换键；
   dict 键序不影响摘要
2. 开关：cache_ttl_s=0 的槽位（generation / judge / 入库三件套）根本不进缓存
3. 命中：开了缓存的槽位第二次不再打远程，且 TTL 按槽位配置写
4. 校验器：chat_json 不会把解不出 JSON 的输出缓起来；缓存里的坏值就地作废
5. 记账：命中时报出**实测**省下的 token（存在信封里的真实用量）
6. 降级：Redis 挂掉 / 缓存层整体抛异常都不影响调用结果
7. 配置：六个检索期槽位默认开、生成/判定/入库槽位默认关；**且配置里覆盖过
   某个槽位后缓存依然是开的**（这一条治的是真实发生过的静默失效）；显式写 0
   能真的关掉；真实 .env 下六个槽位确实开着

清理：只写 ``rag4c-test:llm-cache-<pid>-<rand>:`` 前缀下的键，退出时 sweep，
并核对共用实例的 dbsize 未被波及。
"""
from __future__ import annotations

import atexit
import os
import sys
import uuid
from pathlib import Path

# ruff: noqa: E402

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _smoke_redis import TEST_PREFIX_ROOT, sweep

# 本进程独有的前缀（不用 isolate_redis_keyspace）：那个函数在 run_tests.py 下
# 会沿用整轮共用的前缀，而本脚本收尾要 sweep 自己的键并断言 dbsize 没变——
# 用共用前缀会把同轮其它脚本还在用的键一并删掉。仍挂在 TEST_PREFIX_ROOT 之下，
# sweep() 的护栏照常生效。
_PREFIX = f"{TEST_PREFIX_ROOT}llm-cache-{os.getpid()}-{uuid.uuid4().hex[:8]}:"
os.environ["RAG4C_REDIS_KEY_PREFIX"] = _PREFIX
atexit.register(sweep, _PREFIX)

# 必须早于 config.settings 的导入：get_settings 是 lru_cache 单例。
_REDIS_URL = os.environ.get("RAG4C_SMOKE_REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ["RAG4C_REDIS_URL"] = ""          # 第一阶段：无 Redis

from types import SimpleNamespace

from config.settings import (
    _RETRIEVAL_SLOTS,
    LlmSlotSettings,
    LlmSlotsSettings,
    get_settings,
)
from core import redis_client
from core.llm import LLMClient, ParseFallbackError
from core.llm_cache import _digest, get_llm_cache, reset_llm_cache
from core.metrics import get_metrics

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
    reset_llm_cache()


_BASE = dict(
    model="m", base_url="http://a/v1", temperature=0.0, max_tokens=100,
    seed=None, json_mode=False, messages=[{"role": "user", "content": "hi"}],
)


class _Fake:
    """最小 chat.completions 桩：记录调用次数，可指定每次返回什么。"""

    def __init__(self, *replies: str, usage: object | None = None) -> None:
        self.replies = list(replies) or ["答案"]
        self.calls = 0
        self.usage = usage if usage is not None else SimpleNamespace(
            prompt_tokens=30, completion_tokens=12, total_tokens=42
        )

    def create(self, **kwargs):
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=reply))],
            usage=self.usage,
        )


def _client(fake: _Fake, **slot_kwargs) -> LLMClient:
    cfg = LlmSlotSettings(model="fake-model", base_url="http://fake/v1", **slot_kwargs)
    c = LLMClient(cfg)
    c._client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    return c


def _saved() -> float:
    """已记账的「缓存省下的 total token」之和。

    取 ``sum`` 而不是 ``count``：incr(value=N) 记的是一次 N 的采样，count 是
    调用次数。拿 count 当 token 数会得到一条恒等于命中次数的曲线。
    """
    snap = get_metrics().snapshot()
    return sum(v.get("sum", 0.0)
               for k, v in snap.items() if k.startswith("llm.cache.saved.total"))


# ===========================================================================
print("== 1. 摘要：请求的任一维度变了就必须换键 ==")
# ===========================================================================
# 这些字段住在槽位配置上、不在调用参数里，配置热更新能把它们改掉而 prompt
# 一个字不变。不进键的话，改完配置拿到的还是旧模型的输出，不报错也查不出来。
_ref = _digest(**_BASE)
for field, other in (
    ("model", "m2"),
    ("base_url", "http://b/v1"),
    ("temperature", 0.7),
    ("max_tokens", 200),
    ("seed", 42),
    ("json_mode", True),
):
    check(f"{field} 变了就换键", _digest(**{**_BASE, field: other}) != _ref)
check("消息内容变了就换键",
      _digest(**{**_BASE, "messages": [{"role": "user", "content": "hi!"}]}) != _ref)
check("消息条数变了就换键",
      _digest(**{**_BASE, "messages": _BASE["messages"] * 2}) != _ref)
# str(dict) 依赖插入顺序：同样内容只因构造顺序不同就算出不同摘要，命中率会
# 莫名其妙地低且没人查得出为什么。sort_keys 治的就是这个。
check("dict 键序不影响摘要",
      _digest(**{**_BASE, "messages": [{"content": "hi", "role": "user"}]}) == _ref)
check("同样的请求算出同样的摘要", _digest(**_BASE) == _ref)

# ===========================================================================
print("== 2. 开关：cache_ttl_s=0 的槽位根本不进缓存 ==")
# ===========================================================================
_use_redis("")
f = _Fake("一", "二")
c = _client(f, cache_ttl_s=0.0)
check("关着缓存时两次都真调", c.chat([{"role": "user", "content": "q"}]) == "一"
      and c.chat([{"role": "user", "content": "q"}]) == "二", f"calls={f.calls}")
check("关着缓存时一条都没写进去", get_llm_cache().stats()["size"] == 0,
      str(get_llm_cache().stats()))

# ===========================================================================
print("== 3. 命中：开了缓存的槽位第二次不打远程 ==")
# ===========================================================================
reset_llm_cache()
f = _Fake("一", "二")
c = _client(f, cache_ttl_s=60.0)
msgs = [{"role": "user", "content": "同一个问题"}]
check("第一次真调", c.chat(msgs) == "一")
check("第二次命中缓存（内容一致）", c.chat(msgs) == "一")
check("远程只被调了一次", f.calls == 1, f"calls={f.calls}")
check("换个问题不会误命中", c.chat([{"role": "user", "content": "另一个"}]) == "二")

# 不同槽位、同样的请求 -> 共用一条缓存。键里不放槽位名是刻意的：请求相同
# 就说明问的是同一件事，各缓一份只是浪费。
f2 = _Fake("三")
c2 = _client(f2, cache_ttl_s=999.0)
check("同样的请求跨槽位共用（键里不含槽位名）", c2.chat(msgs) == "一" and f2.calls == 0,
      f"calls={f2.calls}")

# 空回复不写缓存：那多半是上游出错的产物，缓起来等于把一次偶发失败钉死。
f3 = _Fake("")
c3 = _client(f3, cache_ttl_s=60.0)
_before = get_llm_cache().stats()["size"]
c3.chat([{"role": "user", "content": "空回复"}])
check("空回复不写缓存", get_llm_cache().stats()["size"] == _before)

# ===========================================================================
print("== 4. 校验器：解不出 JSON 的输出不许留在缓存里 ==")
# ===========================================================================
# 这一条治的是"坏值黏住整个 TTL"：JSON 槽位恰好是默认开缓存的那几个，一次
# 偶发的非 JSON 输出若被缓存，之后每个请求都要先吃一发坏命中、再真调一次
# 修复，稳定地花双份钱。
reset_llm_cache()
f = _Fake("这不是 JSON", '{"ok": 1}')
c = _client(f, cache_ttl_s=60.0)
out = c.chat_json([{"role": "user", "content": "给我 JSON"}])
check("chat_json 靠重试拿到了合法 JSON", out == {"ok": 1}, str(out))
check("坏输出没有被缓存（只留下了那条好的）", get_llm_cache().stats()["size"] == 1,
      str(get_llm_cache().stats()))

# 缓存里已经躺着一份坏值（模拟：当初写进去时还没有校验器）时，要就地作废
# 并真调一次，而不是把坏值原样交出去。
cache = get_llm_cache()
bad_msgs = [{"role": "user", "content": "历史坏值"}]
bad_digest = _digest(
    model="fake-model", base_url="http://fake/v1", temperature=0.2,
    max_tokens=4096, seed=None, json_mode=True, messages=bad_msgs,
)
cache.put_digest(bad_digest, cache.envelope("依然不是 JSON"), ttl_s=60.0)
f = _Fake('{"fixed": true}')
c = _client(f, cache_ttl_s=60.0)
check("缓存里的坏值不会被交出去", c.chat_json(bad_msgs) == {"fixed": True})
check("坏值被就地作废", cache.get_digest(bad_digest) is None
      or cache.get_digest(bad_digest).get("t") != "依然不是 JSON")

# 两次都解不出来时仍要抛 ParseFallbackError，而不是被缓存悄悄兜住。
f = _Fake("不是 JSON")
c = _client(f, cache_ttl_s=60.0)
try:
    c.chat_json([{"role": "user", "content": "永远坏"}])
    check("两次都解不出仍然抛错", False)
except ParseFallbackError:
    check("两次都解不出仍然抛错", True)

# ===========================================================================
print("== 5. 记账：命中时报出实测省下的 token ==")
# ===========================================================================
reset_llm_cache()
f = _Fake("答案", usage=SimpleNamespace(prompt_tokens=30, completion_tokens=12,
                                        total_tokens=42))
c = _client(f, cache_ttl_s=60.0)
m = [{"role": "user", "content": "记账问题"}]
c.chat(m)                       # 真调：花掉 30+12
_before = _saved()
c.chat(m)                       # 命中：省下 30+12
check("命中记了省下的 token（实测值，不是估的）", _saved() - _before == 42.0,
      f"{_before} -> {_saved()}")

# 端点不返回 usage 是常态，那时只记命中数、不往省量里编数。
reset_llm_cache()
f = _Fake("答案", usage=None)
f.usage = None
c = _client(f, cache_ttl_s=60.0)
m2 = [{"role": "user", "content": "没有用量的问题"}]
c.chat(m2)
_before = _saved()
c.chat(m2)
check("拿不到用量时不编造省量", _saved() == _before, f"{_before} -> {_saved()}")

# ===========================================================================
print("== 6. 降级：缓存层出任何事都不能影响调用结果 ==")
# ===========================================================================
reset_llm_cache()
import core.llm_cache as lc_module

_orig_get = lc_module.get_llm_cache


def _explode():
    raise RuntimeError("cache backend exploded")


lc_module.get_llm_cache = _explode   # type: ignore[assignment]
try:
    f = _Fake("照常返回")
    c = _client(f, cache_ttl_s=60.0)
    check("缓存层整体抛异常时调用照常成功", c.chat([{"role": "user", "content": "x"}])
          == "照常返回")
finally:
    lc_module.get_llm_cache = _orig_get   # type: ignore[assignment]

# ===========================================================================
print("== 7. 默认配置：哪些槽位默认开缓存 ==")
# ===========================================================================
# 这组把"为什么只有检索期六个槽位开"钉成断言。generation 尤其不能开：它的
# prompt 里带着检索结果，而 ACL 决定了检索得到什么——prompt 相同不代表
# **看得见它的人**相同，那一层的缓存必须由 core.query_cache 按租户+ACL+代次做。
_slots = LlmSlotsSettings()
for name in _RETRIEVAL_SLOTS:
    check(f"{name} 默认开缓存", getattr(_slots, name).cache_ttl_s > 0,
          str(getattr(_slots, name).cache_ttl_s))
for name in ("generation", "judge", "triplet", "contextual", "classifier"):
    check(f"{name} 默认不缓存", getattr(_slots, name).cache_ttl_s == 0.0,
          str(getattr(_slots, name).cache_ttl_s))

# ↓ 这几条才是真正的回归护栏。上面那组只证明了"配置里什么都不写时是对的"，
# 而实际部署恰恰不是那样：本项目 .env 把六个检索期槽位全指到了 DashScope。
# 一旦配置里出现了某个槽位，pydantic 会拿那份配置**重新构造**整个
# LlmSlotSettings，写在字段默认值上的 cache_ttl_s 随之落回 0.0——缓存代码
# 一行都不会执行，且不报任何错。这个 bug 真的发生过（实测六个槽位全是 0），
# 修法是把它变成模型校验器里的不变量而不是默认值。
_overridden = LlmSlotsSettings(rewrite=LlmSlotSettings(model="换个模型"))
check("配置里覆盖了某个槽位后，缓存仍然是开的（最容易静默失效的一步）",
      _overridden.rewrite.cache_ttl_s > 0, str(_overridden.rewrite.cache_ttl_s))
check("覆盖一个槽位不影响其它槽位", _overridden.hyde.cache_ttl_s > 0,
      str(_overridden.hyde.cache_ttl_s))

# 显式写 0 必须被尊重，否则"我就是要把这个槽位的缓存关掉"没法表达。
_off = LlmSlotsSettings(rewrite=LlmSlotSettings(cache_ttl_s=0.0))
check("显式写 0 能真的关掉", _off.rewrite.cache_ttl_s == 0.0, str(_off.rewrite.cache_ttl_s))
_explicit = LlmSlotsSettings(rewrite=LlmSlotSettings(cache_ttl_s=5.0))
check("显式写的值不被默认值覆盖", _explicit.rewrite.cache_ttl_s == 5.0,
      str(_explicit.rewrite.cache_ttl_s))

# 幂等：配置中心热更新会重新校验一遍，二次解析不能把已生效的值改掉。
_again = LlmSlotsSettings.model_validate(_off.model_dump())
check("二次校验后显式的 0 仍是 0（幂等）", _again.rewrite.cache_ttl_s == 0.0,
      str(_again.rewrite.cache_ttl_s))

# 真实配置（读 .env）：这是最终的接线断言——上面全绿但这条红，就说明缓存
# 在这台机器的实际部署里根本不会生效。
_real = get_settings().llm
_off_slots = [n for n in _RETRIEVAL_SLOTS if getattr(_real, n).cache_ttl_s <= 0]
check("真实配置下六个检索期槽位都开着缓存", not _off_slots, f"关着的: {_off_slots}")

# ===========================================================================
print("== 8. 有 Redis：跨副本命中，且键一律带 TTL ==")
# ===========================================================================
_use_redis(_REDIS_URL)
_client_r = redis_client.get_client()
try:
    _dbsize_before = _client_r.dbsize() if _client_r is not None else None
except Exception:  # noqa: BLE001
    _dbsize_before = None
if _client_r is None or not redis_client.is_configured():
    skip("跨副本命中", "Redis 不可达")
    skip("L2 键带 TTL", "Redis 不可达")
    skip("按槽位写 TTL", "Redis 不可达")
else:
    q = [{"role": "user", "content": f"跨副本-{uuid.uuid4().hex[:8]}"}]
    fa = _Fake("甲")
    ca = _client(fa, cache_ttl_s=120.0)
    ca.chat(q)
    reset_llm_cache()               # 「另一个副本」：L1 是空的，只剩 Redis
    fb = _Fake("乙")
    cb = _client(fb, cache_ttl_s=120.0)
    check("另一个副本读得到（跨副本 / 跨重启命中）", cb.chat(q) == "甲" and fb.calls == 0,
          f"calls={fb.calls}")

    keys = list(_client_r.scan_iter(match=f"{_PREFIX}llm:*", count=100))
    ttls = [_client_r.pttl(k) for k in keys]
    # 共用实例上 maxmemory=0（不淘汰），不带 TTL 的键只进不出，撑爆的是别人。
    check("L2 上的键都带 TTL", bool(ttls) and all(t and t > 0 for t in ttls), str(ttls[:3]))
    check("TTL 按槽位的 cache_ttl_s 写（120s 上界）",
          all(t <= 120_000 for t in ttls if t), str(ttls[:3]))

# ===========================================================================
print("== 清理 ==")
# ===========================================================================
removed = sweep(_PREFIX)
if removed < 0:
    skip("Redis 清理", "无连接，无从核对残留")
else:
    check(f"本次运行写入的键已清理（{removed} 条）", True)
    if _client_r is not None and _dbsize_before is not None:
        check("共用 db 的键总数未被波及",
              _client_r.dbsize() == _dbsize_before, f"before={_dbsize_before}")

print()
print(f"结果：{passed} passed, {failed} failed, {skipped} skipped")
sys.exit(1 if failed else 0)
