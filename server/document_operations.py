"""Deprecated internal-only synchronous cleanup helper.

HTTP APIs must use ``DocumentDeletionRepository`` and durable projection workers.
This helper remains only for controlled maintenance compatibility and focused legacy tests.
"""

from __future__ import annotations

from typing import Any

from core import cache_epoch, catalog


def delete_document_artifacts(
    doc_id: str,
    *,
    catalog_api: Any = catalog,
    pipeline: Any,
) -> dict[str, int | str]:
    """Deprecated: synchronously delete graph, vector, and catalog state.

    Do not call this function from HTTP request handlers.

    The catalog row is removed last so a partial external-store failure remains
    visible and can be retried by an operator. Graph/vector deletion methods are
    expected to be idempotent.
    """
    doc = catalog_api.get_document(doc_id)
    if doc is None:
        raise KeyError(f"文档不存在: {doc_id}")

    tenant_id = str(doc.get("tenant_id") or "")
    chunks = pipeline.milvus.query_chunks_by_doc(doc_id, tenant_id)
    chunk_ids = [str(chunk.chunk_id) for chunk in chunks if getattr(chunk, "chunk_id", "")]

    removed_relations = 0
    removed_entities = 0
    graph_builder = getattr(pipeline, "graph_builder", None)
    if graph_builder is not None and chunk_ids:
        removed_relations, removed_entities = graph_builder.delete_by_chunk_ids(
            chunk_ids, tenant_id=tenant_id
        )

    removed_chunks = int(pipeline.milvus.delete_by_doc_id(doc_id) or 0)
    catalog_api.remove_document(doc_id)
    if tenant_id:
        cache_epoch.bump(tenant_id, reason=f"删除文档 {doc_id}")

    return {
        "document_id": doc_id,
        "removed_chunks": removed_chunks,
        "removed_relations": int(removed_relations or 0),
        "removed_entities": int(removed_entities or 0),
    }


__all__: list[str] = []
