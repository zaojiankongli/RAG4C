"""Add durable document deletion request authority.

Revision ID: 0011_durable_delete
Revises: 0010_dataset_profile
Create Date: 2026-08-25
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "0011_durable_delete"
down_revision: str | None = "0010_dataset_profile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _datetime6():
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


def _assert_source_doc_ids_fit() -> None:
    context = op.get_context()
    if context.as_sql:
        return
    too_long = int(
        op.get_bind()
        .execute(sa.text("SELECT COUNT(*) FROM source_document_states WHERE LENGTH(doc_id) > 64"))
        .scalar()
        or 0
    )
    if too_long:
        raise RuntimeError(
            "source_document_states contains doc_id values longer than the document authority key"
        )


def upgrade() -> None:
    with op.batch_alter_table("datasets") as batch:
        batch.add_column(
            sa.Column("mutation_generation", sa.BigInteger(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("serving_generation", sa.BigInteger(), nullable=False, server_default="0")
        )
        batch.create_check_constraint(
            "ck_datasets_generations_nonnegative",
            "mutation_generation >= 0 AND serving_generation >= 0",
        )

    op.create_table(
        "document_delete_batches",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("requested_count", sa.Integer(), nullable=False),
        sa.Column("accepted_count", sa.Integer(), nullable=False),
        sa.Column("completed_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("rejected_count", sa.Integer(), nullable=False),
        sa.Column("created_at", _datetime6(), nullable=False),
        sa.Column("updated_at", _datetime6(), nullable=False),
        sa.Column("finished_at", _datetime6(), nullable=True),
        sa.CheckConstraint(
            "status IN ('preparing', 'running', 'completed', 'partially_failed', 'failed')",
            name="ck_document_delete_batches_status",
        ),
        sa.CheckConstraint(
            "requested_count >= 0 AND accepted_count >= 0 AND completed_count >= 0 "
            "AND failed_count >= 0 AND rejected_count >= 0",
            name="ck_document_delete_batches_counts",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_document_delete_batches_scope_dataset",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "id",
            name="uq_document_delete_batches_scope_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "idempotency_key",
            name="uq_document_delete_batches_idempotency",
        ),
    )
    for name, columns in (
        ("ix_document_delete_batches_tenant_id", ["tenant_id"]),
        ("ix_document_delete_batches_dataset_id", ["dataset_id"]),
        ("ix_document_delete_batches_status", ["status"]),
        (
            "ix_document_delete_batches_scope_status",
            ["tenant_id", "dataset_id", "status", "created_at"],
        ),
    ):
        op.create_index(name, "document_delete_batches", columns, unique=False)

    with op.batch_alter_table("document_ingest_attempts") as batch:
        batch.create_unique_constraint(
            "uq_ingest_attempt_scope_id", ["tenant_id", "dataset_id", "id"]
        )
        batch.add_column(
            sa.Column(
                "attempt_kind",
                sa.String(length=24),
                nullable=False,
                server_default="ingest",
            )
        )
        batch.add_column(
            sa.Column("document_generation", sa.BigInteger(), nullable=False, server_default="0")
        )
        batch.create_check_constraint(
            "ck_ingest_attempt_kind",
            "attempt_kind IN ('ingest', 'reindex', 'source_sync', 'chunk_mutation', "
            "'document_delete', 'restore')",
        )
        batch.create_check_constraint(
            "ck_ingest_attempt_document_generation_nonnegative",
            "document_generation >= 0",
        )
        batch.create_index(
            "ix_ingest_attempt_document_generation",
            ["document_id", "document_generation", "attempt_kind"],
        )

    op.create_table(
        "document_delete_operations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("batch_id", sa.String(length=64), nullable=True),
        sa.Column("request_index", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("requested_document_id", sa.String(length=128), nullable=False),
        sa.Column("document_id", sa.String(length=64), nullable=True),
        sa.Column("expected_generation", sa.BigInteger(), nullable=True),
        sa.Column("delete_generation", sa.BigInteger(), nullable=True),
        sa.Column("attempt_id", sa.String(length=64), nullable=True),
        sa.Column("origin", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("result_code", sa.String(length=64), nullable=False),
        sa.Column("result_message", sa.Text(), nullable=False),
        sa.Column("chunk_manifest_count", sa.Integer(), nullable=False),
        sa.Column("chunk_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("quota_chunk_count", sa.Integer(), nullable=False),
        sa.Column("required_store_count", sa.Integer(), nullable=False),
        sa.Column("completed_store_count", sa.Integer(), nullable=False),
        sa.Column("failed_store_count", sa.Integer(), nullable=False),
        sa.Column("requested_by", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.String(length=512), nullable=False),
        sa.Column("started_at", _datetime6(), nullable=True),
        sa.Column("finalized_at", _datetime6(), nullable=True),
        sa.Column("created_at", _datetime6(), nullable=False),
        sa.Column("updated_at", _datetime6(), nullable=False),
        sa.CheckConstraint(
            "origin IN ('operator', 'source_sync', 'dataset_reset', 'retention')",
            name="ck_document_delete_operations_origin",
        ),
        sa.CheckConstraint(
            "status IN ('rejected', 'queued', 'projecting', 'finalizing', 'completed', 'failed')",
            name="ck_document_delete_operations_status",
        ),
        sa.CheckConstraint(
            "request_index >= 0 AND chunk_manifest_count >= 0 AND quota_chunk_count >= 0 "
            "AND required_store_count >= 0 AND completed_store_count >= 0 "
            "AND failed_store_count >= 0 AND completed_store_count + failed_store_count <= required_store_count",
            name="ck_document_delete_operations_counts",
        ),
        sa.CheckConstraint(
            "(expected_generation IS NULL OR expected_generation >= 0) AND "
            "(delete_generation IS NULL OR delete_generation >= 0)",
            name="ck_document_delete_operations_generations",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_document_delete_operations_scope_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "batch_id"],
            [
                "document_delete_batches.tenant_id",
                "document_delete_batches.dataset_id",
                "document_delete_batches.id",
            ],
            name="fk_document_delete_operations_scope_batch",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "document_id"],
            ["documents.tenant_id", "documents.dataset_id", "documents.id"],
            name="fk_document_delete_operations_scope_document",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "attempt_id"],
            [
                "document_ingest_attempts.tenant_id",
                "document_ingest_attempts.dataset_id",
                "document_ingest_attempts.id",
            ],
            name="fk_document_delete_operations_scope_attempt",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "id",
            name="uq_document_delete_operations_scope_id",
        ),
        sa.UniqueConstraint(
            "document_id",
            "delete_generation",
            name="uq_document_delete_operations_generation",
        ),
        sa.UniqueConstraint(
            "document_id",
            "id",
            name="uq_document_delete_operations_document_id",
        ),
        sa.UniqueConstraint(
            "batch_id",
            "request_index",
            name="uq_document_delete_operations_batch_index",
        ),
    )
    for name, columns in (
        ("ix_document_delete_operations_tenant_id", ["tenant_id"]),
        ("ix_document_delete_operations_dataset_id", ["dataset_id"]),
        ("ix_document_delete_operations_status", ["status"]),
        ("ix_document_delete_operations_batch_status", ["batch_id", "status"]),
        ("ix_document_delete_operations_document_status", ["document_id", "status"]),
        (
            "ix_document_delete_operations_scope_status",
            ["tenant_id", "dataset_id", "status", "created_at"],
        ),
    ):
        op.create_index(name, "document_delete_operations", columns, unique=False)

    with op.batch_alter_table("documents") as batch:
        batch.add_column(
            sa.Column("mutation_generation", sa.BigInteger(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("active_delete_operation_id", sa.String(length=64), nullable=True)
        )
        batch.add_column(sa.Column("deletion_requested_at", _datetime6(), nullable=True))
        batch.add_column(sa.Column("deleted_at", _datetime6(), nullable=True))
        batch.add_column(sa.Column("usage_released_at", _datetime6(), nullable=True))
        batch.create_check_constraint(
            "ck_documents_mutation_generation_nonnegative",
            "mutation_generation >= 0",
        )
        batch.create_foreign_key(
            "fk_documents_scope_active_delete_operation",
            "document_delete_operations",
            ["tenant_id", "dataset_id", "active_delete_operation_id"],
            ["tenant_id", "dataset_id", "id"],
        )
        batch.create_index(
            "ix_documents_scope_lifecycle",
            ["tenant_id", "dataset_id", "lifecycle_state", "id"],
        )
        batch.create_index(
            "ix_documents_scope_retrieval",
            ["tenant_id", "dataset_id", "retrieval_enabled", "id"],
        )
        batch.create_index(
            "ix_documents_active_delete_operation",
            ["active_delete_operation_id"],
        )

    with op.batch_alter_table("index_operations") as batch:
        batch.add_column(
            sa.Column("document_generation", sa.BigInteger(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("delete_operation_id", sa.String(length=64), nullable=True))
        batch.create_check_constraint(
            "ck_index_operations_document_generation_nonnegative",
            "document_generation >= 0",
        )
        batch.create_foreign_key(
            "fk_index_operations_scope_delete_operation",
            "document_delete_operations",
            ["tenant_id", "dataset_id", "delete_operation_id"],
            ["tenant_id", "dataset_id", "id"],
        )
        batch.create_index(
            "ix_index_operations_delete_status",
            ["delete_operation_id", "status"],
        )
        batch.create_index(
            "ix_index_operations_document_generation",
            ["document_id", "document_generation", "status"],
        )

    with op.batch_alter_table("data_sources") as batch:
        batch.add_column(
            sa.Column("mutation_generation", sa.BigInteger(), nullable=False, server_default="0")
        )
        batch.create_check_constraint(
            "ck_data_sources_mutation_generation_nonnegative",
            "mutation_generation >= 0",
        )

    with op.batch_alter_table("source_sync_runs") as batch:
        batch.add_column(
            sa.Column("source_generation", sa.BigInteger(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("dataset_generation", sa.BigInteger(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("pending_deletes", sa.Integer(), nullable=False, server_default="0")
        )
        batch.create_check_constraint(
            "ck_source_sync_runs_generations_counts",
            "source_generation >= 0 AND dataset_generation >= 0 AND pending_deletes >= 0",
        )

    _assert_source_doc_ids_fit()
    with op.batch_alter_table("source_document_states") as batch:
        batch.alter_column(
            "doc_id",
            existing_type=sa.String(length=128),
            type_=sa.String(length=64),
            existing_nullable=False,
        )
        batch.add_column(
            sa.Column("state", sa.String(length=24), nullable=False, server_default="active")
        )
        batch.add_column(
            sa.Column("document_generation", sa.BigInteger(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("delete_operation_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("suppressed_at", _datetime6(), nullable=True))
        batch.create_check_constraint(
            "ck_source_document_states_state",
            "state IN ('active', 'operator_suppressed', 'upstream_absent', 'delete_pending')",
        )
        batch.create_check_constraint(
            "ck_source_document_states_document_generation_nonnegative",
            "document_generation >= 0",
        )
        batch.create_foreign_key(
            "fk_source_document_states_scope_delete_operation",
            "document_delete_operations",
            ["doc_id", "delete_operation_id"],
            ["document_id", "id"],
        )
        batch.create_index(
            "ix_source_document_state_delete_operation",
            ["delete_operation_id"],
        )


def downgrade() -> None:
    # Drop every child reference to document_delete_operations before removing
    # the parent table or any referenced unique/index contract.
    with op.batch_alter_table("source_document_states") as batch:
        batch.drop_constraint(
            "fk_source_document_states_scope_delete_operation", type_="foreignkey"
        )
    with op.batch_alter_table("index_operations") as batch:
        batch.drop_constraint("fk_index_operations_scope_delete_operation", type_="foreignkey")
    with op.batch_alter_table("documents") as batch:
        batch.drop_constraint("fk_documents_scope_active_delete_operation", type_="foreignkey")

    op.drop_table("document_delete_operations")
    op.drop_table("document_delete_batches")

    with op.batch_alter_table("source_document_states") as batch:
        batch.drop_index("ix_source_document_state_delete_operation")
        batch.drop_constraint(
            "ck_source_document_states_document_generation_nonnegative", type_="check"
        )
        batch.drop_constraint("ck_source_document_states_state", type_="check")
        batch.drop_column("suppressed_at")
        batch.drop_column("delete_operation_id")
        batch.drop_column("document_generation")
        batch.drop_column("state")
        batch.alter_column(
            "doc_id",
            existing_type=sa.String(length=64),
            type_=sa.String(length=128),
            existing_nullable=False,
        )

    with op.batch_alter_table("index_operations") as batch:
        batch.drop_index("ix_index_operations_document_generation")
        batch.drop_index("ix_index_operations_delete_status")
        batch.drop_constraint("ck_index_operations_document_generation_nonnegative", type_="check")
        batch.drop_column("delete_operation_id")
        batch.drop_column("document_generation")

    with op.batch_alter_table("document_ingest_attempts") as batch:
        batch.drop_index("ix_ingest_attempt_document_generation")
        batch.drop_constraint("ck_ingest_attempt_document_generation_nonnegative", type_="check")
        batch.drop_constraint("ck_ingest_attempt_kind", type_="check")
        batch.drop_constraint("uq_ingest_attempt_scope_id", type_="unique")
        batch.drop_column("document_generation")
        batch.drop_column("attempt_kind")

    with op.batch_alter_table("documents") as batch:
        batch.drop_index("ix_documents_active_delete_operation")
        batch.drop_index("ix_documents_scope_retrieval")
        batch.drop_index("ix_documents_scope_lifecycle")
        batch.drop_constraint("ck_documents_mutation_generation_nonnegative", type_="check")
        batch.drop_column("usage_released_at")
        batch.drop_column("deleted_at")
        batch.drop_column("deletion_requested_at")
        batch.drop_column("active_delete_operation_id")
        batch.drop_column("mutation_generation")

    with op.batch_alter_table("source_sync_runs") as batch:
        batch.drop_constraint("ck_source_sync_runs_generations_counts", type_="check")
        batch.drop_column("pending_deletes")
        batch.drop_column("dataset_generation")
        batch.drop_column("source_generation")

    with op.batch_alter_table("data_sources") as batch:
        batch.drop_constraint("ck_data_sources_mutation_generation_nonnegative", type_="check")
        batch.drop_column("mutation_generation")

    with op.batch_alter_table("datasets") as batch:
        batch.drop_constraint("ck_datasets_generations_nonnegative", type_="check")
        batch.drop_column("serving_generation")
        batch.drop_column("mutation_generation")
