"""单次问答的 LLM 用量台账（calls / token / 缓存省下的 token / 估算成本）。

为什么要有这一层：全局指标里已经有 ``llm.tokens.*`` 和 ``llm.cache.saved.*``，
但它们是**进程累计计数器**——能回答"这一小时一共烧了多少"，回答不了
"这一问花了多少"。而成本权衡、预算规划、以及"开这个增强值不值"这类判断，
需要的恰恰是后者。缓存那一层已经把"省了多少 token"测出来了（不是拿命中率估的），
这里只是把它按查询归拢起来。

设计要点：

- **旁路**：台账走 contextvar，写入一律包在 try 里，任何异常都被吞掉。
  埋点绝不能把一次本来成功的回答变成 500。
- **并发安全**：contextvar 在 asyncio / 线程池里各任务互不串扰，
  A 查询的用量不会记到 B 查询头上。
- **可配置**：价格表由配置提供（``llm.price_table``），本模块**不内置任何
  价格**——价格会变，写死就是在骗人。没配价格的模型只计 token、不计金额，
  并在 ``unpriced_total_tokens`` 里如实报出来。
- **可关**：``usage_scope()`` 不调用就没有台账，零开销；关掉开关后
  ``QueryResult.usage`` 为空字典，前端已有逻辑不受影响。
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Optional

# 价格表形状：``{model: {"prompt": 每 1K token 单价, "completion": 每 1K token 单价}}``
# 货币由部署方自定（人民币 / 美元 / 内部计价单位都行），本模块不做换算。
PriceTable = Mapping[str, Mapping[str, float]]

_USAGE_VAR: ContextVar[Optional["UsageLedger"]] = ContextVar(
    "rag4c_llm_usage_ledger", default=None
)


@dataclass
class UsageEntry:
    """一个（模型 × 槽位）组合的用量累计。"""

    model: str = ""
    slot: str = ""
    calls: int = 0
    prompt_tokens: float = 0.0
    completion_tokens: float = 0.0
    #: 命中槽位缓存的次数（这些调用没真的发请求）
    cached_calls: int = 0
    saved_prompt_tokens: float = 0.0
    saved_completion_tokens: float = 0.0
    failures: int = 0

    @property
    def total_tokens(self) -> float:
        return self.prompt_tokens + self.completion_tokens

    @property
    def saved_total_tokens(self) -> float:
        return self.saved_prompt_tokens + self.saved_completion_tokens

    def cost(self, prices: PriceTable | None = None) -> tuple[float, float]:
        """返回 ``(金额, 未能计价的 token 数)``。

        没配价格的模型金额记 0，但 token 如实计入第二个返回值——宁可报
        "这笔算不出钱"，也不要编一个数出来。
        """
        if not prices:
            return 0.0, float(self.total_tokens)
        table = prices.get(self.model) or prices.get("*") or {}
        prompt_price = table.get("prompt")
        completion_price = table.get("completion")
        if prompt_price is None or completion_price is None:
            return 0.0, float(self.total_tokens)
        amount = (
            self.prompt_tokens / 1000.0 * float(prompt_price)
            + self.completion_tokens / 1000.0 * float(completion_price)
        )
        return amount, 0.0

    def as_dict(self, prices: PriceTable | None = None) -> dict[str, Any]:
        amount, unpriced = self.cost(prices)
        return {
            "model": self.model,
            "slot": self.slot,
            "calls": self.calls,
            "cached_calls": self.cached_calls,
            "failures": self.failures,
            "prompt_tokens": round(self.prompt_tokens, 3),
            "completion_tokens": round(self.completion_tokens, 3),
            "total_tokens": round(self.total_tokens, 3),
            "saved_prompt_tokens": round(self.saved_prompt_tokens, 3),
            "saved_completion_tokens": round(self.saved_completion_tokens, 3),
            "saved_total_tokens": round(self.saved_total_tokens, 3),
            "cost": round(amount, 6),
            "unpriced_total_tokens": round(unpriced, 3),
        }


@dataclass
class UsageLedger:
    """一次问答（或一次入库任务）的用量台账。"""

    entries: dict[tuple[str, str], UsageEntry] = field(default_factory=dict)

    def _entry(self, model: str, slot: str = "") -> UsageEntry:
        key = (model or "unknown", slot or "")
        entry = self.entries.get(key)
        if entry is None:
            entry = UsageEntry(model=key[0], slot=key[1])
            self.entries[key] = entry
        return entry

    # -- 写入 -------------------------------------------------------------
    def record_call(self, model: str, slot: str = "", usage: Any = None) -> None:
        """记一次真实调用（``usage`` 为 OpenAI 风格的用量对象或 None）。"""
        entry = self._entry(model, slot)
        entry.calls += 1
        if usage is None:
            return
        for attr, target in (
            ("prompt_tokens", "prompt_tokens"),
            ("completion_tokens", "completion_tokens"),
            ("total_tokens", None),
        ):
            value = getattr(usage, attr, None)
            if isinstance(value, (int, float)) and value > 0:
                if target:
                    setattr(entry, target, getattr(entry, target) + float(value))

    def record_cache_hit(
        self, model: str, slot: str = "", *, prompt: float = 0.0, completion: float = 0.0
    ) -> None:
        """记一次缓存命中，以及因此没花掉的 token（实测值，不是估算）。"""
        entry = self._entry(model, slot)
        entry.cached_calls += 1
        entry.saved_prompt_tokens += float(prompt or 0.0)
        entry.saved_completion_tokens += float(completion or 0.0)

    def record_failure(self, model: str, slot: str = "") -> None:
        self._entry(model, slot).failures += 1

    # -- 读取 -------------------------------------------------------------
    def totals(self) -> UsageEntry:
        total = UsageEntry(model="*", slot="*")
        for entry in self.entries.values():
            total.calls += entry.calls
            total.cached_calls += entry.cached_calls
            total.failures += entry.failures
            total.prompt_tokens += entry.prompt_tokens
            total.completion_tokens += entry.completion_tokens
            total.saved_prompt_tokens += entry.saved_prompt_tokens
            total.saved_completion_tokens += entry.saved_completion_tokens
        return total

    def as_dict(self, prices: PriceTable | None = None) -> dict[str, Any]:
        """落成可 JSON 序列化的台账（给 ``QueryResult.usage`` 用）。"""
        total = self.totals()
        # 金额必须**逐条**算再相加：汇总条目的 model 是 "*"，拿它去查价格表
        # 只会命中通配价（或者什么都查不到），把按模型精确定价的部分全抹掉。
        amount = 0.0
        unpriced = 0.0
        for entry in self.entries.values():
            entry_amount, entry_unpriced = entry.cost(prices)
            amount += entry_amount
            unpriced += entry_unpriced
        return {
            "calls": total.calls,
            "cached_calls": total.cached_calls,
            "failures": total.failures,
            "prompt_tokens": round(total.prompt_tokens, 3),
            "completion_tokens": round(total.completion_tokens, 3),
            "total_tokens": round(total.total_tokens, 3),
            # 缓存省下的部分（与上面同一把尺子：当初真调那一次实际花掉的数）
            "saved_prompt_tokens": round(total.saved_prompt_tokens, 3),
            "saved_completion_tokens": round(total.saved_completion_tokens, 3),
            "saved_total_tokens": round(total.saved_total_tokens, 3),
            "cost": round(amount, 6),
            "cost_priced": bool(prices) and unpriced <= 0,
            "unpriced_total_tokens": round(unpriced, 3),
            "by_slot": [
                entry.as_dict(prices)
                for entry in sorted(
                    self.entries.values(), key=lambda e: (-e.total_tokens, e.slot, e.model)
                )
            ],
        }


@contextmanager
def usage_scope() -> Iterator[UsageLedger]:
    """进入一次用量台账会话（退出自动解绑）。

    Usage::

        with usage_scope() as ledger:
            result = _answer_sequential(...)
            result.usage = ledger.as_dict(prices)
    """
    ledger = UsageLedger()
    token: Token = _USAGE_VAR.set(ledger)
    try:
        yield ledger
    finally:
        _USAGE_VAR.reset(token)


def current_ledger() -> Optional[UsageLedger]:
    """当前上下文绑定的台账；没有开 :func:`usage_scope` 时为 None。"""
    return _USAGE_VAR.get()


def iter_with_usage(gen: Iterator[Any], ledger: Optional[UsageLedger] = None) -> Iterator[Any]:
    """把一个生成器包进台账上下文（流式路径专用）。

    为什么不能直接用 ``with usage_scope(): yield from gen``：SSE 的生成器是**被
    驱动方逐段消费**的，每一段可能在**不同的线程/上下文**里恢复——contextvar
    的值不会跟着走，于是第 2 段之后的埋点就找不到台账了。

    这里改成**每次恢复后重新绑定**：内层生成器的代码永远紧跟着本函数的
    ``set()`` 执行，因此无论它被哪个线程恢复，看到的都是同一个台账。
    并发的多条流各自持有自己的 wrapper + ledger，互不串账。
    """
    if ledger is None:
        ledger = UsageLedger()
    # 首次推进前就要挂上：内层第一段代码跑在 `for` 的第一次 next() 里，
    # 等到拿到第一个事件再 set，那一段的用量就已经漏记了。
    token: Token = _USAGE_VAR.set(ledger)
    try:
        for event in gen:
            yield event
            # 让出后可能换了上下文：恢复的第一件事就是把台账重新挂上
            _USAGE_VAR.set(ledger)
    finally:
        try:
            _USAGE_VAR.reset(token)
        except ValueError:
            # token 属于另一个上下文（生成器被换线程恢复过），无从 reset；
            # 这里的清理是尽力而为，重复 set 不会累积状态。
            pass


def record_call(model: str, slot: str = "", usage: Any = None) -> None:
    """旁路写入：有台账才记，且任何异常都吞掉（埋点不得影响主流程）。"""
    try:
        ledger = current_ledger()
        if ledger is not None:
            ledger.record_call(model, slot, usage)
    except Exception:  # noqa: BLE001 - 埋点永远不该影响调用结果
        pass


def record_cache_hit(
    model: str, slot: str = "", *, prompt: float = 0.0, completion: float = 0.0
) -> None:
    try:
        ledger = current_ledger()
        if ledger is not None:
            ledger.record_cache_hit(model, slot, prompt=prompt, completion=completion)
    except Exception:  # noqa: BLE001
        pass


def record_failure(model: str, slot: str = "") -> None:
    try:
        ledger = current_ledger()
        if ledger is not None:
            ledger.record_failure(model, slot)
    except Exception:  # noqa: BLE001
        pass


def finish(
    ledger: Optional["UsageLedger"],
    *,
    scope: str = "",
    prices: PriceTable | None = None,
    metrics: Any = None,
    logger: Any = None,
) -> dict[str, Any]:
    """台账收尾：算 payload -> 写全局指标 -> 打一条日志（三步都可失败可退）。

    问答路径把 payload 挂到 ``QueryResult.usage``（见 ``rag._attach_usage``）；
    入库路径没有这样的返回字段，就靠这里的**指标 + 日志**留痕——
    "这篇文档入库烧了多少 token"因此变成一个可以在监控里看到的量，
    而不是只能靠猜。

    Args:
        ledger: :func:`usage_scope` 给出的台账；None 时返回空字典。
        scope: 指标标签（如 ``ingest`` / ``query``），区分同一进程里的不同场景。
        prices: 价格表；None 时金额记 0 并如实报未计价 token。
        metrics: ``core.metrics.MetricsRegistry``；None 时惰性取全局单例。
        logger: 标准 logger；None 时不打日志。

    Returns:
        与 :meth:`UsageLedger.as_dict` 同构的字典（失败时为空字典）。
    """
    if ledger is None:
        return {}
    try:
        payload = ledger.as_dict(prices)
    except Exception:  # noqa: BLE001 - 台账永远不该影响业务结果
        return {}
    try:
        registry = metrics
        if registry is None:
            from core.metrics import get_metrics

            registry = get_metrics()
        tags = {"scope": scope or "unknown"}
        registry.observe("usage.tokens.total", float(payload.get("total_tokens", 0) or 0), tags=tags)
        registry.observe("usage.saved_tokens.total", float(payload.get("saved_total_tokens", 0) or 0), tags=tags)
        registry.incr("usage.calls", tags=tags, value=float(payload.get("calls", 0) or 0))
        if payload.get("failures"):
            registry.incr("usage.failures", tags=tags, value=float(payload["failures"]))
    except Exception:  # noqa: BLE001
        pass
    if logger is not None:
        try:
            logger.info(
                "LLM 用量[%s]: calls=%s total_tokens=%s 缓存省下=%s cost=%s by_slot=%s",
                scope or "unknown",
                payload.get("calls"),
                payload.get("total_tokens"),
                payload.get("saved_total_tokens"),
                payload.get("cost"),
                [(item["slot"], item["total_tokens"]) for item in payload.get("by_slot", [])],
            )
        except Exception:  # noqa: BLE001
            pass
    return payload


__all__ = [
    "PriceTable",
    "UsageEntry",
    "UsageLedger",
    "current_ledger",
    "finish",
    "record_cache_hit",
    "record_call",
    "record_failure",
    "usage_scope",
]
