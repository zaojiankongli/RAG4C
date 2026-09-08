from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.catalog_schema import HEAD_REVISION, _alembic_config


CONTENT_TABLES = {
    "document_versions",
    "qa_knowledge",
    "qa_alternative_questions",
}
DOCUMENT_LIFECYCLE_COLUMNS = {
    "current_version_id",
    "lifecycle_state",
    "retrieval_enabled",
    "effective_from",
    "expires_at",
    "purge_after",
}


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _unique_columns(inspector, table: str) -> set[tuple[str, ...]]:
    return {
        tuple(item.get("column_names") or ()) for item in inspector.get_unique_constraints(table)
    }


def _foreign_keys(inspector, table: str) -> set[tuple[tuple[str, ...], tuple[str, ...]]]:
    return {
        (
            tuple(item.get("constrained_columns") or ()),
            tuple(item.get("referred_columns") or ()),
        )
        for item in inspector.get_foreign_keys(table)
    }


def _insert_legacy_tenant_dataset(connection) -> None:
    connection.execute(
        text(
            "INSERT INTO tenants (id, name, plan, status, quota_documents, quota_chunks, "
            "doc_count, chunk_count, created_at) VALUES "
            "('tenant-1', 'Tenant', 'free', 'active', 1000, 100000, 0, 0, CURRENT_TIMESTAMP)"
        )
    )
    connection.execute(
        text(
            "INSERT INTO datasets (id, tenant_id, name, description, status, doc_count, "
            "chunk_count, created_at) VALUES "
            "('dataset-1', 'tenant-1', 'Knowledge', '', 'active', 0, 0, CURRENT_TIMESTAMP)"
        )
    )


def test_0009_sqlite_upgrade_downgrade_reupgrade_and_head(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path / "content-migration.db")
    config = _alembic_config(url)
    command.upgrade(config, "0008_governance")
    engine = create_engine(url)
    with engine.begin() as connection:
        _insert_legacy_tenant_dataset(connection)
        connection.execute(
            text(
                "INSERT INTO documents ("
                "id, dataset_id, tenant_id, name, file_path, file_hash, doc_type, "
                "status, status_detail, progress, chunk_count, error_message, parser_meta, "
                "created_at, updated_at"
                ") VALUES ("
                "'doc-1', 'dataset-1', 'tenant-1', 'Existing document', '', '', '', "
                "'waiting', '', 0, 0, '', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP"
                ")"
            )
        )
    inspector = inspect(engine)
    assert not (CONTENT_TABLES & set(inspector.get_table_names()))
    assert not (
        DOCUMENT_LIFECYCLE_COLUMNS & {item["name"] for item in inspector.get_columns("documents")}
    )
    engine.dispose()

    command.upgrade(config, "0009_content")
    engine = create_engine(url)
    inspector = inspect(engine)
    assert CONTENT_TABLES <= set(inspector.get_table_names())
    assert DOCUMENT_LIFECYCLE_COLUMNS <= {
        item["name"] for item in inspector.get_columns("documents")
    }
    assert ("tenant_id", "dataset_id", "document_id", "revision") in _unique_columns(
        inspector, "document_versions"
    )
    assert ("tenant_id", "dataset_id", "id") in _unique_columns(inspector, "qa_knowledge")
    assert (
        "tenant_id",
        "dataset_id",
        "qa_id",
        "normalized_hash",
    ) in _unique_columns(inspector, "qa_alternative_questions")
    assert (
        ("tenant_id", "dataset_id"),
        ("tenant_id", "id"),
    ) in _foreign_keys(inspector, "document_versions")
    assert (
        ("tenant_id", "dataset_id", "document_id"),
        ("tenant_id", "dataset_id", "id"),
    ) in _foreign_keys(inspector, "document_versions")
    assert (
        ("tenant_id", "dataset_id", "id", "current_version_id"),
        ("tenant_id", "dataset_id", "document_id", "id"),
    ) in _foreign_keys(inspector, "documents")
    assert (
        ("tenant_id", "dataset_id"),
        ("tenant_id", "id"),
    ) in _foreign_keys(inspector, "qa_knowledge")
    assert (
        ("tenant_id", "dataset_id", "source_document_id"),
        ("tenant_id", "dataset_id", "id"),
    ) in _foreign_keys(inspector, "qa_knowledge")
    assert (
        ("tenant_id", "dataset_id"),
        ("tenant_id", "id"),
    ) in _foreign_keys(inspector, "qa_alternative_questions")
    assert (
        ("tenant_id", "dataset_id", "qa_id"),
        ("tenant_id", "dataset_id", "id"),
    ) in _foreign_keys(inspector, "qa_alternative_questions")
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT lifecycle_state, retrieval_enabled, current_version_id "
                "FROM documents WHERE id='doc-1'"
            )
        ).one()
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert tuple(row) == ("active", 1, None)
    assert revision == "0009_content"
    assert ScriptDirectory.from_config(config).get_current_head() == HEAD_REVISION
    engine.dispose()

    command.downgrade(config, "0008_governance")
    engine = create_engine(url)
    inspector = inspect(engine)
    assert not (CONTENT_TABLES & set(inspector.get_table_names()))
    assert not (
        DOCUMENT_LIFECYCLE_COLUMNS & {item["name"] for item in inspector.get_columns("documents")}
    )
    engine.dispose()

    command.upgrade(config, "0009_content")
    engine = create_engine(url)
    try:
        assert CONTENT_TABLES <= set(inspect(engine).get_table_names())
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0009_content"
            )
    finally:
        engine.dispose()


def test_0009_uses_mysql_safe_index_columns_and_unified_lifecycle_names(
    tmp_path: Path,
) -> None:
    url = _sqlite_url(tmp_path / "content-indexes.db")
    command.upgrade(_alembic_config(url), "0009_content")
    engine = create_engine(url)
    inspector = inspect(engine)

    for table in CONTENT_TABLES:
        columns = {item["name"]: item["type"] for item in inspector.get_columns(table)}
        for index in inspector.get_indexes(table):
            for name in index["column_names"]:
                assert name not in {"question", "answer", "source_identity"}
                length = getattr(columns[name], "length", None)
                assert length is None or length <= 256

    document_columns = {item["name"] for item in inspector.get_columns("documents")}
    qa_columns = {item["name"] for item in inspector.get_columns("qa_knowledge")}
    assert {"lifecycle_state", "retrieval_enabled", "effective_from", "expires_at"} <= (
        document_columns & qa_columns
    )
    assert "purge_after" in document_columns
    assert not {
        "lifecycle_status",
        "is_searchable",
        "mutation_generation",
        "active_delete_operation_id",
        "deleted_at",
        "usage_released_at",
        "serving_generation",
    } & (document_columns | qa_columns)
    engine.dispose()


def test_unified_lifecycle_reserved_delete_states_are_non_retrievable(
    tmp_path: Path,
) -> None:
    url = _sqlite_url(tmp_path / "content-lifecycle.db")
    command.upgrade(_alembic_config(url), "0009_content")
    engine = create_engine(url)
    with engine.begin() as connection:
        _insert_legacy_tenant_dataset(connection)
        connection.execute(
            text(
                "INSERT INTO documents ("
                "id, dataset_id, tenant_id, name, file_path, file_hash, doc_type, "
                "status, status_detail, progress, chunk_count, error_message, parser_meta, "
                "created_at, updated_at"
                ") VALUES ("
                "'doc-1', 'dataset-1', 'tenant-1', 'Document', '', '', '', "
                "'waiting', '', 0, 0, '', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP"
                ")"
            )
        )
        connection.execute(
            text(
                "INSERT INTO qa_knowledge ("
                "id, tenant_id, dataset_id, question, answer, source_uri, created_by, "
                "created_at, updated_at"
                ") VALUES ("
                "'qa-1', 'tenant-1', 'dataset-1', 'Question', 'Answer', '', 'user-1', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP"
                ")"
            )
        )

    with Session(engine) as session:
        for state in (
            "expired",
            "delete_requested",
            "deleting",
            "delete_failed",
            "deleted",
        ):
            session.execute(
                text(
                    "UPDATE documents SET lifecycle_state=:state, retrieval_enabled=0 "
                    "WHERE id='doc-1'"
                ),
                {"state": state},
            )
            session.execute(
                text(
                    "UPDATE qa_knowledge SET lifecycle_state=:state, retrieval_enabled=0 "
                    "WHERE id='qa-1'"
                ),
                {"state": state},
            )
            session.commit()

        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "UPDATE documents SET lifecycle_state='expired', retrieval_enabled=1 "
                    "WHERE id='doc-1'"
                )
            )
            session.commit()
        session.rollback()

        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "UPDATE qa_knowledge SET lifecycle_state='delete_requested', "
                    "retrieval_enabled=1 WHERE id='qa-1'"
                )
            )
            session.commit()
        session.rollback()

        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "UPDATE qa_knowledge SET lifecycle_state='active', "
                    "review_status='pending', retrieval_enabled=1 WHERE id='qa-1'"
                )
            )
            session.commit()
        session.rollback()
    engine.dispose()
