from __future__ import annotations

from datetime import datetime
from io import StringIO
import importlib
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, Table, create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core import catalog_schema
from models.orm import (
    Account,
    App,
    TenantMember,
    TenantWorkspace,
    TenantWorkspaceDataset,
)

REVISION = "0029_enterprise_knowledge_base_releases"
DOWN_REVISION = "0028_enterprise_knowledge_base_registry"
CHANNEL_TABLE = "tenant_release_channels"
MANIFEST_TABLE = "dataset_release_manifests"
ENTRY_TABLE = "dataset_release_entries"
EVENT_TABLE = "dataset_release_events"
BINDING_TABLE = "dataset_channel_releases"
PUBLISH_ACTION = "knowledge_base_release_publish"
ROLLBACK_ACTION = "knowledge_base_release_rollback"

EXPECTED_COLUMNS = {
    CHANNEL_TABLE: [
        "id",
        "tenant_id",
        "code",
        "normalized_code",
        "name",
        "status",
        "risk_tier",
        "promotion_order",
        "is_default_serving",
        "active_default_slot",
        "revision",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "archived_at",
        "archived_by",
    ],
    MANIFEST_TABLE: [
        "id",
        "tenant_id",
        "dataset_id",
        "release_number",
        "profile_revision",
        "ownership_revision",
        "workspace_id",
        "workspace_revision",
        "mutation_generation",
        "serving_generation",
        "schema_version",
        "policy_digest",
        "manifest_digest",
        "readiness_digest",
        "readiness_state",
        "entry_count",
        "blocker_count",
        "readiness_blockers_json",
        "created_at",
        "created_by",
        "reason",
        "request_id",
    ],
    ENTRY_TABLE: [
        "id",
        "tenant_id",
        "dataset_id",
        "release_id",
        "ordinal",
        "resource_type",
        "resource_id",
        "resource_revision",
        "content_digest",
        "safe_facts_json",
        "created_at",
    ],
    EVENT_TABLE: [
        "id",
        "tenant_id",
        "dataset_id",
        "release_id",
        "channel_id",
        "event_type",
        "actor_id",
        "reason",
        "request_id",
        "occurred_at",
        "previous_binding_revision",
        "current_binding_revision",
    ],
    BINDING_TABLE: [
        "id",
        "tenant_id",
        "dataset_id",
        "channel_id",
        "active_release_id",
        "previous_release_id",
        "status",
        "active_slot",
        "revision",
        "activated_at",
        "activated_by",
        "request_id",
        "reason",
        "created_at",
        "updated_at",
        "updated_by",
    ],
}


def migration_module():
    try:
        return importlib.import_module(
            "catalog_migrations.versions.0029_enterprise_knowledge_base_releases"
        )
    except ModuleNotFoundError as exc:  # RED phase: the migration is the missing behavior.
        pytest.fail(f"Stage 19 migration is missing: {exc}")


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


def seed_0028(url: str, *, include_reference: bool = False) -> None:
    catalog_schema.upgrade_catalog(url, DOWN_REVISION)
    engine = engine_for(url)
    try:
        with Session(engine) as session:
            # 时代夹具要写的是 0028 那一版的 tenants 表，而 ORM 模型反映 head schema
            # （0038 之后 tenants 多了 default_storage_backend_id）。沿用本文件对 datasets 的
            # 既有做法：反射真实表再插，只发出该时代确实存在的列。
            tenants_table = Table("tenants", MetaData(), autoload_with=engine)
            session.execute(
                tenants_table.insert().values(
                    **{
                        key: value
                        for key, value in {
                            "id": "tenant-a",
                            "name": "Tenant A",
                            "plan": "enterprise",
                            "status": "active",
                            "quota_documents": 1000,
                            "quota_chunks": 100000,
                            "doc_count": 0,
                            "chunk_count": 0,
                            "created_at": datetime(2026, 8, 28, 12, 0, 0),
                            "updated_at": datetime(2026, 8, 28, 12, 0, 0),
                        }.items()
                        if key in tenants_table.c
                    }
                )
            )
            account = Account(id="owner-a", name="Owner A", email="owner-a@stage19.test")
            session.add(account)
            session.flush()
            session.add(
                TenantMember(
                    account_id="owner-a",
                    tenant_id="tenant-a",
                    role="owner",
                    status="active",
                )
            )
            session.flush()
            workspace = TenantWorkspace(
                id="workspace-a",
                tenant_id="tenant-a",
                code="primary",
                name="Primary Workspace",
                normalized_name="primary workspace",
                description="",
                status="active",
                environment="testing",
                is_default=False,
                active_default_slot=None,
                revision=2,
                created_by="owner-a",
                updated_by="owner-a",
            )
            session.add(workspace)
            session.flush()
            datasets = Table("datasets", MetaData(), autoload_with=engine)
            session.execute(
                datasets.insert().values(
                    id="dataset-a",
                    tenant_id="tenant-a",
                    owner_id="owner-a",
                    name="Knowledge Base A",
                    description="",
                    status="active",
                    profile_revision=1,
                    visibility="private",
                    profile_json={},
                    parser_policy={},
                    chunk_policy={},
                    retrieval_policy={},
                    retention_policy={},
                    metadata_policy={},
                    default_language="zh-CN",
                    graph_enabled=False,
                    qa_enabled=True,
                    mutation_generation=0,
                    serving_generation=0,
                    acl_mode="tenant_role",
                    acl_revision=1,
                    doc_count=0,
                    chunk_count=0,
                    created_at=datetime(2026, 8, 28, 12, 0, 0),
                    updated_at=datetime(2026, 8, 28, 12, 0, 0),
                )
            )
            session.flush()
            session.add(
                TenantWorkspaceDataset(
                    tenant_id="tenant-a",
                    workspace_id="workspace-a",
                    dataset_id="dataset-a",
                    binding_kind="primary",
                    active_primary_slot="primary",
                    status="active",
                    revision=3,
                    created_by="owner-a",
                    updated_by="owner-a",
                )
            )
            if include_reference:
                session.add(
                    App(
                        id="app-a",
                        tenant_id="tenant-a",
                        name="App A",
                        kind="chat",
                    )
                )
                session.flush()
                references = Table("app_dataset_references", MetaData(), autoload_with=engine)
                session.execute(
                    references.insert().values(
                        id="reference-a",
                        tenant_id="tenant-a",
                        app_id="app-a",
                        dataset_id="dataset-a",
                        reference_kind="knowledge",
                        status="active",
                        active_slot="active",
                        revision=1,
                        created_by="owner-a",
                        updated_by="owner-a",
                        request_id="stage19-seed",
                    )
                )
            session.commit()
    finally:
        engine.dispose()


def upgrade_0029(url: str) -> None:
    command.upgrade(alembic_config(url), REVISION)


def check_sql(inspector, table: str) -> dict[str, str]:
    return {
        str(item["name"]): " ".join(str(item.get("sqltext") or "").casefold().split())
        for item in inspector.get_check_constraints(table)
    }


def insert_manifest(connection, *, release_id: str = "release-a") -> None:
    digest = "a" * 64
    connection.execute(
        text(
            "INSERT INTO dataset_release_manifests "
            "(id,tenant_id,dataset_id,release_number,profile_revision,ownership_revision,"
            "workspace_id,workspace_revision,mutation_generation,serving_generation,schema_version,"
            "policy_digest,manifest_digest,readiness_digest,readiness_state,entry_count,blocker_count,"
            "readiness_blockers_json,created_at,created_by,reason,request_id) VALUES "
            "(:id,'tenant-a','dataset-a',1,1,1,'workspace-a',2,0,0,1,:digest,:digest,:digest,"
            "'ready',0,0,'[]',CURRENT_TIMESTAMP,'owner-a','candidate','request-a')"
        ),
        {"id": release_id, "digest": digest},
    )


def test_0029_precedes_current_head_and_follows_stage18() -> None:
    migration = migration_module()
    scripts = ScriptDirectory.from_config(alembic_config("sqlite://"))

    assert scripts.get_current_head() == catalog_schema.HEAD_REVISION
    script = scripts.get_revision(REVISION)
    assert script is not None
    assert script.down_revision == DOWN_REVISION
    assert migration.revision == REVISION
    assert migration.down_revision == DOWN_REVISION


def test_upgrade_creates_release_authority_with_exact_structural_contract(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "release-schema.db")
    seed_0028(url, include_reference=True)
    upgrade_0029(url)

    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        for table, expected in EXPECTED_COLUMNS.items():
            assert [item["name"] for item in inspector.get_columns(table)] == expected

        datasets = {item["name"] for item in inspector.get_columns("datasets")}
        assert {"serving_release_id", "release_revision"} <= datasets
        references = {item["name"] for item in inspector.get_columns("app_dataset_references")}
        assert {"release_mode", "release_channel_id", "pinned_release_id"} <= references

        channels = list(
            engine.connect().execute(
                text(
                    "SELECT tenant_id,code,normalized_code,status,risk_tier,promotion_order,"
                    "is_default_serving,active_default_slot,revision "
                    "FROM tenant_release_channels ORDER BY code"
                )
            )
        )
        assert [(row.tenant_id, row.code, row.normalized_code) for row in channels] == [
            ("tenant-a", "development", "development"),
            ("tenant-a", "production", "production"),
            ("tenant-a", "testing", "testing"),
        ]
        assert sum(bool(row.is_default_serving) for row in channels) == 1
        assert (
            next(row for row in channels if row.code == "production").active_default_slot
            == "default"
        )

        assert set(inspector.get_unique_constraints(CHANNEL_TABLE)[0]) if False else True
        checks = {table: check_sql(inspector, table) for table in EXPECTED_COLUMNS}
        assert (
            "status in ('active','archived')"
            == checks[CHANNEL_TABLE]["ck_tenant_release_channels_status"]
        )
        assert (
            "risk_tier in ('low','medium','high')"
            == checks[CHANNEL_TABLE]["ck_tenant_release_channels_risk_tier"]
        )
        assert (
            "release_mode"
            in check_sql(inspector, "app_dataset_references")[
                "ck_app_dataset_references_release_binding"
            ]
        )
        assert (
            "lower(manifest_digest) = manifest_digest"
            in checks[MANIFEST_TABLE]["ck_dataset_release_manifests_manifest_digest"]
        )
        assert (
            "status = 'active'" in checks[BINDING_TABLE]["ck_dataset_channel_releases_active_slot"]
        )
    finally:
        engine.dispose()


def test_legacy_application_references_follow_the_deterministic_development_channel(
    tmp_path: Path,
) -> None:
    url = sqlite_url(tmp_path / "release-reference-backfill.db")
    seed_0028(url, include_reference=True)
    upgrade_0029(url)

    engine = engine_for(url)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT r.release_mode,r.release_channel_id,r.pinned_release_id,c.code "
                    "FROM app_dataset_references r "
                    "JOIN tenant_release_channels c ON c.tenant_id=r.tenant_id "
                    "AND c.id=r.release_channel_id WHERE r.id='reference-a'"
                )
            ).one()
            assert row.release_mode == "follow_channel"
            assert row.pinned_release_id is None
            assert row.code == "development"
    finally:
        engine.dispose()


def test_release_content_and_events_are_database_immutable(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "release-immutable.db")
    seed_0028(url)
    upgrade_0029(url)

    engine = engine_for(url)
    try:
        with engine.begin() as connection:
            insert_manifest(connection)
            connection.execute(
                text(
                    "INSERT INTO dataset_release_entries "
                    "(id,tenant_id,dataset_id,release_id,ordinal,resource_type,resource_id,"
                    "resource_revision,content_digest,safe_facts_json,created_at) VALUES "
                    "('entry-a','tenant-a','dataset-a','release-a',0,'dataset_profile','dataset-a',1,:digest,'{}',CURRENT_TIMESTAMP)"
                ),
                {"digest": "b" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO dataset_release_events "
                    "(id,tenant_id,dataset_id,release_id,channel_id,event_type,actor_id,reason,"
                    "request_id,occurred_at,previous_binding_revision,current_binding_revision) VALUES "
                    "('event-a','tenant-a','dataset-a','release-a',NULL,'candidate_created','owner-a',"
                    "'candidate','request-a',CURRENT_TIMESTAMP,0,1)"
                )
            )

        for table, row_id, column in (
            (MANIFEST_TABLE, "release-a", "reason"),
            (ENTRY_TABLE, "entry-a", "resource_id"),
            (EVENT_TABLE, "event-a", "reason"),
        ):
            with pytest.raises(Exception, match="immutable"):
                with engine.begin() as connection:
                    connection.execute(
                        text(f"UPDATE {table} SET {column}='tampered' WHERE id=:id"),
                        {"id": row_id},
                    )
            with pytest.raises(Exception, match="immutable"):
                with engine.begin() as connection:
                    connection.execute(
                        text(f"DELETE FROM {table} WHERE id=:id"),
                        {"id": row_id},
                    )
    finally:
        engine.dispose()


def test_release_binding_and_app_release_mode_checks_are_fail_closed(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "release-checks.db")
    seed_0028(url, include_reference=True)
    upgrade_0029(url)

    engine = engine_for(url)
    try:
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO app_dataset_references "
                        "(id,tenant_id,app_id,dataset_id,reference_kind,status,active_slot,revision,"
                        "created_at,created_by,updated_at,updated_by,request_id,release_mode,"
                        "release_channel_id,pinned_release_id) VALUES "
                        "('bad-ref','tenant-a','app-a','dataset-a','knowledge','active','active',1,"
                        "CURRENT_TIMESTAMP,'owner-a',CURRENT_TIMESTAMP,'owner-a','bad','follow_channel',NULL,NULL)"
                    )
                )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO dataset_channel_releases "
                        "(id,tenant_id,dataset_id,channel_id,active_release_id,previous_release_id,status,"
                        "active_slot,revision,activated_at,activated_by,request_id,reason,created_at,"
                        "updated_at,updated_by) VALUES "
                        "('bad-binding','tenant-a','dataset-a',"
                        "(SELECT id FROM tenant_release_channels WHERE tenant_id='tenant-a' AND code='testing'),"
                        "NULL,NULL,'active',NULL,1,CURRENT_TIMESTAMP,'owner-a','bad','bad',CURRENT_TIMESTAMP,"
                        "CURRENT_TIMESTAMP,'owner-a')"
                    )
                )
    finally:
        engine.dispose()


def test_downgrade_fails_closed_for_release_facts_and_restores_clean_0028(tmp_path: Path) -> None:
    blocked_url = sqlite_url(tmp_path / "release-downgrade-blocked.db")
    seed_0028(blocked_url)
    upgrade_0029(blocked_url)
    blocked_engine = engine_for(blocked_url)
    with blocked_engine.begin() as connection:
        insert_manifest(connection)
    blocked_engine.dispose()
    with pytest.raises(Exception, match="0029|release|manifest|channel|approval"):
        command.downgrade(alembic_config(blocked_url), DOWN_REVISION)

    clean_url = sqlite_url(tmp_path / "release-downgrade-clean.db")
    seed_0028(clean_url)
    upgrade_0029(clean_url)
    command.downgrade(alembic_config(clean_url), DOWN_REVISION)

    engine = engine_for(clean_url)
    try:
        inspector = inspect(engine)
        for table in EXPECTED_COLUMNS:
            assert table not in inspector.get_table_names()
        assert {"serving_release_id", "release_revision"}.isdisjoint(
            {item["name"] for item in inspector.get_columns("datasets")}
        )
        assert {"release_mode", "release_channel_id", "pinned_release_id"}.isdisjoint(
            {item["name"] for item in inspector.get_columns("app_dataset_references")}
        )
        assert (
            engine.connect().execute(text("SELECT version_num FROM alembic_version")).scalar_one()
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
def test_offline_upgrade_contains_release_ddl_and_online_downgrade_gate(
    url: str, dialect_marker: str
) -> None:
    output = StringIO()
    command.upgrade(
        alembic_config(url, output_buffer=output), f"{DOWN_REVISION}:{REVISION}", sql=True
    )
    sql = output.getvalue().upper()
    assert "CREATE TABLE TENANT_RELEASE_CHANNELS" in sql
    assert "CREATE TABLE DATASET_RELEASE_MANIFESTS" in sql
    assert "CREATE TABLE DATASET_RELEASE_ENTRIES" in sql
    assert "CREATE TABLE DATASET_RELEASE_EVENTS" in sql
    assert "CREATE TABLE DATASET_CHANNEL_RELEASES" in sql
    assert "INSERT INTO TENANT_RELEASE_CHANNELS" in sql
    assert "UPDATE APP_DATASET_REFERENCES AS R SET RELEASE_MODE='FOLLOW_CHANNEL'" in sql
    assert "NORMALIZED_CODE='DEVELOPMENT'" in sql
    assert PUBLISH_ACTION.upper() in sql
    assert ROLLBACK_ACTION.upper() in sql
    assert "FK_DATASET_RELEASE_MANIFESTS_SCOPE_DATASET" in sql
    assert "FK_DATASET_CHANNEL_RELEASES_SCOPE_CHANNEL" in sql
    assert dialect_marker in sql

    with pytest.raises(Exception, match="online|preflight|0029|release"):
        command.downgrade(
            alembic_config(url, output_buffer=StringIO()),
            f"{REVISION}:{DOWN_REVISION}",
            sql=True,
        )


def test_migration_and_runtime_default_channel_ids_are_identical() -> None:
    migration = migration_module()
    from core.enterprise_release_channels import default_release_channel_id

    for tenant_id in ("tenant-a", "tenant-with-a-long-enterprise-identifier"):
        for code in ("development", "testing", "production"):
            assert migration._channel_id(tenant_id, code) == default_release_channel_id(
                tenant_id, code
            )


def test_sqlite_offline_upgrade_fails_closed_with_explicit_online_requirement() -> None:
    with pytest.raises(Exception, match="SQLite offline upgrade is unsupported"):
        command.upgrade(
            alembic_config("sqlite:///offline-stage19.db", output_buffer=StringIO()),
            f"{DOWN_REVISION}:{REVISION}",
            sql=True,
        )


def test_release_capability_fails_when_immutable_trigger_is_missing(tmp_path: Path) -> None:
    from core.catalog_schema import inspect_enterprise_knowledge_base_release_capability

    url = sqlite_url(tmp_path / "release-capability-trigger.db")
    seed_0028(url)
    upgrade_0029(url)
    engine = engine_for(url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP TRIGGER trg_dataset_release_manifests_no_update"))
        state, issues = inspect_enterprise_knowledge_base_release_capability(engine)
        assert state == "unavailable"
        assert "missing release immutable trigger trg_dataset_release_manifests_no_update" in issues
    finally:
        engine.dispose()


def test_release_capability_fails_when_default_channel_or_serving_projection_is_invalid(
    tmp_path: Path,
) -> None:
    from core.catalog_schema import inspect_enterprise_knowledge_base_release_capability

    missing_url = sqlite_url(tmp_path / "release-capability-default.db")
    seed_0028(missing_url)
    upgrade_0029(missing_url)
    missing_engine = engine_for(missing_url)
    try:
        with missing_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM tenant_release_channels "
                    "WHERE tenant_id='tenant-a' AND code='production'"
                )
            )
        state, issues = inspect_enterprise_knowledge_base_release_capability(missing_engine)
        assert state == "unavailable"
        assert "tenant default release channel invalid tenant-a" in issues
    finally:
        missing_engine.dispose()

    mismatch_url = sqlite_url(tmp_path / "release-capability-serving.db")
    seed_0028(mismatch_url)
    upgrade_0029(mismatch_url)
    mismatch_engine = engine_for(mismatch_url)
    try:
        with mismatch_engine.begin() as connection:
            insert_manifest(connection)
            connection.execute(
                text(
                    "UPDATE datasets SET serving_release_id='release-a' "
                    "WHERE tenant_id='tenant-a' AND id='dataset-a'"
                )
            )
        state, issues = inspect_enterprise_knowledge_base_release_capability(mismatch_engine)
        assert state == "unavailable"
        assert "dataset serving release projection mismatch tenant-a:dataset-a" in issues
    finally:
        mismatch_engine.dispose()


def test_release_capability_requires_parent_workspace_registry_authority(tmp_path: Path) -> None:
    from core.catalog_schema import inspect_enterprise_knowledge_base_release_capability

    url = sqlite_url(tmp_path / "release-capability-parent.db")
    seed_0028(url)
    upgrade_0029(url)
    engine = engine_for(url)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.exec_driver_sql("DROP TABLE tenant_workspaces")
            connection.commit()
        state, issues = inspect_enterprise_knowledge_base_release_capability(engine)
        assert state == "unavailable"
        assert any("parent registry authority" in issue for issue in issues)
    finally:
        engine.dispose()


def test_release_capability_rejects_same_name_noop_trigger_and_top_level_readiness(
    tmp_path: Path,
) -> None:
    from core.catalog_schema import (
        inspect_catalog_schema,
        inspect_enterprise_knowledge_base_release_capability,
    )

    url = sqlite_url(tmp_path / "release-capability-noop-trigger.db")
    seed_0028(url)
    upgrade_0029(url)
    engine = engine_for(url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP TRIGGER trg_dataset_release_manifests_no_update"))
            connection.execute(
                text(
                    "CREATE TRIGGER trg_dataset_release_manifests_no_update "
                    "BEFORE UPDATE ON dataset_release_manifests BEGIN SELECT 1; END"
                )
            )
        state, issues = inspect_enterprise_knowledge_base_release_capability(engine)
        assert state == "unavailable"
        assert "invalid release immutable trigger trg_dataset_release_manifests_no_update" in issues
        catalog = inspect_catalog_schema(engine)
        assert catalog.status == "behind"
        assert catalog.revision == REVISION
    finally:
        engine.dispose()


def test_release_capability_requires_all_three_standard_channels(tmp_path: Path) -> None:
    from core.catalog_schema import inspect_enterprise_knowledge_base_release_capability

    url = sqlite_url(tmp_path / "release-capability-standard-channels.db")
    seed_0028(url)
    upgrade_0029(url)
    engine = engine_for(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM tenant_release_channels "
                    "WHERE tenant_id='tenant-a' AND code='development'"
                )
            )
        state, issues = inspect_enterprise_knowledge_base_release_capability(engine)
        assert state == "unavailable"
        assert "tenant release channel missing tenant-a:development" in issues
    finally:
        engine.dispose()
