"""LLM 槽位结果缓存（L1 进程内 LRU + L2 Redis）。

## 缓的是什么

一次 ``chat()`` 调用的**原始返回文本**，键是整个请求的摘要。开在哪些槽位由
``LlmSlotSettings.cache_ttl_s`` 决定（``0`` = 不缓），默认只有检索期那六个
轻量槽位开着——理由写在 :data:`config.settings._PREPROC_CACHE_TTL_S` 上。

## 为什么键里不需要语料代次

答案缓存（:mod:`core.query_cache`）必须带代次，因为它缓的是"整条链路的产物"，
而链路的输入包含检索结果，检索结果会随语料变。这一层不一样：它缓的是
``chat(请求) -> 文本``，而**请求本身就是全部输入**。语料变了，如果会影响这次
调用，那一定是通过 prompt 影响的，prompt 一变摘要就变，旧值自然够不着；如果
不影响 prompt，那它本来就不该让结果失效。同 :mod:`core.embed_cache` 的论证。

正因如此，这一层对 ``cache_epoch.bump()`` **不响应**，这是特性不是遗漏。

## 键里必须有的东西

模型名、端点、温度、max_tokens、seed、json_mode，以及渲染后的完整消息列表。
前几项都住在 ``LlmSlotSettings`` 上而不在调用参数里，配置热更新能把它们改掉
**而 prompt 一个字不变**——不进键的话，改完配置拿到的还是旧模型的输出，不报
错也查不出来。端点也要进：同一个模型名在不同网关后面可能是不同的权重。

## 为什么不按槽位名分区

不放槽位名，键就只由"请求长什么样"决定。两个槽位若真拼出了完全相同的请求，
那它们问的就是同一件事，共用结果是对的。反过来，放了槽位名只会让配置成同一
模型的槽位各缓一份。调试时想区分来源看 metrics 的 ``slot`` 标签即可。

## 降级

骨架在 :mod:`core.two_level_cache`：**加速件，不是依赖件**，Redis 缺席或出错
时静默退回纯 L1，所有出口不抛异常。
"""
from __future__ import annotations

import hashlib
import json
import threading
from typing import Any, Optional

from core.two_level_cache import TwoLevelCache, env_float, env_int

#: 进程内 LRU 条数上限。这些值是短文本（改写后的问句、几条子查询、一段
#: 假设文档），按平均 1KB 估，1024 条约 1MB。
_L1_MAX = 1024

#: 写入方没给 TTL 时的兜底存活时长。正常路径上 TTL 总是由槽位的
#: ``cache_ttl_s`` 显式给出，这个值只在直接调 ``put_digest`` 的场景（测试、
#: 将来的其它调用方）生效，防止漏给 TTL 变成"永不过期的键"——那在共用且
#: ``maxmemory=0`` 的实例上是只进不出的。
_DEFAULT_TTL_S = 1800.0


def _digest(
    *,
    model: str,
    base_url: str,
    temperature: float,
    max_tokens: int,
    seed: Any,
    json_mode: bool,
    messages: list[dict],
    enable_thinking: Any = None,
) -> str:
    """把一次请求压成 Redis 键名分量。

    用 ``json.dumps(..., sort_keys=True)`` 而不是 ``str(messages)``：后者依赖
    dict 的插入顺序，同样内容的两条消息只因构造顺序不同就会算出不同的摘要，
    命中率会莫名其妙地低，而且没人查得出为什么。``ensure_ascii=False`` 是为
    了让中文按原样进摘要（不影响正确性，只是别把体积放大三倍）。

    不截断摘要：碰撞在这里的表现是"某次提问拿到了另一次提问的模型输出"，
    静默、且几乎无法定位。省那几十字节不值。
    """
    payload = json.dumps(
        {
            "m": model,
            "u": base_url,
            "t": temperature,
            "x": max_tokens,
            "s": seed,
            "j": json_mode,
            # 思考链开关必须进键：同一份 prompt 在开关翻转后是两个不同的
            # 请求（关掉思考的答案短得多、引用编号也不同），共用一个键等于
            # 让一次配置变更静默地继续吃旧值。
            "th": enable_thinking,
            "msgs": messages,
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,      # 消息里混进不可序列化的东西时退化成 repr，不抛
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class LlmCache(TwoLevelCache[dict]):
    """LLM 调用结果的两级缓存。线程安全，所有出口不抛异常。

    值不是裸文本而是一个小信封 ``{"t": 文本, "p": prompt_tokens,
    "c": completion_tokens}``。多存两个整数是为了让"缓存省了多少 token"成为
    **实测**而不是估算：命中时把当初真实花掉的用量原样报进 metrics，就能和
    :func:`core.llm._record_usage` 记的实际消耗对上账。否则只能拿"命中次数 ×
    拍脑袋的平均值"去说省了多少——那不是测量，是自证。

    与 :class:`~core.embed_cache.EmbedCache` 的另一个区别：TTL 不是实例级的，
    而是每次写入按槽位给（``put_digest`` 的 ``ttl_s``）。同一个进程里六个
    槽位的 ``cache_ttl_s`` 可以各不相同，共用一个实例才能共用 L1 容量预算。
    """

    namespace = "llm"

    def __init__(self, *, l1_max: int | None = None) -> None:
        super().__init__(
            l1_max=l1_max if l1_max is not None else env_int("RAG4C_LLM_CACHE_MAX", _L1_MAX),
            l2_ttl_s=env_float("RAG4C_LLM_CACHE_TTL_S", _DEFAULT_TTL_S),
        )

    # -- 骨架钩子 ------------------------------------------------------- #
    def _encode(self, value: dict) -> bytes:
        return json.dumps(value, ensure_ascii=False).encode("utf-8")

    def _decode(self, raw: bytes) -> Optional[dict]:
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None      # 键被别的东西占了 / 信封换过版本；当作未命中
        if not isinstance(obj, dict) or not isinstance(obj.get("t"), str) or not obj["t"]:
            return None
        return obj

    def _copy(self, value: dict) -> dict:
        return dict(value)

    # -- 领域接口 ------------------------------------------------------- #
    @staticmethod
    def envelope(text: str, prompt_tokens: float = 0.0, completion_tokens: float = 0.0) -> dict:
        """按信封格式打包一次调用的结果。"""
        return {"t": text, "p": prompt_tokens, "c": completion_tokens}


_instance: Optional[LlmCache] = None
_instance_lock = threading.Lock()


def get_llm_cache() -> LlmCache:
    """进程内单例。

    单例而不是挂在 :class:`~core.llm.LLMClient` 上：配置热更新会重建整条
    管线（``rag.get_pipeline``），挂在客户端上等于每次热更新都清空缓存。
    而键里已经带了模型、端点和全部采样参数，热更新改了什么自然就换键。
    """
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = LlmCache()
    return _instance


def reset_llm_cache() -> None:
    """测试钩子：丢弃单例（不碰 Redis）。"""
    global _instance
    with _instance_lock:
        _instance = None


__all__ = ["LlmCache", "get_llm_cache", "reset_llm_cache", "_digest"]
