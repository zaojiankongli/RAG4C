"""QA/FAQ ops: content_hash, import batch id, origin=import, negative questions.

Revision ID: 0037_qa_faq_ops
Revises: 0037_enterprise_knowledge_operations_feedback
Create Date: 2026-09-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037_qa_faq_ops"
down_revision: str | None = "0037_enterprise_knowledge_operations_feedback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SUPPORTED_DIALECTS = frozenset({"sqlite", "mysql", "mariadb", "postgresql"})


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name if bind is not None else ""
    if dialect and dialect not in SUPPORTED_DIALECTS:
        raise RuntimeError(f"unsupported dialect for {revision}: {dialect}")

    with op.batch_alter_table("qa_knowledge") as batch:
        batch.add_column(sa.Column("content_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("import_batch_id", sa.String(64), nullable=True))
        batch.drop_constraint("ck_qa_knowledge_origin", type_="check")
        batch.create_check_constraint(
            "ck_qa_knowledge_origin",
            "origin IN ('manual', 'automatic', 'import')",
        )
    op.create_index(
        "ix_qa_knowledge_scope_content_hash",
        "qa_knowledge",
        ["tenant_id", "dataset_id", "content_hash"],
    )

    op.create_table(
        "qa_negative_questions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("dataset_id", sa.String(64), nullable=False),
        sa.Column("qa_id", sa.String(64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("normalized_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_qa_negative_questions_scope_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "qa_id"],
            ["qa_knowledge.tenant_id", "qa_knowledge.dataset_id", "qa_knowledge.id"],
            name="fk_qa_negative_questions_scope_qa",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "qa_id",
            "normalized_hash",
            name="uq_qa_negative_questions_qa_normalized_hash",
        ),
    )
    op.create_index(
        "ix_qa_negative_questions_scope_qa",
        "qa_negative_questions",
        ["tenant_id", "dataset_id", "qa_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_qa_negative_questions_scope_qa", table_name="qa_negative_questions")
    op.drop_table("qa_negative_questions")
    op.drop_index("ix_qa_knowledge_scope_content_hash", table_name="qa_knowledge")
    with op.batch_alter_table("qa_knowledge") as batch:
        batch.drop_column("import_batch_id")
        batch.drop_column("content_hash")
