"""Add enterprise document catalog query indexes.

Revision ID: 0015_document_catalog_indexes
Revises: 0014_source_schedules
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0015_document_catalog_indexes"
down_revision: str | None = "0014_source_schedules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# These are ordinary B-tree indexes for tenant-scoped exact filters and stable
# updated_at/id pagination. They do not accelerate arbitrary contains/full-text
# search; a future full-text/search capability is intentionally out of scope.
# MySQL/MariaDB receives a bounded prefix for the long folder path so the
# composite key stays within common utf8mb4 index-size limits.
DOCUMENT_CATALOG_INDEX_SPECS: tuple[tuple[str, tuple[str, ...], dict[str, object]], ...] = (
    (
        "ix_documents_catalog_scope_updated",
        ("tenant_id", "dataset_id", "updated_at", "id"),
        {},
    ),
    (
        "ix_documents_catalog_scope_status_updated",
        ("tenant_id", "dataset_id", "status", "updated_at", "id"),
        {},
    ),
    (
        "ix_documents_catalog_scope_doc_type_updated",
        ("tenant_id", "dataset_id", "doc_type", "updated_at", "id"),
        {},
    ),
    (
        "ix_documents_catalog_scope_folder_updated",
        ("tenant_id", "dataset_id", "logical_folder_path", "updated_at", "id"),
        {"mysql_length": {"logical_folder_path": 191}},
    ),
)


def upgrade() -> None:
    for name, columns, kwargs in DOCUMENT_CATALOG_INDEX_SPECS:
        op.create_index(name, "documents", list(columns), **kwargs)


def downgrade() -> None:
    for name, _columns, _kwargs in reversed(DOCUMENT_CATALOG_INDEX_SPECS):
        op.drop_index(name, table_name="documents")
