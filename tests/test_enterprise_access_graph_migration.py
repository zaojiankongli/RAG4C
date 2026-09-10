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


ACCESS_GRAPH_TABLES = {
    "tenant_organization_units",
    "tenant_groups",
    "tenant_group_members",
    "dataset_access_grants",
    "tenant_invitations",
}

EXPECTED_COLUMNS: dict[str, tuple[str, ...]] = {
    "tenant_organization_units": (
        "id",
        "tenant_id",
        "parent_id",
        "name",
        "code",
        "status",
        "sort_order",
        "revision",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
    ),
    "tenant_groups": (
        "id",
        "tenant_id",
        "name",
        "normalized_name",
        "description",
        "status",
        "revision",
        "created_at",
        "updated_at",
    ),
    "tenant_group_members": (
        "id",
        "tenant_id",
        "group_id",
        "account_id",
        "status",
        "created_at",
        "created_by",
    ),
    "dataset_access_grants": (
        "id",
        "tenant_id",
        "dataset_id",
        "subject_type",
        "subject_id",
        "role",
        "status",
        "revision",
        "created_at",
        "updated_at",
    ),
    "tenant_invitations": (
        "id",
        "tenant_id",
        "email",
        "normalized_email",
        "role",
        "status",
        "token_hash",
        "expires_at",
        "accepted_at",
        "accepted_by",
        "invited_by",
        "revision",
        "created_at",
        "updated_at",
    ),
}

EXPECTED_UNIQUES: dict[str, dict[str, tuple[str, ...]]] = {
    "tenant_organization_units": {
        "uq_tenant_organization_units_scope_id": ("tenant_id", "id"),
        "uq_tenant_organization_units_tenant_code": ("tenant_id", "code"),
        "uq_tenant_organization_units_tenant_parent_name": (
            "tenant_id",
            "parent_id",
            "name",
        ),
    },
    "tenant_groups": {
        "uq_tenant_groups_scope_id": ("tenant_id", "id"),
        "uq_tenant_groups_tenant_normalized_name": (
            "tenant_id",
            "normalized_name",
        ),
    },
    "tenant_group_members": {
        "uq_tenant_group_members_group_account": (
            "tenant_id",
            "group_id",
            "account_id",
        ),
    },
    "dataset_access_grants": {
        "uq_dataset_access_grants_dataset_subject": (
            "tenant_id",
            "dataset_id",
            "subject_type",
            "subject_id",
        ),
    },
    "tenant_invitations": {},
}

EXPECTED_FOREIGN_KEYS: dict[
    str,
    dict[str, tuple[tuple[str, ...], str, tuple[str, ...]]],
] = {
    "tenant_organization_units": {
        "fk_tenant_organization_units_tenant": (
            ("tenant_id",),
            "tenants",
            ("id",),
        ),
        "fk_tenant_organization_units_scope_parent": (
            ("tenant_id", "parent_id"),
            "tenant_organization_units",
            ("tenant_id", "id"),
        ),
    },
    "tenant_groups": {
        "fk_tenant_groups_tenant": (("tenant_id",), "tenants", ("id",)),
    },
    "tenant_group_members": {
        "fk_tenant_group_members_scope_group": (
            ("tenant_id", "group_id"),
            "tenant_groups",
            ("tenant_id", "id"),
        ),
        "fk_tenant_group_members_scope_account": (
            ("account_id", "tenant_id"),
            "tenant_members",
            ("account_id", "tenant_id"),
        ),
    },
    "dataset_access_grants": {
        "fk_dataset_access_grants_scope_dataset": (
            ("tenant_id", "dataset_id"),
            "datasets",
            ("tenant_id", "id"),
        ),
    },
    "tenant_invitations": {
        "fk_tenant_invitations_tenant": (("tenant_id",), "tenants", ("id",)),
        "fk_tenant_invitations_scope_inviter": (
            ("invited_by", "tenant_id"),
            "tenant_members",
            ("account_id", "tenant_id"),
        ),
        "fk_tenant_invitations_scope_acceptor": (
            ("accepted_by", "tenant_id"),
            "tenant_members",
            ("account_id", "tenant_id"),
        ),
    },
}

EXPECTED_CHECKS: dict[str, dict[str, tuple[str, ...]]] = {
    "tenant_organization_units": {
        "ck_tenant_organization_units_status": ("active", "archived"),
        "ck_tenant_organization_units_revision_positive": ("revision > 0",),
    },
    "tenant_groups": {
        "ck_tenant_groups_status": ("active", "archived"),
        "ck_tenant_groups_revision_positive": ("revision > 0",),
    },
    "tenant_group_members": {
        "ck_tenant_group_members_status": ("active", "removed"),
    },
    "dataset_access_grants": {
        "ck_dataset_access_grants_subject_type": ("account", "group", "organization_unit"),
        "ck_dataset_access_grants_role": ("viewer", "editor", "manager"),
        "ck_dataset_access_grants_status": ("active", "revoked"),
        "ck_dataset_access_grants_revision_positive": ("revision > 0",),
    },
    "tenant_invitations": {
        "ck_tenant_invitations_role": ("owner", "admin", "editor", "member"),
        "ck_tenant_invitations_status": (
            "pending",
            "accepted",
            "revoked",
            "expired",
        ),
        "ck_tenant_invitations_revision_positive": ("revision > 0",),
    },
}

EXPECTED_INDEXES: dict[str, dict[str, tuple[str, ...]]] = {
    "tenant_organization_units": {
        "ix_tenant_organization_units_tenant_parent_status": (
            "tenant_id",
            "parent_id",
            "status",
            "sort_order",
            "id",
        ),
        "ix_tenant_organization_units_tenant_status": (
            "tenant_id",
            "status",
            "sort_order",
            "id",
        ),
    },
    "tenant_groups": {
        "ix_tenant_groups_tenant_status_name": (
            "tenant_id",
            "status",
            "normalized_name",
            "id",
        ),
    },
    "tenant_group_members": {
        "ix_tenant_group_members_tenant_account": (
            "tenant_id",
            "account_id",
            "id",
        ),
        "ix_tenant_group_members_tenant_group": (
            "tenant_id",
            "group_id",
            "id",
        ),
    },
    "dataset_access_grants": {
        "ix_dataset_access_grants_tenant_dataset_status": (
            "tenant_id",
            "dataset_id",
            "status",
            "id",
        ),
        "ix_dataset_access_grants_tenant_subject": (
            "tenant_id",
            "subject_type",
            "subject_id",
            "id",
        ),
    },
    "tenant_invitations": {
        "ix_tenant_invitations_tenant_status_expires": (
            "tenant_id",
            "status",
            "expires_at",
            "id",
        ),
        "ix_tenant_invitations_tenant_email": (
            "tenant_id",
            "normalized_email",
            "id",
        ),
    },
}

ORM_CLASSES = {
    "tenant_organization_units": "TenantOrganizationUnit",
    "tenant_groups": "TenantGroup",
    "tenant_group_members": "TenantGroupMember",
    "dataset_access_grants": "DatasetAccessGrant",
    "tenant_invitations": "TenantInvitation",
}


def _migration_module() -> ModuleType:
    try:
        return importlib.import_module("catalog_migrations.versions.0017_enterprise_access_graph")
    except ModuleNotFoundError:
        pytest.fail("0017 enterprise access graph migration is missing")


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _indexes(inspector: Any, table: str) -> dict[str, tuple[str, ...]]:
    return {
        str(item.get("name")): tuple(item.get("column_names") or ())
        for item in inspector.get_indexes(table)
        if item.get("name")
    }


def _uniques(inspector: Any, table: str) -> dict[str, tuple[str, ...]]:
    return {
        str(item.get("name")): tuple(item.get("column_names") or ())
        for item in inspector.get_unique_constraints(table)
        if item.get("name")
    }


def _foreign_keys(
    inspector: Any,
    table: str,
) -> dict[str, tuple[tuple[str, ...], str, tuple[str, ...]]]:
    return {
        str(item.get("name")): (
            tuple(item.get("constrained_columns") or ()),
            str(item.get("referred_table")),
            tuple(item.get("referred_columns") or ()),
        )
        for item in inspector.get_foreign_keys(table)
        if item.get("name")
    }


def _checks(inspector: Any, table: str) -> dict[str, str]:
    return {
        str(item.get("name")): " ".join(str(item.get("sqltext") or "").casefold().split())
        for item in inspector.get_check_constraints(table)
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
        if item.name and str(item.name) in EXPECTED_INDEXES[table.name]
    }


def _enable_sqlite_foreign_keys(connection: Any) -> None:
    connection.exec_driver_sql("PRAGMA foreign_keys=ON")


def _seed_0016_authority(url: str) -> None:
    engine = create_engine(url)
    now = datetime(2026, 8, 26, 10, 0, 0)
    try:
        with engine.begin() as connection:
            _enable_sqlite_foreign_keys(connection)
            for tenant_id, owner_id, email in (
                ("tenant-1", "owner-1", "owner-1@example.test"),
                ("tenant-2", "owner-2", "owner-2@example.test"),
            ):
                connection.execute(
                    text(
                        "INSERT INTO tenants "
                        "(id, name, plan, status, quota_documents, quota_chunks, "
                        "doc_count, chunk_count, created_at) VALUES "
                        "(:id, :name, 'enterprise', 'active', 1000, 100000, 0, 0, :created_at)"
                    ),
                    {"id": tenant_id, "name": tenant_id, "created_at": now},
                )
                connection.execute(
                    text(
                        "INSERT INTO accounts (id, name, email, created_at) "
                        "VALUES (:id, :name, :email, :created_at)"
                    ),
                    {
                        "id": owner_id,
                        "name": owner_id,
                        "email": email,
                        "created_at": now,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO tenant_members "
                        "(account_id, tenant_id, role, status, revision, created_at, "
                        "updated_at, updated_by) VALUES "
                        "(:account_id, :tenant_id, 'owner', 'active', 1, :created_at, "
                        ":updated_at, :updated_by)"
                    ),
                    {
                        "account_id": owner_id,
                        "tenant_id": tenant_id,
                        "created_at": now,
                        "updated_at": now,
                        "updated_by": owner_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO datasets "
                        "(id, tenant_id, name, description, status, profile_revision, "
                        "owner_id, visibility, profile_json, parser_policy, chunk_policy, "
                        "retrieval_policy, retention_policy, metadata_policy, default_language, "
                        "graph_enabled, qa_enabled, mutation_generation, serving_generation, "
                        "doc_count, chunk_count, created_at, updated_at) VALUES "
                        "(:id, :tenant_id, :name, '', 'active', 1, :owner_id, 'private', "
                        "'{}', '{}', '{}', '{}', '{}', '{}', 'zh-CN', 0, 1, 0, 0, 0, 0, "
                        ":created_at, :updated_at)"
                    ),
                    {
                        "id": f"dataset-{tenant_id[-1]}",
                        "tenant_id": tenant_id,
                        "name": f"Dataset {tenant_id}",
                        "owner_id": owner_id,
                        "created_at": now,
                        "updated_at": now,
                    },
                )
    finally:
        engine.dispose()


def _assert_integrity_error(url: str, statement: str, parameters: dict[str, object]) -> None:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            _enable_sqlite_foreign_keys(connection)
            connection.commit()
            with pytest.raises(IntegrityError):
                with connection.begin():
                    connection.execute(text(statement), parameters)
    finally:
        engine.dispose()


def test_0017_precedes_the_current_application_head() -> None:
    migration = _migration_module()
    scripts = ScriptDirectory.from_config(_alembic_config("sqlite://"))

    assert migration.revision == "0017_enterprise_access_graph"
    assert migration.down_revision == "0016_enterprise_membership"
    assert scripts.get_current_head() == catalog_schema.HEAD_REVISION


def test_access_graph_orm_and_manifest_contracts_match() -> None:
    _migration_module()

    assert ACCESS_GRAPH_TABLES <= set(orm.Base.metadata.tables)
    assert ACCESS_GRAPH_TABLES <= set(catalog_schema.HEAD_CATALOG_TABLES)

    for table_name, class_name in ORM_CLASSES.items():
        model = getattr(orm, class_name, None)
        assert model is not None, f"models.orm.{class_name} is missing"
        table = model.__table__

        if table_name == "tenant_invitations":
            assert set(EXPECTED_COLUMNS[table_name]) <= set(table.columns.keys())
            assert EXPECTED_UNIQUES[table_name].items() <= _orm_uniques(table).items()
            assert EXPECTED_FOREIGN_KEYS[table_name].items() <= _orm_foreign_keys(table).items()
            assert set(EXPECTED_CHECKS[table_name]) <= set(_orm_checks(table))
        else:
            assert tuple(column.name for column in table.columns) == EXPECTED_COLUMNS[table_name]
            assert _orm_uniques(table) == EXPECTED_UNIQUES[table_name]
            assert _orm_foreign_keys(table) == EXPECTED_FOREIGN_KEYS[table_name]
            assert set(_orm_checks(table)) == set(EXPECTED_CHECKS[table_name])
        for check_name, fragments in EXPECTED_CHECKS[table_name].items():
            sql = _orm_checks(table)[check_name]
            assert all(fragment in sql for fragment in fragments)
        assert _orm_indexes(table) == EXPECTED_INDEXES[table_name]

        if table_name == "tenant_group_members":
            assert isinstance(table.c.id.type, Integer)
        else:
            assert isinstance(table.c.id.type, String)
            assert table.c.id.type.length == 64
        if table_name == "tenant_organization_units":
            assert isinstance(table.c.parent_id.type, String)
            assert table.c.parent_id.type.length == 64
        if table_name == "tenant_group_members":
            assert isinstance(table.c.group_id.type, String)
            assert table.c.group_id.type.length == 64
        if table_name == "dataset_access_grants":
            assert isinstance(table.c.subject_id.type, String)
            assert table.c.subject_id.type.length == 64

        if table_name == "tenant_invitations":
            assert set(EXPECTED_COLUMNS[table_name]) <= set(
                catalog_schema._HEAD_REQUIRED_COLUMNS[table_name]
            )
            assert (
                EXPECTED_UNIQUES[table_name].items()
                <= catalog_schema._HEAD_REQUIRED_UNIQUES[table_name].items()
            )
            assert (
                EXPECTED_FOREIGN_KEYS[table_name].items()
                <= catalog_schema._HEAD_REQUIRED_FOREIGN_KEYS[table_name].items()
            )
            assert (
                EXPECTED_CHECKS[table_name].items()
                <= catalog_schema._HEAD_REQUIRED_CHECK_FRAGMENTS[table_name].items()
            )
        else:
            assert catalog_schema._HEAD_REQUIRED_COLUMNS[table_name] == frozenset(
                EXPECTED_COLUMNS[table_name]
            )
            assert catalog_schema._HEAD_REQUIRED_UNIQUES[table_name] == EXPECTED_UNIQUES[table_name]
            assert (
                catalog_schema._HEAD_REQUIRED_FOREIGN_KEYS[table_name]
                == EXPECTED_FOREIGN_KEYS[table_name]
            )
            assert (
                catalog_schema._HEAD_REQUIRED_CHECK_FRAGMENTS[table_name]
                == EXPECTED_CHECKS[table_name]
            )
        if table_name == "tenant_invitations":
            assert (
                EXPECTED_INDEXES[table_name].items()
                <= catalog_schema._HEAD_REQUIRED_INDEXES[table_name].items()
            )
        else:
            assert catalog_schema._HEAD_REQUIRED_INDEXES[table_name] == EXPECTED_INDEXES[table_name]

    assert "token_hash" in EXPECTED_COLUMNS["tenant_invitations"]
    assert "token" not in EXPECTED_COLUMNS["tenant_invitations"]
    assert "raw_token" not in EXPECTED_COLUMNS["tenant_invitations"]


def test_access_graph_python_defaults_fill_required_identity_fields(tmp_path: Path) -> None:
    models = {
        table_name: getattr(orm, class_name, None) for table_name, class_name in ORM_CLASSES.items()
    }
    assert all(models.values()), "0017 access graph ORM models are missing"

    url = _sqlite_url(tmp_path / "enterprise-access-graph-defaults.db")
    engine = create_engine(url)
    try:
        orm.Base.metadata.create_all(engine)
        now = datetime(2026, 8, 26, 9, 0, 0)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenants "
                    "(id, name, plan, status, quota_documents, quota_chunks, doc_count, "
                    "chunk_count, created_at) VALUES "
                    "('tenant-defaults', 'Defaults', 'enterprise', 'active', 1000, 100000, "
                    "0, 0, :now)"
                ),
                {"now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO accounts (id, name, email, created_at) VALUES "
                    "('owner-defaults', 'Owner', 'owner-defaults@example.test', :now)"
                ),
                {"now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO tenant_members "
                    "(account_id, tenant_id, role, status, revision, created_at, updated_at, "
                    "updated_by) VALUES "
                    "('owner-defaults', 'tenant-defaults', 'owner', 'active', 1, :now, :now, "
                    "'owner-defaults')"
                ),
                {"now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO datasets "
                    "(id, tenant_id, name, description, status, profile_revision, owner_id, "
                    "visibility, profile_json, parser_policy, chunk_policy, retrieval_policy, "
                    "retention_policy, metadata_policy, default_language, graph_enabled, "
                    "qa_enabled, mutation_generation, serving_generation, doc_count, chunk_count, "
                    "created_at, updated_at) VALUES "
                    "('dataset-defaults', 'tenant-defaults', 'Defaults', '', 'active', 1, "
                    "'owner-defaults', 'private', '{}', '{}', '{}', '{}', '{}', '{}', 'zh-CN', "
                    "0, 1, 0, 0, 0, 0, :now, :now)"
                ),
                {"now": now},
            )

        from sqlalchemy.orm import Session

        with Session(engine) as session:
            organization = models["tenant_organization_units"](
                id="ou-defaults",
                tenant_id="tenant-defaults",
                parent_id=None,
                name="平台研发部",
                status="active",
                created_at=now,
            )
            group = models["tenant_groups"](
                id="group-defaults",
                tenant_id="tenant-defaults",
                name="Platform Engineers",
                description="",
                status="active",
                created_at=now,
            )
            session.add_all([organization, group])
            session.flush()
            session.add_all(
                [
                    models["tenant_group_members"](
                        tenant_id="tenant-defaults",
                        group_id="group-defaults",
                        account_id="owner-defaults",
                        status="active",
                        created_at=now,
                    ),
                    models["dataset_access_grants"](
                        id="grant-defaults",
                        tenant_id="tenant-defaults",
                        dataset_id="dataset-defaults",
                        subject_type="group",
                        subject_id="group-defaults",
                        role="manager",
                        status="active",
                        created_at=now,
                    ),
                    models["tenant_invitations"](
                        id="invite-defaults",
                        tenant_id="tenant-defaults",
                        email="New.User@Example.Test ",
                        role="member",
                        status="pending",
                        token_hash="sha256:defaults-only",
                        expires_at=now + timedelta(days=7),
                        invited_by="owner-defaults",
                        created_at=now,
                        pending_email_key="new.user@example.test",
                        last_sent_at=now,
                        updated_by="owner-defaults",
                    ),
                ]
            )
            session.commit()

            assert organization.code == "ou-defaults"
            assert organization.created_by == "system"
            assert organization.updated_by == "system"
            assert group.normalized_name == "platform engineers"
            invitation = session.get(models["tenant_invitations"], "invite-defaults")
            assert invitation is not None
            assert invitation.normalized_email == "new.user@example.test"
    finally:
        engine.dispose()


def test_0017_sqlite_upgrade_downgrade_reupgrade_roundtrip(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path / "enterprise-access-graph.db")
    config = _alembic_config(url)

    command.upgrade(config, "0016_enterprise_membership")
    engine = create_engine(url)
    try:
        assert not ACCESS_GRAPH_TABLES & set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    _seed_0016_authority(url)
    command.upgrade(config, "0017_enterprise_access_graph")

    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        assert ACCESS_GRAPH_TABLES <= set(inspector.get_table_names())
        for table_name in sorted(ACCESS_GRAPH_TABLES):
            assert (
                tuple(column["name"] for column in inspector.get_columns(table_name))
                == (EXPECTED_COLUMNS[table_name])
            )
            assert _uniques(inspector, table_name) == EXPECTED_UNIQUES[table_name]
            assert _foreign_keys(inspector, table_name) == EXPECTED_FOREIGN_KEYS[table_name]
            assert set(_checks(inspector, table_name)) == set(EXPECTED_CHECKS[table_name])
            for check_name, fragments in EXPECTED_CHECKS[table_name].items():
                sql = _checks(inspector, table_name)[check_name]
                assert all(fragment in sql for fragment in fragments)
            assert {
                name: _indexes(inspector, table_name)[name] for name in EXPECTED_INDEXES[table_name]
            } == EXPECTED_INDEXES[table_name]

        invitation_columns = {item["name"] for item in inspector.get_columns("tenant_invitations")}
        assert "token_hash" in invitation_columns
        assert "token" not in invitation_columns
        assert "raw_token" not in invitation_columns

        now = datetime(2026, 8, 26, 10, 30, 0)
        with engine.begin() as connection:
            _enable_sqlite_foreign_keys(connection)
            root_id = connection.execute(
                text(
                    "INSERT INTO tenant_organization_units "
                    "(id, tenant_id, parent_id, name, code, status, sort_order, revision, "
                    "created_at, created_by, updated_at, updated_by) VALUES "
                    "('ou-1-root', 'tenant-1', NULL, '总部', 'HQ', 'active', 0, 1, "
                    ":now, 'owner-1', :now, 'owner-1') RETURNING id"
                ),
                {"now": now},
            ).scalar_one()
            child_id = connection.execute(
                text(
                    "INSERT INTO tenant_organization_units "
                    "(id, tenant_id, parent_id, name, code, status, sort_order, revision, "
                    "created_at, created_by, updated_at, updated_by) VALUES "
                    "('ou-1-rnd', 'tenant-1', :parent_id, '研发中心', 'RND', 'active', "
                    "10, 1, :now, 'owner-1', :now, 'owner-1') RETURNING id"
                ),
                {"parent_id": root_id, "now": now},
            ).scalar_one()
            group_id = connection.execute(
                text(
                    "INSERT INTO tenant_groups "
                    "(id, tenant_id, name, normalized_name, description, status, revision, "
                    "created_at, updated_at) VALUES "
                    "('group-1-rnd', 'tenant-1', '研发组', '研发组', '研发访问组', "
                    "'active', 1, :now, :now) RETURNING id"
                ),
                {"now": now},
            ).scalar_one()
            member_id = connection.execute(
                text(
                    "INSERT INTO tenant_group_members "
                    "(tenant_id, group_id, account_id, status, created_at, created_by) VALUES "
                    "('tenant-1', :group_id, 'owner-1', 'active', :now, 'owner-1') "
                    "RETURNING id"
                ),
                {"group_id": group_id, "now": now},
            ).scalar_one()
            grant_id = connection.execute(
                text(
                    "INSERT INTO dataset_access_grants "
                    "(id, tenant_id, dataset_id, subject_type, subject_id, role, status, revision, "
                    "created_at, updated_at) VALUES "
                    "('grant-1-rnd', 'tenant-1', 'dataset-1', 'organization_unit', "
                    ":subject_id, 'manager', 'active', 1, :now, :now) RETURNING id"
                ),
                {"subject_id": str(child_id), "now": now},
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO tenant_invitations "
                    "(id, tenant_id, email, normalized_email, role, status, token_hash, "
                    "expires_at, accepted_at, accepted_by, invited_by, revision, created_at, "
                    "updated_at) VALUES "
                    "('invite-1', 'tenant-1', 'USER@example.test', 'user@example.test', "
                    "'member', 'pending', :token_hash, :expires_at, NULL, NULL, 'owner-1', 1, "
                    ":now, :now)"
                ),
                {
                    "token_hash": "a" * 64,
                    "expires_at": now + timedelta(days=7),
                    "now": now,
                },
            )

        assert (root_id, child_id, group_id, grant_id) == (
            "ou-1-root",
            "ou-1-rnd",
            "group-1-rnd",
            "grant-1-rnd",
        )
        assert member_id > 0
        state = catalog_schema.inspect_catalog_schema(engine)
        assert state.revision == "0017_enterprise_access_graph"
        assert state.status == "behind"
    finally:
        engine.dispose()

    _assert_integrity_error(
        url,
        "INSERT INTO tenant_organization_units "
        "(id, tenant_id, parent_id, name, code, status, sort_order, revision, created_at, "
        "created_by, updated_at, updated_by) VALUES "
        "('ou-2-cross', 'tenant-2', :parent_id, '越界部门', 'CROSS', 'active', 0, "
        "1, :now, 'owner-2', "
        ":now, 'owner-2')",
        {"parent_id": root_id, "now": datetime(2026, 8, 26, 11, 0, 0)},
    )
    _assert_integrity_error(
        url,
        "INSERT INTO tenant_group_members "
        "(tenant_id, group_id, account_id, status, created_at, created_by) VALUES "
        "('tenant-2', :group_id, 'owner-2', 'active', :now, 'owner-2')",
        {"group_id": group_id, "now": datetime(2026, 8, 26, 11, 0, 0)},
    )
    _assert_integrity_error(
        url,
        "INSERT INTO tenant_group_members "
        "(tenant_id, group_id, account_id, status, created_at, created_by) VALUES "
        "('tenant-1', :group_id, 'owner-2', 'active', :now, 'owner-1')",
        {"group_id": group_id, "now": datetime(2026, 8, 26, 11, 0, 0)},
    )
    _assert_integrity_error(
        url,
        "INSERT INTO dataset_access_grants "
        "(id, tenant_id, dataset_id, subject_type, subject_id, role, status, revision, created_at, "
        "updated_at) VALUES "
        "('grant-2-cross', 'tenant-2', 'dataset-1', 'account', 'owner-2', "
        "'viewer', 'active', 1, :now, :now)",
        {"now": datetime(2026, 8, 26, 11, 0, 0)},
    )
    _assert_integrity_error(
        url,
        "INSERT INTO tenant_invitations "
        "(id, tenant_id, email, normalized_email, role, status, token_hash, expires_at, "
        "accepted_at, accepted_by, invited_by, revision, created_at, updated_at) VALUES "
        "('invite-cross', 'tenant-1', 'cross@example.test', 'cross@example.test', 'member', "
        "'pending', :token_hash, :expires_at, NULL, NULL, 'owner-2', 1, :now, :now)",
        {
            "token_hash": "b" * 64,
            "expires_at": datetime(2026, 9, 2, 11, 0, 0),
            "now": datetime(2026, 8, 26, 11, 0, 0),
        },
    )

    command.downgrade(config, "0016_enterprise_membership")
    engine = create_engine(url)
    try:
        assert not ACCESS_GRAPH_TABLES & set(inspect(engine).get_table_names())
        state = catalog_schema.inspect_catalog_schema(engine)
        assert state.revision == "0016_enterprise_membership"
        assert state.status == "behind"
    finally:
        engine.dispose()

    command.upgrade(config, "0017_enterprise_access_graph")
    engine = create_engine(url)
    try:
        assert ACCESS_GRAPH_TABLES <= set(inspect(engine).get_table_names())
        state = catalog_schema.inspect_catalog_schema(engine)
        assert state.revision == "0017_enterprise_access_graph"
        assert state.status == "behind"
    finally:
        engine.dispose()


def test_0017_mysql_offline_ddl_compiles() -> None:
    _migration_module()
    output = StringIO()
    config = _alembic_config("mysql+pymysql://user:pass@localhost/rag4c")
    config.output_buffer = output

    command.upgrade(
        config,
        "0016_enterprise_membership:0017_enterprise_access_graph",
        sql=True,
    )

    ddl = output.getvalue().upper()
    for table_name in ACCESS_GRAPH_TABLES:
        assert f"CREATE TABLE {table_name.upper()}" in ddl
    assert ddl.count("AUTO_INCREMENT") >= 1
    assert "VARCHAR(64)" in ddl
    assert "DATETIME(6)" in ddl
    assert "TOKEN_HASH" in ddl
    assert " RAW_TOKEN " not in ddl
    assert "FK_TENANT_ORGANIZATION_UNITS_SCOPE_PARENT" in ddl
    assert "FK_TENANT_GROUP_MEMBERS_SCOPE_GROUP" in ddl
    assert "FK_TENANT_GROUP_MEMBERS_SCOPE_ACCOUNT" in ddl
    assert "FK_DATASET_ACCESS_GRANTS_SCOPE_DATASET" in ddl
    assert "FK_TENANT_INVITATIONS_SCOPE_INVITER" in ddl
    assert "CK_DATASET_ACCESS_GRANTS_SUBJECT_TYPE" in ddl
    assert "CK_TENANT_INVITATIONS_STATUS" in ddl
    assert "IX_TENANT_INVITATIONS_TENANT_STATUS_EXPIRES" in ddl
