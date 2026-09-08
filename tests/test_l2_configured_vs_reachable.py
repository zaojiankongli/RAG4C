"""L2 的两个信号：**配置上开了** 与 **此刻连得上**。

## 这些测试在防什么

从前只有一个 `l2_enabled()`，它读起来像"能用"，实际只检查了 url 非空。于是
最该被看见的那个状态——**配了 Redis，但 Redis 挂了**——恰好伪装成一切正常：

- `/api/metrics` 里 `l2_enabled` 恒为 True；
- 每个请求都在静默 miss，答案照出，只是慢；
- 唯一的痕迹是 `l2_errors` 单调上涨，而没人盯着一个只涨不跌的计数器。

所以本文件的核心断言不是"两个方法各自返回对了"，而是**它们在宕机时必须
分开**：`l2_configured` 保持 True（配置没变），`l2_reachable` 变成 False。
任何把两者合成一个的改动，都会让 `test_outage_*` 变红。

另一半是性能与语义的分工：闸门（每条命令前的判断）必须零 IO，状态上报才
允许发 PING；且探活结果对**成功与失败一视同仁**地缓存——否则 Redis 一挂，
抓一次指标要吃三个 socket 超时，宕机期间的探活成本反而比正常时高。
"""
from __future__ import annotations

import pytest

from config.settings import get_settings
from core import redis_client
from core.query_cache import CacheConfig, QueryCache
from core.two_level_cache import TwoLevelCache


class _StrCache(TwoLevelCache[str]):
    """最小可用子类，只为触达基类的 L2 分支。"""

    namespace = "test-l2"

    def _encode(self, value: str) -> bytes:
        return value.encode("utf-8")

    def _decode(self, raw: bytes) -> str | None:
        return raw.decode("utf-8", "ignore") or None


class _DeadClient:
    """任何命令都抛的假客户端（模拟连接在飞行中断开）。"""

    def __init__(self) -> None:
        self.calls = 0

    def __getattr__(self, _name: str):
        def _boom(*_a: object, **_kw: object):
            self.calls += 1
            raise ConnectionError("模拟 Redis 断线")

        return _boom


class _LiveClient:
    """PING 得通的假客户端；记录被问了多少次，用来验探活缓存。"""

    def __init__(self) -> None:
        self.pings = 0

    def ping(self) -> bool:
        self.pings += 1
        return True

    def get(self, *_a: object, **_kw: object) -> None:
        return None

    def set(self, *_a: object, **_kw: object) -> bool:
        return True


@pytest.fixture()
def redis_env(monkeypatch):
    """把 Redis 配成"已配置"，并保证每个用例从干净的探活缓存开始。

    不连真 Redis：这些性质说的是**信号怎么分开**，与真实实例无关，挂在真
    Redis 上反而会让"宕机"这一半没法稳定复现。
    """
    monkeypatch.setenv("RAG4C_REDIS_URL", "redis://127.0.0.1:6379/0")
    get_settings.cache_clear()
    redis_client.reset_client()
    yield
    get_settings.cache_clear()
    redis_client.reset_client()


def _install(monkeypatch, client) -> None:
    """替换连接工厂并清掉探活缓存（否则读到上一个用例的结论）。"""
    monkeypatch.setattr(redis_client, "get_client", lambda: client)
    redis_client._reachable_cache = None  # noqa: SLF001


# --------------------------------------------------------------------- #
# 核心：宕机时两个信号必须分开
# --------------------------------------------------------------------- #
def test_outage_query_cache_configured_stays_true_reachable_goes_false(redis_env, monkeypatch):
    cache = QueryCache(CacheConfig.from_settings())
    _install(monkeypatch, _LiveClient())
    assert cache.l2_configured() is True
    assert cache.l2_reachable() is True

    _install(monkeypatch, _DeadClient())
    # 配置一个字没改，所以 configured 不该动；变的是"连得上"。
    assert cache.l2_configured() is True
    assert cache.l2_reachable() is False


def test_outage_two_level_cache_configured_stays_true_reachable_goes_false(redis_env, monkeypatch):
    cache = _StrCache(l1_max=8, l2_ttl_s=60.0)
    _install(monkeypatch, _LiveClient())
    assert (cache.l2_configured(), cache.l2_reachable()) == (True, True)

    _install(monkeypatch, _DeadClient())
    assert (cache.l2_configured(), cache.l2_reachable()) == (True, False)


def test_outage_shows_up_in_stats(redis_env, monkeypatch):
    """看板上必须看得出区别——这正是从前缺的那一眼。"""
    cache = _StrCache(l1_max=8, l2_ttl_s=60.0)
    _install(monkeypatch, _DeadClient())
    snap = cache.stats()
    assert snap["l2_configured"] is True
    assert snap["l2_reachable"] is False


def test_stats_keeps_l2_enabled_meaning_configured(redis_env, monkeypatch):
    """兼容字段的语义不能在一次升级里静默翻转。

    `l2_enabled` 还有四五个脚本消费方。把它改成 reachable，会让"配了但连不
    上"从 True 变 False——正确，但是**悄悄地**正确，那些脚本的判断分支会在
    没人改动它们的情况下换一条走。要改就显式改调用方（已改成 l2_reachable
    的那几处），不要在字段底下动手脚。
    """
    for cache in (_StrCache(l1_max=8, l2_ttl_s=60.0), QueryCache(CacheConfig.from_settings())):
        _install(monkeypatch, _DeadClient())
        snap = cache.stats()
        assert snap["l2_enabled"] == snap["l2_configured"] is True
        assert snap["l2_reachable"] is False


# --------------------------------------------------------------------- #
# 分工：闸门零 IO，探活才发 PING
# --------------------------------------------------------------------- #
def test_configured_never_touches_the_network(redis_env, monkeypatch):
    """`l2_configured` 每条命令前都会被问一次，绝不能有网络成本。"""
    live = _LiveClient()
    _install(monkeypatch, live)
    cache = _StrCache(l1_max=8, l2_ttl_s=60.0)
    for _ in range(50):
        assert cache.l2_configured() is True
    assert live.pings == 0


def test_reachable_probe_is_cached_for_both_outcomes(redis_env, monkeypatch):
    """成功与失败都缓存。负缓存尤其重要：宕机期间探活不该比正常时更贵。"""
    live = _LiveClient()
    _install(monkeypatch, live)
    for _ in range(10):
        assert redis_client.is_reachable() is True
    assert live.pings == 1, "探活没缓存：一次抓指标会被放大成 N 次 socket 超时"

    dead = _DeadClient()
    _install(monkeypatch, dead)          # _install 会清缓存
    for _ in range(10):
        assert redis_client.is_reachable() is False
    assert dead.calls == 1, "失败没被缓存——恰恰是 Redis 挂着时最贵的那种情况"


def test_reachable_probe_can_be_forced(redis_env, monkeypatch):
    live = _LiveClient()
    _install(monkeypatch, live)
    redis_client.is_reachable()
    redis_client.is_reachable(max_age_s=0)   # 强制重新探
    assert live.pings == 2


def test_reset_client_clears_the_probe_cache(redis_env, monkeypatch):
    """换了 url 还拿旧探活结果，会出现"连的是 A、报的是 B 的健康"这种错位。"""
    _install(monkeypatch, _LiveClient())
    assert redis_client.is_reachable() is True
    monkeypatch.setattr(redis_client, "get_client", lambda: _DeadClient())
    assert redis_client.is_reachable() is True, "TTL 内应命中缓存（前置条件）"
    redis_client.reset_client()
    assert redis_client.is_reachable() is False


# --------------------------------------------------------------------- #
# 闸门用 configured 的后果：宕机时命令照发、照记错，而不是被静默关掉
# --------------------------------------------------------------------- #
def test_outage_still_attempts_and_counts_errors(redis_env, monkeypatch):
    """闸门若改用 reachable，L2 会在宕机时被"关掉"，`l2_errors` 从此不再涨——
    那个计数器是唯一能反推 Redis 出过问题的痕迹，不能被优化掉。"""
    dead = _DeadClient()
    _install(monkeypatch, dead)
    cache = _StrCache(l1_max=8, l2_ttl_s=60.0)
    assert cache.get_digest("nope") is None       # 不抛
    cache.put_digest("k", "v")                    # 不抛
    stats = cache.stats()
    assert stats["l2_errors"] >= 2, stats
    assert dead.calls >= 2, "命令根本没发出去，错误计数就成了摆设"


def test_l1_still_serves_during_outage(redis_env, monkeypatch):
    """Redis 是加速件不是依赖件：挂了只该少一层，不该少功能。"""
    _install(monkeypatch, _DeadClient())
    cache = _StrCache(l1_max=8, l2_ttl_s=60.0)
    cache.put_digest("k", "v")
    assert cache.get_digest("k") == "v"


# --------------------------------------------------------------------- #
# 顺带修好的分歧：cache_on 从前只对三个缓存里的一个生效
# --------------------------------------------------------------------- #
def test_cache_on_off_disables_l2_for_two_level_cache(redis_env, monkeypatch):
    """`redis.cache_on=false` 从前停得掉答案缓存的 L2（QueryCache 查了这个
    开关），却停不掉 embedding / LLM 缓存的 L2——同一个开关对三个缓存里的两
    个不生效，想彻底关掉的人只能去把 url 清空。"""
    monkeypatch.setenv("RAG4C_REDIS_CACHE_ON", "false")
    get_settings.cache_clear()
    live = _LiveClient()
    _install(monkeypatch, live)

    two_level = _StrCache(l1_max=8, l2_ttl_s=60.0)
    query = QueryCache(CacheConfig.from_settings())
    assert two_level.l2_configured() is False
    assert query.l2_configured() is False, "答案缓存本来就该关掉（回归保护）"
    assert two_level.l2_reachable() is False


def test_unconfigured_reports_both_false_without_probing(redis_env, monkeypatch):
    monkeypatch.setenv("RAG4C_REDIS_URL", "")
    get_settings.cache_clear()
    live = _LiveClient()
    _install(monkeypatch, live)
    cache = _StrCache(l1_max=8, l2_ttl_s=60.0)
    assert cache.l2_configured() is False
    assert cache.l2_reachable() is False
    assert live.pings == 0, "没配 url 还去发 PING，等于给未启用的功能付超时成本"


def test_deprecated_alias_matches_configured(redis_env, monkeypatch):
    _install(monkeypatch, _DeadClient())
    for cache in (_StrCache(l1_max=8, l2_ttl_s=60.0), QueryCache(CacheConfig.from_settings())):
        assert cache.l2_enabled() == cache.l2_configured()


# --------------------------------------------------------------------- #
# 报警面：两个 gauge 才写得出"配了但连不上"这条表达式
# --------------------------------------------------------------------- #
def test_prometheus_exports_both_l2_signals(redis_env, monkeypatch):
    """时序指标回答不了"L2 还在不在"——答案照出，只是每个请求静默 miss。

    真正该报警的是 `l2_configured == 1 and l2_reachable == 0`，少任何一个
    gauge 这条表达式都写不出来。
    """
    from server.app import _prometheus_health_lines

    _install(monkeypatch, _DeadClient())
    lines = _prometheus_health_lines()
    assert 'rag4c_cache_l2_configured{cache="answer"} 1' in lines
    assert 'rag4c_cache_l2_reachable{cache="answer"} 0' in lines
    assert "rag4c_redis_connected 0" in lines
    assert "rag4c_redis_configured 1" in lines


def test_prometheus_health_never_raises(redis_env, monkeypatch):
    """监控出口把自己搞挂，等于在最需要它的时候瞎掉。"""
    from server import app as app_module

    def _boom() -> dict:
        raise RuntimeError("统计源炸了")

    monkeypatch.setattr(app_module, "_cache_stats", _boom)
    monkeypatch.setattr(app_module, "_embed_cache_stats", lambda: {"error": "无法导入"})
    _install(monkeypatch, _LiveClient())
    lines = app_module._prometheus_health_lines()
    assert not any('cache="answer"' in line for line in lines)
    assert not any('cache="embed"' in line for line in lines)
    assert any('cache="llm"' in line for line in lines), "一个源坏掉不该带走其余两个"
