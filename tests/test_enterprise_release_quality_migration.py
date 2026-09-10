from __future__ import annotations

from importlib import import_module
from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import exc as sa_exc
from core import catalog_schema
from sqlalchemy import inspect, text

from tests.test_enterprise_knowledge_base_release_migration import (
    alembic_config,
    engine_for,
    seed_0028,
    sqlite_url,
    upgrade_0029,
)

REVISION = "0030_enterprise_release_quality_certification"
DOWN_REVISION = "0029_enterprise_knowledge_base_releases"
WAIVER_ACTION = "knowledge_base_release_quality_waiver"
TABLES = {
    "tenant_release_quality_gate_policies",
    "dataset_quality_baselines",
    "dataset_quality_baseline_items",
    "dataset_release_quality_certifications",
    "dataset_release_quality_certification_evidence",
    "dataset_release_quality_waivers",
    "dataset_release_quality_events",
}
EXPECTED_COLUMNS = {
    "tenant_release_quality_gate_policies": (
        "id",
        "tenant_id",
        "name",
        "scope_type",
        "scope_value",
        "channel_id",
        "active_scope_key",
        "status",
        "revision",
        "min_experiment_count",
        "min_judged_result_count",
        "min_judgment_coverage_bps",
        "min_exact_agreement_bps",
        "min_mean_score_milli",
        "max_conflicting_results",
        "require_all_experiments_completed",
        "require_no_degraded_results",
        "max_certification_age_minutes",
        "policy_digest",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "disabled_at",
        "disabled_by",
    ),
    "dataset_quality_baselines": (
        "id",
        "tenant_id",
        "dataset_id",
        "name",
        "normalized_name",
        "baseline_revision",
        "parent_baseline_id",
        "experiment_count",
        "query_count",
        "baseline_digest",
        "created_at",
        "created_by",
        "reason",
        "request_id",
    ),
    "dataset_quality_baseline_items": (
        "id",
        "tenant_id",
        "dataset_id",
        "baseline_id",
        "ordinal",
        "experiment_id",
        "experiment_sequence",
        "query_hash",
        "experiment_serving_generation",
        "strategy_digest",
        "result_digest",
        "evidence_digest",
        "judgment_digest",
        "created_at",
    ),
    "dataset_release_quality_certifications": (
        "id",
        "tenant_id",
        "dataset_id",
        "release_id",
        "baseline_id",
        "policy_id",
        "policy_revision",
        "release_manifest_digest",
        "release_mutation_generation",
        "release_serving_generation",
        "status",
        "experiment_count",
        "completed_experiment_count",
        "degraded_experiment_count",
        "query_count",
        "judged_result_count",
        "total_result_count",
        "judgment_count",
        "judgment_coverage_bps",
        "multi_judged_results",
        "unanimous_results",
        "conflicting_results",
        "exact_agreement_bps",
        "mean_score_milli",
        "failed_rule_count",
        "policy_snapshot_json",
        "summary_json",
        "evidence_digest",
        "certification_digest",
        "valid_until",
        "created_at",
        "created_by",
        "reason",
        "request_id",
    ),
    "dataset_release_quality_certification_evidence": (
        "id",
        "tenant_id",
        "dataset_id",
        "certification_id",
        "baseline_item_id",
        "experiment_id",
        "ordinal",
        "status",
        "result_count",
        "judged_result_count",
        "judgment_count",
        "multi_judged_results",
        "unanimous_results",
        "conflicting_results",
        "exact_agreement_bps",
        "mean_score_milli",
        "experiment_digest",
        "judgment_digest",
        "safe_facts_json",
        "created_at",
    ),
    "dataset_release_quality_waivers": (
        "id",
        "tenant_id",
        "dataset_id",
        "release_id",
        "channel_id",
        "policy_id",
        "policy_revision",
        "release_manifest_digest",
        "approval_request_id",
        "approval_execution_id",
        "reason",
        "valid_from",
        "expires_at",
        "waiver_digest",
        "created_at",
        "created_by",
        "request_id",
    ),
    "dataset_release_quality_events": (
        "id",
        "tenant_id",
        "dataset_id",
        "release_id",
        "channel_id",
        "certification_id",
        "waiver_id",
        "event_type",
        "event_sequence",
        "state",
        "previous_event_digest",
        "event_digest",
        "approval_request_id",
        "approval_execution_id",
        "actor_id",
        "reason",
        "safe_snapshot_json",
        "request_id",
        "occurred_at",
    ),
}


def migration_module():
    return import_module(
        "catalog_migrations.versions.0030_enterprise_release_quality_certification"
    )


def upgrade_0030(url: str) -> None:
    command.upgrade(alembic_config(url), REVISION)


def _insert_quality_policy(
    connection,
    *,
    policy_id: str,
    active_scope_key: str = "global:*",
    policy_digest: str = "a" * 64,
) -> None:
    connection.execute(
        text(
            "INSERT INTO tenant_release_quality_gate_policies "
            "(id,tenant_id,name,scope_type,scope_value,channel_id,active_scope_key,status,revision,"
            "min_experiment_count,min_judged_result_count,min_judgment_coverage_bps,"
            "min_exact_agreement_bps,min_mean_score_milli,max_conflicting_results,"
            "require_all_experiments_completed,require_no_degraded_results,"
            "max_certification_age_minutes,policy_digest,created_at,created_by,updated_at,updated_by) "
            "VALUES (:id,'tenant-a','Quality','global','*',NULL,:active_scope_key,'active',1,"
            "1,1,8000,8000,2000,0,1,1,1440,:policy_digest,CURRENT_TIMESTAMP,'owner-a',"
            "CURRENT_TIMESTAMP,'owner-a')"
        ),
        {
            "id": policy_id,
            "active_scope_key": active_scope_key,
            "policy_digest": policy_digest,
        },
    )


def test_0030_follows_release_control_plane_and_is_superseded_by_the_current_head() -> None:
    migration = migration_module()
    from alembic.script import ScriptDirectory

    scripts = ScriptDirectory.from_config(alembic_config("sqlite://"))
    # 0030 写的时候是本仓库的 head，之后 0031-0036 又接了上去。
    # 断言"它是 head"会随每次新增迁移误挂——改为断言链头一致 + 本迁移已被超越。
    assert scripts.get_current_head() == catalog_schema.HEAD_REVISION
    assert migration.revision == REVISION
    assert catalog_schema.HEAD_REVISION != REVISION
    assert migration.down_revision == DOWN_REVISION


def test_upgrade_creates_exact_quality_authority_and_approval_action(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "quality-0030.db")
    seed_0028(url)
    upgrade_0029(url)
    upgrade_0030(url)
    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        assert TABLES <= set(inspector.get_table_names())
        for table, expected in EXPECTED_COLUMNS.items():
            assert tuple(item["name"] for item in inspector.get_columns(table)) == expected
        for table in ("tenant_approval_policies", "tenant_approval_requests"):
            checks = {
                item["name"]: str(item.get("sqltext") or "")
                for item in inspector.get_check_constraints(table)
            }
            assert WAIVER_ACTION in checks[f"ck_{table}_action_type"]
        assert (
            engine.connect().execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == REVISION
        )
    finally:
        engine.dispose()


def test_active_quality_policy_scope_is_canonical_and_database_unique(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "quality-policy-authority.db")
    seed_0028(url)
    upgrade_0029(url)
    upgrade_0030(url)
    engine = engine_for(url)
    try:
        with engine.begin() as connection:
            _insert_quality_policy(connection, policy_id="quality-policy-a")
            with pytest.raises(sa_exc.IntegrityError):
                _insert_quality_policy(connection, policy_id="quality-policy-duplicate")
            with pytest.raises(sa_exc.IntegrityError):
                _insert_quality_policy(
                    connection,
                    policy_id="quality-policy-noncanonical",
                    active_scope_key="global:forged",
                )
            with pytest.raises(sa_exc.IntegrityError):
                _insert_quality_policy(
                    connection,
                    policy_id="quality-policy-nonhex",
                    policy_digest="g" * 64,
                )
    finally:
        engine.dispose()


def test_quality_tables_have_tenant_leading_foreign_keys_and_immutable_guards(
    tmp_path: Path,
) -> None:
    url = sqlite_url(tmp_path / "quality-fks.db")
    seed_0028(url)
    upgrade_0029(url)
    upgrade_0030(url)
    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        for table in TABLES:
            for fk in inspector.get_foreign_keys(table):
                columns = tuple(fk.get("constrained_columns") or ())
                assert columns and columns[0] == "tenant_id", (table, fk)
        triggers = set(
            engine.connect()
            .execute(text("SELECT name FROM sqlite_master WHERE type='trigger'"))
            .scalars()
        )
        for table in TABLES - {"tenant_release_quality_gate_policies"}:
            assert f"trg_{table}_no_update" in triggers
            assert f"trg_{table}_no_delete" in triggers
    finally:
        engine.dispose()


def test_certification_evidence_fk_target_has_tenant_dataset_identity(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "quality-evidence-fk-target.db")
    seed_0028(url)
    upgrade_0029(url)
    upgrade_0030(url)
    engine = engine_for(url)
    try:
        uniques = {
            item["name"]: tuple(item.get("column_names") or ())
            for item in inspect(engine).get_unique_constraints("dataset_quality_baseline_items")
        }
        assert uniques["uq_dataset_quality_baseline_items_scope_dataset_id"] == (
            "tenant_id",
            "dataset_id",
            "id",
        )
    finally:
        engine.dispose()


def test_offline_mysql_postgresql_include_quality_ddl_and_sqlite_fails_closed() -> None:
    for url, marker in (
        ("mysql+pymysql://u:p@localhost/rag4c", "DATETIME(6)"),
        ("postgresql+psycopg://u:p@localhost/rag4c", "TIMESTAMP"),
    ):
        output = StringIO()
        command.upgrade(
            alembic_config(url, output_buffer=output), f"{DOWN_REVISION}:{REVISION}", sql=True
        )
        sql = output.getvalue().upper()
        assert "CREATE TABLE TENANT_RELEASE_QUALITY_GATE_POLICIES" in sql
        assert "CREATE TABLE DATASET_RELEASE_QUALITY_CERTIFICATIONS" in sql
        assert WAIVER_ACTION.upper() in sql
        assert marker in sql
        assert "ACTIVE_SCOPE_KEY" in sql
        if url.startswith("mysql"):
            assert "CONCAT('CHANNEL:', SCOPE_VALUE)" in sql
        else:
            assert "'CHANNEL:' || SCOPE_VALUE" in sql
    try:
        command.upgrade(
            alembic_config("sqlite:///quality-offline.db", output_buffer=StringIO()),
            f"{DOWN_REVISION}:{REVISION}",
            sql=True,
        )
    except Exception as exc:
        assert "online" in str(exc).casefold() or "sqlite" in str(exc).casefold()
    else:
        raise AssertionError("SQLite offline quality migration must fail closed")


def test_clean_downgrade_restores_0029_and_nonempty_authority_blocks(tmp_path: Path) -> None:
    clean_url = sqlite_url(tmp_path / "quality-clean-down.db")
    seed_0028(clean_url)
    upgrade_0029(clean_url)
    upgrade_0030(clean_url)
    command.downgrade(alembic_config(clean_url), DOWN_REVISION)
    clean_engine = engine_for(clean_url)
    try:
        inspector = inspect(clean_engine)
        assert TABLES.isdisjoint(inspector.get_table_names())
        assert (
            clean_engine.connect()
            .execute(text("SELECT version_num FROM alembic_version"))
            .scalar_one()
            == DOWN_REVISION
        )
    finally:
        clean_engine.dispose()

    blocked_url = sqlite_url(tmp_path / "quality-blocked-down.db")
    seed_0028(blocked_url)
    upgrade_0029(blocked_url)
    upgrade_0030(blocked_url)
    blocked_engine = engine_for(blocked_url)
    with blocked_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenant_release_quality_gate_policies "
                "(id,tenant_id,name,scope_type,scope_value,channel_id,active_scope_key,status,revision,"
                "min_experiment_count,min_judged_result_count,min_judgment_coverage_bps,"
                "min_exact_agreement_bps,min_mean_score_milli,max_conflicting_results,"
                "require_all_experiments_completed,require_no_degraded_results,"
                "max_certification_age_minutes,policy_digest,created_at,created_by,updated_at,updated_by) "
                "VALUES ('quality-policy-a','tenant-a','Quality','global','*',NULL,'global:*','active',1,"
                "1,1,8000,8000,2000,0,1,1,1440,:digest,CURRENT_TIMESTAMP,'owner-a',CURRENT_TIMESTAMP,'owner-a')"
            ),
            {"digest": "a" * 64},
        )
    blocked_engine.dispose()
    try:
        command.downgrade(alembic_config(blocked_url), DOWN_REVISION)
    except Exception as exc:
        assert "0030" in str(exc) or "quality" in str(exc).casefold()
    else:
        raise AssertionError("nonempty quality authority must block downgrade")


def test_quality_event_approval_request_is_tenant_scoped(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "quality-event-approval-fk.db")
    seed_0028(url)
    upgrade_0029(url)
    upgrade_0030(url)
    engine = engine_for(url)
    try:
        foreign_keys = {
            item["name"]: (
                tuple(item.get("constrained_columns") or ()),
                item.get("referred_table"),
                tuple(item.get("referred_columns") or ()),
            )
            for item in inspect(engine).get_foreign_keys("dataset_release_quality_events")
        }
        assert foreign_keys["fk_dataset_release_quality_events_scope_approval"] == (
            ("tenant_id", "approval_request_id"),
            "tenant_approval_requests",
            ("tenant_id", "id"),
        )
    finally:
        engine.dispose()
