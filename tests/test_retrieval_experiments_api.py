from __future__ import annotations

import time
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.exceptions import HTTPException as StarletteHTTPException

from config.settings import KnowledgeSecuritySettings, RunHistorySettings, TenantSettings
from core import catalog
from models.orm import (
    Account,
    Base,
    ChunkHead,
    Dataset,
    Document,
    KnowledgeAuditEvent,
    RetrievalExperiment,
    Tenant,
    TenantMember,
)
from server.knowledge_auth import issue_knowledge_actor_token
from server.middleware import (
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from server.retrieval_experiments_api import JudgmentPatch, router


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        run_history=RunHistorySettings(ops_bearer_token=SecretStr("ops-secret")),
        knowledge_security=KnowledgeSecuritySettings(
            actor_signing_secret=SecretStr("retrieval-api-actor-secret"),
            actor_max_ttl_s=900,
        ),
        tenant=TenantSettings(enforced=True, default_tenant="tenant-a"),
    )


def _engine(database_url: str = "sqlite+pysqlite:///:memory:"):
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id="tenant-a", name="Tenant A", status="active"),
                Tenant(id="tenant-b", name="Tenant B", status="active"),
                Dataset(
                    id="dataset-a",
                    tenant_id="tenant-a",
                    name="Dataset A",
                    status="active",
                    serving_generation=12,
                ),
                Dataset(
                    id="dataset-a2",
                    tenant_id="tenant-a",
                    name="Dataset A2",
                    status="archived",
                    serving_generation=12,
                ),
                Dataset(
                    id="dataset-a3",
                    tenant_id="tenant-a",
                    name="Dataset A3",
                    status="disabled",
                    serving_generation=12,
                ),
                Dataset(id="dataset-b", tenant_id="tenant-b", name="Dataset B", status="active"),
                Account(id="admin-a", name="Admin A", email="admin-a@example.test"),
                Account(id="editor-a", name="Editor A", email="editor-a@example.test"),
                Account(id="member-a", name="Member A", email="member-a@example.test"),
                Account(id="admin-b", name="Admin B", email="admin-b@example.test"),
                TenantMember(account_id="admin-a", tenant_id="tenant-a", role="admin"),
                TenantMember(account_id="editor-a", tenant_id="tenant-a", role="editor"),
                TenantMember(account_id="member-a", tenant_id="tenant-a", role="member"),
                TenantMember(account_id="admin-b", tenant_id="tenant-b", role="admin"),
                Document(
                    id="doc-1",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    name="Document One",
                    content_revision=4,
                ),
                Document(
                    id="doc-2",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    name="Document Two",
                    content_revision=2,
                ),
            ]
        )
        session.flush()
        session.add(
            ChunkHead(
                id="chunk-1",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                document_id="doc-1",
                chunk_index=0,
                document_revision=4,
                content_revision=8,
                source_content="source",
                content="content",
                content_hash="hash",
            )
        )
        session.commit()
    return engine


@pytest.fixture()
def api(monkeypatch: pytest.MonkeyPatch):
    engine, settings = _engine(), _settings()
    monkeypatch.setattr(catalog, "get_engine", lambda: engine)
    app = FastAPI()
    app.state.knowledge_auth_engine = engine
    app.state.knowledge_auth_settings = settings
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.include_router(router)
    return (
        TestClient(app, client=("10.0.0.2", 50000), raise_server_exceptions=False),
        engine,
        settings,
        app,
    )


def _headers(
    settings: SimpleNamespace,
    actor: str,
    tenant: str = "tenant-a",
    *,
    asserted_tenant: str | None = None,
    request_id: str = "req-retrieval-api",
) -> dict[str, str]:
    token = issue_knowledge_actor_token(actor, tenant, 300, int(time.time()), settings=settings)
    return {
        "Authorization": f"Bearer {token}",
        "X-RAG4C-Tenant": asserted_tenant or tenant,
        "X-Request-ID": request_id,
    }


def _snapshots() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        {
            "strategy_revision": 9,
            "dataset_serving_generation": 12,
            "mode": "hybrid",
            "retrieval": {"dense_k": 24, "lexical_k": 16},
            "reranker": {
                "name": "bge",
                "api_token": {"credential_ref": "vault://retrieval/reranker"},
                "token_count": 73,
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
                    "score": 0.875,
                },
                {
                    "rank": 2,
                    "document_id": "doc-2",
                    "chunk_id": None,
                    "document_revision": 2,
                    "content_revision": 0,
                    "score": 0.5,
                },
            ],
        },
        {
            "evidence_revision": 2,
            "dataset_serving_generation": 12,
            "citations": [
                {
                    "rank": 1,
                    "document_id": "doc-1",
                    "chunk_id": "chunk-1",
                    "document_revision": 4,
                    "content_revision": 8,
                    "content_hash": "hash",
                    "uri": "knowledge://doc-1/chunk-1",
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
    )


def _create_body(*, query: str = "What is governed retrieval?") -> dict[str, Any]:
    strategy, results, lineage = _snapshots()
    return {
        "query": query,
        "strategy_snapshot": strategy,
        "result_snapshot": results,
        "evidence_lineage": lineage,
        "latency_ms": 37,
        "status": "completed",
        "run_id": "run-1",
    }


def _create(
    client: TestClient,
    settings: SimpleNamespace,
    *,
    actor: str = "editor-a",
    dataset_id: str = "dataset-a",
    query: str = "What is governed retrieval?",
) -> dict[str, Any]:
    response = client.post(
        f"/api/knowledge-bases/{dataset_id}/retrieval-experiments",
        headers=_headers(settings, actor),
        json=_create_body(query=query),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_get_and_list_preserve_snapshots_and_signed_actor_audit(api) -> None:
    client, engine, settings, _ = api
    body = _create_body()
    response = client.post(
        "/api/knowledge-bases/dataset-a/retrieval-experiments",
        headers=_headers(settings, "editor-a", request_id="req-create-experiment"),
        json=body,
    )

    assert response.status_code == 201
    created = response.json()
    assert created["sequence"] == 1
    assert created["tenant_id"] == "tenant-a"
    assert created["dataset_id"] == "dataset-a"
    assert created["query"] == body["query"]
    assert created["strategy_snapshot"] == body["strategy_snapshot"]
    assert created["result_snapshot"] == body["result_snapshot"]
    assert created["evidence_lineage"] == body["evidence_lineage"]
    assert created["status"] == "completed"
    assert created["created_at"].endswith("Z")
    assert created["judgments"] == []

    listed = client.get(
        "/api/knowledge-bases/dataset-a/retrieval-experiments",
        headers=_headers(settings, "member-a"),
    )
    assert listed.status_code == 200
    assert listed.json()["items"] == [
        {key: value for key, value in created.items() if key != "judgments"}
    ]
    assert listed.json()["next_before_sequence"] is None

    fetched = client.get(
        f"/api/knowledge-bases/dataset-a/retrieval-experiments/{created['id']}",
        headers=_headers(settings, "member-a"),
    )
    assert fetched.status_code == 200
    assert fetched.json() == created

    with Session(engine) as session:
        experiment = session.get(RetrievalExperiment, 1)
        assert experiment is not None
        assert experiment.created_by == "editor-a"
        assert experiment.strategy_snapshot == body["strategy_snapshot"]
        audit = session.scalar(
            select(KnowledgeAuditEvent).where(
                KnowledgeAuditEvent.resource_id == created["id"],
                KnowledgeAuditEvent.action == "retrieval_experiment.create",
            )
        )
        assert audit is not None
        assert audit.actor_id == "editor-a"
        assert audit.request_id == "req-create-experiment"


def test_judgment_lifecycle_conflicts_scoping_and_agreement(api) -> None:
    client, _, settings, _ = api
    experiment = _create(client, settings)
    other = _create(client, settings, query="A second experiment")

    forbidden = client.post(
        f"/api/knowledge-bases/dataset-a/retrieval-experiments/{experiment['id']}/judgments",
        headers=_headers(settings, "member-a"),
        json={"result_rank": 1, "relevance_label": "relevant", "score": 3},
    )
    assert forbidden.status_code == 403

    first = client.post(
        f"/api/knowledge-bases/dataset-a/retrieval-experiments/{experiment['id']}/judgments",
        headers=_headers(settings, "editor-a", request_id="req-add-judgment"),
        json={
            "result_rank": 1,
            "document_id": "doc-1",
            "chunk_id": "chunk-1",
            "relevance_label": "relevant",
            "score": 3,
            "note": "strong match",
        },
    )
    assert first.status_code == 201
    judgment = first.json()
    assert judgment["revision"] == 1
    assert judgment["created_by"] == "editor-a"
    assert judgment["created_at"].endswith("Z")

    duplicate = client.post(
        f"/api/knowledge-bases/dataset-a/retrieval-experiments/{experiment['id']}/judgments",
        headers=_headers(settings, "editor-a"),
        json={"result_rank": 1, "relevance_label": "partial"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "retrieval_experiment_conflict"

    wrong_experiment = client.patch(
        f"/api/knowledge-bases/dataset-a/retrieval-experiments/{other['id']}/judgments/{judgment['id']}",
        headers=_headers(settings, "editor-a"),
        json={"expected_revision": 1, "relevance_label": "partial", "score": 2},
    )
    assert wrong_experiment.status_code == 404

    wrong_actor = client.patch(
        f"/api/knowledge-bases/dataset-a/retrieval-experiments/{experiment['id']}/judgments/{judgment['id']}",
        headers=_headers(settings, "admin-a"),
        json={"expected_revision": 1, "relevance_label": "partial", "score": 2},
    )
    assert wrong_actor.status_code == 409

    updated = client.patch(
        f"/api/knowledge-bases/dataset-a/retrieval-experiments/{experiment['id']}/judgments/{judgment['id']}",
        headers=_headers(settings, "editor-a", request_id="req-update-judgment"),
        json={
            "expected_revision": 1,
            "relevance_label": "partial",
            "score": 2,
            "note": "reviewed again",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    assert updated.json()["relevance_label"] == "partial"

    second = client.post(
        f"/api/knowledge-bases/dataset-a/retrieval-experiments/{experiment['id']}/judgments",
        headers=_headers(settings, "admin-a"),
        json={"result_rank": 1, "relevance_label": "relevant", "score": 3},
    )
    assert second.status_code == 201

    fetched = client.get(
        f"/api/knowledge-bases/dataset-a/retrieval-experiments/{experiment['id']}",
        headers=_headers(settings, "member-a"),
    )
    assert fetched.status_code == 200
    assert [item["created_by"] for item in fetched.json()["judgments"]] == [
        "editor-a",
        "admin-a",
    ]

    agreement = client.get(
        f"/api/knowledge-bases/dataset-a/retrieval-experiments/{experiment['id']}/agreement",
        headers=_headers(settings, "member-a"),
    )
    assert agreement.status_code == 200
    assert agreement.json() == {
        "experiment_id": experiment["id"],
        "judged_results": 1,
        "judgment_count": 2,
        "multi_judged_results": 1,
        "unanimous_results": 0,
        "conflicting_results": 1,
        "exact_agreement_rate": 0.0,
        "label_counts": {"partial": 1, "relevant": 1},
        "mean_score": 2.5,
    }


def test_patch_preserves_omitted_judgment_fields_and_requires_a_change(api) -> None:
    client, _, settings, _ = api
    experiment = _create(client, settings)
    created = client.post(
        f"/api/knowledge-bases/dataset-a/retrieval-experiments/{experiment['id']}/judgments",
        headers=_headers(settings, "editor-a"),
        json={
            "result_rank": 1,
            "relevance_label": "relevant",
            "score": 3,
            "note": "initial",
        },
    )
    assert created.status_code == 201
    judgment = created.json()

    patched = client.patch(
        f"/api/knowledge-bases/dataset-a/retrieval-experiments/{experiment['id']}/judgments/{judgment['id']}",
        headers=_headers(settings, "editor-a"),
        json={"expected_revision": 1, "note": "note only"},
    )
    assert patched.status_code == 200
    assert patched.json()["revision"] == 2
    assert patched.json()["relevance_label"] == "relevant"
    assert patched.json()["score"] == 3
    assert patched.json()["note"] == "note only"

    no_change = client.patch(
        f"/api/knowledge-bases/dataset-a/retrieval-experiments/{experiment['id']}/judgments/{judgment['id']}",
        headers=_headers(settings, "editor-a"),
        json={"expected_revision": 2},
    )
    assert no_change.status_code == 422
    assert no_change.json()["error"]["code"] == "validation_error"

    null_label = client.patch(
        f"/api/knowledge-bases/dataset-a/retrieval-experiments/{experiment['id']}/judgments/{judgment['id']}",
        headers=_headers(settings, "editor-a"),
        json={"expected_revision": 2, "relevance_label": None},
    )
    assert null_label.status_code == 422
    assert null_label.json()["error"]["code"] == "validation_error"


def test_patch_delegates_partial_fields_without_repository_prereads(
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, settings, _ = api
    captured: dict[str, Any] = {}

    class PartialRepository:
        def update_judgment_partial(self, **kwargs: Any) -> SimpleNamespace:
            captured.update(kwargs)
            return SimpleNamespace(
                id=kwargs["judgment_id"],
                tenant_id=kwargs["tenant_id"],
                dataset_id=kwargs["dataset_id"],
                experiment_id=kwargs["experiment_id"],
                result_rank=1,
                document_id="doc-1",
                chunk_id="chunk-1",
                relevance_label="relevant",
                score=3,
                note=kwargs["note"],
                revision=2,
                created_by="editor-a",
                created_at=datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
            )

    import server.retrieval_experiments_api as retrieval_api

    monkeypatch.setattr(
        retrieval_api, "_repository", lambda _request, **_kwargs: PartialRepository()
    )
    response = client.patch(
        "/api/knowledge-bases/dataset-a/retrieval-experiments/exp-1/judgments/judgment-1",
        headers=_headers(settings, "editor-a"),
        json={"expected_revision": 1, "note": "note only"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["created_at"] == "2026-08-25T12:00:00Z"
    assert captured["experiment_id"] == "exp-1"
    assert captured["judgment_id"] == "judgment-1"
    assert captured["note"] == "note only"
    assert "relevance_label" not in captured
    assert "score" not in captured


def test_archived_dataset_is_readable_but_not_writable(api) -> None:
    client, _, settings, _ = api
    readable = client.get(
        "/api/knowledge-bases/dataset-a2/retrieval-experiments",
        headers=_headers(settings, "member-a"),
    )
    assert readable.status_code == 200
    assert readable.json() == {"items": [], "next_before_sequence": None}

    disabled = client.get(
        "/api/knowledge-bases/dataset-a3/retrieval-experiments",
        headers=_headers(settings, "member-a"),
    )
    assert disabled.status_code == 403
    assert disabled.json()["error"]["code"] == "knowledge_dataset_inactive"

    blocked = client.post(
        "/api/knowledge-bases/dataset-a2/retrieval-experiments",
        headers=_headers(settings, "editor-a"),
        json=_create_body(query="Archived dataset write"),
    )
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "knowledge_dataset_inactive"


def test_auth_scope_validation_and_database_errors_use_safe_global_envelope(
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine, settings, _ = api

    missing = client.get("/api/knowledge-bases/dataset-a/retrieval-experiments")
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "knowledge_actor_token_required"

    mismatched_header = client.get(
        "/api/knowledge-bases/dataset-a/retrieval-experiments",
        headers=_headers(settings, "member-a", asserted_tenant="tenant-b"),
    )
    assert mismatched_header.status_code == 401

    missing_tenant_headers = _headers(settings, "member-a")
    missing_tenant_headers.pop("X-RAG4C-Tenant")
    missing_tenant = client.get(
        "/api/knowledge-bases/dataset-a/retrieval-experiments",
        headers=missing_tenant_headers,
    )
    assert missing_tenant.status_code == 400
    assert missing_tenant.json()["error"]["code"] == "tenant_required"

    cross_tenant = client.get(
        "/api/knowledge-bases/dataset-b/retrieval-experiments",
        headers=_headers(settings, "member-a"),
    )
    assert cross_tenant.status_code == 404
    assert cross_tenant.json()["error"]["code"] == "retrieval_experiment_not_found"

    read_only_create = client.post(
        "/api/knowledge-bases/dataset-a/retrieval-experiments",
        headers=_headers(settings, "member-a"),
        json=_create_body(),
    )
    assert read_only_create.status_code == 403

    for invalid_body in (
        {**_create_body(), "latency_ms": "37"},
        {**_create_body(), "status": "running"},
        {**_create_body(), "unexpected": True},
    ):
        response = client.post(
            "/api/knowledge-bases/dataset-a/retrieval-experiments",
            headers=_headers(settings, "editor-a"),
            json=invalid_body,
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    plaintext_secret = _create_body()
    plaintext_secret["strategy_snapshot"]["api_token"] = "SENTINEL-PLAINTEXT-SECRET"
    rejected = client.post(
        "/api/knowledge-bases/dataset-a/retrieval-experiments",
        headers=_headers(settings, "editor-a"),
        json=plaintext_secret,
    )
    assert rejected.status_code == 422
    assert "SENTINEL-PLAINTEXT-SECRET" not in rejected.text

    class BrokenRepository:
        def __init__(self):
            self.engine = engine

        def list_experiments(self, *_args, **_kwargs):
            raise SQLAlchemyError("postgresql://internal:SENTINEL-DB-SECRET@db/retrieval")

    import server.retrieval_experiments_api as retrieval_api

    monkeypatch.setattr(
        retrieval_api, "_repository", lambda _request, **_kwargs: BrokenRepository()
    )
    unavailable = client.get(
        "/api/knowledge-bases/dataset-a/retrieval-experiments",
        headers=_headers(settings, "member-a"),
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "retrieval_experiment_unavailable"
    assert "SENTINEL-DB-SECRET" not in unavailable.text


def test_pydantic_validation_is_strict_and_never_echoes_malformed_inputs(api) -> None:
    client, _, settings, _ = api
    headers = _headers(settings, "editor-a")
    secret = "SENTINEL-PYDANTIC-SECRET"
    invalid_creates = [
        {**_create_body(), "strategy_snapshot": [secret]},
        {**_create_body(), "result_snapshot": secret},
        {**_create_body(), "evidence_lineage": None},
        {**_create_body(), "latency_ms": secret},
        {**_create_body(), secret: True},
    ]
    for body in invalid_creates:
        response = client.post(
            "/api/knowledge-bases/dataset-a/retrieval-experiments",
            headers=headers,
            json=body,
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"
        assert secret not in response.text
        assert "'input'" not in response.text
        assert "'ctx'" not in response.text

    malformed_json = client.post(
        "/api/knowledge-bases/dataset-a/retrieval-experiments",
        headers={**headers, "Content-Type": "application/json"},
        content=b'{"query": "unterminated",',
    )
    assert malformed_json.status_code == 422
    assert malformed_json.json()["error"] == {
        "code": "validation_error",
        "message": "body: malformed JSON",
    }
    assert "unterminated" not in malformed_json.text

    experiment = _create(client, settings, query="strict judgments")
    invalid_judgments = [
        {"result_rank": secret, "relevance_label": "relevant"},
        {"result_rank": 1, "relevance_label": "relevant", secret: True},
    ]
    for body in invalid_judgments:
        response = client.post(
            f"/api/knowledge-bases/dataset-a/retrieval-experiments/{experiment['id']}/judgments",
            headers=headers,
            json=body,
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"
        assert secret not in response.text

    judgment = client.post(
        f"/api/knowledge-bases/dataset-a/retrieval-experiments/{experiment['id']}/judgments",
        headers=headers,
        json={"result_rank": 1, "relevance_label": "relevant"},
    ).json()
    invalid_patches = [
        {"expected_revision": secret, "note": "x"},
        {"expected_revision": 1, "note": None},
        {"expected_revision": 1, "relevance_label": None},
        {"expected_revision": 1},
        {"expected_revision": 1, "note": "x", secret: True},
    ]
    for body in invalid_patches:
        response = client.patch(
            f"/api/knowledge-bases/dataset-a/retrieval-experiments/{experiment['id']}/judgments/{judgment['id']}",
            headers=headers,
            json=body,
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] in {
            "validation_error",
            "retrieval_experiment_invalid",
        }
        assert secret not in response.text


@pytest.mark.parametrize(
    ("body", "error_type"),
    [
        ({"expected_revision": 1}, "judgment_patch_mutation_required"),
        (
            {"expected_revision": 1, "relevance_label": None},
            "judgment_patch_relevance_null",
        ),
        ({"expected_revision": 1, "note": None}, "judgment_patch_note_null"),
    ],
)
def test_judgment_patch_uses_stable_pydantic_error_codes(
    body: dict[str, Any], error_type: str
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        JudgmentPatch.model_validate(body)
    assert exc_info.value.errors()[0]["type"] == error_type


def test_judgment_patch_validation_has_stable_actionable_reasons(api) -> None:
    client, _, settings, _ = api
    experiment = _create(client, settings, query="patch validation reasons")
    judgment = client.post(
        f"/api/knowledge-bases/dataset-a/retrieval-experiments/{experiment['id']}/judgments",
        headers=_headers(settings, "editor-a"),
        json={"result_rank": 1, "relevance_label": "relevant"},
    ).json()
    endpoint = (
        f"/api/knowledge-bases/dataset-a/retrieval-experiments/{experiment['id']}"
        f"/judgments/{judgment['id']}"
    )
    cases = [
        (
            {"expected_revision": 1},
            "body: one of relevance_label, score, or note is required",
        ),
        (
            {"expected_revision": 1, "relevance_label": None},
            "body.relevance_label: relevance_label cannot be null",
        ),
        (
            {"expected_revision": 1, "note": None},
            "body.note: note cannot be null",
        ),
    ]
    for body, message in cases:
        response = client.patch(
            endpoint,
            headers=_headers(settings, "editor-a"),
            json=body,
        )
        assert response.status_code == 422
        assert response.json() == {"error": {"code": "validation_error", "message": message}}


def test_list_uses_positive_sequence_keyset_cursor_default_50_and_max_100(api) -> None:
    client, _, settings, _ = api
    created = [_create(client, settings, query=f"Experiment {index}") for index in range(1, 4)]

    first = client.get(
        "/api/knowledge-bases/dataset-a/retrieval-experiments",
        headers=_headers(settings, "member-a"),
        params={"limit": 2},
    )
    assert first.status_code == 200
    first_payload = first.json()
    assert [item["id"] for item in first_payload["items"]] == [
        created[2]["id"],
        created[1]["id"],
    ]
    assert first_payload["next_before_sequence"] == created[1]["sequence"]

    second = client.get(
        "/api/knowledge-bases/dataset-a/retrieval-experiments",
        headers=_headers(settings, "member-a"),
        params={"before_sequence": first_payload["next_before_sequence"], "limit": 2},
    )
    assert second.status_code == 200
    assert [item["id"] for item in second.json()["items"]] == [created[0]["id"]]
    assert second.json()["next_before_sequence"] is None

    for params in ({"before_sequence": 0}, {"before_sequence": 999}, {"limit": 101}):
        invalid = client.get(
            "/api/knowledge-bases/dataset-a/retrieval-experiments",
            headers=_headers(settings, "member-a"),
            params=params,
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] in {
            "validation_error",
            "retrieval_experiment_invalid",
        }

    default_operation = api[3].openapi()["paths"][
        "/api/knowledge-bases/{dataset_id}/retrieval-experiments"
    ]["get"]
    limit_parameter = next(
        parameter for parameter in default_operation["parameters"] if parameter["name"] == "limit"
    )
    assert limit_parameter["schema"]["default"] == 50
    assert limit_parameter["schema"]["maximum"] == 100


def test_retrieval_router_openapi_is_exact_secure_and_contains_no_internal_credentials(api) -> None:
    _, _, _, app = api
    schema = app.openapi()
    paths = schema["paths"]
    expected = {
        "/api/knowledge-bases/{dataset_id}/retrieval-experiments": {"get", "post"},
        "/api/knowledge-bases/{dataset_id}/retrieval-experiments/run": {"post"},
        "/api/knowledge-bases/{dataset_id}/retrieval-experiments/{experiment_id}": {"get"},
        "/api/knowledge-bases/{dataset_id}/retrieval-experiments/{experiment_id}/judgments": {
            "post"
        },
        "/api/knowledge-bases/{dataset_id}/retrieval-experiments/{experiment_id}/judgments/{judgment_id}": {
            "patch"
        },
        "/api/knowledge-bases/{dataset_id}/retrieval-experiments/{experiment_id}/agreement": {
            "get"
        },
    }
    assert {path: set(methods) for path, methods in paths.items()} == expected
    assert "KnowledgeBearerAuth" in schema["components"]["securitySchemes"]

    for path, methods in expected.items():
        for method in methods:
            operation = paths[path][method]
            assert operation["security"] == [{"KnowledgeBearerAuth": []}]
            assert {"400", "401", "403", "404", "409", "422"} <= set(operation["responses"])
            tenant_header = next(
                parameter
                for parameter in operation["parameters"]
                if parameter["name"] == "X-RAG4C-Tenant"
            )
            assert tenant_header["in"] == "header"
            assert "required for remote requests" in tenant_header["schema"]["description"]
            tenant_string_schema = next(
                item for item in tenant_header["schema"]["anyOf"] if item.get("type") == "string"
            )
            assert tenant_string_schema["maxLength"] == 64
            error_schema = operation["responses"]["400"]["content"]["application/json"]["schema"]
            assert error_schema["$ref"].endswith("/ErrorEnvelope")

    patch_schema = schema["components"]["schemas"]["JudgmentPatch"]
    assert patch_schema["required"] == ["expected_revision"]
    assert patch_schema["anyOf"] == [
        {"required": ["relevance_label"]},
        {"required": ["score"]},
        {"required": ["note"]},
    ]
    assert "default" not in patch_schema["properties"]["relevance_label"]
    assert "default" not in patch_schema["properties"]["note"]
    assert "default" not in patch_schema["properties"]["score"]
    assert patch_schema["properties"]["note"]["type"] == "string"
    assert {item.get("type") for item in patch_schema["properties"]["score"]["anyOf"]} == {
        "integer",
        "null",
    }

    schema_text = str(schema).casefold()
    assert "actor_signing_secret" not in schema_text
    assert "ops_bearer_token" not in schema_text
    assert "database_url" not in schema_text
    assert "internal credential" not in schema_text


def test_bridge_app_registers_retrieval_experiment_routes() -> None:
    from server.app import app as bridge_app

    paths = bridge_app.openapi()["paths"]
    assert {
        "/api/knowledge-bases/{dataset_id}/retrieval-experiments",
        "/api/knowledge-bases/{dataset_id}/retrieval-experiments/run",
        "/api/knowledge-bases/{dataset_id}/retrieval-experiments/{experiment_id}",
        "/api/knowledge-bases/{dataset_id}/retrieval-experiments/{experiment_id}/judgments",
        "/api/knowledge-bases/{dataset_id}/retrieval-experiments/{experiment_id}/judgments/{judgment_id}",
        "/api/knowledge-bases/{dataset_id}/retrieval-experiments/{experiment_id}/agreement",
    } <= set(paths)


def _run_body(variant_count: int = 2) -> dict[str, Any]:
    return {
        "query": "Where is governed retrieval documented?",
        "acl": ["legal"],
        "variants": [
            {
                "name": f"variant-{index}",
                "route_target": "hybrid",
                "top_k": index + 1,
                "hybrid_search_on": True,
                "rerank_on": False,
                "graph_retrieval_on": False,
                "sentence_window_on": False,
                "source_diversity": "off",
            }
            for index in range(variant_count)
        ],
    }


def _run_retrieval_pipeline() -> Any:
    from config.settings import PipelineSettings
    from models.schemas import Chunk, RetrievedChunk, RouteDecision
    from retrieval.pipeline import RetrievalPipeline

    now = datetime(2026, 8, 25, tzinfo=timezone.utc)

    class Embedder:
        @staticmethod
        def embed_query(_query: str) -> list[float]:
            return [1.0, 0.0]

    class Milvus:
        @staticmethod
        def build_filters(**_kwargs: Any) -> None:
            return None

        @staticmethod
        def hybrid_search(**_kwargs: Any) -> list[RetrievedChunk]:
            return [
                RetrievedChunk(
                    chunk=Chunk(
                        chunk_id="chunk-1",
                        doc_id="doc-1",
                        text="untrusted",
                        text_hash="hash",
                        created_at=now,
                        updated_at=now,
                        tenant_id="tenant-a",
                        dataset_id="dataset-a",
                        document_revision=4,
                        content_revision=8,
                    ),
                    score=0.8,
                    rank=0,
                    branch="hybrid",
                    dense_cosine=0.62,
                )
            ]

    class Rewriter:
        @staticmethod
        def rewrite(query: str) -> tuple[str, bool]:
            raise AssertionError(f"fixed route must not call rewrite LLM: {query}")

    class Router:
        @staticmethod
        def route(_query: str) -> RouteDecision:
            raise AssertionError("fixed route must not call router LLM")

    return RetrievalPipeline(
        embedder=Embedder(),
        milvus=Milvus(),
        reranker=None,
        rewriter=Rewriter(),
        router=Router(),
        settings=SimpleNamespace(
            pipeline=PipelineSettings(),
            tenant=TenantSettings(enforced=True, default_tenant="tenant-a"),
        ),
    )


def test_run_experiments_uses_writable_engine_when_reads_are_physically_read_only(
    tmp_path,
) -> None:
    database_path = tmp_path / "retrieval-experiments.db"
    write_engine = _engine(f"sqlite+pysqlite:///{database_path.as_posix()}")
    read_engine = create_engine(
        f"sqlite:///file:{database_path.as_posix()}?mode=ro&uri=true",
        connect_args={"uri": True, "check_same_thread": False},
    )
    settings = _settings()
    app = FastAPI()
    app.state.knowledge_auth_engine = read_engine
    app.state.retrieval_experiment_mutation_engine = write_engine
    app.state.knowledge_auth_settings = settings
    app.state.retrieval_experiment_retrieval = _run_retrieval_pipeline()
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.include_router(router)
    client = TestClient(app, client=("10.0.0.2", 50000), raise_server_exceptions=False)
    try:
        created = client.post(
            "/api/knowledge-bases/dataset-a/retrieval-experiments/run",
            headers=_headers(settings, "editor-a"),
            json=_run_body(2),
        )
        assert created.status_code == 201, created.text
        listed = client.get(
            "/api/knowledge-bases/dataset-a/retrieval-experiments",
            headers=_headers(settings, "member-a"),
        )
        assert listed.status_code == 200, listed.text
        assert len(listed.json()["items"]) == 2
        with Session(write_engine) as session:
            assert session.scalar(select(func.count()).select_from(RetrievalExperiment)) == 2
    finally:
        read_engine.dispose()
        write_engine.dispose()


def test_run_experiments_uses_injected_pure_retrieval_and_returns_ordered_facts(
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine, settings, app = api
    app.state.retrieval_experiment_retrieval = _run_retrieval_pipeline()
    import server.retrieval_experiments_api as retrieval_api

    monkeypatch.setattr(
        retrieval_api,
        "get_pipeline",
        lambda: (_ for _ in ()).throw(AssertionError("global pipeline must not be resolved")),
        raising=False,
    )

    response = client.post(
        "/api/knowledge-bases/dataset-a/retrieval-experiments/run",
        headers=_headers(settings, "editor-a", request_id="run-experiments"),
        json=_run_body(),
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["dataset_serving_generation"] == 12
    assert [item["name"] for item in payload["items"]] == ["variant-0", "variant-1"]
    assert "winner" not in payload
    assert all(item["run_id"] == payload["run_id"] for item in payload["items"])
    assert all(item["status"] == "completed" for item in payload["items"])
    assert all(item["route"] == "hybrid" for item in payload["items"])
    assert all(item["result_count"] == 1 for item in payload["items"])
    assert all(item["reranked"] is False for item in payload["items"])
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(RetrievalExperiment)) == 2
        assert session.scalar(select(func.count()).select_from(KnowledgeAuditEvent)) == 2


def test_run_experiments_enforces_write_active_scope_and_strict_max_four(api) -> None:
    client, _, settings, app = api
    app.state.retrieval_experiment_retrieval = _run_retrieval_pipeline()

    member = client.post(
        "/api/knowledge-bases/dataset-a/retrieval-experiments/run",
        headers=_headers(settings, "member-a"),
        json=_run_body(1),
    )
    assert member.status_code == 403

    cross_tenant = client.post(
        "/api/knowledge-bases/dataset-b/retrieval-experiments/run",
        headers=_headers(settings, "editor-a"),
        json=_run_body(1),
    )
    assert cross_tenant.status_code == 404

    archived = client.post(
        "/api/knowledge-bases/dataset-a2/retrieval-experiments/run",
        headers=_headers(settings, "editor-a"),
        json=_run_body(1),
    )
    assert archived.status_code == 403

    for body in (
        _run_body(5),
        {**_run_body(2), "variants": [_run_body(1)["variants"][0]] * 2},
        {**_run_body(1), "filters": {"document_id": "doc-1"}},
        {
            **_run_body(1),
            "variants": [{**_run_body(1)["variants"][0], "endpoint": "https://evil.test"}],
        },
    ):
        invalid = client.post(
            "/api/knowledge-bases/dataset-a/retrieval-experiments/run",
            headers=_headers(settings, "editor-a"),
            json=body,
        )
        assert invalid.status_code == 422, invalid.text


def test_run_experiment_openapi_contract_is_strict_and_documents_errors(api) -> None:
    _, _, _, app = api
    operation = app.openapi()["paths"][
        "/api/knowledge-bases/{dataset_id}/retrieval-experiments/run"
    ]["post"]
    assert operation["security"] == [{"KnowledgeBearerAuth": []}]
    assert {"201", "400", "401", "403", "404", "409", "422", "503"} <= set(
        operation["responses"]
    )
    tenant_header = next(
        parameter for parameter in operation["parameters"]
        if parameter["name"] == "X-RAG4C-Tenant"
    )
    assert tenant_header["in"] == "header"
    request_ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    request_schema = app.openapi()["components"]["schemas"][request_ref.rsplit("/", 1)[-1]]
    assert request_schema["additionalProperties"] is False
    variants_schema = request_schema["properties"]["variants"]
    assert variants_schema["minItems"] == 1
    assert variants_schema["maxItems"] == 4
    schema_text = str(operation).casefold()
    for forbidden in ("endpoint", "credential", "filter", "winner", "raw settings"):
        assert forbidden not in schema_text


def test_run_experiments_returns_safe_503_when_base_retrieval_is_unavailable(api) -> None:
    client, _, settings, app = api
    app.state.retrieval_experiment_retrieval = object()

    response = client.post(
        "/api/knowledge-bases/dataset-a/retrieval-experiments/run",
        headers=_headers(settings, "editor-a"),
        json=_run_body(1),
    )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "retrieval_experiment_unavailable",
            "message": "检索实验服务暂不可用",
        }
    }



def test_run_experiments_requires_dedicated_app_state_and_never_calls_global(
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine, settings, app = api
    for state_name in (
        "retrieval_experiment_retrieval",
        "retrieval_experiment_base_retrieval",
        "retrieval_pipeline",
        "retrieval",
    ):
        if hasattr(app.state, state_name):
            delattr(app.state, state_name)
    import server.retrieval_experiments_api as retrieval_api

    global_calls = 0

    def forbidden_global_pipeline() -> dict[str, Any]:
        nonlocal global_calls
        global_calls += 1
        return {"retrieval": _run_retrieval_pipeline()}

    monkeypatch.setattr(
        retrieval_api,
        "get_pipeline",
        forbidden_global_pipeline,
        raising=False,
    )

    response = client.post(
        "/api/knowledge-bases/dataset-a/retrieval-experiments/run",
        headers=_headers(settings, "editor-a"),
        json=_run_body(1),
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "retrieval_experiment_unavailable"
    assert global_calls == 0
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(RetrievalExperiment)) == 0
        assert session.scalar(select(func.count()).select_from(KnowledgeAuditEvent)) == 0


def test_bridge_lifespan_installs_uncached_dedicated_experiment_retrieval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import server.app as server_app

    settings = SimpleNamespace(sources=SimpleNamespace())
    dedicated_retrieval = object()
    global_retrieval = object()
    pipeline_calls: list[Any] = []
    run_runtime = SimpleNamespace(close=lambda: None)

    class RunFactory:
        @classmethod
        def bootstrap(cls, _settings: Any) -> Any:
            return run_runtime

    def fake_get_pipeline(configured: Any = None) -> dict[str, Any]:
        pipeline_calls.append(configured)
        return {
            "retrieval": dedicated_retrieval if configured is settings else global_retrieval
        }

    monkeypatch.setattr(server_app, "RunOpsRuntime", RunFactory)
    monkeypatch.setattr(server_app, "get_settings", lambda: settings)
    monkeypatch.setattr(server_app, "get_pipeline", fake_get_pipeline)
    monkeypatch.setattr(
        server_app.app.state,
        "knowledge_source_dispatcher_test_override",
        None,
        raising=False,
    )
    monkeypatch.setattr(server_app, "_start_query_executor", lambda: None)
    monkeypatch.setattr(server_app.documents_api, "start_ingest_executor", lambda: None)
    monkeypatch.setattr(server_app, "_prime_cache_epoch", lambda: None)
    monkeypatch.setattr(server_app, "_prewarm_router", lambda: None)
    monkeypatch.setattr(server_app, "_metrics_persist_loop", lambda: None)
    monkeypatch.setattr(server_app, "_persist_metrics_once", lambda: None)
    monkeypatch.setattr(server_app, "_shutdown_query_executor", lambda: None)
    monkeypatch.setattr(server_app.documents_api, "shutdown_ingest_executor", lambda: None)
    monkeypatch.setattr(
        server_app.documents_api,
        "shutdown_index_operation_worker",
        lambda: None,
    )
    if hasattr(server_app.app.state, "retrieval_experiment_retrieval"):
        delattr(server_app.app.state, "retrieval_experiment_retrieval")

    async def scenario() -> None:
        async with server_app.lifespan(server_app.app):
            assert (
                server_app.app.state.retrieval_experiment_retrieval
                is dedicated_retrieval
            )
            assert server_app.app.state.retrieval_experiment_retrieval is not global_retrieval
            assert pipeline_calls == [settings]
        assert not hasattr(server_app.app.state, "retrieval_experiment_retrieval")

    asyncio.run(scenario())
