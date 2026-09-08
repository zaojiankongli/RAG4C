"""语料代次（epoch）：答案缓存按租户失效的唯一可行手段。

## 为什么不能靠"删键"

L2 的 Redis 键名是 ``sha256(逻辑键)``（见 :func:`core.query_cache._digest`），
**不可逆**：拿着 ``tenant=acme`` 反推不出该租户写过哪些键。按前缀扫也不行，
键名里没有租户信息——这是故意的，问句和租户不该以明文躺在 MONITOR / SLOWLOG
/ RDB 里。再加上目标 Redis 与其它应用共用同一个 db，仓库里**不存在**任何针对
生产前缀的批量删除入口，将来也不该有。

于是"入库之后让旧答案失效"只剩一条路：**把代次号拌进缓存键**。代次一涨，
所有旧键在逻辑上就再也拼不出来了，它们变成无人引用的垃圾，由各自的 TTL
自然回收。这是 O(1) 的失效，不扫键、不删键、不碰别人的键空间。

## 为什么按租户而不按知识库

按 ``dataset_id`` 粒度更细，看着更省。但有两处让它**不成立**：

1. ``dataset_id=""`` 的查询语义是"不限知识库、全域检索"（见
   ``core.milvus_client.build_dataset_filter``）。它的答案覆盖该租户下**所有**
   知识库，因此往任何一个库写入，都必须让这批全域缓存失效。
2. 图索引里根本没有 ``dataset_id`` 字段（``core/graph_store.py`` 全文为 0 处），
   图检索只按租户隔离。开着 ``graph_index_on`` 时，任一知识库的入库都会改变
   同租户下所有知识库的图检索结果。

两条合起来：只要写了这个租户的语料，同租户下**任何**缓存条目都可能过期。
按租户失效就是这个事实的直接表达。代价是同租户跨库的过度失效——入库不是
高频操作，多算几次的成本远小于把过期答案发出去。

## 降级

与 Redis 相关的一切一样，代次也是**加速件不是依赖件**：Redis 缺席时退回
进程内计数器，同进程内的入库照样能让缓存失效（单副本部署下这就够了），
跨副本失效则失去——但服务不会因此少一分能力。任何出口都不抛异常。
"""
from __future__ import annotations

import threading
import time
from typing import Any

from core import redis_client
from core.observability import get_logger

_logger = get_logger(__name__)

#: 代次值在进程内的缓存时长。每次查询都去 Redis 读一次代次，等于给每个请求
#: 加一次 RTT，把缓存省下的钱又还回去一部分。取一个很短的值：入库之后最多
#: 这么久旧答案还会被发出去，而入库本身是分钟级的操作，几秒的滞后无关紧要。
EPOCH_CACHE_TTL_S = 3.0

_lock = threading.Lock()
#: tenant -> (代次值, 读到的时刻)
_cached: dict[str, tuple[int, float]] = {}
#: Redis 缺席时的进程内代次（同进程失效仍然有效）
_local: dict[str, int] = {}
#: 正在后台刷新的租户（防同一租户同时起一堆刷新线程）
_refreshing: set[str] = set()


def _rkey(tenant: str) -> str:
    return redis_client.key("epoch", tenant or "-")


def _norm(tenant: str | None) -> str:
    """把租户标识归一到与缓存键**同一个口径**（``resolve_tenant``）。

    归一放在这里而不是交给调用方，是因为两边的来源天然不一致：写侧的租户来自
    ``catalog``（存的是请求原样传进来的值，可能是 ``""``），读侧的缓存键用的
    是 ``resolve_tenant`` 解析后的值（强制隔离下 ``""`` → ``"default"``）。
    这两个是同一个租户，代次却会记在两个格子里——于是入库照常涨代次，查询
    照常命中旧缓存，**失效彻底失灵而且一声不吭**。这类错必须在唯一入口堵死，
    逐个叮嘱调用方"记得先 resolve"是堵不住的。

    ``_GLOBAL`` 不参与解析：它不是租户，是全局代次的占位。
    """
    if tenant == _GLOBAL:
        return _GLOBAL
    try:
        from config.settings import get_settings, resolve_tenant

        return resolve_tenant(tenant, get_settings())
    except Exception:  # noqa: BLE001 - 配置加载失败不该让失效机制不可用
        return tenant or ""


def current(tenant: str | None) -> int:
    """读当前代次。**从不阻塞、从不抛异常**，永远立刻返回一个可用的值。

    读不到就沿用上一次读到的值（没有则 0）。这个选择是有意的：读不到就当代次
    没变，缓存继续照旧命中。反过来（读失败就换个新代次）会让 Redis 每抖一下
    就全量击穿缓存——为了一个可选组件的抖动，把整片流量打到 LLM 上。

    **为什么进程内的值过期了也不当场去读 Redis。** 本函数在拼缓存键时被调用，
    而拼键发生在**事件循环里**（``server.app._cache_key``）。Redis 的
    ``socket_timeout`` 是 1 秒：一旦它卡住，每个请求都会把整个事件循环拽停
    最长 1 秒，32 并发就是几十秒的全局停摆。``_cache_peek`` 当初刻意不查 L2
    就是为了躲开这件事，代次读要是又把它请回来，等于白躲。

    所以过期后的刷新丢给后台线程，本次调用照旧返回旧值（stale-while-
    revalidate）。这不是妥协：代次本来就允许滞后 :data:`EPOCH_CACHE_TTL_S`
    秒，"滞后 3 秒"与"滞后 3 秒零一个 RTT"在语义上没有区别，在延迟上差一个
    数量级。冷启动同理——首次读返回 0 并在后台补，最坏结果是那一小会儿算出来
    的键与真实代次对不上，白白多算一次；而绝不会发出过期答案，因为对不上的
    键必然未命中。
    """
    t = _norm(tenant)
    now = time.monotonic()
    with _lock:
        hit = _cached.get(t)
        if hit is not None and now - hit[1] < EPOCH_CACHE_TTL_S:
            return hit[0]
        stale = hit[0] if hit is not None else _local.get(t, 0)

    if not redis_client.is_configured():
        return stale

    _refresh_async(t, stale)
    return stale


def _read_epoch(t: str, fallback: int) -> int:
    """同步读一次代次（只在后台线程与测试里调用）。"""
    raw = redis_client.call("get", _rkey(t))
    if raw is None:
        # 键不存在（还没人 bump 过）与命令失败，在 call() 里都是 None。
        # 两者都按 fallback 处理：前者本来就该是 0，后者不该制造抖动。
        return fallback
    try:
        return int(raw)
    except (TypeError, ValueError):
        _logger.debug("代次值无法解析（按 %s 处理）：%r", fallback, raw)
        return fallback


def _refresh_async(t: str, fallback: int) -> None:
    """后台补一次代次读取；同一租户同时最多一个在飞。

    每租户每 :data:`EPOCH_CACHE_TTL_S` 秒最多起一个线程，量级可忽略。加在飞
    标记是为了防"缓存刚过期、32 个请求同时进来"起 32 个线程去读同一个键。
    """
    with _lock:
        if t in _refreshing:
            return
        _refreshing.add(t)

    def _work() -> None:
        try:
            value = _read_epoch(t, fallback)
            with _lock:
                _cached[t] = (value, time.monotonic())
        except Exception as exc:  # noqa: BLE001 - 后台刷新失败只是继续用旧值
            _logger.debug("代次后台刷新失败（继续用旧值）：%s", exc)
        finally:
            with _lock:
                _refreshing.discard(t)

    try:
        threading.Thread(target=_work, name=f"epoch-refresh-{t}", daemon=True).start()
    except RuntimeError:  # 解释器正在退出，起不了线程
        with _lock:
            _refreshing.discard(t)


def refresh_now(tenant: str | None) -> int:
    """同步刷新并返回代次（**会阻塞**，只给非事件循环的调用方与测试用）。"""
    t = _norm(tenant)
    if not redis_client.is_configured():
        with _lock:
            return _local.get(t, 0)
    with _lock:
        hit = _cached.get(t)
        fallback = hit[0] if hit is not None else _local.get(t, 0)
    value = _read_epoch(t, fallback)
    with _lock:
        _cached[t] = (value, time.monotonic())
    return value


def bump(tenant: str | None, *, reason: str = "") -> int:
    """让该租户的全部已缓存答案失效，返回新代次（失败返回旧值，不抛）。

    调用点是"语料**已经真的**写完"的那一刻，而不是"提交了入库任务"的那一刻
    ——任务还在队列里排队时旧答案仍然是对的，提前失效只是白白多算几遍。
    """
    t = _norm(tenant)
    with _lock:
        _local[t] = _local.get(t, 0) + 1
        local_value = _local[t]
        # 立刻让本进程的读跳过 TTL 窗口：入库线程与查询线程通常同进程，
        # 等 3 秒才生效的话，"传完文档马上再问一次"这个最常见的动作就会
        # 拿到旧答案——而这恰恰是用户最容易发现、最像 bug 的那一次。
        _cached.pop(t, None)

    if not redis_client.is_configured():
        _logger.info("语料代次已递增（进程内）：tenant=%s epoch=%s %s",
                     t or "-", local_value, reason)
        return local_value

    value = redis_client.call("incr", _rkey(t))
    if value is None:
        _logger.warning("代次递增失败（Redis 不可用），仅本进程生效：tenant=%s", t or "-")
        return local_value
    try:
        new_epoch = int(value)
    except (TypeError, ValueError):
        return local_value
    with _lock:
        _cached[t] = (new_epoch, time.monotonic())
    _logger.info("语料代次已递增：tenant=%s epoch=%s %s", t or "-", new_epoch, reason)
    return new_epoch


def bump_all(*, reason: str = "") -> None:
    """全域失效：配置变更等"影响所有租户"的场景。

    没有"遍历所有租户"这回事——租户名单在 Milvus 里，为了失效去扫一遍不值。
    改用一个全局代次：它进每一个缓存键，涨一次就让所有租户的旧键一起作废。
    """
    bump(_GLOBAL, reason=reason)


#: 全局代次的租户位。用一个正常租户名取不到的值，避免与真实租户撞车。
_GLOBAL = "\x00global"


def stamp(tenant: str | None) -> str:
    """给缓存键用的代次戳：``<全局代次>.<租户代次>``。

    两个代次都要进键：租户代次管"这个租户的语料变了"，全局代次管"检索
    /嵌入配置变了，所有人的旧答案都不作数了"。
    """
    return f"{current(_GLOBAL)}.{current(tenant)}"


def prime(tenant: str | None = None) -> None:
    """同步预读代次（全局 + 指定租户）。**会阻塞**，只在进程启动时调。

    消掉的是这样一个冷启动窗口：:func:`current` 冷启动先返回 0、再由后台线程
    补读真值（它必须这样，见该函数的说明），而一次真实问答要跑好几秒，比
    :data:`EPOCH_CACHE_TTL_S` 还长。于是第一个请求用 epoch=0 算键并写缓存，
    等它答完，后台刷新已把代次改成真值——第二个人问同一句算出来的是另一个键，
    **必然未命中**，头一条缓存从落地起就是垃圾。症状是"服务刚起来那会儿缓存
    像是不工作"，几秒后自愈，日志里一声不吭。

    启动阶段本来就允许阻塞几十毫秒，在那里花一次 GET 把窗口整个消掉最划算。
    """
    refresh_now(_GLOBAL)
    refresh_now(tenant)


def reset() -> None:
    """测试钩子：清空进程内的代次缓存与本地计数（不碰 Redis）。"""
    with _lock:
        _cached.clear()
        _local.clear()
        _refreshing.clear()


def stats() -> dict[str, Any]:
    with _lock:
        return {
            "cached_tenants": len(_cached),
            "local_only": not redis_client.is_configured(),
            "ttl_s": EPOCH_CACHE_TTL_S,
        }


__all__ = [
    "EPOCH_CACHE_TTL_S",
    "bump",
    "bump_all",
    "current",
    "prime",
    "refresh_now",
    "reset",
    "stamp",
    "stats",
]
