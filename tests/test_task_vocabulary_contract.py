"""Task category/status vocabulary contract tests."""

from __future__ import annotations

import pytest

from core import catalog_schema
from core.enterprise_task_operations import TASK_CATEGORIES, TASK_NORMALIZED_STATUSES
from core.task_source_kinds import TASK_SOURCE_CATEGORIES
from core.task_vocabulary import (
    TASK_CATEGORY_API_VALUES,
    TASK_CATEGORY_ALIASES,
    TASK_CATEGORY_VALUES,
    TASK_NORMALIZED_STATUS_VALUES,
    TASK_STATUS_ALIASES,
    canonical_task_category,
    canonical_task_status,
    task_display_status,
)


def test_canonical_task_vocabularies_are_immutable_and_complete() -> None:
    assert TASK_CATEGORY_VALUES == (
        "documents",
        "indexing",
        "sources",
        "compliance",
        "quality",
    )
    assert TASK_NORMALIZED_STATUS_VALUES == (
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "blocked",
        "unavailable",
    )
    assert TASK_CATEGORY_ALIASES["content"] == "documents"
    assert TASK_CATEGORY_ALIASES["source"] == "sources"
    assert TASK_CATEGORY_API_VALUES == (
        "content",
        "documents",
        "indexing",
        "source",
        "sources",
        "compliance",
        "quality",
    )
    assert TASK_STATUS_ALIASES["completed"] == "succeeded"
    assert TASK_STATUS_ALIASES["primary_ready"] == "running"
    with pytest.raises(TypeError):
        TASK_CATEGORY_ALIASES["new"] = "documents"  # type: ignore[index]


def test_backend_consumers_pin_to_the_same_canonical_contract() -> None:
    assert TASK_SOURCE_CATEGORIES == frozenset(TASK_CATEGORY_VALUES)
    assert TASK_CATEGORIES == frozenset(TASK_CATEGORY_VALUES)
    assert TASK_NORMALIZED_STATUSES == frozenset(TASK_NORMALIZED_STATUS_VALUES)
    assert catalog_schema.ENTERPRISE_TASK_CATEGORIES is TASK_CATEGORY_VALUES
    assert catalog_schema.ENTERPRISE_TASK_STATUSES is TASK_NORMALIZED_STATUS_VALUES


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("documents", "documents"),
        ("content", "documents"),
        ("document", "documents"),
        ("release-quality", "quality"),
        ("SOURCE", "sources"),
    ],
)
def test_category_aliases_are_canonicalized(raw: str, expected: str) -> None:
    assert canonical_task_category(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("queued", "queued"),
        ("pending", "queued"),
        ("primary-ready", "running"),
        ("completed", "succeeded"),
        ("success", "succeeded"),
        ("rejected", "blocked"),
        ("stale", "unavailable"),
    ],
)
def test_status_aliases_are_canonicalized(raw: str, expected: str) -> None:
    assert canonical_task_status(raw) == expected


def test_fact_status_and_display_status_are_explicitly_distinct() -> None:
    assert task_display_status("succeeded") == "completed"
    assert task_display_status("failed") == "failed"


@pytest.mark.parametrize(
    "value",
    ["", "not-a-category", "succeeded", None, 1],
)
def test_unknown_category_fails_closed(value: object) -> None:
    with pytest.raises(ValueError):
        canonical_task_category(value)


@pytest.mark.parametrize(
    "value",
    ["", "not-a-status", "content", None, 1],
)
def test_unknown_status_fails_closed(value: object) -> None:
    with pytest.raises(ValueError):
        canonical_task_status(value)
