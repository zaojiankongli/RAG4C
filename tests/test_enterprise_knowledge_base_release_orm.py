from __future__ import annotations

from collections.abc import Iterable

import pytest
from sqlalchemy import BigInteger, Boolean, DateTime, Integer, JSON, String, create_engine, event
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.orm import configure_mappers

import models.orm as orm


RELEASE_TABLES = {
    "tenant_release_channels": "TenantReleaseChannel",
    "dataset_release_manifests": "DatasetReleaseManifest",
    "dataset_release_entries": "DatasetReleaseEntry",
    "dataset_release_events": "DatasetReleaseEvent",
    "dataset_channel_releases": "DatasetChannelRelease",
}

EXPECTED_COLUMNS = {
    "tenant_release_channels": (
        "id",
        "tenant_id",
        "code",
        "normalized_code",
        "name",
        "status",
        "risk_tier",
        "promotion_order",
        "is_default_serving",
        "active_default_slot",
        "revision",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "archived_at",
        "archived_by",
    ),
    "dataset_release_manifests": (
        "id",
        "tenant_id",
        "dataset_id",
        "release_number",
        "profile_revision",
        "ownership_revision",
        "workspace_id",
        "workspace_revision",
        "mutation_generation",
        "serving_generation",
        "schema_version",
        "policy_digest",
        "manifest_digest",
        "readiness_digest",
        "readiness_state",
        "entry_count",
        "blocker_count",
        "readiness_blockers_json",
        "created_at",
        "created_by",
        "reason",
        "request_id",
    ),
    "dataset_release_entries": (
        "id",
        "tenant_id",
        "dataset_id",
        "release_id",
        "ordinal",
        "resource_type",
        "resource_id",
        "resource_revision",
        "content_digest",
        "safe_facts_json",
        "created_at",
    ),
    "dataset_release_events": (
        "id",
        "tenant_id",
        "dataset_id",
        "release_id",
        "channel_id",
        "event_type",
        "actor_id",
        "reason",
        "request_id",
        "occurred_at",
        "previous_binding_revision",
        "current_binding_revision",
    ),
    "dataset_channel_releases": (
        "id",
        "tenant_id",
        "dataset_id",
        "channel_id",
        "active_release_id",
        "previous_release_id",
        "status",
        "active_slot",
        "revision",
        "activated_at",
        "activated_by",
        "request_id",
        "reason",
        "created_at",
        "updated_at",
        "updated_by",
    ),
}

EXPECTED_TYPES = {
    "tenant_release_channels": {
        "id": (String, 128),
        "tenant_id": (String, 64),
        "code": (String, 64),
        "normalized_code": (String, 64),
        "name": (String, 128),
        "status": (String, 16),
        "risk_tier": (String, 16),
        "promotion_order": (Integer, None),
        "is_default_serving": (Boolean, None),
        "active_default_slot": (String, 16),
        "revision": (Integer, None),
        "created_at": (DateTime, None),
        "created_by": (String, 64),
        "updated_at": (DateTime, None),
        "updated_by": (String, 64),
        "archived_at": (DateTime, None),
        "archived_by": (String, 64),
    },
    "dataset_release_manifests": {
        "id": (String, 64),
        "tenant_id": (String, 64),
        "dataset_id": (String, 64),
        "release_number": (Integer, None),
        "profile_revision": (Integer, None),
        "ownership_revision": (Integer, None),
        "workspace_id": (String, 128),
        "workspace_revision": (Integer, None),
        "mutation_generation": (BigInteger, None),
        "serving_generation": (BigInteger, None),
        "schema_version": (Integer, None),
        "policy_digest": (String, 64),
        "manifest_digest": (String, 64),
        "readiness_digest": (String, 64),
        "readiness_state": (String, 16),
        "entry_count": (Integer, None),
        "blocker_count": (Integer, None),
        "readiness_blockers_json": (JSON, None),
        "created_at": (DateTime, None),
        "created_by": (String, 64),
        "reason": (String, 512),
        "request_id": (String, 128),
    },
    "dataset_release_entries": {
        "id": (String, 64),
        "tenant_id": (String, 64),
        "dataset_id": (String, 64),
        "release_id": (String, 64),
        "ordinal": (Integer, None),
        "resource_type": (String, 32),
        "resource_id": (String, 128),
        "resource_revision": (BigInteger, None),
        "content_digest": (String, 64),
        "safe_facts_json": (JSON, None),
        "created_at": (DateTime, None),
    },
    "dataset_release_events": {
        "id": (String, 64),
        "tenant_id": (String, 64),
        "dataset_id": (String, 64),
        "release_id": (String, 64),
        "channel_id": (String, 128),
        "event_type": (String, 24),
        "actor_id": (String, 64),
        "reason": (String, 512),
        "request_id": (String, 128),
        "occurred_at": (DateTime, None),
        "previous_binding_revision": (Integer, None),
        "current_binding_revision": (Integer, None),
    },
    "dataset_channel_releases": {
        "id": (String, 64),
        "tenant_id": (String, 64),
        "dataset_id": (String, 64),
        "channel_id": (String, 128),
        "active_release_id": (String, 64),
        "previous_release_id": (String, 64),
        "status": (String, 16),
        "active_slot": (String, 16),
        "revision": (Integer, None),
        "activated_at": (DateTime, None),
        "activated_by": (String, 64),
        "request_id": (String, 128),
        "reason": (String, 512),
        "created_at": (DateTime, None),
        "updated_at": (DateTime, None),
        "updated_by": (String, 64),
    },
}

EXPECTED_UNIQUE_CONSTRAINTS = {
    "tenant_release_channels": {
        "uq_tenant_release_channels_scope_id": ("tenant_id", "id"),
        "uq_tenant_release_channels_tenant_code": ("tenant_id", "code"),
        "uq_tenant_release_channels_tenant_normalized_code": (
            "tenant_id",
            "normalized_code",
        ),
        "uq_tenant_release_channels_active_default": ("tenant_id", "active_default_slot"),
    },
    "dataset_release_manifests": {
        "uq_dataset_release_manifests_scope_id": ("tenant_id", "id"),
        "uq_dataset_release_manifests_scope_dataset_id": ("tenant_id", "dataset_id", "id"),
        "uq_dataset_release_manifests_dataset_number": (
            "tenant_id",
            "dataset_id",
            "release_number",
        ),
        "uq_dataset_release_manifests_dataset_digest": (
            "tenant_id",
            "dataset_id",
            "manifest_digest",
        ),
    },
    "dataset_release_entries": {
        "uq_dataset_release_entries_scope_id": ("tenant_id", "id"),
        "uq_dataset_release_entries_release_ordinal": (
            "tenant_id",
            "dataset_id",
            "release_id",
            "ordinal",
        ),
        "uq_dataset_release_entries_resource": (
            "tenant_id",
            "dataset_id",
            "release_id",
            "resource_type",
            "resource_id",
        ),
    },
    "dataset_release_events": {
        "uq_dataset_release_events_scope_id": ("tenant_id", "id"),
    },
    "dataset_channel_releases": {
        "uq_dataset_channel_releases_scope_id": ("tenant_id", "id"),
        "uq_dataset_channel_releases_scope_channel": (
            "tenant_id",
            "dataset_id",
            "channel_id",
        ),
        "uq_dataset_channel_releases_active_slot": (
            "tenant_id",
            "dataset_id",
            "channel_id",
            "active_slot",
        ),
    },
}

EXPECTED_CHECKS = {
    "tenant_release_channels": {
        "ck_tenant_release_channels_status": "status IN ('active','archived')",
        "ck_tenant_release_channels_risk_tier": "risk_tier IN ('low','medium','high')",
        "ck_tenant_release_channels_code_length": "length(code) BETWEEN 1 AND 64",
        "ck_tenant_release_channels_promotion_order_nonnegative": "promotion_order >= 0",
        "ck_tenant_release_channels_revision_positive": "revision > 0",
        "ck_tenant_release_channels_lifecycle_evidence": (
            "(status = 'active' AND archived_at IS NULL AND archived_by IS NULL) OR "
            "(status = 'archived' AND archived_at IS NOT NULL AND archived_by IS NOT NULL)"
        ),
        "ck_tenant_release_channels_active_default_slot": (
            "(is_default_serving = false AND active_default_slot IS NULL) OR "
            "(is_default_serving = true AND status = 'active' AND active_default_slot = 'default')"
        ),
    },
    "dataset_release_manifests": {
        "ck_dataset_release_manifests_release_number_positive": "release_number > 0",
        "ck_dataset_release_manifests_revisions_positive": (
            "profile_revision > 0 AND ownership_revision > 0 AND workspace_revision > 0"
        ),
        "ck_dataset_release_manifests_generations_nonnegative": (
            "mutation_generation >= 0 AND serving_generation >= 0"
        ),
        "ck_dataset_release_manifests_schema_version_positive": "schema_version > 0",
        "ck_dataset_release_manifests_policy_digest": (
            "length(policy_digest) = 64 AND lower(policy_digest) = policy_digest"
        ),
        "ck_dataset_release_manifests_manifest_digest": (
            "length(manifest_digest) = 64 AND lower(manifest_digest) = manifest_digest"
        ),
        "ck_dataset_release_manifests_readiness_digest": (
            "length(readiness_digest) = 64 AND lower(readiness_digest) = readiness_digest"
        ),
        "ck_dataset_release_manifests_readiness_state": (
            "readiness_state IN ('ready','blocked','unavailable')"
        ),
        "ck_dataset_release_manifests_counts_nonnegative": (
            "entry_count >= 0 AND blocker_count >= 0"
        ),
    },
    "dataset_release_entries": {
        "ck_dataset_release_entries_ordinal_nonnegative": "ordinal >= 0",
        "ck_dataset_release_entries_resource_type": (
            "resource_type IN ('dataset_profile','document_version','qa_revision','source_generation','projection_revision')"
        ),
        "ck_dataset_release_entries_resource_revision_nonnegative": "resource_revision >= 0",
        "ck_dataset_release_entries_content_digest": (
            "content_digest IS NULL OR (length(content_digest) = 64 AND lower(content_digest) = content_digest)"
        ),
    },
    "dataset_release_events": {
        "ck_dataset_release_events_event_type": (
            "event_type IN ('candidate_created','promoted','superseded','rolled_back','retired')"
        ),
        "ck_dataset_release_events_reason_length": "length(reason) BETWEEN 1 AND 512",
        "ck_dataset_release_events_previous_revision_nonnegative": (
            "previous_binding_revision IS NULL OR previous_binding_revision >= 0"
        ),
        "ck_dataset_release_events_current_revision_nonnegative": (
            "current_binding_revision IS NULL OR current_binding_revision >= 0"
        ),
    },
    "dataset_channel_releases": {
        "ck_dataset_channel_releases_status": "status IN ('active','removed')",
        "ck_dataset_channel_releases_revision_positive": "revision > 0",
        "ck_dataset_channel_releases_active_slot": (
            "(status = 'active' AND active_slot = 'active' AND active_release_id IS NOT NULL "
            "AND activated_at IS NOT NULL AND activated_by IS NOT NULL) OR "
            "(status = 'removed' AND active_slot IS NULL AND active_release_id IS NULL)"
        ),
    },
    "datasets": {
        "ck_datasets_release_revision_positive": "release_revision > 0",
    },
    "app_dataset_references": {
        "ck_app_dataset_references_release_binding": (
            "(release_mode = 'follow_channel' AND release_channel_id IS NOT NULL AND pinned_release_id IS NULL) OR "
            "(release_mode = 'pinned' AND release_channel_id IS NULL AND pinned_release_id IS NOT NULL)"
        ),
    },
}

EXPECTED_INDEXES = {
    "tenant_release_channels": {
        "ix_tenant_release_channels_tenant_status_order": (
            "tenant_id",
            "status",
            "promotion_order",
            "id",
        ),
        "ix_tenant_release_channels_tenant_default": (
            "tenant_id",
            "status",
            "active_default_slot",
            "id",
        ),
    },
    "dataset_release_manifests": {
        "ix_dataset_release_manifests_tenant_dataset_number": (
            "tenant_id",
            "dataset_id",
            "release_number",
            "id",
        ),
        "ix_dataset_release_manifests_tenant_dataset_created": (
            "tenant_id",
            "dataset_id",
            "created_at",
            "id",
        ),
        "ix_dataset_release_manifests_tenant_readiness": (
            "tenant_id",
            "readiness_state",
            "created_at",
            "id",
        ),
    },
    "dataset_release_entries": {
        "ix_dataset_release_entries_tenant_release_ordinal": (
            "tenant_id",
            "dataset_id",
            "release_id",
            "ordinal",
            "id",
        ),
        "ix_dataset_release_entries_tenant_resource": (
            "tenant_id",
            "dataset_id",
            "resource_type",
            "resource_id",
            "id",
        ),
    },
    "dataset_release_events": {
        "ix_dataset_release_events_tenant_release_time": (
            "tenant_id",
            "dataset_id",
            "release_id",
            "occurred_at",
            "id",
        ),
        "ix_dataset_release_events_tenant_channel_time": (
            "tenant_id",
            "channel_id",
            "occurred_at",
            "id",
        ),
    },
    "dataset_channel_releases": {
        "ix_dataset_channel_releases_tenant_dataset_status": (
            "tenant_id",
            "dataset_id",
            "status",
            "revision",
            "id",
        ),
        "ix_dataset_channel_releases_tenant_channel_status": (
            "tenant_id",
            "channel_id",
            "status",
            "id",
        ),
    },
}

EXPECTED_FOREIGN_KEYS = {
    "tenant_release_channels": {
        "fk_tenant_release_channels_tenant": (
            ("tenant_id",),
            "tenants",
            ("id",),
        ),
    },
    "dataset_release_manifests": {
        "fk_dataset_release_manifests_tenant": (
            ("tenant_id",),
            "tenants",
            ("id",),
        ),
        "fk_dataset_release_manifests_scope_dataset": (
            ("tenant_id", "dataset_id"),
            "datasets",
            ("tenant_id", "id"),
        ),
        "fk_dataset_release_manifests_scope_workspace": (
            ("tenant_id", "workspace_id"),
            "tenant_workspaces",
            ("tenant_id", "id"),
        ),
    },
    "dataset_release_entries": {
        "fk_dataset_release_entries_tenant": (
            ("tenant_id",),
            "tenants",
            ("id",),
        ),
        "fk_dataset_release_entries_scope_dataset": (
            ("tenant_id", "dataset_id"),
            "datasets",
            ("tenant_id", "id"),
        ),
        "fk_dataset_release_entries_scope_release": (
            ("tenant_id", "dataset_id", "release_id"),
            "dataset_release_manifests",
            ("tenant_id", "dataset_id", "id"),
        ),
    },
    "dataset_release_events": {
        "fk_dataset_release_events_tenant": (
            ("tenant_id",),
            "tenants",
            ("id",),
        ),
        "fk_dataset_release_events_scope_dataset": (
            ("tenant_id", "dataset_id"),
            "datasets",
            ("tenant_id", "id"),
        ),
        "fk_dataset_release_events_scope_release": (
            ("tenant_id", "dataset_id", "release_id"),
            "dataset_release_manifests",
            ("tenant_id", "dataset_id", "id"),
        ),
        "fk_dataset_release_events_scope_channel": (
            ("tenant_id", "channel_id"),
            "tenant_release_channels",
            ("tenant_id", "id"),
        ),
    },
    "dataset_channel_releases": {
        "fk_dataset_channel_releases_tenant": (
            ("tenant_id",),
            "tenants",
            ("id",),
        ),
        "fk_dataset_channel_releases_scope_dataset": (
            ("tenant_id", "dataset_id"),
            "datasets",
            ("tenant_id", "id"),
        ),
        "fk_dataset_channel_releases_scope_channel": (
            ("tenant_id", "channel_id"),
            "tenant_release_channels",
            ("tenant_id", "id"),
        ),
        "fk_dataset_channel_releases_scope_active_release": (
            ("tenant_id", "dataset_id", "active_release_id"),
            "dataset_release_manifests",
            ("tenant_id", "dataset_id", "id"),
        ),
        "fk_dataset_channel_releases_scope_previous_release": (
            ("tenant_id", "dataset_id", "previous_release_id"),
            "dataset_release_manifests",
            ("tenant_id", "dataset_id", "id"),
        ),
    },
    "datasets": {
        "fk_datasets_scope_serving_release": (
            ("tenant_id", "serving_release_id"),
            "dataset_release_manifests",
            ("tenant_id", "id"),
        ),
    },
    "app_dataset_references": {
        "fk_app_dataset_references_scope_release_channel": (
            ("tenant_id", "release_channel_id"),
            "tenant_release_channels",
            ("tenant_id", "id"),
        ),
        "fk_app_dataset_references_scope_pinned_release": (
            ("tenant_id", "dataset_id", "pinned_release_id"),
            "dataset_release_manifests",
            ("tenant_id", "dataset_id", "id"),
        ),
    },
}


def _normalise_sql(value: object) -> str:
    return " ".join(str(value).casefold().split())


def _named_constraints(table, constraint_type: type) -> dict[str, object]:
    return {
        str(constraint.name): constraint
        for constraint in table.constraints
        if isinstance(constraint, constraint_type) and constraint.name
    }


def _named_foreign_keys(table) -> dict[str, object]:
    return {
        str(constraint.name): constraint
        for constraint in table.foreign_key_constraints
        if constraint.name
    }


def _column_keys(columns: Iterable[object]) -> tuple[str, ...]:
    return tuple(str(column.name) for column in columns)


def _server_default(column) -> str | None:
    if column.server_default is None:
        return None
    return str(column.server_default.arg).strip()


def test_release_orm_registers_exact_0029_models_and_columns() -> None:
    metadata = orm.Base.metadata

    for table_name, class_name in RELEASE_TABLES.items():
        model = getattr(orm, class_name, None)
        assert model is not None, f"models.orm.{class_name} is missing"
        assert model.__tablename__ == table_name
        table = metadata.tables.get(table_name)
        assert table is not None, f"ORM table {table_name} is missing"
        assert tuple(table.columns.keys()) == EXPECTED_COLUMNS[table_name]

        for column_name, (expected_type, expected_length) in EXPECTED_TYPES[table_name].items():
            column = table.c[column_name]
            assert isinstance(column.type, expected_type), f"{table_name}.{column_name} type"
            if expected_length is not None:
                assert column.type.length == expected_length


def test_release_orm_defaults_and_compatibility_columns_match_0029() -> None:
    metadata = orm.Base.metadata

    expected_defaults = {
        ("tenant_release_channels", "status"): "'active'",
        ("tenant_release_channels", "promotion_order"): "0",
        ("tenant_release_channels", "is_default_serving"): "0",
        ("tenant_release_channels", "revision"): "1",
        ("tenant_release_channels", "created_at"): "CURRENT_TIMESTAMP",
        ("tenant_release_channels", "updated_at"): "CURRENT_TIMESTAMP",
        ("dataset_release_manifests", "schema_version"): "1",
        ("dataset_release_manifests", "entry_count"): "0",
        ("dataset_release_manifests", "blocker_count"): "0",
        ("dataset_release_manifests", "created_at"): "CURRENT_TIMESTAMP",
        ("dataset_release_entries", "created_at"): "CURRENT_TIMESTAMP",
        ("dataset_release_events", "occurred_at"): "CURRENT_TIMESTAMP",
        ("dataset_channel_releases", "status"): "'active'",
        ("dataset_channel_releases", "revision"): "1",
        ("dataset_channel_releases", "created_at"): "CURRENT_TIMESTAMP",
        ("dataset_channel_releases", "updated_at"): "CURRENT_TIMESTAMP",
        ("datasets", "release_revision"): "1",
        ("app_dataset_references", "release_mode"): "'follow_channel'",
    }
    for (table_name, column_name), expected in expected_defaults.items():
        assert _server_default(metadata.tables[table_name].c[column_name]) == expected

    datasets = metadata.tables["datasets"]
    assert datasets.c.serving_release_id.nullable is True
    assert datasets.c.release_revision.nullable is False
    assert isinstance(datasets.c.serving_release_id.type, String)
    assert datasets.c.serving_release_id.type.length == 64
    assert isinstance(datasets.c.release_revision.type, Integer)

    references = metadata.tables["app_dataset_references"]
    assert references.c.release_mode.nullable is False
    assert references.c.release_channel_id.nullable is True
    assert references.c.pinned_release_id.nullable is True
    assert references.c.release_channel_id.type.length == 128
    assert references.c.pinned_release_id.type.length == 64


def test_release_orm_has_exact_named_checks_uniques_indexes_and_foreign_keys() -> None:
    from sqlalchemy import CheckConstraint, UniqueConstraint

    metadata = orm.Base.metadata

    for table_name, expected in EXPECTED_CHECKS.items():
        table = metadata.tables[table_name]
        checks = _named_constraints(table, CheckConstraint)
        for constraint_name, expression in expected.items():
            assert constraint_name in checks, f"missing {constraint_name}"
            assert _normalise_sql(checks[constraint_name].sqltext) == _normalise_sql(expression)

    for table_name, expected in EXPECTED_UNIQUE_CONSTRAINTS.items():
        table = metadata.tables[table_name]
        uniques = _named_constraints(table, UniqueConstraint)
        assert set(uniques) == set(expected)
        for constraint_name, columns in expected.items():
            assert _column_keys(uniques[constraint_name].columns) == columns

    for table_name, expected in EXPECTED_INDEXES.items():
        table = metadata.tables[table_name]
        indexes = {str(index.name): index for index in table.indexes}
        assert set(indexes) == set(expected)
        for index_name, columns in expected.items():
            assert _column_keys(indexes[index_name].columns) == columns

    for table_name, expected in EXPECTED_FOREIGN_KEYS.items():
        table = metadata.tables[table_name]
        foreign_keys = _named_foreign_keys(table)
        for constraint_name, (columns, referred_table, referred_columns) in expected.items():
            assert constraint_name in foreign_keys, f"missing {constraint_name}"
            constraint = foreign_keys[constraint_name]
            assert _column_keys(constraint.columns) == columns
            assert constraint.referred_table.name == referred_table
            assert _column_keys(constraint.elements[i].column for i in range(len(columns))) == (
                referred_columns
            )


def test_release_orm_configures_and_creates_full_metadata_on_sqlite() -> None:
    configure_mappers()
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    try:
        orm.Base.metadata.create_all(engine)
        inspector = sqlalchemy_inspect(engine)
        tables = set(inspector.get_table_names())
        assert set(RELEASE_TABLES) <= tables
        assert {"datasets", "app_dataset_references"} <= tables
        assert engine.connect().exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("table_name", "column_name"),
    [
        ("dataset_release_manifests", "manifest_digest"),
        ("dataset_release_entries", "safe_facts_json"),
        ("dataset_release_events", "request_id"),
        ("dataset_channel_releases", "active_release_id"),
    ],
)
def test_release_orm_exposes_mutable_columns_as_plain_authority_fields(
    table_name: str, column_name: str
) -> None:
    """The ORM maps authority facts without adding relationship-side effects."""
    column = orm.Base.metadata.tables[table_name].c[column_name]
    assert column.table.name == table_name
