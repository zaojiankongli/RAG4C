from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.knowledge_auth import KnowledgeActor

try:
    from server.enterprise_content_recovery_api import build_enterprise_content_recovery_router
except ModuleNotFoundError as exc:  # RED until Stage 23 API exists
    pytest.fail(f"Stage 23 recovery API is missing: {exc}")


@dataclass
class Result:
    body: dict[str, Any]
    status: int = 200


class RecordingService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, dict[str, Any]]] = []
        self.failure: Exception | None = None

    def __getattr__(self, operation: str) -> Callable[..., Result]:
        def call(engine: Any, **kwargs: Any) -> Result:
            self.calls.append((operation, engine, kwargs))
            if self.failure is not None:
                raise self.failure
            if operation == "get_recovery_summary":
                return Result(
                    {
                        "state": "ready",
                        "tenant_id": kwargs["tenant_id"],
                        "account_id": kwargs["actor_id"],
                        "recycled_count": 0,
                        "expiring_count": 0,
                        "legal_hold_count": 0,
                        "pending_purge_count": 0,
                        "as_of": "2026-08-29T12:00:00.000000Z",
                        "reason_code": None,
                    }
                )
            if operation in {
                "list_recycle_entries",
                "list_legal_holds",
                "list_purge_requests",
            }:
                return Result({"items": [], "next_cursor": None, "invalid_item_count": 0})
            if operation == "get_retention_policy":
                return Result(
                    {
                        "id": "retention-tenant-a",
                        "tenant_id": kwargs["tenant_id"],
                        "status": "active",
                        "retention_days": 30,
                        "auto_purge_enabled": False,
                        "purge_requires_approval": True,
                        "revision": 1,
                        "created_at": "2026-08-29T12:00:00.000000Z",
                        "created_by": "owner-a",
                        "updated_at": "2026-08-29T12:00:00.000000Z",
                        "updated_by": "owner-a",
                    }
                )
            return Result(
                {
                    "state": "applied",
                    "operation": operation,
                    "resource_id": kwargs.get("entry_id", kwargs.get("document_id")),
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
        request_id="request-stage23-api",
        request_ip="127.0.0.1",
    )


def client() -> tuple[TestClient, RecordingService]:
    service = RecordingService()
    app = FastAPI()
    app.include_router(
        build_enterprise_content_recovery_router(
            read_engine_provider=lambda: "read-engine",
            mutation_engine_provider=lambda: "mutation-engine",
            service=service,
            actor_dependency=lambda: actor(),
        )
    )
    return TestClient(app), service


def mutation_headers() -> dict[str, str]:
    return {"Idempotency-Key": "stage23-idempotency-key"}


def test_recovery_routes_use_read_and_mutation_engines() -> None:
    api, service = client()
    assert api.get("/api/enterprise/recovery/summary").status_code == 200
    assert api.get("/api/enterprise/recycle-bin").status_code == 200
    assert api.get("/api/enterprise/recovery/retention-policy").status_code == 200
    assert api.get("/api/enterprise/recycle-bin/entry-a/holds").status_code == 200
    assert api.get("/api/enterprise/recycle-bin/entry-a/purge-requests").status_code == 200

    assert (
        api.post(
            "/api/enterprise/recycle-bin/documents/document-a",
            headers=mutation_headers(),
            json={
                "dataset_id": "dataset-a",
                "expected_mutation_generation": 4,
                "reason": "retire obsolete content",
            },
        ).status_code
        == 200
    )
    assert (
        api.post(
            "/api/enterprise/recycle-bin/entry-a/restore",
            headers=mutation_headers(),
            json={"expected_revision": 1, "reason": "restore requested content"},
        ).status_code
        == 200
    )
    assert all(engine == "read-engine" for _, engine, _ in service.calls[:5])
    assert all(engine == "mutation-engine" for _, engine, _ in service.calls[5:])
    assert service.calls[5][2]["tenant_id"] == "tenant-a"
    assert service.calls[5][2]["actor_id"] == "owner-a"
    assert service.calls[5][2]["account_id"] == "owner-a"
    assert service.calls[5][2]["request_ip"] == "127.0.0.1"


def test_every_mutation_requires_idempotency_and_rejects_actor_override() -> None:
    api, service = client()
    response = api.post(
        "/api/enterprise/recycle-bin/documents/document-a",
        json={"dataset_id": "dataset-a", "expected_mutation_generation": 4, "reason": "recycle"},
    )
    assert response.status_code == 422
    response = api.post(
        "/api/enterprise/recycle-bin/documents/document-a",
        headers=mutation_headers(),
        json={
            "dataset_id": "dataset-a",
            "expected_mutation_generation": 4,
            "reason": "recycle",
            "account_id": "other-account",
        },
    )
    assert response.status_code == 422
    assert service.calls == []


def test_purge_request_rejects_execution_ticket_and_requires_exact_revision() -> None:
    api, service = client()
    path = "/api/enterprise/recycle-bin/entry-a/purge-requests"
    valid = {
        "expected_revision": 2,
        "approval_policy_id": "policy-document-purge",
        "reason": "retention elapsed",
    }
    assert api.post(path, headers=mutation_headers(), json=valid).status_code == 200
    assert (
        api.post(
            path,
            headers=mutation_headers(),
            json={**valid, "execution_ticket": "must-not-enter-recovery-api"},
        ).status_code
        == 422
    )
    assert (
        api.post(
            path,
            headers=mutation_headers(),
            json={**valid, "expected_revision": "2"},
        ).status_code
        == 422
    )
    assert service.calls[0][0] == "request_document_purge"


def test_hold_and_policy_contracts_are_strict() -> None:
    api, _ = client()
    assert (
        api.post(
            "/api/enterprise/recycle-bin/entry-a/holds",
            headers=mutation_headers(),
            json={"expected_revision": 1, "reason_code": "legal", "safe_reason": "litigation hold"},
        ).status_code
        == 200
    )
    assert (
        api.patch(
            "/api/enterprise/recovery/retention-policy",
            headers=mutation_headers(),
            json={
                "expected_revision": 1,
                "retention_days": 90,
                "auto_purge_enabled": False,
                "purge_requires_approval": True,
                "status": "active",
                "reason": "quarterly policy update",
            },
        ).status_code
        == 200
    )
    assert (
        api.patch(
            "/api/enterprise/recovery/retention-policy",
            headers=mutation_headers(),
            json={
                "expected_revision": 1,
                "retention_days": 0,
                "auto_purge_enabled": False,
                "purge_requires_approval": True,
                "status": "active",
                "reason": "invalid",
            },
        ).status_code
        == 422
    )


def test_bulk_recycle_is_bounded_to_100_unique_documents() -> None:
    api, service = client()
    path = "/api/enterprise/recycle-bin/documents/bulk"
    body = {
        "dataset_id": "dataset-a",
        "items": [
            {"document_id": f"document-{index}", "expected_mutation_generation": index}
            for index in range(101)
        ],
        "reason": "bulk recycle",
    }
    assert api.post(path, headers=mutation_headers(), json=body).status_code == 422
    assert service.calls == []


def test_main_app_mounts_recovery_routes() -> None:
    from server.app import app

    paths = app.openapi()["paths"]
    required = {
        "/api/enterprise/recovery/summary",
        "/api/enterprise/recycle-bin",
        "/api/enterprise/recycle-bin/{entry_id}",
        "/api/enterprise/recycle-bin/documents/{document_id}",
        "/api/enterprise/recycle-bin/{entry_id}/restore",
        "/api/enterprise/recycle-bin/{entry_id}/holds",
        "/api/enterprise/recycle-bin/{entry_id}/purge-requests",
        "/api/enterprise/recovery/retention-policy",
    }
    assert required <= set(paths)
