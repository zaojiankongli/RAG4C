"""Stage 6 RED contracts for Dataset ACL grant mutations.

This module is intentionally test-only.  It defines the wished-for mutation
boundary before production code exists; the first run must be RED.  The
existing read-only access-graph router is expected to grow an explicit,
lazily-used ``mutation_engine_provider`` while retaining its explicit
``read_engine_provider``.

The contract is deliberately exercised through FastAPI and temporary SQLite
engines rather than through mocks of the mutation implementation.  Signed
Actor tokens, tenant headers, the real KnowledgeActor dependency, transaction
state, ORM rows, and TenantAuditEvent rows are all observable at the boundary.
No real database URL, migration, backup, or external service is used.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from config.settings import KnowledgeSecuritySettings, RunHistorySettings, TenantSettings
from models.orm import (
    Account,
    Base,
    Dataset,
    DatasetAccessGrant,
    DatasetAclMutationRequest,
    Tenant,
    TenantAuditEvent,
    TenantGroup,
    TenantGroupMember,
    TenantInvitation,
    TenantMember,
    TenantOrganizationUnit,
    TenantOrganizationUnitMember,
)
from server.knowledge_auth import issue_knowledge_actor_token

BASE_TIME = datetime(2026, 8, 26, 14, 0, 0)
DATASET_ID = "dataset-a"
MIGRATION_REQUIRED_CODE = "enterprise_access_graph_migration_required"

BASE_TABLES = [
    Tenant.__table__,
    Account.__table__,
    TenantMember.__table__,
    Dataset.__table__,
    TenantAuditEvent.__table__,
]
ACCESS_GRAPH_TABLES = [
    TenantOrganizationUnit.__table__,
    TenantGroup.__table__,
    TenantGroupMember.__table__,
    TenantInvitation.__table__,
    DatasetAccessGrant.__table__,
    DatasetAclMutationRequest.__table__,
]
ORGANIZATION_MEMBERSHIP_TABLES = [TenantOrganizationUnitMember.__table__]
WRITE_PREFIXES = frozenset(
    {
        "ALTER",
        "CREATE",
        "DELETE",
        "DROP",
        "INSERT",
        "REPLACE",
        "TRUNCATE",
        "UPDATE",
    }
)


@dataclass
class _Harness:
    """A real temporary catalog plus explicit read/write provider counters."""

    read_engine: Any
    write_engine: Any
    settings: SimpleNamespace
    read_calls: int = 0
    write_calls: int = 0
    read_statements: list[str] = field(default_factory=list)
    write_statements: list[str] = field(default_factory=list)

    def client(self) -> TestClient:
        """Build the app; a missing mutation provider is the intentional RED."""

        try:
            from server.enterprise_access_graph_api import (
                build_enterprise_access_graph_router,
            )
        except (ImportError, ModuleNotFoundError) as exc:
            raise AssertionError(
                "Stage 6 RED: Dataset ACL mutation router is not implemented: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        app = FastAPI()
        app.state.knowledge_auth_engine = self.read_engine
        app.state.knowledge_auth_settings = self.settings

        def read_provider() -> Any:
            self.read_calls += 1
            return self.read_engine

        def mutation_provider() -> Any:
            self.write_calls += 1
            return self.write_engine

        try:
            router = build_enterprise_access_graph_router(
                read_engine_provider=read_provider,
                mutation_engine_provider=mutation_provider,
            )
        except TypeError as exc:
            raise AssertionError(
                "Stage 6 RED: enterprise access-graph router must accept an "
                "explicit mutation_engine_provider and register Dataset ACL "
                f"mutation routes: {exc}"
            ) from exc

        app.include_router(router)
        return TestClient(app)

    def close(self) -> None:
        self.read_engine.dispose()
        if self.write_engine is not self.read_engine:
            self.write_engine.dispose()


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        run_history=RunHistorySettings(ops_bearer_token=SecretStr("ops-secret")),
        knowledge_security=KnowledgeSecuritySettings(
            actor_signing_secret=SecretStr("stage6-dataset-acl-mutation-secret"),
            actor_max_ttl_s=900,
        ),
        tenant=TenantSettings(enforced=True, default_tenant="tenant-a"),
    )


def _headers(
    settings: SimpleNamespace,
    actor_id: str,
    tenant_id: str = "tenant-a",
    *,
    request_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, str]:
    token = issue_knowledge_actor_token(
        actor_id,
        tenant_id,
        300,
        int(time.time()),
        settings=settings,
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-RAG4C-Tenant": tenant_id,
        "X-RAG4C-Actor": actor_id,
        "X-Request-ID": request_id or f"stage6-{actor_id}-{time.time_ns()}",
        "Idempotency-Key": idempotency_key or f"stage7-test-{time.time_ns()}",
    }


def _enable_sqlite_foreign_keys(engine: Any) -> None:
    @event.listens_for(engine, "connect")
    def _set_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def _seed_engine(
    *,
    access_graph: bool = True,
    organization_membership: bool = True,
) -> Any:
    """Create only the catalog tables required by this contract."""

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _enable_sqlite_foreign_keys(engine)
    tables = list(BASE_TABLES)
    if access_graph:
        tables.extend(ACCESS_GRAPH_TABLES)
        if organization_membership:
            tables.extend(ORGANIZATION_MEMBERSHIP_TABLES)
    Base.metadata.create_all(engine, tables=tables)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS dataset_release_manifests ("
                "tenant_id VARCHAR(64) NOT NULL, id VARCHAR(64) NOT NULL, "
                "UNIQUE (tenant_id, id))"
            )
        )
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)"))
        connection.execute(
            text("INSERT INTO alembic_version(version_num) VALUES ('0019_dataset_acl_control')")
        )

    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id="tenant-a", name="RAG4C Enterprise", status="active"),
                Tenant(id="tenant-b", name="Other Enterprise", status="active"),
                Account(
                    id="owner-a",
                    name="Owner A",
                    email="owner-a@example.test",
                    created_at=BASE_TIME,
                ),
                Account(
                    id="admin-a",
                    name="Admin A",
                    email="admin-a@example.test",
                    created_at=BASE_TIME,
                ),
                Account(
                    id="editor-a",
                    name="Editor A",
                    email="editor-a@example.test",
                    created_at=BASE_TIME,
                ),
                Account(
                    id="member-a",
                    name="Member A",
                    email="member-a@example.test",
                    created_at=BASE_TIME,
                ),
                Account(
                    id="member-b",
                    name="Member B",
                    email="member-b@example.test",
                    created_at=BASE_TIME,
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                TenantMember(
                    account_id="owner-a",
                    tenant_id="tenant-a",
                    role="owner",
                    status="active",
                    revision=1,
                    created_at=BASE_TIME,
                ),
                TenantMember(
                    account_id="admin-a",
                    tenant_id="tenant-a",
                    role="admin",
                    status="active",
                    revision=1,
                    created_at=BASE_TIME,
                ),
                TenantMember(
                    account_id="editor-a",
                    tenant_id="tenant-a",
                    role="editor",
                    status="active",
                    revision=1,
                    created_at=BASE_TIME,
                ),
                TenantMember(
                    account_id="member-a",
                    tenant_id="tenant-a",
                    role="member",
                    status="active",
                    revision=1,
                    created_at=BASE_TIME,
                ),
                TenantMember(
                    account_id="member-b",
                    tenant_id="tenant-b",
                    role="member",
                    status="active",
                    revision=1,
                    created_at=BASE_TIME,
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                Dataset(
                    id=DATASET_ID,
                    tenant_id="tenant-a",
                    name="制度知识库",
                    description="Stage 6 mutation fixture",
                    status="active",
                    owner_id="owner-a",
                    visibility="private",
                    acl_mode="dataset_acl" if access_graph else "tenant_role",
                    acl_revision=1,
                ),
                Dataset(
                    id="dataset-b",
                    tenant_id="tenant-b",
                    name="Other Dataset",
                    description="Cross-tenant sentinel",
                    status="active",
                    owner_id="member-b",
                    visibility="private",
                ),
            ]
        )
        if access_graph:
            session.add_all(
                [
                    TenantOrganizationUnit(
                        id="ou-a",
                        tenant_id="tenant-a",
                        code="engineering",
                        name="工程中心",
                        status="active",
                        revision=1,
                        created_at=BASE_TIME,
                    ),
                    TenantOrganizationUnit(
                        id="ou-archived-a",
                        tenant_id="tenant-a",
                        code="archived",
                        name="历史部门",
                        status="archived",
                        revision=1,
                        created_at=BASE_TIME,
                    ),
                    TenantOrganizationUnit(
                        id="ou-b",
                        tenant_id="tenant-b",
                        code="engineering",
                        name="Other Engineering",
                        status="active",
                        revision=1,
                        created_at=BASE_TIME,
                    ),
                    TenantOrganizationUnit(
                        id="ou-create-a",
                        tenant_id="tenant-a",
                        code="new-engineering",
                        name="新工程中心",
                        status="active",
                        revision=1,
                        created_at=BASE_TIME,
                    ),
                    TenantGroup(
                        id="group-a",
                        tenant_id="tenant-a",
                        name="工程组",
                        normalized_name="工程组",
                        status="active",
                        revision=1,
                        created_at=BASE_TIME,
                    ),
                    TenantGroup(
                        id="group-archived-a",
                        tenant_id="tenant-a",
                        name="历史组",
                        normalized_name="历史组",
                        status="archived",
                        revision=1,
                        created_at=BASE_TIME,
                    ),
                    TenantGroup(
                        id="group-b",
                        tenant_id="tenant-b",
                        name="Other Group",
                        normalized_name="other group",
                        status="active",
                        revision=1,
                        created_at=BASE_TIME,
                    ),
                    TenantGroup(
                        id="group-create-a",
                        tenant_id="tenant-a",
                        name="新工程组",
                        normalized_name="新工程组",
                        status="active",
                        revision=1,
                        created_at=BASE_TIME,
                    ),
                    TenantGroupMember(
                        id=1,
                        tenant_id="tenant-a",
                        group_id="group-a",
                        account_id="member-a",
                        status="active",
                        created_at=BASE_TIME,
                    ),
                ]
            )
            if organization_membership:
                session.add(
                    TenantOrganizationUnitMember(
                        id=1,
                        tenant_id="tenant-a",
                        organization_unit_id="ou-a",
                        account_id="member-a",
                        status="active",
                        revision=1,
                        created_at=BASE_TIME,
                    )
                )
            session.flush()
            session.add_all(
                [
                    DatasetAccessGrant(
                        id="grant-account-a",
                        tenant_id="tenant-a",
                        dataset_id=DATASET_ID,
                        subject_type="account",
                        subject_id="member-a",
                        role="viewer",
                        status="active",
                        revision=1,
                        created_at=BASE_TIME,
                    ),
                    DatasetAccessGrant(
                        id="grant-group-a",
                        tenant_id="tenant-a",
                        dataset_id=DATASET_ID,
                        subject_type="group",
                        subject_id="group-a",
                        role="editor",
                        status="active",
                        revision=1,
                        created_at=BASE_TIME,
                    ),
                    DatasetAccessGrant(
                        id="grant-revoked-a",
                        tenant_id="tenant-a",
                        dataset_id=DATASET_ID,
                        subject_type="account",
                        subject_id="editor-a",
                        role="editor",
                        status="revoked",
                        revision=4,
                        created_at=BASE_TIME,
                    ),
                ]
            )
        session.commit()
    return engine


def _trace_writes(engine: Any) -> list[str]:
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _trace(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        first_word = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else ""
        if first_word in WRITE_PREFIXES:
            statements.append(statement)

    return statements


def _harness(
    *,
    access_graph: bool = True,
    organization_membership: bool = True,
    separate_write_engine: bool = False,
) -> _Harness:
    read_engine = _seed_engine(
        access_graph=access_graph,
        organization_membership=organization_membership,
    )
    write_engine = (
        _seed_engine(
            access_graph=access_graph,
            organization_membership=organization_membership,
        )
        if separate_write_engine
        else read_engine
    )
    harness = _Harness(
        read_engine=read_engine,
        write_engine=write_engine,
        settings=_settings(),
    )
    harness.read_statements = _trace_writes(read_engine)
    if write_engine is read_engine:
        harness.write_statements = harness.read_statements
    else:
        harness.write_statements = _trace_writes(write_engine)
    return harness


@pytest.fixture()
def api() -> Any:
    harness = _harness()
    try:
        yield harness
    finally:
        harness.close()


def _grant(engine: Any, grant_id: str) -> dict[str, Any] | None:
    with Session(engine) as session:
        row = session.scalar(
            select(DatasetAccessGrant).where(
                DatasetAccessGrant.tenant_id == "tenant-a",
                DatasetAccessGrant.dataset_id == DATASET_ID,
                DatasetAccessGrant.id == grant_id,
            )
        )
        if row is None:
            return None
        return {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "dataset_id": row.dataset_id,
            "subject_type": row.subject_type,
            "subject_id": row.subject_id,
            "role": row.role,
            "status": row.status,
            "revision": row.revision,
        }


def _grant_count(engine: Any) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM dataset_access_grants "
                    "WHERE tenant_id = 'tenant-a' AND dataset_id = 'dataset-a'"
                )
            ).scalar_one()
        )


def _audit_rows(engine: Any) -> list[TenantAuditEvent]:
    with Session(engine) as session:
        return list(
            session.scalars(
                select(TenantAuditEvent)
                .where(TenantAuditEvent.tenant_id == "tenant-a")
                .order_by(TenantAuditEvent.sequence.asc())
            )
        )


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return json.loads(value)
    assert isinstance(value, dict)
    return value


def _assert_error(response: Any, status_code: int, code: str | None = None) -> None:
    assert response.status_code == status_code, response.text
    if code is not None:
        payload = response.json()
        assert payload["detail"]["code"] == code, response.text


def _assert_grant_payload(
    response: Any,
    *,
    grant_id: str,
    subject_type: str,
    subject_id: str,
    role: str,
    status: str,
    revision: int,
    status_code: int,
) -> None:
    assert response.status_code == status_code, response.text
    payload = response.json()["grant"]
    assert payload["id"] == grant_id
    assert payload["dataset_id"] == DATASET_ID
    assert payload["subject_type"] == subject_type
    assert payload["subject_id"] == subject_id
    assert payload["role"] == role
    assert payload["status"] == status
    assert payload["revision"] == revision


def _assert_audit_safe(row: TenantAuditEvent) -> None:
    serialized = json.dumps(
        {
            "actor_id": row.actor_id,
            "action": row.action,
            "resource_type": row.resource_type,
            "resource_id": row.resource_id,
            "before": row.before_snapshot,
            "after": row.after_snapshot,
        },
        ensure_ascii=False,
    )
    for forbidden in (
        "Authorization",
        "X-RAG4C-Tenant",
        "X-RAG4C-Actor",
        "token",
        "token_hash",
        "raw_token",
        "database_url",
        "mysql://",
    ):
        assert forbidden.casefold() not in serialized.casefold()


def test_dataset_acl_mutation_router_is_explicitly_split_from_read_engine(api: _Harness) -> None:
    """The router must expose both providers without touching writable state at build time."""

    client = api.client()

    assert client is not None
    assert api.write_calls == 0
    assert api.write_statements == []


def test_get_access_grants_never_calls_writable_provider(api: _Harness) -> None:
    client = api.client()

    response = client.get(
        f"/api/knowledge-bases/{DATASET_ID}/access-grants",
        headers=_headers(api.settings, "admin-a"),
        params={"limit": 20},
    )

    assert response.status_code == 200, response.text
    assert api.read_calls >= 1
    assert api.write_calls == 0
    assert api.write_statements == []


@pytest.mark.parametrize(
    ("subject_type", "subject_id", "role"),
    [
        ("account", "owner-a", "editor"),
        ("group", "group-create-a", "viewer"),
        ("organization_unit", "ou-create-a", "manager"),
    ],
)
def test_create_validates_subject_in_tenant_and_returns_revision_one(
    api: _Harness,
    subject_type: str,
    subject_id: str,
    role: str,
) -> None:
    client = api.client()
    before_grants = _grant_count(api.write_engine)

    response = client.post(
        f"/api/knowledge-bases/{DATASET_ID}/access-grants",
        headers=_headers(api.settings, "admin-a", request_id=f"create-{subject_type}"),
        json={
            "subject_type": subject_type,
            "subject_id": subject_id,
            "role": role,
            "reason": "阶段六授权变更",
        },
    )

    assert response.status_code == 201, response.text
    payload = response.json()["grant"]
    assert payload["id"]
    assert payload["dataset_id"] == DATASET_ID
    assert payload["subject_type"] == subject_type
    assert payload["subject_id"] == subject_id
    assert payload["role"] == role
    assert payload["status"] == "active"
    assert payload["revision"] == 1
    stored = _grant(api.write_engine, payload["id"])
    assert stored is not None
    for key in ("id", "dataset_id", "subject_type", "subject_id", "role", "status", "revision"):
        assert stored[key] == payload[key]
    assert _grant_count(api.write_engine) == before_grants + 1
    assert api.write_calls == 1
    rows = _audit_rows(api.write_engine)
    assert len(rows) == 1
    assert rows[0].action == "dataset_access_grant.created"
    assert _json_object(rows[0].after_snapshot)["id"] == payload["id"]


@pytest.mark.parametrize(
    ("subject_type", "subject_id"),
    [
        ("account", "member-b"),
        ("account", "account-does-not-exist"),
        ("group", "group-b"),
        ("group", "group-does-not-exist"),
        ("organization_unit", "ou-b"),
        ("organization_unit", "ou-does-not-exist"),
        ("group", "group-archived-a"),
        ("organization_unit", "ou-archived-a"),
    ],
)
def test_create_rejects_cross_tenant_missing_or_inactive_subjects_without_audit(
    api: _Harness,
    subject_type: str,
    subject_id: str,
) -> None:
    client = api.client()
    before_grants = _grant_count(api.write_engine)
    before_audits = len(_audit_rows(api.write_engine))

    response = client.post(
        f"/api/knowledge-bases/{DATASET_ID}/access-grants",
        headers=_headers(api.settings, "admin-a"),
        json={
            "subject_type": subject_type,
            "subject_id": subject_id,
            "role": "viewer",
            "reason": "无效主体不应写入",
        },
    )

    _assert_error(response, 404)
    assert _grant_count(api.write_engine) == before_grants
    assert len(_audit_rows(api.write_engine)) == before_audits


def test_create_duplicate_subject_returns_conflict_and_does_not_add_audit(api: _Harness) -> None:
    client = api.client()
    before_grants = _grant_count(api.write_engine)
    before_audits = len(_audit_rows(api.write_engine))

    response = client.post(
        f"/api/knowledge-bases/{DATASET_ID}/access-grants",
        headers=_headers(api.settings, "admin-a"),
        json={
            "subject_type": "account",
            "subject_id": "member-a",
            "role": "manager",
            "reason": "重复授权主体",
        },
    )

    _assert_error(response, 409)
    assert _grant_count(api.write_engine) == before_grants
    assert len(_audit_rows(api.write_engine)) == before_audits


def test_create_duplicate_revoked_subject_returns_conflict_instead_of_parallel_row(
    api: _Harness,
) -> None:
    client = api.client()
    before_grants = _grant_count(api.write_engine)
    before_audits = len(_audit_rows(api.write_engine))

    response = client.post(
        f"/api/knowledge-bases/{DATASET_ID}/access-grants",
        headers=_headers(api.settings, "admin-a"),
        json={
            "subject_type": "account",
            "subject_id": "editor-a",
            "role": "viewer",
            "reason": "撤销记录不能重复创建",
        },
    )

    _assert_error(response, 409)
    assert _grant_count(api.write_engine) == before_grants
    assert len(_audit_rows(api.write_engine)) == before_audits


def test_role_patch_is_revision_fenced_and_audited(api: _Harness) -> None:
    client = api.client()
    request_id = "stage6-role-update"

    response = client.patch(
        f"/api/knowledge-bases/{DATASET_ID}/access-grants/grant-account-a",
        headers=_headers(api.settings, "admin-a", request_id=request_id),
        json={
            "role": "manager",
            "expected_revision": 1,
            "reason": "职责升级",
        },
    )

    _assert_grant_payload(
        response,
        grant_id="grant-account-a",
        subject_type="account",
        subject_id="member-a",
        role="manager",
        status="active",
        revision=2,
        status_code=200,
    )
    rows = _audit_rows(api.write_engine)
    assert len(rows) == 1
    row = rows[0]
    assert row.action == "dataset_access_grant.role_updated"
    assert row.resource_type == "dataset_access_grant"
    assert row.resource_id == "grant-account-a"
    assert row.actor_id == "admin-a"
    assert row.request_id == request_id
    before = _json_object(row.before_snapshot)
    after = _json_object(row.after_snapshot)
    assert before["id"] == "grant-account-a"
    assert before["subject_type"] == "account"
    assert before["subject_id"] == "member-a"
    assert before["role"] == "viewer"
    assert before["status"] == "active"
    assert before["revision"] == 1
    assert after["role"] == "manager"
    assert after["status"] == "active"
    assert after["revision"] == 2
    assert after["reason"] == "职责升级"
    _assert_audit_safe(row)


def test_revoke_changes_active_to_revoked_and_increments_revision(api: _Harness) -> None:
    client = api.client()

    response = client.post(
        f"/api/knowledge-bases/{DATASET_ID}/access-grants/grant-account-a/revoke",
        headers=_headers(api.settings, "admin-a", request_id="stage6-revoke"),
        json={"expected_revision": 1, "reason": "账号离开项目"},
    )

    _assert_grant_payload(
        response,
        grant_id="grant-account-a",
        subject_type="account",
        subject_id="member-a",
        role="viewer",
        status="revoked",
        revision=2,
        status_code=200,
    )
    rows = _audit_rows(api.write_engine)
    assert len(rows) == 1
    assert rows[0].action == "dataset_access_grant.revoked"
    assert _json_object(rows[0].before_snapshot)["status"] == "active"
    assert _json_object(rows[0].after_snapshot)["status"] == "revoked"
    assert _json_object(rows[0].after_snapshot)["revision"] == 2
    assert _json_object(rows[0].after_snapshot)["reason"] == "账号离开项目"
    _assert_audit_safe(rows[0])


def test_resume_preserves_role_when_role_is_omitted_and_increments_revision(api: _Harness) -> None:
    client = api.client()

    response = client.post(
        f"/api/knowledge-bases/{DATASET_ID}/access-grants/grant-revoked-a/resume",
        headers=_headers(api.settings, "admin-a"),
        json={"expected_revision": 4, "reason": "重新加入项目"},
    )

    _assert_grant_payload(
        response,
        grant_id="grant-revoked-a",
        subject_type="account",
        subject_id="editor-a",
        role="editor",
        status="active",
        revision=5,
        status_code=200,
    )
    rows = _audit_rows(api.write_engine)
    assert len(rows) == 1
    assert rows[0].action == "dataset_access_grant.resumed"
    assert _json_object(rows[0].before_snapshot)["status"] == "revoked"
    assert _json_object(rows[0].before_snapshot)["revision"] == 4
    assert _json_object(rows[0].after_snapshot)["role"] == "editor"
    assert _json_object(rows[0].after_snapshot)["status"] == "active"
    assert _json_object(rows[0].after_snapshot)["revision"] == 5


def test_resume_can_change_role_and_increments_revision(api: _Harness) -> None:
    client = api.client()

    response = client.post(
        f"/api/knowledge-bases/{DATASET_ID}/access-grants/grant-revoked-a/resume",
        headers=_headers(api.settings, "admin-a"),
        json={
            "role": "manager",
            "expected_revision": 4,
            "reason": "恢复并提升权限",
        },
    )

    _assert_grant_payload(
        response,
        grant_id="grant-revoked-a",
        subject_type="account",
        subject_id="editor-a",
        role="manager",
        status="active",
        revision=5,
        status_code=200,
    )
    row = _audit_rows(api.write_engine)[0]
    assert _json_object(row.after_snapshot)["role"] == "manager"
    assert _json_object(row.after_snapshot)["reason"] == "恢复并提升权限"


def test_stale_revision_returns_409_and_writes_no_state_or_audit(api: _Harness) -> None:
    client = api.client()
    before_grant = _grant(api.write_engine, "grant-account-a")
    before_audits = len(_audit_rows(api.write_engine))

    response = client.patch(
        f"/api/knowledge-bases/{DATASET_ID}/access-grants/grant-account-a",
        headers=_headers(api.settings, "admin-a"),
        json={
            "role": "manager",
            "expected_revision": 99,
            "reason": "过期页面提交",
        },
    )

    _assert_error(response, 409)
    assert _grant(api.write_engine, "grant-account-a") == before_grant
    assert len(_audit_rows(api.write_engine)) == before_audits


def test_revoke_active_grant_twice_returns_state_conflict_without_second_audit(
    api: _Harness,
) -> None:
    client = api.client()
    first = client.post(
        f"/api/knowledge-bases/{DATASET_ID}/access-grants/grant-account-a/revoke",
        headers=_headers(api.settings, "admin-a"),
        json={"expected_revision": 1, "reason": "第一次撤销"},
    )
    assert first.status_code == 200, first.text
    before = _grant(api.write_engine, "grant-account-a")
    before_audits = len(_audit_rows(api.write_engine))

    second = client.post(
        f"/api/knowledge-bases/{DATASET_ID}/access-grants/grant-account-a/revoke",
        headers=_headers(api.settings, "admin-a"),
        json={"expected_revision": 2, "reason": "重复撤销"},
    )

    _assert_error(second, 409)
    assert _grant(api.write_engine, "grant-account-a") == before
    assert len(_audit_rows(api.write_engine)) == before_audits


def test_resume_active_grant_returns_state_conflict_without_audit(api: _Harness) -> None:
    client = api.client()
    before = _grant(api.write_engine, "grant-account-a")
    before_audits = len(_audit_rows(api.write_engine))

    response = client.post(
        f"/api/knowledge-bases/{DATASET_ID}/access-grants/grant-account-a/resume",
        headers=_headers(api.settings, "admin-a"),
        json={"expected_revision": 1, "reason": "不能恢复 active 授权"},
    )

    _assert_error(response, 409)
    assert _grant(api.write_engine, "grant-account-a") == before
    assert len(_audit_rows(api.write_engine)) == before_audits


def test_unknown_grant_returns_404_and_does_not_create_audit(api: _Harness) -> None:
    client = api.client()
    before_audits = len(_audit_rows(api.write_engine))

    response = client.patch(
        f"/api/knowledge-bases/{DATASET_ID}/access-grants/not-a-real-grant",
        headers=_headers(api.settings, "admin-a"),
        json={"role": "viewer", "expected_revision": 1, "reason": "不存在资源"},
    )

    _assert_error(response, 404)
    assert len(_audit_rows(api.write_engine)) == before_audits


def test_dataset_resolver_blocks_cross_tenant_dataset_before_writable_provider(
    api: _Harness,
) -> None:
    client = api.client()

    response = client.post(
        "/api/knowledge-bases/dataset-b/access-grants",
        headers=_headers(api.settings, "admin-a"),
        json={
            "subject_type": "account",
            "subject_id": "editor-a",
            "role": "viewer",
            "reason": "跨租户不应写入",
        },
    )

    _assert_error(response, 403, "knowledge_dataset_scope_forbidden")
    assert api.write_calls == 0
    assert api.write_statements == []


def test_missing_or_invalid_actor_is_401_and_never_calls_writable_provider(api: _Harness) -> None:
    client = api.client()
    valid = _headers(api.settings, "admin-a")

    missing = client.post(
        f"/api/knowledge-bases/{DATASET_ID}/access-grants",
        headers={"X-RAG4C-Tenant": "tenant-a", "X-RAG4C-Actor": "admin-a"},
        json={
            "subject_type": "account",
            "subject_id": "editor-a",
            "role": "viewer",
            "reason": "缺少 token",
        },
    )
    invalid = client.post(
        f"/api/knowledge-bases/{DATASET_ID}/access-grants",
        headers={**valid, "Authorization": "Bearer not-a-valid-token"},
        json={
            "subject_type": "account",
            "subject_id": "editor-a",
            "role": "viewer",
            "reason": "无效 token",
        },
    )

    _assert_error(missing, 401)
    _assert_error(invalid, 401)
    assert api.write_calls == 0
    assert api.write_statements == []


def test_non_manager_actor_is_403_and_never_calls_writable_provider(api: _Harness) -> None:
    client = api.client()
    before_grants = _grant_count(api.write_engine)
    before_audits = len(_audit_rows(api.write_engine))

    response = client.post(
        f"/api/knowledge-bases/{DATASET_ID}/access-grants",
        headers=_headers(api.settings, "member-a"),
        json={
            "subject_type": "account",
            "subject_id": "editor-a",
            "role": "viewer",
            "reason": "viewer 不能管理 ACL",
        },
    )

    _assert_error(response, 403)
    assert api.write_calls == 0
    assert _grant_count(api.write_engine) == before_grants
    assert len(_audit_rows(api.write_engine)) == before_audits


def test_dataset_acl_manager_grant_can_authorize_mutation_without_tenant_admin_role(
    api: _Harness,
) -> None:
    with Session(api.read_engine) as session:
        grant = session.scalar(
            select(DatasetAccessGrant).where(
                DatasetAccessGrant.id == "grant-revoked-a",
                DatasetAccessGrant.tenant_id == "tenant-a",
                DatasetAccessGrant.dataset_id == DATASET_ID,
            )
        )
        assert grant is not None
        grant.role = "manager"
        grant.status = "active"
        grant.revision = 5
        session.commit()

    client = api.client()
    response = client.post(
        f"/api/knowledge-bases/{DATASET_ID}/access-grants",
        headers=_headers(api.settings, "editor-a"),
        json={
            "subject_type": "organization_unit",
            "subject_id": "ou-create-a",
            "role": "viewer",
            "reason": "Dataset manager 变更",
        },
    )

    assert response.status_code == 201, response.text
    created = response.json()["grant"]
    assert created["subject_type"] == "organization_unit"
    assert created["subject_id"] == "ou-create-a"
    assert created["role"] == "viewer"
    assert created["status"] == "active"
    assert created["revision"] == 1
    stored = _grant(api.write_engine, created["id"])
    assert stored is not None
    for key in ("id", "dataset_id", "subject_type", "subject_id", "role", "status", "revision"):
        assert stored[key] == created[key]


def test_mutation_re_evaluates_dataset_manage_inside_writable_transaction() -> None:
    api = _harness(separate_write_engine=True)
    try:
        with Session(api.write_engine) as session:
            session.execute(
                text(
                    "UPDATE tenant_members SET role = 'member' "
                    "WHERE tenant_id = 'tenant-a' AND account_id = 'admin-a'"
                )
            )
            session.commit()
        api.write_statements.clear()

        client = api.client()
        before_grants = _grant_count(api.write_engine)
        response = client.post(
            f"/api/knowledge-bases/{DATASET_ID}/access-grants",
            headers=_headers(api.settings, "admin-a"),
            json={
                "subject_type": "account",
                "subject_id": "editor-a",
                "role": "viewer",
                "reason": "写事务内应再次鉴权",
            },
        )

        _assert_error(response, 403)
        assert api.write_calls == 1
        assert _grant_count(api.write_engine) == before_grants
        assert _audit_rows(api.write_engine) == []
    finally:
        api.close()


def test_audit_failure_rolls_back_grant_and_does_not_leave_partial_state(api: _Harness) -> None:
    def reject_audit(_mapper: Any, _connection: Any, _target: TenantAuditEvent) -> None:
        raise RuntimeError("audit sink intentionally unavailable")

    event.listen(TenantAuditEvent, "before_insert", reject_audit)
    try:
        client = api.client()
        response = client.post(
            f"/api/knowledge-bases/{DATASET_ID}/access-grants",
            headers=_headers(api.settings, "admin-a"),
            json={
                "subject_type": "account",
                "subject_id": "owner-a",
                "role": "viewer",
                "reason": "审计失败必须回滚",
            },
        )
    finally:
        event.remove(TenantAuditEvent, "before_insert", reject_audit)

    _assert_error(response, 503)
    assert _grant_count(api.write_engine) == 3
    assert _audit_rows(api.write_engine) == []


@pytest.mark.parametrize(
    ("access_graph", "organization_membership"),
    [(False, False), (True, False)],
)
def test_missing_0017_or_0018_fails_closed_with_migration_required_and_no_audit(
    access_graph: bool,
    organization_membership: bool,
) -> None:
    api = _harness(
        access_graph=access_graph,
        organization_membership=organization_membership,
    )
    try:
        client = api.client()
        response = client.post(
            f"/api/knowledge-bases/{DATASET_ID}/access-grants",
            headers=_headers(api.settings, "admin-a"),
            json={
                "subject_type": "account",
                "subject_id": "owner-a",
                "role": "viewer",
                "reason": "迁移前拒绝写入",
            },
        )

        _assert_error(response, 503, MIGRATION_REQUIRED_CODE)
        assert api.write_calls == 1
        assert api.write_statements == []
    finally:
        api.close()


def test_unavailable_writable_engine_returns_503_without_any_write() -> None:
    api = _harness()
    original = api.write_engine
    api.write_engine = object()
    try:
        client = api.client()
        response = client.post(
            f"/api/knowledge-bases/{DATASET_ID}/access-grants",
            headers=_headers(api.settings, "admin-a"),
            json={
                "subject_type": "account",
                "subject_id": "editor-a",
                "role": "viewer",
                "reason": "可写引擎不可用",
            },
        )

        _assert_error(response, 503)
        assert api.write_calls == 1
        assert api.write_statements == []
        assert _grant_count(original) == 3
        assert _audit_rows(original) == []
    finally:
        api.write_engine = original
        api.close()


@pytest.mark.parametrize(
    ("method", "suffix", "body", "action", "grant_id"),
    [
        (
            "patch",
            "grant-account-a",
            {"role": "editor", "expected_revision": 1, "reason": "变更角色"},
            "dataset_access_grant.role_updated",
            "grant-account-a",
        ),
        (
            "post",
            "grant-account-a/revoke",
            {"expected_revision": 1, "reason": "撤销授权"},
            "dataset_access_grant.revoked",
            "grant-account-a",
        ),
        (
            "post",
            "grant-revoked-a/resume",
            {"expected_revision": 4, "reason": "恢复授权"},
            "dataset_access_grant.resumed",
            "grant-revoked-a",
        ),
    ],
)
def test_each_successful_mutation_has_one_action_specific_audit_event(
    api: _Harness,
    method: str,
    suffix: str,
    body: dict[str, Any],
    action: str,
    grant_id: str,
) -> None:
    client = api.client()
    request = getattr(client, method)
    response = request(
        f"/api/knowledge-bases/{DATASET_ID}/access-grants/{suffix}",
        headers=_headers(api.settings, "admin-a", request_id=f"audit-{method}-{suffix}"),
        json=body,
    )

    assert response.status_code == 200, response.text
    rows = _audit_rows(api.write_engine)
    assert len(rows) == 1
    assert rows[0].action == action
    assert rows[0].resource_type == "dataset_access_grant"
    assert rows[0].resource_id == grant_id
    assert rows[0].before_snapshot is not None
    assert rows[0].after_snapshot is not None
    _assert_audit_safe(rows[0])


def test_body_reason_and_role_are_validated_before_state_change(api: _Harness) -> None:
    client = api.client()
    before_grant = _grant(api.write_engine, "grant-account-a")
    before_audits = len(_audit_rows(api.write_engine))

    blank_reason = client.patch(
        f"/api/knowledge-bases/{DATASET_ID}/access-grants/grant-account-a",
        headers=_headers(api.settings, "admin-a"),
        json={"role": "manager", "expected_revision": 1, "reason": "   "},
    )
    invalid_role = client.patch(
        f"/api/knowledge-bases/{DATASET_ID}/access-grants/grant-account-a",
        headers=_headers(api.settings, "admin-a"),
        json={"role": "superuser", "expected_revision": 1, "reason": "非法角色"},
    )

    assert blank_reason.status_code == 422, blank_reason.text
    assert invalid_role.status_code == 422, invalid_role.text
    assert _grant(api.write_engine, "grant-account-a") == before_grant
    assert len(_audit_rows(api.write_engine)) == before_audits
