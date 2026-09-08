from __future__ import annotations

import json
import time
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
from models.orm import Account, Base, Tenant, TenantMember
from server.enterprise_access_graph_api import build_enterprise_access_graph_router
from server.knowledge_auth import issue_knowledge_actor_token

REVISION = "0021_enterprise_identity_federation"
TENANT_A = "tenant-identity-a"
TENANT_B = "tenant-identity-b"

SETTINGS = SimpleNamespace(
    run_history=RunHistorySettings(ops_bearer_token=SecretStr("ops-secret")),
    knowledge_security=KnowledgeSecuritySettings(
        actor_signing_secret=SecretStr("stage9-identity-secret"),
        actor_max_ttl_s=900,
    ),
    tenant=TenantSettings(enforced=True, default_tenant=TENANT_A),
)


class MemoryIdentityResolver:
    def __init__(
        self,
        *,
        txt: dict[str, tuple[str, ...]] | None = None,
        addresses: dict[str, tuple[str, ...]] | None = None,
        unavailable: bool = False,
    ) -> None:
        self.txt = txt or {}
        self.addresses = addresses or {}
        self.unavailable = unavailable
        self.txt_calls: list[str] = []
        self.address_calls: list[str] = []

    def resolve_txt(self, name: str) -> tuple[str, ...]:
        self.txt_calls.append(name)
        if self.unavailable:
            raise RuntimeError("resolver unavailable")
        return self.txt.get(name, ())

    def resolve_host_addresses(self, host: str) -> tuple[str, ...]:
        self.address_calls.append(host)
        if self.unavailable:
            raise RuntimeError("resolver unavailable")
        return self.addresses.get(host, ("93.184.216.34",))


def _enable_foreign_keys(engine: Any) -> None:
    @event.listens_for(engine, "connect")
    def _configure(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


def _install_0021(engine: Any, *, revision: str = REVISION) -> None:
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        for table in (
            "tenant_scim_tokens",
            "tenant_identity_providers",
            "tenant_verified_domains",
        ):
            connection.execute(text(f"DROP TABLE IF EXISTS {table}"))
        connection.execute(
            text(
                """
                CREATE TABLE tenant_verified_domains (
                    id VARCHAR(64) NOT NULL PRIMARY KEY,
                    tenant_id VARCHAR(64) NOT NULL,
                    normalized_domain VARCHAR(253) NOT NULL,
                    status VARCHAR(16) NOT NULL DEFAULT 'pending',
                    verification_method VARCHAR(16) NOT NULL DEFAULT 'dns_txt',
                    challenge_token VARCHAR(128) NOT NULL,
                    txt_host VARCHAR(320) NOT NULL,
                    txt_value VARCHAR(512) NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1,
                    last_checked_at DATETIME,
                    verified_at DATETIME,
                    verified_by VARCHAR(64),
                    revoked_at DATETIME,
                    revoked_by VARCHAR(64),
                    created_at DATETIME NOT NULL,
                    created_by VARCHAR(64) NOT NULL,
                    updated_at DATETIME NOT NULL,
                    updated_by VARCHAR(64) NOT NULL,
                    CONSTRAINT uq_tenant_verified_domains_global_domain UNIQUE (normalized_domain),
                    CONSTRAINT uq_tenant_verified_domains_scope_id UNIQUE (tenant_id, id),
                    CONSTRAINT ck_tenant_verified_domains_status
                        CHECK (status IN ('pending','verified','revoked')),
                    CONSTRAINT ck_tenant_verified_domains_method
                        CHECK (verification_method='dns_txt'),
                    CONSTRAINT ck_tenant_verified_domains_revision_positive CHECK (revision > 0),
                    CONSTRAINT ck_tenant_verified_domains_verified_evidence
                        CHECK (status <> 'verified' OR (verified_at IS NOT NULL AND verified_by IS NOT NULL)),
                    CONSTRAINT ck_tenant_verified_domains_revoked_evidence
                        CHECK (status <> 'revoked' OR (revoked_at IS NOT NULL AND revoked_by IS NOT NULL)),
                    CONSTRAINT fk_tenant_verified_domains_tenant
                        FOREIGN KEY (tenant_id) REFERENCES tenants (id),
                    CONSTRAINT fk_tenant_verified_domains_creator
                        FOREIGN KEY (created_by, tenant_id)
                        REFERENCES tenant_members (account_id, tenant_id),
                    CONSTRAINT fk_tenant_verified_domains_updater
                        FOREIGN KEY (updated_by, tenant_id)
                        REFERENCES tenant_members (account_id, tenant_id),
                    CONSTRAINT fk_tenant_verified_domains_verifier
                        FOREIGN KEY (verified_by, tenant_id)
                        REFERENCES tenant_members (account_id, tenant_id),
                    CONSTRAINT fk_tenant_verified_domains_revoker
                        FOREIGN KEY (revoked_by, tenant_id)
                        REFERENCES tenant_members (account_id, tenant_id)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE tenant_identity_providers (
                    id VARCHAR(64) NOT NULL PRIMARY KEY,
                    tenant_id VARCHAR(64) NOT NULL,
                    name VARCHAR(128) NOT NULL,
                    provider_type VARCHAR(16) NOT NULL,
                    status VARCHAR(16) NOT NULL DEFAULT 'draft',
                    active_slot VARCHAR(16),
                    trusted_domain_id VARCHAR(64) NOT NULL,
                    issuer_url VARCHAR(1024),
                    client_id VARCHAR(256),
                    secret_ref VARCHAR(512),
                    scopes VARCHAR(512),
                    entity_id VARCHAR(512),
                    sso_url VARCHAR(1024),
                    metadata_url VARCHAR(1024),
                    certificate_fingerprint VARCHAR(128),
                    validation_state VARCHAR(16) NOT NULL DEFAULT 'unchecked',
                    last_validated_at DATETIME,
                    validation_error VARCHAR(512),
                    metadata_hash VARCHAR(64),
                    revision INTEGER NOT NULL DEFAULT 1,
                    created_at DATETIME NOT NULL,
                    created_by VARCHAR(64) NOT NULL,
                    updated_at DATETIME NOT NULL,
                    updated_by VARCHAR(64) NOT NULL,
                    activated_at DATETIME,
                    activated_by VARCHAR(64),
                    disabled_at DATETIME,
                    disabled_by VARCHAR(64),
                    CONSTRAINT uq_tenant_identity_providers_active_slot
                        UNIQUE (tenant_id, active_slot),
                    CONSTRAINT ck_tenant_identity_providers_type
                        CHECK (provider_type IN ('oidc','saml')),
                    CONSTRAINT ck_tenant_identity_providers_status
                        CHECK (status IN ('draft','active','disabled')),
                    CONSTRAINT ck_tenant_identity_providers_validation
                        CHECK (validation_state IN ('unchecked','valid','invalid','unavailable')),
                    CONSTRAINT ck_tenant_identity_providers_revision_positive CHECK (revision > 0),
                    CONSTRAINT ck_tenant_identity_providers_active_slot
                        CHECK ((status='active' AND active_slot='primary') OR
                               (status<>'active' AND active_slot IS NULL)),
                    CONSTRAINT ck_tenant_identity_providers_type_fields
                        CHECK ((provider_type='oidc' AND issuer_url IS NOT NULL
                                AND client_id IS NOT NULL AND secret_ref IS NOT NULL) OR
                               (provider_type='saml' AND entity_id IS NOT NULL
                                AND sso_url IS NOT NULL AND certificate_fingerprint IS NOT NULL)),
                    CONSTRAINT fk_tenant_identity_providers_tenant
                        FOREIGN KEY (tenant_id) REFERENCES tenants (id),
                    CONSTRAINT fk_tenant_identity_providers_domain
                        FOREIGN KEY (tenant_id, trusted_domain_id)
                        REFERENCES tenant_verified_domains (tenant_id, id)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE tenant_scim_tokens (
                    id VARCHAR(64) NOT NULL PRIMARY KEY,
                    tenant_id VARCHAR(64) NOT NULL,
                    name VARCHAR(128) NOT NULL,
                    active_name_key VARCHAR(128),
                    token_hash VARCHAR(64) NOT NULL,
                    token_prefix VARCHAR(32) NOT NULL,
                    status VARCHAR(16) NOT NULL DEFAULT 'active',
                    scopes JSON NOT NULL,
                    expires_at DATETIME NOT NULL,
                    last_used_at DATETIME,
                    revision INTEGER NOT NULL DEFAULT 1,
                    issued_at DATETIME NOT NULL,
                    issued_by VARCHAR(64) NOT NULL,
                    revoked_at DATETIME,
                    revoked_by VARCHAR(64),
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    CONSTRAINT uq_tenant_scim_tokens_active_name
                        UNIQUE (tenant_id, active_name_key),
                    CONSTRAINT uq_tenant_scim_tokens_hash UNIQUE (tenant_id, token_hash),
                    CONSTRAINT ck_tenant_scim_tokens_status
                        CHECK (status IN ('active','revoked','expired')),
                    CONSTRAINT ck_tenant_scim_tokens_hash_length CHECK (length(token_hash)=64),
                    CONSTRAINT ck_tenant_scim_tokens_revision_positive CHECK (revision > 0),
                    CONSTRAINT ck_tenant_scim_tokens_active_name
                        CHECK ((status='active' AND active_name_key=name) OR
                               (status<>'active' AND active_name_key IS NULL)),
                    CONSTRAINT ck_tenant_scim_tokens_revoked_evidence
                        CHECK (status <> 'revoked' OR
                               (revoked_at IS NOT NULL AND revoked_by IS NOT NULL)),
                    CONSTRAINT fk_tenant_scim_tokens_tenant
                        FOREIGN KEY (tenant_id) REFERENCES tenants (id),
                    CONSTRAINT fk_tenant_scim_tokens_issuer
                        FOREIGN KEY (issued_by, tenant_id)
                        REFERENCES tenant_members (account_id, tenant_id),
                    CONSTRAINT fk_tenant_scim_tokens_revoker
                        FOREIGN KEY (revoked_by, tenant_id)
                        REFERENCES tenant_members (account_id, tenant_id)
                )
                """
            )
        )
        connection.execute(
            text(
                "CREATE INDEX ix_tenant_verified_domains_tenant_status_updated "
                "ON tenant_verified_domains (tenant_id, status, updated_at, id)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX ix_tenant_identity_providers_tenant_status "
                "ON tenant_identity_providers (tenant_id, status, updated_at, id)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX ix_tenant_scim_tokens_tenant_status_expires "
                "ON tenant_scim_tokens (tenant_id, status, expires_at, id)"
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


def _engine(*, revision: str = REVISION) -> Any:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _enable_foreign_keys(engine)
    _install_0021(engine, revision=revision)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id=TENANT_A, name="Identity Tenant A", status="active"),
                Tenant(id=TENANT_B, name="Identity Tenant B", status="active"),
                Account(id="owner-a", name="Owner A", email="owner-a@example.test"),
                Account(id="admin-a", name="Admin A", email="admin-a@example.test"),
                Account(id="editor-a", name="Editor A", email="editor-a@example.test"),
                Account(id="owner-b", name="Owner B", email="owner-b@example.test"),
            ]
        )
        session.flush()
        session.add_all(
            [
                TenantMember(
                    tenant_id=TENANT_A,
                    account_id="owner-a",
                    role="owner",
                    status="active",
                    revision=1,
                ),
                TenantMember(
                    tenant_id=TENANT_A,
                    account_id="admin-a",
                    role="admin",
                    status="active",
                    revision=1,
                ),
                TenantMember(
                    tenant_id=TENANT_A,
                    account_id="editor-a",
                    role="editor",
                    status="active",
                    revision=1,
                ),
                TenantMember(
                    tenant_id=TENANT_B,
                    account_id="owner-b",
                    role="owner",
                    status="active",
                    revision=1,
                ),
            ]
        )
        session.commit()
    return engine


def _headers(
    actor_id: str,
    *,
    tenant_id: str = TENANT_A,
    key: str = "stage9-key-0001",
) -> dict[str, str]:
    token = issue_knowledge_actor_token(
        actor_id,
        tenant_id,
        300,
        int(time.time()),
        settings=SETTINGS,
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-RAG4C-Tenant": tenant_id,
        "X-RAG4C-Actor": actor_id,
        "X-Request-ID": f"stage9-{actor_id}-{time.time_ns()}",
        "Idempotency-Key": key,
    }


def _client(engine: Any, resolver: Any) -> TestClient:
    app = FastAPI()
    app.state.knowledge_auth_engine = engine
    app.state.knowledge_auth_settings = SETTINGS
    kwargs = {
        "read_engine_provider": lambda: engine,
        "mutation_engine_provider": lambda: engine,
    }
    try:
        router = build_enterprise_access_graph_router(
            **kwargs,
            identity_resolver_provider=lambda: resolver,
        )
    except TypeError:
        router = build_enterprise_access_graph_router(**kwargs)
    app.include_router(router)
    return TestClient(app)


def _rows(engine: Any, table: str) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(text(f"SELECT * FROM {table} ORDER BY id")).mappings()
        ]


def _create_domain(
    client: TestClient,
    domain: str = "example.com",
    *,
    actor: str = "owner-a",
    tenant_id: str = TENANT_A,
    key: str = "stage9-domain-create",
) -> Any:
    return client.post(
        "/api/enterprise/identity/domains",
        headers=_headers(actor, tenant_id=tenant_id, key=key),
        json={"domain": domain, "reason": "企业域名接入"},
    )


def test_domain_create_and_dns_verify_with_injected_resolver() -> None:
    engine = _engine()
    resolver = MemoryIdentityResolver()
    client = _client(engine, resolver)
    created = _create_domain(client)

    assert created.status_code == 201, created.text
    domain = created.json()["domain"]
    assert domain["status"] == "pending"
    assert domain["dns_txt_host"] == "_rag4c-verify.example.com"
    assert domain["dns_txt_value"].startswith("rag4c-verification=")
    resolver.txt[domain["dns_txt_host"]] = (domain["dns_txt_value"],)

    verified = client.post(
        f"/api/enterprise/identity/domains/{domain['id']}/verify",
        headers=_headers("admin-a", key="stage9-domain-verify"),
        json={"revision": 1, "reason": "检查 TXT"},
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["domain"]["status"] == "verified"
    assert resolver.txt_calls == [domain["dns_txt_host"]]


def test_domain_global_uniqueness_cross_tenant_and_revoke_revision() -> None:
    engine = _engine()
    resolver = MemoryIdentityResolver()
    client = _client(engine, resolver)
    first = _create_domain(client, "Example.COM", key="stage9-domain-first")
    duplicate = _create_domain(client, "example.com", key="stage9-domain-duplicate")
    cross = _create_domain(
        client,
        "example.com",
        actor="owner-b",
        tenant_id=TENANT_B,
        key="stage9-domain-cross",
    )
    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert cross.status_code == 409
    assert cross.json()["detail"]["code"] == "identity_domain_already_claimed"

    domain_id = first.json()["domain"]["id"]
    stale = client.post(
        f"/api/enterprise/identity/domains/{domain_id}/revoke",
        headers=_headers("owner-a", key="stage9-domain-revoke-stale"),
        json={"revision": 9, "reason": "错误版本"},
    )
    revoked = client.post(
        f"/api/enterprise/identity/domains/{domain_id}/revoke",
        headers=_headers("owner-a", key="stage9-domain-revoke"),
        json={"revision": 1, "reason": "停止信任"},
    )
    assert stale.status_code == 409
    assert revoked.status_code == 200
    assert revoked.json()["domain"]["status"] == "revoked"


def test_dns_resolver_unavailable_is_explicit_and_keeps_domain_pending() -> None:
    engine = _engine()
    resolver = MemoryIdentityResolver(unavailable=True)
    client = _client(engine, resolver)
    created = _create_domain(client)
    domain_id = created.json()["domain"]["id"]
    response = client.post(
        f"/api/enterprise/identity/domains/{domain_id}/verify",
        headers=_headers("owner-a", key="stage9-dns-unavailable"),
        json={"revision": 1, "reason": "检查 DNS"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "identity_dns_resolver_unavailable"
    assert _rows(engine, "tenant_verified_domains")[0]["status"] == "pending"


def test_dns_mismatch_records_pending_check_and_replays_same_409() -> None:
    engine = _engine()
    resolver = MemoryIdentityResolver()
    client = _client(engine, resolver)
    domain = _create_domain(client).json()["domain"]
    resolver.txt[domain["dns_txt_host"]] = ("unrelated=value",)
    headers = _headers("owner-a", key="stage9-domain-check-mismatch")
    body = {"revision": 1, "reason": "检查 TXT 不匹配"}

    first = client.post(
        f"/api/enterprise/identity/domains/{domain['id']}/verify",
        headers=headers,
        json=body,
    )
    audit_count = len(_rows(engine, "tenant_audit_events"))
    ledger_count = len(_rows(engine, "tenant_control_mutation_requests"))
    second = client.post(
        f"/api/enterprise/identity/domains/{domain['id']}/verify",
        headers=headers,
        json=body,
    )

    assert first.status_code == 409
    assert second.status_code == 409
    assert first.json()["detail"] == second.json()["detail"]
    assert first.json()["detail"]["code"] == "identity_domain_verification_failed"
    row = _rows(engine, "tenant_verified_domains")[0]
    assert row["status"] == "pending"
    assert row["last_checked_at"] is not None
    assert len(_rows(engine, "tenant_audit_events")) == audit_count
    assert len(_rows(engine, "tenant_control_mutation_requests")) == ledger_count


@pytest.mark.parametrize(
    "url",
    [
        "http://id.example.com",
        "https://127.0.0.1",
        "https://[::1]",
        "https://10.0.0.8",
        "https://169.254.169.254/latest/meta-data",
        "https://user:pass@id.example.com",
        "https://localhost",
    ],
)
def test_oidc_rejects_non_https_and_ssrf_endpoints(url: str) -> None:
    engine = _engine()
    resolver = MemoryIdentityResolver(addresses={"id.example.com": ("93.184.216.34",)})
    client = _client(engine, resolver)
    domain = _create_domain(client).json()["domain"]
    response = client.post(
        "/api/enterprise/identity/providers",
        headers=_headers("owner-a", key=f"stage9-ssrf-{abs(hash(url))}"),
        json={
            "name": "Corporate OIDC",
            "provider_type": "oidc",
            "domain_id": domain["id"],
            "issuer_url": url,
            "client_id": "rag4c-client",
            "secret_ref": "vault://identity/oidc/client-secret",
            "scopes": ["openid", "profile", "email"],
            "reason": "配置 OIDC",
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "identity_endpoint_unsafe"


def test_oidc_secret_ref_only_and_runtime_not_connected() -> None:
    engine = _engine()
    resolver = MemoryIdentityResolver(addresses={"id.example.com": ("93.184.216.34",)})
    client = _client(engine, resolver)
    domain = _create_domain(client).json()["domain"]
    raw_secret = client.post(
        "/api/enterprise/identity/providers",
        headers=_headers("owner-a", key="stage9-raw-secret"),
        json={
            "name": "Bad OIDC",
            "provider_type": "oidc",
            "domain_id": domain["id"],
            "issuer_url": "https://id.example.com",
            "client_id": "rag4c-client",
            "secret_ref": "sk_live_raw_secret_value",
            "scopes": ["openid"],
            "reason": "raw secret",
        },
    )
    assert raw_secret.status_code == 422
    assert raw_secret.json()["detail"]["code"] == "identity_secret_ref_invalid"

    created = client.post(
        "/api/enterprise/identity/providers",
        headers=_headers("admin-a", key="stage9-oidc-create"),
        json={
            "name": "Corporate OIDC",
            "provider_type": "oidc",
            "domain_id": domain["id"],
            "issuer_url": "https://id.example.com",
            "client_id": "rag4c-client",
            "secret_ref": "vault://identity/oidc/client-secret",
            "scopes": ["openid", "email"],
            "reason": "draft config",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["provider"]["status"] == "draft"
    assert created.json()["provider"]["validation_state"] == "valid"
    assert created.json()["runtime"]["state"] == "not_connected"
    serialized = json.dumps(_rows(engine, "tenant_identity_providers"), ensure_ascii=False)
    assert "sk_live_raw_secret_value" not in serialized


def test_provider_activation_requires_verified_domain_and_owner() -> None:
    engine = _engine()
    resolver = MemoryIdentityResolver(addresses={"id.example.com": ("93.184.216.34",)})
    client = _client(engine, resolver)
    domain = _create_domain(client).json()["domain"]
    provider = client.post(
        "/api/enterprise/identity/providers",
        headers=_headers("admin-a", key="stage9-provider-draft"),
        json={
            "name": "Corporate OIDC",
            "provider_type": "oidc",
            "domain_id": domain["id"],
            "issuer_url": "https://id.example.com",
            "client_id": "client",
            "secret_ref": "env://OIDC_CLIENT_SECRET",
            "scopes": ["openid"],
            "reason": "draft",
        },
    ).json()["provider"]

    unverified = client.post(
        f"/api/enterprise/identity/providers/{provider['id']}/activate",
        headers=_headers("owner-a", key="stage9-activate-unverified"),
        json={"revision": 1, "reason": "activate"},
    )
    admin = client.post(
        f"/api/enterprise/identity/providers/{provider['id']}/activate",
        headers=_headers("admin-a", key="stage9-activate-admin"),
        json={"revision": 1, "reason": "activate"},
    )
    assert unverified.status_code == 409
    assert unverified.json()["detail"]["code"] == "identity_domain_not_verified"
    assert admin.status_code == 403
    assert admin.json()["detail"]["code"] == "identity_owner_required"


def test_provider_update_activate_and_owner_only_disable_lifecycle() -> None:
    engine = _engine()
    resolver = MemoryIdentityResolver(addresses={"id.example.com": ("93.184.216.34",)})
    client = _client(engine, resolver)
    domain = _create_domain(client).json()["domain"]
    resolver.txt[domain["dns_txt_host"]] = (domain["dns_txt_value"],)
    verified = client.post(
        f"/api/enterprise/identity/domains/{domain['id']}/verify",
        headers=_headers("owner-a", key="stage9-provider-domain-verify"),
        json={"revision": 1, "reason": "verify"},
    )
    assert verified.status_code == 200
    provider = client.post(
        "/api/enterprise/identity/providers",
        headers=_headers("admin-a", key="stage9-provider-lifecycle-create"),
        json={
            "name": "Corporate OIDC",
            "provider_type": "oidc",
            "domain_id": domain["id"],
            "issuer_url": "https://id.example.com",
            "client_id": "client-v1",
            "secret_ref": "vault://identity/oidc/v1",
            "scopes": ["openid"],
            "reason": "create",
        },
    ).json()["provider"]

    updated = client.patch(
        f"/api/enterprise/identity/providers/{provider['id']}",
        headers=_headers("admin-a", key="stage9-provider-lifecycle-update"),
        json={
            "revision": 1,
            "issuer_url": "https://id.example.com",
            "client_id": "client-v2",
            "secret_ref": "vault://identity/oidc/v2",
            "scopes": ["openid", "email"],
            "reason": "update",
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["provider"]["revision"] == 2

    activated = client.post(
        f"/api/enterprise/identity/providers/{provider['id']}/activate",
        headers=_headers("owner-a", key="stage9-provider-lifecycle-activate"),
        json={"revision": 2, "reason": "activate"},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["provider"]["status"] == "active"

    admin_disable = client.post(
        f"/api/enterprise/identity/providers/{provider['id']}/disable",
        headers=_headers("admin-a", key="stage9-provider-lifecycle-admin-disable"),
        json={"revision": 3, "reason": "disable"},
    )
    owner_disable = client.post(
        f"/api/enterprise/identity/providers/{provider['id']}/disable",
        headers=_headers("owner-a", key="stage9-provider-lifecycle-owner-disable"),
        json={"revision": 3, "reason": "disable"},
    )
    assert admin_disable.status_code == 403
    assert admin_disable.json()["detail"]["code"] == "identity_owner_required"
    assert owner_disable.status_code == 200
    assert owner_disable.json()["provider"]["status"] == "disabled"
    assert owner_disable.json()["runtime"]["state"] == "not_connected"


def test_scim_token_issue_list_replay_redaction_and_revoke() -> None:
    engine = _engine()
    resolver = MemoryIdentityResolver()
    client = _client(engine, resolver)
    body = {
        "name": "HR provisioning",
        "scopes": ["users:read", "users:write"],
        "expires_in_days": 30,
        "reason": "SCIM integration",
    }
    first = client.post(
        "/api/enterprise/identity/scim-tokens",
        headers=_headers("admin-a", key="stage9-scim-issue"),
        json=body,
    )
    replay = client.post(
        "/api/enterprise/identity/scim-tokens",
        headers=_headers("admin-a", key="stage9-scim-issue"),
        json=body,
    )
    assert first.status_code == 201, first.text
    raw = first.json()["delivery"]["scim_token"]
    assert raw
    assert replay.status_code == 201
    assert replay.json()["delivery"]["state"] == "token_already_issued"
    assert "scim_token" not in replay.json()["delivery"]

    listed = client.get(
        "/api/enterprise/identity/scim-tokens",
        headers=_headers("editor-a", key="unused-list-key"),
    )
    assert listed.status_code == 200
    assert listed.json()["data_plane"]["state"] == "not_connected"
    serialized = json.dumps(listed.json(), ensure_ascii=False)
    assert raw not in serialized
    assert "token_hash" not in serialized

    token = first.json()["token"]
    revoked = client.post(
        f"/api/enterprise/identity/scim-tokens/{token['id']}/revoke",
        headers=_headers("admin-a", key="stage9-scim-revoke"),
        json={"revision": 1, "reason": "rotate credentials"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["token"]["status"] == "revoked"


def test_identity_mutations_are_idempotent_audited_and_0021_gated() -> None:
    engine = _engine()
    resolver = MemoryIdentityResolver()
    client = _client(engine, resolver)
    first = _create_domain(client, key="stage9-domain-idempotent")
    replay = _create_domain(client, key="stage9-domain-idempotent")
    conflict = _create_domain(
        client,
        domain="different.example.com",
        key="stage9-domain-idempotent",
    )
    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json() == first.json()
    assert conflict.status_code == 409
    assert len(_rows(engine, "tenant_verified_domains")) == 1
    assert len(_rows(engine, "tenant_audit_events")) == 1

    behind = _engine(revision="0020_tenant_invitation_lifecycle")
    response = _create_domain(
        _client(behind, resolver),
        key="stage9-behind-gate",
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "identity_federation_migration_required"
    assert "0021_enterprise_identity_federation" in response.json()["detail"]["message"]
