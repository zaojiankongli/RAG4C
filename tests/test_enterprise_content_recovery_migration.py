from __future__ import annotations

from importlib import import_module
from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from core import catalog_schema
from tests.test_enterprise_knowledge_base_release_migration import (
    alembic_config,
    engine_for,
    sqlite_url,
)
from tests.test_enterprise_notification_center_migration import _upgrade as upgrade_0032

REVISION = "0033_enterprise_content_recovery"
DOWN_REVISION = "0032_enterprise_notification_center"
TABLES = {
    "tenant_content_retention_policies",
    "tenant_document_recycle_entries",
    "tenant_document_legal_holds",
    "tenant_document_purge_requests",
    "tenant_document_recovery_events",
}
EXPECTED_COLUMNS = {
    "tenant_content_retention_policies": (
        "id",
        "tenant_id",
        "status",
        "retention_days",
        "auto_purge_enabled",
        "purge_requires_approval",
        "revision",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
    ),
    "tenant_document_recycle_entries": (
        "id",
        "tenant_id",
        "dataset_id",
        "document_id",
        "recycle_generation",
        "active_recycle_key",
        "status",
        "revision",
        "document_mutation_generation",
        "original_lifecycle_state",
        "original_retrieval_enabled",
        "retention_days_snapshot",
        "recycled_at",
        "recycled_by",
        "purge_eligible_at",
        "restored_at",
        "restored_by",
        "purge_requested_at",
        "purged_at",
        "purged_by",
        "safe_snapshot_json",
        "snapshot_digest",
        "created_at",
        "updated_at",
    ),
    "tenant_document_legal_holds": (
        "id",
        "tenant_id",
        "dataset_id",
        "document_id",
        "recycle_entry_id",
        "status",
        "active_hold_key",
        "revision",
        "reason_code",
        "safe_reason",
        "held_at",
        "held_by",
        "released_at",
        "released_by",
        "created_at",
        "updated_at",
    ),
    "tenant_document_purge_requests": (
        "id",
        "tenant_id",
        "dataset_id",
        "document_id",
        "recycle_entry_id",
        "status",
        "revision",
        "expected_entry_revision",
        "request_digest",
        "idempotency_key_digest",
        "approval_request_id",
        "retention_snapshot_json",
        "legal_hold_count_snapshot",
        "requested_at",
        "requested_by",
        "approved_at",
        "cancelled_at",
        "cancelled_by",
        "expires_at",
        "executed_at",
        "created_at",
        "updated_at",
    ),
    "tenant_document_recovery_events": (
        "id",
        "tenant_id",
        "dataset_id",
        "document_id",
        "recycle_entry_id",
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
        return import_module("catalog_migrations.versions.0033_enterprise_content_recovery")
    except ModuleNotFoundError as exc:
        pytest.fail(f"Stage 23 migration is missing: {exc}")


def upgrade_0033(url: str) -> None:
    command.upgrade(alembic_config(url), REVISION)


def _upgrade(url: str) -> None:
    upgrade_0032(url)
    upgrade_0033(url)


def _checks(inspector, table: str) -> dict[str, str]:
    return {
        str(item["name"]): " ".join(str(item.get("sqltext") or "").casefold().split())
        for item in inspector.get_check_constraints(table)
        if item.get("name")
    }


def _foreign_keys(inspector, table: str) -> dict[str, tuple[tuple[str, ...], str, tuple[str, ...]]]:
    return {
        str(item["name"]): (
            tuple(item.get("constrained_columns") or ()),
            str(item.get("referred_table")),
            tuple(item.get("referred_columns") or ()),
        )
        for item in inspector.get_foreign_keys(table)
        if item.get("name")
    }


def test_0033_follows_notification_center_and_precedes_current_head() -> None:
    migration = migration_module()
    scripts = ScriptDirectory.from_config(alembic_config("sqlite://"))
    assert scripts.get_current_head() == catalog_schema.HEAD_REVISION
    assert migration.revision == REVISION
    assert migration.down_revision == DOWN_REVISION


def test_upgrade_creates_exactly_five_recovery_tables_and_contract(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "content-recovery.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        assert TABLES <= set(inspector.get_table_names())
        for table, expected in EXPECTED_COLUMNS.items():
            assert tuple(item["name"] for item in inspector.get_columns(table)) == expected
        assert len(TABLES) == 5
        assert (
            engine.connect()
            .execute(text("SELECT version_num FROM alembic_version"))
            .scalar_one()
            == REVISION
        )
    finally:
        engine.dispose()


def test_recovery_contract_has_tenant_leading_fks_and_canonical_active_keys(
    tmp_path: Path,
) -> None:
    url = sqlite_url(tmp_path / "content-recovery-contract.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        for table in TABLES:
            for name, (constrained, referred, referred_columns) in _foreign_keys(
                inspector, table
            ).items():
                assert constrained[0] == "tenant_id", (table, name, constrained)
                if referred == "tenants":
                    assert referred_columns == ("id",)
                else:
                    assert referred_columns[0] == "tenant_id", (table, name, referred_columns)

        uniques = {
            table: {
                str(item["name"]): tuple(item.get("column_names") or ())
                for item in inspector.get_unique_constraints(table)
                if item.get("name")
            }
            for table in TABLES
        }
        assert uniques["tenant_content_retention_policies"][
            "uq_tenant_content_retention_policies_tenant"
        ] == ("tenant_id",)
        assert uniques["tenant_document_recycle_entries"][
            "uq_tenant_document_recycle_entries_active_key"
        ] == ("tenant_id", "active_recycle_key")
        assert uniques["tenant_document_legal_holds"][
            "uq_tenant_document_legal_holds_active_key"
        ] == ("tenant_id", "active_hold_key")

        recycle_checks = _checks(inspector, "tenant_document_recycle_entries")
        recycle_key_check = recycle_checks["ck_tenant_document_recycle_entries_active_key"]
        for active_status in ("recycled", "restoring", "purge_requested"):
            assert f"status = '{active_status}'" in recycle_key_check
        for terminal_status in ("restored", "purged", "failed"):
            assert terminal_status in recycle_key_check
        assert "active_recycle_key" in recycle_key_check
        assert "dataset_id" in recycle_checks["ck_tenant_document_recycle_entries_active_key"]
        assert "document_id" in recycle_checks["ck_tenant_document_recycle_entries_active_key"]
        hold_checks = _checks(inspector, "tenant_document_legal_holds")
        assert "active_hold_key" in hold_checks["ck_tenant_document_legal_holds_active_key"]
        assert "reason_code" in hold_checks["ck_tenant_document_legal_holds_active_key"]
    finally:
        engine.dispose()


def test_recovery_event_guards_are_immutable_and_require_contiguous_entry_chain(
    tmp_path: Path,
) -> None:
    url = sqlite_url(tmp_path / "content-recovery-events.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        with engine.connect() as connection:
            trigger_names = {
                str(row[0])
                for row in connection.execute(
                    text(
                        "SELECT name FROM sqlite_master WHERE type='trigger' "
                        "AND tbl_name='tenant_document_recovery_events'"
                    )
                )
            }
        assert {
            "trg_tenant_document_recovery_events_no_update",
            "trg_tenant_document_recovery_events_no_delete",
            "trg_tenant_document_recovery_events_validate_insert",
        } <= trigger_names
        checks = _checks(inspector, "tenant_document_recovery_events")
        assert "sequence > 0" in checks["ck_tenant_document_recovery_events_sequence"]
        assert "recycled" in checks["ck_tenant_document_recovery_events_type"]
        assert "previous_event_digest" in checks["ck_tenant_document_recovery_events_hash_chain"]
    finally:
        engine.dispose()


def test_document_lifecycle_and_approval_action_checks_are_extended_without_losing_legacy_actions(
    tmp_path: Path,
) -> None:
    url = sqlite_url(tmp_path / "content-recovery-compatibility.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        document_checks = _checks(inspector, "documents")
        lifecycle = document_checks["ck_documents_lifecycle_state"]
        assert "recycled" in lifecycle
        assert "active" in lifecycle and "deleted" in lifecycle

        for table in ("tenant_approval_policies", "tenant_approval_requests"):
            check = _checks(inspector, table)[f"ck_{table}_action_type"]
            assert "document_purge" in check
            assert "catalog_upgrade" in check
            assert "knowledge_base_release_quality_waiver" in check
    finally:
        engine.dispose()


def test_offline_mysql_postgresql_emit_0033_ddl_and_sqlite_fails_closed() -> None:
    for url, marker in (
        ("mysql+pymysql://u:p@localhost/rag4c", "DATETIME(6)"),
        ("postgresql+psycopg://u:p@localhost/rag4c", "TIMESTAMP"),
    ):
        output = StringIO()
        command.upgrade(
            alembic_config(url, output_buffer=output),
            f"{DOWN_REVISION}:{REVISION}",
            sql=True,
        )
        sql = output.getvalue().upper()
        for table in TABLES:
            assert f"CREATE TABLE {table.upper()}" in sql
        assert "DOCUMENT_PURGE" in sql
        assert "RECYCLED" in sql
        assert "EVENT_DIGEST" in sql
        assert marker in sql

    with pytest.raises(Exception, match="online|SQLite|sqlite"):
        command.upgrade(
            alembic_config("sqlite:///content-recovery-offline.db", output_buffer=StringIO()),
            f"{DOWN_REVISION}:{REVISION}",
            sql=True,
        )


def test_clean_downgrade_restores_0032_and_nonempty_recovery_authority_blocks(
    tmp_path: Path,
) -> None:
    clean_url = sqlite_url(tmp_path / "content-recovery-clean-down.db")
    _upgrade(clean_url)
    command.downgrade(alembic_config(clean_url), DOWN_REVISION)
    clean_engine = engine_for(clean_url)
    try:
        assert TABLES.isdisjoint(inspect(clean_engine).get_table_names())
        assert (
            clean_engine.connect()
            .execute(text("SELECT version_num FROM alembic_version"))
            .scalar_one()
            == DOWN_REVISION
        )
    finally:
        clean_engine.dispose()

    blocked_url = sqlite_url(tmp_path / "content-recovery-blocked-down.db")
    _upgrade(blocked_url)
    blocked_engine = engine_for(blocked_url)
    with blocked_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenant_content_retention_policies "
                "(id,tenant_id,status,retention_days,auto_purge_enabled,purge_requires_approval,"
                "revision,created_at,created_by,updated_at,updated_by) VALUES "
                "('recovery-policy-a','tenant-a','active',30,0,1,1,CURRENT_TIMESTAMP,'owner-a',CURRENT_TIMESTAMP,'owner-a')"
            )
        )
    blocked_engine.dispose()
    with pytest.raises(Exception, match="0033|recovery|Recovery|content"):
        command.downgrade(alembic_config(blocked_url), DOWN_REVISION)


def test_downgrade_is_blocked_when_a_document_remains_recycled(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "content-recovery-recycled-document.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO documents "
                    "(id,dataset_id,tenant_id,name,file_path,file_hash,doc_type,status,status_detail,"
                    "progress,chunk_count,error_message,created_at,updated_at,lifecycle_state,retrieval_enabled,"
                    "mutation_generation) VALUES ('document-recovery-a','dataset-a','tenant-a','Recovery doc',"
                    "'', '', '', 'waiting', '', 0, 0, '', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'recycled', 0, 0)"
                )
            )
    finally:
        engine.dispose()
    with pytest.raises(Exception, match="recycled|0033|recovery|Recovery"):
        command.downgrade(alembic_config(url), DOWN_REVISION)

