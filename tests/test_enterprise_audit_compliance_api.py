from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import importlib
import json
from pathlib import Path
import time
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from config.settings import KnowledgeSecuritySettings, RunHistorySettings, TenantSettings
from models.orm import Account, Base, Tenant, TenantAuditEvent, TenantMember
from server.knowledge_auth import issue_knowledge_actor_token

REVISION = "0023_enterprise_audit_compliance"
TENANT = "tenant-compliance-a"
NOW = datetime(2026, 8, 26, 12, 0, 0)
CONFIRMATION = "DELETE AUDIT EVENTS"
SETTINGS = SimpleNamespace(
    run_history=RunHistorySettings(ops_bearer_token=SecretStr("ops-secret")),
    knowledge_security=KnowledgeSecuritySettings(
        actor_signing_secret=SecretStr("stage11-compliance-secret"),
        actor_max_ttl_s=900,
    ),
    tenant=TenantSettings(enforced=True, default_tenant=TENANT),
)


def _enable_sqlite(engine: Any) -> None:
    @event.listens_for(engine, "connect")
    def _configure(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


def _install_0023(engine: Any, *, revision: str = REVISION) -> None:
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        for table in (
            "tenant_audit_export_jobs",
            "tenant_audit_legal_holds",
            "tenant_audit_retention_policies",
        ):
            connection.execute(text(f"DROP TABLE IF EXISTS {table}"))
        connection.execute(
            text(
                """
                CREATE TABLE tenant_audit_retention_policies (
                    id VARCHAR(64) NOT NULL PRIMARY KEY,
                    tenant_id VARCHAR(64) NOT NULL,
                    audit_retention_days INTEGER NOT NULL,
                    export_retention_days INTEGER NOT NULL,
                    status VARCHAR(16) NOT NULL,
                    revision INTEGER NOT NULL,
                    last_preview_at DATETIME,
                    last_execution_at DATETIME,
                    last_executed_by VARCHAR(64),
                    created_at DATETIME NOT NULL,
                    created_by VARCHAR(64) NOT NULL,
                    updated_at DATETIME NOT NULL,
                    updated_by VARCHAR(64) NOT NULL,
                    CONSTRAINT uq_tenant_audit_retention_policies_tenant UNIQUE (tenant_id),
                    CONSTRAINT ck_tenant_audit_retention_audit_days
                        CHECK (audit_retention_days BETWEEN 30 AND 3650),
                    CONSTRAINT ck_tenant_audit_retention_export_days
                        CHECK (export_retention_days BETWEEN 1 AND 365),
                    CONSTRAINT ck_tenant_audit_retention_status
                        CHECK (status IN ('active','paused')),
                    CONSTRAINT fk_tenant_audit_retention_tenant
                        FOREIGN KEY (tenant_id) REFERENCES tenants (id)
                )
                """
            )
        )
        connection.execute(
            text(
                "CREATE INDEX ix_tenant_audit_retention_status "
                "ON tenant_audit_retention_policies (tenant_id,status)"
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE tenant_audit_legal_holds (
                    id VARCHAR(64) NOT NULL PRIMARY KEY,
                    tenant_id VARCHAR(64) NOT NULL,
                    name VARCHAR(128) NOT NULL,
                    active_name_key VARCHAR(128),
                    reason VARCHAR(512) NOT NULL,
                    status VARCHAR(16) NOT NULL,
                    sequence_from INTEGER,
                    sequence_to INTEGER,
                    time_from DATETIME,
                    time_to DATETIME,
                    revision INTEGER NOT NULL,
                    created_at DATETIME NOT NULL,
                    created_by VARCHAR(64) NOT NULL,
                    released_at DATETIME,
                    released_by VARCHAR(64),
                    updated_at DATETIME NOT NULL,
                    updated_by VARCHAR(64) NOT NULL,
                    CONSTRAINT uq_tenant_audit_legal_holds_active_name
                        UNIQUE (tenant_id,active_name_key),
                    CONSTRAINT ck_tenant_audit_legal_holds_status
                        CHECK (status IN ('active','released')),
                    CONSTRAINT fk_tenant_audit_legal_holds_tenant
                        FOREIGN KEY (tenant_id) REFERENCES tenants (id)
                )
                """
            )
        )
        connection.execute(
            text(
                "CREATE INDEX ix_tenant_audit_legal_holds_status "
                "ON tenant_audit_legal_holds (tenant_id,status,updated_at,id)"
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE tenant_audit_export_jobs (
                    id VARCHAR(64) NOT NULL PRIMARY KEY,
                    tenant_id VARCHAR(64) NOT NULL,
                    format VARCHAR(16) NOT NULL,
                    status VARCHAR(16) NOT NULL,
                    filters_json JSON NOT NULL,
                    sequence_from INTEGER,
                    sequence_to INTEGER,
                    time_from DATETIME,
                    time_to DATETIME,
                    object_key VARCHAR(512),
                    sha256 VARCHAR(64),
                    byte_size INTEGER,
                    row_count INTEGER,
                    revision INTEGER NOT NULL,
                    requested_at DATETIME NOT NULL,
                    requested_by VARCHAR(64) NOT NULL,
                    completed_at DATETIME,
                    expires_at DATETIME,
                    CONSTRAINT ck_tenant_audit_export_jobs_format
                        CHECK (format IN ('ndjson','csv')),
                    CONSTRAINT ck_tenant_audit_export_jobs_status
                        CHECK (status IN ('queued','running','completed','failed','expired')),
                    CONSTRAINT fk_tenant_audit_export_jobs_tenant
                        FOREIGN KEY (tenant_id) REFERENCES tenants (id)
                )
                """
            )
        )
        connection.execute(
            text(
                "CREATE INDEX ix_tenant_audit_export_jobs_status "
                "ON tenant_audit_export_jobs (tenant_id,status,requested_at,id)"
            )
        )
        connection.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(64) NOT NULL)")
        )
        connection.execute(text("DELETE FROM alembic_version"))
        connection.execute(
            text("INSERT INTO alembic_version(version_num) VALUES (:revision)"),
            {"revision": revision},
        )
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")


def _engine(tmp_path: Path, *, revision: str = REVISION) -> Any:
    engine = create_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'compliance.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    _enable_sqlite(engine)
    _install_0023(engine, revision=revision)
    with Session(engine) as session:
        session.add(Tenant(id=TENANT, name="Compliance Tenant", status="active"))
        session.add_all(
            [
                Account(id="owner", name="Owner", email="owner@example.test"),
                Account(id="admin", name="Admin", email="admin@example.test"),
                Account(id="editor", name="Editor", email="editor@example.test"),
                Account(id="member", name="Member", email="member@example.test"),
            ]
        )
        session.flush()
        for actor_id, role in (
            ("owner", "owner"),
            ("admin", "admin"),
            ("editor", "editor"),
            ("member", "member"),
        ):
            session.add(
                TenantMember(
                    tenant_id=TENANT,
                    account_id=actor_id,
                    role=role,
                    status="active",
                    revision=1,
                    updated_by="owner",
                )
            )
        session.flush()
        session.execute(
            text(
                """
                INSERT INTO tenant_audit_retention_policies (
                    id,tenant_id,audit_retention_days,export_retention_days,status,revision,
                    created_at,created_by,updated_at,updated_by
                ) VALUES ('policy-a',:tenant,30,30,'active',1,:now,'owner',:now,'owner')
                """
            ),
            {"tenant": TENANT, "now": NOW},
        )
        old = NOW - timedelta(days=60)
        recent = NOW - timedelta(days=2)
        events = [
            ("old-delete-1", "document.deleted", "document", "doc-1", old),
            ("old-delete-2", "member.removed", "tenant_member", "member-x", old),
            ("old-protected", "security.incident", "dataset", "dataset-1", old),
            ("recent-formula", '=HYPERLINK("https://evil")', "document", "doc-2", recent),
            ("recent-normal", "dataset.updated", "dataset", "dataset-2", recent),
        ]
        for event_id, action, resource_type, resource_id, occurred_at in events:
            session.add(
                TenantAuditEvent(
                    id=event_id,
                    tenant_id=TENANT,
                    actor_id="owner",
                    actor_name_snapshot="Owner",
                    actor_email_snapshot="owner@example.test",
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    target_account_id=None,
                    before_snapshot={"before": "value"},
                    after_snapshot={"after": "value"},
                    request_id=f"request-{event_id}",
                    request_ip="127.0.0.1",
                    occurred_at=occurred_at,
                )
            )
        session.flush()
        protected_sequence = session.scalar(
            text("SELECT sequence FROM tenant_audit_events WHERE id='old-protected'")
        )
        session.execute(
            text(
                """
                INSERT INTO tenant_audit_legal_holds (
                    id,tenant_id,name,active_name_key,reason,status,
                    sequence_from,sequence_to,time_from,time_to,
                    revision,created_at,created_by,updated_at,updated_by
                ) VALUES (
                    'hold-seeded',:tenant,'Incident hold','incident hold','investigation','active',
                    :sequence,:sequence,NULL,NULL,1,:now,'owner',:now,'owner'
                )
                """
            ),
            {"tenant": TENANT, "sequence": protected_sequence, "now": NOW},
        )
        session.commit()
    return engine


def _headers(actor_id: str, *, key: str = "stage11-key-0001") -> dict[str, str]:
    token = issue_knowledge_actor_token(
        actor_id,
        TENANT,
        300,
        int(time.time()),
        settings=SETTINGS,
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-RAG4C-Tenant": TENANT,
        "X-RAG4C-Actor": actor_id,
        "X-Request-ID": f"stage11-{actor_id}-{time.time_ns()}",
        "Idempotency-Key": key,
    }


def _client(engine: Any, export_root: Path) -> TestClient:
    app = FastAPI()
    app.state.knowledge_auth_engine = engine
    app.state.knowledge_auth_settings = SETTINGS
    try:
        module = importlib.import_module("server.enterprise_compliance_api")
    except ModuleNotFoundError:
        return TestClient(app)
    app.include_router(
        module.build_enterprise_compliance_router(
            read_engine_provider=lambda: engine,
            mutation_engine_provider=lambda: engine,
            export_root_provider=lambda: export_root,
            now_provider=lambda: NOW,
        )
    )
    return TestClient(app)


def _rows(engine: Any, table: str) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(text(f"SELECT * FROM {table}")).mappings()]


def _preview(client: TestClient, actor: str = "admin") -> Any:
    return client.post(
        "/api/enterprise/compliance/retention/preview",
        headers=_headers(actor, key="preview-unused"),
        json={},
    )


def _create_export(
    client: TestClient,
    *,
    format_name: str = "ndjson",
    filters: dict[str, str] | None = None,
    key: str = "stage11-export-create",
) -> Any:
    return client.post(
        "/api/enterprise/compliance/audit-exports",
        headers=_headers("admin", key=key),
        json={
            "format": format_name,
            "filters": filters or {},
            "expires_in_days": 7,
            "reason": "compliance export",
        },
    )


def test_policy_read_update_role_revision_and_idempotency(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    client = _client(engine, tmp_path / "exports")
    readable = client.get(
        "/api/enterprise/compliance/retention-policy",
        headers=_headers("member"),
    )
    assert readable.status_code == 200, readable.text
    assert readable.json()["policy"]["revision"] == 1
    forbidden = client.put(
        "/api/enterprise/compliance/retention-policy",
        headers=_headers("admin", key="policy-admin"),
        json={
            "audit_retention_days": 90,
            "export_retention_days": 14,
            "status": "active",
            "revision": 1,
            "reason": "policy update",
        },
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"]["code"] == "compliance_owner_required"
    body = {
        "audit_retention_days": 90,
        "export_retention_days": 14,
        "status": "active",
        "revision": 1,
        "reason": "policy update",
    }
    first = client.put(
        "/api/enterprise/compliance/retention-policy",
        headers=_headers("owner", key="policy-owner"),
        json=body,
    )
    replay = client.put(
        "/api/enterprise/compliance/retention-policy",
        headers=_headers("owner", key="policy-owner"),
        json=body,
    )
    conflict = client.put(
        "/api/enterprise/compliance/retention-policy",
        headers=_headers("owner", key="policy-owner"),
        json={**body, "audit_retention_days": 91},
    )
    assert first.status_code == 200, first.text
    assert first.json()["policy"]["revision"] == 2
    assert replay.json() == first.json()
    assert conflict.status_code == 409


def test_preview_counts_candidates_protected_rows_and_stable_fingerprint(tmp_path: Path) -> None:
    client = _client(_engine(tmp_path), tmp_path / "exports")
    first = _preview(client)
    second = _preview(client)
    assert first.status_code == 200, first.text
    assert first.json()["preview"]["candidate_count"] == 2
    assert first.json()["preview"]["protected_count"] == 1
    assert first.json()["preview"]["fingerprint"] == second.json()["preview"]["fingerprint"]
    assert first.json()["execution"]["mode"] == "manual_execution_only"
    assert _preview(client, "editor").status_code == 403


def test_execute_is_owner_only_fingerprint_fenced_audited_and_replayed(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    client = _client(engine, tmp_path / "exports")
    preview = _preview(client).json()["preview"]
    body = {
        "revision": 1,
        "preview_fingerprint": preview["fingerprint"],
        "confirmation": CONFIRMATION,
        "reason": "approved retention run",
    }
    admin = client.post(
        "/api/enterprise/compliance/retention/execute",
        headers=_headers("admin", key="execute-admin"),
        json=body,
    )
    assert admin.status_code == 403
    wrong = client.post(
        "/api/enterprise/compliance/retention/execute",
        headers=_headers("owner", key="execute-wrong"),
        json={**body, "preview_fingerprint": "0" * 64},
    )
    assert wrong.status_code == 409
    first = client.post(
        "/api/enterprise/compliance/retention/execute",
        headers=_headers("owner", key="execute-owner"),
        json=body,
    )
    replay = client.post(
        "/api/enterprise/compliance/retention/execute",
        headers=_headers("owner", key="execute-owner"),
        json=body,
    )
    assert first.status_code == 200, first.text
    assert first.json()["execution"]["deleted_count"] == 2
    assert first.json()["execution"]["protected_count"] == 1
    assert replay.json() == first.json()
    remaining_ids = {row["id"] for row in _rows(engine, "tenant_audit_events")}
    assert "old-protected" in remaining_ids
    assert "old-delete-1" not in remaining_ids
    assert "old-delete-2" not in remaining_ids
    assert (
        sum(
            row["action"] == "compliance.retention.executed"
            for row in _rows(engine, "tenant_audit_events")
        )
        == 1
    )


def test_legal_hold_create_list_release_owner_boundaries(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    client = _client(engine, tmp_path / "exports")
    body = {
        "name": "Quarterly investigation",
        "reason": "case-2026-08",
        "sequence_from": 1,
        "sequence_to": 100,
    }
    created = client.post(
        "/api/enterprise/compliance/legal-holds",
        headers=_headers("owner", key="hold-create"),
        json=body,
    )
    replay = client.post(
        "/api/enterprise/compliance/legal-holds",
        headers=_headers("owner", key="hold-create"),
        json=body,
    )
    assert created.status_code == 201, created.text
    assert replay.json() == created.json()
    duplicate = client.post(
        "/api/enterprise/compliance/legal-holds",
        headers=_headers("owner", key="hold-duplicate"),
        json={**body, "reason": "other"},
    )
    assert duplicate.status_code == 409
    listed = client.get(
        "/api/enterprise/compliance/legal-holds",
        headers=_headers("member"),
    )
    assert listed.status_code == 200
    hold = created.json()["legal_hold"]
    forbidden = client.post(
        f"/api/enterprise/compliance/legal-holds/{hold['id']}/release",
        headers=_headers("admin", key="hold-release-admin"),
        json={"revision": 1, "reason": "release"},
    )
    released = client.post(
        f"/api/enterprise/compliance/legal-holds/{hold['id']}/release",
        headers=_headers("owner", key="hold-release-owner"),
        json={"revision": 1, "reason": "release"},
    )
    assert forbidden.status_code == 403
    assert released.status_code == 200, released.text
    assert released.json()["legal_hold"]["status"] == "released"


def test_ndjson_export_is_atomic_hashed_redacted_and_downloadable(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    export_root = tmp_path / "exports"
    client = _client(engine, export_root)
    first = _create_export(client)
    replay = _create_export(client)
    assert first.status_code == 201, first.text
    assert replay.json() == first.json()
    job = first.json()["export"]
    assert job["status"] == "completed"
    assert len(job["sha256"]) == 64
    assert job["row_count"] == 5
    serialized = json.dumps(first.json())
    assert str(export_root) not in serialized
    assert "object_key" not in serialized
    download = client.get(
        f"/api/enterprise/compliance/audit-exports/{job['id']}/download",
        headers=_headers("admin"),
    )
    assert download.status_code == 200, download.text
    assert hashlib.sha256(download.content).hexdigest() == job["sha256"]
    lines = [json.loads(line) for line in download.text.splitlines()]
    assert len(lines) == 5
    assert "tenant_id" not in lines[0]
    assert "request_ip" not in lines[0]


def test_csv_export_escapes_spreadsheet_formula_prefixes(tmp_path: Path) -> None:
    client = _client(_engine(tmp_path), tmp_path / "exports")
    created = _create_export(
        client,
        format_name="csv",
        filters={"resource_type": "document"},
        key="csv-export",
    )
    assert created.status_code == 201, created.text
    job = created.json()["export"]
    download = client.get(
        f"/api/enterprise/compliance/audit-exports/{job['id']}/download",
        headers=_headers("owner"),
    )
    assert download.status_code == 200
    assert "'=HYPERLINK" in download.text
    assert download.headers["content-type"].startswith("text/csv")


def test_export_download_rejects_corruption_and_path_escape(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    root = tmp_path / "exports"
    client = _client(engine, root)
    created = _create_export(client, key="corrupt-export")
    job = created.json()["export"]
    row = next(
        item for item in _rows(engine, "tenant_audit_export_jobs") if item["id"] == job["id"]
    )
    path = root / row["object_key"]
    path.write_bytes(path.read_bytes() + b"corrupt")
    corrupt = client.get(
        f"/api/enterprise/compliance/audit-exports/{job['id']}/download",
        headers=_headers("owner"),
    )
    assert corrupt.status_code == 409
    assert corrupt.json()["detail"]["code"] == "compliance_export_integrity_failed"
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE tenant_audit_export_jobs SET object_key='../escape.ndjson' WHERE id=:id"),
            {"id": job["id"]},
        )
    escaped = client.get(
        f"/api/enterprise/compliance/audit-exports/{job['id']}/download",
        headers=_headers("owner"),
    )
    assert escaped.status_code == 409
    assert escaped.json()["detail"]["code"] == "compliance_export_integrity_failed"


def test_export_filters_are_allowlisted_and_do_not_create_files(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    root = tmp_path / "exports"
    client = _client(engine, root)
    response = _create_export(
        client,
        filters={"where": "1=1; DROP TABLE tenant_audit_events"},
        key="bad-filter",
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "compliance_filter_invalid"
    assert _rows(engine, "tenant_audit_export_jobs") == []
    assert not root.exists() or list(root.rglob("*")) == []


def test_audit_failure_rolls_back_policy_and_idempotency(tmp_path: Path, monkeypatch: Any) -> None:
    engine = _engine(tmp_path)
    module = importlib.import_module("core.enterprise_compliance")

    def fail_audit(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(module, "_audit", fail_audit)
    response = _client(engine, tmp_path / "exports").put(
        "/api/enterprise/compliance/retention-policy",
        headers=_headers("owner", key="audit-failure"),
        json={
            "audit_retention_days": 120,
            "export_retention_days": 14,
            "status": "active",
            "revision": 1,
            "reason": "update",
        },
    )
    assert response.status_code == 503
    policy = _rows(engine, "tenant_audit_retention_policies")[0]
    assert policy["revision"] == 1
    assert policy["audit_retention_days"] == 30
    assert [
        row
        for row in _rows(engine, "tenant_control_mutation_requests")
        if row["operation"].startswith("compliance.")
    ] == []


def test_missing_0023_fails_closed_before_mutation(tmp_path: Path) -> None:
    engine = _engine(tmp_path, revision="0022_scim_provisioning_data_plane")
    client = _client(engine, tmp_path / "exports")
    response = client.put(
        "/api/enterprise/compliance/retention-policy",
        headers=_headers("owner", key="missing-0023"),
        json={
            "audit_retention_days": 90,
            "export_retention_days": 14,
            "status": "active",
            "revision": 1,
            "reason": "update",
        },
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "compliance_migration_required"
    assert _rows(engine, "tenant_audit_retention_policies")[0]["revision"] == 1
