from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, delete, func, select, text, update
from sqlalchemy.exc import DatabaseError, IntegrityError
from sqlalchemy.orm import Session

from core.knowledge_governance import AuditContext, sanitize_audit_snapshot
from core.secret_fields import is_sensitive_field, normalize_field_name
from core.retrieval_experiments import (
    RetrievalExperimentConflict,
    RetrievalExperimentNotFound,
    RetrievalExperimentRepository,
)
from models.orm import (
    ChunkHead,
    ChunkRevision,
    Dataset,
    Document,
    KnowledgeAuditEvent,
    RetrievalExperiment,
    RetrievalJudgment,
    Tenant,
)


def _audit(actor: str, request: str) -> AuditContext:
    return AuditContext(actor_id=actor, request_id=request, request_ip="127.0.0.1")


def _snapshots() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    return (
        {
            "strategy_revision": 7,
            "dataset_serving_generation": 12,
            "mode": "hybrid",
            "reranker": {
                "name": "bge",
                "api_token": {"credential_ref": "vault://retrieval/reranker"},
                "tokenizer": "BAAI/bge-m3",
                "max_tokens": 8192,
                "token_count": 73,
                "endpoint": "https://models.example.test/v1#rerank",
            },
        },
        {
            "result_revision": 3,
            "dataset_serving_generation": 12,
            "results": [
                {
                    "rank": 1,
                    "document_id": "doc-1",
                    "chunk_id": "chunk-1",
                    "document_revision": 4,
                    "content_revision": 8,
                    "content_hash": "hash",
                },
                {
                    "rank": 2,
                    "document_id": "doc-2",
                    "chunk_id": None,
                    "document_revision": 2,
                    "content_revision": 0,
                },
            ],
        },
        {
            "evidence_revision": 2,
            "dataset_serving_generation": 12,
            "citations": [{
                "rank": 1, "document_id": "doc-1", "chunk_id": "chunk-1",
                "document_revision": 4, "content_revision": 8,
                "content_hash": "hash",
                "uri": "https://u:p@example.test/a?token=secret",
            }, {
                "rank": 2, "document_id": "doc-2", "chunk_id": None,
                "document_revision": 2, "content_revision": 0,
            }],
        },
    )


def _repository(tmp_path: Path) -> tuple[object, RetrievalExperimentRepository]:
    from core.catalog_schema import upgrade_catalog

    url = f"sqlite:///{(tmp_path / 'retrieval-experiments.db').as_posix()}"
    upgrade_catalog(url)
    engine = create_engine(url)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id="tenant-1", name="Tenant 1"),
                Tenant(id="tenant-2", name="Tenant 2"),
                Dataset(id="dataset-1", tenant_id="tenant-1", name="KB 1", serving_generation=12),
                Dataset(id="dataset-2", tenant_id="tenant-1", name="KB 2"),
                Dataset(id="dataset-3", tenant_id="tenant-2", name="KB 3"),
                Document(
                    id="doc-1", tenant_id="tenant-1", dataset_id="dataset-1",
                    name="One", content_revision=4,
                ),
                Document(
                    id="doc-2", tenant_id="tenant-1", dataset_id="dataset-1",
                    name="Two", content_revision=2,
                ),
                Document(
                    id="doc-other",
                    tenant_id="tenant-2",
                    dataset_id="dataset-3",
                    name="Other",
                ),
            ]
        )
        session.flush()
        session.add(
            ChunkHead(
                id="chunk-1",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                document_id="doc-1",
                chunk_index=0,
                document_revision=4,
                content_revision=8,
                source_content="source",
                content="content",
                content_hash="hash",
            )
        )
        session.flush()
        session.add(
            ChunkRevision(
                id="chunk-revision-7", chunk_id="chunk-1", tenant_id="tenant-1",
                dataset_id="dataset-1", document_id="doc-1", revision=7,
                content="historical", content_hash="historical-hash",
            )
        )
        session.commit()
    return engine, RetrievalExperimentRepository(engine)


def _create(repository: RetrievalExperimentRepository, *, query: str = "What is RAG?", actor: str = "judge-a") -> RetrievalExperiment:
    strategy, results, lineage = _snapshots()
    return repository.create_experiment(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        query=query,
        strategy_snapshot=strategy,
        result_snapshot=results,
        evidence_lineage=lineage,
        latency_ms=37,
        status="completed",
        run_id="run-1",
        audit=_audit(actor, f"create-{actor}-{query}"),
    )


def test_create_experiment_preserves_lossless_strict_json_and_sanitizes_only_audit(tmp_path: Path) -> None:
    engine, repository = _repository(tmp_path)
    strategy, results, lineage = _snapshots()
    experiment = repository.create_experiment(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        query="  What\u3000is RAG?  ",
        strategy_snapshot=strategy,
        result_snapshot=results,
        evidence_lineage=lineage,
        latency_ms=37,
        status="completed",
        run_id="run-1",
        audit=_audit("author", "request-create"),
    )

    assert experiment.query == "What is RAG?"
    assert len(experiment.query_hash) == 64
    assert experiment.strategy_snapshot["reranker"] == {
        "name": "bge",
        "api_token": {"credential_ref": "vault://retrieval/reranker"},
        "tokenizer": "BAAI/bge-m3",
        "max_tokens": 8192,
        "token_count": 73,
        "endpoint": "https://models.example.test/v1#rerank",
    }
    assert experiment.evidence_lineage["citations"][0]["uri"] == (
        "https://u:p@example.test/a?token=secret"
    )
    strategy["mode"] = "changed"
    results["results"][0]["rank"] = 99
    assert experiment.strategy_snapshot["mode"] == "hybrid"
    assert experiment.result_snapshot["results"][0]["rank"] == 1

    with Session(engine) as session:
        event = session.scalar(
            select(KnowledgeAuditEvent).where(
                KnowledgeAuditEvent.action == "retrieval_experiment.create"
            )
        )
        assert event is not None
        assert event.resource_id == experiment.id
        assert event.after_snapshot["strategy_snapshot"]["reranker"]["api_token"] == "[REDACTED]"
        assert event.after_snapshot["strategy_snapshot"]["reranker"]["tokenizer"] == (
            "BAAI/bge-m3"
        )
        assert event.after_snapshot["strategy_snapshot"]["reranker"]["max_tokens"] == 8192
        assert event.after_snapshot["strategy_snapshot"]["reranker"]["token_count"] == 73

    with pytest.raises(ValueError, match="JSON tree"):
        repository.create_experiment(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            query="bad json",
            strategy_snapshot={"revision": "1", "generation": 1, "bad": {1, 2}},
            result_snapshot=results,
            evidence_lineage=lineage,
            latency_ms=1,
            status="failed",
            audit=_audit("author", "bad-json"),
        )
    engine.dispose()


def test_authoritative_snapshot_rejects_plain_secrets_cycles_and_nonfinite_values(
    tmp_path: Path,
) -> None:
    engine, repository = _repository(tmp_path)
    strategy, results, lineage = _snapshots()
    strategy["reranker"]["api_token"] = "plaintext-secret"
    with pytest.raises(ValueError, match="credential_ref"):
        repository.create_experiment(
            tenant_id="tenant-1", dataset_id="dataset-1", query="secret",
            strategy_snapshot=strategy, result_snapshot=results, evidence_lineage=lineage,
            latency_ms=1, status="failed", audit=_audit("author", "plain-secret")
        )

    strategy, results, lineage = _snapshots()
    strategy["cycle"] = strategy
    with pytest.raises(ValueError, match="cycle"):
        repository.create_experiment(
            tenant_id="tenant-1", dataset_id="dataset-1", query="cycle",
            strategy_snapshot=strategy, result_snapshot=results, evidence_lineage=lineage,
            latency_ms=1, status="failed", audit=_audit("author", "cycle")
        )

    strategy, results, lineage = _snapshots()
    strategy["temperature"] = float("inf")
    with pytest.raises(ValueError, match="non-finite"):
        repository.create_experiment(
            tenant_id="tenant-1", dataset_id="dataset-1", query="infinite",
            strategy_snapshot=strategy, result_snapshot=results, evidence_lineage=lineage,
            latency_ms=1, status="failed", audit=_audit("author", "infinite")
        )
    engine.dispose()


@pytest.mark.parametrize(
    ("snapshot_name", "mutation"),
    [
        ("strategy", lambda item: item.pop("strategy_revision")),
        ("strategy", lambda item: item.update({"strategy_revision": 0})),
        ("strategy", lambda item: item.update({"strategy_revision": True})),
        ("strategy", lambda item: item.update({"strategy_revision": "7"})),
        ("strategy", lambda item: (item.pop("strategy_revision"), item.update({"revisionist": 1}))),
        ("result", lambda item: item.update({"dataset_serving_generation": -1})),
        ("result", lambda item: (item.pop("dataset_serving_generation"), item.update({"regeneration": 3}))),
        ("evidence", lambda item: item.pop("evidence_revision")),
        ("evidence", lambda item: item.update({"dataset_serving_generation": True})),
    ],
)
def test_snapshot_fences_require_exact_keys_and_types(
    tmp_path: Path, snapshot_name: str, mutation
) -> None:
    engine, repository = _repository(tmp_path)
    strategy, results, lineage = _snapshots()
    target = {"strategy": strategy, "result": results, "evidence": lineage}[snapshot_name]
    mutation(target)
    with pytest.raises(ValueError, match="revision|dataset_serving_generation"):
        repository.create_experiment(
            tenant_id="tenant-1", dataset_id="dataset-1", query=f"bad-{snapshot_name}",
            strategy_snapshot=strategy, result_snapshot=results, evidence_lineage=lineage,
            latency_ms=1, status="failed", audit=_audit("author", f"bad-{snapshot_name}")
        )
    engine.dispose()


def test_secret_alias_policy_rejects_leaks_without_false_positives(tmp_path: Path) -> None:
    engine, repository = _repository(tmp_path)
    for alias in ("credential_ref", "apiToken", "auth_token", "github_token",
                  "database_password", "service_client_secret", "service_secret",
                  "jwt_token", "db_passwd", "passphrase", "aws_secret_access_key",
                  "session_token", "id_token", "api_secret", "secret_key"):
        strategy, results, lineage = _snapshots()
        strategy[alias] = "plaintext"
        with pytest.raises(ValueError, match="credential_ref"):
            repository.create_experiment(
                tenant_id="tenant-1", dataset_id="dataset-1", query=alias,
                strategy_snapshot=strategy, result_snapshot=results, evidence_lineage=lineage,
                latency_ms=1, status="failed", audit=_audit("author", alias)
            )
    strategy, results, lineage = _snapshots()
    strategy["credential_ref"] = "vault://retrieval/direct"
    direct = repository.create_experiment(
        tenant_id="tenant-1", dataset_id="dataset-1", query="direct credential ref",
        strategy_snapshot=strategy, result_snapshot=results, evidence_lineage=lineage,
        latency_ms=1, status="completed", audit=_audit("author", "direct-ref")
    )
    assert direct.strategy_snapshot["credential_ref"] == "vault://retrieval/direct"
    strategy, results, lineage = _snapshots()
    strategy.update({"token": 128, "token_count": 4, "tokenizer": "bge",
                     "max_tokens": 512, "cookie_domain": "example.test"})
    for alias in ("auth_token", "github_token", "database_password",
                  "service_client_secret"):
        strategy[alias] = {"credential_ref": f"vault://retrieval/{alias}"}
    stored = repository.create_experiment(
        tenant_id="tenant-1", dataset_id="dataset-1", query="safe aliases",
        strategy_snapshot=strategy, result_snapshot=results, evidence_lineage=lineage,
        latency_ms=1, status="completed", audit=_audit("author", "safe-aliases")
    )
    assert stored.strategy_snapshot["token"] == 128
    assert stored.strategy_snapshot["auth_token"] == {
        "credential_ref": "vault://retrieval/auth_token"
    }
    with Session(engine) as session:
        event = session.scalar(select(KnowledgeAuditEvent).where(
            KnowledgeAuditEvent.resource_id == stored.id
        ))
        assert event is not None
        for alias in ("auth_token", "github_token", "database_password",
                      "service_client_secret"):
            assert event.after_snapshot["strategy_snapshot"][alias] == "[REDACTED]"
        assert event.after_snapshot["strategy_snapshot"]["tokenizer"] == "bge"
        assert event.after_snapshot["strategy_snapshot"]["token_count"] == 4
        assert event.after_snapshot["strategy_snapshot"]["max_tokens"] == 512
    strategy, results, lineage = _snapshots()
    strategy["apiToken"] = {"credential_ref": "https://user:pass@example.test/secret"}
    with pytest.raises(ValueError, match="safe secret or vault URI"):
        repository.create_experiment(
            tenant_id="tenant-1", dataset_id="dataset-1", query="unsafe ref",
            strategy_snapshot=strategy, result_snapshot=results, evidence_lineage=lineage,
            latency_ms=1, status="failed", audit=_audit("author", "unsafe-ref")
        )
    engine.dispose()


def test_central_secret_classifier_aliases_and_benign_fields() -> None:
    assert normalize_field_name("service-client_secret") == "serviceclientsecret"
    for key in (
        "credential_ref", "service_secret", "jwt_token", "db_passwd",
        "passphrase", "apiToken", "auth_token", "aws_secret_access_key",
        "session_token", "id_token", "api_secret", "secret_key",
        "database_login_password", "service_signing_key", "oauth_access_token",
    ):
        assert is_sensitive_field(key)
    for key in ("token", "tokenizer", "token_count", "max_tokens", "cookie_domain"):
        assert not is_sensitive_field(key)
    snapshot = sanitize_audit_snapshot({
        "credential_ref": "secret://x", "service_secret": "x", "jwt_token": "x",
        "db_passwd": "x", "passphrase": "x", "aws_secret_access_key": "x",
        "session_token": "x", "id_token": "x", "api_secret": "x",
        "secret_key": "x", "token": 3, "token_count": 4,
        "tokenizer": "bge", "max_tokens": 5, "cookie_domain": "example.test",
    })
    for key in (
        "credential_ref", "service_secret", "jwt_token", "db_passwd",
        "passphrase", "aws_secret_access_key", "session_token", "id_token",
        "api_secret", "secret_key",
    ):
        assert snapshot[key] == "[REDACTED]"
    # The authority classifier treats exact ``token`` as a benign business field,
    # while audit persistence applies a stricter, context-specific redaction policy.
    assert snapshot["token"] == "[REDACTED]"
    assert snapshot["token_count"] == 4
    assert snapshot["tokenizer"] == "bge"
    assert snapshot["max_tokens"] == 5
    assert snapshot["cookie_domain"] == "example.test"


def test_current_chunk_evidence_requires_document_revision_and_content_hash(tmp_path: Path) -> None:
    engine, repository = _repository(tmp_path)
    strategy, results, lineage = _snapshots()
    results["results"][0]["content_hash"] = "hash"
    lineage["citations"][0]["content_hash"] = "hash"
    with Session(engine) as session:
        document = session.get(Document, "doc-1")
        assert document is not None
        document.content_revision = 5
        session.commit()
    with pytest.raises(RetrievalExperimentConflict, match="document.*chunk"):
        repository.create_experiment(
            tenant_id="tenant-1", dataset_id="dataset-1", query="stale document",
            strategy_snapshot=strategy, result_snapshot=results, evidence_lineage=lineage,
            latency_ms=1, status="failed", audit=_audit("author", "stale-doc")
        )
    with Session(engine) as session:
        document = session.get(Document, "doc-1")
        assert document is not None
        document.content_revision = 4
        session.commit()
    results["results"][0]["content_hash"] = "forged"
    lineage["citations"][0]["content_hash"] = "forged"
    with pytest.raises(RetrievalExperimentConflict, match="content hash"):
        repository.create_experiment(
            tenant_id="tenant-1", dataset_id="dataset-1", query="forged hash",
            strategy_snapshot=strategy, result_snapshot=results, evidence_lineage=lineage,
            latency_ms=1, status="failed", audit=_audit("author", "forged-hash")
        )
    engine.dispose()


def test_snapshot_generation_must_match_locked_dataset_authority(tmp_path: Path) -> None:
    engine, repository = _repository(tmp_path)
    strategy, results, lineage = _snapshots()
    for snapshot in (strategy, results, lineage):
        snapshot["dataset_serving_generation"] = 11
    with pytest.raises(RetrievalExperimentConflict, match="serving generation"):
        repository.create_experiment(
            tenant_id="tenant-1", dataset_id="dataset-1", query="stale generation",
            strategy_snapshot=strategy, result_snapshot=results, evidence_lineage=lineage,
            latency_ms=1, status="failed", audit=_audit("author", "stale-generation")
        )
    engine.dispose()


def test_evidence_lineage_must_match_ranked_result_authority(tmp_path: Path) -> None:
    engine, repository = _repository(tmp_path)
    for field, forged in (("rank", 9), ("document_id", "doc-other"),
                          ("chunk_id", "missing"), ("content_revision", 7)):
        strategy, results, lineage = _snapshots()
        lineage["citations"][0][field] = forged
        with pytest.raises((ValueError, RetrievalExperimentConflict), match="evidence"):
            repository.create_experiment(
                tenant_id="tenant-1", dataset_id="dataset-1", query=f"forged-{field}",
                strategy_snapshot=strategy, result_snapshot=results, evidence_lineage=lineage,
                latency_ms=1, status="failed", audit=_audit("author", f"forged-{field}")
            )
    engine.dispose()


def test_experiment_result_snapshot_references_must_match_catalog_scope(tmp_path: Path) -> None:
    engine, repository = _repository(tmp_path)
    strategy, results, lineage = _snapshots()
    results["results"][0]["document_id"] = "doc-other"
    lineage["citations"][0]["document_id"] = "doc-other"
    with pytest.raises(RetrievalExperimentNotFound, match="result document"):
        repository.create_experiment(
            tenant_id="tenant-1", dataset_id="dataset-1", query="cross scope",
            strategy_snapshot=strategy, result_snapshot=results, evidence_lineage=lineage,
            latency_ms=1, status="completed", audit=_audit("author", "cross-scope")
        )

    _, invalid_results, _ = _snapshots()
    invalid_results["results"][0]["document_id"] = 123
    with pytest.raises(ValueError, match="document_id"):
        repository.create_experiment(
            tenant_id="tenant-1", dataset_id="dataset-1", query="invalid identity",
            strategy_snapshot=strategy, result_snapshot=invalid_results,
            evidence_lineage=lineage, latency_ms=1, status="completed",
            audit=_audit("author", "invalid-identity")
        )

    _, mismatched_results, _ = _snapshots()
    mismatched_results["results"][0]["document_id"] = "doc-2"
    lineage["citations"][0]["document_id"] = "doc-2"
    with pytest.raises(RetrievalExperimentConflict, match="chunk.*document"):
        repository.create_experiment(
            tenant_id="tenant-1", dataset_id="dataset-1", query="chunk mismatch",
            strategy_snapshot=strategy, result_snapshot=mismatched_results,
            evidence_lineage=lineage, latency_ms=1, status="completed",
            audit=_audit("author", "chunk-mismatch")
        )
    engine.dispose()


def test_historical_chunk_evidence_requires_actual_revision_snapshot(tmp_path: Path) -> None:
    engine, repository = _repository(tmp_path)
    strategy, results, lineage = _snapshots()
    results["results"][0].update({
        "document_revision": None, "content_revision": 7,
        "chunk_revision_id": "chunk-revision-7", "content_hash": "historical-hash",
    })
    lineage["citations"][0].update(results["results"][0])
    historical = repository.create_experiment(
        tenant_id="tenant-1", dataset_id="dataset-1", query="historical",
        strategy_snapshot=strategy, result_snapshot=results, evidence_lineage=lineage,
        latency_ms=1, status="completed", audit=_audit("author", "historical")
    )
    assert historical.result_snapshot["results"][0]["chunk_revision_id"] == "chunk-revision-7"

    strategy, results, lineage = _snapshots()
    results["results"][0]["content_revision"] = 7
    lineage["citations"][0]["content_revision"] = 7
    with pytest.raises(RetrievalExperimentConflict, match="historical.*snapshot"):
        repository.create_experiment(
            tenant_id="tenant-1", dataset_id="dataset-1", query="unprovable historical",
            strategy_snapshot=strategy, result_snapshot=results, evidence_lineage=lineage,
            latency_ms=1, status="failed", audit=_audit("author", "unprovable")
        )
    engine.dispose()


def test_experiment_requires_revision_generation_snapshots_and_is_immutable(tmp_path: Path) -> None:
    engine, repository = _repository(tmp_path)
    strategy, results, lineage = _snapshots()
    with pytest.raises(ValueError, match="strategy_revision"):
        repository.create_experiment(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            query="missing fence",
            strategy_snapshot={"mode": "vector"},
            result_snapshot=results,
            evidence_lineage=lineage,
            latency_ms=1,
            status="completed",
            audit=_audit("author", "missing-fence"),
        )

    experiment = _create(repository)
    with Session(engine) as session:
        stored = session.get(RetrievalExperiment, experiment.sequence)
        assert stored is not None
        stored.query = "mutated"
        with pytest.raises((TypeError, RuntimeError), match="immutable"):
            session.commit()
        session.rollback()
    engine.dispose()


def test_scope_get_list_query_hash_time_and_sequence_id_cursor(tmp_path: Path) -> None:
    engine, repository = _repository(tmp_path)
    first = _create(repository, query="First")
    second = _create(repository, query="Second")
    third = _create(repository, query="Third")

    assert repository.get_experiment("tenant-1", "dataset-1", second.id).id == second.id
    with pytest.raises(RetrievalExperimentNotFound):
        repository.get_experiment("tenant-2", "dataset-3", second.id)

    page = repository.list_experiments("tenant-1", "dataset-1", limit=2)
    assert [item.id for item in page] == [third.id, second.id]
    next_page = repository.list_experiments(
        "tenant-1",
        "dataset-1",
        before_sequence=page[-1].sequence,
        before_id=page[-1].id,
        limit=2,
    )
    assert [item.id for item in next_page] == [first.id]
    assert [item.id for item in repository.list_experiments(
        "tenant-1", "dataset-1", query_hash=second.query_hash
    )] == [second.id]
    assert [item.id for item in repository.list_experiments(
        "tenant-1", "dataset-1", run_id="run-1", created_by="judge-a"
    )] == [third.id, second.id, first.id]
    assert [item.id for item in repository.list_experiments(
        "tenant-1",
        "dataset-1",
        created_from=second.created_at - timedelta(microseconds=1),
        created_to=second.created_at + timedelta(microseconds=1),
    )] == [second.id]
    with pytest.raises(ValueError, match="cursor"):
        repository.list_experiments(
            "tenant-1", "dataset-1", before_sequence=second.sequence, before_id=first.id
        )
    engine.dispose()


def test_judgment_validates_rank_scope_relation_and_supports_multi_judge_cas(tmp_path: Path) -> None:
    engine, repository = _repository(tmp_path)
    experiment = _create(repository)
    first = repository.add_judgment(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        experiment_id=experiment.id,
        result_rank=1,
        document_id="doc-1",
        chunk_id="chunk-1",
        relevance_label="relevant",
        score=3,
        note="grounded",
        audit=_audit("judge-a", "judge-a-create"),
    )
    second = repository.add_judgment(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        experiment_id=experiment.id,
        result_rank=1,
        document_id="doc-1",
        chunk_id="chunk-1",
        relevance_label="partial",
        score=2,
        note="mixed",
        audit=_audit("judge-b", "judge-b-create"),
    )
    assert first.revision == second.revision == 1
    inherited = repository.add_judgment(
        tenant_id="tenant-1", dataset_id="dataset-1", experiment_id=experiment.id,
        result_rank=2, relevance_label="partial", score=1,
        audit=_audit("judge-c", "judge-c-inherit")
    )
    assert inherited.document_id == "doc-2"
    assert inherited.chunk_id is None

    updated = repository.update_judgment(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        judgment_id=first.id,
        expected_revision=1,
        relevance_label="partial",
        score=2,
        note="re-reviewed",
        audit=_audit("judge-a", "judge-a-update"),
    )
    assert updated.revision == 2
    with pytest.raises(RetrievalExperimentConflict, match="revision"):
        repository.update_judgment(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            judgment_id=first.id,
            expected_revision=1,
            relevance_label="irrelevant",
            score=0,
            note="stale",
            audit=_audit("judge-a", "judge-a-stale"),
        )
    with pytest.raises(RetrievalExperimentConflict, match="judge"):
        repository.update_judgment(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            judgment_id=second.id,
            expected_revision=1,
            relevance_label="relevant",
            score=3,
            note="impersonation",
            audit=_audit("judge-a", "wrong-judge"),
        )

    with pytest.raises(ValueError, match="rank"):
        repository.add_judgment(
            tenant_id="tenant-1", dataset_id="dataset-1", experiment_id=experiment.id,
            result_rank=9, relevance_label="irrelevant", audit=_audit("judge-c", "bad-rank")
        )
    with pytest.raises(ValueError, match="result snapshot"):
        repository.add_judgment(
            tenant_id="tenant-1", dataset_id="dataset-1", experiment_id=experiment.id,
            result_rank=1, document_id="doc-2", relevance_label="irrelevant",
            audit=_audit("judge-c", "bad-document")
        )
    with pytest.raises(RetrievalExperimentNotFound):
        repository.add_judgment(
            tenant_id="tenant-2", dataset_id="dataset-3", experiment_id=experiment.id,
            result_rank=1, relevance_label="relevant", audit=_audit("judge-c", "cross-tenant")
        )
    engine.dispose()


def test_judgment_uses_one_canonical_actor_for_owner_cas_and_audit(tmp_path: Path) -> None:
    engine, repository = _repository(tmp_path)
    experiment = _create(repository)
    raw_actor = "  ｊudge-canonical  "
    judgment = repository.add_judgment(
        tenant_id="tenant-1", dataset_id="dataset-1", experiment_id=experiment.id,
        result_rank=1, relevance_label="relevant", audit=_audit(raw_actor, "canonical-create")
    )
    assert judgment.created_by == "judge-canonical"
    updated = repository.update_judgment(
        tenant_id="tenant-1", dataset_id="dataset-1", judgment_id=judgment.id,
        expected_revision=1, relevance_label="partial",
        audit=_audit(raw_actor, "canonical-update")
    )
    assert updated.revision == 2
    with Session(engine) as session:
        actors = list(session.scalars(select(KnowledgeAuditEvent.actor_id).where(
            KnowledgeAuditEvent.resource_id == judgment.id
        ).order_by(KnowledgeAuditEvent.sequence)))
    assert actors == ["judge-canonical", "judge-canonical"]
    engine.dispose()


def test_database_triggers_block_bulk_and_raw_experiment_update_delete(tmp_path: Path) -> None:
    engine, repository = _repository(tmp_path)
    experiment = _create(repository)
    with Session(engine) as session:
        with pytest.raises(DatabaseError, match="immutable"):
            session.execute(update(RetrievalExperiment).values(status="failed"))
            session.commit()
        session.rollback()
        with pytest.raises(DatabaseError, match="immutable"):
            session.execute(delete(RetrievalExperiment).where(
                RetrievalExperiment.id == experiment.id
            ))
            session.commit()
        session.rollback()
    with engine.begin() as connection:
        with pytest.raises(DatabaseError, match="immutable"):
            connection.execute(text(
                "UPDATE retrieval_experiments SET status='failed' WHERE id=:id"
            ), {"id": experiment.id})
    engine.dispose()


def test_agreement_summary_uses_only_scoped_judgments(tmp_path: Path) -> None:
    engine, repository = _repository(tmp_path)
    experiment = _create(repository)
    for actor, rank, label, score in (
        ("judge-a", 1, "relevant", 3),
        ("judge-b", 1, "relevant", 3),
        ("judge-a", 2, "partial", 2),
        ("judge-b", 2, "irrelevant", 0),
    ):
        repository.add_judgment(
            tenant_id="tenant-1", dataset_id="dataset-1", experiment_id=experiment.id,
            result_rank=rank, relevance_label=label, score=score,
            audit=_audit(actor, f"{actor}-{rank}"),
        )

    summary = repository.summarize_agreement("tenant-1", "dataset-1", experiment.id)
    assert summary.judged_results == 2
    assert summary.judgment_count == 4
    assert summary.multi_judged_results == 2
    assert summary.unanimous_results == 1
    assert summary.conflicting_results == 1
    assert summary.exact_agreement_rate == 0.5
    assert summary.label_counts == {"irrelevant": 1, "partial": 1, "relevant": 2}
    assert summary.mean_score == 2.0
    engine.dispose()


def test_duplicate_reviewer_and_database_constraints_are_safe(tmp_path: Path) -> None:
    engine, repository = _repository(tmp_path)
    experiment = _create(repository)
    repository.add_judgment(
        tenant_id="tenant-1", dataset_id="dataset-1", experiment_id=experiment.id,
        result_rank=1, relevance_label="relevant", audit=_audit("judge-a", "first")
    )
    with pytest.raises(RetrievalExperimentConflict, match="already judged"):
        repository.add_judgment(
            tenant_id="tenant-1", dataset_id="dataset-1", experiment_id=experiment.id,
            result_rank=1, relevance_label="partial", audit=_audit("judge-a", "duplicate")
        )

    with Session(engine) as session:
        row = session.scalar(select(RetrievalJudgment))
        assert row is not None
        row.score = 4
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
    engine.dispose()


def test_audit_insert_failure_rolls_back_experiment_and_judgment_mutations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, repository = _repository(tmp_path)
    strategy, results, lineage = _snapshots()

    def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(repository, "_add_audit_event", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        repository.create_experiment(
            tenant_id="tenant-1", dataset_id="dataset-1", query="rollback",
            strategy_snapshot=strategy, result_snapshot=results, evidence_lineage=lineage,
            latency_ms=3, status="completed", audit=_audit("author", "rollback-create")
        )
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(RetrievalExperiment)) == 0

    monkeypatch.undo()
    experiment = _create(repository)
    monkeypatch.setattr(repository, "_add_audit_event", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        repository.add_judgment(
            tenant_id="tenant-1", dataset_id="dataset-1", experiment_id=experiment.id,
            result_rank=1, relevance_label="relevant", audit=_audit("judge-a", "rollback-judge")
        )
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(RetrievalJudgment)) == 0
    engine.dispose()


def _empty_snapshots(generation: int = 12) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    return (
        {"strategy_revision": 1, "dataset_serving_generation": generation},
        {"result_revision": 1, "dataset_serving_generation": generation, "results": []},
        {"evidence_revision": 1, "dataset_serving_generation": generation, "citations": []},
    )


def test_empty_results_allow_empty_citations(tmp_path: Path) -> None:
    _, repository = _repository(tmp_path)
    strategy, results, lineage = _empty_snapshots()

    row = repository.create_experiment(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        query="no hit",
        strategy_snapshot=strategy,
        result_snapshot=results,
        evidence_lineage=lineage,
        latency_ms=1,
        status="completed",
        audit=_audit("judge-a", "empty-results"),
    )

    assert row.result_snapshot["results"] == []
    assert row.evidence_lineage["citations"] == []


def test_empty_results_reject_nonempty_citations(tmp_path: Path) -> None:
    _, repository = _repository(tmp_path)
    strategy, results, lineage = _empty_snapshots()
    lineage["citations"] = [{"rank": 1}]

    with pytest.raises(ValueError, match="forged|missing|empty"):
        repository.create_experiment(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            query="forged empty",
            strategy_snapshot=strategy,
            result_snapshot=results,
            evidence_lineage=lineage,
            latency_ms=1,
            status="completed",
            audit=_audit("judge-a", "forged-empty"),
        )


def test_create_experiments_batch_rolls_back_all_rows_and_audits(tmp_path: Path) -> None:
    engine, repository = _repository(tmp_path)
    strategy, results, lineage = _empty_snapshots()
    item = {
        "query": "batch query",
        "strategy_snapshot": strategy,
        "result_snapshot": results,
        "evidence_lineage": lineage,
        "latency_ms": 1,
        "status": "completed",
        "experiment_id": "duplicate-experiment",
    }

    with pytest.raises(RetrievalExperimentConflict):
        repository.create_experiments_batch(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            run_id="run-atomic",
            expected_generation=12,
            experiments=[item, dict(item)],
            audit=_audit("judge-a", "batch-rollback"),
        )

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(RetrievalExperiment)) == 0
        assert session.scalar(select(func.count()).select_from(KnowledgeAuditEvent)) == 0


def test_create_experiments_batch_rejects_generation_race_without_partial_rows(
    tmp_path: Path,
) -> None:
    engine, repository = _repository(tmp_path)
    strategy, results, lineage = _empty_snapshots()
    with Session(engine) as session:
        session.execute(
            update(Dataset)
            .where(Dataset.id == "dataset-1", Dataset.tenant_id == "tenant-1")
            .values(serving_generation=13)
        )
        session.commit()

    with pytest.raises(RetrievalExperimentConflict, match="generation"):
        repository.create_experiments_batch(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            run_id="run-race",
            expected_generation=12,
            experiments=[{
                "query": "raced query",
                "strategy_snapshot": strategy,
                "result_snapshot": results,
                "evidence_lineage": lineage,
                "latency_ms": 1,
                "status": "completed",
            }],
            audit=_audit("judge-a", "batch-race"),
        )

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(RetrievalExperiment)) == 0
        assert session.scalar(select(func.count()).select_from(KnowledgeAuditEvent)) == 0
