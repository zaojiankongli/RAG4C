from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import Session

from config.settings import PipelineSettings, TenantSettings
from core.knowledge_governance import AuditContext
from core.retrieval_experiment_runner import (
    RetrievalExperimentRunner,
    RetrievalExperimentVariant,
)
from core.retrieval_experiments import (
    RetrievalExperimentConflict,
    RetrievalExperimentRepository,
)
from models.orm import (
    Base,
    ChunkHead,
    ChunkRevision,
    Dataset,
    Document,
    KnowledgeAuditEvent,
    RetrievalExperiment,
    Tenant,
)
from models.schemas import Chunk, RetrievedChunk, RouteDecision
from retrieval.pipeline import RetrievalPipeline

_NOW = datetime(2026, 8, 25, tzinfo=timezone.utc)


def _audit(request_id: str = "run-request") -> AuditContext:
    return AuditContext(actor_id="editor-a", request_id=request_id, request_ip="127.0.0.1")


def _engine(tmp_path: Path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'runner.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id="tenant-a", name="Tenant A"),
                Tenant(id="tenant-b", name="Tenant B"),
                Dataset(
                    id="dataset-a",
                    tenant_id="tenant-a",
                    name="Dataset A",
                    status="active",
                    serving_generation=12,
                ),
                Dataset(
                    id="dataset-b",
                    tenant_id="tenant-b",
                    name="Dataset B",
                    status="active",
                    serving_generation=7,
                ),
                Document(
                    id="doc-a",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    name="Authority A",
                    source_uri="https://user:pass@example.test/manual?q=ok&token=secret",
                    source_type="web",
                    content_revision=4,
                ),
                Document(
                    id="doc-b",
                    tenant_id="tenant-b",
                    dataset_id="dataset-b",
                    name="Foreign B",
                    content_revision=2,
                ),
                Document(
                    id="doc-future",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    name="Future A",
                    content_revision=1,
                    effective_from=_NOW + timedelta(days=3650),
                ),
                Document(
                    id="doc-parent",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    name="Parent Authority",
                    content_revision=5,
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                ChunkHead(
                    id="chunk-a",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    document_id="doc-a",
                    chunk_index=0,
                    document_revision=4,
                    content_revision=8,
                    source_content="authority source",
                    content="authority content",
                    content_hash="authority-hash",
                    enabled=True,
                    chunk_metadata={"acl": "legal"},
                ),
                ChunkHead(
                    id="chunk-b",
                    tenant_id="tenant-b",
                    dataset_id="dataset-b",
                    document_id="doc-b",
                    chunk_index=0,
                    document_revision=2,
                    content_revision=3,
                    source_content="foreign source",
                    content="foreign secret",
                    content_hash="foreign-hash",
                    enabled=True,
                    chunk_metadata={"acl": "legal"},
                ),
                ChunkHead(
                    id="chunk-future",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    document_id="doc-future",
                    chunk_index=0,
                    document_revision=1,
                    content_revision=1,
                    source_content="future source",
                    content="future content",
                    content_hash="future-hash",
                    enabled=True,
                    chunk_metadata={"acl": "legal"},
                ),
                ChunkHead(
                    id="chunk-finance",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    document_id="doc-a",
                    chunk_index=1,
                    document_revision=4,
                    content_revision=1,
                    source_content="finance source",
                    content="finance content",
                    content_hash="finance-hash",
                    enabled=True,
                    chunk_metadata={"acl": "finance"},
                ),
                ChunkHead(
                    id="chunk-parent",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    document_id="doc-parent",
                    chunk_index=0,
                    chunk_role="parent",
                    document_revision=5,
                    content_revision=3,
                    source_content="parent source",
                    content="parent current",
                    content_hash="parent-current-hash",
                    enabled=True,
                    chunk_metadata={"acl": "legal"},
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                ChunkRevision(
                    id="revision-flat-7",
                    chunk_id="chunk-a",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    document_id="doc-a",
                    revision=7,
                    content="flat historical",
                    content_hash="flat-historical-hash",
                ),
                ChunkRevision(
                    id="revision-parent-2",
                    chunk_id="chunk-parent",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    document_id="doc-parent",
                    revision=2,
                    content="parent historical",
                    content_hash="parent-historical-hash",
                ),
            ]
        )
        session.commit()
    return engine


def _retrieved(
    chunk_id: str,
    document_id: str,
    *,
    tenant_id: str,
    dataset_id: str,
    score: float = 0.75,
    document_revision: int = 4,
    content_revision: int = 8,
    text_hash: str = "authority-hash",
    text: str = "retrieved payload",
    acl: str = "legal",
    branch: str = "hybrid",
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk=Chunk(
            chunk_id=chunk_id,
            doc_id=document_id,
            text=text,
            text_hash=text_hash,
            created_at=_NOW,
            updated_at=_NOW,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_revision=document_revision,
            content_revision=content_revision,
            source="https://attacker:secret@example.test/?api_key=secret",
            metadata={"acl": acl},
        ),
        score=score,
        rank=99,
        branch=branch,
        dense_cosine=0.61,
    )



class _Embedder:
    def embed_query(self, _text: str) -> list[float]:
        return [1.0, 0.0]


class _Milvus:
    def __init__(self, batches: list[Any], *, on_search=None) -> None:
        self.batches = list(batches)
        self.on_search = on_search
        self.calls = 0
        self.filter_calls: list[dict[str, Any]] = []

    def build_filters(self, **kwargs: Any) -> frozenset[str] | None:
        self.filter_calls.append(dict(kwargs))
        if kwargs.get("acl") and kwargs.get("acl_filter_on"):
            return frozenset(str(item) for item in kwargs["acl"])
        return None

    def hybrid_search(self, **kwargs: Any) -> list[RetrievedChunk]:
        self.calls += 1
        if self.on_search is not None:
            self.on_search(self.calls)
        value = self.batches[min(self.calls - 1, len(self.batches) - 1)]
        if isinstance(value, Exception):
            raise value
        items = [item.model_copy(deep=True) for item in value]
        allowed = kwargs.get("filter_expr")
        if isinstance(allowed, frozenset):
            items = [item for item in items if item.chunk.metadata.get("acl") in allowed]
        return items

    @staticmethod
    def get_chunks_by_ids(_ids: list[str]) -> list[Chunk]:
        return []



class _Reranker:
    def rerank(self, _query: str, chunks: list[Chunk]) -> list[float]:
        return [0.9 for _ in chunks]


class _NeverLlm:
    def __init__(self) -> None:
        self.calls = 0

    def rewrite(self, _query: str) -> tuple[str, bool]:
        self.calls += 1
        raise AssertionError("LLM-backed rewriter must not run for a fixed route")

    def route(self, _query: str) -> RouteDecision:
        self.calls += 1
        raise AssertionError("LLM-backed router must not run for a fixed route")

    def generate(self, _query: str):
        self.calls += 1
        raise AssertionError("query enhancement LLM must be disabled")


class _Router:
    @staticmethod
    def route(_query: str) -> RouteDecision:
        return RouteDecision(target="hybrid", confidence=1.0)


def _base_pipeline(
    milvus: _Milvus,
    *,
    reranker: Any = None,
    reranker_cb: Any = None,
    graph_retriever: Any = None,
) -> tuple[RetrievalPipeline, list[_NeverLlm]]:
    rewriter = _NeverLlm()
    router = _NeverLlm()
    hyde = _NeverLlm()
    subqueries = _NeverLlm()
    stepback = _NeverLlm()
    settings = SimpleNamespace(
        pipeline=PipelineSettings(
            top_k=8,
            hybrid_search_on=True,
            rerank_on=True,
            graph_retrieval_on=True,
            sentence_window_on=True,
            source_diversity="group_mmr",
            complexity_gate_on=True,
            acl_filter_on=False,
            hyde_on=True,
            subqueries_on=True,
            stepback_on=True,
        ),
        graph=SimpleNamespace(use_llm_rerank=True),
        tenant=TenantSettings(enforced=True, default_tenant="tenant-a"),
    )
    return (
        RetrievalPipeline(
            embedder=_Embedder(),
            milvus=milvus,
            reranker=reranker or _Reranker(),
            rewriter=rewriter,
            router=router,
            settings=settings,
            graph_retriever=graph_retriever,
            hyde=hyde,
            subqueries=subqueries,
            stepback=stepback,
            reranker_cb=reranker_cb,
        ),
        [rewriter, router, hyde, subqueries, stepback],
    )



def _variant(name: str, **overrides: Any) -> RetrievalExperimentVariant:
    values = {
        "name": name,
        "route_target": "hybrid",
        "top_k": 4,
        "hybrid_search_on": True,
        "rerank_on": False,
        "graph_retrieval_on": False,
        "sentence_window_on": False,
        "source_diversity": "off",
    }
    values.update(overrides)
    return RetrievalExperimentVariant(**values)


def test_runner_isolates_strategy_maps_current_authority_and_filters_scope(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    milvus = _Milvus(
        [[
            _retrieved(
                "chunk-a", "doc-a", tenant_id="tenant-a", dataset_id="dataset-a"
            ),
            _retrieved(
                "chunk-b",
                "doc-b",
                tenant_id="tenant-b",
                dataset_id="dataset-b",
                document_revision=2,
                content_revision=3,
                text_hash="foreign-hash",
            ),
            _retrieved(
                "chunk-future",
                "doc-future",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                document_revision=1,
                content_revision=1,
                text_hash="future-hash",
            ),
        ]]
    )
    base, llm_components = _base_pipeline(milvus)
    original = base.settings.pipeline.model_dump()
    runner = RetrievalExperimentRunner(RetrievalExperimentRepository(engine), base)

    outcome = runner.run(
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        query="Where is the manual?",
        acl=["legal"],
        variants=[_variant("dense-only", hybrid_search_on=False, top_k=4)],
        audit=_audit(),
    )

    assert outcome.dataset_serving_generation == 12
    assert len(outcome.items) == 1
    item = outcome.items[0]
    assert item.name == "dense-only"
    assert item.status == "completed"
    assert item.route == "hybrid"
    assert item.result_count == 1
    assert item.reranked is False
    assert item.degraded is False
    result = item.experiment.result_snapshot["results"][0]
    assert result == {
        "rank": 1,
        "document_id": "doc-a",
        "chunk_id": "chunk-a",
        "document_revision": 4,
        "content_revision": 8,
        "content_hash": "authority-hash",
        "score": 0.75,
        "branch": "hybrid",
        "dense_cosine": 0.61,
        "content": "authority content",
        "source": {
            "document_name": "Authority A",
            "source_type": "web",
            "source_uri": "https://example.test/manual",
        },
    }
    assert item.experiment.evidence_lineage["citations"] == [result]
    assert item.experiment.strategy_snapshot["strategy_revision"] == 1
    assert item.experiment.result_snapshot["result_revision"] == 1
    assert item.experiment.evidence_lineage["evidence_revision"] == 1
    assert base.settings.pipeline.model_dump() == original
    assert all(component.calls == 0 for component in llm_components)


def test_runner_persists_failed_and_no_hit_variants_in_one_run(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    base, _ = _base_pipeline(_Milvus([[], RuntimeError("token=raw-secret")]))
    runner = RetrievalExperimentRunner(RetrievalExperimentRepository(engine), base)

    outcome = runner.run(
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        query="No answer",
        acl=None,
        variants=[_variant("no-hit"), _variant("failure")],
        audit=_audit("failed-variant"),
    )

    assert [item.status for item in outcome.items] == ["completed", "failed"]
    assert [item.result_count for item in outcome.items] == [0, 0]
    assert all(item.experiment.run_id == outcome.run_id for item in outcome.items)
    for item in outcome.items:
        assert item.experiment.result_snapshot["results"] == []
        assert item.experiment.evidence_lineage["citations"] == []
    failed_snapshot = outcome.items[1].experiment.result_snapshot
    failed_text = str(failed_snapshot).casefold()
    assert "raw-secret" not in failed_text
    assert failed_snapshot.get("traces", []) == []
    assert failed_snapshot["failure_code"] == "retrieval_execution_failed"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(RetrievalExperiment)) == 2
        assert session.scalar(select(func.count()).select_from(KnowledgeAuditEvent)) == 2


def test_runner_generation_change_after_retrieval_is_atomic_conflict(tmp_path: Path) -> None:
    engine = _engine(tmp_path)

    def bump_generation(_call: int) -> None:
        with Session(engine) as session:
            session.execute(
                update(Dataset)
                .where(Dataset.id == "dataset-a", Dataset.tenant_id == "tenant-a")
                .values(serving_generation=13)
            )
            session.commit()

    base, _ = _base_pipeline(_Milvus([[]], on_search=bump_generation))
    runner = RetrievalExperimentRunner(RetrievalExperimentRepository(engine), base)

    with pytest.raises(RetrievalExperimentConflict, match="generation"):
        runner.run(
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            query="race",
            acl=None,
            variants=[_variant("first"), _variant("second")],
            audit=_audit("generation-race"),
        )

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(RetrievalExperiment)) == 0
        assert session.scalar(select(func.count()).select_from(KnowledgeAuditEvent)) == 0


def test_runner_lifecycle_change_after_retrieval_is_atomic_conflict(tmp_path: Path) -> None:
    engine = _engine(tmp_path)

    def archive_dataset(_call: int) -> None:
        with Session(engine) as session:
            session.execute(
                update(Dataset)
                .where(Dataset.id == "dataset-a", Dataset.tenant_id == "tenant-a")
                .values(status="archived")
            )
            session.commit()

    base, _ = _base_pipeline(_Milvus([[]], on_search=archive_dataset))
    runner = RetrievalExperimentRunner(RetrievalExperimentRepository(engine), base)

    with pytest.raises(RetrievalExperimentConflict, match="lifecycle"):
        runner.run(
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            query="lifecycle race",
            acl=None,
            variants=[_variant("first"), _variant("second")],
            audit=_audit("lifecycle-race"),
        )

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(RetrievalExperiment)) == 0
        assert session.scalar(select(func.count()).select_from(KnowledgeAuditEvent)) == 0


def test_projection_sanitizer_blocks_review_leak_probes() -> None:
    from core.retrieval_experiment_runner import _sanitize_projection_text

    projected = _sanitize_projection_text(
        '{"api_key": "sk live multi word", "token": "tok en two", '
        '"Authorization": "Basic dXNlcjpwYXNz"} '
        "{'secret': 'quoted multi word secret'} "
        "Bearer abc.def "
        "https://user:pass@example.test/a?api_key=query-secret#token=fragment-secret",
        20_000,
    )

    assert projected == (
        '{"api_key": "[redacted]", "token": "[redacted]", '
        '"Authorization": "[redacted]"} '
        "{'secret': '[redacted]'} Bearer [redacted] https://example.test/a"
    )
    for leak in (
        "sk live multi word",
        "tok en two",
        "dXNlcjpwYXNz",
        "quoted multi word secret",
        "abc.def",
        "user:pass",
        "query-secret",
        "fragment-secret",
    ):
        assert leak not in projected


def test_trace_projection_accepts_only_allowlisted_timing_and_degraded_facts() -> None:
    from core.retrieval_experiment_runner import _sanitize_traces

    assert _sanitize_traces(
        [
            "embed:1.23ms",
            "route:0.00ms",
            "rerank 失败，使用原始顺序",
            'headers={"Authorization": "Bearer raw.header.secret"}',
            "graph 检索失败，降级 hybrid: api_key='multi word secret'",
        ]
    ) == [
        "embed:1.23ms",
        "route:0.00ms",
        "degraded:rerank:rerank_failed",
        "degraded:graph:retrieval_failed",
    ]


class _GraphLlmProbe:
    def __init__(self) -> None:
        self.calls = 0

    def chat_json(self, **_kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        raise AssertionError("graph LLM must not run")


class _GraphRetrieverProbe:
    def __init__(self, llm: _GraphLlmProbe) -> None:
        self.llm = llm
        self.instances: list[Any] = []

    def retrieve(self, _query: str, *, tenant_id: str) -> Any:
        assert tenant_id == "tenant-a"
        self.instances.append(self)
        if self.llm is not None:
            self.llm.chat_json()
        return SimpleNamespace(passage_ids=[], degraded=True)


class _BreakerProbe:
    name = "base-reranker"

    def __init__(self) -> None:
        self.allow_calls = 0
        self.successes = 0
        self.failures = 0

    def allow(self) -> bool:
        self.allow_calls += 1
        return True

    def record_success(self) -> None:
        self.successes += 1

    def record_failure(self) -> None:
        self.failures += 1


class _FlakyReranker:
    def __init__(self) -> None:
        self.calls = 0

    def rerank(self, _query: str, chunks: list[Chunk]) -> list[float]:
        from core.reranker import RerankError

        self.calls += 1
        if self.calls == 1:
            raise RerankError("Authorization: Bearer raw.variant.secret")
        return [0.95 for _ in chunks]


def test_runner_forces_acl_filter_when_base_disables_it(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    milvus = _Milvus(
        [[
            _retrieved(
                "chunk-a", "doc-a", tenant_id="tenant-a", dataset_id="dataset-a"
            ),
            _retrieved(
                "chunk-finance",
                "doc-a",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                content_revision=1,
                text_hash="finance-hash",
                acl="finance",
            ),
        ]]
    )
    base, _ = _base_pipeline(milvus)
    assert base.settings.pipeline.acl_filter_on is False

    outcome = RetrievalExperimentRunner(
        RetrievalExperimentRepository(engine), base
    ).run(
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        query="ACL probe",
        acl=["legal"],
        variants=[_variant("acl")],
        audit=_audit("acl-filter"),
    )

    assert outcome.items[0].result_count == 1
    assert outcome.items[0].experiment.result_snapshot["results"][0]["chunk_id"] == "chunk-a"
    assert milvus.filter_calls[-1]["acl"] == ["legal"]
    assert milvus.filter_calls[-1]["acl_filter_on"] is True
    assert base.settings.pipeline.acl_filter_on is False


def test_runner_auto_route_and_graph_clone_never_call_llms(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    graph_llm = _GraphLlmProbe()
    graph_retriever = _GraphRetrieverProbe(graph_llm)
    base, llm_components = _base_pipeline(
        _Milvus([[]]), graph_retriever=graph_retriever
    )
    original_pipeline = base.settings.pipeline.model_dump()
    assert base.settings.graph.use_llm_rerank is True

    outcome = RetrievalExperimentRunner(
        RetrievalExperimentRepository(engine), base
    ).run(
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        query="auto graph",
        acl=None,
        variants=[_variant("auto-graph", route_target="auto", graph_retrieval_on=True)],
        audit=_audit("auto-graph"),
    )

    item = outcome.items[0]
    assert item.status == "completed"
    assert item.route == "vector_graph_rag"
    assert item.degraded is True
    assert graph_llm.calls == 0
    assert len(graph_retriever.instances) == 1
    assert graph_retriever.instances[0] is not graph_retriever
    assert graph_retriever.instances[0].llm is None
    assert graph_retriever.llm is graph_llm
    assert base.settings.graph.use_llm_rerank is True
    assert base.settings.pipeline.model_dump() == original_pipeline
    assert all(component.calls == 0 for component in llm_components)


def test_runner_does_not_share_reranker_breaker_between_variants(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    breaker = _BreakerProbe()
    reranker = _FlakyReranker()
    hit = _retrieved("chunk-a", "doc-a", tenant_id="tenant-a", dataset_id="dataset-a")
    base, _ = _base_pipeline(
        _Milvus([[hit], [hit]]), reranker=reranker, reranker_cb=breaker
    )

    outcome = RetrievalExperimentRunner(
        RetrievalExperimentRepository(engine), base
    ).run(
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        query="breaker isolation",
        acl=None,
        variants=[
            _variant("first", rerank_on=True),
            _variant("second", rerank_on=True),
        ],
        audit=_audit("breaker-isolation"),
    )

    assert [item.status for item in outcome.items] == ["completed", "completed"]
    assert [item.reranked for item in outcome.items] == [False, True]
    assert (breaker.allow_calls, breaker.successes, breaker.failures) == (0, 0, 0)
    assert base.reranker_cb is breaker


def test_runner_snapshots_exact_historical_flat_and_parent_revisions(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    base, _ = _base_pipeline(
        _Milvus(
            [[
                _retrieved(
                    "chunk-a",
                    "doc-a",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    document_revision=4,
                    content_revision=7,
                    text_hash="flat-historical-hash",
                ),
                _retrieved(
                    "chunk-parent",
                    "doc-parent",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    document_revision=5,
                    content_revision=2,
                    text_hash="parent-historical-hash",
                ),
            ]]
        )
    )

    outcome = RetrievalExperimentRunner(
        RetrievalExperimentRepository(engine), base
    ).run(
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        query="historical",
        acl=None,
        variants=[_variant("historical")],
        audit=_audit("historical"),
    )

    results = outcome.items[0].experiment.result_snapshot["results"]
    assert [(item["chunk_id"], item["chunk_revision_id"]) for item in results] == [
        ("chunk-a", "revision-flat-7"),
        ("chunk-parent", "revision-parent-2"),
    ]
    assert [item["document_revision"] for item in results] == [None, None]
    assert [item["content_revision"] for item in results] == [7, 2]
    assert [item["content_hash"] for item in results] == [
        "flat-historical-hash",
        "parent-historical-hash",
    ]
    assert [item["content"] for item in results] == [
        "flat historical",
        "parent historical",
    ]


def test_runner_discards_unknown_stale_flat_and_parent_chunks(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    base, _ = _base_pipeline(
        _Milvus(
            [[
                _retrieved(
                    "chunk-a",
                    "doc-a",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    content_revision=6,
                    text_hash="unknown-flat-hash",
                ),
                _retrieved(
                    "chunk-parent",
                    "doc-parent",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    document_revision=5,
                    content_revision=1,
                    text_hash="unknown-parent-hash",
                ),
            ]]
        )
    )

    outcome = RetrievalExperimentRunner(
        RetrievalExperimentRepository(engine), base
    ).run(
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        query="unknown stale",
        acl=None,
        variants=[_variant("unknown-stale")],
        audit=_audit("unknown-stale"),
    )

    item = outcome.items[0]
    assert item.status == "completed"
    assert item.result_count == 0
    assert item.experiment.result_snapshot["results"] == []
    assert item.experiment.evidence_lineage["citations"] == []


def test_runner_sanitizes_authoritative_content_and_source_projection(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with Session(engine) as session:
        document = session.get(Document, "doc-a")
        head = session.get(ChunkHead, "chunk-a")
        assert document is not None and head is not None
        document.name = '{"token": "name multi word secret"}'
        document.source_type = "Authorization: Basic c291cmNlOnNlY3JldA=="
        document.source_uri = (
            "https://user:pass@example.test/manual?api_key=query-secret"
            "#token=fragment-secret"
        )
        head.content = (
            "{'api_key': 'content multi word secret'} "
            "Bearer raw.content.header"
        )
        session.commit()

    base, _ = _base_pipeline(
        _Milvus(
            [[
                _retrieved(
                    "chunk-a",
                    "doc-a",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                )
            ]]
        )
    )
    outcome = RetrievalExperimentRunner(
        RetrievalExperimentRepository(engine), base
    ).run(
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        query="projection leaks",
        acl=None,
        variants=[_variant("projection")],
        audit=_audit("projection-leaks"),
    )

    snapshot = outcome.items[0].experiment.result_snapshot["results"][0]
    serialized = str(snapshot)
    for leak in (
        "name multi word secret",
        "c291cmNlOnNlY3JldA==",
        "query-secret",
        "fragment-secret",
        "content multi word secret",
        "raw.content.header",
        "user:pass",
    ):
        assert leak not in serialized
    assert snapshot["source"]["source_uri"] == "https://example.test/manual"
