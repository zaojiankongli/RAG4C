"""Read-side adapters for chunk-shaped projection consistency reports.

This registry is deliberately separate from projection write and dead-letter
requeue registries: write eligibility does not imply that a target can be read
or compared safely.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import inspect
import re
from typing import Any, Literal, Protocol

from core.projection_consistency_fences import (
    ProjectionConsistencyFence,
    begin_projection_consistency_fence,
    finish_projection_consistency_fence,
    resolve_projection_consistency_fence,
)
from core.providers import ProviderRegistry, UnknownProviderError
from models.schemas import Chunk

ProjectionReadCompleteness = Literal["best_effort", "incomplete"]
ProjectionFenceStatus = Literal[
    "unfenced",
    "target_observation_stable",
    "target_changed",
    "unavailable",
]
ProjectionReaderFactory = Callable[["ProjectionConsistencyReaderContext"], Any]

_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_MILVUS_DOCUMENT_READ_LIMIT = 16_384
_BUILTIN_TARGETS = frozenset({"milvus_chunks"})
_UNSUPPORTED_TARGETS = frozenset({"graph_projection"})


@dataclass(frozen=True)
class ProjectionConsistencyReaderContext:
    """Trusted construction context supplied to one target-specific adapter."""

    target_store: str
    backend: Any


@dataclass(frozen=True)
class ProjectionConsistencyReadRequest:
    """Exact catalog scope for one document read."""

    target_store: str
    tenant_id: str
    dataset_id: str
    document_id: str
    target_snapshot_token: str | None = None


@dataclass(frozen=True)
class ProjectionConsistencyReadResult:
    """Materialized result; incomplete reads must never be treated as empty."""

    target_store: str
    tenant_id: str
    dataset_id: str
    document_id: str
    chunks: tuple[Chunk, ...]
    completeness: ProjectionReadCompleteness
    snapshot_token: str | None = None
    incomplete_reason: str = ""
    fence_status: ProjectionFenceStatus = "unfenced"

    def __post_init__(self) -> None:
        if type(self.chunks) is not tuple:
            raise TypeError("projection reader chunks must be a materialized tuple")
        if any(not isinstance(chunk, Chunk) for chunk in self.chunks):
            raise TypeError("projection reader chunks must contain Chunk records")
        object.__setattr__(
            self,
            "chunks",
            tuple(chunk.model_copy(deep=True) for chunk in self.chunks),
        )
        if self.completeness not in {"best_effort", "incomplete"}:
            raise ValueError("projection reader completeness is invalid")
        if self.completeness == "incomplete" and not self.incomplete_reason:
            raise ValueError("incomplete projection reads require a reason code")
        if self.completeness != "incomplete" and self.incomplete_reason:
            raise ValueError("only incomplete projection reads may include a reason code")
        if self.snapshot_token is not None and (
            type(self.snapshot_token) is not str or not self.snapshot_token
        ):
            raise ValueError("projection reader snapshot_token must be a non-empty string")
        if self.fence_status not in {
            "unfenced",
            "target_observation_stable",
            "target_changed",
            "unavailable",
        }:
            raise ValueError("projection reader fence_status is invalid")
        if self.fence_status == "target_observation_stable" and not self.snapshot_token:
            raise ValueError("stable target observations require a snapshot_token")
        if self.fence_status in {"target_changed", "unavailable"} and self.completeness != "incomplete":
            raise ValueError("non-stable projection fences require an incomplete read")


class ProjectionConsistencyReader(Protocol):
    """Adapter for a target whose projection rows share the chunk-head shape."""

    def read_document(
        self, request: ProjectionConsistencyReadRequest
    ) -> ProjectionConsistencyReadResult: ...


@dataclass(frozen=True)
class _ReaderPolicy:
    factory: ProjectionReaderFactory


_PROJECTION_CONSISTENCY_READERS: ProviderRegistry[
    ProjectionConsistencyReaderContext, _ReaderPolicy
] = ProviderRegistry("projection consistency reader")
_BUILTIN_FACTORIES: dict[str, ProjectionReaderFactory] = {}


def _validate_target_store(target_store: str) -> str:
    if type(target_store) is not str or _CODE.fullmatch(target_store) is None:
        raise ValueError("projection target_store must be a lowercase code")
    return target_store


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


def _validate_factory(factory: ProjectionReaderFactory) -> None:
    _validate_callable_shape(factory, label="projection reader factory")
    try:
        inspect.signature(factory).bind(
            ProjectionConsistencyReaderContext(target_store="probe", backend=None)
        )
    except (TypeError, ValueError) as exc:
        raise TypeError("projection reader factory must accept one positional context") from exc


def _validate_reader(reader: Any) -> ProjectionConsistencyReader:
    method = getattr(reader, "read_document", None)
    _validate_callable_shape(method, label="projection reader read_document")
    try:
        inspect.signature(method).bind(
            ProjectionConsistencyReadRequest(
                target_store="probe",
                tenant_id="tenant",
                dataset_id="dataset",
                document_id="document",
            )
        )
    except (TypeError, ValueError) as exc:
        raise TypeError("projection reader must accept one positional read request") from exc
    return reader


def _validate_result(
    result: Any,
    request: ProjectionConsistencyReadRequest,
) -> ProjectionConsistencyReadResult:
    if not isinstance(result, ProjectionConsistencyReadResult):
        raise TypeError("projection reader must return ProjectionConsistencyReadResult")
    if result.fence_status != "unfenced":
        raise ValueError("projection reader cannot self-assert a projection fence")
    if (
        result.target_store != request.target_store
        or result.tenant_id != request.tenant_id
        or result.dataset_id != request.dataset_id
        or result.document_id != request.document_id
    ):
        raise ValueError("projection reader returned a result for a different scope")
    chunk_ids = [chunk.chunk_id for chunk in result.chunks]
    if any(not isinstance(chunk_id, str) or not chunk_id for chunk_id in chunk_ids):
        raise ValueError("projection reader returned an invalid chunk identifier")
    if len(chunk_ids) != len(set(chunk_ids)):
        raise ValueError("projection reader returned duplicate chunk identifiers")
    if any(chunk.doc_id != request.document_id for chunk in result.chunks):
        raise ValueError("projection reader returned a chunk for a different document")
    if any(chunk.tenant_id and chunk.tenant_id != request.tenant_id for chunk in result.chunks):
        raise ValueError("projection reader returned a chunk for a different tenant")
    if any(chunk.dataset_id and chunk.dataset_id != request.dataset_id for chunk in result.chunks):
        raise ValueError("projection reader returned a chunk for a different dataset")
    if result.completeness == "best_effort" and any(
        not chunk.tenant_id or not chunk.dataset_id for chunk in result.chunks
    ):
        raise ValueError("projection reader returned a chunk without complete tenant/dataset scope")
    return result


def _register_reader(
    target_store: str,
    factory: ProjectionReaderFactory,
    *,
    builtin: bool,
) -> None:
    target = _validate_target_store(target_store)
    if target in _UNSUPPORTED_TARGETS:
        raise ValueError(f"projection consistency reader is intentionally unsupported: {target}")
    if builtin != (target in _BUILTIN_TARGETS):
        if target in _BUILTIN_TARGETS:
            raise ValueError(f"built-in projection consistency reader is reserved: {target}")
        raise ValueError("projection consistency built-in registration mismatch")
    _validate_factory(factory)
    _PROJECTION_CONSISTENCY_READERS.register(
        target,
        lambda _context, factory=factory: _ReaderPolicy(factory=factory),
    )
    if builtin:
        _BUILTIN_FACTORIES[target] = factory


def register_projection_consistency_reader(
    target_store: str,
    factory: ProjectionReaderFactory,
) -> None:
    """Register a custom chunk-shaped reader; target keys are exact lowercase codes."""

    _register_reader(target_store, factory, builtin=False)


def unregister_projection_consistency_reader(target_store: str) -> None:
    """Remove a custom reader; built-in adapters are immutable."""

    target = _validate_target_store(target_store)
    if target in _BUILTIN_TARGETS:
        raise ValueError(f"built-in projection consistency reader cannot be unregistered: {target}")
    _PROJECTION_CONSISTENCY_READERS.unregister(target)


def resolve_projection_consistency_reader(
    target_store: str,
    *,
    backend: Any,
) -> ProjectionConsistencyReader:
    """Create and validate the exact reader adapter for a report target."""

    target = _validate_target_store(target_store)
    if target in _UNSUPPORTED_TARGETS:
        raise UnknownProviderError(
            f"unknown projection consistency reader: {target} (target is not chunk-shaped)"
        )
    policy = _PROJECTION_CONSISTENCY_READERS.create(
        target,
        ProjectionConsistencyReaderContext(target_store=target, backend=backend),
    )
    if not isinstance(policy, _ReaderPolicy):
        raise TypeError("projection consistency reader policy is invalid")
    if target in _BUILTIN_TARGETS and policy.factory is not _BUILTIN_FACTORIES.get(target):
        raise TypeError("built-in projection consistency reader contract was replaced")
    _validate_factory(policy.factory)
    return _validate_reader(
        policy.factory(ProjectionConsistencyReaderContext(target_store=target, backend=backend))
    )


def projection_consistency_reader_names() -> tuple[str, ...]:
    """List configured reader keys without implying snapshot completeness."""

    return _PROJECTION_CONSISTENCY_READERS.names()


def read_projection_document(
    reader: ProjectionConsistencyReader,
    request: ProjectionConsistencyReadRequest,
    *,
    fence: ProjectionConsistencyFence | None = None,
) -> ProjectionConsistencyReadResult:
    """Run one adapter read and apply an optional target-owned fence.

    A reader may expose an opaque token for diagnostics, but the host clears
    that token unless a separately registered fence adapter verifies it.
    """

    _validate_reader(reader)
    begin_token: Any = None
    if fence is not None:
        begin_token = begin_projection_consistency_fence(fence, request)
    result = reader.read_document(request)
    if inspect.isawaitable(result):
        close = getattr(result, "close", None)
        if callable(close):
            close()
        raise TypeError("projection reader read_document returned a deferred result")
    if inspect.isgenerator(result) or inspect.isasyncgen(result):
        raise TypeError("projection reader read_document must return a materialized result")
    validated = _validate_result(result, request)
    if fence is None:
        return replace(validated, snapshot_token=None, fence_status="unfenced")

    observation = finish_projection_consistency_fence(
        fence,
        request,
        begin_token,
        # A target fence must compare target-owned state. Do not let it
        # accidentally consume a token asserted by the reader itself.
        replace(validated, snapshot_token=None, fence_status="unfenced"),
    )
    if observation.status == "stable":
        return replace(
            validated,
            snapshot_token=observation.snapshot_token,
            fence_status="target_observation_stable",
        )
    reason = observation.reason
    if observation.status == "changed":
        reason = reason or "projection_fence_changed"
        fence_status: ProjectionFenceStatus = "target_changed"
    else:
        reason = reason or "projection_fence_unavailable"
        fence_status = "unavailable"
    return replace(
        validated,
        completeness="incomplete",
        incomplete_reason=reason,
        snapshot_token=observation.snapshot_token,
        fence_status=fence_status,
    )


class _MilvusProjectionConsistencyReader:
    def __init__(self, backend: Any) -> None:
        self._backend = backend

    def read_document(
        self, request: ProjectionConsistencyReadRequest
    ) -> ProjectionConsistencyReadResult:
        rows = self._backend.query_chunks_by_doc(
            request.document_id,
            tenant_id=request.tenant_id,
            dataset_id=request.dataset_id,
            include_unscoped_scope=True,
        )
        if not isinstance(rows, (list, tuple)):
            raise TypeError("Milvus document query must return a materialized row sequence")
        materialized = tuple(rows)
        at_limit = len(materialized) >= _MILVUS_DOCUMENT_READ_LIMIT
        missing_scope = any(not chunk.tenant_id or not chunk.dataset_id for chunk in materialized)
        incomplete_reason = (
            "document_read_limit_reached"
            if at_limit
            else "chunk_scope_unavailable"
            if missing_scope
            else ""
        )
        return ProjectionConsistencyReadResult(
            target_store=request.target_store,
            tenant_id=request.tenant_id,
            dataset_id=request.dataset_id,
            document_id=request.document_id,
            chunks=materialized,
            completeness="incomplete" if incomplete_reason else "best_effort",
            incomplete_reason=incomplete_reason,
        )


def _milvus_reader_factory(
    context: ProjectionConsistencyReaderContext,
) -> ProjectionConsistencyReader:
    return _MilvusProjectionConsistencyReader(context.backend)


_register_reader("milvus_chunks", _milvus_reader_factory, builtin=True)


__all__ = [
    "ProjectionFenceStatus",
    "ProjectionConsistencyReadRequest",
    "ProjectionConsistencyReadResult",
    "ProjectionConsistencyReader",
    "ProjectionConsistencyReaderContext",
    "ProjectionReadCompleteness",
    "resolve_projection_consistency_fence",
    "projection_consistency_reader_names",
    "read_projection_document",
    "register_projection_consistency_reader",
    "resolve_projection_consistency_reader",
    "unregister_projection_consistency_reader",
]
