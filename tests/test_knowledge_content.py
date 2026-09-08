from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.knowledge_governance import AuditContext


def _audit(number: int = 1) -> AuditContext:
    return AuditContext(
        actor_id=f"user-{number}",
        request_id=f"request-{number}",
        request_ip="127.0.0.1",
    )


def _repository(tmp_path: Path):
    from core.catalog_schema import upgrade_catalog
    from core.knowledge_content import KnowledgeContentRepository
    from models.orm import Dataset, Document, Tenant

    url = f"sqlite:///{(tmp_path / 'knowledge-content.db').as_posix()}"
    upgrade_catalog(url)
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id="tenant-1", name="Tenant 1"),
                Tenant(id="tenant-2", name="Tenant 2"),
                Dataset(id="dataset-1", tenant_id="tenant-1", name="Knowledge 1"),
                Dataset(id="dataset-2", tenant_id="tenant-1", name="Knowledge 2"),
                Dataset(id="dataset-3", tenant_id="tenant-2", name="Knowledge 3"),
                Document(
                    id="doc-1",
                    tenant_id="tenant-1",
                    dataset_id="dataset-1",
                    name="Guide",
                ),
                Document(
                    id="doc-2",
                    tenant_id="tenant-1",
                    dataset_id="dataset-1",
                    name="Policy",
                ),
                Document(
                    id="doc-other-dataset",
                    tenant_id="tenant-1",
                    dataset_id="dataset-2",
                    name="Other dataset",
                ),
                Document(
                    id="doc-other-tenant",
                    tenant_id="tenant-2",
                    dataset_id="dataset-3",
                    name="Other tenant",
                ),
            ]
        )
        session.commit()
    return engine, KnowledgeContentRepository(engine)


def test_document_versions_are_immutable_scoped_heads_with_optimistic_concurrency(
    tmp_path: Path,
) -> None:
    from core import knowledge_content as content
    from models.orm import Document, KnowledgeAuditEvent

    engine, repository = _repository(tmp_path)
    first = repository.create_document_version(
        "tenant-1",
        "dataset-1",
        "doc-1",
        expected_current_revision=0,
        expected_current_version_id=None,
        source_identity="upload:guide-v1.pdf",
        source_hash="a" * 64,
        parser_policy_snapshot={"parser": "mineru", "chunk_size": 800},
        parser_metadata={"pages": 12},
        source_content_ref="object://knowledge/doc-1/v1",
        change_reason="initial import",
        audit=_audit(1),
    )
    second = repository.create_document_version(
        "tenant-1",
        "dataset-1",
        "doc-1",
        expected_current_revision=1,
        expected_current_version_id=first.id,
        source_identity="upload:guide-v2.pdf",
        source_hash="b" * 64,
        parser_policy_snapshot={"parser": "mineru", "chunk_size": 1000},
        parser_metadata={"pages": 13},
        source_content_ref="object://knowledge/doc-1/v2",
        change_reason="replace source",
        audit=_audit(2),
    )

    assert (first.revision, second.revision) == (1, 2)
    assert (
        repository.get_document_version("tenant-1", "dataset-1", "doc-1", revision=1).id == first.id
    )
    assert [
        item.revision
        for item in repository.list_document_versions("tenant-1", "dataset-1", "doc-1")
    ] == [2, 1]

    with pytest.raises(content.ContentConflict, match="current document version"):
        repository.create_document_version(
            "tenant-1",
            "dataset-1",
            "doc-1",
            expected_current_revision=1,
            expected_current_version_id=first.id,
            source_identity="stale",
            source_hash="c" * 64,
            audit=_audit(3),
        )

    with Session(engine) as session:
        document = session.get(Document, "doc-1")
        assert document is not None
        assert document.current_version_id == second.id
        assert document.lifecycle_state == "active"
        assert document.retrieval_enabled is True
        actions = list(
            session.scalars(
                select(KnowledgeAuditEvent.action)
                .where(KnowledgeAuditEvent.resource_id == "doc-1")
                .order_by(KnowledgeAuditEvent.sequence)
            )
        )
        assert actions == ["document_version.create", "document_version.create"]
    engine.dispose()


def test_document_version_mutation_owns_document_effective_and_retention_window(
    tmp_path: Path,
) -> None:
    from models.orm import Document

    engine, repository = _repository(tmp_path)
    effective = datetime(2026, 8, 25, 8, 0)
    expires = datetime(2026, 9, 25, 8, 0)
    purge = datetime(2026, 12, 25, 8, 0)

    repository.create_document_version(
        "tenant-1",
        "dataset-1",
        "doc-1",
        expected_current_revision=0,
        source_identity="upload:scheduled.pdf",
        source_hash="9" * 64,
        effective_from=effective,
        expires_at=expires,
        purge_after=purge,
        retrieval_enabled=False,
        audit=_audit(1),
    )

    with Session(engine) as session:
        document = session.get(Document, "doc-1")
        assert document is not None
        assert document.effective_from == effective
        assert document.expires_at == expires
        assert document.purge_after == purge
        assert document.retrieval_enabled is False
    engine.dispose()


def test_document_version_scope_is_fail_closed_in_repository_and_database(
    tmp_path: Path,
) -> None:
    from core import knowledge_content as content
    from models.orm import Document, DocumentVersion

    engine, repository = _repository(tmp_path)
    version = repository.create_document_version(
        "tenant-1",
        "dataset-1",
        "doc-1",
        expected_current_revision=0,
        source_identity="upload:guide.pdf",
        source_hash="d" * 64,
        audit=_audit(1),
    )

    with pytest.raises(content.ContentNotFound):
        repository.get_document_version("tenant-1", "dataset-2", "doc-1", version_id=version.id)
    with pytest.raises(content.ContentNotFound):
        repository.create_document_version(
            "tenant-2",
            "dataset-3",
            "doc-1",
            expected_current_revision=1,
            source_identity="cross-tenant",
            source_hash="e" * 64,
            audit=_audit(2),
        )

    with Session(engine) as session:
        forged = DocumentVersion(
            id="version-forged",
            tenant_id="tenant-1",
            dataset_id="dataset-2",
            document_id="doc-1",
            revision=1,
            source_identity="forged",
            source_hash="f" * 64,
            created_by="attacker",
        )
        session.add(forged)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        document = session.get(Document, "doc-other-dataset")
        assert document is not None
        document.current_version_id = version.id
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
    engine.dispose()


def test_qa_requires_approved_review_before_active_and_uses_revision_conflicts(
    tmp_path: Path,
) -> None:
    from core import knowledge_content as content

    engine, repository = _repository(tmp_path)
    generated = repository.create_qa(
        "tenant-1",
        "dataset-1",
        question="How do I rotate credentials?",
        answer="Use the security settings page.",
        origin="automatic",
        source_document_id="doc-1",
        metadata={"generator": "qa-v2"},
        audit=_audit(1),
    )

    assert generated.revision == 1
    assert generated.review_status == "pending"
    assert generated.lifecycle_state == "active"
    assert generated.retrieval_enabled is False
    assert (
        repository.list_effective_qa("tenant-1", "dataset-1", now=datetime(2026, 8, 24, 12, 0))
        == []
    )

    approved = repository.review_qa(
        "tenant-1",
        "dataset-1",
        generated.id,
        expected_revision=1,
        decision="approved",
        audit=_audit(3),
    )
    assert approved.revision == 2
    assert approved.review_status == "approved"
    assert approved.retrieval_enabled is True
    assert [
        item.id
        for item in repository.list_effective_qa(
            "tenant-1", "dataset-1", now=datetime(2026, 8, 24, 12, 0)
        )
    ] == [generated.id]

    with pytest.raises(content.ContentConflict, match="revision"):
        repository.update_qa(
            "tenant-1",
            "dataset-1",
            generated.id,
            expected_revision=1,
            answer="stale edit",
            audit=_audit(4),
        )

    updated = repository.update_qa(
        "tenant-1",
        "dataset-1",
        generated.id,
        expected_revision=2,
        question="How are credentials rotated?",
        answer="Use Security > Credentials.",
        source_document_id="doc-2",
        source_uri="knowledge://policy/credentials",
        metadata={"generator": "human-edit", "confidence": 1.0},
        effective_from=datetime(2026, 8, 25, 8, 0),
        expires_at=datetime(2027, 8, 25, 8, 0),
        audit=_audit(5),
    )
    assert updated.revision == 3
    assert updated.question == "How are credentials rotated?"
    assert updated.answer == "Use Security > Credentials."
    assert updated.source_document_id == "doc-2"
    assert updated.source_uri == "knowledge://policy/credentials"
    assert updated.review_status == "pending"
    assert updated.retrieval_enabled is False
    assert updated.reviewed_by is None
    assert updated.reviewed_at is None
    assert (
        repository.list_effective_qa("tenant-1", "dataset-1", now=datetime(2026, 8, 25, 12, 0))
        == []
    )
    engine.dispose()


def test_qa_alternatives_are_normalized_unique_scoped_and_audited(tmp_path: Path) -> None:
    from core import knowledge_content as content
    from models.orm import KnowledgeAuditEvent, QAAlternativeQuestion, QAKnowledge

    engine, repository = _repository(tmp_path)
    qa = repository.create_qa(
        "tenant-1",
        "dataset-1",
        question="What is the retention policy?",
        answer="Seven years.",
        audit=_audit(1),
    )
    approved = repository.review_qa(
        "tenant-1",
        "dataset-1",
        qa.id,
        expected_revision=1,
        decision="approved",
        audit=_audit(2),
    )
    alternative = repository.add_alternative(
        "tenant-1",
        "dataset-1",
        qa.id,
        expected_revision=approved.revision,
        question="  WHAT   is the retention policy？ ",
        audit=_audit(3),
    )
    assert alternative.normalized_hash
    with Session(engine) as session:
        after_add = session.get(QAKnowledge, qa.id)
        assert after_add is not None
        assert (after_add.revision, after_add.review_status, after_add.retrieval_enabled) == (
            3,
            "pending",
            False,
        )
        assert after_add.reviewed_by is None

    with pytest.raises(content.ContentConflict, match="revision"):
        repository.add_alternative(
            "tenant-1",
            "dataset-1",
            qa.id,
            expected_revision=2,
            question="How long is knowledge retained?",
            audit=_audit(4),
        )

    with pytest.raises(content.ContentConflict, match="alternative"):
        repository.add_alternative(
            "tenant-1",
            "dataset-1",
            qa.id,
            expected_revision=3,
            question="what is the retention policy?",
            audit=_audit(4),
        )
    with pytest.raises(content.ContentNotFound):
        repository.add_alternative(
            "tenant-1",
            "dataset-2",
            qa.id,
            expected_revision=3,
            question="Cross scope",
            audit=_audit(5),
        )

    with pytest.raises(content.ContentConflict, match="revision"):
        repository.remove_alternative(
            "tenant-1",
            "dataset-1",
            qa.id,
            alternative.id,
            expected_revision=2,
            audit=_audit(6),
        )
    repository.remove_alternative(
        "tenant-1",
        "dataset-1",
        qa.id,
        alternative.id,
        expected_revision=3,
        audit=_audit(7),
    )
    with Session(engine) as session:
        assert session.get(QAAlternativeQuestion, alternative.id) is None
        refreshed = session.get(QAKnowledge, qa.id)
        assert refreshed is not None
        assert refreshed.revision == 4
        assert refreshed.review_status == "pending"
        assert refreshed.retrieval_enabled is False
        assert refreshed.reviewed_by is None
        assert list(
            session.scalars(
                select(KnowledgeAuditEvent.action)
                .where(KnowledgeAuditEvent.resource_id == qa.id)
                .order_by(KnowledgeAuditEvent.sequence)
            )
        ) == [
            "qa.create",
            "qa.review.approved",
            "qa_alternative.add",
            "qa_alternative.remove",
        ]
    engine.dispose()


def test_effective_qa_and_expire_due_stop_retrieval_but_retain_rows_and_audit(
    tmp_path: Path,
) -> None:
    from models.orm import Document, KnowledgeAuditEvent, QAKnowledge

    engine, repository = _repository(tmp_path)
    now = datetime(2026, 8, 24, 12, 0)
    expired_qa = repository.create_qa(
        "tenant-1",
        "dataset-1",
        question="Old answer?",
        answer="Old.",
        effective_from=now - timedelta(days=2),
        expires_at=now - timedelta(seconds=1),
        audit=_audit(1),
    )
    expired_qa = repository.review_qa(
        "tenant-1",
        "dataset-1",
        expired_qa.id,
        expected_revision=1,
        decision="approved",
        audit=_audit(2),
    )
    future_qa = repository.create_qa(
        "tenant-1",
        "dataset-1",
        question="Future answer?",
        answer="Future.",
        effective_from=now + timedelta(days=1),
        audit=_audit(3),
    )
    repository.review_qa(
        "tenant-1",
        "dataset-1",
        future_qa.id,
        expected_revision=1,
        decision="approved",
        audit=_audit(4),
    )

    with Session(engine) as session:
        document = session.get(Document, "doc-1")
        assert document is not None
        document.expires_at = now - timedelta(seconds=1)
        document.purge_after = now + timedelta(days=90)
        session.commit()

    assert repository.list_effective_qa("tenant-1", "dataset-1", now=now) == []
    result = repository.expire_due("tenant-1", "dataset-1", now=now, audit=_audit(5))
    assert result.documents_expired == 1
    assert result.qa_expired == 1

    with Session(engine) as session:
        document = session.get(Document, "doc-1")
        retained_qa = session.get(QAKnowledge, expired_qa.id)
        assert document is not None and retained_qa is not None
        assert (document.lifecycle_state, document.retrieval_enabled) == (
            "expired",
            False,
        )
        assert document.purge_after == now + timedelta(days=90)
        assert (retained_qa.lifecycle_state, retained_qa.retrieval_enabled) == (
            "expired",
            False,
        )
        expiry_actions = list(
            session.scalars(
                select(KnowledgeAuditEvent.action)
                .where(KnowledgeAuditEvent.action.like("%.expire"))
                .order_by(KnowledgeAuditEvent.sequence)
            )
        )
        assert expiry_actions == ["document.expire", "qa.expire"]
    engine.dispose()


def test_expire_due_two_workers_emit_one_audit_and_increment_qa_once(
    tmp_path: Path,
) -> None:
    from models.orm import Document, KnowledgeAuditEvent, QAKnowledge

    engine, repository = _repository(tmp_path)
    now = datetime(2026, 8, 25, 12, 0)
    qa = repository.create_qa(
        "tenant-1",
        "dataset-1",
        question="Concurrent expiry?",
        answer="Expire once.",
        expires_at=now - timedelta(seconds=1),
        audit=_audit(1),
    )
    repository.review_qa(
        "tenant-1",
        "dataset-1",
        qa.id,
        expected_revision=1,
        decision="approved",
        audit=_audit(2),
    )
    with Session(engine) as session:
        document = session.get(Document, "doc-1")
        assert document is not None
        document.expires_at = now - timedelta(seconds=1)
        session.commit()

    candidates_ready = Barrier(2)

    def synchronize_candidate_reads(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        normalized = statement.casefold()
        if (
            normalized.lstrip().startswith("select")
            and "qa_knowledge.expires_at" in normalized
            and "order by qa_knowledge.id" in normalized
        ):
            candidates_ready.wait(timeout=10)

    event.listen(engine, "before_cursor_execute", synchronize_candidate_reads)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda number: repository.expire_due(
                        "tenant-1", "dataset-1", now=now, audit=_audit(number)
                    ),
                    (3, 4),
                )
            )
    finally:
        event.remove(engine, "before_cursor_execute", synchronize_candidate_reads)

    assert sorted((result.documents_expired, result.qa_expired) for result in results) == [
        (0, 0),
        (1, 1),
    ]
    with Session(engine) as session:
        stored_qa = session.get(QAKnowledge, qa.id)
        stored_document = session.get(Document, "doc-1")
        assert stored_qa is not None and stored_document is not None
        assert (stored_qa.revision, stored_qa.lifecycle_state) == (3, "expired")
        assert stored_document.lifecycle_state == "expired"
        assert (
            session.scalar(
                select(func.count())
                .select_from(KnowledgeAuditEvent)
                .where(KnowledgeAuditEvent.action == "qa.expire")
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(KnowledgeAuditEvent)
                .where(KnowledgeAuditEvent.action == "document.expire")
            )
            == 1
        )
    engine.dispose()


def test_qa_review_uses_atomic_cas_across_two_threads(tmp_path: Path) -> None:
    from core import knowledge_content as content
    from models.orm import KnowledgeAuditEvent, QAKnowledge

    engine, repository = _repository(tmp_path)
    qa = repository.create_qa(
        "tenant-1",
        "dataset-1",
        question="Concurrent review?",
        answer="One decision wins.",
        audit=_audit(1),
    )
    barrier = Barrier(2)

    def review(decision: str, number: int) -> str:
        barrier.wait()
        try:
            repository.review_qa(
                "tenant-1",
                "dataset-1",
                qa.id,
                expected_revision=1,
                decision=decision,
                audit=_audit(number),
            )
            return "succeeded"
        except content.ContentConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda args: review(*args), [("approved", 2), ("rejected", 3)]))

    assert sorted(outcomes) == ["conflict", "succeeded"]
    with Session(engine) as session:
        stored = session.get(QAKnowledge, qa.id)
        assert stored is not None
        assert stored.revision == 2
        assert stored.review_status in {"approved", "rejected"}
        assert (
            session.scalar(
                select(func.count())
                .select_from(KnowledgeAuditEvent)
                .where(KnowledgeAuditEvent.action.like("qa.review.%"))
            )
            == 1
        )
    engine.dispose()


def test_explicit_qa_expire_and_restore_are_cas_fenced(tmp_path: Path) -> None:
    from core import knowledge_content as content

    engine, repository = _repository(tmp_path)
    qa = repository.create_qa(
        "tenant-1", "dataset-1", question="Restore?", answer="Yes.", audit=_audit(1)
    )
    approved = repository.review_qa(
        "tenant-1",
        "dataset-1",
        qa.id,
        expected_revision=1,
        decision="approved",
        audit=_audit(2),
    )

    with pytest.raises(TypeError):
        repository.update_qa(
            "tenant-1",
            "dataset-1",
            qa.id,
            expected_revision=approved.revision,
            lifecycle_state="delete_requested",
            audit=_audit(3),
        )

    expired = repository.expire_qa(
        "tenant-1",
        "dataset-1",
        qa.id,
        expected_revision=approved.revision,
        audit=_audit(4),
    )
    assert (expired.revision, expired.lifecycle_state, expired.retrieval_enabled) == (
        3,
        "expired",
        False,
    )
    restored = repository.restore_qa(
        "tenant-1",
        "dataset-1",
        qa.id,
        expected_revision=expired.revision,
        audit=_audit(5),
    )
    assert (restored.revision, restored.lifecycle_state, restored.retrieval_enabled) == (
        4,
        "active",
        True,
    )
    with pytest.raises(content.ContentConflict, match="revision"):
        repository.restore_qa(
            "tenant-1",
            "dataset-1",
            qa.id,
            expected_revision=3,
            audit=_audit(6),
        )
    engine.dispose()


def test_review_audit_failure_rolls_back_cas_and_approval(tmp_path: Path) -> None:
    from core import knowledge_content as content
    from models.orm import Dataset, KnowledgeAuditEvent, QAKnowledge

    engine, repository = _repository(tmp_path)
    qa = repository.create_qa(
        "tenant-1", "dataset-1", question="Rollback review?", answer="Yes.", audit=_audit(1)
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TRIGGER reject_review_audit BEFORE INSERT ON knowledge_audit_events "
                "WHEN NEW.action = 'qa.review.approved' BEGIN "
                "SELECT RAISE(ABORT, 'audit unavailable'); END"
            )
        )

    with pytest.raises(content.ContentConflict, match="QA review"):
        repository.review_qa(
            "tenant-1",
            "dataset-1",
            qa.id,
            expected_revision=1,
            decision="approved",
            audit=_audit(2),
        )

    with Session(engine) as session:
        stored = session.get(QAKnowledge, qa.id)
        assert stored is not None
        assert (stored.revision, stored.review_status, stored.retrieval_enabled) == (
            1,
            "pending",
            False,
        )
        assert stored.reviewed_by is None
        assert session.get(Dataset, "dataset-1").serving_generation == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(KnowledgeAuditEvent)
                .where(KnowledgeAuditEvent.action.like("qa.review.%"))
            )
            == 0
        )
    engine.dispose()


def test_audit_failure_rolls_back_qa_mutation(tmp_path: Path) -> None:
    from core import knowledge_content as content
    from models.orm import KnowledgeAuditEvent, QAKnowledge

    engine, repository = _repository(tmp_path)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TRIGGER reject_qa_audit BEFORE INSERT ON knowledge_audit_events "
                "WHEN NEW.action = 'qa.create' BEGIN "
                "SELECT RAISE(ABORT, 'audit unavailable'); END"
            )
        )

    with pytest.raises(content.ContentConflict, match="QA creation"):
        repository.create_qa(
            "tenant-1",
            "dataset-1",
            question="Should rollback?",
            answer="Yes.",
            audit=_audit(1),
        )

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(QAKnowledge)) == 0
        assert session.scalar(select(func.count()).select_from(KnowledgeAuditEvent)) == 0
    engine.dispose()


def test_serving_generation_bumps_for_document_version_eligibility_changes(
    tmp_path: Path,
) -> None:
    from models.orm import Dataset

    engine, repository = _repository(tmp_path)
    first = repository.create_document_version(
        "tenant-1",
        "dataset-1",
        "doc-1",
        expected_current_revision=0,
        source_identity="upload:v1",
        source_hash="a" * 64,
        audit=_audit(1),
    )
    with Session(engine) as session:
        assert session.get(Dataset, "dataset-1").serving_generation == 0

    repository.create_document_version(
        "tenant-1",
        "dataset-1",
        "doc-1",
        expected_current_revision=1,
        expected_current_version_id=first.id,
        source_identity="upload:v2",
        source_hash="b" * 64,
        effective_from=datetime(2026, 8, 25, 9, 0),
        retrieval_enabled=False,
        audit=_audit(2),
    )
    with Session(engine) as session:
        assert session.get(Dataset, "dataset-1").serving_generation == 1
    engine.dispose()


def test_expire_due_bumps_dataset_serving_generation_once_for_document_and_qa(
    tmp_path: Path,
) -> None:
    from models.orm import Dataset, Document

    engine, repository = _repository(tmp_path)
    now = datetime(2026, 8, 25, 12, 0)
    qa = repository.create_qa(
        "tenant-1",
        "dataset-1",
        question="Expiring QA?",
        answer="Yes.",
        expires_at=now - timedelta(seconds=1),
        audit=_audit(1),
    )
    repository.review_qa(
        "tenant-1",
        "dataset-1",
        qa.id,
        expected_revision=1,
        decision="approved",
        audit=_audit(2),
    )
    with Session(engine) as session:
        baseline = session.get(Dataset, "dataset-1").serving_generation
        document = session.get(Document, "doc-1")
        document.expires_at = now - timedelta(seconds=1)
        session.commit()

    result = repository.expire_due("tenant-1", "dataset-1", now=now, audit=_audit(3))
    assert (result.documents_expired, result.qa_expired) == (1, 1)
    with Session(engine) as session:
        assert session.get(Dataset, "dataset-1").serving_generation == baseline + 1
    engine.dispose()


def test_expire_and_restore_qa_each_bump_dataset_serving_generation(tmp_path: Path) -> None:
    from models.orm import Dataset

    engine, repository = _repository(tmp_path)
    qa = repository.create_qa(
        "tenant-1", "dataset-1", question="Restore serving?", answer="Yes.", audit=_audit(1)
    )
    approved = repository.review_qa(
        "tenant-1",
        "dataset-1",
        qa.id,
        expected_revision=1,
        decision="approved",
        audit=_audit(2),
    )
    with Session(engine) as session:
        baseline = session.get(Dataset, "dataset-1").serving_generation

    expired = repository.expire_qa(
        "tenant-1",
        "dataset-1",
        qa.id,
        expected_revision=approved.revision,
        audit=_audit(3),
    )
    with Session(engine) as session:
        assert session.get(Dataset, "dataset-1").serving_generation == baseline + 1

    repository.restore_qa(
        "tenant-1",
        "dataset-1",
        qa.id,
        expected_revision=expired.revision,
        audit=_audit(4),
    )
    with Session(engine) as session:
        assert session.get(Dataset, "dataset-1").serving_generation == baseline + 2
    engine.dispose()


def test_document_version_serving_generation_rolls_back_with_audit_failure(
    tmp_path: Path,
) -> None:
    from core import knowledge_content as content
    from models.orm import Dataset, Document, DocumentVersion

    engine, repository = _repository(tmp_path)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TRIGGER reject_version_audit BEFORE INSERT ON knowledge_audit_events "
                "WHEN NEW.action = 'document_version.create' BEGIN "
                "SELECT RAISE(ABORT, 'audit unavailable'); END"
            )
        )

    with pytest.raises(content.ContentConflict, match="document version creation"):
        repository.create_document_version(
            "tenant-1",
            "dataset-1",
            "doc-1",
            expected_current_revision=0,
            source_identity="upload:rollback",
            source_hash="c" * 64,
            effective_from=datetime(2026, 8, 25, 9, 0),
            retrieval_enabled=False,
            audit=_audit(1),
        )

    with Session(engine) as session:
        assert session.get(Dataset, "dataset-1").serving_generation == 0
        assert session.get(Document, "doc-1").current_version_id is None
        assert session.scalar(select(func.count()).select_from(DocumentVersion)) == 0
    engine.dispose()
