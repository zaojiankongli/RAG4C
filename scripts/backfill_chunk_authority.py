"""Backfill authoritative chunk heads from catalog documents and Milvus.

The command is dry-run by default. Applying is restricted to a verified schema
in the shadow authority rollout. Retained summaries contain stable pseudonyms,
not source-derived document or chunk identifiers.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from core.chunk_catalog import ChunkCatalog, ChunkRevisionConflict
from models.orm import ChunkHead, Dataset, Document
from scripts.rollout_security import (
    REPORT_SECRET_ENV as _REPORT_SECRET_ENV,
    decode_canonical_base64url_json,
    validate_rollout_cursor_fields,
    validate_rollout_secret,
    validate_sha256_hmac,
)
from scripts.rollout_snapshot import (
    DEFAULT_ROLLOUT_BATCH_SIZE,
    capture_document_snapshot,
    estimate_rollout_cost,
    rollout_snapshot_session,
)

_CURSOR_VERSION = 5
_DOMAIN = "rag4c/knowledgeops/backfill/v1"
_CURSOR_FIELDS = (
    "v",
    "dataset_generation",
    "snapshot_started_at",
    "snapshot_count",
    "snapshot_fingerprint",
    "upper_created_at",
    "upper_document_id",
    "last_created_at",
    "last_document_id",
)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _secret_bytes(report_secret: str) -> bytes:
    return validate_rollout_secret(report_secret)


def _hmac_hex(report_secret: str, *, domain: str, value: Any) -> str:
    message = domain.encode("utf-8") + b"\0" + _canonical(value)
    return hmac.new(_secret_bytes(report_secret), message, hashlib.sha256).hexdigest()


def _pseudonym(value: str, *, report_secret: str, kind: str) -> str:
    digest = _hmac_hex(report_secret, domain=f"{_DOMAIN}/report/{kind}", value=value)
    return f"ref-{digest[:20]}"


def _datetime_text(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat(timespec="microseconds")


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("invalid backfill cursor timestamp") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _dataset_generation(dataset: Dataset, *, tenant_id: str, report_secret: str) -> str:
    return _hmac_hex(
        report_secret,
        domain=f"{_DOMAIN}/cursor/dataset-generation",
        value={
            "tenant_id": tenant_id,
            "dataset_id": dataset.id,
            "created_at": _datetime_text(dataset.created_at),
            "status": dataset.status,
        },
    )


@dataclass(frozen=True)
class _ScanState:
    dataset_generation: str
    snapshot_started_at: str
    snapshot_count: int
    snapshot_fingerprint: str
    upper_created_at: str
    upper_document_id: str
    last_created_at: str = ""
    last_document_id: str = ""

    def unsigned_payload(self) -> dict[str, Any]:
        return {
            "v": _CURSOR_VERSION,
            "dataset_generation": self.dataset_generation,
            "snapshot_started_at": self.snapshot_started_at,
            "snapshot_count": self.snapshot_count,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "upper_created_at": self.upper_created_at,
            "upper_document_id": self.upper_document_id,
            "last_created_at": self.last_created_at,
            "last_document_id": self.last_document_id,
        }


def _encode_cursor(state: _ScanState, *, report_secret: str) -> str:
    unsigned = state.unsigned_payload()
    payload = {
        **unsigned,
        "signature": _hmac_hex(report_secret, domain=f"{_DOMAIN}/cursor/signature", value=unsigned),
    }
    return base64.urlsafe_b64encode(_canonical(payload)).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str, *, report_secret: str) -> _ScanState | None:
    _secret_bytes(report_secret)
    if not cursor:
        return None
    error_message = "invalid backfill cursor"
    payload = decode_canonical_base64url_json(cursor, error_message=error_message)
    if set(payload) != {*_CURSOR_FIELDS, "signature"}:
        raise ValueError(error_message)
    unsigned = {key: payload[key] for key in _CURSOR_FIELDS}
    expected_signature = _hmac_hex(
        report_secret, domain=f"{_DOMAIN}/cursor/signature", value=unsigned
    )
    authentication_error = "backfill cursor is not authenticated"
    validate_rollout_cursor_fields(
        unsigned,
        expected_version=_CURSOR_VERSION,
        error_message=authentication_error,
    )
    signature = validate_sha256_hmac(payload["signature"], error_message=authentication_error)
    if not hmac.compare_digest(signature, expected_signature):
        raise ValueError(authentication_error)
    state = _ScanState(
        dataset_generation=unsigned["dataset_generation"],
        snapshot_started_at=unsigned["snapshot_started_at"],
        snapshot_count=unsigned["snapshot_count"],
        snapshot_fingerprint=unsigned["snapshot_fingerprint"],
        upper_created_at=unsigned["upper_created_at"],
        upper_document_id=unsigned["upper_document_id"],
        last_created_at=unsigned["last_created_at"],
        last_document_id=unsigned["last_document_id"],
    )
    _parse_datetime(state.snapshot_started_at)
    if state.upper_created_at:
        _parse_datetime(state.upper_created_at)
    if state.last_created_at:
        _parse_datetime(state.last_created_at)
    return state


def _key_after(created_at: datetime, document_id: str) -> Any:
    return or_(
        Document.created_at > created_at,
        and_(Document.created_at == created_at, Document.id > document_id),
    )


def _key_at_or_before(created_at: datetime, document_id: str) -> Any:
    return or_(
        Document.created_at < created_at,
        and_(Document.created_at == created_at, Document.id <= document_id),
    )


def _scan_state(
    session: Session,
    *,
    tenant_id: str,
    dataset_id: str,
    cursor: str,
    report_secret: str,
) -> _ScanState:
    dataset = session.scalar(
        select(Dataset).where(Dataset.id == dataset_id, Dataset.tenant_id == tenant_id)
    )
    if dataset is None:
        raise ValueError("dataset does not exist in requested scope")
    current_generation = _dataset_generation(
        dataset, tenant_id=tenant_id, report_secret=report_secret
    )
    current_snapshot = capture_document_snapshot(
        session,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
    )
    resumed = _decode_cursor(cursor, report_secret=report_secret)
    if resumed is None:
        upper = session.scalar(
            select(Document)
            .where(
                Document.tenant_id == tenant_id,
                Document.dataset_id == dataset_id,
            )
            .order_by(Document.created_at.desc(), Document.id.desc())
            .limit(1)
        )
        return _ScanState(
            dataset_generation=current_generation,
            snapshot_started_at=_datetime_text(datetime.now(timezone.utc).replace(tzinfo=None)),
            snapshot_count=current_snapshot.count,
            snapshot_fingerprint=current_snapshot.fingerprint,
            upper_created_at=_datetime_text(upper.created_at) if upper else "",
            upper_document_id=upper.id if upper else "",
        )
    if resumed.dataset_generation != current_generation:
        raise ValueError("dataset generation changed during rollout scan")
    if resumed.snapshot_count != current_snapshot.count or not hmac.compare_digest(
        resumed.snapshot_fingerprint,
        current_snapshot.fingerprint,
    ):
        raise ValueError("rollout scan snapshot assumptions were violated")
    return resumed


def _assert_scan_state_current(
    chunk_catalog: ChunkCatalog,
    *,
    state: _ScanState,
    tenant_id: str,
    dataset_id: str,
    report_secret: str,
) -> None:
    """Re-read snapshot and dataset generation in a fresh transaction."""
    with Session(chunk_catalog.engine, expire_on_commit=False) as session:
        dataset = session.scalar(
            select(Dataset).where(Dataset.id == dataset_id, Dataset.tenant_id == tenant_id)
        )
        if dataset is None:
            raise ValueError("dataset does not exist in requested scope")
        if state.dataset_generation != _dataset_generation(
            dataset, tenant_id=tenant_id, report_secret=report_secret
        ):
            raise ValueError("dataset generation changed during rollout scan")
        current = capture_document_snapshot(session, tenant_id=tenant_id, dataset_id=dataset_id)
    if state.snapshot_count != current.count or not hmac.compare_digest(
        state.snapshot_fingerprint, current.fingerprint
    ):
        raise ValueError("rollout scan snapshot assumptions were violated")


def _chunk_role(
    chunk: Any,
    metadata: dict[str, Any],
    *,
    referenced_parent_ids: set[str],
) -> str:
    explicit = str(metadata.get("chunk_role") or "").lower()
    if explicit in {"parent", "child", "flat"}:
        return explicit
    chunk_id = str(chunk.chunk_id)
    if chunk_id in referenced_parent_ids:
        return "parent"
    if getattr(chunk, "parent_chunk_id", None):
        return "child"
    # The legacy chunker marks short standalone sections as is_parent=true.
    # They remain searchable flat chunks unless an actual child references them.
    return "flat"


def _context_header(metadata: dict[str, Any]) -> str:
    for key in ("context_header", "context"):
        value = metadata.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _enabled(metadata: dict[str, Any]) -> bool:
    if "enabled" in metadata:
        return bool(metadata["enabled"])
    if "disabled" in metadata:
        return not bool(metadata["disabled"])
    return True


def _expected_document_revision(document: Document) -> int:
    current = max(0, int(document.content_revision or 0))
    if current:
        return current
    return max(0, int(document.desired_index_revision or 0))


@dataclass(frozen=True)
class BackfillReport:
    tenant_id: str
    dataset_id: str
    mode: str
    documents_scanned: int
    chunks_scanned: int
    would_create: int
    created: int
    repaired_existing: int
    skipped_existing: int
    skipped_unsafe: int
    existing_drift_ids: tuple[str, ...]
    scope_mismatch_ids: tuple[str, ...]
    hash_mismatch_ids: tuple[str, ...]
    document_revision_mismatch_ids: tuple[str, ...]
    snapshot_count: int
    batch_size: int
    manifest_hash: str
    post_snapshot_excluded: int
    next_cursor: str
    complete: bool

    @property
    def has_drift(self) -> bool:
        return bool(
            self.existing_drift_ids
            or self.scope_mismatch_ids
            or self.hash_mismatch_ids
            or self.document_revision_mismatch_ids
        )

    def to_summary(self, report_secret: str) -> dict[str, Any]:
        _secret_bytes(report_secret)

        def refs(values: tuple[str, ...]) -> list[str]:
            return [
                _pseudonym(value, report_secret=report_secret, kind="chunk") for value in values
            ]

        cost = estimate_rollout_cost(document_count=self.snapshot_count, batch_size=self.batch_size)
        return {
            "tenant_id": _pseudonym(self.tenant_id, report_secret=report_secret, kind="tenant"),
            "dataset_id": _pseudonym(self.dataset_id, report_secret=report_secret, kind="dataset"),
            "mode": self.mode,
            "documents_scanned": self.documents_scanned,
            "chunks_scanned": self.chunks_scanned,
            "would_create": self.would_create,
            "created": self.created,
            "repaired_existing": self.repaired_existing,
            "skipped_existing": self.skipped_existing,
            "skipped_unsafe": self.skipped_unsafe,
            "existing_drift_ids": refs(self.existing_drift_ids),
            "scope_mismatch_ids": refs(self.scope_mismatch_ids),
            "hash_mismatch_ids": refs(self.hash_mismatch_ids),
            "document_revision_mismatch_ids": refs(self.document_revision_mismatch_ids),
            "manifest_ref": "ref-"
            + _hmac_hex(
                report_secret,
                domain=f"{_DOMAIN}/report/manifest-ref",
                value=self.manifest_hash,
            ),
            "snapshot_validation": {
                "estimated_batches": cost.batch_count,
                "fingerprint_scans": cost.fingerprint_scan_count,
                "estimated_rows_scanned": cost.estimated_rows_scanned,
                "warning": cost.warning,
            },
            "post_snapshot_excluded": self.post_snapshot_excluded,
            "resume_cursor_ref": (
                _hmac_hex(
                    report_secret,
                    domain=f"{_DOMAIN}/report/resume-cursor-ref",
                    value=self.next_cursor,
                )[:20]
                if self.next_cursor
                else ""
            ),
            "complete": self.complete,
            "has_drift": self.has_drift,
        }


def _existing_matches(
    head: ChunkHead,
    *,
    chunk: Any,
    document: Document,
    metadata: dict[str, Any],
    role: str,
    enabled: bool,
    actual_hash: str,
    context_header: str,
    fallback_index: int,
) -> bool:
    return bool(
        head.tenant_id == document.tenant_id
        and head.dataset_id == document.dataset_id
        and head.document_id == document.id
        and head.parent_chunk_id == getattr(chunk, "parent_chunk_id", None)
        and head.chunk_index == int(metadata.get("chunk_index", fallback_index))
        and head.chunk_role == role
        and head.document_revision == max(0, int(chunk.document_revision or 0))
        and head.content_revision == max(0, int(chunk.content_revision or 0))
        and head.content_hash == actual_hash
        and head.source_content == str(chunk.text)
        and head.content == str(chunk.text)
        and head.enabled is enabled
        and dict(head.chunk_metadata or {}) == metadata
        and head.context_header == context_header
    )


def _write_head(
    chunk_catalog: ChunkCatalog,
    *,
    chunk: Any,
    document: Document,
    fallback_index: int,
    metadata: dict[str, Any],
    role: str,
    enabled: bool,
    actual_hash: str,
    context_header: str,
) -> bool:
    """Create or deterministically repair one backfill head in one transaction."""
    chunk_id = str(chunk.chunk_id)
    content_revision = max(0, int(chunk.content_revision or 0))
    with Session(chunk_catalog.engine, expire_on_commit=False) as session:
        current_document = session.scalar(
            select(Document)
            .where(
                Document.id == document.id,
                Document.tenant_id == document.tenant_id,
                Document.dataset_id == document.dataset_id,
            )
            .with_for_update()
        )
        if current_document is None or any(
            (
                getattr(current_document, field) != getattr(document, field)
                for field in (
                    "created_at",
                    "updated_at",
                    "content_revision",
                    "desired_index_revision",
                    "status",
                )
            )
        ):
            raise ValueError("rollout scan snapshot assumptions were violated")
        head = session.get(ChunkHead, chunk_id)
        created = head is None
        if head is None:
            head = ChunkHead(id=chunk_id)
            session.add(head)
        head.tenant_id = document.tenant_id
        head.dataset_id = document.dataset_id
        head.document_id = document.id
        head.parent_chunk_id = getattr(chunk, "parent_chunk_id", None)
        head.chunk_index = int(metadata.get("chunk_index", fallback_index))
        head.chunk_role = role
        head.document_revision = max(0, int(chunk.document_revision or 0))
        head.content_revision = content_revision
        head.source_content = str(chunk.text)
        head.content = str(chunk.text)
        head.content_hash = actual_hash
        head.enabled = enabled
        head.desired_index_revision = content_revision
        # 停用（墓碑）头与 worker 的 delete 语义对齐：投影里已无此 chunk 就是
        # 期望态，同样记 ready/追平，否则墓碑的 projection_pending 恒真。
        # 前提是遗留部署没有 enabled=False 的向量仍驻留在 milvus（生产管线
        # 不产这种元数据）；若真有，ready 只是与物理状态脱节，不产生写放大。
        if role != "parent":
            head.indexed_revision = content_revision
            head.index_status = "ready"
        else:
            head.indexed_revision = 0
            head.index_status = "not_indexed"
        head.editor_id = "knowledgeops-backfill"
        head.edit_source = "backfill"
        head.context_header = context_header
        head.chunk_metadata = metadata
        session.commit()
        return created


@dataclass(frozen=True)
class _HeadWritePlan:
    chunk: Any
    document: Document
    fallback_index: int
    metadata: dict[str, Any]
    role: str
    enabled: bool
    actual_hash: str
    context_header: str


def backfill_chunk_authority(
    chunk_catalog: ChunkCatalog,
    milvus: Any,
    *,
    tenant_id: str,
    dataset_id: str,
    report_secret: str,
    cursor: str = "",
    batch_size: int = DEFAULT_ROLLOUT_BATCH_SIZE,
    apply: bool = False,
    allow_unsafe_mismatches: bool = False,
) -> BackfillReport:
    """Backfill one deterministic document batch within a tenant/dataset scope."""
    if not tenant_id or not dataset_id:
        raise ValueError("tenant_id and dataset_id are required")
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    with rollout_snapshot_session(chunk_catalog.engine) as session:
        state = _scan_state(
            session,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            cursor=cursor,
            report_secret=report_secret,
        )
        filters: list[Any] = [
            Document.tenant_id == tenant_id,
            Document.dataset_id == dataset_id,
        ]
        if state.last_created_at:
            filters.append(
                _key_after(_parse_datetime(state.last_created_at), state.last_document_id)
            )
        if state.upper_created_at:
            filters.append(
                _key_at_or_before(
                    _parse_datetime(state.upper_created_at),
                    state.upper_document_id,
                )
            )
        else:
            filters.append(Document.id == "__empty_snapshot__")
        selected = list(
            session.scalars(
                select(Document)
                .where(*filters)
                .order_by(Document.created_at, Document.id)
                .limit(batch_size + 1)
            )
        )
        documents = selected[:batch_size]
        has_more = len(selected) > batch_size

        chunks_scanned = would_create = skipped = skipped_unsafe = 0
        hash_mismatches: list[str] = []
        revision_mismatches: list[str] = []
        scope_mismatches: list[str] = []
        existing_drift: list[str] = []
        manifest: list[dict[str, Any]] = []
        write_plans: list[_HeadWritePlan] = []

        for document in documents:
            chunks = sorted(
                milvus.query_chunks_by_doc(document.id, tenant_id=document.tenant_id),
                key=lambda item: str(item.chunk_id),
            )
            referenced_parent_ids = {
                str(chunk.parent_chunk_id)
                for chunk in chunks
                if getattr(chunk, "parent_chunk_id", None)
            }
            document_manifest: list[dict[str, Any]] = []
            expected_revision = _expected_document_revision(document)
            for fallback_index, chunk in enumerate(chunks):
                chunks_scanned += 1
                chunk_id = str(chunk.chunk_id)
                metadata = dict(chunk.metadata or {})
                role = _chunk_role(chunk, metadata, referenced_parent_ids=referenced_parent_ids)
                enabled = _enabled(metadata)
                context_header = _context_header(metadata)
                actual_hash = hashlib.sha256(str(chunk.text).encode("utf-8")).hexdigest()
                stored_hash_matches = str(chunk.text_hash or "") == actual_hash
                revision_matches = max(0, int(chunk.document_revision or 0)) == expected_revision
                scope_ok = bool(
                    chunk.doc_id == document.id
                    and chunk.tenant_id == document.tenant_id
                    and chunk.dataset_id == document.dataset_id
                )
                if not stored_hash_matches:
                    hash_mismatches.append(chunk_id)
                if not revision_matches:
                    revision_mismatches.append(chunk_id)
                if not scope_ok:
                    scope_mismatches.append(chunk_id)
                document_manifest.append(
                    {
                        "chunk_id": chunk_id,
                        "parent_chunk_id": str(chunk.parent_chunk_id or ""),
                        "chunk_index": int(metadata.get("chunk_index", fallback_index)),
                        "chunk_role": role,
                        "document_revision": max(0, int(chunk.document_revision or 0)),
                        "content_revision": max(0, int(chunk.content_revision or 0)),
                        "content_hash": actual_hash,
                        "stored_hash_matches": stored_hash_matches,
                        "document_revision_matches": revision_matches,
                        "enabled": enabled,
                        "context_hash": hashlib.sha256(context_header.encode("utf-8")).hexdigest(),
                        "metadata_hash": _digest(metadata),
                        "scope_ok": scope_ok,
                    }
                )
                if not scope_ok:
                    continue
                unsafe = not stored_hash_matches or not revision_matches
                if unsafe and not allow_unsafe_mismatches:
                    skipped_unsafe += 1
                    continue
                try:
                    head = chunk_catalog.get_head(chunk_id, session=session)
                except ChunkRevisionConflict:
                    head = None
                plan = _HeadWritePlan(
                    chunk=chunk,
                    document=document,
                    fallback_index=fallback_index,
                    metadata=metadata,
                    role=role,
                    enabled=enabled,
                    actual_hash=actual_hash,
                    context_header=context_header,
                )
                if head is None:
                    would_create += 1
                    if apply:
                        write_plans.append(plan)
                    continue
                if _existing_matches(
                    head,
                    chunk=chunk,
                    document=document,
                    metadata=metadata,
                    role=role,
                    enabled=enabled,
                    actual_hash=actual_hash,
                    context_header=context_header,
                    fallback_index=fallback_index,
                ):
                    skipped += 1
                    continue
                existing_drift.append(chunk_id)
                if apply:
                    write_plans.append(plan)
            manifest.append({"document_id": document.id, "chunks": document_manifest})

    _assert_scan_state_current(
        chunk_catalog,
        state=state,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        report_secret=report_secret,
    )

    created = repaired = 0
    for plan in write_plans:
        was_created = _write_head(
            chunk_catalog,
            chunk=plan.chunk,
            document=plan.document,
            fallback_index=plan.fallback_index,
            metadata=plan.metadata,
            role=plan.role,
            enabled=plan.enabled,
            actual_hash=plan.actual_hash,
            context_header=plan.context_header,
        )
        if was_created:
            created += 1
        else:
            repaired += 1

    next_cursor = ""
    if has_more and documents:
        next_cursor = _encode_cursor(
            replace(
                state,
                last_created_at=_datetime_text(documents[-1].created_at),
                last_document_id=documents[-1].id,
            ),
            report_secret=report_secret,
        )
    return BackfillReport(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        mode="apply" if apply else "dry-run",
        documents_scanned=len(documents),
        chunks_scanned=chunks_scanned,
        would_create=would_create,
        created=created,
        repaired_existing=repaired,
        skipped_existing=skipped,
        skipped_unsafe=skipped_unsafe,
        existing_drift_ids=tuple(sorted(existing_drift)),
        scope_mismatch_ids=tuple(sorted(scope_mismatches)),
        hash_mismatch_ids=tuple(sorted(hash_mismatches)),
        document_revision_mismatch_ids=tuple(sorted(revision_mismatches)),
        snapshot_count=state.snapshot_count,
        batch_size=batch_size,
        manifest_hash=_digest(manifest),
        post_snapshot_excluded=0,
        next_cursor=next_cursor,
        complete=not has_more,
    )


def _runtime_dependencies() -> tuple[Any, Any, Any]:
    from config.settings import get_settings
    from core import catalog
    from core.milvus_client import RagMilvusClient

    settings = get_settings()
    return catalog.get_engine(), RagMilvusClient(settings.milvus), settings


def _runtime_safety_error(engine: Any, settings: Any, *, mutation: bool) -> str:
    from core.catalog_schema import CatalogSchemaError, verify_catalog_schema

    try:
        verify_catalog_schema(engine)
    except CatalogSchemaError as exc:
        return str(exc)
    catalog_settings = settings.catalog
    if str(catalog_settings.schema_mode) != "verify":
        return "catalog schema_mode must be verify"
    if mutation and str(catalog_settings.chunk_authority_mode) != "shadow":
        return "chunk authority mutation requires shadow rollout mode"
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--cursor", default="")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_ROLLOUT_BATCH_SIZE)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-unsafe-mismatches", action="store_true")
    parser.add_argument("--fail-on-drift", action="store_true")
    parser.add_argument("--emit-sensitive-resume-cursor", action="store_true")
    args = parser.parse_args(argv)

    report_secret = os.getenv(_REPORT_SECRET_ENV, "")
    if not report_secret.strip():
        print(
            json.dumps(
                {"status": "unsafe", "error": f"{_REPORT_SECRET_ENV} is required"},
                sort_keys=True,
            )
        )
        return 2
    try:
        _secret_bytes(report_secret)
    except ValueError as exc:
        print(json.dumps({"status": "unsafe", "error": str(exc)}, sort_keys=True))
        return 2
    engine, milvus, settings = _runtime_dependencies()
    safety_error = _runtime_safety_error(engine, settings, mutation=args.apply)
    if safety_error:
        print(json.dumps({"status": "unsafe", "error": safety_error}, sort_keys=True))
        return 2
    report = backfill_chunk_authority(
        ChunkCatalog(engine),
        milvus,
        tenant_id=args.tenant_id,
        dataset_id=args.dataset_id,
        report_secret=report_secret,
        cursor=args.cursor,
        batch_size=args.batch_size,
        apply=args.apply,
        allow_unsafe_mismatches=args.allow_unsafe_mismatches,
    )
    print(json.dumps(report.to_summary(report_secret), ensure_ascii=False, sort_keys=True))
    if args.emit_sensitive_resume_cursor and report.next_cursor:
        print(
            json.dumps({"sensitive_resume_cursor": report.next_cursor}, sort_keys=True),
            file=sys.stderr,
        )
    return 2 if args.fail_on_drift and report.has_drift else 0


if __name__ == "__main__":
    raise SystemExit(main())
