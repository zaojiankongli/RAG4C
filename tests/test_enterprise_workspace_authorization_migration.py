from __future__ import annotations

from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core import catalog_schema
from models.orm import Account, Tenant, TenantMember

REVISION = "0027_enterprise_workspace_authorization"
DOWN_REVISION = "0026_enterprise_workspace_control"
TABLE = "tenant_workspace_authorization_policies"
ACTION = "workspace_authorization_mode_change"
MIGRATION_ACTOR = "migration:0027"


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


def seed_0026(url: str, *, include_long_workspace: bool = False) -> str | None:
    catalog_schema.upgrade_catalog(url, "0025_enterprise_approval_control")
    engine = engine_for(url)
    try:
        with Session(engine) as session:
            session.add_all(
                [
                    Account(id="owner-a", name="Owner A", email="owner-a@stage17.test"),
                    Account(id="owner-b", name="Owner B", email="owner-b@stage17.test"),
                    Tenant(id="tenant-a", name="Alpha", plan="enterprise", status="active"),
                    Tenant(id="tenant-b", name="Beta", plan="enterprise", status="active"),
                ]
            )
            session.flush()
            session.add_all(
                [
                    TenantMember(
                        account_id="owner-a",
                        tenant_id="tenant-a",
                        role="owner",
                        status="active",
                    ),
                    TenantMember(
                        account_id="owner-b",
                        tenant_id="tenant-b",
                        role="owner",
                        status="active",
                    ),
                ]
            )
            session.commit()
    finally:
        engine.dispose()

    command.upgrade(alembic_config(url), DOWN_REVISION)
    long_workspace_id = None
    engine = engine_for(url)
    try:
        with engine.begin() as connection:
            now = datetime.utcnow()
            connection.execute(
                text(
                    "INSERT INTO tenant_workspaces "
                    "(id,tenant_id,code,name,normalized_name,description,status,environment,is_default,"
                    "active_default_slot,revision,created_at,created_by,updated_at,updated_by,archived_at,archived_by) "
                    "VALUES ('workspace-archived-a','tenant-a','archived-a','Archived A','archived a','',"
                    "'archived','production',0,NULL,2,:now,'owner-a',:now,'owner-a',:now,'owner-a')"
                ),
                {"now": now},
            )
            if include_long_workspace:
                long_workspace_id = "w" * 128
                connection.execute(
                    text(
                        "INSERT INTO tenant_workspaces "
                        "(id,tenant_id,code,name,normalized_name,description,status,environment,is_default,"
                        "active_default_slot,revision,created_at,created_by,updated_at,updated_by) "
                        "VALUES (:id,'tenant-a','long-workspace','Long Workspace','long workspace','',"
                        "'active','testing',0,NULL,1,:now,'owner-a',:now,'owner-a')"
                    ),
                    {"id": long_workspace_id, "now": now},
                )
    finally:
        engine.dispose()
    return long_workspace_id


def upgrade_0027(url: str) -> None:
    command.upgrade(alembic_config(url), REVISION)


def check_sql(inspector, table: str) -> dict[str, str]:
    return {
        str(item["name"]): str(item.get("sqltext") or "").casefold()
        for item in inspector.get_check_constraints(table)
    }


def insert_workspace_mode_approval_rows(connection) -> None:
    now = datetime.utcnow()
    expires = now + timedelta(hours=1)
    connection.execute(
        text(
            "INSERT INTO tenant_approval_policies "
            "(id,tenant_id,name,action_type,resource_scope,active_scope_key,status,required_approvals,"
            "request_expiry_minutes,revision,created_at,created_by,updated_at,updated_by) "
            "VALUES ('workspace-auth-approval','tenant-a','Workspace authorization',:action,"
            "'tenant_workspace:*','workspace-authorization|tenant-a','active',1,1440,1,:now,"
            "'owner-a',:now,'owner-a')"
        ),
        {"action": ACTION, "now": now},
    )
    connection.execute(
        text(
            "INSERT INTO tenant_approval_requests "
            "(id,tenant_id,policy_id,requester_id,action_type,resource_type,resource_id,snapshot_json,"
            "payload_hash,reason,status,required_approvals,received_approvals,idempotency_key,expires_at,"
            "revision,created_at,created_by,updated_at,updated_by) "
            "VALUES ('workspace-auth-request','tenant-a','workspace-auth-approval','owner-a',:action,"
            "'tenant_workspace','workspace-default-tenant-a','{}',:payload_hash,'mode change','pending',"
            "1,0,'workspace-auth-idempotency',:expires,1,:now,'owner-a',:now,'owner-a')"
        ),
        {"action": ACTION, "payload_hash": "a" * 64, "expires": expires, "now": now},
    )


def test_0027_precedes_the_current_catalog_head_and_follows_workspace_control() -> None:
    scripts = ScriptDirectory.from_config(alembic_config("sqlite://"))
    assert scripts.get_current_head() == catalog_schema.HEAD_REVISION
    assert catalog_schema.HEAD_REVISION == "0029_enterprise_knowledge_base_releases"
    script = scripts.get_revision(REVISION)
    assert script is not None
    assert script.down_revision == DOWN_REVISION


def test_upgrade_creates_policy_authority_and_backfills_workspace_modes(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "workspace-authorization.db")
    long_workspace_id = seed_0026(url, include_long_workspace=True)

    upgrade_0027(url)

    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        assert TABLE in inspector.get_table_names()
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == REVISION
            )
            rows = (
                connection.execute(
                    text(
                        f"SELECT id,tenant_id,workspace_id,mode,permission_model_version,revision,"
                        f"created_by,updated_by,enforced_at,enforced_by,disabled_at,disabled_by "
                        f"FROM {TABLE} ORDER BY tenant_id, workspace_id"
                    )
                )
                .mappings()
                .all()
            )

        assert [(row["tenant_id"], row["workspace_id"], row["mode"]) for row in rows] == [
            ("tenant-a", "workspace-archived-a", "disabled"),
            ("tenant-a", "workspace-default-tenant-a", "shadow"),
            ("tenant-a", long_workspace_id, "shadow"),
            ("tenant-b", "workspace-default-tenant-b", "shadow"),
        ]
        assert all(row["permission_model_version"] == 1 for row in rows)
        assert all(row["revision"] == 1 for row in rows)
        assert all(row["created_by"] == MIGRATION_ACTOR for row in rows)
        assert all(row["updated_by"] == MIGRATION_ACTOR for row in rows)
        assert all(len(row["id"]) == 64 for row in rows)
        assert len({row["id"] for row in rows}) == len(rows)
        archived = next(row for row in rows if row["workspace_id"] == "workspace-archived-a")
        assert archived["disabled_at"] is not None
        assert archived["disabled_by"] == MIGRATION_ACTOR
        assert archived["enforced_at"] is None
        assert archived["enforced_by"] is None
        for row in rows:
            if row["mode"] == "shadow":
                assert row["enforced_at"] is None
                assert row["enforced_by"] is None
                assert row["disabled_at"] is None
                assert row["disabled_by"] is None
    finally:
        engine.dispose()


def test_policy_constraints_foreign_keys_and_indexes_are_enforced(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "workspace-authorization-contract.db")
    seed_0026(url)
    upgrade_0027(url)
    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        uniques = {
            item["name"]: tuple(item.get("column_names") or ())
            for item in inspector.get_unique_constraints(TABLE)
        }
        assert uniques["uq_tenant_workspace_authorization_policies_scope_id"] == (
            "tenant_id",
            "id",
        )
        assert uniques["uq_tenant_workspace_authorization_policies_scope_workspace"] == (
            "tenant_id",
            "workspace_id",
        )
        foreign_keys = {
            item["name"]: (
                tuple(item.get("constrained_columns") or ()),
                item.get("referred_table"),
                tuple(item.get("referred_columns") or ()),
            )
            for item in inspector.get_foreign_keys(TABLE)
        }
        assert foreign_keys["fk_tenant_workspace_authorization_policies_tenant"] == (
            ("tenant_id",),
            "tenants",
            ("id",),
        )
        assert foreign_keys["fk_tenant_workspace_authorization_policies_scope_workspace"] == (
            ("tenant_id", "workspace_id"),
            "tenant_workspaces",
            ("tenant_id", "id"),
        )
        indexes = {
            item["name"]: tuple(item.get("column_names") or ())
            for item in inspector.get_indexes(TABLE)
        }
        assert indexes == {
            "ix_tw_auth_policies_tenant_mode_updated": (
                "tenant_id",
                "mode",
                "updated_at",
                "id",
            ),
            "ix_tw_auth_policies_tenant_workspace_mode": (
                "tenant_id",
                "workspace_id",
                "mode",
                "id",
            ),
        }
        checks = check_sql(inspector, TABLE)
        assert {
            "ck_tenant_workspace_auth_policies_mode",
            "ck_tenant_workspace_auth_policies_model_version_positive",
            "ck_tenant_workspace_auth_policies_revision_positive",
            "ck_tenant_workspace_auth_policies_mode_evidence",
        } <= set(checks)
        evidence = checks["ck_tenant_workspace_auth_policies_mode_evidence"]
        for fragment in (
            "mode = 'shadow'",
            "mode = 'enforced'",
            "mode = 'disabled'",
            "enforced_at is null",
            "enforced_at is not null",
            "disabled_at is null",
            "disabled_at is not null",
        ):
            assert fragment in evidence

        invalid_rows = [
            (
                "cross-tenant",
                "tenant-b",
                "workspace-default-tenant-a",
                "shadow",
                1,
                1,
                None,
                None,
                None,
                None,
            ),
            (
                "bad-shadow-evidence",
                "tenant-a",
                "workspace-default-tenant-a",
                "shadow",
                1,
                1,
                None,
                None,
                datetime.utcnow(),
                MIGRATION_ACTOR,
            ),
            (
                "bad-enforced-evidence",
                "tenant-a",
                "workspace-default-tenant-a",
                "enforced",
                1,
                1,
                None,
                None,
                None,
                None,
            ),
            (
                "bad-disabled-evidence",
                "tenant-a",
                "workspace-default-tenant-a",
                "disabled",
                1,
                1,
                None,
                None,
                None,
                None,
            ),
            (
                "bad-model-version",
                "tenant-a",
                "workspace-default-tenant-a",
                "shadow",
                0,
                1,
                None,
                None,
                None,
                None,
            ),
            (
                "bad-revision",
                "tenant-a",
                "workspace-default-tenant-a",
                "shadow",
                1,
                0,
                None,
                None,
                None,
                None,
            ),
        ]
        for (
            row_id,
            tenant_id,
            workspace_id,
            mode,
            model_version,
            revision,
            enforced_at,
            enforced_by,
            disabled_at,
            disabled_by,
        ) in invalid_rows:
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            f"INSERT INTO {TABLE} "
                            "(id,tenant_id,workspace_id,mode,permission_model_version,revision,created_at,"
                            "created_by,updated_at,updated_by,enforced_at,enforced_by,disabled_at,disabled_by) "
                            "VALUES (:id,:tenant_id,:workspace_id,:mode,:model_version,:revision,CURRENT_TIMESTAMP,"
                            ":actor,CURRENT_TIMESTAMP,:actor,:enforced_at,:enforced_by,:disabled_at,:disabled_by)"
                        ),
                        {
                            "id": row_id,
                            "tenant_id": tenant_id,
                            "workspace_id": workspace_id,
                            "mode": mode,
                            "model_version": model_version,
                            "revision": revision,
                            "actor": MIGRATION_ACTOR,
                            "enforced_at": enforced_at,
                            "enforced_by": enforced_by,
                            "disabled_at": disabled_at,
                            "disabled_by": disabled_by,
                        },
                    )
    finally:
        engine.dispose()


def test_approval_action_checks_accept_workspace_mode_change_and_reject_unknown_action(
    tmp_path: Path,
) -> None:
    url = sqlite_url(tmp_path / "workspace-authorization-approval.db")
    seed_0026(url)
    upgrade_0027(url)
    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        policy_checks = check_sql(inspector, "tenant_approval_policies")
        request_checks = check_sql(inspector, "tenant_approval_requests")
        assert ACTION in policy_checks["ck_tenant_approval_policies_action_type"]
        assert ACTION in request_checks["ck_tenant_approval_requests_action_type"]
        with engine.begin() as connection:
            insert_workspace_mode_approval_rows(connection)
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO tenant_approval_policies "
                        "(id,tenant_id,name,action_type,resource_scope,active_scope_key,status,required_approvals,"
                        "request_expiry_minutes,revision,created_at,created_by,updated_at,updated_by) "
                        "VALUES ('bad-action','tenant-a','Bad','workspace_authorization_unknown',NULL,"
                        "'bad-action|tenant-a','active',1,1440,1,CURRENT_TIMESTAMP,'owner-a',CURRENT_TIMESTAMP,'owner-a')"
                    )
                )
    finally:
        engine.dispose()


def test_downgrade_restores_0026_action_checks_and_preserves_workspace_rows(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "workspace-authorization-downgrade.db")
    seed_0026(url)
    upgrade_0027(url)

    command.downgrade(alembic_config(url), DOWN_REVISION)

    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        assert TABLE not in inspector.get_table_names()
        assert (
            ACTION
            not in check_sql(inspector, "tenant_approval_policies")[
                "ck_tenant_approval_policies_action_type"
            ]
        )
        assert (
            ACTION
            not in check_sql(inspector, "tenant_approval_requests")[
                "ck_tenant_approval_requests_action_type"
            ]
        )
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == DOWN_REVISION
            )
            assert (
                connection.execute(text("SELECT COUNT(*) FROM tenant_workspaces")).scalar_one() == 3
            )
    finally:
        engine.dispose()


def test_downgrade_fails_closed_while_workspace_mode_approval_rows_exist(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "workspace-authorization-downgrade-guard.db")
    seed_0026(url)
    upgrade_0027(url)
    engine = engine_for(url)
    try:
        with engine.begin() as connection:
            insert_workspace_mode_approval_rows(connection)
    finally:
        engine.dispose()

    with pytest.raises(Exception, match="workspace_authorization_mode_change"):
        command.downgrade(alembic_config(url), DOWN_REVISION)

    engine = engine_for(url)
    try:
        assert TABLE in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == REVISION
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
def test_offline_upgrade_sql_is_cross_dialect_and_downgrade_requires_online_preflight(
    url: str, dialect_marker: str
) -> None:
    output = StringIO()
    command.upgrade(
        alembic_config(url, output_buffer=output), f"{DOWN_REVISION}:{REVISION}", sql=True
    )
    sql = output.getvalue().upper()
    assert "CREATE TABLE TENANT_WORKSPACE_AUTHORIZATION_POLICIES" in sql
    assert "WORKSPACE_AUTHORIZATION_MODE_CHANGE" in sql
    assert dialect_marker in sql

    with pytest.raises(Exception, match="online|preflight|workspace_authorization_mode_change"):
        command.downgrade(
            alembic_config(url, output_buffer=StringIO()),
            f"{REVISION}:{DOWN_REVISION}",
            sql=True,
        )
