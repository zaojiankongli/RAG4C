from __future__ import annotations

import importlib
from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DatabaseError, IntegrityError

from core import catalog_schema
from core.catalog_schema import _alembic_config

REV = "0025_enterprise_approval_control"
DOWN = "0024_oidc_sso_runtime"
TABLES = {
    "tenant_approval_policies",
    "tenant_approval_policy_approvers",
    "tenant_approval_requests",
    "tenant_approval_decisions",
}
DECISION_UPDATE_TRIGGER = "trg_tenant_approval_decisions_no_update"
DECISION_DELETE_TRIGGER = "trg_tenant_approval_decisions_no_delete"
DECISION_IMMUTABLE_MESSAGE = "tenant_approval_decisions are immutable"


def migration():
    try:
        return importlib.import_module(
            "catalog_migrations.versions.0025_enterprise_approval_control"
        )
    except ModuleNotFoundError:
        pytest.fail("0025 approval migration is missing")


def _scope(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'approval.db').as_posix()}"
    config = _alembic_config(url)
    command.upgrade(config, DOWN)
    return url, config


def _seed_scope(url: str) -> None:
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            now = "2026-08-27 12:00:00"
            connection.execute(
                text(
                    "INSERT INTO tenants (id,name,plan,status,quota_documents,quota_chunks,doc_count,chunk_count,created_at) "
                    "VALUES ('tenant-approval-1','Approval tenant','enterprise','active',1000,100000,0,0,:now)"
                ),
                {"now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO accounts (id,name,email,created_at) VALUES "
                    "('approval-account-1','Approver One','approver1@example.test',:now),"
                    "('approval-account-2','Approver Two','approver2@example.test',:now)"
                ),
                {"now": now},
            )
            for account_id, role in (
                ("approval-account-1", "admin"),
                ("approval-account-2", "editor"),
            ):
                connection.execute(
                    text(
                        "INSERT INTO tenant_members (account_id,tenant_id,role,status,revision,created_at,updated_at,updated_by) "
                        "VALUES (:account_id,'tenant-approval-1',:role,'active',1,:now,:now,:account_id)"
                    ),
                    {"account_id": account_id, "role": role, "now": now},
                )
            connection.execute(
                text(
                    "INSERT INTO tenant_groups (id,tenant_id,name,normalized_name,description,status,revision,created_at,updated_at) "
                    "VALUES ('approval-group-1','tenant-approval-1','Approvers','approvers','', 'active',1,:now,:now)"
                ),
                {"now": now},
            )
    finally:
        engine.dispose()


def _seed_policy_and_request(
    url: str,
    *,
    request_id: str,
    idempotency_key: str,
    status: str = "pending",
    execution_ticket_hash: str | None = None,
    ticket_consumed_at: str | None = None,
    executed_at: str | None = None,
    executed_by: str | None = None,
    execution_failed_at: str | None = None,
    execution_failed_by: str | None = None,
    execution_error: str | None = None,
) -> None:
    engine = create_engine(url)
    now = "2026-08-27 12:00:00"
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenant_approval_policies "
                    "(id,tenant_id,name,action_type,resource_scope,active_scope_key,status,required_approvals,request_expiry_minutes,revision,created_at,created_by,updated_at,updated_by) "
                    "VALUES (:policy_id,'tenant-approval-1','Catalog changes','catalog_upgrade',NULL,:scope_key,'active',2,1440,1,:now,'approval-account-1',:now,'approval-account-1')"
                ),
                {
                    "policy_id": f"policy-{request_id}",
                    "scope_key": f"catalog_upgrade|{request_id}",
                    "now": now,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO tenant_approval_requests "
                    "(id,tenant_id,policy_id,requester_id,action_type,resource_type,resource_id,snapshot_json,payload_hash,reason,status,required_approvals,received_approvals,idempotency_key,expires_at,revision,execution_ticket_hash,ticket_issued_at,ticket_consumed_at,rejected_at,rejected_by,rejection_comment,cancelled_at,cancelled_by,executed_at,executed_by,execution_failed_at,execution_failed_by,execution_error,created_at,created_by,updated_at,updated_by) "
                    "VALUES (:request_id,'tenant-approval-1',:policy_id,'approval-account-2','catalog_upgrade','catalog','catalog','{}',:payload_hash,'routine',:status,2,0,:idempotency_key,:expires_at,1,:execution_ticket_hash,NULL,:ticket_consumed_at,NULL,NULL,NULL,NULL,NULL,:executed_at,:executed_by,:execution_failed_at,:execution_failed_by,:execution_error,:now,'approval-account-2',:now,'approval-account-2')"
                ),
                {
                    "request_id": request_id,
                    "policy_id": f"policy-{request_id}",
                    "payload_hash": "a" * 64,
                    "status": status,
                    "idempotency_key": idempotency_key,
                    "expires_at": "2026-08-28 12:00:00",
                    "execution_ticket_hash": execution_ticket_hash,
                    "ticket_consumed_at": ticket_consumed_at,
                    "executed_at": executed_at,
                    "executed_by": executed_by,
                    "execution_failed_at": execution_failed_at,
                    "execution_failed_by": execution_failed_by,
                    "execution_error": execution_error,
                    "now": now,
                },
            )
    finally:
        engine.dispose()


def _named_checks(table) -> dict[str, str]:
    return {
        str(constraint.name): str(constraint.sqltext).lower()
        for constraint in table.constraints
        if getattr(constraint, "sqltext", None) is not None and constraint.name
    }


def test_0025_precedes_current_head_and_exposes_all_authority_tables() -> None:
    migration_module = migration()
    scripts = ScriptDirectory.from_config(_alembic_config("sqlite://"))

    assert migration_module.revision == REV
    assert migration_module.down_revision == DOWN
    assert (
        scripts.get_current_head()
        == catalog_schema.HEAD_REVISION
        == "0030_enterprise_release_quality_certification"
    )
    assert TABLES <= set(catalog_schema.HEAD_CATALOG_TABLES)

    import models.orm as orm

    assert TABLES <= set(orm.Base.metadata.tables)
    for table in TABLES:
        assert table in catalog_schema._HEAD_REQUIRED_COLUMNS
        assert table in catalog_schema._HEAD_REQUIRED_NOT_NULL
        assert table in catalog_schema._HEAD_REQUIRED_UNIQUES
        assert table in catalog_schema._HEAD_REQUIRED_FOREIGN_KEYS
        assert table in catalog_schema._HEAD_REQUIRED_CHECK_FRAGMENTS
        assert table in catalog_schema._HEAD_REQUIRED_INDEXES

    from models.orm import TenantApprovalDecision, TenantApprovalRequest

    request_checks = _named_checks(TenantApprovalRequest.__table__)
    assert "'executing'" in request_checks["ck_tenant_approval_requests_status"]
    assert (
        "ticket_consumed_at is not null"
        in request_checks["ck_tenant_approval_requests_executing_evidence"]
    )
    assert (
        "execution_ticket_hash is not null"
        in request_checks["ck_tenant_approval_requests_executing_evidence"]
    )
    decision_checks = _named_checks(TenantApprovalDecision.__table__)
    assert "ck_tenant_approval_decisions_decision" in decision_checks


def test_upgrade_has_tenant_safe_constraints_indexes_and_hash_only_columns(tmp_path: Path) -> None:
    url, config = _scope(tmp_path)
    command.upgrade(config, REV)
    _seed_scope(url)
    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        assert TABLES <= set(inspector.get_table_names())
        request_columns = {
            item["name"] for item in inspector.get_columns("tenant_approval_requests")
        }
        assert {"snapshot_json", "payload_hash", "execution_ticket_hash"} <= request_columns
        assert not any(
            name in request_columns
            for name in ("execution_ticket", "ticket", "raw_ticket", "authorization_ticket")
        )

        request_fks = {
            item["name"]: (
                tuple(item["constrained_columns"]),
                item["referred_table"],
                tuple(item["referred_columns"]),
            )
            for item in inspector.get_foreign_keys("tenant_approval_requests")
        }
        assert request_fks["fk_tenant_approval_requests_scope_policy"] == (
            ("tenant_id", "policy_id"),
            "tenant_approval_policies",
            ("tenant_id", "id"),
        )
        assert request_fks["fk_tenant_approval_requests_scope_requester"] == (
            ("requester_id", "tenant_id"),
            "tenant_members",
            ("account_id", "tenant_id"),
        )

        decision_uniques = {
            item["name"]: tuple(item["column_names"])
            for item in inspector.get_unique_constraints("tenant_approval_decisions")
        }
        assert decision_uniques["uq_tenant_approval_decisions_request_approver"] == (
            "tenant_id",
            "request_id",
            "approver_id",
        )
        index_names = {item["name"] for table in TABLES for item in inspector.get_indexes(table)}
        assert {
            "ix_tenant_approval_policies_tenant_status_action",
            "ix_tenant_approval_policy_approvers_tenant_policy_status",
            "ix_tenant_approval_requests_tenant_status_expiry",
            "ix_tenant_approval_decisions_tenant_request_time",
        } <= index_names
    finally:
        engine.dispose()


def test_policy_scope_uniqueness_and_bounds_are_database_enforced(tmp_path: Path) -> None:
    url, config = _scope(tmp_path)
    command.upgrade(config, REV)
    _seed_scope(url)
    engine = create_engine(url)
    now = "2026-08-27 12:00:00"
    values = {"now": now, "key": "catalog_upgrade|global"}
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.execute(
                text(
                    "INSERT INTO tenant_approval_policies "
                    "(id,tenant_id,name,action_type,resource_scope,active_scope_key,status,required_approvals,request_expiry_minutes,revision,created_at,created_by,updated_at,updated_by) "
                    "VALUES ('policy-1','tenant-approval-1','Catalog changes','catalog_upgrade',NULL,:key,'active',2,1440,1,:now,'approval-account-1',:now,'approval-account-1')"
                ),
                values,
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO tenant_approval_policies "
                        "(id,tenant_id,name,action_type,resource_scope,active_scope_key,status,required_approvals,request_expiry_minutes,revision,created_at,created_by,updated_at,updated_by) "
                        "VALUES ('policy-duplicate','tenant-approval-1','Duplicate','catalog_upgrade',NULL,:key,'active',2,1440,1,:now,'approval-account-1',:now,'approval-account-1')"
                    ),
                    values,
                )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO tenant_approval_policies "
                        "(id,tenant_id,name,action_type,resource_scope,active_scope_key,status,required_approvals,request_expiry_minutes,revision,created_at,created_by,updated_at,updated_by) "
                        "VALUES ('policy-bad','tenant-approval-1','Bad','catalog_upgrade','x','bad','active',6,14,0,:now,'approval-account-1',:now,'approval-account-1')"
                    ),
                    values,
                )
    finally:
        engine.dispose()


def test_request_and_decision_hash_count_status_and_scope_constraints(tmp_path: Path) -> None:
    url, config = _scope(tmp_path)
    command.upgrade(config, REV)
    _seed_scope(url)
    engine = create_engine(url)
    now = "2026-08-27 12:00:00"
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.execute(
                text(
                    "INSERT INTO tenant_approval_policies "
                    "(id,tenant_id,name,action_type,resource_scope,active_scope_key,status,required_approvals,request_expiry_minutes,revision,created_at,created_by,updated_at,updated_by) "
                    "VALUES ('policy-1','tenant-approval-1','Catalog changes','catalog_upgrade',NULL,'catalog_upgrade|global','active',2,1440,1,:now,'approval-account-1',:now,'approval-account-1')"
                ),
                {"now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO tenant_approval_requests "
                    "(id,tenant_id,policy_id,requester_id,action_type,resource_type,resource_id,snapshot_json,payload_hash,reason,status,required_approvals,received_approvals,idempotency_key,expires_at,revision,created_at,created_by,updated_at,updated_by) "
                    "VALUES ('request-1','tenant-approval-1','policy-1','approval-account-2','catalog_upgrade','catalog','catalog','{}',:hash,'routine', 'pending',2,0,'idem-1',:expires,1,:now,'approval-account-2',:now,'approval-account-2')"
                ),
                {"hash": "a" * 64, "expires": "2026-08-28 12:00:00", "now": now},
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO tenant_approval_requests "
                        "(id,tenant_id,policy_id,requester_id,action_type,resource_type,resource_id,snapshot_json,payload_hash,reason,status,required_approvals,received_approvals,idempotency_key,expires_at,revision,created_at,created_by,updated_at,updated_by) "
                        "VALUES ('request-bad','tenant-approval-1','policy-1','approval-account-2','catalog_upgrade','catalog','catalog','{}','short','bad', 'pending',0,2,'idem-2',:expires,0,:now,'approval-account-2',:now,'approval-account-2')"
                    ),
                    {"expires": "2026-08-28 12:00:00", "now": now},
                )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenant_approval_decisions "
                    "(id,tenant_id,request_id,approver_id,decision,comment,decided_at,revision,created_at,created_by) "
                    "VALUES ('decision-1','tenant-approval-1','request-1','approval-account-1','approved','ok',:now,1,:now,'approval-account-1')"
                ),
                {"now": now},
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO tenant_approval_decisions "
                        "(id,tenant_id,request_id,approver_id,decision,comment,decided_at,revision,created_at,created_by) "
                        "VALUES ('decision-duplicate','tenant-approval-1','request-1','approval-account-1','approved','again',:now,1,:now,'approval-account-1')"
                    ),
                    {"now": now},
                )
    finally:
        engine.dispose()


def test_decision_facts_are_database_immutable_but_insertable(tmp_path: Path) -> None:
    url, config = _scope(tmp_path)
    command.upgrade(config, REV)
    _seed_scope(url)
    _seed_policy_and_request(
        url,
        request_id="immutable-request",
        idempotency_key="immutable-idem",
    )
    engine = create_engine(url)
    now = "2026-08-27 12:00:00"
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenant_approval_decisions "
                    "(id,tenant_id,request_id,approver_id,decision,comment,decided_at,revision,created_at,created_by) "
                    "VALUES ('immutable-decision','tenant-approval-1','immutable-request','approval-account-1','approved','approved',:now,1,:now,'approval-account-1')"
                ),
                {"now": now},
            )

        with engine.connect() as connection:
            trigger_rows = (
                connection.execute(
                    text(
                        "SELECT name, sql FROM sqlite_master "
                        "WHERE type='trigger' AND tbl_name='tenant_approval_decisions' "
                        "ORDER BY name"
                    )
                )
                .mappings()
                .all()
            )
        assert [row["name"] for row in trigger_rows] == [
            DECISION_DELETE_TRIGGER,
            DECISION_UPDATE_TRIGGER,
        ]
        trigger_sql = {row["name"]: row["sql"].upper() for row in trigger_rows}
        assert "BEFORE UPDATE ON TENANT_APPROVAL_DECISIONS" in trigger_sql[DECISION_UPDATE_TRIGGER]
        assert "BEFORE DELETE ON TENANT_APPROVAL_DECISIONS" in trigger_sql[DECISION_DELETE_TRIGGER]

        for statement in (
            "UPDATE tenant_approval_decisions SET comment='tampered' WHERE id='immutable-decision'",
            "DELETE FROM tenant_approval_decisions WHERE id='immutable-decision'",
        ):
            with pytest.raises(DatabaseError, match=DECISION_IMMUTABLE_MESSAGE):
                with engine.begin() as connection:
                    connection.execute(text(statement))

        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT comment FROM tenant_approval_decisions "
                        "WHERE id='immutable-decision'"
                    )
                ).scalar_one()
                == "approved"
            )
    finally:
        engine.dispose()


def test_request_executing_state_requires_consumed_ticket_evidence_and_keeps_terminal_checks(
    tmp_path: Path,
) -> None:
    url, config = _scope(tmp_path)
    command.upgrade(config, REV)
    _seed_scope(url)

    with pytest.raises(IntegrityError, match="ck_tenant_approval_requests_executing_evidence"):
        _seed_policy_and_request(
            url,
            request_id="executing-missing-evidence",
            idempotency_key="executing-missing-evidence-idem",
            status="executing",
        )

    _seed_policy_and_request(
        url,
        request_id="executing-valid",
        idempotency_key="executing-valid-idem",
        status="executing",
        execution_ticket_hash="b" * 64,
        ticket_consumed_at="2026-08-27 12:01:00",
    )

    with pytest.raises(IntegrityError, match="ck_tenant_approval_requests_executed_evidence"):
        _seed_policy_and_request(
            url,
            request_id="executed-missing-evidence",
            idempotency_key="executed-missing-evidence-idem",
            status="executed",
            execution_ticket_hash="c" * 64,
        )

    with pytest.raises(
        IntegrityError, match="ck_tenant_approval_requests_execution_failed_evidence"
    ):
        _seed_policy_and_request(
            url,
            request_id="failed-missing-evidence",
            idempotency_key="failed-missing-evidence-idem",
            status="execution_failed",
        )

    _seed_policy_and_request(
        url,
        request_id="executed-valid",
        idempotency_key="executed-valid-idem",
        status="executed",
        execution_ticket_hash="c" * 64,
        executed_at="2026-08-27 12:02:00",
        executed_by="approval-account-1",
    )
    _seed_policy_and_request(
        url,
        request_id="failed-valid",
        idempotency_key="failed-valid-idem",
        status="execution_failed",
        execution_failed_at="2026-08-27 12:03:00",
        execution_failed_by="approval-account-1",
        execution_error="downstream unavailable",
    )

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT status, execution_ticket_hash, ticket_consumed_at "
                        "FROM tenant_approval_requests WHERE id='executing-valid'"
                    )
                )
                .mappings()
                .one()
            )
            assert row["status"] == "executing"
            assert row["execution_ticket_hash"] == "b" * 64
            assert row["ticket_consumed_at"] == "2026-08-27 12:01:00"
    finally:
        engine.dispose()


def test_downgrade_removes_0025_authority_tables(tmp_path: Path) -> None:
    url, config = _scope(tmp_path)
    command.upgrade(config, REV)
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            trigger_names = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='trigger' AND tbl_name='tenant_approval_decisions'"
                    )
                )
            }
        assert trigger_names == {DECISION_UPDATE_TRIGGER, DECISION_DELETE_TRIGGER}
    finally:
        engine.dispose()

    command.downgrade(config, DOWN)
    engine = create_engine(url)
    try:
        assert not TABLES & set(inspect(engine).get_table_names())
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT COUNT(*) FROM sqlite_master "
                        "WHERE type='trigger' AND name IN (:update_name, :delete_name)"
                    ),
                    {
                        "update_name": DECISION_UPDATE_TRIGGER,
                        "delete_name": DECISION_DELETE_TRIGGER,
                    },
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()


def test_mysql_offline_sql_contains_0025_contract() -> None:
    output = StringIO()
    config = _alembic_config("mysql+pymysql://u:p@localhost/r")
    config.output_buffer = output
    command.upgrade(config, f"{DOWN}:{REV}", sql=True)
    sql = output.getvalue().upper()
    assert all(f"CREATE TABLE {table.upper()}" in sql for table in TABLES)
    assert "SNAPSHOT_JSON" in sql
    assert "PAYLOAD_HASH" in sql
    assert "EXECUTION_TICKET_HASH" in sql
    assert "DATETIME(6)" in sql

    down_output = StringIO()
    down_config = _alembic_config("mysql+pymysql://u:p@localhost/r")
    down_config.output_buffer = down_output
    command.downgrade(down_config, f"{REV}:{DOWN}", sql=True)
    down = down_output.getvalue().upper()
    assert (
        "CREATE TRIGGER TRG_TENANT_APPROVAL_DECISIONS_NO_UPDATE "
        "BEFORE UPDATE ON TENANT_APPROVAL_DECISIONS FOR EACH ROW "
        "SIGNAL SQLSTATE '45000'" in sql
    )
    assert (
        "CREATE TRIGGER TRG_TENANT_APPROVAL_DECISIONS_NO_DELETE "
        "BEFORE DELETE ON TENANT_APPROVAL_DECISIONS FOR EACH ROW "
        "SIGNAL SQLSTATE '45000'" in sql
    )
    decision_table = down.index("DROP TABLE TENANT_APPROVAL_DECISIONS")
    assert (
        down.index("DROP TRIGGER IF EXISTS TRG_TENANT_APPROVAL_DECISIONS_NO_UPDATE")
        < decision_table
    )
    assert (
        down.index("DROP TRIGGER IF EXISTS TRG_TENANT_APPROVAL_DECISIONS_NO_DELETE")
        < decision_table
    )


def test_0027_extends_approval_actions_without_rewriting_existing_facts(
    tmp_path: Path,
) -> None:
    url, config = _scope(tmp_path)
    _seed_scope(url)
    command.upgrade(config, REV)
    _seed_policy_and_request(
        url,
        request_id="request-before-0027",
        idempotency_key="approval-before-0027",
    )

    command.upgrade(config, "0028_enterprise_knowledge_base_registry")
    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        policy_checks = {
            item["name"]: str(item.get("sqltext") or "").casefold()
            for item in inspector.get_check_constraints("tenant_approval_policies")
        }
        request_checks = {
            item["name"]: str(item.get("sqltext") or "").casefold()
            for item in inspector.get_check_constraints("tenant_approval_requests")
        }
        action = "workspace_authorization_mode_change"
        assert action in policy_checks["ck_tenant_approval_policies_action_type"]
        assert action in request_checks["ck_tenant_approval_requests_action_type"]

        with engine.connect() as connection:
            legacy_policy = (
                connection.execute(
                    text(
                        "SELECT action_type, revision FROM tenant_approval_policies "
                        "WHERE id='policy-request-before-0027'"
                    )
                )
                .mappings()
                .one()
            )
            legacy_request = (
                connection.execute(
                    text(
                        "SELECT action_type, status, revision FROM tenant_approval_requests "
                        "WHERE id='request-before-0027'"
                    )
                )
                .mappings()
                .one()
            )
        assert dict(legacy_policy) == {"action_type": "catalog_upgrade", "revision": 1}
        assert dict(legacy_request) == {
            "action_type": "catalog_upgrade",
            "status": "pending",
            "revision": 1,
        }
    finally:
        engine.dispose()
