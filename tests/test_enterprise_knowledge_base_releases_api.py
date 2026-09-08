from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.knowledge_auth import KnowledgeActor


class RecordingReleaseService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, dict[str, Any]]] = []

    def __getattr__(self, name: str):
        allowed = {
            "list_release_channels",
            "create_release_channel",
            "update_release_channel",
            "list_releases",
            "capture_release_candidate",
            "get_release",
            "get_release_readiness",
            "get_release_impact",
            "get_release_audit",
            "list_channel_bindings",
            "promote_release",
            "rollback_channel_release",
            "update_app_release_binding",
        }
        if name not in allowed:
            raise AttributeError(name)

        def operation(engine: Any, **kwargs: Any) -> dict[str, Any]:
            self.calls.append((name, engine, kwargs))
            if name.startswith("list_"):
                return {"items": [], "count": 0, "next_cursor": None}
            if name == "capture_release_candidate":
                return {"release": {"id": "release-1"}, "state": "candidate_created"}
            return {"state": "applied", "resource_id": kwargs.get("release_id")}

        return operation


def actor() -> KnowledgeActor:
    return KnowledgeActor(
        account_id="owner-a",
        tenant_id="tenant-a",
        role="owner",
        request_id="request-api",
        request_ip="127.0.0.1",
    )


def client_and_service() -> tuple[TestClient, RecordingReleaseService]:
    from server.enterprise_knowledge_base_releases_api import (
        build_enterprise_knowledge_base_releases_router,
    )

    service = RecordingReleaseService()
    app = FastAPI()
    app.include_router(
        build_enterprise_knowledge_base_releases_router(
            read_engine_provider=lambda: "read-engine",
            mutation_engine_provider=lambda: "mutation-engine",
            service=service,
            actor_dependency=actor,
        )
    )
    return TestClient(app), service


def test_release_router_forwards_tenant_scoped_keyset_reads() -> None:
    client, service = client_and_service()

    responses = [
        client.get("/api/enterprise/release-channels", params={"cursor": "channel-c", "limit": 30}),
        client.get(
            "/api/enterprise/knowledge-bases/dataset-a/releases",
            params={"cursor": "release-c", "limit": 40},
        ),
        client.get("/api/enterprise/knowledge-bases/dataset-a/releases/release-1"),
        client.get("/api/enterprise/knowledge-bases/dataset-a/releases/release-1/readiness"),
        client.get("/api/enterprise/knowledge-bases/dataset-a/releases/release-1/impact"),
        client.get("/api/enterprise/knowledge-bases/dataset-a/releases/release-1/audit"),
        client.get("/api/enterprise/knowledge-bases/dataset-a/release-channels"),
    ]

    assert [response.status_code for response in responses] == [200] * 7
    assert service.calls == [
        (
            "list_release_channels",
            "read-engine",
            {
                "tenant_id": "tenant-a",
                "actor_id": "owner-a",
                "cursor": "channel-c",
                "limit": 30,
            },
        ),
        (
            "list_releases",
            "read-engine",
            {
                "tenant_id": "tenant-a",
                "actor_id": "owner-a",
                "dataset_id": "dataset-a",
                "cursor": "release-c",
                "limit": 40,
            },
        ),
        (
            "get_release",
            "read-engine",
            {
                "tenant_id": "tenant-a",
                "actor_id": "owner-a",
                "dataset_id": "dataset-a",
                "release_id": "release-1",
            },
        ),
        (
            "get_release_readiness",
            "read-engine",
            {
                "tenant_id": "tenant-a",
                "actor_id": "owner-a",
                "dataset_id": "dataset-a",
                "release_id": "release-1",
            },
        ),
        (
            "get_release_impact",
            "read-engine",
            {
                "tenant_id": "tenant-a",
                "actor_id": "owner-a",
                "dataset_id": "dataset-a",
                "release_id": "release-1",
            },
        ),
        (
            "get_release_audit",
            "read-engine",
            {
                "tenant_id": "tenant-a",
                "actor_id": "owner-a",
                "dataset_id": "dataset-a",
                "release_id": "release-1",
            },
        ),
        (
            "list_channel_bindings",
            "read-engine",
            {"tenant_id": "tenant-a", "actor_id": "owner-a", "dataset_id": "dataset-a"},
        ),
    ]


def test_release_router_forwards_capture_and_channel_mutations_with_exact_fences() -> None:
    client, service = client_and_service()
    headers = {"Idempotency-Key": "release-stable-key"}

    created_channel = client.post(
        "/api/enterprise/release-channels",
        headers=headers,
        json={
            "code": "uat-cn",
            "name": "UAT China",
            "risk_tier": "medium",
            "promotion_order": 25,
            "is_default_serving": False,
            "reason": "create release lane",
        },
    )
    captured = client.post(
        "/api/enterprise/knowledge-bases/dataset-a/releases",
        headers=headers,
        json={
            "expected_profile_revision": 4,
            "expected_ownership_revision": 5,
            "expected_workspace_revision": 2,
            "expected_mutation_generation": 9,
            "expected_serving_generation": 3,
            "reason": "capture candidate",
        },
    )

    assert created_channel.status_code == 201
    assert captured.status_code == 201
    assert service.calls[0] == (
        "create_release_channel",
        "mutation-engine",
        {
            "tenant_id": "tenant-a",
            "actor_id": "owner-a",
            "code": "uat-cn",
            "name": "UAT China",
            "risk_tier": "medium",
            "promotion_order": 25,
            "is_default_serving": False,
            "reason": "create release lane",
            "request_id": "request-api",
            "request_ip": "127.0.0.1",
            "idempotency_key": "release-stable-key",
        },
    )
    assert service.calls[1] == (
        "capture_release_candidate",
        "mutation-engine",
        {
            "tenant_id": "tenant-a",
            "actor_id": "owner-a",
            "dataset_id": "dataset-a",
            "expected_profile_revision": 4,
            "expected_ownership_revision": 5,
            "expected_workspace_revision": 2,
            "expected_mutation_generation": 9,
            "expected_serving_generation": 3,
            "reason": "capture candidate",
            "request_id": "request-api",
            "request_ip": "127.0.0.1",
            "idempotency_key": "release-stable-key",
        },
    )


def test_release_router_forwards_promotion_rollback_and_app_binding_xor() -> None:
    client, service = client_and_service()
    headers = {"Idempotency-Key": "release-mutation-key"}

    promoted = client.post(
        "/api/enterprise/knowledge-bases/dataset-a/releases/release-1/promote",
        headers=headers,
        json={
            "channel_id": "channel-production",
            "expected_channel_revision": 2,
            "expected_profile_revision": 4,
            "expected_ownership_revision": 5,
            "expected_workspace_revision": 2,
            "expected_serving_generation": 3,
            "reason": "promote release",
        },
    )
    rolled_back = client.post(
        "/api/enterprise/knowledge-bases/dataset-a/channels/channel-production/rollback",
        headers=headers,
        json={
            "target_release_id": "release-0",
            "expected_channel_revision": 3,
            "expected_serving_generation": 4,
            "reason": "rollback release",
        },
    )
    pinned = client.patch(
        "/api/enterprise/apps/app-a/knowledge-bases/dataset-a/release-binding",
        headers=headers,
        json={
            "expected_reference_revision": 7,
            "release_mode": "pinned",
            "pinned_release_id": "release-1",
            "reason": "pin approved release",
        },
    )
    invalid = client.patch(
        "/api/enterprise/apps/app-a/knowledge-bases/dataset-a/release-binding",
        headers=headers,
        json={
            "expected_reference_revision": 7,
            "release_mode": "follow_channel",
            "release_channel_id": "channel-testing",
            "pinned_release_id": "release-1",
            "reason": "invalid mixed authority",
        },
    )

    assert [promoted.status_code, rolled_back.status_code, pinned.status_code] == [200, 200, 200]
    assert invalid.status_code == 422
    assert [call[0] for call in service.calls] == [
        "promote_release",
        "rollback_channel_release",
        "update_app_release_binding",
    ]
    assert service.calls[0][2]["expected_workspace_revision"] == 2
    assert service.calls[1][2]["target_release_id"] == "release-0"
    assert service.calls[2][2] == {
        "tenant_id": "tenant-a",
        "actor_id": "owner-a",
        "app_id": "app-a",
        "dataset_id": "dataset-a",
        "expected_reference_revision": 7,
        "release_mode": "pinned",
        "release_channel_id": None,
        "pinned_release_id": "release-1",
        "reason": "pin approved release",
        "request_id": "request-api",
        "request_ip": "127.0.0.1",
        "idempotency_key": "release-mutation-key",
    }


def test_release_router_rejects_extra_fields_and_non_exact_revisions() -> None:
    client, _service = client_and_service()
    response = client.post(
        "/api/enterprise/knowledge-bases/dataset-a/releases",
        headers={"Idempotency-Key": "release-invalid"},
        json={
            "expected_profile_revision": True,
            "expected_ownership_revision": 5,
            "expected_workspace_revision": 2,
            "expected_mutation_generation": 9,
            "expected_serving_generation": 3,
            "reason": "invalid",
            "unexpected": "forbidden",
        },
    )
    assert response.status_code == 422


def test_main_app_mounts_release_routes_without_initializing_a_writable_engine() -> None:
    from server.app import app

    paths: set[str] = set()

    def collect(routes: Any) -> None:
        for route in routes:
            path = getattr(route, "path", None)
            if isinstance(path, str):
                paths.add(path)
            nested = getattr(route, "routes", None)
            if nested is None:
                nested = getattr(getattr(route, "original_router", None), "routes", None)
            if nested is not None:
                collect(nested)

    collect(app.routes)
    assert "/api/enterprise/release-channels" in paths
    assert "/api/enterprise/knowledge-bases/{dataset_id}/releases" in paths
    assert "/api/enterprise/knowledge-bases/{dataset_id}/releases/{release_id}/promote" in paths
    assert "/api/enterprise/apps/{app_id}/knowledge-bases/{dataset_id}/release-binding" in paths
