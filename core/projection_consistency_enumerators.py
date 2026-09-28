"""Dataset-scoped projection target enumeration adapters.

Enumeration is intentionally separate from the per-document consistency
reader.  It can discover projection rows whose Catalog document no longer
exists, but without a shared Catalog/target generation it only produces
best-effort candidates and never confirms drift or authorizes repair.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import inspect
import re
from typing import Any, Literal, Protocol

from core.providers import ProviderRegistry, UnknownProviderError

ProjectionEnumerationCompleteness = Literal["best_effort", "incomplete"]
ProjectionEnumerationIncompleteReason = Literal[
    "target_scope_unavailable",
    "target_enumeration_limit_reached",
    "target_enumeration_invalid",
    "target_enumeration_unavailable",
]
ProjectionTargetEnumeratorFactory = Callable[
    ["ProjectionConsistencyTargetEnumeratorContext"], Any
]
PROJECTION_TARGET_ENUMERATION_LIMIT = 16_384

_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_INCOMPLETE_REASONS = frozenset(
    {
        "target_scope_unavailable",
        "target_enumeration_limit_reached",
        "target_enumeration_invalid",
        "target_enumeration_unavailable",
    }
)
_BUILTIN_TARGETS = frozenset({"milvus_chunks"})
_UNSUPPORTED_TARGETS = frozenset({"graph_projection"})


def _safe_scope_value(value: object, field: str, *, allow_empty: bool = False) -> str:
    if type(value) is not str:
        raise TypeError(f"{field} must be a string")
    if not allow_empty and not value:
        raise ValueError(f"{field} must be non-empty")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ValueError(f"{field} contains control characters")
    return value


def _target_store(value: object) -> str:
    if type(value) is not str or _CODE.fullmatch(value) is None:
        raise ValueError("projection target_store must be a lowercase code")
    return value


@dataclass(frozen=True)
class ProjectionConsistencyEnumerationRequest:
    """Exact tenant/dataset scope for one bounded target enumeration."""

    target_store: str
    tenant_id: str
    dataset_id: str
    limit: int = PROJECTION_TARGET_ENUMERATION_LIMIT

    def __post_init__(self) -> None:
        _target_store(self.target_store)
        _safe_scope_value(self.tenant_id, "tenant_id")
        _safe_scope_value(self.dataset_id, "dataset_id")
        if type(self.limit) is not int or not 1 <= self.limit <= PROJECTION_TARGET_ENUMERATION_LIMIT:
            raise ValueError(
                "projection enumeration limit must be between 1 and "
                f"{PROJECTION_TARGET_ENUMERATION_LIMIT}"
            )


@dataclass(frozen=True)
class ProjectionConsistencyTargetReference:
    """Non-content identity returned by a target enumeration."""

    target_store: str
    tenant_id: str
    dataset_id: str
    document_id: str
    chunk_id: str

    def __post_init__(self) -> None:
        _target_store(self.target_store)
        _safe_scope_value(self.tenant_id, "tenant_id", allow_empty=True)
        _safe_scope_value(self.dataset_id, "dataset_id", allow_empty=True)
        _safe_scope_value(self.document_id, "document_id")
        _safe_scope_value(self.chunk_id, "chunk_id")


@dataclass(frozen=True)
class ProjectionConsistencyEnumerationResult:
    """Materialized target references; incomplete never means empty."""

    target_store: str
    tenant_id: str
    dataset_id: str
    rows: tuple[ProjectionConsistencyTargetReference, ...]
    completeness: ProjectionEnumerationCompleteness
    incomplete_reason: str = ""

    def __post_init__(self) -> None:
        _target_store(self.target_store)
        _safe_scope_value(self.tenant_id, "tenant_id")
        _safe_scope_value(self.dataset_id, "dataset_id")
        if type(self.rows) is not tuple:
            raise TypeError("projection enumeration rows must be a materialized tuple")
        if any(not isinstance(row, ProjectionConsistencyTargetReference) for row in self.rows):
            raise TypeError("projection enumeration rows contain an invalid reference")
        if self.completeness not in {"best_effort", "incomplete"}:
            raise ValueError("projection enumeration completeness is invalid")
        if self.completeness == "incomplete" and self.incomplete_reason not in _INCOMPLETE_REASONS:
            raise ValueError("incomplete projection enumeration requires a safe reason code")
        if self.completeness == "best_effort" and self.incomplete_reason:
            raise ValueError("best-effort projection enumeration cannot include a reason")


@dataclass(frozen=True)
class ProjectionConsistencyTargetEnumeratorContext:
    target_store: str
    backend: Any


class ProjectionConsistencyTargetEnumerator(Protocol):
    def enumerate(
        self, request: ProjectionConsistencyEnumerationRequest
    ) -> ProjectionConsistencyEnumerationResult: ...


@dataclass(frozen=True)
class _EnumeratorPolicy:
    factory: ProjectionTargetEnumeratorFactory


_PROJECTION_CONSISTENCY_ENUMERATORS: ProviderRegistry[
    ProjectionConsistencyTargetEnumeratorContext, _EnumeratorPolicy
] = ProviderRegistry("projection consistency enumerator")
_BUILTIN_FACTORIES: dict[str, ProjectionTargetEnumeratorFactory] = {}


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


def _validate_factory(factory: ProjectionTargetEnumeratorFactory) -> None:
    _validate_callable_shape(factory, label="projection enumerator factory")
    try:
        inspect.signature(factory).bind(
            ProjectionConsistencyTargetEnumeratorContext(target_store="probe", backend=None)
        )
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "projection enumerator factory must accept one positional context"
        ) from exc


def _validate_enumerator(enumerator: Any) -> ProjectionConsistencyTargetEnumerator:
    method = getattr(enumerator, "enumerate", None)
    _validate_callable_shape(method, label="projection target enumerator enumerate")
    try:
        inspect.signature(method).bind(
            ProjectionConsistencyEnumerationRequest(
                target_store="probe",
                tenant_id="tenant",
                dataset_id="dataset",
            )
        )
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "projection target enumerator must accept one enumeration request"
        ) from exc
    return enumerator


def _register_enumerator(
    target_store: str,
    factory: ProjectionTargetEnumeratorFactory,
    *,
    builtin: bool,
) -> None:
    target = _target_store(target_store)
    if target in _UNSUPPORTED_TARGETS:
        raise ValueError(f"projection consistency enumerator is intentionally unsupported: {target}")
    if builtin != (target in _BUILTIN_TARGETS):
        if target in _BUILTIN_TARGETS:
            raise ValueError(f"built-in projection consistency enumerator is reserved: {target}")
        raise ValueError("projection consistency enumerator built-in registration mismatch")
    _validate_factory(factory)
    _PROJECTION_CONSISTENCY_ENUMERATORS.register(
        target,
        lambda _context, factory=factory: _EnumeratorPolicy(factory=factory),
    )
    if builtin:
        _BUILTIN_FACTORIES[target] = factory


def register_projection_consistency_enumerator(
    target_store: str,
    factory: ProjectionTargetEnumeratorFactory,
) -> None:
    _register_enumerator(target_store, factory, builtin=False)


def unregister_projection_consistency_enumerator(target_store: str) -> None:
    target = _target_store(target_store)
    if target in _BUILTIN_TARGETS:
        raise ValueError(
            f"built-in projection consistency enumerator cannot be unregistered: {target}"
        )
    if target in _UNSUPPORTED_TARGETS:
        raise ValueError(f"projection consistency enumerator is intentionally unsupported: {target}")
    _PROJECTION_CONSISTENCY_ENUMERATORS.unregister(target)


def resolve_projection_consistency_enumerator(
    target_store: str,
    *,
    backend: Any,
) -> ProjectionConsistencyTargetEnumerator:
    target = _target_store(target_store)
    if target in _UNSUPPORTED_TARGETS:
        raise UnknownProviderError(
            f"unknown projection consistency enumerator: {target} "
            "(target enumeration is not supported)"
        )
    policy = _PROJECTION_CONSISTENCY_ENUMERATORS.create(
        target,
        ProjectionConsistencyTargetEnumeratorContext(target_store=target, backend=backend),
    )
    if not isinstance(policy, _EnumeratorPolicy):
        raise TypeError("projection consistency enumerator policy is invalid")
    if target in _BUILTIN_TARGETS and policy.factory is not _BUILTIN_FACTORIES.get(target):
        raise TypeError("built-in projection consistency enumerator contract was replaced")
    _validate_factory(policy.factory)
    return _validate_enumerator(
        policy.factory(
            ProjectionConsistencyTargetEnumeratorContext(target_store=target, backend=backend)
        )
    )


def projection_consistency_enumerator_names() -> tuple[str, ...]:
    return _PROJECTION_CONSISTENCY_ENUMERATORS.names()


def _validate_result(
    result: Any,
    request: ProjectionConsistencyEnumerationRequest,
) -> ProjectionConsistencyEnumerationResult:
    if not isinstance(result, ProjectionConsistencyEnumerationResult):
        raise TypeError("projection target enumerator must return ProjectionConsistencyEnumerationResult")
    if (
        result.target_store != request.target_store
        or result.tenant_id != request.tenant_id
        or result.dataset_id != request.dataset_id
    ):
        raise ValueError("projection target enumerator returned a different scope")
    if len(result.rows) > request.limit:
        raise ValueError("projection target enumerator returned more rows than requested")
    identities: set[str] = set()
    incomplete_scope = False
    for row in result.rows:
        if row.target_store != request.target_store:
            raise ValueError("projection target enumerator returned a different target")
        if row.tenant_id and row.tenant_id != request.tenant_id:
            raise ValueError("projection target enumerator returned a different tenant")
        if row.dataset_id and row.dataset_id != request.dataset_id:
            raise ValueError("projection target enumerator returned a different dataset")
        if not row.tenant_id or not row.dataset_id:
            incomplete_scope = True
        identity = row.chunk_id
        if identity in identities:
            raise ValueError("projection target enumerator returned duplicate references")
        identities.add(identity)
    if len(result.rows) >= request.limit and result.completeness == "best_effort":
        return ProjectionConsistencyEnumerationResult(
            target_store=result.target_store,
            tenant_id=result.tenant_id,
            dataset_id=result.dataset_id,
            rows=result.rows,
            completeness="incomplete",
            incomplete_reason="target_enumeration_limit_reached",
        )
    if incomplete_scope and result.completeness == "best_effort":
        return ProjectionConsistencyEnumerationResult(
            target_store=result.target_store,
            tenant_id=result.tenant_id,
            dataset_id=result.dataset_id,
            rows=result.rows,
            completeness="incomplete",
            incomplete_reason="target_scope_unavailable",
        )
    return result


def enumerate_projection_target(
    enumerator: ProjectionConsistencyTargetEnumerator,
    request: ProjectionConsistencyEnumerationRequest,
) -> ProjectionConsistencyEnumerationResult:
    _validate_enumerator(enumerator)
    result = enumerator.enumerate(request)
    if inspect.isawaitable(result):
        close = getattr(result, "close", None)
        if callable(close):
            close()
        raise TypeError("projection target enumerator returned a deferred result")
    if inspect.isgenerator(result) or inspect.isasyncgen(result):
        raise TypeError("projection target enumerator must return a materialized result")
    return _validate_result(result, request)


class _MilvusProjectionConsistencyEnumerator:
    def __init__(self, backend: Any) -> None:
        self._backend = backend

    def enumerate(
        self, request: ProjectionConsistencyEnumerationRequest
    ) -> ProjectionConsistencyEnumerationResult:
        rows = self._backend.query_chunk_references_by_dataset(
            tenant_id=request.tenant_id,
            dataset_id=request.dataset_id,
            limit=request.limit,
            include_unscoped_scope=True,
        )
        if not isinstance(rows, (list, tuple)):
            raise TypeError("Milvus target enumeration must return a materialized row sequence")
        references: list[ProjectionConsistencyTargetReference] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise TypeError("Milvus target enumeration returned an invalid row")
            references.append(
                ProjectionConsistencyTargetReference(
                    target_store=request.target_store,
                    tenant_id=row.get("tenant_id") or "",
                    dataset_id=row.get("dataset_id") or "",
                    document_id=row.get("doc_id") or "",
                    chunk_id=row.get("chunk_id") or "",
                )
            )
        incomplete_reason = (
            "target_enumeration_limit_reached"
            if len(references) >= request.limit
            else ""
        )
        return ProjectionConsistencyEnumerationResult(
            target_store=request.target_store,
            tenant_id=request.tenant_id,
            dataset_id=request.dataset_id,
            rows=tuple(references),
            completeness="incomplete" if incomplete_reason else "best_effort",
            incomplete_reason=incomplete_reason,
        )


def _milvus_enumerator_factory(
    context: ProjectionConsistencyTargetEnumeratorContext,
) -> ProjectionConsistencyTargetEnumerator:
    return _MilvusProjectionConsistencyEnumerator(context.backend)


_register_enumerator("milvus_chunks", _milvus_enumerator_factory, builtin=True)


__all__ = [
    "PROJECTION_TARGET_ENUMERATION_LIMIT",
    "ProjectionConsistencyEnumerationRequest",
    "ProjectionConsistencyEnumerationResult",
    "ProjectionEnumerationIncompleteReason",
    "ProjectionConsistencyTargetEnumerator",
    "ProjectionConsistencyTargetEnumeratorContext",
    "ProjectionConsistencyTargetReference",
    "enumerate_projection_target",
    "projection_consistency_enumerator_names",
    "register_projection_consistency_enumerator",
    "resolve_projection_consistency_enumerator",
    "unregister_projection_consistency_enumerator",
]
