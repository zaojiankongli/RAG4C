"""RAG4C 评测模块离线自检（smoke test）。

运行：``python scripts/smoke_eval.py``

全程不联网、不加载模型、不依赖 Milvus。所有断言通过后打印
``SMOKE EVAL PASSED (exit 0)`` 并以退出码 0 结束；任何失败打印 FAIL 并以 1 退出。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

# 确保从任意工作目录运行都能导入项目包
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.llm import ParseFallbackError  # noqa: E402
from eval.dataset_sample import SAMPLE_DATASET  # noqa: E402
from eval.judges import GroundednessJudge, RelevanceJudge  # noqa: E402
from eval.run_eval import Evaluator, FakeJudgeLLM, extract_evidence  # noqa: E402
from models.schemas import Citation, QueryResult  # noqa: E402


class FailingJudgeLLM(FakeJudgeLLM):
    """永远解析失败的裁判桩，用于验证显式 score=None 路径。"""

    def chat_json(self, messages: list[dict], schema_hint: str | None = None) -> dict:
        raise ParseFallbackError("模拟：模型返回不可解析内容")


def _check(step: str, ok: bool) -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {step}")
    if not ok:
        raise AssertionError(step)


def _make_canned_pipeline(dataset: list[dict]) -> Callable[[str], QueryResult]:
    """构造离线管线：1 条可答用例拒答、1 条不可答用例作答，用于压测三类比率。

    用例序号约定（与 SAMPLE_DATASET 一致）：0-4 可答，5-7 不可答。
    """
    by_question = {case["question"]: index for index, case in enumerate(dataset)}

    def pipeline(question: str) -> QueryResult:
        index = by_question[question]
        if index == 0:
            # 答案可答却被拒：过度拒答样本
            return QueryResult(query=question, answer="", abstained=True, route="abstain")
        if index == 5:
            # 知识库无答案却作答：幻觉样本（引用假证据）
            return QueryResult(
                query=question,
                answer=f"（幻觉回答）关于「{question}」的猜测性答案。",
                citations=[Citation(claim=f"模拟引用{index}", chunk_id=f"c{index}", status="ok", reason="模拟")],
                abstained=False,
                route="hybrid",
            )
        if index in (6, 7):
            # 无答案且正确拒答
            return QueryResult(query=question, answer="", abstained=True, route="abstain")
        # 其余可答用例正常作答；index==2 的用例带一条失败引用
        status = "unsupported" if index == 2 else "ok"
        citations = [Citation(claim=f"模拟引用{index}", chunk_id=f"c{index}", status=status, reason="模拟")]
        return QueryResult(
            query=question,
            answer=f"（模拟回答）针对「{question}」的答案。",
            citations=citations,
            abstained=False,
            route="hybrid",
        )

    return pipeline


def main() -> int:
    step = 0
    try:
        # ---- 步骤 1：数据集结构（必须 5 可答 + 3 不可答）----
        step = 1
        total = len(SAMPLE_DATASET)
        answerable = sum(1 for c in SAMPLE_DATASET if not c["unanswerable"])
        unanswerable = sum(1 for c in SAMPLE_DATASET if c["unanswerable"])
        _check(
            f"数据集共 {total} 条：可答 {answerable}，不可答 {unanswerable}",
            total == 8 and answerable == 5 and unanswerable == 3,
        )
        required_keys = {"id", "question", "unanswerable", "doc_text", "expected_keywords", "notes"}
        _check(
            "每条用例包含全部必需字段",
            all(required_keys <= set(c.keys()) for c in SAMPLE_DATASET),
        )
        _check(
            "5 条可答用例均提供 doc_text",
            all(c.get("doc_text") for c in SAMPLE_DATASET if not c["unanswerable"]),
        )
        _check(
            "3 条不可答用例均不提供 doc_text",
            all(not c.get("doc_text") for c in SAMPLE_DATASET if c["unanswerable"]),
        )

        # ---- 步骤 2：裁判分数映射 ----
        step = 2
        groundedness_judge = GroundednessJudge(llm_client=FakeJudgeLLM())
        relevance_judge = RelevanceJudge(llm_client=FakeJudgeLLM())
        g_res = groundedness_judge.judge("问题", "声明一。声明二。", [])
        _check(
            f"有据性裁判：score={g_res.score}（期望 0.5，1 条无支撑）",
            g_res.score == 0.5 and len(g_res.unsupported_claims) == 1,
        )
        r_res = relevance_judge.judge("问题", "回答。", [])
        _check(
            f"相关性裁判：relevance=4 映射 score={r_res.score}（期望 0.75）",
            r_res.score == 0.75,
        )

        # ---- 步骤 3：解析失败必须显式 score=None（绝非 0）----
        step = 3
        fail_g = GroundednessJudge(llm_client=FailingJudgeLLM())
        fail_r = RelevanceJudge(llm_client=FailingJudgeLLM())
        fg = fail_g.judge("问题", "回答。", [])
        fr = fail_r.judge("问题", "回答。", [])
        _check(
            f"有据性解析失败：score={fg.score}，rationale={fg.rationale}",
            fg.score is None and fg.rationale == "judge_parse_failed",
        )
        _check(
            f"相关性解析失败：score={fr.score}，rationale={fr.rationale}",
            fr.score is None and fr.rationale == "judge_parse_failed",
        )

        # ---- 步骤 4：证据提取 ----
        step = 4
        qr_full = QueryResult(
            query="q",
            answer="a",
            verdict={
                "evidence_chunks": [
                    {
                        "chunk_id": "c1",
                        "doc_id": "d1",
                        "text": "证据文本",
                        "text_hash": "",
                        "created_at": "2026-01-01T00:00:00",
                        "updated_at": "2026-01-01T00:00:00",
                    }
                ]
            },
        )
        ev = extract_evidence(qr_full)
        _check(
            f"证据提取（完整字段）：{len(ev)} 条，text={ev[0].text if ev else None}",
            len(ev) == 1 and ev[0].text == "证据文本" and ev[0].chunk_id == "c1",
        )
        qr_str = QueryResult(query="q", answer="a", verdict={"evidence_chunks": ["简单证据文本"]})
        ev2 = extract_evidence(qr_str)
        _check(f"证据提取（纯文本）：{len(ev2)} 条", len(ev2) == 1 and ev2[0].text == "简单证据文本")
        _check("证据提取（无证据）：空列表", extract_evidence(QueryResult(query="q", answer="a")) == [])

        # ---- 步骤 5：完整离线评测（Evaluator + 干跑管线）----
        step = 5
        report = Evaluator(_make_canned_pipeline(SAMPLE_DATASET), groundedness_judge, relevance_judge).evaluate(
            SAMPLE_DATASET
        )
        metrics = report.metrics
        required_metrics = {
            "refusal_rate",
            "over_refusal_rate",
            "hallucination_rate",
            "avg_groundedness",
            "avg_relevance",
            "citation_failure_rate",
        }
        _check("指标字典包含全部必需键", required_metrics <= set(metrics.keys()))
        _check(
            f"拒答率 = {metrics['refusal_rate']:.4f}（期望 0.3750）",
            abs(metrics["refusal_rate"] - 0.375) < 1e-9,
        )
        _check(
            f"过度拒答率 = {metrics['over_refusal_rate']:.4f}（期望 0.2000）",
            abs(metrics["over_refusal_rate"] - 0.2) < 1e-9,
        )
        _check(
            f"幻觉率 = {metrics['hallucination_rate']:.4f}（期望 0.3333）",
            abs(metrics["hallucination_rate"] - 1 / 3) < 1e-9,
        )
        _check(
            f"平均有据性 = {metrics['avg_groundedness']:.4f}（期望 0.5000）",
            abs(metrics["avg_groundedness"] - 0.5) < 1e-9,
        )
        _check(
            f"平均相关性 = {metrics['avg_relevance']:.4f}（期望 0.7500）",
            abs(metrics["avg_relevance"] - 0.75) < 1e-9,
        )
        _check(
            f"引用失败率 = {metrics['citation_failure_rate']:.4f}（期望 0.2000）",
            abs(metrics["citation_failure_rate"] - 0.2) < 1e-9,
        )

        # ---- 步骤 6：逐用例语义 ----
        step = 6
        by_id = {c.id: c for c in report.cases}
        c0 = by_id[SAMPLE_DATASET[0]["id"]]
        c2 = by_id[SAMPLE_DATASET[2]["id"]]
        c5 = by_id[SAMPLE_DATASET[5]["id"]]
        _check(
            f"拒答用例（{c0.id}）：跳过裁判不判分",
            c0.abstained and c0.groundedness is None and c0.relevance is None,
        )
        _check(
            f"幻觉用例（{c5.id}）：作答且被判分",
            c5.answered and c5.groundedness == 0.5 and c5.relevance == 0.75,
        )
        _check(f"失败引用用例（{c2.id}）：citations_ok=False", not c2.citations_ok)

        print()
        print("SMOKE EVAL PASSED (exit 0)")
        return 0
    except Exception as exc:  # noqa: BLE001 - 自检脚本统一兜底
        print()
        print(f"SMOKE EVAL FAILED at step {step}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
