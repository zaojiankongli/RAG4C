"""答案层降级识别与节流/并发接线的守卫测试 —— master-plan F1.2。

计划要求基线里 ``degraded_rate`` 与六个质量指标**同报**，理由写得很直白：
"区分『质量差』和『上游挂了』"。一次硅基流动限流会让有据性掉、幻觉率涨，
把它读成"模型变笨了"就会照着错误结论去调提示词——所以这两类数字必须在
结构上就分开，而不是靠读报告的人记得。

守卫五条：
1. 降级痕迹能从 traces 里认出来，且**不**把"配置选择"（L3 被显式关掉）
   误判成故障——否则每轮都是 100% 降级率，等于没测；
2. 裁判没判成（score=None）算降级，且降级率的分母是**全部用例**
   （上游一挂用例就变弃权，而弃权恰恰最该被看见）；
3. 降级率进得了 release gate 默认阈值——"这轮数字能不能信"要能签字拦截；
4. ``--workers`` 并发不改变结果：并发数不同，逐用例结论必须逐字一致；
5. ``--sleep-ms`` 只影响墙钟，不影响结论。
"""
from __future__ import annotations

from pathlib import Path

from eval.run_eval import (
    DEFAULT_RELEASE_THRESHOLDS,
    CaseResult,
    Evaluator,
    FakeJudgeLLM,
    GroundednessJudge,
    RelevanceJudge,
    aggregate,
    detect_degradation,
    release_gate,
    summarize_usage,
)
from models.schemas import Chunk, Citation, QueryResult

# ---------------------------------------------------------------------------
# 造结果的最小工具
# ---------------------------------------------------------------------------


def _qr(answer: str = "答案", *, traces: list[str] | None = None, citations: int = 0) -> QueryResult:
    now_chunks = [
        Chunk(
            chunk_id=f"c{i}",
            doc_id="d",
            text="证据",
            text_hash="",
            created_at="2026-10-04T00:00:00",
            updated_at="2026-10-04T00:00:00",
        )
        for i in range(citations)
    ]
    return QueryResult(
        query="q",
        answer=answer,
        abstained=not answer,
        traces=traces or [],
        citations=[
            Citation(claim="c", chunk_id=c.chunk_id, status="ok", reason="ok")
            for c in now_chunks
        ],
        verdict={"evidence_chunks": [c.model_dump(mode="json") for c in now_chunks]},
    )


def _judges() -> tuple[GroundednessJudge, RelevanceJudge]:
    return GroundednessJudge(llm_client=FakeJudgeLLM()), RelevanceJudge(llm_client=FakeJudgeLLM())


# ---------------------------------------------------------------------------
# 1 / 2：降级识别
# ---------------------------------------------------------------------------


def test_detects_upstream_failures_from_traces() -> None:
    assert detect_degradation(
        _qr("答案", traces=["L3 判定失败（降级：蕴含不可用，本轮不参与弃权判定）"]),
        groundedness=0.5,
        relevance=0.5,
    ) == ["entailment_unavailable"]
    assert detect_degradation(
        _qr("", traces=["检索失败（弃权）: milvus down"]), answered=False
    ) == ["retrieval_failed"]
    assert detect_degradation(
        _qr("答案", traces=["图编排不可用，回退顺序管线"]), groundedness=0.5, relevance=0.5
    ) == ["graph_fallback"]
    assert detect_degradation(_qr("答案"), groundedness=0.5, relevance=0.5) == []


def test_config_choice_is_not_a_failure() -> None:
    """显式关掉 L3 是配置选择，不是上游故障。

    把它算进降级率的后果很具体：``entailment_mode=skip`` 的部署每一条用例都
    会命中，degraded_rate 恒为 1.0，这项指标就再也提供不了任何信息。
    """
    qr = _qr("答案", traces=["L3 蕴含判定已跳过（entailment_mode=skip）"])
    assert detect_degradation(qr, groundedness=0.5, relevance=0.5) == []


def test_judge_failure_counts_as_degradation() -> None:
    """裁判没判成 = 评测侧上游故障，不得被读成"这条答案质量差"。"""
    assert detect_degradation(_qr("答案"), groundedness=None, relevance=0.5) == ["judge_failed"]
    assert detect_degradation(_qr("答案"), groundedness=0.5, relevance=None) == ["judge_failed"]
    # 未作答的用例不追究裁判分——它压根没调裁判
    assert detect_degradation(_qr(""), groundedness=None, relevance=None, answered=False) == []


def test_degraded_rate_denominator_is_all_cases() -> None:
    """分母是全部用例：上游一挂用例就变弃权，弃权最该被看见。"""
    cases = [
        # 2 条正常作答 + 1 条因上游故障而弃权
        _case_result("ok1", degraded=False, abstained=False, unanswerable=False),
        _case_result("ok2", degraded=False, abstained=False, unanswerable=False),
        _case_result("bad", degraded=True, abstained=True, unanswerable=False, kinds=["retrieval_failed"]),
    ]
    report = aggregate(cases)
    assert report.metrics["degraded_rate"] == 1 / 3
    assert report.degraded_cases == 1
    assert report.degraded_kinds == {"retrieval_failed": 1}
    # 且降级必须与质量指标分开可读：这轮拒答率 1/3，但拒答**不是**质量问题
    assert report.metrics["refusal_rate"] == 1 / 3
    assert report.cases[2].degraded is True
    assert report.cases[0].degraded is False


def _case_result(
    case_id: str,
    *,
    degraded: bool,
    abstained: bool,
    unanswerable: bool,
    kinds: list[str] | None = None,
) -> CaseResult:
    return CaseResult(
        id=case_id,
        question=case_id,
        unanswerable=unanswerable,
        abstained=abstained,
        answered=not abstained,
        degraded=degraded,
        degraded_kinds=kinds or [],
    )


# ---------------------------------------------------------------------------
# 3：门禁
# ---------------------------------------------------------------------------


def test_degraded_rate_is_gated_by_default() -> None:
    """上游大面积挂掉时门禁必须拦下——不能拿这种轮次的数字签字。"""
    assert "degraded_rate" in DEFAULT_RELEASE_THRESHOLDS
    from eval.run_eval import as_v2

    report = as_v2(aggregate([_case_result("a", degraded=True, abstained=True, unanswerable=False,
                                          kinds=["judge_failed"])]))
    result = release_gate(report)
    assert result["passed"] is False
    assert "degraded_rate" in result["failures"]


def test_judge_failure_reason_is_recorded_separately_from_the_flag() -> None:
    """`judge_failed` 只说"失败了"，`judge_failures` 说"为什么失败"。

    F1.2 基线里 9 条 judge_failed 查不出原因，只能看到 score=None——而有据性
    裁判只有三种失败路径（judge_parse_failed / no_claims / no_verdicts，见
    eval/judges.py），它们的处置完全不同：前者查模型输出，后两者查切分。
    缺了这个字段，基线里出现 judge_failed 就只能靠猜。
    """
    dataset = [{"id": "x", "question": "问题"}]

    def pipeline(question: str) -> QueryResult:
        return _qr("答案", citations=1)

    class _FailGrounded:
        def judge(self, q, a, chunks):
            from models.schemas import JudgeResult

            return JudgeResult(score=None, rationale="no_claims")

    class _OkRelevance:
        def judge(self, q, a, chunks):
            from models.schemas import JudgeResult

            return JudgeResult(score=0.75, rationale="正常")

    from eval.run_eval import Evaluator

    report = Evaluator(pipeline, _FailGrounded(), _OkRelevance()).evaluate(dataset)
    case = report.cases[0]
    assert case.degraded is True
    assert "judge_failed" in case.degraded_kinds
    assert case.judge_failures == ["groundedness:no_claims"], (
        f"失败原因应被记下，实际 {case.judge_failures}"
    )
    # 相关性正常就不该出现在失败列表里
    assert not any(f.startswith("relevance:") for f in case.judge_failures)


def test_healthy_case_records_no_judge_failures() -> None:
    dataset = [{"id": "x", "question": "问题"}]

    def pipeline(question: str) -> QueryResult:
        return _qr("答案", citations=1)

    report = Evaluator(pipeline, *_judges()).evaluate(dataset)
    assert report.cases[0].judge_failures == []


# ---------------------------------------------------------------------------
# 4 / 5：并发与节流不改变结论
# ---------------------------------------------------------------------------


def test_workers_do_not_change_results() -> None:
    dataset = [{"id": f"c{i}", "question": f"问题{i}"} for i in range(8)]
    groundedness, relevance = _judges()

    def pipeline(question: str) -> QueryResult:
        # 交错返回弃权/作答，且带一条降级痕迹，验证顺序与归因都不串
        idx = int(question[-1])
        if idx % 3 == 0:
            return _qr("", traces=["检索失败（弃权）: boom"])
        return _qr(f"答案{idx}", citations=idx + 1)

    serial = Evaluator(pipeline, groundedness, relevance).evaluate(dataset)
    parallel = Evaluator(pipeline, groundedness, relevance).evaluate(dataset, workers=4)
    throttled = Evaluator(pipeline, groundedness, relevance).evaluate(dataset, sleep_ms=1.0)

    for other in (parallel, throttled):
        assert other.metrics == serial.metrics
        assert [(c.id, c.degraded, c.degraded_kinds, c.citations_total) for c in other.cases] == [
            (c.id, c.degraded, c.degraded_kinds, c.citations_total) for c in serial.cases
        ]
    # 前提：这条链路真的产生了降级样本，否则本测试等于什么都没验
    assert serial.degraded_cases == 3


# ---------------------------------------------------------------------------
# 用量台账
# ---------------------------------------------------------------------------


def test_usage_summary_is_empty_when_nothing_recorded() -> None:
    """没开台账时返回空字典——不返回一堆 0，那会让"没记账"像"花了 0"。"""
    assert summarize_usage([_case_result("a", degraded=False, abstained=False, unanswerable=False)]) == {}


def test_usage_summary_adds_up_tokens_and_marks_unpriced() -> None:
    def _with_usage(case_id: str, tokens: float, priced: bool) -> CaseResult:
        return CaseResult(
            id=case_id,
            question=case_id,
            unanswerable=False,
            abstained=False,
            answered=True,
            usage={
                "calls": 2,
                "total_tokens": tokens,
                "prompt_tokens": tokens / 2,
                "completion_tokens": tokens / 2,
                "cost": 0.0,
                "cost_priced": priced,
                "by_slot": [{"slot": "judge", "calls": 2, "total_tokens": tokens}],
            },
        )

    total = summarize_usage([_with_usage("a", 100.0, False), _with_usage("b", 50.0, True)])
    assert total["total_tokens"] == 150.0
    assert total["calls"] == 4
    # 一条没配价就不能报 cost_priced
    assert total["cost_priced"] is False
    assert total["by_slot"][0]["slot"] == "judge"
    assert total["by_slot"][0]["total_tokens"] == 150.0


# ---------------------------------------------------------------------------
# CLI 透传
# ---------------------------------------------------------------------------


def test_cli_accepts_sleep_and_workers() -> None:
    """CLI 必须真把两个参数透传到 evaluate（配了不接 = 静默无效）。"""
    import argparse

    from eval.run_eval import main as _main

    captured: dict[str, object] = {}
    import eval.run_eval as mod

    real = mod.Evaluator.evaluate

    def spy(self, dataset, *, sleep_ms=0.0, workers=1):  # type: ignore[no-untyped-def]
        captured["sleep_ms"] = sleep_ms
        captured["workers"] = workers
        return real(self, dataset, sleep_ms=sleep_ms, workers=workers)

    mod.Evaluator.evaluate = spy  # type: ignore[method-assign]
    try:
        rc = _main(
            [
                "--dry-run",
                "--sleep-ms",
                "250",
                "--workers",
                "3",
                "--out",
                str(Path("eval/results-cli-test.json")),
            ]
        )
    finally:
        mod.Evaluator.evaluate = real  # type: ignore[method-assign]
    assert rc == 0
    assert captured == {"sleep_ms": 250.0, "workers": 3}
    assert argparse  # 仅避免未使用告警
