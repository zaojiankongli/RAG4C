"""Per-dialect proof that a read-only catalog engine really is read-only.

Readiness reporting is allowed to say "this deployment's DB is read-only" only when it
can prove it, and the proof is dialect-specific: SQLite is proven by opening the file with
``mode=ro``, PostgreSQL by a session that defaults transactions to read-only, MySQL and
MariaDB by a session-level read-only transaction. Before this table the three facts lived
as a hand-written dict plus two ``if backend == ...`` ladders in
``server/enterprise_readiness_api.py``, so adding a dialect meant editing the file that
decides whether a production deployment is safe to inspect — and forgetting the
fail-closed branch there turns "we cannot prove it" into "we proved it".

One store, one spelling
    Lookups are exact-match against the canonical dialect name (SQLAlchemy's own
    ``dialect.name``, already lowercase). ``register_read_only_dialect`` rejects a
    declaration whose ``dialect`` is not its own key, so a padded or upper-cased spelling
    cannot exist in the table, and an unregistered dialect resolves to ``None`` — which
    every caller must treat as "not provable", never as "provable by default".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

__all__ = [
    "ReadOnlyDialectSpec",
    "BUILTIN_READ_ONLY_DIALECT_SPECS",
    "register_read_only_dialect",
    "unregister_read_only_dialect",
    "resolve_read_only_dialect",
    "read_only_dialect_names",
]


@dataclass(frozen=True)
class ReadOnlyDialectSpec:
    """What one dialect needs in order to open, and to prove, a read-only connection.

    Attributes:
        dialect: SQLAlchemy dialect name, e.g. ``"postgresql"``.
        mechanism: The label a created engine must carry on its proof. It is compared for
            equality, so it doubles as the contract between "how we opened the engine" and
            "what we are willing to claim".
        connect_args: Driver kwargs merged into ``create_engine``. Empty for a dialect
            proven by the URI it was opened with (SQLite), which needs no driver kwargs.
        live_probe_required: Whether opening a connection and reading a session variable is
            part of the proof.
        proof_statements: SELECTs tried in order; the first one the server answers decides.
            Ordered because MySQL renamed this variable — the modern name is listed first
            and the legacy ``tx_read_only`` is the fallback.
        accepted_values: Case-folded readings that mean "yes, read-only".
    """

    dialect: str
    mechanism: str
    connect_args: Mapping[str, Any] = field(default_factory=dict)
    live_probe_required: bool = True
    proof_statements: tuple[str, ...] = ()
    accepted_values: frozenset[str] = frozenset({"on", "true", "1"})


BUILTIN_READ_ONLY_DIALECT_SPECS: tuple[ReadOnlyDialectSpec, ...] = (
    # SQLite is proven by the URI it was opened with (``?mode=ro``), which the caller that
    # rewrites the URL owns; it therefore declares no driver kwargs and no live probe.
    ReadOnlyDialectSpec(
        dialect="sqlite",
        mechanism="sqlite-uri-mode-ro",
        live_probe_required=False,
    ),
    ReadOnlyDialectSpec(
        dialect="postgresql",
        mechanism="postgresql-default-transaction-read-only",
        connect_args={"options": "-c default_transaction_read_only=on"},
        proof_statements=("SHOW transaction_read_only",),
    ),
    ReadOnlyDialectSpec(
        dialect="mysql",
        mechanism="mysql-session-transaction-read-only",
        connect_args={"init_command": "SET SESSION TRANSACTION READ ONLY"},
        proof_statements=(
            "SELECT @@session.transaction_read_only",
            "SELECT @@session.tx_read_only",
        ),
    ),
    ReadOnlyDialectSpec(
        dialect="mariadb",
        mechanism="mysql-session-transaction-read-only",
        connect_args={"init_command": "SET SESSION TRANSACTION READ ONLY"},
        proof_statements=(
            "SELECT @@session.transaction_read_only",
            "SELECT @@session.tx_read_only",
        ),
    ),
)

_BY_DIALECT: dict[str, ReadOnlyDialectSpec] = {}


def _validate_spec(spec: ReadOnlyDialectSpec) -> None:
    if not isinstance(spec, ReadOnlyDialectSpec):
        raise TypeError(f"expected ReadOnlyDialectSpec, got {type(spec).__name__}")
    name = spec.dialect
    if not isinstance(name, str) or not name.strip():
        raise ValueError("ReadOnlyDialectSpec.dialect must be a non-empty string")
    if name != name.strip().lower():
        raise ValueError(
            f"ReadOnlyDialectSpec.dialect must already be its own canonical key: {name!r}"
        )
    if not isinstance(spec.mechanism, str) or not spec.mechanism.strip():
        raise ValueError(f"{name}: mechanism must be a non-empty string")
    if spec.live_probe_required and not spec.proof_statements:
        raise ValueError(
            f"{name}: a dialect that needs a live probe must declare at least one proof statement"
        )
    if not spec.live_probe_required and spec.proof_statements:
        raise ValueError(
            f"{name}: proof_statements without live_probe_required would never be read"
        )
    if not spec.accepted_values:
        raise ValueError(f"{name}: accepted_values must not be empty")
    for key in dict(spec.connect_args):
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"{name}: connect_args keys must be non-empty strings")


def register_read_only_dialect(spec: ReadOnlyDialectSpec, *, replace: bool = False) -> None:
    """Declare one dialect's open-and-prove shape."""
    _validate_spec(spec)
    if spec.dialect in _BY_DIALECT and not replace:
        raise ValueError(f"read-only dialect already registered: {spec.dialect}")
    _BY_DIALECT[spec.dialect] = spec


def unregister_read_only_dialect(dialect: str) -> None:
    """Withdraw a declaration; an unknown dialect is ignored (idempotent teardown)."""
    _BY_DIALECT.pop(str(dialect).strip().lower(), None)


def resolve_read_only_dialect(dialect: object) -> ReadOnlyDialectSpec | None:
    """Look one dialect up by exact spelling. ``None`` means *not provable*, and callers
    must fail closed on it — this module never supplies a default mechanism."""
    if not isinstance(dialect, str):
        return None
    return _BY_DIALECT.get(dialect)


def read_only_dialect_names() -> tuple[str, ...]:
    """Every dialect this deployment can prove read-only-ness for."""
    return tuple(sorted(_BY_DIALECT))


for _spec in BUILTIN_READ_ONLY_DIALECT_SPECS:
    register_read_only_dialect(_spec)
