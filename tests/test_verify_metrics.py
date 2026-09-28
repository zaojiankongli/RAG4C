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


def _sum(key: str) -> int:
    """`incr(name, value=N)` 记的是**一次值为 N 的采样**：count 数调用次数，sum 才是累加值。

    这点被 `verify/verifier.py` 的注释误导过一次——它写"按非 ok 引用数计数"，
    而前端读的是 `.count`，于是"引用失败率"实际是"有引用失败的验证占比"。
    """
    entry = _snapshot().get(key)
    return int(entry.get("sum", 0)) if entry else 0


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


def test_citations_total_is_the_denominator_of_the_failure_rate() -> None:
    """引用失败率的**分母**必须是引用条数，不是验证次数。

    `verify.total` 每次验证 +1（本文件第一条用例就钉着这个语义），而前端
    `MonitorPage.tsx:559` 拿 `verify.citations.failed.count / verify.total.count` 相除
    再标成 `%`。两边都是**验证次数**，于是这张卡实际读数是
    "有引用失败的验证占比"，却被写成"引用失败率"——
    实机一轮：2 次验证、界面里 21~47 条引用，卡片显示 100.0 %。
    """
    metrics = get_metrics()
    metrics.reset()
    verifier = CitationVerifier(milvus=None, judge_llm=_OkJudge(status="unsupported"), strict=True)
    result = verifier.verify(_answer(4), _evidence(4))
    total = _sum("verify.citations.total")
    failed = _sum("verify.citations.failed")
    assert total == len(result.citations) == 4
    assert failed == len([c for c in result.citations if c.status != "ok"])
    assert failed <= total


def test_all_supported_reports_zero_failures_against_a_nonzero_total() -> None:
    """反向配对：全 ok 时 total 仍要计数，否则分母会跟着一起消失。"""
    metrics = get_metrics()
    metrics.reset()
    verifier = CitationVerifier(milvus=None, judge_llm=_OkJudge(status="supported"), strict=True)
    result = verifier.verify(_answer(3), _evidence(3))
    assert _sum("verify.citations.total") == len(result.citations) == 3
    assert _sum("verify.citations.failed") == 0


def test_failed_citation_count_is_a_sum_not_a_call_count() -> None:
    """`verify.citations.failed` 的引用条数在 `sum` 里，`count` 只是调用次数。

    钉住这条是因为前端读的是 `.count`：一次 4 条全失败的验证，
    count=1、sum=4。想按引用算比率就必须用 sum。
    """
    metrics = get_metrics()
    metrics.reset()
    verifier = CitationVerifier(milvus=None, judge_llm=_OkJudge(status="unsupported"), strict=True)
    verifier.verify(_answer(4), _evidence(4))
    assert _count("verify.citations.failed") == 1
    assert _sum("verify.citations.failed") == 4
