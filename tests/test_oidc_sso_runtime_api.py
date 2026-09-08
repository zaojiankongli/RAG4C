from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from types import SimpleNamespace
import hashlib
import importlib
import json
import threading

import pytest
from typing import Any
from urllib.parse import urlencode

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from core.knowledge_permissions import KNOWLEDGE_READ
from server.knowledge_auth import KnowledgeActor, require_knowledge_permission
from config.settings import KnowledgeSecuritySettings
from tests.test_enterprise_identity_federation_api import (
    SETTINGS,
    TENANT_A,
    _engine as identity_engine,
    _headers as actor_headers,
)

REVISION = "0024_oidc_sso_runtime"
NOW = datetime(2026, 8, 26, 12, 0, 0)
REDIRECT_URI = "https://app.example.test/auth/oidc/callback"
PROVIDER_ID = "provider-oidc-primary"
DOMAIN_ID = "domain-oidc-primary"


class MemoryOidcRuntimeClient:
    def __init__(self) -> None:
        self.authorization_calls: list[dict[str, Any]] = []
        self.exchange_calls: list[dict[str, Any]] = []
        self.identity: dict[str, Any] = {
            "issuer": "https://id.example.com",
            "subject": "subject-admin-a",
            "email": "admin-a@example.test",
            "email_verified": True,
            "audience": "rag4c-client",
        }
        self.error_code: str | None = None

    def build_authorization_url(
        self,
        *,
        provider: dict[str, Any],
        redirect_uri: str,
        state: str,
        nonce: str,
        code_challenge: str,
    ) -> str:
        self.authorization_calls.append(
            {
                "provider": provider,
                "redirect_uri": redirect_uri,
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
            }
        )
        return "https://id.example.com/authorize?" + urlencode(
            {
                "client_id": provider["client_id"],
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": "openid email profile",
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )

    def exchange_and_validate(
        self,
        *,
        provider: dict[str, Any],
        code: str,
        redirect_uri: str,
        code_verifier: str,
        nonce: str,
    ) -> dict[str, Any]:
        self.exchange_calls.append(
            {
                "provider": provider,
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
                "nonce": nonce,
            }
        )
        if self.error_code:
            raise RuntimeError(self.error_code)
        return dict(self.identity)


def _install_0024(engine: Any, *, revision: str = REVISION) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        for table in (
            "tenant_sso_sessions",
            "tenant_oidc_subject_links",
            "tenant_oidc_login_transactions",
        ):
            connection.execute(text(f"DROP TABLE IF EXISTS {table}"))
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_test_identity_provider_scope_id "
                "ON tenant_identity_providers (tenant_id,id)"
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE tenant_oidc_login_transactions (
                    id VARCHAR(64) PRIMARY KEY,
                    tenant_id VARCHAR(64) NOT NULL,
                    provider_id VARCHAR(64) NOT NULL,
                    state_digest VARCHAR(64) NOT NULL,
                    nonce_digest VARCHAR(64) NOT NULL,
                    pkce_verifier_ciphertext TEXT NOT NULL,
                    key_version INTEGER NOT NULL,
                    redirect_uri VARCHAR(1024) NOT NULL,
                    expires_at DATETIME NOT NULL,
                    status VARCHAR(16) NOT NULL,
                    consumed_at DATETIME,
                    failed_at DATETIME,
                    error_code VARCHAR(64),
                    revision INTEGER NOT NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    CONSTRAINT uq_tenant_oidc_login_state UNIQUE (state_digest),
                    CONSTRAINT ck_tenant_oidc_login_status
                        CHECK (status IN ('pending','consumed','failed','expired')),
                    CONSTRAINT ck_tenant_oidc_login_digests
                        CHECK (length(state_digest)=64 AND length(nonce_digest)=64),
                    CONSTRAINT ck_tenant_oidc_login_revisions
                        CHECK (key_version > 0 AND revision > 0),
                    CONSTRAINT fk_tenant_oidc_login_provider
                        FOREIGN KEY (tenant_id,provider_id)
                        REFERENCES tenant_identity_providers (tenant_id,id)
                )
                """
            )
        )
        connection.execute(
            text(
                "CREATE INDEX ix_tenant_oidc_login_tenant_status "
                "ON tenant_oidc_login_transactions (tenant_id,status,expires_at,id)"
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE tenant_oidc_subject_links (
                    id VARCHAR(64) PRIMARY KEY,
                    tenant_id VARCHAR(64) NOT NULL,
                    provider_id VARCHAR(64) NOT NULL,
                    account_id VARCHAR(64) NOT NULL,
                    issuer VARCHAR(1024) NOT NULL,
                    subject_digest VARCHAR(64) NOT NULL,
                    normalized_email VARCHAR(256) NOT NULL,
                    revision INTEGER NOT NULL,
                    last_login_at DATETIME,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    CONSTRAINT uq_tenant_oidc_subject
                        UNIQUE (tenant_id,provider_id,subject_digest),
                    CONSTRAINT uq_tenant_oidc_account
                        UNIQUE (tenant_id,provider_id,account_id),
                    CONSTRAINT fk_tenant_oidc_subject_provider
                        FOREIGN KEY (tenant_id,provider_id)
                        REFERENCES tenant_identity_providers (tenant_id,id),
                    CONSTRAINT fk_tenant_oidc_subject_member
                        FOREIGN KEY (account_id,tenant_id)
                        REFERENCES tenant_members (account_id,tenant_id)
                )
                """
            )
        )
        connection.execute(
            text(
                "CREATE INDEX ix_tenant_oidc_subject_tenant_email "
                "ON tenant_oidc_subject_links (tenant_id,normalized_email,id)"
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE tenant_sso_sessions (
                    id VARCHAR(64) PRIMARY KEY,
                    session_token_hash VARCHAR(64) NOT NULL,
                    tenant_id VARCHAR(64) NOT NULL,
                    account_id VARCHAR(64) NOT NULL,
                    provider_id VARCHAR(64) NOT NULL,
                    status VARCHAR(16) NOT NULL,
                    expires_at DATETIME NOT NULL,
                    last_seen_at DATETIME,
                    ip_hash VARCHAR(64),
                    user_agent_hash VARCHAR(64),
                    revision INTEGER NOT NULL,
                    revoked_at DATETIME,
                    revoked_by VARCHAR(64),
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    CONSTRAINT uq_tenant_sso_session_token UNIQUE (session_token_hash),
                    CONSTRAINT ck_tenant_sso_sessions_status
                        CHECK (status IN ('active','revoked','expired')),
                    CONSTRAINT ck_tenant_sso_sessions_digest_revision
                        CHECK (length(session_token_hash)=64 AND revision > 0),
                    CONSTRAINT fk_tenant_sso_sessions_member
                        FOREIGN KEY (account_id,tenant_id)
                        REFERENCES tenant_members (account_id,tenant_id),
                    CONSTRAINT fk_tenant_sso_sessions_provider
                        FOREIGN KEY (tenant_id,provider_id)
                        REFERENCES tenant_identity_providers (tenant_id,id)
                )
                """
            )
        )
        connection.execute(
            text(
                "CREATE INDEX ix_tenant_sso_sessions_tenant_status "
                "ON tenant_sso_sessions (tenant_id,status,expires_at,id)"
            )
        )
        connection.execute(text("DELETE FROM tenant_identity_providers"))
        connection.execute(text("DELETE FROM tenant_verified_domains"))
        connection.execute(
            text(
                """
                INSERT INTO tenant_verified_domains (
                    id,tenant_id,normalized_domain,status,verification_method,
                    challenge_token,txt_host,txt_value,revision,last_checked_at,
                    verified_at,verified_by,created_at,created_by,updated_at,updated_by
                ) VALUES (
                    :id,:tenant,'example.test','verified','dns_txt','challenge',
                    '_rag4c.example.test','rag4c-domain-verification=challenge',1,:now,
                    :now,'owner-a',:now,'owner-a',:now,'owner-a'
                )
                """
            ),
            {"id": DOMAIN_ID, "tenant": TENANT_A, "now": NOW},
        )
        connection.execute(
            text(
                """
                INSERT INTO tenant_identity_providers (
                    id,tenant_id,name,provider_type,status,active_slot,trusted_domain_id,
                    issuer_url,client_id,secret_ref,scopes,entity_id,sso_url,metadata_url,
                    certificate_fingerprint,validation_state,last_validated_at,validation_error,
                    metadata_hash,revision,created_at,created_by,updated_at,updated_by,activated_at,activated_by
                ) VALUES (
                    :id,:tenant,'Primary OIDC','oidc','active','primary',:domain,
                    'https://id.example.com','rag4c-client','env://OIDC_CLIENT_SECRET',
                    :scopes,NULL,NULL,NULL,NULL,'valid',:now,NULL,NULL,1,:now,'owner-a',
                    :now,'owner-a',:now,'owner-a'
                )
                """
            ),
            {
                "id": PROVIDER_ID,
                "tenant": TENANT_A,
                "domain": DOMAIN_ID,
                "scopes": json.dumps(["openid", "email", "profile"]),
                "now": NOW,
            },
        )
        connection.execute(text("DELETE FROM alembic_version"))
        connection.execute(
            text("INSERT INTO alembic_version(version_num) VALUES (:revision)"),
            {"revision": revision},
        )
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")


def _engine(*, revision: str = REVISION) -> Any:
    engine = identity_engine()
    _install_0024(engine, revision=revision)
    return engine


def _file_engine(tmp_path: Any, *, revision: str = REVISION) -> Any:
    source = identity_engine()
    target = create_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'oidc-concurrency.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    source_raw = source.raw_connection()
    target_raw = target.raw_connection()
    try:
        source_raw.driver_connection.backup(target_raw.driver_connection)
    finally:
        target_raw.close()
        source_raw.close()
        source.dispose()
    _install_0024(target, revision=revision)
    return target


def _client(
    engine: Any,
    runtime_client: MemoryOidcRuntimeClient,
    *,
    base_url: str = "https://testserver",
) -> TestClient:
    app = FastAPI()
    app.state.knowledge_auth_engine = engine
    app.state.knowledge_auth_settings = SETTINGS
    try:
        module = importlib.import_module("server.oidc_runtime_api")
    except ModuleNotFoundError:
        return TestClient(app, base_url=base_url)
    app.include_router(
        module.build_oidc_runtime_router(
            read_engine_provider=lambda: engine,
            mutation_engine_provider=lambda: engine,
            oidc_client_provider=lambda: runtime_client,
            signing_secret_provider=lambda: "stage12-oidc-fernet-secret",
            actor_token_settings_provider=lambda: SETTINGS,
            redirect_uri_allowlist_provider=lambda: (REDIRECT_URI,),
            now_provider=lambda: NOW,
        )
    )

    @app.get("/whoami")
    def whoami(
        actor: KnowledgeActor = Depends(require_knowledge_permission(KNOWLEDGE_READ)),
    ) -> dict[str, str]:
        return {"account_id": actor.account_id, "tenant_id": actor.tenant_id}

    return TestClient(app, base_url=base_url)


def _rows(engine: Any, table: str) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(text(f"SELECT * FROM {table}")).mappings()]


def _start(
    client: TestClient,
    *,
    redirect_uri: str = REDIRECT_URI,
    body_tenant_id: str = TENANT_A,
    actor_id: str = "owner-a",
) -> Any:
    return client.post(
        "/api/enterprise/sso/oidc/start",
        headers=actor_headers(actor_id, tenant_id=TENANT_A, key="oidc-start"),
        json={
            "tenant_id": body_tenant_id,
            "provider_id": PROVIDER_ID,
            "redirect_uri": redirect_uri,
        },
    )


def test_fernet_domain_separation_encrypts_pkce_without_plaintext() -> None:
    module = importlib.import_module("core.enterprise_oidc_runtime")
    key = module.derive_oidc_fernet_key("stage12-signing-secret", key_version=1)
    other = module.derive_oidc_fernet_key("stage12-signing-secret", key_version=2)
    encrypted = module.encrypt_pkce_verifier("verifier-secret", key)
    assert key != other
    assert b"stage12-signing-secret" not in key
    assert "verifier-secret" not in encrypted
    assert module.decrypt_pkce_verifier(encrypted, key) == "verifier-secret"


def test_start_uses_active_provider_verified_domain_state_nonce_and_pkce() -> None:
    engine = _engine()
    runtime = MemoryOidcRuntimeClient()
    response = _start(_client(engine, runtime))
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["authorization_url"].startswith("https://id.example.com/authorize?")
    assert payload["provider_id"] == PROVIDER_ID
    assert payload["state"]
    assert "nonce" not in payload
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"]
    assert runtime.authorization_calls[0]["code_challenge"]
    row = _rows(engine, "tenant_oidc_login_transactions")[0]
    serialized = json.dumps(row, default=str)
    assert payload["state"] not in serialized
    assert runtime.authorization_calls[0]["nonce"] not in serialized
    assert runtime.authorization_calls[0]["code_challenge"] not in serialized
    assert runtime.authorization_calls[0]["provider"]["secret_ref"] == "env://OIDC_CLIENT_SECRET"


def test_start_rejects_unallowlisted_redirect_and_inactive_provider() -> None:
    engine = _engine()
    client = _client(engine, MemoryOidcRuntimeClient())
    unsafe = _start(client, redirect_uri="https://evil.example/callback")
    assert unsafe.status_code == 422
    assert unsafe.json()["detail"]["code"] == "oidc_redirect_uri_forbidden"
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE tenant_identity_providers SET status='disabled', active_slot=NULL WHERE id=:id"
            ),
            {"id": PROVIDER_ID},
        )
    inactive = _start(client)
    assert inactive.status_code == 409
    assert inactive.json()["detail"]["code"] == "oidc_provider_unavailable"


def test_callback_creates_subject_session_actor_token_and_consumes_state(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr("server.knowledge_auth.time.time", lambda: NOW.timestamp())
    engine = _engine()
    runtime = MemoryOidcRuntimeClient()
    client = _client(engine, runtime)
    state = _start(client).json()["state"]
    callback = client.post(
        "/api/enterprise/sso/oidc/callback",
        json={"state": state, "code": "authorization-code"},
        headers={"User-Agent": "Stage12-Test-Agent"},
    )
    assert callback.status_code == 200, callback.text
    payload = callback.json()
    assert payload["session_token"]
    assert payload["actor_token"]
    assert payload["knowledge_actor_token"] == payload["actor_token"]
    assert payload["status"] == "authenticated"
    assert payload["actor"] == {
        "id": "admin-a",
        "name": "Admin A",
        "email": "admin-a@example.test",
        "role": "admin",
    }
    assert payload["tenant"] == {"id": TENANT_A, "name": "Identity Tenant A"}
    assert payload["provider"] == {"id": PROVIDER_ID, "name": "Primary OIDC"}
    assert payload["session"]["status"] == "active"
    whoami = client.get(
        "/whoami",
        headers={
            "Authorization": f"Bearer {payload['actor_token']}",
            "X-RAG4C-Tenant": TENANT_A,
            "X-RAG4C-Actor": "admin-a",
        },
    )
    assert whoami.status_code == 200, whoami.text
    assert whoami.json() == {"account_id": "admin-a", "tenant_id": TENANT_A}
    assert _rows(engine, "tenant_oidc_login_transactions")[0]["status"] == "consumed"
    assert len(_rows(engine, "tenant_oidc_subject_links")) == 1
    session = _rows(engine, "tenant_sso_sessions")[0]
    assert (
        session["session_token_hash"]
        == hashlib.sha256(
            b"rag4c:tenant-sso-session:v1\x00" + payload["session_token"].encode()
        ).hexdigest()
    )
    persisted = json.dumps(
        {
            "login": _rows(engine, "tenant_oidc_login_transactions"),
            "subject": _rows(engine, "tenant_oidc_subject_links"),
            "session": _rows(engine, "tenant_sso_sessions"),
            "audit": _rows(engine, "tenant_audit_events"),
        },
        default=str,
    )
    assert "authorization-code" not in persisted
    assert payload["session_token"] not in persisted
    assert payload["actor_token"] not in persisted


def test_callback_replay_and_invalid_state_are_stable_and_do_not_duplicate() -> None:
    engine = _engine()
    client = _client(engine, MemoryOidcRuntimeClient())
    state = _start(client).json()["state"]
    first = client.post(
        "/api/enterprise/sso/oidc/callback",
        json={"state": state, "code": "code-one"},
    )
    replay_without_cookie = client.post(
        "/api/enterprise/sso/oidc/callback",
        json={"state": state, "code": "code-two"},
    )
    replay_with_matching_cookie = client.post(
        "/api/enterprise/sso/oidc/callback",
        json={"state": state, "code": "code-two"},
        headers={"Cookie": f"rag4c_oidc_state={state}"},
    )
    invalid_with_matching_cookie = client.post(
        "/api/enterprise/sso/oidc/callback",
        json={"state": "unknown-state", "code": "code-three"},
        headers={"Cookie": "rag4c_oidc_state=unknown-state"},
    )
    assert first.status_code == 200
    assert replay_without_cookie.status_code == 403
    assert replay_without_cookie.json()["detail"]["code"] == "oidc_state_cookie_mismatch"
    assert replay_with_matching_cookie.status_code == 409
    assert replay_with_matching_cookie.json()["detail"]["code"] == "oidc_state_consumed"
    assert invalid_with_matching_cookie.status_code == 404
    assert len(_rows(engine, "tenant_sso_sessions")) == 1
    assert len(_rows(engine, "tenant_oidc_subject_links")) == 1


def test_callback_requires_verified_email_existing_account_and_active_member() -> None:
    engine = _engine()
    runtime = MemoryOidcRuntimeClient()
    runtime.identity["email"] = "unknown@example.test"
    client = _client(engine, runtime)
    state = _start(client).json()["state"]
    response = client.post(
        "/api/enterprise/sso/oidc/callback",
        json={"state": state, "code": "code"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "oidc_member_required"
    assert _rows(engine, "tenant_sso_sessions") == []
    assert _rows(engine, "tenant_oidc_subject_links") == []
    transaction = _rows(engine, "tenant_oidc_login_transactions")[0]
    assert transaction["status"] == "failed"
    assert transaction["error_code"] == "oidc_member_required"


def test_callback_email_domain_and_subject_binding_are_tenant_safe() -> None:
    engine = _engine()
    runtime = MemoryOidcRuntimeClient()
    runtime.identity["email"] = "admin-a@other.test"
    client = _client(engine, runtime)
    state = _start(client).json()["state"]
    mismatch = client.post(
        "/api/enterprise/sso/oidc/callback",
        json={"state": state, "code": "code"},
    )
    assert mismatch.status_code == 403
    assert mismatch.json()["detail"]["code"] == "oidc_email_domain_mismatch"

    runtime.identity["email"] = "admin-a@example.test"
    first_state = _start(client).json()["state"]
    assert (
        client.post(
            "/api/enterprise/sso/oidc/callback",
            json={"state": first_state, "code": "code-one"},
        ).status_code
        == 200
    )
    runtime.identity["subject"] = "different-subject"
    second_state = _start(client).json()["state"]
    conflict = client.post(
        "/api/enterprise/sso/oidc/callback",
        json={"state": second_state, "code": "code-two"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "oidc_subject_binding_conflict"


def test_callback_audit_failure_rolls_back_session_subject_and_consume(monkeypatch: Any) -> None:
    engine = _engine()
    runtime = MemoryOidcRuntimeClient()
    client = _client(engine, runtime)
    state = _start(client).json()["state"]
    module = importlib.import_module("core.enterprise_oidc_runtime")

    def fail_audit(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(module, "_audit", fail_audit)
    response = client.post(
        "/api/enterprise/sso/oidc/callback",
        json={"state": state, "code": "code"},
    )
    assert response.status_code == 503
    assert _rows(engine, "tenant_sso_sessions") == []
    assert _rows(engine, "tenant_oidc_subject_links") == []
    assert _rows(engine, "tenant_oidc_login_transactions")[0]["status"] == "pending"


def test_session_revoke_allows_self_or_manager_and_fences_revision(monkeypatch: Any) -> None:
    monkeypatch.setattr("server.knowledge_auth.time.time", lambda: NOW.timestamp())
    engine = _engine()
    client = _client(engine, MemoryOidcRuntimeClient())
    state = _start(client).json()["state"]
    callback_payload = client.post(
        "/api/enterprise/sso/oidc/callback",
        json={"state": state, "code": "code"},
    ).json()
    session_payload = callback_payload["session"]
    oidc_actor_headers = {
        "Authorization": f"Bearer {callback_payload['actor_token']}",
        "X-RAG4C-Tenant": TENANT_A,
        "X-RAG4C-Actor": "admin-a",
    }
    assert client.get("/whoami", headers=oidc_actor_headers).status_code == 200
    stale = client.post(
        f"/api/enterprise/sso/sessions/{session_payload['id']}/revoke",
        headers=actor_headers("admin-a", key="unused"),
        json={"revision": 2, "reason": "logout"},
    )
    assert stale.status_code == 409
    revoked = client.post(
        f"/api/enterprise/sso/sessions/{session_payload['id']}/revoke",
        headers=actor_headers("admin-a", key="unused"),
        json={"revision": 1, "reason": "logout"},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["session"]["status"] == "revoked"
    assert revoked.json()["session"]["revision"] == 2
    rejected = client.get("/whoami", headers=oidc_actor_headers)
    assert rejected.status_code == 401
    assert rejected.json()["detail"]["code"] == "knowledge_sso_session_inactive"


def test_missing_0024_fails_closed_before_login_write() -> None:
    engine = _engine(revision="0023_enterprise_audit_compliance")
    response = _start(_client(engine, MemoryOidcRuntimeClient()))
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "oidc_runtime_migration_required"
    assert _rows(engine, "tenant_oidc_login_transactions") == []


def test_router_default_allowlist_comes_from_knowledge_security_settings() -> None:
    engine = _engine()
    runtime = MemoryOidcRuntimeClient()
    module = importlib.import_module("server.oidc_runtime_api")
    configured_settings = SimpleNamespace(
        knowledge_security=KnowledgeSecuritySettings(
            actor_signing_secret=SETTINGS.knowledge_security.actor_signing_secret,
            actor_max_ttl_s=SETTINGS.knowledge_security.actor_max_ttl_s,
            oidc_redirect_uri_allowlist=[REDIRECT_URI],
        )
    )
    app = FastAPI()
    app.state.knowledge_auth_engine = engine
    app.state.knowledge_auth_settings = configured_settings
    app.include_router(
        module.build_oidc_runtime_router(
            read_engine_provider=lambda: engine,
            mutation_engine_provider=lambda: engine,
            oidc_client_provider=lambda: runtime,
            signing_secret_provider=lambda: "stage12-oidc-fernet-secret",
            actor_token_settings_provider=lambda: configured_settings,
            now_provider=lambda: NOW,
        )
    )
    response = TestClient(app, base_url="https://testserver").post(
        "/api/enterprise/sso/oidc/start",
        headers=actor_headers("owner-a", tenant_id=TENANT_A, key="oidc-default-allowlist"),
        json={"tenant_id": TENANT_A, "provider_id": PROVIDER_ID, "redirect_uri": REDIRECT_URI},
    )
    assert response.status_code == 201, response.text


def test_callback_requires_matching_state_cookie_without_consuming_transaction() -> None:
    engine = _engine()
    client = _client(engine, MemoryOidcRuntimeClient())
    state = _start(client).json()["state"]
    client.cookies.clear()
    missing = client.post(
        "/api/enterprise/sso/oidc/callback",
        json={"state": state, "code": "code-missing-cookie"},
    )
    assert missing.status_code == 403
    assert missing.json()["detail"]["code"] == "oidc_state_cookie_mismatch"
    assert _rows(engine, "tenant_oidc_login_transactions")[0]["status"] == "pending"

    second_state = _start(client).json()["state"]
    client.cookies.clear()
    mismatch = client.post(
        "/api/enterprise/sso/oidc/callback",
        json={"state": second_state, "code": "code-mismatch"},
        headers={"Cookie": "rag4c_oidc_state=wrong-state"},
    )
    assert mismatch.status_code == 403
    assert mismatch.json()["detail"]["code"] == "oidc_state_cookie_mismatch"
    rows = _rows(engine, "tenant_oidc_login_transactions")
    assert all(row["status"] == "pending" for row in rows)


def test_callback_actor_token_uses_injected_clock(monkeypatch: Any) -> None:
    engine = _engine()
    client = _client(engine, MemoryOidcRuntimeClient())
    state = _start(client).json()["state"]
    module = importlib.import_module("server.oidc_runtime_api")
    captured: dict[str, Any] = {}

    def fake_issue(
        account_id: str,
        tenant_id: str,
        ttl: int,
        issued_at: int,
        *,
        settings: Any,
        session_id: str | None = None,
    ) -> str:
        captured.update(
            account_id=account_id,
            tenant_id=tenant_id,
            ttl=ttl,
            issued_at=issued_at,
            settings=settings,
            session_id=session_id,
        )
        return "synthetic-actor-token"

    monkeypatch.setattr(module, "issue_knowledge_actor_token", fake_issue)
    response = client.post(
        "/api/enterprise/sso/oidc/callback",
        json={"state": state, "code": "code"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["actor_token"] == "synthetic-actor-token"
    assert captured["issued_at"] == int(NOW.timestamp())


def test_start_requires_actor_and_exact_tenant_scope_before_side_effects() -> None:
    engine = _engine()
    runtime = MemoryOidcRuntimeClient()
    client = _client(engine, runtime)
    anonymous = client.post(
        "/api/enterprise/sso/oidc/start",
        json={"tenant_id": TENANT_A, "provider_id": PROVIDER_ID, "redirect_uri": REDIRECT_URI},
    )
    cross_tenant = client.post(
        "/api/enterprise/sso/oidc/start",
        headers=actor_headers("owner-a", tenant_id=TENANT_A, key="oidc-cross-tenant"),
        json={
            "tenant_id": "tenant-identity-b",
            "provider_id": PROVIDER_ID,
            "redirect_uri": REDIRECT_URI,
        },
    )
    assert anonymous.status_code == 401
    assert cross_tenant.status_code == 403
    assert cross_tenant.json()["detail"]["code"] == "oidc_tenant_scope_mismatch"
    assert runtime.authorization_calls == []
    assert _rows(engine, "tenant_oidc_login_transactions") == []


def test_loopback_http_start_cookies_are_non_secure_but_remote_http_remains_secure() -> None:
    engine = _engine()
    runtime = MemoryOidcRuntimeClient()
    loopback = _start(_client(engine, runtime, base_url="http://127.0.0.1:1420"))
    remote = _start(_client(engine, runtime, base_url="http://app.example.test"))
    assert loopback.status_code == 201, loopback.text
    assert "Secure" not in loopback.headers["set-cookie"]
    assert remote.status_code == 201, remote.text
    assert "Secure" in remote.headers["set-cookie"]


def test_expired_oidc_state_returns_410() -> None:
    engine = _engine()
    client = _client(engine, MemoryOidcRuntimeClient())
    state = _start(client).json()["state"]
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE tenant_oidc_login_transactions "
                "SET expires_at=:expired,error_code='oidc_exchange_in_progress'"
            ),
            {"expired": NOW - timedelta(seconds=1)},
        )
    response = client.post(
        "/api/enterprise/sso/oidc/callback",
        json={"state": state, "code": "expired-code"},
    )
    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "oidc_state_expired"


def test_multi_audience_requires_matching_authorized_party() -> None:
    for authorized_party in (None, "different-client"):
        engine = _engine()
        runtime = MemoryOidcRuntimeClient()
        runtime.identity["audience"] = ["rag4c-client", "secondary-audience"]
        if authorized_party is not None:
            runtime.identity["authorized_party"] = authorized_party
        client = _client(engine, runtime)
        state = _start(client).json()["state"]
        response = client.post(
            "/api/enterprise/sso/oidc/callback",
            json={"state": state, "code": "multi-audience-code"},
        )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "oidc_authorized_party_mismatch"

    engine = _engine()
    runtime = MemoryOidcRuntimeClient()
    runtime.identity.update(
        audience=["rag4c-client", "secondary-audience"],
        authorized_party="rag4c-client",
    )
    client = _client(engine, runtime)
    state = _start(client).json()["state"]
    accepted = client.post(
        "/api/enterprise/sso/oidc/callback",
        json={"state": state, "code": "multi-audience-code"},
    )
    assert accepted.status_code == 200, accepted.text


class _MemoryEndpointResolver:
    def __init__(self, addresses: dict[str, tuple[str, ...]]) -> None:
        self.addresses = addresses
        self.calls: list[str] = []

    def resolve_host_addresses(self, host: str) -> tuple[str, ...]:
        self.calls.append(host)
        return self.addresses.get(host, ())


class _StreamingResponse:
    def __init__(self, chunks: list[bytes], *, content_length: str | None = None) -> None:
        self._chunks = chunks
        self.headers = {} if content_length is None else {"content-length": content_length}
        self.iterated = 0

    def __enter__(self) -> "_StreamingResponse":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_bytes(self):
        for chunk in self._chunks:
            self.iterated += 1
            yield chunk


class _StreamingClient:
    def __init__(
        self, response: _StreamingResponse, captured: dict[str, Any], **kwargs: Any
    ) -> None:
        self._response = response
        captured.update(kwargs)

    def __enter__(self) -> "_StreamingClient":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def stream(self, method: str, url: str, **kwargs: Any) -> _StreamingResponse:
        return self._response


def test_production_oidc_adapter_rejects_non_global_endpoints_before_network() -> None:
    module = importlib.import_module("core.enterprise_oidc_runtime")
    resolver = _MemoryEndpointResolver(
        {
            "id.example.test": ("93.184.216.34", "127.0.0.1"),
        }
    )
    network_called = False

    def factory(**_kwargs: Any):
        nonlocal network_called
        network_called = True
        raise AssertionError("unsafe endpoint must be rejected before creating HTTP client")

    runtime = module.AuthlibOidcRuntimeClient(
        secret_resolver=lambda _ref: "secret",
        resolver=resolver,
        http_client_factory=factory,
    )
    with pytest.raises(module.OidcRuntimeError) as captured:
        runtime._json_get("https://id.example.test/.well-known/openid-configuration")
    assert captured.value.code == "oidc_runtime_endpoint_unsafe"
    assert network_called is False
    assert resolver.calls == ["id.example.test"]


def test_production_oidc_adapter_streams_with_redirects_disabled_and_hard_size_cap() -> None:
    module = importlib.import_module("core.enterprise_oidc_runtime")
    resolver = _MemoryEndpointResolver({"id.example.test": ("93.184.216.34",)})
    captured: dict[str, Any] = {}
    oversized = _StreamingResponse([b"x" * 9_000, b"y" * 9_000, b"ignored"])

    def factory(**kwargs: Any) -> _StreamingClient:
        return _StreamingClient(oversized, captured, **kwargs)

    runtime = module.AuthlibOidcRuntimeClient(
        secret_resolver=lambda _ref: "secret",
        resolver=resolver,
        http_client_factory=factory,
        max_response_bytes=16_384,
    )
    with pytest.raises(module.OidcRuntimeError) as error:
        runtime._json_get("https://id.example.test/metadata")
    assert error.value.code == "oidc_runtime_response_too_large"
    assert captured["follow_redirects"] is False
    assert oversized.iterated == 2


def test_production_oidc_adapter_rejects_oversized_content_length_before_reading() -> None:
    module = importlib.import_module("core.enterprise_oidc_runtime")
    resolver = _MemoryEndpointResolver({"id.example.test": ("93.184.216.34",)})
    captured: dict[str, Any] = {}
    oversized = _StreamingResponse([b"must-not-read"], content_length="20000")

    def factory(**kwargs: Any) -> _StreamingClient:
        return _StreamingClient(oversized, captured, **kwargs)

    runtime = module.AuthlibOidcRuntimeClient(
        secret_resolver=lambda _ref: "secret",
        resolver=resolver,
        http_client_factory=factory,
        max_response_bytes=16_384,
    )
    with pytest.raises(module.OidcRuntimeError) as error:
        runtime._json_get("https://id.example.test/metadata")
    assert error.value.code == "oidc_runtime_response_too_large"
    assert oversized.iterated == 0


class _TransactionProbeOidcRuntimeClient(MemoryOidcRuntimeClient):
    def __init__(self, engine: Any) -> None:
        super().__init__()
        self.engine = engine
        self.exchange_observed_transaction: bool | None = None

    def exchange_and_validate(self, **kwargs: Any) -> dict[str, Any]:
        with self.engine.connect() as connection:
            raw_connection = connection.connection.driver_connection
            self.exchange_observed_transaction = bool(raw_connection.in_transaction)
        return super().exchange_and_validate(**kwargs)


def test_callback_exchanges_code_outside_database_transaction() -> None:
    engine = _engine()
    runtime = _TransactionProbeOidcRuntimeClient(engine)
    client = _client(engine, runtime)
    state = _start(client).json()["state"]
    response = client.post(
        "/api/enterprise/sso/oidc/callback",
        json={"state": state, "code": "transaction-probe-code"},
    )
    assert response.status_code == 200, response.text
    assert runtime.exchange_observed_transaction is False


class _BlockingOidcRuntimeClient(MemoryOidcRuntimeClient):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self._lock = threading.Lock()

    def exchange_and_validate(self, **kwargs: Any) -> dict[str, Any]:
        with self._lock:
            self.calls += 1
        self.started.set()
        if not self.release.wait(timeout=5):
            raise RuntimeError("test exchange release timed out")
        return super().exchange_and_validate(**kwargs)


def test_concurrent_callback_claims_once_and_does_not_duplicate_exchange(tmp_path: Any) -> None:
    engine = _file_engine(tmp_path)
    runtime = _BlockingOidcRuntimeClient()
    first_client = _client(engine, runtime)
    second_client = _client(engine, runtime)
    state = _start(first_client).json()["state"]
    nonce = first_client.cookies.get("rag4c_oidc_nonce")
    cookie_header = f"rag4c_oidc_state={state}; rag4c_oidc_nonce={nonce}"

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(
            first_client.post,
            "/api/enterprise/sso/oidc/callback",
            json={"state": state, "code": "first-code"},
            headers={"Cookie": cookie_header},
        )
        assert runtime.started.wait(timeout=2)
        second_future = executor.submit(
            second_client.post,
            "/api/enterprise/sso/oidc/callback",
            json={"state": state, "code": "second-code"},
            headers={"Cookie": cookie_header},
        )
        assert runtime.calls == 1
        runtime.release.set()
        first = first_future.result(timeout=5)
        second = second_future.result(timeout=5)

    assert first.status_code == 200, first.text
    assert second.status_code in {409, 503}, second.text
    assert second.json()["detail"]["code"] in {
        "oidc_state_unavailable",
        "oidc_state_consumed",
        "oidc_runtime_unavailable",
    }
    assert runtime.calls == 1
    assert len(_rows(engine, "tenant_sso_sessions")) == 1
    assert len(_rows(engine, "tenant_oidc_subject_links")) == 1


class _StartTransactionProbeOidcRuntimeClient(MemoryOidcRuntimeClient):
    def __init__(self, engine: Any) -> None:
        super().__init__()
        self.engine = engine
        self.metadata_observed_transaction: bool | None = None

    def build_authorization_url(self, **kwargs: Any) -> str:
        with self.engine.connect() as connection:
            raw_connection = connection.connection.driver_connection
            self.metadata_observed_transaction = bool(raw_connection.in_transaction)
        return super().build_authorization_url(**kwargs)


def test_start_builds_authorization_url_outside_database_transaction() -> None:
    engine = _engine()
    runtime = _StartTransactionProbeOidcRuntimeClient(engine)
    response = _start(_client(engine, runtime))
    assert response.status_code == 201, response.text
    assert runtime.metadata_observed_transaction is False
