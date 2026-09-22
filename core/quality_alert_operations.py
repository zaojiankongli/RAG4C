"""Quality-alert operations: one declaration per mutation instead of a branch ladder.

``_mutate_alert`` decided everything about an operation inline: which states it may start
from, which columns to stamp, which columns to release, the audit action, and the operator-
visible message. The shared ceremony — revision CAS, ``session.flush()``, source
re-validation, the before/after audit envelope, the idempotent replay — was already correct
and is exactly what must not be re-derived per operation.

Two fences are enforced at registration rather than by convention
    1. A **terminal** target status must release ``active_alert_key``. That column is the
       partial-unique slot holding "one live alert per fingerprint"; a terminal state that
       leaves it set either blocks the next alert for the same fingerprint forever or lets a
       resolved alert keep occupying an active slot.
    2. A **non-terminal** target must not release it. Freeing the slot while the alert is
       still active is how two "live" alerts for one fingerprint appear side by side.

Neither was expressible before: the behaviour lived in an ``elif`` body, so a new operation
could get it wrong silently. Field names are declared explicitly (``stamp_time`` may be
``None``) because the naming is not uniform — suppression has ``suppressed_by`` /
``suppressed_comment`` and a *bound* (``suppressed_until``) but no ``suppressed_at`` — so a
derive-from-status convention would have written attributes that do not exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

__all__ = [
    "QualityAlertOperationSpec",
    "ACTIVE_ALERT_STATUSES",
    "TERMINAL_ALERT_STATUSES",
    "BUILTIN_QUALITY_ALERT_OPERATIONS",
    "register_quality_alert_operation",
    "unregister_quality_alert_operation",
    "resolve_quality_alert_operation",
    "quality_alert_operation_names",
    "apply_operation",
    "configure_alert_model",
    "declared_columns",
]

ACTIVE_ALERT_STATUSES = frozenset({"open", "acknowledged", "suppressed"})
TERMINAL_ALERT_STATUSES = frozenset({"resolved"})
ALL_ALERT_STATUSES = ACTIVE_ALERT_STATUSES | TERMINAL_ALERT_STATUSES

ACTIVE_SLOT_COLUMN = "active_alert_key"


@dataclass(frozen=True)
class QualityAlertOperationSpec:
    """One legal alert mutation."""

    operation: str
    target_status: str
    allowed_from: frozenset[str]
    audit_action: str
    response_message: str
    conflict_message: str
    stamp_actor: str
    stamp_comment: str
    stamp_time: str | None = None
    sets_bound: str | None = None
    clears: frozenset[str] = frozenset()
    terminal_conflict_message: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.target_status in TERMINAL_ALERT_STATUSES


BUILTIN_QUALITY_ALERT_OPERATIONS: tuple[QualityAlertOperationSpec, ...] = (
    QualityAlertOperationSpec(
        operation="acknowledge_quality_alert",
        target_status="acknowledged",
        allowed_from=frozenset({"open"}),
        audit_action="knowledge_base.release_quality.alert_acknowledged",
        response_message="Quality alert acknowledged",
        conflict_message="only open quality alerts can be acknowledged",
        stamp_actor="acknowledged_by",
        stamp_comment="acknowledged_comment",
        stamp_time="acknowledged_at",
    ),
    QualityAlertOperationSpec(
        operation="suppress_quality_alert",
        target_status="suppressed",
        allowed_from=frozenset({"open", "acknowledged"}),
        audit_action="knowledge_base.release_quality.alert_suppressed",
        response_message="Quality alert suppressed",
        conflict_message="only open or acknowledged quality alerts can be suppressed",
        stamp_actor="suppressed_by",
        stamp_comment="suppressed_comment",
        sets_bound="suppressed_until",
    ),
    QualityAlertOperationSpec(
        operation="resolve_quality_alert",
        target_status="resolved",
        allowed_from=ACTIVE_ALERT_STATUSES,
        audit_action="knowledge_base.release_quality.alert_resolved",
        response_message="Quality alert resolved",
        conflict_message="quality alert cannot be resolved from its current state",
        terminal_conflict_message="resolved quality alerts are terminal",
        stamp_actor="resolved_by",
        stamp_comment="resolved_comment",
        stamp_time="resolved_at",
        clears=frozenset(
            {ACTIVE_SLOT_COLUMN, "suppressed_until", "suppressed_by", "suppressed_comment"}
        ),
    ),
)


def _text(value: Any, name: str, operation: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{operation}: {name} must be a non-empty string")
    return value


def _validate(spec: QualityAlertOperationSpec) -> None:
    if not isinstance(spec, QualityAlertOperationSpec):
        raise TypeError(
            f"expected QualityAlertOperationSpec, got {type(spec).__name__}"
        )
    operation = _text(spec.operation, "operation", "alert operation")
    if operation != operation.strip().lower():
        raise ValueError(
            f"{operation}: operation must already be its own canonical key"
        )
    _text(spec.audit_action, "audit_action", operation)
    _text(spec.response_message, "response_message", operation)
    _text(spec.conflict_message, "conflict_message", operation)
    _text(spec.stamp_actor, "stamp_actor", operation)
    _text(spec.stamp_comment, "stamp_comment", operation)
    if spec.target_status not in ALL_ALERT_STATUSES:
        raise ValueError(
            f"{operation}: target_status must be one of {sorted(ALL_ALERT_STATUSES)}"
        )
    if not spec.allowed_from or not spec.allowed_from <= ALL_ALERT_STATUSES:
        raise ValueError(f"{operation}: allowed_from must be a non-empty subset of known statuses")
    if spec.target_status in spec.allowed_from:
        raise ValueError(
            f"{operation}: target_status must not also be an allowed starting status "
            "(that would let an alert re-stamp itself without changing)"
        )
    released = ACTIVE_SLOT_COLUMN in spec.clears
    if spec.is_terminal and not released:
        raise ValueError(
            f"{operation}: a terminal target must release {ACTIVE_SLOT_COLUMN!r}, otherwise a "
            "resolved alert keeps occupying the one-live-alert-per-fingerprint slot"
        )
    if not spec.is_terminal and released:
        raise ValueError(
            f"{operation}: only a terminal target may release {ACTIVE_SLOT_COLUMN!r}; freeing "
            "the slot while the alert is still active allows two live alerts per fingerprint"
        )
    if spec.sets_bound is not None:
        _text(spec.sets_bound, "sets_bound", operation)
    if spec.stamp_time is not None:
        _text(spec.stamp_time, "stamp_time", operation)
    # A column the model does not have would be set as a plain Python attribute and lost on
    # flush — i.e. the operation would look like it worked. Checked once the model is known.
    if _COLUMN_EXISTS is not None:
        missing = sorted(c for c in declared_columns(spec) if not _COLUMN_EXISTS(c))
        if missing:
            raise ValueError(
                f"{operation}: declares columns the alert model does not have: {', '.join(missing)}"
            )


_BY_OPERATION: dict[str, QualityAlertOperationSpec] = {}
_COLUMN_EXISTS: "Callable[[str], bool] | None" = None


def declared_columns(spec: QualityAlertOperationSpec) -> frozenset[str]:
    """Every column this operation writes or releases (besides the ceremony's own ``status``,
    ``revision`` and ``updated_at``)."""
    return frozenset(
        name
        for name in (
            spec.stamp_actor,
            spec.stamp_comment,
            spec.stamp_time,
            spec.sets_bound,
            *sorted(spec.clears),
        )
        if name
    )


def configure_alert_model(column_exists: "Callable[[str], bool]") -> None:
    """Tell the registry which columns really exist, and re-check every declaration.

    The host calls this once at import. Doing it here rather than importing ``models.orm``
    keeps the registry free of the ORM, and re-validating the builtins means a column rename
    fails at import instead of on the first alert an operator touches.
    """
    global _COLUMN_EXISTS
    _COLUMN_EXISTS = column_exists
    for spec in tuple(_BY_OPERATION.values()):
        missing = sorted(c for c in declared_columns(spec) if not column_exists(c))
        if missing:
            raise ValueError(
                f"{spec.operation}: declares columns the alert model does not have: "
                + ", ".join(missing)
            )


def register_quality_alert_operation(
    spec: QualityAlertOperationSpec, *, replace: bool = False
) -> None:
    """Declare one legal alert mutation."""
    _validate(spec)
    if spec.operation in _BY_OPERATION and not replace:
        raise ValueError(f"quality alert operation already registered: {spec.operation}")
    _BY_OPERATION[spec.operation] = spec


def unregister_quality_alert_operation(operation: str) -> None:
    """Withdraw a declaration; unknown names are ignored (idempotent teardown)."""
    _BY_OPERATION.pop(str(operation).strip().lower(), None)


def resolve_quality_alert_operation(operation: object) -> QualityAlertOperationSpec | None:
    """Look one operation up by exact spelling. ``None`` means "not an operation", and the
    caller must reject it — the ladder this replaces ended in an explicit refusal, and this
    keeps that property without needing a new ``elif`` for every addition."""
    if not isinstance(operation, str):
        return None
    return _BY_OPERATION.get(operation)


def quality_alert_operation_names() -> tuple[str, ...]:
    return tuple(sorted(_BY_OPERATION))


def apply_operation(
    spec: QualityAlertOperationSpec,
    alert: Any,
    *,
    actor: str,
    timestamp: Any,
    comment: str | None,
    bound: Any = None,
) -> None:
    """Write the declared columns onto one alert row.

    The ceremony owns ``revision`` and ``updated_at``; this function deliberately cannot
    touch them, which is what keeps "the row was mutated" a fact produced in exactly one
    place.
    """
    for column in sorted(spec.clears):
        setattr(alert, column, None)
    alert.status = spec.target_status
    if spec.stamp_time is not None:
        setattr(alert, spec.stamp_time, timestamp)
    setattr(alert, spec.stamp_actor, actor)
    setattr(alert, spec.stamp_comment, comment)
    if spec.sets_bound is not None:
        setattr(alert, spec.sets_bound, bound)


def operation_projection_fields() -> Mapping[str, tuple[str, ...]]:
    """Declared column names per operation, for guards and diagnostics."""
    return {
        spec.operation: tuple(sorted(declared_columns(spec)))
        for spec in _BY_OPERATION.values()
    }


for _spec in BUILTIN_QUALITY_ALERT_OPERATIONS:
    register_quality_alert_operation(_spec)
