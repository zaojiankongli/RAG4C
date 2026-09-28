from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import rag
import rag_stream
from config.settings import PipelineSettings
from models.schemas import Chunk, RetrievedChunk
from retrieval.pipeline import RetrievalPipeline
from verify.abstention import AbstentionGate
from verify.verifier import VerificationResult

_NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)

_QA_BUNDLE = [
    {
        "qa_id": "qa-faq-1",
        "question": "How do I rotate credentials?",
        "answer": "Open Security > Credentials and rotate.",
        "revision": 2,
        "origin": "import",
        "tenant_id": "tenant-a",
        "dataset_id": "dataset-a",
        "source_document_id": "doc-sec",
        "alternatives": ["Where can I rotate credentials?"],
        "negatives": [],
    }
]


class _Embedder:
    def embed_query(self, _text: str) -> list[float]:
        return [1.0, 0.0]


class _MilvusEmpty:
    @staticmethod
    def build_filters(**_kwargs: Any) -> None:
        return None

    @staticmethod
    def hybrid_search(**_kwargs: Any) -> list[RetrievedChunk]:
        return []


class _MilvusWeak:
    @staticmethod
    def build_filters(**_kwargs: Any) -> None:
        return None

    @staticmethod
    def hybrid_search(**_kwargs: Any) -> list[RetrievedChunk]:
        return [
            RetrievedChunk(
                chunk=Chunk(
                    chunk_id="doc::weak",
                    doc_id="doc-weak",
                    text="unrelated document",
                    text_hash="h",
                    created_at=_NOW,
                    updated_at=_NOW,
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                ),
                score=0.01,
                rank=1,
                branch="hybrid",
            )
        ]


class _Rewriter:
    @staticmethod
    def rewrite(query: str) -> tuple[str, bool]:
        return query, False


class _Router:
    @staticmethod
    def route(_query: str) -> Any:
        from models.schemas import RouteDecision

        return RouteDecision(target="hybrid", confidence=0.9)


class _RerankerOff:
    enabled = False

    @staticmethod
    def rerank(_query: str, chunks: list[Chunk]) -> list[float]:
        return []


class _Generated:
    def __init__(self, answer: str) -> None:
        self.answer = answer


class _Generator:
    def __init__(self) -> None:
        self.seen_chunks: list[RetrievedChunk] = []

    def generate(self, query: str, chunks: list[RetrievedChunk]) -> _Generated:
        self.seen_chunks = list(chunks)
        if not chunks:
            raise AssertionError("generator called without evidence")
        return _Generated("Rotate credentials from Security > Credentials.")


class _StreamGenerator:
    def __init__(self) -> None:
        self.seen_chunks: list[RetrievedChunk] = []

    def generate_stream(self, query: str, chunks: list[RetrievedChunk]) -> Iterator[str]:
        self.seen_chunks = list(chunks)
        yield "Rotate credentials from Security > Credentials."

    @staticmethod
    def _dedup_chunks(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        return chunks

    @staticmethod
    def postprocess_stream(text: str, _evidence_count: int) -> _Generated:
        return _Generated(text)


class _Verifier:
    @staticmethod
    def verify(_answer: str, chunks: list[RetrievedChunk]) -> VerificationResult:
        return VerificationResult(
            supported=True,
            entailment_scores={"claim": 1.0},
            entailment_evaluated=True,
        )


class _Settings:
    def __init__(self, pipeline: PipelineSettings) -> None:
        self.pipeline = pipeline
        self.observability = None
        self.tenant = SimpleNamespace(enforced=False, default_tenant="default")


def _comp(milvus: Any, generator: Any, settings: Any) -> dict[str, Any]:
    pipeline = getattr(settings, "pipeline", settings)
    retrieval = RetrievalPipeline(
        embedder=_Embedder(),
        milvus=milvus,
        reranker=_RerankerOff(),
        rewriter=_Rewriter(),
        router=_Router(),
        settings=pipeline,
    )
    return {
        "retrieval": retrieval,
        "generator": generator,
        "verifier": _Verifier(),
        "gate": AbstentionGate(retrieval_threshold=0.3, entailment_threshold=0.5),
        "settings": settings,
    }


def test_answer_sequential_injects_qa_when_hybrid_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from retrieval import qa_retrieval as qr

    monkeypatch.setattr(qr, "load_qa_bundle_best_effort", lambda *a, **k: list(_QA_BUNDLE))
    pipeline = PipelineSettings(
        qa_retrieval_on=True,
        complexity_gate_on=False,
        rerank_on=False,
        source_diversity="off",
        top_k=2,
        hyde_on=False,
        subqueries_on=False,
        stepback_on=False,
    )
    settings = _Settings(pipeline)
    gen = _Generator()
    comp = _comp(_MilvusEmpty(), gen, settings)
    result = rag._answer_sequential(
        comp,
        "How do I rotate credentials?",
        None,
        query_id="q-qa-1",
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        retry=False,
    )
    assert result.abstained is False
    assert result.answer
    assert gen.seen_chunks
    assert gen.seen_chunks[0].branch == "qa"
    assert gen.seen_chunks[0].chunk.chunk_id == "qa::qa-faq-1"
    evidence = (result.verdict or {}).get("evidence_chunks") or []
    assert any(item.get("chunk_id") == "qa::qa-faq-1" for item in evidence)
    assert any(item.get("source_kind") == "qa" for item in evidence)
    assert any(item.get("qa_id") == "qa-faq-1" for item in evidence)
    assert any("QA 检索" in t for t in result.traces)


def test_answer_sequential_qa_disables_retrieval_abstention_on_weak_hybrid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from retrieval import qa_retrieval as qr

    monkeypatch.setattr(qr, "load_qa_bundle_best_effort", lambda *a, **k: list(_QA_BUNDLE))
    pipeline = PipelineSettings(
        qa_retrieval_on=True,
        complexity_gate_on=False,
        rerank_on=False,
        source_diversity="off",
        top_k=2,
        hyde_on=False,
        subqueries_on=False,
        stepback_on=False,
    )
    settings = _Settings(pipeline)
    gen = _Generator()
    comp = _comp(_MilvusWeak(), gen, settings)
    # Without rerank, hybrid scores are RRF-like (0.01). QA absolute score must keep path alive.
    result = rag._answer_sequential(
        comp,
        "How do I rotate credentials?",
        None,
        query_id="q-qa-2",
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        retry=False,
    )
    assert result.abstained is False
    assert any(rc.branch == "qa" for rc in gen.seen_chunks)


def test_answer_sequential_catalog_failure_silently_keeps_hybrid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from retrieval import qa_retrieval as qr

    def _apply_boom(*_a, **_k):
        raise RuntimeError("catalog unavailable")

    monkeypatch.setattr(qr, "apply_qa_retrieval", _apply_boom)
    pipeline = PipelineSettings(
        qa_retrieval_on=True,
        rerank_on=True,
        complexity_gate_on=False,
        source_diversity="off",
        top_k=2,
    )
    settings = _Settings(pipeline)

    class _MilvusStrong(_MilvusWeak):
        @staticmethod
        def hybrid_search(**_kwargs: Any) -> list[RetrievedChunk]:
            return [
                RetrievedChunk(
                    chunk=Chunk(
                        chunk_id="doc::ok",
                        doc_id="doc-ok",
                        text="supported answer body",
                        text_hash="ok",
                        created_at=_NOW,
                        updated_at=_NOW,
                        tenant_id="tenant-a",
                        dataset_id="dataset-a",
                    ),
                    score=0.9,
                    rank=1,
                    branch="hybrid",
                )
            ]

    class _RerankerOn(_RerankerOff):
        enabled = True

        @staticmethod
        def rerank(_query: str, chunks: list[Chunk]) -> list[float]:
            return [0.9 for _ in chunks]

    gen = _Generator()
    from retrieval.pipeline import RetrievalPipeline as RP

    comp = _comp(_MilvusStrong(), gen, settings)
    comp["retrieval"] = RP(
        embedder=_Embedder(),
        milvus=_MilvusStrong(),
        reranker=_RerankerOn(),
        rewriter=_Rewriter(),
        router=_Router(),
        settings=pipeline,
    )
    result = rag._answer_sequential(
        comp,
        "supported question",
        None,
        query_id="q-qa-3",
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        retry=False,
    )
    joined = "\n".join(result.traces)
    assert "QA 检索跳过" in joined
    assert "doc::ok" in [rc.chunk.chunk_id for rc in gen.seen_chunks]
    # hybrid path still answers when catalog is down
    assert result.abstained is False, result.traces
    assert result.answer


def test_stream_path_injects_qa_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    from retrieval import qa_retrieval as qr

    monkeypatch.setattr(qr, "load_qa_bundle_best_effort", lambda *a, **k: list(_QA_BUNDLE))
    pipeline = PipelineSettings(
        qa_retrieval_on=True,
        graph_engine_on=False,
        complexity_gate_on=False,
        rerank_on=False,
        source_diversity="off",
        top_k=2,
        hyde_on=False,
        subqueries_on=False,
        stepback_on=False,
    )
    settings = _Settings(pipeline)
    sgen = _StreamGenerator()
    comp = _comp(_MilvusEmpty(), sgen, settings)

    import rag as rag_mod
    from core import observability as obs

    monkeypatch.setattr(obs, "setup_observability", lambda *_a, **_k: None)
    monkeypatch.setattr(rag_mod, "get_pipeline", lambda _s=None: comp)
    monkeypatch.setattr(
        "rag_stream.resolve_tenant",
        lambda tid, _s=None: tid or "default",
    )
    events = list(
        rag_stream.answer_query_stream(
            "How do I rotate credentials?",
            None,
            retry=False,
            settings=settings,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            query_id="q-qa-stream",
        )
    )
    done = next(e for e in events if e.get("type") == "done")
    payload = done["result"]
    assert payload["abstained"] is False
    evidence = (payload.get("verdict") or {}).get("evidence_chunks") or []
    assert any(item.get("source_kind") == "qa" for item in evidence)
    assert any(item.get("qa_id") == "qa-faq-1" for item in evidence)
    flat = payload.get("evidence") or []
    # 扁平投影按契约只输出 6 个键，QA 权威字段走 verdict.evidence_chunks；
    # 前端靠 chunk_id 的 qa:: 前缀推导 source_kind/qa_id，故此处钉前缀而非键名。
    assert any(
        str(item.get("chunk_id") or "").startswith("qa::") for item in flat
    ), flat
    assert sgen.seen_chunks and sgen.seen_chunks[0].branch == "qa"


def test_answer_sequential_blocks_acl_filtered_global_qa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """钉住 rag.py 真把 acl 传下去了：没传的话这条路径会照常注入 FAQ。

    单元级断言只覆盖 apply_qa_retrieval 自己的分支，入口忘传参数时仍然全绿。
    """
    from retrieval import qa_retrieval as qr

    calls: list = []
    monkeypatch.setattr(
        qr, "load_qa_bundle_best_effort", lambda *a, **k: calls.append(a) or list(_QA_BUNDLE)
    )
    pipeline = PipelineSettings(
        qa_retrieval_on=True,
        complexity_gate_on=False,
        rerank_on=False,
        source_diversity="off",
        top_k=2,
        hyde_on=False,
        subqueries_on=False,
        stepback_on=False,
    )
    gen = _Generator()
    comp = _comp(_MilvusEmpty(), gen, _Settings(pipeline))
    result = rag._answer_sequential(
        comp,
        "How do I rotate credentials?",
        ["hr"],
        query_id="q-acl-1",
        tenant_id="tenant-a",
        dataset_id=None,
        retry=False,
    )
    assert calls == [], "全域 + acl 不该去查 FAQ 包"
    assert not [c for c in gen.seen_chunks if c.branch == "qa"]
    assert any("QA 检索跳过" in t for t in result.traces)


def test_answer_sequential_injects_global_qa_when_acl_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """反向证据：上一红的理由是 acl，不是全域本身或入口接线断了。"""
    from retrieval import qa_retrieval as qr

    monkeypatch.setattr(qr, "load_qa_bundle_best_effort", lambda *a, **k: list(_QA_BUNDLE))
    pipeline = PipelineSettings(
        qa_retrieval_on=True,
        complexity_gate_on=False,
        rerank_on=False,
        source_diversity="off",
        top_k=2,
        hyde_on=False,
        subqueries_on=False,
        stepback_on=False,
    )
    gen = _Generator()
    comp = _comp(_MilvusEmpty(), gen, _Settings(pipeline))
    rag._answer_sequential(
        comp,
        "How do I rotate credentials?",
        None,
        query_id="q-global-1",
        tenant_id="tenant-a",
        dataset_id=None,
        retry=False,
    )
    assert gen.seen_chunks and gen.seen_chunks[0].branch == "qa"


def test_answer_evidence_repository_accepts_qa_chunk_ids(tmp_path: Path) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    from core.answer_evidence_facts import AnswerEvidenceRepository
    from models.orm import Account, Base, Tenant, TenantMember

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id="tenant-a", name="A", status="active"),
                Account(id="owner-a", name="O", email="o@a.test"),
                TenantMember(account_id="owner-a", tenant_id="tenant-a", role="owner"),
            ]
        )
        session.commit()

    repo = AnswerEvidenceRepository(engine)
    fact_id = repo.record_answer_fact(
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        run_id="run-qa",
        question="How do I rotate credentials?",
        answer="Open Security > Credentials and rotate.",
        route="rag",
        outcome="answered",
        citations=[{"chunk_id": "qa::qa-faq-1", "status": "ok"}],
        evidence=[
            {
                "chunk_id": "qa::qa-faq-1",
                "doc_id": "doc-sec",
                "text": "How do I rotate credentials?\nOpen Security > Credentials and rotate.",
                "source_kind": "qa",
                "qa_id": "qa-faq-1",
                "qa_revision": 2,
            }
        ],
    )
    detail = repo.get_answer_fact("tenant-a", fact_id)
    assert detail is not None
    assert detail["evidence_refs"]
    assert detail["evidence_refs"][0]["chunk_id"] == "qa::qa-faq-1"
