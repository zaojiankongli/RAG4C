"""Authoritative serving fences for knowledge retrieval.

MySQL is the serving truth.  Vector and graph stores are projections that may
still contain facts after a document has expired or entered durable deletion.
This module snapshots the dataset serving generation, filters projection
candidates with one scoped relational lookup, and prevents answers/cache writes
when that generation changes while a query is in flight.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterator, Sequence, TypeVar

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from models.orm import Dataset, Document


class KnowledgeChanged(RuntimeError):
    """The knowledge serving generation changed during an in-flight query."""


@dataclass(frozen=True)
class ServingSnapshot:
    """Immutable tenant/dataset serving-generation snapshot.

    ``dataset_generations`` also supports the existing unscoped query mode.  A
    scoped query contains exactly one entry and exposes its integer generation
    through ``generation``; an unscoped query compares the complete sorted set.
    """

    tenant_id: str
    dataset_id: str
    generation: int | None
    dataset_generations: tuple[tuple[str, int], ...]

    @property
    def cache_token(self) -> str:
        """Stable token mixed into the answer-cache key."""
        if self.dataset_id and self.generation is not None:
            return str(self.generation)
        payload = json.dumps(self.dataset_generations, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DocumentServingContext:
    guard: "DocumentServingGuard"
    snapshot: ServingSnapshot


_bound_context: contextvars.ContextVar[DocumentServingContext | None] = contextvars.ContextVar(
    "rag4c_document_serving_context",
    default=None,
)


@contextlib.contextmanager
def bind_document_serving(
    guard: "DocumentServingGuard",
    snapshot: ServingSnapshot,
) -> Iterator[DocumentServingContext]:
    """Bind one HTTP query's serving snapshot to the retrieval pipeline."""
    context = DocumentServingContext(guard=guard, snapshot=snapshot)
    token = _bound_context.set(context)
    try:
        yield context
    finally:
        _bound_context.reset(token)


def current_document_serving() -> DocumentServingContext | None:
    return _bound_context.get()


_CandidateT = TypeVar("_CandidateT")


class DocumentServingGuard:
    """Read and enforce authoritative document-serving eligibility."""

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    @classmethod
    def from_catalog(cls) -> "DocumentServingGuard":
        from core.catalog import get_engine

        return cls(get_engine())

    @staticmethod
    def _scope(tenant_id: str, dataset_id: str | None) -> tuple[str, str]:
        tenant = str(tenant_id or "").strip()
        dataset = str(dataset_id or "").strip()
        if not tenant:
            raise KnowledgeChanged("knowledge tenant serving scope is unavailable")
        return tenant, dataset

    def snapshot(self, tenant_id: str, dataset_id: str | None) -> ServingSnapshot:
        """Read the current dataset generation in one relational query."""
        tenant, dataset = self._scope(tenant_id, dataset_id)
        statement = select(Dataset.id, Dataset.serving_generation).where(
            Dataset.tenant_id == tenant
        )
        if dataset:
            statement = statement.where(Dataset.id == dataset)
        statement = statement.order_by(Dataset.id)
        with Session(self._engine) as session:
            rows = tuple(
                (str(dataset_key), int(generation))
                for dataset_key, generation in session.execute(statement).all()
            )
        if dataset and not rows:
            raise KnowledgeChanged("knowledge dataset serving scope is unavailable")
        generation = rows[0][1] if dataset else None
        return ServingSnapshot(
            tenant_id=tenant,
            dataset_id=dataset,
            generation=generation,
            dataset_generations=rows,
        )

    def assert_current(self, snapshot: ServingSnapshot) -> None:
        """Fail if any dataset serving generation changed since ``snapshot``."""
        current = self.snapshot(snapshot.tenant_id, snapshot.dataset_id)
        if current.dataset_generations != snapshot.dataset_generations:
            raise KnowledgeChanged("knowledge changed while the query was running")

    def filter_document_ids(
        self,
        snapshot: ServingSnapshot,
        document_ids: Sequence[str],
        *,
        now: datetime | None = None,
    ) -> set[str]:
        """Return eligible document IDs using bounded bulk lookups, never N+1."""
        unique_ids = tuple(sorted({str(value) for value in document_ids if str(value)}))
        if not unique_ids:
            return set()
        dataset_ids = tuple(dataset_id for dataset_id, _ in snapshot.dataset_generations)
        if not dataset_ids:
            return set()
        effective_now = now or datetime.now(UTC).replace(tzinfo=None)
        allowed: set[str] = set()
        # Keep below SQLite's common 999 bind-parameter limit while retaining a
        # single query for normal retrieval candidate counts.
        batch_size = 500
        with Session(self._engine) as session:
            for offset in range(0, len(unique_ids), batch_size):
                batch = unique_ids[offset : offset + batch_size]
                statement = select(Document.id).where(
                    Document.tenant_id == snapshot.tenant_id,
                    Document.dataset_id.in_(dataset_ids),
                    Document.id.in_(batch),
                    Document.lifecycle_state == "active",
                    Document.retrieval_enabled.is_(True),
                    or_(
                        Document.effective_from.is_(None), Document.effective_from <= effective_now
                    ),
                    or_(Document.expires_at.is_(None), Document.expires_at > effective_now),
                )
                allowed.update(str(value) for value in session.scalars(statement))
        return allowed

    def filter_candidates(
        self,
        snapshot: ServingSnapshot,
        candidates: Sequence[_CandidateT],
        *,
        now: datetime | None = None,
    ) -> list[_CandidateT]:
        """Preserve candidate order while removing non-serving documents."""

        def document_id(candidate: Any) -> str:
            chunk = getattr(candidate, "chunk", candidate)
            return str(getattr(chunk, "doc_id", "") or "")

        allowed = self.filter_document_ids(
            snapshot,
            [document_id(candidate) for candidate in candidates],
            now=now,
        )
        return [candidate for candidate in candidates if document_id(candidate) in allowed]


__all__ = [
    "DocumentServingContext",
    "DocumentServingGuard",
    "KnowledgeChanged",
    "ServingSnapshot",
    "bind_document_serving",
    "current_document_serving",
]
