from __future__ import annotations

import importlib
import json
from datetime import datetime
from io import StringIO
from pathlib import Path
from types import ModuleType

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from core import catalog_schema
from core.catalog_schema import _alembic_config
import models.orm as orm


def _migration_module() -> ModuleType:
    try:
        return importlib.import_module("catalog_migrations.versions.0016_enterprise_membership")
    except ModuleNotFoundError:
        pytest.fail("0016 enterprise membership migration is missing")


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _indexes(engine, table: str) -> dict[str, tuple[str, ...]]:
    return {
        str(item["name"]): tuple(item.get("column_names") or ())
        for item in inspect(engine).get_indexes(table)
        if item.get("name")
    }


def _constraints(engine, table: str) -> dict[str, str]:
    return {
        str(item.get("name")): str(item.get("sqltext") or "")
        for item in inspect(engine).get_check_constraints(table)
        if item.get("name")
    }


def _create_preflight_database(
    tmp_path: Path,
    *,
    tenants: tuple[tuple[str, str], ...],
    accounts: tuple[str, ...],
    members: tuple[tuple[int, str, str, str | None], ...],
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    engine = create_engine(_sqlite_url(tmp_path / "preflight.db"))
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE accounts (id VARCHAR(64) PRIMARY KEY)")
        connection.exec_driver_sql(
            "CREATE TABLE tenants (id VARCHAR(64) PRIMARY KEY, status VARCHAR(16) NOT NULL)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE tenant_members ("
            "id INTEGER PRIMARY KEY, account_id VARCHAR(64), tenant_id VARCHAR(64), "
            "role VARCHAR(16))"
        )
        for account_id in accounts:
            connection.execute(
                text("INSERT INTO accounts (id) VALUES (:id)"),
                {"id": account_id},
            )
        for tenant_id, status in tenants:
            connection.execute(
                text("INSERT INTO tenants (id, status) VALUES (:id, :status)"),
                {"id": tenant_id, "status": status},
            )
        for row_id, account_id, tenant_id, role in members:
            connection.execute(
                text(
                    "INSERT INTO tenant_members (id, account_id, tenant_id, role) "
                    "VALUES (:id, :account_id, :tenant_id, :role)"
                ),
                {
                    "id": row_id,
                    "account_id": account_id,
                    "tenant_id": tenant_id,
                    "role": role,
                },
            )
    return engine


def _seed_valid_0015(url: str, *, tenant_id: str = "tenant-1") -> None:
    command.upgrade(_alembic_config(url), "0015_document_catalog_indexes")
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenants "
                    "(id, name, plan, status, quota_documents, quota_chunks, "
                    "doc_count, chunk_count, created_at) VALUES "
                    "(:id, :name, 'enterprise', 'active', 1000, 100000, 0, 0, :created_at)"
                ),
                {
                    "id": tenant_id,
                    "name": "Tenant",
                    "created_at": datetime(2026, 8, 26, 10, 0, 0),
                },
            )
            connection.execute(
                text(
                    "INSERT INTO accounts (id, name, email, created_at) "
                    "VALUES ('owner-1', 'Owner', 'owner@example.test', :created_at)"
                ),
                {"created_at": datetime(2026, 8, 26, 10, 0, 0)},
            )
            connection.execute(
                text(
                    "INSERT INTO tenant_members "
                    "(account_id, tenant_id, role, created_at) "
                    "VALUES ('owner-1', :tenant_id, 'owner', :created_at)"
                ),
                {
                    "tenant_id": tenant_id,
                    "created_at": datetime(2026, 8, 26, 10, 0, 0),
                },
            )
    finally:
        engine.dispose()


def test_0016_precedes_application_head_and_follows_0015() -> None:
    migration = _migration_module()
    scripts = ScriptDirectory.from_config(_alembic_config("sqlite://"))

    assert migration.revision == "0016_enterprise_membership"
    assert migration.down_revision == "0015_document_catalog_indexes"
    assert scripts.get_current_head() == "0029_enterprise_knowledge_base_releases"
    assert catalog_schema.HEAD_REVISION == "0029_enterprise_knowledge_base_releases"


def test_tenant_member_and_audit_models_declare_enterprise_contract() -> None:
    migration = _migration_module()
    tenant_member = orm.TenantMember
    audit_event = getattr(orm, "TenantAuditEvent", None)

    assert audit_event is not None
    assert {
        "status",
        "revision",
        "updated_at",
        "updated_by",
        "suspended_at",
        "suspended_by",
    } <= set(tenant_member.__table__.columns.keys())
    assert {
        "uq_tenant_members_account_tenant",
        "ck_tenant_members_role",
        "ck_tenant_members_status",
        "ck_tenant_members_revision_positive",
    } <= {str(constraint.name) for constraint in tenant_member.__table__.constraints}
    assert {
        "ix_tenant_members_tenant_status_role_id": (
            "tenant_id",
            "status",
            "role",
            "id",
        ),
        "ix_tenant_members_tenant_account_status": (
            "tenant_id",
            "account_id",
            "status",
        ),
    }.items() <= {
        str(index.name): tuple(column.name for column in index.columns)
        for index in tenant_member.__table__.indexes
    }.items()

    assert audit_event.__tablename__ == "tenant_audit_events"
    assert {
        "sequence",
        "id",
        "tenant_id",
        "actor_id",
        "actor_name_snapshot",
        "actor_email_snapshot",
        "action",
        "resource_type",
        "resource_id",
        "target_account_id",
        "before_snapshot",
        "after_snapshot",
        "request_id",
        "request_ip",
        "occurred_at",
    } <= set(audit_event.__table__.columns.keys())
    assert {
        "uq_tenant_audit_events_id",
        "fk_tenant_audit_events_tenant",
    } <= {str(constraint.name) for constraint in audit_event.__table__.constraints}
    assert migration.ENTERPRISE_AUDIT_INDEX_SPECS


def test_0016_manifest_covers_membership_and_tenant_audit_contract() -> None:
    assert "tenant_audit_events" in catalog_schema.HEAD_CATALOG_TABLES
    assert {
        "status",
        "revision",
        "updated_at",
        "updated_by",
        "suspended_at",
        "suspended_by",
    } <= catalog_schema._HEAD_REQUIRED_COLUMNS["tenant_members"]
    assert {
        "sequence",
        "id",
        "tenant_id",
        "actor_id",
        "actor_name_snapshot",
        "actor_email_snapshot",
        "action",
        "resource_type",
        "resource_id",
        "target_account_id",
        "before_snapshot",
        "after_snapshot",
        "request_id",
        "request_ip",
        "occurred_at",
    } <= catalog_schema._HEAD_REQUIRED_COLUMNS["tenant_audit_events"]
    assert catalog_schema._HEAD_REQUIRED_UNIQUES["tenant_audit_events"][
        "uq_tenant_audit_events_id"
    ] == ("id",)
    assert catalog_schema._HEAD_REQUIRED_FOREIGN_KEYS["tenant_audit_events"][
        "fk_tenant_audit_events_tenant"
    ] == (("tenant_id",), "tenants", ("id",))
    assert catalog_schema._HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_members"][
        "ck_tenant_members_role"
    ] == ("owner", "admin", "editor", "member")
    assert catalog_schema._HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_members"][
        "ck_tenant_members_status"
    ] == ("active", "suspended")


def test_0016_preflight_rejects_unknown_or_empty_roles(tmp_path: Path) -> None:
    migration = _migration_module()
    engine = _create_preflight_database(
        tmp_path,
        tenants=(("tenant-1", "active"),),
        accounts=("owner-1", "member-1"),
        members=(
            (1, "owner-1", "tenant-1", "owner"),
            (2, "member-1", "tenant-1", "contractor"),
        ),
    )
    try:
        with engine.connect() as connection:
            with pytest.raises(migration.EnterpriseMembershipPreflightError, match="role"):
                migration._validate_tenant_membership_preflight(connection)
    finally:
        engine.dispose()

    engine = _create_preflight_database(
        tmp_path / "empty-role",
        tenants=(("tenant-1", "active"),),
        accounts=("owner-1", "member-1"),
        members=(
            (1, "owner-1", "tenant-1", "owner"),
            (2, "member-1", "tenant-1", ""),
        ),
    )
    try:
        with engine.connect() as connection:
            with pytest.raises(migration.EnterpriseMembershipPreflightError, match="role"):
                migration._validate_tenant_membership_preflight(connection)
    finally:
        engine.dispose()


def test_0016_preflight_rejects_duplicate_members(tmp_path: Path) -> None:
    migration = _migration_module()
    engine = _create_preflight_database(
        tmp_path,
        tenants=(("tenant-1", "active"),),
        accounts=("owner-1", "member-1"),
        members=(
            (1, "owner-1", "tenant-1", "owner"),
            (2, "member-1", "tenant-1", "member"),
            (3, "member-1", "tenant-1", "member"),
        ),
    )
    try:
        with engine.connect() as connection:
            with pytest.raises(migration.EnterpriseMembershipPreflightError, match="duplicate"):
                migration._validate_tenant_membership_preflight(connection)
    finally:
        engine.dispose()


def test_0016_preflight_rejects_orphan_member_relations(tmp_path: Path) -> None:
    migration = _migration_module()
    engine = _create_preflight_database(
        tmp_path,
        tenants=(("tenant-1", "active"),),
        accounts=("owner-1",),
        members=(
            (1, "owner-1", "tenant-1", "owner"),
            (2, "missing-account", "tenant-1", "member"),
            (3, "owner-1", "missing-tenant", "member"),
        ),
    )
    try:
        with engine.connect() as connection:
            with pytest.raises(migration.EnterpriseMembershipPreflightError, match="orphan"):
                migration._validate_tenant_membership_preflight(connection)
    finally:
        engine.dispose()


def test_0016_preflight_rejects_active_tenant_without_owner(tmp_path: Path) -> None:
    migration = _migration_module()
    engine = _create_preflight_database(
        tmp_path,
        tenants=(("tenant-1", "active"),),
        accounts=("member-1",),
        members=((1, "member-1", "tenant-1", "member"),),
    )
    try:
        with engine.connect() as connection:
            with pytest.raises(migration.EnterpriseMembershipPreflightError, match="owner"):
                migration._validate_tenant_membership_preflight(connection)
    finally:
        engine.dispose()


def test_0016_upgrade_blocks_ownerless_live_catalog_before_schema_mutation(
    tmp_path: Path,
) -> None:
    url = _sqlite_url(tmp_path / "ownerless-live-catalog.db")
    command.upgrade(_alembic_config(url), "0015_document_catalog_indexes")
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenants "
                    "(id, name, plan, status, quota_documents, quota_chunks, "
                    "doc_count, chunk_count, created_at) VALUES "
                    "('tenant-ownerless', 'Ownerless', 'enterprise', 'active', "
                    "1000, 100000, 0, 0, :created_at)"
                ),
                {"created_at": datetime(2026, 8, 26, 10, 0, 0)},
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="owner"):
        command.upgrade(_alembic_config(url), "0016_enterprise_membership")

    engine = create_engine(url)
    try:
        assert "tenant_audit_events" not in inspect(engine).get_table_names()
        assert "status" not in {
            column["name"] for column in inspect(engine).get_columns("tenant_members")
        }
    finally:
        engine.dispose()


def test_0016_sqlite_upgrade_downgrade_reupgrade_round_trip(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path / "enterprise-membership-round-trip.db")
    _seed_valid_0015(url)
    config = _alembic_config(url)
    command.upgrade(config, "0016_enterprise_membership")

    engine = create_engine(url)
    try:
        member_columns = {
            column["name"] for column in inspect(engine).get_columns("tenant_members")
        }
        assert {
            "status",
            "revision",
            "updated_at",
            "updated_by",
            "suspended_at",
            "suspended_by",
        } <= member_columns
        assert {
            "ix_tenant_members_tenant_status_role_id": (
                "tenant_id",
                "status",
                "role",
                "id",
            ),
            "ix_tenant_members_tenant_account_status": (
                "tenant_id",
                "account_id",
                "status",
            ),
        }.items() <= _indexes(engine, "tenant_members").items()

        assert "tenant_audit_events" in inspect(engine).get_table_names()
        assert {
            "ix_tenant_audit_scope_sequence": (
                "tenant_id",
                "sequence",
            ),
            "ix_tenant_audit_scope_time": (
                "tenant_id",
                "occurred_at",
                "sequence",
            ),
            "ix_tenant_audit_actor_time": (
                "tenant_id",
                "actor_id",
                "occurred_at",
                "sequence",
            ),
            "ix_tenant_audit_resource_time": (
                "tenant_id",
                "resource_type",
                "resource_id",
                "occurred_at",
                "sequence",
            ),
            "ix_tenant_audit_target_time": (
                "tenant_id",
                "target_account_id",
                "occurred_at",
                "sequence",
            ),
            "ix_tenant_audit_request": ("tenant_id", "request_id"),
        }.items() <= _indexes(engine, "tenant_audit_events").items()
        assert {
            "ck_tenant_members_role",
            "ck_tenant_members_status",
            "ck_tenant_members_revision_positive",
        } <= _constraints(engine, "tenant_members").keys()
        assert {
            "fk_tenant_audit_events_tenant",
        } <= {
            str(item.get("name"))
            for item in inspect(engine).get_foreign_keys("tenant_audit_events")
        }

        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenant_audit_events "
                    "(id, tenant_id, actor_id, actor_name_snapshot, actor_email_snapshot, "
                    "action, resource_type, resource_id, target_account_id, "
                    "before_snapshot, after_snapshot, request_id, request_ip, occurred_at) "
                    "VALUES (:id, :tenant_id, :actor_id, :actor_name, :actor_email, "
                    ":action, :resource_type, :resource_id, :target, :before, :after, "
                    ":request_id, :request_ip, :occurred_at)"
                ),
                {
                    "id": "audit-1",
                    "tenant_id": "tenant-1",
                    "actor_id": "deleted-actor",
                    "actor_name": "Former Owner",
                    "actor_email": "former@example.test",
                    "action": "member.suspend",
                    "resource_type": "tenant_member",
                    "resource_id": "membership-1",
                    "target": "owner-1",
                    "before": json.dumps({"status": "active"}),
                    "after": json.dumps({"status": "suspended"}),
                    "request_id": "request-1",
                    "request_ip": "127.0.0.1",
                    "occurred_at": datetime(2026, 8, 26, 10, 1, 0),
                },
            )
            row = connection.execute(
                text(
                    "SELECT sequence, actor_id, actor_name_snapshot, target_account_id "
                    "FROM tenant_audit_events WHERE id = 'audit-1'"
                )
            ).one()
        assert tuple(row) == (1, "deleted-actor", "Former Owner", "owner-1")
        assert catalog_schema.inspect_catalog_schema(engine).status == "behind"
    finally:
        engine.dispose()

    command.downgrade(config, "0015_document_catalog_indexes")
    engine = create_engine(url)
    try:
        assert "tenant_audit_events" not in inspect(engine).get_table_names()
        old_member_columns = {
            column["name"] for column in inspect(engine).get_columns("tenant_members")
        }
        assert (
            not {
                "status",
                "revision",
                "updated_at",
                "updated_by",
                "suspended_at",
                "suspended_by",
            }
            & old_member_columns
        )
    finally:
        engine.dispose()

    command.upgrade(config, "0016_enterprise_membership")
    engine = create_engine(url)
    try:
        assert "tenant_audit_events" in inspect(engine).get_table_names()
        assert catalog_schema.inspect_catalog_schema(engine).status == "behind"
    finally:
        engine.dispose()


def test_0016_mysql_offline_ddl_compiles() -> None:
    _migration_module()
    output = StringIO()
    config = _alembic_config("mysql+pymysql://user:pass@localhost/rag4c")
    config.output_buffer = output

    command.upgrade(
        config,
        "0015_document_catalog_indexes:0016_enterprise_membership",
        sql=True,
    )

    ddl = output.getvalue().upper()
    assert "CREATE TABLE TENANT_AUDIT_EVENTS" in ddl
    assert "BIGINT" in ddl
    assert "AUTO_INCREMENT" in ddl
    assert "FK_TENANT_AUDIT_EVENTS_TENANT" in ddl
    assert "IX_TENANT_AUDIT_SCOPE_SEQUENCE" in ddl
    assert "IX_TENANT_AUDIT_RESOURCE_TIME" in ddl
    assert "ADD COLUMN STATUS VARCHAR(16)" in ddl
    assert "ADD COLUMN REVISION INTEGER" in ddl
    assert "UPDATED_AT DATETIME(6)" in ddl
    assert "SUSPENDED_AT DATETIME(6)" in ddl
    assert "CK_TENANT_MEMBERS_ROLE" in ddl
    assert "CK_TENANT_MEMBERS_STATUS" in ddl
    assert "CK_TENANT_MEMBERS_REVISION_POSITIVE" in ddl
