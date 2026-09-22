"""Revocation kinds: one declaration per identity resource that can be withdrawn.

``core/enterprise_identity_control.py`` used to answer "what does revoking this kind of
identity resource mean" with eight ``kind == "domain_revoke"`` ternaries inside
``_simple_state_mutation``: which operation to reserve, which resource type and table to
lock, which column to release, how to project the row into the response, and which audit
action to write. Everything else — request hash, idempotent replay, ``SELECT ... FOR
UPDATE``, the revision CAS, the audit envelope — is shared ceremony.

Keeping the ceremony shared is the point, so the declaration is a value, not a subclass:
a new kind contributes only its names, its projector, and the columns it releases.

The fence the table may not cross
    A revocation writes five columns that *are* the revoke: ``status``, ``revision``,
    ``revoked_at``, ``revoked_by``, plus ``updated_at``. A declaration that returned any of
    those from ``release_values`` would silently undo the revoke it is part of (or break the
    CAS that makes it safe), so the overlap is refused twice: ``_validate_spec`` at
    registration, and the ceremony again right before the ``UPDATE``. The second check is not
    redundant — registration only gets to call ``release_values`` once, with a synthetic
    actor, so a declaration that branches on its argument or on how often it has been called
    passes registration and then clobbers the revoke at runtime. Before this table the same
    hazard existed but was invisible: the two kinds hard-coded ``updated_by`` /
    ``active_name_key`` in an ``if/else`` that no new kind could extend wrongly without being
    noticed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

__all__ = [
    "RevocationKindSpec",
    "FENCE_COLUMNS",
    "SCOPE_COLUMNS",
    "PROTECTED_COLUMNS",
    "register_revocation_kind",
    "unregister_revocation_kind",
    "resolve_revocation_kind",
    "revocation_kind_names",
]

#: Columns the shared ceremony owns. A kind may not write them.
FENCE_COLUMNS = frozenset(
    {"status", "revision", "revoked_at", "revoked_by", "updated_at"}
)

#: Columns that say *which row and whose row* this is. Outside both sets a released key
#: reaches the UPDATE unfiltered, so a declaration that returned ``{"tenant_id": ...}``
#: would move a document between tenants while the audit row still stamped the requesting
#: tenant — the trail would say something false. The five revoke-columns are not enough.
SCOPE_COLUMNS = frozenset({"id", "tenant_id", "created_at"})

#: Everything the ceremony refuses to take from a declaration.
PROTECTED_COLUMNS = FENCE_COLUMNS | SCOPE_COLUMNS


@dataclass(frozen=True)
class RevocationKindSpec:
    """Everything that differs between two kinds of revocation."""

    kind: str
    operation: str
    resource_type: str
    table_name: str
    payload_key: str
    audit_action: str
    project: Callable[[Mapping[str, Any]], dict[str, Any]]
    release_values: Callable[[str], Mapping[str, Any]] = field(
        default=lambda actor_id: {}
    )


_BY_KIND: dict[str, RevocationKindSpec] = {}


def _require_text(value: Any, name: str, kind: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{kind}: {name} must be a non-empty string")
    return value


def _validate_spec(spec: RevocationKindSpec) -> None:
    if not isinstance(spec, RevocationKindSpec):
        raise TypeError(f"expected RevocationKindSpec, got {type(spec).__name__}")
    kind = _require_text(spec.kind, "kind", "revocation kind")
    if kind != kind.strip().lower():
        raise ValueError(
            f"{kind}: kind must already be its own canonical key (lowercase, no padding)"
        )
    for name in ("operation", "resource_type", "table_name", "payload_key", "audit_action"):
        _require_text(getattr(spec, name), name, kind)
    if not callable(spec.project):
        raise ValueError(f"{kind}: project must be callable")
    if not callable(spec.release_values):
        raise ValueError(f"{kind}: release_values must be callable")
    probe = spec.release_values("probe-actor")
    if not isinstance(probe, Mapping):
        raise ValueError(f"{kind}: release_values must return a mapping")
    for key in probe:
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"{kind}: release_values keys must be non-empty column names")
        if key in FENCE_COLUMNS:
            raise ValueError(
                f"{kind}: release_values may not write {key!r} — that column is the revoke itself"
            )
        if key in SCOPE_COLUMNS:
            raise ValueError(
                f"{kind}: release_values may not write {key!r} — that column says which row this"
                " is and whose it is; moving it would leave the audit trail false"
            )


def register_revocation_kind(spec: RevocationKindSpec, *, replace: bool = False) -> None:
    """Declare one revocable identity resource."""
    _validate_spec(spec)
    if spec.kind in _BY_KIND and not replace:
        raise ValueError(f"revocation kind already registered: {spec.kind}")
    _BY_KIND[spec.kind] = spec


def unregister_revocation_kind(kind: str) -> None:
    """Withdraw a declaration; unknown kinds are ignored (idempotent teardown)."""
    _BY_KIND.pop(str(kind).strip().lower(), None)


def resolve_revocation_kind(kind: object) -> RevocationKindSpec | None:
    """Look one kind up by exact spelling. ``None`` means *nothing is known about it*, and
    callers must refuse rather than pick a default — the old ternary chain did the opposite
    and treated any unexpected value as a SCIM token revoke."""
    if not isinstance(kind, str):
        return None
    return _BY_KIND.get(kind)


def revocation_kind_names() -> tuple[str, ...]:
    return tuple(sorted(_BY_KIND))
