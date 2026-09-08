"""Add authoritative document versions, QA knowledge, and unified lifecycle truth.

Revision ID: 0009_content
Revises: 0008_governance
Create Date: 2026-08-25
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "0009_content"
down_revision: str | None = "0008_governance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LIFECYCLE_CHECK = (
    "lifecycle_state IN ('active', 'expired', 'delete_requested', "
    "'deleting', 'delete_failed', 'deleted')"
)


def _datetime6():
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("current_version_id", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column(
                "lifecycle_state",
                sa.String(length=24),
                nullable=False,
                server_default="active",
            )
        )
        batch.add_column(
            sa.Column(
                "retrieval_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch.add_column(sa.Column("effective_from", _datetime6(), nullable=True))
        batch.add_column(sa.Column("expires_at", _datetime6(), nullable=True))
        batch.add_column(sa.Column("purge_after", _datetime6(), nullable=True))
        batch.create_check_constraint("ck_documents_lifecycle_state", _LIFECYCLE_CHECK)
        batch.create_check_constraint(
            "ck_documents_retrieval_lifecycle",
            "lifecycle_state = 'active' OR NOT retrieval_enabled",
        )
        batch.create_index("ix_documents_current_version_id", ["current_version_id"])
        batch.create_index("ix_documents_lifecycle_state", ["lifecycle_state"])
        batch.create_index("ix_documents_retrieval_enabled", ["retrieval_enabled"])
        batch.create_index("ix_documents_expires_at", ["expires_at"])
        batch.create_index("ix_documents_purge_after", ["purge_after"])
        batch.create_index(
            "ix_documents_scope_effective",
            [
                "tenant_id",
                "dataset_id",
                "lifecycle_state",
                "retrieval_enabled",
                "effective_from",
                "expires_at",
            ],
        )

    op.create_table(
        "document_versions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("document_id", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("source_identity", sa.String(length=512), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("parser_policy_snapshot", sa.JSON(), nullable=True),
        sa.Column("parser_metadata", sa.JSON(), nullable=True),
        sa.Column("source_content_ref", sa.String(length=1024), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("change_reason", sa.String(length=512), nullable=False),
        sa.Column("created_at", _datetime6(), nullable=False),
        sa.CheckConstraint("revision > 0", name="ck_document_versions_revision_positive"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_document_versions_scope_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "document_id"],
            ["documents.tenant_id", "documents.dataset_id", "documents.id"],
            name="fk_document_versions_scope_document",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "document_id",
            "id",
            name="uq_document_versions_scope_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "document_id",
            "revision",
            name="uq_document_versions_document_revision",
        ),
    )
    for name, columns in (
        ("ix_document_versions_tenant_id", ["tenant_id"]),
        ("ix_document_versions_dataset_id", ["dataset_id"]),
        ("ix_document_versions_document_id", ["document_id"]),
        (
            "ix_document_versions_scope_revision",
            ["tenant_id", "dataset_id", "document_id", "revision"],
        ),
        (
            "ix_document_versions_scope_hash",
            ["tenant_id", "dataset_id", "source_hash"],
        ),
    ):
        op.create_index(name, "document_versions", columns, unique=False)

    with op.batch_alter_table("documents") as batch:
        batch.create_foreign_key(
            "fk_documents_scope_current_version",
            "document_versions",
            ["tenant_id", "dataset_id", "id", "current_version_id"],
            ["tenant_id", "dataset_id", "document_id", "id"],
        )

    op.create_table(
        "qa_knowledge",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(length=24), nullable=False, server_default="manual"),
        sa.Column("review_status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("lifecycle_state", sa.String(length=24), nullable=False, server_default="active"),
        sa.Column(
            "retrieval_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("effective_from", _datetime6(), nullable=True),
        sa.Column("expires_at", _datetime6(), nullable=True),
        sa.Column("source_document_id", sa.String(length=64), nullable=True),
        sa.Column("source_uri", sa.String(length=1024), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("reviewed_by", sa.String(length=64), nullable=True),
        sa.Column("reviewed_at", _datetime6(), nullable=True),
        sa.Column("created_at", _datetime6(), nullable=False),
        sa.Column("updated_at", _datetime6(), nullable=False),
        sa.CheckConstraint("revision > 0", name="ck_qa_knowledge_revision_positive"),
        sa.CheckConstraint(
            "review_status IN ('pending', 'approved', 'rejected')",
            name="ck_qa_knowledge_review_status",
        ),
        sa.CheckConstraint(_LIFECYCLE_CHECK, name="ck_qa_knowledge_lifecycle_state"),
        sa.CheckConstraint(
            "lifecycle_state = 'active' OR NOT retrieval_enabled",
            name="ck_qa_knowledge_retrieval_lifecycle",
        ),
        sa.CheckConstraint(
            "review_status = 'approved' OR NOT retrieval_enabled",
            name="ck_qa_knowledge_review_retrieval",
        ),
        sa.CheckConstraint("origin IN ('manual', 'automatic')", name="ck_qa_knowledge_origin"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_qa_knowledge_scope_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "source_document_id"],
            ["documents.tenant_id", "documents.dataset_id", "documents.id"],
            name="fk_qa_knowledge_scope_document",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "dataset_id", "id", name="uq_qa_knowledge_scope_id"),
    )
    for name, columns in (
        ("ix_qa_knowledge_tenant_id", ["tenant_id"]),
        ("ix_qa_knowledge_dataset_id", ["dataset_id"]),
        ("ix_qa_knowledge_review_status", ["review_status"]),
        ("ix_qa_knowledge_lifecycle_state", ["lifecycle_state"]),
        ("ix_qa_knowledge_retrieval_enabled", ["retrieval_enabled"]),
        ("ix_qa_knowledge_expires_at", ["expires_at"]),
        ("ix_qa_knowledge_source_document_id", ["source_document_id"]),
        (
            "ix_qa_knowledge_scope_effective",
            [
                "tenant_id",
                "dataset_id",
                "lifecycle_state",
                "retrieval_enabled",
                "effective_from",
                "expires_at",
            ],
        ),
        (
            "ix_qa_knowledge_scope_review",
            ["tenant_id", "dataset_id", "review_status"],
        ),
    ):
        op.create_index(name, "qa_knowledge", columns, unique=False)

    op.create_table(
        "qa_alternative_questions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("qa_id", sa.String(length=64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("normalized_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", _datetime6(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_qa_alternatives_scope_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "qa_id"],
            ["qa_knowledge.tenant_id", "qa_knowledge.dataset_id", "qa_knowledge.id"],
            name="fk_qa_alternatives_scope_qa",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "qa_id",
            "normalized_hash",
            name="uq_qa_alternatives_qa_normalized_hash",
        ),
    )
    for name, columns in (
        ("ix_qa_alternative_questions_tenant_id", ["tenant_id"]),
        ("ix_qa_alternative_questions_dataset_id", ["dataset_id"]),
        ("ix_qa_alternative_questions_qa_id", ["qa_id"]),
        (
            "ix_qa_alternatives_scope_qa",
            ["tenant_id", "dataset_id", "qa_id"],
        ),
    ):
        op.create_index(name, "qa_alternative_questions", columns, unique=False)


def downgrade() -> None:
    op.drop_table("qa_alternative_questions")
    op.drop_table("qa_knowledge")

    with op.batch_alter_table("documents") as batch:
        batch.drop_constraint("fk_documents_scope_current_version", type_="foreignkey")

    op.drop_table("document_versions")

    with op.batch_alter_table("documents") as batch:
        batch.drop_index("ix_documents_scope_effective")
        batch.drop_index("ix_documents_purge_after")
        batch.drop_index("ix_documents_expires_at")
        batch.drop_index("ix_documents_retrieval_enabled")
        batch.drop_index("ix_documents_lifecycle_state")
        batch.drop_index("ix_documents_current_version_id")
        batch.drop_constraint("ck_documents_retrieval_lifecycle", type_="check")
        batch.drop_constraint("ck_documents_lifecycle_state", type_="check")
        batch.drop_column("purge_after")
        batch.drop_column("expires_at")
        batch.drop_column("effective_from")
        batch.drop_column("retrieval_enabled")
        batch.drop_column("lifecycle_state")
        batch.drop_column("current_version_id")
