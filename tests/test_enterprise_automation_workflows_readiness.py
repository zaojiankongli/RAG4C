from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from core import catalog_schema as manifest
from core.enterprise_automation_workflows import (
    canonical_automation_action_request_digest,
    canonical_automation_event,
    canonical_automation_rule_revision,
    canonical_automation_run_digest,
)
from tests.test_enterprise_automation_workflows_migration import (
    _upgrade as upgrade_0035,
)
from tests.test_enterprise_knowledge_base_release_migration import (
    sqlite_url,
)
from tests.test_enterprise_task_operations_migration import _upgrade as upgrade_0034


REVISION = "0035_enterprise_automation_workflows"
TABLES = {
    "tenant_automation_rules",
    "tenant_automation_rule_revisions",
    "tenant_automation_source_cursors",
    "tenant_automation_runs",
    "tenant_automation_action_requests",
    "tenant_automation_events",
}
TIME = "2026-08-29T12:00:00.000000Z"


def _engine(tmp_path: Path, name: str, *, full: bool = True):
    url = sqlite_url(tmp_path / name)
    (upgrade_0035 if full else upgrade_0034)(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT OR IGNORE INTO dataset_workspace_ownerships "
                "(id,tenant_id,dataset_id,workspace_id,revision,created_at,created_by,updated_at,updated_by) "
                "VALUES ('ownership-stage25','tenant-a','dataset-a','workspace-a',1,"
                "CURRENT_TIMESTAMP,'test:stage25',CURRENT_TIMESTAMP,'test:stage25')"
            )
        )
    return engine


def _rewrite_sqlite_table_ddl(engine, table: str, old: str, new: str):
    url = str(engine.url)
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA writable_schema=ON")
        connection.execute(
            text(
                "UPDATE sqlite_master SET sql=replace(sql, :old, :new) "
                "WHERE type='table' AND name=:name"
            ),
            {"old": old, "new": new, "name": table},
        )
        connection.exec_driver_sql("PRAGMA writable_schema=OFF")
    engine.dispose()
    return create_engine(url)


def _seed_valid_automation_graph(engine) -> None:
    revision = canonical_automation_rule_revision(
        {
            "tenant_id": "tenant-a",
            "rule_id": "rule-a",
            "rule_revision_id": "revision-a",
            "revision": 1,
            "trigger_code": "task_failed",
            "condition_code": "always",
            "condition_params": {},
            "action_plan": [
                {"action_code": "pause_rule", "params": {"reason_code": "maintenance"}}
            ],
            "created_at": TIME,
            "created_by": "owner-a",
        }
    )
    trigger_digest = "a" * 64
    run_digest = canonical_automation_run_digest(
        {
            "tenant_id": "tenant-a",
            "rule_id": "rule-a",
            "rule_revision_id": "revision-a",
            "trigger_event_digest": trigger_digest,
        }
    )
    action_digest = canonical_automation_action_request_digest(
        {
            "tenant_id": "tenant-a",
            "rule_id": "rule-a",
            "rule_revision_id": "revision-a",
            "trigger_event_digest": trigger_digest,
            "target_kind": "automation_rule",
            "target_id": "rule-a",
            "action_code": "pause_rule",
            "step_index": 0,
            "safe_params": {"reason_code": "maintenance"},
        }
    )
    first_event = canonical_automation_event(
        {
            "tenant_id": "tenant-a",
            "rule_id": "rule-a",
            "run_id": "run-a",
            "stream_key": "run:run-a",
            "sequence": 1,
            "event_type": "run_started",
            "previous_event_digest": None,
            "actor_id": "system:automation",
            "request_id": "request-a",
            "safe_snapshot": {
                "rule_revision_id": "revision-a",
                "definition_digest": revision["definition_digest"],
                "trigger_event_digest": trigger_digest,
            },
            "occurred_at": TIME,
        }
    )
    second_event = canonical_automation_event(
        {
            "tenant_id": "tenant-a",
            "rule_id": "rule-a",
            "run_id": "run-a",
            "stream_key": "run:run-a",
            "sequence": 2,
            "event_type": "trigger_observed",
            "previous_event_digest": first_event["event_digest"],
            "actor_id": "system:automation",
            "request_id": "request-b",
            "safe_snapshot": {
                "trigger_code": "task_failed",
                "source_stream_id": "stream-a",
                "source_event_id": "source-event-a",
                "source_event_digest": trigger_digest,
                "sequence": 1,
                "status": "failed",
                "severity": "error",
            },
            "occurred_at": TIME,
        }
    )
    third_event = canonical_automation_event(
        {
            "tenant_id": "tenant-a",
            "rule_id": "rule-a",
            "run_id": "run-a",
            "stream_key": "run:run-a",
            "sequence": 3,
            "event_type": "action_requested",
            "previous_event_digest": second_event["event_digest"],
            "actor_id": "system:automation",
            "request_id": "request-c",
            "safe_snapshot": {
                "step_index": 0,
                "action_code": "pause_rule",
                "action_status": "requested",
                "target_kind": "automation_rule",
                "target_id": "rule-a",
                "action_digest": action_digest,
            },
            "occurred_at": TIME,
        }
    )
    fourth_event = canonical_automation_event(
        {
            "tenant_id": "tenant-a",
            "rule_id": "rule-a",
            "run_id": "run-a",
            "stream_key": "run:run-a",
            "sequence": 4,
            "event_type": "run_completed",
            "previous_event_digest": third_event["event_digest"],
            "actor_id": "system:automation",
            "request_id": "request-d",
            "safe_snapshot": {
                "run_status": "requested",
                "action_count": 1,
                "requested_count": 1,
                "rejected_count": 0,
            },
            "occurred_at": TIME,
        }
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenant_automation_rules "
                "(id,tenant_id,name,normalized_name,status,active_rule_key,revision,current_revision_id,"
                "workspace_id,dataset_id,priority,created_at,created_by,updated_at,updated_by,archived_at,archived_by) "
                "VALUES (:id,:tenant,:name,:normalized,:status,:active_key,:revision,:current_revision,"
                "NULL,NULL,100,:created_at,:created_by,:updated_at,:updated_by,NULL,NULL)"
            ),
            {
                "id": "rule-a",
                "tenant": "tenant-a",
                "name": "Rule A",
                "normalized": "rule a",
                "status": "active",
                "active_key": "rule a",
                "revision": 1,
                "current_revision": "revision-a",
                "created_at": TIME,
                "created_by": "owner-a",
                "updated_at": TIME,
                "updated_by": "owner-a",
            },
        )
        connection.execute(
            text(
                "INSERT INTO tenant_automation_rule_revisions "
                "(id,tenant_id,rule_id,revision,trigger_code,condition_code,condition_params_json,"
                "action_plan_json,definition_digest,created_at,created_by) "
                "VALUES (:id,:tenant,:rule,:revision,:trigger,:condition,:params,:actions,:digest,:created_at,:created_by)"
            ),
            {
                "id": "revision-a",
                "tenant": "tenant-a",
                "rule": "rule-a",
                "revision": 1,
                "trigger": revision["trigger_code"],
                "condition": revision["condition_code"],
                "params": json.dumps(revision["condition_params"], ensure_ascii=False),
                "actions": json.dumps(revision["action_plan"], ensure_ascii=False),
                "digest": revision["definition_digest"],
                "created_at": TIME,
                "created_by": "owner-a",
            },
        )
        connection.execute(
            text(
                "INSERT INTO tenant_automation_source_cursors "
                "(id,tenant_id,rule_id,source_kind,source_stream_id,last_sequence,last_event_digest,status,"
                "lease_owner,lease_until,revision,updated_at) "
                "VALUES ('cursor-a','tenant-a','rule-a','task_failed','stream-a',0,NULL,'idle',NULL,NULL,1,:updated_at)"
            ),
            {"updated_at": TIME},
        )
        connection.execute(
            text(
                "INSERT INTO tenant_automation_runs "
                "(id,tenant_id,rule_id,rule_revision_id,trigger_event_id,trigger_event_digest,status,"
                "condition_matched,action_count,requested_count,rejected_count,idempotency_digest,started_at,"
                "completed_at,safe_error_code,safe_error,created_at,updated_at) "
                "VALUES ('run-a','tenant-a','rule-a','revision-a','source-event-a',:trigger_digest,'requested',"
                "1,1,1,0,:idempotency,:started_at,:completed_at,NULL,NULL,:created_at,:updated_at)"
            ),
            {
                "trigger_digest": trigger_digest,
                "idempotency": run_digest,
                "started_at": TIME,
                "completed_at": TIME,
                "created_at": TIME,
                "updated_at": TIME,
            },
        )
        connection.execute(
            text(
                "INSERT INTO tenant_automation_action_requests "
                "(id,tenant_id,run_id,rule_id,step_index,action_code,status,target_kind,target_id,target_revision,"
                "target_digest,idempotency_key_digest,safe_params_json,safe_reason,approval_request_id,notification_id,"
                "task_id,requested_at,dispatched_at,applied_at,rejected_at,expires_at,created_at,updated_at) "
                "VALUES ('action-a','tenant-a','run-a','rule-a',0,'pause_rule','requested','automation_rule','rule-a',NULL,NULL,"
                ":idempotency,:params,'automation_rule_matched',NULL,NULL,NULL,:requested_at,NULL,NULL,NULL,:expires_at,:created_at,:updated_at)"
            ),
            {
                "idempotency": action_digest,
                "params": json.dumps({"reason_code": "maintenance"}),
                "requested_at": TIME,
                "expires_at": "2026-08-30T12:00:00.000000Z",
                "created_at": TIME,
                "updated_at": TIME,
            },
        )
        for event in (first_event, second_event, third_event, fourth_event):
            connection.execute(
                text(
                    "INSERT INTO tenant_automation_events "
                    "(id,tenant_id,rule_id,run_id,stream_key,sequence,event_type,previous_event_digest,"
                    "event_digest,actor_id,request_id,safe_snapshot_json,occurred_at) "
                    "VALUES (:id,:tenant,:rule,:run,:stream,:sequence,:event_type,:previous,:digest,:actor,:request,:snapshot,:occurred_at)"
                ),
                {
                    "id": f"event-{event['sequence']}",
                    "tenant": event["tenant_id"],
                    "rule": event["rule_id"],
                    "run": event["run_id"],
                    "stream": event["stream_key"],
                    "sequence": event["sequence"],
                    "event_type": event["event_type"],
                    "previous": event["previous_event_digest"],
                    "digest": event["event_digest"],
                    "actor": event["actor_id"],
                    "request": event["request_id"],
                    "snapshot": json.dumps(event["safe_snapshot"], ensure_ascii=False),
                    "occurred_at": TIME,
                },
            )


def test_stage25_manifest_is_head_and_covers_all_six_tables() -> None:
    assert manifest.ENTERPRISE_AUTOMATION_WORKFLOWS_REVISION == REVISION
    assert manifest.ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_TABLES == TABLES
    assert TABLES <= manifest.HEAD_CATALOG_TABLES
    for table in TABLES:
        assert table in manifest.ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_COLUMNS
        assert table in manifest.ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_NOT_NULL
        assert table in manifest.ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_UNIQUES
        assert table in manifest.ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_FOREIGN_KEYS
        assert table in manifest.ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_CHECK_FRAGMENTS
        assert table in manifest.ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_INDEXES
        assert table in manifest._HEAD_REQUIRED_COLUMNS
        assert table in manifest._HEAD_REQUIRED_NOT_NULL
        assert table in manifest._HEAD_REQUIRED_UNIQUES
        assert table in manifest._HEAD_REQUIRED_FOREIGN_KEYS
        assert table in manifest._HEAD_REQUIRED_CHECK_FRAGMENTS
        assert table in manifest._HEAD_REQUIRED_INDEXES


def test_stage25_manifest_requires_complete_trigger_and_event_allowlists() -> None:
    trigger_fragments = manifest.ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_CHECK_FRAGMENTS[
        "tenant_automation_source_cursors"
    ]["ck_tenant_automation_source_cursors_source_kind"]
    event_fragments = manifest.ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_CHECK_FRAGMENTS[
        "tenant_automation_events"
    ]["ck_tenant_automation_events_type"]
    assert {
        "task_failed",
        "task_source_stale",
        "source_sync_failed",
        "release_quality_alert_opened",
        "release_recertification_blocked",
        "approval_request_terminal",
    } <= set(trigger_fragments)
    assert {
        "rule_created",
        "revision_created",
        "revision_activated",
        "rule_paused",
        "trigger_observed",
        "condition_not_matched",
        "run_started",
        "action_requested",
        "action_rejected",
        "run_completed",
        "run_failed",
    } <= set(event_fragments)


def test_0035_capability_is_not_available_before_0035_and_ready_at_0035(tmp_path: Path) -> None:
    old_engine = _engine(tmp_path, "pre-0035.db", full=False)
    try:
        assert manifest.inspect_enterprise_automation_workflows_capability(old_engine) == (
            "not_available",
            (),
        )
    finally:
        old_engine.dispose()

    engine = _engine(tmp_path, "stage25-ready.db")
    try:
        assert manifest.inspect_enterprise_automation_workflows_capability(engine) == ("ready", ())
        assert manifest.inspect_enterprise_task_operations_capability(engine) == ("ready", ())
        assert manifest.inspect_enterprise_content_recovery_capability(engine) == ("ready", ())
        assert manifest.inspect_enterprise_notification_center_capability(engine) == ("ready", ())
        state = manifest.inspect_catalog_schema(engine)
        assert state.revision == REVISION
        assert state.status == "behind"
    finally:
        engine.dispose()


def test_capability_order_places_automation_after_task_operations() -> None:
    from server import enterprise_readiness_api as api

    keys = [item.key for item in api._CAPABILITIES]
    assert keys.index("enterprise_automation_workflows") == (
        keys.index("enterprise_task_operations") + 1
    )
    capability = {item.key: item for item in api._CAPABILITIES}["enterprise_automation_workflows"]
    assert capability.revision == REVISION
    assert capability.tables == TABLES
    assert capability.issue_fragments == manifest.ENTERPRISE_AUTOMATION_WORKFLOWS_ISSUE_FRAGMENTS
    assert keys.index("enterprise_knowledge_serving_reliability") == (
        keys.index("enterprise_automation_workflows") + 1
    )


def test_capability_rejects_missing_event_guard(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "missing-event-guard.db")
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP TRIGGER trg_tenant_automation_events_validate_insert"))
        state, issues = manifest.inspect_enterprise_automation_workflows_capability(engine)
        assert state == "unavailable"
        assert any("event insert" in issue.lower() for issue in issues)
    finally:
        engine.dispose()


def test_capability_accepts_active_rule_with_pending_revision_head(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "active-pending-revision.db")
    try:
        _seed_valid_automation_graph(engine)
        revision = canonical_automation_rule_revision(
            {
                "tenant_id": "tenant-a",
                "rule_id": "rule-a",
                "rule_revision_id": "revision-b",
                "revision": 2,
                "trigger_code": "task_failed",
                "condition_code": "always",
                "condition_params": {},
                "action_plan": [
                    {"action_code": "pause_rule", "params": {"reason_code": "maintenance"}}
                ],
                "created_at": TIME,
                "created_by": "owner-a",
            }
        )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenant_automation_rule_revisions "
                    "(id,tenant_id,rule_id,revision,trigger_code,condition_code,condition_params_json,"
                    "action_plan_json,definition_digest,created_at,created_by) "
                    "VALUES (:id,:tenant,:rule,:revision,:trigger,:condition,:params,:actions,:digest,:created_at,:created_by)"
                ),
                {
                    "id": revision["rule_revision_id"],
                    "tenant": revision["tenant_id"],
                    "rule": revision["rule_id"],
                    "revision": revision["revision"],
                    "trigger": revision["trigger_code"],
                    "condition": revision["condition_code"],
                    "params": json.dumps(revision["condition_params"], ensure_ascii=False),
                    "actions": json.dumps(revision["action_plan"], ensure_ascii=False),
                    "digest": revision["definition_digest"],
                    "created_at": TIME,
                    "created_by": "owner-a",
                },
            )
            connection.execute(
                text("UPDATE tenant_automation_rules SET revision=2 WHERE id='rule-a'")
            )
        assert manifest.inspect_enterprise_automation_workflows_capability(engine) == ("ready", ())
    finally:
        engine.dispose()


def test_capability_rejects_active_rule_current_revision_mismatch(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "active-current-mismatch.db")
    try:
        _seed_valid_automation_graph(engine)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE tenant_automation_rules SET current_revision_id='missing-revision' "
                    "WHERE id='rule-a'"
                )
            )
        state, issues = manifest.inspect_enterprise_automation_workflows_capability(engine)
        assert state == "unavailable"
        assert any("current revision" in issue.lower() for issue in issues)
    finally:
        engine.dispose()


def test_capability_fails_closed_for_unknown_automation_dialect() -> None:
    connection = SimpleNamespace(dialect=SimpleNamespace(name="oracle"))
    issues = manifest._automation_guard_issues(connection)
    assert any("unsupported" in issue.lower() and "oracle" in issue.lower() for issue in issues)


def test_capability_rejects_definition_digest_and_bounded_schema(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "definition-digest.db")
    try:
        _seed_valid_automation_graph(engine)
        with engine.begin() as connection:
            connection.execute(text("DROP TRIGGER trg_tenant_automation_rule_revisions_no_update"))
            connection.execute(
                text(
                    "UPDATE tenant_automation_rule_revisions SET definition_digest=:digest "
                    "WHERE id='revision-a'"
                ),
                {"digest": "f" * 64},
            )
        state, issues = manifest.inspect_enterprise_automation_workflows_capability(engine)
        assert state == "unavailable"
        assert any("definition digest" in issue.lower() for issue in issues)
    finally:
        engine.dispose()

    engine = _engine(tmp_path, "bounded-schema.db")
    try:
        _seed_valid_automation_graph(engine)
        with engine.begin() as connection:
            connection.execute(text("DROP TRIGGER trg_tenant_automation_rule_revisions_no_update"))
            connection.execute(
                text(
                    "UPDATE tenant_automation_rule_revisions SET action_plan_json=:actions "
                    "WHERE id='revision-a'"
                ),
                {
                    "actions": json.dumps(
                        [
                            {
                                "step_index": 0,
                                "action_code": "pause_rule",
                                "params": {"url": "https://bad"},
                            }
                        ]
                    )
                },
            )
        state, issues = manifest.inspect_enterprise_automation_workflows_capability(engine)
        assert state == "unavailable"
        assert any("bounded" in issue.lower() or "action plan" in issue.lower() for issue in issues)
    finally:
        engine.dispose()


def test_capability_rejects_cursor_lease_sequence_and_digest(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "cursor-fence.db")
    try:
        _seed_valid_automation_graph(engine)
        with engine.begin() as connection:
            connection.execute(text("PRAGMA ignore_check_constraints=ON"))
            connection.execute(
                text(
                    "UPDATE tenant_automation_source_cursors SET last_sequence=0,last_event_digest=:digest,"
                    "lease_owner='worker-a',lease_until=NULL WHERE id='cursor-a'"
                ),
                {"digest": "A" * 64},
            )
            connection.execute(text("PRAGMA ignore_check_constraints=OFF"))
        state, issues = manifest.inspect_enterprise_automation_workflows_capability(engine)
        assert state == "unavailable"
        rendered = " ".join(issues).lower()
        assert "cursor" in rendered
        assert "digest" in rendered
        assert "lease" in rendered
    finally:
        engine.dispose()


def test_capability_rejects_run_and_action_lifecycle(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "run-action-lifecycle.db")
    try:
        _seed_valid_automation_graph(engine)
        with engine.begin() as connection:
            connection.execute(text("PRAGMA ignore_check_constraints=ON"))
            connection.execute(
                text(
                    "UPDATE tenant_automation_runs SET status='completed',completed_at=NULL "
                    "WHERE id='run-a'"
                )
            )
            connection.execute(
                text(
                    "UPDATE tenant_automation_action_requests SET status='applied',applied_at=NULL "
                    "WHERE id='action-a'"
                )
            )
            connection.execute(text("PRAGMA ignore_check_constraints=OFF"))
        state, issues = manifest.inspect_enterprise_automation_workflows_capability(engine)
        assert state == "unavailable"
        rendered = " ".join(issues).lower()
        assert "run" in rendered and "lifecycle" in rendered
        assert "action" in rendered and "lifecycle" in rendered
    finally:
        engine.dispose()


def test_capability_rejects_broken_event_chain(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "event-chain.db")
    try:
        _seed_valid_automation_graph(engine)
        with engine.begin() as connection:
            connection.execute(text("DROP TRIGGER trg_tenant_automation_events_no_update"))
            connection.execute(text("PRAGMA ignore_check_constraints=ON"))
            connection.execute(
                text(
                    "UPDATE tenant_automation_events SET previous_event_digest=:previous "
                    "WHERE id='event-2'"
                ),
                {"previous": "d" * 64},
            )
            connection.execute(text("PRAGMA ignore_check_constraints=OFF"))
        state, issues = manifest.inspect_enterprise_automation_workflows_capability(engine)
        assert state == "unavailable"
        assert any(
            "event chain" in issue.lower() or "hash chain" in issue.lower() for issue in issues
        )
    finally:
        engine.dispose()


def test_readiness_api_returns_503_for_stage25_damage(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "readiness-api.db")
    try:
        from tests.test_enterprise_knowledge_serving_migration import upgrade_0036

        upgrade_0036(str(engine.url))
        with engine.begin() as connection:
            connection.execute(text("DROP INDEX ix_tenant_automation_runs_tenant_status_started"))
        api = __import__(
            "server.enterprise_readiness_api", fromlist=["build_enterprise_readiness_router"]
        )
        app = FastAPI()
        app.include_router(
            api.build_enterprise_readiness_router(
                engine_provider=lambda: engine,
                schema_inspector=manifest.inspect_catalog_schema,
                read_only_proof=lambda _engine: True,
            )
        )
        response = TestClient(app).get("/api/enterprise/readiness")
        assert response.status_code == 503
        payload = response.json()
        assert payload["status"] == "malformed"
        assert payload["mutations_safe"] is False
        assert payload["missing_capability_groups"] == ["enterprise_automation_workflows"]
    finally:
        engine.dispose()


def test_capability_rejects_extra_trigger_and_event_check_values(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "exact-allowlists.db")
    try:
        _seed_valid_automation_graph(engine)
        engine = _rewrite_sqlite_table_ddl(
            engine,
            "tenant_automation_source_cursors",
            "'approval_request_terminal')",
            "'approval_request_terminal','custom_execute')",
        )
        engine = _rewrite_sqlite_table_ddl(
            engine,
            "tenant_automation_rule_revisions",
            "'approval_request_terminal')",
            "'approval_request_terminal','custom_execute')",
        )
        engine = _rewrite_sqlite_table_ddl(
            engine,
            "tenant_automation_events",
            "'run_failed')",
            "'run_failed','custom_webhook')",
        )
        state, issues = manifest.inspect_enterprise_automation_workflows_capability(engine)
        assert state == "unavailable"
        rendered = " ".join(issues).lower()
        assert "exact" in rendered
        assert "allow-list" in rendered
        assert "custom_execute" in rendered or "custom_webhook" in rendered
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("table", "old", "new", "unexpected"),
    [
        (
            "tenant_automation_rules",
            "'draft','active','paused','archived')",
            "'draft','active','paused','archived','custom_execute')",
            "custom_execute",
        ),
        (
            "tenant_automation_rule_revisions",
            "'source_is_stale')",
            "'source_is_stale','custom_execute')",
            "custom_execute",
        ),
        (
            "tenant_automation_source_cursors",
            "'idle','claimed','blocked')",
            "'idle','claimed','blocked','custom_execute')",
            "custom_execute",
        ),
        (
            "tenant_automation_runs",
            "'started','not_matched','requested','completed','failed','blocked')",
            "'started','not_matched','requested','completed','failed','blocked','custom_execute')",
            "custom_execute",
        ),
        (
            "tenant_automation_action_requests",
            "'requested','dispatched','applied','rejected','expired')",
            "'requested','dispatched','applied','rejected','expired','custom_execute')",
            "custom_execute",
        ),
    ],
)
def test_capability_rejects_extra_condition_and_status_check_values(
    tmp_path: Path,
    table: str,
    old: str,
    new: str,
    unexpected: str,
) -> None:
    engine = _engine(tmp_path, f"exact-{table}.db")
    try:
        _seed_valid_automation_graph(engine)
        engine = _rewrite_sqlite_table_ddl(engine, table, old, new)
        state, issues = manifest.inspect_enterprise_automation_workflows_capability(engine)
        assert state == "unavailable"
        rendered = " ".join(issues).lower()
        assert "exact" in rendered
        assert "allow-list" in rendered
        assert unexpected in rendered
    finally:
        engine.dispose()


def test_postgresql_any_array_check_reflection_is_parsed_exactly() -> None:
    sql = (
        "condition_code = ANY (ARRAY["
        "'always'::character varying,"
        "'status_is'::character varying,"
        "'action_required'::character varying,"
        "'severity_at_least'::character varying,"
        "'attempt_exhausted'::character varying,"
        "'source_is_stale'::character varying])"
    )
    assert manifest._automation_check_allowlist_values(sql, "condition_code") == (
        "always",
        "status_is",
        "action_required",
        "severity_at_least",
        "attempt_exhausted",
        "source_is_stale",
    )


def test_capability_rejects_run_idempotency_digest_recalculation_mismatch(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "run-digest.db")
    try:
        _seed_valid_automation_graph(engine)
        with engine.begin() as connection:
            connection.execute(text("PRAGMA ignore_check_constraints=ON"))
            connection.execute(
                text(
                    "UPDATE tenant_automation_runs SET idempotency_digest=:digest WHERE id='run-a'"
                ),
                {"digest": "d" * 64},
            )
            connection.execute(text("PRAGMA ignore_check_constraints=OFF"))
        state, issues = manifest.inspect_enterprise_automation_workflows_capability(engine)
        assert state == "unavailable"
        rendered = " ".join(issues).lower()
        assert "run" in rendered
        assert "idempotency" in rendered
    finally:
        engine.dispose()


def test_capability_rejects_action_idempotency_digest_recalculation_mismatch(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path, "action-digest.db")
    try:
        _seed_valid_automation_graph(engine)
        with engine.begin() as connection:
            connection.execute(text("PRAGMA ignore_check_constraints=ON"))
            connection.execute(
                text(
                    "UPDATE tenant_automation_action_requests SET idempotency_key_digest=:digest "
                    "WHERE id='action-a'"
                ),
                {"digest": "d" * 64},
            )
            connection.execute(text("PRAGMA ignore_check_constraints=OFF"))
        state, issues = manifest.inspect_enterprise_automation_workflows_capability(engine)
        assert state == "unavailable"
        rendered = " ".join(issues).lower()
        assert "action" in rendered
        assert "idempotency" in rendered
    finally:
        engine.dispose()


def test_capability_rejects_action_params_that_do_not_match_revision_schema(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "action-params.db")
    try:
        _seed_valid_automation_graph(engine)
        with engine.begin() as connection:
            connection.execute(text("PRAGMA ignore_check_constraints=ON"))
            connection.execute(
                text(
                    "UPDATE tenant_automation_action_requests SET safe_params_json=:params "
                    "WHERE id='action-a'"
                ),
                {"params": json.dumps({"reason_code": "maintenance", "status": "failed"})},
            )
            connection.execute(text("PRAGMA ignore_check_constraints=OFF"))
        state, issues = manifest.inspect_enterprise_automation_workflows_capability(engine)
        assert state == "unavailable"
        rendered = " ".join(issues).lower()
        assert "action" in rendered
        assert "params" in rendered or "definition" in rendered
    finally:
        engine.dispose()


def test_capability_rejects_action_target_outside_allowlist(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "action-target.db")
    try:
        _seed_valid_automation_graph(engine)
        with engine.begin() as connection:
            connection.execute(text("PRAGMA ignore_check_constraints=ON"))
            connection.execute(
                text(
                    "UPDATE tenant_automation_action_requests "
                    "SET target_kind='custom_webhook',target_id='webhook-a' WHERE id='action-a'"
                )
            )
            connection.execute(text("PRAGMA ignore_check_constraints=OFF"))
        state, issues = manifest.inspect_enterprise_automation_workflows_capability(engine)
        assert state == "unavailable"
        rendered = " ".join(issues).lower()
        assert "action" in rendered
        assert "target" in rendered
        assert "allow" in rendered
    finally:
        engine.dispose()


def test_capability_rejects_action_target_fence_without_digest_pair(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "action-fence.db")
    try:
        _seed_valid_automation_graph(engine)
        with engine.begin() as connection:
            connection.execute(text("PRAGMA ignore_check_constraints=ON"))
            connection.execute(
                text(
                    "UPDATE tenant_automation_action_requests SET target_revision=2,target_digest=NULL "
                    "WHERE id='action-a'"
                )
            )
            connection.execute(text("PRAGMA ignore_check_constraints=OFF"))
        state, issues = manifest.inspect_enterprise_automation_workflows_capability(engine)
        assert state == "unavailable"
        rendered = " ".join(issues).lower()
        assert "action" in rendered
        assert "fence" in rendered or "target" in rendered
    finally:
        engine.dispose()


def test_capability_rejects_unproven_action_target_fence_even_with_digest_pair(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path, "action-fence-unproven.db")
    try:
        _seed_valid_automation_graph(engine)
        with engine.begin() as connection:
            connection.execute(text("PRAGMA ignore_check_constraints=ON"))
            connection.execute(
                text(
                    "UPDATE tenant_automation_action_requests "
                    "SET target_revision=2,target_digest=:digest WHERE id='action-a'"
                ),
                {"digest": "e" * 64},
            )
            connection.execute(text("PRAGMA ignore_check_constraints=OFF"))
        state, issues = manifest.inspect_enterprise_automation_workflows_capability(engine)
        assert state == "unavailable"
        rendered = " ".join(issues).lower()
        assert "unproven" in rendered
        assert "target fence" in rendered
    finally:
        engine.dispose()


def test_capability_rejects_unsafe_action_reason(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "action-reason.db")
    try:
        _seed_valid_automation_graph(engine)
        with engine.begin() as connection:
            connection.execute(text("PRAGMA ignore_check_constraints=ON"))
            connection.execute(
                text(
                    "UPDATE tenant_automation_action_requests SET safe_reason=:reason WHERE id='action-a'"
                ),
                {"reason": "SELECT * FROM users"},
            )
            connection.execute(text("PRAGMA ignore_check_constraints=OFF"))
        state, issues = manifest.inspect_enterprise_automation_workflows_capability(engine)
        assert state == "unavailable"
        rendered = " ".join(issues).lower()
        assert "action" in rendered
        assert "reason" in rendered
        assert "unsafe" in rendered or "safe" in rendered
    finally:
        engine.dispose()


def test_capability_rejects_run_counts_that_disagree_with_action_rows(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "run-counts.db")
    try:
        _seed_valid_automation_graph(engine)
        with engine.begin() as connection:
            connection.execute(text("PRAGMA ignore_check_constraints=ON"))
            connection.execute(
                text("UPDATE tenant_automation_runs SET requested_count=0 WHERE id='run-a'")
            )
            connection.execute(text("PRAGMA ignore_check_constraints=OFF"))
        state, issues = manifest.inspect_enterprise_automation_workflows_capability(engine)
        assert state == "unavailable"
        rendered = " ".join(issues).lower()
        assert "run" in rendered
        assert "count" in rendered
    finally:
        engine.dispose()


def test_capability_rejects_action_ownership_mismatch(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "action-ownership.db")
    try:
        _seed_valid_automation_graph(engine)
        with engine.begin() as connection:
            connection.execute(text("PRAGMA foreign_keys=OFF"))
            connection.execute(text("PRAGMA ignore_check_constraints=ON"))
            connection.execute(
                text(
                    "UPDATE tenant_automation_action_requests SET rule_id='rule-other' "
                    "WHERE id='action-a'"
                )
            )
            connection.execute(text("PRAGMA ignore_check_constraints=OFF"))
        state, issues = manifest.inspect_enterprise_automation_workflows_capability(engine)
        assert state == "unavailable"
        rendered = " ".join(issues).lower()
        assert "action" in rendered
        assert "ownership" in rendered or "scope" in rendered or "rule" in rendered
    finally:
        engine.dispose()
