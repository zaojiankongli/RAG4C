"""Per-store revision advancement strategies for durable projection workers."""
from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from core.providers import ProviderRegistry
from models.orm import Document, IndexOperation

_STORE_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")


@dataclass(frozen=True)
class ProjectionRevisionContext:
    engine: Any
    operation: IndexOperation


ProjectionRevisionStrategy = Callable[[ProjectionRevisionContext], None]


class InvalidProjectionRevisionTarget(ValueError):
    """A target_store value is not a canonical lowercase key."""


PROJECTION_REVISION_STRATEGIES: ProviderRegistry[
    ProjectionRevisionContext, None
] = ProviderRegistry("projection revision strategy")


def _validate_target_store(target_store: str) -> None:
    if not isinstance(target_store, str) or _STORE_CODE.fullmatch(target_store) is None:
        raise InvalidProjectionRevisionTarget("target_store must be a lowercase code")


def register_projection_revision_strategy(
    target_store: str,
    strategy: ProjectionRevisionStrategy,
    *,
    replace: bool = False,
) -> None:
    _validate_target_store(target_store)
    if not callable(strategy):
        raise TypeError("projection revision strategy must be callable")
    callable_methods = (strategy, getattr(strategy, "__call__", None))
    if any(
        inspect.iscoroutinefunction(method)
        or inspect.isasyncgenfunction(method)
        or inspect.isgeneratorfunction(method)
        for method in callable_methods
        if method is not None
    ):
        raise TypeError("projection revision strategy must be synchronous")
    try:
        inspect.signature(strategy).bind(object())
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "projection revision strategy must accept one positional context"
        ) from exc
    PROJECTION_REVISION_STRATEGIES.register(target_store, strategy, replace=replace)


def unregister_projection_revision_strategy(target_store: str) -> None:
    PROJECTION_REVISION_STRATEGIES.unregister(target_store)


def resolve_projection_revision_strategy(
    target_store: str,
) -> ProjectionRevisionStrategy:
    _validate_target_store(target_store)
    return PROJECTION_REVISION_STRATEGIES.get_factory(target_store)


def invoke_projection_revision_strategy(
    strategy: ProjectionRevisionStrategy, context: ProjectionRevisionContext
) -> None:
    result = strategy(context)
    if (
        inspect.isawaitable(result)
        or inspect.isgenerator(result)
        or inspect.isasyncgen(result)
    ):
        cancel = getattr(result, "cancel", None)
        if callable(cancel):
            cancel()
        close = getattr(result, "close", None)
        if callable(close):
            close()
        raise TypeError("projection revision strategy must be synchronous")


def _advance_milvus_revision(context: ProjectionRevisionContext) -> None:
    operation = context.operation
    with Session(context.engine) as session:
        document = session.get(Document, operation.document_id)
        if document is None:
            return
        if (
            document.desired_index_revision == operation.target_revision
            and document.indexed_revision < operation.target_revision
        ):
            document.indexed_revision = operation.target_revision
        session.commit()


def _advance_graph_revision(context: ProjectionRevisionContext) -> None:
    operation = context.operation
    with Session(context.engine) as session:
        document = session.get(Document, operation.document_id)
        if document is None:
            return
        if (
            document.content_revision == operation.target_revision
            and document.graph_revision < operation.target_revision
        ):
            document.graph_revision = operation.target_revision
        session.commit()


register_projection_revision_strategy(
    "milvus_chunks", _advance_milvus_revision, replace=True
)
register_projection_revision_strategy(
    "graph_projection", _advance_graph_revision, replace=True
)


__all__ = [
    "InvalidProjectionRevisionTarget",
    "ProjectionRevisionContext",
    "ProjectionRevisionStrategy",
    "PROJECTION_REVISION_STRATEGIES",
    "invoke_projection_revision_strategy",
    "register_projection_revision_strategy",
    "resolve_projection_revision_strategy",
    "unregister_projection_revision_strategy",
]
