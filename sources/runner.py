"""文档源 -> 入库的执行器：清单解析、增量判定、批量入库。

**增量是这一层的核心价值。** 官方文档仓库动辄上千篇，每次全量重跑要把
所有内容重新嵌入一遍——既慢又白花嵌入调用。这里按「内容哈希」做文件级
跳过：只有内容真变了的文档才会重新解析 + 嵌入 + 写库。

为什么不直接复用 :func:`indexing.reindex.reindex_document`：它的哈希来源
写死为「本地文件的 sha256」，且依赖 catalog 里已有文档记录。源层的哈希由
:class:`sources.base.FetchedDocument` 提供（远程源可以用 ETag / commit sha
之类更廉价的判据），状态也自成一份，与 catalog 解耦——源同步是运维动作，
不应该要求先在业务目录里建好文档记录。两者可以并存，各管各的场景。

状态文件（每个源一份 JSON，落在缓存目录里）::

    {"docs": {"<doc_id>": {"hash": "...", "uri": "...", "chunks": 12}}}

删除检测同样靠它：本次没再出现的 doc_id，说明上游删了这篇文档，
对应的 chunk 也要从库里清掉，否则知识库会残留早已不存在的内容。
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from core import cache_epoch, catalog
from sources.state_modes import SourceStateModeSpec, source_state_mode
from core.document_deletion import DocumentDeletionRepository
from core.knowledge_governance import AuditContext
from core.source_sync_ledger import SourceSyncLedgerConflict
from indexing.state_machine import (
    DocumentWritePermit,
    DocumentWriteSuperseded,
    assert_document_write_permit,
    read_document_write_permit,
)
from core.observability import get_logger
from sources.base import (
    FetchedDocument,
    SourceError,
    prepare_source_cache_directory,
    prepare_verified_staging,
)
from sources.registry import create_source

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

_logger = get_logger("sources.runner")


@dataclass
class SourceSpec:
    """manifest 里的一条源声明。"""

    name: str
    type: str
    dataset_id: str
    params: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    tenant_id: str = ""
    enabled: bool = True

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SourceSpec":
        missing = [k for k in ("name", "type") if not raw.get(k)]
        if missing:
            raise SourceError(f"源声明缺少字段 {missing}: {raw}")
        return cls(
            name=str(raw["name"]),
            type=str(raw["type"]),
            # dataset_id 默认取 name：一个源天然对应一个知识库，
            # 让「每个项目的文档各自成库」成为不需要额外配置的默认行为。
            dataset_id=str(raw.get("dataset_id") or raw["name"]),
            params=dict(raw.get("params") or {}),
            metadata=dict(raw.get("metadata") or {}),
            tenant_id=str(raw.get("tenant_id") or ""),
            enabled=bool(raw.get("enabled", True)),
        )


@dataclass
class SyncReport:
    """一次源同步的结果。"""

    source: str
    dataset_id: str
    fetched: int = 0
    ingested: int = 0
    skipped: int = 0
    removed: int = 0
    pending_deletes: int = 0
    chunks: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)
    #: 抓取整体中断的原因（网络重置等）。非空表示**本轮没走完**：
    #: 已完成的部分有效且已落盘，剩下的要靠重跑补齐；这一轮不做删除清理。
    fetch_error: str = ""
    elapsed_s: float = 0.0

    def summary(self) -> str:
        base = (
            f"{self.source}[{self.dataset_id}]: 抓取 {self.fetched}，"
            f"入库 {self.ingested}（{self.chunks} chunk），"
            f"跳过 {self.skipped}，删除请求 {self.pending_deletes}，"
            f"已清理 {self.removed}，"
            f"耗时 {self.elapsed_s:.1f}s"
        )
        if self.failed:
            base += f"，失败 {len(self.failed)}"
        if self.fetch_error:
            base += "，**未走完（重跑可续）**"
        return base


def load_manifest(path: Path) -> list[SourceSpec]:
    """读取源清单。

    清单用独立 JSON 文件而不是 ``.env``：源的参数是结构化的（include 是
    数组、params 是嵌套对象），而 ``.env`` 只能表达扁平字符串；且
    :class:`config.settings.Rag4cEnvSource` 明确跳过 dict 字段，本来也映射不进来。
    独立文件还能进版本库，改了什么一目了然。
    """
    if not path.is_file():
        raise SourceError(
            f"源清单不存在: {path}。"
            "可以复制 config/sources.example.json 作为起点。"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SourceError(f"源清单不是合法 JSON（{path}）: {exc}") from exc
    items = raw.get("sources") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise SourceError(f"源清单格式错误（{path}）：期望 {{'sources': [...]}}")
    return [SourceSpec.from_dict(x) for x in items]


def make_doc_id(source_name: str, rel_path: str) -> str:
    """生成稳定 doc_id。

    必须**只由 (源名, 相对路径) 决定**：稳定才谈得上增量与删除检测——
    doc_id 每次变一下，旧 chunk 就永远删不掉，库里会不断堆积同一篇文档的
    历史副本。

    带一段可读前缀而不是纯哈希，是为了在 Milvus 里直接 query 时能一眼看出
    这条 chunk 来自哪个源；哈希后缀负责消歧与长度上界。
    """
    digest = hashlib.sha256(f"{source_name}/{rel_path}".encode("utf-8")).hexdigest()[:16]
    stem = Path(rel_path).stem[:40].replace(" ", "-")
    return f"{source_name}-{stem}-{digest}"


class SourceSyncer:
    """把一个源的文档同步进知识库（抓取 -> 增量判定 -> 入库 -> 清理）。

    Args:
        pipeline: :class:`indexing.ingest.IngestPipeline`（鸭子类型，
            只用到 ``ensure_collection`` / ``add_file`` / ``delete_document``）。
            ``delete_document`` 需接受 ``unregister`` 关键字参数——替换式重入库
            要传 ``False``。自己实现替身时别漏，漏了会在「单篇入库」里抛
            TypeError，被上层归一成「这篇失败」，于是整源看起来是内容问题，
            实际是签名对不上。
        cache_dir: 抓取缓存与状态文件的根目录。
        settings: Settings 实例，透传给源工厂。
    """

    def __init__(
        self,
        pipeline: Any,
        cache_dir: Path,
        settings: Any = None,
        *,
        ledger: Any = None,
        state_mode: str = "json",
        execution_guard: Any = None,
    ) -> None:
        # 词表与"这一种模式要什么"都由 sources/state_modes.py 一处声明；这里不再手写
        # 第二份 {json,dual,database}。
        declared = source_state_mode(state_mode)
        if declared.requires_ledger and ledger is None:
            raise ValueError("database state_mode requires a SourceSyncLedger")
        self.pipeline = pipeline
        self.cache_dir = Path(cache_dir)
        self.settings = settings
        self.ledger = ledger
        self.state_mode = state_mode
        self.execution_guard = execution_guard
        self._active_source_id: str | None = None
        self._active_cache_key: str | None = None

    # -- 状态 ------------------------------------------------------------- #

    @staticmethod
    def _derived_cache_key(spec: SourceSpec) -> str:
        identity = json.dumps(
            {
                "tenant_id": spec.tenant_id,
                "dataset_id": spec.dataset_id,
                "type": spec.type,
                "name": spec.name,
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return "source-local-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]

    def _cache_key(self, spec: SourceSpec) -> str:
        return self._active_cache_key or self._derived_cache_key(spec)

    def _source_cache_dir(self, spec: SourceSpec) -> Path:
        return prepare_source_cache_directory(self.cache_dir, self._cache_key(spec))

    def _state_path(self, spec: SourceSpec) -> Path:
        return self._source_cache_dir(spec) / "_state.json"

    def _load_json_state(self, spec: SourceSpec) -> dict[str, dict[str, Any]]:
        p = self._state_path(spec)
        if not p.is_file():
            return {}
        try:
            return dict(json.loads(p.read_text(encoding="utf-8")).get("docs") or {})
        except (json.JSONDecodeError, OSError):
            # 状态文件损坏不该让同步失败：最坏结果是这一轮退化成全量，
            # 而全量是安全的（每篇入库前都会先删同 doc_id 的旧 chunk）。
            return {}

    def _save_json_state(self, spec: SourceSpec, docs: dict[str, dict[str, Any]]) -> None:
        p = self._state_path(spec)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"docs": docs, "updated_at": int(time.time())}, ensure_ascii=False),
            encoding="utf-8",
        )


    @property
    def state_mode_spec(self) -> SourceStateModeSpec:
        """当前模式的声明行。派生而不是另存一份，避免两个字段各说一句话。

        查不到就抛：让一个没登记的模式按 {json,dual,database} 之外的默认值走，
        表现是状态静默写到错的存储上，而不是装配期就报出来。
        """
        return source_state_mode(self.state_mode)

    def _ledger_call(self, action: str, callback: Callable[[], Any]) -> Any:
        try:
            return callback()
        except Exception as exc:  # noqa: BLE001 - dual mode preserves JSON path
            if self.state_mode_spec.ledger_failures_are_fatal:
                raise
            _logger.warning("源同步 ledger dual 写入失败（%s）: %s", action, str(exc)[:200])
            return None

    def _load_state(self, spec: SourceSpec) -> dict[str, dict[str, Any]]:
        if self.state_mode_spec.uses_ledger and self.ledger is not None:
            source_id = self._active_source_id
            if source_id:
                database_state = self._ledger_call(
                    "load_state", lambda: self.ledger.load_state(source_id)
                )
                if database_state or self.state_mode_spec.ledger_authoritative:
                    return dict(database_state or {})
        return self._load_json_state(spec)

    def _save_state(self, spec: SourceSpec, docs: dict[str, dict[str, Any]]) -> None:
        if self.state_mode_spec.writes_json:
            self._save_json_state(spec, docs)

    def _assert_run_current(self, sync_run: Any) -> None:
        if self.execution_guard is not None:
            self.execution_guard.check()
        if sync_run is not None and self.ledger is not None:
            self.ledger.assert_run_current(str(sync_run.id))

    def _queue_stale_document_cleanup(
        self, spec: SourceSpec, doc_id: str, sync_run: Any
    ) -> None:
        if self.ledger is None or not spec.tenant_id or sync_run is None:
            return
        from sqlalchemy.orm import Session

        from models.orm import Document

        with Session(self.ledger.engine) as session:
            document = session.get(Document, doc_id)
            if document is None:
                return
            generation = int(document.mutation_generation or 0)
        identity = (
            f"stale-source-writer:{sync_run.id}:{doc_id}:{generation}:"
            f"{sync_run.source_generation}:{sync_run.dataset_generation}"
        )
        try:
            DocumentDeletionRepository(self.ledger.engine).request_delete(
                tenant_id=spec.tenant_id,
                dataset_id=spec.dataset_id,
                document_id=doc_id,
                expected_generation=generation,
                idempotency_key=(
                    "source-stale:"
                    + hashlib.sha256(identity.encode("utf-8")).hexdigest()
                ),
                actor=AuditContext.system(
                    "system:source-sync", f"source-sync-cleanup:{sync_run.id}"
                ),
                reason="source writer generation changed after document registration",
                origin="source_sync",
            )
        except Exception as exc:  # noqa: BLE001 - an existing delete fence is acceptable
            with Session(self.ledger.engine) as session:
                refreshed = session.get(Document, doc_id)
                still_active = bool(
                    refreshed is not None and refreshed.lifecycle_state == "active"
                )
            if refreshed is None or still_active:
                _logger.warning(
                    "failed to queue stale source writer cleanup for %s: %s",
                    doc_id,
                    str(exc)[:200],
                )

    @staticmethod
    def _suppressed_state(value: dict[str, Any] | None) -> bool:
        return str((value or {}).get("state") or "active") in {
            "operator_suppressed",
            "delete_pending",
        }

    # -- 同步 ------------------------------------------------------------- #

    def sync(
        self,
        spec: SourceSpec,
        force: bool = False,
        dry_run: bool = False,
        limit: int = 0,
        on_progress: Callable[[str], None] | None = None,
    ) -> SyncReport:
        """同步一个源，并按配置把运行事实写入 JSON、数据库或双写。"""
        t0 = time.perf_counter()
        report = SyncReport(source=spec.name, dataset_id=spec.dataset_id)
        log = on_progress or (lambda _msg: None)

        source = create_source(spec.type, spec.params, self.settings)
        source_record = None
        sync_run = None
        if self.state_mode_spec.uses_ledger and self.ledger is not None:
            source_record = self._ledger_call(
                "ensure_source", lambda: self.ledger.ensure_source(spec)
            )
            self._active_source_id = None if source_record is None else source_record.id
            self._active_cache_key = self._active_source_id

        source_cache = self._source_cache_dir(spec)
        workdir = source_cache / "files"
        workdir.mkdir(exist_ok=True, mode=0o700)
        workdir.chmod(0o700)
        ttl = float(
            getattr(getattr(self.settings, "sources", None), "verified_staging_ttl_seconds", 86400.0)
        )
        prepare_verified_staging(workdir, ttl_seconds=ttl)

        old_state = self._load_state(spec)
        new_state: dict[str, dict[str, Any]] = {}
        if source_record is not None:
            sync_run = self._ledger_call(
                "start_run",
                lambda: self.ledger.start_run(
                    source_record.id,
                    trigger="manual",
                    force_full=force,
                    dry_run=dry_run,
                    cursor_before=source_record.last_cursor or {"documents": len(old_state)},
                ),
            )

        if not dry_run:
            self._assert_run_current(sync_run)
            self.pipeline.ensure_collection()

        try:
            for doc in source.fetch(workdir):
                self._assert_run_current(sync_run)
                report.fetched += 1
                doc_id = make_doc_id(spec.name, doc.rel_path)
                prev = old_state.get(doc_id)

                if self._suppressed_state(prev):
                    report.skipped += 1
                    new_state[doc_id] = dict(prev or {})
                    if sync_run is not None:
                        self._ledger_call(
                            "record_suppressed",
                            lambda doc=doc, doc_id=doc_id, prev=prev: self.ledger.record_item(
                                sync_run.id,
                                external_id=doc.rel_path,
                                doc_id=doc_id,
                                source_uri=doc.uri,
                                content_hash=doc.content_hash,
                                action="skip",
                                result="suppressed",
                                chunk_count=int((prev or {}).get("chunks") or 0),
                            ),
                        )
                    continue

                if not force and prev and prev.get("hash") == doc.content_hash:
                    report.skipped += 1
                    new_state[doc_id] = prev
                    if sync_run is not None:
                        self._ledger_call(
                            "record_skip",
                            lambda doc=doc, doc_id=doc_id, prev=prev: self.ledger.record_item(
                                sync_run.id,
                                external_id=doc.rel_path,
                                doc_id=doc_id,
                                source_uri=doc.uri,
                                content_hash=doc.content_hash,
                                action="skip",
                                result="skipped",
                                chunk_count=int(prev.get("chunks") or 0),
                            ),
                        )
                    continue

                if dry_run:
                    report.ingested += 1
                    new_state[doc_id] = {
                        "hash": doc.content_hash,
                        "uri": doc.uri,
                        "chunks": 0,
                    }
                    if sync_run is not None:
                        self._ledger_call(
                            "record_dry_run",
                            lambda doc=doc, doc_id=doc_id: self.ledger.record_item(
                                sync_run.id,
                                external_id=doc.rel_path,
                                doc_id=doc_id,
                                source_uri=doc.uri,
                                content_hash=doc.content_hash,
                                action="upsert",
                                result="dry_run",
                            ),
                        )
                    log(f"  [DRY] 将入库 {doc.rel_path}")
                else:
                    try:
                        writer_permit = None
                        if spec.tenant_id and catalog.get_document(doc_id) is not None:
                            writer_permit = read_document_write_permit(
                                doc_id, dataset_id=spec.dataset_id
                            )
                        chunks = self._ingest_one(
                            spec,
                            doc,
                            doc_id,
                            replace=bool(prev),
                            sync_run=sync_run,
                            writer_permit=writer_permit,
                        )
                        document_generation = (
                            read_document_write_permit(
                                doc_id, dataset_id=spec.dataset_id
                            ).document_generation
                            if spec.tenant_id
                            else 0
                        )
                    except SourceSyncLedgerConflict as exc:
                        if "generation changed" in str(exc):
                            raise
                        report.failed.append((doc.rel_path, str(exc)[:200]))
                        log(f"  [FAIL] {doc.rel_path}: {str(exc)[:120]}")
                        continue
                    except DocumentWriteSuperseded as exc:
                        report.failed.append((doc.rel_path, str(exc)[:200]))
                        log(f"  [SUPERSEDED] {doc.rel_path}: {str(exc)[:120]}")
                        continue
                    except Exception as exc:  # noqa: BLE001 - per-item failure isolation
                        report.failed.append((doc.rel_path, str(exc)[:200]))
                        if sync_run is not None:
                            self._ledger_call(
                                "record_failure",
                                lambda doc=doc, doc_id=doc_id, exc=exc: self.ledger.record_item(
                                    sync_run.id,
                                    external_id=doc.rel_path,
                                    doc_id=doc_id,
                                    source_uri=doc.uri,
                                    content_hash=doc.content_hash,
                                    action="upsert",
                                    result="failed",
                                    error_code=type(exc).__name__,
                                    error_message=str(exc),
                                ),
                            )
                        log(f"  [FAIL] {doc.rel_path}: {str(exc)[:120]}")
                        continue

                    report.ingested += 1
                    report.chunks += chunks
                    new_state[doc_id] = {
                        "hash": doc.content_hash,
                        "uri": doc.uri,
                        "chunks": chunks,
                    }
                    if sync_run is not None and source_record is not None:
                        self._ledger_call(
                            "record_completed",
                            lambda doc=doc, doc_id=doc_id, chunks=chunks: self.ledger.record_item(
                                sync_run.id,
                                external_id=doc.rel_path,
                                doc_id=doc_id,
                                source_uri=doc.uri,
                                content_hash=doc.content_hash,
                                action="upsert",
                                result="completed",
                                chunk_count=chunks,
                            ),
                        )
                        self._ledger_call(
                            "upsert_state",
                            lambda doc=doc, doc_id=doc_id, chunks=chunks: self.ledger.upsert_state(
                                source_record.id,
                                doc_id=doc_id,
                                external_id=doc.rel_path,
                                source_uri=doc.uri,
                                content_hash=doc.content_hash,
                                chunk_count=chunks,
                                run_id=sync_run.id,
                                document_generation=document_generation,
                            ),
                        )
                    if report.ingested % 20 == 0:
                        log(f"  已入库 {report.ingested} 篇（{report.chunks} chunk）…")

                if limit and report.ingested >= limit:
                    log(f"  已达 --limit {limit}，停止本轮")
                    break
        except SourceSyncLedgerConflict as exc:
            report.fetch_error = (
                "source sync generation changed"
                if "generation changed" in str(exc)
                else str(exc)[:300]
            )
        except Exception as exc:  # noqa: BLE001 - fetch interruption is durable
            report.fetch_error = str(exc)[:300]
            log(f"  [中断] 抓取未能走完：{report.fetch_error}")
            log("         已完成的部分已记入状态，重跑会从断点继续")

        incomplete = bool(limit and report.ingested >= limit) or bool(report.fetch_error)
        if not dry_run and not incomplete:
            for doc_id in set(old_state) - set(new_state):
                previous = dict(old_state[doc_id])
                if self._suppressed_state(previous):
                    new_state[doc_id] = previous
                    continue
                if (
                    self.ledger is None
                    or source_record is None
                    or sync_run is None
                    or not spec.tenant_id
                ):
                    report.failed.append(
                        (doc_id, "durable source delete authority is unavailable")
                    )
                    new_state[doc_id] = previous
                    continue
                try:
                    self._assert_run_current(sync_run)
                    generation = int(previous.get("document_generation") or 0)
                    identity = f"{source_record.id}:{sync_run.source_generation}:{doc_id}:{generation}"
                    idempotency_key = (
                        "source-delete:"
                        + hashlib.sha256(identity.encode("utf-8")).hexdigest()
                    )
                    operation = DocumentDeletionRepository(self.ledger.engine).request_delete(
                        tenant_id=spec.tenant_id,
                        dataset_id=spec.dataset_id,
                        document_id=doc_id,
                        expected_generation=generation,
                        idempotency_key=idempotency_key,
                        actor=AuditContext.system(
                            "system:source-sync", f"source-sync:{sync_run.id}"
                        ),
                        reason=f"upstream source {spec.name} no longer contains document",
                        origin="source_sync",
                    )
                    report.pending_deletes += 1
                    new_state[doc_id] = {
                        **previous,
                        "state": "delete_pending",
                        "document_generation": int(operation.delete_generation or generation),
                        "delete_operation_id": operation.id,
                    }
                    self._ledger_call(
                        "record_delete",
                        lambda doc_id=doc_id, previous=previous, operation=operation: self.ledger.record_item(
                            sync_run.id,
                            external_id=str(previous.get("external_id") or doc_id),
                            doc_id=doc_id,
                            source_uri=str(previous.get("uri") or ""),
                            content_hash=str(previous.get("hash") or ""),
                            action="delete",
                            result="queued",
                        ),
                    )
                except Exception as exc:  # noqa: BLE001 - cleanup failure isolation
                    report.failed.append((doc_id, str(exc)[:200]))
                    new_state[doc_id] = previous
                    log(f"  [WARN] durable delete {doc_id} failed: {exc}")
        elif incomplete:
            for doc_id, prev in old_state.items():
                new_state.setdefault(doc_id, prev)

        if not dry_run and report.fetch_error != "source sync generation changed":
            self._assert_run_current(sync_run)
            self._save_state(spec, new_state)

        if not dry_run and (
            report.ingested or report.removed or report.pending_deletes
        ):
            cache_epoch.bump(
                spec.tenant_id or None,
                reason=(
                    f"源同步 {spec.name}（入库 {report.ingested} "
                    f"删除请求 {report.pending_deletes}）"
                ),
            )

        report.elapsed_s = time.perf_counter() - t0
        if sync_run is not None:
            status = "dry_run" if dry_run else "incomplete" if incomplete else "completed"
            self._ledger_call(
                "finish_run",
                lambda: self.ledger.finish_run(
                    sync_run.id,
                    status=status,
                    cursor_after={"documents": len(new_state)},
                    counts={
                        "fetched": report.fetched,
                        "ingested": report.ingested,
                        "skipped": report.skipped,
                        "removed": report.removed,
                        "pending_deletes": report.pending_deletes,
                        "chunks": report.chunks,
                        "failed": len(report.failed),
                    },
                    fetch_error=report.fetch_error,
                    duration_ms=int(report.elapsed_s * 1000),
                ),
            )
        return report

    def _ingest_one(
        self,
        spec: SourceSpec,
        doc: FetchedDocument,
        doc_id: str,
        replace: bool,
        *,
        sync_run: Any = None,
        writer_permit: DocumentWritePermit | None = None,
    ) -> int:
        """入库单篇，返回写入的 chunk 数。

        走 ``add_file`` 而不是 ``add_document``：后者是精简路径，会绕过
        cleaner、预切分策略与切分模式路由，导致远程文档和本地文档的切分
        结果不一致。

        入库成功后**登记进关系库**。此前这条路径完全不碰 catalog：源同步的文档
        在关系库里根本不存在，于是既不计入配额、也不出现在文档列表、删除时也无
        从退还用量。三本账（关系库 / ``_state.json`` / Milvus 实查）各说各话，
        而配额只认关系库那本——那本永远是 0，所以源同步这条路上配额从来没生效
        过，想灌多少灌多少。
        """
        self._assert_run_current(sync_run)
        if writer_permit is not None:
            assert_document_write_permit(writer_permit)

        metadata = dict(spec.metadata)
        metadata.update(doc.metadata)
        metadata["source_name"] = spec.name
        metadata["rel_path"] = doc.rel_path

        durable_writer = bool(
            sync_run is not None and self.ledger is not None and spec.tenant_id
        )
        if durable_writer:
            from core.chunk_catalog import ChunkCatalog
            from core.index_operations import IndexOperationQueue
            from core.ingest_ledger import IngestLedger
            from indexing.state_machine import DocumentIngestJob

            existing = catalog.get_document(doc_id)
            if existing is None:
                registered = self._register(spec, doc, doc_id, 0)
                if registered is None:
                    raise SourceSyncLedgerConflict(
                        "source document authority registration failed"
                    )
                existing = catalog.get_document(doc_id)
            try:
                self._assert_run_current(sync_run)
            except SourceSyncLedgerConflict:
                self._queue_stale_document_cleanup(spec, doc_id, sync_run)
                raise
            if writer_permit is not None:
                assert_document_write_permit(writer_permit)
            if existing is None:
                raise SourceSyncLedgerConflict(
                    "source document authority registration failed"
                )
            if existing.get("status") in {"completed", "error"}:
                catalog.reset_document(doc_id, detail="源同步 generation-fenced 重建")
            result = DocumentIngestJob(
                self.pipeline,
                doc_id,
                dataset_id=spec.dataset_id,
                ledger=IngestLedger(self.ledger.engine),
                operation_queue=IndexOperationQueue(self.ledger.engine),
                chunk_catalog=ChunkCatalog(self.ledger.engine),
                ledger_mode="active",
                attempt_kind="source_sync",
                source_id=str(self._active_source_id or ""),
                source_generation=int(sync_run.source_generation or 0),
                source_run_id=(
                    str(sync_run.id)
                    if str(getattr(self.ledger, "execution_owner", "") or "")
                    else None
                ),
                execution_owner=(
                    str(getattr(self.ledger, "execution_owner", "") or "") or None
                ),
            ).run(
                str(doc.local_path),
                metadata=metadata,
            )
            if result.get("status") == "superseded":
                self._queue_stale_document_cleanup(spec, doc_id, sync_run)
                self._assert_run_current(sync_run)
                raise DocumentWriteSuperseded(
                    str(result.get("error") or "source writer superseded")
                )
            if result.get("status") == "error":
                raise RuntimeError(str(result.get("error") or "source ingest failed"))
            return int(result.get("chunk_count") or 0)

        self._assert_run_current(sync_run)
        if writer_permit is not None:
            assert_document_write_permit(writer_permit)
        if replace and writer_permit is not None:
            from indexing.reindex import reindex_document

            result = reindex_document(
                self.pipeline,
                doc_id,
                str(doc.local_path),
                metadata=metadata,
                force=True,
            )
            if result.get("status") == "superseded":
                raise DocumentWriteSuperseded(str(result.get("error") or "source writer superseded"))
            if result.get("status") == "error":
                raise RuntimeError(str(result.get("error") or "source reindex failed"))
            count = int(result.get("chunk_count") or 0)
        else:
            result = self.pipeline.add_file(
                str(doc.local_path),
                doc_id=doc_id,
                source=doc.uri,
                metadata=metadata,
                tenant_id=spec.tenant_id or None,
                dataset_id=spec.dataset_id,
            )
            count = int(getattr(result, "chunk_count", 0) or 0)
        self._assert_run_current(sync_run)
        if writer_permit is not None:
            assert_document_write_permit(writer_permit)
        self._register(spec, doc, doc_id, count)
        return count

    def _register(
        self, spec: SourceSpec, doc: FetchedDocument, doc_id: str, chunk_count: int
    ) -> dict[str, Any] | None:
        """把这篇文档登记进关系库并同步用量。

        登记失败**不让入库失败**：chunk 已经写进 Milvus 了，此时抛错只会让上层
        把这篇记成 failed 并在下一轮重灌一遍，结果是内容重复而账依旧不平。与
        ``IngestPipeline._enforce_chunk_quota`` 同一套 fail-open 取舍——记账是成
        本护栏，不是安全边界。偏了的账由 ``catalog.recount_usage`` 对账兜底。
        """
        if not spec.tenant_id:
            # 未启用多租户时没有配额边界可言，也没有租户行可挂账。
            return None
        try:
            from core import catalog

            return catalog.register_synced_document(
                tenant_id=spec.tenant_id,
                dataset_id=spec.dataset_id,
                doc_id=doc_id,
                name=doc.rel_path or doc_id,
                chunk_count=chunk_count,
                file_path=doc.uri or "",
                file_hash=doc.content_hash or "",
                doc_type=Path(doc.rel_path or "").suffix.lstrip(".").lower(),
                source_uri=doc.uri or None,
                external_id=doc.rel_path or None,
                source_type=spec.type,
                source_id=self._active_source_id,
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "文档 %s 已入库，但登记到关系库失败（可跑 recount_usage 对账）: %s",
                doc_id, exc,
            )
            return None


__all__ = [
    "SourceSpec",
    "SourceSyncer",
    "SyncReport",
    "load_manifest",
    "make_doc_id",
]
