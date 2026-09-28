"""Fail-closed preflight for projection targets before durable work is emitted."""

from __future__ import annotations

import re

from core.document_delete_targets import (
    DocumentDeleteTargetPolicy,
    _document_delete_projection_targets_snapshot,
    _register_document_delete_target,
    _unregister_document_delete_target,
)
from core.projection_attempt_lifecycle import PROJECTION_ATTEMPT_LIFECYCLE_POLICIES
from core.projection_revision_strategies import PROJECTION_REVISION_STRATEGIES
from core.projection_target_contract import unsupported_target_requeue_operations
from indexing.projection_handlers import PROJECTION_OPERATION_STRATEGIES
from core.projection_target_producers import (
    unregister_projection_target_policy,
)

_STORE_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_REQUIRED_PROJECTION_OPERATIONS = (
    "upsert",
    "reconcile",
    "delete",
    "delete_document",
)


class IncompleteProjectionTargetRuntime(RuntimeError):
    """A candidate target is missing one or more mandatory runtime boundaries."""


def validate_projection_target_runtime(
    target_store: str,
    *,
    require_delete_target: bool = True,
) -> None:
    """Reject target production until worker and durable-delete paths are wired."""

    if type(target_store) is not str or _STORE_CODE.fullmatch(target_store) is None:
        raise ValueError("projection target_store must be a lowercase code")

    handler_names = set(PROJECTION_OPERATION_STRATEGIES.names())
    missing_handlers = tuple(
        operation
        for operation in _REQUIRED_PROJECTION_OPERATIONS
        if f"{target_store}:{operation}" not in handler_names
    )
    if missing_handlers:
        raise IncompleteProjectionTargetRuntime(
            f"projection target {target_store!r} is missing worker handlers: "
            f"{', '.join(missing_handlers)}"
        )

    if target_store not in PROJECTION_REVISION_STRATEGIES.names():
        raise IncompleteProjectionTargetRuntime(
            f"projection target {target_store!r} is missing a revision strategy"
        )
    if target_store not in PROJECTION_ATTEMPT_LIFECYCLE_POLICIES.names():
        raise IncompleteProjectionTargetRuntime(
            f"projection target {target_store!r} is missing an attempt lifecycle policy"
        )
    if require_delete_target and target_store not in _document_delete_projection_targets_snapshot():
        raise IncompleteProjectionTargetRuntime(
            f"projection target {target_store!r} is not covered by durable document deletion"
        )
    missing_consistency_operations = unsupported_target_requeue_operations(target_store)
    if missing_consistency_operations:
        missing = ", ".join(sorted(missing_consistency_operations))
        raise IncompleteProjectionTargetRuntime(
            f"projection target {target_store!r} is missing consistency operation coverage: "
            f"{missing}"
        )


def register_projection_delete_target_runtime(target_store: str, *, order: int) -> None:
    """Enable durable deletion only after its worker and consistency contracts exist."""

    if type(order) is not int or order < 0:
        raise ValueError("document delete target order must be a non-negative integer")
    validate_projection_target_runtime(target_store, require_delete_target=False)
    policy = DocumentDeleteTargetPolicy(
        order=order,
        runtime_check=lambda registered_store: validate_projection_target_runtime(registered_store),
    )
    _register_document_delete_target(target_store, policy)
    try:
        validate_projection_target_runtime(target_store)
    except Exception:
        _unregister_document_delete_target(target_store)
        raise


def retire_projection_delete_target_runtime(target_store: str) -> None:
    """Stop new ingestion while retaining durable-delete coverage for old data."""

    unregister_projection_target_policy(target_store)


__all__ = [
    "IncompleteProjectionTargetRuntime",
    "register_projection_delete_target_runtime",
    "retire_projection_delete_target_runtime",
    "validate_projection_target_runtime",
]
