"""Add authoritative knowledge governance folders, tags and audit facts.

Revision ID: 0008_governance
Revises: 0007_chunk_rev
Create Date: 2026-08-24
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "0008_governance"
down_revision: str | None = "0007_chunk_rev"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _datetime6():
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


def upgrade() -> None:
    with op.batch_alter_table("datasets") as batch:
        batch.create_unique_constraint("uq_datasets_tenant_id", ["tenant_id", "id"])

    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("folder_id", sa.String(length=64), nullable=True))
        batch.create_unique_constraint(
            "uq_documents_scope_id", ["tenant_id", "dataset_id", "id"]
        )
        batch.create_index("ix_documents_folder_id", ["folder_id"], unique=False)

    op.create_table(
        "knowledge_folders",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("parent_id", sa.String(length=64), nullable=True),
        sa.Column("parent_key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("normalized_name", sa.String(length=256), nullable=False),
        sa.Column("path", sa.String(length=2048), nullable=False),
        sa.Column("path_hash", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "parent_key = COALESCE(parent_id, '')",
            name="ck_knowledge_folders_parent_key",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_knowledge_folders_scope_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "parent_id"],
            [
                "knowledge_folders.tenant_id",
                "knowledge_folders.dataset_id",
                "knowledge_folders.id",
            ],
            name="fk_knowledge_folders_scope_parent",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "dataset_id", "id", name="uq_knowledge_folders_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "parent_key",
            "normalized_name",
            name="uq_knowledge_folders_scope_sibling_name",
        ),
    )
    for name, columns in (
        ("ix_knowledge_folders_tenant_id", ["tenant_id"]),
        ("ix_knowledge_folders_dataset_id", ["dataset_id"]),
        ("ix_knowledge_folders_parent_id", ["parent_id"]),
        ("ix_knowledge_folders_status", ["status"]),
        (
            "ix_knowledge_folders_scope_parent_sort",
            ["tenant_id", "dataset_id", "parent_key", "sort_order"],
        ),
        (
            "ix_knowledge_folders_scope_path_hash",
            ["tenant_id", "dataset_id", "path_hash"],
        ),
    ):
        op.create_index(name, "knowledge_folders", columns, unique=False)

    with op.batch_alter_table("documents") as batch:
        batch.create_foreign_key(
            "fk_documents_scope_folder",
            "knowledge_folders",
            ["tenant_id", "dataset_id", "folder_id"],
            ["tenant_id", "dataset_id", "id"],
        )

    op.create_table(
        "knowledge_tags",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("normalized_name", sa.String(length=256), nullable=False),
        sa.Column("color", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_knowledge_tags_scope_dataset",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "dataset_id", "id", name="uq_knowledge_tags_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "normalized_name",
            name="uq_knowledge_tags_scope_name",
        ),
    )
    for name, columns in (
        ("ix_knowledge_tags_tenant_id", ["tenant_id"]),
        ("ix_knowledge_tags_dataset_id", ["dataset_id"]),
        (
            "ix_knowledge_tags_scope_name",
            ["tenant_id", "dataset_id", "normalized_name"],
        ),
    ):
        op.create_index(name, "knowledge_tags", columns, unique=False)

    op.create_table(
        "document_tags",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("document_id", sa.String(length=64), nullable=False),
        sa.Column("tag_id", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_document_tags_scope_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "document_id"],
            ["documents.tenant_id", "documents.dataset_id", "documents.id"],
            name="fk_document_tags_scope_document",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "tag_id"],
            ["knowledge_tags.tenant_id", "knowledge_tags.dataset_id", "knowledge_tags.id"],
            name="fk_document_tags_scope_tag",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "tag_id", name="uq_document_tags_document_tag"),
    )
    for name, columns in (
        ("ix_document_tags_tenant_id", ["tenant_id"]),
        ("ix_document_tags_dataset_id", ["dataset_id"]),
        ("ix_document_tags_document_id", ["document_id"]),
        ("ix_document_tags_tag_id", ["tag_id"]),
        (
            "ix_document_tags_scope_document",
            ["tenant_id", "dataset_id", "document_id"],
        ),
        ("ix_document_tags_scope_tag", ["tenant_id", "dataset_id", "tag_id"]),
    ):
        op.create_index(name, "document_tags", columns, unique=False)

    op.create_table(
        "knowledge_audit_events",
        sa.Column("sequence", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  autoincrement=True, nullable=False),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=512), nullable=False),
        sa.Column("before_snapshot", sa.JSON(), nullable=True),
        sa.Column("after_snapshot", sa.JSON(), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("request_ip", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", _datetime6(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_knowledge_audit_scope_dataset",
        ),
        sa.PrimaryKeyConstraint("sequence"),
        sa.UniqueConstraint("id", name="uq_knowledge_audit_events_id"),
    )
    for name, columns in (
        ("ix_knowledge_audit_events_tenant_id", ["tenant_id"]),
        ("ix_knowledge_audit_events_dataset_id", ["dataset_id"]),
        ("ix_knowledge_audit_events_actor_id", ["actor_id"]),
        ("ix_knowledge_audit_events_action", ["action"]),
        ("ix_knowledge_audit_events_resource_type", ["resource_type"]),
        ("ix_knowledge_audit_events_resource_id", ["resource_id"]),
        (
            "ix_knowledge_audit_scope_time",
            ["tenant_id", "dataset_id", "occurred_at", "sequence"],
        ),
        (
            "ix_knowledge_audit_resource_time",
            ["resource_type", "resource_id", "occurred_at", "sequence"],
        ),
        ("ix_knowledge_audit_request_id", ["request_id"]),
    ):
        op.create_index(name, "knowledge_audit_events", columns, unique=False)


def downgrade() -> None:
    op.drop_table("knowledge_audit_events")
    op.drop_table("document_tags")
    op.drop_table("knowledge_tags")
    with op.batch_alter_table("documents") as batch:
        batch.drop_constraint("fk_documents_scope_folder", type_="foreignkey")
        batch.drop_index("ix_documents_folder_id")
        batch.drop_constraint("uq_documents_scope_id", type_="unique")
        batch.drop_column("folder_id")
    op.drop_table("knowledge_folders")
    with op.batch_alter_table("datasets") as batch:
        batch.drop_constraint("uq_datasets_tenant_id", type_="unique")
