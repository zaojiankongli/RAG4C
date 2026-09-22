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
from typing import Any, Callable, Literal, Protocol

from core import cache_epoch, catalog
from core.chunk_catalog import ChunkCatalog, ChunkRevisionConflict
from core.index_operations import IndexOperationQueue
from core.ingest_ledger import IngestLedger
from core.providers import ProviderRegistry
from sqlalchemy import select
from sqlalchemy.orm import Session
from indexing.hashing import text_hash
from indexing.projection_handlers import ProjectionHandlers
from indexing.state_machine import (
    DocumentWritePermit,
    DocumentWriteSuperseded,
    assert_document_write_permit,
    read_document_write_permit,
)
from models.orm import ChunkRevision
from models.schemas import Chunk

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


def _durable_operation(head: Any) -> str:
    """Projection work a changed head requires, derived from the head -- not the verb.

    A revert that lands on a disabled revision must delete the projection, and a
    restore must re-upsert it; keying either off the verb name would get both wrong.
    """
    return _DELETE_OPERATION if not head.enabled else _UPSERT_OPERATION


def _mutate_authority_with_outbox(
    *,
    doc: dict[str, Any],
    m: "ChunkMutation",
    verb: ChunkVerb,
    include_graph: bool,
    chunk_catalog: ChunkCatalog,
    operation_queue: IndexOperationQueue,
    permit: DocumentWritePermit,
    strict_missing_head: bool = False,
    verify_completeness: bool = False,
    initial_status: str = "pending",
) -> _AuthorityMutation:
    """Shared ChunkHead transaction + outbox ceremony; the verb lives in ``verb``.

    Everything below is identical for every authority mode (write fences, attempt
    record, durable operations, generation stamping).  What differs per mode arrives
    as data from the writer (``strict_missing_head`` / ``verify_completeness`` /
    ``initial_status``) and what differs per verb arrives as the ``verb`` policy, so
    neither axis can grow an ``if`` back in here.  The verdict is taken before the
    completeness fence on purpose: that fence compares enabled-head counts against the
    pre-write document state, so it must not see the head this verb is about to flip.
    """
    chunk_id = m.chunk_id
    target_revision = int(doc.get("desired_index_revision") or doc.get("content_revision") or 0)
    with Session(chunk_catalog.engine, expire_on_commit=False) as session:
        with session.begin():
            assert_document_write_permit(permit, session=session, for_update=True)
            try:
                current = chunk_catalog.get_head(chunk_id, session=session, for_update=True)
            except ChunkRevisionConflict as exc:
                if strict_missing_head:
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
            verdict = verb.decide(current, m)
            if verdict == "noop":
                return _AuthorityMutation(current, (), False)
            if verify_completeness:
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
            if verdict == "reject":
                raise ChunkRevisionConflict("chunk is tombstoned")
            head = verb.write(session, current, m, chunk_catalog)
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
                mutation=_durable_operation(head),
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


ChunkOp = Literal["edit", "tombstone", "restore", "revert"]

#: Durable-operation vocabulary understood by ``IndexOperationQueue``.  Kept as the two
#: legacy strings so projection workers, the reconciler and the operation dedup key are
#: unaffected by the verbs added here.
_UPSERT_OPERATION = "edit"
_DELETE_OPERATION = "delete"

_REASON_MAX_CHARS = 500


@dataclass(frozen=True)
class ChunkMutation:
    """One operator request against a single ChunkHead.

    Argument validation lives here so a verb/argument mismatch (revert with no target
    revision, edit with blank text) is rejected before any authority mode is consulted.

    Raises:
        ValueError: the verb's required arguments are missing or malformed.
    """

    op: ChunkOp
    doc_id: str
    chunk_id: str
    expected_revision: int
    text: str | None = None
    target_revision: int | None = None
    reason: str | None = None
    editor_id: str = _MANUAL_ACTOR

    def __post_init__(self) -> None:
        if self.op not in ("edit", "tombstone", "restore", "revert"):
            raise ValueError(f"未知切片动作: {self.op!r}")
        if int(self.expected_revision) < 0:
            raise ValueError("expected_revision 不能为负数")
        if self.op == "edit":
            normalized = str(self.text or "").strip()
            if not normalized:
                raise ValueError("切片正文不能为空")
            object.__setattr__(self, "text", normalized)
        elif self.op == "revert":
            if self.target_revision is None or int(self.target_revision) < 0:
                raise ValueError("回滚切片必须给出 target_revision")
            object.__setattr__(self, "target_revision", int(self.target_revision))
        elif self.text is not None or self.target_revision is not None:
            raise ValueError(f"动作 {self.op} 不接受 text / target_revision")

    @property
    def metadata_patch(self) -> dict[str, Any] | None:
        """Audit note carried on the head; the revision rows keep no reason (S-CL S2.4)."""
        if not self.reason:
            return None
        stamp = datetime.now(timezone.utc).isoformat()
        return {
            "edit_reason": self.reason,
            "edit_reason_at": stamp,
            "edit_reason_by": self.editor_id,
        }


@dataclass(frozen=True)
class WriterContext:
    """Everything a writer needs, resolved once by :func:`apply_chunk_mutation`."""

    doc: dict[str, Any]
    pipeline: Any
    catalog_api: Any
    chunk_catalog: ChunkCatalog | None
    operation_queue: IndexOperationQueue | None
    include_graph: bool


@dataclass(frozen=True)
class ChunkWriteOutcome:
    """Result of one applied :class:`ChunkMutation`, neutral to verb and to mode."""

    head: Any = None
    chunk: Any = None
    authority_changed: bool = True
    operation_ids: tuple[str, ...] = ()
    projection_pending: bool = False
    #: Enabled-head count in the modes that treat the authority as the chunk-count truth.
    remaining_chunks: int | None = None
    removed_chunks: int = 0
    removed_relations: int = 0
    removed_entities: int = 0
    graph_entities: int = 0
    graph_relations: int = 0


# --------------------------------------------------------------------------- #
# Verbs -- one entry in AUTHORITY_STEPS per ChunkOp.  ``decide`` is pure so the
# idempotent / reject verdicts are testable without a database, and so the shared
# transaction can keep the original order: verdict -> completeness fence -> guard -> write.
# --------------------------------------------------------------------------- #
VerbVerdict = Literal["noop", "proceed", "reject"]


@dataclass(frozen=True)
class ChunkVerb:
    decide: Callable[[Any, ChunkMutation], VerbVerdict]
    write: Callable[[Session, Any, ChunkMutation, ChunkCatalog], Any]


def _decide_requires_enabled(current: Any, m: ChunkMutation) -> VerbVerdict:
    return "proceed" if current.enabled else "reject"


def _decide_tombstone(current: Any, m: ChunkMutation) -> VerbVerdict:
    if current.content_revision != m.expected_revision:
        raise ChunkRevisionConflict("chunk revision conflict")
    return "noop" if not current.enabled else "proceed"


def _decide_restore(current: Any, m: ChunkMutation) -> VerbVerdict:
    if current.content_revision != m.expected_revision:
        raise ChunkRevisionConflict("chunk revision conflict")
    return "noop" if current.enabled else "proceed"


def _write_edit(
    session: Session, current: Any, m: ChunkMutation, chunks: ChunkCatalog
) -> Any:
    return chunks.edit_chunk(
        m.chunk_id,
        expected_revision=m.expected_revision,
        content=str(m.text),
        editor_id=m.editor_id,
        edit_source="user",
        metadata_patch=m.metadata_patch,
        session=session,
    )


def _write_tombstone(
    session: Session, current: Any, m: ChunkMutation, chunks: ChunkCatalog
) -> Any:
    return chunks.tombstone_chunk(
        m.chunk_id,
        expected_revision=m.expected_revision,
        editor_id=m.editor_id,
        session=session,
    )


def _write_restore(
    session: Session, current: Any, m: ChunkMutation, chunks: ChunkCatalog
) -> Any:
    return chunks.edit_chunk(
        m.chunk_id,
        expected_revision=m.expected_revision,
        content=current.content,
        editor_id=m.editor_id,
        edit_source="restore",
        enabled=True,
        metadata_patch=m.metadata_patch,
        session=session,
    )


def _write_revert(
    session: Session, current: Any, m: ChunkMutation, chunks: ChunkCatalog
) -> Any:
    revision = session.scalar(
        select(ChunkRevision).where(
            ChunkRevision.chunk_id == m.chunk_id,
            ChunkRevision.tenant_id == current.tenant_id,
            ChunkRevision.dataset_id == current.dataset_id,
            ChunkRevision.document_id == current.document_id,
            ChunkRevision.revision == int(m.target_revision or 0),
        )
    )
    if revision is None:
        raise ChunkRevisionConflict("target revision does not exist")
    return chunks.edit_chunk(
        m.chunk_id,
        expected_revision=m.expected_revision,
        content=revision.content,
        editor_id=m.editor_id,
        edit_source="revert",
        enabled=revision.enabled,
        metadata_patch=m.metadata_patch,
        session=session,
    )


AUTHORITY_STEPS: dict[str, ChunkVerb] = {
    "edit": ChunkVerb(_decide_requires_enabled, _write_edit),
    "tombstone": ChunkVerb(_decide_tombstone, _write_tombstone),
    "restore": ChunkVerb(_decide_restore, _write_restore),
    "revert": ChunkVerb(_decide_requires_enabled, _write_revert),
}


# --------------------------------------------------------------------------- #
# Authority-mode writers -- the only place rollout semantics differ, expressed
# as declared data so the shared code never tests which mode it is in.
# --------------------------------------------------------------------------- #
class ChunkProjectionWriter(Protocol):
    """Applies one :class:`ChunkMutation` under a single chunk authority rollout mode.

    Adding a mode is one :func:`register_chunk_writer` call; no code in this module
    branches on a mode name.
    """

    mode: str
    #: A missing ChunkHead means incomplete authority, not a plain 404.
    strict_missing_head: bool
    #: Enabled-head count must equal the recorded chunk_count before writing.
    verify_completeness: bool
    #: Initial status stamped on the durable projection operations.
    initial_status: str
    #: Whether the legacy synchronous projection still serves reads in this mode.
    mirrors_legacy: bool
    #: Whether a changed write leaves the projection awaiting an async worker.
    async_projection: bool
    #: Whether enabled ChunkHead count is the truth for the document's chunk_count.
    counts_heads_as_remaining: bool

    def apply(self, m: ChunkMutation, ctx: WriterContext) -> ChunkWriteOutcome: ...


class _AuthorityWriter:
    """Body shared by the two modes that have a ChunkHead authority."""

    def __init__(self, spec: ChunkProjectionWriter) -> None:
        self.spec = spec

    def apply(self, m: ChunkMutation, ctx: WriterContext) -> ChunkWriteOutcome:
        doc = ctx.doc
        authority, queue = _authority_dependencies(
            catalog_api=ctx.catalog_api,
            chunk_catalog=ctx.chunk_catalog,
            operation_queue=ctx.operation_queue,
        )
        try:
            permit = read_document_write_permit(
                m.doc_id,
                dataset_id=str(doc.get("dataset_id") or ""),
                engine=authority.engine,
            )
        except DocumentWriteSuperseded as exc:
            raise ChunkAuthorityIncomplete(str(exc)) from exc
        if self.spec.mirrors_legacy:
            _bootstrap_authority_head(
                doc=doc, chunk_id=m.chunk_id, pipeline=ctx.pipeline, chunk_catalog=authority
            )
        mutation = _mutate_authority_with_outbox(
            doc=doc,
            m=m,
            verb=AUTHORITY_STEPS[m.op],
            include_graph=ctx.include_graph,
            chunk_catalog=authority,
            operation_queue=queue,
            permit=permit,
            strict_missing_head=self.spec.strict_missing_head,
            verify_completeness=self.spec.verify_completeness,
            initial_status=self.spec.initial_status,
        )
        head = mutation.head
        outcome = ChunkWriteOutcome(
            head=head,
            chunk=_head_to_chunk(head),
            authority_changed=mutation.changed,
            operation_ids=mutation.operation_ids,
            projection_pending=self.spec.async_projection and mutation.changed,
            remaining_chunks=self._remaining_chunks(authority, m, head),
        )
        if not self.spec.mirrors_legacy or not mutation.changed:
            return outcome
        return self._mirror_legacy(m, ctx, authority, permit, outcome)

    def _remaining_chunks(
        self, authority: ChunkCatalog, m: ChunkMutation, head: Any
    ) -> int | None:
        if not self.spec.counts_heads_as_remaining:
            return None
        return authority.count_document_heads(
            m.doc_id,
            document_revision=int(head.document_revision),
            enabled_only=True,
        )

    def _mirror_legacy(
        self,
        m: ChunkMutation,
        ctx: WriterContext,
        authority: ChunkCatalog,
        permit: DocumentWritePermit,
        outcome: ChunkWriteOutcome,
    ) -> ChunkWriteOutcome:
        """Run the legacy synchronous projection under the worker's own lock namespace."""
        assert_document_write_permit(permit, engine=authority.engine)
        base = {
            "head": outcome.head,
            "chunk": outcome.chunk,
            "authority_changed": outcome.authority_changed,
            "operation_ids": outcome.operation_ids,
            "projection_pending": False,
        }
        if m.op == "tombstone":
            removed_chunks, removed_relations, removed_entities = _legacy_delete(
                doc=ctx.doc,
                chunk_id=m.chunk_id,
                pipeline=ctx.pipeline,
                permit=permit,
                authority=authority,
            )
            return ChunkWriteOutcome(
                **{
                    **base,
                    "removed_chunks": removed_chunks,
                    "removed_relations": removed_relations,
                    "removed_entities": removed_entities,
                }
            )
        legacy_chunk = _legacy_chunk(m.doc_id, m.chunk_id, doc=ctx.doc, pipeline=ctx.pipeline)
        result = _legacy_update(
            doc=ctx.doc,
            chunk=legacy_chunk,
            chunk_id=m.chunk_id,
            normalized=str(outcome.head.content),
            pipeline=ctx.pipeline,
            permit=permit,
            authority=authority,
        )
        return ChunkWriteOutcome(
            **{
                **base,
                "removed_relations": result.removed_relations,
                "removed_entities": result.removed_entities,
                "graph_entities": result.graph_entities,
                "graph_relations": result.graph_relations,
            }
        )


class LegacyWriter:
    """``off`` -- no ChunkHead authority; the legacy synchronous projection is the truth."""

    mode = "off"
    strict_missing_head = False
    verify_completeness = False
    initial_status = "pending"
    mirrors_legacy = True
    async_projection = False
    counts_heads_as_remaining = False

    def apply(self, m: ChunkMutation, ctx: WriterContext) -> ChunkWriteOutcome:
        if m.op in ("restore", "revert"):
            raise ChunkAuthorityIncomplete(
                f"动作 {m.op} 需要 ChunkHead 权威（catalog.chunk_authority_mode=shadow 或 active）"
            )
        doc = ctx.doc
        permit = _legacy_writer_permit(m.doc_id, doc=doc, catalog_api=ctx.catalog_api)
        if permit is not None:
            assert_document_write_permit(permit)
        legacy_chunk = _legacy_chunk(m.doc_id, m.chunk_id, doc=doc, pipeline=ctx.pipeline)
        authority = ctx.chunk_catalog
        if authority is None and callable(getattr(ctx.catalog_api, "get_engine", None)):
            authority = ChunkCatalog(ctx.catalog_api.get_engine())
        if m.op == "tombstone":
            removed_chunks, removed_relations, removed_entities = _legacy_delete(
                doc=doc,
                chunk_id=m.chunk_id,
                pipeline=ctx.pipeline,
                permit=permit,
                authority=authority,
            )
            return ChunkWriteOutcome(
                chunk=legacy_chunk,
                removed_chunks=removed_chunks,
                removed_relations=removed_relations,
                removed_entities=removed_entities,
            )
        result = _legacy_update(
            doc=doc,
            chunk=legacy_chunk,
            chunk_id=m.chunk_id,
            normalized=str(m.text),
            pipeline=ctx.pipeline,
            permit=permit,
            authority=authority,
        )
        return ChunkWriteOutcome(
            chunk=result.chunk,
            removed_relations=result.removed_relations,
            removed_entities=result.removed_entities,
            graph_entities=result.graph_entities,
            graph_relations=result.graph_relations,
        )


class ShadowWriter(_AuthorityWriter):
    """``shadow`` -- record authoritative revisions, then run the legacy path for real."""

    mode = "shadow"
    strict_missing_head = False
    verify_completeness = False
    initial_status = "shadow"
    mirrors_legacy = True
    async_projection = False
    counts_heads_as_remaining = False

    def __init__(self) -> None:
        super().__init__(self)


class ActiveWriter(_AuthorityWriter):
    """``active`` -- change only the MySQL authority; workers project asynchronously."""

    mode = "active"
    strict_missing_head = True
    verify_completeness = True
    initial_status = "pending"
    mirrors_legacy = False
    async_projection = True
    counts_heads_as_remaining = True

    def __init__(self) -> None:
        super().__init__(self)


CHUNK_WRITERS: ProviderRegistry[None, ChunkProjectionWriter] = ProviderRegistry("chunk writer")


def register_chunk_writer(writer: ChunkProjectionWriter, *, replace: bool = False) -> None:
    """Register a chunk authority-mode writer under its own ``mode`` key."""
    CHUNK_WRITERS.register(writer.mode, lambda _config, _w=writer: _w, replace=replace)


def register_builtin_chunk_writers() -> None:
    """(Re)register the three built-in rollout modes; safe to call more than once."""
    for writer in (LegacyWriter(), ShadowWriter(), ActiveWriter()):
        register_chunk_writer(writer, replace=True)


register_builtin_chunk_writers()


def apply_chunk_mutation(
    m: ChunkMutation,
    *,
    authority_mode: str = "off",
    catalog_api: Any = catalog,
    pipeline: Any,
    chunk_catalog: ChunkCatalog | None = None,
    operation_queue: IndexOperationQueue | None = None,
) -> ChunkWriteOutcome:
    """Single entry point for every operator chunk mutation.

    An unknown mode raises with the registered names listed, so a mistyped
    ``RAG4C_CATALOG_CHUNK_AUTHORITY_MODE`` fails visibly instead of silently
    falling back to the legacy projection path.

    Raises:
        ValueError: unknown mode, or a verb/argument mismatch on ``m``.
        ChunkAuthorityIncomplete: authority is absent, stale or incomplete.
        ChunkRevisionConflict: ``expected_revision`` no longer matches the head.
    """
    writer = CHUNK_WRITERS.create(authority_mode, None)
    doc = _document(m.doc_id, catalog_api=catalog_api)
    return writer.apply(
        m,
        WriterContext(
            doc=doc,
            pipeline=pipeline,
            catalog_api=catalog_api,
            chunk_catalog=chunk_catalog,
            operation_queue=operation_queue,
            include_graph=getattr(pipeline, "graph_builder", None) is not None,
        ),
    )


def _bounded_reason(reason: str | None) -> str | None:
    cleaned = str(reason or "").strip()
    if not cleaned:
        return None
    return cleaned[:_REASON_MAX_CHARS]


def _apply_operator_mutation(
    m: ChunkMutation,
    *,
    authority_mode: str,
    catalog_api: Any,
    pipeline: Any,
    chunk_catalog: ChunkCatalog | None,
    operation_queue: IndexOperationQueue | None,
    epoch_reason: str,
) -> ChunkUpdateResult:
    outcome = apply_chunk_mutation(
        m,
        authority_mode=authority_mode,
        catalog_api=catalog_api,
        pipeline=pipeline,
        chunk_catalog=chunk_catalog,
        operation_queue=operation_queue,
    )
    tenant_id = str(_document(m.doc_id, catalog_api=catalog_api).get("tenant_id") or "")
    if tenant_id:
        cache_epoch.bump(tenant_id, reason=f"{epoch_reason} {m.chunk_id}")
    return ChunkUpdateResult(
        chunk=outcome.chunk,
        removed_relations=outcome.removed_relations,
        removed_entities=outcome.removed_entities,
        graph_entities=outcome.graph_entities,
        graph_relations=outcome.graph_relations,
        authority_mode=authority_mode,
        projection_pending=outcome.projection_pending,
        operation_ids=outcome.operation_ids,
    )


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
    reason: str | None = None,
    enabled: bool | None = None,
) -> ChunkUpdateResult:
    """Edit or enable/disable one chunk; a compatible shell over the writer registry.

    ``enabled`` selects the verb when given (True -> restore, False -> tombstone);
    otherwise a non-empty ``text`` is an edit.
    """
    del editor_id, ledger  # client identity is not trusted until governance middleware exists
    op: ChunkOp = "edit" if enabled is None else ("restore" if enabled else "tombstone")
    return _apply_operator_mutation(
        ChunkMutation(
            op=op,
            doc_id=doc_id,
            chunk_id=chunk_id,
            expected_revision=int(expected_revision),
            text=None if enabled is not None else str(text),
            reason=_bounded_reason(reason),
        ),
        authority_mode=authority_mode,
        catalog_api=catalog_api,
        pipeline=pipeline,
        chunk_catalog=chunk_catalog,
        operation_queue=operation_queue,
        epoch_reason="编辑切片",
    )


def revert_document_chunk_artifacts(
    doc_id: str,
    chunk_id: str,
    *,
    target_revision: int,
    expected_revision: int = 0,
    editor_id: str = _MANUAL_ACTOR,
    authority_mode: str = "off",
    catalog_api: Any = catalog,
    pipeline: Any,
    chunk_catalog: ChunkCatalog | None = None,
    operation_queue: IndexOperationQueue | None = None,
    reason: str | None = None,
) -> ChunkUpdateResult:
    """Roll one chunk head back to a recorded revision as a new revision."""
    del editor_id  # client identity is not trusted until governance middleware exists
    return _apply_operator_mutation(
        ChunkMutation(
            op="revert",
            doc_id=doc_id,
            chunk_id=chunk_id,
            expected_revision=int(expected_revision),
            target_revision=int(target_revision),
            reason=_bounded_reason(reason),
        ),
        authority_mode=authority_mode,
        catalog_api=catalog_api,
        pipeline=pipeline,
        chunk_catalog=chunk_catalog,
        operation_queue=operation_queue,
        epoch_reason="回滚切片",
    )


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
    """Tombstone one chunk: the durable delete of its projection plus count/epoch upkeep.

    Kept as a compatible shell so ``documents.delete_document_chunk`` and the tests that
    monkeypatch it by name keep working.
    """
    del editor_id, ledger  # client identity is not trusted until governance middleware exists
    doc = _document(doc_id, catalog_api=catalog_api)
    tenant_id = str(doc.get("tenant_id") or "")
    outcome = apply_chunk_mutation(
        ChunkMutation(
            op="tombstone",
            doc_id=doc_id,
            chunk_id=chunk_id,
            expected_revision=int(expected_revision),
        ),
        authority_mode=authority_mode,
        catalog_api=catalog_api,
        pipeline=pipeline,
        chunk_catalog=chunk_catalog,
        operation_queue=operation_queue,
    )
    if outcome.remaining_chunks is not None:
        next_count = int(outcome.remaining_chunks)
    else:
        next_count = int(doc.get("chunk_count") or 0)
        if outcome.authority_changed:
            next_count = max(0, next_count - 1)
            next_count = _update_document_after_delete(
                doc_id=doc_id, doc=doc, catalog_api=catalog_api
            )
    if tenant_id and outcome.authority_changed:
        cache_epoch.bump(tenant_id, reason=f"删除切片 {chunk_id}")
    head = outcome.head
    return {
        "document_id": doc_id,
        "chunk_id": chunk_id,
        "removed_chunks": outcome.removed_chunks,
        "removed_relations": outcome.removed_relations,
        "removed_entities": outcome.removed_entities,
        "remaining_chunks": next_count,
        "content_revision": int(head.content_revision) if head is not None else int(expected_revision),
        "authority_mode": authority_mode,
        "projection_pending": outcome.projection_pending,
        "operation_ids": list(outcome.operation_ids),
    }


__all__ = [
    "AUTHORITY_STEPS",
    "ActiveWriter",
    "CHUNK_WRITERS",
    "ChunkAuthorityIncomplete",
    "ChunkMutation",
    "ChunkUpdateResult",
    "ChunkWriteOutcome",
    "LegacyWriter",
    "ShadowWriter",
    "apply_chunk_mutation",
    "delete_document_chunk_artifacts",
    "register_chunk_writer",
    "revert_document_chunk_artifacts",
    "update_document_chunk_artifacts",
]
