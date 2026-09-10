"""L3 蕴含判定抽样路径回归测试（P2：裁判延迟优化基础）。

覆盖 `CitationVerifier` 的确定性抽样：
- `strict=False + sample_ratio<1` 时，送入 judge_llm 的声明数按比例下降
  （notes 明确记录「抽样评审 N/M 条引用」）；
- `sample_ratio=1.0` 全量评审；
- 抽样确定性（同输入同输出）；
- `strict=True` 忽略 sample_ratio（全量）；
- `chat_json` 只调用一次（单批评审）。
"""
from __future__ import annotations

import re
from typing import Any

from models.schemas import Chunk, RetrievedChunk
from verify.verifier import CitationVerifier


class _CountingJudge:
    """记录 chat_json 调用次数与最近一次请求，返回合法 verdicts。"""

    def __init__(self) -> None:
        self.calls = 0
        self.last_prompt: str = ""

    def chat_json(self, messages: list[dict[str, Any]], schema_hint: str | None = None):
        self.calls += 1
        self.last_prompt = messages[0]["content"] if messages else ""
        return {"verdicts": []}


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


def _answer_with_citations(n: int) -> str:
    """构造含 n 条独立声明的答案（每条各带一个 [N] 引用）。"""
    return "\n".join(f"声明 {i}。[{i + 1}]" for i in range(n))


def _sampled_count(notes: list[str]) -> int | None:
    """从 notes 解析「抽样评审 N/M 条引用」的 N。"""
    for note in notes:
        m = re.search(r"抽样评审 (\d+)/", note)
        if m:
            return int(m.group(1))
    return None


def test_sampling_reduces_l3_claims_with_ratio_half() -> None:
    judge = _CountingJudge()
    verifier = CitationVerifier(milvus=None, judge_llm=judge, strict=False, sample_ratio=0.5)
    result = verifier.verify(_answer_with_citations(8), _evidence(8))
    assert judge.calls == 1  # 单批评审调用
    sampled = _sampled_count(result.notes)
    assert sampled is not None
    assert sampled <= 4  # 步长 2，8 条最多抽 4 条（比全量 8 少）
    assert result.entailment_scores == {}  # stub 返回空 verdicts → 无蕴含分（不报错）


def test_full_ratio_judges_all_claims() -> None:
    judge = _CountingJudge()
    verifier = CitationVerifier(milvus=None, judge_llm=judge, strict=False, sample_ratio=1.0)
    result = verifier.verify(_answer_with_citations(5), _evidence(5))
    # ratio=1 时抽样 = 全量（note 仍打「抽样评审 5/5」，语义上是全量）
    assert _sampled_count(result.notes) == 5


def test_strict_mode_ignores_sample_ratio() -> None:
    judge = _CountingJudge()
    verifier = CitationVerifier(milvus=None, judge_llm=judge, strict=True, sample_ratio=0.25)
    result = verifier.verify(_answer_with_citations(6), _evidence(6))
    assert _sampled_count(result.notes) is None  # strict 全量，忽略 ratio


def test_sampling_is_deterministic() -> None:
    judge_a = _CountingJudge()
    judge_b = _CountingJudge()
    a = CitationVerifier(milvus=None, judge_llm=judge_a, strict=False, sample_ratio=0.5)
    b = CitationVerifier(milvus=None, judge_llm=judge_b, strict=False, sample_ratio=0.5)
    ra = a.verify(_answer_with_citations(10), _evidence(10))
    rb = b.verify(_answer_with_citations(10), _evidence(10))
    assert _sampled_count(ra.notes) == _sampled_count(rb.notes)
    assert judge_a.last_prompt == judge_b.last_prompt  # 同输入同抽样


def test_sample_ratio_validation_rejects_out_of_range() -> None:
    try:
        CitationVerifier(milvus=None, judge_llm=_CountingJudge(), strict=False, sample_ratio=1.5)
    except ValueError:
        return
    raise AssertionError("sample_ratio > 1 应被拒绝")
