from __future__ import annotations

from importlib import import_module
from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import inspect, text

from tests.test_enterprise_knowledge_base_release_migration import (
    alembic_config,
    engine_for,
    sqlite_url,
)
from tests.test_enterprise_release_quality_operations_migration import _upgrade_fixture

REVISION = "0032_enterprise_notification_center"
DOWN_REVISION = "0031_enterprise_release_quality_operations"
TABLES = {
    "tenant_notification_subscriptions",
    "tenant_notifications",
    "tenant_notification_recipients",
    "tenant_notification_receipts",
    "tenant_notification_events",
}
EXPECTED_COLUMNS = {
    "tenant_notification_subscriptions": (
        "id",
        "tenant_id",
        "account_id",
        "category",
        "status",
        "preference",
        "active_subscription_key",
        "revision",
        "minimum_severity",
        "muted_until",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "archived_at",
        "archived_by",
    ),
    "tenant_notifications": (
        "id",
        "tenant_id",
        "source_kind",
        "source_id",
        "source_revision",
        "source_dataset_id",
        "category",
        "severity",
        "action_required",
        "mandatory",
        "notification_key",
        "source_digest",
        "title_code",
        "summary_code",
        "safe_facts_json",
        "target_route_code",
        "target_route_params_json",
        "occurred_at",
        "created_at",
        "created_by",
    ),
    "tenant_notification_recipients": (
        "id",
        "tenant_id",
        "notification_id",
        "account_id",
        "recipient_reason",
        "mandatory",
        "assignment_digest",
        "assigned_at",
    ),
    "tenant_notification_receipts": (
        "id",
        "tenant_id",
        "notification_id",
        "account_id",
        "status",
        "revision",
        "read_at",
        "archived_at",
        "updated_at",
    ),
    "tenant_notification_events": (
        "id",
        "tenant_id",
        "notification_id",
        "account_id",
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
        return import_module("catalog_migrations.versions.0032_enterprise_notification_center")
    except ModuleNotFoundError as exc:
        pytest.fail(f"Stage 22 migration is missing: {exc}")


def upgrade_0032(url: str) -> None:
    command.upgrade(alembic_config(url), REVISION)


def _upgrade(url: str) -> None:
    _upgrade_fixture(url)
    upgrade_0032(url)


def _checks(inspector, table: str) -> dict[str, str]:
    return {
        str(item["name"]): " ".join(str(item.get("sqltext") or "").casefold().split())
        for item in inspector.get_check_constraints(table)
    }


def _insert_subscription(connection) -> None:
    connection.execute(
        text(
            "INSERT INTO tenant_notification_subscriptions "
            "(id,tenant_id,account_id,category,status,preference,active_subscription_key,revision,"
            "minimum_severity,created_at,created_by,updated_at,updated_by) VALUES "
            "('subscription-a','tenant-a','owner-a','quality','active','subscribed',"
            "'owner-a:quality',1,'warning',CURRENT_TIMESTAMP,'owner-a',CURRENT_TIMESTAMP,'owner-a')"
        )
    )


def test_0032_is_head_and_follows_quality_operations() -> None:
    migration = migration_module()
    from alembic.script import ScriptDirectory

    scripts = ScriptDirectory.from_config(alembic_config("sqlite://"))
    assert scripts.get_current_head() == REVISION
    assert migration.revision == REVISION
    assert migration.down_revision == DOWN_REVISION


def test_upgrade_creates_exact_notification_center_contract(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "notification-center.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        assert TABLES <= set(inspector.get_table_names())
        for table, expected in EXPECTED_COLUMNS.items():
            assert tuple(item["name"] for item in inspector.get_columns(table)) == expected
        member_uniques = {
            item["name"]: tuple(item.get("column_names") or ())
            for item in inspector.get_unique_constraints("tenant_members")
        }
        assert member_uniques["uq_tenant_members_tenant_account"] == ("tenant_id", "account_id")
        assert (
            engine.connect().execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == REVISION
        )
    finally:
        engine.dispose()


def test_all_notification_foreign_keys_are_tenant_leading_and_target_unique(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "notification-fks.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        for table in TABLES:
            for fk in inspector.get_foreign_keys(table):
                constrained = tuple(fk.get("constrained_columns") or ())
                referred = tuple(fk.get("referred_columns") or ())
                target = str(fk["referred_table"])
                assert constrained and constrained[0] == "tenant_id", (table, fk)
                if target == "tenants":
                    assert referred == ("id",)
                else:
                    assert referred and referred[0] == "tenant_id", (table, fk)
                identities = {
                    tuple(item.get("column_names") or ())
                    for item in inspector.get_unique_constraints(target)
                }
                pk = tuple(
                    (inspector.get_pk_constraint(target) or {}).get("constrained_columns") or ()
                )
                if pk:
                    identities.add(pk)
                assert referred in identities, (table, fk, identities)
    finally:
        engine.dispose()


def test_notification_identities_receipt_lifecycle_and_hash_chain_are_fail_closed(
    tmp_path: Path,
) -> None:
    url = sqlite_url(tmp_path / "notification-checks.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        subscription = _checks(inspector, "tenant_notification_subscriptions")
        assert "active_subscription_key" in subscription["ck_notification_subscriptions_active_key"]
        assert "preference='muted'" in subscription["ck_notification_subscriptions_mute"]
        notification = _checks(inspector, "tenant_notifications")
        assert "source_kind='quality_alert'" in notification["ck_tenant_notifications_source_scope"]
        assert "target_route_code" in notification["ck_tenant_notifications_route"]
        receipt = _checks(inspector, "tenant_notification_receipts")
        assert "read_at" in receipt["ck_notification_receipts_lifecycle"]
        assert "archived_at" in receipt["ck_notification_receipts_lifecycle"]
        event = _checks(inspector, "tenant_notification_events")
        assert "previous_event_digest" in event["ck_notification_events_hash_chain"]

        notification_uniques = {
            item["name"]: tuple(item.get("column_names") or ())
            for item in inspector.get_unique_constraints("tenant_notifications")
        }
        assert notification_uniques["uq_tenant_notifications_notification_key"] == (
            "tenant_id",
            "notification_key",
        )
        recipient_uniques = {
            item["name"]: tuple(item.get("column_names") or ())
            for item in inspector.get_unique_constraints("tenant_notification_recipients")
        }
        assert recipient_uniques["uq_notification_recipients_identity"] == (
            "tenant_id",
            "notification_id",
            "account_id",
        )
    finally:
        engine.dispose()


def test_notification_recipient_and_event_tables_are_immutable(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "notification-triggers.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        names = {
            str(row[0])
            for row in engine.connect()
            .execute(text("SELECT name FROM sqlite_master WHERE type='trigger'"))
            .all()
        }
        for table in (
            "tenant_notifications",
            "tenant_notification_recipients",
            "tenant_notification_events",
        ):
            assert f"trg_{table}_no_update" in names
            assert f"trg_{table}_no_delete" in names
    finally:
        engine.dispose()


def test_event_insert_guard_rejects_missing_receipt_and_wrong_previous_digest(
    tmp_path: Path,
) -> None:
    url = sqlite_url(tmp_path / "notification-event-insert-guard.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenant_notifications "
                    "(id,tenant_id,source_kind,source_id,source_revision,source_dataset_id,category,"
                    "severity,action_required,mandatory,notification_key,source_digest,title_code,"
                    "summary_code,safe_facts_json,target_route_code,target_route_params_json,"
                    "occurred_at,created_at,created_by) VALUES "
                    "('notification-guard','tenant-a','approval_pending_for_me','approval-guard',1,NULL,"
                    "'approval','warning',1,1,'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',"
                    "'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',"
                    "'approval.pending','approval.pending','{}','enterprise_approval',"
                    "'{\"request_id\":\"approval-guard\"}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'owner-a')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO tenant_notification_recipients "
                    "(id,tenant_id,notification_id,account_id,recipient_reason,mandatory,"
                    "assignment_digest,assigned_at) VALUES "
                    "('recipient-guard','tenant-a','notification-guard','owner-a','eligible_approver',1,"
                    "'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',CURRENT_TIMESTAMP)"
                )
            )
            with pytest.raises(Exception, match="receipt|Receipt|event|Event"):
                connection.execute(
                    text(
                        "INSERT INTO tenant_notification_events "
                        "(id,tenant_id,notification_id,account_id,sequence,event_type,"
                        "previous_event_digest,event_digest,actor_id,request_id,safe_snapshot_json,occurred_at) "
                        "VALUES ('eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',"
                        "'tenant-a','notification-guard','owner-a',1,'materialized',NULL,"
                        "'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',"
                        "'owner-a','request-guard','{}',CURRENT_TIMESTAMP)"
                    )
                )

        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenant_notification_receipts "
                    "(id,tenant_id,notification_id,account_id,status,revision,updated_at) VALUES "
                    "('receipt-guard','tenant-a','notification-guard','owner-a','unread',1,CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO tenant_notification_events "
                    "(id,tenant_id,notification_id,account_id,sequence,event_type,"
                    "previous_event_digest,event_digest,actor_id,request_id,safe_snapshot_json,occurred_at) "
                    "VALUES ('dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',"
                    "'tenant-a','notification-guard','owner-a',1,'materialized',NULL,"
                    "'dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',"
                    "'owner-a','request-guard-1','{}',CURRENT_TIMESTAMP)"
                )
            )
            with pytest.raises(Exception, match="chain|previous|event|Event"):
                connection.execute(
                    text(
                        "INSERT INTO tenant_notification_events "
                        "(id,tenant_id,notification_id,account_id,sequence,event_type,"
                        "previous_event_digest,event_digest,actor_id,request_id,safe_snapshot_json,occurred_at) "
                        "VALUES ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff',"
                        "'tenant-a','notification-guard','owner-a',2,'marked_read',"
                        "'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',"
                        "'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff',"
                        "'owner-a','request-guard-2','{}',CURRENT_TIMESTAMP)"
                    )
                )
    finally:
        engine.dispose()


def test_offline_mysql_postgresql_include_notification_ddl_and_sqlite_fails_closed() -> None:
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
        assert "UQ_TENANT_MEMBERS_TENANT_ACCOUNT" in sql
        assert "NOTIFICATION_KEY" in sql
        assert "EVENT_DIGEST" in sql
        assert marker in sql
    with pytest.raises(Exception, match="online|SQLite|sqlite"):
        command.upgrade(
            alembic_config("sqlite:///notification-offline.db", output_buffer=StringIO()),
            f"{DOWN_REVISION}:{REVISION}",
            sql=True,
        )


def test_clean_downgrade_restores_0031_and_nonempty_authority_blocks(tmp_path: Path) -> None:
    clean_url = sqlite_url(tmp_path / "notification-clean-down.db")
    _upgrade(clean_url)
    command.downgrade(alembic_config(clean_url), DOWN_REVISION)
    engine = engine_for(clean_url)
    try:
        assert TABLES.isdisjoint(inspect(engine).get_table_names())
        member_uniques = {
            item["name"] for item in inspect(engine).get_unique_constraints("tenant_members")
        }
        assert "uq_tenant_members_tenant_account" not in member_uniques
    finally:
        engine.dispose()

    blocked_url = sqlite_url(tmp_path / "notification-blocked-down.db")
    _upgrade(blocked_url)
    blocked = engine_for(blocked_url)
    with blocked.begin() as connection:
        _insert_subscription(connection)
    blocked.dispose()
    with pytest.raises(Exception, match="0032|notification|Notification"):
        command.downgrade(alembic_config(blocked_url), DOWN_REVISION)
