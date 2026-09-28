from __future__ import annotations

from types import SimpleNamespace

import pytest

from core import projection_consistency_report_fences as report_fences
from core.providers import UnknownProviderError
from core.projection_consistency_report_fences import (
    CatalogProjectionSnapshotIdentity,
    ProjectionConsistencyReportFenceContext,
    ProjectionConsistencyReportFenceObservation,
    ProjectionConsistencyReportFenceSession,
    begin_projection_consistency_report_fence,
    finish_projection_consistency_report_fence,
    register_projection_consistency_report_fence,
    resolve_projection_consistency_report_fence,
    unregister_projection_consistency_report_fence,
)


def _identity(*, count: int = 2) -> CatalogProjectionSnapshotIdentity:
    return CatalogProjectionSnapshotIdentity(
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        dataset_identity_digest="d" * 64,
        document_snapshot_fingerprint="f" * 64,
        document_snapshot_count=count,
    )


def _session(identity: CatalogProjectionSnapshotIdentity) -> ProjectionConsistencyReportFenceSession:
    return ProjectionConsistencyReportFenceSession(
        target_store="custom_chunks",
        tenant_id=identity.tenant_id,
        dataset_id=identity.dataset_id,
        dataset_identity_digest=identity.dataset_identity_digest,
        document_snapshot_fingerprint=identity.document_snapshot_fingerprint,
        document_snapshot_count=identity.document_snapshot_count,
        target_snapshot_token="generation:7",
    )


def test_identity_and_session_are_typed_and_scope_bound() -> None:
    identity = _identity()
    session = _session(identity)

    assert session.matches(identity)
    assert not session.matches(_identity(count=3))
    with pytest.raises(ValueError, match="different Catalog identity"):
        begin_projection_consistency_report_fence(
            SimpleNamespace(
                begin_report=lambda _identity: session,
                finish_report=lambda *_args: ProjectionConsistencyReportFenceObservation(
                    status="stable",
                    snapshot_token="generation:7",
                ),
            ),
            _identity(count=3),
        )

    with pytest.raises(ValueError, match="fingerprint"):
        CatalogProjectionSnapshotIdentity(
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            dataset_identity_digest="d" * 64,
            document_snapshot_fingerprint="not-a-sha",
            document_snapshot_count=2,
        )
    with pytest.raises(ValueError, match="non-negative"):
        CatalogProjectionSnapshotIdentity(
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            dataset_identity_digest="d" * 64,
            document_snapshot_fingerprint="f" * 64,
            document_snapshot_count=-1,
        )


def test_report_fence_registry_dispatches_stable_changed_and_unavailable() -> None:
    target = "report_fenced_chunks"
    identity = _identity()
    backend = SimpleNamespace(generation=7, outcome="stable")

    class GenerationReportFence:
        def begin_report(
            self,
            received: CatalogProjectionSnapshotIdentity,
        ) -> ProjectionConsistencyReportFenceSession:
            assert received == identity
            return ProjectionConsistencyReportFenceSession(
                target_store=target,
                tenant_id=received.tenant_id,
                dataset_id=received.dataset_id,
                dataset_identity_digest=received.dataset_identity_digest,
                document_snapshot_fingerprint=received.document_snapshot_fingerprint,
                document_snapshot_count=received.document_snapshot_count,
                target_snapshot_token=f"generation:{backend.generation}",
            )

        def finish_report(
            self,
            received: CatalogProjectionSnapshotIdentity,
            session: ProjectionConsistencyReportFenceSession,
            documents_read: int,
        ) -> ProjectionConsistencyReportFenceObservation:
            assert received == identity
            assert documents_read == 2
            if backend.outcome == "unavailable":
                return ProjectionConsistencyReportFenceObservation(
                    status="unavailable",
                    reason="target_generation_unavailable",
                )
            if backend.outcome == "changed":
                return ProjectionConsistencyReportFenceObservation(
                    status="changed",
                    snapshot_token=f"generation:{backend.generation + 1}",
                    reason="target_generation_changed",
                )
            return ProjectionConsistencyReportFenceObservation(
                status="stable",
                snapshot_token=session.target_snapshot_token,
            )

    register_projection_consistency_report_fence(
        target,
        lambda _context: GenerationReportFence(),
    )
    try:
        fence = resolve_projection_consistency_report_fence(target, backend=backend)
        assert fence is not None
        session = begin_projection_consistency_report_fence(
            fence,
            identity,
            target_store=target,
        )
        stable = finish_projection_consistency_report_fence(
            fence,
            identity,
            session,
            2,
            target_store=target,
        )
        assert stable.status == "stable"

        backend.outcome = "changed"
        changed = finish_projection_consistency_report_fence(
            fence,
            identity,
            session,
            2,
            target_store=target,
        )
        assert changed.status == "changed"

        backend.outcome = "unavailable"
        unavailable = finish_projection_consistency_report_fence(
            fence,
            identity,
            session,
            2,
            target_store=target,
        )
        assert unavailable.status == "unavailable"
    finally:
        unregister_projection_consistency_report_fence(target)


def test_report_fence_registry_keeps_milvus_and_graph_closed() -> None:
    with pytest.raises(ValueError, match="report-unfenced.*reserved"):
        register_projection_consistency_report_fence("milvus_chunks", lambda _context: object())
    with pytest.raises(ValueError, match="report-unfenced.*reserved"):
        unregister_projection_consistency_report_fence("milvus_chunks")
    with pytest.raises(ValueError, match="intentionally unsupported"):
        register_projection_consistency_report_fence("graph_projection", lambda _context: object())

    registry = report_fences._PROJECTION_CONSISTENCY_REPORT_FENCES
    registry.register("graph_projection", lambda _context: object())
    try:
        with pytest.raises(UnknownProviderError, match="report fence"):
            resolve_projection_consistency_report_fence("graph_projection", backend=object())
    finally:
        registry.unregister("graph_projection")

    registry.register("milvus_chunks", lambda _context: object())
    try:
        assert resolve_projection_consistency_report_fence("milvus_chunks", backend=object()) is None
    finally:
        registry.unregister("milvus_chunks")


def test_report_fence_factory_errors_are_not_misclassified_as_missing() -> None:
    target = "raising_report_fence"

    def factory(_context: ProjectionConsistencyReportFenceContext):
        raise UnknownProviderError("factory failure")

    register_projection_consistency_report_fence(target, factory)
    try:
        with pytest.raises(UnknownProviderError, match="factory failure"):
            resolve_projection_consistency_report_fence(target, backend=object())
    finally:
        unregister_projection_consistency_report_fence(target)


def test_report_fence_rejects_bad_shapes_and_stable_token_mismatch() -> None:
    target = "invalid_report_fence"

    async def async_factory(_context):
        return object()

    with pytest.raises(TypeError, match="report fence factory"):
        register_projection_consistency_report_fence(target, async_factory)

    class BadTokenFence:
        def begin_report(
            self, received: CatalogProjectionSnapshotIdentity
        ) -> ProjectionConsistencyReportFenceSession:
            return ProjectionConsistencyReportFenceSession(
                target_store=target,
                tenant_id=received.tenant_id,
                dataset_id=received.dataset_id,
                dataset_identity_digest=received.dataset_identity_digest,
                document_snapshot_fingerprint=received.document_snapshot_fingerprint,
                document_snapshot_count=received.document_snapshot_count,
                target_snapshot_token="generation:7",
            )

        def finish_report(self, identity, session, documents_read):
            return ProjectionConsistencyReportFenceObservation(
                status="stable",
                snapshot_token="different-token",
            )

    def bad_factory(_context):
        return BadTokenFence()

    register_projection_consistency_report_fence(target, bad_factory)
    try:
        fence = resolve_projection_consistency_report_fence(target, backend=object())
        assert fence is not None
        identity = _identity()
        session = begin_projection_consistency_report_fence(
            fence,
            identity,
            target_store=target,
        )
        with pytest.raises(ValueError, match="does not match its session"):
            finish_projection_consistency_report_fence(fence, identity, session, 2)
    finally:
        unregister_projection_consistency_report_fence(target)
