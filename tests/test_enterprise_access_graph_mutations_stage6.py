from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from config.settings import KnowledgeSecuritySettings, RunHistorySettings, TenantSettings
from models.orm import Account, Base, Dataset, DatasetAccessGrant, Tenant, TenantMember
from server.enterprise_access_graph_api import build_enterprise_access_graph_router
from server.knowledge_auth import issue_knowledge_actor_token


SETTINGS = SimpleNamespace(
    run_history=RunHistorySettings(ops_bearer_token=SecretStr("ops-secret")),
    knowledge_security=KnowledgeSecuritySettings(
        actor_signing_secret=SecretStr("stage6-route-secret"),
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
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)"))
        connection.execute(
            text("INSERT INTO alembic_version(version_num) VALUES ('0019_dataset_acl_control')")
        )
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id="tenant-a", name="A", status="active"),
                Account(id="owner-a", name="Owner", email="owner-a@stage6.test"),
                Account(id="member-a", name="Member", email="member-a@stage6.test"),
                TenantMember(
                    tenant_id="tenant-a",
                    account_id="owner-a",
                    role="owner",
                    status="active",
                    revision=1,
                ),
                TenantMember(
                    tenant_id="tenant-a",
                    account_id="member-a",
                    role="member",
                    status="active",
                    revision=1,
                ),
                Dataset(
                    id="dataset-a",
                    tenant_id="tenant-a",
                    owner_id="owner-a",
                    name="Knowledge",
                    status="active",
                    acl_mode="dataset_acl",
                    acl_revision=1,
                ),
            ]
        )
        session.commit()
    return engine


def _headers(account_id: str = "owner-a") -> dict[str, str]:
    token = issue_knowledge_actor_token(
        account_id,
        "tenant-a",
        300,
        int(time.time()),
        settings=SETTINGS,
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-RAG4C-Tenant": "tenant-a",
        "X-RAG4C-Actor": account_id,
        "X-Request-ID": "stage6-route-test",
        "Idempotency-Key": f"stage7-stage6-{account_id}-{time.time_ns()}",
    }


def _client(engine, *, mutation_engine_provider=None, read_engine_provider=None) -> TestClient:
    app = FastAPI()
    app.state.knowledge_auth_engine = engine
    app.state.knowledge_auth_settings = SETTINGS
    app.include_router(
        build_enterprise_access_graph_router(
            read_engine_provider=read_engine_provider or (lambda: engine),
            mutation_engine_provider=mutation_engine_provider or (lambda: engine),
        )
    )
    return TestClient(app)


def test_patch_role_accepts_frontend_revision_contract_on_canonical_grant_path() -> None:
    engine = _engine()
    with Session(engine) as session:
        session.add(
            DatasetAccessGrant(
                id="grant-a",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                subject_type="account",
                subject_id="member-a",
                role="viewer",
                status="active",
                revision=1,
            )
        )
        session.commit()

    response = _client(engine).patch(
        "/api/knowledge-bases/dataset-a/access-grants/grant-a",
        headers=_headers(),
        json={"role": "editor", "revision": 1, "reason": "岗位调整"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["grant"]["role"] == "editor"
    assert response.json()["grant"]["revision"] == 2


def test_patch_role_alias_remains_supported() -> None:
    engine = _engine()
    with Session(engine) as session:
        session.add(
            DatasetAccessGrant(
                id="grant-a",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                subject_type="account",
                subject_id="member-a",
                role="viewer",
                status="active",
                revision=1,
            )
        )
        session.commit()

    response = _client(engine).patch(
        "/api/knowledge-bases/dataset-a/access-grants/grant-a/role",
        headers=_headers(),
        json={"role": "editor", "expected_revision": 1, "reason": "岗位调整"},
    )

    assert response.status_code == 200, response.text


def test_get_uses_read_provider_and_never_initializes_writable_provider() -> None:
    engine = _engine()
    read_calls = 0
    mutation_calls = 0

    def read_provider():
        nonlocal read_calls
        read_calls += 1
        return engine

    def mutation_provider():
        nonlocal mutation_calls
        mutation_calls += 1
        raise AssertionError("GET must not initialize writable provider")

    response = _client(
        engine,
        read_engine_provider=read_provider,
        mutation_engine_provider=mutation_provider,
    ).get(
        "/api/knowledge-bases/dataset-a/access-grants",
        headers=_headers(),
        params={"limit": 10},
    )

    assert response.status_code == 200, response.text
    assert read_calls >= 1
    assert mutation_calls == 0


def test_non_manager_is_rejected_before_writable_provider() -> None:
    engine = _engine()
    mutation_calls = 0

    def mutation_provider():
        nonlocal mutation_calls
        mutation_calls += 1
        raise AssertionError("forbidden mutation must not initialize writable provider")

    response = _client(engine, mutation_engine_provider=mutation_provider).post(
        "/api/knowledge-bases/dataset-a/access-grants",
        headers=_headers("member-a"),
        json={
            "subject_type": "account",
            "subject_id": "member-a",
            "role": "viewer",
            "reason": "不应执行",
        },
    )

    assert response.status_code == 403, response.text
    assert mutation_calls == 0


def test_invalid_revision_is_422_without_writable_provider() -> None:
    engine = _engine()
    mutation_calls = 0

    def mutation_provider():
        nonlocal mutation_calls
        mutation_calls += 1
        return engine

    response = _client(engine, mutation_engine_provider=mutation_provider).patch(
        "/api/knowledge-bases/dataset-a/access-grants/missing",
        headers=_headers(),
        json={"role": "editor", "revision": 0, "reason": "无效"},
    )

    assert response.status_code == 422, response.text
    assert mutation_calls == 0


def test_create_and_revoke_have_expected_mutation_paths() -> None:
    engine = _engine()
    client = _client(engine)
    created = client.post(
        "/api/knowledge-bases/dataset-a/access-grants",
        headers=_headers(),
        json={
            "subject_type": "account",
            "subject_id": "member-a",
            "role": "viewer",
            "reason": "建立授权",
        },
    )
    assert created.status_code == 201, created.text
    grant_id = created.json()["grant"]["id"]
    second = client.post(
        "/api/knowledge-bases/dataset-a/access-grants",
        headers=_headers(),
        json={
            "subject_type": "account",
            "subject_id": "owner-a",
            "role": "viewer",
            "reason": "保留 ACL 生效状态",
        },
    )
    assert second.status_code == 201, second.text

    revoked = client.post(
        f"/api/knowledge-bases/dataset-a/access-grants/{grant_id}/revoke",
        headers=_headers(),
        json={"revision": 1, "reason": "撤销授权"},
    )
    assert revoked.status_code == 200, revoked.text
    with Session(engine) as session:
        assert (
            session.scalar(
                select(DatasetAccessGrant.status).where(DatasetAccessGrant.id == grant_id)
            )
            == "revoked"
        )


@pytest.mark.parametrize("method", ["post", "patch"])
def test_missing_bearer_is_401(method: str) -> None:
    client = _client(_engine())
    if method == "post":
        response = client.post(
            "/api/knowledge-bases/dataset-a/access-grants",
            json={
                "subject_type": "account",
                "subject_id": "member-a",
                "role": "viewer",
                "reason": "缺少身份",
            },
        )
    else:
        response = client.patch(
            "/api/knowledge-bases/dataset-a/access-grants/grant-a",
            json={"role": "viewer", "revision": 1, "reason": "缺少身份"},
        )
    assert response.status_code == 401, response.text
