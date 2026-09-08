"""Redis 接入层：惰性单例连接 + 全链路优雅降级。

**设计前提：Redis 是加速件，不是依赖件。**

答案缓存和跨副本排队都跑在 Redis 上，但这两件事都有等价的进程内实现
（LRU 缓存 / asyncio 信号量）。因此本模块的每个出口都遵循同一条契约：

    连不上 / 命令失败 -> 返回 None 或 False，**绝不抛给调用方**

调用方据此退回内存态，服务照常提供完整能力，只是失去跨副本共享。
反过来的做法——Redis 一挂问答服务跟着 500——是拿可用性换性能，方向反了。

键空间::

    <prefix>cache:<sha256>      答案缓存（String，带 TTL）
    <prefix>lock:<sha256>       跨进程 single-flight 锁（String NX，带 TTL）
    <prefix>q:query             请求排队（Stream + 消费者组）
    <prefix>res:<job_id>        排队任务的结果回传（String，带 TTL）

前缀不是可选的：目标实例可能与其它应用共用同一个 db（实测 dbsize=19），
没有前缀就是在别人的键空间里裸奔。同理，本模块**不提供** FLUSHDB 之类的
批量删除入口。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional

from core.observability import get_logger

_logger = get_logger(__name__)

_lock = threading.Lock()
_client: Any = None
_client_key: str = ""
_unavailable_logged = False


def _config() -> Any:
    from config.settings import get_settings

    return get_settings().redis


def key(*parts: str) -> str:
    """拼出带前缀的键名。所有键都必须经过这里。"""
    return _config().key_prefix + ":".join(parts)


def is_configured() -> bool:
    """是否配置了 Redis（url 非空）。不代表连得上。"""
    try:
        return bool(_config().url.strip())
    except Exception:
        return False


def get_client() -> Optional[Any]:
    """返回 Redis 客户端；未配置 / 连不上 / 缺依赖时返回 None。

    连接对象按 url 缓存为进程级单例。``redis-py`` 自带连接池，因此这里
    不需要（也不应该）每次调用新建连接——那会在高并发下把 TIME_WAIT 打满。

    首次不可用时记一条 WARNING，之后静默：Redis 挂掉期间每个请求都刷一行
    日志，只会把真正有用的错误淹掉。
    """
    global _client, _client_key, _unavailable_logged

    cfg = _config()
    url = cfg.url.strip()
    if not url:
        return None

    if _client is not None and _client_key == url:
        return _client

    with _lock:
        if _client is not None and _client_key == url:
            return _client
        try:
            import redis  # 惰性导入：未装 redis-py 时其余功能不受影响
        except ImportError:
            if not _unavailable_logged:
                _logger.warning("未安装 redis-py，Redis 相关能力退回进程内实现")
                _unavailable_logged = True
            return None
        try:
            client = redis.Redis.from_url(
                url,
                socket_timeout=cfg.socket_timeout_s,
                socket_connect_timeout=cfg.socket_connect_timeout_s,
                # 全部按 bytes 收发，由各调用方自己决定怎么解码。
                # decode_responses=True 会让二进制值（未来若存压缩载荷）损坏。
                decode_responses=False,
                health_check_interval=30,
            )
            client.ping()
        except Exception as exc:
            if not _unavailable_logged:
                _logger.warning("Redis 连接失败（退回进程内实现）：%s", exc)
                _unavailable_logged = True
            return None
        _client = client
        _client_key = url
        _unavailable_logged = False
        _logger.info("Redis 已连接：%s", _redact(url))
        return _client


def _redact(url: str) -> str:
    """隐去连接串里的密码后再记日志。"""
    if "@" not in url:
        return url
    head, _, tail = url.rpartition("@")
    scheme, sep, cred = head.partition("://")
    if not sep or ":" not in cred:
        return url
    user, _, _pw = cred.partition(":")
    return f"{scheme}://{user}:****@{tail}"


def call(fn_name: str, *args: Any, **kwargs: Any) -> Any:
    """执行一条 Redis 命令，任何异常都收敛为 None。

    这是本模块对外的**统一容错出口**——调用方不必在每个命令外面各写一遍
    try/except，也就不会有人漏写。想区分「命令失败」和「键不存在」的场景
    请直接用 :func:`get_client`，自己承担异常处理。
    """
    client = get_client()
    if client is None:
        return None
    try:
        return getattr(client, fn_name)(*args, **kwargs)
    except Exception as exc:
        _logger.debug("Redis 命令 %s 失败：%s", fn_name, exc)
        return None


def ping() -> bool:
    """探活（供 /api/health 与 /readyz 使用）。每次都真的发一条 PING。"""
    return call("ping") is True


#: :func:`is_reachable` 的探活结果缓存时长（秒）。与 server/health.py 的
#: ``PROBE_TTL_S`` 取同一个数量级，理由也一样：状态查询不该按调用次数付费。
_REACHABLE_TTL_S = 5.0
_reachable_cache: tuple[float, bool] | None = None
_reachable_lock = threading.Lock()


def is_reachable(max_age_s: float = _REACHABLE_TTL_S) -> bool:
    """Redis 此刻是否**真的连得上**（带 TTL 缓存，绝不抛异常）。

    与 :func:`is_configured` 的区别是整个 L2 可观测性的关键：前者只看 url
    是否非空，后者要发一条 PING。"配了但连不上"是最常见的生产状态，也正是
    从前完全看不见的那一种——运维看板显示缓存健康，实际每个请求都在静默
    miss。

    结果必须缓存：``/api/metrics`` 一次调用会问三个缓存实例（answer / embed /
    llm）要状态，逐个真发 PING 的话，Redis 一挂，每次抓指标就要吃三个
    ``socket_timeout_s``。缓存对成功与失败**一视同仁**——负缓存同样重要，
    否则宕机期间的探活成本比正常时还高。

    Args:
        max_age_s: 缓存的最大年龄；传 0 强制重新探活。
    """
    global _reachable_cache

    if not is_configured():
        return False
    now = time.monotonic()
    cached = _reachable_cache
    if cached is not None and max_age_s > 0 and (now - cached[0]) < max_age_s:
        return cached[1]
    with _reachable_lock:
        # 双检：等锁期间可能已有别的线程探完
        cached = _reachable_cache
        if cached is not None and max_age_s > 0 and (time.monotonic() - cached[0]) < max_age_s:
            return cached[1]
        alive = ping()
        _reachable_cache = (time.monotonic(), alive)
        return alive


def stats() -> dict[str, Any]:
    """状态快照：是否配置、是否连通。健康检查用，不抛异常。"""
    if not is_configured():
        return {"configured": False, "connected": False, "detail": "未配置（使用进程内实现）"}
    connected = is_reachable()
    return {
        "configured": True,
        "connected": connected,
        "detail": "ok" if connected else "连接失败（已退回进程内实现）",
    }


def reset_client() -> None:
    """丢弃缓存的连接（测试用；配置变更后也可调用）。"""
    global _client, _client_key, _unavailable_logged, _reachable_cache
    with _lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:
                pass
        _client = None
        _client_key = ""
        _unavailable_logged = False
    with _reachable_lock:
        # 探活缓存必须跟着一起清：换了 url 还拿旧结果，就会出现"连的是 A、
        # 报的是 B 的健康状态"这种极难查的错位。
        _reachable_cache = None
