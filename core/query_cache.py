"""两级答案缓存（L1 进程内 LRU + L2 Redis）+ 跨进程 single-flight。

**定位：缓存是加速件，Redis 是加速件的加速件。**

原来的实现只有 L1（``server/app.py`` 里的 ``_query_cache`` / ``_query_flights``），
在单副本下够用，多副本一开就露馅：N 个副本各算各的，命中率被切成 1/N，而热点
问题（发布会当天所有人问同一句）会同时打穿 N 条完整 RAG 链路。本模块把那套
语义原样搬过来，再往下垫一层 Redis::

    读：L1 -> L2 -> miss          写：L1 + L2 同写
    在途：进程内 Event + Redis SET NX 锁（跨进程合并）

三条硬约束，顺序即优先级：

1. **不能因为 Redis 挂了就不能答题。** 所有 Redis 出口都收敛为「失败 = 当作
   没有 L2」，本模块的公开 API 一个异常都不往外抛。Redis 缺席时行为与今天的
   纯 L1 实现逐字节一致。
2. **不能把用户问句写进 Redis 键名。** 目标实例与其它应用共用 db（实测
   dbsize=19），键名会出现在 MONITOR / SLOWLOG / ``KEYS *`` / RDB 里，等于把
   问句泄漏给任何能连这个 db 的人。所以键名一律取逻辑键的 sha256。L1 用逻辑
   键本身做索引——它只活在本进程内存里，而本进程本来就持有原始问句，
   再哈希一遍纯属自欺。
3. **不能删别人的锁。** 见 :meth:`QueryCache.release` 的注释。

用法（与被替换的 ``_cache_*`` 函数一一对应）::

    cache = get_query_cache()
    k = make_cache_key(query, acl, retry, tenant_id, dataset_id)
    cached, flight, owner = cache.reserve(k)
    if cached is not None:
        return cached
    if not owner:
        flight.wait(timeout=...)   # 回到循环重判（此时多半已命中）
    ...
    cache.put(k, payload)
    cache.release(k, flight)       # 仅 owner 需要，且必须在 finally 里
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Optional

from core import redis_client as _redis
from core.observability import get_logger

_logger = get_logger(__name__)

# 释放锁的比较删除脚本。放 Lua 里是因为 GET + DEL 两条命令之间存在窗口：
# 窗口里锁可能过期并被别的进程重新持有，此时 DEL 删的是**别人的**锁，
# single-flight 当场退化成「谁都能进」，热点问题瞬间打穿 N 条链路。
# Redis 的 Lua 是单线程原子执行的，这才是 GET-then-DEL 的正确写法。
_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


def make_cache_key(
    query: str,
    acl: list[str] | None,
    retry: bool,
    tenant_id: str | None,
    dataset_id: str | None = None,
    epoch: str = "",
    serving_generation: str | int = "",
) -> str:
    """拼出逻辑缓存键。

    dataset_id 必须进缓存键：同一问句在不同知识库下的答案不同，
    漏掉这一维会把 A 库的答案回给 B 库的查询。acl 排序后入键，
    否则 ``["a","b"]`` 与 ``["b","a"]`` 会被当成两个不同的问题各算一遍。

    ``epoch`` 是租户级加速失效戳；``serving_generation`` 是 Dataset 表中的
    持久化 serving fence。后者必须独立进键：即使 Redis epoch bump 失败，删除
    请求事务已经提交的 generation 仍会让旧答案键永久失联。两者并存，前者保留
    既有粗粒度失效，后者提供关系数据库权威的删除/到期安全边界。

    ``dataset_id`` 在这里 ``strip()``：业务侧（``rag.py`` / ``retrieval``）一律
    strip 之后再用，缓存键不 strip 的话 ``"kb "`` 与 ``"kb"`` 会各存一份——
    问的是同一个库，却互相不命中，白算一遍。tenant 不在这里归一化：它要经
    ``config.settings.resolve_tenant`` 按配置决定回退，那需要 settings，
    保持本函数是纯函数，由调用方传入已解析好的值。
    """
    return json.dumps(
        [
            tenant_id or "",
            (dataset_id or "").strip(),
            query,
            sorted(acl or []),
            retry,
            epoch,
            str(serving_generation),
        ],
        ensure_ascii=False,
    )


def _digest(logical_key: str) -> str:
    """逻辑键 -> Redis 键名分量（sha256 十六进制）。

    不截断：截到 16 字符省下的那点内存换来的是生日碰撞——两个不同问句共用
    一条缓存，表现为「偶尔答非所问」，这类 bug 在线上几乎不可能被定位。
    """
    return hashlib.sha256(logical_key.encode("utf-8")).hexdigest()


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass
class CacheConfig:
    """两级缓存参数。

    L1 与 L2 的 TTL 是**分开的**，不是疏忽：L1 存的是本进程刚算出来的东西，
    容量只有几十条，短 TTL 换新鲜度；L2 是跨副本共享的公共品，多存一会儿才
    摊得平那次 RAG 全链路的成本（实测约 5.6s）。硬绑成同一个值只会逼人在
    「本地太陈旧」和「共享太浪费」之间二选一。
    """

    l1_ttl_s: float = 600.0        # L1 存活时长
    l1_max: int = 64               # L1 容量上限（LRU 淘汰）
    l2_ttl_s: float = 900.0        # L2 存活时长（Redis TTL）
    abstain_ttl_s: float = 60.0    # 弃权结果的存活时长（两级都用它）
    ttl_jitter: float = 0.15       # TTL 抖动幅度（只往短里抖，见 _jittered_ttl）
    lock_ttl_s: float = 300.0      # 跨进程锁持有时长
    wait_timeout_s: float = 300.0  # 等待他进程算完的总时限（超时后自己算）
    poll_interval_s: float = 0.05  # 轮询 L2 的初始间隔
    poll_max_interval_s: float = 0.5  # 轮询间隔退避上限
    redis_on: bool = True          # 是否启用 L2（url 为空时此项无效）

    @classmethod
    def from_settings(cls, settings: Any = None) -> "CacheConfig":
        """从 ``get_settings().redis`` + 既有 ``RAG4C_QUERY_CACHE_*`` 环境变量构造。

        L1 的两个参数继续读老环境变量而不是搬进 RedisSettings：它们描述的是
        进程内内存，与 Redis 一点关系没有，塞进 redis 段会让「关掉 Redis」
        这个动作看起来像是会顺带改掉内存缓存的容量。
        """
        if settings is None:
            try:
                from config.settings import get_settings

                settings = get_settings().redis
            except Exception:  # 配置加载失败不该让缓存不可用——用默认值继续
                settings = None
        lock_ttl = float(getattr(settings, "lock_ttl_s", 300.0))
        return cls(
            l1_ttl_s=_env_float("RAG4C_QUERY_CACHE_TTL_S", 600.0),
            l1_max=_env_int("RAG4C_QUERY_CACHE_MAX", 64),
            l2_ttl_s=float(getattr(settings, "cache_ttl_s", 900.0)),
            abstain_ttl_s=_env_float("RAG4C_QUERY_CACHE_ABSTAIN_TTL_S", 60.0),
            ttl_jitter=_env_float("RAG4C_QUERY_CACHE_TTL_JITTER", 0.15),
            lock_ttl_s=lock_ttl,
            # 等待时限默认对齐锁的 TTL：锁最迟在 TTL 到点时消失，等过这个点
            # 还没结果，就说明持锁者已经死了，再等下去只是陪葬。
            wait_timeout_s=_env_float("RAG4C_QUERY_CACHE_WAIT_S", lock_ttl),
            redis_on=bool(getattr(settings, "cache_on", True)),
        )


class QueryCache:
    """线程安全的两级答案缓存。

    实例之间**只共享 L2**：每个实例有独立的 L1 与独立的锁持有记录，因此
    「两个实例」就是「两个进程」的忠实模拟（测试即靠这一点验证跨进程行为）。
    """

    def __init__(
        self,
        config: Optional[CacheConfig] = None,
        *,
        namespace: str = "cache",
    ) -> None:
        self.cfg = config or CacheConfig.from_settings()
        self.namespace = namespace
        self._lock = threading.Lock()
        self._l1: "OrderedDict[str, tuple[float, dict[str, Any]]]" = OrderedDict()
        self._flights: dict[str, threading.Event] = {}
        # 本进程当前持有的锁令牌：逻辑键 -> token。同一逻辑键在本进程内至多
        # 一个 owner（由 _flights 保证），所以一个 dict 就够，不需要栈。
        self._tokens: dict[str, str] = {}
        self._c_l1_hits = 0
        self._c_l2_hits = 0
        self._c_misses = 0
        self._c_promotions = 0
        self._c_sf_local_waits = 0   # 同进程内等在 Event 上的次数
        self._c_sf_remote_waits = 0  # 因他进程持锁而轮询等待的次数
        self._c_sf_timeouts = 0      # 等超时后自己重算的次数
        self._c_l2_errors = 0        # Redis 命令失败次数（含连不上）

    # ------------------------------------------------------------------ #
    # Redis 出口：全部经过这里，保证「失败 = 没有 L2」而不是抛异常
    # ------------------------------------------------------------------ #
    def l2_configured(self) -> bool:
        """配置上是否启用了 L2（开关打开且配了 url）。**不代表连得上。**

        这是所有"要不要走一趟 Redis"的判断该用的谓词：纯配置检查、零 IO，
        每条命令前都会问一次，不能有网络成本。
        """
        if not self.cfg.redis_on:
            return False
        try:
            return _redis.is_configured()
        except Exception:
            return False

    def l2_reachable(self) -> bool:
        """L2 此刻是否**真的可用**（配置启用 + Redis 连得上）。

        只给"报告状态"用，不要拿它当命令前的闸门——它可能发一条 PING。

        和 :meth:`l2_configured` 分开，是因为二者混为一谈时最坏的那个组合
        （配了 url、Redis 挂了）会伪装成健康：``l2_enabled`` 恒为 True，
        ``/api/metrics`` 一直报缓存正常，而每个请求都在静默 miss，唯一的痕迹
        是 ``l2_errors`` 在涨——没人盯着一个只涨不跌的计数器。
        """
        if not self.l2_configured():
            return False
        try:
            return _redis.is_reachable()
        except Exception:
            return False

    def l2_enabled(self) -> bool:
        """已弃用：改用语义明确的 :meth:`l2_configured` / :meth:`l2_reachable`。

        保留是为了不悄悄改变既有调用方的行为——这个名字读起来像"能用"，
        实际只是"配了"，两者在 Redis 宕机时正好相反。
        """
        return self.l2_configured()

    def _exec(self, fn_name: str, *args: Any, **kwargs: Any) -> tuple[bool, Any]:
        """执行一条 Redis 命令，返回 ``(是否成功, 返回值)``。

        这里刻意**不用** ``redis_client.call``：它把「命令失败」和「键不存在」
        都压成 None，而 single-flight 恰恰要区分这两者——``SET NX`` 返回 None
        既可能是「别人持锁」（该等），也可能是「Redis 挂了」（该立刻自己算）。
        把这两种情况混为一谈的后果是 Redis 一挂，每个请求都白等一个
        wait_timeout_s 才开始干活，缓存从加速件变成刹车片。
        ``redis_client.call`` 的文档字符串本身也指明了这种场景该自己接管。
        """
        if not self.l2_configured():
            return False, None
        try:
            client = _redis.get_client()
            if client is None:
                return False, None
            return True, getattr(client, fn_name)(*args, **kwargs)
        except Exception as exc:  # 网络抖动 / 超时 / 依赖缺失，一律降级
            with self._lock:
                self._c_l2_errors += 1
            _logger.debug("查询缓存 Redis 命令 %s 失败（已降级为纯 L1）：%s", fn_name, exc)
            return False, None

    def _rkey(self, kind: str, digest: str) -> str:
        try:
            return _redis.key(kind, digest)
        except Exception:
            # key() 只有在配置加载炸掉时才会失败；给个自带前缀的兜底，
            # 免得异常从这么一个纯字符串拼接的地方逃出去。
            return f"rag4c:{kind}:{digest}"

    # ------------------------------------------------------------------ #
    # L1：TTL + LRU
    # ------------------------------------------------------------------ #
    def _l1_get(self, key: str) -> dict[str, Any] | None:
        """读 L1（调用方需已持有 self._lock）。过期条目顺手清掉。"""
        item = self._l1.get(key)
        if item is None:
            return None
        expires_at, payload = item
        if time.monotonic() > expires_at:
            self._l1.pop(key, None)
            return None
        self._l1.move_to_end(key)
        # 返回浅拷贝：调用方会往结果里塞 ``cached=True`` 之类的标记，
        # 直接交出内部对象等于让每个读者都能改写所有后续读者看到的内容。
        return dict(payload)

    def _jittered(self, ttl: float) -> float:
        """给 TTL 加抖动，只往短里抖。实现见 :func:`core.two_level_cache.jittered_ttl`。

        为什么共用一份：抖动幅度是个会被反复调的旋钮，而答案缓存与槽位/向量
        缓存面对的是同一种同步过期。两份拷贝迟早会调歪一份，且歪掉的那份不会
        报错——只会在某个整点上突然多打一批 LLM 调用。
        """
        from core.two_level_cache import jittered_ttl

        return jittered_ttl(ttl, self.cfg.ttl_jitter)

    def _l1_put(
        self, key: str, payload: dict[str, Any], ttl_s: float | None = None
    ) -> None:
        # 存到期时刻而不是写入时刻：条目可以各有各的 TTL（弃权结果就该短命，
        # 见 put 的说明），存写入时刻的话过期判定就只能用全局那一个 TTL。
        ttl = self._jittered(self.cfg.l1_ttl_s if ttl_s is None else max(0.0, ttl_s))

        with self._lock:
            self._l1[key] = (time.monotonic() + ttl, dict(payload))
            self._l1.move_to_end(key)
            while len(self._l1) > self.cfg.l1_max:
                self._l1.popitem(last=False)

    # ------------------------------------------------------------------ #
    # L2：String + TTL
    # ------------------------------------------------------------------ #
    def _l2_get(self, digest: str) -> dict[str, Any] | None:
        ok, raw = self._exec("get", self._rkey(self.namespace, digest))
        if not ok or not raw:
            return None
        try:
            payload = json.loads(raw)
        except Exception:
            # 载荷格式变了（版本回滚 / 键名撞车）。当作未命中即可，
            # 不删键：删了也就是省下几百字节，而 TTL 一到自然消失。
            return None
        return payload if isinstance(payload, dict) else None

    def _l2_put(
        self, digest: str, payload: dict[str, Any], ttl_s: float | None = None
    ) -> bool:
        try:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            # 载荷里混进了不可序列化对象。L1 已经写成功了，这里静默跳过 L2 即可
            # ——为了共享缓存把一次已经算完的问答变成 500，是本末倒置。
            _logger.debug("查询缓存载荷无法 JSON 序列化，跳过 L2：%s", exc)
            return False
        ttl = self._jittered(self.cfg.l2_ttl_s if ttl_s is None else max(0.0, ttl_s))
        ok, _ = self._exec(
            "set", self._rkey(self.namespace, digest), raw,
            px=max(1, int(ttl * 1000)),
        )
        return ok

    # ------------------------------------------------------------------ #
    # 跨进程锁
    # ------------------------------------------------------------------ #
    def _lock_acquire(self, digest: str) -> tuple[bool, str | None]:
        """尝试拿锁，返回 ``(Redis 是否可用, 令牌或 None)``。

        令牌带上 pid/线程号只是为了排查时能一眼看出锁被谁拿着；
        唯一性靠 uuid4 保证（pid 会回绕，容器里更是天天撞车）。
        """
        token = f"{os.getpid()}-{threading.get_ident()}-{uuid.uuid4().hex}"
        ok, res = self._exec(
            "set", self._rkey("lock", digest), token.encode("utf-8"),
            nx=True, px=max(1, int(self.cfg.lock_ttl_s * 1000)),
        )
        if not ok:
            return False, None
        return True, (token if res else None)

    def _lock_alive(self, digest: str) -> bool:
        ok, res = self._exec("exists", self._rkey("lock", digest))
        # Redis 不可用时返回 False：让等待者立刻结束轮询去自己算，
        # 而不是对着一个连不上的 Redis 空转到超时。
        return bool(ok and res)

    def _lock_release(self, digest: str, token: str) -> None:
        self._exec("eval", _RELEASE_LUA, 1, self._rkey("lock", digest), token.encode("utf-8"))

    # ------------------------------------------------------------------ #
    # 公开 API
    # ------------------------------------------------------------------ #
    def peek(self, key: str, *, allow_l2: bool = True) -> dict[str, Any] | None:
        """只读地看一眼缓存，**不**登记 single-flight 所有权。

        与 :meth:`reserve` 的区别是不占所有权，因此可以在缓存命中的请求
        **取得执行槽之前**就返回——命中本身不需要工作线程，为它排队是荒谬的。

        ``allow_l2=False`` 是给事件循环里的调用点准备的逃生门：查 L2 是一次
        网络往返，socket_timeout 是 1s，在 async 端点里直接调用等于给整个事件
        循环挂上最长 1s 的停摆风险。跨副本命中率和事件循环的响应性哪个更值，
        由调用点自己判断，这里不替它决定。
        """
        with self._lock:
            payload = self._l1_get(key)
            if payload is not None:
                self._c_l1_hits += 1
                return payload
        if not allow_l2:
            return None
        payload = self._l2_get(_digest(key))
        if payload is None:
            return None
        # 回填 L1：跨副本命中的键往往是热点键，不回填的话每次都要走一趟网络，
        # 等于把「省下一次 RAG」换成「每次多一次 RTT」，只赚了一半。
        # 回填也走内容策略：弃权结果在 L2 只剩不到一分钟，回填到 L1 却给它
        # 十分钟，等于让这一个副本比别人多陈旧九分钟。
        self._l1_put(key, payload, self._ttl_for(payload))
        with self._lock:
            self._c_l2_hits += 1
            self._c_promotions += 1
        return dict(payload)

    def _ttl_for(self, payload: dict[str, Any]) -> float | None:
        """按载荷内容决定存活时长；``None`` 表示用各级的默认 TTL。

        **弃权结果短命。** 弃权的意思是"以现在的语料回答不了这个问题"，
        而一个人紧接着重问同一句的最常见原因，恰恰是他刚把缺的那份文档传上来。
        按普通答案的 15 分钟缓存，等于在最需要给出新答案的那一刻，把"我不知道"
        又原样发一遍。代次机制（:mod:`core.cache_epoch`）能覆盖走完整入库流程
        的情况，但覆盖不到"外部直接写了 Milvus""同事在另一个租户下补的料"
        "上一次弃权只是因为重排模型抖了一下"这些代次不会涨的场景。短 TTL 是
        对这些漏网情况的兜底：最多陈旧一分钟，代价只是偶尔多算一次。

        弃权标记要在两处找：服务端存进来的是 ``{"result": {...}, ...}``
        这层信封（``server.app._serialize``），``abstained`` 在信封**里面**。
        只看顶层的话这个策略会静默失效——一直返回默认 TTL，测不出来也报不出错。
        """
        if payload.get("abstained"):
            return self.cfg.abstain_ttl_s
        inner = payload.get("result")
        if isinstance(inner, dict) and inner.get("abstained"):
            return self.cfg.abstain_ttl_s
        return None

    def put(self, key: str, payload: dict[str, Any], ttl_s: float | None = None) -> None:
        """双写 L1 + L2。L2 写失败不影响 L1（也不抛异常）。

        不传 ``ttl_s`` 时按 :meth:`_ttl_for` 的内容策略取（弃权短命，其余用
        默认）。策略放在缓存里而不是交给调用方，是因为写缓存的入口不止一处
        （``put`` 与两处 L2 回填），逐个叮嘱"记得给弃权传短 TTL"迟早会漏。
        """
        ttl = self._ttl_for(payload) if ttl_s is None else ttl_s
        self._l1_put(key, payload, ttl)
        if self.l2_configured():
            self._l2_put(_digest(key), payload, ttl)

    def reserve(
        self, key: str
    ) -> tuple[dict[str, Any] | None, threading.Event, bool]:
        """返回缓存结果，或取得「算这一次」的所有权。

        返回 ``(缓存内容或 None, 进程内在途事件, 是否为 owner)``，与被替换的
        ``_cache_reserve`` 完全同形。契约：

        - ``owner=True``  -> 由你计算，算完 :meth:`put`，并在 **finally** 里
          :meth:`release`（不 release 会把同 key 的请求全部挂死到超时）；
        - ``owner=False`` -> 要么已拿到结果，要么该等 ``flight``；**不要**
          调用 release，锁的归属不在你手上。

        ``owner=False`` 且无结果时不会永久等待：调用方按老写法带超时等
        ``flight`` 再回到循环重判即可。

        注意本方法在「他进程持锁」时会阻塞轮询（上限 ``wait_timeout_s``）。
        这不是退化——调用方拿到 ``owner=False`` 之后本来就要原地等，
        差别只是等在 Event 上还是等在这里。但也因此**不可**在事件循环里调用。
        """
        digest = ""

        with self._lock:
            payload = self._l1_get(key)
            if payload is not None:
                self._c_l1_hits += 1
                return payload, threading.Event(), False
            flight = self._flights.get(key)
            if flight is not None:
                # 同进程已有人在算：等 Event 即可，连 Redis 都不用碰。
                # 这一层存在的意义就是把绝大多数同 key 请求挡在网络之前。
                self._c_sf_local_waits += 1
                return None, flight, False
            flight = threading.Event()
            self._flights[key] = flight

        # 走到这里：本进程的慢路径归我了。以下任何提前返回都必须先释放
        # flight，否则同进程的后来者会等一个永远不会 set 的 Event。
        # 哈希推迟到这里算：L1 命中是最热的路径，没必要为它多做一次 sha256。
        try:
            digest = _digest(key)
            payload = self._l2_get(digest)
            if payload is not None:
                return self._hit_from_l2(key, payload, flight)

            if not self.l2_configured():
                with self._lock:
                    self._c_misses += 1
                return None, flight, True

            return self._reserve_across_processes(key, digest, flight)
        except Exception as exc:  # 兜底：慢路径出任何意外都不能让请求失败
            _logger.debug("查询缓存慢路径异常（按未命中处理）：%s", exc)
            with self._lock:
                self._c_misses += 1
            return None, flight, True

    def _hit_from_l2(
        self, key: str, payload: dict[str, Any], flight: threading.Event
    ) -> tuple[dict[str, Any] | None, threading.Event, bool]:
        """慢路径上从 L2 捞到结果时的统一收尾：回填 L1、放掉在途、按命中返回。

        必须在这里放掉 flight：调用方拿到 ``owner=False`` 就不会再调 release，
        而 flight 是我们自己在慢路径入口登记的——不放，同进程的后来者就会
        等一个永远不会 set 的 Event，直到各自的超时。
        """
        self._l1_put(key, payload, self._ttl_for(payload))
        with self._lock:
            self._c_l2_hits += 1
            self._c_promotions += 1
        self._release_local(key, flight)
        return dict(payload), threading.Event(), False

    def _reserve_across_processes(
        self, key: str, digest: str, flight: threading.Event
    ) -> tuple[dict[str, Any] | None, threading.Event, bool]:
        """L2 未命中时的跨进程仲裁：抢锁 -> 抢不到就等 -> 等不到就自己算。"""
        deadline = time.monotonic() + self.cfg.wait_timeout_s
        interval = self.cfg.poll_interval_s
        counted_wait = False
        attempt = 0

        while True:
            attempt += 1
            usable, token = self._lock_acquire(digest)
            if not usable:
                # Redis 在这一刻不可用：立刻退化为纯 L1 owner。绝不能在这里
                # 等——等的对象（他进程的锁）本身就看不见了。
                with self._lock:
                    self._c_misses += 1
                return None, flight, True
            if token is not None:
                # 拿到锁之后**再查一次** L2（第一轮除外，reserve 刚查过）。
                # 少了这一步就有一个必然踩中的坑：等待者是在「持锁者释放锁」
                # 的瞬间抢到锁的，而那一刻结果早已写进 L2——不复查就会把刚
                # 算完的答案原样重算一遍，single-flight 只挡住了并发窗口，
                # 挡不住紧随其后的这一批，热点键上等于完全没生效。
                if attempt > 1:
                    payload = self._l2_get(digest)
                    if payload is not None:
                        self._lock_release(digest, token)
                        return self._hit_from_l2(key, payload, flight)
                with self._lock:
                    self._tokens[key] = token
                    self._c_misses += 1
                return None, flight, True

            # 锁在别人手上。先看一眼结果——持锁者可能就在这一瞬间写完了。
            payload = self._l2_get(digest)
            if payload is not None:
                return self._hit_from_l2(key, payload, flight)

            if not counted_wait:
                with self._lock:
                    self._c_sf_remote_waits += 1
                counted_wait = True

            if time.monotonic() >= deadline:
                # 等超时。宁可重复算一次，也不能让请求无限期挂着：
                # 持锁者可能进程被 kill 了而锁 TTL 还剩几分钟。
                # 这里**没有**令牌，release 时因此不会去删那把不属于我们的锁。
                with self._lock:
                    self._c_sf_timeouts += 1
                    self._c_misses += 1
                self._metric("singleflight_timeout")
                _logger.warning("跨进程 single-flight 等待超时（%.0fs），本进程自行计算",
                                self.cfg.wait_timeout_s)
                return None, flight, True

            if not self._lock_alive(digest):
                # 锁没了但结果也没有：持锁者要么失败要么死了。回到循环重新抢锁
                # ——注意是「抢」不是「直接算」，否则一群等待者会在锁释放的
                # 同一瞬间全部冲进去，single-flight 白做。超时判定放在这个
                # 分支**之前**，否则一旦出现「锁反复被别人抢走」的抖动，
                # 这里就是个永不检查 deadline 的死循环。
                continue

            time.sleep(min(interval, max(0.0, deadline - time.monotonic())))
            interval = min(interval * 1.5, self.cfg.poll_max_interval_s)

    def _release_local(self, key: str, flight: threading.Event) -> None:
        """摘掉进程内在途登记并唤醒等待者。"""
        with self._lock:
            if self._flights.get(key) is flight:
                self._flights.pop(key, None)
        flight.set()

    def release(self, key: str, flight: threading.Event) -> None:
        """释放所有权：唤醒同进程等待者 + 比较删除 Redis 锁。

        「比较」是关键：直接 ``DEL`` 在锁已因 TTL 过期、并被**另一个进程**
        重新持有时，删的是别人的锁。此后两个进程同时以为自己是 owner，
        热点问题一次打穿两条链路——而且这种竞态只在慢查询超过 lock_ttl_s 时
        才出现，压测压不出来，线上偏偏专挑最忙的时候发作。

        幂等：重复调用、非 owner 误调用都不会有副作用。
        """
        try:
            self._release_local(key, flight)
            with self._lock:
                token = self._tokens.pop(key, None)
            if token:
                self._lock_release(_digest(key), token)
        except Exception as exc:  # release 在 finally 里被调用，绝不能抛
            _logger.debug("查询缓存释放失败（忽略）：%s", exc)

    def wait(
        self, key: str, flight: threading.Event, timeout: float
    ) -> dict[str, Any] | None:
        """等在途结果，返回缓存内容或 None（超时）。

        便利封装：等 Event 之后再 peek 一次。给不想自己写 while 循环的调用方
        用；老式的「wait 完回到循环重判」写法同样有效，两者语义一致。
        """
        got = flight.wait(timeout=timeout)
        if not got:
            with self._lock:
                self._c_sf_timeouts += 1
            self._metric("singleflight_timeout")
        return self.peek(key)

    def invalidate(self, key: str) -> None:
        """删除某个键的两级缓存（配置变更 / 文档重建索引后调用）。"""
        with self._lock:
            self._l1.pop(key, None)
        if self.l2_configured():
            self._exec("delete", self._rkey(self.namespace, _digest(key)))

    def clear_l1(self) -> None:
        """只清空 L1。

        **本模块不提供清空 L2 的入口。** 目标实例与其它应用共用 db，
        任何 ``SCAN``+``DEL`` 扫尾都可能因为前缀写错而误伤别人的键；
        L2 有 TTL，等它自己过期是唯一不需要祈祷的方案。
        """
        with self._lock:
            self._l1.clear()

    def stats(self) -> dict[str, Any]:
        """状态快照（供 /api/metrics 与健康检查；不抛异常）。

        ``hits`` / ``misses`` / ``size`` / ``max`` / ``ttl_s`` 保留原
        ``_cache_stats()`` 的字段名与含义，监控面板与历史数据无需改动；
        分层明细走 ``l1_hits`` / ``l2_hits`` 等新字段。
        """
        with self._lock:
            l1_hits, l2_hits, misses = self._c_l1_hits, self._c_l2_hits, self._c_misses
            snapshot = {
                "size": len(self._l1),
                "max": self.cfg.l1_max,
                "hits": l1_hits + l2_hits,
                "misses": misses,
                "ttl_s": self.cfg.l1_ttl_s,
                "l1_hits": l1_hits,
                "l2_hits": l2_hits,
                "l2_ttl_s": self.cfg.l2_ttl_s,
                "abstain_ttl_s": self.cfg.abstain_ttl_s,
                "ttl_jitter": self.cfg.ttl_jitter,
                "promotions": self._c_promotions,
                "inflight": len(self._flights),
                "singleflight_local_waits": self._c_sf_local_waits,
                "singleflight_remote_waits": self._c_sf_remote_waits,
                "singleflight_timeouts": self._c_sf_timeouts,
                "l2_errors": self._c_l2_errors,
            }
        total = snapshot["hits"] + misses
        snapshot["hit_rate"] = round(snapshot["hits"] / total, 4) if total else 0.0
        # 两个信号分开报。合成一个的时候，"配了 url 但 Redis 挂了"这个最需要
        # 被看见的状态恰好长得和"一切正常"一模一样。
        snapshot["l2_configured"] = self.l2_configured()
        snapshot["l2_reachable"] = self.l2_reachable()
        # 兼容既有消费方（scripts/e2e_llm_cache.py 等）。语义保持"已配置"不变，
        # 不改成 reachable——那会让这个字段的含义在一次升级里静默翻转。
        snapshot["l2_enabled"] = snapshot["l2_configured"]
        return snapshot

    def _metric(self, event: str) -> None:
        try:
            from core.metrics import get_metrics

            get_metrics().incr(f"query_cache.{event}")
        except Exception:  # 埋点失败不影响缓存主流程
            pass


# ---------------------------------------------------------------------------
# 进程级单例
# ---------------------------------------------------------------------------
_default_lock = threading.Lock()
_default: Optional[QueryCache] = None


def get_query_cache() -> QueryCache:
    """进程级默认缓存实例（懒构造）。

    单例而非每次新建：L1 是有状态的，每个请求各造一个等于永远不命中。
    """
    global _default
    if _default is not None:
        return _default
    with _default_lock:
        if _default is None:
            _default = QueryCache()
        return _default


def install_query_cache(cache: QueryCache) -> None:
    """指定进程级默认实例（供服务层在启动时装配调好参的缓存）。

    存在的理由是 :meth:`QueryCache.reserve` 的等待时限必须服从**调用方**的
    超时预算：HTTP 侧 240s 就放弃了，缓存却默认等到 300s（对齐锁 TTL），
    差出来的这一分钟里工作线程还在为一个早已没人接收的答案排队——正是
    ``core/retry.py`` 的 deadline 要根除的那类空转。服务层知道自己的总闸是
    多少，所以由它构造 config 并装进来，而不是让缓存去猜。
    """
    global _default
    with _default_lock:
        _default = cache


def reset_query_cache() -> None:
    """丢弃默认实例（测试用；配置变更后也可调用）。"""
    global _default
    with _default_lock:
        _default = None


__all__ = [
    "CacheConfig",
    "QueryCache",
    "make_cache_key",
    "get_query_cache",
    "install_query_cache",
    "reset_query_cache",
]
