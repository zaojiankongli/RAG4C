"""两级缓存骨架：进程内 LRU（L1）+ Redis（L2）。

## 为什么抽出来

仓库里已经有两处几乎一模一样的 L1+L2 结构（:mod:`core.embed_cache` 与将要
加的 LLM 结果缓存），差别只在**值怎么变成字节**。再复制一遍就意味着以后每
修一个 bug 都要记得修三处——而"记得"从来不是一种机制。所以把不变的部分
（LRU 淘汰、回填、TTL、统计口径、降级）收在这里，变化的部分交给子类的三个
钩子：:meth:`_encode` / :meth:`_decode` / :meth:`_copy`。

:mod:`core.query_cache` **没有**并进来：它缓的是整条链路的产物，键里带语料
代次、还有 single-flight 合并与 SSE 事件重放，形状和职责都不一样，强行统一
只会让两边都别扭。

## 不变量

1. **Redis 是加速件，不是依赖件。** 所有出口在 Redis 缺席或出错时静默退回
   纯 L1，绝不抛异常——缓存层把一次成功的请求变成 500 是本末倒置。
2. **L2 上的键一律带 TTL。** 目标实例与其它应用共用且 ``maxmemory=0``
   （不淘汰），不带 TTL 的键只进不出，撑爆的是别人的服务。
3. **假值不写。** 空向量、空字符串多半是上游出错的产物，把它缓起来等于把
   一次偶发失败钉死一个 TTL。
4. **L1 出口给副本。** 调用方拿到的东西被就地改动是迟早的事，返回内部对象
   会让缓存悄悄变质。
"""
from __future__ import annotations

import os
import random
import threading
from collections import OrderedDict
from typing import Any, Generic, Optional, TypeVar

from core import redis_client as _redis
from core.observability import get_logger

_logger = get_logger(__name__)

T = TypeVar("T")


def env_float(name: str, default: float) -> float:
    """读环境变量里的浮点数；缺失 / 空串 / 写错都退回默认值。"""
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    """读环境变量里的整数；缺失 / 空串 / 写错都退回默认值。"""
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


#: TTL 抖动的默认幅度。与 ``core.query_cache`` 的默认值保持一致：两层缓存
#: 面对的是同一种同步过期，没有理由抖得不一样。
DEFAULT_TTL_JITTER = 0.15


def jittered_ttl(ttl: float, jitter: float = DEFAULT_TTL_JITTER) -> float:
    """给 TTL 加抖动，**只往短里抖，绝不延长**。

    要治的是同步过期。这一层尤其明显：一次查询会在**同一瞬间**把改写、HyDE、
    子查询、退一步、路由这几个槽位的结果一起写进去，它们带着一模一样的 TTL，
    于是也在同一秒集体过期——下一个同样的问题不是少命中一条，而是整套预处理
    全部落空，一次性打出五六个 LLM 调用。批量预热、服务重启后的第一波流量
    同理，只是规模更大：缓存把负载从"平摊"整形成了"周期性尖峰"，尖峰的高度
    恰好等于命中率。热点越明显，这一下越狠。

    抖动只减不增，因为两个方向的代价不对称：提前过期最多让某个值多算一次；
    延后过期是多发一次陈旧内容。这也让所有"TTL 是上界"的断言继续成立——
    配了 120 秒就绝不会活过 120 秒（冒烟里就是这么断言的）。

    这个实现由 :class:`TwoLevelCache` 与 :class:`core.query_cache.QueryCache`
    共用。抖动策略是个会被反复调的旋钮，两份拷贝迟早会调歪一份。
    """
    if jitter <= 0.0 or ttl <= 0.0:
        return ttl
    # TTL 抖动是统计学用途（打散过期时刻防雪崩），不是密钥生成——刻意不换 secrets。
    return ttl * (1.0 - random.random() * min(jitter, 0.9))


class TwoLevelCache(Generic[T]):
    """摘要键 -> 值的两级缓存。线程安全，所有出口不抛异常。

    子类负责：给出 ``namespace``（Redis 键名的第一段），并实现
    :meth:`_encode` / :meth:`_decode`（值 <-> 字节）与 :meth:`_copy`（L1 出口
    的防御性拷贝）。摘要怎么算由子类的领域方法决定，本类不关心。
    """

    #: Redis 键名的第一段，用于把不同用途的缓存分开。子类必须覆盖。
    namespace = "kv"

    def __init__(self, *, l1_max: int, l2_ttl_s: float, ttl_jitter: float | None = None) -> None:
        self.l1_max = max(1, l1_max)
        self.l2_ttl_s = l2_ttl_s
        # 抖动只作用在 L2。L1 压根没有时间维度（纯 LRU，靠容量淘汰），
        # 谈不上"同步过期"。
        self.ttl_jitter = (
            env_float("RAG4C_CACHE_TTL_JITTER", DEFAULT_TTL_JITTER)
            if ttl_jitter is None else ttl_jitter
        )
        self._lock = threading.Lock()
        self._l1: "OrderedDict[str, T]" = OrderedDict()
        self._c_l1_hits = 0
        self._c_l2_hits = 0
        self._c_misses = 0
        self._c_l2_errors = 0

    # -- 子类钩子 ------------------------------------------------------- #
    def _encode(self, value: T) -> bytes:
        raise NotImplementedError

    def _decode(self, raw: bytes) -> Optional[T]:
        """字节 -> 值；解不出来返回 None（当作未命中，绝不抛）。"""
        raise NotImplementedError

    def _copy(self, value: T) -> T:
        """L1 出口的防御性拷贝。不可变类型（如 str）可原样返回。"""
        return value

    # -- 读写 ----------------------------------------------------------- #
    def _rkey(self, digest: str) -> str:
        return _redis.key(self.namespace, digest)

    def l2_configured(self) -> bool:
        """配置上是否启用了 L2。纯配置检查、零 IO，命令闸门用这个。

        这里**也检查 ``redis.cache_on``**。从前只看 ``is_configured()``，于是
        关掉 ``cache_on`` 会停掉答案缓存的 L2（QueryCache 检查了这个开关），
        却停不掉 embedding / LLM 缓存的 L2——同一个开关对三个缓存里的两个不
        生效。想彻底关 Redis 的人只能去把 url 清空。
        """
        try:
            from config.settings import get_settings

            if not bool(getattr(get_settings().redis, "cache_on", True)):
                return False
        except Exception:  # 配置读不到时不该连缓存都不能用，退回只看 url
            pass
        try:
            return _redis.is_configured()
        except Exception:  # noqa: BLE001
            return False

    def l2_reachable(self) -> bool:
        """L2 此刻是否真的连得上（可能发一条 PING，只给状态上报用）。"""
        if not self.l2_configured():
            return False
        try:
            return _redis.is_reachable()
        except Exception:  # noqa: BLE001
            return False

    def l2_enabled(self) -> bool:
        """已弃用：改用 :meth:`l2_configured` / :meth:`l2_reachable`。"""
        return self.l2_configured()

    def get_digest(self, digest: str) -> Optional[T]:
        """按摘要查缓存。未命中返回 None。"""
        with self._lock:
            hit = self._l1.get(digest)
            if hit is not None:
                self._l1.move_to_end(digest)
                self._c_l1_hits += 1
                return self._copy(hit)

        value = self._l2_get(digest)
        if value is not None:
            with self._lock:
                self._c_l2_hits += 1
            self._l1_put(digest, value)   # 回填：下次不必再跑一趟网络
            return self._copy(value)

        with self._lock:
            self._c_misses += 1
        return None

    def put_digest(self, digest: str, value: T, *, ttl_s: float | None = None) -> None:
        """按摘要写缓存（两级同写）。假值不写。

        ``ttl_s`` 覆盖实例默认的 L2 存活时长。给它是因为有的缓存（LLM 槽位
        结果）里，TTL 是**每个写入方**的属性而不是缓存的属性：六个槽位共用
        一份 L1 容量预算，但各自的 ``cache_ttl_s`` 可以不同。
        """
        if not value:
            return
        self._l1_put(digest, self._copy(value))
        self._l2_put(digest, value, ttl_s)

    def drop_digest(self, digest: str) -> None:
        """作废一条（两级同删）。

        给"缓下来的值事后被判定为不可用"这种情形用（例如缓存里的文本解不出
        JSON）。不做也能跑，但那条坏值会黏住整个 TTL，把一次偶发失败放大成
        持续一个 TTL 的双倍调用。
        """
        with self._lock:
            self._l1.pop(digest, None)
        if not self.l2_configured():
            return
        # UNLINK 而不是 DEL：回收在后台线程做，不阻塞共用实例的主线程。
        self._exec("unlink", self._rkey(digest))

    # -- 内部 ----------------------------------------------------------- #
    def _l1_put(self, digest: str, value: T) -> None:
        with self._lock:
            self._l1[digest] = value
            self._l1.move_to_end(digest)
            while len(self._l1) > self.l1_max:
                self._l1.popitem(last=False)

    def _exec(self, fn_name: str, *args: Any, **kwargs: Any) -> tuple[bool, Any]:
        """执行一条 L2 命令，返回 ``(是否成功, 返回值)``，失败计入 ``l2_errors``。

        这里刻意**不用** :func:`core.redis_client.call`。它把任何异常吞成
        ``None``，于是外面那层 ``except`` 永远等不到——本类的 ``l2_errors``
        计数器因此恒为 0（写这段的人以为自己在计数，实测一次都没涨过）。
        后果不是少一个指标：``l2_errors`` 是"Redis 出过问题"在指标里**仅有**
        的痕迹，抹掉它，embedding / LLM 缓存在 Redis 宕机期间就完全静默——
        命中率掉下去，而看板上没有任何一格变色。

        与 :meth:`core.query_cache.QueryCache._exec` 保持同一形状：两处都要
        区分"命令失败"与"键不存在"，没有理由长得不一样。
        """
        client = _redis.get_client()
        if client is None:
            # 连都没连上（未配置 / 缺依赖 / 建连失败）。这不算命令失败，
            # 由 l2_reachable() 负责报告，不污染 l2_errors 的口径。
            return False, None
        try:
            return True, getattr(client, fn_name)(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - L2 出错等于没有 L2
            with self._lock:
                self._c_l2_errors += 1
            _logger.debug("%s 缓存 L2 命令 %s 失败（已降级为纯 L1）：%s",
                          self.namespace, fn_name, exc)
            return False, None

    def _l2_get(self, digest: str) -> Optional[T]:
        if not self.l2_configured():
            return None
        ok, raw = self._exec("get", self._rkey(digest))
        if not ok or raw is None:
            return None
        if isinstance(raw, str):     # decode_responses=True 的客户端（不该出现）
            return None
        return self._decode(raw)

    def _l2_put(self, digest: str, value: T, ttl_s: float | None = None) -> None:
        if not self.l2_configured():
            return
        ttl = self.l2_ttl_s if ttl_s is None else ttl_s
        ttl = jittered_ttl(ttl, self.ttl_jitter)
        self._exec(
            "set", self._rkey(digest), self._encode(value),
            px=max(1, int(ttl * 1000)),
        )

    # -- 运维 ----------------------------------------------------------- #
    def clear_l1(self) -> None:
        """只清进程内那一层（测试用；L2 在共用实例上，不提供批量删除）。"""
        with self._lock:
            self._l1.clear()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            l1_hits, l2_hits, misses = self._c_l1_hits, self._c_l2_hits, self._c_misses
            snapshot: dict[str, Any] = {
                "size": len(self._l1),
                "max": self.l1_max,
                "l1_hits": l1_hits,
                "l2_hits": l2_hits,
                "hits": l1_hits + l2_hits,
                "misses": misses,
                "l2_errors": self._c_l2_errors,
                "l2_ttl_s": self.l2_ttl_s,
                "ttl_jitter": self.ttl_jitter,
            }
        total = snapshot["hits"] + misses
        snapshot["hit_rate"] = round(snapshot["hits"] / total, 4) if total else 0.0
        # 两个信号分开报，理由同 core/query_cache.py 的 stats()
        snapshot["l2_configured"] = self.l2_configured()
        snapshot["l2_reachable"] = self.l2_reachable()
        snapshot["l2_enabled"] = snapshot["l2_configured"]  # 兼容既有消费方
        return snapshot


__all__ = ["TwoLevelCache", "env_float", "env_int", "jittered_ttl", "DEFAULT_TTL_JITTER"]
