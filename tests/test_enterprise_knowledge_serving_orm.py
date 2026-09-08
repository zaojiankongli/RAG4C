from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint

import models.orm as orm

TABLES = {
    "tenant_knowledge_serving_profiles": "TenantKnowledgeServingProfile",
    "tenant_knowledge_serving_policy_revisions": "TenantKnowledgeServingPolicyRevision",
    "tenant_knowledge_serving_snapshots": "TenantKnowledgeServingSnapshot",
    "tenant_knowledge_serving_stage_facts": "TenantKnowledgeServingStageFact",
    "tenant_knowledge_serving_evidence_links": "TenantKnowledgeServingEvidenceLink",
    "tenant_knowledge_serving_events": "TenantKnowledgeServingEvent",
}


def _check(table_name: str, name: str) -> str:
    table = orm.Base.metadata.tables[table_name]
    constraint = next(
        item
        for item in table.constraints
        if isinstance(item, CheckConstraint) and item.name == name
    )
    return str(constraint.sqltext.compile(compile_kwargs={"literal_binds": True})).casefold()


def test_serving_orm_models_exist_with_constraints_and_tenant_leading_fks() -> None:
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


def test_serving_orm_has_exact_six_tables_and_canonical_identities() -> None:
    assert {
        name for name in orm.Base.metadata.tables if name.startswith("tenant_knowledge_serving_")
    } == set(TABLES)
    expected = {
        "tenant_knowledge_serving_profiles": {
            "uq_tenant_knowledge_serving_profiles_scope_id": ("tenant_id", "id"),
            "uq_tenant_knowledge_serving_profiles_dataset_id": ("tenant_id", "dataset_id"),
            "uq_tenant_knowledge_serving_profiles_active_key": ("tenant_id", "active_profile_key"),
        },
        "tenant_knowledge_serving_policy_revisions": {
            "uq_tenant_knowledge_serving_policy_revisions_scope_id": ("tenant_id", "id"),
            "uq_tenant_knowledge_serving_policy_revisions_profile_scope": (
                "tenant_id",
                "profile_id",
                "id",
            ),
            "uq_tenant_knowledge_serving_policy_revisions_profile_revision": (
                "tenant_id",
                "profile_id",
                "revision",
            ),
        },
        "tenant_knowledge_serving_snapshots": {
            "uq_tenant_knowledge_serving_snapshots_scope_id": ("tenant_id", "id"),
            "uq_tenant_knowledge_serving_snapshots_profile_scope": (
                "tenant_id",
                "profile_id",
                "id",
            ),
            "uq_tenant_knowledge_serving_snapshots_observation": (
                "tenant_id",
                "profile_id",
                "observation_key",
            ),
            "uq_tenant_knowledge_serving_snapshots_digest": ("tenant_id", "snapshot_digest"),
        },
        "tenant_knowledge_serving_stage_facts": {
            "uq_tenant_knowledge_serving_stage_facts_scope_id": ("tenant_id", "id"),
            "uq_tenant_knowledge_serving_stage_facts_profile_snapshot_id": (
                "tenant_id",
                "profile_id",
                "snapshot_id",
                "id",
            ),
            "uq_tenant_knowledge_serving_stage_facts_stage": (
                "tenant_id",
                "snapshot_id",
                "stage_code",
            ),
            "uq_tenant_knowledge_serving_stage_facts_sequence": (
                "tenant_id",
                "snapshot_id",
                "sequence",
            ),
        },
        "tenant_knowledge_serving_evidence_links": {
            "uq_tenant_knowledge_serving_evidence_links_scope_id": ("tenant_id", "id"),
            "uq_tenant_knowledge_serving_evidence_links_digest": ("tenant_id", "evidence_digest"),
        },
        "tenant_knowledge_serving_events": {
            "uq_tenant_knowledge_serving_events_scope_id": ("tenant_id", "id"),
            "uq_tenant_knowledge_serving_events_stream_sequence": (
                "tenant_id",
                "stream_key",
                "sequence",
            ),
            "uq_tenant_knowledge_serving_events_digest": ("tenant_id", "event_digest"),
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


def test_serving_orm_is_body_free_and_has_exact_allowlists() -> None:
    forbidden = {
        "sql",
        "query",
        "url",
        "uri",
        "webhook",
        "payload",
        "raw",
        "body",
        "content",
        "answer",
        "prompt",
        "token",
        "credential",
        "ticket",
        "password",
    }
    for table_name in TABLES:
        assert forbidden.isdisjoint(orm.Base.metadata.tables[table_name].c.keys()), table_name
    profile_status = _check(
        "tenant_knowledge_serving_profiles", "ck_tenant_knowledge_serving_profiles_status"
    )
    assert all(value in profile_status for value in ("draft", "active", "paused", "archived"))
    stages = _check(
        "tenant_knowledge_serving_stage_facts", "ck_tenant_knowledge_serving_stage_facts_stage"
    )
    assert all(value in stages for value in ("source", "parse", "chunk", "index", "serve"))
    states = _check(
        "tenant_knowledge_serving_stage_facts", "ck_tenant_knowledge_serving_stage_facts_state"
    )
    assert all(
        value in states for value in ("ready", "lagging", "blocked", "missing", "unavailable")
    )
    evidence = _check(
        "tenant_knowledge_serving_evidence_links", "ck_tenant_knowledge_serving_evidence_links_kind"
    )
    assert all(
        value in evidence
        for value in (
            "source",
            "source_sync_run",
            "document",
            "ingest_attempt",
            "chunk_head",
            "index_operation",
            "release",
            "certification",
            "task",
        )
    )
    events = _check("tenant_knowledge_serving_events", "ck_tenant_knowledge_serving_events_type")
    assert all(
        value in events for value in ("profile_created", "snapshot_recorded", "service_recovered")
    )
    chain = _check(
        "tenant_knowledge_serving_events", "ck_tenant_knowledge_serving_events_hash_chain"
    )
    assert "sequence" in chain and "previous_event_digest" in chain



def test_serving_orm_has_profile_owned_pointer_foreign_keys() -> None:
    expected = {
        ("tenant_knowledge_serving_profiles", "fk_tenant_knowledge_serving_profiles_current_policy"): (
            ("tenant_id", "id", "current_policy_revision_id"),
            ("tenant_knowledge_serving_policy_revisions", "tenant_id", "profile_id", "id"),
        ),
        ("tenant_knowledge_serving_profiles", "fk_tenant_knowledge_serving_profiles_current_snapshot"): (
            ("tenant_id", "id", "current_snapshot_id"),
            ("tenant_knowledge_serving_snapshots", "tenant_id", "profile_id", "id"),
        ),
        ("tenant_knowledge_serving_snapshots", "fk_tenant_knowledge_serving_snapshots_policy"): (
            ("tenant_id", "profile_id", "policy_revision_id"),
            ("tenant_knowledge_serving_policy_revisions", "tenant_id", "profile_id", "id"),
        ),
        ("tenant_knowledge_serving_events", "fk_tenant_knowledge_serving_events_snapshot"): (
            ("tenant_id", "profile_id", "snapshot_id"),
            ("tenant_knowledge_serving_snapshots", "tenant_id", "profile_id", "id"),
        ),
        (
            "tenant_knowledge_serving_stage_facts",
            "fk_tenant_knowledge_serving_stage_facts_snapshot",
        ): (
            ("tenant_id", "profile_id", "snapshot_id"),
            ("tenant_knowledge_serving_snapshots", "tenant_id", "profile_id", "id"),
        ),
        (
            "tenant_knowledge_serving_evidence_links",
            "fk_tenant_knowledge_serving_evidence_links_snapshot",
        ): (
            ("tenant_id", "profile_id", "snapshot_id"),
            ("tenant_knowledge_serving_snapshots", "tenant_id", "profile_id", "id"),
        ),
        (
            "tenant_knowledge_serving_evidence_links",
            "fk_tenant_knowledge_serving_evidence_links_stage_fact",
        ): (
            ("tenant_id", "profile_id", "snapshot_id", "stage_fact_id"),
            (
                "tenant_knowledge_serving_stage_facts",
                "tenant_id",
                "profile_id",
                "snapshot_id",
                "id",
            ),
        ),
    }
    for (table_name, name), (local, remote) in expected.items():
        table = orm.Base.metadata.tables[table_name]
        fk = next(item for item in table.foreign_key_constraints if item.name == name)
        assert tuple(fk.column_keys) == local
        assert tuple(element.column.table.name for element in fk.elements) == (remote[0],) * len(fk.elements)
        assert tuple(element.column.name for element in fk.elements) == remote[1:]


def test_serving_orm_uses_positive_resource_revision_and_exact_kind_route_check() -> None:
    evidence = orm.Base.metadata.tables["tenant_knowledge_serving_evidence_links"]
    resource_id = evidence.c.resource_id
    assert resource_id.type.length == 128

    checks = {
        str(item.name): str(item.sqltext).casefold()
        for item in evidence.constraints
        if isinstance(item, CheckConstraint) and item.name
    }
    assert "resource_revision >= 1" in checks[
        "ck_tenant_knowledge_serving_evidence_links_digest"
    ]
    kind_route = checks["ck_tenant_knowledge_serving_evidence_links_kind_route"]
    for evidence_kind, route_code in (
        ("source", "knowledge_sources"),
        ("source_sync_run", "knowledge_sources"),
        ("document", "knowledge_documents"),
        ("ingest_attempt", "knowledge_documents"),
        ("chunk_head", "knowledge_documents"),
        ("index_operation", "knowledge_indexing"),
        ("release", "knowledge_base_releases"),
        ("certification", "release_quality"),
        ("task", "enterprise_tasks"),
    ):
        assert f"(evidence_kind='{evidence_kind}' and route_code='{route_code}')" in kind_route

