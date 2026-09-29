from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
import json
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from config.settings import KnowledgeSecuritySettings, RunHistorySettings, TenantSettings
from core.chunk_catalog import ChunkCatalog
from models.orm import (
    Account,
    Base,
    Dataset,
    Document,
    DocumentDeleteBatch,
    DocumentDeleteOperation,
    DocumentIngestAttempt,
    IndexDeadLetter,
    IndexOperation,
    KnowledgeAuditEvent,
    Tenant,
    TenantMember,
)
from scripts.reconcile_chunk_authority import ReconcileReport
import server.knowledge_consistency_api as consistency_api
from server.knowledge_auth import issue_knowledge_actor_token

REPORT_SECRET = "YXdPepaKHpWMOu6ypgNSxKNac00g6vMXcEIGc0itofU"
RAW_CHUNK_ID = "raw-chunk-secret"
RAW_DOCUMENT_ID = "doc-a"
RAW_DEAD_LETTER_A = "dead-letter-a-secret"
RAW_DEAD_LETTER_B = "dead-letter-b-secret"


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        run_history=RunHistorySettings(ops_bearer_token=SecretStr("ops-secret")),
        knowledge_security=KnowledgeSecuritySettings(
            actor_signing_secret=SecretStr("consistency-api-actor-secret"),
            actor_max_ttl_s=900,
        ),
        tenant=TenantSettings(enforced=True, default_tenant="tenant-a"),
    )


def _engine(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'consistency-api.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id="tenant-a", name="Tenant A", status="active"),
                Tenant(id="tenant-b", name="Tenant B", status="active"),
                Dataset(id="dataset-a", tenant_id="tenant-a", name="Dataset A", status="active"),
                Dataset(id="dataset-b", tenant_id="tenant-b", name="Dataset B", status="active"),
                Account(id="owner-a", name="Owner", email="owner-a@example.test"),
                Account(id="editor-a", name="Editor", email="editor-a@example.test"),
                Account(id="member-a", name="Member", email="member-a@example.test"),
                Account(id="owner-b", name="Owner B", email="owner-b@example.test"),
                TenantMember(account_id="owner-a", tenant_id="tenant-a", role="owner"),
                TenantMember(account_id="editor-a", tenant_id="tenant-a", role="editor"),
                TenantMember(account_id="member-a", tenant_id="tenant-a", role="member"),
                TenantMember(account_id="owner-b", tenant_id="tenant-b", role="owner"),
                Document(
                    id="doc-a",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    name="A",
                    desired_index_revision=2,
                    mutation_generation=4,
                ),
                Document(id="doc-b", tenant_id="tenant-b", dataset_id="dataset-b", name="B"),
                DocumentIngestAttempt(
                    id="attempt-a",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    document_id="doc-a",
                    attempt_no=1,
                    input_revision=2,
                    document_generation=4,
                ),
                DocumentIngestAttempt(
                    id="attempt-b",
                    tenant_id="tenant-b",
                    dataset_id="dataset-b",
                    document_id="doc-b",
                    attempt_no=1,
                ),
            ]
        )
        session.flush()
        operation_a = IndexOperation(
            id="op-a-dead",
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            document_id="doc-a",
            attempt_id="attempt-a",
            target_store="milvus_chunks",
            operation="reconcile",
            dedup_key="dead-op-a",
            target_revision=2,
            document_generation=4,
            payload={"document_id": "doc-a", "target_revision": 2},
            status="dead",
            retry_count=3,
            max_retries=3,
            last_error_code="projection_failed",
            last_error="internal host and raw details must not leak",
        )
        operation_b = IndexOperation(
            id="op-b-dead",
            tenant_id="tenant-b",
            dataset_id="dataset-b",
            document_id="doc-b",
            attempt_id="attempt-b",
            target_store="graph",
            operation="reconcile",
            dedup_key="dead-op-b",
            target_revision=1,
            payload={"orphaned_ids": ["other-raw-chunk"]},
            status="dead",
            retry_count=2,
            max_retries=2,
        )
        session.add_all([operation_a, operation_b])
        session.flush()
        session.add_all(
            [
                IndexDeadLetter(
                    id=RAW_DEAD_LETTER_A,
                    operation_id=operation_a.id,
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    document_id="doc-a",
                    attempt_id="attempt-a",
                    dedup_key=operation_a.dedup_key,
                    target_store=operation_a.target_store,
                    operation=operation_a.operation,
                    target_revision=operation_a.target_revision,
                    payload=operation_a.payload,
                    retry_count=operation_a.retry_count,
                    last_error_code=operation_a.last_error_code,
                    last_error=operation_a.last_error,
                    failed_at=datetime(2026, 8, 25, 8, 0, 0),
                ),
                IndexDeadLetter(
                    id=RAW_DEAD_LETTER_B,
                    operation_id=operation_b.id,
                    tenant_id="tenant-b",
                    dataset_id="dataset-b",
                    document_id="doc-b",
                    attempt_id="attempt-b",
                    dedup_key=operation_b.dedup_key,
                    target_store=operation_b.target_store,
                    operation=operation_b.operation,
                    target_revision=operation_b.target_revision,
                    payload=operation_b.payload,
                    retry_count=operation_b.retry_count,
                    failed_at=datetime(2026, 8, 25, 8, 1, 0),
                ),
            ]
        )
        session.commit()
    return engine


def _report() -> ReconcileReport:
    return ReconcileReport(
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        mode="report-only",
        documents_scanned=3,
        authoritative_heads=7,
        projection_chunks=6,
        projection_read_incomplete_documents=0,
        missing_ids=(RAW_CHUNK_ID,),
        stale_ids=("raw-stale-secret",),
        orphaned_ids=("raw-orphan-secret",),
        stale_reasons={"raw-stale-secret": ("hash", "content_revision")},
        repair_blocked_document_ids=(RAW_DOCUMENT_ID,),
        enqueued=0,
        snapshot_count=3,
        batch_size=1000,
        manifest_hash="a" * 64,
        post_snapshot_excluded=0,
        next_cursor="sensitive-resume-cursor",
        complete=True,
    )


@pytest.fixture()
def api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    engine = _engine(tmp_path)
    settings = _settings()
    calls: list[dict[str, object]] = []

    def reconciler(**kwargs):
        calls.append(dict(kwargs))
        return _report()

    monkeypatch.setenv("RAG4C_ROLLOUT_REPORT_SECRET", REPORT_SECRET)
    from server.app import app as bridge_app

    # 裸赋值会污染进程级 app.state，后续用例拿到内存 sqlite 引擎；monkeypatch 会还原。
    # 下面四个 knowledge_consistency_* 是本用例新造的属性，仍由 teardown 的 delattr 收尾。
    monkeypatch.setattr(bridge_app.state, "knowledge_auth_engine", engine)
    monkeypatch.setattr(bridge_app.state, "knowledge_auth_settings", settings, raising=False)
    bridge_app.state.knowledge_consistency_engine = engine
    bridge_app.state.knowledge_consistency_report_secret = REPORT_SECRET
    bridge_app.state.knowledge_consistency_repair_enabled = False
    bridge_app.state.knowledge_consistency_reconciler = reconciler
    yield TestClient(bridge_app, client=("10.0.0.2", 50000)), engine, settings, calls

    for name in (
        "knowledge_consistency_engine",
        "knowledge_consistency_report_secret",
        "knowledge_consistency_repair_enabled",
        "knowledge_consistency_reconciler",
    ):
        if hasattr(bridge_app.state, name):
            delattr(bridge_app.state, name)
    engine.dispose()


def _headers(
    settings: SimpleNamespace,
    actor: str,
    *,
    tenant: str = "tenant-a",
    idempotency_key: str | None = None,
    request_id: str = "req-consistency-api",
) -> dict[str, str]:
    token = issue_knowledge_actor_token(actor, tenant, 300, int(time.time()), settings=settings)
    headers = {
        "Authorization": f"Bearer {token}",
        "X-RAG4C-Tenant": tenant,
        "X-Request-ID": request_id,
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _counts(engine) -> tuple[int, int]:
    with Session(engine) as session:
        return (
            int(session.scalar(select(func.count(IndexOperation.id))) or 0),
            int(session.scalar(select(func.count(KnowledgeAuditEvent.sequence))) or 0),
        )


def test_summary_requires_read_runs_dry_run_and_never_exposes_raw_identifiers(api) -> None:
    client, engine, settings, calls = api
    path = "/api/knowledge-bases/dataset-a/consistency/summary"

    assert client.get(path).status_code == 401
    before = _counts(engine)
    response = client.get(path, headers=_headers(settings, "member-a"))

    assert response.status_code == 200
    assert _counts(engine) == before
    assert calls[-1]["repair"] is False
    payload = response.json()
    # QA 权威计数自 e0bebde 起进摘要。它必须是固定词表 + 纯计数（无任何标识符），
    # 这里钉住键名与数值，而不把 note 的中文文案钉进整体相等断言。
    qa_authority = payload.pop("qa_authority")
    assert set(qa_authority) == {
        "total",
        "effective_retrieval",
        "pending_review",
        "rejected",
        "expired",
        "retrieval_disabled",
        "note",
    }
    assert {k: v for k, v in qa_authority.items() if k != "note"} == {
        "total": 0,
        "effective_retrieval": 0,
        "pending_review": 0,
        "rejected": 0,
        "expired": 0,
        "retrieval_disabled": 0,
    }
    assert payload == {
        "mode": "report-only",
        "projection_read_status": "best_effort",
        "counts": {
            "documents_scanned": 3,
            "authoritative_heads": 7,
            "projection_chunks": 6,
            "projection_read_incomplete_documents": 0,
            "missing_chunks": 1,
            "stale_chunks": 1,
            "orphaned_chunks": 1,
            "blocked_documents": 1,
        },
        "drift_categories": {
            "missing": 1,
            "stale": 1,
            "orphaned": 1,
            "blocked": 1,
            "stale_reasons": {"content_revision": 1, "hash": 1},
        },
        "manifest_ref": payload["manifest_ref"],
        "complete": False,
        "confirmable": False,
        "snapshot_guarantee": "catalog_only",
        "best_effort": True,
        "has_drift": True,
    }
    assert payload["manifest_ref"].startswith("ref-")
    serialized = json.dumps(payload, sort_keys=True)
    for secret in (
        REPORT_SECRET,
        RAW_CHUNK_ID,
        RAW_DOCUMENT_ID,
        "raw-stale-secret",
        "raw-orphan-secret",
        "sensitive-resume-cursor",
        "tenant-a",
        "dataset-a",
    ):
        assert secret not in serialized


def test_summary_exposes_incomplete_projection_reads_without_inventing_drift(api) -> None:
    client, _engine, settings, _calls = api
    report = replace(
        _report(),
        missing_ids=(),
        stale_ids=(),
        orphaned_ids=(),
        stale_reasons={},
        repair_blocked_document_ids=(),
        projection_read_incomplete_documents=1,
    )
    client.app.state.knowledge_consistency_reconciler = lambda **_kwargs: report

    response = client.get(
        "/api/knowledge-bases/dataset-a/consistency/summary",
        headers=_headers(settings, "member-a"),
    )

    assert response.status_code == 200
    assert response.json()["projection_read_status"] == "incomplete"
    assert response.json()["counts"]["projection_read_incomplete_documents"] == 1
    assert response.json()["has_drift"] is False


def test_repair_plan_requires_manage_and_fails_without_projection_snapshot_authority(api) -> None:
    client, engine, settings, calls = api
    path = "/api/knowledge-bases/dataset-a/consistency/repair-plan"

    forbidden = client.post(path, headers=_headers(settings, "editor-a"))
    assert forbidden.status_code == 403

    before = _counts(engine)
    call_count = len(calls)
    response = client.post(path, headers=_headers(settings, "owner-a"))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == (
        "knowledge_consistency_projection_snapshot_unavailable"
    )
    assert "manifest" not in json.dumps(response.json(), sort_keys=True).casefold()
    assert _counts(engine) == before
    assert len(calls) == call_count


def test_repair_is_disabled_by_default_and_enabled_mode_fails_closed_without_atomic_adapter(
    api,
) -> None:
    client, engine, settings, calls = api
    path = "/api/knowledge-bases/dataset-a/consistency/repair"
    headers = _headers(
        settings,
        "owner-a",
        idempotency_key="repair-idempotency-0001",
    )
    body = {"confirm_manifest_ref": "ref-" + "b" * 64}
    before = _counts(engine)

    disabled_without_confirmation = client.post(
        path, headers=_headers(settings, "owner-a"), json={}
    )
    assert disabled_without_confirmation.status_code == 403

    disabled = client.post(path, headers=headers, json=body)
    assert disabled.status_code == 403
    assert disabled.json()["error"]["code"] == "knowledge_consistency_repair_disabled"
    assert _counts(engine) == before
    assert not any(call.get("repair") is True for call in calls)

    client.app.state.knowledge_consistency_repair_enabled = True
    missing_key = client.post(path, headers=_headers(settings, "owner-a"), json=body)
    assert missing_key.status_code == 422

    unavailable = client.post(path, headers=headers, json=body)
    assert unavailable.status_code == 501
    assert unavailable.json()["error"]["code"] == "knowledge_consistency_repair_not_atomic"
    assert _counts(engine) == before
    assert not any(call.get("repair") is True for call in calls)


def test_dead_letters_are_scoped_redacted_and_requeue_with_atomic_audit(api) -> None:
    client, engine, settings, _ = api
    path = "/api/knowledge-bases/dataset-a/consistency/dead-letters"
    listed = client.get(path, headers=_headers(settings, "member-a"))

    assert listed.status_code == 200
    payload = listed.json()
    assert payload["count"] == 1
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert len(item["dead_letter_ref"]) == 68
    assert len(item["operation_ref"]) == 68
    assert len(item["document_ref"]) == 68
    serialized = json.dumps(payload, sort_keys=True)
    for raw in (
        RAW_DEAD_LETTER_A,
        RAW_DEAD_LETTER_B,
        "op-a-dead",
        "op-b-dead",
        "doc-a",
        "doc-b",
        RAW_CHUNK_ID,
        "internal host and raw details must not leak",
    ):
        assert raw not in serialized

    ref = item["dead_letter_ref"]
    requeue_path = f"{path}/{ref}/requeue"
    forbidden = client.post(
        requeue_path,
        headers=_headers(settings, "editor-a"),
        json={"operator_note": "retry after inspection"},
    )
    assert forbidden.status_code == 403

    cross_scope_list = client.get(
        "/api/knowledge-bases/dataset-b/consistency/dead-letters",
        headers=_headers(settings, "owner-b", tenant="tenant-b"),
    )
    other_ref = cross_scope_list.json()["items"][0]["dead_letter_ref"]
    hidden = client.post(
        f"{path}/{other_ref}/requeue",
        headers=_headers(settings, "owner-a"),
        json={"operator_note": "must not cross scope"},
    )
    assert hidden.status_code == 404

    before = _counts(engine)
    accepted = client.post(
        requeue_path,
        headers=_headers(settings, "owner-a", request_id="req-requeue-1"),
        json={"operator_note": "retry after inspection"},
    )
    assert accepted.status_code == 202
    assert len(accepted.json()["operation_ref"]) == 68
    assert RAW_DEAD_LETTER_A not in json.dumps(accepted.json())
    after = _counts(engine)
    assert after == (before[0] + 1, before[1] + 1)

    repeated = client.post(
        requeue_path,
        headers=_headers(settings, "owner-a", request_id="req-requeue-2"),
        json={"operator_note": "retry after inspection"},
    )
    assert repeated.status_code == 202
    assert repeated.json()["operation_ref"] == accepted.json()["operation_ref"]
    assert _counts(engine) == after

    with Session(engine) as session:
        event = session.scalar(
            select(KnowledgeAuditEvent).where(
                KnowledgeAuditEvent.action == "consistency.dead_letter_requeued"
            )
        )
    assert event is not None
    assert event.tenant_id == "tenant-a"
    assert event.dataset_id == "dataset-a"
    assert event.actor_id == "owner-a"
    assert event.request_id == "req-requeue-1"
    assert RAW_DEAD_LETTER_A not in json.dumps(event.after_snapshot, sort_keys=True)


def test_bridge_exposes_exact_consistency_control_plane_routes(api) -> None:
    client, _, _, _ = api
    paths = client.app.openapi()["paths"]
    expected = {
        "/api/knowledge-bases/{dataset_id}/consistency/summary": {"get"},
        "/api/knowledge-bases/{dataset_id}/consistency/repair-plan": {"post"},
        "/api/knowledge-bases/{dataset_id}/consistency/repair": {"post"},
        "/api/knowledge-bases/{dataset_id}/consistency/dead-letters": {"get"},
        "/api/knowledge-bases/{dataset_id}/consistency/dead-letters/{dead_letter_ref}/requeue": {
            "post"
        },
    }
    for path, methods in expected.items():
        assert path in paths
        assert set(paths[path]) == methods


def _insert_delete_dead_letter(
    engine,
    *,
    suffix: str,
    target_store: str,
    operation_name: str,
) -> str:
    generation = 9
    with Session(engine) as session:
        document = session.get(Document, "doc-a")
        document.mutation_generation = generation
        batch = DocumentDeleteBatch(
            id=f"delete-batch-{suffix}",
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            idempotency_key=f"delete-idempotency-{suffix}",
            request_hash=(suffix * 64)[:64],
            actor_id="owner-a",
            status="running",
            requested_count=1,
            accepted_count=1,
        )
        attempt = DocumentIngestAttempt(
            id=f"attempt-delete-{suffix}",
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            document_id="doc-a",
            attempt_no=10,
            attempt_kind="document_delete",
            document_generation=generation,
        )
        parent = DocumentDeleteOperation(
            id=f"delete-parent-{suffix}",
            batch_id=batch.id,
            request_index=0,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            requested_document_id="doc-a",
            document_id="doc-a",
            expected_generation=generation - 1,
            delete_generation=generation,
            attempt_id=attempt.id,
            origin="operator",
            status="finalizing" if target_store == "catalog_finalize" else "projecting",
            requested_by="owner-a",
            required_store_count=2,
        )
        original = IndexOperation(
            id=f"delete-original-{suffix}",
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            document_id="doc-a",
            attempt_id=attempt.id,
            target_store=target_store,
            operation=operation_name,
            dedup_key=f"delete-original-dedup-{suffix}",
            target_revision=generation,
            document_generation=generation,
            delete_operation_id=parent.id,
            payload={
                "delete_operation_id": parent.id,
                "document_generation": generation,
                "chunk_manifest_hash": "c" * 64,
            },
            status="dead",
            retry_count=3,
            max_retries=3,
        )
        letter = IndexDeadLetter(
            id=f"delete-letter-{suffix}",
            operation_id=original.id,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            document_id="doc-a",
            attempt_id=attempt.id,
            dedup_key=original.dedup_key,
            target_store="tampered_projection",
            operation="tampered_operation",
            target_revision=1,
            payload={"tampered": True},
            retry_count=3,
        )
        session.add_all([batch, attempt, parent, original, letter])
        session.commit()
        return letter.id


def _insert_regular_dead_letter(engine, *, suffix: str) -> str:
    with Session(engine) as session:
        operation = IndexOperation(
            id=f"regular-original-{suffix}",
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            document_id="doc-a",
            attempt_id="attempt-a",
            target_store="milvus_chunks",
            operation="reconcile",
            dedup_key=f"regular-original-dedup-{suffix}",
            target_revision=5,
            document_generation=4,
            payload={"missing_ids": [f"raw-{suffix}"]},
            status="dead",
            retry_count=2,
            max_retries=2,
        )
        letter = IndexDeadLetter(
            id=f"regular-letter-{suffix}",
            operation_id=operation.id,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            document_id="doc-a",
            attempt_id="attempt-a",
            dedup_key=operation.dedup_key,
            target_store=operation.target_store,
            operation=operation.operation,
            target_revision=operation.target_revision,
            payload=operation.payload,
            retry_count=operation.retry_count,
        )
        session.add_all([operation, letter])
        session.commit()
        return letter.id


@pytest.mark.parametrize(
    ("suffix", "target_store", "operation_name"),
    [
        ("child", "milvus_chunks", "delete_document"),
        ("graph-child", "graph_projection", "delete_document"),
        ("finalizer", "catalog_finalize", "finalize_document_delete"),
    ],
)
def test_dead_letter_requeue_preserves_delete_child_and_finalizer_lineage(
    api,
    suffix: str,
    target_store: str,
    operation_name: str,
) -> None:
    client, engine, settings, _ = api
    letter_id = _insert_delete_dead_letter(
        engine,
        suffix=suffix,
        target_store=target_store,
        operation_name=operation_name,
    )
    ref = consistency_api._resource_ref(letter_id, report_secret=REPORT_SECRET, kind="dead-letter")

    response = client.post(
        f"/api/knowledge-bases/dataset-a/consistency/dead-letters/{ref}/requeue",
        headers=_headers(settings, "owner-a"),
        json={"operator_note": "lineage checked"},
    )

    assert response.status_code == 202
    with Session(engine) as session:
        letter = session.get(IndexDeadLetter, letter_id)
        original = session.get(IndexOperation, letter.operation_id)
        requeued = session.get(IndexOperation, letter.requeued_to_operation_id)
    assert requeued is not None
    assert original is not None
    assert (
        requeued.tenant_id,
        requeued.dataset_id,
        requeued.document_id,
        requeued.attempt_id,
    ) == (
        original.tenant_id,
        original.dataset_id,
        original.document_id,
        original.attempt_id,
    )
    assert requeued.target_store == target_store
    assert requeued.operation == operation_name
    assert requeued.target_revision == original.target_revision == 9
    assert requeued.document_generation == original.document_generation == 9
    assert requeued.delete_operation_id == original.delete_operation_id
    assert requeued.payload == original.payload


def test_dead_letter_requeue_rejects_scope_mismatch_between_letter_and_original(api) -> None:
    client, engine, settings, _ = api
    with Session(engine) as session:
        original = IndexOperation(
            id="scope-mismatch-original",
            tenant_id="tenant-b",
            dataset_id="dataset-b",
            document_id="doc-b",
            attempt_id="attempt-b",
            target_store="graph_projection",
            operation="delete_document",
            dedup_key="scope-mismatch-original-dedup",
            target_revision=6,
            document_generation=6,
            status="dead",
        )
        letter = IndexDeadLetter(
            id="scope-mismatch-letter",
            operation_id=original.id,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            document_id="doc-a",
            attempt_id="attempt-a",
            dedup_key=original.dedup_key,
            target_store=original.target_store,
            operation=original.operation,
            target_revision=original.target_revision,
            retry_count=1,
        )
        session.add_all([original, letter])
        letter_id = letter.id
        session.commit()
    ref = consistency_api._resource_ref(letter_id, report_secret=REPORT_SECRET, kind="dead-letter")

    response = client.post(
        f"/api/knowledge-bases/dataset-a/consistency/dead-letters/{ref}/requeue",
        headers=_headers(settings, "owner-a"),
        json={},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "knowledge_consistency_dead_letter_scope_conflict"


def test_dead_letter_ref_collision_fails_closed(api, monkeypatch: pytest.MonkeyPatch) -> None:
    client, engine, settings, _ = api
    _insert_regular_dead_letter(engine, suffix="collision")
    collision_ref = "ref-" + "a" * 64
    original_ref = consistency_api._resource_ref

    def collide(value: str, *, report_secret: str, kind: str) -> str:
        if kind == "dead-letter":
            return collision_ref
        return original_ref(value, report_secret=report_secret, kind=kind)

    monkeypatch.setattr(consistency_api, "_resource_ref", collide)
    response = client.post(
        f"/api/knowledge-bases/dataset-a/consistency/dead-letters/{collision_ref}/requeue",
        headers=_headers(settings, "owner-a"),
        json={},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "knowledge_consistency_ref_collision"


def test_concurrent_dead_letter_requeue_enqueues_once_and_reloads_dedup_winner(
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine, settings, _ = api
    listed = client.get(
        "/api/knowledge-bases/dataset-a/consistency/dead-letters",
        headers=_headers(settings, "owner-a"),
    )
    ref = listed.json()["items"][0]["dead_letter_ref"]
    path = f"/api/knowledge-bases/dataset-a/consistency/dead-letters/{ref}/requeue"
    before = _counts(engine)
    monkeypatch.setattr(consistency_api, "process_keyed_lock", lambda _key: nullcontext())

    def invoke(index: int):
        return client.post(
            path,
            headers=_headers(settings, "owner-a", request_id=f"req-concurrent-{index}"),
            json={"operator_note": "concurrent retry"},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(invoke, range(2)))

    assert [response.status_code for response in responses] == [202, 202]
    assert {response.json()["status"] for response in responses} == {
        "enqueued",
        "already_requeued",
    }
    assert len({response.json()["operation_ref"] for response in responses}) == 1
    after = _counts(engine)
    assert after == (before[0] + 1, before[1] + 1)


def test_openapi_declares_bearer_security_models_and_consistency_errors(api) -> None:
    client, _, _, _ = api
    schema = client.app.openapi()
    bearer = schema["components"]["securitySchemes"]["KnowledgeBearerAuth"]
    assert bearer["type"] == "http"
    assert bearer["scheme"] == "bearer"

    paths = schema["paths"]
    operations = [
        paths["/api/knowledge-bases/{dataset_id}/consistency/summary"]["get"],
        paths["/api/knowledge-bases/{dataset_id}/consistency/repair-plan"]["post"],
        paths["/api/knowledge-bases/{dataset_id}/consistency/repair"]["post"],
        paths["/api/knowledge-bases/{dataset_id}/consistency/dead-letters"]["get"],
        paths[
            "/api/knowledge-bases/{dataset_id}/consistency/dead-letters/{dead_letter_ref}/requeue"
        ]["post"],
    ]
    assert all(operation["security"] == [{"KnowledgeBearerAuth": []}] for operation in operations)

    summary = operations[0]
    assert summary["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ConsistencySummaryResponse"
    )
    plan = operations[1]
    assert plan["responses"]["503"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ErrorEnvelope"
    )
    dead_letters = operations[3]
    assert dead_letters["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/DeadLetterListResponse")
    requeue = operations[4]
    assert requeue["responses"]["202"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/DeadLetterRequeueResponse"
    )

    for operation in operations:
        assert operation["responses"]["422"]["content"]["application/json"]["schema"][
            "$ref"
        ].endswith("/ErrorEnvelope")

    repair = operations[2]
    assert "200" not in repair["responses"]
    assert repair["responses"]["501"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ErrorEnvelope"
    )
    assert "successful" not in repair["responses"]["501"]["description"].casefold()
    assert "successful" not in plan["responses"]["503"]["description"].casefold()

    declared = {code for operation in operations for code in operation["responses"]}
    assert {"401", "403", "404", "409", "422", "501", "503"}.issubset(declared)


def test_existing_requeue_dedup_must_match_original_lineage(api) -> None:
    client, engine, settings, _ = api
    listed = client.get(
        "/api/knowledge-bases/dataset-a/consistency/dead-letters",
        headers=_headers(settings, "owner-a"),
    )
    ref = listed.json()["items"][0]["dead_letter_ref"]
    with Session(engine) as session:
        session.add(
            IndexOperation(
                id="wrong-dedup-winner",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                document_id="doc-a",
                attempt_id="attempt-a",
                target_store="graph_projection",
                operation="delete_document",
                dedup_key=f"consistency:dead-letter-requeue:{ref[4:]}",
                target_revision=999,
                document_generation=999,
                payload={"wrong": True},
                status="pending",
            )
        )
        session.commit()

    response = client.post(
        f"/api/knowledge-bases/dataset-a/consistency/dead-letters/{ref}/requeue",
        headers=_headers(settings, "owner-a"),
        json={},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == (
        "knowledge_consistency_dead_letter_lineage_conflict"
    )


def test_integrity_error_audit_id_collision_does_not_accept_unlinked_dedup_operation(
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine, settings, _ = api
    listed = client.get(
        "/api/knowledge-bases/dataset-a/consistency/dead-letters",
        headers=_headers(settings, "owner-a"),
    )
    ref = listed.json()["items"][0]["dead_letter_ref"]
    fixed_hex = "1" * 32
    with Session(engine) as session:
        original = session.get(IndexOperation, "op-a-dead")
        session.add_all(
            [
                IndexOperation(
                    id="preexisting-valid-dedup",
                    tenant_id=original.tenant_id,
                    dataset_id=original.dataset_id,
                    document_id=original.document_id,
                    attempt_id=original.attempt_id,
                    target_store=original.target_store,
                    operation=original.operation,
                    dedup_key=f"consistency:dead-letter-requeue:{ref[4:]}",
                    target_revision=original.target_revision,
                    document_generation=original.document_generation,
                    delete_operation_id=original.delete_operation_id,
                    payload=original.payload,
                    status="pending",
                ),
                KnowledgeAuditEvent(
                    id=f"audit-{fixed_hex[:16]}",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    actor_id="owner-a",
                    action="unrelated.audit",
                    resource_type="test",
                    resource_id="unrelated",
                    request_id="existing-audit-id",
                ),
            ]
        )
        session.commit()

    monkeypatch.setattr(
        consistency_api.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex=fixed_hex),
    )
    response = client.post(
        f"/api/knowledge-bases/dataset-a/consistency/dead-letters/{ref}/requeue",
        headers=_headers(settings, "owner-a", request_id="audit-collision"),
        json={},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "knowledge_consistency_requeue_conflict"
    with Session(engine) as session:
        letter = session.get(IndexDeadLetter, RAW_DEAD_LETTER_A)
        matching_audits = list(
            session.scalars(
                select(KnowledgeAuditEvent).where(
                    KnowledgeAuditEvent.action == "consistency.dead_letter_requeued",
                    KnowledgeAuditEvent.resource_id == ref,
                )
            )
        )
    assert letter.requeued_to_operation_id is None
    assert matching_audits == []


def test_already_requeued_requires_exactly_one_matching_audit(api) -> None:
    client, engine, settings, _ = api
    listed = client.get(
        "/api/knowledge-bases/dataset-a/consistency/dead-letters",
        headers=_headers(settings, "owner-a"),
    )
    ref = listed.json()["items"][0]["dead_letter_ref"]
    path = f"/api/knowledge-bases/dataset-a/consistency/dead-letters/{ref}/requeue"
    first = client.post(path, headers=_headers(settings, "owner-a"), json={})
    assert first.status_code == 202
    operation_ref = first.json()["operation_ref"]
    with Session(engine) as session:
        session.add(
            KnowledgeAuditEvent(
                id="audit-duplicate-requeue",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                actor_id="owner-a",
                action="consistency.dead_letter_requeued",
                resource_type="index_dead_letter",
                resource_id=ref,
                after_snapshot={"dead_letter_ref": ref, "operation_ref": operation_ref},
                request_id="duplicate-audit",
            )
        )
        session.commit()

    repeated = client.post(path, headers=_headers(settings, "owner-a"), json={})

    assert repeated.status_code == 409
    assert repeated.json()["error"]["code"] == "knowledge_consistency_requeue_conflict"


def test_requeue_rejects_mutually_consistent_denormalized_cross_scope_corruption(api) -> None:
    client, engine, settings, _ = api
    with Session(engine) as session:
        operation = IndexOperation(
            id="canonical-cross-scope-original",
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            document_id="doc-b",
            attempt_id="attempt-b",
            target_store="milvus_chunks",
            operation="reconcile",
            dedup_key="canonical-cross-scope-dedup",
            target_revision=0,
            document_generation=0,
            status="dead",
        )
        letter = IndexDeadLetter(
            id="canonical-cross-scope-letter",
            operation_id=operation.id,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            document_id="doc-b",
            attempt_id="attempt-b",
            dedup_key=operation.dedup_key,
            target_store=operation.target_store,
            operation=operation.operation,
            target_revision=operation.target_revision,
            retry_count=1,
        )
        session.add_all([operation, letter])
        session.commit()
    ref = consistency_api._resource_ref(
        "canonical-cross-scope-letter",
        report_secret=REPORT_SECRET,
        kind="dead-letter",
    )

    response = client.post(
        f"/api/knowledge-bases/dataset-a/consistency/dead-letters/{ref}/requeue",
        headers=_headers(settings, "owner-a"),
        json={},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "knowledge_consistency_dead_letter_scope_conflict"


def _configure_supported_ordinary_operation(
    engine, *, target_store: str, operation_name: str
) -> None:
    if operation_name == "delete":
        catalog = ChunkCatalog(engine)
        catalog.upsert_head(
            chunk_id="chunk-delete",
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            document_id="doc-a",
            parent_chunk_id=None,
            chunk_index=0,
            chunk_role="flat",
            document_revision=2,
            source_content="delete me",
            content="delete me",
            enabled=True,
        )
        head = catalog.tombstone_chunk("chunk-delete", expected_revision=0, editor_id="owner-a")
    with Session(engine) as session:
        original = session.get(IndexOperation, "op-a-dead")
        attempt = session.get(DocumentIngestAttempt, "attempt-a")
        original.target_store = target_store
        original.operation = operation_name
        if operation_name == "upsert":
            attempt.attempt_kind = "ingest"
            original.payload = {
                "chunk_ids": ["chunk-a"],
                "document_generation": 4,
                "dataset_generation": 0,
            }
        elif operation_name == "delete":
            attempt.attempt_kind = "chunk_mutation"
            original.payload = {
                "chunk_id": head.id,
                "chunk_ids": [head.id],
                "expected_content_revision": head.content_revision,
                "mutation": "delete",
                "document_generation": 4,
                "dataset_generation": 0,
            }
        else:
            attempt.attempt_kind = "ingest"
            original.payload = {"document_id": "doc-a", "target_revision": 2}
        session.commit()


@pytest.mark.parametrize(
    ("target_store", "operation_name"),
    [
        ("milvus_chunks", "upsert"),
        ("milvus_chunks", "delete"),
        ("milvus_chunks", "reconcile"),
        ("graph_projection", "upsert"),
        ("graph_projection", "delete"),
    ],
)
def test_requeue_accepts_supported_ordinary_projection_pairs(
    api, target_store: str, operation_name: str
) -> None:
    client, engine, settings, _ = api
    _configure_supported_ordinary_operation(
        engine, target_store=target_store, operation_name=operation_name
    )
    ref = consistency_api._resource_ref(
        RAW_DEAD_LETTER_A, report_secret=REPORT_SECRET, kind="dead-letter"
    )

    response = client.post(
        f"/api/knowledge-bases/dataset-a/consistency/dead-letters/{ref}/requeue",
        headers=_headers(settings, "owner-a"),
        json={},
    )

    assert response.status_code == 202
    with Session(engine) as session:
        letter = session.get(IndexDeadLetter, RAW_DEAD_LETTER_A)
        requeued = session.get(IndexOperation, letter.requeued_to_operation_id)
    assert (requeued.target_store, requeued.operation) == (target_store, operation_name)


def test_custom_projection_requeue_dispatches_registered_validator_and_copies_original(
    api,
) -> None:
    from core.projection_target_contract import (
        ProjectionRequeuePolicy,
        register_projection_requeue_policy,
        unregister_projection_requeue_policy,
    )

    client, engine, settings, _ = api
    target_store = "custom_vector"
    seen: list[tuple[str, str, tuple[str, ...]]] = []

    def validate_target(context) -> bool:
        seen.append(
            (
                context.target_store,
                context.operation,
                context.payload["chunk_ids"],
            )
        )
        return context.payload["chunk_ids"] == ("chunk-a",)

    register_projection_requeue_policy(
        target_store,
        "upsert",
        ProjectionRequeuePolicy("ordinary", validate_target),
    )
    try:
        _configure_supported_ordinary_operation(
            engine,
            target_store=target_store,
            operation_name="upsert",
        )
        ref = consistency_api._resource_ref(
            RAW_DEAD_LETTER_A,
            report_secret=REPORT_SECRET,
            kind="dead-letter",
        )

        response = client.post(
            f"/api/knowledge-bases/dataset-a/consistency/dead-letters/{ref}/requeue",
            headers=_headers(settings, "owner-a", request_id="custom-vector-requeue"),
            json={},
        )

        assert response.status_code == 202
        assert seen == [(target_store, "upsert", ("chunk-a",))]
        with Session(engine) as session:
            letter = session.get(IndexDeadLetter, RAW_DEAD_LETTER_A)
            original = session.get(IndexOperation, "op-a-dead")
            requeued = session.get(IndexOperation, letter.requeued_to_operation_id)
            audit_count = int(
                session.scalar(
                    select(func.count(KnowledgeAuditEvent.sequence)).where(
                        KnowledgeAuditEvent.action == "consistency.dead_letter_requeued"
                    )
                )
                or 0
            )
        assert original is not None
        assert requeued is not None
        assert (
            requeued.target_store,
            requeued.operation,
            requeued.target_revision,
            requeued.document_generation,
            requeued.delete_operation_id,
            dict(requeued.payload or {}),
        ) == (
            original.target_store,
            original.operation,
            original.target_revision,
            original.document_generation,
            original.delete_operation_id,
            dict(original.payload or {}),
        )
        assert audit_count == 1
    finally:
        unregister_projection_requeue_policy(target_store, "upsert")


def test_unregistered_custom_projection_requeue_fails_without_writes(api) -> None:
    client, engine, settings, _ = api
    target_store = "custom_vector"
    _configure_supported_ordinary_operation(
        engine,
        target_store=target_store,
        operation_name="upsert",
    )
    ref = consistency_api._resource_ref(
        RAW_DEAD_LETTER_A,
        report_secret=REPORT_SECRET,
        kind="dead-letter",
    )
    before = _counts(engine)

    response = client.post(
        f"/api/knowledge-bases/dataset-a/consistency/dead-letters/{ref}/requeue",
        headers=_headers(settings, "owner-a"),
        json={},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "knowledge_consistency_dead_letter_target_conflict"
    assert _counts(engine) == before
    with Session(engine) as session:
        letter = session.get(IndexDeadLetter, RAW_DEAD_LETTER_A)
    assert letter is not None
    assert letter.requeued_to_operation_id is None


def test_custom_projection_requeue_fails_closed_when_live_registry_dispatch_is_bypassed(
    api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.projection_target_contract import (
        ProjectionRequeuePolicy,
        register_projection_requeue_policy,
        unregister_projection_requeue_policy,
    )

    client, engine, settings, _ = api
    target_store = "custom_vector"
    validator_calls: list[str] = []

    def validate_target(context) -> bool:
        validator_calls.append(context.target_store)
        return True

    register_projection_requeue_policy(
        target_store,
        "upsert",
        ProjectionRequeuePolicy("ordinary", validate_target),
    )
    try:
        _configure_supported_ordinary_operation(
            engine,
            target_store=target_store,
            operation_name="upsert",
        )
        ref = consistency_api._resource_ref(
            RAW_DEAD_LETTER_A,
            report_secret=REPORT_SECRET,
            kind="dead-letter",
        )
        monkeypatch.setattr(
            consistency_api,
            "resolve_projection_requeue_policy",
            lambda *_args: None,
        )
        before = _counts(engine)

        response = client.post(
            f"/api/knowledge-bases/dataset-a/consistency/dead-letters/{ref}/requeue",
            headers=_headers(settings, "owner-a"),
            json={},
        )

        assert response.status_code == 409
        assert (
            response.json()["error"]["code"] == "knowledge_consistency_dead_letter_target_conflict"
        )
        assert validator_calls == []
        assert _counts(engine) == before
    finally:
        unregister_projection_requeue_policy(target_store, "upsert")


def test_custom_projection_validator_cannot_bypass_canonical_generation_fences(api) -> None:
    from core.projection_target_contract import (
        ProjectionRequeuePolicy,
        register_projection_requeue_policy,
        unregister_projection_requeue_policy,
    )

    client, engine, settings, _ = api
    target_store = "custom_vector"
    validator_calls: list[str] = []

    def validate_target(context) -> bool:
        validator_calls.append(context.target_store)
        return True

    register_projection_requeue_policy(
        target_store,
        "upsert",
        ProjectionRequeuePolicy("ordinary", validate_target),
    )
    try:
        _configure_supported_ordinary_operation(
            engine,
            target_store=target_store,
            operation_name="upsert",
        )
        with Session(engine) as session:
            document = session.get(Document, "doc-a")
            assert document is not None
            document.mutation_generation = 5
            session.commit()
        ref = consistency_api._resource_ref(
            RAW_DEAD_LETTER_A,
            report_secret=REPORT_SECRET,
            kind="dead-letter",
        )
        before = _counts(engine)

        response = client.post(
            f"/api/knowledge-bases/dataset-a/consistency/dead-letters/{ref}/requeue",
            headers=_headers(settings, "owner-a"),
            json={},
        )

        assert response.status_code == 409
        assert (
            response.json()["error"]["code"] == "knowledge_consistency_dead_letter_lineage_conflict"
        )
        assert validator_calls == []
        assert _counts(engine) == before
    finally:
        unregister_projection_requeue_policy(target_store, "upsert")


def test_custom_projection_validator_can_only_reject_and_never_enqueue_on_failure(api) -> None:
    from core.projection_target_contract import (
        ProjectionRequeuePolicy,
        register_projection_requeue_policy,
        unregister_projection_requeue_policy,
    )

    client, engine, settings, _ = api
    target_store = "custom_vector"
    validator_calls: list[tuple[str, str]] = []

    def reject_target(context) -> bool:
        validator_calls.append((context.target_store, context.operation))
        return False

    register_projection_requeue_policy(
        target_store,
        "upsert",
        ProjectionRequeuePolicy("ordinary", reject_target),
    )
    try:
        _configure_supported_ordinary_operation(
            engine,
            target_store=target_store,
            operation_name="upsert",
        )
        ref = consistency_api._resource_ref(
            RAW_DEAD_LETTER_A,
            report_secret=REPORT_SECRET,
            kind="dead-letter",
        )
        before = _counts(engine)

        response = client.post(
            f"/api/knowledge-bases/dataset-a/consistency/dead-letters/{ref}/requeue",
            headers=_headers(settings, "owner-a"),
            json={},
        )

        assert response.status_code == 409
        assert (
            response.json()["error"]["code"] == "knowledge_consistency_dead_letter_lineage_conflict"
        )
        assert validator_calls == [(target_store, "upsert")]
        assert _counts(engine) == before
        with Session(engine) as session:
            letter = session.get(IndexDeadLetter, RAW_DEAD_LETTER_A)
        assert letter is not None
        assert letter.requeued_to_operation_id is None
    finally:
        unregister_projection_requeue_policy(target_store, "upsert")


def test_requeue_rejects_invalid_target_operation_pair(api) -> None:
    client, engine, settings, _ = api
    with Session(engine) as session:
        original = session.get(IndexOperation, "op-a-dead")
        original.target_store = "milvus_chunks"
        original.operation = "finalize_document_delete"
        session.commit()
    ref = consistency_api._resource_ref(
        RAW_DEAD_LETTER_A, report_secret=REPORT_SECRET, kind="dead-letter"
    )

    response = client.post(
        f"/api/knowledge-bases/dataset-a/consistency/dead-letters/{ref}/requeue",
        headers=_headers(settings, "owner-a"),
        json={},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == ("knowledge_consistency_dead_letter_target_conflict")


def test_delete_requeue_rejects_reconcile_payload_and_requires_chunk_mutation_semantics(
    api,
) -> None:
    client, engine, settings, _ = api
    with Session(engine) as session:
        original = session.get(IndexOperation, "op-a-dead")
        attempt = session.get(DocumentIngestAttempt, "attempt-a")
        original.target_store = "milvus_chunks"
        original.operation = "delete"
        original.payload = {"document_id": "doc-a", "target_revision": 2}
        attempt.attempt_kind = "chunk_mutation"
        session.commit()
    ref = consistency_api._resource_ref(
        RAW_DEAD_LETTER_A, report_secret=REPORT_SECRET, kind="dead-letter"
    )

    response = client.post(
        f"/api/knowledge-bases/dataset-a/consistency/dead-letters/{ref}/requeue",
        headers=_headers(settings, "owner-a"),
        json={},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == (
        "knowledge_consistency_dead_letter_lineage_conflict"
    )


def test_upsert_requeue_rejects_legacy_payload_missing_generation_fences(api) -> None:
    client, engine, settings, _ = api
    with Session(engine) as session:
        original = session.get(IndexOperation, "op-a-dead")
        original.target_store = "milvus_chunks"
        original.operation = "upsert"
        original.payload = {"chunk_ids": ["chunk-a"]}
        session.commit()
    ref = consistency_api._resource_ref(
        RAW_DEAD_LETTER_A, report_secret=REPORT_SECRET, kind="dead-letter"
    )

    response = client.post(
        f"/api/knowledge-bases/dataset-a/consistency/dead-letters/{ref}/requeue",
        headers=_headers(settings, "owner-a"),
        json={},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == (
        "knowledge_consistency_dead_letter_lineage_conflict"
    )


def test_reconcile_requeue_accepts_direct_payload_with_chunk_mutation_attempt(api) -> None:
    client, engine, settings, _ = api
    with Session(engine) as session:
        original = session.get(IndexOperation, "op-a-dead")
        attempt = session.get(DocumentIngestAttempt, "attempt-a")
        original.target_store = "milvus_chunks"
        original.operation = "reconcile"
        original.payload = {"document_id": "doc-a", "target_revision": 2}
        attempt.attempt_kind = "chunk_mutation"
        session.commit()
    ref = consistency_api._resource_ref(
        RAW_DEAD_LETTER_A, report_secret=REPORT_SECRET, kind="dead-letter"
    )

    response = client.post(
        f"/api/knowledge-bases/dataset-a/consistency/dead-letters/{ref}/requeue",
        headers=_headers(settings, "owner-a"),
        json={},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "enqueued"


def test_reconcile_requeue_rejects_chunk_mutation_payload(api) -> None:
    client, engine, settings, _ = api
    with Session(engine) as session:
        original = session.get(IndexOperation, "op-a-dead")
        attempt = session.get(DocumentIngestAttempt, "attempt-a")
        original.target_store = "milvus_chunks"
        original.operation = "reconcile"
        original.payload = {
            "chunk_id": "chunk-a",
            "chunk_ids": ["chunk-a"],
            "expected_content_revision": 1,
            "mutation": "delete",
            "document_generation": 4,
            "dataset_generation": 0,
        }
        attempt.attempt_kind = "chunk_mutation"
        session.commit()
    ref = consistency_api._resource_ref(
        RAW_DEAD_LETTER_A, report_secret=REPORT_SECRET, kind="dead-letter"
    )

    response = client.post(
        f"/api/knowledge-bases/dataset-a/consistency/dead-letters/{ref}/requeue",
        headers=_headers(settings, "owner-a"),
        json={},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == (
        "knowledge_consistency_dead_letter_lineage_conflict"
    )


def test_upsert_requeue_accepts_canonical_chunk_edit_payload(api) -> None:
    client, engine, settings, _ = api
    catalog = ChunkCatalog(engine)
    head = catalog.upsert_head(
        chunk_id="chunk-edit",
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        document_id="doc-a",
        parent_chunk_id=None,
        chunk_index=0,
        chunk_role="flat",
        document_revision=2,
        source_content="before",
        content="after",
        enabled=True,
    )
    with Session(engine) as session:
        original = session.get(IndexOperation, "op-a-dead")
        attempt = session.get(DocumentIngestAttempt, "attempt-a")
        original.target_store = "graph_projection"
        original.operation = "upsert"
        original.payload = {
            "chunk_id": head.id,
            "chunk_ids": [head.id],
            "expected_content_revision": head.content_revision,
            "mutation": "edit",
            "document_generation": 4,
            "dataset_generation": 0,
        }
        attempt.attempt_kind = "chunk_mutation"
        session.commit()
    ref = consistency_api._resource_ref(
        RAW_DEAD_LETTER_A, report_secret=REPORT_SECRET, kind="dead-letter"
    )

    response = client.post(
        f"/api/knowledge-bases/dataset-a/consistency/dead-letters/{ref}/requeue",
        headers=_headers(settings, "owner-a"),
        json={},
    )

    assert response.status_code == 202


def test_repeat_requeue_preserves_historical_idempotency_after_generation_advances(api) -> None:
    client, engine, settings, _ = api
    listed = client.get(
        "/api/knowledge-bases/dataset-a/consistency/dead-letters",
        headers=_headers(settings, "owner-a"),
    )
    ref = listed.json()["items"][0]["dead_letter_ref"]
    path = f"/api/knowledge-bases/dataset-a/consistency/dead-letters/{ref}/requeue"
    first = client.post(path, headers=_headers(settings, "owner-a"), json={})
    assert first.status_code == 202
    with Session(engine) as session:
        document = session.get(Document, "doc-a")
        document.mutation_generation = 5
        session.commit()

    repeated = client.post(path, headers=_headers(settings, "owner-a"), json={})

    assert repeated.status_code == 202
    assert repeated.json()["status"] == "already_requeued"
    assert repeated.json()["operation_ref"] == first.json()["operation_ref"]
