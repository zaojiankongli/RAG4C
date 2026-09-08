"""Active external projection handlers backed by authoritative chunk heads."""
from __future__ import annotations

import hashlib
from contextlib import ExitStack, contextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.chunk_catalog import ChunkCatalog, ChunkRevisionConflict
from core.document_deletion import DocumentDeletionRepository
from core.index_operations import process_keyed_lock
from models.orm import Document, IndexOperation
from models.schemas import Chunk


class StaleProjectionOperation(RuntimeError):
    """Raised when an operation no longer targets current desired state."""


class ProjectionHandlers:
    def __init__(
        self,
        *,
        chunk_catalog: ChunkCatalog,
        embedder: Any,
        milvus: Any,
        graph_builder: Any = None,
    ):
        self.chunk_catalog = chunk_catalog
        self.embedder = embedder
        self.milvus = milvus
        self.graph_builder = graph_builder

    def as_mapping(self) -> dict[str, Any]:
        mapping: dict[str, Any] = {"milvus_chunks": self.handle_milvus}
        if self.graph_builder is not None:
            mapping["graph_projection"] = self.handle_graph
        return mapping

    @staticmethod
    def _head_signature(head: Any) -> tuple[Any, ...]:
        return (
            head.id,
            head.document_id,
            head.document_revision,
            head.content_revision,
            head.content_hash,
            head.enabled,
            head.chunk_role,
        )

    @classmethod
    def _heads_signature(cls, heads: list[Any]) -> tuple[tuple[Any, ...], ...]:
        return tuple(cls._head_signature(head) for head in heads)

    @staticmethod
    def _writer_fence_matches(
        current: IndexOperation | None,
        document: Document | None,
        operation: IndexOperation,
        worker_id: str,
    ) -> bool:
        return bool(
            current is not None
            and document is not None
            and current.status == "claimed"
            and current.claimed_by == worker_id
            and current.tenant_id == operation.tenant_id
            and current.dataset_id == operation.dataset_id
            and current.document_id == operation.document_id
            and current.target_store == operation.target_store
            and current.operation == operation.operation
            and current.target_revision == operation.target_revision
            and current.document_generation == operation.document_generation
            and dict(current.payload or {}) == dict(operation.payload or {})
            and document.tenant_id == current.tenant_id
            and document.dataset_id == current.dataset_id
            and current.document_generation == document.mutation_generation
            and document.lifecycle_state == "active"
            and document.retrieval_enabled
            and document.desired_index_revision == current.target_revision
        )

    def _read_writer_fence(
        self, operation: IndexOperation
    ) -> tuple[IndexOperation, Document]:
        worker_id = str(getattr(operation, "claimed_by", "") or "")
        if not worker_id:
            raise StaleProjectionOperation("projection operation has no worker ownership")
        with Session(self.chunk_catalog.engine, expire_on_commit=False) as session:
            current = session.get(IndexOperation, operation.id)
            document = session.get(Document, operation.document_id)
            if not self._writer_fence_matches(
                current, document, operation, worker_id
            ):
                raise StaleProjectionOperation(
                    "projection writer ownership or document generation fence is stale"
                )
            return current, document

    def _is_current(self, operation: IndexOperation) -> bool:
        try:
            self._read_writer_fence(operation)
        except StaleProjectionOperation:
            return False
        return True

    def _assert_current(self, operation: IndexOperation) -> IndexOperation:
        current, _document = self._read_writer_fence(operation)
        return current

    def _mark_projection_revision_locked(self, operation: IndexOperation) -> None:
        worker_id = str(getattr(operation, "claimed_by", "") or "")
        with Session(self.chunk_catalog.engine) as session:
            document = session.get(Document, operation.document_id, with_for_update=True)
            current = session.get(IndexOperation, operation.id, with_for_update=True)
            if not self._writer_fence_matches(
                current, document, operation, worker_id
            ):
                raise StaleProjectionOperation(
                    "projection ownership was lost before revision commit"
                )
            if operation.target_store == "milvus_chunks":
                document.indexed_revision = max(
                    int(document.indexed_revision or 0), operation.target_revision
                )
            elif operation.target_store == "graph_projection":
                document.graph_revision = max(
                    int(document.graph_revision or 0), operation.target_revision
                )
            session.commit()
        setattr(operation, "_projection_revision_advanced", True)

    def _chunk_mutation_head(self, operation: IndexOperation) -> Any | None:
        payload = operation.payload if isinstance(operation.payload, dict) else {}
        mutation = payload.get("mutation")
        if mutation is None:
            return None
        chunk_id = payload.get("chunk_id")
        expected_revision = payload.get("expected_content_revision")
        if (
            mutation not in {"edit", "delete"}
            or not isinstance(chunk_id, str)
            or not chunk_id
            or type(expected_revision) is not int
        ):
            raise StaleProjectionOperation("invalid chunk mutation fence payload")
        try:
            head = self.chunk_catalog.get_head(chunk_id)
        except ChunkRevisionConflict as exc:
            raise StaleProjectionOperation("chunk mutation target no longer exists") from exc
        if (
            head.document_id != operation.document_id
            or head.tenant_id != operation.tenant_id
            or head.dataset_id != operation.dataset_id
            or head.document_revision != operation.target_revision
            or head.content_revision != expected_revision
            or head.chunk_role == "parent"
            or (mutation == "edit" and not head.enabled)
            or (mutation == "delete" and head.enabled)
        ):
            raise StaleProjectionOperation("chunk mutation revision fence is stale")
        return head

    @staticmethod
    def _canonical_text(head: Any) -> str:
        context = str(head.context_header or "").strip()
        return f"{context}\n\n{head.content}" if context else str(head.content)

    @staticmethod
    def _to_chunk(head: Any) -> Chunk:
        metadata = dict(head.chunk_metadata or {})
        metadata["chunk_index"] = head.chunk_index
        metadata["chunk_role"] = head.chunk_role
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

    def _assert_full_head_plan(
        self,
        operation: IndexOperation,
        expected_signature: tuple[tuple[Any, ...], ...],
    ) -> list[Any]:
        heads = self.chunk_catalog.list_projection_candidates(
            operation.document_id, document_revision=operation.target_revision
        )
        if self._heads_signature(heads) != expected_signature:
            raise StaleProjectionOperation("projection chunk-head plan changed")
        return heads

    def handle_milvus(self, operation: IndexOperation) -> None:
        if operation.operation == "delete_document":
            self._handle_milvus_document_delete(operation)
            return

        mutation_head = self._chunk_mutation_head(operation)
        mutation = None
        prepared_vector = None
        prepared_head_signature = None
        prepared_heads: list[Any] = []
        prepared_chunks: list[Chunk] = []
        prepared_vectors: Any = []
        prepared_signature: tuple[tuple[Any, ...], ...] = ()
        if mutation_head is not None:
            mutation = str(operation.payload["mutation"])
            prepared_head_signature = self._head_signature(mutation_head)
            if mutation == "edit":
                prepared_vector = self.embedder.embed_texts(
                    [self._canonical_text(mutation_head)]
                )
        else:
            prepared_heads = self.chunk_catalog.list_projection_candidates(
                operation.document_id, document_revision=operation.target_revision
            )
            prepared_signature = self._heads_signature(prepared_heads)
            prepared_chunks = [self._to_chunk(head) for head in prepared_heads]
            if prepared_chunks:
                prepared_vectors = self.embedder.embed_texts(
                    [self._canonical_text(head) for head in prepared_heads]
                )

        written_ids: list[str] = []
        with self._projection_lock(operation, store="milvus", include_chunk=True):
            current = self._assert_current(operation)
            try:
                if mutation_head is not None:
                    current_head = self._chunk_mutation_head(current)
                    if (
                        current_head is None
                        or self._head_signature(current_head) != prepared_head_signature
                    ):
                        raise StaleProjectionOperation(
                            "chunk mutation changed during embedding"
                        )
                    if mutation == "delete":
                        self.milvus.delete_by_ids([current_head.id])
                    else:
                        assert prepared_vector is not None
                        self.milvus.upsert_chunks(
                            [self._to_chunk(current_head)], prepared_vector
                        )
                        written_ids = [current_head.id]
                    post = self._assert_current(operation)
                    post_head = self._chunk_mutation_head(post)
                    if (
                        post_head is None
                        or self._head_signature(post_head) != prepared_head_signature
                    ):
                        raise StaleProjectionOperation(
                            "chunk mutation became stale after Milvus write"
                        )
                    if mutation == "edit":
                        self.chunk_catalog.mark_indexed(
                            post_head.id,
                            expected_revision=post_head.content_revision,
                        )
                    self._mark_projection_revision_locked(operation)
                    return

                current_heads = self._assert_full_head_plan(current, prepared_signature)
                if prepared_chunks:
                    self.milvus.upsert_chunks(prepared_chunks, prepared_vectors)
                    written_ids = [chunk.chunk_id for chunk in prepared_chunks]
                desired_ids = {chunk.chunk_id for chunk in prepared_chunks}
                existing = self.milvus.query_chunks_by_doc(
                    operation.document_id, tenant_id=operation.tenant_id
                )
                stale_ids = [
                    chunk.chunk_id
                    for chunk in existing
                    if chunk.chunk_id not in desired_ids
                ]
                if stale_ids:
                    self.milvus.delete_by_ids(stale_ids)
                post = self._assert_current(operation)
                self._assert_full_head_plan(post, prepared_signature)
                self._mark_projection_revision_locked(operation)
                for head in current_heads:
                    self.chunk_catalog.mark_indexed(
                        head.id, expected_revision=head.content_revision
                    )
                self._mark_projection_revision_locked(operation)
            except StaleProjectionOperation:
                if written_ids:
                    self.milvus.delete_by_ids(written_ids)
                raise

    @staticmethod
    def _projection_lock_keys(
        operation: IndexOperation, *, store: str, include_chunk: bool = False
    ) -> list[str]:
        payload = operation.payload if isinstance(operation.payload, dict) else {}
        scope = "\x1f".join(
            (operation.tenant_id, operation.dataset_id, operation.document_id)
        )
        keys = [f"projection-{store}-document\x1f{scope}"]
        chunk_id = payload.get("chunk_id") if include_chunk else None
        if isinstance(chunk_id, str) and chunk_id:
            keys.append(f"projection-{store}-chunk\x1f{scope}\x1f{chunk_id}")
        return keys

    @staticmethod
    def _postgres_lock_id(key: str) -> int:
        digest = hashlib.sha256(key.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], byteorder="big", signed=True)

    @staticmethod
    def _mysql_lock_name(key: str) -> str:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return f"rag4c:projection:{digest[:42]}"

    @contextmanager
    def _projection_lock(
        self, operation: IndexOperation, *, store: str, include_chunk: bool = False
    ):
        engine = self.chunk_catalog.engine
        keys = self._projection_lock_keys(
            operation, store=store, include_chunk=include_chunk
        )
        dialect = engine.dialect.name
        if dialect == "sqlite":
            # SQLite has no cross-process advisory lock. This keyed fallback is
            # intentionally single-process only (tests/local mode). Active workers
            # in multi-process production require MySQL or PostgreSQL below.
            database = engine.url.render_as_string(hide_password=True)
            with ExitStack() as stack:
                for key in keys:
                    stack.enter_context(process_keyed_lock(f"{database}\x1f{key}"))
                yield
            return

        with engine.connect() as connection:
            acquired: list[tuple[str, Any]] = []
            try:
                if dialect in {"mysql", "mariadb"}:
                    for key in keys:
                        lock_name = self._mysql_lock_name(key)
                        locked = connection.scalar(
                            text("SELECT GET_LOCK(:lock_name, :timeout_seconds)"),
                            {"lock_name": lock_name, "timeout_seconds": 120},
                        )
                        if int(locked or 0) != 1:
                            raise RuntimeError("timed out acquiring projection advisory lock")
                        acquired.append(("mysql", lock_name))
                elif dialect == "postgresql":
                    for key in keys:
                        lock_id = self._postgres_lock_id(key)
                        connection.execute(
                            text("SELECT pg_advisory_lock(:lock_id)"),
                            {"lock_id": lock_id},
                        )
                        acquired.append(("postgresql", lock_id))
                else:
                    raise RuntimeError(
                        f"projection advisory locks are unsupported for {dialect}"
                    )
                # End SQLAlchemy's implicit transaction before any LLM, embedding,
                # or graph-store call. The acquired locks are connection-scoped.
                connection.commit()
                yield
            finally:
                for lock_type, lock_value in reversed(acquired):
                    try:
                        if lock_type == "mysql":
                            connection.execute(
                                text("SELECT RELEASE_LOCK(:lock_name)"),
                                {"lock_name": lock_value},
                            )
                        else:
                            connection.execute(
                                text("SELECT pg_advisory_unlock(:lock_id)"),
                                {"lock_id": lock_value},
                            )
                    except Exception:  # noqa: BLE001 - connection loss releases locks
                        connection.invalidate()
                        break
                if not connection.invalidated:
                    connection.commit()

    @contextmanager
    def _graph_projection_lock(self, operation: IndexOperation):
        with self._projection_lock(
            operation, store="graph", include_chunk=True
        ):
            yield

    def _handle_milvus_document_delete(self, operation: IndexOperation) -> None:
        repository = DocumentDeletionRepository(self.chunk_catalog.engine)
        with self._projection_lock(operation, store="milvus"):
            plan = repository.projection_plan(operation)
            self.milvus.delete_by_doc_id(plan.document_id)
            flush = getattr(self.milvus, "flush", None)
            if not callable(flush):
                raise RuntimeError("Milvus client does not support durable flush")
            flush()
            remaining = self.milvus.count_chunks_by_document(
                plan.document_id,
                tenant_id=plan.tenant_id,
                consistency="strong",
            )
            if int(remaining or 0) != 0:
                raise RuntimeError("Milvus document chunks remain after durable delete")

    def _cleanup_stale_graph_write(
        self, chunk_ids: list[str], *, tenant_id: str
    ) -> None:
        if chunk_ids:
            self.graph_builder.delete_by_chunk_ids(chunk_ids, tenant_id=tenant_id)

    def _prepare_graph_build(self, chunks: list[tuple[str, str]]) -> Any:
        prepare = getattr(self.graph_builder, "prepare_build", None)
        if callable(prepare):
            return ("prepared", prepare(chunks))
        return ("legacy", chunks)

    def _write_graph_build(
        self,
        prepared: Any,
        *,
        tenant_id: str,
    ) -> Any:
        kind, value = prepared
        if kind == "prepared":
            return self.graph_builder.write_prepared(value, tenant_id=tenant_id)
        return self.graph_builder.build(value, tenant_id=tenant_id)

    def _handle_graph_document_delete(self, operation: IndexOperation) -> None:
        repository = DocumentDeletionRepository(self.chunk_catalog.engine)
        with self._projection_lock(operation, store="graph"):
            plan = repository.projection_plan(operation)
            chunk_ids = list(plan.manifest.chunk_ids)
            self.graph_builder.delete_by_chunk_ids(
                chunk_ids, tenant_id=plan.tenant_id
            )
            remaining = self.graph_builder.count_facts_by_chunk_ids(
                chunk_ids, tenant_id=plan.tenant_id
            )
            if int(remaining or 0) != 0:
                raise RuntimeError("graph facts remain after durable document delete")

    def handle_graph(self, operation: IndexOperation) -> None:
        if self.graph_builder is None:
            raise RuntimeError("graph projection handler is not configured")
        if operation.operation == "delete_document":
            self._handle_graph_document_delete(operation)
            return

        mutation_head = self._chunk_mutation_head(operation)
        mutation = None
        prepared_head_signature = None
        prepared_heads: list[Any] = []
        prepared_signature: tuple[tuple[Any, ...], ...] = ()
        graph_chunks: list[tuple[str, str]] = []
        if mutation_head is not None:
            mutation = str(operation.payload["mutation"])
            prepared_head_signature = self._head_signature(mutation_head)
            if mutation == "edit":
                graph_chunks = [
                    (mutation_head.id, self._canonical_text(mutation_head))
                ]
        else:
            prepared_heads = self.chunk_catalog.list_projection_candidates(
                operation.document_id, document_revision=operation.target_revision
            )
            prepared_signature = self._heads_signature(prepared_heads)
            graph_chunks = [
                (head.id, self._canonical_text(head)) for head in prepared_heads
            ]
        prepared_graph = (
            self._prepare_graph_build(graph_chunks)
            if mutation != "delete"
            else None
        )
        cleanup_ids = [chunk_id for chunk_id, _text in graph_chunks]

        with self._graph_projection_lock(operation):
            current = self._assert_current(operation)
            try:
                if mutation_head is not None:
                    current_head = self._chunk_mutation_head(current)
                    if (
                        current_head is None
                        or self._head_signature(current_head) != prepared_head_signature
                    ):
                        raise StaleProjectionOperation(
                            "graph chunk mutation changed during preparation"
                        )
                    self.graph_builder.delete_by_chunk_ids(
                        [current_head.id], tenant_id=operation.tenant_id
                    )
                    if mutation == "edit":
                        assert prepared_graph is not None
                        self._write_graph_build(
                            prepared_graph, tenant_id=operation.tenant_id
                        )
                    post = self._assert_current(operation)
                    post_head = self._chunk_mutation_head(post)
                    if (
                        post_head is None
                        or self._head_signature(post_head) != prepared_head_signature
                    ):
                        raise StaleProjectionOperation(
                            "graph chunk mutation became stale after write"
                        )
                    self._mark_projection_revision_locked(operation)
                    return

                self._assert_full_head_plan(current, prepared_signature)
                assert prepared_graph is not None
                self._write_graph_build(
                    prepared_graph, tenant_id=operation.tenant_id
                )
                post = self._assert_current(operation)
                self._assert_full_head_plan(post, prepared_signature)
            except StaleProjectionOperation:
                if cleanup_ids:
                    self._cleanup_stale_graph_write(
                        cleanup_ids, tenant_id=operation.tenant_id
                    )
                raise
