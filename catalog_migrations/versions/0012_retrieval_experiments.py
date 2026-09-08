"""Add retrieval experiment and reviewer judgment authority.

Revision ID: 0012_retrieval_experiments
Revises: 0011_durable_delete
Create Date: 2026-08-25
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "0012_retrieval_experiments"
down_revision: str | None = "0011_durable_delete"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _datetime6():
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")



def _dialect_name() -> str:
    return str(op.get_context().dialect.name).casefold()


def _create_immutable_guards() -> None:
    dialect = _dialect_name()
    if dialect == "sqlite":
        op.execute(
            "CREATE TRIGGER trg_retrieval_experiments_no_update "
            "BEFORE UPDATE ON retrieval_experiments BEGIN "
            "SELECT RAISE(ABORT, 'retrieval_experiments are immutable'); END"
        )
        op.execute(
            "CREATE TRIGGER trg_retrieval_experiments_no_delete "
            "BEFORE DELETE ON retrieval_experiments BEGIN "
            "SELECT RAISE(ABORT, 'retrieval_experiments are immutable'); END"
        )
    elif dialect in {"mysql", "mariadb"}:
        op.execute(
            "CREATE TRIGGER trg_retrieval_experiments_no_update "
            "BEFORE UPDATE ON retrieval_experiments FOR EACH ROW "
            "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
            "'retrieval_experiments are immutable'"
        )
        op.execute(
            "CREATE TRIGGER trg_retrieval_experiments_no_delete "
            "BEFORE DELETE ON retrieval_experiments FOR EACH ROW "
            "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
            "'retrieval_experiments are immutable'"
        )
    elif dialect == "postgresql":
        op.execute(
            "CREATE FUNCTION rag4c_retrieval_experiments_immutable() "
            "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            "RAISE EXCEPTION 'retrieval_experiments are immutable'; END; $$"
        )
        for action in ("UPDATE", "DELETE"):
            suffix = action.casefold()
            op.execute(
                f"CREATE TRIGGER trg_retrieval_experiments_no_{suffix} "
                f"BEFORE {action} ON retrieval_experiments FOR EACH ROW "
                "EXECUTE FUNCTION rag4c_retrieval_experiments_immutable()"
            )


def _drop_immutable_guards() -> None:
    dialect = _dialect_name()
    if dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_retrieval_experiments_no_update "
            "ON retrieval_experiments"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_retrieval_experiments_no_delete "
            "ON retrieval_experiments"
        )
        op.execute("DROP FUNCTION IF EXISTS rag4c_retrieval_experiments_immutable()")
    elif dialect in {"sqlite", "mysql", "mariadb"}:
        op.execute("DROP TRIGGER IF EXISTS trg_retrieval_experiments_no_update")
        op.execute("DROP TRIGGER IF EXISTS trg_retrieval_experiments_no_delete")

def upgrade() -> None:
    with op.batch_alter_table("chunk_heads") as batch:
        batch.create_unique_constraint(
            "uq_chunk_heads_scope_id", ["tenant_id", "dataset_id", "id"]
        )

    op.create_table(
        "retrieval_experiments",
        sa.Column(
            "sequence",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("strategy_snapshot", sa.JSON(), nullable=False),
        sa.Column("result_snapshot", sa.JSON(), nullable=False),
        sa.Column("evidence_lineage", sa.JSON(), nullable=False),
        sa.Column("latency_ms", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", _datetime6(), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "status IN ('completed', 'failed')", name="ck_retrieval_experiments_status"
        ),
        sa.CheckConstraint(
            "latency_ms >= 0", name="ck_retrieval_experiments_latency_nonnegative"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_retrieval_experiments_scope_dataset",
        ),
        sa.PrimaryKeyConstraint("sequence"),
        sa.UniqueConstraint("id", name="uq_retrieval_experiments_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "id",
            name="uq_retrieval_experiments_scope_id",
        ),
    )
    for name, columns in (
        ("ix_retrieval_experiments_tenant_id", ["tenant_id"]),
        ("ix_retrieval_experiments_dataset_id", ["dataset_id"]),
        (
            "ix_retrieval_experiments_scope_sequence",
            ["tenant_id", "dataset_id", "sequence"],
        ),
        (
            "ix_retrieval_experiments_scope_query_hash",
            ["tenant_id", "dataset_id", "query_hash", "sequence"],
        ),
        (
            "ix_retrieval_experiments_scope_status_time",
            ["tenant_id", "dataset_id", "status", "created_at", "sequence"],
        ),
        ("ix_retrieval_experiments_run_id", ["run_id"]),
        (
            "ix_retrieval_experiments_scope_run",
            ["tenant_id", "dataset_id", "run_id", "sequence"],
        ),
    ):
        op.create_index(name, "retrieval_experiments", columns, unique=False)

    _create_immutable_guards()

    op.create_table(
        "retrieval_judgments",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("experiment_id", sa.String(length=64), nullable=False),
        sa.Column("result_rank", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.String(length=64), nullable=True),
        sa.Column("chunk_id", sa.String(length=512), nullable=True),
        sa.Column("relevance_label", sa.String(length=16), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", _datetime6(), nullable=False),
        sa.CheckConstraint(
            "result_rank > 0", name="ck_retrieval_judgments_rank_positive"
        ),
        sa.CheckConstraint(
            "relevance_label IN ('relevant', 'partial', 'irrelevant')",
            name="ck_retrieval_judgments_relevance_label",
        ),
        sa.CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 3)",
            name="ck_retrieval_judgments_score",
        ),
        sa.CheckConstraint(
            "revision > 0", name="ck_retrieval_judgments_revision_positive"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_retrieval_judgments_scope_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "experiment_id"],
            [
                "retrieval_experiments.tenant_id",
                "retrieval_experiments.dataset_id",
                "retrieval_experiments.id",
            ],
            name="fk_retrieval_judgments_scope_experiment",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "document_id"],
            ["documents.tenant_id", "documents.dataset_id", "documents.id"],
            name="fk_retrieval_judgments_scope_document",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "chunk_id"],
            ["chunk_heads.tenant_id", "chunk_heads.dataset_id", "chunk_heads.id"],
            name="fk_retrieval_judgments_scope_chunk",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", name="uq_retrieval_judgments_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "experiment_id",
            "result_rank",
            "created_by",
            name="uq_retrieval_judgments_experiment_rank_judge",
        ),
    )
    for name, columns in (
        ("ix_retrieval_judgments_tenant_id", ["tenant_id"]),
        ("ix_retrieval_judgments_dataset_id", ["dataset_id"]),
        ("ix_retrieval_judgments_experiment_id", ["experiment_id"]),
        (
            "ix_retrieval_judgments_scope_experiment_rank",
            ["tenant_id", "dataset_id", "experiment_id", "result_rank"],
        ),
        (
            "ix_retrieval_judgments_scope_document",
            ["tenant_id", "dataset_id", "document_id"],
        ),
        (
            "ix_retrieval_judgments_scope_chunk",
            ["tenant_id", "dataset_id", "chunk_id"],
        ),
    ):
        op.create_index(name, "retrieval_judgments", columns, unique=False)


def downgrade() -> None:
    _drop_immutable_guards()
    op.drop_table("retrieval_judgments")
    op.drop_table("retrieval_experiments")
    with op.batch_alter_table("chunk_heads") as batch:
        batch.drop_constraint("uq_chunk_heads_scope_id", type_="unique")
