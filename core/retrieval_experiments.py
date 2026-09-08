"""Authoritative retrieval experiment snapshots and reviewer judgments."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import math
import re
import sqlite3
import unicodedata
import uuid
from urllib.parse import urlsplit
from typing import Any, NoReturn

from sqlalchemy import Engine, event, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from core.knowledge_governance import AuditContext, sanitize_audit_snapshot
from core.secret_fields import is_sensitive_field, normalize_field_name
from models.orm import (
    ChunkHead,
    ChunkRevision,
    Dataset,
    Document,
    KnowledgeAuditEvent,
    RetrievalExperiment,
    RetrievalJudgment,
)

_EXPERIMENT_STATUSES = frozenset({"completed", "failed"})
_RELEVANCE_LABELS = frozenset({"relevant", "partial", "irrelevant"})
_WHITESPACE = re.compile(r"\s+")
_MAX_JSON_DEPTH = 32
_MAX_JSON_NODES = 100_000


class RetrievalExperimentError(RuntimeError):
    """Base error for retrieval experiment authority operations."""


class RetrievalExperimentNotFound(RetrievalExperimentError):
    """Requested entity does not exist inside the supplied scope."""


class RetrievalExperimentConflict(RetrievalExperimentError):
    """Optimistic-concurrency or authority contract was violated."""


class RetrievalExperimentDatasetInactive(RetrievalExperimentError):
    """The scoped dataset exists but cannot accept retrieval mutations."""


class RetrievalExperimentUnavailable(RetrievalExperimentError):
    """Retrieval execution dependencies are unavailable."""


_UNSET = object()


@dataclass(frozen=True)
class JudgmentAgreementSummary:
    experiment_id: str
    judged_results: int
    judgment_count: int
    multi_judged_results: int
    unanimous_results: int
    conflicting_results: int
    exact_agreement_rate: float | None
    label_counts: dict[str, int]
    mean_score: float | None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _clean_text(value: str | None, maximum: int, *, field: str, required: bool = True) -> str:
    result = unicodedata.normalize("NFKC", str(value or "")).strip()
    if required and not result:
        raise ValueError(f"{field} is required")
    if len(result) > maximum:
        raise ValueError(f"{field} must be at most {maximum} characters")
    return result


def _canonical_actor(audit: AuditContext) -> str:
    return _clean_text(audit.actor_id, 64, field="actor_id")


def normalize_query(value: str) -> str:
    query = _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", str(value or ""))).strip()
    if not query:
        raise ValueError("query is required")
    if len(query) > 20_000:
        raise ValueError("query must be at most 20000 characters")
    return query


def query_hash(value: str) -> str:
    return hashlib.sha256(normalize_query(value).encode("utf-8")).hexdigest()


def _safe_credential_reference_uri(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a safe secret or vault URI")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"secret", "vault"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{field} must be a safe secret or vault URI")
    return value


def _credential_reference(value: Any, *, field: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"credential_ref"}:
        raise ValueError(
            f"{field} must not contain plaintext credentials; use a credential_ref object"
        )
    reference = _safe_credential_reference_uri(
        value.get("credential_ref"), field=f"{field}.credential_ref"
    )
    return {"credential_ref": reference}


def _strict_json_tree(value: Any, *, field: str) -> dict[str, Any]:
    """Copy a lossless standard-JSON tree without applying audit redaction."""
    nodes = 0
    active_containers: set[int] = set()

    def visit(item: Any, *, key: str | None = None, depth: int = 0) -> Any:
        nonlocal nodes
        nodes += 1
        if nodes > _MAX_JSON_NODES:
            raise ValueError(f"{field} JSON tree exceeds {_MAX_JSON_NODES} nodes")
        if depth > _MAX_JSON_DEPTH:
            raise ValueError(f"{field} JSON tree exceeds depth {_MAX_JSON_DEPTH}")
        if key is not None and normalize_field_name(key) == "credentialref":
            return _safe_credential_reference_uri(item, field=f"{field}.{key}")
        if key is not None and is_sensitive_field(key):
            return _credential_reference(item, field=f"{field}.{key}")
        if item is None or isinstance(item, (str, bool, int)):
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError(f"{field} JSON tree contains a non-finite number")
            return item
        if not isinstance(item, (dict, list)):
            raise ValueError(f"{field} must be a strict JSON tree")
        identity = id(item)
        if identity in active_containers:
            raise ValueError(f"{field} JSON tree contains a cycle")
        active_containers.add(identity)
        try:
            if isinstance(item, dict):
                result: dict[str, Any] = {}
                for item_key, item_value in item.items():
                    if not isinstance(item_key, str):
                        raise ValueError(f"{field} JSON tree keys must be strings")
                    result[item_key] = visit(
                        item_value, key=item_key, depth=depth + 1
                    )
                return result
            return [visit(child, depth=depth + 1) for child in item]
        finally:
            active_containers.remove(identity)

    copied = visit(value)
    if not isinstance(copied, dict):
        raise ValueError(f"{field} must be a JSON object")
    return copied


def _require_revision(snapshot: dict[str, Any], key: str, *, field: str) -> None:
    value = snapshot.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field}.{key} must be a positive integer")


def _require_generation(snapshot: dict[str, Any], *, field: str) -> int:
    value = snapshot.get("dataset_serving_generation")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field}.dataset_serving_generation must be a nonnegative integer")
    return value


def _validate_snapshot_fences(
    strategy: dict[str, Any], result: dict[str, Any], evidence: dict[str, Any]
) -> None:
    _require_revision(strategy, "strategy_revision", field="strategy_snapshot")
    _require_revision(result, "result_revision", field="result_snapshot")
    _require_revision(evidence, "evidence_revision", field="evidence_lineage")
    generations = {
        _require_generation(strategy, field="strategy_snapshot"),
        _require_generation(result, field="result_snapshot"),
        _require_generation(evidence, field="evidence_lineage"),
    }
    if len(generations) != 1:
        raise ValueError("snapshot dataset_serving_generation values must match")


def _result_by_rank(snapshot: dict[str, Any]) -> dict[int, dict[str, Any]]:
    raw_results = snapshot.get("results")
    if not isinstance(raw_results, list):
        raise ValueError("result_snapshot.results must be a JSON array")
    result: dict[int, dict[str, Any]] = {}
    for raw in raw_results:
        if not isinstance(raw, dict):
            raise ValueError("result_snapshot results must be JSON objects")
        rank = raw.get("rank")
        if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
            raise ValueError("result_snapshot rank must be a positive integer")
        if rank in result:
            raise ValueError("result_snapshot ranks must be unique")
        document_id = raw.get("document_id")
        if not isinstance(document_id, str) or not document_id:
            raise ValueError("result_snapshot document_id must be a nonempty string")
        chunk_id = raw.get("chunk_id")
        if chunk_id is not None and (not isinstance(chunk_id, str) or not chunk_id):
            raise ValueError("result_snapshot chunk_id must be a nonempty string or null")
        historical_id = raw.get("chunk_revision_id")
        document_revision = raw.get("document_revision")
        if historical_id is not None:
            if not isinstance(historical_id, str) or not historical_id:
                raise ValueError("result_snapshot chunk_revision_id must be nonempty")
            if document_revision is not None:
                raise ValueError("historical result document_revision must be null")
        elif (isinstance(document_revision, bool) or
              not isinstance(document_revision, int) or document_revision < 0):
            raise ValueError("result_snapshot document_revision must be a nonnegative integer")
        content_revision = raw.get("content_revision")
        if (isinstance(content_revision, bool) or
                not isinstance(content_revision, int) or content_revision < 0):
            raise ValueError("result_snapshot content_revision must be a nonnegative integer")
        result[rank] = raw
    return result


def _validate_result_references(
    session: Session, tenant_id: str, dataset_id: str,
    results: dict[int, dict[str, Any]],
) -> None:
    document_ids = {str(item["document_id"]) for item in results.values()}
    documents = {row.id: row for row in session.scalars(select(Document).where(
        Document.tenant_id == tenant_id, Document.dataset_id == dataset_id,
        Document.id.in_(document_ids)))}
    if document_ids - documents.keys():
        raise RetrievalExperimentNotFound("result document does not exist in dataset scope")
    chunk_ids = {str(item["chunk_id"]) for item in results.values() if item["chunk_id"]}
    chunks = {row.id: row for row in session.scalars(select(ChunkHead).where(
        ChunkHead.tenant_id == tenant_id, ChunkHead.dataset_id == dataset_id,
        ChunkHead.id.in_(chunk_ids)))} if chunk_ids else {}
    if chunk_ids - chunks.keys():
        raise RetrievalExperimentNotFound("result chunk does not exist in dataset scope")
    revision_ids = {str(item["chunk_revision_id"]) for item in results.values()
                    if item.get("chunk_revision_id")}
    revisions = {row.id: row for row in session.scalars(select(ChunkRevision).where(
        ChunkRevision.tenant_id == tenant_id, ChunkRevision.dataset_id == dataset_id,
        ChunkRevision.id.in_(revision_ids)))} if revision_ids else {}
    if revision_ids - revisions.keys():
        raise RetrievalExperimentConflict("historical chunk revision snapshot does not exist")
    for item in results.values():
        document_id = str(item["document_id"])
        chunk_id = item["chunk_id"]
        content_revision = int(item["content_revision"])
        revision_id = item.get("chunk_revision_id")
        if chunk_id is None:
            if item["document_revision"] != documents[document_id].content_revision or content_revision != 0:
                raise RetrievalExperimentConflict("result document revision does not match document authority")
            continue
        chunk = chunks[str(chunk_id)]
        if chunk.document_id != document_id:
            raise RetrievalExperimentConflict("result chunk does not belong to result document")
        if revision_id:
            revision = revisions[str(revision_id)]
            if (revision.chunk_id != chunk_id or revision.document_id != document_id or
                    revision.revision != content_revision or
                    revision.content_hash != item.get("content_hash")):
                raise RetrievalExperimentConflict("historical chunk revision snapshot is invalid")
        else:
            submitted_document_revision = item["document_revision"]
            if (documents[document_id].content_revision != chunk.document_revision or
                    chunk.document_revision != submitted_document_revision):
                raise RetrievalExperimentConflict(
                    "document and chunk document revision authority does not match"
                )
            if chunk.content_revision != content_revision:
                raise RetrievalExperimentConflict(
                    "current chunk content revision does not match authority; "
                    "historical evidence requires a revision snapshot"
                )
            if item.get("content_hash") != chunk.content_hash:
                raise RetrievalExperimentConflict("current chunk content hash does not match authority")


def _validate_evidence_lineage(evidence: dict[str, Any], results: dict[int, dict[str, Any]]) -> None:
    citations = evidence.get("citations")
    if not isinstance(citations, list):
        raise ValueError("evidence_lineage.citations must be a JSON array")
    if not citations:
        if results:
            raise ValueError("evidence citations are missing ranked results")
        return
    if not results:
        raise ValueError("evidence citations are forged for empty results")
    by_rank: dict[int, dict[str, Any]] = {}
    for citation in citations:
        if not isinstance(citation, dict) or isinstance(citation.get("rank"), bool) or not isinstance(citation.get("rank"), int):
            raise ValueError("evidence citation rank must be an integer")
        rank = citation["rank"]
        if rank in by_rank or rank not in results:
            raise ValueError("evidence citation rank is forged or duplicated")
        expected = results[rank]
        for key in ("document_id", "chunk_id", "document_revision", "content_revision",
                    "chunk_revision_id", "content_hash"):
            if citation.get(key) != expected.get(key):
                raise RetrievalExperimentConflict(f"evidence citation {key} does not match ranked result")
        by_rank[rank] = citation
    if set(by_rank) != set(results):
        raise ValueError("evidence citations are missing ranked results")


def _experiment_snapshot(item: RetrievalExperiment) -> dict[str, Any]:
    return {
        "id": item.id,
        "query_hash": item.query_hash,
        "strategy_snapshot": item.strategy_snapshot,
        "result_snapshot": item.result_snapshot,
        "evidence_lineage": item.evidence_lineage,
        "latency_ms": item.latency_ms,
        "status": item.status,
        "run_id": item.run_id,
    }


def _judgment_snapshot(item: RetrievalJudgment) -> dict[str, Any]:
    return {
        "id": item.id,
        "experiment_id": item.experiment_id,
        "result_rank": item.result_rank,
        "document_id": item.document_id,
        "chunk_id": item.chunk_id,
        "relevance_label": item.relevance_label,
        "score": item.score,
        "note": item.note,
        "revision": item.revision,
        "created_by": item.created_by,
    }


@event.listens_for(RetrievalExperiment, "before_update")
def _prevent_experiment_update(_mapper: Any, _connection: Any, _target: Any) -> None:
    raise TypeError("retrieval experiment snapshots are immutable")


@event.listens_for(RetrievalExperiment, "before_delete")
def _prevent_experiment_delete(_mapper: Any, _connection: Any, _target: Any) -> None:
    raise TypeError("retrieval experiment snapshots are immutable")


class RetrievalExperimentRepository:
    """Tenant/dataset-scoped repository with atomic audit mutations."""

    def __init__(self, engine: Engine):
        self.engine = engine

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:16]}"

    @staticmethod
    def _dataset(session: Session, tenant_id: str, dataset_id: str, *, lock: bool = False) -> Dataset:
        statement = select(Dataset).where(Dataset.tenant_id == tenant_id, Dataset.id == dataset_id)
        row = session.scalar(statement.with_for_update() if lock else statement)
        if row is None:
            raise RetrievalExperimentNotFound("dataset does not exist in tenant scope")
        return row

    def _before_dataset_active_fence(
        self,
        _session: Session,
        _tenant_id: str,
        _dataset_id: str,
    ) -> None:
        """Test seam after the scoped read and before the authoritative DML fence."""

    def _raise_dataset_fence_failure(
        self,
        session: Session,
        tenant_id: str,
        dataset_id: str,
        *,
        expected_generation: int | None,
    ) -> NoReturn:
        session.rollback()
        row = self._dataset(session, tenant_id, dataset_id)
        if str(row.status).strip().casefold() != "active":
            raise RetrievalExperimentDatasetInactive(
                "dataset is not active for retrieval experiment mutation"
            )
        if (
            expected_generation is not None
            and row.serving_generation != expected_generation
        ):
            raise RetrievalExperimentConflict(
                "snapshot serving generation does not match dataset authority"
            )
        raise RetrievalExperimentConflict("dataset active mutation fence conflict")

    def _active_dataset(
        self,
        session: Session,
        tenant_id: str,
        dataset_id: str,
        *,
        expected_generation: int | None = None,
    ) -> Dataset:
        row = self._dataset(session, tenant_id, dataset_id, lock=True)
        self._before_dataset_active_fence(session, tenant_id, dataset_id)
        predicates = [
            Dataset.tenant_id == tenant_id,
            Dataset.id == dataset_id,
            Dataset.status == "active",
        ]
        if expected_generation is not None:
            predicates.append(Dataset.serving_generation == expected_generation)
        try:
            fence = session.execute(
                update(Dataset)
                .where(*predicates)
                .values(status=Dataset.status)
            )
        except OperationalError as exc:
            sqlite_lock_codes = {
                sqlite3.SQLITE_BUSY,
                sqlite3.SQLITE_LOCKED,
                getattr(sqlite3, "SQLITE_BUSY_SNAPSHOT", -1),
            }
            if (
                session.get_bind().dialect.name != "sqlite"
                or getattr(exc.orig, "sqlite_errorcode", None) not in sqlite_lock_codes
            ):
                raise
            try:
                self._raise_dataset_fence_failure(
                    session,
                    tenant_id,
                    dataset_id,
                    expected_generation=expected_generation,
                )
            except RetrievalExperimentError as authority_error:
                raise authority_error from exc
        if fence.rowcount != 1:
            self._raise_dataset_fence_failure(
                session,
                tenant_id,
                dataset_id,
                expected_generation=expected_generation,
            )
        return row

    @staticmethod
    def _experiment(
        session: Session,
        tenant_id: str,
        dataset_id: str,
        experiment_id: str,
        *,
        lock: bool = False,
    ) -> RetrievalExperiment:
        query = select(RetrievalExperiment).where(
            RetrievalExperiment.tenant_id == tenant_id,
            RetrievalExperiment.dataset_id == dataset_id,
            RetrievalExperiment.id == experiment_id,
        )
        row = session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise RetrievalExperimentNotFound(
                "retrieval experiment does not exist in dataset scope"
            )
        return row

    @staticmethod
    def _judgment(
        session: Session,
        tenant_id: str,
        dataset_id: str,
        judgment_id: str,
        *,
        lock: bool = False,
    ) -> RetrievalJudgment:
        query = select(RetrievalJudgment).where(
            RetrievalJudgment.tenant_id == tenant_id,
            RetrievalJudgment.dataset_id == dataset_id,
            RetrievalJudgment.id == judgment_id,
        )
        row = session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise RetrievalExperimentNotFound(
                "retrieval judgment does not exist in dataset scope"
            )
        return row

    def _add_audit_event(
        self,
        session: Session,
        *,
        tenant_id: str,
        dataset_id: str,
        actor_id: str,
        audit: AuditContext,
        action: str,
        resource_type: str,
        resource_id: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> KnowledgeAuditEvent:
        row = KnowledgeAuditEvent(
            id=self._new_id("audit"),
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            before_snapshot=sanitize_audit_snapshot(before),
            after_snapshot=sanitize_audit_snapshot(after),
            request_id=_clean_text(audit.request_id, 128, field="request_id"),
            request_ip=_clean_text(audit.request_ip, 64, field="request_ip", required=False),
            occurred_at=_utc_now(),
        )
        session.add(row)
        session.flush()
        return row

    @staticmethod
    def _commit(session: Session, message: str) -> None:
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise RetrievalExperimentConflict(message) from exc

    def get_active_dataset_generation(self, tenant_id: str, dataset_id: str) -> int:
        """Return the active dataset serving generation inside the supplied scope."""
        with Session(self.engine) as session:
            row = self._dataset(session, tenant_id, dataset_id)
            if str(row.status).strip().casefold() != "active":
                raise RetrievalExperimentDatasetInactive(
                    "dataset is not active for retrieval experiment mutation"
                )
            return int(row.serving_generation)

    def create_experiments_batch(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        run_id: str | None,
        expected_generation: int,
        experiments: list[dict[str, Any]],
        audit: AuditContext,
        lifecycle_change_is_conflict: bool = True,
    ) -> list[RetrievalExperiment]:
        """Atomically persist one retrieval run and all of its variant facts."""
        if not experiments:
            raise ValueError("experiments must contain at least one item")
        if isinstance(expected_generation, bool) or int(expected_generation) < 0:
            raise ValueError("expected_generation must be a nonnegative integer")
        generation = int(expected_generation)
        actor_id = _canonical_actor(audit)
        normalized_tenant = _clean_text(tenant_id, 64, field="tenant_id")
        normalized_dataset = _clean_text(dataset_id, 64, field="dataset_id")
        normalized_run_id = (
            None if run_id is None else _clean_text(run_id, 64, field="run_id")
        )
        prepared: list[tuple[dict[str, Any], dict[int, dict[str, Any]]]] = []
        for item in experiments:
            if not isinstance(item, dict):
                raise ValueError("experiment batch items must be objects")
            normalized_query = normalize_query(str(item.get("query") or ""))
            lifecycle = _clean_text(item.get("status"), 16, field="status").casefold()
            if lifecycle not in _EXPERIMENT_STATUSES:
                raise ValueError("status must be completed or failed")
            latency_raw = item.get("latency_ms")
            if isinstance(latency_raw, bool):
                raise ValueError("latency_ms must be nonnegative")
            latency = int(latency_raw)
            if latency < 0:
                raise ValueError("latency_ms must be nonnegative")
            strategy = _strict_json_tree(
                item.get("strategy_snapshot"), field="strategy_snapshot"
            )
            results = _strict_json_tree(
                item.get("result_snapshot"), field="result_snapshot"
            )
            evidence = _strict_json_tree(
                item.get("evidence_lineage"), field="evidence_lineage"
            )
            _validate_snapshot_fences(strategy, results, evidence)
            if int(strategy["dataset_serving_generation"]) != generation:
                raise RetrievalExperimentConflict(
                    "snapshot serving generation does not match batch authority"
                )
            ranked_results = _result_by_rank(results)
            _validate_evidence_lineage(evidence, ranked_results)
            created_at = item.get("created_at")
            if created_at is not None and not isinstance(created_at, datetime):
                raise ValueError("created_at must be a datetime")
            prepared.append(
                (
                    {
                        "id": _clean_text(
                            item.get("experiment_id") or self._new_id("retrieval-experiment"),
                            64,
                            field="experiment_id",
                        ),
                        "tenant_id": normalized_tenant,
                        "dataset_id": normalized_dataset,
                        "query": normalized_query,
                        "query_hash": hashlib.sha256(
                            normalized_query.encode("utf-8")
                        ).hexdigest(),
                        "strategy_snapshot": strategy,
                        "result_snapshot": results,
                        "evidence_lineage": evidence,
                        "latency_ms": latency,
                        "status": lifecycle,
                        "created_by": actor_id,
                        "created_at": created_at or _utc_now(),
                        "run_id": normalized_run_id,
                    },
                    ranked_results,
                )
            )

        with Session(self.engine, expire_on_commit=False) as session:
            try:
                try:
                    self._active_dataset(
                        session,
                        normalized_tenant,
                        normalized_dataset,
                        expected_generation=generation,
                    )
                except RetrievalExperimentDatasetInactive as exc:
                    if not lifecycle_change_is_conflict:
                        raise
                    raise RetrievalExperimentConflict(
                        "dataset lifecycle changed after retrieval"
                    ) from exc
                for _values, ranked_results in prepared:
                    _validate_result_references(
                        session,
                        normalized_tenant,
                        normalized_dataset,
                        ranked_results,
                    )
                rows: list[RetrievalExperiment] = []
                for values, _ranked_results in prepared:
                    row = RetrievalExperiment(**values)
                    session.add(row)
                    session.flush()
                    self._add_audit_event(
                        session,
                        tenant_id=normalized_tenant,
                        dataset_id=normalized_dataset,
                        actor_id=actor_id,
                        audit=audit,
                        action="retrieval_experiment.create",
                        resource_type="retrieval_experiment",
                        resource_id=row.id,
                        after=_experiment_snapshot(row),
                    )
                    rows.append(row)
                self._commit(session, "retrieval experiment identity already exists")
                return rows
            except IntegrityError as exc:
                session.rollback()
                raise RetrievalExperimentConflict(
                    "retrieval experiment identity already exists"
                ) from exc

    def create_experiment(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        query: str,
        strategy_snapshot: dict[str, Any],
        result_snapshot: dict[str, Any],
        evidence_lineage: dict[str, Any],
        latency_ms: int,
        status: str,
        audit: AuditContext,
        run_id: str | None = None,
        experiment_id: str | None = None,
        created_at: datetime | None = None,
    ) -> RetrievalExperiment:
        strategy = _strict_json_tree(strategy_snapshot, field="strategy_snapshot")
        results = _strict_json_tree(result_snapshot, field="result_snapshot")
        evidence = _strict_json_tree(evidence_lineage, field="evidence_lineage")
        _validate_snapshot_fences(strategy, results, evidence)
        expected_generation = _require_generation(strategy, field="strategy_snapshot")
        rows = self.create_experiments_batch(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            run_id=run_id,
            expected_generation=expected_generation,
            experiments=[
                {
                    "query": query,
                    "strategy_snapshot": strategy,
                    "result_snapshot": results,
                    "evidence_lineage": evidence,
                    "latency_ms": latency_ms,
                    "status": status,
                    "experiment_id": experiment_id,
                    "created_at": created_at,
                }
            ],
            audit=audit,
            lifecycle_change_is_conflict=False,
        )
        return rows[0]

    def get_experiment(
        self, tenant_id: str, dataset_id: str, experiment_id: str
    ) -> RetrievalExperiment:
        with Session(self.engine, expire_on_commit=False) as session:
            return self._experiment(session, tenant_id, dataset_id, experiment_id)

    def list_experiments(
        self,
        tenant_id: str,
        dataset_id: str,
        *,
        status: str | None = None,
        query_hash: str | None = None,
        run_id: str | None = None,
        created_by: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        before_sequence: int | None = None,
        before_id: str | None = None,
        limit: int = 100,
    ) -> list[RetrievalExperiment]:
        bounded = max(1, min(int(limit), 500))
        with Session(self.engine, expire_on_commit=False) as session:
            self._dataset(session, tenant_id, dataset_id)
            if before_id is not None and before_sequence is None:
                raise ValueError("cursor id requires cursor sequence")
            if before_sequence is not None:
                sequence = int(before_sequence)
                if sequence < 1:
                    raise ValueError("cursor sequence must be positive")
                cursor = session.scalar(
                    select(RetrievalExperiment.id).where(
                        RetrievalExperiment.tenant_id == tenant_id,
                        RetrievalExperiment.dataset_id == dataset_id,
                        RetrievalExperiment.sequence == sequence,
                    )
                )
                if cursor is None or (before_id is not None and cursor != before_id):
                    raise ValueError("cursor sequence/id does not match dataset scope")
            statement = select(RetrievalExperiment).where(
                RetrievalExperiment.tenant_id == tenant_id,
                RetrievalExperiment.dataset_id == dataset_id,
            )
            if status is not None:
                lifecycle = _clean_text(status, 16, field="status").casefold()
                if lifecycle not in _EXPERIMENT_STATUSES:
                    raise ValueError("status must be completed or failed")
                statement = statement.where(RetrievalExperiment.status == lifecycle)
            if query_hash is not None:
                digest = _clean_text(query_hash, 64, field="query_hash").casefold()
                if not re.fullmatch(r"[0-9a-f]{64}", digest):
                    raise ValueError("query_hash must be a SHA-256 hex digest")
                statement = statement.where(RetrievalExperiment.query_hash == digest)
            if run_id is not None:
                statement = statement.where(
                    RetrievalExperiment.run_id == _clean_text(run_id, 64, field="run_id")
                )
            if created_by is not None:
                statement = statement.where(
                    RetrievalExperiment.created_by
                    == _clean_text(created_by, 64, field="created_by")
                )
            if created_from is not None:
                statement = statement.where(RetrievalExperiment.created_at >= created_from)
            if created_to is not None:
                statement = statement.where(RetrievalExperiment.created_at <= created_to)
            if before_sequence is not None:
                statement = statement.where(
                    RetrievalExperiment.sequence < int(before_sequence)
                )
            return list(
                session.scalars(
                    statement.order_by(RetrievalExperiment.sequence.desc()).limit(bounded)
                )
            )

    def add_judgment(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        experiment_id: str,
        result_rank: int,
        relevance_label: str,
        audit: AuditContext,
        document_id: str | None = None,
        chunk_id: str | None = None,
        score: int | None = None,
        note: str = "",
        judgment_id: str | None = None,
    ) -> RetrievalJudgment:
        rank = int(result_rank)
        if rank < 1:
            raise ValueError("result rank must be positive")
        label = _clean_text(relevance_label, 16, field="relevance_label").casefold()
        if label not in _RELEVANCE_LABELS:
            raise ValueError("relevance_label must be relevant, partial, or irrelevant")
        numeric_score = None if score is None else int(score)
        if numeric_score is not None and not 0 <= numeric_score <= 3:
            raise ValueError("score must be between 0 and 3")
        actor_id = _canonical_actor(audit)
        with Session(self.engine, expire_on_commit=False) as session:
            self._active_dataset(session, tenant_id, dataset_id)
            experiment = self._experiment(session, tenant_id, dataset_id, experiment_id, lock=True)
            result = _result_by_rank(experiment.result_snapshot).get(rank)
            if result is None:
                raise ValueError("result rank does not exist in experiment result snapshot")
            expected_document = result.get("document_id")
            expected_chunk = result.get("chunk_id")
            if document_id is not None and document_id != expected_document:
                raise ValueError("document_id does not match result snapshot")
            if chunk_id is not None and chunk_id != expected_chunk:
                raise ValueError("chunk_id does not match result snapshot")
            resolved_document_id = expected_document if document_id is None else document_id
            resolved_chunk_id = expected_chunk if chunk_id is None else chunk_id
            row = RetrievalJudgment(
                id=_clean_text(
                    judgment_id or self._new_id("retrieval-judgment"),
                    64,
                    field="judgment_id",
                ),
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                experiment_id=experiment.id,
                result_rank=rank,
                document_id=(
                    None
                    if resolved_document_id is None
                    else _clean_text(resolved_document_id, 64, field="document_id")
                ),
                chunk_id=(
                    None
                    if resolved_chunk_id is None
                    else _clean_text(resolved_chunk_id, 512, field="chunk_id")
                ),
                relevance_label=label,
                score=numeric_score,
                note=_clean_text(note, 20_000, field="note", required=False),
                revision=1,
                created_by=actor_id,
                created_at=_utc_now(),
            )
            session.add(row)
            try:
                session.flush()
            except IntegrityError as exc:
                session.rollback()
                raise RetrievalExperimentConflict(
                    "reviewer already judged this experiment result rank"
                ) from exc
            self._add_audit_event(
                session,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                actor_id=actor_id,
                audit=audit,
                action="retrieval_judgment.create",
                resource_type="retrieval_judgment",
                resource_id=row.id,
                after=_judgment_snapshot(row),
            )
            self._commit(session, "retrieval judgment creation conflict")
            return row

    def _update_judgment_fields(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        judgment_id: str,
        expected_revision: int,
        audit: AuditContext,
        experiment_id: str | None = None,
        relevance_label: str | object = _UNSET,
        score: int | None | object = _UNSET,
        note: str | object = _UNSET,
    ) -> RetrievalJudgment:
        revision = int(expected_revision)
        if revision < 1:
            raise ValueError("expected_revision must be positive")
        values: dict[str, Any] = {}
        if relevance_label is not _UNSET:
            if relevance_label is None:
                raise ValueError("relevance_label must not be null")
            label = _clean_text(relevance_label, 16, field="relevance_label").casefold()
            if label not in _RELEVANCE_LABELS:
                raise ValueError("relevance_label must be relevant, partial, or irrelevant")
            values["relevance_label"] = label
        if score is not _UNSET:
            numeric_score = None if score is None else int(score)
            if numeric_score is not None and not 0 <= numeric_score <= 3:
                raise ValueError("score must be between 0 and 3")
            values["score"] = numeric_score
        if note is not _UNSET:
            if note is None:
                raise ValueError("note must not be null")
            values["note"] = _clean_text(note, 20_000, field="note", required=False)
        if not values:
            raise ValueError("at least one judgment field must be provided")

        actor_id = _canonical_actor(audit)
        with Session(self.engine, expire_on_commit=False) as session:
            self._active_dataset(session, tenant_id, dataset_id)
            statement = select(RetrievalJudgment).where(
                RetrievalJudgment.id == judgment_id,
                RetrievalJudgment.tenant_id == tenant_id,
                RetrievalJudgment.dataset_id == dataset_id,
            )
            if experiment_id is not None:
                statement = statement.where(RetrievalJudgment.experiment_id == experiment_id)
            row = session.scalar(statement.with_for_update())
            if row is None:
                raise RetrievalExperimentNotFound(
                    "retrieval judgment does not exist in experiment scope"
                )
            if row.created_by != actor_id:
                raise RetrievalExperimentConflict("judgment belongs to a different judge")
            before = _judgment_snapshot(row)
            predicates = [
                RetrievalJudgment.id == row.id,
                RetrievalJudgment.tenant_id == tenant_id,
                RetrievalJudgment.dataset_id == dataset_id,
                RetrievalJudgment.created_by == actor_id,
                RetrievalJudgment.revision == revision,
            ]
            if experiment_id is not None:
                predicates.append(RetrievalJudgment.experiment_id == experiment_id)
            result = session.execute(
                update(RetrievalJudgment).where(*predicates).values(
                    **values, revision=RetrievalJudgment.revision + 1,
                )
            )
            if result.rowcount != 1:
                session.rollback()
                raise RetrievalExperimentConflict("judgment revision conflict")
            session.expire(row)
            session.refresh(row)
            self._add_audit_event(
                session,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                actor_id=actor_id,
                audit=audit,
                action="retrieval_judgment.update",
                resource_type="retrieval_judgment",
                resource_id=row.id,
                before=before,
                after=_judgment_snapshot(row),
            )
            self._commit(session, "retrieval judgment update conflict")
            return row

    def update_judgment_partial(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        experiment_id: str,
        judgment_id: str,
        expected_revision: int,
        audit: AuditContext,
        relevance_label: str | object = _UNSET,
        score: int | None | object = _UNSET,
        note: str | object = _UNSET,
    ) -> RetrievalJudgment:
        return self._update_judgment_fields(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            experiment_id=experiment_id,
            judgment_id=judgment_id,
            expected_revision=expected_revision,
            relevance_label=relevance_label,
            score=score,
            note=note,
            audit=audit,
        )

    def update_judgment(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        judgment_id: str,
        expected_revision: int,
        relevance_label: str,
        audit: AuditContext,
        score: int | None = None,
        note: str = "",
    ) -> RetrievalJudgment:
        return self._update_judgment_fields(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            judgment_id=judgment_id,
            expected_revision=expected_revision,
            relevance_label=relevance_label,
            score=score,
            note=note,
            audit=audit,
        )

    def summarize_agreement(
        self, tenant_id: str, dataset_id: str, experiment_id: str
    ) -> JudgmentAgreementSummary:
        with Session(self.engine) as session:
            self._experiment(session, tenant_id, dataset_id, experiment_id)
            rows = list(
                session.scalars(
                    select(RetrievalJudgment).where(
                        RetrievalJudgment.tenant_id == tenant_id,
                        RetrievalJudgment.dataset_id == dataset_id,
                        RetrievalJudgment.experiment_id == experiment_id,
                    )
                )
            )
        by_rank: dict[int, list[RetrievalJudgment]] = defaultdict(list)
        for row in rows:
            by_rank[row.result_rank].append(row)
        multi = [items for items in by_rank.values() if len(items) >= 2]
        unanimous = sum(
            1 for items in multi if len({item.relevance_label for item in items}) == 1
        )
        scores = [item.score for item in rows if item.score is not None]
        labels = Counter(item.relevance_label for item in rows)
        return JudgmentAgreementSummary(
            experiment_id=experiment_id,
            judged_results=len(by_rank),
            judgment_count=len(rows),
            multi_judged_results=len(multi),
            unanimous_results=unanimous,
            conflicting_results=len(multi) - unanimous,
            exact_agreement_rate=None if not multi else unanimous / len(multi),
            label_counts={key: labels[key] for key in sorted(labels)},
            mean_score=None if not scores else sum(scores) / len(scores),
        )


__all__ = [
    "JudgmentAgreementSummary",
    "RetrievalExperimentConflict",
    "RetrievalExperimentDatasetInactive",
    "RetrievalExperimentError",
    "RetrievalExperimentNotFound",
    "RetrievalExperimentRepository",
    "RetrievalExperimentUnavailable",
    "normalize_query",
    "query_hash",
]
