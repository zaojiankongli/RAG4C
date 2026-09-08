"""Stage 4 RED contracts for the enterprise access-graph read API.

This file deliberately describes the wished-for public API before the 0017
models or router exist.  The current repository is expected to fail these
contracts because production implementation is intentionally out of scope.

Collection contract:

* tenant-scoped business reads only;
* descending ``before_id`` keyset pagination;
* ``count`` is the total number of rows matching the current filters, not the
  current page length;
* ``next_before_id`` is the last visible id only when another page exists;
* every route requires the existing signed KnowledgeActor flow and
  ``knowledge.manage``;
* invitation payloads never expose ``token_hash``;
* a catalog without the 0017 tables fails closed with
  ``enterprise_access_graph_migration_required``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from config.settings import KnowledgeSecuritySettings, RunHistorySettings, TenantSettings
from core import catalog
from models.orm import Account, Base, Dataset, Tenant, TenantMember
from server import knowledge_auth
from server.knowledge_auth import issue_knowledge_actor_token


BASE_TIME = datetime(2026, 8, 26, 9, 0, 0)
MAX_PAGE_LIMIT = 200
MIGRATION_REQUIRED_CODE = "enterprise_access_graph_migration_required"

CONTRACT_PATHS = (
    "/api/enterprise/organization-units",
    "/api/enterprise/groups",
    "/api/enterprise/groups/{group_id}/members",
    "/api/enterprise/invitations",
    "/api/knowledge-bases/{dataset_id}/access-grants",
)

REQUEST_PATHS = (
    "/api/enterprise/organization-units?limit=1",
    "/api/enterprise/groups?limit=1",
    "/api/enterprise/groups/group-a-z-platform-engineering/members",
    "/api/enterprise/invitations?limit=1",
    "/api/knowledge-bases/dataset-a/access-grants?limit=1",
)


@dataclass(frozen=True)
class _AccessGraphContract:
    build_router: Callable[..., Any]
    organization_unit: type[Any]
    organization_unit_member: type[Any]
    group: type[Any]
    group_member: type[Any]
    access_grant: type[Any]
    invitation: type[Any]


@dataclass
class _Api:
    client: TestClient
    engine: Any
    settings: SimpleNamespace
    read_calls: list[Any]
    write_statements: list[str]


def _contract() -> _AccessGraphContract:
    """Load only the production symbols this RED contract requires.

    Keeping the imports here lets pytest collect and print a focused RED reason
    rather than hiding the missing implementation behind a skip.
    """

    try:
        from models.orm import (
            DatasetAccessGrant,
            TenantGroup,
            TenantGroupMember,
            TenantInvitation,
            TenantOrganizationUnit,
            TenantOrganizationUnitMember,
        )
        from server.enterprise_access_graph_api import (
            build_enterprise_access_graph_router,
        )
    except (ImportError, ModuleNotFoundError) as exc:
        pytest.fail(
            "Stage 4 RED: enterprise access-graph models/router are not implemented: "
            f"{type(exc).__name__}: {exc}"
        )

    return _AccessGraphContract(
        build_router=build_enterprise_access_graph_router,
        organization_unit=TenantOrganizationUnit,
        organization_unit_member=TenantOrganizationUnitMember,
        group=TenantGroup,
        group_member=TenantGroupMember,
        access_grant=DatasetAccessGrant,
        invitation=TenantInvitation,
    )


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        run_history=RunHistorySettings(ops_bearer_token=SecretStr("ops-secret")),
        knowledge_security=KnowledgeSecuritySettings(
            actor_signing_secret=SecretStr("enterprise-access-graph-secret"),
            actor_max_ttl_s=900,
        ),
        tenant=TenantSettings(enforced=True, default_tenant="tenant-a"),
    )


def _headers(
    settings: SimpleNamespace,
    actor_id: str,
    tenant_id: str = "tenant-a",
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
        "X-Request-ID": "req-enterprise-access-graph",
    }


def _create_auth_tables(engine: Any) -> None:
    Base.metadata.create_all(
        engine,
        tables=[
            Tenant.__table__,
            Account.__table__,
            TenantMember.__table__,
            Dataset.__table__,
        ],
    )


def _create_access_graph_tables(engine: Any, contract: _AccessGraphContract) -> None:
    Base.metadata.create_all(
        engine,
        tables=[
            contract.organization_unit.__table__,
            contract.organization_unit_member.__table__,
            contract.group.__table__,
            contract.group_member.__table__,
            contract.invitation.__table__,
            contract.access_grant.__table__,
        ],
    )


def _seed_identity_and_datasets(session: Session) -> None:
    session.add_all(
        [
            Tenant(id="tenant-a", name="RAG4C Enterprise", plan="enterprise", status="active"),
            Tenant(id="tenant-b", name="Other Enterprise", plan="enterprise", status="active"),
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
                id="owner-b",
                name="Owner B",
                email="owner-b@example.test",
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
                created_at=BASE_TIME,
            ),
            TenantMember(
                account_id="admin-a",
                tenant_id="tenant-a",
                role="admin",
                status="active",
                created_at=BASE_TIME,
            ),
            TenantMember(
                account_id="editor-a",
                tenant_id="tenant-a",
                role="editor",
                status="active",
                created_at=BASE_TIME,
            ),
            TenantMember(
                account_id="member-a",
                tenant_id="tenant-a",
                role="member",
                status="active",
                created_at=BASE_TIME,
            ),
            TenantMember(
                account_id="owner-b",
                tenant_id="tenant-b",
                role="owner",
                status="active",
                created_at=BASE_TIME,
            ),
        ]
    )
    session.flush()
    session.add_all(
        [
            Dataset(
                id="dataset-a",
                tenant_id="tenant-a",
                name="制度知识库",
                status="active",
                owner_id="owner-a",
                visibility="private",
            ),
            Dataset(
                id="dataset-b",
                tenant_id="tenant-b",
                name="Other Dataset",
                status="active",
                owner_id="owner-b",
                visibility="private",
            ),
        ]
    )


def _seed_access_graph(session: Session, contract: _AccessGraphContract) -> None:
    OrganizationUnit = contract.organization_unit
    OrganizationUnitMember = contract.organization_unit_member
    Group = contract.group
    GroupMember = contract.group_member
    AccessGrant = contract.access_grant
    Invitation = contract.invitation

    session.add_all(
        [
            OrganizationUnit(
                id="ou-a-root",
                code="HQ",
                tenant_id="tenant-a",
                parent_id=None,
                name="总部",
                status="active",
                created_at=BASE_TIME,
            ),
            OrganizationUnit(
                id="ou-a-support",
                code="SUPPORT",
                tenant_id="tenant-a",
                parent_id="ou-a-root",
                name="支持中心",
                status="active",
                created_at=BASE_TIME,
            ),
            OrganizationUnit(
                id="ou-a-platform",
                code="PLATFORM",
                tenant_id="tenant-a",
                parent_id="ou-a-root",
                name="平台研发部",
                status="active",
                created_at=BASE_TIME,
            ),
            OrganizationUnit(
                id="ou-a-archived",
                code="ARCHIVED",
                tenant_id="tenant-a",
                parent_id="ou-a-root",
                name="历史交付部",
                status="archived",
                created_at=BASE_TIME,
            ),
            OrganizationUnit(
                id="ou-b-root",
                code="OTHER",
                tenant_id="tenant-b",
                parent_id=None,
                name="Other Tenant Root",
                status="active",
                created_at=BASE_TIME,
            ),
        ]
    )
    session.flush()

    session.add_all(
        [
            OrganizationUnitMember(
                id=301,
                tenant_id="tenant-a",
                organization_unit_id="ou-a-root",
                account_id="owner-a",
                status="active",
                revision=1,
                created_at=BASE_TIME,
            ),
            OrganizationUnitMember(
                id=302,
                tenant_id="tenant-a",
                organization_unit_id="ou-a-platform",
                account_id="admin-a",
                status="active",
                revision=1,
                created_at=BASE_TIME,
            ),
            OrganizationUnitMember(
                id=303,
                tenant_id="tenant-a",
                organization_unit_id="ou-a-platform",
                account_id="member-a",
                status="active",
                revision=1,
                created_at=BASE_TIME,
            ),
            OrganizationUnitMember(
                id=304,
                tenant_id="tenant-a",
                organization_unit_id="ou-a-platform",
                account_id="editor-a",
                status="removed",
                revision=2,
                created_at=BASE_TIME,
            ),
        ]
    )

    session.add_all(
        [
            Group(
                id="group-a-z-platform-engineering",
                tenant_id="tenant-a",
                name="平台研发组",
                description="平台研发访问主体",
                status="active",
                created_at=BASE_TIME,
            ),
            Group(
                id="group-a-y-platform-support",
                tenant_id="tenant-a",
                name="平台支持组",
                description="平台支持访问主体",
                status="active",
                created_at=BASE_TIME,
            ),
            Group(
                id="group-a-x-platform-legacy",
                tenant_id="tenant-a",
                name="平台历史组",
                description="已归档访问主体",
                status="archived",
                created_at=BASE_TIME,
            ),
            Group(
                id="group-b-z-external",
                tenant_id="tenant-b",
                name="Other Tenant Group",
                description="Cross-tenant sentinel",
                status="active",
                created_at=BASE_TIME,
            ),
        ]
    )
    session.flush()

    session.add_all(
        [
            GroupMember(
                id=101,
                tenant_id="tenant-a",
                group_id="group-a-z-platform-engineering",
                account_id="admin-a",
                status="active",
                created_at=BASE_TIME,
            ),
            GroupMember(
                id=102,
                tenant_id="tenant-a",
                group_id="group-a-z-platform-engineering",
                account_id="member-a",
                status="active",
                created_at=BASE_TIME,
            ),
            GroupMember(
                id=103,
                tenant_id="tenant-a",
                group_id="group-a-y-platform-support",
                account_id="member-a",
                status="active",
                created_at=BASE_TIME,
            ),
            GroupMember(
                id=201,
                tenant_id="tenant-b",
                group_id="group-b-z-external",
                account_id="owner-b",
                status="active",
                created_at=BASE_TIME,
            ),
        ]
    )

    session.add_all(
        [
            Invitation(
                id="invite-a-z-admin",
                tenant_id="tenant-a",
                email="new-admin@example.test",
                role="admin",
                status="pending",
                invited_by="owner-a",
                token_hash="sha256:must-never-leave-the-server-z",
                expires_at=datetime(2026, 9, 30, 0, 0, 0),
                created_at=BASE_TIME,
                pending_email_key="new-admin@example.test",
                last_sent_at=BASE_TIME,
                send_count=1,
                updated_by="owner-a",
            ),
            Invitation(
                id="invite-a-y-member",
                tenant_id="tenant-a",
                email="new-member@example.test",
                role="member",
                status="pending",
                invited_by="admin-a",
                token_hash="sha256:must-never-leave-the-server-y",
                expires_at=datetime(2026, 9, 29, 0, 0, 0),
                created_at=BASE_TIME,
                pending_email_key="new-member@example.test",
                last_sent_at=BASE_TIME,
                send_count=1,
                updated_by="admin-a",
            ),
            Invitation(
                id="invite-a-x-accepted",
                tenant_id="tenant-a",
                email="accepted@example.test",
                role="member",
                status="accepted",
                invited_by="owner-a",
                token_hash="sha256:must-never-leave-the-server-x",
                expires_at=datetime(2026, 9, 28, 0, 0, 0),
                accepted_at=BASE_TIME,
                accepted_by="member-a",
                created_at=BASE_TIME,
                pending_email_key=None,
                last_sent_at=BASE_TIME,
                send_count=1,
                updated_by="owner-a",
            ),
            Invitation(
                id="invite-b-z-secret",
                tenant_id="tenant-b",
                email="other-tenant@example.test",
                role="admin",
                status="pending",
                invited_by="owner-b",
                token_hash="sha256:cross-tenant-secret",
                expires_at=datetime(2026, 9, 30, 0, 0, 0),
                created_at=BASE_TIME,
                pending_email_key="other-tenant@example.test",
                last_sent_at=BASE_TIME,
                send_count=1,
                updated_by="owner-b",
            ),
        ]
    )

    session.add_all(
        [
            AccessGrant(
                id="grant-a-z-platform-engineering",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                subject_type="group",
                subject_id="group-a-z-platform-engineering",
                role="editor",
                status="active",
                created_at=BASE_TIME,
            ),
            AccessGrant(
                id="grant-a-y-platform-support",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                subject_type="group",
                subject_id="group-a-y-platform-support",
                role="editor",
                status="active",
                created_at=BASE_TIME,
            ),
            AccessGrant(
                id="grant-a-x-account",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                subject_type="account",
                subject_id="member-a",
                role="viewer",
                status="active",
                created_at=BASE_TIME,
            ),
            AccessGrant(
                id="grant-a-w-organization",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                subject_type="organization_unit",
                subject_id="ou-a-platform",
                role="viewer",
                status="revoked",
                created_at=BASE_TIME,
            ),
            AccessGrant(
                id="grant-b-z-secret",
                tenant_id="tenant-b",
                dataset_id="dataset-b",
                subject_type="group",
                subject_id="group-b-z-external",
                role="editor",
                status="active",
                created_at=BASE_TIME,
            ),
        ]
    )


def _engine(
    contract: _AccessGraphContract,
    *,
    include_access_graph_tables: bool,
) -> Any:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _create_auth_tables(engine)
    if include_access_graph_tables:
        _create_access_graph_tables(engine, contract)

    with Session(engine) as session:
        _seed_identity_and_datasets(session)
        if include_access_graph_tables:
            _seed_access_graph(session, contract)
        session.commit()
    return engine


def _install_write_guard(engine: Any) -> list[str]:
    writes: list[str] = []
    write_prefixes = {
        "ALTER",
        "CREATE",
        "DELETE",
        "DROP",
        "INSERT",
        "REPLACE",
        "TRUNCATE",
        "UPDATE",
    }

    @event.listens_for(engine, "before_cursor_execute")
    def reject_business_write(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        normalized = statement.lstrip()
        keyword = normalized.split(None, 1)[0].upper() if normalized else ""
        if keyword in write_prefixes:
            writes.append(statement)
            raise AssertionError(f"enterprise access-graph GET attempted SQL write: {statement}")

    return writes


def _client(
    *,
    include_access_graph_tables: bool = True,
    engine_provider: Callable[[], Any] | None = None,
) -> _Api:
    contract = _contract()
    engine = _engine(
        contract,
        include_access_graph_tables=include_access_graph_tables,
    )
    settings = _settings()
    read_calls: list[Any] = []
    write_statements = _install_write_guard(engine)

    def read_engine_provider() -> Any:
        read_calls.append(engine)
        if engine_provider is not None:
            return engine_provider()
        return engine

    app = FastAPI()
    app.state.knowledge_auth_engine = engine
    app.state.knowledge_auth_settings = settings
    app.include_router(
        contract.build_router(
            read_engine_provider=read_engine_provider,
            mutation_engine_provider=lambda: engine,
        )
    )
    return _Api(
        client=TestClient(app, client=("10.0.0.2", 50000)),
        engine=engine,
        settings=settings,
        read_calls=read_calls,
        write_statements=write_statements,
    )


@pytest.fixture()
def api() -> _Api:
    return _client()


def _assert_no_key(value: Any, forbidden: str) -> None:
    if isinstance(value, dict):
        assert forbidden not in value
        for child in value.values():
            _assert_no_key(child, forbidden)
    elif isinstance(value, list):
        for child in value:
            _assert_no_key(child, forbidden)


def test_router_registers_the_five_stage4_read_routes(api: _Api) -> None:
    paths = api.client.app.openapi()["paths"]

    assert set(CONTRACT_PATHS) <= set(paths)
    for path in CONTRACT_PATHS:
        assert "get" in paths[path]
        if path not in {
            "/api/enterprise/invitations",
            "/api/knowledge-bases/{dataset_id}/access-grants",
        }:
            assert set(paths[path]) == {"get"}
    assert "post" in paths["/api/enterprise/invitations"]
    assert "post" in paths["/api/knowledge-bases/{dataset_id}/access-grants"]


def test_organization_units_are_tenant_isolated_and_support_parent_status_keyset(
    api: _Api,
) -> None:
    headers = _headers(api.settings, "owner-a")

    first = api.client.get(
        "/api/enterprise/organization-units",
        headers=headers,
        params={"parent_id": "ou-a-root", "status": "active", "limit": 1},
    )
    assert first.status_code == 200, first.text
    assert first.json() == {
        "items": [
            {
                "id": "ou-a-support",
                "name": "支持中心",
                "parent_id": "ou-a-root",
                "code": "SUPPORT",
                "status": "active",
                "revision": 1,
                "member_count": 0,
                "child_count": 0,
            }
        ],
        "count": 2,
        "next_before_id": "ou-a-support",
    }

    second = api.client.get(
        "/api/enterprise/organization-units",
        headers=headers,
        params={
            "parent_id": "ou-a-root",
            "status": "active",
            "limit": 1,
            "before_id": "ou-a-support",
        },
    )
    assert second.status_code == 200, second.text
    assert second.json() == {
        "items": [
            {
                "id": "ou-a-platform",
                "name": "平台研发部",
                "parent_id": "ou-a-root",
                "code": "PLATFORM",
                "status": "active",
                "revision": 1,
                "member_count": 2,
                "child_count": 0,
            }
        ],
        "count": 2,
        "next_before_id": None,
    }

    all_units = api.client.get(
        "/api/enterprise/organization-units",
        headers=headers,
        params={"limit": 50},
    )
    assert all_units.status_code == 200, all_units.text
    assert all(item["id"].startswith("ou-a-") for item in all_units.json()["items"])
    assert "ou-b-root" not in {item["id"] for item in all_units.json()["items"]}
    counts = {item["id"]: item["member_count"] for item in all_units.json()["items"]}
    assert counts["ou-a-root"] == 1
    assert counts["ou-a-platform"] == 2


def test_groups_support_q_status_keyset_total_count_and_member_count(api: _Api) -> None:
    headers = _headers(api.settings, "admin-a")

    first = api.client.get(
        "/api/enterprise/groups",
        headers=headers,
        params={"q": "平台", "status": "active", "limit": 1},
    )
    assert first.status_code == 200, first.text
    assert first.json() == {
        "items": [
            {
                "id": "group-a-z-platform-engineering",
                "name": "平台研发组",
                "description": "平台研发访问主体",
                "status": "active",
                "member_count": 2,
                "revision": 1,
            }
        ],
        "count": 2,
        "next_before_id": "group-a-z-platform-engineering",
    }

    second = api.client.get(
        "/api/enterprise/groups",
        headers=headers,
        params={
            "q": "平台",
            "status": "active",
            "limit": 1,
            "before_id": "group-a-z-platform-engineering",
        },
    )
    assert second.status_code == 200, second.text
    assert second.json() == {
        "items": [
            {
                "id": "group-a-y-platform-support",
                "name": "平台支持组",
                "description": "平台支持访问主体",
                "status": "active",
                "member_count": 1,
                "revision": 1,
            }
        ],
        "count": 2,
        "next_before_id": None,
    }

    all_groups = api.client.get(
        "/api/enterprise/groups",
        headers=headers,
        params={"limit": 50},
    )
    assert all_groups.status_code == 200, all_groups.text
    assert "group-b-z-external" not in {item["id"] for item in all_groups.json()["items"]}


def test_group_members_return_real_accounts_and_hide_cross_tenant_groups(api: _Api) -> None:
    headers = _headers(api.settings, "owner-a")

    own_group = api.client.get(
        "/api/enterprise/groups/group-a-z-platform-engineering/members",
        headers=headers,
    )
    assert own_group.status_code == 200, own_group.text
    assert own_group.json() == {
        "items": [
            {
                "id": "102",
                "account_id": "member-a",
                "name": "Member A",
                "email": "member-a@example.test",
                "role": "member",
                "status": "active",
            },
            {
                "id": "101",
                "account_id": "admin-a",
                "name": "Admin A",
                "email": "admin-a@example.test",
                "role": "admin",
                "status": "active",
            },
        ],
        "count": 2,
        "next_before_id": None,
    }

    cross_tenant = api.client.get(
        "/api/enterprise/groups/group-b-z-external/members",
        headers=headers,
    )
    assert cross_tenant.status_code == 404
    assert cross_tenant.json() == {
        "detail": {
            "code": "knowledge_resource_not_found",
            "message": "资源不存在",
        }
    }
    assert "Other Tenant Group" not in cross_tenant.text


def test_invitations_support_status_keyset_and_never_return_token_hash(api: _Api) -> None:
    headers = _headers(api.settings, "owner-a")

    first = api.client.get(
        "/api/enterprise/invitations",
        headers=headers,
        params={"status": "pending", "limit": 1},
    )
    assert first.status_code == 200, first.text
    assert first.json() == {
        "items": [
            {
                "id": "invite-a-z-admin",
                "email": "new-admin@example.test",
                "role": "admin",
                "status": "pending",
                "invited_by": "owner-a",
                "expires_at": "2026-09-30T00:00:00",
                "revision": 1,
            }
        ],
        "count": 2,
        "next_before_id": "invite-a-z-admin",
    }

    second = api.client.get(
        "/api/enterprise/invitations",
        headers=headers,
        params={
            "status": "pending",
            "limit": 1,
            "before_id": "invite-a-z-admin",
        },
    )
    assert second.status_code == 200, second.text
    assert second.json() == {
        "items": [
            {
                "id": "invite-a-y-member",
                "email": "new-member@example.test",
                "role": "member",
                "status": "pending",
                "invited_by": "admin-a",
                "expires_at": "2026-09-29T00:00:00",
                "revision": 1,
            }
        ],
        "count": 2,
        "next_before_id": None,
    }

    all_invitations = api.client.get(
        "/api/enterprise/invitations",
        headers=headers,
        params={"limit": 50},
    )
    assert all_invitations.status_code == 200, all_invitations.text
    assert "invite-b-z-secret" not in {item["id"] for item in all_invitations.json()["items"]}

    for response in (first, second, all_invitations):
        payload = response.json()
        _assert_no_key(payload, "token_hash")
        assert "must-never-leave-the-server" not in response.text
        assert "cross-tenant-secret" not in response.text
        assert "token_hash" not in json.dumps(payload, ensure_ascii=False)


def test_dataset_access_grants_are_dataset_tenant_isolated_and_filterable(api: _Api) -> None:
    headers = _headers(api.settings, "owner-a")

    first = api.client.get(
        "/api/knowledge-bases/dataset-a/access-grants",
        headers=headers,
        params={
            "subject_type": "group",
            "role": "editor",
            "status": "active",
            "limit": 1,
        },
    )
    assert first.status_code == 200, first.text
    assert first.json() == {
        "items": [
            {
                "id": "grant-a-z-platform-engineering",
                "dataset_id": "dataset-a",
                "subject_type": "group",
                "subject_id": "group-a-z-platform-engineering",
                "subject_name": "平台研发组",
                "role": "editor",
                "status": "active",
                "revision": 1,
            }
        ],
        "count": 2,
        "next_before_id": "grant-a-z-platform-engineering",
    }

    second = api.client.get(
        "/api/knowledge-bases/dataset-a/access-grants",
        headers=headers,
        params={
            "subject_type": "group",
            "role": "editor",
            "status": "active",
            "limit": 1,
            "before_id": "grant-a-z-platform-engineering",
        },
    )
    assert second.status_code == 200, second.text
    assert second.json() == {
        "items": [
            {
                "id": "grant-a-y-platform-support",
                "dataset_id": "dataset-a",
                "subject_type": "group",
                "subject_id": "group-a-y-platform-support",
                "subject_name": "平台支持组",
                "role": "editor",
                "status": "active",
                "revision": 1,
            }
        ],
        "count": 2,
        "next_before_id": None,
    }

    all_grants = api.client.get(
        "/api/knowledge-bases/dataset-a/access-grants",
        headers=headers,
        params={"limit": 50},
    )
    assert all_grants.status_code == 200, all_grants.text
    assert "grant-b-z-secret" not in {item["id"] for item in all_grants.json()["items"]}

    cross_tenant_dataset = api.client.get(
        "/api/knowledge-bases/dataset-b/access-grants",
        headers=headers,
    )
    assert cross_tenant_dataset.status_code == 403
    assert cross_tenant_dataset.json() == {
        "detail": {
            "code": "knowledge_dataset_scope_forbidden",
            "message": "知识库不属于当前租户作用域",
        }
    }


@pytest.mark.parametrize("path", REQUEST_PATHS)
def test_access_graph_routes_use_the_signed_actor_header_flow(
    api: _Api,
    path: str,
) -> None:
    headers = _headers(api.settings, "owner-a")
    headers["X-RAG4C-Actor"] = "admin-a"

    response = api.client.get(path, headers=headers)

    assert response.status_code == 401
    assert response.json() == {
        "detail": {
            "code": "knowledge_actor_mismatch",
            "message": "actor assertion 与签名身份不一致",
        }
    }


@pytest.mark.parametrize("path", REQUEST_PATHS)
def test_access_graph_routes_require_knowledge_manage_under_the_real_role_policy(
    api: _Api,
    path: str,
) -> None:
    response = api.client.get(
        path,
        headers=_headers(api.settings, "member-a"),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "knowledge_permission_forbidden"
    expected_message = (
        "当前身份没有该知识库所需的权限"
        if "access-grants" in path
        else "当前租户角色没有所需的知识库权限"
    )
    assert response.json()["detail"]["message"] == expected_message


@pytest.mark.parametrize("path", REQUEST_PATHS)
def test_each_route_is_bound_specifically_to_knowledge_manage(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    # Editor normally lacks both knowledge.manage and knowledge.audit.  This
    # pure policy seam grants exactly knowledge.manage, so a route wired to any
    # other permission remains 403 while a correctly wired route executes its
    # real query and returns 200.
    monkeypatch.setattr(
        knowledge_auth,
        "role_allows",
        lambda _role, permission: permission == "knowledge.manage",
    )
    from core.enterprise_access_control import DatasetAccessDecision

    monkeypatch.setattr(
        knowledge_auth,
        "evaluate_dataset_permissions",
        lambda *_args, **_kwargs: DatasetAccessDecision(
            enforcement_mode="dataset_acl",
            effective_permissions=frozenset({"knowledge.manage"}),
            dataset_acl_supported=True,
            group_grants_supported=True,
        ),
    )
    api = _client()

    response = api.client.get(
        path,
        headers=_headers(api.settings, "editor-a"),
    )

    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    "path",
    (
        "/api/enterprise/organization-units?limit=0",
        f"/api/enterprise/organization-units?limit={MAX_PAGE_LIMIT + 1}",
        "/api/enterprise/groups?limit=0",
        f"/api/enterprise/groups?limit={MAX_PAGE_LIMIT + 1}",
        "/api/enterprise/invitations?limit=0",
        f"/api/enterprise/invitations?limit={MAX_PAGE_LIMIT + 1}",
        "/api/knowledge-bases/dataset-a/access-grants?limit=0",
        f"/api/knowledge-bases/dataset-a/access-grants?limit={MAX_PAGE_LIMIT + 1}",
    ),
)
def test_keyset_collection_limits_are_bounded(api: _Api, path: str) -> None:
    response = api.client.get(path, headers=_headers(api.settings, "owner-a"))

    assert response.status_code == 422


@pytest.mark.parametrize("path", REQUEST_PATHS)
def test_missing_0017_tables_fail_closed_without_schema_details(path: str) -> None:
    api = _client(include_access_graph_tables=False)

    response = api.client.get(
        path,
        headers=_headers(api.settings, "owner-a"),
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == MIGRATION_REQUIRED_CODE
    assert "no such table" not in response.text.casefold()
    assert "sqlalchemy" not in response.text.casefold()
    assert api.read_calls == [api.engine]
    assert api.write_statements == []


def test_all_business_routes_use_the_explicit_read_engine_provider_and_never_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _client()
    assert api.read_calls == []

    monkeypatch.setattr(
        catalog,
        "get_engine",
        lambda: (_ for _ in ()).throw(
            AssertionError("global writable catalog engine must not be used")
        ),
    )

    headers = _headers(api.settings, "owner-a")
    responses = [api.client.get(path, headers=headers) for path in REQUEST_PATHS]

    assert [response.status_code for response in responses] == [200, 200, 200, 200, 200]
    assert api.read_calls == [api.engine] * len(REQUEST_PATHS)
    assert api.write_statements == []


def test_main_application_mounts_the_enterprise_access_graph_routes() -> None:
    from server.app import app

    paths = app.openapi()["paths"]

    assert set(CONTRACT_PATHS) <= set(paths)
