"""Stage 5 RED contracts for dataset-scoped ACL enforcement.

This file is intentionally a contract-only test seam.  It does not implement
the evaluator or change the existing KnowledgeActor dependency.  The first
run is expected to be RED until the following production seams exist:

* ``core.enterprise_access_control.evaluate_dataset_permissions`` with the
  five-argument signature used below;
* dataset-aware enforcement inside ``require_knowledge_permission``;
* the dataset access-summary projection exposing the same server-owned
  decision evidence.

The evaluator contract returns a mapping with these fields:

``enforcement_mode``
    Either ``dataset_acl`` or ``tenant_role_fallback``.
``matched_grants``
    A list of matching grant identifiers.  A bypass has no matching grant and
    therefore returns an empty list.
``effective_permissions``
    A deterministic list of KnowledgeOps permission strings.
``warnings``
    A list of server-owned diagnostic strings; clients must not infer an
    enforcement mode from the visible grant rows.

Only temporary SQLite engines are created here.  Actor identity is supplied
through the existing signed Bearer-token issuer; no real database or external
service is used.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from config.settings import KnowledgeSecuritySettings, RunHistorySettings, TenantSettings
from core import catalog
from core.knowledge_permissions import (
    KNOWLEDGE_AUDIT,
    KNOWLEDGE_DELETE,
    KNOWLEDGE_MANAGE,
    KNOWLEDGE_READ,
    KNOWLEDGE_WRITE,
)
from models.orm import (
    Account,
    Base,
    Dataset,
    DatasetAccessGrant,
    Tenant,
    TenantGroup,
    TenantGroupMember,
    TenantMember,
    TenantOrganizationUnit,
    TenantOrganizationUnitMember,
)
from server.knowledge_auth import issue_knowledge_actor_token


FULL_PERMISSIONS = frozenset(
    {
        KNOWLEDGE_READ,
        KNOWLEDGE_WRITE,
        KNOWLEDGE_DELETE,
        KNOWLEDGE_MANAGE,
        KNOWLEDGE_AUDIT,
    }
)
ROLE_PERMISSIONS = {
    "owner": FULL_PERMISSIONS,
    "admin": FULL_PERMISSIONS,
    "editor": frozenset({KNOWLEDGE_READ, KNOWLEDGE_WRITE, KNOWLEDGE_DELETE}),
    "member": frozenset({KNOWLEDGE_READ}),
}
GRANT_PERMISSIONS = {
    "viewer": frozenset({KNOWLEDGE_READ}),
    "editor": frozenset({KNOWLEDGE_READ, KNOWLEDGE_WRITE, KNOWLEDGE_DELETE}),
    "manager": FULL_PERMISSIONS,
}

DATASET_ACL = "dataset_acl"
TENANT_ROLE_FALLBACK = "tenant_role_fallback"
DATASET_ID = "dataset-a"

BASE_TABLES = [
    Tenant.__table__,
    Account.__table__,
    TenantMember.__table__,
    Dataset.__table__,
]
ACCESS_GRAPH_TABLES = [
    TenantOrganizationUnit.__table__,
    TenantOrganizationUnitMember.__table__,
    TenantGroup.__table__,
    TenantGroupMember.__table__,
    DatasetAccessGrant.__table__,
]


@dataclass(frozen=True)
class _Contract:
    evaluator: Callable[..., Mapping[str, Any]]


def _load_contract() -> _Contract:
    """Load the exact production symbol and preserve a focused RED reason."""

    try:
        from core.enterprise_access_control import evaluate_dataset_permissions
    except (ImportError, ModuleNotFoundError) as exc:
        pytest.fail(
            f"Stage 5 RED: dataset ACL evaluator is not implemented: {type(exc).__name__}: {exc}"
        )

    if not callable(evaluate_dataset_permissions):
        pytest.fail(
            "Stage 5 RED: core.enterprise_access_control.evaluate_dataset_permissions "
            "must be callable"
        )
    return _Contract(evaluator=evaluate_dataset_permissions)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        run_history=RunHistorySettings(ops_bearer_token=SecretStr("ops-secret")),
        knowledge_security=KnowledgeSecuritySettings(
            actor_signing_secret=SecretStr("stage5-dataset-acl-secret"),
            actor_max_ttl_s=900,
        ),
        tenant=TenantSettings(enforced=True, default_tenant="tenant-a"),
    )


def _headers(
    settings: SimpleNamespace,
    account_id: str,
    tenant_id: str = "tenant-a",
) -> dict[str, str]:
    token = issue_knowledge_actor_token(
        account_id,
        tenant_id,
        300,
        int(time.time()),
        settings=settings,
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-RAG4C-Tenant": tenant_id,
        "X-RAG4C-Actor": account_id,
        "X-Request-ID": "req-stage5-dataset-acl",
    }


def _engine(*, with_access_graph: bool) -> Any:
    """Create an isolated catalog with no production connection path."""

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=BASE_TABLES)
    if with_access_graph:
        Base.metadata.create_all(engine, tables=ACCESS_GRAPH_TABLES)
    revision = "0018_organization_membership" if with_access_graph else "0016_enterprise_membership"
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)"))
        connection.execute(
            text("INSERT INTO alembic_version(version_num) VALUES (:revision)"),
            {"revision": revision},
        )

    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id="tenant-a", name="Tenant A", status="active"),
                Tenant(id="tenant-b", name="Tenant B", status="active"),
                Account(id="owner-a", name="Owner A", email="owner-a@example.test"),
                Account(id="admin-a", name="Admin A", email="admin-a@example.test"),
                Account(id="editor-a", name="Editor A", email="editor-a@example.test"),
                Account(id="member-a", name="Member A", email="member-a@example.test"),
                Account(id="viewer-a", name="Viewer A", email="viewer-a@example.test"),
                Account(
                    id="org-member-a",
                    name="Org Member A",
                    email="org-member-a@example.test",
                ),
                Account(id="owner-b", name="Owner B", email="owner-b@example.test"),
                Account(id="member-b", name="Member B", email="member-b@example.test"),
                TenantMember(account_id="owner-a", tenant_id="tenant-a", role="owner"),
                TenantMember(account_id="admin-a", tenant_id="tenant-a", role="admin"),
                TenantMember(account_id="editor-a", tenant_id="tenant-a", role="editor"),
                TenantMember(account_id="member-a", tenant_id="tenant-a", role="member"),
                TenantMember(account_id="viewer-a", tenant_id="tenant-a", role="member"),
                TenantMember(
                    account_id="org-member-a",
                    tenant_id="tenant-a",
                    role="member",
                ),
                TenantMember(account_id="owner-b", tenant_id="tenant-b", role="owner"),
                TenantMember(account_id="member-b", tenant_id="tenant-b", role="member"),
                Dataset(
                    id=DATASET_ID,
                    tenant_id="tenant-a",
                    name="Dataset A",
                    description="Stage 5 ACL contract fixture",
                    status="active",
                    owner_id="owner-a",
                    visibility="private",
                ),
                Dataset(
                    id="dataset-owner-editor",
                    tenant_id="tenant-a",
                    name="Dataset Owned By Editor",
                    status="active",
                    owner_id="editor-a",
                    visibility="private",
                ),
                Dataset(
                    id="dataset-b",
                    tenant_id="tenant-b",
                    name="Dataset B",
                    status="active",
                    owner_id="owner-b",
                    visibility="private",
                ),
            ]
        )
        if with_access_graph:
            session.add_all(
                [
                    TenantOrganizationUnit(
                        id="unit-a",
                        tenant_id="tenant-a",
                        name="Engineering",
                        code="engineering",
                        status="active",
                    ),
                    TenantOrganizationUnit(
                        id="unit-b",
                        tenant_id="tenant-b",
                        name="Other Engineering",
                        code="other-engineering",
                        status="active",
                    ),
                    TenantGroup(
                        id="group-a",
                        tenant_id="tenant-a",
                        name="Platform",
                        normalized_name="platform",
                        status="active",
                    ),
                    TenantGroup(
                        id="group-b",
                        tenant_id="tenant-b",
                        name="Other Platform",
                        normalized_name="other-platform",
                        status="active",
                    ),
                    TenantGroupMember(
                        tenant_id="tenant-a",
                        group_id="group-a",
                        account_id="member-a",
                        status="active",
                    ),
                    TenantGroupMember(
                        tenant_id="tenant-b",
                        group_id="group-b",
                        account_id="member-b",
                        status="active",
                    ),
                    TenantOrganizationUnitMember(
                        tenant_id="tenant-a",
                        organization_unit_id="unit-a",
                        account_id="org-member-a",
                        status="active",
                    ),
                    TenantOrganizationUnitMember(
                        tenant_id="tenant-b",
                        organization_unit_id="unit-b",
                        account_id="member-b",
                        status="active",
                    ),
                ]
            )
        session.commit()
    return engine


def _grant(
    engine: Any,
    *,
    grant_id: str,
    tenant_id: str = "tenant-a",
    dataset_id: str = DATASET_ID,
    subject_type: str = "account",
    subject_id: str = "member-a",
    role: str = "viewer",
    status: str = "active",
) -> None:
    with Session(engine) as session:
        session.add(
            DatasetAccessGrant(
                id=grant_id,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                subject_type=subject_type,
                subject_id=subject_id,
                role=role,
                status=status,
            )
        )
        if status == "active":
            dataset = session.get(Dataset, dataset_id)
            assert dataset is not None
            dataset.acl_mode = "dataset_acl"
            dataset.acl_revision = max(int(dataset.acl_revision or 1), 1)
        session.commit()


def _result(result: Mapping[str, Any]) -> Mapping[str, Any]:
    assert isinstance(result, Mapping)
    assert result["enforcement_mode"] in {DATASET_ACL, TENANT_ROLE_FALLBACK}
    assert isinstance(result["matched_grants"], list)
    assert isinstance(result["effective_permissions"], list)
    assert isinstance(result["warnings"], list)
    return result


def _permissions(result: Mapping[str, Any]) -> frozenset[str]:
    return frozenset(str(permission) for permission in result["effective_permissions"])


def _evaluate(
    contract: _Contract,
    engine: Any,
    *,
    account_id: str = "member-a",
    tenant_role: str = "member",
    dataset_id: str = DATASET_ID,
) -> Mapping[str, Any]:
    return _result(
        contract.evaluator(
            engine,
            "tenant-a",
            account_id,
            tenant_role,
            dataset_id,
        )
    )


def test_old_schema_without_access_graph_keeps_existing_tenant_role_permissions() -> None:
    contract = _load_contract()
    engine = _engine(with_access_graph=False)

    result = _evaluate(contract, engine, account_id="editor-a", tenant_role="editor")

    assert result["enforcement_mode"] == TENANT_ROLE_FALLBACK
    assert result["matched_grants"] == []
    assert _permissions(result) == ROLE_PERMISSIONS["editor"]
    assert result["warnings"]


def test_access_graph_without_active_grant_uses_tenant_role_fallback() -> None:
    contract = _load_contract()
    engine = _engine(with_access_graph=True)

    result = _evaluate(contract, engine, account_id="editor-a", tenant_role="editor")

    assert result["enforcement_mode"] == TENANT_ROLE_FALLBACK
    assert result["matched_grants"] == []
    assert _permissions(result) == ROLE_PERMISSIONS["editor"]


def test_first_active_grant_opts_dataset_into_acl_and_unmatched_actor_is_denied() -> None:
    contract = _load_contract()
    engine = _engine(with_access_graph=True)
    _grant(
        engine,
        grant_id="grant-unrelated-viewer",
        subject_id="viewer-a",
        role="viewer",
    )

    result = _evaluate(contract, engine, account_id="member-a", tenant_role="editor")

    assert result["enforcement_mode"] == DATASET_ACL
    assert result["matched_grants"] == []
    assert _permissions(result) == frozenset()


@pytest.mark.parametrize(
    ("account_id", "tenant_role", "dataset_id"),
    (
        ("owner-a", "owner", DATASET_ID),
        ("admin-a", "admin", DATASET_ID),
        ("editor-a", "editor", "dataset-owner-editor"),
    ),
)
def test_dataset_owner_and_tenant_owner_admin_have_manager_bypass(
    account_id: str,
    tenant_role: str,
    dataset_id: str,
) -> None:
    contract = _load_contract()
    engine = _engine(with_access_graph=True)
    _grant(
        engine,
        grant_id=f"grant-bypass-trigger-{account_id}",
        dataset_id=dataset_id,
        subject_id="viewer-a",
        role="viewer",
    )

    result = _evaluate(
        contract,
        engine,
        account_id=account_id,
        tenant_role=tenant_role,
        dataset_id=dataset_id,
    )

    assert result["enforcement_mode"] == DATASET_ACL
    assert result["matched_grants"] == []
    assert _permissions(result) == FULL_PERMISSIONS


@pytest.mark.parametrize(
    ("grant_role", "expected"),
    (
        ("viewer", frozenset({KNOWLEDGE_READ})),
        (
            "editor",
            frozenset({KNOWLEDGE_READ, KNOWLEDGE_WRITE, KNOWLEDGE_DELETE}),
        ),
        ("manager", FULL_PERMISSIONS),
    ),
)
def test_direct_account_grant_maps_to_the_fixed_permission_matrix(
    grant_role: str,
    expected: frozenset[str],
) -> None:
    contract = _load_contract()
    engine = _engine(with_access_graph=True)
    grant_id = f"grant-direct-{grant_role}"
    _grant(
        engine,
        grant_id=grant_id,
        subject_type="account",
        subject_id="member-a",
        role=grant_role,
    )

    result = _evaluate(contract, engine, account_id="member-a", tenant_role="member")

    assert result["enforcement_mode"] == DATASET_ACL
    assert result["matched_grants"] == [grant_id]
    assert _permissions(result) == expected


def test_active_group_grant_applies_only_through_active_group_membership() -> None:
    contract = _load_contract()
    engine = _engine(with_access_graph=True)
    _grant(
        engine,
        grant_id="grant-group-editor",
        subject_type="group",
        subject_id="group-a",
        role="editor",
    )

    result = _evaluate(contract, engine, account_id="member-a", tenant_role="member")

    assert result["enforcement_mode"] == DATASET_ACL
    assert result["matched_grants"] == ["grant-group-editor"]
    assert _permissions(result) == GRANT_PERMISSIONS["editor"]


def test_removed_group_membership_does_not_apply_an_active_group_grant() -> None:
    contract = _load_contract()
    engine = _engine(with_access_graph=True)
    _grant(
        engine,
        grant_id="grant-group-removed",
        subject_type="group",
        subject_id="group-a",
        role="manager",
    )
    with Session(engine) as session:
        session.query(TenantGroupMember).filter_by(
            tenant_id="tenant-a",
            group_id="group-a",
            account_id="member-a",
        ).update({"status": "removed"})
        session.commit()

    result = _evaluate(contract, engine, account_id="member-a", tenant_role="member")

    assert result["enforcement_mode"] == DATASET_ACL
    assert result["matched_grants"] == []
    assert _permissions(result) == frozenset()


def test_active_organization_unit_grant_applies_only_through_active_membership() -> None:
    contract = _load_contract()
    engine = _engine(with_access_graph=True)
    _grant(
        engine,
        grant_id="grant-org-manager",
        subject_type="organization_unit",
        subject_id="unit-a",
        role="manager",
    )

    result = _evaluate(
        contract,
        engine,
        account_id="org-member-a",
        tenant_role="member",
    )

    assert result["enforcement_mode"] == DATASET_ACL
    assert result["matched_grants"] == ["grant-org-manager"]
    assert _permissions(result) == FULL_PERMISSIONS


def test_removed_organization_unit_membership_does_not_apply_an_active_grant() -> None:
    contract = _load_contract()
    engine = _engine(with_access_graph=True)
    _grant(
        engine,
        grant_id="grant-org-removed",
        subject_type="organization_unit",
        subject_id="unit-a",
        role="editor",
    )
    with Session(engine) as session:
        session.query(TenantOrganizationUnitMember).filter_by(
            tenant_id="tenant-a",
            organization_unit_id="unit-a",
            account_id="org-member-a",
        ).update({"status": "removed"})
        session.commit()

    result = _evaluate(
        contract,
        engine,
        account_id="org-member-a",
        tenant_role="member",
    )

    assert result["enforcement_mode"] == DATASET_ACL
    assert result["matched_grants"] == []
    assert _permissions(result) == frozenset()


def test_revoked_grant_does_not_opt_a_dataset_into_acl_mode() -> None:
    contract = _load_contract()
    engine = _engine(with_access_graph=True)
    _grant(
        engine,
        grant_id="grant-revoked",
        subject_type="account",
        subject_id="member-a",
        role="manager",
        status="revoked",
    )

    result = _evaluate(contract, engine, account_id="member-a", tenant_role="member")

    assert result["enforcement_mode"] == TENANT_ROLE_FALLBACK
    assert result["matched_grants"] == []
    assert _permissions(result) == ROLE_PERMISSIONS["member"]


def test_multiple_matching_grants_take_the_union_of_their_permissions() -> None:
    contract = _load_contract()
    engine = _engine(with_access_graph=True)
    _grant(
        engine,
        grant_id="grant-union-direct-viewer",
        subject_type="account",
        subject_id="member-a",
        role="viewer",
    )
    _grant(
        engine,
        grant_id="grant-union-group-editor",
        subject_type="group",
        subject_id="group-a",
        role="editor",
    )

    result = _evaluate(contract, engine, account_id="member-a", tenant_role="member")

    assert result["enforcement_mode"] == DATASET_ACL
    assert set(result["matched_grants"]) == {
        "grant-union-direct-viewer",
        "grant-union-group-editor",
    }
    assert _permissions(result) == GRANT_PERMISSIONS["editor"]


def test_cross_tenant_dataset_grant_cannot_elevate_the_current_tenant_actor() -> None:
    contract = _load_contract()
    engine = _engine(with_access_graph=True)
    # Deliberately seed an integrity-damaged row with SQLite FK enforcement
    # disabled.  The tenant and dataset predicates must both be enforced by
    # the evaluator rather than trusting a subject id alone.
    _grant(
        engine,
        grant_id="grant-cross-tenant-dataset",
        tenant_id="tenant-b",
        dataset_id=DATASET_ID,
        subject_type="account",
        subject_id="member-a",
        role="manager",
    )

    result = _evaluate(contract, engine, account_id="member-a", tenant_role="member")

    assert result["enforcement_mode"] == DATASET_ACL
    assert result["matched_grants"] == []
    assert _permissions(result) == frozenset()


@pytest.mark.parametrize(
    ("subject_type", "subject_id", "account_id"),
    (
        ("account", "member-b", "member-b"),
        ("group", "group-b", "member-b"),
        ("organization_unit", "unit-b", "member-b"),
    ),
)
def test_cross_tenant_subject_membership_never_matches_a_tenant_a_grant(
    subject_type: str,
    subject_id: str,
    account_id: str,
) -> None:
    contract = _load_contract()
    engine = _engine(with_access_graph=True)
    _grant(
        engine,
        grant_id=f"grant-cross-subject-{subject_type}",
        tenant_id="tenant-a",
        dataset_id=DATASET_ID,
        subject_type=subject_type,
        subject_id=subject_id,
        role="manager",
    )

    result = _evaluate(
        contract,
        engine,
        account_id=account_id,
        tenant_role="member",
    )

    assert result["enforcement_mode"] == DATASET_ACL
    assert result["matched_grants"] == []
    assert _permissions(result) == frozenset()


def test_schema_present_but_acl_query_failure_never_falls_back_to_tenant_role() -> None:
    contract = _load_contract()
    engine = _engine(with_access_graph=True)

    def fail_acl_query(
        _conn: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if "dataset_access_grants" in statement.casefold():
            raise RuntimeError("synthetic dataset ACL read failure")

    event.listen(engine, "before_cursor_execute", fail_acl_query)
    try:
        try:
            result = contract.evaluator(
                engine,
                "tenant-a",
                "editor-a",
                "editor",
                DATASET_ID,
            )
        except Exception:
            # A dedicated unavailable exception is an acceptable fail-closed
            # representation.  What is forbidden is silently returning the
            # editor's tenant-role permissions.
            return
    finally:
        event.remove(engine, "before_cursor_execute", fail_acl_query)

    result = _result(result)
    assert result["enforcement_mode"] == DATASET_ACL
    assert _permissions(result) == frozenset()
    assert result["matched_grants"] == []
    assert not (
        result["enforcement_mode"] == TENANT_ROLE_FALLBACK
        and _permissions(result) == ROLE_PERMISSIONS["editor"]
    )


def _summary_app(
    engine: Any,
    settings: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    try:
        from server.enterprise_admin_api import build_enterprise_admin_router
    except (ImportError, ModuleNotFoundError) as exc:
        pytest.fail(
            "Stage 5 RED: enterprise access-summary route contract is not available: "
            f"{type(exc).__name__}: {exc}"
        )

    monkeypatch.setattr(catalog, "get_engine", lambda: engine)
    app = FastAPI()
    app.state.knowledge_auth_engine = engine
    app.state.knowledge_auth_settings = settings
    app.include_router(
        build_enterprise_admin_router(
            read_engine_provider=lambda: engine,
            mutation_engine_provider=lambda: engine,
        )
    )
    return TestClient(app, client=("10.0.0.2", 50000))


def test_access_summary_reports_dataset_acl_mode_matched_grants_permissions_and_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _load_contract()
    engine = _engine(with_access_graph=True)
    _grant(
        engine,
        grant_id="grant-summary-editor",
        subject_type="account",
        subject_id="editor-a",
        role="editor",
    )
    settings = _settings()
    client = _summary_app(engine, settings, monkeypatch)

    response = client.get(
        f"/api/knowledge-bases/{DATASET_ID}/access-summary",
        headers=_headers(settings, "editor-a"),
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["enforcement_mode"] == DATASET_ACL
    assert payload["matched_grants"] == ["grant-summary-editor"]
    assert set(payload["effective_permissions"]) == GRANT_PERMISSIONS["editor"]
    assert isinstance(payload["warnings"], list)


def test_access_summary_reports_tenant_role_fallback_when_dataset_has_no_active_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _load_contract()
    engine = _engine(with_access_graph=True)
    settings = _settings()
    client = _summary_app(engine, settings, monkeypatch)

    response = client.get(
        f"/api/knowledge-bases/{DATASET_ID}/access-summary",
        headers=_headers(settings, "editor-a"),
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["enforcement_mode"] == TENANT_ROLE_FALLBACK
    assert payload["matched_grants"] == []
    assert set(payload["effective_permissions"]) == ROLE_PERMISSIONS["editor"]
    assert isinstance(payload["warnings"], list)


def _dataset_route_client(
    engine: Any,
    settings: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    try:
        from server.knowledge_dataset_api import router
    except (ImportError, ModuleNotFoundError) as exc:
        pytest.fail(
            "Stage 5 RED: actual dataset route contract is not available: "
            f"{type(exc).__name__}: {exc}"
        )

    monkeypatch.setattr(catalog, "get_engine", lambda: engine)
    app = FastAPI()
    app.state.knowledge_auth_engine = engine
    app.state.knowledge_auth_settings = settings
    app.include_router(router)
    return TestClient(app, client=("10.0.0.2", 50000))


def test_actual_require_knowledge_permission_dataset_route_denies_unmatched_acl_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _load_contract()
    engine = _engine(with_access_graph=True)
    _grant(
        engine,
        grant_id="grant-route-unrelated",
        subject_type="account",
        subject_id="viewer-a",
        role="viewer",
    )
    settings = _settings()
    client = _dataset_route_client(engine, settings, monkeypatch)

    response = client.get(
        f"/api/knowledge-bases/{DATASET_ID}",
        headers=_headers(settings, "member-a"),
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "knowledge_permission_forbidden"


def test_actual_require_knowledge_permission_dataset_route_allows_matching_acl_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _load_contract()
    engine = _engine(with_access_graph=True)
    _grant(
        engine,
        grant_id="grant-route-member",
        subject_type="account",
        subject_id="member-a",
        role="viewer",
    )
    settings = _settings()
    client = _dataset_route_client(engine, settings, monkeypatch)

    response = client.get(
        f"/api/knowledge-bases/{DATASET_ID}",
        headers=_headers(settings, "member-a"),
    )

    assert response.status_code == 200, response.text
    assert response.json()["id"] == DATASET_ID
