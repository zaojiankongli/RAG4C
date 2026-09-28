"""Explicit repair adapters for chunk-shaped projection consistency reports.

Reading a projection and enqueueing a durable repair are separate capabilities.
This registry keeps repair opt-in: a target reader never becomes repairable
merely because it can return chunk-shaped data.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import inspect
import re
from typing import Any, Protocol

from core.providers import ProviderRegistry, UnknownProviderError

_TARGET_STORE_CODE = re.compile(r"[a-z][a-z0-9_]{0,31}")
_OPERATION_CODE = re.compile(r"[a-z][a-z0-9_]{0,23}")
_BUILTIN_TARGETS = frozenset({"milvus_chunks"})
_UNSUPPORTED_TARGETS = frozenset({"graph_projection"})

RepairAdapterFactory = Callable[["ProjectionConsistencyRepairAdapterContext"], Any]


@dataclass(frozen=True)
class ProjectionConsistencyRepairAdapterContext:
    """Trusted construction context passed to one target repair adapter."""

    target_store: str
    backend: Any


@dataclass(frozen=True)
class ProjectionConsistencyRepairRequest:
    """Validated durable repair command presented to one target adapter."""

    target_store: str
    operation: str
    tenant_id: str
    dataset_id: str
    document_id: str
    attempt_id: str
    target_revision: int
    dedup_key: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        _validate_target_store(self.target_store)
        _validate_operation(self.operation)
        for name in ("tenant_id", "dataset_id", "document_id", "attempt_id", "dedup_key"):
            value = getattr(self, name)
            if type(value) is not str or not value or any(
                ord(char) < 0x20 or ord(char) == 0x7F for char in value
            ):
                raise ValueError(f"projection repair {name} must be a non-empty safe string")
        if type(self.target_revision) is not int or self.target_revision < 0:
            raise ValueError("projection repair target_revision must be non-negative")
        if type(self.payload) is not dict:
            raise TypeError("projection repair payload must be a dict")
        object.__setattr__(self, "payload", dict(self.payload))


class ProjectionConsistencyRepairAdapter(Protocol):
    """Adapter that translates one repair request into durable target work."""

    target_store: str
    operation: str

    def enqueue(
        self,
        request: ProjectionConsistencyRepairRequest,
        queue: Any,
        session: Any,
    ) -> None: ...


@dataclass(frozen=True)
class _RepairAdapterPolicy:
    factory: RepairAdapterFactory


_PROJECTION_CONSISTENCY_REPAIR_ADAPTERS: ProviderRegistry[
    ProjectionConsistencyRepairAdapterContext, _RepairAdapterPolicy
] = ProviderRegistry("projection consistency repair adapter")
_BUILTIN_FACTORIES: dict[str, RepairAdapterFactory] = {}


def _validate_target_store(target_store: str) -> str:
    if type(target_store) is not str or _TARGET_STORE_CODE.fullmatch(target_store) is None:
        raise ValueError("projection repair target_store must be a lowercase code of at most 32 chars")
    return target_store


def _validate_operation(operation: str) -> str:
    if type(operation) is not str or _OPERATION_CODE.fullmatch(operation) is None:
        raise ValueError("projection repair operation must be a lowercase code of at most 24 chars")
    return operation


def _validate_callable_shape(callback: Callable[..., Any], *, label: str) -> None:
    if not callable(callback):
        raise TypeError(f"{label} must be callable")
    methods = (callback, getattr(callback, "__call__", None))
    if any(
        inspect.iscoroutinefunction(method)
        or inspect.isasyncgenfunction(method)
        or inspect.isgeneratorfunction(method)
        for method in methods
        if method is not None
    ):
        raise TypeError(f"{label} must be synchronous")


def _validate_factory(factory: RepairAdapterFactory) -> None:
    _validate_callable_shape(factory, label="projection repair adapter factory")
    try:
        inspect.signature(factory).bind(
            ProjectionConsistencyRepairAdapterContext(target_store="probe", backend=None)
        )
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "projection repair adapter factory must accept one positional context"
        ) from exc


def _validate_adapter(
    adapter: Any,
    *,
    target_store: str,
) -> ProjectionConsistencyRepairAdapter:
    actual_target = getattr(adapter, "target_store", None)
    actual_operation = getattr(adapter, "operation", None)
    if actual_target != target_store:
        raise ValueError("projection repair adapter target_store does not match its registry key")
    _validate_target_store(actual_target)
    _validate_operation(actual_operation)
    enqueue = getattr(adapter, "enqueue", None)
    _validate_callable_shape(enqueue, label="projection repair adapter enqueue")
    try:
        inspect.signature(enqueue).bind(
            ProjectionConsistencyRepairRequest(
                target_store=target_store,
                operation=actual_operation,
                tenant_id="tenant",
                dataset_id="dataset",
                document_id="document",
                attempt_id="attempt",
                target_revision=0,
                dedup_key="dedup",
                payload={},
            ),
            object(),
            object(),
        )
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "projection repair adapter enqueue must accept request, queue, and session"
        ) from exc
    return adapter


def _register_adapter(
    target_store: str,
    factory: RepairAdapterFactory,
    *,
    builtin: bool,
) -> None:
    target = _validate_target_store(target_store)
    if target in _UNSUPPORTED_TARGETS:
        raise ValueError(f"projection consistency repair is intentionally unsupported: {target}")
    if builtin != (target in _BUILTIN_TARGETS):
        if target in _BUILTIN_TARGETS:
            raise ValueError(f"built-in projection repair adapter is reserved: {target}")
        raise ValueError("projection repair built-in registration mismatch")
    _validate_factory(factory)
    _PROJECTION_CONSISTENCY_REPAIR_ADAPTERS.register(
        target,
        lambda _context, factory=factory: _RepairAdapterPolicy(factory=factory),
    )
    if builtin:
        _BUILTIN_FACTORIES[target] = factory


def register_projection_consistency_repair_adapter(
    target_store: str,
    factory: RepairAdapterFactory,
) -> None:
    """Register an explicit target repair adapter."""

    _register_adapter(target_store, factory, builtin=False)


def unregister_projection_consistency_repair_adapter(target_store: str) -> None:
    """Remove one custom repair adapter; built-ins are immutable."""

    target = _validate_target_store(target_store)
    if target in _BUILTIN_TARGETS:
        raise ValueError(f"built-in projection repair adapter cannot be unregistered: {target}")
    if target in _UNSUPPORTED_TARGETS:
        raise ValueError(f"projection consistency repair is intentionally unsupported: {target}")
    _PROJECTION_CONSISTENCY_REPAIR_ADAPTERS.unregister(target)


def resolve_projection_consistency_repair_adapter(
    target_store: str,
    *,
    backend: Any,
) -> ProjectionConsistencyRepairAdapter | None:
    """Resolve an explicit repair adapter, returning ``None`` when not opted in."""

    target = _validate_target_store(target_store)
    if target in _UNSUPPORTED_TARGETS:
        raise UnknownProviderError(
            f"unknown projection consistency repair adapter: {target} "
            "(target is not repairable)"
        )
    try:
        factory = _PROJECTION_CONSISTENCY_REPAIR_ADAPTERS.get_factory(target)
    except UnknownProviderError as exc:
        if target in _BUILTIN_TARGETS:
            raise TypeError(
                f"built-in projection consistency repair adapter was unregistered: {target}"
            ) from exc
        return None
    policy = factory(
        ProjectionConsistencyRepairAdapterContext(target_store=target, backend=backend)
    )
    if not isinstance(policy, _RepairAdapterPolicy):
        if target in _BUILTIN_TARGETS:
            raise TypeError("built-in projection consistency repair adapter contract was replaced")
        raise TypeError("projection repair adapter policy is invalid")
    if target in _BUILTIN_TARGETS and policy.factory is not _BUILTIN_FACTORIES.get(target):
        raise TypeError("built-in projection consistency repair adapter contract was replaced")
    _validate_factory(policy.factory)
    return _validate_adapter(
        policy.factory(
            ProjectionConsistencyRepairAdapterContext(target_store=target, backend=backend)
        ),
        target_store=target,
    )


def projection_consistency_repair_adapter_names() -> tuple[str, ...]:
    return _PROJECTION_CONSISTENCY_REPAIR_ADAPTERS.names()


def enqueue_projection_consistency_repair(
    adapter: ProjectionConsistencyRepairAdapter,
    request: ProjectionConsistencyRepairRequest,
    queue: Any,
    session: Any,
) -> None:
    """Invoke one adapter and reject deferred or malformed enqueue behavior."""

    _validate_adapter(adapter, target_store=request.target_store)
    if adapter.operation != request.operation:
        raise ValueError("projection repair adapter operation does not match the request")
    result = adapter.enqueue(request, queue, session)
    if inspect.isawaitable(result) or inspect.isgenerator(result) or inspect.isasyncgen(result):
        close = getattr(result, "close", None)
        if callable(close):
            close()
        raise TypeError("projection repair adapter enqueue must return None")
    if result is not None:
        raise TypeError("projection repair adapter enqueue must return None")


@dataclass(frozen=True)
class _MilvusReconcileRepairAdapter:
    target_store: str = "milvus_chunks"
    operation: str = "reconcile"

    def enqueue(
        self,
        request: ProjectionConsistencyRepairRequest,
        queue: Any,
        session: Any,
    ) -> None:
        queue.enqueue_operation(
            tenant_id=request.tenant_id,
            dataset_id=request.dataset_id,
            document_id=request.document_id,
            attempt_id=request.attempt_id,
            target_store=request.target_store,
            operation=request.operation,
            dedup_key=request.dedup_key,
            target_revision=request.target_revision,
            payload=request.payload,
            session=session,
        )


def _milvus_repair_adapter_factory(
    _context: ProjectionConsistencyRepairAdapterContext,
) -> ProjectionConsistencyRepairAdapter:
    return _MilvusReconcileRepairAdapter()


_register_adapter("milvus_chunks", _milvus_repair_adapter_factory, builtin=True)


__all__ = [
    "ProjectionConsistencyRepairAdapter",
    "ProjectionConsistencyRepairAdapterContext",
    "ProjectionConsistencyRepairRequest",
    "enqueue_projection_consistency_repair",
    "projection_consistency_repair_adapter_names",
    "register_projection_consistency_repair_adapter",
    "resolve_projection_consistency_repair_adapter",
    "unregister_projection_consistency_repair_adapter",
]
