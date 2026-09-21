from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from core.answer_evidence_facts import AnswerEvidenceRepository
from core.knowledge_governance import AuditContext
from models.orm import Base, Dataset, QAKnowledge, Tenant


def _audit(n: int) -> AuditContext:
    return AuditContext(actor_id=f"actor-{n}", request_id=f"req-{n}")


def _repository(tmp_path: Path):
    from core.catalog_schema import upgrade_catalog
    from core.knowledge_content import KnowledgeContentRepository

    url = f"sqlite:///{(tmp_path / 'qa-retrieval-bundle.db').as_posix()}"
    upgrade_catalog(url)
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id="tenant-1", name="Tenant 1"),
                Dataset(id="dataset-1", tenant_id="tenant-1", name="Knowledge 1"),
            ]
        )
        session.commit()
    return engine, KnowledgeContentRepository(engine)


def _approve(repository, qa_id: str, expected_revision: int):
    return repository.review_qa(
        "tenant-1",
        "dataset-1",
        qa_id,
        expected_revision=expected_revision,
        decision="approved",
        audit=_audit(99),
    )


def test_list_qa_retrieval_bundle_only_effective_with_alts_and_negatives(tmp_path: Path):
    engine, repository = _repository(tmp_path)
    now = datetime(2026, 9, 20, 12, 0)

    pending = repository.create_qa(
        "tenant-1",
        "dataset-1",
        question="pending question",
        answer="pending answer",
        origin="manual",
        audit=_audit(1),
    )
    approved = repository.create_qa(
        "tenant-1",
        "dataset-1",
        question="How do I open system settings?",
        answer="Use the config page identity section.",
        origin="manual",
        audit=_audit(2),
    )
    rev = _approve(repository, approved.id, 1).revision

    repository.add_alternative(
        "tenant-1",
        "dataset-1",
        approved.id,
        expected_revision=rev,
        question="Where is Actor Bearer configured?",
        audit=_audit(3),
    )
    with Session(engine) as session:
        after_alt = session.get(QAKnowledge, approved.id)
        rev = after_alt.revision
    rev = _approve(repository, approved.id, rev).revision

    repository.add_negative_question(
        "tenant-1",
        "dataset-1",
        approved.id,
        expected_revision=rev,
        question="How do I delete the knowledge base?",
        audit=_audit(4),
    )
    with Session(engine) as session:
        after_neg = session.get(QAKnowledge, approved.id)
        rev = after_neg.revision
    _approve(repository, approved.id, rev)

    bundle = repository.list_qa_retrieval_bundle("tenant-1", "dataset-1", now=now)
    assert [item["qa_id"] for item in bundle] == [approved.id]
    item = bundle[0]
    assert item["question"] == "How do I open system settings?"
    assert "Where is Actor Bearer configured?" in item["alternatives"]
    assert "How do I delete the knowledge base?" in item["negatives"]
    assert pending.id not in {x["qa_id"] for x in bundle}

    # Force expiry on the approved row without going through review reset.
    with Session(engine) as session:
        row = session.get(QAKnowledge, approved.id)
        row.expires_at = datetime(2026, 9, 1, 0, 0)
        session.commit()

    assert repository.list_qa_retrieval_bundle("tenant-1", "dataset-1", now=now) == []
    # expired data remains in catalog
    with Session(engine) as session:
        retained = session.scalar(select(QAKnowledge).where(QAKnowledge.id == approved.id))
        assert retained is not None
        assert retained.lifecycle_state == "active"


def test_answer_evidence_records_qa_chunk_refs(tmp_path: Path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'facts.db').as_posix()}")
    Base.metadata.create_all(engine)
    repo = AnswerEvidenceRepository(engine)
    fact_id = repo.record_answer_fact(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        question="How do I open system settings?",
        answer="Use the config page identity section.",
        route="hybrid",
        outcome="answered",
        citations=[{"chunk_id": "qa::qa-1", "status": "ok", "document_id": "doc-9"}],
        evidence=[
            {
                "chunk_id": "qa::qa-1",
                "doc_id": "doc-9",
                "metadata": {"qa_id": "qa-1", "qa_revision": 2},
            }
        ],
    )
    payload = repo.get_answer_fact("tenant-1", fact_id, dataset_id="dataset-1")
    assert payload is not None
    refs = payload["evidence_refs"]
    assert refs
    assert refs[0]["chunk_id"] == "qa::qa-1"
    assert refs[0]["document_id"] == "doc-9"
