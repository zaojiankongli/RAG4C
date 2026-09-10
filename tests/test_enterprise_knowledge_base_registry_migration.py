from __future__ import annotations

from datetime import datetime, timedelta
from io import StringIO
import importlib
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core import catalog_schema
from models.orm import (
    Account,
    App,
    Dataset,
    Tenant,
    TenantMember,
    TenantWorkspace,
    TenantWorkspaceDataset,
)

REVISION = "0028_enterprise_knowledge_base_registry"
DOWN_REVISION = "0027_enterprise_workspace_authorization"
OWNERSHIP_TABLE = "dataset_workspace_ownerships"
REFERENCE_TABLE = "app_dataset_references"
TRANSFER_ACTION = "dataset_workspace_transfer"
MIGRATION_ACTOR = "migration:0028"


def migration_module():
    try:
        return importlib.import_module(
            "catalog_migrations.versions.0028_enterprise_knowledge_base_registry"
        )
    except ModuleNotFoundError as exc:  # RED phase: the migration is the missing behavior.
        pytest.fail(f"Stage 18 migration is missing: {exc}")


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def engine_for(url: str):
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def alembic_config(url: str, *, output_buffer: StringIO | None = None) -> Config:
    config = Config(str(Path("alembic.ini").resolve()), output_buffer=output_buffer)
    config.set_main_option("script_location", str(Path("catalog_migrations").resolve()))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def _workspace(
    *,
    workspace_id: str,
    tenant_id: str,
    code: str,
    name: str,
    created_by: str,
) -> TenantWorkspace:
    return TenantWorkspace(
        id=workspace_id,
        tenant_id=tenant_id,
        code=code,
        name=name,
        normalized_name=name.casefold(),
        description="",
        status="active",
        environment="testing",
        is_default=False,
        active_default_slot=None,
        revision=1,
        created_by=created_by,
        updated_by=created_by,
    )


def seed_0027(url: str, *, long_ids: bool = False) -> dict[str, str]:
    catalog_schema.upgrade_catalog(url, DOWN_REVISION)
    tenant_a = "t" * 64 if long_ids else "tenant-a"
    tenant_b = "tenant-b"
    dataset_primary = "d" * 64 if long_ids else "dataset-primary-a"
    dataset_shared_only = "dataset-shared-only-a"
    workspace_primary = "w" * 128 if long_ids else "workspace-primary-a"
    workspace_shared = "workspace-shared-a"
    workspace_b = "workspace-primary-b"
    owner_a = "owner-a"
    owner_b = "owner-b"

    engine = engine_for(url)
    try:
        with Session(engine) as session:
            session.add_all(
                [
                    Account(id=owner_a, name="Owner A", email="owner-a@stage18.test"),
                    Account(id=owner_b, name="Owner B", email="owner-b@stage18.test"),
                    Tenant(id=tenant_a, name="Alpha", plan="enterprise", status="active"),
                    Tenant(id=tenant_b, name="Beta", plan="enterprise", status="active"),
                ]
            )
            session.flush()
            session.add_all(
                [
                    TenantMember(
                        account_id=owner_a,
                        tenant_id=tenant_a,
                        role="owner",
                        status="active",
                    ),
                    TenantMember(
                        account_id=owner_b,
                        tenant_id=tenant_b,
                        role="owner",
                        status="active",
                    ),
                ]
            )
            session.flush()
            session.add_all(
                [
                    Dataset(
                        id=dataset_primary,
                        tenant_id=tenant_a,
                        owner_id=owner_a,
                        name="Primary Knowledge Base",
                        description="Primary dataset",
                        status="active",
                    ),
                    Dataset(
                        id=dataset_shared_only,
                        tenant_id=tenant_a,
                        owner_id=owner_a,
                        name="Shared Only Knowledge Base",
                        description="Shared dataset",
                        status="active",
                    ),
                    Dataset(
                        id="dataset-primary-b",
                        tenant_id=tenant_b,
                        owner_id=owner_b,
                        name="Tenant B Knowledge Base",
                        description="Tenant B dataset",
                        status="active",
                    ),
                ]
            )
            session.flush()
            session.add_all(
                [
                    _workspace(
                        workspace_id=workspace_primary,
                        tenant_id=tenant_a,
                        code="primary-a",
                        name="Primary A",
                        created_by=MIGRATION_ACTOR,
                    ),
                    _workspace(
                        workspace_id=workspace_shared,
                        tenant_id=tenant_a,
                        code="shared-a",
                        name="Shared A",
                        created_by=MIGRATION_ACTOR,
                    ),
                    _workspace(
                        workspace_id=workspace_b,
                        tenant_id=tenant_b,
                        code="primary-b",
                        name="Primary B",
                        created_by=MIGRATION_ACTOR,
                    ),
                ]
            )
            session.flush()
            session.add_all(
                [
                    TenantWorkspaceDataset(
                        tenant_id=tenant_a,
                        workspace_id=workspace_primary,
                        dataset_id=dataset_primary,
                        binding_kind="primary",
                        active_primary_slot="primary",
                        status="active",
                        created_by=MIGRATION_ACTOR,
                        updated_by=MIGRATION_ACTOR,
                    ),
                    TenantWorkspaceDataset(
                        tenant_id=tenant_a,
                        workspace_id=workspace_shared,
                        dataset_id=dataset_primary,
                        binding_kind="shared",
                        active_primary_slot=None,
                        status="active",
                        created_by=MIGRATION_ACTOR,
                        updated_by=MIGRATION_ACTOR,
                    ),
                    TenantWorkspaceDataset(
                        tenant_id=tenant_a,
                        workspace_id=workspace_shared,
                        dataset_id=dataset_shared_only,
                        binding_kind="shared",
                        active_primary_slot=None,
                        status="active",
                        created_by=MIGRATION_ACTOR,
                        updated_by=MIGRATION_ACTOR,
                    ),
                    TenantWorkspaceDataset(
                        tenant_id=tenant_b,
                        workspace_id=workspace_b,
                        dataset_id="dataset-primary-b",
                        binding_kind="primary",
                        active_primary_slot="primary",
                        status="active",
                        created_by=MIGRATION_ACTOR,
                        updated_by=MIGRATION_ACTOR,
                    ),
                ]
            )
            session.add_all(
                [
                    App(id="app-a", tenant_id=tenant_a, name="App A", kind="api"),
                    App(id="app-b", tenant_id=tenant_b, name="App B", kind="chat"),
                ]
            )
            session.commit()
    finally:
        engine.dispose()

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "dataset_primary": dataset_primary,
        "dataset_shared_only": dataset_shared_only,
        "dataset_b": "dataset-primary-b",
        "workspace_primary": workspace_primary,
        "workspace_shared": workspace_shared,
        "workspace_b": workspace_b,
        "owner_a": owner_a,
        "owner_b": owner_b,
    }


def upgrade_0028(url: str) -> None:
    command.upgrade(alembic_config(url), REVISION)


def check_sql(inspector, table: str) -> dict[str, str]:
    return {
        str(item["name"]): " ".join(str(item.get("sqltext") or "").casefold().split())
        for item in inspector.get_check_constraints(table)
    }


def insert_transfer_approval_rows(connection, *, tenant_id: str = "tenant-a") -> None:
    now = datetime.utcnow()
    expires = now + timedelta(hours=1)
    connection.execute(
        text(
            "INSERT INTO tenant_approval_policies "
            "(id,tenant_id,name,action_type,resource_scope,active_scope_key,status,required_approvals,"
            "request_expiry_minutes,revision,created_at,created_by,updated_at,updated_by) "
            "VALUES ('stage18-policy',:tenant_id,'Dataset transfer',:action,'knowledge_base:*',"
            "'knowledge-base-transfer|tenant-a','active',1,1440,1,:now,'owner-a',:now,'owner-a')"
        ),
        {"tenant_id": tenant_id, "action": TRANSFER_ACTION, "now": now},
    )
    connection.execute(
        text(
            "INSERT INTO tenant_approval_requests "
            "(id,tenant_id,policy_id,requester_id,action_type,resource_type,resource_id,snapshot_json,"
            "payload_hash,reason,status,required_approvals,received_approvals,idempotency_key,expires_at,"
            "revision,created_at,created_by,updated_at,updated_by) "
            "VALUES ('stage18-request',:tenant_id,'stage18-policy','owner-a',:action,'knowledge_base',"
            "'dataset-primary-a','{}',:payload_hash,'transfer','pending',1,0,'stage18-idempotency',"
            ":expires,1,:now,'owner-a',:now,'owner-a')"
        ),
        {
            "tenant_id": tenant_id,
            "action": TRANSFER_ACTION,
            "payload_hash": "b" * 64,
            "expires": expires,
            "now": now,
        },
    )


def test_0028_precedes_the_current_release_catalog_head() -> None:
    migration = migration_module()
    scripts = ScriptDirectory.from_config(alembic_config("sqlite://"))
    assert scripts.get_current_head() == catalog_schema.HEAD_REVISION
    script = scripts.get_revision(REVISION)
    assert script is not None
    assert script.down_revision == DOWN_REVISION
    assert migration.revision == REVISION
    assert migration.down_revision == DOWN_REVISION


def test_upgrade_creates_registry_authority_with_exact_structural_contract(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "registry-schema.db")
    seed_0027(url)
    upgrade_0028(url)

    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        assert {OWNERSHIP_TABLE, REFERENCE_TABLE} <= set(inspector.get_table_names())
        assert {
            "uq_apps_tenant_id",
        } <= {str(item["name"]) for item in inspector.get_unique_constraints("apps")}

        assert [item["name"] for item in inspector.get_columns(OWNERSHIP_TABLE)] == [
            "id",
            "tenant_id",
            "dataset_id",
            "workspace_id",
            "revision",
            "created_at",
            "created_by",
            "updated_at",
            "updated_by",
            "last_transfer_at",
        ]
        assert [item["name"] for item in inspector.get_columns(REFERENCE_TABLE)] == [
            "id",
            "tenant_id",
            "app_id",
            "dataset_id",
            "reference_kind",
            "status",
            "active_slot",
            "revision",
            "created_at",
            "created_by",
            "updated_at",
            "updated_by",
            "removed_at",
            "removed_by",
            "request_id",
        ]

        uniques = {
            table: {
                str(item["name"]): tuple(item.get("column_names") or ())
                for item in inspector.get_unique_constraints(table)
            }
            for table in (OWNERSHIP_TABLE, REFERENCE_TABLE)
        }
        assert uniques[OWNERSHIP_TABLE] == {
            "uq_dataset_workspace_ownerships_scope_id": ("tenant_id", "id"),
            "uq_dataset_workspace_ownerships_scope_dataset": ("tenant_id", "dataset_id"),
        }
        assert uniques[REFERENCE_TABLE] == {
            "uq_app_dataset_references_scope_id": ("tenant_id", "id"),
            "uq_app_dataset_references_active_slot": (
                "tenant_id",
                "app_id",
                "dataset_id",
                "active_slot",
            ),
        }

        foreign_keys = {
            table: {
                str(item["name"]): (
                    tuple(item.get("constrained_columns") or ()),
                    item.get("referred_table"),
                    tuple(item.get("referred_columns") or ()),
                )
                for item in inspector.get_foreign_keys(table)
            }
            for table in (OWNERSHIP_TABLE, REFERENCE_TABLE)
        }
        assert foreign_keys[OWNERSHIP_TABLE] == {
            "fk_dataset_workspace_ownerships_tenant": (("tenant_id",), "tenants", ("id",)),
            "fk_dataset_workspace_ownerships_scope_dataset": (
                ("tenant_id", "dataset_id"),
                "datasets",
                ("tenant_id", "id"),
            ),
            "fk_dataset_workspace_ownerships_scope_workspace": (
                ("tenant_id", "workspace_id"),
                "tenant_workspaces",
                ("tenant_id", "id"),
            ),
        }
        assert foreign_keys[REFERENCE_TABLE] == {
            "fk_app_dataset_references_tenant": (("tenant_id",), "tenants", ("id",)),
            "fk_app_dataset_references_scope_app": (
                ("tenant_id", "app_id"),
                "apps",
                ("tenant_id", "id"),
            ),
            "fk_app_dataset_references_scope_dataset": (
                ("tenant_id", "dataset_id"),
                "datasets",
                ("tenant_id", "id"),
            ),
        }

        assert {
            str(item["name"]): tuple(item.get("column_names") or ())
            for item in inspector.get_indexes(OWNERSHIP_TABLE)
        } == {
            "ix_dataset_workspace_ownerships_tenant_workspace_dataset": (
                "tenant_id",
                "workspace_id",
                "dataset_id",
            ),
            "ix_dataset_workspace_ownerships_tenant_updated_dataset": (
                "tenant_id",
                "updated_at",
                "dataset_id",
            ),
        }
        assert {
            str(item["name"]): tuple(item.get("column_names") or ())
            for item in inspector.get_indexes(REFERENCE_TABLE)
        } == {
            "ix_app_dataset_references_tenant_dataset_status": (
                "tenant_id",
                "dataset_id",
                "status",
                "id",
            ),
            "ix_app_dataset_references_tenant_app_status": (
                "tenant_id",
                "app_id",
                "status",
                "id",
            ),
            "ix_app_dataset_references_tenant_status_updated": (
                "tenant_id",
                "status",
                "updated_at",
                "id",
            ),
        }

        ownership_checks = check_sql(inspector, OWNERSHIP_TABLE)
        reference_checks = check_sql(inspector, REFERENCE_TABLE)
        assert ownership_checks["ck_dataset_workspace_ownerships_revision_positive"] == (
            "revision > 0"
        )
        assert reference_checks["ck_app_dataset_references_reference_kind"] == (
            "reference_kind = 'knowledge'"
        )
        assert reference_checks["ck_app_dataset_references_status"] == (
            "status in ('active','removed')"
        )
        assert reference_checks["ck_app_dataset_references_revision_positive"] == ("revision > 0")
        assert reference_checks["ck_app_dataset_references_lifecycle_evidence"] == (
            "(status = 'active' and active_slot = 'active' and removed_at is null and "
            "removed_by is null) or (status = 'removed' and active_slot is null and "
            "removed_at is not null and removed_by is not null)"
        )
    finally:
        engine.dispose()


def test_backfill_is_deterministic_and_never_promotes_shared_bindings_to_ownership(
    tmp_path: Path,
) -> None:
    first_url = sqlite_url(tmp_path / "registry-backfill-a.db")
    second_url = sqlite_url(tmp_path / "registry-backfill-b.db")
    first_ids = seed_0027(first_url)
    second_ids = seed_0027(second_url)
    upgrade_0028(first_url)
    upgrade_0028(second_url)

    rows_by_url = []
    for url, ids in ((first_url, first_ids), (second_url, second_ids)):
        engine = engine_for(url)
        try:
            with engine.connect() as connection:
                rows = connection.execute(
                    text(
                        "SELECT id,tenant_id,dataset_id,workspace_id,revision,created_by,updated_by "
                        "FROM dataset_workspace_ownerships ORDER BY tenant_id,dataset_id"
                    )
                ).all()
                rows_by_url.append(rows)
                assert [row.dataset_id for row in rows] == [
                    ids["dataset_primary"],
                    ids["dataset_b"],
                ]
                assert all(row.revision == 1 for row in rows)
                assert all(row.created_by == MIGRATION_ACTOR for row in rows)
                assert all(row.updated_by == MIGRATION_ACTOR for row in rows)
                assert (
                    connection.execute(
                        text(
                            "SELECT COUNT(*) FROM dataset_workspace_ownerships "
                            "WHERE dataset_id=:dataset_id"
                        ),
                        {"dataset_id": ids["dataset_shared_only"]},
                    ).scalar_one()
                    == 0
                )
                assert (
                    connection.execute(
                        text(
                            "SELECT workspace_id FROM dataset_workspace_ownerships "
                            "WHERE dataset_id=:dataset_id"
                        ),
                        {"dataset_id": ids["dataset_primary"]},
                    ).scalar_one()
                    == ids["workspace_primary"]
                )
        finally:
            engine.dispose()

    assert rows_by_url[0] == rows_by_url[1]


def test_registry_constraints_enforce_tenant_scope_lifecycle_and_active_uniqueness(
    tmp_path: Path,
) -> None:
    url = sqlite_url(tmp_path / "registry-constraints.db")
    ids = seed_0027(url)
    upgrade_0028(url)
    engine = engine_for(url)
    try:
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO dataset_workspace_ownerships "
                        "(id,tenant_id,dataset_id,workspace_id,revision,created_at,created_by,"
                        "updated_at,updated_by) VALUES "
                        "('ownership-cross-tenant','tenant-a','dataset-primary-b',:workspace,1,"
                        "CURRENT_TIMESTAMP,'migration:0028',CURRENT_TIMESTAMP,'migration:0028')"
                    ),
                    {"workspace": ids["workspace_primary"]},
                )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO app_dataset_references "
                        "(id,tenant_id,app_id,dataset_id,reference_kind,status,active_slot,revision,"
                        "created_at,created_by,updated_at,updated_by,request_id) VALUES "
                        "('reference-cross-tenant','tenant-a','app-b','dataset-primary-a','knowledge',"
                        "'active','active',1,CURRENT_TIMESTAMP,'migration:0028',CURRENT_TIMESTAMP,"
                        "'migration:0028','request-cross')"
                    )
                )

        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO app_dataset_references "
                    "(id,tenant_id,app_id,dataset_id,reference_kind,status,active_slot,revision,"
                    "created_at,created_by,updated_at,updated_by,request_id) VALUES "
                    "('reference-a','tenant-a','app-a','dataset-primary-a','knowledge','active','active',"
                    "1,CURRENT_TIMESTAMP,'migration:0028',CURRENT_TIMESTAMP,'migration:0028','request-a')"
                )
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO app_dataset_references "
                        "(id,tenant_id,app_id,dataset_id,reference_kind,status,active_slot,revision,"
                        "created_at,created_by,updated_at,updated_by,request_id) VALUES "
                        "('reference-duplicate','tenant-a','app-a','dataset-primary-a','knowledge','active',"
                        "'active',1,CURRENT_TIMESTAMP,'migration:0028',CURRENT_TIMESTAMP,'migration:0028',"
                        "'request-duplicate')"
                    )
                )

        invalid_rows = (
            (
                "reference-kind",
                "reference_kind",
                "'workflow'",
                "status,active_slot",
                "'active','active'",
            ),
            (
                "reference-status",
                "reference_kind,status,active_slot",
                "'knowledge','removed','active'",
                "",
                "",
            ),
            (
                "reference-revision",
                "reference_kind,status,active_slot,revision",
                "'knowledge','active','active',0",
                "",
                "",
            ),
        )
        for row in invalid_rows:
            row_id, columns, values, suffix_columns, suffix_values = row
            if suffix_columns:
                columns = f"{columns},{suffix_columns}"
                values = f"{values},{suffix_values}"
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO app_dataset_references "
                            f"(id,tenant_id,app_id,dataset_id,{columns},created_at,created_by,"
                            "updated_at,updated_by,request_id) VALUES "
                            f"('{row_id}','tenant-a','app-a','dataset-shared-only-a',{values},"
                            "CURRENT_TIMESTAMP,'migration:0028',CURRENT_TIMESTAMP,'migration:0028',"
                            f"'{row_id}-request')"
                        )
                    )
    finally:
        engine.dispose()


def test_long_scope_ids_fit_mysql_compatible_columns_and_keep_ownership_id_bounded(
    tmp_path: Path,
) -> None:
    url = sqlite_url(tmp_path / "registry-long-ids.db")
    ids = seed_0027(url, long_ids=True)
    upgrade_0028(url)
    engine = engine_for(url)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT id,tenant_id,dataset_id,workspace_id FROM dataset_workspace_ownerships "
                    "WHERE tenant_id=:tenant_id AND dataset_id=:dataset_id"
                ),
                {"tenant_id": ids["tenant_a"], "dataset_id": ids["dataset_primary"]},
            ).one()
            assert len(row.id) == 64
            assert len(row.tenant_id) == 64
            assert len(row.dataset_id) == 64
            assert len(row.workspace_id) == 128
    finally:
        engine.dispose()


def test_approval_action_check_accepts_dataset_workspace_transfer_and_rejects_unknown_action(
    tmp_path: Path,
) -> None:
    url = sqlite_url(tmp_path / "registry-approval.db")
    seed_0027(url)
    upgrade_0028(url)
    engine = engine_for(url)
    try:
        checks = check_sql(inspect(engine), "tenant_approval_policies")
        request_checks = check_sql(inspect(engine), "tenant_approval_requests")
        assert TRANSFER_ACTION in checks["ck_tenant_approval_policies_action_type"]
        assert TRANSFER_ACTION in request_checks["ck_tenant_approval_requests_action_type"]
        with engine.begin() as connection:
            insert_transfer_approval_rows(connection)
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO tenant_approval_policies "
                        "(id,tenant_id,name,action_type,resource_scope,active_scope_key,status,"
                        "required_approvals,request_expiry_minutes,revision,created_at,created_by,"
                        "updated_at,updated_by) VALUES ('bad-action','tenant-a','Bad',"
                        "'dataset_workspace_transfer_unknown',NULL,'bad|tenant-a','active',1,1440,1,"
                        "CURRENT_TIMESTAMP,'owner-a',CURRENT_TIMESTAMP,'owner-a')"
                    )
                )
    finally:
        engine.dispose()


def test_downgrade_fails_closed_while_registry_facts_or_transfer_approvals_remain(
    tmp_path: Path,
) -> None:
    url = sqlite_url(tmp_path / "registry-downgrade-blocked.db")
    seed_0027(url)
    upgrade_0028(url)
    with pytest.raises(Exception, match="0028|ownership|reference|transfer"):
        command.downgrade(alembic_config(url), DOWN_REVISION)


def test_downgrade_removes_empty_registry_authority_and_restores_0027_action_checks(
    tmp_path: Path,
) -> None:
    url = sqlite_url(tmp_path / "registry-downgrade.db")
    catalog_schema.upgrade_catalog(url, DOWN_REVISION)
    upgrade_0028(url)
    command.downgrade(alembic_config(url), DOWN_REVISION)

    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        assert OWNERSHIP_TABLE not in inspector.get_table_names()
        assert REFERENCE_TABLE not in inspector.get_table_names()
        assert "uq_apps_tenant_id" not in {
            str(item["name"]) for item in inspector.get_unique_constraints("apps")
        }
        assert (
            TRANSFER_ACTION
            not in check_sql(inspector, "tenant_approval_policies")[
                "ck_tenant_approval_policies_action_type"
            ]
        )
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == DOWN_REVISION
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("url", "dialect_marker"),
    [
        ("mysql+pymysql://u:p@localhost/rag4c", "DATETIME(6)"),
        ("postgresql+psycopg://u:p@localhost/rag4c", "TIMESTAMP"),
    ],
)
def test_offline_upgrade_contains_registry_mysql_ddl_and_online_downgrade_gate(
    url: str, dialect_marker: str
) -> None:
    output = StringIO()
    command.upgrade(
        alembic_config(url, output_buffer=output), f"{DOWN_REVISION}:{REVISION}", sql=True
    )
    sql = output.getvalue().upper()
    assert "CREATE TABLE DATASET_WORKSPACE_OWNERSHIPS" in sql
    assert "CREATE TABLE APP_DATASET_REFERENCES" in sql
    assert "UQ_APPS_TENANT_ID" in sql
    assert "DATASET_WORKSPACE_TRANSFER" in sql
    assert "FK_DATASET_WORKSPACE_OWNERSHIPS_SCOPE_DATASET" in sql
    assert "FK_APP_DATASET_REFERENCES_SCOPE_APP" in sql
    assert dialect_marker in sql

    with pytest.raises(Exception, match="online|preflight|0028|ownership|reference|transfer"):
        command.downgrade(
            alembic_config(url, output_buffer=StringIO()),
            f"{REVISION}:{DOWN_REVISION}",
            sql=True,
        )
