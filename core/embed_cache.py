"""查询向量缓存（L1 进程内 LRU + L2 Redis）。

## 为什么单独做一层，而不是塞进 core.query_cache

答案缓存缓的是"整条 RAG 链路的产物"，键里拌了租户、ACL、知识库和**语料代次**
——语料一变就得整批作废。向量不是这样的东西：``embed(model, text)`` 是个纯
函数，语料入库一万次也不会改变"这句话的向量是什么"。把它塞进答案缓存那套键，
每次入库都会把辛苦攒下的向量全部作废，白白重算——这是个看着谨慎、实则纯亏的
做法，所以这里刻意不引入代次。

## 命中的是什么

一次问答里同一段文本会被嵌**两遍**：路由器嵌一次 ``search_query`` 决定路线
（``retrieval/router.py``），主检索紧接着又嵌同一个字符串（``retrieval/
pipeline.py``）。进程内 LRU 已经能吃掉这一对（原来就在 ``ApiEmbedder`` 里）。
这一层往下再垫 Redis，多吃两类：

1. **跨副本**：另一个副本上有人问过同一句，本副本直接拿现成向量；
2. **跨重启**：进程重启后 L1 是空的，热点问句不必重新掏钱嵌一遍。

## 键里必须有模型名与变体

不同模型的向量在不同的空间里。拿 A 模型的向量去查 B 模型建的索引，**不会
报错**，只会让召回悄悄变成噪声——这是最难被发现的一类故障。所以模型名进键。
``variant`` 同理：归一化开关、max_length 这些参数会改变同一模型对同一文本的
输出，它们也必须进键。

## 只缓单条查询，不缓批量入库

``embed_texts`` 在入库时一篇文档就是上千个 chunk，全写进 Redis 会把一个**与
其它应用共用**的实例撑爆，而这些向量马上就要进 Milvus、之后再不会以文本为
键被查第二次。所以本模块只服务 ``embed_query``。

## 降级

两级缓存的骨架（LRU、回填、TTL、降级、统计）在 :mod:`core.two_level_cache`。
与本仓库其它 Redis 用法一致：**加速件，不是依赖件**，所有出口在 Redis 缺席
或出错时静默退回纯 L1，绝不抛异常。
"""
from __future__ import annotations

import hashlib
import struct
import threading
from typing import Optional

from core.two_level_cache import TwoLevelCache, env_float, env_int

#: 进程内 LRU 条数上限。一条 1024 维 float32 约 4KB，512 条约 2MB。
_L1_MAX = 512

#: L2 存活时长。向量本身永不过期（纯函数），TTL 在这里只承担两个职责：
#: 回收再也不会被问到的冷门问句，以及给"模型换了但名字没换"这种意外一个
#: 自愈期限。默认 24 小时而不是一周，是因为目标 Redis 与其它应用共用且
#: ``maxmemory=0``（不淘汰）——写进去的东西不会被自动挤掉，撑爆内存伤的是
#: 别人的服务。等 maxmemory + volatile-lru 配好之后，这个值可以放长。
_L2_TTL_S = 86400.0


def _digest(model: str, variant: str, text: str) -> str:
    """(模型, 变体, 文本) -> Redis 键名分量。

    用 ``\\x00`` 分隔而不是直接拼接：``("m1", "ab")`` 与 ``("m1a", "b")``
    拼出来是同一个串，而 ``\\x00`` 不可能出现在模型名里，分隔就永远无歧义。
    不截断摘要——省下的几十字节换来的是碰撞，而碰撞在这里的表现是"拿到别的
    句子的向量"，召回悄悄变差且无从定位。
    """
    raw = "\x00".join((model, variant, text)).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _pack(vec: list[float]) -> bytes:
    """向量 -> 小端 float32 字节串。

    用 float32 不是 float64：模型本身就以 float32 输出，Milvus 的
    FLOAT_VECTOR 也是 float32，多出来的那一半精度在这条链路上没有任何读者，
    只是把体积和网络往返翻倍。显式小端（``<``）而不是 ``array.tobytes()``
    的本机序：同一个 Redis 可能被不同架构的副本共用，本机序会让其中一边
    读出一堆合法但完全错误的浮点数——不报错，只是召回崩掉。
    """
    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack(raw: bytes) -> Optional[list[float]]:
    """字节串 -> 向量；长度不是 4 的整数倍或为空时返回 None（当作未命中）。"""
    if not raw or len(raw) % 4 != 0:
        return None
    return list(struct.unpack(f"<{len(raw) // 4}f", raw))


class EmbedCache(TwoLevelCache[list]):
    """查询向量的两级缓存。线程安全，所有出口不抛异常。"""

    namespace = "emb"

    def __init__(
        self,
        *,
        l1_max: int | None = None,
        l2_ttl_s: float | None = None,
        ttl_jitter: float | None = None,
    ) -> None:
        super().__init__(
            l1_max=l1_max if l1_max is not None else env_int("RAG4C_EMBED_CACHE_MAX", _L1_MAX),
            l2_ttl_s=(
                l2_ttl_s if l2_ttl_s is not None
                else env_float("RAG4C_EMBED_CACHE_TTL_S", _L2_TTL_S)
            ),
            ttl_jitter=ttl_jitter,
        )

    # -- 骨架钩子 ------------------------------------------------------- #
    def _encode(self, value: list) -> bytes:
        return _pack(value)

    def _decode(self, raw: bytes) -> Optional[list]:
        return _unpack(raw)

    def _copy(self, value: list) -> list:
        return list(value)

    # -- 领域接口 ------------------------------------------------------- #
    def get(self, model: str, text: str, *, variant: str = "") -> Optional[list[float]]:
        """查缓存。未命中返回 None。"""
        return self.get_digest(_digest(model, variant, text))

    def put(self, model: str, text: str, vec: list[float], *, variant: str = "") -> None:
        """写缓存（两级同写）。空向量不写——那多半是上游出错的产物。"""
        self.put_digest(_digest(model, variant, text), list(vec))


_instance: Optional[EmbedCache] = None
_instance_lock = threading.Lock()


def get_embed_cache() -> EmbedCache:
    """进程内单例。

    单例而不是挂在 embedder 实例上：配置热更新会重建 embedder，挂在实例上
    等于每次热更新都清空缓存。而键里已经有模型名与变体，换了模型自然换键，
    不存在"拿旧模型的向量喂新模型"的风险——那正是把模型名放进键的理由。
    """
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = EmbedCache()
    return _instance


def reset_embed_cache() -> None:
    """测试钩子：丢弃单例（不碰 Redis）。"""
    global _instance
    with _instance_lock:
        _instance = None


__all__ = ["EmbedCache", "get_embed_cache", "reset_embed_cache"]
