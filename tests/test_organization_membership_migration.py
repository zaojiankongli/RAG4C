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
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.exc import IntegrityError

from core import catalog_schema
from core.catalog_schema import _alembic_config
import models.orm as orm


TABLE_NAME = "tenant_organization_unit_members"
EXPECTED_COLUMNS = (
    "id",
    "tenant_id",
    "organization_unit_id",
    "account_id",
    "status",
    "revision",
    "created_at",
    "created_by",
    "updated_at",
    "updated_by",
)
EXPECTED_UNIQUES = {
    "uq_tenant_organization_unit_members_unit_account": (
        "tenant_id",
        "organization_unit_id",
        "account_id",
    ),
}
EXPECTED_FOREIGN_KEYS = {
    "fk_tenant_organization_unit_members_scope_unit": (
        ("tenant_id", "organization_unit_id"),
        "tenant_organization_units",
        ("tenant_id", "id"),
    ),
    "fk_tenant_organization_unit_members_scope_account": (
        ("account_id", "tenant_id"),
        "tenant_members",
        ("account_id", "tenant_id"),
    ),
}
EXPECTED_CHECKS = {
    "ck_tenant_organization_unit_members_status": ("active", "removed"),
    "ck_tenant_organization_unit_members_revision_positive": ("revision > 0",),
}
EXPECTED_INDEXES = {
    "ix_tenant_organization_unit_members_tenant_unit_status": (
        "tenant_id",
        "organization_unit_id",
        "status",
        "id",
    ),
    "ix_tenant_organization_unit_members_tenant_account_status": (
        "tenant_id",
        "account_id",
        "status",
        "id",
    ),
}


def _migration_module() -> ModuleType:
    try:
        return importlib.import_module("catalog_migrations.versions.0018_organization_membership")
    except ModuleNotFoundError:
        pytest.fail("0018 organization membership migration is missing")


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _indexes(inspector: Any) -> dict[str, tuple[str, ...]]:
    return {
        str(item["name"]): tuple(item.get("column_names") or ())
        for item in inspector.get_indexes(TABLE_NAME)
        if item.get("name")
    }


def _uniques(inspector: Any) -> dict[str, tuple[str, ...]]:
    return {
        str(item["name"]): tuple(item.get("column_names") or ())
        for item in inspector.get_unique_constraints(TABLE_NAME)
        if item.get("name")
    }


def _foreign_keys(inspector: Any) -> dict[str, tuple[tuple[str, ...], str, tuple[str, ...]]]:
    return {
        str(item["name"]): (
            tuple(item.get("constrained_columns") or ()),
            str(item["referred_table"]),
            tuple(item.get("referred_columns") or ()),
        )
        for item in inspector.get_foreign_keys(TABLE_NAME)
        if item.get("name")
    }


def _checks(inspector: Any) -> dict[str, str]:
    return {
        str(item["name"]): " ".join(str(item.get("sqltext") or "").casefold().split())
        for item in inspector.get_check_constraints(TABLE_NAME)
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


def _enable_sqlite_foreign_keys(connection: Any) -> None:
    connection.exec_driver_sql("PRAGMA foreign_keys=ON")


def _seed_0017_scope(url: str) -> None:
    now = datetime(2026, 8, 26, 12, 0, 0)
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            _enable_sqlite_foreign_keys(connection)
            for tenant_id, account_id in (("tenant-1", "account-1"), ("tenant-2", "account-2")):
                connection.execute(
                    text(
                        "INSERT INTO tenants "
                        "(id, name, plan, status, quota_documents, quota_chunks, "
                        "doc_count, chunk_count, created_at) VALUES "
                        "(:tenant_id, :tenant_id, 'enterprise', 'active', 1000, 100000, "
                        "0, 0, :now)"
                    ),
                    {"tenant_id": tenant_id, "now": now},
                )
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
                        "(:account_id, :tenant_id, 'member', 'active', 1, :now, :now, :account_id)"
                    ),
                    {"account_id": account_id, "tenant_id": tenant_id, "now": now},
                )
                connection.execute(
                    text(
                        "INSERT INTO tenant_organization_units "
                        "(id, tenant_id, parent_id, name, code, status, sort_order, revision, "
                        "created_at, created_by, updated_at, updated_by) VALUES "
                        "(:unit_id, :tenant_id, NULL, :name, :code, 'active', 0, 1, "
                        ":now, :account_id, :now, :account_id)"
                    ),
                    {
                        "unit_id": f"unit-{tenant_id[-1]}",
                        "tenant_id": tenant_id,
                        "name": f"Team {tenant_id}",
                        "code": f"TEAM-{tenant_id[-1]}",
                        "account_id": account_id,
                        "now": now,
                    },
                )
    finally:
        engine.dispose()


def _insert_membership(
    url: str,
    *,
    tenant_id: str = "tenant-1",
    unit_id: str = "unit-1",
    account_id: str = "account-1",
    status: str = "active",
    revision: int = 1,
) -> int:
    now = datetime(2026, 8, 26, 12, 1, 0)
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            _enable_sqlite_foreign_keys(connection)
            connection.execute(
                text(
                    "INSERT INTO tenant_organization_unit_members "
                    "(tenant_id, organization_unit_id, account_id, status, revision, "
                    "created_at, created_by, updated_at, updated_by) VALUES "
                    "(:tenant_id, :unit_id, :account_id, :status, :revision, :now, "
                    ":account_id, :now, :account_id)"
                ),
                {
                    "tenant_id": tenant_id,
                    "unit_id": unit_id,
                    "account_id": account_id,
                    "status": status,
                    "revision": revision,
                    "now": now,
                },
            )
            return int(
                connection.execute(
                    text("SELECT id FROM tenant_organization_unit_members ORDER BY id DESC LIMIT 1")
                ).scalar_one()
            )
    finally:
        engine.dispose()


def test_0018_is_the_organization_membership_head() -> None:
    migration = _migration_module()
    scripts = ScriptDirectory.from_config(_alembic_config("sqlite://"))

    assert migration.revision == "0018_organization_membership"
    assert migration.down_revision == "0017_enterprise_access_graph"
    assert scripts.get_current_head() == catalog_schema.HEAD_REVISION


def test_organization_membership_orm_and_manifest_contracts_match() -> None:
    _migration_module()
    model = getattr(orm, "TenantOrganizationUnitMember", None)
    assert model is not None, "models.orm.TenantOrganizationUnitMember is missing"
    assert TABLE_NAME in orm.Base.metadata.tables
    assert TABLE_NAME in catalog_schema.HEAD_CATALOG_TABLES

    table = model.__table__
    assert tuple(column.name for column in table.columns) == EXPECTED_COLUMNS
    assert _orm_uniques(table) == EXPECTED_UNIQUES
    assert _orm_foreign_keys(table) == EXPECTED_FOREIGN_KEYS
    assert set(_orm_checks(table)) == set(EXPECTED_CHECKS)
    for check_name, fragments in EXPECTED_CHECKS.items():
        sql = _orm_checks(table)[check_name]
        assert all(fragment in sql for fragment in fragments)
    assert _orm_indexes(table) == EXPECTED_INDEXES

    assert isinstance(table.c.id.type, Integer)
    assert isinstance(table.c.tenant_id.type, String)
    assert table.c.tenant_id.type.length == 64
    assert isinstance(table.c.organization_unit_id.type, String)
    assert table.c.organization_unit_id.type.length == 64
    assert isinstance(table.c.account_id.type, String)
    assert table.c.account_id.type.length == 64
    assert isinstance(table.c.status.type, String)
    assert table.c.status.type.length == 16
    assert isinstance(table.c.created_by.type, String)
    assert table.c.created_by.type.length == 64
    assert isinstance(table.c.updated_by.type, String)
    assert table.c.updated_by.type.length == 64

    assert catalog_schema._HEAD_REQUIRED_COLUMNS[TABLE_NAME] == frozenset(EXPECTED_COLUMNS)
    assert catalog_schema._HEAD_REQUIRED_NOT_NULL[TABLE_NAME] == frozenset(EXPECTED_COLUMNS)
    assert catalog_schema._HEAD_REQUIRED_UNIQUES[TABLE_NAME] == EXPECTED_UNIQUES
    assert catalog_schema._HEAD_REQUIRED_FOREIGN_KEYS[TABLE_NAME] == EXPECTED_FOREIGN_KEYS
    assert catalog_schema._HEAD_REQUIRED_CHECK_FRAGMENTS[TABLE_NAME] == EXPECTED_CHECKS
    assert catalog_schema._HEAD_REQUIRED_INDEXES[TABLE_NAME] == EXPECTED_INDEXES


def test_0018_upgrade_creates_contract_and_sqlite_constraints(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path / "organization-membership.db")
    config = _alembic_config(url)
    command.upgrade(config, "0017_enterprise_access_graph")
    _seed_0017_scope(url)

    command.upgrade(config, "0018_organization_membership")
    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        assert TABLE_NAME in inspector.get_table_names()
        assert (
            tuple(column["name"] for column in inspector.get_columns(TABLE_NAME))
            == EXPECTED_COLUMNS
        )
        assert _uniques(inspector) == EXPECTED_UNIQUES
        assert _foreign_keys(inspector) == EXPECTED_FOREIGN_KEYS
        checks = _checks(inspector)
        assert set(checks) == set(EXPECTED_CHECKS)
        for check_name, fragments in EXPECTED_CHECKS.items():
            assert all(fragment in checks[check_name] for fragment in fragments)
        assert _indexes(inspector) == EXPECTED_INDEXES
        columns = {column["name"]: column for column in inspector.get_columns(TABLE_NAME)}
        assert isinstance(columns["id"]["type"], Integer)
        assert columns["tenant_id"]["type"].length == 64
        assert columns["organization_unit_id"]["type"].length == 64
        assert columns["account_id"]["type"].length == 64
        assert columns["status"]["type"].length == 16
        assert columns["revision"]["type"].python_type is int
    finally:
        engine.dispose()

    member_id = _insert_membership(url)
    assert member_id == 1

    engine = create_engine(url)
    try:
        row = (
            engine.connect()
            .execute(
                text(
                    "SELECT tenant_id, organization_unit_id, account_id, status, revision "
                    "FROM tenant_organization_unit_members WHERE id=:id"
                ),
                {"id": member_id},
            )
            .one()
        )
        assert tuple(row) == ("tenant-1", "unit-1", "account-1", "active", 1)
    finally:
        engine.dispose()

    for parameters in (
        {
            "tenant_id": "tenant-1",
            "unit_id": "unit-1",
            "account_id": "account-1",
            "status": "active",
            "revision": 1,
        },
        {
            "tenant_id": "tenant-1",
            "unit_id": "unit-1",
            "account_id": "account-1",
            "status": "removed",
            "revision": 2,
        },
    ):
        engine = create_engine(url)
        try:
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    _enable_sqlite_foreign_keys(connection)
                    connection.execute(
                        text(
                            "INSERT INTO tenant_organization_unit_members "
                            "(tenant_id, organization_unit_id, account_id, status, revision, "
                            "created_at, created_by, updated_at, updated_by) VALUES "
                            "(:tenant_id, :unit_id, :account_id, :status, :revision, "
                            ":now, 'operator', :now, 'operator')"
                        ),
                        {**parameters, "now": datetime(2026, 8, 26, 12, 2, 0)},
                    )
        finally:
            engine.dispose()

    for parameters in (
        {
            "tenant_id": "tenant-1",
            "unit_id": "unit-1",
            "account_id": "account-1",
            "status": "pending",
            "revision": 1,
        },
        {
            "tenant_id": "tenant-1",
            "unit_id": "unit-1",
            "account_id": "account-1",
            "status": "active",
            "revision": 0,
        },
        {
            "tenant_id": "tenant-2",
            "unit_id": "unit-1",
            "account_id": "account-2",
            "status": "active",
            "revision": 1,
        },
        {
            "tenant_id": "tenant-1",
            "unit_id": "unit-2",
            "account_id": "account-1",
            "status": "active",
            "revision": 1,
        },
    ):
        engine = create_engine(url)
        try:
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    _enable_sqlite_foreign_keys(connection)
                    connection.execute(
                        text(
                            "INSERT INTO tenant_organization_unit_members "
                            "(tenant_id, organization_unit_id, account_id, status, revision, "
                            "created_at, created_by, updated_at, updated_by) VALUES "
                            "(:tenant_id, :unit_id, :account_id, :status, :revision, "
                            ":now, 'operator', :now, 'operator')"
                        ),
                        {**parameters, "now": datetime(2026, 8, 26, 12, 3, 0)},
                    )
        finally:
            engine.dispose()


def test_0017_to_0018_to_0017_to_0018_sqlite_roundtrip(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path / "organization-membership-roundtrip.db")
    config = _alembic_config(url)

    command.upgrade(config, "0017_enterprise_access_graph")
    _seed_0017_scope(url)
    command.upgrade(config, "0018_organization_membership")
    first_id = _insert_membership(url)
    assert first_id == 1

    command.downgrade(config, "0017_enterprise_access_graph")
    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        assert TABLE_NAME not in inspector.get_table_names()
        state = catalog_schema.inspect_catalog_schema(engine)
        assert state.revision == "0017_enterprise_access_graph"
        assert state.status == "behind"
    finally:
        engine.dispose()

    command.upgrade(config, "0018_organization_membership")
    second_id = _insert_membership(url)
    assert second_id == 1
    engine = create_engine(url)
    try:
        assert catalog_schema.inspect_catalog_schema(engine).status == "behind"
        assert TABLE_NAME in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_0018_mysql_offline_ddl_compiles() -> None:
    output = StringIO()
    config = _alembic_config("mysql+pymysql://user:pass@localhost/rag4c")
    config.output_buffer = output

    command.upgrade(
        config,
        "0017_enterprise_access_graph:0018_organization_membership",
        sql=True,
    )

    ddl = output.getvalue().upper()
    assert f"CREATE TABLE {TABLE_NAME.upper()}" in ddl
    assert "AUTO_INCREMENT" in ddl
    assert "VARCHAR(64)" in ddl
    assert "DATETIME(6)" in ddl
    assert "STATUS IN ('ACTIVE', 'REMOVED')" in ddl
    assert "REVISION > 0" in ddl
    for name in (*EXPECTED_UNIQUES, *EXPECTED_FOREIGN_KEYS, *EXPECTED_CHECKS, *EXPECTED_INDEXES):
        assert name.upper() in ddl
    assert "FOREIGN KEY(TENANT_ID, ORGANIZATION_UNIT_ID)" in ddl
    assert "REFERENCES TENANT_ORGANIZATION_UNITS (TENANT_ID, ID)" in ddl
    assert "FOREIGN KEY(ACCOUNT_ID, TENANT_ID)" in ddl
    assert "REFERENCES TENANT_MEMBERS (ACCOUNT_ID, TENANT_ID)" in ddl
