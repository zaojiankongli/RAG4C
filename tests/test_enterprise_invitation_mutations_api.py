from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from config.settings import KnowledgeSecuritySettings, RunHistorySettings, TenantSettings
from models.orm import Account, Base, Tenant, TenantAuditEvent, TenantMember
from server.enterprise_access_graph_api import build_enterprise_access_graph_router
from server.knowledge_auth import issue_knowledge_actor_token

REVISION = "0020_tenant_invitation_lifecycle"
TENANT_A = "tenant-invite-a"
TENANT_B = "tenant-invite-b"

SETTINGS = SimpleNamespace(
    run_history=RunHistorySettings(ops_bearer_token=SecretStr("ops-secret")),
    knowledge_security=KnowledgeSecuritySettings(
        actor_signing_secret=SecretStr("stage8-invitation-secret"),
        actor_max_ttl_s=900,
    ),
    tenant=TenantSettings(enforced=True, default_tenant=TENANT_A),
)


def _enable_foreign_keys(engine: Any) -> None:
    @event.listens_for(engine, "connect")
    def _configure(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


def _install_0020(engine: Any, *, revision: str = REVISION) -> None:
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(64) NOT NULL)")
        )
        connection.execute(text("DELETE FROM alembic_version"))
        connection.execute(
            text("INSERT INTO alembic_version(version_num) VALUES (:revision)"),
            {"revision": revision},
        )


def _engine(*, revision: str = REVISION) -> Any:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _enable_foreign_keys(engine)
    _install_0020(engine, revision=revision)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id=TENANT_A, name="Invite Tenant A", status="active"),
                Tenant(id=TENANT_B, name="Invite Tenant B", status="active"),
                Account(id="owner-a", name="Owner A", email="owner-a@example.test"),
                Account(id="admin-a", name="Admin A", email="admin-a@example.test"),
                Account(id="editor-a", name="Editor A", email="editor-a@example.test"),
                Account(id="member-a", name="Member A", email="member-a@example.test"),
                Account(
                    id="invitee-a",
                    name="Invitee A",
                    email="invitee@example.test",
                ),
                Account(
                    id="wrong-email-a",
                    name="Wrong Email",
                    email="wrong@example.test",
                ),
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
                    tenant_id=TENANT_A,
                    account_id="member-a",
                    role="member",
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
    key: str = "stage8-idempotency-0001",
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
        "X-Request-ID": f"stage8-{actor_id}-{time.time_ns()}",
        "Idempotency-Key": key,
    }


def _client(engine: Any) -> TestClient:
    app = FastAPI()
    app.state.knowledge_auth_engine = engine
    app.state.knowledge_auth_settings = SETTINGS
    app.include_router(
        build_enterprise_access_graph_router(
            read_engine_provider=lambda: engine,
            mutation_engine_provider=lambda: engine,
        )
    )
    return TestClient(app)


def _rows(engine: Any, table: str) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        result = connection.execute(text(f"SELECT * FROM {table} ORDER BY id"))
        return [dict(row) for row in result.mappings()]


def _invitation(engine: Any, invitation_id: str) -> dict[str, Any]:
    with engine.connect() as connection:
        row = (
            connection.execute(
                text("SELECT * FROM tenant_invitations WHERE id=:id"),
                {"id": invitation_id},
            )
            .mappings()
            .one()
        )
        return dict(row)


def _create_invitation(
    client: TestClient,
    *,
    actor: str = "owner-a",
    email: str = "Invitee@Example.Test",
    role: str = "member",
    key: str = "stage8-create-0001",
) -> Any:
    return client.post(
        "/api/enterprise/invitations",
        headers=_headers(actor, key=key),
        json={
            "email": email,
            "role": role,
            "expires_in_days": 7,
            "reason": "加入知识运营团队",
        },
    )


def test_create_invitation_returns_one_time_token_and_persists_digest_only() -> None:
    engine = _engine()
    response = _create_invitation(_client(engine))

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["invitation"]["status"] == "pending"
    assert payload["invitation"]["revision"] == 1
    assert payload["invitation"]["send_count"] == 1
    assert payload["delivery"]["state"] == "manual_link_required"
    raw_token = payload["delivery"]["invite_token"]
    assert raw_token and "=" not in raw_token

    invitation = _invitation(engine, payload["invitation"]["id"])
    assert invitation["token_hash"] != raw_token
    assert len(invitation["token_hash"]) == 64
    serialized = json.dumps(
        {
            "ledger": _rows(engine, "tenant_control_mutation_requests"),
            "audit": _rows(engine, "tenant_audit_events"),
        },
        ensure_ascii=False,
        default=str,
    )
    assert raw_token not in serialized


def test_admin_cannot_invite_owner_and_editor_cannot_mutate() -> None:
    engine = _engine()
    client = _client(engine)
    admin = _create_invitation(
        client,
        actor="admin-a",
        role="owner",
        key="stage8-admin-owner-0001",
    )
    editor = _create_invitation(
        client,
        actor="editor-a",
        key="stage8-editor-create-0001",
    )

    assert admin.status_code == 403
    assert admin.json()["detail"]["code"] == "tenant_invitation_role_forbidden"
    assert editor.status_code == 403
    assert editor.json()["detail"]["code"] == "tenant_invitation_forbidden"


def test_duplicate_pending_email_and_existing_membership_are_conflicts() -> None:
    engine = _engine()
    client = _client(engine)
    first = _create_invitation(client, key="stage8-duplicate-first")
    duplicate = _create_invitation(client, key="stage8-duplicate-second")
    member = _create_invitation(
        client,
        email="member-a@example.test",
        key="stage8-existing-member",
    )

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "tenant_invitation_duplicate_pending"
    assert member.status_code == 409
    assert member.json()["detail"]["code"] == "tenant_invitation_membership_exists"


def test_create_idempotency_replays_without_reissuing_raw_token_and_hash_conflicts() -> None:
    engine = _engine()
    client = _client(engine)
    first = _create_invitation(client, key="stage8-create-replay")
    replay = _create_invitation(client, key="stage8-create-replay")
    conflict = _create_invitation(
        client,
        role="editor",
        key="stage8-create-replay",
    )

    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json()["invitation"] == first.json()["invitation"]
    assert replay.json()["delivery"]["state"] == "manual_link_already_issued"
    assert "invite_token" not in replay.json()["delivery"]
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "tenant_control_idempotency_conflict"
    assert len(_rows(engine, "tenant_invitations")) == 1
    assert len(_rows(engine, "tenant_control_mutation_requests")) == 1
    assert len(_rows(engine, "tenant_audit_events")) == 1


def test_resend_rotates_token_revision_and_send_count() -> None:
    engine = _engine()
    client = _client(engine)
    created = _create_invitation(client)
    invitation_id = created.json()["invitation"]["id"]
    old_token = created.json()["delivery"]["invite_token"]

    resent = client.post(
        f"/api/enterprise/invitations/{invitation_id}/resend",
        headers=_headers("admin-a", key="stage8-resend-0001"),
        json={"revision": 1, "expires_in_days": 5, "reason": "重新生成链接"},
    )
    assert resent.status_code == 200, resent.text
    assert resent.json()["invitation"]["revision"] == 2
    assert resent.json()["invitation"]["send_count"] == 2
    new_token = resent.json()["delivery"]["invite_token"]
    assert new_token != old_token

    old_accept = client.post(
        "/api/enterprise/invitations/accept",
        headers=_headers("invitee-a", key="stage8-old-token-accept"),
        json={"invite_token": old_token},
    )
    assert old_accept.status_code == 404
    assert old_accept.json()["detail"]["code"] == "tenant_invitation_token_invalid"


def test_revoke_is_revision_fenced_and_blocks_accept() -> None:
    engine = _engine()
    client = _client(engine)
    created = _create_invitation(client)
    invitation_id = created.json()["invitation"]["id"]
    token = created.json()["delivery"]["invite_token"]

    stale = client.post(
        f"/api/enterprise/invitations/{invitation_id}/revoke",
        headers=_headers("owner-a", key="stage8-revoke-stale"),
        json={"revision": 99, "reason": "过期页面"},
    )
    revoked = client.post(
        f"/api/enterprise/invitations/{invitation_id}/revoke",
        headers=_headers("owner-a", key="stage8-revoke-ok"),
        json={"revision": 1, "reason": "岗位取消"},
    )
    accepted = client.post(
        "/api/enterprise/invitations/accept",
        headers=_headers("invitee-a", key="stage8-revoked-accept"),
        json={"invite_token": token},
    )

    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "tenant_invitation_revision_conflict"
    assert revoked.status_code == 200
    assert revoked.json()["invitation"]["status"] == "revoked"
    assert accepted.status_code == 409
    assert accepted.json()["detail"]["code"] == "tenant_invitation_state_conflict"


def test_accept_identity_does_not_require_preexisting_membership_and_flushes_member_first() -> None:
    engine = _engine()
    client = _client(engine)
    created = _create_invitation(client)
    invitation_id = created.json()["invitation"]["id"]
    token = created.json()["delivery"]["invite_token"]

    accepted = client.post(
        "/api/enterprise/invitations/accept",
        headers=_headers("invitee-a", key="stage8-accept-0001"),
        json={"invite_token": token},
    )

    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["invitation"]["status"] == "accepted"
    with Session(engine) as session:
        membership = session.scalar(
            select(TenantMember).where(
                TenantMember.tenant_id == TENANT_A,
                TenantMember.account_id == "invitee-a",
            )
        )
        assert membership is not None
        assert membership.role == "member"
    invitation = _invitation(engine, invitation_id)
    assert invitation["accepted_by"] == "invitee-a"
    assert invitation["pending_email_key"] is None


def test_accept_rejects_email_mismatch_and_cross_tenant_token() -> None:
    engine = _engine()
    client = _client(engine)
    created = _create_invitation(client)
    token = created.json()["delivery"]["invite_token"]

    mismatch = client.post(
        "/api/enterprise/invitations/accept",
        headers=_headers("wrong-email-a", key="stage8-email-mismatch"),
        json={"invite_token": token},
    )
    cross_tenant = client.post(
        "/api/enterprise/invitations/accept",
        headers=_headers(
            "owner-b",
            tenant_id=TENANT_B,
            key="stage8-cross-tenant-accept",
        ),
        json={"invite_token": token},
    )

    assert mismatch.status_code == 403
    assert mismatch.json()["detail"]["code"] == "tenant_invitation_email_mismatch"
    assert cross_tenant.status_code == 404
    assert cross_tenant.json()["detail"]["code"] == "tenant_invitation_token_invalid"


def test_expired_invitation_transitions_to_expired_and_fails_closed() -> None:
    engine = _engine()
    client = _client(engine)
    created = _create_invitation(client)
    invitation_id = created.json()["invitation"]["id"]
    token = created.json()["delivery"]["invite_token"]
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE tenant_invitations SET expires_at=:past WHERE id=:invitation_id"),
            {
                "past": datetime.utcnow() - timedelta(minutes=1),
                "invitation_id": invitation_id,
            },
        )

    response = client.post(
        "/api/enterprise/invitations/accept",
        headers=_headers("invitee-a", key="stage8-expired-accept"),
        json={"invite_token": token},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "tenant_invitation_expired"
    assert _invitation(engine, invitation_id)["status"] == "expired"


def test_expired_accept_same_key_replays_409_without_second_audit() -> None:
    engine = _engine()
    client = _client(engine)
    created = _create_invitation(client)
    invitation_id = created.json()["invitation"]["id"]
    token = created.json()["delivery"]["invite_token"]
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE tenant_invitations SET expires_at=:past WHERE id=:id"),
            {"past": datetime.utcnow() - timedelta(minutes=1), "id": invitation_id},
        )

    headers = _headers("invitee-a", key="stage8-expired-replay")
    first = client.post(
        "/api/enterprise/invitations/accept",
        headers=headers,
        json={"invite_token": token},
    )
    audit_count = len(_rows(engine, "tenant_audit_events"))
    ledger_count = len(_rows(engine, "tenant_control_mutation_requests"))
    second = client.post(
        "/api/enterprise/invitations/accept",
        headers=headers,
        json={"invite_token": token},
    )

    assert first.status_code == 409
    assert second.status_code == 409
    assert first.json()["detail"] == second.json()["detail"]
    assert first.json()["detail"]["code"] == "tenant_invitation_expired"
    assert len(_rows(engine, "tenant_audit_events")) == audit_count
    assert len(_rows(engine, "tenant_control_mutation_requests")) == ledger_count
    accept_rows = [
        row
        for row in _rows(engine, "tenant_control_mutation_requests")
        if row["operation"] == "tenant_invitation.accept"
    ]
    assert len(accept_rows) == 1
    assert accept_rows[0]["status"] == "completed"
    assert accept_rows[0]["http_status"] == 409


def test_accept_audit_failure_rolls_back_membership_and_invitation() -> None:
    engine = _engine()
    client = _client(engine)
    created = _create_invitation(client)
    invitation_id = created.json()["invitation"]["id"]
    token = created.json()["delivery"]["invite_token"]

    def reject_audit(_mapper: Any, _connection: Any, _target: TenantAuditEvent) -> None:
        raise RuntimeError("audit unavailable")

    event.listen(TenantAuditEvent, "before_insert", reject_audit)
    try:
        response = client.post(
            "/api/enterprise/invitations/accept",
            headers=_headers("invitee-a", key="stage8-accept-rollback"),
            json={"invite_token": token},
        )
    finally:
        event.remove(TenantAuditEvent, "before_insert", reject_audit)

    assert response.status_code == 503
    with Session(engine) as session:
        assert (
            session.scalar(
                select(TenantMember).where(
                    TenantMember.tenant_id == TENANT_A,
                    TenantMember.account_id == "invitee-a",
                )
            )
            is None
        )
    assert _invitation(engine, invitation_id)["status"] == "pending"


def test_create_audit_failure_rolls_back_invitation_and_ledger() -> None:
    engine = _engine()
    client = _client(engine)

    def reject_audit(_mapper: Any, _connection: Any, _target: TenantAuditEvent) -> None:
        raise RuntimeError("audit unavailable")

    event.listen(TenantAuditEvent, "before_insert", reject_audit)
    try:
        response = _create_invitation(client, key="stage8-create-rollback")
    finally:
        event.remove(TenantAuditEvent, "before_insert", reject_audit)

    assert response.status_code == 503
    assert _rows(engine, "tenant_invitations") == []
    assert _rows(engine, "tenant_control_mutation_requests") == []


def test_missing_0020_stamp_is_migration_required() -> None:
    engine = _engine(revision="0019_dataset_acl_control")
    response = _create_invitation(_client(engine), key="stage8-missing-0020")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "tenant_invitation_migration_required"
    assert "0020_tenant_invitation_lifecycle" in response.json()["detail"]["message"]


def test_accept_identity_rejects_missing_account_without_requiring_membership() -> None:
    engine = _engine()
    client = _client(engine)
    created = _create_invitation(client)
    token = created.json()["delivery"]["invite_token"]

    response = client.post(
        "/api/enterprise/invitations/accept",
        headers=_headers("missing-account", key="stage8-missing-account"),
        json={"invite_token": token},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "invitation_accept_account_required"
