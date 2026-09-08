"""把冒烟测试的 Redis 键空间挪到测试专用前缀，并在进程退出时清理干净。

**为什么需要这个模块**：``smoke_concurrency`` / ``smoke_server`` 走的是
``server/app.py`` 的真实缓存路径，键前缀取自配置（默认 ``rag4c:``）。本机
Redis 是**与其它应用共用**的实例（实测 ``dbsize=19``），于是一次开着
``RAG4C_REDIS_URL`` 的测试跑完，生产命名空间里就多出了这样两条：

    rag4c:cache:e1db5774…  ->  {"result": {"query": "测试问题", "answer": "",
                                "abstained": true, …}}

问句 ``测试问题`` 是一句**完全可能被真人输入**的中文，而缓存值是桩数据造
出来的空答案 + ``abstained: true``。缓存不看来路，只看键：15 分钟 TTL 内真
有人问了同一句，拿到的就是这条测试残留的空答案。这不是"不整洁"，是测试
数据污染线上应答——所以隔离在测试侧强制执行，而不是靠"记得起唯一问句"。

用法（必须在 ``import server.app`` **之前**调用，配置是 lru_cache 单例，
第一次 ``get_settings()`` 之后再改环境变量就晚了）::

    from _smoke_redis import isolate_redis_keyspace
    isolate_redis_keyspace("concurrency")
    bridge = importlib.import_module("server.app")

``scripts/run_tests.py`` 会为整轮测试统一指定一个前缀，子脚本检测到已在测
试前缀下就直接沿用，不再各自新建。
"""
from __future__ import annotations

import atexit
import os
import sys
from pathlib import Path

#: 测试键的统一根前缀。清理只允许发生在这个根下面——生产前缀 ``rag4c:``
#: 全程没有任何批量删除入口，这里也不例外。
TEST_PREFIX_ROOT = "rag4c-test:"

#: 环境变量名（对应 ``config.settings.RedisSettings.key_prefix``）
ENV_VAR = "RAG4C_REDIS_KEY_PREFIX"


def isolate_redis_keyspace(tag: str) -> str:
    """把本进程的 Redis 键前缀切到 ``rag4c-test:<tag>:`` 并登记退出清理。

    幂等：已经处在测试前缀下（通常是 ``run_tests.py`` 统一设好的）时原样
    沿用，不覆盖——同一轮测试共用一个前缀，清理也只需做一次。

    Args:
        tag: 用于区分脚本的短标签，仅出现在键名里。

    Returns:
        本进程实际生效的键前缀。
    """
    current = os.environ.get(ENV_VAR, "")
    if current.startswith(TEST_PREFIX_ROOT):
        return current
    prefix = f"{TEST_PREFIX_ROOT}{tag}:"
    os.environ[ENV_VAR] = prefix
    atexit.register(sweep, prefix)
    return prefix


def sweep(prefix: str) -> int:
    """删除 ``prefix*`` 下的全部键。

    Returns:
        删除条数；**Redis 没连上（含未配置）时返回 -1**，与"扫过了，一条都没有"
        的 0 明确区分。这个区分不是洁癖：调用方看到 0 会以为键空间干净了，而
        真相可能是本进程根本没配 ``RAG4C_REDIS_URL``、压根没去看——残留就这么
        留在了共用实例上。静默降级本身就是这个仓库反复在治的毛病。

    只接受 :data:`TEST_PREFIX_ROOT` 开头的前缀。这不是防御性冗余：本函数是
    仓库里唯一的批量删除入口，一旦被误传 ``rag4c:``（或空串）就会清掉与别人
    共用的那个实例上的真实数据，代价不可逆。

    用 ``scan_iter`` 而非 ``keys``：共用实例上 ``KEYS`` 会阻塞整个 Redis。
    """
    if not prefix.startswith(TEST_PREFIX_ROOT):
        raise ValueError(f"拒绝清理非测试前缀: {prefix!r}")
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from core import redis_client

        client = redis_client.get_client()
        if client is None:
            return -1
        removed = 0
        for k in client.scan_iter(match=f"{prefix}*", count=200):
            removed += int(client.delete(k) or 0)
        return removed
    except Exception:  # noqa: BLE001 - 清理失败不该让测试判负；键都带 TTL 会自灭
        return -1


__all__ = ["ENV_VAR", "TEST_PREFIX_ROOT", "isolate_redis_keyspace", "sweep"]
