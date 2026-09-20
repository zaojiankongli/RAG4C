from __future__ import annotations

from importlib import import_module
from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from core import catalog_schema
from tests.test_enterprise_automation_workflows_migration import (
    _upgrade as upgrade_0035,
    alembic_config,
    engine_for,
    sqlite_url,
)

REVISION = "0036_enterprise_knowledge_serving_reliability"
DOWN_REVISION = "0035_enterprise_automation_workflows"
TABLES = {
    "tenant_knowledge_serving_profiles",
    "tenant_knowledge_serving_policy_revisions",
    "tenant_knowledge_serving_snapshots",
    "tenant_knowledge_serving_stage_facts",
    "tenant_knowledge_serving_evidence_links",
    "tenant_knowledge_serving_events",
}
EXPECTED_COLUMNS = {
    "tenant_knowledge_serving_profiles": (
        "id",
        "tenant_id",
        "workspace_id",
        "dataset_id",
        "name",
        "normalized_name",
        "status",
        "active_profile_key",
        "revision",
        "current_policy_revision_id",
        "current_snapshot_id",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "archived_at",
        "archived_by",
    ),
    "tenant_knowledge_serving_policy_revisions": (
        "id",
        "tenant_id",
        "profile_id",
        "revision",
        "max_source_staleness_seconds",
        "max_parse_lag_seconds",
        "max_index_lag_seconds",
        "max_failed_document_count",
        "max_pending_index_count",
        "require_current_release",
        "require_passing_certification",
        "policy_digest",
        "created_at",
        "created_by",
    ),
    "tenant_knowledge_serving_snapshots": (
        "id",
        "tenant_id",
        "profile_id",
        "policy_revision_id",
        "observation_key",
        "state",
        "source_count",
        "ready_source_count",
        "stale_source_count",
        "active_document_count",
        "failed_document_count",
        "pending_index_count",
        "expected_serving_generation",
        "observed_serving_generation",
        "current_release_id",
        "current_certification_id",
        "stage_count",
        "ready_stage_count",
        "blocked_stage_count",
        "snapshot_digest",
        "as_of",
        "created_at",
        "created_by",
    ),
    "tenant_knowledge_serving_stage_facts": (
        "id",
        "tenant_id",
        "profile_id",
        "snapshot_id",
        "stage_code",
        "sequence",
        "state",
        "item_count",
        "ready_count",
        "warning_count",
        "pending_count",
        "error_count",
        "lag_seconds",
        "expected_revision",
        "observed_revision",
        "expected_digest",
        "observed_digest",
        "safe_error_code",
        "safe_error",
        "stage_digest",
        "observed_at",
    ),
    "tenant_knowledge_serving_evidence_links": (
        "id",
        "tenant_id",
        "profile_id",
        "snapshot_id",
        "stage_fact_id",
        "evidence_kind",
        "resource_id",
        "resource_revision",
        "resource_digest",
        "route_code",
        "safe_label",
        "evidence_digest",
        "created_at",
    ),
    "tenant_knowledge_serving_events": (
        "id",
        "tenant_id",
        "profile_id",
        "snapshot_id",
        "stream_key",
        "sequence",
        "event_type",
        "previous_event_digest",
        "event_digest",
        "actor_id",
        "request_id",
        "safe_snapshot_json",
        "occurred_at",
    ),
}


def migration_module():
    try:
        return import_module(
            "catalog_migrations.versions.0036_enterprise_knowledge_serving_reliability"
        )
    except ModuleNotFoundError as exc:
        pytest.fail(f"Stage26 migration is missing: {exc}")


def upgrade_0036(url: str) -> None:
    command.upgrade(alembic_config(url), REVISION)


def _upgrade(url: str) -> None:
    upgrade_0035(url)
    upgrade_0036(url)


def _foreign_keys(inspector, table: str):
    return {
        str(item["name"]): (
            tuple(item.get("constrained_columns") or ()),
            str(item.get("referred_table")),
            tuple(item.get("referred_columns") or ()),
        )
        for item in inspector.get_foreign_keys(table)
        if item.get("name")
    }


def _checks(inspector, table: str):
    return {
        str(item["name"]): " ".join(str(item.get("sqltext") or "").casefold().split())
        for item in inspector.get_check_constraints(table)
        if item.get("name")
    }


def _indexes(inspector, table: str):
    return {
        str(item["name"]): tuple(item.get("column_names") or ())
        for item in inspector.get_indexes(table)
        if item.get("name")
    }


def test_0036_precedes_the_current_catalog_head_and_follows_0035() -> None:
    migration = migration_module()
    scripts = ScriptDirectory.from_config(alembic_config("sqlite://"))
    assert scripts.get_current_head() == catalog_schema.HEAD_REVISION
    script = scripts.get_revision(REVISION)
    assert script is not None
    assert script.down_revision == DOWN_REVISION
    assert migration.revision == REVISION
    assert migration.down_revision == DOWN_REVISION


def test_upgrade_creates_exactly_six_tables_with_expected_columns(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "knowledge-serving.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        actual = {
            name
            for name in inspector.get_table_names()
            if name.startswith("tenant_knowledge_serving_")
        }
        assert actual == TABLES
        for table, columns in EXPECTED_COLUMNS.items():
            assert tuple(item["name"] for item in inspector.get_columns(table)) == columns
        assert (
            engine.connect().execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == REVISION
        )
    finally:
        engine.dispose()


def test_serving_foreign_keys_are_tenant_leading_and_pointer_owned(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "knowledge-serving-fks.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        expected = {
            "tenant_knowledge_serving_profiles": {
                "fk_tenant_knowledge_serving_profiles_tenant": (("tenant_id",), "tenants", ("id",)),
                "fk_tenant_knowledge_serving_profiles_workspace": (
                    ("tenant_id", "workspace_id"),
                    "tenant_workspaces",
                    ("tenant_id", "id"),
                ),
                "fk_tenant_knowledge_serving_profiles_dataset": (
                    ("tenant_id", "dataset_id"),
                    "datasets",
                    ("tenant_id", "id"),
                ),
                "fk_tenant_knowledge_serving_profiles_creator": (
                    ("tenant_id", "created_by"),
                    "tenant_members",
                    ("tenant_id", "account_id"),
                ),
                "fk_tenant_knowledge_serving_profiles_updater": (
                    ("tenant_id", "updated_by"),
                    "tenant_members",
                    ("tenant_id", "account_id"),
                ),
                "fk_tenant_knowledge_serving_profiles_archiver": (
                    ("tenant_id", "archived_by"),
                    "tenant_members",
                    ("tenant_id", "account_id"),
                ),
                "fk_tenant_knowledge_serving_profiles_current_policy": (
                    ("tenant_id", "id", "current_policy_revision_id"),
                    "tenant_knowledge_serving_policy_revisions",
                    ("tenant_id", "profile_id", "id"),
                ),
                "fk_tenant_knowledge_serving_profiles_current_snapshot": (
                    ("tenant_id", "id", "current_snapshot_id"),
                    "tenant_knowledge_serving_snapshots",
                    ("tenant_id", "profile_id", "id"),
                ),
            },
            "tenant_knowledge_serving_policy_revisions": {
                "fk_tenant_knowledge_serving_policy_revisions_profile": (
                    ("tenant_id", "profile_id"),
                    "tenant_knowledge_serving_profiles",
                    ("tenant_id", "id"),
                ),
                "fk_tenant_knowledge_serving_policy_revisions_creator": (
                    ("tenant_id", "created_by"),
                    "tenant_members",
                    ("tenant_id", "account_id"),
                ),
            },
            "tenant_knowledge_serving_snapshots": {
                "fk_tenant_knowledge_serving_snapshots_profile": (
                    ("tenant_id", "profile_id"),
                    "tenant_knowledge_serving_profiles",
                    ("tenant_id", "id"),
                ),
                "fk_tenant_knowledge_serving_snapshots_policy": (
                    ("tenant_id", "profile_id", "policy_revision_id"),
                    "tenant_knowledge_serving_policy_revisions",
                    ("tenant_id", "profile_id", "id"),
                ),
                "fk_tenant_knowledge_serving_snapshots_release": (
                    ("tenant_id", "current_release_id"),
                    "dataset_release_manifests",
                    ("tenant_id", "id"),
                ),
                "fk_tenant_knowledge_serving_snapshots_certification": (
                    ("tenant_id", "current_certification_id"),
                    "dataset_release_quality_certifications",
                    ("tenant_id", "id"),
                ),
                "fk_tenant_knowledge_serving_snapshots_creator": (
                    ("tenant_id", "created_by"),
                    "tenant_members",
                    ("tenant_id", "account_id"),
                ),
            },
            "tenant_knowledge_serving_stage_facts": {
                "fk_tenant_knowledge_serving_stage_facts_profile": (
                    ("tenant_id", "profile_id"),
                    "tenant_knowledge_serving_profiles",
                    ("tenant_id", "id"),
                ),
                "fk_tenant_knowledge_serving_stage_facts_snapshot": (
                    ("tenant_id", "profile_id", "snapshot_id"),
                    "tenant_knowledge_serving_snapshots",
                    ("tenant_id", "profile_id", "id"),
                ),
            },
            "tenant_knowledge_serving_evidence_links": {
                "fk_tenant_knowledge_serving_evidence_links_profile": (
                    ("tenant_id", "profile_id"),
                    "tenant_knowledge_serving_profiles",
                    ("tenant_id", "id"),
                ),
                "fk_tenant_knowledge_serving_evidence_links_snapshot": (
                    ("tenant_id", "profile_id", "snapshot_id"),
                    "tenant_knowledge_serving_snapshots",
                    ("tenant_id", "profile_id", "id"),
                ),
                "fk_tenant_knowledge_serving_evidence_links_stage_fact": (
                    ("tenant_id", "profile_id", "snapshot_id", "stage_fact_id"),
                    "tenant_knowledge_serving_stage_facts",
                    ("tenant_id", "profile_id", "snapshot_id", "id"),
                ),
            },
            "tenant_knowledge_serving_events": {
                "fk_tenant_knowledge_serving_events_profile": (
                    ("tenant_id", "profile_id"),
                    "tenant_knowledge_serving_profiles",
                    ("tenant_id", "id"),
                ),
                "fk_tenant_knowledge_serving_events_snapshot": (
                    ("tenant_id", "profile_id", "snapshot_id"),
                    "tenant_knowledge_serving_snapshots",
                    ("tenant_id", "profile_id", "id"),
                ),
                "fk_tenant_knowledge_serving_events_actor": (
                    ("tenant_id", "actor_id"),
                    "tenant_members",
                    ("tenant_id", "account_id"),
                ),
            },
        }
        for table, contract in expected.items():
            assert _foreign_keys(inspector, table) == contract
            assert all(columns[0] == "tenant_id" for columns, _, _ in contract.values())
    finally:
        engine.dispose()


def test_serving_uniques_checks_and_indexes_are_canonical(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "knowledge-serving-contract.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        expected_uniques = {
            "tenant_knowledge_serving_profiles": {
                "uq_tenant_knowledge_serving_profiles_scope_id": ("tenant_id", "id"),
                "uq_tenant_knowledge_serving_profiles_dataset_id": (
                    "tenant_id",
                    "dataset_id",
                ),
                "uq_tenant_knowledge_serving_profiles_active_key": (
                    "tenant_id",
                    "active_profile_key",
                ),
            },
            "tenant_knowledge_serving_policy_revisions": {
                "uq_tenant_knowledge_serving_policy_revisions_scope_id": ("tenant_id", "id"),
                "uq_tenant_knowledge_serving_policy_revisions_profile_scope": (
                "tenant_id",
                "profile_id",
                "id",
            ),
            "uq_tenant_knowledge_serving_policy_revisions_profile_revision": (
                "tenant_id",
                "profile_id",
                "revision",
            ),
            },
            "tenant_knowledge_serving_snapshots": {
                "uq_tenant_knowledge_serving_snapshots_scope_id": ("tenant_id", "id"),
                "uq_tenant_knowledge_serving_snapshots_profile_scope": (
                "tenant_id",
                "profile_id",
                "id",
            ),
            "uq_tenant_knowledge_serving_snapshots_observation": (
                "tenant_id",
                "profile_id",
                "observation_key",
            ),
                "uq_tenant_knowledge_serving_snapshots_digest": ("tenant_id", "snapshot_digest"),
            },
            "tenant_knowledge_serving_stage_facts": {
                "uq_tenant_knowledge_serving_stage_facts_scope_id": ("tenant_id", "id"),
                "uq_tenant_knowledge_serving_stage_facts_profile_snapshot_id": (
                    "tenant_id",
                    "profile_id",
                    "snapshot_id",
                    "id",
                ),
                "uq_tenant_knowledge_serving_stage_facts_stage": (
                    "tenant_id",
                    "snapshot_id",
                    "stage_code",
                ),
                "uq_tenant_knowledge_serving_stage_facts_sequence": (
                    "tenant_id",
                    "snapshot_id",
                    "sequence",
                ),
            },
            "tenant_knowledge_serving_evidence_links": {
                "uq_tenant_knowledge_serving_evidence_links_scope_id": ("tenant_id", "id"),
                "uq_tenant_knowledge_serving_evidence_links_digest": (
                    "tenant_id",
                    "evidence_digest",
                ),
            },
            "tenant_knowledge_serving_events": {
                "uq_tenant_knowledge_serving_events_scope_id": ("tenant_id", "id"),
                "uq_tenant_knowledge_serving_events_stream_sequence": (
                    "tenant_id",
                    "stream_key",
                    "sequence",
                ),
                "uq_tenant_knowledge_serving_events_digest": ("tenant_id", "event_digest"),
            },
        }
        for table, contract in expected_uniques.items():
            actual = {
                str(item["name"]): tuple(item.get("column_names") or ())
                for item in inspector.get_unique_constraints(table)
                if item.get("name")
            }
            for name, columns in contract.items():
                assert actual.get(name) == columns, (table, name, actual)

        checks = {table: _checks(inspector, table) for table in TABLES}
        expected_checks = {
            "tenant_knowledge_serving_profiles": {
                "ck_tenant_knowledge_serving_profiles_status",
                "ck_tenant_knowledge_serving_profiles_revision",
                "ck_tenant_knowledge_serving_profiles_active_identity",
                "ck_tenant_knowledge_serving_profiles_lifecycle",
            },
            "tenant_knowledge_serving_policy_revisions": {
                "ck_tenant_knowledge_serving_policy_revisions_thresholds",
                "ck_tenant_knowledge_serving_policy_revisions_digest",
            },
            "tenant_knowledge_serving_snapshots": {
                "ck_tenant_knowledge_serving_snapshots_state",
                "ck_tenant_knowledge_serving_snapshots_counts",
                "ck_tenant_knowledge_serving_snapshots_digest",
            },
            "tenant_knowledge_serving_stage_facts": {
                "ck_tenant_knowledge_serving_stage_facts_stage",
                "ck_tenant_knowledge_serving_stage_facts_sequence",
                "ck_tenant_knowledge_serving_stage_facts_state",
                "ck_tenant_knowledge_serving_stage_facts_counts",
                "ck_tenant_knowledge_serving_stage_facts_error",
                "ck_tenant_knowledge_serving_stage_facts_digest",
            },
            "tenant_knowledge_serving_evidence_links": {
                "ck_tenant_knowledge_serving_evidence_links_kind",
                "ck_tenant_knowledge_serving_evidence_links_route",
                "ck_tenant_knowledge_serving_evidence_links_kind_route",
                "ck_tenant_knowledge_serving_evidence_links_digest",
            },
            "tenant_knowledge_serving_events": {
                "ck_tenant_knowledge_serving_events_type",
                "ck_tenant_knowledge_serving_events_sequence",
                "ck_tenant_knowledge_serving_events_digest",
                "ck_tenant_knowledge_serving_events_hash_chain",
                "ck_tenant_knowledge_serving_events_snapshot",
            },
        }
        for table, names in expected_checks.items():
            assert names <= set(checks[table]), (table, checks[table])
        assert all(
            value
            in checks["tenant_knowledge_serving_stage_facts"][
                "ck_tenant_knowledge_serving_stage_facts_stage"
            ]
            for value in ("source", "parse", "chunk", "index", "serve")
        )
        assert all(
            value
            in checks["tenant_knowledge_serving_stage_facts"][
                "ck_tenant_knowledge_serving_stage_facts_state"
            ]
            for value in ("ready", "lagging", "blocked", "missing", "unavailable")
        )
        assert (
            "service_recovered"
            in checks["tenant_knowledge_serving_events"]["ck_tenant_knowledge_serving_events_type"]
        )
        assert (
            "source_sync_run"
            in checks["tenant_knowledge_serving_evidence_links"][
                "ck_tenant_knowledge_serving_evidence_links_kind"
            ]
        )

        expected_indexes = {
            "tenant_knowledge_serving_profiles": (
                "ix_tenant_knowledge_serving_profiles_tenant_status_updated",
                ("tenant_id", "status", "updated_at", "id"),
            ),
            "tenant_knowledge_serving_policy_revisions": (
                "ix_tenant_knowledge_serving_policy_revisions_profile_created",
                ("tenant_id", "profile_id", "created_at", "id"),
            ),
            "tenant_knowledge_serving_snapshots": (
                "ix_tenant_knowledge_serving_snapshots_profile_as_of",
                ("tenant_id", "profile_id", "as_of", "id"),
            ),
            "tenant_knowledge_serving_stage_facts": (
                "ix_tenant_knowledge_serving_stage_facts_snapshot_sequence",
                ("tenant_id", "snapshot_id", "sequence", "id"),
            ),
            "tenant_knowledge_serving_evidence_links": (
                "ix_tenant_knowledge_serving_evidence_links_snapshot_kind",
                ("tenant_id", "snapshot_id", "evidence_kind", "id"),
            ),
            "tenant_knowledge_serving_events": (
                "ix_tenant_knowledge_serving_events_profile_time",
                ("tenant_id", "profile_id", "occurred_at", "id"),
            ),
        }
        for table, (name, columns) in expected_indexes.items():
            assert _indexes(inspector, table).get(name) == columns
    finally:
        engine.dispose()


def test_serving_immutable_guards_and_event_predecessor_validation(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "knowledge-serving-guards.db")
    _upgrade(url)
    engine = engine_for(url)
    immutable_tables = (
        "tenant_knowledge_serving_policy_revisions",
        "tenant_knowledge_serving_snapshots",
        "tenant_knowledge_serving_stage_facts",
        "tenant_knowledge_serving_evidence_links",
        "tenant_knowledge_serving_events",
    )
    try:
        with engine.connect() as connection:
            names = {
                str(row[0])
                for row in connection.execute(
                    text(
                        "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_tenant_knowledge_serving_%'"
                    )
                )
            }
        expected = {
            f"trg_{table}_no_{operation}"
            for table in immutable_tables
            for operation in ("update", "delete")
        }
        expected.add("trg_tenant_knowledge_serving_events_validate_insert")
        assert expected <= names

        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenant_knowledge_serving_policy_revisions "
                    "(id,tenant_id,profile_id,revision,max_source_staleness_seconds,max_parse_lag_seconds,max_index_lag_seconds,max_failed_document_count,max_pending_index_count,require_current_release,require_passing_certification,policy_digest,created_at,created_by) "
                    "VALUES ('policy-guard','tenant-a','profile-guard',1,1,1,0,0,0,0,0,:digest,CURRENT_TIMESTAMP,'owner-a')"
                ),
                {"digest": "a" * 64},
            )
            with pytest.raises(Exception, match="immutable|serving"):
                connection.execute(
                    text(
                        "UPDATE tenant_knowledge_serving_policy_revisions SET revision=2 WHERE id='policy-guard'"
                    )
                )
            with pytest.raises(Exception, match="immutable|serving"):
                connection.execute(
                    text(
                        "DELETE FROM tenant_knowledge_serving_policy_revisions WHERE id='policy-guard'"
                    )
                )
            connection.execute(
                text(
                    "INSERT INTO tenant_knowledge_serving_events "
                    "(id,tenant_id,profile_id,snapshot_id,stream_key,sequence,event_type,previous_event_digest,event_digest,actor_id,request_id,safe_snapshot_json,occurred_at) "
                    "VALUES ('event-guard-1','tenant-a','profile-guard',NULL,'profile-guard',1,'profile_created',NULL,:digest,'owner-a','request-1','{}',CURRENT_TIMESTAMP)"
                ),
                {"digest": "b" * 64},
            )
            with pytest.raises(Exception, match="previous|chain|event"):
                connection.execute(
                    text(
                        "INSERT INTO tenant_knowledge_serving_events "
                        "(id,tenant_id,profile_id,snapshot_id,stream_key,sequence,event_type,previous_event_digest,event_digest,actor_id,request_id,safe_snapshot_json,occurred_at) "
                        "VALUES ('event-guard-2','tenant-a','profile-guard',NULL,'profile-guard',2,'snapshot_recorded',:previous,:digest,'owner-a','request-2','{}',CURRENT_TIMESTAMP)"
                    ),
                    {"previous": "c" * 64, "digest": "d" * 64},
                )
    finally:
        engine.dispose()


def test_offline_ddl_supports_mysql_postgresql_and_sqlite_fails_closed() -> None:
    for url, marker in (
        ("mysql+pymysql://u:p@localhost/rag4c", "DATETIME(6)"),
        ("postgresql+psycopg://u:p@localhost/rag4c", "TIMESTAMP"),
    ):
        output = StringIO()
        command.upgrade(
            alembic_config(url, output_buffer=output), f"{DOWN_REVISION}:{REVISION}", sql=True
        )
        sql = output.getvalue().upper()
        for table in TABLES:
            assert f"CREATE TABLE {table.upper()}" in sql
        assert "CURRENT_POLICY_REVISION_ID" in sql and "STAGE_CODE" in sql and "EVENT_DIGEST" in sql
        assert marker in sql
    with pytest.raises(Exception, match="online|SQLite|sqlite"):
        command.upgrade(
            alembic_config("sqlite:///knowledge-serving-offline.db", output_buffer=StringIO()),
            f"{DOWN_REVISION}:{REVISION}",
            sql=True,
        )


def test_migration_fails_closed_for_unknown_dialect(monkeypatch: pytest.MonkeyPatch) -> None:
    migration = migration_module()
    monkeypatch.setattr(migration, "_dialect_name", lambda: "oracle")
    monkeypatch.setattr(migration.context, "is_offline_mode", lambda: False)
    monkeypatch.setattr(migration.op, "create_table", lambda *args, **kwargs: None)
    monkeypatch.setattr(migration.op, "create_index", lambda *args, **kwargs: None)
    monkeypatch.setattr(migration.op, "execute", lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError, match="unsupported|dialect"):
        migration.upgrade()


def test_clean_downgrade_removes_stage26_and_nonempty_authority_blocks(tmp_path: Path) -> None:
    clean_url = sqlite_url(tmp_path / "knowledge-serving-clean-down.db")
    _upgrade(clean_url)
    command.downgrade(alembic_config(clean_url), DOWN_REVISION)
    engine = engine_for(clean_url)
    try:
        assert TABLES.isdisjoint(inspect(engine).get_table_names())
        assert (
            engine.connect().execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == DOWN_REVISION
        )
    finally:
        engine.dispose()

    blocked_url = sqlite_url(tmp_path / "knowledge-serving-blocked-down.db")
    _upgrade(blocked_url)
    blocked_engine = engine_for(blocked_url)
    try:
        with blocked_engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        with blocked_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenant_knowledge_serving_profiles "
                    "(id,tenant_id,workspace_id,dataset_id,name,normalized_name,status,active_profile_key,revision,current_policy_revision_id,current_snapshot_id,created_at,created_by,updated_at,updated_by,archived_at,archived_by) "
                    "VALUES ('profile-down','tenant-a','workspace-a','dataset-a','Serving','serving','draft',NULL,1,NULL,NULL,CURRENT_TIMESTAMP,'owner-a',CURRENT_TIMESTAMP,'owner-a',NULL,NULL)"
                )
            )
    finally:
        blocked_engine.dispose()
    with pytest.raises(Exception, match="0036 downgrade blocked|Knowledge Serving|serving"):
        command.downgrade(alembic_config(blocked_url), DOWN_REVISION)



def test_profile_dataset_identity_is_unique_at_database_level(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "knowledge-serving-profile-identity.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            values = {
                "tenant_id": "tenant-a",
                "dataset_id": "dataset-a",
                "name": "Serving",
                "normalized_name": "serving",
                "created_by": "owner-a",
                "updated_by": "owner-a",
            }
            connection.execute(
                text(
                    "INSERT INTO tenant_knowledge_serving_profiles "
                    "(id,tenant_id,dataset_id,name,normalized_name,created_by,updated_by) "
                    "VALUES ('profile-identity-a',:tenant_id,:dataset_id,:name,:normalized_name,:created_by,:updated_by)"
                ),
                values,
            )
            connection.commit()
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO tenant_knowledge_serving_profiles "
                        "(id,tenant_id,dataset_id,name,normalized_name,created_by,updated_by) "
                        "VALUES ('profile-identity-b',:tenant_id,:dataset_id,:name,:normalized_name,:created_by,:updated_by)"
                    ),
                    values,
                )
    finally:
        engine.dispose()


def test_evidence_contract_has_exact_kind_route_pairs_and_positive_revision(
    tmp_path: Path,
) -> None:
    url = sqlite_url(tmp_path / "knowledge-serving-evidence-contract.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        resource_id = next(
            item for item in inspector.get_columns("tenant_knowledge_serving_evidence_links")
            if item["name"] == "resource_id"
        )
        assert resource_id["type"].length == 128

        checks = _checks(inspector, "tenant_knowledge_serving_evidence_links")
        digest_check = checks["ck_tenant_knowledge_serving_evidence_links_digest"]
        assert "resource_revision >= 1" in digest_check

        kind_route_check = checks[
            "ck_tenant_knowledge_serving_evidence_links_kind_route"
        ]
        for evidence_kind, route_code in (
            ("source", "knowledge_sources"),
            ("source_sync_run", "knowledge_sources"),
            ("document", "knowledge_documents"),
            ("ingest_attempt", "knowledge_documents"),
            ("chunk_head", "knowledge_documents"),
            ("index_operation", "knowledge_indexing"),
            ("release", "knowledge_base_releases"),
            ("certification", "release_quality"),
            ("task", "enterprise_tasks"),
        ):
            assert (
                f"(evidence_kind='{evidence_kind}' and route_code='{route_code}')"
                in kind_route_check
            )
    finally:
        engine.dispose()
