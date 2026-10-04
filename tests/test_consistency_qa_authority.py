"""QA authority counts on consistency summary (catalog-only)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from models.orm import Dataset, QAKnowledge, Tenant
from server.knowledge_consistency_api import _qa_authority_counts


def _qa(**kwargs) -> QAKnowledge:
    base = {
        "id": "qa-x",
        "tenant_id": "t1",
        "dataset_id": "ds1",
        "revision": 1,
        "question": "q",
        "answer": "a",
        "origin": "manual",
        "review_status": "pending",
        "lifecycle_state": "active",
        "retrieval_enabled": False,
        "source_uri": "",
        "created_by": "tester",
        "created_at": datetime(2026, 9, 20, 10, 0, 0),
        "updated_at": datetime(2026, 9, 20, 10, 0, 0),
    }
    base.update(kwargs)
    return QAKnowledge(**base)


def test_qa_authority_counts_breakdown(tmp_path):
    # 目录库用 session 级模板库（tests/_catalog_template.py）：建一次已迁移到
    # head 的 SQLite、各用例拷文件，实测 4.0ms/次 vs 重跑迁移 13.9s（3432x）。
    # 本文件不需要 BASELINE（不验证「从 baseline 升到 head」的过程本身），
    # 所以拷模板与重跑迁移等价。
    from _catalog_template import head_db_url
    url = head_db_url(tmp_path / "qa-auth.db")
    engine = create_engine(url)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id="t1", name="T1"),
                Dataset(id="ds1", tenant_id="t1", name="D"),
                _qa(id="qa-pending", review_status="pending"),
                _qa(
                    id="qa-effective",
                    review_status="approved",
                    retrieval_enabled=True,
                ),
                _qa(id="qa-rejected", review_status="rejected"),
                _qa(
                    id="qa-expired",
                    review_status="approved",
                    lifecycle_state="expired",
                    retrieval_enabled=False,
                ),
                _qa(
                    id="qa-disabled",
                    review_status="approved",
                    retrieval_enabled=False,
                ),
            ]
        )
        session.commit()

    counts = _qa_authority_counts(engine, "t1", "ds1")
    assert counts["total"] == 5
    assert counts["effective_retrieval"] == 1
    assert counts["pending_review"] == 1
    assert counts["rejected"] == 1
    assert counts["expired"] == 1
    assert counts["retrieval_disabled"] == 1
    assert counts["note"]

    other = _qa_authority_counts(engine, "t-other", "ds1")
    assert other["total"] == 0
    assert other["effective_retrieval"] == 0
