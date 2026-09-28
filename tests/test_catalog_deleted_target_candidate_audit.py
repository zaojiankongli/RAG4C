from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import scripts.audit_catalog_deleted_projection_candidates as audit_module
from sqlalchemy.orm import Session

from core.projection_consistency_enumerators import (
    ProjectionConsistencyEnumerationResult,
    ProjectionConsistencyTargetReference,
    register_projection_consistency_enumerator,
    unregister_projection_consistency_enumerator,
)
from models.orm import Document
from scripts.audit_catalog_deleted_projection_candidates import (
    audit_catalog_deleted_projection_candidates,
)
from scripts.reconcile_chunk_authority import _pseudonym
from tests.test_knowledgeops_chunk_reconcile import ROLLOUT_SECRET, _state
from core.chunk_catalog import ChunkCatalog


class _StaticEnumerator:
    def __init__(
        self,
        rows: tuple[ProjectionConsistencyTargetReference, ...],
        *,
        completeness: str = "best_effort",
        incomplete_reason: str = "",
    ) -> None:
        self.rows = rows
        self.completeness = completeness
        self.incomplete_reason = incomplete_reason

    def enumerate(self, request):
        return ProjectionConsistencyEnumerationResult(
            target_store=request.target_store,
            tenant_id=request.tenant_id,
            dataset_id=request.dataset_id,
            rows=self.rows,
            completeness=self.completeness,
            incomplete_reason=self.incomplete_reason,
        )


def _reference(
    chunk_id: str,
    document_id: str,
    *,
    tenant_id: str = "tenant-1",
    dataset_id: str = "dataset-1",
    target_store: str = "candidate_chunks",
) -> ProjectionConsistencyTargetReference:
    return ProjectionConsistencyTargetReference(
        target_store=target_store,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=document_id,
        chunk_id=chunk_id,
    )


def test_candidate_audit_does_not_classify_tombstones_as_catalog_deleted(
    tmp_path,
) -> None:
    engine, _ = _state(tmp_path, with_attempt=False)
    with Session(engine) as session:
        session.add(
            Document(
                id="doc-deleted",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                name="Deleted tombstone",
                lifecycle_state="deleted",
                retrieval_enabled=False,
                created_at=datetime(2026, 8, 24, 8, 2, 0),
                updated_at=datetime(2026, 8, 24, 8, 2, 0),
            )
        )
        session.commit()

    target = "candidate_chunks"
    rows = (
        _reference("chunk-live", "doc-1"),
        _reference("chunk-tombstone", "doc-deleted"),
        _reference("chunk-missing", "doc-physically-missing"),
    )
    register_projection_consistency_enumerator(
        target,
        lambda _context: _StaticEnumerator(rows),
    )
    try:
        report = audit_catalog_deleted_projection_candidates(
            ChunkCatalog(engine),
            object(),
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            target_store=target,
            report_secret=ROLLOUT_SECRET,
        )
    finally:
        unregister_projection_consistency_enumerator(target)

    assert report.completeness == "best_effort"
    assert [row.document_id for row in report.candidate_references] == [
        "doc-physically-missing"
    ]
    summary = report.to_summary(ROLLOUT_SECRET)
    assert "doc-physically-missing" not in str(summary)
    assert summary["candidate_document_count"] == 1
    assert summary["complete"] is False
    assert summary["confirmable"] is False
    assert summary["repairable"] is False
    engine.dispose()


def test_candidate_audit_surfaces_incomplete_without_confirming_or_repairing(tmp_path) -> None:
    engine, _ = _state(tmp_path, with_attempt=False)
    target = "candidate_incomplete"
    rows = (
        _reference(
            "chunk-unscoped",
            "doc-physically-missing",
            tenant_id="",
            target_store=target,
        ),
    )
    register_projection_consistency_enumerator(
        target,
        lambda _context: _StaticEnumerator(
            rows,
            completeness="incomplete",
            incomplete_reason="target_scope_unavailable",
        ),
    )
    try:
        report = audit_catalog_deleted_projection_candidates(
            ChunkCatalog(engine),
            object(),
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            target_store=target,
            report_secret=ROLLOUT_SECRET,
        )
    finally:
        unregister_projection_consistency_enumerator(target)

    assert report.completeness == "incomplete"
    assert report.incomplete_reason == "target_scope_unavailable"
    assert report.candidate_references == ()
    assert report.to_summary(ROLLOUT_SECRET)["status"] == "incomplete"
    engine.dispose()


def test_candidate_summary_pseudonymizes_all_candidate_references(tmp_path) -> None:
    engine, _ = _state(tmp_path, with_attempt=False)
    target = "candidate_safe"
    rows = (_reference("chunk-sensitive", "doc-sensitive", target_store=target),)
    register_projection_consistency_enumerator(
        target,
        lambda _context: _StaticEnumerator(rows),
    )
    try:
        report = audit_catalog_deleted_projection_candidates(
            ChunkCatalog(engine),
            object(),
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            target_store=target,
            report_secret=ROLLOUT_SECRET,
        )
    finally:
        unregister_projection_consistency_enumerator(target)

    summary_text = str(report.to_summary(ROLLOUT_SECRET))
    assert "doc-sensitive" not in summary_text
    assert "chunk-sensitive" not in summary_text
    assert _pseudonym("doc-sensitive", report_secret=ROLLOUT_SECRET, kind="document") in summary_text
    assert _pseudonym("chunk-sensitive", report_secret=ROLLOUT_SECRET, kind="chunk") in summary_text
    engine.dispose()


def test_cli_is_read_only_and_returns_nonzero_for_incomplete_enumeration(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    engine, _ = _state(tmp_path, with_attempt=False)

    class ReadOnlyBackend:
        def query_chunk_references_by_dataset(self, **_kwargs):
            return [
                {
                    "chunk_id": "chunk-cli-sensitive",
                    "doc_id": "doc-cli-sensitive",
                    "tenant_id": "",
                    "dataset_id": "dataset-1",
                }
            ]

    settings = SimpleNamespace(catalog=SimpleNamespace(schema_mode="verify"))
    monkeypatch.setenv(audit_module._REPORT_SECRET_ENV, ROLLOUT_SECRET)
    monkeypatch.setattr(
        audit_module,
        "_runtime_dependencies",
        lambda: (engine, ReadOnlyBackend(), settings),
    )

    exit_code = audit_module.main(
        [
            "--tenant-id",
            "tenant-1",
            "--dataset-id",
            "dataset-1",
        ]
    )

    assert exit_code == 2
    output = capsys.readouterr().out
    assert "doc-cli-sensitive" not in output
    assert "chunk-cli-sensitive" not in output
    assert "confirmable\": false" in output
    assert "repairable\": false" in output
    engine.dispose()


def test_audit_converts_adapter_and_backend_failures_to_safe_incomplete_codes(tmp_path) -> None:
    engine, _ = _state(tmp_path, with_attempt=False)

    class RaisingEnumerator:
        def enumerate(self, _request):
            raise RuntimeError("dataset-1/doc-secret/chunk-secret backend detail")

    target = "candidate_failure"
    register_projection_consistency_enumerator(
        target,
        lambda _context: RaisingEnumerator(),
    )
    try:
        report = audit_catalog_deleted_projection_candidates(
            ChunkCatalog(engine),
            object(),
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            target_store=target,
            report_secret=ROLLOUT_SECRET,
        )
    finally:
        unregister_projection_consistency_enumerator(target)

    assert report.completeness == "incomplete"
    assert report.incomplete_reason == "target_enumeration_unavailable"
    assert "dataset-1" not in str(report.to_summary(ROLLOUT_SECRET))
    assert "chunk-secret" not in str(report.to_summary(ROLLOUT_SECRET))
    engine.dispose()
