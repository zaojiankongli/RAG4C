from __future__ import annotations

import pytest
from sqlalchemy.exc import NoInspectionAvailable


def test_enterprise_task_operations_revision_is_head() -> None:
    from core import catalog_schema as api

    assert api.ENTERPRISE_TASK_OPERATIONS_REVISION == "0034_enterprise_task_operations"


def test_enterprise_task_operations_manifest_contains_five_table_contract() -> None:
    from core import catalog_schema as api

    tables = {
        "tenant_task_projections",
        "tenant_task_operator_actions",
        "tenant_task_events",
        "tenant_task_saved_views",
        "tenant_task_reconciliation_runs",
    }

    assert api.ENTERPRISE_TASK_OPERATIONS_REQUIRED_TABLES == tables
    assert tables <= api.HEAD_CATALOG_TABLES
    for table in tables:
        assert table in api._HEAD_REQUIRED_COLUMNS
        assert table in api._HEAD_REQUIRED_NOT_NULL
        assert table in api._HEAD_REQUIRED_UNIQUES
        assert table in api._HEAD_REQUIRED_FOREIGN_KEYS
        assert table in api._HEAD_REQUIRED_CHECK_FRAGMENTS
        assert table in api._HEAD_REQUIRED_INDEXES


def test_enterprise_task_operations_is_exposed_as_readiness_capability() -> None:
    from core import catalog_schema as manifest
    from server import enterprise_readiness_api as api

    capabilities = {item.key: item for item in api._CAPABILITIES}
    capability = capabilities["enterprise_task_operations"]

    assert capability.revision == manifest.ENTERPRISE_TASK_OPERATIONS_REVISION
    assert capability.tables == manifest.ENTERPRISE_TASK_OPERATIONS_REQUIRED_TABLES
    assert capability.issue_fragments == manifest.ENTERPRISE_TASK_OPERATIONS_ISSUE_FRAGMENTS
    assert list(capabilities).index("enterprise_task_operations") == (
        list(capabilities).index("enterprise_content_recovery") + 1
    )


def test_task_operations_capability_is_not_available_before_0034(tmp_path) -> None:
    from sqlalchemy import create_engine, text

    from core import catalog_schema as api

    engine = create_engine(f"sqlite:///{(tmp_path / 'pre-0034.db').as_posix()}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
            )
            connection.execute(
                text(
                    "INSERT INTO alembic_version (version_num) VALUES ('0033_enterprise_content_recovery')"
                )
            )
        assert api.inspect_enterprise_task_operations_capability(engine) == ("not_available", ())
    finally:
        engine.dispose()


def _task_revision_engine(path, revisions: tuple[str, ...], tables=()):
    from sqlalchemy import create_engine, text

    engine = create_engine(f"sqlite:///{path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)"))
        for revision in revisions:
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                {"revision": revision},
            )
        for table in tables:
            connection.execute(text(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY)'))
    return engine


@pytest.mark.parametrize(
    ("revisions", "tables", "uses_frozen_original"),
    [
        (("0033_enterprise_content_recovery",), (), True),
        (
            ("0033_enterprise_content_recovery",),
            ("tenant_task_projections",),
            True,
        ),
        (("0034_enterprise_task_operations",), (), False),
        (
            ("0034_enterprise_task_operations",),
            ("tenant_task_projections",),
            False,
        ),
        (("0036_enterprise_knowledge_serving_reliability",), (), False),
        (
            ("0036_enterprise_knowledge_serving_reliability",),
            ("tenant_task_projections",),
            False,
        ),
        (("unknown_catalog_revision",), (), True),
        ((), (), True),
        (
            ("0033_enterprise_content_recovery", "0034_enterprise_task_operations"),
            (),
            True,
        ),
    ],
)
def test_task_operations_shared_policy_matches_frozen_compatibility_matrix(
    tmp_path,
    revisions: tuple[str, ...],
    tables: tuple[str, ...],
    uses_frozen_original: bool,
) -> None:
    from core import catalog_schema as api

    engine = _task_revision_engine(
        tmp_path / f"task-compat-{len(revisions)}-{len(tables)}.db",
        revisions,
        tables,
    )
    try:
        frozen_compatibility = api._knowledge_serving_revision_compatible(
            engine,
            api._KNOWLEDGE_SERVING_ORIGINAL_TASK_CAPABILITY,
            api.ENTERPRISE_TASK_OPERATIONS_TABLES,
            api.ENTERPRISE_TASK_OPERATIONS_REVISION,
            api._enterprise_task_operations_capability_issues,
        )
        active = api.inspect_enterprise_task_operations_capability(engine)
        assert active == frozen_compatibility
        assert api.inspect_catalog_capability("task_operations", engine) == active
        if uses_frozen_original:
            assert active == api._KNOWLEDGE_SERVING_ORIGINAL_TASK_CAPABILITY(engine)
    finally:
        engine.dispose()


def test_task_operations_active_and_frozen_exception_contracts_remain_distinct() -> None:
    from core import catalog_schema as api

    with pytest.raises(NoInspectionAvailable):
        api.inspect_enterprise_task_operations_capability(object())
    assert api._KNOWLEDGE_SERVING_ORIGINAL_TASK_CAPABILITY(object()) == (
        "unavailable",
        ("Task Operations schema inspection failed: NoInspectionAvailable",),
    )


def test_task_operations_shared_policy_matches_no_revision_table_compatibility(tmp_path) -> None:
    from sqlalchemy import create_engine

    from core import catalog_schema as api

    engine = create_engine(f"sqlite:///{(tmp_path / 'task-no-revision-table.db').as_posix()}")
    try:
        frozen_compatibility = api._knowledge_serving_revision_compatible(
            engine,
            api._KNOWLEDGE_SERVING_ORIGINAL_TASK_CAPABILITY,
            api.ENTERPRISE_TASK_OPERATIONS_TABLES,
            api.ENTERPRISE_TASK_OPERATIONS_REVISION,
            api._enterprise_task_operations_capability_issues,
        )
        active = api.inspect_enterprise_task_operations_capability(engine)
        assert active == frozen_compatibility
        assert active == api._KNOWLEDGE_SERVING_ORIGINAL_TASK_CAPABILITY(engine)
        assert active == api.inspect_catalog_capability("task_operations", engine)
    finally:
        engine.dispose()


def test_task_operations_legacy_fallback_keeps_second_connection_error_handling(
    tmp_path,
    monkeypatch,
) -> None:
    from core import catalog_schema as api

    def engine_with_failing_second_connect(name: str):
        engine = _task_revision_engine(
            tmp_path / name,
            ("0033_enterprise_content_recovery",),
        )
        original_connect = engine.connect
        calls = 0

        def connect():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("transient fallback connection failure")
            return original_connect()

        monkeypatch.setattr(engine, "connect", connect)
        return engine, lambda: calls

    compatibility_engine, compatibility_calls = engine_with_failing_second_connect(
        "task-fallback-compatibility.db"
    )
    active_engine, active_calls = engine_with_failing_second_connect("task-fallback-active.db")
    try:
        expected = api._knowledge_serving_revision_compatible(
            compatibility_engine,
            api._KNOWLEDGE_SERVING_ORIGINAL_TASK_CAPABILITY,
            api.ENTERPRISE_TASK_OPERATIONS_TABLES,
            api.ENTERPRISE_TASK_OPERATIONS_REVISION,
            api._enterprise_task_operations_capability_issues,
        )
        actual = api.inspect_enterprise_task_operations_capability(active_engine)
        assert (
            expected
            == actual
            == (
                "unavailable",
                ("Task Operations schema inspection failed: RuntimeError",),
            )
        )
        assert compatibility_calls() == active_calls() == 2
    finally:
        compatibility_engine.dispose()
        active_engine.dispose()


def test_task_operations_capability_is_ready_and_preserves_0033_parents(tmp_path) -> None:
    from sqlalchemy import create_engine

    from core import catalog_schema as api

    url = f"sqlite:///{(tmp_path / 'task-operations-readiness.db').as_posix()}"
    api.upgrade_catalog(url)
    engine = create_engine(url)
    try:
        assert api.inspect_enterprise_task_operations_capability(engine) == ("ready", ())
        assert api.inspect_enterprise_content_recovery_capability(engine) == ("ready", ())
        assert api.inspect_enterprise_notification_center_capability(engine) == ("ready", ())
        assert api.inspect_enterprise_release_quality_operations_capability(engine) == ("ready", ())
    finally:
        engine.dispose()


def _seed_task_projection(
    connection, *, source_current: bool = True, status: str = "failed"
) -> str:
    from core.enterprise_task_operations import canonical_task_projection

    timestamp = "2026-08-29T12:00:00.000000Z"
    source_digest = "a" * 64
    canonical = canonical_task_projection(
        {
            "tenant_id": "tenant-a",
            "task_id": "task-readiness",
            "source_kind": "source_sync",
            "source_id": "source-readiness",
            "source_revision": 1,
            "source_digest": source_digest,
            "dataset_id": None,
            "workspace_id": None,
            "category": "sources",
            "normalized_status": status,
            "action_required": True,
            "progress_percent": 25,
            "attempt_number": 1,
            "max_attempts": 3,
            "safe_error_code": "SOURCE_FAILED",
            "safe_error": "source failed safely",
            "source_current": source_current,
            "occurred_at": timestamp,
            "started_at": timestamp,
            "finished_at": timestamp,
            "updated_at": timestamp,
        }
    )
    connection.execute(
        __import__("sqlalchemy").text(
            "INSERT INTO tenant_task_projections "
            "(id,tenant_id,source_kind,source_id,source_revision,source_digest,category,"
            "normalized_status,action_required,progress_percent,attempt_number,max_attempts,"
            "safe_error_code,safe_error,target_route_code,target_route_params_json,source_current,"
            "projection_digest,occurred_at,started_at,finished_at,created_at,updated_at) VALUES "
            "('task-readiness','tenant-a','source_sync','source-readiness',1,:source_digest,'sources',"
            ":status,1,25,1,3,'SOURCE_FAILED','source failed safely','sources','{}',:current,"
            ":projection_digest,:timestamp,:timestamp,:timestamp,:timestamp,:timestamp)"
        ),
        {
            "source_digest": source_digest,
            "status": status,
            "current": source_current,
            "projection_digest": canonical["projection_digest"],
            "timestamp": timestamp,
        },
    )
    return canonical["projection_digest"]


def test_task_operations_readiness_rejects_stale_fences_and_unsafe_saved_view(tmp_path) -> None:
    from sqlalchemy import create_engine, text

    from core import catalog_schema as api

    url = f"sqlite:///{(tmp_path / 'task-operations-data-drift.db').as_posix()}"
    api.upgrade_catalog(url)
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            _seed_task_projection(connection, source_current=False, status="failed")
            connection.execute(
                text(
                    "INSERT INTO tenant_task_operator_actions "
                    "(id,tenant_id,task_id,action_type,status,expected_source_revision,"
                    "expected_source_digest,idempotency_key_digest,actor_id,request_id,safe_reason,"
                    "requested_at,expires_at,created_at,updated_at) VALUES "
                    "('action-readiness','tenant-a','task-readiness','retry','requested',2,:digest,"
                    ":digest,'owner-a','request-readiness','retry after review',CURRENT_TIMESTAMP,"
                    "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                ),
                {"digest": "b" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO tenant_task_saved_views "
                    "(id,tenant_id,account_id,name,normalized_name,status,active_view_key,revision,"
                    "filters_json,filter_digest,created_at,created_by,updated_at,updated_by) VALUES "
                    "('view-readiness','tenant-a','owner-a','Unsafe View','unsafe view','active',"
                    "'owner-a:unsafe view',1,:filters,:digest,CURRENT_TIMESTAMP,'owner-a',"
                    "CURRENT_TIMESTAMP,'owner-a')"
                ),
                {"filters": '{"query":"status=failed"}', "digest": "c" * 64},
            )
        state, issues = api.inspect_enterprise_task_operations_capability(engine)
        assert state == "unavailable"
        assert any(
            "stale Task Operations projection is not unavailable" in issue for issue in issues
        )
        assert any("stale Task Operations action source fence" in issue for issue in issues)
        assert any("saved view filters" in issue for issue in issues)
    finally:
        engine.dispose()


def test_task_operations_readiness_rejects_broken_event_chain_and_missing_guard(tmp_path) -> None:
    from sqlalchemy import create_engine, text

    from core import catalog_schema as api
    from core.enterprise_task_operations import canonical_task_event

    url = f"sqlite:///{(tmp_path / 'task-operations-event-drift.db').as_posix()}"
    api.upgrade_catalog(url)
    engine = create_engine(url)
    try:
        timestamp = "2026-08-29T12:00:00.000000Z"
        with engine.begin() as connection:
            _seed_task_projection(connection)
            connection.execute(text("DROP TRIGGER trg_tenant_task_events_validate_insert"))
            first = canonical_task_event(
                {
                    "tenant_id": "tenant-a",
                    "task_id": "task-readiness",
                    "sequence": 1,
                    "event_type": "materialized",
                    "previous_event_digest": None,
                    "actor_id": "system:task-reconciler",
                    "request_id": "request-event-1",
                    "safe_snapshot": {"source_revision": 1},
                    "occurred_at": timestamp,
                }
            )
            second = canonical_task_event(
                {
                    "tenant_id": "tenant-a",
                    "task_id": "task-readiness",
                    "sequence": 2,
                    "event_type": "status_changed",
                    "previous_event_digest": "d" * 64,
                    "actor_id": "system:task-reconciler",
                    "request_id": "request-event-2",
                    "safe_snapshot": {"source_revision": 1},
                    "occurred_at": timestamp,
                }
            )
            for event_id, event in (("event-1", first), ("event-2", second)):
                connection.execute(
                    text(
                        "INSERT INTO tenant_task_events "
                        "(id,tenant_id,task_id,sequence,event_type,previous_event_digest,event_digest,"
                        "actor_id,request_id,safe_snapshot_json,occurred_at) VALUES "
                        "(:id,'tenant-a','task-readiness',:sequence,:event_type,:previous,:digest,"
                        ":actor,:request,:snapshot,:occurred_at)"
                    ),
                    {
                        "id": event_id,
                        "sequence": event["sequence"],
                        "event_type": event["event_type"],
                        "previous": event["previous_event_digest"],
                        "digest": event["event_digest"],
                        "actor": event["actor_id"],
                        "request": event["request_id"],
                        "snapshot": '{"source_revision":1}',
                        "occurred_at": timestamp,
                    },
                )
        state, issues = api.inspect_enterprise_task_operations_capability(engine)
        assert state == "unavailable"
        assert any("missing Task Operations event insert trigger" in issue for issue in issues)
        assert any("broken Task Operations event hash chain" in issue for issue in issues)
    finally:
        engine.dispose()


def test_task_operations_readiness_accepts_service_materialized_canonical_evidence(
    tmp_path, monkeypatch
) -> None:
    from core import catalog_schema as api
    from core import enterprise_task_operations_service as service
    from test_enterprise_knowledge_base_release_snapshot import _release_engine
    from test_enterprise_task_operations_service import _source, install_registry

    engine, _ = _release_engine(tmp_path)
    install_registry(
        monkeypatch,
        {"source_sync": [_source("source_sync", "source-readiness-service")]},
    )
    service.reconcile_enterprise_tasks(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        source_kinds=["source_sync"],
        dry_run=False,
        reason="materialize controlled readiness evidence",
        idempotency_key="readiness-service-materialization",
        account_id="owner-a",
        request_id="request-readiness-service",
    )
    try:
        assert api.inspect_enterprise_task_operations_capability(engine) == ("ready", ())
    finally:
        engine.dispose()
