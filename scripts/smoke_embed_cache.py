"""查询向量缓存（core/embed_cache.py）冒烟。

运行：python scripts/smoke_embed_cache.py
覆盖：
1. 编解码：小端 float32 往返、残缺字节当作未命中
2. 键的隔离：模型名、变体、文本任一不同就是不同的键（串味 = 召回静默变差）
3. L1 行为：命中 / LRU 淘汰 / 统计口径
4. 接线：ApiEmbedder.embed_query 真的只嵌一次（第二次走缓存）
5. 有 Redis：跨实例（= 跨副本、跨重启）命中，且 L2 命中会回填 L1
6. Redis 挂掉：静默退回纯 L1，不抛异常

Redis 不可达时第 5 组打印 [SKIP]：与本仓库其它缓存一样，Redis 是加速件不是
依赖件，没有它的形态是被支持的，不该判红。

清理：只写 ``rag4c-test:embed-cache-<pid>-<rand>:`` 前缀下的键，退出时 sweep，
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

# 用**本进程独有**的前缀，而不是 isolate_redis_keyspace()。后者在 run_tests.py
# 统一设好前缀时会原样沿用整轮共用的 rag4c-test:run:——那正是本脚本不能要的：
# 下面收尾时要 sweep 自己的键、并断言共用 db 的 dbsize 没变，若前缀是全轮共用
# 的，这一 sweep 会把同轮**其它脚本**还在用的键一并删掉，dbsize 也跟着对不上。
# 前缀仍挂在 TEST_PREFIX_ROOT 之下，sweep() 的护栏（只许删测试前缀）照常生效。
_PREFIX = f"{TEST_PREFIX_ROOT}embed-cache-{os.getpid()}-{uuid.uuid4().hex[:8]}:"
os.environ["RAG4C_REDIS_KEY_PREFIX"] = _PREFIX
atexit.register(sweep, _PREFIX)

# 必须早于 config.settings 的导入：get_settings 是 lru_cache 单例。
_REDIS_URL = os.environ.get("RAG4C_SMOKE_REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ["RAG4C_REDIS_URL"] = ""          # 第一阶段：无 Redis

from types import SimpleNamespace

from config.settings import get_settings
from core import redis_client
from core.embed_cache import (
    EmbedCache,
    _digest,
    _pack,
    _unpack,
    get_embed_cache,
    reset_embed_cache,
)
from core.embedding import ApiEmbedder

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
    reset_embed_cache()


# ===========================================================================
print("== 1. 编解码：小端 float32 往返 ==")
# ===========================================================================
_vec = [0.5, -0.25, 1.0, 0.0]
_raw = _pack(_vec)
check("每维 4 字节（float32，不是 float64）", len(_raw) == 16, str(len(_raw)))
check("往返无损（这几个值在 float32 里可精确表示）", _unpack(_raw) == _vec, str(_unpack(_raw)))
# 字节被截断（Redis 值被别的东西覆盖过、版本回滚）时必须当未命中处理，
# 而不是解出一个维度错误的向量——后者会被原样送进 Milvus 检索。
check("残缺字节当作未命中", _unpack(_raw[:-1]) is None)
check("空值当作未命中", _unpack(b"") is None)

# ===========================================================================
print("== 2. 键的隔离：模型 / 变体 / 文本任一不同即不同键 ==")
# ===========================================================================
# 这组是本模块最要命的地方：拿 A 模型的向量去查 B 模型建的索引**不会报错**，
# 只会让召回悄悄退化成噪声。所以隔离必须有断言看着。
_use_redis("")
c = EmbedCache()
c.put("model-a", "同一句话", [1.0, 2.0])
check("同模型同文本命中", c.get("model-a", "同一句话") == [1.0, 2.0])
check("换模型不命中", c.get("model-b", "同一句话") is None)
check("换变体不命中", c.get("model-a", "同一句话", variant="norm=1") is None)
check("换文本不命中", c.get("model-a", "另一句话") is None)
# 分隔符：("m1","ab") 与 ("m1a","b") 直接拼接会撞成同一个键。
c.put("m1", "x", [1.0], variant="ab")
check("模型名与变体的边界不含糊", c.get("m1a", "x", variant="b") is None)

# ===========================================================================
print("== 3. L1 行为与统计口径 ==")
# ===========================================================================
c = EmbedCache(l1_max=2)
c.put("m", "a", [1.0])
c.put("m", "b", [2.0])
c.put("m", "c", [3.0])
check("超出容量后最旧的被淘汰", c.get("m", "a") is None)
check("新的还在", c.get("m", "c") == [3.0])
st = c.stats()
check("stats 里有 hit_rate 且分母含未命中", 0.0 < st["hit_rate"] < 1.0, str(st))
check("空向量不写入（多半是上游出错的产物）",
      (c.put("m", "empty", []), c.get("m", "empty"))[1] is None)
check("返回的是副本，改它不会污染缓存",
      (lambda v: (v.append(9.0), c.get("m", "c") == [3.0])[1])(c.get("m", "c")))


class _FakeEmbeddings:
    """最小 /embeddings 桩，记录被调了几次。"""

    def __init__(self) -> None:
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        n = len(kwargs.get("input") or [])
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.125, 0.25, 0.375]) for _ in range(n)]
        )


def _embedder(model: str = "fake-embed") -> tuple[ApiEmbedder, _FakeEmbeddings]:
    fake = _FakeEmbeddings()
    e = ApiEmbedder(model=model, base_url="http://fake")
    e._client = SimpleNamespace(embeddings=fake)
    return e, fake


# ===========================================================================
print("== 4. 接线：embed_query 第二次不再走远程 ==")
# ===========================================================================
# 缓存建好却没接上是这个仓库刚踩过的坑（SSE 那条路径命中率曾恒为 0）。
_use_redis("")
_q = f"向量缓存冒烟-{uuid.uuid4().hex[:8]}"
emb, fake = _embedder()
v1 = emb.embed_query(_q)
v2 = emb.embed_query(_q)
check("两次返回同一向量", v1 == v2 == [0.125, 0.25, 0.375], str(v1))
check("远程只被调了一次（第二次命中缓存）", fake.calls == 1, f"calls={fake.calls}")

# 批量入库路径**不**该进缓存：一篇文档上千个 chunk 全写 Redis 会撑爆共用实例，
# 而这些向量马上进 Milvus，之后再不会以文本为键被查。
before = get_embed_cache().stats()["size"]
emb.embed_texts([f"chunk-{i}" for i in range(5)])
check("embed_texts 不写向量缓存", get_embed_cache().stats()["size"] == before,
      f"{before} -> {get_embed_cache().stats()['size']}")

# ===========================================================================
print("== 5. 有 Redis：跨实例（跨副本 / 跨重启）命中 ==")
# ===========================================================================
_use_redis(_REDIS_URL)
_client = redis_client.get_client()
try:
    _dbsize_before = _client.dbsize() if _client is not None else None
except Exception:  # noqa: BLE001 - 拿不到基线就不做这条核对，不影响其余断言
    _dbsize_before = None
if _client is None or not redis_client.is_configured():
    skip("跨实例命中", "Redis 不可达")
    skip("L2 命中回填 L1", "Redis 不可达")
    skip("Redis 挂掉时静默退回纯 L1", "Redis 不可达")
else:
    text = f"跨副本问句-{uuid.uuid4().hex[:8]}"
    a = EmbedCache()            # 「副本 A」
    b = EmbedCache()            # 「副本 B」：独立 L1，共享同一个 Redis
    a.put("m-x", text, [0.5, -1.5, 2.25])
    got = b.get("m-x", text)
    check("另一个实例读得到（跨副本 / 跨重启命中）", got == [0.5, -1.5, 2.25], str(got))
    check("这一命中记在 l2_hits 上", b.stats()["l2_hits"] == 1, str(b.stats()))
    check("L2 命中被回填进 L1", b.stats()["size"] == 1, str(b.stats()))
    b.get("m-x", text)
    check("回填之后再问走的是 L1", b.stats()["l1_hits"] == 1, str(b.stats()))

    # 向量键必须带 TTL：共用实例上 maxmemory=0（不淘汰），不带 TTL 的键
    # 只进不出，撑爆的是别人的服务。
    ttls = [_client.pttl(k) for k in _client.scan_iter(match=f"{_PREFIX}emb:*", count=100)]
    check("L2 上的向量键都带 TTL", bool(ttls) and all(t and t > 0 for t in ttls), str(ttls[:3]))

    # -- TTL 抖动 ---------------------------------------------------------
    # 治的是同步过期：一批向量往往在同一瞬间被写进去（一次批量提问、重启后
    # 的第一波流量），带着一模一样的 TTL，于是也在同一秒集体过期，把负载从
    # 平摊整形成周期性尖峰。抖动只往短里抖，所以"TTL 是上界"仍然成立。
    j = EmbedCache(l2_ttl_s=600.0)
    _jtag = uuid.uuid4().hex[:8]
    _jtexts = [f"{_jtag}-{i}" for i in range(24)]
    for i, t in enumerate(_jtexts):
        j.put("m-jitter", t, [float(i) + 0.5])
    # 精确取这 24 条自己写的键，不要 scan `emb:*`：同前缀下还躺着上面几节用
    # 默认 86400s TTL 写的向量，扫进来会让"600s 上界"这条断言量错对象——
    # 第一版就是这么假失败的。
    jttls = [_client.pttl(j._rkey(_digest("m-jitter", "", t))) for t in _jtexts]
    jttls = [t for t in jttls if t and t > 0]
    check("这 24 条键都写进去了", len(jttls) == 24, str(len(jttls)))
    check("抖动后 TTL 仍是上界（绝不超过配置值）",
          all(t <= 600_000 for t in jttls), f"max={max(jttls) if jttls else None}")
    # 24 条同一瞬间写入的键若 TTL 全部相同，说明抖动根本没生效。允许少量
    # 重复（毫秒取整会碰撞），但不该只有一两个不同值。
    check("同批写入的键 TTL 被打散（抖动确实生效）", len(set(jttls)) > 5,
          f"{len(set(jttls))} 个不同值 / 共 {len(jttls)} 条")
    check("抖动幅度受控（不会把 600s 抖成几乎立刻过期）",
          all(t >= 600_000 * 0.85 - 5_000 for t in jttls),
          f"min={min(jttls) if jttls else None}")

    # 关掉抖动要能拿回精确 TTL：有些场景（对齐外部过期、调试）需要确定性。
    z = EmbedCache(l2_ttl_s=600.0, ttl_jitter=0.0)
    z.put("m-nojitter", f"{_jtag}-exact", [1.0])
    ztt = _client.pttl(z._rkey(_digest("m-nojitter", "", f"{_jtag}-exact")))
    check("ttl_jitter=0 时 TTL 精确（不抖）", ztt is not None and 599_000 <= ztt <= 600_000,
          str(ztt))

    # Redis 中途不可用：静默退回纯 L1，不抛。
    # 故障注入点在 get_client 而不是 call。原因有二：
    #   1) 真实的故障就长这样——连接对象还在，命令开始抛；
    #   2) call() 自己就把异常吞成 None，打在它身上，两级缓存永远看不到异常，
    #      于是 l2_errors 断言实际上无论如何都不成立。这条断言从前之所以过，
    #      只是因为骨架当时也经 call 走；骨架改成直连之后它立刻失灵了——注释
    #      里那句"打源头更不容易失灵"正好说反了。
    original = redis_client.get_client

    class _DeadClient:
        def __getattr__(self, _name):
            def _boom(*a, **kw):
                raise RuntimeError("connection lost")

            return _boom

    redis_client.get_client = lambda: _DeadClient()   # type: ignore[assignment]
    try:
        d = EmbedCache()
        d.put("m-x", "断线期间", [1.0])         # 写 L2 失败，L1 仍要写成功
        check("Redis 挂掉时静默退回纯 L1",
              d.get("m-x", "断线期间") == [1.0] and d.stats()["l2_errors"] >= 1,
              str(d.stats()))
    finally:
        redis_client.get_client = original    # type: ignore[assignment]

# ===========================================================================
print("== 清理 ==")
# ===========================================================================
removed = sweep(_PREFIX)
if removed < 0:
    skip("Redis 清理", "无连接，无从核对残留")
else:
    check(f"本次运行写入的键已清理（{removed} 条）", True)
    if _client is not None and _dbsize_before is not None:
        check("共用 db 的键总数未被波及",
              _client.dbsize() == _dbsize_before, f"before={_dbsize_before}")

print()
print(f"结果：{passed} passed, {failed} failed, {skipped} skipped")
sys.exit(1 if failed else 0)
