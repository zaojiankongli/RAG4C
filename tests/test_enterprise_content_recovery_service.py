from __future__ import annotations

from datetime import datetime, timedelta
import importlib
from pathlib import Path
from typing import Any, Callable

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.catalog_schema import inspect_enterprise_content_recovery_capability
from core.enterprise_approval_control import create_approval_policy
from models.orm import (
    Dataset,
    Document,
    TenantApprovalRequest,
    TenantContentRetentionPolicy,
    TenantDocumentLegalHold,
    TenantDocumentPurgeRequest,
    TenantDocumentRecoveryEvent,
    TenantDocumentRecycleEntry,
    TenantWorkspaceAuthorizationPolicy,
)
from test_enterprise_knowledge_base_release_snapshot import _release_engine

NOW = datetime(2026, 8, 29, 12, 0, 0)
MODULE = "core.enterprise_content_recovery_service"


def module() -> Any:
    try:
        return importlib.import_module(MODULE)
    except ModuleNotFoundError as exc:
        pytest.fail(f"Stage 23 recovery service is missing: {exc}")


def operation(name: str) -> Callable[..., Any]:
    value = getattr(module(), name, None)
    if not callable(value):
        pytest.fail(f"Stage 23 recovery service operation is missing: {name}")
    return value


def body(value: Any) -> dict[str, Any]:
    projected = getattr(value, "body", value)
    assert isinstance(projected, dict)
    return projected


def seed(tmp_path: Path, *, retention_days: int = 30) -> Any:
    engine, _ = _release_engine(tmp_path)
    with Session(engine) as session:
        session.add(
            TenantWorkspaceAuthorizationPolicy(
                id="workspace-auth-stage23",
                tenant_id="tenant-a",
                workspace_id="workspace-a",
                mode="shadow",
                permission_model_version=1,
                revision=1,
                created_at=NOW,
                created_by="owner-a",
                updated_at=NOW,
                updated_by="owner-a",
                enforced_at=None,
                enforced_by=None,
                disabled_at=None,
                disabled_by=None,
            )
        )
        session.add(
            Document(
                id="document-stage23",
                dataset_id="dataset-a",
                tenant_id="tenant-a",
                name="员工离职制度.pdf",
                file_path="/controlled/employee-policy.pdf",
                file_hash="a" * 64,
                doc_type="pdf",
                status="completed",
                progress=1.0,
                chunk_count=12,
                content_revision=3,
                desired_index_revision=3,
                indexed_revision=3,
                graph_revision=3,
                lifecycle_state="active",
                retrieval_enabled=True,
                mutation_generation=4,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            TenantContentRetentionPolicy(
                id="retention-tenant-a",
                tenant_id="tenant-a",
                status="active",
                retention_days=retention_days,
                auto_purge_enabled=False,
                purge_requires_approval=True,
                revision=1,
                created_at=NOW,
                created_by="owner-a",
                updated_at=NOW,
                updated_by="owner-a",
            )
        )
        session.commit()
    return engine


def common() -> dict[str, Any]:
    return {
        "tenant_id": "tenant-a",
        "actor_id": "owner-a",
        "account_id": "owner-a",
        "request_id": "request-stage23-service",
        "request_ip": "127.0.0.1",
    }


def test_recycle_and_restore_are_revision_fenced_atomic_and_replay_safe(tmp_path: Path) -> None:
    engine = seed(tmp_path)
    try:
        with Session(engine) as session:
            initial_serving_generation = int(session.get(Dataset, "dataset-a").serving_generation)
        recycle = operation("recycle_document")
        first = body(
            recycle(
                engine,
                **common(),
                dataset_id="dataset-a",
                document_id="document-stage23",
                expected_mutation_generation=4,
                reason="move obsolete content into governed recovery",
                idempotency_key="recycle-document-stage23",
                now=NOW,
            )
        )
        replay = body(
            recycle(
                engine,
                **common(),
                dataset_id="dataset-a",
                document_id="document-stage23",
                expected_mutation_generation=4,
                reason="move obsolete content into governed recovery",
                idempotency_key="recycle-document-stage23",
                now=NOW,
            )
        )
        assert first == replay
        assert first["state"] in {"applied", "coalesced"}
        entry_id = first["resource_id"]

        with Session(engine) as session:
            document = session.get(Document, "document-stage23")
            entry = session.get(TenantDocumentRecycleEntry, entry_id)
            assert document is not None and document.lifecycle_state == "recycled"
            assert document.retrieval_enabled is False
            assert document.mutation_generation == 5
            assert (
                int(session.get(Dataset, "dataset-a").serving_generation)
                == initial_serving_generation + 1
            )
            assert entry is not None and entry.status == "recycled" and entry.revision == 1
            assert entry.active_recycle_key == "dataset-a:document-stage23"
            assert (
                session.scalar(select(func.count()).select_from(TenantDocumentRecoveryEvent)) == 1
            )

        restored = body(
            operation("restore_document")(
                engine,
                **common(),
                entry_id=entry_id,
                expected_revision=1,
                reason="restore content after owner review",
                idempotency_key="restore-document-stage23",
                now=NOW + timedelta(hours=1),
            )
        )
        assert restored["state"] == "applied"
        with Session(engine) as session:
            document = session.get(Document, "document-stage23")
            entry = session.get(TenantDocumentRecycleEntry, entry_id)
            assert document is not None and document.lifecycle_state == "active"
            assert document.retrieval_enabled is True
            assert document.mutation_generation == 6
            assert (
                int(session.get(Dataset, "dataset-a").serving_generation)
                == initial_serving_generation + 2
            )
            assert entry is not None and entry.status == "restored" and entry.revision == 2
            assert entry.active_recycle_key is None
            events = list(
                session.scalars(
                    select(TenantDocumentRecoveryEvent).order_by(
                        TenantDocumentRecoveryEvent.sequence
                    )
                )
            )
            assert [event.event_type for event in events] == ["recycled", "restored"]
            assert events[1].previous_event_digest == events[0].event_digest
    finally:
        engine.dispose()


def test_sqlite_capability_accepts_rows_written_by_recovery_service(tmp_path: Path) -> None:
    engine = seed(tmp_path)
    try:
        recycle = body(
            operation("recycle_document")(
                engine,
                **common(),
                dataset_id="dataset-a",
                document_id="document-stage23",
                expected_mutation_generation=4,
                reason="exercise capability projection",
                idempotency_key="recycle-capability-stage23",
                now=NOW,
            )
        )
        body(
            operation("restore_document")(
                engine,
                **common(),
                entry_id=recycle["resource_id"],
                expected_revision=1,
                reason="exercise restored event projection",
                idempotency_key="restore-capability-stage23",
                now=NOW + timedelta(hours=1),
            )
        )

        state, issues = inspect_enterprise_content_recovery_capability(engine)

        assert state == "ready", issues
        assert issues == ()
    finally:
        engine.dispose()


def test_legal_hold_blocks_purge_until_released_and_purge_only_creates_approval(
    tmp_path: Path,
) -> None:
    engine = seed(tmp_path, retention_days=1)
    try:
        create_approval_policy(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            name="Document Purge Approval",
            action_type="document_purge",
            resource_scope=None,
            required_approvals=1,
            request_expiry_minutes=120,
            approvers=[{"kind": "account", "ref": "owner-a"}],
            reason="protect permanent content removal",
            idempotency_key="policy-document-purge-stage23",
            request_id="request-policy-stage23",
            request_ip="127.0.0.1",
            now=NOW,
        )
        entry_id = body(
            operation("recycle_document")(
                engine,
                **common(),
                dataset_id="dataset-a",
                document_id="document-stage23",
                expected_mutation_generation=4,
                reason="recycle before purge",
                idempotency_key="recycle-before-purge-stage23",
                now=NOW,
            )
        )["resource_id"]
        hold_id = body(
            operation("apply_legal_hold")(
                engine,
                **common(),
                entry_id=entry_id,
                expected_revision=1,
                reason_code="litigation",
                reason="active litigation hold",
                idempotency_key="hold-stage23",
                now=NOW + timedelta(minutes=1),
            )
        )["resource_id"]
        with pytest.raises(Exception, match="hold|保留|法律"):
            operation("request_document_purge")(
                engine,
                **common(),
                entry_id=entry_id,
                expected_revision=2,
                reason="retention elapsed",
                idempotency_key="purge-blocked-stage23",
                now=NOW + timedelta(days=2),
            )
        operation("release_legal_hold")(
            engine,
            **common(),
            entry_id=entry_id,
            hold_id=hold_id,
            expected_revision=1,
            reason="legal team released hold",
            idempotency_key="release-hold-stage23",
            now=NOW + timedelta(days=2),
        )
        purge = body(
            operation("request_document_purge")(
                engine,
                **common(),
                entry_id=entry_id,
                expected_revision=3,
                reason="retention elapsed and hold released",
                idempotency_key="purge-request-stage23",
                now=NOW + timedelta(days=2, minutes=1),
            )
        )
        assert purge["state"] == "applied"
        with Session(engine) as session:
            entry = session.get(TenantDocumentRecycleEntry, entry_id)
            request = session.get(TenantDocumentPurgeRequest, purge["resource_id"])
            document = session.get(Document, "document-stage23")
            hold = session.get(TenantDocumentLegalHold, hold_id)
            assert entry is not None and entry.status == "purge_requested"
            assert request is not None and request.status == "pending_approval"
            assert request.approval_request_id
            assert hold is not None and hold.status == "released"
            assert document is not None and document.lifecycle_state == "recycled"
            assert document.active_delete_operation_id is None
        operation("cancel_document_purge_request")(
            engine,
            **common(),
            entry_id=entry_id,
            purge_request_id=purge["resource_id"],
            expected_revision=1,
            reason="operator cancelled purge request",
            idempotency_key="cancel-purge-stage23",
            now=NOW + timedelta(days=2, minutes=2),
        )
        with Session(engine) as session:
            request = session.get(TenantDocumentPurgeRequest, purge["resource_id"])
            approval = session.get(TenantApprovalRequest, request.approval_request_id)
            entry = session.get(TenantDocumentRecycleEntry, entry_id)
            assert request.status == "cancelled"
            assert approval.status == "cancelled"
            assert entry.status == "recycled"
    finally:
        engine.dispose()


def test_purge_persistence_failure_cancels_orphan_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = seed(tmp_path, retention_days=1)
    try:
        create_approval_policy(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            name="Document Purge Approval",
            action_type="document_purge",
            resource_scope=None,
            required_approvals=1,
            request_expiry_minutes=120,
            approvers=[{"kind": "account", "ref": "owner-a"}],
            reason="protect purge",
            idempotency_key="policy-purge-compensation",
            request_id="policy-purge-compensation",
            request_ip="127.0.0.1",
            now=NOW,
        )
        entry_id = body(
            operation("recycle_document")(
                engine,
                **common(),
                dataset_id="dataset-a",
                document_id="document-stage23",
                expected_mutation_generation=4,
                reason="recycle for compensation",
                idempotency_key="recycle-compensation",
                now=NOW,
            )
        )["resource_id"]
        service = module()
        monkeypatch.setattr(
            service,
            "_append_event",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("forced event failure")),
        )
        with pytest.raises(RuntimeError, match="forced event failure"):
            operation("request_document_purge")(
                engine,
                **common(),
                entry_id=entry_id,
                expected_revision=1,
                reason="force compensation",
                idempotency_key="purge-compensation",
                now=NOW + timedelta(days=2),
            )
        with Session(engine) as session:
            approvals = list(
                session.scalars(
                    select(TenantApprovalRequest).where(
                        TenantApprovalRequest.tenant_id == "tenant-a",
                        TenantApprovalRequest.action_type == "document_purge",
                    )
                )
            )
            assert approvals and all(row.status == "cancelled" for row in approvals)
            assert session.scalar(select(func.count()).select_from(TenantDocumentPurgeRequest)) == 0
            assert session.get(TenantDocumentRecycleEntry, entry_id).status == "recycled"
    finally:
        engine.dispose()


def test_reads_are_tenant_scoped_and_do_not_mutate(tmp_path: Path) -> None:
    engine = seed(tmp_path)
    try:
        before: dict[str, int] = {}
        with Session(engine) as session:
            before = {
                "entries": int(
                    session.scalar(select(func.count()).select_from(TenantDocumentRecycleEntry))
                    or 0
                ),
                "events": int(
                    session.scalar(select(func.count()).select_from(TenantDocumentRecoveryEvent))
                    or 0
                ),
            }
        summary = body(
            operation("get_recovery_summary")(
                engine, tenant_id="tenant-a", actor_id="owner-a", now=NOW
            )
        )
        page = body(
            operation("list_recycle_entries")(
                engine, tenant_id="tenant-a", actor_id="owner-a", limit=50, now=NOW
            )
        )
        policy = body(
            operation("get_retention_policy")(engine, tenant_id="tenant-a", actor_id="owner-a")
        )
        assert summary["recycled_count"] == 0
        assert page["items"] == []
        assert policy["retention_days"] == 30
        with Session(engine) as session:
            after = {
                "entries": int(
                    session.scalar(select(func.count()).select_from(TenantDocumentRecycleEntry))
                    or 0
                ),
                "events": int(
                    session.scalar(select(func.count()).select_from(TenantDocumentRecoveryEvent))
                    or 0
                ),
            }
        assert after == before
        with pytest.raises(Exception, match="tenant|member|actor|scope"):
            operation("get_recovery_summary")(
                engine, tenant_id="tenant-b", actor_id="owner-a", now=NOW
            )
    finally:
        engine.dispose()


def test_restore_rejects_document_version_head_changes(tmp_path: Path) -> None:
    engine = seed(tmp_path)
    try:
        entry_id = body(
            operation("recycle_document")(
                engine,
                **common(),
                dataset_id="dataset-a",
                document_id="document-stage23",
                expected_mutation_generation=4,
                reason="recycle before version drift",
                idempotency_key="recycle-version-drift-stage23",
                now=NOW,
            )
        )["resource_id"]
        with Session(engine) as session:
            document = session.get(Document, "document-stage23")
            document.content_revision = 9
            session.commit()
        with pytest.raises(Exception, match="version|revision|changed|authority"):
            operation("restore_document")(
                engine,
                **common(),
                entry_id=entry_id,
                expected_revision=1,
                reason="must reject changed version head",
                idempotency_key="restore-version-drift-stage23",
                now=NOW + timedelta(hours=1),
            )
    finally:
        engine.dispose()


def test_dataset_acl_denial_blocks_recovery_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = seed(tmp_path)
    try:
        service = module()

        class Denied:
            effective_permissions = frozenset()

        monkeypatch.setattr(
            service, "evaluate_dataset_permissions", lambda *args, **kwargs: Denied()
        )
        with pytest.raises(Exception, match="permission|Dataset|access"):
            operation("recycle_document")(
                engine,
                **common(),
                dataset_id="dataset-a",
                document_id="document-stage23",
                expected_mutation_generation=4,
                reason="must be denied",
                idempotency_key="acl-denied-stage23",
                now=NOW,
            )
    finally:
        engine.dispose()


def test_bulk_recycle_has_parent_replay_and_explicit_partial_results(tmp_path: Path) -> None:
    engine = seed(tmp_path)
    try:
        request = {
            **common(),
            "dataset_id": "dataset-a",
            "items": [
                {"document_id": "document-stage23", "expected_mutation_generation": 4},
                {"document_id": "document-missing", "expected_mutation_generation": 0},
            ],
            "reason": "governed bulk recycle",
            "idempotency_key": "bulk-parent-stage23",
            "now": NOW,
        }
        first = body(operation("bulk_recycle_documents")(engine, **request))
        replay = body(operation("bulk_recycle_documents")(engine, **request))
        assert first == replay
        assert first["requested_count"] == 2
        assert first["applied_count"] == 1
        assert first["rejected_count"] == 1
        assert [item["state"] for item in first["items"]] == ["applied", "rejected"]
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(TenantDocumentRecycleEntry)) == 1
    finally:
        engine.dispose()


def test_bulk_recycle_is_bounded_and_atomic(tmp_path: Path) -> None:
    engine = seed(tmp_path)
    try:
        with pytest.raises(Exception, match="100|bulk|between"):
            operation("bulk_recycle_documents")(
                engine,
                **common(),
                dataset_id="dataset-a",
                items=[
                    {"document_id": f"document-{index}", "expected_mutation_generation": 0}
                    for index in range(101)
                ],
                reason="too many",
                idempotency_key="bulk-too-many-stage23",
                now=NOW,
            )
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(TenantDocumentRecycleEntry)) == 0
    finally:
        engine.dispose()
