from __future__ import annotations

from retrieval.qa_matcher import (
    QARecord,
    match_qa,
    match_to_retrieved_chunk,
    merge_qa_into_chunks,
    normalize_question,
    parse_qa_chunk_id,
    qa_chunk_id,
)
from retrieval.qa_retrieval import (
    apply_qa_retrieval,
    qa_evidence_enrichment,
    qa_records_from_bundle,
)
from verify.abstention import AbstentionGate


def _rec(**kwargs) -> QARecord:
    base = {
        "qa_id": "qa-1",
        "question": "如何重置 KnowledgeOps Actor Bearer？",
        "answer": "在系统设置的工作区身份页重新签发。",
        "revision": 2,
        "origin": "manual",
        "tenant_id": "t1",
        "dataset_id": "d1",
        "alternatives": (),
        "negatives": (),
    }
    base.update(kwargs)
    return QARecord(**base)


def test_normalize_question_is_nfkc_casefold_whitespace():
    assert normalize_question("  How\tReset  ") == "how reset"
    assert normalize_question("如何重置？") == "如何重置?"


def test_exact_question_match_scores_one():
    rec = _rec()
    matches = match_qa("如何重置 KnowledgeOps Actor Bearer？", [rec])
    assert len(matches) == 1
    assert matches[0].score == 1.0
    assert matches[0].matched_on == "question"
    assert matches[0].qa_id == "qa-1"


def test_alternative_exact_match_scores_095():
    rec = _rec(alternatives=("在哪里配置 Actor Token？",))
    matches = match_qa("在哪里配置 Actor Token？", [rec])
    assert matches and matches[0].matched_on == "alternative"
    assert matches[0].score == 0.95
    assert matches[0].answer == rec.answer


def test_weak_overlap_below_min_score_is_dropped():
    rec = _rec(question="如何重置 KnowledgeOps Actor Bearer？", answer="A")
    matches = match_qa("今天天气怎么样", [rec], min_score=0.55)
    assert matches == []


def test_negative_question_suppresses_hit():
    rec = _rec(negatives=("如何重置 KnowledgeOps Actor Bearer？",))
    assert match_qa("如何重置 KnowledgeOps Actor Bearer？", [rec]) == []
    # containment negative also suppresses
    rec2 = _rec(qa_id="qa-2", negatives=("如何重置 Bearer",))
    assert match_qa("如何重置 Bearer 凭据", [rec2]) == []


def test_match_to_retrieved_chunk_and_merge():
    rec = _rec(source_document_id="doc-9")
    m = match_qa(rec.question, [rec])[0]
    rc = match_to_retrieved_chunk(m, rank=1)
    assert rc.branch == "qa"
    assert rc.chunk.chunk_id == qa_chunk_id("qa-1")
    assert rc.chunk.doc_id == "doc-9"
    assert rc.chunk.metadata["source_kind"] == "qa"
    assert parse_qa_chunk_id(rc.chunk.chunk_id) == "qa-1"
    assert "如何重置" in rc.chunk.text

    class _Doc:
        pass

    from datetime import datetime

    from models.schemas import Chunk, RetrievedChunk

    doc = RetrievedChunk(
        chunk=Chunk(
            chunk_id="doc::1",
            doc_id="doc-1",
            text="doc text",
            text_hash="h",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        ),
        score=0.4,
        rank=1,
        branch="hybrid",
    )
    merged = merge_qa_into_chunks([doc], [rc])
    assert merged[0].branch == "qa"
    assert merged[0].rank == 1
    assert merged[1].chunk.chunk_id == "doc::1"
    # dedupe
    again = merge_qa_into_chunks(merged, [rc])
    assert len(again) == 2


def test_apply_qa_retrieval_injects_and_marks_qa_hit():
    from datetime import datetime

    from models.schemas import Chunk, RetrievedChunk

    bundle = [
        {
            "qa_id": "qa-9",
            "question": "什么是知识生命线？",
            "answer": "从源文档到答案的可追溯链。",
            "revision": 1,
            "origin": "import",
            "tenant_id": "t1",
            "dataset_id": "d1",
            "alternatives": ["Knowledge Lifeline 是什么？"],
            "negatives": [],
        }
    ]
    doc = RetrievedChunk(
        chunk=Chunk(
            chunk_id="doc::2",
            doc_id="doc-2",
            text="无关文档",
            text_hash="h2",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        ),
        score=0.01,
        rank=1,
        branch="hybrid",
    )

    class _P:
        qa_retrieval_on = True
        qa_match_min_score = 0.55
        qa_match_top_k = 3

    class _S:
        pipeline = _P()

    chunks, traces, hit = apply_qa_retrieval(
        "Knowledge Lifeline 是什么？",
        [doc],
        tenant_id="t1",
        dataset_id="d1",
        settings=_S(),
        bundle=bundle,
    )
    assert hit is True
    assert chunks[0].branch == "qa"
    assert any("QA 检索" in t for t in traces)

    # disabled
    class _Off:
        qa_retrieval_on = False
        qa_match_min_score = 0.55
        qa_match_top_k = 3

    class _SOff:
        pipeline = _Off()

    chunks2, _, hit2 = apply_qa_retrieval(
        "Knowledge Lifeline 是什么？",
        [doc],
        settings=_SOff(),
        bundle=bundle,
    )
    assert hit2 is False
    assert chunks2[0].chunk.chunk_id == "doc::2"


def test_gate_accepts_qa_only_absolute_scores():
    gate = AbstentionGate(retrieval_threshold=0.3, entailment_threshold=0.5)
    abstain, reason = gate.decide([1.0], None, retrieval_scores_comparable=True)
    assert abstain is False
    # RRF-like without comparable still needs cosines; empty comparable False skips step2
    abstain2, _ = gate.decide([0.01], None, retrieval_scores_comparable=False, dense_cosines=None)
    assert abstain2 is False


def test_qa_evidence_enrichment_adds_lifeline_fields():
    dump = {
        "chunk_id": "qa::qa-1",
        "doc_id": "doc-9",
        "metadata": {"qa_id": "qa-1", "qa_revision": 2, "qa_origin": "manual"},
    }
    enriched = qa_evidence_enrichment(dump)
    assert enriched["source_kind"] == "qa"
    assert enriched["qa_id"] == "qa-1"
    assert enriched["qa_revision"] == 2
    plain = qa_evidence_enrichment({"chunk_id": "doc::1", "metadata": {}})
    assert "source_kind" not in plain


def test_qa_records_from_bundle_skips_empty_ids():
    records = qa_records_from_bundle(
        [
            {"qa_id": "", "question": "x", "answer": "y"},
            {
                "qa_id": "qa-3",
                "question": "Q",
                "answer": "A",
                "alternatives": ["Alt", ""],
                "negatives": ["Neg"],
                "tenant_id": "t",
                "dataset_id": "d",
            },
        ]
    )
    assert len(records) == 1
    assert records[0].qa_id == "qa-3"
    assert records[0].alternatives == ("Alt",)
    assert records[0].negatives == ("Neg",)


def test_apply_qa_retrieval_emits_metrics(monkeypatch):
    from types import SimpleNamespace

    from core import metrics as metrics_mod
    from retrieval.qa_retrieval import apply_qa_retrieval

    counts: dict[str, float] = {}

    class _M:
        def incr(self, name, tags=None, value=1.0):
            counts[name] = counts.get(name, 0) + value

    monkeypatch.setattr(metrics_mod, "get_metrics", lambda: _M())

    class _Off:
        pipeline = SimpleNamespace(qa_retrieval_on=False)

    apply_qa_retrieval("q", [], settings=_Off(), bundle=[{"qa_id": "x", "question": "q", "answer": "a"}])
    assert counts.get("query.qa_retrieval.enabled_skip") == 1

    counts.clear()
    class _On:
        pipeline = SimpleNamespace(qa_retrieval_on=True, qa_match_min_score=0.55, qa_match_top_k=3)

    apply_qa_retrieval("什么都没命中", [], settings=_On(), bundle=[])
    assert counts.get("query.qa_retrieval.no_bundle") == 1

    counts.clear()
    apply_qa_retrieval(
        "完全不相关的问题",
        [],
        settings=_On(),
        bundle=[{"qa_id": "qa-1", "question": "如何重置？", "answer": "A"}],
    )
    assert counts.get("query.qa_retrieval.no_match") == 1

    counts.clear()
    apply_qa_retrieval(
        "如何重置？",
        [],
        settings=_On(),
        bundle=[{"qa_id": "qa-1", "question": "如何重置？", "answer": "A"}],
    )
    assert counts.get("query.qa_retrieval.hit") == 1
