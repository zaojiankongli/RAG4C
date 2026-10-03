"""``chat_json`` 的 system 消息必须排在最前面（跨 provider 兼容性守卫）。

背景：``core/llm.py::chat_json`` 原本把 schema 提示作为 system 消息**追加在
末尾**。OpenAI 容忍这个顺序，但硅基流动会直接 400：

    code 20015: "messages" in request are illegal:
    System message must be at the beginning

后果不是"报错"那么简单：judge / router_llm / metadata_filter 三个 JSON 槽位
在硅基流动上全部静默失败，L3 引用蕴含判定因此**不参与弃权判定**，traces 里
只留一行"降级：蕴含不可用"。也就是说一个消息顺序问题会让引用校验整条消失——
这正是本项目「宁可弃权，不可编造」最不能承受的失效模式。

守卫三条：
1. 单条 user 消息时，system 插在最前（不是追加在末尾）；
2. 已有 system 在首位时，把提示**并进那条**，不新增第二条 system
   （多条 system 会踩另一些 provider 的限制）；
3. system 不在首位时也不追加在末尾——提示统一前置，保证任何输入下顺序都对。
"""
from __future__ import annotations

from typing import Any

import pytest

from config.settings import LlmSlotSettings
from core.llm import LLMClient


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResp:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _CapturingCompletions:
    """记录最后一次收到的 messages，用于断言顺序。"""

    def __init__(self, reply: str = '{"ok": true}') -> None:
        self.reply = reply
        self.calls: list[list[dict[str, Any]]] = []

    def create(self, **kwargs: Any) -> _FakeResp:
        self.calls.append(kwargs.get("messages", []))
        return _FakeResp(self.reply)


def _client_with_capture(capture: _CapturingCompletions) -> LLMClient:
    client = LLMClient(LlmSlotSettings())
    fake_client = type("C", (), {"chat": type("M", (), {"completions": capture})()})
    object.__setattr__(client, "_client", fake_client())
    return client


def _roles(msgs: list[dict[str, Any]]) -> list[str]:
    return [m.get("role", "") for m in msgs]


def test_schema_hint_system_goes_first() -> None:
    cap = _CapturingCompletions()
    client = _client_with_capture(cap)
    client.chat_json([{"role": "user", "content": "hi"}], schema_hint='{"ok": "bool"}')
    assert cap.calls, "应当真的发起了一次调用"
    roles = _roles(cap.calls[0])
    assert roles[0] == "system", f"system 必须在首位，实际顺序={roles}"
    assert "schema" in cap.calls[0][0]["content"]


def test_existing_system_is_merged_not_duplicated() -> None:
    cap = _CapturingCompletions()
    client = _client_with_capture(cap)
    client.chat_json(
        [
            {"role": "system", "content": "你是裁判"},
            {"role": "user", "content": "hi"},
        ],
        schema_hint='{"ok": "bool"}',
    )
    msgs = cap.calls[0]
    system_msgs = [m for m in msgs if m.get("role") == "system"]
    # 并进原有那条，而不是新增第二条 system
    assert len(system_msgs) == 1, f"不应出现多条 system，实际 {len(system_msgs)}"
    assert "你是裁判" in system_msgs[0]["content"], "原有 system 内容不能被丢掉"
    assert "schema" in system_msgs[0]["content"], "schema 提示应该并进来"


def test_hint_stays_first_even_if_system_was_not_first() -> None:
    """即使调用方把 system 放在后面，提示也统一前置。"""
    cap = _CapturingCompletions()
    client = _client_with_capture(cap)
    client.chat_json(
        [
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "后来的 system"},
        ],
        schema_hint='{"ok": "bool"}',
    )
    roles = _roles(cap.calls[0])
    assert roles[0] == "system", f"首位必须是 system，实际={roles}"


def test_no_schema_hint_leaves_messages_untouched() -> None:
    """没给 schema_hint 时不应凭空插入 system（保持原有行为）。"""
    cap = _CapturingCompletions()
    client = _client_with_capture(cap)
    client.chat_json([{"role": "user", "content": "hi"}])
    assert _roles(cap.calls[0]) == ["user"]


if __name__ == "__main__":
    pytest.main([__file__])
