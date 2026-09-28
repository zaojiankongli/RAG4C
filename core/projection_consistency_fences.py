"""Target-owned generation/snapshot fences for consistency reads.

Readers translate an external projection into chunk-shaped data.  A fence is a
separate adapter because a reader-owned token is not evidence that the target
remained stable while the read was in progress.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import inspect
import re
from typing import Any, Literal, Protocol

from core.providers import ProviderRegistry, UnknownProviderError

ProjectionFenceObservationStatus = Literal["stable", "changed", "unavailable"]
ProjectionFenceFactory = Callable[["ProjectionConsistencyFenceContext"], Any]

_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_BUILTIN_TARGETS = frozenset({"milvus_chunks"})
_UNSUPPORTED_TARGETS = frozenset({"graph_projection"})


@dataclass(frozen=True)
class ProjectionConsistencyFenceContext:
    """Trusted construction context passed to one target-specific fence."""

    target_store: str
    backend: Any


@dataclass(frozen=True)
class ProjectionConsistencyFenceObservation:
    """Outcome of comparing target state before and after one materialized read."""

    status: ProjectionFenceObservationStatus
    snapshot_token: str | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if self.status not in {"stable", "changed", "unavailable"}:
            raise ValueError("projection fence observation status is invalid")
        if self.snapshot_token is not None and (
            type(self.snapshot_token) is not str or not self.snapshot_token
        ):
            raise ValueError("projection fence snapshot_token must be a non-empty string")
        if self.status == "stable" and not self.snapshot_token:
            raise ValueError("stable projection fence observations require a snapshot_token")
        if self.status in {"changed", "unavailable"} and not self.reason:
            raise ValueError("non-stable projection fence observations require a reason")


class ProjectionConsistencyFence(Protocol):
    """Adapter for a target-owned generation or snapshot boundary."""

    def begin(self, request: Any) -> Any: ...

    def finish(
        self,
        request: Any,
        begin_token: Any,
        read_result: Any,
    ) -> ProjectionConsistencyFenceObservation: ...


@dataclass(frozen=True)
class _FencePolicy:
    factory: ProjectionFenceFactory


_PROJECTION_CONSISTENCY_FENCES: ProviderRegistry[ProjectionConsistencyFenceContext, _FencePolicy] = (
    ProviderRegistry("projection consistency fence")
)


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


def _validate_factory(factory: ProjectionFenceFactory) -> None:
    _validate_callable_shape(factory, label="projection fence factory")
    try:
        inspect.signature(factory).bind(
            ProjectionConsistencyFenceContext(target_store="probe", backend=None)
        )
    except (TypeError, ValueError) as exc:
        raise TypeError("projection fence factory must accept one positional context") from exc


def _validate_fence(fence: Any) -> ProjectionConsistencyFence:
    begin = getattr(fence, "begin", None)
    finish = getattr(fence, "finish", None)
    _validate_callable_shape(begin, label="projection fence begin")
    _validate_callable_shape(finish, label="projection fence finish")
    try:
        inspect.signature(begin).bind(object())
    except (TypeError, ValueError) as exc:
        raise TypeError("projection fence begin must accept one positional request") from exc
    try:
        inspect.signature(finish).bind(object(), object(), object())
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "projection fence finish must accept request, begin_token, and read_result"
        ) from exc
    return fence


def _close_deferred(value: Any) -> None:
    cancel = getattr(value, "cancel", None)
    if callable(cancel):
        cancel()
    close = getattr(value, "close", None)
    if callable(close):
        close()
        return
    aclose = getattr(value, "aclose", None)
    if callable(aclose):
        close_awaitable = aclose()
        close_sync = getattr(close_awaitable, "close", None)
        if callable(close_sync):
            close_sync()


def register_projection_consistency_fence(
    target_store: str,
    factory: ProjectionFenceFactory,
) -> None:
    """Register one exact target-owned generation/snapshot fence."""

    target = _validate_target_store(target_store)
    if target in _BUILTIN_TARGETS:
        raise ValueError(
            f"built-in projection target is intentionally unfenced and reserved: {target}"
        )
    if target in _UNSUPPORTED_TARGETS:
        raise ValueError(f"projection consistency fence is intentionally unsupported: {target}")
    _validate_factory(factory)
    _PROJECTION_CONSISTENCY_FENCES.register(
        target,
        lambda _context, factory=factory: _FencePolicy(factory=factory),
    )


def unregister_projection_consistency_fence(target_store: str) -> None:
    """Remove one custom fence; unknown names are harmless like other registries."""

    target = _validate_target_store(target_store)
    if target in _BUILTIN_TARGETS:
        raise ValueError(
            f"built-in projection target is intentionally unfenced and reserved: {target}"
        )
    if target in _UNSUPPORTED_TARGETS:
        raise ValueError(f"projection consistency fence is intentionally unsupported: {target}")
    _PROJECTION_CONSISTENCY_FENCES.unregister(target)


def resolve_projection_consistency_fence(
    target_store: str,
    *,
    backend: Any,
) -> ProjectionConsistencyFence | None:
    """Resolve a fence, returning ``None`` when a target is explicitly unfenced."""

    target = _validate_target_store(target_store)
    if target in _BUILTIN_TARGETS:
        # Milvus has no target generation/snapshot authority in this slice.
        # Keep this explicit even if a test or an unsafe caller injects a raw
        # registry entry.
        return None
    if target in _UNSUPPORTED_TARGETS:
        raise UnknownProviderError(
            f"unknown projection consistency fence: {target} (target is not chunk-shaped)"
        )
    try:
        factory = _PROJECTION_CONSISTENCY_FENCES.get_factory(target)
    except UnknownProviderError:
        return None
    policy = factory(ProjectionConsistencyFenceContext(target_store=target, backend=backend))
    if not isinstance(policy, _FencePolicy):
        raise TypeError("projection consistency fence policy is invalid")
    _validate_factory(policy.factory)
    return _validate_fence(
        policy.factory(
            ProjectionConsistencyFenceContext(target_store=target, backend=backend)
        )
    )


def projection_consistency_fence_names() -> tuple[str, ...]:
    return _PROJECTION_CONSISTENCY_FENCES.names()


def begin_projection_consistency_fence(
    fence: ProjectionConsistencyFence,
    request: Any,
) -> Any:
    """Invoke a fence start and reject deferred execution at the port boundary."""

    _validate_fence(fence)
    token = fence.begin(request)
    if inspect.isawaitable(token) or inspect.isgenerator(token) or inspect.isasyncgen(token):
        _close_deferred(token)
        raise TypeError("projection fence begin must return a materialized token")
    return token


def finish_projection_consistency_fence(
    fence: ProjectionConsistencyFence,
    request: Any,
    begin_token: Any,
    read_result: Any,
) -> ProjectionConsistencyFenceObservation:
    """Invoke and validate a target fence verdict."""

    _validate_fence(fence)
    observation = fence.finish(request, begin_token, read_result)
    if (
        inspect.isawaitable(observation)
        or inspect.isgenerator(observation)
        or inspect.isasyncgen(observation)
    ):
        _close_deferred(observation)
        raise TypeError("projection fence finish must return a materialized observation")
    if not isinstance(observation, ProjectionConsistencyFenceObservation):
        raise TypeError(
            "projection fence finish must return ProjectionConsistencyFenceObservation"
        )
    return observation


__all__ = [
    "ProjectionConsistencyFence",
    "ProjectionConsistencyFenceContext",
    "ProjectionConsistencyFenceObservation",
    "ProjectionFenceObservationStatus",
    "begin_projection_consistency_fence",
    "finish_projection_consistency_fence",
    "projection_consistency_fence_names",
    "register_projection_consistency_fence",
    "resolve_projection_consistency_fence",
    "unregister_projection_consistency_fence",
]
