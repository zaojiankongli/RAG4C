from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event, select
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
    from models.orm import Dataset, Tenant

    url = f"sqlite:///{(tmp_path / 'qa-faq-ops.db').as_posix()}"
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
                Dataset(id="dataset-1", tenant_id="tenant-1", name="Knowledge 1"),
            ]
        )
        session.commit()
    return engine, KnowledgeContentRepository(engine)


def test_import_qa_dedups_by_content_hash_and_records_negatives(tmp_path: Path) -> None:
    from models.orm import QAKnowledge, QANegativeQuestion

    engine, repository = _repository(tmp_path)
    first = repository.import_qa_batch(
        "tenant-1",
        "dataset-1",
        items=[
            {
                "question": "报销流程是什么？",
                "answer": "提交单据后由财务复核。",
                "alternatives": ["怎么报销"],
                "negative_questions": ["如何报销机票"],
            },
            {
                "question": "  报销流程是什么？ ",
                "answer": "提交单据后由财务复核。",
            },
        ],
        audit=_audit(),
    )
    assert first["counts"]["created"] == 1
    assert first["counts"]["skipped_duplicate"] == 1
    assert first["created"][0]["content_hash"]
    second = repository.import_qa_batch(
        "tenant-1",
        "dataset-1",
        items=[
            {
                "question": "报销流程是什么？",
                "answer": "提交单据后由财务复核。",
            }
        ],
        audit=_audit(),
    )
    assert second["counts"]["created"] == 0
    assert second["counts"]["skipped_duplicate"] == 1
    assert second["skipped_duplicate"][0]["reason"] == "duplicate_existing"
    with Session(engine) as session:
        rows = list(session.scalars(select(QAKnowledge)))
        assert len(rows) == 1
        assert rows[0].origin == "import"
        assert rows[0].review_status == "pending"
        assert rows[0].retrieval_enabled is False
        negatives = list(session.scalars(select(QANegativeQuestion)))
        assert len(negatives) == 1
        assert negatives[0].question == "如何报销机票"


def test_batch_review_partial_failure_preserves_successes(tmp_path: Path) -> None:
    engine, repository = _repository(tmp_path)
    result = repository.import_qa_batch(
        "tenant-1",
        "dataset-1",
        items=[
            {"question": "Q1", "answer": "A1"},
            {"question": "Q2", "answer": "A2"},
        ],
        audit=_audit(),
    )
    qa_ids = [item["id"] for item in result["created"]]
    batch = repository.batch_review_qa(
        "tenant-1",
        "dataset-1",
        items=[
            {"qa_id": qa_ids[0], "expected_revision": 1, "decision": "approved"},
            {"qa_id": qa_ids[1], "expected_revision": 99, "decision": "approved"},
        ],
        audit=_audit(),
        action="review",
    )
    assert batch["counts"]["succeeded"] == 1
    assert batch["counts"]["failed"] == 1
    assert batch["failed"][0]["qa_id"] == qa_ids[1]
    assert batch["succeeded"][0]["qa_id"] == qa_ids[0]
    assert batch["succeeded"][0]["review_status"] == "approved"
    from models.orm import QAKnowledge

    with Session(engine) as session:
        approved = session.scalar(
            select(QAKnowledge).where(QAKnowledge.id == qa_ids[0])
        )
        assert approved is not None
        assert approved.review_status == "approved"
        assert approved.revision == 2


def test_update_qa_recomputes_content_hash_for_import_dedup(tmp_path: Path) -> None:
    from models.orm import QAKnowledge

    engine, repository = _repository(tmp_path)
    created = repository.create_qa(
        "tenant-1",
        "dataset-1",
        question="旧问题",
        answer="旧答案",
        audit=_audit(),
    )
    old_hash = created.content_hash
    updated = repository.update_qa(
        "tenant-1",
        "dataset-1",
        created.id,
        expected_revision=1,
        question="新问题",
        answer="新答案",
        audit=_audit(),
    )
    assert updated.content_hash != old_hash
    from core.knowledge_content import qa_content_hash

    assert updated.content_hash == qa_content_hash("新问题", "新答案")
    again = repository.import_qa_batch(
        "tenant-1",
        "dataset-1",
        items=[{"question": "新问题", "answer": "新答案"}],
        audit=_audit(),
    )
    assert again["counts"]["skipped_duplicate"] == 1
    assert again["counts"]["created"] == 0
    with Session(engine) as session:
        row = session.scalar(select(QAKnowledge).where(QAKnowledge.id == created.id))
        assert row.content_hash == updated.content_hash


def test_negative_question_lifecycle(tmp_path: Path) -> None:
    from models.orm import QANegativeQuestion

    engine, repository = _repository(tmp_path)
    created = repository.create_qa(
        "tenant-1",
        "dataset-1",
        question="如何重置密码",
        answer="联系管理员重置。",
        audit=_audit(),
    )
    negative = repository.add_negative_question(
        "tenant-1",
        "dataset-1",
        created.id,
        expected_revision=1,
        question="如何重置路由器",
        audit=_audit(),
    )
    assert negative.question == "如何重置路由器"
    repository.remove_negative_question(
        "tenant-1",
        "dataset-1",
        created.id,
        negative.id,
        expected_revision=2,
        audit=_audit(),
    )
    with Session(engine) as session:
        remaining = list(session.scalars(select(QANegativeQuestion)))
        assert remaining == []
