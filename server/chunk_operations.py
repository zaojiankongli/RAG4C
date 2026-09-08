"""Operator chunk mutation services used by the document workbench.

Chunk authority rollout modes:
- ``off`` keeps the legacy synchronous Milvus/graph mutation path.
- ``shadow`` records authoritative revisions and shadow projection operations, then
  executes the legacy path so rollout facts do not change serving behavior.
- ``active`` changes only the MySQL authority and durable desired-state queue;
  projection workers apply Milvus/graph changes asynchronously.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from core import cache_epoch, catalog
from core.chunk_catalog import ChunkCatalog, ChunkRevisionConflict
from core.index_operations import IndexOperationQueue
from core.ingest_ledger import IngestLedger
from sqlalchemy.orm import Session
from indexing.hashing import text_hash
from indexing.projection_handlers import ProjectionHandlers
from indexing.state_machine import (
    DocumentWritePermit,
    DocumentWriteSuperseded,
    assert_document_write_permit,
    read_document_write_permit,
)
from models.schemas import Chunk

_AUTHORITY_MODES = frozenset({"off", "shadow", "active"})
_MANUAL_ACTOR = "system:document-workbench"


class ChunkAuthorityIncomplete(RuntimeError):
    """Raised when active-mode document authority cannot be proven complete."""


@dataclass(frozen=True)
class ChunkUpdateResult:
    chunk: Any
    removed_relations: int = 0
    removed_entities: int = 0
    graph_entities: int = 0
    graph_relations: int = 0
    authority_mode: str = "off"
    projection_pending: bool = False
    operation_ids: tuple[str, ...] = ()


def _document(doc_id: str, *, catalog_api: Any) -> dict[str, Any]:
    doc = catalog_api.get_document(doc_id)
    if doc is None:
        raise KeyError(f"文档不存在: {doc_id}")
    return doc


def _legacy_writer_permit(
    doc_id: str, *, doc: dict[str, Any], catalog_api: Any
) -> DocumentWritePermit | None:
    lifecycle = str(doc.get("lifecycle_state") or "active")
    retrieval_enabled = bool(doc.get("retrieval_enabled", True))
    if lifecycle != "active" or not retrieval_enabled or doc.get("active_delete_operation_id"):
        raise ChunkAuthorityIncomplete("document is not active for mutation")
    get_engine = getattr(catalog_api, "get_engine", None)
    if not callable(get_engine):
        return None
    try:
        return read_document_write_permit(
            doc_id,
            dataset_id=str(doc.get("dataset_id") or ""),
            engine=get_engine(),
        )
    except DocumentWriteSuperseded as exc:
        raise ChunkAuthorityIncomplete(str(exc)) from exc


def _legacy_chunk(
    doc_id: str,
    chunk_id: str,
    *,
    doc: dict[str, Any],
    pipeline: Any,
    required: bool = True,
) -> Any | None:
    chunks = pipeline.milvus.query_chunks_by_doc(doc_id, str(doc.get("tenant_id") or ""))
    chunk = next((item for item in chunks if str(item.chunk_id) == chunk_id), None)
    if chunk is None and required:
        raise KeyError(f"切片不存在: {chunk_id}")
    return chunk


def _document_and_chunk(
    doc_id: str, chunk_id: str, *, catalog_api: Any, pipeline: Any
) -> tuple[dict[str, Any], Any]:
    """Compatibility helper retained for authority-off callers."""
    doc = _document(doc_id, catalog_api=catalog_api)
    chunk = _legacy_chunk(doc_id, chunk_id, doc=doc, pipeline=pipeline)
    return doc, chunk


def _authority_dependencies(
    *,
    catalog_api: Any,
    chunk_catalog: ChunkCatalog | None,
    operation_queue: IndexOperationQueue | None,
) -> tuple[ChunkCatalog, IndexOperationQueue]:
    if chunk_catalog is not None and operation_queue is not None:
        if chunk_catalog.engine is not operation_queue.engine:
            raise RuntimeError("chunk authority and outbox must share one engine")
        return chunk_catalog, operation_queue
    get_engine = getattr(catalog_api, "get_engine", None)
    if not callable(get_engine):
        raise RuntimeError("chunk authority requires a catalog engine")
    engine = get_engine()
    return chunk_catalog or ChunkCatalog(engine), operation_queue or IndexOperationQueue(engine)

def _bootstrap_authority_head(
    *,
    doc: dict[str, Any],
    chunk_id: str,
    pipeline: Any,
    chunk_catalog: ChunkCatalog,
) -> Any:
    try:
        head = chunk_catalog.get_head(chunk_id)
    except ChunkRevisionConflict:
        projected = _legacy_chunk(
            str(doc["id"]), chunk_id, doc=doc, pipeline=pipeline, required=False
        )
        if projected is None:
            raise KeyError(f"切片不存在: {chunk_id}") from None
        metadata = dict(getattr(projected, "metadata", None) or {})
        role = str(metadata.get("chunk_role") or "")
        if role not in {"parent", "child", "flat"}:
            role = "child" if getattr(projected, "parent_chunk_id", None) else "flat"
        head = chunk_catalog.upsert_head(
            chunk_id=chunk_id,
            tenant_id=str(doc.get("tenant_id") or getattr(projected, "tenant_id", "")),
            dataset_id=str(doc.get("dataset_id") or getattr(projected, "dataset_id", "")),
            document_id=str(doc["id"]),
            parent_chunk_id=getattr(projected, "parent_chunk_id", None),
            chunk_index=int(metadata.get("chunk_index", metadata.get("seq", 0)) or 0),
            chunk_role=role,
            document_revision=int(
                getattr(projected, "document_revision", 0) or doc.get("content_revision") or 0
            ),
            source_content=str(metadata.get("source_content") or projected.text),
            content=str(projected.text),
            content_hash=str(getattr(projected, "text_hash", "") or ""),
            content_revision=int(getattr(projected, "content_revision", 0) or 0),
            enabled=True,
            metadata=metadata,
        )
    if str(head.document_id) != str(doc["id"]):
        raise KeyError(f"切片不存在: {chunk_id}")
    return head


def _head_to_chunk(head: Any) -> Chunk:
    metadata = dict(head.chunk_metadata or {})
    metadata["chunk_index"] = int(head.chunk_index or 0)
    metadata["chunk_role"] = str(head.chunk_role or "flat")
    if head.context_header:
        metadata["context"] = head.context_header
    return Chunk(
        chunk_id=head.id,
        doc_id=head.document_id,
        text=head.content,
        text_hash=head.content_hash,
        created_at=head.created_at,
        updated_at=head.updated_at,
        tenant_id=head.tenant_id,
        dataset_id=head.dataset_id,
        parent_chunk_id=head.parent_chunk_id,
        source=metadata.get("source"),
        metadata=metadata,
        document_revision=head.document_revision,
        content_revision=head.content_revision,
    )


@dataclass(frozen=True)
class _AuthorityMutation:
    head: Any
    operation_ids: tuple[str, ...]
    changed: bool


def _mutate_authority_with_outbox(
    *,
    doc: dict[str, Any],
    chunk_id: str,
    expected_revision: int,
    content: str,
    mutation: str,
    authority_mode: str,
    include_graph: bool,
    chunk_catalog: ChunkCatalog,
    operation_queue: IndexOperationQueue,
    permit: DocumentWritePermit,
) -> _AuthorityMutation:
    target_revision = int(doc.get("desired_index_revision") or doc.get("content_revision") or 0)
    with Session(chunk_catalog.engine, expire_on_commit=False) as session:
        with session.begin():
            assert_document_write_permit(permit, session=session, for_update=True)
            try:
                current = chunk_catalog.get_head(chunk_id, session=session, for_update=True)
            except ChunkRevisionConflict as exc:
                if authority_mode == "active":
                    raise ChunkAuthorityIncomplete(
                        "document chunk authority is not complete for active mutation"
                    ) from exc
                raise
            if (
                str(current.document_id) != str(doc["id"])
                or int(current.document_revision) != target_revision
                or current.chunk_role == "parent"
            ):
                raise ChunkAuthorityIncomplete(
                    "document chunk authority is not complete for active mutation"
                )
            if mutation == "delete" and not current.enabled:
                tombstone = chunk_catalog.tombstone_chunk(
                    chunk_id,
                    expected_revision=expected_revision,
                    editor_id=_MANUAL_ACTOR,
                    session=session,
                )
                return _AuthorityMutation(tombstone, (), False)
            if authority_mode == "active":
                actual_count = chunk_catalog.count_document_heads(
                    str(doc["id"]),
                    document_revision=target_revision,
                    enabled_only=True,
                    session=session,
                )
                if actual_count != int(doc.get("chunk_count") or 0):
                    raise ChunkAuthorityIncomplete(
                        "document chunk authority is not complete for active mutation"
                    )
            if not current.enabled:
                raise ChunkRevisionConflict("chunk is tombstoned")
            if mutation == "edit":
                head = chunk_catalog.edit_chunk(
                    chunk_id,
                    expected_revision=expected_revision,
                    content=content,
                    editor_id=_MANUAL_ACTOR,
                    edit_source="user",
                    session=session,
                )
            else:
                head = chunk_catalog.tombstone_chunk(
                    chunk_id,
                    expected_revision=expected_revision,
                    editor_id=_MANUAL_ACTOR,
                    session=session,
                )
            initial_status = "shadow" if authority_mode == "shadow" else "pending"
            attempt = operation_queue.start_chunk_mutation_attempt(
                tenant_id=str(head.tenant_id),
                dataset_id=str(head.dataset_id),
                document_id=str(head.document_id),
                input_revision=target_revision,
                initial_status=initial_status,
                session=session,
            )
            attempt.attempt_kind = "chunk_mutation"
            attempt.document_generation = permit.document_generation
            operations = operation_queue.enqueue_chunk_projection_operations(
                tenant_id=str(head.tenant_id),
                dataset_id=str(head.dataset_id),
                document_id=str(head.document_id),
                attempt_id=str(attempt.id),
                target_revision=target_revision,
                chunk_id=str(head.id),
                expected_content_revision=int(head.content_revision),
                mutation=mutation,
                include_graph=include_graph,
                initial_status=initial_status,
                session=session,
            )
            for operation in operations:
                operation.document_generation = permit.document_generation
                payload = dict(operation.payload or {})
                payload["document_generation"] = permit.document_generation
                payload["dataset_generation"] = permit.dataset_generation
                operation.payload = payload
        return _AuthorityMutation(head, tuple(item.id for item in operations), True)

@contextmanager
def _legacy_projection_lock(
    *,
    authority: ChunkCatalog | None,
    pipeline: Any,
    permit: DocumentWritePermit | None,
    chunk_id: str,
    store: str,
):
    if authority is None or permit is None:
        yield
        return
    handlers = ProjectionHandlers(
        chunk_catalog=authority,
        embedder=getattr(pipeline, "embedder", None),
        milvus=pipeline.milvus,
        graph_builder=getattr(pipeline, "graph_builder", None),
    )
    operation = SimpleNamespace(
        tenant_id=permit.tenant_id,
        dataset_id=permit.dataset_id,
        document_id=permit.document_id,
        payload={"chunk_id": chunk_id},
    )
    with handlers._projection_lock(  # noqa: SLF001 - exact worker lock namespace
        operation, store=store, include_chunk=True
    ):
        yield


def _assert_legacy_permit(
    permit: DocumentWritePermit | None, authority: ChunkCatalog | None
) -> None:
    if permit is not None:
        assert_document_write_permit(
            permit, engine=None if authority is None else authority.engine
        )


def _legacy_update(
    *,
    doc: dict[str, Any],
    chunk: Any,
    chunk_id: str,
    normalized: str,
    pipeline: Any,
    permit: DocumentWritePermit | None,
    authority: ChunkCatalog | None,
) -> ChunkUpdateResult:
    metadata = dict(chunk.metadata or {})
    metadata["manual_edit"] = True
    metadata["manual_edit_at"] = datetime.now(timezone.utc).isoformat()
    updated = chunk.model_copy(
        update={
            "text": normalized,
            "text_hash": text_hash(normalized),
            "updated_at": datetime.now(timezone.utc),
            "content_revision": int(getattr(chunk, "content_revision", 0) or 0) + 1,
            "metadata": metadata,
        }
    )
    embedding_text = (
        pipeline._embedding_text(updated)
        if hasattr(pipeline, "_embedding_text")
        else normalized
    )
    vectors = pipeline.embedder.embed_texts([embedding_text])
    try:
        with _legacy_projection_lock(
            authority=authority,
            pipeline=pipeline,
            permit=permit,
            chunk_id=chunk_id,
            store="milvus",
        ):
            _assert_legacy_permit(permit, authority)
            pipeline.milvus.upsert_chunks([updated], vectors)
            try:
                _assert_legacy_permit(permit, authority)
            except DocumentWriteSuperseded:
                pipeline.milvus.delete_by_ids([chunk_id])
                raise

        removed_relations = removed_entities = graph_entities = graph_relations = 0
        graph_builder = getattr(pipeline, "graph_builder", None)
        if graph_builder is not None:
            with _legacy_projection_lock(
                authority=authority,
                pipeline=pipeline,
                permit=permit,
                chunk_id=chunk_id,
                store="graph",
            ):
                _assert_legacy_permit(permit, authority)
                removed_relations, removed_entities = graph_builder.delete_by_chunk_ids(
                    [chunk_id], tenant_id=str(doc.get("tenant_id") or "")
                )
                graph_result = graph_builder.build(
                    [(chunk_id, normalized)],
                    tenant_id=str(doc.get("tenant_id") or ""),
                )
                try:
                    _assert_legacy_permit(permit, authority)
                except DocumentWriteSuperseded:
                    graph_builder.delete_by_chunk_ids(
                        [chunk_id], tenant_id=str(doc.get("tenant_id") or "")
                    )
                    raise
                graph_entities = int(getattr(graph_result, "entity_count", 0) or 0)
                graph_relations = int(getattr(graph_result, "relation_count", 0) or 0)
        return ChunkUpdateResult(
            chunk=updated,
            removed_relations=int(removed_relations or 0),
            removed_entities=int(removed_entities or 0),
            graph_entities=graph_entities,
            graph_relations=graph_relations,
        )
    except DocumentWriteSuperseded as exc:
        # A graph-side stale fence may happen after Milvus completed. Remove the
        # newly written chunk under the exact worker lock before returning 409.
        with _legacy_projection_lock(
            authority=authority,
            pipeline=pipeline,
            permit=permit,
            chunk_id=chunk_id,
            store="milvus",
        ):
            pipeline.milvus.delete_by_ids([chunk_id])
        raise ChunkAuthorityIncomplete(str(exc)) from exc


def update_document_chunk_artifacts(
    doc_id: str,
    chunk_id: str,
    text: str,
    *,
    expected_revision: int = 0,
    editor_id: str = _MANUAL_ACTOR,
    authority_mode: str = "off",
    catalog_api: Any = catalog,
    pipeline: Any,
    chunk_catalog: ChunkCatalog | None = None,
    operation_queue: IndexOperationQueue | None = None,
    ledger: IngestLedger | None = None,
) -> ChunkUpdateResult:
    normalized = text.strip()
    if not normalized:
        raise ValueError("切片正文不能为空")
    if authority_mode not in _AUTHORITY_MODES:
        raise ValueError("chunk authority mode 必须是 off / shadow / active")
    doc = _document(doc_id, catalog_api=catalog_api)

    if authority_mode == "off":
        permit = _legacy_writer_permit(doc_id, doc=doc, catalog_api=catalog_api)
        if permit is not None:
            assert_document_write_permit(permit)
        legacy_chunk = _legacy_chunk(doc_id, chunk_id, doc=doc, pipeline=pipeline)
        legacy_authority = (
            chunk_catalog
            or (
                ChunkCatalog(catalog_api.get_engine())
                if callable(getattr(catalog_api, "get_engine", None))
                else None
            )
        )
        result = _legacy_update(
            doc=doc,
            chunk=legacy_chunk,
            chunk_id=chunk_id,
            normalized=normalized,
            pipeline=pipeline,
            permit=permit,
            authority=legacy_authority,
        )
    else:
        del editor_id, ledger  # client identity is not trusted until governance middleware exists
        authority, queue = _authority_dependencies(
            catalog_api=catalog_api,
            chunk_catalog=chunk_catalog,
            operation_queue=operation_queue,
        )
        try:
            permit = read_document_write_permit(
                doc_id,
                dataset_id=str(doc.get("dataset_id") or ""),
                engine=authority.engine,
            )
        except DocumentWriteSuperseded as exc:
            raise ChunkAuthorityIncomplete(str(exc)) from exc
        if authority_mode == "shadow":
            _bootstrap_authority_head(
                doc=doc, chunk_id=chunk_id, pipeline=pipeline, chunk_catalog=authority
            )
        mutation = _mutate_authority_with_outbox(
            doc=doc,
            chunk_id=chunk_id,
            expected_revision=int(expected_revision),
            content=normalized,
            mutation="edit",
            authority_mode=authority_mode,
            include_graph=getattr(pipeline, "graph_builder", None) is not None,
            chunk_catalog=authority,
            operation_queue=queue,
            permit=permit,
        )
        edited = mutation.head
        operation_ids = mutation.operation_ids
        if authority_mode == "shadow":
            assert_document_write_permit(permit, engine=authority.engine)
            legacy_chunk = _legacy_chunk(doc_id, chunk_id, doc=doc, pipeline=pipeline)
            legacy_result = _legacy_update(
                doc=doc,
                chunk=legacy_chunk,
                chunk_id=chunk_id,
                normalized=normalized,
                pipeline=pipeline,
                permit=permit,
                authority=authority,
            )
            result = ChunkUpdateResult(
                chunk=_head_to_chunk(edited),
                removed_relations=legacy_result.removed_relations,
                removed_entities=legacy_result.removed_entities,
                graph_entities=legacy_result.graph_entities,
                graph_relations=legacy_result.graph_relations,
                authority_mode=authority_mode,
                operation_ids=operation_ids,
            )
        else:
            result = ChunkUpdateResult(
                chunk=_head_to_chunk(edited),
                authority_mode=authority_mode,
                projection_pending=True,
                operation_ids=operation_ids,
            )

    tenant_id = str(doc.get("tenant_id") or "")
    if tenant_id:
        cache_epoch.bump(tenant_id, reason=f"编辑切片 {chunk_id}")
    return result


def _update_document_after_delete(*, doc_id: str, doc: dict[str, Any], catalog_api: Any) -> int:
    next_count = max(0, int(doc.get("chunk_count") or 0) - 1)
    parser_meta = dict(doc.get("parser_meta") or {})
    parser_meta["chunk_count"] = next_count
    parser_meta["manual_chunk_deletes"] = int(parser_meta.get("manual_chunk_deletes") or 0) + 1
    parser_meta["last_chunk_delete_at"] = datetime.now(timezone.utc).isoformat()
    catalog_api.set_document_status(
        doc_id,
        str(doc.get("status") or "completed"),
        detail="切片已人工删除",
        chunk_count=next_count,
        parser_meta=parser_meta,
    )
    return next_count


def _legacy_delete(
    *,
    doc: dict[str, Any],
    chunk_id: str,
    pipeline: Any,
    permit: DocumentWritePermit | None,
    authority: ChunkCatalog | None,
) -> tuple[int, int, int]:
    tenant_id = str(doc.get("tenant_id") or "")
    try:
        removed_relations = removed_entities = 0
        graph_builder = getattr(pipeline, "graph_builder", None)
        if graph_builder is not None:
            with _legacy_projection_lock(
                authority=authority,
                pipeline=pipeline,
                permit=permit,
                chunk_id=chunk_id,
                store="graph",
            ):
                _assert_legacy_permit(permit, authority)
                removed_relations, removed_entities = graph_builder.delete_by_chunk_ids(
                    [chunk_id], tenant_id=tenant_id
                )
                _assert_legacy_permit(permit, authority)
        with _legacy_projection_lock(
            authority=authority,
            pipeline=pipeline,
            permit=permit,
            chunk_id=chunk_id,
            store="milvus",
        ):
            _assert_legacy_permit(permit, authority)
            removed_chunks = int(pipeline.milvus.delete_by_ids([chunk_id]) or 0)
            _assert_legacy_permit(permit, authority)
        return removed_chunks, int(removed_relations or 0), int(removed_entities or 0)
    except DocumentWriteSuperseded as exc:
        raise ChunkAuthorityIncomplete(str(exc)) from exc


def delete_document_chunk_artifacts(
    doc_id: str,
    chunk_id: str,
    *,
    expected_revision: int = 0,
    editor_id: str = _MANUAL_ACTOR,
    authority_mode: str = "off",
    catalog_api: Any = catalog,
    pipeline: Any,
    chunk_catalog: ChunkCatalog | None = None,
    operation_queue: IndexOperationQueue | None = None,
    ledger: IngestLedger | None = None,
) -> dict[str, Any]:
    if authority_mode not in _AUTHORITY_MODES:
        raise ValueError("chunk authority mode 必须是 off / shadow / active")
    doc = _document(doc_id, catalog_api=catalog_api)
    tenant_id = str(doc.get("tenant_id") or "")
    operation_ids: tuple[str, ...] = ()
    content_revision = int(expected_revision)
    projection_pending = False
    authority_changed = True

    if authority_mode == "off":
        permit = _legacy_writer_permit(doc_id, doc=doc, catalog_api=catalog_api)
        if permit is not None:
            assert_document_write_permit(permit)
        _legacy_chunk(doc_id, chunk_id, doc=doc, pipeline=pipeline)
        legacy_authority = (
            chunk_catalog
            or (
                ChunkCatalog(catalog_api.get_engine())
                if callable(getattr(catalog_api, "get_engine", None))
                else None
            )
        )
        removed_chunks, removed_relations, removed_entities = _legacy_delete(
            doc=doc,
            chunk_id=chunk_id,
            pipeline=pipeline,
            permit=permit,
            authority=legacy_authority,
        )
    else:
        del editor_id, ledger  # client identity is not trusted until governance middleware exists
        authority, queue = _authority_dependencies(
            catalog_api=catalog_api,
            chunk_catalog=chunk_catalog,
            operation_queue=operation_queue,
        )
        try:
            permit = read_document_write_permit(
                doc_id,
                dataset_id=str(doc.get("dataset_id") or ""),
                engine=authority.engine,
            )
        except DocumentWriteSuperseded as exc:
            raise ChunkAuthorityIncomplete(str(exc)) from exc
        if authority_mode == "shadow":
            _bootstrap_authority_head(
                doc=doc, chunk_id=chunk_id, pipeline=pipeline, chunk_catalog=authority
            )
        mutation = _mutate_authority_with_outbox(
            doc=doc,
            chunk_id=chunk_id,
            expected_revision=int(expected_revision),
            content="",
            mutation="delete",
            authority_mode=authority_mode,
            include_graph=getattr(pipeline, "graph_builder", None) is not None,
            chunk_catalog=authority,
            operation_queue=queue,
            permit=permit,
        )
        tombstone = mutation.head
        authority_changed = mutation.changed
        content_revision = int(tombstone.content_revision)
        operation_ids = mutation.operation_ids
        if authority_mode == "shadow" and authority_changed:
            assert_document_write_permit(permit, engine=authority.engine)
            removed_chunks, removed_relations, removed_entities = _legacy_delete(
                doc=doc,
                chunk_id=chunk_id,
                pipeline=pipeline,
                permit=permit,
                authority=authority,
            )
        else:
            removed_chunks = removed_relations = removed_entities = 0
            projection_pending = authority_changed

    if authority_mode == "active":
        next_count = authority.count_document_heads(
            doc_id,
            document_revision=int(tombstone.document_revision),
            enabled_only=True,
        )
    else:
        next_count = int(doc.get("chunk_count") or 0)
        if authority_changed:
            next_count = max(0, next_count - 1)
            next_count = _update_document_after_delete(
                doc_id=doc_id, doc=doc, catalog_api=catalog_api
            )
    if tenant_id and authority_changed:
        cache_epoch.bump(tenant_id, reason=f"删除切片 {chunk_id}")
    return {
        "document_id": doc_id,
        "chunk_id": chunk_id,
        "removed_chunks": removed_chunks,
        "removed_relations": removed_relations,
        "removed_entities": removed_entities,
        "remaining_chunks": next_count,
        "content_revision": content_revision,
        "authority_mode": authority_mode,
        "projection_pending": projection_pending,
        "operation_ids": list(operation_ids),
    }


__all__ = [
    "ChunkAuthorityIncomplete",
    "ChunkUpdateResult",
    "update_document_chunk_artifacts",
    "delete_document_chunk_artifacts",
]
