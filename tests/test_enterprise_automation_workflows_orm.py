from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint

from models import orm

TABLES = {
    "tenant_automation_rules": "TenantAutomationRule",
    "tenant_automation_rule_revisions": "TenantAutomationRuleRevision",
    "tenant_automation_source_cursors": "TenantAutomationSourceCursor",
    "tenant_automation_runs": "TenantAutomationRun",
    "tenant_automation_action_requests": "TenantAutomationActionRequest",
    "tenant_automation_events": "TenantAutomationEvent",
}


def _check(table_name: str, name: str) -> str:
    table = orm.Base.metadata.tables[table_name]
    constraint = next(
        item
        for item in table.constraints
        if isinstance(item, CheckConstraint) and item.name == name
    )
    return str(constraint.sqltext).casefold()


def test_automation_orm_declares_exact_six_tables_and_enterprise_constraints() -> None:
    actual = {name for name in orm.Base.metadata.tables if name.startswith("tenant_automation_")}
    assert actual == set(TABLES)
    for table_name, class_name in TABLES.items():
        model = getattr(orm, class_name)
        assert model.__table__ is orm.Base.metadata.tables[table_name]
        table = model.__table__
        assert any(isinstance(item, UniqueConstraint) for item in table.constraints)
        assert any(isinstance(item, CheckConstraint) for item in table.constraints)
        assert any(isinstance(item, Index) for item in table.indexes)
        for fk in (item for item in table.constraints if isinstance(item, ForeignKeyConstraint)):
            assert tuple(fk.column_keys)[0] == "tenant_id", (table_name, fk.name)


def test_automation_orm_is_body_free_and_strictly_allowlisted() -> None:
    forbidden = {
        "sql",
        "query",
        "url",
        "webhook",
        "code",
        "script",
        "token",
        "credential",
        "ticket",
        "raw_payload",
        "body",
        "prompt",
    }
    for table_name in TABLES:
        assert forbidden.isdisjoint(orm.Base.metadata.tables[table_name].c.keys())
    assert "task_failed" in _check(
        "tenant_automation_rule_revisions", "ck_tenant_automation_rule_revisions_trigger"
    )
    assert "notify_operator" in _check(
        "tenant_automation_action_requests", "ck_tenant_automation_action_requests_action"
    )
    assert "run_completed" in _check("tenant_automation_events", "ck_tenant_automation_events_type")



def test_automation_rule_current_revision_fk_is_tenant_leading() -> None:
    table = orm.Base.metadata.tables["tenant_automation_rules"]
    foreign_keys = {
        constraint.name: (
            tuple(constraint.column_keys),
            tuple(element.column.name for element in constraint.elements),
        )
        for constraint in table.foreign_key_constraints
    }
    assert foreign_keys["fk_tenant_automation_rules_current_revision"] == (
        ("tenant_id", "current_revision_id"),
        ("tenant_id", "id"),
    )
