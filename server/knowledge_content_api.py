"""Authenticated KnowledgeOps document-version and QA APIs."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from core import catalog
from core.knowledge_content import (
    ContentConflict,
    ContentNotFound,
    KnowledgeContentRepository,
)
from core.knowledge_permissions import KNOWLEDGE_MANAGE, KNOWLEDGE_READ, KNOWLEDGE_WRITE
from models.orm import QAAlternativeQuestion, QAKnowledge, QANegativeQuestion
from server.knowledge_auth import (
    KnowledgeActor,
    require_knowledge_permission,
    resolve_path_dataset,
)

router = APIRouter(prefix="/api/knowledge-bases/{dataset_id}", tags=["knowledge-content"])

_SAFE_ID = r"^[^\x00-\x1f\x7f]+$"
DatasetId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_ID)]
DocumentId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_ID)]
QAId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_ID)]
AlternativeId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_ID)]

_READ = require_knowledge_permission(KNOWLEDGE_READ, resolve_path_dataset("dataset_id"))
_WRITE = require_knowledge_permission(KNOWLEDGE_WRITE, resolve_path_dataset("dataset_id"))
_MANAGE = require_knowledge_permission(KNOWLEDGE_MANAGE, resolve_path_dataset("dataset_id"))
ReadActor = Annotated[KnowledgeActor, Depends(_READ)]
WriteActor = Annotated[KnowledgeActor, Depends(_WRITE)]
ManageActor = Annotated[KnowledgeActor, Depends(_MANAGE)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class QAOrigin(StrEnum):
    manual = "manual"
    automatic = "automatic"
    qa_import = "import"


class ReviewStatus(StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class ReviewDecision(StrEnum):
    approved = "approved"
    rejected = "rejected"


class QALifecycle(StrEnum):
    active = "active"
    expired = "expired"
    delete_requested = "delete_requested"
    deleting = "deleting"
    delete_failed = "delete_failed"
    deleted = "deleted"


class DocumentVersionCreate(StrictModel):
    expected_current_revision: int = Field(ge=0, strict=True)
    expected_current_version_id: str | None = Field(default=None, min_length=1, max_length=64)
    source_identity: str = Field(min_length=1, max_length=512)
    source_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9A-Fa-f]{64}$")
    parser_policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    parser_metadata: dict[str, Any] = Field(default_factory=dict)
    source_content_ref: str = Field(default="", max_length=1024)
    change_reason: str = Field(default="", max_length=512)
    effective_from: datetime | None = None
    expires_at: datetime | None = None
    purge_after: datetime | None = None
    retrieval_enabled: bool | None = Field(default=None, strict=True)


class QACreate(StrictModel):
    question: str = Field(min_length=1, max_length=4096)
    answer: str = Field(min_length=1, max_length=65535)
    origin: QAOrigin = QAOrigin.manual
    source_document_id: str | None = Field(default=None, min_length=1, max_length=64)
    source_uri: str = Field(default="", max_length=1024)
    metadata: dict[str, Any] = Field(default_factory=dict)
    effective_from: datetime | None = None
    expires_at: datetime | None = None


class QAUpdate(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    question: str | None = Field(default=None, min_length=1, max_length=4096)
    answer: str | None = Field(default=None, min_length=1, max_length=65535)
    source_document_id: str | None = Field(default=None, min_length=1, max_length=64)
    source_uri: str | None = Field(default=None, max_length=1024)
    metadata: dict[str, Any] | None = None
    effective_from: datetime | None = None
    expires_at: datetime | None = None


class QARevisionRequest(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)


class QAReviewRequest(QARevisionRequest):
    decision: ReviewDecision


class QAAlternativeCreate(QARevisionRequest):
    question: str = Field(min_length=1, max_length=4096)


class QAImportItem(StrictModel):
    question: str = Field(min_length=1, max_length=4096)
    answer: str = Field(min_length=1, max_length=65535)
    alternatives: list[str] = Field(default_factory=list)
    negative_questions: list[str] = Field(default_factory=list)
    source_uri: str = Field(default="", max_length=1024)
    metadata: dict[str, Any] = Field(default_factory=dict)


class QAImportRequest(StrictModel):
    items: list[QAImportItem] = Field(min_length=1, max_length=200)
    origin: QAOrigin = QAOrigin.qa_import


class QABatchItem(StrictModel):
    qa_id: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(ge=1, strict=True)
    decision: ReviewDecision | None = None


class QABatchReviewRequest(StrictModel):
    items: list[QABatchItem] = Field(min_length=1, max_length=100)


class QARevisionRequestItem(StrictModel):
    qa_id: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(ge=1, strict=True)


class QABatchLifecycleRequest(StrictModel):
    items: list[QARevisionRequestItem] = Field(min_length=1, max_length=100)


def _repository() -> KnowledgeContentRepository:
    return KnowledgeContentRepository(catalog.get_engine())


def _raise_content(exc: Exception) -> None:
    if isinstance(exc, ContentNotFound):
        raise HTTPException(
            status_code=404,
            detail={"code": "knowledge_resource_not_found", "message": "资源不存在"},
        ) from exc
    if isinstance(exc, ContentConflict):
        raise HTTPException(
            status_code=409,
            detail={"code": "knowledge_content_conflict", "message": str(exc)},
        ) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(
            status_code=422,
            detail={"code": "knowledge_content_invalid", "message": str(exc)},
        ) from exc
    raise exc


def _version_payload(version: Any) -> dict[str, Any]:
    return {
        "id": version.id,
        "tenant_id": version.tenant_id,
        "dataset_id": version.dataset_id,
        "document_id": version.document_id,
        "revision": version.revision,
        "source_identity": version.source_identity,
        "source_hash": version.source_hash,
        "parser_policy_snapshot": version.parser_policy_snapshot or {},
        "parser_metadata": version.parser_metadata or {},
        # This is an opaque reference. The API never resolves or reads referenced content.
        "source_content_ref": version.source_content_ref,
        "created_by": version.created_by,
        "change_reason": version.change_reason,
        "created_at": version.created_at,
    }


def _alternative_payload(alternative: Any) -> dict[str, Any]:
    return {
        "id": alternative.id,
        "qa_id": alternative.qa_id,
        "question": alternative.question,
        "created_by": alternative.created_by,
        "created_at": alternative.created_at,
    }


def _negative_payload(negative: Any) -> dict[str, Any]:
    return {
        "id": negative.id,
        "qa_id": negative.qa_id,
        "question": negative.question,
        "created_by": negative.created_by,
        "created_at": negative.created_at,
    }


def _qa_payload(
    qa: Any,
    alternatives: list[Any] | None = None,
    negative_questions: list[Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": qa.id,
        "tenant_id": qa.tenant_id,
        "dataset_id": qa.dataset_id,
        "revision": qa.revision,
        "question": qa.question,
        "answer": qa.answer,
        "origin": qa.origin,
        "review_status": qa.review_status,
        "lifecycle_state": qa.lifecycle_state,
        "retrieval_enabled": qa.retrieval_enabled,
        "effective_from": qa.effective_from,
        "expires_at": qa.expires_at,
        "source_document_id": qa.source_document_id,
        "source_uri": qa.source_uri,
        "content_hash": getattr(qa, "content_hash", None),
        "import_batch_id": getattr(qa, "import_batch_id", None),
        "metadata": qa.metadata_json or {},
        "created_by": qa.created_by,
        "reviewed_by": qa.reviewed_by,
        "reviewed_at": qa.reviewed_at,
        "created_at": qa.created_at,
        "updated_at": qa.updated_at,
        "alternatives": [_alternative_payload(item) for item in (alternatives or [])],
        "negative_questions": [_negative_payload(item) for item in (negative_questions or [])],
    }


def _qa_with_alternatives(repository: KnowledgeContentRepository, qa: Any) -> dict[str, Any]:
    with Session(repository.engine, expire_on_commit=False) as session:
        alternatives = list(
            session.scalars(
                select(QAAlternativeQuestion)
                .where(
                    QAAlternativeQuestion.tenant_id == qa.tenant_id,
                    QAAlternativeQuestion.dataset_id == qa.dataset_id,
                    QAAlternativeQuestion.qa_id == qa.id,
                )
                .order_by(QAAlternativeQuestion.created_at, QAAlternativeQuestion.id)
            )
        )
        negatives = list(
            session.scalars(
                select(QANegativeQuestion)
                .where(
                    QANegativeQuestion.tenant_id == qa.tenant_id,
                    QANegativeQuestion.dataset_id == qa.dataset_id,
                    QANegativeQuestion.qa_id == qa.id,
                )
                .order_by(QANegativeQuestion.created_at, QANegativeQuestion.id)
            )
        )
    return _qa_payload(qa, alternatives, negatives)


@router.get("/documents/{document_id}/versions")
def list_document_versions(
    dataset_id: DatasetId,
    document_id: DocumentId,
    actor: ReadActor,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    try:
        rows = _repository().list_document_versions(
            actor.tenant_id, dataset_id, document_id, limit=limit
        )
    except Exception as exc:  # repository errors are mapped to the public contract
        _raise_content(exc)
    return {"items": [_version_payload(item) for item in rows], "count": len(rows)}


@router.post(
    "/documents/{document_id}/versions",
    status_code=status.HTTP_201_CREATED,
)
def create_document_version(
    dataset_id: DatasetId,
    document_id: DocumentId,
    body: DocumentVersionCreate,
    actor: WriteActor,
) -> dict[str, Any]:
    if "retrieval_enabled" in body.model_fields_set and body.retrieval_enabled is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "knowledge_content_invalid",
                "message": "retrieval_enabled 不得为 null",
            },
        )
    optional_fenced_fields = {
        "expected_current_version_id",
        "effective_from",
        "expires_at",
        "purge_after",
        "retrieval_enabled",
    }
    values = body.model_dump(exclude=optional_fenced_fields)
    for field_name in optional_fenced_fields & body.model_fields_set:
        values[field_name] = getattr(body, field_name)
    try:
        version = _repository().create_document_version(
            actor.tenant_id,
            dataset_id,
            document_id,
            **values,
            audit=actor.to_audit_context(),
        )
    except Exception as exc:
        _raise_content(exc)
    return _version_payload(version)


@router.get("/qa")
def list_qa(
    dataset_id: DatasetId,
    actor: ReadActor,
    review_status: Annotated[ReviewStatus | None, Query()] = None,
    lifecycle_state: Annotated[QALifecycle | None, Query()] = None,
    origin: Annotated[QAOrigin | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    repository = _repository()
    with Session(repository.engine, expire_on_commit=False) as session:
        query = select(QAKnowledge).where(
            QAKnowledge.tenant_id == actor.tenant_id,
            QAKnowledge.dataset_id == dataset_id,
        )
        if review_status is not None:
            query = query.where(QAKnowledge.review_status == review_status.value)
        if lifecycle_state is not None:
            query = query.where(QAKnowledge.lifecycle_state == lifecycle_state.value)
        if origin is not None:
            query = query.where(QAKnowledge.origin == origin.value)
        rows = list(
            session.scalars(
                query.order_by(QAKnowledge.created_at.desc(), QAKnowledge.id.desc()).limit(limit)
            )
        )
        qa_ids = [item.id for item in rows]
        alternatives = (
            list(
                session.scalars(
                    select(QAAlternativeQuestion)
                    .where(
                        QAAlternativeQuestion.tenant_id == actor.tenant_id,
                        QAAlternativeQuestion.dataset_id == dataset_id,
                        QAAlternativeQuestion.qa_id.in_(qa_ids),
                    )
                    .order_by(QAAlternativeQuestion.created_at, QAAlternativeQuestion.id)
                )
            )
            if qa_ids
            else []
        )
        negatives = (
            list(
                session.scalars(
                    select(QANegativeQuestion)
                    .where(
                        QANegativeQuestion.tenant_id == actor.tenant_id,
                        QANegativeQuestion.dataset_id == dataset_id,
                        QANegativeQuestion.qa_id.in_(qa_ids),
                    )
                    .order_by(QANegativeQuestion.created_at, QANegativeQuestion.id)
                )
            )
            if qa_ids
            else []
        )
    grouped: dict[str, list[Any]] = {qa_id: [] for qa_id in qa_ids}
    for alternative in alternatives:
        grouped[alternative.qa_id].append(alternative)
    grouped_negatives: dict[str, list[Any]] = {qa_id: [] for qa_id in qa_ids}
    for negative in negatives:
        grouped_negatives[negative.qa_id].append(negative)
    return {
        "items": [
            _qa_payload(item, grouped[item.id], grouped_negatives[item.id]) for item in rows
        ],
        "count": len(rows),
    }


@router.post("/qa", status_code=status.HTTP_201_CREATED)
def create_qa(dataset_id: DatasetId, body: QACreate, actor: WriteActor) -> dict[str, Any]:
    try:
        repository = _repository()
        qa = repository.create_qa(
            actor.tenant_id,
            dataset_id,
            **body.model_dump(),
            audit=actor.to_audit_context(),
        )
    except Exception as exc:
        _raise_content(exc)
    return _qa_with_alternatives(repository, qa)


@router.post("/qa/import")
def import_qa(
    dataset_id: DatasetId,
    body: QAImportRequest,
    actor: WriteActor,
) -> dict[str, Any]:
    try:
        result = _repository().import_qa_batch(
            actor.tenant_id,
            dataset_id,
            items=[item.model_dump() for item in body.items],
            origin=body.origin.value,
            audit=actor.to_audit_context(),
        )
    except Exception as exc:
        _raise_content(exc)
    return result


@router.post("/qa/batch/review")
def batch_review_qa(
    dataset_id: DatasetId,
    body: QABatchReviewRequest,
    actor: WriteActor,
) -> dict[str, Any]:
    items = []
    for item in body.items:
        if item.decision is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "knowledge_content_invalid",
                    "message": "batch review item requires decision",
                },
            )
        items.append(
            {
                "qa_id": item.qa_id,
                "expected_revision": item.expected_revision,
                "decision": item.decision.value,
            }
        )
    try:
        return _repository().batch_review_qa(
            actor.tenant_id,
            dataset_id,
            items=items,
            audit=actor.to_audit_context(),
            action="review",
        )
    except Exception as exc:
        _raise_content(exc)


@router.post("/qa/batch/expire")
def batch_expire_qa(
    dataset_id: DatasetId,
    body: QABatchLifecycleRequest,
    actor: ManageActor,
) -> dict[str, Any]:
    try:
        return _repository().batch_review_qa(
            actor.tenant_id,
            dataset_id,
            items=[item.model_dump() for item in body.items],
            audit=actor.to_audit_context(),
            action="expire",
        )
    except Exception as exc:
        _raise_content(exc)


@router.post("/qa/batch/restore")
def batch_restore_qa(
    dataset_id: DatasetId,
    body: QABatchLifecycleRequest,
    actor: ManageActor,
) -> dict[str, Any]:
    try:
        return _repository().batch_review_qa(
            actor.tenant_id,
            dataset_id,
            items=[item.model_dump() for item in body.items],
            audit=actor.to_audit_context(),
            action="restore",
        )
    except Exception as exc:
        _raise_content(exc)


@router.get("/qa/export")
def export_qa(
    dataset_id: DatasetId,
    actor: ReadActor,
    review_status: Annotated[ReviewStatus | None, Query()] = None,
    lifecycle_state: Annotated[QALifecycle | None, Query()] = None,
    origin: Annotated[QAOrigin | None, Query()] = None,
    format: Annotated[str, Query(pattern=r"^(json|csv)$")] = "json",
    limit: Annotated[int, Query(ge=1, le=500)] = 500,
) -> Any:
    payload = list_qa(
        dataset_id=dataset_id,
        actor=actor,
        review_status=review_status,
        lifecycle_state=lifecycle_state,
        origin=origin,
        limit=limit,
    )
    items = payload.get("items") or []
    if format == "json":
        return payload
    headers = [
        "id",
        "revision",
        "origin",
        "review_status",
        "lifecycle_state",
        "retrieval_enabled",
        "question",
        "answer",
        "source_uri",
        "content_hash",
        "import_batch_id",
        "alternatives",
        "negative_questions",
    ]
    lines = [",".join(headers)]
    for item in items:
        alts = "|".join(a.get("question") or "" for a in item.get("alternatives") or [])
        negs = "|".join(
            n.get("question") or "" for n in item.get("negative_questions") or []
        )
        row = [
            item.get("id") or "",
            str(item.get("revision") or ""),
            item.get("origin") or "",
            item.get("review_status") or "",
            item.get("lifecycle_state") or "",
            "1" if item.get("retrieval_enabled") else "0",
            (item.get("question") or "").replace("\n", " ").replace(",", "，"),
            (item.get("answer") or "").replace("\n", " ").replace(",", "，"),
            item.get("source_uri") or "",
            item.get("content_hash") or "",
            item.get("import_batch_id") or "",
            alts.replace(",", "，"),
            negs.replace(",", "，"),
        ]
        lines.append(",".join(row))
    return Response(content="\n".join(lines) + "\n", media_type="text/csv; charset=utf-8")


@router.patch("/qa/{qa_id}")
def update_qa(
    dataset_id: DatasetId,
    qa_id: QAId,
    body: QAUpdate,
    actor: WriteActor,
) -> dict[str, Any]:
    for required_text_field in ("question", "answer", "source_uri"):
        if (
            required_text_field in body.model_fields_set
            and getattr(body, required_text_field) is None
        ):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "knowledge_content_invalid",
                    "message": f"{required_text_field} 不得为 null",
                },
            )
    values = body.model_dump(exclude_unset=True)
    expected_revision = values.pop("expected_revision")
    if not values:
        raise HTTPException(
            status_code=422,
            detail={"code": "knowledge_content_invalid", "message": "至少提供一个修改字段"},
        )
    try:
        repository = _repository()
        qa = repository.update_qa(
            actor.tenant_id,
            dataset_id,
            qa_id,
            expected_revision=expected_revision,
            **values,
            audit=actor.to_audit_context(),
        )
    except Exception as exc:
        _raise_content(exc)
    return _qa_with_alternatives(repository, qa)


@router.post("/qa/{qa_id}/review")
def review_qa(
    dataset_id: DatasetId,
    qa_id: QAId,
    body: QAReviewRequest,
    actor: WriteActor,
) -> dict[str, Any]:
    try:
        repository = _repository()
        qa = repository.review_qa(
            actor.tenant_id,
            dataset_id,
            qa_id,
            expected_revision=body.expected_revision,
            decision=body.decision.value,
            audit=actor.to_audit_context(),
        )
    except Exception as exc:
        _raise_content(exc)
    return _qa_with_alternatives(repository, qa)


@router.post("/qa/{qa_id}/expire")
def expire_qa(
    dataset_id: DatasetId,
    qa_id: QAId,
    body: QARevisionRequest,
    actor: ManageActor,
) -> dict[str, Any]:
    try:
        repository = _repository()
        qa = repository.expire_qa(
            actor.tenant_id,
            dataset_id,
            qa_id,
            expected_revision=body.expected_revision,
            audit=actor.to_audit_context(),
        )
    except Exception as exc:
        _raise_content(exc)
    return _qa_with_alternatives(repository, qa)


@router.post("/qa/{qa_id}/restore")
def restore_qa(
    dataset_id: DatasetId,
    qa_id: QAId,
    body: QARevisionRequest,
    actor: ManageActor,
) -> dict[str, Any]:
    try:
        repository = _repository()
        qa = repository.restore_qa(
            actor.tenant_id,
            dataset_id,
            qa_id,
            expected_revision=body.expected_revision,
            audit=actor.to_audit_context(),
        )
    except Exception as exc:
        _raise_content(exc)
    return _qa_with_alternatives(repository, qa)


@router.post("/qa/{qa_id}/alternatives", status_code=status.HTTP_201_CREATED)
def add_alternative(
    dataset_id: DatasetId,
    qa_id: QAId,
    body: QAAlternativeCreate,
    actor: WriteActor,
) -> dict[str, Any]:
    try:
        repository = _repository()
        alternative = repository.add_alternative(
            actor.tenant_id,
            dataset_id,
            qa_id,
            expected_revision=body.expected_revision,
            question=body.question,
            audit=actor.to_audit_context(),
        )
        with Session(repository.engine) as session:
            qa_revision = session.scalar(
                select(QAKnowledge.revision).where(
                    QAKnowledge.id == qa_id,
                    QAKnowledge.tenant_id == actor.tenant_id,
                    QAKnowledge.dataset_id == dataset_id,
                )
            )
    except Exception as exc:
        _raise_content(exc)
    return {**_alternative_payload(alternative), "qa_revision": qa_revision}


@router.delete(
    "/qa/{qa_id}/alternatives/{alternative_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_alternative(
    dataset_id: DatasetId,
    qa_id: QAId,
    alternative_id: AlternativeId,
    actor: WriteActor,
    expected_revision: Annotated[str, Query(min_length=1, max_length=10, pattern=r"^[1-9][0-9]*$")],
) -> Response:
    try:
        _repository().remove_alternative(
            actor.tenant_id,
            dataset_id,
            qa_id,
            alternative_id,
            expected_revision=int(expected_revision),
            audit=actor.to_audit_context(),
        )
    except Exception as exc:
        _raise_content(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/qa/{qa_id}/negative-questions", status_code=status.HTTP_201_CREATED)
def add_negative_question(
    dataset_id: DatasetId,
    qa_id: QAId,
    body: QAAlternativeCreate,
    actor: WriteActor,
) -> dict[str, Any]:
    try:
        repository = _repository()
        negative = repository.add_negative_question(
            actor.tenant_id,
            dataset_id,
            qa_id,
            expected_revision=body.expected_revision,
            question=body.question,
            audit=actor.to_audit_context(),
        )
        with Session(repository.engine) as session:
            qa_revision = session.scalar(
                select(QAKnowledge.revision).where(
                    QAKnowledge.id == qa_id,
                    QAKnowledge.tenant_id == actor.tenant_id,
                    QAKnowledge.dataset_id == dataset_id,
                )
            )
    except Exception as exc:
        _raise_content(exc)
    return {**_negative_payload(negative), "qa_revision": qa_revision}


@router.delete(
    "/qa/{qa_id}/negative-questions/{negative_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_negative_question(
    dataset_id: DatasetId,
    qa_id: QAId,
    negative_id: AlternativeId,
    actor: WriteActor,
    expected_revision: Annotated[str, Query(min_length=1, max_length=10, pattern=r"^[1-9][0-9]*$")],
) -> Response:
    try:
        _repository().remove_negative_question(
            actor.tenant_id,
            dataset_id,
            qa_id,
            negative_id,
            expected_revision=int(expected_revision),
            audit=actor.to_audit_context(),
        )
    except Exception as exc:
        _raise_content(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
