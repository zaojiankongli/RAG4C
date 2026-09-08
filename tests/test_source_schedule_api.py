from __future__ import annotations

import json
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
from models.orm import (
    Account,
    Base,
    DataSourceRecord,
    Dataset,
    KnowledgeAuditEvent,
    SourceSchedule,
    Tenant,
    TenantMember,
)
from server.knowledge_auth import issue_knowledge_actor_token
from server.knowledge_sources_api import router


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        run_history=RunHistorySettings(ops_bearer_token=SecretStr("ops-secret")),
        knowledge_security=KnowledgeSecuritySettings(
            actor_signing_secret=SecretStr("schedule-api-secret"), actor_max_ttl_s=900
        ),
        tenant=TenantSettings(enforced=True, default_tenant="tenant-a"),
        sources=SimpleNamespace(),
    )


def _headers(
    settings: SimpleNamespace,
    actor: str,
    tenant: str = "tenant-a",
    request_id: str = "schedule-request",
) -> dict[str, str]:
    token = issue_knowledge_actor_token(actor, tenant, 300, int(time.time()), settings=settings)
    return {
        "Authorization": f"Bearer {token}",
        "X-RAG4C-Tenant": tenant,
        "X-Request-ID": request_id,
    }


@pytest.fixture()
def api(monkeypatch: pytest.MonkeyPatch):
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
                Dataset(id="dataset-a2", tenant_id="tenant-a", name="Dataset A2", status="active"),
                Dataset(id="dataset-b", tenant_id="tenant-b", name="Dataset B", status="active"),
                Account(id="admin-a", name="Admin", email="admin-a-schedule@example.test"),
                Account(id="editor-a", name="Editor", email="editor-a-schedule@example.test"),
                Account(id="member-a", name="Member", email="member-a-schedule@example.test"),
                Account(id="admin-b", name="Admin B", email="admin-b-schedule@example.test"),
                TenantMember(account_id="admin-a", tenant_id="tenant-a", role="admin"),
                TenantMember(account_id="editor-a", tenant_id="tenant-a", role="editor"),
                TenantMember(account_id="member-a", tenant_id="tenant-a", role="member"),
                TenantMember(account_id="admin-b", tenant_id="tenant-b", role="admin"),
                DataSourceRecord(
                    id="source-a",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    name="Source A",
                    source_type="local_dir",
                    status="active",
                ),
                DataSourceRecord(
                    id="source-b",
                    tenant_id="tenant-b",
                    dataset_id="dataset-b",
                    name="Source B",
                    source_type="local_dir",
                    status="active",
                ),
            ]
        )
        session.commit()
    settings = _settings()
    monkeypatch.setattr(catalog, "get_engine", lambda: engine)
    app = FastAPI()
    app.state.knowledge_auth_engine = engine
    app.state.knowledge_auth_settings = settings
    app.include_router(router)
    yield TestClient(app, client=("10.0.0.2", 50000)), engine, settings, app
    engine.dispose()


def _schedule_url(dataset_id: str = "dataset-a", source_id: str = "source-a") -> str:
    return f"/api/knowledge-bases/{dataset_id}/sources/{source_id}/schedule"


def _put_body(
    expected_revision: int = 0,
    interval_seconds: int = 300,
    force_full: bool = False,
    status: str | None = None,
) -> dict[str, object]:
    body: dict[str, object] = {
        "expected_revision": expected_revision,
        "interval_seconds": interval_seconds,
        "force_full": force_full,
    }
    if status is not None:
        body["status"] = status
    return body


def test_schedule_read_manage_rbac_and_scope_are_fail_closed(api) -> None:
    client, _, settings, _ = api
    assert client.get(
        _schedule_url(), headers=_headers(settings, "member-a")
    ).status_code == 404
    for actor in ("member-a", "editor-a"):
        assert client.put(
            _schedule_url(), headers=_headers(settings, actor), json=_put_body()
        ).status_code == 403

    created = client.put(
        _schedule_url(),
        headers=_headers(settings, "admin-a", request_id="schedule-create"),
        json=_put_body(interval_seconds=600, force_full=True),
    )
    assert created.status_code == 200
    payload = created.json()
    assert payload["tenant_id"] == "tenant-a"
    assert payload["dataset_id"] == "dataset-a"
    assert payload["source_id"] == "source-a"
    assert payload["revision"] == 1
    assert payload["status"] == "active"
    assert payload["interval_seconds"] == 600
    assert payload["force_full"] is True
    assert payload["capability"] == "fixed_interval_utc"
    assert payload["last_enqueued_at"] is None
    assert payload["last_run_id"] is None

    assert client.get(
        _schedule_url(), headers=_headers(settings, "member-a")
    ).json() == payload
    assert client.get(
        _schedule_url(dataset_id="dataset-a2"),
        headers=_headers(settings, "admin-a"),
    ).status_code == 404
    assert client.get(
        _schedule_url(dataset_id="dataset-b", source_id="source-b"),
        headers=_headers(settings, "admin-a"),
    ).status_code == 403
    assert client.get(
        _schedule_url(dataset_id="dataset-a", source_id="source-b"),
        headers=_headers(settings, "admin-a"),
    ).status_code == 404


def test_schedule_put_is_strict_fixed_interval_cas(api) -> None:
    client, _, settings, _ = api
    invalid_bodies = [
        {**_put_body(), "dry_run": True},
        _put_body(interval_seconds=299),
        _put_body(interval_seconds=604801),
        {**_put_body(), "force_full": 1},
        _put_body(status="archived"),
        {"expected_revision": 0, "interval_seconds": 300},
    ]
    for index, body in enumerate(invalid_bodies):
        response = client.put(
            _schedule_url(),
            headers=_headers(settings, "admin-a", request_id=f"invalid-{index}"),
            json=body,
        )
        assert response.status_code == 422, response.text

    created = client.put(
        _schedule_url(), headers=_headers(settings, "admin-a"), json=_put_body()
    )
    assert created.status_code == 200
    stale = client.put(
        _schedule_url(),
        headers=_headers(settings, "admin-a", request_id="stale"),
        json=_put_body(expected_revision=0, interval_seconds=900),
    )
    assert stale.status_code == 409
    updated = client.put(
        _schedule_url(),
        headers=_headers(settings, "admin-a", request_id="update"),
        json=_put_body(expected_revision=1, interval_seconds=900, status="paused"),
    )
    assert updated.status_code == 200
    assert (updated.json()["revision"], updated.json()["status"]) == (2, "paused")


def test_pause_resume_and_delete_archive_with_revision_cas(api) -> None:
    client, engine, settings, _ = api
    created = client.put(
        _schedule_url(), headers=_headers(settings, "admin-a"), json=_put_body()
    ).json()
    paused = client.post(
        _schedule_url() + "/pause",
        headers=_headers(settings, "admin-a", request_id="pause"),
        json={"expected_revision": created["revision"]},
    )
    assert paused.status_code == 200
    assert (paused.json()["status"], paused.json()["revision"]) == ("paused", 2)
    assert client.post(
        _schedule_url() + "/resume",
        headers=_headers(settings, "admin-a", request_id="stale-resume"),
        json={"expected_revision": 1},
    ).status_code == 409
    resumed = client.post(
        _schedule_url() + "/resume",
        headers=_headers(settings, "admin-a", request_id="resume"),
        json={"expected_revision": 2},
    )
    assert resumed.status_code == 200
    assert (resumed.json()["status"], resumed.json()["revision"]) == ("active", 3)
    archived = client.request(
        "DELETE",
        _schedule_url(),
        headers=_headers(settings, "admin-a", request_id="archive"),
        json={"expected_revision": 3},
    )
    assert archived.status_code == 200
    assert (archived.json()["status"], archived.json()["revision"]) == ("archived", 4)
    with Session(engine) as session:
        row = session.scalar(select(SourceSchedule).where(SourceSchedule.source_id == "source-a"))
        assert row is not None and row.status == "archived"
        actions = list(
            session.scalars(
                select(KnowledgeAuditEvent.action).where(
                    KnowledgeAuditEvent.resource_id == row.id
                ).order_by(KnowledgeAuditEvent.sequence)
            )
        )
    assert actions == [
        "source.schedule.created",
        "source.schedule.paused",
        "source.schedule.resumed",
        "source.schedule.archived",
    ]


def test_schedule_inactive_gates_and_safe_audit(api) -> None:
    client, engine, settings, _ = api
    with Session(engine) as session:
        session.get(DataSourceRecord, "source-a").status = "disabled"
        session.commit()
    blocked = client.put(
        _schedule_url(),
        headers=_headers(settings, "admin-a", request_id="inactive-source"),
        json=_put_body(),
    )
    assert blocked.status_code == 409

    with Session(engine) as session:
        session.get(DataSourceRecord, "source-a").status = "active"
        session.get(Dataset, "dataset-a").status = "disabled"
        session.commit()
    blocked_dataset = client.put(
        _schedule_url(),
        headers=_headers(settings, "admin-a", request_id="inactive-dataset"),
        json=_put_body(),
    )
    assert blocked_dataset.status_code == 403
    with Session(engine) as session:
        assert session.scalar(select(SourceSchedule)) is None
        serialized = json.dumps(
            [
                {"before": row.before_snapshot, "after": row.after_snapshot}
                for row in session.scalars(select(KnowledgeAuditEvent))
            ],
            sort_keys=True,
        ).casefold()
    assert "authorization" not in serialized
    assert "bearer" not in serialized
    assert "schedule-api-secret" not in serialized


def test_schedule_openapi_is_typed_and_explicitly_fixed_interval(api) -> None:
    client, _, _, app = api
    schema = app.openapi()
    base = "/api/knowledge-bases/{dataset_id}/sources/{source_id}/schedule"
    assert set(schema["paths"][base]) >= {"get", "put", "delete"}
    assert "post" in schema["paths"][base + "/pause"]
    assert "post" in schema["paths"][base + "/resume"]
    put_schema = schema["paths"][base]["put"]
    assert "200" in put_schema["responses"]
    assert {"401", "403", "404", "409", "422", "503"} <= set(put_schema["responses"])
    encoded = json.dumps(schema, sort_keys=True).casefold()
    assert "fixed_interval_utc" in encoded
    assert "interval_seconds" in encoded
    assert "cron" not in encoded
    assert "calendar" not in encoded
    assert client.get("/openapi.json").status_code == 200
