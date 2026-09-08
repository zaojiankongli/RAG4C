"""Authoritative document-version, QA review, and knowledge lifecycle repository."""

from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Engine, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.knowledge_governance import AuditContext
from models.orm import (
    Dataset,
    Document,
    DocumentVersion,
    KnowledgeAuditEvent,
    QAAlternativeQuestion,
    QAKnowledge,
)

_REVIEW_DECISIONS = frozenset({"approved", "rejected"})
_QA_ORIGINS = frozenset({"manual", "automatic"})
_WHITESPACE = re.compile(r"\s+")
_UNSET = object()


class ContentError(RuntimeError):
    """Base error for authoritative knowledge-content mutations."""


class ContentNotFound(ContentError):
    """A resource was not found inside the requested tenant/dataset scope."""


class ContentConflict(ContentError):
    """An optimistic-concurrency or uniqueness invariant was violated."""


@dataclass(frozen=True)
class ExpiryResult:
    documents_expired: int = 0
    qa_expired: int = 0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _clean(value: str | None, maximum: int | None = None, *, field: str = "value") -> str:
    cleaned = unicodedata.normalize("NFKC", str(value or "")).strip()
    if maximum is not None and len(cleaned) > maximum:
        raise ValueError(f"{field} must be at most {maximum} characters")
    return cleaned


def _required_text(value: str | None, *, field: str, maximum: int | None = None) -> str:
    cleaned = _clean(value, maximum, field=field)
    if not cleaned:
        raise ValueError(f"{field} must not be empty")
    return cleaned


def _question_normal_form(value: str) -> tuple[str, str]:
    display = _required_text(value, field="question")
    normalized = _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", display)).strip().casefold()
    return display, hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _validate_window(effective_from: datetime | None, expires_at: datetime | None) -> None:
    if effective_from is not None and expires_at is not None and effective_from >= expires_at:
        raise ValueError("effective_from must be before expires_at")


def _validate_retention_window(expires_at: datetime | None, purge_after: datetime | None) -> None:
    if expires_at is not None and purge_after is not None and expires_at >= purge_after:
        raise ValueError("expires_at must be before purge_after")


def _document_snapshot(document: Document) -> dict[str, Any]:
    return {
        "id": document.id,
        "current_version_id": document.current_version_id,
        "lifecycle_state": document.lifecycle_state,
        "retrieval_enabled": document.retrieval_enabled,
        "effective_from": None
        if document.effective_from is None
        else document.effective_from.isoformat(),
        "expires_at": None if document.expires_at is None else document.expires_at.isoformat(),
        "purge_after": None if document.purge_after is None else document.purge_after.isoformat(),
    }


def _qa_snapshot(qa: QAKnowledge) -> dict[str, Any]:
    return {
        "id": qa.id,
        "revision": qa.revision,
        "question": qa.question,
        "answer": qa.answer,
        "origin": qa.origin,
        "review_status": qa.review_status,
        "lifecycle_state": qa.lifecycle_state,
        "retrieval_enabled": qa.retrieval_enabled,
        "effective_from": None if qa.effective_from is None else qa.effective_from.isoformat(),
        "expires_at": None if qa.expires_at is None else qa.expires_at.isoformat(),
        "source_document_id": qa.source_document_id,
        "source_uri": qa.source_uri,
        "metadata": qa.metadata_json,
        "reviewed_by": qa.reviewed_by,
        "reviewed_at": None if qa.reviewed_at is None else qa.reviewed_at.isoformat(),
    }


class KnowledgeContentRepository:
    """Tenant/dataset-scoped authority for versions, reviewed QA, and expiry."""

    def __init__(self, engine: Engine):
        self.engine = engine

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:16]}"

    @staticmethod
    def _dataset(session: Session, tenant_id: str, dataset_id: str) -> Dataset:
        row = session.scalar(
            select(Dataset).where(Dataset.id == dataset_id, Dataset.tenant_id == tenant_id)
        )
        if row is None:
            raise ContentNotFound("dataset does not exist in tenant scope")
        return row

    @staticmethod
    def _bump_serving_generation(
        session: Session,
        tenant_id: str,
        dataset_id: str,
    ) -> None:
        result = session.execute(
            update(Dataset)
            .where(Dataset.id == dataset_id, Dataset.tenant_id == tenant_id)
            .values(serving_generation=Dataset.serving_generation + 1)
        )
        if result.rowcount != 1:
            raise ContentNotFound("dataset does not exist in tenant scope")

    @staticmethod
    def _document_serving_signature(document: Document) -> tuple[Any, ...]:
        return (
            document.lifecycle_state,
            bool(document.retrieval_enabled),
            document.effective_from,
            document.expires_at,
        )

    @staticmethod
    def _qa_serving_signature(qa: QAKnowledge) -> tuple[Any, ...]:
        return (
            qa.lifecycle_state,
            bool(qa.retrieval_enabled),
            qa.effective_from,
            qa.expires_at,
        )

    @staticmethod
    def _document(
        session: Session,
        tenant_id: str,
        dataset_id: str,
        document_id: str,
        *,
        lock: bool = False,
    ) -> Document:
        query = select(Document).where(
            Document.id == document_id,
            Document.tenant_id == tenant_id,
            Document.dataset_id == dataset_id,
        )
        row = session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise ContentNotFound("document does not exist in dataset scope")
        return row

    @staticmethod
    def _qa(
        session: Session,
        tenant_id: str,
        dataset_id: str,
        qa_id: str,
        *,
        lock: bool = False,
    ) -> QAKnowledge:
        query = select(QAKnowledge).where(
            QAKnowledge.id == qa_id,
            QAKnowledge.tenant_id == tenant_id,
            QAKnowledge.dataset_id == dataset_id,
        )
        row = session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise ContentNotFound("QA knowledge does not exist in dataset scope")
        return row

    def _event(
        self,
        session: Session,
        *,
        tenant_id: str,
        dataset_id: str,
        audit: AuditContext,
        action: str,
        resource_type: str,
        resource_id: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        session.add(
            KnowledgeAuditEvent(
                id=self._new_id("audit"),
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                actor_id=_clean(audit.actor_id, 64, field="actor_id"),
                action=_clean(action, 128, field="action"),
                resource_type=_clean(resource_type, 64, field="resource_type"),
                resource_id=_clean(resource_id, 512, field="resource_id"),
                before_snapshot=before,
                after_snapshot=after,
                request_id=_clean(audit.request_id, 128, field="request_id"),
                request_ip=_clean(audit.request_ip, 64, field="request_ip"),
                occurred_at=_naive_utc(occurred_at) or _utc_now(),
            )
        )

    @staticmethod
    def _flush(session: Session, message: str) -> None:
        try:
            session.flush()
        except IntegrityError as exc:
            session.rollback()
            raise ContentConflict(message) from exc

    @staticmethod
    def _commit(session: Session, message: str) -> None:
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise ContentConflict(message) from exc

    def create_document_version(
        self,
        tenant_id: str,
        dataset_id: str,
        document_id: str,
        *,
        expected_current_revision: int,
        expected_current_version_id: str | None | object = _UNSET,
        source_identity: str,
        source_hash: str,
        parser_policy_snapshot: dict[str, Any] | None = None,
        parser_metadata: dict[str, Any] | None = None,
        source_content_ref: str = "",
        change_reason: str = "",
        effective_from: datetime | None | object = _UNSET,
        expires_at: datetime | None | object = _UNSET,
        purge_after: datetime | None | object = _UNSET,
        retrieval_enabled: bool | object = _UNSET,
        audit: AuditContext,
    ) -> DocumentVersion:
        if int(expected_current_revision) < 0:
            raise ValueError("expected_current_revision must be non-negative")
        identity = _required_text(source_identity, field="source_identity", maximum=512)
        digest = _required_text(source_hash, field="source_hash", maximum=64)
        if len(digest) != 64:
            raise ValueError("source_hash must be exactly 64 characters")
        with Session(self.engine, expire_on_commit=False) as session:
            document = self._document(session, tenant_id, dataset_id, document_id, lock=True)
            current: DocumentVersion | None = None
            if document.current_version_id is not None:
                current = session.scalar(
                    select(DocumentVersion).where(
                        DocumentVersion.id == document.current_version_id,
                        DocumentVersion.tenant_id == tenant_id,
                        DocumentVersion.dataset_id == dataset_id,
                        DocumentVersion.document_id == document_id,
                    )
                )
                if current is None:
                    raise ContentConflict("current document version head is invalid")
            current_revision = 0 if current is None else current.revision
            if current_revision != int(expected_current_revision):
                raise ContentConflict("current document version revision conflict")
            if expected_current_version_id is not _UNSET:
                actual_head = None if current is None else current.id
                if actual_head != expected_current_version_id:
                    raise ContentConflict("current document version head conflict")

            before = _document_snapshot(document)
            before_serving = self._document_serving_signature(document)
            next_effective = (
                document.effective_from if effective_from is _UNSET else _naive_utc(effective_from)  # type: ignore[arg-type]
            )
            next_expiry = (
                document.expires_at if expires_at is _UNSET else _naive_utc(expires_at)  # type: ignore[arg-type]
            )
            next_purge = (
                document.purge_after if purge_after is _UNSET else _naive_utc(purge_after)  # type: ignore[arg-type]
            )
            _validate_window(next_effective, next_expiry)
            _validate_retention_window(next_expiry, next_purge)
            next_retrieval = (
                document.retrieval_enabled
                if retrieval_enabled is _UNSET
                else bool(retrieval_enabled)
            )
            if next_retrieval and document.lifecycle_state != "active":
                raise ContentConflict("only active documents can enable retrieval")

            version = DocumentVersion(
                id=self._new_id("document-version"),
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                document_id=document_id,
                revision=current_revision + 1,
                source_identity=identity,
                source_hash=digest,
                parser_policy_snapshot=dict(parser_policy_snapshot or {}),
                parser_metadata=dict(parser_metadata or {}),
                source_content_ref=_clean(source_content_ref, 1024, field="source_content_ref"),
                created_by=_clean(audit.actor_id, 64, field="actor_id"),
                change_reason=_clean(change_reason, 512, field="change_reason"),
            )
            session.add(version)
            self._flush(session, "document version creation failed")
            document.current_version_id = version.id
            document.effective_from = next_effective
            document.expires_at = next_expiry
            document.purge_after = next_purge
            document.retrieval_enabled = next_retrieval
            if self._document_serving_signature(document) != before_serving:
                self._bump_serving_generation(session, tenant_id, dataset_id)
            self._event(
                session,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                audit=audit,
                action="document_version.create",
                resource_type="document",
                resource_id=document_id,
                before=before,
                after={
                    **_document_snapshot(document),
                    "version": {
                        "id": version.id,
                        "revision": version.revision,
                        "source_hash": version.source_hash,
                        "source_identity": version.source_identity,
                    },
                },
            )
            self._commit(session, "document version creation failed")
            return version

    def list_document_versions(
        self,
        tenant_id: str,
        dataset_id: str,
        document_id: str,
        *,
        limit: int = 100,
    ) -> list[DocumentVersion]:
        bounded = max(1, min(int(limit), 500))
        with Session(self.engine, expire_on_commit=False) as session:
            self._document(session, tenant_id, dataset_id, document_id)
            return list(
                session.scalars(
                    select(DocumentVersion)
                    .where(
                        DocumentVersion.tenant_id == tenant_id,
                        DocumentVersion.dataset_id == dataset_id,
                        DocumentVersion.document_id == document_id,
                    )
                    .order_by(DocumentVersion.revision.desc())
                    .limit(bounded)
                )
            )

    def get_document_version(
        self,
        tenant_id: str,
        dataset_id: str,
        document_id: str,
        *,
        version_id: str | None = None,
        revision: int | None = None,
    ) -> DocumentVersion:
        with Session(self.engine, expire_on_commit=False) as session:
            document = self._document(session, tenant_id, dataset_id, document_id)
            query = select(DocumentVersion).where(
                DocumentVersion.tenant_id == tenant_id,
                DocumentVersion.dataset_id == dataset_id,
                DocumentVersion.document_id == document_id,
            )
            if version_id is not None:
                query = query.where(DocumentVersion.id == version_id)
            elif revision is not None:
                query = query.where(DocumentVersion.revision == int(revision))
            elif document.current_version_id is not None:
                query = query.where(DocumentVersion.id == document.current_version_id)
            else:
                raise ContentNotFound("document has no current version")
            row = session.scalar(query.limit(1))
            if row is None:
                raise ContentNotFound("document version does not exist in dataset scope")
            return row

    def create_qa(
        self,
        tenant_id: str,
        dataset_id: str,
        *,
        question: str,
        answer: str,
        origin: str = "manual",
        source_document_id: str | None = None,
        source_uri: str = "",
        metadata: dict[str, Any] | None = None,
        effective_from: datetime | None = None,
        expires_at: datetime | None = None,
        audit: AuditContext,
    ) -> QAKnowledge:
        display_question, _ = _question_normal_form(question)
        display_answer = _required_text(answer, field="answer")
        qa_origin = _clean(origin, 24, field="origin").casefold()
        if qa_origin not in _QA_ORIGINS:
            raise ValueError("origin must be manual or automatic")
        effective = _naive_utc(effective_from)
        expiry = _naive_utc(expires_at)
        _validate_window(effective, expiry)
        with Session(self.engine, expire_on_commit=False) as session:
            self._dataset(session, tenant_id, dataset_id)
            if source_document_id is not None:
                self._document(session, tenant_id, dataset_id, source_document_id)
            qa = QAKnowledge(
                id=self._new_id("qa"),
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                revision=1,
                question=display_question,
                answer=display_answer,
                origin=qa_origin,
                review_status="pending",
                lifecycle_state="active",
                retrieval_enabled=False,
                effective_from=effective,
                expires_at=expiry,
                source_document_id=source_document_id,
                source_uri=_clean(source_uri, 1024, field="source_uri"),
                metadata_json=dict(metadata or {}),
                created_by=_clean(audit.actor_id, 64, field="actor_id"),
            )
            session.add(qa)
            self._event(
                session,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                audit=audit,
                action="qa.create",
                resource_type="qa_knowledge",
                resource_id=qa.id,
                after=_qa_snapshot(qa),
            )
            self._commit(session, "QA creation failed")
            return qa

    def _cas_qa(
        self,
        session: Session,
        *,
        tenant_id: str,
        dataset_id: str,
        qa_id: str,
        expected_revision: int,
        values: dict[Any, Any],
        required_lifecycle_state: str | None = None,
        rollback_on_conflict: bool = True,
    ) -> QAKnowledge:
        if int(expected_revision) < 1:
            raise ValueError("expected_revision must be positive")
        conditions = [
            QAKnowledge.id == qa_id,
            QAKnowledge.tenant_id == tenant_id,
            QAKnowledge.dataset_id == dataset_id,
            QAKnowledge.revision == int(expected_revision),
        ]
        if required_lifecycle_state is not None:
            conditions.append(QAKnowledge.lifecycle_state == required_lifecycle_state)
        mutation = dict(values)
        mutation[QAKnowledge.revision] = QAKnowledge.revision + 1
        mutation[QAKnowledge.updated_at] = _utc_now()
        result = session.execute(update(QAKnowledge).where(*conditions).values(mutation))
        if result.rowcount != 1:
            if rollback_on_conflict:
                session.rollback()
            raise ContentConflict("QA revision conflict")
        session.expire_all()
        return self._qa(session, tenant_id, dataset_id, qa_id)

    @staticmethod
    def _review_reset_values() -> dict[Any, Any]:
        return {
            QAKnowledge.review_status: "pending",
            QAKnowledge.retrieval_enabled: False,
            QAKnowledge.reviewed_by: None,
            QAKnowledge.reviewed_at: None,
        }

    def update_qa(
        self,
        tenant_id: str,
        dataset_id: str,
        qa_id: str,
        *,
        expected_revision: int,
        question: str | object = _UNSET,
        answer: str | object = _UNSET,
        source_document_id: str | None | object = _UNSET,
        source_uri: str | object = _UNSET,
        metadata: dict[str, Any] | None | object = _UNSET,
        effective_from: datetime | None | object = _UNSET,
        expires_at: datetime | None | object = _UNSET,
        audit: AuditContext,
    ) -> QAKnowledge:
        material = (
            question,
            answer,
            source_document_id,
            source_uri,
            metadata,
            effective_from,
            expires_at,
        )
        if all(value is _UNSET for value in material):
            raise ValueError("QA update requires at least one material field")
        with Session(self.engine, expire_on_commit=False) as session:
            qa = self._qa(session, tenant_id, dataset_id, qa_id)
            before = _qa_snapshot(qa)
            before_serving = self._qa_serving_signature(qa)
            values = self._review_reset_values()
            if question is not _UNSET:
                values[QAKnowledge.question] = _question_normal_form(str(question))[0]
            if answer is not _UNSET:
                values[QAKnowledge.answer] = _required_text(str(answer), field="answer")
            if source_document_id is not _UNSET:
                if source_document_id is not None:
                    self._document(
                        session,
                        tenant_id,
                        dataset_id,
                        str(source_document_id),
                    )
                values[QAKnowledge.source_document_id] = source_document_id
            if source_uri is not _UNSET:
                values[QAKnowledge.source_uri] = _clean(str(source_uri), 1024, field="source_uri")
            if metadata is not _UNSET:
                values[QAKnowledge.metadata_json] = None if metadata is None else dict(metadata)
            next_effective = (
                qa.effective_from if effective_from is _UNSET else _naive_utc(effective_from)  # type: ignore[arg-type]
            )
            next_expiry = (
                qa.expires_at if expires_at is _UNSET else _naive_utc(expires_at)  # type: ignore[arg-type]
            )
            _validate_window(next_effective, next_expiry)
            if effective_from is not _UNSET:
                values[QAKnowledge.effective_from] = next_effective
            if expires_at is not _UNSET:
                values[QAKnowledge.expires_at] = next_expiry
            updated = self._cas_qa(
                session,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                qa_id=qa_id,
                expected_revision=expected_revision,
                values=values,
            )
            if self._qa_serving_signature(updated) != before_serving:
                self._bump_serving_generation(session, tenant_id, dataset_id)
            self._event(
                session,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                audit=audit,
                action="qa.update",
                resource_type="qa_knowledge",
                resource_id=updated.id,
                before=before,
                after=_qa_snapshot(updated),
            )
            self._commit(session, "QA update failed")
            return updated

    def review_qa(
        self,
        tenant_id: str,
        dataset_id: str,
        qa_id: str,
        *,
        expected_revision: int,
        decision: str,
        audit: AuditContext,
    ) -> QAKnowledge:
        review = _clean(decision, 24, field="decision").casefold()
        if review not in _REVIEW_DECISIONS:
            raise ValueError("decision must be approved or rejected")
        with Session(self.engine, expire_on_commit=False) as session:
            qa = self._qa(session, tenant_id, dataset_id, qa_id)
            before = _qa_snapshot(qa)
            before_serving = self._qa_serving_signature(qa)
            reviewed_at = _utc_now()
            updated = self._cas_qa(
                session,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                qa_id=qa_id,
                expected_revision=expected_revision,
                values={
                    QAKnowledge.review_status: review,
                    QAKnowledge.reviewed_by: _clean(audit.actor_id, 64, field="actor_id"),
                    QAKnowledge.reviewed_at: reviewed_at,
                    QAKnowledge.retrieval_enabled: (
                        review == "approved" and qa.lifecycle_state == "active"
                    ),
                },
            )
            if self._qa_serving_signature(updated) != before_serving:
                self._bump_serving_generation(session, tenant_id, dataset_id)
            self._event(
                session,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                audit=audit,
                action=f"qa.review.{review}",
                resource_type="qa_knowledge",
                resource_id=updated.id,
                before=before,
                after=_qa_snapshot(updated),
            )
            self._commit(session, "QA review failed")
            return updated

    def add_alternative(
        self,
        tenant_id: str,
        dataset_id: str,
        qa_id: str,
        *,
        expected_revision: int,
        question: str,
        audit: AuditContext,
    ) -> QAAlternativeQuestion:
        display, normalized_hash = _question_normal_form(question)
        with Session(self.engine, expire_on_commit=False) as session:
            qa = self._qa(session, tenant_id, dataset_id, qa_id)
            before = _qa_snapshot(qa)
            before_serving = self._qa_serving_signature(qa)
            updated = self._cas_qa(
                session,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                qa_id=qa_id,
                expected_revision=expected_revision,
                values=self._review_reset_values(),
            )
            existing = session.scalar(
                select(QAAlternativeQuestion.id).where(
                    QAAlternativeQuestion.tenant_id == tenant_id,
                    QAAlternativeQuestion.dataset_id == dataset_id,
                    QAAlternativeQuestion.qa_id == qa_id,
                    QAAlternativeQuestion.normalized_hash == normalized_hash,
                )
            )
            if existing is not None:
                session.rollback()
                raise ContentConflict("alternative question already exists")
            alternative = QAAlternativeQuestion(
                id=self._new_id("qa-alternative"),
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                qa_id=updated.id,
                question=display,
                normalized_hash=normalized_hash,
                created_by=_clean(audit.actor_id, 64, field="actor_id"),
            )
            session.add(alternative)
            if self._qa_serving_signature(updated) != before_serving:
                self._bump_serving_generation(session, tenant_id, dataset_id)
            self._event(
                session,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                audit=audit,
                action="qa_alternative.add",
                resource_type="qa_knowledge",
                resource_id=updated.id,
                before=before,
                after={
                    **_qa_snapshot(updated),
                    "alternative": {
                        "id": alternative.id,
                        "question": alternative.question,
                        "normalized_hash": alternative.normalized_hash,
                    },
                },
            )
            self._commit(session, "alternative question already exists")
            return alternative

    def remove_alternative(
        self,
        tenant_id: str,
        dataset_id: str,
        qa_id: str,
        alternative_id: str,
        *,
        expected_revision: int,
        audit: AuditContext,
    ) -> None:
        with Session(self.engine, expire_on_commit=False) as session:
            qa = self._qa(session, tenant_id, dataset_id, qa_id)
            alternative = session.scalar(
                select(QAAlternativeQuestion).where(
                    QAAlternativeQuestion.id == alternative_id,
                    QAAlternativeQuestion.tenant_id == tenant_id,
                    QAAlternativeQuestion.dataset_id == dataset_id,
                    QAAlternativeQuestion.qa_id == qa.id,
                )
            )
            if alternative is None:
                raise ContentNotFound("alternative question does not exist in QA scope")
            before = {
                **_qa_snapshot(qa),
                "alternative": {
                    "id": alternative.id,
                    "question": alternative.question,
                    "normalized_hash": alternative.normalized_hash,
                },
            }
            before_serving = self._qa_serving_signature(qa)
            updated = self._cas_qa(
                session,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                qa_id=qa_id,
                expected_revision=expected_revision,
                values=self._review_reset_values(),
            )
            session.delete(alternative)
            if self._qa_serving_signature(updated) != before_serving:
                self._bump_serving_generation(session, tenant_id, dataset_id)
            self._event(
                session,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                audit=audit,
                action="qa_alternative.remove",
                resource_type="qa_knowledge",
                resource_id=updated.id,
                before=before,
                after=_qa_snapshot(updated),
            )
            self._commit(session, "alternative question removal failed")

    def expire_qa(
        self,
        tenant_id: str,
        dataset_id: str,
        qa_id: str,
        *,
        expected_revision: int,
        audit: AuditContext,
    ) -> QAKnowledge:
        with Session(self.engine, expire_on_commit=False) as session:
            qa = self._qa(session, tenant_id, dataset_id, qa_id)
            if qa.revision != int(expected_revision):
                raise ContentConflict("QA revision conflict")
            if qa.lifecycle_state != "active":
                raise ContentConflict("only active QA can expire")
            before = _qa_snapshot(qa)
            before_serving = self._qa_serving_signature(qa)
            updated = self._cas_qa(
                session,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                qa_id=qa_id,
                expected_revision=expected_revision,
                required_lifecycle_state="active",
                values={
                    QAKnowledge.lifecycle_state: "expired",
                    QAKnowledge.retrieval_enabled: False,
                },
            )
            if self._qa_serving_signature(updated) != before_serving:
                self._bump_serving_generation(session, tenant_id, dataset_id)
            self._event(
                session,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                audit=audit,
                action="qa.expire",
                resource_type="qa_knowledge",
                resource_id=updated.id,
                before=before,
                after=_qa_snapshot(updated),
            )
            self._commit(session, "QA expiry failed")
            return updated

    def restore_qa(
        self,
        tenant_id: str,
        dataset_id: str,
        qa_id: str,
        *,
        expected_revision: int,
        audit: AuditContext,
    ) -> QAKnowledge:
        with Session(self.engine, expire_on_commit=False) as session:
            qa = self._qa(session, tenant_id, dataset_id, qa_id)
            if qa.revision != int(expected_revision):
                raise ContentConflict("QA revision conflict")
            if qa.lifecycle_state != "expired":
                raise ContentConflict("only expired QA can restore")
            before = _qa_snapshot(qa)
            before_serving = self._qa_serving_signature(qa)
            updated = self._cas_qa(
                session,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                qa_id=qa_id,
                expected_revision=expected_revision,
                required_lifecycle_state="expired",
                values={
                    QAKnowledge.lifecycle_state: "active",
                    QAKnowledge.retrieval_enabled: qa.review_status == "approved",
                },
            )
            if self._qa_serving_signature(updated) != before_serving:
                self._bump_serving_generation(session, tenant_id, dataset_id)
            self._event(
                session,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                audit=audit,
                action="qa.restore",
                resource_type="qa_knowledge",
                resource_id=updated.id,
                before=before,
                after=_qa_snapshot(updated),
            )
            self._commit(session, "QA restore failed")
            return updated

    def list_effective_qa(
        self,
        tenant_id: str,
        dataset_id: str,
        *,
        now: datetime | None = None,
        limit: int = 500,
    ) -> list[QAKnowledge]:
        moment = _naive_utc(now) or _utc_now()
        bounded = max(1, min(int(limit), 1000))
        with Session(self.engine, expire_on_commit=False) as session:
            self._dataset(session, tenant_id, dataset_id)
            return list(
                session.scalars(
                    select(QAKnowledge)
                    .where(
                        QAKnowledge.tenant_id == tenant_id,
                        QAKnowledge.dataset_id == dataset_id,
                        QAKnowledge.review_status == "approved",
                        QAKnowledge.lifecycle_state == "active",
                        QAKnowledge.retrieval_enabled.is_(True),
                        or_(
                            QAKnowledge.effective_from.is_(None),
                            QAKnowledge.effective_from <= moment,
                        ),
                        or_(QAKnowledge.expires_at.is_(None), QAKnowledge.expires_at > moment),
                    )
                    .order_by(QAKnowledge.created_at, QAKnowledge.id)
                    .limit(bounded)
                )
            )

    def expire_due(
        self,
        tenant_id: str,
        dataset_id: str,
        *,
        now: datetime | None = None,
        audit: AuditContext,
    ) -> ExpiryResult:
        moment = _naive_utc(now) or _utc_now()
        with Session(self.engine, expire_on_commit=False) as candidate_session:
            self._dataset(candidate_session, tenant_id, dataset_id)
            document_candidates = [
                (document.id, _document_snapshot(document))
                for document in candidate_session.scalars(
                    select(Document)
                    .where(
                        Document.tenant_id == tenant_id,
                        Document.dataset_id == dataset_id,
                        Document.expires_at.is_not(None),
                        Document.expires_at <= moment,
                        Document.lifecycle_state == "active",
                    )
                    .order_by(Document.id)
                )
            ]
            qa_candidates = [
                (qa.id, qa.revision, _qa_snapshot(qa))
                for qa in candidate_session.scalars(
                    select(QAKnowledge)
                    .where(
                        QAKnowledge.tenant_id == tenant_id,
                        QAKnowledge.dataset_id == dataset_id,
                        QAKnowledge.expires_at.is_not(None),
                        QAKnowledge.expires_at <= moment,
                        QAKnowledge.lifecycle_state == "active",
                    )
                    .order_by(QAKnowledge.id)
                )
            ]

        documents_expired = 0
        qa_expired = 0
        with Session(self.engine, expire_on_commit=False) as mutation_session:
            for document_id, before in document_candidates:
                result = mutation_session.execute(
                    update(Document)
                    .where(
                        Document.id == document_id,
                        Document.tenant_id == tenant_id,
                        Document.dataset_id == dataset_id,
                        Document.lifecycle_state == "active",
                        Document.expires_at.is_not(None),
                        Document.expires_at <= moment,
                    )
                    .values(
                        lifecycle_state="expired",
                        retrieval_enabled=False,
                        updated_at=moment,
                    )
                )
                if result.rowcount != 1:
                    continue
                mutation_session.expire_all()
                updated_document = self._document(
                    mutation_session, tenant_id, dataset_id, document_id
                )
                self._event(
                    mutation_session,
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    audit=audit,
                    action="document.expire",
                    resource_type="document",
                    resource_id=document_id,
                    before=before,
                    after=_document_snapshot(updated_document),
                    occurred_at=moment,
                )
                documents_expired += 1

            for qa_id, expected_revision, before in qa_candidates:
                try:
                    updated_qa = self._cas_qa(
                        mutation_session,
                        tenant_id=tenant_id,
                        dataset_id=dataset_id,
                        qa_id=qa_id,
                        expected_revision=expected_revision,
                        required_lifecycle_state="active",
                        rollback_on_conflict=False,
                        values={
                            QAKnowledge.lifecycle_state: "expired",
                            QAKnowledge.retrieval_enabled: False,
                        },
                    )
                except ContentConflict:
                    continue
                self._event(
                    mutation_session,
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    audit=audit,
                    action="qa.expire",
                    resource_type="qa_knowledge",
                    resource_id=qa_id,
                    before=before,
                    after=_qa_snapshot(updated_qa),
                    occurred_at=moment,
                )
                qa_expired += 1

            if documents_expired or qa_expired:
                self._bump_serving_generation(mutation_session, tenant_id, dataset_id)
            self._commit(mutation_session, "knowledge expiry failed")
        return ExpiryResult(documents_expired, qa_expired)


__all__ = [
    "ContentConflict",
    "ContentError",
    "ContentNotFound",
    "ExpiryResult",
    "KnowledgeContentRepository",
]
