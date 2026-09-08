"""Read-only integrity audits for catalog identities."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, text


class CatalogIntegrityError(RuntimeError):
    """Raised when catalog data must be repaired before migration."""


@dataclass(frozen=True)
class DuplicateGroup:
    row_ids: tuple[int | str, ...]


@dataclass(frozen=True)
class CatalogDuplicateReport:
    groups: dict[str, tuple[DuplicateGroup, ...]]

    @property
    def unresolved_groups(self) -> int:
        return sum(len(items) for items in self.groups.values())

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "unresolved_groups": self.unresolved_groups,
            "tables": {
                table: [{"row_ids": list(group.row_ids)} for group in groups]
                for table, groups in self.groups.items()
            },
        }


def _quoted(dialect: Any, identifier: str) -> str:
    return dialect.identifier_preparer.quote(identifier)


def build_duplicate_group_query(
    dialect: Any, table: str, identity_columns: tuple[str, ...]
) -> str:
    quoted_table = _quoted(dialect, table)
    columns = ", ".join(_quoted(dialect, column) for column in identity_columns)
    return (
        f"SELECT {columns} FROM {quoted_table} "
        f"GROUP BY {columns} HAVING COUNT(*) > 1"
    )


def _build_duplicate_ids_query(
    dialect: Any,
    table: str,
    id_column: str,
    identity_columns: tuple[str, ...],
) -> str:
    quoted_table = _quoted(dialect, table)
    quoted_id = _quoted(dialect, id_column)
    predicates = " AND ".join(
        f"{_quoted(dialect, column)} = :value_{index}"
        for index, column in enumerate(identity_columns)
    )
    return (
        f"SELECT {quoted_id} FROM {quoted_table} "
        f"WHERE {predicates} ORDER BY {quoted_id}"
    )


_SPECS = (
    ("tenant_members", "id", ("account_id", "tenant_id")),
    ("document_segments", "id", ("document_id", "seq")),
    ("metadata_fields", "id", ("dataset_id", "key")),
)


def audit_catalog_duplicates(engine: Engine) -> CatalogDuplicateReport:
    """Return duplicate row IDs without exposing the duplicated identity values."""
    report: dict[str, tuple[DuplicateGroup, ...]] = {}
    with engine.connect() as connection:
        for table, id_column, identity_columns in _SPECS:
            duplicate_keys = connection.execute(
                text(build_duplicate_group_query(engine.dialect, table, identity_columns))
            ).all()
            groups: list[DuplicateGroup] = []
            for key in duplicate_keys:
                params = {f"value_{index}": value for index, value in enumerate(key)}
                ids = connection.execute(
                    text(
                        _build_duplicate_ids_query(
                            engine.dialect, table, id_column, identity_columns
                        )
                    ),
                    params,
                ).scalars()
                groups.append(DuplicateGroup(tuple(ids)))
            report[table] = tuple(groups)
    return CatalogDuplicateReport(report)


def ensure_no_catalog_duplicates(engine: Engine) -> CatalogDuplicateReport:
    report = audit_catalog_duplicates(engine)
    if report.unresolved_groups:
        raise CatalogIntegrityError(
            f"catalog has {report.unresolved_groups} duplicate identity groups"
        )
    return report
