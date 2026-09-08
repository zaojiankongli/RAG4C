"""文档入库状态机（catalog 持久化 + 断点恢复 + 双粒度进度）。

把 IngestPipeline 的两段式入库包装为状态机驱动任务：

    waiting -> parsing -> splitting -> indexing -> completed / error

- 文档级进度：add_file 的阶段回调映射到绝对进度区间
  （parsing 0~0.25 / splitting 0.25~0.6 / indexing 0.6~1.0），
  每阶段状态经 catalog.set_document_status 持久化；
- 分段级进度：parse_and_chunk 的 splitting 回调携带「第 N/M 段」，
  由调用方按需经 catalog.upsert_segment 登记（文档级状态机预留）；
- 失败：状态置 error + error_message，可重试（error 允许回到任意前序
  阶段，整链幂等 upsert 保证重复执行安全）；
- 断点恢复：completed 跳过；waiting / error 可直接 run；进程重启后
  状态仍在 SQLite，调用方扫描 waiting/error 重排队即可。

用法::

    job = DocumentIngestJob(pipeline, doc_id="doc-xxx", dataset_id="dataset-fin")
    result = job.run("path/to/file.pdf")   # waiting -> ... -> completed
    result = job.run("path/to/file.pdf")   # 已 completed：跳过
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from core import cache_epoch, catalog
from core.db_clock import read_db_utc
from core.observability import get_logger
from indexing.ingest import QuotaExceededError
from models.orm import (
    DataSourceRecord,
    Dataset,
    Document,
    DocumentIngestAttempt,
    SourceSyncRun,
)

_logger = get_logger(__name__)


class DocumentWriteSuperseded(RuntimeError):
    """A writer permit no longer matches active document desired state."""

    status_code = 409


@dataclass(frozen=True)
class DocumentWritePermit:
    tenant_id: str
    dataset_id: str
    document_id: str
    document_generation: int
    dataset_generation: int
    source_id: str | None = None
    source_generation: int | None = None
    source_run_id: str | None = None
    execution_owner: str | None = None


def _writer_engine():
    get_engine = getattr(catalog, "get_engine", None)
    if not callable(get_engine):
        raise DocumentWriteSuperseded("document writer authority is unavailable")
    return get_engine()


def _validate_writer_rows(
    document: Document | None,
    dataset: Dataset | None,
    source: DataSourceRecord | None = None,
    *,
    permit: DocumentWritePermit | None = None,
) -> DocumentWritePermit:
    if document is None or dataset is None:
        raise DocumentWriteSuperseded("document writer target no longer exists")
    if document.tenant_id != dataset.tenant_id or document.dataset_id != dataset.id:
        raise DocumentWriteSuperseded("document writer scope changed")
    if (
        document.lifecycle_state != "active"
        or not bool(document.retrieval_enabled)
        or bool(document.active_delete_operation_id)
        or dataset.status != "active"
    ):
        raise DocumentWriteSuperseded("document is not active for mutation")
    expected_source_id = permit.source_id if permit is not None else (source.id if source else None)
    expected_source_generation = (
        permit.source_generation if permit is not None else None
    )
    if expected_source_id is not None:
        if (
            source is None
            or source.id != expected_source_id
            or source.tenant_id != document.tenant_id
            or source.dataset_id != document.dataset_id
            or source.status != "active"
        ):
            raise DocumentWriteSuperseded("source writer authority changed")
        if (
            expected_source_generation is not None
            and int(source.mutation_generation or 0) != expected_source_generation
        ):
            raise DocumentWriteSuperseded("source writer generation changed")
    current = DocumentWritePermit(
        tenant_id=str(document.tenant_id),
        dataset_id=str(document.dataset_id),
        document_id=str(document.id),
        document_generation=int(document.mutation_generation or 0),
        dataset_generation=int(dataset.mutation_generation or 0),
        source_id=expected_source_id,
        source_generation=(
            int(source.mutation_generation or 0) if expected_source_id is not None else None
        ),
        source_run_id=permit.source_run_id if permit is not None else None,
        execution_owner=permit.execution_owner if permit is not None else None,
    )
    if permit is not None and current != permit:
        raise DocumentWriteSuperseded("document writer generation changed")
    return current


def read_document_write_permit(
    document_id: str,
    *,
    dataset_id: str = "",
    engine: Any = None,
    source_id: str | None = None,
    source_generation: int | None = None,
    source_run_id: str | None = None,
    execution_owner: str | None = None,
) -> DocumentWritePermit:
    with Session(engine or _writer_engine()) as session:
        document = session.get(Document, document_id)
        if document is None:
            raise DocumentWriteSuperseded("document writer target no longer exists")
        if dataset_id and document.dataset_id != dataset_id:
            raise DocumentWriteSuperseded("document writer dataset changed")
        dataset = session.scalar(
            select(Dataset).where(
                Dataset.id == document.dataset_id, Dataset.tenant_id == document.tenant_id
            )
        )
        source = session.get(DataSourceRecord, source_id) if source_id else None
        current = _validate_writer_rows(document, dataset, source)
        if source_id is None:
            return current
        expected = DocumentWritePermit(
            tenant_id=current.tenant_id,
            dataset_id=current.dataset_id,
            document_id=current.document_id,
            document_generation=current.document_generation,
            dataset_generation=current.dataset_generation,
            source_id=source_id,
            source_generation=(
                int(source_generation)
                if source_generation is not None
                else current.source_generation
            ),
            source_run_id=source_run_id,
            execution_owner=execution_owner,
        )
        _validate_writer_rows(document, dataset, source, permit=expected)
        return expected


def assert_document_write_permit(
    permit: DocumentWritePermit,
    *,
    session: Session | None = None,
    for_update: bool = False,
    engine: Any = None,
) -> tuple[Document, Dataset]:
    owned = session is None
    active_session = session or Session(engine or _writer_engine())
    try:
        document_query = select(Document).where(
            Document.id == permit.document_id,
            Document.tenant_id == permit.tenant_id,
            Document.dataset_id == permit.dataset_id,
        )
        dataset_query = select(Dataset).where(
            Dataset.id == permit.dataset_id, Dataset.tenant_id == permit.tenant_id
        )
        if for_update:
            document_query = document_query.with_for_update()
            dataset_query = dataset_query.with_for_update()
        dataset = active_session.scalar(dataset_query)
        document = active_session.scalar(document_query)
        source = None
        if permit.source_id is not None:
            source_query = select(DataSourceRecord).where(
                DataSourceRecord.id == permit.source_id
            )
            if for_update:
                source_query = source_query.with_for_update()
            source = active_session.scalar(source_query)
        _validate_writer_rows(document, dataset, source, permit=permit)
        if permit.source_run_id is not None:
            run_query = select(SourceSyncRun).where(SourceSyncRun.id == permit.source_run_id)
            if for_update:
                run_query = run_query.with_for_update()
            source_run = active_session.scalar(run_query)
            database_now = read_db_utc(active_session)
            if (
                source_run is None
                or not permit.execution_owner
                or source_run.status != "running"
                or source_run.execution_state != "executing"
                or source_run.execution_owner != permit.execution_owner
                or source_run.execution_lease_until is None
                or database_now is None
                or source_run.execution_lease_until < database_now
            ):
                raise DocumentWriteSuperseded("source execution authority changed")
        assert document is not None and dataset is not None
        return document, dataset
    finally:
        if owned:
            active_session.close()


def _file_sha256(path: str) -> str:
    """文件字节级 sha256（供状态机记录，增量重索引检测变更）。"""
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


# 阶段 -> 文档级绝对进度区间。
# 权重按实测耗时分配：建图（每片段一次 LLM 抽取）通常是最慢的一段，
# 因此单独占据末段 15%，不再挤在 indexing 里——否则界面会长时间停在
# 「indexing 100%」，看上去像卡死。
_STAGE_SPAN: dict[str, tuple[float, float]] = {
    "parsing": (0.0, 0.20),
    "splitting": (0.20, 0.45),
    "contextual": (0.45, 0.60),
    "indexing": (0.60, 0.85),
    "graph": (0.85, 1.0),
}

# 阶段 -> 状态机状态。
# 文档状态机只有 waiting/parsing/splitting/indexing/completed/error 六态
# （models/orm.py），不为每个子阶段新增状态——contextual 归入 splitting、
# graph 归入 indexing，真正的子阶段信息由 status_detail 承载。
# 这样既能表达细粒度进度，又不必改数据库状态机与迁移。
_STAGE_STATUS: dict[str, str] = {
    "parsing": "parsing",
    "splitting": "splitting",
    "contextual": "splitting",
    "indexing": "indexing",
    "graph": "indexing",
}


def _record_ingest_metrics(meta: dict[str, Any], *, error: bool = False) -> None:
    """Publish persisted ingest telemetry to process metrics without affecting jobs."""
    try:
        from core.metrics import get_metrics

        metrics = get_metrics()
        if error:
            metrics.incr("ingest.errors")
            return
        total_ms = meta.get("total_ms")
        if isinstance(total_ms, (int, float)):
            metrics.observe("ingest.total", float(total_ms))
        stages = meta.get("stage_ms") if isinstance(meta.get("stage_ms"), dict) else {}
        for stage, value in stages.items():
            if isinstance(value, (int, float)):
                metrics.observe(f"ingest.stage.{stage}", float(value))
    except Exception:
        pass


class DocumentIngestJob:
    """单文档入库任务；可选把运行事实 shadow-write 到 durable ledger。"""

    def __init__(
        self,
        pipeline: Any,
        doc_id: str,
        dataset_id: str = "",
        on_segment: Optional[Callable[[int, int], None]] = None,
        *,
        ledger: Any = None,
        operation_queue: Any = None,
        chunk_catalog: Any = None,
        ledger_mode: str = "off",
        attempt_kind: str = "ingest",
        source_id: str | None = None,
        source_generation: int | None = None,
        source_run_id: str | None = None,
        execution_owner: str | None = None,
    ):
        if ledger_mode not in {"off", "shadow", "active"}:
            raise ValueError("ledger_mode 必须是 off / shadow / active")
        self.pipeline = pipeline
        self.doc_id = doc_id
        self.dataset_id = dataset_id
        self.on_segment = on_segment
        self.ledger = ledger
        self.operation_queue = operation_queue
        self.chunk_catalog = chunk_catalog
        self.ledger_mode = ledger_mode
        self.attempt_kind = str(attempt_kind or "ingest")
        self.source_id = str(source_id or "").strip() or None
        if source_generation is not None and (
            type(source_generation) is not int or source_generation < 0
        ):
            raise ValueError("source_generation must be a non-negative integer")
        if (self.source_id is None) != (source_generation is None):
            raise ValueError("source_id and source_generation must be provided together")
        self.source_generation = source_generation
        self.source_run_id = source_run_id
        self.execution_owner = execution_owner
        self._active_attempt_id: str | None = None
        self._open_ledger_spans: set[str] = set()
        self._closed_ledger_spans: set[str] = set()
        self._writer_permit: DocumentWritePermit | None = None

    def _ledger_call(self, action: str, callback: Callable[[], Any]) -> Any:
        try:
            return callback()
        except Exception as exc:  # noqa: BLE001 - shadow mode must not break ingestion
            if self.ledger_mode == "active":
                raise
            _logger.warning("入库 ledger shadow 写入失败（%s）: %s", action, str(exc)[:200])
            return None

    def _start_ledger_attempt(
        self,
        doc: dict[str, Any],
        input_hash: str,
        target_revision: int,
        permit: DocumentWritePermit,
    ) -> Any:
        if self.ledger_mode == "off" or self.ledger is None:
            return None
        engine = getattr(self.ledger, "engine", None)
        if engine is None:
            return self._ledger_call(
                "start_attempt",
                lambda: self.ledger.start_attempt(
                    tenant_id=doc["tenant_id"],
                    dataset_id=self.dataset_id or doc["dataset_id"],
                    document_id=self.doc_id,
                    input_content_hash=input_hash,
                    input_revision=target_revision,
                    attempt_kind=self.attempt_kind,
                    document_generation=permit.document_generation,
                ),
            )
        try:
            with Session(engine, expire_on_commit=False) as session:
                with session.begin():
                    assert_document_write_permit(
                        permit, session=session, for_update=True
                    )
                    attempt = self.ledger.start_attempt_in_session(
                        tenant_id=doc["tenant_id"],
                        dataset_id=self.dataset_id or doc["dataset_id"],
                        document_id=self.doc_id,
                        input_content_hash=input_hash,
                        input_revision=target_revision,
                        attempt_kind=self.attempt_kind,
                        document_generation=permit.document_generation,
                        session=session,
                    )
            self._active_attempt_id = str(attempt.id)
            return attempt
        except Exception as exc:  # noqa: BLE001 - shadow remains fail-open
            if self.ledger_mode == "active" or isinstance(
                exc, DocumentWriteSuperseded
            ):
                raise
            _logger.warning(
                "入库 ledger shadow 写入失败（start_attempt）: %s", str(exc)[:200]
            )
            return None

    def _record_ledger_progress(self, stage: str, fraction: float, detail: str) -> None:
        if self._active_attempt_id is None or self.ledger is None:
            return
        if stage in self._closed_ledger_spans:
            return
        if stage not in self._open_ledger_spans:
            created = self._ledger_call(
                f"start_span:{stage}",
                lambda: self.ledger.start_span(
                    self._active_attempt_id,
                    span_id=stage,
                    name=stage,
                    kind="stage",
                    metadata={"detail": detail[:256]},
                ),
            )
            if created is None:
                return
            self._open_ledger_spans.add(stage)
            if stage == "graph":
                self._ledger_call(
                    "attempt_finalizing",
                    lambda: self.ledger.set_attempt_state(
                        self._active_attempt_id, "finalizing"
                    ),
                )
        if fraction >= 1.0:
            finished = self._ledger_call(
                f"finish_span:{stage}",
                lambda: self.ledger.finish_span(
                    self._active_attempt_id,
                    stage,
                    "done",
                    output={"detail": detail[:256]},
                ),
            )
            if finished is not None:
                self._open_ledger_spans.discard(stage)
                self._closed_ledger_spans.add(stage)
                if stage == "indexing":
                    self._ledger_call(
                        "attempt_primary_ready",
                        lambda: self.ledger.set_attempt_state(
                            self._active_attempt_id, "primary_ready"
                        ),
                    )

    def _fail_open_ledger_spans(self, error_message: str) -> None:
        if self._active_attempt_id is None or self.ledger is None:
            return
        for stage in tuple(self._open_ledger_spans):
            finished = self._ledger_call(
                f"fail_span:{stage}",
                lambda stage=stage: self.ledger.finish_span(
                    self._active_attempt_id,
                    stage,
                    "failed",
                    error_code="INGEST_STAGE_FAILED",
                    error_message=error_message,
                ),
            )
            if finished is not None:
                self._open_ledger_spans.discard(stage)
                self._closed_ledger_spans.add(stage)

    def _finish_ledger_attempt(self, state: str, error_message: str = "") -> None:
        if self._active_attempt_id is None or self.ledger is None:
            return
        self._ledger_call(
            f"finish_attempt:{state}",
            lambda: self.ledger.finish_attempt(
                self._active_attempt_id,
                state,
                error_code="INGEST_FAILED" if state == "failed" else "",
                error_message=error_message,
            ),
        )


    def _persist_chunk_heads(
        self,
        doc: dict[str, Any],
        chunks: list[Any],
        target_revision: int,
        *,
        session: Session | None = None,
    ) -> None:
        if self.chunk_catalog is None:
            for chunk in chunks:
                chunk.document_revision = target_revision
                chunk.content_revision = int(getattr(chunk, "content_revision", 0) or 0)
            return
        for fallback_index, chunk in enumerate(chunks):
            metadata = dict(getattr(chunk, "metadata", {}) or {})
            is_parent = bool(metadata.get("is_parent"))
            parent_chunk_id = getattr(chunk, "parent_chunk_id", None)
            role = "parent" if is_parent else "child" if parent_chunk_id else "flat"
            chunk_index = int(metadata.get("chunk_index", fallback_index))
            chunk.document_revision = target_revision
            chunk.content_revision = int(getattr(chunk, "content_revision", 0) or 0)
            def callback(
                chunk=chunk,
                role=role,
                chunk_index=chunk_index,
                metadata=metadata,
            ):
                return self.chunk_catalog.upsert_head(
                    chunk_id=str(chunk.chunk_id),
                    tenant_id=doc["tenant_id"],
                    dataset_id=self.dataset_id or doc["dataset_id"],
                    document_id=self.doc_id,
                    parent_chunk_id=getattr(chunk, "parent_chunk_id", None),
                    chunk_index=chunk_index,
                    chunk_role=role,
                    document_revision=target_revision,
                    source_content=str(chunk.text),
                    content=str(chunk.text),
                    content_hash=str(chunk.text_hash),
                    enabled=True,
                    metadata=metadata,
                    session=session,
                )
            if session is None:
                self._ledger_call(f"upsert_chunk_head:{chunk.chunk_id}", callback)
            else:
                callback()

    def _enqueue_projection_intent(
        self,
        doc: dict[str, Any],
        attempt: Any,
        chunks: list[Any],
        target_revision: int,
        *,
        include_graph: bool = False,
        permit: DocumentWritePermit,
        session: Session | None = None,
    ) -> None:
        if attempt is None or self.operation_queue is None:
            return
        chunk_ids = [str(chunk.chunk_id) for chunk in chunks]
        initial_status = "shadow" if self.ledger_mode == "shadow" else "pending"
        stores = ["milvus_chunks"]
        if include_graph:
            stores.append("graph_projection")
        for target_store in stores:
            operation = self.operation_queue.enqueue_operation(
                tenant_id=doc["tenant_id"],
                dataset_id=self.dataset_id or doc["dataset_id"],
                document_id=self.doc_id,
                attempt_id=attempt.id,
                target_store=target_store,
                operation="upsert",
                dedup_key=f"{self.doc_id}:{target_revision}:{'milvus' if target_store == 'milvus_chunks' else 'graph'}:upsert",
                target_revision=target_revision,
                payload={
                    "chunk_ids": chunk_ids,
                    "document_generation": permit.document_generation,
                    "dataset_generation": permit.dataset_generation,
                    **(
                        {"source_generation": self.source_generation}
                        if self.source_generation is not None
                        else {}
                    ),
                },
                initial_status=initial_status,
                session=session,
            )
            operation.document_generation = permit.document_generation

    def _persist_authority_and_enqueue(
        self,
        doc: dict[str, Any],
        attempt: Any,
        chunks: list[Any],
        target_revision: int,
        *,
        include_graph: bool,
        permit: DocumentWritePermit,
    ) -> None:
        if self.chunk_catalog is None:
            assert_document_write_permit(permit)
            self._persist_chunk_heads(doc, chunks, target_revision)
            self._enqueue_projection_intent(
                doc,
                attempt,
                chunks,
                target_revision,
                include_graph=include_graph,
                permit=permit,
            )
            return
        engine = self.chunk_catalog.engine
        if self.operation_queue is not None and self.operation_queue.engine is not engine:
            raise RuntimeError("chunk authority and outbox must share one engine")
        with Session(engine, expire_on_commit=False) as session:
            with session.begin():
                assert_document_write_permit(permit, session=session, for_update=True)
                attempt_row = (
                    session.get(DocumentIngestAttempt, str(attempt.id))
                    if attempt is not None
                    else None
                )
                if attempt_row is not None:
                    attempt_row.attempt_kind = self.attempt_kind
                    attempt_row.document_generation = permit.document_generation
                self._persist_chunk_heads(
                    doc, chunks, target_revision, session=session
                )
                self._enqueue_projection_intent(
                    doc,
                    attempt_row or attempt,
                    chunks,
                    target_revision,
                    include_graph=include_graph,
                    permit=permit,
                    session=session,
                )

    def _on_progress(self, stage: str, fraction: float, detail: str) -> None:
        if self._writer_permit is not None:
            assert_document_write_permit(self._writer_permit)
        span = _STAGE_SPAN.get(stage)
        status = _STAGE_STATUS.get(stage)
        if span is None or status is None:
            return
        lo, hi = span
        abs_progress = lo + fraction * (hi - lo)
        catalog.set_document_status(
            self.doc_id, status, detail, progress=abs_progress
        )
        self._record_ledger_progress(stage, fraction, detail)
        if stage == "splitting" and self.on_segment is not None:
            import re

            match = re.search(r"(\d+)/(\d+)", detail)
            if match:
                self.on_segment(int(match.group(1)), int(match.group(2)))

    def _collect_meta(self, result: Any = None) -> dict[str, Any]:
        collect = getattr(self.pipeline, "ingest_meta", None)
        if not callable(collect):
            return {}
        try:
            meta = collect(result)
        except Exception:  # noqa: BLE001 - observability must not change outcome
            return {}
        return meta if isinstance(meta, dict) else {}

    def run(
        self,
        file_path: str,
        doc_type: str | None = None,
        metadata: dict[str, Any] | None = None,
        graph: bool | None = None,
    ) -> dict[str, Any]:
        doc = catalog.get_document(self.doc_id)
        if doc is None:
            raise KeyError(f"文档未登记: {self.doc_id}（先 catalog.create_document）")
        permit = read_document_write_permit(
            self.doc_id,
            dataset_id=self.dataset_id or str(doc.get("dataset_id") or ""),
            source_id=self.source_id,
            source_generation=self.source_generation,
            source_run_id=self.source_run_id,
            execution_owner=self.execution_owner,
        )
        self._writer_permit = permit
        if doc["status"] == "completed":
            return {"status": "completed", "chunk_count": doc["chunk_count"], "skipped": True}

        try:
            try:
                input_hash = _file_sha256(file_path)
            except Exception:  # noqa: BLE001 - parser path keeps the authoritative error
                input_hash = ""
            current_revision = int(doc.get("content_revision") or 0)
            changed = bool(
                input_hash and input_hash != str(doc.get("file_hash") or "")
            )
            target_revision = max(1, current_revision + (1 if changed else 0))
            assert_document_write_permit(permit)
            attempt = self._start_ledger_attempt(
                doc, input_hash, target_revision, permit
            )
            assert_document_write_permit(permit)
            catalog.set_document_status(
                self.doc_id, "parsing", "开始解析", progress=0.01
            )
            chunks = self.pipeline.parse_and_chunk(
                file_path,
                doc_id=self.doc_id,
                doc_type=doc_type,
                metadata=metadata,
                tenant_id=doc["tenant_id"],
                dataset_id=self.dataset_id,
                progress=self._on_progress,
            )
            assert_document_write_permit(permit)
            catalog.set_document_status(
                self.doc_id, "splitting", "解析切分完成", progress=0.45
            )
            if not chunks:
                empty_meta = self._collect_meta(None)
                assert_document_write_permit(permit)
                catalog.set_document_status(
                    self.doc_id,
                    "completed",
                    "空文档（无可索引内容）",
                    progress=1.0,
                    chunk_count=0,
                    file_hash=input_hash,
                    parser_meta=empty_meta,
                    content_revision=target_revision,
                    desired_index_revision=target_revision,
                    indexed_revision=target_revision,
                )
                self._finish_ledger_attempt("completed")
                _record_ingest_metrics(empty_meta)
                return {"status": "completed", "chunk_count": 0}

            include_graph = bool(
                (getattr(self.pipeline, "graph_enabled", False) if graph is None else graph)
                and getattr(self.pipeline, "graph_builder", None) is not None
            )
            self._persist_authority_and_enqueue(
                doc,
                attempt,
                chunks,
                target_revision,
                include_graph=include_graph,
                permit=permit,
            )
            if self.ledger_mode == "active":
                assert_document_write_permit(permit)
                catalog.set_document_status(
                    self.doc_id,
                    "indexing",
                    "等待持久化投影 worker",
                    progress=0.85,
                    chunk_count=len(chunks),
                    file_hash=input_hash,
                    content_revision=target_revision,
                    desired_index_revision=target_revision,
                )
                return {
                    "status": "indexing",
                    "chunk_count": len(chunks),
                    "queued": True,
                }
            assert_document_write_permit(permit)
            result = self.pipeline.embed_and_insert(
                chunks,
                tenant_id=doc["tenant_id"],
                graph=graph,
                progress=self._on_progress,
            )

            assert_document_write_permit(permit)
            ingest_meta = self._collect_meta(result)
            graph_revision = target_revision if getattr(result, "graph", None) is not None else None
            catalog.set_document_status(
                self.doc_id,
                "completed",
                "入库完成",
                progress=1.0,
                chunk_count=result.chunk_count,
                file_hash=input_hash,
                parser_meta=ingest_meta,
                content_revision=target_revision,
                desired_index_revision=target_revision,
                indexed_revision=target_revision,
                graph_revision=graph_revision,
            )
            self._finish_ledger_attempt("completed")
            _record_ingest_metrics(ingest_meta)
            _logger.info("文档 %s 入库完成（%d chunks）", self.doc_id, result.chunk_count)
            cache_epoch.bump(doc["tenant_id"], reason=f"入库 {self.doc_id}")
            return {"status": "completed", "chunk_count": result.chunk_count}
        except DocumentWriteSuperseded as exc:
            self._fail_open_ledger_spans(str(exc))
            self._finish_ledger_attempt("superseded", str(exc))
            return {"status": "superseded", "error": str(exc)}
        except QuotaExceededError as exc:
            try:
                assert_document_write_permit(permit)
            except DocumentWriteSuperseded as superseded:
                self._fail_open_ledger_spans(str(superseded))
                self._finish_ledger_attempt("superseded", str(superseded))
                return {"status": "superseded", "error": str(superseded)}
            self._fail_open_ledger_spans(str(exc))
            self._finish_ledger_attempt("failed", str(exc))
            catalog.set_document_status(
                self.doc_id,
                "error",
                "配额不足，未入库",
                error_message=str(exc)[:500],
            )
            _record_ingest_metrics({}, error=True)
            _logger.info("文档 %s 因配额被拒: %s", self.doc_id, str(exc)[:200])
            return {"status": "error", "error": str(exc)[:500], "quota_exceeded": True}
        except Exception as exc:  # noqa: BLE001 - state machine terminal guard
            try:
                assert_document_write_permit(permit)
            except DocumentWriteSuperseded as superseded:
                self._fail_open_ledger_spans(str(superseded))
                self._finish_ledger_attempt("superseded", str(superseded))
                return {"status": "superseded", "error": str(superseded)}
            self._fail_open_ledger_spans(str(exc))
            self._finish_ledger_attempt("failed", str(exc))
            catalog.set_document_status(
                self.doc_id,
                "error",
                "入库失败",
                error_message=str(exc)[:500],
            )
            _record_ingest_metrics({}, error=True)
            _logger.warning("文档 %s 入库失败: %s", self.doc_id, str(exc)[:200])
            return {"status": "error", "error": str(exc)[:500]}



__all__ = [
    "DocumentIngestJob",
    "DocumentWritePermit",
    "DocumentWriteSuperseded",
    "assert_document_write_permit",
    "read_document_write_permit",
]
