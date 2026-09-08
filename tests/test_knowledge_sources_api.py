from __future__ import annotations

import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from config.settings import KnowledgeSecuritySettings, RunHistorySettings, TenantSettings
from core import catalog
from models.orm import (
    Account,
    Base,
    DataSourceRecord,
    Dataset,
    KnowledgeAuditEvent,
    SourceSchedule,
    SourceSyncItem,
    SourceSyncRun,
    Tenant,
    TenantMember,
)
from server.knowledge_auth import issue_knowledge_actor_token
from server.knowledge_sources_api import router, sanitize_source_uri


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        run_history=RunHistorySettings(ops_bearer_token=SecretStr("ops-secret")),
        knowledge_security=KnowledgeSecuritySettings(
            actor_signing_secret=SecretStr("sources-api-actor-secret"), actor_max_ttl_s=900
        ),
        tenant=TenantSettings(enforced=True, default_tenant="tenant-a"),
        sources=SimpleNamespace(
            cache_dir=".rag4c_cache/sources",
            state_mode="database",
            local_allowed_roots=[],
            local_allowed_extensions=[],
            github_allowed_repositories=[],
            github_allowed_organizations=[],
        ),
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
                Dataset(
                    id="dataset-a2", tenant_id="tenant-a", name="Dataset A2", status="active"
                ),
                Dataset(id="dataset-b", tenant_id="tenant-b", name="Dataset B", status="active"),
                Account(id="admin-a", name="Admin", email="admin-a@example.test"),
                Account(id="editor-a", name="Editor", email="editor-a@example.test"),
                Account(id="member-a", name="Member", email="member-a@example.test"),
                Account(id="admin-b", name="Admin B", email="admin-b@example.test"),
                TenantMember(account_id="admin-a", tenant_id="tenant-a", role="admin"),
                TenantMember(account_id="editor-a", tenant_id="tenant-a", role="editor"),
                TenantMember(account_id="member-a", tenant_id="tenant-a", role="member"),
                TenantMember(account_id="admin-b", tenant_id="tenant-b", role="admin"),
            ]
        )
        session.commit()
    return engine


@pytest.fixture()
def api(monkeypatch: pytest.MonkeyPatch, tmp_path):
    engine, settings = _engine(), _settings()
    submitted: list[Callable[[], None]] = []
    executed: list[tuple[str, str]] = []

    monkeypatch.setattr(catalog, "get_engine", lambda: engine)
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    settings.sources.local_allowed_roots = [str(allowed_root)]
    settings.sources.local_allowed_extensions = [".md", ".txt"]
    settings.sources.github_allowed_repositories = ["openai/openai-python"]
    settings.sources.github_allowed_organizations = ["trusted-org"]
    app = FastAPI()
    app.state.knowledge_auth_engine = engine
    app.state.knowledge_auth_settings = settings
    app.state.knowledge_source_task_submitter = submitted.append

    def execute(spec, run_id: str) -> None:
        executed.append((spec.name, run_id))

    app.state.knowledge_source_sync_executor = execute
    app.include_router(router)
    return (
        TestClient(app, client=("10.0.0.2", 50000)),
        engine,
        settings,
        submitted,
        executed,
        allowed_root,
        app,
    )


def _headers(
    settings: SimpleNamespace,
    actor: str,
    tenant: str = "tenant-a",
    request_id: str = "req-sources",
    idempotency_key: str | None = None,
) -> dict[str, str]:
    token = issue_knowledge_actor_token(actor, tenant, 300, int(time.time()), settings=settings)
    headers = {
        "Authorization": f"Bearer {token}",
        "X-RAG4C-Tenant": tenant,
        "X-Request-ID": request_id,
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _create_source(
    client: TestClient,
    settings: SimpleNamespace,
    root,
    *,
    dataset_id: str = "dataset-a",
    name: str = "handbook",
    actor: str = "admin-a",
    extra: dict[str, Any] | None = None,
):
    body: dict[str, Any] = {
        "name": name,
        "kind": "local_dir",
        "config": {"path": str(root)},
        "metadata": {"team": "platform"},
        "enabled": True,
    }
    body.update(extra or {})
    return client.post(
        f"/api/knowledge-bases/{dataset_id}/sources",
        headers=_headers(settings, actor, request_id=f"create-{name}"),
        json=body,
    )


def test_source_rbac_and_dataset_scope_are_fail_closed(api) -> None:
    client, _, settings, _, _, root, _ = api

    assert client.get(
        "/api/knowledge-bases/dataset-a/sources", headers=_headers(settings, "member-a")
    ).status_code == 200
    assert _create_source(client, settings, root, actor="member-a").status_code == 403
    assert _create_source(client, settings, root, actor="editor-a").status_code == 403

    created = _create_source(client, settings, root)
    assert created.status_code == 201
    source_id = created.json()["id"]

    assert client.get(
        f"/api/knowledge-bases/dataset-a/sources/{source_id}",
        headers=_headers(settings, "member-a"),
    ).status_code == 200
    assert client.get(
        f"/api/knowledge-bases/dataset-a2/sources/{source_id}",
        headers=_headers(settings, "admin-a"),
    ).status_code == 404
    assert client.get(
        f"/api/knowledge-bases/dataset-a/sources/{source_id}",
        headers=_headers(settings, "admin-b", tenant="tenant-b"),
    ).status_code == 403


def test_create_rejects_unknown_kind_extra_fields_and_inline_secrets(api) -> None:
    client, _, settings, _, _, root, _ = api

    unknown = _create_source(
        client, settings, root, name="database", extra={"kind": "postgresql"}
    )
    assert unknown.status_code == 422

    extra = _create_source(client, settings, root, name="extra", extra={"surprise": True})
    assert extra.status_code == 422

    for config in (
        {"path": str(root), "token": "ghp_inline"},
        {"path": str(root), "nested": {"password": "inline"}},
        {"path": str(root), "credential_ref": "plain-text-secret"},
    ):
        response = _create_source(
            client,
            settings,
            root,
            name=f"secret-{len(config)}-{hash(str(config))}",
            extra={"config": config},
        )
        assert response.status_code == 422
        assert "ghp_inline" not in response.text
        assert "plain-text-secret" not in response.text


def test_connector_preflight_rejects_path_escape_and_arbitrary_github_url(api, tmp_path) -> None:
    client, _, settings, _, _, root, _ = api
    outside = tmp_path / "outside"
    outside.mkdir()

    escaped = _create_source(
        client,
        settings,
        root,
        name="escaped",
        extra={"config": {"path": str(outside)}},
    )
    assert escaped.status_code == 422

    arbitrary_url = _create_source(
        client,
        settings,
        root,
        name="url-source",
        extra={
            "kind": "github_repo",
            "config": {"repo": "https://evil.example/private/repository"},
        },
    )
    assert arbitrary_url.status_code == 422


def test_create_preserves_safe_credential_reference_but_never_exposes_effective_config(api) -> None:
    client, engine, settings, _, _, root, _ = api
    created = _create_source(
        client,
        settings,
        root,
        extra={"config": {"path": str(root), "credential_ref": "secret://sources/local"}},
    )
    assert created.status_code == 201
    payload = created.json()
    assert "effective_config" not in payload
    assert payload["config"]["credential_ref"] == "secret://sources/local"

    with Session(engine) as session:
        source = session.get(DataSourceRecord, payload["id"])
        assert source is not None
        assert source.effective_config["params"]["credential_ref"] == "secret://sources/local"
        audit = session.scalar(
            select(KnowledgeAuditEvent).where(
                KnowledgeAuditEvent.action == "source.create",
                KnowledgeAuditEvent.resource_id == payload["id"],
            )
        )
        assert audit is not None
        assert audit.actor_id == "admin-a"
        assert "secret://sources/local" not in str(audit.after_snapshot)


def test_patch_and_enable_disable_use_generation_cas_and_audit(api) -> None:
    client, engine, settings, _, _, root, _ = api
    source = _create_source(client, settings, root).json()
    with Session(engine) as session:
        session.add(
            SourceSyncRun(
                id="run-before-update",
                source_id=source["id"],
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                status="running",
                trigger="manual",
                source_generation=0,
                dataset_generation=0,
            )
        )
        session.commit()

    updated = client.patch(
        f"/api/knowledge-bases/dataset-a/sources/{source['id']}",
        headers=_headers(settings, "admin-a", request_id="source-update"),
        json={
            "expected_generation": 0,
            "name": "engineering-handbook",
            "metadata": {"team": "engineering"},
        },
    )
    assert updated.status_code == 200
    assert updated.json()["generation"] == 1
    assert updated.json()["name"] == "engineering-handbook"
    with Session(engine) as session:
        assert session.get(SourceSyncRun, "run-before-update").status == "superseded"

    stale = client.patch(
        f"/api/knowledge-bases/dataset-a/sources/{source['id']}",
        headers=_headers(settings, "admin-a"),
        json={"expected_generation": 0, "metadata": {"team": "stale"}},
    )
    assert stale.status_code == 409

    disabled = client.post(
        f"/api/knowledge-bases/dataset-a/sources/{source['id']}/disable",
        headers=_headers(settings, "admin-a"),
        json={"expected_generation": 1},
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    assert disabled.json()["generation"] == 2

    enabled = client.post(
        f"/api/knowledge-bases/dataset-a/sources/{source['id']}/enable",
        headers=_headers(settings, "admin-a"),
        json={"expected_generation": 2},
    )
    assert enabled.status_code == 200
    assert enabled.json()["status"] == "active"
    assert enabled.json()["generation"] == 3

    with Session(engine) as session:
        actions = list(
            session.scalars(
                select(KnowledgeAuditEvent.action)
                .where(KnowledgeAuditEvent.resource_id == source["id"])
                .order_by(KnowledgeAuditEvent.sequence)
            )
        )
    assert actions == ["source.create", "source.update", "source.disable", "source.enable"]


def test_sync_now_is_202_non_blocking_and_does_not_assume_success(api) -> None:
    client, engine, settings, submitted, executed, root, app = api
    source = _create_source(client, settings, root).json()

    def fail(_spec, _run_id: str) -> None:
        raise RuntimeError("token=do-not-leak upstream unavailable")

    app.state.knowledge_source_sync_executor = fail
    response = client.post(
        f"/api/knowledge-bases/dataset-a/sources/{source['id']}/sync-now",
        headers=_headers(
            settings,
            "editor-a",
            request_id="sync-now",
            idempotency_key="source-sync-now-0001",
        ),
        json={"force_full": False, "dry_run": False},
    )
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    assert response.json()["status"] == "running"
    assert executed == []
    assert len(submitted) == 1

    with Session(engine) as session:
        assert session.get(SourceSyncRun, run_id).status == "running"

    submitted.pop()()
    with Session(engine) as session:
        run = session.get(SourceSyncRun, run_id)
        assert run is not None
        assert run.status == "running"
        assert run.execution_state == "failed"
        assert "do-not-leak" not in run.execution_last_error


def test_run_and_item_filters_are_scoped(api) -> None:
    client, engine, settings, _, _, root, _ = api
    source = _create_source(client, settings, root).json()
    planned_at = datetime.utcnow()
    with Session(engine) as session:
        session.add_all(
            [
                SourceSchedule(
                    id="schedule-api-filter",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    source_id=source["id"],
                    revision=1,
                    status="active",
                    interval_seconds=300,
                    force_full=False,
                    next_run_at=planned_at + timedelta(seconds=300),
                    created_by="admin-a",
                    updated_by="admin-a",
                    created_at=planned_at,
                    updated_at=planned_at,
                ),
                SourceSyncRun(
                    id="run-completed",
                    source_id=source["id"],
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    status="completed",
                    trigger="manual",
                    source_generation=0,
                    dataset_generation=0,
                ),
                SourceSyncRun(
                    id="run-failed",
                    source_id=source["id"],
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    status="failed",
                    trigger="scheduled",
                    schedule_id="schedule-api-filter",
                    schedule_revision=1,
                    planned_at=planned_at,
                    source_generation=0,
                    dataset_generation=0,
                    fetch_error="safe failure",
                ),
                SourceSyncItem(
                    id="item-failed",
                    run_id="run-failed",
                    source_id=source["id"],
                    external_id="guide.md",
                    doc_id="doc-guide",
                    action="upsert",
                    result="failed",
                    error_message="safe item failure",
                ),
                SourceSyncItem(
                    id="item-skipped",
                    run_id="run-failed",
                    source_id=source["id"],
                    external_id="readme.md",
                    doc_id="doc-readme",
                    action="skip",
                    result="skipped",
                ),
            ]
        )
        session.commit()

    runs = client.get(
        f"/api/knowledge-bases/dataset-a/sources/{source['id']}/runs",
        headers=_headers(settings, "member-a"),
        params={"status": "failed", "trigger": "scheduled", "limit": 20},
    )
    assert runs.status_code == 200
    assert [item["id"] for item in runs.json()["items"]] == ["run-failed"]

    detail = client.get(
        f"/api/knowledge-bases/dataset-a/sources/{source['id']}/runs/run-failed",
        headers=_headers(settings, "member-a"),
    )
    assert detail.status_code == 200
    assert detail.json()["fetch_error"] == "safe failure"

    items = client.get(
        f"/api/knowledge-bases/dataset-a/sources/{source['id']}/runs/run-failed/items",
        headers=_headers(settings, "member-a"),
        params={"result": "failed", "action": "upsert"},
    )
    assert items.status_code == 200
    assert [item["id"] for item in items.json()["items"]] == ["item-failed"]


def test_retry_requires_manage_failed_state_and_current_generation(api) -> None:
    client, engine, settings, submitted, _, root, _ = api
    source = _create_source(client, settings, root).json()
    with Session(engine) as session:
        session.add_all(
            [
                SourceSyncRun(
                    id="run-current-failed",
                    source_id=source["id"],
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    status="failed",
                    trigger="manual",
                    source_generation=0,
                    dataset_generation=0,
                ),
                SourceSyncRun(
                    id="run-completed",
                    source_id=source["id"],
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    status="completed",
                    trigger="manual",
                    source_generation=0,
                    dataset_generation=0,
                ),
            ]
        )
        session.commit()

    forbidden = client.post(
        f"/api/knowledge-bases/dataset-a/sources/{source['id']}/runs/run-current-failed/retry",
        headers=_headers(settings, "editor-a"),
        json={},
    )
    assert forbidden.status_code == 403

    not_failed = client.post(
        f"/api/knowledge-bases/dataset-a/sources/{source['id']}/runs/run-completed/retry",
        headers=_headers(settings, "admin-a"),
        json={},
    )
    assert not_failed.status_code == 409

    retried = client.post(
        f"/api/knowledge-bases/dataset-a/sources/{source['id']}/runs/run-current-failed/retry",
        headers=_headers(settings, "admin-a", request_id="retry-run"),
        json={},
    )
    assert retried.status_code == 202
    assert retried.json()["status"] == "running"
    assert retried.json()["trigger"] == "retry"
    assert retried.json()["retry_of_run_id"] == "run-current-failed"
    assert len(submitted) == 1

    with Session(engine) as session:
        stale = SourceSyncRun(
            id="run-stale-failed",
            source_id=source["id"],
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            status="failed",
            trigger="manual",
            source_generation=0,
            dataset_generation=0,
        )
        session.add(stale)
        session.get(DataSourceRecord, source["id"]).mutation_generation = 1
        session.commit()

    stale_response = client.post(
        f"/api/knowledge-bases/dataset-a/sources/{source['id']}/runs/run-stale-failed/retry",
        headers=_headers(settings, "admin-a"),
        json={},
    )
    assert stale_response.status_code == 409


def test_real_server_app_exposes_source_control_plane_openapi() -> None:
    from server.app import app as real_app

    paths = real_app.openapi()["paths"]
    assert "/api/knowledge-bases/{dataset_id}/sources" in paths
    assert "/api/knowledge-bases/{dataset_id}/sources/{source_id}/sync-now" in paths
    assert (
        "/api/knowledge-bases/{dataset_id}/sources/{source_id}/runs/{run_id}/retry" in paths
    )



def test_sync_now_requires_idempotency_and_replays_same_request_once(api) -> None:
    client, engine, settings, submitted, _, root, _ = api
    source = _create_source(client, settings, root).json()
    url = f"/api/knowledge-bases/dataset-a/sources/{source['id']}/sync-now"

    missing = client.post(
        url,
        headers=_headers(settings, "editor-a"),
        json={"force_full": False, "dry_run": False},
    )
    assert missing.status_code == 422

    headers = _headers(
        settings,
        "editor-a",
        request_id="sync-idempotent",
        idempotency_key="source-sync-idempotency-0001",
    )
    first = client.post(url, headers=headers, json={"force_full": False, "dry_run": False})
    replay = client.post(url, headers=headers, json={"force_full": False, "dry_run": False})
    assert first.status_code == 202
    assert replay.status_code == 202
    assert replay.json()["run_id"] == first.json()["run_id"]
    assert first.json()["replayed"] is False
    assert replay.json()["replayed"] is True
    assert len(submitted) == 1

    conflict = client.post(url, headers=headers, json={"force_full": True, "dry_run": False})
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "knowledge_source_idempotency_conflict"

    with Session(engine) as session:
        run = session.get(SourceSyncRun, first.json()["run_id"])
        assert run is not None
        assert run.idempotency_key == "source-sync-idempotency-0001"
        assert len(run.request_hash) == 64


def test_retry_is_exactly_once_and_returns_existing_retry(api) -> None:
    client, engine, settings, submitted, _, root, _ = api
    source = _create_source(client, settings, root).json()
    with Session(engine) as session:
        session.add(
            SourceSyncRun(
                id="run-failed-once",
                source_id=source["id"],
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                status="failed",
                trigger="manual",
                source_generation=0,
                dataset_generation=0,
            )
        )
        session.commit()

    url = (
        f"/api/knowledge-bases/dataset-a/sources/{source['id']}"
        "/runs/run-failed-once/retry"
    )
    first = client.post(url, headers=_headers(settings, "admin-a"), json={})
    second = client.post(url, headers=_headers(settings, "admin-a"), json={})
    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["run_id"] == first.json()["run_id"]
    assert first.json()["replayed"] is False
    assert second.json()["replayed"] is True
    assert len(submitted) == 1

    with Session(engine) as session:
        retry = session.get(SourceSyncRun, first.json()["run_id"])
        assert retry is not None
        assert retry.retry_of_run_id == "run-failed-once"
        count = session.query(SourceSyncRun).filter(
            SourceSyncRun.retry_of_run_id == "run-failed-once"
        ).count()
        assert count == 1


def test_source_allowlists_fail_closed_and_connector_models_are_strict(api, tmp_path) -> None:
    client, _, settings, _, _, root, _ = api

    settings.sources.local_allowed_roots = []
    blocked = _create_source(client, settings, root, name="blocked-local")
    assert blocked.status_code == 422
    settings.sources.local_allowed_roots = [str(root)]

    settings.sources.local_allowed_extensions = []
    blocked_extensions = _create_source(client, settings, root, name="blocked-extension")
    assert blocked_extensions.status_code == 422
    settings.sources.local_allowed_extensions = [".md"]

    unsupported_extension = _create_source(
        client,
        settings,
        root,
        name="unsupported-extension",
        extra={"config": {"path": str(root), "extensions": [".pdf"]}},
    )
    assert unsupported_extension.status_code == 422

    unknown_field = _create_source(
        client,
        settings,
        root,
        name="unknown-field",
        extra={"config": {"path": str(root), "shell": "whoami"}},
    )
    assert unknown_field.status_code == 422

    wrong_type = _create_source(
        client,
        settings,
        root,
        name="wrong-type",
        extra={"config": {"path": str(root), "max_files": "10"}},
    )
    assert wrong_type.status_code == 422

    settings.sources.github_allowed_repositories = []
    settings.sources.github_allowed_organizations = []
    github_blocked = _create_source(
        client,
        settings,
        root,
        name="github-blocked",
        extra={"kind": "github_repo", "config": {"repo": "openai/openai-python"}},
    )
    assert github_blocked.status_code == 422

    settings.sources.github_allowed_organizations = ["trusted-org"]
    github_allowed = _create_source(
        client,
        settings,
        root,
        name="github-allowed",
        extra={"kind": "github_repo", "config": {"repo": "trusted-org/docs"}},
    )
    assert github_allowed.status_code == 201


def test_run_item_uri_sanitizer_preserves_shape_and_redacts_credentials(api) -> None:
    client, engine, settings, _, _, root, _ = api
    source = _create_source(client, settings, root).json()
    with Session(engine) as session:
        session.add(
            SourceSyncRun(
                id="run-uri",
                source_id=source["id"],
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                status="failed",
                trigger="manual",
                source_generation=0,
                dataset_generation=0,
            )
        )
        session.add(
            SourceSyncItem(
                id="item-uri",
                run_id="run-uri",
                source_id=source["id"],
                external_id="guide.md",
                doc_id="doc-guide",
                source_uri=(
                    "https://user:password@example.test/docs/guide.md"
                    "?token=top-secret&lang=zh#section-1"
                ),
                action="upsert",
                result="failed",
            )
        )
        session.commit()

    response = client.get(
        f"/api/knowledge-bases/dataset-a/sources/{source['id']}/runs/run-uri/items",
        headers=_headers(settings, "member-a"),
    )
    assert response.status_code == 200
    uri = response.json()["items"][0]["source_uri"]
    assert uri.startswith("https://[REDACTED]@example.test/docs/guide.md?")
    assert "lang=zh" in uri
    assert "token=%5BREDACTED%5D" in uri
    assert uri.endswith("#section-1")
    assert "password" not in uri
    assert "top-secret" not in uri


def test_run_filters_cursor_and_openapi_contracts_are_strict(api) -> None:
    client, engine, settings, _, _, root, app = api
    source = _create_source(client, settings, root).json()
    with Session(engine) as session:
        for index in range(3):
            session.add(
                SourceSyncRun(
                    id=f"run-page-{index}",
                    source_id=source["id"],
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    status="failed",
                    trigger="manual",
                    source_generation=0,
                    dataset_generation=0,
                )
            )
        session.commit()

    invalid_filter = client.get(
        f"/api/knowledge-bases/dataset-a/sources/{source['id']}/runs",
        headers=_headers(settings, "member-a"),
        params={"status": "anything"},
    )
    assert invalid_filter.status_code == 422
    invalid_cursor = client.get(
        f"/api/knowledge-bases/dataset-a/sources/{source['id']}/runs",
        headers=_headers(settings, "member-a"),
        params={"cursor": "not-a-valid-cursor"},
    )
    assert invalid_cursor.status_code == 422

    first = client.get(
        f"/api/knowledge-bases/dataset-a/sources/{source['id']}/runs",
        headers=_headers(settings, "member-a"),
        params={"status": "failed", "trigger": "manual", "limit": 2},
    )
    assert first.status_code == 200
    assert len(first.json()["items"]) == 2
    assert first.json()["next_cursor"]
    second = client.get(
        f"/api/knowledge-bases/dataset-a/sources/{source['id']}/runs",
        headers=_headers(settings, "member-a"),
        params={"status": "failed", "trigger": "manual", "limit": 2, "cursor": first.json()["next_cursor"]},
    )
    assert second.status_code == 200
    assert {item["id"] for item in first.json()["items"]}.isdisjoint(
        {item["id"] for item in second.json()["items"]}
    )

    schema = app.openapi()
    assert "KnowledgeBearerAuth" in schema["components"]["securitySchemes"]
    operation = schema["paths"][
        "/api/knowledge-bases/{dataset_id}/sources/{source_id}/sync-now"
    ]["post"]
    assert operation["security"] == [{"KnowledgeBearerAuth": []}]
    assert {"202", "401", "403", "404", "409", "422", "503"} <= set(
        operation["responses"]
    )
    idempotency = next(
        parameter for parameter in operation["parameters"] if parameter["name"] == "Idempotency-Key"
    )
    assert idempotency["required"] is True
    assert "$ref" in operation["responses"]["202"]["content"]["application/json"]["schema"]



def test_sync_crash_after_commit_leaves_durable_pending_and_replay_dispatches_once(
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine, settings, submitted, _, root, _ = api
    source = _create_source(client, settings, root).json()
    url = f"/api/knowledge-bases/dataset-a/sources/{source['id']}/sync-now"
    headers = _headers(
        settings,
        "editor-a",
        idempotency_key="source-dispatch-crash-0001",
    )

    import server.knowledge_sources_api as source_api

    original_schedule = source_api.request_source_dispatch
    monkeypatch.setattr(source_api, "request_source_dispatch", lambda *_args: False)
    first = client.post(url, headers=headers, json={"force_full": False, "dry_run": False})
    assert first.status_code == 202
    with Session(engine) as session:
        run = session.get(SourceSyncRun, first.json()["run_id"])
        assert run is not None
        assert run.execution_state == "pending"
        assert run.execution_finished_at is None

    monkeypatch.setattr(source_api, "request_source_dispatch", original_schedule)
    replay = client.post(url, headers=headers, json={"force_full": False, "dry_run": False})
    second_replay = client.post(url, headers=headers, json={"force_full": False, "dry_run": False})
    assert replay.status_code == 202
    assert second_replay.status_code == 202
    assert replay.json()["run_id"] == first.json()["run_id"]
    assert replay.json()["execution_state"] == "pending"
    assert second_replay.json()["execution_state"] == "pending"
    assert len(submitted) == 1
    submitted.pop()()
    with Session(engine) as session:
        run = session.get(SourceSyncRun, first.json()["run_id"])
        assert run.execution_state == "completed"
        assert run.execution_attempts == 1
        assert run.execution_finished_at is not None


def test_dispatch_submit_failure_remains_durable_and_replay_recovers(api) -> None:
    client, engine, settings, submitted, _, root, app = api
    source = _create_source(client, settings, root).json()
    url = f"/api/knowledge-bases/dataset-a/sources/{source['id']}/sync-now"
    headers = _headers(settings, "editor-a", idempotency_key="source-dispatch-fail-0001")

    app.state.knowledge_source_task_submitter = lambda _task: (_ for _ in ()).throw(
        RuntimeError("queue offline")
    )
    first = client.post(url, headers=headers, json={"force_full": False, "dry_run": False})
    assert first.status_code == 202
    assert first.json()["execution_state"] == "pending"
    with Session(engine) as session:
        run = session.get(SourceSyncRun, first.json()["run_id"])
        assert run.execution_state == "pending"
        assert run.execution_attempts == 0
        assert run.execution_last_error == ""

    app.state.knowledge_source_task_submitter = submitted.append
    with Session(engine) as session:
        run = session.get(SourceSyncRun, first.json()["run_id"])
        run.reservation_lease_until = datetime.utcnow() - timedelta(seconds=1)
        session.commit()
    replay = client.post(url, headers=headers, json={"force_full": False, "dry_run": False})
    assert replay.status_code == 202
    assert replay.json()["execution_state"] == "pending"
    assert len(submitted) == 1


def test_idempotency_scope_includes_dataset_generation(api) -> None:
    client, engine, settings, submitted, _, root, _ = api
    source = _create_source(client, settings, root).json()
    url = f"/api/knowledge-bases/dataset-a/sources/{source['id']}/sync-now"
    headers = _headers(settings, "editor-a", idempotency_key="Dataset-Key-Exact-0001")
    first = client.post(url, headers=headers, json={"force_full": False, "dry_run": False})
    assert first.status_code == 202

    with Session(engine) as session:
        dataset = session.get(Dataset, "dataset-a")
        dataset.mutation_generation += 1
        session.commit()

    second = client.post(url, headers=headers, json={"force_full": False, "dry_run": False})
    assert second.status_code == 202
    assert second.json()["run_id"] != first.json()["run_id"]
    with Session(engine) as session:
        runs = list(
            session.scalars(
                select(SourceSyncRun).where(SourceSyncRun.idempotency_key == "Dataset-Key-Exact-0001")
            )
        )
        assert {run.dataset_generation for run in runs} == {0, 1}
    assert len(submitted) == 2


def test_uri_sanitizer_applies_to_uri_shaped_metadata_and_supports_file_uri(api) -> None:
    client, _, settings, _, _, root, _ = api
    created = _create_source(
        client,
        settings,
        root,
        name="uri-metadata",
        extra={
            "metadata": {
                "origin": "https://user:pass@example.test/docs?token=secret&lang=zh",
                "label": "https is a protocol, not a URI here",
                "local": "file:///C:/docs/guide.md",
            }
        },
    )
    assert created.status_code == 201
    metadata = created.json()["metadata"]
    assert metadata["origin"].startswith("https://[REDACTED]@example.test/docs?")
    assert "token=%5BREDACTED%5D" in metadata["origin"]
    assert "lang=zh" in metadata["origin"]
    assert metadata["label"] == "https is a protocol, not a URI here"
    assert metadata["local"] == "file:///C:/docs/guide.md"
    assert sanitize_source_uri("file:///var/lib/rag4c/guide.md") == (
        "file:///var/lib/rag4c/guide.md"
    )



def test_idempotency_keys_preserve_exact_case(api) -> None:
    client, _, settings, submitted, _, root, _ = api
    source = _create_source(client, settings, root).json()
    url = f"/api/knowledge-bases/dataset-a/sources/{source['id']}/sync-now"
    upper = client.post(
        url,
        headers=_headers(settings, "editor-a", idempotency_key="Case-Sensitive-Key-0001"),
        json={"force_full": False, "dry_run": False},
    )
    lower = client.post(
        url,
        headers=_headers(settings, "editor-a", idempotency_key="case-sensitive-key-0001"),
        json={"force_full": False, "dry_run": False},
    )
    assert upper.status_code == 202
    assert lower.status_code == 202
    assert upper.json()["run_id"] != lower.json()["run_id"]
    assert len(submitted) == 2
def test_synchronous_submitter_executes_safely_but_response_remains_durable_queued(api) -> None:
    client, engine, settings, _, executed, root, app = api
    source = _create_source(client, settings, root).json()
    app.state.knowledge_source_task_submitter = lambda task: task()

    response = client.post(
        f"/api/knowledge-bases/dataset-a/sources/{source['id']}/sync-now",
        headers=_headers(
            settings,
            "editor-a",
            idempotency_key="source-sync-synchronous-0001",
        ),
        json={"force_full": False, "dry_run": False},
    )
    assert response.status_code == 202
    assert response.json()["execution_state"] == "pending"
    assert executed and executed[0][0] == "handbook"
    with Session(engine) as session:
        run = session.get(SourceSyncRun, response.json()["run_id"])
        assert run.status == "completed"
        assert run.execution_state == "completed"
        assert run.execution_attempts == 1



def test_durable_reservation_blocks_duplicate_enqueue_across_app_instances(api) -> None:
    client, engine, settings, submitted, _, root, app = api
    source = _create_source(client, settings, root).json()
    url = f"/api/knowledge-bases/dataset-a/sources/{source['id']}/sync-now"
    headers = _headers(settings, "editor-a", idempotency_key="cross-replica-reserve-0001")
    first = client.post(url, headers=headers, json={"force_full": False, "dry_run": False})
    assert first.status_code == 202

    from server.source_dispatcher import request_source_dispatch

    replica = SimpleNamespace(
        state=SimpleNamespace(
            knowledge_auth_engine=engine,
            knowledge_auth_settings=settings,
            knowledge_source_task_submitter=submitted.append,
        )
    )
    assert request_source_dispatch(replica, first.json()["run_id"]) is False
    assert len(submitted) == 1
    with Session(engine) as session:
        run = session.get(SourceSyncRun, first.json()["run_id"])
        assert run.reservation_owner
        assert run.reservation_lease_until is not None



def test_max_execution_attempt_surfaces_terminal_failure_in_api(api) -> None:
    client, engine, settings, submitted, _, root, app = api
    settings.sources.execution_max_attempts = 1
    app.state.knowledge_source_sync_executor = lambda *_args: (_ for _ in ()).throw(
        RuntimeError("token=terminal-secret exhausted")
    )
    source = _create_source(client, settings, root).json()
    accepted = client.post(
        f"/api/knowledge-bases/dataset-a/sources/{source['id']}/sync-now",
        headers=_headers(settings, "editor-a", idempotency_key="Max-Attempt-API-0001"),
        json={"force_full": False, "dry_run": False},
    )
    assert accepted.status_code == 202
    submitted.pop()()
    detail = client.get(
        f"/api/knowledge-bases/dataset-a/sources/{source['id']}/runs/{accepted.json()['run_id']}",
        headers=_headers(settings, "member-a"),
    )
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["status"] == "failed"
    assert payload["execution_state"] == "completed"
    assert payload["finished_at"] is not None
    assert payload["fetch_error"]
    assert "terminal-secret" not in payload["fetch_error"]
    with Session(engine) as session:
        run = session.get(SourceSyncRun, accepted.json()["run_id"])
        assert run.execution_next_attempt_at is None
