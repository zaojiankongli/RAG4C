from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.knowledge_auth import KnowledgeActor


class RecordingRegistryService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, dict[str, Any]]] = []

    def _record(self, name: str, engine: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, engine, kwargs))
        if name.startswith("list_"):
            return {"items": [], "count": 0, "next_cursor": None}
        return {"knowledge_base": {"id": kwargs.get("dataset_id", "dataset-a")}}

    def __getattr__(self, name: str):
        allowed = {
            "list_knowledge_bases",
            "get_knowledge_base",
            "get_knowledge_base_dependencies",
            "list_app_references",
            "create_app_reference",
            "remove_app_reference",
            "transfer_dataset_ownership",
        }
        if name not in allowed:
            raise AttributeError(name)

        def operation(engine: Any, **kwargs: Any) -> dict[str, Any]:
            return self._record(name, engine, kwargs)

        return operation


def _actor() -> KnowledgeActor:
    return KnowledgeActor(
        account_id="owner-a",
        tenant_id="tenant-a",
        role="owner",
        request_id="request-api",
        request_ip="127.0.0.1",
    )


def test_registry_router_exposes_strict_authenticated_contract_and_forwards_scope() -> None:
    from server.enterprise_knowledge_base_registry_api import (
        build_enterprise_knowledge_base_registry_router,
    )

    service = RecordingRegistryService()
    app = FastAPI()
    app.include_router(
        build_enterprise_knowledge_base_registry_router(
            read_engine_provider=lambda: "read-engine",
            mutation_engine_provider=lambda: "mutation-engine",
            service=service,
            actor_dependency=lambda: _actor(),
        )
    )
    client = TestClient(app)

    listed = client.get(
        "/api/enterprise/knowledge-bases",
        params={
            "workspace_id": "workspace-a",
            "status": "archived",
            "keyword": "ops",
            "cursor": "cursor-1",
            "limit": 25,
        },
    )
    detail = client.get("/api/enterprise/knowledge-bases/dataset-a")
    dependencies = client.get("/api/enterprise/knowledge-bases/dataset-a/dependencies")
    references = client.get("/api/enterprise/apps/app-a/knowledge-bases")

    assert [response.status_code for response in (listed, detail, dependencies, references)] == [
        200,
        200,
        200,
        200,
    ]
    assert service.calls == [
        (
            "list_knowledge_bases",
            "read-engine",
            {
                "tenant_id": "tenant-a",
                "actor_id": "owner-a",
                "workspace_id": "workspace-a",
                "status": "archived",
                "keyword": "ops",
                "cursor": "cursor-1",
                "limit": 25,
            },
        ),
        (
            "get_knowledge_base",
            "read-engine",
            {"tenant_id": "tenant-a", "actor_id": "owner-a", "dataset_id": "dataset-a"},
        ),
        (
            "get_knowledge_base_dependencies",
            "read-engine",
            {"tenant_id": "tenant-a", "actor_id": "owner-a", "dataset_id": "dataset-a"},
        ),
        (
            "list_app_references",
            "read-engine",
            {"tenant_id": "tenant-a", "actor_id": "owner-a", "app_id": "app-a"},
        ),
    ]

    invalid = client.post(
        "/api/enterprise/apps/app-a/knowledge-bases/dataset-a",
        headers={"Idempotency-Key": "reference-1"},
        json={"reason": "link", "unexpected": True},
    )
    assert invalid.status_code == 422


def test_registry_router_mounts_mutation_routes_with_revision_and_idempotency_inputs() -> None:
    from server.enterprise_knowledge_base_registry_api import (
        build_enterprise_knowledge_base_registry_router,
    )

    service = RecordingRegistryService()
    app = FastAPI()
    app.include_router(
        build_enterprise_knowledge_base_registry_router(
            read_engine_provider=lambda: "read-engine",
            mutation_engine_provider=lambda: "mutation-engine",
            service=service,
            actor_dependency=lambda: _actor(),
        )
    )
    client = TestClient(app)

    created = client.post(
        "/api/enterprise/apps/app-a/knowledge-bases/dataset-a",
        headers={"Idempotency-Key": "reference-1"},
        json={"reference_kind": "knowledge", "reason": "bind application"},
    )
    removed = client.request(
        "DELETE",
        "/api/enterprise/apps/app-a/knowledge-bases/dataset-a",
        headers={"Idempotency-Key": "reference-2"},
        json={"expected_revision": 1, "reason": "remove application"},
    )
    transferred = client.post(
        "/api/enterprise/knowledge-bases/dataset-a/workspace-transfer",
        headers={"Idempotency-Key": "transfer-1"},
        json={
            "target_workspace_id": "workspace-target",
            "expected_dataset_profile_revision": 1,
            "expected_ownership_revision": 1,
            "expected_source_workspace_revision": 1,
            "expected_target_workspace_revision": 1,
            "reason": "move ownership",
        },
    )

    assert [response.status_code for response in (created, removed, transferred)] == [201, 200, 200]
    assert [call[0] for call in service.calls] == [
        "create_app_reference",
        "remove_app_reference",
        "transfer_dataset_ownership",
    ]
    assert service.calls[0][1] == "mutation-engine"
    assert service.calls[0][2]["idempotency_key"] == "reference-1"
    assert service.calls[1][2]["expected_revision"] == 1
    assert service.calls[2][2]["expected_dataset_profile_revision"] == 1
    assert service.calls[2][2]["expected_ownership_revision"] == 1
    assert service.calls[2][2]["expected_source_workspace_revision"] == 1
    assert service.calls[2][2]["expected_target_workspace_revision"] == 1
