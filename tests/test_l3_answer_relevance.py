"""L3 答案相关性维度（answer_status）的守卫测试 —— F1.2 基线驱动的改动。

F1.2 的 145 条基线给出一个反直觉的读数：14 条"幻觉"样本里 11 条（79%）
的 ``relevance`` 恰好 0.00，而 ``groundedness`` 在 0.71~1.00。逐条看：

    q="Milvus 的 GraphQL 接口怎么查询集合列表？"  g=1.00 r=0.00
    q="Milvus 里两个集合之间怎么做类似 SQL JOIN？" g=1.00 r=0.00

系统检索到了正确主题的文档，生成了一段**确实由证据支持**的回答——只是那段
回答没回答用户问的那个具体事实。v1 模板拿不到用户的问题（只有 ``{claims}``
与 ``{evidence}``），所以它在**原理上不可能**发现这一点，"有据"的高分反而把
弃权门放行了。

v2 加了 ``{question}`` 占位符与 ``answer_status`` 维度。守卫的重点不是"它能
工作"，而是下面这几条**不会静默失效**的地方：

1. ``topic_only``/``irrelevant`` 必须真的把分数压到阈值以下——否则新维度形同虚设；
2. **不得**把「没给 answer_status」当成 ``answered``（fail-open）；
3. v1 模板路径下行为**完全不变**（评测侧还在用 v1，基线要保持可比）；
4. ``topic_only`` 与 ``unsupported`` 必须在分数与引用状态上可区分——否则事后
   分不出"说错了"和"答非所问"这两类失败，而 F1.2 的价值恰恰在于区分它们；
5. 弃权门读的是分数的聚合值，所以压低必须作用在**每一条**声明上。
"""
from __future__ import annotations

from typing import Any

import pytest

from models.schemas import Chunk, RetrievedChunk
from verify.verifier import (
    MAX_EVIDENCE_CHUNKS,
    CitationVerifier,
    _ANSWER_STATUS_SCORES,
)


class _StubJudge:
    """按预设返回体做桩 LLM 客户端。"""

    def __init__(self, payload: dict[str, Any] | Exception) -> None:
        self.payload = payload
        self.calls: list[dict[str, str]] = []

    def chat_json(self, messages, schema_hint=None) -> dict[str, Any]:
        self.calls.append({"prompt": messages[0]["content"]})
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _chunk(idx: int, text: str = "Milvus 支持 IVF_FLAT 与 HNSW 索引。"):
    return RetrievedChunk(
        chunk=Chunk(
            chunk_id=f"c{idx}",
            doc_id="d",
            text=text,
            text_hash=f"h{idx}",
            created_at=0,
            updated_at=0,
        ),
        score=0.9 - idx * 0.01,
        rank=idx,
    )


def _verifier(payload: dict[str, Any] | Exception) -> tuple[CitationVerifier, _StubJudge]:
    judge = _StubJudge(payload)
    v = CitationVerifier(
        milvus=None,
        judge_llm=judge,
        groundedness_template="prompts/judge_groundedness_v1.txt",
    )
    return v, judge


# ---------------------------------------------------------------------------
# 1：topic_only / irrelevant 真的压分
# ---------------------------------------------------------------------------


def test_topic_only_pushes_scores_below_the_default_threshold() -> None:
    """默认阈值 0.6。逐条声明都判 supported 也不该放行。"""
    payload = {
        "answer_status": "topic_only",
        "answer_reason": "证据讲的是 Milvus 索引，没讲 GraphQL 接口",
        "verdicts": [
            {"claim": "Milvus 支持 IVF_FLAT 索引", "status": "supported",
             "cited_chunk_ids": [1], "reason": "证据 [1]"},
        ],
    }
    v, _ = _verifier(payload)
    result = v.verify("Milvus 支持 IVF_FLAT 索引[1]。", [_chunk(1)], question="GraphQL 怎么查集合列表？")
    assert result.entailment_scores["Milvus 支持 IVF_FLAT 索引"] < 0.6
    # 且是 topic_only 那一档，不是 unsupported 那一档
    assert result.entailment_scores["Milvus 支持 IVF_FLAT 索引"] == pytest.approx(
        _ANSWER_STATUS_SCORES["topic_only"]
    )


def test_irrelevant_scores_lower_than_topic_only() -> None:
    """离域提问（证据与问题完全无关）比"主题相关但没答案"更糟。"""
    assert _ANSWER_STATUS_SCORES["irrelevant"] < _ANSWER_STATUS_SCORES["topic_only"]


def test_answered_does_not_change_scores() -> None:
    payload = {
        "answer_status": "answered",
        "answer_reason": "证据直接回答了",
        "verdicts": [
            {"claim": "Milvus 支持 IVF_FLAT 索引", "status": "supported",
             "cited_chunk_ids": [1], "reason": "证据 [1]"},
        ],
    }
    v, _ = _verifier(payload)
    result = v.verify("Milvus 支持 IVF_FLAT 索引[1]。", [_chunk(1)], question="Milvus 支持哪些索引？")
    assert result.entailment_scores["Milvus 支持 IVF_FLAT 索引"] == 1.0
    assert not any("压到" in n for n in result.notes)


# ---------------------------------------------------------------------------
# 2：不得 fail-open
# ---------------------------------------------------------------------------


def test_missing_answer_status_is_not_treated_as_answered() -> None:
    """没给 answer_status 时**保持逐条声明的原分**，而不是当成 answered。

    这不是"不支持这个维度"——逐条 supported 本来就是 1.0。这里要防的是
    另一个方向：万一实现写成"缺字段就压到 0.3"，那所有用 v1 模板的调用方
    （评测侧、以及显式传 v1 的生产配置）会突然全被拦。返回 "" 时
    _l3_entailment 完全不动分数，是唯一安全的行为。
    """
    payload = {
        "verdicts": [
            {"claim": "Milvus 支持 IVF_FLAT 索引", "status": "supported",
             "cited_chunk_ids": [1], "reason": "证据 [1]"},
        ],
    }
    v, _ = _verifier(payload)
    result = v.verify("Milvus 支持 IVF_FLAT 索引[1]。", [_chunk(1)], question="Milvus 支持哪些索引？")
    assert result.entailment_scores["Milvus 支持 IVF_FLAT 索引"] == 1.0
    assert not any("压到" in n for n in result.notes)


def test_illegal_answer_status_value_is_ignored() -> None:
    """非法值（模型跑飞吐出 "maybe"）按"没给"处理，不当 answered 也不当压分。"""
    payload = {
        "answer_status": "maybe",
        "verdicts": [
            {"claim": "Milvus 支持 IVF_FLAT 索引", "status": "supported",
             "cited_chunk_ids": [1], "reason": "证据 [1]"},
        ],
    }
    v, _ = _verifier(payload)
    result = v.verify("Milvus 支持 IVF_FLAT 索引[1]。", [_chunk(1)], question="q")
    assert result.entailment_scores["Milvus 支持 IVF_FLAT 索引"] == 1.0


def test_missing_or_illegal_status_returns_empty_string_not_answered() -> None:
    """直接钉住 ``_judge_claims`` 的返回值：缺/非法一律是 ``""``。

    为什么必须直接测这个函数而不是只测分数：当前实现里 ``answered`` 与 ``""``
    对分数的效果**恰好相同**（都不压分），所以"缺字段当成 answered"这个变异
    跑现有断言不会变红——本条测试是那道断言的加强版，测的是语义本身。

    少测一层就等于把这个不变量交给运气：将来若有人给 ``answered`` 也加上
    特殊处理（例如 answered 时不计入某个统计），默认值从 ``""`` 悄悄变成
    ``"answered"`` 就会让 v1 模板路径突然被判成"证据回答了问题"——而 v1
    根本答不了这个问题。
    """
    claims = [("A 成立", [1])]
    evidence = [_chunk(1)]

    for payload in (
        {"verdicts": [{"claim": "A 成立", "status": "supported", "reason": "r"}]},
        {"answer_status": "maybe",
         "verdicts": [{"claim": "A 成立", "status": "supported", "reason": "r"}]},
        {"answer_status": None,
         "verdicts": [{"claim": "A 成立", "status": "supported", "reason": "r"}]},
    ):
        v, _ = _verifier(payload)
        _verdicts, status = v._judge_claims(claims, evidence, "用户的问题")
        assert status == "", f"缺/非法 answer_status 应返回空串，实际 {status!r}"

    # 对照组：合法值必须原样透出
    v, _ = _verifier({
        "answer_status": "topic_only",
        "verdicts": [{"claim": "A 成立", "status": "supported", "reason": "r"}],
    })
    _verdicts, status = v._judge_claims(claims, evidence, "用户的问题")
    assert status == "topic_only"


# ---------------------------------------------------------------------------
# 3：v1 路径行为不变
# ---------------------------------------------------------------------------


def test_v1_template_path_leaves_scores_untouched() -> None:
    """v1 模板下（评测侧口径）answer_status 缺失，分数与旧版逐字一致。"""
    payload = {
        "verdicts": [
            {"claim": "A 成立", "status": "supported", "cited_chunk_ids": [1], "reason": "r"},
            {"claim": "B 成立", "status": "unsupported", "cited_chunk_ids": [1], "reason": "r"},
            {"claim": "C 待定", "status": "neutral", "cited_chunk_ids": [1], "reason": "r"},
        ],
    }
    v, _ = _verifier(payload)
    result = v.verify("A 成立[1]。B 成立[1]。C 待定[1]。", [_chunk(1)], question="随便问")
    assert result.entailment_scores == {"A 成立": 1.0, "B 成立": 0.0, "C 待定": 0.5}


def test_v1_template_never_raises_on_missing_question_placeholder() -> None:
    """v1 模板没有 {question} 占位符，传入 question 不得报错。"""
    assert "{question}" not in (CitationVerifier.__module__ and
                               open("prompts/judge_groundedness_v1.txt", encoding="utf-8").read())
    payload = {
        "verdicts": [{"claim": "A", "status": "supported", "cited_chunk_ids": [1], "reason": "r"}],
    }
    v, judge = _verifier(payload)
    v.verify("A[1]。", [_chunk(1)], question="这个问题在 v1 模板里没有占位符")
    assert "这个问题" not in judge.calls[0]["prompt"], "占位符替换不应污染 prompt"


# ---------------------------------------------------------------------------
# 4：两类失败可区分
# ---------------------------------------------------------------------------


def test_topic_only_is_distinguishable_from_unsupported() -> None:
    """F1.2 的价值在于区分"说错了"与"答非所问"，两者不能塌成同一形态。"""
    def run(status: str) -> CitationVerifier:
        payload = {
            "answer_status": status,
            "answer_reason": "r",
            "verdicts": [{"claim": "A", "status": "supported", "cited_chunk_ids": [1], "reason": "r"}],
        }
        v, _ = _verifier(payload)
        return v

    topic = run("topic_only").verify("A[1]。", [_chunk(1)], question="q")
    unsup = CitationVerifier(
        milvus=None,
        judge_llm=_StubJudge({
            "verdicts": [{"claim": "A", "status": "unsupported", "cited_chunk_ids": [1], "reason": "r"}],
        }),
        groundedness_template="prompts/judge_groundedness_v1.txt",
    ).verify("A[1]。", [_chunk(1)], question="q")

    # 分数分三档：supported 1.0 > topic_only 0.3 > unsupported 0.0
    assert topic.entailment_scores["A"] > unsup.entailment_scores["A"]
    # 引用状态也不同：topic_only 降级为 exists_only（"没用"）而不是
    # unsupported（"说错了"）。断言写成**白名单**而不是"两者不等"——
    # "不等"在把 topic_only 也误标成 unsupported 时会恰好成立（都变成
    # unsupported），于是这类变异跑测试不会变红。
    topic_cit = next(c for c in topic.citations if c.claim == "A")
    unsup_cit = next(c for c in unsup.citations if c.claim == "A")
    assert topic_cit.status == "exists_only", (
        f"topic_only 必须是 exists_only（安全，只是没用），实际 {topic_cit.status}"
    )
    assert unsup_cit.status == "unsupported"
    assert "L3" in topic_cit.reason


# ---------------------------------------------------------------------------
# 5：压低作用在每一条声明上
# ---------------------------------------------------------------------------


def test_cap_applies_to_every_claim_not_just_one() -> None:
    """弃权门按分数聚合，所以必须每条都压——漏一条就可能被它放行。"""
    payload = {
        "answer_status": "topic_only",
        "answer_reason": "r",
        "verdicts": [
            {"claim": "声明一", "status": "supported", "cited_chunk_ids": [1], "reason": "r"},
            {"claim": "声明二", "status": "supported", "cited_chunk_ids": [2], "reason": "r"},
            {"claim": "声明三", "status": "supported", "cited_chunk_ids": [2], "reason": "r"},
        ],
    }
    v, _ = _verifier(payload)
    result = v.verify("声明一[1]。声明二[2]。声明三[2]。", [_chunk(1), _chunk(2)], question="q")
    assert len(result.entailment_scores) == 3
    assert all(s < 0.6 for s in result.entailment_scores.values()), (
        f"每条都该被压到阈值下，实际 {result.entailment_scores}"
    )


def test_cap_takes_min_so_supported_detail_is_preserved_in_notes() -> None:
    """压低用 min 而不是覆盖：notes 要留下「为什么被压」的线索。"""
    payload = {
        "answer_status": "topic_only",
        "answer_reason": "r",
        "verdicts": [{"claim": "A", "status": "supported", "cited_chunk_ids": [1], "reason": "r"}],
    }
    v, _ = _verifier(payload)
    result = v.verify("A[1]。", [_chunk(1)], question="q")
    assert any("主题相关" in n for n in result.notes), (
        f"notes 应说明是主题相关性拦下的，实际 {result.notes}"
    )


# ---------------------------------------------------------------------------
# 诊断信息（降级率排查用）
# ---------------------------------------------------------------------------


def test_failure_note_carries_diagnostic_shape() -> None:
    """失败 note 必须带声明数/证据数/异常类型。

    F1.2 基线里 9 条 entailment_unavailable 只能看到"失败了"、看不到原因，
    排查时只能靠猜。这三个数字让下一轮基线能直接读出是"返回体对不上"、
    "模板读不到"还是"JSON 解析失败"。
    """
    v = CitationVerifier(
        milvus=None,
        judge_llm=_StubJudge(ValueError("judge 返回体不含任何可用判定（0 条原始条目）")),
        groundedness_template="prompts/judge_groundedness_v1.txt",
    )
    result = v.verify("A[1]。B[2]。", [_chunk(1), _chunk(2)], question="q")
    joined = " ".join(result.notes)
    assert "声明 2 条" in joined
    assert f"证据 {min(2, MAX_EVIDENCE_CHUNKS)} 条" in joined
    assert "ValueError" in joined


def test_default_template_is_v2_and_eval_side_keeps_v1() -> None:
    """生产 L3 用 v2；评测侧 GroundednessJudge 仍用 v1（F1.2 基线要可比）。"""
    from verify.verifier import _DEFAULT_TEMPLATE

    assert _DEFAULT_TEMPLATE.endswith("judge_answer_relevance_v2.txt")
    v2 = open(_DEFAULT_TEMPLATE, encoding="utf-8").read()
    assert "{question}" in v2, "v2 必须含 question 占位符，否则拿不到用户的问题"
    assert "answer_status" in v2

    from eval.judges import GroundednessJudge

    import inspect

    src = inspect.getsource(GroundednessJudge)
    assert "judge_groundedness_v1.txt" in src, "评测侧必须继续用 v1"


def test_eval_harness_uses_its_own_judges_not_the_production_verifier() -> None:
    import inspect

    import eval.run_eval as run_eval

    src = inspect.getsource(run_eval.Evaluator.__init__)
    assert "GroundednessJudge" in src and "RelevanceJudge" in src
    assert "CitationVerifier" not in src, (
        "评测器不应直接复用生产的 CitationVerifier——它现在用 v2 模板，"
        "会让 F1.2 基线口径被悄悄改掉"
    )


def test_verify_signature_accepts_question_without_breaking_old_callers() -> None:
    """``CitationVerifier.verify`` 新增 ``question`` 形参，但必须**可选**。

    这一条是被真实回归教出来的：加上 ``question`` 之后，7 条
    ``test_stream_observability`` 用例当场红了——它们��桩是
    ``def verify(self, answer, chunks)``，收到 ``question=`` 关键字就抛
    ``TypeError``，而那个 TypeError 被编排层当成"验证失败"吞掉，于是症状
    变成"事件序列不对"，与真正的病因隔了三层。

    所以契约是：``question`` 有默认值、位置在 ``strict`` 之后，旧的
    ``verify(answer, chunks)`` 与 ``verify(answer, chunks, strict)`` 两种
    调法都必须还能工作。
    """
    import inspect

    sig = inspect.signature(CitationVerifier.verify)
    assert "question" in sig.parameters, "verify 必须接受 question"
    assert sig.parameters["question"].default == "", (
        "question 必须可选——测试桩与外部调用方都按旧签名实现"
    )
    assert sig.parameters["strict"].default is None
