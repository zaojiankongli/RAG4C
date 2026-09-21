"""Runtime wiring: merge catalog-authoritative QA hits into retrieval evidence."""

from __future__ import annotations

from typing import Any, Sequence

from models.schemas import RetrievedChunk

from .qa_matcher import (
    QARecord,
    match_qa,
    match_to_retrieved_chunk,
    merge_qa_into_chunks,
    parse_qa_chunk_id,
)


def qa_records_from_bundle(bundle: Sequence[dict[str, Any]]) -> list[QARecord]:
    out: list[QARecord] = []
    for item in bundle or []:
        qa_id = str(item.get("qa_id") or item.get("id") or "").strip()
        if not qa_id:
            continue
        alts = item.get("alternatives") or ()
        negs = item.get("negatives") or ()
        out.append(
            QARecord(
                qa_id=qa_id,
                question=str(item.get("question") or ""),
                answer=str(item.get("answer") or ""),
                revision=int(item.get("revision") or 1),
                origin=str(item.get("origin") or "manual"),
                source_document_id=item.get("source_document_id"),
                tenant_id=str(item.get("tenant_id") or ""),
                dataset_id=str(item.get("dataset_id") or ""),
                alternatives=tuple(str(a) for a in alts if str(a or "").strip()),
                negatives=tuple(str(n) for n in negs if str(n or "").strip()),
            )
        )
    return out


def load_qa_bundle_best_effort(
    tenant_id: str | None,
    dataset_id: str | None,
) -> list[dict[str, Any]]:
    """Read effective QA bundle from catalog; never raise into the query path."""
    if not tenant_id:
        return []
    try:
        from core import catalog
        from core.knowledge_content import KnowledgeContentRepository
        from core.metrics import get_metrics
        from core.observability import get_logger
    except Exception:  # noqa: BLE001
        return []
    try:
        engine = catalog.get_engine()
    except Exception:  # noqa: BLE001
        return []
    if engine is None:
        return []
    try:
        repo = KnowledgeContentRepository(engine)
        return repo.list_qa_retrieval_bundle(tenant_id, dataset_id or "default")
    except Exception:  # noqa: BLE001 - 生产失败可见，但不阻断问答
        try:
            get_metrics().incr("query.qa_retrieval.catalog_error")
        except Exception:  # noqa: BLE001
            pass
        try:
            get_logger("retrieval.qa_retrieval").warning(
                "QA retrieval catalog load failed", exc_info=True
            )
        except Exception:  # noqa: BLE001
            pass
        return []


def apply_qa_retrieval(
    query: str,
    chunks: Sequence[RetrievedChunk],
    *,
    tenant_id: str | None = None,
    dataset_id: str | None = None,
    settings: Any = None,
    bundle: Sequence[dict[str, Any]] | None = None,
) -> tuple[list[RetrievedChunk], list[str], bool]:
    """Merge QA evidence into retrieval chunks.

    Returns ``(chunks, traces, qa_hit)``. ``qa_hit`` is True when at least one
    catalog QA was injected — callers should treat QA scores as absolute
    (comparable to retrieval_score_threshold) rather than RRF ranks.
    """
    traces: list[str] = []
    try:
        if settings is not None:
            pipeline = getattr(settings, "pipeline", settings)
            enabled = bool(getattr(pipeline, "qa_retrieval_on", True))
            min_score = float(getattr(pipeline, "qa_match_min_score", 0.55))
            top_k = int(getattr(pipeline, "qa_match_top_k", 3))
        else:
            enabled, min_score, top_k = True, 0.55, 3
    except Exception:  # noqa: BLE001
        enabled, min_score, top_k = True, 0.55, 3
    if not enabled:
        return list(chunks), traces, False

    if bundle is None:
        bundle = load_qa_bundle_best_effort(tenant_id, dataset_id)
    if not bundle:
        return list(chunks), traces, False

    records = qa_records_from_bundle(bundle)
    matches = match_qa(query, records, min_score=min_score, top_k=top_k)
    if not matches:
        traces.append("QA 检索：无有效 FAQ 命中")
        return list(chunks), traces, False

    qa_items = [match_to_retrieved_chunk(m, rank=i + 1) for i, m in enumerate(matches)]
    merged = merge_qa_into_chunks(chunks, qa_items)
    traces.append(
        f"QA 检索：命中 {len(qa_items)} 条权威 FAQ（"
        + ", ".join(f"{m.qa_id}:{m.score:.2f}" for m in matches)
        + "）"
    )
    return merged, traces, True


def qa_evidence_enrichment(chunk_dump: dict[str, Any]) -> dict[str, Any]:
    """Add lifeline fields for QA evidence chunks in answer payloads."""
    meta = chunk_dump.get("metadata") or {}
    chunk_id = str(chunk_dump.get("chunk_id") or "")
    qa_id = meta.get("qa_id") or parse_qa_chunk_id(chunk_id)
    if not qa_id:
        return chunk_dump
    out = dict(chunk_dump)
    out["source_kind"] = "qa"
    out["qa_id"] = str(qa_id)
    if meta.get("qa_revision") is not None:
        out["qa_revision"] = meta.get("qa_revision")
    if meta.get("qa_origin"):
        out["qa_origin"] = meta.get("qa_origin")
    return out


__all__ = [
    "apply_qa_retrieval",
    "load_qa_bundle_best_effort",
    "qa_evidence_enrichment",
    "qa_records_from_bundle",
]
