"""增量重索引：文件哈希变化检测 + chunk 级差量重建。

流程::

    reindex(pipeline, doc_id, file_path)
      1. 文件哈希未变且文档 completed 且未强制 -> 跳过（零成本）；
      2. 否则 parse_and_chunk 得新 chunks（未嵌入）；
      3. 按 doc_id+租户 取旧 chunks，差量对比（chunk_id 确定性）：
         - unchanged：id 与 text_hash 均未变 -> 跳过嵌入（保留旧向量）；
         - changed / new：重新嵌入并 upsert；
         - removed：旧有、新无 -> 按 id 删除；
      4. 更新 catalog（状态 / chunk_count / file_hash）。

注意：调用方需先做文件哈希检测（hashing.text_hash 对文件字节），
本模块只负责「给定文件路径的差量重建」。
"""
from __future__ import annotations

from typing import Any

from core import catalog
from core.observability import get_logger
from indexing.state_machine import (
    DocumentIngestJob,
    assert_document_write_permit,
    read_document_write_permit,
)

_logger = get_logger(__name__)


def _file_sha256(path: str) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def reindex_document(
    pipeline: Any,
    doc_id: str,
    file_path: str,
    doc_type: str | None = None,
    metadata: dict[str, Any] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Queue a full authoritative reindex without synchronous external writes."""
    from core.chunk_catalog import ChunkCatalog
    from core.index_operations import IndexOperationQueue
    from core.ingest_ledger import IngestLedger

    doc = catalog.get_document(doc_id)
    if doc is None:
        raise KeyError(f"文档未登记: {doc_id}")
    permit = read_document_write_permit(
        doc_id, dataset_id=str(doc.get("dataset_id") or "")
    )
    file_hash = _file_sha256(file_path)
    if not force and doc["status"] == "completed" and doc.get("file_hash") == file_hash:
        return {
            "skipped": True,
            "status": "completed",
            "chunk_count": doc["chunk_count"],
        }
    if doc["status"] in ("completed", "error"):
        assert_document_write_permit(permit)
        catalog.reset_document(doc_id)
    assert_document_write_permit(permit)
    engine = catalog.get_engine()
    result = DocumentIngestJob(
        pipeline,
        doc_id,
        dataset_id=str(doc.get("dataset_id") or ""),
        ledger=IngestLedger(engine),
        operation_queue=IndexOperationQueue(engine),
        chunk_catalog=ChunkCatalog(engine),
        ledger_mode="active",
        attempt_kind="reindex",
    ).run(file_path, doc_type=doc_type, metadata=metadata, graph=None)
    return {
        "skipped": False,
        **result,
        "unchanged": 0,
        "updated": int(result.get("chunk_count") or 0),
        "removed": 0,
    }


__all__ = ["reindex_document"]
