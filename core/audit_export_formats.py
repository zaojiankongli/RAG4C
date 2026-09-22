"""Audit export formats: one declaration per wire format instead of four ternaries.

Adding a format used to mean editing four places that had to agree: the accepted-name check
(and its user-visible message), the serializer's ``if/else``, the filename extension, and the
download response's media type. Two of those could disagree silently:

* ``_serialize_export`` ended in an ``else`` that produced **CSV**, so an unvalidated format
  name would get CSV bytes while the job row claimed something else.
* The download path re-derived media type and extension from the *stored* row with two more
  ``if format_name == "ndjson"`` ternaries, so a row whose format was no longer known would be
  served as CSV with a CSV name — a mislabelled artefact, not an error.

Both are refusals now. The declarations themselves live in
``core/enterprise_compliance.py`` because the serializers need the redaction helpers
(``_csv_safe`` / ``_bounded_snapshot``) and the export field list; keeping them there means
there is still exactly one place that knows how an audit row becomes bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

__all__ = [
    "AuditExportFormatSpec",
    "register_audit_export_format",
    "unregister_audit_export_format",
    "resolve_audit_export_format",
    "audit_export_format_names",
]


@dataclass(frozen=True)
class AuditExportFormatSpec:
    """How one wire format is named, described, and produced."""

    format: str
    extension: str
    media_type: str
    serialize: Callable[[Sequence[Mapping[str, Any]]], bytes]


_BY_FORMAT: dict[str, AuditExportFormatSpec] = {}


def _validate(spec: AuditExportFormatSpec) -> None:
    if not isinstance(spec, AuditExportFormatSpec):
        raise TypeError(f"expected AuditExportFormatSpec, got {type(spec).__name__}")
    name = spec.format
    if not isinstance(name, str) or not name.strip():
        raise ValueError("AuditExportFormatSpec.format must be a non-empty string")
    if name != name.strip().lower():
        raise ValueError(
            f"AuditExportFormatSpec.format must already be its own canonical key: {name!r}"
        )
    for attr in ("extension", "media_type"):
        value = getattr(spec, attr)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name}: {attr} must be a non-empty string")
    if "/" in spec.extension or "\\" in spec.extension:
        raise ValueError(f"{name}: extension must be a bare suffix, not a path fragment")
    if not callable(spec.serialize):
        raise ValueError(f"{name}: serialize must be callable")


def register_audit_export_format(spec: AuditExportFormatSpec, *, replace: bool = False) -> None:
    """Declare one export format."""
    _validate(spec)
    if spec.format in _BY_FORMAT and not replace:
        raise ValueError(f"audit export format already registered: {spec.format}")
    _BY_FORMAT[spec.format] = spec


def unregister_audit_export_format(format_name: str) -> None:
    """Withdraw a declaration; unknown names are ignored (idempotent teardown)."""
    _BY_FORMAT.pop(str(format_name).strip().lower(), None)


def resolve_audit_export_format(format_name: object) -> AuditExportFormatSpec | None:
    """Look one format up by exact spelling. ``None`` means "we do not know how to produce or
    label this", and every caller must refuse rather than fall through to a default format."""
    if not isinstance(format_name, str):
        return None
    return _BY_FORMAT.get(format_name)


def audit_export_format_names() -> tuple[str, ...]:
    return tuple(sorted(_BY_FORMAT))
