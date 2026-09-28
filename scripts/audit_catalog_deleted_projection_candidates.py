"""Audit possible projection rows for Catalog documents that no longer exist.

This command is intentionally read-only and best-effort. It never classifies a
candidate as confirmed drift and never enqueues repair work.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from core.chunk_catalog import ChunkCatalog
from core.projection_consistency_enumerators import (
    PROJECTION_TARGET_ENUMERATION_LIMIT,
    ProjectionConsistencyEnumerationRequest,
    ProjectionConsistencyTargetReference,
    enumerate_projection_target,
    resolve_projection_consistency_enumerator,
)
from models.orm import Document
from scripts.reconcile_chunk_authority import _pseudonym, _secret_bytes
from scripts.rollout_security import REPORT_SECRET_ENV
from scripts.rollout_snapshot import rollout_snapshot_session

_REPORT_SECRET_ENV = REPORT_SECRET_ENV


@dataclass(frozen=True)
class CatalogDeletedTargetCandidateReport:
    tenant_id: str
    dataset_id: str
    target_store: str
    catalog_document_count: int
    target_reference_count: int
    candidate_references: tuple[ProjectionConsistencyTargetReference, ...]
    completeness: str
    incomplete_reason: str = ""

    @property
    def complete(self) -> bool:
        # This audit is deliberately never a cross-store complete report.
        return False

    @property
    def candidate_document_ids(self) -> tuple[str, ...]:
        return tuple(sorted({row.document_id for row in self.candidate_references}))

    def to_summary(self, report_secret: str) -> dict[str, Any]:
        _secret_bytes(report_secret)
        return {
            "status": "incomplete" if self.completeness == "incomplete" else "best_effort",
            "authority": "candidate_only",
            "tenant_id": _pseudonym(self.tenant_id, report_secret=report_secret, kind="tenant"),
            "dataset_id": _pseudonym(self.dataset_id, report_secret=report_secret, kind="dataset"),
            "target_store": self.target_store,
            "catalog_document_count": self.catalog_document_count,
            "target_reference_count": self.target_reference_count,
            "candidate_document_count": len(self.candidate_document_ids),
            "candidate_count": len(self.candidate_references),
            "candidate_references": [
                {
                    "document_ref": _pseudonym(
                        row.document_id,
                        report_secret=report_secret,
                        kind="document",
                    ),
                    "chunk_ref": _pseudonym(
                        row.chunk_id,
                        report_secret=report_secret,
                        kind="chunk",
                    ),
                }
                for row in self.candidate_references
            ],
            "complete": False,
            "confirmable": False,
            "repairable": False,
            "incomplete_reason": self.incomplete_reason or None,
        }


def audit_catalog_deleted_projection_candidates(
    chunk_catalog: ChunkCatalog,
    target_backend: Any,
    *,
    tenant_id: str,
    dataset_id: str,
    report_secret: str,
    target_store: str = "milvus_chunks",
    max_rows: int = PROJECTION_TARGET_ENUMERATION_LIMIT,
) -> CatalogDeletedTargetCandidateReport:
    """Return possible target rows for physically absent Catalog documents."""

    if not tenant_id or not dataset_id:
        raise ValueError("tenant_id and dataset_id are required")
    _secret_bytes(report_secret)
    if not isinstance(max_rows, int) or not 1 <= max_rows <= PROJECTION_TARGET_ENUMERATION_LIMIT:
        raise ValueError(
            "max_rows must be between 1 and "
            f"{PROJECTION_TARGET_ENUMERATION_LIMIT}"
        )

    with rollout_snapshot_session(chunk_catalog.engine) as session:
        catalog_document_ids = set(
            session.scalars(
                select(Document.id).where(
                    Document.tenant_id == tenant_id,
                    Document.dataset_id == dataset_id,
                )
            )
        )
        try:
            enumerator = resolve_projection_consistency_enumerator(
                target_store,
                backend=target_backend,
            )
            result = enumerate_projection_target(
                enumerator,
                ProjectionConsistencyEnumerationRequest(
                    target_store=target_store,
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    limit=max_rows,
                ),
            )
        except (TypeError, ValueError):
            return CatalogDeletedTargetCandidateReport(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                target_store=target_store,
                catalog_document_count=len(catalog_document_ids),
                target_reference_count=0,
                candidate_references=(),
                completeness="incomplete",
                incomplete_reason="target_enumeration_invalid",
            )
        except Exception:
            return CatalogDeletedTargetCandidateReport(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                target_store=target_store,
                catalog_document_count=len(catalog_document_ids),
                target_reference_count=0,
                candidate_references=(),
                completeness="incomplete",
                incomplete_reason="target_enumeration_unavailable",
            )

    candidates = tuple(
        sorted(
            (
                row
                for row in result.rows
                if row.tenant_id == tenant_id
                and row.dataset_id == dataset_id
                and row.document_id not in catalog_document_ids
            ),
            key=lambda row: (row.document_id, row.chunk_id),
        )
    )
    return CatalogDeletedTargetCandidateReport(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        target_store=target_store,
        catalog_document_count=len(catalog_document_ids),
        target_reference_count=len(result.rows),
        candidate_references=candidates,
        completeness=result.completeness,
        incomplete_reason=result.incomplete_reason,
    )


def _runtime_dependencies() -> tuple[Any, Any, Any]:
    from config.settings import get_settings
    from core import catalog
    from core.milvus_client import RagMilvusClient

    settings = get_settings()
    return catalog.get_engine(), RagMilvusClient(settings.milvus), settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--max-rows", type=int, default=PROJECTION_TARGET_ENUMERATION_LIMIT)
    args = parser.parse_args(argv)

    report_secret = os.getenv(_REPORT_SECRET_ENV, "")
    if not report_secret.strip():
        print(json.dumps({"status": "unsafe", "error": f"{_REPORT_SECRET_ENV} is required"}))
        return 2
    try:
        _secret_bytes(report_secret)
    except ValueError as exc:
        print(json.dumps({"status": "unsafe", "error": str(exc)}))
        return 2

    engine, milvus, settings = _runtime_dependencies()
    if str(settings.catalog.schema_mode) != "verify":
        print(json.dumps({"status": "unsafe", "error": "catalog schema_mode must be verify"}))
        return 2
    report = audit_catalog_deleted_projection_candidates(
        ChunkCatalog(engine),
        milvus,
        tenant_id=args.tenant_id,
        dataset_id=args.dataset_id,
        report_secret=report_secret,
        max_rows=args.max_rows,
    )
    print(json.dumps(report.to_summary(report_secret), ensure_ascii=False, sort_keys=True))
    return 2 if report.completeness == "incomplete" else 0


if __name__ == "__main__":
    raise SystemExit(main())
