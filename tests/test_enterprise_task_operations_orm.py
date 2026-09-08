from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint

import models.orm as orm

TABLES = {
    "tenant_task_projections": "TenantTaskProjection",
    "tenant_task_operator_actions": "TenantTaskOperatorAction",
    "tenant_task_events": "TenantTaskEvent",
    "tenant_task_saved_views": "TenantTaskSavedView",
    "tenant_task_reconciliation_runs": "TenantTaskReconciliationRun",
}


def _check(table_name: str, name: str) -> str:
    table = orm.Base.metadata.tables[table_name]
    constraint = next(
        item
        for item in table.constraints
        if isinstance(item, CheckConstraint) and item.name == name
    )
    return str(constraint.sqltext.compile(compile_kwargs={"literal_binds": True})).casefold()


def test_task_operations_orm_models_exist_with_enterprise_constraints() -> None:
    for table_name, class_name in TABLES.items():
        model = getattr(orm, class_name, None)
        assert model is not None, class_name
        table = orm.Base.metadata.tables[table_name]
        assert table is model.__table__
        assert any(isinstance(item, UniqueConstraint) for item in table.constraints)
        assert any(isinstance(item, CheckConstraint) for item in table.constraints)
        assert any(isinstance(item, Index) for item in table.indexes)
        for fk in (item for item in table.constraints if isinstance(item, ForeignKeyConstraint)):
            assert tuple(fk.column_keys)[0] == "tenant_id", (table_name, fk.name)


def test_task_operations_orm_declares_exact_five_tables_and_canonical_identities() -> None:
    task_tables = {name for name in orm.Base.metadata.tables if name.startswith("tenant_task_")}
    assert task_tables == set(TABLES)

    expected = {
        "tenant_task_projections": {
            "uq_tenant_task_projections_scope_id": ("tenant_id", "id"),
            "uq_tenant_task_projections_source": (
                "tenant_id",
                "source_kind",
                "source_id",
            ),
        },
        "tenant_task_operator_actions": {
            "uq_tenant_task_operator_actions_scope_id": ("tenant_id", "id"),
            "uq_tenant_task_operator_actions_idempotency": (
                "tenant_id",
                "actor_id",
                "idempotency_key_digest",
            ),
        },
        "tenant_task_events": {
            "uq_tenant_task_events_scope_id": ("tenant_id", "id"),
            "uq_tenant_task_events_stream_sequence": (
                "tenant_id",
                "task_id",
                "sequence",
            ),
        },
        "tenant_task_saved_views": {
            "uq_tenant_task_saved_views_scope_id": ("tenant_id", "id"),
            "uq_tenant_task_saved_views_active_key": ("tenant_id", "active_view_key"),
        },
        "tenant_task_reconciliation_runs": {
            "uq_tenant_task_reconciliation_runs_scope_id": ("tenant_id", "id"),
        },
    }
    for table_name, contracts in expected.items():
        table = orm.Base.metadata.tables[table_name]
        actual = {
            str(item.name): tuple(item.columns.keys())
            for item in table.constraints
            if isinstance(item, UniqueConstraint) and item.name
        }
        for name, columns in contracts.items():
            assert actual.get(name) == columns, (table_name, name, actual)


def test_task_operations_orm_is_body_free_and_has_strict_allowlists() -> None:
    forbidden = {
        "query",
        "result_body",
        "raw_payload",
        "raw_content",
        "note",
        "comment",
        "ticket",
        "execution_ticket",
        "token",
        "credential",
        "password",
        "url",
        "uri",
    }
    for table_name in TABLES:
        assert forbidden.isdisjoint(orm.Base.metadata.tables[table_name].c.keys()), table_name

    source_kind = _check("tenant_task_projections", "ck_tenant_task_projections_source_kind")
    assert "document_ingest" in source_kind
    assert "release_recertification" in source_kind
    category = _check("tenant_task_projections", "ck_tenant_task_projections_category")
    assert "documents" in category and "indexing" in category and "sources" in category
    status = _check("tenant_task_projections", "ck_tenant_task_projections_status")
    assert "queued" in status and "unavailable" in status
    route = _check("tenant_task_projections", "ck_tenant_task_projections_route")
    assert "target_route_code" in route and "documents" in route
    digest = _check("tenant_task_projections", "ck_tenant_task_projections_digest")
    assert "projection_digest" in digest and "source_digest" in digest

    action_type = _check("tenant_task_operator_actions", "ck_tenant_task_operator_actions_type")
    assert "retry" in action_type and "cancel" in action_type and "acknowledge" in action_type
    action_status = _check("tenant_task_operator_actions", "ck_tenant_task_operator_actions_status")
    assert "requested" in action_status and "expired" in action_status

    lifecycle = _check("tenant_task_saved_views", "ck_tenant_task_saved_views_lifecycle")
    assert "active_view_key" in lifecycle and "archived_at" in lifecycle
    filters = _check("tenant_task_saved_views", "ck_tenant_task_saved_views_filters")
    assert "filters_json" in filters and "filter_digest" in filters

    event_type = _check("tenant_task_events", "ck_tenant_task_events_type")
    assert "materialized" in event_type and "attention_acknowledged" in event_type
    chain = _check("tenant_task_events", "ck_tenant_task_events_hash_chain")
    assert "sequence" in chain and "previous_event_digest" in chain

    reconciliation_scope = _check(
        "tenant_task_reconciliation_runs", "ck_tenant_task_reconciliation_runs_source_kinds"
    )
    assert "source_kinds_json" in reconciliation_scope
    counts = _check("tenant_task_reconciliation_runs", "ck_tenant_task_reconciliation_runs_counts")
    assert "invalid_count" in counts and "source_count" in counts
