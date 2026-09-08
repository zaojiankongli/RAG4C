from __future__ import annotations

import importlib
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.dialects import mysql, sqlite
from sqlalchemy.exc import IntegrityError

from core import catalog_schema
from core.catalog_schema import _alembic_config
import models.orm as orm
from tests.test_organization_membership_migration import _seed_0017_scope


REVISION = "0020_tenant_invitation_lifecycle"
DOWN_REVISION = "0019_dataset_acl_control"
LEDGER_TABLE = "tenant_control_mutation_requests"
INVITATION_TABLE = "tenant_invitations"

INVITATION_NEW_COLUMNS = frozenset(
    {
        "pending_email_key",
        "last_sent_at",
        "send_count",
        "revoked_at",
        "revoked_by",
        "updated_by",
    }
)
INVITATION_UNIQUES = {
    "uq_tenant_invitations_pending_email": ("tenant_id", "pending_email_key"),
    "uq_tenant_invitations_token_hash": ("tenant_id", "token_hash"),
}
INVITATION_FOREIGN_KEYS = {
    "fk_tenant_invitations_scope_revoker": (
        ("revoked_by", "tenant_id"),
        "tenant_members",
        ("account_id", "tenant_id"),
    ),
    "fk_tenant_invitations_scope_updater": (
        ("updated_by", "tenant_id"),
        "tenant_members",
        ("account_id", "tenant_id"),
    ),
}
INVITATION_CHECKS = {
    "ck_tenant_invitations_send_count_positive": ("send_count > 0",),
    "ck_tenant_invitations_pending_email_key": (
        "status = 'pending'",
        "pending_email_key is not null",
        "pending_email_key = normalized_email",
        "status <> 'pending'",
        "pending_email_key is null",
    ),
    "ck_tenant_invitations_accepted_evidence": (
        "status <> 'accepted'",
        "accepted_at is not null",
        "accepted_by is not null",
    ),
    "ck_tenant_invitations_revoked_evidence": (
        "status <> 'revoked'",
        "revoked_at is not null",
        "revoked_by is not null",
    ),
}
INVITATION_INDEXES = {
    "ix_tenant_invitations_tenant_status_updated": (
        "tenant_id",
        "status",
        "updated_at",
        "id",
    ),
}

LEDGER_COLUMNS = (
    "id",
    "tenant_id",
    "actor_id",
    "idempotency_key",
    "request_hash",
    "operation",
    "resource_type",
    "resource_id",
    "status",
    "response_json",
    "http_status",
    "created_at",
    "completed_at",
)
LEDGER_NOT_NULL = frozenset(
    {
        "id",
        "tenant_id",
        "actor_id",
        "idempotency_key",
        "request_hash",
        "operation",
        "resource_type",
        "status",
        "created_at",
    }
)
LEDGER_UNIQUES = {
    "uq_tenant_control_mutation_requests_actor_key": (
        "tenant_id",
        "actor_id",
        "idempotency_key",
    ),
}
LEDGER_FOREIGN_KEYS = {
    "fk_tenant_control_mutation_requests_tenant": (
        ("tenant_id",),
        "tenants",
        ("id",),
    ),
    "fk_tenant_control_mutation_requests_actor": (
        ("actor_id",),
        "accounts",
        ("id",),
    ),
}
LEDGER_CHECKS = {
    "ck_tenant_control_mutation_requests_status": (
        "pending",
        "completed",
        "failed",
    ),
    "ck_tenant_control_mutation_requests_idempotency_digest": ("length(idempotency_key) = 64",),
    "ck_tenant_control_mutation_requests_request_hash": ("length(request_hash) = 64",),
}
LEDGER_INDEXES = {
    "ix_tenant_control_mutation_requests_tenant_status_created": (
        "tenant_id",
        "status",
        "created_at",
        "id",
    ),
    "ix_tenant_control_mutation_requests_tenant_actor_created": (
        "tenant_id",
        "actor_id",
        "created_at",
        "id",
    ),
    "ix_tenant_control_mutation_requests_tenant_resource_created": (
        "tenant_id",
        "resource_type",
        "resource_id",
        "created_at",
        "id",
    ),
}


def _migration_module() -> ModuleType:
    try:
        return importlib.import_module(
            "catalog_migrations.versions.0020_tenant_invitation_lifecycle"
        )
    except ModuleNotFoundError:
        pytest.fail("0020 tenant invitation lifecycle migration is missing")


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _enable_foreign_keys(connection: Any) -> None:
    connection.exec_driver_sql("PRAGMA foreign_keys=ON")


def _normalized_sql(value: Any) -> str:
    return " ".join(str(value if value is not None else "").casefold().replace('"', "").split())


def _checks(inspector: Any, table: str) -> dict[str, str]:
    return {
        str(item["name"]): _normalized_sql(item.get("sqltext"))
        for item in inspector.get_check_constraints(table)
        if item.get("name")
    }


def _uniques(inspector: Any, table: str) -> dict[str, tuple[str, ...]]:
    return {
        str(item["name"]): tuple(item.get("column_names") or ())
        for item in inspector.get_unique_constraints(table)
        if item.get("name")
    }


def _foreign_keys(
    inspector: Any, table: str
) -> dict[str, tuple[tuple[str, ...], str, tuple[str, ...]]]:
    return {
        str(item["name"]): (
            tuple(item.get("constrained_columns") or ()),
            str(item["referred_table"]),
            tuple(item.get("referred_columns") or ()),
        )
        for item in inspector.get_foreign_keys(table)
        if item.get("name")
    }


def _indexes(inspector: Any, table: str) -> dict[str, tuple[str, ...]]:
    return {
        str(item["name"]): tuple(item.get("column_names") or ())
        for item in inspector.get_indexes(table)
        if item.get("name")
    }


def _orm_checks(table: Any) -> dict[str, str]:
    return {
        str(item.name): _normalized_sql(item.sqltext)
        for item in table.constraints
        if isinstance(item, CheckConstraint) and item.name
    }


def _orm_uniques(table: Any) -> dict[str, tuple[str, ...]]:
    return {
        str(item.name): tuple(column.name for column in item.columns)
        for item in table.constraints
        if isinstance(item, UniqueConstraint) and item.name
    }


def _orm_foreign_keys(
    table: Any,
) -> dict[str, tuple[tuple[str, ...], str, tuple[str, ...]]]:
    result: dict[str, tuple[tuple[str, ...], str, tuple[str, ...]]] = {}
    for constraint in table.foreign_key_constraints:
        if not constraint.name:
            continue
        elements = list(constraint.elements)
        result[str(constraint.name)] = (
            tuple(element.parent.name for element in elements),
            elements[0].column.table.name,
            tuple(element.column.name for element in elements),
        )
    return result


def _orm_indexes(table: Any) -> dict[str, tuple[str, ...]]:
    return {
        str(item.name): tuple(column.name for column in item.columns)
        for item in table.indexes
        if item.name
    }


def _upgrade_to_0019(tmp_path: Path, name: str = "invitation-lifecycle.db") -> tuple[str, Any]:
    url = _sqlite_url(tmp_path / name)
    config = _alembic_config(url)
    command.upgrade(config, "0017_enterprise_access_graph")
    _seed_0017_scope(url)
    command.upgrade(config, DOWN_REVISION)
    _add_member(url, "account-accepted")
    return url, config


def _add_member(url: str, account_id: str, *, tenant_id: str = "tenant-1") -> None:
    now = datetime(2026, 8, 26, 9, 0, 0)
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            _enable_foreign_keys(connection)
            connection.execute(
                text(
                    "INSERT INTO accounts (id, name, email, created_at) VALUES "
                    "(:id, :id, :email, :now)"
                ),
                {"id": account_id, "email": f"{account_id}@example.test", "now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO tenant_members "
                    "(account_id, tenant_id, role, status, revision, created_at, updated_at, updated_by) "
                    "VALUES (:id, :tenant_id, 'member', 'active', 1, :now, :now, :id)"
                ),
                {"id": account_id, "tenant_id": tenant_id, "now": now},
            )
    finally:
        engine.dispose()


def _insert_invitation(
    url: str,
    *,
    invitation_id: str,
    normalized_email: str,
    token_hash: str,
    status: str = "pending",
    accepted_at: datetime | None = None,
    accepted_by: str | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> None:
    created = created_at or datetime(2026, 8, 26, 10, 0, 0)
    updated = updated_at or created
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            _enable_foreign_keys(connection)
            connection.execute(
                text(
                    "INSERT INTO tenant_invitations "
                    "(id, tenant_id, email, normalized_email, role, status, token_hash, "
                    "expires_at, accepted_at, accepted_by, invited_by, revision, created_at, "
                    "updated_at) VALUES "
                    "(:id, 'tenant-1', :email, :normalized_email, 'member', :status, "
                    ":token_hash, :expires_at, :accepted_at, :accepted_by, 'account-1', 1, "
                    ":created_at, :updated_at)"
                ),
                {
                    "id": invitation_id,
                    "email": normalized_email,
                    "normalized_email": normalized_email,
                    "status": status,
                    "token_hash": token_hash,
                    "expires_at": created + timedelta(days=7),
                    "accepted_at": accepted_at,
                    "accepted_by": accepted_by,
                    "created_at": created,
                    "updated_at": updated,
                },
            )
    finally:
        engine.dispose()


def _insert_ledger(url: str, *, request_id: str = "tenant-request-1") -> None:
    now = datetime(2026, 8, 26, 12, 0, 0)
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            _enable_foreign_keys(connection)
            connection.execute(
                text(
                    f"INSERT INTO {LEDGER_TABLE} "
                    "(id, tenant_id, actor_id, idempotency_key, request_hash, operation, "
                    "resource_type, resource_id, status, response_json, http_status, created_at) "
                    "VALUES (:id, 'tenant-1', 'account-1', :key, :hash, "
                    "'tenant_invitation.create', 'tenant_invitation', 'invite-pending', "
                    "'completed', :response, 201, :now)"
                ),
                {
                    "id": request_id,
                    "key": "a" * 64,
                    "hash": "b" * 64,
                    "response": '{"ok": true}',
                    "now": now,
                },
            )
    finally:
        engine.dispose()


def test_0020_precedes_the_current_application_head() -> None:
    migration = _migration_module()
    scripts = ScriptDirectory.from_config(_alembic_config("sqlite://"))

    assert migration.revision == REVISION
    assert migration.down_revision == DOWN_REVISION
    assert (
        scripts.get_current_head()
        == catalog_schema.HEAD_REVISION
        == "0029_enterprise_knowledge_base_releases"
    )


def test_invitation_lifecycle_orm_and_manifest_contracts_match() -> None:
    _migration_module()
    invitation = orm.TenantInvitation.__table__
    ledger_model = getattr(orm, "TenantControlMutationRequest", None)
    assert ledger_model is not None
    ledger = ledger_model.__table__

    assert INVITATION_NEW_COLUMNS <= set(invitation.columns.keys())
    assert INVITATION_UNIQUES.items() <= _orm_uniques(invitation).items()
    assert INVITATION_FOREIGN_KEYS.items() <= _orm_foreign_keys(invitation).items()
    invitation_checks = _orm_checks(invitation)
    for name, fragments in INVITATION_CHECKS.items():
        assert name in invitation_checks
        assert all(fragment in invitation_checks[name] for fragment in fragments)
    invitation_indexes = _orm_indexes(invitation)
    for name, columns in INVITATION_INDEXES.items():
        assert invitation_indexes[name] == columns

    assert tuple(column.name for column in ledger.columns) == LEDGER_COLUMNS
    assert _orm_uniques(ledger) == LEDGER_UNIQUES
    assert _orm_foreign_keys(ledger) == LEDGER_FOREIGN_KEYS
    ledger_checks = _orm_checks(ledger)
    assert set(ledger_checks) == set(LEDGER_CHECKS)
    for name, fragments in LEDGER_CHECKS.items():
        assert all(fragment in ledger_checks[name] for fragment in fragments)
    assert _orm_indexes(ledger) == LEDGER_INDEXES

    assert isinstance(ledger.c.id.type, String) and ledger.c.id.type.length == 64
    assert isinstance(ledger.c.idempotency_key.type, String)
    assert ledger.c.idempotency_key.type.length == 64
    assert isinstance(ledger.c.request_hash.type, String)
    assert ledger.c.request_hash.type.length == 64
    assert isinstance(ledger.c.response_json.type, JSON)
    assert isinstance(ledger.c.http_status.type, Integer)
    assert isinstance(ledger.c.created_at.type, DateTime)

    assert catalog_schema._HEAD_REQUIRED_COLUMNS[LEDGER_TABLE] == frozenset(LEDGER_COLUMNS)
    assert catalog_schema._HEAD_REQUIRED_NOT_NULL[LEDGER_TABLE] == LEDGER_NOT_NULL
    assert catalog_schema._HEAD_REQUIRED_UNIQUES[LEDGER_TABLE] == LEDGER_UNIQUES
    assert catalog_schema._HEAD_REQUIRED_FOREIGN_KEYS[LEDGER_TABLE] == LEDGER_FOREIGN_KEYS
    assert catalog_schema._HEAD_REQUIRED_CHECK_FRAGMENTS[LEDGER_TABLE] == LEDGER_CHECKS
    assert catalog_schema._HEAD_REQUIRED_INDEXES[LEDGER_TABLE] == LEDGER_INDEXES


def test_0020_upgrade_backfills_historical_rows_and_creates_contract(tmp_path: Path) -> None:
    url, config = _upgrade_to_0019(tmp_path)
    pending_created = datetime(2026, 8, 26, 10, 1, 0)
    accepted_at = datetime(2026, 8, 26, 10, 7, 0)
    revoked_updated = datetime(2026, 8, 26, 10, 9, 0)
    _insert_invitation(
        url,
        invitation_id="invite-pending",
        normalized_email="pending@example.test",
        token_hash="1" * 64,
        created_at=pending_created,
    )
    _insert_invitation(
        url,
        invitation_id="invite-accepted",
        normalized_email="accepted@example.test",
        token_hash="2" * 64,
        status="accepted",
        accepted_at=accepted_at,
        accepted_by="account-accepted",
    )
    _insert_invitation(
        url,
        invitation_id="invite-expired",
        normalized_email="expired@example.test",
        token_hash="3" * 64,
        status="expired",
    )
    _insert_invitation(
        url,
        invitation_id="invite-revoked",
        normalized_email="revoked@example.test",
        token_hash="4" * 64,
        status="revoked",
        updated_at=revoked_updated,
    )

    command.upgrade(config, REVISION)

    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        assert LEDGER_TABLE in inspector.get_table_names()
        invitation_columns = {
            column["name"]: column for column in inspector.get_columns(INVITATION_TABLE)
        }
        assert INVITATION_NEW_COLUMNS <= set(invitation_columns)
        assert invitation_columns["last_sent_at"]["nullable"] is False
        assert invitation_columns["send_count"]["nullable"] is False
        assert invitation_columns["updated_by"]["nullable"] is False
        assert INVITATION_UNIQUES.items() <= _uniques(inspector, INVITATION_TABLE).items()
        assert INVITATION_FOREIGN_KEYS.items() <= _foreign_keys(inspector, INVITATION_TABLE).items()
        checks = _checks(inspector, INVITATION_TABLE)
        for name, fragments in INVITATION_CHECKS.items():
            assert name in checks
            assert all(fragment in checks[name] for fragment in fragments)
        indexes = _indexes(inspector, INVITATION_TABLE)
        for name, columns in INVITATION_INDEXES.items():
            assert indexes[name] == columns

        ledger_columns = {column["name"]: column for column in inspector.get_columns(LEDGER_TABLE)}
        assert tuple(ledger_columns) == LEDGER_COLUMNS
        assert _uniques(inspector, LEDGER_TABLE) == LEDGER_UNIQUES
        assert _foreign_keys(inspector, LEDGER_TABLE) == LEDGER_FOREIGN_KEYS
        ledger_checks = _checks(inspector, LEDGER_TABLE)
        assert set(ledger_checks) == set(LEDGER_CHECKS)
        assert _indexes(inspector, LEDGER_TABLE) == LEDGER_INDEXES

        with engine.connect() as connection:
            rows = {
                row.id: row
                for row in connection.execute(
                    text(
                        "SELECT id, pending_email_key, last_sent_at, send_count, revoked_at, "
                        "revoked_by, updated_by FROM tenant_invitations ORDER BY id"
                    )
                ).mappings()
            }
        assert rows["invite-pending"]["pending_email_key"] == "pending@example.test"
        assert rows["invite-pending"]["send_count"] == 1
        assert rows["invite-pending"]["updated_by"] == "account-1"
        assert rows["invite-accepted"]["pending_email_key"] is None
        assert rows["invite-expired"]["pending_email_key"] is None
        assert rows["invite-revoked"]["pending_email_key"] is None
        assert rows["invite-revoked"]["revoked_by"] == "account-1"
    finally:
        engine.dispose()

    _insert_ledger(url)


def test_0020_duplicate_pending_preflight_fails_before_schema_changes(tmp_path: Path) -> None:
    url, config = _upgrade_to_0019(tmp_path, "duplicate-pending.db")
    _insert_invitation(
        url,
        invitation_id="invite-duplicate-1",
        normalized_email="duplicate@example.test",
        token_hash="a" * 64,
    )
    _insert_invitation(
        url,
        invitation_id="invite-duplicate-2",
        normalized_email="duplicate@example.test",
        token_hash="b" * 64,
    )
    _migration_module()

    with pytest.raises(RuntimeError, match="duplicate pending") as exc_info:
        command.upgrade(config, REVISION)
    assert type(exc_info.value).__name__ == "TenantInvitationLifecyclePreflightError"

    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        assert LEDGER_TABLE not in inspector.get_table_names()
        assert not INVITATION_NEW_COLUMNS & {
            column["name"] for column in inspector.get_columns(INVITATION_TABLE)
        }
        assert catalog_schema.inspect_catalog_schema(engine).revision == DOWN_REVISION
    finally:
        engine.dispose()


def test_0020_database_constraints_reject_invalid_lifecycle_and_ledger_rows(
    tmp_path: Path,
) -> None:
    url, config = _upgrade_to_0019(tmp_path, "invitation-constraints.db")
    _insert_invitation(
        url,
        invitation_id="invite-pending",
        normalized_email="pending@example.test",
        token_hash="a" * 64,
    )
    command.upgrade(config, REVISION)
    now = datetime(2026, 8, 26, 12, 0, 0)
    engine = create_engine(url)
    try:
        invitation_sql = (
            "INSERT INTO tenant_invitations "
            "(id, tenant_id, email, normalized_email, role, status, token_hash, expires_at, "
            "accepted_at, accepted_by, invited_by, revision, created_at, updated_at, "
            "pending_email_key, last_sent_at, send_count, revoked_at, revoked_by, updated_by) "
            "VALUES (:id, 'tenant-1', :email, :email, 'member', :status, :token_hash, "
            ":expires_at, :accepted_at, :accepted_by, 'account-1', 1, :now, :now, "
            ":pending_email_key, :now, :send_count, :revoked_at, :revoked_by, :updated_by)"
        )
        invalid_rows = [
            {
                "id": "invalid-send-count",
                "email": "count@example.test",
                "status": "pending",
                "token_hash": "1" * 64,
                "pending_email_key": "count@example.test",
                "send_count": 0,
                "accepted_at": None,
                "accepted_by": None,
                "revoked_at": None,
                "revoked_by": None,
                "updated_by": "account-1",
            },
            {
                "id": "invalid-pending-key",
                "email": "pending-key@example.test",
                "status": "pending",
                "token_hash": "2" * 64,
                "pending_email_key": None,
                "send_count": 1,
                "accepted_at": None,
                "accepted_by": None,
                "revoked_at": None,
                "revoked_by": None,
                "updated_by": "account-1",
            },
            {
                "id": "invalid-accepted",
                "email": "accepted-invalid@example.test",
                "status": "accepted",
                "token_hash": "3" * 64,
                "pending_email_key": None,
                "send_count": 1,
                "accepted_at": None,
                "accepted_by": None,
                "revoked_at": None,
                "revoked_by": None,
                "updated_by": "account-1",
            },
            {
                "id": "invalid-revoked",
                "email": "revoked-invalid@example.test",
                "status": "revoked",
                "token_hash": "4" * 64,
                "pending_email_key": None,
                "send_count": 1,
                "accepted_at": None,
                "accepted_by": None,
                "revoked_at": None,
                "revoked_by": None,
                "updated_by": "account-1",
            },
        ]
        for row in invalid_rows:
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    _enable_foreign_keys(connection)
                    connection.execute(
                        text(invitation_sql),
                        {**row, "expires_at": now + timedelta(days=7), "now": now},
                    )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                _enable_foreign_keys(connection)
                connection.execute(
                    text(invitation_sql),
                    {
                        "id": "duplicate-pending",
                        "email": "pending@example.test",
                        "status": "pending",
                        "token_hash": "5" * 64,
                        "pending_email_key": "pending@example.test",
                        "send_count": 1,
                        "accepted_at": None,
                        "accepted_by": None,
                        "revoked_at": None,
                        "revoked_by": None,
                        "updated_by": "account-1",
                        "expires_at": now + timedelta(days=7),
                        "now": now,
                    },
                )

        ledger_sql = (
            f"INSERT INTO {LEDGER_TABLE} "
            "(id, tenant_id, actor_id, idempotency_key, request_hash, operation, "
            "resource_type, status, created_at) VALUES "
            "(:id, :tenant_id, :actor_id, :key, :hash, 'tenant_invitation.create', "
            "'tenant_invitation', :status, :now)"
        )
        invalid_ledger_rows = [
            {
                "id": "bad-key",
                "tenant_id": "tenant-1",
                "actor_id": "account-1",
                "key": "a" * 63,
                "hash": "b" * 64,
                "status": "pending",
            },
            {
                "id": "bad-hash",
                "tenant_id": "tenant-1",
                "actor_id": "account-1",
                "key": "a" * 64,
                "hash": "b" * 63,
                "status": "pending",
            },
            {
                "id": "bad-status",
                "tenant_id": "tenant-1",
                "actor_id": "account-1",
                "key": "c" * 64,
                "hash": "d" * 64,
                "status": "running",
            },
        ]
        for row in invalid_ledger_rows:
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    _enable_foreign_keys(connection)
                    connection.execute(text(ledger_sql), {**row, "now": now})
    finally:
        engine.dispose()


def test_tenant_control_ledger_accepts_nonmember_account_with_tenant_scoped_key(
    tmp_path: Path,
) -> None:
    url, config = _upgrade_to_0019(tmp_path, "pre-membership-actor.db")
    command.upgrade(config, REVISION)
    now = datetime(2026, 8, 26, 12, 30, 0)
    statement = text(
        f"INSERT INTO {LEDGER_TABLE} "
        "(id, tenant_id, actor_id, idempotency_key, request_hash, operation, "
        "resource_type, status, created_at) VALUES "
        "(:id, :tenant_id, 'account-2', :key, :hash, 'tenant_invitation.accept', "
        "'tenant_invitation', 'pending', :now)"
    )
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            _enable_foreign_keys(connection)
            connection.execute(
                statement,
                {
                    "id": "accept-tenant-1",
                    "tenant_id": "tenant-1",
                    "key": "a" * 64,
                    "hash": "b" * 64,
                    "now": now,
                },
            )
            connection.execute(
                statement,
                {
                    "id": "accept-tenant-2",
                    "tenant_id": "tenant-2",
                    "key": "a" * 64,
                    "hash": "b" * 64,
                    "now": now,
                },
            )
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"SELECT tenant_id, actor_id FROM {LEDGER_TABLE} "
                    "WHERE id IN ('accept-tenant-1', 'accept-tenant-2') "
                    "ORDER BY tenant_id"
                )
            ).all()
        assert rows == [("tenant-1", "account-2"), ("tenant-2", "account-2")]
        # Database integrity proves Account existence and tenant-key separation;
        # signed actor/tenant authorization remains the service boundary.
    finally:
        engine.dispose()


def test_0019_to_0020_to_0019_to_0020_roundtrip(tmp_path: Path) -> None:
    url, config = _upgrade_to_0019(tmp_path, "invitation-roundtrip.db")
    _insert_invitation(
        url,
        invitation_id="invite-roundtrip",
        normalized_email="roundtrip@example.test",
        token_hash="c" * 64,
    )
    command.upgrade(config, REVISION)
    _insert_ledger(url)

    command.downgrade(config, DOWN_REVISION)
    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        assert LEDGER_TABLE not in inspector.get_table_names()
        assert not INVITATION_NEW_COLUMNS & {
            column["name"] for column in inspector.get_columns(INVITATION_TABLE)
        }
        assert catalog_schema.inspect_catalog_schema(engine).status == "behind"
    finally:
        engine.dispose()

    command.upgrade(config, REVISION)
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT pending_email_key, send_count, updated_by "
                    "FROM tenant_invitations WHERE id='invite-roundtrip'"
                )
            ).one()
        assert tuple(row) == ("roundtrip@example.test", 1, "account-1")
        assert catalog_schema.inspect_catalog_schema(engine).status == "behind"
    finally:
        engine.dispose()


def test_0020_mysql_offline_ddl_compiles() -> None:
    output = StringIO()
    config = _alembic_config("mysql+pymysql://user:pass@localhost/rag4c")
    config.output_buffer = output

    command.upgrade(config, f"{DOWN_REVISION}:{REVISION}", sql=True)

    ddl = output.getvalue().upper()
    assert "ALTER TABLE TENANT_INVITATIONS" in ddl
    assert "PENDING_EMAIL_KEY" in ddl
    assert "LAST_SENT_AT" in ddl
    assert "SEND_COUNT" in ddl
    assert "REVOKED_AT" in ddl
    assert "REVOKED_BY" in ddl
    assert "UPDATED_BY" in ddl
    assert f"CREATE TABLE {LEDGER_TABLE.upper()}" in ddl
    assert "DATETIME(6)" in ddl
    assert "JSON" in ddl
    assert "LENGTH(IDEMPOTENCY_KEY) = 64" in ddl
    assert "LENGTH(REQUEST_HASH) = 64" in ddl
    assert "REFERENCES ACCOUNTS (ID)" in ddl
    assert "FOREIGN KEY(ACTOR_ID, TENANT_ID)" not in ddl
    for name in (
        *INVITATION_UNIQUES,
        *INVITATION_FOREIGN_KEYS,
        *INVITATION_CHECKS,
        *INVITATION_INDEXES,
        *LEDGER_UNIQUES,
        *LEDGER_FOREIGN_KEYS,
        *LEDGER_CHECKS,
        *LEDGER_INDEXES,
    ):
        assert name.upper() in ddl


def test_0020_datetime6_helper_is_dialect_aware() -> None:
    migration = _migration_module()
    sqlite_type = migration._datetime6().dialect_impl(sqlite.dialect())
    mysql_type = migration._datetime6().dialect_impl(mysql.dialect())
    assert isinstance(sqlite_type, DateTime)
    assert isinstance(mysql_type, mysql.DATETIME)
    assert mysql_type.fsp == 6
