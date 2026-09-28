"""钉住 ``rag_stream._result_payload`` 证据项的字段透传（交接 §9 第 21a 条）。

背景更正（2026-09-27）：交接条目 21a 原称「evidence 由 langchain4j-sister-project
（Java 侧）产出、Python 只透传」——不成立。证据链是纯 Python 闭环：
检索管线 ``RetrievedChunk``（``Chunk.dataset_id`` 本就有值）→
``rag_common.evidence_chunks`` → ``_result_payload``。缺口只是挑字段时把
``dataset_id`` 落下，导致前端 stale 深链因缺库归属而无法直达。本套件钉住：
evidence 项必须原样透传 chunk 自身的 ``dataset_id``，缺失时为 ``None``，
前端据此明确降级，不用查询作用域冒充出处。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from models.schemas import Chunk, QueryResult, RetrievedChunk
from rag_stream import _result_payload


def _retrieved(dataset_id: str) -> RetrievedChunk:
    chunk = Chunk(
        chunk_id="chunk-1",
        doc_id="doc-1",
        text="body",
        text_hash="hash",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        tenant_id="tenant-1",
        dataset_id=dataset_id,
    )
    return RetrievedChunk(chunk=chunk, score=0.9, rank=1, branch="hybrid")


def _result(evidence_chunks: list[Any]) -> QueryResult:
    return QueryResult(
        query="q",
        answer="a",
        citations=[],
        verdict={"evidence_chunks": evidence_chunks},
        abstained=False,
        route="hybrid",
        traces=[],
    )


def test_evidence_item_carries_the_chunk_own_dataset_id() -> None:
    payload = _result_payload(_result([_retrieved("dataset-b")]))
    item = payload["evidence"][0]
    assert item["dataset_id"] == "dataset-b"
    assert item["doc_id"] == "doc-1"


def test_missing_dataset_id_stays_none_instead_of_borrowing_query_scope() -> None:
    chunk_dict = {
        "chunk_id": "chunk-2",
        "doc_id": "doc-2",
        "text": "legacy",
        "source": None,
    }
    payload = _result_payload(_result([chunk_dict]))
    assert payload["evidence"][0]["dataset_id"] is None
