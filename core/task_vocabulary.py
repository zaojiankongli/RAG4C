"""Canonical Task Operations category/status vocabulary.

This module is deliberately independent from ORM, HTTP, and source adapters.
It owns the small contract shared by task authority, service, schema, and API
boundaries. Compatibility aliases are accepted only at input boundaries; the
returned values are always canonical persisted facts.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final, Mapping

TASK_CATEGORY_VALUES: Final[tuple[str, ...]] = (
    "documents",
    "indexing",
    "sources",
    "compliance",
    "quality",
)
# Public HTTP compatibility values intentionally remain narrower than the
# backend alias map. The extra aliases are accepted by trusted/internal
# canonicalization paths but are not added to the OpenAPI enum by accident.
TASK_CATEGORY_API_VALUES: Final[tuple[str, ...]] = (
    "content",
    "documents",
    "indexing",
    "source",
    "sources",
    "compliance",
    "quality",
)
TASK_CATEGORY_ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "documents": "documents",
        "document": "documents",
        "content": "documents",
        "ingest": "documents",
        "indexing": "indexing",
        "index": "indexing",
        "sources": "sources",
        "source": "sources",
        "compliance": "compliance",
        "audit": "compliance",
        "quality": "quality",
        "release_quality": "quality",
    }
)

TASK_NORMALIZED_STATUS_VALUES: Final[tuple[str, ...]] = (
    "queued",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "blocked",
    "unavailable",
)
TASK_STATUS_ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "queued": "queued",
        "queue": "queued",
        "pending": "queued",
        "created": "queued",
        "waiting": "queued",
        "scheduled": "queued",
        "running": "running",
        "in_progress": "running",
        "inprogress": "running",
        "processing": "running",
        "claimed": "running",
        "started": "running",
        "primary_ready": "running",
        "finalizing": "running",
        "succeeded": "succeeded",
        "success": "succeeded",
        "completed": "succeeded",
        "complete": "succeeded",
        "done": "succeeded",
        "failed": "failed",
        "failure": "failed",
        "error": "failed",
        "cancelled": "cancelled",
        "canceled": "cancelled",
        "aborted": "cancelled",
        "blocked": "blocked",
        "paused": "blocked",
        "awaiting_evidence": "blocked",
        "ready_to_certify": "blocked",
        "rejected": "blocked",
        "unavailable": "unavailable",
        "unknown": "unavailable",
        "stale": "unavailable",
        "expired": "unavailable",
        "superseded": "unavailable",
    }
)


def _normalized_key(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} is not allowed")
    normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
    if not normalized:
        raise ValueError(f"{field} is not allowed")
    return normalized


def canonical_task_category(value: object, *, field: str = "category") -> str:
    normalized = _normalized_key(value, field)
    canonical = TASK_CATEGORY_ALIASES.get(normalized)
    if canonical is None:
        raise ValueError(f"{field} is not allowed")
    return canonical


def canonical_task_status(
    value: object,
    *,
    field: str = "normalized_status",
) -> str:
    normalized = _normalized_key(value, field)
    canonical = TASK_STATUS_ALIASES.get(normalized)
    if canonical is None:
        raise ValueError(f"{field} is not allowed")
    return canonical


def task_display_status(value: object) -> str:
    canonical = canonical_task_status(value, field="status")
    return "completed" if canonical == "succeeded" else canonical


__all__ = [
    "TASK_CATEGORY_ALIASES",
    "TASK_CATEGORY_API_VALUES",
    "TASK_CATEGORY_VALUES",
    "TASK_NORMALIZED_STATUS_VALUES",
    "TASK_STATUS_ALIASES",
    "canonical_task_category",
    "canonical_task_status",
    "task_display_status",
]
