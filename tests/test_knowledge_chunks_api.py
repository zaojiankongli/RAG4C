from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from config.settings import KnowledgeSecuritySettings, RunHistorySettings, TenantSettings
from core import catalog
from core.chunk_catalog import ChunkCatalog, ChunkRevisionConflict
from models.orm import Account, Base, Dataset, Document, Tenant, TenantMember
from server.knowledge_auth import issue_knowledge_actor_token


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        run_history=RunHistorySettings(ops_bearer_token=SecretStr("ops-secret")),
        knowledge_security=KnowledgeSecuritySettings(
            actor_signing_secret=SecretStr("chunk-api-secret"), actor_max_ttl_s=900
        ),
        tenant=TenantSettings(enforced=True, default_tenant="tenant-a"),
    )


def _headers(settings: SimpleNamespace, actor: str, tenant: str = "tenant-a") -> dict[str, str]:
    token = issue_knowledge_actor_token(actor, tenant, 300, int(time.time()), settings=settings)
    return {"Authorization": f"Bearer {token}", "X-RAG4C-Tenant": tenant}


@pytest.fixture()
def api(monkeypatch: pytest.MonkeyPatch):
    from server import knowledge_chunks_api

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            Tenant(id="tenant-a", name="A", status="active"),
            Tenant(id="tenant-b", name="B", status="active"),
            Dataset(id="dataset-a", tenant_id="tenant-a", name="A", status="active"),
            Dataset(id="dataset-b", tenant_id="tenant-b", name="B", status="active"),
            Account(id="owner-a", name="Owner", email="owner@example.test"),
            Account(id="editor-a", name="Editor", email="editor@example.test"),
            Account(id="member-a", name="Member", email="member@example.test"),
            Account(id="owner-b", name="Owner B", email="owner-b@example.test"),
            TenantMember(account_id="owner-a", tenant_id="tenant-a", role="owner"),
            TenantMember(account_id="editor-a", tenant_id="tenant-a", role="editor"),
            TenantMember(account_id="member-a", tenant_id="tenant-a", role="member"),
            TenantMember(account_id="owner-b", tenant_id="tenant-b", role="owner"),
            Document(id="doc-a", tenant_id="tenant-a", dataset_id="dataset-a", name="Guide", status="completed", content_revision=3, desired_index_revision=3),
            Document(id="doc-b", tenant_id="tenant-b", dataset_id="dataset-b", name="Secret", status="completed", content_revision=1),
        ])
        session.commit()
    chunks = ChunkCatalog(engine)
    chunks.upsert_head(chunk_id="parent-a", tenant_id="tenant-a", dataset_id="dataset-a", document_id="doc-a", parent_chunk_id=None, chunk_index=0, chunk_role="parent", document_revision=3, source_content="parent", content="parent")
    for index in range(235):
        chunks.upsert_head(
            chunk_id=f"chunk-{index:03d}", tenant_id="tenant-a", dataset_id="dataset-a",
            document_id="doc-a", parent_chunk_id="parent-a" if index == 1 else None,
            chunk_index=index + 1, chunk_role="child" if index == 1 else "flat",
            document_revision=3, source_content=f"source {index}",
            content=f"policy body {index}" if index != 222 else "needle reimbursement policy",
            metadata={
                "seq": index,
                "page": index // 10 + 1,
                "heading": f"Section {index}",
                "source": "https://user:secret@example.test/doc?token=hidden#fragment",
                "api_key": "must-not-leak",
                "nested": {"password": "must-not-leak"},
                "language": "en",
            },
        )
    chunks.upsert_head(chunk_id="foreign", tenant_id="tenant-b", dataset_id="dataset-b", document_id="doc-b", parent_chunk_id=None, chunk_index=0, chunk_role="flat", document_revision=1, source_content="secret", content="secret")
    settings = _settings()
    monkeypatch.setattr(catalog, "get_engine", lambda: engine)
    app = FastAPI()
    app.state.knowledge_auth_engine = engine
    app.state.knowledge_auth_settings = settings
    app.state.chunk_authority_mode = "active"
    app.include_router(knowledge_chunks_api.router)
    return TestClient(app, client=("10.0.0.2", 50000)), engine, settings, app


def test_chunk_routes_require_bearer_tenant_and_permissions(api, monkeypatch) -> None:
    client, _, settings, _ = api
    path = "/api/knowledge-bases/dataset-a/documents/doc-a/chunks"
    assert client.get(path).status_code == 401
    no_tenant = _headers(settings, "member-a")
    no_tenant.pop("X-RAG4C-Tenant")
    assert client.get(path, headers=no_tenant).status_code == 400
    assert client.get(path, headers=_headers(settings, "member-a")).status_code == 200
    assert client.patch(f"{path}/chunk-000", headers=_headers(settings, "member-a"), json={"text": "edit", "expected_revision": 0}).status_code == 403


def test_chunk_routes_conceal_cross_scope_and_expose_bearer_openapi(api) -> None:
    client, _, settings, _ = api
    hidden = client.get(
        "/api/knowledge-bases/dataset-a/documents/doc-b/chunks",
        headers=_headers(settings, "owner-a"),
    )
    assert hidden.status_code == 404
    assert hidden.json()["detail"]["code"] == "knowledge_resource_not_found"
    schema = client.get("/openapi.json").json()
    assert "KnowledgeBearerAuth" in schema["components"]["securitySchemes"]
    operation = schema["paths"]["/api/knowledge-bases/{dataset_id}/documents/{doc_id}/chunks"]["get"]
    assert operation["security"] == [{"KnowledgeBearerAuth": []}]


def test_sql_page_search_count_safe_projection_and_parent_facts(api) -> None:
    client, _, settings, _ = api
    path = "/api/knowledge-bases/dataset-a/documents/doc-a/chunks"
    first = client.get(path, headers=_headers(settings, "member-a"), params={"limit": 100})
    assert first.status_code == 200
    body = first.json()
    assert body["authority_mode"] == "active"
    assert body["total"] == 235
    assert len(body["items"]) == 100
    assert body["items"][0]["chunk_id"] == "chunk-000"
    assert "metadata" not in body["items"][0]
    assert "must-not-leak" not in str(body)
    assert body["items"][0]["source_reference"] == "https://example.test/doc"
    assert "parent-a" in body["known_parent_ids"]
    second = client.get(path, headers=_headers(settings, "member-a"), params={"offset": 200, "limit": 100}).json()
    assert len(second["items"]) == 35
    found = client.get(path, headers=_headers(settings, "member-a"), params={"query": "reimbursement"}).json()
    assert found["total"] == 1 and found["items"][0]["chunk_id"] == "chunk-222"
    assert client.get(path, headers=_headers(settings, "member-a"), params={"query": "x" * 257}).status_code == 422


def test_detail_and_mode_fail_closed(api) -> None:
    client, _, settings, app = api
    path = "/api/knowledge-bases/dataset-a/documents/doc-a/chunks/chunk-001"
    detail = client.get(path, headers=_headers(settings, "member-a"))
    assert detail.status_code == 200
    assert detail.json()["parent_relation"] == "known"
    app.state.chunk_authority_mode = "off"
    blocked = client.get(path, headers=_headers(settings, "member-a"))
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "knowledge_chunk_authority_unavailable"


def test_patch_delete_use_exact_post_mutation_projection(api, monkeypatch) -> None:
    from server import knowledge_chunks_api
    client, _, settings, _ = api
    monkeypatch.setattr(knowledge_chunks_api, "_reserve", lambda _doc_id: None)
    monkeypatch.setattr(knowledge_chunks_api, "_release", lambda _doc_id: None)

    def edit(*_args, **_kwargs):
        catalog_api = ChunkCatalog(catalog.get_engine())
        head = catalog_api.edit_chunk("chunk-000", expected_revision=0, content="edited", editor_id="manual")
        return SimpleNamespace(authority_mode="active", operation_ids=("op-1",), graph_entities=0, graph_relations=0, removed_entities=0, removed_relations=0, chunk=head)

    def delete(*_args, **_kwargs):
        catalog_api = ChunkCatalog(catalog.get_engine())
        head = catalog_api.tombstone_chunk("chunk-001", expected_revision=0, editor_id="manual")
        return {"authority_mode": "active", "operation_ids": ["op-2"], "chunk_id": head.id}

    monkeypatch.setattr(knowledge_chunks_api, "_update", edit)
    monkeypatch.setattr(knowledge_chunks_api, "_delete", delete)
    base = "/api/knowledge-bases/dataset-a/documents/doc-a/chunks"
    patched = client.patch(f"{base}/chunk-000", headers=_headers(settings, "editor-a"), json={"text": "edited", "expected_revision": 0})
    assert patched.status_code == 200
    assert patched.json()["content_revision"] == 1
    assert patched.json()["index_status"] == "pending"
    assert patched.json()["projection_pending"] is True
    assert patched.json()["operation_ids"] == ["op-1"]
    deleted = client.delete(f"{base}/chunk-001", headers=_headers(settings, "editor-a"), params={"expected_revision": 0})
    assert deleted.status_code == 200
    assert deleted.json()["enabled"] is False
    assert deleted.json()["operation_ids"] == ["op-2"]
    again = client.patch(f"{base}/chunk-001", headers=_headers(settings, "editor-a"), json={"text": "loop", "expected_revision": 1})
    assert again.status_code == 409


def test_legacy_chunk_http_routes_are_gone(monkeypatch) -> None:
    from server.documents import router as legacy_router
    app = FastAPI()
    app.include_router(legacy_router)
    client = TestClient(app)
    for method, path in [
        ("get", "/api/documents/doc-a/chunks"),
        ("patch", "/api/documents/doc-a/chunks/chunk-1"),
        ("delete", "/api/documents/doc-a/chunks/chunk-1?expected_revision=0"),
    ]:
        response = client.patch(path, json={"text": "x", "expected_revision": 0}) if method == "patch" else getattr(client, method)(path)
        assert response.status_code == 410
        assert response.json()["detail"]["code"] == "knowledge_chunk_legacy_route_gone"


def test_failed_or_unacquired_reservation_never_releases_another_owner(api, monkeypatch) -> None:
    from server import knowledge_chunks_api
    client, _, settings, _ = api
    releases: list[str] = []
    monkeypatch.setattr(knowledge_chunks_api, "_release", lambda doc_id: releases.append(doc_id))
    monkeypatch.setattr(
        knowledge_chunks_api,
        "_reserve",
        lambda _doc_id: (_ for _ in ()).throw(HTTPException(status_code=409, detail="busy")),
    )
    response = client.patch(
        "/api/knowledge-bases/dataset-a/documents/doc-a/chunks/chunk-000",
        headers=_headers(settings, "editor-a"),
        json={"text": "edit", "expected_revision": 0},
    )
    assert response.status_code == 409
    assert releases == []

    # Tombstone validation fails before reservation and must not release either.
    chunks = ChunkCatalog(catalog.get_engine())
    chunks.tombstone_chunk("chunk-001", expected_revision=0, editor_id="test")
    response = client.patch(
        "/api/knowledge-bases/dataset-a/documents/doc-a/chunks/chunk-001",
        headers=_headers(settings, "editor-a"),
        json={"text": "edit", "expected_revision": 1},
    )
    assert response.status_code == 409
    assert releases == []


def test_acquired_reservation_releases_once_when_mutation_fails(api, monkeypatch) -> None:
    from server import knowledge_chunks_api
    client, _, settings, _ = api
    events: list[str] = []
    monkeypatch.setattr(knowledge_chunks_api, "_reserve", lambda doc_id: events.append(f"reserve:{doc_id}"))
    monkeypatch.setattr(knowledge_chunks_api, "_release", lambda doc_id: events.append(f"release:{doc_id}"))
    monkeypatch.setattr(
        knowledge_chunks_api,
        "_update",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ChunkRevisionConflict("conflict")),
    )
    response = client.patch(
        "/api/knowledge-bases/dataset-a/documents/doc-a/chunks/chunk-000",
        headers=_headers(settings, "editor-a"),
        json={"text": "edit", "expected_revision": 0},
    )
    assert response.status_code == 409
    assert events == ["reserve:doc-a", "release:doc-a"]


def test_safe_projection_omits_nested_scalars_and_malformed_uri_never_500(api) -> None:
    from models.orm import ChunkHead
    client, engine, settings, _ = api
    with Session(engine) as session:
        head = session.get(ChunkHead, "chunk-000")
        assert head is not None
        head.chunk_metadata = {
            "page": {"nested": "secret"},
            "heading": ["not", "scalar"],
            "seq": {"bad": 1},
            "source": "https://example.test:bad/path?token=secret#fragment",
            "language": {"token": "secret"},
        }
        session.commit()
    response = client.get(
        "/api/knowledge-bases/dataset-a/documents/doc-a/chunks/chunk-000",
        headers=_headers(settings, "member-a"),
    )
    assert response.status_code == 200
    body = response.json()
    assert "page" not in body
    assert "heading" not in body
    assert body["seq"] == 1  # bounded fallback to authoritative chunk_index
    assert "language" not in body
    assert body["source_reference"] == "protected_reference"
    assert "secret" not in str(body)


def test_projection_omits_non_string_heading_and_container_source(api) -> None:
    from models.orm import ChunkHead
    client, engine, settings, _ = api
    with Session(engine) as session:
        head = session.get(ChunkHead, "chunk-000")
        assert head is not None
        head.chunk_metadata = {
            "heading": 123,
            "title": ["nested"],
            "section": {"secret": "hidden"},
            "language": 7,
            "mime_type": ["text/plain"],
            "source": {"token": "must-not-leak"},
            "page": 4,
            "seq": 9,
        }
        session.commit()
    body = client.get(
        "/api/knowledge-bases/dataset-a/documents/doc-a/chunks/chunk-000",
        headers=_headers(settings, "member-a"),
    ).json()
    for key in ("heading", "language", "mime_type", "source_reference"):
        assert key not in body
    assert body["page"] == 4
    assert body["seq"] == 9
    assert "must-not-leak" not in str(body)


def test_patch_delete_recompute_relation_and_child_count(api, monkeypatch) -> None:
    from models.orm import ChunkHead
    from server import knowledge_chunks_api
    client, engine, settings, _ = api
    with Session(engine) as session:
        child = session.get(ChunkHead, "chunk-002")
        assert child is not None
        child.parent_chunk_id = "chunk-000"
        session.commit()
    monkeypatch.setattr(knowledge_chunks_api, "_reserve", lambda _doc_id: None)
    monkeypatch.setattr(knowledge_chunks_api, "_release", lambda _doc_id: None)

    def edit(*_args, **_kwargs):
        head = ChunkCatalog(engine).edit_chunk("chunk-000", expected_revision=0, content="edited", editor_id="test")
        return SimpleNamespace(authority_mode="active", operation_ids=("op",), chunk=head)

    def delete(*_args, **_kwargs):
        head = ChunkCatalog(engine).tombstone_chunk("chunk-001", expected_revision=0, editor_id="test")
        return {"authority_mode": "active", "operation_ids": ["delete-op"], "chunk_id": head.id}

    monkeypatch.setattr(knowledge_chunks_api, "_update", edit)
    monkeypatch.setattr(knowledge_chunks_api, "_delete", delete)
    base = "/api/knowledge-bases/dataset-a/documents/doc-a/chunks"
    patched = client.patch(
        f"{base}/chunk-000",
        headers=_headers(settings, "editor-a"),
        json={"text": "edited", "expected_revision": 0},
    ).json()
    assert patched["parent_relation"] == "none"
    assert patched["child_count"] == 1
    deleted = client.delete(
        f"{base}/chunk-001",
        headers=_headers(settings, "editor-a"),
        params={"expected_revision": 0},
    ).json()
    assert deleted["parent_relation"] == "known"
    assert deleted["child_count"] == 0
