from __future__ import annotations

from dataclasses import dataclass
import importlib
from typing import Any, Callable

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from server.knowledge_auth import KnowledgeActor


@dataclass
class ServiceResult:
    status: int
    body: Any


class ServiceFailure(RuntimeError):
    def __init__(self, status: Any, code: Any, message: Any) -> None:
        super().__init__(str(message))
        self.status = status
        self.code = code
        self.message = message


class RecordingService:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[tuple[str, Any, dict[str, Any]]] = []
        self.failure: Exception | None = None
        self.next_result: Any = None

    def __getattr__(self, operation: str) -> Callable[..., Any]:
        def call(engine: Any, **kwargs: Any) -> Any:
            self.calls.append((operation, engine, kwargs))
            if self.failure is not None:
                raise self.failure
            if self.next_result is not None:
                result = self.next_result
                self.next_result = None
                return result
            if operation == "get_notification_summary":
                return {
                    "state": "ready",
                    "tenant_id": kwargs["tenant_id"],
                    "account_id": kwargs["actor_id"],
                    "unread_count": 0,
                    "unread_state": "zero",
                    "as_of": "2026-08-29T00:00:00.000000Z",
                    "reason_code": None,
                }
            if operation in {"list_notifications", "list_notification_subscriptions"}:
                return {"items": [], "next_cursor": None, "invalid_item_count": 0}
            if operation == "get_notification":
                return {
                    "notification": {
                        "id": kwargs["notification_id"],
                        "tenant_id": kwargs["tenant_id"],
                    },
                    "recipient": {
                        "id": "recipient-a",
                        "tenant_id": kwargs["tenant_id"],
                        "notification_id": kwargs["notification_id"],
                        "account_id": kwargs["actor_id"],
                    },
                    "receipt": {
                        "id": "receipt-a",
                        "tenant_id": kwargs["tenant_id"],
                        "notification_id": kwargs["notification_id"],
                        "account_id": kwargs["actor_id"],
                    },
                    "events": [],
                }
            return {
                "state": "applied",
                "operation": operation,
                "resource_id": kwargs.get("notification_id", kwargs.get("subscription_id")),
                "message": None,
                "retryable": False,
            }

        return call


def _actor(account_id: str = "account-a", tenant_id: str = "tenant-a") -> KnowledgeActor:
    return KnowledgeActor(
        account_id=account_id,
        tenant_id=tenant_id,
        role="member",
        request_id="request-notification-api",
        request_ip="127.0.0.1",
    )


def _client(
    *,
    actor: KnowledgeActor | None = None,
    receipts: RecordingService | None = None,
    subscriptions: RecordingService | None = None,
) -> tuple[TestClient, RecordingService, RecordingService]:
    from server.enterprise_notification_api import build_enterprise_notification_router

    receipt_service = receipts or RecordingService("receipts")
    subscription_service = subscriptions or RecordingService("subscriptions")
    app = FastAPI()
    app.include_router(
        build_enterprise_notification_router(
            read_engine_provider=lambda: "read-engine",
            mutation_engine_provider=lambda: "mutation-engine",
            receipt_service=receipt_service,
            subscription_service=subscription_service,
            actor_dependency=lambda: actor or _actor(),
        )
    )
    return TestClient(app), receipt_service, subscription_service


def _receipt_body() -> dict[str, Any]:
    return {"expected_revision": 1, "reason": "operator reviewed the notification"}


def _bulk_body(count: int = 1) -> dict[str, Any]:
    return {
        "items": [
            {"notification_id": f"notification-{index}", "expected_revision": 1}
            for index in range(count)
        ],
        "reason": "operator reviewed the selected notifications",
    }


def _subscription_body() -> dict[str, Any]:
    return {
        "expected_revision": 1,
        "preference": "muted",
        "minimum_severity": "warning",
        "muted_until": "2026-08-30T00:00:00Z",
        "reason": "temporarily reduce optional quality noise",
    }


def test_request_models_forbid_extra_fields_and_coercion() -> None:
    from server.enterprise_notification_api import (
        BulkReadItem,
        BulkReadRequest,
        ReceiptMutationRequest,
        SubscriptionPatchRequest,
    )

    with pytest.raises(ValidationError):
        ReceiptMutationRequest(**{**_receipt_body(), "account_id": "other-account"})
    with pytest.raises(ValidationError):
        ReceiptMutationRequest(expected_revision=True, reason="review")
    with pytest.raises(ValidationError):
        BulkReadItem(notification_id="notification-a", expected_revision=False)
    with pytest.raises(ValidationError):
        BulkReadRequest(**{**_bulk_body(), "unexpected": "reject"})
    with pytest.raises(ValidationError):
        BulkReadRequest(items=[], reason="review")
    with pytest.raises(ValidationError):
        BulkReadRequest(**_bulk_body(201))
    with pytest.raises(ValidationError):
        BulkReadRequest(
            items=[
                {"notification_id": "notification-a", "expected_revision": 1},
                {"notification_id": "notification-a", "expected_revision": 1},
            ],
            reason="review",
        )
    with pytest.raises(ValidationError):
        SubscriptionPatchRequest(**{**_subscription_body(), "tenant_id": "other-tenant"})
    with pytest.raises(ValidationError):
        SubscriptionPatchRequest(
            expected_revision=1,
            preference="muted",
            minimum_severity="warning",
            muted_until="2026-08-30T00:00:00",
            reason="review",
        )
    with pytest.raises(ValidationError):
        SubscriptionPatchRequest(
            expected_revision=1,
            preference="muted",
            minimum_severity="warning",
            muted_until="2026-08-30T00:00:00Z",
            reason="review",
            enabled=1,
        )


def test_all_approved_routes_use_the_correct_service_and_engine() -> None:
    client, receipts, subscriptions = _client()
    mutation_headers = {"Idempotency-Key": "notification-api-key-1"}

    assert client.get("/api/enterprise/notifications/summary").status_code == 200
    assert (
        client.get(
            "/api/enterprise/notifications",
            params={
                "cursor": "opaque-v1",
                "limit": 7,
                "status": "unread",
                "category": "quality",
                "severity": "critical",
            },
        ).status_code
        == 200
    )
    assert client.get("/api/enterprise/notifications/notification-a").status_code == 200
    assert (
        client.post(
            "/api/enterprise/notifications/notification-a/read",
            headers=mutation_headers,
            json=_receipt_body(),
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/enterprise/notifications/notification-a/unread",
            headers=mutation_headers,
            json=_receipt_body(),
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/enterprise/notifications/notification-a/archive",
            headers=mutation_headers,
            json=_receipt_body(),
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/enterprise/notifications/bulk-read",
            headers=mutation_headers,
            json=_bulk_body(2),
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/api/enterprise/notification-subscriptions",
            params={
                "cursor": "opaque-subscriptions",
                "limit": 9,
                "status": "active",
                "category": "quality",
            },
        ).status_code
        == 200
    )
    assert (
        client.patch(
            "/api/enterprise/notification-subscriptions/subscription-a",
            headers=mutation_headers,
            json=_subscription_body(),
        ).status_code
        == 200
    )

    assert [call[0] for call in receipts.calls] == [
        "get_notification_summary",
        "list_notifications",
        "get_notification",
        "mark_notification_read",
        "mark_notification_unread",
        "archive_notification",
        "bulk_mark_notifications_read",
    ]
    assert receipts.calls[0][1] == "read-engine"
    assert receipts.calls[1][2]["cursor"] == "opaque-v1"
    assert receipts.calls[1][2]["limit"] == 7
    assert receipts.calls[3][1] == "mutation-engine"
    assert receipts.calls[3][2]["account_id"] == "account-a"
    assert receipts.calls[3][2]["tenant_id"] == "tenant-a"
    assert receipts.calls[3][2]["idempotency_key"] == "notification-api-key-1"
    assert receipts.calls[6][2]["items"][0]["notification_id"] == "notification-0"
    assert [call[0] for call in subscriptions.calls] == [
        "list_notification_subscriptions",
        "update_notification_subscription",
    ]
    assert subscriptions.calls[0][1] == "read-engine"
    assert subscriptions.calls[1][1] == "mutation-engine"
    assert subscriptions.calls[1][2]["account_id"] == "account-a"
    assert subscriptions.calls[1][2]["subscription_id"] == "subscription-a"


def test_subscription_list_defaults_omitted_status_to_active() -> None:
    client, _, subscriptions = _client()

    response = client.get("/api/enterprise/notification-subscriptions?limit=50")

    assert response.status_code == 200
    assert subscriptions.calls == [
        (
            "list_notification_subscriptions",
            "read-engine",
            {
                "tenant_id": "tenant-a",
                "actor_id": "account-a",
                "cursor": None,
                "limit": 50,
                "status": "active",
                "category": None,
            },
        )
    ]


def test_receipt_and_subscription_mutations_cannot_choose_another_account() -> None:
    client, receipts, subscriptions = _client(actor=_actor("account-owner"))

    response = client.post(
        "/api/enterprise/notifications/notification-a/read",
        headers={"Idempotency-Key": "ownership-key"},
        json={**_receipt_body(), "account_id": "account-other"},
    )
    assert response.status_code == 422
    assert receipts.calls == []

    response = client.patch(
        "/api/enterprise/notification-subscriptions/subscription-a",
        headers={"Idempotency-Key": "ownership-subscription-key"},
        json={**_subscription_body(), "account_id": "account-other"},
    )
    assert response.status_code == 422
    assert subscriptions.calls == []

    response = client.post(
        "/api/enterprise/notifications/notification-a/read",
        headers={"Idempotency-Key": "ownership-key-2"},
        json=_receipt_body(),
    )
    assert response.status_code == 200
    assert receipts.calls[-1][2]["account_id"] == "account-owner"


def test_every_mutation_requires_a_non_blank_idempotency_key() -> None:
    client, receipts, subscriptions = _client()
    mutation_requests = (
        ("/api/enterprise/notifications/notification-a/read", _receipt_body()),
        ("/api/enterprise/notifications/notification-a/unread", _receipt_body()),
        ("/api/enterprise/notifications/notification-a/archive", _receipt_body()),
        ("/api/enterprise/notifications/bulk-read", _bulk_body()),
    )
    for path, body in mutation_requests:
        assert client.post(path, json=body).status_code == 422
    assert (
        client.patch(
            "/api/enterprise/notification-subscriptions/subscription-a", json=_subscription_body()
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/enterprise/notifications/notification-a/read",
            headers={"Idempotency-Key": "   "},
            json=_receipt_body(),
        ).status_code
        == 422
    )
    assert receipts.calls == []
    assert subscriptions.calls == []


def test_bulk_read_has_a_hard_server_side_limit_of_200() -> None:
    client, receipts, _ = _client()

    response = client.post(
        "/api/enterprise/notifications/bulk-read",
        headers={"Idempotency-Key": "bulk-limit-key"},
        json=_bulk_body(201),
    )
    assert response.status_code == 422
    assert receipts.calls == []


def test_cursor_limit_and_filter_validation_is_strict() -> None:
    client, receipts, subscriptions = _client()

    invalid_lists = (
        {"limit": 0},
        {"limit": 201},
        {"limit": "1.5"},
        {"cursor": ""},
        {"status": "pending"},
        {"category": "security"},
        {"severity": "urgent"},
    )
    for params in invalid_lists:
        assert client.get("/api/enterprise/notifications", params=params).status_code == 422
    invalid_subscriptions = (
        {"limit": 0},
        {"limit": 201},
        {"cursor": ""},
        {"status": "deleted"},
        {"category": "security"},
    )
    for params in invalid_subscriptions:
        assert (
            client.get("/api/enterprise/notification-subscriptions", params=params).status_code
            == 422
        )
    assert receipts.calls == []
    assert subscriptions.calls == []


def test_safe_service_error_preserves_only_valid_status_code_and_message() -> None:
    client, receipts, _ = _client()
    receipts.failure = ServiceFailure(
        409, "notification_revision_conflict", "notification revision is stale"
    )

    response = client.post(
        "/api/enterprise/notifications/notification-a/read",
        headers={"Idempotency-Key": "error-key"},
        json=_receipt_body(),
    )
    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "code": "notification_revision_conflict",
            "message": "notification revision is stale",
        }
    }

    receipts.failure = ServiceFailure(200, "bad\ncode", "token=do-not-leak")
    response = client.get("/api/enterprise/notifications/summary")
    assert response.status_code == 503
    payload = response.json()
    assert payload["detail"]["code"] == "enterprise_notification_center_unavailable"
    assert "do-not-leak" not in response.text
    assert "token" not in response.text


@pytest.mark.parametrize(
    "message",
    [
        "Bearer do-not-leak",
        "authorization do-not-leak",
        "failed at postgresql://user:password@internal/db",
        "request failed: https://internal.example/notification",
    ],
)
def test_service_errors_reject_tokens_and_internal_urls_without_separator_assumptions(
    message: str,
) -> None:
    client, receipts, _ = _client()
    receipts.failure = ServiceFailure(409, "notification_revision_conflict", message)

    response = client.get("/api/enterprise/notifications/summary")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "enterprise_notification_center_unavailable"
    assert message not in response.text


def test_service_provider_missing_tenant_contract_fails_closed_without_invocation() -> None:
    class MissingTenantService:
        called = False

        def get_notification_summary(self, engine: Any, *, actor_id: str) -> dict[str, Any]:
            del engine, actor_id
            self.called = True
            return {
                "state": "ready",
                "tenant_id": "tenant-a",
                "account_id": "account-a",
                "unread_count": 0,
                "unread_state": "zero",
                "as_of": "2026-08-29T00:00:00.000000Z",
                "reason_code": None,
            }

    service = MissingTenantService()
    client, _, _ = _client(receipts=service)  # type: ignore[arg-type]

    response = client.get("/api/enterprise/notifications/summary")

    assert response.status_code == 503
    assert service.called is False


def test_service_http_exception_is_safely_projected() -> None:
    client, receipts, _ = _client()
    receipts.failure = HTTPException(
        status_code=409,
        detail={
            "code": "notification_revision_conflict",
            "message": "notification revision is stale",
        },
    )
    response = client.get("/api/enterprise/notifications/summary")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "notification_revision_conflict"

    receipts.failure = HTTPException(
        status_code=500,
        detail={"code": "bad\ncode", "message": "token=do-not-leak"},
    )
    response = client.get("/api/enterprise/notifications/summary")
    assert response.status_code == 503
    assert "do-not-leak" not in response.text
    assert "token" not in response.text


def test_service_result_status_is_preserved_without_exposing_transport_objects() -> None:
    client, receipts, subscriptions = _client()
    receipts.next_result = ServiceResult(
        status=206,
        body={"items": [], "next_cursor": "opaque-next", "invalid_item_count": 0},
    )
    response = client.get("/api/enterprise/notifications")
    assert response.status_code == 206
    assert response.json()["next_cursor"] == "opaque-next"

    subscriptions.next_result = ServiceResult(
        status=202,
        body={"state": "applied", "operation": "update_notification_subscription"},
    )
    response = client.patch(
        "/api/enterprise/notification-subscriptions/subscription-a",
        headers={"Idempotency-Key": "subscription-status-key"},
        json=_subscription_body(),
    )
    assert response.status_code == 202
    assert response.json()["state"] == "applied"


def test_read_routes_never_use_mutation_engine() -> None:
    client, receipts, subscriptions = _client()

    client.get("/api/enterprise/notifications/summary")
    client.get("/api/enterprise/notifications")
    client.get("/api/enterprise/notifications/notification-a")
    client.get("/api/enterprise/notification-subscriptions")

    assert all(engine == "read-engine" for _, engine, _ in receipts.calls)
    assert all(engine == "read-engine" for _, engine, _ in subscriptions.calls)


def test_default_task4_core_modules_expose_the_server_contract_when_available() -> None:
    receipts_module = pytest.importorskip(
        "core.enterprise_notification_receipts",
        reason="Task4 receipt service is delivered in parallel",
    )
    subscriptions_module = pytest.importorskip(
        "core.enterprise_notification_subscriptions",
        reason="Task4 subscription service is delivered in parallel",
    )
    for operation in (
        "list_notifications",
        "get_notification_summary",
        "get_notification",
        "mark_notification_read",
        "mark_notification_unread",
        "archive_notification",
        "bulk_mark_notifications_read",
    ):
        assert callable(getattr(receipts_module, operation, None)), operation
    for operation in ("list_notification_subscriptions", "update_notification_subscription"):
        assert callable(getattr(subscriptions_module, operation, None)), operation


def test_main_app_mounts_all_notification_center_routes() -> None:
    from server.app import app

    paths = app.openapi()["paths"]
    routes = {
        (path, tuple(sorted(method.upper() for method in methods)))
        for path, methods in paths.items()
    }
    expected = {
        ("/api/enterprise/notifications/summary", ("GET",)),
        ("/api/enterprise/notifications", ("GET",)),
        ("/api/enterprise/notifications/{notification_id}", ("GET",)),
        ("/api/enterprise/notifications/{notification_id}/read", ("POST",)),
        ("/api/enterprise/notifications/{notification_id}/unread", ("POST",)),
        ("/api/enterprise/notifications/{notification_id}/archive", ("POST",)),
        ("/api/enterprise/notifications/bulk-read", ("POST",)),
        ("/api/enterprise/notification-subscriptions", ("GET",)),
        ("/api/enterprise/notification-subscriptions/{subscription_id}", ("PATCH",)),
    }
    assert expected <= routes


# Keep this import visible to type checkers and make accidental unused import regressions obvious.
def test_notification_api_module_can_be_imported_by_name() -> None:
    module = importlib.import_module("server.enterprise_notification_api")
    assert hasattr(module, "build_enterprise_notification_router")
