from __future__ import annotations

from io import StringIO
import os
from pathlib import Path
import uuid

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.catalog_schema import HEAD_REVISION, _alembic_config
from models.orm import (
    DataSourceRecord,
    Dataset,
    Document,
    DocumentDeleteBatch,
    SourceDocumentState,
    Tenant,
)


ADDED_COLUMNS = {
    "datasets": {"mutation_generation", "serving_generation"},
    "documents": {
        "mutation_generation",
        "active_delete_operation_id",
        "deletion_requested_at",
        "deleted_at",
        "usage_released_at",
    },
    "document_ingest_attempts": {"attempt_kind", "document_generation"},
    "index_operations": {"document_generation", "delete_operation_id"},
    "data_sources": {"mutation_generation"},
    "source_sync_runs": {"source_generation", "dataset_generation", "pending_deletes"},
    "source_document_states": {
        "state",
        "document_generation",
        "delete_operation_id",
        "suppressed_at",
    },
}


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def test_0011_sqlite_upgrade_downgrade_reupgrade_and_defaults(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path / "durable-delete-migration.db")
    config = _alembic_config(url)
    command.upgrade(config, "0010_dataset_profile")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenants (id, name, plan, status, quota_documents, quota_chunks, "
                "doc_count, chunk_count, created_at) VALUES "
                "('tenant-1', 'Tenant', 'free', 'active', 1000, 100000, 0, 0, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO datasets (id, tenant_id, name, description, status, profile_revision, "
                "visibility, profile_json, parser_policy, chunk_policy, retrieval_policy, retention_policy, "
                "metadata_policy, default_language, graph_enabled, qa_enabled, doc_count, chunk_count, "
                "created_at, updated_at) VALUES "
                "('dataset-1', 'tenant-1', 'Knowledge', '', 'active', 1, 'private', '{}', '{}', '{}', '{}', "
                "'{}', '{}', 'zh-CN', 0, 1, 1, 2, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO documents ("
                "id, dataset_id, tenant_id, name, file_path, file_hash, doc_type, status, "
                "status_detail, progress, chunk_count, error_message, parser_meta, content_revision, "
                "desired_index_revision, indexed_revision, graph_revision, lifecycle_state, "
                "retrieval_enabled, created_at, updated_at"
                ") VALUES ("
                "'doc-1', 'dataset-1', 'tenant-1', 'Guide', '', '', '', 'completed', '', 1, 2, '', "
                "'{}', 4, 4, 4, 4, 'active', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
    engine.dispose()

    command.upgrade(config, "0011_durable_delete")
    engine = create_engine(url)
    inspector = inspect(engine)
    assert {"document_delete_batches", "document_delete_operations"} <= set(
        inspector.get_table_names()
    )
    for table, expected in ADDED_COLUMNS.items():
        assert expected <= {column["name"] for column in inspector.get_columns(table)}
    with engine.connect() as connection:
        dataset = connection.execute(
            text(
                "SELECT mutation_generation, serving_generation FROM datasets WHERE id='dataset-1'"
            )
        ).one()
        document = connection.execute(
            text(
                "SELECT mutation_generation, active_delete_operation_id, deletion_requested_at, "
                "deleted_at, usage_released_at FROM documents WHERE id='doc-1'"
            )
        ).one()
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert tuple(dataset) == (0, 0)
    assert tuple(document) == (0, None, None, None, None)
    assert revision == "0011_durable_delete"
    assert ScriptDirectory.from_config(config).get_current_head() == HEAD_REVISION
    engine.dispose()

    command.downgrade(config, "0010_dataset_profile")
    engine = create_engine(url)
    inspector = inspect(engine)
    assert "document_delete_batches" not in inspector.get_table_names()
    assert "document_delete_operations" not in inspector.get_table_names()
    for table, expected in ADDED_COLUMNS.items():
        assert not expected & {column["name"] for column in inspector.get_columns(table)}
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(url)
    try:
        assert {
            "document_delete_batches",
            "document_delete_operations",
        } <= set(inspect(engine).get_table_names())
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == HEAD_REVISION
    finally:
        engine.dispose()


def test_0011_constraints_scope_foreign_keys_and_state_checks(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path / "durable-delete-contract.db")
    command.upgrade(_alembic_config(url), "0011_durable_delete")
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    inspector = inspect(engine)
    batch_uniques = {
        (item.get("name"), tuple(item.get("column_names") or ()))
        for item in inspector.get_unique_constraints("document_delete_batches")
    }
    assert (
        "uq_document_delete_batches_idempotency",
        ("tenant_id", "dataset_id", "idempotency_key"),
    ) in batch_uniques
    operation_uniques = {
        (item.get("name"), tuple(item.get("column_names") or ()))
        for item in inspector.get_unique_constraints("document_delete_operations")
    }
    assert (
        "uq_document_delete_operations_generation",
        ("document_id", "delete_generation"),
    ) in operation_uniques
    assert (
        "uq_document_delete_operations_batch_index",
        ("batch_id", "request_index"),
    ) in operation_uniques
    assert (
        "uq_document_delete_operations_document_id",
        ("document_id", "id"),
    ) in operation_uniques

    batch_fks = {
        (
            item.get("name"),
            tuple(item.get("constrained_columns") or ()),
            item.get("referred_table"),
            tuple(item.get("referred_columns") or ()),
        )
        for item in inspector.get_foreign_keys("document_delete_batches")
    }
    operation_fks = {
        (
            item.get("name"),
            tuple(item.get("constrained_columns") or ()),
            item.get("referred_table"),
            tuple(item.get("referred_columns") or ()),
        )
        for item in inspector.get_foreign_keys("document_delete_operations")
    }
    assert (
        "fk_document_delete_batches_scope_dataset",
        ("tenant_id", "dataset_id"),
        "datasets",
        ("tenant_id", "id"),
    ) in batch_fks
    assert (
        "fk_document_delete_operations_scope_document",
        ("tenant_id", "dataset_id", "document_id"),
        "documents",
        ("tenant_id", "dataset_id", "id"),
    ) in operation_fks
    assert (
        "fk_document_delete_operations_scope_batch",
        ("tenant_id", "dataset_id", "batch_id"),
        "document_delete_batches",
        ("tenant_id", "dataset_id", "id"),
    ) in operation_fks

    document_fks = {
        (
            item.get("name"),
            tuple(item.get("constrained_columns") or ()),
            item.get("referred_table"),
            tuple(item.get("referred_columns") or ()),
        )
        for item in inspector.get_foreign_keys("documents")
    }
    assert (
        "fk_documents_scope_active_delete_operation",
        ("tenant_id", "dataset_id", "active_delete_operation_id"),
        "document_delete_operations",
        ("tenant_id", "dataset_id", "id"),
    ) in document_fks
    assert any(
        item.get("name") == "fk_index_operations_scope_delete_operation"
        and tuple(item.get("constrained_columns") or ())
        == ("tenant_id", "dataset_id", "delete_operation_id")
        for item in inspector.get_foreign_keys("index_operations")
    )
    assert any(
        item.get("name") == "fk_source_document_states_scope_delete_operation"
        and tuple(item.get("constrained_columns") or ()) == ("doc_id", "delete_operation_id")
        for item in inspector.get_foreign_keys("source_document_states")
    )

    attempt_uniques = {
        (item.get("name"), tuple(item.get("column_names") or ()))
        for item in inspector.get_unique_constraints("document_ingest_attempts")
    }
    assert (
        "uq_ingest_attempt_scope_id",
        ("tenant_id", "dataset_id", "id"),
    ) in attempt_uniques
    assert (
        "fk_document_delete_operations_scope_attempt",
        ("tenant_id", "dataset_id", "attempt_id"),
        "document_ingest_attempts",
        ("tenant_id", "dataset_id", "id"),
    ) in operation_fks

    checks = {
        item.get("name") for item in inspector.get_check_constraints("document_delete_operations")
    }
    assert {
        "ck_document_delete_operations_origin",
        "ck_document_delete_operations_status",
        "ck_document_delete_operations_counts",
    } <= checks
    source_checks = {
        item.get("name") for item in inspector.get_check_constraints("source_document_states")
    }
    assert "ck_source_document_states_state" in source_checks
    engine.dispose()


def test_0011_mysql_offline_ddl_uses_datetime6_and_safe_indexes() -> None:
    config = _alembic_config("mysql+pymysql://user:pass@localhost/rag4c")
    output = StringIO()
    config.output_buffer = output
    command.upgrade(config, "0010_dataset_profile:0011_durable_delete", sql=True)
    ddl = output.getvalue().upper()
    assert "DATETIME(6)" in ddl
    assert "DOCUMENT_DELETE_BATCHES" in ddl
    assert "DOCUMENT_DELETE_OPERATIONS" in ddl
    assert "PROFILE_JSON" not in " ".join(
        line for line in ddl.splitlines() if "CREATE INDEX" in line
    )


def test_0011_mysql_offline_orders_parent_keys_before_children_and_safe_downgrade() -> None:
    config = _alembic_config("mysql+pymysql://user:pass@localhost/rag4c")
    upgrade_output = StringIO()
    config.output_buffer = upgrade_output
    command.upgrade(config, "0010_dataset_profile:0011_durable_delete", sql=True)
    upgrade_sql = upgrade_output.getvalue().upper()
    attempt_unique = upgrade_sql.index("UQ_INGEST_ATTEMPT_SCOPE_ID")
    delete_table = upgrade_sql.index("CREATE TABLE DOCUMENT_DELETE_OPERATIONS")
    assert attempt_unique < delete_table

    downgrade_config = _alembic_config("mysql+pymysql://user:pass@localhost/rag4c")
    downgrade_output = StringIO()
    downgrade_config.output_buffer = downgrade_output
    command.downgrade(
        downgrade_config,
        "0011_durable_delete:0010_dataset_profile",
        sql=True,
    )
    downgrade_sql = downgrade_output.getvalue().upper()
    drop_source_fk = downgrade_sql.index("FK_SOURCE_DOCUMENT_STATES_SCOPE_DELETE_OPERATION")
    drop_index_fk = downgrade_sql.index("FK_INDEX_OPERATIONS_SCOPE_DELETE_OPERATION")
    drop_document_fk = downgrade_sql.index("FK_DOCUMENTS_SCOPE_ACTIVE_DELETE_OPERATION")
    drop_delete_table = downgrade_sql.index("DROP TABLE DOCUMENT_DELETE_OPERATIONS")
    drop_attempt_unique = downgrade_sql.index("UQ_INGEST_ATTEMPT_SCOPE_ID", drop_delete_table)
    assert max(drop_source_fk, drop_index_fk, drop_document_fk) < drop_delete_table
    assert drop_delete_table < drop_attempt_unique


def test_orm_defaults_and_database_state_constraints() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    BaseMetadata = DocumentDeleteBatch.metadata
    BaseMetadata.create_all(engine)
    with Session(engine) as session:
        tenant = Tenant(id="tenant-1", name="Tenant")
        dataset = Dataset(id="dataset-1", tenant_id="tenant-1", name="Dataset")
        document = Document(
            id="doc-1",
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            name="Guide",
        )
        session.add_all([tenant, dataset, document])
        session.commit()
        assert dataset.mutation_generation == 0
        assert dataset.serving_generation == 0
        assert document.mutation_generation == 0

        source = DataSourceRecord(
            id="source-1",
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            name="Source",
            source_type="local_dir",
        )
        session.add(source)
        session.flush()
        state = SourceDocumentState(
            id="state-1",
            source_id="source-1",
            doc_id="doc-1",
            external_id="external-1",
        )
        session.add(state)
        session.commit()
        assert source.mutation_generation == 0
        assert state.state == "active"
        assert state.document_generation == 0

        state.state = "invalid"
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
        else:
            raise AssertionError("invalid source state must be rejected")
    engine.dispose()


@pytest.mark.skipif(not os.getenv("TEST_MYSQL_URL"), reason="TEST_MYSQL_URL not configured")
def test_0011_disposable_mysql_round_trip() -> None:
    source = make_url(os.environ["TEST_MYSQL_URL"])
    assert source.get_backend_name() == "mysql"
    database_name = f"rag4c_dd1_{uuid.uuid4().hex[:12]}"
    admin_url = source.set(database="mysql")
    test_url = source.set(database=database_name)
    admin_engine = create_engine(admin_url)
    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f"CREATE DATABASE `{database_name}` CHARACTER SET utf8mb4"))
        rendered = test_url.render_as_string(hide_password=False)
        config = _alembic_config(rendered)
        command.upgrade(config, "0010_dataset_profile")
        command.upgrade(config, "0011_durable_delete")
        engine = create_engine(test_url)
        try:
            assert {"document_delete_batches", "document_delete_operations"} <= set(
                inspect(engine).get_table_names()
            )
            with engine.connect() as connection:
                assert (
                    connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                    == "0011_durable_delete"
                )
        finally:
            engine.dispose()
        command.downgrade(config, "0010_dataset_profile")
        command.upgrade(config, "0011_durable_delete")
    finally:
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{database_name}`"))
        admin_engine.dispose()
