from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint

import models.orm as orm

TABLES = {
    "tenant_content_retention_policies": "TenantContentRetentionPolicy",
    "tenant_document_recycle_entries": "TenantDocumentRecycleEntry",
    "tenant_document_legal_holds": "TenantDocumentLegalHold",
    "tenant_document_purge_requests": "TenantDocumentPurgeRequest",
    "tenant_document_recovery_events": "TenantDocumentRecoveryEvent",
}


def _check(table_name: str, name: str) -> str:
    table = orm.Base.metadata.tables[table_name]
    constraint = next(
        item
        for item in table.constraints
        if isinstance(item, CheckConstraint) and item.name == name
    )
    return str(constraint.sqltext.compile(compile_kwargs={"literal_binds": True})).casefold()


def test_content_recovery_orm_models_exist_with_enterprise_constraints() -> None:
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


def test_content_recovery_orm_declares_exact_five_tables_and_canonical_keys() -> None:
    assert set(TABLES) <= set(orm.Base.metadata.tables)
    policy = orm.Base.metadata.tables["tenant_content_retention_policies"]
    policy_uniques = {
        tuple(item.columns.keys())
        for item in policy.constraints
        if isinstance(item, UniqueConstraint)
    }
    assert ("tenant_id",) in policy_uniques

    recycle = orm.Base.metadata.tables["tenant_document_recycle_entries"]
    recycle_uniques = {
        tuple(item.columns.keys())
        for item in recycle.constraints
        if isinstance(item, UniqueConstraint)
    }
    assert ("tenant_id", "active_recycle_key") in recycle_uniques
    assert ("tenant_id", "dataset_id", "document_id", "recycle_generation") in recycle_uniques

    assert "dataset_id" in _check(
        "tenant_document_recycle_entries", "ck_tenant_document_recycle_entries_active_key"
    )
    assert "document_id" in _check(
        "tenant_document_recycle_entries", "ck_tenant_document_recycle_entries_active_key"
    )
    assert "reason_code" in _check(
        "tenant_document_legal_holds", "ck_tenant_document_legal_holds_active_key"
    )


def test_content_recovery_orm_is_body_free_and_declares_document_purge_action() -> None:
    forbidden = {
        "body",
        "content",
        "raw_metadata",
        "metadata",
        "query",
        "note",
        "token",
        "credential",
        "external_url",
        "execution_ticket",
    }
    for table_name in TABLES:
        assert forbidden.isdisjoint(orm.Base.metadata.tables[table_name].c.keys())

    lifecycle = _check("documents", "ck_documents_lifecycle_state")
    assert "recycled" in lifecycle

    for table_name in ("tenant_approval_policies", "tenant_approval_requests"):
        assert "document_purge" in _check(table_name, f"ck_{table_name}_action_type")
