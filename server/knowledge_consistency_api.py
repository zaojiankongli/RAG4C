"""Authenticated, tenant-scoped KnowledgeOps consistency control-plane API.

Catalog reconciliation remains read-only. Milvus does not yet expose a projection
generation that can be fenced with the catalog snapshot, so projection counts are
explicitly best-effort and cannot authorize repair.
"""

from __future__ import annotations

import hmac
import os
import uuid
from collections import Counter
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, status
from fastapi.security import HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core import catalog
from core.chunk_catalog import ChunkCatalog
from core.index_operations import IndexOperationQueue, process_keyed_lock
from core.knowledge_governance import sanitize_audit_snapshot
from core.knowledge_permissions import KNOWLEDGE_MANAGE, KNOWLEDGE_READ
from models.orm import (
    ChunkHead,
    DataSourceRecord,
    Dataset,
    Document,
    DocumentDeleteOperation,
    DocumentIngestAttempt,
    IndexDeadLetter,
    IndexOperation,
    KnowledgeAuditEvent,
)
from scripts.reconcile_chunk_authority import ReconcileReport, reconcile_chunk_authority
from scripts.rollout_security import REPORT_SECRET_ENV, validate_rollout_secret
from scripts.rollout_snapshot import DEFAULT_ROLLOUT_BATCH_SIZE
from server.knowledge_auth import KnowledgeActor, require_knowledge_permission, resolve_path_dataset

_OPENAPI_BEARER = HTTPBearer(
    auto_error=False,
    scheme_name="KnowledgeBearerAuth",
    description="Actor-bound signed KnowledgeOps bearer token.",
)
router = APIRouter(
    prefix="/api/knowledge-bases/{dataset_id}/consistency",
    tags=["knowledge-consistency"],
    dependencies=[Depends(_OPENAPI_BEARER)],
)

_SAFE_ID = r"^[^\x00-\x1f\x7f]+$"
_REF_PATTERN = r"^ref-[0-9a-f]{64}$"
_MANIFEST_REF_PATTERN = r"^ref-[0-9a-f]{64}$"
_IDEMPOTENCY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
_REF_DOMAIN = b"rag4c:knowledge-consistency-ref:v1\x00"
_ORDINARY_TARGET_OPERATIONS = frozenset(
    {
        ("milvus_chunks", "upsert"),
        ("milvus_chunks", "delete"),
        ("milvus_chunks", "reconcile"),
        ("graph_projection", "upsert"),
        ("graph_projection", "delete"),
    }
)
_DELETE_TARGET_OPERATIONS = frozenset(
    {
        ("milvus_chunks", "delete_document"),
        ("graph_projection", "delete_document"),
        ("catalog_finalize", "finalize_document_delete"),
    }
)
_SUPPORTED_TARGET_OPERATIONS = _ORDINARY_TARGET_OPERATIONS | _DELETE_TARGET_OPERATIONS
DatasetId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_ID)]
DeadLetterRef = Annotated[str, Path(pattern=_REF_PATTERN)]

_READ = require_knowledge_permission(KNOWLEDGE_READ, resolve_path_dataset("dataset_id"))
_MANAGE = require_knowledge_permission(KNOWLEDGE_MANAGE, resolve_path_dataset("dataset_id"))
ReadActor = Annotated[KnowledgeActor, Depends(_READ)]
ManageActor = Annotated[KnowledgeActor, Depends(_MANAGE)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ErrorDetail(StrictModel):
    code: str
    message: str


class ErrorEnvelope(StrictModel):
    error: ErrorDetail


class ConsistencyCounts(StrictModel):
    documents_scanned: int
    authoritative_heads: int
    projection_chunks: int
    missing_chunks: int
    stale_chunks: int
    orphaned_chunks: int
    blocked_documents: int


class DriftCategories(StrictModel):
    missing: int
    stale: int
    orphaned: int
    blocked: int
    stale_reasons: dict[str, int]


class QAAuthorityCounts(StrictModel):
    """Catalog-only QA retrieval authority (not a Milvus projection claim)."""

    total: int
    effective_retrieval: int
    pending_review: int
    rejected: int
    expired: int
    retrieval_disabled: int
    note: str


class ConsistencySummaryResponse(StrictModel):
    mode: Literal["report-only"]
    counts: ConsistencyCounts
    drift_categories: DriftCategories
    manifest_ref: str
    complete: Literal[False]
    confirmable: Literal[False]
    snapshot_guarantee: Literal["catalog_only"]
    best_effort: Literal[True]
    has_drift: bool
    #: None when QA catalog scan failed — never invent zeros for production authority.
    qa_authority: QAAuthorityCounts | None = None


class DeadLetterItem(StrictModel):
    dead_letter_ref: str
    operation_ref: str
    document_ref: str
    target_store: str
    operation: str
    retry_count: int
    failed_at: datetime
    requeued_operation_ref: str | None = None


class DeadLetterListResponse(StrictModel):
    items: list[DeadLetterItem]
    count: int


class DeadLetterRequeueResponse(StrictModel):
    status: Literal["enqueued", "already_requeued"]
    dead_letter_ref: str
    operation_ref: str


class RepairRequest(StrictModel):
    confirm_manifest_ref: str = Field(pattern=_MANIFEST_REF_PATTERN)


class DeadLetterRequeueRequest(StrictModel):
    operator_note: str = Field(default="", max_length=512)


def _error_responses(*codes: int) -> dict[int, dict[str, Any]]:
    descriptions = {
        401: "Missing or invalid actor bearer token.",
        403: "Actor lacks permission, dataset scope, or repair is disabled.",
        404: "Scoped dead letter does not exist.",
        409: "Reference collision, lineage conflict, or requeue conflict.",
        422: "Request validation failed.",
        501: "No atomic repair adapter is available.",
        503: "Consistency authority or report secret is unavailable.",
    }
    return {code: {"model": ErrorEnvelope, "description": descriptions[code]} for code in codes}


def _engine(request: Request) -> Any:
    configured = getattr(request.app.state, "knowledge_consistency_engine", None)
    if configured is not None:
        return configured
    configured = getattr(request.app.state, "knowledge_auth_engine", None)
    return configured if configured is not None else catalog.get_engine()


def _report_secret(request: Request) -> str:
    configured = getattr(request.app.state, "knowledge_consistency_report_secret", None)
    value = str(configured if configured is not None else os.getenv(REPORT_SECRET_ENV, ""))
    try:
        validate_rollout_secret(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "knowledge_consistency_secret_unavailable",
                "message": "一致性报告密钥未安全配置",
            },
        ) from exc
    return value


def _repair_enabled(request: Request) -> bool:
    configured = getattr(request.app.state, "knowledge_consistency_repair_enabled", None)
    if configured is not None:
        return configured is True
    value = os.getenv("RAG4C_KNOWLEDGE_CONSISTENCY_REPAIR_ENABLED", "")
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _require_repair_enabled(request: Request) -> None:
    if not _repair_enabled(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "knowledge_consistency_repair_disabled",
                "message": "在线一致性修复未启用",
            },
        )


RepairEnabled = Annotated[None, Depends(_require_repair_enabled)]


def _default_reconcile(
    request: Request,
    *,
    tenant_id: str,
    dataset_id: str,
    report_secret: str,
    batch_size: int,
    repair: bool,
) -> ReconcileReport:
    from config.settings import get_settings
    from core.milvus_client import RagMilvusClient

    engine = _engine(request)
    milvus = getattr(request.app.state, "knowledge_consistency_milvus", None)
    if milvus is None:
        milvus = RagMilvusClient(get_settings().milvus)
    return reconcile_chunk_authority(
        ChunkCatalog(engine),
        milvus,
        IndexOperationQueue(engine),
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        report_secret=report_secret,
        cursor="",
        batch_size=batch_size,
        repair=repair,
    )


def _run_report(
    request: Request,
    *,
    actor: KnowledgeActor,
    dataset_id: str,
    batch_size: int = DEFAULT_ROLLOUT_BATCH_SIZE,
) -> tuple[ReconcileReport, str]:
    report_secret = _report_secret(request)
    configured = getattr(request.app.state, "knowledge_consistency_reconciler", None)
    if configured is None:
        report = _default_reconcile(
            request,
            tenant_id=actor.tenant_id,
            dataset_id=dataset_id,
            report_secret=report_secret,
            batch_size=batch_size,
            repair=False,
        )
    else:
        report = configured(
            tenant_id=actor.tenant_id,
            dataset_id=dataset_id,
            report_secret=report_secret,
            batch_size=batch_size,
            repair=False,
        )
    if report.mode != "report-only" or int(report.enqueued or 0) != 0:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "knowledge_consistency_read_only_violation",
                "message": "一致性只读检查未能证明零写入",
            },
        )
    return report, report_secret


def _counts(report: ReconcileReport) -> dict[str, int]:
    return {
        "documents_scanned": int(report.documents_scanned),
        "authoritative_heads": int(report.authoritative_heads),
        "projection_chunks": int(report.projection_chunks),
        "missing_chunks": len(report.missing_ids),
        "stale_chunks": len(report.stale_ids),
        "orphaned_chunks": len(report.orphaned_ids),
        "blocked_documents": len(report.repair_blocked_document_ids),
    }


def _stale_reason_counts(report: ReconcileReport) -> dict[str, int]:
    reasons: Counter[str] = Counter()
    for values in report.stale_reasons.values():
        reasons.update(str(value) for value in values)
    return dict(sorted(reasons.items()))


def _manifest_ref(report: ReconcileReport, report_secret: str) -> str:
    summary = report.to_summary(report_secret)
    value = summary.get("manifest_ref")
    if not isinstance(value, str) or not value.startswith("ref-"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "knowledge_consistency_manifest_unavailable",
                "message": "一致性清单引用生成失败",
            },
        )
    return value


def _qa_authority_counts(engine: Any, tenant_id: str, dataset_id: str) -> dict[str, Any]:
    """Catalog-only QA retrieval authority counts (no Milvus projection claim)."""
    from datetime import datetime

    from sqlalchemy import func, or_, select
    from sqlalchemy.orm import Session

    from models.orm import QAKnowledge

    now = datetime.utcnow()
    with Session(engine, expire_on_commit=False) as session:
        base = (
            QAKnowledge.tenant_id == tenant_id,
            QAKnowledge.dataset_id == dataset_id,
        )

        def _count(*extra) -> int:
            return int(
                session.scalar(select(func.count()).select_from(QAKnowledge).where(*base, *extra))
                or 0
            )

        total = _count()
        effective = _count(
            QAKnowledge.review_status == "approved",
            QAKnowledge.lifecycle_state == "active",
            QAKnowledge.retrieval_enabled.is_(True),
            or_(QAKnowledge.effective_from.is_(None), QAKnowledge.effective_from <= now),
            or_(QAKnowledge.expires_at.is_(None), QAKnowledge.expires_at > now),
        )
        pending = _count(QAKnowledge.review_status == "pending")
        rejected = _count(QAKnowledge.review_status == "rejected")
        expired = _count(QAKnowledge.lifecycle_state == "expired")
        retrieval_disabled = _count(
            QAKnowledge.review_status == "approved",
            QAKnowledge.lifecycle_state == "active",
            QAKnowledge.retrieval_enabled.is_(False),
        )
        return {
            "total": total,
            "effective_retrieval": effective,
            "pending_review": pending,
            "rejected": rejected,
            "expired": expired,
            "retrieval_disabled": retrieval_disabled,
            "note": "QA 权威来自 MySQL Catalog；不投影 Milvus，不作为可确认修复对象",
        }


def _summary_payload(
    report: ReconcileReport,
    report_secret: str,
    *,
    qa_authority: dict[str, Any] | None = None,
) -> dict[str, Any]:
    counts = _counts(report)
    return {
        "mode": "report-only",
        "counts": counts,
        "drift_categories": {
            "missing": counts["missing_chunks"],
            "stale": counts["stale_chunks"],
            "orphaned": counts["orphaned_chunks"],
            "blocked": counts["blocked_documents"],
            "stale_reasons": _stale_reason_counts(report),
        },
        "manifest_ref": _manifest_ref(report, report_secret),
        # The catalog scan is stable, but Milvus has no projection generation
        # fence yet. Never promote these mixed-store counts to a confirmable plan.
        "complete": False,
        "confirmable": False,
        "snapshot_guarantee": "catalog_only",
        "best_effort": True,
        "has_drift": bool(report.has_drift),
        "qa_authority": qa_authority,
    }


def _resource_ref(value: str, *, report_secret: str, kind: str) -> str:
    key = validate_rollout_secret(report_secret)
    message = _REF_DOMAIN + kind.encode("utf-8") + b"\x00" + str(value).encode("utf-8")
    return "ref-" + hmac.new(key, message, "sha256").hexdigest()


def _dead_letter_payload(letter: IndexDeadLetter, report_secret: str) -> dict[str, Any]:
    return {
        "dead_letter_ref": _resource_ref(
            letter.id, report_secret=report_secret, kind="dead-letter"
        ),
        "operation_ref": _resource_ref(
            letter.operation_id, report_secret=report_secret, kind="operation"
        ),
        "document_ref": _resource_ref(
            letter.document_id, report_secret=report_secret, kind="document"
        ),
        "target_store": str(letter.target_store),
        "operation": str(letter.operation),
        "retry_count": int(letter.retry_count),
        "failed_at": letter.failed_at,
        "requeued_operation_ref": (
            _resource_ref(
                letter.requeued_to_operation_id,
                report_secret=report_secret,
                kind="operation",
            )
            if letter.requeued_to_operation_id
            else None
        ),
    }


def _scope_conflict() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "knowledge_consistency_dead_letter_scope_conflict",
            "message": "死信与原始投影操作的作用域不一致",
        },
    )


def _validate_operation_scope(
    letter: IndexDeadLetter,
    operation: IndexOperation,
    *,
    tenant_id: str,
    dataset_id: str,
) -> None:
    expected = (tenant_id, dataset_id, letter.document_id, letter.attempt_id)
    if (letter.tenant_id, letter.dataset_id, letter.document_id, letter.attempt_id) != expected:
        raise _scope_conflict()
    if (
        operation.tenant_id,
        operation.dataset_id,
        operation.document_id,
        operation.attempt_id,
    ) != expected:
        raise _scope_conflict()


def _target_conflict() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "knowledge_consistency_dead_letter_target_conflict",
            "message": "原始投影操作的目标与操作类型组合不受支持",
        },
    )


def _lineage_conflict(message: str = "死信原始投影操作的权威谱系不一致") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "knowledge_consistency_dead_letter_lineage_conflict",
            "message": message,
        },
    )


def _non_negative_int(value: Any) -> bool:
    return type(value) is int and value >= 0


def _identifier_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and bool(item) for item in value)


def _validate_dataset_generation_payload(
    payload: dict[str, Any],
    *,
    dataset: Dataset,
    document: Document,
    session: Session,
    lock: bool,
) -> None:
    value = payload.get("dataset_generation")
    if not _non_negative_int(value) or value != int(dataset.mutation_generation or 0):
        raise _lineage_conflict("投影 payload 的 dataset generation 不一致")
    if "source_generation" not in payload:
        return
    source_generation = payload.get("source_generation")
    if not _non_negative_int(source_generation) or not document.source_id:
        raise _lineage_conflict("投影 payload 的 source generation 无效")
    source = session.get(DataSourceRecord, document.source_id, with_for_update=lock)
    if (
        source is None
        or source.tenant_id != document.tenant_id
        or source.dataset_id != document.dataset_id
        or int(source.mutation_generation or 0) != source_generation
    ):
        raise _lineage_conflict("投影 payload 的 source generation 不一致")


def _validate_chunk_mutation_payload(
    session: Session,
    *,
    operation: IndexOperation,
    attempt: DocumentIngestAttempt,
    expected_mutation: Literal["edit", "delete"],
    lock: bool,
) -> None:
    payload = dict(operation.payload or {})
    required = {
        "chunk_id",
        "chunk_ids",
        "expected_content_revision",
        "mutation",
        "document_generation",
        "dataset_generation",
    }
    if set(payload) != required or attempt.attempt_kind != "chunk_mutation":
        raise _lineage_conflict("切片变更投影 payload 或 attempt kind 无效")
    chunk_id = payload.get("chunk_id")
    expected_revision = payload.get("expected_content_revision")
    if (
        payload.get("mutation") != expected_mutation
        or not isinstance(chunk_id, str)
        or not chunk_id
        or payload.get("chunk_ids") != [chunk_id]
        or not _non_negative_int(expected_revision)
        or payload.get("document_generation") != int(operation.document_generation or 0)
    ):
        raise _lineage_conflict("切片变更投影 fence payload 无效")
    head = session.get(ChunkHead, chunk_id, with_for_update=lock)
    if (
        head is None
        or head.tenant_id != operation.tenant_id
        or head.dataset_id != operation.dataset_id
        or head.document_id != operation.document_id
        or int(head.document_revision or 0) != int(operation.target_revision or 0)
        or int(head.content_revision or 0) != expected_revision
        or head.chunk_role == "parent"
        or (expected_mutation == "edit" and not bool(head.enabled))
        or (expected_mutation == "delete" and bool(head.enabled))
    ):
        raise _lineage_conflict("切片变更投影与 canonical ChunkHead 不一致")


def _validate_full_upsert_payload(
    session: Session,
    *,
    operation: IndexOperation,
    document: Document,
    attempt: DocumentIngestAttempt,
    dataset: Dataset,
    lock: bool,
) -> None:
    payload = dict(operation.payload or {})
    required = {"chunk_ids", "document_generation", "dataset_generation"}
    allowed = required | {"source_generation"}
    if (
        not required.issubset(payload)
        or not set(payload).issubset(allowed)
        or attempt.attempt_kind not in {"ingest", "reindex", "source_sync", "restore"}
        or not _identifier_list(payload.get("chunk_ids"))
        or len(set(payload["chunk_ids"])) != len(payload["chunk_ids"])
        or payload.get("document_generation") != int(operation.document_generation or 0)
    ):
        raise _lineage_conflict("全量 upsert 投影 payload 或 attempt kind 无效")
    _validate_dataset_generation_payload(
        payload,
        dataset=dataset,
        document=document,
        session=session,
        lock=lock,
    )


def _valid_reconcile_payload(payload: dict[str, Any], operation: IndexOperation) -> bool:
    keys = set(payload)
    direct = {"document_id", "target_revision"}
    catalog_drift = {"missing_ids", "orphan_ids", "revision_mismatch_ids"}
    authority_drift = {
        "missing_ids",
        "orphaned_ids",
        "stale_ids",
        "stale_reasons",
        "manifest_hash",
    }
    if keys == direct:
        return payload.get("document_id") == operation.document_id and payload.get(
            "target_revision"
        ) == int(operation.target_revision or 0)
    if keys == catalog_drift:
        return all(_identifier_list(payload.get(key)) for key in catalog_drift)
    if keys != authority_drift:
        return False
    if not all(
        _identifier_list(payload.get(key)) for key in ("missing_ids", "orphaned_ids", "stale_ids")
    ):
        return False
    stale_reasons = payload.get("stale_reasons")
    manifest_hash = payload.get("manifest_hash")
    return bool(
        isinstance(stale_reasons, dict)
        and all(
            isinstance(key, str)
            and bool(key)
            and isinstance(values, list)
            and all(isinstance(value, str) and bool(value) for value in values)
            for key, values in stale_reasons.items()
        )
        and isinstance(manifest_hash, str)
        and len(manifest_hash) == 64
        and set(manifest_hash).issubset(set("0123456789abcdef"))
    )


def _validate_ordinary_operation_semantics(
    session: Session,
    *,
    operation: IndexOperation,
    document: Document,
    attempt: DocumentIngestAttempt,
    dataset: Dataset,
    lock: bool,
) -> None:
    if int(operation.target_revision or 0) != int(document.desired_index_revision or 0) or int(
        attempt.input_revision or 0
    ) != int(operation.target_revision or 0):
        raise _lineage_conflict("普通投影 target revision 与 canonical authority 不一致")
    payload = dict(operation.payload or {})
    if operation.operation == "delete":
        _validate_chunk_mutation_payload(
            session,
            operation=operation,
            attempt=attempt,
            expected_mutation="delete",
            lock=lock,
        )
        _validate_dataset_generation_payload(
            payload,
            dataset=dataset,
            document=document,
            session=session,
            lock=lock,
        )
        return
    if operation.operation == "upsert" and "mutation" in payload:
        _validate_chunk_mutation_payload(
            session,
            operation=operation,
            attempt=attempt,
            expected_mutation="edit",
            lock=lock,
        )
        _validate_dataset_generation_payload(
            payload,
            dataset=dataset,
            document=document,
            session=session,
            lock=lock,
        )
        return
    if operation.operation == "upsert":
        _validate_full_upsert_payload(
            session,
            operation=operation,
            document=document,
            attempt=attempt,
            dataset=dataset,
            lock=lock,
        )
        return
    if (
        operation.operation != "reconcile"
        or operation.target_store != "milvus_chunks"
        or not _valid_reconcile_payload(payload, operation)
    ):
        raise _lineage_conflict("reconcile 投影 payload 无效")


def _validate_canonical_authority(
    session: Session,
    *,
    letter: IndexDeadLetter,
    operation: IndexOperation,
    tenant_id: str,
    dataset_id: str,
    lock: bool,
) -> None:
    pair = (str(operation.target_store), str(operation.operation))
    if pair not in _SUPPORTED_TARGET_OPERATIONS:
        raise _target_conflict()
    if operation.status != "dead":
        raise _lineage_conflict("死信原始投影操作不是 dead 终态")

    document = session.get(Document, operation.document_id, with_for_update=lock)
    attempt = session.get(DocumentIngestAttempt, operation.attempt_id, with_for_update=lock)
    dataset = session.get(Dataset, dataset_id, with_for_update=lock)
    if document is None or attempt is None or dataset is None:
        raise _scope_conflict()
    if (document.tenant_id, document.dataset_id, document.id) != (
        tenant_id,
        dataset_id,
        operation.document_id,
    ) or (dataset.tenant_id, dataset.id) != (tenant_id, dataset_id):
        raise _scope_conflict()
    if (
        attempt.tenant_id,
        attempt.dataset_id,
        attempt.document_id,
        attempt.id,
    ) != (tenant_id, dataset_id, document.id, operation.attempt_id):
        raise _scope_conflict()

    generation = int(operation.document_generation or 0)
    if (
        int(document.mutation_generation or 0) != generation
        or int(attempt.document_generation or 0) != generation
    ):
        raise _lineage_conflict("投影操作、文档与入库尝试的 generation 不一致")

    if pair in _ORDINARY_TARGET_OPERATIONS:
        if operation.delete_operation_id is not None:
            raise _lineage_conflict("普通投影操作不得携带删除父操作")
        _validate_ordinary_operation_semantics(
            session,
            operation=operation,
            document=document,
            attempt=attempt,
            dataset=dataset,
            lock=lock,
        )
        return

    if not operation.delete_operation_id or attempt.attempt_kind != "document_delete":
        raise _lineage_conflict("删除投影操作缺少删除父操作或删除尝试")
    parent = session.get(
        DocumentDeleteOperation, operation.delete_operation_id, with_for_update=lock
    )
    if parent is None:
        raise _lineage_conflict("删除父操作不存在")
    if (
        parent.tenant_id,
        parent.dataset_id,
        parent.document_id,
        parent.attempt_id,
        int(parent.delete_generation or 0),
    ) != (tenant_id, dataset_id, document.id, attempt.id, generation):
        raise _lineage_conflict("删除父操作与投影操作谱系不一致")
    if int(operation.target_revision or 0) != generation:
        raise _lineage_conflict("删除投影 target revision 与 generation 不一致")
    payload = dict(operation.payload or {})
    if (
        payload.get("delete_operation_id") != parent.id
        or int(payload.get("document_generation") or -1) != generation
    ):
        raise _lineage_conflict("删除投影 payload 与父操作谱系不一致")


def _validate_requeue_lineage(original: IndexOperation, requeued: IndexOperation) -> None:
    if (
        requeued.tenant_id,
        requeued.dataset_id,
        requeued.document_id,
        requeued.attempt_id,
        requeued.target_store,
        requeued.operation,
        int(requeued.target_revision or 0),
        int(requeued.document_generation or 0),
        requeued.delete_operation_id,
        dict(requeued.payload or {}),
    ) != (
        original.tenant_id,
        original.dataset_id,
        original.document_id,
        original.attempt_id,
        original.target_store,
        original.operation,
        int(original.target_revision or 0),
        int(original.document_generation or 0),
        original.delete_operation_id,
        dict(original.payload or {}),
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "knowledge_consistency_dead_letter_lineage_conflict",
                "message": "死信重放目标与原始投影操作谱系不一致",
            },
        )


def _find_dead_letter_with_origin(
    session: Session,
    *,
    tenant_id: str,
    dataset_id: str,
    dead_letter_ref: str,
    report_secret: str,
    lock: bool,
) -> tuple[IndexDeadLetter, IndexOperation] | None:
    query = select(IndexDeadLetter).where(
        IndexDeadLetter.tenant_id == tenant_id,
        IndexDeadLetter.dataset_id == dataset_id,
    )
    if lock:
        query = query.with_for_update()
    matches = [
        letter
        for letter in session.scalars(query)
        if hmac.compare_digest(
            _resource_ref(letter.id, report_secret=report_secret, kind="dead-letter"),
            dead_letter_ref,
        )
    ]
    if len(matches) > 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "knowledge_consistency_ref_collision",
                "message": "脱敏引用发生碰撞，已拒绝操作",
            },
        )
    if not matches:
        return None
    letter = matches[0]
    operation_query = select(IndexOperation).where(IndexOperation.id == letter.operation_id)
    if lock:
        operation_query = operation_query.with_for_update()
    operation = session.scalar(operation_query)
    if operation is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "knowledge_consistency_requeue_conflict",
                "message": "死信的原始投影操作不存在",
            },
        )
    _validate_operation_scope(
        letter,
        operation,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
    )
    return letter, operation


def _matching_requeue_audits(
    session: Session,
    *,
    tenant_id: str,
    dataset_id: str,
    dead_letter_ref: str,
    operation_ref: str,
) -> list[KnowledgeAuditEvent]:
    rows = session.scalars(
        select(KnowledgeAuditEvent).where(
            KnowledgeAuditEvent.tenant_id == tenant_id,
            KnowledgeAuditEvent.dataset_id == dataset_id,
            KnowledgeAuditEvent.action == "consistency.dead_letter_requeued",
            KnowledgeAuditEvent.resource_type == "index_dead_letter",
            KnowledgeAuditEvent.resource_id == dead_letter_ref,
        )
    )
    return [
        event
        for event in rows
        if dict(event.after_snapshot or {}).get("dead_letter_ref") == dead_letter_ref
        and dict(event.after_snapshot or {}).get("operation_ref") == operation_ref
    ]


def _require_single_requeue_audit(
    session: Session,
    *,
    tenant_id: str,
    dataset_id: str,
    dead_letter_ref: str,
    operation_ref: str,
) -> None:
    matches = _matching_requeue_audits(
        session,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        dead_letter_ref=dead_letter_ref,
        operation_ref=operation_ref,
    )
    if len(matches) != 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "knowledge_consistency_requeue_conflict",
                "message": "死信重放缺少唯一且已提交的审计事实",
            },
        )


def _linked_requeue_operation(
    session: Session,
    *,
    letter: IndexDeadLetter,
    original: IndexOperation,
    tenant_id: str,
    dataset_id: str,
    dead_letter_ref: str,
    report_secret: str,
) -> IndexOperation | None:
    if not letter.requeued_to_operation_id:
        return None
    operation = session.scalar(
        select(IndexOperation).where(
            IndexOperation.id == letter.requeued_to_operation_id,
            IndexOperation.tenant_id == tenant_id,
            IndexOperation.dataset_id == dataset_id,
        )
    )
    if operation is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "knowledge_consistency_requeue_conflict",
                "message": "死信重放目标已丢失",
            },
        )
    _validate_operation_scope(
        letter,
        operation,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
    )
    _validate_requeue_lineage(original, operation)
    operation_ref = _resource_ref(operation.id, report_secret=report_secret, kind="operation")
    _require_single_requeue_audit(
        session,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        dead_letter_ref=dead_letter_ref,
        operation_ref=operation_ref,
    )
    return operation


def _audit_requeue(
    session: Session,
    *,
    actor: KnowledgeActor,
    dataset_id: str,
    dead_letter_ref: str,
    operation_ref: str,
    original: IndexOperation,
) -> None:
    session.add(
        KnowledgeAuditEvent(
            id=f"audit-{uuid.uuid4().hex[:16]}",
            tenant_id=actor.tenant_id,
            dataset_id=dataset_id,
            actor_id=actor.account_id[:64],
            action="consistency.dead_letter_requeued",
            resource_type="index_dead_letter",
            resource_id=dead_letter_ref,
            after_snapshot=sanitize_audit_snapshot(
                {
                    "dead_letter_ref": dead_letter_ref,
                    "operation_ref": operation_ref,
                    "target_store": original.target_store,
                    "operation": original.operation,
                }
            ),
            request_id=actor.request_id[:128],
            request_ip=actor.request_ip[:64],
        )
    )


def _already_requeued(
    operation: IndexOperation,
    *,
    dead_letter_ref: str,
    report_secret: str,
) -> dict[str, str]:
    return {
        "status": "already_requeued",
        "dead_letter_ref": dead_letter_ref,
        "operation_ref": _resource_ref(
            operation.id,
            report_secret=report_secret,
            kind="operation",
        ),
    }


def _reload_requeue_winner(
    engine: Any,
    *,
    tenant_id: str,
    dataset_id: str,
    dead_letter_ref: str,
    report_secret: str,
) -> dict[str, str] | None:
    with Session(engine, expire_on_commit=False) as session:
        found = _find_dead_letter_with_origin(
            session,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            dead_letter_ref=dead_letter_ref,
            report_secret=report_secret,
            lock=False,
        )
        if found is None:
            return None
        letter, original = found
        operation = _linked_requeue_operation(
            session,
            letter=letter,
            original=original,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            dead_letter_ref=dead_letter_ref,
            report_secret=report_secret,
        )
        if operation is None:
            return None
        return _already_requeued(
            operation,
            dead_letter_ref=dead_letter_ref,
            report_secret=report_secret,
        )


@router.get(
    "/summary",
    response_model=ConsistencySummaryResponse,
    responses=_error_responses(401, 403, 422, 503),
)
def consistency_summary(
    dataset_id: DatasetId,
    request: Request,
    actor: ReadActor,
) -> dict[str, Any]:
    report, report_secret = _run_report(request, actor=actor, dataset_id=dataset_id)
    qa_authority: dict[str, Any] | None
    try:
        qa_authority = _qa_authority_counts(_engine(request), actor.tenant_id, dataset_id)
    except Exception:  # noqa: BLE001 - QA 计数失败不阻断文档投影摘要；不编造 0
        from core.observability import get_logger

        get_logger("server.knowledge_consistency_api").warning(
            "qa_authority counts unavailable for consistency summary", exc_info=True
        )
        qa_authority = None
    return _summary_payload(report, report_secret, qa_authority=qa_authority)


@router.post(
    "/repair-plan",
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    response_model=ErrorEnvelope,
    response_description="Projection snapshot authority unavailable.",
    responses=_error_responses(401, 403, 422),
)
def consistency_repair_plan(
    dataset_id: DatasetId,
    actor: ManageActor,
) -> dict[str, Any]:
    del dataset_id, actor
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "knowledge_consistency_projection_snapshot_unavailable",
            "message": "Milvus 投影尚无可与目录快照绑定的代次权威，无法生成可确认修复计划",
        },
    )


@router.post(
    "/repair",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    response_model=ErrorEnvelope,
    response_description="Atomic repair adapter unavailable.",
    responses=_error_responses(401, 403, 422, 503),
)
def consistency_repair(
    dataset_id: DatasetId,
    request: Request,
    actor: ManageActor,
    repair_enabled: RepairEnabled,
    body: RepairRequest,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=16,
            max_length=128,
            pattern=_IDEMPOTENCY_PATTERN,
        ),
    ],
) -> dict[str, Any]:
    del dataset_id, body, request, actor, repair_enabled, idempotency_key
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "code": "knowledge_consistency_repair_not_atomic",
            "message": "缺少可将修复操作与审计绑定到同一事务的原子接口",
        },
    )


@router.get(
    "/dead-letters",
    response_model=DeadLetterListResponse,
    responses=_error_responses(401, 403, 422, 503),
)
def list_consistency_dead_letters(
    dataset_id: DatasetId,
    request: Request,
    actor: ReadActor,
) -> dict[str, Any]:
    report_secret = _report_secret(request)
    with Session(_engine(request), expire_on_commit=False) as session:
        letters = list(
            session.scalars(
                select(IndexDeadLetter)
                .where(
                    IndexDeadLetter.tenant_id == actor.tenant_id,
                    IndexDeadLetter.dataset_id == dataset_id,
                )
                .order_by(IndexDeadLetter.failed_at, IndexDeadLetter.id)
            )
        )
    return {
        "items": [_dead_letter_payload(letter, report_secret) for letter in letters],
        "count": len(letters),
    }


@router.post(
    "/dead-letters/{dead_letter_ref}/requeue",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DeadLetterRequeueResponse,
    responses=_error_responses(401, 403, 404, 409, 422, 503),
)
def requeue_consistency_dead_letter(
    dataset_id: DatasetId,
    dead_letter_ref: DeadLetterRef,
    body: DeadLetterRequeueRequest,
    request: Request,
    actor: ManageActor,
) -> dict[str, Any]:
    engine = _engine(request)
    report_secret = _report_secret(request)
    dedup_key = f"consistency:dead-letter-requeue:{dead_letter_ref[4:]}"
    lock_key = f"consistency-dead-letter:{actor.tenant_id}:{dataset_id}:{dead_letter_ref}"
    with process_keyed_lock(lock_key):
        try:
            with Session(engine, expire_on_commit=False) as session:
                if session.bind is not None and session.bind.dialect.name == "sqlite":
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                found = _find_dead_letter_with_origin(
                    session,
                    tenant_id=actor.tenant_id,
                    dataset_id=dataset_id,
                    dead_letter_ref=dead_letter_ref,
                    report_secret=report_secret,
                    lock=True,
                )
                if found is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail={
                            "code": "knowledge_consistency_dead_letter_not_found",
                            "message": "死信不存在",
                        },
                    )
                letter, original = found
                linked = _linked_requeue_operation(
                    session,
                    letter=letter,
                    original=original,
                    tenant_id=actor.tenant_id,
                    dataset_id=dataset_id,
                    dead_letter_ref=dead_letter_ref,
                    report_secret=report_secret,
                )
                if linked is not None:
                    session.rollback()
                    return _already_requeued(
                        linked,
                        dead_letter_ref=dead_letter_ref,
                        report_secret=report_secret,
                    )

                _validate_canonical_authority(
                    session,
                    letter=letter,
                    operation=original,
                    tenant_id=actor.tenant_id,
                    dataset_id=dataset_id,
                    lock=True,
                )
                existing = session.scalar(
                    select(IndexOperation)
                    .where(
                        IndexOperation.dedup_key == dedup_key,
                        IndexOperation.tenant_id == actor.tenant_id,
                        IndexOperation.dataset_id == dataset_id,
                    )
                    .with_for_update()
                )
                response_status = "already_requeued" if existing is not None else "enqueued"
                operation = existing
                if operation is None:
                    operation = IndexOperation(
                        id=f"index-op-{uuid.uuid4().hex[:16]}",
                        tenant_id=original.tenant_id,
                        dataset_id=original.dataset_id,
                        document_id=original.document_id,
                        attempt_id=original.attempt_id,
                        target_store=original.target_store,
                        operation=original.operation,
                        dedup_key=dedup_key,
                        target_revision=int(original.target_revision or 0),
                        document_generation=int(original.document_generation or 0),
                        delete_operation_id=original.delete_operation_id,
                        payload=dict(original.payload or {}),
                        status="pending",
                        max_retries=max(1, int(letter.retry_count or original.max_retries or 1)),
                    )
                    session.add(operation)
                    session.flush()
                else:
                    _validate_operation_scope(
                        letter,
                        operation,
                        tenant_id=actor.tenant_id,
                        dataset_id=dataset_id,
                    )
                    _validate_requeue_lineage(original, operation)

                letter.requeued_to_operation_id = operation.id
                letter.operator_note = body.operator_note[:512]
                operation_ref = _resource_ref(
                    operation.id,
                    report_secret=report_secret,
                    kind="operation",
                )
                _audit_requeue(
                    session,
                    actor=actor,
                    dataset_id=dataset_id,
                    dead_letter_ref=dead_letter_ref,
                    operation_ref=operation_ref,
                    original=original,
                )
                session.commit()
                return {
                    "status": response_status,
                    "dead_letter_ref": dead_letter_ref,
                    "operation_ref": operation_ref,
                }
        except HTTPException:
            raise
        except IntegrityError:
            winner = _reload_requeue_winner(
                engine,
                tenant_id=actor.tenant_id,
                dataset_id=dataset_id,
                dead_letter_ref=dead_letter_ref,
                report_secret=report_secret,
            )
            if winner is not None:
                return winner
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "knowledge_consistency_requeue_conflict",
                    "message": "死信重放发生并发冲突",
                },
            ) from None


__all__ = ["router"]
