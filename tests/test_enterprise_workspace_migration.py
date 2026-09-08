from __future__ import annotations

from pathlib import Path
import importlib
import subprocess
import sys

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.orm import Account, Dataset, Tenant, TenantMember

REVISION = "0026_enterprise_workspace_control"
DOWN_REVISION = "0025_enterprise_approval_control"


def catalog_schema():
    return importlib.import_module("core.catalog_schema")


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


def alembic_config(url: str) -> Config:
    config = Config(str(Path("alembic.ini").resolve()))
    config.set_main_option("script_location", str(Path("catalog_migrations").resolve()))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def seed_0025(url: str) -> None:
    api = catalog_schema()
    api.upgrade_catalog(url, DOWN_REVISION)
    engine = engine_for(url)
    try:
        with Session(engine) as session:
            accounts = [
                Account(id="owner-a", name="Owner A", email="owner-a@example.test"),
                Account(id="admin-a", name="Admin A", email="admin-a@example.test"),
                Account(id="editor-a", name="Editor A", email="editor-a@example.test"),
                Account(id="member-a", name="Member A", email="member-a@example.test"),
                Account(id="suspended-a", name="Suspended A", email="suspended-a@example.test"),
                Account(id="admin-b", name="Admin B", email="admin-b@example.test"),
            ]
            session.add_all(accounts)
            session.add_all(
                [
                    Tenant(id="tenant-a", name="Alpha", plan="enterprise", status="active"),
                    Tenant(id="tenant-b", name="Beta", plan="pro", status="active"),
                ]
            )
            session.flush()
            session.add_all(
                [
                    TenantMember(
                        account_id="owner-a", tenant_id="tenant-a", role="owner", status="active"
                    ),
                    TenantMember(
                        account_id="admin-a", tenant_id="tenant-a", role="admin", status="active"
                    ),
                    TenantMember(
                        account_id="editor-a", tenant_id="tenant-a", role="editor", status="active"
                    ),
                    TenantMember(
                        account_id="member-a", tenant_id="tenant-a", role="member", status="active"
                    ),
                    TenantMember(
                        account_id="suspended-a",
                        tenant_id="tenant-a",
                        role="member",
                        status="suspended",
                    ),
                    TenantMember(
                        account_id="admin-b", tenant_id="tenant-b", role="admin", status="active"
                    ),
                ]
            )
            session.add_all(
                [
                    Dataset(
                        id="dataset-a1", tenant_id="tenant-a", name="Alpha handbook", description=""
                    ),
                    Dataset(
                        id="dataset-a2", tenant_id="tenant-a", name="Alpha policies", description=""
                    ),
                    Dataset(
                        id="dataset-b1", tenant_id="tenant-b", name="Beta handbook", description=""
                    ),
                ]
            )
            session.commit()
    finally:
        engine.dispose()


def upgrade_workspace(url: str) -> None:
    command.upgrade(alembic_config(url), REVISION)


def test_stage16_orm_relationships_configure_without_overlap_warnings() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import warnings; from sqlalchemy.exc import SAWarning; warnings.simplefilter('error', SAWarning); "
            "from models.orm import Account, Dataset, Tenant, TenantMember, TenantWorkspace, TenantWorkspaceDataset; "
            "from sqlalchemy.orm import configure_mappers; "
            "Account(id='a', name='A', email='a@example.test'); Tenant(id='t', name='T'); "
            "TenantMember(account_id='a', tenant_id='t', role='owner'); Dataset(id='d', tenant_id='t', name='D'); "
            "TenantWorkspace(id='w', tenant_id='t', code='w', name='W', normalized_name='w', description='', "
            "created_by='migration:0026', updated_by='migration:0026'); "
            "TenantWorkspaceDataset(tenant_id='t', workspace_id='w', dataset_id='d', binding_kind='primary', created_by='migration:0026', updated_by='migration:0026'); "
            "configure_mappers()",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_0026_revision_and_three_tables_are_real_migration_authority(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "workspace-schema.db")
    seed_0025(url)

    upgrade_workspace(url)

    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        assert {
            "tenant_workspaces",
            "tenant_workspace_members",
            "tenant_workspace_datasets",
        } <= set(inspector.get_table_names())
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == REVISION
            )
    finally:
        engine.dispose()


def test_backfill_is_deterministic_tenant_safe_and_maps_active_member_roles(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "workspace-backfill.db")
    seed_0025(url)
    upgrade_workspace(url)

    engine = engine_for(url)
    try:
        with engine.connect() as connection:
            workspaces = (
                connection.execute(
                    text(
                        "SELECT id, tenant_id, code, name, normalized_name, status, environment, "
                        "is_default, active_default_slot, revision FROM tenant_workspaces "
                        "ORDER BY tenant_id"
                    )
                )
                .mappings()
                .all()
            )
            assert [row["id"] for row in workspaces] == [
                "workspace-default-tenant-a",
                "workspace-default-tenant-b",
            ]
            assert all(row["code"] == "default" for row in workspaces)
            assert all(row["is_default"] in (True, 1) for row in workspaces)
            assert all(row["active_default_slot"] == "default" for row in workspaces)
            assert all(row["status"] == "active" for row in workspaces)
            assert all(row["environment"] == "production" for row in workspaces)
            assert all(row["revision"] == 1 for row in workspaces)

            members = (
                connection.execute(
                    text(
                        "SELECT tenant_id, account_id, role, status, revision FROM tenant_workspace_members "
                        "ORDER BY tenant_id, account_id"
                    )
                )
                .mappings()
                .all()
            )
            assert [(row["tenant_id"], row["account_id"], row["role"]) for row in members] == [
                ("tenant-a", "admin-a", "admin"),
                ("tenant-a", "editor-a", "editor"),
                ("tenant-a", "member-a", "viewer"),
                ("tenant-a", "owner-a", "owner"),
                ("tenant-b", "admin-b", "admin"),
            ]
            assert all(row["status"] == "active" and row["revision"] == 1 for row in members)

            bindings = (
                connection.execute(
                    text(
                        "SELECT tenant_id, workspace_id, dataset_id, binding_kind, status, active_primary_slot "
                        "FROM tenant_workspace_datasets ORDER BY tenant_id, dataset_id"
                    )
                )
                .mappings()
                .all()
            )
            assert [
                (row["tenant_id"], row["dataset_id"], row["binding_kind"]) for row in bindings
            ] == [
                ("tenant-a", "dataset-a1", "primary"),
                ("tenant-a", "dataset-a2", "primary"),
                ("tenant-b", "dataset-b1", "primary"),
            ]
            assert all(row["status"] == "active" for row in bindings)
            assert all(row["active_primary_slot"] == "primary" for row in bindings)
    finally:
        engine.dispose()


def test_long_tenant_name_backfill_is_deterministic_and_within_mysql_column_contract(
    tmp_path: Path,
) -> None:
    long_name = "x" * 128
    first_url = sqlite_url(tmp_path / "workspace-long-name-a.db")
    second_url = sqlite_url(tmp_path / "workspace-long-name-b.db")

    for url in (first_url, second_url):
        seed_0025(url)
        engine = engine_for(url)
        try:
            with Session(engine) as session:
                session.add(
                    Tenant(id="tenant-long", name=long_name, plan="enterprise", status="active")
                )
                session.commit()
        finally:
            engine.dispose()
        upgrade_workspace(url)

    expected_name = "x" * 110 + " Default Workspace"
    results: list[tuple[str, str]] = []
    for url in (first_url, second_url):
        engine = engine_for(url)
        try:
            with engine.connect() as connection:
                row = connection.execute(
                    text(
                        "SELECT name, normalized_name FROM tenant_workspaces "
                        "WHERE id = 'workspace-default-tenant-long'"
                    )
                ).one()
                columns = {
                    item["name"]: item
                    for item in inspect(connection).get_columns("tenant_workspaces")
                }
                assert columns["name"]["type"].length == 128
                assert columns["normalized_name"]["type"].length == 256
                results.append((str(row.name), str(row.normalized_name)))
        finally:
            engine.dispose()

    assert results == [(expected_name, expected_name.casefold())] * 2
    assert len(results[0][0]) == 128
    assert len(results[0][1]) <= 256


def test_casefold_expanding_tenant_name_keeps_normalized_name_within_256(
    tmp_path: Path,
) -> None:
    url = sqlite_url(tmp_path / "workspace-unicode-name.db")
    seed_0025(url)
    engine = engine_for(url)
    try:
        with Session(engine) as session:
            session.add(
                Tenant(
                    id="tenant-unicode",
                    name="\u0390" * 128,
                    plan="enterprise",
                    status="active",
                )
            )
            session.commit()
    finally:
        engine.dispose()

    upgrade_workspace(url)

    engine = engine_for(url)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT name, normalized_name FROM tenant_workspaces "
                    "WHERE id = 'workspace-default-tenant-unicode'"
                )
            ).one()
            assert len(str(row.name)) <= 128
            assert len(str(row.normalized_name)) <= 256
            assert str(row.name).endswith(" Default Workspace")
            assert str(row.normalized_name) == str(row.name).casefold()
    finally:
        engine.dispose()


def test_database_constraints_enforce_one_active_default_and_one_primary_per_dataset(
    tmp_path: Path,
) -> None:
    url = sqlite_url(tmp_path / "workspace-constraints.db")
    seed_0025(url)
    upgrade_workspace(url)
    engine = engine_for(url)
    try:
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO tenant_workspaces "
                        "(id,tenant_id,code,name,normalized_name,description,status,environment,is_default,active_default_slot,revision,created_at,created_by,updated_at,updated_by) "
                        "VALUES ('workspace-extra','tenant-a','other','Other','other','', 'active','production',1,'default',1,CURRENT_TIMESTAMP,'migration:0026',CURRENT_TIMESTAMP,'migration:0026')"
                    )
                )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO tenant_workspace_datasets "
                        "(tenant_id,workspace_id,dataset_id,binding_kind,active_primary_slot,status,revision,created_at,created_by,updated_at,updated_by) "
                        "VALUES ('tenant-b','workspace-default-tenant-b','dataset-a1','primary','primary','active',1,CURRENT_TIMESTAMP,'migration:0026',CURRENT_TIMESTAMP,'migration:0026')"
                    )
                )
    finally:
        engine.dispose()


def test_active_primary_slot_check_rejects_null_bypass_and_accepts_shared_removed(
    tmp_path: Path,
) -> None:
    url = sqlite_url(tmp_path / "workspace-primary-slot-semantics.db")
    seed_0025(url)
    upgrade_workspace(url)
    engine = engine_for(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenant_workspaces "
                    "(id,tenant_id,code,name,normalized_name,description,status,environment,is_default,active_default_slot,revision,created_at,created_by,updated_at,updated_by,archived_at,archived_by) "
                    "VALUES ('workspace-secondary','tenant-a','secondary','Secondary','secondary','', 'active','production',0,NULL,1,CURRENT_TIMESTAMP,'owner-a',CURRENT_TIMESTAMP,'owner-a',NULL,NULL)"
                )
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO tenant_workspace_datasets "
                        "(tenant_id,workspace_id,dataset_id,binding_kind,active_primary_slot,status,revision,created_at,created_by,updated_at,updated_by,removed_at,removed_by) "
                        "VALUES ('tenant-a','workspace-secondary','dataset-a1','primary',NULL,'active',1,CURRENT_TIMESTAMP,'owner-a',CURRENT_TIMESTAMP,'owner-a',NULL,NULL)"
                    )
                )

        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenant_workspace_datasets "
                    "(tenant_id,workspace_id,dataset_id,binding_kind,active_primary_slot,status,revision,created_at,created_by,updated_at,updated_by,removed_at,removed_by) "
                    "VALUES ('tenant-a','workspace-secondary','dataset-a2','shared',NULL,'active',1,CURRENT_TIMESTAMP,'owner-a',CURRENT_TIMESTAMP,'owner-a',NULL,NULL)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO tenant_workspace_datasets "
                    "(tenant_id,workspace_id,dataset_id,binding_kind,active_primary_slot,status,revision,created_at,created_by,updated_at,updated_by,removed_at,removed_by) "
                    "VALUES ('tenant-a','workspace-secondary','dataset-a1','primary',NULL,'removed',1,CURRENT_TIMESTAMP,'owner-a',CURRENT_TIMESTAMP,'owner-a',CURRENT_TIMESTAMP,'owner-a')"
                )
            )
            rows = connection.execute(
                text(
                    "SELECT dataset_id, binding_kind, status, active_primary_slot "
                    "FROM tenant_workspace_datasets WHERE workspace_id='workspace-secondary' "
                    "ORDER BY dataset_id"
                )
            ).all()
            assert rows == [
                ("dataset-a1", "primary", "removed", None),
                ("dataset-a2", "shared", "active", None),
            ]
    finally:
        engine.dispose()


def test_workspace_lifecycle_evidence_check_is_bidirectional(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "workspace-lifecycle-evidence.db")
    seed_0025(url)
    upgrade_workspace(url)
    engine = engine_for(url)
    try:
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO tenant_workspaces "
                        "(id,tenant_id,code,name,normalized_name,description,status,environment,is_default,active_default_slot,revision,created_at,created_by,updated_at,updated_by,archived_at,archived_by) "
                        "VALUES ('workspace-active-evidence','tenant-a','active-evidence','Active Evidence','active evidence','', 'active','production',0,NULL,1,CURRENT_TIMESTAMP,'owner-a',CURRENT_TIMESTAMP,'owner-a',CURRENT_TIMESTAMP,'owner-a')"
                    )
                )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO tenant_workspaces "
                        "(id,tenant_id,code,name,normalized_name,description,status,environment,is_default,active_default_slot,revision,created_at,created_by,updated_at,updated_by,archived_at,archived_by) "
                        "VALUES ('workspace-archived-missing','tenant-a','archived-missing','Archived Missing','archived missing','', 'archived','production',0,NULL,1,CURRENT_TIMESTAMP,'owner-a',CURRENT_TIMESTAMP,'owner-a',NULL,NULL)"
                    )
                )

        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenant_workspaces "
                    "(id,tenant_id,code,name,normalized_name,description,status,environment,is_default,active_default_slot,revision,created_at,created_by,updated_at,updated_by,archived_at,archived_by) "
                    "VALUES ('workspace-active-valid','tenant-a','active-valid','Active Valid','active valid','', 'active','production',0,NULL,1,CURRENT_TIMESTAMP,'owner-a',CURRENT_TIMESTAMP,'owner-a',NULL,NULL)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO tenant_workspaces "
                    "(id,tenant_id,code,name,normalized_name,description,status,environment,is_default,active_default_slot,revision,created_at,created_by,updated_at,updated_by,archived_at,archived_by) "
                    "VALUES ('workspace-archived-valid','tenant-a','archived-valid','Archived Valid','archived valid','', 'archived','production',0,NULL,1,CURRENT_TIMESTAMP,'owner-a',CURRENT_TIMESTAMP,'owner-a',CURRENT_TIMESTAMP,'owner-a')"
                )
            )
            statuses = (
                connection.execute(
                    text(
                        "SELECT status FROM tenant_workspaces "
                        "WHERE id IN ('workspace-active-valid','workspace-archived-valid') ORDER BY id"
                    )
                )
                .scalars()
                .all()
            )
            assert statuses == ["active", "archived"]
    finally:
        engine.dispose()


def test_orm_workspace_checks_encode_bidirectional_primary_and_lifecycle_semantics() -> None:
    from models.orm import TenantWorkspace, TenantWorkspaceDataset

    workspace_checks = {
        constraint.name: str(constraint.sqltext).casefold()
        for constraint in TenantWorkspace.__table__.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    dataset_checks = {
        constraint.name: str(constraint.sqltext).casefold()
        for constraint in TenantWorkspaceDataset.__table__.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    lifecycle = workspace_checks["ck_tenant_workspaces_lifecycle_evidence"]
    assert "status = 'active'" in lifecycle
    assert "archived_at is null" in lifecycle
    assert "archived_by is null" in lifecycle
    assert "status = 'archived'" in lifecycle
    assert "archived_at is not null" in lifecycle
    assert "archived_by is not null" in lifecycle

    primary = dataset_checks["ck_tenant_workspace_datasets_active_primary_slot"]
    assert "status = 'active'" in primary
    assert "binding_kind = 'primary'" in primary
    assert "active_primary_slot is not null" in primary
    assert "active_primary_slot = 'primary'" in primary
    assert "status <> 'active'" in primary
    assert "binding_kind <> 'primary'" in primary
    assert "active_primary_slot is null" in primary


def test_composite_foreign_keys_prevent_cross_tenant_workspace_member_and_dataset_bindings(
    tmp_path: Path,
) -> None:
    url = sqlite_url(tmp_path / "workspace-isolation.db")
    seed_0025(url)
    upgrade_workspace(url)
    engine = engine_for(url)
    try:
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO tenant_workspace_members "
                        "(tenant_id,workspace_id,account_id,role,status,revision,created_at,created_by,updated_at,updated_by) "
                        "VALUES ('tenant-a','workspace-default-tenant-a','admin-b','admin','active',1,CURRENT_TIMESTAMP,'migration:0026',CURRENT_TIMESTAMP,'migration:0026')"
                    )
                )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO tenant_workspace_datasets "
                        "(tenant_id,workspace_id,dataset_id,binding_kind,active_primary_slot,status,revision,created_at,created_by,updated_at,updated_by) "
                        "VALUES ('tenant-b','workspace-default-tenant-b','dataset-a1','shared',NULL,'active',1,CURRENT_TIMESTAMP,'migration:0026',CURRENT_TIMESTAMP,'migration:0026')"
                    )
                )
    finally:
        engine.dispose()


def test_downgrade_removes_workspace_authority_without_removing_existing_business_rows(
    tmp_path: Path,
) -> None:
    url = sqlite_url(tmp_path / "workspace-downgrade.db")
    seed_0025(url)
    upgrade_workspace(url)

    command.downgrade(alembic_config(url), DOWN_REVISION)

    engine = engine_for(url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert "tenant_workspaces" not in tables
        assert "tenant_workspace_members" not in tables
        assert "tenant_workspace_datasets" not in tables
        with engine.connect() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM tenants")).scalar_one() == 2
            assert connection.execute(text("SELECT COUNT(*) FROM datasets")).scalar_one() == 3
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == DOWN_REVISION
            )
    finally:
        engine.dispose()


def test_invalid_preexisting_tenant_reference_fails_closed_before_workspace_backfill(
    tmp_path: Path,
) -> None:
    url = sqlite_url(tmp_path / "workspace-preflight.db")
    seed_0025(url)
    engine = engine_for(url)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.execute(
                text(
                    "INSERT INTO datasets "
                    "(id,tenant_id,name,description,status,profile_revision,owner_id,visibility,profile_json,"
                    "parser_policy,chunk_policy,retrieval_policy,retention_policy,metadata_policy,default_language,"
                    "graph_enabled,qa_enabled,archived_at,archived_by,mutation_generation,serving_generation,"
                    "acl_mode,acl_revision,acl_enabled_at,acl_enabled_by,doc_count,chunk_count,created_at,updated_at) "
                    "VALUES ('orphan-dataset','missing-tenant','Orphan','', 'active',1,NULL,'private','{}','{}','{}','{}','{}','{}',"
                    "'zh-CN',0,1,NULL,NULL,0,0,'tenant_role',1,NULL,NULL,0,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                )
            )
            connection.commit()
    finally:
        engine.dispose()

    with pytest.raises(Exception, match="tenant|dataset|reference|orphan"):
        upgrade_workspace(url)

    engine = engine_for(url)
    try:
        assert "tenant_workspaces" not in set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
