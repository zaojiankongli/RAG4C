from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from config.settings import (
    KnowledgeSecuritySettings,
    RunHistorySettings,
    TenantSettings,
)
from core.knowledge_permissions import (
    KNOWLEDGE_AUDIT,
    KNOWLEDGE_DELETE,
    KNOWLEDGE_MANAGE,
    KNOWLEDGE_READ,
    KNOWLEDGE_WRITE,
    permissions_for_role,
    role_allows,
)
from models.orm import Account, Base, Dataset, Tenant, TenantMember
from server.knowledge_auth import (
    KnowledgeActor,
    issue_knowledge_actor_token,
    require_knowledge_permission,
    resolve_path_dataset,
)

PERMISSIONS = (
    KNOWLEDGE_READ,
    KNOWLEDGE_WRITE,
    KNOWLEDGE_DELETE,
    KNOWLEDGE_MANAGE,
    KNOWLEDGE_AUDIT,
)
TOKEN_DOMAIN = b"rag4c:knowledge-actor:v1\x00"
_DEFAULT_TOKEN = object()


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
                Tenant(id="tenant-s", name="Tenant Suspended", status="suspended"),
                Dataset(
                    id="dataset-a",
                    tenant_id="tenant-a",
                    name="Dataset A",
                    status="active",
                ),
                Dataset(
                    id="dataset-archived",
                    tenant_id="tenant-a",
                    name="Dataset Archived",
                    status="archived",
                ),
                Dataset(
                    id="dataset-disabled",
                    tenant_id="tenant-a",
                    name="Dataset Disabled",
                    status="disabled",
                ),
                Dataset(
                    id="dataset-b",
                    tenant_id="tenant-b",
                    name="Dataset B",
                    status="active",
                ),
                Dataset(
                    id="dataset-s",
                    tenant_id="tenant-s",
                    name="Dataset Suspended",
                    status="active",
                ),
                Account(id="owner-a", name="Owner", email="owner@example.test"),
                Account(id="admin-a", name="Admin", email="admin@example.test"),
                Account(id="editor-a", name="Editor", email="editor@example.test"),
                Account(id="member-a", name="Member", email="member@example.test"),
                Account(id="member-b", name="Other", email="other@example.test"),
                Account(id="owner-s", name="Suspended", email="suspended@example.test"),
                Account(id="orphan", name="Orphan", email="orphan@example.test"),
                TenantMember(account_id="owner-a", tenant_id="tenant-a", role="owner"),
                TenantMember(account_id="admin-a", tenant_id="tenant-a", role="admin"),
                TenantMember(account_id="editor-a", tenant_id="tenant-a", role="editor"),
                TenantMember(account_id="member-a", tenant_id="tenant-a", role="member"),
                TenantMember(account_id="member-b", tenant_id="tenant-b", role="member"),
                TenantMember(account_id="owner-s", tenant_id="tenant-s", role="owner"),
            ]
        )
        session.commit()
    return engine


def _settings(
    operator_token: str | None = "ops-secret",
    *,
    actor_secret: str | None = "dedicated-actor-secret",
    max_ttl: int = 900,
) -> SimpleNamespace:
    return SimpleNamespace(
        run_history=RunHistorySettings(
            ops_bearer_token=(SecretStr(operator_token) if operator_token is not None else None)
        ),
        knowledge_security=KnowledgeSecuritySettings(
            actor_signing_secret=(SecretStr(actor_secret) if actor_secret is not None else None),
            actor_max_ttl_s=max_ttl,
        ),
        tenant=TenantSettings(enforced=True, default_tenant="tenant-a"),
    )


def _issue(
    account_id: str = "owner-a",
    tenant_id: str = "tenant-a",
    *,
    ttl: int = 300,
    now: int | None = None,
    settings: Any | None = None,
) -> str:
    return issue_knowledge_actor_token(
        account_id,
        tenant_id,
        ttl,
        int(time.time()) if now is None else now,
        settings=settings or _settings(),
    )


def _app(
    permission: str,
    *,
    dataset_resolver=resolve_path_dataset("dataset_id"),
    allow_loopback_local_actor: str | None = None,
    settings: Any | None = None,
    engine: Any | None = None,
) -> FastAPI:
    app = FastAPI()
    app.state.knowledge_auth_engine = engine or _engine()
    app.state.knowledge_auth_settings = settings or _settings()
    dependency = require_knowledge_permission(
        permission,
        dataset_resolver,
        allow_loopback_local_actor=allow_loopback_local_actor,
    )

    @app.get("/datasets/{dataset_id}")
    def guarded_dataset(actor: KnowledgeActor = Depends(dependency)) -> dict[str, object]:
        audit = actor.to_audit_context()
        return {
            "account_id": actor.account_id,
            "tenant_id": actor.tenant_id,
            "role": actor.role,
            "request_id": actor.request_id,
            "request_ip": actor.request_ip,
            "audit": {
                "actor_id": audit.actor_id,
                "request_id": audit.request_id,
                "request_ip": audit.request_ip,
            },
        }

    return app


def _headers(
    *,
    account_id: str = "owner-a",
    tenant: str | None = "tenant-a",
    token: str | None | object = _DEFAULT_TOKEN,
    actor_assertion: str | None = None,
    request_id: str | None = None,
) -> dict[str, str]:
    resolved_token = _issue(account_id, tenant or "tenant-a") if token is _DEFAULT_TOKEN else token
    headers: dict[str, str] = {}
    if resolved_token is not None:
        headers["Authorization"] = f"Bearer {resolved_token}"
    if tenant is not None:
        headers["X-RAG4C-Tenant"] = tenant
    if actor_assertion is not None:
        headers["X-RAG4C-Actor"] = actor_assertion
    if request_id is not None:
        headers["X-Request-ID"] = request_id
    return headers


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_payload(token: str) -> dict[str, Any]:
    payload_part = token.split(".", 1)[0]
    raw = base64.urlsafe_b64decode(payload_part + "=" * (-len(payload_part) % 4))
    return json.loads(raw)


def _sign_payload_bytes(
    payload_bytes: bytes,
    *,
    secret: str = "dedicated-actor-secret",
    domain: bytes = TOKEN_DOMAIN,
) -> str:
    payload_part = _b64(payload_bytes)
    signature = hmac.new(
        secret.encode("utf-8"), domain + payload_part.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{payload_part}.{_b64(signature)}"


def _forge_token(payload: dict[str, Any], *, secret: str = "dedicated-actor-secret") -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sign_payload_bytes(canonical, secret=secret)


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        ("owner", set(PERMISSIONS)),
        ("admin", set(PERMISSIONS)),
        ("editor", {KNOWLEDGE_READ, KNOWLEDGE_WRITE, KNOWLEDGE_DELETE}),
        ("member", {KNOWLEDGE_READ}),
        ("unknown", set()),
    ],
)
def test_role_matrix_is_fail_closed(role: str, allowed: set[str]) -> None:
    assert permissions_for_role(role) == frozenset(allowed)
    assert {permission for permission in PERMISSIONS if role_allows(role, permission)} == allowed


def test_issuer_creates_canonical_actor_bound_token() -> None:
    token = _issue("editor-a", "tenant-a", ttl=60, now=1_700_000_000)
    payload_part, signature_part = token.split(".")

    assert "=" not in token
    assert (
        _b64(base64.urlsafe_b64decode(payload_part + "=" * (-len(payload_part) % 4)))
        == payload_part
    )
    assert (
        _b64(base64.urlsafe_b64decode(signature_part + "=" * (-len(signature_part) % 4)))
        == signature_part
    )
    payload = _decode_payload(token)
    assert set(payload) == {"sub", "tenant", "iat", "exp", "jti"}
    assert payload["sub"] == "editor-a"
    assert payload["tenant"] == "tenant-a"
    assert payload["iat"] == 1_700_000_000
    assert payload["exp"] == 1_700_000_060
    assert isinstance(payload["jti"], str) and payload["jti"]


@pytest.mark.parametrize("token", [None, "ops-secret", "wrong-secret"])
def test_shared_operator_secret_is_not_a_knowledge_actor_token(token: str | None) -> None:
    client = TestClient(_app(KNOWLEDGE_READ), client=("10.0.0.2", 50000))
    response = client.get("/datasets/dataset-a", headers=_headers(token=token))

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["detail"]["code"] in {
        "knowledge_actor_token_required",
        "knowledge_actor_token_invalid",
    }


def test_remote_actor_identity_comes_from_token_without_actor_header() -> None:
    client = TestClient(_app(KNOWLEDGE_READ), client=("10.0.0.2", 50000))
    response = client.get(
        "/datasets/dataset-a",
        headers=_headers(account_id="member-a"),
    )

    assert response.status_code == 200
    assert response.json()["account_id"] == "member-a"
    assert response.json()["role"] == "member"


def test_remote_access_requires_explicit_tenant_header() -> None:
    client = TestClient(_app(KNOWLEDGE_READ), client=("10.0.0.2", 50000))
    response = client.get(
        "/datasets/dataset-a",
        headers=_headers(token=_issue("owner-a", "tenant-a"), tenant=None),
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "tenant_required"


def test_actor_header_is_only_a_consistency_assertion() -> None:
    client = TestClient(_app(KNOWLEDGE_READ), client=("10.0.0.2", 50000))
    response = client.get(
        "/datasets/dataset-a",
        headers=_headers(account_id="member-a", actor_assertion="owner-a"),
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "knowledge_actor_mismatch"


def test_member_token_cannot_impersonate_owner_or_gain_owner_permissions() -> None:
    client = TestClient(_app(KNOWLEDGE_MANAGE), client=("10.0.0.2", 50000))
    response = client.get(
        "/datasets/dataset-a",
        headers=_headers(account_id="member-a"),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "knowledge_permission_forbidden"


@pytest.mark.parametrize(
    ("actor", "permission", "status"),
    [
        ("owner-a", KNOWLEDGE_AUDIT, 200),
        ("admin-a", KNOWLEDGE_MANAGE, 200),
        ("editor-a", KNOWLEDGE_DELETE, 200),
        ("editor-a", KNOWLEDGE_MANAGE, 403),
        ("member-a", KNOWLEDGE_READ, 200),
        ("member-a", KNOWLEDGE_WRITE, 403),
        ("orphan", KNOWLEDGE_READ, 403),
    ],
)
def test_database_membership_and_role_are_authoritative(
    actor: str, permission: str, status: int
) -> None:
    client = TestClient(_app(permission), client=("10.0.0.2", 50000))
    response = client.get(
        "/datasets/dataset-a",
        headers=_headers(account_id=actor),
    )

    assert response.status_code == status
    if status == 403:
        assert response.json()["detail"]["code"] in {
            "knowledge_membership_required",
            "knowledge_permission_forbidden",
        }


def test_cross_tenant_token_is_rejected_before_membership_lookup() -> None:
    client = TestClient(_app(KNOWLEDGE_READ), client=("10.0.0.2", 50000))
    response = client.get(
        "/datasets/dataset-a",
        headers=_headers(token=_issue("member-b", "tenant-b"), tenant="tenant-a"),
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "knowledge_tenant_mismatch"


def test_dataset_resolver_enforces_tenant_scope_without_disclosing_dataset() -> None:
    client = TestClient(_app(KNOWLEDGE_READ), client=("10.0.0.2", 50000))
    response = client.get(
        "/datasets/dataset-b",
        headers=_headers(account_id="owner-a", tenant="tenant-a"),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "knowledge_dataset_scope_forbidden"


@pytest.mark.parametrize("permission", [KNOWLEDGE_READ, KNOWLEDGE_AUDIT])
def test_read_and_audit_allow_archived_dataset(permission: str) -> None:
    client = TestClient(_app(permission), client=("10.0.0.2", 50000))
    response = client.get("/datasets/dataset-archived", headers=_headers())

    assert response.status_code == 200


@pytest.mark.parametrize("permission", [KNOWLEDGE_WRITE, KNOWLEDGE_DELETE, KNOWLEDGE_MANAGE])
def test_dataset_mutations_require_active_dataset(permission: str) -> None:
    client = TestClient(_app(permission), client=("10.0.0.2", 50000))
    response = client.get("/datasets/dataset-archived", headers=_headers())

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "knowledge_dataset_inactive"


@pytest.mark.parametrize("permission", PERMISSIONS)
def test_disabled_dataset_is_fail_closed_for_every_permission(permission: str) -> None:
    client = TestClient(_app(permission), client=("10.0.0.2", 50000))
    response = client.get("/datasets/dataset-disabled", headers=_headers())

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "knowledge_dataset_inactive"


def test_suspended_tenant_is_forbidden_even_for_owner() -> None:
    client = TestClient(_app(KNOWLEDGE_READ), client=("10.0.0.2", 50000))
    response = client.get(
        "/datasets/dataset-s",
        headers=_headers(
            token=_issue("owner-s", "tenant-s"),
            tenant="tenant-s",
        ),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "knowledge_tenant_inactive"



def test_suspended_tenant_member_is_forbidden_before_role_or_acl_bypass() -> None:
    engine = _engine()
    with Session(engine) as session:
        membership = session.query(TenantMember).filter_by(
            account_id="owner-a",
            tenant_id="tenant-a",
        ).one()
        membership.status = "suspended"
        session.commit()

    client = TestClient(
        _app(KNOWLEDGE_READ, engine=engine),
        client=("10.0.0.2", 50000),
    )
    response = client.get("/datasets/dataset-a", headers=_headers())

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "knowledge_membership_inactive"

def test_request_id_and_audit_actor_come_from_verified_token() -> None:
    client = TestClient(_app(KNOWLEDGE_WRITE), client=("10.0.0.2", 50000))
    response = client.get(
        "/datasets/dataset-a",
        headers=_headers(
            account_id="editor-a",
            actor_assertion="editor-a",
            request_id="request-123",
        ),
    )

    assert response.status_code == 200
    assert response.json() == {
        "account_id": "editor-a",
        "tenant_id": "tenant-a",
        "role": "editor",
        "request_id": "request-123",
        "request_ip": "10.0.0.2",
        "audit": {
            "actor_id": "editor-a",
            "request_id": "request-123",
            "request_ip": "10.0.0.2",
        },
    }


def test_request_id_is_generated_when_missing() -> None:
    client = TestClient(_app(KNOWLEDGE_READ), client=("10.0.0.2", 50000))
    response = client.get("/datasets/dataset-a", headers=_headers())

    assert response.status_code == 200
    request_id = response.json()["request_id"]
    assert request_id.startswith("req-")
    assert len(request_id) == 36


@pytest.mark.parametrize(
    "token",
    [
        pytest.param("expired", id="expired"),
        pytest.param("tampered", id="tampered"),
        pytest.param("noncanonical", id="noncanonical"),
        pytest.param("extra-field", id="extra-field"),
        pytest.param("future-iat", id="future-iat"),
    ],
)
def test_invalid_actor_tokens_are_rejected(token: str) -> None:
    now = int(time.time())
    if token == "expired":
        candidate = _issue(now=now - 600, ttl=60)
    elif token == "tampered":
        valid = _issue(now=now)
        payload_part, signature_part = valid.split(".")
        replacement = "A" if payload_part[-1] != "A" else "B"
        candidate = f"{payload_part[:-1]}{replacement}.{signature_part}"
    elif token == "noncanonical":
        candidate = _issue(now=now) + "="
    elif token == "extra-field":
        candidate = _forge_token(
            {
                "sub": "owner-a",
                "tenant": "tenant-a",
                "iat": now,
                "exp": now + 60,
                "jti": "jti-extra",
                "role": "owner",
            }
        )
    else:
        candidate = _forge_token(
            {
                "sub": "owner-a",
                "tenant": "tenant-a",
                "iat": now + 300,
                "exp": now + 600,
                "jti": "jti-future",
            }
        )

    client = TestClient(_app(KNOWLEDGE_READ), client=("10.0.0.2", 50000))
    response = client.get("/datasets/dataset-a", headers=_headers(token=candidate))

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "knowledge_actor_token_invalid"


@pytest.mark.parametrize(
    "permission", [KNOWLEDGE_WRITE, KNOWLEDGE_DELETE, KNOWLEDGE_MANAGE, KNOWLEDGE_AUDIT]
)
def test_loopback_mutation_and_audit_require_actor_token(permission: str) -> None:
    client = TestClient(_app(permission), client=("127.0.0.1", 50000))
    response = client.get(
        "/datasets/dataset-a",
        headers={"X-RAG4C-Actor": "owner-a"},
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "knowledge_actor_token_required"


def test_loopback_mutation_accepts_valid_actor_token_without_tenant_header() -> None:
    client = TestClient(_app(KNOWLEDGE_DELETE), client=("127.0.0.1", 50000))
    response = client.get(
        "/datasets/dataset-a",
        headers={"Authorization": f"Bearer {_issue()}"},
    )

    assert response.status_code == 200
    assert response.json()["account_id"] == "owner-a"
    assert response.json()["tenant_id"] == "tenant-a"


def test_loopback_read_can_use_explicitly_configured_local_actor() -> None:
    client = TestClient(
        _app(KNOWLEDGE_READ, allow_loopback_local_actor="member-a"),
        client=("127.0.0.1", 50000),
    )
    response = client.get("/datasets/dataset-a")

    assert response.status_code == 200
    assert response.json()["account_id"] == "member-a"
    assert response.json()["tenant_id"] == "tenant-a"
    assert response.json()["audit"]["actor_id"] == "member-a"


def test_loopback_read_requires_explicit_local_actor_opt_in() -> None:
    client = TestClient(_app(KNOWLEDGE_READ), client=("127.0.0.1", 50000))
    response = client.get("/datasets/dataset-a")

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "knowledge_actor_token_required"


def test_same_machine_proxy_cannot_use_loopback_local_actor_bypass() -> None:
    client = TestClient(
        _app(KNOWLEDGE_READ, allow_loopback_local_actor="member-a"),
        client=("127.0.0.1", 50000),
    )
    response = client.get(
        "/datasets/dataset-a",
        headers={"X-Forwarded-For": "198.51.100.25"},
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "knowledge_actor_token_required"


def test_actor_ip_uses_direct_peer_and_ignores_forwarding_headers() -> None:
    client = TestClient(_app(KNOWLEDGE_READ), client=("10.0.0.2", 50000))
    headers = _headers()
    headers["X-Forwarded-For"] = "127.0.0.1"
    response = client.get("/datasets/dataset-a", headers=headers)

    assert response.status_code == 200
    assert response.json()["request_ip"] == "10.0.0.2"
    assert response.json()["audit"]["request_ip"] == "10.0.0.2"


def test_configured_dataset_resolver_cannot_silently_skip_scope_validation() -> None:
    client = TestClient(
        _app(KNOWLEDGE_READ, dataset_resolver=lambda _request: None),
        client=("10.0.0.2", 50000),
    )
    response = client.get("/datasets/dataset-a", headers=_headers())

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "knowledge_dataset_required"


def test_permission_factory_rejects_unknown_permission_at_startup() -> None:
    with pytest.raises(ValueError, match="unknown KnowledgeOps permission"):
        require_knowledge_permission("knowledge.superuser")


def test_issuer_rejects_invalid_ttl_and_missing_dedicated_secret() -> None:
    with pytest.raises(ValueError, match="ttl"):
        _issue(ttl=0)
    with pytest.raises(ValueError, match="maximum"):
        _issue(ttl=901)
    with pytest.raises(RuntimeError, match="issuer secret"):
        _issue(settings=_settings(actor_secret=None))


def test_actor_token_signature_is_domain_separated() -> None:
    now = int(time.time())
    payload = json.dumps(
        {
            "sub": "owner-a",
            "tenant": "tenant-a",
            "iat": now,
            "exp": now + 60,
            "jti": "jti-no-domain",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    token = _sign_payload_bytes(payload, domain=b"")
    client = TestClient(_app(KNOWLEDGE_READ), client=("10.0.0.2", 50000))
    response = client.get("/datasets/dataset-a", headers=_headers(token=token))

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "knowledge_actor_token_invalid"


def test_actor_token_payload_must_be_canonical_json() -> None:
    now = int(time.time())
    noncanonical = json.dumps(
        {
            "sub": "owner-a",
            "tenant": "tenant-a",
            "iat": now,
            "exp": now + 60,
            "jti": "jti-spaced-json",
        },
        sort_keys=False,
        indent=2,
    ).encode("utf-8")
    token = _sign_payload_bytes(noncanonical)
    client = TestClient(_app(KNOWLEDGE_READ), client=("10.0.0.2", 50000))
    response = client.get("/datasets/dataset-a", headers=_headers(token=token))

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "knowledge_actor_token_invalid"


def test_shared_operator_secret_plus_actor_header_cannot_impersonate_owner() -> None:
    client = TestClient(_app(KNOWLEDGE_MANAGE), client=("10.0.0.2", 50000))
    response = client.get(
        "/datasets/dataset-a",
        headers=_headers(token="ops-secret", actor_assertion="owner-a"),
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "knowledge_actor_token_invalid"


def test_valid_format_token_signed_with_operator_secret_is_rejected() -> None:
    now = int(time.time())
    attacker_token = _forge_token(
        {
            "sub": "owner-a",
            "tenant": "tenant-a",
            "iat": now,
            "exp": now + 60,
            "jti": "attacker-knows-ops-secret",
        },
        secret="ops-secret",
    )
    client = TestClient(_app(KNOWLEDGE_MANAGE), client=("10.0.0.2", 50000))
    response = client.get(
        "/datasets/dataset-a",
        headers=_headers(token=attacker_token, actor_assertion="owner-a"),
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "knowledge_actor_token_invalid"


def test_dedicated_actor_secret_signs_tokens_independently_of_operator_secret() -> None:
    settings = _settings(operator_token="operator-only", actor_secret="actor-only")
    token = _issue(settings=settings)
    client = TestClient(
        _app(KNOWLEDGE_READ, settings=settings),
        client=("10.0.0.2", 50000),
    )
    response = client.get(
        "/datasets/dataset-a",
        headers={
            "Authorization": f"Bearer {token}",
            "X-RAG4C-Tenant": "tenant-a",
        },
    )

    assert response.status_code == 200
    assert response.json()["account_id"] == "owner-a"


def test_actor_token_ttl_boundary_is_enforced_by_issuer_and_parser() -> None:
    settings = _settings(max_ttl=60)
    token = _issue(ttl=60, settings=settings)
    client = TestClient(
        _app(KNOWLEDGE_READ, settings=settings),
        client=("10.0.0.2", 50000),
    )
    response = client.get(
        "/datasets/dataset-a",
        headers={
            "Authorization": f"Bearer {token}",
            "X-RAG4C-Tenant": "tenant-a",
        },
    )

    assert response.status_code == 200
    with pytest.raises(ValueError, match="maximum"):
        _issue(ttl=61, settings=settings)


def test_parser_rejects_validly_signed_token_above_configured_max_ttl() -> None:
    now = int(time.time())
    token = _forge_token(
        {
            "sub": "owner-a",
            "tenant": "tenant-a",
            "iat": now,
            "exp": now + 901,
            "jti": "self-signed-long-ttl",
        }
    )
    client = TestClient(_app(KNOWLEDGE_READ), client=("10.0.0.2", 50000))
    response = client.get("/datasets/dataset-a", headers=_headers(token=token))

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "knowledge_actor_token_invalid"


def test_missing_dedicated_secret_fails_closed_during_verification() -> None:
    settings = _settings(actor_secret=None)
    token = _forge_token(
        {
            "sub": "owner-a",
            "tenant": "tenant-a",
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
            "jti": "missing-server-secret",
        }
    )
    client = TestClient(
        _app(KNOWLEDGE_READ, settings=settings),
        client=("10.0.0.2", 50000),
    )
    response = client.get(
        "/datasets/dataset-a",
        headers={
            "Authorization": f"Bearer {token}",
            "X-RAG4C-Tenant": "tenant-a",
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "knowledge_actor_token_invalid"
