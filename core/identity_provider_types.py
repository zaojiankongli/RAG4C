"""Identity provider field shapes: one type, one declaration.

The two built-in projectors are behavior, not rows of data. HTTPS checks, scope
sorting, and optional metadata stay in the projector. The host only looks the
type up. A type that is not registered is not a default: the host raises the
same validation error it raised when the two branches were inline.

Registering a type does not widen ``ck_tenant_identity_providers_type`` or the
field-combination CHECK. A type registered only in this table can be projected
in process and still cannot be stored. ``builtin_identity_provider_types`` is
the sealed import-time snapshot, so a test registration does not move the
schema probe or the CHECK reconciliation.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

_TYPE_RE = re.compile(r"[a-z][a-z0-9_]{0,15}")

STORAGE_COLUMNS = (
    "issuer_url",
    "client_id",
    "secret_ref",
    "scopes",
    "entity_id",
    "sso_url",
    "metadata_url",
    "certificate_fingerprint",
)


@dataclass(frozen=True)
class IdentityProviderTypeSpec:
    provider_type: str
    required_not_null: tuple[str, ...]
    project: Callable[[Mapping[str, Any], Any], dict[str, Any]]


_BY_TYPE: dict[str, IdentityProviderTypeSpec] = {}
_BUILTINS: tuple[IdentityProviderTypeSpec, ...] = ()


def _validate(spec: IdentityProviderTypeSpec) -> None:
    if not isinstance(spec, IdentityProviderTypeSpec):
        raise TypeError(f"expected IdentityProviderTypeSpec, got {type(spec).__name__}")
    if not isinstance(spec.provider_type, str) or _TYPE_RE.fullmatch(spec.provider_type) is None:
        raise ValueError(
            f"{spec.provider_type!r}: provider_type must be a lowercase code of at most 16 characters"
        )
    if not isinstance(spec.required_not_null, tuple) or not spec.required_not_null:
        raise ValueError(f"{spec.provider_type}: required_not_null must be a non-empty tuple")
    if len(set(spec.required_not_null)) != len(spec.required_not_null):
        raise ValueError(f"{spec.provider_type}: required_not_null has duplicate columns")
    unknown = [column for column in spec.required_not_null if column not in STORAGE_COLUMNS]
    if unknown:
        raise ValueError(
            f"{spec.provider_type}: required_not_null has columns that are not stored: {unknown}"
        )
    if not callable(spec.project):
        raise ValueError(f"{spec.provider_type}: project must be callable")


def register_identity_provider_type(
    spec: IdentityProviderTypeSpec, *, replace: bool = False
) -> None:
    _validate(spec)
    if spec.provider_type in _BY_TYPE and not replace:
        raise ValueError(f"identity provider type already registered: {spec.provider_type}")
    _BY_TYPE[spec.provider_type] = spec


def unregister_identity_provider_type(provider_type: str) -> None:
    _BY_TYPE.pop(str(provider_type).strip().lower(), None)


def seal_builtin_identity_provider_types() -> None:
    """Snapshot whatever is registered at import. Later registrations stay out of it."""
    global _BUILTINS
    _BUILTINS = tuple(_BY_TYPE[name] for name in sorted(_BY_TYPE))


def builtin_identity_provider_types() -> tuple[IdentityProviderTypeSpec, ...]:
    return _BUILTINS


def identity_provider_type(provider_type: object) -> IdentityProviderTypeSpec | None:
    if not isinstance(provider_type, str):
        return None
    return _BY_TYPE.get(provider_type)
