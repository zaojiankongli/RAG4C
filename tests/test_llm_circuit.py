"""P4：LLM 客户端熔断（CircuitBreaker）接入测试。

覆盖：
- 熔断打开时 `chat` 快速失败（不调用下游 _ensure_client / retry）；
- 熔断关闭时正常调用，成功后 record_success；
- 下游异常时 record_failure（熔断累计失败）；
- `create_client(cfg, circuit=...)` 正确附加熔断；
- 未提供 circuit 时行为与接入前一致。
"""
from __future__ import annotations

from typing import Any

import pytest

from config.settings import LlmSlotSettings
from core.circuit import CircuitBreaker, CircuitConfig
from core.llm import LLMClient, create_client


def _breaker(failure_threshold: int = 2, cooldown_s: float = 60.0) -> CircuitBreaker:
    return CircuitBreaker(
        "test-llm", CircuitConfig(failure_threshold=failure_threshold, cooldown_s=cooldown_s)
    )


class _FakeResp:
    def __init__(self, text: str) -> None:
        self.choices = [type("C", (), {"message": type("M", (), {"content": text})})()]


class _FakeCompletions:
    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    def create(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("downstream down")
        return _FakeResp("hello")


class _FakeClient:
    def __init__(self) -> None:
        self.chat = type("Chat", (), {"completions": _FakeCompletions()})()


def _slot(**overrides: Any) -> LlmSlotSettings:
    base = {
        "model": "test-model",
        "provider": "openai_compatible",
        "base_url": "http://127.0.0.1:1",
        "api_key": "k",
        "temperature": 0.0,
    }
    base.update(overrides)
    return LlmSlotSettings(**base)


def _client_with_stub(circuit=None) -> tuple[LLMClient, _FakeClient]:
    client = LLMClient(_slot(), circuit=circuit)
    fake = _FakeClient()
    client._ensure_client = lambda: fake  # type: ignore[method-assign]
    return client, fake


def test_open_circuit_fails_fast_without_calling_downstream() -> None:
    breaker = _breaker()
    breaker.record_failure()
    breaker.record_failure()  # 达到阈值 → 打开
    client, fake = _client_with_stub(breaker)
    with pytest.raises(Exception) as exc_info:
        client.chat([{"role": "user", "content": "hi"}])
    assert "熔断" in str(exc_info.value)
    assert fake.chat.completions.calls == 0  # 未发起下游调用


def test_closed_circuit_calls_downstream_and_records_success() -> None:
    breaker = _breaker()
    client, fake = _client_with_stub(breaker)
    text = client.chat([{"role": "user", "content": "hi"}])
    assert text == "hello"
    assert fake.chat.completions.calls == 1
    assert breaker.allow() is True  # 成功后不误开（仍放行）


def test_downstream_failure_records_failure() -> None:
    breaker = _breaker(failure_threshold=1)
    client, fake = _client_with_stub(breaker)
    fake.chat.completions.fail = True
    with pytest.raises(Exception):
        client.chat([{"role": "user", "content": "hi"}])
    # 连续失败达阈值 → 熔断打开（不再放行）
    assert breaker.allow() is False


def test_create_client_attaches_circuit() -> None:
    breaker = _breaker()
    client = create_client(_slot(), circuit=breaker)
    assert client.circuit is breaker


def test_no_circuit_behavior_unchanged() -> None:
    client, fake = _client_with_stub(circuit=None)
    text = client.chat([{"role": "user", "content": "hi"}])
    assert text == "hello"
    assert fake.chat.completions.calls == 1
