"""R3-B：验证阶段质量指标埋点测试。

覆盖 verify/verifier.py 的 metrics 埋点：
- verify.total 每次验证 +1；
- verify.citations.failed 按非 ok 引用数计数（引用失败率分子）；
- verify.l3.evaluated 按 L3 实际判定数计数；
- 埋点失败不影响验证主流程（指标是观测件）。
"""
from __future__ import annotations

from typing import Any

from core.metrics import get_metrics
from models.schemas import Chunk, RetrievedChunk
from verify.verifier import CitationVerifier


class _OkJudge:
    """judge 返回指定 status 的 verdicts（默认 supported）。"""

    def __init__(self, status: str = "supported") -> None:
        self.status = status

    def chat_json(self, messages: list[dict[str, Any]], schema_hint: str | None = None):
        claims = [
            line.split("声明：", 1)[1].split("（引用编号", 1)[0]
            for line in messages[0]["content"].splitlines()
            if line.strip().startswith(tuple(f"{i}. 声明：" for i in range(1, 50)))
        ]
        return {
            "verdicts": [
                {"claim": c, "status": self.status, "cited_chunk_ids": [], "reason": "x"}
                for c in claims
            ]
        }


def _evidence(n: int) -> list[RetrievedChunk]:
    out = []
    for i in range(n):
        chunk = Chunk(
            chunk_id=f"chunk-{i}",
            doc_id="doc",
            text=f"证据文本 {i}。" * 3,
            text_hash=f"hash-{i}",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
        )
        out.append(RetrievedChunk(chunk=chunk, score=0.9, rank=i + 1))
    return out


def _answer(n: int) -> str:
    return "\n".join(f"声明 {i}。[{i + 1}]" for i in range(n))


def _snapshot():
    return get_metrics().snapshot()


def _count(key: str) -> int:
    entry = _snapshot().get(key)
    return int(entry.get("count", 0)) if entry else 0


def test_verify_total_is_incremented() -> None:
    metrics = get_metrics()
    metrics.reset()
    verifier = CitationVerifier(milvus=None, judge_llm=_OkJudge(), strict=False, sample_ratio=1.0)
    verifier.verify(_answer(2), _evidence(2))
    assert _count("verify.total") == 1
    verifier.verify(_answer(2), _evidence(2))
    assert _count("verify.total") == 2


def test_citations_failed_counted_for_bad_citations() -> None:
    metrics = get_metrics()
    metrics.reset()
    # judge 判 unsupported -> 引用变非 ok -> 引用失败计数
    verifier = CitationVerifier(milvus=None, judge_llm=_OkJudge(status="unsupported"), strict=True)
    verifier.verify(_answer(2), _evidence(2))
    assert _count("verify.citations.failed") >= 1


def test_all_supported_has_zero_failed() -> None:
    metrics = get_metrics()
    metrics.reset()
    verifier = CitationVerifier(milvus=None, judge_llm=_OkJudge(status="supported"), strict=True)
    verifier.verify(_answer(2), _evidence(2))
    assert _count("verify.citations.failed") == 0


def test_l3_evaluated_counts_judged_claims() -> None:
    metrics = get_metrics()
    metrics.reset()
    verifier = CitationVerifier(milvus=None, judge_llm=_OkJudge(), strict=True)
    verifier.verify(_answer(3), _evidence(3))
    assert _count("verify.l3.evaluated") >= 1


def test_metrics_failure_does_not_break_verify(monkeypatch) -> None:
    metrics = get_metrics()
    metrics.reset()

    def boom(*args, **kwargs):
        raise RuntimeError("metrics down")

    monkeypatch.setattr("core.metrics.MetricsRegistry.incr", boom)
    verifier = CitationVerifier(milvus=None, judge_llm=_OkJudge(), strict=False, sample_ratio=1.0)
    result = verifier.verify(_answer(2), _evidence(2))
    assert result is not None  # 埋点失败不影响验证结果
