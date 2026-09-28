from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
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
    assert refs[0]["source_kind"] == "qa"
    assert refs[0]["qa_id"] == "qa-1"
    assert refs[0].get("qa_revision") == 2
    assert payload.get("qa_evidence_count") == 1


class _Counts:
    """Collect incr() calls so a test can assert which signal fired."""

    def __init__(self) -> None:
        self.counts: dict[str, float] = {}

    def install(self, monkeypatch: pytest.MonkeyPatch) -> "_Counts":
        from core import metrics as metrics_mod

        monkeypatch.setattr(metrics_mod, "get_metrics", lambda: self)
        return self

    def incr(self, name, tags=None, value=1.0) -> None:
        self.counts[name] = self.counts.get(name, 0) + value


def _runtime_engine(tmp_path: Path):
    """Catalog engine wired the way the query path builds it (core.catalog)."""
    from core.catalog_schema import upgrade_catalog

    url = f"sqlite:///{(tmp_path / 'qa-bundle-runtime.db').as_posix()}"
    upgrade_catalog(url)
    engine = create_engine(url)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id="tenant-1", name="Tenant 1"),
                Dataset(id="dataset-1", tenant_id="tenant-1", name="Knowledge 1"),
            ]
        )
        session.commit()
    return engine


def test_loader_reports_no_counter_for_a_provisioned_empty_dataset(tmp_path: Path, monkeypatch):
    """知识库存在但没有 FAQ：静默返回空包，一个计数器都不该亮。

    名字里写"returns rows"会把这条读成"验证了取回数据"，而它断言的是 ``== []``；
    真正取回数据由 ``test_loader_passes_global_scope_through`` 负责。
    """
    from core import catalog as catalog_mod
    from retrieval.qa_retrieval import load_qa_bundle_best_effort

    monkeypatch.setattr(catalog_mod, "get_engine", lambda *a, **k: _runtime_engine(tmp_path))
    counts = _Counts().install(monkeypatch)

    assert load_qa_bundle_best_effort("tenant-1", "dataset-1") == []
    assert counts.counts == {}


def test_missing_dataset_is_not_reported_as_catalog_failure(tmp_path: Path, monkeypatch):
    """未建数据集是正常流量，不是目录故障。

    监控页把 ``catalog_error`` 标成"QA 目录读取失败"并挂成 warning 卡片；
    一次指向不存在数据集的问答就能点亮它，真正的读失败便再无独有信号。
    """
    from core import catalog as catalog_mod
    from retrieval.qa_retrieval import load_qa_bundle_best_effort

    monkeypatch.setattr(catalog_mod, "get_engine", lambda *a, **k: _runtime_engine(tmp_path))
    counts = _Counts().install(monkeypatch)

    assert load_qa_bundle_best_effort("tenant-1", "dataset-missing") == []
    assert counts.counts == {}


def test_catalog_read_failure_still_reports_catalog_error(monkeypatch):
    """反向证据：把读失败也一并静默掉，这条指标就等于没有。"""
    from sqlalchemy.exc import OperationalError

    from core import catalog as catalog_mod
    from core import knowledge_content as content_mod
    from retrieval.qa_retrieval import load_qa_bundle_best_effort

    class _Boom:
        def __init__(self, _engine):
            pass

        def list_qa_retrieval_bundle(self, *_a, **_k):
            raise OperationalError("SELECT 1", {}, Exception("connection reset"))

    monkeypatch.setattr(catalog_mod, "get_engine", lambda *a, **k: object())
    monkeypatch.setattr(content_mod, "KnowledgeContentRepository", _Boom)
    counts = _Counts().install(monkeypatch)

    assert load_qa_bundle_best_effort("tenant-1", "dataset-1") == []
    assert counts.counts == {"query.qa_retrieval.catalog_error": 1}


def _two_dataset_engine(tmp_path: Path):
    """tenant-1 有两个知识库，另有 tenant-2 的同名知识库作为跨租户反证。"""
    from core.catalog_schema import upgrade_catalog
    from core.knowledge_content import KnowledgeContentRepository

    url = f"sqlite:///{(tmp_path / 'qa-bundle-scope.db').as_posix()}"
    upgrade_catalog(url)
    engine = create_engine(url)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id="tenant-1", name="Tenant 1"),
                Tenant(id="tenant-2", name="Tenant 2"),
                Dataset(id="dataset-1", tenant_id="tenant-1", name="Knowledge 1"),
                Dataset(id="dataset-2", tenant_id="tenant-1", name="Knowledge 2"),
                Dataset(id="dataset-3", tenant_id="tenant-2", name="Foreign 1"),
            ]
        )
        session.commit()
    repository = KnowledgeContentRepository(engine)
    ids = {}
    for index, (tenant, dataset, question) in enumerate(
        (
            ("tenant-1", "dataset-1", "投影注册表是什么？"),
            ("tenant-1", "dataset-2", "回执 handoff 怎么派发？"),
            ("tenant-2", "dataset-3", "别的租户的问答"),
        )
    ):
        qa = repository.create_qa(
            tenant,
            dataset,
            question=question,
            answer=f"A{index}",
            origin="manual",
            audit=_audit(index),
        )
        repository.review_qa(
            tenant, dataset, qa.id, expected_revision=1, decision="approved", audit=_audit(90 + index)
        )
        ids[question] = qa.id
    return engine, repository, ids


def test_empty_dataset_scope_spans_the_tenant_not_a_dataset_named_default(
    tmp_path: Path,
):
    """``dataset_id=""`` 是全域检索（QueryRequest 与 build_dataset_filter 都这么声明），
    所以 QA 权威也要覆盖本租户全部知识库；此前它被当成一个叫 default 的知识库，
    UI 默认口径下 FAQ 永远不生效。
    """
    _, repository, ids = _two_dataset_engine(tmp_path)

    bundle = repository.list_qa_retrieval_bundle("tenant-1", "")
    assert {item["qa_id"] for item in bundle} == {
        ids["投影注册表是什么？"],
        ids["回执 handoff 怎么派发？"],
    }
    assert {item["dataset_id"] for item in bundle} == {"dataset-1", "dataset-2"}


def test_empty_dataset_scope_never_reaches_another_tenant(tmp_path: Path):
    _, repository, ids = _two_dataset_engine(tmp_path)

    bundle = repository.list_qa_retrieval_bundle("tenant-1", "")
    assert ids["别的租户的问答"] not in {item["qa_id"] for item in bundle}


def test_loader_passes_global_scope_through(tmp_path: Path, monkeypatch):
    """加载器不得再替空口径编一个 "default" 知识库出来。"""
    from core import catalog as catalog_mod
    from retrieval.qa_retrieval import load_qa_bundle_best_effort

    engine, _, ids = _two_dataset_engine(tmp_path)
    monkeypatch.setattr(catalog_mod, "get_engine", lambda *a, **k: engine)
    counts = _Counts().install(monkeypatch)

    bundle = load_qa_bundle_best_effort("tenant-1", None)
    assert {item["qa_id"] for item in bundle} == {
        ids["投影注册表是什么？"],
        ids["回执 handoff 怎么派发？"],
    }
    assert counts.counts == {}


def test_named_scope_still_limits_to_one_dataset(tmp_path: Path):
    """反向证据：全域实现若写成"忽略参数"，这条会红。"""
    _, repository, ids = _two_dataset_engine(tmp_path)

    bundle = repository.list_qa_retrieval_bundle("tenant-1", "dataset-1")
    assert {item["qa_id"] for item in bundle} == {ids["投影注册表是什么？"]}
