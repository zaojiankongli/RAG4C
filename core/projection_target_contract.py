"""Registered dead-letter requeue contracts for projection target operations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
import inspect
import re
from types import MappingProxyType
from typing import Any, Literal

from core.providers import ProviderRegistry, UnknownProviderError

ProjectionRequeueFamily = Literal["ordinary", "document_delete"]
ProjectionRequeueValidator = Callable[["ProjectionRequeueValidationContext"], bool]

_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
REQUIRED_TARGET_REQUEUE_OPERATIONS = frozenset({"upsert", "delete", "delete_document"})


def _freeze_payload_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_payload_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_payload_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_payload_value(item) for item in value)
    return deepcopy(value)


@dataclass(frozen=True)
class ProjectionRequeueValidationContext:
    """Read-only snapshot supplied to a target-specific requeue validator."""

    target_store: str
    operation: str
    tenant_id: str
    dataset_id: str
    document_id: str
    attempt_id: str
    target_revision: int
    document_generation: int
    attempt_kind: str
    delete_operation_id: str | None
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.payload, Mapping):
            raise TypeError("projection requeue validation payload must be a mapping")
        object.__setattr__(self, "payload", _freeze_payload_value(self.payload))


@dataclass(frozen=True)
class ProjectionRequeuePolicy:
    """Shared lineage family plus an optional target-specific, stricter validator."""

    family: ProjectionRequeueFamily
    validator: ProjectionRequeueValidator | None = None


_BUILTIN_FAMILIES: dict[str, ProjectionRequeueFamily] = {
    "milvus_chunks:upsert": "ordinary",
    "milvus_chunks:delete": "ordinary",
    "milvus_chunks:reconcile": "ordinary",
    "graph_projection:upsert": "ordinary",
    "graph_projection:delete": "ordinary",
    "milvus_chunks:delete_document": "document_delete",
    "graph_projection:delete_document": "document_delete",
    "catalog_finalize:finalize_document_delete": "document_delete",
}
_BUILTIN_POLICY_NAMES = frozenset(_BUILTIN_FAMILIES)
_PROJECTION_REQUEUE_POLICIES: ProviderRegistry[None, ProjectionRequeuePolicy] = ProviderRegistry(
    "projection requeue"
)


def _operation_key(target_store: str, operation: str) -> str:
    if type(target_store) is not str or _CODE.fullmatch(target_store) is None:
        raise ValueError("projection target_store must be a lowercase code")
    if type(operation) is not str or _CODE.fullmatch(operation) is None:
        raise ValueError("projection operation must be a lowercase code")
    return f"{target_store}:{operation}"


def _validate_policy(
    key: str,
    policy: ProjectionRequeuePolicy,
    *,
    builtin: bool,
) -> None:
    if not isinstance(policy, ProjectionRequeuePolicy):
        raise TypeError("projection requeue policy must be ProjectionRequeuePolicy")
    if policy.family not in {"ordinary", "document_delete"}:
        raise ValueError("projection requeue family must be ordinary or document_delete")

    operation = key.split(":", 1)[1]
    valid_operations = (
        {"upsert", "delete", "reconcile"}
        if policy.family == "ordinary"
        else {"delete_document", "finalize_document_delete"}
    )
    if operation not in valid_operations:
        raise ValueError(
            f"projection requeue family {policy.family!r} does not match operation {operation!r}"
        )

    validator = policy.validator
    if builtin and validator is not None:
        raise ValueError("built-in projection requeue policy cannot define a custom validator")
    if not builtin and not callable(validator):
        raise TypeError("custom projection requeue policy requires a target validator")
    if validator is None:
        return

    callable_methods = (validator, getattr(validator, "__call__", None))
    if any(
        inspect.iscoroutinefunction(method)
        or inspect.isasyncgenfunction(method)
        or inspect.isgeneratorfunction(method)
        for method in callable_methods
        if method is not None
    ):
        raise TypeError(
            "projection requeue validator must be synchronous and accept one positional context"
        )
    try:
        inspect.signature(validator).bind(object())
    except (TypeError, ValueError) as exc:
        raise TypeError("projection requeue validator must accept one positional context") from exc


def _register_policy(
    target_store: str,
    operation: str,
    policy: ProjectionRequeuePolicy,
    *,
    builtin: bool,
) -> None:
    key = _operation_key(target_store, operation)
    if builtin:
        if _BUILTIN_FAMILIES.get(key) != policy.family:
            raise ValueError("projection requeue built-in policy registration mismatch")
    elif key in _BUILTIN_POLICY_NAMES:
        raise ValueError(f"built-in projection requeue policy is reserved: {key}")
    _validate_policy(key, policy, builtin=builtin)
    _PROJECTION_REQUEUE_POLICIES.register(
        key,
        lambda _context, policy=policy: policy,
    )


def register_projection_requeue_policy(
    target_store: str,
    operation: str,
    policy: ProjectionRequeuePolicy,
) -> None:
    """Register one custom target/operation pair and its required validator."""

    _register_policy(target_store, operation, policy, builtin=False)


def unregister_projection_requeue_policy(target_store: str, operation: str) -> None:
    """Remove a custom policy; built-in requeue contracts are immutable."""

    key = _operation_key(target_store, operation)
    if key in _BUILTIN_POLICY_NAMES:
        raise ValueError(f"built-in projection requeue policy cannot be unregistered: {key}")
    _PROJECTION_REQUEUE_POLICIES.unregister(key)


def resolve_projection_requeue_policy(
    target_store: str,
    operation: str,
) -> ProjectionRequeuePolicy | None:
    """Resolve the current exact-pair policy without caching a registry snapshot."""

    key = _operation_key(target_store, operation)
    try:
        policy = _PROJECTION_REQUEUE_POLICIES.create(key, None)
    except UnknownProviderError:
        return None
    try:
        _validate_policy(key, policy, builtin=key in _BUILTIN_POLICY_NAMES)
    except (TypeError, ValueError):
        return None
    return policy


def projection_requeue_operation_names() -> tuple[str, ...]:
    return _PROJECTION_REQUEUE_POLICIES.names()


def unsupported_target_requeue_operations(target_store: str) -> frozenset[str]:
    """Return mandatory dead-letter requeue operations missing from registration."""

    _operation_key(target_store, "upsert")
    return frozenset(
        operation
        for operation in REQUIRED_TARGET_REQUEUE_OPERATIONS
        if resolve_projection_requeue_policy(target_store, operation) is None
    )


def validate_projection_requeue_policy(
    policy: ProjectionRequeuePolicy,
    context: ProjectionRequeueValidationContext,
) -> bool:
    """Run the optional target-specific validator and require an exact bool result."""

    if policy.validator is None:
        return True
    result = policy.validator(context)
    if type(result) is not bool:
        raise TypeError("projection requeue validator must return bool")
    return result


for _builtin_key, _builtin_family in _BUILTIN_FAMILIES.items():
    _builtin_target, _builtin_operation = _builtin_key.split(":", 1)
    _register_policy(
        _builtin_target,
        _builtin_operation,
        ProjectionRequeuePolicy(_builtin_family),
        builtin=True,
    )
del _builtin_family, _builtin_key, _builtin_operation, _builtin_target


__all__ = [
    "ProjectionRequeueFamily",
    "ProjectionRequeuePolicy",
    "ProjectionRequeueValidationContext",
    "REQUIRED_TARGET_REQUEUE_OPERATIONS",
    "projection_requeue_operation_names",
    "register_projection_requeue_policy",
    "resolve_projection_requeue_policy",
    "unregister_projection_requeue_policy",
    "unsupported_target_requeue_operations",
    "validate_projection_requeue_policy",
]
