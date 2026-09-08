from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import importlib
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from models.orm import Account, Base, Tenant, TenantMember

REVISION = "0022_scim_provisioning_data_plane"
TENANT_A = "tenant-scim-a"
TENANT_B = "tenant-scim-b"
NOW = datetime(2026, 8, 26, 12, 0, 0)
RAW_TOKEN_A = "rag4c_scim_stage10_primary_token_secret_aaaaaaaa"
RAW_TOKEN_B = "rag4c_scim_stage10_secondary_token_secret_bbbbbbbb"
RAW_USERS_READ = "rag4c_scim_stage10_users_read_only_cccccccc"
RAW_GROUPS_READ = "rag4c_scim_stage10_groups_read_only_dddddddd"
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


def _digest(raw: str) -> str:
    digest = hashlib.sha256()
    digest.update(b"rag4c:tenant-scim-token:v1\x00")
    digest.update(raw.encode())
    return digest.hexdigest()


def _engine(tmp_path: Path, *, revision: str = REVISION) -> Any:
    engine = create_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'scim.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _configure(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        # 0022's current parallel ORM uses the reverse composite order for this FK.
        # The additional authority index makes the exact named FK executable on SQLite.
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_test_tenant_members_tenant_account "
                "ON tenant_members (tenant_id, account_id)"
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
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id=TENANT_A, name="SCIM Tenant A", status="active"),
                Tenant(id=TENANT_B, name="SCIM Tenant B", status="active"),
                Account(id="issuer-a", name="Issuer A", email="issuer-a@example.test"),
                Account(id="issuer-b", name="Issuer B", email="issuer-b@example.test"),
            ]
        )
        session.flush()
        session.add_all(
            [
                TenantMember(
                    tenant_id=TENANT_A,
                    account_id="issuer-a",
                    role="owner",
                    status="active",
                    revision=1,
                    updated_by="issuer-a",
                ),
                TenantMember(
                    tenant_id=TENANT_B,
                    account_id="issuer-b",
                    role="owner",
                    status="active",
                    revision=1,
                    updated_by="issuer-b",
                ),
            ]
        )
        session.flush()
        for token_id, tenant_id, raw, scopes, issuer in (
            (
                "scim-token-a",
                TENANT_A,
                RAW_TOKEN_A,
                ["users:read", "users:write", "groups:read", "groups:write"],
                "issuer-a",
            ),
            (
                "scim-token-b",
                TENANT_B,
                RAW_TOKEN_B,
                ["users:read", "users:write", "groups:read", "groups:write"],
                "issuer-b",
            ),
            ("scim-users-read", TENANT_A, RAW_USERS_READ, ["users:read"], "issuer-a"),
            ("scim-groups-read", TENANT_A, RAW_GROUPS_READ, ["groups:read"], "issuer-a"),
        ):
            session.execute(
                text(
                    """
                    INSERT INTO tenant_scim_tokens (
                        id, tenant_id, name, active_name_key, token_hash, token_prefix,
                        status, scopes, expires_at, last_used_at, last_used_ip_hash,
                        use_count, revision, issued_at, issued_by, created_at, updated_at
                    ) VALUES (
                        :id, :tenant_id, :name, :name, :token_hash, :token_prefix,
                        'active', :scopes, :expires_at, NULL, NULL, 0, 1,
                        :issued_at, :issued_by, :created_at, :updated_at
                    )
                    """
                ),
                {
                    "id": token_id,
                    "tenant_id": tenant_id,
                    "name": token_id,
                    "token_hash": _digest(raw),
                    "token_prefix": raw[:16],
                    "scopes": json.dumps(scopes),
                    "expires_at": NOW + timedelta(days=1),
                    "issued_at": NOW,
                    "issued_by": issuer,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
            )
        session.commit()
    return engine


def _client(engine: Any) -> TestClient:
    app = FastAPI()
    try:
        module = importlib.import_module("server.scim_api")
    except ModuleNotFoundError:
        return TestClient(app)
    app.include_router(
        module.build_scim_router(
            read_engine_provider=lambda: engine,
            mutation_engine_provider=lambda: engine,
            now_provider=lambda: NOW,
            ip_hash_key_provider=lambda: b"stage10-ip-hash-key",
        )
    )
    return TestClient(app)


def _headers(
    raw: str = RAW_TOKEN_A,
    *,
    if_match: str | None = None,
    tenant_override: str | None = None,
) -> dict[str, str]:
    result = {
        "Authorization": f"Bearer {raw}",
        "X-Request-ID": "stage10-request-0001",
        "X-Forwarded-For": "203.0.113.77",
    }
    if if_match is not None:
        result["If-Match"] = if_match
    if tenant_override is not None:
        result["X-RAG4C-Tenant"] = tenant_override
    return result


def _rows(engine: Any, table: str) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(text(f"SELECT * FROM {table}")).mappings()]


def _create_user(
    client: TestClient,
    *,
    raw: str = RAW_TOKEN_A,
    user_name: str = "alice@example.test",
    external_id: str = "employee-1001",
    display_name: str = "Alice Example",
) -> Any:
    return client.post(
        "/scim/v2/Users",
        headers=_headers(raw),
        json={
            "schemas": [USER_SCHEMA],
            "externalId": external_id,
            "userName": user_name,
            "displayName": display_name,
            "active": True,
        },
    )


def _create_group(
    client: TestClient,
    *,
    members: list[dict[str, str]] | None = None,
    display_name: str = "Platform Engineering",
    external_id: str = "group-2001",
) -> Any:
    return client.post(
        "/scim/v2/Groups",
        headers=_headers(),
        json={
            "schemas": [GROUP_SCHEMA],
            "externalId": external_id,
            "displayName": display_name,
            "members": members or [],
        },
    )


def _assert_error(response: Any, status: int, scim_type: str | None = None) -> None:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/scim+json")
    payload = response.json()
    assert payload["schemas"] == [ERROR_SCHEMA]
    assert payload["status"] == str(status)
    assert payload["detail"]
    if scim_type is not None:
        assert payload["scimType"] == scim_type


def test_discovery_requires_active_token_and_returns_scim_documents(tmp_path: Path) -> None:
    client = _client(_engine(tmp_path))
    _assert_error(client.get("/scim/v2/ServiceProviderConfig"), 401)

    config = client.get("/scim/v2/ServiceProviderConfig", headers=_headers(RAW_USERS_READ))
    resources = client.get("/scim/v2/ResourceTypes", headers=_headers(RAW_USERS_READ))
    schemas = client.get("/scim/v2/Schemas", headers=_headers(RAW_USERS_READ))

    assert config.status_code == 200, config.text
    assert config.headers["content-type"].startswith("application/scim+json")
    assert config.json()["patch"]["supported"] is True
    assert config.json()["filter"]["supported"] is True
    assert config.json()["etag"]["supported"] is True
    assert resources.json()["schemas"] == [LIST_SCHEMA]
    assert {item["name"] for item in resources.json()["Resources"]} == {"User", "Group"}
    assert schemas.json()["schemas"] == [LIST_SCHEMA]
    assert {item["id"] for item in schemas.json()["Resources"]} == {USER_SCHEMA, GROUP_SCHEMA}


def test_bearer_scope_and_token_tenant_are_authoritative(tmp_path: Path) -> None:
    client = _client(_engine(tmp_path))
    _assert_error(client.get("/scim/v2/Users", headers=_headers("not-real")), 401)
    _assert_error(
        client.post(
            "/scim/v2/Users",
            headers=_headers(RAW_USERS_READ),
            json={"userName": "blocked@example.test", "displayName": "Blocked"},
        ),
        403,
    )
    created_b = _create_user(
        client,
        raw=RAW_TOKEN_B,
        user_name="tenant-b@example.test",
        external_id="tenant-b-user",
        display_name="Tenant B User",
    )
    assert created_b.status_code == 201, created_b.text
    tenant_a = client.get(
        "/scim/v2/Users",
        headers=_headers(RAW_TOKEN_A, tenant_override=TENANT_B),
    )
    assert tenant_a.status_code == 200
    assert tenant_a.json()["totalResults"] == 0


def test_user_lifecycle_etag_stale_and_deactivation(tmp_path: Path) -> None:
    client = _client(_engine(tmp_path))
    created = _create_user(client)
    assert created.status_code == 201, created.text
    user = created.json()
    assert user["schemas"] == [USER_SCHEMA]
    assert user["active"] is True
    assert user["meta"]["version"] == 'W/"1"'
    assert created.headers["etag"] == 'W/"1"'
    assert created.headers["location"].endswith(f"/scim/v2/Users/{user['id']}")
    assert "tenant_id" not in user

    fetched = client.get(f"/scim/v2/Users/{user['id']}", headers=_headers())
    assert fetched.status_code == 200
    assert fetched.headers["etag"] == 'W/"1"'

    patched = client.patch(
        f"/scim/v2/Users/{user['id']}",
        headers=_headers(if_match='W/"1"'),
        json={
            "schemas": [PATCH_SCHEMA],
            "Operations": [
                {"op": "replace", "path": "displayName", "value": "Alice Renamed"},
                {"op": "replace", "path": "active", "value": False},
            ],
        },
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["displayName"] == "Alice Renamed"
    assert patched.json()["active"] is False
    assert patched.headers["etag"] == 'W/"2"'

    stale = client.patch(
        f"/scim/v2/Users/{user['id']}",
        headers=_headers(if_match='W/"1"'),
        json={
            "schemas": [PATCH_SCHEMA],
            "Operations": [{"op": "replace", "path": "active", "value": True}],
        },
    )
    _assert_error(stale, 412)
    deleted = client.delete(f"/scim/v2/Users/{user['id']}", headers=_headers(if_match='W/"2"'))
    assert deleted.status_code == 204
    after = client.get(f"/scim/v2/Users/{user['id']}", headers=_headers())
    assert after.json()["active"] is False
    assert after.headers["etag"] == 'W/"3"'


def test_user_patch_and_delete_require_if_match(tmp_path: Path) -> None:
    client = _client(_engine(tmp_path))
    user = _create_user(client).json()
    patch = client.patch(
        f"/scim/v2/Users/{user['id']}",
        headers=_headers(),
        json={
            "schemas": [PATCH_SCHEMA],
            "Operations": [{"op": "replace", "path": "active", "value": False}],
        },
    )
    delete = client.delete(f"/scim/v2/Users/{user['id']}", headers=_headers())
    _assert_error(patch, 428)
    _assert_error(delete, 428)


def test_existing_account_attach_and_duplicate_conflicts(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with Session(engine) as session:
        session.add(Account(id="preexisting", name="Existing", email="existing@example.test"))
        session.commit()
    client = _client(engine)
    attached = _create_user(
        client,
        user_name="existing@example.test",
        external_id="employee-existing",
        display_name="Existing Attached",
    )
    assert attached.status_code == 201, attached.text
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM accounts WHERE email='existing@example.test'")
            )
            == 1
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM tenant_members "
                    "WHERE tenant_id=:tenant AND account_id='preexisting'"
                ),
                {"tenant": TENANT_A},
            )
            == 1
        )
    _assert_error(
        _create_user(
            client,
            user_name="existing@example.test",
            external_id="employee-other",
            display_name="Duplicate",
        ),
        409,
        "uniqueness",
    )
    _assert_error(
        _create_user(
            client,
            user_name="other@example.test",
            external_id="employee-existing",
            display_name="Duplicate External",
        ),
        409,
        "uniqueness",
    )


def test_user_exact_filters_and_bounded_pagination(tmp_path: Path) -> None:
    client = _client(_engine(tmp_path))
    for index, name in enumerate(("Alice", "Bob", "Carol"), start=1):
        response = _create_user(
            client,
            user_name=f"{name.casefold()}@example.test",
            external_id=f"employee-{index}",
            display_name=f"{name} Example",
        )
        assert response.status_code == 201, response.text
    page = client.get("/scim/v2/Users?startIndex=2&count=1", headers=_headers())
    assert page.json()["totalResults"] == 3
    assert page.json()["startIndex"] == 2
    assert page.json()["itemsPerPage"] == 1
    assert len(page.json()["Resources"]) == 1
    exact = client.get(
        "/scim/v2/Users?filter=userName%20eq%20%22bob%40example.test%22",
        headers=_headers(),
    )
    display = client.get(
        "/scim/v2/Users?filter=displayName%20eq%20%22Carol%20Example%22",
        headers=_headers(),
    )
    assert exact.json()["Resources"][0]["userName"] == "bob@example.test"
    assert display.json()["Resources"][0]["displayName"] == "Carol Example"
    _assert_error(
        client.get("/scim/v2/Users?filter=userName%20co%20%22example%22", headers=_headers()),
        400,
        "invalidFilter",
    )


def test_group_lifecycle_members_and_archive(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    client = _client(engine)
    alice = _create_user(client).json()
    bob = _create_user(
        client,
        user_name="bob@example.test",
        external_id="employee-1002",
        display_name="Bob Example",
    ).json()
    created = _create_group(client, members=[{"value": alice["id"]}])
    assert created.status_code == 201, created.text
    group = created.json()
    assert group["schemas"] == [GROUP_SCHEMA]
    assert [item["value"] for item in group["members"]] == [alice["id"]]
    assert group["members"][0]["$ref"] == f"/scim/v2/Users/{alice['id']}"
    patched = client.patch(
        f"/scim/v2/Groups/{group['id']}",
        headers=_headers(if_match='W/"1"'),
        json={
            "schemas": [PATCH_SCHEMA],
            "Operations": [
                {"op": "replace", "path": "displayName", "value": "Core Platform"},
                {"op": "add", "path": "members", "value": [{"value": bob["id"]}]},
                {"op": "remove", "path": f'members[value eq "{alice["id"]}"]'},
            ],
        },
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["displayName"] == "Core Platform"
    assert [item["value"] for item in patched.json()["members"]] == [bob["id"]]
    archived = client.delete(f"/scim/v2/Groups/{group['id']}", headers=_headers(if_match='W/"2"'))
    assert archived.status_code == 204
    _assert_error(client.get(f"/scim/v2/Groups/{group['id']}", headers=_headers()), 404)
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM tenant_group_members "
                    "WHERE tenant_id=:tenant AND status='active'"
                ),
                {"tenant": TENANT_A},
            )
            == 0
        )


def test_group_rejects_cross_tenant_members_and_duplicates(tmp_path: Path) -> None:
    client = _client(_engine(tmp_path))
    foreign_user = _create_user(
        client,
        raw=RAW_TOKEN_B,
        user_name="foreign@example.test",
        external_id="foreign-user",
        display_name="Foreign User",
    ).json()
    _assert_error(
        _create_group(client, members=[{"value": foreign_user["id"]}]),
        400,
        "invalidValue",
    )
    assert _create_group(client, display_name="Security", external_id="security").status_code == 201
    _assert_error(
        _create_group(client, display_name="Security", external_id="security-other"),
        409,
        "uniqueness",
    )


def test_group_filters_and_pagination(tmp_path: Path) -> None:
    client = _client(_engine(tmp_path))
    assert _create_group(client, display_name="Platform", external_id="platform").status_code == 201
    assert _create_group(client, display_name="Security", external_id="security").status_code == 201
    exact = client.get(
        "/scim/v2/Groups?filter=displayName%20eq%20%22Security%22", headers=_headers()
    )
    page = client.get("/scim/v2/Groups?startIndex=2&count=1", headers=_headers())
    assert exact.json()["totalResults"] == 1
    assert exact.json()["Resources"][0]["displayName"] == "Security"
    assert page.json()["totalResults"] == 2
    assert page.json()["itemsPerPage"] == 1


def test_token_usage_is_hashed_and_bearer_never_persists(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    response = _client(engine).get("/scim/v2/Users", headers=_headers())
    assert response.status_code == 200, response.text
    token = next(row for row in _rows(engine, "tenant_scim_tokens") if row["id"] == "scim-token-a")
    assert token["use_count"] == 1
    assert token["last_used_at"] is not None
    assert len(token["last_used_ip_hash"]) == 64
    serialized = json.dumps(
        {
            "token": token,
            "audits": _rows(engine, "tenant_audit_events"),
            "users": _rows(engine, "tenant_scim_user_links"),
            "groups": _rows(engine, "tenant_scim_group_links"),
        },
        default=str,
    )
    assert RAW_TOKEN_A not in serialized
    assert "203.0.113.77" not in serialized


def test_audit_failure_rolls_back_resource_and_token_usage(
    tmp_path: Path, monkeypatch: Any
) -> None:
    engine = _engine(tmp_path)
    module = importlib.import_module("core.enterprise_scim")

    def fail_audit(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(module, "_write_audit", fail_audit)
    _assert_error(_create_user(_client(engine)), 503)
    assert _rows(engine, "tenant_scim_user_links") == []
    assert [row for row in _rows(engine, "accounts") if row["email"] == "alice@example.test"] == []
    token = next(row for row in _rows(engine, "tenant_scim_tokens") if row["id"] == "scim-token-a")
    assert token["use_count"] == 0
    assert token["last_used_at"] is None
    assert _rows(engine, "tenant_audit_events") == []


def test_missing_0022_fails_closed_before_usage_write(tmp_path: Path) -> None:
    engine = _engine(tmp_path, revision="0021_enterprise_identity_federation")
    response = _client(engine).get("/scim/v2/Users", headers=_headers())
    _assert_error(response, 503)
    assert response.json()["detail"] == "SCIM provisioning schema requires 0022"
    token = next(row for row in _rows(engine, "tenant_scim_tokens") if row["id"] == "scim-token-a")
    assert token["use_count"] == 0


def test_expired_and_revoked_tokens_are_rejected(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE tenant_scim_tokens SET expires_at=:expired WHERE id='scim-users-read'"),
            {"expired": NOW - timedelta(seconds=1)},
        )
        connection.execute(
            text(
                "UPDATE tenant_scim_tokens SET status='revoked', active_name_key=NULL, "
                "revoked_at=:now, revoked_by='issuer-a' WHERE id='scim-groups-read'"
            ),
            {"now": NOW},
        )
    client = _client(engine)
    _assert_error(client.get("/scim/v2/Users", headers=_headers(RAW_USERS_READ)), 401)
    _assert_error(client.get("/scim/v2/Groups", headers=_headers(RAW_GROUPS_READ)), 401)


def test_invalid_patch_paths_and_filters_do_not_mutate(tmp_path: Path) -> None:
    client = _client(_engine(tmp_path))
    user = _create_user(client).json()
    invalid_patch = client.patch(
        f"/scim/v2/Users/{user['id']}",
        headers=_headers(if_match='W/"1"'),
        json={
            "schemas": [PATCH_SCHEMA],
            "Operations": [{"op": "replace", "path": "tenant_id", "value": "attacker-tenant"}],
        },
    )
    malformed_filter = client.get(
        "/scim/v2/Users?filter=userName%20eq%20%22alice%40example.test%22%20or%20active%20eq%20true",
        headers=_headers(),
    )
    _assert_error(invalid_patch, 400, "invalidPath")
    _assert_error(malformed_filter, 400, "invalidFilter")
    fetched = client.get(f"/scim/v2/Users/{user['id']}", headers=_headers())
    assert fetched.json()["meta"]["version"] == 'W/"1"'
