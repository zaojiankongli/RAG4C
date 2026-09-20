from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from config.settings import KnowledgeSecuritySettings, RunHistorySettings, TenantSettings
from core import catalog
from models.orm import Account, Base, Dataset, Document, Tenant, TenantMember
from server.knowledge_auth import issue_knowledge_actor_token
from server.storage_backends_api import router


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        run_history=RunHistorySettings(ops_bearer_token=SecretStr("ops-secret")),
        knowledge_security=KnowledgeSecuritySettings(
            actor_signing_secret=SecretStr("storage-actor-secret"),
            actor_max_ttl_s=900,
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
                Dataset(
                    id="dataset-empty",
                    tenant_id="tenant-a",
                    name="Empty",
                    status="active",
                    doc_count=0,
                ),
                Dataset(
                    id="dataset-full",
                    tenant_id="tenant-a",
                    name="Full",
                    status="active",
                    doc_count=3,
                ),
                Account(id="owner-a", name="Owner", email="o@a.test"),
                Account(id="editor-a", name="Editor", email="e@a.test"),
                TenantMember(account_id="owner-a", tenant_id="tenant-a", role="owner"),
                TenantMember(account_id="editor-a", tenant_id="tenant-a", role="editor"),
            ]
        )
        session.commit()
    return engine


@pytest.fixture()
def api(monkeypatch: pytest.MonkeyPatch):
    engine = _engine()
    settings = _settings()
    monkeypatch.setattr(catalog, "get_engine", lambda: engine)
    app = FastAPI()
    app.state.knowledge_auth_engine = engine
    app.state.knowledge_auth_settings = settings
    app.include_router(router)
    return TestClient(app, client=("10.0.0.2", 50000)), engine, settings


def _headers(settings: SimpleNamespace, actor: str, tenant: str = "tenant-a"):
    token = issue_knowledge_actor_token(
        actor, tenant, 300, int(time.time()), settings=settings
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-RAG4C-Tenant": tenant,
        "X-Request-ID": "req-storage",
    }


def test_storage_backend_lifecycle_masks_secrets_and_tests_local(api, tmp_path):
    client, _, settings = api
    root = (tmp_path / "kb-root").as_posix()
    created = client.post(
        "/api/storage-backends",
        headers=_headers(settings, "owner-a"),
        json={
            "name": "local-main",
            "provider": "local",
            "config": {"root_path": root},
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["provider"] == "local"
    assert body["config_masked"]["root_path"] == root
    backend_id = body["id"]

    listed = client.get("/api/storage-backends", headers=_headers(settings, "editor-a"))
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    assert listed.json()["default_storage_backend_id"] in (None, "")

    denied = client.post(
        "/api/storage-backends",
        headers=_headers(settings, "editor-a"),
        json={"name": "nope", "provider": "local", "config": {"root_path": root}},
    )
    assert denied.status_code == 403

    test_res = client.post(
        f"/api/storage-backends/{backend_id}/test",
        headers=_headers(settings, "owner-a"),
    )
    assert test_res.status_code == 200
    assert test_res.json()["status"] == "ok"

    defaulted = client.post(
        f"/api/storage-backends/{backend_id}/default",
        headers=_headers(settings, "owner-a"),
    )
    assert defaulted.status_code == 200
    assert defaulted.json()["is_default"] is True

    deny_delete = client.delete(
        f"/api/storage-backends/{backend_id}",
        headers=_headers(settings, "owner-a"),
    )
    assert deny_delete.status_code == 409

    # minio create masks secrets
    mini = client.post(
        "/api/storage-backends",
        headers=_headers(settings, "owner-a"),
        json={
            "name": "minio-1",
            "provider": "minio",
            "config": {
                "endpoint": "http://127.0.0.1:9000",
                "bucket": "rag4c",
                "access_key_id": "minioadmin",
                "secret_access_key": "minioadmin",
            },
        },
    )
    assert mini.status_code == 201
    masked = mini.json()["config_masked"]
    assert masked["secret_access_key"] == "***"
    assert masked["access_key_id"].startswith("ak_***")
    assert "minioadmin" not in str(masked)


def test_dataset_bind_frozen_when_documents_exist(api):
    client, _, settings = api
    created = client.post(
        "/api/storage-backends",
        headers=_headers(settings, "owner-a"),
        json={
            "name": "bindable",
            "provider": "local",
            "config": {"root_path": "/tmp/rag4c-storage-test"},
        },
    )
    backend_id = created.json()["id"]
    ok = client.put(
        "/api/storage-backends/bind-dataset",
        headers=_headers(settings, "owner-a"),
        json={"dataset_id": "dataset-empty", "storage_backend_id": backend_id},
    )
    assert ok.status_code == 200
    assert ok.json()["storage_backend_id"] == backend_id
    frozen = client.put(
        "/api/storage-backends/bind-dataset",
        headers=_headers(settings, "owner-a"),
        json={"dataset_id": "dataset-full", "storage_backend_id": backend_id},
    )
    assert frozen.status_code == 409
    types = client.get("/api/storage-backends/types", headers=_headers(settings, "editor-a"))
    assert types.status_code == 200
    assert any(p["provider"] == "minio" for p in types.json()["providers"])
