from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.enterprise_task_operations import (
    TaskOperationsAuthorityInvalid,
    canonical_task_action,
    canonical_task_action_digest,
    canonical_task_digest,
    canonical_task_event,
    canonical_task_event_digest,
    canonical_task_projection,
    canonical_task_projection_digest,
    canonical_task_reconciliation,
    canonical_task_reconciliation_digest,
    canonical_task_saved_view,
    canonical_task_saved_view_digest,
    canonical_task_source,
    canonical_task_source_digest,
    project_task_route,
    project_task_source_route,
)

UTC = timezone.utc


def task_source(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "tenant_id": "tenant-a",
        "source_kind": "source_sync",
        "source_id": "sync-a",
        "source_revision": 4,
        "source_digest": "a" * 64,
        "dataset_id": "dataset-a",
        "workspace_id": "workspace-a",
        "category": "sources",
        "status": "processing",
        "action_required": True,
        "progress_percent": 25,
        "attempt_number": 1,
        "max_attempts": 3,
        "lease_owner": "worker-a",
        "lease_until": "2026-08-29T20:30:00.123456+08:00",
        "occurred_at": "2026-08-29T20:00:00.123456+08:00",
        "started_at": "2026-08-29T19:59:59.000001Z",
        "updated_at": "2026-08-29T20:00:00.123456Z",
        "safe_facts": {"connector_id": "connector-a", "phase": "fetch"},
    }
    value.update(overrides)
    return value


def task_projection(**overrides: object) -> dict[str, object]:
    value = task_source()
    value.update(
        {
            "task_id": "task-a",
            "source_current": True,
            "finished_at": None,
        }
    )
    value.update(overrides)
    return value


def task_event(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "tenant_id": "tenant-a",
        "task_id": "task-a",
        "source_kind": "source_sync",
        "source_id": "sync-a",
        "source_revision": 4,
        "source_digest": "a" * 64,
        "sequence": 1,
        "event_type": "materialized",
        "previous_event_digest": None,
        "actor_id": "system:task-reconciler",
        "request_id": "reconcile-a",
        "safe_snapshot": {"normalized_status": "queued", "source_current": True},
        "occurred_at": "2026-08-29T12:00:00.000001Z",
    }
    value.update(overrides)
    return value


def task_action(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "tenant_id": "tenant-a",
        "action_id": "action-a",
        "task_id": "task-a",
        "source_kind": "source_sync",
        "source_id": "sync-a",
        "action": "retry",
        "action_status": "requested",
        "expected_source_revision": 4,
        "expected_source_digest": "a" * 64,
        "idempotency_key_digest": "b" * 64,
        "actor_id": "account-a",
        "request_id": "request-a",
        "safe_reason": "Retry after a transient worker failure",
        "requested_at": "2026-08-29T12:00:00.000001Z",
    }
    value.update(overrides)
    return value


def saved_view(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "tenant_id": "tenant-a",
        "account_id": "account-a",
        "view_id": "view-a",
        "name": "My failed source tasks",
        "view_status": "active",
        "filters": {
            "source_kinds": ["source_sync", "audit_export"],
            "categories": ["sources", "compliance"],
            "normalized_statuses": ["failed", "blocked"],
            "action_required": True,
            "dataset_ids": ["dataset-a"],
            "workspace_ids": ["workspace-a"],
            "occurred_from": "2026-08-01T00:00:00+08:00",
            "occurred_to": "2026-08-29T23:59:59.123456+08:00",
        },
        "created_at": "2026-08-29T12:00:00Z",
        "updated_at": "2026-08-29T12:00:00Z",
    }
    value.update(overrides)
    return value


def reconciliation(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "tenant_id": "tenant-a",
        "reconciliation_id": "reconcile-a",
        "status": "completed",
        "source_inventory_digest": "c" * 64,
        "source_counts": {
            "document_ingest": 2,
            "index_operation": 1,
            "source_sync": 3,
            "document_delete": 0,
            "audit_export": 1,
            "release_quality_scan": 2,
            "release_recertification": 1,
        },
        "created_count": 4,
        "updated_count": 3,
        "stale_count": 1,
        "invalid_count": 0,
        "started_at": "2026-08-29T12:00:00.000001Z",
        "completed_at": "2026-08-29T12:00:01.000001Z",
    }
    value.update(overrides)
    return value


def test_task_digests_are_deterministic_domain_separated_and_microsecond_utc_precise() -> None:
    first = canonical_task_source_digest(
        {
            "b": 2,
            "a": 1,
            "occurred_at": "2026-08-29T20:00:00.123456+08:00",
        }
    )
    second = canonical_task_source_digest(
        {
            "occurred_at": datetime(2026, 8, 29, 12, 0, 0, 123456, tzinfo=UTC),
            "a": 1,
            "b": 2,
        }
    )
    assert first == second
    assert first != canonical_task_projection_digest({"a": 1, "b": 2})
    assert first != canonical_task_digest("task-event", {"a": 1, "b": 2})
    assert len(canonical_task_action_digest(task_action())) == 64
    assert len(canonical_task_event_digest(task_event())) == 64
    assert len(canonical_task_saved_view_digest(saved_view())) == 64
    assert len(canonical_task_reconciliation_digest(reconciliation())) == 64


def test_source_projection_normalizes_all_seven_source_kinds_and_statuses() -> None:
    expected_categories = {
        "document_ingest": "documents",
        "index_operation": "indexing",
        "source_sync": "sources",
        "document_delete": "documents",
        "audit_export": "compliance",
        "release_quality_scan": "quality",
        "release_recertification": "quality",
    }
    for source_kind, category in expected_categories.items():
        projected = canonical_task_source(
            task_source(
                source_kind=source_kind,
                source_id=f"{source_kind}-a",
                category=category,
                status="processing",
            )
        )
        assert projected["source_kind"] == source_kind
        assert projected["category"] == category
        assert projected["normalized_status"] == "running"
        assert projected["occurred_at"] == "2026-08-29T12:00:00.123456Z"


def test_source_projection_rejects_unknown_kinds_statuses_and_non_exact_scalars() -> None:
    with pytest.raises(TaskOperationsAuthorityInvalid, match="source_kind"):
        canonical_task_source(task_source(source_kind="query_run"))
    with pytest.raises(TaskOperationsAuthorityInvalid, match="status"):
        canonical_task_source(task_source(status="notice"))
    with pytest.raises(TaskOperationsAuthorityInvalid, match="exact integer"):
        canonical_task_source(task_source(source_revision=True))
    with pytest.raises(TaskOperationsAuthorityInvalid, match="exact boolean"):
        canonical_task_source(task_source(action_required=1))
    with pytest.raises(TaskOperationsAuthorityInvalid, match="progress"):
        canonical_task_source(task_source(progress_percent=101))


def test_projection_digest_validates_currentness_error_pair_and_projection_identity() -> None:
    projected = canonical_task_projection(task_projection())
    assert projected["task_id"] == "task-a"
    assert projected["source_current"] is True
    assert projected["normalized_status"] == "running"
    assert len(projected["projection_digest"]) == 64
    assert canonical_task_projection(
        {**task_projection(), "projection_digest": projected["projection_digest"]}
    )

    with pytest.raises(TaskOperationsAuthorityInvalid, match="projection_digest"):
        canonical_task_projection(task_projection(projection_digest="f" * 64))
    with pytest.raises(TaskOperationsAuthorityInvalid, match="safe error"):
        canonical_task_projection(task_projection(safe_error_code="worker_timeout"))
    with pytest.raises(TaskOperationsAuthorityInvalid, match="source_current"):
        canonical_task_projection(task_projection(source_current=1))


def test_event_builds_a_contiguous_hash_chain_and_rejects_forgery() -> None:
    first = canonical_task_event(task_event())
    second = canonical_task_event(
        task_event(
            sequence=2,
            event_type="status_changed",
            previous_event_digest=first["event_digest"],
            safe_snapshot={"normalized_status": "running", "source_current": True},
        )
    )
    assert first["previous_event_digest"] is None
    assert second["previous_event_digest"] == first["event_digest"]
    assert second["event_digest"] != first["event_digest"]

    with pytest.raises(TaskOperationsAuthorityInvalid, match="first event"):
        canonical_task_event(task_event(event_type="status_changed"))
    with pytest.raises(TaskOperationsAuthorityInvalid, match="SHA-256"):
        canonical_task_event(task_event(sequence=2, previous_event_digest="bad"))
    with pytest.raises(TaskOperationsAuthorityInvalid, match="event_digest"):
        canonical_task_event(task_event(event_digest="f" * 64))


def test_action_normalizes_allow_list_and_requires_revision_fence() -> None:
    action = canonical_task_action(task_action(action="CANCEL", action_status="pending"))
    assert action["action_type"] == "cancel"
    assert action["action_status"] == "requested"
    assert action["expected_source_revision"] == 4

    for action_type in ("retry", "cancel", "acknowledge"):
        assert canonical_task_action(task_action(action=action_type))["action_type"] == action_type

    with pytest.raises(TaskOperationsAuthorityInvalid, match="action"):
        canonical_task_action(task_action(action="delete"))
    with pytest.raises(TaskOperationsAuthorityInvalid, match="expected_source_revision"):
        canonical_task_action(task_action(expected_source_revision=True))
    with pytest.raises(TaskOperationsAuthorityInvalid, match="idempotency"):
        canonical_task_action(task_action(idempotency_key="raw-key"))


def test_saved_view_canonicalizes_bounded_filters_and_rejects_free_form_query() -> None:
    projected = canonical_task_saved_view(saved_view())
    assert projected["view_status"] == "active"
    assert projected["filters"]["source_kinds"] == ["audit_export", "source_sync"]
    assert projected["filters"]["occurred_from"] == "2026-07-31T16:00:00.000000Z"
    assert projected["filters"]["occurred_to"] == "2026-08-29T15:59:59.123456Z"
    assert len(projected["view_digest"]) == 64

    with pytest.raises(TaskOperationsAuthorityInvalid, match="query"):
        canonical_task_saved_view(saved_view(filters={"query": "status=failed"}))
    with pytest.raises(TaskOperationsAuthorityInvalid, match="time"):
        canonical_task_saved_view(
            saved_view(
                filters={
                    "occurred_from": "2026-08-30T00:00:00Z",
                    "occurred_to": "2026-08-29T00:00:00Z",
                }
            )
        )
    with pytest.raises(TaskOperationsAuthorityInvalid, match="status"):
        canonical_task_saved_view(saved_view(filters={"normalized_statuses": ["done"]}))


def test_reconciliation_normalizes_counts_and_lifecycle() -> None:
    projected = canonical_task_reconciliation(reconciliation())
    assert projected["status"] == "completed"
    assert set(projected["source_counts"]) == {
        "document_ingest",
        "index_operation",
        "source_sync",
        "document_delete",
        "audit_export",
        "release_quality_scan",
        "release_recertification",
    }
    assert projected["source_counts"]["document_delete"] == 0
    assert len(projected["reconciliation_digest"]) == 64

    with pytest.raises(TaskOperationsAuthorityInvalid, match="source_counts"):
        canonical_task_reconciliation(reconciliation(source_counts={"unknown": 1}))
    with pytest.raises(TaskOperationsAuthorityInvalid, match="exact integer"):
        canonical_task_reconciliation(reconciliation(created_count=True))
    with pytest.raises(TaskOperationsAuthorityInvalid, match="status"):
        canonical_task_reconciliation(reconciliation(status="running"))


def test_safe_source_routes_are_allow_listed_source_scoped_and_exact() -> None:
    route = project_task_source_route(
        source_kind="source_sync",
        route_code="knowledge_sources",
        params={"tenant_id": "tenant-a", "source_id": "sync-a", "run_id": "run-a"},
    )
    assert route == {
        "target_route_code": "knowledge_sources",
        "target_route_params_json": {
            "run_id": "run-a",
            "source_id": "sync-a",
            "tenant_id": "tenant-a",
        },
    }
    assert (
        project_task_route(
            "enterprise_documents",
            {"tenant_id": "tenant-a", "dataset_id": "dataset-a", "document_id": "document-a"},
        )["target_route_code"]
        == "enterprise_documents"
    )

    with pytest.raises(TaskOperationsAuthorityInvalid, match="route"):
        project_task_source_route(
            source_kind="source_sync",
            route_code="https://internal.example/tasks",
            params={"tenant_id": "tenant-a", "source_id": "sync-a"},
        )
    with pytest.raises(TaskOperationsAuthorityInvalid, match="scope"):
        project_task_source_route(
            source_kind="source_sync",
            route_code="knowledge_documents",
            params={
                "tenant_id": "tenant-a",
                "dataset_id": "dataset-a",
                "document_id": "document-a",
            },
        )
    with pytest.raises(TaskOperationsAuthorityInvalid, match="forbidden"):
        project_task_source_route(
            source_kind="source_sync",
            route_code="knowledge_sources",
            params={"tenant_id": "tenant-a", "source_id": "sync-a", "token": "secret"},
        )


@pytest.mark.parametrize(
    "unsafe",
    [
        {"raw_payload": {"private": "content"}},
        {"query": "select private records"},
        {"result_body": "raw result body"},
        {"reviewer_note": "keep this note"},
        {"ticket": "opaque-ticket-123"},
        {"token": "bearer topsecret"},
        {"credential": "secret://vault/value"},
        {"source_url": "https://example.test/private"},
    ],
)
def test_all_task_authority_boundaries_reject_unsafe_payloads(unsafe: dict[str, object]) -> None:
    for builder, base in (
        (canonical_task_source, task_source()),
        (canonical_task_projection, task_projection()),
        (canonical_task_event, task_event(safe_snapshot={})),
        (canonical_task_action, task_action()),
        (canonical_task_saved_view, saved_view()),
        (canonical_task_reconciliation, reconciliation()),
    ):
        candidate = dict(base)
        if builder is canonical_task_event:
            candidate["safe_snapshot"] = unsafe
        elif builder is canonical_task_saved_view:
            candidate["filters"] = unsafe
        else:
            candidate.update(unsafe)
        with pytest.raises(TaskOperationsAuthorityInvalid):
            builder(candidate)

    with pytest.raises(TaskOperationsAuthorityInvalid):
        canonical_task_digest("unsafe", unsafe)


def test_task_authority_has_no_orm_imports() -> None:
    tree = ast.parse(Path("core/enterprise_task_operations.py").read_text(encoding="utf-8"))
    imported_modules = {
        node.module.casefold()
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    imported_names = {
        alias.name.casefold()
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any(
        module == "sqlalchemy" or module.startswith("sqlalchemy.") for module in imported_modules
    )
    assert "orm" not in imported_names
