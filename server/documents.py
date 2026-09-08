"""文档管理 API：入库（后台任务）/ 状态查询 / 增量重索引。

端点：
- GET  /api/documents?dataset_id=&tenant_id=   文档列表（状态/进度）
- GET  /api/documents/{doc_id}                 文档详情（含分段进度汇总）
- POST /api/documents/ingest                   登记并后台执行入库
- POST /api/documents/ingest-folder            递归登记文件夹并后台逐个入库
- POST /api/documents/{doc_id}/reindex         增量重索引（后台）

入库任务在后台线程执行，状态机进度持久化于 catalog（SQLite），
前端轮询 GET /api/documents/{doc_id} 获取实时进度；进程重启后
waiting / error 状态的文档可再次 ingest 续跑（幂等）。
"""
from __future__ import annotations

import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from core import catalog
from core.chunk_catalog import ChunkRevisionConflict
from core.document_deletion import (
    DeleteBatchItemRequest,
    DeleteBatchProjection,
    DeleteOperationProjection,
    DocumentDeletionRepository,
)
from core.knowledge_content import ContentConflict, ContentNotFound
from core.knowledge_permissions import KNOWLEDGE_DELETE, KNOWLEDGE_READ
from core.observability import get_logger
from server.knowledge_auth import (
    KnowledgeActor,
    require_knowledge_permission,
    resolve_path_dataset,
)

_logger = get_logger("server.documents")

# 项目根目录：本文件位于 <root>/server/documents.py（提示词模板等资源定位用）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

router = APIRouter(prefix="/api", tags=["documents"])

# 后台任务注册表：doc_id -> {running: bool}
_jobs: dict[str, dict[str, bool]] = {}
_jobs_lock = threading.Lock()
INGEST_MAX_CONCURRENT = max(1, int(os.environ.get("RAG4C_INGEST_MAX_CONCURRENT", "2")))
INGEST_QUEUE_MAX = max(
    INGEST_MAX_CONCURRENT,
    int(os.environ.get("RAG4C_INGEST_QUEUE_MAX", "8")),
)
_job_capacity = threading.BoundedSemaphore(INGEST_QUEUE_MAX)
_executor_lock = threading.RLock()
_ingest_executor: ThreadPoolExecutor | None = None
_pipeline_local = threading.local()
_pipelines: set[Any] = set()
_pipelines_lock = threading.Lock()
# 配置代次：配置保存后 +1，各入库工作线程在取用管线时发现落后即按新配置重建
_pipeline_generation = 0
_generation_lock = threading.Lock()
_index_operation_worker_lock = threading.RLock()
_index_operation_worker_stop = threading.Event()
_index_operation_worker_thread: threading.Thread | None = None

# 文件夹导入既覆盖无需解析器的文本格式，也覆盖解析器支持的版面文档。
# 这里显式列出是为了让目录扫描在装配重型解析引擎之前就能完成过滤。
FOLDER_IMPORT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".adoc",
        ".asciidoc",
        ".doc",
        ".docx",
        ".jpeg",
        ".jpg",
        ".markdown",
        ".md",
        ".mdx",
        ".pdf",
        ".png",
        ".ppt",
        ".pptx",
        ".rst",
        ".text",
        ".txt",
        ".xls",
        ".xlsx",
    }
)


@dataclass(frozen=True)
class FolderScanResult:
    files: list[Path]
    discovered_count: int
    unsupported_count: int


def discover_folder_documents(folder_path: Path) -> FolderScanResult:
    """递归发现可入库文件；隐藏项、符号链接和不支持格式不进入任务。"""
    files: list[Path] = []
    discovered_count = 0
    unsupported_count = 0

    for current, dir_names, file_names in os.walk(folder_path, followlinks=False):
        dir_names[:] = sorted(
            name
            for name in dir_names
            if not name.startswith(".") and not (Path(current) / name).is_symlink()
        )
        for name in sorted(file_names):
            if name.startswith("."):
                continue
            path = Path(current) / name
            if path.is_symlink() or not path.is_file():
                continue
            discovered_count += 1
            if path.suffix.lower() not in FOLDER_IMPORT_EXTENSIONS:
                unsupported_count += 1
                continue
            files.append(path)

    files.sort(key=lambda path: str(path).casefold())
    return FolderScanResult(
        files=files,
        discovered_count=discovered_count,
        unsupported_count=unsupported_count,
    )


def reset_ingest_pipelines() -> None:
    """使所有入库线程的管线缓存失效（配置热更新用，幂等）。

    入库管线是 thread-local 的，无法从当前线程清除其它工作线程的缓存，
    因此这里只递增代次计数器；每个工作线程在下次取用时对比代次，发现
    落后就按最新配置重建。不影响正在执行中的入库任务——它们已经持有
    旧管线的引用，会用旧配置安全跑完。
    """
    global _pipeline_generation
    with _generation_lock:
        _pipeline_generation += 1


def start_ingest_executor() -> ThreadPoolExecutor:
    """Ensure the bounded ingest worker pool exists and return it."""
    global _ingest_executor
    with _executor_lock:
        if _ingest_executor is None:
            _ingest_executor = ThreadPoolExecutor(
                max_workers=INGEST_MAX_CONCURRENT,
                thread_name_prefix="rag-ingest",
            )
        return _ingest_executor


def shutdown_ingest_executor(*, wait: bool = True) -> None:
    """Stop ingest workers; a later startup can create a fresh pool."""
    global _ingest_executor
    with _executor_lock:
        executor = _ingest_executor
        _ingest_executor = None
        if executor is not None:
            executor.shutdown(wait=wait, cancel_futures=True)
    if wait:
        with _pipelines_lock:
            pipelines = list(_pipelines)
            _pipelines.clear()
        for pipeline in pipelines:
            close = getattr(getattr(pipeline, "milvus", None), "close", None)
            if callable(close):
                close()


def _build_ingest_optional(s: Any) -> dict[str, Any]:
    """按 pipeline 段开关构造入库端可选组件（失败降级为 None）。

    与检索侧 ``rag._build_optional_components`` 同构：关闭的策略不构造，
    构造失败只让该策略不可用，不影响文档入库主链路。
    """
    optional: dict[str, Any] = {
        "cleaner": None,
        "contextualizer": None,
        "graph_builder": None,
    }
    p = s.pipeline

    if getattr(p, "clean_on", True):
        try:
            from indexing.cleaner import create_cleaner

            optional["cleaner"] = create_cleaner()
        except Exception as exc:  # noqa: BLE001
            _logger.warning("入库清洗组件装配失败，按关闭处理: %s", exc)

    if getattr(p, "contextual_on", False):
        try:
            from core.llm import create_client
            from indexing.contextual import Contextualizer

            optional["contextualizer"] = Contextualizer(
                llm_client=create_client(s.llm.contextual),
                template_path=_PROJECT_ROOT / "prompts" / "contextual_v1.txt",
                enabled=True,
                concurrency=int(getattr(p, "contextual_concurrency", 4)),
                document_char_limit=int(getattr(p, "contextual_document_chars", 4000)),
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Contextual Retrieval 装配失败，按关闭处理: %s", exc)

    if getattr(p, "graph_index_on", False):
        try:
            from core.embedding import create_embedder
            from core.graph_store import RagGraphStore
            from core.llm import create_client
            from indexing.graph_builder import GraphBuilder
            from indexing.triplet_extractor import TripletExtractor

            # 注意：这里只构造，不调用 ensure_collections()——建集合会真正连
            # Milvus，而本函数在装配期执行。集合创建推迟到入库任务里做
            # （见 ingest / reindex 的 _run），与 pipeline.ensure_collection()
            # 同一时机，避免 Milvus Lite 上对同一 .db 文件开出第二个句柄。
            optional["graph_builder"] = GraphBuilder(
                store=RagGraphStore(s.milvus, s.graph),
                embedder=create_embedder(s.embedding),
                extractor=TripletExtractor(llm=create_client(s.llm.triplet)),
                extract_concurrency=int(getattr(p, "graph_extract_concurrency", 8)),
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning("知识图谱索引组件装配失败，按关闭处理: %s", exc)

    return optional


def _ensure_graph_collections(pipeline: Any) -> None:
    """入库任务开始前确保图集合存在（幂等；失败不阻断文档入库）。

    与 ``pipeline.ensure_collection()`` 同一时机调用：此时已在后台任务
    线程内，连接 Milvus 是预期行为。
    """
    builder = getattr(pipeline, "graph_builder", None)
    store = getattr(builder, "store", None)
    if store is None:
        return
    try:
        store.ensure_collections()
    except Exception as exc:  # noqa: BLE001 - 图集合不可用时退化为不建图
        _logger.warning("图集合初始化失败，本次入库跳过图索引: %s", exc)
        pipeline.graph_builder = None


def _get_ingest_pipeline() -> Any:
    """惰性装配入库管线（与检索共享 embedder / milvus 配置）。

    解析器走 DeepDoc 双引擎路由：pdf-inspector 分类决定
    fast（文本型 PDF）或 vision（MinerU，扫描件/Office）。

    切分模式与入库端增强（清洗 / Contextual Retrieval / 图索引）均按
    ``settings.pipeline`` 的开关装配，与配置中心展示的开关一一对应。
    """
    from config.settings import get_settings
    from core.embedding import create_embedder
    from core.milvus_client import RagMilvusClient
    from indexing.ingest import IngestPipeline
    from indexing.parsers.router import create_document_parser

    with _generation_lock:
        generation = _pipeline_generation
    pipeline = getattr(_pipeline_local, "pipeline", None)
    if pipeline is not None and getattr(_pipeline_local, "generation", -1) == generation:
        return pipeline

    # 本线程的缓存过期（配置已保存过）：释放旧管线的连接后重建。
    # 同一工作线程内任务是串行的，走到这里说明该线程上一个入库任务已经
    # 结束，旧管线不会再被使用，因此这里关闭是安全的。
    if pipeline is not None:
        with _pipelines_lock:
            _pipelines.discard(pipeline)
        close = getattr(getattr(pipeline, "milvus", None), "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001 - 关闭失败不影响重建
                _logger.warning("旧入库管线连接关闭失败", exc_info=True)

    s = get_settings()
    optional = _build_ingest_optional(s)
    pipeline = IngestPipeline(
        embedder=create_embedder(s.embedding),
        milvus=RagMilvusClient(s.milvus),
        parser=create_document_parser(s),
        cleaner=optional["cleaner"],
        contextualizer=optional["contextualizer"],
        contextual_enabled=optional["contextualizer"] is not None,
        graph_builder=optional["graph_builder"],
        graph_enabled=optional["graph_builder"] is not None,
        chunking_mode=s.pipeline.chunking_mode,
        simple_doc_max_chars=s.pipeline.simple_doc_max_chars,
    )
    _pipeline_local.pipeline = pipeline
    _pipeline_local.generation = generation
    with _pipelines_lock:
        _pipelines.add(pipeline)
    return pipeline



def _configured_ingest_ledger_mode() -> str:
    try:
        from config.settings import get_settings

        return str(get_settings().catalog.ingest_ledger_mode)
    except Exception:  # noqa: BLE001 - configuration failure keeps legacy ingestion
        return "off"



def _configured_chunk_authority_mode() -> str:
    try:
        from config.settings import get_settings

        return str(get_settings().catalog.chunk_authority_mode)
    except Exception:  # noqa: BLE001 - absent config preserves off behavior
        return "off"

def _build_document_ingest_job(
    pipeline: Any, doc_id: str, dataset_id: str
) -> Any:
    from indexing.state_machine import DocumentIngestJob

    mode = _configured_ingest_ledger_mode()
    if mode == "off":
        return DocumentIngestJob(pipeline, doc_id, dataset_id=dataset_id)
    from core.chunk_catalog import ChunkCatalog
    from core.index_operations import IndexOperationQueue
    from core.ingest_ledger import IngestLedger

    engine = catalog.get_engine()
    chunk_catalog = (
        None
        if _configured_chunk_authority_mode() == "off"
        else ChunkCatalog(engine)
    )
    return DocumentIngestJob(
        pipeline,
        doc_id,
        dataset_id=dataset_id,
        ledger=IngestLedger(engine),
        operation_queue=IndexOperationQueue(engine),
        chunk_catalog=chunk_catalog,
        ledger_mode=mode,
    )


def _run_index_operation_worker_loop() -> None:
    from core.chunk_catalog import ChunkCatalog
    from core.index_operations import IndexOperationQueue
    from indexing.index_worker import IndexOperationWorker
    from indexing.projection_handlers import ProjectionHandlers

    try:
        pipeline = _get_ingest_pipeline()
        engine = catalog.get_engine()
        projection_handlers = ProjectionHandlers(
            chunk_catalog=ChunkCatalog(engine),
            embedder=pipeline.embedder,
            milvus=pipeline.milvus,
            graph_builder=getattr(pipeline, "graph_builder", None),
        )
        worker = IndexOperationWorker(
            IndexOperationQueue(engine),
            handlers=projection_handlers.as_mapping(),
            worker_id=f"index-worker-{os.getpid()}",
        )
        while not _index_operation_worker_stop.is_set():
            result = worker.run_once(limit=1)
            if result.claimed == 0:
                _index_operation_worker_stop.wait(0.5)
    except Exception:  # noqa: BLE001 - worker failure must be visible, not crash API
        _logger.exception("持久化投影 worker 异常退出")


def start_index_operation_worker() -> threading.Thread | None:
    """Start the active projection worker; off/shadow modes stay inert."""
    global _index_operation_worker_thread
    if _configured_ingest_ledger_mode() != "active":
        return None
    with _index_operation_worker_lock:
        if (
            _index_operation_worker_thread is not None
            and _index_operation_worker_thread.is_alive()
        ):
            return _index_operation_worker_thread
        _index_operation_worker_stop.clear()
        _index_operation_worker_thread = threading.Thread(
            target=_run_index_operation_worker_loop,
            daemon=True,
            name="rag-index-operations",
        )
        _index_operation_worker_thread.start()
        return _index_operation_worker_thread


def shutdown_index_operation_worker(*, wait: bool = True) -> None:
    """Stop the projection worker; a later lifespan can create a fresh thread."""
    global _index_operation_worker_thread
    with _index_operation_worker_lock:
        thread = _index_operation_worker_thread
        _index_operation_worker_thread = None
        _index_operation_worker_stop.set()
    if wait and thread is not None:
        thread.join(timeout=5)

def _submit_job(doc_id: str, job: Any) -> None:
    """提交一个有界后台任务，并在结束时可靠释放容量。"""
    if not _job_capacity.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="入库任务队列已满，请稍后重试")
    with _jobs_lock:
        if _jobs.get(doc_id, {}).get("running"):
            _job_capacity.release()
            raise HTTPException(status_code=409, detail="该文档已有任务在运行")
        _jobs[doc_id] = {"running": True}
    try:
        # Keep submit and shutdown mutually exclusive so a task cannot be
        # handed to an executor while that executor is being torn down.
        with _executor_lock:
            future = start_ingest_executor().submit(job)
    except Exception:
        with _jobs_lock:
            _jobs[doc_id]["running"] = False
        _job_capacity.release()
        raise

    def _done(completed: Future[Any]) -> None:
        try:
            completed.result()
        except Exception:  # noqa: BLE001 - 状态机记录业务错误，此处捕获意外异常
            _logger.exception("文档 %s 后台任务异常", doc_id)
        finally:
            with _jobs_lock:
                _jobs[doc_id]["running"] = False
            _job_capacity.release()

    future.add_done_callback(_done)


def _submit_batch_job(doc_ids: list[str], job: Any) -> None:
    """用一个队列容量提交整批任务，同时锁住批次内的所有文档。"""
    if not doc_ids:
        return
    if not _job_capacity.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="入库任务队列已满，请稍后重试")

    with _jobs_lock:
        if any(_jobs.get(doc_id, {}).get("running") for doc_id in doc_ids):
            _job_capacity.release()
            raise HTTPException(status_code=409, detail="批次中有文档正在运行")
        for doc_id in doc_ids:
            _jobs[doc_id] = {"running": True}

    try:
        with _executor_lock:
            future = start_ingest_executor().submit(job)
    except Exception:
        with _jobs_lock:
            for doc_id in doc_ids:
                _jobs[doc_id]["running"] = False
        _job_capacity.release()
        raise

    def _done(completed: Future[Any]) -> None:
        try:
            completed.result()
        except Exception:  # noqa: BLE001 - 文档状态机已记录业务错误
            _logger.exception("文件夹批量入库后台任务异常")
        finally:
            with _jobs_lock:
                for doc_id in doc_ids:
                    _jobs[doc_id]["running"] = False
            _job_capacity.release()

    future.add_done_callback(_done)


class IngestRequest(BaseModel):
    file_path: str = Field(min_length=1, description="本地文件路径")
    dataset_id: str = Field(default="default")
    tenant_id: str = Field(default="default")
    name: str = Field(default="", description="文档名；缺省取文件名")
    doc_type: str = Field(default="", description="markdown/pdf/word/txt/excel；缺省按扩展名")


class FolderIngestRequest(BaseModel):
    folder_path: str = Field(min_length=1, description="服务器上的文件夹绝对路径")
    dataset_id: str = Field(default="default")
    tenant_id: str = Field(default="default")


class ReindexRequest(BaseModel):
    force: bool = Field(default=False, description="true 时跳过哈希检测强制重建")


class DurableDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_generation: int = Field(strict=True, ge=0)
    reason: str = Field(default="", max_length=512)


class DurableBatchDeleteItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1, max_length=64)
    expected_generation: int = Field(strict=True, ge=0)


class DurableBatchDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[DurableBatchDeleteItemRequest] = Field(min_length=1, max_length=100)
    reason: str = Field(default="", max_length=512)


IdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128),
]


class ChunkUpdateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)
    expected_revision: int = Field(ge=0)


class DocumentSettingsRequest(BaseModel):
    logical_folder_path: str = Field(default="", max_length=1024)
    tags: list[str] = Field(default_factory=list, max_length=20)


class BatchDocumentSettingsRequest(BaseModel):
    document_ids: list[str] = Field(min_length=1, max_length=100)
    logical_folder_path: str | None = Field(default=None, max_length=1024)
    add_tags: list[str] = Field(default_factory=list, max_length=20)
    remove_tags: list[str] = Field(default_factory=list, max_length=20)


def _document_job_running(doc_id: str) -> bool:
    with _jobs_lock:
        return bool(_jobs.get(doc_id, {}).get("running"))


def _reserve_document_operation(doc_id: str) -> bool:
    with _jobs_lock:
        if _jobs.get(doc_id, {}).get("running"):
            return False
        _jobs[doc_id] = {"running": True}
        return True


def _release_document_operation(doc_id: str) -> None:
    with _jobs_lock:
        _jobs.setdefault(doc_id, {})["running"] = False


def _delete_registered_document(doc_id: str) -> dict[str, Any]:
    from server.document_operations import delete_document_artifacts

    pipeline = _get_ingest_pipeline()
    return delete_document_artifacts(doc_id, pipeline=pipeline)


def _delete_one(doc_id: str) -> dict[str, Any]:
    doc = catalog.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")
    if not _reserve_document_operation(doc_id):
        raise HTTPException(status_code=409, detail="文档正在处理，完成后才能删除")
    try:
        return _delete_registered_document(doc_id)
    finally:
        _release_document_operation(doc_id)


@router.post("/documents/batch-settings")
def batch_document_settings(req: BatchDocumentSettingsRequest) -> dict[str, Any]:
    """Move documents between logical categories and merge/remove tags."""
    document_ids = list(dict.fromkeys(doc_id.strip() for doc_id in req.document_ids if doc_id.strip()))
    updated = 0
    not_found: list[str] = []
    for doc_id in document_ids:
        try:
            catalog.update_document_management(
                doc_id,
                logical_folder_path=req.logical_folder_path,
                add_tags=req.add_tags,
                remove_tags=req.remove_tags,
            )
            updated += 1
        except KeyError:
            not_found.append(doc_id)
    return {"requested": len(document_ids), "updated": updated, "not_found": not_found}


@router.patch("/documents/{doc_id}/settings")
def update_document_settings(doc_id: str, req: DocumentSettingsRequest) -> dict[str, Any]:
    """Replace one document's logical category and tag set."""
    try:
        return catalog.update_document_management(
            doc_id,
            logical_folder_path=req.logical_folder_path,
            tags=req.tags,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _document_deletion_repository(request: Request) -> DocumentDeletionRepository:
    engine = getattr(request.app.state, "knowledge_auth_engine", None) or catalog.get_engine()
    return DocumentDeletionRepository(engine)


def _delete_conflict_code(message: str) -> str:
    normalized = str(message or "").casefold()
    if "idempotency" in normalized:
        return "document_delete_idempotency_conflict"
    if "generation" in normalized or "changed while deletion" in normalized:
        return "document_generation_conflict"
    if "authority" in normalized:
        return "chunk_authority_incomplete"
    if "already" in normalized or "active delete" in normalized:
        return "document_delete_already_active"
    if "lifecycle" in normalized:
        return "document_lifecycle_conflict"
    if "source" in normalized:
        return "document_source_identity_incomplete"
    return "document_delete_conflict"


def _raise_document_delete_error(exc: Exception) -> None:
    if isinstance(exc, ContentNotFound):
        raise HTTPException(
            status_code=404,
            detail={"code": "knowledge_resource_not_found", "message": "资源不存在"},
        ) from exc
    if isinstance(exc, ContentConflict):
        message = str(exc) or "document delete request conflicts with current state"
        raise HTTPException(
            status_code=409,
            detail={"code": _delete_conflict_code(message), "message": message},
        ) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(
            status_code=422,
            detail={"code": "document_delete_invalid", "message": str(exc)},
        ) from exc
    raise exc


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _delete_operation_response(item: DeleteOperationProjection) -> dict[str, Any]:
    terminal = item.status in {"completed", "failed", "rejected"}
    return {
        "operation_id": item.id,
        "batch_operation_id": item.batch_id,
        "document_id": item.document_id or item.requested_document_id,
        "requested_document_id": item.requested_document_id,
        "status": item.status,
        "code": item.result_code or None,
        "message": item.result_message or None,
        "expected_generation": item.expected_generation,
        "delete_generation": item.delete_generation,
        "retrieval_enabled": False if item.status != "rejected" else None,
        "projection_pending": not terminal,
        "stores": {
            "required": item.required_store_count,
            "completed": item.completed_store_count,
            "failed": item.failed_store_count,
        },
        "reason": item.reason,
        "timestamps": {
            "started_at": _iso(item.started_at),
            "finalized_at": _iso(item.finalized_at),
            "created_at": _iso(item.created_at),
            "updated_at": _iso(item.updated_at),
        },
    }


def _delete_batch_response(batch: DeleteBatchProjection) -> dict[str, Any]:
    return {
        "batch_operation_id": batch.id,
        "status": batch.status,
        "requested": batch.requested_count,
        "accepted": batch.accepted_count,
        "completed": batch.completed_count,
        "failed": batch.failed_count,
        "rejected": batch.rejected_count,
        "reason": batch.reason,
        "items": [_delete_operation_response(item) for item in batch.items],
        "timestamps": {
            "created_at": _iso(batch.created_at),
            "updated_at": _iso(batch.updated_at),
            "finished_at": _iso(batch.finished_at),
        },
    }


@router.post(
    "/knowledge-bases/{dataset_id}/documents/{doc_id}/delete",
    status_code=status.HTTP_202_ACCEPTED,
)
def request_document_delete(
    dataset_id: str,
    doc_id: str,
    body: DurableDeleteRequest,
    request: Request,
    idempotency_key: IdempotencyKey,
    actor: Annotated[
        KnowledgeActor,
        Depends(
            require_knowledge_permission(
                KNOWLEDGE_DELETE,
                resolve_path_dataset("dataset_id"),
            )
        ),
    ],
) -> dict[str, Any]:
    """Accept a durable document-delete request without calling external stores."""
    try:
        item = _document_deletion_repository(request).request_delete(
            tenant_id=actor.tenant_id,
            dataset_id=dataset_id,
            document_id=doc_id,
            expected_generation=body.expected_generation,
            idempotency_key=idempotency_key,
            actor=actor.to_audit_context(),
            reason=body.reason,
            origin="operator",
        )
        return _delete_operation_response(item)
    except (ContentNotFound, ContentConflict, ValueError) as exc:
        _raise_document_delete_error(exc)
        raise AssertionError("unreachable") from exc


@router.post(
    "/knowledge-bases/{dataset_id}/documents/batch-delete",
    status_code=status.HTTP_202_ACCEPTED,
)
def request_document_batch_delete(
    dataset_id: str,
    body: DurableBatchDeleteRequest,
    request: Request,
    idempotency_key: IdempotencyKey,
    actor: Annotated[
        KnowledgeActor,
        Depends(
            require_knowledge_permission(
                KNOWLEDGE_DELETE,
                resolve_path_dataset("dataset_id"),
            )
        ),
    ],
) -> dict[str, Any]:
    """Accept up to 100 durable delete items and persist every item outcome."""
    try:
        batch = _document_deletion_repository(request).request_batch(
            tenant_id=actor.tenant_id,
            dataset_id=dataset_id,
            items=[
                DeleteBatchItemRequest(item.document_id, item.expected_generation)
                for item in body.items
            ],
            idempotency_key=idempotency_key,
            actor=actor.to_audit_context(),
            reason=body.reason,
            origin="operator",
        )
        return _delete_batch_response(batch)
    except (ContentNotFound, ContentConflict, ValueError) as exc:
        _raise_document_delete_error(exc)
        raise AssertionError("unreachable") from exc


@router.get(
    "/knowledge-bases/{dataset_id}/document-delete-operations/{operation_id}"
)
def get_document_delete_operation(
    dataset_id: str,
    operation_id: str,
    request: Request,
    actor: Annotated[
        KnowledgeActor,
        Depends(
            require_knowledge_permission(
                KNOWLEDGE_READ,
                resolve_path_dataset("dataset_id"),
            )
        ),
    ],
) -> dict[str, Any]:
    try:
        item = _document_deletion_repository(request).get_operation(
            actor.tenant_id, dataset_id, operation_id
        )
        return _delete_operation_response(item)
    except ContentNotFound as exc:
        _raise_document_delete_error(exc)
        raise AssertionError("unreachable") from exc


@router.get("/knowledge-bases/{dataset_id}/document-delete-batches/{batch_id}")
def get_document_delete_batch(
    dataset_id: str,
    batch_id: str,
    request: Request,
    actor: Annotated[
        KnowledgeActor,
        Depends(
            require_knowledge_permission(
                KNOWLEDGE_READ,
                resolve_path_dataset("dataset_id"),
            )
        ),
    ],
) -> dict[str, Any]:
    try:
        batch = _document_deletion_repository(request).get_batch(
            actor.tenant_id, dataset_id, batch_id
        )
        return _delete_batch_response(batch)
    except ContentNotFound as exc:
        _raise_document_delete_error(exc)
        raise AssertionError("unreachable") from exc


def _legacy_delete_gone() -> None:
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "document_delete_endpoint_gone",
            "message": "同步删除端点已停用，请使用知识库 Durable Delete API",
        },
    )


@router.post("/documents/batch-delete")
def batch_delete() -> dict[str, Any]:
    """Deprecated legacy endpoint; every request receives HTTP 410."""
    _legacy_delete_gone()
    raise AssertionError("unreachable")


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: str) -> dict[str, Any]:
    """Deprecated legacy endpoint; it never performs external deletion."""
    del doc_id
    _legacy_delete_gone()
    raise AssertionError("unreachable")


@router.get("/documents")
def list_documents(dataset_id: str = "default") -> dict[str, Any]:
    """文档列表（含状态 / 进度）。"""
    return {"documents": catalog.list_documents(dataset_id)}


@router.get("/documents/metrics")
def document_metrics(dataset_id: str = "default") -> dict[str, Any]:
    """聚合知识库文档状态、解析耗时、引擎分布与慢文档。"""
    from server.document_metrics import aggregate_document_metrics

    return aggregate_document_metrics(catalog.list_documents(dataset_id))


def _chunk_operator_projection(chunk: Any) -> dict[str, Any]:
    metadata = chunk.metadata if isinstance(getattr(chunk, "metadata", None), dict) else {}
    text = str(getattr(chunk, "text", "") or "")
    context = metadata.get("context")
    return {
        "chunk_id": str(getattr(chunk, "chunk_id", "") or ""),
        "doc_id": str(getattr(chunk, "doc_id", "") or ""),
        "text": text,
        "text_hash": str(getattr(chunk, "text_hash", "") or ""),
        "content_revision": int(getattr(chunk, "content_revision", 0) or 0),
        "parent_chunk_id": getattr(chunk, "parent_chunk_id", None),
        "source": getattr(chunk, "source", None),
        "seq": int(metadata.get("seq", 0) or 0),
        "page": metadata.get("page") or metadata.get("page_number"),
        "heading": metadata.get("heading") or metadata.get("title") or metadata.get("section"),
        "context": context if isinstance(context, str) else "",
        "char_count": len(text),
        "metadata": metadata,
        "created_at": (
            chunk.created_at.isoformat() if getattr(chunk, "created_at", None) else None
        ),
        "updated_at": (
            chunk.updated_at.isoformat() if getattr(chunk, "updated_at", None) else None
        ),
    }


def _legacy_chunk_route_gone() -> None:
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "knowledge_chunk_legacy_route_gone",
            "message": "旧切片接口已关闭；请使用受认证的知识库范围接口",
        },
    )


@router.get("/documents/{doc_id}/chunks")
def legacy_get_document_chunks(doc_id: str) -> None:
    del doc_id
    _legacy_chunk_route_gone()


@router.patch("/documents/{doc_id}/chunks/{chunk_id}")
def legacy_patch_document_chunk(doc_id: str, chunk_id: str, req: ChunkUpdateRequest) -> None:
    del doc_id, chunk_id, req
    _legacy_chunk_route_gone()


@router.delete("/documents/{doc_id}/chunks/{chunk_id}")
def legacy_delete_document_chunk(doc_id: str, chunk_id: str, expected_revision: int = 0) -> None:
    del doc_id, chunk_id, expected_revision
    _legacy_chunk_route_gone()


def _chunk_head_operator_projection(head: Any) -> dict[str, Any]:
    metadata = dict(head.chunk_metadata or {})
    text = str(head.content or "")
    context = str(head.context_header or "")
    return {
        "chunk_id": str(head.id),
        "doc_id": str(head.document_id),
        "tenant_id": str(head.tenant_id),
        "dataset_id": str(head.dataset_id),
        "document_revision": int(head.document_revision or 0),
        "enabled": bool(head.enabled),
        "chunk_role": str(head.chunk_role or "flat"),
        "text": text,
        "text_hash": str(head.content_hash or ""),
        "content_revision": int(head.content_revision or 0),
        "indexed_revision": int(head.indexed_revision or 0),
        "desired_index_revision": int(head.desired_index_revision or 0),
        "index_status": str(head.index_status or ""),
        "projection_pending": bool(
            int(head.indexed_revision or 0) < int(head.desired_index_revision or 0)
            or str(head.index_status or "") != "ready"
        ),
        "parent_chunk_id": head.parent_chunk_id,
        "source": metadata.get("source"),
        "seq": int(metadata.get("seq", head.chunk_index) or 0),
        "page": metadata.get("page") or metadata.get("page_number"),
        "heading": metadata.get("heading") or metadata.get("title") or metadata.get("section"),
        "context": context,
        "char_count": len(text),
        "metadata": metadata,
        "created_at": head.created_at.isoformat() if head.created_at else None,
        "updated_at": head.updated_at.isoformat() if head.updated_at else None,
    }


def get_document_chunks(
    doc_id: str,
    offset: int = 0,
    limit: int = 50,
    query: str = "",
    include_disabled: bool = False,
) -> dict[str, Any]:
    """Return operator-safe chunk projections for document inspection."""
    doc = catalog.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), 200))
    authority_mode = _configured_chunk_authority_mode()
    if authority_mode == "active":
        from core.chunk_catalog import ChunkCatalog

        heads = ChunkCatalog(catalog.get_engine()).list_document_heads(
            doc_id,
            document_revision=int(doc.get("content_revision") or 0),
            include_disabled=include_disabled,
        )
        items = [_chunk_head_operator_projection(head) for head in heads]
    else:
        pipeline = _get_ingest_pipeline()
        chunks = pipeline.milvus.query_chunks_by_doc(
            doc_id, str(doc.get("tenant_id") or "")
        )
        items = [_chunk_operator_projection(chunk) for chunk in chunks]
    items.sort(key=lambda item: (int(item.get("seq") or 0), str(item.get("chunk_id") or "")))
    keyword = query.strip().casefold()
    if keyword:
        items = [
            item for item in items
            if keyword in str(item.get("chunk_id") or "").casefold()
            or keyword in str(item.get("text") or "").casefold()
            or keyword in str(item.get("context") or "").casefold()
            or keyword in str(item.get("heading") or "").casefold()
        ]
    total = len(items)
    return {
        "authority_mode": authority_mode,
        "items": items[offset:offset + limit],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


def _reserve_chunk_mutation(doc_id: str) -> None:
    if catalog.get_document(doc_id) is None:
        raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")
    if not _reserve_document_operation(doc_id):
        raise HTTPException(status_code=409, detail="文档正在处理，完成后才能修改切片")


def update_document_chunk(doc_id: str, chunk_id: str, req: ChunkUpdateRequest) -> dict[str, Any]:
    """Edit one chunk, re-embed it, and refresh its graph facts."""
    from server.chunk_operations import (
        ChunkAuthorityIncomplete,
        update_document_chunk_artifacts,
    )

    _reserve_chunk_mutation(doc_id)
    try:
        result = update_document_chunk_artifacts(
            doc_id,
            chunk_id,
            req.text,
            expected_revision=req.expected_revision,
            authority_mode=_configured_chunk_authority_mode(),
            pipeline=_get_ingest_pipeline(),
        )
        payload = _chunk_operator_projection(result.chunk)
        payload["graph_update"] = {
            "removed_relations": result.removed_relations,
            "removed_entities": result.removed_entities,
            "entities": result.graph_entities,
            "relations": result.graph_relations,
        }
        payload["authority_mode"] = result.authority_mode
        payload["projection_pending"] = result.projection_pending
        payload["operation_ids"] = list(result.operation_ids)
        return payload
    except (ChunkRevisionConflict, ChunkAuthorityIncomplete) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        _release_document_operation(doc_id)


def delete_document_chunk(
    doc_id: str,
    chunk_id: str,
    expected_revision: int,
) -> dict[str, Any]:
    """Delete one chunk and update graph, usage, and cache state."""
    from server.chunk_operations import (
        ChunkAuthorityIncomplete,
        delete_document_chunk_artifacts,
    )

    _reserve_chunk_mutation(doc_id)
    try:
        return delete_document_chunk_artifacts(
            doc_id,
            chunk_id,
            expected_revision=expected_revision,
            authority_mode=_configured_chunk_authority_mode(),
            pipeline=_get_ingest_pipeline(),
        )
    except (ChunkRevisionConflict, ChunkAuthorityIncomplete) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        _release_document_operation(doc_id)


@router.get("/documents/{doc_id}")
def get_document(doc_id: str) -> dict[str, Any]:
    """文档详情（含分段进度汇总）。"""
    doc = catalog.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")
    return doc


@router.post("/documents/ingest")
def ingest(req: IngestRequest) -> dict[str, Any]:
    """登记文档并后台执行入库（状态机：waiting -> ... -> completed/error）。"""

    file_path = Path(req.file_path)
    if not file_path.is_file():
        raise HTTPException(status_code=400, detail=f"文件不存在: {req.file_path}")

    # 幂等：同 dataset + 同路径的已完成文档直接返回（增量变更走 reindex）
    existing = catalog.find_document_by_path(req.dataset_id, str(file_path))
    if existing is not None and existing["status"] == "completed":
        return {
            "document_id": existing["id"],
            "status": existing["status"],
            "note": "该文件已在知识库中（completed）。内容有变化请调用 /reindex 增量重建。",
        }

    doc_id = existing["id"] if existing is not None else None
    if doc_id is None:
        try:
            doc = catalog.create_document(
                tenant_id=req.tenant_id,
                dataset_id=req.dataset_id,
                name=req.name or file_path.name,
                file_path=str(file_path),
                doc_type=req.doc_type,
            )
        except catalog.CatalogQuotaError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        doc_id = doc["id"]

    def _run() -> None:
        pipeline = _get_ingest_pipeline()
        pipeline.ensure_collection()
        _ensure_graph_collections(pipeline)
        job = _build_document_ingest_job(pipeline, doc_id, req.dataset_id)
        result = job.run(str(file_path), doc_type=req.doc_type or None)
        _logger.info("文档 %s 入库任务结束: %s", doc_id, result.get("status"))

    _submit_job(doc_id, _run)
    return {
        "document_id": doc_id,
        "status": "waiting",
        "note": "入库任务已启动（后台执行）。轮询 GET /api/documents/{id} 获取实时进度。",
    }


@router.post("/documents/ingest-folder")
def ingest_folder(req: FolderIngestRequest) -> dict[str, Any]:
    """递归登记文件夹中的支持格式，并用一个后台任务逐个入库。"""

    folder_path = Path(req.folder_path)
    if not folder_path.is_dir() or folder_path.is_symlink():
        raise HTTPException(status_code=400, detail=f"文件夹不存在: {req.folder_path}")
    folder_path = folder_path.resolve()
    scan = discover_folder_documents(folder_path)

    entries: list[tuple[str, Path]] = []
    created_document_ids: list[str] = []
    duplicate_count = 0
    rejected_count = 0
    rejected_files: list[str] = []

    for file_path in scan.files:
        existing = catalog.find_document_by_path(req.dataset_id, str(file_path))
        if existing is not None and existing["status"] != "error":
            duplicate_count += 1
            continue

        if existing is not None:
            entries.append((existing["id"], file_path))
            continue

        try:
            doc = catalog.create_document(
                tenant_id=req.tenant_id,
                dataset_id=req.dataset_id,
                name=file_path.name,
                file_path=str(file_path),
            )
        except catalog.CatalogQuotaError:
            rejected_count += 1
            if len(rejected_files) < 20:
                rejected_files.append(file_path.name)
            continue
        entries.append((doc["id"], file_path))
        created_document_ids.append(doc["id"])

    document_ids = [doc_id for doc_id, _ in entries]

    if entries:
        def _run() -> None:
            pipeline = _get_ingest_pipeline()
            pipeline.ensure_collection()
            _ensure_graph_collections(pipeline)
            for doc_id, file_path in entries:
                job = _build_document_ingest_job(pipeline, doc_id, req.dataset_id)
                result = job.run(str(file_path))
                _logger.info(
                    "文件夹批量入库：文档 %s 处理结束: %s",
                    doc_id,
                    result.get("status"),
                )

        try:
            _submit_batch_job(document_ids, _run)
        except Exception:
            # 登记成功但任务未提交时不能留下无法自动恢复的 waiting 记录。
            # 只回滚本次新建行；既有 error 文档仍保留原来的失败信息。
            for doc_id in created_document_ids:
                try:
                    catalog.remove_document(doc_id)
                except Exception:  # noqa: BLE001 - 保留原始提交异常
                    _logger.exception("回滚未提交的批量入库文档失败: %s", doc_id)
            raise

    skipped_count = scan.unsupported_count + duplicate_count + rejected_count
    return {
        "folder_path": str(folder_path),
        "discovered_count": scan.discovered_count,
        "queued_count": len(entries),
        "skipped_count": skipped_count,
        "unsupported_count": scan.unsupported_count,
        "duplicate_count": duplicate_count,
        "rejected_count": rejected_count,
        "rejected_files": rejected_files,
        "document_ids": document_ids,
        "note": (
            f"已登记 {len(entries)} 个文件，跳过 {skipped_count} 个；"
            "入库进度会在文档列表中自动刷新。"
        ),
    }


@router.post("/documents/{doc_id}/reindex")
def reindex(doc_id: str, req: ReindexRequest) -> dict[str, Any]:
    """增量重索引（文件哈希变化检测 + chunk 级差量重建，后台执行）。"""
    from indexing.reindex import reindex_document

    doc = catalog.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")
    if not doc.get("file_path"):
        raise HTTPException(status_code=400, detail="文档无 file_path，无法重索引（请用 ingest 入库）")

    def _run() -> None:
        pipeline = _get_ingest_pipeline()
        _ensure_graph_collections(pipeline)
        result = reindex_document(
            pipeline, doc_id, doc["file_path"],
            doc_type=doc.get("doc_type") or None, force=req.force,
        )
        _logger.info("文档 %s 重索引结束: %s", doc_id, result.get("status"))

    _submit_job(doc_id, _run)
    return {
        "document_id": doc_id,
        "note": "增量重建已启动。轮询 GET /api/documents/{id} 获取实时进度。",
    }


__all__ = [
    "FOLDER_IMPORT_EXTENSIONS",
    "INGEST_MAX_CONCURRENT",
    "INGEST_QUEUE_MAX",
    "FolderIngestRequest",
    "FolderScanResult",
    "discover_folder_documents",
    "reset_ingest_pipelines",
    "router",
    "shutdown_ingest_executor",
    "start_ingest_executor",
]
