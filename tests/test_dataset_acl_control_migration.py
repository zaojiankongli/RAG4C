from __future__ import annotations

import importlib
from datetime import datetime
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
from sqlalchemy.orm import Session

from core import catalog_schema
from core.enterprise_access_control import evaluate_dataset_permissions
from core.knowledge_permissions import KNOWLEDGE_READ
from core.catalog_schema import _alembic_config
import models.orm as orm
from tests.test_organization_membership_migration import _seed_0017_scope


REVISION = "0019_dataset_acl_control"
DOWN_REVISION = "0018_organization_membership"
APPLICATION_HEAD = catalog_schema.HEAD_REVISION
TABLE_NAME = "dataset_acl_mutation_requests"

DATASET_NEW_COLUMNS = (
    "acl_mode",
    "acl_revision",
    "acl_enabled_at",
    "acl_enabled_by",
)
DATASET_CHECKS = {
    "ck_datasets_acl_mode": ("tenant_role", "dataset_acl"),
    "ck_datasets_acl_revision_positive": ("acl_revision > 0",),
}

EXPECTED_COLUMNS = (
    "id",
    "tenant_id",
    "dataset_id",
    "actor_id",
    "idempotency_key",
    "request_hash",
    "operation",
    "status",
    "resource_id",
    "response_json",
    "http_status",
    "created_at",
    "completed_at",
)
EXPECTED_UNIQUES = {
    "uq_dataset_acl_mutation_requests_actor_key": (
        "tenant_id",
        "actor_id",
        "idempotency_key",
    ),
}
EXPECTED_FOREIGN_KEYS = {
    "fk_dataset_acl_mutation_requests_scope_dataset": (
        ("tenant_id", "dataset_id"),
        "datasets",
        ("tenant_id", "id"),
    ),
    "fk_dataset_acl_mutation_requests_scope_actor": (
        ("actor_id", "tenant_id"),
        "tenant_members",
        ("account_id", "tenant_id"),
    ),
}
EXPECTED_CHECKS = {
    "ck_dataset_acl_mutation_requests_status": (
        "pending",
        "completed",
        "failed",
    ),
    "ck_dataset_acl_mutation_requests_idempotency_key_length": (
        "length(idempotency_key)",
        "between 1 and 128",
    ),
}
EXPECTED_INDEXES = {
    "ix_dataset_acl_mutation_requests_tenant_dataset_status_created": (
        "tenant_id",
        "dataset_id",
        "status",
        "created_at",
        "id",
    ),
    "ix_dataset_acl_mutation_requests_tenant_actor_key": (
        "tenant_id",
        "actor_id",
        "idempotency_key",
    ),
}


def _migration_module() -> ModuleType:
    try:
        return importlib.import_module("catalog_migrations.versions.0019_dataset_acl_control")
    except ModuleNotFoundError:
        pytest.fail("0019 dataset ACL control migration is missing")


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _enable_sqlite_foreign_keys(connection: Any) -> None:
    connection.exec_driver_sql("PRAGMA foreign_keys=ON")


def _indexes(inspector: Any, table_name: str) -> dict[str, tuple[str, ...]]:
    return {
        str(item["name"]): tuple(item.get("column_names") or ())
        for item in inspector.get_indexes(table_name)
        if item.get("name")
    }


def _uniques(inspector: Any, table_name: str) -> dict[str, tuple[str, ...]]:
    return {
        str(item["name"]): tuple(item.get("column_names") or ())
        for item in inspector.get_unique_constraints(table_name)
        if item.get("name")
    }


def _foreign_keys(
    inspector: Any, table_name: str
) -> dict[str, tuple[tuple[str, ...], str, tuple[str, ...]]]:
    return {
        str(item["name"]): (
            tuple(item.get("constrained_columns") or ()),
            str(item["referred_table"]),
            tuple(item.get("referred_columns") or ()),
        )
        for item in inspector.get_foreign_keys(table_name)
        if item.get("name")
    }


def _checks(inspector: Any, table_name: str) -> dict[str, str]:
    return {
        str(item["name"]): " ".join(str(item.get("sqltext") or "").casefold().split())
        for item in inspector.get_check_constraints(table_name)
        if item.get("name")
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
    for item in table.foreign_key_constraints:
        if not item.name:
            continue
        elements = list(item.elements)
        result[str(item.name)] = (
            tuple(element.parent.name for element in elements),
            elements[0].column.table.name,
            tuple(element.column.name for element in elements),
        )
    return result


def _orm_checks(table: Any) -> dict[str, str]:
    return {
        str(item.name): " ".join(str(item.sqltext).casefold().split())
        for item in table.constraints
        if isinstance(item, CheckConstraint) and item.name
    }


def _orm_indexes(table: Any) -> dict[str, tuple[str, ...]]:
    return {
        str(item.name): tuple(column.name for column in item.columns)
        for item in table.indexes
        if item.name in EXPECTED_INDEXES
    }


def _insert_dataset(url: str) -> None:
    now = datetime(2026, 8, 26, 12, 0, 0)
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            _enable_sqlite_foreign_keys(connection)
            connection.execute(
                text(
                    "INSERT INTO datasets "
                    "(id, tenant_id, name, description, status, profile_revision, "
                    "owner_id, visibility, profile_json, parser_policy, chunk_policy, "
                    "retrieval_policy, retention_policy, metadata_policy, default_language, "
                    "graph_enabled, qa_enabled, mutation_generation, serving_generation, "
                    "doc_count, chunk_count, created_at, updated_at) VALUES "
                    "('dataset-1', 'tenant-1', 'Dataset 1', '', 'active', 1, "
                    "'account-1', 'private', '{}', '{}', '{}', '{}', '{}', '{}', 'zh-CN', "
                    "0, 1, 0, 0, 0, 0, :now, :now)"
                ),
                {"now": now},
            )
    finally:
        engine.dispose()


def _upgrade_to_0018_with_scope(tmp_path: Path) -> tuple[str, Any]:
    url = _sqlite_url(tmp_path / "dataset-acl-control.db")
    config = _alembic_config(url)
    command.upgrade(config, "0017_enterprise_access_graph")
    _seed_0017_scope(url)
    command.upgrade(config, DOWN_REVISION)
    _insert_dataset(url)
    return url, config


def _insert_same_tenant_member(
    url: str,
    *,
    account_id: str = "account-unmatched",
    role: str = "member",
) -> None:
    now = datetime(2026, 8, 26, 11, 57, 0)
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            _enable_sqlite_foreign_keys(connection)
            connection.execute(
                text(
                    "INSERT INTO accounts (id, name, email, created_at) VALUES "
                    "(:account_id, :account_id, :email, :now)"
                ),
                {
                    "account_id": account_id,
                    "email": f"{account_id}@example.test",
                    "now": now,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO tenant_members "
                    "(account_id, tenant_id, role, status, revision, created_at, "
                    "updated_at, updated_by) VALUES "
                    "(:account_id, 'tenant-1', :role, 'active', 1, :now, :now, :account_id)"
                ),
                {"account_id": account_id, "role": role, "now": now},
            )
    finally:
        engine.dispose()


def _insert_access_grant(
    url: str,
    *,
    grant_id: str,
    subject_type: str,
    subject_id: str,
    status: str,
    created_at: datetime,
    role: str = "viewer",
) -> None:
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            _enable_sqlite_foreign_keys(connection)
            connection.execute(
                text(
                    "INSERT INTO dataset_access_grants "
                    "(id, tenant_id, dataset_id, subject_type, subject_id, role, status, "
                    "revision, created_at, updated_at) VALUES "
                    "(:id, 'tenant-1', 'dataset-1', :subject_type, :subject_id, :role, "
                    ":status, 1, :created_at, :created_at)"
                ),
                {
                    "id": grant_id,
                    "subject_type": subject_type,
                    "subject_id": subject_id,
                    "role": role,
                    "status": status,
                    "created_at": created_at,
                },
            )
    finally:
        engine.dispose()


def _dataset_control_state(url: str) -> tuple[str, int, datetime | None, str | None]:
    engine = create_engine(url)
    try:
        with Session(engine) as session:
            dataset = session.get(orm.Dataset, "dataset-1")
            assert dataset is not None
            return (
                str(dataset.acl_mode),
                int(dataset.acl_revision),
                dataset.acl_enabled_at,
                dataset.acl_enabled_by,
            )
    finally:
        engine.dispose()


def _insert_request(
    url: str,
    *,
    request_id: str = "request-1",
    tenant_id: str = "tenant-1",
    dataset_id: str = "dataset-1",
    actor_id: str = "account-1",
    key: str = "acl-mutation-1",
    status: str = "pending",
) -> None:
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            _enable_sqlite_foreign_keys(connection)
            connection.execute(
                text(
                    f"INSERT INTO {TABLE_NAME} "
                    "(id, tenant_id, dataset_id, actor_id, idempotency_key, request_hash, "
                    "operation, status, resource_id, response_json, http_status, created_at) "
                    "VALUES (:id, :tenant_id, :dataset_id, :actor_id, :key, :hash, "
                    ":operation, :status, :resource_id, :response_json, :http_status, :created_at)"
                ),
                {
                    "id": request_id,
                    "tenant_id": tenant_id,
                    "dataset_id": dataset_id,
                    "actor_id": actor_id,
                    "key": key,
                    "hash": "a" * 64,
                    "operation": "grant.create",
                    "status": status,
                    "resource_id": "grant-1",
                    "response_json": '{"ok": true}',
                    "http_status": 201,
                    "created_at": datetime(2026, 8, 26, 12, 1, 0),
                },
            )
    finally:
        engine.dispose()


def test_0019_is_the_dataset_acl_control_head() -> None:
    migration = _migration_module()
    scripts = ScriptDirectory.from_config(_alembic_config("sqlite://"))

    assert migration.revision == REVISION
    assert migration.down_revision == DOWN_REVISION
    assert scripts.get_current_head() == APPLICATION_HEAD
    assert catalog_schema.HEAD_REVISION == APPLICATION_HEAD


def test_dataset_acl_control_orm_and_manifest_contracts_match() -> None:
    _migration_module()
    dataset = orm.Dataset.__table__
    assert set(DATASET_NEW_COLUMNS) <= set(dataset.columns.keys())
    assert isinstance(dataset.c.acl_mode.type, String)
    assert dataset.c.acl_mode.type.length == 16
    assert dataset.c.acl_revision.type.__class__ is Integer
    assert isinstance(dataset.c.acl_enabled_at.type, DateTime)
    assert isinstance(dataset.c.acl_enabled_by.type, String)
    assert dataset.c.acl_enabled_by.type.length == 64
    assert dataset.c.acl_mode.nullable is False
    assert dataset.c.acl_revision.nullable is False
    assert dataset.c.acl_enabled_at.nullable is True
    assert dataset.c.acl_enabled_by.nullable is True
    assert dataset.c.acl_mode.default is not None
    assert dataset.c.acl_mode.default.arg == "tenant_role"
    assert dataset.c.acl_revision.default is not None
    assert dataset.c.acl_revision.default.arg == 1
    dataset_checks = _orm_checks(dataset)
    for name, fragments in DATASET_CHECKS.items():
        assert name in dataset_checks
        assert all(fragment.casefold() in dataset_checks[name] for fragment in fragments)

    model = getattr(orm, "DatasetAclMutationRequest", None)
    assert model is not None, "models.orm.DatasetAclMutationRequest is missing"
    assert TABLE_NAME in orm.Base.metadata.tables
    assert TABLE_NAME in catalog_schema.HEAD_CATALOG_TABLES
    table = model.__table__
    assert tuple(column.name for column in table.columns) == EXPECTED_COLUMNS
    assert _orm_uniques(table) == EXPECTED_UNIQUES
    assert _orm_foreign_keys(table) == EXPECTED_FOREIGN_KEYS
    checks = _orm_checks(table)
    assert set(checks) == set(EXPECTED_CHECKS)
    for name, fragments in EXPECTED_CHECKS.items():
        assert all(fragment.casefold() in checks[name] for fragment in fragments)
    assert _orm_indexes(table) == EXPECTED_INDEXES

    expected_types = {
        "id": (String, 64, False),
        "tenant_id": (String, 64, False),
        "dataset_id": (String, 64, False),
        "actor_id": (String, 64, False),
        "idempotency_key": (String, 128, False),
        "request_hash": (String, 64, False),
        "operation": (String, 32, False),
        "status": (String, 16, False),
        "resource_id": (String, 64, True),
        "response_json": (JSON, None, True),
        "http_status": (Integer, None, True),
        "created_at": (DateTime, None, False),
        "completed_at": (DateTime, None, True),
    }
    for name, (expected_type, length, nullable) in expected_types.items():
        column = table.c[name]
        assert isinstance(column.type, expected_type)
        if length is not None:
            assert column.type.length == length
        assert column.nullable is nullable
    assert table.c.status.default is not None
    assert table.c.status.default.arg == "pending"

    assert catalog_schema._HEAD_REQUIRED_COLUMNS["datasets"] >= set(DATASET_NEW_COLUMNS)
    assert catalog_schema._HEAD_REQUIRED_NOT_NULL["datasets"] >= {
        "acl_mode",
        "acl_revision",
    }
    assert catalog_schema._HEAD_REQUIRED_COLUMNS[TABLE_NAME] == frozenset(EXPECTED_COLUMNS)
    assert catalog_schema._HEAD_REQUIRED_NOT_NULL[TABLE_NAME] == frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "actor_id",
            "idempotency_key",
            "request_hash",
            "operation",
            "status",
            "created_at",
        }
    )
    assert catalog_schema._HEAD_REQUIRED_UNIQUES[TABLE_NAME] == EXPECTED_UNIQUES
    assert catalog_schema._HEAD_REQUIRED_FOREIGN_KEYS[TABLE_NAME] == EXPECTED_FOREIGN_KEYS
    assert catalog_schema._HEAD_REQUIRED_CHECK_FRAGMENTS[TABLE_NAME] == EXPECTED_CHECKS
    assert catalog_schema._HEAD_REQUIRED_INDEXES[TABLE_NAME] == EXPECTED_INDEXES


def test_0019_upgrade_creates_dataset_acl_control_contract(tmp_path: Path) -> None:
    url, config = _upgrade_to_0018_with_scope(tmp_path)
    _insert_access_grant(
        url,
        grant_id="grant-revoked",
        subject_type="account",
        subject_id="account-1",
        status="revoked",
        created_at=datetime(2026, 8, 26, 11, 55, 0),
    )
    command.upgrade(config, REVISION)
    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        assert TABLE_NAME in inspector.get_table_names()
        columns = {item["name"]: item for item in inspector.get_columns(TABLE_NAME)}
        assert tuple(columns) == EXPECTED_COLUMNS
        assert _uniques(inspector, TABLE_NAME) == EXPECTED_UNIQUES
        assert _foreign_keys(inspector, TABLE_NAME) == EXPECTED_FOREIGN_KEYS
        checks = _checks(inspector, TABLE_NAME)
        assert set(checks) == set(EXPECTED_CHECKS)
        for name, fragments in EXPECTED_CHECKS.items():
            assert all(fragment.casefold() in checks[name] for fragment in fragments)
        assert _indexes(inspector, TABLE_NAME) == EXPECTED_INDEXES

        dataset_columns = {item["name"]: item for item in inspector.get_columns("datasets")}
        assert (
            tuple(name for name in DATASET_NEW_COLUMNS if name in dataset_columns)
            == DATASET_NEW_COLUMNS
        )
        assert dataset_columns["acl_mode"]["nullable"] is False
        assert dataset_columns["acl_revision"]["nullable"] is False
        assert dataset_columns["acl_enabled_at"]["nullable"] is True
        assert dataset_columns["acl_enabled_by"]["nullable"] is True
        assert dataset_columns["acl_mode"]["type"].length == 16
        assert dataset_columns["acl_revision"]["type"].python_type is int
        assert dataset_columns["acl_enabled_by"]["type"].length == 64
        dataset_checks = _checks(inspector, "datasets")
        for name, fragments in DATASET_CHECKS.items():
            assert name in dataset_checks
            assert all(fragment.casefold() in dataset_checks[name] for fragment in fragments)

        row = (
            engine.connect()
            .execute(
                text(
                    "SELECT acl_mode, acl_revision, acl_enabled_at, acl_enabled_by "
                    "FROM datasets WHERE id='dataset-1'"
                )
            )
            .one()
        )
        assert tuple(row) == ("tenant_role", 1, None, None)
    finally:
        engine.dispose()

    _insert_request(url)
    engine = create_engine(url)
    try:
        row = (
            engine.connect()
            .execute(
                text(
                    f"SELECT status, resource_id, response_json, http_status, completed_at "
                    f"FROM {TABLE_NAME} WHERE id='request-1'"
                )
            )
            .one()
        )
        assert row.status == "pending"
        assert row.resource_id == "grant-1"
        assert row.http_status == 201
        assert row.completed_at is None
    finally:
        engine.dispose()


def test_0019_backfills_active_grants_and_denies_unmatched_member(
    tmp_path: Path,
) -> None:
    url, config = _upgrade_to_0018_with_scope(tmp_path)
    _insert_same_tenant_member(url)
    earliest = datetime(2026, 8, 26, 11, 58, 0)
    _insert_access_grant(
        url,
        grant_id="grant-active-account",
        subject_type="account",
        subject_id="account-1",
        status="active",
        created_at=earliest,
    )
    _insert_access_grant(
        url,
        grant_id="grant-active-organization",
        subject_type="organization_unit",
        subject_id="unit-1",
        status="active",
        created_at=datetime(2026, 8, 26, 11, 59, 0),
    )

    command.upgrade(config, REVISION)

    assert _dataset_control_state(url) == (
        "dataset_acl",
        1,
        earliest,
        "migration:0019",
    )
    engine = create_engine(url)
    try:
        decision = evaluate_dataset_permissions(
            engine,
            "tenant-1",
            "account-unmatched",
            "member",
            "dataset-1",
        )
        assert decision.enforcement_mode == "dataset_acl"
        assert decision.effective_permissions == frozenset()
        assert decision.allows(KNOWLEDGE_READ) is False
    finally:
        engine.dispose()


@pytest.mark.parametrize("key", ["k", "k" * 128])
def test_0019_idempotency_key_length_boundaries_accept_valid_values(
    tmp_path: Path,
    key: str,
) -> None:
    url, config = _upgrade_to_0018_with_scope(tmp_path)
    command.upgrade(config, REVISION)

    _insert_request(url, request_id=f"valid-key-{len(key)}", key=key)


@pytest.mark.parametrize("key", ["", "k" * 129])
def test_0019_idempotency_key_length_boundaries_reject_invalid_values(
    tmp_path: Path,
    key: str,
) -> None:
    url, config = _upgrade_to_0018_with_scope(tmp_path)
    command.upgrade(config, REVISION)

    with pytest.raises(IntegrityError):
        _insert_request(url, request_id=f"invalid-key-{len(key)}", key=key)


def test_0019_sqlite_constraints_enforce_acl_control_contract(tmp_path: Path) -> None:
    url, config = _upgrade_to_0018_with_scope(tmp_path)
    command.upgrade(config, REVISION)

    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            _enable_sqlite_foreign_keys(connection)
            connection.execute(
                text(
                    "UPDATE datasets SET acl_mode='dataset_acl', acl_revision=2, "
                    "acl_enabled_by='account-1' WHERE id='dataset-1'"
                )
            )
            connection.execute(
                text(
                    f"INSERT INTO {TABLE_NAME} "
                    "(id, tenant_id, dataset_id, actor_id, idempotency_key, request_hash, "
                    "operation, status, created_at) VALUES "
                    "('valid-request', 'tenant-1', 'dataset-1', 'account-1', 'key-1', "
                    "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
                    "'grant.create', 'completed', :now)"
                ),
                {"now": datetime(2026, 8, 26, 12, 2, 0)},
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE datasets SET acl_mode='unsupported' WHERE id='dataset-1'")
                )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(text("UPDATE datasets SET acl_revision=0 WHERE id='dataset-1'"))
    finally:
        engine.dispose()

    engine = create_engine(url)
    try:
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                _enable_sqlite_foreign_keys(connection)
                connection.execute(
                    text(
                        f"INSERT INTO {TABLE_NAME} "
                        "(id, tenant_id, dataset_id, actor_id, idempotency_key, request_hash, "
                        "operation, status, created_at) VALUES "
                        "('duplicate', 'tenant-1', 'dataset-1', 'account-1', 'key-1', "
                        "'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', "
                        "'grant.create', 'pending', :now)"
                    ),
                    {"now": datetime(2026, 8, 26, 12, 3, 0)},
                )
    finally:
        engine.dispose()

    for invalid_status in ("running", "cancelled", ""):
        with pytest.raises(IntegrityError):
            engine = create_engine(url)
            try:
                with engine.begin() as connection:
                    _enable_sqlite_foreign_keys(connection)
                    connection.execute(
                        text(
                            f"INSERT INTO {TABLE_NAME} "
                            "(id, tenant_id, dataset_id, actor_id, idempotency_key, request_hash, "
                            "operation, status, created_at) VALUES "
                            "(:id, 'tenant-1', 'dataset-1', 'account-1', :key, :hash, "
                            "'grant.create', :status, :now)"
                        ),
                        {
                            "id": f"invalid-{invalid_status or 'empty'}",
                            "key": f"key-{invalid_status or 'empty'}",
                            "hash": "c" * 64,
                            "status": invalid_status,
                            "now": datetime(2026, 8, 26, 12, 4, 0),
                        },
                    )
            finally:
                engine.dispose()

    for parameters in (
        {"tenant_id": "tenant-2", "dataset_id": "dataset-1", "actor_id": "account-2"},
        {"tenant_id": "tenant-1", "dataset_id": "missing", "actor_id": "account-1"},
        {"tenant_id": "tenant-1", "dataset_id": "dataset-1", "actor_id": "account-2"},
    ):
        with pytest.raises(IntegrityError):
            engine = create_engine(url)
            try:
                with engine.begin() as connection:
                    _enable_sqlite_foreign_keys(connection)
                    connection.execute(
                        text(
                            f"INSERT INTO {TABLE_NAME} "
                            "(id, tenant_id, dataset_id, actor_id, idempotency_key, request_hash, "
                            "operation, status, created_at) VALUES "
                            "(:id, :tenant_id, :dataset_id, :actor_id, :key, :hash, "
                            "'grant.create', 'pending', :now)"
                        ),
                        {
                            **parameters,
                            "id": f"fk-{parameters['tenant_id']}-{parameters['dataset_id']}-{parameters['actor_id']}",
                            "key": f"fk-key-{parameters['dataset_id']}-{parameters['actor_id']}",
                            "hash": "d" * 64,
                            "now": datetime(2026, 8, 26, 12, 5, 0),
                        },
                    )
            finally:
                engine.dispose()


def test_0018_to_0019_to_0018_to_0019_sqlite_roundtrip(tmp_path: Path) -> None:
    url, config = _upgrade_to_0018_with_scope(tmp_path)
    enabled_at = datetime(2026, 8, 26, 11, 58, 0)
    _insert_access_grant(
        url,
        grant_id="grant-roundtrip-active",
        subject_type="account",
        subject_id="account-1",
        status="active",
        created_at=enabled_at,
    )
    command.upgrade(config, REVISION)
    assert _dataset_control_state(url) == (
        "dataset_acl",
        1,
        enabled_at,
        "migration:0019",
    )
    _insert_request(url)

    command.downgrade(config, DOWN_REVISION)
    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        assert TABLE_NAME not in inspector.get_table_names()
        dataset_columns = {item["name"] for item in inspector.get_columns("datasets")}
        assert not (set(DATASET_NEW_COLUMNS) & dataset_columns)
        state = catalog_schema.inspect_catalog_schema(engine)
        assert state.revision == DOWN_REVISION
        assert state.status == "behind"
    finally:
        engine.dispose()

    command.upgrade(config, REVISION)
    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        assert TABLE_NAME in inspector.get_table_names()
        row = (
            engine.connect()
            .execute(
                text(
                    "SELECT acl_mode, acl_revision, acl_enabled_at, acl_enabled_by "
                    "FROM datasets WHERE id='dataset-1'"
                )
            )
            .one()
        )
        assert tuple(row[:2]) == ("dataset_acl", 1)
        assert row[3] == "migration:0019"
        assert catalog_schema.inspect_catalog_schema(engine).status == "behind"
    finally:
        engine.dispose()

    _insert_request(url, request_id="request-after-roundtrip", key="roundtrip-key")


def test_0019_mysql_offline_ddl_compiles() -> None:
    output = StringIO()
    config = _alembic_config("mysql+pymysql://user:pass@localhost/rag4c")
    config.output_buffer = output

    command.upgrade(
        config,
        f"{DOWN_REVISION}:{REVISION}",
        sql=True,
    )

    ddl = output.getvalue().upper()
    assert "ALTER TABLE DATASETS" in ddl
    assert "ACL_MODE" in ddl
    assert "ACL_REVISION" in ddl
    assert "ACL_ENABLED_AT" in ddl
    assert "ACL_ENABLED_BY" in ddl
    assert f"CREATE TABLE {TABLE_NAME.upper()}" in ddl
    assert "VARCHAR(64)" in ddl
    assert "VARCHAR(128)" in ddl
    assert "VARCHAR(32)" in ddl
    assert "DATETIME(6)" in ddl
    assert "JSON" in ddl
    assert "STATUS IN ('PENDING', 'COMPLETED', 'FAILED')" in ddl
    assert "ACL_MODE IN ('TENANT_ROLE', 'DATASET_ACL')" in ddl
    assert "ACL_REVISION > 0" in ddl
    assert "LENGTH(IDEMPOTENCY_KEY) BETWEEN 1 AND 128" in ddl
    assert "UPDATE DATASETS" in ddl
    assert "DATASET_ACCESS_GRANTS" in ddl
    assert "STATUS='ACTIVE'" in ddl
    assert "MIGRATION:0019" in ddl
    assert "FOREIGN KEY(TENANT_ID, DATASET_ID)" in ddl
    assert "REFERENCES DATASETS (TENANT_ID, ID)" in ddl
    assert "FOREIGN KEY(ACTOR_ID, TENANT_ID)" in ddl
    assert "REFERENCES TENANT_MEMBERS (ACCOUNT_ID, TENANT_ID)" in ddl
    for name in (
        *EXPECTED_UNIQUES,
        *EXPECTED_FOREIGN_KEYS,
        *EXPECTED_CHECKS,
        *EXPECTED_INDEXES,
        *DATASET_CHECKS,
    ):
        assert name.upper() in ddl


def test_0019_mysql_datetime6_helper_is_dialect_aware() -> None:
    migration = _migration_module()
    sqlite_datetime = migration._datetime6().dialect_impl(sqlite.dialect())
    mysql_datetime = migration._datetime6().dialect_impl(mysql.dialect())
    assert isinstance(sqlite_datetime, DateTime)
    assert isinstance(mysql_datetime, mysql.DATETIME)
    assert mysql_datetime.fsp == 6
