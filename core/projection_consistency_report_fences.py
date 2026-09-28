"""Dataset/report-scoped target observation fences.

This is intentionally separate from the document-scoped fence registry.  A
report fence binds one target observation to one Catalog scan identity and can
therefore reject a target generation change between document reads.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import inspect
import re
from typing import Any, Literal, Protocol

from core.providers import ProviderRegistry, UnknownProviderError

ReportFenceObservationStatus = Literal["stable", "changed", "unavailable"]
ReportFenceFactory = Callable[["ProjectionConsistencyReportFenceContext"], Any]

_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_BUILTIN_TARGETS = frozenset({"milvus_chunks"})
_UNSUPPORTED_TARGETS = frozenset({"graph_projection"})


@dataclass(frozen=True)
class CatalogProjectionSnapshotIdentity:
    """The exact Catalog scan identity bound to one target report fence."""

    tenant_id: str
    dataset_id: str
    dataset_identity_digest: str
    document_snapshot_fingerprint: str
    document_snapshot_count: int

    def __post_init__(self) -> None:
        for name in ("tenant_id", "dataset_id"):
            value = getattr(self, name)
            if type(value) is not str or not value:
                raise ValueError(f"Catalog projection snapshot {name} must be non-empty")
        if type(self.dataset_identity_digest) is not str or not self.dataset_identity_digest:
            raise ValueError("Catalog projection dataset identity digest must be non-empty")
        if type(self.document_snapshot_fingerprint) is not str or not _HEX64.fullmatch(
            self.document_snapshot_fingerprint
        ):
            raise ValueError("Catalog projection document snapshot fingerprint must be sha256")
        if type(self.document_snapshot_count) is not int or self.document_snapshot_count < 0:
            raise ValueError("Catalog projection document snapshot count must be non-negative")


@dataclass(frozen=True)
class ProjectionConsistencyReportFenceSession:
    """Immutable target session returned by ``begin_report``."""

    target_store: str
    tenant_id: str
    dataset_id: str
    dataset_identity_digest: str
    document_snapshot_fingerprint: str
    document_snapshot_count: int
    target_snapshot_token: str

    def __post_init__(self) -> None:
        if type(self.target_store) is not str or _CODE.fullmatch(self.target_store) is None:
            raise ValueError("projection report fence target_store must be a lowercase code")
        if type(self.target_snapshot_token) is not str or not self.target_snapshot_token:
            raise ValueError("projection report fence target_snapshot_token must be non-empty")
        CatalogProjectionSnapshotIdentity(
            tenant_id=self.tenant_id,
            dataset_id=self.dataset_id,
            dataset_identity_digest=self.dataset_identity_digest,
            document_snapshot_fingerprint=self.document_snapshot_fingerprint,
            document_snapshot_count=self.document_snapshot_count,
        )

    def matches(self, identity: CatalogProjectionSnapshotIdentity) -> bool:
        return (
            self.tenant_id == identity.tenant_id
            and self.dataset_id == identity.dataset_id
            and self.dataset_identity_digest == identity.dataset_identity_digest
            and self.document_snapshot_fingerprint == identity.document_snapshot_fingerprint
            and self.document_snapshot_count == identity.document_snapshot_count
        )


@dataclass(frozen=True)
class ProjectionConsistencyReportFenceObservation:
    """Target report result after all document reads have completed."""

    status: ReportFenceObservationStatus
    snapshot_token: str | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if self.status not in {"stable", "changed", "unavailable"}:
            raise ValueError("projection report fence observation status is invalid")
        if self.snapshot_token is not None and (
            type(self.snapshot_token) is not str or not self.snapshot_token
        ):
            raise ValueError("projection report fence snapshot_token must be non-empty")
        if self.status == "stable" and not self.snapshot_token:
            raise ValueError("stable projection report fences require a snapshot_token")
        if self.status in {"changed", "unavailable"} and not self.reason:
            raise ValueError("non-stable projection report fences require a reason")


class ProjectionConsistencyReportFence(Protocol):
    """Adapter that observes one target across a complete Catalog report."""

    def begin_report(
        self,
        identity: CatalogProjectionSnapshotIdentity,
    ) -> ProjectionConsistencyReportFenceSession: ...

    def finish_report(
        self,
        identity: CatalogProjectionSnapshotIdentity,
        session: ProjectionConsistencyReportFenceSession,
        documents_read: int,
    ) -> ProjectionConsistencyReportFenceObservation: ...


@dataclass(frozen=True)
class _ReportFencePolicy:
    factory: ReportFenceFactory


_PROJECTION_CONSISTENCY_REPORT_FENCES: ProviderRegistry[
    ProjectionConsistencyReportFenceContext,
    _ReportFencePolicy,
] = ProviderRegistry("projection consistency report fence")


@dataclass(frozen=True)
class ProjectionConsistencyReportFenceContext:
    target_store: str
    backend: Any


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


def _validate_factory(factory: ReportFenceFactory) -> None:
    _validate_callable_shape(factory, label="projection report fence factory")
    try:
        inspect.signature(factory).bind(
            ProjectionConsistencyReportFenceContext(target_store="probe", backend=None)
        )
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "projection report fence factory must accept one positional context"
        ) from exc


def _validate_fence(fence: Any) -> ProjectionConsistencyReportFence:
    begin = getattr(fence, "begin_report", None)
    finish = getattr(fence, "finish_report", None)
    _validate_callable_shape(begin, label="projection report fence begin_report")
    _validate_callable_shape(finish, label="projection report fence finish_report")
    try:
        inspect.signature(begin).bind(object())
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "projection report fence begin_report must accept one snapshot identity"
        ) from exc
    try:
        inspect.signature(finish).bind(object(), object(), object())
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "projection report fence finish_report must accept identity, session, "
            "and documents_read"
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


def register_projection_consistency_report_fence(
    target_store: str,
    factory: ReportFenceFactory,
) -> None:
    target = _validate_target_store(target_store)
    if target in _BUILTIN_TARGETS:
        raise ValueError(
            f"built-in projection target is intentionally report-unfenced and reserved: {target}"
        )
    if target in _UNSUPPORTED_TARGETS:
        raise ValueError(
            f"projection consistency report fence is intentionally unsupported: {target}"
        )
    _validate_factory(factory)
    _PROJECTION_CONSISTENCY_REPORT_FENCES.register(
        target,
        lambda _context, factory=factory: _ReportFencePolicy(factory=factory),
    )


def unregister_projection_consistency_report_fence(target_store: str) -> None:
    target = _validate_target_store(target_store)
    if target in _BUILTIN_TARGETS:
        raise ValueError(
            f"built-in projection target is intentionally report-unfenced and reserved: {target}"
        )
    if target in _UNSUPPORTED_TARGETS:
        raise ValueError(
            f"projection consistency report fence is intentionally unsupported: {target}"
        )
    _PROJECTION_CONSISTENCY_REPORT_FENCES.unregister(target)


def resolve_projection_consistency_report_fence(
    target_store: str,
    *,
    backend: Any,
) -> ProjectionConsistencyReportFence | None:
    target = _validate_target_store(target_store)
    if target in _BUILTIN_TARGETS:
        return None
    if target in _UNSUPPORTED_TARGETS:
        raise UnknownProviderError(
            f"unknown projection consistency report fence: {target} "
            "(target is not chunk-shaped)"
        )
    try:
        factory = _PROJECTION_CONSISTENCY_REPORT_FENCES.get_factory(target)
    except UnknownProviderError:
        return None
    policy = factory(
        ProjectionConsistencyReportFenceContext(target_store=target, backend=backend)
    )
    if not isinstance(policy, _ReportFencePolicy):
        raise TypeError("projection consistency report fence policy is invalid")
    _validate_factory(policy.factory)
    return _validate_fence(
        policy.factory(
            ProjectionConsistencyReportFenceContext(target_store=target, backend=backend)
        )
    )


def projection_consistency_report_fence_names() -> tuple[str, ...]:
    return _PROJECTION_CONSISTENCY_REPORT_FENCES.names()


def begin_projection_consistency_report_fence(
    fence: ProjectionConsistencyReportFence,
    identity: CatalogProjectionSnapshotIdentity,
    *,
    target_store: str | None = None,
) -> ProjectionConsistencyReportFenceSession:
    _validate_fence(fence)
    session = fence.begin_report(identity)
    if inspect.isawaitable(session) or inspect.isgenerator(session) or inspect.isasyncgen(session):
        _close_deferred(session)
        raise TypeError("projection report fence begin_report must return a materialized session")
    if not isinstance(session, ProjectionConsistencyReportFenceSession):
        raise TypeError(
            "projection report fence begin_report must return "
            "ProjectionConsistencyReportFenceSession"
        )
    if target_store is not None and session.target_store != _validate_target_store(target_store):
        raise ValueError("projection report fence session target_store does not match the request")
    if not session.matches(identity):
        raise ValueError("projection report fence session is bound to a different Catalog identity")
    return session


def finish_projection_consistency_report_fence(
    fence: ProjectionConsistencyReportFence,
    identity: CatalogProjectionSnapshotIdentity,
    session: ProjectionConsistencyReportFenceSession,
    documents_read: int,
    *,
    target_store: str | None = None,
) -> ProjectionConsistencyReportFenceObservation:
    _validate_fence(fence)
    if target_store is not None and session.target_store != _validate_target_store(target_store):
        raise ValueError("projection report fence session target_store does not match the request")
    if not session.matches(identity):
        raise ValueError("projection report fence session is bound to a different Catalog identity")
    if type(documents_read) is not int or documents_read < 0:
        raise ValueError("projection report fence documents_read must be non-negative")
    observation = fence.finish_report(identity, session, documents_read)
    if (
        inspect.isawaitable(observation)
        or inspect.isgenerator(observation)
        or inspect.isasyncgen(observation)
    ):
        _close_deferred(observation)
        raise TypeError(
            "projection report fence finish_report must return a materialized observation"
        )
    if not isinstance(observation, ProjectionConsistencyReportFenceObservation):
        raise TypeError(
            "projection report fence finish_report must return "
            "ProjectionConsistencyReportFenceObservation"
        )
    if observation.status == "stable" and observation.snapshot_token != session.target_snapshot_token:
        raise ValueError(
            "stable projection report fence observation token does not match its session"
        )
    return observation


__all__ = [
    "CatalogProjectionSnapshotIdentity",
    "ProjectionConsistencyReportFence",
    "ProjectionConsistencyReportFenceContext",
    "ProjectionConsistencyReportFenceObservation",
    "ProjectionConsistencyReportFenceSession",
    "ReportFenceObservationStatus",
    "begin_projection_consistency_report_fence",
    "finish_projection_consistency_report_fence",
    "projection_consistency_report_fence_names",
    "register_projection_consistency_report_fence",
    "resolve_projection_consistency_report_fence",
    "unregister_projection_consistency_report_fence",
]
