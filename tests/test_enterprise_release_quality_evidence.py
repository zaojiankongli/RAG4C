from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from core.enterprise_knowledge_base_releases import (
    ReleaseManifestConflict,
    capture_release_candidate,
)
from core.enterprise_release_quality import collect_release_experiment_evidence
from models.orm import RetrievalExperiment, RetrievalJudgment
from test_enterprise_knowledge_base_release_snapshot import _release_engine


def capture(engine) -> str:
    result = capture_release_candidate(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        expected_profile_revision=4,
        expected_ownership_revision=5,
        expected_workspace_revision=2,
        expected_mutation_generation=9,
        expected_serving_generation=3,
        reason="quality candidate",
        request_id="quality-capture",
        request_ip="127.0.0.1",
        idempotency_key="quality-capture-key",
    )
    return result.body["release"]["id"]


def seed_experiment(
    engine,
    *,
    experiment_id: str = "experiment-quality-a",
    generation: int = 3,
    document_revision: int | None = 1,
) -> None:
    with Session(engine) as session:
        experiment = RetrievalExperiment(
            id=experiment_id,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            query="Where is the handbook?",
            query_hash="a" * 64,
            strategy_snapshot={
                "strategy_revision": 1,
                "dataset_serving_generation": generation,
                "variant_name": "quality-baseline",
                "top_k": 2,
            },
            result_snapshot={
                "result_revision": 1,
                "dataset_serving_generation": generation,
                "route": "hybrid",
                "reranked": True,
                "degraded": False,
                "results": [
                    {
                        "rank": 1,
                        "document_id": "document-a",
                        "chunk_id": "chunk-quality-1",
                        "document_revision": document_revision,
                        "content_revision": 1,
                        "content_hash": "c" * 64,
                        "content": "private result body must not enter certification evidence",
                    },
                    {
                        "rank": 2,
                        "document_id": "document-a",
                        "chunk_id": "chunk-quality-2",
                        "document_revision": document_revision,
                        "content_revision": 2,
                        "content_hash": "d" * 64,
                        "content": "another private result body",
                    },
                ],
            },
            evidence_lineage={
                "evidence_revision": 1,
                "dataset_serving_generation": generation,
                "citations": [
                    {
                        "rank": 1,
                        "document_id": "document-a",
                        "chunk_id": "chunk-quality-1",
                        "document_revision": document_revision,
                    },
                    {
                        "rank": 2,
                        "document_id": "document-a",
                        "chunk_id": "chunk-quality-2",
                        "document_revision": document_revision,
                    },
                ],
            },
            latency_ms=12,
            status="completed",
            created_by="runner-a",
            run_id="run-quality-a",
        )
        session.add(experiment)
        session.flush()
        session.add_all(
            [
                RetrievalJudgment(
                    id="judgment-quality-1-a",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    experiment_id=experiment_id,
                    result_rank=1,
                    document_id="document-a",
                    chunk_id=None,
                    relevance_label="relevant",
                    score=3,
                    note="private reviewer note",
                    revision=1,
                    created_by="judge-a",
                ),
                RetrievalJudgment(
                    id="judgment-quality-1-b",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    experiment_id=experiment_id,
                    result_rank=1,
                    document_id="document-a",
                    chunk_id=None,
                    relevance_label="relevant",
                    score=2,
                    note="second private note",
                    revision=2,
                    created_by="judge-b",
                ),
                RetrievalJudgment(
                    id="judgment-quality-2-a",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    experiment_id=experiment_id,
                    result_rank=2,
                    document_id="document-a",
                    chunk_id=None,
                    relevance_label="partial",
                    score=1,
                    note="third private note",
                    revision=1,
                    created_by="judge-a",
                ),
            ]
        )
        session.commit()


def test_collect_release_experiment_evidence_binds_release_and_excludes_bodies(
    tmp_path: Path,
) -> None:
    engine, _ = _release_engine(tmp_path)
    release_id = capture(engine)
    seed_experiment(engine)
    with Session(engine) as session:
        evidence = collect_release_experiment_evidence(
            session,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            release_id=release_id,
            experiment_ids=["experiment-quality-a"],
        )
    assert len(evidence) == 1
    item = evidence[0]
    assert item["experiment_id"] == "experiment-quality-a"
    assert item["dataset_serving_generation"] == 3
    assert item["result_ranks"] == [1, 2]
    assert len(item["judgments"]) == 3
    rendered = repr(evidence)
    assert "Where is the handbook?" not in rendered
    assert "private result body" not in rendered
    assert "private reviewer note" not in rendered
    assert "judgment_digest" in rendered
    engine.dispose()


@pytest.mark.parametrize(
    ("generation", "document_revision", "message"),
    [
        (4, 1, "serving generation"),
        (3, 99, "document revision"),
        (3, None, "document revision"),
    ],
)
def test_collect_release_experiment_evidence_rejects_stale_or_historical_lineage(
    tmp_path: Path,
    generation: int,
    document_revision: int | None,
    message: str,
) -> None:
    engine, _ = _release_engine(tmp_path)
    release_id = capture(engine)
    seed_experiment(
        engine,
        generation=generation,
        document_revision=document_revision,
    )
    with Session(engine) as session, pytest.raises(ReleaseManifestConflict, match=message):
        collect_release_experiment_evidence(
            session,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            release_id=release_id,
            experiment_ids=["experiment-quality-a"],
        )
    engine.dispose()


def test_collect_release_experiment_evidence_rejects_missing_or_duplicate_scope(
    tmp_path: Path,
) -> None:
    engine, _ = _release_engine(tmp_path)
    release_id = capture(engine)
    seed_experiment(engine)
    with Session(engine) as session:
        with pytest.raises(Exception, match="unique"):
            collect_release_experiment_evidence(
                session,
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                release_id=release_id,
                experiment_ids=["experiment-quality-a", "experiment-quality-a"],
            )
        with pytest.raises(ReleaseManifestConflict, match="scope"):
            collect_release_experiment_evidence(
                session,
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                release_id=release_id,
                experiment_ids=["experiment-missing"],
            )
    engine.dispose()
