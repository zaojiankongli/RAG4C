"""Privacy-safe answer facts and evidence refs.

Revision ID: 0039_answer_evidence_facts
Revises: 0038_storage_backends
Create Date: 2026-09-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039_answer_evidence_facts"
down_revision: str | None = "0038_storage_backends"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenant_knowledge_answer_facts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("dataset_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("request_id_digest", sa.String(64), nullable=False, server_default=""),
        sa.Column("query_digest", sa.String(64), nullable=False, server_default=""),
        sa.Column("answer_digest", sa.String(64), nullable=False, server_default=""),
        sa.Column("evidence_chain_digest", sa.String(64), nullable=False, server_default=""),
        sa.Column("fact_digest", sa.String(64), nullable=False, server_default=""),
        sa.Column("safe_query_preview", sa.String(160), nullable=True),
        sa.Column("outcome_code", sa.String(24), nullable=False, server_default="answered"),
        sa.Column("route_code", sa.String(32), nullable=False, server_default="rag"),
        sa.Column("citation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "id", name="uq_answer_facts_tenant_id"),
        sa.CheckConstraint(
            "outcome_code IN ('answered','abstained','cancelled','failed','cached')",
            name="ck_answer_facts_outcome",
        ),
        sa.CheckConstraint("citation_count >= 0", name="ck_answer_facts_citation_count"),
        sa.CheckConstraint("evidence_count >= 0", name="ck_answer_facts_evidence_count"),
    )
    op.create_index(
        "ix_answer_facts_scope_observed",
        "tenant_knowledge_answer_facts",
        ["tenant_id", "dataset_id", "observed_at"],
    )
    op.create_index(
        "ix_answer_facts_tenant_run",
        "tenant_knowledge_answer_facts",
        ["tenant_id", "run_id"],
    )

    op.create_table(
        "tenant_knowledge_answer_evidence_refs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("answer_fact_id", sa.String(64), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunk_id", sa.String(64), nullable=True),
        sa.Column("chunk_revision_id", sa.String(64), nullable=True),
        sa.Column("document_id", sa.String(64), nullable=True),
        sa.Column("citation_status", sa.String(32), nullable=False, server_default="ok"),
        sa.Column("evidence_digest", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "id", name="uq_answer_evidence_refs_tenant_id"),
        sa.CheckConstraint("seq >= 0", name="ck_answer_evidence_seq_nonneg"),
    )
    op.create_index(
        "ix_answer_evidence_fact_seq",
        "tenant_knowledge_answer_evidence_refs",
        ["answer_fact_id", "seq"],
    )
    op.create_index(
        "ix_answer_evidence_tenant_fact",
        "tenant_knowledge_answer_evidence_refs",
        ["tenant_id", "answer_fact_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_answer_evidence_tenant_fact", table_name="tenant_knowledge_answer_evidence_refs")
    op.drop_index("ix_answer_evidence_fact_seq", table_name="tenant_knowledge_answer_evidence_refs")
    op.drop_table("tenant_knowledge_answer_evidence_refs")
    op.drop_index("ix_answer_facts_tenant_run", table_name="tenant_knowledge_answer_facts")
    op.drop_index("ix_answer_facts_scope_observed", table_name="tenant_knowledge_answer_facts")
    op.drop_table("tenant_knowledge_answer_facts")
