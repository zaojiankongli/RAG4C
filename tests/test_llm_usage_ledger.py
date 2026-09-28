"""单次问答 LLM 用量台账的离线测试（零网络）。

钉住五件事：账算得对、槽位能归属、缓存省下的 token 是实测值、
没配价格时不编造金额、并发上下文不串账。外加一条接线守卫：
``QueryResult`` 必须带 usage 字段，且默认空字典（老调用方零影响）。
"""
from __future__ import annotations

import threading
from typing import Any

import pytest

from core.llm_usage import (
    _USAGE_VAR,
    UsageLedger,
    current_ledger,
    iter_with_usage,
    record_cache_hit,
    record_call,
    record_failure,
    usage_scope,
)
from models.schemas import QueryResult


class _FakeUsage:
    def __init__(self, prompt: int, completion: int) -> None:
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = prompt + completion


def test_ledger_accumulates_calls_tokens_and_savings():
    with usage_scope() as ledger:
        record_call("m1", "generation", _FakeUsage(1000, 200))
        record_call("m1", "generation", _FakeUsage(500, 100))
        record_call("m2", "judge", _FakeUsage(300, 50))
        record_cache_hit("m1", "generation", prompt=1000, completion=200)
        record_failure("m2", "judge")

    payload = ledger.as_dict()
    assert payload["calls"] == 3
    assert payload["prompt_tokens"] == 1800
    assert payload["completion_tokens"] == 350
    assert payload["total_tokens"] == 2150
    # 缓存省下的是"当初真调那一次实际花掉的数"，不是命中率乘平均值
    assert payload["cached_calls"] == 1
    assert payload["saved_total_tokens"] == 1200
    assert payload["failures"] == 1


def test_ledger_splits_by_slot_when_models_are_shared():
    """同一模型被多个槽位共用时，仍要能分出谁烧的——这是按 model 打标签做不到的。"""
    with usage_scope() as ledger:
        record_call("same-model", "generation", _FakeUsage(100, 10))
        record_call("same-model", "judge", _FakeUsage(20, 2))

    by_slot = {item["slot"]: item["total_tokens"] for item in ledger.as_dict()["by_slot"]}
    assert by_slot == {"generation": 110, "judge": 22}


def test_cost_is_zero_without_prices_and_reports_unpriced_tokens():
    with usage_scope() as ledger:
        record_call("m1", "generation", _FakeUsage(1000, 500))
    payload = ledger.as_dict()
    assert payload["cost"] == 0.0
    assert payload["cost_priced"] is False
    assert payload["unpriced_total_tokens"] == 1500  # 如实报"算不出钱"，不编数


def test_cost_uses_per_1k_price_table():
    prices = {"m1": {"prompt": 2.0, "completion": 6.0}}
    with usage_scope() as ledger:
        record_call("m1", "generation", _FakeUsage(1000, 500))
    payload = ledger.as_dict(prices)
    assert payload["cost"] == pytest.approx(2.0 + 3.0)
    assert payload["cost_priced"] is True
    assert payload["unpriced_total_tokens"] == 0


def test_wildcard_price_applies_to_unlisted_models():
    prices = {"*": {"prompt": 1.0, "completion": 1.0}}
    entry = UsageLedger()
    entry.record_call("unknown-model", "generation", _FakeUsage(1000, 1000))
    assert entry.as_dict(prices)["cost"] == pytest.approx(2.0)


def test_usage_scope_is_isolated_between_threads():
    """并发不串账：A 线程的用量不能算到 B 线程头上。"""
    results: dict[str, int] = {}

    def worker(name: str, tokens: int) -> None:
        with usage_scope() as ledger:
            record_call("m", name, _FakeUsage(tokens, 0))
            results[name] = int(ledger.as_dict()["total_tokens"])

    threads = [
        threading.Thread(target=worker, args=("a", 10)),
        threading.Thread(target=worker, args=("b", 999)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == {"a": 10, "b": 999}


def test_no_scope_outside_usage_scope():
    assert current_ledger() is None
    # 无台账时写入必须静默成功（旁路埋点不得抛）
    record_call("m", "generation", _FakeUsage(1, 1))
    record_cache_hit("m", "generation", prompt=1, completion=1)
    record_failure("m", "generation")


def test_query_result_has_usage_field_defaulting_to_empty():
    """老调用方不传 usage 时行为不变（空字典），新字段不破坏既有契约。"""
    result = QueryResult(query="q", answer="a")
    assert result.usage == {}
    assert QueryResult(query="q", answer="a", usage={"calls": 1}).usage["calls"] == 1


def test_stream_wrapper_keeps_the_ledger_across_yields():
    """SSE 的生成器会被逐段消费，每段可能在不同上下文恢复；台账不能因此丢。

    这条测试模拟"每次恢复时上下文都被清空"的最坏情况：只要 wrapper 在恢复后
    重新绑定，内层代码就一定能看到同一个台账。
    """

    def inner():
        for i in range(3):
            record_call("m", "generation", _FakeUsage(10, 1))
            yield {"type": "phase", "i": i}
        yield {"type": "done", "result": {"answer": "答案"}}

    ledger = UsageLedger()
    events = []
    stream = iter_with_usage(inner(), ledger)
    for event in stream:
        events.append(event)
        # 消费侧把上下文清掉：下一次 next() 恢复时台账必须还在
        _USAGE_VAR.set(None)

    assert [e.get("type") for e in events] == ["phase", "phase", "phase", "done"]
    assert ledger.as_dict()["calls"] == 3
    assert ledger.as_dict()["total_tokens"] == 33


def test_two_concurrent_streams_do_not_mix_ledgers():
    """两条并行的流各记各的账（每条流一个 ledger，交错推进也不串）。"""

    def inner(name, tokens):
        record_call("m", name, _FakeUsage(tokens, 0))
        yield {"type": "phase"}
        record_call("m", name, _FakeUsage(tokens, 0))
        yield {"type": "done", "result": {}}

    ledger_a, ledger_b = UsageLedger(), UsageLedger()
    streams = [
        iter_with_usage(inner("a", 5), ledger_a),
        iter_with_usage(inner("b", 500), ledger_b),
    ]
    for stream in streams:
        next(stream)  # 各自推进一段，交错执行
    for stream in streams:
        list(stream)
    assert ledger_a.as_dict()["total_tokens"] == 10
    assert ledger_b.as_dict()["total_tokens"] == 1000


def test_stream_entrypoint_attaches_usage_to_the_done_event(monkeypatch):
    """流式入口的接线守卫：用量必须出现在 done 事件的 result.usage 上。

    与非流式共用同一个 ``UsageLedger``，所以前端两种交互拿到的结构一致。
    """
    import rag_stream

    def fake_inner(*args, **kwargs):
        record_call("m", "generation", _FakeUsage(3, 1))
        yield {"type": "phase", "stage": "retrieval"}
        record_call("m", "generation", _FakeUsage(4, 2))
        yield {"type": "done", "result": {"answer": "流式答案"}}

    monkeypatch.setattr(rag_stream, "_answer_stream_inner", fake_inner)
    events = list(rag_stream.answer_query_stream("问题"))
    assert events[-1]["type"] == "done"
    usage = events[-1]["result"]["usage"]
    assert usage["calls"] == 2
    assert usage["total_tokens"] == 10
    # 中途的 phase 事件不该被塞台账：只有 done 带
    assert "usage" not in events[0]


def test_answer_query_attaches_the_ledger_to_the_result(monkeypatch):
    """接线守卫：answer_query 必须开台账并把结果挂到 QueryResult.usage。

    这条测试存在的理由：``_answer_sequential`` 里有 5 处 QueryResult 构造点，
    靠人记住每一处都挂台账是不现实的——所以挂载点收在 ``_attach_usage``
    一个地方，这里验证那个收口真的在 answer_query 的路径上。
    """
    import rag

    def fake_sequential(comp, query, acl, retry, query_id, tenant_id=None, dataset_id=None):
        # 台账必须已经打开，否则组件里的埋点无处可记
        assert current_ledger() is not None
        record_call("m", "generation", _FakeUsage(7, 3))
        return QueryResult(query=query, answer="答案")

    monkeypatch.setattr(rag, "get_pipeline", lambda settings=None: {})
    monkeypatch.setattr(rag, "_answer_sequential", fake_sequential)
    monkeypatch.setattr(rag, "resolve_tenant", lambda tenant_id, settings=None: tenant_id or "default")
    monkeypatch.setattr(rag, "setup_observability", lambda cfg: None)

    from config.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings.pipeline, "graph_engine_on", False)

    result = rag.answer_query("问题", settings=settings)
    assert result.usage["calls"] == 1
    assert result.usage["total_tokens"] == 10
    assert result.usage["by_slot"][0]["slot"] == "generation"


def test_ledger_tolerates_missing_usage_object():
    """端点不返回 usage 时只记调用次数，不往 token 里编数。"""
    with usage_scope() as ledger:
        record_call("m", "generation", None)
    payload = ledger.as_dict()
    assert payload["calls"] == 1
    assert payload["total_tokens"] == 0


# ---------------------------------------------------------------------------
# 入库路径（Round 4）：没有返回字段可挂台账，走"指标 + 日志"留痕
# ---------------------------------------------------------------------------


class _FakeMetrics:
    """只记录调用的假指标注册表。"""

    def __init__(self) -> None:
        self.observed: list[tuple[str, float, dict]] = []
        self.incremented: list[tuple[str, float, dict]] = []

    def observe(self, name: str, value: float, tags: Any = None) -> None:
        self.observed.append((name, float(value), dict(tags or {})))

    def incr(self, name: str, tags: Any = None, value: float = 1.0) -> None:
        self.incremented.append((name, float(value), dict(tags or {})))


def test_finish_writes_metrics_and_returns_payload():
    from core.llm_usage import finish

    with usage_scope() as ledger:
        record_call("m", "contextual", _FakeUsage(100, 20))
        record_cache_hit("m", "contextual", prompt=100, completion=20)
        metrics = _FakeMetrics()
        payload = finish(ledger, scope="ingest", metrics=metrics)

    assert payload["total_tokens"] == 120
    assert payload["saved_total_tokens"] == 120
    names = {name for name, _, _ in metrics.observed}
    assert {"usage.tokens.total", "usage.saved_tokens.total"} <= names
    assert ("usage.calls", 1.0, {"scope": "ingest"}) in metrics.incremented


def test_finish_is_safe_without_ledger_and_with_broken_metrics():
    from core.llm_usage import finish

    assert finish(None) == {}

    class _Boom:
        def observe(self, *args, **kwargs):
            raise RuntimeError("metrics down")

        def incr(self, *args, **kwargs):
            raise RuntimeError("metrics down")

    with usage_scope() as ledger:
        record_call("m", "triplet", _FakeUsage(1, 1))
        # 指标挂了不能连累业务：payload 照常返回
        assert finish(ledger, scope="ingest", metrics=_Boom())["calls"] == 1


def test_ingest_path_opens_a_usage_ledger():
    """源码守卫：入库入口必须开台账（contextual/triplet/classifier 按片段线性放大）。"""
    from pathlib import Path

    source = Path("indexing/ingest.py").read_text(encoding="utf-8")
    assert "from core.llm_usage import finish, usage_scope" in source
    assert "with usage_scope() as _usage:" in source
    assert 'finish(_usage, scope="ingest"' in source


def test_contextual_degradation_is_visible_not_silent(caplog):
    """上下文增强整批降级时必须留痕（warning + 指标），不能静默吞掉。

    实测踩过：入库期槽位指向本机 Ollama 且没启动时，调用要先把重试耗满
    （113 秒）才失败，而失败被静默降级成空结果——外面看就是"增强开了、
    文档慢了两分钟、上下文一条没生成"，完全无感知。
    """
    import logging
    from pathlib import Path

    from indexing.contextual import Contextualizer

    ctx = Contextualizer(llm_client=None, template_path=Path("prompts/contextual_v1.txt"), enabled=True)

    def boom(*args, **kwargs):
        return []  # 模拟"调用失败 -> 静默降级"

    ctx._call_batch = boom  # type: ignore[method-assign]
    metrics = _FakeMetrics()
    with caplog.at_level(logging.WARNING):
        result = ctx.contextualize("文档全文", [("c1", "片段一")])
    assert result == {}
    assert any("未产出任何上下文" in r.message for r in caplog.records)
    del metrics  # 真实指标在 core.metrics 里；这里只验证日志侧
