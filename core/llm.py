"""OpenAI 兼容 LLM 客户端槽位。

设计目标：
- 用 openai SDK 驱动任意 OpenAI 兼容端点（Ollama / vLLM / 各类网关），
  通过配置中的 ``base_url`` 与 ``api_key`` 切换。
- openai 包为**可选依赖**：未安装时本模块仍可 import（惰性导入），
  只有真正发起调用时才报 :class:`LLMDependencyError`。
- ``chat_json`` 自带解析失败自动重试（1 次），仍失败则显式抛出
  :class:`ParseFallbackError`，**绝不静默返回 None**。
- ``chat`` 对瞬态异常（连接错误 / 超时 / 限流 / 服务端 5xx）做**指数退避
  重试**，策略来自 ``config.settings.retry``；非瞬态错误（如 400 参数错误）
  不重试，立即抛给调用方走 json 降级或报错。

业务槽位（rewrite / router_llm / generation / judge）都由本类承载，
各自持有一个 :class:`~config.settings.LlmSlotSettings` 实例。
"""
from __future__ import annotations

import json
import re
import threading
from typing import Any, Optional

from config.settings import LlmSlotSettings, get_settings
from core.retry import RetryPolicy, retry_call
from core.providers import ProviderRegistry


class LLMError(RuntimeError):
    """LLM 调用失败（网络错误、服务端错误、结果异常等）。"""


class LLMDependencyError(LLMError):
    """缺少可选依赖 openai 包。"""


class ParseFallbackError(LLMError):
    """chat_json 多次尝试仍无法解析出合法 JSON 对象。"""


def _extract_json_block(text: str) -> Optional[dict]:
    """从可能包裹 markdown 代码块的文本中提取 JSON 对象。

    顺序尝试：完整 JSON -> 去掉 ```json ... ``` 围栏 -> 首尾 ``{``/``}`` 切片。
    均失败返回 None。
    """
    text = text.strip()
    # 直接解析
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    # 去除围栏
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    if fenced:
        try:
            obj = json.loads(fenced.group(1).strip())
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            pass
    # 首尾切片
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


# ---------------------------------------------------------------------------
# LLM 重试策略（惰性探测 openai 瞬态异常类，保持离线可导入）
# ---------------------------------------------------------------------------

_RETRY_ON_CACHE: tuple | None = None


def _retry_policy() -> "RetryPolicy":
    """构造 LLM 重试策略（惰性探测 openai 瞬态异常类，保持离线可导入）。

    瞬态异常：连接错误 / 超时 / 限流 / 服务端 5xx；
    非瞬态（400 参数错误、401 鉴权等）不重试，立即抛给调用方降级。

    openai 未安装时退化为重试所有 Exception（此时实际调用必然先抛
    LLMDependencyError，重试路径基本不可达，仅作防御）。
    """
    global _RETRY_ON_CACHE
    if _RETRY_ON_CACHE is None:
        try:
            from openai import (
                APIConnectionError,
                APITimeoutError,
                InternalServerError,
                RateLimitError,
            )
            _RETRY_ON_CACHE = (
                APIConnectionError,
                APITimeoutError,
                RateLimitError,
                InternalServerError,
            )
        except ImportError:  # openai 未安装：退化重试所有 Exception
            _RETRY_ON_CACHE = (Exception,)
    policy = RetryPolicy.from_settings(get_settings().retry)
    policy.retry_on = _RETRY_ON_CACHE
    return policy


def _observe_retry_delay(attempt: int, exc: BaseException, delay: float) -> None:
    """on_retry 埋点：把退避延迟（秒）记入 metrics 直方图（惰性导入）。

    core.metrics 惰性导入；回调异常被 core.retry 吞掉，不影响重试流程。
    """
    from core.metrics import get_metrics

    get_metrics().observe("retry.llm.delay", delay)


def _record_usage(model: str, usage: Any, *, streamed: bool) -> None:
    """把一次调用的 token 用量记进 metrics（惰性导入，**永不抛**）。

    为什么这组计数器是必需的：这个项目在缓存上花了很大力气，而缓存对外的
    承诺是"省 token"。没有实测用量，省了多少就只能用「命中次数 × 拍脑袋的
    平均值」估算——那不是测量，是自证。有了它，命中率曲线和 token 曲线可以
    互相印证，缓存策略（TTL、语义阈值、按槽位缓存）才有得调。

    按 model 打标签而不是按槽位：槽位名没有传到这一层（``LlmSlotSettings``
    里也没有），而模型名恰好是费用的计价单位，基数也有界。

    用量拿不到就静默跳过：有些 OpenAI 兼容端点（部分 Ollama / 老版 vLLM）
    压根不返回 usage。为了一条埋点把已经成功的回答变成 500 是本末倒置。
    """
    if usage is None:
        return
    try:
        from core.metrics import get_metrics

        metrics = get_metrics()
        tags = {"model": model, "stream": "1" if streamed else "0"}
        for field, name in (
            ("prompt_tokens", "llm.tokens.prompt"),
            ("completion_tokens", "llm.tokens.completion"),
            ("total_tokens", "llm.tokens.total"),
        ):
            value = getattr(usage, field, None)
            if isinstance(value, (int, float)) and value > 0:
                metrics.incr(name, tags=tags, value=float(value))
        metrics.incr("llm.calls", tags=tags)
    except Exception:  # noqa: BLE001 - 埋点永远不该影响调用结果
        pass


def _record_cache_hit(model: str, envelope: dict) -> None:
    """记一次槽位缓存命中，以及**因此没花掉**的 token（惰性导入，永不抛）。

    ``llm.cache.saved.*`` 与 :func:`_record_usage` 的 ``llm.tokens.*`` 是同一
    把尺子量出来的：省下的数字就是当初真调那一次实际花掉的数字，原样存在
    缓存信封里。两条曲线可以直接相加对照，"缓存省了多少"从此是个可以拿出来
    看的量，而不是一句"命中率挺高的"。

    信封里没有用量（端点不返回 usage）时只记命中数，不往省量里编数。
    """
    try:
        from core.metrics import get_metrics

        metrics = get_metrics()
        tags = {"model": model}
        metrics.incr("llm.cache.hits", tags=tags)
        prompt = float(envelope.get("p") or 0.0)
        completion = float(envelope.get("c") or 0.0)
        for name, value in (
            ("llm.cache.saved.prompt", prompt),
            ("llm.cache.saved.completion", completion),
            ("llm.cache.saved.total", prompt + completion),
        ):
            if value > 0:
                metrics.incr(name, tags=tags, value=value)
    except Exception:  # noqa: BLE001 - 埋点永远不该影响调用结果
        pass


class LLMClient:
    """一个可重用的 LLM 客户端（对应一个 llm 槽位）。

    ``circuit`` 为可选熔断器（``core.circuit.CircuitBreaker``）：提供时，
    ``chat`` / ``chat_json`` 在调用前检查 ``allow()``（熔断打开则快速失败，
    不再耗尽超时×重试），调用后按结果记录 success / failure。为 None 时
    行为与接入前完全一致（下游故障由 retry + deadline 兜底）。
    """

    def __init__(self, config: LlmSlotSettings, circuit: Any = None) -> None:
        self.config = config
        self.circuit = circuit
        self._client: Any = None  # openai.OpenAI，惰性创建
        self._client_lock = threading.Lock()

    def _circuit_guard(self) -> None:
        """熔断前置检查：打开则快速失败（不发起下游调用）。"""
        if self.circuit is not None and not self.circuit.allow():
            raise LLMError(
                f"LLM 槽位熔断打开（{getattr(self.circuit, 'name', '?')} 冷却中），快速失败"
            )

    def _circuit_ok(self) -> None:
        if self.circuit is not None:
            self.circuit.record_success()

    def _circuit_fail(self) -> None:
        if self.circuit is not None:
            self.circuit.record_failure()

    # ------------------------------------------------------------------ #
    # 内部：惰性初始化 openai 客户端
    # ------------------------------------------------------------------ #
    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        with self._client_lock:
            if self._client is not None:
                return self._client
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - 依赖缺失路径
                raise LLMDependencyError(
                    "openai 包未安装。请执行 `pip install 'rag4c[llm]'` 或 `pip install openai>=1.40`。"
                ) from exc
            try:
                self._client = OpenAI(
                    base_url=self.config.base_url or None,
                    api_key=self.config.api_key or "not-set",
                    timeout=self.config.timeout,
                )
            except Exception as exc:
                raise LLMError(
                    f"初始化 OpenAI 客户端失败 base_url={self.config.base_url!r}: {exc}"
                ) from exc
            return self._client

    # ------------------------------------------------------------------ #
    # 基础对话
    # ------------------------------------------------------------------ #
    def chat(
        self,
        messages: list[dict],
        json_mode: bool = False,
        *,
        cache_ok: Any = None,
    ) -> str:
        """发起一次对话补全，返回纯文本内容。

        Args:
            messages: OpenAI 风格消息列表 ``[{"role": ..., "content": ...}]``。
            json_mode: 为 True 时请求服务端返回 JSON 对象
                （部分端点不支持 response_format，会自动降级重试一次）。
            cache_ok: 可选校验器 ``(str) -> bool``。只在本槽位开了缓存时有意义：
                缓存里读到的值校验不过就当未命中并就地作废，新算出来的值校验
                不过就不写。给它是因为 :meth:`chat_json` 会遇到"模型返回的
                不是合法 JSON"——那种输出一旦被缓存就会黏住整个 TTL，把一次
                偶发失败放大成持续一个 TTL 的双倍调用。

        Returns:
            模型返回的文本（content）。空串表示模型未返回内容。

        Raises:
            LLMError: 调用失败。
        """
        self._circuit_guard()  # 熔断打开则快速失败
        client = self._ensure_client()
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if self.config.seed is not None:
            kwargs["seed"] = self.config.seed
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        cached, digest = self._cache_lookup(messages, json_mode, cache_ok)
        if cached is not None:
            return cached

        try:
            policy = _retry_policy()
            try:
                resp = retry_call(
                    client.chat.completions.create, **kwargs,
                    policy=policy, on_retry=_observe_retry_delay,
                )
            except Exception:
                if json_mode and "response_format" in kwargs:
                    # 端点不支持 response_format（如部分 Ollama 版本），降级重试；
                    # response_format 被 400 拒绝属非瞬态，不重试直接进入此降级
                    kwargs.pop("response_format", None)
                    resp = retry_call(
                        client.chat.completions.create, **kwargs,
                        policy=policy, on_retry=_observe_retry_delay,
                    )
                else:
                    raise
        except Exception as exc:  # 原有 LLMError 包装保持不变（__cause__ 契约）
            self._circuit_fail()
            raise LLMError(f"LLM 调用失败: {exc}") from exc

        content = resp.choices[0].message.content if resp.choices else None
        usage = getattr(resp, "usage", None)
        _record_usage(self.config.model, usage, streamed=False)
        text = content or ""
        self._cache_store(digest, text, cache_ok, usage)
        self._circuit_ok()
        return text

    # ------------------------------------------------------------------ #
    # 内部：槽位结果缓存（见 core.llm_cache）
    # ------------------------------------------------------------------ #
    def _cache_lookup(
        self, messages: list[dict], json_mode: bool, cache_ok: Any
    ) -> tuple[Optional[str], Optional[str]]:
        """查槽位缓存。

        Returns:
            ``(命中的文本或 None, 摘要或 None)``。摘要为 None 表示本槽位没开
            缓存，调用方据此跳过写入——把"开没开"的判断收在这一处，免得读写
            两边各判一次、哪天改配置字段名时漏改一边。

        整个函数被 try 包住：缓存层把一次本来能成功的调用变成异常是本末倒置。
        """
        ttl = getattr(self.config, "cache_ttl_s", 0.0) or 0.0
        if ttl <= 0:
            return None, None
        try:
            from core.llm_cache import _digest, get_llm_cache

            digest = _digest(
                model=self.config.model,
                base_url=self.config.base_url,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                seed=self.config.seed,
                json_mode=json_mode,
                messages=messages,
            )
            cache = get_llm_cache()
            env = cache.get_digest(digest)
            text = env.get("t") if env else None
            if text is not None and cache_ok is not None and not cache_ok(text):
                # 缓存里躺着一份用不了的输出（多半是当初写进去时还没有校验器，
                # 或者换了模型但键没变）。就地作废，本次照常真调一次。
                cache.drop_digest(digest)
                text = None
            if text is not None:
                _record_cache_hit(self.config.model, env or {})
            return text, digest
        except Exception:  # noqa: BLE001 - 缓存永远不该影响调用结果
            return None, None

    def _cache_store(
        self,
        digest: Optional[str],
        text: str,
        cache_ok: Any,
        usage: Any = None,
    ) -> None:
        """把结果写进槽位缓存。``digest`` 为 None（没开缓存）时什么都不做。

        顺带把本次的真实用量存进信封，将来命中时就能报出**实测**省了多少
        token，而不是拿命中数去估。用量拿不到（不少兼容端点不返回 usage）
        就存 0：省了多少统计不出来，总好过缓存本身不生效。
        """
        if not digest or not text:
            return
        try:
            if cache_ok is not None and not cache_ok(text):
                return
            from core.llm_cache import LlmCache, get_llm_cache

            get_llm_cache().put_digest(
                digest,
                LlmCache.envelope(
                    text,
                    float(getattr(usage, "prompt_tokens", 0) or 0),
                    float(getattr(usage, "completion_tokens", 0) or 0),
                ),
                ttl_s=self.config.cache_ttl_s,
            )
        except Exception:  # noqa: BLE001 - 同上
            pass

    # ------------------------------------------------------------------ #
    # 流式对话（SSE 用）
    # ------------------------------------------------------------------ #
    def chat_stream(self, messages: list[dict]):
        """流式对话补全：逐段产出文本增量（生成器）。

        首包建立失败走与 chat() 相同的重试策略（重试的是「建立流连接」，
        而非重放已产出的 token）；流中失败直接抛出 LLMError，由调用方决定
        回退策略（如改用非流式 chat 重试）。

        Args:
            messages: OpenAI 风格消息列表。

        Yields:
            模型产出的文本增量（str）。

        Raises:
            LLMError: 调用失败。
        """
        client = self._ensure_client()
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": True,
            # 要用量。流式默认不返回 usage，只有显式打开这个开关，服务端才会在
            # 流末尾补一个 choices 为空、只带 usage 的收尾块。不打开的话，前端
            # 走的这条主路径（SSE）在 token 账本上是**完全不可见**的——而它恰好
            # 是流量最大的那条，缓存省了多少 token 也就无从谈起。
            # 兼容性由下面的降级重试兜底（有些端点不认 stream_options）。
            "stream_options": {"include_usage": True},
        }
        if self.config.seed is not None:
            kwargs["seed"] = self.config.seed
        stream = None
        try:
            policy = _retry_policy()
            try:
                stream = retry_call(
                    client.chat.completions.create, **kwargs,
                    policy=policy, on_retry=_observe_retry_delay,
                )
            except Exception:
                # 端点不认 stream_options（部分 Ollama / 老网关会直接 400，
                # 属非瞬态，重试多少次都一样）。降级重试一次，代价只是这一路
                # 没有 token 账本——与"整条流式路径不可用"不是一个量级。
                # 这与 chat() 里 response_format 的降级是同一套处理。
                if "stream_options" not in kwargs:
                    raise
                kwargs.pop("stream_options", None)
                stream = retry_call(
                    client.chat.completions.create, **kwargs,
                    policy=policy, on_retry=_observe_retry_delay,
                )
            for chunk in stream:
                # 收尾块：choices 为空、只带 usage。它必须在这里被认出来，
                # 否则下面的 choices[0] 取不到东西，用量就被静默丢掉了。
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    _record_usage(self.config.model, usage, streamed=True)
                choices = getattr(chunk, "choices", None) or []
                delta = choices[0].delta if choices else None
                content = getattr(delta, "content", None) if delta else None
                if content:
                    yield content
        except GeneratorExit:
            # 消费方提前关闭（如客户端断开 / 停止生成）：关闭底层流释放连接
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            raise
        except Exception as exc:  # 原有 LLMError 包装保持不变
            raise LLMError(f"LLM 流式调用失败: {exc}") from exc

    # ------------------------------------------------------------------ #
    # JSON 对话（带自动重试）
    # ------------------------------------------------------------------ #
    def chat_json(self, messages: list[dict], schema_hint: str | None = None) -> dict:
        """要求模型返回 JSON 对象，并自动解析。

        解析失败会带纠正指令重试**一次**；仍失败则抛出
        :class:`ParseFallbackError`（绝不静默返回 None）。

        Args:
            messages: OpenAI 风格消息列表。
            schema_hint: 可选的 JSON schema 提示（拼入 system 消息）。

        Returns:
            解析成功的 dict。

        Raises:
            LLMError: 底层调用失败。
            ParseFallbackError: 两次尝试均无法解析出合法 JSON 对象。
        """
        base_msgs = list(messages)
        if schema_hint:
            base_msgs = base_msgs + [
                {
                    "role": "system",
                    "content": (
                        "You must respond with ONLY a single valid JSON object "
                        f"matching this schema: {schema_hint}. "
                        "Do not wrap it in markdown fences and do not add commentary."
                    ),
                }
            ]

        last_text = ""
        for attempt in range(2):  # 首次 + 1 次重试
            msgs = list(base_msgs)
            if attempt > 0:
                msgs = msgs + [
                    {"role": "assistant", "content": last_text},
                    {
                        "role": "user",
                        "content": (
                            "The previous response was not valid JSON. "
                            "Return ONLY a single valid JSON object, no markdown, no extra text."
                        ),
                    },
                ]
            try:
                # 带上校验器：这条路径上"模型返回的不是合法 JSON"是常态而非
                # 异常，而槽位缓存恰好在这几个 JSON 槽位上默认开着。不校验
                # 的话，一次偶发的坏输出会被缓存黏住整个 TTL——之后每个请求
                # 都先吃一发命中的坏值、再真调一次修复，稳定地花双份钱。
                last_text = self.chat(
                    msgs, json_mode=True,
                    cache_ok=lambda t: _extract_json_block(t) is not None,
                )
            except LLMError:
                # 底层调用失败不属于"解析失败"，重试一次后原样抛出
                if attempt == 1:
                    raise
                continue
            parsed = _extract_json_block(last_text)
            if parsed is not None:
                return parsed

        raise ParseFallbackError(
            f"模型未返回可解析的 JSON（model={self.config.model}, 已重试 1 次）。"
            f" 原始输出片段: {last_text[:300]!r}"
        )


LLM_PROVIDERS: ProviderRegistry[LlmSlotSettings, LLMClient] = ProviderRegistry("llm")
LLM_PROVIDERS.register("openai_compatible", LLMClient)
LLM_PROVIDERS.register("openai", LLMClient)


def create_client(cfg: LlmSlotSettings, circuit: Any = None) -> LLMClient:
    """工厂函数：由槽位配置创建 LLMClient。

    ``circuit`` 为可选熔断器（``core.circuit.CircuitBreaker``）：提供时附加到
    客户端，``chat`` / ``chat_json`` 在熔断打开时快速失败（不发起下游调用）。
    None 时行为与接入前完全一致。

    Usage:
        from config.settings import get_settings
        from core.llm import create_client

        generation = create_client(get_settings().llm.generation)
    """
    client = LLM_PROVIDERS.create(cfg.provider, cfg)
    if circuit is not None:
        client.circuit = circuit
    return client


__all__ = [
    "LLMError",
    "LLMDependencyError",
    "ParseFallbackError",
    "LLMClient",
    "LLM_PROVIDERS",
    "create_client",
]
