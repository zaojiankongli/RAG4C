"""JSON 解析重试的守卫测试。

F1.2 的 145 条基线里裁判有 9 条 ``judge_parse_failed``（v2 模板下 13 条），
根因是模型在 ``json_mode`` 下偶发不闭合 JSON。实测两组失败样本**基本不重叠**
（v2 新失败 6 条、v2 修好 6 条）——是随机性，不是系统性缺陷。

而这个失败的代价不小：裁判解析失败 → 整条弃权门少一道闸
（``entailment_unavailable``）→ 直接推高幻觉率。所以重试次数与重试边界
必须正确。

守卫三条：
1. 重试次数 = 1 + ``json_parse_retries``（默认 1 → 2 次，与改动前一致）；
2. **LLMError 的重抛边界跟着 attempts 走**——写死 ``attempt == 1`` 会在
   attempts=3 时提前放弃，白配了重试；
3. 解析失败的错误消息必须带**输出长度与是否以 } 结尾**——没这两个字段就
   无法区分「被 max_tokens 截断」与「模型就写了 400 字」，而 F1.2 的排查
   正是卡在这里（notes 里 13 条片段全是 344~398 字符，看起来像被截断，
   但那只是 ``last_text[:300]`` 的切片）。
"""
from __future__ import annotations

import pytest

from config.settings import LlmSlotSettings
from core.llm import LLMClient, LLMError, ParseFallbackError


class _FlakyLLM(LLMClient):
    """前 ``bad`` 次返回非法 JSON，之后返回合法 JSON。"""

    def __init__(self, bad: int, cfg: LlmSlotSettings) -> None:
        super().__init__(cfg)
        self.bad = bad
        self.calls = 0

    def chat(self, messages, **kwargs) -> str:  # type: ignore[override]
        self.calls += 1
        if self.calls <= self.bad:
            return '{"verdicts": [ {"claim": "a", "status": "sup'  # 未闭合
        return '{"answer_status": "answered", "verdicts": []}'


def _cfg(retries: int = 1) -> LlmSlotSettings:
    return LlmSlotSettings(
        provider="openai_compatible",
        model="m",
        base_url="http://127.0.0.1:1/v1",
        api_key="k",
        json_parse_retries=retries,
    )


# ---------------------------------------------------------------------------
# 1：次数口径
# ---------------------------------------------------------------------------


def test_default_keeps_the_original_behaviour() -> None:
    """默认 json_parse_retries=1 → 总共 2 次（与改动前逐字一致）。"""
    client = _FlakyLLM(bad=1, cfg=_cfg(retries=1))
    result = client.chat_json([{"role": "user", "content": "x"}])
    assert result["answer_status"] == "answered"
    assert client.calls == 2


def test_retries_zero_means_single_attempt() -> None:
    """retries=0 → 只试一次。"""
    client = _FlakyLLM(bad=1, cfg=_cfg(retries=0))
    with pytest.raises(ParseFallbackError):
        client.chat_json([{"role": "user", "content": "x"}])
    assert client.calls == 1


def test_higher_retry_count_recovers_from_more_failures() -> None:
    """retries=3 → 总共 4 次，能扛过 3 次连续失败。"""
    client = _FlakyLLM(bad=3, cfg=_cfg(retries=3))
    assert client.chat_json([{"role": "user", "content": "x"}])["answer_status"] == "answered"
    assert client.calls == 4


# ---------------------------------------------------------------------------
# 2：重抛边界跟着 attempts 走
# ---------------------------------------------------------------------------


def test_llm_error_boundary_follows_attempts_not_a_hardcoded_one() -> None:
    """LLMError 的重抛边界必须比较最后一次，而不是写死 ``attempt == 1``。

    写死 1 在 attempts=4 时会让第 2 次失败就抛出——重试配了却只用了 1 次，
    这种 bug 不会报错、只会让"提高重试次数"这个配置静默失效。
    """
    calls = {"n": 0}

    class _AlwaysFails(LLMClient):
        def chat(self, messages, **kwargs) -> str:  # type: ignore[override]
            calls["n"] += 1
            raise LLMError("上游挂了")  # 必须是 LLMError：非它会直接穿透

    client = _AlwaysFails(_cfg(retries=3))
    with pytest.raises(Exception):
        client.chat_json([{"role": "user", "content": "x"}])
    assert calls["n"] == 4, f"应尝试 4 次，实际 {calls['n']}"


# ---------------------------------------------------------------------------
# 3：错误消息要能回答"是不是被截断"
# ---------------------------------------------------------------------------


def test_error_message_reports_length_and_closing_brace() -> None:
    """错误消息必须带输出长度与「是否以 } 结尾」。

    这两个字段是 F1.2 排查时缺的那一块：notes 里 13 条失败片段全是 344~398
    字符、全部「未闭合」，但那只是 ``last_text[:300]`` 的切片，看起来像被
    max_tokens 截断——而没有长度就分不清「真被截断」与「模型就写了 400 字」。
    """
    client = _FlakyLLM(bad=99, cfg=_cfg(retries=0))
    with pytest.raises(ParseFallbackError) as exc:
        client.chat_json([{"role": "user", "content": "x"}])
    msg = str(exc.value)
    assert "原始输出共" in msg, "必须报出输出长度"
    assert "以 '}' 结尾" in msg, "必须报出是否闭合"
    assert "已尝试 1 次" in msg, "必须报出尝试次数"


def test_error_message_fragment_is_longer_than_300_chars() -> None:
    """片段从 300 提到 600 —— 300 恰好覆盖不到一个完整的短 verdicts 数组。"""
    body = "x" * 900
    client = _FlakyLLM(bad=99, cfg=_cfg(retries=0))
    client.bad = 99

    class _Long(LLMClient):
        def chat(self, messages, **kwargs) -> str:  # type: ignore[override]
            return '{"verdicts": ["' + body

    long_client = _Long(_cfg(retries=0))
    with pytest.raises(ParseFallbackError) as exc:
        long_client.chat_json([{"role": "user", "content": "x"}])
    msg = str(exc.value)
    assert "原始输出共 915 字符" in msg
    assert body[:500] in msg, "片段应含前 500 字符"
