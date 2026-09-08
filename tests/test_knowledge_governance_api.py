from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from config.settings import KnowledgeSecuritySettings, RunHistorySettings, TenantSettings
from core import catalog
from models.orm import Account, Base, Dataset, Document, KnowledgeAuditEvent, Tenant, TenantMember
from server.knowledge_auth import issue_knowledge_actor_token
from server.knowledge_governance_api import router


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        run_history=RunHistorySettings(ops_bearer_token=SecretStr("ops-secret")),
        knowledge_security=KnowledgeSecuritySettings(
            actor_signing_secret=SecretStr("governance-api-actor-secret"), actor_max_ttl_s=900
        ),
        tenant=TenantSettings(enforced=True, default_tenant="tenant-a"),
    )


def _engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
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
                Document(id="doc-a", tenant_id="tenant-a", dataset_id="dataset-a", name="Guide"),
            ]
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
    app.include_router(router)
    return TestClient(app, client=("10.0.0.2", 50000)), engine, settings


def _headers(
    settings: SimpleNamespace,
    actor: str,
    tenant: str = "tenant-a",
    request_id: str = "req-governance",
) -> dict[str, str]:
    token = issue_knowledge_actor_token(actor, tenant, 300, int(time.time()), settings=settings)
    return {
        "Authorization": f"Bearer {token}",
        "X-RAG4C-Tenant": tenant,
        "X-Request-ID": request_id,
    }


def test_folder_write_and_manage_permissions_with_scope_opaque_lookup(api) -> None:
    client, _, settings = api
    assert (
        client.get(
            "/api/knowledge-bases/dataset-a/folders", headers=_headers(settings, "member-a")
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/knowledge-bases/dataset-a/folders",
            headers=_headers(settings, "member-a"),
            json={"name": "Policies"},
        ).status_code
        == 403
    )

    created = client.post(
        "/api/knowledge-bases/dataset-a/folders",
        headers=_headers(settings, "editor-a", request_id="req-folder-create"),
        json={"name": "Policies", "description": "Approved policy", "sort_order": 5},
    )
    assert created.status_code == 201
    folder_id = created.json()["id"]

    updated = client.patch(
        f"/api/knowledge-bases/dataset-a/folders/{folder_id}",
        headers=_headers(settings, "editor-a"),
        json={"name": "Company Policies"},
    )
    assert updated.status_code == 200
    assert updated.json()["path"] == "Company Policies"

    parent = client.post(
        "/api/knowledge-bases/dataset-a/folders",
        headers=_headers(settings, "editor-a"),
        json={"name": "Engineering"},
    ).json()
    moved = client.post(
        f"/api/knowledge-bases/dataset-a/folders/{folder_id}/move",
        headers=_headers(settings, "editor-a"),
        json={"new_parent_id": parent["id"]},
    )
    assert moved.status_code == 200
    assert moved.json()["path"] == "Engineering/Company Policies"

    assert (
        client.post(
            f"/api/knowledge-bases/dataset-a/folders/{folder_id}/archive",
            headers=_headers(settings, "editor-a"),
        ).status_code
        == 403
    )
    archived = client.post(
        f"/api/knowledge-bases/dataset-a/folders/{folder_id}/archive",
        headers=_headers(settings, "owner-a"),
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"

    hidden = client.patch(
        "/api/knowledge-bases/dataset-a/folders/folder-does-not-exist",
        headers=_headers(settings, "editor-a"),
        json={"description": "x"},
    )
    assert hidden.status_code == 404
    assert hidden.json()["detail"]["code"] == "knowledge_resource_not_found"

    cross = client.get(
        "/api/knowledge-bases/dataset-a/folders",
        headers=_headers(settings, "owner-b", "tenant-b"),
    )
    assert cross.status_code == 403


def test_tags_attach_detach_merge_and_conflict_mapping(api) -> None:
    client, _, settings = api
    first = client.post(
        "/api/knowledge-bases/dataset-a/tags",
        headers=_headers(settings, "editor-a"),
        json={"name": "Security", "color": "#2563EB"},
    )
    second = client.post(
        "/api/knowledge-bases/dataset-a/tags",
        headers=_headers(settings, "editor-a"),
        json={"name": "Approved"},
    )
    assert first.status_code == second.status_code == 201

    updated = client.patch(
        f"/api/knowledge-bases/dataset-a/tags/{second.json()['id']}",
        headers=_headers(settings, "editor-a"),
        json={"description": "Approved material"},
    )
    assert updated.status_code == 200
    assert updated.json()["description"] == "Approved material"

    duplicate = client.post(
        "/api/knowledge-bases/dataset-a/tags",
        headers=_headers(settings, "editor-a"),
        json={"name": "SECURITY"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "knowledge_governance_conflict"

    attached = client.post(
        f"/api/knowledge-bases/dataset-a/documents/doc-a/tags/{first.json()['id']}",
        headers=_headers(settings, "editor-a", request_id="req-tag-attach"),
    )
    assert attached.status_code == 201
    assert attached.json()["attached"] is True

    editor_merge = client.post(
        f"/api/knowledge-bases/dataset-a/tags/{second.json()['id']}/merge",
        headers=_headers(settings, "editor-a"),
        json={"source_tag_id": first.json()["id"]},
    )
    assert editor_merge.status_code == 403
    merged = client.post(
        f"/api/knowledge-bases/dataset-a/tags/{second.json()['id']}/merge",
        headers=_headers(settings, "owner-a"),
        json={"source_tag_id": first.json()["id"]},
    )
    assert merged.status_code == 200
    assert merged.json()["usage_count"] == 1

    detached = client.delete(
        f"/api/knowledge-bases/dataset-a/documents/doc-a/tags/{second.json()['id']}",
        headers=_headers(settings, "editor-a"),
    )
    assert detached.status_code == 200
    assert detached.json()["detached"] is True


def test_governance_mutation_uses_authenticated_actor_for_audit(api) -> None:
    client, engine, settings = api
    response = client.post(
        "/api/knowledge-bases/dataset-a/folders",
        headers=_headers(settings, "editor-a", request_id="req-folder-audit"),
        json={"name": "Engineering"},
    )
    assert response.status_code == 201
    with Session(engine) as session:
        event = session.scalar(
            select(KnowledgeAuditEvent).where(KnowledgeAuditEvent.request_id == "req-folder-audit")
        )
        assert event is not None
        assert event.actor_id == "editor-a"
        assert event.action == "folder.create"


def test_folder_archive_rejects_active_children_through_api(api) -> None:
    client, _, settings = api
    parent = client.post(
        "/api/knowledge-bases/dataset-a/folders",
        headers=_headers(settings, "editor-a"),
        json={"name": "Parent"},
    ).json()
    client.post(
        "/api/knowledge-bases/dataset-a/folders",
        headers=_headers(settings, "editor-a"),
        json={"name": "Child", "parent_id": parent["id"]},
    )
    response = client.post(
        f"/api/knowledge-bases/dataset-a/folders/{parent['id']}/archive",
        headers=_headers(settings, "owner-a"),
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "knowledge_governance_conflict"
