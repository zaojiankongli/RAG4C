"""思考链开关（``LlmSlotSettings.enable_thinking``）的守卫测试。

实测依据（``eval/.cache/probe_thinking.py``，硅基流动 Qwen3.5-9B，答一道带
11 条引用的题）：

    默认              69.4s   completion=3397   正文 193 字   思考 11130 字
    enable_thinking=False  8.1s   completion=418    正文 721 字   思考 0 字

即 88% 的 token 与 90% 的墙钟时间花在不会出现在答案里的思考链上。思考
token **不受 max_tokens 约束**，所以 120s 的槽位超时必然被打爆：F1.2 的
145 条基线按 69s+ 重试的路径跑要 22 小时。

守卫五条：
1. 缺省不干预——不配置时**不发** extra_body，行为与引入本字段前逐字一致
   （由别人家的模型决定，不由我们替部署方做主）；
2. 显式 false / true 都如实透传；
3. 开关进缓存键——否则配置一翻转，旧的长思考输出会继续被命中；
4. ``chat_json`` 走同一条路径（它复用 chat），JSON 模式下开关同样生效；
5. 配置项可被 env 覆盖，否则 .env 里写了不起作用。
"""
from __future__ import annotations

from typing import Any

import pytest

from config.settings import LlmSlotSettings
from core.llm import LLMClient


class _FakeResp:
    def __init__(self, content: str) -> None:
        msg = type("M", (), {"content": content})()
        self.choices = [type("C", (), {"message": msg})()]
        self.usage = type("U", (), {"prompt_tokens": 1, "completion_tokens": 1})()


class _CapturingCompletions:
    """记录收到的完整 kwargs（要断言的是 extra_body，光看 messages 不够）。"""

    def __init__(self, reply: str = '{"ok": true}') -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeResp:
        self.calls.append(kwargs)
        return _FakeResp(self.reply)


def _client(capture: _CapturingCompletions, **slot_kwargs: Any) -> LLMClient:
    client = LLMClient(LlmSlotSettings(base_url="http://127.0.0.1:1/v1", api_key="k", **slot_kwargs))
    object.__setattr__(client, "_client", type("C", (), {"chat": type("M", (), {"completions": capture})()})())
    return client


@pytest.fixture(autouse=True)
def _bypass_endpoint_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """跳过端点可达性探测与熔断记录：这里要验的是请求体，不是网络。"""
    import core.llm as llm_mod

    monkeypatch.setattr(llm_mod, "_assert_endpoint_reachable", lambda *a, **k: None)


def test_absent_by_default_sends_no_extra_body() -> None:
    """缺省不干预：没配就不发这个字段，行为与引入前逐字一致。"""
    cap = _CapturingCompletions()
    _client(cap).chat([{"role": "user", "content": "hi"}])
    assert cap.calls, "应当真的发起了一次调用"
    assert "extra_body" not in cap.calls[0]


def test_explicit_false_is_passed_through() -> None:
    cap = _CapturingCompletions()
    _client(cap, enable_thinking=False).chat([{"role": "user", "content": "hi"}])
    assert cap.calls[0]["extra_body"] == {"enable_thinking": False}


def test_explicit_true_is_passed_through() -> None:
    cap = _CapturingCompletions()
    _client(cap, enable_thinking=True).chat([{"role": "user", "content": "hi"}])
    assert cap.calls[0]["extra_body"] == {"enable_thinking": True}


def test_switch_changes_cache_key() -> None:
    """开关必须进缓存键：配置一翻转，旧的长思考输出不能继续被命中。"""
    from core.llm_cache import _digest

    common = dict(
        model="m",
        base_url="u",
        temperature=0.0,
        max_tokens=100,
        seed=None,
        json_mode=False,
        messages=[{"role": "user", "content": "hi"}],
    )
    keys = {
        _digest(**common, enable_thinking=flag) for flag in (True, False, None)
    }
    assert len(keys) == 3, "三种取值必须算出三个不同的键"


def test_json_mode_also_honours_switch() -> None:
    """chat_json 复用 chat，JSON 模式下开关同样生效（裁判槽位靠它压延迟）。"""
    cap = _CapturingCompletions()
    parsed = _client(cap, enable_thinking=False).chat_json(
        [{"role": "user", "content": "hi"}], schema_hint='{"a": 1}'
    )
    assert parsed == {"ok": True}
    assert cap.calls[0]["extra_body"] == {"enable_thinking": False}
    assert cap.calls[0]["response_format"] == {"type": "json_object"}


def test_setting_is_env_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    """配置项必须能被 env 覆盖，否则 .env 里写了不起作用。

    走 ``Settings()`` 而不是 ``LlmSlotsSettings()``：后者是普通 BaseModel，
    env 源只挂在顶层 Settings 上，直接构造子对象读不到任何环境变量。
    """
    from config.settings import Settings

    monkeypatch.setenv("RAG4C_LLM_GENERATION_ENABLE_THINKING", "false")
    settings = Settings()
    assert settings.llm.generation.enable_thinking is False
    # 没提到的槽位仍然不干预
    assert settings.llm.judge.enable_thinking is None
