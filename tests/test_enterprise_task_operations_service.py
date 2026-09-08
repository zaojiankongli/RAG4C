from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
from pathlib import Path
from typing import Any, Callable

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models.orm import (
    Account,
    DocumentDeleteOperation,
    DocumentIngestAttempt,
    IndexOperation,
    Tenant,
    TenantAuditExportJob,
    SourceSyncRun,
    TenantMember,
    TenantTaskEvent,
    TenantTaskOperatorAction,
    TenantTaskProjection,
    TenantTaskReconciliationRun,
    TenantTaskSavedView,
)
from test_enterprise_knowledge_base_release_snapshot import _release_engine


MODULE = "core.enterprise_task_operations_service"
NOW = datetime(2026, 8, 29, 12, 0, 0)
UTC = timezone.utc
SOURCE_KINDS = (
    "document_ingest",
    "index_operation",
    "source_sync",
    "document_delete",
    "audit_export",
    "release_quality_scan",
    "release_recertification",
)


def module() -> Any:
    try:
        return importlib.import_module(MODULE)
    except ModuleNotFoundError as exc:
        pytest.fail(f"Stage 24 Task Operations service is missing: {exc}")


def operation(name: str) -> Callable[..., Any]:
    value = getattr(module(), name, None)
    if not callable(value):
        pytest.fail(f"Stage 24 Task Operations service operation is missing: {name}")
    return value


def body(value: Any) -> dict[str, Any]:
    projected = getattr(value, "body", value)
    assert isinstance(projected, dict)
    return projected


def digest(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _source(
    kind: str,
    source_id: str,
    *,
    tenant_id: str = "tenant-a",
    source_revision: int = 1,
    status: str = "failed",
    action_required: bool | None = None,
    dataset_id: str | None = "dataset-a",
    workspace_id: str | None = "workspace-a",
    attempt_number: int = 1,
    max_attempts: int = 3,
    safe_error_code: str | None = "WORKER_FAILED",
    safe_error: str | None = "worker failed safely",
) -> dict[str, Any]:
    occurred = NOW.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")
    if action_required is None:
        action_required = status in {"failed", "blocked", "unavailable"}
    return {
        "tenant_id": tenant_id,
        "source_kind": kind,
        "source_id": source_id,
        "source_revision": source_revision,
        "source_digest": digest(f"source:{tenant_id}:{kind}:{source_id}:{source_revision}"),
        "dataset_id": dataset_id,
        "workspace_id": workspace_id,
        "category": {
            "document_ingest": "documents",
            "index_operation": "indexing",
            "source_sync": "sources",
            "document_delete": "documents",
            "audit_export": "compliance",
            "release_quality_scan": "quality",
            "release_recertification": "quality",
        }[kind],
        "normalized_status": status,
        "action_required": action_required,
        "progress_percent": 100 if status == "succeeded" else 25,
        "attempt_number": attempt_number,
        "max_attempts": max_attempts,
        "safe_error_code": safe_error_code,
        "safe_error": safe_error,
        "occurred_at": occurred,
        "started_at": occurred,
        "finished_at": occurred if status in {"succeeded", "failed", "cancelled"} else None,
        "updated_at": occurred,
    }


def install_registry(
    monkeypatch: pytest.MonkeyPatch, sources: dict[str, list[dict[str, Any]]]
) -> list[str]:
    service = module()
    calls: list[str] = []

    def make_adapter(kind: str) -> Callable[..., list[dict[str, Any]]]:
        def adapter(*, session: Any, tenant_id: str, now: datetime) -> list[dict[str, Any]]:
            del session, now
            calls.append(kind)
            return [dict(item) for item in sources.get(kind, []) if item["tenant_id"] == tenant_id]

        return adapter

    registry = {kind: make_adapter(kind) for kind in SOURCE_KINDS}
    monkeypatch.setattr(service, "SOURCE_ADAPTER_REGISTRY", registry)
    return calls


def add_tenant_b(engine: Any) -> None:
    with Session(engine) as session:
        session.add(Tenant(id="tenant-b", name="Tenant B", plan="enterprise", status="active"))
        session.add(Account(id="owner-b", name="Owner B", email="owner-b@test"))
        session.flush()
        session.add(
            TenantMember(
                account_id="owner-b",
                tenant_id="tenant-b",
                role="owner",
                status="active",
                revision=1,
                created_at=NOW,
                updated_at=NOW,
                updated_by="owner-b",
            )
        )
        session.commit()


def seed_source_tables(tmp_path: Path) -> Any:
    engine, _ = _release_engine(tmp_path)
    with Session(engine) as session:
        session.add(
            DocumentIngestAttempt(
                id="ingest-a",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                document_id="document-a",
                attempt_no=1,
                input_content_hash="a" * 64,
                input_revision=1,
                attempt_kind="ingest",
                document_generation=2,
                state="failed",
                pending_subtasks=1,
                error_code="INGEST_FAILED",
                error_message="safe ingest failure",
                worker_id="worker-a",
                lease_until=None,
                started_at=NOW,
                finished_at=NOW + timedelta(minutes=1),
                created_at=NOW,
                updated_at=NOW + timedelta(minutes=1),
            )
        )
        session.flush()
        session.add(
            IndexOperation(
                id="index-a",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                document_id="document-a",
                attempt_id="ingest-a",
                target_store="search",
                operation="upsert",
                dedup_key="index-a-dedup",
                target_revision=1,
                document_generation=2,
                payload={},
                status="failed",
                claimed_by="",
                lease_until=None,
                retry_count=1,
                max_retries=3,
                next_retry_at=None,
                last_error_code="INDEX_FAILED",
                last_error="safe index failure",
                created_at=NOW,
                updated_at=NOW + timedelta(minutes=2),
                finished_at=NOW + timedelta(minutes=2),
            )
        )
        session.add(
            SourceSyncRun(
                id="sync-a",
                source_id="source-a",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                status="failed",
                trigger="manual",
                force_full=0,
                dry_run=0,
                source_generation=4,
                dataset_generation=9,
                execution_state="completed",
                execution_owner="",
                execution_lease_until=None,
                execution_heartbeat_at=None,
                execution_attempts=1,
                execution_last_error="safe sync failure",
                reservation_owner="",
                reservation_lease_until=None,
                fetched=10,
                ingested=8,
                skipped=1,
                removed=0,
                chunks=20,
                failed=1,
                fetch_error="safe sync failure",
                duration_ms=1200,
                started_at=NOW,
                finished_at=NOW + timedelta(minutes=3),
                created_at=NOW,
            )
        )
        session.add(
            DocumentDeleteOperation(
                id="delete-a",
                batch_id=None,
                request_index=0,
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                requested_document_id="document-a",
                document_id="document-a",
                expected_generation=2,
                delete_generation=3,
                attempt_id="ingest-a",
                origin="operator",
                status="failed",
                result_code="DELETE_FAILED",
                result_message="safe delete failure",
                chunk_manifest_count=1,
                chunk_manifest_hash="c" * 64,
                quota_chunk_count=1,
                required_store_count=1,
                completed_store_count=0,
                failed_store_count=1,
                requested_by="owner-a",
                request_id="delete-request-a",
                reason="safe delete reason",
                started_at=NOW,
                finalized_at=NOW + timedelta(minutes=4),
                created_at=NOW,
                updated_at=NOW + timedelta(minutes=4),
            )
        )
        session.add(
            TenantAuditExportJob(
                id="export-a",
                tenant_id="tenant-a",
                format="ndjson",
                status="failed",
                filters_json={"scope": "tenant"},
                revision=2,
                requested_at=NOW,
                requested_by="owner-a",
            )
        )
        session.commit()
    return engine


def common(tenant_id: str = "tenant-a", actor_id: str = "owner-a") -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "actor_id": actor_id,
        "account_id": actor_id,
        "request_id": "request-stage24-service",
        "request_ip": "127.0.0.1",
        "now": NOW,
    }


def test_source_adapter_registry_is_explicit_and_stable() -> None:
    service = module()
    assert tuple(service.SOURCE_ADAPTER_ORDER) == SOURCE_KINDS
    assert tuple(service.SOURCE_ADAPTER_REGISTRY) == SOURCE_KINDS
    assert all(callable(service.SOURCE_ADAPTER_REGISTRY[kind]) for kind in SOURCE_KINDS)
    assert set(service.ACTION_ADAPTER_REGISTRY) == {
        ("source_sync", "retry"),
        ("release_recertification", "cancel"),
        ("audit_export", "cancel"),
    }


def test_reconcile_materializes_seven_sources_replays_without_duplicate_events_or_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = seed_source_tables(tmp_path)
    try:
        sources = {kind: [_source(kind, f"{kind}-a", status="failed")] for kind in SOURCE_KINDS}
        calls = install_registry(monkeypatch, sources)
        request = {
            **common(),
            "source_kinds": list(SOURCE_KINDS),
            "dry_run": False,
            "reason": "reconcile the isolated task authority",
            "idempotency_key": "reconcile-stage24-all",
        }
        first = body(operation("reconcile_enterprise_tasks")(engine, **request))
        replay = body(operation("reconcile_enterprise_tasks")(engine, **request))
        assert first == replay
        assert first["state"] == "applied"
        assert first["resource_id"]
        assert calls == list(SOURCE_KINDS)

        with Session(engine) as session:
            projections = list(
                session.scalars(
                    select(TenantTaskProjection)
                    .where(TenantTaskProjection.tenant_id == "tenant-a")
                    .order_by(TenantTaskProjection.source_kind)
                )
            )
            events = list(
                session.scalars(
                    select(TenantTaskEvent)
                    .where(TenantTaskEvent.tenant_id == "tenant-a")
                    .order_by(TenantTaskEvent.task_id, TenantTaskEvent.sequence)
                )
            )
            runs = list(
                session.scalars(
                    select(TenantTaskReconciliationRun).where(
                        TenantTaskReconciliationRun.tenant_id == "tenant-a"
                    )
                )
            )
        assert len(projections) == 7
        assert len(events) == 7
        assert all(event.sequence == 1 and event.previous_event_digest is None for event in events)
        assert len(runs) == 1
        assert runs[0].source_count == 7
        assert runs[0].created_count == 7
        assert runs[0].updated_count == 0
        assert runs[0].stale_count == 0
        assert runs[0].invalid_count == 0
    finally:
        engine.dispose()


def test_reconciliation_source_scope_survives_process_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = seed_source_tables(tmp_path)
    try:
        install_registry(
            monkeypatch,
            {"source_sync": [_source("source_sync", "sync-persisted-scope")]},
        )
        body(
            operation("reconcile_enterprise_tasks")(
                engine,
                **common(),
                source_kinds=["source_sync"],
                dry_run=False,
                reason="persist bounded reconciliation scope",
                idempotency_key="persisted-reconciliation-scope",
            )
        )
        service = module()
        if hasattr(service, "_RECONCILIATION_SOURCE_KINDS"):
            service._RECONCILIATION_SOURCE_KINDS.clear()
        runs = body(operation("list_reconciliation_runs")(engine, **common(), limit=20))["items"]
        assert runs[0]["source_kinds"] == ["source_sync"]
    finally:
        engine.dispose()


def test_reconcile_marks_disappeared_source_stale_without_deleting_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = seed_source_tables(tmp_path)
    try:
        sources = {kind: [] for kind in SOURCE_KINDS}
        sources["source_sync"] = [_source("source_sync", "sync-a", status="failed")]
        install_registry(monkeypatch, sources)
        body(
            operation("reconcile_enterprise_tasks")(
                engine,
                **common(),
                source_kinds=["source_sync"],
                dry_run=False,
                reason="materialize source sync",
                idempotency_key="stale-stage24-first",
            )
        )
        task_id = body(
            operation("list_tasks")(engine, **common(), source_kind="source_sync", limit=20)
        )["items"][0]["id"]
        sources["source_sync"] = []
        second = body(
            operation("reconcile_enterprise_tasks")(
                engine,
                **common(),
                source_kinds=["source_sync"],
                dry_run=False,
                reason="mark missing source stale",
                idempotency_key="stale-stage24-second",
            )
        )
        assert second["state"] == "applied"
        task = body(operation("get_task")(engine, **common(), task_id=task_id))["task"]
        assert task["source_current"] is False
        assert task["normalized_status"] == "unavailable"
        assert task["action_required"] is True
        with Session(engine) as session:
            projection = session.get(TenantTaskProjection, task_id)
            events = list(
                session.scalars(
                    select(TenantTaskEvent)
                    .where(
                        TenantTaskEvent.tenant_id == "tenant-a",
                        TenantTaskEvent.task_id == task_id,
                    )
                    .order_by(TenantTaskEvent.sequence)
                )
            )
        assert projection is not None
        assert [event.event_type for event in events] == ["materialized", "source_stale"]
        assert events[1].previous_event_digest == events[0].event_digest
    finally:
        engine.dispose()


def test_revision_digest_fence_tenant_isolation_and_unsupported_actions_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = seed_source_tables(tmp_path)
    add_tenant_b(engine)
    try:
        sources = {kind: [] for kind in SOURCE_KINDS}
        sources["source_sync"] = [
            _source("source_sync", "sync-a", status="failed", source_revision=4)
        ]
        sources["document_ingest"] = [_source("document_ingest", "ingest-a", status="failed")]
        sources["source_sync"].append(
            _source(
                "source_sync",
                "sync-b",
                tenant_id="tenant-b",
                dataset_id=None,
                workspace_id=None,
                status="failed",
                source_revision=1,
            )
        )
        install_registry(monkeypatch, sources)
        body(
            operation("reconcile_enterprise_tasks")(
                engine,
                **common(),
                source_kinds=["source_sync", "document_ingest"],
                dry_run=False,
                reason="fence source tasks",
                idempotency_key="fence-stage24-reconcile",
            )
        )
        page = body(operation("list_tasks")(engine, **common(), limit=50))
        assert {item["tenant_id"] for item in page["items"]} == {"tenant-a"}
        source_task = next(item for item in page["items"] if item["source_kind"] == "source_sync")
        old_revision = source_task["source_revision"]
        old_digest = source_task["source_digest"]

        sources["source_sync"][0] = _source(
            "source_sync", "sync-a", status="running", source_revision=old_revision + 1
        )
        body(
            operation("reconcile_enterprise_tasks")(
                engine,
                **common(),
                source_kinds=["source_sync"],
                dry_run=False,
                reason="source authority advanced",
                idempotency_key="fence-stage24-reconcile-2",
            )
        )
        fenced = body(
            operation("request_task_retry")(
                engine,
                **common(),
                task_id=source_task["id"],
                expected_source_revision=old_revision,
                expected_source_digest=old_digest,
                reason="retry against stale observation",
                idempotency_key="fence-stage24-old-action",
            )
        )
        assert fenced["state"] == "conflict"

        unsupported_task = next(
            item for item in page["items"] if item["source_kind"] == "document_ingest"
        )
        unsupported = body(
            operation("request_task_cancel")(
                engine,
                **common(),
                task_id=unsupported_task["id"],
                expected_source_revision=unsupported_task["source_revision"],
                expected_source_digest=unsupported_task["source_digest"],
                reason="cancel unsupported task",
                idempotency_key="unsupported-stage24-cancel",
            )
        )
        assert unsupported["state"] == "blocked"
        tenant_b_page = body(
            operation("list_tasks")(engine, **common("tenant-b", "owner-b"), limit=50)
        )
        assert tenant_b_page["items"] == []
    finally:
        engine.dispose()


def test_preview_and_reads_are_non_mutating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = seed_source_tables(tmp_path)
    try:
        sources = {kind: [_source(kind, f"{kind}-preview")] for kind in SOURCE_KINDS}
        install_registry(monkeypatch, sources)
        before: dict[str, int] = {}
        with Session(engine) as session:
            for model, key in (
                (TenantTaskProjection, "projections"),
                (TenantTaskEvent, "events"),
                (TenantTaskReconciliationRun, "runs"),
            ):
                before[key] = int(session.scalar(select(func.count()).select_from(model)) or 0)
        preview = body(
            operation("preview_task_reconciliation")(
                engine,
                **common(),
                source_kinds=list(SOURCE_KINDS),
                dry_run=True,
                reason="preview only",
                idempotency_key="preview-stage24",
            )
        )
        assert preview["state"] == "applied"
        body(operation("get_task_summary")(engine, **common()))
        body(operation("list_tasks")(engine, **common(), limit=20))
        body(operation("list_reconciliation_runs")(engine, **common(), limit=20))
        with Session(engine) as session:
            after = {
                key: int(session.scalar(select(func.count()).select_from(model)) or 0)
                for model, key in (
                    (TenantTaskProjection, "projections"),
                    (TenantTaskEvent, "events"),
                    (TenantTaskReconciliationRun, "runs"),
                )
            }
        assert after == before
    finally:
        engine.dispose()


def test_retry_request_is_idempotent_and_acknowledge_is_local_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = seed_source_tables(tmp_path)
    try:
        sources = {kind: [] for kind in SOURCE_KINDS}
        sources["source_sync"] = [
            _source("source_sync", "sync-action", status="failed", source_revision=4)
        ]
        sources["document_ingest"] = [_source("document_ingest", "ingest-action", status="failed")]
        install_registry(monkeypatch, sources)
        body(
            operation("reconcile_enterprise_tasks")(
                engine,
                **common(),
                source_kinds=["source_sync", "document_ingest"],
                dry_run=False,
                reason="prepare safe actions",
                idempotency_key="action-stage24-reconcile",
            )
        )
        task = next(
            item
            for item in body(operation("list_tasks")(engine, **common(), limit=20))["items"]
            if item["source_id"] == "sync-action"
        )
        request = {
            **common(),
            "task_id": task["id"],
            "expected_source_revision": task["source_revision"],
            "expected_source_digest": task["source_digest"],
            "reason": "request one safe source retry",
            "idempotency_key": "action-stage24-retry",
        }
        first = body(operation("request_task_retry")(engine, **request))
        replay = body(operation("request_task_retry")(engine, **request))
        assert first == replay
        assert first["state"] == "applied"
        assert first["action_id"]
        with Session(engine) as session:
            actions = list(
                session.scalars(
                    select(TenantTaskOperatorAction).where(
                        TenantTaskOperatorAction.tenant_id == "tenant-a"
                    )
                )
            )
        assert len(actions) == 1
        assert actions[0].status == "requested"

        acknowledged = body(
            operation("acknowledge_task_attention")(
                engine,
                **common(),
                task_id=task["id"],
                expected_source_revision=task["source_revision"],
                expected_source_digest=task["source_digest"],
                reason="acknowledge this isolated attention item",
                idempotency_key="action-stage24-ack",
            )
        )
        acknowledged_replay = body(
            operation("acknowledge_task_attention")(
                engine,
                **common(),
                task_id=task["id"],
                expected_source_revision=task["source_revision"],
                expected_source_digest=task["source_digest"],
                reason="acknowledge this isolated attention item",
                idempotency_key="action-stage24-ack",
            )
        )
        assert acknowledged == acknowledged_replay
        assert acknowledged["state"] == "applied"
        updated = body(operation("get_task")(engine, **common(), task_id=task["id"]))["task"]
        assert updated["action_required"] is False
        with Session(engine) as session:
            events = list(
                session.scalars(
                    select(TenantTaskEvent)
                    .where(
                        TenantTaskEvent.tenant_id == "tenant-a",
                        TenantTaskEvent.task_id == task["id"],
                    )
                    .order_by(TenantTaskEvent.sequence)
                )
            )
        assert [event.event_type for event in events] == [
            "materialized",
            "action_requested",
            "action_requested",
            "attention_acknowledged",
        ]
        assert all(
            event.sequence == index and (index == 1 or event.previous_event_digest)
            for index, event in enumerate(events, start=1)
        )
    finally:
        engine.dispose()


def test_saved_views_are_account_scoped_revision_fenced_and_replay_safe(tmp_path: Path) -> None:
    engine = seed_source_tables(tmp_path)
    try:
        filters = {
            "source_kinds": ["source_sync"],
            "categories": ["sources"],
            "statuses": ["failed"],
            "action_required": True,
            "dataset_id": "dataset-a",
            "workspace_id": "workspace-a",
            "occurred_from": "2026-08-01T00:00:00Z",
            "occurred_to": "2026-08-29T23:59:59Z",
        }
        create = {
            **common(),
            "name": "失败同步任务",
            "filters": filters,
            "reason": "save a bounded task view",
            "idempotency_key": "view-stage24-create",
        }
        first = body(operation("create_saved_task_view")(engine, **create))
        replay = body(operation("create_saved_task_view")(engine, **create))
        assert first == replay
        assert first["state"] == "applied"
        view_id = first["resource_id"]
        listed = body(operation("list_saved_task_views")(engine, **common(), limit=20))
        assert [item["id"] for item in listed["items"]] == [view_id]
        assert listed["items"][0]["filters_json"]["source_kinds"] == ["source_sync"]

        updated = body(
            operation("update_saved_task_view")(
                engine,
                **common(),
                view_id=view_id,
                expected_revision=1,
                name="失败同步与阻塞任务",
                filters={"source_kinds": ["source_sync"], "statuses": ["failed", "blocked"]},
                status="active",
                reason="tighten bounded task view",
                idempotency_key="view-stage24-update",
            )
        )
        assert updated["state"] == "applied"
        assert updated["revision"] == 2
        with pytest.raises(Exception, match="revision|conflict|stale"):
            operation("update_saved_task_view")(
                engine,
                **common(),
                view_id=view_id,
                expected_revision=1,
                name="stale update",
                filters=filters,
                status="active",
                reason="must reject stale view revision",
                idempotency_key="view-stage24-stale",
            )
        with pytest.raises(Exception, match="query|unsupported|filter|unsafe"):
            operation("create_saved_task_view")(
                engine,
                **common(),
                name="unsafe view",
                filters={"query": "status=failed"},
                reason="must reject free form query",
                idempotency_key="view-stage24-unsafe",
            )
        with Session(engine) as session:
            row = session.get(TenantTaskSavedView, view_id)
            assert row is not None
            assert row.tenant_id == "tenant-a"
            assert row.account_id == "owner-a"
            assert row.active_view_key == "owner-a:失败同步与阻塞任务"
    finally:
        engine.dispose()


def test_reconcile_same_key_is_serialized_under_concurrency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = seed_source_tables(tmp_path)
    try:
        sources = {kind: [] for kind in SOURCE_KINDS}
        sources["source_sync"] = [_source("source_sync", "sync-concurrent", status="failed")]
        install_registry(monkeypatch, sources)

        def run() -> dict[str, Any]:
            return body(
                operation("reconcile_enterprise_tasks")(
                    engine,
                    **common(),
                    source_kinds=["source_sync"],
                    dry_run=False,
                    reason="concurrent isolated reconciliation",
                    idempotency_key="concurrent-stage24-reconcile",
                )
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: run(), range(2)))
        assert results[0] == results[1]
        with Session(engine) as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(TenantTaskReconciliationRun)
                    .where(TenantTaskReconciliationRun.tenant_id == "tenant-a")
                )
                == 1
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(TenantTaskProjection)
                    .where(TenantTaskProjection.tenant_id == "tenant-a")
                )
                == 1
            )
    finally:
        engine.dispose()
