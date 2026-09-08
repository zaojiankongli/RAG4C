"""Pure aggregation for document-ingestion monitoring payloads."""
from __future__ import annotations

from typing import Any

IN_FLIGHT = {"waiting", "parsing", "splitting", "indexing"}


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    position = q * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    fraction = position - low
    return round(ordered[low] * (1 - fraction) + ordered[high] * fraction, 2)


def _latency(values: list[float]) -> dict[str, float | int]:
    return {
        "count": len(values),
        "mean": round(sum(values) / len(values), 2) if values else 0.0,
        "p50": _percentile(values, 0.5),
        "p95": _percentile(values, 0.95),
        "max": round(max(values), 2) if values else 0.0,
    }


def _distribution(values: list[str]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for value in values:
        if value:
            counts[value] = counts.get(value, 0) + 1
    return [
        {"name": name, "value": value}
        for name, value in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def aggregate_document_metrics(documents: list[dict[str, Any]]) -> dict[str, Any]:
    parse_values: list[float] = []
    total_values: list[float] = []
    stage_values: dict[str, list[float]] = {}
    engines: list[str] = []
    doc_types: list[str] = []
    legacy = 0
    slow: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for doc in documents:
        meta = doc.get("parser_meta") if isinstance(doc.get("parser_meta"), dict) else {}
        parse_ms = meta.get("parse_ms")
        total_ms = meta.get("total_ms")
        if isinstance(parse_ms, (int, float)):
            parse_values.append(float(parse_ms))
        if isinstance(total_ms, (int, float)):
            total_values.append(float(total_ms))
            slow.append({
                "document_id": doc.get("id"), "name": doc.get("name"),
                "total_ms": round(float(total_ms), 2), "status": doc.get("status"),
            })
        if not meta:
            legacy += 1
        stage_ms = meta.get("stage_ms") if isinstance(meta.get("stage_ms"), dict) else {}
        for stage, value in stage_ms.items():
            if isinstance(value, (int, float)):
                stage_values.setdefault(str(stage), []).append(float(value))
        engines.append(str(meta.get("engine") or ""))
        doc_types.append(str(doc.get("doc_type") or "other"))
        if doc.get("status") == "error":
            failures.append({
                "document_id": doc.get("id"), "name": doc.get("name"),
                "message": doc.get("error_message") or "处理失败",
                "updated_at": doc.get("updated_at"),
            })

    return {
        "summary": {
            "total": len(documents),
            "completed": sum(doc.get("status") == "completed" for doc in documents),
            "failed": sum(doc.get("status") == "error" for doc in documents),
            "processing": sum(doc.get("status") in IN_FLIGHT for doc in documents),
            "chunks": sum(int(doc.get("chunk_count") or 0) for doc in documents),
        },
        "latency_ms": {
            "parse": _latency(parse_values),
            "total": _latency(total_values),
            "stages": {stage: _latency(values) for stage, values in sorted(stage_values.items())},
        },
        "engine_distribution": _distribution(engines),
        "type_distribution": _distribution(doc_types),
        "slow_documents": sorted(slow, key=lambda item: item["total_ms"], reverse=True)[:10],
        "recent_failures": failures[-10:][::-1],
        "legacy_metadata_count": legacy,
    }


__all__ = ["aggregate_document_metrics"]
