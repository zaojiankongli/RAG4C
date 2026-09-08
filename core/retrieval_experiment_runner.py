"""Pure retrieval A/B experiment execution with immutable authority snapshots."""
from __future__ import annotations

from copy import copy, deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import PurePosixPath
import re
import time
from typing import Any, Literal, Sequence
from urllib.parse import urlsplit, urlunsplit
import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from core.knowledge_governance import AuditContext, sanitize_audit_snapshot
from core.retrieval_experiments import RetrievalExperimentRepository
from models.orm import ChunkHead, ChunkRevision, Document, RetrievalExperiment
from models.schemas import RouteDecision
from retrieval.pipeline import RetrievalPipeline, RetrievalResult

RouteTarget = Literal["auto", "hybrid", "vector_graph_rag", "full"]
SourceDiversity = Literal["off", "group_only", "group_mmr"]
_ROUTE_TARGETS = frozenset({"auto", "hybrid", "vector_graph_rag", "full"})
_SOURCE_DIVERSITY = frozenset({"off", "group_only", "group_mmr"})
_SAFE_ACL = re.compile(r"^[^\"'\\\x00-\x1f\x7f]+$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_URI = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s<>\[\]{}\"']+")
_AUTH_SCHEME = re.compile(r"(?i)\b(basic|bearer)\s+[A-Za-z0-9._~+/=-]+")
_AUTHORIZATION_ASSIGNMENT = re.compile(
    r"(?ix)"
    r"(?P<prefix>(?P<keyquote>[\"']?)authorization(?P=keyquote)\s*[:=]\s*)"
    r"(?P<value>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|"
    r"(?:basic|bearer)\s+[^\s,;}\]]+|[^\s,;}\]]+)"
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?ix)"
    r"(?P<prefix>(?P<keyquote>[\"']?)"
    r"(?:password|passwd|pwd|token|secret|api[_-]?key|access[_-]?key|"
    r"refresh[_-]?token|authorization|basic|bearer|cookie|credential|"
    r"private[_-]?key|signing[_-]?key|session[_-]?key)"
    r"(?P=keyquote)\s*[:=]\s*)"
    r"(?P<value>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;}\]]+)"
)
_TIMING_TRACE = re.compile(
    r"^(?:gate|rewrite|route|hyde|embed|search|subqueries|stepback|diversity|"
    r"graph|rerank|sentence_window):(?:0|[1-9]\d*)(?:\.\d{1,3})?ms$"
)
_DEGRADED_TRACE_RULES: tuple[tuple[str, str], ...] = (
    ("rerank \u5931\u8d25\uff1a\u5206\u6570\u6570\u91cf\u4e0d\u7b26", "degraded:rerank:invalid_score_count"),
    ("rerank \u5931\u8d25\uff0c\u4f7f\u7528\u539f\u59cb\u987a\u5e8f", "degraded:rerank:rerank_failed"),
    ("rerank \u672a\u6ce8\u5165", "degraded:rerank:unavailable"),
    ("rerank \u7194\u65ad\u6253\u5f00", "degraded:rerank:circuit_open"),
    ("graph \u68c0\u7d22\u5931\u8d25\uff0c\u964d\u7ea7 hybrid", "degraded:graph:retrieval_failed"),
    ("graph \u65e0\u547d\u4e2d\uff0c\u964d\u7ea7 hybrid", "degraded:graph:no_match"),
    ("graph \u5f00\u5173\u5173\u95ed\u6216\u672a\u6ce8\u5165\u56fe\u68c0\u7d22\u5668", "degraded:graph:unavailable"),
    ("graph \u547d\u4e2d\u4f46 ACL \u8fc7\u6ee4", "degraded:graph:filtered_no_results"),
    ("sentence_window \u5c55\u5f00\u5931\u8d25", "degraded:sentence_window:expand_failed"),
)
_REDACTED = str(sanitize_audit_snapshot("secret", key="token")).casefold()


@dataclass(frozen=True)
class RetrievalExperimentVariant:
    """One strictly bounded retrieval strategy variant."""

    name: str
    route_target: RouteTarget
    top_k: int
    hybrid_search_on: bool
    rerank_on: bool
    graph_retrieval_on: bool
    sentence_window_on: bool
    source_diversity: SourceDiversity

    def __post_init__(self) -> None:
        name = str(self.name).strip()
        if not name or len(name) > 64:
            raise ValueError("variant name must contain 1 to 64 characters")
        if self.route_target not in _ROUTE_TARGETS:
            raise ValueError("variant route_target is invalid")
        if isinstance(self.top_k, bool) or not 1 <= int(self.top_k) <= 50:
            raise ValueError("variant top_k must be between 1 and 50")
        if self.source_diversity not in _SOURCE_DIVERSITY:
            raise ValueError("variant source_diversity is invalid")
        for field_name in (
            "hybrid_search_on",
            "rerank_on",
            "graph_retrieval_on",
            "sentence_window_on",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"variant {field_name} must be a boolean")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "top_k", int(self.top_k))


@dataclass(frozen=True)
class RetrievalExperimentVariantResult:
    name: str
    experiment: RetrievalExperiment
    status: str
    route: str
    latency_ms: int
    result_count: int
    reranked: bool
    degraded: bool


@dataclass(frozen=True)
class RetrievalExperimentRunResult:
    run_id: str
    dataset_serving_generation: int
    items: tuple[RetrievalExperimentVariantResult, ...]


@dataclass(frozen=True)
class _VariantFacts:
    variant: RetrievalExperimentVariant
    persistence: dict[str, Any]
    route: str
    result_count: int
    reranked: bool
    degraded: bool


class _NoopRewriter:
    @staticmethod
    def rewrite(query: str) -> tuple[str, bool]:
        return query, False


class _DeterministicRouter:
    def __init__(self, target: str) -> None:
        self.target = target

    def route(self, _query: str) -> RouteDecision:
        return RouteDecision(target=self.target, confidence=1.0, degraded=False)


def _deep_copy_settings(settings: Any) -> Any:
    copier = getattr(settings, "model_copy", None)
    if callable(copier):
        return copier(deep=True)
    return deepcopy(settings)


def _safe_reference(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    raw = _CONTROL.sub("", value).strip()
    if not raw:
        return ""
    normalized = raw.replace("\\", "/")
    try:
        parsed = urlsplit(normalized)
        scheme = parsed.scheme.casefold()
        if scheme in {"secret", "vault"}:
            return _REDACTED
        if scheme == "file":
            return PurePosixPath(parsed.path).name[:160] or "protected_reference"
        if scheme:
            if not parsed.netloc or not parsed.hostname:
                return "protected_reference"
            hostname = parsed.hostname
            if ":" in hostname and not hostname.startswith("["):
                hostname = f"[{hostname}]"
            port = f":{parsed.port}" if parsed.port is not None else ""
            return urlunsplit((scheme, hostname + port, parsed.path, "", ""))[:512]
        if "/" in normalized:
            return PurePosixPath(normalized).name[:160] or "protected_reference"
        return normalized[:160]
    except (TypeError, ValueError, OverflowError):
        return "protected_reference"


def _redact_assignment(match: re.Match[str]) -> str:
    value = match.group("value")
    if value.startswith('"') and value.endswith('"'):
        replacement = f'"{_REDACTED}"'
    elif value.startswith("'") and value.endswith("'"):
        replacement = f"'{_REDACTED}'"
    else:
        replacement = _REDACTED
    return f"{match.group('prefix')}{replacement}"


def _sanitize_projection_text(value: Any, maximum: int) -> str:
    text = _CONTROL.sub("", str(value or ""))

    def replace_uri(match: re.Match[str]) -> str:
        raw = match.group(0)
        trailing = ""
        while raw and raw[-1] in ".,;:!?)]}":
            trailing = raw[-1] + trailing
            raw = raw[:-1]
        return _safe_reference(raw) + trailing

    text = _URI.sub(replace_uri, text)
    text = _AUTHORIZATION_ASSIGNMENT.sub(_redact_assignment, text)
    text = _SECRET_ASSIGNMENT.sub(_redact_assignment, text)
    text = _AUTH_SCHEME.sub(lambda match: f"{match.group(1)} {_REDACTED}", text)
    return text.strip()[:maximum]


def _safe_number(value: Any, *, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _safe_optional_number(value: Any) -> float | None:
    if value is None:
        return None
    number = _safe_number(value, default=float("nan"))
    return number if math.isfinite(number) else None


def _sanitize_traces(values: Sequence[Any]) -> list[str]:
    traces: list[str] = []
    seen: set[str] = set()
    for value in list(values)[:64]:
        text = str(value or "").strip()
        projected: str | None = text if _TIMING_TRACE.fullmatch(text) else None
        if projected is None:
            for prefix, canonical in _DEGRADED_TRACE_RULES:
                if text.startswith(prefix):
                    projected = canonical
                    break
        if projected is not None and projected not in seen:
            seen.add(projected)
            traces.append(projected)
    return traces


def _safe_acl(acl: Sequence[str] | None) -> list[str]:
    if acl is None:
        return []
    if len(acl) > 100:
        raise ValueError("acl must contain at most 100 entries")
    result: list[str] = []
    for raw in acl:
        if not isinstance(raw, str):
            raise ValueError("acl entries must be strings")
        value = raw.strip()
        if not value or len(value) > 256 or _SAFE_ACL.fullmatch(value) is None:
            raise ValueError("acl entries must be safe strings of at most 256 characters")
        result.append(value)
    return result


def _resolved_route(variant: RetrievalExperimentVariant) -> str:
    if variant.route_target != "auto":
        return variant.route_target
    return "vector_graph_rag" if variant.graph_retrieval_on else "hybrid"


def _clone_graph_retriever(base: Any) -> Any:
    if base is None:
        return None
    cloned = copy(base)
    if hasattr(cloned, "llm"):
        cloned.llm = None
    return cloned


class RetrievalExperimentRunner:
    """Execute bounded variants sequentially and atomically persist their facts."""

    def __init__(
        self,
        repository: RetrievalExperimentRepository,
        base_retrieval: RetrievalPipeline,
    ) -> None:
        self.repository = repository
        self.base_retrieval = base_retrieval

    def _variant_pipeline(self, variant: RetrievalExperimentVariant) -> RetrievalPipeline:
        base = self.base_retrieval
        settings = _deep_copy_settings(base.settings)
        pipeline_settings = getattr(settings, "pipeline", settings)
        pipeline_settings.top_k = variant.top_k
        pipeline_settings.hybrid_search_on = variant.hybrid_search_on
        pipeline_settings.rerank_on = variant.rerank_on
        pipeline_settings.graph_retrieval_on = variant.graph_retrieval_on
        pipeline_settings.sentence_window_on = variant.sentence_window_on
        pipeline_settings.source_diversity = variant.source_diversity
        pipeline_settings.acl_filter_on = True
        pipeline_settings.complexity_gate_on = False
        for field_name in ("hyde_on", "subqueries_on", "stepback_on"):
            if hasattr(pipeline_settings, field_name):
                setattr(pipeline_settings, field_name, False)
        graph_settings = getattr(settings, "graph", None)
        if graph_settings is not None and hasattr(graph_settings, "use_llm_rerank"):
            graph_settings.use_llm_rerank = False

        target = _resolved_route(variant)
        return RetrievalPipeline(
            embedder=base.embedder,
            milvus=base.milvus,
            reranker=base.reranker,
            rewriter=_NoopRewriter(),
            router=_DeterministicRouter(target),
            settings=settings,
            graph_retriever=_clone_graph_retriever(base.graph_retriever),
            hyde=None,
            subqueries=None,
            stepback=None,
            sentence_window=base.sentence_window,
            reranker_cb=None,
            serving_guard=base.serving_guard,
        )

    def _authority_results(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        result: RetrievalResult,
    ) -> list[dict[str, Any]]:
        ordered_ids: list[str] = []
        retrieved_by_id: dict[str, Any] = {}
        for retrieved in result.chunks:
            chunk_id = str(retrieved.chunk.chunk_id or "").strip()
            if not chunk_id or chunk_id in retrieved_by_id:
                continue
            ordered_ids.append(chunk_id)
            retrieved_by_id[chunk_id] = retrieved
        if not ordered_ids:
            return []

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with Session(self.repository.engine) as session:
            chunks = {
                row.id: row
                for row in session.scalars(
                    select(ChunkHead).where(
                        ChunkHead.tenant_id == tenant_id,
                        ChunkHead.dataset_id == dataset_id,
                        ChunkHead.id.in_(ordered_ids),
                        ChunkHead.enabled.is_(True),
                    )
                )
            }
            document_ids = {row.document_id for row in chunks.values()}
            documents = {
                row.id: row
                for row in session.scalars(
                    select(Document).where(
                        Document.tenant_id == tenant_id,
                        Document.dataset_id == dataset_id,
                        Document.id.in_(document_ids),
                        Document.lifecycle_state == "active",
                        Document.retrieval_enabled.is_(True),
                        or_(Document.effective_from.is_(None), Document.effective_from <= now),
                        or_(Document.expires_at.is_(None), Document.expires_at > now),
                    )
                )
            }
            revisions = {
                (row.chunk_id, int(row.revision), str(row.content_hash)): row
                for row in session.scalars(
                    select(ChunkRevision).where(
                        ChunkRevision.tenant_id == tenant_id,
                        ChunkRevision.dataset_id == dataset_id,
                        ChunkRevision.chunk_id.in_(ordered_ids),
                    )
                )
            }

        snapshots: list[dict[str, Any]] = []
        for chunk_id in ordered_ids:
            head = chunks.get(chunk_id)
            if head is None:
                continue
            document = documents.get(head.document_id)
            if document is None or int(document.content_revision) != int(head.document_revision):
                continue
            retrieved = retrieved_by_id[chunk_id]
            retrieved_chunk = retrieved.chunk
            if str(retrieved_chunk.doc_id) != str(head.document_id):
                continue
            current = (
                int(retrieved_chunk.document_revision) == int(head.document_revision)
                and int(retrieved_chunk.content_revision) == int(head.content_revision)
                and str(retrieved_chunk.text_hash) == str(head.content_hash)
            )
            chunk_revision_id: str | None = None
            document_revision: int | None
            if current:
                document_revision = int(head.document_revision)
                content_revision = int(head.content_revision)
                content_hash = str(head.content_hash)
                content = head.content
            else:
                revision = revisions.get(
                    (
                        str(head.id),
                        int(retrieved_chunk.content_revision),
                        str(retrieved_chunk.text_hash),
                    )
                )
                if revision is None or str(revision.document_id) != str(head.document_id):
                    continue
                chunk_revision_id = str(revision.id)
                document_revision = None
                content_revision = int(revision.revision)
                content_hash = str(revision.content_hash)
                content = revision.content

            source_uri = _sanitize_projection_text(
                _safe_reference(document.source_uri), 512
            )
            snapshot: dict[str, Any] = {
                "rank": len(snapshots) + 1,
                "document_id": str(document.id),
                "chunk_id": str(head.id),
                "document_revision": document_revision,
                "content_revision": content_revision,
                "content_hash": content_hash,
                "score": _safe_number(retrieved.score),
                "branch": (
                    retrieved.branch if retrieved.branch in {"hybrid", "graph"} else "hybrid"
                ),
                "dense_cosine": _safe_optional_number(retrieved.dense_cosine),
                "content": _sanitize_projection_text(content, 20_000),
                "source": {
                    "document_name": _sanitize_projection_text(document.name, 256),
                    "source_type": _sanitize_projection_text(document.source_type, 64),
                    "source_uri": source_uri,
                },
            }
            if chunk_revision_id is not None:
                snapshot["chunk_revision_id"] = chunk_revision_id
            snapshots.append(snapshot)
        return snapshots

    @staticmethod
    def _strategy_snapshot(
        variant: RetrievalExperimentVariant,
        generation: int,
        acl: list[str],
    ) -> dict[str, Any]:
        return {
            "strategy_revision": 1,
            "dataset_serving_generation": generation,
            "variant_name": variant.name,
            "route_target": variant.route_target,
            "top_k": variant.top_k,
            "hybrid_search_on": variant.hybrid_search_on,
            "rerank_on": variant.rerank_on,
            "graph_retrieval_on": variant.graph_retrieval_on,
            "sentence_window_on": variant.sentence_window_on,
            "source_diversity": variant.source_diversity,
            "acl": acl,
        }

    def _execute_variant(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        query: str,
        acl: list[str],
        generation: int,
        variant: RetrievalExperimentVariant,
    ) -> _VariantFacts:
        started = time.perf_counter()
        status = "completed"
        route = _resolved_route(variant)
        reranked = False
        degraded = False
        traces: list[str] = []
        failure_code: str | None = None
        results: list[dict[str, Any]] = []
        try:
            pipeline = self._variant_pipeline(variant)
            retrieval = pipeline.run(
                query,
                acl=acl or None,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
            )
            route = retrieval.route.target
            reranked = bool(retrieval.reranked)
            degraded = bool(retrieval.route.degraded)
            traces = _sanitize_traces(retrieval.traces)
            results = self._authority_results(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                result=retrieval,
            )
        except Exception:
            status = "failed"
            degraded = True
            failure_code = "retrieval_execution_failed"
            traces = []
        latency_ms = max(0, int(round((time.perf_counter() - started) * 1000.0)))
        result_snapshot: dict[str, Any] = {
            "result_revision": 1,
            "dataset_serving_generation": generation,
            "route": route,
            "reranked": reranked,
            "degraded": degraded,
            "results": results,
        }
        if traces:
            result_snapshot["traces"] = traces
        if failure_code is not None:
            result_snapshot["failure_code"] = failure_code
        evidence = {
            "evidence_revision": 1,
            "dataset_serving_generation": generation,
            "citations": deepcopy(results),
        }
        return _VariantFacts(
            variant=variant,
            persistence={
                "query": query,
                "strategy_snapshot": self._strategy_snapshot(variant, generation, acl),
                "result_snapshot": result_snapshot,
                "evidence_lineage": evidence,
                "latency_ms": latency_ms,
                "status": status,
            },
            route=route,
            result_count=len(results),
            reranked=reranked,
            degraded=degraded,
        )

    def run(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        query: str,
        acl: Sequence[str] | None,
        variants: Sequence[RetrievalExperimentVariant],
        audit: AuditContext,
    ) -> RetrievalExperimentRunResult:
        variant_list = list(variants)
        if not 1 <= len(variant_list) <= 4:
            raise ValueError("variants must contain between 1 and 4 items")
        names = [variant.name.casefold() for variant in variant_list]
        if len(set(names)) != len(names):
            raise ValueError("variant names must be unique")
        safe_acl = _safe_acl(acl)
        generation = self.repository.get_active_dataset_generation(tenant_id, dataset_id)
        facts = [
            self._execute_variant(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                query=query,
                acl=safe_acl,
                generation=generation,
                variant=variant,
            )
            for variant in variant_list
        ]
        run_id = f"retrieval-run-{uuid.uuid4().hex[:16]}"
        rows = self.repository.create_experiments_batch(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            run_id=run_id,
            expected_generation=generation,
            experiments=[item.persistence for item in facts],
            audit=audit,
        )
        items = tuple(
            RetrievalExperimentVariantResult(
                name=fact.variant.name,
                experiment=row,
                status=row.status,
                route=fact.route,
                latency_ms=int(row.latency_ms),
                result_count=fact.result_count,
                reranked=fact.reranked,
                degraded=fact.degraded,
            )
            for fact, row in zip(facts, rows)
        )
        return RetrievalExperimentRunResult(
            run_id=run_id,
            dataset_serving_generation=generation,
            items=items,
        )


__all__ = [
    "RetrievalExperimentRunResult",
    "RetrievalExperimentRunner",
    "RetrievalExperimentVariant",
    "RetrievalExperimentVariantResult",
]
