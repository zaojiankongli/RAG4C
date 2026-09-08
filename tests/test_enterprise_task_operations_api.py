from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.knowledge_auth import KnowledgeActor

try:
    from server.enterprise_task_operations_api import build_enterprise_task_operations_router
except ModuleNotFoundError as exc:
    pytest.fail(f"Stage 24 Task Operations API is missing: {exc}")


@dataclass
class Result:
    body: dict[str, Any]
    status: int = 200


class RecordingService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, dict[str, Any]]] = []

    def __getattr__(self, operation: str) -> Callable[..., Result]:
        def call(engine: Any, **kwargs: Any) -> Result:
            self.calls.append((operation, engine, kwargs))
            if operation == "get_task_summary":
                return Result(
                    {
                        "state": "ready",
                        "tenant_id": kwargs["tenant_id"],
                        "running_count": 0,
                        "queued_count": 0,
                        "failed_count": 0,
                        "stale_count": 0,
                        "action_required_count": 0,
                        "as_of": "2026-08-29T12:00:00.000000Z",
                        "reason_code": None,
                    }
                )
            if operation in {
                "list_tasks",
                "list_task_events",
                "list_saved_task_views",
                "list_reconciliation_runs",
            }:
                return Result({"items": [], "next_cursor": None, "invalid_item_count": 0})
            if operation == "get_task":
                return Result(
                    {
                        "task": {"id": kwargs["task_id"], "tenant_id": kwargs["tenant_id"]},
                        "events": [],
                    }
                )
            return Result(
                {
                    "state": "applied",
                    "operation": operation,
                    "resource_id": kwargs.get("task_id"),
                    "message": None,
                    "retryable": False,
                }
            )

        return call


def actor() -> KnowledgeActor:
    return KnowledgeActor(
        account_id="owner-a",
        tenant_id="tenant-a",
        role="owner",
        request_id="request-stage24",
        request_ip="127.0.0.1",
    )


def client() -> tuple[TestClient, RecordingService]:
    service = RecordingService()
    app = FastAPI()
    app.include_router(
        build_enterprise_task_operations_router(
            read_engine_provider=lambda: "read-engine",
            mutation_engine_provider=lambda: "mutation-engine",
            service=service,
            actor_dependency=lambda: actor(),
        )
    )
    return TestClient(app), service


def key() -> dict[str, str]:
    return {"Idempotency-Key": "stage24-idempotency-key"}


def test_task_routes_use_read_and_mutation_engines() -> None:
    api, service = client()
    assert api.get("/api/enterprise/tasks/summary").status_code == 200
    assert api.get("/api/enterprise/tasks?status=failed&source_kind=source_sync").status_code == 200
    assert api.get("/api/enterprise/tasks/task-a").status_code == 200
    assert api.get("/api/enterprise/tasks/task-a/events").status_code == 200
    assert api.get("/api/enterprise/task-views").status_code == 200
    assert api.get("/api/enterprise/tasks/reconciliation-runs").status_code == 200
    assert (
        api.post(
            "/api/enterprise/tasks/task-a/retry",
            headers=key(),
            json={
                "expected_source_revision": 3,
                "expected_source_digest": "a" * 64,
                "reason": "retry controlled failure",
            },
        ).status_code
        == 200
    )
    assert (
        api.post(
            "/api/enterprise/tasks/task-a/cancel",
            headers=key(),
            json={
                "expected_source_revision": 3,
                "expected_source_digest": "a" * 64,
                "reason": "cancel queued task",
            },
        ).status_code
        == 200
    )
    assert (
        api.post(
            "/api/enterprise/tasks/task-a/acknowledge",
            headers=key(),
            json={
                "expected_source_revision": 3,
                "expected_source_digest": "a" * 64,
                "reason": "acknowledge attention",
            },
        ).status_code
        == 200
    )
    assert all(engine == "read-engine" for _, engine, _ in service.calls[:6])
    assert all(engine == "mutation-engine" for _, engine, _ in service.calls[6:])
    assert service.calls[6][2]["tenant_id"] == "tenant-a"
    assert service.calls[6][2]["actor_id"] == "owner-a"


def test_mutations_require_idempotency_and_forbid_actor_or_adapter_override() -> None:
    api, service = client()
    body = {"expected_source_revision": 2, "reason": "retry"}
    assert api.post("/api/enterprise/tasks/task-a/retry", json=body).status_code == 422
    assert (
        api.post(
            "/api/enterprise/tasks/task-a/retry", headers=key(), json={**body, "actor_id": "other"}
        ).status_code
        == 422
    )
    assert (
        api.post(
            "/api/enterprise/tasks/task-a/retry",
            headers=key(),
            json={**body, "adapter": "arbitrary.module"},
        ).status_code
        == 422
    )
    assert service.calls == []


def test_reconcile_is_explicit_bounded_and_never_accepts_source_payload() -> None:
    api, service = client()
    body = {
        "source_kinds": ["document_ingest", "source_sync"],
        "dry_run": True,
        "reason": "operator preview",
    }
    assert (
        api.post("/api/enterprise/tasks/reconcile/preview", headers=key(), json=body).status_code
        == 200
    )
    assert (
        api.post(
            "/api/enterprise/tasks/reconcile", headers=key(), json={**body, "dry_run": False}
        ).status_code
        == 200
    )
    assert (
        api.post(
            "/api/enterprise/tasks/reconcile",
            headers=key(),
            json={**body, "raw_payload": {"token": "secret"}},
        ).status_code
        == 422
    )
    assert [call[0] for call in service.calls] == [
        "preview_task_reconciliation",
        "reconcile_enterprise_tasks",
    ]


def test_saved_view_filter_schema_is_strict() -> None:
    api, service = client()
    valid = {
        "name": "失败同步任务",
        "filters": {
            "source_kinds": ["source_sync"],
            "statuses": ["failed"],
            "action_required": True,
        },
        "reason": "save operations view",
    }
    assert api.post("/api/enterprise/task-views", headers=key(), json=valid).status_code == 200
    assert (
        api.post(
            "/api/enterprise/task-views",
            headers=key(),
            json={**valid, "filters": {"query": "raw backend query"}},
        ).status_code
        == 422
    )
    assert service.calls[0][0] == "create_saved_task_view"


def test_main_app_mounts_task_operations_routes() -> None:
    from server.app import app

    paths = set(app.openapi()["paths"])
    assert {
        "/api/enterprise/tasks/summary",
        "/api/enterprise/tasks",
        "/api/enterprise/tasks/{task_id}",
        "/api/enterprise/tasks/{task_id}/events",
        "/api/enterprise/tasks/{task_id}/retry",
        "/api/enterprise/tasks/{task_id}/cancel",
        "/api/enterprise/tasks/{task_id}/acknowledge",
        "/api/enterprise/task-views",
        "/api/enterprise/tasks/reconcile/preview",
        "/api/enterprise/tasks/reconcile",
    } <= paths


def test_action_requires_and_forwards_the_full_source_fence() -> None:
    api, service = client()
    missing = api.post(
        "/api/enterprise/tasks/task-a/retry",
        headers=key(),
        json={"expected_source_revision": 3, "reason": "retry controlled failure"},
    )
    assert missing.status_code == 422

    accepted = api.post(
        "/api/enterprise/tasks/task-a/retry",
        headers=key(),
        json={
            "expected_source_revision": 3,
            "expected_source_digest": "a" * 64,
            "reason": "retry controlled failure",
        },
    )
    assert accepted.status_code == 200
    _, _, kwargs = service.calls[-1]
    assert kwargs["expected_source_revision"] == 3
    assert kwargs["expected_source_digest"] == "a" * 64


def test_saved_view_patch_is_revision_fenced_and_partial() -> None:
    api, service = client()
    response = api.patch(
        "/api/enterprise/task-views/view-a",
        headers=key(),
        json={
            "expected_revision": 2,
            "status": "archived",
            "reason": "archive obsolete operator view",
        },
    )
    assert response.status_code == 200
    operation, _, kwargs = service.calls[-1]
    assert operation == "update_saved_task_view"
    assert kwargs["expected_revision"] == 2
    assert kwargs["status"] == "archived"
    assert "name" not in kwargs
    assert "filters" not in kwargs


def test_child_collection_queries_are_strict_and_forwarded() -> None:
    api, service = client()
    assert (
        api.get("/api/enterprise/tasks/task-a/events?cursor=event-cursor&limit=10").status_code
        == 200
    )
    assert (
        api.get("/api/enterprise/task-views?cursor=view-cursor&limit=20&status=active").status_code
        == 200
    )
    assert (
        api.get(
            "/api/enterprise/tasks/reconciliation-runs?cursor=run-cursor&limit=30&status=completed"
        ).status_code
        == 200
    )
    calls = {operation: kwargs for operation, _, kwargs in service.calls}
    assert calls["list_task_events"] == {
        "tenant_id": "tenant-a",
        "actor_id": "owner-a",
        "task_id": "task-a",
        "cursor": "event-cursor",
        "limit": 10,
    }
    assert calls["list_saved_task_views"] == {
        "tenant_id": "tenant-a",
        "actor_id": "owner-a",
        "cursor": "view-cursor",
        "limit": 20,
        "status": "active",
    }
    assert calls["list_reconciliation_runs"] == {
        "tenant_id": "tenant-a",
        "actor_id": "owner-a",
        "cursor": "run-cursor",
        "limit": 30,
        "status": "completed",
    }
